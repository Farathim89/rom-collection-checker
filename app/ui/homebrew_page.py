"""The Homebrew page: free games, demos and music from Homebrew Hub for Game Boy / Color / Advance and NES - browse
with screenshots, and download them straight into the right console folder (through the Downloads queue)."""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtNetwork import QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QSplitter, QVBoxLayout, QWidget)

from ..config import STATE
from ..core import homebrew as hb
from ..core.consoles import BY_KEY
from . import theme
from .main_window import Item, table

COLS = ["Title", "Console", "Type", "Developer", "License", "Tags", "In your folder"]
KINDS = {"Games": "game", "Demos": "demo", "Music": "music", "Everything": ""}


class HomebrewPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.store = hb.HomebrewStore(STATE / "homebrew.json")
        self._mine: dict[str, set[str]] | None = None     # console -> lower-case file names in your folders
        self._rows: list[dict] = []
        self._reply: QNetworkReply | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        head = QLabel("Homebrew")
        head.setObjectName("pagetitle")
        v.addWidget(head)
        self.note = QLabel()
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        self.note.setOpenExternalLinks(True)
        v.addWidget(self.note)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search title, developer, tag…")
        self.search.textChanged.connect(lambda *_: self.fill())
        row.addWidget(self.search, 1)
        self.console = QComboBox()
        self.console.addItem("All consoles", "")
        for key in hb.PLATFORMS.values():
            self.console.addItem(BY_KEY[key].name, key)
        self.kind = QComboBox()
        self.kind.addItems(list(KINDS))
        self.only_new = QCheckBox("Only ones not in my folders")
        for wd in (self.console, self.kind):
            wd.currentIndexChanged.connect(lambda *_: self.fill())
        self.only_new.toggled.connect(lambda *_: self.fill())
        for wd in (QLabel("Console"), self.console, QLabel("Show"), self.kind, self.only_new):
            row.addWidget(wd)
        self.b_update = QPushButton("🔄  Get the list")
        self.b_update.setObjectName("primary")
        self.b_update.clicked.connect(self.update_list)
        row.addWidget(self.b_update)
        v.addLayout(row)
        split = QSplitter(Qt.Horizontal)
        self.t = table(COLS, {0: 300, 1: 150, 2: 70, 3: 200, 4: 130, 5: 220}, 6)
        self.t.itemSelectionChanged.connect(self.show_entry)
        self.t.itemDoubleClicked.connect(lambda *_: self.download_selected())
        split.addWidget(self.t)
        side = QWidget()
        sv = QVBoxLayout(side)
        sv.setContentsMargins(10, 0, 0, 0)
        self.title = QLabel()
        self.title.setObjectName("pagetitle")
        self.title.setWordWrap(True)
        self.shot = QLabel()
        self.shot.setAlignment(Qt.AlignCenter)
        self.shot.setMinimumSize(320, 288)
        self.shot.setObjectName("cover")
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.RichText)
        self.info.setOpenExternalLinks(True)
        self.info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.b_get = QPushButton("⬇  Download")
        self.b_get.setObjectName("primary")
        self.b_get.clicked.connect(self.download_selected)
        for wd in (self.title, self.shot, self.info):
            sv.addWidget(wd)
        sv.addStretch(1)
        sv.addWidget(self.b_get)
        split.addWidget(side)
        split.setSizes([1000, 460])
        v.addWidget(split, 1)
        self.fill()

    # -- the list -----------------------------------------------------------------------------------
    def update_list(self) -> None:
        def job(w):
            return hb.fetch_all(w.signals.progress.emit, lambda: w.stop)

        def done(entries) -> None:
            if entries:
                self.store.save(entries)
            self._mine = None
            self.fill()
            self.main.status.setText(f"Homebrew Hub: {len(self.store.entries)} entries")
        self.main.run(job, done, "Getting the Homebrew Hub list…")

    def mine(self) -> dict[str, set[str]]:
        """File names already in your console folders (to tick what you have)."""
        if self._mine is None:
            found = self.main.folders()
            self._mine = {}
            for key in hb.PLATFORMS.values():
                names: set[str] = set()
                for f in found.get(key, []):
                    for _dir, _sub, files in os.walk(f):
                        names.update(n.lower() for n in files)
                self._mine[key] = names
        return self._mine

    def have(self, e: dict) -> bool:
        names = self.mine().get(hb.console_of(e), set())
        return any(Path(f).name.lower() in names for f in hb.rom_files(e))

    def fill(self) -> None:
        s = self.store
        when = time.strftime("%Y-%m-%d", time.localtime(s.updated)) if s.updated else ""
        self.note.setText(
            "Free games, demos and music made by the community for Game Boy, Game Boy Color, Game Boy Advance and NES, "
            "shared by their authors on <a href='https://hh.gbdev.io'>Homebrew Hub</a>. Each one's license is shown - "
            "most are free to download and play. Download puts the ROM straight into the console's folder."
            + (f" List from {when}, {len(s.entries)} entries." if s.entries else
               " Press <b>Get the list</b> to fetch it (about 1,600 entries, a few MB, from hh3.gbdev.io)."))
        self.b_update.setText("🔄  Update the list" if s.entries else "🔄  Get the list")
        self.b_update.setObjectName("" if s.entries else "primary")
        self.b_update.style().unpolish(self.b_update)
        self.b_update.style().polish(self.b_update)
        rows = hb.search(s.entries, self.search.text(), self.console.currentData() or "",
                         KINDS[self.kind.currentText()])
        if self.only_new.isChecked():
            rows = [e for e in rows if not self.have(e)]
        self._rows = rows
        t = self.t
        p = theme.current()
        t.setSortingEnabled(False)
        t.setRowCount(len(rows))
        for i, e in enumerate(rows):
            key = hb.console_of(e)
            got = self.have(e) if s.entries else False
            vals = [e.get("title", e["slug"]), BY_KEY[key].name if key else e.get("platform", ""),
                    e.get("typetag", ""), hb.developers(e), e.get("license", "") or "", ", ".join(e.get("tags") or []),
                    "✓ yes" if got else ""]
            for c, val in enumerate(vals):
                it = Item(str(val))
                if c == 0:
                    it.setData(Qt.UserRole, i)
                if c == 6 and got:
                    it.setForeground(QColor(p.ready))
                t.setItem(i, c, it)
        t.setSortingEnabled(True)
        self.b_get.setEnabled(bool(rows))
        if rows and not t.selectedIndexes():
            t.selectRow(0)

    # -- one entry ----------------------------------------------------------------------------------
    def selected(self) -> list[dict]:
        rows = sorted({i.row() for i in self.t.selectedIndexes()})
        out = []
        for r in rows:
            it = self.t.item(r, 0)
            if it is not None and it.data(Qt.UserRole) is not None and it.data(Qt.UserRole) < len(self._rows):
                out.append(self._rows[it.data(Qt.UserRole)])
        return out

    def show_entry(self) -> None:
        sel = self.selected()
        if not sel:
            return
        e = sel[0]
        self.title.setText(e.get("title", ""))
        links = [f"<a href='{hb.SITE.format(slug=e['slug'])}'>Homebrew Hub</a>"]
        for k in ("gameWebsite", "website", "devWebsite", "repository"):
            if e.get(k):
                links.append(f"<a href='{e[k]}'>{'source code' if k == 'repository' else 'website'}</a>")
        desc = (e.get("description") or "").replace("<", "&lt;")
        if len(desc) > 600:
            desc = desc[:600].rsplit(" ", 1)[0] + "…"
        key = hb.console_of(e)
        self.info.setText(
            f"<b>{hb.developers(e)}</b><br>{BY_KEY[key].name if key else e.get('platform', '')} · {e.get('typetag', '')}"
            + (f" · {e['date']}" if e.get("date") else "")
            + f"<br>License: {e.get('license') or 'not given'}<br><br>{desc}<br><br>" + " · ".join(links))
        n = len(sel)
        self.b_get.setText(f"⬇  Download {n} to their console folders" if n > 1 else
                           f"⬇  Download to {Path(self.target(key)).name if key else 'the inbox'}")
        self.load_shot(hb.cover_url(e))

    def load_shot(self, url: str) -> None:
        self.shot.setPixmap(QPixmap())
        self.shot.setText("…" if url else "no screenshot")
        if self._reply is not None:
            self._reply.abort()
        if not url:
            return
        reply = self._reply = self.main.net.get(QNetworkRequest(QUrl(url)))

        def done() -> None:
            reply.deleteLater()
            if reply is not self._reply:
                return
            self._reply = None
            pix = QPixmap()
            if reply.error() == QNetworkReply.NoError and pix.loadFromData(reply.readAll()):
                big = pix.scaled(self.shot.width(), self.shot.height(), Qt.KeepAspectRatio,
                                 Qt.FastTransformation if pix.width() <= 240 else Qt.SmoothTransformation)
                self.shot.setPixmap(big)     # pixel art stays sharp
            else:
                self.shot.setText("no screenshot")
        reply.finished.connect(done)

    # -- download -----------------------------------------------------------------------------------
    def target(self, key: str) -> str:
        """The console's folder in your collection, else <games folder>\\<maker>\\<console>."""
        found = self.main.folders().get(key)
        if found:
            return found[0]
        c = BY_KEY[key]
        root = self.main.s.games_root or self.main.s.inbox
        return str(Path(root) / c.maker / c.folders[0]) if root else ""

    def download_selected(self) -> None:
        sel = self.selected()
        if not sel:
            return
        by_folder: dict[str, list[str]] = {}
        skipped = 0
        for e in sel:
            files = hb.rom_files(e)
            key = hb.console_of(e)
            folder = self.target(key) if key else (self.main.s.inbox or "")
            if not files or not folder:
                skipped += 1
                continue
            by_folder.setdefault(folder, []).append(hb.file_url(e, files[0]))
        if not by_folder:
            QMessageBox.information(self, "Homebrew", "Nothing to download (no ROM file, or no games folder set).")
            return
        mgr = self.main.downloads.mgr
        n = sum(mgr.add(urls, folder) for folder, urls in by_folder.items())
        self._mine = None
        self.main.status.setText(f"{n} homebrew downloads added - see Downloads" +
                                 (f" ({skipped} without a ROM file)" if skipped else ""))
        self.main.fill_menu()

    def open_site(self) -> None:
        sel = self.selected()
        if sel:
            QDesktopServices.openUrl(QUrl(hb.SITE.format(slug=sel[0]["slug"])))
