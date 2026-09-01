"""Tests for virtual-PDF text-only magazine installs."""

import zipfile
from pathlib import Path

from app.services import download, metadata, search_index, state


def make_data_zip(directory: Path, name: str, members: dict[str, str]) -> Path:
    path = directory / name
    with zipfile.ZipFile(path, "w") as archive:
        for member, content in members.items():
            archive.writestr(member, content)
    return path


def test_partner_zip_resolves_without_pdf(workspace):
    data = workspace / "Magazines" / "Folder"
    data.mkdir()
    for name in ("Issue.zip", "Issue_Data.zip"):
        path = data / name
        path.write_bytes(b"zip")
        assert metadata.get_partner_zip(f"Folder/{name.removesuffix('.zip')}.pdf") == path
        path.unlink()


def test_cache_discovers_zip_only_install(workspace):
    data = workspace / "Magazines" / "Folder"
    data.mkdir()
    make_data_zip(data, "data.zip", {"metadata.txt": "Pdf Filename: Foo.pdf\nMagazine Name: Test"})
    metadata.load_metadata_cache()
    assert state.METADATA_CACHE["Folder/Foo.pdf"] == {
        "pdf_filename": "Foo.pdf",
        "name": "Test",
        "text_only": "true",
    }


def test_cache_virtual_name_fallback_heuristic(workspace):
    data = workspace / "Magazines" / "Folder"
    data.mkdir()
    make_data_zip(data, "Bar_Data.zip", {"metadata.txt": "Magazine Name: Bar"})
    metadata.load_metadata_cache()
    assert "Folder/Bar.pdf" in state.METADATA_CACHE


def test_cache_partner_zip_not_double_registered(workspace):
    data = workspace / "Magazines" / "Folder"
    data.mkdir()
    (data / "Issue.pdf").write_bytes(b"pdf")
    make_data_zip(data, "Issue_Data.zip", {"metadata.txt": "Magazine Name: Issue"})
    metadata.load_metadata_cache()
    assert list(state.METADATA_CACHE) == ["Folder/Issue.pdf"]
    assert "text_only" not in state.METADATA_CACHE["Folder/Issue.pdf"]


def test_cache_unsafe_pdf_filename_falls_back(workspace):
    data = workspace / "Magazines" / "Folder"
    data.mkdir()
    make_data_zip(data, "Bar_Data.zip", {"metadata.txt": "Pdf Filename: ../../evil.pdf"})
    metadata.load_metadata_cache()
    assert "Folder/Bar.pdf" in state.METADATA_CACHE
    assert "evil.pdf" not in state.METADATA_CACHE


def test_get_text_page_count(workspace):
    data = workspace / "Magazines" / "Folder"
    data.mkdir()
    make_data_zip(
        data,
        "Pages_Data.zip",
        {"Pages_p001.txt": "one", "Pages_p003.txt": "three", "metadata.txt": ""},
    )
    assert metadata.get_text_page_count("Folder/Pages.pdf") == 3
    make_data_zip(
        data,
        "Master_Data.zip",
        {"Master_COMPLETE.txt": "[[PAGE_002]] two\n[[PAGE_004]] four"},
    )
    assert metadata.get_text_page_count("Folder/Master.pdf") == 4
    (data / "Loose_COMPLETE.txt").write_text("[[PAGE_005]] five", encoding="utf-8")
    assert metadata.get_text_page_count("Folder/Loose.pdf") == 5
    assert metadata.get_text_page_count("Folder/None.pdf") == 0


def _item():
    return {
        "id": "text",
        "magazine_name": "Mag",
        "date": "2020",
        "issue_name": "One",
        "pdf_filename": "Virtual.pdf",
        "zip_filename": "Virtual_Data.zip",
        "pdf_sources": ["pdf"],
        "zip_sources": ["zip"],
    }


def test_worker_text_mode_skips_pdf_waterfall(workspace, monkeypatch):
    calls = []

    def waterfall(task, path, sources, file_type):
        calls.append(file_type)
        if file_type == "Data ZIP":
            make_data_zip(path.parent, path.name, {"metadata.txt": "Pdf Filename: Virtual.pdf"})
        return True

    monkeypatch.setattr(download, "download_waterfall", waterfall)
    download.download_worker("text", _item(), mode="text")
    final = workspace / "Magazines" / "Mag" / "2020 - One"
    assert calls == ["Data ZIP"]
    assert (final / "Virtual_Data.zip").exists()
    assert not (final / "Virtual.pdf").exists()
    with zipfile.ZipFile(final / "Virtual_Data.zip") as z:
        assert "Pdf Filename: Virtual.pdf" in z.read("metadata.txt").decode()


def test_worker_text_mode_requires_zip_sources(workspace):
    download.download_worker("missing", dict(_item(), zip_sources=[]), mode="text")
    assert state.DOWNLOAD_STATE["missing"]["done"]
    assert "no transcription" in state.DOWNLOAD_STATE["missing"]["error"].lower()
    assert not list((workspace / "Magazines").rglob("*.zip"))


def test_worker_text_mode_indexes_virtual_path(workspace, monkeypatch):
    indexed = []
    monkeypatch.setattr(search_index, "index_magazine_path", indexed.append)

    def waterfall(task, path, sources, file_type):
        make_data_zip(path.parent, path.name, {"metadata.txt": ""})
        return True

    monkeypatch.setattr(download, "download_waterfall", waterfall)
    download.download_worker("indexed", _item(), mode="text")
    assert indexed == ["Mag/2020 - One/Virtual.pdf"]


def test_full_over_text_only_downloads_pdf(workspace, monkeypatch):
    data = workspace / "Magazines" / "Mag"
    data.mkdir(parents=True)
    make_data_zip(data, "Virtual_Data.zip", {"metadata.txt": "Pdf Filename: Virtual.pdf"})
    metadata.load_metadata_cache()
    calls = []

    def waterfall(task, path, sources, file_type):
        calls.append(file_type)
        if file_type == "PDF":
            path.write_bytes(b"pdf")
        else:
            make_data_zip(path.parent, path.name, {"metadata.txt": "Pdf Filename: Virtual.pdf"})
        return True

    monkeypatch.setattr(download, "download_waterfall", waterfall)
    download.download_worker("upgrade", _item())
    assert "PDF" in calls
    assert (workspace / "Magazines" / "Mag" / "2020 - One" / "Virtual.pdf").exists()


def test_api_download_mode_validation(client, token_headers, monkeypatch):
    monkeypatch.setattr(
        "app.routes.api_write.catalog.get_all_catalogs",
        lambda force_refresh=False: [{"id": "x"}],
    )
    response = client.post(
        "/api/download", json={"id": "x", "mode": "bogus"}, headers=token_headers
    )
    assert response.status_code == 400


def test_api_download_text_mode_starts_worker(client, token_headers, monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, **kwargs):
            started.append(kwargs["args"])

        def start(self):
            return None

    monkeypatch.setattr(
        "app.routes.api_write.catalog.get_all_catalogs",
        lambda force_refresh=False: [{"id": "x"}],
    )
    monkeypatch.setattr("app.routes.api_write.threading.Thread", FakeThread)
    response = client.post("/api/download", json={"id": "x", "mode": "text"}, headers=token_headers)
    assert response.status_code == 200
    assert started[0][2] == "text"


def test_api_list_includes_text_only(client, workspace):
    data = workspace / "Magazines" / "Folder"
    data.mkdir()
    make_data_zip(
        data, "Data.zip", {"metadata.txt": "Pdf Filename: Listed.pdf\nMagazine Name: Listed"}
    )
    response = client.get("/api/list")
    assert response.status_code == 200
    assert response.get_json()["files"] == ["Folder/Listed.pdf"]
    assert response.get_json()["metadata"]["Folder/Listed.pdf"]["text_only"] == "true"


def test_api_text_total_pages_fallback(client, workspace):
    data = workspace / "Magazines" / "Folder"
    data.mkdir()
    make_data_zip(
        data,
        "Data.zip",
        {
            "metadata.txt": "Pdf Filename: Text.pdf",
            "Text_COMPLETE.txt": "[[PAGE_001]]\n#GA-TRANSCRIPTION\none\n[[PAGE_003]]\nthree",
        },
    )
    response = client.get("/api/text?mag=Folder/Text.pdf&page=3")
    assert response.status_code == 200
    assert response.get_json()["total_pages"] == 3
    assert "three" in response.get_json()["jp"]


def test_api_render_missing_pdf_404(client, workspace):
    data = workspace / "Magazines" / "Folder"
    data.mkdir()
    make_data_zip(data, "Data.zip", {"metadata.txt": "Pdf Filename: Render.pdf"})
    response = client.get("/api/render?mag=Folder/Render.pdf&page=0")
    assert response.status_code == 404
    assert response.get_json()["error"] == "no_pdf"


def test_api_uninstall_text_only(client, token_headers, workspace):
    data = workspace / "Magazines" / "Folder"
    data.mkdir()
    archive = make_data_zip(data, "Data.zip", {"metadata.txt": "Pdf Filename: Remove.pdf"})
    metadata.load_metadata_cache()
    response = client.post(
        "/api/uninstall", json={"pdf_filename": "Remove.pdf"}, headers=token_headers
    )
    assert response.status_code == 200
    assert not archive.exists()
    assert "Folder/Remove.pdf" not in state.METADATA_CACHE


def test_search_finds_text_only_install(client, workspace):
    data = workspace / "Magazines" / "Folder"
    data.mkdir()
    make_data_zip(
        data,
        "Data.zip",
        {
            "metadata.txt": "Pdf Filename: Search.pdf",
            "Search_COMPLETE.txt": "[[PAGE_001]] unique-token",
        },
    )
    metadata.load_metadata_cache()
    search_index.init_index()
    response = client.get("/api/search?q=unique-token&incJp=true")
    assert response.status_code == 200
    assert any(
        result.get("mag") == "Folder/Search.pdf" for result in response.get_json()["results"]
    )
