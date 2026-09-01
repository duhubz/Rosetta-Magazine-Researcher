"""FTS5 search index tests: indexing, incremental refresh, removal,
diacritics folding, and hostile query strings."""

import os

import pytest

import app.config as cfg
from app.services import metadata, search, search_index, state


def _make_magazine(data_dir, folder, stem, pages):
    """Creates a fake PDF + _COMPLETE.txt master with the given page texts."""
    mag_dir = data_dir / folder
    mag_dir.mkdir(parents=True, exist_ok=True)
    (mag_dir / f"{stem}.pdf").write_bytes(b"%PDF-1.4 fake")
    master = "\n\n".join(f"[[PAGE_{str(p).zfill(3)}]]\n{text}" for p, text in sorted(pages.items()))
    (mag_dir / f"{stem}_COMPLETE.txt").write_text(master, encoding="utf-8")
    return mag_dir


@pytest.fixture
def library(workspace):
    """A small two-magazine library, metadata cache loaded, index built."""
    data_dir = cfg.data_dir()
    _make_magazine(
        data_dir,
        "TestMag/1992-10 - Vol 1",
        "Issue1",
        {
            1: "Welcome to the café résumé edition\n#GA-TRANSLATION\nHello world translation\n#GA-SUMMARY\nA short summary",
            2: "Second page about Mario games and consoles",
        },
    )
    _make_magazine(
        data_dir,
        "OtherMag",
        "Other1",
        {
            1: "Completely different content about Zelda",
        },
    )
    metadata.load_metadata_cache()
    conn = search_index.get_index()
    search_index.rebuild_all(conn)
    return data_dir


def _search(q, **kw):
    args = dict(
        scope="global",
        inc_jp=True,
        inc_en=True,
        inc_sum=True,
        current_mag="",
        mag_filter="",
        date_start="",
        date_end="",
        tag_filter="",
    )
    args.update(kw)
    return search.search(q, **args)


# --- Indexing ------------------------------------------------------------------


def test_rebuild_indexes_all_pages(library):
    conn = search_index.get_index()
    assert conn.execute("SELECT count(*) FROM pages").fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM index_meta").fetchone()[0] == 2


def test_search_finds_word_and_page(library):
    results, highlights = _search("mario")
    assert len(results) == 1
    assert results[0]["mag"] == "TestMag/1992-10 - Vol 1/Issue1.pdf"
    assert results[0]["page"] == 2
    assert "<mark>Mario</mark>" in results[0]["snippet"]
    assert highlights == ["mario"]


def test_search_exact_phrase(library):
    results, _ = _search('"hello world"')
    assert [r["page"] for r in results] == [1]
    results, _ = _search('"world hello"')
    assert results == []


def test_search_multiple_words_are_anded(library):
    results, _ = _search("mario consoles")
    assert len(results) == 1
    assert results[0]["page"] == 2
    results, _ = _search("mario zelda")
    assert results == []


def test_search_negation_and_or(library):
    results, _ = _search("mario OR zelda")
    assert {r["mag"].split("/")[0] for r in results} == {"TestMag", "OtherMag"}
    results, _ = _search("games -zelda")
    assert len(results) == 1 and results[0]["page"] == 2


def test_diacritics_insensitive_search(library):
    """unicode61 remove_diacritics 2: plain ASCII matches accented text."""
    results, _ = _search("cafe resume")
    assert len(results) == 1
    assert results[0]["page"] == 1


def test_refresh_stale_reindexes_on_mtime_change(library):
    data_dir = library
    master = data_dir / "TestMag/1992-10 - Vol 1/Issue1_COMPLETE.txt"
    assert _search("sonic")[0] == []

    master.write_text("[[PAGE_001]]\nNow all about Sonic instead", encoding="utf-8")
    os.utime(master, (os.path.getmtime(master) + 5, os.path.getmtime(master) + 5))
    search_index.refresh_stale(search_index.get_index())

    results, _ = _search("sonic")
    assert len(results) == 1 and results[0]["page"] == 1
    # Old pages for that magazine are gone (page 2 was removed by the edit).
    assert _search("mario")[0] == []
    # The untouched magazine is still indexed.
    assert len(_search("zelda")[0]) == 1


def test_refresh_stale_full_rebuilds_empty_index(library):
    conn = search_index.get_index()
    conn.execute("DELETE FROM pages")
    conn.execute("DELETE FROM index_meta")
    search_index.refresh_stale(conn)
    assert conn.execute("SELECT count(*) FROM pages").fetchone()[0] == 3


def test_refresh_stale_drops_vanished_magazines(library):
    state.METADATA_CACHE = {k: v for k, v in state.METADATA_CACHE.items() if "OtherMag" not in k}
    search_index.refresh_stale(search_index.get_index())
    assert _search("zelda")[0] == []
    assert len(_search("mario")[0]) == 1


def test_remove_magazine(library):
    conn = search_index.get_index()
    search_index.remove_magazine(conn, "TestMag/1992-10 - Vol 1/Issue1.pdf")
    assert _search("mario")[0] == []
    assert len(_search("zelda")[0]) == 1
    assert conn.execute("SELECT count(*) FROM index_meta").fetchone()[0] == 1


def test_index_magazine_after_save_style_update(library):
    """index_magazine on one path refreshes just that magazine (save flow)."""
    data_dir = library
    master = data_dir / "OtherMag/Other1_COMPLETE.txt"
    master.write_text("[[PAGE_001]]\nZelda plus brand-new Metroid coverage", encoding="utf-8")
    search_index.index_magazine_path("OtherMag/Other1.pdf")
    results, _ = _search("metroid")
    assert len(results) == 1 and results[0]["mag"] == "OtherMag/Other1.pdf"


# --- Robustness ------------------------------------------------------------------


@pytest.mark.parametrize(
    "evil",
    [
        '" OR 1=1 --',
        "'); DROP TABLE pages; --",
        'AND OR NOT ( ) " * ^',
        "NEAR(a b)",
        "\\'\"`{};",
        '-"only a negation"',
        "***",
        "",
        "   ",
    ],
)
def test_malicious_queries_do_not_blow_up(library, evil):
    results, highlights = _search(evil)
    assert isinstance(results, list)
    assert isinstance(highlights, list)
    # And the table survived any injection attempt.
    conn = search_index.get_index()
    assert conn.execute("SELECT count(*) FROM pages").fetchone()[0] == 3


def test_empty_library_no_crash(workspace):
    """No magazines yet: init + search must not crash."""
    metadata.load_metadata_cache()
    search_index.init_index()
    results, _ = _search("anything")
    assert results == []


def test_index_db_is_hidden_from_library_scans(library):
    """The dot-prefixed DB (and WAL siblings) never appear as magazines."""
    db = search_index.index_db_path()
    assert db.exists()
    assert db.name.startswith(".")
    (db.parent / ".temp_x").mkdir(exist_ok=True)
    (db.parent / ".temp_x" / "partial.pdf").write_bytes(b"%PDF")
    metadata.load_metadata_cache()
    assert all(".temp_x" not in k for k in state.METADATA_CACHE)


def test_section_toggles_restrict_matches(library):
    # 'translation' appears in the EN section of page 1
    results, _ = _search("hello", inc_jp=False, inc_en=True, inc_sum=False)
    assert len(results) == 1
    # 'welcome' is transcription-only; EN-only search must not return it
    results, _ = _search("welcome", inc_jp=False, inc_en=True, inc_sum=False)
    assert results == []
