"""
PDF Document Cache
Thread-safe LRU of open fitz (PyMuPDF) Document handles, so page turns don't
reopen the same PDF from disk on every /api/render and /api/text request.

Design:
- An OrderedDict maps resolved path -> (Document, per-document Lock),
  guarded by a module-level lock for cache bookkeeping.
- fitz Documents are NOT safe for concurrent access to the same document,
  so get_doc() is a context manager that holds the document's own lock for
  the duration of the `with` block.
- Evicted/removed documents are closed under their per-document lock so a
  render in progress can never have its handle closed underneath it.
"""

import collections
import contextlib
import logging
import threading
from collections.abc import Iterator
from pathlib import Path

import fitz  # PyMuPDF

import app.config as cfg

logger = logging.getLogger(__name__)

_CACHE_LOCK = threading.Lock()
_CACHE: "collections.OrderedDict[str, tuple[fitz.Document, threading.Lock]]" = (
    collections.OrderedDict()
)


def _max_size() -> int:
    return max(1, cfg.pdf_cache_max_open_documents())


def _close_entry(doc: fitz.Document, doc_lock: threading.Lock) -> None:
    """Closes a document after acquiring its per-document lock."""
    with doc_lock:
        try:
            doc.close()
        except Exception as e:
            # Best-effort cleanup: a close failure only leaks one handle.
            logger.debug("Error closing cached PDF document: %s", e)


@contextlib.contextmanager
def get_doc(pdf_path: Path) -> Iterator[fitz.Document]:
    """
    Yields an open fitz.Document for `pdf_path`, held under its per-document
    lock for the duration of the `with` block.

    Opens and caches the document on miss, evicting least-recently-used
    entries beyond the configured capacity. If fitz.open fails the exception
    propagates to the caller (matching the previous direct-open behavior).
    """
    key = str(Path(pdf_path).resolve())

    while True:
        evicted: list[tuple[fitz.Document, threading.Lock]] = []
        with _CACHE_LOCK:
            entry = _CACHE.get(key)
            if entry is None or entry[0].is_closed:
                doc = fitz.open(str(pdf_path))
                entry = (doc, threading.Lock())
                _CACHE[key] = entry
            _CACHE.move_to_end(key)
            while len(_CACHE) > _max_size():
                _, old_entry = _CACHE.popitem(last=False)
                evicted.append(old_entry)

        # Close evicted docs outside the cache lock (their per-doc lock may
        # be held by an in-flight render; closing waits for it to finish).
        for old_doc, old_lock in evicted:
            _close_entry(old_doc, old_lock)

        doc, doc_lock = entry
        with doc_lock:
            if doc.is_closed:
                # Lost a race with evict()/close_all(); retry with a fresh open.
                continue
            yield doc
            return


def evict(pdf_path: Path) -> None:
    """Removes and closes a specific document (called from /api/uninstall)."""
    key = str(Path(pdf_path).resolve())
    with _CACHE_LOCK:
        entry = _CACHE.pop(key, None)
    if entry is not None:
        _close_entry(*entry)


def close_all() -> None:
    """Closes every cached document (cooperative shutdown / tests)."""
    with _CACHE_LOCK:
        entries = list(_CACHE.values())
        _CACHE.clear()
    for entry in entries:
        _close_entry(*entry)
