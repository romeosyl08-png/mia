import os
import subprocess


DEFAULT_TIMEOUT = 30
MAX_OUTPUT = 12000


def run_sandbox_command(command, cwd=None, timeout=DEFAULT_TIMEOUT):
    """
    Exécute une commande dans un environnement contrôlé.

    On conserve l'environnement du processus parent pour préserver PATH/HOME,
    tout en gardant un contexte minimal et sans shell interactif.
    """

    if not isinstance(command, list) or not command:
        return {
            "error": "La commande doit être une liste non vide."
        }

    command = [str(part) for part in command]

    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    timeout = max(1, min(timeout, 120))

    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
    }

    try:
        process = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )

        return {
            "command": command,
            "stdout": process.stdout[:MAX_OUTPUT],
            "stderr": process.stderr[:MAX_OUTPUT],
            "exit_code": process.returncode,
            "timed_out": False,
        }

    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "stdout": (exc.stdout or "")[:MAX_OUTPUT],
            "stderr": (exc.stderr or "")[:MAX_OUTPUT],
            "exit_code": None,
            "timed_out": True,
            "error": f"Commande interrompue après {timeout} secondes.",
        }

    except Exception as exc:
        return {
            "command": command,
            "stdout": "",
            "stderr": "",
            "exit_code": None,
            "timed_out": False,
            "error": str(exc),
        }