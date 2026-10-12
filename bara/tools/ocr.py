import tempfile
from pathlib import Path
from .registry import register_tool
from .sandbox import run_sandbox_command
from .definitions import _get_file


@register_tool(name='extract_document_text',description='Extrait le texte d’une image ou d’un PDF scanné, jusqu’à 10 pages, dans un environnement isolé.',
    parameters={'type':'object','properties':{'file_id':{'type':'integer'}},'required':['file_id']})
def extract_document_text(workspace, file_id):
    file = _get_file(workspace,file_id)
    if file is None:
        return {'error':'Fichier introuvable.'}
    if file.content_type not in {'application/pdf','image/png','image/jpeg','image/webp'}:
        return {'error':'Image ou PDF requis.'}
    with tempfile.TemporaryDirectory(prefix='ai2ai-ocr-') as root:
        path = Path(root)/('source.pdf' if file.content_type == 'application/pdf' else 'source.image')
        with file.file.open('rb') as source:
            data = source.read(10*1024*1024+1)
        if len(data) > 10*1024*1024:
            return {'error':'Document trop volumineux.'}
        path.write_bytes(data)
        script = (
            "import pathlib,subprocess; p=pathlib.Path('/workspace'); "
            "subprocess.run(['pdftoppm','-f','1','-l','10','-scale-to','2000','-png',str(p/'source.pdf'),'/tmp/page'],check=True); "
            "images=sorted(pathlib.Path('/tmp').glob('page-*.png')); "
            "[(print('[Page %s]'%(i+1)),subprocess.run(['tesseract',str(img),'stdout','-l','fra+eng'],check=True)) for i,img in enumerate(images)]"
        ) if file.content_type == 'application/pdf' else "import subprocess; subprocess.run(['tesseract','/workspace/source.image','stdout','-l','fra+eng'],check=True)"
        result = run_sandbox_command(['python','-c',script],cwd=root,timeout=120)
        if result.get('exit_code') != 0:
            return {'error':result.get('error') or 'Extraction OCR échouée.'}
        file.extracted_text = result['stdout'][:50000]
        file.retrieval_index = {}
        file.save(update_fields=['extracted_text','retrieval_index'])
        return {'file_id':file.pk,'text':file.extracted_text,'max_pages':10,'may_be_truncated':len(result['stdout'])>=12000}
