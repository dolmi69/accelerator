"""Short HTTP requests: enqueue and observe, never execute the model here."""
from uuid import UUID
import json

from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.cache import never_cache

from founder.forms import LabPromptForm
from founder.lab_views import _owned_startup, _version
from founder.models import LabGenerationJob
from founder.services.lab_conversation import route_message
from founder.services.lab_jobs import ACTIVE, describe, launch, refresh

FIELDS = {'automatic', 'prompt', 'source_version', 'kind', 'message_mode', 'edit_scope', 'rebuild', 'start_new', 'modules_selected', 'modules', 'task_context', 'feature_context'}


@login_required
@require_POST
@never_cache
def lab_job_start(request, startup_id):
    startup = _owned_startup(request, startup_id)
    try:
        job_id = UUID(request.POST.get('request_id', ''))
    except (ValueError, TypeError, AttributeError):
        return JsonResponse({'error': 'Некорректный идентификатор запроса.'}, status=400)
    payload = {key: request.POST.getlist(key) for key in FIELDS if key in request.POST}
    existing = LabGenerationJob.objects.filter(pk=job_id, startup=startup, user=request.user).first()
    if existing:
        original = json.loads(existing.payload['_submitted'][0]) if '_submitted' in existing.payload else {key:value for key,value in existing.payload.items() if key in FIELDS}
        if original != payload:
            return JsonResponse({'error': 'Этот идентификатор уже использован для другого сообщения.'}, status=409)
        if existing.status == 'queued':
            launch(existing.pk)
        return JsonResponse({'job': describe(refresh(existing))})
    source = _version(startup, request.POST['source_version']) if request.POST.get('source_version') else None
    form = LabPromptForm(request.POST, source=source)
    if not form.is_valid():
        return JsonResponse({'error': ' '.join(str(error) for errors in form.errors.values() for error in errors)}, status=400)
    mode = 'edit' if request.POST.get('automatic') == '1' else route_message(form.cleaned_data['prompt'], form.cleaned_data.get('message_mode') or 'auto', has_site=bool(source))
    try:
        with transaction.atomic():
            from founder.services.lab_task_history import bind_context
            submitted = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            bind_context(request, startup, payload, mode)
            payload['_submitted'] = [submitted]
            job = LabGenerationJob.objects.create(id=job_id, startup=startup, user=request.user, source=source,
                mode=mode, payload=payload, origin=request.build_absolute_uri('/').rstrip('/'))
    except IntegrityError:
        # Concurrent tabs may submit the same job or another request. Never bill twice.
        existing = LabGenerationJob.objects.filter(pk=job_id, startup=startup, user=request.user).first()
        if existing:
            return JsonResponse({'job': describe(refresh(existing))})
        active = LabGenerationJob.objects.filter(user=request.user, status__in=ACTIVE).first()
        if active:
            refresh(active)
        return JsonResponse({'error': 'Бруно уже выполняет запрос. Он продолжится в фоне; дождитесь результата.'}, status=409)
    launch(job.pk)
    return JsonResponse({'job': describe(job)}, status=202)


@login_required
@require_GET
@never_cache
def lab_job_status(request, startup_id):
    startup = _owned_startup(request, startup_id, edit=False)
    jobs = LabGenerationJob.objects.filter(startup=startup)
    job = jobs.filter(status__in=ACTIVE).first() or jobs.first()
    job = refresh(job)
    if job and job.status == 'queued':
        launch(job.pk)
    return JsonResponse({'job': describe(job)})
