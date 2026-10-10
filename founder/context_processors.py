"""Navigation context for Bruno's companion on authenticated pages."""
from uuid import UUID
from urllib.parse import urlsplit
from django.conf import settings

from founder.models import ChatSession, MascotState


def bruno_pet(request):
    if not request.user.is_authenticated:
        return {}

    projects = request.user.startups.select_related('mascot_state')
    match = request.resolver_match
    current_id = match.kwargs.get('startup_id') if match else None
    remembered_id = request.session.get('bruno_last_project')
    project = None
    # Always resolve inside the current account, even for a stale session value.
    for candidate in (current_id, remembered_id):
        if not candidate:
            continue
        try:
            project_id = UUID(str(candidate))
        except (ValueError, TypeError):
            continue
        project = projects.filter(pk=project_id).first()
        if project:
            break
    project = project or projects.first()
    if project and current_id and str(project.pk) == str(current_id):
        if remembered_id != str(project.pk):
            request.session['bruno_last_project'] = str(project.pk)

    mascot = getattr(project, 'mascot_state', None) if project else None
    chat = project.chat_sessions.filter(
        mode=ChatSession.Mode.COFOUNDER, completed_at__isnull=True,
    ).first() if project else None
    local_project = (project and current_id and str(project.pk) == str(current_id)
                     and request.path.startswith('/startups/')
                     and settings.LAB_BACKEND_RUNTIME_ENABLED
                     and urlsplit('//' + request.get_host()).hostname in {'localhost', '127.0.0.1'})
    return {'lab_workspace': {'project_id': project.pk, 'user_id': request.user.pk} if local_project else None,
            'bruno_pet': {
        'project': project,
        'mascot': mascot or MascotState(mood=MascotState.Mood.CURIOUS),
        'chat': chat,
    }}


def community(request):
    if not request.user.is_authenticated:
        return {}
    from founder.services.messaging import unread_count
    return {'direct_unread': unread_count(request.user.pk)}
