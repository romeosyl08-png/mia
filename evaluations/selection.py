"""Text-only eligibility and a budget-constrained epsilon/softmax pair sampler."""
import math
import random
from collections import Counter

from django.conf import settings

from chat.billing import credits_for_usage
from chat.routing import eligible_models
from chat.services import estimate_cost

from .models import ModelPerformance


def available_models():
    allowed = set(settings.COMPARISON_PROVIDER_ALLOWLIST)
    denied = set(settings.COMPARISON_MODEL_DENYLIST)
    model_allowlist = set(settings.COMPARISON_MODEL_ALLOWLIST)
    return [model for model in eligible_models(surface='chat')
            if model.provider.slug in allowed and model.model_id not in denied
            and (not model_allowlist or model.model_id in model_allowlist)
            and (not model.input_modalities or 'text' in model.input_modalities)
            and not any((model.openrouter_rankings or {}).get(key) is True for key in ('comparison_disabled',))]


def quote(prompt, model, max_tokens):
    estimated, tokens = estimate_cost(prompt, model, max_output_tokens=max_tokens)
    # The shared estimator is an average (4 characters/token). Reserve a
    # byte-based upper envelope plus protocol overhead for multilingual text.
    tokens = max(tokens, len(prompt.encode('utf-8')) + 512)
    return max(estimated, credits_for_usage(model, tokens, max_tokens)), tokens


def origin(model):
    # OpenRouter is a gateway: diversify the underlying publishers as well.
    return model.model_id.split('/', 1)[0] if model.provider.slug == 'openrouter' else model.provider.slug


def select_pair(prompt, category, max_tokens, budget, models=None, rng=None):
    models = list(models if models is not None else available_models())
    quotes = {m.pk: quote(prompt, m, max_tokens)[0] for m in models}
    # Bounded balanced shortlist for large catalogs. Its exact universe is logged.
    if len(models) > settings.COMPARISON_MAX_CANDIDATES:
        groups = {}
        for model in sorted(models, key=lambda m: (quotes[m.pk], m.pk)):
            groups.setdefault(origin(model), []).append(model)
        shortlist = []
        while len(shortlist) < settings.COMPARISON_MAX_CANDIDATES and any(groups.values()):
            for publisher in sorted(groups):
                if groups[publisher] and len(shortlist) < settings.COMPARISON_MAX_CANDIDATES:
                    shortlist.append(groups[publisher].pop(0))
        models = shortlist
    pairs = [(a, b) for index, a in enumerate(models) for b in models[index + 1:]
             if a.model_id != b.model_id and quotes[a.pk] + quotes[b.pk] <= budget]
    if not pairs:
        raise ValueError('Deux modèles distincts compatibles avec ce budget sont nécessaires.')
    history = {p.model_id: p for p in ModelPerformance.objects.filter(category=category, model__in=models)}
    publishers = Counter(origin(m) for m in models)
    minimum = min(quotes[a.pk] + quotes[b.pk] for a, b in pairs)
    weights = []
    for a, b in pairs:
        quality = sum(history[m.pk].estimated_quality if m.pk in history else .5 for m in (a, b)) / 2
        count = sum(history[m.pk].total_comparisons if m.pk in history else 0 for m in (a, b))
        economical = minimum / max(1, quotes[a.pk] + quotes[b.pk])
        diversity = 1.0 if origin(a) != origin(b) else .35
        representation = math.sqrt(publishers[origin(a)] * publishers[origin(b)])
        weights.append(math.exp(2 * quality + economical + .5 / math.sqrt(1 + count)) * diversity / representation)
    epsilon = min(1., max(.05, settings.COMPARISON_EXPLORATION_RATE))
    total = sum(weights)
    probabilities = [epsilon / len(pairs) + (1 - epsilon) * w / total for w in weights]
    rng = rng or random.SystemRandom()
    index = rng.choices(range(len(pairs)), weights=probabilities, k=1)[0]
    pair = list(pairs[index])
    rng.shuffle(pair)  # Equal exposure of left/right position, independent of model strength.
    return pair, {
        'strategy': 'epsilon_softmax_pair_v1', 'epsilon': epsilon,
        'pair_probability': probabilities[index], 'ordered_probability': probabilities[index] / 2,
        'feasible_pair_count': len(pairs), 'candidate_ids': [m.pk for m in models],
        'probabilities': probabilities, 'pair_ids': [[a.pk, b.pk] for a, b in pairs],
    }
