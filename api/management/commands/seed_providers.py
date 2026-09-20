from django.core.management.base import BaseCommand
from chat.models import APIProvider, AIModel


PROVIDERS = [
    {
        "slug": "groq",
        "name": "Groq",
        "base_url": "https://api.groq.com/openai",
        "docs_url": "https://console.groq.com/docs",
        "models": [
            {"model_id": "openai/gpt-oss-120b", "name": "GPT-OSS 120B (Groq)"},
            {"model_id": "qwen/qwen3.6-27b", "name": "Qwen 3.6 27B (Groq)"},
        ],
    },
    {
        "slug": "together",
        "name": "Together AI",
        "base_url": "https://api.together.xyz",
        "docs_url": "https://docs.together.ai",
        "models": [
            {"model_id": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "name": "Llama 3.3 70B (Together)"},
            {"model_id": "Qwen/Qwen2.5-7B-Instruct-Turbo", "name": "Qwen 2.5 7B (Together)"},
            {"model_id": "mistralai/Mistral-Small-24B-Instruct-2501", "name": "Mistral Small (Together)"},
        ],
    },
    {
        "slug": "anthropic",
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "docs_url": "https://docs.anthropic.com",
        "models": [
            {"model_id": "claude-sonnet-4-6", "name": "Claude Sonnet"},
            {"model_id": "claude-haiku-4-5", "name": "Claude Haiku"},
        ],
    },
]


class Command(BaseCommand):
    help = "Crée ou met à jour les fournisseurs IA et leurs modèles."

    def handle(self, *args, **options):
        for entry in PROVIDERS:
            provider, created = APIProvider.objects.update_or_create(
                slug=entry["slug"],
                defaults={
                    "name": entry["name"],
                    "base_url": entry["base_url"],
                    "docs_url": entry["docs_url"],
                    "is_active": True,
                },
            )
            action = "créé" if created else "mis à jour"
            self.stdout.write(f"Fournisseur {provider.name} {action}.")

            for model_data in entry["models"]:
                model, model_created = AIModel.objects.update_or_create(
                    provider=provider,
                    model_id=model_data["model_id"],
                    defaults={"name": model_data["name"], "is_active": True},
                )
                model_action = "créé" if model_created else "mis à jour"
                self.stdout.write(f"  Modèle {model.name} {model_action}.")

        self.stdout.write(self.style.SUCCESS("Terminé. Pense à ajouter les clés API via k.set_key(...) dans le shell."))