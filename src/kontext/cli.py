"""kontext command line interface."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from kontext.survey import db as survey_dbmod
from kontext.survey.report import build_report, render_report, write_json
from kontext.survey.run import run_survey

app = typer.Typer(add_completion=False, no_args_is_help=True, help="find context in a personal ebook library")
console = Console()

_DEFAULT_WORKERS = max(1, (os.cpu_count() or 4) - 1)


@app.command()
def survey(
    root: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="root of the ebook dump (read-only)")],
    db: Annotated[Path, typer.Option(help="manifest database to create/update")] = Path("survey.db"),
    json_out: Annotated[Path, typer.Option(help="where to write the report json")] = Path("survey_report.json"),
    workers: Annotated[int, typer.Option(min=1, help="parallel probe processes")] = _DEFAULT_WORKERS,
    sample: Annotated[Optional[int], typer.Option(min=1, help="probe only a random sample of N files (quick look)")] = None,
    follow_symlinks: Annotated[bool, typer.Option(help="follow symlinks while walking")] = False,
    reprobe: Annotated[bool, typer.Option(help="re-probe files even if unchanged since the last run")] = False,
) -> None:
    """walk the dump, probe every file, build the manifest + report.

    resumable: re-runs skip files whose size+mtime are unchanged, so an
    interrupted survey continues where it stopped.
    """
    run_survey(
        root, db, json_out, workers, console,
        sample=sample, follow_symlinks=follow_symlinks, reprobe=reprobe,
        show_report=True,
    )
    console.print(f"manifest: [bold]{db}[/bold] | report json: [bold]{json_out}[/bold]")


@app.command()
def report(
    db: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="manifest database from `kontext survey`")] = Path("survey.db"),
    json_out: Annotated[Path, typer.Option(help="where to write the report json")] = Path("survey_report.json"),
    embed_rate: Annotated[Optional[float], typer.Option(help="override embedding throughput (chunks/s)")] = None,
    ocr_rate: Annotated[Optional[float], typer.Option(help="override ocr throughput (pages/s)")] = None,
) -> None:
    """re-render the report from an existing manifest (no probing)."""
    conn = survey_dbmod.connect(db)
    rep = build_report(conn, embed_rate=embed_rate, ocr_rate=ocr_rate)
    write_json(rep, json_out)
    render_report(rep, console)
    console.print(f"report json: [bold]{json_out}[/bold]")


@app.command()
def ingest(
    root: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="root of the ebook dump (read-only)")],
    db: Annotated[Path, typer.Option(help="catalog database (books + extracted text)")] = Path("kontext.db"),
    survey_db: Annotated[Path, typer.Option(help="survey manifest database")] = Path("survey.db"),
    workers: Annotated[int, typer.Option(min=1, help="parallel extraction processes")] = _DEFAULT_WORKERS,
    no_survey: Annotated[bool, typer.Option(help="skip the survey refresh, use the manifest as-is")] = False,
) -> None:
    """extract text from every usable book into the catalog (phase 1).

    refreshes the survey first (incremental), then extracts new files only:
    re-running after more downloads picks up just the additions. scanned
    books are registered as awaiting_ocr; mobi/doc files convert when
    calibre/libreoffice are installed, otherwise they wait as
    needs_conversion.
    """
    from kontext.ingest.runner import run_ingest

    if not no_survey:
        run_survey(root, survey_db, None, workers, console, show_report=False)
    elif not survey_db.exists():
        raise typer.BadParameter(f"survey manifest {survey_db} not found; run `kontext survey` first")
    run_ingest(survey_db, db, workers, console)
    console.print(f"catalog: [bold]{db}[/bold]")


@app.command()
def books(
    db: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="catalog database")] = Path("kontext.db"),
    status: Annotated[Optional[str], typer.Option(help="filter: extracted | awaiting_ocr | needs_conversion")] = None,
    search: Annotated[Optional[str], typer.Option(help="substring match on title/author")] = None,
    limit: Annotated[int, typer.Option(min=1)] = 40,
) -> None:
    """list works in the catalog."""
    from kontext import catalog

    conn = catalog.connect(db)
    q = ("SELECT b.id, b.title, b.author, b.language, b.source_format, b.status,"
         " b.word_count, COUNT(f.sha256) AS nfiles"
         " FROM books b LEFT JOIN book_files f ON f.book_id = b.id WHERE 1=1")
    params: list = []
    if status:
        q += " AND b.status = ?"
        params.append(status)
    if search:
        q += " AND (b.title LIKE ? OR b.author LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    q += " GROUP BY b.id ORDER BY b.id LIMIT ?"
    params.append(limit)

    t = Table(title="catalog", title_justify="left")
    for col, justify in [("id", "right"), ("title", "left"), ("author", "left"),
                         ("lang", "left"), ("fmt", "left"), ("status", "left"),
                         ("words", "right"), ("files", "right")]:
        t.add_column(col, justify=justify)
    for r in conn.execute(q, params):
        t.add_row(
            str(r["id"]), (r["title"] or "?")[:48], (r["author"] or "")[:24],
            r["language"] or "?", r["source_format"] or "?", r["status"],
            f"{r['word_count']:,}", str(r["nfiles"]),
        )
    console.print(t)


@app.command()
def show(
    book_id: Annotated[int, typer.Argument(help="book id from `kontext books`")],
    db: Annotated[Path, typer.Option(exists=True, dir_okay=False, help="catalog database")] = Path("kontext.db"),
    blocks: Annotated[int, typer.Option(min=0, help="how many blocks to preview")] = 3,
) -> None:
    """inspect one work: metadata, files, first text blocks."""
    from kontext import catalog

    conn = catalog.connect(db)
    b = conn.execute("SELECT * FROM books WHERE id=?", (book_id,)).fetchone()
    if b is None:
        console.print(f"[red]no book with id {book_id}[/red]")
        raise typer.Exit(1)
    console.print(f"[bold]{b['title'] or '?'}[/bold] — {b['author'] or 'unknown'}")
    console.print(
        f"id {b['id']} | {b['language'] or '?'} | {b['source_format']} | {b['status']}"
        f" | {b['word_count']:,} words in {b['block_count']:,} blocks"
        f" | quality {b['quality'] if b['quality'] is not None else '?'}"
    )
    for f in conn.execute("SELECT * FROM book_files WHERE book_id=? ORDER BY role", (book_id,)):
        console.print(f"  [dim]{f['role']}:[/dim] {f['path']}")
    if blocks:
        console.print()
        for blk in conn.execute(
            "SELECT * FROM blocks WHERE book_id=? ORDER BY seq LIMIT ?", (book_id, blocks)
        ):
            loc = f"p. {blk['page']}" if blk["page"] else (blk["chapter_title"] or "text")
            console.print(f"[dim]({loc})[/dim] {blk['text'][:300]}")


if __name__ == "__main__":
    app()
