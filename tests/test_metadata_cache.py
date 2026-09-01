"""Concurrency test: metadata cache reloads must never break iterating readers."""

import threading

from app.services import metadata, state


def _make_library(data_dir, count=5):
    for i in range(count):
        mag_dir = data_dir / f"Mag{i}"
        mag_dir.mkdir()
        (mag_dir / f"issue{i}.pdf").write_bytes(b"%PDF-fake")
        (mag_dir / f"issue{i}.metadata.txt").write_text(
            f"Name: Magazine {i}\nDate: 199{i}-01", encoding="utf-8"
        )


def test_concurrent_reload_and_iteration(workspace):
    data_dir = workspace / "Magazines"
    _make_library(data_dir)
    metadata.load_metadata_cache()
    assert len(state.METADATA_CACHE) == 5

    errors = []
    stop = threading.Event()

    def reloader():
        while not stop.is_set():
            try:
                metadata.load_metadata_cache()
            except Exception as e:  # pragma: no cover
                errors.append(e)
                return

    def reader():
        while not stop.is_set():
            try:
                # Snapshot-style iteration, as used in search.py / api.py
                snapshot = list(state.METADATA_CACHE.items())
                for _rel_path, meta in snapshot:
                    meta.get("name")
                # Repeated lookups against the (possibly swapped) global
                cache = state.METADATA_CACHE
                for key in list(cache.keys()):
                    cache.get(key, {})
            except Exception as e:  # pragma: no cover
                errors.append(e)
                return

    threads = [threading.Thread(target=reloader) for _ in range(2)]
    threads += [threading.Thread(target=reader) for _ in range(4)]
    for t in threads:
        t.start()
    # Let them contend for a moment
    import time

    time.sleep(1.0)
    stop.set()
    for t in threads:
        t.join(timeout=5)

    assert errors == []
    # Cache is intact after the storm
    assert len(state.METADATA_CACHE) == 5


def test_reload_swaps_binding_never_exposes_empty_cache(workspace):
    """The reload must rebind the global, not clear-then-refill in place."""
    data_dir = workspace / "Magazines"
    _make_library(data_dir, count=3)
    metadata.load_metadata_cache()
    original = state.METADATA_CACHE

    metadata.load_metadata_cache()
    # A reload produces a NEW dict object (atomic swap)...
    assert state.METADATA_CACHE is not original
    # ...and the old snapshot a reader may still hold remains fully populated.
    assert len(original) == 3
