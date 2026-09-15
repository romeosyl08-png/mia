from django.apps import AppConfig


class AiUserConfig(AppConfig):
    name = 'ai_user'

    def ready(self):
        from . import signals  # noqa: F401