"""API tests: validation (400 vs 500), uninstall matching, token + Host checks."""

import json

from app.services import state


# --- Query/body validation ---------------------------------------------------

def test_render_non_numeric_page_is_400(client):
    res = client.get("/api/render?mag=Some/mag.pdf&page=abc")
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_render_non_numeric_zoom_is_400(client):
    res = client.get("/api/render?mag=Some/mag.pdf&page=1&zoom=abc")
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_render_traversal_mag_is_400(client):
    res = client.get("/api/render?mag=../../etc/passwd&page=0")
    assert res.status_code == 400


def test_text_non_numeric_page_is_400(client):
    res = client.get("/api/text?mag=Some/mag.pdf&page=abc")
    assert res.status_code == 400


def test_text_missing_mag_is_400(client):
    res = client.get("/api/text?page=1")
    assert res.status_code == 400


def test_save_missing_content_keys_is_400(client, token_headers):
    res = client.post(
        "/api/save",
        json={"mag": "Some/mag.pdf", "page": 1},
        headers=token_headers,
    )
    assert res.status_code == 400
    detail = res.get_json().get("detail", "")
    for key in ("jp", "en", "sum"):
        assert key in detail


def test_save_non_numeric_page_is_400(client, token_headers):
    res = client.post(
        "/api/save",
        json={"mag": "Some/mag.pdf", "page": "abc", "jp": "", "en": "", "sum": ""},
        headers=token_headers,
    )
    assert res.status_code == 400


def test_bookmarks_post_missing_keys_is_400(client, token_headers):
    res = client.post("/api/bookmarks", json={"tags": "x"}, headers=token_headers)
    assert res.status_code == 400


def test_download_missing_id_is_400(client, token_headers):
    res = client.post("/api/download", json={}, headers=token_headers)
    assert res.status_code == 400


def test_uninstall_missing_filename_is_400(client, token_headers):
    res = client.post("/api/uninstall", json={}, headers=token_headers)
    assert res.status_code == 400


# --- Uninstall exact-basename matching (C1) ----------------------------------

def test_uninstall_suffix_match_does_not_delete_other_file(
    client, token_headers, workspace
):
    """'game.pdf' must NOT match (or delete) 'Endgame.pdf'."""
    data_dir = workspace / "Magazines"
    mag_dir = data_dir / "EndgameMag"
    mag_dir.mkdir()
    endgame = mag_dir / "Endgame.pdf"
    endgame.write_bytes(b"pdf content")
    state.METADATA_CACHE = {"EndgameMag/Endgame.pdf": {"name": "Endgame"}}

    res = client.post(
        "/api/uninstall", json={"pdf_filename": "game.pdf"}, headers=token_headers
    )
    assert res.status_code == 404
    assert endgame.exists(), "Endgame.pdf was wrongly deleted by a suffix match"


def test_uninstall_exact_match_deletes(client, token_headers, workspace):
    data_dir = workspace / "Magazines"
    mag_dir = data_dir / "EndgameMag"
    mag_dir.mkdir()
    endgame = mag_dir / "Endgame.pdf"
    endgame.write_bytes(b"pdf content")
    state.METADATA_CACHE = {"EndgameMag/Endgame.pdf": {"name": "Endgame"}}

    res = client.post(
        "/api/uninstall", json={"pdf_filename": "Endgame.pdf"}, headers=token_headers
    )
    assert res.status_code == 200
    assert not endgame.exists()


# --- Session token + Host checks (step 5) -------------------------------------

def test_non_get_without_token_is_403(client):
    res = client.post("/api/download", json={"id": "x"})
    assert res.status_code == 403


def test_non_get_with_wrong_token_is_403(client):
    res = client.post(
        "/api/download", json={"id": "x"}, headers={"X-Rosetta-Token": "wrong"}
    )
    assert res.status_code == 403


def test_non_get_with_token_passes_security(client, token_headers):
    # 400/404 (validation) proves it got past the 403 security gate
    res = client.post("/api/download", json={}, headers=token_headers)
    assert res.status_code != 403


def test_get_requests_do_not_require_token(client):
    res = client.get("/api/downloads")
    assert res.status_code == 200


def test_evil_host_header_is_403(client, token_headers):
    res = client.post(
        "/api/download",
        json={"id": "x"},
        headers={**token_headers, "Host": "evil.com"},
    )
    assert res.status_code == 403


def test_ping_post_without_token_is_allowed(client):
    """sendBeacon can't set headers; /api/ping POST must skip the token check."""
    res = client.post("/api/ping")
    assert res.status_code == 200


def test_index_injects_csrf_meta_tag(client, token):
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert f'<meta name="csrf-token" content="{token}">' in html


# --- Bookmarks happy path (behavior preserved) --------------------------------

def test_bookmarks_roundtrip(client, token_headers, workspace):
    res = client.post(
        "/api/bookmarks",
        json={"mag": "Mag/a.pdf", "page": "3", "tags": "boss"},
        headers=token_headers,
    )
    assert res.status_code == 200
    assert "Mag/a.pdf_3" in res.get_json()

    res = client.get("/api/bookmarks")
    assert "Mag/a.pdf_3" in res.get_json()

    res = client.delete("/api/bookmarks?key=Mag/a.pdf_3", headers=token_headers)
    assert res.status_code == 200
    assert "Mag/a.pdf_3" not in res.get_json()

    # File on disk is valid JSON (atomic write)
    on_disk = json.loads((workspace / "bookmarks.json").read_text(encoding="utf-8"))
    assert on_disk == {}


# --- FTS5 search endpoint + index integration ---------------------------------

def _install_searchable_mag(workspace):
    from app.services import metadata, search_index

    data_dir = workspace / "Magazines"
    mag_dir = data_dir / "SearchMag"
    mag_dir.mkdir(parents=True, exist_ok=True)
    (mag_dir / "Issue.pdf").write_bytes(b"%PDF-1.4 fake")
    (mag_dir / "Issue_COMPLETE.txt").write_text(
        "[[PAGE_001]]\nLegendary shmup interview\n#GA-TRANSLATION\nEnglish text here",
        encoding="utf-8",
    )
    metadata.load_metadata_cache()
    search_index.init_index()
    return mag_dir


def test_search_endpoint_returns_results_shape(client, workspace):
    _install_searchable_mag(workspace)
    res = client.get("/api/search?q=shmup&scope=global&incJp=true&incEn=true&incSum=true")
    assert res.status_code == 200
    data = res.get_json()
    assert set(data) == {"results", "terms_to_highlight"}
    assert data["terms_to_highlight"] == ["shmup"]
    assert len(data["results"]) == 1
    hit = data["results"][0]
    assert hit["mag"] == "SearchMag/Issue.pdf"
    assert hit["page"] == 1
    assert "<mark>shmup</mark>" in hit["snippet"]


def test_search_endpoint_no_match_is_empty_200(client, workspace):
    _install_searchable_mag(workspace)
    res = client.get("/api/search?q=nonexistentword&scope=global&incJp=true&incEn=true&incSum=true")
    assert res.status_code == 200
    assert res.get_json()["results"] == []


def test_search_endpoint_hostile_query_is_200(client, workspace):
    _install_searchable_mag(workspace)
    res = client.get(
        "/api/search?q=%22%20OR%201%3D1%20--&scope=global&incJp=true&incEn=true&incSum=true"
    )
    assert res.status_code == 200


def test_uninstall_removes_from_search_index(client, token_headers, workspace):
    from app.services import search_index

    _install_searchable_mag(workspace)
    conn = search_index.get_index()
    assert conn.execute("SELECT count(*) FROM pages WHERE pdf_path=?",
                        ("SearchMag/Issue.pdf",)).fetchone()[0] == 1

    res = client.post(
        "/api/uninstall", json={"pdf_filename": "Issue.pdf"}, headers=token_headers
    )
    assert res.status_code == 200
    conn = search_index.get_index()
    assert conn.execute("SELECT count(*) FROM pages WHERE pdf_path=?",
                        ("SearchMag/Issue.pdf",)).fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM index_meta WHERE pdf_path=?",
                        ("SearchMag/Issue.pdf",)).fetchone()[0] == 0


def test_save_reindexes_magazine(client, token_headers, workspace):
    from app.services import search_index

    _install_searchable_mag(workspace)
    res = client.post(
        "/api/save",
        json={"mag": "SearchMag/Issue.pdf", "page": 1,
              "jp": "Fresh gradius strategy", "en": "translated", "sum": ""},
        headers=token_headers,
    )
    assert res.status_code == 200
    res = client.get("/api/search?q=gradius&scope=global&incJp=true&incEn=true&incSum=true")
    hits = res.get_json()["results"]
    assert len(hits) == 1 and hits[0]["page"] == 1
