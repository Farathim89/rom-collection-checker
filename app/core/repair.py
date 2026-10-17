"""Trimmed and overdumped ROMs: a dump whose checksum isn't in the list is often a good dump with its empty end
cut off ("trimmed": DS, GBA, N64 tools did that to save space) or with empty space added (an overdump). Padding
it back to a size the list has, or cutting off its padding, gives the real dump when that checksum is listed.
Only padding is ever added or removed (0xFF / 0x00 bytes), never data."""
from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path

from .hashing import Tools, variants

LIMIT = 128 << 20                    # cartridges only: no point reading discs whole
_PADS = (b"\xff", b"\x00")


def _title(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"\s*[\(\[].*", "", Path(name).stem).lower())


def item_bytes(path: Path, inner: str, tools: Tools) -> bytes | None:
    """The file's bytes (a loose file, or one inside a zip / 7z / rar)."""
    try:
        if not inner:
            return path.read_bytes() if path.stat().st_size <= LIMIT else None
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as z:
                info = z.getinfo(inner)
                return z.read(info) if info.file_size <= LIMIT else None
        with tempfile.TemporaryDirectory(prefix="romfix-") as tmp:
            if not tools.extract_all(path, Path(tmp)):
                return None
            got = Path(tmp) / inner
            if not got.is_file():
                got = next(Path(tmp).rglob(Path(inner).name), got)
            return got.read_bytes() if got.is_file() and got.stat().st_size <= LIMIT else None
    except (OSError, KeyError, zipfile.BadZipFile):
        return None


def title_sizes(entries) -> dict[str, list[int]]:
    """Title -> the sizes the list has for it (a trimmed dump can only become one of those)."""
    out: dict[str, set[int]] = {}
    for e in entries:
        if e.size:
            out.setdefault(_title(e.game), set()).add(e.size)
    return {t: sorted(v) for t, v in out.items()}


def sizes_for(name: str, by_title: dict[str, list[int]]) -> list[int]:
    return by_title.get(_title(name), [])


def fixed(data: bytes, size: int, pad: bytes) -> bytes:
    return data + pad * (size - len(data)) if size > len(data) else data[:size]


def find(data: bytes, name: str, sizes: list[int], index: dict) -> tuple[object, int, bytes] | None:
    """(list entry, size, pad byte) when padding the dump to a listed size - or cutting its padding off - makes it a
    listed dump; pad is b"" for a cut."""
    for size in sizes:
        if size == len(data):
            continue
        if size < len(data):
            tail = set(data[size:])
            if not tail <= {0x00, 0xFF} or len(tail) != 1:
                continue                  # only padding may be cut off
            tries = [b""]
        else:
            tries = list(_PADS)
        for pad in tries:
            new = fixed(data, size, pad) if pad else data[:size]
            for c in variants(new, name):
                if c in index:
                    return index[c][0], size, pad
    return None


def apply(target: Path, size: int, pad: bytes, want: set[str], name: str) -> bool:
    """Pads / cuts the file just placed at target; True when it then has one of the wanted checksums."""
    data = target.read_bytes()
    new = fixed(data, size, pad) if pad else data[:size]
    if not set(variants(new, name)) & want:
        return False
    target.write_bytes(new)
    return True

