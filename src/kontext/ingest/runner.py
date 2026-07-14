"""ingest orchestration: plan -> extract in parallel -> catalog -> dedup."""

from __future__ import annotations

import shutil
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from kontext import catalog
from kontext.ingest import dedup, planner
from kontext.ingest.extractors import extract_task
from kontext.model import Extraction
from kontext.survey import db as survey_dbmod


def run_ingest(
    survey_db: Path,
    catalog_db: Path,
    workers: int,
    console: Console,
) -> dict:
    survey_conn = survey_dbmod.connect(survey_db)
    conn = catalog.connect(catalog_db)

    retry = _clear_retryable_conversions(conn)
    if retry:
        console.print(f"retrying {retry} previously unconvertible books (converter now installed)")

    tasks, skipped = planner.plan(survey_conn, catalog.known_hashes(conn))
    survey_conn.close()

    registers = [t for t in tasks if t["kind"] == "register"]
    extracts = [t for t in tasks if t["kind"] != "register"]
    console.print(
        f"{len(extracts):,} books to extract, {len(registers):,} to register "
        f"(awaiting ocr/conversion), skipped: "
        + (", ".join(f"{k} {v:,}" for k, v in skipped.items()) or "none")
    )

    counts: Counter = Counter()
    new_ids: set[int] = set()

    for t in registers:
        ext = Extraction(
            status=t["status"], title=t["title"], author=t["author"],
            language=t["language"], source_format=t["format"],
        )
        catalog.insert_book(conn, ext, t["files"])
        counts[t["status"]] += 1

    failures: list[tuple[str, str]] = []
    if extracts:
        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(), MofNCompleteColumn(),
            TimeElapsedColumn(), TimeRemainingColumn(),
            console=console,
        )
        with progress, ProcessPoolExecutor(max_workers=workers) as pool:
            bar = progress.add_task("extracting", total=len(extracts))
            futures = {pool.submit(extract_task, t): t for t in extracts}
            for fut in as_completed(futures):
                t = futures[fut]
                result = fut.result()
                ext: Extraction = result["extraction"]
                if ext.status == "failed":
                    counts["failed"] += 1
                    failures.append((t["paths"][0], ext.error or "?"))
                    catalog.log_error(
                        conn, t["paths"][0], t["files"][0]["sha256"],
                        stage="extract", error=ext.error or "?",
                    )
                else:
                    book_id = catalog.insert_book(conn, ext, t["files"], result["minhash"])
                    if ext.status == "extracted":
                        new_ids.add(book_id)
                    counts[ext.status] += 1
                    counts["words"] += ext.word_count
                    counts["blocks"] += len(ext.blocks)
                progress.advance(bar)

    merges = dedup.find_merges(
        catalog.load_signatures(conn, dedup.MIN_WORDS_FOR_DEDUP), new_ids
    )
    for canonical_id, alternate_id in merges:
        catalog.merge_books(conn, canonical_id, alternate_id)

    _summary(conn, console, counts, merges, failures)
    conn.close()
    return {"counts": counts, "merges": len(merges), "failures": failures}


def _clear_retryable_conversions(conn) -> int:
    """needs_conversion books whose converter is now installed get their
    placeholder rows dropped, so the planner re-plans them this run."""
    cleared = 0
    for fmt, available in (
        ("mobi", shutil.which("ebook-convert") is not None),
        ("doc", (shutil.which("soffice") or shutil.which("libreoffice")) is not None),
    ):
        if available:
            # chm stays out: no installed tool ever handles it, retrying loops
            cur = conn.execute(
                "DELETE FROM books WHERE status='needs_conversion' AND source_format=?"
                " AND id NOT IN (SELECT book_id FROM book_files WHERE lower(path) LIKE '%.chm')",
                (fmt,),
            )
            cleared += cur.rowcount
    conn.commit()
    return cleared


def _summary(conn, console: Console, counts: Counter,
             merges: list, failures: list[tuple[str, str]]) -> None:
    t = Table(title="ingest result", title_justify="left")
    t.add_column("what")
    t.add_column("value", justify="right")
    t.add_row("books extracted", f"{counts['extracted']:,}")
    t.add_row("words / blocks", f"{counts['words']:,} / {counts['blocks']:,}")
    t.add_row("awaiting ocr (phase 4)", f"{counts['awaiting_ocr']:,}")
    t.add_row("need conversion (calibre/libreoffice)", f"{counts['needs_conversion']:,}")
    t.add_row("failed", f"{counts['failed']:,}")
    t.add_row("works merged (same book, other format/edition)", f"{len(merges):,}")
    console.print(t)

    total_books, total_words = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(word_count), 0) FROM books"
    ).fetchone()
    console.print(f"catalog now: [bold]{total_books:,}[/bold] works, {total_words:,} words")

    if failures:
        console.print(f"\n[yellow]failures ({len(failures)}):[/yellow]")
        for path, err in failures[:10]:
            console.print(f"  - {Path(path).name}: {err[:120]}")
        if len(failures) > 10:
            console.print(f"  ... and {len(failures) - 10} more (see ingest_errors table)")
