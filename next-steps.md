# next steps — where to pick up

updated 2026-07-20 (switching laptops mid-work). the two phase-2/3 work items
below now have their scaffolding **built and tested** — what remains is mostly
*your* input (the golden cases) plus tuning, and some optional polish. lyrik is
still grinding its queues in tmux (see below).

## lyrik queues (still running, unchanged)

- `kontext embed`: 4.44m chunks at ~8/s → done ~jul 22. resumable; the index
  is searchable as it fills.
- `kontext ocr --workers 5`: ~9.9k scanned books. when it drains, run
  `kontext chunk` then `kontext embed` on lyrik to fold the new text in (a
  still-running embed picks new chunks up by itself).

## 1. golden set — BUILT, needs your cases (phase 2, last step)

**done:**

- [golden.yaml](golden.yaml) at the repo root — the format + a header spec, and
  5 placeholder cases showing the case types.
- `kontext eval` — loads the set, runs the live search, prints hit@k + mrr per
  case and total, and flags `unreachable` cases (book not indexed, or a
  must_contain phrase that straddles a chunk boundary). loads the model **once**
  per run. flags: `--rerank`, `--k`, `--golden`, `--db`, `--qdrant`.
- tested in [tests/test_eval.py](tests/test_eval.py).

**left to do (the real work):**

- replace the 5 placeholders with **~30 real cases**. each: `query`, `book`
  (title/author substring or numeric id), optional `must_contain` (a short —
  under ~12 words — verbatim quote from the target passage). spread them:
  paraphrase (no shared words), exact-term/name (bm25), non-english,
  cross-language (en query → fr/de book), natural-language questions.
- **run `kontext eval` on lyrik** (the full index). the dev laptops only have
  ~1% of the local corpus embedded (85k chunks, ~1024 vectors) — bm25 is full
  but the dense leg is nearly empty there, so semantic recall can't be judged
  on a laptop.
- tune against the number: chunk size (300/200/500 words), overlap, rrf,
  rerank on/off, candidate pool. record the best hit@10 / mrr in the readme.
  don't polish the ui until this number is respectable.

## 2. web app — BUILT (phase 3)

**done:**

- `kontext serve` → fastapi + a server-rendered ui (jinja, a GET form — no
  javascript, no build step). models load once at startup → sub-second
  queries. binds `0.0.0.0` (lan, no auth). routes:
  - `GET /` — search box + result cards (title, author, locator, score,
    query terms highlighted), download links.
  - `GET /api/search?q=&limit=&rerank=` — json, reuses `search()` as-is.
  - `GET /download/{book_id}` — streams the read-only original (untouched).
- code in [src/kontext/web/](src/kontext/web/); deps added to
  [pyproject.toml](pyproject.toml) (fastapi, uvicorn, jinja2, pyyaml) — run
  `uv sync` on the other laptop.
- tested in [tests/test_web.py](tests/test_web.py) (routing, highlight, json,
  download 200/404).

**left to do (optional polish, only after the golden number is good):**

- htmx: swap the results list in place instead of a full-page GET. thin layer
  over the same endpoints; the form already degrades without it.
- filters: language / format / author. qdrant payload carries `book_id`;
  language/format live in sqlite (filter there, or push into the qdrant query).

## operational notes

- **qdrant has no restart policy**, so a reboot leaves it stopped and search
  reports "vector store unreachable". fix once:
  `docker update --restart unless-stopped qdrant`. the readme start command now
  caps its memory (`-m 2g`).
- run `kontext serve` / `kontext eval` on **lyrik** for the full index; the
  laptops are for plumbing + authoring keyword-ish cases only.

## smaller known loose ends (unchanged, from building the ocr queue)

- **mixed pdfs** (617 books, status=extracted + needs_ocr=1): text pages
  indexed, scanned pages not. re-ocr needs block merge + re-chunk + re-embed.
  deferred deliberately.
- **dedup after embedding**: folding an already-embedded book orphans its
  qdrant vectors (harmless — hydration drops them — but a `store.delete(book_id)`
  on merge would reclaim the recall headroom).
- **progress bars**: `embed` counts only the remaining chunks after a restart
  (looks like it restarts — it doesn't); `ocr` ticks per book. cosmetic.
