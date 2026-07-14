from pathlib import Path

from kontext.survey.probes import probe_file

from conftest import (
    make_epub, make_mobi, make_plain_zip_with_ebooks, make_scanned_pdf,
    make_text_pdf, make_vector_page_pdf,
)


def probe(path: Path):
    return probe_file(path, path.stat().st_size)


def test_text_pdf(tmp_path):
    make_text_pdf(tmp_path / "book.pdf", pages=4)
    r = probe(tmp_path / "book.pdf")
    assert r.format == "pdf"
    assert r.status == "ok"
    assert r.text_class == "text"
    assert r.pages == 4
    assert r.word_estimate > 200
    assert r.language == "en"


def test_scanned_pdf(tmp_path):
    make_scanned_pdf(tmp_path / "scan.pdf", pages=3)
    r = probe(tmp_path / "scan.pdf")
    assert r.status == "ok"
    assert r.text_class == "scanned"
    assert r.pages == 3
    assert r.word_estimate == 0


def test_vector_page_pdf_needs_ocr(tmp_path):
    make_vector_page_pdf(tmp_path / "rendered.pdf", pages=2)
    r = probe(tmp_path / "rendered.pdf")
    assert r.status == "ok"
    assert r.text_class == "scanned"  # heavy textless pages -> ocr scope, not "empty"


def test_truly_blank_pdf_is_empty(tmp_path):
    doc = __import__("pymupdf").open()
    doc.new_page()
    doc.save(tmp_path / "blank.pdf")
    doc.close()
    r = probe(tmp_path / "blank.pdf")
    assert r.text_class == "empty"


def test_doc_formats_are_surfaced(tmp_path):
    (tmp_path / "thesis.doc").write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 128)
    r = probe(tmp_path / "thesis.doc")
    assert r.format == "doc"
    assert r.status == "unsupported"


def test_corrupt_pdf(tmp_path):
    (tmp_path / "bad.pdf").write_bytes(b"%PDF-1.4 then it all goes wrong")
    r = probe(tmp_path / "bad.pdf")
    assert r.format == "pdf"
    assert r.status == "corrupt"


def test_epub_metadata_and_language(tmp_path):
    make_epub(tmp_path / "b.epub", title="Der Prozess", author="F. Kafka", language="de-DE")
    r = probe(tmp_path / "b.epub")
    assert r.format == "epub"
    assert r.status == "ok"
    assert r.title == "Der Prozess"
    assert r.author == "F. Kafka"
    assert r.language == "de"  # normalized from de-DE
    assert r.text_class == "text"
    assert r.word_estimate > 100


def test_epub_drm(tmp_path):
    make_epub(tmp_path / "locked.epub", drm=True)
    r = probe(tmp_path / "locked.epub")
    assert r.status == "drm"


def test_epub_font_obfuscation_is_not_drm(tmp_path):
    make_epub(tmp_path / "fonts.epub", font_obfuscation_only=True)
    r = probe(tmp_path / "fonts.epub")
    assert r.status == "ok"


def test_epub_detected_by_magic_despite_wrong_extension(tmp_path):
    make_epub(tmp_path / "renamed.zip")
    r = probe(tmp_path / "renamed.zip")
    assert r.format == "epub"
    assert r.status == "ok"


def test_mobi(tmp_path):
    make_mobi(tmp_path / "k.mobi", title="Palm Sized Stories", text_length=120_000)
    r = probe(tmp_path / "k.mobi")
    assert r.format == "mobi"
    assert r.status == "ok"
    assert r.title == "Palm Sized Stories"
    assert r.language == "en"
    assert r.word_estimate == 20_000  # 120000 chars / 6


def test_mobi_drm(tmp_path):
    make_mobi(tmp_path / "locked.azw3", encryption=2)
    r = probe(tmp_path / "locked.azw3")
    assert r.format == "mobi"
    assert r.status == "drm"


def test_cbr_is_comic_not_archive(tmp_path):
    (tmp_path / "issue 01.cbr").write_bytes(b"Rar!\x1a\x07\x00" + b"\x00" * 64)
    r = probe(tmp_path / "issue 01.cbr")
    assert r.format == "comic"
    assert r.status == "unsupported"


def test_plain_zip_reports_contained_ebooks(tmp_path):
    make_plain_zip_with_ebooks(tmp_path / "bundle.zip")
    r = probe(tmp_path / "bundle.zip")
    assert r.format == "zip"
    assert r.status == "unsupported"
    assert "epub" in (r.contains or "")


def test_txt(tmp_path, request):
    text = "word " * 3000
    (tmp_path / "notes.txt").write_text(text)
    r = probe(tmp_path / "notes.txt")
    assert r.format == "txt"
    assert r.status == "ok"
    assert r.text_class == "text"
    assert r.word_estimate > 1000


def test_empty_file(tmp_path):
    (tmp_path / "zero.pdf").touch()
    r = probe(tmp_path / "zero.pdf")
    assert r.status == "corrupt"
