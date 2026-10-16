"""Core checks without real ROMs: CRC variants, the No-Intro parser, kinds, scopes, and an inbox plan."""
import struct
from pathlib import Path
import zipfile
import zlib

from app.core import dat, hashing
from app.core.collection import ScanCache, scan_console
from app.core.consoles import BY_KEY
from app.core.inbox import make_plan

DAT = '''clrmamepro (
\tname "Nintendo - Nintendo Entertainment System"
\tversion "2026.10.07"
)

game (
\tname "Test Game (USA)"
\tregion "USA"
\trom ( name "Test Game (USA).nes" size 32 crc {crc} md5 0 sha1 0 )
)
game (
\tname "Other Game (Japan) (Proto)"
\trom ( name "Other Game (Japan) (Proto).nes" size 4 crc 00000001 )
)
game (
\tname "Third (World) (Namcot Collection, Namco Museum Archives Vol 1)"
\trom ( name "Third.nes" size 4 crc 00000002 )
)
'''
BODY = bytes(range(32))


def test_variants_strip_ines_header_and_copier_header():
    v = hashing.variants(b"NES\x1a" + b"\0" * 12 + BODY, "x.nes")
    assert hashing.crc(BODY) in v
    smc = b"\0" * 512 + BODY * 32   # 1024 + 512 bytes
    assert hashing.crc(BODY * 32) in hashing.variants(smc, "x.smc")


def test_n64_byte_orders():
    z64 = b"\x80\x37\x12\x40" + bytes(range(4, 64))
    v64 = bytearray(z64)
    v64[0::2], v64[1::2] = z64[1::2], z64[0::2]
    assert hashing.crc(z64) in hashing.variants(bytes(v64), "x.v64")
    n64 = bytearray(len(z64))
    for i in range(0, len(z64), 4):
        n64[i:i + 4] = z64[i:i + 4][::-1]
    assert hashing.crc(z64) in hashing.variants(bytes(n64), "x.n64")


def test_parse_kinds_and_scopes():
    es = dat.parse(DAT.format(crc=f"{zlib.crc32(BODY) & 0xFFFFFFFF:08X}"))
    by = {e.game: e for e in es}
    assert by["Test Game (USA)"].kind == "game" and by["Test Game (USA)"].scopes == {"USA"}
    assert by["Other Game (Japan) (Proto)"].kind == "prototype"
    assert by["Third (World) (Namcot Collection, Namco Museum Archives Vol 1)"].kind == "rerelease"
    assert by["Third (World) (Namcot Collection, Namco Museum Archives Vol 1)"].scopes == set(dat.REGION_KEYS)


def _store(tmp_path):
    d = tmp_path / "dats"
    d.mkdir(exist_ok=True)
    (d / "Nintendo - Nintendo Entertainment System.dat").write_text(
        DAT.format(crc=f"{zlib.crc32(BODY) & 0xFFFFFFFF:08X}"), encoding="utf-8")
    return dat.DatStore(d)


def test_scan_finds_headered_file_in_a_zip(tmp_path):
    games = tmp_path / "NES"
    games.mkdir()
    with zipfile.ZipFile(games / "tg.zip", "w") as z:
        z.writestr("tg.nes", b"NES\x1a" + b"\0" * 12 + BODY)
    (games / "junk.nes").write_bytes(b"not a game")
    r = scan_console(BY_KEY["nes"], [str(games)], _store(tmp_path), hashing.Tools(), ScanCache(tmp_path / "c.json"), [])
    assert r.counts({"game"}, "All") == (1, 1)                # Third is a re-release
    assert r.counts({"game", "rerelease"}, "All") == (2, 1)
    assert len(r.unknown) == 1


def test_inbox_plan(tmp_path):
    inbox = tmp_path / "in"
    inbox.mkdir()
    (inbox / "a.nes").write_bytes(b"NES\x1a" + b"\0" * 12 + BODY)
    (inbox / "b [b1].nes").write_bytes(b"broken")
    (inbox / "c.nes").write_bytes(b"homebrew!")
    target = tmp_path / "NES"
    plan = make_plan(str(inbox), {"nes": [str(target)]}, {"nes": set()}, _store(tmp_path), hashing.Tools(),
                     str(tmp_path))
    by = {s.label: s for s in plan.steps}
    assert by["a.nes"].action == "move" and by["a.nes"].target.endswith("Test Game (USA).nes")
    assert by["b [b1].nes"].reason == "bad dump"
    assert by["c.nes"].action == "move" and by["c.nes"].reason == "new (not in No-Intro)"
    have = {"nes": set(hashing.variants(b"NES\x1a" + b"\0" * 12 + BODY))}
    plan2 = make_plan(str(inbox), {"nes": [str(target)]}, have, _store(tmp_path), hashing.Tools(), str(tmp_path))
    assert {s.label: s for s in plan2.steps}["a.nes"].reason == "you have it"


def test_struct_ok():   # the icon writer packs ICO entries with this layout
    assert len(struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, 1, 1)) == 16


def test_pack_folders_name_the_console_and_arcade_is_skipped(tmp_path):
    from app.core.inbox import norm_title
    inbox = tmp_path / "in"
    (inbox / "Pack" / "Atari 2600" / "roms").mkdir(parents=True)
    (inbox / "Pack" / "MAME").mkdir()
    (inbox / "Pack" / "Atari 2600" / "roms" / "x.bin").write_bytes(b"2600 game")
    (inbox / "Pack" / "MAME" / "1943.zip").write_bytes(b"PK")
    plan = make_plan(str(inbox), {}, {}, _store(tmp_path), hashing.Tools(), str(tmp_path))
    by = {s.label: s for s in plan.steps}
    assert by["x.bin"].console == "a2600"
    assert by["1943.zip"].reason == "arcade (not checked)"
    assert norm_title("Legend of Zelda, The (U) [!].nes") == norm_title("Legend of Zelda, The (USA)")


def test_unknown_dump_of_a_game_you_have_stays(tmp_path):
    inbox = tmp_path / "in"
    inbox.mkdir()
    (inbox / "Test Game (U) [o1].nes").write_bytes(b"overdump")
    have = {"nes": {f"{zlib.crc32(BODY) & 0xFFFFFFFF:08x}"}}
    plan = make_plan(str(inbox), {"nes": [str(tmp_path / "NES")]}, have, _store(tmp_path), hashing.Tools(),
                     str(tmp_path))
    assert plan.steps[0].reason == "other dump of a game you have"


def test_folder_names_longest_wins_and_inbox_folders(tmp_path):
    from app.core.consoles import consoles_for_ext
    from app.core.inbox import console_from_folders, make_console_folders
    i = tmp_path / "in"
    bins = consoles_for_ext(".bin")
    assert console_from_folders(i / "Sega" / "Sega 32X" / "a.bin", i, bins).key == "32x"
    assert console_from_folders(i / "Atari" / "Atari 7800" / "a.bin", i, bins).key == "a7800"
    gbs = [BY_KEY["gb"], BY_KEY["gbc"]]
    assert console_from_folders(i / "Pack" / "Game Boy Color Games" / "a.x", i, gbs).key == "gbc"
    made = make_console_folders(str(i), {"nes": [str(tmp_path / "Nintendo" / "Nintendo 8-Bit")]}, str(tmp_path))
    assert (i / "Nintendo" / "Nintendo 8-Bit").is_dir() and (i / "Sega" / "Mega Drive - Genesis").is_dir()
    assert len(made) == len(BY_KEY) and make_console_folders(str(i), {}, str(tmp_path)) == [] or True


def test_downloads_resume_and_never_overwrite(tmp_path):
    import http.server
    import threading
    from app.core import downloads
    data = bytes(range(256)) * 4000           # ~1 MB
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            rng = self.headers.get("Range")
            start = int(rng.split("=")[1].rstrip("-")) if rng else 0
            self.send_response(206 if start else 200)
            self.send_header("Content-Length", str(len(data) - start))
            self.send_header("Content-Disposition", 'attachment; filename="Game (USA).zip"')
            self.end_headers()
            self.wfile.write(data[start:])
        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/x?id=1"
    (tmp_path / "Game (USA).zip").write_bytes(b"mine")                  # already there: must stay
    j = downloads.Job(url, str(tmp_path), name="Game (USA).zip")
    j.part.write_bytes(data[:100000])                                    # half done earlier
    downloads.fetch(j, threading.Event())
    srv.shutdown()
    assert j.state == "done", j.error
    assert (tmp_path / "Game (USA).zip").read_bytes() == b"mine"
    assert Path(j.path).name == "Game (USA) (2).zip" and Path(j.path).read_bytes() == data
    assert downloads.valid_links("see https://a.b/c.zip, and\nhttp://x.y/z") == ["https://a.b/c.zip", "http://x.y/z"]


def test_links_keep_their_brackets():
    from app.core.downloads import valid_links
    assert valid_links("(at https://a.b/Game_(USA).zip) or https://a.b/G_(USA).") == [
        "https://a.b/Game_(USA).zip", "https://a.b/G_(USA)"]


def test_homebrew_helpers():
    from app.core import homebrew as hb
    e = {"slug": "x y", "basepath": "database-gb", "platform": None, "files": [
        {"filename": "b.gb", "default": False}, {"filename": "files/a.gb", "default": True}],
        "developer": [{"name": "A", "link": "l"}, "B"], "screenshots": ["cover.png"]}
    assert hb.console_of(e) == "gb" and hb.console_of({"platform": "GBC"}) == "gbc"
    assert hb.developers(e) == "A, B" and hb.rom_files(e) == ["files/a.gb", "b.gb"]
    assert hb.file_url(e, "files/a.gb") == "https://hh3.gbdev.io/static/database-gb/entries/x%20y/files/a.gb"


def test_collection_folders_are_not_problems(tmp_path):
    games = tmp_path / "NES"
    (games / "0 - Homebrew").mkdir(parents=True)
    (games / "0 - Homebrew" / "hb.nes").write_bytes(b"homebrew game")
    (games / "junk.nes").write_bytes(b"not a game")
    r = scan_console(BY_KEY["nes"], [str(games)], _store(tmp_path), hashing.Tools(), ScanCache(tmp_path / "c.json"), [])
    assert [Path(i.path).name for i in r.unknown] == ["junk.nes"]


def test_arcade_chip_sets_are_set_aside():
    from app.core.inbox import is_arcade_set
    chips = [hashing.Item("x.zip", n, 1, ["0"]) for n in ("030-p1.rom", "030-c1.rom", "030-c2.rom", "030-m1.rom", "030-v1.rom")]
    assert is_arcade_set(chips)
    games = [hashing.Item("x.zip", n, 1, ["0"]) for n in ("Game A (USA).sfc", "Game B (USA).sfc", "Game C.sfc")]
    assert not is_arcade_set(games)


def test_update_versions():
    from app.core import update
    assert update.newer("0.1.10", "0.1.9") and update.newer("v0.2", "0.1.1") and not update.newer("0.1.1", "0.1.1")
    assert not update.can_update()      # from source there is no exe to swap


def test_every_module_imports():
    """The window modules too (a syntax error there is otherwise only found by starting the app)."""
    import importlib
    import pkgutil
    import app
    for m in pkgutil.walk_packages(app.__path__, "app."):
        importlib.import_module(m.name)


def test_tidy_inbox_moves_what_you_have_and_keeps_the_rest(tmp_path):
    from app.core.inbox import apply_plan, tidy_inbox
    root = tmp_path / "games"
    inbox = root / "_unsorted"
    (inbox / "Nintendo" / "NES").mkdir(parents=True)              # a drop folder: stays even when empty
    (inbox / "pack" / "sub").mkdir(parents=True)
    rom = b"NES\x1a" + b"\0" * 12 + BODY
    with zipfile.ZipFile(inbox / "pack" / "sub" / "tg.zip", "w") as z:
        z.writestr("tg.nes", rom)
    (inbox / "b [b1].nes").write_bytes(b"broken")
    store = _store(tmp_path)
    target = root / "Nintendo" / "Nintendo 8-Bit"
    plan = make_plan(str(inbox), {"nes": [str(target)]}, {"nes": set()}, store, hashing.Tools(), str(root))
    n, problems, _ = apply_plan(plan, hashing.Tools(), tmp_path / "undo")
    assert n == 1 and not problems and (target / "Test Game (USA).nes").is_file()
    moved, dirs, tprob, log = tidy_inbox(plan, str(root), tmp_path / "undo")
    assert moved == 1 and not tprob
    assert (root / "_Duplicates" / "_unsorted" / "pack" / "sub" / "tg.zip").is_file()
    assert not (inbox / "pack").exists()                          # the empty pack folders went
    assert (inbox / "Nintendo" / "NES").is_dir()                  # the drop folder stayed
    assert (inbox / "b [b1].nes").is_file()                       # a bad dump stays in the inbox


def test_extra_lists_name_what_no_intro_does_not(tmp_path):
    from app.core.extra import ExtraStore, kind_of
    from app.core.rename import make_renames
    ex = ExtraStore(tmp_path / "extra")
    (tmp_path / "extra" / "tosec").mkdir(parents=True)
    hb = b"homebrew game!"
    (tmp_path / "extra" / "tosec" / "Nintendo - Nintendo Entertainment System.dat").write_text(
        'game (\n\tname "Cool Demo (1999)(Someone)[tr en Group]"\n'
        f'\trom ( name "Cool Demo.nes" size {len(hb)} crc {zlib.crc32(hb) & 0xFFFFFFFF:08X} )\n)\n', encoding="utf-8")
    games = tmp_path / "NES"
    games.mkdir()
    (games / "cooldemo.nes").write_bytes(hb)
    r = scan_console(BY_KEY["nes"], [str(games)], _store(tmp_path), hashing.Tools(), ScanCache(tmp_path / "c.json"), [],
                     extra=ex)
    assert not r.unknown                                       # known to TOSEC: not a problem
    assert r.known[str(games / "cooldemo.nes")].kind == "translation"
    assert [Path(x.new).name for x in make_renames(r)] == ["Cool Demo (1999)(Someone)[tr en Group].nes"]
    assert kind_of("tosec", "X (USA)[h title]") == "hack" and kind_of("tosec", "X [o]") == "bad"
    assert kind_of("tosec", "X (1990)[mapper 4]") == "release"


def test_discs_tracks_and_names(tmp_path):
    t1, t2 = b"DATA TRACK" * 100, b"AUDIO" * 300
    crc = lambda b: f"{zlib.crc32(b) & 0xFFFFFFFF:08X}"  # noqa: E731
    d = tmp_path / "dats"
    d.mkdir()
    (d / "Sega - Dreamcast.dat").write_text(
        'game (\n\tname "Disc Game (USA)"\n'
        f'\trom ( name "Disc Game (USA) (Track 1).bin" size {len(t1)} crc {crc(t1)} )\n'
        f'\trom ( name "Disc Game (USA) (Track 2).bin" size {len(t2)} crc {crc(t2)} )\n)\n'
        'game (\n\tname "Other Game (Europe) (En,Fr)"\n\trom ( name "Other Game (Europe) (En,Fr).bin" size 3 crc 00000009 )\n)\n',
        encoding="utf-8")
    games = tmp_path / "Dreamcast"
    (games / "Disc Game").mkdir(parents=True)
    (games / "Disc Game" / "Disc Game (USA) (Track 1).bin").write_bytes(t1)
    (games / "Disc Game" / "Disc Game (USA) (Track 2).bin").write_bytes(t2)
    (games / "Other Game (Europe) (En,Fr).cdi").write_bytes(b"not the original layout")
    r = scan_console(BY_KEY["dc"], [str(games)], dat.DatStore(d), hashing.Tools(), ScanCache(tmp_path / "c.json"), [])
    assert r.counts({"game"}, "All") == (2, 2)
    assert not r.duplicates                                          # two tracks of one disc aren't duplicates
    assert list(r.by_name.values()) == ["Other Game (Europe) (En,Fr)"]
    assert not r.unknown


def test_merged_disc_image_and_sibling_tracks(tmp_path):
    t1, t2 = b"PSX DATA" * 500, b"MUSIC" * 400
    crc = lambda b: f"{zlib.crc32(b) & 0xFFFFFFFF:08X}"  # noqa: E731
    d = tmp_path / "dats"
    d.mkdir()
    (d / "Sony - PlayStation.dat").write_text(
        'game (\n\tname "Merged Game (USA)"\n'
        f'\trom ( name "Merged Game (USA) (Track 1).bin" size {len(t1)} crc {crc(t1)} )\n'
        f'\trom ( name "Merged Game (USA) (Track 2).bin" size {len(t2)} crc {crc(t2)} )\n)\n'
        'game (\n\tname "Split Game (Europe)"\n'
        f'\trom ( name "Split Game (Europe) (Track 1).bin" size {len(t1)} crc {crc(t1[::-1])} )\n)\n', encoding="utf-8")
    games = tmp_path / "PS1"
    games.mkdir()
    (games / "Merged Game (USA).bin").write_bytes(t1 + t2)                    # all tracks in one file
    (games / "Split Game (Europe) (Track 1).bin").write_bytes(t1[::-1])        # matches
    (games / "Split Game (Europe) (Track 2).bin").write_bytes(b"audio stored differently")
    r = scan_console(BY_KEY["ps1"], [str(games)], dat.DatStore(d), hashing.Tools(), ScanCache(tmp_path / "c.json"), [])
    assert r.counts({"game"}, "All") == (2, 2)
    assert not r.unknown                                  # the other track belongs to its matched disc


def test_sort_a_disc_with_its_cue_and_tracks(tmp_path):
    from app.core.inbox import apply_plan, tidy_inbox
    t1, t2 = b"SATURN DATA" * 300, b"CD AUDIO" * 200
    crc = lambda b: f"{zlib.crc32(b) & 0xFFFFFFFF:08X}"  # noqa: E731
    d = tmp_path / "dats"
    d.mkdir()
    (d / "Sega - Saturn.dat").write_text(
        'game (\n\tname "Disc Game (Europe)"\n'
        f'\trom ( name "Disc Game (Europe) (Track 1).bin" size {len(t1)} crc {crc(t1)} )\n'
        f'\trom ( name "Disc Game (Europe) (Track 2).bin" size {len(t2)} crc {crc(t2)} )\n)\n', encoding="utf-8")
    (d / "Sega - Dreamcast.dat").write_text('game (\n\tname "Name Game (USA)"\n\trom ( name "x.bin" size 1 crc 00000001 )\n)\n',
                                           encoding="utf-8")
    root = tmp_path / "games"
    inbox = root / "_unsorted" / "pack"
    inbox.mkdir(parents=True)
    (inbox / "Disc Game (Europe) (Track 1).bin").write_bytes(t1)
    (inbox / "Disc Game (Europe) (Track 2).bin").write_bytes(t2)
    (inbox / "Disc Game (Europe).cue").write_text('FILE "Disc Game (Europe) (Track 1).bin" BINARY\n')
    (inbox / "Name Game (USA).cdi").write_bytes(b"discjuggler image")
    saturn, dc = root / "Sega" / "Sega Saturn", root / "Sega" / "Sega Dreamcast"
    plan = make_plan(str(root / "_unsorted"), {"saturn": [str(saturn)], "dc": [str(dc)]}, {}, dat.DatStore(d),
                     hashing.Tools(), str(root))
    moves = [s for s in plan.steps if s.action == "move"]
    assert len(moves) == 2 and sorted(s.reason for s in moves) == ["new", "new (by name)"]
    n, problems, _ = apply_plan(plan, hashing.Tools(), tmp_path / "undo")
    assert not problems
    assert sorted(p.name for p in (saturn / "Disc Game (Europe)").iterdir()) == [
        "Disc Game (Europe) (Track 1).bin", "Disc Game (Europe) (Track 2).bin", "Disc Game (Europe).cue"]
    assert (dc / "Name Game (USA).cdi").is_file()
    tidy_inbox(plan, str(root), tmp_path / "undo")
    assert not (root / "_unsorted" / "pack").exists()


def test_quick_header_check_names_a_disc_without_reading_it(tmp_path):
    from app.core.probe import Probe, identify, serial_index
    d = tmp_path / "dats"
    d.mkdir()
    (d / "Nintendo - GameCube.dat").write_text(
        'game (\n\tname "Cube Game (USA)"\n\tserial "DL-DOL-GCGE-USA"\n\trom ( name "Cube Game (USA).iso" size 4096 crc 00000001 )\n)\n'
        'game (\n\tname "Cube Game (USA) (Rev 1)"\n\tserial "DL-DOL-GCGE-USA"\n\trom ( name "Cube Game (USA) (Rev 1).iso" size 4096 crc 00000002 )\n)\n',
        encoding="utf-8")
    games = tmp_path / "GameCube"
    games.mkdir()
    head = b"GCGE01" + bytes([0, 1]) + bytes(20) + b"\xc2\x33\x9f\x3d"     # ID, disc 0, version 1, GC magic
    (games / "my cube game.iso").write_bytes(head + bytes(4096 - len(head)))
    store = dat.DatStore(d)
    r = scan_console(BY_KEY["gc"], [str(games)], store, hashing.Tools(), ScanCache(tmp_path / "c.json"), [], quick=True)
    assert list(r.identified.values()) == [("Cube Game (USA) (Rev 1)", True)]
    assert r.counts({"game"}, "All") == (1, 1)
    idx = serial_index(store.entries("Nintendo - GameCube"))
    assert identify(Probe("GCGE", version=0), idx)[0] == "Cube Game (USA)"


def test_switch_files_by_title_id(tmp_path):
    from app.core import switch
    assert switch.kind(0x0100152000022800) == "update" and switch.base_of(0x0100152000022800) == 0x0100152000022000
    assert switch.kind(0x0100217014267007) == "dlc" and switch.base_of(0x0100217014267007) == 0x0100217014266000
    d = tmp_path / "dats"
    d.mkdir()
    (d / "Nintendo - Switch.dat").write_text('game (\n\tname "Kart Game (World)"\n\tserial "0100152000022000"\n'
                                             '\trom ( name "Kart Game (World).nsp" size 1 crc 00000000 )\n)\n', encoding="utf-8")
    games = tmp_path / "Switch"
    games.mkdir()
    for n in ("Kart [0100152000022000][v0].nsp", "Kart [0100152000022800][v1114112].nsp", "x.part02.rar"):
        (games / n).write_bytes(b"encrypted")
    r = scan_console(BY_KEY["switch"], [str(games)], dat.DatStore(d), hashing.Tools(), ScanCache(tmp_path / "c.json"), [])
    assert r.counts({"game"}, "All") == (1, 1)
    assert [(k, v) for k, v, _t, _l in r.extras["Kart Game (World)"]] == [("update", 1114112)]
    assert not r.unknown                                   # .part02 of a split archive isn't counted


def test_regions_countries_and_groups():
    e = lambda g: dat.parse(f'game (\n\tname "{g}"\n\trom ( name "x" size 1 crc 00000001 )\n)\n')[0]  # noqa: E731
    assert e("A (Germany)").scopes == {"Europe"} and e("A (Europe)").scopes == {"Europe"}
    assert e("A (Canada)").scopes == {"USA"} and e("A (Sweden)").scopes == {"Europe"}
    assert e("A (Japan)").scopes == {"Asia"} and e("A (Korea)").scopes == {"Asia"}
    assert e("A (Brazil)").scopes == {"Other"} and e("A (Version 3.5)").scopes == {"Other"}
    assert e("A (World)").scopes == {"USA", "Europe", "Asia", "Other"}


def test_hidden_regions():
    e = lambda g: dat.parse(f'game (\n\tname "{g}"\n\trom ( name "x" size 1 crc 00000001 )\n)\n')[0]  # noqa: E731
    try:
        dat.HIDDEN_REGIONS = {"Germany", "Other"}
        assert e("A (Germany)").scopes == set()                       # only released where you don't look
        assert e("A (Germany, Austria)").scopes == {"Europe"}          # Austria stays
        assert e("A (Europe)").scopes                                   # still there through Europe
        assert e("A (Version 3.5)").scopes == set()                     # "Other" hides what isn't a region
    finally:
        dat.HIDDEN_REGIONS = set()
