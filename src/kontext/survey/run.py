"""survey execution, shared by `kontext survey` and `kontext ingest`."""

from __future__ import annotations

import random
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn,
    TimeRemainingColumn,
)

from kontext.survey import db as dbmod
from kontext.survey.probes import PROBE_VERSION
from kontext.survey.report import build_report, render_report, write_json
from kontext.survey.walker import walk_files
from kontext.survey.worker import survey_one

_UPSERT_BATCH = 250


def run_survey(
    root: Path,
    db: Path,
    json_out: Path | None,
    workers: int,
    console: Console,
    sample: int | None = None,
    follow_symlinks: bool = False,
    reprobe: bool = False,
    show_report: bool = True,
) -> dict:
    root = root.resolve()
    conn = dbmod.connect(db)
    known = {} if reprobe else dbmod.existing_signatures(conn)

    console.print(f"walking [bold]{root}[/bold] ...")
    found = list(walk_files(root, follow_symlinks=follow_symlinks))
    if sample is not None and sample < len(found):
        found = random.sample(found, sample)

    todo = [
        f for f in found
        if known.get(f.path, (None, None, None)) != (f.size, f.mtime_ns, PROBE_VERSION)
    ]
    skipped = len(found) - len(todo)
    total_bytes = sum(f.size for f in todo)
    console.print(
        f"{len(found):,} files found, {skipped:,} already surveyed, "
        f"{len(todo):,} to probe ({total_bytes / 1024**3:.1f} gb to hash)"
    )

    run_id = dbmod.start_run(
        conn, str(root), datetime.now(timezone.utc).isoformat(timespec="seconds"),
        sampled=sample is not None, probe_version=PROBE_VERSION,
    )

    errors = 0
    probed = 0
    batch: list[dict] = []
    if todo:
        progress = Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(), MofNCompleteColumn(),
            TimeElapsedColumn(), TimeRemainingColumn(),
            console=console,
        )
        with progress, ProcessPoolExecutor(max_workers=workers) as pool:
            task = progress.add_task("probing", total=len(todo))
            futures = [pool.submit(survey_one, f.path, f.size, f.mtime_ns) for f in todo]
            for fut in as_completed(futures):
                row = fut.result()
                probed += 1
                if row["status"] == "error":
                    errors += 1
                batch.append(row)
                if len(batch) >= _UPSERT_BATCH:
                    dbmod.upsert_files(conn, batch)
                    batch = []
                progress.advance(task)
        dbmod.upsert_files(conn, batch)

    dbmod.finish_run(
        conn, run_id, datetime.now(timezone.utc).isoformat(timespec="seconds"),
        seen=len(found), probed=probed, skipped=skipped, errors=errors,
    )

    rep = build_report(conn)
    if json_out is not None:
        write_json(rep, json_out)
    if show_report:
        render_report(rep, console)
    conn.close()
    return {"seen": len(found), "probed": probed, "skipped": skipped, "errors": errors}
