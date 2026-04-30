"""
Download Service
Handles background downloading of PDFs and Data ZIPs from community catalogs.
Features a "waterfall" download system to try multiple mirror URLs.
"""

import os
import shutil
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import app.config as cfg
from app.services import metadata, state, zip_utils

def download_waterfall(task_id: str, out_path: Path, sources: list[str], file_type: str) -> bool:
    """
    Attempts to download a file by trying a list of mirror URLs in order.
    
    Updates the global DOWNLOAD_STATE with progress for the UI.
    """
    if not sources:
        return True
        
    timeout = cfg.download_timeout()
    for url in sources:
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
                        chunk = response.read(16384)
                        if not chunk: break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size:
                            state.DOWNLOAD_STATE[task_id]["progress"] = int((downloaded / total_size) * 100)
            return True
        except Exception:
            if out_path.exists(): out_path.unlink()
            continue
            
    state.DOWNLOAD_STATE[task_id]["error"] = f"All {file_type} mirrors failed."
    return False

def download_worker(task_id: str, item: dict[str, Any]) -> None:
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

    pdf_filename = item.get("pdf_filename", "mag.pdf")
    pdf_temp = temp_dir / pdf_filename
    zip_temp = temp_dir / (item.get("zip_filename") or f"{Path(pdf_filename).stem}_Data.zip")

    # Step 1: Check Local PDF availability
    existing_rel_path = next((f for f in state.METADATA_CACHE.keys() if f.endswith(pdf_filename)), None)
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

    # Build Folder: Magazines/MagName/Date - IssueName
    mag_name = meta.get("name", item.get("magazine_name", "Unsorted")).replace("/", "_").replace("\\", "_")
    date_str = meta.get("date", item.get("date", "")).replace("/", "-").replace("\\", "-")
    issue_name = meta.get("issue_name", item.get("issue_name", "")).replace("/", "_").replace("\\", "_")

    folder_name = ""
    if date_str and issue_name: folder_name = f"{date_str} - {issue_name}"
    elif issue_name: folder_name = issue_name
    elif date_str: folder_name = date_str

    final_dir = data_dir / mag_name
    if folder_name: final_dir = final_dir / folder_name
    final_dir.mkdir(parents=True, exist_ok=True)

    # Move files from temp to final
    if success_pdf and pdf_temp.exists():
        os.replace(pdf_temp, final_dir / item.get("pdf_filename"))
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
        loose_meta.write_text(meta_content, encoding="utf-8")

    # Cleanup temp
    try: shutil.rmtree(temp_dir)
    except Exception: pass

    state.DOWNLOAD_STATE[task_id]["progress"] = 100
    state.DOWNLOAD_STATE[task_id]["status"] = "Complete!"
    state.DOWNLOAD_STATE[task_id]["done"] = True
    metadata.load_metadata_cache()