"""sqlite manifest: one row per file in the dump.

this database is the survey's main artifact -- phase 1 ingestion starts
from it instead of re-walking and re-probing the dump.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path            TEXT PRIMARY KEY,
    size            INTEGER NOT NULL,
    mtime_ns        INTEGER NOT NULL,
    ext             TEXT,
    format          TEXT,
    status          TEXT NOT NULL,
    sha256          TEXT,
    pages           INTEGER,
    text_class      TEXT,
    chars_per_page  REAL,
    word_estimate   INTEGER,
    language        TEXT,
    lang_confidence REAL,
    title           TEXT,
    author          TEXT,
    has_metadata    INTEGER NOT NULL DEFAULT 0,
    contains        TEXT,
    error           TEXT,
    probe_version   INTEGER NOT NULL,
    surveyed_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);
CREATE INDEX IF NOT EXISTS idx_files_format ON files(format);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    root          TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    files_seen    INTEGER,
    files_probed  INTEGER,
    files_skipped INTEGER,
    errors        INTEGER,
    sampled       INTEGER NOT NULL DEFAULT 0,
    probe_version INTEGER NOT NULL
);
"""

FILE_COLUMNS = [
    "path", "size", "mtime_ns", "ext", "format", "status", "sha256", "pages",
    "text_class", "chars_per_page", "word_estimate", "language",
    "lang_confidence", "title", "author", "has_metadata", "contains", "error",
    "probe_version", "surveyed_at",
]

_UPSERT = (
    f"INSERT OR REPLACE INTO files ({', '.join(FILE_COLUMNS)}) "
    f"VALUES ({', '.join(':' + c for c in FILE_COLUMNS)})"
)


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


def existing_signatures(conn: sqlite3.Connection) -> dict[str, tuple[int, int, int]]:
    """path -> (size, mtime_ns, probe_version) for resume/skip decisions."""
    rows = conn.execute("SELECT path, size, mtime_ns, probe_version FROM files")
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def upsert_files(conn: sqlite3.Connection, records: list[dict]) -> None:
    if not records:
        return
    conn.executemany(_UPSERT, records)
    conn.commit()


def start_run(conn: sqlite3.Connection, root: str, started_at: str,
              sampled: bool, probe_version: int) -> int:
    cur = conn.execute(
        "INSERT INTO runs (root, started_at, sampled, probe_version) VALUES (?, ?, ?, ?)",
        (root, started_at, int(sampled), probe_version),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, finished_at: str,
               seen: int, probed: int, skipped: int, errors: int) -> None:
    conn.execute(
        "UPDATE runs SET finished_at=?, files_seen=?, files_probed=?, "
        "files_skipped=?, errors=? WHERE id=?",
        (finished_at, seen, probed, skipped, errors, run_id),
    )
    conn.commit()
