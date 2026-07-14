"""dense embeddings: bge-m3 via sentence-transformers, gpu when present.

the lexical half of hybrid search is sqlite fts5 (bm25) over the same
chunks -- bge-m3's own sparse vectors would need the flagembedding
library; fts5 gives the exact-term signal with zero extra dependencies.
"""

from __future__ import annotations

import sqlite3
from typing import Callable

import numpy as np

EMBED_MODEL = "BAAI/bge-m3"
EMBED_DIM = 1024
# bge-m3 asymmetric usage: queries get an instruction-free prefix-less encode,
# passages are encoded as-is; chunk texts are capped well under this
MAX_TOKENS = 1024


class Embedder:
    """lazy model wrapper so `kontext chunk`/cli --help never load torch."""

    def __init__(self, device: str | None = None, batch_size: int | None = None):
        self._model = None
        self._device = device
        self.batch_size = batch_size

    def _load(self):
        if self._model is None:
            import torch
            from sentence_transformers import SentenceTransformer

            device = self._device or ("cuda" if torch.cuda.is_available() else "cpu")
            if self.batch_size is None:
                self.batch_size = 64 if device == "cuda" else 8
            self._model = SentenceTransformer(EMBED_MODEL, device=device)
            self._model.max_seq_length = MAX_TOKENS
        return self._model

    @property
    def device(self) -> str:
        return str(self._load().device)

    def encode(self, texts: list[str]) -> np.ndarray:
        model = self._load()
        return model.encode(
            texts, batch_size=self.batch_size, normalize_embeddings=True,
            convert_to_numpy=True, show_progress_bar=False,
        ).astype(np.float32)


def run_embed(
    conn: sqlite3.Connection,
    store,  # kontext.search.store.VectorStore
    encode: Callable[[list[str]], np.ndarray],
    batch: int = 256,
    on_progress: Callable[[int], None] | None = None,
) -> int:
    """embed every chunk not yet in the vector store; resumable, a chunk is
    marked embedded only after qdrant accepted it."""
    from kontext import catalog

    done = 0
    while True:
        rows = conn.execute(
            "SELECT c.id, c.text, c.book_id FROM chunks c WHERE c.embedded=0"
            " ORDER BY c.id LIMIT ?", (batch,)
        ).fetchall()
        if not rows:
            return done
        ids = [r[0] for r in rows]
        vectors = encode([r[1] for r in rows])
        store.upsert(ids, vectors, [r[2] for r in rows])
        catalog.mark_embedded(conn, ids)
        done += len(ids)
        if on_progress:
            on_progress(len(ids))
