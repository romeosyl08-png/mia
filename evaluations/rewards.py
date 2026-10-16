"""Human-reviewed bonus grants; the existing wallet remains the only balance."""
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from ai_user.models import CreditAccount

from .models import EvaluationBudget, EvaluationReward


def reviewer_allowed(reviewer):
    if not reviewer.is_active or not reviewer.is_staff or not reviewer.has_perm('evaluations.change_evaluationreward'):
        raise PermissionDenied


@transaction.atomic
def approve(reward_id, reviewer, note):
    reviewer_allowed(reviewer)
    user_id = EvaluationReward.objects.values_list('user_id', flat=True).get(pk=reward_id)
    CreditAccount.objects.select_for_update().get(user_id=user_id)
    reward = EvaluationReward.objects.select_for_update(of=('self',)).select_related('vote', 'user').get(pk=reward_id)
    if not settings.EVALUATION_REWARDS_ENABLED:
        raise ValueError('Récompenses désactivées.')
    if reward.status != 'pending' or reward.user_id == reviewer.pk or not note.strip():
        raise ValueError('Une validation indépendante motivée est requise pour une récompense en attente.')
    if not reward.vote or reward.vote.suspicious_reasons or not reward.user.is_active:
        raise ValueError('Cette évaluation ne peut pas être récompensée.')
    reward.status = 'approved'
    reward.reviewed_by = reviewer
    reward.review_note = note[:500]
    reward.save(update_fields=['status', 'reviewed_by', 'review_note'])
    return reward


@transaction.atomic
def credit(reward_id, reviewer):
    reviewer_allowed(reviewer)
    budget = EvaluationBudget.objects.select_for_update().get(pk=1)
    user_id = EvaluationReward.objects.values_list('user_id', flat=True).get(pk=reward_id)
    account = CreditAccount.objects.select_for_update().get(user_id=user_id)
    reward = EvaluationReward.objects.select_for_update(of=('self',)).select_related('vote', 'user').get(pk=reward_id)
    if reward.status == 'credited':
        return reward
    if not settings.EVALUATION_REWARDS_ENABLED:
        raise ValueError('Récompenses désactivées.')
    if reward.status != 'approved' or not reward.reviewed_by_id or reward.user_id == reviewer.pk:
        raise ValueError('Récompense non validée.')
    if not reward.user.is_active or not reward.vote or reward.vote.suspicious_reasons:
        raise ValueError('Compte ou évaluation inéligible.')
    now = timezone.now()
    issued = EvaluationReward.objects.filter(user_id=user_id, status='credited')
    if issued.filter(credited_at__date=now.date()).count() >= settings.EVALUATION_DAILY_REWARD_LIMIT:
        raise ValueError('Plafond quotidien atteint.')
    if issued.filter(credited_at__gte=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)).count() >= settings.EVALUATION_MONTHLY_REWARD_LIMIT:
        raise ValueError('Plafond mensuel atteint.')
    if budget.reward_credits_distributed + reward.credits > settings.EVALUATION_GLOBAL_REWARD_BUDGET:
        raise ValueError('Budget global de récompenses atteint.')
    # A bonus is a grant, not a refund. Keep the established transaction reasons.
    entry = account.credit(reward.credits, reason='recharge', note=f'Bonus évaluation #{reward.pk} — utilisable sur 2AI')
    reward.status = 'credited'
    reward.transaction = entry
    reward.credited_at = now
    reward.save(update_fields=['status', 'transaction', 'credited_at'])
    budget.reward_credits_distributed += reward.credits
    budget.save(update_fields=['reward_credits_distributed'])
    return reward


def track_bonus(account_id, amount, refund=False):
    """Called inside the wallet's transaction and row lock, including all AI surfaces.

    Bonus-first fungible accounting. Adjustments restore consumed bonus first;
    ordinary top-ups do not. This never creates or changes a wallet transaction.
    """
    query = EvaluationReward.objects.filter(user__credit_account__pk=account_id, status='credited')
    if refund:
        query = query.filter(consumed_credits__gt=0).order_by('-credited_at', '-pk')
    else:
        from django.db.models import F
        query = query.filter(consumed_credits__lt=F('credits')).order_by('credited_at', 'pk')
    for reward in query.select_for_update():
        available = reward.consumed_credits if refund else reward.credits - reward.consumed_credits
        used = min(amount, available)
        reward.consumed_credits += -used if refund else used
        reward.save(update_fields=['consumed_credits'])
        amount -= used
        if not amount:
            break
