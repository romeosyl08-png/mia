"""Exécution des outils enregistrés par l'agent."""

import logging

from .registry import TOOLS

logger = logging.getLogger(__name__)

# Le résultat d'un outil repart dans le contexte du modèle, donc potentiellement
# à l'écran : une exception brute y exposerait des chemins serveur ou des
# messages de base de données. On journalise le détail et on répond court.
GENERIC_ERROR = "L'outil a échoué. Réessaie avec d'autres arguments."


def execute_tool(name, workspace, arguments):
    """Exécute un outil enregistré."""

    tool = TOOLS.get(name)
    if not tool:
        return {"error": f"Outil inconnu : {name}"}

    function = tool["function"]
    try:
        return function(workspace=workspace, **arguments)
    except TypeError as error:
        # Le modèle a fourni de mauvais arguments : c'est utile de le lui dire.
        logger.warning("Outil %s appelé avec des arguments invalides : %s", name, error)
        return {"error": f"Arguments invalides pour {name}."}
    except Exception as error:
        logger.exception("Outil %s en échec", name)
        del error  # le détail reste dans les journaux, pas dans le contexte du modèle
        return {"error": GENERIC_ERROR}
