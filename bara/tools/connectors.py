import base64
import re
from urllib.parse import quote
from ..plugin_services import PluginError, _access_token, _provider_request as _request_json
from .registry import register_tool

CONNECTOR_TOOLS = {'drive_search':'google_drive','drive_read':'google_drive','calendar_events':'google_calendar',
                   'github_repositories':'github','github_read_file':'github'}


def request(workspace, plugin, url, params=None):
    connection = workspace.plugin_connections.filter(plugin=plugin,is_enabled=True).first()
    if not connection:
        raise PluginError('Connecte ce service dans Connexions.')
    token = connection.get_access_token() if plugin == 'github' else _access_token(connection)
    return _request_json(url,token,params=params,headers={'Accept':'application/vnd.github+json'} if plugin=='github' else None)


@register_tool(name='drive_search',description='Recherche les fichiers Google Drive du compte connecté (lecture seule).',parameters={'type':'object','properties':{'query':{'type':'string'}},'required':['query']})
def drive_search(workspace,query):
    try:
        query = str(query)[:200].replace('\\','\\\\').replace("'","\\'")
        return request(workspace,'google_drive','https://www.googleapis.com/drive/v3/files',
                       {'q':f"trashed = false and (name contains '{query}' or fullText contains '{query}')",'pageSize':10,'fields':'files(id,name,mimeType,webViewLink)'})
    except PluginError as exc:
        return {'error':str(exc)}


@register_tool(name='drive_read',description='Lit un document Google Docs ou le texte d’un fichier Google Drive (lecture seule).',parameters={'type':'object','properties':{'file_id':{'type':'string'}},'required':['file_id']})
def drive_read(workspace,file_id):
    import requests
    try:
        if not re.fullmatch(r'[\w-]{1,200}',file_id):
            raise PluginError('Identifiant Drive invalide.')
        meta = request(workspace,'google_drive','https://www.googleapis.com/drive/v3/files/'+file_id,{'fields':'id,name,mimeType'})
        connection = workspace.plugin_connections.get(plugin='google_drive',is_enabled=True)
        google_doc = meta['mimeType']=='application/vnd.google-apps.document'
        if not google_doc and not meta['mimeType'].startswith('text/'):
            return {'error':'Ce lecteur prend en charge Google Docs et les fichiers texte. Télécharge les autres formats dans Bara.'}
        url = 'https://www.googleapis.com/drive/v3/files/'+file_id + ('/export' if google_doc else '')
        with requests.get(url,headers={'Authorization':'Bearer '+_access_token(connection)},
                          params={'mimeType':'text/plain'} if google_doc else {'alt':'media'},timeout=20,stream=True) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_content(8192):
                data.extend(chunk)
                if len(data)>=50000:
                    break
        return {'file_id':file_id,'name':meta['name'],'text':bytes(data[:50000]).decode('utf-8','replace'),'truncated':len(data)>=50000}
    except (PluginError,requests.RequestException,KeyError) as exc:
        return {'error':str(exc) if isinstance(exc,PluginError) else 'Lecture Drive échouée.'}


@register_tool(name='calendar_events',description='Recherche les événements du calendrier Google principal (lecture seule).',parameters={'type':'object','properties':{'query':{'type':'string'},'time_min':{'type':'string','description':'Date ISO 8601 avec fuseau'}}})
def calendar_events(workspace,query='',time_min=''):
    from django.utils import timezone
    try:
        data = request(workspace,'google_calendar','https://www.googleapis.com/calendar/v3/calendars/primary/events',
                       {'q':str(query)[:200],'timeMin':time_min or timezone.now().isoformat(),'singleEvents':'true','orderBy':'startTime','maxResults':20})
        return {'events':[{k:event.get(k) for k in ('id','summary','start','end','location','htmlLink')} for event in data.get('items',[])]}
    except PluginError as exc:
        return {'error':str(exc)}


@register_tool(name='github_repositories',description='Liste les dépôts accessibles au compte GitHub connecté (lecture seule).',parameters={'type':'object','properties':{}})
def github_repositories(workspace):
    import requests
    try:
        connection = workspace.plugin_connections.get(plugin='github',is_enabled=True)
        response = requests.get('https://api.github.com/user/repos',params={'per_page':20,'sort':'updated'},
            headers={'Authorization':'Bearer '+connection.get_access_token(),'Accept':'application/vnd.github+json'},timeout=20)
        response.raise_for_status()
        return {'repositories':[{k:r.get(k) for k in ('full_name','description','html_url','default_branch')} for r in response.json()]}
    except Exception:
        return {'error':'Lecture GitHub échouée. Vérifie la connexion et ses permissions.'}


@register_tool(name='github_read_file',description='Lit un fichier du dépôt GitHub connecté (lecture seule).',parameters={'type':'object','properties':{'repository':{'type':'string'},'path':{'type':'string'},'ref':{'type':'string'}},'required':['repository','path']})
def github_read_file(workspace,repository,path,ref=''):
    try:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository) or len(path)>500 or '..' in path.split('/'):
            raise PluginError('Dépôt ou chemin invalide.')
        data = request(workspace,'github','https://api.github.com/repos/'+repository+'/contents/'+quote(path,safe='/'), {'ref':ref} if ref else None)
        if data.get('type')!='file' or data.get('size',0)>100000 or data.get('encoding')!='base64':
            return {'error':'Fichier texte trop volumineux ou format non pris en charge.'}
        text = base64.b64decode(data['content']).decode('utf-8')
        return {'path':path,'content':text,'url':data.get('html_url')}
    except (PluginError,ValueError,KeyError,UnicodeError) as exc:
        return {'error':str(exc) if isinstance(exc,PluginError) else 'Fichier GitHub invalide.'}
