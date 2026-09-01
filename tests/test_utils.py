"""Tests for app.utils: get_safe_path, safe_name, atomic writes."""

import os

import pytest

from app.utils import atomic_write_bytes, atomic_write_text, get_safe_path, safe_name

# --- get_safe_path -----------------------------------------------------------


def test_get_safe_path_accepts_nested_relative(workspace):
    data_dir = workspace / "Magazines"
    (data_dir / "Mag").mkdir()
    p = get_safe_path("Mag/issue.pdf")
    assert p == (data_dir / "Mag" / "issue.pdf").resolve()


def test_get_safe_path_rejects_parent_traversal(workspace):
    with pytest.raises(ValueError):
        get_safe_path("../evil.pdf")


def test_get_safe_path_rejects_deep_traversal(workspace):
    with pytest.raises(ValueError):
        get_safe_path("Mag/../../../etc/passwd")


def test_get_safe_path_rejects_sibling_prefix_bypass(workspace):
    """'MagazinesEvil' starts with 'Magazines' — the old startswith() check
    let this sibling directory through; is_relative_to must reject it."""
    evil_sibling = workspace / "MagazinesEvil"
    evil_sibling.mkdir()
    (evil_sibling / "x.pdf").write_text("x")
    with pytest.raises(ValueError):
        get_safe_path("../MagazinesEvil/x.pdf")


def test_get_safe_path_rejects_absolute_path(workspace):
    with pytest.raises(ValueError):
        get_safe_path("/etc/passwd")


# --- safe_name ---------------------------------------------------------------


def test_safe_name_passthrough():
    assert safe_name("issue_01.pdf") == "issue_01.pdf"


def test_safe_name_strips_directories():
    assert safe_name("foo/bar") == "bar"


def test_safe_name_traversal_reduced_to_basename():
    assert safe_name("../../etc/passwd") == "passwd"


def test_safe_name_windows_separators():
    assert safe_name("..\\evil") == "evil"
    assert safe_name("..\\..\\evil.pdf") == "evil.pdf"


def test_safe_name_rejects_dotdot():
    with pytest.raises(ValueError):
        safe_name("..")


def test_safe_name_rejects_empty():
    with pytest.raises(ValueError):
        safe_name("")


def test_safe_name_rejects_dot():
    with pytest.raises(ValueError):
        safe_name(".")


def test_safe_name_rejects_hidden():
    with pytest.raises(ValueError):
        safe_name(".hidden")


def test_safe_name_uses_default_when_empty():
    assert safe_name("", "fallback.pdf") == "fallback.pdf"
    assert safe_name(None, "fallback.pdf") == "fallback.pdf"


# --- atomic writes -----------------------------------------------------------


def test_atomic_write_text_success(tmp_path):
    target = tmp_path / "out.txt"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"
    # No stray temp file left behind
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_bytes_success(tmp_path):
    target = tmp_path / "out.bin"
    atomic_write_bytes(target, b"\x00\x01")
    assert target.read_bytes() == b"\x00\x01"
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_overwrites_existing(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("old")
    atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission checks")
def test_atomic_write_interrupted_leaves_original_intact(tmp_path):
    """If the temp-file write fails (read-only dir), the original survives."""
    ro_dir = tmp_path / "ro"
    ro_dir.mkdir()
    target = ro_dir / "data.txt"
    target.write_text("original")
    ro_dir.chmod(0o500)
    try:
        with pytest.raises(OSError):
            atomic_write_text(target, "replacement")
        assert target.read_text(encoding="utf-8") == "original"
    finally:
        ro_dir.chmod(0o700)
