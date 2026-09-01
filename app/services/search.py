"""
Search Service
Full-text search across magazine transcriptions and translations, backed by
the persistent SQLite FTS5 index (see search_index.py). Supports the advanced
query syntax the UI documents (OR, negations, exact phrases, trailing-*
prefix wildcards) and returns ranked results with server-side snippets.
"""

import logging
import re
import sqlite3
from typing import Any

from app.services import search_index, state
from app.services.text_utils import split_sections

logger = logging.getLogger(__name__)

MAX_RESULTS = 200


def _normalize_meta_date(d_str: str) -> str:
    """
    Normalizes human-entered dates into a sortable ISO-like format (YYYY-MM-DD).

    Handles:
    - 1999 -> 1999-01-01
    - 1999/10 -> 1999-10-01
    - 10-31-1999 -> 1999-10-31
    """
    if not d_str:
        return ""
    clean = re.sub(r"[^\d\-\/]", "", d_str).replace("/", "-")
    parts = clean.split("-")
    if len(parts) == 3:
        if len(parts[0]) == 4:
            return f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}"
        elif len(parts[2]) == 4:
            return f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
    elif len(parts) == 2 and len(parts[0]) == 4:
        return f"{parts[0]}-{parts[1].zfill(2)}-01"
    elif len(parts) == 1 and len(parts[0]) == 4:
        return f"{parts[0]}-01-01"
    return clean


def _fts_quote(term: str) -> str:
    """Escapes a bare term for FTS5 MATCH: double quotes, wrap in quotes.

    A trailing '*' becomes an FTS5 prefix query ("term"*); any other '*'
    characters are dropped (FTS5 has no infix wildcards).
    """
    prefix = term.endswith("*") and len(term) > 1
    core = term.replace("*", "").strip()
    if not core:
        return ""
    quoted = '"' + core.replace('"', '""') + '"'
    return quoted + "*" if prefix else quoted


def _fts_phrase(phrase: str) -> str:
    """Escapes an exact phrase for FTS5 MATCH (quoted phrase query)."""
    core = phrase.replace('"', '""').strip()
    return f'"{core}"' if core else ""


def _build_match_expression(
    or_groups: list[list[str]],
    exact_phrases: list[str],
    neg_terms: list[str],
    neg_exact: list[str],
) -> str:
    """
    Translates the parsed query into an FTS5 MATCH expression.

    Positive terms are AND-ed within a group, groups are OR-ed; exact
    phrases are AND-ed on top; negations are applied with FTS5's NOT.
    Returns '' when the query has no positive component (FTS5 cannot
    evaluate a bare NOT).
    """
    group_exprs = []
    for grp in or_groups:
        quoted = [q for q in (_fts_quote(t) for t in grp) if q]
        if quoted:
            group_exprs.append("(" + " AND ".join(quoted) + ")")
    parts = []
    if group_exprs:
        parts.append("(" + " OR ".join(group_exprs) + ")")
    phrase_exprs = [q for q in (_fts_phrase(p) for p in exact_phrases) if q]
    parts.extend(phrase_exprs)

    if not parts:
        return ""
    expr = "(" + " AND ".join(parts) + ")"

    for neg in neg_terms:
        q = _fts_quote(neg)
        if q:
            expr = f"({expr} NOT {q})"
    for neg in neg_exact:
        q = _fts_phrase(neg)
        if q:
            expr = f"({expr} NOT {q})"
    return expr


def search(
    query: str,
    scope: str,
    inc_jp: bool,
    inc_en: bool,
    inc_sum: bool,
    current_mag: str,
    mag_filter: str,
    date_start: str,
    date_end: str,
    tag_filter: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Performs a filtered, index-backed search across the local magazine library.

    Query Syntax Supported:
    - "exact phrase": Wrapped in quotes for strict matching.
    - -term: Excludes results containing this word.
    - word OR term: Matches either word.
    - wildcard*: '*' at the end of a word (prefix match).

    Results are ranked by FTS5 bm25 relevance and include a server-generated
    snippet with <mark>…</mark> around matched terms.

    Raises:
        search_index.IndexUnavailableError: when the index DB cannot be opened.

    Returns:
        tuple: (results_list, terms_to_highlight_in_ui)
    """
    # --- 1. Query Parsing (same syntax as the pre-index implementation) ---
    neg_exact = re.findall(r'-"([^"]+)"', query)
    query = re.sub(r'-"[^"]+"', "", query)

    exact_phrases = re.findall(r'"([^"]+)"', query)
    query = re.sub(r'"[^"]+"', "", query)

    raw_terms = query.split()
    neg_terms = [t[1:] for t in raw_terms if t.startswith("-") and len(t) > 1]
    pos_terms_raw = [t for t in raw_terms if not t.startswith("-")]

    pos_query = " ".join(pos_terms_raw)
    or_groups = [grp.split() for grp in pos_query.split(" OR ") if grp.split()]

    # List of terms for the UI to highlight in red
    highlight_list = exact_phrases + [t.replace("*", "") for t in pos_terms_raw if t != "OR"]

    match_expr = _build_match_expression(or_groups, exact_phrases, neg_terms, neg_exact)
    if not match_expr:
        return [], highlight_list

    # --- 2. Pre-compute the set of magazines passing metadata filters ---
    cache_snapshot = list(state.METADATA_CACHE.items())
    allowed: set[str] = set()
    for mag_rel_path, meta in cache_snapshot:
        if scope == "current" and mag_rel_path != current_mag:
            continue
        if mag_filter and mag_filter not in meta.get("name", "").lower():
            continue
        if tag_filter:
            meta_tags = meta.get("tags", "").lower()
            if not all(t.strip() in meta_tags for t in tag_filter.split(",") if t.strip()):
                continue
        if date_start or date_end:
            m_date = meta.get("date", "")
            if not m_date:
                continue
            norm_m_date = _normalize_meta_date(m_date)
            if not norm_m_date:
                continue
            if date_start and norm_m_date < date_start:
                continue
            if date_end and norm_m_date > date_end:
                continue
        allowed.add(mag_rel_path)

    if not allowed:
        return [], highlight_list

    # --- 3. FTS5 query ---
    conn = search_index.get_index()
    restrict_sections = not (inc_jp and inc_en and inc_sum)

    results: list[dict[str, Any]] = []
    with search_index._LOCK:
        try:
            cursor = conn.execute(
                "SELECT pdf_path, page, "
                "snippet(pages, 2, '<mark>', '</mark>', '…', 20), text "
                "FROM pages WHERE pages MATCH ? ORDER BY rank",
                (match_expr,),
            )
            for pdf_path, page, snip, text in cursor:
                if pdf_path not in allowed:
                    continue
                if restrict_sections and not _matches_sections(
                    text,
                    inc_jp,
                    inc_en,
                    inc_sum,
                    or_groups,
                    exact_phrases,
                    neg_terms,
                    neg_exact,
                ):
                    continue
                results.append({"mag": pdf_path, "page": int(page), "snippet": snip})
                if len(results) >= MAX_RESULTS:
                    break
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
            # A malformed MATCH expression should never 500; log and return empty.
            logger.warning(f"FTS5 query failed for {match_expr!r}: {e}")
            return [], highlight_list

    return results, highlight_list


def _matches_sections(
    text: str,
    inc_jp: bool,
    inc_en: bool,
    inc_sum: bool,
    or_groups: list[list[str]],
    exact_phrases: list[str],
    neg_terms: list[str],
    neg_exact: list[str],
) -> bool:
    """
    Section-restricted post-filter: when the user unticks transcription /
    translation / summary checkboxes, verify the match against only the
    enabled sections (literal, case-insensitive — diacritic folding is not
    re-applied here).
    """
    jp_text, en_text, sum_text = split_sections(text)
    searchable = ""
    if inc_jp:
        searchable += jp_text + " "
    if inc_en:
        searchable += en_text + " "
    if inc_sum:
        searchable += sum_text + " "
    if not searchable.strip():
        return False
    blob = searchable.lower()

    if any(nep.lower() in blob for nep in neg_exact):
        return False
    if any(nt.lower() in blob for nt in neg_terms):
        return False
    if any(ep.lower() not in blob for ep in exact_phrases):
        return False

    if or_groups:

        def term_to_regex(term: str) -> str:
            return re.escape(term.lower()).replace(r"\*", ".*")

        return any(all(re.search(term_to_regex(t), blob) for t in grp) for grp in or_groups)
    return True
