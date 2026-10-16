"""The window: your consoles on the left (have / total), the selected console's games in the middle, the selected
game's versions and box art on the right. Overview and Problems are pages of their own."""
from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QMenu, QProgressBar, QPushButton, QSplitter, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from .. import __version__
from ..config import CACHE, DATS, EXTRA, Settings, load_settings, save_settings
from ..core.collection import ConsoleResult, ScanCache, find_folders, scan_console, wrong_console
from ..core.consoles import BY_KEY, CONSOLES
from ..core.dat import DatStore
from ..core.extra import ExtraStore
from ..core.hashing import Tools
from ..portable import ui_settings
from . import theme

KIND_SETS = {"Official games": {"game"},
             "+ unlicensed": {"game", "unlicensed"},
             "Everything (protos, demos, homebrew …)": {"game", "unlicensed", "prototype", "demo", "homebrew",
                                                        "pirate", "program", "rerelease"}}
REGIONS = ["USA", "Europe", "Japan", "Other"]   # Other: Korea, Brazil, Asia, China ... (World counts as USA + Europe)
GAME_COLS = ["Title", "Regions", "Status", "Versions", "Your file"]
OVERVIEW_COLS = ["Console", "Have", "Total", "%", "Missing", "Files", "Not in No-Intro", "Duplicates", "List"]
THUMBS = "https://thumbnails.libretro.com/{}/Named_Boxarts/{}.png"


class _Signals(QObject):
    progress = Signal(str)
    done = Signal(object)
    failed = Signal(str)


class Worker(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn, self.signals, self.stop = fn, _Signals(), False

    def run(self) -> None:
        try:
            self.signals.done.emit(self.fn(self))
        except Exception as e:  # noqa: BLE001 - shown to the user
            self.signals.failed.emit(f"{type(e).__name__}: {e}")


class Item(QTableWidgetItem):
    """Sorts numbers as numbers; a cell with a sort value (UserRole + 1) by that."""

    def __lt__(self, other) -> bool:  # noqa: D105
        a, b = self.data(Qt.UserRole + 1), other.data(Qt.UserRole + 1)
        if a is not None and b is not None:
            return a < b
        try:
            return float(self.text().rstrip("%") or -1) < float(other.text().rstrip("%") or -1)
        except ValueError:
            return self.text().lower() < other.text().lower()


def make_copyable(table: QTableWidget) -> None:
    from PySide6.QtGui import QAction, QKeySequence  # noqa: PLC0415
    from PySide6.QtWidgets import QMenu  # noqa: PLC0415

    def rows_text() -> str:
        rows = sorted({i.row() for i in table.selectedIndexes()})
        return "\n".join("\t".join(table.item(r, c).text() if table.item(r, c) else ""
                                   for c in range(table.columnCount())) for r in rows)
    act = QAction("Copy", table)
    act.setShortcut(QKeySequence.Copy)
    act.setShortcutContext(Qt.WidgetShortcut)
    act.triggered.connect(lambda: QGuiApplication.clipboard().setText(rows_text()))
    table.addAction(act)

    def menu(pos) -> None:
        m = QMenu(table)
        item = table.itemAt(pos)
        if item is not None:
            m.addAction("Copy", lambda: QGuiApplication.clipboard().setText(item.text()))
        m.addAction("Copy rows", lambda: QGuiApplication.clipboard().setText(rows_text()))
        m.exec(table.viewport().mapToGlobal(pos))
    table.setContextMenuPolicy(Qt.CustomContextMenu)
    table.customContextMenuRequested.connect(menu)


def table(cols: list[str], widths: dict[int, int], stretch: int | None = None) -> QTableWidget:
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)   # rows stay in the order they are filled
    t.setSortingEnabled(True)
    t.verticalHeader().setVisible(False)
    t.setWordWrap(False)
    head = t.horizontalHeader()
    head.setSectionResizeMode(QHeaderView.Interactive)
    if stretch is not None:
        head.setSectionResizeMode(stretch, QHeaderView.Stretch)
    for c, w in widths.items():
        t.setColumnWidth(c, w)
    make_copyable(t)
    return t


def open_folder_of(path: str) -> None:
    p = Path(path.split(" › ")[0])
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p.parent if p.is_file() or not p.exists() else p)))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.s: Settings = load_settings()
        self.dats = DatStore(DATS)
        self.extra = ExtraStore(EXTRA)
        self.cache = ScanCache(CACHE)
        self.results: dict[str, ConsoleResult] = {}
        self.wrong: list[tuple[str, str, str]] = []
        self.worker: Worker | None = None
        self.net = QNetworkAccessManager(self)
        self._cover_reply: QNetworkReply | None = None
        self.setWindowTitle(f"ROM Collection Checker v{__version__}")
        self.resize(1500, 880)
        root = QWidget()
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._sidebar())
        main = QWidget()
        v = QVBoxLayout(main)
        v.setContentsMargins(12, 10, 12, 8)
        v.addLayout(self._filters())
        self.pages = QStackedWidget()
        self.pages.addWidget(self._overview_page())
        self.pages.addWidget(self._console_page())
        self.pages.addWidget(self._problems_page())
        from .downloads_page import DownloadsPage  # noqa: PLC0415 - it uses this module's helpers
        self.downloads = DownloadsPage(self)
        self.pages.addWidget(self.downloads)
        from .homebrew_page import HomebrewPage  # noqa: PLC0415
        self.homebrew = HomebrewPage(self)
        self.pages.addWidget(self.homebrew)
        v.addWidget(self.pages, 1)
        h.addWidget(main, 1)
        self.setCentralWidget(root)
        self.status = QLabel()
        self.bar = QProgressBar()
        self.bar.setMaximumWidth(260)
        self.bar.setRange(0, 0)
        self.bar.setVisible(False)
        self.b_stop = QPushButton("Stop")
        self.b_stop.setVisible(False)
        self.b_stop.clicked.connect(lambda: self.worker and setattr(self.worker, "stop", True))
        self.statusBar().addWidget(self.status, 1)
        self.statusBar().addPermanentWidget(self.bar)
        self.statusBar().addPermanentWidget(self.b_stop)
        geo = ui_settings().value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        self.fill_menu()
        if not self.s.games_root:
            QTimer.singleShot(0, self.open_settings)
        else:
            QTimer.singleShot(0, self.scan)
        QTimer.singleShot(8000, lambda: self.check_updates(manual=False))

    def closeEvent(self, e) -> None:  # noqa: N802
        ui_settings().setValue("geometry", self.saveGeometry())
        if self.worker is not None:      # a running scan stops at its next file, so the app can close
            self.worker.stop = True
        self.downloads.shutdown()
        self.cache.save()
        super().closeEvent(e)

    # -- layout ------------------------------------------------------------------------------------------
    def _sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(290)
        outer = QVBoxLayout(side)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        brand = QWidget()
        brand.setObjectName("brand")
        b = QVBoxLayout(brand)
        b.setContentsMargins(16, 10, 16, 8)
        b.setSpacing(0)
        title = QLabel("ROM COLLECTION")
        title.setObjectName("brandtitle")
        sub = QLabel(f"have · missing · tidy  ·  v{__version__}")
        sub.setObjectName("brandsub")
        b.addWidget(title)
        b.addWidget(sub)
        outer.addWidget(brand)
        body = QWidget()
        body.setObjectName("sidebody")
        outer.addWidget(body, 1)
        v = QVBoxLayout(body)
        v.setContentsMargins(10, 10, 10, 10)
        self.menu = QListWidget()
        self.menu.setObjectName("sidemenu")
        self.menu.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.menu.setTextElideMode(Qt.ElideMiddle)
        self.menu.currentItemChanged.connect(lambda *_: self.show_page())
        v.addWidget(self.menu, 1)
        for text, fn, primary, tip in (
                ("🔄  Scan collection", self.scan, True, "Read your console folders (only new or changed files)"),
                ("📥  Sort inbox…", self.sort_inbox, False, "Move new games from a folder into the right console"),
                ("✏  Clean names…", self.clean_names, False, "Rename the shown console's games to their No-Intro names"),
                ("↩  Undo…", self.undo_dialog, False, "Undo a sort or a rename"),
                ("📋  Copy missing list", self.copy_missing, False, "The missing games shown, to the clipboard"),
                ("💾  Save missing list…", self.save_missing, False, "The missing games shown, as a text file"),
                ("⚙  Settings", self.open_settings, False, "")):
            btn = QPushButton(text)
            btn.setObjectName("primary" if primary else "sideaction")
            btn.setToolTip(tip)
            btn.clicked.connect(lambda _=False, f=fn: f())
            v.addWidget(btn)
        self.totals = QLabel()
        self.totals.setObjectName("muted")
        self.totals.setWordWrap(True)
        v.addWidget(self.totals)
        return side

    def _filters(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search games…")
        self.search.textChanged.connect(lambda *_: self.fill_games())
        row.addWidget(self.search, 1)
        self.show_box = QComboBox()
        self.show_box.addItems(["All", "Missing", "Have"])
        self.show_box.setCurrentText(ui_settings().value("show", "Missing"))
        # regions: tick the ones you want; games only released in the others are hidden and not counted
        self.region_btn = QToolButton()
        self.region_btn.setObjectName("regionbtn")
        self.region_btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.region_btn)
        self.region_acts: dict[str, QAction] = {}
        for r in REGIONS:
            a = QAction(r, menu, checkable=True)
            a.setChecked(r in self.s.regions)
            a.toggled.connect(lambda *_: self._regions_changed())
            menu.addAction(a)
            self.region_acts[r] = a
        menu.addSeparator()
        menu.addAction("All regions", lambda: [a.setChecked(True) for a in self.region_acts.values()])
        self.region_btn.setMenu(menu)
        self.region_btn.setText(self.scope_text())
        self.kind_box = QComboBox()
        self.kind_box.addItems(list(KIND_SETS))
        cur = next((k for k, v in KIND_SETS.items() if v == set(self.s.count_kinds)), "Official games")
        self.kind_box.setCurrentText(cur)
        self.filter_widgets: list[QWidget] = []          # hidden on the Downloads page
        for label, box in (("Show", self.show_box), ("Regions", self.region_btn), ("Count", self.kind_box)):
            lab = QLabel(label)
            if box is self.show_box:
                self.show_label = lab
            else:
                self.filter_widgets += [lab, box]
            row.addWidget(lab)
            row.addWidget(box)
            if isinstance(box, QComboBox):
                box.currentTextChanged.connect(lambda *_: self._filters_changed())
        return row

    def _regions_changed(self) -> None:
        if not any(a.isChecked() for a in self.region_acts.values()):   # at least one region stays
            self.region_acts["USA"].setChecked(True)
            return
        self.s.regions = [r for r, a in self.region_acts.items() if a.isChecked()]
        self.region_btn.setText(self.scope_text())
        self._filters_changed()

    def _filters_changed(self) -> None:
        self.s.regions = [r for r, a in self.region_acts.items() if a.isChecked()]
        self.s.count_kinds = sorted(KIND_SETS[self.kind_box.currentText()])
        ui_settings().setValue("show", self.show_box.currentText())
        save_settings(self.s)
        self.fill_menu()
        self.fill_overview()
        self.fill_games()

    def _overview_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        head = QLabel("Overview")
        head.setObjectName("pagetitle")
        v.addWidget(head)
        self.overview_note = QLabel()
        self.overview_note.setObjectName("muted")
        self.overview_note.setWordWrap(True)
        v.addWidget(self.overview_note)
        self.overview = table(OVERVIEW_COLS, {0: 260, 1: 70, 2: 70, 3: 60, 4: 80, 5: 70, 6: 120, 7: 90}, 8)
        self.overview.itemDoubleClicked.connect(lambda it: self.select_console(
            self.overview.item(it.row(), 0).data(Qt.UserRole)))
        v.addWidget(self.overview, 1)
        return w

    def _console_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        self.console_title = QLabel()
        self.console_title.setObjectName("pagetitle")
        v.addWidget(self.console_title)
        split = QSplitter()
        self.games = table(GAME_COLS, {0: 380, 1: 150, 2: 100, 3: 80}, 4)
        self.games.itemSelectionChanged.connect(self.show_game)
        self.games.itemDoubleClicked.connect(lambda it: (f := self.games.item(it.row(), 4).data(Qt.UserRole))
                                             and open_folder_of(f))
        split.addWidget(self.games)
        split.addWidget(self._details())
        split.setSizes([1050, 420])
        v.addWidget(split, 1)
        return w

    def _details(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 0, 0, 0)
        self.heading = QLabel("Select a game")
        self.heading.setObjectName("heading")
        self.heading.setWordWrap(True)
        v.addWidget(self.heading)
        self.cover = QLabel()
        self.cover.setObjectName("cover")
        self.cover.setFixedSize(380, 280)
        self.cover.setAlignment(Qt.AlignCenter)
        v.addWidget(self.cover)
        v.addWidget(QLabel("Versions (No-Intro)"))
        self.versions = QListWidget()
        self.versions.itemDoubleClicked.connect(lambda it: (f := it.data(Qt.UserRole)) and open_folder_of(f))
        v.addWidget(self.versions, 1)
        return w

    def _problems_page(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        head = QLabel("Problems")
        head.setObjectName("pagetitle")
        v.addWidget(head)
        note = QLabel("Double-click a line to open its folder. Nothing here is changed by the checker.")
        note.setObjectName("muted")
        v.addWidget(note)
        self.ptabs = QTabWidget()
        self.p_dupes = table(["Console", "Game", "Copies", "Files"], {0: 170, 1: 380, 2: 60}, 3)
        self.p_unknown = table(["Console", "File", "Looks like", "Known as (TOSEC / hacks list)"], {0: 170, 1: 600, 2: 110}, 3)
        self.p_wrong = table(["File", "Belongs to", "Game"], {0: 700, 1: 170}, 2)
        self.p_errors = table(["File", "Problem"], {0: 800}, 1)
        for t, name in ((self.p_dupes, "Duplicates"), (self.p_unknown, "Not in No-Intro"),
                        (self.p_wrong, "Wrong console"), (self.p_errors, "Unreadable")):
            col = 1 if t in (self.p_dupes, self.p_unknown) else 0
            t.itemDoubleClicked.connect(lambda it, t=t, col=col: (f := t.item(it.row(), col).data(Qt.UserRole))
                                        and open_folder_of(f))
            self.ptabs.addTab(t, name)
        v.addWidget(self.ptabs, 1)
        return w

    # -- data -----------------------------------------------------------------------------------------------
    def kinds(self) -> set[str]:
        return KIND_SETS[self.kind_box.currentText()]

    def scope(self):
        """"All", or the set of regions ticked."""
        on = {r for r in REGIONS if r in self.s.regions}
        return "All" if len(on) == len(REGIONS) else on

    def scope_text(self) -> str:
        sc = self.scope()
        return "All regions" if sc == "All" else " + ".join(r for r in REGIONS if r in sc)

    def folders(self) -> dict[str, list[str]]:
        f = find_folders(self.s.games_root, self.s.folders)
        if self.s.consoles:
            f = {k: v for k, v in f.items() if k in self.s.consoles}
        return f

    def fill_menu(self) -> None:
        keep = self.menu.currentItem().data(Qt.UserRole) if self.menu.currentItem() else "overview"
        self.menu.blockSignals(True)
        self.menu.clear()
        running, waiting = self.downloads.counts() if hasattr(self, "downloads") else (0, 0)
        dl = "⬇  Downloads" + (f"   {running} ⬇ · {waiting} ⏳" if running or waiting else "")
        for label, key in (("📊  Overview", "overview"), ("⚠  Problems", "problems"), (dl, "downloads"),
                           ("🎮  Free homebrew", "homebrew")):
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, key)
            self.menu.addItem(it)
        found = self.folders()
        tot_all = have_all = empty = 0
        maker = ""
        for c in CONSOLES:                 # grouped by maker: NINTENDO, SEGA, NEC, ATARI ...
            if c.key not in found:
                continue
            r = self.results.get(c.key)
            if r and not r.files:          # a console folder without games (an emulator's empty folder)
                empty += 1
                continue
            if c.maker != maker:
                maker = c.maker
                head = QListWidgetItem(maker.upper())
                head.setFlags(Qt.NoItemFlags)
                self.menu.addItem(head)
            text = f"   {c.name}"
            if r:
                total, have = r.counts(self.kinds(), self.scope())
                tot_all += total
                have_all += have
                text += f"   {have}/{total}"
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, c.key)
            it.setToolTip("; ".join(found[c.key]))
            self.menu.addItem(it)
        for i in range(self.menu.count()):
            if self.menu.item(i).data(Qt.UserRole) == keep:
                self.menu.setCurrentRow(i)
                break
        else:
            self.menu.setCurrentRow(0)
        self.menu.blockSignals(False)
        if self.results:
            self.totals.setText(f"{have_all} of {tot_all} games ({self.scope_text()}, {self.kind_box.currentText().lower()})"
                                f" · {len(self.results) - empty} consoles"
                                + (f" ({empty} empty folders hidden)" if empty else ""))
        self.show_page()

    def select_console(self, key: str) -> None:
        for i in range(self.menu.count()):
            if self.menu.item(i).data(Qt.UserRole) == key:
                self.menu.setCurrentRow(i)
                return

    def current_key(self) -> str:
        it = self.menu.currentItem()
        return it.data(Qt.UserRole) if it else "overview"

    def show_page(self) -> None:
        key = self.current_key()
        self.search.setVisible(key in BY_KEY)
        self.show_box.setVisible(key in BY_KEY)
        self.show_label.setVisible(key in BY_KEY)
        for wd in self.filter_widgets:
            wd.setVisible(key not in ("downloads", "homebrew"))
        if key == "overview":
            self.pages.setCurrentIndex(0)
            self.fill_overview()
        elif key == "problems":
            self.pages.setCurrentIndex(2)
            self.fill_problems()
        elif key == "downloads":
            self.pages.setCurrentIndex(3)
        elif key == "homebrew":
            self.pages.setCurrentIndex(4)
        elif key:
            self.pages.setCurrentIndex(1)
            self.fill_games()

    def fill_overview(self) -> None:
        t = self.overview
        t.setSortingEnabled(False)
        rows = [self.results[c.key] for c in CONSOLES if c.key in self.results and self.results[c.key].files]
        t.setRowCount(len(rows))
        for i, r in enumerate(rows):
            total, have = r.counts(self.kinds(), self.scope())
            vals = [r.console.name, have, total, f"{have / total:.0%}" if total else "", total - have, r.files,
                    len(r.unknown), len(r.duplicates), r.dat_version]
            for c, val in enumerate(vals):
                it = Item(str(val))
                if c == 0:
                    it.setData(Qt.UserRole, r.console.key)
                if c == 3 and total:
                    it.setData(Qt.UserRole + 1, have / total)
                t.setItem(i, c, it)
        t.setSortingEnabled(True)
        self.overview_note.setText(
            "Counted against No-Intro's lists by the games' content (any version of a game counts as having it). "
            f"Regions: {self.scope_text()} · {self.kind_box.currentText()}. Double-click a console to see its games."
            if self.results else "Scan your collection to see what you have.")

    def fill_games(self) -> None:
        key = self.current_key()
        r = self.results.get(key)
        c = BY_KEY.get(key)
        if not c:
            return
        t = self.games
        t.setSortingEnabled(False)
        t.setRowCount(0)
        if not r:
            self.console_title.setText(f"{c.name} - not scanned yet")
            return
        kinds, scope, show = self.kinds(), self.scope(), self.show_box.currentText()
        q = self.search.text().strip().lower()
        p = theme.current()
        rows = []
        for ti in r.titles.values():
            games = ti.games_in(kinds, scope)
            if not games:
                continue
            have = bool(ti.have)
            if (show == "Missing" and have) or (show == "Have" and not have) or (q and q not in ti.title.lower()):
                continue
            rows.append((ti, games, have))
        total, have_n = r.counts(kinds, scope)
        self.console_title.setText(f"{c.name}  -  you have {have_n} of {total} ({have_n / total:.0%})" if total
                                   else c.name)
        t.setRowCount(len(rows))
        for i, (ti, games, have) in enumerate(sorted(rows, key=lambda x: x[0].title.lower())):
            regions = sorted({reg for g in games for reg in ti.games[g][0].regions})
            files = [f for fs in ti.have.values() for f in fs]
            vals = [ti.title, ", ".join(regions), "✓ Have" if have else "✗ Missing",
                    f"{len(ti.have)} / {len(ti.games)}", Path(files[0].split(" › ")[0]).name if files else ""]
            for col, val in enumerate(vals):
                it = Item(val)
                if col == 0:
                    it.setData(Qt.UserRole, ti.title)
                if col == 2:
                    it.setForeground(QColor(p.ready if have else p.problem))
                if col == 4 and files:
                    it.setData(Qt.UserRole, files[0])
                    it.setToolTip("\n".join(files))
                t.setItem(i, col, it)
        t.setSortingEnabled(True)
        if not getattr(self, "_sorted_once", False):
            t.sortByColumn(0, Qt.AscendingOrder)
            self._sorted_once = True
        self.status.setText(f"{len(rows)} shown")
        if t.rowCount():
            t.selectRow(0)
            self.show_game()
        else:
            self.heading.setText("")
            self.versions.clear()
            self.cover.setPixmap(QPixmap())
            self.cover.setText("")

    def show_game(self) -> None:
        r = self.results.get(self.current_key())
        rows = {i.row() for i in self.games.selectedIndexes()}
        if not r or not rows:
            return
        title = self.games.item(next(iter(rows)), 0).data(Qt.UserRole)
        ti = r.titles.get(title)
        if not ti:
            return
        self.heading.setText(ti.title)
        self.versions.clear()
        p = theme.current()
        shown = set(ti.games_in({e.kind for es in ti.games.values() for e in es}, self.scope()))
        for g in sorted(shown, key=lambda g: (g not in ti.have, g)):
            e = ti.games[g][0]
            mine = ti.have.get(g)
            it = QListWidgetItem(("✓  " if mine else "✗  ") + g + ("" if e.kind == "game" else f"   [{e.kind}]"))
            it.setForeground(QColor(p.ready if mine else p.muted))
            if mine:
                it.setData(Qt.UserRole, mine[0])
                it.setToolTip("\n".join(mine))
            self.versions.addItem(it)
        if len(ti.games) > len(shown):
            it = QListWidgetItem(f"   + {len(ti.games) - len(shown)} from regions you hid")
            it.setFlags(Qt.NoItemFlags)
            self.versions.addItem(it)
        best = next((g for g in ti.games if g in ti.have), None) or next(iter(ti.games))
        self.load_cover(r.console.dat, best)

    def load_cover(self, dat: str, game: str) -> None:
        self.cover.setText("…")
        self.cover.setPixmap(QPixmap())
        if self._cover_reply is not None:
            self._cover_reply.abort()
        name = re.sub(r'[&*/:`<>?\\|"]', "_", game)
        url = THUMBS.format(urllib.parse.quote(dat), urllib.parse.quote(name))
        reply = self._cover_reply = self.net.get(QNetworkRequest(QUrl(url)))

        def done() -> None:
            reply.deleteLater()
            if reply is not self._cover_reply:      # an older game's cover, aborted
                return
            self._cover_reply = None
            pix = QPixmap()
            if reply.error() == QNetworkReply.NoError and pix.loadFromData(reply.readAll()):
                self.cover.setPixmap(pix.scaled(self.cover.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            else:
                self.cover.setText("no box art")
        reply.finished.connect(done)

    def fill_problems(self) -> None:
        def fill(t: QTableWidget, rows: list[list[str]], key_col: int, paths: list[str]) -> None:
            t.setSortingEnabled(False)
            t.setRowCount(len(rows))
            for i, row in enumerate(rows):
                for c, val in enumerate(row):
                    it = Item(str(val))
                    if c == key_col:
                        it.setData(Qt.UserRole, paths[i])
                    t.setItem(i, c, it)
            t.setSortingEnabled(True)
        d_rows, d_paths, u_rows, u_paths, e_rows, e_paths = [], [], [], [], [], []
        for r in self.results.values():
            for g, fs in r.duplicates.items():
                d_rows.append([r.console.name, g, len(set(fs)), " | ".join(sorted(set(fs)))])
                d_paths.append(sorted(set(fs))[0])
            for it in r.unknown:
                name = it.inner or Path(it.path).name
                k = r.known.get(it.label)
                look = ("bad dump / overdump" if k and k.kind == "bad" else "bad dump" if re.search(r"\[b\d*\]", name)
                        else "hack" if re.search(r"\[h", name) else "translation" if re.search(r"\[T[+-]", name) else "")
                u_rows.append([r.console.name, it.label, look, f"{k.source}: {k.name}" if k else ""])
                u_paths.append(it.path)
            for f, err in r.errors.items():
                e_rows.append([f, err])
                e_paths.append(f)
        fill(self.p_dupes, d_rows, 1, d_paths)
        fill(self.p_unknown, u_rows, 1, u_paths)
        fill(self.p_wrong, [list(w) for w in self.wrong], 0, [w[0] for w in self.wrong])
        fill(self.p_errors, e_rows, 0, e_paths)
        for i, n in enumerate((len(d_rows), len(u_rows), len(self.wrong), len(e_rows))):
            self.ptabs.setTabText(i, f"{['Duplicates', 'Not in No-Intro', 'Wrong console', 'Unreadable'][i]} ({n})")

    # -- actions ----------------------------------------------------------------------------------------------
    def busy(self, on: bool, text: str = "") -> None:
        self.bar.setVisible(on)
        self.b_stop.setVisible(on)
        if text:
            self.status.setText(text)

    def run(self, fn, done, text: str) -> None:
        if self.worker is not None:
            QMessageBox.information(self, "Busy", "Wait until the current job is done (or press Stop).")
            return
        w = self.worker = Worker(fn)
        w.signals.progress.connect(self.status.setText)
        w.signals.done.connect(lambda res: (setattr(self, "worker", None), self.busy(False), done(res)))
        w.signals.failed.connect(lambda err: (setattr(self, "worker", None), self.busy(False),
                                              QMessageBox.warning(self, "Something went wrong", err)))
        self.busy(True, text)
        QThreadPool.globalInstance().start(w)

    def ensure_lists(self, keys: list[str]) -> bool:
        """The No-Intro lists of these consoles: asks before downloading the ones not here yet."""
        need = sorted({BY_KEY[k].dat for k in keys if not self.dats.have(BY_KEY[k].dat)})
        if not need:
            return True
        ok = QMessageBox.question(
            self, "Download No-Intro lists?",
            f"To know which games exist, the checker needs No-Intro's list for {len(need)} console(s):\n\n"
            + "\n".join(need) + "\n\nThey are text files (a few MB each) from github.com/libretro/libretro-database, "
            "kept in the checker's data folder. Download them now?")
        if ok != QMessageBox.Yes:
            return False
        return True

    def scan(self) -> None:
        found = self.folders()
        if not found:
            QMessageBox.information(self, "No console folders", "Set your games folder in Settings first.")
            return
        if not self.ensure_lists(list(found)):
            return
        tools = Tools(self.s.sevenzip, self.s.winrar_dir)

        def job(w: Worker):
            for k in found:
                d = BY_KEY[k].dat
                if not self.dats.have(d):
                    w.signals.progress.emit(f"Downloading the {BY_KEY[k].name} list…")
                    self.dats.download(d)
            out = {}
            for k, folders in found.items():
                if w.stop:
                    break
                c = BY_KEY[k]
                if not self.dats.have(c.dat):
                    continue
                w.signals.progress.emit(f"Reading {c.name}…")
                out[k] = scan_console(c, folders, self.dats, tools, self.cache, self.s.skip_folders,
                                      w.signals.progress.emit, lambda: w.stop, extra=self.extra)
                self.cache.save()
            return out, wrong_console(out, self.dats)

        def done(res) -> None:
            self.results, self.wrong = res
            self.fill_menu()
            files = sum(r.files for r in self.results.values())
            self.status.setText(f"Scanned {files} files in {len(self.results)} console folders")
        self.run(job, done, "Scanning…")

    def missing_lines(self) -> list[str]:
        key = self.current_key()
        targets = [self.results[key]] if key in self.results else list(self.results.values())
        out = []
        for r in targets:
            miss = [ti for ti in r.titles.values() if ti.games_in(self.kinds(), self.scope()) and not ti.have]
            if not miss:
                continue
            out.append(f"{r.console.name} - missing {len(miss)} ({self.scope_text()}, {self.kind_box.currentText()})")
            for ti in sorted(miss, key=lambda t: t.title.lower()):
                regs = sorted({reg for g in ti.games_in(self.kinds(), self.scope()) for reg in ti.games[g][0].regions})
                out.append(f"  {ti.title}  ({', '.join(regs)})")
            out.append("")
        return out

    def copy_missing(self) -> None:
        lines = self.missing_lines()
        QGuiApplication.clipboard().setText("\n".join(lines))
        self.status.setText(f"Copied {sum(1 for x in lines if x.startswith('  '))} missing games")

    def save_missing(self) -> None:
        lines = self.missing_lines()
        if not lines:
            return
        key = self.current_key()
        name = (BY_KEY[key].name if key in BY_KEY else "All consoles").replace("/", "-")
        start = str(Path(self.s.games_root or ".") / f"{name} missing ({self.scope_text()}).txt")
        path, _ = QFileDialog.getSaveFileName(self, "Save missing list", start, "Text (*.txt)")
        if path:
            Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.status.setText(f"Saved {path}")

    def open_settings(self) -> None:
        from .dialogs import SettingsDialog  # noqa: PLC0415
        before = theme.current()
        d = SettingsDialog(self.s, self.dats, self)
        d.preview.connect(lambda name: theme.apply(name))
        if d.exec():
            save_settings(self.s)
            theme.apply(self.s.theme)
            self.fill_menu()
            if d.rescan:
                self.scan()
        else:
            theme.apply(next((n for n, p in theme.THEMES.items() if p == before), theme.DEFAULT_THEME))

    def sort_inbox(self) -> None:
        if not self.results:
            QMessageBox.information(self, "Scan first", "Scan your collection first, so the checker knows what you have.")
            return
        from .dialogs import SortDialog  # noqa: PLC0415
        SortDialog(self).exec()

    def clean_names(self) -> None:
        key = self.current_key()
        if key not in self.results:
            QMessageBox.information(self, "Pick a console", "Select a console on the left first (and scan).")
            return
        from .dialogs import RenameDialog  # noqa: PLC0415
        RenameDialog(self, self.results[key]).exec()

    # -- self-update ----------------------------------------------------------------------------------
    def check_updates(self, manual: bool = True, ask: bool = True) -> None:
        """At start (once a day, when switched on) or from Settings: a newer release on GitHub? Asks first
        (not with ask=False: ROMCollectionChecker.exe --update)."""
        import time  # noqa: PLC0415
        from ..core import update  # noqa: PLC0415
        if not manual and (not self.s.check_updates or not update.can_update()
                           or time.time() - self.s.last_update_check < 20 * 3600):
            return
        if manual and not update.can_update():
            QMessageBox.information(self, "Updates", "Updates are for the built ROMCollectionChecker.exe - "
                                    "from source, use git pull.")
            return
        w = Worker(lambda _w: update.latest())

        def done(rel) -> None:
            self.s.last_update_check = time.time()
            save_settings(self.s)
            if not rel or not update.newer(rel.version, __version__):
                if manual:
                    QMessageBox.information(self, "Updates", f"You have the newest version ({__version__}).")
                return
            if not ask:
                self.install_update(rel)
                return
            notes = re.sub(r"🤖.*", "", rel.notes).strip()
            box = QMessageBox(self)
            box.setWindowTitle("Update available")
            box.setTextFormat(Qt.MarkdownText)
            box.setText(f"**ROM Collection Checker {rel.version}** is out (you have {__version__}).\n\n"
                        + (notes[:1500] + ("…" if len(notes) > 1500 else "")) +
                        "\n\nUpdate now? It downloads the new exe, closes the checker, swaps it and starts again. "
                        "Your settings and lists stay.")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            if box.exec() == QMessageBox.Yes:
                self.install_update(rel)

        w.signals.done.connect(done)
        w.signals.failed.connect(lambda err: manual and QMessageBox.warning(self, "Updates", f"Couldn't check: {err}"))
        self._update_worker = w
        QThreadPool.globalInstance().start(w)

    def install_update(self, rel) -> None:
        import sys  # noqa: PLC0415
        from ..core import update  # noqa: PLC0415
        target = Path(sys.executable).with_name(Path(sys.executable).name + ".new")

        def job(w: Worker):
            mb = lambda n: f"{n / 1048576:.1f}"  # noqa: E731
            return update.download(rel, target, lambda d, t: w.signals.progress.emit(
                f"Downloading {rel.version}: {mb(d)} of {mb(t)} MB"), lambda: w.stop)

        def done(path) -> None:
            if self.worker is not None:          # a scan or sort still running: stop it, then close
                self.worker.stop = True
            update.install(path)
            self.status.setText("Updating - the checker starts again in a moment…")
            QTimer.singleShot(300, self.close)

        # its own worker: an update must not wait for (or be refused by) a scan that is running
        w = Worker(job)
        w.signals.progress.connect(self.status.setText)
        w.signals.done.connect(done)
        w.signals.failed.connect(lambda err: QMessageBox.warning(self, "Update", f"The update failed: {err}"))
        self._download_worker = w
        self.status.setText(f"Downloading version {rel.version}…")
        QThreadPool.globalInstance().start(w)

    def undo_dialog(self) -> None:
        from .dialogs import UndoDialog  # noqa: PLC0415
        UndoDialog(self).exec()
