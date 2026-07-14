"""file discovery over the (read-only) dump."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, NamedTuple


class FoundFile(NamedTuple):
    path: str
    size: int
    mtime_ns: int


def walk_files(root: Path, follow_symlinks: bool = False) -> Iterator[FoundFile]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full, follow_symlinks=follow_symlinks)
            except OSError:
                continue
            if not os.path.isfile(full):
                continue
            yield FoundFile(full, st.st_size, st.st_mtime_ns)
