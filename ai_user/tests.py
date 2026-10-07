from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from api.models import AIModel, APIProvider
from .models import RechargePurchase


class RechargeTests(TestCase):
	def setUp(self):
		self.user = User.objects.create_user(
			username="recharge-user",
			password="test-password",
		)

	def test_recharge_page_requires_login(self):
		response = self.client.get("/recharge/")

		self.assertRedirects(response, "/auth/?next=/recharge/")

	def test_recharge_page_lists_all_mobile_money_operators(self):
		self.client.force_login(self.user)
		self.user.credit_account.balance = 12345
		self.user.credit_account.save(update_fields=["balance"])

		with override_settings(CINETPAY_XOF_PER_USD=Decimal("600")):
			response = self.client.get("/recharge/")

		self.assertContains(response, "Wave")
		self.assertContains(response, "Orange Money")
		self.assertContains(response, "Momo")
		self.assertContains(response, "Moov Money")
		self.assertContains(response, "12 345")
		self.assertContains(response, "FCFA")
		self.assertContains(response, "600 FCFA =")
		self.assertContains(response, "10 000 jetons")
		self.assertContains(response, "Voir les tarifs de toutes les IA")
		self.assertContains(response, 'data-operator="wave"')
		self.assertContains(response, 'data-operator="orange"')
		self.assertContains(response, 'data-operator="momo"')
		self.assertContains(response, 'data-operator="moov"')

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

	def test_mobile_money_rejects_amount_below_configured_fcfa_minimum(self):
		self.client.force_login(self.user)

		with override_settings(
			CINETPAY_API_KEY="test-cinetpay-key",
			CINETPAY_SITE_ID="test-cinetpay-site",
			CINETPAY_XOF_PER_USD=Decimal("600"),
		):
			response = self.client.post("/recharge/mobile-money/", {
				"amount": "599",
				"operator": "wave",
				"phone": "+2250700000000",
			})

		self.assertEqual(response.status_code, 400)
		self.assertContains(response, "minimum est de 600 FCFA", status_code=400)

	def test_mobile_money_rejects_unknown_operator(self):
		self.client.force_login(self.user)

		with override_settings(
			CINETPAY_API_KEY="test-cinetpay-key",
			CINETPAY_SITE_ID="test-cinetpay-site",
			CINETPAY_XOF_PER_USD=Decimal("600"),
		):
			response = self.client.post("/recharge/mobile-money/", {
				"amount": "600",
				"operator": "unknown",
				"phone": "+2250700000000",
			})

		self.assertEqual(response.status_code, 400)

	def test_mobile_money_requires_an_exchange_rate(self):
		self.client.force_login(self.user)

		with override_settings(
			CINETPAY_API_KEY="test-cinetpay-key",
			CINETPAY_SITE_ID="test-cinetpay-site",
			CINETPAY_XOF_PER_USD=Decimal("0"),
		):
			response = self.client.post("/recharge/mobile-money/", {
				"amount": "600",
				"operator": "wave",
				"phone": "+2250700000000",
			})

		self.assertEqual(response.status_code, 503)
		self.assertContains(response, "taux de conversion FCFA/USD", status_code=503)

	def test_mobile_money_converts_fcfa_to_credits_and_starts_cinetpay_checkout(self):
		self.client.force_login(self.user)
		provider_response = MagicMock()
		provider_response.json.return_value = {
			"code": "201",
			"data": {"payment_url": "https://checkout.example.test/pay"},
		}

		with (
			override_settings(
				CINETPAY_API_KEY="test-cinetpay-key",
				CINETPAY_SITE_ID="test-cinetpay-site",
				CINETPAY_XOF_PER_USD=Decimal("600"),
			),
			patch("ai_user.views.requests.post", return_value=provider_response) as post,
		):
			response = self.client.post("/recharge/mobile-money/", {
				"amount": "1200",
				"operator": "wave",
				"phone": "+2250700000000",
			})

		self.assertEqual(response.status_code, 200)
		self.assertEqual(response.json()["payment_url"], "https://checkout.example.test/pay")
		self.assertEqual(post.call_args.kwargs["json"]["amount"], 1200)
		self.assertEqual(post.call_args.kwargs["json"]["currency"], "XOF")
		purchase = RechargePurchase.objects.get(method=RechargePurchase.Method.CINETPAY)
		self.assertEqual(purchase.credits, 20_000)
		self.assertEqual(purchase.phone_number, "+2250700000000")

	def test_mobile_money_rejects_cinetpay_response_without_payment_url(self):
		self.client.force_login(self.user)
		provider_response = MagicMock()
		provider_response.json.return_value = {"code": "201", "data": {}}

		with (
			override_settings(
				CINETPAY_API_KEY="test-cinetpay-key",
				CINETPAY_SITE_ID="test-cinetpay-site",
				CINETPAY_XOF_PER_USD=Decimal("600"),
			),
			patch("ai_user.views.requests.post", return_value=provider_response),
		):
			response = self.client.post("/recharge/mobile-money/", {
				"amount": "600",
				"operator": "wave",
				"phone": "+2250700000000",
			})

		self.assertEqual(response.status_code, 502)
		self.assertIn("lien de paiement valide", response.json()["error"])
		self.assertFalse(RechargePurchase.objects.exists())


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
