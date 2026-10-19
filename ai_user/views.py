import hashlib
import hmac
import json
import logging
import re
import time
from decimal import Decimal, ROUND_DOWN
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordResetView
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpResponse
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
    is_local_provider,
)
from .currency import (
	SUPPORTED_CURRENCIES,
	amount_for_usd,
	credits_for_amount,
	currencies,
	currency_context,
	format_amount,
	selected_currency,
	set_selected_currency,
)
from .models import GoogleIdentity, RechargePurchase, TelegramIdentity
from .paydunya import (
    PayDunyaError,
    apply_invoice as _apply_paydunya_invoice,
    checkout_url as _paydunya_checkout_url,
    confirm_purchase as _confirm_paydunya_purchase,
    headers as _paydunya_headers,
    is_configured as _paydunya_is_configured,
)
from .forms import SignupForm
import requests

logger = logging.getLogger(__name__)


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


def _public_absolute_uri(request, path):
	if settings.PUBLIC_BASE_URL:
		return f"{settings.PUBLIC_BASE_URL}{path}"
	return request.build_absolute_uri(path)


def _google_redirect_uri(request):
	return settings.GOOGLE_REDIRECT_URI or _public_absolute_uri(
		request, reverse("google-callback")
	)


def _client_identifier(request):
    """Adresse IP utilisée pour la limitation des tentatives d'authentification.

    `X-Forwarded-For` n'est pris en compte que si l'application déclare être
    derrière un proxy de confiance : sinon l'en-tête est contrôlable par le
    client et la limitation serait contournable en une ligne.
    """
    if getattr(settings, "TRUST_X_FORWARDED_FOR", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[-1].strip() or "inconnue"
    return request.META.get("REMOTE_ADDR") or "inconnue"


def _signup_rate_limited(request):
    """Plafonne les créations de compte par adresse IP."""
    key = f"signup:{_client_identifier(request)}"
    if cache.get(key, 0) >= getattr(settings, "SIGNUP_MAX_PER_WINDOW", 5):
        return True
    window = getattr(settings, "SIGNUP_WINDOW_SECONDS", 3600)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window)
    return False


def _auth_failures_blocked(request, identifier):
    key = f"auth-failures:{_client_identifier(request)}:{(identifier or '').strip().lower()}"
    return cache.get(key, 0) >= getattr(settings, "AUTH_MAX_FAILURES", 10)


def _record_auth_failure(request, identifier):
    key = f"auth-failures:{_client_identifier(request)}:{(identifier or '').strip().lower()}"
    window = getattr(settings, "AUTH_FAILURE_WINDOW_SECONDS", 300)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window)


def _auth_context(request, **extra):
	next_url = _next_url(request)
	telegram_configured = bool(
		settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_BOT_USERNAME
	)
	context = {
		"telegram_bot_username": settings.TELEGRAM_BOT_USERNAME,
		"telegram_configured": telegram_configured,
		"telegram_auth_url": (
			f"{_public_absolute_uri(request, reverse('telegram_auth'))}?{urlencode({'next': next_url})}"
			if telegram_configured else ""
		),
		"google_configured": bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET),
		"next_url": next_url,
		"whatsapp_available": False,
	}
	context.update(extra)
	return context


def auth_page(request):
	return render(request, "losign.html", _auth_context(request))


def login_view(request):
	if request.method == "POST":
		identifier = request.POST.get("username", "").strip()
		password = request.POST.get("password", "")
		if _auth_failures_blocked(request, identifier):
			logger.warning("Authentification bloquée après trop d'échecs (identifiant=%r)", identifier[:40])
			return render(request, "losign.html", _auth_context(
				request,
				form=type("LoginForm", (), {"errors": True})(),
				login_error="Trop de tentatives infructueuses. Patiente quelques minutes puis réessaie.",
			), status=429)
		user = authenticate(
			request,
			username=identifier,
			password=password,
		)
		if user is None and '@' in identifier:
			matches = list(User.objects.filter(email__iexact=identifier).values_list('username', flat=True)[:2])
			if len(matches) == 1:
				user = authenticate(request, username=matches[0], password=password)
		if user is not None:
			login(request, user)
			return redirect(_next_url(request))
		_record_auth_failure(request, identifier)
	return render(request, "losign.html", _auth_context(
		request,
		form=type("LoginForm", (), {"errors": request.method == "POST"})(),
	))


def signup_view(request):
	form = SignupForm(request.POST if request.method == 'POST' else None)
	if request.method == 'POST' and _signup_rate_limited(request):
		logger.warning("Inscription refusée : trop de créations depuis %s", _client_identifier(request))
		return render(request, "losign.html", _auth_context(
			request,
			signup_form=form,
			signup_error="Trop de comptes créés depuis cette connexion. Patiente quelques minutes.",
		), status=429)
	if request.method == 'POST' and form.is_valid():
		with transaction.atomic():
			user = form.save()
		login(request, user)
		return redirect(_next_url(request))
	return render(request, "losign.html", _auth_context(request, signup_form=form))


class SafePasswordResetView(PasswordResetView):
    """Réinitialisation du mot de passe, sans 500 si l’envoi d’e-mail échoue.

    Sans serveur SMTP configuré, `PasswordResetView` propage l’erreur de
    connexion. On préfère un message explicite, l’utilisateur conservant
    Telegram ou Google comme porte de secours.
    """

    template_name = "registration/password_reset_form.html"
    email_template_name = "registration/password_reset_email.html"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = None

    def get_success_url(self):
        from django.urls import reverse

        return reverse("password_reset_done")

    def get_extra_email_context(self):
        from django.contrib.auth import PASSWORD_RESET_TIMEOUT

        return {
            "password_reset_timeout_days": PASSWORD_RESET_TIMEOUT // 86400,
        }

    def form_valid(self, form):
        try:
            return super().form_valid(form)
        except Exception:
            logger.exception("Password reset email could not be sent")
            form.add_error(
                None,
                "L’envoi de l’e-mail a échoué : le serveur de messagerie n’est pas "
                "configuré. Utilise Telegram ou Google pour te connecter.",
            )
            return self.form_invalid(form)


def whatsapp_send_otp(request):
	if request.method != "POST":
		return JsonResponse({"error": "Methode non autorisee."}, status=405)
	return JsonResponse(
		{"error": "La connexion WhatsApp est indisponible pour le moment."},
		status=501,
	)


@login_required(login_url="/auth/?next=/recharge/")
def recharge_page(request):
	account = getattr(request.user, "credit_account", None)
	credits = account.balance if account is not None else 0
	currency_data = currency_context(request)
	currency = currency_data["currency"]
	if currency.code == "XOF":
		minimum_amount = Decimal(max(settings.PAYDUNYA_MINIMUM_XOF, 200))
		preset_amounts = [minimum_amount, amount_for_usd(currency, 5), amount_for_usd(currency, 10)]
		payment_available = _paydunya_is_configured()
		unavailable_reason = "Le paiement PayDunya n’est pas configuré pour le moment."
	else:
		minimum_amount = amount_for_usd(currency, 5)
		preset_amounts = [amount_for_usd(currency, value) for value in (5, 10, 25)]
		payment_available = bool(settings.STRIPE_SECRET_KEY)
		unavailable_reason = "Le paiement par carte n’est pas configuré pour le moment."
	presets = [
		{
			"amount": format(amount, f".{currency.decimals}f"),
			"label": format_amount(amount, currency.decimals),
		}
		for amount in dict.fromkeys(preset_amounts)
	]
	default_credits = credits_for_amount(currency, minimum_amount, CREDITS_PER_DOLLAR)
	context = {
		"credits": credits,
		"credits_display": f"{credits:,}".replace(",", " "),
		"credits_per_dollar": CREDITS_PER_DOLLAR,
		"credits_per_dollar_display": f"{CREDITS_PER_DOLLAR:,}".replace(",", " "),
		"currency_options": [currencies()[code] for code in SUPPORTED_CURRENCIES],
		"minimum_amount": format(minimum_amount, f".{currency.decimals}f"),
		"minimum_amount_display": format_amount(minimum_amount, currency.decimals),
		"presets": presets,
		"default_credits_display": f"{default_credits:,}".replace(",", " "),
		"payment_available": payment_available,
		"payment_unavailable_reason": unavailable_reason,
	}
	context.update(currency_data)
	return render(request, "recharge.html", context)


@login_required(login_url="/auth/?next=/recharge/")
def set_currency(request):
	if request.method != "POST":
		return JsonResponse({"error": "Methode non autorisee."}, status=405)
	if not set_selected_currency(request, request.POST.get("currency")):
		return JsonResponse({"error": "Monnaie non prise en charge."}, status=400)
	next_url = request.POST.get("next", reverse("recharge-page"))
	if not url_has_allowed_host_and_scheme(
		next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
	):
		next_url = reverse("recharge-page")
	return redirect(next_url)


def tariffs_page(request):
    models = (
        AIModel.objects.filter(is_active=True, provider__is_active=True)
        .select_related("provider")
        .order_by("name", "model_id")
    )
    tariffs = [
        {
            "name": model.name,
            "model_id": model.model_id,
            "input_price": model.input_price_per_million,
            "output_price": model.output_price_per_million,
        }
        for model in models
        if not is_local_provider(model.provider)
    ]
    context = {
        "tariffs": tariffs,
        "model_count": len(tariffs),
        "margin_multiplier": MARGIN_MULTIPLIER,
    }
    return render(request, "tariffs.html", context)


@login_required(login_url="/auth/?next=/recharge/")
def create_recharge_checkout(request):
	if request.method != "POST":
		return JsonResponse({"error": "Methode non autorisee."}, status=405)
	if not settings.STRIPE_SECRET_KEY:
		return JsonResponse({"error": "Stripe n'est pas configure."}, status=503)
	currency = selected_currency(request)
	if currency.provider != "stripe" or request.POST.get("currency", currency.code) != currency.code:
		return JsonResponse({"error": "La monnaie de paiement a changé. Recharge la page."}, status=400)
	customer, customer_error = _payment_customer_details(request)
	if customer_error:
		return JsonResponse({"error": customer_error}, status=400)

	try:
		amount = Decimal(request.POST.get("amount", ""))
		amount_cents = int((amount * 100).to_integral_value(rounding=ROUND_DOWN))
	except (ArithmeticError, TypeError, ValueError):
		return JsonResponse({"error": "Montant invalide."}, status=400)
	normalized_amount = Decimal(amount_cents) / 100
	if amount != normalized_amount:
		return JsonResponse({"error": "Le montant doit avoir au plus deux décimales."}, status=400)
	minimum_amount = amount_for_usd(currency, 5)
	if not amount.is_finite() or amount < minimum_amount:
		return JsonResponse({
			"error": f"Le montant Stripe minimum est de {format_amount(minimum_amount, currency.decimals)} {currency.code}."
		}, status=400)
	plan = {
		"amount_cents": amount_cents,
		"credits": credits_for_amount(currency, normalized_amount, CREDITS_PER_DOLLAR),
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
					"currency": currency.code.lower(),
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
				"currency": currency.code,
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

    currency = selected_currency(request)
    if currency.code != "XOF" or request.POST.get("currency", "XOF") != "XOF":
        return JsonResponse({"error": "La monnaie de paiement a changé. Recharge la page."}, status=400)

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
            "callback_url": _public_absolute_uri(request, reverse("paydunya-webhook")),
            "return_url": _public_absolute_uri(request, reverse("recharge-success")),
            "cancel_url": _public_absolute_uri(request, reverse("recharge-page")),
        },
    }
    try:
        response = requests.post(
            _paydunya_checkout_url(),
            json=payload,
            headers=_paydunya_headers(),
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
    request.session["paydunya_checkout_token"] = token
    return redirect(payment_url)


@login_required(login_url="/auth/?next=/recharge/")
def recharge_success(request):
    token = request.GET.get("token", "")
    legacy_return = request.GET.get("paydunya", "")
    # Les anciennes factures contiennent déjà une querystring dans return_url.
    if not token and legacy_return.startswith("1?token="):
        token = legacy_return.removeprefix("1?token=")
    paydunya_return = bool(token) or legacy_return == "1"
    context = {"paydunya_return": paydunya_return}
    if paydunya_return:
        token = token or request.session.get("paydunya_checkout_token", "")
        if token:
            purchase = get_object_or_404(
                RechargePurchase,
                user=request.user,
                method=RechargePurchase.Method.PAYDUNYA,
                paydunya_token=token,
            )
            try:
                purchase = _confirm_paydunya_purchase(purchase)
            except PayDunyaError as error:
                logger.warning("Confirmation PayDunya de la recharge %s : %s", purchase.pk, error)
                # Une notification peut avoir crédité le compte pendant la vérification.
                purchase.refresh_from_db()
                context["payment_verification_error"] = True
            context["payment_status"] = purchase.status
            if purchase.status == RechargePurchase.Status.PAID:
                request.user.credit_account.refresh_from_db()
                context["credited_credits_display"] = f"{purchase.credits:,}".replace(",", " ")
                context["balance_display"] = f"{request.user.credit_account.balance:,}".replace(",", " ")
    return render(request, "recharge_success.html", context)

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

    try:
        _apply_paydunya_invoice(data)
    except PayDunyaError as error:
        return HttpResponse(str(error), status=error.status_code)
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
		) else reverse("chat:chat-home"))

import secrets

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def google_login(request):
    if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
        return HttpResponseServerError("La connexion Google n'est pas configurée.")
    state = secrets.token_urlsafe(24)
    request.session["google_oauth_state"] = state
    next_url = request.GET.get("next", "/")
    request.session["google_oauth_next"] = next_url

    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": _google_redirect_uri(request),
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
            "redirect_uri": _google_redirect_uri(request),
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
