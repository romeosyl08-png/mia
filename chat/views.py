import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from .models import Conversation, Message, Persona
from ai_user.models import InsufficientCreditsError

from .services import ProviderError, call_llm_provider, call_llm_team, estimate_cost, is_local_provider, stream_llm_provider, stream_llm_team
from api.models import AIModel
from .services import PRICING_TIERS 
from itertools import groupby

from bara.services import get_workspace_context,actual_cost_credits
from chat.services import check_user_rate_limit

logger = logging.getLogger(__name__)


def chat_home(request):
	if not request.user.is_authenticated:
		return redirect(f'{reverse("auth-page")}?next=/')

	credits = 0
	credit_account = getattr(request.user, "credit_account", None)
	credits = credit_account.balance if credit_account is not None else 0
	models = AIModel.objects.filter(
		is_active=True,
		provider__is_active=True,
	).select_related('provider').order_by('provider__name', 'name')
	models_by_provider = [
    {'provider_name': provider_name, 'models': list(group)}
    for provider_name, group in groupby(models, key=lambda m: m.provider.name)
	]

	chat_config = {
		'initialCredits': credits,
		'isAuthenticated': request.user.is_authenticated,
		'loginUrl': '/auth/?next=/',
		'hasActiveModels': models.exists(),
		'modelId': models.first().model_id if models.exists() else 'demo-mistral',
	}

	return render(request, 'chat.html', {
		'models_by_provider': models_by_provider,
		'ai_models': models,
		'chat_config': chat_config,
		  'initial_credits': credits,
		  'is_authenticated': request.user.is_authenticated,
	})

def dap_home(request):
	if not request.user.is_authenticated:
		return redirect(f'{reverse("auth-page")}?next=/dap/')
	return render(request, 'bara/workspace.html')

def customize(request):
    return render(
        request,
        "customize.html",
        {
            "is_authenticated": request.user.is_authenticated,
            "ai_models": AIModel.objects.filter(is_active=True),
        },
    )

def _chat_error(message, error_type, status):
	return JsonResponse({'error': {'message': message, 'type': error_type}}, status=status)


def _sse_event(data):
	return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _complete_chat_turn(user, conversation, persona, content, reply, model, account, reserve, estimated_input, provider_input, provider_output, edit_message=None):
	prompt_tokens = provider_input or estimated_input
	completion_tokens = provider_output or round(len(reply) / 4)
	actual_cost = actual_cost_credits(model, prompt_tokens, completion_tokens)
	if actual_cost < reserve:
		account.credit(reserve - actual_cost, reason='adjustment', note=f'Ajustement {model.model_id}')

	if edit_message:
		is_first_message = not conversation.messages.filter(id__lt=edit_message.id).exists()
		conversation.messages.filter(id__gte=edit_message.id).delete()
		if is_first_message:
			conversation.title = content[:200]
			conversation.save(update_fields=['title', 'updated_at'])
	if conversation is None:
		conversation = Conversation.objects.create(
			user=user,
			title=content[:200],
			persona=persona,
		)
	user_message = Message.objects.create(
			conversation=conversation,
			role='user',
			content=content,
			prompt_tokens=prompt_tokens,
	)
	assistant_message = Message.objects.create(
			conversation=conversation,
			role='assistant',
			content=reply,
			completion_tokens=completion_tokens,
	)
	return {
		'conversation_id': conversation.id,
		'title': conversation.title,
		'reply': reply,
		'prompt_tokens': prompt_tokens,
		'completion_tokens': completion_tokens,
		'remaining_balance': account.balance,
		'user_message_id': user_message.id,
		'assistant_message_id': assistant_message.id,
	}


@method_decorator(login_required(login_url='/auth/?next=/chat/api/send/'), name='dispatch')
class SendMessageView(View):
	"""Reçoit un message, appelle le fournisseur et facture le coût côté serveur."""
	def post(self, request):
		try:
			payload = json.loads(request.body or '{}')
		except (TypeError, ValueError):
			return _chat_error('JSON invalide.', 'invalid_request', 400)

		content = payload.get('content', '').strip() if isinstance(payload, dict) else ''
		if not content:
			return _chat_error('Le contenu du message est obligatoire.', 'invalid_request', 400)
		
		allowed, retry_after = check_user_rate_limit(request.user.id, max_requests=10, window_seconds=60, key_prefix="chat")
		if not allowed:
			return _chat_error(
				f"Trop de messages envoyés. Réessaie dans {retry_after} secondes.",
				"rate_limit_exceeded",
				429,
    )
		model_query = AIModel.objects.filter(is_active=True, provider__is_active=True).select_related('provider')
		model_id = payload.get('model_id')
		team_mode = bool(payload.get('team_mode'))
		team_ids = payload.get('team_model_ids') or []
		if not isinstance(team_ids, list):
			team_ids = []
		if team_mode:
			team_models = list(model_query.filter(model_id__in=team_ids[:3])) if team_ids else []
			model = team_models[0] if team_models else None
		else:
			team_models = []
			model = model_query.filter(model_id=model_id).first() if model_id else model_query.first()
		if not model:
			return _chat_error('Aucun modèle actif n’est configuré.', 'provider_unavailable', 503)

		conversation_id = payload.get('conversation_id')
		conversation = None
		if conversation_id:
			conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
		edit_message = None
		edit_message_id = payload.get('edit_message_id')
		if edit_message_id:
			if not conversation:
				return _chat_error('Une conversation est obligatoire pour modifier un message.', 'invalid_request', 400)
			try:
				edit_message_id = int(edit_message_id)
			except (TypeError, ValueError):
				return _chat_error('Identifiant de message invalide.', 'invalid_request', 400)
			edit_message = get_object_or_404(
				Message,
				id=edit_message_id,
				conversation=conversation,
				role='user',
			)
		persona_id = payload.get('persona_id')
		persona = get_object_or_404(Persona, id=persona_id, user=request.user) if persona_id else None
		if conversation and persona and conversation.persona_id != persona.id:
			conversation.persona = persona
			conversation.save(update_fields=['persona', 'updated_at'])

		system_prompt = str(payload.get('system_prompt', ''))
		messages = []
		if conversation:
			conversation_messages = conversation.messages.filter(role__in={'user', 'assistant'})
			if edit_message:
				conversation_messages = conversation_messages.filter(id__lt=edit_message.id)
			messages = list(conversation_messages.values('role', 'content'))
		messages.append({'role': 'user', 'content': content})

		
		workspace_context = get_workspace_context(None)
		full_system_prompt = (system_prompt + "\n\n" + workspace_context).strip() if workspace_context else system_prompt
		
		reserve, estimated_input = estimate_cost(content, model, full_system_prompt, messages)
		if team_mode:
			reserve *= max(1, len(team_models) + 1)
		account = request.user.credit_account
		try:
			account.debit(reserve, reason='usage', note=f'Reservation {model.model_id}')
		except InsufficientCreditsError:
			return _chat_error('Crédits insuffisants pour ce message.', 'insufficient_credits', 402)

		if 'text/event-stream' in request.headers.get('Accept', ''):
			return StreamingHttpResponse(
				self._stream_reply(
					request, conversation, persona, content, model, team_models, team_mode,
					messages, full_system_prompt, float(payload.get('temperature', 0.7)),
					reserve, estimated_input, account, edit_message,
				),
				content_type='text/event-stream',
				headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
			)

		try:
			if team_mode:
				reply, provider_input, provider_output = call_llm_team(
					team_models, messages, full_system_prompt, float(payload.get('temperature', 0.7)), 500
				)
			else:
				api_key = model.provider.keys.filter(is_active=True).order_by('id').first()
				if (not api_key and not is_local_provider(model.provider)) or not model.provider.base_url:
					account.credit(reserve, reason='adjustment', note='Remboursement modèle non configuré')
					return _chat_error('Le fournisseur IA n’est pas configuré.', 'provider_unavailable', 503)
				reply, provider_input, provider_output = call_llm_provider(
					model.provider,
					api_key.get_key() if api_key else '',
					model.model_id,
					messages,
					full_system_prompt,
					float(payload.get('temperature', 0.7)),
					500,
				)
		except ProviderError as error:
			account.credit(reserve, reason='adjustment', note='Remboursement appel IA échoué')
			return _chat_error(str(error), 'provider_error', 502)
		except Exception:
			logger.exception(
				"Unexpected provider failure for chat model %s (user_id=%s)",
				model.model_id,
				request.user.pk,
			)
			account.credit(reserve, reason='adjustment', note='Remboursement erreur fournisseur inattendue')
			return _chat_error(
				"Erreur inattendue pendant l’appel au fournisseur. Réessaie dans un instant.",
				'provider_error',
				502,
			)

		return JsonResponse(_complete_chat_turn(
			request.user, conversation, persona, content, reply, model, account, reserve,
			estimated_input, provider_input, provider_output, edit_message,
		))

	def _stream_reply(self, request, conversation, persona, content, model, team_models, team_mode, messages, system_prompt, temperature, reserve, estimated_input, account, edit_message=None):
		def events():
			reply_parts = []
			provider_input = 0
			provider_output = 0
			settled = False
			try:
				if not team_mode:
					api_key = model.provider.keys.filter(is_active=True).order_by('id').first()
					if (not api_key and not is_local_provider(model.provider)) or not model.provider.base_url:
						raise ProviderError('Le fournisseur IA n’est pas configuré.')
					provider_stream = stream_llm_provider(
						model.provider,
						api_key.get_key() if api_key else '',
						model.model_id,
						messages,
						system_prompt,
						temperature,
						500,
					)
				else:
					provider_stream = stream_llm_team(team_models, messages, system_prompt, temperature, 500)

				for event in provider_stream:
					if event['type'] == 'delta':
						reply_parts.append(event['content'])
						yield _sse_event(event)
					else:
						provider_input = event.get('input_tokens', 0)
						provider_output = event.get('output_tokens', 0)

				reply = ''.join(reply_parts) or '(réponse vide)'
				result = _complete_chat_turn(
					request.user, conversation, persona, content, reply, model, account, reserve,
					estimated_input, provider_input, provider_output, edit_message,
				)
				settled = True
				yield _sse_event({'type': 'done', **result})
			except ProviderError as error:
				account.credit(reserve, reason='adjustment', note='Remboursement appel IA échoué')
				settled = True
				yield _sse_event({'type': 'error', 'error': {'type': 'provider_error', 'message': str(error)}})
			except Exception:
				logger.exception(
					"Unexpected streaming provider failure for chat model %s (user_id=%s)",
					model.model_id,
					request.user.pk,
				)
				account.credit(reserve, reason='adjustment', note='Remboursement erreur fournisseur inattendue')
				settled = True
				yield _sse_event({'type': 'error', 'error': {'type': 'provider_error', 'message': 'Erreur inattendue pendant l’appel au fournisseur. Réessaie dans un instant.'}})
			finally:
				if not settled:
					account.credit(reserve, reason='adjustment', note='Remboursement flux interrompu')

		return events()



class ConversationListView(View):
	@method_decorator(login_required(login_url='/auth/?next=/chat/api/conversations/'))
	def get(self, request):
		convos = request.user.conversations.all()
		return JsonResponse([
			{"id": c.id, "title": c.title, "persona_id": c.persona_id}
			for c in convos
		], safe=False)


class ConversationDetailView(View):
	@method_decorator(login_required(login_url='/auth/?next=/chat/api/conversations/'))
	def get(self, request, pk):
		conv = get_object_or_404(Conversation, id=pk, user=request.user)
		messages = list(conv.messages.values("id", "role", "content", "prompt_tokens", "completion_tokens", "created_at"))
		return JsonResponse({"id": conv.id, "title": conv.title, "persona_id": conv.persona_id, "messages": messages})

	@method_decorator(login_required(login_url='/auth/?next=/chat/api/conversations/'))
	def patch(self, request, pk):
		conv = get_object_or_404(Conversation, id=pk, user=request.user)
		try:
			payload = json.loads(request.body or '{}')
		except (TypeError, ValueError):
			return _chat_error('JSON invalide.', 'invalid_request', 400)
		persona_id = payload.get('persona_id') if isinstance(payload, dict) else None
		persona = get_object_or_404(Persona, id=persona_id, user=request.user) if persona_id else None
		conv.persona = persona
		conv.save(update_fields=['persona', 'updated_at'])
		return JsonResponse({'id': conv.id, 'persona_id': conv.persona_id})


class ConversationDeleteView(View):
	@method_decorator(login_required(login_url='/auth/?next=/chat/api/conversations/'))
	def delete(self, request, pk):
		conv = get_object_or_404(Conversation, id=pk, user=request.user)
		conv.delete()
		return JsonResponse({}, status=204)


def _persona_data(persona):
	return {
		'id': persona.id,
		'name': persona.name,
		'prompt': persona.prompt,
		'tone': persona.tone,
		'temp': persona.temperature,
		'length': persona.length,
	}


@method_decorator(login_required(login_url='/auth/?next=/chat/api/personas/'), name='dispatch')
class PersonaListView(View):
	def get(self, request):
		return JsonResponse([_persona_data(persona) for persona in request.user.personas.all()], safe=False)

	def post(self, request):
		try:
			payload = json.loads(request.body or '{}')
		except (TypeError, ValueError):
			return _chat_error('JSON invalide.', 'invalid_request', 400)
		if not isinstance(payload, dict):
			return _chat_error('JSON invalide.', 'invalid_request', 400)
		persona = Persona.objects.create(
			user=request.user,
			name=payload.get('name', 'Nouveau persona'),
			prompt=payload.get('prompt', ''),
			tone=payload.get('tone', 'neutre'),
			temperature=payload.get('temp', 0.7),
			length=payload.get('length', 'moyenne'),
		)
		return JsonResponse(_persona_data(persona), status=201)


@method_decorator(login_required(login_url='/auth/?next=/chat/api/personas/'), name='dispatch')
class PersonaDetailView(View):
	def patch(self, request, pk):
		persona = get_object_or_404(Persona, id=pk, user=request.user)
		try:
			payload = json.loads(request.body or '{}')
		except (TypeError, ValueError):
			return _chat_error('JSON invalide.', 'invalid_request', 400)
		if not isinstance(payload, dict):
			return _chat_error('JSON invalide.', 'invalid_request', 400)
		for field in ('name', 'prompt', 'tone', 'length'):
			if field in payload:
				setattr(persona, field, payload[field])
		if 'temp' in payload:
			persona.temperature = payload['temp']
		persona.save()
		return JsonResponse(_persona_data(persona))

	def delete(self, request, pk):
		persona = get_object_or_404(Persona, id=pk, user=request.user)
		persona.delete()
		return JsonResponse({}, status=204)




def pricing_page(request):
    return render(request, 'pricing.html', {
        'is_authenticated': request.user.is_authenticated,
        'tiers': PRICING_TIERS,
    })