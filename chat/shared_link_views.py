import json

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from bara.tools.definitions import fetch_url
from chat.services import check_user_rate_limit
from .shared_links import SharedLinkError, canonical_share_url


@require_POST
def preview_shared_link(request):
    def reply(data, status=200):
        response = JsonResponse(data, status=status)
        response['Cache-Control'] = 'private, no-store'
        response['Vary'] = 'Cookie'
        return response

    if not request.user.is_authenticated:
        return reply({'error': 'Connecte-toi pour lire un lien.'}, 401)
    if not check_user_rate_limit(request.user.pk, 5, 60, 'shared-link-reader')[0]:
        response = reply({'error': 'Trop de lectures. Réessaie dans une minute.'}, 429)
        response['Retry-After'] = '60'
        return response
    try:
        if len(request.body) > 5000:
            raise SharedLinkError('Requête trop volumineuse.')
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise SharedLinkError('Un objet JSON est requis.')
        url = canonical_share_url(payload.get('url'))
    except (ValueError, UnicodeDecodeError) as error:
        return reply({'error': str(error) if isinstance(error, SharedLinkError) else 'Requête invalide.'}, 400)
    try:
        result = fetch_url(None, url)
    except Exception:
        result = {'error': 'Le service de lecture est temporairement indisponible.'}
    if result.get('error') or result.get('source_type') != 'chatgpt_shared_conversation':
        return reply({'error': 'Lecture du lien impossible. Le partage peut être privé, supprimé ou bloqué. Ouvre-le puis colle son texte ci-dessous.',
                      'paste_available': True}, 422)
    return reply(result)
