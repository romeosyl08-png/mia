import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from .models import Conversation, Message, Persona
from ai_user.models import InsufficientCreditsError

from .services import ProviderError, call_llm_provider, credit_multiplier, estimate_cost
from api.models import AIModel


def chat_home(request):
	if not request.user.is_authenticated:
		return redirect(f'{reverse("auth-page")}?next=/')

	credits = 0
	credit_account = getattr(request.user, "credit_account", None)
	credits = credit_account.balance if credit_account is not None else 0
	models = AIModel.objects.filter(
		is_active=True,
		provider__is_active=True,
	).select_related('provider')

	return render(request, 'chat.html', {
		'ai_models': models,
		'initial_credits': credits,
		'is_authenticated': request.user.is_authenticated,
		'login_url': '/auth/?next=/',
		'has_active_models': models.exists(),
	})


def dap_home(request):
	if not request.user.is_authenticated:
		return redirect(f'{reverse("auth-page")}?next=/dap/')
	return render(request, 'dap.html')


def _chat_error(message, error_type, status):
	return JsonResponse({'error': {'message': message, 'type': error_type}}, status=status)


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

		model_query = AIModel.objects.filter(is_active=True, provider__is_active=True).select_related('provider')
		model_id = payload.get('model_id')
		model = model_query.filter(model_id=model_id).first() if model_id else model_query.first()
		if not model:
			return _chat_error('Aucun modèle actif n’est configuré.', 'provider_unavailable', 503)

		api_key = model.provider.keys.filter(is_active=True).order_by('id').first()
		if not api_key or not model.provider.base_url:
			return _chat_error('Le fournisseur IA n’est pas configuré.', 'provider_unavailable', 503)

		conversation_id = payload.get('conversation_id')
		conversation = None
		if conversation_id:
			conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
		persona_id = payload.get('persona_id')
		persona = get_object_or_404(Persona, id=persona_id, user=request.user) if persona_id else None
		if conversation and persona and conversation.persona_id != persona.id:
			conversation.persona = persona
			conversation.save(update_fields=['persona', 'updated_at'])

		system_prompt = str(payload.get('system_prompt', ''))
		messages = []
		if conversation:
			messages = list(conversation.messages.filter(role__in={'user', 'assistant'}).values('role', 'content'))
		messages.append({'role': 'user', 'content': content})
		reserve, estimated_input = estimate_cost(content, model, system_prompt, messages)
		account = request.user.credit_account
		try:
			account.debit(reserve, reason='usage', note=f'Reservation {model.model_id}')
		except InsufficientCreditsError:
			return _chat_error('Crédits insuffisants pour ce message.', 'insufficient_credits', 402)

		try:
			reply, provider_input, provider_output = call_llm_provider(
				model.provider,
				api_key.get_key(),
				model.model_id,
				messages,
				system_prompt,
				float(payload.get('temperature', 0.7)),
				500,
			)
		except ProviderError as error:
			account.credit(reserve, reason='adjustment', note='Remboursement appel IA échoué')
			return _chat_error(str(error), 'provider_error', 502)

		prompt_tokens = provider_input or estimated_input
		completion_tokens = provider_output or round(len(reply) / 4)
		actual_cost = (
			max(1, prompt_tokens) + max(1, completion_tokens)
		) * credit_multiplier(model)
		if actual_cost < reserve:
			account.credit(reserve - actual_cost, reason='adjustment', note=f'Ajustement {model.model_id}')

		if conversation is None:
			conversation = Conversation.objects.create(
				user=request.user,
				title=content[:200],
				persona=persona,
			)
		Message.objects.create(
			conversation=conversation,
			role='user',
			content=content,
			prompt_tokens=prompt_tokens,
		)
		Message.objects.create(
			conversation=conversation,
			role='assistant',
			content=reply,
			completion_tokens=completion_tokens,
		)

		return JsonResponse({
			'conversation_id': conversation.id,
			'title': conversation.title,
			'reply': reply,
			'prompt_tokens': prompt_tokens,
			'completion_tokens': completion_tokens,
			'remaining_balance': account.balance,
		})

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
		messages = list(conv.messages.values("role", "content", "prompt_tokens", "completion_tokens", "created_at"))
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
