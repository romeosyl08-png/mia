"""Immutable scene versions and a validated set of studio operations."""
import copy
import math
import re
import uuid
from django.db import transaction
from django.db.models import Max
from .models import Asset, Version

def empty_scene():
    return {'schema_version':1,'brief':'','references':[],'visuals':[],'audio':[],'exports':[],
            'settings':{'width':1280,'height':720,'fps':24,'duration':20}}

def number(value, minimum, maximum, label):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(label+' invalide.')
    return round(value,3)

def asset(project, pk, kinds=None, role=None):
    if type(pk) is not int:
        raise ValueError('Identifiant de média invalide.')
    item = Asset.objects.filter(project=project,pk=pk).first()
    if not item or (kinds and item.kind not in kinds) or (role and item.role != role):
        raise ValueError('Média inaccessible ou incompatible avec cette opération.')
    return item

def validate_scene(project, raw):
    if not isinstance(raw,dict) or raw.get('schema_version') != 1:
        raise ValueError('Format du projet invalide.')
    result = empty_scene()
    brief = raw.get('brief','')
    if not isinstance(brief,str) or len(brief)>4000:
        raise ValueError('Direction artistique trop longue (4 000 caractères maximum).')
    result['brief'] = brief
    config = raw.get('settings',{})
    if not isinstance(config,dict) or any(type(config.get(key,default)) is not int for key,default in [('width',1280),('height',720),('fps',24)]) or (config.get('width',1280),config.get('height',720)) not in {(1280,720),(720,1280),(720,720)} or config.get('fps',24) not in {24,30}:
        raise ValueError('Format de sortie invalide.')
    result['settings'] = {'width':config.get('width',1280),'height':config.get('height',720),'fps':config.get('fps',24),
                          'duration':number(config.get('duration',20),1,180,'Durée')}
    seen = set()
    for key,kinds in [('visuals',{'image','video'}),('audio',{'audio'})]:
        rows = raw.get(key,[])
        if not isinstance(rows,list) or len(rows)>24:
            raise ValueError('24 éléments maximum par timeline.')
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,60}',row['id']) or row['id'] in seen:
                raise ValueError('Identifiant de piste invalide ou dupliqué.')
            seen.add(row['id'])
            item = asset(project,row.get('asset_id'),kinds)
            clean = {'id':row['id'],'asset_id':item.pk,'label':str(row.get('label') or item.name)[:120],'locked':row.get('locked') is True}
            if key == 'visuals':
                clean['duration'] = number(row.get('duration',4),0.2,30,'Durée du plan')
            else:
                role = row.get('role','music')
                if role not in {'voice','music','ambience','effect'}:
                    raise ValueError('Type de piste audio invalide.')
                clean.update(role=role,volume=number(row.get('volume',1),0,2,'Volume'),start=number(row.get('start',0),0,179,'Début de piste'))
            result[key].append(clean)
    if sum(row['duration'] for row in result['visuals'])>180:
        raise ValueError('Le montage est limité à trois minutes.')
    for key,kinds in [('references',{'image'}),('exports',{'image','video','audio'})]:
        ids = raw.get(key,[])
        if not isinstance(ids,list) or len(ids)>(8 if key=='references' else 100):
            raise ValueError('Trop de références ou d’exports.')
        result[key] = list(dict.fromkeys(asset(project,pk,kinds).pk for pk in ids))
    return result

@transaction.atomic
def new_version(project, scene, label, parent=None, activate=True):
    from .models import Project
    project = Project.objects.select_for_update().get(pk=project.pk)
    clean = validate_scene(project,scene)
    sequence = (project.versions.aggregate(value=Max('number'))['value'] or 0)+1
    version = Version.objects.create(project=project,number=sequence,parent=parent or project.current_version,scene=clean,label=label[:160])
    if activate:
        project.current_version = version
        project.save(update_fields=['current_version','updated_at'])
    return version

def normalize_operations(project, raw, scene=None):
    scene = scene or project.current_version.scene
    if not isinstance(raw,list) or not 1 <= len(raw) <= 8:
        raise ValueError('Le plan doit contenir entre une et huit opérations.')
    operations = []
    for row in raw:
        if not isinstance(row,dict):
            raise ValueError('Opération invalide.')
        op = row.get('operation')
        if op in {'generate_image','generate_video','generate_audio','edit_image'}:
            prompt = row.get('prompt')
            if not isinstance(prompt,str) or not 1 <= len(prompt.strip()) <= 4000:
                raise ValueError('Description requise (4 000 caractères maximum).')
            clean = {'operation':op,'prompt':prompt.strip()}
            from chat.media_generation import normalize_generation_options
            from chat.services import ProviderError
            kind = 'image' if op=='edit_image' else op.removeprefix('generate_')
            try:
                clean['options'] = normalize_generation_options(kind,'openrouter' if kind!='audio' else 'fal','',row.get('options',{}))
            except (ProviderError,TypeError) as error:
                raise ValueError(str(error)) from error
            refs = row.get('reference_ids',scene['references'])
            if not isinstance(refs,list) or len(refs)>8:
                raise ValueError('Huit images de référence maximum.')
            clean['reference_ids'] = [asset(project,pk,{'image'}).pk for pk in refs]
            if op=='edit_image':
                source = asset(project,row.get('source_id'),{'image'})
                if any(track['asset_id']==source.pk and track['locked'] for track in scene['visuals']):
                    raise ValueError('Déverrouille le plan avant de modifier cette image.')
                clean['source_id'] = source.pk
                clean['mask_id'] = asset(project,row['mask_id'],{'image'},'mask').pk if row.get('mask_id') else None
            if kind=='audio':
                role = row.get('role','music')
                if role not in {'voice','music','ambience','effect'}:
                    raise ValueError('Type de piste invalide.')
                clean['role'] = role
            if row.get('first_frame_id'):
                if kind!='video':
                    raise ValueError('La première image s’applique uniquement aux vidéos.')
                clean['first_frame_id'] = asset(project,row['first_frame_id'],{'image'}).pk
        elif op=='recolor_image':
            source = asset(project,row.get('source_id'),{'image'})
            mask = asset(project,row.get('mask_id'),{'image'},'mask')
            if any(track['asset_id']==source.pk and track['locked'] for track in scene['visuals']):
                raise ValueError('Déverrouille le plan avant de modifier cette image.')
            color = row.get('color')
            if not isinstance(color,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',color):
                raise ValueError('Couleur invalide.')
            clean = {'operation':op,'source_id':source.pk,'mask_id':mask.pk,'color':color}
        elif op=='update_track':
            track = next((track for key in ['visuals','audio'] for track in scene[key] if track['id']==row.get('track_id')),None)
            changes = row.get('changes')
            if not track or track['locked'] or not isinstance(changes,dict) or not changes or set(changes)-{'volume','start','duration','label'}:
                raise ValueError('Piste inexistante, verrouillée ou modification invalide.')
            sample = copy.deepcopy(scene)
            for key in ['visuals','audio']:
                for item in sample[key]:
                    if item['id']==track['id']:
                        if set(changes)-set(item):
                            raise ValueError('Cette propriété ne s’applique pas à la piste.')
                        item.update(changes)
            validate_scene(project,sample)
            clean = {'operation':op,'track_id':track['id'],'changes':changes}
        elif op=='render':
            if row.get('kind') not in {'video','audio'}:
                raise ValueError('Choisis un export vidéo ou audio.')
            clean = {'operation':op,'kind':row['kind']}
        else:
            raise ValueError('Opération non prise en charge : '+str(op))
        operations.append(clean)
    return operations

def add_asset(scene, item, role=None):
    track = {'id':'track_'+uuid.uuid4().hex[:12],'asset_id':item.pk,'label':item.name,'locked':False}
    if item.kind=='audio':
        scene['audio'].append({**track,'volume':1,'start':0,'role':role or 'music'})
    else:
        scene['visuals'].append({**track,'duration':min(30,max(1,item.metadata.get('duration',4) or 4))})
