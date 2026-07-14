import numpy as np
import pytest

from kontext import catalog
from kontext.model import Block, Extraction
from kontext.search.chunker import (
    CHUNK_MAX_WORDS, CHUNK_WORDS, OVERLAP_WORDS, chunk_book,
)
from kontext.search.embedder import run_embed
from kontext.search.query import lexical_search, rrf_fuse, search

from conftest import ENGLISH, SECOND_TEXT


def pdf_blocks(pages: int, sentences_per_page: int = 30) -> list[Block]:
    text = " ".join("The quick brown fox jumps over the lazy dog again." for _ in range(sentences_per_page))
    return [Block(seq=i, text=text, word_count=len(text.split()), page=i + 1)
            for i in range(pages)]


def epub_blocks(chapters: int, paras: int = 12) -> list[Block]:
    blocks = []
    seq = 0
    for ci in range(chapters):
        offset = 0
        for _ in range(paras):
            blocks.append(Block(seq=seq, text=ENGLISH.strip(), word_count=len(ENGLISH.split()),
                                chapter_idx=ci, chapter_title=f"Chapter {ci + 1}",
                                char_offset=offset))
            offset += len(ENGLISH)
            seq += 1
    return blocks


def test_chunks_have_sane_sizes_and_seq():
    chunks = chunk_book(pdf_blocks(pages=6))
    assert [c["seq"] for c in chunks] == list(range(len(chunks)))
    # all but the last chunk should be at least the target size
    assert all(c["word_count"] >= CHUNK_WORDS * 0.5 for c in chunks[:-1])
    assert all(c["word_count"] <= CHUNK_MAX_WORDS for c in chunks)


def test_pdf_chunks_carry_page_spans():
    chunks = chunk_book(pdf_blocks(pages=6))
    assert chunks[0]["page_start"] == 1
    assert all(c["page_start"] <= c["page_end"] for c in chunks)
    assert chunks[-1]["page_end"] == 6


def test_chunks_overlap_within_chapter():
    chunks = chunk_book(pdf_blocks(pages=4))
    assert len(chunks) >= 2
    tail = " ".join(chunks[0]["text"].split()[-10:])
    assert tail in chunks[1]["text"]


def test_chunks_never_cross_chapters():
    chunks = chunk_book(epub_blocks(chapters=3))
    for c in chunks:
        assert c["chapter_idx"] is not None
    # every chapter contributes at least one chunk of only its own material
    assert {c["chapter_idx"] for c in chunks} == {0, 1, 2}
    assert all(c["chapter_title"] == f"Chapter {c['chapter_idx'] + 1}" for c in chunks)


def test_giant_sentence_becomes_own_chunk():
    words = " ".join(["word"] * (CHUNK_MAX_WORDS + 100))
    blocks = [Block(seq=0, text=words, word_count=CHUNK_MAX_WORDS + 100, page=1)]
    chunks = chunk_book(blocks)
    assert len(chunks) == 1  # unsplittable atom is accepted oversize


def test_giant_sentence_mid_text_terminates():
    # regression: an unpunctuated >400-word atom arriving while a chunk is
    # accumulating used to flush/reseed/retry forever (oom on real books)
    normal = "A short and ordinary sentence about books. " * 20
    giant = " ".join(["ocrjunk"] * (CHUNK_MAX_WORDS + 50))
    blocks = [
        Block(seq=0, text=normal.strip(), word_count=len(normal.split()), page=1),
        Block(seq=1, text=giant, word_count=CHUNK_MAX_WORDS + 50, page=2),
        Block(seq=2, text=normal.strip(), word_count=len(normal.split()), page=3),
    ]
    chunks = chunk_book(blocks)
    assert any(c["word_count"] > CHUNK_MAX_WORDS for c in chunks)  # the giant, alone
    assert len(chunks) < 10  # and nothing repeated endlessly


@pytest.fixture
def indexed_catalog(tmp_path):
    conn = catalog.connect(tmp_path / "kontext.db")
    for title, text in [("Fox Book", "The quick brown fox jumps over the lazy dog. " * 40),
                        ("Garden Book", SECOND_TEXT * 6)]:
        ext = Extraction(status="extracted", title=title, source_format="epub")
        ext.blocks = [Block(seq=0, text=text, word_count=len(text.split()),
                            chapter_idx=0, chapter_title="One", char_offset=0)]
        book_id = catalog.insert_book(conn, ext, files=[{
            "sha256": title, "path": f"/dump/{title}.epub", "format": "epub",
            "size": 1, "role": "primary"}])
        catalog.insert_chunks(conn, book_id, chunk_book(ext.blocks))
    return conn


def test_lexical_search_finds_terms(indexed_catalog):
    ids = lexical_search(indexed_catalog, "brown fox jumping", limit=5)
    assert ids
    text = indexed_catalog.execute(
        "SELECT text FROM chunks WHERE id=?", (ids[0],)).fetchone()[0]
    assert "fox" in text


def test_rrf_prefers_agreement():
    scores = rrf_fuse([[1, 2, 3], [2, 9, 1]])
    assert scores[2] > scores[9]
    assert scores[1] > scores[3]


class FakeStore:
    """dense side stub: returns whatever ids it was primed with."""
    def __init__(self, ids):
        self.ids = ids
        self.upserted: list[int] = []

    def search(self, vector, limit):
        return [(i, 0.9) for i in self.ids[:limit]]

    def upsert(self, ids, vectors, book_ids):
        assert len(ids) == len(vectors) == len(book_ids)
        self.upserted.extend(ids)


class FakeEmbedder:
    def encode(self, texts):
        return np.zeros((len(texts), 4), dtype=np.float32)


def test_hybrid_search_end_to_end(indexed_catalog):
    garden_ids = lexical_search(indexed_catalog, "gardens seasons soil", limit=3)
    hits = search(indexed_catalog, FakeStore(garden_ids), FakeEmbedder(),
                  "gardens change with the seasons", limit=3)
    assert hits
    assert hits[0].title == "Garden Book"
    assert hits[0].locator == "One"
    assert hits[0].path == "/dump/Garden Book.epub"


def test_run_embed_is_resumable(indexed_catalog):
    store = FakeStore([])
    total = indexed_catalog.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    done = run_embed(indexed_catalog, store, FakeEmbedder().encode, batch=2)
    assert done == total
    assert len(store.upserted) == total
    # second run: nothing left
    assert run_embed(indexed_catalog, store, FakeEmbedder().encode, batch=2) == 0
