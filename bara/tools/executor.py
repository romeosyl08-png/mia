from .registry import TOOLS


def execute_tool(name, workspace, arguments):
    """
    Exécute un outil enregistré.
    """

    tool = TOOLS.get(name)

    if not tool:
        return {
            "error": f"Outil inconnu : {name}"
        }

    function = tool["function"]

    try:
        result = function(
            workspace=workspace,
            **arguments,
        )

        return result

    except Exception as error:
        return {
            "error": str(error)
        }