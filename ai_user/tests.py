import hashlib
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
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
		self.assertContains(response, "600 FCFA =")
		self.assertContains(response, "10 000 jetons")
		self.assertContains(response, "Voir les tarifs de toutes les IA")
		self.assertContains(response, 'id="paydunya-amount" name="amount" type="number" min="200"')
		self.assertContains(response, 'data-payment-currency="XOF"')
		self.assertContains(response, 'data-payment-currency="USD"')
		self.assertContains(response, 'id="paydunyaPayment"')
		self.assertContains(response, 'id="stripePayment"')
		self.assertContains(response, 'id="paydunya-customer-name"')
		self.assertContains(response, 'id="paydunya-customer-email"')
		self.assertContains(response, 'id="paydunya-customer-phone"')
		self.assertContains(response, 'id="stripe-customer-name"')
		self.assertContains(response, 'id="stripe-customer-email"')
		self.assertContains(response, 'id="stripe-customer-phone"')
		self.assertNotContains(response, "<h3>PayDunya</h3>")
		self.assertNotContains(response, "<h3>Stripe</h3>")

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
