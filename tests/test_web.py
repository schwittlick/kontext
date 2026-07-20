import numpy as np
import pytest

from kontext import catalog
from kontext.model import Block, Extraction
from kontext.search.chunker import chunk_book
from kontext.search.query import lexical_search

from conftest import SECOND_TEXT

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from starlette.testclient import TestClient      # noqa: E402

from kontext.web import create_app                # noqa: E402


class FakeStore:
    def __init__(self, ids):
        self.ids = ids

    def search(self, vector, limit):
        return [(i, 0.9) for i in self.ids[:limit]]


class FakeEmbedder:
    def encode(self, texts):
        return np.zeros((len(texts), 4), dtype=np.float32)


@pytest.fixture
def served(tmp_path):
    """an app wired to a tiny indexed catalog, with real+missing files so
    the download endpoint can be exercised both ways. models are faked, so
    no torch/qdrant is touched."""
    db = tmp_path / "kontext.db"
    conn = catalog.connect(db)
    real = tmp_path / "garden.epub"
    real.write_bytes(b"PK\x03\x04 pretend epub bytes")
    paths = {"Garden Book": str(real), "Fox Book": str(tmp_path / "gone.epub")}
    ids = {}
    for title, text in [("Garden Book", SECOND_TEXT * 6),
                        ("Fox Book", "The quick brown fox. " * 40)]:
        ext = Extraction(status="extracted", title=title, source_format="epub")
        ext.blocks = [Block(seq=0, text=text, word_count=len(text.split()),
                            chapter_idx=0, chapter_title="One")]
        ids[title] = catalog.insert_book(conn, ext, files=[{
            "sha256": title, "path": paths[title], "format": "epub",
            "size": 1, "role": "primary"}])
        catalog.insert_chunks(conn, ids[title], chunk_book(ext.blocks))
    garden_ids = lexical_search(conn, "gardens seasons soil accounting", 5)
    conn.close()

    app = create_app(db, embedder=FakeEmbedder(), store=FakeStore(garden_ids))
    return {"app": app, "real": real, "ids": ids}


def test_home_renders_highlighted_results(served):
    with TestClient(served["app"]) as client:
        r = client.get("/", params={"q": "gardens seasons soil"})
    assert r.status_code == 200
    assert "Garden Book" in r.text
    assert "<mark>gardens</mark>" in r.text.lower() or "<mark>seasons</mark>" in r.text.lower()
    assert f'/download/{served["ids"]["Garden Book"]}' in r.text


def test_home_without_query_shows_no_results(served):
    with TestClient(served["app"]) as client:
        r = client.get("/")
    assert r.status_code == 200
    assert "passage" not in r.text          # no result/"no passages" block rendered


def test_api_search_returns_json(served):
    with TestClient(served["app"]) as client:
        r = client.get("/api/search", params={"q": "gardens seasons soil"})
    assert r.status_code == 200
    data = r.json()
    assert data["results"]
    top = data["results"][0]
    assert top["title"] == "Garden Book"
    assert top["download"] == f"/download/{served['ids']['Garden Book']}"


def test_api_search_empty_query_is_noop(served):
    with TestClient(served["app"]) as client:
        r = client.get("/api/search", params={"q": "   "})
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_download_streams_the_original(served):
    with TestClient(served["app"]) as client:
        r = client.get(f"/download/{served['ids']['Garden Book']}")
    assert r.status_code == 200
    assert r.content == served["real"].read_bytes()


def test_download_404_when_file_absent(served):
    with TestClient(served["app"]) as client:
        r = client.get(f"/download/{served['ids']['Fox Book']}")
    assert r.status_code == 404
