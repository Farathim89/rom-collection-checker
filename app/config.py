"""Settings: where your games are, the inbox to sort, which games count, the tools. Saved as settings.json in
the data folder."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .core.collection import SKIP_DEFAULT
from .core.dat import EUROPE_KEYS, REGION_KEYS
from .portable import PORTABLE

STATE: Path = PORTABLE if PORTABLE is not None else \
    Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "ROMCollectionChecker"
SETTINGS_FILE = STATE / "settings.json"
DATS = STATE / "dats"
UNDO = STATE / "undo"
CACHE = STATE / "scan-cache.json"
DOWNLOADS = STATE / "downloads.json"
EXTRA = STATE / "extra-dats"          # TOSEC / hacks / homebrew lists


@dataclass
class Settings:
    games_root: str = ""                 # e.g. E:\Games  (console folders, also inside maker folders)
    inbox: str = ""                      # a folder to sort new games out of
    folders: dict[str, list[str]] = field(default_factory=dict)   # console key -> folders (overrides the search)
    skip_folders: list[str] = field(default_factory=lambda: list(SKIP_DEFAULT))
    consoles: list[str] = field(default_factory=list)              # consoles to check ([] = every one found)
    count_kinds: list[str] = field(default_factory=lambda: ["game"])  # what counts: game, unlicensed, prototype…
    scope: str = "All"                   # (old setting, read once into regions)
    regions: list[str] = field(default_factory=lambda: list(REGION_KEYS))  # the ones shown (see dat.REGION_GROUPS)
    unpack: bool = True                  # take ROMs out of zips when sorting (not DS: emulators read .7z)
    official_names: bool = True          # sorted games get their No-Intro name
    take_hacks: bool = False             # hacks are sorted in too (translations always are)
    take_unknown: bool = True            # dumps No-Intro doesn't list (homebrew, translations, multicarts)
    sevenzip: str = ""                   # 7z.exe / 7za.exe (found by itself when empty)
    winrar_dir: str = ""                 # WinRAR's folder (found by itself when empty)
    dolphin_tool: str = ""               # DolphinTool.exe, for .rvz / .wbfs / .gcz discs (found by itself when empty)
    download_folder: str = ""            # where downloads go ("" = the inbox)
    download_parallel: int = 3           # downloads at the same time
    sort_after_downloads: bool = False   # open Sort inbox when the queue is done
    quick_check: bool = True             # discs, DS, N64: named by their header (Verify does the full checksum)
    tidy_inbox: bool = True              # after a sort: what you now have -> _Duplicates, empty folders go
    check_updates: bool = True           # look for a new version on GitHub at start (at most once a day)
    last_update_check: float = 0.0
    theme: str = "Gold (dark)"


def guess_games_root() -> str:
    for p in (r"D:\Games", r"E:\Games", r"D:\ROMs", r"E:\ROMs"):
        if Path(p).is_dir():
            return p
    return ""


def load_settings(path: Path = SETTINGS_FILE) -> Settings:
    s = Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s.games_root = guess_games_root()
        if s.games_root and (Path(s.games_root) / "_unsorted").is_dir():
            s.inbox = str(Path(s.games_root) / "_unsorted")
        return s
    for f in fields(Settings):
        if f.name in data:
            setattr(s, f.name, data[f.name])
    if "regions" not in data and s.scope in ("USA", "Europe", "Japan", "Other"):
        s.regions = [s.scope]
    old = {"USA": ["USA"], "Europe": list(EUROPE_KEYS), "Japan": ["Japan"],
           "Other": ["Asia", "Korea", "China", "Taiwan", "Brazil", "Other"]}
    s.regions = [r for r in s.regions if r != "World"]
    if s.regions and set(s.regions) <= set(old) and not set(s.regions) & {"Germany", "Asia"}:
        s.regions = list(dict.fromkeys(k for r in s.regions for k in old[r]))   # the 4 regions before countries
        if len(s.regions) >= len(REGION_KEYS) - 1:
            s.regions = list(REGION_KEYS)
    return s


def save_settings(s: Settings, path: Path = SETTINGS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(s), ensure_ascii=False, indent=1), encoding="utf-8")
