"""A second check for what No-Intro doesn't list: TOSEC (every known dump, also demos, public domain, translations
and hacks, with flags like [tr en], [h], [b], [o]) and libretro's hacks and homebrew lists. Downloaded on request
into <data>/dats/extra/<set>/<console>.dat."""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .dat import parse

URL = "https://raw.githubusercontent.com/libretro/libretro-database/master/metadat/{}/{}.dat"
SETS = ("tosec", "hacks", "homebrew")
# what libretro has, per set (names as in libretro-database/metadat/<set>)
AVAILABLE = {
    "tosec": ["Atari - 2600", "Nintendo - Game Boy Advance", "Nintendo - Game Boy Color", "Nintendo - Game Boy",
              "Nintendo - Nintendo Entertainment System", "Nintendo - Super Nintendo Entertainment System", "Sega - 32X",
              "Sega - Master System - Mark III", "Sega - Mega Drive - Genesis", "Sega - SG-1000"],
    "hacks": ["NEC - PC Engine - TurboGrafx 16", "Nintendo - Game Boy Advance", "Nintendo - Game Boy Color",
              "Nintendo - Game Boy", "Nintendo - Nintendo 64", "Nintendo - Nintendo DS",
              "Nintendo - Nintendo Entertainment System", "Nintendo - Super Nintendo Entertainment System",
              "SNK - Neo Geo Pocket Color", "Sega - 32x", "Sega - Master System - Mark III", "Sega - Mega Drive - Genesis"],
    "homebrew": ["Nintendo - Game Boy Advance", "Nintendo - Game Boy", "Nintendo - Nintendo Entertainment System",
                 "Nintendo - Super Nintendo Entertainment System", "Sega - Mega Drive - Genesis"],
}
_FLAGS = re.compile(r"\[([^\]]*)\]")


@dataclass(frozen=True)
class Known:
    source: str      # tosec / hacks / homebrew
    name: str        # its name in that list
    kind: str        # release / translation / hack / homebrew / bad


def kind_of(source: str, name: str) -> str:
    """TOSEC flags: [tr xx] translation, [h] [f] [t] [p] hack / fix / trainer / pirate, [b] [o] bad / overdump."""
    if source == "homebrew":
        return "homebrew"
    flags = [f.strip().lower() for f in _FLAGS.findall(name)]
    code = lambda f, codes: re.match(rf"^(?:{codes})\d*(?: |$)", f)  # noqa: E731 - "[h2 title]" yes, "[mapper 15]" no
    if any(code(f, "b|o|u") for f in flags):              # bad dump / overdump / underdump
        return "bad"
    if any(code(f, "tr") for f in flags) or re.search(r"\[T[-+]", name):
        return "translation"
    if source == "hacks" or any(code(f, "h|f|t|p|cr|m|v") for f in flags):
        return "hack"
    return "release"


class ExtraStore:
    def __init__(self, folder: Path):
        self.folder = folder
        self._cache: dict[str, dict[str, Known]] = {}

    def lists_for(self, dat: str) -> list[tuple[str, str]]:
        """(set, list name) libretro has for a console's No-Intro dat name (32X is spelled 'Sega - 32x' in hacks)."""
        return [(s, n) for s, names in AVAILABLE.items() for n in names if n.lower() == dat.lower()]

    def path(self, s: str, name: str) -> Path:
        return self.folder / s / f"{name}.dat"

    def have_any(self) -> bool:
        return any((self.folder / s).glob("*.dat") for s in SETS if (self.folder / s).is_dir())

    def missing(self, dats: list[str]) -> list[tuple[str, str]]:
        return [x for d in dats for x in self.lists_for(d) if not self.path(*x).is_file()]

    def download(self, s: str, name: str, timeout: float = 60) -> int:
        req = urllib.request.Request(URL.format(s, urllib.parse.quote(name)), headers={"User-Agent": "ROMCollectionChecker"})
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - fixed https host
            data = r.read()
        p = self.path(s, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.with_suffix(".part").write_bytes(data)
        p.with_suffix(".part").replace(p)
        self._cache.clear()
        return len(data)

    def index(self, dat: str) -> dict[str, Known]:
        """crc -> what it is, from every extra list of this console (TOSEC first)."""
        if dat not in self._cache:
            out: dict[str, Known] = {}
            for s, name in self.lists_for(dat):
                p = self.path(s, name)
                if not p.is_file():
                    continue
                for e in parse(p.read_text(encoding="utf-8", errors="replace")):
                    out.setdefault(e.crc, Known(s, e.game, kind_of(s, e.game)))
            self._cache[dat] = out
        return self._cache[dat]
