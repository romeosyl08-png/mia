import hashlib
from copy import deepcopy
from decimal import Decimal
from io import StringIO
from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from api.models import AIModel, APIProvider
from .models import RechargePurchase


PAYMENT_CUSTOMER = {
	"customer_name": "Ada Lovelace",
	"customer_email": "ada@example.test",
	"customer_phone": "+221 77 123 45 67",
}


class RechargeTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(
			username="recharge-user",
			password="test-password",
		)

	def test_recharge_page_requires_login(self):
		response = self.client.get("/recharge/")

		self.assertRedirects(response, "/auth/?next=/recharge/")

	def test_recharge_page_lists_paydunya_recharge(self):
		self.client.force_login(self.user)
		self.user.credit_account.balance = 12345
		self.user.credit_account.save(update_fields=["balance"])

		with override_settings(
			PAYDUNYA_MASTER_KEY="master-key",
			PAYDUNYA_PRIVATE_KEY="private-key",
			PAYDUNYA_TOKEN="token",
			PAYDUNYA_XOF_PER_USD=Decimal("600"),
		):
			response = self.client.get("/recharge/")

		self.assertContains(response, "PayDunya")
		self.assertContains(response, "12 345")
		self.assertContains(response, "FCFA")
		self.assertContains(response, "600 XOF =")
		self.assertContains(response, "10 000 jetons")
		self.assertContains(response, "Voir les tarifs de toutes les IA")
		self.assertContains(response, 'id="payment-amount" name="amount" type="number" min="200"')
		self.assertContains(response, 'id="paymentForm"')
		self.assertContains(response, 'action="/recharge/paydunya/"')
		self.assertNotContains(response, 'action="/recharge/create-checkout/"')

	def test_currency_is_detected_from_country_header_and_saved_in_session(self):
		self.client.force_login(self.user)
		with override_settings(STRIPE_SECRET_KEY="sk_test_currency"):
			response = self.client.get("/recharge/", HTTP_CF_IPCOUNTRY="FR")

		self.assertEqual(response.context["currency"].code, "EUR")
		self.assertEqual(self.client.session["display_currency"], "EUR")
		self.assertContains(response, "0,92 EUR = 10 000 jetons")
		self.assertContains(response, 'action="/recharge/create-checkout/"')
		self.assertNotContains(response, 'action="/recharge/paydunya/"')

	def test_currency_is_detected_from_public_ip_when_country_header_is_absent(self):
		self.client.force_login(self.user)
		lookup_response = MagicMock(text="US")
		lookup_response.raise_for_status.return_value = None
		with (
			override_settings(
				STRIPE_SECRET_KEY="sk_test_currency",
				IP_CURRENCY_LOOKUP_ENABLED=True,
			),
			patch("ai_user.currency.requests.get", return_value=lookup_response) as get,
		):
			response = self.client.get("/recharge/", REMOTE_ADDR="8.8.8.8")

		self.assertEqual(response.context["currency"].code, "USD")
		self.assertEqual(get.call_args.args[0], "https://ipapi.co/8.8.8.8/country/")

	def test_user_can_change_currency_and_choice_overrides_location(self):
		self.client.force_login(self.user)
		response = self.client.post("/recharge/currency/", {
			"currency": "EUR",
			"next": "/recharge/",
		})
		self.assertRedirects(response, "/recharge/")

		with override_settings(STRIPE_SECRET_KEY="sk_test_currency"):
			page = self.client.get("/recharge/", HTTP_CF_IPCOUNTRY="CI")
		self.assertEqual(page.context["currency"].code, "EUR")
		self.assertContains(page, 'name="currency" value="EUR"')
		self.assertNotContains(page, 'name="currency" value="XOF"')

	def test_stripe_checkout_uses_selected_euro_currency_and_conversion(self):
		self.client.force_login(self.user)
		session = self.client.session
		session["display_currency"] = "EUR"
		session.save()
		checkout = MagicMock(url="https://checkout.stripe.com/test-session")
		with (
			override_settings(STRIPE_SECRET_KEY="sk_test_currency", EUR_PER_USD=Decimal("0.92")),
			patch("stripe.checkout.Session.create", return_value=checkout) as create,
		):
			response = self.client.post("/recharge/create-checkout/", {
				"amount": "4.60",
				"currency": "EUR",
				**PAYMENT_CUSTOMER,
			})

		self.assertRedirects(response, checkout.url, fetch_redirect_response=False)
		price_data = create.call_args.kwargs["line_items"][0]["price_data"]
		self.assertEqual(price_data["currency"], "eur")
		self.assertEqual(price_data["unit_amount"], 460)
		self.assertEqual(create.call_args.kwargs["metadata"]["credits"], "50000")

	def test_tariffs_use_jetons_and_preserve_payment_currency(self):
		session = self.client.session
		session["display_currency"] = "EUR"
		session.save()

		response = self.client.get("/tarifs/", HTTP_CF_IPCOUNTRY="CI")

		self.assertContains(response, "Tarifs 2AI en jetons par million de tokens")
		self.assertContains(response, "Un seul solde de jetons pour Chat, Bara et Barrassons")
		for fiat in ('FCFA', 'EUR', 'USD', 'XOF', '€'):
			self.assertNotContains(response, fiat)
		self.assertEqual(self.client.session['display_currency'], 'EUR')

	def test_tariffs_page_lists_active_models_with_their_token_prices(self):
		provider = APIProvider.objects.create(name="OpenRouter")
		AIModel.objects.create(
			provider=provider,
			name="Tariff test model",
			model_id="test/tariff-model",
			input_price_per_million=Decimal("1"),
			output_price_per_million=Decimal("2"),
		)
		AIModel.objects.create(
			provider=provider,
			name="Inactive test model",
			model_id="test/inactive-model",
			is_active=False,
			input_price_per_million=Decimal("1"),
			output_price_per_million=Decimal("2"),
		)

		response = self.client.get("/tarifs/")

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "Tariff test model")
		self.assertContains(response, "test/tariff-model")
		self.assertContains(response, "15 000")
		self.assertContains(response, "30 000")
		self.assertNotContains(response, "OpenRouter")
		self.assertNotContains(response, "Fournisseur")
		self.assertNotContains(response, "Inactive test model")

	def test_paydunya_creates_a_checkout_invoice_for_card_and_local_payments(self):
		self.client.force_login(self.user)
		provider_response = MagicMock()
		provider_response.json.return_value = {
			"response_code": "00",
			"response_text": "https://app.paydunya.com/checkout/invoice/pay-token",
			"token": "pay-token",
		}

		with (
			override_settings(
				PAYDUNYA_MASTER_KEY="master-key",
				PAYDUNYA_PRIVATE_KEY="private-key",
				PAYDUNYA_TOKEN="token",
				PAYDUNYA_XOF_PER_USD=Decimal("600"),
			),
			patch("ai_user.views.requests.post", return_value=provider_response) as post,
		):
			response = self.client.post("/recharge/paydunya/", {
				"amount": "1200",
				**PAYMENT_CUSTOMER,
			})

		self.assertRedirects(
			response,
			"https://app.paydunya.com/checkout/invoice/pay-token",
			fetch_redirect_response=False,
		)
		self.assertEqual(post.call_args.kwargs["headers"]["PAYDUNYA-MASTER-KEY"], "master-key")
		self.assertEqual(post.call_args.kwargs["json"]["invoice"]["total_amount"], 1200)
		self.assertEqual(post.call_args.kwargs["json"]["custom_data"]["customer_email"], "ada@example.test")
		self.assertEqual(
			post.call_args.kwargs["json"]["actions"]["callback_url"],
			"http://testserver/recharge/paydunya-webhook/",
		)
		purchase = RechargePurchase.objects.get(method=RechargePurchase.Method.PAYDUNYA)
		self.assertEqual(purchase.paydunya_token, "pay-token")
		self.assertEqual(purchase.amount_cents, 120_000)
		self.assertEqual(purchase.credits, 20_000)

	def test_paydunya_requires_customer_details(self):
		self.client.force_login(self.user)

		with override_settings(
			PAYDUNYA_MASTER_KEY="master-key",
			PAYDUNYA_PRIVATE_KEY="private-key",
			PAYDUNYA_TOKEN="token",
			PAYDUNYA_XOF_PER_USD=Decimal("600"),
		):
			response = self.client.post("/recharge/paydunya/", {"amount": "200"})

		self.assertEqual(response.status_code, 400)
		self.assertContains(response, "nom complet est requis", status_code=400)

	def test_paydunya_rejects_amount_below_200_fcfa(self):
		self.client.force_login(self.user)

		with override_settings(
			PAYDUNYA_MASTER_KEY="master-key",
			PAYDUNYA_PRIVATE_KEY="private-key",
			PAYDUNYA_TOKEN="token",
			PAYDUNYA_XOF_PER_USD=Decimal("600"),
			PAYDUNYA_MINIMUM_XOF=200,
		):
			response = self.client.post("/recharge/paydunya/", {"amount": "199"})

		self.assertEqual(response.status_code, 400)
		self.assertContains(response, "minimum est de 200 FCFA", status_code=400)

	def test_paydunya_uses_the_sandbox_endpoint_for_test_keys(self):
		self.client.force_login(self.user)
		provider_response = MagicMock()
		provider_response.json.return_value = {
			"response_code": "00",
			"response_text": "https://app.paydunya.com/sandbox-checkout/invoice/test-pay-token",
			"token": "test-pay-token",
		}

		with (
			override_settings(
				PAYDUNYA_MASTER_KEY="master-key",
				PAYDUNYA_PRIVATE_KEY="test_private_key",
				PAYDUNYA_TOKEN="token",
				PAYDUNYA_XOF_PER_USD=Decimal("600"),
				PAYDUNYA_CHECKOUT_URL="",
			),
			patch("ai_user.views.requests.post", return_value=provider_response) as post,
		):
			response = self.client.post("/recharge/paydunya/", {
				"amount": "1200",
				**PAYMENT_CUSTOMER,
			})

		self.assertEqual(response.status_code, 302)
		self.assertEqual(
			post.call_args.args[0],
			"https://app.paydunya.com/sandbox-api/v1/checkout-invoice/create",
		)

	def test_paydunya_returns_the_provider_error_description(self):
		self.client.force_login(self.user)
		provider_response = MagicMock()
		provider_response.json.return_value = {
			"response_code": "401",
			"description": "Invalid API credentials",
		}

		with (
			override_settings(
				PAYDUNYA_MASTER_KEY="master-key",
				PAYDUNYA_PRIVATE_KEY="private-key",
				PAYDUNYA_TOKEN="token",
				PAYDUNYA_XOF_PER_USD=Decimal("600"),
			),
			patch("ai_user.views.requests.post", return_value=provider_response),
		):
			response = self.client.post("/recharge/paydunya/", {
				"amount": "1200",
				**PAYMENT_CUSTOMER,
			})

		self.assertEqual(response.status_code, 502)
		self.assertEqual(response.json()["error"], "PayDunya : Invalid API credentials")

	def test_paydunya_webhook_credits_only_a_signed_matching_completed_invoice(self):
		starting_balance = self.user.credit_account.balance
		purchase = RechargePurchase.objects.create(
			user=self.user,
			method=RechargePurchase.Method.PAYDUNYA,
			paydunya_token="pay-token",
			amount_cents=120_000,
			credits=20_000,
		)
		master_key = "master-key"
		payload = {
			"data": {
				"hash": hashlib.sha512(master_key.encode()).hexdigest(),
				"status": "completed",
				"invoice": {
					"token": "pay-token",
					"total_amount": "1200",
				},
			},
		}

		with override_settings(PAYDUNYA_MASTER_KEY=master_key):
			response = self.client.post(
				"/recharge/paydunya-webhook/",
				data=payload,
				content_type="application/json",
			)
			duplicate_response = self.client.post(
				"/recharge/paydunya-webhook/",
				data=payload,
				content_type="application/json",
			)

		purchase.refresh_from_db()
		self.user.credit_account.refresh_from_db()
		self.assertEqual(response.status_code, 200)
		self.assertEqual(duplicate_response.status_code, 200)
		self.assertEqual(purchase.status, RechargePurchase.Status.PAID)
		self.assertEqual(self.user.credit_account.balance, starting_balance + 20_000)

	def test_paydunya_webhook_rejects_invalid_hash_before_crediting(self):
		starting_balance = self.user.credit_account.balance
		RechargePurchase.objects.create(
			user=self.user,
			method=RechargePurchase.Method.PAYDUNYA,
			paydunya_token="pay-token",
			amount_cents=120_000,
			credits=20_000,
		)
		payload = {
			"data": {
				"hash": "invalid",
				"status": "completed",
				"invoice": {
					"token": "pay-token",
					"total_amount": "1200",
				},
			},
		}

		with override_settings(PAYDUNYA_MASTER_KEY="master-key"):
			response = self.client.post(
				"/recharge/paydunya-webhook/",
				data=payload,
				content_type="application/json",
			)

		self.user.credit_account.refresh_from_db()
		self.assertEqual(response.status_code, 403)
		self.assertEqual(self.user.credit_account.balance, starting_balance)

	def test_paydunya_webhook_credits_a_form_encoded_ipn_payload(self):
		starting_balance = self.user.credit_account.balance
		RechargePurchase.objects.create(
			user=self.user,
			method=RechargePurchase.Method.PAYDUNYA,
			paydunya_token="pay-token",
			amount_cents=120_000,
			credits=20_000,
		)
		master_key = "master-key"

		with override_settings(PAYDUNYA_MASTER_KEY=master_key):
			response = self.client.post("/recharge/paydunya-webhook/", {
				"data[hash]": hashlib.sha512(master_key.encode()).hexdigest(),
				"data[status]": "completed",
				"data[invoice][token]": "pay-token",
				"data[invoice][total_amount]": "1200",
			})

		self.user.credit_account.refresh_from_db()
		self.assertEqual(response.status_code, 200)
		self.assertEqual(self.user.credit_account.balance, starting_balance + 20_000)


@override_settings(
	PAYDUNYA_MASTER_KEY="master-key",
	PAYDUNYA_PRIVATE_KEY="test_private_key",
	PAYDUNYA_TOKEN="token",
	PAYDUNYA_CHECKOUT_URL="",
	PAYDUNYA_XOF_PER_USD=Decimal("600"),
	PUBLIC_BASE_URL="",
	IP_CURRENCY_LOOKUP_ENABLED=False,
)
class PayDunyaConfirmationTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(username="paydunya-return-user", password="test-password")
		self.client.force_login(self.user)
		self.starting_balance = self.user.credit_account.balance
		self.purchase = RechargePurchase.objects.create(
			user=self.user,
			method=RechargePurchase.Method.PAYDUNYA,
			paydunya_token="test_pay-token",
			amount_cents=20_000,
			credits=3_333,
		)
		self.payload = {
			"response_code": "00",
			"hash": hashlib.sha512(b"master-key").hexdigest(),
			"status": "completed",
			"mode": "test",
			"invoice": {"token": "test_pay-token", "total_amount": 200},
		}

	def provider_response(self, payload=None):
		response = MagicMock()
		response.json.return_value = self.payload if payload is None else payload
		return response

	def assert_balance(self, expected):
		self.user.credit_account.refresh_from_db()
		self.assertEqual(self.user.credit_account.balance, expected)

	def test_return_confirms_sandbox_payment_without_ipn_and_late_ipn_does_not_duplicate_credits(self):
		with patch("ai_user.paydunya.requests.get", return_value=self.provider_response()) as get:
			response = self.client.get("/recharge/success/?token=test_pay-token")
			repeated_return = self.client.get("/recharge/success/?token=test_pay-token")

		self.assertContains(response, "Recharge ajoutée")
		self.assertContains(response, "3 333 jetons ont été ajoutés")
		self.assertContains(response, f"{self.starting_balance + 3_333:,}".replace(",", " "))
		self.assertEqual(repeated_return.status_code, 200)
		get.assert_called_once()
		self.assertEqual(
			get.call_args.args[0],
			"https://app.paydunya.com/sandbox-api/v1/checkout-invoice/confirm/test_pay-token",
		)
		self.assertEqual(get.call_args.kwargs["headers"]["PAYDUNYA-PRIVATE-KEY"], "test_private_key")
		late_ipn = self.client.post(
			"/recharge/paydunya-webhook/", {"data": self.payload}, content_type="application/json",
		)
		self.assertEqual(late_ipn.status_code, 200)
		self.assert_balance(self.starting_balance + 3_333)
		self.purchase.refresh_from_db()
		self.assertEqual(self.purchase.status, RechargePurchase.Status.PAID)
		self.assertIsNotNone(self.purchase.paid_at)
		self.assertEqual(self.user.credit_account.transactions.filter(note="PayDunya test_pay-token").count(), 1)

	def test_return_after_ipn_does_not_request_provider_or_credit_again(self):
		self.client.post("/recharge/paydunya-webhook/", self.payload, content_type="application/json")
		with patch("ai_user.paydunya.requests.get") as get:
			response = self.client.get("/recharge/success/?token=test_pay-token")
		self.assertContains(response, "Recharge ajoutée")
		get.assert_not_called()
		self.assert_balance(self.starting_balance + 3_333)

	def test_confirmation_uses_live_endpoint_for_live_keys(self):
		with (
			override_settings(PAYDUNYA_PRIVATE_KEY="live_private_key"),
			patch("ai_user.paydunya.requests.get", return_value=self.provider_response()) as get,
		):
			self.client.get("/recharge/success/?token=test_pay-token")
		self.assertEqual(
			get.call_args.args[0],
			"https://app.paydunya.com/api/v1/checkout-invoice/confirm/test_pay-token",
		)

	def test_new_checkout_uses_public_callbacks_and_return_url_without_querystring(self):
		provider_response = self.provider_response({
			"response_code": "00",
			"response_text": "https://app.paydunya.com/sandbox-checkout/invoice/test_new-token",
			"token": "test_new-token",
		})
		with (
			override_settings(PUBLIC_BASE_URL="https://2ai.example"),
			patch("ai_user.views.requests.post", return_value=provider_response) as post,
		):
			response = self.client.post("/recharge/paydunya/", {"amount": "200", **PAYMENT_CUSTOMER})
		self.assertEqual(response.status_code, 302)
		self.assertEqual(post.call_args.kwargs["json"]["actions"], {
			"callback_url": "https://2ai.example/recharge/paydunya-webhook/",
			"return_url": "https://2ai.example/recharge/success/",
			"cancel_url": "https://2ai.example/recharge/",
		})
		self.assertEqual(self.client.session["paydunya_checkout_token"], "test_new-token")

	def test_legacy_return_with_second_question_mark_can_still_confirm_payment(self):
		with patch("ai_user.paydunya.requests.get", return_value=self.provider_response()):
			response = self.client.get("/recharge/success/?paydunya=1?token=test_pay-token")
		self.assertContains(response, "Recharge ajoutée")
		self.assert_balance(self.starting_balance + 3_333)

	def test_legacy_return_without_token_uses_checkout_session(self):
		session = self.client.session
		session["paydunya_checkout_token"] = self.purchase.paydunya_token
		session.save()
		with patch("ai_user.paydunya.requests.get", return_value=self.provider_response()):
			response = self.client.get("/recharge/success/?paydunya=1")
		self.assertContains(response, "Recharge ajoutée")
		self.assert_balance(self.starting_balance + 3_333)

	def test_return_rejects_another_users_invoice_before_requesting_paydunya(self):
		other_user = User.objects.create_user(username="another-payer", password="test-password")
		self.client.force_login(other_user)
		with patch("ai_user.paydunya.requests.get") as get:
			response = self.client.get("/recharge/success/?token=test_pay-token")
		self.assertEqual(response.status_code, 404)
		get.assert_not_called()
		self.assert_balance(self.starting_balance)

	def test_confirmation_rejects_invalid_hash_token_amount_and_provider_payload(self):
		payloads = [
			{**self.payload, "hash": "invalid"},
			{**self.payload, "hash": "é"},
			{**self.payload, "response_code": "401"},
			{**self.payload, "invoice": {"token": "another-token", "total_amount": 200}},
			{**self.payload, "invoice": {"token": "test_pay-token", "total_amount": "200.001"}},
			{**self.payload, "invoice": {"token": "test_pay-token", "total_amount": "NaN"}},
			[],
		]
		for payload in payloads:
			with self.subTest(payload=payload), patch("ai_user.paydunya.requests.get", return_value=self.provider_response(payload)):
				response = self.client.get("/recharge/success/?token=test_pay-token")
				self.assertContains(response, "vérification du paiement est temporairement indisponible")
				self.assertNotContains(response, "Recharge ajoutée")
				self.assert_balance(self.starting_balance)
				self.purchase.refresh_from_db()
				self.assertEqual(self.purchase.status, RechargePurchase.Status.PENDING)

	def test_pending_failed_and_cancelled_payments_do_not_credit(self):
		for status in ("pending", "failed", "cancelled", "canceled"):
			with self.subTest(status=status), patch(
				"ai_user.paydunya.requests.get", return_value=self.provider_response({**self.payload, "status": status}),
			):
				response = self.client.get("/recharge/success/?token=test_pay-token")
				self.assertContains(response, "Confirmation du paiement" if status == "pending" else "Paiement non finalisé")
				self.assert_balance(self.starting_balance)

	def test_provider_timeout_can_be_retried_without_losing_purchase(self):
		with patch("ai_user.paydunya.requests.get", side_effect=requests.Timeout):
			response = self.client.get("/recharge/success/?token=test_pay-token")
		self.assertContains(response, "Vérifier à nouveau")
		self.assert_balance(self.starting_balance)
		with patch("ai_user.paydunya.requests.get", return_value=self.provider_response()):
			response = self.client.get("/recharge/success/?token=test_pay-token")
		self.assertContains(response, "Recharge ajoutée")
		self.assert_balance(self.starting_balance + 3_333)

	def test_webhook_rejects_amount_with_extra_fraction_before_crediting(self):
		payload = deepcopy(self.payload)
		payload["invoice"]["total_amount"] = "200.001"
		response = self.client.post("/recharge/paydunya-webhook/", payload, content_type="application/json")
		self.assertEqual(response.status_code, 400)
		self.assert_balance(self.starting_balance)

	def test_reconciliation_recovers_completed_payments_once_and_keeps_pending_payments(self):
		pending = RechargePurchase.objects.create(
			user=self.user, method=RechargePurchase.Method.PAYDUNYA,
			paydunya_token="test_pending-token", amount_cents=20_000, credits=3_333,
		)
		pending_payload = deepcopy(self.payload)
		pending_payload["invoice"]["token"] = pending.paydunya_token
		pending_payload["status"] = "pending"
		output = StringIO()
		with patch("ai_user.paydunya.requests.get", side_effect=[
			self.provider_response(), self.provider_response(pending_payload), self.provider_response(pending_payload),
		]) as get:
			call_command("reconcile_paydunya", stdout=output)
			call_command("reconcile_paydunya", stdout=output)
		self.assertEqual(get.call_count, 3)
		self.assertIn("Confirmées : 1 (3333 jetons) ; en attente : 1", output.getvalue())
		self.assertIn("Confirmées : 0 (0 jetons) ; en attente : 1", output.getvalue())
		self.assert_balance(self.starting_balance + 3_333)
		pending.refresh_from_db()
		self.assertEqual(pending.status, RechargePurchase.Status.PENDING)


class APIKeyTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(
			username="api-key-user",
			password="test-password",
		)

	def test_api_key_page_creates_a_prefix_that_fits_the_database(self):
		self.client.force_login(self.user)

		response = self.client.post("/api-keys/", {"action": "create", "name": "Barrassons"})

		self.assertEqual(response.status_code, 200)
		key = self.user.api_keys.get(name="Barrassons")
		self.assertLessEqual(len(key.key_prefix), 20)
		self.assertContains(response, "Nouvelle clé créée")

	def test_barrassons_guide_requires_login(self):
		response = self.client.get("/api-keys/barrassons/")

		self.assertRedirects(response, "/auth/?next=/api-keys/barrassons/")

	def test_barrassons_guide_explains_both_api_endpoints(self):
		self.client.force_login(self.user)

		response = self.client.get("/api-keys/barrassons/")

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, "http://127.0.0.1:8000/v1")
		self.assertContains(response, "https://mia-vkl1.onrender.com/v1")
		self.assertContains(response, "barrassons logout")
		self.assertNotContains(response, "sk-ai2ai-")

# Create your tests here.
