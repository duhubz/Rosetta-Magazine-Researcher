"""
State Service
Maintains the global mutable state for the application during its lifecycle.
Includes the metadata cache, download progress tracking, and the idle-shutdown monitor.
"""

import contextlib
import os
import secrets
import threading
import time
from typing import Any, Iterator, Optional

import app.config as cfg

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

# Set when the idle monitor decides to shut down; long-running work
# (downloads, extractions) should check this and abort cleanly.
SHUTDOWN_EVENT = threading.Event()

# Counter of writes currently in progress, so shutdown can wait for them.
_write_lock = threading.Lock()
_writes_in_progress = 0

# Internal reference to the heartbeat thread
_heartbeat_thread: Optional[threading.Thread] = None


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


def start_heartbeat_monitor() -> None:
    """
    Starts a background thread that monitors the 'LAST_PING' timestamp.
    
    If the browser tab is closed, the UI stops sending pings. After the 
    threshold defined in config (default 180s), this thread signals 
    SHUTDOWN_EVENT, waits briefly for in-progress writes to finish, and 
    then exits the process to free up system memory.
    """
    global _heartbeat_thread

    def monitor() -> None:
        shutdown_sec = cfg.heartbeat_shutdown_seconds()
        interval = cfg.heartbeat_check_interval()
        while True:
            time.sleep(interval)
            # If the gap between now and the last ping exceeds our limit, shut down.
            if time.time() - LAST_PING > shutdown_sec:
                # Cooperative shutdown: signal workers, then give in-progress
                # writes a 5-second grace period to complete.
                SHUTDOWN_EVENT.set()
                deadline = time.time() + 5.0
                while writes_in_progress() > 0 and time.time() < deadline:
                    time.sleep(0.1)
                # Use os._exit(0) to ensure the entire process tree closes.
                os._exit(0)

    _heartbeat_thread = threading.Thread(target=monitor, daemon=True)
    _heartbeat_thread.start()