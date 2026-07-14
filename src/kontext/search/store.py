"""qdrant vector store: one collection, int8-quantized, disk-backed."""

from __future__ import annotations

import numpy as np

COLLECTION = "kontext"
DEFAULT_URL = "http://localhost:6333"

START_HINT = (
    "qdrant is not reachable -- start it with:\n"
    "  docker run -d --name qdrant -p 6333:6333 "
    "-v $(pwd)/qdrant_storage:/qdrant/storage qdrant/qdrant"
)


class VectorStore:
    def __init__(self, url: str = DEFAULT_URL, dim: int = 1024):
        from qdrant_client import QdrantClient

        self.client = QdrantClient(url=url, timeout=60)
        self.dim = dim

    def ensure_collection(self) -> None:
        from qdrant_client import models

        if self.client.collection_exists(COLLECTION):
            return
        self.client.create_collection(
            COLLECTION,
            vectors_config=models.VectorParams(
                size=self.dim, distance=models.Distance.COSINE, on_disk=True,
            ),
            quantization_config=models.ScalarQuantization(
                scalar=models.ScalarQuantizationConfig(
                    type=models.ScalarType.INT8, always_ram=True,
                ),
            ),
        )

    def upsert(self, ids: list[int], vectors: np.ndarray, book_ids: list[int]) -> None:
        from qdrant_client import models

        self.client.upsert(
            COLLECTION,
            points=models.Batch(
                ids=ids,
                vectors=vectors.tolist(),
                payloads=[{"book_id": b} for b in book_ids],
            ),
            wait=True,
        )

    def search(self, vector: np.ndarray, limit: int) -> list[tuple[int, float]]:
        hits = self.client.query_points(COLLECTION, query=vector.tolist(), limit=limit)
        return [(int(p.id), float(p.score)) for p in hits.points]

    def count(self) -> int:
        return self.client.count(COLLECTION).count
