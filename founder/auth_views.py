"""Refresh stale authentication forms without weakening CSRF validation."""
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@require_GET
@never_cache
def csrf_token(request):
    # The secret stays in an HttpOnly cookie; only the masked token is returned.
    # This endpoint has no CORS access and never rotates an existing secret.
    return JsonResponse({"csrfToken": get_token(request)})


@never_cache
def csrf_failure(request, reason=""):
    route = getattr(request.resolver_match, "url_name", None)
    if route in {"register", "login"}:
        retry_url = reverse(route)
    else:
        retry_url = request.path if request.path.startswith("/") and not request.path.startswith("//") else "/"
    return render(request, "errors/csrf.html", {"retry_url": retry_url}, status=403)
