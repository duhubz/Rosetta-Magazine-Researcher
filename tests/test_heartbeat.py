"""Tests for the tab-aware idle-shutdown heartbeat logic (state.record_ping /
state.should_shutdown) and the /api/ping route's tab registration."""

import pytest

import app.config as cfg
from app.services import state

THRESHOLD = 180
GRACE = 20


@pytest.fixture(autouse=True)
def pinned_heartbeat_config(monkeypatch):
    """Pin the heartbeat thresholds so tests don't depend on config.yaml."""
    monkeypatch.setattr(cfg, "heartbeat_shutdown_seconds", lambda: THRESHOLD)
    monkeypatch.setattr(cfg, "heartbeat_close_grace_seconds", lambda: GRACE)
    # Fixed baseline: last ping long ago, no tabs, no goodbyes.
    monkeypatch.setattr(state, "LAST_PING", 1000.0)
    monkeypatch.setattr(state, "LAST_CLOSE", None)
    state.ACTIVE_TABS.clear()


# ---------------------------------------------------------------------------
# should_shutdown decision rules
# ---------------------------------------------------------------------------


def test_registered_tab_keeps_server_alive():
    state.ACTIVE_TABS["tab-a"] = 1000.0
    assert state.should_shutdown(now=1000.0 + THRESHOLD) is False


def test_hidden_tab_with_throttled_pings_survives():
    # Browsers throttle hidden-tab timers to ~1/min; entries stay fresh.
    state.ACTIVE_TABS["tab-a"] = 1000.0
    for now in (1060.0, 1120.0, 1180.0):
        state.ACTIVE_TABS["tab-a"] = now
        assert state.should_shutdown(now=now + 5) is False


def test_stale_tab_is_pruned_and_threshold_failsafe_fires():
    # Tab never says goodbye (browser crash): pruned once silent > threshold,
    # then the LAST_PING failsafe triggers the shutdown.
    state.ACTIVE_TABS["tab-a"] = 1000.0
    state.LAST_PING = 1000.0
    assert state.should_shutdown(now=1000.0 + THRESHOLD) is False
    assert state.should_shutdown(now=1000.0 + THRESHOLD + 1) is True
    assert state.ACTIVE_TABS == {}


def test_startup_without_browser_uses_threshold_not_grace():
    # No tab has ever registered or closed: the short grace must NOT apply,
    # or the server would exit before the browser finishes opening.
    state.LAST_PING = 1000.0
    assert state.should_shutdown(now=1000.0 + GRACE + 1) is False
    assert state.should_shutdown(now=1000.0 + THRESHOLD + 1) is True


def test_clean_close_exits_after_grace():
    state.LAST_PING = 1000.0
    state.LAST_CLOSE = 1005.0  # goodbye arrived after the last ping
    assert state.should_shutdown(now=1005.0 + GRACE) is False
    assert state.should_shutdown(now=1005.0 + GRACE + 1) is True


def test_refresh_cancels_pending_close():
    # Refresh = goodbye followed by a re-register from the reloaded page.
    state.LAST_CLOSE = 1005.0
    state.LAST_PING = 1008.0
    state.ACTIVE_TABS["tab-b"] = 1008.0
    assert state.should_shutdown(now=1005.0 + GRACE + 10) is False


def test_ping_after_goodbye_blocks_quick_exit_even_without_tab_id():
    # A ping newer than the goodbye (legacy client without ?tab=) must
    # disable the quick-exit path; only the threshold failsafe remains.
    state.LAST_CLOSE = 1005.0
    state.LAST_PING = 1010.0
    assert state.should_shutdown(now=1010.0 + GRACE + 1) is False
    assert state.should_shutdown(now=1010.0 + THRESHOLD + 1) is True


def test_second_tab_keeps_server_alive_when_first_closes():
    state.record_ping("tab-a")
    state.record_ping("tab-b")
    state.record_ping("tab-a", closing=True)
    assert "tab-a" not in state.ACTIVE_TABS
    assert "tab-b" in state.ACTIVE_TABS
    assert state.should_shutdown() is False


# ---------------------------------------------------------------------------
# record_ping semantics
# ---------------------------------------------------------------------------


def test_record_ping_registers_and_updates_last_ping():
    before = state.LAST_PING
    state.record_ping("tab-a")
    assert "tab-a" in state.ACTIVE_TABS
    assert before < state.LAST_PING


def test_record_closing_deregisters_without_touching_last_ping():
    state.record_ping("tab-a")
    last_ping = state.LAST_PING
    state.record_ping("tab-a", closing=True)
    assert state.ACTIVE_TABS == {}
    assert last_ping == state.LAST_PING
    assert state.LAST_CLOSE is not None


def test_record_ping_without_tab_id_only_updates_last_ping():
    before = state.LAST_PING
    state.record_ping(None)
    assert state.ACTIVE_TABS == {}
    assert before < state.LAST_PING


# ---------------------------------------------------------------------------
# /api/ping route
# ---------------------------------------------------------------------------


def test_ping_route_registers_tab(client):
    res = client.get("/api/ping?tab=route-tab")
    assert res.status_code == 200
    assert "route-tab" in state.ACTIVE_TABS


def test_ping_route_closing_deregisters_tab_via_tokenless_post(client):
    client.get("/api/ping?tab=route-tab")
    # sendBeacon POSTs without the session token; must stay whitelisted.
    res = client.post("/api/ping?tab=route-tab&closing=1")
    assert res.status_code == 200
    assert "route-tab" not in state.ACTIVE_TABS
    assert state.LAST_CLOSE is not None


def test_ping_route_without_params_still_ok(client):
    res = client.get("/api/ping")
    assert res.status_code == 200
    assert res.get_data(as_text=True) == "ok"
