"""Tests for the thumbnail endpoint and its disk cache."""

import os
from pathlib import Path

import pymupdf

from app.services import metadata


def _make_pdf(path: Path, pages: int = 1) -> Path:
    """Create a small real PDF for thumbnail endpoint tests."""
    doc = pymupdf.open()
    for number in range(pages):
        page = doc.new_page(width=100, height=100)
        page.insert_text((10, 50), f"page {number + 1}")
    doc.save(str(path))
    doc.close()
    return path


def _install_pdf(workspace: Path, pages: int = 1) -> Path:
    """Create the test magazine PDF in the configured data directory."""
    magazine_dir = workspace / "Magazines" / "Folder"
    magazine_dir.mkdir()
    return _make_pdf(magazine_dir / "Issue.pdf", pages)


def test_thumb_returns_png_and_writes_cache(client, workspace):
    _install_pdf(workspace)
    response = client.get("/api/thumb?mag=Folder/Issue.pdf&page=0")
    assert response.status_code == 200
    assert response.data[:8] == b"\x89PNG\r\n\x1a\n"
    cached = list((workspace / "Magazines" / ".thumbs").glob("**/0.png"))
    assert len(cached) == 1


def test_thumb_cache_hit_skips_render(client, workspace, monkeypatch):
    _install_pdf(workspace)
    first = client.get("/api/thumb?mag=Folder/Issue.pdf&page=0")

    def fail_render(*args):
        raise AssertionError("thumbnail cache was not used")

    monkeypatch.setattr("app.services.rendering.render_page_png", fail_render)
    second = client.get("/api/thumb?mag=Folder/Issue.pdf&page=0")
    assert second.status_code == 200
    assert second.data == first.data


def test_thumb_invalidates_on_pdf_change(client, workspace):
    pdf = _install_pdf(workspace)
    assert client.get("/api/thumb?mag=Folder/Issue.pdf&page=0").status_code == 200
    thumbs_dir = workspace / "Magazines" / ".thumbs"
    old_dirs = list(thumbs_dir.iterdir())
    assert len(old_dirs) == 1
    old_dir = old_dirs[0]

    _make_pdf(pdf, pages=2)
    os.utime(pdf, ns=(pdf.stat().st_atime_ns, pdf.stat().st_mtime_ns + 1_000_000))
    response = client.get("/api/thumb?mag=Folder/Issue.pdf&page=0")
    assert response.status_code == 200
    new_dirs = list(thumbs_dir.iterdir())
    assert old_dir not in new_dirs
    assert len(new_dirs) == 1


def test_thumb_rejects_traversal(client):
    response = client.get("/api/thumb?mag=../outside.pdf&page=0")
    assert response.status_code == 400


def test_thumb_bad_page(client, workspace):
    _install_pdf(workspace)
    assert client.get("/api/thumb?mag=Folder/Issue.pdf&page=-1").status_code == 400
    assert client.get("/api/thumb?mag=Folder/Issue.pdf&page=abc").status_code == 400


def test_thumb_text_only_404(client, workspace):
    response = client.get("/api/thumb?mag=Folder/Missing.pdf&page=0")
    assert response.status_code == 404
    assert response.get_json()["error"] == "no_pdf"


def test_thumbs_dir_not_listed_as_magazines(client, workspace):
    _install_pdf(workspace)
    assert client.get("/api/thumb?mag=Folder/Issue.pdf&page=0").status_code == 200
    listed = client.get("/api/list").get_json()["files"]
    assert not any(".thumbs" in path for path in listed)
    metadata.load_metadata_cache()
    assert not any(".thumbs" in path for path in metadata.state.METADATA_CACHE)
