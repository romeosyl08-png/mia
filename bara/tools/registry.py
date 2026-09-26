TOOLS = {}


def register_tool(name, description, parameters):
    """
    Enregistre un outil utilisable par l'agent.
    """

    def decorator(function):
        TOOLS[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "function": function,
        }

        return function

    return decorator


def get_tools():
    """
    Retourne les outils disponibles.
    """

    return list(TOOLS.values())