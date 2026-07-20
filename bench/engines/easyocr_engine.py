# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["easyocr>=1.7", "torch>=2.4"]
# ///
"""EasyOCR (neural: CRAFT detector + CRNN recognizer), German+English,
GPU if available. Reads the shared 300 DPI renders, writes one txt per
page plus timings.json (model load reported separately).

The CRAFT detector needs ~1 GB of free VRAM at full page resolution, so
on a shared 6 GB card an OOM is possible: pages that OOM twice on GPU
are retried on a CPU reader (slower but correct) and flagged in the log.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

BENCH = Path(__file__).resolve().parent.parent
PAGES = BENCH / "out" / "pages"
OUT = BENCH / "out" / "ocr" / "easyocr"


def main() -> None:
    import easyocr
    import torch

    gpu = torch.cuda.is_available()
    print(f"easyocr: gpu={gpu}")
    t0 = time.perf_counter()
    reader = easyocr.Reader(["de", "en"], gpu=gpu)
    load_s = time.perf_counter() - t0
    cpu_reader = None

    # stale outputs from a previous run must not survive a failed one
    shutil.rmtree(OUT, ignore_errors=True)

    timings: dict[str, float] = {}
    for png in sorted(PAGES.glob("*/page_*.png")):
        slug = png.parent.name
        note = ""
        t0 = time.perf_counter()
        try:
            lines = reader.readtext(str(png), detail=0, paragraph=True)
        except torch.OutOfMemoryError:
            torch.cuda.empty_cache()
            try:
                lines = reader.readtext(str(png), detail=0, paragraph=True)
            except torch.OutOfMemoryError:
                torch.cuda.empty_cache()
                if cpu_reader is None:
                    cpu_reader = easyocr.Reader(["de", "en"], gpu=False)
                lines = cpu_reader.readtext(str(png), detail=0, paragraph=True)
                note = " (CPU fallback after GPU OOM)"
        timings[f"{slug}/{png.stem}"] = time.perf_counter() - t0
        text = "\n".join(lines)
        dest = OUT / slug / f"{png.stem}.txt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        print(f"[easyocr] {slug}/{png.stem}: {len(text)} chars "
              f"in {timings[f'{slug}/{png.stem}']:.1f}s{note}")
    (OUT / "timings.json").write_text(
        json.dumps({"model_load_s": load_s, "pages": timings}, indent=2)
    )


if __name__ == "__main__":
    main()
