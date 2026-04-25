def apply_safety(action: dict) -> dict:
    name = action.get("name")
    params = action.setdefault("params", {})

    if name == "delete_file":
        action["name"] = "move_to_trash"
    elif name == "shutdown_device":
        params["delay"] = 5
    elif name in {"send_email", "send_sms", "send_whatsapp"}:
        action["preview"] = True

    return action
