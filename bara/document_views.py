from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render, redirect
from .access import get_workspace
from .models import WorkFile
from .tools.documents import save_document
from .services import StorageQuotaError


@login_required
def document_detail(request, workspace_id, file_id):
    workspace = get_workspace(request,workspace_id)
    file = get_object_or_404(WorkFile,pk=file_id,workspace=workspace,artifact__isnull=False)
    file.workspace = workspace
    artifact = file.artifact
    spec = artifact.specification
    error = ''
    if request.method == 'POST':
        spec = {**spec,'title':request.POST.get('title','')[:200], 'body':request.POST.get('body','')[:100000]}
        if artifact.kind == 'xlsx':
            rows = []
            for line in request.POST.get('table','').splitlines():
                row = []
                for value in line.split('\t'):
                    try:
                        parsed = float(value)
                        row.append(int(parsed) if parsed.is_integer() else parsed)
                    except ValueError:
                        row.append(value)
                rows.append(row)
            spec['tables'] = [{'rows':rows}]
        if artifact.kind == 'pptx':
            spec['slides'] = [{'title':spec['title'],'body':part.strip()} for part in spec['body'].split('\n---\n')]
        try:
            save_document(file.workspace,file.original_name,artifact.kind,spec)
            return redirect(request.path)
        except (ValueError,TypeError,StorageQuotaError) as exc:
            error = str(exc)
    table = '\n'.join('\t'.join(str(c) if c is not None else '' for c in row) for row in (spec.get('tables') or [{}])[0].get('rows',[]))
    body = spec.get('body','')
    if artifact.kind == 'pptx' and spec.get('slides'):
        body = '\n---\n'.join(s.get('body','') for s in spec['slides'])
    return render(request,'bara/document.html',{'file':file,'title':spec.get('title',''),'body':body,'table':table,'error':error})
