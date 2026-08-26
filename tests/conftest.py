"""Shared fixtures: temp workspace, sample catalog data, Flask test client."""

import sys
from pathlib import Path

import pytest

# Ensure the repo root is importable when pytest is run from anywhere.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import app.config as cfg  # noqa: E402
from app.services import catalog, pdf_cache, search_index, state  # noqa: E402


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A temporary data workspace with all cfg path accessors redirected."""
    data_dir = tmp_path / "Magazines"
    data_dir.mkdir()
    catalogs_dir = tmp_path / "Catalogs"
    catalogs_dir.mkdir()
    covers_dir = tmp_path / "Covers"
    covers_dir.mkdir()
    bookmarks_file = tmp_path / "bookmarks.json"
    catalog_file = tmp_path / "catalog.json"

    monkeypatch.setattr(cfg, "data_dir", lambda: data_dir)
    monkeypatch.setattr(cfg, "bookmarks_file", lambda: bookmarks_file)
    monkeypatch.setattr(cfg, "catalog_file", lambda: catalog_file)
    monkeypatch.setattr(cfg, "catalogs_dir", lambda: catalogs_dir)
    monkeypatch.setattr(cfg, "covers_dir", lambda: covers_dir)

    return tmp_path


def _reset_caches():
    """Reset all module-level caches/singletons (search index, PDF LRU, catalogs)."""
    search_index.close_index()
    pdf_cache.close_all()
    with catalog._CACHE_LOCK:
        catalog._CATALOG_CACHE.clear()
        catalog._BY_ID = {}
        catalog._by_id_version = -1
        catalog._cache_version = 0


@pytest.fixture(autouse=True)
def clean_state():
    """Reset global mutable state around every test."""
    state.METADATA_CACHE = {}
    state.DOWNLOAD_STATE.clear()
    state.SHUTDOWN_EVENT.clear()
    _reset_caches()
    yield
    state.METADATA_CACHE = {}
    state.DOWNLOAD_STATE.clear()
    state.SHUTDOWN_EVENT.clear()
    _reset_caches()


@pytest.fixture
def sample_catalog_item():
    """A well-formed catalog entry."""
    return {
        "id": "smm_01",
        "magazine_name": "Super Mario Magazine",
        "publisher": "Shogakukan",
        "date": "1992-10",
        "issue_name": "Vol 1",
        "version": "1.0",
        "pdf_filename": "SMM_1992_10.pdf",
        "zip_filename": "SMM_1992_10_Data.zip",
        "pdf_sources": [],
        "zip_sources": [],
    }


@pytest.fixture
def malicious_catalog_item():
    """A catalog entry attempting path traversal in every derived field."""
    return {
        "id": "evil_01",
        "magazine_name": "../../EscapedMag",
        "date": "../..",
        "issue_name": "../outside",
        "version": "9.9",
        "pdf_filename": "../../evil.pdf",
        "zip_filename": "../../evil_Data.zip",
        "pdf_sources": [],
        "zip_sources": [],
    }


@pytest.fixture
def flask_app(workspace):
    from app import create_app

    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture
def token():
    return state.SESSION_TOKEN


@pytest.fixture
def token_headers(token):
    return {"X-Rosetta-Token": token}
