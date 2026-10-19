from decimal import Decimal, InvalidOperation

from django import template

from ai_user.currency import format_usd_price
from chat.billing import MARGIN_MULTIPLIER


register = template.Library()


@register.filter
def model_price(value, currency):
    """2AI rate in the selected currency, including the billing margin once."""
    try:
        amount = Decimal(str(value)) * Decimal(str(MARGIN_MULTIPLIER))
    except (InvalidOperation, TypeError, ValueError):
        return '—'
    return format_usd_price(currency, amount)
