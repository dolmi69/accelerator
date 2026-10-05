"""Local-only origin for the temporary HTTPS tunnel; same DB as the website."""
from urllib.parse import urlsplit

from .settings import *  # noqa: F403

DEBUG = False
ALLOWED_HOSTS = [urlsplit(TELEGRAM_MINI_APP_URL).hostname, "127.0.0.1", "localhost"]
CSRF_TRUSTED_ORIGINS = [TELEGRAM_MINI_APP_URL.rstrip("/")]
# This server binds ONLY 127.0.0.1; cloudflared is the trusted TLS terminator.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
TRUSTED_CLIENT_IP_HEADER = "HTTP_CF_CONNECTING_IP"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_SAMESITE = "None"
CSRF_COOKIE_SAMESITE = "None"
CHAT_BUFFERED_RESPONSES = True  # Quick Tunnels do not support SSE.
MIDDLEWARE = ["founder.telegram_middleware.TelegramFrameMiddleware", *MIDDLEWARE]
