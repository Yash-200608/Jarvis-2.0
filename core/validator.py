from core.tools import get_tool_schema


def validate_action(action: dict) -> bool:
    if "name" not in action or "params" not in action:
        return False

    params = action["params"]
    if not isinstance(params, dict):
        return False

    schema = get_tool_schema(action["name"])
    if schema is None:
        return False

    required_params = schema["params"]
    return all(param in params for param in required_params)
