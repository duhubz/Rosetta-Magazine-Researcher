"""
API Routes — read-only endpoints
Thin HTTP wrappers: parse the request, delegate to a service module, format
the response. Write endpoints (save/bookmarks/download/uninstall) live in
api_write.py; both blueprints share the /api URL prefix.
"""

import io
import logging
import re
import time
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file

import app.config as cfg
from app.services import catalog, metadata, rendering, search_index, state
from app.services import search as search_svc
from app.services.text_utils import split_sections
from app.utils import (
    URLBlockedError,
    atomic_write_bytes,
    get_safe_path,
    has_hidden_component,
    safe_urlopen,
)

logger = logging.getLogger(__name__)
bp = Blueprint("api", __name__)


def add_no_cache_headers(response: Response) -> Response:
    """Disable browser caching for all API responses to ensure data freshness.

    Shared with the write blueprint (api_write.py).
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, public, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


bp.after_request(add_no_cache_headers)


@bp.route("/ping", methods=["GET", "POST"])
def ping() -> str:
    """Heartbeat endpoint to keep the server alive while the browser tab is open.

    Accepts POST (without the session token) so navigator.sendBeacon can
    deliver a final heartbeat on pagehide.
    """
    state.LAST_PING = time.time()
    return "ok"


@bp.route("/list")
def list_mags() -> Response:
    """Returns a list of all local PDF magazines and their cached metadata."""
    data_dir = cfg.data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    metadata.load_metadata_cache()
    mags = [
        p.relative_to(data_dir).as_posix()
        for p in data_dir.rglob("*.pdf")
        if not has_hidden_component(p, data_dir)
    ]
    return jsonify({"files": sorted(mags), "metadata": state.METADATA_CACHE})


@bp.route("/render")
def render_page() -> Response:
    """Renders a specific PDF page to a PNG image for the viewer."""
    mag = request.args.get("mag", "")
    try:
        pn = int(request.args.get("page", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "Bad request", "detail": "'page' must be an integer."}), 400
    try:
        zoom = float(request.args.get("zoom", 1.5))
    except (TypeError, ValueError):
        return jsonify({"error": "Bad request", "detail": "'zoom' must be a number."}), 400
    zoom = rendering.clamp_zoom(zoom)

    if not mag or pn < 0:
        return jsonify({"error": "Invalid magazine or page parameters"}), 400

    try:
        pdf_path = get_safe_path(mag)
    except ValueError as e:
        return jsonify({"error": "Bad request", "detail": str(e)}), 400

    try:
        img = rendering.render_page_png(pdf_path, pn, zoom)
        return send_file(io.BytesIO(img), mimetype="image/png")
    except Exception as e:
        logger.error(f"Render failed for {mag} page {pn}: {e}")
        return jsonify({"error": str(e)}), 500


@bp.route("/text")
def get_text() -> Response:
    """Retrieves all text sections and spatial coordinates for a specific page."""
    mag_rel_path = request.args.get("mag", "")
    pg_raw = request.args.get("page", "1")
    if not mag_rel_path:
        return jsonify({"error": "Bad request", "detail": "'mag' is required."}), 400
    if not pg_raw.isdigit():
        return jsonify(
            {"error": "Bad request", "detail": "'page' must be a positive integer."}
        ), 400
    pg = pg_raw.zfill(3)

    try:
        pdf_path = Path(get_safe_path(mag_rel_path))
    except ValueError as e:
        return jsonify({"error": "Bad request", "detail": str(e)}), 400
    content = metadata.get_transcription_text(mag_rel_path, pg)

    # Determine total pages for UI constraints (cached document handle)
    total = rendering.get_page_count(pdf_path)

    # Split Rosetta format into sections (Transcription, Translation, Summary)
    jp, en, sum_t = "No transcription found.", "", ""
    if content:
        jp, en, sum_t = split_sections(content)

    partner_zip = metadata.get_partner_zip(mag_rel_path)
    # Raw metadata for the visual editor + spatial coordinates for highlighting
    raw_meta = metadata.read_raw_metadata(pdf_path, partner_zip)
    coords_data = rendering.get_page_coordinates(pdf_path, partner_zip, int(pg))

    return jsonify(
        {
            "jp": jp,
            "en": en,
            "sum": sum_t,
            "total_pages": total,
            "metadata": state.METADATA_CACHE.get(mag_rel_path, {}),
            "raw_meta": raw_meta,
            "coordinates": coords_data,
        }
    )


@bp.route("/search")
def search() -> Response:
    """Executes full-text search across all transcriptions using advanced query logic."""
    query = request.args.get("q", "")
    try:
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
    except search_index.IndexUnavailableError as e:
        logger.warning(f"Search unavailable: {e}")
        return jsonify({"error": "Search index unavailable", "detail": str(e)}), 503
    return jsonify({"results": results, "terms_to_highlight": highlight_list})


@bp.route("/cover/<item_id>")
def get_cover(item_id: str) -> Response:
    """Fetches cover images. Uses local cache, then remote download via local catalog lookup."""
    v = re.sub(r"[^\w.-]", "", request.args.get("v", "1.0")) or "1.0"
    safe_id = "".join(c for c in item_id if c.isalnum() or c in "_-")
    cache_name = f"{safe_id}_v{v}.cache"

    covers_dir = cfg.covers_dir()
    covers_dir.mkdir(parents=True, exist_ok=True)
    cache_path = covers_dir / cache_name

    # 1. Serve from disk if cached
    if cache_path.exists():
        return send_file(cache_path, mimetype="image/jpeg")

    # 2. Look up URL in the cached by-id catalog index (O(1); no force
    # refresh here to prevent UI lag)
    item = catalog.get_catalog_index(force_refresh=False).get(str(item_id))

    if item and item.get("cover_url"):
        try:
            # safe_urlopen validates the URL and every redirect hop against
            # the scheme/host allowlist.
            with safe_urlopen(item["cover_url"], timeout=cfg.cover_fetch_timeout()) as response:
                img_data = response.read()
                # Clean old version caches (best-effort: a locked/vanished
                # file only means a stale cache entry lingers)
                for old in covers_dir.glob(f"{safe_id}_v*.cache"):
                    try:
                        old.unlink()
                    except Exception as e:
                        logger.debug("Could not remove stale cover cache %s: %s", old, e)
                atomic_write_bytes(cache_path, img_data)
                return send_file(io.BytesIO(img_data), mimetype="image/jpeg")
        except URLBlockedError as e:
            logger.warning(f"Blocked cover URL for {item_id} ({e.reason}): {e.url}")
            return _fallback_cover_svg()
        except Exception as e:
            logger.warning(f"Could not download cover for {item_id}: {e}")

    # 3. Fallback SVG
    return _fallback_cover_svg()


def _fallback_cover_svg() -> Response:
    """Placeholder cover art returned when no cover can be served."""
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="#222"/><text x="50%" y="50%" fill="#666" font-family="sans-serif" font-size="14" text-anchor="middle">No Cover Art</text></svg>'
    return Response(svg, mimetype="image/svg+xml")


@bp.route("/catalog")
def get_catalog() -> Response:
    """Fetches combined catalogs and triggers a background mirror update."""
    return jsonify(catalog.get_all_catalogs(force_refresh=True))


@bp.route("/downloads")
def get_downloads() -> Response:
    """Polled by UI to track current download progress and errors."""
    return jsonify(state.DOWNLOAD_STATE)
