from unittest.mock import patch
from django.test import TestCase
from . import test_tasks
from .evaluations import verify


class EvaluationTests(TestCase):
    setUp = test_tasks.DurableTaskTests.setUp
    task = test_tasks.DurableTaskTests.task
    def test_citation_numbers_must_exist_and_entailment_needs_review(self):
        task = self.task(status='completed', state={'sources':[{'link':'https://a.test'},{'link':'https://b.test'}]}, result='U=RI [[sources:1,8]]')
        self.assertFalse(verify(task,'research')['passed'])
        task.result = 'U=RI [[sources:1,2]]'
        verdict = verify(task,'research')
        self.assertTrue(verdict['passed'])
        self.assertFalse(verdict['quality_fully_verified'])

    def test_coding_requires_real_execution_evidence(self):
        task = self.task(status='completed', result='Tous les tests passent')
        with patch('bara.tools.definitions.run_command', return_value={'error':'Runtime indisponible'}):
            self.assertFalse(verify(task,'coding')['passed'])

    def test_document_benchmark_verifies_workbook_and_feeds_quality_feedback(self):
        import json
        from .evaluations import SCENARIOS, run_scenario
        from chat.models import RoutingFeedback
        args = {'filename':'bilan.xlsx','format':'xlsx','specification':{'tables':[{'rows':[['Article','Montant'],['A',120],['B',80],['Total','=SUM(B2:B3)']]}]}}
        call = {'id':'document','type':'function','function':{'name':'create_document','arguments':json.dumps(args)}}
        with patch('bara.tasks.call_llm_with_tools',side_effect=[({'tool_calls':[call]},10,10),({'content':'Bilan terminé'},10,10)]):
            report = run_scenario(self.user,self.model,SCENARIOS[2],1000)
        self.assertTrue(report['quality_fully_verified'])
        self.assertEqual(RoutingFeedback.objects.get().rating,'positive')
