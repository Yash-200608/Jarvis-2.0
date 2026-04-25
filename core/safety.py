from copy import deepcopy


def apply_safety(action: dict) -> dict:
    action = deepcopy(action)
    name = action.get("name")
    action.setdefault("params", {})

    if name == "delete_file":
        action["name"] = "move_to_trash"
    elif name in {"send_email", "send_sms", "send_whatsapp"}:
        action["preview"] = True

    return action
