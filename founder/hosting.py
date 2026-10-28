"""BrunoHost releases and guest-origin dispatcher.

This middleware intentionally runs BEFORE sessions/auth/CSRF/URL routing: a user
application must never acquire the platform's cookie, admin or authenticated API.
"""
import logging
import re
import secrets
import string
import subprocess
from uuid import UUID
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from runtime_support import file_lock

from founder.models import HostedSite, LabSiteVersion, StartupProfile
from founder.services.access import get_startup
from founder.services.django_builder import BuilderError
from founder.services.generated_backend import validate_files
from founder.services.lab_bruno import protect_service_secrets
from founder.services.qwen import QwenOutputError
from founder.services.request_limits import consume_limit, RequestLimitExceeded
from founder.services import hosting_runtime

logger = logging.getLogger(__name__)
SLUG = re.compile(r'h[a-z0-9]{13}')
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; form-action 'self'; "
       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; worker-src 'none'")


def base_url():
    value = urlsplit(settings.BRUNOHOST_BASE_URL)
    try:
        port = value.port
    except ValueError:
        return None
    if (not value.hostname or value.scheme not in {'http', 'https'} or value.username or value.password or
            value.path not in {'', '/'} or value.query or value.fragment or
            not re.fullmatch(r'[a-z0-9.-]+', value.hostname) or
            (value.scheme != 'https' and not (settings.DEBUG and value.hostname == 'localhost'))):
        return None
    return value


def site_url(site):
    base = base_url()
    if not base:
        return ''
    port = f':{base.port}' if base.port else ''
    return f'{base.scheme}://{site.slug}.{base.hostname}{port}/'


@contextmanager
def site_lock(site):
    folder = Path(settings.BRUNOHOST_ROOT) / site.slug
    folder.mkdir(parents=True, exist_ok=True)
    with file_lock(folder / 'release.lock'):
        yield


def context(startup, selected):
    site = HostedSite.objects.filter(startup=startup).select_related('version').first()
    supported = selected and selected.kind in {'static', 'generated'}
    reason = ''
    if not base_url():
        reason = 'Для публикации подключите отдельный домен BrunoHost.'
    elif selected and selected.kind == 'django':
        reason = 'Публикация готовых Django-модулей ещё не подключена. Сейчас поддерживаются HTML-сайты и сайты с сервером модели.'
    elif selected and selected.kind == 'generated' and not hosting_runtime.enabled():
        reason = 'Для серверных сайтов ещё не подключён изолированный исполнитель BrunoHost.'
    return {'site': site, 'url': site_url(site) if site else '', 'can_publish': bool(supported and not reason),
            'reason': reason, 'up_to_date': bool(site and site.active and selected and site.version_id == selected.pk)}


@login_required
@require_POST
def manage(request, startup_id):
    startup = get_startup(request, startup_id, edit=True)
    version_id = request.POST.get('version')
    if version_id:
        try:
            version_id = UUID(version_id)
        except (ValueError, AttributeError):
            raise Http404 from None
    version = get_object_or_404(LabSiteVersion, startup=startup, pk=version_id) if version_id else None
    action = request.POST.get('action', 'publish')
    if action not in {'publish', 'stop'} or (action == 'publish' and not version):
        return HttpResponseBadRequest('Неизвестное действие или версия.')
    destination = reverse('lab', args=[startup.pk]) + (f'?version={version.pk}' if version else '')
    try:
        consume_limit(f'hosting-publish:{request.user.pk}', 20, 3600)
        if action == 'publish':
            info = context(startup, version)
            if not info['can_publish']:
                raise BuilderError(info['reason'] or 'Эту версию пока нельзя опубликовать.')
            if version.kind == 'generated':
                validate_files(version.backend_files)
            else:
                protect_service_secrets(version.html)
                if not version.html.strip() or len(version.html.encode()) > 2_000_000:
                    raise BuilderError('В версии отсутствует готовый HTML-сайт.')
        # Serialize releases per startup, including first publication. An explicit
        # startup lock prevents two random addresses from being allocated at once.
        root = Path(settings.BRUNOHOST_ROOT)
        root.mkdir(parents=True, exist_ok=True)
        with file_lock(root / f'publish-{startup.pk}.lock'):
            site = HostedSite.objects.filter(startup=startup).first()
            if not site and action == 'stop':
                return redirect(destination)
            if not site:
                while True:
                    slug = 'h' + ''.join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(13))
                    if not HostedSite.objects.filter(slug=slug).exists():
                        break
                site = HostedSite(startup=startup, slug=slug)
            with site_lock(site):
                previous = HostedSite.objects.filter(pk=site.pk).select_related('version', 'startup').first() if site.pk else None
                try:
                    with transaction.atomic():
                        StartupProfile.objects.select_for_update().get(pk=startup.pk)
                        if action == 'stop':
                            site.active = False
                            hosting_runtime.stop(site)
                        else:
                            site.version = version
                            site.active = True
                            site.published_at = timezone.now()
                            if version.kind == 'generated':
                                hosting_runtime.ensure(site)  # Health check before exposing the release.
                            else:
                                hosting_runtime.stop(site)
                        site.save()
                except Exception:
                    # The previous release stays published if startup/import fails.
                    if previous and previous.active and previous.version and previous.version.kind == 'generated':
                        try:
                            hosting_runtime.ensure(previous)
                        except Exception:
                            logger.exception('Could not restore hosted runtime %s', site.slug)
                    raise
        messages.success(request, 'Сайт снят с публикации. Адрес и данные сохранены.' if action == 'stop' else 'Сайт опубликован. Ссылку можно открыть без аккаунта BrunoLab.')
    except (BuilderError, QwenOutputError, RequestLimitExceeded, TimeoutError, OSError, subprocess.SubprocessError) as error:
        logger.warning('Hosting publication failed for %s: %s', startup.pk, type(error).__name__)
        messages.error(request, str(error) if isinstance(error, (BuilderError, QwenOutputError, RequestLimitExceeded)) else 'Не удалось опубликовать сайт. Повторите попытку позже.')
    return redirect(destination)


def secured(response):
    response['Content-Security-Policy'] = CSP
    response['X-Content-Type-Options'] = 'nosniff'
    response['X-Frame-Options'] = 'DENY'
    response['Referrer-Policy'] = 'no-referrer'
    response['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response['Cache-Control'] = 'no-store'
    return response


def error_response(status, message):
    return secured(HttpResponse(message, status=status, content_type='text/plain; charset=utf-8'))


class HostedSiteMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        base = base_url()
        if not base:
            return self.get_response(request)
        raw = request.META.get('HTTP_HOST', '')
        try:
            incoming = urlsplit('http://' + raw)
            host, port = incoming.hostname, incoming.port
        except ValueError:
            return error_response(400, 'Некорректный адрес.')
        if not host or not host.endswith('.' + base.hostname):
            return self.get_response(request)
        # Catch ALL subdomains of the hosting base, including unknown/malformed
        # ones. Never let them fall through to the platform's normal routes.
        slug = host[:-len(base.hostname) - 1]
        if incoming.username or incoming.password or incoming.path or incoming.query or incoming.fragment or not SLUG.fullmatch(slug):
            return error_response(404, 'Сайт не найден.')
        if (port or (443 if request.is_secure() else 80)) != (base.port or (443 if base.scheme == 'https' else 80)):
            return error_response(404, 'Сайт не найден.')
        if base.scheme == 'https' and not request.is_secure():
            return error_response(400, 'Откройте сайт по HTTPS.')
        try:
            return self.serve(request, slug)
        except RequestLimitExceeded as error:
            response = error_response(429, str(error))
            response['Retry-After'] = error.retry_after
            return response
        except Exception:
            logger.exception('Hosted request failed: %s', slug)
            return error_response(503, 'Сайт временно недоступен. Попробуйте позже.')

    def serve(self, request, slug):
        site = HostedSite.objects.select_related('version', 'startup__owner').filter(
            slug=slug, active=True, version__isnull=False, startup__owner__is_active=True).first()
        if not site or site.version.startup_id != site.startup_id:
            return error_response(404, 'Сайт не опубликован или снят с публикации.')
        if request.path.startswith('/__lab_'):
            return error_response(404, 'Страница не найдена.')
        if request.method not in {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'}:
            return error_response(405, 'Метод не поддерживается.')
        if request.method not in {'GET', 'HEAD', 'OPTIONS'} and request.headers.get('Origin') != site_url(site).rstrip('/'):
            return error_response(403, 'Запрос с другого сайта отклонён.')
        consume_limit(f'host:{slug}:{request.META.get("REMOTE_ADDR", "")}', 180, 60)
        try:
            length = int(request.META.get('CONTENT_LENGTH') or '0')
        except ValueError:
            return error_response(400, 'Некорректный запрос.')
        if not 0 <= length <= 1_000_000:
            return error_response(413, 'Запрос слишком большой.')
        if site.version.kind == 'static':
            if request.method not in {'GET', 'HEAD'}:
                return error_response(405, 'Это статический сайт.')
            if request.path not in {'/', '/index.html'}:
                return error_response(404, 'Страница не найдена.')
            content = site.version.html.encode()
            response = HttpResponse(content if request.method != 'HEAD' else b'', content_type='text/html; charset=utf-8')
            response['Content-Length'] = len(content)
            return secured(response)
        if site.version.kind != 'generated':
            return error_response(503, 'Этот тип приложения пока не поддерживается.')
        with site_lock(site):
            # Recheck after waiting for an in-progress update/unpublication.
            site.refresh_from_db()
            if not site.active or not site.version_id:
                return error_response(404, 'Сайт снят с публикации.')
            if site.version.kind != 'generated':
                return error_response(503, 'Сайт обновляется. Перезагрузите страницу.')
            state = hosting_runtime.ensure(site)
            headers = {'Host': state['host'], 'Origin': 'http://' + state['host'],
                       'Content-Type': request.headers.get('Content-Type', 'application/octet-stream')}
            cookie = '; '.join(item.strip() for item in request.headers.get('Cookie', '').split(';') if item.strip().startswith('lab_'))
            if cookie:
                headers['Cookie'] = cookie
            # Bound this read ourselves: platform form limits are deliberately
            # smaller and must not turn a hosted JSON request into a debug page.
            body = request.read(1_000_001)
            if len(body) > 1_000_000:
                return error_response(413, 'Запрос слишком большой.')
            try:
                with hosting_runtime.client(state) as connection:
                    with connection.stream(request.method, hosting_runtime.target(state) + request.get_full_path(), content=body, headers=headers) as upstream:
                        chunks, size = [], 0
                        for chunk in upstream.iter_bytes():
                            size += len(chunk)
                            if size > 4_000_000:
                                return error_response(502, 'Ответ сайта слишком большой.')
                            chunks.append(chunk)
                        response = HttpResponse(b''.join(chunks), status=upstream.status_code,
                                                content_type=upstream.headers.get('content-type', 'application/octet-stream'))
                        location = upstream.headers.get('location', '')
                        if location.startswith('/') and not location.startswith('//') and '\\' not in location:
                            response['Location'] = location
                        for value in upstream.headers.get_list('set-cookie'):
                            cookies = SimpleCookie()
                            cookies.load(value)
                            for name, morsel in cookies.items():
                                if not name.startswith('lab_') or morsel['domain']:
                                    continue
                                max_age = morsel['max-age']
                                if max_age and not re.fullmatch(r'-?\d{1,10}', max_age):
                                    continue
                                response.set_cookie(name, morsel.value, path='/', httponly=True, samesite='Lax',
                                    secure=request.is_secure(), max_age=int(max_age) if max_age else None,
                                    expires=morsel['expires'] if not max_age else None)
                        return secured(response)
            except httpx.HTTPError:
                hosting_runtime.stop(site)
                return error_response(503, 'Сайт временно недоступен. Попробуйте позже.')
