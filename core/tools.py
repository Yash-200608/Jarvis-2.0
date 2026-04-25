TOOLS: dict[str, dict[str, list[str] | str]] = {
    "open_app": {
        "params": ["app_name"],
        "risk": "low",
    },
    "delete_file": {
        "params": ["path"],
        "risk": "high",
    },
    "send_sms": {
        "params": ["phone_number", "message"],
        "risk": "medium",
    },
    "send_email": {
        "params": ["to", "subject", "body"],
        "risk": "medium",
    },
    "shutdown_device": {
        "params": [],
        "risk": "high",
    },
    "read_file": {
        "params": ["path"],
        "risk": "low",
    },
}


def get_tool_schema(name: str) -> dict | None:
    return TOOLS.get(name)
