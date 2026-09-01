"""Tests for the application update-check service."""

import io
import json
import time

import pytest

import app.config as cfg
from app.services import update_check


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._stream = io.BytesIO(payload)

    def read(self) -> bytes:
        return self._stream.read()

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def reset_status():
    """Reset the service's in-memory result between tests."""
    with update_check._status_lock:
        update_check._status = None
    yield
    with update_check._status_lock:
        update_check._status = None


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("v1.0", (1, 0, 0, 1, "")),
        ("1.2-beta", (1, 2, 0, 0, "beta")),
        ("v2.0.1", (2, 0, 1, 1, "")),
        ("garbage", None),
        ("", None),
    ],
)
def test_parse_version_table(tag, expected):
    assert update_check.parse_version(tag) == expected


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("v1.0", False),
        ("v2.0", False),
        ("v2.0.1", True),
        ("v2.1-beta", True),
        ("v3.0-beta", True),
        ("2.0.0-beta", False),
        ("garbage", False),
    ],
)
def test_is_newer_table(candidate, expected):
    assert update_check.is_newer(candidate, "2.0.0") is expected


def test_check_for_updates_success(monkeypatch):
    monkeypatch.setattr(
        update_check,
        "safe_urlopen",
        lambda url, **kwargs: _FakeResponse(
            b'{"tag_name":"v9.9.9","html_url":"https://github.com/example/release"}'
        ),
    )
    result = update_check.check_for_updates()
    assert result == {
        "update_available": True,
        "current_version": "2.0.0",
        "latest_version": "v9.9.9",
        "download_url": "https://github.com/example/release",
    }


def test_check_for_updates_malformed_json_returns_none(monkeypatch):
    monkeypatch.setattr(
        update_check, "safe_urlopen", lambda url, **kwargs: _FakeResponse(b"not json")
    )
    assert update_check.check_for_updates() is None


def test_check_for_updates_missing_tag_returns_none(monkeypatch):
    monkeypatch.setattr(
        update_check,
        "safe_urlopen",
        lambda url, **kwargs: _FakeResponse(b'{"html_url":"https://github.com/example"}'),
    )
    assert update_check.check_for_updates() is None


def test_run_check_rate_limited_skips_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "data_dir", lambda: tmp_path)
    status = {
        "update_available": True,
        "current_version": "2.0.0",
        "latest_version": "v9.9.9",
        "download_url": "https://github.com/example",
    }
    (tmp_path / update_check.STATE_FILENAME).write_text(
        json.dumps({"last_check": time.time(), "status": status}), encoding="utf-8"
    )
    monkeypatch.setattr(
        update_check, "safe_urlopen", lambda *args, **kwargs: pytest.fail("fetched")
    )
    update_check._run_check()
    assert update_check.get_update_status() == status


def test_run_check_fetches_and_persists_when_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "data_dir", lambda: tmp_path)
    (tmp_path / update_check.STATE_FILENAME).write_text(
        json.dumps({"last_check": 0, "status": {}}), encoding="utf-8"
    )
    payload = b'{"tag_name":"v9.9.9","html_url":"https://github.com/example"}'
    monkeypatch.setattr(update_check, "safe_urlopen", lambda url, **kwargs: _FakeResponse(payload))
    update_check._run_check()
    saved = json.loads((tmp_path / update_check.STATE_FILENAME).read_text(encoding="utf-8"))
    assert saved["status"] == update_check.get_update_status()
    assert update_check.get_update_status()["update_available"] is True


def test_run_check_rate_limited_recomputes_after_upgrade(tmp_path, monkeypatch):
    """A persisted 'update available' from before an upgrade must not resurface."""
    monkeypatch.setattr(cfg, "data_dir", lambda: tmp_path)
    status = {
        "update_available": True,
        "current_version": "1.0.0",  # written by the old binary
        "latest_version": "v2.0",  # ...which the user has since installed
        "download_url": "https://github.com/example",
    }
    (tmp_path / update_check.STATE_FILENAME).write_text(
        json.dumps({"last_check": time.time(), "status": status}), encoding="utf-8"
    )
    monkeypatch.setattr(
        update_check, "safe_urlopen", lambda *args, **kwargs: pytest.fail("fetched")
    )
    update_check._run_check()
    refreshed = update_check.get_update_status()
    assert refreshed["update_available"] is False
    assert refreshed["current_version"] == "2.0.0"
    assert refreshed["latest_version"] == "v2.0"


def test_start_thread_disabled_noop(monkeypatch):
    monkeypatch.setattr(cfg, "update_check_enabled", lambda: False)
    monkeypatch.setattr(
        update_check, "safe_urlopen", lambda *args, **kwargs: pytest.fail("fetched")
    )
    update_check.start_update_check_thread()
    assert update_check.get_update_status()["latest_version"] is None


def test_update_status_endpoint(client):
    response = client.get("/api/update-status")
    assert response.status_code == 200
    assert set(response.json) == {
        "update_available",
        "current_version",
        "latest_version",
        "download_url",
    }


def test_github_is_allowed_fetch_host():
    assert "api.github.com" in cfg.DEFAULT_ALLOWED_FETCH_HOSTS
