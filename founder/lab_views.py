"""Private website laboratory and isolated previews for generated HTML."""

from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from founder.forms import LabPromptForm
from founder.models import LabSiteVersion, StartupProfile
from founder.services.qwen import QwenError
from founder.services.site_generator import generate_site


def _owned_startup(request, startup_id):
    return get_object_or_404(StartupProfile, pk=startup_id, owner=request.user)


def _version(startup, value):
    try:
        version_id = UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise Http404("Версия сайта не найдена") from exc
    return get_object_or_404(startup.lab_versions, pk=version_id)


def _lab_page(request, startup, form=None, *, status=200):
    versions = list(startup.lab_versions.all()[:20])
    selected = _version(startup, request.GET["version"]) if request.GET.get("version") else (
        versions[0] if versions else None
    )
    return render(request, "founder/lab.html", {
        "startup": startup,
        "workspace_tab": "lab",
        "form": form or LabPromptForm(),
        "selected": selected,
        "versions": versions,
    }, status=status)


@login_required
@require_GET
def lab(request, startup_id):
    return _lab_page(request, _owned_startup(request, startup_id))


@login_required
@require_POST
def lab_generate(request, startup_id):
    startup = _owned_startup(request, startup_id)
    form = LabPromptForm(request.POST)
    if not form.is_valid():
        return _lab_page(request, startup, form, status=400)

    source = None
    if not request.POST.get("start_new") and request.POST.get("source_version"):
        source = _version(startup, request.POST["source_version"])
    try:
        result = generate_site(
            form.cleaned_data["prompt"],
            previous_html=source.html if source else "",
            max_tokens=min(6144, settings.QWEN_CODE_MAX_TOKENS),
        )
    except QwenError as exc:
        form.add_error(None, str(exc))
        return _lab_page(request, startup, form, status=503)

    version = LabSiteVersion.objects.create(
        startup=startup, source=source, prompt=form.cleaned_data["prompt"],
        html=result.text, model=result.model,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
    )
    messages.success(request, "Сайт готов. Его можно посмотреть и доработать ниже.")
    return redirect("lab", startup_id=startup.pk)


@login_required
@require_GET
def lab_preview(request, startup_id, version_id):
    startup = _owned_startup(request, startup_id)
    version = get_object_or_404(startup.lab_versions, pk=version_id)
    response = HttpResponse(version.html, content_type="text/html; charset=utf-8")
    # Two sandboxes: the iframe has an opaque origin and CSP constrains resources.
    # The generated page cannot read our session, fetch endpoints or navigate top.
    response["Content-Security-Policy"] = (
        "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; img-src data:; font-src data:; "
        "connect-src 'none'; frame-src 'none'; object-src 'none'; "
        "form-action 'none'; base-uri 'none'; frame-ancestors 'self'"
    )
    response["X-Frame-Options"] = "SAMEORIGIN"
    response["Referrer-Policy"] = "no-referrer"
    response["Cache-Control"] = "no-store"
    return response
