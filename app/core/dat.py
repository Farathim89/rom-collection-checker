"""No-Intro lists (clrmamepro .dat files from libretro-database): every known dump of a console, with its
checksum. Downloaded on request into the data folder; read into a CRC index."""
from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

URL = "https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/{source}/{name}.dat"
MAX_AGE = 30 * 86400   # lists are refreshed when a month old (when you ask for an update)

_GAME = re.compile(r'game \(\s*name "((?:[^"\\]|\\.)*)"(.*?)\n\)', re.S)
_ROM = re.compile(r'rom \( name "((?:[^"\\]|\\.)*)" size (\d+) crc ([0-9A-Fa-f]{1,8})')
_TAG = re.compile(r"\(([^)]*)\)")
_SERIAL = re.compile(r'^\s*serial "([^"]*)"', re.M)

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
# the region picker: (group, keys) - each key a tick of its own
REGION_GROUPS = (("", ("USA",)),          # a World release counts for every region, so World isn't a tick
                 ("Europe", ("Europe", "UK", "Germany", "France", "Spain", "Italy", "Netherlands", "Scandinavia",
                             "Portugal", "Poland", "Russia", "Greece", "Australia")),
                 ("Asia", ("Japan", "Asia", "Korea", "China", "Taiwan")),
                 ("", ("Brazil", "Other")))
REGION_KEYS = tuple(k for _g, ks in REGION_GROUPS for k in ks)
EUROPE_KEYS = ("Europe", "UK", "Germany", "France", "Spain", "Italy", "Netherlands", "Scandinavia", "Portugal",
               "Poland", "Russia", "Greece", "Australia")
REGION_OF = {k: k for k in REGION_KEYS}
REGION_OF.update({"World": "World", "Canada": "USA", "United Kingdom": "UK", "Belgium": "Netherlands", "Austria": "Germany",
                  "Switzerland": "Germany", "Sweden": "Scandinavia", "Denmark": "Scandinavia", "Norway": "Scandinavia",
                  "Finland": "Scandinavia", "New Zealand": "Australia", "Hong Kong": "Asia", "India": "Asia",
                  "United Arab Emirates": "Asia", "Latin America": "Brazil", "Argentina": "Brazil", "Peru": "Brazil",
                  "Mexico": "Brazil", "Turkey": "Other"})
HIDDEN_REGIONS: set[str] = set()   # Settings → Regions: real region names you hid ("Other" = all unnamed ones)


def region_hidden(r: str) -> bool:
    return r in HIDDEN_REGIONS or (r not in REGION_OF and "Other" in HIDDEN_REGIONS)


def region_counts(entries_lists) -> dict[str, int]:
    """Real region name -> games (anything the lists put there that isn't a region counts as Other)."""
    out: dict[str, int] = {}
    for entries in entries_lists:
        seen = set()
        for e in entries:
            if e.game in seen:
                continue
            seen.add(e.game)
            for r in e.regions or ("Other",):
                k = r if r in REGION_OF else "Other"
                out[k] = out.get(k, 0) + 1
    return out


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
    serial: str = ""   # the game's ID as the list writes it (SLUS-20946, DL-DOL-GM8P-EUR, BKAJ …), "" if none

    @property
    def scopes(self) -> set[str]:
        """The region picker's choices this version counts for. A World release counts everywhere, a Europe
        release for each European country too; Canada counts as USA, Sweden / Denmark … as Scandinavia."""
        out = set()
        regions = [r for r in self.regions if not region_hidden(r)]
        if not regions:                             # only released in regions you hid (Settings → Regions)
            return set() if self.regions or region_hidden("Other") else {"Other"}
        for r in regions:
            k = REGION_OF.get(r, "Other")
            if k == "World":
                out.update(REGION_KEYS)
                continue
            out.add(k)
            if k == "Europe":
                out.update(EUROPE_KEYS)
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
        sm = _SERIAL.search(gm.group(2))
        serial = sm.group(1) if sm else ""
        for rm in _ROM.finditer(gm.group(2)):
            rom = rm.group(1)
            out.append(Entry(game, rom, int(rm.group(2)), rm.group(3).lower().zfill(8), title_of(game),
                             regions_of(game), kind_of(game, rom), serial))
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

    def download(self, dat: str, timeout: float = 60, source: str = "") -> int:
        """Fetches one list (No-Intro, or Redump for discs); returns its size in bytes."""
        if not source:
            from .consoles import CONSOLES  # noqa: PLC0415
            source = next((c.source for c in CONSOLES if c.dat == dat), "no-intro")
        if source == "titledb":                         # Switch: built from titledb (about 90 MB to fetch)
            from . import switch  # noqa: PLC0415
            n = switch.fetch(self.folder.parent / "titledb", timeout=max(timeout, 300))
            switch.build(self.folder.parent / "titledb", self.folder)
            self._cache.pop(dat, None)
            return n
        url = URL.format(source=source, name=urllib.parse.quote(dat))
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
