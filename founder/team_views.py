"""Команда проекта: приглашения по тегу, роли и выход из проекта."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from founder.forms import TeamInviteForm
from founder.models import ProjectMember
from founder.services.access import OWNER, ROLE_LABELS, get_startup


def _team_context(startup, form=None):
    members = list(startup.members.select_related("user", "invited_by"))
    return {
        "startup": startup, "workspace_tab": "team",
        "is_project_owner": startup.user_role == OWNER, "role_labels": ROLE_LABELS,
        "active_members": [m for m in members if m.status == ProjectMember.Status.ACTIVE],
        "invited_members": [m for m in members if m.status == ProjectMember.Status.INVITED],
        "invite_form": form or TeamInviteForm(startup=startup),
        "roles": ProjectMember.Role.choices,
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
            form.add_error("handle", "Этот человек уже в команде или приглашён.")
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
    member = get_object_or_404(startup.members, pk=member_id)
    action = request.POST.get("action")
    if action == "remove":
        member.delete()
        messages.success(request, f"@{member.user.handle} больше не в команде проекта.")
    elif action == "role" and request.POST.get("role") in ProjectMember.Role.values:
        member.role = request.POST["role"]
        member.save(update_fields=["role"])
        messages.success(request, f"Роль @{member.user.handle}: {member.get_role_display().lower()}.")
    else:
        messages.error(request, "Неизвестное действие.")
    return redirect("team", startup_id=startup.pk)


@login_required
@require_POST
def team_leave(request, startup_id):
    deleted, _ = ProjectMember.objects.filter(startup_id=startup_id, user=request.user,
                                              status=ProjectMember.Status.ACTIVE).delete()
    if not deleted:
        raise Http404("Вы не участник этого проекта.")
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
        messages.success(request, f"Вы в команде проекта «{invite.startup.name}».")
        return redirect("dashboard", startup_id=invite.startup_id)
    invite.delete()
    messages.success(request, "Приглашение отклонено.")
    return redirect("home")
