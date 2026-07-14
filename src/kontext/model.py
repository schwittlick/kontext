"""canonical document model shared by ingestion and (later) search.

a Book is one work. it can be backed by several files (formats, duplicates);
blocks -- the extracted text in reading order -- always come from the
primary file. every block carries a locator: page for fixed-layout sources,
chapter + char offset for reflowable ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Block:
    seq: int
    text: str
    word_count: int
    chapter_idx: int | None = None
    chapter_title: str | None = None
    page: int | None = None          # 1-based, pdf only
    char_offset: int | None = None   # within the chapter, reflowable only

    def locator(self) -> str:
        if self.page is not None:
            return f"p. {self.page}"
        if self.chapter_idx is not None:
            title = self.chapter_title or f"chapter {self.chapter_idx + 1}"
            return title
        return "text"


@dataclass
class Extraction:
    """result of extracting one candidate book (one file or one exploded dir)."""
    status: str                      # extracted | awaiting_ocr | needs_conversion | failed
    title: str | None = None
    author: str | None = None
    language: str | None = None
    source_format: str | None = None
    needs_ocr: bool = False          # mixed pdfs: text extracted, ocr would add more
    quality: float | None = None     # 0..1 plausibility of the extracted text
    blocks: list[Block] = field(default_factory=list)
    error: str | None = None

    @property
    def word_count(self) -> int:
        return sum(b.word_count for b in self.blocks)
