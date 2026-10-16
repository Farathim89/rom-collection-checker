"""What you have: finds each console's folder in your games folder, reads every game file (cached by size and
date, so only new files are read again) and matches them against the console's No-Intro list."""
from __future__ import annotations

import json
import os
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .consoles import ARCHIVES, CONSOLES, Console
from .dat import DatStore, Entry
from .hashing import Item, Tools, file_items

# a "0 - Name" folder in a console folder is a collection of its own (MediaDeck shows it as a sub tab):
# 0 - Homebrew, 0 - Randomizer, 0 - Hacks ... - games No-Intro doesn't know are expected there
COLLECTION = re.compile(r"[\\/]0 - [^\\/]+[\\/]")
HOMEBREW_FOLDER = "0 - Homebrew"

SKIP_DEFAULT = ["_unsorted", "_Duplicates", "_Originals", "_Extras", "_unpacked", "_Lists"]


def find_folders(root: str, overrides: dict[str, list[str]] | None = None) -> dict[str, list[str]]:
    """console key -> its folders: from the settings, else the first folder (or maker\\folder) whose name is
    one of the console's usual names."""
    out: dict[str, list[str]] = {k: list(v) for k, v in (overrides or {}).items() if v}
    if not root or not Path(root).is_dir():
        return out
    names = {}
    for c in CONSOLES:
        for n in c.folders:
            names.setdefault(n.lower(), c.key)
    cands: list[Path] = []
    for d in Path(root).iterdir():
        if d.is_dir() and not d.name.startswith("_"):
            cands.append(d)
            try:
                cands.extend(s for s in d.iterdir() if s.is_dir() and not s.name.startswith("_"))
            except OSError:
                pass
    for d in cands:
        key = names.get(d.name.lower())
        if key and key not in out:
            out[key] = [str(d)]
    return out


class ScanCache:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.data: dict = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self.dirty = False

    def get(self, p: Path, st: os.stat_result) -> tuple[list[Item], str] | None:
        e = self.data.get(str(p))
        if e and e["s"] == st.st_size and e["m"] == int(st.st_mtime):
            return [Item(str(p), i[0], i[1], i[2]) for i in e["i"]], e.get("e", "")
        return None

    def get_probe(self, p: Path, st: os.stat_result) -> tuple[str, bool] | None:
        """A header check done before: (game or "", sure)."""
        e = self.data.get("probe:" + str(p))
        if e and e["s"] == st.st_size and e["m"] == int(st.st_mtime):
            return e["g"], e["sure"]
        return None

    def put_probe(self, p: Path, st: os.stat_result, game: str, sure: bool) -> None:
        self.data["probe:" + str(p)] = {"s": st.st_size, "m": int(st.st_mtime), "g": game, "sure": sure}
        self.dirty = True

    def put(self, p: Path, st: os.stat_result, items: list[Item], err: str) -> None:
        self.data[str(p)] = {"s": st.st_size, "m": int(st.st_mtime), "i": [[i.inner, i.size, i.crcs] for i in items],
                             "e": err}
        self.dirty = True

    def save(self) -> None:
        if self.dirty:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".part")
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            tmp.replace(self.path)
            self.dirty = False


def walk_files(folders: list[str], exts: set[str], skip: list[str]) -> list[Path]:
    skip_l = {s.lower() for s in skip}
    out: list[Path] = []
    for f in folders:
        for dirpath, dirnames, filenames in os.walk(f):
            dirnames[:] = [d for d in dirnames if d.lower() not in skip_l]
            for n in filenames:
                if Path(n).suffix.lower().lstrip(".") in exts:
                    out.append(Path(dirpath) / n)
    return out


@dataclass
class TitleInfo:
    title: str
    games: dict[str, list[Entry]] = field(default_factory=dict)    # game name -> its list entries
    have: dict[str, list[str]] = field(default_factory=dict)       # game name -> your files

    def kinds(self) -> set[str]:
        return {e.kind for es in self.games.values() for e in es}

    def games_in(self, kinds: set[str], scope) -> list[str]:
        """The versions of this title that count: scope is "All", one region or a set of regions
        (USA / Europe / Japan / Other)."""
        want = None if scope == "All" else ({scope} if isinstance(scope, str) else set(scope))
        out = []
        for g, es in self.games.items():
            e = es[0]
            if e.kind in kinds and (want is None or want & e.scopes):
                out.append(g)
        return out


@dataclass
class ConsoleResult:
    console: Console
    folders: list[str]
    dat_version: str = ""
    titles: dict[str, TitleInfo] = field(default_factory=dict)
    files: int = 0
    unknown: list[Item] = field(default_factory=list)          # not in the No-Intro list
    duplicates: dict[str, list[str]] = field(default_factory=dict)   # game -> 2+ files with that exact dump
    errors: dict[str, str] = field(default_factory=dict)          # unreadable files
    matched: dict[str, list[Entry]] = field(default_factory=dict)  # item label -> entries it matches
    crcs: set[str] = field(default_factory=set)                   # every CRC variant of every file you have
    known: dict = field(default_factory=dict)       # item label -> extra.Known (TOSEC / hacks / homebrew)
    others: list[Item] = field(default_factory=list)  # every file No-Intro doesn't list (also in 0 - folders)
    by_name: dict = field(default_factory=dict)     # item label -> game, for discs that can only be matched by name
    identified: dict = field(default_factory=dict)  # label -> (game, sure): named by its header, not verified
    extras: dict = field(default_factory=dict)      # Switch: game -> [(update / dlc, version, title id, file)]

    def counts(self, kinds: set[str], scope) -> tuple[int, int]:
        """(titles in scope, titles you have - any version of them counts)."""
        total = have = 0
        for t in self.titles.values():
            if t.games_in(kinds, scope):
                total += 1
                if t.have:
                    have += 1
        return total, have


def index(entries: list[Entry]) -> dict[str, list[Entry]]:
    ix: dict[str, list[Entry]] = {}
    for e in entries:
        ix.setdefault(e.crc, []).append(e)
    return ix


def scan_console(c: Console, folders: list[str], dats: DatStore, tools: Tools, cache: ScanCache,
                 skip: list[str], progress=lambda text: None, stop=lambda: False, extra=None,
                 quick: bool = False) -> ConsoleResult:
    """extra: an ExtraStore - files No-Intro doesn't list are looked up in TOSEC / hacks / homebrew too.
    quick: discs, DS and N64 games are named by their header (a few KB) instead of a checksum of the whole file;
    files that already have a checksum count as verified."""
    from .probe import QUICK_CONSOLES, identify, probe, serial_index  # noqa: PLC0415
    quick = quick and c.key in QUICK_CONSOLES
    sidx = serial_index(entries_for_quick(dats, c)) if quick else {}
    discs: dict[tuple[str, str], str] = {}        # (folder, disc name) -> game, for a disc's other tracks
    res = ConsoleResult(c, folders, dats.version(c.dat))
    known_ix = extra.index(c.dat) if extra is not None else {}
    entries = dats.entries(c.dat)
    ix = index(entries)
    for e in entries:
        res.titles.setdefault(e.title, TitleInfo(e.title)).games.setdefault(e.game, []).append(e)
    if c.source == "titledb":
        return _scan_switch(c, folders, res, entries, skip)
    exts = set(c.exts) | set(ARCHIVES)
    names, names_entries = ({}, {})
    by_game_entries: dict[str, list[Entry]] = {}
    if c.source == "redump":
        names_entries = {e.game: e for e in entries}
        names = name_index(entries)
        for e in entries:
            by_game_entries.setdefault(e.game, []).append(e)
    files = walk_files(folders, exts, skip)
    res.files = len(files)
    by_game: dict[str, list[str]] = {}
    same: dict[tuple[str, int], list] = {}       # (crc, size) of files No-Intro does not list -> [name, files]
    for n, p in enumerate(files, 1):
        if stop():
            break
        if n % 25 == 0:
            progress(f"{c.name}: {n} / {len(files)} files")
        try:
            st = p.stat()
        except OSError as e:
            res.errors[str(p)] = str(e)
            continue
        got = cache.get(p, st)
        if c.key == "ps3" and p.suffix.lower() == ".sfo" and                 not (p.name.upper() == "PARAM.SFO" and p.parent.name.upper() == "PS3_GAME"):
            continue                               # trophies, updates: only the game's own PARAM.SFO
        if got is None and quick and COLLECTION.search(str(p)):
            continue                               # 0 - Hacks / Translations …: on purpose, not owned games
        if got is None and quick:
            label = str(p.parent.parent) if p.suffix.lower() == ".sfo" else str(p)
            base = (str(p.parent).lower(), _TRACK.sub("", p.stem).strip().lower())
            if _TRACK.search(p.stem) and base in discs:     # another track of a disc named by its header
                res.titles[names_or_entry(entries, discs[base]).title].have.setdefault(discs[base], []).append(label)
                continue
            pg = cache.get_probe(p, st)
            if pg is None:
                pr = probe(p, c.key, tools.dolphin)
                pg = identify(pr, sidx, p, st.st_size) if pr else ("", False)
                cache.put_probe(p, st, *pg)
            if pg[0]:
                res.identified[label] = pg
                discs[base] = pg[0]
                res.titles[names_or_entry(entries, pg[0]).title].have.setdefault(pg[0], []).append(label)
                continue
            if c.key == "ps3":                      # a game folder can't get a checksum
                res.unknown.append(Item(label, "", st.st_size, []))
                continue
        if got is None:
            if stop() or tools.cancel.is_set():
                break
            got = file_items(p, tools, set(c.exts), set(c.name_only))
            if tools.cancel.is_set():
                break                         # stopped halfway through this file: not cached
            cache.put(p, st, *got)
        items, err = got
        if err:
            res.errors[str(p)] = err
        for it in items:
            found = [e for crc in it.crcs for e in ix.get(crc, [])]
            if not found and c.source == "redump" and it.crcs and not it.inner and names:
                # one .bin with all tracks merged: its start is Redump's Track 1 (checked once, then cached)
                e1 = merged_track1(p, it, names, by_game_entries)
                if e1:
                    it.crcs.append(e1.crc)
                    cache.put(p, st, items, err)
                    found = [e1]
            res.crcs.update(it.crcs)
            if not found and names and (not it.crcs or p.suffix.lower().lstrip(".") in c.name_fallback):
                # a disc format that can't be checked (or a trimmed .wbfs): by its name
                g = match_by_name(p, names)
                if g:
                    res.by_name[it.label] = g
                    e = names_entries[g]
                    res.titles[e.title].have.setdefault(g, []).append(it.label)
                    continue
            if not found:
                res.others.append(it)
                k = next((known_ix[x] for x in it.crcs if x in known_ix), None)
                if k:
                    res.known[it.label] = k
                if c.source == "no-intro" and it.crcs:     # the same dump twice, also when No-Intro doesn't list it
                    same.setdefault((it.crcs[0], it.size), [k.name if k else Path(it.inner or p.name).stem, []])[1].append(it.label)
                # a problem: not in a "0 - Homebrew" ... folder (those are on purpose), and not a known good dump
                if not COLLECTION.search(str(p)) and (k is None or k.kind == "bad"):
                    res.unknown.append(it)
                continue
            res.matched[it.label] = found
            for g in dict.fromkeys(e.game for e in found):
                e = next(x for x in found if x.game == g)
                res.titles[e.title].have.setdefault(g, []).append(it.label)
                for x in (y for y in found if y.game == g):   # per track: a disc's tracks aren't duplicates
                    by_game.setdefault((g, x.rom), []).append(it.label)
    # the other tracks of a disc whose track matched (Redump keeps some tracks in another form): part of that disc
    if c.source == "redump" and res.unknown:
        disc = {}
        for label, es in res.matched.items():
            disc[(str(Path(label).parent).lower(), _norm(_TRACK.sub("", Path(label).stem)))] = es[0].game
        keep = []
        for it in res.unknown:
            g = disc.get((str(Path(it.path).parent).lower(), _norm(_TRACK.sub("", Path(it.path).stem))))
            if g:
                res.titles[names_entries.get(g, next(e for e in entries if e.game == g)).title].have.setdefault(g, []).append(it.label)
            else:
                keep.append(it)
        res.unknown = keep
    dup: dict[str, list[str]] = {}
    for (g, _rom), fs in by_game.items():
        if len(set(fs)) > 1:
            dup.setdefault(g, [])
            dup[g] += [f for f in dict.fromkeys(fs) if f not in dup[g]]
    for name, fs in same.values():
        if len(set(fs)) > 1:
            dup.setdefault(name, [])
            dup[name] += [f for f in dict.fromkeys(fs) if f not in dup[name]]
    res.duplicates = dup
    return res


_TRACK = re.compile(r"\s*\(Track \d+\)", re.I)


def _scan_switch(c: Console, folders: list[str], res: ConsoleResult, entries: list[Entry], skip: list[str]):
    """Switch files by the title ID in their name (or the folder's): base games count; updates and DLC are noted
    with their game. A name without an ID is matched by name."""
    from . import switch  # noqa: PLC0415
    by_id = {e.serial.upper(): e for e in entries if e.serial}
    names = name_index(entries)
    files = walk_files(folders, set(c.exts), skip)
    res.files = len(files)
    for p in files:
        if not switch.first_part(p.name):
            continue
        label = str(p)
        ids = switch.ids_in(p.name) or switch.ids_in(p.parent.name)
        if not ids:
            g = match_by_name(p, names) or match_by_name(p.parent, names)
            if g:
                e = names_or_entry(entries, g)
                res.identified[label] = (g, False)
                res.titles[e.title].have.setdefault(g, []).append(label)
            else:
                res.unknown.append(Item(label, "", 0, []))
            continue
        for tid, ver in ids:
            e = by_id.get(f"{switch.base_of(tid):016X}")
            if e is None:                          # an ID the US list doesn't have (another region): by its name
                clean = re.sub(r"\s*\[[^\]]*\]", "", p.stem).strip()
                g = names.get(_norm(clean)) or names.get(_norm(re.sub(r"\s*[\(\[].*$", "", clean)))
                e = names_or_entry(entries, g) if g else None
            if e is None:
                res.unknown.append(Item(label, "", 0, []))
                continue
            k = switch.kind(tid)
            if k == "base":
                res.identified[label] = (e.game, True)
                res.titles[e.title].have.setdefault(e.game, []).append(label)
            else:
                res.extras.setdefault(e.game, []).append((k, ver, f"{tid:016X}", label))
    return res


_ENTRY_BY_GAME: dict[int, dict[str, Entry]] = {}


def entries_for_quick(dats: DatStore, c: Console) -> list[Entry]:
    return dats.entries(c.dat)


def names_or_entry(entries: list[Entry], game: str) -> Entry:
    m = _ENTRY_BY_GAME.get(id(entries))
    if m is None:
        m = _ENTRY_BY_GAME[id(entries)] = {e.game: e for e in entries}
    return m[game]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9()]+", " ", s.lower().replace("&", "and")).strip()


def name_index(entries: list[Entry]) -> dict[str, str]:
    """normalised full name and title -> game (the full name wins: 'metroid prime (europe) (en fr de es it)')."""
    out: dict[str, str] = {}
    for e in entries:
        out.setdefault(_norm(re.sub(r"\s*\(.*$", "", e.game)), e.game)
    for e in entries:
        out[_norm(e.game)] = e.game
    return out


def match_by_name(p: Path, names: dict[str, str]) -> str:
    """A disc file (.cdi, .pbp …) or its game folder named like a Redump game: the full name first, then the title."""
    for cand in (p.stem, p.parent.name):
        n = _norm(cand)
        if n in names:
            return names[n]
        t = _norm(re.sub(r"\s*[\(\[].*$", "", cand))
        if t and t in names:
            return names[t]
    return ""


def merged_track1(p: Path, it: Item, names: dict[str, str], by_game: dict[str, list[Entry]]) -> Entry | None:
    """A disc image named like a Redump game but bigger than its Track 1 (the audio tracks merged in): does the
    start of the file match Track 1?"""
    g = match_by_name(p, names)
    for e in by_game.get(g, []):
        if 0 < e.size < it.size and (_TRACK.search(e.rom) is None or re.search(r"\(Track 0?1\)", e.rom, re.I)):
            c, left = 0, e.size
            with open(p, "rb") as f:
                while left:
                    b = f.read(min(left, 1 << 22))
                    if not b:
                        break
                    c = zlib.crc32(b, c)
                    left -= len(b)
            if not left and f"{c & 0xFFFFFFFF:08x}" == e.crc:
                return e
    return None


def wrong_console(results: dict[str, ConsoleResult], dats: DatStore) -> list[tuple[str, str, str]]:
    """Files in one console's folder that are a known dump of another console: (file, belongs to, game)."""
    out = []
    loaded = {c.key: index(dats.entries(c.dat)) for c in CONSOLES if dats.have(c.dat)}
    names = {c.key: c.name for c in CONSOLES}
    for key, r in results.items():
        for it in r.unknown:
            for other, ix in loaded.items():
                if other == key:
                    continue
                hit = next((ix[c][0] for c in it.crcs if c in ix), None)
                if hit:
                    out.append((it.label, names[other], hit.game))
                    break
    return out
