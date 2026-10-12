from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import CreditAccount
from chat.models import Persona

DEFAULT_STARTING_BALANCE = 20000
DEFAULT_PERSONAS = [
    {"name": "Assistant général", "appearance_name": "2AI", "appearance_avatar": "🐘", "appearance_accent": "#E8A33D", "appearance_theme": "light", "prompt": "Tu es un assistant poli, clair et utile.", "tone": "neutre", "temperature": 0.7, "length": "moyenne"},
    {"name": "Tuteur de langues", "appearance_name": "Tuteur de langues", "appearance_avatar": "📚", "appearance_accent": "#3F8E8A", "appearance_theme": "light", "prompt": "Tu es un tuteur patient qui aide à apprendre une langue étrangère, corrige en douceur et explique la grammaire simplement.", "tone": "pedagogue", "temperature": 0.5, "length": "moyenne"},
    {"name": "Coach code", "appearance_name": "Coach code", "appearance_avatar": "⌘", "appearance_accent": "#4E9F3D", "appearance_theme": "dark", "prompt": "Tu es un mentor en développement logiciel, tu donnes des explications concrètes avec des exemples de code.", "tone": "decontracte", "temperature": 0.4, "length": "longue"},
]


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_credit_account(sender, instance, created, **kwargs):
    """Donne un compte crédit avec un solde de départ à chaque nouvel utilisateur."""
    if created:
        CreditAccount.objects.create(
            user=instance,
            balance=DEFAULT_STARTING_BALANCE,
        )
        for persona_data in DEFAULT_PERSONAS:
            Persona.objects.create(user=instance, **persona_data)
        from chat.persona_context import ensure_big_persona
        ensure_big_persona(instance)
