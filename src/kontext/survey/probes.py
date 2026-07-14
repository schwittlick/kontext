"""per-file probing: format detection and cheap, non-destructive inspection.

every probe answers the questions later phases need:
  - can text be extracted directly, or is ocr needed? (pdf text layer)
  - is the file locked? (drm, password)
  - how many words would it contribute to the index?
  - what language, title, author?

probes never write anything and read as little of the file as possible.
"""

from __future__ import annotations

import html as html_mod
import json
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

import pymupdf

pymupdf.TOOLS.mupdf_display_errors(False)

# bump when probe logic changes so `kontext survey` re-probes existing rows
PROBE_VERSION = 3

CHARS_PER_WORD = 6.0

# how many pdf pages to sample, spread evenly through the document
PDF_SAMPLE_PAGES = 8
# avg extracted chars/page above this -> real text layer
PDF_TEXT_CHARS_PER_PAGE = 150
# below this -> no usable text layer
PDF_SCANNED_CHARS_PER_PAGE = 25
# textless pages above this size carry rendered/vector content -> ocr scope;
# a genuinely blank page is well under this
PDF_RENDERED_BYTES_PER_PAGE = 15_000

TEXT_EXTS = {"txt", "text", "md", "markdown", "rst"}
HTML_EXTS = {"html", "htm", "xhtml"}
COMIC_EXTS = {"cbz", "cbr"}
# office/help documents: text content, but need conversion in phase 1
DOC_EXTS = {"doc", "docx", "rtf", "odt", "ppt", "pptx", "odp", "chm"}

# windows lcid primary language codes as used in mobi headers
MOBI_LANGUAGES = {
    0x04: "zh", 0x07: "de", 0x09: "en", 0x0A: "es", 0x0C: "fr",
    0x10: "it", 0x11: "ja", 0x13: "nl", 0x15: "pl", 0x16: "pt",
    0x19: "ru", 0x1D: "sv",
}

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


@dataclass
class ProbeResult:
    format: str = "other"
    status: str = "ok"  # ok | corrupt | encrypted | drm | unsupported | error
    pages: int | None = None
    text_class: str | None = None  # text | scanned | mixed | empty | unknown
    chars_per_page: float | None = None
    word_estimate: int | None = None
    language: str | None = None
    lang_confidence: float | None = None
    title: str | None = None
    author: str | None = None
    contains: str | None = None  # json summary for archives
    error: str | None = None
    sample_text: str = field(default="", repr=False)  # for language detection, not stored


# ---------------------------------------------------------------- language

_langid = None


def detect_language(text: str) -> tuple[str | None, float | None]:
    """detect language of a text sample; returns (iso-639-1, confidence 0..1)."""
    global _langid
    text = text.strip()
    if len(text) < 80:
        return None, None
    if _langid is None:
        try:
            from py3langid.langid import MODEL_FILE, LanguageIdentifier

            _langid = LanguageIdentifier.from_pickled_model(MODEL_FILE, norm_probs=True)
        except Exception:
            _langid = False
    if _langid is False:
        return None, None
    try:
        lang, conf = _langid.classify(text[:4000])
        return str(lang), float(conf)
    except Exception:
        return None, None


def _norm_lang(code: str | None) -> str | None:
    if not code:
        return None
    code = code.strip().lower().split("-")[0].split("_")[0]
    return code or None


# ---------------------------------------------------------------- detection

def detect_format(path: Path, head: bytes) -> str:
    ext = path.suffix.lower().lstrip(".")
    # extension wins over container magic here: cbr/cbz are rar/zip by magic
    # but semantically comics; docx/odt are zip but office documents
    if ext in COMIC_EXTS:
        return "comic"
    if ext in DOC_EXTS:
        return "doc"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "zip"  # refined to epub by the probe
    if head.startswith(b"AT&TFORM"):
        return "djvu"
    if head.startswith(b"Rar!"):
        return "rar"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    if len(head) >= 68 and head[60:68] in (b"BOOKMOBI", b"TEXtREAd"):
        return "mobi"

    if ext in TEXT_EXTS:
        return "txt"
    if ext in HTML_EXTS or b"<FictionBook" in head:
        return "fb2" if ext == "fb2" or b"<FictionBook" in head else "html"
    if ext == "fb2":
        return "fb2"
    if ext in COMIC_EXTS:
        return "comic"
    # trust the extension for formats whose magic we could not confirm --
    # the probe will flag them corrupt if they do not parse
    if ext in ("pdf", "epub", "mobi", "azw", "azw3", "djvu"):
        return {"azw": "mobi", "azw3": "mobi", "epub": "zip"}.get(ext, ext)
    return "other"


def probe_file(path: Path, size: int) -> ProbeResult:
    """dispatch to a format-specific probe; must never raise."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(128)
    except OSError as exc:
        return ProbeResult(status="error", error=f"unreadable: {exc}")

    if size == 0:
        return ProbeResult(status="corrupt", error="empty file")

    fmt = detect_format(path, head)
    try:
        if fmt == "pdf":
            result = probe_pdf(path, size)
        elif fmt == "zip":
            result = probe_zip(path)
        elif fmt == "mobi":
            result = probe_mobi(path, size)
        elif fmt == "djvu":
            result = probe_djvu(size)
        elif fmt in ("txt", "html", "fb2"):
            result = probe_textlike(path, size, fmt)
        elif fmt == "comic":
            result = ProbeResult(format="comic", status="unsupported", error="comic archive (images)")
        elif fmt == "doc":
            result = ProbeResult(format="doc", status="unsupported",
                                 error="office/help document, convert in phase 1 (libreoffice/pandoc)")
        elif fmt in ("rar", "7z"):
            result = ProbeResult(format=fmt, status="unsupported", error="compressed archive, extract before ingest")
        else:
            result = ProbeResult(format="other", status="unsupported")
    except Exception as exc:  # any parse failure means an unusable file, not a crash
        return ProbeResult(format=fmt, status="corrupt", error=f"{type(exc).__name__}: {exc}")

    if result.language is None and result.sample_text:
        result.language, result.lang_confidence = detect_language(result.sample_text)
    if not result.title:
        result.title = _title_from_filename(path)
    return result


def _title_from_filename(path: Path) -> str:
    stem = re.sub(r"[_.]+", " ", path.stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem[:200]


# ---------------------------------------------------------------- pdf

def probe_pdf(path: Path, size: int) -> ProbeResult:
    r = ProbeResult(format="pdf")
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        return ProbeResult(format="pdf", status="corrupt", error=f"{type(exc).__name__}: {exc}")

    with doc:
        if doc.needs_pass:
            r.status = "encrypted"
            r.error = "password protected"
            return r
        r.pages = doc.page_count
        if r.pages == 0:
            r.status = "corrupt"
            r.error = "zero pages"
            return r

        n = min(PDF_SAMPLE_PAGES, r.pages)
        step = max(1, r.pages // n)
        chars = 0
        images = 0
        samples: list[str] = []
        sampled = 0
        for idx in range(0, r.pages, step):
            if sampled >= n:
                break
            try:
                page = doc[idx]
                text = page.get_text("text").strip()
                chars += len(text)
                images += len(page.get_images())
                if text:
                    samples.append(text[:1500])
            except Exception:
                pass
            sampled += 1

        r.chars_per_page = chars / max(1, sampled)
        if r.chars_per_page >= PDF_TEXT_CHARS_PER_PAGE:
            r.text_class = "text"
        elif r.chars_per_page <= PDF_SCANNED_CHARS_PER_PAGE:
            # raster images or heavy textless pages (vector-rendered scans,
            # text drawn as paths) both need ocr; only truly blank is "empty"
            rendered = size / max(1, r.pages) >= PDF_RENDERED_BYTES_PER_PAGE
            r.text_class = "scanned" if images or rendered else "empty"
        else:
            r.text_class = "mixed"

        if r.text_class in ("text", "mixed"):
            r.word_estimate = int(r.chars_per_page * r.pages / CHARS_PER_WORD)
        else:
            r.word_estimate = 0  # ocr yield is estimated at report level from pages

        meta = doc.metadata or {}
        r.title = (meta.get("title") or "").strip() or None
        r.author = (meta.get("author") or "").strip() or None
        r.sample_text = " ".join(samples)[:5000]
    return r


# ---------------------------------------------------------------- epub / zip

def probe_zip(path: Path) -> ProbeResult:
    try:
        zf = zipfile.ZipFile(path)
    except Exception as exc:
        return ProbeResult(format="zip", status="corrupt", error=f"bad zip: {exc}")

    with zf:
        names = set(zf.namelist())
        is_epub = "META-INF/container.xml" in names
        if "mimetype" in names and not is_epub:
            try:
                is_epub = zf.read("mimetype").strip() == b"application/epub+zip"
            except Exception:
                is_epub = False
        if not is_epub:
            return _probe_plain_zip(zf, names)
        return _probe_epub(zf, names)


def _probe_plain_zip(zf: zipfile.ZipFile, names: set[str]) -> ProbeResult:
    counts: dict[str, int] = {}
    for name in names:
        ext = Path(name).suffix.lower().lstrip(".")
        if ext in ("pdf", "epub", "mobi", "azw", "azw3", "djvu", "txt", "fb2"):
            counts[ext] = counts.get(ext, 0) + 1
    r = ProbeResult(format="zip", status="unsupported")
    r.contains = json.dumps(counts) if counts else None
    r.error = "archive" + (f" containing ebooks: {r.contains}" if counts else "")
    return r


def _probe_epub(zf: zipfile.ZipFile, names: set[str]) -> ProbeResult:
    r = ProbeResult(format="epub")

    if "META-INF/encryption.xml" in names and _epub_has_real_drm(zf):
        r.status = "drm"
        r.error = "encryption.xml encrypts content files"
        return r

    opf_name = _epub_opf_name(zf)
    if opf_name and opf_name in names:
        _epub_metadata(zf, opf_name, r)

    html_names = [n for n in names if re.search(r"\.(x?html?|xml)$", n, re.IGNORECASE)
                  and "container.xml" not in n and not n.endswith(".opf")]
    total_html_bytes = 0
    for n in html_names:
        try:
            total_html_bytes += zf.getinfo(n).file_size
        except KeyError:
            pass

    # sample the largest few documents to measure the text-to-markup ratio
    sample_names = sorted(html_names, key=lambda n: zf.getinfo(n).file_size, reverse=True)[:3]
    raw_len = 0
    text_len = 0
    samples: list[str] = []
    for n in sample_names:
        try:
            raw = zf.read(n)[:40_000].decode("utf-8", errors="replace")
        except Exception:
            continue
        clean = _strip_html(raw)
        raw_len += len(raw)
        text_len += len(clean)
        samples.append(clean[:2000])

    ratio = (text_len / raw_len) if raw_len else 0.7
    r.word_estimate = int(total_html_bytes * ratio / CHARS_PER_WORD)
    r.text_class = "text" if r.word_estimate > 0 else "empty"
    r.sample_text = " ".join(samples)[:5000]
    return r


def _epub_has_real_drm(zf: zipfile.ZipFile) -> bool:
    """encryption.xml that only obfuscates fonts is not drm."""
    try:
        root = ElementTree.fromstring(zf.read("META-INF/encryption.xml"))
    except Exception:
        return True  # unreadable encryption info: assume the worst
    refs = [
        el.get("URI", "")
        for el in root.iter()
        if el.tag.rsplit("}", 1)[-1] == "CipherReference"
    ]
    if not refs:
        return False
    font_exts = (".ttf", ".otf", ".woff", ".woff2")
    return any(not ref.lower().endswith(font_exts) for ref in refs)


def _epub_opf_name(zf: zipfile.ZipFile) -> str | None:
    try:
        root = ElementTree.fromstring(zf.read("META-INF/container.xml"))
    except Exception:
        return None
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] == "rootfile":
            return el.get("full-path")
    return None


def _epub_metadata(zf: zipfile.ZipFile, opf_name: str, r: ProbeResult) -> None:
    try:
        root = ElementTree.fromstring(zf.read(opf_name))
    except Exception:
        return
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        text = (el.text or "").strip()
        if not text:
            continue
        if tag == "title" and not r.title:
            r.title = text[:300]
        elif tag == "creator" and not r.author:
            r.author = text[:300]
        elif tag == "language" and not r.language:
            r.language = _norm_lang(text)


def _strip_html(raw: str) -> str:
    raw = _SCRIPT_RE.sub(" ", raw)
    raw = _TAG_RE.sub(" ", raw)
    raw = html_mod.unescape(raw)
    return _WS_RE.sub(" ", raw).strip()


# ---------------------------------------------------------------- mobi / azw

def probe_mobi(path: Path, size: int) -> ProbeResult:
    """parse the palm database + mobi headers; enough for drm flag, text
    length, title and language without decompressing any content."""
    r = ProbeResult(format="mobi")
    with open(path, "rb") as fh:
        header = fh.read(86)
        if len(header) < 86:
            return ProbeResult(format="mobi", status="corrupt", error="truncated palm header")
        num_records = struct.unpack_from(">H", header, 76)[0]
        if num_records < 1:
            return ProbeResult(format="mobi", status="corrupt", error="no palm records")
        rec0_off = struct.unpack_from(">I", header, 78)[0]
        if rec0_off >= size:
            return ProbeResult(format="mobi", status="corrupt", error="record 0 offset out of range")

        fh.seek(rec0_off)
        rec0 = fh.read(4096)
        if len(rec0) < 16:
            return ProbeResult(format="mobi", status="corrupt", error="truncated record 0")

        text_length = struct.unpack_from(">I", rec0, 4)[0]
        encryption = struct.unpack_from(">H", rec0, 12)[0]
        if encryption != 0:
            r.status = "drm"
            r.error = f"mobipocket encryption type {encryption}"

        r.word_estimate = 0 if r.status == "drm" else int(text_length / CHARS_PER_WORD)
        r.text_class = "text" if r.status == "ok" else None

        if len(rec0) >= 96 and rec0[16:20] == b"MOBI":
            locale = struct.unpack_from(">I", rec0, 16 + 76)[0]
            r.language = MOBI_LANGUAGES.get(locale & 0xFF)
            name_off = struct.unpack_from(">I", rec0, 16 + 68)[0]
            name_len = struct.unpack_from(">I", rec0, 16 + 72)[0]
            if 0 < name_len < 1024 and rec0_off + name_off + name_len <= size:
                fh.seek(rec0_off + name_off)
                raw = fh.read(name_len)
                title = raw.decode("utf-8", errors="replace").strip("\x00 ").strip()
                if title:
                    r.title = title[:300]
    return r


# ---------------------------------------------------------------- djvu

def probe_djvu(size: int) -> ProbeResult:
    # detecting an embedded djvu text layer needs djvulibre; assume ocr is
    # required and estimate the page count from typical bytes/page
    r = ProbeResult(format="djvu")
    r.pages = max(1, size // 60_000)
    r.text_class = "scanned"
    r.word_estimate = 0
    return r


# ---------------------------------------------------------------- text-like

def probe_textlike(path: Path, size: int, fmt: str) -> ProbeResult:
    r = ProbeResult(format=fmt)
    with open(path, "rb") as fh:
        raw = fh.read(32_768)
    text = _decode(raw)
    if fmt in ("html", "fb2"):
        text = _strip_html(text)
        r.word_estimate = int(size * 0.7 / CHARS_PER_WORD)
    else:
        r.word_estimate = int(size / CHARS_PER_WORD)
    r.text_class = "text" if text.strip() else "empty"
    r.sample_text = text[:5000]
    return r


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", errors="replace")
