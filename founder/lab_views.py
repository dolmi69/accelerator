"""Private website laboratory and isolated previews for generated HTML."""

from uuid import UUID, uuid4
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
from django.views.decorators.cache import never_cache
from django.db import OperationalError, transaction

from founder.forms import LabPromptForm, LabCustomizeForm, LabBackendModulesForm
from founder.models import LabSiteVersion, StartupProfile, LabAIUsage
from founder.services.qwen import QwenError, QwenOutputError
from founder.services.site_generator import generate_site, GENERATOR_REVISION
from founder.services.lab_design import design_context, design_pending
from founder.services.lab_reply import version_reply, saved_reply, incomplete_reply, reviewed_reply
from founder.services.lab_bruno import request_policy, validate_result, LabRequestRejected
from founder.services import activity
from founder.services.access import get_startup
from founder.services.lab_testing import publication_for, test_results
from founder.services.django_builder import BuilderError, RuntimeSelectionChanged, export_project, runtime_status, start_runtime, stop_runtime, select_runtime_project
from founder.services.ai_costs import billing_scope, usage_summary
from founder.services.site_editor import customize, simple_command, text_command, replace_visible_text
from founder.services.backend_modules import module_command, normalize_modules, MODULES, OPTIONAL_MODULES, DEFAULT_OPTIONAL, MODULE_GROUPS
from founder.services.request_limits import RequestLimitExceeded, acquire_ai_lease, consume_limit, release_ai_lease


def _owned_startup(request, startup_id, *, edit=True):
    return get_startup(request, startup_id, edit=edit)


def _version(startup, value):
    try:
        version_id = UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise Http404("Версия сайта не найдена") from exc
    return get_object_or_404(startup.lab_versions.select_related('source'), pk=version_id)


def _lab_page(request, startup, form=None, *, status=200):
    versions = list(startup.lab_versions.select_related('source')[:20])
    selected = _version(startup, request.GET["version"]) if request.GET.get("version") else (
        versions[0] if versions else None
    )
    if form is not None and request.POST.get('source_version'):
        selected = _version(startup, request.POST['source_version'])
    if selected and selected not in versions:
        versions.append(selected)
    for version in versions:
        version.history_reply = saved_reply(version)
    backend_form = LabBackendModulesForm(request.POST if form is not None and request.POST.get('modules_selected') else None, initial={'modules': [key for key in selected.backend_modules if key in OPTIONAL_MODULES]
        if selected and selected.kind == 'django' else DEFAULT_OPTIONAL})
    checkboxes = {widget.data['value']: widget for widget in backend_form['modules']}
    section = 'globalization' if request.GET.get('section') == 'globalization' and form is None else 'development'
    reply = saved_reply(selected)
    notice = request.session.pop('lab_reply_notice', None)
    if selected and notice and notice.get('startup') == str(startup.pk) and notice.get('version') == str(selected.pk):
        reply = notice['reply']
    if status >= 400:
        reply = {'title': 'Не получилось завершить запрос',
                 'items': [str(error) for errors in form.errors.values() for error in errors] if form is not None else ['Попробуй ещё раз.'],
                 'usage': '', 'mood': 'focused', 'failed': True}
    # Separate users and fresh notices; reloading a seen version keeps its timer.
    reply_key = hashlib.sha256(json.dumps([
        request.user.pk, str(startup.pk), str(selected.pk) if selected else None, reply,
        str(uuid4()) if reply.get('fresh') or status >= 400 else None,
    ], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return render(request, "founder/lab.html", {
        "startup": startup,
        "workspace_tab": "lab",
        "form": form or LabPromptForm(source=selected, initial={"kind": selected.kind if selected else LabSiteVersion.Kind.STATIC}),
        'backend_form': backend_form,
        'module_groups': [{'title': title, 'fields': [checkboxes[key] for key in keys]} for title, keys in MODULE_GROUPS],
        'lab_section': section,
        'backend_labels':[MODULES[key][0] for key in normalize_modules(selected.backend_modules)] if selected and selected.kind == 'django' else [],
        "usage": usage_summary(request.user),
        "selected": selected,
        "lab_reply": reply,
        "lab_reply_key": reply_key if selected or status >= 400 else '',
        "lab_reply_mascot": {"mood": reply["mood"], "get_mood_display": "готов помочь"},
        "design_pending": design_pending(selected),
        "large_design": bool(selected and len(selected.html.encode('utf-8')) > 18_000),
        "versions": versions,
        "publication": publication_for(startup),
        "results": test_results(selected) if selected else None,
        "card_published": hasattr(startup, 'project_card') and startup.project_card.published_at is not None,
        "runtime_available": _local_runtime(request),
        "runtime_paused": request.GET.get('paused') == '1',
        "runtime": runtime_status(startup.pk) if _local_runtime(request) else None,
    }, status=status)


def _reply_redirect(request, startup, version, source=None, *, cached=False, incomplete=False):
    reply = None
    if incomplete:
        reply = incomplete_reply(source)
    elif cached:
        reply = {**saved_reply(version), 'title': 'Открыл сохранённый результат',
                 'usage': 'Новый AI-запрос не понадобился · 0 токенов'}
    elif source and version.pk == source.pk:
        reply = {'title': 'Всё уже настроено', 'items': ['Новых изменений нет: эти настройки уже были включены.'],
                 'usage': 'Без AI · 0 токенов', 'mood': 'focused'}
    if reply:
        reply['fresh'] = True
        request.session['lab_reply_notice'] = {'startup': str(startup.pk), 'version': str(version.pk), 'reply': reply}
    return redirect(reverse_lab_version(startup, version))


@login_required
@require_GET
@never_cache
def lab(request, startup_id):
    return _lab_page(request, _owned_startup(request, startup_id, edit=False))


def generation_key(startup, source, prompt, kind, scope="auto", rebuild=False, modules=None, context=None):
    return hashlib.sha256(json.dumps([
        GENERATOR_REVISION, str(startup.pk), str(source.pk) if source else None,
        hashlib.sha256(source.html.encode()).hexdigest() if source else None,
        prompt, kind, scope, rebuild, settings.QWEN_CODE_MODEL,
        settings.LAB_CREATE_MAX_TOKENS, settings.LAB_PATCH_MAX_TOKENS,
        settings.QWEN_CODE_MAX_TOKENS, settings.LAB_CREATE_TEMPERATURE, settings.LAB_PATCH_TEMPERATURE,
        modules if modules is not None else source.backend_modules if source else [],
        context,
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
    try:
        request_policy(prompt)
    except LabRequestRejected as exc:
        form.add_error(None, str(exc))
        return _lab_page(request, startup, form, status=400)
    kind = form.cleaned_data["kind"]
    chosen = source.backend_modules if source and source.kind == 'django' else normalize_modules() if kind == 'django' else []
    if request.POST.get('modules_selected'):
        module_form = LabBackendModulesForm(request.POST)
        if not module_form.is_valid():
            form.add_error(None, 'Выберите модули из списка.')
            return _lab_page(request, startup, form, status=400)
        chosen = normalize_modules(module_form.cleaned_data['modules'])
        if module_form.cleaned_data['modules'] or (source and source.kind == 'django'):
            kind = LabSiteVersion.Kind.DJANGO
        else:
            chosen = []
    scope = form.cleaned_data["edit_scope"] or "auto"
    rebuild = form.cleaned_data["rebuild"]
    rename = text_command(prompt) if source and scope == 'auto' and not rebuild and not simple_command(prompt) else None
    if rename and chosen == source.backend_modules:
        old, new = rename
        replacements = dict(source.presentation.get('text_replacements', {}))
        # A second rename updates the original label's override as well.
        for original, current in list(replacements.items()):
            if current.casefold() == old.casefold():
                replacements[original] = new
        replacements[old] = new
        html_changed = replace_visible_text(source.html, {old: new}) != source.html
        template_changed = False
        if source.kind == LabSiteVersion.Kind.DJANGO:
            from founder.services.django_builder import project_files
            template_changed = any(replace_visible_text(content.decode(), {old: new}) != content.decode()
                for name, content in project_files(source).items()
                if name.startswith('templates/') and name.endswith('.html'))
        if not html_changed and not template_changed:
            form.add_error(None, f'Надпись «{old}» не найдена в этой версии сайта. Укажите её точный текст.')
            return _lab_page(request, startup, form, status=400)
        if len(replacements) > 10:
            form.add_error(None, 'Достигнут лимит сохранённых замен текста для этой версии.')
            return _lab_page(request, startup, form, status=400)
        presentation = {**source.presentation, 'text_replacements': replacements}
        version = _customized_version(startup, source, presentation, prompt, kind)
        messages.success(request, f'Надпись заменена на «{new}» без AI: 0 токенов.')
        return _reply_redirect(request, startup, version, source)
    modules_command = module_command(prompt) if scope == 'auto' and not rebuild else None
    if modules_command:
        chosen = normalize_modules([*chosen,*modules_command])
        version = _backend_version(startup,source,chosen,prompt)
        _log_version(request, startup, source, version)
        messages.success(request,'Готовые модули подключены без AI: 0 токенов. Запустите сайт ниже.')
        return _reply_redirect(request, startup, version, source)
    context = design_context(startup, chosen, source.presentation if source else {}, source)
    key = generation_key(startup, source, prompt, kind, scope, rebuild, chosen, context)
    cached = startup.lab_versions.filter(generation_key=key).first()
    if cached:
        messages.success(request, "Открыли уже сохранённый результат. Новый AI-запрос не понадобился.")
        return _reply_redirect(request, startup, cached, source, cached=True)
    local = simple_command(prompt) if source and scope == "auto" and not rebuild else None
    if local and (not source or chosen == source.backend_modules):
        version = _customized_version(startup, source, {**source.presentation, **local}, prompt, kind, key)
        _log_version(request, startup, source, version)
        messages.success(request, "Оформление изменено без AI: 0 токенов.")
        return _reply_redirect(request, startup, version, source)
    generating_design = False
    feedback = None
    presentation = source.presentation if source else {}
    try:
        # Check after free edits/cache lookup. Middleware releases this lease.
        with transaction.atomic():
            # One Qwen call plus validation; no paid planning/review rounds.
            request.ai_lease = acquire_ai_lease(request.user.pk,
                ttl_seconds=max(600, int(settings.QWEN_CODE_TIMEOUT) + 180))
            consume_limit(f"ai-minute:{request.user.pk}", settings.AI_REQUESTS_PER_MINUTE, 60)
            consume_limit(f"ai-day:{request.user.pk}", settings.AI_REQUESTS_PER_DAY, 86400)
            # Zero disables the personal cap during local development.
            if settings.LAB_REQUESTS_PER_DAY > 0:
                consume_limit(f"lab-day:{request.user.pk}", settings.LAB_REQUESTS_PER_DAY, 86400)
            consume_limit("lab-global-day", settings.LAB_GLOBAL_REQUESTS_PER_DAY, 86400)
        # A concurrent request may have completed between cache lookup and lease.
        cached = startup.lab_versions.filter(generation_key=key).first()
        if cached:
            return _reply_redirect(request, startup, cached, source, cached=True)
        generating_design = True
        result = _generate_design(request, startup, source, prompt, kind, scope, rebuild, chosen, context)
        generating_design = False
        intent = result.intent
        if intent:
            presentation = {**presentation, **intent['settings']}
        if result.add_modules:
            chosen = normalize_modules([*chosen, *result.add_modules])
            kind = LabSiteVersion.Kind.DJANGO
        feedback = validate_result(prompt, source, result, chosen)
    except RequestLimitExceeded as exc:
        form.add_error(None, str(exc))
        return _lab_page(request, startup, form, status=429)
    except OperationalError:
        form.add_error(None, "Не удалось проверить лимиты. Повторите запрос позже.")
        return _lab_page(request, startup, form, status=503)
    except LabRequestRejected as exc:
        form.add_error(None, str(exc))
        return _lab_page(request, startup, form, status=400)
    except QwenOutputError as exc:
        if generating_design and kind == LabSiteVersion.Kind.DJANGO:
            version = _backend_version(startup, source, chosen, prompt, report=incomplete_reply(source))
            messages.warning(request, 'AI не завершил дизайн. Сохранили рабочую Django-основу с выбранными модулями без дополнительного AI-запроса. Незавершённый ответ мог быть оплачен; повторную генерацию запускайте только по желанию.')
            return _reply_redirect(request, startup, version, source, incomplete=True)
        form.add_error(None, str(exc))
        return _lab_page(request, startup, form, status=503)
    except QwenError as exc:
        form.add_error(None, str(exc))
        return _lab_page(request, startup, form, status=503)
    finally:
        # ASGI clients can disconnect before response middleware runs. The
        # synchronous AI work still ends here, so release its exact lease token.
        token = getattr(request, "ai_lease", None)
        if token is not None:
            try:
                release_ai_lease(request.user.pk, token)
            except OperationalError:
                pass  # Expiry remains the fallback if the database is unavailable.

    version = _create_version(
        startup=startup, source=source, prompt=prompt, kind=kind,
        html=result.text, model=result.model,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        generation_key=key, edit_method=result.edit_method,
        presentation=presentation, backend_modules=chosen, feedback=feedback,
    )
    if result.request_id or result.request_ids:
        LabAIUsage.objects.filter(pk__in=result.request_ids or (result.request_id,), user=request.user, startup=startup).update(version=version)
    _log_version(request, startup, source, version)
    messages.success(request, "Готовая правка применена. Qwen распознал запрос; генерация кода не понадобилась." if intent else "Правка сохранена. Отчёт Qwen и ограничения — под прототипом.")
    return redirect("lab", startup_id=startup.pk)


def _generate_design(request, startup, source, prompt, kind, scope, rebuild, chosen, context):
    options = {"backend": True} if kind == LabSiteVersion.Kind.DJANGO else {}
    if scope != "auto":
        options["edit_scope"] = scope
    if rebuild:
        options["rebuild"] = True
    # The ready scaffold has no design yet. Its first design needs a complete
    # document, rather than a small patch to a placeholder page.
    initial_design = design_pending(source)
    with billing_scope(request.user, startup, "patch" if source and not rebuild and not initial_design else "create"):
        result = generate_site(prompt, previous_html=source.html if source and not initial_design else "",
            max_tokens=min(settings.LAB_CREATE_MAX_TOKENS, settings.QWEN_CODE_MAX_TOKENS),
            project_context=context, report=True, **options)
    return result


def _create_version(*, report=None, feedback=None, **fields):
    version = LabSiteVersion(**fields)
    version.bruno_report = report if report is not None else reviewed_reply(version, feedback) if feedback else version_reply(version)
    version.save(force_insert=True)
    return version
def _log_version(request, startup, source, version):
    if source is None or version.pk != source.pk:
        activity.log(startup, request.user, activity.Kind.LAB, f"Новая версия сайта: {activity.quoted(version.prompt, 70)}")


def _customized_version(startup, source, presentation, prompt, kind=None, key=None):
    html = customize(source.html, presentation)
    if html == source.html and presentation == source.presentation and (kind is None or kind == source.kind):
        return source
    return _create_version(startup=startup, source=source, kind=kind or source.kind,
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
    version = _customized_version(startup, source, {**source.presentation, **form.cleaned_data}, "Оформление без AI")
    _log_version(request, startup, source, version)
    messages.success(request, "Настройки сохранены без расхода AI-токенов.")
    return _reply_redirect(request, startup, version, source)


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
    chosen = normalize_modules(form.cleaned_data['modules']) if request.POST.get('modules_selected') else normalize_modules(source.backend_modules if source and source.kind == 'django' else DEFAULT_OPTIONAL)
    version = _backend_version(startup,source,chosen,'Готовые модули Django')
    _log_version(request, startup, source, version)
    messages.success(request, "Модули сохранены без AI. Можно запустить сайт или скачать проект.")
    return _reply_redirect(request, startup, version, source)


def _backend_version(startup,source,chosen,prompt,*,report=None):
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
        version = _create_version(report=report, startup=startup, source=source, kind=LabSiteVersion.Kind.DJANGO,
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
def lab_activate(request, startup_id):
    startup = _owned_startup(request, startup_id)
    if not _local_runtime(request):
        return JsonResponse({'error': 'Локальный автозапуск недоступен.'}, status=403)
    try:
        response = JsonResponse(select_runtime_project(startup))
        response['Cache-Control'] = 'no-store'
        return response
    except BuilderError as exc:
        return JsonResponse({'error': str(exc)}, status=503)


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
        options = {'selection_token': request.POST['selection_token']} if request.POST.get('selection_token') else {}
        url = start_runtime(version, launcher_origin=request.build_absolute_uri('/').rstrip('/'),
            return_path=reverse_lab_version(startup, version) + '#backend-title', **options)
        if request.POST.get('preview') == '1':
            # The embedded preview opens the website, never an owner setup token.
            # Setup remains an explicit action in the laboratory's version menu.
            origin = urlsplit(url)
            url = f'{origin.scheme}://{origin.netloc}/'
        if as_json:
            response = JsonResponse({'url': url})
            response['Cache-Control'] = 'no-store'
            return response
        return redirect(url)
    except BuilderError as exc:
        if as_json:
            return JsonResponse({'error': str(exc)}, status=409 if isinstance(exc, RuntimeSelectionChanged) else 503)
        messages.error(request, str(exc))
        return redirect(reverse_lab_version(startup, version))


@login_required
@require_POST
def lab_stop(request, startup_id):
    startup = _owned_startup(request, startup_id)
    source = _version(startup, request.POST['source_version']) if request.POST.get('source_version') else None
    if not _local_runtime(request):
        return HttpResponse("Локальный автозапуск недоступен.", status=403)
    try:
        stop_runtime(startup.pk)
        messages.success(request, "Сайт остановлен. Аккаунты и переписки сохранены.")
    except BuilderError as exc:
        messages.error(request, str(exc))
    # Reloading the laboratory after an explicit stop must not restart the site.
    if source:
        return redirect(reverse_lab_version(startup, source) + '&paused=1')
    from django.urls import reverse
    return redirect(reverse('lab', args=[startup.pk]) + '?paused=1')


@login_required
@require_GET
def lab_preview(request, startup_id, version_id):
    startup = _owned_startup(request, startup_id, edit=False)
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
