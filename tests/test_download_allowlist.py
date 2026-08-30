"""Tests for the download-source URL allowlist (pdf_sources / zip_sources).

The download waterfall must never fetch non-http(s) or non-allowlisted URLs
supplied by community catalogs; blocked sources are skipped (next mirror is
tried), and a fully-blocked waterfall ends in a clear error state.
"""

import io
import logging
import urllib.request

import pytest

import app.config as cfg
from app.services import download, state

PDF_BYTES = b"%PDF-1.4 fake content"


class _FakeResponse:
    """Minimal stand-in for urllib's HTTP response (headers + chunked read)."""

    def __init__(self, data: bytes = PDF_BYTES):
        self._buf = io.BytesIO(data)
        self.headers = {"Content-Length": str(len(data))}

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


@pytest.fixture
def fetched_urls(monkeypatch):
    """Mock urllib.request.urlopen; record every URL actually fetched."""
    urls: list[str] = []

    def fake_urlopen(req, timeout=None):
        urls.append(req.full_url if hasattr(req, "full_url") else str(req))
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return urls


def _seed_task(task_id: str) -> None:
    state.DOWNLOAD_STATE[task_id] = {
        "status": "Initializing...", "progress": 0, "error": None, "done": False,
    }


# --- waterfall-level behavior --------------------------------------------------

def test_file_url_rejected_and_logged_next_source_used(workspace, fetched_urls, caplog):
    """file:///etc/passwd is never fetched; the waterfall moves on to the
    next (allowlisted) mirror and succeeds."""
    out = workspace / "Magazines" / "out.pdf"
    _seed_task("t_file")
    sources = ["file:///etc/passwd", "https://archive.org/download/item/x.pdf"]

    with caplog.at_level(logging.WARNING):
        ok = download.download_waterfall("t_file", out, sources, "PDF")

    assert ok is True
    assert len(fetched_urls) == 1
    assert fetched_urls[0].startswith("https://archive.org/download/item/x.pdf")
    assert out.read_bytes() == PDF_BYTES
    assert "file:///etc/passwd" in caplog.text
    assert "scheme not http/https" in caplog.text


def test_non_allowlisted_host_rejected_and_logged(workspace, fetched_urls, caplog):
    """evil.com is not in the allowlist -> skipped with a logged reason."""
    out = workspace / "Magazines" / "out.pdf"
    _seed_task("t_evil")
    sources = ["http://evil.com/x.pdf", "https://archive.org/download/item/x.pdf"]

    with caplog.at_level(logging.WARNING):
        ok = download.download_waterfall("t_evil", out, sources, "PDF")

    assert ok is True
    assert all("evil.com" not in u for u in fetched_urls)
    assert "http://evil.com/x.pdf" in caplog.text
    assert "host not in allowlist" in caplog.text


@pytest.mark.parametrize("url", [
    "https://archive.org/download/item/x.pdf",
    "https://ia801408.us.archive.org/12/items/x/x.pdf",
    "https://www.gamingalexandria.com/files/x.pdf",
])
def test_allowlisted_hosts_are_fetched(workspace, fetched_urls, url):
    out = workspace / "Magazines" / "out.pdf"
    _seed_task("t_ok")

    ok = download.download_waterfall("t_ok", out, [url], "PDF")

    assert ok is True
    assert len(fetched_urls) == 1 and fetched_urls[0].startswith(url)
    assert out.read_bytes() == PDF_BYTES


def test_public_mega_source_uses_mega_provider(workspace, monkeypatch):
    out = workspace / "Magazines" / "out.pdf"
    _seed_task("t_mega")
    source = "https://mega.nz/file/8t5EDIZD#6tYoTihYmdxdiEVIFXDJU2qEv7nCuKxe3xwgm3nDxY4"
    called = []

    def fake_mega_download(url, out_path):
        called.append((url, out_path))
        out_path.write_bytes(PDF_BYTES)

    monkeypatch.setattr(download.mega_download, "download_public_file", fake_mega_download)

    assert download.download_waterfall("t_mega", out, [source], "PDF") is True
    assert called == [(source, out)]
    assert out.read_bytes() == PDF_BYTES


def test_dict_shaped_source_entries(workspace, fetched_urls):
    """Per-source dicts with a 'url' key follow the same allowlist rules."""
    out = workspace / "Magazines" / "out.pdf"
    _seed_task("t_dict")
    sources = [
        {"url": "http://evil.com/x.pdf"},
        {"url": "https://archive.org/download/item/x.pdf"},
    ]

    ok = download.download_waterfall("t_dict", out, sources, "PDF")

    assert ok is True
    assert len(fetched_urls) == 1
    assert "archive.org" in fetched_urls[0]


def test_bypass_flag_allows_any_host_but_not_file(workspace, fetched_urls, monkeypatch, caplog):
    """allow_downloads_from_any_host=true skips the host check, but file://
    (and every non-http(s) scheme) is still rejected."""
    monkeypatch.setattr(cfg, "allow_downloads_from_any_host", lambda: True)
    out = workspace / "Magazines" / "out.pdf"
    _seed_task("t_bypass")
    sources = ["file:///etc/passwd", "javascript:alert(1)", "http://evil.com/x.pdf"]

    with caplog.at_level(logging.WARNING):
        ok = download.download_waterfall("t_bypass", out, sources, "PDF")

    assert ok is True
    assert fetched_urls and fetched_urls[0].startswith("http://evil.com/x.pdf")
    assert all(not u.startswith("file:") for u in fetched_urls)
    assert "scheme not http/https" in caplog.text


# --- worker-level (end state) behavior ------------------------------------------

def test_all_sources_blocked_ends_in_error_state(workspace, fetched_urls, sample_catalog_item):
    """If every pdf_sources entry is blocked, nothing is fetched and the job
    ends done=True with an actionable error message."""
    item = dict(
        sample_catalog_item,
        pdf_sources=["file:///etc/passwd", "http://evil.com/x.pdf"],
    )

    download.download_worker("task_blocked", item)

    task = state.DOWNLOAD_STATE["task_blocked"]
    assert task["done"] is True
    assert "blocked or failed" in (task["error"] or "")
    assert fetched_urls == []
    # DOWNLOAD_STATE schema stays intact for the polling frontend
    assert {"status", "progress", "error", "done"} <= set(task.keys())


def test_zip_sources_follow_same_rules(workspace, fetched_urls, sample_catalog_item, caplog):
    """zip_sources entries are allowlisted too: all-blocked ZIP mirrors fail
    the job even when the PDF download succeeded."""
    item = dict(
        sample_catalog_item,
        pdf_sources=["https://archive.org/download/item/x.pdf"],
        zip_sources=["file:///etc/shadow", "http://evil.com/data.zip"],
    )

    with caplog.at_level(logging.WARNING):
        download.download_worker("task_zip_blocked", item)

    task = state.DOWNLOAD_STATE["task_zip_blocked"]
    assert task["done"] is True
    assert "blocked or failed" in (task["error"] or "")
    # Only the allowlisted PDF source was ever fetched
    assert len(fetched_urls) == 1 and "archive.org" in fetched_urls[0]
    assert "file:///etc/shadow" in caplog.text
    assert "http://evil.com/data.zip" in caplog.text


def test_zip_waterfall_skips_blocked_mirror_and_installs(workspace, fetched_urls, sample_catalog_item):
    """A blocked ZIP mirror is skipped in favor of an allowlisted one and the
    install completes without error (waterfall semantics preserved)."""
    item = dict(
        sample_catalog_item,
        pdf_sources=["https://archive.org/download/item/SMM_1992_10.pdf"],
        zip_sources=[
            "http://evil.com/data.zip",
            "https://ia800100.us.archive.org/items/x/SMM_Data.zip",
        ],
    )

    download.download_worker("task_zip_ok", item)

    task = state.DOWNLOAD_STATE["task_zip_ok"]
    assert task["done"] is True
    assert not task["error"]
    assert all("evil.com" not in u for u in fetched_urls)
    assert len(fetched_urls) == 2
    installed_pdf = (
        workspace / "Magazines" / "Super Mario Magazine" / "1992-10 - Vol 1" / "SMM_1992_10.pdf"
    )
    assert installed_pdf.exists()
