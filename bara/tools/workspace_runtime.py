from contextlib import contextmanager
from pathlib import Path
import tempfile

from ..models import WorkFile


@contextmanager
def materialize_workspace(workspace):
    """
    Crée une copie temporaire du workspace.

    Les WorkFile ne sont jamais modifiés.
    Le dossier temporaire est supprimé automatiquement à la fin.
    """

    with tempfile.TemporaryDirectory(prefix="bara-workspace-") as temp_dir:
        root = Path(temp_dir)

        files = WorkFile.objects.filter(workspace=workspace)

        for work_file in files:
            name = (work_file.original_name or "").strip()

            if not name or name.endswith("/.keep"):
                continue

            relative_path = Path(name)

            # Protection contre les chemins sortant du workspace.
            if relative_path.is_absolute():
                continue

            if ".." in relative_path.parts:
                continue

            destination = root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)

            content = work_file.extracted_text or ""

            destination.write_text(
                content,
                encoding="utf-8",
            )

        yield root