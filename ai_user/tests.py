from django.contrib.auth.models import User
from django.test import TestCase


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

		response = self.client.get("/recharge/")

		self.assertContains(response, "Wave")
		self.assertContains(response, "Orange Money")
		self.assertContains(response, "Momo")
		self.assertContains(response, "Moov Money")
		self.assertContains(response, 'data-operator="wave"')
		self.assertContains(response, 'data-operator="orange"')
		self.assertContains(response, 'data-operator="momo"')
		self.assertContains(response, 'data-operator="moov"')

	def test_mobile_money_rejects_amount_below_one_dollar(self):
		self.client.force_login(self.user)

		response = self.client.post("/recharge/mobile-money/", {
			"amount": "0.99",
			"operator": "wave",
			"phone": "+2250700000000",
		})

		self.assertEqual(response.status_code, 400)
		self.assertContains(response, "minimum est de 1 dollar")

	def test_mobile_money_rejects_unknown_operator(self):
		self.client.force_login(self.user)

		response = self.client.post("/recharge/mobile-money/", {
			"amount": "1",
			"operator": "unknown",
			"phone": "+2250700000000",
		})

		self.assertEqual(response.status_code, 400)

	def test_mobile_money_accepts_the_four_supported_operators(self):
		self.client.force_login(self.user)

		for operator in ("wave", "orange", "momo", "moov"):
			response = self.client.post("/recharge/mobile-money/", {
				"amount": "1",
				"operator": operator,
				"phone": "+2250700000000",
			})

			self.assertEqual(response.status_code, 503)
			self.assertContains(response, "prestataire Mobile Money")

# Create your tests here.
