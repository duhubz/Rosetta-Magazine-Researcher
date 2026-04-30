"""
API Routes
Handles all data requests, file manipulation, searching, and remote catalog fetching.
"""

import io
import json
import logging
import os
import re
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from flask import Blueprint, Response, jsonify, request, send_file

import app.config as cfg
from app.services import catalog, download, metadata, search as search_svc, state
from app.services import zip_utils
from app.utils import get_safe_path

logger = logging.getLogger(__name__)
bp = Blueprint("api", __name__)

@bp.after_request
def add_header(response: Response) -> Response:
    """Disable browser caching for all API responses to ensure data freshness."""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, public, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

@bp.route("/ping")
def ping() -> str:
    """Heartbeat endpoint to keep the server alive while the browser tab is open."""
    state.LAST_PING = time.time()
    return "ok"

@bp.route("/list")
def list_mags() -> Response:
    """Returns a list of all local PDF magazines and their cached metadata."""
    data_dir = cfg.data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    metadata.load_metadata_cache()
    mags = [p.relative_to(data_dir).as_posix() for p in data_dir.rglob("*.pdf")]
    return jsonify({"files": sorted(mags), "metadata": state.METADATA_CACHE})

@bp.route("/render")
def render_page() -> Response:
    """Renders a specific PDF page to a PNG image for the viewer."""
    mag = request.args.get("mag", "")
    pn = int(request.args.get("page", 0))
    zoom = float(request.args.get("zoom", 1.5))
    
    if not mag or pn < 0:
        return jsonify({"error": "Invalid magazine or page parameters"}), 400

    try:
        pdf_path = get_safe_path(mag)
        doc = fitz.open(pdf_path)
        page = doc.load_page(pn)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        img = pix.tobytes("png")
        doc.close()
        return send_file(io.BytesIO(img), mimetype="image/png")
    except Exception as e:
        logger.error(f"Render failed for {mag} page {pn}: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route("/text")
def get_text() -> Response:
    """Retrieves all text sections and spatial coordinates for a specific page."""
    mag_rel_path = request.args.get("mag", "")
    pg = request.args.get("page", "1").zfill(3)
    
    content = metadata.get_transcription_text(mag_rel_path, pg)
    pdf_path = Path(get_safe_path(mag_rel_path))

    # Determine total pages for UI constraints
    total = 0
    try:
        doc = fitz.open(pdf_path)
        total = len(doc)
        doc.close()
    except Exception: pass

    # Split Rosetta format into sections (Transcription, Translation, Summary)
    jp, en, sum_t = "No transcription found.", "", ""
    if content:
        content = re.sub(r"^#\s?GA-TRANSCRIPTION\s*", "", content, flags=re.IGNORECASE)
        parts = re.split(r"#\s?GA-TRANSLATION", content, flags=re.IGNORECASE)

        if len(parts) > 1:
            jp = parts[0].strip()
            sub = re.split(r"#\s?GA-SUMMARY", parts[1], flags=re.IGNORECASE)
            en = sub[0].strip()
            sum_t = sub[1].strip() if len(sub) > 1 else ""
        else:
            sub = re.split(r"#\s?GA-SUMMARY", parts[0], flags=re.IGNORECASE)
            jp = sub[0].strip()
            sum_t = sub[1].strip() if len(sub) > 1 else ""

    # Fetch raw metadata for the visual editor
    raw_meta = ""
    partner_zip = metadata.get_partner_zip(mag_rel_path)

    if partner_zip:
        try:
            with zipfile.ZipFile(partner_zip, "r") as z:
                meta_file = next((n for n in z.namelist() if n.split("/")[-1].lower() == "metadata.txt"), None)
                if meta_file: raw_meta = z.read(meta_file).decode("utf-8", errors="ignore")
        except Exception: pass
    else:
        loose_meta = pdf_path.with_name(pdf_path.stem + ".metadata.txt")
        if loose_meta.exists(): raw_meta = loose_meta.read_text(encoding="utf-8", errors="ignore")

    # Fetch spatial coordinates for highlighting
    coords_data = []
    coords_filename = f"{pdf_path.stem}_COORDINATES.json"
    
    if partner_zip:
        try:
            with zipfile.ZipFile(partner_zip, "r") as z:
                z_c = next((n for n in z.namelist() if n.split("/")[-1].lower() == coords_filename.lower()), None)
                if z_c:
                    all_coords = json.loads(z.read(z_c).decode("utf-8"))
                    coords_data = next((c.get("data",[]) for c in all_coords if str(c.get("page")) == str(int(pg))), [])
        except Exception: pass
    else:
        l_c = pdf_path.parent / coords_filename
        if l_c.exists():
            try:
                all_coords = json.loads(l_c.read_text(encoding="utf-8"))
                coords_data = next((c.get("data",[]) for c in all_coords if str(c.get("page")) == str(int(pg))), [])
            except Exception: pass

    return jsonify({
        "jp": jp, "en": en, "sum": sum_t, "total_pages": total,
        "metadata": state.METADATA_CACHE.get(mag_rel_path, {}),
        "raw_meta": raw_meta, "coordinates": coords_data,
    })

@bp.route("/save", methods=["POST"])
def save_text() -> Response:
    """Saves edited transcription, metadata, and coordinates to local disk or ZIP."""
    data = request.json
    rel_path = data.get("mag")
    page_num = int(data.get("page", 0))
    
    if not rel_path or page_num <= 0:
        return jsonify({"error": "Invalid magazine path or page number"}), 400

    pdf_path = Path(get_safe_path(rel_path))
    new_page_content = f"{data['jp']}\n\n#GA-TRANSLATION\n{data['en']}\n\n#GA-SUMMARY\n{data['sum']}"
    
    try:
        partner_zip = metadata.get_partner_zip(rel_path)
        master_filename = f"{pdf_path.stem}_COMPLETE.txt"
        master_path = pdf_path.parent / master_filename
        if not master_path.exists(): master_path = None
        
        # 1. Update Content (Master File or Page Files)
        if master_path or (partner_zip and any(n.split("/")[-1].lower() == master_filename.lower() for n in zipfile.ZipFile(partner_zip, "r").namelist())):
            raw_text = master_path.read_text(encoding="utf-8") if master_path else ""
            if not raw_text and partner_zip:
                with zipfile.ZipFile(partner_zip, "r") as z:
                    z_m = next(n for n in z.namelist() if n.split("/")[-1].lower() == master_filename.lower())
                    raw_text = z.read(z_m).decode("utf-8")
            
            pages = metadata.get_pages_from_master(raw_text)
            pages[page_num] = new_page_content
            new_master = "\n\n".join([f"[[PAGE_{str(p).zfill(3)}]]\n{c}" for p, c in sorted(pages.items())])
            
            if master_path: master_path.write_text(new_master, encoding="utf-8")
            else: zip_utils.update_zip_content(partner_zip, master_filename, new_master)
        else:
            content_h = f"#GA-TRANSCRIPTION\n{new_page_content}"
            if partner_zip:
                target = f"{pdf_path.stem}_p{str(page_num).zfill(3)}.txt"
                zip_utils.update_zip_content(partner_zip, target, content_h)
            else:
                target_p = pdf_path.parent / f"{pdf_path.stem}_p{str(page_num).zfill(3)}.txt"
                target_p.write_text(content_h, encoding="utf-8")

        # 2. Update Metadata
        if partner_zip: zip_utils.update_zip_content(partner_zip, "metadata.txt", data.get("meta", ""))
        else: (pdf_path.with_name(pdf_path.stem + ".metadata.txt")).write_text(data.get("meta", ""), encoding="utf-8")

        # 3. Update Coordinates
        if data.get("coords") is not None:
            c_fn = f"{pdf_path.stem}_COORDINATES.json"
            all_c = []
            if partner_zip:
                try:
                    with zipfile.ZipFile(partner_zip, "r") as z:
                        z_c = next((n for n in z.namelist() if n.split("/")[-1].lower() == c_fn.lower()), None)
                        if z_c: all_c = json.loads(z.read(z_c).decode("utf-8"))
                except Exception: pass
            else:
                l_c = pdf_path.parent / c_fn
                if l_c.exists():
                    try: all_c = json.loads(l_c.read_text(encoding="utf-8"))
                    except Exception: pass
            
            found = False
            for c in all_c:
                if str(c.get("page")) == str(page_num):
                    c["data"] = data["coords"]; found = True; break
            if not found: all_c.append({"page": page_num, "data": data["coords"]})
            
            new_c_json = json.dumps(all_c, ensure_ascii=False, indent=2)
            if partner_zip: zip_utils.update_zip_content(partner_zip, c_fn, new_c_json)
            else: (pdf_path.parent / c_fn).write_text(new_c_json, encoding="utf-8")

        metadata.load_metadata_cache()
        logger.info(f"Saved changes for {rel_path} page {page_num}")
        return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Save failed for {rel_path}: {e}")
        return jsonify({"error": str(e)}), 500

@bp.route("/search")
def search() -> Response:
    """Executes full-text search across all transcriptions using advanced query logic."""
    query = request.args.get("q", "")
    results, highlight_list = search_svc.search(
        query=query,
        scope=request.args.get("scope", "global"),
        inc_jp=request.args.get("incJp") == "true",
        inc_en=request.args.get("incEn") == "true",
        inc_sum=request.args.get("incSum") == "true",
        current_mag=request.args.get("currentMag", ""),
        mag_filter=request.args.get("magFilter", "").lower(),
        date_start=request.args.get("dateStart", ""),
        date_end=request.args.get("dateEnd", ""),
        tag_filter=request.args.get("tagFilter", "").lower(),
    )
    return jsonify({"results": results, "terms_to_highlight": highlight_list})

@bp.route("/bookmarks", methods=["GET", "POST", "DELETE"])
def bookmarks_handler() -> Response:
    """Handles retrieval, creation, and deletion of page bookmarks."""
    bookmarks_file = cfg.bookmarks_file()
    if not bookmarks_file.exists():
        bookmarks_file.write_text("{}", encoding="utf-8")
    
    bks = json.loads(bookmarks_file.read_text(encoding="utf-8"))
    
    if request.method == "POST":
        d = request.json
        bks[f"{d['mag']}_{d['page']}"] = d
    elif request.method == "DELETE":
        key = request.args.get("key")
        if key in bks: del bks[key]
        
    bookmarks_file.write_text(json.dumps(bks), encoding="utf-8")
    return jsonify(bks)

@bp.route("/cover/<item_id>")
def get_cover(item_id: str) -> Response:
    """Fetches cover images. Uses local cache, then remote download via local catalog lookup."""
    v = request.args.get("v", "1.0")
    safe_id = "".join(c for c in item_id if c.isalnum() or c in "_-")
    cache_name = f"{safe_id}_v{v}.cache"

    covers_dir = cfg.covers_dir()
    covers_dir.mkdir(parents=True, exist_ok=True)
    cache_path = covers_dir / cache_name

    # 1. Serve from disk if cached
    if cache_path.exists():
        return send_file(cache_path, mimetype="image/jpeg")

    # 2. Look up URL in local catalog (No force refresh here to prevent UI lag)
    catalogs = catalog.get_all_catalogs(force_refresh=False)
    item = next((i for i in catalogs if str(i.get("id")) == item_id), None)

    if item and item.get("cover_url"):
        try:
            req = urllib.request.Request(item["cover_url"], headers={"User-Agent": "RosettaResearcher/1.0"})
            with urllib.request.urlopen(req, timeout=cfg.cover_fetch_timeout()) as response:
                img_data = response.read()
                # Clean old version caches
                for old in covers_dir.glob(f"{safe_id}_v*.cache"):
                    try: old.unlink()
                    except Exception: pass
                cache_path.write_bytes(img_data)
                return send_file(io.BytesIO(img_data), mimetype="image/jpeg")
        except Exception as e:
            logger.warning(f"Could not download cover for {item_id}: {e}")

    # 3. Fallback SVG
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="#222"/><text x="50%" y="50%" fill="#666" font-family="sans-serif" font-size="14" text-anchor="middle">No Cover Art</text></svg>'
    return Response(svg, mimetype="image/svg+xml")

@bp.route("/catalog")
def get_catalog() -> Response:
    """Fetches combined catalogs and triggers a background mirror update."""
    return jsonify(catalog.get_all_catalogs(force_refresh=True))

@bp.route("/download", methods=["POST"])
def start_download() -> Response:
    """Starts a background download worker for a specific catalog ID."""
    item_id = request.json.get("id")
    catalog_data = catalog.get_all_catalogs(force_refresh=False)
    item = next((i for i in catalog_data if i.get("id") == item_id), None)
    if item:
        threading.Thread(target=download.download_worker, args=(item_id, item), daemon=True).start()
        return jsonify({"status": "started"})
    return jsonify({"error": "Item not found in catalog"}), 404

@bp.route("/downloads")
def get_downloads() -> Response:
    """Polled by UI to track current download progress and errors."""
    return jsonify(state.DOWNLOAD_STATE)

@bp.route("/uninstall", methods=["POST"])
def uninstall_mag() -> Response:
    """Safely removes a magazine PDF and all associated data files from the local library."""
    pdf_filename = request.json.get("pdf_filename")
    target_rel_path = next((f for f in state.METADATA_CACHE.keys() if f.endswith(pdf_filename)), None)
    
    if not target_rel_path:
        return jsonify({"error": "File not found"}), 404

    data_dir = cfg.data_dir()
    pdf_path = data_dir / target_rel_path
    try:
        partner_zip = metadata.get_partner_zip(target_rel_path)
        if partner_zip and partner_zip.exists(): os.remove(partner_zip)
        for txt in pdf_path.parent.glob(f"{pdf_path.stem}_p*.txt"): os.remove(txt)
        if pdf_path.exists(): os.remove(pdf_path)
        
        # Cleanup parent directory if empty
        if pdf_path.parent != data_dir and not any(pdf_path.parent.iterdir()):
            pdf_path.parent.rmdir()

        metadata.load_metadata_cache()
        logger.info(f"Uninstalled: {pdf_filename}")
        return jsonify({"status": "uninstalled"})
    except Exception as e:
        logger.error(f"Uninstall failed for {pdf_filename}: {e}")
        return jsonify({"error": str(e)}), 500