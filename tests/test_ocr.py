import shutil
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from kontext import catalog
from kontext.cli import app
from kontext.model import Block, Extraction
from kontext.ocr import engine

from conftest import THIRD_TEXT

runner = CliRunner()

needs_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None or "eng" not in engine.available_langs(),
    reason="tesseract with eng traineddata not installed",
)


def make_image_pdf(path: Path, text: str, pages: int = 2) -> None:
    """a real 'scan': pages are raster images of typeset text, no text layer."""
    src = pymupdf.open()
    for _ in range(pages):
        page = src.new_page()
        page.insert_textbox(pymupdf.Rect(60, 60, 540, 780), text * 2, fontsize=13)
    out = pymupdf.open()
    for page in src:
        pix = page.get_pixmap(dpi=150)
        dest = out.new_page(width=page.rect.width, height=page.rect.height)
        dest.insert_image(dest.rect, stream=pix.tobytes("png"))
    out.save(path)
    out.close()
    src.close()


def test_pick_lang_maps_and_falls_back():
    available = {"eng", "fra"}
    assert engine.pick_lang("fr", available) == "fra"
    assert engine.pick_lang("en", available) == "eng"
    assert engine.pick_lang("zh", available) == "eng"   # pack not installed
    assert engine.pick_lang("xx", available) == "eng"   # unknown code
    assert engine.pick_lang(None, available) == "eng"


def test_finish_ocr_promotes_book(tmp_path):
    conn = catalog.connect(tmp_path / "kontext.db")
    book_id = catalog.insert_book(
        conn,
        Extraction(status="awaiting_ocr", title="Old Scan", source_format="pdf"),
        [{"sha256": "aa" * 32, "path": "/dump/old scan.pdf", "format": "pdf",
          "size": 123, "role": "primary"}],
    )
    assert catalog.books_needing_chunks(conn) == []
    queue = catalog.books_awaiting_ocr(conn)
    assert [r["id"] for r in queue] == [book_id]
    assert queue[0]["path"] == "/dump/old scan.pdf"

    ext = Extraction(
        status="extracted", source_format="pdf", language="en", quality=0.95,
        blocks=[
            Block(seq=0, text=THIRD_TEXT, word_count=len(THIRD_TEXT.split()), page=1),
            Block(seq=1, text=THIRD_TEXT, word_count=len(THIRD_TEXT.split()), page=2),
        ],
    )
    # run twice: an interrupted-and-redone book must not duplicate blocks
    catalog.finish_ocr(conn, book_id, ext, minhash=None)
    catalog.finish_ocr(conn, book_id, ext, minhash=None)

    book = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    assert book["status"] == "extracted"
    assert book["needs_ocr"] == 0
    assert book["block_count"] == 2
    assert book["title"] == "Old Scan"  # registered metadata is kept
    pages = [r[0] for r in conn.execute(
        "SELECT page FROM blocks WHERE book_id=? ORDER BY seq", (book_id,))]
    assert pages == [1, 2]
    assert catalog.books_awaiting_ocr(conn) == []
    assert catalog.books_needing_chunks(conn) == [book_id]


@needs_tesseract
def test_ocr_pdf_reads_scanned_pages(tmp_path):
    pdf = tmp_path / "scan.pdf"
    make_image_pdf(pdf, THIRD_TEXT, pages=2)
    ext = engine.ocr_pdf(str(pdf), "eng")
    assert ext.status == "extracted"
    assert [b.page for b in ext.blocks] == [1, 2]
    text = " ".join(b.text for b in ext.blocks).lower()
    assert "debugging" in text
    assert "reproduce" in text


@needs_tesseract
def test_ocr_end_to_end_cli(tmp_path):
    dump = tmp_path / "dump"
    dump.mkdir()
    make_image_pdf(dump / "scanned essay.pdf", THIRD_TEXT, pages=2)
    db = tmp_path / "kontext.db"

    result = runner.invoke(app, [
        "ingest", str(dump),
        "--db", str(db), "--survey-db", str(tmp_path / "survey.db"),
    ])
    assert result.exit_code == 0, result.output
    conn = catalog.connect(db)
    assert conn.execute(
        "SELECT status FROM books"
    ).fetchone()[0] == "awaiting_ocr"
    conn.close()

    result = runner.invoke(app, ["ocr", "--db", str(db), "--workers", "2"])
    assert result.exit_code == 0, result.output
    assert "books ocr'd" in result.output

    conn = catalog.connect(db)
    book = conn.execute("SELECT * FROM books").fetchone()
    assert book["status"] == "extracted"
    assert book["language"] == "en"
    assert book["word_count"] > 100
    text = conn.execute(
        "SELECT text FROM blocks WHERE book_id=? AND seq=0", (book["id"],)
    ).fetchone()[0]
    assert "debugging" in text.lower()

    # a second run finds an empty queue
    result = runner.invoke(app, ["ocr", "--db", str(db)])
    assert result.exit_code == 0, result.output
    assert "queue is empty" in result.output
