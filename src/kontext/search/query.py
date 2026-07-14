"""hybrid retrieval: dense (qdrant) + bm25 (fts5) -> rrf -> optional rerank."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

RRF_K = 60
RERANK_MODEL = "BAAI/bge-reranker-v2-m3"

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


@dataclass
class Hit:
    chunk_id: int
    book_id: int
    score: float
    title: str | None
    author: str | None
    language: str | None
    source_format: str | None
    locator: str
    text: str
    path: str | None


def lexical_search(conn: sqlite3.Connection, query: str, limit: int) -> list[int]:
    tokens = _TOKEN_RE.findall(query.lower())
    if not tokens:
        return []
    match = " OR ".join(f'"{t}"' for t in tokens)
    rows = conn.execute(
        "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
        (match, limit),
    )
    return [r[0] for r in rows]


def rrf_fuse(rankings: list[list[int]]) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for pos, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + pos + 1)
    return scores


def search(
    conn: sqlite3.Connection,
    store,
    embedder,
    query: str,
    limit: int = 10,
    candidates: int = 50,
    rerank: bool = False,
) -> list[Hit]:
    dense = [cid for cid, _ in store.search(embedder.encode([query])[0], candidates)]
    lexical = lexical_search(conn, query, candidates)
    fused = sorted(rrf_fuse([dense, lexical]).items(), key=lambda kv: -kv[1])

    pool = fused[: candidates if rerank else limit]
    hits = _hydrate(conn, pool)
    if rerank and hits:
        hits = _rerank(query, hits)[:limit]
    return hits


def _hydrate(conn: sqlite3.Connection, scored: list[tuple[int, float]]) -> list[Hit]:
    by_id = dict(scored)
    if not by_id:
        return []
    marks = ",".join("?" * len(by_id))
    rows = conn.execute(
        f"""SELECT c.id, c.book_id, c.text, c.chapter_idx, c.chapter_title,
                   c.page_start, c.page_end,
                   b.title, b.author, b.language, b.source_format,
                   (SELECT path FROM book_files f WHERE f.book_id = b.id
                     AND f.role = 'primary' LIMIT 1) AS path
            FROM chunks c JOIN books b ON b.id = c.book_id
            WHERE c.id IN ({marks})""",
        list(by_id),
    ).fetchall()
    hits = [
        Hit(
            chunk_id=r["id"], book_id=r["book_id"], score=by_id[r["id"]],
            title=r["title"], author=r["author"], language=r["language"],
            source_format=r["source_format"], locator=_locator(r),
            text=r["text"], path=r["path"],
        )
        for r in rows
    ]
    return sorted(hits, key=lambda h: -h.score)


def _locator(r) -> str:
    if r["page_start"] is not None:
        if r["page_end"] and r["page_end"] != r["page_start"]:
            return f"pp. {r['page_start']}–{r['page_end']}"
        return f"p. {r['page_start']}"
    if r["chapter_title"]:
        return r["chapter_title"]
    if r["chapter_idx"] is not None:
        return f"chapter {r['chapter_idx'] + 1}"
    return "text"


_cross_encoder = None


def _rerank(query: str, hits: list[Hit]) -> list[Hit]:
    global _cross_encoder
    if _cross_encoder is None:
        from sentence_transformers import CrossEncoder

        _cross_encoder = CrossEncoder(RERANK_MODEL)
    scores = _cross_encoder.predict([(query, h.text) for h in hits])
    for h, s in zip(hits, scores):
        h.score = float(s)
    return sorted(hits, key=lambda h: -h.score)
