"""Creation and the shared media library, independent of chat conversations."""
import json
import mimetypes
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from bara.models import WorkFile, Workspace
from bara.services import StorageQuotaError, check_quota
from chat.models import Conversation, GeneratedMedia, Message
from chat.views import ImageGenerationView, _bounded_int, _media_audio_provider
from api.models import APIProvider
from studio import media as studio_media
from studio.models import Asset, Project
from studio.schema import empty_scene, new_version


class GenerationView(ImageGenerationView):
    standalone = True

    def dispatch(self, request, *args, **kwargs):
        return private(super().dispatch(request, *args, **kwargs))


def private(response):
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
@require_GET
def index(request, tab='generate'):
    source = None
    if request.GET.get('message'):
        source = get_object_or_404(Message.objects.select_related('conversation'),
            pk=_bounded_int(request.GET['message'], 0, 0, 2**63-1), conversation__user=request.user)
    openrouter = APIProvider.objects.filter(slug='openrouter', is_active=True, keys__is_active=True).exists()
    config = {
        'userId': request.user.pk,
        'tab': tab,
        'kind': request.GET.get('kind') if request.GET.get('kind') in {'image', 'video', 'audio'} else 'image',
        'balance': request.user.credit_account.balance,
        'available': {
            'image': openrouter and bool(settings.OPENROUTER_IMAGE_MODEL),
            'video': openrouter and bool(settings.OPENROUTER_VIDEO_MODEL),
            'audio': _media_audio_provider() is not None,
        },
        'prompt': source.content[:5000] if source else '',
        'conversationId': source.conversation_id if source else None,
        'draft': request.GET.get('draft') == '1',
        'urls': {name: reverse('creation:' + name) for name in
            ('index', 'library', 'generate', 'media-list', 'use-in-chat', 'use-in-studio')},
    }
    return private(render(request, 'creation/index.html', {
        'creation_config': config, 'tab': tab,
        'projects': Project.objects.filter(owner=request.user)[:100],
        'source': source,
    }))


def generated_item(item):
    url = reverse('chat:generated-media', args=[item.pk])
    return {
        'id': item.pk, 'source': 'generated', 'kind': item.kind,
        'prompt': item.prompt, 'name': item.prompt[:120], 'created_at': item.created_at,
        'media_url': url, 'download_url': url + '?download=1',
        'conversation_title': item.conversation.title if item.conversation else None,
        'origin_url': (reverse('chat:chat-home') + '?' + urlencode({'conversation': item.conversation_id}))
            if item.conversation_id else None,
        'origin_label': 'Conversation d’origine' if item.conversation_id else 'Création',
    }


def studio_item(item):
    url = reverse('studio:asset', args=[item.project_id, item.pk])
    return {
        'id': item.pk, 'source': 'studio', 'kind': item.kind,
        'prompt': item.name, 'name': item.name, 'created_at': item.created_at,
        'media_url': url, 'download_url': url + '?download=1',
        'origin_url': reverse('studio:detail', args=[item.project_id]),
        'origin_label': 'Studio · ' + item.project.title,
    }


@login_required
@require_GET
def library(request):
    kind = request.GET.get('kind', '')
    try:
        offset = int(request.GET.get('offset', '0'))
        if kind not in {'', 'image', 'video', 'audio'} or not 0 <= offset <= 10000:
            raise ValueError
    except ValueError:
        return private(JsonResponse({'error': {'message': 'Filtre ou pagination invalide.'}}, status=400))
    query = request.GET.get('q', '').strip()[:200]
    generated = GeneratedMedia.objects.filter(owner=request.user).select_related('conversation')
    assets = Asset.objects.filter(project__owner=request.user).exclude(role='mask').select_related('project')
    if kind:
        generated, assets = generated.filter(kind=kind), assets.filter(kind=kind)
    if query:
        generated = generated.filter(Q(prompt__icontains=query) | Q(conversation__title__icontains=query))
        assets = assets.filter(Q(name__icontains=query) | Q(project__title__icontains=query))
    # Each source contributes at most the first N rows needed for this page.
    end = offset + 25
    items = [generated_item(item) for item in generated.order_by('-created_at', '-pk')[:end]]
    items += [studio_item(item) for item in assets.order_by('-created_at', '-pk')[:end]]
    items.sort(key=lambda item: (item['created_at'], item['source'], item['id']), reverse=True)
    page = items[offset:end]
    more = len(page) > 24
    return private(JsonResponse({'items': page[:24], 'next_offset': offset + 24 if more else None}))


def media_endpoint(function):
    @wraps(function)
    @login_required
    @require_POST
    def wrapped(request):
        try:
            payload = json.loads(request.body)
            if not isinstance(payload, dict):
                raise ValueError('Demande invalide.')
            with transaction.atomic():
                return private(function(request, payload))
        except (ValueError, TypeError, StorageQuotaError) as error:
            return private(JsonResponse({'error': {'message': str(error)[:400] or 'Demande invalide.'}}, status=400))
        except OSError:
            return private(JsonResponse({'error': {'message': 'Le fichier est indisponible. Réessaie plus tard.'}}, status=503))
    return wrapped


def owned_media(user, payload):
    pk = payload.get('id')
    if not isinstance(pk, int) or isinstance(pk, bool):
        raise ValueError('Identifiant de média invalide.')
    if payload.get('source') == 'generated':
        item = get_object_or_404(GeneratedMedia, pk=pk, owner=user)
        return item.image, item.kind, item.content_type, item.prompt
    if payload.get('source') == 'studio':
        item = get_object_or_404(Asset, pk=pk, project__owner=user)
        return item.file, item.kind, item.content_type, item.name
    raise ValueError('Source du média invalide.')


def read_media(file):
    with file.open('rb') as handle:
        data = handle.read(studio_media.MAX_BYTES + 1)
    if not data or len(data) > studio_media.MAX_BYTES:
        raise ValueError('Le fichier doit faire entre 1 octet et 100 Mo.')
    return data


@media_endpoint
def use_in_chat(request, payload):
    file, kind, content_type, prompt = owned_media(request.user, payload)
    data = read_media(file)
    # Images use the existing vision attachment flow; other media become workspace files.
    if kind == 'image' and len(data) > 5 * 1024 * 1024:
        raise ValueError('Les images jointes au chat sont limitées à 5 Mo.')
    check_quota(request.user, extra_bytes=len(data), new_files=1)
    workspace = Workspace.objects.create(user=request.user, title=('Média · ' + prompt)[:200])
    name = f'{kind}-{payload["id"]}{mimetypes.guess_extension(content_type) or ".bin"}'
    attachment = WorkFile(workspace=workspace, original_name=name, content_type=content_type,
        size_bytes=len(data), extracted_text='Description du média : ' + prompt[:5000])
    attachment.file.save(name, ContentFile(data), save=True)
    conversation = Conversation.objects.create(user=request.user, workspace=workspace,
        persona=request.user.personas.order_by('id').first(), title=prompt[:200] or 'Conversation sur un média')
    params = {'conversation': conversation.pk, 'creation_file': attachment.pk}
    return JsonResponse({'url': reverse('chat:chat-home') + '?' + urlencode(params)})


@media_endpoint
def use_in_studio(request, payload):
    file, kind, content_type, prompt = owned_media(request.user, payload)
    project_id = payload.get('project_id')
    if project_id is not None and (not isinstance(project_id, int) or isinstance(project_id, bool)):
        raise ValueError('Projet invalide.')
    if project_id:
        project = get_object_or_404(Project.objects.select_for_update(), pk=project_id, owner=request.user)
    else:
        project = Project.objects.create(owner=request.user, title=prompt[:120] or 'Nouveau montage')
        new_version(project, empty_scene(), 'Création du projet')
    studio_media.store_asset(project, read_media(file), prompt[:120], role='source', declared_type=content_type)
    return JsonResponse({'url': reverse('studio:detail', args=[project.pk])})
