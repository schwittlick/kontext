"""the catalog: kontext's main database (books, files, blocks).

separate from survey.db (the raw file manifest): the catalog holds works
and their extracted text, the manifest holds per-file probe results.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from kontext.model import Block, Extraction

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT,
    author        TEXT,
    language      TEXT,
    status        TEXT NOT NULL,      -- extracted | awaiting_ocr | needs_conversion
    source_format TEXT,
    needs_ocr     INTEGER NOT NULL DEFAULT 0,
    word_count    INTEGER NOT NULL DEFAULT 0,
    block_count   INTEGER NOT NULL DEFAULT 0,
    quality       REAL,
    minhash       BLOB,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS book_files (
    sha256   TEXT PRIMARY KEY,
    book_id  INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    path     TEXT NOT NULL,
    format   TEXT,
    size     INTEGER,
    role     TEXT NOT NULL,           -- primary | member | duplicate | alternate_format
    added_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_book_files_book ON book_files(book_id);

CREATE TABLE IF NOT EXISTS blocks (
    book_id       INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    seq           INTEGER NOT NULL,
    chapter_idx   INTEGER,
    chapter_title TEXT,
    page          INTEGER,
    char_offset   INTEGER,
    word_count    INTEGER NOT NULL,
    text          TEXT NOT NULL,
    PRIMARY KEY (book_id, seq)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS ingest_errors (
    path   TEXT NOT NULL,
    sha256 TEXT,
    stage  TEXT,
    error  TEXT,
    at     TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def known_hashes(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT sha256 FROM book_files")}


def insert_book(
    conn: sqlite3.Connection,
    extraction: Extraction,
    files: list[dict],  # {sha256, path, format, size, role}
    minhash: bytes | None = None,
) -> int:
    ts = now()
    cur = conn.execute(
        "INSERT INTO books (title, author, language, status, source_format, needs_ocr,"
        " word_count, block_count, quality, minhash, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            extraction.title, extraction.author, extraction.language,
            extraction.status, extraction.source_format, int(extraction.needs_ocr),
            extraction.word_count, len(extraction.blocks), extraction.quality,
            minhash, ts, ts,
        ),
    )
    book_id = int(cur.lastrowid)
    conn.executemany(
        "INSERT OR REPLACE INTO book_files (sha256, book_id, path, format, size, role, added_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(f["sha256"], book_id, f["path"], f["format"], f["size"], f["role"], ts) for f in files],
    )
    conn.executemany(
        "INSERT INTO blocks (book_id, seq, chapter_idx, chapter_title, page, char_offset,"
        " word_count, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (book_id, b.seq, b.chapter_idx, b.chapter_title, b.page, b.char_offset,
             b.word_count, b.text)
            for b in extraction.blocks
        ],
    )
    conn.commit()
    return book_id


def log_error(conn: sqlite3.Connection, path: str, sha256: str | None,
              stage: str, error: str) -> None:
    conn.execute(
        "INSERT INTO ingest_errors (path, sha256, stage, error, at) VALUES (?, ?, ?, ?, ?)",
        (path, sha256, stage, error, now()),
    )
    conn.commit()


def merge_books(conn: sqlite3.Connection, canonical_id: int, alternate_id: int) -> None:
    """fold `alternate_id` (same work, other format/edition) into `canonical_id`:
    its files move over, its blocks and book row go away."""
    conn.execute(
        "UPDATE book_files SET book_id=?, role=CASE role WHEN 'duplicate' THEN 'duplicate'"
        " ELSE 'alternate_format' END WHERE book_id=?",
        (canonical_id, alternate_id),
    )
    conn.execute("DELETE FROM blocks WHERE book_id=?", (alternate_id,))
    conn.execute("DELETE FROM books WHERE id=?", (alternate_id,))
    conn.execute("UPDATE books SET updated_at=? WHERE id=?", (now(), canonical_id))
    conn.commit()


def load_signatures(conn: sqlite3.Connection, min_words: int) -> list[tuple[int, bytes, str, int]]:
    """(book_id, minhash, source_format, word_count) for all dedup-eligible
    books. needs_ocr books are excluded: until ocr runs, their text is not
    representative of the work (often just shared front matter)."""
    rows = conn.execute(
        "SELECT id, minhash, source_format, word_count FROM books"
        " WHERE minhash IS NOT NULL AND status='extracted' AND needs_ocr=0"
        " AND word_count >= ?",
        (min_words,),
    )
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def iter_blocks(conn: sqlite3.Connection, book_id: int):
    for r in conn.execute(
        "SELECT seq, chapter_idx, chapter_title, page, char_offset, word_count, text"
        " FROM blocks WHERE book_id=? ORDER BY seq",
        (book_id,),
    ):
        yield Block(
            seq=r["seq"], chapter_idx=r["chapter_idx"], chapter_title=r["chapter_title"],
            page=r["page"], char_offset=r["char_offset"], word_count=r["word_count"],
            text=r["text"],
        )
