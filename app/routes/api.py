"""API routes."""

import io
import json
import os
import re
import threading
import time
import urllib.request
import zipfile

import fitz
from flask import Blueprint, Response, jsonify, request, send_file

import app.config as cfg
from app.services import catalog, download, metadata, search as search_svc, state
from app.services import zip_utils
from app.utils import get_safe_path

bp = Blueprint("api", __name__)


@bp.route("/ping")
def ping():
    state.LAST_PING = time.time()
    return "ok"


@bp.route("/list")
def list_mags():
    data_dir = cfg.data_dir()
    bookmarks_file = cfg.bookmarks_file()
    data_dir.mkdir(parents=True, exist_ok=True)
    metadata.load_metadata_cache()
    mags = [p.relative_to(data_dir).as_posix() for p in data_dir.rglob("*.pdf")]
    return jsonify({"files": sorted(mags), "metadata": state.METADATA_CACHE})


@bp.route("/render")
def render_page():
    mag = request.args.get("mag", "")
    pn = int(request.args.get("page", 0))
    zoom = float(request.args.get("zoom", 1.5))
    try:
        pdf_path = get_safe_path(mag)
        doc = fitz.open(pdf_path)
        page = doc.load_page(pn)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        img = pix.tobytes("png")
        doc.close()
        return send_file(io.BytesIO(img), mimetype="image/png")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/text")
def get_text():
    mag_rel_path = request.args.get("mag", "")
    pg = request.args.get("page", "1").zfill(3)
    content = metadata.get_transcription_text(mag_rel_path, pg)

    data_dir = cfg.data_dir()
    total = 0
    try:
        doc = fitz.open(get_safe_path(mag_rel_path))
        total = len(doc)
        doc.close()
    except Exception:
        pass

    jp, en, sum_t = "No transcription found for this page.", "", ""
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

    meta = state.METADATA_CACHE.get(mag_rel_path, {})

    raw_meta = ""
    partner_zip = metadata.get_partner_zip(mag_rel_path)
    pdf_path = data_dir / mag_rel_path

    if partner_zip:
        try:
            with zipfile.ZipFile(partner_zip, "r") as z:
                meta_file = next(
                    (
                        n
                        for n in z.namelist()
                        if n.split("/")[-1].lower() == "metadata.txt"
                    ),
                    None,
                )
                if meta_file:
                    raw_meta = z.read(meta_file).decode("utf-8", errors="ignore")
        except Exception:
            pass
    else:
        loose_meta = pdf_path.with_name(pdf_path.stem + ".metadata.txt")
        generic_meta = pdf_path.parent / "metadata.txt"
        if loose_meta.exists():
            raw_meta = loose_meta.read_text(encoding="utf-8", errors="ignore")
        elif generic_meta.exists() and pdf_path.parent != data_dir:
            raw_meta = generic_meta.read_text(encoding="utf-8", errors="ignore")

    return jsonify(
        {
            "jp": jp,
            "en": en,
            "sum": sum_t,
            "total_pages": total,
            "metadata": meta,
            "raw_meta": raw_meta,
        }
    )


@bp.route("/save", methods=["POST"])
def save_text():
    data = request.json
    rel_path = data["mag"]
    page_num = int(data["page"])
    pdf_path = get_safe_path(rel_path)
    new_page_content = (
        f"{data['jp']}\n\n#GA-TRANSLATION\n{data['en']}\n\n#GA-SUMMARY\n{data['sum']}"
    )
    meta_content = data.get("meta", "")

    try:
        partner_zip = metadata.get_partner_zip(rel_path)
        master_filename = f"{pdf_path.stem}_COMPLETE.txt"
        master_path = next(pdf_path.parent.glob("*_COMPLETE.txt"), None)
        uses_master = master_path or (
            partner_zip
            and any(
                n.endswith("_COMPLETE.txt")
                for n in zipfile.ZipFile(partner_zip, "r").namelist()
            )
        )

        if uses_master:
            if master_path:
                raw_text = master_path.read_text(encoding="utf-8")
            else:
                with zipfile.ZipFile(partner_zip, "r") as z:
                    z_master = next(
                        n for n in z.namelist() if n.endswith("_COMPLETE.txt")
                    )
                    raw_text = z.read(z_master).decode("utf-8")

            pages = metadata.get_pages_from_master(raw_text)
            pages[page_num] = new_page_content
            new_master_text = "\n\n".join(
                [f"[[PAGE_{str(p).zfill(3)}]]\n{c}" for p, c in sorted(pages.items())]
            )

            if master_path:
                master_path.write_text(new_master_text, encoding="utf-8")
                loose_meta = pdf_path.with_name(pdf_path.stem + ".metadata.txt")
                generic_meta = pdf_path.parent / "metadata.txt"
                if generic_meta.exists() and pdf_path.parent != cfg.data_dir():
                    generic_meta.write_text(meta_content, encoding="utf-8")
                else:
                    loose_meta.write_text(meta_content, encoding="utf-8")
            else:
                zip_utils.update_zip_content(
                    partner_zip, master_filename, new_master_text
                )
                zip_utils.update_zip_content(partner_zip, "metadata.txt", meta_content)
        else:
            content_with_header = f"#GA-TRANSCRIPTION\n{new_page_content}"
            pattern = re.compile(rf"_p0*{page_num}\.txt$", re.IGNORECASE)
            if partner_zip:
                target_filename = f"{pdf_path.stem}_p{str(page_num).zfill(3)}.txt"
                with zipfile.ZipFile(partner_zip, "r") as z:
                    for zname in z.namelist():
                        if pattern.search(zname.split("/")[-1]):
                            target_filename = zname
                            break
                zip_utils.update_zip_content(
                    partner_zip, target_filename, content_with_header
                )
                zip_utils.update_zip_content(partner_zip, "metadata.txt", meta_content)
            else:
                target_filepath = None
                for lp in pdf_path.parent.glob("*.txt"):
                    if pattern.search(lp.name):
                        target_filepath = lp
                        break
                if not target_filepath:
                    target_filepath = (
                        pdf_path.parent
                        / f"{pdf_path.stem}_p{str(page_num).zfill(3)}.txt"
                    )
                target_filepath.write_text(content_with_header, encoding="utf-8")

                loose_meta = pdf_path.with_name(pdf_path.stem + ".metadata.txt")
                generic_meta = pdf_path.parent / "metadata.txt"
                if generic_meta.exists() and pdf_path.parent != cfg.data_dir():
                    generic_meta.write_text(meta_content, encoding="utf-8")
                else:
                    loose_meta.write_text(meta_content, encoding="utf-8")

        metadata.load_metadata_cache()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/search")
def search():
    query = request.args.get("q", "")
    scope = request.args.get("scope", "global")
    inc_jp = request.args.get("incJp") == "true"
    inc_en = request.args.get("incEn") == "true"
    inc_sum = request.args.get("incSum") == "true"
    current_mag = request.args.get("currentMag", "")
    mag_filter = request.args.get("magFilter", "").lower()
    date_start = request.args.get("dateStart", "")
    date_end = request.args.get("dateEnd", "")
    tag_filter = request.args.get("tagFilter", "").lower()

    results, highlight_list = search_svc.search(
        query=query,
        scope=scope,
        inc_jp=inc_jp,
        inc_en=inc_en,
        inc_sum=inc_sum,
        current_mag=current_mag,
        mag_filter=mag_filter,
        date_start=date_start,
        date_end=date_end,
        tag_filter=tag_filter,
    )
    return jsonify({"results": results, "terms_to_highlight": highlight_list})


@bp.route("/bookmarks", methods=["GET", "POST", "DELETE"])
def bookmarks_handler():
    bookmarks_file = cfg.bookmarks_file()
    if not bookmarks_file.exists():
        bookmarks_file.write_text("{}", encoding="utf-8")
    bks = json.loads(bookmarks_file.read_text(encoding="utf-8"))
    if request.method == "POST":
        d = request.json
        bks[f"{d['mag']}_{d['page']}"] = d
    elif request.method == "DELETE":
        key = request.args.get("key")
        if key in bks:
            del bks[key]
    bookmarks_file.write_text(json.dumps(bks), encoding="utf-8")
    return jsonify(bks)


@bp.route("/cover/<item_id>")
def get_cover(item_id: str):
    v = request.args.get("v", "1.0")
    safe_id = "".join(c for c in item_id if c.isalnum() or c in "_-")
    cache_name = f"{safe_id}_v{v}.cache"

    covers_dir = cfg.covers_dir()
    covers_dir.mkdir(parents=True, exist_ok=True)
    cache_path = covers_dir / cache_name

    if cache_path.exists():
        return send_file(cache_path, mimetype="image/jpeg")

    catalogs = catalog.get_all_catalogs()
    item = next((i for i in catalogs if str(i.get("id")) == item_id), None)

    if item and item.get("cover_url"):
        try:
            req = urllib.request.Request(
                item["cover_url"], headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(
                req, timeout=cfg.cover_fetch_timeout()
            ) as response:
                img_data = response.read()

                for old_file in covers_dir.glob(f"{safe_id}_v*.cache"):
                    try:
                        old_file.unlink()
                    except Exception:
                        pass

                cache_path.write_bytes(img_data)
                return send_file(io.BytesIO(img_data), mimetype="image/jpeg")
        except Exception:
            pass

    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="#222"/><text x="50%" y="50%" fill="#666" font-family="sans-serif" font-size="14" text-anchor="middle">No Cover Art</text></svg>'
    return send_file(io.BytesIO(svg.encode("utf-8")), mimetype="image/svg+xml")


@bp.route("/catalog")
def get_catalog():
    return jsonify(catalog.get_all_catalogs())


@bp.route("/download", methods=["POST"])
def start_download():
    data = request.json
    item_id = data.get("id")
    catalog_data = catalog.get_all_catalogs()
    item = next((i for i in catalog_data if i.get("id") == item_id), None)
    if item:
        threading.Thread(
            target=download.download_worker, args=(item_id, item), daemon=True
        ).start()
        return jsonify({"status": "started"})
    return jsonify({"error": "Item not found"}), 404


@bp.route("/downloads")
def get_downloads():
    return jsonify(state.DOWNLOAD_STATE)


@bp.route("/uninstall", methods=["POST"])
def uninstall_mag():
    data = request.json
    pdf_filename = data.get("pdf_filename")

    target_rel_path = next(
        (f for f in state.METADATA_CACHE.keys() if f.endswith(pdf_filename)), None
    )
    if not target_rel_path:
        return jsonify({"error": "File not found"}), 404

    data_dir = cfg.data_dir()
    pdf_path = data_dir / target_rel_path
    if pdf_path.exists():
        partner_zip = metadata.get_partner_zip(target_rel_path)
        loose_texts = list(pdf_path.parent.glob(f"{pdf_path.stem}_p*.txt"))

        if partner_zip and partner_zip.exists():
            try:
                os.remove(partner_zip)
            except Exception as e:
                return jsonify({"error": f"Failed to delete ZIP: {e}"}), 500

        for txt in loose_texts:
            try:
                os.remove(txt)
            except Exception as e:
                return jsonify({"error": f"Failed to delete text file: {e}"}), 500

        try:
            os.remove(pdf_path)
        except Exception as e:
            return jsonify({"error": f"Failed to delete PDF: {e}"}), 500

        if pdf_path.parent != data_dir:
            try:
                if not any(pdf_path.parent.iterdir()):
                    pdf_path.parent.rmdir()
            except Exception as e:
                return jsonify({"error": f"Failed to clean up folder: {e}"}), 500

    metadata.load_metadata_cache()
    return jsonify({"status": "uninstalled"})
