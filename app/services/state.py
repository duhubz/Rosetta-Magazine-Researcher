"""Shared mutable state for the application."""

import os
import threading
import time

import app.config as cfg

# Global state
METADATA_CACHE: dict = {}
DOWNLOAD_STATE: dict = {}
LAST_PING: float = time.time()

_heartbeat_thread: threading.Thread | None = None


def start_heartbeat_monitor() -> None:
    """Start background thread that exits the process when browser tab is closed."""
    global _heartbeat_thread

    def monitor() -> None:
        shutdown_sec = cfg.heartbeat_shutdown_seconds()
        interval = cfg.heartbeat_check_interval()
        while True:
            time.sleep(interval)
            if time.time() - LAST_PING > shutdown_sec:
                os._exit(0)

    _heartbeat_thread = threading.Thread(target=monitor, daemon=True)
    _heartbeat_thread.start()
