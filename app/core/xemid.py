"""Xbox 360: which release a disc is, when its title ID fits several (Forza Horizon's MS-2505 is the Europe,
Europe / Asia and USA releases). The game's default.xex carries its release number - the SKU digits of the disc's
XeMID that redump.org lists on each disc page (MS250507W0CF11: 07 = Europe). Asked once per title, cached;
only when that is switched on in Settings (it sends the game's title to redump.org)."""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ONLINE = [False]                   # Settings → Tools & lists: ask redump.org
CACHE: list[Path | None] = [None]  # RomChecker-data/xemid.json: title -> [[regions, xemid], …]
REDUMP = "http://redump.org"       # (http only)


def _get(url: str, timeout: float = 20) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ROMCollectionChecker"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - fixed host
        return r.read().decode("utf-8", "replace")


def _disc(html: str) -> tuple[list[str], str] | None:
    """(regions, XeMID) of an Xbox 360 disc page, or None for another system."""
    if "Microsoft Xbox 360" not in html:
        return None
    reg = re.search(r"Region</th><td>(.*?)</td>", html, re.S)
    regions = [r.strip() for t in re.findall(r'title="([^"]+)"', reg.group(1)) for r in t.split(",")] if reg else []
    xemid = re.search(r"XeMID(?:</b>)?:\s*([A-Z0-9]{2}\d{4}\d{2}[A-Z0-9]+)", html)
    return (regions, xemid.group(1)) if xemid else None


def releases(title: str, serial: str) -> list[tuple[list[str], str]]:
    """[(regions, XeMID)] of a title's Xbox 360 discs on redump.org with this title ID (MS2505), cached."""
    cache = {}
    if CACHE[0] and CACHE[0].is_file():
        try:
            cache = json.loads(CACHE[0].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
    if serial in cache:
        return [(r, x) for r, x in cache[serial]]
    if not ONLINE[0]:
        return []
    out = []
    try:
        words = "-".join(re.findall(r"[A-Za-z0-9']+", title))       # redump joins the words with dashes
        page = _get(f"{REDUMP}/discs/quicksearch/{urllib.parse.quote(words)}/")
        ids = list(dict.fromkeys(re.findall(r'<a href="/disc/(\d+)/">', page)))
        one = _disc(page)                          # one result: redump shows the disc page itself
        if one and one[1].startswith(serial):
            out.append(one)
        for i in ids[:12]:
            time.sleep(0.5)                        # gently
            d = _disc(_get(f"{REDUMP}/disc/{i}/"))
            if d and d[1].startswith(serial):
                out.append(d)
    except OSError:
        return []                                  # offline: try again next time
    cache[serial] = [[r, x] for r, x in out]
    if CACHE[0]:
        CACHE[0].write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def pick(games: list[str], title: str, serial: str, sku: int) -> list[str]:
    """The candidates whose release on redump.org has this SKU (all of them when that can't be told)."""
    from .dat import regions_of   # noqa: PLC0415
    rel = [set(r) for r, x in releases(title, serial) if len(x) >= 8 and int(x[6:8]) == sku]
    keep = [g for g in games if set(regions_of(g)) in rel]
    return keep or games
