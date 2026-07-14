"""process-pool worker: hash + probe one file, return a manifest row."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from kontext.survey.probes import PROBE_VERSION, probe_file

_HASH_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def survey_one(path_str: str, size: int, mtime_ns: int) -> dict:
    """runs inside a worker process; must always return a row dict."""
    path = Path(path_str)
    row = {
        "path": path_str,
        "size": size,
        "mtime_ns": mtime_ns,
        "ext": path.suffix.lower().lstrip("."),
        "format": None,
        "status": "error",
        "sha256": None,
        "pages": None,
        "text_class": None,
        "chars_per_page": None,
        "word_estimate": None,
        "language": None,
        "lang_confidence": None,
        "title": None,
        "author": None,
        "has_metadata": 0,
        "contains": None,
        "error": None,
        "probe_version": PROBE_VERSION,
        "surveyed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        row["sha256"] = sha256_file(path) if size else None
    except OSError as exc:
        row["error"] = f"unreadable: {exc}"
        return row

    result = probe_file(path, size)
    row.update(
        format=result.format,
        status=result.status,
        pages=result.pages,
        text_class=result.text_class,
        chars_per_page=result.chars_per_page,
        word_estimate=result.word_estimate,
        language=result.language,
        lang_confidence=result.lang_confidence,
        title=result.title,
        author=result.author,
        # embedded metadata (not a filename fallback) counts as real metadata
        has_metadata=int(bool(result.author)),
        contains=result.contains,
        error=result.error,
    )
    return row
