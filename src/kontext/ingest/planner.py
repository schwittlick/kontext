"""turn the survey manifest into ingest tasks.

decisions made here:
  - exact duplicates (same sha256): one representative path is ingested
  - folders with many html files are one exploded book, not many
  - scanned pdfs / djvu: registered as awaiting_ocr (phase 4 fills the text)
  - mobi / office docs: conversion tasks (graceful when tools are missing)
  - empty pdfs and corrupt/drm files: skipped, counted
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from kontext.ingest.extractors import _natural_key

EXPLODED_HTML_MIN_FILES = 8
# an exploded book has machine-ish sequential names (page_1.html, split003
# ...); a folder of many descriptively named htmls is a collection of
# individual articles and stays one-book-per-file
EXPLODED_DIGIT_STEM_SHARE = 0.7
EXPLODED_COMMON_PREFIX_MIN = 6

# dir names that never are a book title; walk up for a real one
GENERIC_DIR_NAMES = {
    "files", "file", "html", "htm", "xhtml", "text", "texts", "book", "books",
    "ebook", "ebooks", "chapters", "content", "contents", "data", "pages",
    "images", "oebps", "ops", "www", "output", "export",
}

EXTRACT_FORMATS = {"pdf", "epub", "txt", "html", "fb2"}
CONVERT_FORMATS = {"mobi", "doc"}
OCR_FORMATS = {"djvu"}


def plan(survey_conn: sqlite3.Connection, known_hashes: set[str]) -> tuple[list[dict], Counter]:
    rows = [dict(r) for r in survey_conn.execute(
        "SELECT path, size, sha256, format, status, text_class, title, author, language"
        " FROM files WHERE sha256 IS NOT NULL AND ("
        "   (status='ok' AND format IN ('pdf','epub','mobi','txt','html','fb2','djvu'))"
        "   OR format='doc')"
    )]
    skipped: Counter = Counter()

    # one representative per sha256; identical copies add nothing
    by_sha: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_sha[r["sha256"]].append(r)
    unique_rows = []
    for group in by_sha.values():
        group.sort(key=lambda r: (len(r["path"]), r["path"]))
        unique_rows.append(group[0])
        skipped["exact_duplicate"] += len(group) - 1

    # group html files into exploded books per directory
    html_by_dir: dict[str, list[dict]] = defaultdict(list)
    for r in unique_rows:
        if r["format"] == "html":
            html_by_dir[str(Path(r["path"]).parent)].append(r)
    exploded_dirs = {d for d, files in html_by_dir.items() if _looks_exploded(files)}

    tasks: list[dict] = []
    done_dirs: set[str] = set()
    for r in unique_rows:
        # office lock files (~$foo.doc, .~lock.foo#) are artifacts, not documents
        if Path(r["path"]).name.startswith(("~$", ".~lock")):
            skipped["lock_artifact"] += 1
            continue
        if r["sha256"] in known_hashes:
            skipped["already_ingested"] += 1
            continue
        fmt = r["format"]
        base = {
            "title": r["title"], "author": r["author"], "language": r["language"],
            "format": fmt, "paths": [r["path"]],
            "files": [_file_entry(r, role="primary")],
        }

        if fmt == "html" and str(Path(r["path"]).parent) in exploded_dirs:
            d = str(Path(r["path"]).parent)
            if d in done_dirs:
                continue  # the whole directory became one earlier task
            done_dirs.add(d)
            members = sorted(html_by_dir[d], key=lambda m: _natural_key(m["path"]))
            members = [m for m in members if m["sha256"] not in known_hashes]
            if not members:
                skipped["already_ingested"] += 1
                continue
            tasks.append({
                "kind": "exploded_html", "format": "html",
                "title": _dir_title(d), "author": None, "language": r["language"],
                "paths": [m["path"] for m in members],
                "files": [_file_entry(m, role="member" if i else "primary")
                          for i, m in enumerate(members)],
            })
        elif fmt == "pdf" and r["text_class"] in ("text", "mixed"):
            tasks.append({**base, "kind": "pdf", "needs_ocr": r["text_class"] == "mixed"})
        elif fmt == "pdf" and r["text_class"] == "scanned" or fmt in OCR_FORMATS:
            tasks.append({**base, "kind": "register", "status": "awaiting_ocr"})
        elif fmt == "pdf":
            skipped["empty_pdf"] += 1
        elif fmt in CONVERT_FORMATS:
            tasks.append({**base, "kind": "convert"})
        elif fmt in EXTRACT_FORMATS:
            tasks.append({**base, "kind": fmt})

    return tasks, skipped


def _looks_exploded(files: list[dict]) -> bool:
    if len(files) < EXPLODED_HTML_MIN_FILES:
        return False
    stems = [Path(f["path"]).stem for f in files]
    digit_share = sum(1 for s in stems if re.search(r"\d", s)) / len(stems)
    if digit_share >= EXPLODED_DIGIT_STEM_SHARE:
        return True
    return len(os.path.commonprefix(stems)) >= EXPLODED_COMMON_PREFIX_MIN


def _dir_title(d: str) -> str:
    """nearest ancestor dir name that looks like a title, not like plumbing."""
    for part in reversed(Path(d).parts):
        cleaned = re.sub(r"^[\d\s_.\-]+", "", part).strip()
        if cleaned and cleaned.lower() not in GENERIC_DIR_NAMES:
            return cleaned[:200]
    return Path(d).name


def _file_entry(row: dict, role: str) -> dict:
    return {
        "sha256": row["sha256"], "path": row["path"], "format": row["format"],
        "size": row["size"], "role": role,
    }
