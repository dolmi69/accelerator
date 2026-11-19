from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from founder.models import LabSiteVersion
from founder.services.access import get_startup
from founder.services.shared_schema import bind_space, space_key
from founder.shared_api import serve
from founder.shared_models import SharedMember, SharedSpace


@login_required
def preview(request, startup_id, version_id, endpoint=""):
    startup = get_startup(request, startup_id)
    version = get_object_or_404(LabSiteVersion, startup=startup, pk=version_id, kind="shared")
    root = reverse("lab_preview", args=[startup.pk, version.pk])
    return serve(request, version, live=False, endpoint=endpoint, root=root)


@login_required
@require_POST
def grant(request, startup_id, version_id):
    startup = get_startup(request, startup_id, edit=True)
    if startup.owner_id != request.user.pk:
        raise Http404
    version = get_object_or_404(LabSiteVersion, startup=startup, pk=version_id, kind="shared")
    if request.POST.get("environment") not in {"live", "draft"}:
        return HttpResponseBadRequest("Выберите предпросмотр или опубликованный сайт.")
    live = request.POST["environment"] == "live"
    key = space_key(version, live)
    with transaction.atomic():
        bind_space(key)
        space = get_object_or_404(SharedSpace.objects.select_for_update(), pk=key, version=version)
        role = request.POST.get("role")
        username = request.POST.get("username", "").strip().casefold()
        if role not in space.schema["roles"] or not SharedMember.objects.filter(
            space=space, username=username
        ).update(role=role):
            messages.error(request, "Проверьте логин зарегистрированного пользователя и роль.")
        else:
            messages.success(request, "Права пользователя обновлены.")
    return redirect(reverse("lab", args=[startup_id]) + "?version=" + str(version_id))
