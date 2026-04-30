"""
Configuration Loader
Handles portable path resolution and deep-merging of yaml configurations.
"""

import sys
import os
from pathlib import Path
from typing import Any, Optional  # Added Optional here to fix the NameError

# --- PORTABLE PATH LOGIC ---
if getattr(sys, "frozen", False):
    # If running as a bundled executable
    exe_path = Path(sys.executable).resolve()
    
    # MAC BUNDLE FIX: 
    # If we are inside a Mac .app (Rosetta.app/Contents/MacOS/Rosetta)
    # we need to step up 3 levels to get to the folder containing the .app
    if "Contents/MacOS" in str(exe_path):
        ROOT_DIR = exe_path.parents[3]
    else:
        ROOT_DIR = exe_path.parent
else:
    # If running in a dev environment
    ROOT_DIR = Path(__file__).resolve().parent.parent

_config: Optional[dict[str, Any]] = None

def _default_config() -> dict[str, Any]:
    """Provides fallback values if config.yaml is missing."""
    return {
        "server": {"port": 18028, "dev_mode": False},
        "paths": {
            "data_dir": "Magazines",
            "bookmarks_file": "bookmarks.json",
            "catalog_file": "catalog.json",
            "catalogs_dir": "Catalogs",
            "covers_dir": "Covers",
        },
        "catalog": {
            "urls": [
                "https://www.gamingalexandria.com/ga-researcher/catalog.json",
                "https://archive.org/download/ga-researcher-files/catalog.json",
            ]
        },
        "download": {
            "timeout_seconds": 60,
            "catalog_fetch_timeout": 10,
            "cover_fetch_timeout": 5,
        },
        "heartbeat": {
            "shutdown_after_idle_seconds": 20,
            "check_interval_seconds": 5,
        },
    }

def get_config() -> dict[str, Any]:
    """Return the loaded configuration (cached)."""
    global _config
    if _config is None:
        try:
            import yaml
            config = _default_config()
            for filename in ("config.yaml", "config.local.yaml"):
                p = ROOT_DIR / filename
                if p.exists():
                    with open(p, encoding="utf-8") as f:
                        loaded = yaml.safe_load(f) or {}
                        # Recursive update
                        for k, v in loaded.items():
                            if isinstance(v, dict) and k in config:
                                config[k].update(v)
                            else:
                                config[k] = v
            _config = config
        except ImportError:
            # Fallback if PyYAML is not installed
            _config = _default_config()
    return _config

def get_path(key: str) -> Path:
    """Resolve a path key (e.g. data_dir) to an absolute Path relative to ROOT_DIR."""
    paths = get_config().get("paths", {})
    value = paths.get(key, "")
    return (ROOT_DIR / value).resolve()

# --- CONVENIENCE ACCESSORS ---
def data_dir() -> Path: return get_path("data_dir")
def bookmarks_file() -> Path: return get_path("bookmarks_file")
def catalog_file() -> Path: return get_path("catalog_file")
def catalogs_dir() -> Path: return get_path("catalogs_dir")
def covers_dir() -> Path: return get_path("covers_dir")
def catalog_urls() -> list[str]: return get_config().get("catalog", {}).get("urls", [])
def server_port() -> int: return int(get_config().get("server", {}).get("port", 18028))
def server_dev_mode() -> bool: return bool(get_config().get("server", {}).get("dev_mode", False))
def download_timeout() -> int: return int(get_config().get("download", {}).get("timeout_seconds", 60))
def catalog_fetch_timeout() -> int: return int(get_config().get("download", {}).get("catalog_fetch_timeout", 10))
def cover_fetch_timeout() -> int: return int(get_config().get("download", {}).get("cover_fetch_timeout", 5))
def heartbeat_shutdown_seconds() -> int: return int(get_config().get("heartbeat", {}).get("shutdown_after_idle_seconds", 20))
def heartbeat_check_interval() -> int: return int(get_config().get("heartbeat", {}).get("check_interval_seconds", 5))