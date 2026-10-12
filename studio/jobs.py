"""Durable jobs with reservations, bounded spending and no automatic paid replay."""
import copy
import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from api.models import APIProvider
from chat.billing import CREDITS_PER_DOLLAR, MARGIN_MULTIPLIER, settle_reservation
from chat.media_generation import generate_media, generate_openrouter_media
from . import media
from .models import Job, Project
from .schema import add_asset, asset, new_version, normalize_operations

logger = logging.getLogger(__name__)
LEASE = timedelta(minutes=20)

class Conflict(ValueError):
    pass

def budget(value):
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise ValueError('Plafond de jetons invalide (0 à 1 000 000).')
    return value

def style(project):
    parts = [project.current_version.scene['brief']]
    if project.persona and project.persona.user_id==project.owner_id:
        parts.extend([project.persona.prompt[:3000],project.persona.memory[:3000]])
    return '\n'.join(part for part in parts if part)

def quote(project, operations):
    from chat.views import _media_audio_provider, _media_generation_credits
    rows = normalize_operations(project,operations)
    scene = project.current_version.scene
    audio = len(scene['audio'])+sum(row['operation']=='generate_audio' for row in rows)
    if audio>24 or len(scene['exports'])+sum(row['operation']=='render' for row in rows)>100:
        raise ValueError('Ce plan dépasse la capacité du montage. Retire des pistes ou crée un nouveau projet.')
    durations = {track['id']:track['duration'] for track in scene['visuals']}
    has_audio = bool(scene['audio'])
    existing_images = {track['asset_id'] for track in scene['visuals']}
    for index,row in enumerate(rows):
        op = row['operation']
        if op in {'generate_image','generate_video'} or (op in {'edit_image','recolor_image'} and row['source_id'] not in existing_images):
            durations[f'new_{index}'] = row.get('options',{}).get('duration_seconds',4)
        if op=='generate_audio':
            has_audio = True
        if op=='update_track' and row['track_id'] in durations:
            durations[row['track_id']] = row['changes'].get('duration',durations[row['track_id']])
        if len(durations)>24:
            raise ValueError('Le plan dépasse 24 plans visuels.')
        if sum(durations.values())>180:
            raise ValueError('Le plan dépasse trois minutes de montage vidéo.')
        if op=='render' and ((row['kind']=='video' and not durations) or (row['kind']=='audio' and not has_audio)):
            raise ValueError('Ajoute des plans visuels ou des pistes audio avant de préparer cet export.')
        if op=='edit_image' and len(set([row['source_id']]+([row['mask_id']] if row.get('mask_id') else [])+row['reference_ids']))>8:
            raise ValueError('Huit références maximum, image source et masque compris.')
    total = 0
    for row in rows:
        op = row['operation']
        if op in {'generate_image','generate_video','generate_audio','edit_image'}:
            kind = 'image' if op=='edit_image' else op.removeprefix('generate_')
            provider = (_media_audio_provider() if kind=='audio' else
                        APIProvider.objects.filter(slug='openrouter',is_active=True,keys__is_active=True).distinct().first())
            if not provider:
                raise ValueError('Configure un fournisseur de génération '+kind+' avant de lancer cette tâche.')
            model = (settings.STUDIO_IMAGE_EDIT_MODEL if op=='edit_image' or (kind=='image' and row['reference_ids']) else
                     {'image':settings.OPENROUTER_IMAGE_MODEL,'video':settings.OPENROUTER_VIDEO_MODEL}.get(kind,'') if kind!='audio' else
                     settings.MEDIA_GENERATION_MODELS.get(provider.slug,{}).get(kind,''))
            if not model:
                raise ValueError('Aucun modèle de génération configuré pour ce média.')
            # Audio engines use text; image references are relevant to visual generators only.
            if kind=='audio' and row['reference_ids']:
                row['reference_ids'] = []
            row.update(provider_id=provider.pk,model_id=model,
                       estimated_credits=_media_generation_credits(kind,provider.slug,row['prompt'],row['options']))
        else:
            row['estimated_credits'] = 0
        total += row['estimated_credits']
    return {'operations':rows,'estimated_credits':total,'expected_version':project.current_version_id}

@transaction.atomic
def enqueue(project, operations, limit, expected_version):
    project = Project.objects.select_for_update(of=('self',)).select_related('current_version','persona').get(pk=project.pk)
    if project.current_version_id!=expected_version:
        raise Conflict('Le projet a changé. Recharge la scène avant de lancer le plan.')
    if project.jobs.filter(status__in=['queued','running']).exists():
        raise Conflict('Une tâche est déjà en cours dans ce projet.')
    if Job.objects.filter(project__owner=project.owner,status__in=['queued','running']).count()>=3:
        raise ValueError('Trois tâches maximum en cours par compte.')
    limit = budget(limit)
    plan = quote(project,operations)
    reserve = plan['estimated_credits']
    if reserve>limit:
        raise ValueError('Le coût estimé dépasse le plafond de jetons choisi.')
    if reserve:
        project.owner.credit_account.debit(reserve,note='Réservation studio : '+project.title[:100])
    return Job.objects.create(project=project,base_version=project.current_version,operations=plan['operations'],
                              style_prompt=style(project),credit_limit=limit,reserved_credits=reserve)

def settle(job):
    # Caller holds the job row lock. This flag makes refunds idempotent.
    if job.settled_at:
        return
    result = settle_reservation(job.project.owner.credit_account,job.reserved_credits,
                                min(job.spent_credits,job.credit_limit),'studio #'+str(job.pk))
    job.spent_credits = result.charged_credits
    if result.shortfall:
        job.warning = 'Le solde disponible limite la facturation de cette tâche.'
    job.settled_at = timezone.now()
    job.save(update_fields=['spent_credits','warning','settled_at','updated_at'])

@transaction.atomic
def cancel(job):
    job = Job.objects.select_for_update(of=('self',)).select_related('project__owner').get(pk=job.pk)
    if job.status!='queued':
        raise Conflict('Seules les tâches en attente peuvent être annulées.')
    job.status,job.progress = 'cancelled','Annulée'
    job.save(update_fields=['status','progress','updated_at'])
    settle(job)
    return job

@transaction.atomic
def claim_job(job_id=None):
    now = timezone.now()
    # A lost response may have incurred provider costs. Never resubmit automatically.
    for stale in Job.objects.select_for_update().filter(status='running',lease_until__lt=now):
        stale.status,stale.error = 'failed','Le worker a été interrompu. Aucune génération payante n’a été relancée.'
        stale.progress = 'Interrompue'
        stale.save(update_fields=['status','error','progress','updated_at'])
        settle(stale)
    rows = Job.objects.filter(status='queued').order_by('created_at')
    if job_id is not None:
        rows = rows.filter(pk=job_id)
    candidate = rows.first()
    if not candidate:
        return None
    token = uuid.uuid4().hex
    changed = Job.objects.filter(pk=candidate.pk,status='queued').update(status='running',lease_token=token,
                lease_until=now+LEASE,progress='Démarrage',updated_at=now)
    return Job.objects.select_related('project__owner','base_version').get(pk=candidate.pk) if changed else None

def heartbeat(job, message):
    changed = Job.objects.filter(pk=job.pk,status='running',lease_token=job.lease_token).update(
        progress=message[:200],lease_until=timezone.now()+LEASE,updated_at=timezone.now())
    if not changed:
        raise ValueError('Cette tâche n’est plus active.')

def replace_image(scene, source_id, item):
    replaced = False
    for row in scene['visuals']:
        if row['asset_id']==source_id:
            row['asset_id'],row['label'] = item.pk,item.name
            replaced = True
    if not replaced:
        add_asset(scene,item)

def execute_job(job):
    scene = copy.deepcopy(job.base_version.scene)
    failure = ''
    try:
        for index,row in enumerate(job.operations):
            op = row['operation']
            heartbeat(job,f'Étape {index+1}/{len(job.operations)} : {op}')
            if job.spent_credits+row['estimated_credits']>job.credit_limit:
                raise ValueError('Le plafond de jetons est atteint. Les résultats déjà obtenus sont conservés.')
            item = None
            if op in {'generate_image','generate_video','generate_audio','edit_image'}:
                provider = APIProvider.objects.get(pk=row['provider_id'],is_active=True)
                key_record = provider.keys.filter(is_active=True).first()
                key = key_record.get_key() if key_record else ''
                if not key:
                    raise ValueError('Le fournisseur de génération n’est plus disponible.')
                kind = 'image' if op=='edit_image' else op.removeprefix('generate_')
                prompt = row['prompt'] + ('\nDirection artistique :\n'+job.style_prompt if job.style_prompt else '')
                cost = None
                if kind=='audio':
                    data,mime = generate_media(provider,key,kind,prompt,row['model_id'],row['options'])
                else:
                    refs = list(row['reference_ids'])
                    if op=='edit_image':
                        primary = [row['source_id']]+([row['mask_id']] if row.get('mask_id') else [])
                        refs = primary+[pk for pk in refs if pk not in primary]
                        if len(refs)>8:
                            raise ValueError('Huit références maximum, image source et masque compris.')
                        if row.get('mask_id'):
                            prompt += '\nLa première référence est l’image source. La deuxième est un masque noir et blanc : modifie la zone blanche conformément à la demande. Conserve le cadrage et les dimensions de l’image source.'
                    data,mime,cost = generate_openrouter_media(key,kind,prompt,row['model_id'],row['options'],
                        input_references=[media.reference(asset(job.project,pk)) for pk in refs],
                        first_frame=media.reference(asset(job.project,row['first_frame_id'])) if row.get('first_frame_id') else None,
                        timeout_seconds=settings.STUDIO_MEDIA_TIMEOUT_SECONDS,
                        progress=lambda message:heartbeat(job,message))
                actual = row['estimated_credits'] if cost is None else max(1,round(Decimal(str(cost))*Decimal(str(MARGIN_MULTIPLIER))*CREDITS_PER_DOLLAR))
                job.spent_credits += actual
                if job.spent_credits>job.credit_limit:
                    job.warning = 'Le coût fournisseur dépasse l’estimation ; ton plafond de jetons est respecté.'
                job.save(update_fields=['spent_credits','warning','updated_at'])
                if op=='edit_image' and row.get('mask_id'):
                    data = media.composite(asset(job.project,row['source_id']),data,asset(job.project,row['mask_id']))
                    mime = 'image/png'
                item = media.store_asset(job.project,data,row['prompt'][:100],'generated',job,mime)
                if op=='edit_image':
                    replace_image(scene,row['source_id'],item)
                else:
                    add_asset(scene,item,row.get('role'))
            elif op=='recolor_image':
                data = media.recolor(asset(job.project,row['source_id']),asset(job.project,row['mask_id']),row['color'])
                item = media.store_asset(job.project,data,'Recoloration '+row['color'],'generated',job,'image/png')
                replace_image(scene,row['source_id'],item)
            elif op=='update_track':
                for key in ['visuals','audio']:
                    for track in scene[key]:
                        if track['id']==row['track_id']:
                            track.update(row['changes'])
            elif op=='render':
                data,mime = media.render(job.project,scene,row['kind'])
                item = media.store_asset(job.project,data,'Montage '+row['kind'],'export',job,mime)
                scene['exports'].append(item.pk)
            if item:
                job.output_ids.append(item.pk)
                job.save(update_fields=['output_ids','updated_at'])
    except Exception as error:
        logger.exception('Studio job %s failed',job.pk)
        from chat.services import ProviderError
        failure = str(error)[:300] if isinstance(error,(ValueError,ProviderError)) else 'La tâche a échoué. Les résultats disponibles sont conservés.'
    with transaction.atomic():
        locked = Job.objects.select_for_update(of=('self',)).select_related('project__owner').get(pk=job.pk)
        if locked.status!='running' or locked.lease_token!=job.lease_token:
            return
        project = Project.objects.select_for_update().get(pk=job.project_id)
        if scene!=job.base_version.scene:
            try:
                locked.result_version = new_version(project,scene,'Résultat de la tâche #'+str(job.pk),parent=job.base_version,
                                                    activate=project.current_version_id==job.base_version_id)
            except ValueError:
                failure = 'Les médias obtenus sont conservés dans la médiathèque, mais le montage dépasse ses limites.'
        locked.status = 'failed' if failure else 'completed'
        locked.error = failure
        locked.progress = 'Échec' if failure else 'Terminée'
        locked.save(update_fields=['status','error','progress','result_version','updated_at'])
        settle(locked)
