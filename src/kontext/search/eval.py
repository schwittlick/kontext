"""golden-set evaluation: score search against known query→passage answers.

a golden set is ~30 cases of the form "for this query, this passage is a
right answer" (see golden.yaml). eval runs each query through the live
search stack and measures how often the expected passage lands in the top
k (hit@k) and at what rank (mrr). that makes every tuning knob — chunk
size, rrf weights, rerank on/off — measurable instead of a guess.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from kontext.search.query import Hit, search

_WS_RE = re.compile(r"\s+")


def _norm(s: str) -> str:
    """casefold + collapse whitespace, so extraction/ocr spacing and case
    don't sink an otherwise-correct match. works for non-latin scripts too."""
    return _WS_RE.sub(" ", s).strip().casefold()


@dataclass
class GoldenCase:
    query: str
    book: str | int              # title/author substring, or a numeric book id
    must_contain: str | None = None
    note: str | None = None


@dataclass
class CaseResult:
    case: GoldenCase
    rank: int | None             # 1-based rank of first correct hit; None = miss
    reachable: bool              # the expected passage exists in the indexed corpus


@dataclass
class EvalReport:
    results: list[CaseResult]
    k: int

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def hits(self) -> int:
        return sum(r.rank is not None for r in self.results)

    @property
    def hit_rate(self) -> float:
        return self.hits / self.total if self.total else 0.0

    @property
    def mrr(self) -> float:
        if not self.total:
            return 0.0
        return sum(1.0 / r.rank for r in self.results if r.rank) / self.total

    @property
    def unreachable(self) -> list[CaseResult]:
        return [r for r in self.results if not r.reachable]


def load_golden(path: Path) -> list[GoldenCase]:
    import yaml

    raw = yaml.safe_load(path.read_text()) or []
    cases = []
    for i, item in enumerate(raw, 1):
        if "query" not in item or "book" not in item:
            raise ValueError(f"golden case #{i} needs both `query` and `book`: {item!r}")
        cases.append(GoldenCase(
            query=item["query"], book=item["book"],
            must_contain=item.get("must_contain"), note=item.get("note"),
        ))
    return cases


def _book_matches(hit: Hit, book: str | int) -> bool:
    if isinstance(book, int):
        return hit.book_id == book
    needle = _norm(str(book))
    return needle in _norm(hit.title or "") or needle in _norm(hit.author or "")


def _is_correct(hit: Hit, case: GoldenCase) -> bool:
    if not _book_matches(hit, case.book):
        return False
    if case.must_contain is None:
        return True
    return _norm(case.must_contain) in _norm(hit.text)


def _resolve_book_ids(conn: sqlite3.Connection, book: str | int) -> list[int]:
    if isinstance(book, int):
        return [r[0] for r in conn.execute("SELECT id FROM books WHERE id=?", (book,))]
    like = f"%{book}%"
    return [r[0] for r in conn.execute(
        "SELECT id FROM books WHERE title LIKE ? OR author LIKE ?", (like, like))]


def _reachable(conn: sqlite3.Connection, case: GoldenCase) -> bool:
    """can search possibly get this right? true iff the named book is in the
    catalog with chunks, and (if given) some chunk contains must_contain.
    a false here means a broken case — book not indexed, or a phrase that
    straddles a chunk boundary — not a search failure."""
    book_ids = _resolve_book_ids(conn, case.book)
    if not book_ids:
        return False
    marks = ",".join("?" * len(book_ids))
    if case.must_contain is None:
        row = conn.execute(
            f"SELECT 1 FROM chunks WHERE book_id IN ({marks}) LIMIT 1", book_ids
        ).fetchone()
        return row is not None
    needle = _norm(case.must_contain)
    rows = conn.execute(
        f"SELECT text FROM chunks WHERE book_id IN ({marks})", book_ids)
    return any(needle in _norm(r[0]) for r in rows)


def evaluate(
    conn: sqlite3.Connection,
    store,
    embedder,
    cases: list[GoldenCase],
    k: int = 10,
    rerank: bool = False,
) -> EvalReport:
    results = []
    for case in cases:
        hits = search(conn, store, embedder, case.query,
                      limit=k, candidates=max(50, k), rerank=rerank)
        rank = next((i for i, h in enumerate(hits, 1) if _is_correct(h, case)), None)
        results.append(CaseResult(case, rank, _reachable(conn, case)))
    return EvalReport(results, k)
