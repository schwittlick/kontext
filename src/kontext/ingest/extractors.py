"""format extractors: file(s) -> Extraction (blocks with locators).

granularity per format:
  - pdf: one block per page (locator = page number)
  - epub / html / fb2: one block per paragraph (locator = chapter + char offset)
  - txt: one block per paragraph (char offset)
mobi and office documents are converted with external tools (calibre,
libreoffice) when available, then routed through the extractors above.
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree

import pymupdf

from kontext.ingest.clean import (
    clean_page_text, count_words, normalize_ws, quality_score, split_paragraphs,
)
from kontext.model import Block, Extraction
from kontext.survey.probes import (
    _decode, _epub_opf_name, _norm_lang, _title_from_filename, detect_language,
)

# embedded metadata titles that are export artifacts, not titles
_JUNK_TITLE_RE = re.compile(
    r"^(microsoft (word|powerpoint)|dokument\d*|document\d*|doc\d+|untitled"
    r"|unbenannt|new document|scan\d*|print|acrobat|slide ?1|präsentation\d*)\b",
    re.IGNORECASE,
)

PDF_MIN_WORDS_PER_PAGE = 10
MIN_BLOCK_CHARS = 3
LANG_MIN_CONFIDENCE = 0.85
QUALITY_SAMPLE_CHARS = 12_000
CONVERT_TIMEOUT_S = 240


# ---------------------------------------------------------------- html

_BLOCK_TAGS = {
    "p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote",
    "td", "th", "dt", "dd", "pre", "tr", "br", "hr", "section", "article",
    "figcaption",
}
_SKIP_TAGS = {"script", "style", "head", "noscript", "svg", "template"}
_HEADING_TAGS = {"h1", "h2", "h3"}


class _BlockParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.first_heading: str | None = None
        self._buf: list[str] = []
        self._skip_depth = 0
        self._heading_buf: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        if tag in _HEADING_TAGS and self.first_heading is None:
            self._heading_buf = []

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        if tag in _HEADING_TAGS and self._heading_buf is not None:
            heading = normalize_ws(" ".join(self._heading_buf))
            if heading:
                self.first_heading = heading
            self._heading_buf = None

    def handle_data(self, data):
        if self._skip_depth:
            return
        self._buf.append(data)
        if self._heading_buf is not None:
            self._heading_buf.append(data)

    def _flush(self):
        text = normalize_ws("".join(self._buf))
        self._buf = []
        if len(text) > MIN_BLOCK_CHARS:
            self.blocks.append(text)

    def close(self):
        super().close()
        self._flush()


def html_blocks(raw: str) -> tuple[list[str], str | None]:
    parser = _BlockParser()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        pass  # keep whatever was parsed before the markup broke
    return parser.blocks, parser.first_heading


def _chapter_blocks(paragraphs: list[str], chapter_idx: int,
                    chapter_title: str | None, start_seq: int) -> list[Block]:
    out = []
    offset = 0
    for text in paragraphs:
        out.append(Block(
            seq=start_seq + len(out), text=text, word_count=count_words(text),
            chapter_idx=chapter_idx, chapter_title=chapter_title, char_offset=offset,
        ))
        offset += len(text) + 1
    return out


# ---------------------------------------------------------------- pdf

def extract_pdf(path: str) -> Extraction:
    ext = Extraction(status="extracted", source_format="pdf")
    with pymupdf.open(path) as doc:
        meta = doc.metadata or {}
        ext.title = (meta.get("title") or "").strip() or None
        ext.author = (meta.get("author") or "").strip() or None
        seq = 0
        for page in doc:
            text = clean_page_text(page.get_text("text"))
            if count_words(text) < PDF_MIN_WORDS_PER_PAGE:
                continue
            ext.blocks.append(Block(
                seq=seq, text=text, word_count=count_words(text), page=page.number + 1,
            ))
            seq += 1
    if not ext.blocks:
        ext.status = "failed"
        ext.error = "no extractable text"
    return ext


# ---------------------------------------------------------------- epub

def extract_epub(path: str) -> Extraction:
    ext = Extraction(status="extracted", source_format="epub")
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        chapter_names = _spine_order(zf, names, ext)
        if not chapter_names:
            chapter_names = sorted(
                (n for n in names if re.search(r"\.x?html?$", n, re.IGNORECASE)),
                key=_natural_key,
            )
        seq = 0
        for ci, name in enumerate(chapter_names):
            try:
                raw = zf.read(name).decode("utf-8", errors="replace")
            except Exception:
                continue
            paragraphs, heading = html_blocks(raw)
            blocks = _chapter_blocks(paragraphs, ci, heading, seq)
            ext.blocks.extend(blocks)
            seq += len(blocks)
    if not ext.blocks:
        ext.status = "failed"
        ext.error = "no extractable text"
    return ext


def _spine_order(zf: zipfile.ZipFile, names: set[str], ext: Extraction) -> list[str]:
    """reading order from the opf spine; also picks up dc metadata."""
    opf_name = _epub_opf_name(zf)
    if not opf_name or opf_name not in names:
        return []
    try:
        root = ElementTree.fromstring(zf.read(opf_name))
    except Exception:
        return []

    manifest: dict[str, tuple[str, str]] = {}
    spine: list[str] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        text = (el.text or "").strip()
        if tag == "item" and el.get("id") and el.get("href"):
            manifest[el.get("id")] = (el.get("href"), el.get("media-type", ""))
        elif tag == "itemref" and el.get("idref"):
            spine.append(el.get("idref"))
        elif tag == "title" and text and not ext.title:
            ext.title = text[:300]
        elif tag == "creator" and text and not ext.author:
            ext.author = text[:300]
        elif tag == "language" and text and not ext.language:
            ext.language = _norm_lang(text)

    opf_dir = posixpath.dirname(opf_name)
    ordered = []
    for idref in spine:
        href, media = manifest.get(idref, (None, ""))
        if not href or ("html" not in media and "xml" not in media):
            continue
        name = posixpath.normpath(posixpath.join(opf_dir, unquote(href)))
        if name in names:
            ordered.append(name)
    return ordered


# ---------------------------------------------------------------- text-like

def extract_txt(path: str) -> Extraction:
    ext = Extraction(status="extracted", source_format="txt")
    raw = _decode(Path(path).read_bytes())
    ext.blocks = _chapter_blocks(split_paragraphs(raw), 0, None, 0)
    for b in ext.blocks:
        b.chapter_idx = None  # a plain text file has no chapters
    if not ext.blocks:
        ext.status = "failed"
        ext.error = "no extractable text"
    return ext


def extract_html_file(path: str, fmt: str = "html") -> Extraction:
    ext = Extraction(status="extracted", source_format=fmt)
    raw = _decode(Path(path).read_bytes())
    paragraphs, heading = html_blocks(raw)
    ext.title = heading
    ext.blocks = _chapter_blocks(paragraphs, 0, heading, 0)
    if not ext.blocks:
        ext.status = "failed"
        ext.error = "no extractable text"
    return ext


def extract_exploded_html(paths: list[str]) -> Extraction:
    """a folder of chapter files is one book; files arrive natural-sorted."""
    ext = Extraction(status="extracted", source_format="html")
    seq = 0
    for ci, p in enumerate(paths):
        raw = _decode(Path(p).read_bytes())
        paragraphs, heading = html_blocks(raw)
        blocks = _chapter_blocks(paragraphs, ci, heading or Path(p).stem, seq)
        ext.blocks.extend(blocks)
        seq += len(blocks)
    if not ext.blocks:
        ext.status = "failed"
        ext.error = "no extractable text"
    return ext


def _natural_key(name: str):
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", name)]


# ---------------------------------------------------------------- conversion

_PRESENTATION_EXTS = {"ppt", "pptx", "odp"}


def _converter_env() -> dict[str, str]:
    """environment for external converters, stripped of our virtualenv.
    calibre/libreoffice spawn python helpers themselves; with the venv on
    PATH they resolve its interpreter (no system site-packages) and crash
    on their own imports."""
    env = os.environ.copy()
    for var in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(var, None)
    prefix = sys.prefix
    env["PATH"] = os.pathsep.join(
        p for p in env.get("PATH", "").split(os.pathsep)
        if p and not p.startswith(prefix)
    )
    return env


def convert_and_extract(path: str, fmt: str) -> Extraction:
    if fmt == "mobi":
        return _convert_with_calibre(path)
    if fmt == "doc":
        return _convert_with_libreoffice(path)
    return Extraction(status="needs_conversion", source_format=fmt,
                      error=f"no converter for {fmt}")


def _convert_with_calibre(path: str) -> Extraction:
    tool = shutil.which("ebook-convert")
    if not tool:
        return Extraction(status="needs_conversion", source_format="mobi",
                          error="ebook-convert not found (install calibre)")
    with tempfile.TemporaryDirectory(prefix="kontext-conv-") as tmp:
        out = Path(tmp) / "converted.epub"
        proc = subprocess.run(
            [tool, path, str(out)],
            capture_output=True, text=True, timeout=CONVERT_TIMEOUT_S,
            env=_converter_env(),
        )
        if proc.returncode != 0 or not out.exists():
            return Extraction(status="failed", source_format="mobi",
                              error=f"ebook-convert failed: {proc.stderr[-300:]}")
        ext = extract_epub(str(out))
        ext.source_format = "mobi"
        return ext


def _convert_with_libreoffice(path: str) -> Extraction:
    tool = shutil.which("soffice") or shutil.which("libreoffice")
    ext_name = Path(path).suffix.lower().lstrip(".")
    if ext_name == "chm" or not tool:
        reason = "chm is unsupported" if ext_name == "chm" else "libreoffice not found"
        return Extraction(status="needs_conversion", source_format="doc",
                          error=f"{reason} (convert manually or install libreoffice)")
    # impress has no text export filter, so presentations go through pdf
    presentation = ext_name in _PRESENTATION_EXTS
    target = "pdf" if presentation else "txt:Text (encoded):UTF8"
    with tempfile.TemporaryDirectory(prefix="kontext-conv-") as tmp:
        proc = subprocess.run(
            [
                tool, "--headless",
                # unique profile dir so parallel soffice instances don't fight
                f"-env:UserInstallation=file://{tmp}/profile",
                "--convert-to", target, "--outdir", tmp, path,
            ],
            capture_output=True, text=True, timeout=CONVERT_TIMEOUT_S,
            env=_converter_env(),
        )
        out = Path(tmp) / (Path(path).stem + (".pdf" if presentation else ".txt"))
        if proc.returncode != 0 or not out.exists():
            return Extraction(status="failed", source_format="doc",
                              error=f"libreoffice convert failed: {proc.stderr[-300:]}")
        ext = extract_pdf(str(out)) if presentation else extract_txt(str(out))
        ext.source_format = "doc"
        return ext


# ---------------------------------------------------------------- dispatch

def extract_task(task: dict) -> dict:
    """worker entry point. task: {kind, paths, format, title, author, language}.
    returns {extraction, minhash} -- minhash is computed here so the cpu work
    stays in the worker processes."""
    kind = task["kind"]
    try:
        if kind == "pdf":
            ext = extract_pdf(task["paths"][0])
            ext.needs_ocr = task.get("needs_ocr", False)
        elif kind == "epub":
            ext = extract_epub(task["paths"][0])
        elif kind == "txt":
            ext = extract_txt(task["paths"][0])
        elif kind in ("html", "fb2"):
            ext = extract_html_file(task["paths"][0], fmt=kind)
        elif kind == "exploded_html":
            ext = extract_exploded_html(task["paths"])
        elif kind == "convert":
            ext = convert_and_extract(task["paths"][0], task["format"])
        else:
            ext = Extraction(status="failed", error=f"unknown task kind {kind}")
    except Exception as exc:
        ext = Extraction(status="failed", error=f"{type(exc).__name__}: {exc}")

    _finalize(ext, task)
    minhash = None
    if ext.status == "extracted" and not ext.needs_ocr:
        from kontext.ingest.dedup import MIN_WORDS_FOR_DEDUP, signature_bytes

        if ext.word_count >= MIN_WORDS_FOR_DEDUP:
            minhash = signature_bytes(b.text for b in ext.blocks)
    return {"extraction": ext, "minhash": minhash}


def _finalize(ext: Extraction, task: dict) -> None:
    """fill title/author from the survey where extraction found none; detect
    language from the actual text, falling back to survey/opf values."""
    ext.title = _pick_title(ext.title, task.get("title"), task["paths"][0])
    ext.author = ext.author or task.get("author")
    if not ext.blocks:
        ext.language = ext.language or task.get("language")
        return
    sample_parts: list[str] = []
    total = 0
    for b in ext.blocks:
        sample_parts.append(b.text)
        total += len(b.text)
        if total >= QUALITY_SAMPLE_CHARS:
            break
    sample = " ".join(sample_parts)[:QUALITY_SAMPLE_CHARS]
    ext.quality = round(quality_score(sample), 3)
    lang, conf = detect_language(sample)
    if lang and conf and conf >= LANG_MIN_CONFIDENCE:
        ext.language = lang
    else:
        ext.language = ext.language or task.get("language")


def _pick_title(extracted: str | None, survey: str | None, path: str) -> str:
    for candidate in (extracted, survey):
        if candidate and not _JUNK_TITLE_RE.match(candidate.strip()):
            return candidate
    return _title_from_filename(Path(path))
