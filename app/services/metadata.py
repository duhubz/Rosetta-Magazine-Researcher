"""Metadata parsing and caching for magazine PDFs."""

import re
import zipfile
from pathlib import Path

import app.config as cfg
from app.services import state


def parse_metadata(text: str) -> dict:
    meta = {}
    mapping = {
        "magazine name": "name",
        "publisher": "publisher",
        "date": "date",
        "issue name": "issue_name",
        "scanner": "scanner",
        "scanner url": "scanner_url",
        "editor": "editor",
        "editor url": "editor_url",
        "region": "region",
        "translation": "translation",
        "tags": "tags",
        "version": "version",
        "notes": "notes",
    }
    for line in text.splitlines():
        if ":" in line:
            key, val = line.split(":", 1)
            clean_key = key.strip().lower()
            if clean_key in mapping:
                meta[mapping[clean_key]] = val.strip()
    return meta


def get_pages_from_master(file_text: str) -> dict:
    """Splits a _COMPLETE.txt file into a dictionary of {page_num: text}."""
    pages = {}
    parts = re.split(r"\[\[PAGE_(\d+)\]\]", file_text)
    for i in range(1, len(parts), 2):
        try:
            p_num = int(parts[i])
            content = parts[i + 1].strip()
            pages[p_num] = content
        except (IndexError, ValueError):
            continue
    return pages


def get_partner_zip(pdf_rel_path: str) -> Path | None:
    data_dir = cfg.data_dir()
    pdf_path = data_dir / pdf_rel_path
    if not pdf_path.exists():
        return None
    
    # 1. Try exact match: MyMag.pdf -> MyMag.zip
    direct_zip = pdf_path.with_suffix(".zip")
    if direct_zip.exists():
        return direct_zip
        
    # 2. Folder-based fallback: Only if we are NOT in the root directory
    if pdf_path.parent != data_dir:
        zips_in_folder = list(pdf_path.parent.glob("*.zip"))
        if len(zips_in_folder) == 1:
            return zips_in_folder[0]
            
    return None


def load_metadata_cache() -> None:
    from app.services import state

    temp_cache = {}
    data_dir = cfg.data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    for pdf in data_dir.rglob("*.pdf"):
        rel_path = pdf.relative_to(data_dir).as_posix()
        partner_zip = get_partner_zip(rel_path)
        meta = {}

        if partner_zip:
            try:
                with zipfile.ZipFile(partner_zip, "r") as z:
                    meta_file = next(
                        (
                            n
                            for n in z.namelist()
                            if n.split("/")[-1].lower() == "metadata.txt"
                        ),
                        None,
                    )
                    if meta_file:
                        meta = parse_metadata(
                            z.read(meta_file).decode("utf-8", errors="ignore")
                        )
            except Exception:
                pass

        loose_meta = pdf.with_name(pdf.stem + ".metadata.txt")
        generic_meta = pdf.parent / "metadata.txt"

        if loose_meta.exists():
            meta.update(
                parse_metadata(loose_meta.read_text(encoding="utf-8", errors="ignore"))
            )
        elif generic_meta.exists() and pdf.parent != data_dir:
            meta.update(
                parse_metadata(
                    generic_meta.read_text(encoding="utf-8", errors="ignore")
                )
            )

        temp_cache[rel_path] = meta

    state.METADATA_CACHE.clear()
    state.METADATA_CACHE.update(temp_cache)


def get_transcription_text(pdf_rel_path: str, page_str: str) -> str | None:
    data_dir = cfg.data_dir()
    pdf_path = data_dir / pdf_rel_path
    p_num_int = int(page_str)

    # 1. PRIORITY: Look inside the Partner ZIP for a _COMPLETE.txt file
    partner_zip = get_partner_zip(pdf_rel_path)
    if partner_zip:
        try:
            with zipfile.ZipFile(partner_zip, "r") as z:
                master_zname = next(
                    (n for n in z.namelist() if n.split("/")[-1].lower() == f"{pdf_path.stem}_complete.txt".lower()), None
                )
                if master_zname:
                    pages = get_pages_from_master(
                        z.read(master_zname).decode("utf-8", errors="ignore")
                    )
                    if p_num_int in pages:
                        return pages[p_num_int]

                pattern = re.compile(rf"^{re.escape(pdf_path.stem)}_p0*{p_num_int}\.txt$", re.IGNORECASE)
                for zname in z.namelist():
                    if pattern.search(zname.split("/")[-1]):
                        return z.read(zname).decode("utf-8", errors="ignore")
        except Exception:
            pass

    # 2. SECONDARY: Look for loose Master File (_COMPLETE.txt)
    master_file = pdf_path.parent / f"{pdf_path.stem}_COMPLETE.txt"
    if master_file.exists():
        pages = get_pages_from_master(
            master_file.read_text(encoding="utf-8", errors="ignore")
        )
        if p_num_int in pages:
            return pages[p_num_int]

    # 3. FINAL FALLBACK: Look for loose individual _pXXX.txt files
    pattern = re.compile(rf"^{re.escape(pdf_path.stem)}_p0*{p_num_int}\.txt$", re.IGNORECASE)
    for lp in pdf_path.parent.glob(f"{pdf_path.stem}_p*.txt"):
        if pattern.search(lp.name):
            return lp.read_text(encoding="utf-8", errors="ignore")

    return None
