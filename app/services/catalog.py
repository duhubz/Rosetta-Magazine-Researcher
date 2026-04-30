"""
Catalog Service
Handles loading and merging magazine lists from official and community sources.
"""

import json
import logging
import urllib.request
from typing import Any

import app.config as cfg

logger = logging.getLogger(__name__)

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
        for url in (catalog_urls if isinstance(catalog_urls, list) else [catalog_urls]):
            if not url: continue
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    raw_data = r.read().decode("utf-8")
                    main_data = json.loads(raw_data)
                    items = main_data.get("items", main_data) if isinstance(main_data, dict) else main_data
                    catalogs.extend(items)
                    official_loaded = True
                    # Update local cache
                    catalog_file.write_text(raw_data, encoding="utf-8")
                    break
            except Exception as e:
                logger.warning(f"Could not refresh official catalog from {url}: {e}")

    # Load from local cache if we didn't refresh or refresh failed
    if not official_loaded and catalog_file.exists():
        try:
            main_data = json.loads(catalog_file.read_text(encoding="utf-8"))
            items = main_data.get("items", main_data) if isinstance(main_data, dict) else main_data
            catalogs.extend(items)
        except Exception as e:
            logger.error(f"Failed to read local catalog file: {e}")

    # 2. Custom Community Catalogs
    catalogs_dir.mkdir(parents=True, exist_ok=True)
    for c_file in catalogs_dir.glob("*.json"):
        try:
            c_data = json.loads(c_file.read_text(encoding="utf-8"))
            # Auto-update community catalogs if update_url is present
            if force_refresh and isinstance(c_data, dict) and "update_url" in c_data:
                try:
                    req = urllib.request.Request(c_data["update_url"], headers=headers)
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        new_data = json.loads(r.read().decode("utf-8"))
                        c_file.write_text(json.dumps(new_data, indent=4), encoding="utf-8")
                        c_data = new_data
                except Exception: pass

            items = c_data.get("items", c_data) if isinstance(c_data, dict) else c_data
            catalogs.extend(items)
        except Exception as e:
            logger.warning(f"Error loading custom catalog {c_file.name}: {e}")

    return catalogs