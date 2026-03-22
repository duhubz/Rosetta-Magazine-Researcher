"""Catalog loading and merging from official and community sources."""

import json
import logging
import urllib.request

import app.config as cfg

logger = logging.getLogger(__name__)


def get_all_catalogs() -> list:
    catalogs = []
    catalog_urls = cfg.catalog_urls()
    catalog_file = cfg.catalog_file()
    catalogs_dir = cfg.catalogs_dir()
    timeout = cfg.catalog_fetch_timeout()

    # 1. Main Official Catalog (with fallback backups)
    official_loaded = False
    if catalog_urls:
        urls_to_try = catalog_urls if isinstance(catalog_urls, list) else [catalog_urls]
        for url in urls_to_try:
            if not url:
                continue
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                        "Accept": "application/json, text/plain, */*",
                    },
                )
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    raw_data = r.read().decode("utf-8")
                    main_data = json.loads(raw_data)
                    catalogs.extend(
                        main_data.get("items", main_data)
                        if isinstance(main_data, dict)
                        else main_data
                    )
                    official_loaded = True
                    try:
                        catalog_file.write_text(raw_data, encoding="utf-8")
                    except Exception:
                        pass
                    break
            except Exception as e:
                logger.warning("Failed to load catalog from %s: %s", url, e)
                continue

    if not official_loaded and catalog_file.exists():
        try:
            main_data = json.loads(catalog_file.read_text(encoding="utf-8"))
            catalogs.extend(
                main_data.get("items", main_data)
                if isinstance(main_data, dict)
                else main_data
            )
        except Exception as e:
            logger.warning("Failed to load local catalog: %s", e)

    # 2. Custom Community Catalogs
    catalogs_dir.mkdir(parents=True, exist_ok=True)
    for c_file in catalogs_dir.glob("*.json"):
        try:
            c_data = json.loads(c_file.read_text(encoding="utf-8"))
            if isinstance(c_data, dict) and "update_url" in c_data:
                try:
                    req = urllib.request.Request(
                        c_data["update_url"],
                        headers={
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                        },
                    )
                    with urllib.request.urlopen(req, timeout=timeout) as r:
                        new_data = json.loads(r.read().decode("utf-8"))
                        c_file.write_text(
                            json.dumps(new_data, indent=4), encoding="utf-8"
                        )
                        c_data = new_data
                except Exception:
                    pass

            items = c_data.get("items", c_data) if isinstance(c_data, dict) else c_data
            catalogs.extend(items)
        except Exception as e:
            logger.warning("Failed to load custom catalog %s: %s", c_file, e)

    return catalogs
