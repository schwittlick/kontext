"""ocr queue orchestration: awaiting_ocr books -> text -> catalog (phase 4).

designed to run alongside `kontext embed`: ocr is cpu-bound (tesseract),
embedding is gpu-bound, and the catalog writes on both sides are short
wal transactions. resumable at book granularity -- a book's status flips
to extracted only after its text is committed.
"""

from __future__ import annotations

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
from kontext.ingest import dedup
from kontext.model import Extraction
from kontext.ocr import engine


def run_ocr(
    catalog_db: Path,
    workers: int,
    console: Console,
    limit: int | None = None,
) -> dict:
    conn = catalog.connect(catalog_db)
    rows = catalog.books_awaiting_ocr(conn)
    if limit:
        rows = rows[:limit]
    if not rows:
        console.print("ocr queue is empty -- no books awaiting ocr")
        return {"counts": Counter(), "failures": []}

    available = engine.available_langs()
    fallbacks: Counter = Counter()
    tasks: list[dict] = []
    for r in rows:
        lang = engine.pick_lang(r["language"], available)
        want = engine.TESS_LANGS.get((r["language"] or "").lower(), engine.FALLBACK_LANG)
        if want != lang:
            fallbacks[want] += 1
        tasks.append({
            "book_id": r["id"], "paths": [r["path"]], "format": r["format"],
            "tess_lang": lang, "title": r["title"], "author": r["author"],
            "language": r["language"],
        })
    for want, n in sorted(fallbacks.items()):
        console.print(
            f"[yellow]tesseract language pack '{want}' not installed --"
            f" {n:,} books fall back to {engine.FALLBACK_LANG}[/yellow]"
        )
    console.print(f"{len(tasks):,} books in the ocr queue")

    counts: Counter = Counter()
    failures: list[tuple[str, str]] = []
    new_ids: set[int] = set()
    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(), MofNCompleteColumn(),
        TimeElapsedColumn(), TimeRemainingColumn(),
        console=console,
    )
    with progress, ProcessPoolExecutor(max_workers=workers) as pool:
        bar = progress.add_task("ocr", total=len(tasks))
        futures = {pool.submit(engine.ocr_task, t): t for t in tasks}
        for fut in as_completed(futures):
            t = futures[fut]
            try:
                result = fut.result()
                ext: Extraction = result["extraction"]
            except Exception as exc:  # worker died (oom, segfault in a lib)
                ext = Extraction(status="failed", error=f"worker: {exc}")
                result = {"minhash": None}
            if ext.status == "extracted":
                catalog.finish_ocr(conn, t["book_id"], ext, result["minhash"])
                new_ids.add(t["book_id"])
                counts["ocred"] += 1
                counts["words"] += ext.word_count
                counts["pages"] += len(ext.blocks)
            else:
                # book stays awaiting_ocr (retried next run); error is logged
                counts["failed"] += 1
                failures.append((t["paths"][0], ext.error or "?"))
                catalog.log_error(conn, t["paths"][0], None,
                                  stage="ocr", error=ext.error or "?")
            progress.advance(bar)

    # ocr'd books have real text (and a minhash) now -- fold editions that
    # turn out to be the same work as an already-extracted book
    merges = dedup.find_merges(
        catalog.load_signatures(conn, dedup.MIN_WORDS_FOR_DEDUP), new_ids
    )
    for canonical_id, alternate_id in merges:
        catalog.merge_books(conn, canonical_id, alternate_id)

    _summary(conn, console, counts, merges, failures)
    conn.close()
    return {"counts": counts, "merges": len(merges), "failures": failures}


def _summary(conn, console: Console, counts: Counter,
             merges: list, failures: list[tuple[str, str]]) -> None:
    t = Table(title="ocr result", title_justify="left")
    t.add_column("what")
    t.add_column("value", justify="right")
    t.add_row("books ocr'd", f"{counts['ocred']:,}")
    t.add_row("words / pages", f"{counts['words']:,} / {counts['pages']:,}")
    t.add_row("failed (retried next run)", f"{counts['failed']:,}")
    t.add_row("works merged (same book, other format/edition)", f"{len(merges):,}")
    console.print(t)

    remaining = conn.execute(
        "SELECT COUNT(*) FROM books WHERE status='awaiting_ocr'"
    ).fetchone()[0]
    if remaining:
        console.print(f"still awaiting ocr: {remaining:,} books")
    if counts["ocred"]:
        console.print("run [bold]kontext index[/bold] to chunk + embed the new text")

    if failures:
        console.print(f"\n[yellow]failures ({len(failures)}):[/yellow]")
        for path, err in failures[:10]:
            console.print(f"  - {Path(path).name}: {err[:120]}")
        if len(failures) > 10:
            console.print(f"  ... and {len(failures) - 10} more (see ingest_errors table)")
