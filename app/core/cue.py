"""Cue sheets for CloneCD discs (.ccd + .img + .sub): emulators like DuckStation open a .cue, not a .ccd. The .img is
the raw disc (2352-byte sectors, like a .bin), so a cue that names it - with the tracks the .ccd lists - is all it
takes; nothing is converted."""
from __future__ import annotations

import re
from pathlib import Path


def _msf(lba: int) -> str:
    return f"{lba // 4500:02d}:{lba // 75 % 60:02d}:{lba % 75:02d}"


def ccd_to_cue(ccd: Path, img_name: str) -> str | None:
    """The cue sheet for a .ccd's tracks ([TRACK n] MODE=0 audio / 1 / 2, INDEX 0 / 1 = sector), or None."""
    try:
        text = ccd.read_text(encoding="latin1")
    except OSError:
        return None
    tracks = []
    for m in re.finditer(r"\[TRACK (\d+)\]([^\[]*)", text, re.I):
        body = m.group(2)
        mode = re.search(r"MODE\s*=\s*(\d)", body)
        idx = {int(a): int(b) for a, b in re.findall(r"INDEX\s*(\d+)\s*=\s*(\d+)", body)}
        if mode and 1 in idx:
            tracks.append((int(m.group(1)), int(mode.group(1)), idx))
    if not tracks:
        return None
    lines = [f'FILE "{img_name}" BINARY']
    for no, mode, idx in sorted(tracks):
        lines.append(f"  TRACK {no:02d} " + {0: "AUDIO", 1: "MODE1/2352"}.get(mode, "MODE2/2352"))
        if 0 in idx and idx[0] != idx[1]:
            lines.append(f"    INDEX 00 {_msf(idx[0])}")
        lines.append(f"    INDEX 01 {_msf(idx[1])}")
    return "\n".join(lines) + "\n"


def ensure_cue(folder: Path) -> list[Path]:
    """Writes the missing .cue next to every .ccd + .img in the folder; returns the cue sheets made."""
    made = []
    for ccd in sorted(folder.glob("*.ccd")):
        img, cue = ccd.with_suffix(".img"), ccd.with_suffix(".cue")
        if img.is_file() and not cue.exists():
            sheet = ccd_to_cue(ccd, img.name)
            if sheet:
                cue.write_text(sheet, encoding="utf-8")
                made.append(cue)
    return made
