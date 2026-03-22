"""Utility functions."""

from pathlib import Path

import app.config as cfg


def get_safe_path(rel_path: str) -> Path:
    """Resolve a relative path safely, preventing directory traversal."""
    data_dir = cfg.data_dir()
    p = (data_dir / rel_path).resolve()
    if not str(p).startswith(str(data_dir.resolve())):
        raise ValueError("Unsafe path traversal detected.")
    return p
