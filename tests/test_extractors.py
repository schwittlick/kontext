from kontext.ingest.clean import clean_page_text, quality_score, split_paragraphs
from kontext.ingest.extractors import (
    convert_and_extract, extract_epub, extract_exploded_html, extract_pdf,
    extract_txt, html_blocks,
)

from conftest import ENGLISH, make_epub, make_exploded_html_book, make_text_pdf


def test_clean_rejoins_hyphenated_linebreaks():
    assert clean_page_text("a beauti-\nful day\nindeed") == "a beautiful day indeed"


def test_split_paragraphs():
    assert split_paragraphs("one\ntwo\n\nthree\n\n\n four ") == ["one two", "three", "four"]


def test_quality_score_separates_prose_from_garbage():
    assert quality_score(ENGLISH) > 0.95
    assert quality_score("\x00\x01\x02" * 100 + "ok") < 0.1


def test_html_blocks_paragraphs_heading_and_skips():
    raw = """<html><head><title>x</title><style>p{}</style></head>
    <body><script>var a=1;</script><h1>The Real Heading</h1>
    <p>first paragraph</p><div>second <b>bold</b> piece</div></body></html>"""
    blocks, heading = html_blocks(raw)
    assert heading == "The Real Heading"
    assert "first paragraph" in blocks
    assert "second bold piece" in blocks
    assert not any("var a" in b for b in blocks)


def test_pdf_blocks_are_pages_with_locators(tmp_path):
    make_text_pdf(tmp_path / "b.pdf", pages=4)
    ext = extract_pdf(str(tmp_path / "b.pdf"))
    assert ext.status == "extracted"
    assert [b.page for b in ext.blocks] == [1, 2, 3, 4]
    assert all(b.word_count > 50 for b in ext.blocks)
    assert ext.blocks[0].locator() == "p. 1"


def test_epub_follows_spine_order_not_filename_order(tmp_path):
    make_epub(tmp_path / "b.epub", chapter_names=["zz_intro.xhtml", "aa_end.xhtml"])
    ext = extract_epub(str(tmp_path / "b.epub"))
    assert ext.status == "extracted"
    assert ext.title == "A Study In Retrieval"
    assert ext.author == "Jane Doe"
    assert ext.language == "en"
    first_chapter = [b for b in ext.blocks if b.chapter_idx == 0]
    assert any("chapter marker 0" in b.text for b in first_chapter)  # zz_intro is first
    assert first_chapter[0].chapter_title == "Chapter 1"
    offsets = [b.char_offset for b in first_chapter]
    assert offsets == sorted(offsets) and offsets[0] == 0


def test_txt_paragraph_offsets(tmp_path):
    (tmp_path / "n.txt").write_text("para one here\n\npara two follows\n\npara three ends")
    ext = extract_txt(str(tmp_path / "n.txt"))
    assert [b.text for b in ext.blocks] == ["para one here", "para two follows", "para three ends"]
    assert ext.blocks[0].locator() == "text"


def test_exploded_html_is_one_book_with_chapters(tmp_path):
    d = make_exploded_html_book(tmp_path, chapters=8)
    paths = sorted(str(p) for p in d.glob("*.html"))
    ext = extract_exploded_html(paths)
    assert ext.status == "extracted"
    assert {b.chapter_idx for b in ext.blocks} == set(range(8))
    ch3 = [b for b in ext.blocks if b.chapter_idx == 2]
    assert ch3[0].chapter_title == "Part 3"
    seqs = [b.seq for b in ext.blocks]
    assert seqs == list(range(len(ext.blocks)))


def test_conversion_without_tools_waits_gracefully(tmp_path, monkeypatch):
    monkeypatch.setattr("kontext.ingest.extractors.shutil.which", lambda _: None)
    (tmp_path / "old.doc").write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
    ext = convert_and_extract(str(tmp_path / "old.doc"), "doc")
    assert ext.status == "needs_conversion"
    assert "libreoffice" in ext.error

    (tmp_path / "k.mobi").write_bytes(b"\x00" * 100)
    ext = convert_and_extract(str(tmp_path / "k.mobi"), "mobi")
    assert ext.status == "needs_conversion"
    assert "calibre" in ext.error
