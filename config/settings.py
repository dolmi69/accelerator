import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DEBUG = os.getenv("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "django-insecure-local-development-only-change-before-deploy"
    else:
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG=0")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "founder",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "founder.security_middleware.RequestProtectionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "founder.context_processors.bruno_pet",
                "founder.context_processors.community",
            ],
        },
    },
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# One process is enough for local development. Redis shares events between
# multiple workers (and the optional second Telegram server).
REDIS_URL = os.getenv("REDIS_URL", "")
CHANNEL_LAYERS = {"default": (
    {"BACKEND": "channels_redis.core.RedisChannelLayer", "CONFIG": {
        # redis-py 8 defaults to 5s, which races Channels' 5s blocking receive.
        "hosts": [{"address": REDIS_URL, "socket_timeout": 15, "socket_connect_timeout": 5}],
        "prefix": "cofounder",
    }}
    if REDIS_URL else {"BACKEND": "channels.layers.InMemoryChannelLayer"}
)}

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=60 if os.getenv("DATABASE_URL") else 0,
    )
}

AUTH_USER_MODEL = "founder.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "ru-ru"
TIME_ZONE = "Europe/Moscow"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_ROOT = BASE_DIR / "private_uploads"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

AI_PROVIDER = os.getenv("AI_PROVIDER", "demo").lower()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
GIGACHAT_MODEL = os.getenv("GIGACHAT_MODEL", "GigaChat-3-Pro")
GIGACHAT_SCOPE = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
GIGACHAT_CA_BUNDLE = os.getenv("GIGACHAT_CA_BUNDLE", "")
CLOUDRU_MODEL = os.getenv("CLOUDRU_MODEL", "ai-sage/GigaChat3-10B-A1.8B")
# Independent from AI_PROVIDER: website generation must not switch Bruno's model.
QWEN_CODE_MODEL = os.getenv("QWEN_CODE_MODEL", "Qwen/Qwen3-Coder-Next")
QWEN_CODE_MAX_TOKENS = int(os.getenv("QWEN_CODE_MAX_TOKENS", "8192"))
QWEN_CODE_TIMEOUT = float(os.getenv("QWEN_CODE_TIMEOUT", "180"))
AI_MAX_OUTPUT_TOKENS = 900
AI_REQUESTS_PER_MINUTE = int(os.getenv("AI_REQUESTS_PER_MINUTE", "12"))
AI_REQUESTS_PER_DAY = int(os.getenv("AI_REQUESTS_PER_DAY", "200"))
DATA_UPLOAD_MAX_MEMORY_SIZE = 256 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 100
DATA_UPLOAD_MAX_NUMBER_FILES = 1
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
FILE_UPLOAD_HANDLERS = [
    "founder.upload_handlers.BoundedUploadHandler",
    "django.core.files.uploadhandler.MemoryFileUploadHandler",
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 3600 if not DEBUG else 0
SECURE_REFERRER_POLICY = "same-origin"
# Enable only behind a proxy that overwrites this header and shields the origin.
if os.getenv("DJANGO_TRUST_PROXY_HTTPS") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = [value.strip() for value in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if value.strip()]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_MINI_APP_URL = os.getenv("TELEGRAM_MINI_APP_URL", "")
CHAT_BUFFERED_RESPONSES = os.getenv("CHAT_BUFFERED_RESPONSES", "0") == "1"
