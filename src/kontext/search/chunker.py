"""blocks -> retrieval chunks.

target ~300 words with ~45 words of overlap, never crossing a chapter
boundary. sentences are the atoms: blocks bigger than the cap (pdf pages)
are split at sentence boundaries, small blocks (paragraphs) are packed
together. every chunk keeps the locator span of the blocks it came from.
"""

from __future__ import annotations

import re
from itertools import groupby

from kontext.model import Block

CHUNK_WORDS = 300      # flush threshold
CHUNK_MAX_WORDS = 450  # hard cap per chunk
OVERLAP_WORDS = 45     # tail of one chunk seeds the next

_SENT_RE = re.compile(r"(?<=[.!?…])\s+")


def chunk_book(blocks: list[Block]) -> list[dict]:
    chunks: list[dict] = []
    for _, chapter_blocks in groupby(blocks, key=lambda b: b.chapter_idx):
        _chunk_chapter(list(chapter_blocks), chunks)
    for seq, c in enumerate(chunks):
        c["seq"] = seq
    return chunks


def _chunk_chapter(blocks: list[Block], out: list[dict]) -> None:
    # atoms: (sentence text, word count, source block)
    atoms: list[tuple[str, int, Block]] = []
    for b in blocks:
        for sent in _SENT_RE.split(b.text):
            sent = sent.strip()
            if sent:
                atoms.append((sent, len(sent.split()), b))

    cur: list[tuple[str, int, Block]] = []
    cur_words = 0
    i = 0
    while i < len(atoms):
        text, words, block = atoms[i]
        if cur and cur_words + words > CHUNK_MAX_WORDS:
            _flush(cur, out)
            cur, cur_words = _overlap_tail(cur)
            if cur_words + words > CHUNK_MAX_WORDS:
                # oversize atom (unpunctuated ocr junk, tables): even the bare
                # overlap seed can't host it -- give it an empty chunk so it
                # lands alone, else flush/reseed/retry loops forever
                cur, cur_words = [], 0
            continue  # retry the same atom against the fresh chunk
        cur.append(atoms[i])
        cur_words += words
        i += 1
        if cur_words >= CHUNK_WORDS:
            _flush(cur, out)
            cur, cur_words = _overlap_tail(cur) if i < len(atoms) else ([], 0)
    if cur and not _only_overlap(cur, out):
        _flush(cur, out)


def _flush(atoms: list[tuple[str, int, Block]], out: list[dict]) -> None:
    text = " ".join(a[0] for a in atoms)
    blocks = [a[2] for a in atoms]
    pages = [b.page for b in blocks if b.page is not None]
    first = blocks[0]
    out.append({
        "seq": -1,  # numbered per book at the end
        "text": text,
        "word_count": sum(a[1] for a in atoms),
        "chapter_idx": first.chapter_idx,
        "chapter_title": first.chapter_title,
        "page_start": min(pages) if pages else None,
        "page_end": max(pages) if pages else None,
        "char_offset": first.char_offset,
    })


def _overlap_tail(atoms: list[tuple[str, int, Block]]) -> tuple[list, int]:
    tail: list[tuple[str, int, Block]] = []
    words = 0
    for atom in reversed(atoms):
        if words + atom[1] > OVERLAP_WORDS:
            break
        tail.insert(0, atom)
        words += atom[1]
    return tail, words


def _only_overlap(cur: list, out: list[dict]) -> bool:
    """a leftover consisting purely of the overlap seed adds nothing."""
    if not out:
        return False
    return " ".join(a[0] for a in cur) in out[-1]["text"]
