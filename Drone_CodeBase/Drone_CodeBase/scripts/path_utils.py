from pathlib import Path


def resolve_path(root: Path, path_value) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return root / path
