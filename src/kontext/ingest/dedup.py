"""work-level dedup: the same book in several formats/editions.

exact duplicates are caught earlier by sha256. here we compare the extracted
text itself: minhash over 5-word shingles, candidate lookup via lsh, verified
by estimated jaccard. the same work in epub and pdf typically lands around
0.7-0.9 (extraction noise: page headers, front matter), unrelated books far
below 0.1 -- the threshold sits in the wide gap between.
"""

from __future__ import annotations

import re
from typing import Iterable

import numpy as np
from datasketch import MinHash, MinHashLSH

NUM_PERM = 128
# pinned so signatures stored in the catalog stay decodable across runs
SCHEME = "affine32"
_HASH_DTYPE = np.uint32
SHINGLE_WORDS = 5
JACCARD_THRESHOLD = 0.6
MIN_WORDS_FOR_DEDUP = 500  # tiny texts are too similar to everything
# same work in two formats has comparable length; a shared-boilerplate match
# (e.g. identical front matter) usually does not
LENGTH_RATIO_MIN = 0.5

# note: books with needs_ocr are excluded from dedup entirely (see
# catalog.load_signatures and extract_task) -- their extractable text can be
# just the digitized front matter shared by every chapter scan of a volume,
# which merges different works. they become dedup-eligible after ocr.

# lower is better; which file becomes the work's primary text source
FORMAT_PRIORITY = {"epub": 0, "pdf": 1, "fb2": 2, "html": 3, "txt": 4, "mobi": 5, "doc": 6}

_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _signature(texts: Iterable[str]) -> MinHash:
    m = MinHash(num_perm=NUM_PERM, scheme=SCHEME)
    window: list[str] = []
    for text in texts:
        for token in _TOKEN_RE.findall(text.lower()):
            window.append(token)
            if len(window) >= SHINGLE_WORDS:
                m.update(" ".join(window[-SHINGLE_WORDS:]).encode("utf-8"))
    return m


def signature_bytes(texts: Iterable[str]) -> bytes:
    return _signature(texts).hashvalues.astype(_HASH_DTYPE).tobytes()


def _from_bytes(blob: bytes) -> MinHash:
    return MinHash(num_perm=NUM_PERM, scheme=SCHEME,
                   hashvalues=np.frombuffer(blob, dtype=_HASH_DTYPE).copy())


def find_merges(
    books: list[tuple[int, bytes, str, int]],  # (book_id, minhash, source_format, word_count)
    new_ids: set[int],
) -> list[tuple[int, int]]:
    """(canonical_id, alternate_id) pairs. only groups touching a newly
    ingested book are considered, so repeat runs stay incremental."""
    if not new_ids:
        return []
    sigs = {bid: _from_bytes(blob) for bid, blob, _, _ in books}
    meta = {bid: (fmt, wc) for bid, _, fmt, wc in books}

    lsh = MinHashLSH(threshold=JACCARD_THRESHOLD, num_perm=NUM_PERM)
    for bid, sig in sigs.items():
        lsh.insert(str(bid), sig)

    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    for bid in sorted(new_ids):
        if bid not in sigs:
            continue
        for key in lsh.query(sigs[bid]):
            other = int(key)
            if other == bid:
                continue
            wc_a, wc_b = meta[bid][1], meta[other][1]
            if min(wc_a, wc_b) / max(wc_a, wc_b, 1) < LENGTH_RATIO_MIN:
                continue
            if sigs[bid].jaccard(sigs[other]) >= JACCARD_THRESHOLD:
                union(bid, other)

    groups: dict[int, list[int]] = {}
    for bid in parent:
        groups.setdefault(find(bid), []).append(bid)

    merges: list[tuple[int, int]] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        canonical = min(
            members,
            key=lambda b: (FORMAT_PRIORITY.get(meta[b][0], 9), -meta[b][1], b),
        )
        merges.extend((canonical, b) for b in sorted(members) if b != canonical)
    return merges
