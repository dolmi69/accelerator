from django.db import OperationalError
from django.http import HttpResponse
from .security import allowed
from .capabilities import enabled
from .lab_viewer import launcher, ancestors


class ProtectionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def process_view(self, request, view, args, kwargs):
        # Check direct URLs too; hiding a menu item alone does not disable a module.
        name = request.resolver_match.url_name
        module = None
        if name == 'register':
            module = 'registration'
        elif name in {'password_reset', 'password_reset_done', 'password_reset_confirm', 'password_reset_complete'}:
            module = 'password_reset'
        elif name in {'people', 'inbox', 'start_chat', 'chat', 'history'}:
            module = 'chat'
        elif name in {'notifications', 'read_notification'}:
            module = 'notifications'
        if module and not enabled(module):
            return HttpResponse('Модуль не подключён.', status=404)

    def __call__(self, request):
        if request.method == "POST":
            route = request.path
            address = request.META.get("REMOTE_ADDR", "unknown")
            try:
                if (route in {"/login/", "/register/", '/password-reset/'} or route.startswith('/setup/')) and not allowed("auth:" + route + address, 20, 900):
                    return HttpResponse("Слишком много попыток. Попробуйте позже.", status=429)
                if request.user.is_authenticated and not allowed("write:" + str(request.user.pk), 60, 60):
                    return HttpResponse("Слишком много действий. Попробуйте через минуту.", status=429)
                if not request.user.is_authenticated and route != '/payments/webhook/' and not allowed('anonymous-write:'+address,30,3600):
                    return HttpResponse('Слишком много действий. Попробуйте позже.',status=429)
            except OperationalError:
                return HttpResponse("Сервис занят. Повторите через несколько секунд.", status=503)
        response = self.get_response(request)
        if request.path != "/prototype/":
            response["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-src 'self'; "
                "form-action 'self'; base-uri 'none'; object-src 'none'; frame-ancestors " + ancestors()
            )
        if launcher():
            # Exact frame-ancestors replaces DENY only for a locally launched site.
            response.headers.pop('X-Frame-Options', None)
        if request.path.startswith(("/messages/", "/people/", "/login/", "/register/", '/profile/', '/manage/', '/team/', '/orders/', '/bookings/', '/setup/', '/invite/', '/notifications/', '/password', '/app-api/', '/results/')):
            response["Cache-Control"] = "no-store"
        response['Referrer-Policy'] = 'same-origin'
        return response
