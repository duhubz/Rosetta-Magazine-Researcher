"""Background service for checking and caching application updates."""

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

import app.config as cfg
from app.services import state
from app.utils import atomic_write_text, safe_urlopen
from app.version import __version__

logger = logging.getLogger(__name__)

STATE_FILENAME = ".update_check.json"
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
_status: dict[str, Any] | None = None
_status_lock = threading.Lock()
_FALLBACK_DOWNLOAD_URL = "https://github.com/duhubz/Rosetta-Magazine-Researcher/releases"


def parse_version(tag: str) -> tuple[int, int, int, int, str] | None:
    """Parse a release tag into a comparable three-component version tuple."""
    if not isinstance(tag, str):
        return None
    value = tag.strip()
    if value.startswith(("v", "V")):
        value = value[1:].strip()
    numeric, separator, prerelease = value.partition("-")
    components = numeric.split(".")
    if not components or len(components) > 3 or any(not part.isdigit() for part in components):
        return None
    numbers = [int(part) for part in components]
    numbers.extend([0] * (3 - len(numbers)))
    return (*numbers, int(not separator), prerelease if separator else "")


def is_newer(candidate_tag: str, current: str) -> bool:
    """Return whether a candidate release is newer than the current version."""
    candidate = parse_version(candidate_tag)
    current_version = parse_version(current)
    return candidate is not None and current_version is not None and candidate > current_version


def check_for_updates() -> dict[str, Any] | None:
    """Fetch the latest release and return its status, or None on failure."""
    try:
        response = safe_urlopen(cfg.update_check_url(), timeout=10.0)
        try:
            release = json.loads(response.read())
        finally:
            response.close()
        if not isinstance(release, dict) or not isinstance(release.get("tag_name"), str):
            raise ValueError("GitHub response is missing a string tag_name")
        tag_name = release["tag_name"]
        html_url = release.get("html_url", _FALLBACK_DOWNLOAD_URL)
        if not isinstance(html_url, str):
            html_url = _FALLBACK_DOWNLOAD_URL
        return {
            "update_available": is_newer(tag_name, __version__),
            "current_version": __version__,
            "latest_version": tag_name,
            "download_url": html_url,
        }
    except Exception as exc:
        logger.warning("Update check failed: %s", exc)
        return None


def _state_path() -> Path:
    return cfg.data_dir() / STATE_FILENAME


def _load_state() -> dict[str, Any] | None:
    """Load persisted update state, tolerating missing or corrupt state."""
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("Could not load update-check state: %s", exc)
        return None


def _save_state(status: dict[str, Any]) -> None:
    """Persist the update status and its fetch timestamp atomically."""
    data_dir = cfg.data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(_state_path(), json.dumps({"last_check": time.time(), "status": status}))


def get_update_status() -> dict[str, Any]:
    """Return the current cached update status."""
    with _status_lock:
        if _status is not None:
            return dict(_status)
    return {
        "update_available": False,
        "current_version": __version__,
        "latest_version": None,
        "download_url": None,
    }


def _set_status(status: dict[str, Any]) -> None:
    global _status
    with _status_lock:
        _status = dict(status)


def _run_check() -> None:
    """Run one update check, using persisted state when within the interval."""
    try:
        persisted = _load_state()
        now = time.time()
        if (
            isinstance(persisted, dict)
            and isinstance(persisted.get("last_check"), (int, float))
            and now - persisted["last_check"] < CHECK_INTERVAL_SECONDS
            and isinstance(persisted.get("status"), dict)
        ):
            # Recompute against the *running* version: the persisted status may
            # predate an app upgrade (a stale "update available" would otherwise
            # show for up to 24h after the user installs the new version).
            saved = persisted["status"]
            latest = saved.get("latest_version")
            _set_status(
                {
                    "update_available": is_newer(latest, __version__)
                    if isinstance(latest, str)
                    else False,
                    "current_version": __version__,
                    "latest_version": latest if isinstance(latest, str) else None,
                    "download_url": saved.get("download_url"),
                }
            )
            return
        if state.SHUTDOWN_EVENT.is_set():
            logger.debug("Skipping update check because shutdown is in progress")
            return
        status = check_for_updates()
        if status is not None:
            _set_status(status)
            _save_state(status)
    except Exception as exc:
        logger.warning("Update-check thread failed: %s", exc)


def start_update_check_thread() -> None:
    """Start the daemon thread that performs the startup update check."""
    if not cfg.update_check_enabled():
        logger.debug("Update checks are disabled")
        return
    thread = threading.Thread(target=_run_check, name="update-check", daemon=True)
    thread.start()
