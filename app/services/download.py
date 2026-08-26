"""
Download Service
Handles background downloading of PDFs and Data ZIPs from community catalogs.
Features a "waterfall" download system to try multiple mirror URLs.
"""

import logging
import os
import shutil
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import app.config as cfg
from app.services import metadata, state, zip_utils
from app.utils import atomic_write_text, is_allowed_fetch_url, safe_name


def _download_url_allowed(url: str) -> tuple[bool, str]:
    """
    Decide whether a catalog-provided download URL may be fetched.

    Returns (allowed, reason); `reason` is a short human-readable string
    ("scheme not http/https" / "host not in allowlist") when blocked.

    http/https is enforced unconditionally. The host allowlist (reusing
    utils.is_allowed_fetch_url and config security.allowed_fetch_hosts) can
    be bypassed via security.allow_downloads_from_any_host for users with
    private mirrors -- but never the scheme check.
    """
    try:
        scheme = urlparse(str(url)).scheme
    except Exception:
        return False, "scheme not http/https"
    if scheme not in ("http", "https"):
        return False, "scheme not http/https"
    if cfg.allow_downloads_from_any_host():
        return True, ""
    if is_allowed_fetch_url(url):
        return True, ""
    return False, "host not in allowlist"


def download_waterfall(task_id: str, out_path: Path, sources: list[str], file_type: str) -> bool:
    """
    Attempts to download a file by trying a list of mirror URLs in order.
    
    Updates the global DOWNLOAD_STATE with progress for the UI.
    """
    if not sources:
        return True
        
    timeout = cfg.download_timeout()
    for entry in sources:
        # Sources are normally plain URL strings, but tolerate per-source
        # dict shapes like {"url": ...} from community catalogs.
        url = entry.get("url") if isinstance(entry, dict) else entry
        if not url or not isinstance(url, str):
            logging.warning("Skipping malformed %s source entry: %r", file_type, entry)
            continue

        allowed, reason = _download_url_allowed(url)
        if not allowed:
            logging.warning("Blocked %s download URL (%s): %s", file_type, reason, url)
            continue

        state.DOWNLOAD_STATE[task_id]["status"] = f"Downloading {file_type}..."
        state.DOWNLOAD_STATE[task_id]["progress"] = 0

        # Cache-busting parameter to prevent stale downloads from CDNs
        cb_param = f"nocache={int(time.time() * 1000)}"
        busted_url = f"{url}&{cb_param}" if "?" in url else f"{url}?{cb_param}"

        try:
            req = urllib.request.Request(
                busted_url,
                headers={
                    "User-Agent": "RosettaResearcher/1.0",
                    "Cache-Control": "no-cache",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                total_size = int(response.headers.get("Content-Length", 0))
                with open(out_path, "wb") as f:
                    downloaded = 0
                    while True:
                        if state.SHUTDOWN_EVENT.is_set():
                            raise RuntimeError("Server is shutting down")
                        chunk = response.read(16384)
                        if not chunk: break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size:
                            state.DOWNLOAD_STATE[task_id]["progress"] = int((downloaded / total_size) * 100)
            return True
        except Exception:
            if out_path.exists(): out_path.unlink()
            if state.SHUTDOWN_EVENT.is_set(): break
            continue
            
    state.DOWNLOAD_STATE[task_id]["error"] = (
        f"All {file_type} download sources were blocked or failed."
    )
    return False

def download_worker(task_id: str, item: dict[str, Any]) -> None:
    """Entry point for the background download thread.

    Marks the whole install as a write-in-progress section so the idle
    shutdown monitor won't kill the process mid-install.
    """
    with state.write_in_progress():
        _download_worker_impl(task_id, item)

def _download_worker_impl(task_id: str, item: dict[str, Any]) -> None:
    """
    Background worker thread for downloading and installing a magazine.
    
    Workflow:
    1. Check if PDF is already local.
    2. Download PDF (if needed) and Data ZIP to a temp folder.
    3. Parse ZIP for metadata to determine correct folder name.
    4. Move files to 'Magazines/MagName/Date - Issue' directory.
    5. Cleanup temp files and refresh cache.
    """
    data_dir = cfg.data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    state.DOWNLOAD_STATE[task_id] = {
        "status": "Initializing...", "progress": 0, "error": None, "done": False,
    }

    temp_dir = data_dir / f".temp_{task_id}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize catalog-derived filenames: basename only, no dot/hidden names.
    try:
        pdf_filename = safe_name(item.get("pdf_filename"), "mag.pdf")
        zip_filename = safe_name(
            item.get("zip_filename") or "", f"{Path(pdf_filename).stem}_Data.zip"
        )
    except ValueError:
        state.DOWNLOAD_STATE[task_id]["error"] = "Catalog entry has an unsafe filename."
        state.DOWNLOAD_STATE[task_id]["done"] = True
        return

    pdf_temp = temp_dir / pdf_filename
    zip_temp = temp_dir / zip_filename

    # Step 1: Check Local PDF availability
    cache = state.METADATA_CACHE
    existing_rel_path = next((f for f in list(cache.keys()) if Path(f).name == pdf_filename), None)
    existing_pdf_path = (data_dir / existing_rel_path) if existing_rel_path else None

    if existing_pdf_path and existing_pdf_path.exists():
        state.DOWNLOAD_STATE[task_id]["status"] = "PDF found locally. Skipping download..."
        shutil.copy2(existing_pdf_path, pdf_temp)
        success_pdf = True
    else:
        success_pdf = download_waterfall(task_id, pdf_temp, item.get("pdf_sources", []), "PDF")

    if not success_pdf:
        state.DOWNLOAD_STATE[task_id]["done"] = True
        return

    # Step 2: Download Data ZIP
    success_zip = download_waterfall(task_id, zip_temp, item.get("zip_sources", []), "Data ZIP")

    if not success_zip:
        state.DOWNLOAD_STATE[task_id]["done"] = True
        return

    # Step 3: Organize Folder Structure
    state.DOWNLOAD_STATE[task_id]["status"] = "Organizing..."
    meta = {}
    if success_zip and zip_temp.exists():
        try:
            with zipfile.ZipFile(zip_temp, "r") as z:
                meta_file = next((n for n in z.namelist() if n.split("/")[-1].lower() == "metadata.txt"), None)
                if meta_file:
                    meta = metadata.parse_metadata(z.read(meta_file).decode("utf-8", errors="ignore"))
        except Exception: pass

    # Build Folder: Magazines/MagName/Date - IssueName (sanitized components)
    try:
        mag_name = safe_name(
            meta.get("name", item.get("magazine_name", "")).replace("/", "_").replace("\\", "_"),
            "Unsorted",
        )
        date_str = meta.get("date", item.get("date", "")).replace("/", "-").replace("\\", "-")
        if date_str: date_str = safe_name(date_str)
        issue_name = meta.get("issue_name", item.get("issue_name", "")).replace("/", "_").replace("\\", "_")
        if issue_name: issue_name = safe_name(issue_name)
    except ValueError:
        state.DOWNLOAD_STATE[task_id]["error"] = "Catalog entry has an unsafe folder name."
        state.DOWNLOAD_STATE[task_id]["done"] = True
        try: shutil.rmtree(temp_dir)
        except Exception: pass
        return

    folder_name = ""
    if date_str and issue_name: folder_name = f"{date_str} - {issue_name}"
    elif issue_name: folder_name = issue_name
    elif date_str: folder_name = date_str

    final_dir = data_dir / mag_name
    if folder_name: final_dir = final_dir / folder_name

    # Defense in depth: verify the destination stays inside data_dir.
    if not final_dir.resolve().is_relative_to(data_dir.resolve()):
        state.DOWNLOAD_STATE[task_id]["error"] = "Unsafe destination path rejected."
        state.DOWNLOAD_STATE[task_id]["done"] = True
        try: shutil.rmtree(temp_dir)
        except Exception: pass
        return

    final_dir.mkdir(parents=True, exist_ok=True)

    # Move files from temp to final
    if success_pdf and pdf_temp.exists():
        os.replace(pdf_temp, final_dir / pdf_filename)
    if success_zip and zip_temp.exists():
        os.replace(zip_temp, final_dir / zip_temp.name)

    # Step 4: Write Local Metadata
    # We consolidate all catalog info into a local metadata.txt for portability
    ml = []
    for k in ["magazine_name", "publisher", "date", "issue_name", "original_language", 
              "translated_language", "version", "tags", "scanner", "scanner_url", 
              "editor", "editor_url", "notes"]:
        if item.get(k): ml.append(f"{k.replace('_',' ').title()}: {item[k]}")
    
    meta_content = "\n".join(ml)
    zip_path = final_dir / zip_temp.name
    loose_meta = final_dir / f"{Path(pdf_filename).stem}.metadata.txt"

    if zip_path.exists():
        try:
            zip_utils.update_zip_content(zip_path, "metadata.txt", meta_content)
            if loose_meta.exists(): os.remove(loose_meta)
        except Exception: pass
    else:
        atomic_write_text(loose_meta, meta_content)

    # Cleanup temp
    try: shutil.rmtree(temp_dir)
    except Exception: pass

    state.DOWNLOAD_STATE[task_id]["progress"] = 100
    state.DOWNLOAD_STATE[task_id]["status"] = "Complete!"
    state.DOWNLOAD_STATE[task_id]["done"] = True
    metadata.load_metadata_cache()

    # Index the newly installed magazine so it is searchable immediately.
    from app.services import search_index  # local import: avoids cycle at module load
    new_rel_path = (final_dir / pdf_filename).relative_to(data_dir).as_posix()
    search_index.index_magazine_path(new_rel_path)