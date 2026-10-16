import copy
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from api.models import AIModel, APIProvider
from . import services
from .billing import credits_for_usage
from .prompt_payloads import compact_json
from .team import call_llm_team, estimate_team_cost, stream_llm_team
from .team_protocol import HANDOFF_HEADER, normalize_packet, packet_limit, role_budgets


PLAN = {'steps': ['Vérifier la somme'], 'constraints': ['Conserver les unités'], 'risks': []}
PROPOSAL = {'result': ['RESULT_UNIQUE = 41 kg'], 'checks': ['Somme à vérifier'], 'risks': [], 'refs': [7]}
REVIEW = {'status': 'changes', 'fixes': ['RESULT_UNIQUE : remplacer 41 par 42 kg'], 'risks': [], 'refs': [7]}
FINAL = 'Le résultat corrigé est de 42 kg. [[sources:7]]'


class TeamProtocolTests(SimpleTestCase):
    def test_packet_keeps_exact_facts_code_indentation_and_citations_but_drops_extra_fields(self):
        data = {'result': ['Été : 0.001 kg', '    return 42\n'], 'refs': [7, True, {'url': 'ignored'}],
                'reasoning': 'Never relay this verbose extra field'}
        packet = normalize_packet('```json\n' + json.dumps(data) + '\n```', 'work', 250)
        self.assertEqual(packet['result'], data['result'])
        self.assertEqual(packet['refs'], [7])
        self.assertTrue(packet['incomplete'])
        self.assertNotIn('reasoning', packet)

    def test_bad_json_and_non_string_status_do_not_crash_or_become_false_approvals(self):
        packet = normalize_packet('{"fixes":[],"status":{"unexpected":true}}', 'review', 160)
        self.assertEqual(packet['status'], 'uncertain')
        self.assertTrue(packet['incomplete'])
        for text in ('Proposition en texte', '{"result": [', '[]', 'null', '[' * 500 + '0' + ']' * 500):
            with self.subTest(text=text[:30]):
                packet = normalize_packet(text, 'work', 250)
                self.assertIn('issue', packet)
                self.assertLessEqual(len(compact_json(packet)), packet_limit(250))

    def test_oversized_or_escaped_output_is_bounded_and_explicitly_marked(self):
        for text in ('x' * 20000, '\x00"\\\n' * 20000):
            with self.subTest(text=text[:10]):
                packet = normalize_packet(text, 'review', 48)
                self.assertTrue(packet['truncated'])
                self.assertLessEqual(len(compact_json(packet)), packet_limit(48))
                self.assertGreater(len(packet['note']), 0)

    def test_empty_results_and_invalid_nested_values_are_not_valid_solutions(self):
        for data in ({'result': []}, {'result': [{'arbitrary': 'nested'}]}, {'steps': []}):
            role = 'plan' if 'steps' in data else 'work'
            with self.subTest(data=data):
                self.assertIn('issue', normalize_packet(json.dumps(data), role, 128))

    def test_role_caps_keep_user_output_limit_and_reduce_default_intermediate_allowance(self):
        for final in (1, 20, 500, 8192):
            with self.subTest(final=final):
                budgets = role_budgets(final)
                self.assertEqual(budgets['final'], final)
                self.assertTrue(all(1 <= budget <= final for budget in budgets.values()))
        budgets = role_budgets(500)
        self.assertLess(sum(budgets.values()), 3 * 500)
        self.assertLess(budgets['work'] + 2 * budgets['review'] + budgets['final'], 4 * 500)


class TeamExchangeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('compact-team-owner')
        cls.models = []
        for index in range(3):
            provider = APIProvider.objects.create(name=f'Team {index}', slug=f'compact-team-{index}', base_url='https://example.test')
            provider.keys.create(name='fixture', key='not-a-real-key')
            cls.models.append(AIModel.objects.create(provider=provider, name=f'Team {index}', model_id=f'compact-{index}',
                                                     input_price_per_million=index * 10 + 1, output_price_per_million=index * 20 + 1))

    def setUp(self):
        self.messages = [
            {'role': 'user', 'content': 'Contexte historique exact'},
            {'role': 'assistant', 'content': 'Contexte conservé'},
            {'role': 'user', 'content': 'Calcule la somme avec les unités et la source 7'},
        ]
        self.prompt = 'Réponds en français. SOURCE_ORIGINALE_7 : preuve initiale. Préférence personnelle.'

    def handoff(self, invocation):
        content = invocation.args[3][-1]['content']
        self.assertTrue(content.startswith(HANDOFF_HEADER))
        return json.loads(content[len(HANDOFF_HEADER):])

    def test_plan_has_separate_corrections_one_writer_and_every_models_actual_tariff(self):
        original = copy.deepcopy(self.messages)
        records = []
        with patch('chat.services.call_llm_provider', side_effect=[
            (compact_json(PLAN), 11, 20), (compact_json(PROPOSAL), 21, 40),
            (compact_json(REVIEW), 31, 10), (FINAL, 41, 60),
        ]) as provider:
            reply, inputs, outputs = call_llm_team(self.models, self.messages, self.prompt,
                                                  strategy='plan_execute_verify', usage_records=records)
        self.assertEqual(reply, FINAL)
        self.assertEqual((inputs, outputs), (104, 130))
        self.assertEqual(self.messages, original)
        calls = provider.call_args_list
        self.assertEqual([call.args[2] for call in calls], ['compact-0', 'compact-1', 'compact-2', 'compact-1'])
        self.assertEqual([call.args[6] for call in calls], [125, 250, 160, 500])
        self.assertEqual(self.handoff(calls[1]), {'plan': PLAN})
        self.assertEqual(self.handoff(calls[2]), {'plan': PLAN, 'proposal': PROPOSAL})
        self.assertEqual(self.handoff(calls[3]), {'proposal': PROPOSAL, 'reviews': [REVIEW]})
        self.assertEqual(calls[3].args[3][-1]['content'].count('RESULT_UNIQUE = 41 kg'), 1)
        for call in calls:
            self.assertEqual(call.args[3][:len(original)], original)
            self.assertTrue(call.args[4].startswith(self.prompt))
        self.assertEqual([record['role'] for record in records], ['plan', 'work', 'review', 'final'])
        for record, model in zip(records, [self.models[0], self.models[1], self.models[2], self.models[1]]):
            self.assertEqual(record['credits'], credits_for_usage(model, record['prompt_tokens'], record['completion_tokens']))
        self.assertEqual(sum(record['prompt_tokens'] for record in records), inputs)
        self.assertEqual(sum(record['completion_tokens'] for record in records), outputs)

    def test_two_model_plan_reuses_planner_for_review_and_executor_for_final(self):
        with patch('chat.services.call_llm_provider', side_effect=[
            (compact_json(PLAN), 1, 1), (compact_json(PROPOSAL), 1, 1),
            (compact_json(REVIEW), 1, 1), (FINAL, 1, 1),
        ]) as provider:
            call_llm_team(self.models[:2], self.messages, strategy='plan_execute_verify')
        self.assertEqual([call.args[2] for call in provider.call_args_list], ['compact-0', 'compact-1', 'compact-0', 'compact-1'])

    def test_consensus_reviews_are_independent_and_disagreements_reach_final_with_original_context(self):
        other_review = {'status': 'uncertain', 'fixes': ['AUTRE_CORRECTION : vérifier 43 kg'], 'risks': ['Source ambiguë'], 'refs': [7]}
        with patch('chat.services.call_llm_provider', side_effect=[
            (compact_json(PROPOSAL), 10, 20), (compact_json(REVIEW), 10, 20),
            (compact_json(other_review), 10, 20), (FINAL, 10, 20),
        ]) as provider:
            reply, inputs, outputs = call_llm_team(self.models, self.messages, self.prompt)
        calls = provider.call_args_list
        self.assertEqual(reply, FINAL)
        self.assertEqual((inputs, outputs), (40, 80))
        self.assertEqual([call.args[6] for call in calls], [250, 160, 160, 500])
        self.assertEqual(self.handoff(calls[1]), {'proposal': PROPOSAL})
        self.assertEqual(self.handoff(calls[2]), {'proposal': PROPOSAL})
        self.assertEqual(self.handoff(calls[3]), {'proposal': PROPOSAL, 'reviews': [REVIEW, other_review]})
        self.assertEqual(calls[3].args[3][:-1], self.messages)
        self.assertIn('SOURCE_ORIGINALE_7', calls[3].args[4])

    def test_bad_intermediate_format_keeps_a_bounded_note_without_an_extra_repair_call(self):
        records = []
        with patch('chat.services.call_llm_provider', side_effect=[
            ('Note simple hors JSON', 10, 20), ('x' * 20000, 20, 5000), (FINAL, 30, 60),
        ]) as provider:
            self.assertEqual(call_llm_team(self.models[:2], self.messages, usage_records=records)[0], FINAL)
        self.assertEqual(provider.call_count, 3)
        handoff = self.handoff(provider.call_args)
        self.assertEqual(handoff['proposal']['issue'], 'unstructured')
        self.assertTrue(handoff['reviews'][0]['truncated'])
        self.assertLessEqual(len(compact_json(handoff['reviews'][0])), packet_limit(160))
        self.assertEqual(records[1]['completion_tokens'], 5000)
        self.assertEqual(records[1]['credits'], credits_for_usage(self.models[1], 20, 5000))

    def test_failed_member_fallback_still_writes_a_final_answer_instead_of_leaking_json(self):
        records = []
        with patch('chat.services.call_llm_provider', side_effect=[
            services.ProviderError('first member down'), (compact_json(PROPOSAL), 3, 4), (FINAL, 5, 6),
        ]) as provider:
            reply, inputs, outputs = call_llm_team(self.models[:2], self.messages, usage_records=records)
        self.assertEqual(reply, FINAL)
        self.assertEqual((inputs, outputs), (8, 10))
        self.assertEqual([call.args[2] for call in provider.call_args_list], ['compact-0', 'compact-1', 'compact-1'])
        self.assertEqual(self.handoff(provider.call_args)['reviews'], [])
        self.assertEqual([record['role'] for record in records], ['work', 'final'])

    def test_reviewer_failure_does_not_discard_successful_proposal_or_other_corrections(self):
        with patch('chat.services.call_llm_provider', side_effect=[
            (compact_json(PROPOSAL), 1, 2), services.ProviderError('review down'),
            (compact_json(REVIEW), 3, 4), (FINAL, 5, 6),
        ]) as provider:
            reply, inputs, outputs = call_llm_team(self.models, self.messages)
        self.assertEqual(reply, FINAL)
        self.assertEqual((inputs, outputs), (9, 12))
        self.assertEqual(self.handoff(provider.call_args)['reviews'], [REVIEW])

    def test_all_failed_members_and_too_few_planning_members_fail_without_final_call(self):
        with patch('chat.services.call_llm_provider', side_effect=services.ProviderError('offline')) as provider:
            with self.assertRaises(services.ProviderError):
                call_llm_team(self.models[:2], self.messages)
            self.assertEqual(provider.call_count, 2)
        with patch('chat.services.call_llm_provider') as provider:
            with self.assertRaisesMessage(services.ProviderError, 'au moins deux'):
                call_llm_team(self.models[:1], self.messages, strategy='plan_execute_verify')
            provider.assert_not_called()

    def test_single_model_is_one_direct_call_with_full_final_budget(self):
        with patch('chat.services.call_llm_provider', return_value=(FINAL, 10, 20)) as provider:
            self.assertEqual(call_llm_team(self.models[:1], self.messages, self.prompt, max_tokens=8192)[0], FINAL)
        provider.assert_called_once()
        self.assertEqual(provider.call_args.args[3], self.messages)
        self.assertEqual(provider.call_args.args[4], self.prompt)
        self.assertEqual(provider.call_args.args[6], 8192)

    def test_streaming_emits_only_final_text_and_counts_all_roles(self):
        for strategy, packets in (
            ('consensus', [PROPOSAL, REVIEW]),
            ('plan_execute_verify', [PLAN, PROPOSAL, REVIEW]),
        ):
            with self.subTest(strategy=strategy):
                records = []
                with patch('chat.services.call_llm_provider', side_effect=[(compact_json(packet), 10, 20) for packet in packets]) as internal, \
                        patch('chat.services.stream_llm_provider', return_value=iter([
                            {'type': 'delta', 'content': 'Réponse '}, {'type': 'delta', 'content': 'finale.'},
                            {'type': 'usage', 'input_tokens': 30, 'output_tokens': 40},
                        ])) as final:
                    events = list(stream_llm_team(self.models[:2], self.messages, self.prompt, strategy=strategy, usage_records=records))
                self.assertEqual(''.join(event['content'] for event in events if event['type'] == 'delta'), 'Réponse finale.')
                self.assertEqual(events[-1], {'type': 'usage', 'input_tokens': len(packets) * 10 + 30,
                                             'output_tokens': len(packets) * 20 + 40})
                self.assertEqual(internal.call_count, len(packets))
                final.assert_called_once()
                self.assertEqual(final.call_args.args[6], 500)
                self.assertEqual(len(records), len(packets) + 1)
                self.assertEqual(records[-1]['role'], 'final')

    def test_closing_final_stream_retains_usage_for_received_content(self):
        records = []
        with patch('chat.services.call_llm_provider', side_effect=[
            (compact_json(PROPOSAL), 10, 20), (compact_json(REVIEW), 30, 40),
        ]), patch('chat.services.stream_llm_provider', return_value=iter([
            {'type': 'delta', 'content': 'Partie finale reçue'}, {'type': 'delta', 'content': ' jamais reçue'},
        ])):
            stream = stream_llm_team(self.models[:2], self.messages, usage_records=records)
            self.assertEqual(next(stream)['content'], 'Partie finale reçue')
            stream.close()
        self.assertEqual(len(records), 3)
        self.assertGreater(records[-1]['prompt_tokens'], 0)
        self.assertEqual(records[-1]['completion_tokens'], round(len('Partie finale reçue') / 4))

    def test_reservation_covers_actual_stage_estimates_including_escaped_packets_and_fallback(self):
        for strategy, team in (('consensus', self.models), ('plan_execute_verify', self.models),
                               ('plan_execute_verify', self.models[:2])):
            with self.subTest(strategy=strategy, models=len(team)):
                escaped = {'result': ['"\\\n' * 200], 'refs': [7]}
                packets = [PLAN, escaped, REVIEW] if strategy == 'plan_execute_verify' else [escaped, REVIEW, REVIEW]
                replies = iter([compact_json(packet) for packet in packets] + [FINAL])
                actual = []

                def provider(provider, key, model_id, messages, prompt, temperature, max_tokens, top_p):
                    model = next(model for model in team if model.model_id == model_id)
                    actual.append(services.estimate_cost('', model, prompt, messages, max_tokens)[0])
                    return next(replies), 10, 20

                reserve = estimate_team_cost(team, '', self.prompt, self.messages, 500, strategy)
                with patch('chat.services.call_llm_provider', side_effect=provider):
                    call_llm_team(team, self.messages, self.prompt, strategy=strategy)
                self.assertGreaterEqual(reserve, sum(actual))

        def fallback(provider, key, model_id, messages, prompt, temperature, max_tokens, top_p):
            if model_id == self.models[0].model_id:
                raise services.ProviderError('offline')
            actual.append(services.estimate_cost('', self.models[1], prompt, messages, max_tokens)[0])
            return compact_json(PROPOSAL) if max_tokens < 500 else FINAL, 10, 20

        actual = []
        reserve = estimate_team_cost(self.models[:2], '', self.prompt, self.messages, 500, 'consensus')
        with patch('chat.services.call_llm_provider', side_effect=fallback):
            call_llm_team(self.models[:2], self.messages, self.prompt)
        self.assertGreaterEqual(reserve, sum(actual))
