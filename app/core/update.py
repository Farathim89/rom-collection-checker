"""Self-update from the GitHub releases: is there a newer version, download its exe (checked against GitHub's
SHA-256), then swap it in after the app closes and start it again. Only the built exe can do this; the data folder
next to it (settings, lists, logs) is never touched."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO = "Farathim89/rom-collection-checker"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
EXE = "ROMCollectionChecker.exe"
UA = "ROMCollectionChecker-updater"


@dataclass
class Release:
    version: str        # "0.1.2"
    notes: str          # the release text (Markdown)
    url: str            # the exe's download link
    sha256: str         # from GitHub's asset digest ("" when GitHub gives none)
    size: int
    page: str           # the release page


def parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:4])


def newer(latest: str, current: str) -> bool:
    return parse_version(latest) > parse_version(current)


def can_update() -> bool:
    """Only the built exe replaces itself (from source there is nothing to swap)."""
    return bool(getattr(sys, "frozen", False)) and Path(sys.executable).name.lower().endswith(".exe")


def latest(timeout: float = 20) -> Release | None:
    req = urllib.request.Request(API, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - fixed https host
        data = json.loads(r.read().decode("utf-8"))
    asset = next((a for a in data.get("assets") or [] if a.get("name") == EXE), None)
    if not asset:
        return None
    digest = str(asset.get("digest") or "")
    return Release(str(data.get("tag_name", "")).lstrip("v"), data.get("body") or "", asset["browser_download_url"],
                   digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else "", int(asset.get("size") or 0),
                   data.get("html_url") or f"https://github.com/{REPO}/releases")


def download(rel: Release, to: Path, progress=lambda done, total: None, stop=lambda: False) -> Path:
    """The new exe next to the running one as '<exe>.new' (same drive: the swap is a rename)."""
    tmp = to.with_name(to.name + ".part")
    h = hashlib.sha256()
    req = urllib.request.Request(rel.url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:   # noqa: S310
        total = int(r.headers.get("Content-Length") or rel.size or 0)
        done = 0
        while True:
            if stop():
                raise InterruptedError("stopped")
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            progress(done, total)
    if rel.size and done != rel.size:
        raise OSError(f"the download is {done} bytes, the release says {rel.size}")
    if rel.sha256 and h.hexdigest() != rel.sha256:
        raise OSError("the download doesn't match GitHub's checksum - not installed")
    tmp.replace(to)
    return to


def install(new_exe: Path) -> None:
    """Starts a small helper that waits until this app has closed, puts the new exe in place and starts it.
    The caller quits the app right after."""
    exe = Path(sys.executable)
    old = exe.with_name(exe.stem + ".old.exe")
    script = Path(tempfile.gettempdir()) / "romchecker-update.cmd"
    # retries until the running exe is free (a one-file exe also has its launcher process to close)
    script.write_text(
        "@echo off\r\n"
        "set n=0\r\n"
        ":wait\r\n"
        "set /a n+=1\r\n"
        "if %n% gtr 60 goto fail\r\n"
        "timeout /t 1 /nobreak >nul\r\n"
        f'move /y "{exe}" "{old}" >nul 2>&1 || goto wait\r\n'
        f'move /y "{new_exe}" "{exe}" >nul 2>&1 || (move /y "{old}" "{exe}" >nul & goto fail)\r\n'
        f'start "" "{exe}"\r\n'
        "exit /b 0\r\n"
        ":fail\r\n"
        f'start "" "{exe}"\r\n', encoding="utf-8")
    flags = 0x00000008 | 0x00000200 | 0x08000000   # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
    # the new exe must not inherit this one-file exe's own variables, or it looks for this run's (by then
    # deleted) unpack folder: "Failed to load Python DLL ...\_MEIxxxx\python310.dll"
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("_MEI", "_PYI", "PYINSTALLER"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen(["cmd", "/c", str(script)], creationflags=flags, close_fds=True,  # noqa: S603, S607
                     cwd=str(exe.parent), env=env)
