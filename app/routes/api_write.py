"""
API Routes — write endpoints
State-changing endpoints (save, bookmarks, download, uninstall), registered
under the same /api prefix as the read-only blueprint in api.py.
"""

import json
import logging
import os
import threading
import zipfile
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

import app.config as cfg
from app.routes.api import add_no_cache_headers
from app.services import (
    catalog,
    download,
    metadata,
    pdf_cache,
    rendering,
    search_index,
    state,
    zip_utils,
)
from app.utils import atomic_write_text, get_safe_path

logger = logging.getLogger(__name__)
bp = Blueprint("api_write", __name__)

# Same no-cache policy as the read-only API blueprint.
bp.after_request(add_no_cache_headers)


@bp.route("/save", methods=["POST"])
def save_text() -> Response:
    """Saves edited transcription, metadata, and coordinates to local disk or ZIP."""
    data = request.get_json(silent=True) or {}
    rel_path = data.get("mag")
    try:
        page_num = int(data.get("page", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Bad request", "detail": "'page' must be an integer."}), 400

    if not rel_path or page_num <= 0:
        return jsonify({"error": "Invalid magazine path or page number"}), 400

    missing = [k for k in ("jp", "en", "sum") if k not in data]
    if missing:
        return jsonify(
            {"error": "Bad request", "detail": f"Missing required keys: {', '.join(missing)}."}
        ), 400

    try:
        pdf_path = Path(get_safe_path(rel_path))
    except ValueError as e:
        return jsonify({"error": "Bad request", "detail": str(e)}), 400
    new_page_content = (
        f"{data['jp']}\n\n#GA-TRANSLATION\n{data['en']}\n\n#GA-SUMMARY\n{data['sum']}"
    )

    try:
        with state.write_in_progress():
            partner_zip = metadata.get_partner_zip(rel_path)
            master_filename = f"{pdf_path.stem}_COMPLETE.txt"
            master_path = pdf_path.parent / master_filename
            if not master_path.exists():
                master_path = None
            # All ZIP member updates are collected here and written in ONE
            # atomic rewrite pass at the end (instead of 3 sequential ones).
            zip_updates: dict[str, str | bytes] = {}

            # 1. Update Content (Master File or Page Files)
            zip_has_master = False
            if master_path is None and partner_zip:
                with zipfile.ZipFile(partner_zip, "r") as z:
                    zip_has_master = (
                        zip_utils.find_member_by_basename(z, master_filename) is not None
                    )
            if master_path or zip_has_master:
                raw_text = master_path.read_text(encoding="utf-8") if master_path else ""
                if not raw_text and partner_zip:
                    with zipfile.ZipFile(partner_zip, "r") as z:
                        z_m = zip_utils.find_member_by_basename(z, master_filename)
                        raw_text = z.read(z_m).decode("utf-8")

                pages = metadata.get_pages_from_master(raw_text)
                pages[page_num] = new_page_content
                new_master = "\n\n".join(
                    [f"[[PAGE_{str(p).zfill(3)}]]\n{c}" for p, c in sorted(pages.items())]
                )

                if master_path:
                    atomic_write_text(master_path, new_master)
                else:
                    zip_updates[master_filename] = new_master
            else:
                content_h = f"#GA-TRANSCRIPTION\n{new_page_content}"
                if partner_zip:
                    target = f"{pdf_path.stem}_p{str(page_num).zfill(3)}.txt"
                    zip_updates[target] = content_h
                else:
                    target_p = pdf_path.parent / f"{pdf_path.stem}_p{str(page_num).zfill(3)}.txt"
                    atomic_write_text(target_p, content_h)

            # 2. Update Metadata (only when the client actually sent a 'meta'
            # key, so partial saves can't blank out metadata.txt)
            if "meta" in data:
                if partner_zip:
                    zip_updates["metadata.txt"] = data.get("meta", "")
                else:
                    atomic_write_text(
                        pdf_path.with_name(pdf_path.stem + ".metadata.txt"), data.get("meta", "")
                    )

            # 3. Update Coordinates
            if data.get("coords") is not None:
                c_fn = rendering.coordinates_filename(pdf_path)
                all_c = rendering.load_all_coordinates(pdf_path, partner_zip)
                rendering.merge_page_coordinates(all_c, page_num, data["coords"])
                new_c_json = json.dumps(all_c, ensure_ascii=False, indent=2)
                if partner_zip:
                    zip_updates[c_fn] = new_c_json
                else:
                    atomic_write_text(pdf_path.parent / c_fn, new_c_json)

            # Single atomic ZIP rewrite for all collected member updates.
            if partner_zip and zip_updates:
                zip_utils.update_zip_contents(partner_zip, zip_updates)

            metadata.load_metadata_cache()
            # Keep the FTS5 search index in sync with the edited magazine.
            search_index.index_magazine_path(rel_path)
            logger.info(f"Saved changes for {rel_path} page {page_num}")
            return jsonify({"status": "ok"})
    except Exception as e:
        logger.error(f"Save failed for {rel_path}: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/bookmarks", methods=["GET", "POST", "DELETE"])
def bookmarks_handler() -> Response:
    """Handles retrieval, creation, and deletion of page bookmarks."""
    bookmarks_file = cfg.bookmarks_file()
    if not bookmarks_file.exists():
        atomic_write_text(bookmarks_file, "{}")

    bks = json.loads(bookmarks_file.read_text(encoding="utf-8"))

    if request.method == "POST":
        d = request.get_json(silent=True) or {}
        if not d.get("mag") or not d.get("page"):
            return jsonify({"error": "Bad request", "detail": "'mag' and 'page' are required."}), (
                400
            )
        bks[f"{d['mag']}_{d['page']}"] = d
    elif request.method == "DELETE":
        key = request.args.get("key")
        if key in bks:
            del bks[key]

    with state.write_in_progress():
        atomic_write_text(bookmarks_file, json.dumps(bks))
    return jsonify(bks)


@bp.route("/download", methods=["POST"])
def start_download() -> Response:
    """Starts a background download worker for a specific catalog ID."""
    item_id = (request.get_json(silent=True) or {}).get("id")
    if not item_id:
        return jsonify({"error": "Bad request", "detail": "'id' is required."}), 400
    catalog_data = catalog.get_all_catalogs(force_refresh=False)
    item = next((i for i in catalog_data if i.get("id") == item_id), None)
    if item:
        threading.Thread(target=download.download_worker, args=(item_id, item), daemon=True).start()
        return jsonify({"status": "started"})
    return jsonify({"error": "Item not found in catalog"}), 404


@bp.route("/uninstall", methods=["POST"])
def uninstall_mag() -> Response:
    """Safely removes a magazine PDF and all associated data files from the local library."""
    pdf_filename = (request.get_json(silent=True) or {}).get("pdf_filename")
    if not pdf_filename:
        return jsonify({"error": "Bad request", "detail": "'pdf_filename' is required."}), 400
    # Exact basename match — endswith() would let 'game.pdf' match 'Endgame.pdf'.
    cache = state.METADATA_CACHE  # local ref: reload swaps the global binding
    target_rel_path = next((f for f in list(cache.keys()) if Path(f).name == pdf_filename), None)

    if not target_rel_path:
        return jsonify({"error": "File not found"}), 404

    data_dir = cfg.data_dir()
    pdf_path = data_dir / target_rel_path
    try:
        # Release any cached open handle first (required for os.remove on Windows).
        pdf_cache.evict(pdf_path)
        partner_zip = metadata.get_partner_zip(target_rel_path)
        if partner_zip and partner_zip.exists():
            os.remove(partner_zip)
        for txt in pdf_path.parent.glob(f"{pdf_path.stem}_p*.txt"):
            os.remove(txt)
        if pdf_path.exists():
            os.remove(pdf_path)

        # Cleanup parent directory if empty
        if pdf_path.parent != data_dir and not any(pdf_path.parent.iterdir()):
            pdf_path.parent.rmdir()

        metadata.load_metadata_cache()
        # Drop the magazine's pages from the FTS5 search index.
        search_index.remove_magazine_path(target_rel_path)
        logger.info(f"Uninstalled: {pdf_filename}")
        return jsonify({"status": "uninstalled"})
    except Exception as e:
        logger.error(f"Uninstall failed for {pdf_filename}: {e}")
        return jsonify({"error": str(e)}), 500
