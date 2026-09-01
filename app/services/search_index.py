"""
Search Index Service
Maintains a persistent SQLite FTS5 full-text index of all transcription /
translation text, so search runs in milliseconds instead of re-reading and
regex-scanning every file in the library on each query.

Storage: <data_dir>/.rosetta_index.db (dot-prefixed so it is excluded from
the library's rglob("*.pdf") scans, which skip all hidden components).

Schema:
    pages       — FTS5 virtual table, one row per (magazine, page).
    index_meta  — per-magazine source mtime, enabling incremental rebuilds.

Thread safety: SQLite connections are not safe for unsynchronized cross-thread
use. This module keeps one shared connection (check_same_thread=False) and
serializes ALL access through a module-level RLock. Measured on a synthetic
20-magazine/2000-page library, holding the lock for a full query costs well
under a millisecond, so a single guarded connection beats per-request opens.
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path

import app.config as cfg
from app.services import metadata, state, zip_utils
from app.services.text_utils import split_pages

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(
    pdf_path UNINDEXED,
    page UNINDEXED,
    text,
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS index_meta (
    pdf_path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    indexed_at REAL NOT NULL
);
"""

# Module-level singleton connection, guarded by _LOCK for every operation.
_LOCK = threading.RLock()
_conn: sqlite3.Connection | None = None
_conn_db_path: Path | None = None


class IndexUnavailableError(RuntimeError):
    """Raised when the search index database cannot be opened."""


def index_db_path() -> Path:
    """Absolute path of the index database inside the data directory."""
    return cfg.data_dir() / cfg.search_index_file()


def open_index() -> sqlite3.Connection:
    """
    Opens (creating if needed) the FTS5 index database.

    Applies the schema when missing and sets WAL journaling for concurrent
    reader friendliness. The returned connection is created with
    check_same_thread=False; callers must serialize access (this module's
    public helpers do so via _LOCK).
    """
    db_path = index_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    return conn


def get_index() -> sqlite3.Connection:
    """
    Returns the shared index connection, opening it lazily.

    Reopens automatically if the configured data_dir changed (tests, config
    reload). Raises IndexUnavailableError when the DB cannot be opened.
    """
    global _conn, _conn_db_path
    with _LOCK:
        db_path = index_db_path()
        if _conn is not None and _conn_db_path == db_path:
            return _conn
        if _conn is not None:
            try:
                _conn.close()
            except Exception as e:
                # Best-effort close before reopening against the new path.
                logger.debug("Error closing stale index connection: %s", e)
            _conn = None
        try:
            _conn = open_index()
            _conn_db_path = db_path
        except Exception as e:
            logger.warning(f"Search index unavailable ({db_path}): {e}", exc_info=True)
            raise IndexUnavailableError(str(e)) from e
        return _conn


def close_index() -> None:
    """Closes the shared connection (cooperative shutdown / tests)."""
    global _conn, _conn_db_path
    with _LOCK:
        if _conn is not None:
            try:
                _conn.close()
            except Exception as e:
                # Best-effort shutdown cleanup; the process is exiting anyway.
                logger.debug("Error closing index connection: %s", e)
        _conn = None
        _conn_db_path = None


# --- Source collection -------------------------------------------------------


def _collect_pages(pdf_rel_path: str) -> tuple[dict[int, str], float]:
    """
    Reads all transcription text for one magazine and the newest source mtime.

    Mirrors the source priority used by the viewer (metadata service):
    loose _COMPLETE.txt master > loose _pNNN.txt page files > partner ZIP
    (master member, then page members).

    Returns:
        tuple: ({page_number: text}, most_recent_source_mtime)
    """
    import re
    import zipfile

    data_dir = cfg.data_dir()
    pdf_path = data_dir / pdf_rel_path
    pages: dict[int, str] = {}
    mtimes: list[float] = []

    master_file = pdf_path.parent / f"{pdf_path.stem}_COMPLETE.txt"
    master_exists = master_file.exists()

    if master_exists:
        mtimes.append(master_file.stat().st_mtime)
        pages.update(split_pages(master_file.read_text(encoding="utf-8", errors="ignore")))
    else:
        pattern = re.compile(rf"^{re.escape(pdf_path.stem)}_p(\d+)\.txt$", re.IGNORECASE)
        for txt in pdf_path.parent.glob(f"{pdf_path.stem}_p*.txt"):
            m = pattern.search(txt.name)
            if m:
                mtimes.append(txt.stat().st_mtime)
                pages[int(m.group(1))] = txt.read_text(encoding="utf-8", errors="ignore")

    partner_zip = metadata.get_partner_zip(pdf_rel_path)
    if partner_zip and not master_exists:
        try:
            mtimes.append(partner_zip.stat().st_mtime)
            with zipfile.ZipFile(partner_zip, "r") as z:
                names = z.namelist()
                master_zname = zip_utils.find_member_by_basename(z, f"{pdf_path.stem}_COMPLETE.txt")
                if master_zname:
                    zip_pages = split_pages(z.read(master_zname).decode("utf-8", errors="ignore"))
                    for p_num, p_text in zip_pages.items():
                        pages.setdefault(p_num, p_text)  # loose page files win
                else:
                    pattern = re.compile(
                        rf"^{re.escape(pdf_path.stem)}_p(\d+)\.txt$", re.IGNORECASE
                    )
                    for zname in names:
                        m = pattern.search(zname.split("/")[-1])
                        if m:
                            pages.setdefault(
                                int(m.group(1)), z.read(zname).decode("utf-8", errors="ignore")
                            )
        except Exception as e:
            logger.warning(f"Could not read partner ZIP for {pdf_rel_path}: {e}")

    return pages, max(mtimes, default=0.0)


# --- Index maintenance -------------------------------------------------------


def index_magazine(conn: sqlite3.Connection, pdf_rel_path: str) -> None:
    """
    (Re)indexes a single magazine: replaces all of its page rows and updates
    its index_meta stamp. Participates in an enclosing transaction when one
    is already open (rebuild_all); otherwise commits on its own.
    """
    with _LOCK:
        pages, src_mtime = _collect_pages(pdf_rel_path)
        own_txn = not conn.in_transaction
        if own_txn:
            conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM pages WHERE pdf_path = ?", (pdf_rel_path,))
            conn.executemany(
                "INSERT INTO pages (pdf_path, page, text) VALUES (?, ?, ?)",
                [(pdf_rel_path, p_num, text) for p_num, text in sorted(pages.items())],
            )
            conn.execute(
                "INSERT INTO index_meta (pdf_path, mtime, indexed_at) VALUES (?, ?, ?) "
                "ON CONFLICT(pdf_path) DO UPDATE SET mtime = excluded.mtime, indexed_at = excluded.indexed_at",
                (pdf_rel_path, src_mtime, time.time()),
            )
            if own_txn:
                conn.execute("COMMIT")
        except Exception:
            if own_txn and conn.in_transaction:
                conn.execute("ROLLBACK")
            raise


def remove_magazine(conn: sqlite3.Connection, pdf_rel_path: str) -> None:
    """Removes all index rows for a magazine (called from /api/uninstall)."""
    with _LOCK, state.write_in_progress():
        own_txn = not conn.in_transaction
        if own_txn:
            conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM pages WHERE pdf_path = ?", (pdf_rel_path,))
            conn.execute("DELETE FROM index_meta WHERE pdf_path = ?", (pdf_rel_path,))
            if own_txn:
                conn.execute("COMMIT")
        except Exception:
            if own_txn and conn.in_transaction:
                conn.execute("ROLLBACK")
            raise


def rebuild_all(conn: sqlite3.Connection) -> None:
    """
    Full rebuild inside a single transaction: drops every row and reindexes
    each magazine currently present in METADATA_CACHE.
    """
    with _LOCK, state.write_in_progress():
        started = time.time()
        mags = list(state.METADATA_CACHE.keys())
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM pages")
            conn.execute("DELETE FROM index_meta")
            for rel_path in mags:
                index_magazine(conn, rel_path)
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        elapsed = time.time() - started
        if elapsed > 1.0:
            logger.info(f"Search index full rebuild: {len(mags)} magazines in {elapsed:.1f}s")


def refresh_stale(conn: sqlite3.Connection) -> None:
    """
    Incremental refresh: reindexes magazines whose source-file mtime differs
    from the stamp in index_meta, indexes new magazines, and drops entries
    for magazines that no longer exist. Falls back to a full rebuild when
    the index is empty.
    """
    with _LOCK:
        mags = list(state.METADATA_CACHE.keys())
        stamps = dict(conn.execute("SELECT pdf_path, mtime FROM index_meta").fetchall())

        if not stamps and mags:
            rebuild_all(conn)
            return

        started = time.time()
        changed = 0
        with state.write_in_progress():
            for rel_path in mags:
                _, src_mtime = _collect_pages_mtime_only(rel_path)
                if rel_path not in stamps or stamps[rel_path] != src_mtime:
                    index_magazine(conn, rel_path)
                    changed += 1
            for stale_path in set(stamps) - set(mags):
                remove_magazine(conn, stale_path)
                changed += 1
        elapsed = time.time() - started
        if elapsed > 1.0:
            logger.info(f"Search index refresh: {changed} magazine(s) reindexed in {elapsed:.1f}s")


def _collect_pages_mtime_only(pdf_rel_path: str) -> tuple[None, float]:
    """Computes the newest source mtime without reading file contents."""
    import re

    data_dir = cfg.data_dir()
    pdf_path = data_dir / pdf_rel_path
    mtimes: list[float] = []

    master_file = pdf_path.parent / f"{pdf_path.stem}_COMPLETE.txt"
    if master_file.exists():
        mtimes.append(master_file.stat().st_mtime)
    else:
        pattern = re.compile(rf"^{re.escape(pdf_path.stem)}_p(\d+)\.txt$", re.IGNORECASE)
        for txt in pdf_path.parent.glob(f"{pdf_path.stem}_p*.txt"):
            if pattern.search(txt.name):
                mtimes.append(txt.stat().st_mtime)
        partner_zip = metadata.get_partner_zip(pdf_rel_path)
        if partner_zip:
            mtimes.append(partner_zip.stat().st_mtime)

    return None, max(mtimes, default=0.0)


# --- Convenience wrappers (used by routes / workers) --------------------------


def init_index() -> None:
    """
    Startup initialization: opens the index and brings it up to date.

    Honors search.rebuild_on_startup (full rebuild) and otherwise performs an
    incremental refresh — which itself full-rebuilds an empty index, covering
    first launch on an existing library. Never raises: on failure the index
    is left unavailable and /api/search returns 503.
    """
    try:
        conn = get_index()
        started = time.time()
        if cfg.search_rebuild_on_startup():
            rebuild_all(conn)
        else:
            refresh_stale(conn)
        elapsed = time.time() - started
        if elapsed > 1.0:
            logger.info(f"Search index ready in {elapsed:.1f}s ({index_db_path()})")
    except Exception as e:
        logger.warning(f"Search index initialization failed: {e}", exc_info=True)


def index_magazine_path(pdf_rel_path: str) -> None:
    """Best-effort single-magazine reindex after a save or download."""
    try:
        with state.write_in_progress():
            index_magazine(get_index(), pdf_rel_path)
    except Exception as e:
        logger.warning(f"Could not reindex {pdf_rel_path}: {e}")


def remove_magazine_path(pdf_rel_path: str) -> None:
    """Best-effort index removal after an uninstall."""
    try:
        remove_magazine(get_index(), pdf_rel_path)
    except Exception as e:
        logger.warning(f"Could not remove {pdf_rel_path} from search index: {e}")
