import json
import math
import re
from datetime import timedelta
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from ai_user.models import InsufficientCreditsError
from chat.billing import credits_per_million_tokens

from . import jobs, services
from .models import CATEGORIES, ComparisonSession, ModelPerformance
from .selection import available_models, quote


def endpoint(function):
    @wraps(function)
    def inner(request, *args, **kwargs):
        if not request.user.is_authenticated:
            response = JsonResponse({'error': 'Authentification requise.'}, status=401)
        else:
            try:
                response = function(request, *args, **kwargs)
            except services.RateLimited as error:
                response = JsonResponse({'error': str(error)}, status=429)
                response['Retry-After'] = '60'
            except services.Conflict as error:
                response = JsonResponse({'error': str(error)}, status=409)
            except InsufficientCreditsError:
                response = JsonResponse({'error': 'Solde de jetons insuffisant.'}, status=402)
            except (ValueError, TypeError) as error:
                response = JsonResponse({'error': str(error)}, status=400)
        response['Cache-Control'] = 'private, no-store'
        response['Vary'] = 'Cookie'
        return response
    return inner


def body(request):
    if len(request.body) > 50000:
        raise ValueError('Requête trop volumineuse.')
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError('JSON invalide.') from error
    if not isinstance(payload, dict):
        raise ValueError('Un objet JSON est requis.')
    return payload


def owned(request, pk):
    return get_object_or_404(ComparisonSession, pk=pk, user=request.user)


def serialize(session):
    evaluation = getattr(session, 'vote', None)
    reveal = session.selection_mode == 'manual' or evaluation is not None
    responses = list(session.responses.select_related('model__provider'))
    # Do not forward provider text containing known identities in blind mode.
    identifiers = set()
    for response in responses:
        model = response.model
        identifiers.update([model.name, model.model_id, model.provider.name, model.provider.slug])
        identifiers.update(model.model_id.split('/'))
    pattern = '|'.join(re.escape(token) for token in sorted(identifiers, key=len, reverse=True) if len(token) > 2)
    data = []
    for response in responses:
        text = response.response_text
        if not reveal and pattern:
            text = re.sub(pattern, '[identité masquée]', text, flags=re.IGNORECASE)
        row = {'slot': response.slot, 'response_text': text, 'status': response.status,
               'latency_ms': response.latency_ms, 'error_message': response.error_message}
        if reveal:
            row.update(model={'id': response.model_id, 'name': response.model.name, 'provider': response.model.provider.name},
                credits_charged=response.credits_charged, provider_cost=str(response.provider_cost),
                usage_reliable=response.usage_reliable, input_tokens=response.input_tokens, output_tokens=response.output_tokens)
        data.append(row)
    reward = getattr(evaluation, 'reward', None) if evaluation else None
    return {'id': session.pk, 'prompt': session.prompt, 'category': session.category,
            'selection_mode': session.selection_mode, 'status': session.status, 'responses': data,
            'analysis_consent': session.analysis_consent, 'voted': evaluation is not None,
            'preferred_response': evaluation.preferred_response if evaluation else None,
            'can_vote': bool(session.prompt) and session.status == 'completed' and evaluation is None,
            'reward': {'status': reward.status, 'credits': reward.credits} if reward else None,
            'state_url': reverse('evaluations:detail', args=[session.pk]),
            'vote_url': reverse('evaluations:vote', args=[session.pk])}


@login_required
@require_GET
def index(request):
    if not settings.COMPARISON_ENABLED:
        raise Http404
    response = render(request, 'evaluations/index.html', {
        'categories': CATEGORIES, 'automatic_enabled': settings.COMPARISON_AUTOMATIC_ENABLED,
        'max_prompt_chars': settings.COMPARISON_MAX_PROMPT_CHARS,
        'max_credits': settings.COMPARISON_MAX_CREDITS,
        'automatic_max_credits': settings.COMPARISON_AUTOMATIC_MAX_CREDITS,
        'max_tokens': settings.COMPARISON_MAX_OUTPUT_TOKENS,
        'retention_days': settings.EVALUATION_RETENTION_DAYS,
        'rewards': request.user.evaluation_rewards.order_by('-created_at')[:30],
    })
    response['Cache-Control'] = 'private, no-store'
    return response


def leaderboard_rows(category):
    """Return only fresh, consented Surprise aggregates with adequate support.

    ModelPerformance.routing is built from blinded, non-suspicious votes without
    reward records. Keep a hard privacy floor even if routing thresholds are
    configured lower, and do not order disconnected model groups together.
    """
    min_votes = max(30, settings.EVALUATION_ROUTING_MIN_SAMPLES)
    min_users = max(10, settings.EVALUATION_ROUTING_MIN_USERS)
    cutoff = timezone.now() - timedelta(hours=48)
    candidates = []
    for performance in ModelPerformance.objects.filter(
        category=category, updated_at__gte=cutoff
    ).select_related('model__provider').order_by('model_id'):
        stats = performance.statistics.get('routing') if isinstance(performance.statistics, dict) else None
        if not isinstance(stats, dict):
            continue
        votes, users = stats.get('total_comparisons'), stats.get('unique_users')
        score = stats.get('estimated_quality')
        connected = stats.get('connected_models')
        if (type(votes) is not int or votes < min_votes
                or type(users) is not int or users < min_users
                or not isinstance(connected, list)
                or isinstance(score, bool) or not isinstance(score, (int, float))):
            continue
        try:
            score = float(score)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(score) or not 0 <= score <= 1:
            continue
        stats = {**stats, 'estimated_quality': score}
        component = tuple(sorted({model_id for model_id in connected if type(model_id) is int and model_id > 0}))
        if len(component) < 2 or performance.model_id not in component:
            continue
        candidates.append((performance, stats, component))

    components = {}
    for performance, stats, component in candidates:
        components.setdefault(component, []).append((performance, stats))
    supported = [(component, rows) for component, rows in components.items() if len(rows) >= 2]
    if not supported:
        return [], min_votes, min_users, None
    component, rows = max(supported, key=lambda item: (
        len(item[0]), len(item[1]), sum(row[1]['total_comparisons'] for row in item[1])
    ))
    rows.sort(key=lambda item: (
        -item[1]['estimated_quality'], -item[1]['total_comparisons'],
        -item[1]['unique_users'], item[0].model.name.casefold()
    ))
    ranking = [{
        'name': performance.model.name,
        'provider': performance.model.provider.name,
        'score': stats['estimated_quality'] * 100,
        'votes': stats['total_comparisons'],
        'users': stats['unique_users'],
        'updated_at': performance.updated_at,
    } for performance, stats in rows]
    latest = max(row['updated_at'] for row in ranking)
    return ranking, min_votes, min_users, latest


@login_required
@require_GET
def leaderboard(request):
    if not settings.COMPARISON_ENABLED:
        raise Http404
    category = request.GET.get('category', 'general')
    if category not in dict(CATEGORIES):
        category = 'general'
    rows, min_votes, min_users, updated_at = leaderboard_rows(category)
    response = render(request, 'evaluations/leaderboard.html', {
        'categories': CATEGORIES,
        'selected_category': category,
        'leaderboard': rows,
        'minimum_votes': min_votes,
        'minimum_users': min_users,
        'updated_at': updated_at,
    })
    response['Cache-Control'] = 'private, no-store'
    response['Vary'] = 'Cookie'
    return response


@endpoint
@require_GET
def models(request):
    if not settings.COMPARISON_ENABLED:
        raise Http404
    return JsonResponse({'models': [
        {'id': model.pk, 'name': model.name, 'provider': model.provider.name,
         'estimated_credits': quote('', model, settings.COMPARISON_MAX_OUTPUT_TOKENS)[0],
         'input_credits_per_million': credits_per_million_tokens(model.input_price_per_million),
         'output_credits_per_million': credits_per_million_tokens(model.output_price_per_million)}
        for model in available_models()]})


@endpoint
@require_http_methods(['GET', 'POST'])
def sessions(request):
    if request.method == 'GET':
        return JsonResponse({'comparisons': [{'id': s.pk, 'status': s.status, 'created_at': s.created_at.isoformat(),
            'selection_mode': s.selection_mode} for s in request.user.comparisons.exclude(prompt='')[:50]]})
    if not settings.COMPARISON_ENABLED:
        raise Http404
    session, created = services.create_comparison(request.user, body(request))
    return JsonResponse(serialize(session), status=201 if created else 200)


@endpoint
@require_http_methods(['GET', 'DELETE'])
def detail(request, pk):
    session = owned(request, pk)
    if request.method == 'DELETE':
        services.erase_content(request.user, session.pk)
        return JsonResponse({'deleted': True})
    jobs.finish(session.pk, stale=True)
    session.refresh_from_db()
    return JsonResponse(serialize(session))


@endpoint
@require_POST
def cast_vote(request, pk):
    session = owned(request, pk)
    services.vote(request.user, session.pk, body(request))
    session.refresh_from_db()
    return JsonResponse(serialize(session))
