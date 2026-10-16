"""Settings, Sort inbox, Clean names and Undo."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTabWidget, QTableWidget,
                               QToolButton, QVBoxLayout, QWidget)

from ..config import UNDO, Settings, save_settings
from ..core.collection import find_folders
from ..core.consoles import BY_KEY, CONSOLES
from ..core.dat import DatStore
from ..core.hashing import Tools
from ..core.inbox import HAD, apply_plan, make_console_folders, make_plan, tidy_inbox, undo
from ..core.rename import apply_renames, make_renames
from . import theme
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
        self.list = QListWidget()
        for c in CONSOLES:
            it = QListWidgetItem(f"{c.name}   →   {'; '.join(found.get(c.key, [])) or 'no folder found'}")
            it.setData(Qt.UserRole, c.key)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            on = (c.key in s.consoles) if s.consoles else (c.key in found)
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.list.addItem(it)
        self.list.itemDoubleClicked.connect(self._pick_folder)
        form.addRow("Consoles", self.list)
        form.addRow("", QLabel("Tick the consoles to check. Double-click one to choose its folder yourself."))

        page = QWidget()
        form = QFormLayout(page)
        tabs.addTab(page, "Sorting")
        self.unpack = QCheckBox("Take ROMs out of zip / 7z / rar (Nintendo DS games stay packed: emulators read .7z)")
        self.unpack.setChecked(s.unpack)
        self.names = QCheckBox("Give sorted games their No-Intro name")
        self.names.setChecked(s.official_names)
        self.hacks = QCheckBox("Also sort in hacks [h] (fan translations [T+] always are)")
        self.hacks.setChecked(s.take_hacks)
        self.unknown = QCheckBox("Sort in dumps No-Intro doesn't list (homebrew, multicarts, translations)")
        self.unknown.setChecked(s.take_unknown)
        for cb in (self.unpack, self.names, self.hacks, self.unknown):
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
        self.auto_update = QCheckBox("Look for a new version of the checker at start (once a day, asks before updating)")
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

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _pick_folder(self, it: QListWidgetItem) -> None:
        key = it.data(Qt.UserRole)
        d = QFileDialog.getExistingDirectory(self, f"Folder for {BY_KEY[key].name}", self.root.text())
        if d:
            self.s.folders[key] = [d]
            it.setText(f"{BY_KEY[key].name}   →   {d}")
            it.setCheckState(Qt.Checked)

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
        ticked = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                  if self.list.item(i).checkState() == Qt.Checked]
        found = find_folders(s.games_root, s.folders)
        s.consoles = [] if set(ticked) == set(found) else ticked
        s.unpack, s.official_names = self.unpack.isChecked(), self.names.isChecked()
        s.take_hacks, s.take_unknown = self.hacks.isChecked(), self.unknown.isChecked()
        s.sevenzip, s.winrar_dir = self.seven.text().strip(), self.winrar.text().strip()
        s.dolphin_tool = self.dolphin.text().strip()
        if s.quick_check != self.quick.isChecked():
            s.quick_check = self.quick.isChecked()
            self.rescan = True
        s.check_updates = self.auto_update.isChecked()
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
        mk.setToolTip("Empty maker\console folders (laid out like your collection). A game you put in one is sorted to "
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
                             progress=w.signals.progress.emit, stop=lambda: w.stop)

        def done(plan) -> None:
            self.plan = plan
            self.fill()
            self.b_apply.setEnabled(any(st.action == "move" for st in plan.steps) or any(st.reason in HAD for st in plan.steps))
        self.b_check.setEnabled(False)
        m.run(job, lambda p: (self.b_check.setEnabled(True), done(p)), "Checking the inbox…")

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
        m.run(job, done, "Moving…")


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


class UndoDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Undo")
        self.resize(700, 420)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Sorts and renames, newest first. Undo puts the files back where they were "
                           "(copies taken out of archives go to the Recycle Bin, or to undo\removed copies on a drive without one)."))
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
