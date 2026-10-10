"""Команда проекта: приглашения по тегу, заявки из сообщества, роли и выход из проекта."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from founder.community_views import published_cards
from founder.forms import JoinRequestForm, TeamInviteForm
from founder.models import BrunoTask, ProjectMember
from founder.services import activity
from founder.services.access import OWNER, ROLE_LABELS, get_startup
from founder.services.messaging import blocked_pair
from founder.services.request_limits import RequestLimitExceeded, consume_limit


def _team_context(startup, form=None):
    members = list(startup.members.select_related("user", "invited_by"))
    return {
        "startup": startup, "workspace_tab": "team",
        "is_project_owner": startup.user_role == OWNER, "role_labels": ROLE_LABELS,
        "active_members": [m for m in members if m.status == ProjectMember.Status.ACTIVE],
        "invited_members": [m for m in members if m.status == ProjectMember.Status.INVITED],
        "join_requests": [m for m in members if m.status == ProjectMember.Status.REQUESTED],
        "invite_form": form or TeamInviteForm(startup=startup),
        "roles": ProjectMember.Role.choices,
        "activities": activity.recent(startup, 30),
    }


@login_required
def team(request, startup_id):
    startup = get_startup(request, startup_id)
    return render(request, "founder/team.html", _team_context(startup))


def _owner_startup(request, startup_id):
    startup = get_startup(request, startup_id)
    if startup.user_role != OWNER:
        raise Http404("Управлять командой может только владелец проекта.")
    return startup


def _release_tasks(member):
    """Открытые задания ушедшего участника снова ничьи."""
    BrunoTask.objects.filter(startup=member.startup, assignee=member.user,
                             status=BrunoTask.Status.TODO).update(assignee=None)


@login_required
@require_POST
def team_invite(request, startup_id):
    startup = _owner_startup(request, startup_id)
    form = TeamInviteForm(request.POST, startup=startup)
    if form.is_valid():
        try:
            with transaction.atomic():
                ProjectMember.objects.create(startup=startup, user=form.cleaned_data["user"],
                                             role=form.cleaned_data["role"], invited_by=request.user)
        except IntegrityError:
            form.add_error("handle", "Этот человек уже в команде, приглашён или подал заявку — посмотрите списки ниже.")
        else:
            invited = form.cleaned_data["user"]
            messages.success(request, f"Приглашение отправлено @{invited.handle}. "
                                      "Оно появится у него в разделе «Мои проекты».")
            return redirect("team", startup_id=startup.pk)
    return render(request, "founder/team.html", _team_context(startup, form), status=400)


@login_required
@require_POST
def team_member_update(request, startup_id, member_id):
    startup = _owner_startup(request, startup_id)
    member = get_object_or_404(startup.members.select_related("user"), pk=member_id)
    action = request.POST.get("action")
    handle = member.user.handle
    if action == "remove":
        was_active = member.status == ProjectMember.Status.ACTIVE
        member.delete()
        if was_active:
            _release_tasks(member)
            activity.log(startup, request.user, activity.Kind.TEAM, f"@{handle} больше не в команде")
            messages.success(request, f"@{handle} больше не в команде проекта.")
        else:
            messages.success(request, "Заявка отклонена." if member.status == ProjectMember.Status.REQUESTED
                             else "Приглашение отозвано.")
    elif action == "accept" and member.status == ProjectMember.Status.REQUESTED:
        member.status = ProjectMember.Status.ACTIVE
        member.role = request.POST.get("role") if request.POST.get("role") in ProjectMember.Role.values else member.role
        member.joined_at = timezone.now()
        member.save(update_fields=["status", "role", "joined_at"])
        activity.log(startup, request.user, activity.Kind.TEAM,
                     f"Заявка @{handle} одобрена · {member.get_role_display().lower()}")
        messages.success(request, f"@{handle} теперь в команде.")
    elif action == "role" and member.status == ProjectMember.Status.ACTIVE and request.POST.get("role") in ProjectMember.Role.values:
        member.role = request.POST["role"]
        member.save(update_fields=["role"])
        activity.log(startup, request.user, activity.Kind.TEAM, f"Роль @{handle}: {member.get_role_display().lower()}")
        messages.success(request, f"Роль @{handle}: {member.get_role_display().lower()}.")
    else:
        messages.error(request, "Неизвестное действие.")
    return redirect("team", startup_id=startup.pk)


@login_required
@require_POST
def team_leave(request, startup_id):
    member = ProjectMember.objects.filter(startup_id=startup_id, user=request.user,
                                          status=ProjectMember.Status.ACTIVE).select_related("startup").first()
    if member is None:
        raise Http404("Вы не участник этого проекта.")
    member.delete()
    _release_tasks(member)
    activity.log(member.startup, request.user, activity.Kind.TEAM, "Участник вышел из проекта")
    messages.success(request, "Вы вышли из проекта.")
    return redirect("home")


@login_required
@require_POST
def invite_respond(request, member_id):
    invite = get_object_or_404(ProjectMember, pk=member_id, user=request.user, status=ProjectMember.Status.INVITED)
    if request.POST.get("accept") == "1":
        invite.status = ProjectMember.Status.ACTIVE
        invite.joined_at = timezone.now()
        invite.save(update_fields=["status", "joined_at"])
        activity.log(invite.startup, request.user, activity.Kind.TEAM,
                     f"Новый участник в команде · {invite.get_role_display().lower()}")
        messages.success(request, f"Вы в команде проекта «{invite.startup.name}».")
        return redirect("dashboard", startup_id=invite.startup_id)
    invite.delete()
    messages.success(request, "Приглашение отклонено.")
    return redirect("home")


@login_required
@require_POST
def join_request(request, startup_id):
    """«Хочу в команду» из опубликованной карточки. Доступ появится только после одобрения владельца."""
    card = get_object_or_404(published_cards(), startup_id=startup_id)
    startup = card.startup
    if request.POST.get("action") == "withdraw":
        ProjectMember.objects.filter(startup=startup, user=request.user, status=ProjectMember.Status.REQUESTED).delete()
        messages.success(request, "Заявка отозвана.")
        return redirect("card_detail", startup_id=startup_id)
    form = JoinRequestForm(request.POST)
    if startup.owner_id == request.user.pk or blocked_pair(request.user.pk, startup.owner_id):
        messages.error(request, "Заявку в этот проект отправить нельзя.")
    elif not form.is_valid():
        messages.error(request, "Расскажите о себе в нескольких словах (до 500 символов).")
    else:
        try:
            consume_limit(f"join-request:{request.user.pk}", 10, 86400)
            with transaction.atomic():
                ProjectMember.objects.create(startup=startup, user=request.user, status=ProjectMember.Status.REQUESTED,
                                             message=form.cleaned_data["message"])
        except RequestLimitExceeded as exc:
            messages.error(request, str(exc))
        except IntegrityError:
            messages.error(request, "Вы уже в команде, приглашены или заявка уже отправлена.")
        else:
            messages.success(request, "Заявка отправлена. Владелец увидит её на странице команды проекта.")
    return redirect("card_detail", startup_id=startup_id)
