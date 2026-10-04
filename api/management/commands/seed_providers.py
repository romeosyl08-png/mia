from django.core.management.base import BaseCommand
from api.models import APIProvider, AIModel


PROVIDERS = [
    {
        "slug": "ollama-local",
        "name": "Mia (Ollama local)",
        "base_url": "http://127.0.0.1:11434",
        "docs_url": "https://ollama.com/blog/openai-compatibility",
        "models": [
            {"model_id": "qwen2.5-coder:1.5b", "name": "Mia — Qwen Coder local", "input_price": 0, "output_price": 0},
        ],
    },
    {
        "slug": "groq",
        "name": "Groq",
        "base_url": "https://api.groq.com/openai",
        "docs_url": "https://console.groq.com/docs",
        "models": [
            {"model_id": "openai/gpt-oss-120b", "name": "GPT-OSS 120B (Groq)", "input_price": 0.15, "output_price": 0.60},
            {"model_id": "openai/gpt-oss-20b", "name": "GPT-OSS 20B (Groq)", "input_price": 0.075, "output_price": 0.30},
            {"model_id": "openai/gpt-oss-safeguard-20b", "name": "GPT-OSS Safeguard 20B (Groq)", "input_price": 0.075, "output_price": 0.30},
            {"model_id": "qwen/qwen3-32b", "name": "Qwen3 32B (Groq)", "input_price": 0.29, "output_price": 0.59},
            {"model_id": "qwen/qwen3.6-27b", "name": "Qwen 3.6 27B (Groq)", "input_price": 0.29, "output_price": 0.59},
            {"model_id": "llama-3.1-8b-instant", "name": "Llama 3.1 8B Instant (Groq)", "input_price": 0.05, "output_price": 0.08},
            {"model_id": "meta-llama/llama-4-scout", "name": "Llama 4 Scout (Groq)", "input_price": 0.11, "output_price": 0.34},
            {"model_id": "moonshotai/kimi-k2-instruct-0905", "name": "Kimi K2 (Groq)", "input_price": 1.00, "output_price": 3.00},
        ],
    },
    {
        "slug": "together",
        "name": "Together AI",
        "base_url": "https://api.together.xyz",
        "docs_url": "https://docs.together.ai",
        "models": [
            {"model_id": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "name": "Llama 3.3 70B (Together)", "input_price": 0.88, "output_price": 0.88},
            {"model_id": "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo", "name": "Llama 3.1 70B (Together)", "input_price": 0.88, "output_price": 0.88},
            {"model_id": "meta-llama/Llama-3.1-8B-Instruct-Turbo", "name": "Llama 3.1 8B (Together)", "input_price": 0.18, "output_price": 0.18},
            {"model_id": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8", "name": "Llama 4 Maverick (Together)", "input_price": 0.27, "output_price": 0.85},
            {"model_id": "Qwen/Qwen2.5-7B-Instruct-Turbo", "name": "Qwen 2.5 7B (Together)", "input_price": 0.30, "output_price": 0.30},
            {"model_id": "Qwen/Qwen2.5-72B-Instruct-Turbo", "name": "Qwen 2.5 72B (Together)", "input_price": 1.20, "output_price": 1.20},
            {"model_id": "Qwen/Qwen3-235B-A22B-Instruct-2507-tput", "name": "Qwen3 235B (Together)", "input_price": 0.20, "output_price": 0.60},
            {"model_id": "mistralai/Mistral-Small-24B-Instruct-2501", "name": "Mistral Small (Together)", "input_price": 0.80, "output_price": 0.80},
            {"model_id": "mistralai/Mixtral-8x7B-Instruct-v0.1", "name": "Mixtral 8x7B (Together)", "input_price": 0.60, "output_price": 0.60},
            {"model_id": "deepseek-ai/DeepSeek-V3", "name": "DeepSeek V3 (Together)", "input_price": 1.25, "output_price": 1.25},
            {"model_id": "moonshotai/Kimi-K2-Instruct-0905", "name": "Kimi K2 (Together)", "input_price": 1.00, "output_price": 3.00},
            {"model_id": "zai-org/GLM-4.6", "name": "GLM-4.6 (Together)", "input_price": 0.60, "output_price": 2.20},
        ],
    },
    {
        "slug": "anthropic",
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "docs_url": "https://docs.anthropic.com",
        "models": [
            {"model_id": "claude-haiku-4-5", "name": "Claude Haiku", "input_price": 1.00, "output_price": 5.00},
            {"model_id": "claude-sonnet-4-6", "name": "Claude Sonnet", "input_price": 3.00, "output_price": 15.00},
            {"model_id": "claude-opus-4-6", "name": "Claude Opus", "input_price": 5.00, "output_price": 25.00},
        ],
    },
    {
        "slug": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com",
        "docs_url": "https://developers.openai.com/api/docs/models",
        "models": [
            {"model_id": "gpt-5.6-luna", "name": "GPT-5.6 Luna", "input_price": 1.00, "output_price": 6.00},
            {"model_id": "gpt-5.6-terra", "name": "GPT-5.6 Terra", "input_price": 2.50, "output_price": 15.00},
            {"model_id": "gpt-5.6-sol", "name": "GPT-5.6 Sol", "input_price": 2.50, "output_price": 15.00},
            {"model_id": "gpt-6-astra", "name": "GPT-6 Astra", "input_price": 10.00, "output_price": 50.00},
        ],
    },
    {
        "slug": "fal",
        "name": "fal.ai",
        "base_url": "https://queue.fal.run",
        "docs_url": "https://fal.ai/docs",
        "models": [],
    },
    {
        "slug": "replicate",
        "name": "Replicate",
        "base_url": "https://api.replicate.com",
        "docs_url": "https://replicate.com/docs/reference/http",
        "models": [],
    },
    {
        "slug": "apiframe",
        "name": "Apiframe",
        "base_url": "https://api.apiframe.ai",
        "docs_url": "https://apiframe.ai/docs",
        "models": [],
    },
]


class Command(BaseCommand):
    help = "Crée ou met à jour les fournisseurs IA, leurs modèles et leurs prix."

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
                    defaults={
                        "name": model_data["name"],
                        "is_active": True,
                        "input_price_per_million": model_data.get("input_price", 0),
                        "output_price_per_million": model_data.get("output_price", 0),
                    },
                )
                model_action = "créé" if model_created else "mis à jour"
                self.stdout.write(f"  Modèle {model.name} {model_action}.")

        self.stdout.write(self.style.SUCCESS("Terminé. Pense à ajouter les clés API via k.set_key(...) dans le shell."))