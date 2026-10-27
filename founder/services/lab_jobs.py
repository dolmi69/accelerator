"""Persistent lab jobs, executed by a detached, single-claim worker process.

No model call is retried automatically. Queued delivery may be repeated safely;
only the worker winning the database claim can execute a job.
"""
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
from datetime import timedelta
from urllib.parse import urlsplit
from runtime_support import process_is_alive

from django.conf import settings
from django.contrib.messages.storage.fallback import FallbackStorage
from django.db import close_old_connections, OperationalError
from django.db.models import Q
from django.http import HttpRequest, QueryDict
from django.urls import reverse
from django.utils import timezone

from founder.models import LabGenerationJob

logger = logging.getLogger(__name__)
ACTIVE = ('queued', 'running')


class JobFailure(Exception):
    pass


def describe(job):
    if not job:
        return None
    url = reverse('lab', args=[job.startup_id])
    if job.version_id:
        url += f'?version={job.version_id}&job={job.pk}'
    else:
        url += f'?job={job.pk}'
    mode = 'auto' if job.payload.get('automatic') == ['1'] and not job.payload.get('_intent_route') else job.mode
    try:
        model_route = json.loads(job.payload.get('_model_route', ['null'])[0])
    except (ValueError, TypeError, IndexError):
        model_route = None
    return {'id': str(job.pk), 'status': job.status, 'mode': mode,
            'model_route': model_route,
            'prompt': job.payload.get('prompt', [''])[0], 'error': job.error,
            'version': str(job.version_id) if job.version_id else None,
            'url': url}


def refresh(job):
    if job and job.status == 'running' and job.heartbeat_at and job.heartbeat_at < timezone.now() - timedelta(seconds=90):
        if job.pid:
            try:
                if process_is_alive(job.pid):
                    return job  # A sleeping computer must not cancel a live worker.
            except ProcessLookupError:
                pass
            except PermissionError:
                return job
        LabGenerationJob.objects.filter(pk=job.pk, status='running', heartbeat_at=job.heartbeat_at).update(
            status='failed', finished_at=timezone.now(),
            error='Фоновый процесс прервался. Предыдущий сайт сохранён. Автоматического платного повтора нет.')
        job.refresh_from_db()
        if job.status == 'failed' and job.lease_token:
            from founder.services.request_limits import release_ai_lease
            release_ai_lease(job.user_id, job.lease_token)
    return job


def launch(job_id):
    """Trusted worker, not generated code. Never waits for the AI in HTTP."""
    now = timezone.now()
    delivered = LabGenerationJob.objects.filter(pk=job_id, status='queued').filter(
        Q(heartbeat_at__isnull=True) | Q(heartbeat_at__lt=now - timedelta(seconds=10))).update(heartbeat_at=now)
    if not delivered:
        return
    directory = settings.BASE_DIR / '.runtime'
    directory.mkdir(exist_ok=True)
    try:
        with (directory / 'lab-jobs.log').open('ab') as log:
            options = {'start_new_session': True} if os.name != 'nt' else {
                'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
            subprocess.Popen([sys.executable, str(settings.BASE_DIR / 'manage.py'), 'run_lab_job', str(job_id)],
                cwd=settings.BASE_DIR, stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
    except OSError:
        # A subsequent status request can redeliver this still-unclaimed job.
        logger.exception('Could not deliver lab job %s', job_id)


def heartbeat(job_id, stopped):
    while not stopped.wait(5):
        close_old_connections()
        try:
            LabGenerationJob.objects.filter(pk=job_id, status='running').update(heartbeat_at=timezone.now())
        except OperationalError:
            logger.warning('Lab job heartbeat delayed: %s', job_id)
        finally:
            close_old_connections()


def run(job_id):
    now = timezone.now()
    claimed = LabGenerationJob.objects.filter(pk=job_id, status='queued').update(
        status='running', pid=os.getpid(), started_at=now, heartbeat_at=now)
    if not claimed:
        return
    job = LabGenerationJob.objects.select_related('user', 'startup').get(pk=job_id)
    stopped = threading.Event()
    monitor = threading.Thread(target=heartbeat, args=(job.pk, stopped), daemon=True)
    monitor.start()
    request = None
    try:
        from founder.lab_views import lab_generate
        # Reuse the validated generation pipeline, independent of any live HTTP
        # connection. Session/messages are private to this worker request.
        request = HttpRequest()
        request.method = 'POST'
        request.path = reverse('lab_generate', args=[job.startup_id])
        request.user = job.user
        if not request.user.is_active:
            raise JobFailure('Аккаунт больше не активен. Запрос остановлен.')
        origin = urlsplit(job.origin)
        request.META.update(HTTP_HOST=origin.netloc, SERVER_NAME=origin.hostname,
            SERVER_PORT=str(origin.port or (443 if origin.scheme == 'https' else 80)),
            HTTP_ACCEPT='application/json', **({'HTTPS': 'on'} if origin.scheme == 'https' else {}))
        request._get_scheme = lambda: origin.scheme
        post = QueryDict(mutable=True)
        for key, values in job.payload.items():
            post.setlist(key, values)
        post['request_id'] = str(job.pk)
        request.POST = post
        request.session = {}
        request._messages = FallbackStorage(request)
        request.lab_background_job = True
        request.lab_job_id = job.pk
        response = lab_generate(request, job.startup_id)
        if response.status_code >= 400:
            try:
                error = json.loads(response.content).get('error')
            except (ValueError, AttributeError):
                error = None
            raise JobFailure(error or 'Не удалось завершить запрос. Предыдущий сайт сохранён.')
        turn_id = None
        if response.get('Content-Type', '').startswith('application/json'):
            turn_id = json.loads(response.content).get('id')
        LabGenerationJob.objects.filter(pk=job.pk, status='running').update(
            status='succeeded', version_id=getattr(request, 'lab_result_version', None),
            turn_id=turn_id, finished_at=timezone.now(), heartbeat_at=timezone.now())
    except Exception as exc:
        if not isinstance(exc, JobFailure):
            logger.exception('Lab job failed: %s', job.pk)
        error = str(exc) if isinstance(exc, JobFailure) else 'Не удалось завершить запрос. Предыдущий сайт сохранён. Автоматического платного повтора нет.'
        LabGenerationJob.objects.filter(pk=job.pk, status='running').update(
            status='failed', error=error[:2000], finished_at=timezone.now(), heartbeat_at=timezone.now())
    finally:
        stopped.set()
        monitor.join(timeout=6)
        if request is not None and getattr(request, 'ai_lease', None):
            from founder.services.request_limits import release_ai_lease
            release_ai_lease(job.user_id, request.ai_lease)
        close_old_connections()


def remember_lease(request, token):
    job_id = getattr(request, 'lab_job_id', None)
    if job_id:
        LabGenerationJob.objects.filter(pk=job_id, status='running').update(lease_token=token)


def remember_model_route(request, route):
    """Persist the server's choice before a paid call, for reload-safe status."""
    job_id = getattr(request, 'lab_job_id', None)
    if job_id and route:
        job = LabGenerationJob.objects.filter(pk=job_id, status='running').first()
        if job:
            LabGenerationJob.objects.filter(pk=job_id, status='running').update(
                payload={**job.payload, '_model_route': [json.dumps(route, ensure_ascii=False)]})
