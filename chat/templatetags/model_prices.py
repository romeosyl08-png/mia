from decimal import Decimal, InvalidOperation

from django import template

from chat.billing import credits_per_million_tokens


register = template.Library()


@register.filter
def model_price(value):
    """2AI rate in jetons, using the same conversion as API billing."""
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            return '—'
        credits = credits_per_million_tokens(amount)
    except (InvalidOperation, TypeError, ValueError):
        return '—'
    return f'{credits:,} jeton{"s" if credits != 1 else ""}'.replace(',', ' ')
