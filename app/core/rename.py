"""Clean names: a file that is a known No-Intro dump gets that dump's official name
("smb.nes" -> "Super Mario Bros. (World).nes"); archives keep their type ("…(World).zip"). Shown as a plan
first; applying writes an undo log. Files that match several games, or whose new name is taken, stay."""
from __future__ import annotations

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
        out.append(Rename(str(p), str(new), game))
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
        except OSError as e:
            problems.append(f"{src.name}: {e}")
    return len(done), problems, write_undo(undo_dir, "rename", done)
