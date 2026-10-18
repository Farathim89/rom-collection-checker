"""Missing cue sheets, written so emulators (DuckStation …) and MediaDeck see the disc as one game - nothing is
converted:
  - a CloneCD disc (.ccd + .img + .sub): the .img is the raw disc (2352-byte sectors, like a .bin); the .cue names it
    with the tracks the .ccd lists
  - a disc of "(Track N).bin" files without a cue: track 1 data (its mode from the sector header), the others music;
    a music track that starts with 2 seconds of silence holds its gap (INDEX 00 / 01), else the cue adds it (PREGAP)"""
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


_TRACK = re.compile(r"^(.*?)\s*\(Track (\d+)\)$", re.I)
_GAP = 150 * 2352                        # 2 seconds of raw sectors


def _track_mode(path: Path) -> str:
    with open(path, "rb") as f:
        head = f.read(16)
    if head[:12] == b"\x00" + b"\xff" * 10 + b"\x00":    # a data sector: sync, address, mode
        return f"MODE{head[15]}/2352" if head[15] in (1, 2) else "MODE2/2352"
    return "AUDIO"


def tracks_to_cue(tracks: list[Path]) -> str:
    lines = []
    for i, t in enumerate(tracks, 1):
        lines.append(f'FILE "{t.name}" BINARY')
        mode = _track_mode(t) if i == 1 else "AUDIO"
        lines.append(f"  TRACK {i:02d} {mode}")
        if mode != "AUDIO" or i == 1:
            lines.append("    INDEX 01 00:00:00")
            continue
        with open(t, "rb") as f:
            head = f.read(_GAP)
        lines += (["    INDEX 00 00:00:00", "    INDEX 01 00:02:00"] if len(head) == _GAP and not any(head)
                  else ["    PREGAP 00:02:00", "    INDEX 01 00:00:00"])
    return "\n".join(lines) + "\n"


def ensure_cue(folder: Path) -> list[Path]:
    """Writes the missing .cue for every CloneCD disc and every "(Track N).bin" disc in the folder (never over a
    .cue / .gdi / .m3u that is there); returns the cue sheets made."""
    made = []
    discs: dict[str, list[tuple[int, Path]]] = {}
    for b in folder.glob("*.bin"):
        m = _TRACK.match(b.stem)
        if m:
            discs.setdefault(m.group(1), []).append((int(m.group(2)), b))
    # a disc that a sheet already points at keeps it - per disc: the discs of a game share a folder (Disc 1 / Disc 2)
    named = ""
    for q in folder.iterdir():
        if q.suffix.lower() in (".cue", ".gdi"):
            try:
                named += q.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                pass
    for base, ts in discs.items():
        nos = sorted(n for n, _ in ts)
        cue = folder / f"{base}.cue"
        has_sheet = any(b.name.lower() in named for _, b in ts)
        if not has_sheet and not cue.exists() and len(ts) > 1 and nos == list(range(1, len(ts) + 1)):
            cue.write_text(tracks_to_cue([b for _, b in sorted(ts)]), encoding="utf-8")
            made.append(cue)
    for ccd in sorted(folder.glob("*.ccd")):
        img, cue = ccd.with_suffix(".img"), ccd.with_suffix(".cue")
        if img.is_file() and not cue.exists():
            sheet = ccd_to_cue(ccd, img.name)
            if sheet:
                cue.write_text(sheet, encoding="utf-8")
                made.append(cue)
    return made
