import copy
import math
import random
import subprocess
import sys
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from . import retrieval
from .models import WorkFile, Workspace


class CosineTests(SimpleTestCase):
    def test_python_fallback_handles_zero_mismatched_and_opposite_vectors(self):
        with patch.object(retrieval, '_native_cosine', None):
            for a, b, expected in [([], [], 0), ([1], [], 0), ([0, 0], [1, 2], 0),
                                   ([1, 0], [0, 1], 0), ([1, 2], [-1, -2], -1),
                                   ([True, 0], [1.0, False], 1)]:
                with self.subTest(a=a, b=b):
                    self.assertAlmostEqual(retrieval.cosine(a, b), expected)

    @skipUnless(retrieval._native_cosine is not None, 'Optional C module is not built')
    def test_native_matches_python_across_provider_vector_sizes(self):
        rng = random.Random(2026)
        pairs = [([], []), ([1], []), ([0, 0], [1, 2]), ([1, 2], [-1, -2]),
                 ([True, 0], (1.0, False))]
        for size in (1, 2, 128, 1536, 8192):
            for _ in range(5):
                pairs.append(([rng.uniform(-1, 1) for _ in range(size)],
                              [rng.uniform(-1, 1) for _ in range(size)]))
        for a, b in pairs:
            with self.subTest(dimensions=len(a)):
                self.assertTrue(math.isclose(retrieval.cosine(a, b), retrieval.python_cosine(a, b),
                                             abs_tol=1e-12, rel_tol=1e-12))

    @skipUnless(retrieval._native_cosine is not None, 'Optional C module is not built')
    def test_native_rejects_invalid_coordinates_without_crashing(self):
        for value in (None, '1', object(), [1]):
            with self.subTest(value=value), self.assertRaises(TypeError):
                retrieval.cosine([1.0], [value])
        with self.assertRaises(OverflowError):
            retrieval.cosine([1], [10 ** 10000])

    def test_import_still_works_when_native_extension_is_absent(self):
        subprocess.run([sys.executable, '-c',
                        'import sys; sys.modules["bara._retrieval_native"] = None; '
                        'from bara import retrieval; '
                        'assert retrieval._native_cosine is None; '
                        'assert retrieval.cosine([1, 0], [1, 0]) == 1'], check=True)


@override_settings(EMBEDDING_MODEL='retrieval-test', EMBEDDING_API_KEY='offline-test')
class RetrievalTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('retrieval-user')
        self.workspace = Workspace.objects.create(user=self.user)

    def document(self, text, workspace=None):
        return WorkFile.objects.create(workspace=workspace or self.workspace,
                                       original_name='document.txt', extracted_text=text)

    def test_indexed_terms_keep_accents_unicode_locations_and_stable_order(self):
        first = self.document('[Page 2]\nÉlectricité, ÉNERGIE et économie. 中文资料测试')
        self.document(first.extracted_text)
        other = Workspace.objects.create(user=self.user)
        self.document(first.extracted_text, workspace=other)
        with patch.object(retrieval, 'embeddings') as provider:
            result = retrieval.retrieve(self.workspace, 'électricité energie', limit=1)
            provider.assert_not_called()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['file_id'], first.pk)
        self.assertEqual(result[0]['score'], 1)
        self.assertEqual(result[0]['page'], 2)
        self.assertEqual(result[0]['text'], first.extracted_text)
        self.assertEqual((result[0]['start'], result[0]['end']), (0, len(first.extracted_text)))
        self.assertNotIn('terms', result[0])
        self.assertTrue(retrieval.retrieve(self.workspace, '中文资料测试'))
        # Warm queries only normalize the query, never the stored passages.
        with patch.object(retrieval, 'terms', wraps=retrieval.terms) as normalize:
            retrieval.retrieve(self.workspace, 'électricité energie')
            self.assertEqual(normalize.call_count, 1)

    def test_legacy_index_upgrades_once_without_losing_vectors_or_reembedding(self):
        file = self.document('Une automobile électrique.')
        with patch.object(retrieval, 'embeddings', return_value=[[1.0, 0.0]]):
            index = retrieval.index_file(file, semantic=True)
        legacy = copy.deepcopy(index)
        legacy.pop('version')
        for row in legacy['chunks']:
            row.pop('terms')
        file.retrieval_index = legacy
        file.save(update_fields=['retrieval_index'])
        with patch.object(retrieval, 'embeddings') as provider, self.assertNumQueries(1):
            upgraded = retrieval.index_file(file, semantic=True)
            provider.assert_not_called()
        self.assertEqual(upgraded['chunks'][0]['vector'], [1.0, 0.0])
        self.assertEqual(upgraded['model'], 'retrieval-test')
        self.assertEqual(upgraded['chunks'][0]['terms'], ['automobile', 'electrique'])
        self.assertIs(file.retrieval_index, upgraded)
        with self.assertNumQueries(0):
            self.assertIs(retrieval.index_file(file), upgraded)
        file.refresh_from_db()
        self.assertEqual(file.retrieval_index, upgraded)

    def test_content_edit_invalidates_terms_and_vectors(self):
        file = self.document('Une automobile électrique.')
        with patch.object(retrieval, 'embeddings', return_value=[[1.0, 0.0]]):
            retrieval.index_file(file, semantic=True)
        file.extracted_text = 'Un bateau solaire.'
        file.save(update_fields=['extracted_text'])
        with patch.object(retrieval, 'embeddings') as provider:
            index = retrieval.index_file(file)
            provider.assert_not_called()
        self.assertNotIn('vector', index['chunks'][0])
        self.assertNotIn('automobile', index['chunks'][0]['terms'])
        self.assertEqual(retrieval.retrieve(self.workspace, 'automobile'), [])
        self.assertTrue(retrieval.retrieve(self.workspace, 'bateau'))

    def test_native_and_python_preserve_semantic_ranking_and_negative_scores(self):
        self.workspace.semantic_search = True
        for text, vector in [('Une automobile.', [1.0, 0.0]),
                             ('Un train.', [0.5, 0.5]), ('Un bateau.', [-1.0, 0.0])]:
            with patch.object(retrieval, 'embeddings', return_value=[vector]):
                retrieval.index_file(self.document(text), semantic=True)
        with patch.object(retrieval, 'embeddings', return_value=[[1.0, 0.0]]):
            native = retrieval.retrieve(self.workspace, 'véhicule')
            with patch.object(retrieval, '_native_cosine', None):
                fallback = retrieval.retrieve(self.workspace, 'véhicule')
        self.assertEqual([row['text'] for row in native], ['Une automobile.', 'Un train.'])
        self.assertEqual([row['file_id'] for row in native], [row['file_id'] for row in fallback])
        for a, b in zip(native, fallback):
            self.assertAlmostEqual(a['score'], b['score'], places=12)

    def test_vectors_from_a_different_model_are_ignored(self):
        file = self.document('Une automobile.')
        with patch.object(retrieval, 'embeddings', return_value=[[1.0, 0.0]]):
            retrieval.index_file(file, semantic=True)
        self.workspace.semantic_search = True
        with override_settings(EMBEDDING_MODEL='different-model'), \
                patch.object(retrieval, 'embeddings', return_value=[[1.0, 0.0]]):
            self.assertEqual(retrieval.retrieve(self.workspace, 'véhicule'), [])

    def test_disabled_semantic_search_and_empty_queries_make_no_provider_calls(self):
        self.document('Une automobile électrique.')
        with patch.object(retrieval, 'embeddings') as provider:
            with self.assertNumQueries(0):
                self.assertEqual(retrieval.retrieve(self.workspace, 'avec pour'), [])
            self.assertTrue(retrieval.retrieve(self.workspace, 'automobile'))
            provider.assert_not_called()
