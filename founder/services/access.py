"""Кто и что может делать с проектом: владелец, соавторы и наблюдатели."""
from django.db.models import BooleanField, ExpressionWrapper, Q
from django.http import Http404
from django.shortcuts import get_object_or_404

from founder.models import ProjectMember, StartupProfile

OWNER = "owner"
EDITOR = ProjectMember.Role.EDITOR
VIEWER = ProjectMember.Role.VIEWER
ROLE_LABELS = {OWNER: "Владелец", EDITOR: "Соавтор", VIEWER: "Наблюдатель"}


def member_filter(user, prefix="", *, edit=False):
    """Q-условие «пользователь видит (или может менять) проект» для запросов с префиксом поля."""
    memberships = ProjectMember.objects.filter(user=user, status=ProjectMember.Status.ACTIVE)
    if edit:
        memberships = memberships.filter(role=EDITOR)
    return Q(**{f"{prefix}owner": user}) | Q(**{f"{prefix}pk__in": memberships.values("startup_id")})


def accessible_startups(user, *, edit=False):
    if not user.is_authenticated:
        return StartupProfile.objects.none()
    return StartupProfile.objects.filter(member_filter(user, edit=edit)).annotate(
        is_shared=ExpressionWrapper(~Q(owner=user), output_field=BooleanField()),
    )


def project_role(user, startup):
    if not user.is_authenticated:
        return None
    if startup.owner_id == user.pk:
        return OWNER
    return (ProjectMember.objects.filter(startup=startup, user=user, status=ProjectMember.Status.ACTIVE)
            .values_list("role", flat=True).first())


def get_startup(request, startup_id, *, edit=False):
    """Проект, доступный текущему пользователю. Чужой проект — 404, без подсказки, что он существует.

    edit=True отсекает наблюдателей: им тоже 404, потому что изменять им нечего.
    """
    startup = get_object_or_404(StartupProfile.objects.select_related("owner"), pk=startup_id)
    role = project_role(request.user, startup)
    if role is None or (edit and role == VIEWER):
        raise Http404("Проект не найден")
    startup.user_role = role
    return startup


def can_edit(user, startup_id):
    return accessible_startups(user, edit=True).filter(pk=startup_id).exists()


def team_user_ids(startup):
    """Владелец и активные участники: их тесты прототипа не считаются внешними."""
    ids = set(ProjectMember.objects.filter(startup=startup, status=ProjectMember.Status.ACTIVE)
              .values_list("user_id", flat=True))
    ids.add(startup.owner_id)
    return ids


def team_members(startup):
    """Активные участники без владельца — для карточки проекта и страницы команды."""
    return [m.user for m in startup.members.filter(status=ProjectMember.Status.ACTIVE).select_related("user")]


def assignable_users(startup):
    """Кому можно поручить задание: владелец и соавторы (наблюдатели ничего не меняют)."""
    editors = [m.user for m in startup.members.filter(status=ProjectMember.Status.ACTIVE, role=EDITOR)
               .select_related("user")]
    return [startup.owner, *editors]


def has_team(startup):
    return startup.members.filter(status=ProjectMember.Status.ACTIVE).exists()


def pending_invites(user):
    return (ProjectMember.objects.filter(user=user, status=ProjectMember.Status.INVITED)
            .select_related("startup", "invited_by"))


def join_requests_for(owner):
    """Заявки «Хочу в команду» в проекты, которыми владеет пользователь."""
    return (ProjectMember.objects.filter(startup__owner=owner, status=ProjectMember.Status.REQUESTED)
            .select_related("startup", "user"))
