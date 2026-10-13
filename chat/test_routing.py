"""Le routeur IA est sur le chemin critique : il doit rester borné."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from api.models import AIModel, APIProvider
from chat.models import RoutingFeedback, RoutingObservation
from chat.routing import model_key, recommend_model


class RouterCostTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("router-cost-user")
        self.provider = APIProvider.objects.create(
            name="Router provider", slug="router-provider", base_url="https://router.example.test"
        )
        self.provider.keys.create(name="router-key", key="router-secret")
        self.models = [
            AIModel.objects.create(
                provider=self.provider,
                name=f"Router model {index}",
                model_id=f"router-model-{index}",
                input_price_per_million=1,
                output_price_per_million=2,
            )
            for index in range(3)
        ]

    def _observations(self, count):
        for index in range(count):
            observation = RoutingObservation.objects.create(
                user=self.user,
                surface="chat",
                task_category="general",
                model_id=self.models[index % len(self.models)].model_id,
                provider_slug=self.provider.slug,
                outcome="success" if index % 2 else "error",
                charged_credits=10 + index,
                duration_ms=100 + index,
            )
            RoutingFeedback.objects.create(
                observation=observation,
                user=self.user,
                rating="positive" if index % 2 else "negative",
            )

    def test_recommendation_uses_a_bounded_number_of_queries(self):
        """Régression : le routeur chargeait jusqu'à 500 lignes puis filtrait en Python."""
        self._observations(40)
        costs = {model_key(model): 100 for model in self.models}
        recommend_model(self.user, self.models, "general", costs)  # échauffe les caches ORM

        with self.assertNumQueries(2):
            decision = recommend_model(self.user, self.models, "general", costs)

        self.assertEqual(decision["basis"], "user_category")
        self.assertIn(decision["model_id"], [model.model_id for model in self.models])

    def test_performance_does_not_degrade_with_the_number_of_observations(self):
        self._observations(20)
        small = recommend_model(self.user, self.models, "general", {})
        self._observations(180)
        large = recommend_model(self.user, self.models, "general", {})
        # Le score est recalculé sur plus de données, mais la requête est la même.
        self.assertEqual(small["basis"], large["basis"])
        self.assertIsInstance(large["score"], float)

    def test_prior_scope_keeps_the_current_model(self):
        decision = recommend_model(self.user, self.models, "general", {}, current_model=self.models[0])
        self.assertEqual(decision["basis"], "prior")
        self.assertEqual(decision["model_id"], self.models[0].model_id)
        self.assertEqual(decision["confidence"], 0.0)

    def test_cost_still_breaks_ties_between_equally_reliable_models(self):
        for model in self.models:
            for _ in range(6):
                RoutingObservation.objects.create(
                    user=self.user,
                    surface="chat",
                    task_category="general",
                    model_id=model.model_id,
                    provider_slug=self.provider.slug,
                    outcome="success",
                    charged_credits=10,
                    duration_ms=100,
                )
        cheap = {model_key(model): 10 * (index + 1) for index, model in enumerate(self.models)}
        decision = recommend_model(self.user, self.models, "general", cheap)
        self.assertEqual(decision["model_id"], self.models[0].model_id)
