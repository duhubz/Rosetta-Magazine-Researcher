"""Unit tests for app/services/rendering.py (extracted from the API routes)."""

import json
import zipfile

import fitz  # PyMuPDF

from app.services import rendering

# --- clamp_zoom -----------------------------------------------------------------


def test_clamp_zoom_bounds():
    assert rendering.clamp_zoom(100.0) == rendering.MAX_ZOOM
    assert rendering.clamp_zoom(0.0) == rendering.MIN_ZOOM
    assert rendering.clamp_zoom(1.5) == 1.5


# --- render_page_png / get_page_count -------------------------------------------


def _make_pdf(path, pages=2):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=100, height=100)
        page.insert_text((10, 50), f"page {i + 1}")
    doc.save(str(path))
    doc.close()
    return path


def test_render_page_png_returns_png_bytes(tmp_path):
    pdf = _make_pdf(tmp_path / "mag.pdf")
    img = rendering.render_page_png(pdf, 0, 1.0)
    assert img[:8] == b"\x89PNG\r\n\x1a\n"


def test_get_page_count(tmp_path):
    pdf = _make_pdf(tmp_path / "mag.pdf", pages=3)
    assert rendering.get_page_count(pdf) == 3


def test_get_page_count_bad_pdf_returns_zero(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf at all")
    assert rendering.get_page_count(bad) == 0


# --- coordinate helpers ----------------------------------------------------------

_COORDS = [
    {"page": 1, "data": [{"x": 1}]},
    {"page": 2, "data": [{"x": 2}]},
]


def test_get_page_coordinates_from_loose_file(tmp_path):
    pdf = tmp_path / "mag.pdf"
    pdf.write_bytes(b"%PDF fake")
    (tmp_path / "mag_COORDINATES.json").write_text(json.dumps(_COORDS), encoding="utf-8")
    assert rendering.get_page_coordinates(pdf, None, 2) == [{"x": 2}]
    assert rendering.get_page_coordinates(pdf, None, 9) == []


def test_get_page_coordinates_from_partner_zip(tmp_path):
    pdf = tmp_path / "mag.pdf"
    pdf.write_bytes(b"%PDF fake")
    zp = tmp_path / "mag.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("Data/MAG_coordinates.JSON", json.dumps(_COORDS))
    assert rendering.get_page_coordinates(pdf, zp, 1) == [{"x": 1}]


def test_load_all_coordinates_missing_returns_empty(tmp_path):
    pdf = tmp_path / "mag.pdf"
    assert rendering.load_all_coordinates(pdf, None) == []


def test_load_all_coordinates_corrupt_json_returns_empty(tmp_path):
    pdf = tmp_path / "mag.pdf"
    (tmp_path / "mag_COORDINATES.json").write_text("{not json", encoding="utf-8")
    assert rendering.load_all_coordinates(pdf, None) == []


def test_merge_page_coordinates_replaces_existing():
    coords = [{"page": 1, "data": ["old"]}]
    out = rendering.merge_page_coordinates(coords, 1, ["new"])
    assert out == [{"page": 1, "data": ["new"]}]


def test_merge_page_coordinates_appends_new_page():
    coords = [{"page": 1, "data": ["a"]}]
    out = rendering.merge_page_coordinates(coords, 2, ["b"])
    assert out == [{"page": 1, "data": ["a"]}, {"page": 2, "data": ["b"]}]
