import base64
from bara.models import WorkFile


def image_parts(workspace, ids):
    if not isinstance(ids,list) or len(ids) > 4 or any(not isinstance(i,int) or isinstance(i,bool) for i in ids):
        raise ValueError('Quatre images maximum, identifiants valides requis.')
    if ids and workspace is None:
        raise ValueError('Sélectionne l’espace contenant les images.')
    files = {f.pk:f for f in WorkFile.objects.filter(workspace=workspace,pk__in=ids)}
    parts = []
    for pk in ids:
        file = files.get(pk)
        if not file or file.content_type not in {'image/jpeg','image/png','image/webp','image/gif'} or file.size_bytes > 5*1024*1024:
            raise ValueError('Image inaccessible ou trop volumineuse (5 Mo maximum).')
        with file.file.open('rb') as f:
            data = f.read(5*1024*1024+1)
        if len(data) > 5*1024*1024:
            raise ValueError('Image trop volumineuse.')
        parts.append({'type':'image_url','image_url':{'url':'data:'+file.content_type+';base64,'+base64.b64encode(data).decode()}})
    return parts
