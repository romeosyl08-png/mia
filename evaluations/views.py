import json
import re
from functools import wraps

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from ai_user.models import InsufficientCreditsError
from chat.billing import credits_per_million_tokens

from . import jobs, services
from .models import CATEGORIES, ComparisonSession
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
                charged_amount_display=f'{response.credits_charged:,} jeton{"s" if response.credits_charged != 1 else ""}'.replace(',', ' '),
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


@endpoint
@require_GET
def models(request):
    if not settings.COMPARISON_ENABLED:
        raise Http404
    rows = []
    for model in available_models():
        estimated_credits = quote('', model, settings.COMPARISON_MAX_OUTPUT_TOKENS)[0]
        rows.append({
            'id': model.pk, 'name': model.name, 'provider': model.provider.name,
            'estimated_credits': estimated_credits,
            'estimated_amount_display': f'{estimated_credits:,} jeton{"s" if estimated_credits != 1 else ""}'.replace(',', ' '),
            'input_credits_per_million': credits_per_million_tokens(model.input_price_per_million),
            'output_credits_per_million': credits_per_million_tokens(model.output_price_per_million),
        })
    return JsonResponse({'models': rows})


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
