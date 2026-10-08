"""Private website laboratory and isolated previews for generated HTML."""

from uuid import UUID
from functools import lru_cache
from html import escape
import re
import hashlib
import json
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from django.db import OperationalError, transaction

from founder.forms import LabPromptForm, LabCustomizeForm, LabBackendModulesForm
from founder.models import LabSiteVersion, StartupProfile, LabAIUsage
from founder.services.qwen import QwenError
from founder.services.site_generator import generate_site
from founder.services.lab_testing import publication_for, test_results
from founder.services.django_builder import BuilderError, export_project, runtime_status, start_runtime, stop_runtime
from founder.services.ai_costs import billing_scope, usage_summary
from founder.services.site_editor import customize, simple_command
from founder.services.backend_modules import module_command, normalize_modules, MODULES, OPTIONAL_MODULES, DEFAULT_OPTIONAL, DEFAULT_BASE_FEATURES, MODULE_GROUPS
from founder.services.request_limits import RequestLimitExceeded, acquire_ai_lease, consume_limit


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
    if form is not None and request.POST.get('source_version'):
        selected = _version(startup, request.POST['source_version'])
    backend_form = LabBackendModulesForm(initial={'modules': [key for key in selected.backend_modules if key in OPTIONAL_MODULES]
        if selected and selected.kind == 'django' else DEFAULT_OPTIONAL})
    checkboxes = {widget.data['value']: widget for widget in backend_form['modules']}
    section = 'globalization' if request.GET.get('section') == 'globalization' and form is None else 'development'
    return render(request, "founder/lab.html", {
        "startup": startup,
        "workspace_tab": "lab",
        "form": form or LabPromptForm(source=selected, initial={"kind": selected.kind if selected else LabSiteVersion.Kind.STATIC}),
        "customize_form": LabCustomizeForm(initial=selected.presentation if selected else {}),
        'backend_form': backend_form,
        'module_groups': [{'title': title, 'fields': [checkboxes[key] for key in keys]} for title, keys in MODULE_GROUPS],
        'lab_section': section,
        'backend_labels':[MODULES[key][0] for key in normalize_modules(selected.backend_modules)] if selected and selected.kind == 'django' else [],
        "usage": usage_summary(request.user),
        "selected": selected,
        "versions": versions,
        "publication": publication_for(startup),
        "results": test_results(selected) if selected else None,
        "card_published": hasattr(startup, 'project_card') and startup.project_card.published_at is not None,
        "runtime_available": _local_runtime(request),
        "runtime": runtime_status(startup.pk) if _local_runtime(request) else None,
    }, status=status)


@login_required
@require_GET
def lab(request, startup_id):
    return _lab_page(request, _owned_startup(request, startup_id))


def generation_key(startup, source, prompt, kind, scope="auto", rebuild=False):
    return hashlib.sha256(json.dumps([
        "modules-v1", str(startup.pk), str(source.pk) if source else None,
        hashlib.sha256(source.html.encode()).hexdigest() if source else None,
        prompt, kind, scope, rebuild, settings.QWEN_CODE_MODEL,
        settings.LAB_CREATE_MAX_TOKENS, settings.LAB_PATCH_MAX_TOKENS,
        source.backend_modules if source else [],
    ], ensure_ascii=False).encode()).hexdigest()


@login_required
@require_POST
def lab_generate(request, startup_id):
    startup = _owned_startup(request, startup_id)
    source = _version(startup, request.POST["source_version"]) if request.POST.get("source_version") else None
    form = LabPromptForm(request.POST, source=source)
    if not form.is_valid():
        return _lab_page(request, startup, form, status=400)

    if request.POST.get("start_new"):
        source = None
    prompt = form.cleaned_data["prompt"]
    kind = form.cleaned_data["kind"]
    scope = form.cleaned_data["edit_scope"] or "auto"
    rebuild = form.cleaned_data["rebuild"]
    modules_command = module_command(prompt) if scope == 'auto' and not rebuild else None
    if modules_command:
        chosen = normalize_modules([*(source.backend_modules if source else []),*modules_command])
        version = _backend_version(startup,source,chosen,prompt)
        messages.success(request,'Готовые модули подключены без AI: 0 токенов. Запустите сайт ниже.')
        return redirect(reverse_lab_version(startup,version))
    key = generation_key(startup, source, prompt, kind, scope, rebuild)
    cached = startup.lab_versions.filter(generation_key=key).first()
    if cached:
        messages.success(request, "Открыли уже сохранённый результат. Новый AI-запрос не понадобился.")
        return redirect(reverse_lab_version(startup, cached))
    local = simple_command(prompt) if source and scope == "auto" and not rebuild else None
    if local:
        version = _customized_version(startup, source, {**source.presentation, **local}, prompt, kind, key)
        messages.success(request, "Оформление изменено без AI: 0 токенов.")
        return redirect(reverse_lab_version(startup, version))
    try:
        # Check after free edits/cache lookup. Middleware releases this lease.
        with transaction.atomic():
            request.ai_lease = acquire_ai_lease(request.user.pk)
            consume_limit(f"ai-minute:{request.user.pk}", settings.AI_REQUESTS_PER_MINUTE, 60)
            consume_limit(f"ai-day:{request.user.pk}", settings.AI_REQUESTS_PER_DAY, 86400)
            consume_limit(f"lab-day:{request.user.pk}", settings.LAB_REQUESTS_PER_DAY, 86400)
            consume_limit("lab-global-day", settings.LAB_GLOBAL_REQUESTS_PER_DAY, 86400)
        # A concurrent request may have completed between cache lookup and lease.
        cached = startup.lab_versions.filter(generation_key=key).first()
        if cached:
            return redirect(reverse_lab_version(startup, cached))
        options = {"backend": True} if kind == LabSiteVersion.Kind.DJANGO else {}
        if scope != "auto":
            options["edit_scope"] = scope
        if rebuild:
            options["rebuild"] = True
        with billing_scope(request.user, startup, "patch" if source and not rebuild else "create"):
            result = generate_site(prompt, previous_html=source.html if source else "",
                max_tokens=min(settings.LAB_CREATE_MAX_TOKENS, settings.QWEN_CODE_MAX_TOKENS), **options)
    except RequestLimitExceeded as exc:
        form.add_error(None, str(exc))
        return _lab_page(request, startup, form, status=429)
    except OperationalError:
        form.add_error(None, "Не удалось проверить лимиты. Повторите запрос позже.")
        return _lab_page(request, startup, form, status=503)
    except QwenError as exc:
        form.add_error(None, str(exc))
        return _lab_page(request, startup, form, status=503)

    version = LabSiteVersion.objects.create(
        startup=startup, source=source, prompt=prompt, kind=kind,
        html=result.text, model=result.model,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        generation_key=key, edit_method=result.edit_method,
        presentation=source.presentation if source else {},
        backend_modules=(source.backend_modules if source and source.kind == 'django' else normalize_modules() if kind == LabSiteVersion.Kind.DJANGO else []),
    )
    if result.request_id:
        LabAIUsage.objects.filter(pk=result.request_id, user=request.user, startup=startup).update(version=version)
    messages.success(request, "Сайт готов. Его можно посмотреть и доработать ниже.")
    return redirect("lab", startup_id=startup.pk)


def _customized_version(startup, source, presentation, prompt, kind=None, key=None):
    html = customize(source.html, presentation)
    if html == source.html and (kind is None or kind == source.kind):
        return source
    return LabSiteVersion.objects.create(startup=startup, source=source, kind=kind or source.kind,
        prompt=prompt, html=html, model="local-editor", input_tokens=0, output_tokens=0,
        presentation=presentation, edit_method="local", generation_key=key, backend_modules=source.backend_modules)


@login_required
@require_POST
def lab_customize(request, startup_id):
    startup = _owned_startup(request, startup_id)
    source = _version(startup, request.POST.get("source_version"))
    form = LabCustomizeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Проверьте название и параметры оформления.")
        return redirect(reverse_lab_version(startup, source))
    version = _customized_version(startup, source, form.cleaned_data, "Оформление без AI")
    messages.success(request, "Настройки сохранены без расхода AI-токенов.")
    return redirect(reverse_lab_version(startup, version))


def _local_runtime(request):
    # A remote browser's localhost is not the server. Offer launch only locally.
    return settings.LAB_BACKEND_RUNTIME_ENABLED and urlsplit("//" + request.get_host()).hostname in {"localhost", "127.0.0.1"}


def reverse_lab_version(startup, version):
    from django.urls import reverse
    return reverse("lab", args=[startup.pk]) + f"?version={version.pk}"


@login_required
@require_POST
def lab_backend_create(request, startup_id):
    startup = _owned_startup(request, startup_id)
    source = _version(startup, request.POST["source_version"]) if request.POST.get("source_version") else None
    form = LabBackendModulesForm(request.POST)
    if not form.is_valid():
        messages.error(request,'Выберите модули из списка.')
        return redirect('lab',startup_id=startup.pk)
    chosen = normalize_modules(form.cleaned_data['modules']) if request.POST.get('modules_selected') else normalize_modules(source.backend_modules if source and source.kind == 'django' else DEFAULT_BASE_FEATURES)
    version = _backend_version(startup,source,chosen,'Готовые модули Django')
    messages.success(request, "Модули сохранены без AI. Можно запустить сайт или скачать проект.")
    return redirect(reverse_lab_version(startup, version))


def _backend_version(startup,source,chosen,prompt):
    key = hashlib.sha256(json.dumps(['backend-modules-v2',str(startup.pk),str(source.pk) if source else None,chosen],ensure_ascii=False).encode()).hexdigest()
    cached = startup.lab_versions.filter(generation_key=key).first()
    if cached:
        return cached
    if source and source.kind == LabSiteVersion.Kind.DJANGO and normalize_modules(source.backend_modules) == chosen:
        version = source
    else:
        html = source.html if source else (
            '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<style>body{font-family:system-ui;background:#f3f6f5;color:#182d34;padding:8vw}h1{font-size:clamp(32px,6vw,64px)}p{line-height:1.6;max-width:600px}</style>'
            f'<title>{escape(startup.name)}</title></head><body><h1>{escape(startup.name)}</h1>'
            f'<p>{escape(startup.one_line_pitch or "Ваше приложение готово к первым участникам.")}</p>'
            '<p>Создайте аккаунт через меню сверху, найдите участника и начните разговор.</p></body></html>'
        )
        version = LabSiteVersion.objects.create(startup=startup, source=source, kind=LabSiteVersion.Kind.DJANGO,
            prompt=prompt, html=html, model="django-modules-v2", backend_modules=chosen, generation_key=key,
            input_tokens=0, output_tokens=0, edit_method="local", presentation=source.presentation if source else {})
    return version


@login_required
@require_GET
def lab_download(request, startup_id, version_id):
    startup = _owned_startup(request, startup_id)
    version = get_object_or_404(startup.lab_versions, pk=version_id, kind=LabSiteVersion.Kind.DJANGO)
    return FileResponse(export_project(version), as_attachment=True,
        filename=f"django-site-{str(startup.pk)[:8]}.zip", content_type="application/zip")


@login_required
@require_POST
def lab_run(request, startup_id, version_id):
    startup = _owned_startup(request, startup_id)
    version = get_object_or_404(startup.lab_versions, pk=version_id, kind=LabSiteVersion.Kind.DJANGO)
    as_json = request.headers.get('Accept') == 'application/json'
    if not _local_runtime(request):
        error = "Автозапуск доступен на компьютере с локальной лабораторией. Скачайте проект для отдельного запуска."
        if as_json:
            return JsonResponse({'error': error}, status=403)
        messages.error(request, error)
        return redirect(reverse_lab_version(startup, version))
    try:
        url = start_runtime(version, launcher_origin=request.build_absolute_uri('/').rstrip('/'),
            return_path=reverse_lab_version(startup, version) + '#backend-title')
        if as_json:
            response = JsonResponse({'url': url})
            response['Cache-Control'] = 'no-store'
            return response
        return redirect(url)
    except BuilderError as exc:
        if as_json:
            return JsonResponse({'error': str(exc)}, status=503)
        messages.error(request, str(exc))
        return redirect(reverse_lab_version(startup, version))


@login_required
@require_POST
def lab_stop(request, startup_id):
    startup = _owned_startup(request, startup_id)
    if not _local_runtime(request):
        return HttpResponse("Локальный автозапуск недоступен.", status=403)
    try:
        stop_runtime(startup.pk)
        messages.success(request, "Сайт остановлен. Аккаунты и переписки сохранены.")
    except BuilderError as exc:
        messages.error(request, str(exc))
    return redirect("lab", startup_id=startup.pk)


@login_required
@require_GET
def lab_preview(request, startup_id, version_id):
    startup = _owned_startup(request, startup_id)
    version = get_object_or_404(startup.lab_versions, pk=version_id)
    return preview_response(version.html, exit_viewer=True)


@lru_cache(maxsize=1)
def _tracker():
    return (settings.BASE_DIR / 'static/founder/js/lab-tracker.js').read_text()


def preview_response(html, *, channel=None, parent_origin=None, exit_viewer=False):
    if exit_viewer:
        # The opaque preview can only ask its direct parent to close the viewer.
        script = "<script>addEventListener('keydown',e=>{if(e.key==='Escape'&&!e.isComposing){e.preventDefault();parent.postMessage({type:'cofounder:lab-exit'},'*')}})</script>"
        html = re.sub(r'(<head\b[^>]*>)', lambda match: match[0] + script, html, count=1, flags=re.I)
    if channel:
        script = (f'<script data-channel="{escape(channel, quote=True)}" '
                  f'data-parent-origin="{escape(parent_origin, quote=True)}">{_tracker()}</script>')
        html = re.sub(r'(<head\b[^>]*>)', lambda match: match[0] + script, html, count=1, flags=re.I)
    response = HttpResponse(html, content_type="text/html; charset=utf-8")
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
