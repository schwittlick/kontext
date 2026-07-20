# /// script
# requires-python = ">=3.11"
# dependencies = ["pymupdf>=1.24"]
# ///
"""Render a fixed sample of pages from the bench/test_data PDFs to PNGs.

Uses the exact same rasterization as kontext's OCR engine
(src/kontext/ocr/engine.py): 300 DPI, grayscale, longest side capped at
4500 px. Every OCR engine in the benchmark consumes these PNGs, so all
engines see identical input.

Page indices are 0-based and deterministic so results are comparable
across runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent
OUT = BENCH / "out" / "pages"

OCR_DPI = 300
MAX_PAGE_PX = 4500

# slug -> (filename prefix in test_data/, 0-based page indices to sample)
SAMPLES: dict[str, tuple[str, list[int]]] = {
    "box423": ("BOX 423", [2, 8, 15, 22, 29, 36]),
    "box431": ("BOX 431", [3, 10, 20, 40, 70, 100]),
}


def find_pdf(prefix: str) -> Path:
    hits = sorted(p for p in (BENCH / "test_data").glob("*.pdf") if p.name.startswith(prefix))
    if len(hits) != 1:
        sys.exit(f"expected exactly one bench/test_data PDF starting with {prefix!r}, found {hits}")
    return hits[0]


def render(doc: pymupdf.Document, index: int, dest: Path) -> None:
    page = doc[index]
    zoom = OCR_DPI / 72
    longest = max(page.rect.width, page.rect.height) * zoom
    if longest > MAX_PAGE_PX:
        zoom *= MAX_PAGE_PX / longest
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY)
    pix.save(dest)


def main() -> None:
    manifest: dict[str, dict] = {}
    for slug, (prefix, indices) in SAMPLES.items():
        pdf = find_pdf(prefix)
        out_dir = OUT / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        doc = pymupdf.open(pdf)
        pages = []
        for i in indices:
            if i >= doc.page_count:
                print(f"skip {slug} page {i}: document has {doc.page_count} pages")
                continue
            dest = out_dir / f"page_{i:04d}.png"
            render(doc, i, dest)
            pages.append(i)
            print(f"rendered {slug} page {i} -> {dest.relative_to(ROOT)}")
        doc.close()
        manifest[slug] = {"pdf": pdf.name, "pages": pages}
    (OUT.parent / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"manifest -> {OUT.parent / 'manifest.json'}")


if __name__ == "__main__":
    main()
