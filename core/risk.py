from core.tools import TOOLS


def get_risk_level(action_name: str) -> str:
    tool = TOOLS.get(action_name)
    if tool is None:
        return "high"

    risk = tool.get("risk")
    if not isinstance(risk, str):
        return "high"

    return risk
