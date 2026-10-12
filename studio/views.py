import copy
import json
import re
import shutil
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from ai_user.models import InsufficientCreditsError
from bara.models import Workspace
from chat.models import Persona
from chat.services import ProviderError, check_user_rate_limit
from . import director, jobs, media
from .models import Asset, Job, Project
from .schema import empty_scene, new_version

def owned(request, pk):
    return get_object_or_404(Project.objects.select_related('current_version','persona','workspace','owner__credit_account'),pk=pk,owner=request.user)

def json_endpoint(function):
    @wraps(function)
    @login_required
    def wrapped(request,*args,**kwargs):
        try:
            response = function(request,*args,**kwargs)
        except jobs.Conflict as error:
            response = JsonResponse({'error':str(error)},status=409)
        except InsufficientCreditsError:
            response = JsonResponse({'error':'Solde de jetons insuffisant. Recharge ton compte ou choisis une tâche gratuite.'},status=402)
        except (ValueError,ProviderError) as error:
            response = JsonResponse({'error':str(error)[:400]},status=400)
        except (TypeError,OverflowError):
            response = JsonResponse({'error':'Format de demande invalide.'},status=400)
        response['Cache-Control'] = 'private, no-store'
        return response
    return wrapped

def body(request):
    if len(request.body)>128*1024:
        raise ValueError('Demande trop volumineuse.')
    value = json.loads(request.body)
    if not isinstance(value,dict):
        raise ValueError('La demande doit être un objet JSON.')
    return value

def serialize(project):
    assets = [{**row,'url':reverse('studio:asset',args=[project.pk,row['id']])} for row in
              project.assets.values('id','name','kind','role','content_type','metadata','size_bytes')]
    return {'project':{'id':project.pk,'title':project.title,'persona':project.persona.name if project.persona else None,
                       'workspace_id':project.workspace_id},
            'version_id':project.current_version_id,'scene':project.current_version.scene,
            'assets':assets,'versions':list(project.versions.values('id','number','label','created_at')[:100]),
            'jobs':list(project.jobs.values('id','status','progress','error','warning','reserved_credits','spent_credits','output_ids','result_version_id')[:25]),
            'balance':project.owner.credit_account.balance,
            'capabilities':{'render':bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))}}

@login_required
@require_GET
def index(request):
    return render(request,'studio/index.html',{'projects':Project.objects.filter(owner=request.user).select_related('persona','workspace'),
                'personas':Persona.objects.filter(user=request.user),'workspaces':Workspace.objects.filter(user=request.user),
                'workspace_id':request.GET.get('workspace_id','')})

@login_required
@require_POST
@transaction.atomic
def create(request):
    title = request.POST.get('title','').strip()[:120] or 'Nouveau projet'
    if any(request.POST.get(key) and not request.POST[key].isdigit() for key in ['persona','workspace']):
        return HttpResponse('Choix de persona ou d’espace invalide.',status=400)
    persona = get_object_or_404(Persona,pk=request.POST['persona'],user=request.user) if request.POST.get('persona') else None
    workspace = get_object_or_404(Workspace,pk=request.POST['workspace'],user=request.user) if request.POST.get('workspace') else None
    project = Project.objects.create(owner=request.user,title=title,persona=persona,workspace=workspace)
    new_version(project,empty_scene(),'Création du projet')
    return redirect('studio:detail',pk=project.pk)

@login_required
@require_GET
def detail(request,pk):
    project = owned(request,pk)
    return render(request,'studio/detail.html',{'project':project,'initial_state':serialize(project)})

@json_endpoint
@require_GET
def state(request,pk):
    return JsonResponse(serialize(owned(request,pk)))

@json_endpoint
@require_POST
@transaction.atomic
def save_scene(request,pk):
    project = owned(request,pk)
    project = Project.objects.select_for_update().get(pk=project.pk)
    data = body(request)
    if data.get('expected_version')!=project.current_version_id:
        raise jobs.Conflict('Une nouvelle version est disponible. Recharge avant d’enregistrer tes modifications.')
    new_version(project,data.get('scene'),'Modification du montage')
    project.refresh_from_db()
    return JsonResponse(serialize(project))

@json_endpoint
@require_POST
@transaction.atomic
def restore(request,pk):
    project = owned(request,pk)
    project = Project.objects.select_for_update().get(pk=project.pk)
    data = body(request)
    if data.get('expected_version')!=project.current_version_id:
        raise jobs.Conflict('Recharge le projet avant de restaurer une version.')
    version = get_object_or_404(project.versions,pk=data.get('version_id'))
    new_version(project,copy.deepcopy(version.scene),'Restauration de la version '+str(version.number),parent=version)
    project.refresh_from_db()
    return JsonResponse(serialize(project))

@json_endpoint
@require_POST
def upload(request,pk):
    project = owned(request,pk)
    file = request.FILES.get('file')
    role = request.POST.get('role','reference')
    if role not in {'reference','mask','source'} or not file or file.size>media.MAX_BYTES:
        raise ValueError('Choisis un fichier de 100 Mo maximum.')
    item = media.store_asset(project,file.read(media.MAX_BYTES+1),file.name,role,declared_type=file.content_type or '')
    return JsonResponse({'asset_id':item.pk,'state':serialize(project)})

@json_endpoint
@require_POST
def quote(request,pk):
    project = owned(request,pk)
    return JsonResponse(jobs.quote(project,body(request).get('operations')))

@json_endpoint
@require_POST
def enqueue(request,pk):
    project = owned(request,pk)
    data = body(request)
    task = jobs.enqueue(project,data.get('operations'),data.get('credit_limit',20000),data.get('expected_version'))
    return JsonResponse({'job_id':task.pk,'state':serialize(owned(request,pk))},status=202)

@json_endpoint
@require_POST
def cancel(request,pk,job_id):
    project = owned(request,pk)
    jobs.cancel(get_object_or_404(Job,pk=job_id,project=project))
    return JsonResponse(serialize(project))

@json_endpoint
@require_POST
def propose(request,pk):
    project = owned(request,pk)
    allowed,retry = check_user_rate_limit(request.user.pk,max_requests=3,key_prefix='studio-director')
    if not allowed:
        return JsonResponse({'error':'Attends un instant avant de solliciter le directeur.','retry_after':retry},status=429)
    data = body(request)
    try:
        result = director.propose(project,data.get('request'),data.get('credit_limit',2000))
    except (json.JSONDecodeError,TypeError,KeyError) as error:
        raise ValueError('Le directeur a renvoyé un plan invalide. Aucun jeton n’a été débité.') from error
    result['balance'] = project.owner.credit_account.balance
    return JsonResponse(result)

@login_required
@require_GET
def scene_json(request,pk):
    project = owned(request,pk)
    response = HttpResponse(json.dumps(project.current_version.scene,ensure_ascii=False,indent=2),content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="scene-{pk}.json"'
    response['Cache-Control'] = 'private, no-store'
    return response

@login_required
@require_GET
def asset_file(request,pk,asset_id):
    project = owned(request,pk)
    item = get_object_or_404(Asset,pk=asset_id,project=project)
    handle = item.file.open('rb')
    requested = request.headers.get('Range')
    if requested:
        match = re.fullmatch(r'bytes=(\d*)-(\d*)',requested)
        start,end = 0,item.size_bytes-1
        if match and any(match.groups()):
            left,right = match.groups()
            if left:
                start = int(left)
                end = min(int(right),end) if right else end
            else:
                start = max(0,item.size_bytes-int(right))
        else:
            start = item.size_bytes
        if not 0<=start<=end<item.size_bytes:
            handle.close()
            response = HttpResponse(status=416)
            response['Content-Range'] = f'bytes */{item.size_bytes}'
            return response
        handle.seek(start)
        def chunks():
            remaining = end-start+1
            try:
                while remaining:
                    chunk = handle.read(min(64*1024,remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk
            finally:
                handle.close()
        response = StreamingHttpResponse(chunks(),status=206,content_type=item.content_type)
        response['Content-Length'] = str(end-start+1)
        response['Content-Range'] = f'bytes {start}-{end}/{item.size_bytes}'
    else:
        response = FileResponse(handle,content_type=item.content_type)
    response['Accept-Ranges'] = 'bytes'
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    if request.GET.get('download'):
        extension = item.file.name.rsplit('.',1)[-1]
        response['Content-Disposition'] = f'attachment; filename="studio-{item.pk}.{extension}"'
    return response
