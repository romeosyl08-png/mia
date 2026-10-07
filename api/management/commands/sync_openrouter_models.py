import json
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.core.management.base import BaseCommand, CommandError

from api.constants import OPENROUTER_RANKING_OPTIONS
from api.models import AIModel, APIKey, APIProvider


OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_RANKINGS_URL = "https://openrouter.ai/api/v1/datasets/rankings-daily"
PRICE_SCALE = Decimal("1000000")


class Command(BaseCommand):
    help = "Synchronise les modèles texte et leurs tarifs depuis OpenRouter."

    def handle(self, *args, **options):
        provider = APIProvider.objects.filter(slug="openrouter", is_active=True).first()
        if provider is None:
            raise CommandError("OpenRouter est absent ou inactif. Lance seed_providers d'abord.")

        headers = {"Accept": "application/json"}
        api_key = APIKey.objects.filter(provider=provider, is_active=True).order_by("id").first()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key.get_key()}"

        request = Request(OPENROUTER_MODELS_URL, headers=headers)
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise CommandError(f"OpenRouter a renvoyé une erreur ({error.code}).") from error
        except (OSError, URLError, ValueError) as error:
            raise CommandError("Impossible de récupérer le catalogue OpenRouter.") from error

        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or not rows:
            raise CommandError("La réponse OpenRouter ne contient aucun modèle.")

        synced_ids = set()
        skipped_unpriced = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            model_id = row.get("id")
            architecture = row.get("architecture") or {}
            inputs = architecture.get("input_modalities", ["text"]) if isinstance(architecture, dict) else []
            outputs = architecture.get("output_modalities", ["text"]) if isinstance(architecture, dict) else []
            if (
                not isinstance(model_id, str)
                or not model_id
                or len(model_id) > 150
                or not isinstance(inputs, list)
                or not isinstance(outputs, list)
                or "text" not in inputs
                or "text" not in outputs
            ):
                continue

            pricing = row.get("pricing") or {}
            if not isinstance(pricing, dict):
                raise CommandError(f"Tarif invalide pour le modèle {model_id}.")
            try:
                input_price = Decimal(str(pricing.get("prompt", "0"))) * PRICE_SCALE
                output_price = Decimal(str(pricing.get("completion", "0"))) * PRICE_SCALE
            except (InvalidOperation, TypeError, ValueError) as error:
                raise CommandError(f"Tarif invalide pour le modèle {model_id}.") from error
            if not input_price.is_finite() or not output_price.is_finite():
                raise CommandError(f"Tarif invalide pour le modèle {model_id}.")
            if min(input_price, output_price) < 0:
                skipped_unpriced += 1
                continue

            AIModel.objects.update_or_create(
                provider=provider,
                model_id=model_id,
                defaults={
                    "name": str(row.get("name") or model_id)[:100],
                    "is_active": True,
                    "input_price_per_million": input_price,
                    "output_price_per_million": output_price,
                },
            )
            synced_ids.add(model_id)

        if not synced_ids:
            raise CommandError("Aucun modèle texte valide n'a été trouvé; le catalogue existant est conservé.")

        AIModel.objects.filter(provider=provider).exclude(model_id__in=synced_ids).update(is_active=False)
        rankings_by_category = self._fetch_rankings(headers) if api_key else {}
        if not api_key:
            self.stderr.write(self.style.WARNING(
                "Aucune clé OpenRouter active : les classements d'usage ne sont pas synchronisés."
            ))
        elif not rankings_by_category:
            self.stderr.write(self.style.WARNING(
                "Aucun classement OpenRouter n'a pu être synchronisé; les classements enregistrés sont conservés."
            ))
        if rankings_by_category:
            for model in AIModel.objects.filter(provider=provider, is_active=True).only(
                "id", "model_id", "openrouter_rankings"
            ):
                rankings = dict(model.openrouter_rankings or {})
                for category, category_rankings in rankings_by_category.items():
                    ranked_model = category_rankings.get(model.model_id)
                    if ranked_model is None:
                        rankings.pop(category, None)
                    else:
                        rankings[category] = ranked_model
                model.openrouter_rankings = rankings
                model.save(update_fields=["openrouter_rankings"])

        message = f"{len(synced_ids)} modèles texte OpenRouter synchronisés."
        if rankings_by_category:
            message += f" Classements d'usage synchronisés : {len(rankings_by_category)}."
        if skipped_unpriced:
            message += f" {skipped_unpriced} modèles ignorés faute de tarif publié."
        self.stdout.write(self.style.SUCCESS(message))

    def _fetch_rankings(self, headers):
        rankings_by_category = {}
        for category, _label in OPENROUTER_RANKING_OPTIONS:
            params = {"period": "week"}
            if category == "all":
                params["modality"] = "text"
            else:
                params["category"] = category
            request = Request(
                f"{OPENROUTER_RANKINGS_URL}?{urlencode(params)}",
                headers=headers,
            )
            try:
                with urlopen(request, timeout=30) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise ValueError("La réponse ne contient pas de liste de classements.")
                metadata = payload.get("meta")
                as_of = metadata.get("as_of") if isinstance(metadata, dict) else None
                if not isinstance(as_of, str) or not as_of:
                    raise ValueError("La réponse ne contient pas la date de référence du classement.")
                rows = []
                for row in payload["data"]:
                    if (
                        not isinstance(row, dict)
                        or not isinstance(row.get("date"), str)
                        or not isinstance(row.get("model_permaslug"), str)
                    ):
                        raise ValueError("Une entrée du classement est invalide.")
                    try:
                        total_tokens = int(row.get("total_tokens"))
                    except (TypeError, ValueError) as error:
                        raise ValueError("Le volume de tokens est invalide.") from error
                    if total_tokens < 0:
                        raise ValueError("Le volume de tokens est négatif.")
                    rows.append((row["date"], row["model_permaslug"], total_tokens))
                dates = [row[0] for row in rows]
                latest_date = max(dates) if dates else None
                category_rankings = {}
                for row_date, model_id, total_tokens in rows:
                    if row_date != latest_date:
                        continue
                    if model_id == "other":
                        continue
                    category_rankings[model_id] = {
                        "total_tokens": total_tokens,
                        "as_of": as_of,
                    }
                rankings_by_category[category] = category_rankings
            except HTTPError as error:
                self.stderr.write(self.style.WARNING(
                    f"Classement OpenRouter « {category} » indisponible (HTTP {error.code}); les classements déjà enregistrés sont conservés."
                ))
                break
            except (OSError, ValueError, TypeError) as error:
                self.stderr.write(self.style.WARNING(
                    f"Classement OpenRouter « {category} » invalide ou inaccessible ({error}); les classements déjà enregistrés sont conservés."
                ))
                break
        return rankings_by_category
