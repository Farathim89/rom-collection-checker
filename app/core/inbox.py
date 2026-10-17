"""Sorting an inbox folder (new downloads, packs): every game file is recognised by its checksum (else by its
file type), compared with what you have, and gets a plan line - move it in, or leave it (you have it, bad dump,
hack, unknown console). Nothing happens until the plan is applied; applying writes an undo log."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .collection import _TRACK, index, match_by_name, name_index, walk_files
from .consoles import ARCHIVES, BY_KEY, CONSOLES, Console, consoles_for_ext
from .dat import REGION_OF, DatStore, regions_of
from .hashing import Tools, file_items
from .probe import QUICK_CONSOLES, identify, probe, serial_index

NO_CHECKSUM = {"wbfs", "pbp", "cso", "chd", "cdi", "ciso", "nsp", "nsz", "xci", "xcz"}

BAD = re.compile(r"\[b\d*\]|\(Bad\)|\(Bad Dump\)", re.I)
HACK = re.compile(r"\[h\w*\]|\(Hack\)", re.I)
TRANSLATION = re.compile(r"\[T[+-][^\]]*\]", re.I)
SAFE = re.compile(r'[<>:"/\\|?*]')
# arcade sets (MAME, Neo Geo MVS ...) are lists of chips, not single games - not checked here
ARCADE = re.compile(r"\b(MAME|FBA|FB ?Neo|Final ?Burn|Arcade|CPS ?[123]?|Neo ?Geo(?! ?Pocket)(?! ?CD))\b", re.I)


def norm_title(name: str) -> str:
    """'Sonic The Hedgehog (W) [!].gen' and 'Sonic the Hedgehog (USA, Europe)' -> 'sonic the hedgehog'."""
    t = re.sub(r"\s*[(\[].*$", "", name)
    t = re.sub(r"\.[A-Za-z0-9]{1,4}$", "", t)          # an extension left when there were no tags
    t = re.sub(r"^\d{3,4}\s*-\s*", "", t)              # "5592 - Yu-Gi-Oh" pack numbers
    t = re.sub(r",\s*(the|a|an)$", "", t, flags=re.I)  # "Legend of Zelda, The"
    return re.sub(r"[^a-z0-9]+", " ", t.lower().replace("&", "and")).strip()


def console_from_folders(path: Path, inbox: Path, candidates: list[Console]) -> Console | None:
    """A pack's folder names ('Atari 2600\\roms', 'Sega Mega Drive (Sega Genesis)') name the console when the
    file type is shared (.bin, .rom). The file name, then the deepest folder, that names exactly one wins."""
    try:
        parts = (*path.parent.relative_to(inbox).parts, path.stem)   # the file name counts first
    except ValueError:
        return None
    for part in reversed(parts):
        low = re.sub(r"[._]+", " ", part.lower())          # "Forza.Horizon.(Xbox.360)" names Xbox 360
        best: dict[str, int] = {}          # console -> length of its longest name in this folder name
        for c in candidates:
            for n in names_of(c):
                if low == n.lower():
                    return c               # an exact folder name ("Game Boy Color") settles it
                if len(n) > 2 and re.search(r"(?<![a-z0-9])" + re.escape(n.lower()) + r"(?![a-z0-9])", low):
                    best[c.key] = max(best.get(c.key, 0), len(n))
        if best:
            top = max(best.values())
            hits = [k for k, n in best.items() if n == top]   # "Game Boy Color" beats "Game Boy"
            if len(hits) == 1:
                return BY_KEY[hits[0]]
    return None


CHIP = re.compile(r"(?:^|[-_.])(?:[a-z]{1,3}\d{1,2}[a-z]?|\d{1,3}[a-z])\.(?:rom|bin)$|\.(?:[pcmsv]\d|u\d{1,3}|ic\d{1,3})$", re.I)
NOT_GAME = re.compile(r"\.(?:srm|sav|spc|rsp|st\d|\d{3}|state\d*|sv|cht|ips|bps|ups|txt|nfo|exe|me)$", re.I)


def is_arcade_set(items) -> bool:
    """An archive of chip dumps (MAME / Neo Geo MVS: 030-p1.rom, 030-c1.rom ...) is an arcade set, not a game file."""
    inner = [i.inner for i in items if i.inner]
    return len(inner) >= 3 and len(inner) == len(items) and \
        sum(bool(CHIP.search(Path(n).name)) for n in inner) >= 0.6 * len(inner)


def names_of(c: Console) -> tuple[str, ...]:
    return (c.name, inbox_name(c), *c.folders)


def inbox_name(c: Console) -> str:
    """A console's folder name when there is none in your collection: 'NES - Famicom'."""
    return SAFE.sub("-", c.name.replace(" / ", " - "))


def make_console_folders(inbox: str, folders: dict[str, list[str]], games_root: str) -> list[str]:
    """Empty maker\\console folders in the inbox, laid out like your collection (Nintendo\\Nintendo 8-Bit) or
    maker\\console for consoles you don't have yet. A game put in one goes to that console when sorted.
    Returns the folders made (existing ones are left as they are)."""
    made = []
    for c in CONSOLES:
        rel = None
        for f in folders.get(c.key, []):
            try:
                rel = Path(f).relative_to(games_root)
                break
            except ValueError:
                continue
        if rel is None or not rel.parts:
            rel = Path(c.maker) / inbox_name(c)
        target = Path(inbox) / rel
        if not target.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            made.append(str(target))
    return made


REGION_NAMES = set(REGION_OF) | {"World"}
REGION_FOLDERS = [False]          # make_plan(region_folders=...): sorted games go into <console>\<region>\


def region_folder(game: str) -> str:
    """The region folder of a No-Intro / Redump name: its region (Japan, Germany …), or its first one when all its
    regions are of one group ("Europe, Australia" -> Europe); World when they span groups ("USA, Europe") or the
    name says World; "" when the name has no region."""
    rs = [r for r in regions_of(game) if r in REGION_NAMES]
    if not rs:
        return ""
    return rs[0] if len({REGION_OF.get(r, r) for r in rs}) == 1 else "World"

def _game_dirs(dest: Path) -> list[Path]:
    """The folders under a console folder that could be one game's own folder (also inside region folders)."""
    out = []
    for q in dest.iterdir():
        if q.is_dir() and not q.name.startswith(("0 - ", "_")):
            out += [r for r in q.iterdir() if r.is_dir()] if q.name in REGION_NAMES else [q]
    return out


def place(dest: Path, filename: str, folder: bool = False, region: str = "") -> str:
    """Where a game goes: the console folder (its region folder in it, when sorting by region), in a folder of its
    own when the games there each have one (Wii / GameCube: Game/Game.wbfs). folder: the target is a folder
    already (a disc of several files)."""
    d = dest / region if region else dest
    if not folder and dest.is_dir():
        ext = Path(filename).suffix.lower()
        subs = _game_dirs(dest)
        tops = [dest] + [q for q in dest.iterdir() if q.is_dir() and q.name in REGION_NAMES]
        loose = any(q.is_file() and q.suffix.lower() == ext for t in tops for q in t.iterdir())
        if subs and not loose and any(r.suffix.lower() == ext for q in subs[:20] for r in q.iterdir() if r.is_file()):
            d = d / Path(filename).stem
    return str(d / filename)


def console_dest(c: Console, folders: dict[str, list[str]], games_root: str) -> Path:
    return Path((folders.get(c.key) or [str(Path(games_root) / c.maker / c.folders[0])])[0])


def region_of_step(game: str) -> str:
    return region_folder(game) if REGION_FOLDERS[0] and game else ""

@dataclass
class Step:
    source: str                 # the file in the inbox
    inner: str = ""             # entry inside an archive ("" = the file itself)
    console: str = ""           # console key ("" = not recognised)
    game: str = ""              # No-Intro name when known
    action: str = "leave"       # "move" | "leave"
    reason: str = ""            # why (shown in the plan)
    target: str = ""            # where it goes
    unpack: bool = False        # take the ROM out of its archive
    enabled: bool = True
    done: bool = False          # set by apply_plan when the game is in the collection
    files: list[str] = field(default_factory=list)   # a disc of several files (.cue + tracks): all move into target
    unpack_all: bool = False    # a disc in an archive: everything in it goes into the target folder
    by_name: bool = False       # a disc recognised by its name only
    by_header: bool = False     # a disc named by its header (serial / game ID), not by a checksum

    @property
    def label(self) -> str:
        return f"{Path(self.source).name} › {self.inner}" if self.inner else Path(self.source).name


@dataclass
class Plan:
    inbox: str
    steps: list[Step] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.steps:
            out[s.reason] = out.get(s.reason, 0) + 1
        return out


def make_plan(inbox: str, folders: dict[str, list[str]], have_crcs: dict[str, set[str]], dats: DatStore,
              tools: Tools, games_root: str, *, have_games: dict[str, set[str]] | None = None, unpack=True,
              official_names=True, take_hacks=False,
              take_unknown_dumps=True, region_folders=False, progress=lambda t: None, stop=lambda: False) -> Plan:
    """have_crcs: console key -> every CRC variant of the games you already have. region_folders: games go into
    <console>/<region>/ (Japan, Germany …; World when released in several)."""
    REGION_FOLDERS[0] = region_folders
    plan = Plan(inbox)
    _DATS_FOR_BIG[:] = [dats]
    ixs = {c.key: index(dats.entries(c.dat)) for c in CONSOLES if dats.have(c.dat)}
    owned: dict[str, set[str]] = {}             # console -> titles you have (for dumps No-Intro doesn't know)
    for key, have in have_crcs.items():
        if key in BY_KEY and dats.have(BY_KEY[key].dat):
            owned[key] = {norm_title(e.title) for e in dats.entries(BY_KEY[key].dat) if e.crc in have}
    all_exts = {e for c in CONSOLES for e in c.exts} | set(ARCHIVES)
    files = walk_files([inbox], all_exts, [])
    seen: dict[tuple[str, str], str] = {}       # (console, crc) -> first inbox file with it
    disc_names: dict[str, dict[str, str]] = {}   # console -> Redump name index (for discs matched by name)
    serials: dict[str, dict] = {}                # console -> serial index (for discs named by their header)
    seen_games: dict[tuple[str, str], str] = {}  # (console, game) -> first inbox file with it
    for n, p in enumerate(files, 1):
        if stop():
            break
        try:
            mb = p.stat().st_size / 1048576
        except OSError:
            mb = 0
        big = f" ({mb / 1024:.1f} GB - unpacking to read it)" if mb > 512 and p.suffix.lower() in (".7z", ".rar")             else f" ({mb / 1024:.1f} GB)" if mb > 1024 else ""
        progress(f"Checking {n} / {len(files)}: {p.name}{big}")
        try:
            rel = p.relative_to(inbox).parent.parts
        except ValueError:
            rel = ()
        if any(ARCADE.search(part) for part in rel):
            plan.steps.append(Step(str(p), reason="arcade (not checked)"))
            continue
        # images that can't match a checksum (trimmed .wbfs, .pbp, .cso …) aren't read whole: their header names them;
        # a big disc image (> 1 GB) too, when its header names it (Xbox 360 / PS2 / Wii …: seconds instead of minutes)
        quick = _big_disc(p, folder_hint=console_from_folders(p, Path(inbox), list(CONSOLES)))
        if quick:
            key, game, size = quick
            st = Step(str(p), "", console=key, game=game, by_header=True)
            plan.steps.append(st)
            _decide(st, p.name, p.suffix.lower(), have_games, seen_games, folders, games_root, official_names)
            continue
        items, err = file_items(p, tools, all_exts - set(ARCHIVES), NO_CHECKSUM)
        if err or not items:
            plan.steps.append(Step(str(p), reason="unreadable" if err else "no game inside"))
            continue
        if is_arcade_set(items):
            plan.steps.append(Step(str(p), reason="arcade (not checked)"))
            continue
        if all(NOT_GAME.search(it.inner or p.name) for it in items):
            plan.steps.append(Step(str(p), reason="save / music / patch (not a game)"))
            continue
        for it in items:
            name = it.inner or p.name
            st = Step(str(p), it.inner, unpack=bool(it.inner) and unpack)
            # which console: a No-Intro match first, else the file type (when only one console uses it)
            hit = None
            for key, ix in ixs.items():
                e = next((ix[c][0] for c in it.crcs if c in ix), None)
                if e:
                    hit = (key, e)
                    break
            if hit:
                st.console, st.game = hit[0], hit[1].game
            else:
                cs = consoles_for_ext(Path(name).suffix)
                if len(cs) == 1:
                    st.console = cs[0].key
                elif cs:
                    c1 = console_from_folders(p, Path(inbox), cs)
                    if c1:
                        st.console = c1.key
            plan.steps.append(st)
            if not st.console:
                st.reason = "unknown console"
                continue
            c = BY_KEY[st.console]
            ext = Path(name).suffix.lower().lstrip(".")
            if not st.game and not it.inner and c.key in QUICK_CONSOLES:
                if c.key not in serials:
                    serials[c.key] = serial_index(dats.entries(c.dat))
                pr = probe(p, c.key, tools.dolphin)
                g = identify(pr, serials[c.key], p, it.size)[0] if pr else ""
                if g:
                    st.game, st.by_header = g, True
            if not st.game and c.source == "redump" and (ext in c.name_only or ext in c.name_fallback or not it.crcs):
                if c.key not in disc_names:
                    disc_names[c.key] = name_index(dats.entries(c.dat))
                g = match_by_name(Path(p.parent / name) if it.inner else p, disc_names[c.key])
                if g:
                    st.game, st.by_name = g, True
            if any(x in have_crcs.get(st.console, set()) for x in it.crcs) or                     (st.game and not st.by_name and st.game in (have_games or {}).get(st.console, ())):
                st.reason = "you have it"      # by checksum, or the same game named by its header in your folder
                continue
            if st.by_name and st.game in (have_games or {}).get(st.console, ()):
                st.reason = "you have it (by name)"   # the same game is in your folder; only the name says so
                continue
            first = next((seen[(st.console, x)] for x in it.crcs if (st.console, x) in seen), None)
            if not first and st.game and (st.by_name or st.by_header):    # a disc named twice (.wbfs and its .7z)
                first = seen_games.get((st.console, st.game))
            if first:
                st.reason = "same as another file here"
                continue
            for x in it.crcs:
                seen[(st.console, x)] = st.label
            if st.game:
                seen_games.setdefault((st.console, st.game), st.label)
            if not st.game:
                if BAD.search(name):
                    st.reason = "bad dump"
                    continue
                if HACK.search(name) and not TRANSLATION.search(name) and not take_hacks:
                    st.reason = "hack"
                    continue
                if norm_title(Path(name).name) in owned.get(st.console, ()):
                    st.reason = "other dump of a game you have"
                    continue
                if not take_unknown_dumps:
                    st.reason = "not in No-Intro"
                    continue
            not_in = "new (not in Redump)" if c.source == "redump" else "new (not in No-Intro)"
            st.action, st.reason = "move", ("new (by name)" if st.by_name else "new (identified by header)"
                                            if st.by_header else "new") if st.game else (
                "translation" if TRANSLATION.search(name) else not_in)
            dest = console_dest(c, folders, games_root)
            if c.keep_archives and it.inner and len(items) == 1:   # one game per archive: it stays packed
                st.unpack = False
            if it.inner and not st.unpack:          # the archive itself moves (DS .7z)
                base, ext = p.stem, p.suffix
            else:                                    # a loose ROM, or the ROM taken out of its archive
                base, ext = Path(name).stem, Path(name).suffix
            if st.game and official_names:
                base = st.game
            st.target = place(dest, SAFE.sub("_", base) + ext.lower(), region=region_of_step(st.game))
    _join_discs(plan, folders, games_root, official_names)
    return plan


_SERIALS: dict[str, dict] = {}
_DATS_FOR_BIG: list = []


def _big_disc(p: Path, folder_hint=None) -> tuple[str, str, int] | None:
    """(console, game, size) for a disc image over 1 GB that its header names (no checksum read)."""
    try:
        size = p.stat().st_size
    except OSError:
        return None
    ext = p.suffix.lower().lstrip(".")
    if size < 1 << 30 or ext not in ("iso", "gcm", "bin", "img") or not _DATS_FOR_BIG:
        return None
    dats = _DATS_FOR_BIG[0]
    keys = [k for k in QUICK_CONSOLES if ext in BY_KEY[k].exts and dats.have(BY_KEY[k].dat)]
    if folder_hint is not None and folder_hint.key in keys:
        keys = [folder_hint.key] + [k for k in keys if k != folder_hint.key]
    for key in keys:
        pr = probe(p, key)
        if not pr:
            continue
        if key not in _SERIALS:
            _SERIALS[key] = serial_index(dats.entries(BY_KEY[key].dat))
        g = identify(pr, _SERIALS[key], p, size)[0]
        if g:
            return key, g, size
    return None


def _decide(st: Step, name: str, ext: str, have_games, seen_games, folders, games_root, official_names) -> None:
    """A disc named by its header: you have it, the same as another file here, or move it in."""
    if st.game in (have_games or {}).get(st.console, ()):
        st.reason = "you have it"
        return
    first = seen_games.get((st.console, st.game))
    if first:
        st.reason = "same as another file here"
        return
    seen_games[(st.console, st.game)] = st.label
    c = BY_KEY[st.console]
    dest = console_dest(c, folders, games_root)
    base = st.game if official_names else Path(name).stem
    st.action, st.reason = "move", "new (identified by header)"
    st.target = place(dest, SAFE.sub("_", base) + ext, region=region_of_step(st.game))


DESCRIPTORS = ("cue", "gdi", "m3u", "ccd", "sub", "img", "mds")


def disc_base(name: str) -> str:
    """"Sonic Adventure (USA) (Track 2).bin" -> "Sonic Adventure (USA)"."""
    return _TRACK.sub("", Path(name).stem).strip()


def _join_discs(plan: Plan, folders: dict[str, list[str]], games_root: str, official_names: bool) -> None:
    """A disc of several files (.cue / .gdi + its tracks) is one move into <console>\\<name>\\ (the track names stay:
    the .cue points at them); a disc in an archive is unpacked whole into that folder."""
    groups: dict[tuple[str, str, str], list[Step]] = {}
    for st in plan.steps:
        if st.console and BY_KEY[st.console].source == "redump" and st.reason != "unknown console":
            key = (st.source.lower(), "", st.console) if st.inner else                 (str(Path(st.source).parent).lower(), disc_base(st.source).lower(), st.console)
            groups.setdefault(key, []).append(st)
    for (_where, base, key), steps in groups.items():
        c = BY_KEY[key]
        if any(s.reason in ("you have it", "same as another file here") for s in steps):
            for s in steps:                    # one known track = you have this disc
                s.action, s.target = "leave", ""
                if s.reason not in ("you have it", "same as another file here"):
                    s.reason = "you have it"
            continue
        lead = next((s for s in steps if s.action == "move"), None)
        if lead is None:
            continue
        game = next((s.game for s in steps if s.game and not s.by_name), "") or next((s.game for s in steps if s.game), "")
        dest = console_dest(c, folders, games_root)
        reg = region_of_step(game)
        name = SAFE.sub("_", game if game and official_names else
                        (Path(lead.source).stem if lead.inner else disc_base(lead.source)))
        if lead.inner:
            if len(steps) > 1 or Path(lead.inner).suffix.lower().lstrip(".") in ("bin", "raw"):
                lead.unpack_all, lead.target = True, place(dest, name, folder=True, region=reg)
        else:
            src_dir = Path(lead.source).parent
            files = sorted({s.source for s in steps} | {
                str(q) for q in src_dir.iterdir() if q.is_file() and q.suffix.lower().lstrip(".") in DESCRIPTORS
                and disc_base(q.name).lower() == base})
            if len(files) > 1:
                lead.files, lead.target = files, place(dest, name, folder=True, region=reg)
            else:
                lead.target = place(dest, name + Path(lead.source).suffix.lower(), region=reg)
        lead.game = game or lead.game
        for s in steps:
            if s is not lead:
                s.action, s.target, s.reason = "leave", "", "part of the disc above"
                s.enabled = False


def apply_plan(plan: Plan, tools: Tools, undo_dir: Path, progress=lambda t: None) -> tuple[int, list[str], Path]:
    """Moves the enabled 'move' steps. Never overwrites. Archives that are unpacked stay in the inbox.
    Returns (moved, problems, undo log)."""
    done: list[dict] = []
    problems: list[str] = []
    targets: set[str] = set()
    unpacked: dict[str, Path | None] = {}       # archive -> its temp folder (each archive is unpacked once)
    work = tempfile.TemporaryDirectory(prefix="romsort-")
    todo = [s for s in plan.steps if s.enabled and s.action == "move"]
    for n, s in enumerate(todo, 1):
        progress(f"Moving {n} / {len(todo)}: {s.label}")
        t = Path(s.target)
        if t.exists() or str(t).lower() in targets:
            problems.append(f"already there, left in the inbox: {t}")
            continue
        try:
            t.parent.mkdir(parents=True, exist_ok=True)
            if s.files:                      # a disc of several files: all of them into its folder
                t.mkdir(parents=True)
                for f in s.files:
                    if Path(f).exists():
                        shutil.move(f, t / Path(f).name)
                        done.append({"op": "moved", "from": f, "to": str(t / Path(f).name)})
                s.done = True
                targets.add(str(t).lower())
                continue
            if s.inner and s.unpack_all:     # a disc in an archive: everything in it into its folder
                t.mkdir(parents=True)
                progress(f"Moving {n} / {len(todo)}: unpacking {Path(s.source).name}…")
                if not tools.extract_all(Path(s.source), t):
                    problems.append(f"could not unpack: {s.source}")
                    continue
                for q in sorted(t.iterdir()):
                    done.append({"op": "created", "path": str(q)})
                s.done = True
                targets.add(str(t).lower())
                continue
            if s.inner and s.unpack:
                if s.source not in unpacked:
                    tmp = Path(work.name) / str(len(unpacked))
                    tmp.mkdir()
                    progress(f"Moving {n} / {len(todo)}: unpacking {Path(s.source).name}…")
                    unpacked[s.source] = tmp if tools.extract_all(Path(s.source), tmp) else None
                tmp = unpacked[s.source]
                if tmp is None:
                    problems.append(f"could not unpack: {s.source}")
                    continue
                got = tmp / s.inner
                if not got.is_file():
                    got = next(tmp.rglob(Path(s.inner).name), got)
                if not got.is_file():
                    problems.append(f"{s.inner} not found in {s.source}")
                    continue
                shutil.copy2(got, t)
                done.append({"op": "created", "path": str(t)})
                s.done = True
            else:
                if not Path(s.source).exists():
                    continue   # an archive with several games already moved by an earlier step
                shutil.move(s.source, t)
                done.append({"op": "moved", "from": s.source, "to": str(t)})
                s.done = True
            targets.add(str(t).lower())
        except OSError as e:
            problems.append(f"{s.label}: {e}")
    work.cleanup()
    return len(done), problems, write_undo(undo_dir, "sort", done)


HAD = ("you have it", "same as another file here")


def leftovers(plan: Plan, games_root: str) -> list[tuple[Path, Path]]:
    """After a sort: inbox files whose every game is now in your collection - games you already had, and archives
    whose games were taken out - with where they go: <games>\\_Duplicates\\<inbox name>\\<same path>."""
    inbox = Path(plan.inbox)
    dest = Path(games_root) / "_Duplicates" / inbox.name
    by: dict[str, list[Step]] = {}
    for st in plan.steps:
        by.setdefault(st.source, []).append(st)
    out = []
    for src, steps in by.items():
        if all(st.reason in HAD or st.done for st in steps) and Path(src).is_file():
            try:
                out.append((Path(src), dest / Path(src).relative_to(inbox)))
            except ValueError:
                continue
    return out


def drop_folder_names() -> set[str]:
    """The Maker\\Console folders 'Make console folders' makes - they stay when the inbox is tidied."""
    names = {c.maker.lower() for c in CONSOLES}
    for c in CONSOLES:
        names.update(n.lower() for n in names_of(c))
    return names


def tidy_inbox(plan: Plan, games_root: str, undo_dir: Path) -> tuple[int, int, list[str], Path]:
    """Moves the leftovers to _Duplicates (logged for Undo) and removes the empty folders they leave
    (not the Maker\\Console drop folders). Returns (files moved, folders removed, problems, undo log)."""
    done, problems = [], []
    for src, dst in leftovers(plan, games_root):
        try:
            q, k = dst, 2
            while q.exists():
                q = dst.with_name(f"{dst.stem} ({k}){dst.suffix}")
                k += 1
            q.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(q))
            done.append({"op": "moved", "from": str(src), "to": str(q)})
        except OSError as e:
            problems.append(f"{src}: {e}")
    keep = drop_folder_names()
    removed = 0
    inbox = Path(plan.inbox)
    for d in sorted((p for p in inbox.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if d.name.lower() in keep:
            continue
        try:
            d.rmdir()            # only succeeds when the folder is empty - nothing is deleted
            removed += 1
        except OSError:
            pass
    return len(done), removed, problems, write_undo(undo_dir, "inbox tidy", done)


def write_undo(undo_dir: Path, kind: str, done: list[dict]) -> Path:
    undo_dir.mkdir(parents=True, exist_ok=True)
    p = undo_dir / f"{time.strftime('%Y-%m-%d %H-%M-%S')} {kind}.json"
    p.write_text(json.dumps({"kind": kind, "steps": done}, indent=1, ensure_ascii=False), encoding="utf-8")
    return p


def undo(log: Path) -> tuple[int, list[str]]:
    """Reverses a sort or rename log (newest step first). Created copies go to the Recycle Bin."""
    data = json.loads(log.read_text(encoding="utf-8"))
    n, problems = 0, []
    for s in reversed(data["steps"]):
        try:
            if s["op"] in ("moved", "renamed"):
                if Path(s["from"]).exists():
                    problems.append(f"in the way, not moved back: {s['from']}")
                    continue
                Path(s["from"]).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(s["to"], s["from"])
            elif s["op"] == "created":
                p = Path(s["path"])
                if p.exists() and recycle_bin_off(p):     # never deleted for good: parked next to the logs
                    park = log.parent / "removed copies" / p.name
                    park.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(park))
                else:
                    recycle(p)
            n += 1
        except OSError as e:
            problems.append(str(e))
    log.rename(log.with_suffix(".undone.json"))
    return n, problems


def recycle_bin_off(p: Path) -> bool:
    """True when Windows deletes files on this drive at once instead of keeping them in the Recycle Bin
    (also when the drive's Recycle Bin is too small for the file)."""
    try:
        import ctypes  # noqa: PLC0415
        import winreg  # noqa: PLC0415
        buf = ctypes.create_unicode_buffer(64)
        root = str(Path(p).resolve().anchor)
        if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(root, buf, 64):
            return False
        guid = buf.value[buf.value.index("{"):buf.value.index("}") + 1]
        key = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume\{guid}"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            nuke = winreg.QueryValueEx(k, "NukeOnDelete")[0]
            try:
                cap_mb = winreg.QueryValueEx(k, "MaxCapacity")[0]
            except OSError:
                cap_mb = 0
        size = sum(f.stat().st_size for f in Path(p).rglob("*") if f.is_file()) if Path(p).is_dir() else Path(p).stat().st_size
        return bool(nuke) or (cap_mb and size > cap_mb * 1024 * 1024)
    except (OSError, ValueError, AttributeError, ImportError):
        return False


def recycle(p: Path) -> None:
    """To the Recycle Bin (Windows), never a hard delete. A drive whose Recycle Bin is off ("remove files
    immediately") would delete for good, so there it refuses."""
    if not p.exists():
        return
    if recycle_bin_off(p):
        raise OSError(f"the Recycle Bin is off on {p.drive} (files would be deleted for good) - left: {p}")
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
        op = SHFILEOPSTRUCTW(None, 3, str(p) + "\0", None, 0x0040 | 0x0010 | 0x0004 | 0x0400, False, None, None)
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) != 0:
            raise OSError(f"could not move to the Recycle Bin: {p}")
    except AttributeError as e:   # not Windows
        raise OSError("Recycle Bin is only available on Windows") from e


def plan_to_json(plan: Plan) -> str:
    return json.dumps({"inbox": plan.inbox, "steps": [asdict(s) for s in plan.steps]}, ensure_ascii=False)


def region_moves(res, root: Path) -> list[tuple[Path, Path]]:
    """Your games of one console into <console>/<region>/: (from, to) for each game - a loose file with the files
    of the same name next to it (its save, a .cue's tracks), or a game's own folder (a disc, a Wii game).
    Games in 0 - folders and files No-Intro doesn't list stay where they are."""
    if res.console.source == "titledb":                  # Switch: every title is "World"
        return []
    units: dict[Path, str] = {}
    for t in res.titles.values():
        for game, labels in t.have.items():
            reg = region_folder(game)
            for label in labels:
                p = Path(label.split(" › ")[0])
                try:
                    parts = p.relative_to(root).parts
                except ValueError:
                    continue
                if not reg or parts[0].startswith(("0 - ", "_")):
                    continue
                inner = parts[1:] if parts[0] in REGION_NAMES and len(parts) > 1 else parts
                top = root / parts[0] if inner is parts else root / parts[0] / inner[0]
                units.setdefault(top if len(inner) > 1 else p, reg)
    moves, taken = [], set()
    for src, reg in units.items():
        group = [src] if src.is_dir() else [q for q in src.parent.iterdir() if q.is_file() and
                                              disc_base(q.name).lower() == disc_base(src.name).lower()]
        for q in group:
            dst = root / reg / q.name
            if q.parent == dst.parent or q in taken:
                continue
            taken.add(q)
            moves.append((q, dst))
    return moves
