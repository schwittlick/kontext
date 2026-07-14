from kontext.ingest.dedup import find_merges, signature_bytes

from conftest import ENGLISH, SECOND_TEXT

BOOK_A = [ENGLISH] * 8            # ~700 words
BOOK_A_VARIANT = [ENGLISH] * 8 + ["Retail edition. Published 2011."]
BOOK_B = [SECOND_TEXT] * 8


def test_same_text_different_edition_merges():
    books = [
        (1, signature_bytes(BOOK_A), "epub", 700),
        (2, signature_bytes(BOOK_A_VARIANT), "pdf", 705),
        (3, signature_bytes(BOOK_B), "epub", 700),
    ]
    merges = find_merges(books, new_ids={2, 3})
    # epub wins format priority -> book 1 is canonical, 2 folds into it, 3 untouched
    assert merges == [(1, 2)]


def test_no_new_books_no_merges():
    books = [
        (1, signature_bytes(BOOK_A), "epub", 700),
        (2, signature_bytes(BOOK_A), "pdf", 700),
    ]
    assert find_merges(books, new_ids=set()) == []


def test_three_way_group_single_canonical():
    books = [
        (1, signature_bytes(BOOK_A), "pdf", 700),
        (2, signature_bytes(BOOK_A), "epub", 700),
        (3, signature_bytes(BOOK_A_VARIANT), "txt", 700),
    ]
    merges = find_merges(books, new_ids={3})
    # 3 links the group; epub (id 2) is the best primary
    assert sorted(merges) == [(2, 1), (2, 3)]
