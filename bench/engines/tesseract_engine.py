# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Tesseract baseline — mirrors kontext's OCR invocation exactly
(src/kontext/ocr/engine.py::_tesseract): `tesseract stdin stdout -l <lang>`
with OMP_THREAD_LIMIT=1 on the 300 DPI grayscale renders.

Two engine variants:
  tesseract        one language per document, like kontext's pick_lang()
  tesseract-multi  -l deu+eng combined

Language packs are read from bench/tessdata (TESSDATA_PREFIX), so no
system packs are needed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent
PAGES = BENCH / "out" / "pages"

# kontext's pick_lang() result per document
SLUG_LANG = {"box423": "deu", "box431": "eng"}


def ocr_png(png: Path, lang: str) -> str:
    env = os.environ | {
        "OMP_THREAD_LIMIT": "1",
        "TESSDATA_PREFIX": str(BENCH / "tessdata"),
    }
    proc = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", lang],
        input=png.read_bytes(),
        capture_output=True,
        env=env,
        timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"tesseract failed on {png}: {proc.stderr.decode()[:500]}")
    return proc.stdout.decode("utf-8", errors="replace")


def run(engine: str, lang_for: dict[str, str] | None) -> None:
    out_root = BENCH / "out" / "ocr" / engine
    # stale outputs from a previous run must not survive a failed one
    shutil.rmtree(out_root, ignore_errors=True)
    timings: dict[str, float] = {}
    for png in sorted(PAGES.glob("*/page_*.png")):
        slug = png.parent.name
        lang = lang_for[slug] if lang_for else "deu+eng"
        t0 = time.perf_counter()
        text = ocr_png(png, lang)
        timings[f"{slug}/{png.stem}"] = time.perf_counter() - t0
        dest = out_root / slug / f"{png.stem}.txt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        print(f"[{engine}] {slug}/{png.stem} ({lang}): {len(text)} chars")
    (out_root / "timings.json").write_text(
        json.dumps({"model_load_s": 0.0, "pages": timings}, indent=2)
    )


def main() -> None:
    variant = sys.argv[1] if len(sys.argv) > 1 else "both"
    if variant in ("single", "both"):
        run("tesseract", SLUG_LANG)
    if variant in ("multi", "both"):
        run("tesseract-multi", None)


if __name__ == "__main__":
    main()
