"""Mega public-share downloads."""

from pathlib import Path
from urllib.parse import urlparse

import app.config as cfg


MEGA_HOSTS = {"mega.nz", "mega.co.nz", "mega.io"}


def is_public_share_url(url: str) -> bool:
    """Return whether ``url`` is a supported public Mega file share."""
    try:
        parsed = urlparse(str(url))
    except Exception:
        return False
    return (
        parsed.scheme in ("http", "https")
        and (parsed.hostname or "").lower() in MEGA_HOSTS
        and parsed.path.startswith("/file/")
        and bool(parsed.fragment)
    )


def download_public_file(url: str, out_path: Path) -> None:
    """Download and decrypt a public Mega file into ``out_path``."""
    if not is_public_share_url(url):
        raise ValueError("Unsupported Mega public file URL")

    try:
        from mega import Mega
    except ImportError as exc:
        raise RuntimeError(
            "Mega downloads require the optional mega.py dependency."
        ) from exc

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    client = Mega().login()
    client.timeout = cfg.download_timeout()
    downloaded = client.download_url(
        url,
        dest_path=str(out_path.parent),
        dest_filename=out_path.name,
    )
    if not out_path.exists():
        raise RuntimeError(
            f"Mega client did not create the expected output file: {downloaded}"
        )
