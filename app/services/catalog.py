"""
Catalog Service
Handles loading and merging magazine lists from official and community sources.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Any

import app.config as cfg
from app.utils import URLBlockedError, atomic_write_text, safe_urlopen

logger = logging.getLogger(__name__)

# --- mtime-invalidated parse cache -------------------------------------------
# Maps file path -> (mtime, parsed item list). Guarded by _CACHE_LOCK; bumping
# _cache_version whenever an entry is (re)parsed or dropped lets the by-id
# index know when it must be rebuilt.
_CACHE_LOCK = threading.Lock()
_CATALOG_CACHE: dict[str, tuple[float, list]] = {}
_cache_version: int = 0
_BY_ID: dict[str, dict[str, Any]] = {}
_by_id_version: int = -1


def _parse_catalog_text(raw: str) -> list[dict[str, Any]]:
    """Parses catalog JSON text into a flat item list."""
    data = json.loads(raw)
    items = data.get("items", data) if isinstance(data, dict) else data
    return list(items) if isinstance(items, list) else []


def _load_catalog_file(path: Path) -> list[dict[str, Any]]:
    """Reads and parses a catalog file, reusing the parsed result while the
    file's mtime is unchanged."""
    global _cache_version
    key = str(path)
    mtime = path.stat().st_mtime
    with _CACHE_LOCK:
        entry = _CATALOG_CACHE.get(key)
        if entry is not None and entry[0] == mtime:
            return entry[1]
    items = _parse_catalog_text(path.read_text(encoding="utf-8"))
    with _CACHE_LOCK:
        _CATALOG_CACHE[key] = (mtime, items)
        _cache_version += 1
    return items


def _store_parsed(path: Path, items: list[dict[str, Any]]) -> None:
    """Caches freshly written catalog content under the file's new mtime."""
    global _cache_version
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return
    with _CACHE_LOCK:
        _CATALOG_CACHE[str(path)] = (mtime, items)
        _cache_version += 1


def _prune_missing(live_paths: set[str]) -> None:
    """Drops cache entries whose backing files disappeared."""
    global _cache_version
    with _CACHE_LOCK:
        stale = [k for k in _CATALOG_CACHE if k not in live_paths]
        for k in stale:
            del _CATALOG_CACHE[k]
        if stale:
            _cache_version += 1


def get_catalog_index(force_refresh: bool = False) -> dict[str, dict[str, Any]]:
    """
    Returns a {str(item_id): item} index over all catalogs for O(1) lookups
    (used by /api/cover). Rebuilt only when the underlying parse cache changed.
    """
    global _BY_ID, _by_id_version
    items = get_all_catalogs(force_refresh=force_refresh)
    with _CACHE_LOCK:
        if _by_id_version == _cache_version:
            return _BY_ID
    index = {str(i["id"]): i for i in items if isinstance(i, dict) and i.get("id") is not None}
    with _CACHE_LOCK:
        _BY_ID = index
        _by_id_version = _cache_version
        return _BY_ID


def get_all_catalogs(force_refresh: bool = False) -> list[dict[str, Any]]:
    """
    Fetches and merges all available magazine catalogs.
    """
    catalogs: list[dict[str, Any]] = []
    catalog_urls = cfg.catalog_urls()
    catalog_file = cfg.catalog_file()
    catalogs_dir = cfg.catalogs_dir()
    timeout = cfg.catalog_fetch_timeout()

    # Common User-Agent to avoid being blocked by mirrors
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    # 1. Main Official Catalog
    official_loaded = False
    if force_refresh and catalog_urls:
        for url in catalog_urls if isinstance(catalog_urls, list) else [catalog_urls]:
            if not url:
                continue
            try:
                # safe_urlopen validates the URL and every redirect hop
                # against the scheme/host allowlist.
                with safe_urlopen(url, timeout=timeout, headers=headers) as r:
                    raw_data = r.read().decode("utf-8")
                    items = _parse_catalog_text(raw_data)
                    catalogs.extend(items)
                    official_loaded = True
                    # Update local cache (file + parsed-entry cache)
                    atomic_write_text(catalog_file, raw_data)
                    _store_parsed(catalog_file, items)
                    break
            except URLBlockedError as e:
                logger.warning(f"Blocked catalog URL ({e.reason}): {e.url}")
            except Exception as e:
                logger.warning(f"Could not refresh official catalog from {url}: {e}")

    # Load from local cache if we didn't refresh or refresh failed
    live_paths: set[str] = set()
    if official_loaded:
        live_paths.add(str(catalog_file))
    elif catalog_file.exists():
        live_paths.add(str(catalog_file))
        try:
            catalogs.extend(_load_catalog_file(catalog_file))
        except Exception as e:
            logger.error(f"Failed to read local catalog file: {e}")

    # 2. Custom Community Catalogs
    catalogs_dir.mkdir(parents=True, exist_ok=True)
    for c_file in catalogs_dir.glob("*.json"):
        live_paths.add(str(c_file))
        try:
            # Auto-update community catalogs if update_url is present
            if force_refresh:
                try:
                    c_data = json.loads(c_file.read_text(encoding="utf-8"))
                except Exception:
                    c_data = None
                if isinstance(c_data, dict) and "update_url" in c_data:
                    try:
                        with safe_urlopen(
                            c_data["update_url"], timeout=timeout, headers=headers
                        ) as r:
                            new_raw = r.read().decode("utf-8")
                            new_data = json.loads(new_raw)
                            atomic_write_text(c_file, json.dumps(new_data, indent=4))
                            _store_parsed(c_file, _parse_catalog_text(json.dumps(new_data)))
                    except URLBlockedError as e:
                        logger.warning(f"Blocked update_url in {c_file.name} ({e.reason}): {e.url}")
                    except Exception:
                        pass

            catalogs.extend(_load_catalog_file(c_file))
        except Exception as e:
            logger.warning(f"Error loading custom catalog {c_file.name}: {e}")

    # Drop parse-cache entries for files that no longer exist on disk.
    _prune_missing(live_paths)

    return catalogs
