"""Tests for the native Mega public-share downloader."""

import hashlib
import io
import json

import pytest
from Crypto.Cipher import AES
from Crypto.Util import Counter

from app.services import mega_download as mega

SMALL = b"Rosetta Mega known-answer vector: the quick brown fox jumps over the lazy dog 0123456789."
TINY = b"tiny-file!"
BIG = bytes((i * 7 + 3) % 256 for i in range(300_000))

VECTORS = [
    {
        "plain": SMALL,
        "name": "small.pdf",
        "key": "EBAQEBAQEBBNC0PxoYxJ_hESExQVFhcYRAFI_ayCRu4",
        "at": "f82XWJ52k9HjnuEu8VnH_4kwPrshfEC2DvX7teQpOBo",
        "k": (16909060, 84281096, 151653132, 219025168),
        "iv": (286397204, 353769240),
        "meta": (1140934909, 2894218990),
        "sha": "490740b6ac6261e2187653bee9059558fe99b6d86018fa57bf94534d7d84de6c",
    },
    {
        "plain": TINY,
        "name": "tiny.bin",
        "key": "EBAQEBAQEBBndmWLROZQuxESExQVFhcYbnxuh0noX6s",
        "at": "Mj1j6D0W2FzAQXDDlhvNZFttuRYxxLC8Am9G7tphrwo",
        "k": (16909060, 84281096, 151653132, 219025168),
        "iv": (286397204, 353769240),
        "meta": (1853648519, 1239965611),
        "sha": "3d5993307eed7a414ee08e779089ece473fa3551ebf9f638306ea9b643461e89",
    },
    {
        "plain": BIG,
        "name": "big_Data.zip",
        "key": "VjQUVAYjVEEmkZZY4JvrUYiZqrvM3e7_JoC0a6TOjSY",
        "at": "mWZpzbfIqO1MUYzCpazp1IX0PKePpC0PJ8ZLmCNg_uE",
        "k": (3735928559, 3405691582, 1122867, 1146447479),
        "iv": (2291772091, 3437096703),
        "meta": (645969003, 2764999974),
        "sha": "b4a75fb1a615f8fc525b1080800d4653a820a56f248a9825f803498db4eeb4ee",
    },
]
DOWNLOAD_URL = "https://gfs204n112.userstorage.mega.co.nz/dl/x"


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._data = io.BytesIO(payload)
        self.status = 200

    def read(self, n=-1):
        return self._data.read(n)

    def close(self):
        pass


def _encrypt(plaintext, k, iv):
    counter = Counter.new(128, initial_value=((iv[0] << 32) + iv[1]) << 64)
    return AES.new(mega._a32_to_str(k), AES.MODE_CTR, counter=counter).encrypt(plaintext)


def _fake_safe_urlopen(api_response_obj, file_bytes):
    calls = []

    def fake(url, **kwargs):
        calls.append(url)
        if url.startswith(mega.MEGA_API_URL):
            return _FakeResponse(json.dumps([api_response_obj]).encode())
        if url == DOWNLOAD_URL:
            return _FakeResponse(file_bytes)
        raise AssertionError(f"unexpected URL: {url}")

    fake.calls = calls
    return fake, DOWNLOAD_URL


def test_is_public_share_url_valid_v2():
    key = "k"
    assert mega.is_public_share_url(f"https://mega.nz/file/AAAAAAAA#{key}")
    assert mega.is_public_share_url(f"https://mega.co.nz/file/AAAAAAAA#{key}")
    assert mega.is_public_share_url(f"https://mega.io/file/AAAAAAAA#{key}")


def test_is_public_share_url_valid_v1_legacy():
    assert mega.is_public_share_url("https://mega.nz/#!AAAAAAAA!somekey")


def test_is_public_share_url_rejects():
    urls = [
        "https://mega.nz/folder/X#k",
        "https://mega.nz/file/X",
        "http://mega.nz/file/X#k",
        "https://mega.nz.evil.com/file/X#k",
        "https://www.mega.nz/file/X#k",
        "not a url",
    ]
    assert all(not mega.is_public_share_url(url) for url in urls)


def test_parse_share_url_v2_and_v1():
    assert mega.parse_share_url("https://mega.nz/file/AAAAAAAA#somekey") == (
        "AAAAAAAA",
        "somekey",
    )
    assert mega.parse_share_url("https://mega.nz/#!AAAAAAAA!somekey") == (
        "AAAAAAAA",
        "somekey",
    )
    with pytest.raises(ValueError):
        mega.parse_share_url("https://mega.nz/folder/X#k")


def test_mask_key():
    assert mega.mask_key("https://mega.nz/file/H#SECRET") == "https://mega.nz/file/H#..."
    assert mega.mask_key("https://mega.nz/file/H") == "https://mega.nz/file/H"


def test_decode_node_key_vectors():
    for vector in (VECTORS[0], VECTORS[2]):
        k, iv, meta = mega._decode_node_key(vector["key"])
        assert (k, iv, meta) == (vector["k"], vector["iv"], vector["meta"])
    with pytest.raises(ValueError):
        mega._decode_node_key("AQ")


def test_decrypt_attrs_vectors():
    assert mega._decrypt_attrs(VECTORS[0]["at"], VECTORS[0]["k"]) == {"n": "small.pdf"}
    assert mega._decrypt_attrs(VECTORS[2]["at"], VECTORS[2]["k"]) == {"n": "big_Data.zip"}
    with pytest.raises(ValueError):
        mega._decrypt_attrs(VECTORS[0]["at"], VECTORS[2]["k"])


def test_get_chunks_schedule():
    assert list(mega._get_chunks(300_000)) == [(0, 131072), (131072, 168928)]
    assert list(mega._get_chunks(10)) == [(0, 10)]
    chunks = list(mega._get_chunks(5_000_000))
    assert sum(size for _, size in chunks) == 5_000_000


@pytest.mark.parametrize("vector", VECTORS)
def test_download_roundtrip(vector, tmp_path, monkeypatch):
    ciphertext = _encrypt(vector["plain"], vector["k"], vector["iv"])
    assert hashlib.sha256(ciphertext).hexdigest() == vector["sha"]
    fake, download_url = _fake_safe_urlopen(
        {"g": DOWNLOAD_URL, "s": len(vector["plain"]), "at": vector["at"]}, ciphertext
    )
    monkeypatch.setattr(mega, "safe_urlopen", fake)
    out = tmp_path / "result"
    assert (
        mega.download_public_file(f"https://mega.nz/file/HANDLE#{vector['key']}", out)
        == vector["name"]
    )
    assert out.read_bytes() == vector["plain"]
    assert all(url.startswith(mega.MEGA_API_URL) or url == download_url for url in fake.calls)


def test_allow_any_host_forwarded(tmp_path, monkeypatch):
    vector = VECTORS[0]
    ciphertext = _encrypt(vector["plain"], vector["k"], vector["iv"])
    calls = []

    def fake(url, **kwargs):
        calls.append(kwargs)
        if url.startswith(mega.MEGA_API_URL):
            return _FakeResponse(
                json.dumps(
                    [{"g": DOWNLOAD_URL, "s": len(vector["plain"]), "at": vector["at"]}]
                ).encode()
            )
        if url == DOWNLOAD_URL:
            return _FakeResponse(ciphertext)
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(mega, "safe_urlopen", fake)
    mega.download_public_file(
        f"https://mega.nz/file/HANDLE#{vector['key']}",
        tmp_path / "allow",
        allow_any_host=True,
    )
    assert all(call.get("allow_any_host") is True for call in calls)

    calls.clear()
    mega.download_public_file(
        f"https://mega.nz/file/HANDLE#{vector['key']}",
        tmp_path / "default",
    )
    assert all(not call.get("allow_any_host", False) for call in calls)


def test_download_mac_mismatch(tmp_path, monkeypatch):
    vector = VECTORS[0]
    ciphertext = bytearray(_encrypt(vector["plain"], vector["k"], vector["iv"]))
    ciphertext[0] ^= 1
    fake, download_url = _fake_safe_urlopen(
        {"g": DOWNLOAD_URL, "s": len(vector["plain"]), "at": vector["at"]}, bytes(ciphertext)
    )
    monkeypatch.setattr(mega, "safe_urlopen", fake)
    with pytest.raises(RuntimeError, match="integrity|MAC"):
        mega.download_public_file(f"https://mega.nz/file/HANDLE#{vector['key']}", tmp_path / "out")
    assert list(tmp_path.iterdir()) == []


def test_download_progress_and_abort(tmp_path, monkeypatch):
    vector = VECTORS[2]
    ciphertext = _encrypt(vector["plain"], vector["k"], vector["iv"])
    progress = []
    fake, download_url = _fake_safe_urlopen(
        {"g": DOWNLOAD_URL, "s": len(BIG), "at": vector["at"]}, ciphertext
    )
    monkeypatch.setattr(mega, "safe_urlopen", fake)
    mega.download_public_file(
        f"https://mega.nz/file/HANDLE#{vector['key']}",
        tmp_path / "ok",
        progress_cb=lambda done, total: progress.append((done, total)),
    )
    assert all(a < b for (a, _), (b, _) in zip(progress, progress[1:], strict=False))
    assert progress[-1] == (300_000, 300_000)
    (tmp_path / "ok").unlink()
    fake, download_url = _fake_safe_urlopen(
        {"g": DOWNLOAD_URL, "s": len(BIG), "at": vector["at"]}, ciphertext
    )
    monkeypatch.setattr(mega, "safe_urlopen", fake)
    with pytest.raises(RuntimeError, match="aborted"):
        mega.download_public_file(
            f"https://mega.nz/file/HANDLE#{vector['key']}",
            tmp_path / "abort",
            should_abort=lambda: True,
        )
    assert list(tmp_path.iterdir()) == []


def test_api_error_int_response(monkeypatch):
    fake, _ = _fake_safe_urlopen(-9, b"")
    monkeypatch.setattr(mega, "safe_urlopen", fake)
    with pytest.raises(RuntimeError, match="-9"):
        mega._api_get_file_info("x")
    monkeypatch.setattr(mega, "safe_urlopen", lambda *args, **kwargs: _FakeResponse(b"-16"))
    with pytest.raises(RuntimeError, match="-16"):
        mega._api_get_file_info("x")


def test_api_missing_g(monkeypatch):
    fake, _ = _fake_safe_urlopen({"s": 1, "at": "x"}, b"")
    monkeypatch.setattr(mega, "safe_urlopen", fake)
    with pytest.raises(RuntimeError, match="not accessible"):
        mega._api_get_file_info("x")


def test_truncated_download(tmp_path, monkeypatch):
    vector = VECTORS[0]
    ciphertext = _encrypt(vector["plain"], vector["k"], vector["iv"])
    fake, download_url = _fake_safe_urlopen(
        {"g": DOWNLOAD_URL, "s": len(vector["plain"]), "at": vector["at"]},
        ciphertext[: len(ciphertext) // 2],
    )
    monkeypatch.setattr(mega, "safe_urlopen", fake)
    with pytest.raises(RuntimeError, match="truncated"):
        mega.download_public_file(f"https://mega.nz/file/HANDLE#{vector['key']}", tmp_path / "out")
    assert list(tmp_path.iterdir()) == []
