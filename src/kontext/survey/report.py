"""aggregate the manifest into the phase-0 report.

produces a json document (machine-readable input for later phases) and a
rich console summary of the same numbers.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

from kontext.survey import estimates
from kontext.survey.estimates import compute_estimates, humanize_hours

EBOOK_FORMATS = ("pdf", "epub", "mobi", "djvu", "txt", "html", "fb2")

_NOISE_RE = re.compile(r"[\(\[\{].*?[\)\]\}]|[-_.,;:!?'\"]+")
_WS_RE = re.compile(r"\s+")


def build_report(conn: sqlite3.Connection,
                 embed_rate: float | None = None,
                 ocr_rate: float | None = None) -> dict:
    rows = [dict(r) for r in conn.execute("SELECT * FROM files")]
    ebooks = [r for r in rows if r["format"] in EBOOK_FORMATS]
    ok = [r for r in ebooks if r["status"] == "ok"]

    formats = _group(rows, "format")
    statuses = _group(rows, "status")

    # pdf text-layer split (pages only exist for pdf/djvu)
    text_layer: dict[str, dict] = {}
    for r in ebooks:
        cls = r["text_class"] or "unknown"
        bucket = text_layer.setdefault(cls, {"files": 0, "pages": 0})
        bucket["files"] += 1
        bucket["pages"] += r["pages"] or 0

    scanned_pages = int(
        sum((r["pages"] or 0) for r in ok if r["text_class"] == "scanned")
        + 0.5 * sum((r["pages"] or 0) for r in ok if r["text_class"] == "mixed")
    )
    words_extractable = sum(r["word_estimate"] or 0 for r in ok)

    duplicates = _duplicates(rows)
    languages = _group([r for r in ok if r["language"]], "language")
    unknown_language = sum(1 for r in ok if not r["language"])

    est = compute_estimates(words_extractable, scanned_pages, embed_rate, ocr_rate)

    problems = {
        status: {
            "count": len(group),
            "files": [
                {"path": r["path"], "format": r["format"], "error": r["error"]}
                for r in group
            ],
        }
        for status in ("corrupt", "encrypted", "drm", "error")
        if (group := [r for r in rows if r["status"] == status])
    }

    archives = [r for r in rows if r["format"] in ("zip", "rar", "7z") and r["status"] == "unsupported"]
    archived_ebooks = 0
    for r in archives:
        if r["contains"]:
            archived_ebooks += sum(json.loads(r["contains"]).values())

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totals": {
            "files": len(rows),
            "bytes": sum(r["size"] for r in rows),
            "ebook_files": len(ebooks),
            "usable_ebooks": len(ok),
        },
        "formats": formats,
        "statuses": statuses,
        "text_layer": text_layer,
        "scanned_pages_estimate": scanned_pages,
        "duplicates": duplicates,
        "works_estimate": _works_estimate(ok),
        "languages": languages,
        "unknown_language_files": unknown_language,
        "metadata_coverage": {
            "with_embedded_author_pct": round(
                100 * sum(r["has_metadata"] for r in ok) / len(ok), 1
            ) if ok else 0.0,
        },
        "archives": {
            "count": len(archives),
            "ebooks_inside_estimate": archived_ebooks,
        },
        "problems": problems,
        "estimates": est,
        "recommendations": _recommendations(
            text_layer, duplicates, archives, archived_ebooks, est,
            unknown_language, len(ok), formats,
        ),
    }
    return report


def _group(rows: list[dict], key: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        k = r[key] or "unknown"
        bucket = out.setdefault(k, {"count": 0, "bytes": 0})
        bucket["count"] += 1
        bucket["bytes"] += r["size"]
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["count"]))


def _duplicates(rows: list[dict]) -> dict:
    by_hash: dict[str, list[dict]] = {}
    for r in rows:
        if r["sha256"]:
            by_hash.setdefault(r["sha256"], []).append(r)
    groups = {h: g for h, g in by_hash.items() if len(g) > 1}
    redundant = sum(len(g) - 1 for g in groups.values())
    wasted = sum((len(g) - 1) * g[0]["size"] for g in groups.values())
    return {
        "exact_groups": len(groups),
        "redundant_files": redundant,
        "wasted_bytes": wasted,
        "examples": [
            [r["path"] for r in g] for g in list(groups.values())[:5]
        ],
    }


def _works_estimate(ok: list[dict]) -> dict:
    """cheap guess at unique works: normalized title clustering."""
    clusters: dict[str, int] = {}
    for r in ok:
        title = (r["title"] or "").lower()
        title = _NOISE_RE.sub(" ", title)
        title = _WS_RE.sub(" ", title).strip()
        if len(title) >= 8:
            clusters[title] = clusters.get(title, 0) + 1
    multi = sum(1 for n in clusters.values() if n > 1)
    return {
        "unique_titles_estimate": len(clusters),
        "titles_in_multiple_files": multi,
        "note": "normalized-title heuristic; real work-level dedup happens in phase 1 (minhash)",
    }


def _recommendations(text_layer, duplicates, archives, archived_ebooks,
                     est, unknown_language, usable, formats) -> list[str]:
    recs: list[str] = []
    docs = formats.get("doc", {}).get("count", 0)
    if docs:
        recs.append(
            f"{docs} office/help documents (doc/rtf/odt/chm) hold indexable text "
            "-> phase 1 needs a conversion step (libreoffice/pandoc) to include them"
        )
    scanned_files = text_layer.get("scanned", {}).get("files", 0)
    if usable and scanned_files / max(1, usable) > 0.25:
        recs.append(
            f"{scanned_files} scanned files -> ocr is the long pole (~{humanize_hours(est['ocr_hours'])} "
            "and growing with the dump); run it as a resumable background queue -- "
            "text-layer books get indexed and searchable first, ocr'd books join later"
        )
    if est["vector_ram_int8_gb"] > estimates.RAM_GB * 0.5:
        recs.append("projected vector index is large -> plan qdrant with on-disk vectors + binary quantization")
    if duplicates["wasted_bytes"] > 5 * estimates.GB:
        recs.append(
            f"{duplicates['redundant_files']} exact duplicate files waste "
            f"{duplicates['wasted_bytes'] / estimates.GB:.1f} gb -> skip duplicates at ingest (by sha256)"
        )
    if archives:
        recs.append(
            f"{len(archives)} archives contain an estimated {archived_ebooks} ebooks "
            "-> extract them before phase 1 or they will not be indexed"
        )
    if usable and unknown_language / max(1, usable) > 0.3:
        recs.append("many files have unknown language -> phase 1 should re-detect on fully extracted text")
    if not recs:
        recs.append("no blockers found -> proceed to phase 1 on this manifest")
    return recs


# ---------------------------------------------------------------- rendering

def render_report(report: dict, console: Console) -> None:
    t = report["totals"]
    console.print()
    console.print(
        f"[bold]corpus:[/bold] {t['files']:,} files, {t['bytes'] / estimates.GB:.1f} gb "
        f"| {t['ebook_files']:,} ebook files, {t['usable_ebooks']:,} usable"
    )

    ft = Table(title="formats", title_justify="left")
    ft.add_column("format")
    ft.add_column("files", justify="right")
    ft.add_column("gb", justify="right")
    for fmt, b in report["formats"].items():
        ft.add_row(fmt, f"{b['count']:,}", f"{b['bytes'] / estimates.GB:.2f}")
    console.print(ft)

    tl = Table(title="text layer (ocr scope)", title_justify="left")
    tl.add_column("class")
    tl.add_column("files", justify="right")
    tl.add_column("pages", justify="right")
    for cls, b in sorted(report["text_layer"].items()):
        tl.add_row(cls, f"{b['files']:,}", f"{b['pages']:,}")
    console.print(tl)

    if report["languages"]:
        lt = Table(title="languages (usable ebooks)", title_justify="left")
        lt.add_column("lang")
        lt.add_column("files", justify="right")
        for lang, b in list(report["languages"].items())[:10]:
            lt.add_row(lang, f"{b['count']:,}")
        if report["unknown_language_files"]:
            lt.add_row("unknown", f"{report['unknown_language_files']:,}")
        console.print(lt)

    d = report["duplicates"]
    console.print(
        f"duplicates: {d['exact_groups']:,} groups, {d['redundant_files']:,} redundant files, "
        f"{d['wasted_bytes'] / estimates.GB:.2f} gb wasted"
    )
    w = report["works_estimate"]
    console.print(f"unique works (title heuristic): ~{w['unique_titles_estimate']:,}")

    for status, info in report["problems"].items():
        console.print(f"[yellow]{status}[/yellow]: {info['count']:,} files")
    if report["archives"]["count"]:
        console.print(
            f"[yellow]archives[/yellow]: {report['archives']['count']:,} "
            f"(~{report['archives']['ebooks_inside_estimate']:,} ebooks inside)"
        )

    e = report["estimates"]
    et = Table(title="downstream estimates (phases 2-4)", title_justify="left")
    et.add_column("what")
    et.add_column("value", justify="right")
    et.add_row("extractable words", f"{e['words_extractable']:,}")
    et.add_row("words from ocr (est.)", f"{e['words_from_ocr']:,}")
    et.add_row("passages to embed", f"{e['chunks']:,}")
    et.add_row("vector index ram (int8)", f"{e['vector_ram_int8_gb']:.1f} gb")
    et.add_row("vector store disk (fp32)", f"{e['vector_disk_fp32_gb']:.1f} gb")
    et.add_row("embedding time (titan x)", humanize_hours(e["embed_hours"]))
    et.add_row("ocr time (titan x)", humanize_hours(e["ocr_hours"]))
    console.print(et)
    console.print(f"[bold]{e['ram_verdict']}[/bold]")

    console.print()
    console.print("[bold]recommendations[/bold]")
    for rec in report["recommendations"]:
        console.print(f"  - {rec}")
    console.print()


def write_json(report: dict, path: Path) -> None:
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
