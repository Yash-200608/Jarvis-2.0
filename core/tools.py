TOOLS: dict[str, dict] = {
    "open_app": {
        "params": ["app_name"],
        "param_types": {"app_name": str},
        "risk": "low",
    },
    "delete_file": {
        "params": ["path"],
        "param_types": {"path": str},
        "risk": "high",
    },
    "move_to_trash": {
        "params": ["path"],
        "param_types": {"path": str},
        "risk": "medium",
    },
    "send_sms": {
        "params": ["phone_number", "message"],
        "param_types": {"phone_number": str, "message": str},
        "risk": "medium",
    },
    "send_email": {
        "params": ["to", "subject", "body"],
        "param_types": {"to": str, "subject": str, "body": str},
        "risk": "medium",
    },
    "send_whatsapp": {
        "params": ["phone_number", "message"],
        "param_types": {"phone_number": str, "message": str},
        "risk": "medium",
    },
    "shutdown_device": {
        "params": [],
        "param_types": {},
        "risk": "high",
    },
    "read_file": {
        "params": ["path"],
        "param_types": {"path": str},
        "risk": "low",
    },
}


def get_tool_schema(name: str) -> dict | None:
    return TOOLS.get(name)
