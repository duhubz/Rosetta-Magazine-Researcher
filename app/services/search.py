"""
Search Service
Provides full-text search capabilities across magazine transcriptions and translations.
Includes support for advanced query syntax (OR, negations, wildcards).
"""

import re
import zipfile
from typing import Any, Optional

import app.config as cfg
from app.services import metadata, state

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
    Performs a filtered search across the local magazine library.
    
    Query Syntax Supported:
    - "exact phrase": Wrapped in quotes for strict matching.
    - -term: Excludes results containing this word.
    - word OR term: Matches either word.
    - wildcard*: Using '*' at the end of a word.
    
    Returns:
        tuple: (results_list, terms_to_highlight_in_ui)
    """
    data_dir = cfg.data_dir()
    results: list[dict[str, Any]] = []

    # --- 1. Query Parsing ---
    # Extract exact phrase negations: -"bad phrase"
    neg_exact = re.findall(r'-"([^"]+)"', query)
    query = re.sub(r'-"[^"]+"', "", query)

    # Extract exact phrase inclusions: "good phrase"
    exact_phrases = re.findall(r'"([^"]+)"', query)
    query = re.sub(r'"[^"]+"', "", query)

    raw_terms = query.split()
    neg_terms = [t[1:] for t in raw_terms if t.startswith("-") and len(t) > 1]
    pos_terms_raw = [t for t in raw_terms if not t.startswith("-")]

    pos_query = " ".join(pos_terms_raw)
    or_groups_raw = pos_query.split(" OR ")

    def term_to_regex(term: str) -> str:
        """Converts user wildcards (*) into regex (.*)."""
        return re.escape(term.lower()).replace(r"\*", ".*")

    or_groups = []
    for grp in or_groups_raw:
        terms = grp.split()
        if terms:
            or_groups.append([term_to_regex(t) for t in terms])

    # List of terms for the UI to highlight in red
    highlight_list = exact_phrases + [
        t.replace("*", "") for t in pos_terms_raw if t != "OR"
    ]

    def scan_text(text: str, mag_rel_path: str, page_num: int) -> None:
        """Inner helper to perform actual regex matching against page text."""
        # Clean GA specific headers
        clean_text = re.sub(r"^#\s?GA-TRANSCRIPTION\s*", "", text, flags=re.IGNORECASE)
        parts = re.split(r"#\s?GA-TRANSLATION", clean_text, flags=re.IGNORECASE)
        en_text, sum_text = "", ""

        # Section Splitting
        if len(parts) > 1:
            jp_text = parts[0]
            sub = re.split(r"#\s?GA-SUMMARY", parts[1], flags=re.IGNORECASE)
            en_text = sub[0]
            sum_text = sub[1] if len(sub) > 1 else ""
        else:
            sub = re.split(r"#\s?GA-SUMMARY", parts[0], flags=re.IGNORECASE)
            jp_text = sub[0]
            sum_text = sub[1] if len(sub) > 1 else ""

        # Build combined searchable blob based on user toggles
        searchable_text = ""
        if inc_jp: searchable_text += jp_text + " "
        if inc_en: searchable_text += en_text + " "
        if inc_sum: searchable_text += sum_text + " "

        if not searchable_text.strip():
            return
        blob = searchable_text.lower()

        # Filtering Logic
        if any(nep.lower() in blob for nep in neg_exact): return
        if any(nt.lower() in blob for nt in neg_terms): return
        if any(ep.lower() not in blob for ep in exact_phrases): return

        if or_groups:
            group_matched = False
            for grp in or_groups:
                if all(re.search(t_regex, blob) for t_regex in grp):
                    group_matched = True
                    break
            if not group_matched: return

        # Result snippet generation
        first_match = 0
        if exact_phrases:
            first_match = blob.find(exact_phrases[0].lower())
        elif pos_terms_raw and pos_terms_raw[0] != "OR":
            m = re.search(or_groups[0][0], blob) if or_groups and or_groups[0] else None
            if m: first_match = m.start()

        idx = max(0, first_match)
        clean_val = searchable_text.replace("\\n", " ").replace("\n", " ")
        snippet = clean_val[max(0, idx - 40) : min(len(clean_val), idx + 60)]
        results.append({"mag": mag_rel_path, "page": page_num, "snippet": snippet})

    # --- 2. Iterate Library ---
    for mag_rel_path in state.METADATA_CACHE.keys():
        if scope == "current" and mag_rel_path != current_mag:
            continue

        meta = state.METADATA_CACHE.get(mag_rel_path, {})

        # Filter by Magazine Name or Tags
        if mag_filter and mag_filter not in meta.get("name", "").lower():
            continue

        if tag_filter:
            meta_tags = meta.get("tags", "").lower()
            if not all(t.strip() in meta_tags for t in tag_filter.split(",") if t.strip()):
                continue

        # Filter by Date Range
        if date_start or date_end:
            m_date = meta.get("date", "")
            if not m_date: continue
            norm_m_date = _normalize_meta_date(m_date)
            if not norm_m_date: continue
            if date_start and norm_m_date < date_start: continue
            if date_end and norm_m_date > date_end: continue

        pdf_path = data_dir / mag_rel_path
        
        # Priority 1: Scan loose page files (most recent edits)
        master_file = pdf_path.parent / f"{pdf_path.stem}_COMPLETE.txt"
        master_txt_exists = master_file.exists()

        if master_txt_exists:
            pages = metadata.get_pages_from_master(master_file.read_text(encoding="utf-8", errors="ignore"))
            for p_num, p_text in pages.items():
                scan_text(p_text, mag_rel_path, p_num)
        else:
            # Individual page files
            pattern = re.compile(rf"^{re.escape(pdf_path.stem)}_p(\d+)\.txt$", re.IGNORECASE)
            for txt in pdf_path.parent.glob(f"{pdf_path.stem}_p*.txt"):
                m = pattern.search(txt.name)
                if m: scan_text(txt.read_text(encoding="utf-8", errors="ignore"), mag_rel_path, int(m.group(1)))

        # Priority 2: Scan partner ZIP (if no loose master overrides it)
        partner_zip = metadata.get_partner_zip(mag_rel_path)
        if partner_zip and not master_txt_exists:
            try:
                with zipfile.ZipFile(partner_zip, "r") as z:
                    master_zname = next((n for n in z.namelist() if n.split("/")[-1].lower() == f"{pdf_path.stem}_complete.txt".lower()), None)
                    if master_zname:
                        pages = metadata.get_pages_from_master(z.read(master_zname).decode("utf-8", errors="ignore"))
                        for p_num, p_text in pages.items():
                            scan_text(p_text, mag_rel_path, p_num)
                    else:
                        pattern = re.compile(rf"^{re.escape(pdf_path.stem)}_p(\d+)\.txt$", re.IGNORECASE)
                        for zname in z.namelist():
                            m = pattern.search(zname.split("/")[-1])
                            if m: scan_text(z.read(zname).decode("utf-8", errors="ignore"), mag_rel_path, int(m.group(1)))
            except Exception:
                pass

    return results[:200], highlight_list