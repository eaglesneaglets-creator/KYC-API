from django.apps import AppConfig


class VerificationConfig(AppConfig):
    name = 'verification'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        from . import schema  # noqa: F401  (registers the API-docs auth scheme)
