import os
from urllib.parse import unquote


ALLOWED_ROOTS = ["C:/Users", "/home"]


def _normalize(path: str) -> str:
    decoded = unquote(path).replace("\\", os.sep).replace("/", os.sep)
    return os.path.normcase(os.path.realpath(os.path.abspath(decoded)))


def resolve_path(path: str) -> str | None:
    if not isinstance(path, str) or not path:
        return None

    try:
        normalized = _normalize(path)
        roots = [_normalize(root) for root in ALLOWED_ROOTS]
        if any(os.path.commonpath([normalized, root]) == root for root in roots):
            return normalized
    except (OSError, ValueError):
        return None

    return None


def validate_path(path: str) -> bool:
    return resolve_path(path) is not None
