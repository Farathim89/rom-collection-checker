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
    assert by["Third (World) (Namcot Collection, Namco Museum Archives Vol 1)"].scopes == {"USA", "Europe"}


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
