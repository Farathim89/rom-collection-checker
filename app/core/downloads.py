"""A small download manager: links you paste are fetched into a folder (normally the inbox), a few at a time,
into '<name>.part' files that resume where they stopped (HTTP Range). A finished file never overwrites one that is
there ('Game (2).zip'). The queue is kept in the data folder, so it survives a restart."""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

CHUNK = 256 * 1024
UA = "ROMCollectionChecker"
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass
class Job:
    url: str
    folder: str
    name: str = ""            # file name (from the server, else from the link)
    size: int = 0             # 0 = not known
    done: int = 0             # bytes on disk
    state: str = "queued"     # queued | running | paused | done | failed
    error: str = ""
    path: str = ""            # the finished file
    added: float = field(default_factory=time.time)
    speed: float = field(default=0.0, metadata={"runtime": True})   # bytes / s while running

    @property
    def part(self) -> Path:
        return Path(self.folder) / (self.name + ".part")


def name_from(url: str, disposition: str | None) -> str:
    """'attachment; filename="Game (USA).zip"' or the last part of the link."""
    if disposition:
        m = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", disposition, re.I)
        if m:
            return clean_name(urllib.parse.unquote(m.group(1)))
        m = re.search(r'filename\s*=\s*"?([^";]+)"?', disposition, re.I)
        if m:
            return clean_name(m.group(1))
    path = urllib.parse.urlparse(url).path
    return clean_name(urllib.parse.unquote(path.rsplit("/", 1)[-1])) or "download"


def clean_name(n: str) -> str:
    return _BAD.sub("_", n).strip(" .")[:200]


def free_path(folder: Path, name: str) -> Path:
    p = folder / name
    n = 2
    while p.exists():
        p = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return p


def valid_links(text: str) -> list[str]:
    """Every http(s) link in pasted text (one per line, or mixed with other text)."""
    out = []
    for m in re.finditer(r"https?://[^\s<>\"']+", text):
        u = m.group(0).rstrip(".,;:!?")
        while u.endswith(")") and u.count(")") > u.count("("):   # "(see https://x/y)" - not "Game_(USA)"
            u = u[:-1].rstrip(".,;:!?")
        out.append(u)
    return list(dict.fromkeys(out))


def fetch(job: Job, stop: threading.Event, timeout: float = 30) -> None:
    """Downloads one job (resuming its .part file). Sets job.state to done / paused / failed."""
    job.state, job.error = "running", ""
    folder = Path(job.folder)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        start = job.part.stat().st_size if job.name and job.part.is_file() else 0
        req = urllib.request.Request(job.url, headers={"User-Agent": UA, **({"Range": f"bytes={start}-"} if start else {})})
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310 - links the user pasted, http(s) only
            if not job.name:
                job.name = name_from(r.geturl(), r.headers.get("Content-Disposition"))
                start = job.part.stat().st_size if job.part.is_file() else 0
                if start:            # a .part of the same name from before: only resumable with a new request
                    r.close()
                    return fetch(job, stop, timeout)
            resumed = start and r.status == 206
            if not resumed:
                start = 0
            length = int(r.headers.get("Content-Length") or 0)
            job.size = start + length if length else 0
            job.done = start
            t0, b0 = time.time(), start
            with open(job.part, "ab" if resumed else "wb") as f:
                while True:
                    if stop.is_set():
                        job.state, job.speed = "paused", 0
                        return
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    job.done += len(chunk)
                    dt = time.time() - t0
                    if dt >= 1:
                        job.speed, t0, b0 = (job.done - b0) / dt, time.time(), job.done
        if job.size and job.done < job.size:
            raise OSError(f"the connection ended early ({job.done} of {job.size} bytes) - press Retry to go on")
        final = free_path(folder, job.name)
        job.part.replace(final)
        job.path, job.state, job.speed = str(final), "done", 0
    except urllib.error.HTTPError as e:
        if e.code == 416 and job.part.is_file():      # the .part is already complete
            final = free_path(folder, job.name)
            job.part.replace(final)
            job.path, job.state, job.speed = str(final), "done", 0
            return
        job.state, job.error, job.speed = "failed", f"HTTP {e.code} {e.reason}", 0
    except (OSError, ValueError) as e:
        job.state, job.error, job.speed = "failed", str(e) or type(e).__name__, 0


class Manager:
    """The queue: runs up to `parallel` jobs in threads. The window reads job fields on a timer."""

    def __init__(self, store: Path, parallel: int = 3):
        self.store = store
        self.parallel = parallel
        self.jobs: list[Job] = []
        self._stops: dict[int, threading.Event] = {}
        self._lock = threading.Lock()
        self.load()

    # -- the queue file ------------------------------------------------------------------------------
    def load(self) -> None:
        try:
            data = json.loads(self.store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for d in data:
            d.pop("speed", None)
            j = Job(**d)
            if j.state == "running":
                j.state = "queued"           # the app was closed while it ran: it resumes
            self.jobs.append(j)

    def save(self) -> None:
        self.store.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for j in self.jobs:
            d = asdict(j)
            d.pop("speed", None)
            rows.append(d)
        self.store.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")

    # -- actions --------------------------------------------------------------------------------------
    def add(self, urls: list[str], folder: str) -> int:
        have = {j.url for j in self.jobs if j.state != "done"}
        new = [Job(u, folder) for u in urls if u not in have]
        self.jobs.extend(new)
        self.save()
        self.tick()
        return len(new)

    def pause(self, job: Job) -> None:
        if job.state == "running":
            self._stops[id(job)].set()
        elif job.state == "queued":
            job.state = "paused"
        self.save()

    def resume(self, job: Job) -> None:
        if job.state in ("paused", "failed"):
            job.state, job.error = "queued", ""
        self.save()
        self.tick()

    def remove(self, job: Job, delete_part: bool = True) -> None:
        """Takes a job off the list (a finished file stays; an unfinished .part goes)."""
        if job.state == "running":
            self._stops[id(job)].set()
            t = getattr(job, "_thread", None)
            if t:
                t.join(5)
        if delete_part and job.state != "done" and job.name and job.part.is_file():
            try:
                from .inbox import recycle  # noqa: PLC0415
                recycle(job.part)        # only our own unfinished .part file, to the Recycle Bin
            except OSError:
                pass
        self.jobs.remove(job)
        self.save()

    def clear_done(self) -> None:
        self.jobs = [j for j in self.jobs if j.state != "done"]
        self.save()

    def running(self) -> int:
        return sum(1 for j in self.jobs if j.state == "running")

    def tick(self) -> None:
        """Starts queued jobs while there is room. Called after changes and on the window's timer."""
        with self._lock:
            free = self.parallel - self.running()
            for j in self.jobs:
                if free <= 0:
                    break
                if j.state == "queued":
                    stop = self._stops[id(j)] = threading.Event()
                    j.state = "running"
                    t = threading.Thread(target=self._run, args=(j, stop), daemon=True)
                    j._thread = t           # noqa: SLF001
                    t.start()
                    free -= 1

    def _run(self, job: Job, stop: threading.Event) -> None:
        fetch(job, stop)
        self.save()
        self.tick()

    def stop_all(self) -> None:
        """When the app closes: running jobs stop and go back in the queue (they resume next time)."""
        was = [j for j in self.jobs if j.state == "running"]
        for j in was:
            self._stops[id(j)].set()
        for j in was:
            t = getattr(j, "_thread", None)
            if t:
                t.join(3)
            j.state = "queued"
        self.save()
