"""Run with:  py -m unittest -v
Tests for the second round of features: cloud duplicates, registry saves, restore, startup upload,
save-location detection, Luna, device slots, sorting, title cleaning and window-title capture."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

import detect
import luna
import regsync
from launcher import finish_first_play, launch_plan, play
from library import Library, LocalSettings, sort_games, title_sort_key
from sync_engine import Action, SyncEngine, is_cloud_duplicate
from test_launcher import FakeGameFolder
from titles import clean_title


# ---------------------------------------------------------------------------------------------------------------
# Every test runs with a throwaway %APPDATA%: a test that opens a window and changes a setting must never write the
# owner's real settings (it once replaced the real cloud folder with a test folder, and the library looked empty).
_APPDATA: dict = {}


def setUpModule():
    _APPDATA["real"] = os.environ.get("APPDATA")
    _APPDATA["safe"] = tempfile.mkdtemp(prefix="ikemen-test-appdata-")
    os.environ["APPDATA"] = _APPDATA["safe"]


def tearDownModule():
    import shutil
    if _APPDATA.get("real") is not None:
        os.environ["APPDATA"] = _APPDATA["real"]
    shutil.rmtree(_APPDATA["safe"], ignore_errors=True)


IS_WINDOWS = sys.platform == "win32"
GAME = "Fake VN"


def write(folder: Path, name: str, text: str) -> None:
    p = folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def read_all(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): p.read_text("utf-8") for p in folder.rglob("*") if p.is_file()}


def search_now(w, text: str) -> None:
    """Type into the library's search box and apply it at once (the grid normally waits for typing to pause)."""
    w.search.setText(text)
    w._apply_filter()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cloud = self.root / "cloud"
        self.pc_saves, self.laptop_saves = self.root / "pc_saves", self.root / "laptop_saves"
        self.pc = SyncEngine(self.cloud, self.root / "pc_state", computers=lambda: ["DESKTOP-PC", "LAPTOP-X"])
        self.laptop = SyncEngine(self.cloud, self.root / "laptop_state", computers=lambda: ["DESKTOP-PC", "LAPTOP-X"])

    def tearDown(self):
        self._tmp.cleanup()


class CloudDuplicates(Base):
    def test_patterns(self):
        sib = {"saveg.dat", "sub/slot1.sav"}
        comps = ["DESKTOP-PC"]
        self.assertTrue(is_cloud_duplicate("saveg (Jaden's conflicted copy 2026-10-13).dat", sib, comps))
        self.assertTrue(is_cloud_duplicate("saveg (DESKTOP-PC の競合コピー 2026-10-13).dat", sib, comps))
        self.assertTrue(is_cloud_duplicate("saveg-DESKTOP-PC.dat", sib, comps))
        self.assertTrue(is_cloud_duplicate("saveg (1).dat", sib, comps))
        self.assertTrue(is_cloud_duplicate("sub/slot1 (2).sav", sib, comps))
        # not duplicates: no original beside it, or unknown computer name
        self.assertFalse(is_cloud_duplicate("other (1).dat", sib, comps))
        self.assertFalse(is_cloud_duplicate("saveg-SOMEONE.dat", sib, comps))
        self.assertFalse(is_cloud_duplicate("saveg.dat", sib, comps))

    def test_cloud_duplicates_are_ignored_and_never_copied_down(self):
        write(self.pc_saves, "saveg.dat", "chapter 1")
        self.pc.before_launch(GAME, self.pc_saves)
        write(self.pc.cloud_dir(GAME), "saveg (1).dat", "junk from Google Drive")
        write(self.pc.cloud_dir(GAME), "saveg-LAPTOP-X.dat", "junk from OneDrive")
        self.assertEqual(self.pc.status(GAME, self.pc_saves), Action.NOTHING)
        self.laptop.before_launch(GAME, self.laptop_saves)
        self.assertEqual(read_all(self.laptop_saves), {"saveg.dat": "chapter 1"})
        # and an upload doesn't delete them either (we leave the sync app's files alone)
        write(self.pc_saves, "saveg.dat", "chapter 2")
        self.pc.after_exit(GAME, self.pc_saves)
        self.assertTrue((self.pc.cloud_dir(GAME) / "saveg (1).dat").is_file())

    def test_a_real_local_file_named_like_a_duplicate_still_syncs(self):
        write(self.pc_saves, "save.dat", "a")
        write(self.pc_saves, "save (1).dat", "b")       # some games really name saves like this
        self.pc.before_launch(GAME, self.pc_saves)
        self.assertEqual(read_all(self.pc.cloud_dir(GAME)), {"save.dat": "a", "save (1).dat": "b"})


class SafetyGuards(Base):
    def test_blank_or_relative_save_folder_is_refused(self):
        for bad in ("", "   ", ".", "relative\\folder"):
            with self.assertRaises(ValueError):
                self.pc.before_launch(GAME, bad)


class RestoreAndStartupUpload(Base):
    def test_restore_puts_an_old_save_back_and_uploads_it(self):
        write(self.pc_saves, "s.dat", "v1")
        self.pc.before_launch(GAME, self.pc_saves)
        write(self.laptop_saves, "s.dat", "x")
        self.laptop.before_launch(GAME, self.laptop_saves, on_conflict=lambda i: "cloud")  # laptop: v1
        write(self.laptop_saves, "s.dat", "v2")
        self.laptop.after_exit(GAME, self.laptop_saves)
        self.pc.before_launch(GAME, self.pc_saves)                          # PC pulls v2, backs up v1
        self.assertEqual(read_all(self.pc_saves), {"s.dat": "v2"})
        backups = self.pc.list_backups(GAME)
        self.assertEqual(backups[0].reason, "local-before-pull")
        self.pc.restore(GAME, backups[0].name, self.pc_saves)
        self.assertEqual(read_all(self.pc_saves), {"s.dat": "v1"})
        self.assertEqual(read_all(self.pc.cloud_dir(GAME)), {"s.dat": "v1"})
        # the restore itself was backed up, so it can be undone
        self.assertIn("local-before-restore", [b.reason for b in self.pc.list_backups(GAME)])

    def test_push_if_safe_uploads_saves_made_outside_the_launcher(self):
        write(self.pc_saves, "s.dat", "v1")
        self.pc.before_launch(GAME, self.pc_saves)
        write(self.pc_saves, "s.dat", "v2")                                # played without the launcher
        self.assertTrue(self.pc.push_if_safe(GAME, self.pc_saves))
        self.assertEqual(read_all(self.pc.cloud_dir(GAME)), {"s.dat": "v2"})

    def test_push_if_safe_never_overwrites_when_both_changed(self):
        write(self.pc_saves, "s.dat", "v1")
        self.pc.before_launch(GAME, self.pc_saves)
        write(self.laptop_saves, "s.dat", "x")
        self.laptop.before_launch(GAME, self.laptop_saves, on_conflict=lambda i: "cloud")
        write(self.laptop_saves, "s.dat", "laptop progress")
        self.laptop.after_exit(GAME, self.laptop_saves)
        write(self.pc_saves, "s.dat", "pc progress")
        self.assertFalse(self.pc.push_if_safe(GAME, self.pc_saves))
        self.assertEqual(read_all(self.pc.cloud_dir(GAME)), {"s.dat": "laptop progress"})


@unittest.skipUnless(IS_WINDOWS, "the Windows registry only exists on Windows")
class RegistrySaves(Base):
    def setUp(self):
        super().setUp()
        import winreg
        self.winreg = winreg
        self.key = f"HKEY_CURRENT_USER\\Software\\IkemenReaderTest\\{uuid.uuid4().hex[:8]}"
        self.path = regsync.normalize(self.key)

    def tearDown(self):
        try:
            with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, "Software", 0, self.winreg.KEY_ALL_ACCESS) as sw:
                regsync._delete_tree(sw, "IkemenReaderTest")
        except OSError:
            pass
        super().tearDown()

    def set_value(self, name, value):
        with self.winreg.CreateKey(self.winreg.HKEY_CURRENT_USER, self.path) as h:
            if isinstance(value, bytes):
                self.winreg.SetValueEx(h, name, 0, self.winreg.REG_BINARY, value)
            elif isinstance(value, int):
                self.winreg.SetValueEx(h, name, 0, self.winreg.REG_DWORD, value)
            else:
                self.winreg.SetValueEx(h, name, 0, self.winreg.REG_SZ, value)

    def get_value(self, name):
        with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, self.path) as h:
            return self.winreg.QueryValueEx(h, name)[0]

    def test_unsafe_keys_are_refused(self):
        for bad in ("HKEY_LOCAL_MACHINE\\Software\\X\\Y", "HKCU\\Software", "HKCU\\Software\\Maker",
                    "HKCU\\Software\\Microsoft\\Windows", "HKCU\\Control Panel\\Desktop\\X", "Software\\X\\Y"):
            with self.assertRaises(regsync.RegistryKeyNotAllowed, msg=bad):
                regsync.normalize(bad)
        self.assertEqual(regsync.normalize("HKCU\\Software\\Maker\\Game"), "Software\\Maker\\Game")

    def test_unusual_value_types_round_trip(self):
        w = self.winreg
        with w.CreateKey(w.HKEY_CURRENT_USER, self.path) as h:
            w.SetValueEx(h, "multi", 0, w.REG_MULTI_SZ, ["a", "b"])
            w.SetValueEx(h, "big", 0, w.REG_QWORD, 2 ** 40)
            w.SetValueEx(h, "expand", 0, w.REG_EXPAND_SZ, "%APPDATA%\\x")
            w.SetValueEx(h, "odd", 0, w.REG_DWORD_BIG_ENDIAN, b"\x00\x00\x00\x07")
        data = regsync.export_key(self.key)
        regsync.import_key(self.key, regsync.loads(regsync.dumps(data)))
        self.assertEqual(regsync.export_key(self.key), data)

    def test_damaged_registry_download_changes_nothing(self):
        write(self.pc_saves, "s.dat", "pc file")
        self.set_value("progress", 1)
        self.pc.before_launch(GAME, self.pc_saves, registry_key=self.key)
        # the cloud copy gets damaged (half-synced, edited by hand...)
        bad = self.pc.cloud_dir(GAME) / "__registry__.json"
        write(self.pc.cloud_dir(GAME), "s.dat", "cloud file")
        for junk in ('{"values": {"progress": [4, "zz"]}}', '{"values": [1]}', "{", '{"keys": {"a": 5}}'):
            bad.write_text(junk, "utf-8")
            with self.assertRaises(ValueError, msg=junk):
                self.pc.before_launch(GAME, self.pc_saves, on_conflict=lambda i: "cloud", registry_key=self.key)
            self.assertEqual(self.get_value("progress"), 1, junk)              # registry untouched
            self.assertEqual(read_all(self.pc_saves), {"s.dat": "pc file"})    # files untouched
        with self.assertRaises(ValueError):
            regsync.import_key(self.key, {"values": {"x": "not a pair"}})
        self.assertEqual(self.get_value("progress"), 1)

    def test_registry_saves_travel_with_the_files(self):
        write(self.pc_saves, "s.dat", "file save")
        self.set_value("progress", 7)
        self.set_value("blob", b"\x00\x01\xff")
        self.set_value("name", "サクラ")
        self.pc.before_launch(GAME, self.pc_saves, registry_key=self.key)
        self.assertIn("__registry__.json", read_all(self.pc.cloud_dir(GAME)))
        # pretend this is the laptop: its registry has older progress
        self.set_value("progress", 1)
        self.laptop.before_launch(GAME, self.laptop_saves, on_conflict=lambda i: "cloud", registry_key=self.key)
        self.assertEqual(self.get_value("progress"), 7)
        self.assertEqual(self.get_value("blob"), b"\x00\x01\xff")
        self.assertEqual(self.get_value("name"), "サクラ")
        self.assertEqual(read_all(self.laptop_saves), {"s.dat": "file save"})   # no stray json locally

    def test_registry_change_alone_counts_as_new_progress(self):
        write(self.pc_saves, "s.dat", "same")
        self.set_value("progress", 1)
        self.pc.before_launch(GAME, self.pc_saves, registry_key=self.key)
        self.set_value("progress", 2)
        self.assertEqual(self.pc.status(GAME, self.pc_saves, self.key), Action.PUSHED)

    def test_registry_is_backed_up_and_restorable(self):
        write(self.pc_saves, "s.dat", "x")
        self.set_value("progress", 5)
        self.pc.before_launch(GAME, self.pc_saves, registry_key=self.key)
        exported = regsync.loads((self.pc.cloud_dir(GAME) / "__registry__.json").read_text("utf-8"))
        self.set_value("progress", 9)                       # newer local progress
        b = self.pc._backup_root(GAME)
        self.pc._backup(GAME, self.pc._local_files(GAME, self.pc_saves, self.key), "local-before-pull")
        regsync.import_key(self.key, exported)              # something overwrote it with 5
        self.assertEqual(self.get_value("progress"), 5)
        name = self.pc.list_backups(GAME)[0].name
        self.pc.restore(GAME, name, self.pc_saves, self.key)
        self.assertEqual(self.get_value("progress"), 9)


class Detection(unittest.TestCase):
    def test_finds_the_game_named_save_folder_and_ignores_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            appdata, game_dir = root / "AppData" / "Roaming", root / "Games" / "Sakutoki"
            game_dir.mkdir(parents=True)
            (game_dir / "sakuranotoki.exe").write_bytes(b"")
            before = time.time() - 1
            write(appdata / "枕" / "サクラノ刻", "saveg.dat", "x")
            write(appdata / "枕" / "サクラノ刻", "system.dat", "x")
            write(appdata / "Google" / "Chrome", "History", "noise")
            write(appdata / "SomeApp", "settings.ini", "noise")
            write(game_dir, "game.log", "noise")
            found = detect.find_folders(str(game_dir), before, ["サクラノ刻", "sakuranotoki", "Sakutoki"],
                                        roots=[game_dir, appdata])
            self.assertTrue(found)
            self.assertEqual(Path(found[0].path), appdata / "枕" / "サクラノ刻")
            self.assertNotIn(str(appdata / "Google" / "Chrome"), [c.path for c in found])

    def test_save_folder_inside_the_game_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "MyVN"
            before = time.time() - 1
            write(game_dir / "savedata", "data0001.bin", "x")
            write(game_dir / "savedata" / "thumb", "t1.png", "x")
            found = detect.find_folders(str(game_dir), before, ["My VN"], roots=[game_dir])
            self.assertEqual(Path(found[0].path), game_dir / "savedata")

    def test_old_files_are_not_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "MyVN"
            write(game_dir / "savedata", "old.bin", "x")
            old = time.time() - 3600
            os.utime(game_dir / "savedata" / "old.bin", (old, old))
            self.assertEqual(detect.find_folders(str(game_dir), time.time() + 5, ["MyVN"], roots=[game_dir]), [])


class LunaLookup(unittest.TestCase):
    def make_luna(self, root: Path, games: dict) -> str:
        exe = root / "LunaTranslator" / "LunaTranslator.exe"
        write(exe.parent, "LunaTranslator.exe", "")
        write(exe.parent / "userconfig", "savegamedata_5.3.1.json", json.dumps([list(games), games, [None, 1], None, {}]))
        return str(exe)

    def test_finds_the_game_id_by_exe_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game = root / "Games" / "VN" / "game.exe"
            write(game.parent, "game.exe", "")
            lp = self.make_luna(root, {"uid-1": {"gamepath": str(game).upper(), "title": "VN"},
                                       "uid-2": {"gamepath": "D:\\other.exe", "title": "Other"}})
            self.assertEqual(luna.kind(lp), "translator")
            self.assertEqual(luna.find_uid(lp, str(game)), "uid-1")
            self.assertIsNone(luna.find_uid(lp, str(root / "unknown.exe")))
            self.assertEqual(luna.game_path_for_uid(lp, "uid-2"), "D:\\other.exe")

    def test_launch_plan_explains_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game_exe = root / "g" / "game.exe"
            write(game_exe.parent, "game.exe", "")
            lib = Library(root / "cloud")
            g = lib.add_game("VN", "PC", exe=str(game_exe), save_dir=str(root / "s"), use_luna=True)
            s = LocalSettings("PC", str(root / "cloud"))
            self.assertEqual(launch_plan(g, s)[0], "play.luna_missing")
            s.luna_path = self.make_luna(root, {})
            self.assertEqual(launch_plan(g, s)[0], "play.luna_unknown_game")
            s.luna_path = self.make_luna(root, {"u": {"gamepath": str(game_exe)}})
            problem, plan = launch_plan(g, s)
            self.assertIsNone(problem)
            self.assertEqual(plan["luna"][2], "u")
            # with LunaTranslator, Luna's own LE setting applies, so a missing LEProc isn't a problem
            g.use_le = True
            self.assertIsNone(launch_plan(g, s)[0])
            g.use_luna = False
            self.assertEqual(launch_plan(g, s)[0], "play.le_missing")

    def test_admin_build_is_recognised(self):
        self.assertTrue(luna.is_admin_build(r"C:\Luna\LunaTranslator_admin.exe"))
        self.assertFalse(luna.is_admin_build(r"C:\Luna\LunaTranslator.exe"))


class DevicesAndLibrary(unittest.TestCase):
    def test_slot_safeguard_records_which_computer_uses_each_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(tmp)
            self.assertIsNone(lib.slot_owner("PC"))
            lib.claim_slot("PC", "DESKTOP-ABC")
            lib.claim_slot("Laptop", "LAPTOP-XYZ")
            self.assertEqual(lib.slot_owner("PC"), "DESKTOP-ABC")
            self.assertEqual(lib.known_computers(), {"DESKTOP-ABC", "LAPTOP-XYZ"})

    def test_sorting(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(tmp)
            a = lib.add_game("さくら", "PC")
            b = lib.add_game("Ever17", "PC")
            c = lib.add_game("アオイ", "PC")
            lib.record_session(a.game_id, "PC", 100, when=1000)
            lib.record_session(b.game_id, "PC", 5000, when=500)
            games = lib.list_games()
            self.assertEqual([g.title for g in sort_games(games, "recent")][:2], ["さくら", "Ever17"])
            self.assertEqual(sort_games(games, "hours")[0].title, "Ever17")
            # Latin first, then kana in あいうえお order (katakana sorted with hiragana)
            self.assertEqual([g.title for g in sort_games(games, "title")], ["Ever17", "アオイ", "さくら"])

    def test_readding_a_removed_game_reconnects_to_its_cloud_saves(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(tmp)
            g = lib.add_game("VN", "PC")
            (Path(tmp) / "saves" / g.game_id).mkdir(parents=True)
            lib.remove(g.game_id)
            self.assertEqual(lib.add_game("VN", "PC").game_id, g.game_id)

    def test_removing_a_cover_deletes_it_and_is_remembered(self):
        with tempfile.TemporaryDirectory() as tmp:
            art = Path(tmp) / "c.png"
            art.write_bytes(b"img")
            lib = Library(Path(tmp) / "cloud")
            g = lib.add_game("VN", "PC", artwork_src=str(art))
            path = lib.artwork_path(g)
            g = lib.clear_artwork(g.game_id)
            self.assertFalse(path.exists())
            self.assertIsNone(lib.artwork_path(g))
            self.assertEqual(g.artwork_from, "removed")
            # choosing a cover again afterwards works normally
            g = lib.set_artwork(g.game_id, str(art))
            self.assertEqual(g.artwork_from, "user")

    def test_cover_crop_is_kept_and_reset_only_for_a_new_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            art = Path(tmp) / "c.png"
            art.write_bytes(b"img")
            lib = Library(Path(tmp) / "cloud")
            # adding with a crop chosen in the dialog keeps it
            g = lib.add_game("VN", "PC", artwork_src=str(art), crop_zoom=2.0, crop_x=0.8, crop_y=0.2)
            self.assertEqual((g.crop_zoom, g.crop_x, g.crop_y), (2.0, 0.8, 0.2))
            # editing with a new cover AND a crop keeps the dialog's crop
            g = lib.edit(g.game_id, "VN", {}, artwork_src=str(art), crop_zoom=1.5, crop_x=0.3, crop_y=0.6)
            self.assertEqual((g.crop_zoom, g.crop_x, g.crop_y), (1.5, 0.3, 0.6))
            # a cover set some other way (e.g. VNDB after a first play) starts uncropped
            g = lib.set_artwork(g.game_id, str(art), source="vndb")
            self.assertEqual((g.crop_zoom, g.crop_x, g.crop_y), (1.0, 0.5, 0.5))

    def test_crop_maths_stays_inside_the_image(self):
        from app import COVER_H, COVER_W, crop_rect
        r, cx, cy = crop_rect(1200, 800, 1.0, 0.5, 0.5)       # wide image: full height, centred
        self.assertAlmostEqual(r.height(), 800)
        self.assertAlmostEqual(r.width() / r.height(), COVER_W / COVER_H, places=3)
        r, cx, cy = crop_rect(1200, 800, 2.0, 5.0, -3.0)      # way off to one side -> clamped
        self.assertGreaterEqual(r.x(), 0)
        self.assertLessEqual(r.right(), 1200 + 1e-6)
        self.assertGreaterEqual(r.y(), 0)
        self.assertAlmostEqual(r.right(), 1200)
        self.assertAlmostEqual(r.y(), 0)
        r, _, _ = crop_rect(1200, 800, 99, 0.5, 0.5)          # zoom is capped
        self.assertAlmostEqual(r.height(), 800 / 4.0)

    def test_new_fields_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(tmp)
            g = lib.add_game("VN", "PC", use_luna=True, status="reading", title_locked=True)
            g2 = lib.get(g.game_id)
            self.assertEqual((g2.use_luna, g2.use_le, g2.status, g2.title_locked), (True, False, "reading", True))


class PlaytimeFormat(unittest.TestCase):
    def test_minutes_only_under_an_hour(self):
        import i18n
        try:
            for lang, cases in (("en", {49: "49s", 60: "1m", 50 * 60: "50m", 3599: "59m",
                                        3600: "1h 00m", 45 * 3600 + 10 * 60: "45h 10m"}),
                                ("ja", {50 * 60: "50分", 3600 + 17 * 60: "1時間17分"})):
                i18n.set_language(lang)
                for secs, want in cases.items():
                    self.assertEqual(i18n.playtime(secs), want, (lang, secs))
        finally:
            i18n.set_language("en")


class Folders(unittest.TestCase):
    def test_folders_create_rename_move_and_disappear_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(tmp)
            a, b, c = (lib.add_game(t, "PC") for t in ("A", "B", "C"))
            fid = lib.create_folder("Makura")
            lib.set_folder(a.game_id, fid)
            lib.set_folder(b.game_id, fid)
            self.assertEqual(lib.list_folders(), {fid: "Makura"})
            self.assertEqual({g.title for g in lib.list_games() if g.folder == fid}, {"A", "B"})
            lib.rename_folder(fid, "枕")
            self.assertEqual(lib.list_folders()[fid], "枕")
            lib.set_folder(a.game_id, "")
            self.assertIn(fid, lib.list_folders())          # still has B
            lib.set_folder(b.game_id, "")
            self.assertNotIn(fid, lib.list_folders())       # empty -> gone

    def test_removing_a_folder_keeps_its_games(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(tmp)
            a = lib.add_game("A", "PC")
            fid = lib.create_folder("F")
            lib.set_folder(a.game_id, fid)
            lib.delete_folder(fid)
            self.assertEqual(lib.get(a.game_id).folder, "")
            self.assertEqual(lib.list_folders(), {})

    def test_removing_the_last_game_removes_its_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(tmp)
            a = lib.add_game("A", "PC")
            fid = lib.create_folder("F")
            lib.set_folder(a.game_id, fid)
            lib.remove(a.game_id)
            self.assertEqual(lib.list_folders(), {})


class Hardening(unittest.TestCase):
    """Problems found by stress-testing the program."""

    def test_dangerous_save_folders_are_refused(self):
        from sync_engine import save_folder_problem, UnsafeSaveFolder
        home = Path.home()
        with tempfile.TemporaryDirectory() as tmp:
            cloud = Path(tmp) / "OneDrive" / "VNSaves"
            cloud.mkdir(parents=True)
            f = Path(tmp) / "afile.dat"
            f.write_text("x")
            self.assertEqual(save_folder_problem("C:\\", cloud), "error.save_broad")
            self.assertEqual(save_folder_problem(home / "Documents", cloud), "error.save_broad")
            self.assertEqual(save_folder_problem(os.environ["APPDATA"], cloud), "error.save_broad")
            self.assertEqual(save_folder_problem(cloud, cloud), "error.save_cloud")
            self.assertEqual(save_folder_problem(cloud / "saves" / "X", cloud), "error.save_cloud")
            self.assertEqual(save_folder_problem(cloud.parent, cloud), "error.save_cloud")
            self.assertEqual(save_folder_problem(f, cloud), "error.save_file")
            self.assertEqual(save_folder_problem("relative\\x", cloud), "error.save_relative")
            self.assertIsNone(save_folder_problem(Path(os.environ["APPDATA"]) / "枕" / "サクラノ刻", cloud))
            with self.assertRaises(UnsafeSaveFolder):
                SyncEngine(cloud, Path(tmp) / "st").before_launch("G", cloud)

    def test_unusual_titles_get_safe_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(tmp)
            for t in ["CON", "nul", "COM1", "....", "x" * 300, "a/b:c*?"]:
                g = lib.add_game(t, "PC")
                self.assertIsNotNone(lib.get(g.game_id), t)
                self.assertLessEqual(len(g.game_id), 90)

    def test_one_damaged_game_file_does_not_hide_the_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(tmp)
            lib.add_game("Good", "PC")
            for i, text in enumerate(['{"game_id": "a", "title": null}', '{"game_id": "b", "title": "B", "paths": null}',
                                      '[1, 2]', '{"game_id": "c", "title": "C", "crop_zoom": "big", "playtime": {"PC": "x"}}']):
                (lib.games_dir / f"bad{i}.json").write_text(text, "utf-8")
            titles = sorted(g.title for g in lib.list_games())
            self.assertEqual(titles, ["B", "C", "Good"])
            c = next(g for g in lib.list_games() if g.game_id == "c")
            self.assertEqual((c.crop_zoom, c.playtime), (1.0, {}))
            (lib.root / "devices.json").write_text("[1, 2]", "utf-8")
            self.assertIsNone(lib.slot_owner("PC"))

    def test_read_only_save_files_can_be_updated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saves = root / "s"
            saves.mkdir()
            (saves / "r.dat").write_text("old")
            os.chmod(saves / "r.dat", 0o444)
            cloud_copy = root / "cloud" / "saves" / "G"
            cloud_copy.mkdir(parents=True)
            (cloud_copy / "r.dat").write_text("new")
            SyncEngine(root / "cloud", root / "st").before_launch("G", saves, on_conflict=lambda i: "cloud")
            self.assertEqual((saves / "r.dat").read_text(), "new")

    @unittest.skipUnless(IS_WINDOWS, "uses the Windows fake-game runtime")
    def test_launcher_that_starts_the_game_from_a_sibling_folder(self):
        from launcher import run_game
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "VN"
            real = FakeGameFolder(root / "game")
            real.game("realgame", "import time; time.sleep(2.5)", "")
            launcher_folder = FakeGameFolder(root / "launcher")
            exe, rt = launcher_folder.game(
                "launch", "import subprocess\n"
                f"subprocess.Popen([r'{real.runtime}', 'realgame'], cwd=r'{real.dir}', "
                "creationflags=subprocess.DETACHED_PROCESS)\n", "")
            self.assertGreaterEqual(run_game(exe, rt, poll=0.2, grace=0.5), 2.0)

    @unittest.skipUnless(IS_WINDOWS, "uses the Windows fake-game runtime")
    def test_a_game_that_is_already_running_is_not_synced_underneath(self):
        from launcher import warm_up
        warm_up()                     # as the window does when it opens (the first look is slow)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = FakeGameFolder(root / "VN")
            exe, rt = folder.game("game", "import time; time.sleep(6)", "")
            pre = subprocess.Popen([rt, exe], cwd=str(folder.dir))
            try:
                time.sleep(1)
                lib = Library(root / "cloud")
                g = lib.add_game("VN", "PC", exe=exe, save_dir=str(root / "s"), use_le=True)
                r = play(g, LocalSettings("PC", str(root / "cloud"), le_path=rt), lib,
                         SyncEngine(root / "cloud", root / "st"), status=lambda m: None, poll=0.2, grace=0.5)
                self.assertEqual((r.launched, r.message, r.retry_without), (False, "play.already_running", "watch"))
            finally:
                pre.wait()


@unittest.skipUnless(IS_WINDOWS, "Windows startup list")
class StartupOption(unittest.TestCase):
    KEY = r"Software\IkemenReaderTest\Run"          # a test key, not the real startup list

    def tearDown(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software", 0, winreg.KEY_ALL_ACCESS) as sw:
                regsync._delete_tree(sw, "IkemenReaderTest")
        except OSError:
            pass

    def test_switching_on_and_off(self):
        import startup
        self.assertFalse(startup.is_enabled(self.KEY))
        startup.set_enabled(True, self.KEY)
        self.assertTrue(startup.is_enabled(self.KEY))
        self.assertIn("app.py", startup.current(self.KEY))
        self.assertIn("pythonw.exe", startup.current(self.KEY).lower())
        startup.set_enabled(False, self.KEY)
        self.assertFalse(startup.is_enabled(self.KEY))
        startup.set_enabled(False, self.KEY)              # turning off twice is fine

    def test_refresh_repoints_a_stale_entry(self):
        import startup, winreg
        startup.set_enabled(True, self.KEY)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.KEY, 0, winreg.KEY_SET_VALUE) as h:
            winreg.SetValueEx(h, startup.VALUE, 0, winreg.REG_SZ, r'"C:\old\place\Ikemen Reader.exe"')
        startup.refresh(self.KEY)
        self.assertEqual(startup.current(self.KEY), startup.command())


class Titles(unittest.TestCase):
    CASES = {
        "サクラノ刻 ―櫻の森の下を歩む―": "サクラノ刻 ―櫻の森の下を歩む―",
        "サクラノ刻 ver1.02": "サクラノ刻",
        "サクラノ刻 Ver.2.00": "サクラノ刻",
        "Sakura no Toki v1.0": "Sakura no Toki",
        "Sakura no Toki - Complete Edition": "Sakura no Toki",
        "千恋＊万花 【体験版】": "千恋＊万花",
        "千恋＊万花 体験版": "千恋＊万花",
        "千恋＊万花体験版": "千恋＊万花",
        "素晴らしき日々 ～不連続存在～ [DL版]": "素晴らしき日々 ～不連続存在～",
        "WHITE ALBUM2 (Trial)": "WHITE ALBUM2",
        "Ever17 -the out of infinity- Steam Edition ver1.0.3": "Ever17 -the out of infinity-",
        "スロウ・ダメージ 普及版": "スロウ・ダメージ",
        "Steins;Gate 0": "Steins;Gate 0",
        "Muv-Luv Alternative": "Muv-Luv Alternative",
        "Fate/stay night [Réalta Nua]": "Fate/stay night [Réalta Nua]",
        "大穢-前編-": "大穢-前編-",
        "サクラノ詩－櫻の森の上を舞う－ 完全版 ver1.01": "サクラノ詩－櫻の森の上を舞う－",
        "Trial": "Trial",
        "Summer Pockets REFLECTION BLUE": "Summer Pockets REFLECTION BLUE",
        "Game 2": "Game 2",
        "クロスチャンネル ～For all people～ ver.1.10": "クロスチャンネル ～For all people～",
        "ATRI -My Dear Moments- (Demo)": "ATRI -My Dear Moments-",
    }

    def test_clean_title(self):
        for raw, want in self.CASES.items():
            self.assertEqual(clean_title(raw), want, raw)


@unittest.skipUnless(IS_WINDOWS, "window titles are read with the Windows API")
class FirstPlay(unittest.TestCase):
    def test_window_title_becomes_the_title_and_save_folder_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = FakeGameFolder(root / "FakeVN")
            save = folder.dir / "savedata" / "slot1.dat"
            # a fake game: shows a window titled with an edition tag, writes a save, then closes
            exe, runtime = folder.game(
                "game",
                "import tkinter, pathlib\n"
                f"pathlib.Path(r'{save}').parent.mkdir(exist_ok=True)\n"
                f"pathlib.Path(r'{save}').write_text('progress')\n"
                "t = tkinter.Tk(); t.title('ふぇいく物語 体験版 ver1.02'); t.geometry('200x80+-3000+-3000')\n"
                "t.after(2500, t.destroy); t.mainloop()\n", "")
            lib = Library(root / "cloud")
            g = lib.add_game("FakeVN", "PC", exe=exe, use_le=True)        # no save folder yet
            s = LocalSettings("PC", str(root / "cloud"), le_path=runtime)
            engine = SyncEngine(root / "cloud", root / "state")
            r = play(g, s, lib, engine, status=lambda m: None, poll=0.2, grace=0.5)
            self.assertTrue(r.launched, r.message)
            self.assertEqual(r.window_title, "ふぇいく物語 体験版 ver1.02")
            self.assertEqual(lib.get(g.game_id).title, "ふぇいく物語")
            self.assertTrue(r.candidates, "no save location found")
            self.assertEqual(Path(r.candidates[0].path), save.parent)
            res = finish_first_play(lib.get(g.game_id), s, lib, engine, r.candidates[0].path)
            self.assertEqual(res.action, Action.PUSHED)
            self.assertEqual(read_all(engine.cloud_dir(g.game_id)), {"slot1.dat": "progress"})

    def test_window_title_is_remembered_even_when_the_title_is_typed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = FakeGameFolder(root / "VN")
            exe, runtime = folder.game(
                "game", "import tkinter\nt = tkinter.Tk(); t.title('Window Name ver1.0'); t.geometry('+-3000+-3000')\n"
                        "t.after(1500, t.destroy); t.mainloop()\n", "")
            lib = Library(root / "cloud")
            g = lib.add_game("Mine", "PC", exe=exe, save_dir=str(root / "s"), use_le=True, title_locked=True)
            play(g, LocalSettings("PC", str(root / "cloud"), le_path=runtime), lib,
                 SyncEngine(root / "cloud", root / "state"), status=lambda m: None, poll=0.2, grace=0.5)
            g = lib.get(g.game_id)
            self.assertEqual((g.title, g.auto_title), ("Mine", "Window Name"))

    def test_typed_title_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = FakeGameFolder(root / "VN")
            exe, runtime = folder.game(
                "game", "import tkinter\nt = tkinter.Tk(); t.title('Window Name'); t.geometry('+-3000+-3000')\n"
                        "t.after(1500, t.destroy); t.mainloop()\n", "")
            lib = Library(root / "cloud")
            g = lib.add_game("My Own Title", "PC", exe=exe, save_dir=str(root / "s"), use_le=True, title_locked=True)
            r = play(g, LocalSettings("PC", str(root / "cloud"), le_path=runtime), lib,
                     SyncEngine(root / "cloud", root / "state"), status=lambda m: None, poll=0.2, grace=0.5)
            self.assertTrue(r.launched)
            self.assertEqual(lib.get(g.game_id).title, "My Own Title")


@unittest.skipUnless(IS_WINDOWS, "shortcuts are a Windows thing")
class Shortcuts(unittest.TestCase):
    def test_shortcut_resolves_to_its_exe(self):
        import lnk
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "ゲーム" / "game.exe"
            write(target.parent, "game.exe", "")
            link = Path(tmp) / "link.lnk"
            ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
                  f"$s.TargetPath='{target}';$s.Save()")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)
            self.assertEqual(Path(lnk.resolve_game_exe(str(link))), target)


if __name__ == "__main__":
    unittest.main()

class SettingsSave(unittest.TestCase):
    """Settings' Save button really saves (it once crashed after a new setting was added)."""

    def test_save_language_and_fonts(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            import i18n
            from library import load_local_settings
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            current = LocalSettings("PC", str(cloud), font_en="Georgia")
            try:
                d = app.SettingsDialog(current)
                d.lang.setCurrentIndex(1)                       # Japanese
                d._save()
                self.assertIsNotNone(d.result_settings)
                self.assertEqual(d.result_settings.language, "ja")
                saved = load_local_settings()
                self.assertEqual((saved.language, saved.font_en, saved.cloud_root),
                                 ("ja", "Georgia", str(cloud)))
            finally:
                i18n.set_language("en")

class IkemenSync(unittest.TestCase):
    """Device Settings Sync: one device shares its look/preferences, the other copies them."""

    def test_share_and_copy(self):
        import devsync
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cloud = tmp / "cloud"
            cloud.mkdir()
            bg = tmp / "bg.png"
            bg.write_bytes(b"picture")
            font = tmp / "MyFont.ttf"
            font.write_bytes(b"font data")
            laptop = LocalSettings("Laptop", str(cloud), le_path=r"C:\laptop\LEProc.exe", language="ja",
                                   theme="custom", custom_colors={"bg": "#112233"}, bg_image=str(bg),
                                   bg_opacity=0.5, bg_zoom=2.0, font_en="My Font", pulse=False, sort="title")
            devsync.publish(laptop, "LAPTOP-PC", {"My Font": str(font)})
            shared = devsync.read(str(cloud), "Laptop")
            self.assertEqual(shared["computer"], "LAPTOP-PC")
            first_time = shared["shared_at"]
            devsync.publish(laptop, "LAPTOP-PC", {"My Font": str(font)})           # nothing changed
            self.assertEqual(devsync.read(str(cloud), "Laptop")["shared_at"], first_time)

            pc = LocalSettings("PC", str(cloud), le_path=r"D:\pc\LEProc.exe", luna_path=r"D:\luna.exe")
            new, files = devsync.apply(devsync.read(str(cloud), "Laptop"), str(cloud), "Laptop", pc)
            # the look and preferences come across ...
            self.assertEqual((new.language, new.theme, new.custom_colors, new.bg_opacity, new.bg_zoom,
                              new.font_en, new.pulse, new.sort),
                             ("ja", "custom", {"bg": "#112233"}, 0.5, 2.0, "My Font", False, "title"))
            self.assertEqual(Path(files["background"]).read_bytes(), b"picture")
            self.assertEqual(Path(files["fonts"]["My Font"]).read_bytes(), b"font data")
            # ... but what belongs to this computer doesn't
            self.assertEqual((new.machine_name, new.cloud_root, new.le_path, new.luna_path),
                             ("PC", str(cloud), r"D:\pc\LEProc.exe", r"D:\luna.exe"))

            # removing the background / going back to built-in fonts tidies the shared files
            laptop.bg_image, laptop.font_en = "", "Hiragino Maru Gothic ProN"
            devsync.publish(laptop, "LAPTOP-PC", {"My Font": str(font)})
            d = devsync.device_dir(str(cloud), "Laptop")
            self.assertEqual(sorted(p.name for p in d.iterdir() if p.is_file()), ["settings.json"])
            self.assertEqual(list((d / "fonts").iterdir()), [])

    def test_folders_named_after_device_numbers_and_swap(self):
        import devsync
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "device_settings"
            (base / "PC").mkdir(parents=True)                                     # left by an older version
            (base / "PC" / "settings.json").write_text('{"computer": "DESKTOP"}', "utf-8")
            devsync.publish(LocalSettings("Laptop", tmp, theme="light"), "LAPTOP", {})
            devsync.publish(LocalSettings("PC", tmp, theme="dark"), "DESKTOP", {})
            self.assertEqual(sorted(p.name for p in base.iterdir()), ["Device 1", "Device 2"])
            # the PC becomes Device 2 (and the laptop Device 1): the folders swap, nothing is lost
            devsync.publish(LocalSettings("Laptop", tmp, theme="dark"), "DESKTOP", {})
            self.assertEqual(devsync.read(tmp, "Laptop")["computer"], "DESKTOP")
            self.assertEqual(devsync.read(tmp, "PC"), dict(devsync.read(tmp, "PC"), computer="LAPTOP", theme="light"))
            self.assertEqual(sorted(p.name for p in base.iterdir()), ["Device 1", "Device 2"])

    def test_other_device_on_an_older_version(self):
        """1.0.1 on the other device still shares into "Laptop": its newer copy wins over a stale "Device 2"."""
        import devsync
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "device_settings"
            for name, theme, when in (("Device 2", "dark", 100), ("Laptop", "light", 200)):
                (base / name).mkdir(parents=True)
                (base / name / "settings.json").write_text(json.dumps({"computer": "LAPTOP", "theme": theme,
                                                                       "shared_at": when}), "utf-8")
            devsync.publish(LocalSettings("PC", tmp), "DESKTOP", {})
            self.assertEqual(devsync.read(tmp, "Laptop")["theme"], "light")
            self.assertEqual(sorted(p.name for p in base.iterdir()), ["Device 1", "Device 2"])
            (base / "Laptop").mkdir()                                            # an older, stale leftover
            (base / "Laptop" / "settings.json").write_text(json.dumps({"theme": "dark", "shared_at": 50}), "utf-8")
            devsync.publish(LocalSettings("PC", tmp), "DESKTOP", {})
            self.assertEqual(devsync.read(tmp, "Laptop")["theme"], "light")

    def test_damaged_or_missing(self):
        import devsync
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(devsync.read(tmp, "Laptop"))                      # never shared
            d = devsync.device_dir(tmp, "Laptop")
            d.mkdir(parents=True)
            (d / "settings.json").write_text("{not json", "utf-8")
            self.assertIsNone(devsync.read(tmp, "Laptop"))
            mine = LocalSettings("PC", tmp)
            new, files = devsync.apply({"pulse": "yes", "bg_opacity": 1, "theme": 5}, tmp, "Laptop", mine)
            self.assertEqual((new.pulse, new.bg_opacity, new.theme), (True, 1.0, "dark"))   # wrong types ignored
            self.assertEqual(files, {"background": "", "fonts": {}})

class DamagedSharedFiles(unittest.TestCase):
    """The cloud folder is shared, half-synced and sometimes hand-edited: nothing in it may crash the
    program or reach outside its own folders."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.lib = Library(self.root / "cloud")

    def tearDown(self):
        self._tmp.cleanup()

    def test_cover_names_cannot_point_outside_the_artwork_folder(self):
        self.lib.add_game("Victim", "PC")
        g = self.lib.add_game("Bait", "PC")
        f = self.lib._file(g.game_id)
        data = json.loads(f.read_text("utf-8"))
        for evil in ("../games/Victim.json", "..\\\\games\\\\Victim.json", "C:\\\\Windows\\\\x.png", ".."):
            data["artwork"] = evil
            f.write_text(json.dumps(data), "utf-8")
            self.assertEqual(self.lib.get(g.game_id).artwork, "", evil)
        self.lib.remove(g.game_id)
        self.assertIsNotNone(self.lib.get("Victim"))          # the other game's file is untouched

    def test_odd_folder_and_device_files_never_crash(self):
        self.lib.add_game("A", "PC")
        for junk in ("[1,2]", "5", '"x"', '{"f1": {"name": 7}}', '{"f1": 3}', "{"):
            self.lib.folders_file.write_text(junk, "utf-8")
            folders = self.lib.list_folders()
            self.assertTrue(all(isinstance(n, str) for n in folders.values()), junk)
            self.lib.devices_file.write_text(junk.replace("name", "computer"), "utf-8")
            self.assertIn(self.lib.slot_owner("PC"), (None,), junk)
            self.assertTrue(all(isinstance(c, str) for c in self.lib.known_computers()))

    def test_odd_numbers_in_game_and_settings_files(self):
        from library import load_local_settings, save_local_settings
        g = self.lib.add_game("A", "PC")
        f = self.lib._file(g.game_id)
        data = json.loads(f.read_text("utf-8"))
        data.update(last_played=float("nan"), crop_zoom=float("inf"), playtime={"PC": "lots"})
        f.write_text(json.dumps(data), "utf-8")
        g = self.lib.get(g.game_id)
        self.assertEqual((g.last_played, g.crop_zoom, g.playtime), (0.0, 1.0, {}))
        app_dir = self.root / "app"
        save_local_settings(LocalSettings("PC", str(self.root)), app_dir)
        s = json.loads((app_dir / "settings.json").read_text("utf-8"))
        s.update(bg_opacity=float("nan"), bg_zoom=float("-inf"), machine_name="Toaster", custom_colors={"bg": "red"})
        (app_dir / "settings.json").write_text(json.dumps(s), "utf-8")
        s = load_local_settings(app_dir)
        self.assertEqual((s.bg_opacity, s.bg_zoom, s.machine_name, s.custom_colors), (0.35, 1.0, "PC", {}))

    def test_shared_device_settings_cannot_reach_outside_their_folder(self):
        import devsync
        cloud = self.root / "cloud"
        secret = self.root / "secret.ttf"
        secret.write_bytes(b"not yours")
        shared = {"background": "../../secret.ttf", "fonts": {"X": "../../../secret.ttf", 5: "a"},
                  "custom_colors": {"bg": "url(evil)", "text": "#ABCDEF"}, "bg_opacity": float("nan")}
        new, files = devsync.apply(shared, str(cloud), "Laptop", LocalSettings("PC", str(cloud)))
        self.assertEqual(files, {"background": "", "fonts": {}})
        self.assertEqual(new.custom_colors, {"text": "#ABCDEF"})
        self.assertEqual(new.bg_opacity, 0.35)

class DamagedShortcuts(unittest.TestCase):
    def test_damaged_shortcuts_are_rejected_without_hanging(self):
        import lnk, os, struct, threading
        with tempfile.TemporaryDirectory() as tmp:
            body = bytearray(struct.pack("<I", 0x4C) + b"\\0" * 72)
            body[20:24] = struct.pack("<I", 0x2)
            unterminated = bytes(body) + struct.pack("<IIIIIII", 0x40, 0x24, 1, 0, 0, 0, 0) + struct.pack("<I", 0x24) + b"A\\0B\\0"
            for i, data in enumerate((b"", os.urandom(300), bytes(body)[:60], unterminated)):
                f = Path(tmp) / f"{i}.lnk"
                f.write_bytes(data)
                out = []
                t = threading.Thread(target=lambda: out.append(lnk.resolve_game_exe(str(f))), daemon=True)
                t.start()
                t.join(3)
                self.assertFalse(t.is_alive(), f"shortcut {i} hung")

class LongTitles(unittest.TestCase):
    def test_many_games_with_the_same_long_title_never_hang(self):
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(tmp)
            ids = []
            def add_all():
                for i in range(5):
                    ids.append(lib.add_game("x" * 200 + f" {i}", "PC").game_id)      # differ only after 80 chars
                    ids.append(lib.add_game("サクラノ刻 ―櫻の森の下を歩む― 完全版" * 4 + f"{i}", "PC").game_id)
            t = threading.Thread(target=add_all, daemon=True)
            t.start()
            t.join(10)
            self.assertFalse(t.is_alive(), "adding games hung")
            self.assertEqual(len(set(ids)), 10)
            self.assertTrue(all(len(i) <= 80 for i in ids))

class ExactPlaytime(unittest.TestCase):
    def test_sleep_never_counts_and_overlaps_count_once(self):
        import launcher
        c = launcher._ExactClock(not_before=0.0)
        c.ok = True
        # wall clock vs awake clock: awake for 10 s, asleep for 2 hours (wall jumps, awake doesn't), awake 20 s
        c.samples = [(1000.0, 50.0), (1010.0, 60.0), (8210.0, 60.0), (8230.0, 80.0)]
        c.handles = {1: 1, 2: 2}
        c._times = lambda: [(1000.0, 8230.0), (1005.0, 1009.0)]      # the game, plus a helper inside it
        c.sample = lambda: None
        self.assertAlmostEqual(c.total(), 30.0, places=6)             # 10 s + 20 s, the sleep isn't playtime

    def test_handover_gap_is_not_playtime(self):
        import launcher
        c = launcher._ExactClock(not_before=0.0)
        c.ok, c.handles, c.sample = True, {1: 1, 2: 2}, lambda: None
        c.samples = [(0.0, 0.0), (100.0, 100.0)]
        c._times = lambda: [(10.0, 12.5), (14.0, 74.25)]               # launcher, 1.5 s gap, then the game
        self.assertAlmostEqual(c.total(), 62.75, places=6)

    def test_milliseconds_add_up_exactly_over_many_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(tmp)
            g = lib.add_game("A", "PC")
            for _ in range(1000):
                lib.record_session(g.game_id, "PC", 1.234)
            self.assertAlmostEqual(lib.get(g.game_id).playtime["PC"], 1234.0, places=3)

class StatisticsSetting(unittest.TestCase):
    def test_shown_by_default_remembered_and_shared(self):
        import devsync
        from library import load_local_settings, save_local_settings
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp) / "app"
            s = LocalSettings("PC", tmp)
            self.assertTrue(s.show_stats)                                   # on by default
            s.show_stats = False
            save_local_settings(s, app_dir)
            self.assertFalse(load_local_settings(app_dir).show_stats)      # remembered
            self.assertIn("show_stats", devsync.SHARED)                     # Ikemen Sync copies it
            new, _ = devsync.apply({"show_stats": False}, tmp, "Laptop", LocalSettings("PC", tmp))
            self.assertFalse(new.show_stats)

class LiveRefresh(unittest.TestCase):
    def test_signature_changes_only_with_shared_library_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(tmp)
            g = lib.add_game("A", "PC")
            sig = lib.change_signature()
            self.assertEqual(lib.change_signature(), sig)                       # nothing changed
            lib.claim_slot("PC")                                                # who-uses-which-device: not library data
            self.assertEqual(lib.change_signature(), sig)
            (Path(tmp) / "device_settings").mkdir()
            (Path(tmp) / "device_settings" / "x.json").write_text("{}")        # Ikemen Sync data: not library data
            self.assertEqual(lib.change_signature(), sig)
            for change in (lambda: lib.add_game("B", "PC"), lambda: lib.update(g.game_id, status="reading"),
                           lambda: lib.create_folder("F"),
                           lambda: (Path(tmp) / "saves" / "A").mkdir(parents=True) or (Path(tmp) / "saves" / "A" / "s.dat").write_text("1")):
                change()
                new = lib.change_signature()
                self.assertNotEqual(new, sig)
                sig = new


class BottomBarWhilePlaying(unittest.TestCase):
    """While a game runs: selecting another game shows that game; selecting nothing (or the running game)
    shows the running one with Stop."""

    def test_stop_stays_when_clicking_elsewhere(self):
        import threading
        from types import SimpleNamespace
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            import i18n
            from library import Library
            QApplication.instance() or QApplication([])
            i18n.set_language("en")
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            exe = Path(tmp) / "game.exe"
            exe.write_bytes(b"")
            reading = lib.add_game("Reading Now", "PC", exe=str(exe))
            other = lib.add_game("Something Else", "PC", exe=str(exe))
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.refresh()
                w.games = {g.game_id: g for g in lib.list_games()}
                w.worker = SimpleNamespace(stop_pressed=threading.Event(), force_close=threading.Event())
                w.playing_id, w.play_phase = reading.game_id, "playing"

                def bar():
                    return w.d_title.text(), w.play_btn.text(), w.play_btn.isEnabled()
                w.clear_selection()                                         # click on empty space
                self.assertEqual(bar(), ("Reading Now", "Stop", True))
                for i in range(w.grid.count()):                             # click another game
                    if w.grid.item(i).data(app.GAME_ID_ROLE) == other.game_id:
                        w.grid.setCurrentItem(w.grid.item(i))
                self.assertEqual(w.selected_id(), other.game_id)
                self.assertEqual(bar(), ("Something Else", "Read", False))  # its own details; Read waits
                w.play_btn.click()
                self.assertFalse(w.worker.stop_pressed.is_set())
                w.clear_selection()                                         # click off again
                self.assertEqual(bar(), ("Reading Now", "Stop", True))
                w.play_btn.click()                                          # Stop acts on the running game
                self.assertTrue(w.worker.stop_pressed.is_set())
                for i in range(w.grid.count()):                             # select the other game again
                    if w.grid.item(i).data(app.GAME_ID_ROLE) == other.game_id:
                        w.grid.setCurrentItem(w.grid.item(i))
                w.worker, w.playing_id, w.play_phase = None, None, "done"   # it closed
                w._update_details()
                self.assertEqual(bar(), ("Something Else", "Read", True))  # back to the selection
            finally:
                w.worker = None
                w.close()


class SaveFolderSuggestions(unittest.TestCase):
    """Adding a game: its save folder is suggested straight away when it can be known without a session."""

    def setUp(self):
        import detect
        self.detect = detect
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.appdata = self.root / "AppData" / "Roaming"
        self.appdata.mkdir(parents=True)
        self.game = self.root / "D" / "Slow Damage JP" / "スロウ・ダメージ"
        self.game.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def suggest(self, other=None, names=("スロウ・ダメージ", "slowdamage"), usable=lambda p: True):
        return self.detect.suggest_save_folder(str(self.game), list(names), other, usable,
                                               home=self.root, roots=[self.game, self.appdata])

    def test_other_devices_folder_under_this_users_profile(self):
        s = self.suggest((r"C:\Users\doge_\VN\slowdamage.exe",
                          r"C:\Users\doge_\AppData\Roaming\NITRO CHiRAL\slowdamage 普及版\1.10"))
        self.assertEqual((s.path, s.reason),
                         (str(self.appdata / "NITRO CHiRAL" / "slowdamage 普及版" / "1.10"), "other_device"))

    def test_other_devices_folder_inside_its_game_folder(self):
        s = self.suggest((r"C:\Users\doge_\BL\Ooe\大穢.exe", r"C:\Users\doge_\BL\Ooe\savedata"))
        self.assertEqual(s.path, str(self.game / "savedata"))       # this device's game folder, not C:\Users\...

    def test_other_devices_exact_path_outside_any_profile(self):
        s = self.suggest((r"E:\VN\game.exe", r"C:\ProgramData\Maker\Saves"))
        self.assertEqual(s.path, r"C:\ProgramData\Maker\Saves")

    def test_existing_folder_named_after_the_game(self):
        saves = self.appdata / "NITRO CHiRAL" / "slowdamage 普及版" / "1.10"
        (saves / "0001").mkdir(parents=True)
        (saves / "system.dat").write_bytes(b"x")
        s = self.suggest()
        self.assertEqual((s.path, s.reason), (str(saves), "existing"))   # down to 1.10, like the other device

    def test_save_folder_inside_the_game_folder(self):
        (self.game / "savedata").mkdir()
        (self.game / "savedata" / "save01.dat").write_bytes(b"x")
        self.assertEqual(self.suggest().path, str(self.game / "savedata"))

    def test_generic_exe_names_and_empty_folders_suggest_nothing(self):
        (self.appdata / "Start Menu").mkdir()
        (self.appdata / "Start Menu" / "x.lnk").write_bytes(b"x")
        (self.appdata / "スロウ・ダメージ").mkdir()                      # named after it, but empty
        self.assertIsNone(self.suggest(names=("Start",)))
        self.assertIsNone(self.suggest())

    def test_unsafe_places_are_never_suggested(self):
        self.assertIsNone(self.suggest((r"C:\Users\doge_\VN\game.exe", r"C:\Users\doge_\Documents"),
                                       usable=lambda p: False))


class SaveFolderSuggestionsInTheWindow(unittest.TestCase):
    def _wait(self, cond, seconds=10):
        from PySide6.QtWidgets import QApplication
        end = time.time() + seconds
        while time.time() < end and not cond():
            QApplication.processEvents()
            time.sleep(0.02)
        return cond()

    def test_game_window_and_read_button(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            import i18n
            from library import Library
            QApplication.instance() or QApplication([])
            i18n.set_language("en")
            tmp = Path(tmp)
            cloud = tmp / "cloud"
            cloud.mkdir()
            game_dir = tmp / "games" / "Ooe"
            game_dir.mkdir(parents=True)
            exe = game_dir / "ooe.exe"
            exe.write_bytes(b"")
            lib = Library(cloud)
            g = lib.add_game("大穢", "Laptop", exe=r"C:\Users\doge_\BL\Ooe\ooe.exe", save_dir=r"C:\Users\doge_\BL\Ooe\savedata")
            lib.set_paths(g.game_id, "PC", exe=str(exe))
            settings = LocalSettings("PC", str(cloud))
            want = str(game_dir / "savedata")

            # the game window fills in the other device's location, translated to this device
            d = app.GameDialog(lib, settings, lib.get(g.game_id))
            save_e = d.fields["PC"][1]
            self.assertTrue(self._wait(lambda: save_e.text() == want))
            self.assertIn("other device", d.save_hint.text())
            d.reject()

            # Read with no save folder: offered first; "Use This Folder" saves it, then the game starts
            w = app.MainWindow(settings)
            started = []
            fake = mock.MagicMock()
            fake.return_value.start.side_effect = lambda: started.append(True)
            try:
                w.refresh()
                with mock.patch.object(app, "message", return_value="use") as asked, \
                        mock.patch.object(app, "PlayWorker", fake):
                    w.play_selected(gid=g.game_id)
                    self.assertTrue(self._wait(lambda: started))
                self.assertIn(want, asked.call_args[0][1])
                self.assertEqual(lib.get(g.game_id).paths_for("PC").save_dir, want)
            finally:
                w.worker = None
                w.close()


class SilentSyncInTheWindow(unittest.TestCase):
    """With Ikemen Reader open, saves the other device uploads arrive without pressing Read."""

    def test_downloads_skips_open_games_and_asks_once_about_conflicts(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            import i18n
            from library import Library
            from sync_engine import SyncEngine
            QApplication.instance() or QApplication([])
            i18n.set_language("en")
            tmp = Path(tmp)
            cloud = tmp / "cloud"
            cloud.mkdir()
            exe = tmp / "game" / "game.exe"
            exe.parent.mkdir()
            exe.write_bytes(b"")
            pc_saves, laptop_saves = tmp / "pc_saves", tmp / "laptop_saves"
            lib = Library(cloud)
            g = lib.add_game("Slow Damage", "PC", exe=str(exe), save_dir=str(pc_saves))
            lib.set_paths(g.game_id, "Laptop", exe=str(exe), save_dir=str(tmp / "laptop_saves"))   # set up on both
            laptop = SyncEngine(cloud, tmp / "laptop_state")
            (laptop_saves / "0001").mkdir(parents=True)
            (laptop_saves / "0001" / "frames.npf").write_text("laptop progress", "utf-8")
            laptop.before_launch(g.game_id, laptop_saves)                  # the laptop uploads

            w = app.MainWindow(LocalSettings("PC", str(cloud)))

            def checked():
                """Refresh, then wait until every check (including re-checks it triggers) has finished."""
                w.refresh()
                end, quiet_since = time.time() + 20, None
                while time.time() < end:
                    QApplication.processEvents()
                    busy = w.checker is not None and w.checker.isRunning() or w._recheck
                    quiet_since = None if busy else (quiet_since or time.time())
                    if quiet_since and time.time() - quiet_since > 0.4:
                        break
                    time.sleep(0.02)
            try:
                # a game open outside Ikemen Reader: nothing is downloaded underneath it
                with mock.patch.object(app.launcher, "already_running", return_value=True):
                    checked()
                self.assertFalse((pc_saves / "0001" / "frames.npf").exists())
                self.assertEqual(w.sync_states[g.game_id], "pull")

                # closed: the routine poll (no refresh, nothing new in the cloud) brings it in by itself
                w._live_poll()
                end = time.time() + 20
                while time.time() < end and w.sync_states.get(g.game_id) != "synced":
                    QApplication.processEvents()
                    time.sleep(0.02)
                self.assertEqual((pc_saves / "0001" / "frames.npf").read_text("utf-8"), "laptop progress")
                self.assertEqual(w.sync_states[g.game_id], "synced")
                self.assertIn("Downloaded newer saves for: Slow Damage", w.statusBar().currentMessage())

                # changed on both devices: asked once; Later holds until the other device changes again
                (pc_saves / "0001" / "frames.npf").write_text("pc route", "utf-8")
                (laptop_saves / "0001" / "frames.npf").write_text("laptop route", "utf-8")
                laptop.sync_if_safe(g.game_id, laptop_saves)
                with mock.patch.object(app, "message", return_value="later") as asked:
                    checked()
                    checked()
                    self.assertEqual(asked.call_count, 1)
                    self.assertIn("Slow Damage", asked.call_args[0][1])
                    w._conflicts_snoozed = set()                            # (what new news from the laptop does)
                    checked()
                    self.assertEqual(asked.call_count, 2)
                self.assertEqual((pc_saves / "0001" / "frames.npf").read_text("utf-8"), "pc route")   # untouched
            finally:
                w.close()


class Version101QuickFixes(unittest.TestCase):
    """1.0.1: no "..." on menu items, Cancel always on the far left, VNDB survives closed connections."""

    def test_no_trailing_dots_on_menu_items(self):
        import i18n                 # (progress texts like "Searching..." keep theirs)
        for key, pair in i18n.S.items():
            if not key.startswith("menu."):
                continue
            for text in pair:
                self.assertFalse(text.rstrip().endswith(("...", "…")), f"{key}: {text}")

    def test_cancel_on_the_left_main_action_on_the_right(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QDialog, QPushButton
        import app
        QApplication.instance() or QApplication([])
        cases = [([("yes", "Remove"), ("no", "Cancel")], ["Cancel", "Remove"], "Remove"),
                 ([("no", "Cancel"), ("yes", "Confirm")], ["Cancel", "Confirm"], "Cancel"),
                 ([("setup", "Set Up"), ("new", "Add as New"), ("cancel", "Cancel")],
                  ["Cancel", "Add as New", "Set Up"], "Set Up"),
                 ([("now", "Choose Now"), ("later", "Later")], ["Later", "Choose Now"], "Choose Now")]
        for buttons, order, default in cases:
            seen = {}

            def fake_exec(d):
                d.layout().activate()
                bs = sorted(d.findChildren(QPushButton), key=lambda b: b.geometry().x())
                seen["order"] = [b.text() for b in bs]
                seen["default"] = [b.text() for b in bs if b.isDefault()]
                return 0
            with mock.patch.object(QDialog, "exec", fake_exec):
                app.message(None, "text", buttons=buttons)
            self.assertEqual(seen["order"], order)
            self.assertEqual(seen["default"], [default])

    def test_vndb_replaces_every_closed_connection(self):
        import queue
        from unittest import mock
        import vndb

        class Dead:
            timeout = 1
            def request(self, *a, **k): raise ConnectionResetError("closed by the server")
            def close(self): pass

        class Alive:
            timeout = 1
            def request(self, *a, **k): pass
            def getresponse(self):
                return mock.Mock(status=200, will_close=True, read=lambda: b"ok")
            def close(self): pass

        pool = queue.LifoQueue(maxsize=6)
        for _ in range(6):
            pool.put((Dead(), time.monotonic()))
        with mock.patch.object(vndb, "_idle", lambda host: pool), \
                mock.patch.object(vndb.http.client, "HTTPSConnection", lambda *a, **k: Alive()):
            self.assertEqual(vndb._request("GET", "https://api.vndb.org/x"), b"ok")
        self.assertTrue(pool.empty())


class SteamStyleGrid(unittest.TestCase):
    """1.0.1: covers stretch to fill each row, never below their smallest size, with the same gap at both
    edges as between covers, at any window width."""

    def test_rows_filled_with_equal_edges(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            from library import Library
            qa = QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            exe = Path(tmp) / "g.exe"
            exe.write_bytes(b"")
            for i in range(20):
                lib.add_game(f"Game {i:02d}", "PC", exe=str(exe))
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.show()
                w.refresh()
                for width in (1150, 1234, 1400, 1777, 2200):
                    w.resize(width, 800)
                    for _ in range(5):
                        qa.processEvents()
                    g = w.grid
                    vp = g.viewport().mapTo(g, app.QPoint(0, 0)).x()
                    covers = [app.CardDelegate.cover_rect(g.visualItemRect(g.item(i)), 0) for i in range(g.count())]
                    row = [c for c in covers if abs(c.y() - covers[0].y()) < 1]
                    left, right = row[0].x() + vp, g.width() - (row[-1].x() + row[-1].width() + vp)
                    gaps = {round(row[i + 1].x() - (row[i].x() + row[i].width())) for i in range(len(row) - 1)}
                    self.assertGreaterEqual(app.GRID["cover_w"], app.COVER_W)
                    self.assertLess(app.GRID["cover_w"], app.COVER_W + app.COVER_W + app.GRID_GAP)
                    self.assertEqual(gaps, {app.GRID_GAP}, width)
                    self.assertLessEqual(abs(left - right), 2, width)
                    self.assertLessEqual(abs(left - app.GRID_GAP), 3, width)
                    # one more column would not have fitted at the smallest size
                    self.assertLess(g.width() - app.GRID_GAP, (len(row) + 1) * (app.COVER_W + app.GRID_GAP), width)
            finally:
                w.close()


class AddedGameTitles(unittest.TestCase):
    """1.0.1: engine names in the .exe are ignored; the better of folder / .exe name is used."""

    def test_engine_names(self):
        from titles import is_engine_name
        for n in ["AdvGame Module", "codeX RScript", "TVP(KIRIKIRI) Z core / Scripting Platform for Win32",
                  "Stuff スクリプトエンジン", "AVGEngineV2"]:
            self.assertTrue(is_engine_name(n), n)
        for n in ["CROSS†CHANNEL", "スロウ・ダメージ 普及版", "Summer Pockets", "Corepunk Stories"]:
            self.assertFalse(is_engine_name(n), n)

    def test_folder_or_exe_name(self):
        from titles import guess_title
        cases = {                                   # (folder path, exe name) -> title (fake .exes have no info)
            ("Albatross/信天翁航海録", "信天翁航海録.exe"): "信天翁航海録",
            ("Kagerou/霞外籠逗留記", "kagerou.exe"): "霞外籠逗留記",
            ("ooe ver2.0", "大穢-完全版-.exe"): "大穢",
            ("Relirium/Reli", "Relirium.exe"): "Relirium",
            ("Mama/Data", "Start.exe"): "Mama",
            ("Mamatomo/ママトモ", "Start.exe"): "ママトモ",
            ("Tsukutori/『つくとり』", "TUKU.EXE"): "つくとり",
            ("Black Sheep Town/BLACK SHEEP TOWN", "Bst.exe"): "BLACK SHEEP TOWN",
            ("11Gatsu/11月のアルカディア", "BGI.exe"): "11月のアルカディア",
        }
        with tempfile.TemporaryDirectory() as tmp:
            for (folder, exe), want in cases.items():
                d = Path(tmp) / folder
                d.mkdir(parents=True, exist_ok=True)
                (d / exe).write_bytes(b"")
                self.assertEqual(guess_title(str(d / exe)), want, folder)

    def test_cleaning(self):
        self.assertEqual(clean_title("大穢-完全版-"), "大穢")
        self.assertEqual(clean_title("大穢-前編-"), "大穢-前編-")              # a real subtitle stays
        self.assertEqual(clean_title("『つくとり』"), "つくとり")
        self.assertEqual(clean_title("【A】 x 【B】"), "【A】 x 【B】")


class SearchByOtherNames(unittest.TestCase):
    """Search finds a game by its title or any name VNDB knows it by (English title, romaji, aliases)."""

    def test_matching(self):
        from library import Game, matches_search
        g = Game("nora", "ノラと皇女と野良猫ハート", names=["Nora to Oujo to Noraneko Heart", "Noratoto",
                 "The Princess, the Stray Cat, and Matters of the Heart"])
        for q in ("ノラと", "noratoto", "nora to oujo", "NORATOTO", "ｎｏｒａｔｏｔｏ", "stray cat", "のら", "  "):
            self.assertTrue(matches_search(g, q), q)
        for q in ("sakura", "サクラ", "xyz"):
            self.assertFalse(matches_search(g, q), q)
        # typing has to follow a name: word starts (Latin), the start (kana), anywhere (kanji)
        self.assertFalse(matches_search(g, "oratoto"))                       # not a word start
        self.assertFalse(matches_search(g, "q"))                             # no word starts with q
        self.assertTrue(matches_search(g, "t"))                              # The / to
        self.assertFalse(matches_search(g, "s"))                             # one letter: only names starting with it
        self.assertTrue(matches_search(g, "st"))                             # two: any word (Stray)
        self.assertFalse(matches_search(Game("b", "Bunny's ママ代行サービス", names=["Bunny's Rent-a-Mom Agency"]), "a"))
        self.assertTrue(matches_search(Game("b", "Bunny's ママ代行サービス", names=["Bunny's Rent-a-Mom Agency"]), "ag"))
        # numbers: that whole number, anywhere ("1" is volume 1, not 11)
        self.assertTrue(matches_search(Game("g", "ガールズ・ブック・メイカー 1"), "1"))
        self.assertFalse(matches_search(Game("a", "11月のアルカディア"), "1"))
        self.assertTrue(matches_search(Game("a", "11月のアルカディア"), "11"))
        self.assertFalse(matches_search(Game("g", "Girls Book Maker 11"), "maker 1"))
        self.assertTrue(matches_search(Game("g", "Girls Book Maker 1"), "maker 1"))
        # several words: each starts a later word, in order, gaps allowed
        st = Game("s", "Sakura no Toki")
        self.assertTrue(matches_search(st, "sakura toki"))
        self.assertTrue(matches_search(st, "sak to"))
        self.assertFalse(matches_search(st, "toki sakura"))
        self.assertTrue(matches_search(Game("g", "Girls Book Maker ~Grimm to Sannin no Ohime-sama 1~"), "girls 1"))
        self.assertFalse(matches_search(Game("g", "Girls Book Maker ~Grimm to Sannin no Ohime-sama 1~"), "girls 11"))
        self.assertTrue(matches_search(g, "heart"))
        self.assertTrue(matches_search(g, "皇女"))                           # kanji: anywhere
        self.assertFalse(matches_search(g, "とおうじょ"))                     # kana: only from the start
        bunny = Game("b", "Bunny's ママ代行サービス")
        self.assertFalse(matches_search(bunny, "まま"))
        self.assertTrue(matches_search(bunny, "bunnys"))
        self.assertTrue(matches_search(bunny, "bunny's まま"))
        self.assertTrue(matches_search(Game("k", "霞外籠逗留記"), "記"))
        self.assertTrue(matches_search(Game("a", "11月のアルカディア"), "11"))
        self.assertTrue(matches_search(Game("s", "サクラノ刻"), "さくらの"))      # katakana = hiragana
        self.assertTrue(matches_search(Game("s", "Sakura no Toki"), "sakuranotoki"))

    def test_names_survive_damaged_files_and_round_trip(self):
        from library import Game
        g = Game.from_json(json.dumps({"game_id": "a", "title": "A", "names": ["B", 3, "", None, "C"], "names_for": "v1"}))
        self.assertEqual((g.names, g.names_for), (["B", "C"], "v1"))
        self.assertEqual(Game.from_json(json.dumps({"game_id": "a", "title": "A", "names": "oops"})).names, [])
        self.assertEqual(Game.from_json(g.to_json()).names, ["B", "C"])

    def test_vndb_names_answer(self):
        from unittest import mock
        import vndb
        answer = {"results": [{"id": "v18148", "title": "Nora to Oujo to Noraneko Heart", "alttitle": "ノラと皇女と野良猫ハート",
                               "aliases": ["Noratoto", "noratoto", 5],
                               "titles": [{"lang": "en", "title": "The Princess, the Stray Cat, and Matters of the Heart", "latin": None},
                                          {"lang": "ja", "title": "ノラと皇女と野良猫ハート", "latin": "Nora to Oujo to Noraneko Heart"}]},
                              "junk"]}
        with mock.patch.object(vndb, "_post", return_value=answer) as post:
            got = vndb.names(["v18148", "v637", "../x"])
        self.assertEqual(got, {"v18148": ["Nora to Oujo to Noraneko Heart", "ノラと皇女と野良猫ハート",
                                          "The Princess, the Stray Cat, and Matters of the Heart", "Noratoto"]})
        self.assertEqual(post.call_args[0][1]["filters"], ["or", ["id", "=", "v18148"], ["id", "=", "v637"]])


class HoverTitles(unittest.TestCase):
    """1.0.1: English breaks between words (or after † ・ etc.), Japanese follows its line-break rules and
    shrinks one size when the last line would hold only one or two characters."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])

    def test_wrapping(self):
        import app
        from PySide6.QtGui import QFontMetrics
        f = app.ui_font(11)
        fm = QFontMetrics(f)
        self.assertEqual(app.wrap_title("CROSS†CHANNEL", fm, fm.horizontalAdvance("CROSS†CHAN"), 2),
                         ["CROSS†", "CHANNEL"])
        self.assertEqual(app.wrap_title("Cross Channel", fm, fm.horizontalAdvance("Cross Chan"), 2),
                         ["Cross", "Channel"])
        w = fm.horizontalAdvance("あ" * 8) + 2
        for text, second in [("あいうえおかきく。けこ", "く。けこ"), ("あいうえおかき「くけこ", "「くけこ"),
                             ("あいうえおかきくーけこ", "くーけこ"), ("あいうえおかきくっけこ", "くっけこ")]:
            self.assertEqual(app.wrap_title(text, fm, w, 3)[1], second, text)
        font, lines = app.fit_title("あいうえおかきくけ", f, w, 2)            # one character over: one size down
        self.assertEqual((font.pointSize(), lines), (10, ["あいうえおかきくけ"]))
        font, lines = app.fit_title("Supercalifragilistic expialidociousness", f, w, 2)
        self.assertEqual(font.pointSize(), 11)                                # English never shrinks


class LavenderAndBookmarks(unittest.TestCase):
    """1.0.1: lavender = in sync but only set up on this device; folder lights split by colour;
    bookmarks setting remembered and shared."""

    def test_solo_state(self):
        import app
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib = Library(root / "cloud")
            saves = root / "saves"
            write(saves, "s.dat", "x")
            g = lib.add_game("Solo", "PC", exe=str(root / "g.exe"), save_dir=str(saves))
            eng = SyncEngine(root / "cloud", root / "state")
            eng.after_exit(g.game_id, str(saves))
            self.assertEqual(app.sync_state(eng, lib.get(g.game_id), "PC"), "solo")
            lib.set_paths(g.game_id, "Laptop", exe=r"C:\Games\g.exe", save_dir=r"C:\Saves")
            self.assertEqual(app.sync_state(eng, lib.get(g.game_id), "PC"), "synced")
            self.assertEqual(app.STATE_LOOK["solo"][0], "lavender")

    def test_folder_colours_in_order(self):
        import app
        from types import SimpleNamespace
        games = [SimpleNamespace(game_id=x) for x in "abcd"]
        fake = SimpleNamespace(folder_games={"f": games}, light_state=None)
        states = {"a": "synced", "b": "conflict", "c": "solo", "d": "synced"}
        fake.light_state = lambda gid: states[gid]
        self.assertEqual(app.MainWindow.folder_colours(fake, "f"), ["red", "lavender", "green"])

    def test_bookmark_setting(self):
        import devsync
        from library import load_local_settings, save_local_settings
        with tempfile.TemporaryDirectory() as tmp:
            s = LocalSettings("PC", tmp)
            self.assertTrue(s.show_bookmarks)
            s.show_bookmarks = False
            save_local_settings(s, Path(tmp))
            self.assertFalse(load_local_settings(Path(tmp)).show_bookmarks)
            self.assertIn("show_bookmarks", devsync.SHARED)


class Updates(unittest.TestCase):
    """1.0.1: new versions from GitHub releases: newer-than check, verified download, unpacking, and the
    helper that swaps the files once Ikemen Reader has closed (run for real on Windows)."""

    def test_versions(self):
        import updater
        self.assertTrue(updater.is_newer("1.0.1", "1.0.0"))
        self.assertTrue(updater.is_newer("v1.1", "1.0.9"))
        self.assertFalse(updater.is_newer("1.0.0", "1.0.0"))
        self.assertFalse(updater.is_newer("1.0", "1.0.0"))
        self.assertFalse(updater.is_newer("nightly", "1.0.0"))

    def test_pick_release(self):
        import updater
        data = {"tag_name": "1.0.1", "body": "## Ikemen Reader 1.0.1\n- Bookmarks",
                "assets": [{"name": "notes.txt"},
                           {"name": "Ikemen-Reader-1.0.1.zip", "size": 5, "digest": "sha256:ABC",
                            "browser_download_url": "https://example/zip"}]}
        r = updater.pick_release(data, "1.0.0")
        self.assertEqual((r.version, r.name, r.size, r.sha256), ("1.0.1", "Ikemen-Reader-1.0.1.zip", 5, "abc"))
        self.assertIsNone(updater.pick_release(data, "1.0.1"))                      # not newer
        self.assertIsNone(updater.pick_release(dict(data, prerelease=True), "1.0.0"))
        self.assertIsNone(updater.pick_release({"tag_name": "2.0"}, "1.0.0"))      # no program zip

    def _zip(self, folder: Path, exe_text: str) -> Path:
        import zipfile
        z = folder / "Ikemen-Reader-9.9.9.zip"
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("Ikemen Reader/Ikemen Reader.exe", exe_text)
            f.writestr("Ikemen Reader/_internal/lib.dll", "new lib")
        return z

    def test_download_checks_the_fingerprint(self):
        import hashlib
        import updater
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = self._zip(tmp, "new exe")
            data = src.read_bytes()
            good = updater.Release("9.9.9", "", src.as_uri(), "dl.zip", len(data), hashlib.sha256(data).hexdigest())
            out = tmp / "update"
            out.mkdir()
            self.assertEqual(updater.download(good, out).read_bytes(), data)
            bad = updater.Release("9.9.9", "", src.as_uri(), "dl2.zip", len(data), "0" * 64)
            with self.assertRaises(ValueError):
                updater.download(bad, out)
            self.assertFalse((out / "dl2.zip").exists())                              # nothing damaged left behind
            new = updater.stage(out / "dl.zip", out)
            self.assertEqual((new / "Ikemen Reader.exe").read_text(), "new exe")
            self.assertEqual(updater.staged(out), new)

    def test_unsafe_zip_refused(self):
        import zipfile
        import updater
        with tempfile.TemporaryDirectory() as tmp:
            z = Path(tmp) / "evil.zip"
            with zipfile.ZipFile(z, "w") as f:
                f.writestr("../outside.txt", "x")
                f.writestr("Ikemen Reader/Ikemen Reader.exe", "x")
            with self.assertRaises(ValueError):
                updater.stage(z, Path(tmp) / "update")
            self.assertFalse((Path(tmp) / "outside.txt").exists())

    @unittest.skipUnless(IS_WINDOWS, "the swap helper is PowerShell")
    def test_helper_swaps_after_the_program_closes(self):
        import updater
        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "Ikemen Reader ほん"          # (a Japanese folder name, like the owner's)
            (program / "_internal").mkdir(parents=True)
            (program / "Ikemen Reader.exe").write_text("old exe")
            (program / "_internal" / "lib.dll").write_text("old lib")
            (program / "Google Drive.lnk").write_text("keep me")
            folder = updater.update_dir(Path(tmp) / "appdata", program)
            self.assertEqual(folder, program / "update")
            z = self._zip(Path(tmp), "new exe")
            (folder / z.name).write_bytes(z.read_bytes())                           # the download
            new = updater.stage(folder / z.name, folder)
            # "Ikemen Reader" = a process that closes after a moment
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
            # started exactly as the app starts it (hidden, on its own: it once quit at once when "detached")
            updater.install_on_exit(folder, new, restart=False, program=program, pid=proc.pid)
            time.sleep(0.8)
            self.assertEqual((program / "Ikemen Reader.exe").read_text(), "old exe")   # still waiting for it to close
            proc.wait()
            end = time.time() + 30
            while time.time() < end and not (folder / "update.log").exists():
                time.sleep(0.2)
            time.sleep(0.5)
            self.assertEqual((program / "Ikemen Reader.exe").read_text(), "new exe")
            self.assertEqual((program / "_internal" / "lib.dll").read_text(), "new lib")
            self.assertEqual((program / "Google Drive.lnk").read_text(), "keep me")  # not ours: untouched
            self.assertEqual((folder / "previous" / "Ikemen Reader.exe").read_text(), "old exe")   # backup
            self.assertFalse((folder / "new").exists())                              # tidied up
            self.assertEqual(list(folder.glob("*.zip")), [])
            self.assertIn("updated", (folder / "update.log").read_text("utf-8"))


class StricterSaveSuggestions(unittest.TestCase):
    """1.0.1: a folder must be named after the WHOLE game (not one shared word) and hold save files; two
    candidates = not sure = nothing. (Cases from the owner's own library.)"""

    def setUp(self):
        import detect
        self.detect = detect
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.docs = self.root / "Documents"
        self.low = self.root / "LocalLow"
        for d in (self.docs, self.low):
            d.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def suggest(self, game: Path, names):
        game.mkdir(parents=True, exist_ok=True)
        return self.detect.suggest_save_folder(str(game), list(names), None, lambda p: True,
                                               home=self.root, roots=[game, self.docs, self.low])

    def test_one_shared_word_is_not_enough(self):
        write(self.docs / "BLACKcyc" / "SaDisticBlooD_savefiles", "save01.dat", "x")    # another game's saves
        self.assertIsNone(self.suggest(self.root / "D" / "BLACK SHEEP TOWN", ["BLACK SHEEP TOWN", "Bst"]))
        s = self.suggest(self.root / "D" / "SaDistic BlooD", ["SaDistic BlooD", "SaDisticBlooD"])
        self.assertEqual(s.path, str(self.docs / "BLACKcyc" / "SaDisticBlooD_savefiles"))

    def test_down_to_the_save_folder_past_engine_clutter(self):
        bst = self.low / "MoralityLine" / "Bst"
        write(bst, "Player.log", "log")
        write(bst / "Unity", "cache.bin", "x")
        write(bst / "Utage" / "Savebst", "save1.utage", "x")
        s = self.suggest(self.root / "D" / "BLACK SHEEP TOWN", ["BLACK SHEEP TOWN", "Bst"])
        self.assertEqual(s.path, str(bst / "Utage" / "Savebst"))

    def test_save_words_after_the_name(self):
        write(self.docs / "魔導書の司書 セーブデータ", "save.dat", "x")
        s = self.suggest(self.root / "D" / "魔導書の司書", ["魔導書の司書"])
        self.assertEqual(s.path, str(self.docs / "魔導書の司書 セーブデータ"))

    def test_two_candidates_means_nothing(self):
        write(self.docs / "slowdamage" / "1.10", "system.dat", "x")
        write(self.low / "NITRO CHiRAL" / "slowdamage 普及版" / "1.10", "system.dat", "x")
        self.assertIsNone(self.suggest(self.root / "D" / "スロウ・ダメージ", ["スロウ・ダメージ", "slowdamage"]))

    def test_only_logs_and_settings_are_not_saves(self):
        write(self.docs / "Indigo", "config.ini", "x")
        write(self.docs / "Indigo", "error.log", "x")
        self.assertIsNone(self.suggest(self.root / "D" / "Indigo", ["Indigo"]))


class RegistryConfidence(unittest.TestCase):
    """1.0.1: a key named after the whole game is stored without asking; part of a name / only the maker's
    name = offered unticked; Windows', programs' and our own keys never."""

    def test_levels(self):
        import detect
        names = ["CROSS†CHANNEL", "CROSSCHANNEL", "CROSS†CHANNEL"]
        self.assertEqual(detect.registry_confidence("WillPlus", "CROSS†CHANNEL", names), "sure")
        self.assertEqual(detect.registry_confidence("WillPlus", "CrossChannel_Config", names), "maybe")
        self.assertEqual(detect.registry_confidence("CROSSCHANNEL", "Settings", names), "maybe")   # maker only
        bst = ["BLACK SHEEP TOWN", "Bst", "BLACK SHEEP TOWN"]
        self.assertEqual(detect.registry_confidence("MoralityLine", "Bst", bst), "sure")
        self.assertEqual(detect.registry_confidence("BLACKcyc", "SaDisticBlooD", bst), "maybe")  # one shared word
        self.assertIsNone(detect.registry_confidence("Microsoft", "Black Sheep Town", bst))
        self.assertIsNone(detect.registry_confidence("IkemenReader", "BLACK SHEEP TOWN", bst))
        self.assertIsNone(detect.registry_confidence("Valve", "Steam", bst))

    def test_window_sections(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import app
        import detect
        QApplication.instance() or QApplication([])
        game = app.Game(game_id="g", title="CROSS†CHANNEL")
        folder = detect.Candidate(r"C:\Saves\CROSS", "folder", 5, 3)
        sure = detect.Candidate(r"HKEY_CURRENT_USER\Software\WillPlus\CROSS†CHANNEL", "registry", 5, 1, sure=True)
        maybe = detect.Candidate(r"HKEY_CURRENT_USER\Software\CROSSCHANNEL\Settings", "registry", 3, 1)
        with tempfile.TemporaryDirectory() as tmp:
            d = app.DetectDialog(game, [folder, sure, maybe], Path(tmp))
            self.assertEqual(d._registry_choice(), sure.path)           # stored without asking
            self.assertTrue(all(b.isHidden() for b in d.reg_boxes))
            d = app.DetectDialog(game, [folder, maybe], Path(tmp))
            self.assertEqual(d._registry_choice(), "")                  # not sure: offered, unticked
            d.reg_boxes[0].setChecked(True)
            self.assertEqual(d._registry_choice(), maybe.path)
            d = app.DetectDialog(game, [maybe], Path(tmp))
            self.assertEqual(d._registry_choice(), maybe.path)          # the only lead: ticked


class SortingAndCustomOrder(unittest.TestCase):
    """1.0.1: Folders First / Mixed / Last, Custom order (library and per folder, synced in the cloud folder),
    each folder's own sort (Same as Library until changed), drag between covers to arrange."""

    def test_library_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(Path(tmp))
            a = lib.add_game("A", "PC")
            b = lib.add_game("B", "PC")
            fid = lib.create_folder("Series")
            lib.set_folder(a.game_id, fid)
            lib.set_folder_sort(fid, "title")
            lib.set_folder_order(fid, [a.game_id])
            lib.rename_folder(fid, "Renamed")                       # renaming keeps sort + order
            self.assertEqual((lib.folder_sort(fid), lib.folder_order(fid)), ("title", [a.game_id]))
            lib.set_library_order([b.game_id, "folder:" + fid])
            lib.set_folder(b.game_id, fid)                         # into the folder: leaves the library's order
            self.assertEqual(lib.library_order(), ["folder:" + fid])
            lib.set_folder(a.game_id, "")                          # out of it: leaves the folder's order
            self.assertEqual(lib.folder_order(fid), [])
            lib.folders_file.write_text('{"x": {"name": 5, "sort": "weird", "order": "nope"}}', "utf-8")
            self.assertEqual((lib.folder_sort("x"), lib.folder_order("x")), ("", []))   # damaged: defaults

    def test_in_order(self):
        import app
        self.assertEqual(app.in_order(["n1", "b", "a", "n2"], lambda x: x, ["a", "gone", "b"]), ["a", "b", "n1", "n2"])

    def _window(self, tmp, titles, folder_titles=()):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import app
        import i18n
        qa = QApplication.instance() or QApplication([])
        i18n.set_language("en")
        cloud = Path(tmp) / "cloud"
        cloud.mkdir()
        lib = Library(cloud)
        ids = {}
        for i, t in enumerate(titles):
            g = lib.add_game(t, "PC")
            lib.update(g.game_id, last_played=1000 - i)               # first title = most recent
            ids[t] = g.game_id
        fid = None
        if folder_titles:
            fid = lib.create_folder("Series")
            for i, t in enumerate(folder_titles):
                g = lib.add_game(t, "PC")
                lib.update(g.game_id, last_played=500 - i)
                lib.set_folder(g.game_id, fid)
                ids[t] = g.game_id
        w = app.MainWindow(LocalSettings("PC", str(cloud)))
        w.show()
        w.resize(1300, 800)
        w.refresh()
        for _ in range(5):
            qa.processEvents()
        return w, lib, ids, fid

    def test_search_finds_folders_by_name(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            w, lib, ids, fid = self._window(tmp, ["Alpha", "Beta"], ["Saga One", "Saga Two"])
            try:
                import app
                shown = lambda: [w.grid.item(i).data(app.GAME_ID_ROLE) for i in range(w.grid.count())]
                f = app.FOLDER_PREFIX + fid
                search_now(w, "ser")                          # the folder is called "Series"
                self.assertEqual(shown(), [f])                   # its games don't match "ser" themselves
                search_now(w, "saga")
                self.assertEqual(sorted(shown()), sorted([ids["Saga One"], ids["Saga Two"]]))
                search_now(w, "s")                            # both: the folder and its games
                self.assertIn(f, shown())
                w.settings.status_filter = "finished"            # a status filter: only folders holding one
                search_now(w, "series")
                self.assertEqual(shown(), [])
                lib.update(ids["Saga One"], status="finished")
                w.refresh()
                self.assertEqual(shown(), [f])
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_folder_places_and_custom(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:     # (a settings share may still be writing)
            w, lib, ids, fid = self._window(tmp, ["A", "B", "C"], ["F1", "F2"])
            try:
                import app
                shown = lambda: [w.grid.item(i).data(app.GAME_ID_ROLE) for i in range(w.grid.count())]
                f = app.FOLDER_PREFIX + fid
                self.assertEqual([w.sort.itemData(i) for i in range(w.sort.count()) if w.sort.itemData(i)],
                                 ["title", "hours", "recent", "place:first", "place:last", "place:mixed", "custom"])
                self.assertEqual(shown(), [ids["A"], ids["B"], ids["C"], f])     # mixed in, by its latest game
                w.choose_sort("place:first")                                       # a setting: stays a sort
                self.assertEqual(shown(), [f, ids["A"], ids["B"], ids["C"]])
                self.assertEqual(w.sort.currentData(), "recent")
                w.choose_sort("title")                                             # remembered in the other sorts
                self.assertEqual(shown()[0], f)
                w.choose_sort("place:last")
                self.assertEqual(shown()[-1], f)
                w._dragging = ids["C"]                                             # drag: My Arrangement
                w._move_to(ids["C"], 0)
                w._dragging = None
                self.assertEqual((w.settings.sort, w.settings.last_sort), ("custom", "title"))
                self.assertEqual(shown(), [ids["C"], ids["A"], ids["B"], f])
                w.choose_sort("place:first")                                       # in My Arrangement: folders moved
                self.assertEqual(shown(), [f, ids["C"], ids["A"], ids["B"]])       # games keep your order
                self.assertEqual(w.sort.currentData(), "custom")                   # (never switches sort)
                w.choose_sort("hours")
                w.choose_sort("custom")                                            # your arrangement is back
                self.assertEqual(shown(), [f, ids["C"], ids["A"], ids["B"]])
                new = lib.add_game("New", "PC")
                w.refresh()
                self.assertEqual(shown()[-1], new.game_id)                          # new games at the end
            finally:
                w.close()

    def test_mixed_in_my_arrangement_takes_the_sorts_spots(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            w, lib, ids, fid = self._window(tmp, ["A", "B", "C", "D"], ["F1"])
            try:
                import app
                shown = lambda: [w.grid.item(i).data(app.GAME_ID_ROLE) for i in range(w.grid.count())]
                f = app.FOLDER_PREFIX + fid
                for t, h in (("A", 1), ("B", 50), ("C", 2), ("D", 3), ("F1", 10)):
                    lib.add_playtime(ids[t], "PC", h * 3600.0)
                w.refresh()
                w.choose_sort("hours")                                              # B 50, F1 10, D 3, C 2, A 1
                self.assertEqual(shown(), [ids["B"], f, ids["D"], ids["C"], ids["A"]])
                w.choose_sort("place:last")
                w._dragging = ids["A"]
                w._move_to(ids["A"], 0)                                             # My Arrangement: A B D C F
                w._dragging = None
                self.assertEqual(shown(), [ids["A"], ids["B"], ids["D"], ids["C"], f])
                w.choose_sort("place:mixed")                                        # F back to Most Played's 2nd spot
                self.assertEqual(shown(), [ids["A"], f, ids["B"], ids["D"], ids["C"]])
            finally:
                w.close()

    def test_folder_options_greyed_without_folders(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            w, lib, ids, fid = self._window(tmp, ["A", "B"])
            try:
                model = w.sort.model()
                for p in ("mixed", "first", "last"):
                    self.assertFalse(model.item(w.sort.findData("place:" + p)).isEnabled())
                self.assertTrue(model.item(w.sort.findData("custom")).isEnabled())
            finally:
                w.close()

    def test_folder_own_sort(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:     # (a settings share may still be writing)
            w, lib, ids, fid = self._window(tmp, ["A"], ["Zeta", "Alpha", "Mid"])
            try:
                import app
                shown = lambda: [w.grid.item(i).data(app.GAME_ID_ROLE) for i in range(w.grid.count())]
                w.open_folder(fid)
                self.assertEqual(w.sort.currentData(), "follow")                   # untouched: Same as Library
                self.assertEqual(shown(), [ids["Zeta"], ids["Alpha"], ids["Mid"]])  # recent, like the library
                w.choose_sort("title")
                self.assertEqual(shown(), [ids["Alpha"], ids["Mid"], ids["Zeta"]])
                self.assertEqual(lib.folder_sort(fid), "title")
                w.close_folder()
                w.choose_sort("hours")                  # the library's sort changes...
                w.open_folder(fid)
                self.assertEqual(w.sort.currentData(), "title")                    # ...the folder keeps its own
                w._dragging = ids["Zeta"]
                w._move_to(ids["Zeta"], 0)                                         # arrange inside: Custom
                w._dragging = None
                self.assertEqual(lib.folder_sort(fid), "custom")
                self.assertEqual(shown()[0], ids["Zeta"])
                self.assertEqual(w.folder_games[fid][0].game_id, ids["Zeta"])     # the mosaic follows it
                w.choose_sort("follow")
                self.assertEqual(lib.folder_sort(fid), "")
            finally:
                w.close()

    def test_onto_or_between(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:     # (a settings share may still be writing)
            w, lib, ids, fid = self._window(tmp, ["A", "B", "C"])
            try:
                import app
                from PySide6.QtCore import QPoint
                rect = lambda k: app.CardDelegate.cover_rect(w.grid.visualItemRect(w.grid.item(k)), 0)
                w._dragging = ids["A"]
                c = rect(1)
                self.assertEqual(w._drop_spot(c.center().toPoint()), ("onto", ids["B"]))           # middle: folder
                spot = w._drop_spot(QPoint(int(c.right() + app.GRID_GAP / 2), int(c.center().y())))
                self.assertEqual(spot[:2], ("between", 2))                                         # gap: move
                self.assertIsNone(w._drop_spot(QPoint(int(c.left() - 4), int(c.center().y()))))   # next to itself
                search_now(w, "A")                                                               # searching:
                w._dragging = ids["A"]
                self.assertIsNone(w._drop_spot(QPoint(int(rect(0).right() + 10), int(rect(0).center().y()))))
            finally:
                w._dragging = None
                w.close()


class DragAndDropFeel(unittest.TestCase):
    """Moving something switches to My Arrangement starting from exactly what was shown; drops glide into
    place and the other covers slide aside (state is clean afterwards)."""

    def test_moving_a_folder_keeps_what_was_shown_and_drops_land(self):
        from PySide6.QtCore import QEventLoop, QPoint, QTimer
        t = SortingAndCustomOrder()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:     # (a settings share may still be writing)
            w, lib, ids, fid = t._window(tmp, ["A", "B", "C"], ["F1"])
            try:
                import app

                def wait(ms):
                    loop = QEventLoop()
                    QTimer.singleShot(ms, loop.quit)
                    loop.exec()
                f = app.FOLDER_PREFIX + fid
                w._move_to(f, 0)                           # dragged to the front: My Arrangement from what was shown
                self.assertEqual(w.settings.sort, "custom")
                self.assertEqual(lib.library_order()[0], f)
                # a real drop: lift, move between two covers, let go
                w._start_game_drag(ids["A"])
                w._drag_watch.stop()                          # (no real mouse button in a test)
                wait(200)
                vp = w.grid.viewport()
                c = app.CardDelegate.cover_rect(w.grid.visualItemRect(w.grid.item(2)), 0)
                spot = vp.mapToGlobal(QPoint(int(c.right() + app.GRID_GAP / 2), int(c.center().y())))
                w._move_game_drag(spot)
                w._end_game_drag(spot, drop=True)
                self.assertIsNotNone(w._landing)
                wait(450)
                self.assertIsNone(w._landing)
                self.assertEqual((w.slides, w.drag_dim, w._dim_gid), ({}, 0.0, None))
            finally:
                w.close()

class SortDropDownClicks(unittest.TestCase):
    """Picking options one after another by clicking (Qt's activated signal) always applies each pick, and
    the drop-downs' lists are layered windows (no white first frame) that slide open by themselves."""

    def test_consecutive_picks_apply(self):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt as QtNS
        import app
        t = SortingAndCustomOrder()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:     # (a settings share may still be writing)
            w, lib, ids, fid = t._window(tmp, ["A", "B"], ["F1"])
            try:
                f = app.FOLDER_PREFIX + fid
                shown = lambda: [w.grid.item(i).data(app.GAME_ID_ROLE) for i in range(w.grid.count())]

                def click(data):
                    w.sort.activated.emit(w.sort.findData(data))
                    for _ in range(3):
                        QApplication.processEvents()
                for pick, first in (("place:first", True), ("place:last", False), ("place:first", True)):
                    click(pick)
                    self.assertEqual(shown()[0] == f, first)
                    self.assertEqual(w.sort.currentData(), "recent")             # the sort stays
                click("title")
                self.assertEqual((w.sort.currentData(), shown()[0]), ("title", f))
                self.assertTrue(w.sort.view().parentWidget().testAttribute(QtNS.WA_TranslucentBackground))
                self.assertFalse(QApplication.isEffectEnabled(QtNS.UI_AnimateCombo))
            finally:
                w.close()

class SortDropDownRealClicks(unittest.TestCase):
    """A real mouse press + release on a choice in the open list picks that choice (the tick moves on press,
    without moving the highlight Qt uses on release)."""

    def test_press_and_release(self):
        from PySide6.QtCore import QEventLoop, QTimer
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt as QtNS
        import app
        t = SortingAndCustomOrder()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            w, lib, ids, fid = t._window(tmp, ["A", "B"], ["F1"])
            try:
                f = app.FOLDER_PREFIX + fid

                def wait(ms):
                    loop = QEventLoop()
                    QTimer.singleShot(ms, loop.quit)
                    loop.exec()
                for pick, first in (("place:first", True), ("place:last", False), ("title", None)):
                    w.sort.showPopup()
                    wait(250)
                    view = w.sort.view()
                    rect = view.visualRect(w.sort.model().index(w.sort.findData(pick), 0))
                    QTest.mouseClick(view.viewport(), QtNS.LeftButton, QtNS.NoModifier, rect.center())
                    wait(250)
                    if first is not None:
                        self.assertEqual(w.grid.item(0).data(app.GAME_ID_ROLE) == f, first)
                    else:
                        self.assertEqual(w.settings.sort, "title")
                    self.assertFalse(view.isVisible())
            finally:
                w.close()




class FolderArrangement(unittest.TestCase):
    """Inside a folder: untouched = Same as Library; moving a game gives the folder its own My Arrangement,
    even when the library itself is on My Arrangement."""

    def test_move_inside_a_folder(self):
        t = SortingAndCustomOrder()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            w, lib, ids, fid = t._window(tmp, ["A"], ["X", "Y", "Z"])
            try:
                w._dragging = ids["A"]
                w._move_to(ids["A"], 1)                       # the library: My Arrangement
                w._dragging = None
                w.open_folder(fid)
                self.assertEqual(w.sort.currentData(), "follow")      # untouched folder: Same as Library
                w._dragging = ids["Z"]
                w._move_to(ids["Z"], 0)
                w._dragging = None
                self.assertEqual((lib.folder_sort(fid), w.sort.currentData()), ("custom", "custom"))
            finally:
                w.close()


class AnkiButton(unittest.TestCase):
    """The easter egg's Anki button brings an open Anki forward instead of starting a second copy."""

    def test_recognises_anki(self):
        import outside

        class Proc:
            def __init__(self, exe, cmd=()):
                self._exe, self._cmd = exe, list(cmd)

            def exe(self):
                return self._exe

            def cmdline(self):
                return self._cmd

        setting = r"C:\Users\Me\AppData\Local\Programs\Anki\anki.exe"
        new_anki = Proc(r"C:\Users\Me\AppData\Local\AnkiProgramFiles\.venv\Scripts\pythonw.exe",
                        ["pythonw.exe", "-c", "import aqt, sys; sys.argv[0] = 'Anki'; aqt.run()"])
        old_anki = Proc(setting)
        ikemen = Proc(r"C:\Python314\python.exe", ["python.exe", "app.py"])
        other_python = Proc(r"C:\Users\Me\AppData\Local\AnkiProgramFiles\.venv\Scripts\python.exe", ["x.py"])
        self.assertTrue(outside._is_anki(new_anki, setting))
        self.assertTrue(outside._is_anki(old_anki, setting))
        self.assertFalse(outside._is_anki(ikemen, setting))
        self.assertFalse(outside._is_anki(other_python, setting))
        self.assertFalse(outside._is_anki(Proc(""), ""))

    def test_open_anki_is_shown_not_started(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QWidget
        import app
        QApplication.instance() or QApplication([])
        w = QWidget()
        w.settings = LocalSettings("PC", "x", anki_path=sys.executable)
        egg = app.EasterEgg(w)
        with mock.patch.object(app.outside, "show_anki", return_value=True), \
                mock.patch.object(app.outside, "popen") as start:
            egg._open_anki()
            start.assert_not_called()
        egg = app.EasterEgg(w)
        with mock.patch.object(app.outside, "show_anki", return_value=False), \
                mock.patch.object(app.outside, "popen") as start:
            egg._open_anki()
            start.assert_called_once()
        w.close()


class SmoothAnimations(unittest.TestCase):
    """The animation work of 2026-10-15: only covers in view are walked each frame, covers are prepared in the
    background, search / filter changes glide (new covers fade in, removed ones fade out), bookmarks fade."""

    def _window(self, tmp, n=40, art_every=2):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QImage, QColor
        import app
        import i18n
        QApplication.instance() or QApplication([])
        i18n.set_language("en")
        cloud = Path(tmp) / "cloud"
        cloud.mkdir()
        lib = Library(cloud)
        exe = Path(tmp) / "game.exe"
        exe.write_bytes(b"")
        for i in range(n):
            g = lib.add_game(f"Game {i:02d}" + (" Alpha" if i % 3 == 0 else ""), "PC", exe=str(exe))
            if i % art_every == 0:
                img = QImage(300, 420, QImage.Format_RGB32)
                img.fill(QColor(i * 5, 100, 150))
                f = Path(tmp) / f"c{i}.png"
                img.save(str(f))
                lib.set_artwork(g.game_id, str(f))
        w = app.MainWindow(LocalSettings("PC", str(cloud)))
        w.resize(1200, 833)
        w.show()
        w.refresh()
        QApplication.processEvents()
        return app, w, lib

    def test_visible_range_matches_a_full_scan(self):
        with tempfile.TemporaryDirectory() as tmp, __import__("unittest").mock.patch.dict(os.environ, {"APPDATA": tmp}):
            app, w, _ = self._window(tmp)
            try:
                sb = w.grid.verticalScrollBar()
                for value in (0, 1, sb.maximum() // 3, sb.maximum() // 2, sb.maximum()):
                    sb.setValue(value)
                    view = w.grid.viewport().rect()
                    full = [i for i in range(w.grid.count())
                            if w.grid.visualItemRect(w.grid.item(i)).intersects(view)]
                    self.assertEqual(list(w.grid.visible_range()), full, value)
                    self.assertTrue(full)
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_prepared_covers_are_used_and_match(self):
        from PySide6.QtCore import QRectF
        with tempfile.TemporaryDirectory() as tmp, __import__("unittest").mock.patch.dict(os.environ, {"APPDATA": tmp}):
            app, w, lib = self._window(tmp, n=6, art_every=1)
            try:
                g = lib.list_games()[0]
                path, crop = lib.artwork_path(g), (1.3, 0.4, 0.6)
                prepared = app.CoverCache.prepare([(path, crop)])
                self.assertEqual(len(prepared), 1)
                cache = app.CoverCache()
                cache.accept(prepared)
                self.assertEqual(len(cache._ready), 1)
                fast = cache.get(path, crop).toImage()
                self.assertEqual(cache._ready, {})               # used, not kept twice
                slow = app.CoverCache().get(path, crop).toImage()
                self.assertEqual(fast, slow)                     # the same picture as decoding on the spot
                self.assertEqual(app.CoverCache.prepare([(path, crop)], cache.cached()), [])   # ready: skipped
                self.assertEqual(app.CoverCache.prepare([(Path(tmp) / "gone.png", crop)]), [])
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_search_glides_fades_in_and_out(self):
        import time as _t
        from PySide6.QtWidgets import QApplication
        with tempfile.TemporaryDirectory() as tmp, __import__("unittest").mock.patch.dict(os.environ, {"APPDATA": tmp}):
            app, w, _ = self._window(tmp, n=12)

            def settle():
                end = _t.monotonic() + app.ARRIVE_WAIT + app.APPEAR_S + app.APPEAR_STAGGER_MAX + 0.5
                while _t.monotonic() < end:
                    QApplication.processEvents()
                    _t.sleep(0.01)
            try:
                search_now(w, "alpha")                         # 4 of 12 stay
                self.assertEqual(w.grid.count(), 4)
                self.assertTrue(w._leaving and w._leaving[-1][1])  # OUT: the others fade out where they were ...
                # ... and the ones whose place changed come back IN at their new place, after the OUT
                self.assertTrue(w.appearing)
                self.assertTrue(all(d >= app.ARRIVE_WAIT - 1e-9 for d in w.appearing.values()))
                self.assertEqual(w.slides, {})                    # nothing slides
                self.assertTrue(w.anim.isActive())
                lf = w.leaving_frame()[-1]
                self.assertTrue(0 < lf[2] <= 1 and 1 - app.LEAVE_SHRINK <= lf[3] <= 1)
                search_now(w, "alph")                             # a change mid-fade: the first fade carries on
                self.assertTrue(w._leaving)
                search_now(w, "")                              # back: IN, the 8 others arrive one after another
                self.assertEqual(w.grid.count(), 12)
                self.assertGreaterEqual(len(w.appearing), 8)      # (+ the ones whose place changes back)
                delays = sorted(w.appearing.values())
                self.assertTrue(0 <= delays[0] <= app.ARRIVE_WAIT + 1e-9)
                self.assertTrue(0 < delays[-1] - delays[0] <= app.APPEAR_STAGGER_MAX + 1e-9)
                first = min(w.appearing, key=w.appearing.get)
                self.assertGreater(w.appear_fade(first), 0.5)
                settle()
                self.assertEqual(w.appear_fade(first), 0.0)       # fully there
                self.assertEqual((w.slides, w.appearing), ({}, {}))
                self.assertEqual(w.leaving_frame(), [])
                # nothing matches: "No Results Found" fades in, and out again when something does
                search_now(w, "zzz")
                self.assertTrue(w.showing_no_results())
                settle()
                self.assertEqual(w.no_results, 1.0)
                search_now(w, "alp")
                self.assertEqual(len(w.appearing), 4)             # from the empty screen: they fade in too
                self.assertGreater(w.appear_fade(next(iter(w.appearing))), 0.5)
                settle()
                self.assertEqual(w.no_results, 0.0)
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_search_never_slides(self):
        """Search: a cover whose place changes fades out there and back in at its new place; nothing moves."""
        with tempfile.TemporaryDirectory() as tmp, __import__("unittest").mock.patch.dict(os.environ, {"APPDATA": tmp}):
            app, w, _ = self._window(tmp, n=12)
            try:
                for q in ("alpha", "alph", "", "beta", "zzz", ""):
                    search_now(w, q)
                    for i in range(w.grid.count()):
                        self.assertIsNone(w.slide_offset(w.grid.item(i).data(app.GAME_ID_ROLE)))
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_bookmarks_fade_and_settings_apply_at_once(self):
        import time as _t
        from PySide6.QtWidgets import QApplication
        with tempfile.TemporaryDirectory() as tmp, __import__("unittest").mock.patch.dict(os.environ, {"APPDATA": tmp}):
            app, w, _ = self._window(tmp, n=3)
            try:
                self.assertEqual(w.bookmark_alpha, 1.0)
                w._toggle_bookmarks()
                self.assertFalse(w.settings.show_bookmarks)
                end = _t.monotonic() + 0.5
                while _t.monotonic() < end and w.bookmark_alpha > 0:
                    QApplication.processEvents()
                    _t.sleep(0.01)
                self.assertEqual(w.bookmark_alpha, 0.0)
                s = w.settings
                s.show_bookmarks = True
                w.apply_settings(s)
                self.assertEqual(w.bookmark_alpha, 1.0)
            finally:
                w.close()
                app.wait_for_tasks(5)
