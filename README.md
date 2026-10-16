# ROM Collection Checker

See what you have, what's missing and what's a mess in your retro game collection - a small Windows app,
the sister of [Book Collection Checker](https://github.com/Farathim89/book-collection-checker).

**Work in progress** - expect rough edges and changes.

## What it does
- **Have / missing per console** - every game file is matched against [No-Intro](https://no-intro.org)'s list of
  known dumps **by its content** (CRC), so badly named files, copier headers (SNES, PC Engine, Mega Drive …), iNES
  headers, `.smd` interleave and N64 byte order don't matter. Zip, 7z and rar are read inside.
  - Region filter: USA / Europe / Japan / Other / All - any version of a game counts as having it.
  - Count only official games, or also unlicensed, prototypes, demos, homebrew …
  - Box art (libretro thumbnails), every No-Intro version of a game with the ones you have ticked.
  - **Copy / save the missing list.**
- **Sort an inbox folder** (new downloads, packs): each game is recognised by its checksum (else its file type),
  compared with what you have and gets a plan - *move it in*, or leave it (*you have it*, *bad dump*, *hack*,
  *unknown console*). ROMs are taken out of their archives (not Nintendo DS: emulators read `.7z`) and can get their
  No-Intro name. Nothing moves until you press Apply; every sort can be undone.
- **Clean names** - known dumps get their official No-Intro name (`smb.nes` → `Super Mario Bros. (World).nes`),
  with a preview and undo.
- **Problems** - duplicates, files No-Intro doesn't know (bad dumps, hacks, translations), games in the wrong console
  folder, unreadable archives.

Console folders are found by themselves in your games folder (also inside maker folders like `Nintendo\SNES`), or
chosen per console in Settings. Cartridge consoles: NES, Famicom Disk System, SNES, N64, Game Boy / Color / Advance,
Nintendo DS, Virtual Boy, Master System, Game Gear, SG-1000, Mega Drive, 32X, PC Engine, Atari 2600 / 5200 / 7800 /
Lynx, WonderSwan / Color, Neo Geo Pocket / Color, ColecoVision, MSX / MSX2. Disc consoles (Redump) are planned.

## Download
Grab the latest [release](https://github.com/Farathim89/rom-collection-checker/releases):
- **ROMCollectionChecker-portable.zip** - unzip anywhere and run `ROMCollectionChecker.exe`; settings, the No-Intro
  lists, the scan cache and the undo logs stay in the `RomChecker-data` folder next to it.

No install, no Python. Windows 10/11. SmartScreen may warn because the exe isn't code-signed: *More info → Run anyway*.

The No-Intro lists (a few MB of text) come from [libretro-database](https://github.com/libretro/libretro-database);
the app asks before downloading them. For `.7z` it uses 7-Zip (or WinRAR), for `.rar` WinRAR - found by themselves.

## From source
```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python checker.pyw
```
`build.cmd` makes the exe (PyInstaller).

## License
[MIT](LICENSE) © Farathim
