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

from .collection import index, walk_files
from .consoles import ARCHIVES, BY_KEY, CONSOLES, Console, consoles_for_ext
from .dat import DatStore
from .hashing import Tools, file_items

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
        low = part.lower()
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
              tools: Tools, games_root: str, *, unpack=True, official_names=True, take_hacks=False,
              take_unknown_dumps=True, progress=lambda t: None, stop=lambda: False) -> Plan:
    """have_crcs: console key -> every CRC variant of the games you already have."""
    plan = Plan(inbox)
    ixs = {c.key: index(dats.entries(c.dat)) for c in CONSOLES if dats.have(c.dat)}
    owned: dict[str, set[str]] = {}             # console -> titles you have (for dumps No-Intro doesn't know)
    for key, have in have_crcs.items():
        if key in BY_KEY and dats.have(BY_KEY[key].dat):
            owned[key] = {norm_title(e.title) for e in dats.entries(BY_KEY[key].dat) if e.crc in have}
    all_exts = {e for c in CONSOLES for e in c.exts} | set(ARCHIVES)
    files = walk_files([inbox], all_exts, [])
    seen: dict[tuple[str, str], str] = {}       # (console, crc) -> first inbox file with it
    for n, p in enumerate(files, 1):
        if stop():
            break
        if n % 20 == 0:
            progress(f"Checking {n} / {len(files)}: {p.name}")
        try:
            rel = p.relative_to(inbox).parent.parts
        except ValueError:
            rel = ()
        if any(ARCADE.search(part) for part in rel):
            plan.steps.append(Step(str(p), reason="arcade (not checked)"))
            continue
        items, err = file_items(p, tools, all_exts - set(ARCHIVES))
        if err or not items:
            plan.steps.append(Step(str(p), reason="unreadable" if err else "no game inside"))
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
            if any(x in have_crcs.get(st.console, set()) for x in it.crcs):
                st.reason = "you have it"
                continue
            first = next((seen[(st.console, x)] for x in it.crcs if (st.console, x) in seen), None)
            if first:
                st.reason = "same as another file here"
                continue
            for x in it.crcs:
                seen[(st.console, x)] = st.label
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
            st.action, st.reason = "move", "new" if st.game else ("translation" if TRANSLATION.search(name)
                                                                    else "new (not in No-Intro)")
            dest = Path((folders.get(c.key) or [str(Path(games_root) / c.maker / c.folders[0])])[0])
            if c.keep_archives and it.inner and len(items) == 1:   # one game per archive: it stays packed
                st.unpack = False
            if it.inner and not st.unpack:          # the archive itself moves (DS .7z)
                base, ext = p.stem, p.suffix
            else:                                    # a loose ROM, or the ROM taken out of its archive
                base, ext = Path(name).stem, Path(name).suffix
            if st.game and official_names:
                base = st.game
            st.target = str(dest / (SAFE.sub("_", base) + ext.lower()))
    return plan


def apply_plan(plan: Plan, tools: Tools, undo_dir: Path, progress=lambda t: None) -> tuple[int, list[str], Path]:
    """Moves the enabled 'move' steps. Never overwrites. Archives that are unpacked stay in the inbox.
    Returns (moved, problems, undo log)."""
    done: list[dict] = []
    problems: list[str] = []
    targets: set[str] = set()
    unpacked: dict[str, Path | None] = {}       # archive -> its temp folder (each archive is unpacked once)
    work = tempfile.TemporaryDirectory(prefix="romsort-")
    for n, s in enumerate([s for s in plan.steps if s.enabled and s.action == "move"], 1):
        progress(f"Moving {n}: {s.label}")
        t = Path(s.target)
        if t.exists() or str(t).lower() in targets:
            problems.append(f"already there, left in the inbox: {t}")
            continue
        try:
            t.parent.mkdir(parents=True, exist_ok=True)
            if s.inner and s.unpack:
                if s.source not in unpacked:
                    tmp = Path(work.name) / str(len(unpacked))
                    tmp.mkdir()
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
            else:
                if not Path(s.source).exists():
                    continue   # an archive with several games already moved by an earlier step
                shutil.move(s.source, t)
                done.append({"op": "moved", "from": s.source, "to": str(t)})
            targets.add(str(t).lower())
        except OSError as e:
            problems.append(f"{s.label}: {e}")
    work.cleanup()
    return len(done), problems, write_undo(undo_dir, "sort", done)


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
                recycle(Path(s["path"]))
            n += 1
        except OSError as e:
            problems.append(str(e))
    log.rename(log.with_suffix(".undone.json"))
    return n, problems


def recycle(p: Path) -> None:
    """To the Recycle Bin (Windows), never a hard delete."""
    if not p.exists():
        return
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
