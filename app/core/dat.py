"""No-Intro lists (clrmamepro .dat files from libretro-database): every known dump of a console, with its
checksum. Downloaded on request into the data folder; read into a CRC index."""
from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

URL = "https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/no-intro/{}.dat"
MAX_AGE = 30 * 86400   # lists are refreshed when a month old (when you ask for an update)

_GAME = re.compile(r'game \(\s*name "((?:[^"\\]|\\.)*)"(.*?)\n\)', re.S)
_ROM = re.compile(r'rom \( name "((?:[^"\\]|\\.)*)" size (\d+) crc ([0-9A-Fa-f]{1,8})')
_TAG = re.compile(r"\(([^)]*)\)")

# what counts as a real, officially released game; everything else is an "extra"
KINDS = (("unlicensed", re.compile(r"^Unl$", re.I)),
         ("prototype", re.compile(r"^(Proto|Beta|Pre-Production|Alpha)\b", re.I)),
         ("demo", re.compile(r"^(Demo|Sample|Kiosk|Promo|Taikenban|Trial)\b", re.I)),
         ("homebrew", re.compile(r"^Aftermarket$", re.I)),
         ("pirate", re.compile(r"^(Pirate|Hack)$", re.I)),
         ("program", re.compile(r"^(Program|Test Program|Enhancement Chip|BIOS)\b", re.I)),
         ("rerelease", re.compile(r"^(Virtual Console|Switch Online|Collection of .*|Namco Museum .*|"
                                  r"Capcom .* Collection|Retro-Bit|Limited Run.*|Arcade Archives|Evercade|Namcot Collection|"
                                  r"Namco Museum Archives.*|.* Collection|Virtual Console.*|NP|LodgeNet)$", re.I)))
EUROPE = {"Europe", "Germany", "France", "Spain", "Italy", "Netherlands", "Sweden", "Scandinavia", "UK",
          "Australia", "Denmark", "Norway", "Finland", "Portugal", "Greece", "Poland", "Russia"}


@dataclass(frozen=True)
class Entry:
    game: str      # "Super Mario Bros. (World)"
    rom: str       # file name in the list
    size: int
    crc: str       # 8 lowercase hex digits
    title: str     # "Super Mario Bros."
    regions: tuple[str, ...]
    kind: str      # "game" for official releases, else unlicensed / prototype / demo / homebrew / ...

    @property
    def scopes(self) -> set[str]:
        """USA / Europe / Japan / Other (World counts for USA and Europe)."""
        out = set()
        for r in self.regions:
            if r in ("USA", "World", "Canada"):
                out.add("USA")
            if r in EUROPE or r == "World":
                out.add("Europe")
            if r == "Japan":
                out.add("Japan")
        return out or {"Other"}


def tags(name: str) -> list[str]:
    return _TAG.findall(name)


def kind_of(game: str, rom: str) -> str:
    all_tags = [part.strip() for t in tags(game) + tags(rom) for part in t.split(",")]
    for kind, pat in KINDS:
        if any(pat.search(t) for t in all_tags):
            return kind
    if game.startswith("[BIOS]"):
        return "program"
    return "game"


def title_of(game: str) -> str:
    return re.sub(r"\s*\(.*$", "", game).strip()


def regions_of(game: str) -> tuple[str, ...]:
    t = tags(game)
    return tuple(r.strip() for r in t[0].split(",")) if t else ()


def parse(text: str) -> list[Entry]:
    out: list[Entry] = []
    for gm in _GAME.finditer(text):
        game = gm.group(1)
        for rm in _ROM.finditer(gm.group(2)):
            rom = rm.group(1)
            out.append(Entry(game, rom, int(rm.group(2)), rm.group(3).lower().zfill(8), title_of(game),
                             regions_of(game), kind_of(game, rom)))
    return out


class DatStore:
    """The downloaded lists in <data>/dats, read once per run."""

    def __init__(self, folder: Path):
        self.folder = folder
        self._cache: dict[str, list[Entry]] = {}

    def path(self, dat: str) -> Path:
        return self.folder / f"{dat}.dat"

    def have(self, dat: str) -> bool:
        return self.path(dat).is_file()

    def age_days(self, dat: str) -> float | None:
        p = self.path(dat)
        return (time.time() - p.stat().st_mtime) / 86400 if p.is_file() else None

    def download(self, dat: str, timeout: float = 60) -> int:
        """Fetches one list; returns its size in bytes."""
        url = URL.format(urllib.parse.quote(dat))
        req = urllib.request.Request(url, headers={"User-Agent": "ROMCollectionChecker"})
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - fixed https host
            data = r.read()
        if b"clrmamepro" not in data[:200] and b"game (" not in data[:4000]:
            raise ValueError(f"{dat}: not a No-Intro list")
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.path(dat).with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(self.path(dat))
        self._cache.pop(dat, None)
        return len(data)

    def version(self, dat: str) -> str:
        try:
            head = self.path(dat).read_text(encoding="utf-8", errors="replace")[:600]
        except OSError:
            return ""
        m = re.search(r'version "([^"]+)"', head)
        return m.group(1) if m else ""

    def entries(self, dat: str) -> list[Entry]:
        if dat not in self._cache:
            try:
                self._cache[dat] = parse(self.path(dat).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                self._cache[dat] = []
        return self._cache[dat]
