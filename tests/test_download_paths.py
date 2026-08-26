"""Tests that malicious catalog entries can never write outside data_dir."""

from pathlib import Path

from app.services import download, state


def _files_outside(root: Path) -> list[Path]:
    """All files in root's parent that are NOT inside root."""
    parent = root.parent
    return [
        p for p in parent.rglob("*")
        if p.is_file() and not p.resolve().is_relative_to(root.resolve())
    ]


def test_traversal_filename_is_neutralized(workspace, malicious_catalog_item):
    """'../../evil.pdf' must reduce to basename; no mirrors -> clean error;
    nothing is ever written outside the Magazines directory."""
    data_dir = workspace / "Magazines"
    before = set(_files_outside(data_dir))

    download.download_worker("task_evil", malicious_catalog_item)

    task = state.DOWNLOAD_STATE["task_evil"]
    assert task["error"]  # no sources -> mirrors failed (after sanitization)
    after = set(_files_outside(data_dir))
    assert after == before, f"files escaped data_dir: {after - before}"
    # And specifically the traversal target does not exist
    assert not (workspace.parent / "evil.pdf").exists()
    assert not (workspace / "evil.pdf").exists()


def test_dotdot_filename_rejected(workspace, sample_catalog_item):
    item = dict(sample_catalog_item, pdf_filename="..")
    download.download_worker("task_dots", item)
    task = state.DOWNLOAD_STATE["task_dots"]
    assert task["done"] is True
    assert "unsafe" in (task["error"] or "").lower()


def test_malicious_folder_names_stay_inside_data_dir(
    workspace, malicious_catalog_item, monkeypatch
):
    """Even when downloads 'succeed', traversal in magazine_name/date/
    issue_name must not place the install outside Magazines/."""
    data_dir = workspace / "Magazines"

    def fake_waterfall(task_id, out_path, sources, file_type):
        out_path.write_bytes(b"fake content")
        return True

    monkeypatch.setattr(download, "download_waterfall", fake_waterfall)
    before = set(_files_outside(data_dir))

    download.download_worker("task_folders", malicious_catalog_item)

    after = set(_files_outside(data_dir))
    assert after == before, f"files escaped data_dir: {after - before}"
    # Whatever was installed must live inside data_dir
    installed = [p for p in data_dir.rglob("*") if p.is_file()]
    for p in installed:
        assert p.resolve().is_relative_to(data_dir.resolve())


def test_happy_path_install_layout(workspace, sample_catalog_item, monkeypatch):
    """A clean catalog entry installs into Magazines/<name>/<date - issue>/."""
    data_dir = workspace / "Magazines"

    def fake_waterfall(task_id, out_path, sources, file_type):
        out_path.write_bytes(b"fake content")
        return True

    monkeypatch.setattr(download, "download_waterfall", fake_waterfall)
    download.download_worker("task_ok", sample_catalog_item)

    task = state.DOWNLOAD_STATE["task_ok"]
    assert task["done"] is True
    assert not task["error"]
    expected = data_dir / "Super Mario Magazine" / "1992-10 - Vol 1" / "SMM_1992_10.pdf"
    assert expected.exists()
