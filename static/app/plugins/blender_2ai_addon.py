bl_info = {
    "name": "2AI Blender Bridge",
    "author": "2AI",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar > 2AI",
    "description": "Connecte Blender à 2AI avec une connexion sortante sécurisée.",
    "category": "3D View",
}

import json
import ipaddress
import queue
import threading
import urllib.parse
import urllib.error
import urllib.request

import bpy
from bpy.props import StringProperty


POLL_PATH = "/bara/plugins/blender/poll/"
COMPLETE_PATH = "/bara/plugins/blender/complete/"
_stop_event = threading.Event()
_incoming_jobs = queue.Queue()
_outgoing_results = queue.Queue()
_worker = None
_worker_running = False
_last_error = ""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


_opener = urllib.request.build_opener(_NoRedirectHandler())


def _preferences():
    addon = bpy.context.preferences.addons.get(__name__)
    return addon.preferences if addon else None


def _request(base_url, token, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
        method="POST" if payload is not None else "GET",
    )
    with _opener.open(request, timeout=5) as response:
        if response.status == 204:
            return None
        content = response.read()
        return json.loads(content.decode("utf-8")) if content else None


def _network_worker(base_url, token):
    global _last_error, _worker_running
    _worker_running = True
    try:
        while not _stop_event.is_set():
            try:
                response = _request(base_url, token, POLL_PATH)
                _last_error = ""
                task = response.get("task") if response else None
                if task:
                    _incoming_jobs.put(task)
            except urllib.error.HTTPError as error:
                _last_error = f"Erreur HTTP {error.code} pendant la connexion."
                if error.code == 401:
                    _stop_event.set()
            except (OSError, urllib.error.URLError, ValueError) as error:
                _last_error = f"Connexion 2AI impossible : {str(error)[:180]}"
                _stop_event.wait(2)

            try:
                result = _outgoing_results.get(timeout=0.1)
            except queue.Empty:
                result = None
            if result:
                try:
                    _request(base_url, token, COMPLETE_PATH, result)
                    _last_error = ""
                except urllib.error.HTTPError as error:
                    _last_error = f"Erreur HTTP {error.code} lors du retour de résultat Blender."
                except (OSError, urllib.error.URLError) as error:
                    _last_error = f"Retour Blender en attente de nouvel envoi : {str(error)[:150]}"
                    _outgoing_results.put(result)
                    _stop_event.wait(2)
                except ValueError as error:
                    _last_error = f"Réponse 2AI invalide : {str(error)[:150]}"
                    _stop_event.wait(2)
            _stop_event.wait(1)
    finally:
        _worker_running = False


def _bounded_number(value, minimum, maximum):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _execute_task(task):
    action = task.get("action")
    arguments = task.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ValueError("Arguments invalides.")

    if action == "scene_info":
        scene = bpy.context.scene
        active = bpy.context.view_layer.objects.active
        return {
            "blender_version": bpy.app.version_string,
            "scene": scene.name,
            "frame": scene.frame_current,
            "object_count": len(scene.objects),
            "active_object": active.name if active else None,
            "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        }

    if action == "list_objects":
        scene_objects = sorted(bpy.context.scene.objects, key=lambda item: item.name)[:100]
        return {
            "objects": [
                {
                    "name": item.name[:120],
                    "type": item.type,
                    "location": [round(value, 4) for value in item.location],
                }
                for item in scene_objects
            ],
            "truncated": len(bpy.context.scene.objects) > len(scene_objects),
        }

    if action == "create_primitive":
        shape = arguments.get("shape")
        size = arguments.get("size")
        location = arguments.get("location")
        if shape not in {"cube", "sphere", "cylinder", "cone"}:
            raise ValueError("Forme non autorisée.")
        if not _bounded_number(size, 0.05, 1000):
            raise ValueError("Taille hors limites.")
        if (
            not isinstance(location, list)
            or len(location) != 3
            or any(not _bounded_number(value, -10000, 10000) for value in location)
        ):
            raise ValueError("Position hors limites.")

        size = float(size)
        location = [float(value) for value in location]
        if shape == "cube":
            bpy.ops.mesh.primitive_cube_add(size=size, location=location)
        elif shape == "sphere":
            bpy.ops.mesh.primitive_uv_sphere_add(radius=size / 2, location=location)
        elif shape == "cylinder":
            bpy.ops.mesh.primitive_cylinder_add(radius=size / 2, depth=size, location=location)
        else:
            bpy.ops.mesh.primitive_cone_add(radius1=size / 2, depth=size, location=location)

        obj = bpy.context.object
        requested_name = arguments.get("name", "")
        if not isinstance(requested_name, str) or len(requested_name) > 80:
            raise ValueError("Nom d'objet invalide.")
        clean_name = "".join(char for char in requested_name if char.isprintable()).strip()
        if clean_name:
            obj.name = clean_name[:80]
        return {"created": obj.name, "type": obj.type, "location": location}

    raise ValueError("Action Blender inconnue.")


def _process_one_job():
    try:
        task = _incoming_jobs.get_nowait()
    except queue.Empty:
        return 0.2

    task_id = task.get("id")
    try:
        result = _execute_task(task)
        status = "success"
    except (TypeError, ValueError, RuntimeError) as error:
        result = {"error": str(error)[:1000]}
        status = "error"
    _outgoing_results.put({"task_id": task_id, "status": status, "result": result})
    return 0.2


class Blender2AIPreferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    backend_url: StringProperty(
        name="URL de 2AI",
        description="Adresse HTTPS publique de l'application 2AI (HTTP autorisé pour localhost)",
        default="https://",
    )
    token: StringProperty(
        name="Jeton de connexion",
        description="Jeton généré une seule fois depuis AUTOMATISATIONS dans 2AI",
        subtype="PASSWORD",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "backend_url")
        layout.prop(self, "token")
        layout.label(text="Le jeton est envoyé uniquement à cette URL.")


class WM_OT_blender_2ai_start(bpy.types.Operator):
    bl_idname = "wm.blender_2ai_start"
    bl_label = "Connecter à 2AI"

    def execute(self, context):
        global _worker
        preferences = _preferences()
        if not preferences or not preferences.backend_url or not preferences.token:
            self.report({"ERROR"}, "Configure l'URL 2AI et le jeton dans les préférences de l'extension.")
            return {"CANCELLED"}
        try:
            parsed_url = urllib.parse.urlsplit(preferences.backend_url)
            hostname = parsed_url.hostname
            parsed_url.port
        except ValueError:
            parsed_url = None
            hostname = None
        loopback = hostname == "localhost"
        if hostname and not loopback:
            try:
                loopback = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                loopback = False
        if (
            not parsed_url
            or not hostname
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
            or parsed_url.path not in {"", "/"}
            or not (parsed_url.scheme == "https" or (parsed_url.scheme == "http" and loopback))
        ):
            self.report({"ERROR"}, "Utilise HTTPS, sauf pour une adresse localhost.")
            return {"CANCELLED"}
        if _worker and _worker.is_alive():
            self.report({"INFO"}, "La connexion 2AI est déjà active.")
            return {"FINISHED"}
        _stop_event.clear()
        _worker = threading.Thread(
            target=_network_worker,
            args=(preferences.backend_url, preferences.token),
            daemon=True,
        )
        _worker.start()
        if not bpy.app.timers.is_registered(_process_one_job):
            bpy.app.timers.register(_process_one_job, first_interval=0.2, persistent=True)
        self.report({"INFO"}, "Connexion sortante 2AI démarrée.")
        return {"FINISHED"}


class WM_OT_blender_2ai_stop(bpy.types.Operator):
    bl_idname = "wm.blender_2ai_stop"
    bl_label = "Déconnecter de 2AI"

    def execute(self, context):
        _stop_event.set()
        if bpy.app.timers.is_registered(_process_one_job):
            bpy.app.timers.unregister(_process_one_job)
        self.report({"INFO"}, "Connexion 2AI arrêtée.")
        return {"FINISHED"}


class VIEW3D_PT_blender_2ai(bpy.types.Panel):
    bl_label = "2AI"
    bl_idname = "VIEW3D_PT_blender_2ai"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "2AI"

    def draw(self, context):
        layout = self.layout
        preferences = _preferences()
        layout.label(text="Connexion sortante sécurisée")
        layout.label(text="État : connecté" if _worker_running else "État : déconnecté")
        if _last_error:
            layout.label(text=_last_error, icon="ERROR")
        layout.operator("wm.blender_2ai_start")
        layout.operator("wm.blender_2ai_stop")
        if preferences:
            layout.label(text="Configure l'URL et le jeton dans les préférences de l'extension.")


CLASSES = (
    Blender2AIPreferences,
    WM_OT_blender_2ai_start,
    WM_OT_blender_2ai_stop,
    VIEW3D_PT_blender_2ai,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    _stop_event.set()
    if bpy.app.timers.is_registered(_process_one_job):
        bpy.app.timers.unregister(_process_one_job)
    if _worker and _worker.is_alive():
        _worker.join(timeout=1)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
