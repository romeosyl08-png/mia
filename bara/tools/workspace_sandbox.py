import os
import shutil
import tempfile

from django.core.files.base import ContentFile

from ..models import WorkFile, FileVersion


class WorkspaceSandbox:
    """
    Copie temporairement un Workspace sur le disque.

    WorkFile reste la source de vérité.
    Le sandbox est uniquement une zone d'exécution.
    """

    def __init__(self, workspace):
        self.workspace = workspace
        self.path = None

    def __enter__(self):
        self.path = tempfile.mkdtemp(
            prefix=f"workspace_{self.workspace.id}_"
        )

        self._sync_from_database()

        return self.path

    def __exit__(self, exc_type, exc_value, traceback):
        if self.path and os.path.exists(self.path):
            shutil.rmtree(self.path, ignore_errors=True)

    def _sync_from_database(self):
        """
        Copie les WorkFile vers le sandbox physique.
        """

        workfiles = WorkFile.objects.filter(
            workspace=self.workspace
        )

        for workfile in workfiles:
            if not workfile.file:
                continue

            relative_path = workfile.original_name

            if not relative_path:
                continue

            # Les marqueurs .keep représentent uniquement
            # des dossiers virtuels dans la base.
            if relative_path.endswith("/.keep"):
                continue

            self._copy_workfile_to_disk(
                workfile,
                relative_path,
            )

    def _copy_workfile_to_disk(self, workfile, relative_path):
        """
        Copie un WorkFile vers le chemin relatif indiqué.
        """

        relative_path = relative_path.replace("\\", "/")
        relative_path = relative_path.lstrip("/")

        destination = os.path.abspath(
            os.path.join(
                self.path,
                relative_path,
            )
        )

        sandbox_root = os.path.abspath(self.path)

        if not destination.startswith(
            sandbox_root + os.sep
        ):
            raise ValueError(
                f"Chemin interdit : {relative_path}"
            )

        parent = os.path.dirname(destination)

        if parent:
            os.makedirs(
                parent,
                exist_ok=True,
            )

        with workfile.file.open("rb") as source:
            with open(destination, "wb") as target:
                shutil.copyfileobj(
                    source,
                    target,
                )

    def sync_to_database(self):
        """
        Synchronise les fichiers créés ou modifiés
        dans le sandbox vers WorkFile.

        WorkFile reste la source de vérité après
        la synchronisation.
        """

        if not self.path:
            return {
                "error": "Sandbox non initialisé."
            }

        if not os.path.exists(self.path):
            return {
                "error": "Le sandbox n'existe plus."
            }

        existing_files = {
            workfile.original_name: workfile
            for workfile in WorkFile.objects.filter(
                workspace=self.workspace
            )
        }

        synchronized = []
        created = []
        updated = []

        for root, directories, filenames in os.walk(self.path):

            for filename in filenames:

                absolute_path = os.path.join(
                    root,
                    filename,
                )

                relative_path = os.path.relpath(
                    absolute_path,
                    self.path,
                )

                relative_path = relative_path.replace(
                    os.sep,
                    "/",
                )

                # Ne jamais transformer un marqueur système
                # en WorkFile normal.
                if relative_path.endswith("/.keep"):
                    continue

                with open(
                    absolute_path,
                    "rb",
                ) as source:
                    content = source.read()

                size_bytes = len(content)

                workfile = existing_files.get(
                    relative_path
                )

                if workfile:

                    FileVersion.objects.create(
                        file=workfile,
                        content=self._text_content(
                            filename,
                            content,
                        ),
                        size_bytes=size_bytes,
                    )

                    workfile.file.save(
                        os.path.basename(filename),
                        ContentFile(content),
                        save=False,
                    )

                    workfile.size_bytes = size_bytes

                    workfile.save(
                        update_fields=[
                            "file",
                            "size_bytes",
                        ]
                    )

                    updated.append(relative_path)

                else:

                    workfile = WorkFile(
                        workspace=self.workspace,
                        original_name=relative_path,
                        content_type=(
                            "text/plain"
                            if self._is_text_file(filename)
                            else ""
                        ),
                        size_bytes=size_bytes,
                    )

                    workfile.file.save(
                        os.path.basename(filename),
                        ContentFile(content),
                        save=False,
                    )

                    workfile.save()

                    created.append(relative_path)

                synchronized.append(relative_path)

        return {
            "synchronized": synchronized,
            "created": created,
            "updated": updated,
        }

    @staticmethod
    def _is_text_file(filename):
        text_extensions = {
            ".txt",
            ".md",
            ".csv",
            ".json",
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".html",
            ".css",
            ".scss",
            ".xml",
            ".yaml",
            ".yml",
            ".sql",
            ".sh",
            ".env",
        }

        extension = os.path.splitext(
            filename
        )[1].lower()

        return extension in text_extensions

    @classmethod
    def _text_content(cls, filename, content):
        if not cls._is_text_file(filename):
            return ""

        return content.decode(
            "utf-8",
            errors="replace",
        )