from django.core.management.base import BaseCommand
from api.models import APIProvider, AIModel


PROVIDERS = [
    {
        "slug": "ollama-local",
        "name": "Mia (Ollama local)",
        "base_url": "http://127.0.0.1:11434",
        "docs_url": "https://ollama.com/blog/openai-compatibility",
        "models": [
            {"model_id": "qwen2.5-coder:1.5b", "name": "Mia — Qwen Coder local"},
        ],
    },
    {
        "slug": "groq",
        "name": "Groq",
        "base_url": "https://api.groq.com/openai",
        "docs_url": "https://console.groq.com/docs",
        "models": [
            {"model_id": "openai/gpt-oss-120b", "name": "GPT-OSS 120B (Groq)"},
            {"model_id": "openai/gpt-oss-20b", "name": "GPT-OSS 20B (Groq)"},
            {"model_id": "openai/gpt-oss-safeguard-20b", "name": "GPT-OSS Safeguard 20B (Groq)"},
            {"model_id": "qwen/qwen3-32b", "name": "Qwen3 32B (Groq)"},
            {"model_id": "qwen/qwen3.6-27b", "name": "Qwen 3.6 27B (Groq)"},
            {"model_id": "llama-3.1-8b-instant", "name": "Llama 3.1 8B Instant (Groq)"},
            {"model_id": "meta-llama/llama-4-scout", "name": "Llama 4 Scout (Groq)"},
            {"model_id": "moonshotai/kimi-k2-instruct-0905", "name": "Kimi K2 (Groq)"},
        ],
    },
    {
        "slug": "together",
        "name": "Together AI",
        "base_url": "https://api.together.xyz",
        "docs_url": "https://docs.together.ai",
        "models": [
            {"model_id": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "name": "Llama 3.3 70B (Together)"},
            {"model_id": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "name": "Llama 3.1 70B (Together)"},
            {"model_id": "meta-llama/Llama-3.1-8B-Instruct-Turbo", "name": "Llama 3.1 8B (Together)"},
            {"model_id": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8", "name": "Llama 4 Maverick (Together)"},
            {"model_id": "Qwen/Qwen2.5-7B-Instruct-Turbo", "name": "Qwen 2.5 7B (Together)"},
            {"model_id": "Qwen/Qwen2.5-72B-Instruct-Turbo", "name": "Qwen 2.5 72B (Together)"},
            {"model_id": "Qwen/Qwen3-235B-A22B-Instruct-2507-tput", "name": "Qwen3 235B (Together)"},
            {"model_id": "mistralai/Mistral-Small-24B-Instruct-2501", "name": "Mistral Small (Together)"},
            {"model_id": "mistralai/Mixtral-8x7B-Instruct-v0.1", "name": "Mixtral 8x7B (Together)"},
            {"model_id": "deepseek-ai/DeepSeek-V3", "name": "DeepSeek V3 (Together)"},
            {"model_id": "moonshotai/Kimi-K2-Instruct-0905", "name": "Kimi K2 (Together)"},
            {"model_id": "zai-org/GLM-4.6", "name": "GLM-4.6 (Together)"},
        ],
    },
    {
        "slug": "anthropic",
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "docs_url": "https://docs.anthropic.com",
        "models": [
            {"model_id": "claude-haiku-4-5", "name": "Claude Haiku"},
            {"model_id": "claude-sonnet-4-6", "name": "Claude Sonnet"},
            {"model_id": "claude-opus-4-6", "name": "Claude Opus"},
        ],
    },
    {
        "slug": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com",
        "docs_url": "https://developers.openai.com/api/docs/models",
        "models": [
            {"model_id": "gpt-5.6-luna", "name": "GPT-5.6 Luna"},
            {"model_id": "gpt-5.6-terra", "name": "GPT-5.6 Terra"},
            {"model_id": "gpt-5.6-sol", "name": "GPT-5.6 Sol"},
            {"model_id": "gpt-6-astra", "name": "GPT-6 Astra"},
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