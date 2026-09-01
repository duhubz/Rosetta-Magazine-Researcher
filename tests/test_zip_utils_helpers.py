"""Unit tests for zip_utils.find_member_by_basename and update_zip_contents."""

import zipfile

import pytest

from app.services import zip_utils

# --- find_member_by_basename ---------------------------------------------------


def _make_zip(path, members):
    with zipfile.ZipFile(path, "w") as z:
        for name, content in members.items():
            z.writestr(name, content)
    return path


def test_find_member_case_insensitive(tmp_path):
    zp = _make_zip(tmp_path / "a.zip", {"Data/METADATA.TXT": "x"})
    with zipfile.ZipFile(zp) as z:
        assert zip_utils.find_member_by_basename(z, "metadata.txt") == "Data/METADATA.TXT"


def test_find_member_no_match_returns_none(tmp_path):
    zp = _make_zip(tmp_path / "a.zip", {"metadata.txt": "x"})
    with zipfile.ZipFile(zp) as z:
        assert zip_utils.find_member_by_basename(z, "other.txt") is None


def test_find_member_in_subdirectory(tmp_path):
    zp = _make_zip(tmp_path / "a.zip", {"deep/nested/dir/page_001.txt": "x"})
    with zipfile.ZipFile(zp) as z:
        found = zip_utils.find_member_by_basename(z, "Page_001.txt")
        assert found == "deep/nested/dir/page_001.txt"


def test_find_member_exact_basename_only(tmp_path):
    """'game.pdf' must NOT match 'Endgame.pdf' (no suffix matching)."""
    zp = _make_zip(tmp_path / "a.zip", {"Mag/Endgame.pdf": "x"})
    with zipfile.ZipFile(zp) as z:
        assert zip_utils.find_member_by_basename(z, "game.pdf") is None
        assert zip_utils.find_member_by_basename(z, "endgame.pdf") == "Mag/Endgame.pdf"


def test_find_member_does_not_match_directory_components(tmp_path):
    """A directory named like the target must not match — basenames only."""
    zp = _make_zip(tmp_path / "a.zip", {"metadata.txt.d/other.bin": "x"})
    with zipfile.ZipFile(zp) as z:
        assert zip_utils.find_member_by_basename(z, "metadata.txt") is None


# --- update_zip_contents --------------------------------------------------------


def test_update_zip_contents_multi_file_single_call(tmp_path):
    zp = _make_zip(
        tmp_path / "a.zip",
        {"Data/master.txt": "old master", "Data/metadata.txt": "old meta", "keep.txt": "keep me"},
    )
    zip_utils.update_zip_contents(
        zp, {"master.txt": "new master", "metadata.txt": "new meta", "added.json": "[1]"}
    )
    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
        # Existing members updated in place (original paths preserved)
        assert z.read("Data/master.txt").decode() == "new master"
        assert z.read("Data/metadata.txt").decode() == "new meta"
        # Untouched member copied through
        assert z.read("keep.txt").decode() == "keep me"
        # Missing member appended under the given name
        assert z.read("added.json").decode() == "[1]"
        assert len(names) == 4


def test_update_zip_contents_preserves_untouched_members(tmp_path):
    members = {f"file_{i}.txt": f"content {i}" for i in range(5)}
    zp = _make_zip(tmp_path / "a.zip", members)
    zip_utils.update_zip_contents(zp, {"file_2.txt": "changed"})
    with zipfile.ZipFile(zp) as z:
        for name, content in members.items():
            expected = "changed" if name == "file_2.txt" else content
            assert z.read(name).decode() == expected


def test_update_zip_contents_atomic_no_temp_left_and_original_intact(tmp_path):
    """On failure, the original ZIP is untouched and no temp files remain."""
    zp = _make_zip(tmp_path / "a.zip", {"a.txt": "original"})

    # An int is not writable content: zipfile raises TypeError mid-rewrite.
    with pytest.raises(TypeError):
        zip_utils.update_zip_contents(zp, {"a.txt": 12345})  # type: ignore[dict-item]

    # Original archive intact
    with zipfile.ZipFile(zp) as z:
        assert z.read("a.txt").decode() == "original"
    # No stray temp files in the directory
    assert [p.name for p in tmp_path.iterdir()] == ["a.zip"]


def test_update_zip_contents_accepts_bytes(tmp_path):
    zp = _make_zip(tmp_path / "a.zip", {"a.bin": b"old"})
    zip_utils.update_zip_contents(zp, {"a.bin": b"\x00\x01\x02"})
    with zipfile.ZipFile(zp) as z:
        assert z.read("a.bin") == b"\x00\x01\x02"


def test_update_zip_contents_empty_dict_is_noop(tmp_path):
    zp = _make_zip(tmp_path / "a.zip", {"a.txt": "x"})
    before = zp.read_bytes()
    zip_utils.update_zip_contents(zp, {})
    assert zp.read_bytes() == before
