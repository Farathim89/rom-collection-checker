"""Settings, Sort inbox, Clean names and Undo."""
from __future__ import annotations

import re

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QTabWidget, QTableWidget,
                               QToolButton, QVBoxLayout, QWidget)

from ..config import UNDO, Settings, save_settings
from ..core.collection import find_folders
from ..core.consoles import BY_KEY, CONSOLES
from ..core.dat import DatStore
from ..core.hashing import Tools
from ..core.inbox import (HAD, apply_plan, apply_tidy, make_console_folders, make_plan, region_moves, tidy_inbox,
                          tidy_plan, undo, write_undo)
from ..core.rename import apply_renames, make_renames
from . import theme
from .icons import console_icon, flag_icon
from .main_window import Item, open_folder_of, table


def folder_row(edit: QLineEdit, parent: QWidget) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(edit, 1)
    b = QPushButton("Browse…")
    b.clicked.connect(lambda: (d := QFileDialog.getExistingDirectory(parent, "Folder", edit.text())) and edit.setText(d))
    h.addWidget(b)
    return w


def tick_groups(groups: list[tuple[str, list[tuple]]], columns: int = 3, icon_size: QSize = QSize(20, 20)):
    """Bold group ticks, each with its items ticks in columns (filled top to bottom) - in a scroll area.
    groups: (heading, [(label, key, ticked[, icon])]). Returns (widget, [(checkbox, key)])."""
    with_icons = any(len(it) > 3 for _h, items in groups for it in items)
    blank = QPixmap(icon_size)
    blank.fill(Qt.transparent)
    out: list[tuple[QCheckBox, str]] = []
    body = QWidget()
    grid = QGridLayout(body)                     # one grid for all groups: their columns line up
    grid.setHorizontalSpacing(24)
    grid.setVerticalSpacing(4)
    grid.setColumnMinimumWidth(0, 2)
    for c in range(1, columns + 1):
        grid.setColumnStretch(c, 1)
    row = 0
    for heading, items in groups:
        head = QCheckBox(heading)
        head.setTristate(True)
        f = head.font()
        f.setBold(True)
        head.setFont(f)
        grid.setRowMinimumHeight(row, 30)
        grid.addWidget(head, row, 0, 1, columns + 1, Qt.AlignBottom)
        row += 1
        boxes = []
        rows = max(1, (len(items) + columns - 1) // columns)
        for i, (label, key, on, *icon) in enumerate(items):
            cb = QCheckBox(label)
            cb.setChecked(on)
            if with_icons:                           # flags line up: a blank space where there is none
                cb.setIcon(icon[0] if icon and icon[0] else QIcon(blank))
                cb.setIconSize(icon_size)
            grid.addWidget(cb, row + i % rows, 1 + i // rows)
            boxes.append(cb)
            out.append((cb, key))
        row += rows

        def sync(_=None, head=head, boxes=boxes) -> None:
            on = sum(b.isChecked() for b in boxes)
            head.blockSignals(True)
            head.setCheckState(Qt.Checked if on == len(boxes) else Qt.Unchecked if not on else Qt.PartiallyChecked)
            head.blockSignals(False)

        def set_all(state, boxes=boxes, sync=sync) -> None:
            for b in boxes:                          # unticked -> all off; partly or fully ticked -> all on
                b.blockSignals(True)
                b.setChecked(state != 0)
                b.blockSignals(False)
            sync()

        head.stateChanged.connect(set_all)
        for cb in boxes:
            cb.toggled.connect(sync)
        sync()
    grid.setRowStretch(row, 1)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidget(body)
    return scroll, out


class ThemePicker(QWidget):
    picked = Signal(str)

    def __init__(self, current: str, columns: int = 4, parent=None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 4, 0, 0)
        grid.setSpacing(10)
        self._group = QButtonGroup(self)
        self.current = current
        for i, name in enumerate(theme.THEMES):
            tile = QToolButton()
            tile.setObjectName("themetile")
            tile.setCheckable(True)
            tile.setChecked(name == current)
            tile.setText(name)
            tile.setIcon(QIcon(theme.swatch(name)))
            tile.setIconSize(QSize(150, 84))
            tile.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            tile.clicked.connect(lambda _=False, n=name: (setattr(self, "current", n), self.picked.emit(n)))
            self._group.addButton(tile)
            grid.addWidget(tile, i // columns, i % columns)


class SettingsDialog(QDialog):
    preview = Signal(str)

    def __init__(self, s: Settings, dats: DatStore, parent=None):
        super().__init__(parent)
        self.s, self.dats, self.rescan = s, dats, False
        self.setWindowTitle("Settings")
        self.resize(820, 640)
        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs, 1)

        page = QWidget()
        form = QFormLayout(page)
        tabs.addTab(page, "Your games")
        self.root = QLineEdit(s.games_root)
        self.root.setPlaceholderText(r"e.g. E:\Games - console folders, also inside maker folders (Nintendo\SNES)")
        form.addRow("Games folder", folder_row(self.root, self))
        self.inbox = QLineEdit(s.inbox)
        self.inbox.setPlaceholderText("a folder with new games to sort in")
        form.addRow("Inbox", folder_row(self.inbox, self))
        self.skip = QLineEdit(", ".join(s.skip_folders))
        form.addRow("Skip folders", self.skip)
        found = find_folders(s.games_root, s.folders)
        makers: dict[str, list] = {}
        for c in CONSOLES:
            on = (c.key in s.consoles) if s.consoles else (c.key in found)
            makers.setdefault(c.maker, []).append((c.name if c.key in found else f"{c.name}   ·   no folder", c.key, on,
                                                   console_icon(c.key)))
        scroll, self.console_boxes = tick_groups(list(makers.items()))
        for cb, key in self.console_boxes:
            cb.setToolTip(self._folder_tip(key, found.get(key, [])))
            cb.setContextMenuPolicy(Qt.CustomContextMenu)
            cb.customContextMenuRequested.connect(lambda _pos, cb=cb, key=key: self._pick_folder(cb, key))
        scroll.setMinimumHeight(260)
        form.addRow("Consoles", scroll)
        form.addRow("", QLabel("Tick the consoles to check. Right-click one to choose its folder yourself."))

        page = QWidget()
        form = QFormLayout(page)
        tabs.addTab(page, "Sorting")
        self.unpack = QCheckBox("Take ROMs out of zip / 7z / rar")
        self.unpack.setChecked(s.unpack)
        self.names = QCheckBox("Give sorted games their No-Intro name")
        self.names.setChecked(s.official_names)
        self.hacks = QCheckBox("Also sort in hacks [h] (fan translations [T+] always are)")
        self.hacks.setChecked(s.take_hacks)
        self.unknown = QCheckBox("Sort in dumps No-Intro doesn't list (homebrew, multicarts, translations)")
        self.unknown.setChecked(s.take_unknown)
        self.demos = QCheckBox("Also sort in demos (kiosk, sampler and magazine demo discs too) - off: they stay in the inbox")
        self.demos.setChecked(s.take_demos)
        self.by_region = QCheckBox("Put sorted games in a folder per region (USA, Japan, Germany …; a game from "
                                   "several regions goes to World)")
        self.by_region.setChecked(s.region_folders)
        for cb in (self.unpack, self.names, self.hacks, self.unknown, self.demos, self.by_region):
            form.addRow(cb)
        form.addRow(QLabel("Bad dumps [b] and games you already have are never moved."))

        page = QWidget()
        form = QFormLayout(page)
        tabs.addTab(page, "Tools && lists")
        t = Tools(s.sevenzip, s.winrar_dir, s.dolphin_tool)
        self.seven = QLineEdit(s.sevenzip)
        self.seven.setPlaceholderText(t.seven or "not found - needed for .7z")
        form.addRow("7-Zip (7z.exe / 7za.exe)", self.seven)
        self.winrar = QLineEdit(s.winrar_dir)
        self.winrar.setPlaceholderText(str(Path(t.unrar).parent) if t.unrar else "not found - needed for .rar")
        form.addRow("WinRAR folder", folder_row(self.winrar, self))
        self.dolphin = QLineEdit(s.dolphin_tool)
        self.dolphin.setPlaceholderText(t.dolphin or "not found - needed to check .rvz / .wbfs / .gcz discs")
        form.addRow("DolphinTool.exe", self.dolphin)
        have = [c for c in CONSOLES if dats.have(c.dat)]
        info = QLabel(f"{len(have)} No-Intro lists downloaded" + (
            "; oldest " + f"{max(dats.age_days(c.dat) for c in have):.0f} days" if have else ""))
        form.addRow("Lists", info)
        upd = QPushButton("Update all lists now")
        upd.clicked.connect(self._update_lists)
        form.addRow("", upd)
        ex = parent.extra if parent is not None and hasattr(parent, "extra") else None
        xl = QPushButton("Get TOSEC, hacks and homebrew lists (about 8 MB)" if ex and not ex.have_any()
                         else "Update TOSEC, hacks and homebrew lists")
        xl.setToolTip("A second check for what No-Intro doesn't list: TOSEC (also demos, public domain, translations, "
                      "hacks), libretro's hacks and homebrew lists - from github.com/libretro/libretro-database")
        xl.clicked.connect(lambda: self._get_extra(ex))
        form.addRow("More lists", xl)
        self.quick = QCheckBox("Quick check for discs, DS and N64: name games by their header (seconds) - "
                               "the Verify button on a console does the full checksum check")
        self.quick.setChecked(s.quick_check)
        form.addRow("Quick check", self.quick)
        self.redump = QCheckBox("Xbox 360: when a disc fits several releases, ask redump.org which one it is "
                                "(sends the game's title; asked once per game)")
        self.redump.setChecked(s.ask_redump)
        form.addRow("", self.redump)
        self.auto_update = QCheckBox("Look for a new version of the checker at every start (asks before updating)")
        self.auto_update.setChecked(s.check_updates)
        form.addRow("Updates", self.auto_update)
        chk = QPushButton("Check for updates now")
        chk.clicked.connect(lambda: parent.check_updates(manual=True) if parent else None)
        form.addRow("", chk)

        look = QScrollArea()
        look.setWidgetResizable(True)
        look.setFrameShape(QFrame.NoFrame)
        box = QWidget()
        lv = QVBoxLayout(box)
        lv.addWidget(QLabel("Colour theme - click one to try it, Save to keep it"))
        self.theme = ThemePicker(s.theme if s.theme in theme.THEMES else theme.DEFAULT_THEME)
        self.theme.picked.connect(self.preview.emit)
        lv.addWidget(self.theme)
        lv.addStretch()
        look.setWidget(box)
        tabs.addTab(look, "Look")
        tabs.addTab(self._regions_tab(s, dats), "Regions")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    @staticmethod
    def _folder_tip(key: str, folders: list[str]) -> str:
        return ("\n".join(folders) if folders else "No folder found") + "\nRight-click to choose its folder."

    def _pick_folder(self, cb: QCheckBox, key: str) -> None:
        d = QFileDialog.getExistingDirectory(self, f"Folder for {BY_KEY[key].name}", self.root.text())
        if d:
            self.s.folders[key] = [d]
            cb.setText(BY_KEY[key].name)
            cb.setToolTip(self._folder_tip(key, [d]))
            cb.setChecked(True)

    def _regions_tab(self, s: Settings, dats: DatStore) -> QWidget:
        """World / USA / Europe / Asia / Other, each with the real region names of your lists under it: untick a
        group or a single region to hide it everywhere."""
        from ..core.dat import REGION_TREE, region_counts  # noqa: PLC0415
        self.regions_changed = False
        page = QWidget()
        v = QVBoxLayout(page)
        note = QLabel("Untick a region to hide it everywhere: games released only there disappear from the lists, "
                      "the counts and the missing lists (a game with a version from a region you keep still counts). "
                      "A group's tick does all its regions. The Regions picker on the main page (USA / Europe / Asia / "
                      "Other) works on top of this.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        v.addWidget(note)
        counts = region_counts(dats.entries(c.dat) for c in CONSOLES if dats.have(c.dat))
        groups = []
        for group, raws in REGION_TREE:
            present = sorted((r for r in raws if counts.get(r)), key=lambda x: -counts[x])
            if present:
                groups.append((f"{group}   -   {sum(counts[r] for r in present):,} games",
                               [(f"{r}   -   {counts[r]:,} games" if r != "Other" else
                                 f"Other / not named   -   {counts[r]:,} games", r, r not in s.hidden_regions, flag_icon(r))
                                for r in present]))
        scroll, self.region_boxes = tick_groups(groups, icon_size=QSize(20, 15))
        v.addWidget(scroll, 1)
        row = QHBoxLayout()
        for text, fn in (("Select all", lambda on: True), ("Select none", lambda on: False), ("Invert", lambda on: not on)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, f=fn: [c.setChecked(f(c.isChecked())) for c, _r in self.region_boxes])
            row.addWidget(b)
        row.addStretch()
        v.addLayout(row)
        return page

    def _get_extra(self, ex) -> None:
        if ex is None:
            return
        from ..core.extra import AVAILABLE  # noqa: PLC0415
        todo = [(s, n) for s, names in AVAILABLE.items() for n in names]
        if QMessageBox.question(self, "More lists", f"Download {len(todo)} lists (about 8 MB of text) from "
                                "github.com/libretro/libretro-database? Then scan again.") != QMessageBox.Yes:
            return
        failed = []
        for s, n in todo:
            try:
                ex.download(s, n)
            except Exception as e:  # noqa: BLE001
                failed.append(f"{s}/{n}: {e}")
        QMessageBox.information(self, "More lists", f"{len(todo) - len(failed)} lists downloaded." +
                                ("\n\n" + "\n".join(failed[:10]) if failed else ""))
        self.rescan = True

    def _update_lists(self) -> None:
        names = sorted({c.dat for c in CONSOLES if self.dats.have(c.dat)})
        if QMessageBox.question(self, "Update lists", f"Download the {len(names)} lists again from "
                                "github.com/libretro/libretro-database?") != QMessageBox.Yes:
            return
        bad = []
        for n in names:
            try:
                self.dats.download(n)
            except Exception as e:  # noqa: BLE001
                bad.append(f"{n}: {e}")
        QMessageBox.information(self, "Lists", "Updated." if not bad else "\n".join(bad))
        self.rescan = True

    def _save(self) -> None:
        s = self.s
        before = (s.games_root, list(s.skip_folders), list(s.consoles), dict(s.folders))
        s.games_root = self.root.text().strip()
        s.inbox = self.inbox.text().strip()
        s.skip_folders = [x.strip() for x in self.skip.text().split(",") if x.strip()]
        ticked = [key for cb, key in self.console_boxes if cb.isChecked()]
        found = find_folders(s.games_root, s.folders)
        s.consoles = [] if set(ticked) == set(found) else ticked
        s.unpack, s.official_names = self.unpack.isChecked(), self.names.isChecked()
        s.region_folders = self.by_region.isChecked()
        s.take_demos = self.demos.isChecked()
        s.take_hacks, s.take_unknown = self.hacks.isChecked(), self.unknown.isChecked()
        s.sevenzip, s.winrar_dir = self.seven.text().strip(), self.winrar.text().strip()
        s.dolphin_tool = self.dolphin.text().strip()
        if s.quick_check != self.quick.isChecked():
            s.quick_check = self.quick.isChecked()
            self.rescan = True
        s.check_updates = self.auto_update.isChecked()
        s.ask_redump = self.redump.isChecked()
        hidden = [r for c, r in self.region_boxes if not c.isChecked()]
        if sorted(hidden) != sorted(s.hidden_regions):
            s.hidden_regions = hidden
            from ..core import dat as dat_mod  # noqa: PLC0415
            dat_mod.HIDDEN_REGIONS = set(hidden)
            self.regions_changed = True
        s.theme = self.theme.current
        self.rescan = self.rescan or before != (s.games_root, s.skip_folders, s.consoles, s.folders)
        self.accept()


PLAN_COLS = ["", "File", "Console", "Game (No-Intro)", "Plan", "Goes to"]


class SortDialog(QDialog):
    """Pick the inbox → Check (makes the plan) → untick what you don't want → Apply."""

    def __init__(self, main):
        super().__init__(main)
        self.main, self.plan = main, None
        self.setWindowTitle("Sort inbox")
        self.resize(1400, 800)
        v = QVBoxLayout(self)
        self.inbox = QLineEdit(main.s.inbox)
        row = QHBoxLayout()
        row.addWidget(QLabel("Inbox"))
        row.addWidget(folder_row(self.inbox, self), 1)
        self.b_check = QPushButton("Check")
        self.b_check.setObjectName("primary")
        self.b_check.clicked.connect(self.check)
        row.addWidget(self.b_check)
        v.addLayout(row)
        self.only_moves = QCheckBox("Show only what will be moved")
        self.only_moves.setChecked(True)
        self.only_moves.toggled.connect(lambda *_: self.fill())
        opts = QHBoxLayout()
        opts.addWidget(self.only_moves)
        opts.addStretch()
        mk = QPushButton("📁  Make console folders in the inbox")
        mk.setToolTip("Empty maker\\console folders (laid out like your collection). A game you put in one is sorted to "
                      "that console, also when its file type (.bin, .rom) could be several consoles.")
        mk.clicked.connect(self.make_folders)
        opts.addWidget(mk)
        v.addLayout(opts)
        self.summary = QLabel("Check reads every game in the inbox and compares it with your collection. "
                              "Nothing is moved until you press Apply.")
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        v.addWidget(self.summary)
        self.t = table(PLAN_COLS, {0: 30, 1: 380, 2: 150, 3: 330, 4: 160}, 5)
        self.t.itemDoubleClicked.connect(lambda it: open_folder_of(self.t.item(it.row(), 1).data(Qt.UserRole)))
        v.addWidget(self.t, 1)
        # what Check / Apply is doing right now (the main window's status bar is behind this window)
        prow = QHBoxLayout()
        self.p_text = QLabel("")
        self.p_text.setObjectName("muted")
        self.p_bar = QProgressBar()
        self.p_bar.setMinimumWidth(320)
        self.p_stop = QPushButton("Stop")
        self.p_stop.clicked.connect(self.main.stop_job)
        prow.addWidget(self.p_text, 1)
        prow.addWidget(self.p_bar)
        prow.addWidget(self.p_stop)
        v.addLayout(prow)
        self._busy(False)
        bottom = QHBoxLayout()
        self.tidy = QCheckBox("Then tidy the inbox: what's now in your collection goes to _Duplicates, empty folders go")
        self.tidy.setChecked(main.s.tidy_inbox)
        self.tidy.setToolTip("Games you already had, files sorted in and archives whose games were taken out move to "
                             "<games folder>\\_Duplicates\\<inbox name> (Undo puts them back). "
                             "Your Maker\\Console drop folders stay.")
        bottom.addWidget(self.tidy)
        bottom.addStretch()
        self.b_apply = QPushButton("Apply")
        self.b_apply.setObjectName("primary")
        self.b_apply.setEnabled(False)
        self.b_apply.clicked.connect(self.apply)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bottom.addWidget(self.b_apply)
        bottom.addWidget(close)
        v.addLayout(bottom)

    def _busy(self, on: bool, text: str = "") -> None:
        self.p_bar.setVisible(on)
        self.p_stop.setVisible(on)
        if text or not on:
            self.p_text.setText(text)

    def _watch(self, text: str) -> None:
        """Shows the running job's progress here: "Checking 12 / 67: …" fills the bar, other steps make it busy."""
        w = self.main.worker
        if w is None:
            return
        self._busy(True, text)
        self.p_bar.setRange(0, 0)
        w.signals.progress.connect(self._progress)
        w.signals.failed.connect(lambda _e: (self._busy(False), self.b_check.setEnabled(True)))

    def _progress(self, text: str) -> None:
        self.p_text.setText(text)
        m = re.search(r"(\d+) / (\d+)", text)
        if m and "unpacking" not in text:
            self.p_bar.setRange(0, int(m.group(2)))
            self.p_bar.setValue(int(m.group(1)))
        else:
            self.p_bar.setRange(0, 0)                  # unpacking a big archive: busy

    def make_folders(self) -> None:
        inbox = self.inbox.text().strip()
        if not inbox or not Path(inbox).is_dir():
            QMessageBox.information(self, "Inbox", "Choose the inbox folder first.")
            return
        made = make_console_folders(inbox, self.main.folders(), self.main.s.games_root)
        QMessageBox.information(self, "Console folders",
                                f"{len(made)} folders made in {inbox}." if made else "The console folders are already there.")

    def check(self) -> None:
        inbox = self.inbox.text().strip()
        if not inbox or not Path(inbox).is_dir():
            QMessageBox.information(self, "Inbox", "Choose the folder to sort.")
            return
        m, s = self.main, self.main.s
        s.inbox = inbox
        missing = [c.key for c in CONSOLES if not m.dats.have(c.dat)]
        if missing and m.ensure_lists(missing):
            dl = missing
        else:
            dl = []
        have = {k: r.crcs for k, r in m.results.items()}
        have_games = {k: {g for t in r.titles.values() for g in t.have} for k, r in m.results.items()}
        folders = m.folders()
        tools = Tools(s.sevenzip, s.winrar_dir, s.dolphin_tool)

        def job(w):
            for k in dl:
                w.signals.progress.emit(f"Downloading the {BY_KEY[k].name} list…")
                try:
                    m.dats.download(BY_KEY[k].dat)
                except Exception:  # noqa: BLE001 - a console without a list is recognised by file type only
                    pass
            return make_plan(inbox, folders, have, m.dats, tools, s.games_root, have_games=have_games, unpack=s.unpack,
                             official_names=s.official_names, take_hacks=s.take_hacks, take_unknown_dumps=s.take_unknown,
                             region_folders=s.region_folders, take_demos=s.take_demos,
                             progress=w.signals.progress.emit, stop=lambda: w.stop)

        def done(plan) -> None:
            self.plan = plan
            self.fill()
            self.b_apply.setEnabled(any(st.action == "move" for st in plan.steps) or any(st.reason in HAD for st in plan.steps))
        self.b_check.setEnabled(False)
        m.run(job, lambda p: (self.b_check.setEnabled(True), self._busy(False), done(p)), "Checking the inbox…")
        self._watch("Checking the inbox…")

    def fill(self) -> None:
        if not self.plan:
            return
        steps = [st for st in self.plan.steps if st.action == "move" or not self.only_moves.isChecked()]
        moves = sum(1 for st in self.plan.steps if st.action == "move")
        parts = ", ".join(f"{n} {k}" for k, n in sorted(self.plan.summary().items(), key=lambda x: -x[1]))
        self.summary.setText(f"{len(self.plan.steps)} games checked: {parts}. {moves} will be moved "
                             "(untick any you don't want).")
        t = self.t
        t.setSortingEnabled(False)
        t.setRowCount(len(steps))
        p = theme.current()
        self._rows = steps
        for i, st in enumerate(steps):
            chk = Item("")
            if st.action == "move":
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                chk.setCheckState(Qt.Checked if st.enabled else Qt.Unchecked)
            chk.setData(Qt.UserRole, i)
            t.setItem(i, 0, chk)
            vals = [st.label, BY_KEY[st.console].name if st.console else "?", st.game,
                    ("➜ " if st.action == "move" else "– ") + st.reason, st.target]
            for c, val in enumerate(vals, 1):
                it = Item(val)
                if c == 1:
                    it.setData(Qt.UserRole, st.source)
                if c == 4:
                    it.setForeground(QColor(p.ready if st.action == "move" else p.muted))
                t.setItem(i, c, it)
        t.setSortingEnabled(True)

    def apply(self) -> None:
        for r in range(self.t.rowCount()):
            chk = self.t.item(r, 0)
            st = self._rows[chk.data(Qt.UserRole)]
            if st.action == "move":
                st.enabled = chk.checkState() == Qt.Checked
        n = sum(1 for st in self.plan.steps if st.action == "move" and st.enabled)
        m, s = self.main, self.main.s
        tidy = self.tidy.isChecked()
        if s.tidy_inbox != tidy:
            s.tidy_inbox = tidy
            save_settings(s)
        ask = f"Move {n} games into your collection?" + (
            " Then tidy the inbox (what's in your collection goes to _Duplicates)?" if tidy else "")
        if QMessageBox.question(self, "Sort", ask + " (An undo log is kept.)") != QMessageBox.Yes:
            return
        tools = Tools(s.sevenzip, s.winrar_dir, s.dolphin_tool)
        plan = self.plan

        def job(w):
            moved, problems, log = apply_plan(plan, tools, UNDO, w.signals.progress.emit)
            tidied = (0, 0, [], None)
            if tidy:
                w.signals.progress.emit("Tidying the inbox…")
                tidied = tidy_inbox(plan, s.games_root, UNDO)
            return moved, problems, log, tidied

        def done(res) -> None:
            moved, problems, log, (out, dirs, tprob, tlog) = res
            text = f"{moved} games moved in (undo log: {log.name})."
            if tlog:
                text += (f"\n{out} files moved from the inbox to _Duplicates, {dirs} empty folders removed "
                         f"(undo log: {tlog.name}).")
            problems = problems + tprob
            QMessageBox.information(self, "Sorted", text + ("\n\n" + "\n".join(problems[:30]) if problems else ""))
            self.b_apply.setEnabled(False)
            m.scan()
        m.run(job, lambda res: (self._busy(False), done(res)), "Moving…")
        self._watch("Moving…")


class RenameDialog(QDialog):
    def __init__(self, main, res):
        super().__init__(main)
        self.main, self.renames = main, make_renames(res)
        self.setWindowTitle(f"Clean names - {res.console.name}")
        self.resize(1300, 760)
        v = QVBoxLayout(self)
        note = QLabel(f"{len(self.renames)} files can get their official No-Intro name. Untick any you want to keep. "
                      "Files that match several games, or whose new name is taken, are left alone.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        v.addWidget(note)
        self.t = table(["", "Now", "Becomes"], {0: 30, 1: 560}, 2)
        self.t.setSortingEnabled(False)
        self.t.setRowCount(len(self.renames))
        for i, r in enumerate(self.renames):
            chk = Item("")
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Checked)
            chk.setData(Qt.UserRole, i)
            self.t.setItem(i, 0, chk)
            self.t.setItem(i, 1, Item(Path(r.path).name))
            self.t.setItem(i, 2, Item(Path(r.new).name))
        self.t.setSortingEnabled(True)
        v.addWidget(self.t, 1)
        row = QHBoxLayout()
        row.addStretch()
        b = QPushButton("Rename")
        b.setObjectName("primary")
        b.setEnabled(bool(self.renames))
        b.clicked.connect(self.apply)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(b)
        row.addWidget(close)
        v.addLayout(row)

    def apply(self) -> None:
        for r in range(self.t.rowCount()):
            chk = self.t.item(r, 0)
            self.renames[chk.data(Qt.UserRole)].enabled = chk.checkState() == Qt.Checked
        n = sum(r.enabled for r in self.renames)
        if QMessageBox.question(self, "Rename", f"Rename {n} files? (An undo log is kept.)") != QMessageBox.Yes:
            return
        done, problems, log = apply_renames(self.renames, UNDO)
        QMessageBox.information(self, "Renamed", f"{done} files renamed.\nUndo log: {log.name}" +
                                ("\n\n" + "\n".join(problems[:30]) if problems else ""))
        self.accept()
        self.main.scan()


class RegionDialog(QDialog):
    """Your games into a folder per region (<console>/<country>/; World when a game spans groups), with a preview."""

    def __init__(self, main, results):
        super().__init__(main)
        self.main = main
        self.moves = [(r.console.name, a, b) for r in results for a, b in region_moves(r, Path(r.folders[0]))]
        names = ", ".join(r.console.name for r in results) if len(results) <= 3 else f"{len(results)} consoles"
        self.setWindowTitle(f"Region folders - {names}")
        self.resize(1300, 760)
        v = QVBoxLayout(self)
        note = QLabel(f"{len(self.moves)} games (and the saves / tracks next to them) can go into a folder per region: "
                      "Japan, Germany … - World when a game is from regions of different groups (USA, Europe). "
                      "Folders are made only for regions you have. Games in 0 - folders and files the list doesn't "
                      "know stay where they are." if self.moves else
                      "Every game is in its region folder already.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        v.addWidget(note)
        self.t = table(["", "Console", "Game", "Goes to"], {0: 30, 1: 200, 2: 760}, 3)
        self.t.setSortingEnabled(False)
        self.t.setRowCount(len(self.moves))
        for i, (con, a, b) in enumerate(self.moves):
            chk = Item("")
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Checked)
            chk.setData(Qt.UserRole, i)
            self.t.setItem(i, 0, chk)
            self.t.setItem(i, 1, Item(con))
            self.t.setItem(i, 2, Item(a.name + ("  (folder)" if a.is_dir() else "")))
            self.t.setItem(i, 3, Item(b.parent.name))
        self.t.setSortingEnabled(True)
        v.addWidget(self.t, 1)
        row = QHBoxLayout()
        row.addStretch()
        b = QPushButton("Move")
        b.setObjectName("primary")
        b.setEnabled(bool(self.moves))
        b.clicked.connect(self.apply)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(b)
        row.addWidget(close)
        v.addLayout(row)

    def apply(self) -> None:
        import os  # noqa: PLC0415
        todo = [self.moves[self.t.item(r, 0).data(Qt.UserRole)] for r in range(self.t.rowCount())
                if self.t.item(r, 0).checkState() == Qt.Checked]
        if QMessageBox.question(self, "Region folders", f"Move {len(todo)} games into their region folders? "
                                "(An undo log is kept.)") != QMessageBox.Yes:
            return
        done, problems = [], []
        for _con, a, b in todo:
            if b.exists():
                problems.append(f"in the way, left alone: {b}")
                continue
            try:
                b.parent.mkdir(parents=True, exist_ok=True)
                os.rename(a, b)                 # never over a file that is there
                done.append({"op": "moved", "from": str(a), "to": str(b)})
            except OSError as e:
                problems.append(f"{a.name}: {e}")
        log = write_undo(UNDO, "region folders", done)
        QMessageBox.information(self, "Region folders", f"{len(done)} moved.\nUndo log: {log.name}" +
                                ("\n\n" + "\n".join(problems[:30]) if problems else ""))
        self.accept()
        self.main.scan()


class TidyDialog(QDialog):
    """The non-original sort: what isn't an original game to <games>\\_Non-original, dumps in another form fixed into
    their region folder, games alone in an archive taken out - a preview first, nothing deleted."""

    @staticmethod
    def plan(main, results, progress=lambda text: None) -> list:
        """[(console name, step)] - slow for big unknown dumps (every repaired form is tried): the main window runs
        it in the background, then opens the dialog with it."""
        s = main.s
        tools = Tools(s.sevenzip, s.winrar_dir, s.dolphin_tool)
        out = []
        for r in results:
            progress(f"Non-originals: {r.console.name}…")
            for st in tidy_plan(r, Path(r.folders[0]), Path(s.games_root), main.dats.entries(r.console.dat), tools,
                                unpack=s.unpack):
                out.append((r.console.name, st))
        return out

    def __init__(self, main, results, steps=None):
        super().__init__(main)
        self.main = main
        s = main.s
        root = Path(s.games_root)
        self.tools = Tools(s.sevenzip, s.winrar_dir, s.dolphin_tool)
        self.steps = steps if steps is not None else self.plan(main, results)
        names = ", ".join(r.console.name for r in results) if len(results) <= 3 else f"{len(results)} consoles"
        self.setWindowTitle(f"Non-originals - {names}")
        self.resize(1400, 780)
        v = QVBoxLayout(self)
        note = QLabel("Translations, unverified dumps and unlicensed games go to "
                      f"{root / '_Non-original'}\\<maker>\\<console> (hidden in MediaDeck; translations get a clean "
                      "name). Dumps in another form (overdumped, trimmed, byte-swapped, interleaved, reversed bits) "
                      "become the real dump in their region folder; games alone in an archive are taken out; clean dumps "
                      "found in _Non-original come back. Old files and archives go to _Duplicates - nothing is deleted. "
                      "Prototypes go to the console's 0 - Prototypes tab (shown in MediaDeck); hacks, randomizers and "
                      "homebrew keep their tab."
                      if self.steps else "Nothing to sort: every game is an original in its place.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        v.addWidget(note)
        self.t = table(["", "Console", "File", "What", "Goes to", "Why"], {0: 30, 1: 160, 2: 420, 3: 110, 4: 420}, 5)
        self.t.setSortingEnabled(False)
        self.t.setRowCount(len(self.steps))
        for i, (con, st) in enumerate(self.steps):
            chk = Item("")
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Checked if st.enabled else Qt.Unchecked)
            chk.setData(Qt.UserRole, i)
            self.t.setItem(i, 0, chk)
            self.t.setItem(i, 1, Item(con))
            self.t.setItem(i, 2, Item(Path(st.source).name))
            self.t.setItem(i, 3, Item(st.what))
            goes = (str(Path(st.target).relative_to(root)) if st.target and Path(st.target).is_relative_to(root)
                    else st.target) or "_Duplicates (you have that game)"
            self.t.setItem(i, 4, Item(goes))
            self.t.setItem(i, 5, Item(st.why))
        self.t.setSortingEnabled(True)
        v.addWidget(self.t, 1)
        row = QHBoxLayout()
        row.addStretch()
        b = QPushButton("Sort")
        b.setObjectName("primary")
        b.setEnabled(bool(self.steps))
        b.clicked.connect(self.apply)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(b)
        row.addWidget(close)
        v.addLayout(row)

    def apply(self) -> None:
        for r in range(self.t.rowCount()):
            self.steps[self.t.item(r, 0).data(Qt.UserRole)][1].enabled = self.t.item(r, 0).checkState() == Qt.Checked
        todo = [st for _c, st in self.steps if st.enabled]
        if QMessageBox.question(self, "Non-originals", f"Sort {len(todo)} files? (Nothing is deleted; an undo log is "
                                "kept.)") != QMessageBox.Yes:
            return
        n, problems, log = apply_tidy(todo, Path(self.main.s.games_root), self.tools, UNDO)
        QMessageBox.information(self, "Non-originals", f"{n} done.\nUndo log: {log.name}" +
                                ("\n\n" + "\n".join(problems[:30]) if problems else ""))
        self.accept()
        self.main.scan()


class UndoDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Undo")
        self.resize(700, 420)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Sorts and renames, newest first. Undo puts the files back where they were "
                           "(copies taken out of archives go to the Recycle Bin, or to undo\\removed copies on a drive without one)."))
        self.list = QListWidget()
        logs = sorted([p for p in UNDO.glob("*.json") if not p.name.endswith(".undone.json")], reverse=True) \
            if UNDO.is_dir() else []
        import json  # noqa: PLC0415
        for p in logs:
            try:
                n = len(json.loads(p.read_text(encoding="utf-8"))["steps"])
            except (OSError, ValueError, KeyError):
                n = 0
            it = QListWidgetItem(f"{p.stem}   ({n} files)")
            it.setData(Qt.UserRole, str(p))
            self.list.addItem(it)
        v.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addStretch()
        b = QPushButton("Undo selected")
        b.setObjectName("primary")
        b.clicked.connect(self.do_undo)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(b)
        row.addWidget(close)
        v.addLayout(row)

    def do_undo(self) -> None:
        it = self.list.currentItem()
        if not it:
            return
        if QMessageBox.question(self, "Undo", f"Undo {it.text()}?") != QMessageBox.Yes:
            return
        n, problems = undo(Path(it.data(Qt.UserRole)))
        QMessageBox.information(self, "Undone", f"{n} steps undone." + ("\n\n" + "\n".join(problems[:30]) if problems else ""))
        self.list.takeItem(self.list.row(it))
        self.main.scan()
