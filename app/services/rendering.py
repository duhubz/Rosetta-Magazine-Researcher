"""
Rendering Service
PDF page rendering and spatial-coordinate lookup, extracted from the API
routes so they stay thin HTTP wrappers.

Coordinates live in '<stem>_COORDINATES.json' (loose file next to the PDF or
a member of the partner ZIP): a list of {"page": n, "data": [...]} entries.
"""

import json
import logging
import zipfile
from pathlib import Path
from typing import Any

import pymupdf

from app.services import pdf_cache, zip_utils

logger = logging.getLogger(__name__)

# Sane zoom bounds so a hostile/buggy request can't allocate a huge pixmap.
MIN_ZOOM = 0.25
MAX_ZOOM = 4.0


def clamp_zoom(zoom: float) -> float:
    """Clamps a requested zoom factor to bound pixmap memory usage."""
    return max(MIN_ZOOM, min(zoom, MAX_ZOOM))


def render_page_png(pdf_path: Path, page_number: int, zoom: float) -> bytes:
    """
    Renders one PDF page to PNG bytes at the given (pre-clamped) zoom.

    Uses the shared document cache; the per-document lock is held for the
    duration of the render (pymupdf Documents are not thread-safe). Exceptions
    (bad page number, corrupt PDF) propagate to the caller.
    """
    with pdf_cache.get_doc(pdf_path) as doc:
        page = doc.load_page(page_number)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        return pix.tobytes("png")


def get_page_count(pdf_path: Path) -> int:
    """Best-effort page count for UI constraints; 0 when the PDF can't be opened."""
    try:
        with pdf_cache.get_doc(pdf_path) as doc:
            return len(doc)
    except Exception as e:
        logger.warning("Could not determine page count for %s: %s", pdf_path, e)
        return 0


def coordinates_filename(pdf_path: Path) -> str:
    """Name of the coordinates sidecar for a given PDF."""
    return f"{pdf_path.stem}_COORDINATES.json"


def load_all_coordinates(pdf_path: Path, partner_zip: Path | None) -> list[dict[str, Any]]:
    """
    Loads the full coordinates list for a magazine (all pages).

    Reads from the partner ZIP when present, otherwise from the loose JSON
    file next to the PDF. Returns [] when missing or unparseable (logged).
    """
    coords_fn = coordinates_filename(pdf_path)

    if partner_zip:
        try:
            with zipfile.ZipFile(partner_zip, "r") as z:
                member = zip_utils.find_member_by_basename(z, coords_fn)
                if member:
                    return json.loads(z.read(member).decode("utf-8"))
        except Exception as e:
            logger.warning("Could not read coordinates from %s: %s", partner_zip, e)
        return []

    loose = pdf_path.parent / coords_fn
    if loose.exists():
        try:
            return json.loads(loose.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Could not parse coordinates file %s: %s", loose, e)
    return []


def get_page_coordinates(pdf_path: Path, partner_zip: Path | None, page_number: int) -> list[Any]:
    """Returns the coordinate entries for one page ([] when absent)."""
    all_coords = load_all_coordinates(pdf_path, partner_zip)
    return next(
        (c.get("data", []) for c in all_coords if str(c.get("page")) == str(page_number)),
        [],
    )


def merge_page_coordinates(
    all_coords: list[dict[str, Any]], page_number: int, page_data: list[Any]
) -> list[dict[str, Any]]:
    """
    Replaces (or appends) the coordinate entry for one page in the full list.
    Mutates and returns `all_coords`.
    """
    for entry in all_coords:
        if str(entry.get("page")) == str(page_number):
            entry["data"] = page_data
            return all_coords
    all_coords.append({"page": page_number, "data": page_data})
    return all_coords
