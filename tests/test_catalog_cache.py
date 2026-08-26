"""Catalog cache tests: mtime-based reuse/invalidation, by-id index,
pruning of deleted files, and thread safety."""

import json
import os
import threading

import pytest

import app.config as cfg
from app.services import catalog


def _write_catalog(path, items):
    path.write_text(json.dumps({"items": items}), encoding="utf-8")


@pytest.fixture
def community_catalog(workspace):
    path = cfg.catalogs_dir() / "community.json"
    _write_catalog(path, [
        {"id": "mag_1", "magazine_name": "Mag One", "cover_url": "https://archive.org/1.jpg"},
        {"id": "mag_2", "magazine_name": "Mag Two"},
    ])
    return path


@pytest.fixture
def parse_counter(monkeypatch):
    """Counts how many times catalog JSON is actually parsed from disk."""
    counter = {"n": 0}
    real = catalog._parse_catalog_text

    def counting(raw):
        counter["n"] += 1
        return real(raw)

    monkeypatch.setattr(catalog, "_parse_catalog_text", counting)
    return counter


def test_cache_hit_when_mtime_unchanged(community_catalog, parse_counter):
    first = catalog.get_all_catalogs(force_refresh=False)
    assert parse_counter["n"] == 1
    second = catalog.get_all_catalogs(force_refresh=False)
    assert parse_counter["n"] == 1  # served from cache, no reparse
    assert first == second
    assert len(first) == 2


def test_reparse_when_mtime_changes(community_catalog, parse_counter):
    catalog.get_all_catalogs(force_refresh=False)
    assert parse_counter["n"] == 1

    _write_catalog(community_catalog, [{"id": "mag_3", "magazine_name": "Mag Three"}])
    st = community_catalog.stat()
    os.utime(community_catalog, (st.st_atime, st.st_mtime + 5))

    items = catalog.get_all_catalogs(force_refresh=False)
    assert parse_counter["n"] == 2
    assert [i["id"] for i in items] == ["mag_3"]


def test_by_id_index_correctness(community_catalog):
    index = catalog.get_catalog_index(force_refresh=False)
    assert set(index) == {"mag_1", "mag_2"}
    assert index["mag_1"]["magazine_name"] == "Mag One"


def test_by_id_index_reused_until_cache_changes(community_catalog):
    index1 = catalog.get_catalog_index(force_refresh=False)
    index2 = catalog.get_catalog_index(force_refresh=False)
    assert index1 is index2  # same object: not rebuilt

    _write_catalog(community_catalog, [{"id": "mag_9"}])
    st = community_catalog.stat()
    os.utime(community_catalog, (st.st_atime, st.st_mtime + 5))
    index3 = catalog.get_catalog_index(force_refresh=False)
    assert index3 is not index1
    assert set(index3) == {"mag_9"}


def test_deleted_file_dropped_from_cache(community_catalog):
    catalog.get_all_catalogs(force_refresh=False)
    assert str(community_catalog) in catalog._CATALOG_CACHE

    community_catalog.unlink()
    items = catalog.get_all_catalogs(force_refresh=False)
    assert items == []
    assert str(community_catalog) not in catalog._CATALOG_CACHE
    assert catalog.get_catalog_index(force_refresh=False) == {}


def test_official_catalog_file_uses_cache_too(workspace, parse_counter):
    _write_catalog(cfg.catalog_file(), [{"id": "official_1"}])
    catalog.get_all_catalogs(force_refresh=False)
    catalog.get_all_catalogs(force_refresh=False)
    assert parse_counter["n"] == 1


def test_thread_safe_concurrent_access(community_catalog):
    """Concurrent readers + a writer bumping mtimes: no exceptions, sane data."""
    errors = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                items = catalog.get_all_catalogs(force_refresh=False)
                index = catalog.get_catalog_index(force_refresh=False)
                assert isinstance(items, list)
                assert isinstance(index, dict)
            except Exception as e:  # pragma: no cover
                errors.append(e)
                return

    def writer():
        for i in range(20):
            _write_catalog(community_catalog, [{"id": f"mag_{i}"}])
            st = community_catalog.stat()
            os.utime(community_catalog, (st.st_atime, st.st_mtime + i + 1))

    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in readers: t.start()
    w = threading.Thread(target=writer)
    w.start(); w.join()
    stop.set()
    for t in readers: t.join()
    assert errors == []
