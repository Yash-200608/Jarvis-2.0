import os
from urllib.parse import unquote


ALLOWED_ROOTS = ["C:/Users", "/home"]


def _normalize(path: str) -> str:
    decoded = unquote(path).replace("\\", os.sep).replace("/", os.sep)
    return os.path.normcase(os.path.realpath(os.path.abspath(decoded)))


def validate_path(path: str) -> bool:
    if not isinstance(path, str) or not path:
        return False

    try:
        normalized = _normalize(path)
        roots = [_normalize(root) for root in ALLOWED_ROOTS]
        return any(os.path.commonpath([normalized, root]) == root for root in roots)
    except (OSError, ValueError):
        return False
