"""Durable at-most-once dispatch; interrupted calls are never replayed automatically."""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ai_user.models import CreditAccount
from api.models import APIKey
from chat.billing import credits_for_usage, settle_reservation
from chat.services import call_llm_provider, is_local_provider

from .models import ComparisonResponse, ComparisonSession
from .selection import available_models

logger = logging.getLogger(__name__)


@transaction.atomic
def claim(session_id=None):
    if not settings.COMPARISON_ENABLED:
        return None
    # A short row lock protects the claim; never keep a transaction open over HTTP.
    query = ComparisonSession.objects.select_for_update().filter(status='pending', settled=False).order_by('created_at')
    if session_id is not None:
        query = query.filter(pk=session_id)
    session = query.first()
    if session is None:
        return None
    session.status = 'processing'
    session.started_at = timezone.now()
    session.save(update_fields=['status', 'started_at'])
    session.responses.update(status='processing')
    return session


def provider_result(response, session, secret):
    start = time.monotonic()
    try:
        model = response.model
        text, inputs, outputs = call_llm_provider(model.provider, secret, model.model_id,
            [{'role': 'user', 'content': session.prompt}], max_tokens=session.max_output_tokens)
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Empty provider response')
        # Malformed, absent or implausible metering must never debit an estimate.
        reliable = all(type(value) is int and 0 < value < 100_000_000 for value in (inputs, outputs))
        input_tokens, output_tokens = (inputs, outputs) if reliable else (0, 0)
        # Freeze prices at reservation time, including when catalog prices change.
        model.input_price_per_million = response.input_price
        model.output_price_per_million = response.output_price
        actual = credits_for_usage(model, input_tokens, output_tokens) if reliable else 0
        cost_inputs = input_tokens if reliable else response.estimated_input_tokens
        cost_outputs = output_tokens if reliable else session.max_output_tokens
        cost = (Decimal(cost_inputs) * response.input_price + Decimal(cost_outputs) * response.output_price) / 1_000_000
        return dict(response_text=text, input_tokens=input_tokens, output_tokens=output_tokens,
            usage_reliable=reliable, provider_cost=cost, status='completed',
            credits_charged=min(actual, response.reserved_credits),
            billing_shortfall=max(0, actual - response.reserved_credits),
            latency_ms=max(0, round((time.monotonic() - start) * 1000)), error_message='')
    except Exception as error:
        # Provider exceptions can embed prompts, identities, URLs and credentials.
        # Log only type and internal response ID; never expose upstream messages.
        logger.warning('Comparison response failed response_id=%s error_type=%s', response.pk, type(error).__name__)
        return dict(status='failed', error_message='Le fournisseur n’a pas fourni de réponse exploitable.',
                    provider_cost=(Decimal(response.estimated_input_tokens) * response.input_price
                                   + Decimal(session.max_output_tokens) * response.output_price) / 1_000_000,
                    latency_ms=max(0, round((time.monotonic() - start) * 1000)))


@transaction.atomic
def store_result(session_id, response_id, result):
    session = ComparisonSession.objects.select_for_update().get(pk=session_id)
    if session.status != 'processing' or session.settled:
        return
    ComparisonResponse.objects.filter(pk=response_id, comparison=session, status='processing').update(**result)


@transaction.atomic
def finish(session_id, cancel=False, stale=False):
    user_id = ComparisonSession.objects.values_list('user_id', flat=True).get(pk=session_id)
    account = CreditAccount.objects.select_for_update().get(user_id=user_id)
    session = ComparisonSession.objects.select_for_update().get(pk=session_id)
    if session.settled:
        return session
    if stale:
        threshold = timezone.now() - timedelta(seconds=settings.COMPARISON_STALE_SECONDS)
        if (session.started_at or session.created_at) > threshold:
            return session
    for response in session.responses.filter(status__in=['pending', 'processing']):
        if response.status == 'processing':
            response.provider_cost = (Decimal(response.estimated_input_tokens) * response.input_price
                                      + Decimal(session.max_output_tokens) * response.output_price) / 1_000_000
        response.status = 'failed'
        response.error_message = 'Comparaison interrompue ; réservation remboursée.'
        response.save(update_fields=['provider_cost', 'status', 'error_message'])
    actual = sum(session.responses.values_list('credits_charged', flat=True))
    settle_reservation(account, session.reserved_credits, actual, f'comparaison #{session.pk}')
    session.settled = True
    session.completed_at = timezone.now()
    session.status = 'cancelled' if cancel else 'completed' if session.responses.filter(status='completed').count() == 2 else 'failed'
    session.save(update_fields=['settled', 'completed_at', 'status'])
    return session


def execute(session):
    # Recheck eligibility at dispatch, since a provider/key may have been disabled in the queue.
    if not settings.COMPARISON_ENABLED or (session.selection_mode == 'automatic' and not settings.COMPARISON_AUTOMATIC_ENABLED):
        finish(session.pk, cancel=True)
        return
    try:
        allowed = {m.pk for m in available_models()}
        responses = list(session.responses.select_related('model__provider'))
        if len(responses) != 2:
            raise ValueError('Incomplete pair')
        secrets = {}
        for response in responses:
            if response.model_id not in allowed:
                store_result(session.pk, response.pk, {'status': 'failed', 'error_message': 'Modèle devenu indisponible.'})
                continue
            provider = response.model.provider
            key = APIKey.objects.filter(provider=provider, is_active=True).first()
            if key:
                try:
                    secrets[response.pk] = key.get_key()
                    APIKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
                except Exception as error:
                    logger.warning('Comparison key unavailable response_id=%s error_type=%s', response.pk, type(error).__name__)
                    store_result(session.pk, response.pk, {'status': 'failed', 'error_message': 'Connexion au fournisseur indisponible.'})
            elif is_local_provider(provider):
                secrets[response.pk] = ''
            else:
                store_result(session.pk, response.pk, {'status': 'failed', 'error_message': 'Modèle devenu indisponible.'})
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(provider_result, response, session, secrets[response.pk]): response.pk
                       for response in responses if response.pk in secrets}
            for future in as_completed(futures):
                store_result(session.pk, futures[future], future.result())
    except Exception as error:
        logger.error('Comparison worker failed session_id=%s error_type=%s', session.pk, type(error).__name__)
    finally:
        finish(session.pk)


def recover_stale():
    threshold = timezone.now() - timedelta(seconds=settings.COMPARISON_STALE_SECONDS)
    sessions = ComparisonSession.objects.filter(settled=False, created_at__lte=threshold).values_list('pk', flat=True)
    for pk in list(sessions):
        finish(pk, stale=True)
