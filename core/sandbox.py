ALLOWED_ROOTS = ["C:/Users", "/home"]


def validate_path(path: str) -> bool:
    if ".." in path:
        return False

    return any(path.startswith(root) for root in ALLOWED_ROOTS)
