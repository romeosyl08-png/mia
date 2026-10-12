"""Save a browser-held temporary conversation only on explicit request."""
import base64
import binascii
import json
import logging
import uuid

from django.contrib.auth.decorators import login_required
from django.core.exceptions import RequestDataTooBig
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import JsonResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View

from bara.access import get_workspace
from bara.models import ToolExecution, WorkFile
from .models import Conversation, GeneratedMedia, Message
from .sources import safe_source_url
from .temporary import selected_memory_persona

logger = logging.getLogger(__name__)
IMAGE_TYPES = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp', 'image/gif': '.gif'}


def error(message, status=400):
    return JsonResponse({'error': {'message': message, 'code': 'invalid_request'}}, status=status)


def validate_messages(raw_messages, workspace):
    if not isinstance(raw_messages, list) or not 1 <= len(raw_messages) <= 200:
        raise ValueError('La conversation doit contenir entre 1 et 200 messages.')
    messages, total_chars = [], 0
    for item in raw_messages:
        if not isinstance(item, dict) or item.get('role') not in {'user', 'assistant'}:
            raise ValueError('Chaque message doit avoir un rôle utilisateur ou assistant.')
        content = item.get('content')
        if not isinstance(content, str) or not content.strip() or len(content) > 100_000:
            raise ValueError('Le contenu d’un message est invalide.')
        total_chars += len(content)
        if total_chars > 900_000:
            raise ValueError('La conversation est trop volumineuse pour être enregistrée.')
        message = {'role': item['role'], 'content': content, 'interrupted': item.get('interrupted') is True}
        for field in ('prompt_tokens', 'completion_tokens'):
            value = item.get(field, 0)
            if type(value) is not int or not 0 <= value <= 100_000_000:
                raise ValueError('Le nombre de tokens est invalide.')
            message[field] = value
        sources = item.get('sources', [])
        if not isinstance(sources, list) or len(sources) > 24 or any(
            not isinstance(source, dict) or not safe_source_url(source.get('link')) for source in sources
        ):
            raise ValueError('Les sources du message sont invalides.')
        message['sources'] = [
            {'link': source['link'], 'title': str(source.get('title', ''))[:300],
             'snippet': str(source.get('snippet', ''))[:1000]} for source in sources
        ]
        events = item.get('tool_events', [])
        if not isinstance(events, list) or len(events) > 100 or any(
            not isinstance(event, dict) or event.get('type') not in {'visualization', 'artifact', 'confirmation'} for event in events
        ):
            raise ValueError('Les résultats d’outils sont invalides.')
        for event in events:
            if event['type'] == 'confirmation' and (
                type(event.get('action_id')) is not int or workspace is None
                or not ToolExecution.objects.filter(pk=event['action_id'], workspace=workspace).exists()
            ):
                raise ValueError('Une action est inaccessible dans cet espace.')
        message['tool_events'] = events
        ids = item.get('image_file_ids', [])
        if not isinstance(ids, list) or len(ids) > 4 or any(type(pk) is not int or pk <= 0 for pk in ids):
            raise ValueError('Les images jointes sont invalides.')
        if ids and (workspace is None or WorkFile.objects.filter(
            workspace=workspace, pk__in=ids, content_type__in=IMAGE_TYPES, size_bytes__lte=5 * 1024 * 1024,
        ).count() != len(set(ids))):
            raise ValueError('Une image jointe est inaccessible dans cet espace.')
        message['image_file_ids'] = ids
        media = None
        media_url = item.get('media_url')
        if media_url:
            if not isinstance(media_url, str) or item['role'] != 'assistant' or item.get('media_kind') != 'image':
                raise ValueError('Le média temporaire est invalide.')
            header, separator, encoded = media_url.partition(',')
            content_type = header.removeprefix('data:').removesuffix(';base64')
            if not separator or content_type not in IMAGE_TYPES or header != f'data:{content_type};base64':
                raise ValueError('L’image temporaire est invalide.')
            try:
                data = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exception:
                raise ValueError('L’image temporaire est invalide.') from exception
            if not data:
                raise ValueError('L’image temporaire est vide.')
            media = (content_type, data)
        messages.append((message, media))
    if not any(message['role'] == 'user' for message, _ in messages):
        raise ValueError('La conversation doit contenir un message utilisateur.')
    return messages


@method_decorator(login_required, name='dispatch')
class TemporaryConversationSaveView(View):
    def post(self, request):
        try:
            if len(request.body) > 2_000_000:
                return error('La conversation est trop volumineuse pour être enregistrée.', 413)
            payload = json.loads(request.body or '{}')
        except RequestDataTooBig:
            return error('La conversation est trop volumineuse pour être enregistrée.', 413)
        except (TypeError, ValueError):
            return error('JSON invalide.')
        if not isinstance(payload, dict) or payload.get('conversation_id') is not None:
            return error('Une conversation temporaire doit être enregistrée dans un nouvel historique.')
        try:
            persona = selected_memory_persona(request.user, payload.get('persona_id'))
            if persona is None:
                return error('Choisis un persona pour enregistrer la conversation.')
            workspace_id = payload.get('workspace_id')
            if workspace_id is not None and (type(workspace_id) is not int or workspace_id <= 0):
                return error('Identifiant d’espace invalide.')
            workspace = get_workspace(request, workspace_id, write=True) if workspace_id else None
            messages = validate_messages(payload.get('messages'), workspace)
        except ValueError as exception:
            return error(str(exception))
        title = str(payload.get('title') or '').strip()[:200]
        if not title or title == 'Nouvelle conversation':
            title = next(message['content'][:200] for message, _ in messages if message['role'] == 'user')
        stored_files, saved_messages = [], []
        try:
            with transaction.atomic():
                conversation = Conversation.objects.create(user=request.user, persona=persona, workspace=workspace, title=title)
                last_prompt = ''
                for fields, media_data in messages:
                    message = Message.objects.create(conversation=conversation, **fields)
                    saved = {'id': message.pk, 'media_url': None}
                    if message.role == 'user':
                        last_prompt = message.content
                    if media_data:
                        content_type, data = media_data
                        media = GeneratedMedia(owner=request.user, conversation=conversation, message=message,
                                               prompt=last_prompt, kind='image', content_type=content_type)
                        media.image.save(f'image-{uuid.uuid4().hex}{IMAGE_TYPES[content_type]}', ContentFile(data), save=False)
                        stored_files.append((media.image.storage, media.image.name))
                        media.save()
                        saved['media_url'] = reverse('chat:generated-media', args=[media.pk])
                    saved_messages.append(saved)
        except Exception:
            for storage, name in stored_files:
                storage.delete(name)
            logger.exception('Could not save temporary conversation (user_id=%s)', request.user.pk)
            return error('Impossible d’enregistrer la conversation. Tu peux réessayer.', 500)
        return JsonResponse({'conversation_id': conversation.pk, 'title': conversation.title,
                             'persona_id': persona.pk, 'workspace_id': getattr(workspace, 'pk', None),
                             'messages': saved_messages}, status=201)
