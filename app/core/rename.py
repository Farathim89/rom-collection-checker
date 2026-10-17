"""Clean names: a file that is a known No-Intro dump gets that dump's official name
("smb.nes" -> "Super Mario Bros. (World).nes"); archives keep their type ("…(World).zip"). A disc named by its
header gets its Redump name too when only one release fits (a game in a folder of its own: the folder as well).
Shown as a plan first; applying writes an undo log. Files that match several games, or whose new name is taken,
stay."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .collection import ConsoleResult
from .inbox import SAFE, write_undo


@dataclass
class Rename:
    path: str
    new: str
    game: str
    enabled: bool = True
    folder: str = ""     # the game's own folder (Wii: Game/Game.wbfs): renamed after the file


def _words(name: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", name.lower().replace("&", "and")))


# a disc's tracks / cue sheet: the .cue names its tracks, so they keep their names
_LINKED = {".bin", ".cue", ".gdi", ".raw", ".img", ".sub", ".ccd", ".mds", ".mdf", ".sfo"}


def make_renames(res: ConsoleResult) -> list[Rename]:
    out: list[Rename] = []
    by_path: dict[str, set[str]] = {}
    for label, entries in res.matched.items():
        path = label.split(" › ")[0]
        by_path.setdefault(path, set()).update(e.game for e in entries)
    # files No-Intro doesn't list, but TOSEC / the hacks list do (not bad dumps): their name there
    for it in res.others:
        k = res.known.get(it.label)
        if k and k.kind != "bad" and it.path not in by_path:
            by_path.setdefault(it.path, set()).add(k.name)
    # named by its header, and only one release fits - not Switch (its file names hold the title ID), and not a file
    # whose name says more than the game's (a mod: "Mario Party 8 GC Controller (Widescreen)")
    for label, pg in res.identified.items():
        path = label.split(" › ")[0]
        if pg[1] and path not in by_path and " › " not in label and Path(path).suffix.lower() not in _LINKED                 and res.console.source != "titledb" and _words(Path(path).stem) <= _words(pg[0]):
            by_path.setdefault(path, set()).add(pg[0])
    taken = set()
    for path, games in by_path.items():
        if len(games) != 1:
            continue        # a multi-game archive or an ambiguous dump keeps its name
        game = next(iter(games))
        p = Path(path)
        new = p.with_name(SAFE.sub("_", game) + p.suffix.lower())
        if new.name == p.name:
            continue
        if (new.exists() and new.resolve() != p.resolve()) or str(new).lower() in taken:
            continue
        taken.add(str(new).lower())
        own = p.parent.name == p.stem and len(list(p.parent.iterdir())) <= 3     # Game/Game.wbfs (+ a cover or two)
        out.append(Rename(str(p), str(new), game, folder=str(p.parent) if own else ""))
    return sorted(out, key=lambda r: r.path.lower())


def apply_renames(renames: list[Rename], undo_dir: Path) -> tuple[int, list[str], Path]:
    done, problems = [], []
    for r in renames:
        if not r.enabled:
            continue
        src, dst = Path(r.path), Path(r.new)
        try:
            if dst.exists() and dst.resolve() != src.resolve():
                problems.append(f"name taken: {dst.name}")
                continue
            src.rename(dst)
            done.append({"op": "renamed", "from": str(src), "to": str(dst)})
            if r.folder:                             # its own folder takes the new name too
                old = Path(r.folder)
                new = old.with_name(dst.stem)
                if not new.exists():
                    old.rename(new)
                    done.append({"op": "renamed", "from": str(old), "to": str(new)})
        except OSError as e:
            problems.append(f"{src.name}: {e}")
    return len(done), problems, write_undo(undo_dir, "rename", done)
