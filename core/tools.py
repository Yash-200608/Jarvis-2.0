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
    # Priority #4 Milestone 6 -- the PC skill subset's kernel-side tool
    # entries (jarvis_node_sdk/pc_skills.py maps pc.* skills onto these).
    "lock_device": {
        "params": [],
        "param_types": {},
        "risk": "low",
    },
    "media_control": {
        "params": ["command"],
        "param_types": {"command": str},
        "risk": "low",
    },
    "run_command": {
        "params": ["command"],
        "param_types": {"command": str},
        "risk": "high",
    },
}


def get_tool_schema(name: str) -> dict | None:
    return TOOLS.get(name)
