import hashlib
import math
import re
import unicodedata
import requests
from django.conf import settings
from django.core.cache import cache


def terms(text):
    normalized = ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))
    stop = {'avec','dans','des','donne','elle','est','faire','fait','leur','mais','mes','mon','nous','notre','pour','quoi','sans','son','sont','sur','tes','toi','ton','tous','tout','une','vous','votre','the','and','for','from','that','this','what','when','where','which','with'}
    return set(re.findall(r'\w{3,}', normalized)) - stop


def embeddings(texts):
    if not settings.EMBEDDING_MODEL or not settings.EMBEDDING_API_KEY:
        return []
    response = requests.post(settings.EMBEDDING_API_URL, headers={'Authorization':'Bearer '+settings.EMBEDDING_API_KEY},
                             json={'model':settings.EMBEDDING_MODEL,'input':texts}, timeout=20)
    response.raise_for_status()
    rows = sorted(response.json()['data'], key=lambda row:row['index'])
    vectors = [row['embedding'] for row in rows]
    if len(vectors) != len(texts) or any(not isinstance(v,list) or not 1 <= len(v) <= 8192 or
            any(not isinstance(n,(int,float)) or not math.isfinite(n) for n in v) for v in vectors):
        raise ValueError('Réponse embeddings invalide.')
    return vectors


def chunks(text):
    page = None
    offset = 0
    for paragraph in re.split(r'\n\s*\n',text):
        base = text.find(paragraph,offset)
        offset = base + len(paragraph)
        for local in range(0,len(paragraph),1500):
            fragment = paragraph[local:local+1800]
            markers = re.findall(r'\[Page (\d+)\]',fragment)
            if markers:
                page = int(markers[0])
            if fragment.strip():
                start = base + local
                yield {'text':fragment,'start':start,'end':start+len(fragment),'page':page}


def index_file(file, semantic=False):
    digest = hashlib.sha256(file.extracted_text.encode()).hexdigest()
    index = file.retrieval_index
    model = settings.EMBEDDING_MODEL if semantic else ''
    if index.get('hash') == digest and (not semantic or index.get('model') == model):
        return index
    rows = list(chunks(file.extracted_text))
    if semantic:
        try:
            vectors = embeddings([r['text'] for r in rows]) if rows else []
            if rows and not vectors:
                model = ''
            for row, vector in zip(rows, vectors):
                row['vector'] = vector
        except (requests.RequestException, ValueError, KeyError, TypeError):
            model = ''
    index = {'hash':digest,'model':model,'chunks':rows}
    type(file).objects.filter(pk=file.pk).update(retrieval_index=index)
    return index


def cosine(a,b):
    if len(a) != len(b):
        return 0
    divisor = math.sqrt(sum(v*v for v in a) * sum(v*v for v in b))
    return sum(x*y for x,y in zip(a,b))/divisor if divisor else 0


def retrieve(workspace, query, limit=6):
    query_terms = terms(query)
    if not query_terms:
        return []
    vector = []
    if workspace.semantic_search:
        key = 'embedding-query:' + hashlib.sha256((str(workspace.pk)+'\n'+settings.EMBEDDING_API_URL+'\n'+settings.EMBEDDING_MODEL+'\n'+query).encode()).hexdigest()
        vector = cache.get(key)
        if vector is None:
            try:
                result = embeddings([query[:2000]])
                vector = result[0] if result else []
                cache.set(key,vector,300)
            except (requests.RequestException, ValueError, KeyError, TypeError):
                vector = []
    rows = []
    for file in workspace.files.exclude(extracted_text='').order_by('id')[:300]:
        index = index_file(file, semantic=False)
        # Semantic indexes are built explicitly; request handling never indexes documents remotely.
        saved = file.retrieval_index
        if workspace.semantic_search and saved.get('hash') == index['hash'] and saved.get('model') == settings.EMBEDDING_MODEL:
            index = saved
        for chunk in index['chunks']:
            lexical = len(query_terms & terms(chunk['text'])) / max(1,len(query_terms))
            semantic = max(0,cosine(vector,chunk.get('vector',[]))) if vector and chunk.get('vector') else 0
            score = lexical + semantic
            if score:
                rows.append({'file_id':file.pk, 'name':file.original_name, 'score':score, **chunk})
    return sorted(rows,key=lambda r:(-r['score'],r['file_id'],r['start']))[:limit]
