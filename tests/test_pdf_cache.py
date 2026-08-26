"""PDF handle cache tests: hits, LRU eviction, per-doc lock serialization,
explicit eviction and close_all."""

import threading
import time

import fitz
import pytest

import app.config as cfg
from app.services import pdf_cache


@pytest.fixture
def pdf_factory(tmp_path):
    def make(name="doc.pdf", pages=1):
        path = tmp_path / name
        doc = fitz.open()
        for _ in range(pages):
            doc.new_page()
        doc.save(str(path))
        doc.close()
        return path
    return make


def test_cache_hit_returns_same_document(pdf_factory):
    path = pdf_factory("a.pdf")
    with pdf_cache.get_doc(path) as d1:
        first_id = id(d1)
        assert len(d1) == 1
    with pdf_cache.get_doc(path) as d2:
        assert id(d2) == first_id
        assert not d2.is_closed


def test_eviction_on_capacity(pdf_factory, monkeypatch):
    monkeypatch.setattr(cfg, "pdf_cache_max_open_documents", lambda: 2)
    p1, p2, p3 = pdf_factory("a.pdf"), pdf_factory("b.pdf"), pdf_factory("c.pdf")
    with pdf_cache.get_doc(p1) as d1:
        pass
    with pdf_cache.get_doc(p2):
        pass
    with pdf_cache.get_doc(p3):
        pass
    # LRU (p1) was evicted and closed; p2/p3 remain open.
    assert d1.is_closed
    assert len(pdf_cache._CACHE) == 2
    keys = list(pdf_cache._CACHE)
    assert str(p1.resolve()) not in keys
    # Re-requesting p1 transparently reopens it.
    with pdf_cache.get_doc(p1) as d1b:
        assert not d1b.is_closed


def test_per_doc_lock_serializes_concurrent_access(pdf_factory):
    path = pdf_factory("a.pdf")
    events = []
    events_lock = threading.Lock()

    def worker(tag):
        with pdf_cache.get_doc(path) as doc:
            with events_lock:
                events.append((tag, "enter"))
            assert not doc.is_closed
            doc.load_page(0)
            time.sleep(0.05)
            with events_lock:
                events.append((tag, "exit"))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()

    # Strict serialization: every 'enter' is immediately followed by the
    # same thread's 'exit' — no interleaving on the same document.
    assert len(events) == 8
    for i in range(0, 8, 2):
        assert events[i][0] == events[i + 1][0]
        assert (events[i][1], events[i + 1][1]) == ("enter", "exit")


def test_evict_closes_specific_document(pdf_factory):
    p1, p2 = pdf_factory("a.pdf"), pdf_factory("b.pdf")
    with pdf_cache.get_doc(p1) as d1: pass
    with pdf_cache.get_doc(p2) as d2: pass
    pdf_cache.evict(p1)
    assert d1.is_closed
    assert not d2.is_closed
    assert str(p1.resolve()) not in pdf_cache._CACHE
    # Evicting an unknown path is a no-op.
    pdf_cache.evict(p1)


def test_close_all_closes_everything(pdf_factory):
    docs = []
    for name in ("a.pdf", "b.pdf"):
        with pdf_cache.get_doc(pdf_factory(name)) as d:
            docs.append(d)
    pdf_cache.close_all()
    assert all(d.is_closed for d in docs)
    assert len(pdf_cache._CACHE) == 0


def test_closed_doc_is_reopened_transparently(pdf_factory):
    """A doc closed behind the cache's back (or evicted) is reopened."""
    path = pdf_factory("a.pdf")
    with pdf_cache.get_doc(path) as d:
        pass
    d.close()  # simulate external close
    with pdf_cache.get_doc(path) as d2:
        assert not d2.is_closed
        assert len(d2) == 1
