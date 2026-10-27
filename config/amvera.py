"""Production settings for one Amvera web process and persistent storage."""
from .settings import *  # noqa: F403
from urllib.parse import urlsplit

DEBUG = False
if TELEGRAM_MINI_APP_URL:
    public_host = urlsplit(TELEGRAM_MINI_APP_URL).hostname
    if public_host:
        ALLOWED_HOSTS = [*ALLOWED_HOSTS, public_host]
        CSRF_TRUSTED_ORIGINS = [*CSRF_TRUSTED_ORIGINS, TELEGRAM_MINI_APP_URL.rstrip("/")]
DATA_DIR = Path(os.getenv("AMVERA_DATA_DIR", "/data"))
if not os.getenv("DATABASE_URL"):
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
        "OPTIONS": {"timeout": 30},
    }}
MEDIA_ROOT = DATA_DIR / "private_uploads"
MIDDLEWARE = [
    "founder.telegram_middleware.TelegramFrameMiddleware",
    *MIDDLEWARE,
]
MIDDLEWARE.insert(MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,
                  "whitenoise.middleware.WhiteNoiseMiddleware")
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
SESSION_COOKIE_SAMESITE = "None"
CSRF_COOKIE_SAMESITE = "None"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# Generated backend execution currently requires the isolated macOS runtime.
LAB_BACKEND_RUNTIME_ENABLED = False
LAB_UNRESTRICTED_TEST_MODE = False
PAYMENTS_ENABLED = False

GIGACHAT_CA_BUNDLE = str(BASE_DIR / "certs" / "russian_trusted_root_ca_pem.crt")
