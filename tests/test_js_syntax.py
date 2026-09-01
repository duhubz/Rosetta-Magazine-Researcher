"""
Parse-check for first-party JavaScript.

pytest/ruff can't see JS, so a single syntax error in main.js (e.g. an
unescaped backtick inside the HELP_MARKDOWN template literal) ships a
completely dead frontend while every Python gate stays green. This smoke
test parses each first-party JS file so that failure mode breaks CI instead.

Vendor bundles are excluded (minified, third-party). esprima predates
optional chaining / nullish coalescing, so those tokens are shimmed away
before parsing — the substitutions are token-level and cannot themselves
introduce or hide a syntax error.
"""

from pathlib import Path

import pytest

esprima = pytest.importorskip("esprima")

JS_DIR = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


def _first_party_js() -> list[Path]:
    return sorted(p for p in JS_DIR.glob("*.js"))


@pytest.mark.parametrize("js_file", _first_party_js(), ids=lambda p: p.name)
def test_js_file_parses(js_file: Path) -> None:
    src = js_file.read_text(encoding="utf-8")
    shimmed = src.replace("?.", ".").replace("??=", "=").replace("??", "||")
    esprima.parseScript(shimmed)
