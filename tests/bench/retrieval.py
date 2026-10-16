"""Offline retrieval benchmark. Excludes SQL/JSON loading, indexing and provider latency."""
import argparse
import hashlib
from pathlib import Path
import random
import statistics
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from django.conf import settings

settings.configure(EMBEDDING_MODEL='offline-benchmark', EMBEDDING_API_URL='offline',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
from django.core.cache import cache
from bara import retrieval


class Files(list):
    def exclude(self, **kwargs):
        return self

    def order_by(self, *args):
        return self


def legacy_retrieve(workspace, query, vector):
    """Original ranking path, including repeated passage normalization."""
    query_terms = retrieval.terms(query)
    matches = []
    for file in workspace.files.exclude(extracted_text='').order_by('id')[:300]:
        index = retrieval.index_file(file)
        for chunk in index['chunks']:
            lexical = len(query_terms & retrieval.terms(chunk['text'])) / len(query_terms)
            semantic = max(0, retrieval.python_cosine(vector, chunk['vector'])) if vector else 0
            score = lexical + semantic
            if score:
                matches.append((score, file.pk, chunk['start']))
    return sorted(matches, key=lambda row: (-row[0], row[1], row[2]))[:6]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--documents', type=int, default=40)
    parser.add_argument('--passages', type=int, default=20)
    parser.add_argument('--dimensions', type=int, default=1536)
    parser.add_argument('--repeat', type=int, default=5)
    args = parser.parse_args()
    if min(args.documents, args.passages, args.dimensions, args.repeat) < 1 or args.documents > 300:
        parser.error('Use positive sizes and at most 300 documents.')
    rng = random.Random(2026)
    query = 'énergie recherche documents vitesse'
    vector = [rng.uniform(-1, 1) for _ in range(args.dimensions)]
    files = Files()
    for number in range(args.documents):
        text = '\n\n'.join((f'[Page {page + 1}] Document {number}. ' +
                             'Énergie renouvelable, documents et recherche française. ' * 22)
                            for page in range(args.passages))
        rows = [{**row, 'terms': sorted(retrieval.terms(row['text'])),
                 'vector': [rng.uniform(-1, 1) for _ in range(args.dimensions)]}
                for row in retrieval.chunks(text)]
        index = {'hash': hashlib.sha256(text.encode()).hexdigest(), 'version': retrieval.INDEX_VERSION,
                 'model': settings.EMBEDDING_MODEL, 'chunks': rows}
        files.append(SimpleNamespace(pk=number, original_name=f'{number}.txt', extracted_text=text,
                                     retrieval_index=index))
    workspace = SimpleNamespace(pk=1, files=files, semantic_search=False)
    key = 'embedding-query:' + hashlib.sha256(
        (f'1\n{settings.EMBEDDING_API_URL}\n{settings.EMBEDDING_MODEL}\n{query}').encode()).hexdigest()
    cache.set(key, vector, timeout=None)
    native = retrieval._native_cosine
    print(f'{len(files)} documents, {sum(len(f.retrieval_index["chunks"]) for f in files)} passages, '
          f'{args.dimensions} dimensions, {args.repeat} samples; C available: {native is not None}')
    print(__doc__)
    for semantic in (False, True):
        workspace.semantic_search = semantic
        reference_vector = vector if semantic else []
        expected = legacy_retrieve(workspace, query, reference_vector)
        baseline = None
        modes = [('Original Python', lambda: legacy_retrieve(workspace, query, reference_vector), None),
                 ('Indexed Python', lambda: retrieval.retrieve(workspace, query), None)]
        if native is not None:
            modes.append(('Indexed C', lambda: retrieval.retrieve(workspace, query), native))
        for label, function, backend in modes:
            with patch.object(retrieval, '_native_cosine', backend):
                result = function()  # Warm-up and verify the same top passages.
                actual = result if label == 'Original Python' else [
                    (r['score'], r['file_id'], r['start']) for r in result]
                assert [r[1:] for r in actual] == [r[1:] for r in expected]
                assert all(abs(a[0] - b[0]) < 1e-12 for a, b in zip(actual, expected))
                samples = []
                for _ in range(args.repeat):
                    start = time.perf_counter()
                    function()
                    samples.append(time.perf_counter() - start)
            elapsed = statistics.median(samples)
            baseline = elapsed if baseline is None else baseline
            print(f'{"semantic" if semantic else "keyword":8} {label:16} '
                  f'{elapsed * 1000:9.2f} ms  {baseline / elapsed:6.2f}x')


if __name__ == '__main__':
    main()
