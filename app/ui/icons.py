"""Small pictures: console logos (RetroArch's Systematic theme, CC BY 4.0 - assets/consoles/CREDITS.txt) and
flags before the regions (flag-icons, MIT - assets/flags)."""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from PySide6 import QtSvg  # noqa: F401 - the flags are SVG (and PyInstaller bundles the plugin)
from PySide6.QtGui import QIcon

from ..portable import app_dir

# Settings → Regions: a flag before each country (flag-icons, MIT - assets/flags); groups like World have none
FLAGS = {"USA": "us", "Canada": "ca", "Europe": "eu", "UK": "gb", "United Kingdom": "gb", "Germany": "de",
         "Austria": "at", "Switzerland": "ch", "France": "fr", "Belgium": "be", "Netherlands": "nl", "Spain": "es",
         "Portugal": "pt", "Italy": "it", "Greece": "gr", "Sweden": "se", "Denmark": "dk", "Norway": "no",
         "Finland": "fi", "Poland": "pl", "Russia": "ru", "Australia": "au", "New Zealand": "nz", "Japan": "jp",
         "Korea": "kr", "China": "cn", "Taiwan": "tw", "Hong Kong": "hk", "India": "in", "United Arab Emirates": "ae",
         "Brazil": "br", "Argentina": "ar", "Peru": "pe", "Mexico": "mx", "Turkey": "tr"}


def asset(*parts: str) -> Path:
    return Path(getattr(sys, "_MEIPASS", None) or app_dir(), "assets", *parts)


def flag_icon(region: str) -> QIcon | None:
    f = asset("flags", f"{FLAGS[region]}.svg") if region in FLAGS else None
    return QIcon(str(f)) if f and f.is_file() else None


@lru_cache(maxsize=None)
def console_icon(key: str) -> QIcon | None:
    """A console's logo (RetroArch's Systematic theme, CC BY 4.0 - assets/consoles/CREDITS.txt)."""
    f = asset("consoles", f"{key}.png")
    return QIcon(str(f)) if f.is_file() else None
