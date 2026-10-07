import os

from django.core.management.base import BaseCommand

from api.models import APIKey, APIProvider

# slug du fournisseur -> variable d'environnement qui contient sa clé
KEY_ENV_VARS = {
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "together": "TOGETHER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "fal": "FAL_API_KEY",
    "replicate": "REPLICATE_API_TOKEN",
    "apiframe": "APIFRAME_API_KEY",
}


class Command(BaseCommand):
    help = "Enregistre, chiffrées, les clés des fournisseurs lues dans les variables d'environnement."

    def handle(self, *args, **options):
        for slug, env_name in KEY_ENV_VARS.items():
            value = os.environ.get(env_name, "").strip()
            if not value:
                self.stdout.write(f"{slug}: {env_name} absente, ignoré.")
                continue
            provider = APIProvider.objects.filter(slug=slug).first()
            if provider is None:
                self.stdout.write(self.style.WARNING(f"{slug}: fournisseur introuvable (seed_providers d'abord)."))
                continue

            key, _ = APIKey.objects.get_or_create(provider=provider, name=f"{slug}-key")
            try:
                unchanged = bool(key.encrypted_key) and key.get_key() == value
            except Exception:  # FERNET_KEY changée : l'ancienne valeur est illisible, on la réécrit
                unchanged = False
            if unchanged:
                self.stdout.write(f"{slug}: déjà à jour.")
                continue

            key.set_key(value)
            key.is_active = True
            key.save()
            self.stdout.write(self.style.SUCCESS(f"{slug}: clé enregistrée."))  # jamais la valeur elle-même