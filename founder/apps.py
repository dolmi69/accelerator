from django.apps import AppConfig


class FounderConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "founder"

    def ready(self):
        from founder import signals  # noqa: F401
