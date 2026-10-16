"""Portable mode: a 'RomChecker-data' folder next to the exe keeps the settings, the No-Intro lists, the scan
cache, the undo logs and the window layout in there instead of the user profile and the registry."""
from __future__ import annotations

import sys
from pathlib import Path

DATA_FOLDER = "RomChecker-data"


def app_dir() -> Path:
    """The folder of ROMCollectionChecker.exe (frozen) or of the project (run from source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def portable_dir(base: Path | None = None) -> Path | None:
    d = (base or app_dir()) / DATA_FOLDER
    return d if d.is_dir() else None


PORTABLE = portable_dir()


def ui_settings():
    """Window size, column widths, filters: an .ini in the portable folder, else Qt's default (registry)."""
    from PySide6.QtCore import QSettings  # noqa: PLC0415
    if PORTABLE is not None:
        return QSettings(str(PORTABLE / "window.ini"), QSettings.IniFormat)
    return QSettings("ROMCollectionChecker", "ROMCollectionChecker")
