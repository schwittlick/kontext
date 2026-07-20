#!/usr/bin/env bash
# OCR benchmark: render sample pages, run every engine, score.
# Engines are independent uv scripts — a missing/broken engine doesn't
# block the others; the scorer works with whatever outputs exist.
set -uo pipefail
cd "$(dirname "$0")"

# language packs for the tesseract baseline (no root needed)
mkdir -p tessdata
for l in eng deu; do
    [ -f "tessdata/$l.traineddata" ] || curl -sL -o "tessdata/$l.traineddata" \
        "https://github.com/tesseract-ocr/tessdata/raw/main/$l.traineddata"
done

uv run render_pages.py || exit 1

for engine in engines/*_engine.py; do
    echo "=== $engine ==="
    uv run "$engine" || echo "!!! $engine failed — continuing"
done

uv run score.py
