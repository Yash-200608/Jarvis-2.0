from core.tools import get_tool_schema


def validate_action(action: dict) -> bool:
    if not isinstance(action, dict):
        return False

    if "name" not in action or "params" not in action:
        return False

    name = action["name"]
    if not isinstance(name, str) or not name:
        return False

    params = action["params"]
    if not isinstance(params, dict):
        return False

    schema = get_tool_schema(name)
    if schema is None:
        return False

    required_params = schema["params"]
    if not isinstance(required_params, list) or not all(
        isinstance(param, str) for param in required_params
    ):
        return False

    if set(params) != set(required_params):
        return False

    param_types = schema.get("param_types", {})
    for param in required_params:
        expected_type = param_types.get(param, str)
        value = params[param]
        if not isinstance(value, expected_type):
            return False
        if expected_type is str and not value:
            return False

    return True
