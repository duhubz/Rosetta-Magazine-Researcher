"""Configuration loader for Rosetta Magazine Researcher."""

import sys
from pathlib import Path

# Portable path logic: when frozen (PyInstaller), root is next to executable
if getattr(sys, "frozen", False):
    ROOT_DIR = Path(sys.executable).parent
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent

_config: dict | None = None


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge two config dictionaries."""
    merged = dict(base)
    for key, value in override.items():
        base_value = merged.get(key)
        if isinstance(base_value, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(base_value, value)
        else:
            merged[key] = value
    return merged


def _load_raw() -> dict:
    """Load config from config.yaml plus optional config.local.yaml overrides."""
    try:
        import yaml
    except ImportError:
        return _default_config()

    config = _default_config()
    for filename in ("config.yaml", "config.local.yaml"):
        config_path = ROOT_DIR / filename
        if not config_path.exists():
            continue
        try:
            with open(config_path, encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if isinstance(loaded, dict):
                config = _deep_merge(config, loaded)
        except Exception:
            continue
    return config


def _default_config() -> dict:
    """Default configuration when no config file exists."""
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


def get_config() -> dict:
    """Return the loaded configuration (cached)."""
    global _config
    if _config is None:
        _config = _load_raw()
    return _config


def get_path(key: str) -> Path:
    """Resolve a path key (e.g. data_dir, bookmarks_file) to an absolute Path."""
    paths = get_config().get("paths", {})
    value = paths.get(key, "")
    return (ROOT_DIR / value).resolve()


# Convenience accessors
def data_dir() -> Path:
    return get_path("data_dir")


def bookmarks_file() -> Path:
    return get_path("bookmarks_file")


def catalog_file() -> Path:
    return get_path("catalog_file")


def catalogs_dir() -> Path:
    return get_path("catalogs_dir")


def covers_dir() -> Path:
    return get_path("covers_dir")


def catalog_urls() -> list[str]:
    urls = get_config().get("catalog", {}).get("urls", [])
    return urls if isinstance(urls, list) else [urls]


def server_port() -> int:
    return int(get_config().get("server", {}).get("port", 18028))


def server_dev_mode() -> bool:
    return bool(get_config().get("server", {}).get("dev_mode", False))


def download_timeout() -> int:
    return int(get_config().get("download", {}).get("timeout_seconds", 60))


def catalog_fetch_timeout() -> int:
    return int(get_config().get("download", {}).get("catalog_fetch_timeout", 10))


def cover_fetch_timeout() -> int:
    return int(get_config().get("download", {}).get("cover_fetch_timeout", 5))


def heartbeat_shutdown_seconds() -> int:
    return int(
        get_config().get("heartbeat", {}).get("shutdown_after_idle_seconds", 20)
    )


def heartbeat_check_interval() -> int:
    return int(
        get_config().get("heartbeat", {}).get("check_interval_seconds", 5)
    )
