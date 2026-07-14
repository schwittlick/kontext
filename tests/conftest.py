"""synthetic ebook fixtures: tiny but structurally real files."""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pymupdf
import pytest

ENGLISH = (
    "The history of the printed book begins long before the printing press. "
    "Scribes copied manuscripts by hand for centuries, and every copy carried "
    "small differences introduced by tired eyes and wandering attention. "
    "When movable type arrived, the economics of knowledge changed completely: "
    "a single workshop could produce more identical copies in a month than a "
    "monastery could produce in a decade. Libraries grew from locked chests "
    "into public rooms, and the idea that an ordinary person might own books "
    "stopped being absurd. "
)


def make_text_pdf(path: Path, pages: int = 4, text: str = ENGLISH) -> None:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 780), text * 2, fontsize=11)
    doc.save(path)
    doc.close()


def make_vector_page_pdf(path: Path, pages: int = 2) -> None:
    """textless, rasterless pages whose content is vector drawings --
    like scans rendered to vector or text converted to outlines."""
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        for i in range(1200):
            page.draw_line((i % 500 + 10, 10), ((i * 7) % 500 + 10, 800))
    doc.save(path, deflate=False)
    doc.close()


def make_scanned_pdf(path: Path, pages: int = 3) -> None:
    """pages that contain only a raster image, like a book scan."""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 64), False)
    pix.clear_with(128)
    png = pix.tobytes("png")
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_image(pymupdf.Rect(0, 0, 595, 842), stream=png)
    doc.save(path)
    doc.close()


_CONTAINER_XML = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

_OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:language>{language}</dc:language>
  </metadata>
  <manifest>
{manifest}
  </manifest>
  <spine>
{spine}
  </spine>
</package>"""

_DRM_ENCRYPTION = """<?xml version="1.0"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:CipherData><enc:CipherReference URI="{uri}"/></enc:CipherData>
  </enc:EncryptedData>
</encryption>"""


def make_epub(
    path: Path,
    title: str = "A Study In Retrieval",
    author: str = "Jane Doe",
    language: str = "en",
    chapters: int = 3,
    body_text: str = ENGLISH,
    chapter_names: list[str] | None = None,
    drm: bool = False,
    font_obfuscation_only: bool = False,
) -> None:
    names = chapter_names or [f"chapter{i + 1}.xhtml" for i in range(chapters)]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", _CONTAINER_XML)
        if drm:
            zf.writestr("META-INF/encryption.xml", _DRM_ENCRYPTION.format(uri="OEBPS/chapter1.xhtml"))
        elif font_obfuscation_only:
            zf.writestr("META-INF/encryption.xml", _DRM_ENCRYPTION.format(uri="OEBPS/fonts/serif.ttf"))
        manifest = "\n".join(
            f'    <item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>'
            for i, name in enumerate(names)
        )
        spine = "\n".join(f'    <itemref idref="c{i}"/>' for i in range(len(names)))
        zf.writestr("OEBPS/content.opf", _OPF.format(
            title=title, author=author, language=language, manifest=manifest, spine=spine,
        ))
        body = "".join(f"<p>{body_text}</p>" for _ in range(5))
        for i, name in enumerate(names):
            zf.writestr(
                f"OEBPS/{name}",
                f"<html><head><title>ch {i + 1}</title></head>"
                f"<body><h2>Chapter {i + 1}</h2><p>chapter marker {i}</p>{body}</body></html>",
            )


def make_mobi(
    path: Path,
    title: str = "Palm Sized Stories",
    text_length: int = 120_000,
    encryption: int = 0,
    locale: int = 0x09,  # english
) -> None:
    """minimal but structurally correct palm database with a mobi header."""
    rec0_off = 100
    title_bytes = title.encode("utf-8")

    palmdoc = struct.pack(">HHIHHHH", 2, 0, text_length, 1, 4096, encryption, 0)
    mobi = bytearray(232)
    mobi[0:4] = b"MOBI"
    struct.pack_into(">I", mobi, 4, 232)          # header length
    struct.pack_into(">I", mobi, 8, 2)            # mobi type: book
    struct.pack_into(">I", mobi, 12, 65001)       # utf-8
    struct.pack_into(">I", mobi, 68, 16 + 232)    # full name offset (from record 0)
    struct.pack_into(">I", mobi, 72, len(title_bytes))
    struct.pack_into(">I", mobi, 76, locale)
    record0 = palmdoc + bytes(mobi) + title_bytes + b"\x00\x00"

    header = bytearray(rec0_off)
    header[0:9] = b"test.mobi"
    header[60:68] = b"BOOKMOBI"
    struct.pack_into(">H", header, 76, 2)               # record count
    struct.pack_into(">I", header, 78, rec0_off)        # record 0 offset
    struct.pack_into(">I", header, 86, rec0_off + len(record0))

    path.write_bytes(bytes(header) + record0 + b"fake text record")


SECOND_TEXT = (
    "Gardens change with the seasons in ways that reward patient observation. "
    "In early spring the soil warms slowly, and the first shoots appear along "
    "the southern wall where sunlight lingers longest. By midsummer the beds "
    "are crowded and the gardener's work shifts from encouragement to "
    "restraint, thinning and pruning so that air can move between the plants. "
    "Autumn is the season of accounting, when seed is gathered and the year's "
    "successes and failures are written plainly in the beds themselves. "
)

THIRD_TEXT = (
    "Every debugging session begins with a claim that cannot yet be trusted. "
    "The program misbehaves, someone describes the symptom, and the "
    "description is already an interpretation shaped by what they expected "
    "to happen. Good debuggers treat the report as testimony rather than "
    "evidence: useful for orientation, dangerous to build on. They reproduce "
    "the failure with their own hands before they reason about causes, "
    "because a fault that cannot be reproduced cannot be understood. "
)

FOURTH_TEXT = (
    "A city street teaches its residents without ever holding a lesson. "
    "Corners where shopkeepers linger become safe by observation, and the "
    "pavement carries a rhythm of deliveries, school runs and evening "
    "strolls that everyone reads without noticing. When planners replace a "
    "dense street with towers set in lawns, the watchers disappear and the "
    "choreography stops, and what remains is distance that must be crossed "
    "rather than a place where anyone would choose to stand. "
)


def make_exploded_html_book(root: Path, chapters: int = 8, text: str = ENGLISH) -> Path:
    """a directory that is one book exploded into per-chapter html files."""
    d = root / "An Exploded Treatise"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(chapters):
        (d / f"chapter_{i + 1:02d}.html").write_text(
            f"<html><body><h1>Part {i + 1}</h1>"
            + "".join(f"<p>{text} (section {i + 1}.{j})</p>" for j in range(4))
            + "</body></html>"
        )
    (d / "style.css").write_text("body { margin: 0; }")
    return d


def make_plain_zip_with_ebooks(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("books/inner1.epub", b"not really an epub, just counted")
        zf.writestr("books/inner2.pdf", b"not really a pdf, just counted")
        zf.writestr("notes.nfo", b"scene release notes")


@pytest.fixture
def dump(tmp_path: Path) -> Path:
    """a miniature unstructured ebook dump exercising every probe path."""
    root = tmp_path / "dump"
    (root / "a" / "b").mkdir(parents=True)

    make_text_pdf(root / "a" / "clean text book.pdf")
    make_scanned_pdf(root / "a" / "old scan.pdf")
    make_epub(root / "ebook one.epub")
    make_epub(root / "a" / "b" / "german book.epub", title="Der Prozess", author="F. Kafka", language="de-DE")
    make_mobi(root / "kindle book.mobi")
    make_mobi(root / "a" / "locked.azw3", encryption=2)
    (root / "plain notes.txt").write_text(ENGLISH * 10)
    (root / "a" / "broken.pdf").write_bytes(b"%PDF-1.4 then it all goes wrong")
    make_plain_zip_with_ebooks(root / "bundle.zip")

    # exact duplicate of an existing file
    dup = root / "a" / "ebook one (copy).epub"
    dup.write_bytes((root / "ebook one.epub").read_bytes())
    return root
