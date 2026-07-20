# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["surya-ocr>=0.13,<0.14", "transformers==4.45.2"]
# ///
"""Surya OCR (neural: transformer-based detection + recognition).
Batch sizes are capped via env vars for the 6 GB RTX 2060; raise them on
bigger GPUs. Writes one txt per page plus timings.json."""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

# must be set before surya imports read them
os.environ.setdefault("RECOGNITION_BATCH_SIZE", "16")
os.environ.setdefault("DETECTOR_BATCH_SIZE", "2")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

BENCH = Path(__file__).resolve().parent.parent
PAGES = BENCH / "out" / "pages"
OUT = BENCH / "out" / "ocr" / "surya"


def main() -> None:
    from PIL import Image

    t0 = time.perf_counter()
    from surya.detection import DetectionPredictor
    from surya.recognition import RecognitionPredictor

    det = DetectionPredictor()
    rec = RecognitionPredictor()
    load_s = time.perf_counter() - t0
    print(f"surya: models loaded in {load_s:.1f}s")

    # stale outputs from a previous run must not survive a failed one
    shutil.rmtree(OUT, ignore_errors=True)

    timings: dict[str, float] = {}
    for png in sorted(PAGES.glob("*/page_*.png")):
        slug = png.parent.name
        image = Image.open(png).convert("RGB")
        t0 = time.perf_counter()
        preds = rec([image], [None], det_predictor=det)
        timings[f"{slug}/{png.stem}"] = time.perf_counter() - t0
        text = "\n".join(line.text for line in preds[0].text_lines)
        dest = OUT / slug / f"{png.stem}.txt"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        print(f"[surya] {slug}/{png.stem}: {len(text)} chars "
              f"in {timings[f'{slug}/{png.stem}']:.1f}s")
    (OUT / "timings.json").write_text(
        json.dumps({"model_load_s": load_s, "pages": timings}, indent=2)
    )


if __name__ == "__main__":
    main()
