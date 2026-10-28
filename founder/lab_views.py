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
from founder.models import LabSiteVersion, StartupProfile, LabAIUsage, LabConversationTurn
from founder.services.lab_conversation import route_message, discussion_reply, local_answer, suggestion_for
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
from founder.services import lab_models


def _owned_startup(request, startup_id, *, edit=True):
    return get_startup(request, startup_id, edit=edit)



def _task_handoff(request, startup):
    """Resolve a project-scoped task/feature without changing task or lab state."""
    if not (request.GET.get('task') or request.GET.get('feature')):
        return None
    _owned_startup(request, startup.pk, edit=True)
    from django.urls import reverse
    from founder.services import mvp
    from founder.services.work_priorities import can_use_lab, linked_features
    if bool(request.GET.get('task')) == bool(request.GET.get('feature')):
        raise Http404('Выберите одно задание или функцию.')
    key = 'task' if request.GET.get('task') else 'feature'
    try:
        identifier = UUID(request.GET[key])
    except (ValueError, TypeError, AttributeError) as exc:
        raise Http404('Задание не найдено.') from exc
    task, feature = None, None
    if key == 'task':
        task = get_object_or_404(startup.bruno_tasks, pk=identifier, status='todo')
        feature = linked_features(startup, mvp.board(startup)).get(str(task.pk))
        if not can_use_lab(task, feature):
            raise Http404('Это задание выполняется вне лаборатории.')
    else:
        feature = get_object_or_404(startup.feature_candidates.select_related('task'), pk=identifier)
        task = feature.task
        if task and not can_use_lab(task, feature):
            raise Http404('Это задание сейчас недоступно для выполнения.')
    anchor = f'task-{task.pk}' if task else f'feature-{feature.pk}'
    return {'task': task, 'feature': feature, 'title': task.title if task else feature.title,
            'return_url': reverse('tasks', args=[startup.pk]) + ('?method=ice' if request.GET.get('method') == 'ice' else '') + '#' + anchor}

def _version(startup, value):
    try:
        version_id = UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise Http404("Версия сайта не найдена") from exc
    return get_object_or_404(startup.lab_versions.select_related('source'), pk=version_id)


def _model_label(model):
    return {'local-editor': 'Без AI', 'openai/gpt-6-luna': 'GPT‑6 Luna',
            'openai/gpt-6.1-sol': 'GPT‑6.1 Sol',
            'anthropic/claude-sonnet-5.5': 'Claude Sonnet 5.5'}.get(model, model)


def _consume_lab_limits(user, *, personal=False):
    if settings.LAB_UNRESTRICTED_TEST_MODE:
        return
    consume_limit(f'ai-minute:{user.pk}', settings.AI_REQUESTS_PER_MINUTE, 60)
    consume_limit(f'ai-day:{user.pk}', settings.AI_REQUESTS_PER_DAY, 86400)
    if personal:
        from founder.services.plans import quota
        daily_limit = quota(user, 'lab_requests_per_day')
        if daily_limit > 0:
            consume_limit(f'lab-day:{user.pk}', daily_limit, 86400)
    consume_limit('lab-global-day', settings.LAB_GLOBAL_REQUESTS_PER_DAY, 86400)


def _lab_page(request, startup, form=None, *, status=200):
    if getattr(request, 'lab_background_job', False):
        from founder.services.lab_jobs import JobFailure
        errors = [str(error) for items in form.errors.values() for error in items] if form is not None else []
        raise JobFailure(' '.join(errors) or 'Не удалось завершить запрос. Предыдущий сайт сохранён.')
    from founder.models import LabGenerationJob
    from founder.services.lab_jobs import ACTIVE, describe, refresh
    handoff = _task_handoff(request, startup) if form is None and request.method == 'GET' else None
    jobs = LabGenerationJob.objects.filter(startup=startup)
    job = refresh(jobs.filter(status__in=ACTIVE).first() or jobs.first())
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
        version.model_label = _model_label(version.model)
    if selected:
        selected.model_label = _model_label(selected.model)
    timeline = [{'version': version, 'created_at': version.created_at} for version in versions]
    for turn in startup.lab_conversation.select_related('user')[:20]:
        turn.reply = {**turn.reply, 'suggestion': suggestion_for(turn.prompt, turn.reply)}
        timeline.append({'discussion': turn, 'created_at': turn.created_at})
    timeline.sort(key=lambda turn: turn['created_at'])
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
        request.lab_failure_message = ' '.join(reply['items'])[:1600]
    # Separate users and fresh notices; reloading a seen version keeps its timer.
    reply_key = hashlib.sha256(json.dumps([
        request.user.pk, str(startup.pk), str(selected.pk) if selected else None, reply,
        str(uuid4()) if reply.get('fresh') or status >= 400 else None,
    ], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if form is None:
        initial = {"kind": LabSiteVersion.Kind.GENERATED if settings.LAB_RAW_AI_TEST_MODE else selected.kind if selected else LabSiteVersion.Kind.STATIC}
        if handoff:
            from founder.services.work_priorities import lab_prompt
            initial['prompt'] = lab_prompt(startup, task=handoff['task'], feature=handoff['feature'], source=selected)
            initial['message_mode'] = 'edit'
            if selected and selected.kind in (LabSiteVersion.Kind.STATIC, LabSiteVersion.Kind.GENERATED):
                initial['kind'] = selected.kind
        form = LabPromptForm(source=selected, initial=initial)
    from founder.hosting import context as hosting_context
    return render(request, "founder/lab.html", {
        "hosting": hosting_context(startup, selected),
        "startup": startup,
        "lab_handoff": handoff,
        "lab_history_entry": selected.progress_entry if selected and hasattr(selected, "progress_entry") else None,
        "lab_job_state": describe(job),
        "lab_job_active": bool(job and job.status in ACTIVE and job.mode == "edit"),
        "workspace_tab": "lab",
        "raw_ai_test": settings.LAB_RAW_AI_TEST_MODE,
        "backend_staged": settings.LAB_BACKEND_STAGED_GENERATION,
        "lab_model_label": _model_label(settings.OFOX_CODE_MODEL if settings.LAB_CODE_PROVIDER == 'ofox' else settings.QWEN_CODE_MODEL),
        "lab_provider_label": 'Ofox' if settings.LAB_CODE_PROVIDER == 'ofox' else 'Cloud.ru',
        "lab_model_routing": lab_models.enabled(),
        "form": form,
        'backend_form': backend_form,
        'module_groups': [{'title': title, 'fields': [checkboxes[key] for key in keys]} for title, keys in MODULE_GROUPS],
        'lab_section': section,
        'backend_labels':[MODULES[key][0] for key in normalize_modules(selected.backend_modules)] if selected and selected.kind == 'django' else [],
        "usage": usage_summary(request.user, startup),
        "selected": selected,
        "lab_reply": reply,
        "lab_reply_key": reply_key if selected or status >= 400 else '',
        "lab_reply_mascot": {"mood": reply["mood"], "get_mood_display": "готов помочь"},
        "design_pending": design_pending(selected) and not settings.LAB_RAW_AI_TEST_MODE,
        "large_design": bool(selected and len(selected.html.encode('utf-8')) > 18_000),
        "versions": versions,
        "lab_timeline": timeline,
        "publication": publication_for(startup),
        "results": test_results(selected) if selected else None,
        "card_published": hasattr(startup, 'project_card') and startup.project_card.published_at is not None,
        "runtime_available": _local_runtime(request),
        "runtime_paused": request.GET.get('paused') == '1',
        "runtime": runtime_status(startup.pk) if _local_runtime(request) else None,
    }, status=status)


def _reply_redirect(request, startup, version, source=None, *, cached=False, incomplete=False):
    from founder.services.project_history import record_lab_result
    record_lab_result(request, startup, source, version)
    request.lab_result_version = version.pk
    reply = None
    if incomplete:
        reply = saved_reply(version)
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


def generation_key(startup, source, prompt, kind, scope="auto", rebuild=False, modules=None, context=None, code_model=None):
    code_profile = (f"ofox/{code_model or settings.OFOX_CODE_MODEL}/{settings.OFOX_CODE_REASONING_EFFORT}/{settings.OFOX_CODE_API}/{settings.OFOX_PROVIDER_TYPE or 'auto'}"
                    if settings.LAB_CODE_PROVIDER == "ofox" else settings.QWEN_CODE_MODEL)
    return hashlib.sha256(json.dumps([
        GENERATOR_REVISION, str(startup.pk), str(source.pk) if source else None,
        settings.LAB_RAW_AI_TEST_MODE,
        hashlib.sha256(source.html.encode()).hexdigest() if source else None,
        prompt, kind, scope, rebuild, code_profile,
        settings.LAB_CREATE_MAX_TOKENS, settings.LAB_PATCH_MAX_TOKENS,
        settings.QWEN_CODE_MAX_TOKENS, settings.LAB_CREATE_TEMPERATURE, settings.LAB_PATCH_TEMPERATURE,
        modules if modules is not None else source.backend_modules if source else [],
        context, lab_models.POLICY if code_model else None,
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
    if source and not form.cleaned_data['rebuild']:
        from founder.services.site_names import rename_site
        from founder.services.lab_task_history import validate_context
        try:
            request_policy(form.cleaned_data['prompt'])
            change = rename_site(source, form.cleaned_data['prompt'])
            if change:
                validate_context(request, startup)
                if change['html'] == source.html:
                    messages.success(request, f'Название уже указано как «{change["new"]}». AI не использовался.')
                    return _reply_redirect(request, startup, source, source)
                report = {'title':'Название изменено',
                          'items':[f'Название сайта изменено с «{change["old"]}» на «{change["new"]}».',
                                   'Обновлены название вкладки, бренд и его подписи. Серверный код, CSS и JavaScript сохранены без изменений.'],
                          'usage':'Без AI · 0 токенов', 'mood':'focused'}
                version = _create_version(startup=startup, source=source, prompt=form.cleaned_data['prompt'],
                    kind=source.kind, html=change['html'], backend_files=change['files'],
                    backend_modules=source.backend_modules, presentation=source.presentation,
                    model='local-editor', edit_method='local', input_tokens=0, output_tokens=0, report=report)
                messages.success(request, 'Название изменено без AI: 0 токенов.')
                return _reply_redirect(request, startup, version, source)
        except QwenOutputError as exc:
            form.add_error(None, str(exc))
            return _lab_page(request, startup, form, status=400)
    if request.POST.get('automatic') == '1':
        return _lab_automatic(request, startup, source, form)
    return _lab_execute(request, startup, source, form)


def _lab_automatic(request, startup, source, form):
    """Keep planning and execution under one lease and one user request quota."""
    token = None
    try:
        from founder.services.lab_intent import decide
        from founder.services.lab_jobs import remember_lease
        protect_prompt = form.cleaned_data['prompt']
        from founder.services.lab_bruno import protect_service_secrets
        protect_service_secrets(protect_prompt)
        with transaction.atomic():
            token = acquire_ai_lease(request.user.pk, ttl_seconds=max(600,
                int(settings.QWEN_CODE_TIMEOUT) * 8 + 180))
            request.ai_lease = token
            remember_lease(request, token)
            _consume_lab_limits(request.user)
        from founder.services.lab_jobs import remember_model_route
        planning = lab_models.planning_route(source)
        request.lab_planning_route = planning
        remember_model_route(request, planning)
        with lab_models.model_scope(planning['model'] if planning else None):
            with billing_scope(request.user, startup, 'intent'):
                decision, result = decide(startup, source, protect_prompt)
        request.lab_automatic_decision = decision
        job_id = getattr(request, 'lab_job_id', None)
        if job_id:
            from founder.models import LabGenerationJob
            job = LabGenerationJob.objects.get(pk=job_id)
            job_payload = {**job.payload, '_intent_route': [decision['action']]}
            LabGenerationJob.objects.filter(pk=job_id, status='running').update(
                mode='edit' if decision['action'] == 'edit' else 'discuss', payload=job_payload)
        if decision['action'] != 'edit':
            request.lab_prepared_answer = ({'answer': decision['answer'],
                'suggestion': decision['suggestion'], 'local': False}, result)
            return _lab_discuss(request, startup, source, form)
        form.cleaned_data['kind'] = decision['kind']
        # Personal generation quota is checked only for a real edit.
        if not settings.LAB_UNRESTRICTED_TEST_MODE:
            from founder.services.plans import quota
            daily_limit = quota(request.user, 'lab_requests_per_day')
            if daily_limit > 0:
                consume_limit(f'lab-day:{request.user.pk}', daily_limit, 86400)
        return _lab_execute(request, startup, source, form)
    except (QwenError, RequestLimitExceeded, LabRequestRejected, OperationalError) as exc:
        error = 'Сервис занят. Попробуйте позже.' if isinstance(exc, OperationalError) else str(exc)
        form.add_error(None, error)
        return _lab_page(request, startup, form, status=429 if isinstance(exc, RequestLimitExceeded) else 503)
    finally:
        if token:
            try:
                release_ai_lease(request.user.pk, token)
            except OperationalError:
                pass
            request.ai_lease = None


def _lab_execute(request, startup, source, form):
    prompt = form.cleaned_data["prompt"]
    # Legacy clients explicitly call the generator. The new composer sends auto.
    if not getattr(request, 'lab_automatic_decision', None) and route_message(prompt, form.cleaned_data.get('message_mode') or 'edit', has_site=bool(source)) == 'discuss':
        return _lab_discuss(request, startup, source, form)
    from founder.services.lab_task_history import validate_context
    validate_context(request, startup)
    try:
        request_policy(prompt)
    except LabRequestRejected as exc:
        form.add_error(None, str(exc))
        return _lab_page(request, startup, form, status=400)
    automatic = getattr(request, 'lab_automatic_decision', None)
    raw = ((settings.LAB_RAW_AI_TEST_MODE and not (automatic and source and source.kind == 'django'))
           or form.cleaned_data['kind'] == LabSiteVersion.Kind.GENERATED)
    kind = (LabSiteVersion.Kind.GENERATED if form.cleaned_data['kind'] == LabSiteVersion.Kind.GENERATED
            else LabSiteVersion.Kind.STATIC if raw else form.cleaned_data["kind"])
    chosen = [] if raw else source.backend_modules if source and source.kind == 'django' else normalize_modules() if kind == 'django' else []
    if not raw and request.POST.get('modules_selected'):
        module_form = LabBackendModulesForm(request.POST)
        if not module_form.is_valid():
            form.add_error(None, 'Выберите модули из списка.')
            return _lab_page(request, startup, form, status=400)
        chosen = normalize_modules(module_form.cleaned_data['modules'])
        if module_form.cleaned_data['modules'] or (source and source.kind == 'django'):
            kind = LabSiteVersion.Kind.DJANGO
        else:
            chosen = []
    scope = "auto" if raw else form.cleaned_data["edit_scope"] or "auto"
    rebuild = True if raw else form.cleaned_data["rebuild"]
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
    context = design_context(startup, chosen, {} if raw else source.presentation if source else {}, source)
    if request.POST.get('task_context'):
        context['task_context'] = {'id':request.POST['task_context'], 'digest':request.POST.get('_task_digest', '')}
    if automatic:
        context['interpreted_request'] = automatic['task']
        if automatic.get('edit_files') is not None:
            context['edit_files'] = automatic['edit_files']
    # Raw mode used to force rebuild=True for every followup. Only an explicit
    # rebuild control may bypass file-scoped edits of an existing application.
    context['full_rebuild'] = bool(form.cleaned_data['rebuild'])
    if raw:
        context['mode'] = 'generated_backend' if kind == LabSiteVersion.Kind.GENERATED else 'raw_static_experiment'
    # An experiment must invoke the model even for identical prompts.
    model_route = lab_models.generation_route(source, automatic, kind=kind, rebuild=form.cleaned_data['rebuild'], prompt=prompt)
    key = None if raw else generation_key(startup, source, prompt, kind, scope, rebuild, chosen, context,
                                         code_model=model_route['model'] if model_route else None)
    cached = startup.lab_versions.filter(generation_key=key).first() if key else None
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
    presentation = {} if raw else source.presentation if source else {}
    try:
        # Check after free edits/cache lookup. Middleware releases this lease.
        if not getattr(request, 'ai_lease', None):
            with transaction.atomic():
                request.ai_lease = acquire_ai_lease(request.user.pk,
                    ttl_seconds=max(600, int(settings.QWEN_CODE_TIMEOUT) *
                        (8 if source else 2 if kind == LabSiteVersion.Kind.GENERATED and settings.LAB_BACKEND_STAGED_GENERATION else 1) + 180))
                from founder.services.lab_jobs import remember_lease
                remember_lease(request, request.ai_lease)
                _consume_lab_limits(request.user, personal=True)
        # A concurrent request may have completed between cache lookup and lease.
        cached = startup.lab_versions.filter(generation_key=key).first() if key else None
        if cached:
            return _reply_redirect(request, startup, cached, source, cached=True)
        generating_design = True
        from founder.services.lab_jobs import remember_model_route
        remember_model_route(request, model_route)
        with lab_models.model_scope(model_route['model'] if model_route else None):
            # Followups are resolved by the intent stage. Sending only "давай
            # обратно" to the code stages lets stale reports override the task.
            effective_prompt = automatic['task'] if automatic else prompt
            result = _generate_design(request, startup, source, effective_prompt, kind, scope, rebuild, chosen, context)
        generating_design = False
        intent = result.intent
        if intent:
            presentation = {**presentation, **intent['settings']}
        if result.add_modules:
            chosen = normalize_modules([*chosen, *result.add_modules])
            kind = LabSiteVersion.Kind.DJANGO
        feedback = ({**result.report, 'checks': {'generation_mode': 'generated_backend',
                    'review_mode': 'local', 'runtime_verified': False,
                    'edit_plan':result.report.get('edit_plan')}} if kind == LabSiteVersion.Kind.GENERATED
                    else validate_result(prompt, source, result, chosen))
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
            failure_report = incomplete_reply(source)
            failure_report["items"] = [str(exc), *failure_report["items"]]
            version = _backend_version(startup, source, chosen, prompt, report=failure_report)
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
        presentation=presentation, backend_modules=chosen, backend_files=result.backend_files, feedback=feedback,
        routing=model_route,
    )
    if result.request_id or result.request_ids:
        LabAIUsage.objects.filter(pk__in=result.request_ids or (result.request_id,), user=request.user, startup=startup).update(version=version)
    _log_version(request, startup, source, version)
    messages.success(request, "Готовая правка применена; генерация кода не понадобилась." if intent else "Правка сохранена. Отчёт Бруно — в истории сообщений.")
    return redirect("lab", startup_id=startup.pk)


@login_required
@require_POST
def lab_route(request, startup_id):
    startup = _owned_startup(request, startup_id)
    source = _version(startup, request.POST['source_version']) if request.POST.get('source_version') else None
    form = LabPromptForm(request.POST, source=source)
    if not form.is_valid():
        return JsonResponse({'error': 'Напишите сообщение до 2000 символов.'}, status=400)
    from founder.services.lab_bruno import protect_service_secrets
    try:
        protect_service_secrets(form.cleaned_data['prompt'])
    except LabRequestRejected as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    return JsonResponse({'route': route_message(form.cleaned_data['prompt'],
        form.cleaned_data.get('message_mode') or 'auto', has_site=bool(source))})


@login_required
@require_POST
def lab_discuss(request, startup_id):
    startup = _owned_startup(request, startup_id)
    source = _version(startup, request.POST['source_version']) if request.POST.get('source_version') else None
    form = LabPromptForm(request.POST, source=source)
    if not form.is_valid():
        return JsonResponse({'error': 'Напишите сообщение до 2000 символов.'}, status=400)
    return _lab_discuss(request, startup, source, form)


def _lab_discuss(request, startup, source, form):
    """Only discussion records and usage may be written here, never site versions."""
    as_json = 'application/json' in request.headers.get('Accept', '')
    try:
        turn_id = UUID(request.POST['request_id']) if request.POST.get('request_id') else uuid4()
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Некорректный идентификатор сообщения.'}, status=400)

    def response(turn):
        if as_json:
            return JsonResponse({'id': str(turn.pk), 'prompt': turn.prompt, 'reply': turn.reply,
                                 'created_at': turn.created_at.isoformat()})
        from django.urls import reverse
        return redirect(reverse_lab_version(startup, source) if source else reverse('lab', args=[startup.pk]))

    existing = LabConversationTurn.objects.filter(pk=turn_id).first()
    if existing:
        if existing.startup_id != startup.pk or existing.user_id != request.user.pk:
            raise Http404('Сообщение не найдено')
        return response(existing)
    prompt = form.cleaned_data['prompt']
    token = None
    try:
        from founder.services.lab_bruno import protect_service_secrets
        protect_service_secrets(prompt)
        is_local = local_answer(prompt, source) is not None
        if not is_local and not getattr(request, 'lab_prepared_answer', None):
            with transaction.atomic():
                token = acquire_ai_lease(request.user.pk, ttl_seconds=max(600, settings.QWEN_CODE_TIMEOUT + 180))
                request.ai_lease = token
                from founder.services.lab_jobs import remember_lease
                remember_lease(request, token)
                _consume_lab_limits(request.user)
            existing = LabConversationTurn.objects.filter(pk=turn_id, startup=startup, user=request.user).first()
            if existing:
                return response(existing)
        planning = getattr(request, 'lab_planning_route', None) or lab_models.planning_route(source)
        from founder.services.lab_jobs import remember_model_route
        remember_model_route(request, planning)
        with lab_models.model_scope(planning['model'] if planning else None):
            with billing_scope(request.user, startup, 'discuss'):
                reply, result = getattr(request, 'lab_prepared_answer', None) or discussion_reply(startup, source, prompt)
        if planning and result:
            reply['routing'] = planning
        reply['usage'] = ('Без AI · 0 токенов' if reply.get('local') else
                          f'AI-ответ · {result.input_tokens + result.output_tokens} токенов'
                          if result.input_tokens is not None and result.output_tokens is not None else 'AI-ответ · расход уточняется')
        turn, _ = LabConversationTurn.objects.get_or_create(pk=turn_id, defaults={
            'startup': startup, 'user': request.user, 'source': source, 'prompt': prompt, 'reply': reply,
            'model': result.model if result else 'saved-report',
            'input_tokens': (result.input_tokens or 0) if result else 0,
            'output_tokens': (result.output_tokens or 0) if result else 0,
        })
        if result and result.request_id:
            LabAIUsage.objects.filter(pk=result.request_id, user=request.user, startup=startup).update(version=source)
        return response(turn)
    except (QwenError, RequestLimitExceeded, OperationalError) as exc:
        error = str(exc) if not isinstance(exc, OperationalError) else 'Сервис занят. Повторите вопрос позже.'
        status = 429 if isinstance(exc, RequestLimitExceeded) else 503
        if as_json:
            return JsonResponse({'error': error}, status=status)
        form.add_error(None, error)
        return _lab_page(request, startup, form, status=status)
    finally:
        if token:
            try:
                release_ai_lease(request.user.pk, token)
            except OperationalError:
                pass


def _generate_design(request, startup, source, prompt, kind, scope, rebuild, chosen, context):
    full_rebuild = context.get('full_rebuild', rebuild)
    context = {key:value for key,value in context.items() if key != 'full_rebuild'}
    if kind == LabSiteVersion.Kind.GENERATED:
        from founder.services.generated_backend import generate_backend
        with billing_scope(request.user, startup, 'patch' if source else 'create'):
            return generate_backend(prompt, source=source, context=context, checkpoint=(request.user.pk, startup.pk),
                                    rebuild=full_rebuild)
    options = {"backend": True} if kind == LabSiteVersion.Kind.DJANGO else {}
    if source:
        rebuild = full_rebuild
    if scope != "auto":
        options["edit_scope"] = scope
    if rebuild:
        options["rebuild"] = True
    # The ready scaffold has no design yet. Its first design needs a complete
    # document, rather than a small patch to a placeholder page.
    initial_design = design_pending(source)
    from founder.services.lab_recovery import validation_scope
    with billing_scope(request.user, startup, "patch" if source and not rebuild and not initial_design else "create"), validation_scope(
            lambda candidate: validate_result(prompt, source, candidate, chosen)):
        result = generate_site(prompt, previous_html=source.html if source and not initial_design else "",
            max_tokens=min(settings.LAB_CREATE_MAX_TOKENS, settings.QWEN_CODE_MAX_TOKENS),
            project_context=context, report=True, **options)
    return result


def _create_version(*, report=None, feedback=None, routing=None, **fields):
    version = LabSiteVersion(**fields)
    version.bruno_report = report if report is not None else reviewed_reply(version, feedback) if feedback else version_reply(version)
    if routing:
        version.bruno_report = {**version.bruno_report, 'routing': routing}
    version.save(force_insert=True)
    return version
def _log_version(request, startup, source, version):
    request.lab_result_version = version.pk
    from founder.services.project_history import record_lab_result
    record_lab_result(request, startup, source, version)
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
    if settings.LAB_RAW_AI_TEST_MODE:
        return JsonResponse({'error': 'Готовые настройки отключены на время сырого тестирования. Отправьте изменение модели.'}, status=409)
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
    if settings.LAB_RAW_AI_TEST_MODE:
        return JsonResponse({'error': 'Встроенные инструменты отключены на время сырого тестирования.'}, status=409)
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
    version = get_object_or_404(startup.lab_versions, pk=version_id, kind__in=[LabSiteVersion.Kind.DJANGO, LabSiteVersion.Kind.GENERATED])
    if version.kind == LabSiteVersion.Kind.GENERATED:
        from founder.services.generated_backend import export_backend
        return FileResponse(export_backend(version), as_attachment=True,
            filename=f'site-backend-{str(startup.pk)[:8]}.zip', content_type='application/zip')
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
    version = get_object_or_404(startup.lab_versions, pk=version_id, kind__in=[LabSiteVersion.Kind.DJANGO, LabSiteVersion.Kind.GENERATED])
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
    return (settings.BASE_DIR / 'static/founder/js/lab-tracker.js').read_text(encoding="utf-8")


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
