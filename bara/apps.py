from django.apps import AppConfig


class BaraConfig(AppConfig):
    name = 'bara'

    def ready(self):
        from . import signals  # noqa: F401
