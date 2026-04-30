"""
State Service
Maintains the global mutable state for the application during its lifecycle.
Includes the metadata cache, download progress tracking, and the idle-shutdown monitor.
"""

import os
import threading
import time
from typing import Any, Optional

import app.config as cfg

# --- Global Caches & Tracking ---

# Stores parsed metadata for all local PDFs. Key: relative path, Value: dict of fields.
METADATA_CACHE: dict[str, dict[str, str]] = {}

# Tracks active download tasks. Key: item_id, Value: status/progress/error dict.
DOWNLOAD_STATE: dict[str, dict[str, Any]] = {}

# Timestamp of the last 'ping' received from the browser UI.
LAST_PING: float = time.time()

# Internal reference to the heartbeat thread
_heartbeat_thread: Optional[threading.Thread] = None


def start_heartbeat_monitor() -> None:
    """
    Starts a background thread that monitors the 'LAST_PING' timestamp.
    
    If the browser tab is closed, the UI stops sending pings. After the 
    threshold defined in config (default 20s), this thread will trigger 
    a process exit to free up system memory.
    """
    global _heartbeat_thread

    def monitor() -> None:
        shutdown_sec = cfg.heartbeat_shutdown_seconds()
        interval = cfg.heartbeat_check_interval()
        while True:
            time.sleep(interval)
            # If the gap between now and the last ping exceeds our limit, shut down.
            if time.time() - LAST_PING > shutdown_sec:
                # Use os._exit(0) to ensure the entire process tree closes immediately.
                os._exit(0)

    _heartbeat_thread = threading.Thread(target=monitor, daemon=True)
    _heartbeat_thread.start()