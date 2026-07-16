"""ocr engine: scanned pdf/djvu -> Extraction, one block per page.

tesseract does the reading; pages are rasterized with pymupdf (pdf) or
ddjvu (djvu). parallelism happens at the book level (one process-pool
worker per book, pages sequential inside it), so each tesseract call is
pinned to one thread -- otherwise every worker spawns its own openmp
team and they trample each other.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pymupdf

from kontext.ingest.clean import clean_page_text, count_words
from kontext.ingest.extractors import PDF_MIN_WORDS_PER_PAGE, _finalize
from kontext.model import Block, Extraction

OCR_DPI = 300
MAX_PAGE_PX = 4500     # cap raster size for oversized scan pages
PAGE_TIMEOUT_S = 180
DJVU_TIMEOUT_S = 600

# iso 639-1 (survey/catalog codes) -> tesseract traineddata names
TESS_LANGS = {
    "en": "eng", "fr": "fra", "es": "spa", "de": "deu", "it": "ita",
    "pt": "por", "pl": "pol", "la": "lat", "ru": "rus", "nl": "nld",
    "zh": "chi_sim", "ja": "jpn", "el": "ell", "cs": "ces", "qu": "que",
}
FALLBACK_LANG = "eng"


def available_langs() -> set[str]:
    """installed tesseract language packs (empty set when tesseract is missing)."""
    try:
        proc = subprocess.run(
            ["tesseract", "--list-langs"], capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    out = proc.stdout if proc.stdout.strip() else proc.stderr  # 4.x lists to stderr
    return {
        line.strip() for line in out.splitlines()
        if line.strip() and not line.lower().startswith("list of")
    }


def pick_lang(language: str | None, available: set[str]) -> str:
    want = TESS_LANGS.get((language or "").lower(), FALLBACK_LANG)
    return want if want in available else FALLBACK_LANG


def _tesseract(image: bytes, lang: str) -> str:
    env = os.environ.copy()
    env["OMP_THREAD_LIMIT"] = "1"  # book-level parallelism only
    proc = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", lang],
        input=image, capture_output=True, timeout=PAGE_TIMEOUT_S, env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"tesseract: {proc.stderr.decode(errors='replace')[-200:]}")
    return proc.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- pdf

def _page_png(page: pymupdf.Page) -> bytes:
    zoom = OCR_DPI / 72
    longest = max(page.rect.width, page.rect.height) * zoom
    if longest > MAX_PAGE_PX:
        zoom *= MAX_PAGE_PX / longest
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY)
    return pix.tobytes("png")


def ocr_pdf(path: str, lang: str) -> Extraction:
    ext = Extraction(status="extracted", source_format="pdf")
    with pymupdf.open(path) as doc:
        meta = doc.metadata or {}
        ext.title = (meta.get("title") or "").strip() or None
        ext.author = (meta.get("author") or "").strip() or None
        seq = 0
        for page in doc:
            # a scanned book can still have a few real-text pages (title,
            # colophon); an existing text layer beats re-reading pixels
            text = clean_page_text(page.get_text("text"))
            if count_words(text) < PDF_MIN_WORDS_PER_PAGE:
                text = clean_page_text(_tesseract(_page_png(page), lang))
            if count_words(text) < PDF_MIN_WORDS_PER_PAGE:
                continue
            ext.blocks.append(Block(
                seq=seq, text=text, word_count=count_words(text), page=page.number + 1,
            ))
            seq += 1
    if not ext.blocks:
        ext.status = "failed"
        ext.error = "ocr produced no text"
    return ext


# ---------------------------------------------------------------- djvu

def _djvu_pages(path: str) -> int:
    proc = subprocess.run(
        ["djvused", "-e", "n", path], capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"djvused: {proc.stderr[-200:]}")
    return int(proc.stdout.strip())


def _djvu_text_layer(path: str) -> list[str]:
    """hidden text layer, one entry per page (djvutxt separates pages with
    form feeds). empty list when there is none worth using."""
    if shutil.which("djvutxt") is None:
        return []
    try:
        proc = subprocess.run(["djvutxt", path],
                              capture_output=True, timeout=DJVU_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return []
    if proc.returncode != 0:
        return []
    return proc.stdout.decode("utf-8", errors="replace").split("\f")


def _ddjvu_page(path: str, page: int) -> bytes:
    proc = subprocess.run(
        ["ddjvu", "-format=pgm", f"-page={page}", path, "-"],
        capture_output=True, timeout=PAGE_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ddjvu p.{page}: {proc.stderr.decode(errors='replace')[-200:]}")
    return proc.stdout


def ocr_djvu(path: str, lang: str) -> Extraction:
    ext = Extraction(status="extracted", source_format="djvu")
    if shutil.which("ddjvu") is None:
        return Extraction(status="failed", source_format="djvu",
                          error="ddjvu not found (install djvulibre)")
    n_pages = _djvu_pages(path)

    # some djvu files carry a hidden text layer from an earlier ocr pass
    layer = _djvu_text_layer(path)
    layer_words = sum(count_words(p) for p in layer)
    use_layer = layer_words >= PDF_MIN_WORDS_PER_PAGE * max(1, n_pages)

    seq = 0
    for page_no in range(1, n_pages + 1):
        if use_layer:
            text = clean_page_text(layer[page_no - 1]) if page_no <= len(layer) else ""
        else:
            text = clean_page_text(_tesseract(_ddjvu_page(path, page_no), lang))
        if count_words(text) < PDF_MIN_WORDS_PER_PAGE:
            continue
        ext.blocks.append(Block(
            seq=seq, text=text, word_count=count_words(text), page=page_no,
        ))
        seq += 1
    if not ext.blocks:
        ext.status = "failed"
        ext.error = "ocr produced no text"
    return ext


# ---------------------------------------------------------------- dispatch

def ocr_task(task: dict) -> dict:
    """worker entry point. task: {book_id, paths, format, tess_lang, title,
    author, language}. mirrors extractors.extract_task: language/quality are
    detected from the ocr'd text, minhash computed here so the cpu work
    stays in the worker."""
    try:
        if task["format"] == "djvu":
            ext = ocr_djvu(task["paths"][0], task["tess_lang"])
        else:
            ext = ocr_pdf(task["paths"][0], task["tess_lang"])
    except Exception as exc:
        ext = Extraction(status="failed", error=f"{type(exc).__name__}: {exc}")

    _finalize(ext, task)
    minhash = None
    if ext.status == "extracted":
        from kontext.ingest.dedup import MIN_WORDS_FOR_DEDUP, signature_bytes

        if ext.word_count >= MIN_WORDS_FOR_DEDUP:
            minhash = signature_bytes(b.text for b in ext.blocks)
    return {"book_id": task["book_id"], "extraction": ext, "minhash": minhash}
