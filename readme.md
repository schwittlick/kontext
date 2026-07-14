# kontext

a tool to find context in a personal ebook library.

## the idea

i have a massive (500gb) database of e-books of various datatypes. and i want to train some system to cite passages of this database. i want to find similar text passages. e.g. i have an idea, a thought, maybe just a sentence, maybe more. and i want to find passages in the ebooks that deal with that topic. i want to know what page i should read in the ebook, with an excerpt of a relevant passage. i want to be able to download the ebook from the database right there.

before i had been implementing this with word2vec etc. but maybe opensearch is a similar tool to achieve this with? ive also used spacy for a lot of pre-processing etc.

technically i am thinking about a web-app that i can use to search, which is written in python. (the architecture questions from the first draft of this readme are answered in "decisions" below.)

## clarified requirements

- input: a thought — a sentence or a paragraph. output: ranked passages from the library, each with book, location (page or chapter), a highlighted excerpt, and a download link for the source file.
- the library is a ~500gb **unstructured dump**: no folder convention, no existing metadata db. ingestion has to discover files, pull metadata out of the files themselves, and deduplicate.
- **all formats**: epub/mobi/azw3, pdfs with a text layer, **scanned pdfs (need ocr)**, plus txt/html/djvu and friends.
- roughly **20k–100k books**, mostly english.
- runs on a **local machine with an nvidia titan x (12 gb vram) and 64 gb ram**; web app is used from devices on the **lan**.
- adding new books must be cheap — no full re-index, definitely no re-training.
- the dump is treated **read-only**: kontext never moves or rewrites the original files, it only reads them and remembers their paths.

## decisions

**no training — pretrained embedding models.** the "train some system" from the original idea is obsolete. word2vec had to be trained because its vectors were corpus-specific and per-word. modern embedding models are pretrained and frozen: they map any passage or query into the same vector space, out of the box. adding a book = extract → chunk → embed → insert into the index. removing a book = delete its vectors. the "add books without re-training" requirement falls out of this for free, because there is no training step anywhere.

**hybrid retrieval, then rerank.** dense vectors find passages that talk about the same thing in different words; a lexical signal (bm25-style sparse vectors) wins for names, rare terms and exact phrases. both run on every query and the result lists are fused (reciprocal rank fusion). a cross-encoder reranker then re-scores the top ~50 fused hits to produce the final top 10 — after the embedding model itself, this is the biggest quality lever available.

**qdrant instead of opensearch.** opensearch can do this (bm25 + knn), but it is a java cluster system built for fleets of servers — heavy to run and tune on one desktop. qdrant is a single container purpose-built for vector search: quantization, on-disk indexes, metadata filters (author/language/year), and sparse vectors for the lexical side, all behind one python client. same capability, a fraction of the ops. (if even one container feels like too much, lancedb is the embedded in-process alternative.)

**what survives from the old stack.** spacy: only the sentence splitter, used by the chunker — no pos/ner pipelines needed. word2vec: retired, replaced by the pretrained embedding model.

**search now, rag later.** rag (retrieval-augmented generation) means: an llm receives the retrieved passages together with the question and writes a synthesized answer that cites them. kontext's core is deliberately just the retrieval half, exposed as a clean api — query in, passages with locations out. the web ui is one consumer of that api; a future rag layer would simply be another consumer. nothing underneath changes, so deferring it costs nothing.

## architecture

```
ingest (offline, incremental, resumable)
─────────────────────────────────────────
ebook dump (read-only)
  │  walk files, detect format + text layer, sha256 dedup
  ▼
extract
  ├─ epub / html / txt        → ebooklib / direct
  ├─ mobi / azw3              → calibre ebook-convert → epub path
  ├─ pdf with text layer      → pymupdf (real page numbers)
  └─ scanned pdf / djvu       → ocr queue (ocrmypdf or surya on gpu)
  ▼                             — the slow lane: runs for days, resumable,
canonical book                    index grows as it finishes
  metadata (title/author/lang, embedded + filename heuristics)
  chapters + page map, fuzzy dedup (minhash) → sqlite
  ▼
chunk   sentence-aware (spacy senter), ~300 words, ~15% overlap,
        never across chapter boundaries, locator kept per chunk
  ▼
embed   bge-m3 on gpu → dense (1024d) + sparse vector per chunk
  ▼
index   qdrant (vectors + filter payload) · sqlite (books, files, chunks, jobs)

search (interactive, target < 1s)
─────────────────────────────────────────
query → embed → qdrant hybrid query (dense + sparse, rrf fusion) → top 50
      → cross-encoder rerank (bge-reranker-v2-m3, gpu)           → top 10
      → hydrate from sqlite: title, author, page/chapter, excerpt,
        download link (streams the untouched original file)
      → fastapi retrieval api → htmx web ui on the lan
```

### components

| concern            | choice                                             |
| ------------------ | -------------------------------------------------- |
| extraction         | pymupdf, ebooklib, calibre cli, ocrmypdf / surya, djvulibre |
| sentence splitting | spacy (senter only)                                |
| embeddings         | bge-m3 via sentence-transformers (dense + sparse, multilingual as a bonus) |
| reranker           | bge-reranker-v2-m3 (cross-encoder)                 |
| vector index       | qdrant (docker, int8/binary quantization, on-disk) |
| metadata + jobs    | sqlite (fts5 available as a lexical fallback)      |
| api                | fastapi                                            |
| web ui             | jinja + htmx — no build step                       |
| cli / ingest       | typer, watchdog for a watch folder later           |
| deploy             | docker compose (app + qdrant), lan binding, optional caddy basic-auth |

### sizing (on the actual hardware: titan x 12 gb, 64 gb ram)

- 20k–100k books × ~300 chunks/book → **~6m–35m passages**. `kontext survey` replaces this range with the real number.
- 1024-dim vectors: fp32 would be 4 kb each (24–140 gb — too much), int8-quantized ~1 kb → **7–41 gb incl. hnsw graph**. qdrant keeps quantized vectors in ram and the fp32 originals on disk for rescoring. with 64 gb ram the low end is comfortable; at the 100k-book extreme it gets tight → binary quantization or on-disk vectors, both supported, no redesign.
- bge-m3 (2.3 gb fp32) fits the 12 gb vram easily with decent batch sizes; the titan x has no tensor cores, so a conservative **~150 chunks/s** → the one-time index build is **~11 h (6m chunks) to ~3 days (35m chunks)**. ocr of the scanned share is the real long pole and lives in its own resumable queue from day one.
- one caveat to verify in phase 2: recent pytorch wheels dropped support for older gpu architectures — the titan x may need a pinned older pytorch/cuda build. worst case, embedding runs on cpu (slower) or the model choice shifts smaller; nothing else changes.
- a fresh book is ~300 chunks → indexed in seconds. that is the whole "add a book" story.

### page numbers vs. reflowable formats

pdfs have real pages, so results say "p. 217". epub/mobi have no fixed pages; results say chapter + position (e.g. "ch. 7, ~34%") and the excerpt itself is the anchor. this is a format property, not a bug — the ui just renders whichever locator the chunk carries.

## installation (arch)

```
sudo pacman -S uv                    # required: everything python runs through uv

# optional converters -- kontext ingest uses them when present, and
# automatically retries books that waited on them once they appear:
sudo pacman -S calibre               # ebook-convert: mobi/azw3 -> epub
sudo pacman -S libreoffice-fresh     # soffice: doc/rtf/odt -> txt, ppt/pptx -> pdf

# phase 4 (ocr queue) will additionally need:
sudo pacman -S ocrmypdf tesseract-data-eng djvulibre
#   (+ tesseract-data-<lang> per corpus language; or surya-ocr via uv on
#    the gpu machine -- decided when phase 4 is built)

# phase 2 (search) on the titan-x machine:
sudo pacman -S docker                # qdrant runs as a container
#   pytorch/sentence-transformers come via uv sync when phase 2 lands;
#   mind the titan-x pytorch caveat in the sizing section
```

python dependencies are handled by `uv sync` — no manual venv/pip.

## usage

```
uv sync                                   # once: create env, install deps
uv run kontext survey data               # phase 0: probe everything -> survey.db + report
uv run kontext report                    # re-render the report (no probing)
uv run kontext ingest data               # phase 1: extract new books -> kontext.db
uv run kontext books --search kafka      # browse the catalog
uv run kontext show 42                   # one work: metadata, files, first blocks
```

everything is resumable and incremental: `survey` skips files whose size+mtime are unchanged, `ingest` refreshes the survey itself and then only touches files it has never extracted (by sha256). after more downloads land, just re-run `ingest`. if extractor logic changes and a clean slate is wanted: delete `kontext.db` and re-run (minutes, the dump is never touched).

artifacts:

- **`survey.db`** — the file manifest: format, sha256, text-layer class, pages, word estimate, language, title/author, drm/corrupt status per file.
- **`survey_report.json`** — aggregate report incl. ocr scope and the downstream estimates, every assumption spelled out.
- **`kontext.db`** — the catalog: works (deduplicated books) → files (primary / duplicate / alternate_format / member) → blocks (extracted text in reading order, each with its locator: page for pdf, chapter + offset for reflowable). this is what phase 2 chunks and embeds.

## roadmap

- **phase 0 — corpus survey. ✓ implemented.** walks the dump: histogram of formats, sizes, text-layer presence, duplicate rate, languages. decides the ocr scope and turns the sizing above from napkin math into numbers. see usage above; probes live in `src/kontext/survey/`, assumptions for the estimates in `estimates.py`.
- **phase 1 — extraction core. ✓ implemented.** canonical book model (works → files → blocks with locators) in `kontext.db`; extractors for pdf/epub/txt/html/fb2, exploded html books, and mobi/office-doc conversion via calibre/libreoffice (auto-retried once the tools are installed). work-level dedup via minhash over the extracted text. hard-won details from running it on the real dump:
  - ocr-pending (mixed) pdfs are excluded from dedup: chapter scans of one volume can share an identical partial text layer (front matter only), which falsely merged 11 different futurism papers before the guard existed. a length-ratio check (≥0.5) guards the same trap generally.
  - exploded html detection requires machine-ish sequential filenames (`page_1.html`...); a folder of descriptively named htmls is a collection of individual articles, one work each. the book title comes from the nearest non-generic ancestor directory (`files/`, `book/` etc. are skipped).
  - junk embedded titles ("Dokument1", "Microsoft Word - ...") fall back to the filename.
- **phase 2 — search core.** chunker, embedder, qdrant, and a `kontext search "..."` cli. build a golden set of ~30 query→expected-passage pairs; tune chunk size and hybrid weights against it before touching any ui.
- **phase 3 — web app.** fastapi retrieval api + htmx ui: excerpt highlighting, locators, download endpoint, lan deployment.
- **phase 4 — full corpus.** ocr queue for the scans, mobi/azw3/djvu conversion, watch-folder ingest, job dashboard, backups (sqlite + qdrant snapshots are tiny next to the dump itself).
- **phase 5 — rag layer (optional, later).** llm answers with citations on top of the retrieval api — local model on the gpu or a hosted api. possibly reranker fine-tuning from own click logs. explicitly out of scope until search itself is good.

## open questions

- what share of the dump is scanned, duplicated, drm'd, or stuck inside archives? → **run `kontext survey` on the dump**; the report answers all of these.
- does current pytorch still ship kernels for the titan x, or does phase 2 need a pinned older build? (see sizing caveat above)
- backup situation for the dump itself?
