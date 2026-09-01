"""Utility functions."""

import logging
import os
import urllib.request
from pathlib import Path
from urllib.parse import urljoin, urlparse

import app.config as cfg

logger = logging.getLogger(__name__)

# User-Agent sent on every outbound request made through safe_urlopen
# (callers may override it via the `headers` argument).
USER_AGENT = "Rosetta-Magazine-Researcher/1.0"

_REDIRECT_CODES = (301, 302, 303, 307, 308)


def get_safe_path(rel_path: str) -> Path:
    """Resolve a relative path safely, preventing directory traversal."""
    data_dir = cfg.data_dir().resolve()
    p = (data_dir / rel_path).resolve()
    if not p.is_relative_to(data_dir):
        raise ValueError("Unsafe path traversal detected.")
    return p


def has_hidden_component(path: Path, base: Path) -> bool:
    """
    True when any component of `path` (relative to `base`) starts with a dot.

    Used to keep dot-directories/files (e.g. '.temp_<id>' in-flight download
    folders and the '.rosetta_index.db' search index) out of library scans.
    """
    try:
        rel = path.relative_to(base)
    except ValueError:
        rel = path
    return any(part.startswith(".") for part in rel.parts)


def safe_name(name: str, default: str = "") -> str:
    """
    Sanitize an untrusted (e.g. catalog-derived) filename or folder name.

    Returns only the basename component, with both '/' and '\\' treated
    as path separators. Rejects empty names, dot-names ('.', '..'), and
    hidden names (leading dot). Raises ValueError on unsafe input.
    """
    raw = str(name) if name else str(default)
    # Normalize Windows-style separators so Path.name works cross-platform.
    base = Path(raw.replace("\\", "/")).name.strip()
    if base in ("", ".", "..") or base.startswith("."):
        raise ValueError(f"Unsafe filename: {name!r}")
    return base


def atomic_write_text(path: Path, data: str, encoding: str = "utf-8") -> None:
    """Write text to `path` atomically (temp file + os.replace)."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding=encoding)
    os.replace(tmp, path)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to `path` atomically (temp file + os.replace)."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def is_allowed_fetch_url(url: str) -> bool:
    """
    Validate a URL the backend is about to fetch.

    Only http/https schemes are allowed, and the host must match the
    configured allowlist (config.yaml: security.allowed_fetch_hosts).
    Entries starting with '*.' match any subdomain of the given domain.
    """
    try:
        parsed = urlparse(str(url))
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    for allowed in cfg.allowed_fetch_hosts():
        allowed = str(allowed).lower().strip()
        if not allowed:
            continue
        if allowed.startswith("*."):
            root = allowed[2:]
            if host == root or host.endswith("." + root):
                return True
        elif host == allowed:
            return True
    return False


class URLBlockedError(Exception):
    """A URL (initial or any redirect hop) failed fetch validation.

    Lets callers distinguish policy blocks from ordinary network failures.
    """

    def __init__(self, url: str, reason: str):
        self.url = url
        self.reason = reason
        super().__init__(f"Blocked URL ({reason}): {url}")


def _fetch_url_block_reason(url: str, allow_any_host: bool) -> str | None:
    """Return why `url` must not be fetched, or None if it may be.

    http/https is enforced unconditionally. The host allowlist (reusing
    is_allowed_fetch_url and config security.allowed_fetch_hosts) is skipped
    when `allow_any_host` is true -- but never the scheme check.
    """
    try:
        scheme = urlparse(str(url)).scheme
    except Exception:
        return "scheme not http/https"
    if scheme not in ("http", "https"):
        return "scheme not http/https"
    if allow_any_host:
        return None
    if is_allowed_fetch_url(url):
        return None
    return "host not in allowlist"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Hand 3xx responses back to the caller instead of following them,
    so safe_urlopen can re-validate every hop itself."""

    def _stop(self, req, fp, code, msg, headers):
        return fp

    http_error_301 = http_error_302 = http_error_303 = _stop
    http_error_307 = http_error_308 = _stop


def _open_no_redirect(req: urllib.request.Request, timeout: float):
    """Perform a single HTTP request WITHOUT following redirects.

    Module-level seam: tests mock this instead of urllib internals.
    """
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(req, timeout=timeout)


def safe_urlopen(
    url: str,
    *,
    timeout: float,
    max_redirects: int | None = None,
    allow_any_host: bool = False,
    headers: dict[str, str] | None = None,
):
    """Open a URL, following redirects manually and re-checking each hop against
    is_allowed_fetch_url(). Raises URLBlockedError with the offending URL if any
    hop fails the allowlist. Returns the final HTTPResponse.

    - `max_redirects` defaults to config security.max_redirects (5).
    - `allow_any_host=True` skips the host allowlist for every hop but keeps
      the http/https scheme check (see security.allow_downloads_from_any_host).
    - `headers` are merged over a default User-Agent header.
    """
    if max_redirects is None:
        max_redirects = cfg.max_redirects()
    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)

    current = str(url)
    for _hop in range(max_redirects + 1):
        reason = _fetch_url_block_reason(current, allow_any_host)
        if reason:
            logger.warning("Blocked fetch URL (%s): %s", reason, current)
            raise URLBlockedError(current, reason)

        req = urllib.request.Request(current, headers=req_headers)
        response = _open_no_redirect(req, timeout=timeout)

        status = getattr(response, "status", None) or getattr(response, "code", None) or 200
        if status not in _REDIRECT_CODES:
            return response

        location = None
        resp_headers = getattr(response, "headers", None)
        if resp_headers is not None:
            location = resp_headers.get("Location") or resp_headers.get("location")
        try:
            response.close()
        except Exception as e:
            # Best-effort close of an intermediate redirect response.
            logger.debug("Error closing redirect response: %s", e)
        if not location:
            raise URLBlockedError(current, "redirect without Location header")
        # Resolve relative Location headers against the current URL.
        current = urljoin(current, location)

    logger.warning(
        "Too many redirects (max %d) fetching %s; stopped at %s",
        max_redirects,
        url,
        current,
    )
    raise URLBlockedError(current, f"too many redirects (max {max_redirects})")
