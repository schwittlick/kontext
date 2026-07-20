import numpy as np
import pytest

from kontext import catalog
from kontext.model import Block, Extraction
from kontext.search.chunker import chunk_book
from kontext.search.eval import (
    GoldenCase, _is_correct, evaluate, load_golden,
)
from kontext.search.query import Hit, lexical_search

from conftest import ENGLISH, SECOND_TEXT


class FakeStore:
    """dense side stub: returns whatever chunk ids it was primed with."""
    def __init__(self, ids):
        self.ids = ids

    def search(self, vector, limit):
        return [(i, 0.9) for i in self.ids[:limit]]


class FakeEmbedder:
    def encode(self, texts):
        return np.zeros((len(texts), 4), dtype=np.float32)


@pytest.fixture
def indexed(tmp_path):
    conn = catalog.connect(tmp_path / "kontext.db")
    for title, author, text in [
        ("Fox Book", "A. Vulpes", "The quick brown fox jumps over the lazy dog. " * 40),
        ("Garden Book", "V. Sackville-West", SECOND_TEXT * 6),
    ]:
        ext = Extraction(status="extracted", title=title, author=author, source_format="epub")
        ext.blocks = [Block(seq=0, text=text, word_count=len(text.split()),
                            chapter_idx=0, chapter_title="One", char_offset=0)]
        book_id = catalog.insert_book(conn, ext, files=[{
            "sha256": title, "path": f"/dump/{title}.epub", "format": "epub",
            "size": 1, "role": "primary"}])
        catalog.insert_chunks(conn, book_id, chunk_book(ext.blocks))
    return conn


def _hit(book_id=1, title="Garden Book", author="V. Sackville-West", text="x"):
    return Hit(chunk_id=1, book_id=book_id, score=1.0, title=title, author=author,
               language="en", source_format="epub", locator="One", text=text, path=None)


def test_load_golden(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text(
        "- query: how do gardens change\n"
        "  book: Garden Book\n"
        "  must_contain: season of accounting\n"
        "- query: a fox\n"
        "  book: 7\n"
    )
    cases = load_golden(p)
    assert [c.query for c in cases] == ["how do gardens change", "a fox"]
    assert cases[0].must_contain == "season of accounting"
    assert cases[1].book == 7 and cases[1].must_contain is None


def test_load_golden_rejects_incomplete(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text("- query: no book here\n")
    with pytest.raises(ValueError):
        load_golden(p)


def test_is_correct_book_by_substring_and_id():
    h = _hit(book_id=3, title="Garden Book", author="V. Sackville-West")
    assert _is_correct(h, GoldenCase(query="q", book="garden"))       # title substring
    assert _is_correct(h, GoldenCase(query="q", book="sackville"))    # author substring
    assert _is_correct(h, GoldenCase(query="q", book=3))              # numeric id
    assert not _is_correct(h, GoldenCase(query="q", book="fox"))


def test_is_correct_must_contain_normalizes():
    h = _hit(text="...Autumn is the SEASON   of accounting, when seed is gathered.")
    assert _is_correct(h, GoldenCase(query="q", book="garden",
                                     must_contain="season of accounting"))
    assert not _is_correct(h, GoldenCase(query="q", book="garden",
                                         must_contain="spring planting"))


def test_evaluate_scores_hit_miss_and_unreachable(indexed):
    # a query with no words in common with the Fox book, so its only honest
    # answers are Garden chunks (both dense and lexical agree on Garden)
    store = FakeStore(lexical_search(indexed, "gardens seasons soil accounting", 5))
    cases = [
        # hit: right book, phrase present -> rank 1
        GoldenCase(query="gardens seasons soil", book="Garden",
                   must_contain="autumn is the season of accounting"),
        # reachable miss: Fox is indexed but never retrieved for this query
        GoldenCase(query="gardens seasons soil", book="Fox Book"),
        # unreachable: phrase is nowhere in the corpus
        GoldenCase(query="gardens seasons soil", book="Garden",
                   must_contain="this phrase appears in no chunk at all"),
    ]
    report = evaluate(indexed, store, FakeEmbedder(), cases, k=10)

    assert report.results[0].rank == 1
    assert report.results[1].rank is None and report.results[1].reachable
    assert report.results[2].rank is None and not report.results[2].reachable

    assert report.total == 3
    assert report.hits == 1
    assert report.hit_rate == pytest.approx(1 / 3)
    assert report.mrr == pytest.approx(1 / 3)   # (1/1 + 0 + 0) / 3
    assert [r.case for r in report.unreachable] == [cases[2]]


def test_evaluate_unreachable_when_book_absent(indexed):
    store = FakeStore(lexical_search(indexed, "fox", 5))
    report = evaluate(indexed, store, FakeEmbedder(),
                      [GoldenCase(query="anything", book="No Such Title")], k=10)
    assert not report.results[0].reachable
