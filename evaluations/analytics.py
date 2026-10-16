"""Bounded, regularized Bradley–Terry observations, stratified before use by Auto.

No prompts or conversation history enter this service. This is observational
evidence, not a causal or representative benchmark of the whole population.
"""
import math
from collections import Counter, defaultdict
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F, Prefetch
from django.utils import timezone

from .models import CATEGORIES, ComparisonResponse, EvaluationBudget, EvaluationVote, ModelPerformance


def sigmoid(value):
    return 1 / (1 + math.exp(-max(-20, min(20, value))))


def fit(votes):
    inverse_propensities = {}
    user_mass = Counter()
    for vote in votes:
        correction = 1.
        if vote.comparison.selection_mode == 'automatic':
            if hasattr(vote, 'selection_probability'):
                probability, population = vote.selection_probability, vote.selection_pair_count
            else:
                meta = vote.comparison.selection_metadata
                probability, population = meta.get('pair_probability', 0), meta.get('feasible_pair_count', 0)
            if isinstance(probability, (int, float)) and probability > 0 and isinstance(population, (int, float)) and population > 0:
                correction = min(5., 1 / (population * probability))
        inverse_propensities[vote.pk] = correction
        user_mass[vote.user_id] += correction
    rows, data, graph = [], {}, defaultdict(set)
    for vote in votes:
        responses = list(vote.comparison.responses.all())
        if len(responses) != 2 or any(r.status != 'completed' for r in responses):
            continue
        responses.sort(key=lambda r: r.slot)
        a, b = responses
        # Normalize after propensity correction, so even rare selections cannot
        # lift a user's total influence above five effective votes per cohort.
        weight = inverse_propensities[vote.pk] * min(1., 5 / user_mass[vote.user_id])
        target = 1. if vote.preferred_response == 'A' else 0. if vote.preferred_response == 'B' else .5
        rows.append((a.model_id, b.model_id, target, weight))
        graph[a.model_id].add(b.model_id)
        graph[b.model_id].add(a.model_id)
        for response, winner in ((a, 'A'), (b, 'B')):
            d = data.setdefault(response.model_id, dict(count=0, wins=0, losses=0, ties=0, bad=0,
                users=set(), weight=0., weighted_wins=0., weighted_bad=0., latency=0., cost=0., credit_cost=0.))
            win = vote.preferred_response == winner
            tie = vote.preferred_response == 'tie'
            bad = vote.preferred_response == 'both_bad'
            d['count'] += 1
            d['wins'] += int(win)
            d['losses'] += int(not win and not tie)
            d['ties'] += int(tie)
            d['bad'] += int(bad)
            d['users'].add(vote.user_id)
            d['weight'] += weight
            d['weighted_wins'] += weight * (1 if win else .5 if tie else 0)
            d['weighted_bad'] += weight * int(bad)
            d['latency'] += response.latency_ms
            d['cost'] += float(response.provider_cost)
            d['credit_cost'] += response.credits_charged
    skills = {pk: 0. for pk in data}
    # L2 prior of one pseudo-observation per model prevents infinite all-win scores.
    for _ in range(100):
        gradients = {pk: -skill for pk, skill in skills.items()}
        curvature = {pk: 1. for pk in skills}
        for a, b, target, weight in rows:
            p = sigmoid(skills[a] - skills[b])
            delta = weight * (target - p)
            gradients[a] += delta
            gradients[b] -= delta
            curvature[a] += weight * .25
            curvature[b] += weight * .25
        for pk in skills:
            skills[pk] += .3 * gradients[pk] / curvature[pk]
    results = {}
    for pk, d in data.items():
        count = d['count']
        effective = min(d['weight'], len(d['users']))  # User-clustered conservative effective sample size.
        p = d['weighted_wins'] / max(1e-9, d['weight'])
        z = 1.96
        interval = z * math.sqrt(p * (1 - p) / max(1., effective) + z * z / (4 * max(1., effective) ** 2))
        center = p + z * z / (2 * max(1., effective))
        divisor = 1 + z * z / max(1., effective)
        component, pending = {pk}, [pk]
        while pending:
            for other in graph[pending.pop()]:
                if other not in component:
                    component.add(other)
                    pending.append(other)
        bad_rate = d['weighted_bad'] / max(1e-9, d['weight'])
        results[pk] = dict(total_comparisons=count, wins=d['wins'], losses=d['losses'], ties=d['ties'],
            win_rate=d['wins'] / count, tie_rate=d['ties'] / count, both_bad_rate=bad_rate,
            estimated_quality=sigmoid(skills[pk]) * (1 - bad_rate), bt_log_strength=skills[pk],
            average_latency=d['latency'] / count, average_cost=d['cost'] / count,
            average_credits=d['credit_cost'] / count, unique_users=len(d['users']),
            effective_samples=effective, confidence=effective / (effective + 20),
            win_rate_interval=[max(0., (center - interval) / divisor), min(1., (center + interval) / divisor)],
            connected_models=sorted(component))
    return results


def analyze(category):
    votes = list(EvaluationVote.objects.filter(comparison__category=category,
        comparison__analysis_consent=True, comparison__status='completed', suspicious_reasons=[])
        .select_related('comparison', 'reward')
        .annotate(selection_probability=F('comparison__selection_metadata__pair_probability'),
                  selection_pair_count=F('comparison__selection_metadata__feasible_pair_count'))
        .defer('justification', 'justification_hash', 'comparison__prompt', 'comparison__request_digest',
               'comparison__selection_metadata', 'reward__review_note')
        .prefetch_related(Prefetch('comparison__responses', queryset=ComparisonResponse.objects.defer('response_text', 'error_message').order_by('slot')))
        .order_by('-created_at')[:settings.EVALUATION_ANALYSIS_MAX_VOTES])
    cohorts = {'all': votes}
    for mode in ('manual', 'automatic'):
        for paid in (False, True):
            key = f'{mode}_{"paid" if paid else "unpaid"}'
            cohorts[key] = [v for v in votes if v.comparison.selection_mode == mode
                           and (hasattr(v, 'reward') and v.reward.status in {'approved', 'credited'}) == paid]
    # For production routing exclude even pending/rejected grants: reward eligibility
    # itself can change who participates, regardless of eventual payout.
    cohorts['routing'] = [v for v in votes if v.comparison.selection_mode == 'automatic' and not hasattr(v, 'reward')]
    return {name: fit(rows) for name, rows in cohorts.items()}


@transaction.atomic
def rebuild():
    # Serialize snapshot publication with consent withdrawal and budget mutations.
    EvaluationBudget.objects.select_for_update().get(pk=1)
    ModelPerformance.objects.all().delete()
    for category, _ in CATEGORIES:
        cohorts = analyze(category)
        for pk, row in cohorts['all'].items():
            ModelPerformance.objects.create(model_id=pk, category=category,
                **{key: row[key] for key in ('total_comparisons', 'wins', 'losses', 'ties', 'estimated_quality', 'average_latency', 'average_cost')},
                statistics={cohort: stats[pk] for cohort, stats in cohorts.items() if pk in stats})


def apply_to_decision(decision, candidates, category, costs):
    if not settings.EVALUATION_ROUTING_ENABLED:
        return decision
    baseline = next((m for m in candidates if m.model_id == decision['model_id'] and m.provider.slug == decision['provider_slug']), None)
    if baseline is None:
        return decision
    rows = ModelPerformance.objects.filter(model__in=candidates, category=category,
        updated_at__gte=timezone.now() - timedelta(hours=48))
    supported = []
    for row in rows:
        stats = row.statistics.get('routing', {})
        if (stats.get('total_comparisons', 0) < settings.EVALUATION_ROUTING_MIN_SAMPLES
                or stats.get('unique_users', 0) < settings.EVALUATION_ROUTING_MIN_USERS
                or stats.get('effective_samples', 0) < settings.EVALUATION_ROUTING_MIN_USERS
                or baseline.pk not in stats.get('connected_models', [])):
            continue
        model = next(m for m in candidates if m.pk == row.model_id)
        cost = costs.get((model.provider.slug, model.model_id), 0)
        # Quality dominates. A small cost/latency penalty never uses platform margin.
        score = stats['estimated_quality'] - .03 * math.log1p(cost) - .01 * math.log1p(stats['average_latency'] / 1000)
        supported.append((score, model, stats))
    if len(supported) < 2 or baseline.pk not in {m.pk for _, m, _ in supported}:
        return decision
    score, best, stats = max(supported, key=lambda item: (item[0], -item[1].pk))
    return {**decision, 'model_id': best.model_id, 'provider_slug': best.provider.slug,
            'basis': 'evaluation_bradley_terry', 'confidence': stats['confidence'], 'score': score,
            'estimated_credits': costs.get((best.provider.slug, best.model_id), 0)}
