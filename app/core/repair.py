"""Trimmed and overdumped ROMs: a dump whose checksum isn't in the list is often a good dump with its empty end
cut off ("trimmed": DS, GBA, N64 tools did that to save space) or with empty space added (an overdump). Padding
it back to a size the list has, or cutting off its padding, gives the real dump when that checksum is listed.
Only padding is ever added or removed (0xFF / 0x00 bytes), never data."""
from __future__ import annotations

import re
import zlib
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



# Dumps in another form (old GoodTools sets): the right game once converted - checked against the list's checksum,
# so nothing that isn't that dump is ever written
_REV = bytes(int(f"{i:08b}"[::-1], 2) for i in range(256))


def _swap16(x: bytes) -> bytes:
    y = bytearray(len(x) - len(x) % 2)
    x = x[:len(y)]
    y[0::2], y[1::2] = x[1::2], x[0::2]
    return bytes(y)


def _deinterleave(x: bytes) -> bytes:
    """Mega Drive SMD layout (16 KB blocks: odd bytes, then even bytes) without its 512-byte header."""
    out = bytearray(len(x))
    for b in range(0, len(x) - len(x) % 16384, 16384):
        s = x[b:b + 16384]
        out[b + 1:b + 16384:2], out[b:b + 16384:2] = s[:8192], s[8192:]
    return bytes(out)


def conversions(data: bytes, size: int) -> list[tuple[str, bytes]]:
    """(how, bytes) - the forms a dump of `size` bytes may hide in: copier / iNES header, byte-swapped, SMD
    interleaved, reversed bit order (US PC Engine cards), mirrored (an Atari 2 KB game stored twice), overdumped
    (cut to the size) or trimmed (padded back)."""
    base = [("", data)]
    if len(data) % 1024 == 512:
        base.append(("header cut", data[512:]))
    if data[:4] == b"NES\x1a":
        base.append(("iNES header cut", data[16:]))
    out = []
    for name, x in base:
        for how, y in ((name, x), (f"{name}, bytes swapped", _swap16(x)), (f"{name}, SMD layout", _deinterleave(x)),
                       (f"{name}, bit order", x.translate(_REV))):
            out.append((how, y))
            if size < len(y):
                out.append((f"{how}, cut to {size}", y[:size]))
            elif size > len(y):
                out += [(f"{how}, padded to {size}", y + pad * (size - len(y))) for pad in _PADS]
    return [(how.lstrip(", ") or "as is", y) for how, y in out]


def find_converted(data: bytes, sizes: list[int], index: dict[str, list]) -> tuple[object, str, bytes] | None:
    """(list entry, how, the real dump) when a conversion of the file is a listed dump - sizes nearest first."""
    n = len(data)
    for size in sorted((s for s in sizes if n / 4 <= s <= n * 2), key=lambda s: abs(s - n)):
        for how, y in conversions(data, size):
            es = index.get(f"{zlib.crc32(y) & 0xFFFFFFFF:08x}")
            if es:
                return es[0], how, y
    return None


def with_ines_header(header: bytes, dump: bytes) -> bytes:
    """A headerless NES dump with the file's own iNES header in front - its CHR count fixed when an overdump's
    header counted the junk too."""
    h = bytearray(header[:16])
    prg = h[4] * 16384
    if prg + h[5] * 8192 != len(dump) and prg < len(dump) and (len(dump) - prg) % 8192 == 0:
        h[5] = (len(dump) - prg) // 8192
    return bytes(h) + dump
