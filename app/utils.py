"""Utility functions."""

import os
from pathlib import Path
from urllib.parse import urlparse

import app.config as cfg


def get_safe_path(rel_path: str) -> Path:
    """Resolve a relative path safely, preventing directory traversal."""
    data_dir = cfg.data_dir().resolve()
    p = (data_dir / rel_path).resolve()
    if not p.is_relative_to(data_dir):
        raise ValueError("Unsafe path traversal detected.")
    return p


def has_hidden_component(path: Path, base: Path) -> bool:
    """
    True when any component of `path` (relative to `base`) starts with a dot.

    Used to keep dot-directories/files (e.g. '.temp_<id>' in-flight download
    folders and the '.rosetta_index.db' search index) out of library scans.
    """
    try:
        rel = path.relative_to(base)
    except ValueError:
        rel = path
    return any(part.startswith(".") for part in rel.parts)


def safe_name(name: str, default: str = "") -> str:
    """
    Sanitize an untrusted (e.g. catalog-derived) filename or folder name.

    Returns only the basename component, with both '/' and '\\' treated
    as path separators. Rejects empty names, dot-names ('.', '..'), and
    hidden names (leading dot). Raises ValueError on unsafe input.
    """
    raw = str(name) if name else str(default)
    # Normalize Windows-style separators so Path.name works cross-platform.
    base = Path(raw.replace("\\", "/")).name.strip()
    if base in ("", ".", "..") or base.startswith("."):
        raise ValueError(f"Unsafe filename: {name!r}")
    return base


def atomic_write_text(path: Path, data: str, encoding: str = "utf-8") -> None:
    """Write text to `path` atomically (temp file + os.replace)."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding=encoding)
    os.replace(tmp, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to `path` atomically (temp file + os.replace)."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def is_allowed_fetch_url(url: str) -> bool:
    """
    Validate a URL the backend is about to fetch.

    Only http/https schemes are allowed, and the host must match the
    configured allowlist (config.yaml: security.allowed_fetch_hosts).
    Entries starting with '*.' match any subdomain of the given domain.
    """
    try:
        parsed = urlparse(str(url))
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    for allowed in cfg.allowed_fetch_hosts():
        allowed = str(allowed).lower().strip()
        if not allowed:
            continue
        if allowed.startswith("*."):
            root = allowed[2:]
            if host == root or host.endswith("." + root):
                return True
        elif host == allowed:
            return True
    return False
