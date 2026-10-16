from django.apps import AppConfig


class EvaluationsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'evaluations'
    verbose_name = 'Comparaisons et évaluations'

    def ready(self):
        from . import checks  # noqa: F401
