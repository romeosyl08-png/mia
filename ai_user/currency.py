import ipaddress
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN

import requests
from django.conf import settings
from django.core.cache import cache


SESSION_KEY = "display_currency"
SUPPORTED_CURRENCIES = ("XOF", "EUR", "USD")

XOF_COUNTRIES = {"BJ", "BF", "CI", "GW", "ML", "NE", "SN", "TG"}
EUR_COUNTRIES = {
    "AD", "AT", "BE", "CY", "DE", "EE", "ES", "FI", "FR", "GR", "HR",
    "IE", "IT", "LT", "LU", "LV", "MC", "ME", "MT", "NL", "PT", "SI",
    "SK", "SM", "VA",
}


@dataclass(frozen=True)
class Currency:
    code: str
    symbol: str
    name: str
    rate_per_usd: Decimal
    decimals: int
    provider: str


def _positive_decimal(value, fallback):
    try:
        number = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        return Decimal(fallback)
    return number if number.is_finite() and number > 0 else Decimal(fallback)


def currencies():
    return {
        "XOF": Currency(
            code="XOF",
            symbol="FCFA",
            name="Franc CFA (XOF)",
            rate_per_usd=_positive_decimal(settings.PAYDUNYA_XOF_PER_USD, "600"),
            decimals=0,
            provider="paydunya",
        ),
        "EUR": Currency(
            code="EUR",
            symbol="€",
            name="Euro (EUR)",
            rate_per_usd=_positive_decimal(settings.EUR_PER_USD, "0.92"),
            decimals=2,
            provider="stripe",
        ),
        "USD": Currency(
            code="USD",
            symbol="$",
            name="Dollar américain (USD)",
            rate_per_usd=Decimal("1"),
            decimals=2,
            provider="stripe",
        ),
    }


def currency_for_country(country_code):
    country_code = str(country_code or "").strip().upper()
    if country_code in XOF_COUNTRIES:
        return "XOF"
    if country_code in EUR_COUNTRIES:
        return "EUR"
    return "USD"


def _country_from_headers(request):
    for header in (
        "HTTP_CF_IPCOUNTRY",
        "HTTP_X_COUNTRY_CODE",
        "HTTP_CLOUDFRONT_VIEWER_COUNTRY",
    ):
        country = request.META.get(header, "").strip().upper()
        if len(country) == 2 and country.isalpha():
            return country
    return ""


def _client_ip(request):
    candidates = [
        request.META.get("HTTP_CF_CONNECTING_IP", ""),
        request.META.get("HTTP_X_FORWARDED_FOR", "").split(",", 1)[0],
        request.META.get("REMOTE_ADDR", ""),
    ]
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate.strip())
        except ValueError:
            continue
        if address.is_global:
            return str(address)
    return ""


def _country_from_ip(request):
    country = _country_from_headers(request)
    if country:
        return country
    if not settings.IP_CURRENCY_LOOKUP_ENABLED:
        return ""
    client_ip = _client_ip(request)
    if not client_ip:
        return ""
    cache_key = f"currency-country:{client_ip}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        lookup_url = settings.IP_CURRENCY_LOOKUP_URL.format(ip=client_ip)
        response = requests.get(
            lookup_url,
            timeout=settings.IP_CURRENCY_LOOKUP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        country = response.text.strip().upper()
        if len(country) != 2 or not country.isalpha():
            country = ""
    except (KeyError, requests.RequestException, ValueError):
        country = ""
    cache.set(cache_key, country, settings.IP_CURRENCY_CACHE_SECONDS)
    return country


def selected_currency(request):
    available = currencies()
    saved = request.session.get(SESSION_KEY)
    if saved in available:
        return available[saved]
    country = _country_from_ip(request)
    code = currency_for_country(country) if country else settings.DEFAULT_CURRENCY.upper()
    if code not in available:
        code = "USD"
    request.session[SESSION_KEY] = code
    return available[code]


def set_selected_currency(request, code):
    code = str(code or "").strip().upper()
    if code not in SUPPORTED_CURRENCIES:
        return False
    request.session[SESSION_KEY] = code
    request.session.modified = True
    return True


def amount_for_usd(currency, usd_amount):
    value = currency.rate_per_usd * Decimal(str(usd_amount))
    quantum = Decimal("1") if currency.decimals == 0 else Decimal("0.01")
    return value.quantize(quantum, rounding=ROUND_CEILING)


def credits_for_amount(currency, amount, credits_per_dollar):
    return int(
        (Decimal(amount) / currency.rate_per_usd * credits_per_dollar).to_integral_value(
            rounding=ROUND_DOWN
        )
    )


def format_amount(amount, decimals=None):
    amount = Decimal(amount)
    if decimals is None:
        decimals = max(0, -amount.as_tuple().exponent)
    rendered = f"{amount:,.{decimals}f}"
    return rendered.replace(",", "\u00a0").replace(".", ",")


def currency_context(request):
    currency = selected_currency(request)
    reference_amount = amount_for_usd(currency, 1)
    return {
        "currency": currency,
        "reference_amount": reference_amount,
        "reference_amount_display": format_amount(reference_amount, currency.decimals),
    }
