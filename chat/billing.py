"""Shared token pricing and reservation settlement for all AI surfaces."""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN

from django.db import transaction

from ai_user.models import InsufficientCreditsError

CREDITS_PER_DOLLAR = 10_000
MARGIN_MULTIPLIER = 1.5


def credits_per_million_tokens(price_usd):
    return round(Decimal(str(price_usd or 0)) * Decimal(str(MARGIN_MULTIPLIER)) * CREDITS_PER_DOLLAR)


def credits_for_usage(model, prompt_tokens, completion_tokens):
    cost = (
        Decimal(str(prompt_tokens)) * Decimal(str(model.input_price_per_million or 0))
        + Decimal(str(completion_tokens)) * Decimal(str(model.output_price_per_million or 0))
    ) / 1_000_000
    credits = cost * Decimal(str(MARGIN_MULTIPLIER)) * CREDITS_PER_DOLLAR
    return max(1, int(credits.to_integral_value(rounding=ROUND_HALF_EVEN)))


@dataclass(frozen=True)
class Settlement:
    charged_credits: int
    shortfall: int = 0


@transaction.atomic
def settle_reservation(account, reserve, actual, note):
    if actual < reserve:
        account.credit(reserve - actual, reason='adjustment', note=f'Ajustement {note}')
    elif actual > reserve:
        try:
            account.debit(actual - reserve, reason='usage', note=f'Complément {note}')
        except InsufficientCreditsError:
            return Settlement(reserve, actual - reserve)
    return Settlement(actual)
