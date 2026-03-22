"""Download logic for PDFs and data ZIPs from catalog."""

import os
import shutil
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import app.config as cfg
from app.services import metadata, state, zip_utils


def download_waterfall(
    task_id: str, out_path: Path, sources: list, file_type: str
) -> bool:
    if not sources:
        return True
    timeout = cfg.download_timeout()
    for url in sources:
        state.DOWNLOAD_STATE[task_id]["status"] = f"Downloading {file_type}..."
        state.DOWNLOAD_STATE[task_id]["progress"] = 0

        cb_param = f"nocache={int(time.time() * 1000)}"
        busted_url = f"{url}&{cb_param}" if "?" in url else f"{url}?{cb_param}"

        try:
            req = urllib.request.Request(
                busted_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                total_size = int(response.headers.get("Content-Length", 0))
                with open(out_path, "wb") as f:
                    downloaded = 0
                    while True:
                        chunk = response.read(16384)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size:
                            state.DOWNLOAD_STATE[task_id]["progress"] = int(
                                (downloaded / total_size) * 100
                            )
            return True
        except Exception:
            if out_path.exists():
                out_path.unlink()
            continue
    state.DOWNLOAD_STATE[task_id]["error"] = f"All {file_type} backups failed."
    return False


def download_worker(task_id: str, item: dict) -> None:
    data_dir = cfg.data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    state.DOWNLOAD_STATE[task_id] = {
        "status": "Initializing...",
        "progress": 0,
        "error": None,
        "done": False,
    }

    temp_dir = data_dir / f".temp_{task_id}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    pdf_filename = item.get("pdf_filename", "mag.pdf")
    pdf_temp = temp_dir / pdf_filename
    zip_temp = temp_dir / (
        item.get("zip_filename") or f"{Path(pdf_filename).stem}_Data.zip"
    )

    existing_rel_path = next(
        (f for f in state.METADATA_CACHE.keys() if f.endswith(pdf_filename)), None
    )
    existing_pdf_path = (data_dir / existing_rel_path) if existing_rel_path else None

    if existing_pdf_path and existing_pdf_path.exists():
        state.DOWNLOAD_STATE[task_id]["status"] = "PDF found locally. Skipping download..."
        shutil.copy2(existing_pdf_path, pdf_temp)
        success_pdf = True
    else:
        success_pdf = download_waterfall(
            task_id, pdf_temp, item.get("pdf_sources", []), "PDF"
        )

    if not success_pdf:
        state.DOWNLOAD_STATE[task_id]["done"] = True
        return

    success_zip = download_waterfall(
        task_id, zip_temp, item.get("zip_sources", []), "Data ZIP"
    )

    if not success_zip:
        state.DOWNLOAD_STATE[task_id]["done"] = True
        return

    state.DOWNLOAD_STATE[task_id]["status"] = "Organizing..."
    meta = {}
    if success_zip and zip_temp.exists():
        try:
            with zipfile.ZipFile(zip_temp, "r") as z:
                meta_file = next(
                    (
                        n
                        for n in z.namelist()
                        if n.split("/")[-1].lower() == "metadata.txt"
                    ),
                    None,
                )
                if meta_file:
                    meta = metadata.parse_metadata(
                        z.read(meta_file).decode("utf-8", errors="ignore")
                    )
        except Exception:
            pass

    mag_name = (
        meta.get("name", item.get("magazine_name", "Unsorted"))
        .replace("/", "_")
        .replace("\\", "_")
    )
    date_str = (
        meta.get("date", item.get("date", "")).replace("/", "-").replace("\\", "-")
    )
    issue_name = (
        meta.get("issue_name", item.get("issue_name", ""))
        .replace("/", "_")
        .replace("\\", "_")
    )

    folder_name = ""
    if date_str and issue_name:
        folder_name = f"{date_str} - {issue_name}"
    elif issue_name:
        folder_name = issue_name
    elif date_str:
        folder_name = date_str

    final_dir = data_dir / mag_name
    if folder_name:
        final_dir = final_dir / folder_name
    final_dir.mkdir(parents=True, exist_ok=True)

    if success_pdf and pdf_temp.exists():
        os.replace(pdf_temp, final_dir / item.get("pdf_filename"))
    if success_zip and zip_temp.exists():
        os.replace(zip_temp, final_dir / zip_temp.name)

    for old_txt in final_dir.glob(
        f"{Path(item.get('pdf_filename', 'mag.pdf')).stem}_p*.txt"
    ):
        try:
            old_txt.unlink()
        except Exception:
            pass

    ml = []
    if item.get("magazine_name"):
        ml.append(f"Magazine Name: {item['magazine_name']}")
    if item.get("publisher"):
        ml.append(f"Publisher: {item['publisher']}")
    if item.get("date"):
        ml.append(f"Date: {item['date']}")
    if item.get("issue_name"):
        ml.append(f"Issue Name: {item['issue_name']}")
    if item.get("original_language"):
        ml.append(f"Region: {item['original_language']}")
    if item.get("translated_language"):
        ml.append(f"Translation: {item['translated_language']}")
    if item.get("version"):
        ml.append(f"Version: {item['version']}")
    if item.get("tags"):
        ml.append(f"Tags: {item['tags']}")
    if item.get("scanner"):
        ml.append(f"Scanner: {item['scanner']}")
    if item.get("scanner_url"):
        ml.append(f"Scanner URL: {item['scanner_url']}")
    if item.get("editor"):
        ml.append(f"Editor: {item['editor']}")
    if item.get("editor_url"):
        ml.append(f"Editor URL: {item['editor_url']}")
    if item.get("notes"):
        ml.append(f"Notes: {item['notes']}")
    meta_content = "\n".join(ml)

    pdf_filename = item.get("pdf_filename", "mag.pdf")
    zip_filename = item.get("zip_filename") or f"{Path(pdf_filename).stem}_Data.zip"
    zip_path = final_dir / zip_filename
    loose_meta = final_dir / f"{Path(pdf_filename).stem}.metadata.txt"

    if zip_path.exists():
        try:
            zip_utils.update_zip_content(zip_path, "metadata.txt", meta_content)
            if loose_meta.exists():
                os.remove(loose_meta)
        except Exception:
            pass
    else:
        loose_meta.write_text(meta_content, encoding="utf-8")

    try:
        shutil.rmtree(temp_dir)
    except Exception:
        pass

    state.DOWNLOAD_STATE[task_id]["progress"] = 100
    state.DOWNLOAD_STATE[task_id]["status"] = "Complete!"
    state.DOWNLOAD_STATE[task_id]["done"] = True
    metadata.load_metadata_cache()
