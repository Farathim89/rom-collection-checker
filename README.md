# ROM Collection Checker

See what you have, what's missing and what's a mess in your retro game collection - a small Windows app,
the sister of [Book Collection Checker](https://github.com/Farathim89/book-collection-checker).

**Work in progress** - expect rough edges and changes.

## What it does
- **Have / missing per console** - every game file is matched against [No-Intro](https://no-intro.org)'s list of
  known dumps **by its content** (CRC), so badly named files, copier headers (SNES, PC Engine, Mega Drive â€¦), iNES
  headers, `.smd` interleave and N64 byte order don't matter. Zip, 7z and rar are read inside.
  - Regions: the picker has USA / Europe / Asia / Other (World releases always count); Settings → Regions lists every
    region of your lists (Germany, Canada, Korea …) under its group, each its own tick. Any version counts as having it.
  - Count only official games, or also unlicensed, prototypes, demos, homebrew â€¦
  - Box art (libretro thumbnails), every No-Intro version of a game with the ones you have ticked.
  - **Copy / save the missing list.**
- **Sort an inbox folder** (new downloads, packs): each game is recognised by its checksum (else its file type),
  compared with what you have and gets a plan - *move it in*, or leave it (*you have it*, *bad dump*, *hack*,
  *unknown console*). ROMs are taken out of their archives (not Nintendo DS: emulators read `.7z`) and can get their
  No-Intro name. Nothing moves until you press Apply; every sort can be undone. Afterwards the inbox is tidied: what's now in
  your collection goes to `_Duplicates\<inbox name>`, empty folders go.
  - Packs are read by their folder names too (`Atari 2600\roms`, `Sega 32X`), arcade sets (MAME, Neo Geo) are set
    aside, and other dumps of a game you already have stay in the inbox.
  - *Make console folders* adds empty `Maker\Console` folders to the inbox, laid out like your collection: a game you
    drop in one is sorted to that console.
- **Downloads** - paste links (http / https) and they download into the inbox, a few at a time, with progress,
  pause / resume / retry; unfinished ones go on after a restart, and a finished file never overwrites one that is
  there. Optionally the inbox is sorted when all are done. It downloads the links you give it - it doesn't search.
- **Clean names** - known dumps get their official No-Intro name (`smb.nes` â†’ `Super Mario Bros. (World).nes`),
  with a preview and undo.
- **Free homebrew** - browse [Homebrew Hub](https://hh.gbdev.io)'s ~1,600 free games, demos and music for Game Boy,
  Game Boy Color, Game Boy Advance and NES (shared by their authors), with screenshots and licenses, and download them
  into `<console folder>\0 - Homebrew`.
- **A second check** - what No-Intro doesn't list is looked up in TOSEC and libretro's hacks and homebrew lists
  (Settings â†’ Tools & lists): known translations, hacks and releases aren't problems, Problems shows what each one
  is, and Clean names gives them their TOSEC name.
- **Problems** - duplicates, files No-Intro doesn't know (bad dumps, hacks, translations), games in the wrong console
  folder, unreadable archives.

A `0 - Name` folder in a console folder (`0 - Homebrew`, `0 - Translations`, `0 - Randomizer`) is a collection of
its own: games No-Intro doesn't know are expected there, so they are not listed as problems
([MediaDeck](https://github.com/Farathim89/MediaDeck) shows each as a tab).

Nothing is ever deleted for good: what you remove goes to the Recycle Bin. On a drive whose Recycle Bin is off the
checker refuses, and Undo parks copies in `RomChecker-data\undo\removed copies` instead.

Console folders are found by themselves in your games folder (also inside maker folders like `Nintendo\SNES`), or
chosen per console in Settings. Cartridge consoles: NES, Famicom Disk System, SNES, N64, Game Boy / Color / Advance,
Nintendo DS, Virtual Boy, Master System, Game Gear, SG-1000, Mega Drive, 32X, PC Engine, Atari 2600 / 5200 / 7800 /
Lynx, WonderSwan / Color, Neo Geo Pocket / Color, ColecoVision, MSX / MSX2.

**Nintendo Switch**: games are named by the title ID in their file names (the files are encrypted) and counted against
[titledb](https://github.com/blawar/titledb) (US eShop). Updates and DLC are shown with their game, and Problems →
*Switch updates* lists the games whose update is older than the newest.

**Disc consoles** are checked against [Redump](http://redump.org): PlayStation 1 / 2 / 3, PSP, GameCube, Wii, Saturn,
Mega-CD, Dreamcast, Xbox / 360, CD-i, PC Engine CD, PC-FX, 3DO, Amiga CD32 and CDTV.
- `.iso` and `.bin` tracks by checksum; one `.bin` with all tracks merged is checked by its first (data) track.
- `.rvz` / `.wbfs` / `.gcz` through Dolphin's `DolphinTool.exe` (found by itself, or set in Settings).
- `.cdi`, `.pbp`, `.cso`, `.chd` and repacked Xbox images can only be matched by name - shown as *by name*.
- **Quick check** (on by default): discs, DS and N64 games are named by their header - the serial and revision, a few KB per file - so a scan takes seconds; they are marked ◇ *identified*. **Verify** on a console page does the full checksum check. `.7z` archives are checked from their own index, without unpacking. PS3 game folders are named by their `PARAM.SFO`.
- Sort inbox moves a disc of several files (`.cue` / `.gdi` + tracks) as one into `<console>\<Redump name>\`.

## Download
Grab the latest [release](https://github.com/Farathim89/rom-collection-checker/releases):
- **ROMCollectionChecker-portable.zip** - unzip anywhere and run `ROMCollectionChecker.exe`; settings, the No-Intro
  lists, the scan cache and the undo logs stay in the `RomChecker-data` folder next to it.

No install, no Python. Windows 10/11. SmartScreen may warn because the exe isn't code-signed: *More info â†’ Run anyway*.

The No-Intro lists (a few MB of text) come from [libretro-database](https://github.com/libretro/libretro-database);
the app asks before downloading them. For `.7z` it uses 7-Zip (or WinRAR), for `.rar` WinRAR - found by themselves.

The checker updates itself: at every start it asks GitHub for a newer version and asks before updating;
`ROMCollectionChecker.exe --update` updates straight away.

## From source
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python checker.pyw
```
`build.cmd` makes the exe (PyInstaller).

## License
[MIT](LICENSE) Â© Farathim
