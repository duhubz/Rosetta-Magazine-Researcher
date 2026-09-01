"""Native downloader for public Mega file shares (API + AES-CTR + MAC verify)."""

import base64
import json
import logging
import os
import struct
import tempfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from Crypto.Cipher import AES
from Crypto.Util import Counter

import app.config as cfg
from app.utils import safe_urlopen

logger = logging.getLogger(__name__)

MEGA_HOSTS = {"mega.nz", "mega.co.nz", "mega.io"}
MEGA_API_URL = "https://g.api.mega.co.nz/cs"
_CHUNK_READ = 65536  # network read size within a Mega chunk


def is_public_share_url(url: str) -> bool:
    """Return whether *url* is a supported public Mega file share."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or host not in MEGA_HOSTS:
            return False
        if parsed.path.startswith("/file/") and bool(parsed.fragment):
            return True
        if parsed.path in ("", "/") and parsed.fragment.startswith("!"):
            handle, separator, key = parsed.fragment[1:].partition("!")
            return bool(separator and handle and key)
    except Exception as exc:
        logger.debug("Could not parse Mega share URL: %s", exc)
    return False


def parse_share_url(url: str) -> tuple[str, str]:
    """Extract a Mega node handle and its base64url key."""
    if not is_public_share_url(url):
        raise ValueError("Unsupported Mega share URL")
    parsed = urlparse(url)
    if parsed.path.startswith("/file/"):
        handle = parsed.path[len("/file/") :].split("/", 1)[0]
        key = parsed.fragment
    else:
        handle, _, key = parsed.fragment[1:].partition("!")
    if not handle or not key:
        raise ValueError("Unsupported Mega share URL")
    return handle, key


def mask_key(url: str) -> str:
    """Hide a share URL's decryption key for safe logging."""
    if "#" not in url:
        return url
    return url.split("#", 1)[0] + "#..."


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _str_to_a32(b: bytes) -> tuple[int, ...]:
    return struct.unpack(">%dI" % (len(b) // 4), b)  # noqa: UP031


def _a32_to_str(a) -> bytes:
    return struct.pack(">%dI" % len(a), *a)  # noqa: UP031


def _decode_node_key(
    key_b64: str,
) -> tuple[tuple[int, int, int, int], tuple[int, int], tuple[int, int]]:
    raw = _b64url_decode(key_b64)
    if len(raw) != 32:
        raise ValueError("Invalid Mega key length")
    w = _str_to_a32(raw)
    return (
        (w[0] ^ w[4], w[1] ^ w[5], w[2] ^ w[6], w[3] ^ w[7]),
        (w[4], w[5]),
        (w[6], w[7]),
    )


def _api_get_file_info(handle: str, *, allow_any_host: bool = False) -> dict:
    """Fetch public Mega file metadata."""
    body = json.dumps([{"a": "g", "g": 1, "p": handle}]).encode()
    response = safe_urlopen(
        MEGA_API_URL + "?id=0",
        timeout=cfg.download_timeout(),
        data=body,
        headers={"Content-Type": "application/json"},
        allow_any_host=allow_any_host,
    )
    resp = json.loads(response.read())
    if isinstance(resp, int):
        raise RuntimeError(f"Mega API error {resp}")
    if isinstance(resp, list) and resp and isinstance(resp[0], int):
        raise RuntimeError(f"Mega API error {resp[0]}")
    info = resp[0]
    if "g" not in info:
        raise RuntimeError("Mega file not accessible")
    return info


def _decrypt_attrs(at_b64: str, k: tuple) -> dict:
    cipher = AES.new(_a32_to_str(k), AES.MODE_CBC, b"\0" * 16)
    data = cipher.decrypt(_b64url_decode(at_b64))
    if not data.startswith(b"MEGA"):
        raise ValueError("Invalid Mega decryption key")
    return json.loads(data[4 : data.rindex(b"}") + 1])


def _get_chunks(size: int):
    p = 0
    s = 0x20000
    while p + s < size:
        yield p, s
        p += s
        if s < 0x100000:
            s += 0x20000
    yield p, size - p


def download_public_file(
    url: str,
    out_path: Path,
    *,
    progress_cb: Callable[[int, int], None] | None = None,
    should_abort: Callable[[], bool] | None = None,
    allow_any_host: bool = False,
) -> str:
    """Download, decrypt, authenticate, and atomically install a Mega file."""
    handle, key_b64 = parse_share_url(url)
    k, iv, meta_mac = _decode_node_key(key_b64)
    info = _api_get_file_info(handle, allow_any_host=allow_any_host)
    attrs = _decrypt_attrs(info["at"], k)
    size = int(info["s"])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    response = None
    try:
        with tempfile.NamedTemporaryFile(dir=out_path.parent, delete=False) as temp:
            tmp_name = temp.name
            k_str = _a32_to_str(k)
            counter = Counter.new(128, initial_value=((iv[0] << 32) + iv[1]) << 64)
            aes = AES.new(k_str, AES.MODE_CTR, counter=counter)
            mac_encryptor = AES.new(k_str, AES.MODE_CBC, b"\0" * 16)
            iv_str = _a32_to_str((iv[0], iv[1], iv[0], iv[1]))
            mac_str = b"\0" * 16
            response = safe_urlopen(
                info["g"], timeout=cfg.download_timeout(), allow_any_host=allow_any_host
            )
            done = 0
            for _chunk_start, chunk_size in _get_chunks(size):
                raw = bytearray()
                while len(raw) < chunk_size:
                    if should_abort and should_abort():
                        raise RuntimeError("Download aborted")
                    piece = response.read(min(_CHUNK_READ, chunk_size - len(raw)))
                    if not piece:
                        raise RuntimeError("Mega download truncated")
                    raw.extend(piece)
                chunk = aes.decrypt(bytes(raw))
                padded = chunk + b"\0" * (-len(chunk) % 16)
                if padded:
                    chunk_mac = AES.new(k_str, AES.MODE_CBC, iv_str).encrypt(padded)[-16:]
                    mac_str = mac_encryptor.encrypt(chunk_mac)
                temp.write(chunk)
                done += chunk_size
                if progress_cb:
                    progress_cb(done, size)
            fm = _str_to_a32(mac_str)
            if (fm[0] ^ fm[1], fm[2] ^ fm[3]) != meta_mac:
                raise RuntimeError("Mega download failed integrity check (MAC mismatch)")
        os.replace(tmp_name, out_path)
        tmp_name = None
        return attrs["n"]
    except Exception:
        logger.debug("Mega download failed for %s", mask_key(url), exc_info=True)
        raise
    finally:
        if response is not None:
            try:
                response.close()
            except Exception as exc:
                logger.debug("Could not close Mega download response: %s", exc)
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError as exc:
                logger.debug("Temporary Mega download already removed: %s", exc)
