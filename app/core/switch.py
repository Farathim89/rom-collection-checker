"""Nintendo Switch: games are named by the title ID in their file names (Animal Well [010020D01AD24000][v0].nsp) -
the files themselves are encrypted. The list is titledb (github.com/blawar/titledb): every title on the US eShop,
turned into a list like the No-Intro ones (base games), plus names of updates / DLC and each game's newest update.

  0100...000  base game      0100...800  its update ([v65536] = version 1)      0100...xxx (bit 12 set)  DLC
"""
from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

TITLEDB = "https://raw.githubusercontent.com/blawar/titledb/master/{}"
FILES = ("US.en.json", "versions.json")
DAT_NAME = "Nintendo - Switch"
_ID = re.compile(r"\[(0100[0-9A-Fa-f]{12})\]")
_VER = re.compile(r"\[v(\d+)\]|\]\[(\d+\.\d+\.\d+)\]")
_TM = re.compile(r"[™®©]")


def kind(tid: int) -> str:
    if tid & 0xFFF == 0x800:
        return "update"
    if tid & 0x1000:
        return "dlc"
    return "base"


def base_of(tid: int) -> int:
    k = kind(tid)
    if k == "update":
        return tid - 0x800
    if k == "dlc":
        return (tid & ~0xFFF) - 0x1000
    return tid


def ids_in(name: str) -> list[tuple[int, int]]:
    """(title id, version) from a file or folder name; version -1 when the name has none."""
    out = []
    v = _VER.search(name)
    ver = int(v.group(1)) if v and v.group(1) else -1
    for m in _ID.finditer(name):
        out.append((int(m.group(1), 16), ver))
    return out


def first_part(name: str) -> bool:
    """A split archive (.part01.rar … .part13.rar) counts once."""
    m = re.search(r"\.part(\d+)\.rar$", name, re.I)
    return not m or int(m.group(1)) == 1


def fetch(folder: Path, timeout: float = 300) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    total = 0
    for n in FILES:
        req = urllib.request.Request(TITLEDB.format(n), headers={"User-Agent": "ROMCollectionChecker"})
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - fixed https host
            data = r.read()
        (folder / (n + ".part")).write_bytes(data)
        (folder / (n + ".part")).replace(folder / n)
        total += len(data)
    return total


def _q(s: str) -> str:
    return s.replace("\\", " ").replace('"', "'")


def build(titledb: Path, dats: Path) -> int:
    """titledb -> dats/Nintendo - Switch.dat (base games, serial = title ID) and switch-extra.json (DLC / update
    names, newest update of each game). Returns the number of games."""
    titles = json.loads((titledb / "US.en.json").read_text(encoding="utf-8"))
    versions = json.loads((titledb / "versions.json").read_text(encoding="utf-8"))
    games, extra = {}, {"names": {}, "latest": {}, "icons": {}}
    for t in titles.values():
        tid, name = (t.get("id") or "").upper(), _TM.sub("", t.get("name") or "").strip()
        if not re.fullmatch(r"0100[0-9A-F]{12}", tid) or not name:
            continue
        n = int(tid, 16)
        icon = t.get("iconUrl") or t.get("bannerUrl")
        if icon and icon != "None" and kind(n) == "base":
            extra["icons"][tid] = icon
        if kind(n) == "base":
            demo = str(t.get("isDemo")) == "True" or re.search(r"\bdemo\b", name, re.I)
            games[tid] = (name, demo, int(t.get("size") or 0) if str(t.get("size") or "").isdigit() else 0,
                          str(t.get("releaseDate") or ""))
        else:
            extra["names"][tid] = name
    for tid, vs in versions.items():
        if vs:
            extra["latest"][tid.upper()] = max(int(v) for v in vs)
    seen = set()
    lines = ['clrmamepro (\n\tname "Nintendo - Switch"\n\tdescription "from titledb (US eShop)"\n)\n']
    for tid, (name, demo, size, _rel) in sorted(games.items(), key=lambda x: x[1][0].lower()):
        g = f"{name} (World)" + (" (Demo)" if demo and "demo" not in name.lower() else "")
        if g.lower() in seen:                                 # two IDs with one name (a re-release): keep both
            g = f"{name} (World) [{tid}]"
        seen.add(g.lower())
        lines.append(f'game (\n\tname "{_q(g)}"\n\tserial "{tid}"\n'
                     f'\trom ( name "{_q(g)}.nsp" size {size} crc 00000000 )\n)\n')
    dats.mkdir(parents=True, exist_ok=True)
    (dats / f"{DAT_NAME}.dat").write_text("\n".join(lines), encoding="utf-8")
    (dats / "switch-extra.json").write_text(json.dumps(extra), encoding="utf-8")
    return len(games)


def load_extra(dats: Path) -> dict:
    try:
        return json.loads((dats / "switch-extra.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"names": {}, "latest": {}}
