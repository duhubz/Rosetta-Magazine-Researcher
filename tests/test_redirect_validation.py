"""Tests for redirect validation in utils.safe_urlopen.

urllib follows HTTP redirects by default, so checking only the initial URL
would let an allowlisted host redirect the app to file:// or an internal
host. safe_urlopen follows redirects manually and re-checks EVERY hop
(scheme + host allowlist). No real network requests are made: the
single-hop opener seam (app.utils._open_no_redirect) is mocked.
"""

import io
import logging
from unittest import mock

import pytest

import app.config as cfg
from app import utils
from app.services import download, state
from app.utils import URLBlockedError, safe_urlopen

BODY = b"%PDF-1.4 final content"


class _FakeHTTPResponse:
    """Minimal stand-in for http.client.HTTPResponse (status/headers/read)."""

    def __init__(self, status: int = 200, headers: dict | None = None, data: bytes = BODY):
        self.status = status
        self.headers = headers or {"Content-Length": str(len(data))}
        self._buf = io.BytesIO(data)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _redirect(location: str, status: int = 302) -> _FakeHTTPResponse:
    return _FakeHTTPResponse(status=status, headers={"Location": location}, data=b"")


class _HopServer:
    """Scripted single-hop responder: URL -> response (or a callable)."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.fetched: list[str] = []

    def __call__(self, req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.fetched.append(url)
        for prefix, resp in self.routes.items():
            if url.startswith(prefix):
                return resp() if callable(resp) else resp
        raise AssertionError(f"Unexpected fetch (no scripted route): {url}")


def _serve(routes: dict) -> _HopServer:
    return _HopServer(routes)


# --- blocked redirect targets ----------------------------------------------------


def test_redirect_to_file_url_blocked():
    """Allowlisted host redirecting to file:// must raise URLBlockedError."""
    server = _serve(
        {
            "https://archive.org/download/item/x.pdf": _redirect("file:///etc/passwd"),
        }
    )
    with (
        mock.patch("app.utils._open_no_redirect", server),
        pytest.raises(URLBlockedError) as exc,
    ):
        safe_urlopen("https://archive.org/download/item/x.pdf", timeout=5)

    assert exc.value.url == "file:///etc/passwd"
    assert "scheme not http/https" in exc.value.reason
    # The file:// hop itself was never fetched
    assert server.fetched == ["https://archive.org/download/item/x.pdf"]


def test_redirect_to_non_allowlisted_host_blocked():
    """Allowlisted host redirecting to a non-allowlisted host is blocked."""
    server = _serve(
        {
            "https://archive.org/x.pdf": _redirect("https://evil.internal.corp/x.pdf"),
        }
    )
    with (
        mock.patch("app.utils._open_no_redirect", server),
        pytest.raises(URLBlockedError) as exc,
    ):
        safe_urlopen("https://archive.org/x.pdf", timeout=5)

    assert exc.value.url == "https://evil.internal.corp/x.pdf"
    assert "host not in allowlist" in exc.value.reason
    assert all("evil" not in u for u in server.fetched)


# --- allowed redirects ------------------------------------------------------------


def test_redirect_between_allowlisted_hosts_allowed():
    server = _serve(
        {
            "https://archive.org/x.pdf": _redirect("https://www.gamingalexandria.com/x.pdf"),
            "https://www.gamingalexandria.com/x.pdf": _FakeHTTPResponse(),
        }
    )
    with mock.patch("app.utils._open_no_redirect", server):
        resp = safe_urlopen("https://archive.org/x.pdf", timeout=5)

    assert resp.read() == BODY
    assert server.fetched == [
        "https://archive.org/x.pdf",
        "https://www.gamingalexandria.com/x.pdf",
    ]


def test_redirect_chain_of_three_allowlisted_hops_allowed():
    server = _serve(
        {
            "https://archive.org/a": _redirect("https://archive.org/b", status=301),
            "https://archive.org/b": _redirect("https://ia1.us.archive.org/c", status=307),
            "https://ia1.us.archive.org/c": _redirect("https://gamingalexandria.com/d", status=308),
            "https://gamingalexandria.com/d": _FakeHTTPResponse(),
        }
    )
    with mock.patch("app.utils._open_no_redirect", server):
        resp = safe_urlopen("https://archive.org/a", timeout=5)

    assert resp.read() == BODY
    assert len(server.fetched) == 4


def test_archive_org_mirror_redirect_pattern(workspace):
    """Real-world archive.org pattern: archive.org -> ia###.us.archive.org.

    Both hosts are covered by the default '*.archive.org' wildcard, so a full
    download through download_waterfall must still succeed end-to-end.
    """
    final = _FakeHTTPResponse()
    server = _serve(
        {
            # download_waterfall appends a ?nocache=... param; match by prefix.
            "https://archive.org/download/item/x.pdf": lambda: _redirect(
                "https://ia801408.us.archive.org/12/items/item/x.pdf"
            ),
            "https://ia801408.us.archive.org/12/items/item/x.pdf": final,
        }
    )
    out = workspace / "Magazines" / "out.pdf"
    state.DOWNLOAD_STATE["t_ia"] = {
        "status": "Initializing...",
        "progress": 0,
        "error": None,
        "done": False,
    }

    with mock.patch("app.utils._open_no_redirect", server):
        ok = download.download_waterfall(
            "t_ia", out, ["https://archive.org/download/item/x.pdf"], "PDF"
        )

    assert ok is True
    assert out.read_bytes() == BODY
    assert len(server.fetched) == 2
    assert server.fetched[1].startswith("https://ia801408.us.archive.org/")
    assert state.DOWNLOAD_STATE["t_ia"]["error"] is None


def test_blocked_redirect_skips_source_and_tries_next_mirror(workspace, caplog):
    """A mirror that redirects to an evil host is skipped (with the blocked
    hop named in the log) and the next mirror is used -- same waterfall
    semantics as a blocked initial URL."""
    server = _serve(
        {
            "https://archive.org/bad.pdf": _redirect("http://evil.com/steal.pdf"),
            "https://gamingalexandria.com/good.pdf": _FakeHTTPResponse(),
        }
    )
    out = workspace / "Magazines" / "out.pdf"
    state.DOWNLOAD_STATE["t_skip"] = {
        "status": "Initializing...",
        "progress": 0,
        "error": None,
        "done": False,
    }
    sources = ["https://archive.org/bad.pdf", "https://gamingalexandria.com/good.pdf"]

    with mock.patch("app.utils._open_no_redirect", server), caplog.at_level(logging.WARNING):
        ok = download.download_waterfall("t_skip", out, sources, "PDF")

    assert ok is True
    assert out.read_bytes() == BODY
    # The blocked hop is named in the warning log
    assert "http://evil.com/steal.pdf" in caplog.text
    assert "host not in allowlist" in caplog.text
    # The evil hop itself was never fetched
    assert all("evil.com" not in u for u in server.fetched)


# --- max_redirects ---------------------------------------------------------------


def test_redirect_chain_exceeding_max_redirects_raises():
    server = _serve(
        {
            # Every hop redirects to itself-ish: an endless chain.
            "https://archive.org/hop": lambda: _redirect("https://archive.org/hop?again=1"),
        }
    )
    with (
        mock.patch("app.utils._open_no_redirect", server),
        pytest.raises(URLBlockedError) as exc,
    ):
        safe_urlopen("https://archive.org/hop", timeout=5, max_redirects=3)

    assert "too many redirects (max 3)" in str(exc.value)
    # initial request + 3 followed redirects = 4 fetches, then abort
    assert len(server.fetched) == 4


def test_redirect_loop_fails_cleanly():
    """A -> B -> A loops until max_redirects, then fails with a clear error."""
    server = _serve(
        {
            "https://archive.org/a": lambda: _redirect("https://archive.org/b"),
            "https://archive.org/b": lambda: _redirect("https://archive.org/a"),
        }
    )
    with (
        mock.patch("app.utils._open_no_redirect", server),
        pytest.raises(URLBlockedError) as exc,
    ):
        safe_urlopen("https://archive.org/a", timeout=5)

    assert "too many redirects" in str(exc.value)
    # default max_redirects (config) is 5: 6 fetches total
    assert len(server.fetched) == cfg.max_redirects() + 1


# --- allow_any_host bypass -------------------------------------------------------


def test_allow_any_host_permits_evil_host_but_not_file_scheme():
    """With the any-host bypass, an evil-host redirect is followed, but a
    file:// redirect is still blocked (scheme check is unconditional)."""
    server = _serve(
        {
            "https://archive.org/x.pdf": _redirect("http://evil.com/x.pdf"),
            "http://evil.com/x.pdf": _FakeHTTPResponse(),
            "https://archive.org/f.pdf": _redirect("file:///etc/passwd"),
        }
    )
    with mock.patch("app.utils._open_no_redirect", server):
        resp = safe_urlopen("https://archive.org/x.pdf", timeout=5, allow_any_host=True)
        assert resp.read() == BODY

        with pytest.raises(URLBlockedError) as exc:
            safe_urlopen("https://archive.org/f.pdf", timeout=5, allow_any_host=True)

    assert exc.value.url == "file:///etc/passwd"
    assert "scheme not http/https" in exc.value.reason
    assert all(not u.startswith("file:") for u in server.fetched)


def test_download_bypass_flag_applies_to_redirect_hops(workspace, monkeypatch):
    """allow_downloads_from_any_host=true propagates to redirect hops in the
    download waterfall (evil redirect allowed, file:// still blocked)."""
    monkeypatch.setattr(cfg, "allow_downloads_from_any_host", lambda: True)
    server = _serve(
        {
            "https://archive.org/x.pdf": lambda: _redirect("http://evil.com/mirror.pdf"),
            "http://evil.com/mirror.pdf": _FakeHTTPResponse(),
        }
    )
    out = workspace / "Magazines" / "out.pdf"
    state.DOWNLOAD_STATE["t_any"] = {
        "status": "Initializing...",
        "progress": 0,
        "error": None,
        "done": False,
    }

    with mock.patch("app.utils._open_no_redirect", server):
        ok = download.download_waterfall("t_any", out, ["https://archive.org/x.pdf"], "PDF")

    assert ok is True
    assert out.read_bytes() == BODY


# --- relative Location resolution ------------------------------------------------


def test_relative_location_resolved_against_current_host():
    """Location: /path/x.pdf is resolved against the redirecting URL's host
    and re-checked (same host here, so it passes)."""
    server = _serve(
        {
            "https://archive.org/download/old.pdf": _redirect("/path/x.pdf"),
            "https://archive.org/path/x.pdf": _FakeHTTPResponse(),
        }
    )
    with mock.patch("app.utils._open_no_redirect", server):
        resp = safe_urlopen("https://archive.org/download/old.pdf", timeout=5)

    assert resp.read() == BODY
    assert server.fetched == [
        "https://archive.org/download/old.pdf",
        "https://archive.org/path/x.pdf",
    ]


# --- misc hardening ---------------------------------------------------------------


def test_blocked_initial_url_never_fetched():
    """The initial-URL check lives inside safe_urlopen too (de-duplicated
    from the old _download_url_allowed pre-check)."""
    server = _serve({})
    with mock.patch("app.utils._open_no_redirect", server):
        with pytest.raises(URLBlockedError):
            safe_urlopen("http://evil.com/x.pdf", timeout=5)
        with pytest.raises(URLBlockedError):
            safe_urlopen("file:///etc/passwd", timeout=5)

    assert server.fetched == []


def test_redirect_without_location_header_raises():
    server = _serve(
        {
            "https://archive.org/x.pdf": _FakeHTTPResponse(status=302, headers={}, data=b""),
        }
    )
    with (
        mock.patch("app.utils._open_no_redirect", server),
        pytest.raises(URLBlockedError) as exc,
    ):
        safe_urlopen("https://archive.org/x.pdf", timeout=5)

    assert "redirect without Location header" in exc.value.reason


def test_user_agent_header_sent(monkeypatch):
    """Every outbound request carries the Rosetta User-Agent by default."""
    seen_headers = {}

    def fake_open(req, timeout=None):
        seen_headers.update(dict(req.header_items()))
        return _FakeHTTPResponse()

    monkeypatch.setattr("app.utils._open_no_redirect", fake_open)
    safe_urlopen("https://archive.org/x.pdf", timeout=5)

    ua = {k.lower(): v for k, v in seen_headers.items()}.get("user-agent", "")
    assert ua == utils.USER_AGENT
