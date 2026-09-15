import json
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import AIModel


def api_home(request):
	return render(request, 'api.html', {
		'base_url': request.build_absolute_uri('/v1/'),
		'models_url': request.build_absolute_uri('/v1/models'),
	})


def _error(message, code, status):
	return JsonResponse({'error': {'message': message, 'type': code, 'param': None, 'code': code}}, status=status)


def _authorized(request):
	configured_key = getattr(settings, 'API_COMPATIBLE_KEY', '')
	if not configured_key:
		return settings.DEBUG
	return request.headers.get('Authorization', '') == f'Bearer {configured_key}'


def _check_auth(request):
	if not _authorized(request):
		return _error('Invalid API key.', 'invalid_api_key', 401)
	return None


def _json_body(request):
	try:
		body = json.loads(request.body or '{}')
	except (TypeError, ValueError):
		return None
	return body if isinstance(body, dict) else None


def models_view(request):
	auth_error = _check_auth(request)
	if auth_error:
		return auth_error
	models = AIModel.objects.filter(
		is_active=True,
		provider__is_active=True,
	).select_related('provider')
	now = int(time.time())
	return JsonResponse({
		'object': 'list',
		'data': [
			{
				'id': model.model_id,
				'object': 'model',
				'created': int(model.created_at.timestamp()) if model.created_at else now,
				'owned_by': model.provider.slug,
			}
			for model in models
		],
	})


def _find_model(model_id):
	return AIModel.objects.filter(
		model_id=model_id,
		is_active=True,
		provider__is_active=True,
	).select_related('provider').first()


def _upstream_request(model, payload):
	provider = model.provider
	key = provider.keys.filter(is_active=True).order_by('id').first()
	if not provider.base_url:
		raise ValueError('The selected provider has no base URL configured.')
	if not key:
		raise ValueError('No active API key is configured for the selected provider.')

	provider_name = f'{provider.slug} {provider.name}'.lower()
	is_anthropic = 'anthropic' in provider_name
	if is_anthropic:
		url = provider.base_url.rstrip('/') + '/v1/messages'
		upstream_payload = {
			'model': model.model_id,
			'max_tokens': payload.get('max_tokens', 1024),
			'messages': payload.get('messages', []),
		}
		if payload.get('system'):
			upstream_payload['system'] = payload['system']
		if 'temperature' in payload:
			upstream_payload['temperature'] = payload['temperature']
		headers = {'x-api-key': key.get_key(), 'anthropic-version': '2023-06-01'}
	else:
		url = provider.base_url.rstrip('/') + '/v1/chat/completions'
		upstream_payload = payload.copy()
		upstream_payload['model'] = model.model_id
		headers = {'Authorization': f'Bearer {key.get_key()}'}

	request = Request(
		url,
		data=json.dumps(upstream_payload).encode('utf-8'),
		headers={'Content-Type': 'application/json', **headers},
		method='POST',
	)
	return request, key, is_anthropic


def _openai_response(model, provider_data, is_anthropic):
	if not is_anthropic:
		return provider_data
	text = ''.join(
		block.get('text', '')
		for block in provider_data.get('content', [])
		if block.get('type') == 'text'
	)
	usage = provider_data.get('usage', {})
	return {
		'id': 'chatcmpl-' + uuid.uuid4().hex,
		'object': 'chat.completion',
		'created': int(time.time()),
		'model': model.model_id,
		'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}],
		'usage': {
			'prompt_tokens': usage.get('input_tokens', 0),
			'completion_tokens': usage.get('output_tokens', 0),
			'total_tokens': usage.get('input_tokens', 0) + usage.get('output_tokens', 0),
		},
	}


@csrf_exempt
def chat_completions(request):
	if request.method != 'POST':
		return _error('Only POST is supported for chat completions.', 'method_not_allowed', 405)
	auth_error = _check_auth(request)
	if auth_error:
		return auth_error
	payload = _json_body(request)
	if not payload or not isinstance(payload.get('messages'), list):
		return _error('The request must contain a messages array.', 'invalid_request_error', 400)
	model_id = payload.get('model')
	model = _find_model(model_id)
	if not model:
		return _error(f'Model {model_id!r} was not found or is inactive.', 'model_not_found', 404)
	if payload.get('stream'):
		return _error('Streaming is not supported yet.', 'invalid_request_error', 400)

	try:
		upstream_request, key, is_anthropic = _upstream_request(model, payload)
		with urlopen(upstream_request, timeout=120) as response:
			provider_data = json.loads(response.read().decode('utf-8'))
	except ValueError as error:
		return _error(str(error), 'configuration_error', 503)
	except HTTPError as error:
		detail = error.read().decode('utf-8', errors='replace')
		return _error(f'Provider request failed ({error.code}): {detail[:500]}', 'provider_error', 502)
	except (URLError, TimeoutError) as error:
		return _error(f'Provider request failed: {error}', 'provider_error', 502)

	key.last_used_at = timezone.now()
	key.save(update_fields=['last_used_at'])
	return JsonResponse(_openai_response(model, provider_data, is_anthropic))
