import hashlib
import json
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from ai_user.models import CreditAccount
from chat.capabilities import request_requirements
from chat.services import check_user_rate_limit

from .models import CATEGORIES, ComparisonResponse, ComparisonSession, EvaluationBudget, EvaluationReward, EvaluationVote
from .selection import available_models, quote, select_pair

logger = logging.getLogger(__name__)


class Conflict(ValueError):
    pass


class RateLimited(ValueError):
    pass


def validate_request(payload):
    if not isinstance(payload, dict):
        raise ValueError('Un objet JSON est requis.')
    prompt = payload.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > settings.COMPARISON_MAX_PROMPT_CHARS:
        raise ValueError('Question vide ou trop longue.')
    mode = payload.get('selection_mode', 'manual')
    category = payload.get('category', 'general')
    if mode not in ComparisonSession.Mode.values or category not in dict(CATEGORIES):
        raise ValueError('Mode ou catégorie invalide.')
    if request_requirements(prompt):
        raise ValueError('La comparaison accepte des demandes de texte sans outils, pièces jointes ni génération multimédia.')
    try:
        key = uuid.UUID(str(payload.get('request_key', '')))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError('Identifiant de requête UUID requis.') from error
    consent = payload.get('analysis_consent', False)
    if type(consent) is not bool:
        raise ValueError('Consentement invalide.')
    ids = payload.get('model_ids', []) if mode == 'manual' else []
    if mode == 'manual' and (not isinstance(ids, list) or len(ids) != 2 or any(type(i) is not int for i in ids) or ids[0] == ids[1]):
        raise ValueError('Sélectionnez exactement deux modèles distincts.')
    data = {'prompt': prompt.strip(), 'selection_mode': mode, 'category': category,
            'analysis_consent': consent, 'model_ids': ids}
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return key, data, digest


@transaction.atomic
def create_comparison(user, payload):
    if not settings.COMPARISON_ENABLED:
        raise ValueError('Comparaisons désactivées.')
    key, data, digest = validate_request(payload)
    # Global budget -> wallet -> session is also the reward administration lock order.
    budget_row = EvaluationBudget.objects.select_for_update().get(pk=1)
    account = CreditAccount.objects.select_for_update().get(user=user)
    existing = ComparisonSession.objects.filter(user=user, request_key=key).first()
    if existing:
        if existing.request_digest != digest:
            raise Conflict('Cet identifiant a déjà été utilisé pour une autre demande.')
        return existing, False
    if data['selection_mode'] == 'automatic' and not settings.COMPARISON_AUTOMATIC_ENABLED:
        raise ValueError('Mode Surprise désactivé.')
    allowed, _ = check_user_rate_limit(user.pk, settings.COMPARISON_RATE_LIMIT, 60, 'comparisons')
    if not allowed:
        raise RateLimited('Trop de comparaisons. Réessayez dans une minute.')
    if ComparisonSession.objects.filter(user=user, created_at__gte=timezone.now() - timedelta(seconds=60)).count() >= settings.COMPARISON_RATE_LIMIT:
        raise RateLimited('Trop de comparaisons. Réessayez dans une minute.')
    if ComparisonSession.objects.filter(user=user, settled=False).exists():
        raise Conflict('Une comparaison est déjà en attente ou en cours.')
    models = available_models()
    cap = settings.COMPARISON_MAX_CREDITS
    if data['selection_mode'] == 'automatic':
        cap = min(cap, settings.COMPARISON_AUTOMATIC_MAX_CREDITS)
        pair, metadata = select_pair(data['prompt'], data['category'], settings.COMPARISON_MAX_OUTPUT_TOKENS, cap, models)
    else:
        by_id = {m.pk: m for m in models}
        pair = [by_id.get(pk) for pk in data['model_ids']]
        if any(m is None for m in pair) or pair[0].model_id == pair[1].model_id:
            raise ValueError('Modèles indisponibles ou identiques.')
        metadata = {'strategy': 'manual_v1'}
    quotes = [quote(data['prompt'], m, settings.COMPARISON_MAX_OUTPUT_TOKENS) for m in pair]
    reserve = sum(q[0] for q in quotes)
    if reserve > cap:
        raise ValueError('Cette comparaison dépasse le budget maximal.')
    now = timezone.now()
    today = now.date()
    daily = ComparisonSession.objects.filter(user=user, created_at__date=today).aggregate(total=Sum('reserved_credits'))['total'] or 0
    if daily + reserve > settings.COMPARISON_USER_DAILY_CREDITS:
        raise RateLimited('Budget quotidien de comparaisons atteint.')
    if budget_row.comparison_day != today:
        budget_row.comparison_day = today
        budget_row.comparison_reserved = 0
    if budget_row.comparison_reserved + reserve > settings.COMPARISON_GLOBAL_DAILY_CREDITS:
        raise RateLimited('Budget quotidien global de comparaisons atteint.')
    session = ComparisonSession.objects.create(
        user=user, request_key=key, request_digest=digest, selection_metadata=metadata,
        reserved_credits=reserve, max_output_tokens=settings.COMPARISON_MAX_OUTPUT_TOKENS,
        **{k: v for k, v in data.items() if k != 'model_ids'},
    )
    # Creation of both slots and reservation is indivisible. No public create-response API.
    ComparisonResponse.objects.bulk_create([
        ComparisonResponse(comparison=session, model=model, slot=slot, reserved_credits=q[0],
                           estimated_input_tokens=q[1], input_price=model.input_price_per_million,
                           output_price=model.output_price_per_million)
        for slot, model, q in zip(('A', 'B'), pair, quotes)
    ])
    account.debit(reserve, reason='usage', note=f'Réservation comparaison #{session.pk}')
    budget_row.comparison_reserved += reserve
    budget_row.save(update_fields=['comparison_day', 'comparison_reserved'])
    return session, True


def normalize_justification(text):
    return hashlib.sha256(' '.join(text.casefold().split()).encode()).hexdigest() if text.strip() else ''


@transaction.atomic
def vote(user, session_id, payload):
    if not isinstance(payload, dict):
        raise ValueError('Un objet JSON est requis.')
    # Serialize votes for this account, including repeated submissions in separate sessions.
    CreditAccount.objects.select_for_update().get(user=user)
    session = ComparisonSession.objects.select_for_update().get(pk=session_id, user=user)
    if not session.prompt or session.status != 'completed' or session.responses.filter(status='completed').count() != 2:
        raise Conflict('Les deux réponses doivent être terminées pour voter.')
    choice = payload.get('preferred_response')
    justification = payload.get('justification', '')
    scores = {key: payload.get(key) for key in ('accuracy_score', 'relevance_score', 'clarity_score')}
    if choice not in {'A', 'B', 'tie', 'both_bad'}:
        raise ValueError('Préférence invalide.')
    if not isinstance(justification, str) or len(justification) > 3000:
        raise ValueError('Justification invalide (3 000 caractères maximum).')
    for score in scores.values():
        if score is not None and (type(score) is not int or not 1 <= score <= 5):
            raise ValueError('Les notes doivent être des entiers de 1 à 5.')
    existing = EvaluationVote.objects.filter(comparison=session).first()
    if existing:
        if existing.preferred_response == choice and existing.justification == justification.strip() and all(getattr(existing, k) == v for k, v in scores.items()):
            return existing, False
        raise Conflict('Une évaluation est déjà enregistrée.')
    if not check_user_rate_limit(user.pk, settings.EVALUATION_VOTE_RATE_LIMIT, 60, 'evaluation-votes')[0]:
        raise RateLimited('Trop de votes. Réessayez dans une minute.')
    if EvaluationVote.objects.filter(user=user, created_at__gte=timezone.now() - timedelta(seconds=60)).count() >= settings.EVALUATION_VOTE_RATE_LIMIT:
        raise RateLimited('Trop de votes. Réessayez dans une minute.')
    recent = EvaluationVote.objects.filter(user=user, created_at__gte=timezone.now() - timedelta(days=30))
    reasons = []
    digest = normalize_justification(justification)
    if digest and recent.filter(justification_hash=digest).exists():
        reasons.append('repeated_justification')
    if session.completed_at and (timezone.now() - session.completed_at).total_seconds() < settings.EVALUATION_MIN_READING_SECONDS:
        reasons.append('rapid_vote')
    previous = list(recent.order_by('-created_at').values_list('preferred_response', flat=True)[:9])
    if len(previous) == 9 and sum(p == choice for p in previous) >= 8:
        reasons.append('repetitive_votes')
    evaluation = EvaluationVote.objects.create(comparison=session, user=user, preferred_response=choice,
        justification=justification.strip(), justification_hash=digest, suspicious_reasons=reasons, **scores)
    # Eligibility only opens a human review. Length is never a quality verdict.
    if settings.EVALUATION_REWARDS_ENABLED and settings.EVALUATION_REWARD_CREDITS > 0:
        old_enough = user.date_joined <= timezone.now() - timedelta(days=settings.EVALUATION_REWARD_MIN_ACCOUNT_DAYS)
        eligible = user.is_active and old_enough and not reasons and all(v is not None for v in scores.values()) and bool(digest)
        EvaluationReward.objects.create(vote=evaluation, user=user, credits=settings.EVALUATION_REWARD_CREDITS,
            status='pending' if eligible else 'rejected', review_note='' if eligible else 'Critères préalables non satisfaits.')
    return evaluation, True


@transaction.atomic
def erase_content(user, session_id, erase_vote=False):
    from . import jobs
    from .models import ModelPerformance
    EvaluationBudget.objects.select_for_update().get(pk=1)
    CreditAccount.objects.select_for_update().get(user=user)
    session = ComparisonSession.objects.select_for_update().get(pk=session_id, user=user)
    jobs.finish(session.pk, cancel=True)
    session.prompt = ''
    session.analysis_consent = False
    session.save(update_fields=['prompt', 'analysis_consent'])
    session.responses.update(response_text='')
    EvaluationReward.objects.filter(vote__comparison=session, status__in=['pending', 'approved']).update(
        status='rejected', review_note='Contenu supprimé ou consentement retiré.')
    if erase_vote:
        EvaluationVote.objects.filter(comparison=session).delete()
    else:
        EvaluationVote.objects.filter(comparison=session).update(justification='', justification_hash='')
    ModelPerformance.objects.filter(category=session.category).delete()
