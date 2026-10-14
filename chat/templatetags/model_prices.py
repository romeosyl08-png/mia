from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter
def usd_price(value):
    """Readable French USD amount, preserving small prices without trailing zeros."""
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            return '—'
    except (InvalidOperation, TypeError, ValueError):
        return '—'
    integer, _, fraction = format(amount, 'f').partition('.')
    fraction = fraction.rstrip('0').ljust(2, '0')
    integer = format(int(integer), ',').replace(',', '\u202f')
    return f'{integer},{fraction} $'
