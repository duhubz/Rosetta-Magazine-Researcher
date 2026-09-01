"""
State Service
Maintains the global mutable state for the application during its lifecycle.
Includes the metadata cache, download progress tracking, and the idle-shutdown monitor.
"""

import contextlib
import logging
import os
import secrets
import threading
import time
from collections.abc import Iterator
from typing import Any

import app.config as cfg

logger = logging.getLogger(__name__)

# --- Global Caches & Tracking ---

# Per-launch session token. The UI reads it from a meta tag and echoes it
# back in the X-Rosetta-Token header on all non-GET requests (CSRF defense).
SESSION_TOKEN: str = secrets.token_urlsafe(32)

# Stores parsed metadata for all local PDFs. Key: relative path, Value: dict of fields.
METADATA_CACHE: dict[str, dict[str, str]] = {}

# Tracks active download tasks. Key: item_id, Value: status/progress/error dict.
DOWNLOAD_STATE: dict[str, dict[str, Any]] = {}

# Timestamp of the last 'ping' received from the browser UI.
LAST_PING: float = time.time()

# Registry of browser tabs currently showing the app.
# Key: opaque per-page-load tab id from the frontend; Value: last ping time.
ACTIVE_TABS: dict[str, float] = {}
_tabs_lock = threading.Lock()

# Timestamp of the most recent explicit "tab closing" notification (the
# pagehide beacon), or None if no tab has ever said goodbye.
LAST_CLOSE: float | None = None

# Set when the idle monitor decides to shut down; long-running work
# (downloads, extractions) should check this and abort cleanly.
SHUTDOWN_EVENT = threading.Event()

# Counter of writes currently in progress, so shutdown can wait for them.
_write_lock = threading.Lock()
_writes_in_progress = 0

# Internal reference to the heartbeat thread
_heartbeat_thread: threading.Thread | None = None


@contextlib.contextmanager
def write_in_progress() -> Iterator[None]:
    """Context manager marking a disk-write critical section.

    The idle-shutdown monitor waits (up to a grace period) for all such
    sections to finish before exiting, so files aren't truncated mid-write.
    """
    global _writes_in_progress
    with _write_lock:
        _writes_in_progress += 1
    try:
        yield
    finally:
        with _write_lock:
            _writes_in_progress -= 1


def writes_in_progress() -> int:
    """Returns the number of write critical-sections currently active."""
    with _write_lock:
        return _writes_in_progress


def record_ping(tab_id: str | None, closing: bool = False) -> None:
    """Records a heartbeat from the browser UI.

    Regular pings (re)register the tab and refresh LAST_PING. A `closing`
    ping (sent via navigator.sendBeacon on pagehide) deregisters the tab
    instead, which lets the idle monitor exit shortly after the last tab
    closes rather than waiting out the full idle threshold.
    """
    global LAST_PING, LAST_CLOSE
    now = time.time()
    if closing:
        LAST_CLOSE = now
        if tab_id:
            with _tabs_lock:
                ACTIVE_TABS.pop(tab_id, None)
        return
    LAST_PING = now
    if tab_id:
        with _tabs_lock:
            ACTIVE_TABS[tab_id] = now


def should_shutdown(now: float | None = None) -> bool:
    """Decides whether the idle-shutdown monitor should exit the process.

    Three rules, checked in order:

    1. While any registered tab has pinged within the idle threshold the
       server stays up. This includes hidden/background tabs — browsers
       throttle their timers to roughly once a minute, still comfortably
       inside the default 180s threshold. Tabs silent for longer than the
       threshold (browser crash, killed process, lost beacon) are pruned.
    2. Failsafe: if nothing at all has pinged within the threshold
       (browser never opened, crashed before goodbye), shut down.
    3. Quick exit: if every tab said goodbye (pagehide beacon) and no ping
       has arrived since, shut down after a short grace period. The grace
       keeps a page refresh alive: its goodbye is followed by an immediate
       re-register from the reloaded page.
    """
    if now is None:
        now = time.time()
    threshold = cfg.heartbeat_shutdown_seconds()
    with _tabs_lock:
        for tab_id, last in list(ACTIVE_TABS.items()):
            if now - last > threshold:
                del ACTIVE_TABS[tab_id]
        if ACTIVE_TABS:
            return False
    if now - LAST_PING > threshold:
        return True
    return (
        LAST_CLOSE is not None
        and LAST_CLOSE >= LAST_PING
        and now - LAST_CLOSE > cfg.heartbeat_close_grace_seconds()
    )


def start_heartbeat_monitor() -> None:
    """
    Starts a background thread that monitors browser heartbeats.

    The server stays alive while any tab is open (see should_shutdown for
    the exact rules). Once the decision to exit is made, this thread signals
    SHUTDOWN_EVENT, waits briefly for in-progress writes to finish, and
    then exits the process to free up system memory.
    """
    global _heartbeat_thread

    def monitor() -> None:
        interval = cfg.heartbeat_check_interval()
        while True:
            time.sleep(interval)
            if should_shutdown():
                # Cooperative shutdown: signal workers, then give in-progress
                # writes a 5-second grace period to complete.
                SHUTDOWN_EVENT.set()
                deadline = time.time() + 5.0
                while writes_in_progress() > 0 and time.time() < deadline:
                    time.sleep(0.1)
                # Release cached resources before the hard exit: open PDF
                # handles and the search-index SQLite connection (flushes WAL).
                try:
                    from app.services import pdf_cache, search_index

                    pdf_cache.close_all()
                    search_index.close_index()
                except Exception as e:
                    # Best-effort cleanup just before os._exit; nothing to do
                    # beyond noting it for debug runs.
                    logger.debug("Shutdown cleanup failed: %s", e)
                # Use os._exit(0) to ensure the entire process tree closes.
                os._exit(0)

    _heartbeat_thread = threading.Thread(target=monitor, daemon=True)
    _heartbeat_thread.start()
