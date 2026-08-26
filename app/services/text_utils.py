"""
Text Utilities
Shared helpers for splitting Rosetta transcription text into pages and sections.

The [[PAGE_nnn]] page-marker format and the #GA-TRANSLATION / #GA-SUMMARY
section format were previously parsed in several places (metadata.py,
search.py, api.py); this module is the single source of truth for both.
"""

import re

# Matches the page markers used by _COMPLETE.txt master files:
#   [[PAGE_001]] page one text ... [[PAGE_002]] page two text ...
PAGE_MARKER_RE = re.compile(r"\[\[PAGE_(\d+)\]\]")

_TRANSCRIPTION_HEADER_RE = re.compile(r"^#\s?GA-TRANSCRIPTION\s*", re.IGNORECASE)
_TRANSLATION_SPLIT_RE = re.compile(r"#\s?GA-TRANSLATION", re.IGNORECASE)
_SUMMARY_SPLIT_RE = re.compile(r"#\s?GA-SUMMARY", re.IGNORECASE)


def split_pages(text: str) -> dict[int, str]:
    """
    Splits a master transcription file (_COMPLETE.txt) into individual pages.

    Format expected: [[PAGE_001]] content... [[PAGE_002]] content...

    Args:
        text: The full text of a _COMPLETE.txt file.

    Returns:
        dict: A mapping of {page_number: content_string}
    """
    pages: dict[int, str] = {}
    parts = PAGE_MARKER_RE.split(text)
    for i in range(1, len(parts), 2):
        try:
            p_num = int(parts[i])
            content = parts[i + 1].strip()
            pages[p_num] = content
        except (IndexError, ValueError):
            continue
    return pages


def split_sections(text: str) -> tuple[str, str, str]:
    """
    Splits a single page's text into its Rosetta sections.

    Layout: [#GA-TRANSCRIPTION] transcription [#GA-TRANSLATION translation]
    [#GA-SUMMARY summary]. The translation and summary sections are optional.

    Returns:
        tuple: (transcription, translation, summary) — stripped strings.
    """
    clean = _TRANSCRIPTION_HEADER_RE.sub("", text or "")
    parts = _TRANSLATION_SPLIT_RE.split(clean)
    en_text, sum_text = "", ""

    if len(parts) > 1:
        jp_text = parts[0]
        sub = _SUMMARY_SPLIT_RE.split(parts[1])
        en_text = sub[0]
        sum_text = sub[1] if len(sub) > 1 else ""
    else:
        sub = _SUMMARY_SPLIT_RE.split(parts[0])
        jp_text = sub[0]
        sum_text = sub[1] if len(sub) > 1 else ""

    return jp_text.strip(), en_text.strip(), sum_text.strip()
