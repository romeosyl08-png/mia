from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import CreditAccount
from chat.models import Persona

DEFAULT_STARTING_BALANCE = 20000
DEFAULT_PERSONAS = [
    {"name": "Assistant général", "prompt": "Tu es un assistant poli, clair et utile.", "tone": "neutre", "temperature": 0.7, "length": "moyenne"},
    {"name": "Tuteur de langues", "prompt": "Tu es un tuteur patient qui aide à apprendre une langue étrangère, corrige en douceur et explique la grammaire simplement.", "tone": "pedagogue", "temperature": 0.5, "length": "moyenne"},
    {"name": "Coach code", "prompt": "Tu es un mentor en développement logiciel, tu donnes des explications concrètes avec des exemples de code.", "tone": "decontracte", "temperature": 0.4, "length": "longue"},
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