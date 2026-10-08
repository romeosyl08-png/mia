import hashlib
import hmac
import json
import re
import time
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden
from django.utils import timezone
from django.contrib.auth.models import User
from django.http import HttpResponseBadRequest, HttpResponseServerError, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from api.models import AIModel
from chat.services import (
    CREDITS_PER_DOLLAR,
    MARGIN_MULTIPLIER,
    credits_per_million_tokens,
    is_local_provider,
)
from .models import GoogleIdentity, RechargePurchase, TelegramIdentity
import requests


RECHARGE_PLANS = {
	"5": {"amount_cents": 500, "credits": 50_000},
	"10": {"amount_cents": 1_000, "credits": 110_000},
	"25": {"amount_cents": 2_500, "credits": 300_000},
}
MIN_RECHARGE_CENTS = 500


def _payment_customer_details(request):
    name = request.POST.get("customer_name", "").strip()
    email = request.POST.get("customer_email", "").strip()
    phone = request.POST.get("customer_phone", "").strip()
    phone_digits = "".join(character for character in phone if character.isdigit())

    if not name or len(name) > 100:
        return None, "Le nom complet est requis et doit contenir au plus 100 caractères."
    try:
        validate_email(email)
    except ValidationError:
        return None, "Une adresse e-mail valide est requise."
    if not 6 <= len(phone_digits) <= 20:
        return None, "Un numéro de téléphone valide est requis."
    return {"name": name, "email": email, "phone": phone}, None


def _paydunya_is_configured():
    return bool(
        settings.PAYDUNYA_MASTER_KEY
        and settings.PAYDUNYA_PRIVATE_KEY
        and settings.PAYDUNYA_TOKEN
    )


def _paydunya_checkout_url():
    if settings.PAYDUNYA_CHECKOUT_URL:
        return settings.PAYDUNYA_CHECKOUT_URL
    if settings.PAYDUNYA_PRIVATE_KEY.startswith("test_private_"):
        return "https://app.paydunya.com/sandbox-api/v1/checkout-invoice/create"
    return "https://app.paydunya.com/api/v1/checkout-invoice/create"


def _paydunya_webhook_payload(request):
    if request.POST:
        nested_payload = {}
        for key, value in request.POST.items():
            path = re.findall(r"([^\[\]]+)", key)
            if not path:
                continue
            current = nested_payload
            for part in path[:-1]:
                existing = current.get(part)
                if existing is None:
                    existing = {}
                    current[part] = existing
                if not isinstance(existing, dict):
                    return None
                current = existing
            current[path[-1]] = value
        return nested_payload
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _next_url(request):
	value = request.POST.get("next") or request.GET.get("next")
	if value and url_has_allowed_host_and_scheme(
		value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
	):
		return value
	return reverse("chat:chat-home")


def auth_page(request):
	return render(request, "losign.html", {
		"telegram_bot_username": settings.TELEGRAM_BOT_USERNAME,
		"next_url": _next_url(request),
	})


def login_view(request):
	if request.method == "POST":
		user = authenticate(
			request,
			username=request.POST.get("username", ""),
			password=request.POST.get("password", ""),
		)
		if user is not None:
			login(request, user)
			return redirect(_next_url(request))
	return render(request, "losign.html", {
		"form": type("LoginForm", (), {"errors": True})(),
		"telegram_bot_username": settings.TELEGRAM_BOT_USERNAME,
		"next_url": _next_url(request),
	})


def signup_view(request):
	if request.method == "POST":
		username = request.POST.get("username", "").strip()
		email = request.POST.get("email", "").strip()
		password = request.POST.get("password1", "")
		if username and email and password and password == request.POST.get("password2"):
			if not User.objects.filter(username=username).exists():
				user = User.objects.create_user(username, email, password)
				login(request, user)
				return redirect(_next_url(request))
	return render(request, "losign.html", {
		"signup_form": type("SignupForm", (), {"errors": True})(),
		"telegram_bot_username": settings.TELEGRAM_BOT_USERNAME,
		"next_url": _next_url(request),
	})


def whatsapp_send_otp(request):
	if request.method != "POST":
		return JsonResponse({"error": "Methode non autorisee."}, status=405)
	return JsonResponse(
		{"error": "La verification WhatsApp n'est pas encore configuree."},
		status=501,
	)


@login_required(login_url="/auth/?next=/recharge/")
def recharge_page(request):
	account = getattr(request.user, "credit_account", None)
	credits = account.balance if account is not None else 0
	paydunya_rate = settings.PAYDUNYA_XOF_PER_USD
	paydunya_rate_configured = paydunya_rate.is_finite() and paydunya_rate > 0
	paydunya_available = _paydunya_is_configured() and paydunya_rate_configured
	if not _paydunya_is_configured():
		paydunya_unavailable_reason = "Le paiement PayDunya n’est pas configuré pour le moment."
	elif not paydunya_rate_configured:
		paydunya_unavailable_reason = "Le taux FCFA/USD PayDunya doit être configuré."
	else:
		paydunya_unavailable_reason = ""
	paydunya_minimum_xof = max(settings.PAYDUNYA_MINIMUM_XOF, 200)
	paydunya_presets = (
		[{
			"amount": paydunya_minimum_xof,
			"label": f"{paydunya_minimum_xof:,}".replace(",", " "),
		}] + [
			{
				"amount": int((paydunya_rate * usd_amount).to_integral_value(rounding=ROUND_CEILING)),
				"label": f"{int((paydunya_rate * usd_amount).to_integral_value(rounding=ROUND_CEILING)):,}".replace(",", " "),
			}
			for usd_amount in (5, 10)
		]
		if paydunya_available else []
	)
	default_paydunya_amount = paydunya_presets[0]["amount"] if paydunya_presets else ""
	default_paydunya_credits = (
		int((Decimal(default_paydunya_amount) * CREDITS_PER_DOLLAR / paydunya_rate).to_integral_value(rounding=ROUND_DOWN))
		if paydunya_available else 0
	)
	return render(request, "recharge.html", {
		"plans": RECHARGE_PLANS,
		"credits": credits,
		"credits_display": f"{credits:,}".replace(",", " "),
		"credits_per_dollar": CREDITS_PER_DOLLAR,
		"credits_per_dollar_display": f"{CREDITS_PER_DOLLAR:,}".replace(",", " "),
		"conversion_rate_display": (
			format(paydunya_rate.normalize(), "f")
			if paydunya_rate_configured else ""
		),
		"stripe_available": bool(settings.STRIPE_SECRET_KEY),
		"stripe_currency": settings.STRIPE_CURRENCY.upper(),
		"stripe_default_credits": MIN_RECHARGE_CENTS * CREDITS_PER_DOLLAR // 100,
		"stripe_default_credits_display": f"{MIN_RECHARGE_CENTS * CREDITS_PER_DOLLAR // 100:,}".replace(",", " "),
		"paydunya_available": paydunya_available,
		"paydunya_unavailable_reason": paydunya_unavailable_reason,
		"paydunya_rate": paydunya_rate if paydunya_rate_configured else "",
		"paydunya_minimum_xof": paydunya_minimum_xof,
		"paydunya_presets": paydunya_presets,
		"default_paydunya_amount": default_paydunya_amount,
		"default_paydunya_credits": default_paydunya_credits,
		"default_paydunya_credits_display": f"{default_paydunya_credits:,}".replace(",", " "),
	})


def tariffs_page(request):
    rate = settings.PAYDUNYA_XOF_PER_USD
    conversion_rate_display = (
        format(rate.normalize(), "f") if rate.is_finite() and rate > 0 else ""
    )
    models = (
        AIModel.objects.filter(is_active=True, provider__is_active=True)
        .select_related("provider")
        .order_by("name", "model_id")
    )
    tariffs = [
        {
            "name": model.name,
            "model_id": model.model_id,
            "input_credits": f"{credits_per_million_tokens(model.input_price_per_million):,}".replace(",", " "),
            "output_credits": f"{credits_per_million_tokens(model.output_price_per_million):,}".replace(",", " "),
        }
        for model in models
        if not is_local_provider(model.provider)
    ]
    return render(request, "tariffs.html", {
        "tariffs": tariffs,
        "model_count": len(tariffs),
        "credits_per_dollar": CREDITS_PER_DOLLAR,
        "credits_per_dollar_display": f"{CREDITS_PER_DOLLAR:,}".replace(",", " "),
        "margin_multiplier": MARGIN_MULTIPLIER,
        "conversion_rate_display": conversion_rate_display,
    })


@login_required(login_url="/auth/?next=/recharge/")
def create_recharge_checkout(request):
	if request.method != "POST":
		return JsonResponse({"error": "Methode non autorisee."}, status=405)
	if not settings.STRIPE_SECRET_KEY:
		return JsonResponse({"error": "Stripe n'est pas configure."}, status=503)
	customer, customer_error = _payment_customer_details(request)
	if customer_error:
		return JsonResponse({"error": customer_error}, status=400)

	try:
		amount = Decimal(request.POST.get("amount", ""))
		amount_cents = int(amount * 100)
	except (ArithmeticError, TypeError, ValueError):
		return JsonResponse({"error": "Montant invalide."}, status=400)
	if amount_cents < MIN_RECHARGE_CENTS:
		return JsonResponse({"error": "Le montant Stripe minimum est de 5 dollars."}, status=400)
	plan = {
		"amount_cents": amount_cents,
		"credits": (amount_cents * CREDITS_PER_DOLLAR) // 100,
	}

	try:
		import stripe
		stripe.api_key = settings.STRIPE_SECRET_KEY
		checkout_session = stripe.checkout.Session.create(
			mode="payment",
			payment_method_types=["card"],
			line_items=[{
				"quantity": 1,
				"price_data": {
					"currency": settings.STRIPE_CURRENCY,
					"unit_amount": plan["amount_cents"],
					"product_data": {"name": f"Recharge de {plan['credits']:,} jetons"},
				},
			}],
			success_url=request.build_absolute_uri(reverse("recharge-success")) + "?session_id={CHECKOUT_SESSION_ID}",
			cancel_url=request.build_absolute_uri(reverse("recharge-page")),
			customer_email=customer["email"],
			metadata={
				"user_id": str(request.user.id),
				"credits": str(plan["credits"]),
				"customer_name": customer["name"],
				"customer_phone": customer["phone"],
			},
		)
	except ImportError:
		return JsonResponse({"error": "Installe le paquet stripe."}, status=503)
	return redirect(checkout_session.url)


@login_required(login_url="/auth/?next=/recharge/")
def create_paydunya_checkout(request):
    if request.method != "POST":
        return JsonResponse({"error": "Methode non autorisee."}, status=405)
    if not _paydunya_is_configured():
        return JsonResponse({"error": "PayDunya n'est pas configure."}, status=503)

    rate = settings.PAYDUNYA_XOF_PER_USD
    if not rate.is_finite() or rate <= 0:
        return JsonResponse({"error": "Le taux de conversion FCFA/USD PayDunya n'est pas configure."}, status=503)

    try:
        amount = Decimal(request.POST.get("amount", ""))
        amount_xof = int(amount)
    except (ArithmeticError, TypeError, ValueError):
        return JsonResponse({"error": "Le montant doit etre un nombre entier en FCFA."}, status=400)
    if not amount.is_finite() or amount != Decimal(amount_xof):
        return JsonResponse({"error": "Le montant PayDunya doit etre un nombre entier en FCFA."}, status=400)

    minimum_amount_xof = max(settings.PAYDUNYA_MINIMUM_XOF, 200)
    if amount_xof < minimum_amount_xof:
        return JsonResponse({
            "error": f"Le montant PayDunya minimum est de {minimum_amount_xof:,} FCFA.".replace(",", " "),
        }, status=400)
    customer, customer_error = _payment_customer_details(request)
    if customer_error:
        return JsonResponse({"error": customer_error}, status=400)

    credits = int(
        (Decimal(amount_xof) * CREDITS_PER_DOLLAR / rate).to_integral_value(rounding=ROUND_DOWN)
    )
    payload = {
        "invoice": {
            "items": {
                "credits": {
                    "name": "Recharge de jetons 2AI",
                    "quantity": 1,
                    "unit_price": str(amount_xof),
                    "total_price": str(amount_xof),
                    "description": f"{credits:,} jetons".replace(",", " "),
                },
            },
            "total_amount": amount_xof,
            "description": f"Recharge de {credits:,} jetons 2AI".replace(",", " "),
        },
        "store": {"name": settings.PAYDUNYA_STORE_NAME},
        "custom_data": {
            "customer_name": customer["name"],
            "customer_email": customer["email"],
            "customer_phone": customer["phone"],
        },
        "actions": {
            "callback_url": request.build_absolute_uri(reverse("paydunya-webhook")),
            "return_url": (
                request.build_absolute_uri(reverse("recharge-success"))
                + "?paydunya=1"
            ),
            "cancel_url": request.build_absolute_uri(reverse("recharge-page")),
        },
    }
    headers = {
        "Content-Type": "application/json",
        "PAYDUNYA-MASTER-KEY": settings.PAYDUNYA_MASTER_KEY,
        "PAYDUNYA-PRIVATE-KEY": settings.PAYDUNYA_PRIVATE_KEY,
        "PAYDUNYA-TOKEN": settings.PAYDUNYA_TOKEN,
    }
    try:
        response = requests.post(
            _paydunya_checkout_url(),
            json=payload,
            headers=headers,
            timeout=30,
        )
        data = response.json()
    except (requests.RequestException, ValueError):
        return JsonResponse({"error": "Le prestataire PayDunya est temporairement indisponible."}, status=503)

    payment_url = data.get("response_text") if isinstance(data, dict) else None
    token = data.get("token") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or data.get("response_code") != "00"
        or not isinstance(payment_url, str)
        or not isinstance(token, str)
        or not token
    ):
        provider_error = data.get("description") or data.get("response_text")
        if not isinstance(provider_error, str) or not provider_error:
            provider_error = "PayDunya n'a pas fourni de lien de paiement valide."
        return JsonResponse({"error": f"PayDunya : {provider_error[:300]}"}, status=502)
    payment_url_parts = urlparse(payment_url)
    if (
        payment_url_parts.scheme != "https"
        or payment_url_parts.hostname not in {"app.paydunya.com", "paydunya.com"}
    ):
        return JsonResponse({"error": "PayDunya a fourni un lien de paiement non sécurisé."}, status=502)

    RechargePurchase.objects.create(
        user=request.user,
        paydunya_token=token,
        method=RechargePurchase.Method.PAYDUNYA,
        amount_cents=amount_xof * 100,
        credits=credits,
    )
    return redirect(payment_url)


@login_required(login_url="/auth/?next=/recharge/")
def recharge_success(request):
    return render(request, "recharge_success.html", {
        "paydunya_return": request.GET.get("paydunya") == "1",
    })

@csrf_exempt
def stripe_webhook(request):
	if not settings.STRIPE_WEBHOOK_SECRET:
		return HttpResponse("Stripe webhook non configure.", status=503)
	try:
		import stripe
		event = stripe.Webhook.construct_event(
			request.body,
			request.headers.get("Stripe-Signature", ""),
			settings.STRIPE_WEBHOOK_SECRET,
		)
	except (ImportError, ValueError):
		return HttpResponse("Webhook invalide.", status=400)
	except Exception:
		return HttpResponse("Signature invalide.", status=400)

	if event["type"] == "checkout.session.completed":
		session = event["data"]["object"]
		metadata = session.metadata or {}
		try:
			user_id = int(metadata["user_id"])
			credits = int(metadata["credits"])
		except (KeyError, TypeError, ValueError):
			return HttpResponse("Metadonnees invalides.", status=400)
		with transaction.atomic():
			purchase, created = RechargePurchase.objects.get_or_create(
				stripe_session_id=session.id,
				defaults={
					"user_id": user_id,
					"amount_cents": session.amount_total or 0,
					"credits": credits,
				},
			)
			if created or purchase.status != RechargePurchase.Status.PAID:
				purchase.status = RechargePurchase.Status.PAID
				purchase.paid_at = timezone.now()
				purchase.save(update_fields=["status", "paid_at"])
				account = purchase.user.credit_account
				account.credit(credits, reason="recharge", note=f"Stripe {session.id}")
	return HttpResponse("ok")

@csrf_exempt
def paydunya_webhook(request):
    if request.method != "POST":
        return HttpResponse("Methode non autorisee.", status=405)
    if not settings.PAYDUNYA_MASTER_KEY:
        return HttpResponse("PayDunya non configure.", status=503)

    payload = _paydunya_webhook_payload(request)
    if payload is None:
        return HttpResponse("Notification invalide.", status=400)
    data = payload.get("data", payload) if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return HttpResponse("Notification invalide.", status=400)

    received_hash = str(data.get("hash", ""))
    expected_hash = hashlib.sha512(settings.PAYDUNYA_MASTER_KEY.encode()).hexdigest()
    if not hmac.compare_digest(received_hash, expected_hash):
        return HttpResponseForbidden("Signature PayDunya invalide.")

    invoice = data.get("invoice")
    if not isinstance(invoice, dict):
        return HttpResponse("Facture PayDunya invalide.", status=400)
    token = invoice.get("token")
    if not isinstance(token, str) or not token:
        return HttpResponse("Token PayDunya manquant.", status=400)

    with transaction.atomic():
        purchase = RechargePurchase.objects.select_for_update().filter(
            paydunya_token=token,
            method=RechargePurchase.Method.PAYDUNYA,
        ).first()
        if purchase is None:
            return HttpResponse("Transaction inconnue.", status=404)

        try:
            paid_amount_cents = int(Decimal(str(invoice["total_amount"])) * 100)
        except (ArithmeticError, KeyError, TypeError, ValueError):
            return HttpResponse("Montant PayDunya invalide.", status=400)
        if paid_amount_cents != purchase.amount_cents:
            return HttpResponse("Montant PayDunya incoherent.", status=400)

        status = str(data.get("status", "")).lower()
        if status in {"failed", "cancelled"} and purchase.status == RechargePurchase.Status.PENDING:
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
    return HttpResponse("ok")


def telegram_auth(request):
	"""Validate Telegram's login widget payload before opening a session."""
	if not settings.TELEGRAM_BOT_TOKEN:
		return HttpResponseServerError("Telegram n'est pas configure.")

	next_url = request.GET.get("next")
	payload = request.GET.dict()
	payload.pop("next", None)
	received_hash = payload.pop("hash", "")
	if not received_hash or any(not payload.get(field) for field in ("id", "auth_date")):
		return HttpResponseBadRequest("Reponse Telegram incomplete.")

	try:
		auth_date = int(payload["auth_date"])
		telegram_id = int(payload["id"])
	except (TypeError, ValueError):
		return HttpResponseBadRequest("Reponse Telegram invalide.")

	now = time.time()
	if now - auth_date > 86400 or auth_date > now + 60:
		return HttpResponseBadRequest("Reponse Telegram expiree.")

	data_check_string = "\n".join(f"{key}={payload[key]}" for key in sorted(payload))
	secret_key = hashlib.sha256(settings.TELEGRAM_BOT_TOKEN.encode()).digest()
	expected_hash = hmac.new(
		secret_key, data_check_string.encode(), hashlib.sha256
	).hexdigest()
	if not hmac.compare_digest(expected_hash, received_hash):
		return HttpResponseBadRequest("Signature Telegram invalide.")

	identity = TelegramIdentity.objects.select_related("user").filter(
		telegram_id=telegram_id
	).first()
	if identity is None:
		base_username = payload.get("username", "").strip() or f"telegram_{telegram_id}"
		django_username = base_username
		suffix = 1
		while User.objects.filter(username=django_username).exists():
			django_username = f"{base_username}_{suffix}"
			suffix += 1
		user = User.objects.create_user(
			username=django_username,
			first_name=payload.get("first_name", ""),
			last_name=payload.get("last_name", ""),
		)
		identity = TelegramIdentity.objects.create(user=user, telegram_id=telegram_id)
	else:
		user = identity.user

	identity.username = payload.get("username", "")
	identity.first_name = payload.get("first_name", "")
	identity.last_name = payload.get("last_name", "")
	identity.save(update_fields=["username", "first_name", "last_name", "updated_at"])
	login(request, user, backend="django.contrib.auth.backends.ModelBackend")
	return redirect(next_url if next_url and url_has_allowed_host_and_scheme(
		next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
	) else reverse("chat-home"))

import secrets
import requests

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def google_login(request):
    state = secrets.token_urlsafe(24)
    request.session["google_oauth_state"] = state
    next_url = request.GET.get("next", "/")
    request.session["google_oauth_next"] = next_url

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": request.build_absolute_uri(reverse("google-callback")),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    query = "&".join(f"{k}={requests.utils.quote(str(v))}" for k, v in params.items())
    return redirect(f"{GOOGLE_AUTH_URL}?{query}")


def google_callback(request):
    error = request.GET.get("error")
    if error:
        return HttpResponseBadRequest("Connexion Google annulée.")

    state = request.GET.get("state")
    code = request.GET.get("code")
    expected_state = request.session.pop("google_oauth_state", None)
    if not state or state != expected_state or not code:
        return HttpResponseBadRequest("Requête Google invalide.")

    try:
        token_response = requests.post(GOOGLE_TOKEN_URL, data={
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": request.build_absolute_uri(reverse("google-callback")),
        }, timeout=20)
        token_data = token_response.json()
        access_token = token_data["access_token"]

        userinfo_response = requests.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        info = userinfo_response.json()
    except (requests.RequestException, KeyError, ValueError):
        return HttpResponseServerError("Impossible de contacter Google.")

    google_sub = info.get("sub")
    email = info.get("email", "")
    if not google_sub:
        return HttpResponseBadRequest("Réponse Google incomplète.")

    identity = GoogleIdentity.objects.select_related("user").filter(google_sub=google_sub).first()
    if identity is None:
        base_username = (email.split("@")[0] if email else f"google_{google_sub[:8]}").strip() or f"google_{google_sub[:8]}"
        django_username = base_username
        suffix = 1
        while User.objects.filter(username=django_username).exists():
            django_username = f"{base_username}_{suffix}"
            suffix += 1
        user = User.objects.create_user(
            username=django_username,
            email=email,
            first_name=info.get("given_name", ""),
            last_name=info.get("family_name", ""),
        )
        identity = GoogleIdentity.objects.create(user=user, google_sub=google_sub, email=email)
    else:
        user = identity.user

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    next_url = request.session.pop("google_oauth_next", "/")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = reverse("chat-home")
    return redirect(next_url)


from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .models import UserAPIKey


@login_required(login_url="/auth/?next=/api-keys/")
def api_keys_page(request):
    new_key = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "create":
            name = (request.POST.get("name") or "").strip() or "Clé par défaut"
            full_key, prefix, key_hash = UserAPIKey.generate()
            UserAPIKey.objects.create(user=request.user, name=name, key_prefix=prefix, key_hash=key_hash)
            new_key = full_key
        elif action == "revoke":
            UserAPIKey.objects.filter(id=request.POST.get("key_id"), user=request.user).delete()
            return redirect("api-keys-page")

    return render(request, "api_keys.html", {"keys": request.user.api_keys.all(), "new_key": new_key})


@login_required(login_url="/auth/?next=/api-keys/barrassons/")
def barrassons_guide(request):
	return render(request, "barrassons_guide.html")