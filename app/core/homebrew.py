"""Free homebrew from Homebrew Hub (hh.gbdev.io): new games, demos and music made by the community for Game Boy,
Game Boy Color, Game Boy Advance and NES, shared by their authors. The list is fetched page by page from its open
API (https://hh3.gbdev.io/api, see gbdev/homebrewhub API.md) and kept in the data folder."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://hh3.gbdev.io/api"
STATIC = "https://hh3.gbdev.io/static/{base}/entries/{slug}/{file}"
SITE = "https://hh.gbdev.io/game/{slug}"
PLATFORMS = {"GB": "gb", "GBC": "gbc", "GBA": "gba", "NES": "nes"}   # Homebrew Hub platform -> console key
UA = "ROMCollectionChecker (Homebrew page)"


def _get(url: str, timeout: float = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - fixed https host
        return json.loads(r.read().decode("utf-8"))


def fetch_all(progress=lambda text: None, stop=lambda: False) -> list[dict]:
    """Every entry (10 per page is the API's limit). Paged in slug order - the only stable one (the API's real
    params are sort=<field>&order=asc, not what API.md says); sorted by title afterwards."""
    out: list[dict] = []
    page, total = 1, 1
    while page <= total and not stop():
        q = urllib.parse.urlencode({"page": page, "page_elements": 10, "sort": "slug", "order": "asc"})
        data = _get(f"{API}/search?{q}")    # (/all in the docs answers 404; search without filters = all)
        total = int(data.get("page_total") or 1)
        out.extend(data.get("entries") or [])
        progress(f"Homebrew Hub list: page {page} of {total} ({len(out)} entries)")
        page += 1
        time.sleep(0.05)         # gentle on a free community server
    seen: set[str] = set()
    out = [e for e in out if not (e.get("slug") in seen or seen.add(e.get("slug")))]
    for e in out:
        e["title"] = str(e.get("title") or e.get("slug")).strip()
    return sorted(out, key=lambda e: e["title"].lower())


class HomebrewStore:
    def __init__(self, path: Path):
        self.path = path
        self.entries: list[dict] = []
        self.updated = 0.0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.entries, self.updated = data["entries"], float(data["updated"])
        except (OSError, ValueError, KeyError):
            pass

    def save(self, entries: list[dict]) -> None:
        self.entries, self.updated = entries, time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".part")
        tmp.write_text(json.dumps({"updated": self.updated, "entries": entries}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


BASES = {"database-gb": "gb", "database-gba": "gba", "database-nes": "nes"}   # entries without a platform


def console_of(e: dict) -> str:
    return PLATFORMS.get(str(e.get("platform") or "").upper()) or BASES.get(e.get("basepath") or "", "")


def developers(e: dict) -> str:
    d = e.get("developer") or ""
    items = d if isinstance(d, list) else [d]
    return ", ".join(str(x.get("name", "")) if isinstance(x, dict) else str(x) for x in items if x)


def file_url(e: dict, filename: str) -> str:
    return STATIC.format(base=urllib.parse.quote(e.get("basepath") or "database-gb"),
                         slug=urllib.parse.quote(e["slug"]), file=urllib.parse.quote(filename))


def rom_files(e: dict) -> list[str]:
    """The playable files, the default one first."""
    fs = [f for f in e.get("files") or [] if f.get("filename")]
    fs.sort(key=lambda f: (not f.get("default"), not f.get("playable", True)))
    return [f["filename"] for f in fs]


def cover_url(e: dict) -> str:
    shots = e.get("screenshots") or []
    return file_url(e, shots[0]) if shots else ""


def search(entries: list[dict], text: str = "", console: str = "", kind: str = "") -> list[dict]:
    t = text.lower().strip()
    out = []
    for e in entries:
        if console and console_of(e) != console:
            continue
        if kind and e.get("typetag") != kind:
            continue
        if t and t not in " ".join([e.get("title", ""), developers(e), " ".join(e.get("tags") or [])]).lower():
            continue
        out.append(e)
    return out
