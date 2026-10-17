"""Quick identification: a game's ID and version read from its header - a few KB instead of the whole file.
It names the game (and its revision where the header has one); it doesn't verify the file (a patched or badly
dumped copy has the same header) - that is what the full checksum check is for.

  PlayStation 1 / 2   SYSTEM.CNF on the disc: BOOT = cdrom:\\SLUS_209.46;1          -> SLUS-20946
  PSP                 UMD_DATA.BIN on the disc: ULUS-10041|...                       -> ULUS-10041
  GameCube / Wii      the disc's first bytes: GM8E01, disc no., version (.rvz / .gcz: DolphinTool header)
  Saturn              SEGA SEGASATURN header: product number, V1.001
  DS / N64            the cartridge header: game code, version
"""
from __future__ import annotations

import re
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .hashing import NO_WINDOW

RAW = 2352          # a raw CD sector (.bin); data starts 16 (mode 1) or 24 (mode 2) bytes in


@dataclass
class Probe:
    serial: str          # as the lists write it, normalised: SLUS20946, GM8E, NO7P, T4305G
    version: int = -1    # revision number from the header (-1 = the header has none)
    disc: int = 0        # disc number (GameCube / Wii)
    region: str = ""     # region letter of a 4-character game ID (E = USA, P = Europe, J = Japan …)


def norm_serial(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


class _Image:
    """2048-byte sectors from an .iso, or the data part of each raw sector of a .bin."""

    def __init__(self, f, raw: bool, offset: int):
        self.f, self.raw, self.offset = f, raw, offset

    def sector(self, n: int, count: int = 1) -> bytes:
        if not self.raw:
            self.f.seek(n * 2048)
            return self.f.read(2048 * count)
        out = b""
        for i in range(count):
            self.f.seek((n + i) * RAW + self.offset)
            out += self.f.read(2048)
        return out


def _open_image(f) -> _Image | None:
    """Finds the ISO 9660 volume descriptor (sector 16) as .iso, raw mode 1 or raw mode 2."""
    for raw, off in ((False, 0), (True, 24), (True, 16)):
        img = _Image(f, raw, off)
        if img.sector(16)[1:6] == b"CD001":
            return img
    return None


def _iso_file(img: _Image, wanted: tuple[str, ...]) -> bytes | None:
    """A small file from the image's root folder (or one level down: PSP_GAME/PARAM.SFO)."""
    pvd = img.sector(16)
    root_lba, root_len = struct.unpack_from("<I", pvd, 158)[0], struct.unpack_from("<I", pvd, 166)[0]

    def read_dir(lba: int, length: int) -> dict[str, tuple[int, int, bool]]:
        data = img.sector(lba, max(1, min(16, (length + 2047) // 2048)))
        out, i = {}, 0
        while i < len(data):
            n = data[i]
            if n == 0:
                i = (i // 2048 + 1) * 2048       # records don't cross sectors
                continue
            rec = data[i:i + n]
            if len(rec) < 34:
                break
            name = rec[33:33 + rec[32]].decode("latin1").split(";")[0].upper()
            out[name] = (struct.unpack_from("<I", rec, 2)[0], struct.unpack_from("<I", rec, 10)[0], bool(rec[25] & 2))
            i += n
        return out

    root = read_dir(root_lba, root_len)
    for w in wanted:
        parts = w.upper().split("/")
        here = root
        for k, part in enumerate(parts):
            hit = here.get(part)
            if not hit:
                break
            lba, length, is_dir = hit
            if k == len(parts) - 1 and not is_dir:
                return img.sector(lba, max(1, min(8, (length + 2047) // 2048)))[:length]
            if is_dir:
                here = read_dir(lba, length)
    return None


def _playstation(p: Path) -> Probe | None:
    with open(p, "rb") as f:
        img = _open_image(f)
        if img is None:
            return None
        cnf = _iso_file(img, ("SYSTEM.CNF",))
        if cnf:
            m = re.search(rb"BOOT2?\s*=\s*cdrom0?:\\?\\?([A-Z]{4})[_-]?(\d{3})\.?(\d{2})", cnf, re.I)
            if m:
                return Probe(norm_serial((m.group(1) + m.group(2) + m.group(3)).decode()))
        umd = _iso_file(img, ("UMD_DATA.BIN",))              # PSP
        if umd:
            m = re.match(rb"([A-Z]{4}-\d{5})", umd)
            if m:
                return Probe(norm_serial(m.group(1).decode()))
    return None


def _nintendo_disc(head: bytes) -> Probe | None:
    """GameCube / Wii disc header: ID6 (GM8E01), disc number, version; magic words at 0x18 (Wii) / 0x1C (GC)."""
    if len(head) < 0x20 or not (head[0x18:0x1C] == b"\x5d\x1c\x9e\xa3" or head[0x1C:0x20] == b"\xc2\x33\x9f\x3d"):
        return None
    gid = head[:4].decode("latin1")
    if not re.fullmatch(r"[A-Z0-9]{4}", gid):
        return None
    return Probe(gid, version=head[7], disc=head[6], region=gid[3])


def _dolphin_header(p: Path, dolphin: str) -> Probe | None:
    if not dolphin:
        return None
    with tempfile.TemporaryDirectory(prefix="romcheck-dolphin-") as user:
        out = subprocess.run([dolphin, "header", "-i", str(p)], capture_output=True, timeout=60,
                             creationflags=NO_WINDOW, cwd=user)
    text = (out.stdout + out.stderr).decode("utf-8", "replace")
    gid = re.search(r"Game ID:\s*([A-Z0-9]{4,6})", text, re.I)
    if not gid:
        return None
    rev = re.search(r"Revision:\s*(\d+)", text, re.I)
    return Probe(gid.group(1)[:4].upper(), version=int(rev.group(1)) if rev else -1, region=gid.group(1)[3].upper())


def _saturn(p: Path) -> Probe | None:
    with open(p, "rb") as f:
        head = f.read(RAW)
    for off in (0, 16, 24):
        h = head[off:off + 256]
        if h.startswith(b"SEGA SEGASATURN"):
            product = h[0x20:0x2A].decode("latin1").strip()
            ver = re.match(r"V(\d)\.(\d{3})", h[0x2A:0x30].decode("latin1"))
            area = h[0x40:0x50].decode("latin1").strip()      # J / U (USA) / E (Europe) / T / K …
            region = {"U": "E", "E": "P", "J": "J"}.get(area, "") if len(area) == 1 else ""
            return Probe(norm_serial(product), version=int(ver.group(1) + ver.group(2)) - 1000 if ver else -1,
                         region=region)
    return None


def _sfo(data: bytes) -> dict[str, str]:
    """PARAM.SFO (PS3 / PSP): its key = value table."""
    if data[:4] != b"\0PSF":
        return {}
    keys, vals, n = struct.unpack_from("<III", data, 8)
    out = {}
    for i in range(n):
        ko, _fmt, ln, _mx, do = struct.unpack_from("<HHIII", data, 20 + i * 16)
        k = data[keys + ko:data.index(b"\0", keys + ko)].decode("latin1")
        out[k] = data[vals + do:vals + do + ln].split(b"\0")[0].decode("utf-8", "replace")
    return out


def _ps3(p: Path) -> Probe | None:
    """A PS3 game folder's PS3_GAME/PARAM.SFO, or an .iso with it inside."""
    if p.suffix.lower() == ".sfo":
        info = _sfo(p.read_bytes())
    else:
        with open(p, "rb") as f:
            img = _open_image(f)
            info = _sfo(_iso_file(img, ("PS3_GAME/PARAM.SFO",)) or b"") if img else {}
    tid = info.get("TITLE_ID", "")
    if not re.fullmatch(r"[A-Z]{4}\d{5}", tid):
        return None
    ver = re.match(r"(\d+)\.(\d+)", info.get("VERSION", "") or info.get("APP_VER", ""))
    return Probe(tid, version=int(ver.group(1)) * 100 + int(ver.group(2)) - 100 if ver else -1)


def _xbox(p: Path) -> Probe | None:
    """default.xbe in an Xbox image (a repacked xiso, or a full Redump image with the video partition first):
    its title ID 0x4D530004 is Redump's MS-004."""
    with open(p, "rb") as f:
        for base in (0, 0x18300000, 0x2080000, 0xFD90000):
            f.seek(base + 0x10000)
            vd = f.read(0x800)
            if not vd.startswith(b"MICROSOFT*XBOX*MEDIA"):
                continue
            root, size = struct.unpack_from("<II", vd, 0x14)
            f.seek(base + root * 2048)
            d = f.read(min(size, 1 << 20))
            i = d.lower().find(b"default.xbe")
            if i < 14:
                return None
            start = struct.unpack_from("<I", d, i - 10)[0]
            f.seek(base + start * 2048)
            xbe = f.read(0x1000)
            if xbe[:4] != b"XBEH":
                return None
            img_base, = struct.unpack_from("<I", xbe, 0x104)
            cert, = struct.unpack_from("<I", xbe, 0x118)
            f.seek(base + start * 2048 + cert - img_base)
            c = f.read(0x1D0)
            tid, = struct.unpack_from("<I", c, 0x8)
            pub = bytes([(tid >> 24) & 0xFF, (tid >> 16) & 0xFF]).decode("latin1")
            if re.fullmatch(r"[A-Z0-9]{2}", pub):
                return Probe(f"{pub}{tid & 0xFFFF:03d}")   # its version isn't Redump's revision
    return None


def _xdvdfs_file(f, name: bytes, bases=(0, 0x2080000, 0xFD90000, 0x18300000)) -> tuple[int, int] | None:
    """(partition base, file offset) of a root file on an Xbox / Xbox 360 disc (XDVDFS)."""
    for base in bases:
        f.seek(base + 0x10000)
        vd = f.read(0x800)
        if not vd.startswith(b"MICROSOFT*XBOX*MEDIA"):
            continue
        root, size = struct.unpack_from("<II", vd, 0x14)
        f.seek(base + root * 2048)
        d = f.read(min(size, 1 << 20))
        i = d.lower().find(name)
        if i >= 14:
            return base, base + struct.unpack_from("<I", d, i - 10)[0] * 2048
    return None


def _xbox360(p: Path) -> Probe | None:
    """default.xex on an Xbox 360 disc (XGD2 / XGD3 image, or a repacked one): its execution info has the title ID
    (0x4D5309C9 = Redump's MS-2505), the version and the disc number."""
    with open(p, "rb") as f:
        hit = _xdvdfs_file(f, b"default.xex")
        if not hit:
            return None
        f.seek(hit[1])
        x = f.read(0x3000)
        if x[:4] != b"XEX2":
            return None
        count, = struct.unpack_from(">I", x, 0x14)
        for k in range(min(count, 64)):
            key, val = struct.unpack_from(">II", x, 0x18 + k * 8)
            if key == 0x00040006 and val + 24 <= len(x):      # execution info
                _media, _version, _base, tid = struct.unpack_from(">IIII", x, val)
                disc = x[val + 18]
                pub = bytes([(tid >> 24) & 0xFF, (tid >> 16) & 0xFF]).decode("latin1")
                if re.fullmatch(r"[A-Z0-9]{2}", pub):
                    return Probe(f"{pub}{tid & 0xFFFF:03d}", disc=max(0, disc - 1))   # (not Redump's revision)
    return None


def probe(p: Path, console: str, dolphin: str = "") -> Probe | None:
    """The game's ID (and version) from its header, or None when it can't be read that way."""
    ext = p.suffix.lower().lstrip(".")
    try:
        if console in ("ps1", "ps2", "psp") and ext in ("iso", "bin", "img"):
            return _playstation(p)
        if console in ("gc", "wii"):
            if ext in ("iso", "gcm"):
                with open(p, "rb") as f:
                    return _nintendo_disc(f.read(0x20))
            if ext == "wbfs":
                with open(p, "rb") as f:
                    if f.read(4) != b"WBFS":
                        return None
                    f.seek(0x200)               # WBFS keeps a copy of the disc header here
                    return _nintendo_disc(f.read(0x20))
            if ext in ("rvz", "gcz", "wia"):
                return _dolphin_header(p, dolphin)
        if console == "ps3" and (ext == "iso" or (p.name.upper() == "PARAM.SFO" and p.parent.name.upper() == "PS3_GAME")):
            return _ps3(p)
        if console == "xbox" and ext == "iso":
            return _xbox(p)
        if console == "x360" and ext == "iso":
            return _xbox360(p)
        if console == "saturn" and ext in ("bin", "iso", "img"):
            return _saturn(p)
        if console == "nds" and ext == "nds":
            with open(p, "rb") as f:
                h = f.read(0x20)
            code = h[0x0C:0x10].decode("latin1")
            return Probe(code, version=h[0x1E], region=code[3]) if re.fullmatch(r"[A-Z0-9]{4}", code) else None
        if console == "n64" and ext in ("z64", "n64", "v64"):
            with open(p, "rb") as f:
                h = bytearray(f.read(0x40))
            if h[:4] == b"\x37\x80\x40\x12":            # .v64: bytes swapped in pairs
                h[0::2], h[1::2] = h[1::2], h[0::2]
            elif h[:4] == b"\x40\x12\x37\x80":          # .n64: little endian
                h = bytearray(b for i in range(0, 0x40, 4) for b in reversed(h[i:i + 4]))
            code = bytes(h[0x3B:0x3F]).decode("latin1")
            return Probe(code, version=h[0x3F], region=code[3]) if re.fullmatch(r"[A-Z0-9]{4}", code) else None
    except (OSError, ValueError, struct.error, subprocess.SubprocessError):
        return None
    return None


QUICK_CONSOLES = ("ps1", "ps2", "ps3", "psp", "gc", "wii", "saturn", "xbox", "x360", "nds", "n64")
REGION_LETTERS = {"E": ("USA", "Canada", "World"), "P": ("Europe", "World", "Australia", "UK"), "J": ("Japan",),
                  "D": ("Germany", "Europe"), "F": ("France", "Europe"), "S": ("Spain", "Europe"), "I": ("Italy", "Europe"),
                  "U": ("Australia", "Europe"), "K": ("Korea",), "W": ("Taiwan", "Asia"), "X": ("Europe",), "Y": ("Europe",)}


def serial_index(entries) -> dict[str, list[str]]:
    """normalised serial (and the 4-character game ID inside DL-DOL-GM8P-EUR / RVL-RMCP-EUR) -> games.
    The key "" holds game -> file sizes (to tell versions with one serial apart)."""
    out: dict = {"": {}}
    for e in entries:
        out[""].setdefault(e.game, set()).add(e.size)
        for s in (x.strip() for x in (e.serial or "").split(",")):
            if not s:
                continue
            keys = {norm_serial(s)}
            keys.update(t for t in s.upper().split("-") if re.fullmatch(r"[A-Z0-9]{4}", t) and not t.isdigit())
            for k in keys:
                lst = out.setdefault(k, [])
                if e.game not in lst:
                    lst.append(e.game)
    return out


def identify(pr: Probe, index: dict, p: Path | None = None, size: int = 0) -> tuple[str, bool]:
    """(game, sure): the game this header belongs to; sure = only one fits."""
    games = candidates(pr, index, p, size)
    return (games[0], len(games) == 1) if games else ("", False)


def candidates(pr: Probe, index: dict, p: Path | None = None, size: int = 0) -> list[str]:
    """The versions this header can be, best first. Several versions with one serial (PlayStation discs have no
    revision in their header) are told apart by file size and file name."""
    games = list(index.get(pr.serial, []))
    # later versions get a digit or a region on the serial in Redump (SLES-52541 v1.03, SLES-525413 v2.01,
    # SLES-52541-ANZ): the whole family, then size and name decide
    for k, v in index.items():
        if k and k != pr.serial and k.startswith(pr.serial) and len(k) - len(pr.serial) <= 3:
            games += [g for g in v if g not in games]
    m = re.fullmatch(r"([A-Z]{4})(\d{5})", pr.serial)
    if not games and m:                            # multi-disc: Redump files discs 2, 3 under disc 1's serial
        for back in (1, 2, 3):
            fam = f"{m.group(1)}{int(m.group(2)) - back:05d}"
            for k, v in index.items():
                if k and k.startswith(fam) and len(k) - len(fam) <= 3:
                    games += [g for g in v if g not in games and "(Disc " in g]
            if games:
                break
    digits = re.sub(r"^[A-Z]+", "", pr.serial)      # Saturn: "MK-81079" is "81079" (USA) or "MK81079-50" (Europe)
    if digits != pr.serial and len(digits) >= 4:
        games += [g for g in index.get(digits, []) if g not in games]
    if len(games) <= 1:
        return games
    sizes = index.get("", {})
    if size:
        by_size = [g for g in games if size in sizes.get(g, ())]
        if len(by_size) == 1:
            return by_size
        games = by_size or games
    if p is not None:
        stem = re.sub(r"\s*\(Track \d+\)", "", p.stem).lower()
        by_name = [g for g in games if g.lower() == stem or g.lower() == p.parent.name.lower()]
        if len(by_name) == 1:
            return by_name
        dn = re.search(r"\(Disc (\d+)\)", p.stem)
        if dn:
            games = [g for g in games if f"(Disc {dn.group(1)})" in g] or games
    if pr.region in REGION_LETTERS:
        reg = [g for g in games if any(f"({r}" in g or f", {r}" in g for r in REGION_LETTERS[pr.region])]
        games = reg or games
    pick = games
    if pr.disc:                                   # GameCube / Wii: disc 2 of a game
        d = [g for g in pick if f"(Disc {pr.disc + 1})" in g]
        pick = d or pick
    elif any("(Disc " in g for g in pick):
        d = [g for g in pick if "(Disc 1)" in g or "(Disc " not in g]
        pick = d or pick
    if pr.version > 0:
        r = [g for g in pick if re.search(rf"\(Rev {pr.version}\)", g)] or [g for g in pick if "(Rev " in g]
        pick = r or pick
    elif pr.version == 0:
        r = [g for g in pick if "(Rev " not in g]
        pick = r or pick
    plain = [g for g in pick if not re.search(r"\((Beta|Proto|Demo|Sample|Kiosk)", g)] or pick
    if p is not None:                             # best first: the same title as the file ("Halo (Japan)" last)
        want = _plain_title(p.stem)
        plain.sort(key=lambda g: _plain_title(g) != want)
    return plain


def _plain_title(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", re.sub(r"\s*[\(\[].*", "", name).lower())
