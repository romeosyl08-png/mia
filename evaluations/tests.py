import json
import threading
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ai_user.models import CreditAccount, InsufficientCreditsError
from api.models import AIModel, APIProvider
from chat.billing import credits_for_usage

from . import analytics, jobs, rewards, services
from .models import ComparisonResponse, ComparisonSession, EvaluationBudget, EvaluationReward, EvaluationVote, ModelPerformance
from .selection import available_models, quote, select_pair


@override_settings(COMPARISON_ENABLED=True, COMPARISON_AUTOMATIC_ENABLED=True,
    COMPARISON_PROVIDER_ALLOWLIST=['openrouter', 'second'], COMPARISON_RATE_LIMIT=100,
    COMPARISON_USER_DAILY_CREDITS=100000, COMPARISON_GLOBAL_DAILY_CREDITS=1000000,
    EVALUATION_MIN_READING_SECONDS=0, EVALUATION_REWARDS_ENABLED=False,
    EVALUATION_ROUTING_ENABLED=False)
class ComparisonTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user('comparison-user', date_joined=timezone.now() - timedelta(days=30))
        self.other = get_user_model().objects.create_user('other-user')
        self.admin = get_user_model().objects.create_superuser('evaluation-admin', 'admin@example.test', 'test')
        self.provider = APIProvider.objects.create(name='OpenRouter', slug='openrouter', base_url='https://provider.example.test/api')
        self.provider.keys.create(name='test', key='never-use-a-real-key')
        self.models = [AIModel.objects.create(provider=self.provider, model_id=f'publisher{index}/model-{index}',
            name=f'Modèle secret {index}', input_price_per_million=1, output_price_per_million=2)
            for index in range(3)]
        self.client.force_login(self.user)
        self.initial = self.balance()

    def balance(self):
        return CreditAccount.objects.get(user=self.user).balance

    def payload(self, **changes):
        return {'prompt': 'Explique les nombres premiers.', 'selection_mode': 'manual', 'category': 'general',
                'request_key': str(uuid.uuid4()), 'model_ids': [m.pk for m in self.models[:2]], **changes}

    def create(self, **changes):
        return services.create_comparison(self.user, self.payload(**changes))[0]

    def run_job(self, session, side_effect=None):
        with patch('evaluations.jobs.call_llm_provider', return_value=('Une réponse utile.', 20, 10), side_effect=side_effect) as upstream:
            jobs.execute(jobs.claim(session.pk))
        session.refresh_from_db()
        return upstream

    def completed(self, **changes):
        session = self.create(**changes)
        self.run_job(session)
        return session

    def vote_payload(self, session, **changes):
        return {'preferred_response': 'A', 'accuracy_score': 4, 'relevance_score': 5, 'clarity_score': 4,
                'justification': f'La réponse explique les facteurs avec un exemple vérifiable pour le cas {session.pk}.', **changes}

    def evaluate(self, session, **changes):
        return services.vote(self.user, session.pk, self.vote_payload(session, **changes))[0]

    def test_manual_reserves_two_calls_and_uses_shared_pricing(self):
        session = self.create()
        self.assertEqual(session.responses.count(), 2)
        self.assertEqual(set(session.responses.values_list('model_id', flat=True)), {m.pk for m in self.models[:2]})
        reserve = sum(quote(session.prompt, m, session.max_output_tokens)[0] for m in self.models[:2])
        self.assertEqual(session.reserved_credits, reserve)
        self.assertEqual(self.balance(), self.initial - reserve)
        self.assertEqual(self.user.credit_account.transactions.filter(amount__lt=0).count(), 1)

    def test_automatic_selection_records_probability_and_distinct_models(self):
        session = self.create(selection_mode='automatic')
        self.assertEqual(session.responses.count(), 2)
        self.assertGreater(session.selection_metadata['pair_probability'], 0)
        self.assertLessEqual(session.selection_metadata['pair_probability'], 1)
        self.assertIn('candidate_ids', session.selection_metadata)
        self.assertLessEqual(session.reserved_credits, 150)
        probabilities = session.selection_metadata['probabilities']
        self.assertAlmostEqual(sum(probabilities), 1.)
        self.assertTrue(all(p >= session.selection_metadata['epsilon'] / len(probabilities) for p in probabilities))

    def test_same_model_and_provider_alias_refused(self):
        with self.assertRaises(ValueError):
            self.create(model_ids=[self.models[0].pk] * 2)
        provider = APIProvider.objects.create(name='Alias', slug='second', base_url='https://alias.example.test')
        provider.keys.create(name='test', key='test')
        alias = AIModel.objects.create(provider=provider, model_id=self.models[0].model_id, name='Alias')
        with self.assertRaises(ValueError):
            self.create(model_ids=[self.models[0].pk, alias.pk])
        self.assertEqual(self.balance(), self.initial)

    def test_disabled_model_provider_and_missing_key_refused(self):
        for target in (self.models[0], self.provider, self.provider.keys.first()):
            with self.subTest(target=type(target).__name__):
                target.is_active = False; target.save()
                with self.assertRaises(ValueError):
                    self.create()
                target.is_active = True; target.save()
        self.assertEqual(ComparisonSession.objects.count(), 0)

    @override_settings(COMPARISON_PROVIDER_ALLOWLIST=[])
    def test_provider_contract_allowlist_fails_closed(self):
        self.assertEqual(available_models(), [])
        with self.assertRaises(ValueError): self.create()

    @override_settings(COMPARISON_MODEL_ALLOWLIST=['publisher0/model-0'])
    def test_model_allowlist_restricts_catalog(self):
        self.assertEqual([m.pk for m in available_models()], [self.models[0].pk])

    def test_nontext_model_refused(self):
        self.models[0].input_modalities = ['image']; self.models[0].save()
        with self.assertRaises(ValueError): self.create()

    def test_insufficient_balance_rolls_back_everything(self):
        CreditAccount.objects.filter(user=self.user).update(balance=0)
        with self.assertRaises(InsufficientCreditsError): self.create()
        self.assertEqual(ComparisonSession.objects.count(), 0)
        self.assertEqual(ComparisonResponse.objects.count(), 0)
        self.assertEqual(EvaluationBudget.objects.get(pk=1).comparison_reserved, 0)

    def test_actual_settlement_and_finish_are_idempotent(self):
        session = self.completed()
        expected = sum(credits_for_usage(m, 20, 10) for m in self.models[:2])
        self.assertEqual(self.balance(), self.initial - expected)
        self.assertTrue(session.settled)
        self.assertEqual(session.status, 'completed')
        count = self.user.credit_account.transactions.count()
        jobs.finish(session.pk); jobs.finish(session.pk)
        self.assertEqual(self.user.credit_account.transactions.count(), count)
        self.assertIsNone(jobs.claim(session.pk))

    @override_settings(PAYDUNYA_XOF_PER_USD=Decimal('600'), EUR_PER_USD=Decimal('0.92'))
    def test_displayed_cost_is_the_actual_debit_in_jetons_for_every_payment_currency(self):
        session = self.completed()
        # A provider estimate is deliberately different from the one-credit debit.
        session.responses.update(provider_cost=Decimal('10'))
        before = self.balance()
        for currency in ('XOF', 'EUR', 'USD'):
            with self.subTest(currency=currency):
                saved = self.client.session
                saved['display_currency'] = currency
                saved.save()
                data = self.client.get(reverse('evaluations:detail', args=[session.pk])).json()
                for row in data['responses']:
                    self.assertEqual(row['credits_charged'], 1)
                    self.assertEqual(row['charged_amount_display'], '1 jeton')
                models = self.client.get(reverse('evaluations:models')).json()['models']
                for row in models:
                    credits = row['estimated_credits']
                    self.assertEqual(row['estimated_amount_display'], f'{credits:,} jeton{"s" if credits != 1 else ""}'.replace(',', ' '))
                self.assertEqual(self.client.session['display_currency'], currency)
        self.assertEqual(self.balance(), before)

    def test_single_failure_refunds_only_failed_response_and_cannot_vote(self):
        def upstream(*args, **kwargs):
            if args[2] == self.models[0].model_id: raise RuntimeError('secret upstream detail')
            return 'Une bonne réponse', 20, 10
        session = self.create(); self.run_job(session, upstream)
        self.assertEqual(session.status, 'failed')
        self.assertEqual(session.responses.filter(status='completed').count(), 1)
        self.assertEqual(self.balance(), self.initial - credits_for_usage(self.models[1], 20, 10))
        with self.assertRaises(services.Conflict): self.evaluate(session)
        self.assertNotIn('secret upstream detail', self.client.get(reverse('evaluations:detail', args=[session.pk])).content.decode())

    def test_double_failure_refunds_everything(self):
        session = self.create(); self.run_job(session, RuntimeError('failed'))
        self.assertEqual(self.balance(), self.initial)
        self.assertEqual(session.status, 'failed')
        self.assertEqual(session.responses.filter(status='completed').count(), 0)

    def test_provider_empty_output_is_failure(self):
        session = self.create()
        with patch('evaluations.jobs.call_llm_provider', return_value=('', 20, 10)):
            jobs.execute(jobs.claim(session.pk))
        session.refresh_from_db()
        self.assertEqual(session.status, 'failed'); self.assertEqual(self.balance(), self.initial)

    def test_missing_usage_is_successful_but_free_with_estimated_provider_cost(self):
        session = self.create()
        with patch('evaluations.jobs.call_llm_provider', return_value=('Réponse sans usage.', 0, None)):
            jobs.execute(jobs.claim(session.pk))
        self.assertEqual(self.balance(), self.initial)
        self.assertEqual(session.responses.filter(status='completed', usage_reliable=False, credits_charged=0).count(), 2)
        self.assertTrue(all(r.provider_cost > 0 for r in session.responses.all()))

    def test_actual_usage_above_reservation_is_capped(self):
        session = self.create(); reserve = session.reserved_credits
        with patch('evaluations.jobs.call_llm_provider', return_value=('Réponse.', 500000, 500000)):
            jobs.execute(jobs.claim(session.pk))
        self.assertEqual(self.balance(), self.initial - reserve)
        self.assertTrue(all(r.billing_shortfall > 0 for r in session.responses.all()))

    def test_price_change_does_not_change_reserved_tariff(self):
        session = self.create()
        AIModel.objects.all().update(input_price_per_million=999, output_price_per_million=999)
        self.run_job(session)
        self.assertEqual(self.balance(), self.initial - 2)

    def test_calls_are_simultaneous_and_share_exact_prompt(self):
        barrier = threading.Barrier(2)
        def upstream(*args, **kwargs):
            self.assertEqual(args[3], [{'role': 'user', 'content': 'Explique les nombres premiers.'}])
            self.assertEqual(kwargs['max_tokens'], 1024)
            barrier.wait(timeout=3)
            return 'Réponse parallèle.', 20, 10
        session = self.create(); self.run_job(session, upstream)
        self.assertEqual(session.status, 'completed')

    def test_model_disabled_after_queue_is_not_called(self):
        session = self.create(); self.models[0].is_active = False; self.models[0].save()
        called = self.run_job(session)
        self.assertEqual(called.call_count, 1)
        self.assertEqual(session.status, 'failed')

    def test_double_submission_and_refresh_do_not_debit_again(self):
        payload = self.payload()
        first = self.client.post(reverse('evaluations:sessions'), data=json.dumps(payload), content_type='application/json')
        second = self.client.post(reverse('evaluations:sessions'), data=json.dumps(payload), content_type='application/json')
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json()['id'], second.json()['id'])
        balance = self.balance()
        for _ in range(3): self.client.get(first.json()['state_url'])
        self.assertEqual(self.balance(), balance)
        self.assertEqual(ComparisonSession.objects.count(), 1)
        payload['prompt'] = 'Autre question'
        self.assertEqual(self.client.post(reverse('evaluations:sessions'), data=json.dumps(payload), content_type='application/json').status_code, 409)

    def test_unfinished_session_blocks_another_reservation(self):
        self.create()
        with self.assertRaises(services.Conflict): self.create()

    def test_stale_processing_is_refunded_once_and_late_result_ignored(self):
        session = self.create(); jobs.claim(session.pk)
        ComparisonSession.objects.filter(pk=session.pk).update(started_at=timezone.now() - timedelta(hours=1), created_at=timezone.now() - timedelta(hours=1))
        jobs.recover_stale(); jobs.recover_stale()
        self.assertEqual(self.balance(), self.initial)
        response = session.responses.first()
        jobs.store_result(session.pk, response.pk, {'status': 'completed', 'credits_charged': 10})
        self.assertEqual(session.responses.filter(status='completed').count(), 0)

    def test_pending_can_be_cancelled_and_refunded(self):
        session = self.create(); jobs.finish(session.pk, cancel=True)
        self.assertEqual(self.balance(), self.initial)
        self.assertIsNone(jobs.claim(session.pk))

    def test_budget_caps_reject_before_debit(self):
        for setting in ('COMPARISON_MAX_CREDITS', 'COMPARISON_AUTOMATIC_MAX_CREDITS', 'COMPARISON_USER_DAILY_CREDITS', 'COMPARISON_GLOBAL_DAILY_CREDITS'):
            with self.subTest(setting=setting), override_settings(**{setting: 1}):
                with self.assertRaises(ValueError): self.create(selection_mode='automatic')
        self.assertEqual(self.balance(), self.initial)

    @override_settings(COMPARISON_RATE_LIMIT=1)
    def test_creation_rate_limit(self):
        self.completed()
        with self.assertRaises(services.RateLimited): self.create()

    def test_vote_unique_idempotent_and_scores_validated(self):
        session = self.completed(); evaluation = self.evaluate(session)
        again, created = services.vote(self.user, session.pk, self.vote_payload(session))
        self.assertFalse(created); self.assertEqual(again.pk, evaluation.pk)
        with self.assertRaises(services.Conflict): self.evaluate(session, preferred_response='B')
        for score in (0, 6, True, '4'):
            with self.assertRaises(ValueError): self.evaluate(session, accuracy_score=score)
        self.assertEqual(EvaluationVote.objects.count(), 1)

    def test_database_constraints_reject_third_slot_or_duplicate_model(self):
        session = self.create(); original = session.responses.first()
        for slot, model in [('C', self.models[2]), ('A', self.models[2]), ('B', original.model)]:
            with self.subTest(slot=slot), self.assertRaises(IntegrityError), transaction.atomic():
                ComparisonResponse.objects.create(comparison=session, slot=slot, model=model, input_price=1, output_price=1)

    def test_authentication_ownership_and_csrf(self):
        session = self.create()
        anon = Client()
        self.assertEqual(anon.get(reverse('evaluations:sessions')).status_code, 401)
        self.assertEqual(anon.get(reverse('evaluations:index')).status_code, 302)
        self.client.force_login(self.other)
        for method, name in [('get', 'evaluations:detail'), ('delete', 'evaluations:detail'), ('post', 'evaluations:vote')]:
            response = getattr(self.client, method)(reverse(name, args=[session.pk]), **({} if method == 'get' else {'data': '{}', 'content_type': 'application/json'}))
            self.assertEqual(response.status_code, 404)
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.user)
        self.assertEqual(strict.post(reverse('evaluations:sessions'), data=json.dumps(self.payload()), content_type='application/json').status_code, 403)

    def test_blind_api_hides_identity_cost_metadata_and_known_self_identification(self):
        session = self.create(selection_mode='automatic')
        def upstream(*args, **kwargs):
            model = next(m for m in self.models if m.model_id == args[2])
            return f'Je suis {model.name}, {model.model_id} via OpenRouter.', 20, 10
        self.run_job(session, upstream)
        before = self.client.get(reverse('evaluations:detail', args=[session.pk]))
        self.assertEqual(before['Cache-Control'], 'private, no-store')
        text = before.content.decode()
        for model in session.responses.all():
            self.assertNotIn(model.model.name, text)
            self.assertNotIn(model.model.model_id, text)
        self.assertNotIn('OpenRouter', text)
        for field in ('selection_metadata', 'provider_cost', 'credits_charged', 'charged_amount_display', 'input_tokens'):
            self.assertNotIn(field, text)
        after = self.client.post(reverse('evaluations:vote', args=[session.pk]), data=json.dumps(self.vote_payload(session)), content_type='application/json')
        self.assertEqual(after.status_code, 200)
        self.assertTrue(after.json()['voted'])
        self.assertIn('model', after.json()['responses'][0])
        self.assertIn('provider_cost', after.json()['responses'][0])

    def test_optional_scores_and_tie_both_bad_choices(self):
        for choice in ('tie', 'both_bad'):
            session = self.completed()
            vote = self.evaluate(session, preferred_response=choice, accuracy_score=None, relevance_score=None, clarity_score=None, justification='')
            self.assertEqual(vote.preferred_response, choice)

    @override_settings(COMPARISON_ENABLED=False)
    def test_feature_disabled_blocks_page_create_and_claim(self):
        self.assertEqual(self.client.get(reverse('evaluations:index')).status_code, 404)
        self.assertEqual(self.client.post(reverse('evaluations:sessions'), data=json.dumps(self.payload()), content_type='application/json').status_code, 404)
        self.assertIsNone(jobs.claim())

    @override_settings(COMPARISON_AUTOMATIC_ENABLED=False)
    def test_automatic_flag_does_not_block_manual(self):
        with self.assertRaises(ValueError): self.create(selection_mode='automatic')
        self.assertEqual(self.create().selection_mode, 'manual')

    def test_ui_and_navigation_follow_feature_flags(self):
        page = self.client.get(reverse('evaluations:index'))
        self.assertContains(page, 'Comparaison IA')
        self.assertContains(page, 'analysis_consent')
        self.assertContains(self.client.get('/'), 'Comparaison IA')
        with override_settings(COMPARISON_ENABLED=False):
            self.assertNotContains(self.client.get('/'), 'Comparaison IA')

    def test_private_deletion_withdraws_consent_and_preserves_idempotency(self):
        payload = self.payload(analysis_consent=True)
        session, _ = services.create_comparison(self.user, payload); self.run_job(session); self.evaluate(session)
        analytics.rebuild()
        self.assertTrue(ModelPerformance.objects.exists())
        balance = self.balance()
        self.assertEqual(self.client.delete(reverse('evaluations:detail', args=[session.pk])).status_code, 200)
        session.refresh_from_db()
        self.assertFalse(session.analysis_consent); self.assertEqual(session.prompt, '')
        self.assertFalse(session.responses.exclude(response_text='').exists())
        self.assertFalse(ModelPerformance.objects.exists())
        services.create_comparison(self.user, payload)
        self.assertEqual(self.balance(), balance)

    @override_settings(EVALUATION_REWARDS_ENABLED=False)
    def test_rewards_disabled(self):
        self.evaluate(self.completed())
        self.assertFalse(EvaluationReward.objects.exists())

    @override_settings(EVALUATION_REWARDS_ENABLED=True, EVALUATION_GLOBAL_REWARD_BUDGET=100)
    def test_reward_requires_human_review_and_credits_once(self):
        evaluation = self.evaluate(self.completed())
        reward = evaluation.reward; balance = self.balance()
        self.assertEqual(reward.status, 'pending')
        with self.assertRaises(ValueError): rewards.credit(reward.pk, self.admin)
        rewards.approve(reward.pk, self.admin, 'Exemple vérifié, note et préférence cohérentes.')
        rewards.credit(reward.pk, self.admin); rewards.credit(reward.pk, self.admin)
        self.assertEqual(self.balance(), balance + reward.credits)
        self.assertEqual(EvaluationBudget.objects.get(pk=1).reward_credits_distributed, reward.credits)
        self.assertEqual(EvaluationReward.objects.filter(transaction__isnull=False).count(), 1)

    @override_settings(EVALUATION_REWARDS_ENABLED=True, EVALUATION_GLOBAL_REWARD_BUDGET=100, EVALUATION_DAILY_REWARD_LIMIT=1)
    def test_daily_reward_cap(self):
        for index in range(2):
            reward = self.evaluate(self.completed()).reward
            rewards.approve(reward.pk, self.admin, 'Qualité contrôlée.')
            if index == 0: rewards.credit(reward.pk, self.admin)
            else:
                with self.assertRaises(ValueError): rewards.credit(reward.pk, self.admin)

    @override_settings(EVALUATION_REWARDS_ENABLED=True, EVALUATION_GLOBAL_REWARD_BUDGET=100, EVALUATION_MONTHLY_REWARD_LIMIT=1)
    def test_monthly_reward_cap(self):
        first = self.evaluate(self.completed()).reward
        rewards.approve(first.pk, self.admin, 'Vérifié.'); rewards.credit(first.pk, self.admin)
        EvaluationReward.objects.filter(pk=first.pk).update(credited_at=timezone.now().replace(hour=0, minute=0, second=0, microsecond=0))
        second = self.evaluate(self.completed()).reward; rewards.approve(second.pk, self.admin, 'Vérifié.')
        with self.assertRaises(ValueError): rewards.credit(second.pk, self.admin)

    @override_settings(EVALUATION_REWARDS_ENABLED=True, EVALUATION_GLOBAL_REWARD_BUDGET=5)
    def test_global_reward_budget(self):
        reward = self.evaluate(self.completed()).reward
        rewards.approve(reward.pk, self.admin, 'Qualité contrôlée.')
        balance = self.balance()
        with self.assertRaises(ValueError): rewards.credit(reward.pk, self.admin)
        self.assertEqual(self.balance(), balance)

    @override_settings(EVALUATION_REWARDS_ENABLED=True)
    def test_repetitive_justification_and_new_accounts_are_not_rewarded(self):
        self.evaluate(self.completed(), justification='Même justification.')
        repeated = self.evaluate(self.completed(), justification='Même justification.')
        self.assertIn('repeated_justification', repeated.suspicious_reasons)
        self.assertEqual(repeated.reward.status, 'rejected')
        self.user.date_joined = timezone.now(); self.user.save()
        self.assertEqual(self.evaluate(self.completed()).reward.status, 'rejected')

    @override_settings(EVALUATION_REWARDS_ENABLED=True, EVALUATION_MIN_READING_SECONDS=10)
    def test_rapid_votes_are_flagged(self):
        evaluation = self.evaluate(self.completed())
        self.assertIn('rapid_vote', evaluation.suspicious_reasons)
        self.assertEqual(evaluation.reward.status, 'rejected')

    @override_settings(EVALUATION_REWARDS_ENABLED=True, EVALUATION_GLOBAL_REWARD_BUDGET=100)
    def test_bonus_consumption_uses_existing_wallet_and_refunds(self):
        reward = self.evaluate(self.completed()).reward
        rewards.approve(reward.pk, self.admin, 'Vérifié.'); rewards.credit(reward.pk, self.admin)
        account = CreditAccount.objects.get(user=self.user)
        account.debit(6, note='Chat ou Bara'); reward.refresh_from_db()
        self.assertEqual(reward.consumed_credits, 6)
        account.credit(4, reason='adjustment', note='Remboursement'); reward.refresh_from_db()
        self.assertEqual(reward.consumed_credits, 2)
        account.credit(50, reason='recharge'); reward.refresh_from_db()
        self.assertEqual(reward.consumed_credits, 2)

    def test_analysis_requires_consent_and_separates_cohorts(self):
        self.evaluate(self.completed(analysis_consent=False))
        self.assertEqual(analytics.analyze('general')['all'], {})
        self.evaluate(self.completed(selection_mode='automatic', analysis_consent=True), preferred_response='B')
        report = analytics.analyze('general')
        self.assertEqual(report['manual_unpaid'], {})
        self.assertEqual(len(report['automatic_unpaid']), 2)
        self.assertEqual(len(report['routing']), 2)
        self.assertEqual(report['automatic_paid'], {})

    def test_bradley_terry_regularization_and_user_influence(self):
        for _ in range(6): self.evaluate(self.completed(analysis_consent=True))
        report = analytics.analyze('general')['all']
        winner, loser = report[self.models[0].pk], report[self.models[1].pk]
        self.assertGreater(winner['estimated_quality'], loser['estimated_quality'])
        self.assertLess(winner['estimated_quality'], 1)
        self.assertEqual(winner['effective_samples'], 1)
        self.assertEqual(winner['unique_users'], 1)

    def test_auto_falls_back_without_data_and_flag_off(self):
        decision = {'model_id': self.models[0].model_id, 'provider_slug': self.provider.slug, 'basis': 'prior'}
        with self.assertNumQueries(0): self.assertEqual(analytics.apply_to_decision(decision, self.models, 'general', {}), decision)
        with override_settings(EVALUATION_ROUTING_ENABLED=True):
            self.assertEqual(analytics.apply_to_decision(decision, self.models, 'general', {}), decision)
            self.evaluate(self.completed(selection_mode='automatic', analysis_consent=True)); analytics.rebuild()
            self.assertEqual(analytics.apply_to_decision(decision, self.models, 'general', {}), decision)

    @override_settings(EVALUATION_ROUTING_ENABLED=True)
    def test_router_uses_only_sufficient_connected_fresh_data(self):
        decision = {'model_id': self.models[0].model_id, 'provider_slug': self.provider.slug, 'basis': 'prior'}
        for index, model in enumerate(self.models[:2]):
            ModelPerformance.objects.create(model=model, category='general', statistics={'routing': {
                'total_comparisons': 50, 'unique_users': 20, 'estimated_quality': .3 + .5 * index,
                'average_latency': 100, 'confidence': .6, 'effective_samples': 20, 'connected_models': [m.pk for m in self.models[:2]],
            }})
        result = analytics.apply_to_decision(decision, self.models, 'general', {})
        self.assertEqual(result['model_id'], self.models[1].model_id)
        self.assertEqual(result['basis'], 'evaluation_bradley_terry')
        snapshot = ModelPerformance.objects.get(model=self.models[1], category='general')
        snapshot.statistics['routing']['effective_samples'] = .5
        snapshot.save()
        self.assertEqual(analytics.apply_to_decision(decision, self.models, 'general', {}), decision)
        ModelPerformance.objects.update(updated_at=timezone.now() - timedelta(days=3))
        self.assertEqual(analytics.apply_to_decision(decision, self.models, 'general', {}), decision)

    def test_admin_dashboard_is_private_and_omits_prompts(self):
        session = self.completed(analysis_consent=True); self.evaluate(session); analytics.rebuild()
        url = reverse('admin:evaluations_dashboard')
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.admin)
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, session.prompt)
        self.assertContains(page, 'Bradley')

    def test_worker_and_purge_commands_preserve_wallet_history(self):
        session = self.create()
        with patch('evaluations.jobs.call_llm_provider', return_value=('Réponse.', 20, 10)):
            call_command('comparison_worker', once=True, verbosity=0)
        session.refresh_from_db(); self.assertTrue(session.settled)
        ComparisonSession.objects.filter(pk=session.pk).update(created_at=timezone.now() - timedelta(days=100))
        balance = self.balance(); count = self.user.credit_account.transactions.count()
        call_command('purge_evaluations', verbosity=0)
        self.assertEqual(self.balance(), balance)
        self.assertEqual(self.user.credit_account.transactions.count(), count)
        session.refresh_from_db()
        self.assertEqual(session.prompt, '')
        self.assertFalse(session.analysis_consent)

    @override_settings(EVALUATION_REWARDS_ENABLED=True, EVALUATION_GLOBAL_REWARD_BUDGET=100)
    def test_private_review_is_permission_checked_and_content_deletion_rejects_pending_reward(self):
        session = self.completed(analysis_consent=True)
        reward = self.evaluate(session).reward
        url = reverse('admin:evaluation_reward_review', args=[reward.pk])
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.admin)
        response = self.client.get(url)
        self.assertContains(response, session.prompt)
        self.assertIn('private', response['Cache-Control'])
        self.assertIn('no-store', response['Cache-Control'])
        services.erase_content(self.user, session.pk)
        reward.refresh_from_db()
        self.assertEqual(reward.status, 'rejected')
        with self.assertRaises(ValueError): rewards.approve(reward.pk, self.admin, 'Note.')
        with self.assertRaises(services.Conflict): self.evaluate(session)

    def test_automatic_dispatch_kill_switch_refunds_queued_request(self):
        session = self.create(selection_mode='automatic')
        claimed = jobs.claim(session.pk)
        with override_settings(COMPARISON_AUTOMATIC_ENABLED=False), patch('evaluations.jobs.call_llm_provider') as upstream:
            jobs.execute(claimed)
        upstream.assert_not_called()
        self.assertEqual(self.balance(), self.initial)

    @override_settings(EVALUATION_REWARDS_ENABLED=True, EVALUATION_GLOBAL_REWARD_BUDGET=100)
    def test_admin_can_approve_after_private_review_and_reward_kill_switch_blocks_payout(self):
        reward = self.evaluate(self.completed()).reward
        self.client.force_login(self.admin)
        response = self.client.post(reverse('admin:evaluation_reward_review', args=[reward.pk]),
            {'review_note': 'Démonstration et exemple vérifiés ; les notes sont cohérentes.'})
        self.assertEqual(response.status_code, 302)
        reward.refresh_from_db()
        self.assertEqual(reward.status, 'approved')
        self.assertEqual(reward.reviewed_by_id, self.admin.pk)
        with override_settings(EVALUATION_REWARDS_ENABLED=False), self.assertRaises(ValueError):
            rewards.credit(reward.pk, self.admin)

    def test_input_validation_blocks_json_arrays_invalid_keys_and_tool_requests(self):
        for payload in ([], self.payload(request_key='bad'), self.payload(analysis_consent='true'), self.payload(prompt='Génère une image de chat'), self.payload(prompt='')):
            response = self.client.post(reverse('evaluations:sessions'), data=json.dumps(payload), content_type='application/json')
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.balance(), self.initial)
