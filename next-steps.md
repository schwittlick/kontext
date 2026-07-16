# next steps — where to pick up

written 2026-07-16. lyrik is grinding through two multi-day queues in tmux:
`kontext embed` (4.44m chunks at a measured ~8 chunks/s → done ~jul 22) and
`kontext ocr --workers 5` (~9.9k scanned books, 1.24m pages → similar window).
both are resumable; partial results are searchable immediately. when the ocr
queue drains, run `kontext chunk` on lyrik — a still-running embed picks the
new chunks up by itself, otherwise re-run `kontext embed`.

the two work items below are dev-laptop work (small test corpus, no gpu
needed) and can happen while lyrik grinds. do them in this order.

## 1. the golden set — a quality yardstick for search (phase 2, last step)

**what:** ~30 test cases of the form "for this query, this passage is the
right answer". real queries against books you actually know, e.g.

- query: "the reproduction of images changes the artwork itself"
  → benjamin, *the work of art in the age of mechanical reproduction*,
  the section on aura

**why:** right now there is no objective way to tell whether search is good,
or whether a tuning change helps or hurts. with a golden set, evaluation is
mechanical: run all 30 queries, count how often the expected passage is in
the top 10 (and at which rank). every knob becomes measurable instead of
vibes: chunk size (300 words? 200? 500?), overlap, rrf weights between dense
and bm25, rerank on/off, candidate pool size.

**how, roughly:**

- a `golden.yaml` (or jsonl) in the repo: `query`, `book` (title substring
  or id), optionally `must_contain` (a phrase the right chunk contains).
  spread the cases: paraphrase queries (no shared words with the passage),
  exact-name/term queries (bm25 territory), non-english ones, a couple of
  cross-language ones (query in english, book in french).
- a small `kontext eval` command: loads the set, runs search with the
  current settings, prints hit@10 / mrr per case and total. needs a chunked
  + embedded corpus, so either the dev laptop gets a mini-corpus of familiar
  books, or eval runs on lyrik once its index is full.
- tune against it, keep the best settings, note the scores in the readme.
  don't touch the ui until this number is respectable.

## 2. the web app (phase 3)

**what:** a small fastapi server that runs permanently on lyrik + an htmx ui
(server-rendered, no build step) so search is usable from any browser on
the lan.

**why:** the cli pays ~20 s of model loading per query and needs ssh. a
resident server holds bge-m3 + the reranker in memory once → sub-second
queries, phones/laptops just open a page.

**pieces, roughly:**

- `GET /api/search?q=...&limit=10&rerank=1` → json passages (reuse
  `kontext.search.query.search` as-is; it already returns hits with title,
  author, locator, excerpt, path). models load once at startup, not per
  request — that is the whole point.
- filters later: language, format, author (qdrant payload already carries
  book_id; language/format live in sqlite).
- ui: one search box, results as cards — title, author, locator ("p. 217" /
  chapter), excerpt with the query terms highlighted, score. htmx swaps the
  result list in place.
- `GET /download/{book_id}` streams the original file from the read-only
  dump (path is in book_files, role='primary'). the dump stays untouched.
- serve on the lan only (bind 0.0.0.0, no auth for now, it's a home net).
- the readme's phase 3 line is the spec: "fastapi retrieval api + htmx ui:
  excerpt highlighting, locators, download endpoint, lan deployment."

## smaller known loose ends (from building the ocr queue)

- **mixed pdfs** (617 books, status=extracted + needs_ocr=1): their text
  pages are indexed, their scanned pages are not. re-ocr needs block
  merging + re-chunk + re-embed of those books. deferred deliberately.
- **dedup after embedding**: when the ocr queue's dedup folds a book that
  was already chunked+embedded, its qdrant vectors are orphaned (harmless:
  hydration drops them silently, but they cost a little recall headroom).
  a `store.delete(book_id)` on merge would clean this up.
- **progress bars**: `embed` counts only the remaining chunks after a
  restart (looks like it starts over — it doesn't); `ocr` ticks per book,
  so big scans make it look frozen. cosmetic.
