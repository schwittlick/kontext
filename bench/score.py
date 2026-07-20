# /// script
# requires-python = ">=3.11"
# dependencies = ["rapidfuzz>=3.9", "wordfreq>=3.1"]
# ///
"""Score all engine outputs under bench/out/ocr/.

Metrics:
- CER / WER against bench/test_data/ground_truth/ (only pages that have a
  reference transcription). Lower is better.
- Reference-free proxies on every sampled page: dictionary-word ratio
  (share of alphabetic tokens known to wordfreq's en/de lists — higher is
  better) and garbage ratio (share of characters outside a plausible
  charset — lower is better).
- Cross-engine agreement: mean pairwise CER between each engine pair.
  An engine that disagrees with everyone is usually the one that's wrong.
- Speed: seconds per page (model load reported separately).

Writes bench/out/results.json and bench/out/report.md.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from rapidfuzz.distance import Levenshtein
from wordfreq import zipf_frequency

BENCH = Path(__file__).resolve().parent
OCR = BENCH / "out" / "ocr"
GT = BENCH / "test_data" / "ground_truth"

OK_CHARS = re.compile(
    r"[A-Za-zÀ-ÿĀ-ž0-9\s.,;:!?()\[\]\-'\"„“”‚‘’«»&/%§+*=°_—–]"
)
TOKEN = re.compile(r"[A-Za-zÀ-ÿĀ-ž]{2,}")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[„“”]", '"', text)
    text = re.sub(r"[‚‘’]", "'", text)
    text = re.sub(r"[—–]", "-", text)
    return " ".join(text.split()).strip()


def cer(hyp: str, ref: str) -> float:
    return Levenshtein.distance(hyp, ref) / max(len(ref), 1)


def wer(hyp: str, ref: str) -> float:
    h, r = hyp.split(), ref.split()
    return Levenshtein.distance(h, r) / max(len(r), 1)


def dict_ratio(text: str) -> float | None:
    tokens = TOKEN.findall(text)
    if not tokens:
        return None
    known = sum(
        1 for t in tokens
        if zipf_frequency(t.lower(), "en") > 0 or zipf_frequency(t.lower(), "de") > 0
    )
    return known / len(tokens)


def garbage_ratio(text: str) -> float | None:
    if not text:
        return None
    return sum(1 for c in text if not OK_CHARS.match(c)) / len(text)


def mean(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def fmt(x, pct=True) -> str:
    if x is None:
        return "—"
    return f"{100 * x:.1f}%" if pct else f"{x:.1f}"


def main() -> None:
    engines = sorted(d.name for d in OCR.iterdir() if d.is_dir())
    if not engines:
        raise SystemExit("no engine outputs under bench/out/ocr/ — run the engines first")

    # texts[engine][page_key] = normalized text; page_key = "slug/page_0003"
    texts: dict[str, dict[str, str]] = defaultdict(dict)
    timings: dict[str, dict] = {}
    for eng in engines:
        for txt in sorted((OCR / eng).glob("*/page_*.txt")):
            texts[eng][f"{txt.parent.name}/{txt.stem}"] = normalize(txt.read_text())
        tfile = OCR / eng / "timings.json"
        timings[eng] = json.loads(tfile.read_text()) if tfile.exists() else {}

    gt: dict[str, str] = {}
    for txt in sorted(GT.glob("*/page_*.txt")):
        gt[f"{txt.parent.name}/{txt.stem}"] = normalize(txt.read_text())

    all_pages = sorted({p for t in texts.values() for p in t})
    slugs = sorted({p.split("/")[0] for p in all_pages})

    results: dict = {"engines": {}, "pairwise_cer": {}, "pages": all_pages, "gt_pages": sorted(gt)}
    for eng in engines:
        per_page = {}
        for page, text in texts[eng].items():
            row: dict = {
                "chars": len(text),
                "dict_ratio": dict_ratio(text),
                "garbage_ratio": garbage_ratio(text),
                "seconds": timings[eng].get("pages", {}).get(page),
            }
            if page in gt:
                row["cer"] = cer(text, gt[page])
                row["wer"] = wer(text, gt[page])
            per_page[page] = row
        by_slug = {
            s: {
                "cer": mean(r.get("cer") for p, r in per_page.items() if p.startswith(s)),
                "wer": mean(r.get("wer") for p, r in per_page.items() if p.startswith(s)),
                "dict_ratio": mean(r["dict_ratio"] for p, r in per_page.items() if p.startswith(s)),
                "garbage_ratio": mean(r["garbage_ratio"] for p, r in per_page.items() if p.startswith(s)),
                "sec_per_page": mean(r["seconds"] for p, r in per_page.items() if p.startswith(s)),
            }
            for s in slugs
        }
        results["engines"][eng] = {
            "model_load_s": timings[eng].get("model_load_s"),
            "pages": per_page,
            "by_slug": by_slug,
            "overall": {
                "cer": mean(r.get("cer") for r in per_page.values()),
                "wer": mean(r.get("wer") for r in per_page.values()),
                "dict_ratio": mean(r["dict_ratio"] for r in per_page.values()),
                "garbage_ratio": mean(r["garbage_ratio"] for r in per_page.values()),
                "sec_per_page": mean(r["seconds"] for r in per_page.values()),
            },
        }

    for i, a in enumerate(engines):
        for b in engines[i + 1:]:
            shared = [p for p in all_pages if p in texts[a] and p in texts[b]]
            results["pairwise_cer"][f"{a}|{b}"] = mean(
                cer(texts[a][p], texts[b][p]) for p in shared
            )

    (BENCH / "out" / "results.json").write_text(json.dumps(results, indent=2))

    # --- markdown report ---
    lines = ["# OCR benchmark report", ""]
    lines += [f"Pages: {len(all_pages)} sampled ({', '.join(slugs)}); "
              f"ground truth on {len(gt)}: {', '.join(sorted(gt))}", ""]

    lines += ["## Accuracy vs ground truth (lower is better)", ""]
    lines += ["| engine | CER | WER | CER box423 (deu) | CER box431 (eng) |",
              "|---|---|---|---|---|"]
    for eng in engines:
        e = results["engines"][eng]
        lines.append(
            f"| {eng} | {fmt(e['overall']['cer'])} | {fmt(e['overall']['wer'])} "
            f"| {fmt(e['by_slug'].get('box423', {}).get('cer'))} "
            f"| {fmt(e['by_slug'].get('box431', {}).get('cer'))} |"
        )

    lines += ["", "## Reference-free metrics, all sampled pages", ""]
    lines += ["| engine | dict-word ratio ↑ | garbage ratio ↓ | sec/page | model load (s) |",
              "|---|---|---|---|---|"]
    for eng in engines:
        e = results["engines"][eng]
        lines.append(
            f"| {eng} | {fmt(e['overall']['dict_ratio'])} "
            f"| {fmt(e['overall']['garbage_ratio'])} "
            f"| {fmt(e['overall']['sec_per_page'], pct=False)} "
            f"| {fmt(e['model_load_s'], pct=False)} |"
        )

    lines += ["", "## Cross-engine agreement (mean pairwise CER)", ""]
    lines += ["| pair | CER |", "|---|---|"]
    for pair, v in sorted(results["pairwise_cer"].items(), key=lambda kv: kv[1] or 9):
        lines.append(f"| {pair.replace('|', ' vs ')} | {fmt(v)} |")

    report = BENCH / "out" / "report.md"
    report.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nwrote {report} and results.json")


if __name__ == "__main__":
    main()
