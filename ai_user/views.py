import hashlib
import hmac
import time
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, HttpResponseForbidden
from django.utils import timezone
from django.contrib.auth.models import User
from django.http import HttpResponseBadRequest, HttpResponseServerError, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from .models import RechargePurchase, TelegramIdentity
import requests
import uuid


RECHARGE_PLANS = {
	"5": {"amount_cents": 500, "credits": 50_000},
	"10": {"amount_cents": 1_000, "credits": 110_000},
	"25": {"amount_cents": 2_500, "credits": 300_000},
}
MIN_RECHARGE_CENTS = 500
CREDITS_PER_DOLLAR = 10_000
MOBILE_MONEY_OPERATORS = {"wave", "orange", "momo", "moov"}


def _next_url(request):
	value = request.POST.get("next") or request.GET.get("next")
	if value and url_has_allowed_host_and_scheme(
		value, allowed_hosts={request.get_host()}, require_https=request.is_secure()
	):
		return value
	return reverse("chat-home")


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
	return render(request, "recharge.html", {"plans": RECHARGE_PLANS})


@login_required(login_url="/auth/?next=/recharge/")
def create_recharge_checkout(request):
	if request.method != "POST":
		return JsonResponse({"error": "Methode non autorisee."}, status=405)
	if not settings.STRIPE_SECRET_KEY:
		return JsonResponse({"error": "Stripe n'est pas configure."}, status=503)

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
			metadata={"user_id": str(request.user.id), "credits": str(plan["credits"])},
		)
	except ImportError:
		return JsonResponse({"error": "Installe le paquet stripe."}, status=503)
	return redirect(checkout_session.url)


@login_required(login_url="/auth/?next=/recharge/")
def create_mobile_money_checkout(request):
    if request.method != "POST":
        return JsonResponse({"error": "Methode non autorisee."}, status=405)
    if not settings.CINETPAY_API_KEY or not settings.CINETPAY_SITE_ID:
        return JsonResponse({"error": "CinetPay n'est pas configure."}, status=503)

    try:
        amount = Decimal(request.POST.get("amount", ""))
        amount_cents = int(amount * 100)
    except (ArithmeticError, TypeError, ValueError):
        return JsonResponse({"error": "Montant invalide."}, status=400)
    if amount_cents < 100:
        return JsonResponse({"error": "Le montant Mobile Money minimum est de 1 dollar."}, status=400)

    phone = request.POST.get("phone", "").strip()
    operator = request.POST.get("operator", "").strip()
    if not phone or operator not in MOBILE_MONEY_OPERATORS:
        return JsonResponse({"error": "Le numero et l'operateur Mobile Money sont requis."}, status=400)

    transaction_id = str(uuid.uuid4())
    credits = (amount_cents * CREDITS_PER_DOLLAR) // 100

    # CinetPay travaille en XOF (FCFA) — conversion approximative si ton montant est en USD/EUR
    # Adapte selon la devise réelle de ton amount ; exemple ici en supposant déjà du FCFA
    amount_fcfa = int(amount)

    payload = {
        "apikey": settings.CINETPAY_API_KEY,
        "site_id": settings.CINETPAY_SITE_ID,
        "transaction_id": transaction_id,
        "amount": amount_fcfa,
        "currency": "XOF",
        "description": f"Recharge de {credits:,} jetons",
        "customer_name": request.user.first_name or request.user.username,
        "customer_surname": request.user.last_name or "-",
        "customer_phone_number": phone,
        "notify_url": request.build_absolute_uri(reverse("cinetpay-webhook")),
        "return_url": request.build_absolute_uri(reverse("recharge-success")),
        "channels": "MOBILE_MONEY",
    }

    try:
        response = requests.post(
            "https://api-checkout.cinetpay.com/v2/payment",
            json=payload,
            timeout=30,
        )
        data = response.json()
    except (requests.RequestException, ValueError):
        return JsonResponse({"error": "CinetPay est temporairement indisponible."}, status=503)

    if data.get("code") != "201":
        return JsonResponse({"error": data.get("message", "Erreur CinetPay.")}, status=502)

    # Enregistre la transaction en attente, avant même la confirmation
    RechargePurchase.objects.create(
        user=request.user,
        stripe_session_id=transaction_id,  # réutilise ce champ, ou ajoute un champ dédié cinetpay_transaction_id
        amount_cents=amount_cents,
        credits=credits,
        status=RechargePurchase.Status.PENDING,
    )

    return JsonResponse({"payment_url": data["data"]["payment_url"]})

@login_required(login_url="/auth/?next=/recharge/")
def recharge_success(request):
	return render(request, "recharge_success.html")

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
def cinetpay_webhook(request):
    if request.method != "POST":
        return HttpResponse("Methode non autorisee.", status=405)

    transaction_id = request.POST.get("cpm_trans_id")
    if not transaction_id:
        return HttpResponse("Transaction manquante.", status=400)

    # Vérifie le statut réel auprès de CinetPay (ne jamais faire confiance au seul webhook)
    try:
        response = requests.post(
            "https://api-checkout.cinetpay.com/v2/payment/check",
            json={
                "apikey": settings.CINETPAY_API_KEY,
                "site_id": settings.CINETPAY_SITE_ID,
                "transaction_id": transaction_id,
            },
            timeout=30,
        )
        data = response.json()
    except (requests.RequestException, ValueError):
        return HttpResponse("Verification impossible.", status=502)

    if data.get("code") != "00" or data.get("data", {}).get("status") != "ACCEPTED":
        return HttpResponse("ok")  # paiement pas encore confirmé, CinetPay réessaiera

    with transaction.atomic():
        purchase = RechargePurchase.objects.filter(stripe_session_id=transaction_id).first()
        if purchase and purchase.status != RechargePurchase.Status.PAID:
            purchase.status = RechargePurchase.Status.PAID
            purchase.paid_at = timezone.now()
            purchase.save(update_fields=["status", "paid_at"])
            account = purchase.user.credit_account
            account.credit(purchase.credits, reason="recharge", note=f"CinetPay {transaction_id}")

    return HttpResponse("ok")


@login_required(login_url="/auth/?next=/recharge/wave/")
def wave_recharge_request(request):
    if request.method == "POST":
        try:
            amount = Decimal(request.POST.get("amount", ""))
            amount_cents = int(amount * 100)
        except (ArithmeticError, TypeError, ValueError):
            return render(request, "wave_request.html", {"error": "Montant invalide."})

        phone_number = request.POST.get("phone_number", "").strip()
        reference = request.POST.get("reference", "").strip()
        if not phone_number:
            return render(request, "wave_request.html", {"error": "Le numéro utilisé pour payer est requis."})

        credits = (amount_cents * CREDITS_PER_DOLLAR) // 100
        RechargePurchase.objects.create(
            user=request.user,
            method=RechargePurchase.Method.WAVE,
            phone_number=phone_number,
            reference=reference,
            amount_cents=amount_cents,
            credits=credits,
            status=RechargePurchase.Status.PENDING,
        )
        return render(request, "wave_request.html", {"submitted": True})

    return render(request, "wave_request.html")

@login_required
def confirm_wave_payment(request, purchase_id):
    if not request.user.is_staff:
        return HttpResponseForbidden()
    purchase = get_object_or_404(RechargePurchase, id=purchase_id, status=RechargePurchase.Status.PENDING)
    with transaction.atomic():
        purchase.status = RechargePurchase.Status.PAID
        purchase.paid_at = timezone.now()
        purchase.save(update_fields=["status", "paid_at"])
        purchase.user.credit_account.credit(purchase.credits, reason="recharge", note="Wave manuel")
    return redirect("admin:ai_user_rechargepurchase_changelist")
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

# Create your views here.
@login_required
def reject_wave_payment(request, purchase_id):
    if not request.user.is_staff:
        return HttpResponseForbidden()
    purchase = get_object_or_404(
        RechargePurchase, id=purchase_id,
        status=RechargePurchase.Status.PENDING,
        method=RechargePurchase.Method.WAVE,
    )
    if request.method == "POST":
        purchase.delete()
        return redirect("wave-pending-payments")
    return render(request, "wave_reject_confirm.html", {"purchase": purchase})

from django.core.paginator import Paginator

@login_required
def wave_pending_payments(request):
    if not request.user.is_staff:
        return HttpResponseForbidden()

    pending_qs = RechargePurchase.objects.filter(
        method=RechargePurchase.Method.WAVE,
        status=RechargePurchase.Status.PENDING,
    ).select_related("user").order_by("created_at")

    total_credits = sum(p.credits for p in pending_qs)
    total_amount_cents = sum(p.amount_cents for p in pending_qs)

    paginator = Paginator(pending_qs, 25)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "wave_pending.html", {
        "pending": page,
        "total_credits": total_credits,
        "total_amount_cents": total_amount_cents,
    })