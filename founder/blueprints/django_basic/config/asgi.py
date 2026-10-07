import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.asgi import get_asgi_application
django_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.conf import settings
from django.urls import path
from core.consumers import ChatConsumer
from core.security import SameOriginWebSocket

application = ProtocolTypeRouter({
    "http": ASGIStaticFilesHandler(django_app) if settings.DEBUG else django_app,
    "websocket": SameOriginWebSocket(AuthMiddlewareStack(URLRouter([
        path("ws/messages/<uuid:conversation_id>/", ChatConsumer.as_asgi()),
    ]))),
})
