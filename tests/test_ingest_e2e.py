from pathlib import Path

from typer.testing import CliRunner

from kontext import catalog
from kontext.cli import app

from conftest import (
    ENGLISH, FOURTH_TEXT, SECOND_TEXT, THIRD_TEXT, make_epub,
    make_exploded_html_book, make_scanned_pdf, make_text_pdf,
)

runner = CliRunner()


def build_dump(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    # every distinct work gets distinct text -- books sharing text would be
    # (correctly) merged by the work-level dedup
    make_text_pdf(root / "notes.pdf", pages=5, text=THIRD_TEXT)
    make_scanned_pdf(root / "archive scan.pdf", pages=4)
    make_epub(root / "same book.epub", title="Same Book")
    # same work, slightly extended text -> different sha256, high jaccard;
    # more words also makes it the deterministic canonical of the merged pair
    make_epub(
        root / "same book (retail).epub", title="Same Book (retail)",
        body_text=ENGLISH + "This retail edition includes a new afterword by the author. ",
    )
    make_epub(root / "other book.epub", title="Other Book", body_text=SECOND_TEXT)
    # short on purpose: stays under MIN_WORDS_FOR_DEDUP, so sharing text
    # with the epubs is harmless
    (root / "clippings.txt").write_text((ENGLISH + "\n\n") * 3)
    make_exploded_html_book(root, chapters=8, text=FOURTH_TEXT)
    (root / "broken.pdf").write_bytes(b"%PDF-1.4 not really")
    # exact byte duplicate
    (root / "other book copy.epub").write_bytes((root / "other book.epub").read_bytes())


def run_ingest(dump: Path, tmp: Path):
    result = runner.invoke(app, [
        "ingest", str(dump),
        "--db", str(tmp / "kontext.db"), "--survey-db", str(tmp / "survey.db"),
    ])
    assert result.exit_code == 0, result.output
    return result


def test_ingest_end_to_end(tmp_path):
    dump = tmp_path / "dump"
    build_dump(dump)
    out = run_ingest(dump, tmp_path)
    assert "exact_duplicate 1" in out.output

    conn = catalog.connect(tmp_path / "kontext.db")
    books = {r["title"]: dict(r) for r in conn.execute("SELECT * FROM books")}

    # the two editions merged into one work with both files; the longer
    # retail edition is the canonical text source
    assert "Same Book" not in books
    same = books["Same Book (retail)"]
    roles = {r[0] for r in conn.execute(
        "SELECT role FROM book_files WHERE book_id=?", (same["id"],))}
    assert roles == {"primary", "alternate_format"}

    # scanned pdf waits for ocr, with metadata but no text yet
    scan = books["archive scan"]
    assert scan["status"] == "awaiting_ocr"
    assert scan["block_count"] == 0

    # pdf blocks carry page locators
    pdf = books["notes"]
    pages = [r[0] for r in conn.execute(
        "SELECT page FROM blocks WHERE book_id=? ORDER BY seq", (pdf["id"],))]
    assert pages == [1, 2, 3, 4, 5]
    assert pdf["language"] == "en"
    assert pdf["quality"] > 0.9

    # exploded directory became exactly one book with 8 chapters
    treatise = books["An Exploded Treatise"]
    n_chapters = conn.execute(
        "SELECT COUNT(DISTINCT chapter_idx) FROM blocks WHERE book_id=?",
        (treatise["id"],)).fetchone()[0]
    assert n_chapters == 8
    member_files = conn.execute(
        "SELECT COUNT(*) FROM book_files WHERE book_id=?", (treatise["id"],)).fetchone()[0]
    assert member_files == 8

    # corrupt pdf never became a book
    assert "broken" not in books
    # works total: same book, other book, notes, scan, clippings, treatise
    assert len(books) == 6


def test_ingest_resume_adds_nothing(tmp_path):
    dump = tmp_path / "dump"
    build_dump(dump)
    run_ingest(dump, tmp_path)
    conn = catalog.connect(tmp_path / "kontext.db")
    before = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    conn.close()

    out = run_ingest(dump, tmp_path)
    assert "0 books to extract, 0 to register" in out.output
    conn = catalog.connect(tmp_path / "kontext.db")
    assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == before


def test_books_and_show_commands(tmp_path):
    dump = tmp_path / "dump"
    build_dump(dump)
    run_ingest(dump, tmp_path)

    result = runner.invoke(app, ["books", "--db", str(tmp_path / "kontext.db"), "--search", "Same"])
    assert result.exit_code == 0, result.output
    assert "Same Book" in result.output

    conn = catalog.connect(tmp_path / "kontext.db")
    book_id = conn.execute(
        "SELECT id FROM books WHERE title='Same Book (retail)'").fetchone()[0]
    result = runner.invoke(app, ["show", str(book_id), "--db", str(tmp_path / "kontext.db")])
    assert result.exit_code == 0, result.output
    assert "alternate_format" in result.output
