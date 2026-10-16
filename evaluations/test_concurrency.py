"""Real row-lock tests. Run on an isolated PostgreSQL; SQLite skips them."""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import close_old_connections
from django.test import TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from api.models import AIModel, APIProvider

from . import jobs, rewards, services
from .models import ComparisonSession, EvaluationBudget, EvaluationReward


@skipUnlessDBFeature('has_select_for_update')
@override_settings(COMPARISON_ENABLED=True, COMPARISON_AUTOMATIC_ENABLED=True,
    COMPARISON_PROVIDER_ALLOWLIST=['openrouter'], COMPARISON_RATE_LIMIT=100,
    EVALUATION_MIN_READING_SECONDS=0, EVALUATION_REWARDS_ENABLED=True,
    EVALUATION_GLOBAL_REWARD_BUDGET=10, EVALUATION_REWARD_CREDITS=10)
class ConcurrencyTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        EvaluationBudget.objects.get_or_create(pk=1)
        self.user = get_user_model().objects.create_user('concurrent-user', date_joined=timezone.now() - timedelta(days=30))
        self.other = get_user_model().objects.create_user('concurrent-other')
        self.admin = get_user_model().objects.create_superuser('concurrent-admin', 'admin@example.test', 'test')
        provider = APIProvider.objects.create(name='Concurrent', slug='openrouter', base_url='https://fixture.example.test')
        provider.keys.create(name='fixture', key='not-real')
        self.models = [AIModel.objects.create(provider=provider, name=str(i), model_id=str(i), input_price_per_million=1, output_price_per_million=2) for i in range(2)]
        self.payload = {'request_key': str(uuid.uuid4()), 'selection_mode': 'manual', 'prompt': 'Question de test',
                        'model_ids': [m.pk for m in self.models]}

    def simultaneous(self, functions):
        barrier = threading.Barrier(len(functions))
        def run(function):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return function()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(functions)) as pool:
            futures = [pool.submit(run, f) for f in functions]
            return [f.result(timeout=20) for f in futures]

    def test_identical_concurrent_submissions_have_one_debit(self):
        results = self.simultaneous([lambda: services.create_comparison(self.user, self.payload)] * 2)
        self.assertEqual({s.pk for s, _ in results}, {ComparisonSession.objects.get().pk})
        self.assertEqual(sum(created for _, created in results), 1)
        self.assertEqual(self.user.credit_account.transactions.filter(amount__lt=0).count(), 1)

    def test_concurrent_claim_dispatches_only_once(self):
        session, _ = services.create_comparison(self.user, self.payload)
        results = self.simultaneous([lambda: jobs.claim(session.pk)] * 2)
        self.assertEqual(sum(s is not None for s in results), 1)

    def test_concurrent_refunds_are_applied_once(self):
        initial = self.user.credit_account.balance
        session, _ = services.create_comparison(self.user, self.payload)
        self.simultaneous([lambda: jobs.finish(session.pk, cancel=True)] * 2)
        self.user.credit_account.refresh_from_db()
        self.assertEqual(self.user.credit_account.balance, initial)
        self.assertEqual(self.user.credit_account.transactions.filter(amount__gt=0).count(), 1)

    @override_settings(COMPARISON_GLOBAL_DAILY_CREDITS=80)
    def test_global_budget_is_atomic_across_accounts(self):
        def create(user):
            try:
                return services.create_comparison(user, self.payload)[0].pk
            except services.RateLimited:
                return None
        results = self.simultaneous([lambda: create(self.user), lambda: create(self.other)])
        self.assertEqual(sum(pk is not None for pk in results), 1)
        self.assertEqual(ComparisonSession.objects.count(), 1)
        self.assertLessEqual(EvaluationBudget.objects.get(pk=1).comparison_reserved, 80)

    def test_concurrent_reward_credit_is_idempotent(self):
        session, _ = services.create_comparison(self.user, self.payload)
        session.responses.update(status='completed', credits_charged=1)
        jobs.finish(session.pk)
        evaluation, _ = services.vote(self.user, session.pk, {'preferred_response': 'tie', 'accuracy_score': 4,
            'relevance_score': 4, 'clarity_score': 4, 'justification': 'Résultats vérifiés, même démonstration dans les deux réponses.'})
        rewards.approve(evaluation.reward.pk, self.admin, 'Qualité vérifiée indépendamment.')
        self.user.credit_account.refresh_from_db()
        initial = self.user.credit_account.balance
        self.simultaneous([lambda: rewards.credit(evaluation.reward.pk, self.admin)] * 2)
        self.user.credit_account.refresh_from_db()
        self.assertEqual(self.user.credit_account.balance, initial + 10)
        self.assertEqual(EvaluationBudget.objects.get(pk=1).reward_credits_distributed, 10)
        self.assertEqual(EvaluationReward.objects.filter(transaction__isnull=False).count(), 1)
