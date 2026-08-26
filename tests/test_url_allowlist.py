"""Tests for the fetch-URL allowlist (schemes + hosts) and its use in catalog.py."""

import io
import json
import urllib.request

import pytest

from app.services import catalog
from app.utils import is_allowed_fetch_url


# --- is_allowed_fetch_url ------------------------------------------------------

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "javascript:alert(1)",
    "ftp://archive.org/file",
    "data:text/html,<script>alert(1)</script>",
    "http://evil.com/catalog.json",
    "https://gamingalexandria.com.evil.com/catalog.json",
    "https://notarchive.org/file",
    "",
])
def test_rejected_urls(url):
    assert is_allowed_fetch_url(url) is False


@pytest.mark.parametrize("url", [
    "https://gamingalexandria.com/catalog.json",
    "https://www.gamingalexandria.com/catalog.json",
    "https://archive.org/download/x.zip",
    "https://ia800100.us.archive.org/item/x.pdf",
    "http://archive.org/legacy",
])
def test_accepted_urls(url):
    assert is_allowed_fetch_url(url) is True


def test_custom_hosts_from_config(monkeypatch):
    import app.config as cfg
    monkeypatch.setattr(cfg, "allowed_fetch_hosts", lambda: ["example.org", "*.cdn.example.org"])
    assert is_allowed_fetch_url("https://example.org/x") is True
    assert is_allowed_fetch_url("https://a.cdn.example.org/x") is True
    assert is_allowed_fetch_url("https://gamingalexandria.com/x") is False


# --- catalog.py integration ----------------------------------------------------

class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_update_url_blocked_for_non_allowlisted_host(workspace, monkeypatch):
    """A community catalog with an evil update_url must never be fetched."""
    catalogs_dir = workspace / "Catalogs"
    (catalogs_dir / "community.json").write_text(json.dumps({
        "update_url": "http://evil.com/steal",
        "items": [{"id": "c1", "magazine_name": "Community Mag"}],
    }), encoding="utf-8")

    fetched_urls = []

    def fake_urlopen(req, timeout=None):
        fetched_urls.append(req.full_url if hasattr(req, "full_url") else str(req))
        return _FakeResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("app.config.catalog_urls", lambda: [])

    items = catalog.get_all_catalogs(force_refresh=True)

    assert all("evil.com" not in u for u in fetched_urls)
    # The local copy of the catalog is still served
    assert any(i.get("id") == "c1" for i in items)


def test_update_url_allowed_host_is_fetched(workspace, monkeypatch):
    catalogs_dir = workspace / "Catalogs"
    (catalogs_dir / "community.json").write_text(json.dumps({
        "update_url": "https://gamingalexandria.com/community.json",
        "items": [{"id": "c1", "magazine_name": "Old"}],
    }), encoding="utf-8")

    updated = {
        "update_url": "https://gamingalexandria.com/community.json",
        "items": [{"id": "c1", "magazine_name": "New"}],
    }
    fetched_urls = []

    def fake_urlopen(req, timeout=None):
        fetched_urls.append(req.full_url if hasattr(req, "full_url") else str(req))
        return _FakeResponse(json.dumps(updated).encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("app.config.catalog_urls", lambda: [])

    items = catalog.get_all_catalogs(force_refresh=True)

    assert fetched_urls == ["https://gamingalexandria.com/community.json"]
    assert any(i.get("magazine_name") == "New" for i in items)


def test_official_catalog_url_scheme_blocked(workspace, monkeypatch):
    """file:// catalog URLs in config must be blocked before fetching."""
    fetched = []

    def fake_urlopen(req, timeout=None):
        fetched.append(req)
        return _FakeResponse(b"[]")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr("app.config.catalog_urls", lambda: ["file:///etc/passwd"])

    catalog.get_all_catalogs(force_refresh=True)
    assert fetched == []
