from pathlib import Path


DEFAULT_SAFE_OUTPUT_BASE = "data/exports"


def _resolve(path_str: str) -> Path:
    return Path(path_str).resolve()


def ensure_path_within_base(path_str: str, base_dir: str = DEFAULT_SAFE_OUTPUT_BASE) -> str:
    """
    Validates that a file path resolves under the allowed base directory.
    Returns the original path string for convenience.
    """
    path = _resolve(path_str)
    base = _resolve(base_dir)
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"path must be under {base}") from exc
    return path_str


def ensure_dir_within_base(dir_str: str, base_dir: str = DEFAULT_SAFE_OUTPUT_BASE) -> str:
    """
    Validates that a directory path resolves under the allowed base directory.
    Returns the original dir string for convenience.
    """
    return ensure_path_within_base(dir_str, base_dir=base_dir)
