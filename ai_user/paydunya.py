"""Confirmation PayDunya et crédit unique d'une recharge."""

import hashlib
import hmac
from decimal import Decimal
from urllib.parse import quote

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import RechargePurchase


class PayDunyaError(Exception):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def is_configured():
    return bool(
        settings.PAYDUNYA_MASTER_KEY
        and settings.PAYDUNYA_PRIVATE_KEY
        and settings.PAYDUNYA_TOKEN
    )


def checkout_url():
    if settings.PAYDUNYA_CHECKOUT_URL:
        return settings.PAYDUNYA_CHECKOUT_URL
    if settings.PAYDUNYA_PRIVATE_KEY.startswith("test_private_"):
        return "https://app.paydunya.com/sandbox-api/v1/checkout-invoice/create"
    return "https://app.paydunya.com/api/v1/checkout-invoice/create"


def headers():
    return {
        "Content-Type": "application/json",
        "PAYDUNYA-MASTER-KEY": settings.PAYDUNYA_MASTER_KEY,
        "PAYDUNYA-PRIVATE-KEY": settings.PAYDUNYA_PRIVATE_KEY,
        "PAYDUNYA-TOKEN": settings.PAYDUNYA_TOKEN,
    }


def apply_invoice(data, *, expected_token=None, user_id=None):
    """Valide une réponse signée et sérialise les crédits IPN/retour navigateur."""
    if not settings.PAYDUNYA_MASTER_KEY:
        raise PayDunyaError("PayDunya non configure.", 503)
    if not isinstance(data, dict):
        raise PayDunyaError("Notification invalide.")

    received_hash = data.get("hash", "")
    expected_hash = hashlib.sha512(settings.PAYDUNYA_MASTER_KEY.encode()).hexdigest()
    if not isinstance(received_hash, str) or not hmac.compare_digest(received_hash.encode(), expected_hash.encode()):
        raise PayDunyaError("Signature PayDunya invalide.", 403)

    invoice = data.get("invoice")
    if not isinstance(invoice, dict):
        raise PayDunyaError("Facture PayDunya invalide.")
    token = invoice.get("token")
    if not isinstance(token, str) or not token:
        raise PayDunyaError("Jeton PayDunya manquant.")
    if expected_token is not None and token != expected_token:
        raise PayDunyaError("Jeton PayDunya incoherent.")

    with transaction.atomic():
        purchases = RechargePurchase.objects.select_for_update().filter(
            paydunya_token=token,
            method=RechargePurchase.Method.PAYDUNYA,
        )
        if user_id is not None:
            purchases = purchases.filter(user_id=user_id)
        purchase = purchases.first()
        if purchase is None:
            raise PayDunyaError("Transaction inconnue.", 404)

        try:
            paid_amount_cents = Decimal(str(invoice["total_amount"])) * 100
        except (ArithmeticError, KeyError, TypeError, ValueError):
            raise PayDunyaError("Montant PayDunya invalide.") from None
        if not paid_amount_cents.is_finite():
            raise PayDunyaError("Montant PayDunya invalide.")
        if paid_amount_cents != purchase.amount_cents:
            raise PayDunyaError("Montant PayDunya incoherent.")

        status = str(data.get("status", "")).strip().lower()
        if status in {"failed", "cancelled", "canceled"} and purchase.status == RechargePurchase.Status.PENDING:
            purchase.status = RechargePurchase.Status.FAILED
            purchase.save(update_fields=["status"])
        elif status == "completed" and purchase.status != RechargePurchase.Status.PAID:
            purchase.status = RechargePurchase.Status.PAID
            purchase.paid_at = timezone.now()
            purchase.save(update_fields=["status", "paid_at"])
            purchase.user.credit_account.credit(
                purchase.credits,
                reason="recharge",
                note=f"PayDunya {token}",
            )
        return purchase


def confirm_purchase(purchase):
    """Vérifie la facture auprès de PayDunya, même si l'IPN n'est pas arrivé."""
    if purchase.status == RechargePurchase.Status.PAID:
        return purchase
    if not is_configured():
        raise PayDunyaError("PayDunya non configure.", 503)

    url = checkout_url().rsplit("/", 1)[0] + "/confirm/" + quote(purchase.paydunya_token, safe="")
    try:
        response = requests.get(url, headers=headers(), timeout=10)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        raise PayDunyaError("La verification PayDunya est temporairement indisponible.", 503) from None
    if not isinstance(data, dict) or data.get("response_code") != "00":
        raise PayDunyaError("PayDunya n'a pas confirme la facture.", 502)
    return apply_invoice(data, expected_token=purchase.paydunya_token, user_id=purchase.user_id)
