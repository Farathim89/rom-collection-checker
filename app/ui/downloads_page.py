"""The Downloads page: paste links, they are fetched into the inbox (or a folder you pick) a few at a time, with
progress, pause / resume / retry. When all are done the inbox can be sorted straight away."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QSpinBox, QTableWidget, QVBoxLayout, QWidget)

from ..config import DOWNLOADS, save_settings
from ..core.downloads import Job, Manager, valid_links
from .main_window import Item, open_folder_of, table

COLS = ["File", "Size", "Progress", "Speed", "Status", "Folder"]
STATE_TEXT = {"queued": "⏳ waiting", "running": "⬇ downloading", "paused": "⏸ paused", "done": "✓ done",
              "failed": "✗ failed"}


def size_text(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return ""


class DownloadsPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        s = main.s
        self.mgr = Manager(DOWNLOADS, max(1, s.download_parallel))
        self._was_busy = False
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        head = QLabel("Downloads")
        head.setObjectName("pagetitle")
        v.addWidget(head)
        note = QLabel("Paste one or more links (http / https). They download into the folder below - your inbox, "
                      "so Sort inbox can put them in the right console. Unfinished downloads go on where they stopped, "
                      "also after a restart.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        v.addWidget(note)
        self.links = QPlainTextEdit()
        self.links.setPlaceholderText("https://…   (one per line, or paste a whole block of text)")
        self.links.setFixedHeight(84)
        v.addWidget(self.links)
        row = QHBoxLayout()
        row.addWidget(QLabel("Save to"))
        self.folder = QLineEdit(s.download_folder or s.inbox)
        self.folder.setPlaceholderText("the inbox")
        from .dialogs import folder_row  # noqa: PLC0415
        row.addWidget(folder_row(self.folder, self), 1)
        row.addWidget(QLabel("At once"))
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 8)
        self.parallel.setValue(max(1, s.download_parallel))
        self.parallel.valueChanged.connect(self._parallel_changed)
        row.addWidget(self.parallel)
        add = QPushButton("⬇  Add")
        add.setObjectName("primary")
        add.clicked.connect(self.add)
        row.addWidget(add)
        v.addLayout(row)
        self.t: QTableWidget = table(COLS, {0: 420, 1: 90, 2: 220, 3: 90, 4: 260}, 5)
        self.t.setSortingEnabled(False)
        self.t.itemDoubleClicked.connect(lambda it: self.open_selected())
        v.addWidget(self.t, 1)
        bottom = QHBoxLayout()
        self.sort_after = QCheckBox("Sort the inbox when all downloads are done")
        self.sort_after.setChecked(s.sort_after_downloads)
        self.sort_after.toggled.connect(self._sort_after_changed)
        bottom.addWidget(self.sort_after)
        bottom.addStretch()
        for text, fn in (("⏸  Pause", self.pause), ("▶  Resume / Retry", self.resume), ("📂  Open folder", self.open_selected),
                         ("✖  Remove", self.remove), ("Clear finished", self.clear_done)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bottom.addWidget(b)
        v.addLayout(bottom)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        self.mgr.tick()
        self.refresh()

    # -- settings ---------------------------------------------------------------------------------
    def _parallel_changed(self, n: int) -> None:
        self.main.s.download_parallel = n
        self.mgr.parallel = n
        save_settings(self.main.s)
        self.mgr.tick()

    def _sort_after_changed(self, on: bool) -> None:
        self.main.s.sort_after_downloads = on
        save_settings(self.main.s)

    # -- actions ----------------------------------------------------------------------------------
    def add(self) -> None:
        urls = valid_links(self.links.toPlainText())
        if not urls:
            QMessageBox.information(self, "Downloads", "Paste one or more links that start with http:// or https://.")
            return
        folder = self.folder.text().strip() or self.main.s.inbox
        if not folder:
            QMessageBox.information(self, "Downloads", "Choose a folder to save to (or set your inbox in Settings).")
            return
        if folder != (self.main.s.download_folder or self.main.s.inbox):
            self.main.s.download_folder = "" if folder == self.main.s.inbox else folder
            save_settings(self.main.s)
        n = self.mgr.add(urls, folder)
        self.links.clear()
        self.main.status.setText(f"{n} downloads added" + (f" ({len(urls) - n} already in the list)" if n < len(urls) else ""))
        self.refresh()

    def selected(self) -> list[Job]:
        rows = sorted({i.row() for i in self.t.selectedIndexes()})
        return [self.mgr.jobs[r] for r in rows if r < len(self.mgr.jobs)]

    def pause(self) -> None:
        for j in self.selected():
            self.mgr.pause(j)

    def resume(self) -> None:
        for j in self.selected() or [j for j in self.mgr.jobs if j.state in ("paused", "failed")]:
            self.mgr.resume(j)

    def remove(self) -> None:
        jobs = self.selected()
        if not jobs:
            return
        unfinished = sum(1 for j in jobs if j.state != "done")
        if unfinished and QMessageBox.question(
                self, "Remove", f"Remove {len(jobs)} from the list? {unfinished} unfinished download(s) "
                "and their partly downloaded data are dropped. Finished files stay where they are.") != QMessageBox.Yes:
            return
        for j in jobs:
            self.mgr.remove(j)
        self.refresh()

    def clear_done(self) -> None:
        self.mgr.clear_done()
        self.refresh()

    def open_selected(self) -> None:
        jobs = self.selected()
        if not jobs:
            return
        j = jobs[0]
        if j.path and Path(j.path).exists():
            open_folder_of(j.path)
        elif Path(j.folder).is_dir():
            os.startfile(j.folder)  # noqa: S606 - a folder the user picked

    # -- the table --------------------------------------------------------------------------------
    def counts(self) -> tuple[int, int]:
        """(running, waiting) - shown in the sidebar."""
        return self.mgr.running(), sum(1 for j in self.mgr.jobs if j.state == "queued")

    def refresh(self) -> None:
        self.mgr.tick()
        jobs = self.mgr.jobs
        t = self.t
        if t.rowCount() != len(jobs):
            t.setRowCount(len(jobs))
        for i, j in enumerate(jobs):
            vals = [j.name or j.url, size_text(j.size) if j.size else "", None,
                    f"{size_text(j.speed)}/s" if j.state == "running" and j.speed else "",
                    STATE_TEXT.get(j.state, j.state) + (f" - {j.error}" if j.error else ""), j.folder]
            for c, val in enumerate(vals):
                if val is None:
                    continue
                it = t.item(i, c)
                if it is None:
                    it = Item(val)
                    t.setItem(i, c, it)
                elif it.text() != val:
                    it.setText(val)
                if c == 0:
                    it.setToolTip(j.url)
            bar = t.cellWidget(i, 2)
            if not isinstance(bar, QProgressBar):
                bar = QProgressBar()
                bar.setTextVisible(True)
                t.setCellWidget(i, 2, bar)
            if j.size:
                bar.setRange(0, 1000)
                bar.setValue(int(j.done * 1000 / j.size))
                bar.setFormat(f"{j.done * 100 / j.size:.0f}%")
            elif j.state == "running":
                bar.setRange(0, 0)
            else:
                bar.setRange(0, 1)
                bar.setValue(1 if j.state == "done" else 0)
                bar.setFormat("100%" if j.state == "done" else (size_text(j.done) if j.done else ""))
        running, waiting = self.counts()
        busy = bool(running or waiting)
        if self._was_busy and not busy:
            self.main.fill_menu()
            if self.sort_after.isChecked() and any(j.state == "done" for j in jobs):
                QTimer.singleShot(300, self.main.sort_inbox)
        elif busy != self._was_busy:
            self.main.fill_menu()
        self._was_busy = busy

    def shutdown(self) -> None:
        self.timer.stop()
        self.mgr.stop_all()
