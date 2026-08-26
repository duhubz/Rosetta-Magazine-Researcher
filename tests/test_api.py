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
