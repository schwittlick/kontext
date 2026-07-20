# OCR benchmark suite

Compares OCR approaches on the scanned PDFs in `bench/test_data/`:
1948 UNESCO typewritten memos in English (`box431`) and 1951 German
newspaper clippings (`box423`) — no text layer in either.

## Engines

| engine | what it is |
|---|---|
| `tesseract` | the approach implemented in this repo (`src/kontext/ocr/engine.py`): 300 DPI grayscale render → `tesseract stdin stdout -l <lang>`, one language per document like `pick_lang()` |
| `tesseract-multi` | same, but `-l deu+eng` combined |
| `easyocr` | neural (CRAFT detector + CRNN recognizer), de+en, GPU |
| `surya` | neural (transformer detection + recognition), GPU |

All engines consume the identical PNGs from `render_pages.py` (the exact
rasterization kontext uses), so differences are purely the OCR engine.
Each engine is a standalone `uv run` script with inline deps — the heavy
neural stacks never touch the project venv. Tesseract language packs are
downloaded to `bench/tessdata/` (no system packages needed).

## Run

```bash
bench/run_bench.sh          # everything: render → all engines → score
# or piecemeal:
uv run bench/render_pages.py
uv run bench/engines/tesseract_engine.py
uv run bench/engines/easyocr_engine.py     # ~2 GB model download on first run
uv run bench/engines/surya_engine.py       # ~1.5 GB model download on first run
uv run bench/score.py
```

Outputs land in `bench/out/` (gitignored): `pages/` renders,
`ocr/<engine>/<slug>/page_NNNN.txt` raw text, `results.json`, `report.md`.

## Validation

- **CER/WER vs ground truth** — `bench/test_data/ground_truth/<slug>/page_NNNN.txt`
  holds reference transcriptions for 4 of the 12 sampled pages (see the
  README there for the transcription policy; add more files and re-run
  `score.py` to widen coverage).
- **Reference-free proxies** on all 12 pages: dictionary-word ratio
  (wordfreq en+de) and garbage-character ratio.
- **Cross-engine agreement**: mean pairwise CER between engines — an
  engine that disagrees with all others is usually the wrong one.
- **Speed**: seconds/page (model load time reported separately; the first
  GPU page includes CUDA warmup).

## Caveats

- Handwritten annotations, stamps and cut-off marginalia are excluded from
  ground truth but engines still read them, so a few CER points come from
  "correctly" reading things the reference omits. This affects all engines
  equally.
- 12 sampled pages, 4 with ground truth — good for ranking engines, too
  small to resolve <1% CER differences.
- Neural engines were tuned for a 6 GB GPU (`RECOGNITION_BATCH_SIZE=16`,
  `DETECTOR_BATCH_SIZE=2` in `surya_engine.py`); raise for bigger cards.
  Desktop apps (VS Code, Chrome) can hold 1+ GB of VRAM — EasyOCR retries
  OOM'd pages on CPU (logged as "CPU fallback", ~5x slower, same text),
  but closing them gives cleaner timing numbers.

## Adding an engine

Drop `bench/engines/<name>_engine.py` that reads
`bench/out/pages/*/page_*.png` and writes
`bench/out/ocr/<name>/<slug>/page_NNNN.txt` plus a `timings.json`
(`{"model_load_s": float, "pages": {"slug/page_NNNN": seconds}}`), then
re-run `score.py`. Candidates: PaddleOCR, docTR, kraken, or a vision-LLM
(e.g. Claude) via API.
