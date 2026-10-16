"""Checksums of what you have. A dump can differ from No-Intro's in ways that don't change the game, so every
file gives a few candidate CRCs: as it is, without a copier/emulator header, de-interleaved (.smd) and in
No-Intro's byte order (N64). Archives are read entry by entry: zip natively, 7z with 7-Zip, rar with WinRAR."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

VARIANT_LIMIT = 64 * 1024 * 1024   # bigger files (DS, N64) only get their plain CRC and the N64 swaps
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def crc(b: bytes) -> str:
    return f"{zlib.crc32(b) & 0xFFFFFFFF:08x}"


def _deinterleave_smd(b: bytes) -> bytes:
    body = b[512:]
    out = bytearray(len(body))
    for blk in range(0, len(body) - 16383, 16384):
        out[blk + 1:blk + 16384:2] = body[blk:blk + 8192]
        out[blk:blk + 16384:2] = body[blk + 8192:blk + 16384]
    return bytes(out)


def _n64_bigendian(b: bytes) -> bytes | None:
    head = b[:4]
    if head == b"\x37\x80\x40\x12":   # .v64: 16-bit byte swapped
        a = bytearray(b)
        a[0::2], a[1::2] = b[1::2], b[0::2]
        return bytes(a)
    if head == b"\x40\x12\x37\x80":   # .n64: 32-bit little endian
        a = bytearray(len(b))
        a[0::4], a[1::4], a[2::4], a[3::4] = b[3::4], b[2::4], b[1::4], b[0::4]
        return bytes(a)
    return None


def variants(data: bytes, name: str = "") -> list[str]:
    """The CRCs this content may have in a No-Intro list (the first one is the file as it is)."""
    out = [crc(data)]
    ext = Path(name).suffix.lower()
    n = len(data)
    swapped = _n64_bigendian(data)
    if swapped is not None:
        out.append(crc(swapped))
    if n > VARIANT_LIMIT:
        return out
    if data[:4] in (b"NES\x1a", b"FDS\x1a"):          # iNES / fwNES header
        out.append(crc(data[16:]))
    if data[:4] == b"LYNX":                           # Lynx: 64-byte header
        out.append(crc(data[64:]))
    if data[1:10] == b"ATARI7800":                    # Atari 7800: 128-byte header
        out.append(crc(data[128:]))
    if n % 1024 == 512:                               # copier header (SNES, PCE, Mega Drive, Master System …)
        out.append(crc(data[512:]))
        if ext == ".smd" or n % 16384 == 512:
            out.append(crc(_deinterleave_smd(data)))
    return list(dict.fromkeys(out))


@dataclass
class Item:
    """One game file: a loose ROM, or one entry inside an archive."""
    path: str          # the file on disk
    inner: str         # name inside the archive ("" for a loose file)
    size: int
    crcs: list[str]

    @property
    def label(self) -> str:
        return f"{self.path} › {self.inner}" if self.inner else self.path


class Tools:
    """Where 7-Zip, WinRAR and DolphinTool are (settings first, then the usual places)."""

    def __init__(self, sevenzip: str = "", winrar_dir: str = "", dolphin_tool: str = ""):
        cands = [sevenzip, r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe",
                 shutil.which("7z") or "", shutil.which("7za") or ""]
        self.seven = next((c for c in cands if c and Path(c).is_file()), "")
        rar_dirs = [winrar_dir, r"C:\Program Files\WinRAR", r"C:\Program Files (x86)\WinRAR"]
        d = next((c for c in rar_dirs if c and (Path(c) / "UnRAR.exe").is_file()), "")
        self.unrar = str(Path(d) / "UnRAR.exe") if d else ""
        self.winrar = str(Path(d) / "WinRAR.exe") if d and (Path(d) / "WinRAR.exe").is_file() else ""
        # DolphinTool reads .rvz / .wbfs / .gcz and gives the original disc's checksum (Dolphin 5.0-15000 or newer)
        dol = [dolphin_tool, shutil.which("DolphinTool") or "", r"C:\Program Files\Dolphin\DolphinTool.exe",
               r"C:\Program Files\Dolphin-x64\DolphinTool.exe",
               str(Path(os.environ.get("LOCALAPPDATA", "")) / "Dolphin" / "DolphinTool.exe")]
        self.dolphin = next((c for c in dol if c and Path(c).is_file()), "")
        self.cancel = threading.Event()   # Stop / closing the app: a running check ends at once

    def disc_crc(self, path: Path) -> str:
        """The CRC32 of the disc inside a Dolphin image (.rvz / .wbfs / .gcz), as Redump lists it."""
        if not self.dolphin:
            raise ValueError("needs DolphinTool (Settings → Tools & lists) to check .rvz / .wbfs / .gcz")
        with tempfile.TemporaryDirectory(prefix="romcheck-dolphin-") as user:
            proc = subprocess.Popen([self.dolphin, "verify", "-u", user, "-a", "crc32", "-i", str(path)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NO_WINDOW)
            while proc.poll() is None:        # a .wbfs is read whole: minutes - Stop must not wait for it
                if self.cancel.wait(0.3):
                    proc.kill()
                    proc.wait()
                    raise InterruptedError("stopped")
            out = subprocess.CompletedProcess(proc.args, proc.returncode, *proc.communicate())
        m = re.search(rb"(?<![0-9a-fA-F])([0-9a-fA-F]{8})(?![0-9a-fA-F])", out.stdout + b"\n" + out.stderr)
        if not m:
            raise ValueError("DolphinTool could not read it")
        return m.group(1).decode().lower()

    def _run(self, args: list[str], timeout: float = 600) -> bytes:
        return subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW).stdout

    def extract_all(self, archive: Path, to: Path) -> bool:
        """Unpacks everything (flat) into `to`: 7-Zip, else WinRAR (it also opens zip and 7z)."""
        ext = archive.suffix.lower()
        if ext == ".zip":
            try:
                with zipfile.ZipFile(archive) as z:
                    for i in z.infolist():
                        if not i.is_dir():
                            (to / Path(i.filename).name).write_bytes(z.read(i))
                return True
            except (zipfile.BadZipFile, OSError, NotImplementedError):
                pass
        if self.seven and ext != ".rar":
            self._run([self.seven, "e", "-y", f"-o{to}", str(archive)])
            if any(to.iterdir()):
                return True
        if self.unrar and ext == ".rar":
            self._run([self.unrar, "e", "-y", "-inul", str(archive), str(to) + "\\"])
            if any(to.iterdir()):
                return True
        if self.winrar:
            self._run([self.winrar, "e", "-ibck", "-y", "-inul", str(archive), str(to) + "\\"])
        return any(to.iterdir())


def _stream_crc(path: Path, cancel: threading.Event | None = None) -> str:
    c = 0
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("stopped")
            c = zlib.crc32(chunk, c)
    return f"{c & 0xFFFFFFFF:08x}"


DOLPHIN_EXTS = ("rvz", "wbfs", "gcz")


def file_items(path: Path, tools: Tools, rom_exts: set[str], name_only: set[str] = frozenset()) -> tuple[list[Item], str]:
    """The game files in `path` (itself, or its archive entries). Second value: an error text, or ''.
    name_only: file types that are not read at all (they can't match a checksum: matched by name later)."""
    ext = path.suffix.lower().lstrip(".")
    try:
        if ext in name_only:
            return [Item(str(path), "", path.stat().st_size, [])], ""
        if ext in DOLPHIN_EXTS:
            return [Item(str(path), "", path.stat().st_size, [tools.disc_crc(path)])], ""
        if ext == "zip":
            return _zip_items(path, rom_exts), ""
        if ext in ("7z", "rar"):
            return _extracted_items(path, tools, rom_exts)
        size = path.stat().st_size
        if size <= VARIANT_LIMIT:   # read whole (header checks); bigger ones (DS, discs) are read in chunks
            return [Item(str(path), "", size, variants(path.read_bytes(), path.name))], ""
        return [Item(str(path), "", size, [_stream_crc(path, tools.cancel)])], ""
    except (OSError, zipfile.BadZipFile, ValueError, NotImplementedError) as e:
        return [], f"{type(e).__name__}: {e}"


def _wanted(name: str, rom_exts: set[str], only_one: bool) -> bool:
    e = Path(name).suffix.lower().lstrip(".")
    return e in rom_exts or (only_one and e not in ("txt", "nfo", "jpg", "png", "diz", "url", "htm", "html"))


def _zip_items(path: Path, rom_exts: set[str]) -> list[Item]:
    out = []
    with zipfile.ZipFile(path) as z:
        files = [i for i in z.infolist() if not i.is_dir()]
        for i in files:
            if not _wanted(i.filename, rom_exts, len(files) == 1):
                continue
            if i.file_size <= VARIANT_LIMIT:
                out.append(Item(str(path), i.filename, i.file_size, variants(z.read(i), i.filename)))
            else:
                out.append(Item(str(path), i.filename, i.file_size, [f"{i.CRC:08x}"]))
    return out


PLAIN_EXTS = {"nds", "dsi", "gba", "gb", "gbc", "z64", "iso", "vb", "ws", "wsc", "ngp", "ngc", "a26",   # never headered
              "wbfs", "rvz", "gcz", "cso", "chd", "pbp", "cdi", "nsp", "nsz", "xci"}   # (disc images: read from the index)


def _listed_items(path: Path, tools: Tools, rom_exts: set[str]) -> list[Item] | None:
    """The CRCs a .7z keeps in its index (7-Zip "l -slt"): exact, without unpacking. Only for big games (DS, N64 …)
    whose CRC needs no header handling; None = unpack it the normal way."""
    if not tools.seven or path.suffix.lower() != ".7z":
        return None
    out = subprocess.run([tools.seven, "l", "-slt", str(path)], capture_output=True, timeout=120,
                         creationflags=NO_WINDOW).stdout.decode("utf-8", "replace")
    entries, cur = [], {}
    for line in out.splitlines():
        if line.startswith("Path = "):
            cur = {"path": line[7:]}
            entries.append(cur)
        elif " = " in line and cur:
            k, v = line.split(" = ", 1)
            cur[k] = v
    files = [e for e in entries[1:] if e.get("Folder") != "+" and "Size" in e]    # entries[0] is the archive
    wanted = [e for e in files if _wanted(e["path"], rom_exts, len(files) == 1)]
    plain = lambda e: Path(e["path"]).suffix.lower().lstrip(".") in PLAIN_EXTS  # noqa: E731 - no header to strip
    if not wanted or any(not e.get("CRC") or (int(e["Size"] or 0) <= VARIANT_LIMIT and not plain(e)) for e in wanted):
        return None
    return [Item(str(path), Path(e["path"]).name, int(e["Size"]), [e["CRC"].lower().zfill(8)]) for e in wanted]


def _extracted_items(path: Path, tools: Tools, rom_exts: set[str]) -> tuple[list[Item], str]:
    if not (tools.seven or tools.unrar or tools.winrar):
        return [], "no 7-Zip or WinRAR to open it"
    try:
        listed = _listed_items(path, tools, rom_exts)
    except (OSError, ValueError, subprocess.SubprocessError):
        listed = None
    if listed:
        return listed, ""
    with tempfile.TemporaryDirectory(prefix="romcheck-") as tmp:
        if not tools.extract_all(path, Path(tmp)):
            return [], "could not be opened"
        files = [p for p in Path(tmp).iterdir() if p.is_file()]
        out = []
        for p in files:
            if not _wanted(p.name, rom_exts, len(files) == 1):
                continue
            size = p.stat().st_size
            out.append(Item(str(path), p.name, size, variants(p.read_bytes(), p.name) if size <= 1 << 30
                            else [_stream_crc(p)]))
        return out, ""
