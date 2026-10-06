"""Bound costly requests before reaching views; preserve streaming responses."""
import logging
import asyncio
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import OperationalError
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.deprecation import MiddlewareMixin

from founder.models import StartupProfile
from founder.services.request_limits import (
    RequestLimitExceeded, acquire_ai_lease, consume_limit, release_ai_lease,
)

logger = logging.getLogger(__name__)


class RequestProtectionMiddleware(MiddlewareMixin):
    @staticmethod
    def error_response(request, message, status, *, retry_after=None):
        # Ordinary forms need a readable page. SSE/fetch clients keep JSON.
        if "text/html" in request.headers.get("Accept", ""):
            back = request.headers.get("Referer", "/")
            if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                back = "/"
            html = render_to_string("errors/request_blocked.html", {
                "error_message": message, "back_url": back,
                "title": "Файл слишком большой" if status == 413 else "Попробуйте чуть позже",
                "retry_after": retry_after,
            })  # No request/context processors: also safe when the DB is busy.
            response = HttpResponse(html, status=status)
        else:
            response = JsonResponse({"error": message}, status=status)
        response["Cache-Control"] = "no-store"
        if retry_after is not None:
            response["Retry-After"] = str(retry_after)
        return response

    def process_request(self, request):
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            return self.error_response(request, "Некорректный размер запроса.", 400)
        if length > settings.MAX_UPLOAD_BYTES + settings.DATA_UPLOAD_MAX_MEMORY_SIZE:
            return self.upload_error(request)

    def upload_error(self, request):
        return self.error_response(request, "Загрузка слишком большая. Максимум 5 МБ, для файлов в чате — 2 МБ.", 413)

    def process_view(self, request, view, args, kwargs):
        if request.method != "POST":
            return None
        if request.content_type == "multipart/form-data":
            # Force parsing when CSRF was supplied in a header. A truncated upload
            # must not silently become a successful form without its attachment.
            request.POST
            if getattr(request, "upload_limit_exceeded", False):
                return self.upload_error(request)
        route = request.resolver_match.url_name
        address = request.META.get("REMOTE_ADDR", "unknown")
        # Only enabled on the loopback-only server behind our Cloudflare tunnel.
        header = getattr(settings, "TRUSTED_CLIENT_IP_HEADER", "")
        if header:
            address = request.META.get(header, address)
        try:
            if route in {"login", "register"}:
                consume_limit(f"auth:{route}:{address}", 30 if route == "login" else 10, 900)
            if not request.user.is_authenticated:
                return None
            consume_limit(f"write:{request.user.pk}", 120, 60)
            ai_request = route in {"chat_send", "metrics_assess", "pitch_finish", "tasks_generate", "card_generate", "lab_generate"}
            if route == "card_edit":
                ai_request = request.POST.get("action") in {"generate", "refine"}
            if ai_request and StartupProfile.objects.filter(pk=kwargs.get("startup_id"), owner=request.user).exists():
                consume_limit(f"ai-minute:{request.user.pk}", settings.AI_REQUESTS_PER_MINUTE, 60)
                consume_limit(f"ai-day:{request.user.pk}", settings.AI_REQUESTS_PER_DAY, 86400)
                if route == "lab_generate":
                    consume_limit(f"lab-day:{request.user.pk}", settings.LAB_REQUESTS_PER_DAY, 86400)
                    consume_limit("lab-global-day", settings.LAB_GLOBAL_REQUESTS_PER_DAY, 86400)
                request.ai_lease = acquire_ai_lease(request.user.pk)
        except RequestLimitExceeded as exc:
            return self.error_response(request, str(exc), 429, retry_after=exc.retry_after)
        except OperationalError:
            # Fail closed on a busy/unavailable limiter instead of spending without bounds.
            return self.error_response(request, "Сервис занят. Повторите запрос через несколько секунд.", 503, retry_after=5)

    def process_response(self, request, response):
        token = getattr(request, "ai_lease", None)
        if token is None:
            return response
        released = False

        def release():
            nonlocal released
            if released:
                return
            try:
                release_ai_lease(request.user.pk, token)
                released = True
            except OperationalError:
                # Lease expiry recovers a DB outage; do not replace a sent answer.
                logger.warning("Could not release AI request lease; waiting for expiry.")

        if response.streaming:
            original = response.streaming_content
            if response.is_async:
                async def events():
                    try:
                        async for chunk in original:
                            yield chunk
                    finally:
                        await sync_to_async(release, thread_sensitive=True)()
            else:
                def events():
                    try:
                        yield from original
                    finally:
                        release()
            response.streaming_content = events()
            # Also release if the connection closes before the iterator starts.
            original_close = response.close
            def close():
                try:
                    if not released:
                        try:
                            loop = asyncio.get_running_loop()
                        except RuntimeError:
                            release()
                        else:
                            # ASGI test clients can close on the event loop; keep
                            # DB work off it, including an unstarted stream.
                            response.lease_cleanup = loop.create_task(sync_to_async(release, thread_sensitive=True)())
                finally:
                    original_close()
            response.close = close
        else:
            release()
        return response
