"""The consoles the checker knows: their No-Intro (cartridges) or Redump (discs) list, the file types their games come in, and the folder
names they are usually kept under (searched for in the games folder, also inside maker folders)."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Console:
    key: str
    name: str            # shown in the app
    maker: str
    dat: str             # No-Intro list name (libretro-database metadat/no-intro/<dat>.dat)
    exts: tuple[str, ...]
    folders: tuple[str, ...] = field(default_factory=tuple)   # usual folder names (first = created when sorting)
    keep_archives: bool = False   # games that stay packed when sorted (big DS cartridges: emulators read .7z)
    source: str = "no-intro"      # no-intro (cartridges) / redump (discs)
    name_only: tuple[str, ...] = ()   # file types that aren't the original disc layout: matched by name only
    name_fallback: tuple[str, ...] = ()   # checked by checksum, but often trimmed (.wbfs): else matched by name


CONSOLES: tuple[Console, ...] = (
    Console("nes", "NES / Famicom", "Nintendo", "Nintendo - Nintendo Entertainment System", ("nes", "unf", "unif"),
            ("Nintendo 8-Bit", "NES", "Nintendo Entertainment System", "Famicom")),
    Console("fds", "Famicom Disk System", "Nintendo", "Nintendo - Family Computer Disk System", ("fds",),
            ("Famicom Disk System", "FDS")),
    Console("snes", "SNES / Super Famicom", "Nintendo", "Nintendo - Super Nintendo Entertainment System",
            ("sfc", "smc", "fig", "swc"), ("Nintendo Snes", "SNES", "Super Nintendo", "Super Famicom")),
    Console("n64", "Nintendo 64", "Nintendo", "Nintendo - Nintendo 64", ("z64", "n64", "v64"),
            ("Nintendo 64", "N64")),
    Console("gb", "Game Boy", "Nintendo", "Nintendo - Game Boy", ("gb",), ("GameBoy", "Game Boy", "GB")),
    Console("gbc", "Game Boy Color", "Nintendo", "Nintendo - Game Boy Color", ("gbc",),
            ("GameBoy Color", "Game Boy Color", "GBC")),
    Console("gba", "Game Boy Advance", "Nintendo", "Nintendo - Game Boy Advance", ("gba",),
            ("GameBoy Advance", "Game Boy Advance", "GBA")),
    Console("nds", "Nintendo DS", "Nintendo", "Nintendo - Nintendo DS", ("nds",),
            ("Nintendo Ds", "Nintendo DS", "NDS", "DS")),
    Console("vb", "Virtual Boy", "Nintendo", "Nintendo - Virtual Boy", ("vb", "vboy"),
            ("Nintendo Virtual Boy", "Virtual Boy")),
    Console("sms", "Master System", "Sega", "Sega - Master System - Mark III", ("sms",),
            ("Sega Master System", "Master System", "SMS")),
    Console("gg", "Game Gear", "Sega", "Sega - Game Gear", ("gg",), ("Sega Game Gear", "Game Gear", "GG")),
    Console("sg1000", "SG-1000", "Sega", "Sega - SG-1000", ("sg",), ("Sega SG-1000", "SG-1000")),
    Console("md", "Mega Drive / Genesis", "Sega", "Sega - Mega Drive - Genesis", ("md", "gen", "smd", "bin"),
            ("Sega Mega Drive (Sega Genesis)", "Mega Drive", "Genesis", "Sega Genesis")),
    Console("32x", "32X", "Sega", "Sega - 32X", ("32x", "bin"), ("Sega 32X", "32X")),
    Console("pce", "PC Engine / TurboGrafx-16", "NEC", "NEC - PC Engine - TurboGrafx 16", ("pce",),
            ("TurboGrafx-16 (PC Engine)", "TurboGrafx-16", "PC Engine", "PCE")),
    Console("a2600", "Atari 2600", "Atari", "Atari - 2600", ("a26", "bin"), ("Atari 2600", "2600")),
    Console("a5200", "Atari 5200", "Atari", "Atari - 5200", ("a52", "bin"), ("Atari 5200", "5200")),
    Console("a7800", "Atari 7800", "Atari", "Atari - 7800", ("a78", "bin"), ("Atari 7800", "7800")),
    Console("lynx", "Atari Lynx", "Atari", "Atari - Lynx", ("lnx", "lyx"), ("Atari Lynx", "Lynx")),
    Console("ws", "WonderSwan", "Bandai", "Bandai - WonderSwan", ("ws",), ("Bandai WonderSwan", "WonderSwan")),
    Console("wsc", "WonderSwan Color", "Bandai", "Bandai - WonderSwan Color", ("wsc",),
            ("Bandai WonderSwan Color", "WonderSwan Color")),
    Console("ngp", "Neo Geo Pocket", "SNK", "SNK - Neo Geo Pocket", ("ngp",), ("SNK Neo Geo Pocket", "Neo Geo Pocket")),
    Console("ngpc", "Neo Geo Pocket Color", "SNK", "SNK - Neo Geo Pocket Color", ("ngc", "ngpc"),
            ("SNK Neo Geo Pocket Color", "Neo Geo Pocket Color")),
    Console("coleco", "ColecoVision", "Coleco", "Coleco - ColecoVision", ("col",), ("ColecoVision",)),
    Console("msx", "MSX", "Microsoft", "Microsoft - MSX", ("rom", "mx1"), ("MSX",)),
    Console("msx2", "MSX2", "Microsoft", "Microsoft - MSX2", ("rom", "mx2"), ("MSX2",)),
    # discs (Redump): data tracks (.bin) and images (.iso) are checked by checksum; .rvz / .wbfs / .gcz through
    # DolphinTool; .cdi, .pbp, .cso, .chd and repacked Xbox images can only be matched by name
    Console("ps1", "PlayStation", "Sony", "Sony - PlayStation", ("bin", "img", "iso", "pbp", "chd"),
            ("Playstation", "PlayStation", "PlayStation 1", "PS1", "PSX"), source="redump", name_only=("pbp", "chd")),
    Console("ps2", "PlayStation 2", "Sony", "Sony - PlayStation 2", ("iso", "bin", "chd"),
            ("PlayStation 2", "PS2"), source="redump", name_only=("chd",)),
    Console("ps3", "PlayStation 3", "Sony", "Sony - PlayStation 3", ("iso", "sfo"), ("PlayStation 3", "PS3"), source="redump",
            name_only=("sfo",)),   # game folders: named by PS3_GAME\PARAM.SFO
    Console("psp", "PSP", "Sony", "Sony - PlayStation Portable", ("iso", "cso", "chd"),
            ("PlayStation Portable", "PSP"), source="redump", name_only=("cso", "chd")),
    Console("gc", "GameCube", "Nintendo", "Nintendo - GameCube", ("iso", "gcm", "rvz", "gcz", "ciso"),
            ("Nintendo GameCube", "GameCube", "NGC"), source="redump", name_only=("ciso",)),
    Console("wii", "Wii", "Nintendo", "Nintendo - Wii", ("iso", "wbfs", "rvz", "gcz"), ("Nintendo WII", "Nintendo Wii", "Wii"),
            source="redump", name_fallback=("wbfs",)),
    # Switch: the files are encrypted - named by the title ID in their names; the list comes from titledb
    Console("switch", "Nintendo Switch", "Nintendo", "Nintendo - Switch", ("nsp", "nsz", "xci", "xcz", "rar"),
            ("Nintendo Switch", "Switch"), source="titledb", name_only=("nsp", "nsz", "xci", "xcz", "rar")),
    Console("saturn", "Saturn", "Sega", "Sega - Saturn", ("bin", "iso", "chd"), ("Sega Saturn", "Saturn"),
            source="redump", name_only=("chd",)),
    Console("segacd", "Mega-CD / Sega CD", "Sega", "Sega - Mega-CD - Sega CD", ("bin", "iso", "chd"),
            ("SEGA CD", "Sega CD", "Mega-CD", "Mega CD"), source="redump", name_only=("chd",)),
    Console("dc", "Dreamcast", "Sega", "Sega - Dreamcast", ("bin", "raw", "cdi", "chd"), ("Sega Dreamcast", "Dreamcast"),
            source="redump", name_only=("cdi", "chd")),
    Console("xbox", "Xbox", "Microsoft", "Microsoft - Xbox", ("iso",), ("Xbox",), source="redump", name_only=("iso",)),
    Console("x360", "Xbox 360", "Microsoft", "Microsoft - Xbox 360", ("iso",), ("Xbox 360",), source="redump"),
    Console("cdi", "CD-i", "Philips", "Philips - CD-i", ("bin", "iso", "chd"), ("Philips cdi", "Philips CD-i", "CD-i"),
            source="redump", name_only=("chd",)),
    Console("pcecd", "PC Engine CD", "NEC", "NEC - PC Engine CD - TurboGrafx-CD", ("bin", "iso", "chd"),
            ("PC Engine CD", "TurboGrafx-CD", "TurboGrafx CD"), source="redump", name_only=("chd",)),
    Console("pcfx", "PC-FX", "NEC", "NEC - PC-FX", ("bin", "chd"), ("NEC PC-FX", "PC-FX"), source="redump",
            name_only=("chd",)),
    Console("cd32", "Amiga CD32", "Commodore", "Commodore - CD32", ("bin", "iso", "chd"),
            ("Amiga CD32", "Commodore Amiga CD32", "CD32"), source="redump", name_only=("chd",)),
    Console("cdtv", "CDTV", "Commodore", "Commodore - CDTV", ("bin", "iso", "chd"), ("Commodore CDTV", "CDTV"),
            source="redump", name_only=("chd",)),
    Console("3do", "3DO", "The 3DO Company", "The 3DO Company - 3DO", ("bin", "iso", "chd"), ("3DO",), source="redump",
            name_only=("chd",)),
)
BY_KEY = {c.key: c for c in CONSOLES}
ARCHIVES = ("zip", "7z", "rar")


def consoles_for_ext(ext: str) -> list[Console]:
    ext = ext.lower().lstrip(".")
    return [c for c in CONSOLES if ext in c.exts]
