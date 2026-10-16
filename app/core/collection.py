"""What you have: finds each console's folder in your games folder, reads every game file (cached by size and
date, so only new files are read again) and matches them against the console's No-Intro list."""
from __future__ import annotations

import json
import os
import re
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
                 skip: list[str], progress=lambda text: None, stop=lambda: False, extra=None) -> ConsoleResult:
    """extra: an ExtraStore - files No-Intro doesn't list are looked up in TOSEC / hacks / homebrew too."""
    res = ConsoleResult(c, folders, dats.version(c.dat))
    known_ix = extra.index(c.dat) if extra is not None else {}
    entries = dats.entries(c.dat)
    ix = index(entries)
    for e in entries:
        res.titles.setdefault(e.title, TitleInfo(e.title)).games.setdefault(e.game, []).append(e)
    exts = set(c.exts) | set(ARCHIVES)
    files = walk_files(folders, exts, skip)
    res.files = len(files)
    by_game: dict[str, list[str]] = {}
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
        if got is None:
            got = file_items(p, tools, set(c.exts))
            cache.put(p, st, *got)
        items, err = got
        if err:
            res.errors[str(p)] = err
        for it in items:
            res.crcs.update(it.crcs)
            found = [e for crc in it.crcs for e in ix.get(crc, [])]
            if not found:
                res.others.append(it)
                k = next((known_ix[x] for x in it.crcs if x in known_ix), None)
                if k:
                    res.known[it.label] = k
                # a problem: not in a "0 - Homebrew" ... folder (those are on purpose), and not a known good dump
                if not COLLECTION.search(str(p)) and (k is None or k.kind == "bad"):
                    res.unknown.append(it)
                continue
            res.matched[it.label] = found
            for g in dict.fromkeys(e.game for e in found):
                e = next(x for x in found if x.game == g)
                res.titles[e.title].have.setdefault(g, []).append(it.label)
                by_game.setdefault(g, []).append(it.label)
    res.duplicates = {g: fs for g, fs in by_game.items() if len(set(fs)) > 1}
    return res


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
