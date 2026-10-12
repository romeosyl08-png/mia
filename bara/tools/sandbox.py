"""Execute code only in a prebuilt container, never on the application host."""
import os
import subprocess
import selectors
import time
import uuid
from pathlib import Path
from django.conf import settings

DEFAULT_TIMEOUT = 30
MAX_OUTPUT = 12000


def run_sandbox_command(command, cwd=None, timeout=DEFAULT_TIMEOUT):
    if not settings.BARA_CODE_EXECUTION_ENABLED:
        return {'error': 'Exécution de code désactivée.'}
    if not isinstance(command, list) or not command or any(not isinstance(v, str) or "\x00" in v for v in command):
        return {'error': 'La commande doit être une liste de chaînes non vide.'}
    try:
        timeout = max(1, min(int(timeout), 120))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    root = Path(cwd).resolve() if cwd else None
    if not root or not root.is_dir() or ',' in str(root):
        return {'error': 'Dossier de travail invalide.'}
    root.chmod(0o755)
    name = 'ai2ai-' + uuid.uuid4().hex
    runtime = settings.BARA_SANDBOX_RUNTIME
    argv = [runtime, 'run', '--rm', '--pull=never', '--name', name,
            '--network=none', '--read-only', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--pids-limit=64',
            '--memory=256m', '--memory-swap=256m', '--cpus=1',
            '--user', str(os.getuid() or 65534),
            '--tmpfs', '/tmp:rw,nosuid,nodev,size=64m,mode=1777',
            '--mount', f'type=bind,source={root},target=/workspace,readonly',
            '--workdir', '/workspace', '--env', 'PYTHONDONTWRITEBYTECODE=1',
            '--env', 'HOME=/tmp', '--entrypoint', command[0], settings.BARA_SANDBOX_IMAGE, *command[1:]]
    try:
        result = _run_bounded(argv,timeout)
        result['isolated'] = True
        if result['exit_code'] in {125,126,127}:
            result['error'] = 'Environnement isolé indisponible ou commande absente de l’image.'
        return result
    except subprocess.TimeoutExpired:
        return {'error': f'Commande interrompue après {timeout} secondes.', 'timed_out': True, 'exit_code': None}
    except OSError:
        return {'error': 'Runtime isolé indisponible. Aucun code n’a été exécuté sur le serveur.'}
    finally:
        try:
            subprocess.run([runtime, 'rm', '-f', name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _run_bounded(argv,timeout):
    process = subprocess.Popen(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        env={'PATH':os.environ.get('PATH',''),'HOME':os.environ.get('HOME','/tmp')})
    buffers = {'stdout':bytearray(),'stderr':bytearray()}
    total = 0
    deadline = time.monotonic()+timeout
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ,'stdout')
            selector.register(process.stderr,selectors.EVENT_READ,'stderr')
            while selector.get_map():
                if time.monotonic()>=deadline:
                    raise subprocess.TimeoutExpired(argv,timeout)
                for key,_ in selector.select(0.1):
                    data = os.read(key.fileobj.fileno(),32768)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(data)
                    buffer = buffers[key.data]
                    buffer.extend(data[:max(0,MAX_OUTPUT-len(buffer))])
                    if total>1_000_000:
                        process.kill()
                        return {'exit_code':None,'stdout':bytes(buffers['stdout']).decode('utf-8','replace'),
                            'stderr':bytes(buffers['stderr']).decode('utf-8','replace'),
                            'error':'Sortie maximale dépassée (1 Mo).','truncated':True}
        process.wait(timeout=max(0.1,deadline-time.monotonic()))
        return {'exit_code':process.returncode,'stdout':bytes(buffers['stdout']).decode('utf-8','replace'),
            'stderr':bytes(buffers['stderr']).decode('utf-8','replace'),'timed_out':False,
            'truncated':total>MAX_OUTPUT}
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()
