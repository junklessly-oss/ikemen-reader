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
from library import Library, LocalSettings, MachinePaths, sort_games
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


# A closed main window isn't deleted (the real program quits instead), so its timers - live polls, lights, prefetch -
# kept running for the rest of the run: on the laptop the later tests took 24-76 s each, 1.8 GB, 18+ min in all
# (2026-10-25). Each test's closed main windows are deleted when it ends.
_run_test = unittest.TestCase.run


def _run_and_tidy(self, result=None):
    try:
        return _run_test(self, result)
    finally:
        from PySide6.QtWidgets import QApplication
        qa = QApplication.instance()
        if qa is not None:
            import app
            closed = [w for w in qa.topLevelWidgets() if isinstance(w, app.MainWindow) and not w.isVisible()]
            if closed:
                from PySide6.QtCore import QTimer
                for w in closed:
                    for t in w.findChildren(QTimer):      # (no new save check while waiting)
                        t.stop()
                    if w.checker:                         # (a save check still running would answer a deleted
                        w.checker.wait(5000)              # window)
                app.wait_for_tasks(5)
                qa.processEvents()
                for w in closed:
                    if w.checker:                         # (one started by the answer just delivered)
                        try:
                            w.checker.done.disconnect()
                        except (RuntimeError, TypeError):
                            pass
                    app.discard(w)


unittest.TestCase.run = _run_and_tidy


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

    def test_upload_only_sync_uploads_saves_made_outside_the_launcher(self):
        write(self.pc_saves, "s.dat", "v1")
        self.pc.before_launch(GAME, self.pc_saves)
        write(self.pc_saves, "s.dat", "v2")                                # played without the launcher
        self.assertEqual(self.pc.sync_if_safe(GAME, self.pc_saves, allow_pull=False), Action.PUSHED)
        self.assertEqual(read_all(self.pc.cloud_dir(GAME)), {"s.dat": "v2"})

    def test_upload_only_sync_never_overwrites_when_both_changed(self):
        write(self.pc_saves, "s.dat", "v1")
        self.pc.before_launch(GAME, self.pc_saves)
        write(self.laptop_saves, "s.dat", "x")
        self.laptop.before_launch(GAME, self.laptop_saves, on_conflict=lambda i: "cloud")
        write(self.laptop_saves, "s.dat", "laptop progress")
        self.laptop.after_exit(GAME, self.laptop_saves)
        write(self.pc_saves, "s.dat", "pc progress")
        self.assertEqual(self.pc.sync_if_safe(GAME, self.pc_saves, allow_pull=False), Action.CONFLICT)
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
        self.pc._backup_root(GAME)
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
            problem, plan = launch_plan(g, s)            # a game new to Luna: added to its list, then launched
            self.assertIsNone(problem)
            self.assertEqual(luna.find_uid(s.luna_path, str(game_exe)), plan["luna"][2])
            data = json.loads((Path(s.luna_path).parent / "userconfig" / "savegamedata_5.3.1.json").read_text("utf-8"))
            self.assertEqual(data[0], [plan["luna"][2]])
            self.assertEqual(data[1][plan["luna"][2]]["title"], "VN")
            self.assertTrue((Path(s.luna_path).parent / "userconfig" / "savegamedata_5.3.1.json.ikemen.bak").is_file())
            (Path(s.luna_path).parent / "userconfig" / "savegamedata_5.3.1.json").write_text("{broken", "utf-8")
            self.assertEqual(launch_plan(g, s)[0], "play.luna_unknown_game")   # a list we can't read: as before
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
            lib.add_game("アオイ", "PC")
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
        from test_launcher import run_game
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

    @staticmethod
    def _link(link: Path, target: Path, args: str = "") -> None:
        ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');"
              f"$s.TargetPath='{target}';$s.Arguments='{args}';$s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True, timeout=60)

    def test_loader_shortcut_keeps_its_arguments(self):
        """2026-10-25 (owner): 相州戦神館學園 万仙陣 opened only AlphaROMdiE's own window - its shortcut is
        "AlphaROMdiE.exe malie.exe" and the "malie.exe" was dropped. A shortcut's arguments come along; a plain
        .exe (or a game added before) gets those of the shortcut to it in its own folder."""
        import lnk
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Bansen jin"
            write(folder, "AlphaROMdiE.exe", "")
            write(folder, "malie.exe", "")
            loader = folder / "AlphaROMdiE.exe"
            self._link(folder / "malie.exe.lnk", loader, "malie.exe")
            self._link(Path(tmp) / "desktop.lnk", loader, '"my game.exe" -x')
            self.assertEqual(lnk.resolve_launch(str(Path(tmp) / "desktop.lnk")), (str(loader), '"my game.exe" -x'))
            self.assertEqual(lnk.resolve_launch(str(loader)), (str(loader), "malie.exe"))   # beside it
            self.assertEqual(lnk.args_beside(str(folder / "malie.exe")), "")                # (not a shortcut to it)
            self.assertEqual(lnk.resolve_game_exe(str(Path(tmp) / "desktop.lnk")), str(loader))
            self._link(folder / "settings.lnk", loader, "-config")                         # two disagree: no guess
            self.assertEqual(lnk.args_beside(str(loader)), "")

    def test_arguments_split_like_windows(self):
        from launcher import split_args
        self.assertEqual(split_args('malie.exe'), ["malie.exe"])
        self.assertEqual(split_args('"my game.exe" -x  '), ["my game.exe", "-x"])
        self.assertEqual(split_args("  "), [])

    def test_arguments_stay_with_the_same_program(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(Path(tmp) / "cloud")
            g = lib.add_game("G", "PC", exe=r"D:\G\AlphaROMdiE.exe")
            g = lib.edit(g.game_id, "G", {"PC": MachinePaths(r"D:\G\AlphaROMdiE.exe", "", "malie.exe")})
            g = lib.edit(g.game_id, "G", {"PC": MachinePaths(r"d:\g\alpharomdie.exe", r"C:\s")})   # Edit's Save
            self.assertEqual(g.paths["PC"].args, "malie.exe")
            g = lib.edit(g.game_id, "G", {"PC": MachinePaths(r"D:\G\other.exe", r"C:\s")})        # another program
            self.assertEqual(g.paths["PC"].args, "")
            g = lib.set_paths(g.game_id, "PC", exe=r"D:\G\AlphaROMdiE.exe", args="malie.exe")
            lib.update(g.game_id, status="reading")
            self.assertEqual(lib.get(g.game_id).paths["PC"].args, "malie.exe")
            self.assertEqual(lib.set_paths(g.game_id, "PC", exe=r"D:\G\new.exe").paths["PC"].args, "")


class ChooseVndbCover(unittest.TestCase):
    """2026-10-25 (owner): Edit's "Choose VNDB Cover" - simply there for a VNDB cover known to have 2+ covers; found
    once for an older game (fades in, remembered in the game); never for your own cover."""

    def test_when_the_button_shows(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            import vndb
            QApplication.instance() or QApplication([])
            lib = Library(Path(tmp) / "cloud")
            settings = LocalSettings("PC", str(Path(tmp) / "cloud"))
            two = [vndb.Cover("a", "main", "", ""), vndb.Cover("b", "pkgfront", "R", "2014")]
            asked = []

            def covers(vid, **kw):
                asked.append(vid)
                return two
            with mock.patch.object(vndb, "covers", side_effect=covers):
                known = lib.add_game("Known", "PC", vndb_id="v1", artwork_from="vndb", vndb_multi=True)
                d = app.GameDialog(lib, settings, known)
                self.assertFalse(d.vndb_covers_btn.isHidden())                  # there at once, nothing asked
                self.assertIsNone(d.vndb_covers_btn.graphicsEffect())          # (no fade)
                d.reject()
                app.wait_for_tasks(5)
                self.assertEqual(asked, [])

                mine = lib.add_game("Mine", "PC", vndb_id="v2", artwork_from="user", vndb_multi=True)
                d = app.GameDialog(lib, settings, mine)
                app.wait_for_tasks(5)
                QApplication.processEvents()
                self.assertTrue(d.vndb_covers_btn.isHidden())                   # own cover: no button
                d.reject()

                old = lib.add_game("Old", "PC", vndb_id="v3", artwork_from="vndb")
                d = app.GameDialog(lib, settings, old)
                self.assertTrue(d.vndb_covers_btn.isHidden())
                end = time.time() + 5
                while time.time() < end and d.vndb_covers_btn.isHidden():
                    QApplication.processEvents()
                    time.sleep(0.02)
                self.assertFalse(d.vndb_covers_btn.isHidden())                  # found once: fades in
                self.assertTrue(lib.get(old.game_id).vndb_multi)                # ... and remembered
                d.reject()
                app.wait_for_tasks(5)


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
                app.wait_for_tasks(5)   # (the settings share still writing into tmp failed its cleanup now and then)

    def test_reload_and_status_pick(self):
        """A full reload (e.g. the upload after a session changed the cloud folder) with the same game selected: the
        bar is updated once and nothing fades (refilling the grid passed the selection through other games and
        none - the name / light / stats flickered). Picking a status: only its words fade, the name's way."""
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            g = lib.add_game("Alpha", "PC", exe=r"C:\a\a.exe")
            lib.add_game("Beta", "PC", exe=r"C:\b\b.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.resize(1200, 800)
                w.show()
                w.refresh()
                w._select(g.game_id)
                end = time.monotonic() + 0.6
                while time.monotonic() < end:
                    QApplication.processEvents()
                    time.sleep(0.005)
                shown = []
                real = w._update_details_now
                with mock.patch.object(w, "_update_details_now", lambda: (shown.append(w.selected_id()), real())):
                    lib.update(g.game_id, auto_title="x")
                    w.refresh()
                self.assertEqual(set(shown), {g.game_id})            # never another game or none in between
                self.assertEqual(w._fader.pieces, {})                # nothing faded
                w.d_status.setCurrentIndex(w.d_status.findData("reading"))
                self.assertEqual(sorted(w._fader.pieces), ["status"])
                f = w._fader.pieces["status"]
                self.assertEqual((f.out_s, f.in_s), (app.BAR_OUT_S, app.BAR_IN_S))
                self.assertEqual(f.rect, w._status_text_rect())      # the words only, not the box
                self.assertEqual(lib.get(g.game_id).status, "reading")
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_stats_come_back_with_synced(self):
        """The game closed: the light says Now Reading until the saves have uploaded (and the button Stop, so both
        change together) - the time / date stay away until then, never beside Now Reading."""
        import threading
        from types import SimpleNamespace
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            g = Library(cloud).add_game("A", "PC", exe=r"C:\a\a.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.refresh()
                w._select(g.game_id)
                w.worker = SimpleNamespace(stop_pressed=threading.Event(), force_close=threading.Event())
                w.playing_id = g.game_id
                w.play_phase = "playing"
                self.assertTrue(w.quiet(g.game_id))
                w._update_details()
                self.assertFalse(w.d_status.isHidden())             # the status keeps showing while reading
                self.assertFalse(w.d_status.blank)                  # (owner: no fade around a session)
                w._sync_wait(g.game_id, after_session=True)        # closed: uploading (not shown yet)
                w.play_phase = "syncing"
                # still Stop + Now Reading: button and light change together, in one update (owner, 2026-10-17)
                self.assertEqual(w._session_look(g.game_id), ("bar.stop", "playing"))
                self.assertTrue(w.quiet(g.game_id))                 # -> no stats yet
                w._waiting = (g.game_id, time.monotonic() - 1, True)
                self.assertEqual(w._session_look(g.game_id)[1], "syncing")
                self.assertTrue(w.quiet(g.game_id))
                w.worker, w.playing_id, w.play_phase = None, None, "done"   # synced: the session is over
                self.assertFalse(w.quiet(g.game_id))
                w._update_details()
                self.assertFalse(w.d_status.blank)
                self.assertFalse(w.d_status.testAttribute(app.Qt.WA_TransparentForMouseEvents))
            finally:
                w.worker = None
                w.close()
                app.wait_for_tasks(5)


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
            # (this place is certain, so the library would fill it in by itself: FewerGuesses; off here to test asking)
            w = app.MainWindow(settings)
            started = []
            fake = mock.MagicMock()
            fake.return_value.start.side_effect = lambda: started.append(True)
            try:
                # (kept off for the whole check: a later reload under a busy test run filled the folder in by itself
                # before Read was pressed, so there was nothing left to ask)
                with mock.patch.object(app, "certain_saves", return_value={}):
                    w.refresh()
                    app.wait_for_tasks(5)
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
        # (Enter = the safe button on the left: what it always did on screen, where Qt gave Enter to the focused
        # leftmost button - set outright since 2026-10-17)
        cases = [([("yes", "Remove"), ("no", "Cancel")], ["Cancel", "Remove"], "Cancel"),
                 ([("no", "Cancel"), ("yes", "Confirm")], ["Cancel", "Confirm"], "Cancel"),
                 ([("setup", "Set Up"), ("new", "Add as New"), ("cancel", "Cancel")],
                  ["Cancel", "Add as New", "Set Up"], "Cancel"),
                 ([("now", "Choose Now"), ("later", "Later")], ["Later", "Choose Now"], "Later")]
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
        self.assertTrue(is_engine_name("Ethornell - BURIKO General Interpreter"))     # BGI (1.0.3)

    def test_edition_tags_hd(self):
        from titles import clean_title, strip_leading_tags
        self.assertEqual(clean_title("素晴らしき日々HD"), "素晴らしき日々")
        self.assertEqual(clean_title("Game HD"), "Game")
        self.assertEqual(clean_title("ShinHD"), "ShinHD")                     # never off the end of a Latin word
        self.assertEqual(strip_leading_tags("[only tags]"), "[only tags]")   # nothing left after them: kept

    def test_shared_exe_names_are_not_the_same_game(self):
        """Two different BGI games (both "BGI.exe") were taken for one game added on the other device."""
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            other = lib.add_game("素晴らしき日々", "Laptop", exe=r"C:\Keromakura\素晴らしき日々HD\BGI.exe")
            mine = Path(tmp) / "向日葵の教会と長い夏休み"
            mine.mkdir()
            (mine / "BGI.exe").write_bytes(b"")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                self.assertIsNone(w._added_on_other_device(str(mine / "BGI.exe")))          # a different game
                same = Path(tmp) / "素晴らしき日々HD"
                same.mkdir()
                (same / "BGI.exe").write_bytes(b"")
                self.assertEqual(w._added_on_other_device(str(same / "BGI.exe")).game_id, other.game_id)  # by title
                lib.add_game("Hoshi", "Laptop", exe=r"C:\x\hoshi_main.exe")
                (Path(tmp) / "y").mkdir()
                (Path(tmp) / "y" / "hoshi_main.exe").write_bytes(b"")
                self.assertEqual(w._added_on_other_device(str(Path(tmp) / "y" / "hoshi_main.exe")).title, "Hoshi")
            finally:
                w.close()
                app.wait_for_tasks(5)

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
            # 1.0.3: download-style folder names lose their leading [date] [circle] tags; "HD" is an edition tag
            ("Bunny/[230331] [アトリエかぐや BARE＆BUNNY] Bunny’s ママ代行サービス", "Start.exe"): "Bunny’s ママ代行サービス",
            ("Relirium/[260529] [Whirlpool] Relirium -レリリウム- 遺跡と出逢いと冒険と 通常版", "Relirium.exe"):
                "Relirium -レリリウム- 遺跡と出逢いと冒険と",
            ("Suba/素晴らしき日々HD", "BGI.exe"): "素晴らしき日々",
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


class ConflictWording(unittest.TestCase):
    """The "which saves to keep" window names the cloud copy truthfully."""

    def test_cloud_copy_named_by_where_it_came_from(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QPushButton, QLabel
        import app
        import i18n
        from sync_engine import ConflictInfo
        QApplication.instance() or QApplication([])
        info = ConflictInfo("g", 1_700_000_000.0, 1_600_000_000.0)
        for lang in ("en", "ja"):
            i18n.set_language(lang)
            try:
                both = app.ConflictDialog("Game", info)
                solo = app.ConflictDialog("Game", info, other_set_up=False)
                texts = lambda d: " ".join(b.text() for b in d.findChildren(QPushButton)) + \
                    " ".join(lab.text() for lab in d.findChildren(QLabel))
                self.assertIn(i18n.tr("conflict.cloud").split("\n")[0], texts(both))
                self.assertNotIn(i18n.tr("conflict.cloud"), texts(solo))
                self.assertIn(i18n.tr("conflict.cloud_solo"), texts(solo))
                self.assertIn(i18n.tr("conflict.body_solo", title="Game"), texts(solo))
                first = app.ConflictDialog("Game", info, first_sync=True)
                self.assertIn(i18n.tr("conflict.body_first", title="Game"), texts(first))
                self.assertIn(i18n.tr("conflict.cloud"), texts(first))     # (the cloud = the other device)
                first.deleteLater()
                both.deleteLater(); solo.deleteLater()
            finally:
                i18n.set_language("en")


class ConflictWordingInTheWindow(unittest.TestCase):
    """A game set up only on this device never claims its saves "changed on both devices"."""

    def test_messages_pick_the_true_wording(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            import i18n
            QApplication.instance() or QApplication([])
            i18n.set_language("en")
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            both = lib.add_game("Both", "PC", exe=r"C:\b\b.exe")
            lib.set_paths(both.game_id, "Laptop", exe=r"C:\b\b.exe")
            here = lib.add_game("Here", "PC", exe=r"C:\h\h.exe")
            first = lib.add_game("First", "Laptop", exe=r"C:\f\f.exe")     # added there, just set up here
            lib.set_paths(first.game_id, "PC", exe=r"C:\a\a.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            w.engine._save_base(both.game_id, "synced before")            # (this device has synced "Both")
            try:
                w.refresh()
                app.wait_for_tasks(5)
                w.sync_states = {both.game_id: "conflict", here.game_id: "conflict", first.game_id: "conflict"}
                w._check_sync = lambda *a, **k: None
                self.assertFalse(w.only_here(w.games[both.game_id]))
                self.assertTrue(w.only_here(w.games[here.game_id]))
                self.assertTrue(w.first_sync(w.games[first.game_id]))
                self.assertFalse(w.first_sync(w.games[both.game_id]) or w.first_sync(w.games[here.game_id]))
                for gid, key in ((both.game_id, "sync.state.conflict"), (here.game_id, "sync.state.conflict_solo"),
                                 (first.game_id, "sync.state.conflict_first")):
                    w.grid.setCurrentItem(next(w.grid.item(i) for i in range(w.grid.count())
                                               if w.grid.item(i).data(app.GAME_ID_ROLE) == gid))
                    w._update_details()
                    self.assertEqual(i18n.tr(key), w.d_sync.text())
                    self.assertEqual(w.d_sync.toolTip(), i18n.tr(key.replace("sync.state.", "sync.tip.")))
                w._update_sync_label()                          # one of them is set up only here: neutral wording
                self.assertEqual(w.sync_label.toolTip(), i18n.tr("top.attention_tip_mixed"))
                with mock.patch.object(app, "message", return_value="later") as asked:
                    w._offer_conflicts()
                self.assertEqual(asked.call_args[0][2], i18n.tr("conflict.waiting_title_mixed"))
                self.assertEqual(w.solo_key("play.not_launched_conflict", w.games[here.game_id]),
                                 "play.not_launched_conflict_solo")
                self.assertEqual(w.solo_key("play.not_launched_conflict", w.games[both.game_id]),
                                 "play.not_launched_conflict")
                self.assertEqual(w.solo_key("play.cancelled", w.games[here.game_id]), "play.cancelled")
                self.assertEqual(w.solo_key("play.not_launched_conflict", w.games[first.game_id]),
                                 "play.not_launched_conflict_first")
            finally:
                w.close()
                app.wait_for_tasks(5)


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


    @unittest.skipUnless(IS_WINDOWS, "the swap helper is PowerShell")
    def test_helper_ends_a_copy_stuck_while_closing(self):
        # 2.0.7, laptop: the old copy never ended, so the update waited and nothing reopened
        import updater
        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "Ikemen Reader"
            (program / "_internal").mkdir(parents=True)
            (program / "Ikemen Reader.exe").write_text("old exe")
            (program / "_internal" / "lib.dll").write_text("old lib")
            folder = updater.update_dir(Path(tmp) / "appdata", program)
            z = self._zip(Path(tmp), "new exe")
            (folder / z.name).write_bytes(z.read_bytes())
            new = updater.stage(folder / z.name, folder)
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])     # never closes
            try:
                updater.install_on_exit(folder, new, restart=False, program=program, pid=proc.pid, wait=2)
                self.assertIsNotNone(proc.wait(30))                                    # ended by the helper
                end = time.time() + 30
                while time.time() < end and "updated" not in ((folder / "update.log").read_text("utf-8")
                                                               if (folder / "update.log").exists() else ""):
                    time.sleep(0.2)
                self.assertEqual((program / "Ikemen Reader.exe").read_text(), "new exe")
                self.assertIn("ended it", (folder / "update.log").read_text("utf-8"))
            finally:
                proc.kill()
                proc.wait()

    @unittest.skipUnless(IS_WINDOWS, "the swap helper is PowerShell")
    def test_broken_update_puts_the_old_version_back(self):
        # the new version closes without ever saying it started (it crashed): the old one goes back and is opened
        import zipfile
        import updater
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:   # (the helper may still be ending)
            program = Path(tmp) / "Ikemen Reader"
            (program / "_internal").mkdir(parents=True)
            (program / "Ikemen Reader.exe").write_text("old exe")      # (not a program: starting it again just fails)
            (program / "_internal" / "lib.dll").write_text("old lib")
            folder = updater.update_dir(Path(tmp) / "appdata", program)
            z = folder / "Ikemen-Reader-9.9.9.zip"
            with zipfile.ZipFile(z, "w") as f:                          # "new version" = a program that ends at once
                f.write(Path(os.environ["SystemRoot"]) / "System32" / "hostname.exe", "Ikemen Reader/Ikemen Reader.exe")
                f.writestr("Ikemen Reader/_internal/lib.dll", "new lib")
                f.writestr("Ikemen Reader/_internal/added.dll", "only in the new one")
            new = updater.stage(z, folder)
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.5)"])
            updater.install_on_exit(folder, new, restart=True, program=program, pid=proc.pid)
            proc.wait()
            log = folder / "update.log"
            end = time.time() + 40
            while time.time() < end and "old version back" not in (log.read_text("utf-8") if log.exists() else ""):
                time.sleep(0.3)
            self.assertIn("closed without starting", log.read_text("utf-8"))
            self.assertEqual((program / "Ikemen Reader.exe").read_text(), "old exe")
            self.assertEqual((program / "_internal" / "lib.dll").read_text(), "old lib")
            self.assertFalse((program / "_internal" / "added.dll").exists())
            self.assertTrue((folder / "failed" / "Ikemen Reader.exe").is_file())     # (kept aside, not deleted)

    def test_new_version_says_it_started(self):
        from unittest import mock
        import updater
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {updater.STARTED_ENV: str(Path(tmp) / "ok")}):
            updater.confirm_started()
            self.assertTrue((Path(tmp) / "ok").is_file())
            self.assertNotIn(updater.STARTED_ENV, os.environ)               # (not passed on to games / Luna)
            updater.confirm_started()                                        # (twice: nothing happens)

    def test_damaged_settings_use_the_spare_copy(self):
        # settings.json damaged (power cut mid-write): the last good copy, not the first-time setup
        from library import LocalSettings, load_local_settings, save_local_settings, SETTINGS_SPARE
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            save_local_settings(LocalSettings(cloud_root=str(d / "cloud"), machine_name="Laptop"), d)
            self.assertTrue((d / SETTINGS_SPARE).is_file())
            (d / "settings.json").write_text('{"cloud_root": "G:', "utf-8")    # cut off
            s = load_local_settings(d)
            self.assertIsNotNone(s)
            self.assertEqual(s.machine_name, "Laptop")
            (d / "settings.json").unlink()
            self.assertEqual(load_local_settings(d).machine_name, "Laptop")
            (d / SETTINGS_SPARE).unlink()
            self.assertIsNone(load_local_settings(d))                          # (nothing at all: a new setup)

    def test_helper_never_ends_a_different_process(self):
        # a process id can be reused once the old copy ends: only the very process that asked is ever ended
        import updater
        script = updater.helper_script(Path("p"), Path("n"), Path("f"), 1234, True, started=0.0)
        self.assertIn("-gt 0", script)                   # (no start time known = never ends anything)
        script = updater.helper_script(Path("p"), Path("n"), Path("f"), 1234, True, started=1761700000.5)
        self.assertIn("[math]::Abs($at - 1761700000.5) -lt 2", script)


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
            g = lib.add_game(t, "PC", exe=rf"C:\vn{i}\game.exe")   # (set up here: the default view, This Device)
            lib.update(g.game_id, last_played=1000 - i)               # first title = most recent
            ids[t] = g.game_id
        fid = None
        if folder_titles:
            fid = lib.create_folder("Series")
            for i, t in enumerate(folder_titles):
                g = lib.add_game(t, "PC", exe=rf"C:\vf{i}\game.exe")
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

    def test_view_choice_saved_after_the_fade_and_on_closing(self):
        from library import load_local_settings
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, \
                __import__("unittest").mock.patch.dict(os.environ, {"APPDATA": tmp}):
            w, lib, ids, fid = self._window(tmp, ["A", "B", "C"])
            import app
            try:
                w.choose_sort("title")
                self.assertTrue(w._view_save.isActive())          # not written during the fade ...
                w.close()                                         # ... but closing saves it at once
                self.assertFalse(w._view_save.isActive())
                self.assertEqual(load_local_settings().sort, "title")
            finally:
                app.wait_for_tasks(5)

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
                new = lib.add_game("New", "PC", exe=r"C:\new\game.exe")
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
                    lib.record_session(ids[t], "PC", h * 3600.0)
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
                own = lambda: lib.view_orders("PC")["folder_sorts"].get(fid, "")
                self.assertEqual(own(), "title")                                   # this device's own sort...
                self.assertEqual(lib.folder_sort(fid), "")                         # ...the shared one untouched
                w.close_folder()
                w.choose_sort("hours")                  # the library's sort changes...
                w.open_folder(fid)
                self.assertEqual(w.sort.currentData(), "title")                    # ...the folder keeps its own
                w._dragging = ids["Zeta"]
                w._move_to(ids["Zeta"], 0)                                         # arrange inside: Custom
                w._dragging = None
                self.assertEqual(own(), "custom")
                self.assertEqual(shown()[0], ids["Zeta"])
                self.assertEqual(w.folder_games[fid][0].game_id, ids["Zeta"])     # the mosaic follows it
                w.choose_sort("follow")
                self.assertEqual(own(), "")
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
                self.assertEqual(lib.view_orders("PC")["library"]["here"][0], f)     # (This Device: this PC's own)
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
                self.assertEqual((lib.view_orders("PC")["folder_sorts"][fid], w.sort.currentData()), ("custom", "custom"))
            finally:
                w.close()


class ArrangementPerView(unittest.TestCase):
    """2026-10-25 (owner): My Arrangement in This Device / Other Device belongs to that computer - arranging it never
    moves covers on the other device. Only Both Devices is shared. A folder's sort is per device (like the library's)."""

    def test_this_device_is_its_own(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from unittest import mock
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            ids = []
            for i in range(4):                                          # on both devices
                g = lib.add_game(f"G{i}", "PC", exe=rf"C:\g{i}\g.exe")
                lib.set_paths(g.game_id, "Laptop", exe=rf"D:\g{i}\g.exe")
                ids.append(g.game_id)
            fid = lib.create_folder("Series")
            inner = [lib.add_game(f"F{i}", "PC", exe=rf"C:\f{i}\g.exe").game_id for i in range(3)]
            for gid in inner:
                lib.set_folder(gid, fid)
            lib.set_library_order(ids + [app.FOLDER_PREFIX + fid])
            s = LocalSettings("PC", str(cloud))
            s.sort = "custom"
            w = app.MainWindow(s)
            shown = lambda: [w.grid.item(i).data(app.GAME_ID_ROLE) for i in range(w.grid.count())]
            try:
                w.show()
                w.refresh()
                self.assertEqual(shown(), ids + [app.FOLDER_PREFIX + fid])    # starts from the shared arrangement
                w._move_to(ids[3], 0)                                          # This Device (the default view)
                self.assertEqual(shown()[0], ids[3])
                self.assertEqual(lib.library_order(), ids + [app.FOLDER_PREFIX + fid])   # shared: untouched
                w.open_folder(fid)
                w._move_to(inner[2], 0)
                self.assertEqual(lib.folder_order(fid), [])                    # shared folder order untouched
                self.assertEqual(lib.folder_sort(fid), "")                     # and its shared sort
                w.close_folder()
                # the laptop, also on This Device: the shared arrangement, not the PC's
                s2 = LocalSettings("Laptop", str(cloud))
                s2.sort = "custom"
                w2 = app.MainWindow(s2)
                try:
                    w2.show()
                    w2.refresh()
                    self.assertEqual([w2.grid.item(i).data(app.GAME_ID_ROLE) for i in range(w2.grid.count())], ids)
                finally:
                    w2.close()
                # Both Devices: the shared one, and arranging it is shared
                w.filter.setCurrentIndex(w.filter.findData("dev:both"))
                self.assertEqual(shown(), ids + [app.FOLDER_PREFIX + fid])
                w._move_to(ids[2], 0)
                self.assertEqual(lib.library_order()[0], ids[2])
                w.filter.setCurrentIndex(w.filter.findData("dev:here"))
                self.assertEqual(shown()[0], ids[3])                           # This Device kept its own
                w.refresh()                                                    # (read back from the file)
                self.assertEqual(shown()[0], ids[3])
                w.library.set_folder(ids[3], fid)                              # into the folder: out of the view lists
                self.assertNotIn(ids[3], lib.view_orders("PC")["library"]["here"])
            finally:
                w.close()
                app.wait_for_tasks(5)


class LanguageListFade(unittest.TestCase):
    """2026-10-25 (owner's idea): Settings' Language list covered the buttons under it. Now only what it lies on (each
    whole) fades out first, then the list opens; it closes, the new language goes in, THEN they fade back in."""

    def test_in_order(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from unittest import mock
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication, QPushButton
            from PySide6.QtCore import QPoint, QRect
            import app
            import i18n
            QApplication.instance() or QApplication([])
            i18n.set_language("en")
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            w = app.MainWindow(LocalSettings("PC", str(cloud)))

            def pump(s):
                end = time.monotonic() + s
                while time.monotonic() < end:
                    QApplication.processEvents()
                    time.sleep(0.003)
            try:
                w.show()
                d = app.SettingsDialog(w.settings, w)
                d.show()
                pump(0.3)
                v = d.lang.veil
                d.lang.showPopup()
                pump(0.03)
                self.assertFalse(d.lang.view().isVisible())          # fading first...
                pump(0.4)
                self.assertTrue(d.lang.view().isVisible())           # ...then the list
                covered = [b.text() for b in d.findChildren(QPushButton)
                           if QRect(b.mapTo(d, QPoint(0, 0)), b.size()).adjusted(-1, -1, 1, 1) in v._rects]
                self.assertIn("English", covered)                    # the buttons right under it
                self.assertNotIn(app.tr("settings.save"), covered)   # nothing further down the page
                d.lang.setCurrentIndex(1)                            # Japanese picked
                d.lang.hidePopup()
                self.assertEqual(i18n.language(), "ja")
                self.assertGreater(v._a, 0.99)                       # (in place before anything shows again)
                pump(0.4)
                self.assertFalse(v.isVisible())                      # back in
                d.reject()
            finally:
                i18n.set_language("en")
                w.close()
                app.wait_for_tasks(5)


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


class GameNotes(unittest.TestCase):
    """Right-click > Notes: a text box over the darkened window, saved (once) when it closes; kept in the game file."""

    def test_notes_saved_on_close_only_when_changed(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QWidget
        import app
        QApplication.instance() or QApplication([])
        w = QWidget()
        w.resize(800, 600)
        saved = []
        layer = app.NotesLayer(w, "Game", "old", lambda text, wait=False: saved.append(text))
        layer.boxes[0].layout().activate()
        box = layer.editor.geometry().translated(layer.boxes[0].pos())
        self.assertEqual(box.size(), app.QSize(580, 435))        # the text box: 72.5 % of the window
        block = layer.boxes[0].geometry()                        # title + text box: centred together
        self.assertEqual(block.left(), 110)
        self.assertLessEqual(abs(block.top() + block.bottom() - 599), 1)     # (odd heights: within a pixel)
        layer.dismiss()
        self.assertEqual(saved, [])                              # unchanged: nothing written
        layer = app.NotesLayer(w, "Game", "old", lambda text, wait=False: saved.append(text))
        layer.editor.setPlainText("\n".join(f"line {i}" for i in range(200)))
        layer.editor.resize(600, 400)
        sb = layer.editor.verticalScrollBar()
        self.assertGreater(sb.maximum(), 1000)                  # long notes scroll, by the pixel (the wheel glides)
        self.assertIs(app.SmoothWheel._area(layer.editor.viewport()), layer.editor)
        layer.editor.setPlainText("new notes")
        layer.dismiss()
        layer.dismiss()                                          # (a second close saves nothing more)
        self.assertEqual(saved, ["new notes"])
        w.close()

    def test_window_saves_notes_in_the_background_in_order(self):
        """The fade never waits on the cloud folder: saved by a background task; reopening at once shows the new
        notes; saves of one game land in order; closing the program writes whatever is still on its way."""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            cloud = Path(tmp) / "cloud"
            lib = Library(cloud)
            exe = Path(tmp) / "g.exe"
            exe.write_bytes(b"")
            gid = lib.add_game("Notes Game", "PC", exe=str(exe)).game_id
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            w.refresh()
            QApplication.processEvents()
            w.open_notes(gid)
            layer = w.findChild(app.NotesLayer)
            self.assertIsNotNone(layer)
            layer.editor.setPlainText("first")
            layer.dismiss()
            self.assertEqual(w.games[gid].notes, "first")             # in memory at once (menu: "Notes")
            w._save_notes(gid, "second")                              # (changed again before the first landed)
            w._save_notes(gid, "third")
            end = time.monotonic() + 10
            while (w._notes_saving or w._notes_queue) and time.monotonic() < end:
                QApplication.processEvents()
                time.sleep(0.01)
            self.assertEqual(lib.get(gid).notes, "third")             # the latest, last
            w._notes_queue = {gid: "on its way"}                      # closing with a save still waiting
            w._flush_notes()
            self.assertEqual(lib.get(gid).notes, "on its way")
            app.wait_for_tasks(5)
            w.close()

    def test_newer_notes_from_the_other_device_shown_unless_typing(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QWidget
        import app
        QApplication.instance() or QApplication([])
        w = QWidget()
        w.resize(800, 600)
        layer = app.NotesLayer(w, "Game", "old", lambda text, wait=False: None)
        layer.refresh("from the laptop")
        self.assertEqual(layer.editor.toPlainText(), "from the laptop")
        layer.editor.setPlainText("typing here")
        layer.refresh("newer still")
        self.assertEqual(layer.editor.toPlainText(), "typing here")   # (what's typed is never replaced)
        w.close()

    def test_keys_stay_in_the_notes_and_long_titles_shorten(self):
        """Typing notes never reaches the window behind: Enter doesn't open / read the selected game, F5 doesn't
        refresh, Backspace in an empty box doesn't go back out of a folder; Esc still closes. A long title ends in
        "..." (pointing at it shows it whole) instead of widening the box."""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        import app
        QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            cloud = Path(tmp) / "cloud"
            lib = Library(cloud)
            exe = Path(tmp) / "g.exe"
            exe.write_bytes(b"")
            gid = lib.add_game("A" * 300, "PC", exe=str(exe)).game_id
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            w.resize(1000, 700)
            w.show()
            w.refresh()
            w._select(gid)
            QApplication.processEvents()
            fired = []
            w.activate_selected = lambda *a: fired.append("enter")
            w.refresh = lambda *a, **k: fired.append("F5")
            w.close_folder = lambda *a: fired.append("back")
            w.open_notes(gid)
            layer = w.findChild(app.NotesLayer)
            end = time.monotonic() + 1
            while time.monotonic() < end:
                QApplication.processEvents()
            box = layer.boxes[0]
            self.assertLessEqual(box.width(), round(w.width() * layer.SIZE))         # the box keeps its size
            self.assertTrue(layer.head.toolTip())                                     # (shortened: the tip has it all)
            QTest.keyClicks(layer.editor, "ab")
            for k in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_F5, Qt.Key_Backspace, Qt.Key_Backspace, Qt.Key_Backspace,
                      Qt.Key_Backspace, Qt.Key_Backspace):
                QTest.keyClick(layer.editor, k)
            self.assertEqual((layer.editor.toPlainText(), fired), ("", []))
            QTest.keyClick(layer.editor, Qt.Key_Escape)
            self.assertEqual(fired, ["back"])                                         # Esc: the window's own "back"
            layer.dismiss()
            app.wait_for_tasks(5)
            w.close()

    def test_removal_warns_about_notes(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import app
        from library import Game, MachinePaths
        g = Game("g", "Game", paths={"PC": MachinePaths("C:/g.exe")})
        for notes, warned in (("", False), ("some notes", True)):
            g.notes = notes
            with mock.patch.object(app, "message", return_value="no") as ask:
                app.confirm_remove(None, g, "PC")
            self.assertEqual(app.i18n.tr("game.remove_notes") in ask.call_args[0][1], warned)

    def test_notes_kept_in_game_file(self):
        with tempfile.TemporaryDirectory() as d:
            lib = Library(d)
            g = lib.add_game("Notes Test", "PC", exe="C:/x.exe")
            lib.update(g.game_id, notes="line 1\n日本語")
            lib.record_session(g.game_id, "PC", 60)              # (later writes keep them)
            self.assertEqual(lib.get(g.game_id).notes, "line 1\n日本語")


class ShortcutIcons(unittest.TestCase):
    """(owner, 2026-10-26) The pinned taskbar button + desktop shortcuts follow the theme's icon (taskicon.py).
    Stand-in shortcuts in a temporary folder - never the owner's real taskbar or desktop."""

    def test_update_and_back(self):
        import taskicon
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pins, icons = tmp / "pins", tmp / "icons"
            pins.mkdir()
            exe = r"C:\Windows\notepad.exe"
            taskicon.make_shortcut(pins / "Ikemen Reader.lnk", exe)
            taskicon.make_shortcut(pins / "Other.lnk", r"C:\Windows\explorer.exe")
            ico = taskicon.ico_bytes([(16, b"one")])
            name = taskicon.icon_name("#a84368", "#FFF8F5")
            self.assertEqual(name, taskicon.icon_name("#A84368", "#fff8f5"))      # (a look = one file name)
            self.assertEqual(taskicon.update(exe, ico, name, icons, [pins]), [pins / "Ikemen Reader.lnk"])
            self.assertEqual(taskicon.icon_of(pins / "Ikemen Reader.lnk"), (str(icons / name), 0))
            self.assertEqual((icons / name).read_bytes(), ico)
            self.assertEqual(taskicon.icon_of(pins / "Other.lnk"), ("", 0))        # other programs: untouched
            self.assertEqual(taskicon.update(exe, ico, name, icons, [pins]), [])  # already right: not touched
            self.assertTrue((icons / "originals" / "Ikemen Reader.lnk").is_file())
            self.assertEqual(taskicon.update(exe, None, "", icons, [pins]), [pins / "Ikemen Reader.lnk"])
            self.assertEqual(taskicon.icon_of(pins / "Ikemen Reader.lnk"), ("", 0))   # original: the .exe's own
            for i in range(taskicon.KEEP_ICONS + 3):                                    # old looks cleaned up
                taskicon.update(exe, ico, f"icon_{i}.ico", icons, [pins])
                time.sleep(0.02)
            left = sorted(p.name for p in icons.glob("icon_*.ico"))
            self.assertEqual(len(left), taskicon.KEEP_ICONS)
            self.assertIn(f"icon_{taskicon.KEEP_ICONS + 2}.ico", left)

    def test_the_edge_cases(self):
        """(owner, 2026-10-26: "fix everything possible") a shortcut that can't be changed doesn't stop the rest; an
        icon the owner chose is left alone; a deleted icon file is made again and Windows told; Start menu subfolders;
        a redrawn picture = a new file name."""
        import stat
        import taskicon
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pins, icons = tmp / "pins", tmp / "icons"
            programs = tmp / "Start Menu" / "Programs"
            (programs / "Ikemen").mkdir(parents=True)
            pins.mkdir()
            exe = r"C:\Windows\notepad.exe"
            for f in (pins / "a.lnk", pins / "b.lnk", pins / "mine.lnk", programs / "Ikemen" / "Ikemen Reader.lnk"):
                taskicon.make_shortcut(f, exe)
            with taskicon._Link(pins / "mine.lnk") as s:                  # the owner's own icon choice
                s.set_icon(r"C:\Windows\explorer.exe", 0)
                s.save()
            os.chmod(pins / "a.lnk", stat.S_IREAD)                         # can't be written (like the shared desktop)
            try:
                ico, name = taskicon.ico_bytes([(16, b"one")]), "icon_x.ico"
                changed = taskicon.update(exe, ico, name, icons, [pins, programs])
                self.assertEqual(changed, [pins / "b.lnk", programs / "Ikemen" / "Ikemen Reader.lnk"])
                self.assertEqual(taskicon.icon_of(pins / "a.lnk"), ("", 0))
                self.assertEqual(taskicon.icon_of(pins / "mine.lnk"), (r"C:\Windows\explorer.exe", 0))
                (icons / name).unlink()                                    # the data folder cleared
                self.assertEqual(taskicon.update(exe, ico, name, icons, [pins]), [pins / "b.lnk"])   # told again
                self.assertTrue((icons / name).is_file())
                self.assertEqual(taskicon.update(exe, ico, name, icons, [pins]), [])
            finally:
                os.chmod(pins / "a.lnk", stat.S_IREAD | stat.S_IWRITE)
            self.assertNotEqual(taskicon.icon_name("#A84368", "#FFF8F5", b"old"),
                                taskicon.icon_name("#A84368", "#FFF8F5", b"new"))

    def test_window_follows_the_theme(self):
        from unittest import mock
        import taskicon
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            pins = Path(tmp) / "pins"
            pins.mkdir()
            exe = r"C:\Windows\notepad.exe"
            taskicon.make_shortcut(pins / "Ikemen Reader.lnk", exe)
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            w.presence.stop()
            self.assertEqual(w._icon_folders, [])                        # (run from source: never the real ones)
            w._icon_folders = [pins]
            try:
                with mock.patch.object(app.sys, "executable", exe):
                    def settle():
                        w._update_shortcut_icons()
                        app.wait_for_tasks(5)
                        QApplication.processEvents()
                    app.apply_theme("light")                             # a recoloured logo: its own icon file
                    settle()
                    loc, _ = taskicon.icon_of(pins / "Ikemen Reader.lnk")
                    picture = app.asset("Icon.png").read_bytes()
                    self.assertEqual(Path(loc).name, taskicon.icon_name(app.LOGO["logo_fill"], app.LOGO["logo_outline"],
                                                                        picture))
                    self.assertTrue(Path(loc).is_file())
                    self.assertTrue(Path(loc).read_bytes().startswith(bytes([0, 0, 1, 0])))   # (an .ico)
                    app.apply_theme("dark")                              # the original look: the .exe's own icon
                    settle()
                    self.assertEqual(taskicon.icon_of(pins / "Ikemen Reader.lnk"), ("", 0))
                    # not while editing (Windows' refresh flickers every icon on screen: once, when Settings closes)
                    w.restyle()
                    self.assertFalse(w._icon_timer.isActive())
                    with mock.patch.object(app, "SettingsDialog",
                                           lambda *a: mock.Mock(exec=lambda: 0, result_settings=None)), \
                            mock.patch.object(app, "discard"):
                        w.open_settings()                                # (cancelled or saved: the look kept)
                    self.assertTrue(w._icon_timer.isActive())
                    self.assertEqual(w._icon_timer.interval(), app.SHORTCUT_ICON_WAIT_MS)
                    w._icon_timer.stop()

                    # the colour picker (2026-10-26, owner: "isn't following the live preview" / title-bar logos
                    # flashed black as it opened): its black / white sample looks never reach the window icons...
                    seen = []
                    real_icon = app.app_icon
                    td = app.ThemeDialog(app.theme_colors("dark"), lambda c: (app.apply_theme("custom", c),
                                                                             app.restyle_all()))
                    try:
                        td.show()
                        with mock.patch.object(app, "app_icon",
                                               lambda logo=None: seen.append(dict(logo or app.LOGO)) or real_icon(logo)):
                            td._live = app.LiveColour("logo_fill", td.colors, td.preview)
                            self.assertNotIn("#000000", [s["logo_fill"] for s in seen])
                            self.assertNotIn("#FFFFFF", [s["logo_fill"] for s in seen])
                            td._set("logo_fill", "#A84368")              # ...and a drag step reaches them live
                            self.assertEqual(w._icon_logo["logo_fill"], "#A84368")
                            end = time.monotonic() + 2
                            while not any(s["logo_fill"] == "#A84368" for s in seen) and time.monotonic() < end:
                                QApplication.processEvents()
                            self.assertTrue(any(s["logo_fill"] == "#A84368" for s in seen))   # window icons
                            self.assertFalse(w._icon_timer.isActive())   # (the taskbar waits for Settings to close)
                        self.assertEqual(taskicon.icon_of(pins / "Ikemen Reader.lnk"), ("", 0))
                    finally:
                        if td._live is not None:
                            td._live.finish()
                            td._live = None
                        app.discard(td)
                    app.apply_theme("light")                             # closed right after Settings: still follows
                    w._icon_timer.start(app.SHORTCUT_ICON_WAIT_MS)
                    w.close()
                    loc, _ = taskicon.icon_of(pins / "Ikemen Reader.lnk")
                    self.assertEqual(Path(loc).name, taskicon.icon_name(app.LOGO["logo_fill"], app.LOGO["logo_outline"],
                                                                        picture))
            finally:
                app.apply_theme("dark")
                w.close()
                app.wait_for_tasks(5)


class ColourCodes(unittest.TestCase):
    """Custom Theme's colour code boxes: a pasted code counts however it arrives (owner, 2026-10-26)."""

    def test_hex_code(self):
        import app
        for text, want in (("#f6d3dd", "#F6D3DD"), ("F6D3DD", "#F6D3DD"), ("##F6D3DD", "#F6D3DD"),
                           (" #F6D3DD ", "#F6D3DD"), ("#F6#F6D3DDD3DD", "#F6D3DD"), ("#F6D3D", None),
                           ("#GGGGGG", None), ("", None)):
            self.assertEqual(app.hex_code(text), want, text)

    def test_double_click_selects_all_and_paste_counts(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        d = app.ThemeDialog(app.theme_colors("light"), lambda c: None)
        try:
            d.show()
            hx = d.rows["bg"][1]
            QTest.mouseDClick(hx, Qt.LeftButton, Qt.NoModifier, QPoint(hx.width() * 3 // 4, hx.height() // 2))
            self.assertEqual(hx.selectedText(), hx.text())             # "#" included
            hx.insert("#F6D3DD")                                       # pasted over it
            hx.editingFinished.emit()
            self.assertEqual(d.colors["bg"], "#F6D3DD")
            hx.setText("##F6D3DD" + "D")                               # the old way's leftover: still counts
            hx.editingFinished.emit()
            self.assertEqual((d.colors["bg"], hx.text()), ("#F6D3DD", "#F6D3DD"))
            hx.setText("nope")                                         # not a colour: the old one comes back
            hx.editingFinished.emit()
            self.assertEqual(hx.text(), "#F6D3DD")
        finally:
            app.discard(d)

    def test_low_contrast_warning_covers_boxes_secondary_text_and_covers(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        for theme in ("dark", "light"):                                # the built-in looks: no warning
            d = app.ThemeDialog(app.theme_colors(theme), lambda c: None)
            try:
                self.assertEqual(d.warning.text(), "", theme)
            finally:
                app.discard(d)
        d = app.ThemeDialog(app.theme_colors("dark"), lambda c: None)
        try:
            for role, where in (("input", "boxes"), ("muted", "muted"), ("placeholder_bottom", "covers")):
                before = d.colors[role]
                d._set(role, d.colors["text"] if role != "muted" else d.colors["bg"])
                self.assertIn(app.tr(f"theme.where_{where}"), d.warning.text(), role)
                d._set(role, before)
            self.assertEqual(d.warning.text(), "")
        finally:
            app.discard(d)


class FolderMosaicCopies(unittest.TestCase):
    """Folder mosaics draw sharp copies made in the background (CoverCache.mini / make_minis), never while drawing."""

    def test_copy_made_once_at_size_and_found(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtGui import QColor, QImage
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cover.png"
            img = QImage(600, 850, QImage.Format_RGB32)
            img.fill(QColor("#336699"))
            img.save(str(path))
            cc, crop, size = app.CoverCache(), (1.0, 0.5, 0.5), (88, 129)
            self.assertIsNone(cc.mini(path, crop, size))              # (nothing yet: drawn as before)
            self.assertTrue(cc.get(path, crop))                        # (drawn once: its file time is known)
            sources = cc.mini_sources([(path, crop)], size)
            self.assertEqual(len(sources), 1)
            self.assertEqual(cc.mini_sources([(path, crop)], size), [])  # (handed out once, not every frame)
            made = app.CoverCache.make_minis(sources, size)
            cc.accept_minis(made)
            pm = cc.mini(path, crop, size)
            self.assertEqual((pm.width(), pm.height()), size)
            self.assertIsNone(cc.mini(path, crop, (90, 131)))           # (another size: its own copy)
            self.assertEqual(QColor(pm.toImage().pixel(40, 60)).name(), "#336699")   # (a flat colour stays flat)
            fresh = app.CoverCache()                                    # (the next launch: read from disk at once)
            self.assertTrue(fresh.get(path, crop))
            kept = fresh.mini(path, crop, size)
            self.assertEqual((kept.width(), kept.height()), size)
            self.assertEqual(fresh.mini_alpha(path, crop, size), 1.0)  # (no fade: it was there from the start)

    def setUp(self):
        import app
        self._dir = tempfile.TemporaryDirectory()
        self._was = app.CoverCache._mini_dir
        app.CoverCache._mini_dir = Path(self._dir.name) / "mosaics"   # (not the real app data folder)

    def tearDown(self):
        import app
        app.CoverCache._mini_dir = self._was
        self._dir.cleanup()


class SavedThemes(unittest.TestCase):
    """Custom Theme > Saved Themes (themes.py): whole looks (colours, background, fonts) in the cloud folder."""

    def test_add_rename_delete_and_files(self):
        import themes
        with tempfile.TemporaryDirectory() as tmp:
            root, pic = Path(tmp) / "cloud", Path(tmp) / "p.png"
            f1, f2 = Path(tmp) / "a" / "F.ttf", Path(tmp) / "b" / "F.ttf"
            for f, data in ((pic, b"pic"), (f1, b"font one"), (f2, b"font two")):
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(data)
            a = themes.add(str(root), "With All", {"bg": "#112233"}, "One", "Two", (str(pic), 0.5, 2.0, 0.1, 0.9),
                           {"One": str(f1)})
            b = themes.add(str(root), "Same File Name", {}, "Two", "Two", None, {"Two": str(f2)})
            saved = {t["id"]: t for t in themes.load(str(root))}
            self.assertEqual([t["name"] for t in themes.load(str(root))], ["With All", "Same File Name"])
            self.assertEqual(saved[a]["bg"]["opacity"], 0.5)
            self.assertEqual(themes.picture(str(root), saved[a]).read_bytes(), b"pic")
            self.assertIsNone(themes.picture(str(root), saved[b]))                     # (no background)
            # two different fonts with the same file name don't overwrite each other
            self.assertEqual(themes.font_file(str(root), saved[a], "One").read_bytes(), b"font one")
            self.assertEqual(themes.font_file(str(root), saved[b], "Two").read_bytes(), b"font two")
            themes.rename(str(root), b, "Renamed")
            self.assertEqual(themes.load(str(root))[1]["name"], "Renamed")
            themes.delete(str(root), a)
            self.assertEqual([t["id"] for t in themes.load(str(root))], [b])
            left = sorted(p.name for p in (root / "themes").rglob("*") if p.is_file())
            self.assertEqual(left, ["F_2.ttf", "themes.json"])                         # its picture and font went

    def test_damaged_list_is_never_overwritten(self):
        import themes
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "themes").mkdir()
            (Path(tmp) / "themes" / "themes.json").write_text("{half a file", "utf-8")
            self.assertEqual(themes.load(tmp), [])
            with self.assertRaises(OSError):
                themes.add(tmp, "New", {}, "F", "F", None, {})
            self.assertEqual((Path(tmp) / "themes" / "themes.json").read_text("utf-8"), "{half a file")

    def test_local_copies_follow_the_cloud(self):
        import themes
        with tempfile.TemporaryDirectory() as tmp:
            root, cache, pic = Path(tmp) / "cloud", Path(tmp) / "cache", Path(tmp) / "p.png"
            pic.write_bytes(b"pic")
            tid = themes.add(str(root), "T", {}, "F", "F", (str(pic), 0.4, 1, 0.5, 0.5), {})
            items = themes.local_copies(str(root), cache, themes.load(str(root)))
            local = Path(items[0]["picture"])
            self.assertEqual((local.parent.name, local.read_bytes()), ("pictures", b"pic"))
            (cache / "pictures" / "old.png").write_bytes(b"x")                         # a deleted theme's copy
            (cache / "pictures" / "tried.png").write_bytes(b"x")                       # one being tried right now
            themes.local_copies(str(root), cache, themes.load(str(root)),
                                frozenset({str(cache / "pictures" / "tried.png")}))
            self.assertEqual(sorted(p.name for p in (cache / "pictures").iterdir()), sorted([local.name, "tried.png"]))
            themes.delete(str(root), tid)
            self.assertEqual(themes.local_copies(str(root), cache, themes.load(str(root))), [])
            self.assertEqual(list((cache / "pictures").iterdir()), [])

    def test_solo_round_trip(self):
        """Going solo takes the saved themes along; back to Dual, those saved / renamed / deleted while solo follow,
        and a rename made on the other device meanwhile stands."""
        import library
        import themes
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cloud = tmp / "cloud"
            cloud.mkdir()
            pic, font = tmp / "p.png", tmp / "f.ttf"
            pic.write_bytes(b"pic")
            font.write_bytes(b"font")
            a, b, c = (themes.add(str(cloud), n, {}, "F", "F", None, {}) for n in ("A", "B", "C"))
            solo = library.start_solo_library(str(cloud), app_dir=tmp)
            self.assertEqual([t["name"] for t in themes.load(str(solo))], ["A", "B", "C"])
            themes.rename(str(solo), a, "A2")
            themes.delete(str(solo), b)
            themes.add(str(solo), "New", {}, "Mine", "F", (str(pic), 0.5, 1, 0.5, 0.5), {"Mine": str(font)})
            themes.rename(str(cloud), c, "C (laptop)")
            library.merge_solo(str(cloud), "PC", app_dir=tmp)
            back = themes.load(str(cloud))
            self.assertEqual([t["name"] for t in back], ["A2", "C (laptop)", "New"])
            self.assertEqual(themes.picture(str(cloud), back[2]).read_bytes(), b"pic")
            self.assertEqual(themes.font_file(str(cloud), back[2], "Mine").read_bytes(), b"font")

    def _dialog(self, root):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        seen = []
        d = app.ThemeDialog(app.theme_colors("dark"), seen.append, root=root)
        d.resize(450, 620)
        d.show()
        end = time.monotonic() + 10
        while root and d.saved is None and time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.01)
        QApplication.processEvents()
        return app, d, seen

    def test_tabs_sit_left_with_equal_room_above_and_below(self):
        from PySide6.QtWidgets import QPushButton
        with tempfile.TemporaryDirectory() as tmp:
            app, d, _ = self._dialog(tmp)
            tabs = [b for b in d.findChildren(QPushButton) if b.isCheckable() and b.text() in
                    (app.tr("theme.tab_colours"), app.tr("theme.tab_saved"))]
            self.assertEqual(len(tabs), 2)
            first = min(tabs, key=lambda b: b.x())
            self.assertEqual(first.x(), d.layout().contentsMargins().left())             # on the left
            above = first.y()
            below = d.pages.y() - (first.y() + first.height())
            self.assertEqual(above, below)                                                # centred in its band
            d.close()
            _, d2, _ = self._dialog("")                                                   # no library yet: no tabs
            self.assertFalse(any(b.text() == app.tr("theme.tab_saved") for b in d2.findChildren(QPushButton)))
            d2.close()

    def test_tab_loads_in_the_background_and_picking_previews_everything(self):
        import themes
        from PySide6.QtGui import QImage, QColor
        from PySide6.QtWidgets import QApplication
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "cloud"
            pic = Path(tmp) / "p.png"
            img = QImage(300, 200, QImage.Format_RGB32)
            img.fill(QColor("#884422"))
            img.save(str(pic))
            themes.add(str(root), "Pictured", {"bg": "#112233"}, "Zen Maru Gothic", "Zen Maru Gothic",
                       (str(pic), 0.5, 1.5, 0.4, 0.6), {})
            themes.add(str(root), "Plain", {"bg": "#AA0000"}, "Zen Maru Gothic", "Zen Maru Gothic", None, {})
            app, d, seen = self._dialog(str(root))
            d._tab(1)
            self.assertEqual([it["theme"]["name"] for it in d.grid.items], ["Pictured", "Plain"])
            self.assertEqual(d.grid.height(), 2 * d.grid.CARD_H + d.grid.GAP)               # 2 to a row, + last
            self.assertEqual(max(d.grid.items[0]["image"].width(), d.grid.items[0]["image"].height()), 300)
            d.grid.grab()                                                                   # (draws)
            d._card_clicked(0)
            self.assertEqual(d.grid.chosen, d.grid.items[0]["theme"]["id"])
            local = d.chosen["bg"][0]
            end = time.monotonic() + 10
            while app.BACKGROUND["path"] != local and time.monotonic() < end:           # (decoded in the background)
                QApplication.processEvents()
                time.sleep(0.01)
            self.assertIn(local, app._bg_source)
            self.assertNotIn(str(root), local)                                            # this computer's copy
            self.assertEqual((app.BACKGROUND["path"], app.BACKGROUND["opacity"], d.colors["bg"]),
                             (local, 0.5, "#112233"))
            d._card_clicked(1)
            self.assertEqual(app.BACKGROUND["path"], "")                                  # a theme without one: none
            d.close()
            app.apply_background("", 0.35)

class ClosedWindowsLeaveNothingBehind(unittest.TestCase):
    """A window closed and deleted a moment after opening: work it had put off "until the next moment" (a drop-down's
    arrow, Settings fitting itself to the screen, Custom Theme's sizing) is dropped with it, not run on a deleted
    window (found by the 2.0.0 fuzz: an error, and once a hard crash)."""

    def test_settings_and_custom_theme_deleted_at_once(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        errors = []
        old = sys.excepthook
        sys.excepthook = lambda k, v, tb: errors.append(f"{k.__name__}: {v}")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                for _ in range(3):
                    d = app.SettingsDialog(LocalSettings("PC", tmp))
                    d.show()
                    app.discard(d)
                    t = app.ThemeDialog(app.theme_colors("dark"), lambda c: None, root=tmp)
                    t.show()
                    t._toggle_more()
                    app.discard(t)
                    end = time.monotonic() + 0.3
                    while time.monotonic() < end:
                        QApplication.processEvents()
                app.wait_for_tasks(5)
                QApplication.processEvents()
        finally:
            sys.excepthook = old
        self.assertEqual(errors, [])


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

    def test_vsync_clock_ticks_per_frame_and_stops(self):
        """Hover popups follow the mouse once per screen refresh (a 16 ms timer juddered on a 143 Hz screen)."""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        clock = app.VsyncClock()
        ticks = []
        clock.timeout.connect(lambda: ticks.append(time.perf_counter()))
        clock.start()
        self.assertTrue(clock.isActive())
        end = time.monotonic() + 0.5
        while time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.001)
        self.assertGreaterEqual(len(ticks), 20)                  # (>= 40 a second, whatever the screen)
        clock.stop()
        QApplication.processEvents()
        n = len(ticks)
        end = time.monotonic() + 0.1
        while time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.001)
        self.assertEqual(len(ticks), n)                          # stopped: nothing more
        self.assertIsInstance(app.SmoothTip.instance()._timer, app.VsyncClock)

    def test_rocking_runs_on_the_windows_clock(self):
        """The easter egg sets how strongly the covers rock; the window's own animation clock draws it (one evenly
        spaced redraw per frame - the egg's timer redrawing as well gave uneven frames and hitches)."""
        with tempfile.TemporaryDirectory() as tmp, __import__("unittest").mock.patch.dict(os.environ, {"APPDATA": tmp}):
            app, w, _ = self._window(tmp, n=6, art_every=1)
            try:
                egg = app.EasterEgg(w)
                egg._t0 -= 1                                     # (the fade in done: the boxes are live)
                egg._step()
                self.assertTrue(egg._rocker.isVisible())
                egg._rocker._scale = egg._rocker.HOT_SCALE
                t = w.rumble_t
                egg._rumble_step()
                self.assertGreater(w.rumble, 0)
                self.assertEqual(w.rumble_t, t)                  # (the egg only sets the strength)
                self.assertTrue(w.anim.isActive())
                w._tick()
                self.assertGreater(w.rumble_t, t)                # the window's frame moves it on
                egg._rocker.hide()
                egg._rumble_step()
                self.assertEqual(w.rumble, 0)
                egg.dismiss()
            finally:
                w.close()
                app.wait_for_tasks(5)

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

class Statistics(unittest.TestCase):
    """2026-10-17 restyle: time read / last read as an icon line after the sync status in the bottom bar (owner's
    pick of four layouts), the same icon line on covers, relative dates, numbers bright and units small, per-device split on hover."""

    def tearDown(self):
        import i18n
        i18n.set_language("en")

    def test_relative_dates(self):
        import i18n
        from datetime import date as d, datetime as dt
        today = d(2026, 10, 17)
        ts = lambda y, m, day: dt(y, m, day, 12).timestamp()
        want = {"en": ["Today", "Today", "Yesterday", "3 Days Ago", "13 Days Ago", "2 Weeks Ago", "4 Weeks Ago",
                       "Sep 12", "Dec 3, 2025"],
                "ja": ["今日", "今日", "昨日", "3日前", "13日前", "2週間前", "4週間前", "9月12日", "2025年12月3日"]}
        days = [ts(2026, 10, 18), ts(2026, 10, 17), ts(2026, 10, 16), ts(2026, 10, 14), ts(2026, 10, 4),
                ts(2026, 10, 3), ts(2026, 9, 16), ts(2026, 9, 12), ts(2025, 12, 3)]
        for lang, expected in want.items():
            i18n.set_language(lang)
            self.assertEqual([i18n.ago(t, today) for t in days], expected, lang)
            self.assertEqual(i18n.date(ts(2026, 10, 3)), "Oct 3, 2026" if lang == "en" else "2026年10月3日")

    def test_playtime_parts_match_playtime(self):
        import i18n
        for lang in ("en", "ja"):
            i18n.set_language(lang)
            for secs in (5, 59, 60, 3599, 3600, 43500, 360000):
                parts = i18n.playtime_parts(secs)
                joined = (" " if lang == "en" else "").join(n + u for n, u in parts)
                self.assertEqual(joined, i18n.playtime(secs), (lang, secs))

    def test_bottom_bar_block(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            g = lib.add_game("Read", "PC", exe=r"C:\r\r.exe")
            lib.record_session(g.game_id, "PC", 3600)
            lib.record_session(g.game_id, "Laptop", 1800)
            lib.update(g.game_id, last_played=time.time() - 3 * 86400)
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            w.quiet = lambda gid: False
            try:
                w.resize(2000, 800)                          # (the off-screen test fonts run wide)
                w.show()
                w.refresh()
                w._select(g.game_id)
                QApplication.processEvents()
                w._update_details()
                self.assertTrue(w.d_stats.isVisible())
                self.assertEqual(w.d_stats.seconds, 5400)
                self.assertEqual(w.d_stats.per_device, [("This Device", 3600), ("Other Device", 1800)])
                self.assertNotIn("Played", w.d_sync.text())                 # the sync line is only the sync status
                w.settings.show_stats = False
                w._update_details()
                end = time.monotonic() + 1                                   # (it fades away)
                while w.d_stats.isVisible() and time.monotonic() < end:
                    QApplication.processEvents()
                    time.sleep(0.01)
                self.assertFalse(w.d_stats.isVisible())                     # Hide Statistics
                QApplication.processEvents()
                for b in (w.edit_btn, w.saves_btn):                         # ...and the buttons don't stretch
                    self.assertLessEqual(b.width(), b.sizeHint().width() + 2, b.text())
                w.settings.show_stats = True
                w._update_details()
                QApplication.processEvents()
                line = w.d_stats
                self.assertGreater(line.x(), w.d_sync.x())                  # on the sync line, after the status
                self.assertEqual(line.y(), w.d_sync.y())
                groups = line._groups(5400, time.time())
                self.assertEqual(len(line._fitting(groups)), 2)
                line.resize(line.LEAD + app.runs_width(groups[0][0]) + 4, line.height())
                self.assertEqual(len(line._fitting(groups)), 1)             # narrow: the date goes first
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_cover_line_drops_the_date_first_when_narrow(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        f = app.ui_font()
        groups = app.cover_stat_groups(f, 43500, time.time() - 3 * 86400)
        self.assertEqual([r[1] for r in groups[0] if r[0] == "i"], ["clock"])
        self.assertEqual([r[1] for r in groups[1] if r[0] == "i"], ["calendar"])
        self.assertEqual([r[1] for r in app.cover_stat_groups(f, 0, 0)[0]], ["Not Started"])
        folder = app.cover_stat_groups(f, 7200, 0, "4 Games")
        self.assertEqual([r[1] for r in folder[0]], ["4", " Games"])

    def test_motion(self):
        """Another game's values show at once (the bar's one cross-fade blends old and new); after a session the
        time counts up and the clock hand sweeps, then everything stops."""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        b = app.StatsLine()
        now = time.time()
        b.set_values(3600, now, [], key=("a",))
        b.resize(b.sizeHint())
        b.show()
        QApplication.processEvents()
        b.set_values(180, now, [], key=("d",))
        self.assertEqual((b.seconds, b.last), (180, now))            # the new values at once...
        self.assertTrue(b.switching)                                 # ...faded in place (icons stay)
        self.assertEqual(b._switch[0][0][0][0][1], "clock")         # (from the old groups: 1h 00m)
        end = time.monotonic() + 1
        while b.switching and time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.01)
        QApplication.processEvents()
        self.assertFalse(b.switching)
        b.count_up(10)
        self.assertEqual(b._count[0], 10)
        b.grab()
        self.assertFalse(app.stat_icon("clock", "#FFFFFF", 14, 1.0, 90).isNull())
        end = time.monotonic() + 2
        while b._count and time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.02)
        self.assertIsNone(b._count)
        self.assertFalse(b._timer.isActive())
        b.count_up(500)                                             # (never counts down)
        self.assertIsNone(b._count)
        b.close()

    def test_last_read_per_device(self):
        """The day's hover box: when each device last read it; Never Played / Not Recorded (both read it before dates
        were kept per device: the shared date can't be told apart)."""
        from library import Game
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(Path(tmp))
            g = lib.add_game("A", "PC", exe=r"C:\a\a.exe")
            self.assertEqual(lib.get(g.game_id).last_played_on("PC"), 0.0)          # never
            lib.record_session(g.game_id, "PC", 60, when=1000.0)
            lib.record_session(g.game_id, "Laptop", 60, when=2000.0)
            g = lib.get(g.game_id)                                                  # (survives the file)
            self.assertEqual((g.last_played_on("PC"), g.last_played_on("Laptop")), (1000.0, 2000.0))
        old = Game("b", "B", playtime={"PC": 60.0}, last_played=500.0)              # from before: one device
        self.assertEqual((old.last_played_on("PC"), old.last_played_on("Laptop")), (500.0, 0.0))
        both = Game("c", "C", playtime={"PC": 60.0, "Laptop": 5.0}, last_played=500.0)
        self.assertEqual((both.last_played_on("PC"), both.last_played_on("Laptop")), (None, None))
        bad = Game.from_json(json.dumps({"game_id": "d", "title": "D", "last_played_by": {"PC": "x", "L": float("nan")}}))
        self.assertEqual(bad.last_played_by, {})
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        b = app.StatsLine()
        b.set_values(60, 1000.0, [], per_last=[("This Device", 1000.0), ("Other Device", 0.0), ("X", None)])
        tip = b._groups(60, 1000.0)[1][1]
        self.assertIn("Never Played", tip)
        self.assertIn("Not Recorded", tip)
        self.assertIn(app.i18n.date(1000.0), tip)
        # the time's box lists both devices too: one that never read it says Never Played
        b.set_values(60, 1000.0, [("This Device", 60.0), ("Other Device", 0.0)])
        time_tip = b._groups(60, 1000.0)[0][1]
        self.assertIn("This Device", time_tip)
        self.assertIn("Other Device&nbsp;&nbsp;&nbsp;<b>Never Played</b>", time_tip)

    def test_count_up_waits_for_the_new_total(self):
        """A game closes: the bar updates at once with the old total (the new one comes with the background
        re-read). The count-up must wait for the new total, not start and drop itself on the first update."""
        import dataclasses
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            g = Library(cloud).add_game("A", "PC", exe=r"C:\a\a.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.resize(1200, 800)
                w.show()
                w.refresh()
                old = dataclasses.replace(w.games[g.game_id], playtime={"PC": 600.0})
                w.games[g.game_id] = old
                w._select(g.game_id)
                w._count_up = (g.game_id, 600.0, time.monotonic())   # a 300 s session just ended
                w._update_details()                                   # the bar at once: still the old total
                self.assertIsNone(w.d_stats._count)
                self.assertIsNotNone(w._count_up)                     # still waiting
                w.games[g.game_id] = dataclasses.replace(old, playtime={"PC": 900.0})   # the re-read lands
                w._update_details()
                self.assertEqual(w.d_stats._count[0], 600.0)          # counts up 600 -> 900
                self.assertIsNone(w._count_up)
                # ...but only once the bar has settled: the old total sits still through the cross-fade first
                self.assertGreater(w.d_stats._count[1], time.monotonic() + app.BAR_OUT_S + app.BAR_IN_S)
            finally:
                w.close()
                app.wait_for_tasks(5)


class RenameFromBar(unittest.TestCase):
    """2026-10-17: click the name in the bottom bar to rename the game / folder in place. Enter saves (a game's
    title is then its own: locked), so does a click anywhere else, Esc cancels, empty / taken names change nothing;
    the box fits the text; the window's Enter / Esc / Backspace shortcuts don't fire while typing."""

    def test_rename_game_and_folder(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtCore import QPoint, Qt
            from PySide6.QtTest import QTest
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            g = lib.add_game("Old Name", "PC", exe=r"C:\o\o.exe")
            other = lib.add_game("Taken", "PC", exe=r"C:\a\a.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.resize(1400, 800)
                w.show()
                w.refresh()
                w._select(g.game_id)
                QApplication.processEvents()
                e = w.d_title_edit

                def rename(text, key=Qt.Key_Return):
                    QTest.mouseClick(w.d_title, Qt.LeftButton, pos=QPoint(10, w.d_title.height() // 2))
                    QApplication.processEvents()
                    self.assertTrue(e.isVisible())
                    e.selectAll()
                    QTest.keyClicks(e, text)
                    with mock.patch.object(w, "activate_selected") as opened, \
                            mock.patch.object(w, "close_folder") as closed:
                        QTest.keyClick(e, Qt.Key_Backspace)          # (removes the last letter typed)
                        QTest.keyClick(e, key)
                        QApplication.processEvents()
                        self.assertFalse(opened.called or closed.called)   # shortcuts stay quiet
                    self.assertFalse(e.isVisible())
                    self.assertTrue(w.d_title.isVisible())

                with mock.patch.object(app, "PlayWorker") as play:
                    rename("New Name!")                                 # Enter saves... and doesn't start the game
                    QTest.keyClick(w, Qt.Key_Space)                     # (nor a key pressed right after it)
                    QApplication.processEvents()
                    self.assertFalse(play.called)
                self.assertFalse(w.play_btn.hasFocus())
                saved = Library(cloud).get(g.game_id)
                self.assertEqual((saved.title, saved.title_locked), ("New Name", True))
                rename("Something Else!", Qt.Key_Escape)                # Esc: nothing changes
                self.assertEqual(Library(cloud).get(g.game_id).title, "New Name")
                with mock.patch.object(app, "report_failure") as failed:
                    rename("Taken!")                                    # another game's name: refused
                    self.assertTrue(failed.called)
                self.assertEqual(Library(cloud).get(g.game_id).title, "New Name")
                self.assertEqual(Library(cloud).get(other.game_id).title, "Taken")

                fid = lib.create_folder("Box")
                lib.set_folder(other.game_id, fid)
                w.refresh()
                w._select(app.FOLDER_PREFIX + fid)
                QApplication.processEvents()
                rename("Shelf!")
                self.assertEqual(Library(cloud).list_folders()[fid], "Shelf")
                # clicking past the end of the name (the empty part of the bar) doesn't open the box
                QTest.mouseClick(w.d_title, Qt.LeftButton, pos=QPoint(w.d_title.width() - 5, w.d_title.height() // 2))
                QApplication.processEvents()
                self.assertFalse(e.isVisible())
                # the box fits what's typed, and a click anywhere else (here: empty space) saves
                QTest.mouseClick(w.d_title, Qt.LeftButton, pos=QPoint(10, w.d_title.height() // 2))
                QApplication.processEvents()
                self.assertTrue(e.isVisible())
                QTest.mouseClick(e, Qt.LeftButton, pos=QPoint(e.width() // 2, e.height() // 2))   # inside: places the cursor
                QApplication.processEvents()
                self.assertTrue(e.isVisible())
                self.assertFalse(e.hasSelectedText())
                e.setText("A")
                short = e.width()
                e.setText("A much, much longer folder name")
                self.assertGreater(e.width(), short)
                QTest.mouseClick(w.grid.viewport(), Qt.LeftButton, pos=w.grid.viewport().rect().bottomRight() - QPoint(5, 5))
                QApplication.processEvents()
                self.assertFalse(e.isVisible())
                self.assertEqual(Library(cloud).list_folders()[fid], "A much, much longer folder name")
            finally:
                w.close()
                app.wait_for_tasks(5)


class FadingStatus(unittest.TestCase):
    """2026-10-17: status line messages fade in, fade out when cleared (also by their timeout), and a new message
    replaces an old one by fading the old one out first (never both on screen)."""

    def test_fades(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMainWindow
        import app
        QApplication.instance() or QApplication([])
        win = QMainWindow()
        bar = app.FadingStatusBar(win)
        win.setStatusBar(bar)
        win.show()

        def run(seconds, watch=None):
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                QApplication.processEvents()
                if watch:
                    watch()
                time.sleep(0.005)

        bar.showMessage("Now Reading...")
        QApplication.processEvents()
        self.assertLess(bar._alpha, 0.5)                     # starts faint...
        run(0.4)
        self.assertEqual((bar._disp, bar._alpha), ("Now Reading...", 1.0))   # ...and fades in
        seen = []
        bar.showMessage("Library updated.")
        run(0.6, lambda: seen.append((bar._disp, bar._alpha)))
        old = [a for d, a in seen if d == "Now Reading..."]
        self.assertTrue(old and old == sorted(old, reverse=True))   # the old one only fades out
        self.assertEqual((bar._disp, bar._alpha), ("Library updated.", 1.0))
        bar.showMessage("Gone soon", 100)                     # its timeout clears it: fades out
        run(0.8)
        self.assertEqual((bar.currentMessage(), bar._alpha), ("", 0.0))
        self.assertFalse(bar._timer.isActive())
        win.close()


class EmptyLibraryMessage(unittest.TestCase):
    """2026-10-17: no games yet -> "No Games Yet" drawn like No Results Found (fades in); adding the first game fades
    it out (keeping its words while it does) and the cover comes in after it."""

    def test_no_games_yet(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            w = app.MainWindow(LocalSettings("PC", str(cloud)))

            def run(seconds):
                end = time.monotonic() + seconds
                while time.monotonic() < end:
                    QApplication.processEvents()
                    time.sleep(0.005)
            try:
                w.resize(1000, 640)
                w.show()
                w.refresh()
                run(0.6)
                self.assertTrue(w.grid.isVisible())                  # drawn on the grid, not a separate label
                self.assertFalse(w.empty.isVisible())
                self.assertEqual((w.no_results_key, w.no_results), ("grid.empty", 1.0))
                Library(cloud).add_game("First", "PC", exe=r"C:\f\f.exe")
                w.refresh()
                QApplication.processEvents()
                self.assertEqual(w.no_results_key, "grid.empty")      # same words while it fades out
                self.assertTrue(w.appearing)                          # the cover waits, then fades in
                run(0.6)
                self.assertEqual(w.no_results, 0.0)
            finally:
                w.close()
                app.wait_for_tasks(5)


class SmoothReadButton(unittest.TestCase):
    """2026-10-17: the Read button's colour eases (label + size change at once: the bar's one cross-fade blends
    them); the status line's playtime has bright numbers; a light whose pulse changes (starting -> playing, same
    blue) cross-fades instead of jumping."""

    def _run(self, seconds, watch=None):
        from PySide6.QtWidgets import QApplication
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            QApplication.processEvents()
            if watch:
                watch()
            time.sleep(0.004)

    def test_button(self):
        """The Read button's label and width change at once (the bar's cross-fade blends them); its colour still
        eases to greyed / ready, and its timer stops when settled."""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QWidget, QHBoxLayout
        import app
        QApplication.instance() or QApplication([])
        host = QWidget()
        lay = QHBoxLayout(host)
        b = app.PulseButton(objectName="play")
        lay.addWidget(b)
        lay.addStretch()
        b.setText("Read")
        host.resize(600, 80)
        host.show()
        self._run(0.1)
        narrow = b.sizeHint().width()
        b.setEnabled(False)
        b.setText("Syncing longer")
        self.assertEqual(b.text(), "Syncing longer")
        self.assertGreater(b.sizeHint().width(), narrow)
        self._run(0.5)
        self.assertEqual(b._bg.name(), app.QColor(app.COLORS["play_disabled"]).name())   # eased to greyed
        self.assertFalse(b._timer.isActive())
        host.close()

    def test_sync_line(self):
        """The words beside the sync light change at once and the space fits them (the bar's one cross-fade
        blends old and new)."""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QWidget, QHBoxLayout
        import app
        QApplication.instance() or QApplication([])
        host = QWidget()
        lay = QHBoxLayout(host)
        d = app.SyncStatus()
        lay.addWidget(d)
        lay.addStretch()
        d.set_status("green", "Synced")
        host.resize(600, 40)
        host.show()
        self._run(0.1)
        d.set_status("blue", "Starting the Game...")
        self.assertEqual(d.text(), "Starting the Game...")
        self.assertEqual(round(d._w, 2), round(d._natural(("blue", "Starting the Game...", False)), 2))
        self._run(0.1)
        self.assertGreaterEqual(d.width(), int(d._w))
        host.close()

    def test_dropdown_arrow_follows_the_way_it_opens(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
        import app
        QApplication.instance() or QApplication([])
        self.assertTrue(app.ICONS["chevron_up"] or not app.ICONS["chevron"])   # the upward arrow is made too
        host = QWidget()
        QVBoxLayout(host)
        c = app.ThemedCombo()
        c.addItems(["One", "Two", "Three", "Four", "Five"])
        host.layout().addWidget(c)
        host.resize(300, 40)
        host.show()
        room = (c.screen() or QApplication.primaryScreen()).availableGeometry()
        host.move(room.left() + 50, room.top() + 50)
        self._run(0.2)
        self.assertFalse(bool(c.property("opens_up")))                     # room below: arrow down
        host.move(room.left() + 50, room.bottom() - 30)
        self._run(0.2)
        self.assertTrue(bool(c.property("opens_up")))                      # no room below: arrow up
        host.close()

    def test_status_time_and_light(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMainWindow
        import app
        QApplication.instance() or QApplication([])
        win = QMainWindow()
        bar = app.FadingStatusBar(win)
        win.setStatusBar(bar)
        runs = bar._runs("Saved.  Read for 1h 05m.")
        bright = [r[1] for r in runs if r[0] == "t" and r[3] == app.COLORS["text"]]
        self.assertEqual(bright, ["1", "05"])                             # only the numbers stand out
        self.assertEqual(bar._runs("Updated to 1.0.4"), [])               # no time in it: plain text
        self.assertEqual([r[1] for r in bar._runs("読書時間：1時間5分") if r[0] == "t" and r[3] == app.COLORS["text"]],
                         ["1", "5"])
        win.close()
        # the light: starting (blue, quick) -> playing (blue, calm) starts a cross-fade
        delegate = app.CardDelegate.__new__(app.CardDelegate)
        delegate._light_fx = {}
        delegate.w = type("W", (), {"settings": type("S", (), {"pulse": True})()})()
        from PySide6.QtGui import QPixmap, QPainter
        pm = QPixmap(40, 40)
        p = QPainter(pm)
        delegate._paint_light(p, app.QPointF(20, 20), "starting", "g")
        delegate._paint_light(p, app.QPointF(20, 20), "playing", "g")
        p.end()
        old, new, _t0 = delegate._light_fx["g"]
        self.assertEqual((old, new), (("blue", "busy"), ("blue", "calm")))


class ChangingWindowTitles(unittest.TestCase):
    """2026-10-17: a game that puts the chapter / scene in its window title never gets renamed to it: a title that
    kept changing gives only the part that stayed the same (nothing shared: no rename), a title flashed at the end
    ("Goodbye!") doesn't count, and a session's title that differs from the last one keeps only what both share."""

    def test_within_a_session(self):
        from collections import Counter
        from titles import session_title, clean_title
        self.assertEqual(session_title(Counter({"サクラノ刻 - 第一章": 500, "サクラノ刻 - 第二章": 400, "Goodbye!": 3})), "サクラノ刻")
        self.assertEqual(session_title(Counter({"Sakura no Toki": 2000, "Goodbye!": 5})), "Sakura no Toki")
        self.assertEqual(session_title(Counter({"Chapter 3 | Black Sheep Town": 300,
                                                "Chapter 4 | Black Sheep Town": 300})), "Black Sheep Town")
        self.assertEqual(session_title(Counter({"大穢【第3話】": 200, "大穢【第4話】": 220})), "大穢")
        self.assertEqual(session_title(Counter({"Loading...": 100, "Title Screen": 100, "Prologue": 100})), "")
        self.assertEqual(clean_title(session_title(Counter({"Indigo Ver1.02": 900}))), "Indigo")
        self.assertEqual(session_title(Counter({"Rent-a-Mom Agency": 10})), "Rent-a-Mom Agency")   # (not cut)

    def test_across_sessions(self):
        from titles import shared_part
        self.assertEqual(shared_part(["Sakura no Toki - Chapter 2", "Sakura no Toki"]), "Sakura no Toki")
        self.assertEqual(shared_part(["Old Name", "New Name"]), "")       # nothing shared: the new name is taken


class SmoothHoverPopups(unittest.TestCase):
    """2026-10-17: hover popups are ours: they come sooner, fade in, follow the mouse smoothly while it stays on the
    thing, only over the thing itself (the time text, not the gap beside it), and stay inside the window (above the
    pointer when there's no room below). The Read button grows on both sides when its label changes."""

    def _run(self, seconds):
        from PySide6.QtWidgets import QApplication
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.004)

    def test_popup(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QEvent, QPoint, QRect
        from PySide6.QtGui import QHelpEvent
        from PySide6.QtWidgets import QApplication, QWidget, QHBoxLayout
        import app
        qa = QApplication.instance() or QApplication([])
        app.install_smooth_tips(qa)
        self.assertEqual(qa.style().styleHint(app.QStyle.SH_ToolTip_WakeUpDelay), app.TIP_WAKE_MS)
        win = QWidget()
        win.resize(600, 300)
        lay = QHBoxLayout(win)
        line = app.StatsLine()
        lay.addWidget(line)
        win.show()
        win.move(100, 100)
        line.set_values(3600, time.time() - 3 * 86400, [("This Device", 3600)], key=("g",))
        self._run(0.1)
        line.repaint()
        a, b, _ = line._spans[0]
        mouse = {"pos": line.mapToGlobal(QPoint(round((a + b) / 2), line.height() // 2))}
        with mock.patch.object(app.QCursor, "pos", side_effect=lambda: QPoint(mouse["pos"])):
            gap = QPoint(round(line._spans[0][1] + app.STAT_GROUP_GAP / 2), line.height() // 2)
            QApplication.sendEvent(line, QHelpEvent(QEvent.ToolTip, gap, line.mapToGlobal(gap)))
            self.assertFalse(app.SmoothTip.instance().isVisible())       # the gap between: no popup
            here = line.mapFromGlobal(mouse["pos"])
            QApplication.sendEvent(line, QHelpEvent(QEvent.ToolTip, here, mouse["pos"]))
            tip = app.SmoothTip.instance()
            self.assertTrue(tip.isVisible())
            self.assertIn("This Device", tip.label.text())
            self._run(0.25)
            self.assertAlmostEqual(tip.windowOpacity(), 1.0, places=2)    # faded in
            start = tip.pos()
            mouse["pos"] = mouse["pos"] + QPoint(10, 0)                    # moves along the time: follows
            self._run(0.4)
            self.assertEqual(tip.pos().x() - start.x(), 10)
            room = QRect(win.mapToGlobal(QPoint(0, 0)), win.size())
            self.assertTrue(room.contains(QRect(tip.pos(), tip.size())))  # inside the window
            mouse["pos"] = mouse["pos"] + QPoint(0, 200)                   # off the time: fades away
            self._run(0.3)
            self.assertFalse(tip.isVisible())
        win.close()

    def test_popup_goes_above_when_no_room_below(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtGui import QHelpEvent
        from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget
        import app
        qa = QApplication.instance() or QApplication([])
        app.install_smooth_tips(qa)
        win = QWidget()
        win.resize(400, 200)
        lay = QVBoxLayout(win)
        lay.addStretch()
        btn = QPushButton("Edit")
        btn.setToolTip("A longer explanation of what this button does.")
        lay.addWidget(btn)
        win.show()
        win.move(100, 100)
        self._run(0.1)
        at = btn.mapToGlobal(btn.rect().center())
        with mock.patch.object(app.QCursor, "pos", side_effect=lambda: QPoint(at)):
            QApplication.sendEvent(btn, QHelpEvent(QEvent.ToolTip, btn.rect().center(), at))
            tip = app.SmoothTip.instance()
            self._run(0.2)
            self.assertLess(tip.geometry().bottom(), at.y())             # above the pointer, inside the window
            self.assertGreaterEqual(tip.y(), win.mapToGlobal(QPoint(0, 0)).y())
            tip.leave()
            self._run(0.2)
        win.close()

    def test_syncing_shows_only_when_it_takes_a_while(self):
        """Owner's rule: Syncing (button + light) mirrors the real sync, but a sync done within SYNC_SHOW_AFTER_S is
        never shown (Stop -> Read, no flash); a longer one shows from then until it ends. The library is then re-read
        in the background (refresh_soon), not while the window waits."""
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            g = lib.add_game("A", "PC", exe=r"C:\a\a.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.show()
                w.refresh()
                w._select(g.game_id)

                def session(sync_seconds):
                    seen = set()
                    w.worker, w.playing_id = object(), g.game_id
                    w._on_phase("playing")
                    w._on_phase("syncing")                # the game closed: uploading...
                    start = time.monotonic()
                    end = start + sync_seconds
                    while time.monotonic() < end:
                        QApplication.processEvents()
                        seen.add((w.play_btn.text(), w.light_state(g.game_id)))
                        time.sleep(0.005)
                    w.worker, w.playing_id = None, None   # ...done
                    took = time.monotonic() - start
                    w._on_phase("done")
                    seen.add((w.play_btn.text(), w.light_state(g.game_id)))
                    return seen, took

                # (a busy test run can stall the loop past SYNC_SHOW_AFTER_S: then that "quick" sync really wasn't
                # quick, and showing it was right - try again; failed twice in full runs on 2026-10-18)
                for _ in range(3):
                    quick, took = session(0.1)
                    if took < app.SYNC_SHOW_AFTER_S * 0.8:
                        break
                self.assertLess(took, app.SYNC_SHOW_AFTER_S * 0.8)
                self.assertFalse(any(l == "syncing" for t, l in quick))       # too quick: never shown
                slow, _ = session(0.8)
                self.assertIn("syncing", {l for t, l in slow})                # the light shows it while it lasts
                self.assertEqual({t for t, l in quick | slow} - {app.tr("bar.stop"), app.tr("bar.play")}, set())
                self.assertEqual(w.play_btn.text(), app.tr("bar.play"))      # (the button: only Read / Stop)
                self.assertTrue(w.play_btn.isEnabled())
                with mock.patch.object(w, "_run") as bg:
                    w.refresh_soon()
                    self.assertTrue(bg.called)
            finally:
                w.worker = None
                w.close()
                app.wait_for_tasks(5)

    def test_closing_while_a_game_runs_asks_cancel_or_continue(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.show()
                w.worker = object()                        # (a game running)
                with mock.patch.object(app, "message", return_value="cancel") as asked:
                    w.close()
                self.assertTrue(w.isVisible())             # Cancel: stays open
                buttons = asked.call_args[0][3]
                self.assertEqual(buttons, [("cancel", app.tr("game.cancel")), ("continue", app.tr("close.continue"))])
                # (the question is "Close anyway?": the button answers it - 2026-10-17 text pass)
                self.assertEqual(app.i18n.S["close.continue"], ("Close Anyway", "それでも閉じる"))
            finally:
                w.worker = None
                w.close()
                app.wait_for_tasks(5)

    def test_deselect_clears_at_once_and_lights_draw_alone(self):
        """Clicking off a game clears the light's words at once, with the rest of the bar; a frame where only the
        lights pulse copies the covers from a picture made without lights (identical, far cheaper)."""
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            g = lib.add_game("A", "PC", exe=r"C:\a\a.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.resize(1200, 800)
                w.show()
                w.refresh()
                w._select(g.game_id)
                self._run(0.3)
                self.assertTrue(w.d_sync.text())
                w.grid.clearSelection()
                w._update_details()
                self.assertEqual((w.d_sync.text(), w.d_sync._shown[1]), ("", ""))   # gone at once
                w.settings.pulse = True
                w.anim.stop()
                self._run(0.3)                                                    # (pending redraws done)
                # (a whole-grid redraw in that pause - background cover loading finishing, under a full run - rightly
                # skips the next lights-only frame: _tick's painted_all. The frame after it is the one tested.)
                w.grid.painted_all = False
                w._tick()
                self.assertIsNotNone(w.grid.light_frame)                         # a lights-only frame...
                w.grid.viewport().repaint(w.grid.light_frame["region"])
                self.assertIsNotNone(w.grid._still)                               # ...drawn from the picture
                w.grid.viewport().repaint()                                       # anything else: drawn as usual
                self.assertIsNone(w.grid._still)
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_still_patch_of_a_thin_strip_keeps_the_cover(self):
        """A thin strip near a cover's bottom edge (a folder hover's region splits the light squares into such strips)
        is patched with the cover in it, not bare backdrop (Qt's own idea of where covers are is GRID_TOP_SHIFT
        higher - the dark rectangles under the lights)."""
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            Library(cloud).add_game("A", "PC", exe=r"C:\a\a.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.resize(1200, 800)
                w.show()
                w.refresh()
                self._run(0.3)
                grid = w.grid
                grid._still = grid._make_still()
                fresh = grid._still.toImage()
                cover = app.CardDelegate.cover_rect(grid.visualItemRect(grid.item(0)), 0.0).toAlignedRect()
                strip = app.QRect(cover.right() - 40, cover.bottom() - 9, 35, 8)   # inside the cover, at its foot
                grid._still.fill(app.QColor("red"))                                  # (so a missed patch shows)
                grid._patch_still(app.QRegion(strip))
                k = fresh.devicePixelRatio()
                got = grid._still.toImage()
                at = (round(strip.center().x() * k), round(strip.center().y() * k))
                self.assertEqual(got.pixelColor(*at), fresh.pixelColor(*at))
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_still_picture_is_never_made_while_painting(self):
        """render() inside paintEvent lost the frame (lights froze; under the easter egg's dark cover their squares
        went black): the still picture is made / patched in _tick, before the frame is asked for, and its size
        check matches at every scale (Python's round(802.5) = 802 had it remade every frame at 125 %)."""
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            Library(cloud).add_game("A", "PC", exe=r"C:\a\a.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.resize(1200, 800)
                w.show()
                w.refresh()
                w.settings.pulse = True
                w.anim.stop()
                self._run(0.3)
                grid = w.grid
                for dirty in (None, app.QRegion(app.QRect(0, 0, 5, 5))):
                    grid._still_dirty = dirty
                    grid.painted_all = False         # (a frame without a full redraw: see LaptopSmoothness)
                    w._tick()
                    self.assertIsNotNone(grid.light_frame)
                    self.assertEqual(grid._still.size(), grid._still_size())     # ready (and patched) beforehand
                    self.assertIsNone(grid._still_dirty)
                    with mock.patch.object(app.LibraryGrid, "_make_still") as make, \
                            mock.patch.object(app.LibraryGrid, "_patch_still") as patch:
                        grid.viewport().repaint(grid.light_frame["region"])
                    make.assert_not_called()
                    patch.assert_not_called()
                with mock.patch.object(grid.viewport(), "devicePixelRatioF", return_value=1.25), \
                        mock.patch.object(grid.viewport(), "height", return_value=642):
                    self.assertEqual(grid._still_size().height(), 803)          # as Qt rounds (802.5 -> 803)
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_bar_pieces_fade_out_then_in(self):
        """Selecting another game: the pieces that changed (the name) fade out, then in; pieces that look the same
        (Edit, the Read button) are left alone; then the layer goes (owner, 2026-10-17: only the Read button
        cross-fades)."""
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            a = lib.add_game("Alpha", "PC", exe=r"C:\a\a.exe")
            z = lib.add_game("Zeta Long Title", "PC", exe=r"C:\z\z.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.resize(1200, 800)
                w.show()
                w.refresh()
                w._select(a.game_id)
                self._run(0.4)
                w._select(z.game_id)
                QApplication.processEvents()
                f = w._fader
                self.assertTrue(f.active)
                self.assertEqual(w.d_title.text(), "Zeta Long Title")      # (new look already underneath)
                self.assertIn("title", f.pieces)                           # the name: out, then in
                self.assertFalse(f.pieces["title"].cross)
                self.assertNotIn("edit", f.pieces)                         # unchanged: left alone
                self.assertNotIn("button", f.pieces)                       # Read -> Read: nothing to cross-fade
                self.assertEqual(f.pieces["title"].out_s, app.BAR_OUT_S)   # the name: the bar's one pace
                # the status words alone: the empty box behind them is the box itself (no guessed colour)
                bare = w._bar_without_status_words()
                self.assertFalse(w.d_status.blank)                          # (put back)
                self.assertEqual(bare.size(), w.details.grab().size())
                first = f.pieces["title"].t0
                w._update_details()                                        # updated again mid-fade, same look:
                self.assertEqual(f.pieces["title"].t0, first)              # no restart (that was the flicker)
                self._run(0.6)
                w.grid.clearSelection()                                    # nothing selected, then a game again:
                self._run(0.6)
                w._select(a.game_id)
                QApplication.processEvents()
                starts = {p.t0 for p in f.pieces.values()}
                self.assertIn("title", f.pieces)
                self.assertIn("button", f.pieces)
                self.assertEqual(len(starts), 1)                           # the whole bar: one moment...
                self.assertTrue(all(p.cross and p.xfade_s == app.BAR_WAKE_S for p in f.pieces.values()))   # ...one time
                end = time.monotonic() + app.BAR_OUT_S + app.BAR_IN_S + 0.3
                while time.monotonic() < end:
                    QApplication.processEvents()
                    time.sleep(0.005)
                self.assertFalse(f.active)
                self.assertFalse(f.isVisible())
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_read_button_keeps_one_size(self):
        """Read / Stop / Open: the same size and place (owner, 2026-10-17: the few px between them read as noise);
        only a label that needs more room makes it bigger, and Read's left edge never moves."""
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            g = lib.add_game("A", "PC", exe=r"C:\a\a.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.resize(1600, 800)
                w.show()
                w.refresh()
                w._select(g.game_id)
                self._run(0.3)
                b = w.play_btn
                geo = b.geometry()
                self.assertEqual(geo.left() + b.MARGINS[0], 25)                    # visible edge 25 px in
                for key in ("bar.stop", "bar.open", "bar.play"):
                    b.setText(app.tr(key))
                    self._run(0.3)
                    self.assertEqual(b.geometry(), geo, key)                       # one size, one place
                b.setText("Starting the Game Now")                                # (a label needing more room)
                self._run(0.3)
                self.assertGreater(b.width(), geo.width())
                self.assertEqual(b.geometry().left(), geo.left())
                b.setText(app.tr("bar.play"))
                self._run(0.3)
                self.assertEqual(b.geometry(), geo)                                # back exactly as it was
            finally:
                w.close()
                app.wait_for_tasks(5)


class UndoRemove(unittest.TestCase):
    """2026-10-17: removing a game says 'Removed X.' with an Undo (bright, underlined) on the status line for 10 s;
    Undo brings back the game file, its cover and the folder it emptied."""

    def test_library_puts_everything_back(self):
        from PySide6.QtGui import QImage, QColor
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(Path(tmp) / "cloud")
            g = lib.add_game("Alpha", "PC", exe=r"C:\a\a.exe")
            img = QImage(30, 40, QImage.Format_RGB32)
            img.fill(QColor("red"))
            img.save(str(Path(tmp) / "c.png"))
            lib.set_artwork(g.game_id, str(Path(tmp) / "c.png"))
            lib.record_session(g.game_id, "PC", 3600)
            fid = lib.create_folder("Only One")
            lib.set_folder(g.game_id, fid)
            before = lib.get(g.game_id)
            removed = lib.remove(g.game_id)
            self.assertIsNone(lib.get(g.game_id))
            self.assertNotIn(fid, lib.list_folders())                  # (emptied: gone with it)
            back = lib.restore_removed(removed)
            self.assertEqual(back, before)                              # playtime, folder, cover name...
            self.assertTrue((lib.art_dir / back.artwork).is_file())
            self.assertEqual(lib.list_folders().get(fid), "Only One")
            self.assertIsNone(lib.restore_removed(removed))             # (already back: nothing twice)

    def test_status_line_offers_undo(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            g = Library(cloud).add_game("Alpha", "PC", exe=r"C:\a\a.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.resize(1200, 800)
                w.show()
                w.refresh()
                with mock.patch.object(app, "confirm_remove", return_value="here"):
                    w.remove_game(g.game_id)
                self.assertNotIn(g.game_id, w.games)
                bar = w.statusBar()
                self.assertEqual(bar.currentMessage(), 'Removed "Alpha".')
                end = time.monotonic() + 0.8
                while time.monotonic() < end:
                    QApplication.processEvents()
                    time.sleep(0.01)
                bar.repaint()
                self.assertFalse(bar._link_rect.isEmpty())                  # "Undo" drawn after the message
                click = bar._link_rect.center()
                bar._action[2]()                                             # (what clicking it does)
                self.assertIn(g.game_id, w.games)
                self.assertEqual(w.selected_id(), g.game_id)
                self.assertGreater(click.x(), 0)
            finally:
                w.close()
                app.wait_for_tasks(5)


class PulseSwitch(unittest.TestCase):
    """2026-10-17 (owner): Pulsing Sync Lights works as it's ticked (Save keeps it, Cancel puts it back); switched
    off, the lights finish their breath and rest where a still light sits, then stop."""

    def test_live_and_settling(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            s = LocalSettings("PC", str(cloud))
            s.pulse = True
            w = app.MainWindow(s)
            try:
                w.refresh()
                d = app.SettingsDialog(w.settings, w)
                d.pulse.setChecked(False)                                  # unticked: at once...
                self.assertFalse(w.settings.pulse)
                self.assertIsNotNone(app.PULSE_OFF_AT[0])
                self.assertTrue(w._lights_moving())                         # ...still finishing its breath
                d.reject()                                                  # Cancel: back on
                self.assertTrue(w.settings.pulse)
                self.assertIsNone(app.PULSE_OFF_AT[0])
                w.set_pulse(False)
                settled = app.pulse_settled_at() + 0.01
                with mock.patch.object(app.time, "monotonic", return_value=settled):
                    self.assertFalse(w._lights_moving())                    # settled: still from here on
                self.assertIsNone(app.PULSE_OFF_AT[0])
                with mock.patch.object(app.time, "monotonic", return_value=settled + 7.3):
                    w.set_pulse(True)                                       # on again (Cancel / ticked)...
                    self.assertEqual(app.pulse_phase(app.PULSE_CALM_S, settled + 7.3), 0.0)   # ...from rest
            finally:
                app.PULSE_OFF_AT[0] = None
                w.close()
                app.wait_for_tasks(5)


class DiscordPresence(unittest.TestCase):
    """2026-10-17 (owner): Browsing / Reading <title> / Tweaking / Away (5 min), logo only (light theme: the light
    one), hover = games in library, elapsed timer restarting only when reading starts / ends; off until turned on;
    Discord trouble never reaches the window."""

    def _fake_discord(self, fail_connect=False):
        import types
        calls = []

        class RPC:
            def __init__(self, cid):
                calls.append(("new", cid))

            def connect(self):
                if fail_connect:
                    raise ConnectionRefusedError("Discord isn't running")
                calls.append(("connect",))

            def update(self, **kw):
                calls.append(("update", kw))

            def clear(self):
                calls.append(("clear",))

            def close(self):
                calls.append(("close",))
        return types.SimpleNamespace(Presence=RPC), calls

    def test_background_thread(self):
        import presence
        fake, calls = self._fake_discord()
        p = presence.Presence("123")
        p.rpc_class = fake.Presence                                       # (a stand-in for Discord)
        p.show("Browsing...", start=1000.0, image="logo", image_text="3 Games in Library")
        end = time.monotonic() + 2
        while not any(c[0] == "update" for c in calls) and time.monotonic() < end:
            time.sleep(0.02)
        self.assertIn(("update", {"details": "Browsing...", "start": 1000, "large_image": "logo",
                                  "large_text": "3 Games in Library"}), calls)
        p.use_application("456")                                          # the language changed: the other name
        end = time.monotonic() + 2
        while ("new", "456") not in calls and time.monotonic() < end:
            time.sleep(0.02)
        time.sleep(0.2)
        self.assertIn(("new", "456"), calls)
        self.assertEqual([c[0] for c in calls if c[0] == "update"], ["update", "update"])   # shown again under it
        self.assertEqual(presence.CLIENT_IDS["ja"] != presence.CLIENT_IDS["en"], True)
        p.stop()
        self.assertIn(("clear",), calls)                                 # nothing left on the profile
        self.assertIn(("close",), calls)
        fake, calls = self._fake_discord(fail_connect=True)              # Discord not running: no harm
        p = presence.Presence("123")
        p.rpc_class = fake.Presence
        p.show("Reading...", "Sakura no Toki")
        time.sleep(0.3)
        p.stop()
        self.assertFalse(any(c[0] == "update" for c in calls))
        self.assertIsNone(presence.Presence("")._thread)                # (no application: nothing started)

    def test_states(self):
        import threading
        from types import SimpleNamespace
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            g = Library(cloud).add_game("Sakura no Toki", "PC", exe=r"C:\a\a.exe")
            s = LocalSettings("PC", str(cloud))
            w = app.MainWindow(s)
            w.presence.stop()
            w.presence = mock.Mock()                                      # (never the owner's real Discord)
            try:
                w.refresh()
                self.assertIsNone(w._presence_look())                    # off until turned on
                w.settings.discord = True
                with mock.patch.object(app.outside, "foreground_pid", return_value=os.getpid()), \
                        mock.patch.object(app.outside, "seconds_since_input", return_value=0.5):
                    self.assertEqual(w._presence_look(), ("Coding...", ""))   # run from source: the owner coding
                patch_frozen = mock.patch.object(app.updater, "FROZEN", True)   # (the packaged app from here on)
                patch_frozen.start()
                self.addCleanup(patch_frozen.stop)
                with mock.patch.object(app.outside, "foreground_pid", return_value=os.getpid()), \
                        mock.patch.object(app.outside, "seconds_since_input", return_value=0.5):
                    self.assertEqual(w._presence_look(), ("Browsing...", ""))
                    d = app.NameDialog("x", "y", w)
                    d.show()
                    self.assertEqual(w._presence_look(), ("Tweaking...", ""))
                    d.close()
                    since = w._presence_since
                    w.worker = SimpleNamespace(stop_pressed=threading.Event(), force_close=threading.Event())
                    w.playing_id, w.play_phase = g.game_id, "playing"
                    time.sleep(0.01)
                    self.assertEqual(w._presence_look(), ("Reading...", "Sakura no Toki"))
                    self.assertGreater(w._presence_since, since)        # the timer restarted with reading
                with mock.patch.object(app.outside, "foreground_pid", return_value=1):   # another program
                    w._last_active = time.monotonic() - 400
                    self.assertEqual(w._presence_look()[0], "Reading...")    # reading wins, even "idle"
                    w.worker, w.playing_id, w.play_phase = None, None, "done"
                    self.assertEqual(w._presence_look()[0], "Browsing...")  # not Away the moment reading ends
                    w._last_active = time.monotonic() - 400              # (reading counted as using it)
                    self.assertEqual(w._presence_look(), ("Away", ""))
                    self.assertAlmostEqual(w._presence_since, time.time() - 400, delta=2)   # away since the last use
                try:
                    app.apply_theme("light")                              # the Light theme: the light picture
                    w._update_presence()
                    shown = w.presence.show.call_args.kwargs
                    self.assertEqual((shown["image"], shown["image_text"]), ("logo_light", "1 Game in Library"))
                    app.apply_theme("custom", app.theme_colors("light"))   # custom, started from Light: same
                    w._update_presence()
                    self.assertEqual(w.presence.show.call_args.kwargs["image"], "logo_light")
                    pink = dict(app.theme_colors("light"), bg="#F6D3DD", logo_fill="#FFF8F5")
                    app.apply_theme("custom", pink)                       # light Main Area, cream logo: still light
                    w._update_presence()
                    self.assertEqual(w.presence.show.call_args.kwargs["image"], "logo_light")
                    app.apply_theme("custom", app.theme_colors("dark"))    # custom from Dark: the original
                    w._update_presence()
                    self.assertEqual(w.presence.show.call_args.kwargs["image"], "logo")
                finally:
                    app.apply_theme("dark")
                w.presence.use_application.assert_called_with(app.presence.CLIENT_IDS["en"])
                app.i18n.set_language("ja")                               # Japanese: the イケメンリーダー application
                try:
                    w._update_presence()
                    w.presence.use_application.assert_called_with(app.presence.CLIENT_IDS["ja"])
                finally:
                    app.i18n.set_language("en")
            finally:
                w.worker = None
                w.close()
                app.wait_for_tasks(5)

    def test_only_the_device_used_last_shows(self):
        """(owner, 2026-10-26) Both devices open: only the one used last shows; closing it doesn't hand over."""
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            self.assertIsNone(lib.discord_device())                      # no note: each shows its own, as before
            (cloud / "discord.json").write_text("[", "utf-8")
            self.assertIsNone(lib.discord_device())
            lib.claim_discord("Laptop")                                   # the laptop was used last
            self.assertEqual(lib.discord_device(), "Laptop")
            s = LocalSettings("PC", str(cloud))
            s.discord = True
            w = app.MainWindow(s)
            w.presence.stop()
            w.presence = mock.Mock()                                      # (never the owner's real Discord)
            try:
                def settle():
                    for _ in range(2):                                    # (a read begun earlier finishes first)
                        w._update_presence()
                        app.wait_for_tasks(5)
                        QApplication.processEvents()
                    w.presence.reset_mock()
                    w._update_presence()
                with mock.patch.object(app.outside, "foreground_pid", return_value=1), \
                        mock.patch.object(app.outside, "seconds_since_input", return_value=0.5):
                    settle()                                              # PC not in use: the laptop shows, not here
                    w.presence.show.assert_not_called()
                    w.presence.clear.assert_called()
                with mock.patch.object(app.outside, "foreground_pid", return_value=os.getpid()), \
                        mock.patch.object(app.outside, "seconds_since_input", return_value=0.5):
                    w._update_presence()                                  # used here: the PC takes over at once
                    w.presence.show.assert_called()
                    app.wait_for_tasks(5)
                    self.assertEqual(lib.discord_device(), "PC")
                lib.claim_discord("Laptop")                               # then the laptop is used again
                w._discord_claimed -= 60                                  # (the PC's own claim long past)
                with mock.patch.object(app.outside, "foreground_pid", return_value=1):
                    settle()
                    w.presence.show.assert_not_called()                   # the PC's status goes
                    (cloud / "discord.json").unlink()                     # ...even "Away" here stays hidden while
                    w._last_active = time.monotonic() - 400               # the laptop was the last one used
                    lib.claim_discord("Laptop")
                    settle()
                    w.presence.show.assert_not_called()
                w.settings.solo = True                                    # My Device: nothing to share
                self.assertFalse(w._discord_shared())
            finally:
                w.close()
                app.wait_for_tasks(5)


class RemoveFromThisDevice(unittest.TestCase):
    """2026-10-17 (owner): removing a game set up on both devices asks: this device only, or both. Only here: hidden
    here, kept for the other device (which sees it as set up only there); set up here again = back with its save
    folder; Undo works; removed on both = gone for good."""

    def test_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(Path(tmp))
            g = lib.add_game("Alpha", "PC", exe=r"C:\a\a.exe")
            lib.set_paths(g.game_id, "PC", save_dir=r"C:\saves\a")
            lib.set_paths(g.game_id, "Laptop", exe=r"D:\a\a.exe")
            undo = lib.remove_here(g.game_id, "PC")
            g = lib.get(g.game_id)
            self.assertTrue(g.removed_here("PC"))
            self.assertEqual(g.paths_for("PC").exe, "")                 # (the laptop sees it as only set up there)
            self.assertEqual(g.paths_for("Laptop").exe, r"D:\a\a.exe")
            self.assertEqual(lib.restore_removed(undo).paths_for("PC").save_dir, r"C:\saves\a")   # Undo
            lib.remove_here(g.game_id, "PC")
            back = lib.set_paths(g.game_id, "PC", exe=r"C:\a\a2.exe")   # set up here again
            self.assertFalse(back.removed_here("PC"))
            self.assertEqual(back.paths_for("PC").save_dir, r"C:\saves\a")   # (its save folder came back)
            lib.remove_here(g.game_id, "Laptop")                           # removed on the laptop...
            lib.remove_here(g.game_id, "PC")                               # ...and here: gone for good
            self.assertIsNone(lib.get(g.game_id))
            solo = lib.add_game("Beta", "PC", exe=r"C:\b\b.exe")          # only ever here: simply removed
            lib.remove_here(solo.game_id, "PC")
            self.assertIsNone(lib.get(solo.game_id))

    def test_window_and_pop_up(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication, QDialog, QPushButton
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            g = lib.add_game("Alpha", "PC", exe=r"C:\a\a.exe")
            lib.set_paths(g.game_id, "Laptop", exe=r"D:\a\a.exe")
            only_here = lib.add_game("Beta", "PC", exe=r"C:\b\b.exe")
            shown = {}

            def fake_exec(d):
                shown[len(shown)] = [b.text() for b in sorted(d.findChildren(QPushButton), key=lambda b: b.x())]
                return 0
            with mock.patch.object(QDialog, "exec", fake_exec):
                app.confirm_remove(None, lib.get(g.game_id), "PC")
                app.confirm_remove(None, lib.get(only_here.game_id), "PC")
            self.assertEqual(shown[0], ["Cancel", "Remove From Both Devices", "Remove From This Device"])
            self.assertEqual(shown[1], ["Cancel", "Confirm"])          # (nothing to choose)
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.resize(1200, 800)
                w.show()
                w.refresh()
                with mock.patch.object(app, "confirm_remove", return_value="here"):
                    w.remove_game(g.game_id)
                self.assertNotIn(g.game_id, w.games)                    # gone here...
                self.assertIsNotNone(lib.get(g.game_id))               # ...still in the shared library
                self.assertEqual(w.statusBar().currentMessage(), 'Removed "Alpha" from this device.')
                w.statusBar()._action[2]()                              # Undo
                self.assertIn(g.game_id, w.games)
            finally:
                w.close()
                app.wait_for_tasks(5)
            lib.remove_here(g.game_id, "PC")
            other = app.MainWindow(LocalSettings("Laptop", str(cloud)))   # the other device
            try:
                other.refresh()
                self.assertIn(g.game_id, other.games)
                self.assertEqual(other.light_state(g.game_id) in ("solo", "checking", "empty", "detect"), True)
                self.assertEqual(app.MainWindow.only_here(other, other.games[g.game_id]), True)
            finally:
                other.close()
                app.wait_for_tasks(5)


class PopupTextPass(unittest.TestCase):
    """2026-10-17 pass over every pop-up: risky questions default to the safe answer, a launch problem with nothing to
    choose says OK (not a lone Cancel), titles fit their messages."""

    def _shown(self, call):
        """(window title, buttons left to right, the Enter button) of the pop-up `call` opens."""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from unittest import mock
        from PySide6.QtWidgets import QApplication, QDialog, QPushButton
        import app
        QApplication.instance() or QApplication([])
        seen = {}

        def fake_exec(d):
            d.show()                                   # (really on screen: Qt moves Enter with the focus)
            QApplication.processEvents()
            bs = d.findChildren(QPushButton)
            seen.update(title=d.windowTitle(), buttons=[b.text() for b in bs],
                        default=[b.text() for b in bs if b.isDefault()])
            d.close()
            return 0
        with mock.patch.object(QDialog, "exec", fake_exec):
            call(app)
        return seen

    def test_force_close_enter_keeps_it_open(self):
        s = self._shown(lambda app: app.ask_yes_no(None, "x", app.tr("stop.force"), app.tr("stop.keep"),
                                                   title=app.tr("stop.title")))
        self.assertEqual(s["buttons"], ["Keep Open", "Force Close"])    # safe one far left...
        self.assertEqual(s["default"], ["Keep Open"])                   # ...and Enter (only it)
        self.assertEqual(s["title"], "Game Still Open")

    def test_enter_is_the_safe_button_whenever_there_is_one(self):
        s = self._shown(lambda app: app.ask_yes_no(None, "x", app.tr("restore.restore"), app.tr("game.cancel")))
        self.assertEqual((s["buttons"], s["default"]), (["Cancel", "Restore"], ["Cancel"]))
        s = self._shown(lambda app: app.message(None, "x", "T", [("watch", "Wait"), ("force", "Force Close"),
                                                                ("cancel", "Cancel")]))
        self.assertEqual((s["buttons"], s["default"]), (["Cancel", "Force Close", "Wait"], ["Cancel"]))
        s = self._shown(lambda app: app.message(None, "x"))              # a plain note: its one button
        self.assertEqual(s["default"], ["OK"])

    def test_message_closes_itself_when_done(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        state = {"n": 0}

        def done() -> bool:
            state["n"] += 1
            return state["n"] >= 2                   # (e.g. the game was closed meanwhile)
        t0 = time.monotonic()
        self.assertEqual(app.message(None, "x", buttons=[("cancel", "Cancel")], done_when=done), "done")
        self.assertLess(time.monotonic() - t0, 3)

    def test_status_line_texts_are_short(self):
        """The strip at the very bottom gets short versions (owner: the long ones ran off the window)."""
        import i18n
        try:
            for lang in ("en", "ja"):
                i18n.set_language(lang)
                for key in [k[:-len(":line")] for k in i18n.S if k.endswith(":line")]:
                    self.assertLessEqual(len(i18n.line(key, title="Sakura no Toki")), 60, key)
                    self.assertLess(len(i18n.line(key, title="x")), len(i18n.tr(key, title="x")) + 1, key)
            i18n.set_language("en")
            self.assertEqual(i18n.line("sync.pushed"), i18n.tr("sync.pushed"))     # (no short one: the usual text)
            self.assertEqual(i18n.games_list(["A", "B"]), "A, B")
            self.assertEqual(i18n.games_list(["A", "B", "C"]), "3 games")
        finally:
            i18n.set_language("en")

    def test_every_text_has_both_languages_and_fills_in(self):
        import i18n
        import string
        for key, (en, ja) in i18n.S.items():
            self.assertTrue(en and ja, key)
            names = lambda t: {f for _, f, _, _ in string.Formatter().parse(t) if f}
            self.assertEqual(names(en), names(ja), key)                 # same {placeholders} in both


class FewerGuesses(unittest.TestCase):
    """2026-10-16: no guessing unless almost certain. Detection after a first session only calls a folder
    "named after the game" when it carries the whole name, and only ticks a plainly-this-game folder; Browse
    opens where its box points (an empty save-folder box: the game's folder; others: This PC), never the last
    folder used; a save folder set up by hand asks "which saves to keep" straight away; the other device's save
    folder fills in this device's empty one when its place here is certain; Settings opens no taller than the
    main window."""

    def test_one_shared_word_is_not_the_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs, game_dir = Path(tmp) / "Documents", Path(tmp) / "Games" / "GirlsBookMaker"
            game_dir.mkdir(parents=True)
            before = time.time() - 1
            write(docs / "Books", "notes.dat", "x")                 # something else wrote here meanwhile
            write(docs / "Girls Book Maker", "save01.dat", "x")     # the game's own folder
            found = detect.find_folders(str(game_dir), before, ["Girls Book Maker", "GBM", "GirlsBookMaker"],
                                        roots=[game_dir, docs])
            self.assertEqual([Path(c.path) for c in found], [docs / "Girls Book Maker"])
            self.assertTrue(found[0].sure)

    def test_sure_only_for_plain_save_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            game_dir, appdata = Path(tmp) / "MyVN", Path(tmp) / "AppData"
            appdata.mkdir()
            before = time.time() - 1
            write(game_dir / "savedata", "data0001.bin", "x")
            write(appdata / "SomeMaker" / "save", "s.dat", "x")      # save-named, but nothing ties it to the game
            found = {Path(c.path): c.sure for c in detect.find_folders(str(game_dir), before, ["My VN"],
                                                                     roots=[game_dir, appdata])}
            self.assertTrue(found[game_dir / "savedata"])
            self.assertFalse(found[appdata / "SomeMaker" / "save"])

    def test_window_ticks_only_a_sure_folder(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        game = app.Game(game_id="g", title="Girls Book Maker")
        guess = detect.Candidate(r"C:\Users\J\Documents\Books", "folder", 3, 2)
        sure = detect.Candidate(r"C:\Users\J\Documents\Girls Book Maker", "folder", 5, 2, sure=True)
        with tempfile.TemporaryDirectory() as tmp:
            d = app.DetectDialog(game, [guess], Path(tmp))
            self.assertIsNone(d.group.checkedButton())              # a guess: nothing ticked, Use waits
            use = next(b for b in d.findChildren(app.QPushButton) if b.text() == app.tr("detect.use"))
            self.assertFalse(use.isEnabled())
            d.group.buttons()[0].setChecked(True)
            self.assertTrue(use.isEnabled())
            d = app.DetectDialog(game, [sure, guess], Path(tmp))
            self.assertEqual(d.group.checkedButton().property("path"), sure.path)

    def test_browse_starts_where_the_box_points_or_this_pc(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import app
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Game"
            write(folder, "game.exe", "")
            self.assertEqual(Path(app.browse_start(str(folder)).toLocalFile()), folder)
            self.assertEqual(Path(app.browse_start(str(folder / "game.exe")).toLocalFile()), folder / "game.exe")
            # not created yet (a save folder suggested from the other device): the nearest folder that exists
            self.assertEqual(Path(app.browse_start(str(folder / "save" / "1")).toLocalFile()), folder)
        empty = app.browse_start("").toString()
        self.assertEqual(empty, app.THIS_PC if sys.platform == "win32" else app.QUrl.fromLocalFile(str(Path.home())).toString())
        self.assertEqual(app.browse_start("relative\\path").toString(), empty)

    def test_empty_save_box_opens_the_game_folder(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import app
        with tempfile.TemporaryDirectory() as tmp:
            game_dir = Path(tmp) / "Game"
            game_dir.mkdir()
            self.assertEqual(Path(app.browse_start("", str(game_dir)).toLocalFile()), game_dir)
            saves = game_dir / "save"
            saves.mkdir()
            self.assertEqual(Path(app.browse_start(str(saves), str(game_dir)).toLocalFile()), saves)   # box wins

    def test_certain_translation_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, game_dir = Path(tmp) / "home", Path(tmp) / "Games" / "VN"
            game_dir.mkdir(parents=True)
            other_exe = r"D:\Other\VN\vn.exe"
            # (a) inside the other device's game folder: same place here, even before it exists
            self.assertEqual(detect.certain_from_other_device(str(game_dir), other_exe, r"D:\Other\VN\savedata", home),
                             game_dir / "savedata")
            # (b) other user's profile: only when the folder is here with save-like files
            other_save = r"C:\Users\doge_\AppData\Roaming\Maker\VN"
            self.assertIsNone(detect.certain_from_other_device(str(game_dir), other_exe, other_save, home))
            write(home / "AppData" / "Roaming" / "Maker" / "VN", "settings.ini", "x")
            self.assertIsNone(detect.certain_from_other_device(str(game_dir), other_exe, other_save, home))
            write(home / "AppData" / "Roaming" / "Maker" / "VN", "save01.dat", "x")
            self.assertEqual(detect.certain_from_other_device(str(game_dir), other_exe, other_save, home),
                             home / "AppData" / "Roaming" / "Maker" / "VN")
            # anything else stays a suggestion
            self.assertIsNone(detect.certain_from_other_device(str(game_dir), other_exe, r"D:\Saves\VN", home))
            self.assertIsNone(detect.certain_from_other_device("", other_exe, r"D:\Other\VN\savedata", home))

    def test_fills_this_devices_empty_save_folder(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            here = Path(tmp) / "here"
            lib = Library(cloud)
            a = lib.add_game("Inside", "Laptop", exe=r"D:\L\A\a.exe", save_dir=r"D:\L\A\save")
            lib.set_paths(a.game_id, "PC", exe=str(here / "A" / "a.exe"))
            b = lib.add_game("Set", "Laptop", exe=r"D:\L\B\b.exe", save_dir=r"D:\L\B\save")
            lib.set_paths(b.game_id, "PC", exe=str(here / "B" / "b.exe"), save_dir=str(here / "mine"))
            c = lib.add_game("Unsure", "Laptop", exe=r"D:\L\C\c.exe", save_dir=r"E:\Saves\C")
            lib.set_paths(c.game_id, "PC", exe=str(here / "C" / "c.exe"))
            d = lib.add_game("Not here", "Laptop", exe=r"D:\L\D\d.exe", save_dir=r"D:\L\D\save")
            settings = LocalSettings("PC", str(cloud))
            found = app.certain_saves(settings, lib.list_games())
            self.assertEqual(found, {a.game_id: str(here / "A" / "save")})   # a set folder is never replaced
            w = app.MainWindow(settings)
            try:
                with mock.patch.object(w, "_offer_conflicts"):
                    w.refresh()
                    for _ in range(3):
                        app.wait_for_tasks(5)
                        QApplication.processEvents()
                g = Library(cloud).get(a.game_id)
                self.assertEqual(g.paths_for("PC").save_dir, str(here / "A" / "save"))
                self.assertIn(a.game_id, w._ask_now)
                self.assertEqual(Library(cloud).get(b.game_id).paths_for("PC").save_dir, str(here / "mine"))
                self.assertEqual(Library(cloud).get(c.game_id).paths_for("PC").save_dir, "")
                self.assertEqual(Library(cloud).get(d.game_id).paths_for("PC").save_dir, "")
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_settings_open_no_taller_than_the_main_window(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.show()
                w.resize(1200, 560)
                QApplication.processEvents()
                s = app.SettingsDialog(w.settings, w)
                s.show()
                for _ in range(5):
                    QApplication.processEvents()
                self.assertLessEqual(s.frameGeometry().height(), w.frameGeometry().height())
                try:
                    self.assertEqual(s.frameGeometry().top(), w.frameGeometry().top())            # lined up
                    # (Windows 10: 1 px shorter on purpose - its solid border then sits on the main window's last
                    # visible row, which an unfocused window draws almost see-through; see SettingsDialog._height_cap)
                    self.assertEqual(s.frameGeometry().bottom(), w.frameGeometry().bottom() - (1 if app.WINDOWS_10 else 0))
                    self.assertLessEqual(abs(s.frameGeometry().center().x() - w.frameGeometry().center().x()), 1)
                    self.assertGreater(s.findChild(app.QScrollArea).verticalScrollBar().maximum(), 0)   # the rest scrolls
                finally:
                    s.reject()                # (even when failing: an open Settings window upset later tests)
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_save_folder_set_by_hand_asks_straight_away(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            g = lib.add_game("Both", "PC", exe=r"C:\b\b.exe")
            lib.set_paths(g.game_id, "Laptop", exe=r"C:\a\a.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.refresh()
                app.wait_for_tasks(5)
                w.sync_states = {g.game_id: "conflict"}
                w._conflicts_snoozed = {g.game_id}                  # "Later" was said before
                with mock.patch.object(app, "message") as asked, mock.patch.object(w, "sync_now") as now:
                    w._offer_conflicts()
                    self.assertFalse(asked.called or now.called)        # snoozed: quiet, as before
                    w._ask_now = {g.game_id}                            # ...but its save folder was just set here
                    w._offer_conflicts()
                self.assertFalse(asked.called)                          # no Choose Now / Later step
                self.assertEqual([x.game_id for x in now.call_args[0][0]], [g.game_id])
                self.assertEqual(w._ask_now, set())
            finally:
                w.close()
                app.wait_for_tasks(5)


class QuickThemePreview(unittest.TestCase):
    """2026-10-18 (owner: the Custom Theme preview lagged): while Custom Theme is open the style sheet sits on the
    outermost window instead of the whole app (each live step ~52 ms instead of ~131); closing puts it back."""

    def test_sheet_moves_while_open(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            qa = QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            seen = {}

            def opened(dlg):
                seen["root"] = app._SHEET_ROOT[0]
                seen["app_sheet"] = qa.styleSheet()
                c = dict(dlg.colors)
                c["bg"] = "#123456"
                dlg.preview(c)                                          # a live step
                seen["window_sheet"] = w.styleSheet()
                return 0                                                # Cancel
            try:
                qa.setStyleSheet(app.build_style())
                d = app.SettingsDialog(w.settings, w)
                with mock.patch.object(app.ThemeDialog, "exec", opened):
                    d._choose_theme("custom")
                self.assertIs(seen["root"], w)                          # on the main window while open...
                self.assertEqual(seen["app_sheet"], "")
                self.assertIn("#123456", seen["window_sheet"].upper())  # ...and the live step went there
                self.assertIsNone(app._SHEET_ROOT[0])                   # closed: back on the app
                self.assertEqual(w.styleSheet(), "")
                self.assertNotIn("#123456", qa.styleSheet().upper())    # (Cancel: the original look)
                self.assertTrue(qa.styleSheet())
                d.reject()
            finally:
                w.close()
                app.wait_for_tasks(5)


class DropDownWordFades(unittest.TestCase):
    """2026-10-18 (owner): picking in the top bar's sort / status filter fades the box's words with the covers (out as
    they leave, in with the first arriving one); Settings' Language / Device at the bottom status drop-down's pace."""

    def test_words_fade(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            qa = QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            for i, t in enumerate(("Zeta", "Alpha", "Mu")):
                lib.add_game(t, "PC", exe=rf"C:\g{i}\x.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.show()
                w.refresh()
                qa.processEvents()
                w.sort.repaint()
                old = w.sort.painted
                i = w.sort.findData("title" if w.settings.sort != "title" else "hours")
                w.sort.setCurrentIndex(i)
                w.sort.activated.emit(i)
                self.assertEqual(w.sort._words[0], old)                 # fading from the click on
                t0 = w.sort._words[1]
                for _ in range(20):
                    qa.processEvents()
                self.assertEqual(w.sort._words[1], t0)                  # the sort landing kept its clock
                self.assertEqual(w.sort._words[2], max(w.arrive_wait, 0.05))   # new words come in with the first cover
                w.filter.repaint()
                old = w.filter.painted
                w.filter.setCurrentIndex(w.filter.findData("dev:other"))     # (a status keeps the box's words)
                self.assertEqual(w.filter._words[0], old)
                d = app.SettingsDialog(w.settings, w)
                d.show()
                d.device.repaint()
                old = d.device.painted
                j = 1 - d.device.currentIndex()
                d.device.setCurrentIndex(j)
                d.device.activated.emit(j)
                self.assertEqual(d.device._words[:3], (old, d.device._words[1], app.BAR_OUT_S))
                self.assertEqual(d.device._words[3], app.BAR_IN_S)
                d.reject()
            finally:
                w.close()
                app.wait_for_tasks(5)


class QuickSettingsClose(unittest.TestCase):
    """2026-10-18 (owner: Settings lagged hard on close): with the same cloud folder and device, Save doesn't read or
    write the cloud folder on the window's thread (Google Drive kept it waiting seconds); Cancel doesn't re-style the
    app unless a theme was previewed; Settings opens without Windows' see-through open animation."""

    def test_no_cloud_work_on_save(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            Library(cloud).add_game("Sakura", "PC", exe=r"C:\g\x.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.refresh()
                lib = w.library
                d = app.SettingsDialog(w.settings, w)
                with mock.patch.object(app.Library, "slot_owner") as owner, \
                        mock.patch.object(app.Library, "claim_slot") as claim, \
                        mock.patch.object(app, "restyle_all") as restyle:
                    d.pulse.setChecked(False)
                    d._save()
                    w.apply_settings(d.result_settings)
                    owner.assert_not_called()
                    claim.assert_not_called()
                    self.assertIs(w.library, lib)                       # nothing set up again
                    d2 = app.SettingsDialog(w.settings, w)
                    d2.reject()
                    restyle.assert_not_called()                         # Cancel with nothing previewed
                app.wait_for_tasks(5)
                self.assertEqual(len(w.games), 1)                       # (re-read in the background)
                d3 = app.SettingsDialog(w.settings, w)
                d3.device.setCurrentIndex(1 - d3.device.currentIndex())
                with mock.patch.object(app.Library, "slot_owner", return_value=None) as owner:
                    d3._save()
                    owner.assert_called()                               # a new device number: checked as before
            finally:
                w.close()
                app.wait_for_tasks(5)


class LiveColourDrag(unittest.TestCase):
    """2026-10-18 (owner: "liquid smooth" colour picking): while the colour picker is open, each step is drawn from
    two pictures (the role black / white) on a layer over each window - every step at once; the real look is put
    back underneath when the picker closes, and the lights pause meanwhile without jumping."""

    def test_picker_drag(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication, QColorDialog
            from PySide6.QtGui import QColor
            import app
            qa = QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            Library(cloud).add_game("Sakura", "PC", exe=r"C:\g\x.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            seen = {}
            try:
                w.show()
                w.refresh()
                qa.processEvents()
                d = app.SettingsDialog(w.settings, w)
                d.show()
                td = app.ThemeDialog(app.theme_colors("dark"), lambda c: d._preview("custom", c), d)
                td.show()
                qa.processEvents()

                def drag(picker):
                    live = td._live
                    seen["live"] = live is not None and live.role == "bg"
                    seen["layers"] = len(live.parts)
                    seen["held"] = app.PULSE_HOLD[0] is not None
                    with mock.patch.object(app, "restyle_all") as restyle:
                        for h in range(0, 360, 30):                       # the drag: no restyle at all
                            picker.currentColorChanged.emit(QColor.fromHsv(h, 120, 90))
                        seen["restyles"] = restyle.call_count
                    canvas = live.parts[0][4]                             # the main window's picture follows
                    seen["shown"] = canvas.pixelColor(10, 120).name()
                    picker.setCurrentColor(QColor("#2E6B4A"))
                    return 1
                with mock.patch.object(QColorDialog, "exec", drag):
                    td._pick("bg")
                self.assertTrue(seen["live"])
                self.assertEqual(seen["layers"], 2)                       # main window + Settings
                self.assertTrue(seen["held"])
                self.assertEqual(seen["restyles"], 0)
                self.assertEqual(seen["shown"], QColor.fromHsv(330, 120, 90).name())
                self.assertIsNone(td._live)                               # closed: layers gone, real look back
                self.assertIsNone(app.PULSE_HOLD[0])
                self.assertEqual(app.COLORS["bg"].upper(), "#2E6B4A")
                qa.processEvents()
                self.assertEqual([c for c in w.findChildren(app._ColourLayer) if c.isVisible()], [])
                td.reject()
                d.reject()
            finally:
                w.close()
                app.wait_for_tasks(5)


class SoloMode(unittest.TestCase):
    """My Device (2026-10-18): a computer of its own - its own copy of the library, nothing synced - and the way back
    to Dual Device, where everything done meanwhile joins the shared library (nothing lost on either side)."""

    def _lib(self, tmp):
        cloud = Path(tmp) / "cloud"
        cloud.mkdir()
        return cloud, Library(cloud)

    def test_solo_copy_and_merge_back(self):
        import library as L
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp) / "app"
            cloud, C = self._lib(tmp)
            a = C.add_game("Alpha", "PC", exe=r"C:\a\a.exe")
            b = C.add_game("Beta", "PC", exe=r"C:\b\b.exe")
            C.set_paths(b.game_id, "Laptop", exe=r"D:\b\b.exe")
            C.record_session(b.game_id, "Laptop", 600)
            gone = C.add_game("Gamma", "PC", exe=r"C:\g\g.exe")
            C.set_paths(gone.game_id, "Laptop", exe=r"D:\g\g.exe")
            root = L.start_solo_library(str(cloud), app_dir)
            self.assertTrue((root / "games" / f"{a.game_id}.json").is_file())
            self.assertFalse((root / "saves").exists())                       # (the library only, never saves)
            S = Library(root)
            time.sleep(0.05)                                                  # (file times must move on)
            S.record_session(b.game_id, "PC", 120)                            # read here while solo
            S.update(b.game_id, status="finished")
            new = S.add_game("Delta", "PC", exe=r"C:\d\d.exe")                 # added here while solo
            S.remove_here(gone.game_id, "PC")                                  # removed here while solo
            C.record_session(b.game_id, "Laptop", 60)                          # meanwhile on the other device
            report = L.merge_solo(str(cloud), "PC", app_dir=app_dir)
            self.assertEqual(report["added"], 1)
            merged = C.get(b.game_id)
            self.assertEqual(merged.playtime["PC"], 120)                        # this computer's time from solo...
            self.assertEqual(merged.playtime["Laptop"], 660)                    # ...the other device's from the cloud
            self.assertIsNotNone(C.get(new.game_id))
            self.assertTrue(C.get(gone.game_id).removed_here("PC"))             # left this device only
            self.assertEqual(C.get(gone.game_id).paths_for("Laptop").exe, r"D:\g\g.exe")

    def test_same_game_added_on_both_is_offered_to_join(self):
        import library as L
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp) / "app"
            cloud, C = self._lib(tmp)
            L.start_solo_library(str(cloud), app_dir)
            S = Library(L.solo_dir(app_dir))
            mine = S.add_game("Sakura no Toki", "PC", exe=r"C:\vn\sakura.exe")
            S.add_game("Some Other", "PC", exe=r"C:\x\BGI.exe")                  # (a name many games share: no)
            theirs = C.add_game("サクラノ刻", "Laptop", exe=r"D:\vn\sakura.exe")
            C.add_game("Unrelated", "Laptop", exe=r"D:\y\BGI.exe")
            pairs = L.solo_joins(str(cloud), "PC", app_dir)
            self.assertEqual([(m.game_id, t.game_id) for m, t in pairs], [(mine.game_id, theirs.game_id)])
            L.merge_solo(str(cloud), "PC", {mine.game_id: theirs.game_id}, app_dir)
            joined = C.get(theirs.game_id)
            self.assertEqual(joined.paths_for("PC").exe, r"C:\vn\sakura.exe")
            self.assertEqual(joined.paths_for("Laptop").exe, r"D:\vn\sakura.exe")
            self.assertIsNone(C.get(mine.game_id))                              # one game, not two

    def test_backups_keep_the_last_five(self):
        import library as L
        with tempfile.TemporaryDirectory() as tmp:
            app_dir = Path(tmp) / "app"
            cloud, C = self._lib(tmp)
            C.add_game("Alpha", "PC", exe=r"C:\a\a.exe")
            L.start_solo_library(str(cloud), app_dir)
            for _ in range(7):
                L.backup_solo_library(app_dir)
            kept = [d for d in (app_dir / "solo backups").iterdir() if d.is_dir()]
            self.assertEqual(len(kept), 5)
            self.assertTrue(all((d / "games").is_dir() for d in kept))

    def test_window_on_my_device(self):
        """No sync engine, no lights, only this computer's games and time; a session never asks where a game saves
        (nothing syncs - and choosing a folder there crashed: no engine); the time line has no stray leading gap."""
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            import launcher
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            here = lib.add_game("Alpha", "PC", exe=sys.executable)              # set up here, no save folder
            lib.record_session(here.game_id, "Laptop", 3600)                     # (time on the other device too)
            lib.record_session(here.game_id, "PC", 120)
            lib.add_game("Elsewhere", "Laptop", exe=r"D:\e\e.exe")              # not set up on this computer
            w = app.MainWindow(LocalSettings("PC", str(cloud), solo=True))
            try:
                w.resize(1200, 800)
                w.show()
                w.refresh()
                self.assertIsNone(w.engine)
                self.assertEqual(set(w.games), {here.game_id})                    # only this computer's games
                self.assertEqual(w.games[here.game_id].total_playtime, 120)       # only this computer's time
                self.assertEqual(app.LIGHTS_ALPHA[0], 0.0)                         # no sync lights
                self.assertEqual(w.sync_states[here.game_id], "synced")

                class Done:
                    without = ()

                    def wait(self):
                        return True
                w.worker, w.playing_id = Done(), here.game_id
                with mock.patch.object(app, "DetectDialog", side_effect=AssertionError("asked where it saves")):
                    w._play_finished(launcher.PlayResult(True, "", 754.0))
                self.assertEqual(w.statusBar().currentMessage(), "Read for 12m.")  # (no leading gap)
                w.restore_selected()                                              # (no engine: nothing happens)
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_unknown_date_on_my_device(self):
        """Read here before dates were kept per device, and on the other device too: this computer's date isn't
        known -> "Unknown" (owner, 2026-10-18), sorted after dated games."""
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            g = lib.add_game("Alpha", "PC", exe=sys.executable)
            raw = json.loads(lib._file(g.game_id).read_text("utf-8"))
            raw.update(playtime={"PC": 60, "Laptop": 60}, last_played=time.time(), last_played_by={})
            lib._file(g.game_id).write_text(json.dumps(raw), "utf-8")
            w = app.MainWindow(LocalSettings("PC", str(cloud), solo=True))
            try:
                w.refresh()
                self.assertEqual(w.games[g.game_id].last_played, -1.0)
                self.assertEqual(app.day_text(-1.0), "Unknown")
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_guide_says_only_what_applies(self):
        import guide
        dots = {k: "#000" for k in ("green", "amber", "blue", "red", "grey", "lavender", "yellow")}
        for lang in ("en", "ja"):
            dual = guide.guide_html(lang, {"text": "#fff"}, dots, solo=False)
            solo = guide.guide_html(lang, {"text": "#fff"}, dots, solo=True)
            for page in (dual, solo):
                self.assertNotIn("§", page)
                self.assertNotIn("¤", page)
            self.assertIn("Sync Lights" if lang == "en" else "同期ランプ", dual)
            self.assertNotIn("Sync Lights" if lang == "en" else "同期ランプ", solo)


class AuditFixes(unittest.TestCase):
    """The 1.1.0 audit (2026-10-18): each test pins a bug found there."""

    def test_quick_process_lookup_survives_the_playtime_clock(self):
        # the playtime clock set kernel32's argument types for the whole program; the quick lookup then failed after
        # the first launch of every session (silently using slow psutil: the 10-17 hitches came back)
        import launcher
        if not IS_WINDOWS:
            self.skipTest("Windows only")
        before = launcher._details_win(os.getpid())
        launcher._ExactClock(time.time())
        after = launcher._details_win(os.getpid())
        self.assertIsNotNone(after)
        self.assertEqual(before, after)

    def test_a_check_that_fails_is_not_called_not_set_up(self):
        # a save folder that can't be read just now (file in use, Drive busy) said "Not Set Up on This Device Yet"
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(Path(tmp) / "cloud")
            g = lib.add_game("Alpha", "PC", exe=sys.executable, save_dir=str(Path(tmp) / "saves"))
            engine = SyncEngine(Path(tmp) / "cloud", Path(tmp) / "state")
            c = app.StatusChecker(engine, [g], "PC", set())
            got = {}
            c.done.connect(lambda states, up, down: got.update(states))
            with mock.patch.object(engine, "status", side_effect=PermissionError("in use")):
                c.run()
            self.assertEqual(got[g.game_id], "failed")
            self.assertEqual(app.STATE_LOOK["failed"][2], "sync.state.failed")

    def test_luna_check_uses_the_quick_program_list(self):
        from unittest import mock
        import psutil
        with mock.patch.object(psutil, "process_iter", side_effect=AssertionError("slow psutil scan")):
            self.assertFalse(luna.running(r"C:\nowhere\LunaTranslator.exe"))

    def test_one_game_is_singular_on_discord(self):
        import i18n
        i18n.set_language("en")
        self.assertEqual(i18n.tr("presence.games_one"), "1 Game in Library")

    def test_quick_double_launch_opens_one_copy(self):
        # clicking the icon several times quickly opened several windows (owner's laptop): a copy only answered once its
        # window was built. Now the first copy holds a Windows lock from its very first moment
        if not IS_WINDOWS:
            self.skipTest("Windows only")
        from unittest import mock
        key = f"IkemenReader-test-{uuid.uuid4().hex[:8]}"
        holder = subprocess.Popen([sys.executable, "-c", (
            "import sys, time; sys.path.insert(0, r'" + str(Path(__file__).parent) + "'); import app; "
            f"app.INSTANCE_KEY = '{key}'; print(app.claim_instance(), flush=True); time.sleep(20)")],
            stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "True")          # the first copy: it's the only one
            import app
            with mock.patch.object(app, "INSTANCE_KEY", key):
                self.assertFalse(app.claim_instance())                         # a second one is told it isn't
        finally:
            holder.kill()
            holder.wait()
            holder.stdout.close()

    def test_stuck_copy_is_ended_when_asked(self):
        # 2.0.7, laptop: a copy whose window had closed stayed running unseen, holding the lock, so every later start
        # quietly did nothing. Now the new copy asks, and ends the stuck one
        if not IS_WINDOWS:
            self.skipTest("Windows only")
        from unittest import mock
        import psutil
        import app
        stuck = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            p = psutil.Process(stuck.pid)
            with mock.patch.object(app, "other_copies", return_value=[p]),                     mock.patch.object(app, "message", return_value="cancel"):
                self.assertFalse(app.take_over_from_stuck_copy())            # Cancel: nothing ended, nothing opens
            self.assertIsNone(stuck.poll())
            with mock.patch.object(app, "other_copies", return_value=[p]),                     mock.patch.object(app, "message", return_value="end") as asked:
                self.assertTrue(app.take_over_from_stuck_copy())
            self.assertEqual(asked.call_args.args[2], app.tr("instance.stuck_title"))
            self.assertIsNotNone(stuck.wait(5))                            # ended
            with mock.patch.object(app, "other_copies", return_value=[]),                     mock.patch.object(app, "message") as asked:
                self.assertTrue(app.take_over_from_stuck_copy())             # gone meanwhile: just opens
            asked.assert_not_called()
        finally:
            stuck.kill()
            stuck.wait()

    def test_frozen_copy_does_not_count_as_answering(self):
        # Windows accepts the connection for a frozen copy (or one stuck while closing) whose listener is still open:
        # that alone used to count as "handed over", so every start quietly quit. Only a reply counts now
        if not IS_WINDOWS:
            self.skipTest("Windows only")
        from unittest import mock
        import app
        here = str(Path(__file__).parent)
        for frozen in (False, True):
            key = f"IkemenReader-test-{uuid.uuid4().hex[:8]}"
            code = ("import os, sys, time; os.environ['QT_QPA_PLATFORM'] = 'offscreen'; sys.path.insert(0, r'" + here
                    + "'); import app; from PySide6.QtWidgets import QApplication, QWidget; from PySide6.QtCore import QTimer; "
                    f"app.INSTANCE_KEY = '{key}'; a = QApplication([]); w = QWidget(); app.listen_for_second_copies(w); "
                    + ("print('ready', flush=True); time.sleep(20)" if frozen else      # (never runs its events)
                       "QTimer.singleShot(0, lambda: print('ready', flush=True)); QTimer.singleShot(20000, a.quit); a.exec()"))
            holder = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline().strip(), "ready")
                with mock.patch.object(app, "INSTANCE_KEY", key):
                    self.assertEqual(app.hand_over_to_running_copy(), not frozen)
            finally:
                holder.kill()
                holder.wait()
                holder.stdout.close()

    def test_closing_never_hangs(self):
        # the closing copy ends itself if quitting takes too long, and writes down what was still running
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            env = dict(os.environ, APPDATA=tmp)
            code = ("import sys, time, threading; sys.path.insert(0, r'" + str(Path(__file__).parent) + "'); import app; "
                    "threading.Thread(target=time.sleep, args=(60,), name='stuck-upload').start(); "
                    "app.exit_watchdog(0.5); sys.exit(5)")       # (sys.exit waits for the non-daemon thread forever)
            start = time.monotonic()
            done = subprocess.run([sys.executable, "-c", code], env=env, timeout=30)
            self.assertLess(time.monotonic() - start, 20)
            self.assertEqual(done.returncode, 0)
            log = (Path(tmp) / "IkemenReader" / "exit.log").read_text("utf-8")
            self.assertIn("still running", log)
            self.assertIn("threading.py", log)                       # (each thread's stack is written down)
            code = ("import sys, time; sys.path.insert(0, r'" + str(Path(__file__).parent) + "'); import app; "
                    "app.exit_watchdog(0.3).set(); time.sleep(1); sys.exit(5)")
            self.assertEqual(subprocess.run([sys.executable, "-c", code], env=env, timeout=30).returncode, 5)  # called off

    def test_window_opens_with_its_real_picture(self):
        # Windows' open animation faded in a WHITE window: the first paint came too late (owner, 2026-10-18). Now the
        # covers it opens with are decoded before it's shown, its picture is drawn into it just before Windows shows it,
        # and the window is never hidden (cloaked) - so Windows' own open animation plays, with the real window
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtGui import QImage, QColor
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            art = Path(tmp) / "cover.png"
            img = QImage(400, 560, QImage.Format_RGB32)
            img.fill(QColor("#884422"))
            img.save(str(art))
            for i in range(3):
                lib.add_game(f"Game {i}", "PC", exe=sys.executable, artwork_src=str(art))
            calls = []
            real = app._dwm_flag
            with mock.patch.object(app, "_dwm_flag", lambda w, attr, on: (calls.append((attr, on)), real(w, attr, on))):
                w = app.MainWindow(LocalSettings("PC", str(cloud)))
                try:
                    w.prepare_first_screen()
                    g = next(iter(w.games.values()))
                    self.assertTrue(w.covers.ready(w.art(g), (g.crop_zoom, g.crop_x, g.crop_y)))   # (decoded already)
                    w.show()
                    QApplication.processEvents()
                    app.paint_into_window(w)                                  # (never raises, whatever the platform)
                    self.assertNotIn((13, True), calls)                       # never hidden: Windows animates it
                finally:
                    w.close()
                    app.wait_for_tasks(5)

    def test_nothing_animates_while_minimized(self):
        # minimized, the breathing lights still drew every frame for nobody (6-8 % of a core); restored, they carry on
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtCore import Qt
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            w = app.MainWindow(LocalSettings("PC", str(cloud), pulse=True))
            try:
                w.show()
                QApplication.processEvents()
                w._update_anim()
                self.assertTrue(w.anim.isActive())                   # lights breathing
                w.setWindowState(Qt.WindowMinimized)
                QApplication.processEvents()
                self.assertFalse(w.anim.isActive())                  # minimized: still
                w.setWindowState(Qt.WindowNoState)
                QApplication.processEvents()
                self.assertTrue(w.anim.isActive())                   # restored: breathing again
            finally:
                w.close()
                app.wait_for_tasks(5)


class LaptopSmoothness(unittest.TestCase):
    """Found on the laptop (2026-10-18; weak Windows 11, 60 Hz), scrolling a big library."""

    def _window(self, tmp, n=1):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        cloud = Path(tmp) / "cloud"
        cloud.mkdir()
        lib = Library(cloud)
        for i in range(n):
            lib.add_game(f"Game {i:03d}", "PC", exe=rf"C:\g{i}\g.exe")
        w = app.MainWindow(LocalSettings("PC", str(cloud)))
        w.resize(1200, 800)
        w.show()
        w.refresh()
        return app, w

    def _run(self, seconds):
        from PySide6.QtWidgets import QApplication
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.004)

    def test_lights_frame_after_a_full_redraw_draws_nothing_twice(self):
        # scrolling redraws the whole grid every frame; the lights frame then made the still picture as well (a second
        # full drawing per frame): now it skips that frame - the lights were just drawn with everything else
        from unittest import mock
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            app, w = self._window(tmp)
            try:
                w.settings.pulse = True
                w.anim.stop()
                self._run(0.3)
                grid = w.grid
                grid._still, grid.painted_all = None, True
                with mock.patch.object(app.LibraryGrid, "_make_still") as make:
                    w._tick()
                make.assert_not_called()
                self.assertIsNone(grid.light_frame)
                self.assertFalse(grid.painted_all)                     # one frame only
                w._tick()
                self.assertIsNotNone(grid._still)                        # the next quiet frame makes it as usual
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_still_picture_moves_with_a_scroll(self):
        # without a background picture Qt moves the covers and draws only the uncovered strip: the lights frames'
        # picture stayed put, so squares of what was there before showed around the lights
        from unittest import mock
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            app, w = self._window(tmp, 40)
            try:
                self._run(0.5)
                grid = w.grid
                grid.painted_all = False
                self.assertTrue(grid.prepare_still())
                before = grid._still.toImage()
                grid.scrollContentsBy(0, -30)
                after = grid._still.toImage()
                k = grid._still.devicePixelRatio()
                y = round(100 * k)
                spot = [(x, y) for x in range(0, before.width(), 7)
                        if before.pixel(x, y) != before.pixel(x, y + round(30 * k))]
                self.assertTrue(spot)                                # (somewhere the picture differs 30 px lower)
                self.assertTrue(all(after.pixel(x, y) == before.pixel(x, y + round(30 * k)) for x, _ in spot))
                self.assertEqual(after.pixel(round(300 * k), y), before.pixel(round(300 * k), y + round(30 * k)))
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_covers_prepared_ahead_are_kept(self):
        # with the cover memory full, covers prepared just around the view were dropped at once (and then decoded
        # while drawing); they push out the covers drawn longest ago instead - but the broad start-up preparation
        # never pushes out drawn ones (a sort brought them straight back)
        import app
        from PySide6.QtGui import QImage
        c = app.CoverCache()
        img = QImage(4, 4, QImage.Format_RGB32)
        for i in range(c.MAX_COVERS):
            c._cache[(f"d{i}", 1.0)] = None
        c.accept([(Path(f"w{i}.png"), 1.0, (1.0, 0.5, 0.5), img) for i in range(5)])
        self.assertEqual(len(c._ready), 0)                                   # broad preparation: drawn ones stay
        self.assertEqual(len(c._cache), c.MAX_COVERS)
        c._mtimes.clear()
        c.accept([(Path(f"a{i}.png"), 1.0, (1.0, 0.5, 0.5), img) for i in range(5)], ahead=True)
        self.assertEqual(len(c._ready), 5)                                   # around the view: kept
        self.assertNotIn(("d0", 1.0), c._cache)                              # the oldest drawn made room
        self.assertIn((f"d{c.MAX_COVERS - 1}", 1.0), c._cache)
        for i in range(200):
            c._mtimes.clear()
            c.accept([(Path(f"b{i}.png"), 1.0, (1.0, 0.5, 0.5), img)], ahead=True)
        self.assertGreaterEqual(len(c._cache), c.KEEP_DRAWN)                  # what's on screen is never dropped
        self.assertLessEqual(len(c._cache) + len(c._ready), c.MAX_COVERS)

    def test_folder_whose_mosaic_changes_fades_instead_of_decoding_mid_frame(self):
        # a sort that reorders a folder's games (it stays in place) changed its mosaic at once, its new covers decoded
        # while drawing (a 64 ms frame on the PC): it now fades out and in like a cover that moved
        from unittest import mock
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            app, w = self._window(tmp, 0)
            try:
                lib = w.library
                fid = lib.create_folder("Box")
                for i, name in enumerate(["Alpha", "Bravo", "Charlie", "Delta", "Echo"]):
                    g = lib.add_game(name, "PC", exe=rf"C:\f{i}\g.exe")
                    lib.set_folder(g.game_id, fid)
                    lib.record_session(g.game_id, "PC", 1000 * (5 - i) if i else 10, time.time())
                w.refresh()
                w.choose_sort("title")
                self._run(0.6)
                iid = app.FOLDER_PREFIX + fid
                mosaic = lambda: [g.game_id for g in w.folder_games[fid][:4]]
                before, place = mosaic(), w._positions()[iid]
                w.choose_sort("hours")
                self.assertNotEqual(mosaic(), before)                            # other covers...
                self.assertEqual(w._positions()[iid], place)                     # ...in the same place
                self.assertIn(iid, w.appearing)                                  # fades in afresh
            finally:
                w.close()
                app.wait_for_tasks(5)


class DeviceFilter(unittest.TestCase):
    """2026-10-18 (owner): the drop-down next to the sort picks the library view - This Device (default) / Other Device
    (set up ONLY there) / Both Devices - a whole library, folders and all, in the sort in use. The reading statuses
    below a divider filter within it as before; the box always shows the view; no ticks."""

    def test_empty_view_says_so(self):
        """An empty view (nothing searched / filtered) has its own words, not "No Results Found" (owner, 2026-10-19:
        a new second computer opens on This Device with nothing set up yet)."""
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            g = lib.add_game("Everywhere", "Laptop", exe=r"D:\a\a.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            pick = lambda data: w.filter.setCurrentIndex(w.filter.findData(data))
            try:
                w.show()
                w.refresh()
                self.assertEqual(w.grid.count(), 0)                           # set up only on the other device
                w._no_results_target()
                self.assertEqual(w.no_results_key, "grid.empty_here")
                search_now(w, "zzz")
                w._no_results_target()
                self.assertEqual(w.no_results_key, "grid.no_results")         # a search that finds nothing
                search_now(w, "")
                lib.set_paths(g.game_id, "PC", exe=r"C:\a\a.exe")             # now on both
                w.refresh()
                pick("dev:other")
                end = time.monotonic() + 1.5                                  # (once the cover has faded out)
                while time.monotonic() < end and (w._leaving or w._no_results_target() == 0):
                    QApplication.processEvents()
                    time.sleep(0.01)
                self.assertEqual(w.no_results_key, "grid.empty_other")
                self.assertTrue(app.tr("grid.empty_here") and app.tr("grid.empty_other"))
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_backspacing_a_search_keeps_its_words(self):
        """Backspacing a search that found nothing: until the games come back (the search waits SEARCH_PAUSE_MS),
        the message still says "No Results Found" - it flashed "No Games on This Device" (owner, 2026-10-29)."""
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            Library(cloud).add_game("Here", "PC", exe=r"C:\a\a.exe")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.show()
                w.refresh()
                search_now(w, "zzz")
                end = time.monotonic() + 1.5                                 # (the cover fades out, the message in)
                while time.monotonic() < end and (w._leaving or w._no_results_target() == 0):
                    QApplication.processEvents()
                    time.sleep(0.01)
                self.assertEqual(w.no_results_key, "grid.no_results")
                w.search.setText("")                                         # backspaced: not applied yet
                self.assertTrue(w._search_pause.isActive())
                w._no_results_target()
                self.assertEqual(w.no_results_key, "grid.no_results")        # same words until the games return
                search_now(w, "")
                self.assertEqual(w.grid.count(), 1)
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_library_views(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            here = lib.add_game("Here", "PC", exe=r"C:\a\a.exe")
            both = lib.add_game("Both", "PC", exe=r"C:\b\b.exe")
            lib.set_paths(both.game_id, "Laptop", exe=r"D:\b\b.exe")
            there = lib.add_game("There", "Laptop", exe=r"D:\c\c.exe")
            in_box = lib.add_game("Boxed", "PC", exe=r"C:\d\d.exe")
            away = lib.add_game("Away", "Laptop", exe=r"D:\e\e.exe")
            fid = lib.create_folder("Box")
            lib.set_folder(in_box.game_id, fid)
            lib.set_folder(away.game_id, fid)
            lib.update(both.game_id, status="reading")
            lib.update(there.game_id, status="reading")
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            folder = app.FOLDER_PREFIX + fid
            shown = lambda: sorted(w.grid.item(i).data(app.GAME_ID_ROLE) for i in range(w.grid.count()))
            pick = lambda data: w.filter.setCurrentIndex(w.filter.findData(data))
            try:
                w.show()
                w.refresh()
                data = [w.filter.itemData(i) for i in range(w.filter.count())]
                self.assertEqual(data[:3], ["dev:here", "dev:other", "dev:both"])
                self.assertIsNone(data[3])                                    # the divider
                self.assertEqual(data[4], "all")
                self.assertEqual(w.filter.currentText(), "This Device")       # the default view
                # This Device: its games, the folder stays a folder holding only this device's game
                self.assertEqual(shown(), sorted([here.game_id, both.game_id, folder]))
                self.assertEqual([g.game_id for g in w.folder_games[fid]], [in_box.game_id])
                pick("dev:other")                                             # only set up on the other device
                self.assertEqual(shown(), sorted([there.game_id, folder]))
                self.assertEqual([g.game_id for g in w.folder_games[fid]], [away.game_id])
                self.assertEqual(w.filter.currentText(), "Other Device")
                pick("reading")                                               # a status within the view
                self.assertEqual(shown(), [there.game_id])
                self.assertEqual(w.filter.currentText(), "Other Device")      # the box keeps the view
                pick("dev:here")                                              # still Reading
                self.assertEqual(shown(), [both.game_id])
                pick("all")
                pick("dev:both")
                self.assertEqual(shown(), sorted([here.game_id, both.game_id, there.game_id, folder]))
                self.assertEqual(len(w.folder_games[fid]), 2)
                # rearranging in a view: the games it hides keep their places
                pick("dev:here")
                order = [w.grid.item(i).data(app.GAME_ID_ROLE) for i in range(w.grid.count())]
                full_before = app.merge_order(w._lib_order() + w._all_top_ids(), order)
                shared = lib.library_order()
                w._move_to(order[-1], 0)
                full = list(w._lib_order())
                self.assertEqual(lib.library_order(), shared)                # the other device sees no change
                hidden = [i for i in full_before if i not in order]
                self.assertEqual([i for i in full if i in hidden], hidden)    # hidden ones: same order...
                for h in hidden:                                              # ...and same places
                    self.assertEqual(full.index(h), full_before.index(h))
                w.settings.solo = True                                        # My Device: no views
                w._apply_mode()
                self.assertNotIn("dev:here", [w.filter.itemData(i) for i in range(w.filter.count())])
                self.assertEqual(w._device_filter(), "both")
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_merge_order(self):
        import app
        self.assertEqual(app.merge_order(["a", "x", "b", "y", "c"], ["c", "a", "b"]), ["c", "x", "a", "y", "b"])
        self.assertEqual(app.merge_order(["a"], ["b", "a"]), ["b", "a"])               # (new ones at the end of the slots)


class DropDownShadow(unittest.TestCase):
    """2026-10-18 (owner: drop-downs "flashing a little"): Windows' shadow lagged the slide-open; ours is drawn inside
    the list's own window (Windows' one switched off), with Windows' measured look."""

    def test_own_shadow(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor
        import app
        qa = QApplication.instance() or QApplication([])
        holder = QWidget()
        combo = app.ThemedCombo()
        QVBoxLayout(holder).addWidget(combo)
        for t in ("One", "Two", "Three"):
            combo.addItem(t)
        holder.show()
        qa.processEvents()
        try:
            combo.showPopup()
            qa.processEvents()
            box, view = combo.view().parentWidget(), combo.view()
            self.assertTrue(box.windowFlags() & Qt.NoDropShadowWindowHint)          # Windows' shadow off
            self.assertTrue(box.windowFlags() & Qt.FramelessWindowHint)             # (else not see-through)
            s = app._ListShadow.SIZE
            v = view.geometry()                                                      # room right of / below the list
            self.assertGreaterEqual(box.width() - v.right() - 1, s)
            self.assertGreaterEqual(box.height() - v.bottom() - 1, s)
            img = app.drop_shadow(view.width(), view.height())
            mid = view.height() // 2
            self.assertEqual([QColor.fromRgba(img.pixel(view.width() - 1 + d, mid)).alpha() for d in range(1, 6)],
                             [113, 84, 43, 14, 3])                                  # Windows' measured profile
            self.assertEqual(QColor.fromRgba(img.pixel(view.width() - 1 + 1, 2)).alpha(), 0)   # eases in at the top
            combo.hidePopup()
            combo.showPopup()                                                        # opened again: same size
            qa.processEvents()
            v = view.geometry()                                                      # room right of / below the list
            self.assertGreaterEqual(box.width() - v.right() - 1, s)
            self.assertGreaterEqual(box.height() - v.bottom() - 1, s)
        finally:
            combo.hidePopup()
            holder.close()


class LaunchPass(unittest.TestCase):
    """1.1.4 (owner, 2026-10-19): the window opens where it was last closed (not on the mouse's screen), nothing is
    active at launch (the search box took the keyboard), and Settings never opens on a half-cut row."""

    def test_window_pos_setting_is_checked(self):
        from library import load_local_settings
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for given, expected in (([100, -1700], [100, -1700]), ([1.5, 2], [1, 2]), ("x", []), ([1, 2, 3], []),
                                    ([True, 1], []), ([1e9, 1], []), (None, [])):
                data = {"machine_name": "PC", "cloud_root": str(d)}
                if given is not None:
                    data["window_pos"] = given
                (d / "settings.json").write_text(json.dumps(data), "utf-8")
                self.assertEqual(load_local_settings(d).window_pos, expected, given)

    def _window(self, tmp, **kw):
        import app
        cloud = Path(tmp) / "cloud"
        cloud.mkdir(exist_ok=True)
        return app.MainWindow(LocalSettings("PC", str(cloud), **kw))

    def test_opens_where_it_was_closed(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            from library import load_local_settings
            QApplication.instance() or QApplication([])
            room = QApplication.primaryScreen().availableGeometry()
            spot = [room.left() + 40, room.top() + 50]
            size = (min(400, room.width() - 80), min(520, room.height() - 60))   # (fits the test screen)
            w = self._window(tmp)
            try:
                w.setGeometry(spot[0], spot[1], *size)
                w._remember_position()
                self.assertEqual(load_local_settings().window_pos, spot)
            finally:
                w.close()
            w = self._window(tmp, window_pos=spot)
            try:
                w.resize(*size)
                w.place_at_last_position()
                self.assertEqual([w.geometry().x(), w.geometry().y()], spot)
            finally:
                w.close()
            # a spot on no screen (a monitor unplugged since): left to Windows, as before
            w = self._window(tmp, window_pos=[-90000, -90000])
            try:
                before = w.geometry()
                w.place_at_last_position()
                self.assertEqual(w.geometry(), before)
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_nothing_active_at_launch(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            (Path(tmp) / "cloud").mkdir()
            Library(Path(tmp) / "cloud").add_game("A Game", "PC", exe=r"C:\a\a.exe")    # (a cover to select)
            w = self._window(tmp)
            try:
                w.show()
                w.activateWindow()
                for _ in range(20):
                    QApplication.processEvents()
                    time.sleep(0.01)
                self.assertFalse(w.search.hasFocus())
                self.assertEqual(w.grid.selectedItems(), [])
                self.assertIsNone(w.selected_id())
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_settings_opens_on_whole_rows(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication, QScrollArea, QWidget
            from PySide6.QtCore import QPoint
            import app
            QApplication.instance() or QApplication([])
            w = self._window(tmp)
            try:
                w.show()
                w.resize(w.width(), 700)                 # (shorter than Settings needs: it scrolls)
                QApplication.processEvents()
                d = app.SettingsDialog(w.settings, w)
                d.show()
                for _ in range(30):
                    QApplication.processEvents()
                    time.sleep(0.01)
                scroll = d.findChild(QScrollArea)
                self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
                edge = scroll.viewport().height()
                content = scroll.widget()
                for wd in content.findChildren(QWidget):
                    if not wd.isVisibleTo(content) or wd.height() > d.HALF_ROW_MAX or \
                            any(c.isVisibleTo(content) for c in wd.findChildren(QWidget)):
                        continue
                    top = wd.mapTo(content, QPoint(0, 0)).y()
                    self.assertFalse(top < edge < top + wd.height(), f"{type(wd).__name__} cut at the edge")
                d.reject()
            finally:
                w.close()
                app.wait_for_tasks(5)


class SetupSavesLabelled(unittest.TestCase):
    """2026-10-19 (owner): "Find It While Reading" - the game wrote its files just by starting, so that copy had the
    newest date and was marked "(Newer)" against the other device's real progress. Saves made in that session are
    remembered (Game.setup_saves) and the "which to keep" window names them instead of calling them newer."""

    def test_recorded_only_when_every_file_is_new(self):
        from sync_engine import all_made_since
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            saves = Path(tmp) / "saves"
            self.assertFalse(all_made_since(saves, 0))                    # (no folder)
            saves.mkdir()
            self.assertFalse(all_made_since(saves, 0))                    # (no files)
            (saves / "system.dat").write_bytes(b"x")
            self.assertTrue(all_made_since(saves, time.time() - 60))
            self.assertFalse(all_made_since(saves, time.time() + 60))     # a file from before the session

    def _two_devices(self, tmp: str):
        root = Path(tmp)
        cloud = root / "cloud"
        cloud.mkdir()
        lib = Library(cloud)
        g = lib.add_game("Game", "PC", exe=str(root / "pc" / "g.exe"))
        lib.set_paths(g.game_id, "Laptop", exe=str(root / "lap" / "g.exe"))
        pc_saves = root / "pc_saves"
        pc_saves.mkdir()
        (pc_saves / "save01.dat").write_bytes(b"hours of progress")
        lib.set_paths(g.game_id, "PC", save_dir=str(pc_saves))
        pc = SyncEngine(cloud, root / "pc_state")
        self.assertEqual(pc.before_launch(g.game_id, pc_saves).action, Action.PUSHED)
        lap_saves = root / "lap_saves"
        lap_saves.mkdir()
        old = time.time() - 3600                                         # the cloud copy's dates: an hour ago
        for f in (cloud / "saves" / g.game_id).rglob("*"):
            os.utime(f, (old, old))
        return root, cloud, lib, g, pc, lap_saves

    def test_first_play_records_and_the_window_names_it(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QPushButton
        import app
        import i18n
        QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root, cloud, lib, g, pc, lap_saves = self._two_devices(tmp)
            started = time.time()
            (lap_saves / "config.dat").write_bytes(b"made by starting")  # the game opened on the laptop
            lap = SyncEngine(cloud, root / "lap_state")
            seen = []
            res = finish_first_play(lib.get(g.game_id), LocalSettings("Laptop", str(cloud)), lib, lap, str(lap_saves),
                                    on_conflict=lambda info: seen.append(info) or None, started_at=started)
            self.assertEqual(res.action, Action.CONFLICT)
            info = seen[0]
            setup = lib.get(g.game_id).setup_saves
            self.assertEqual(setup, {"Laptop": info.local_fp})
            self.assertGreater(info.local_mtime, info.cloud_mtime)        # by date the laptop's copy IS newer
            texts = lambda d: [b.text() for b in d.findChildren(QPushButton)]
            for lang in ("en", "ja"):
                i18n.set_language(lang)
                try:
                    d = app.ConflictDialog("Game", info, first_sync=True, setup=setup, machine="Laptop")
                    local, cloud_b = texts(d)[:2]
                    self.assertIn(i18n.tr("conflict.setup_here"), local)
                    self.assertNotIn(i18n.tr("conflict.newer"), local)
                    self.assertNotIn(i18n.tr("conflict.setup_here"), cloud_b)
                    self.assertTrue(d.findChildren(QPushButton)[1].isDefault())   # Enter = the real saves
                    d.deleteLater()
                    # the same copy seen from the PC (the laptop kept it and uploaded): "the Other Device"
                    flipped = type(info)(info.game_id, info.cloud_mtime, info.local_mtime, info.cloud_fp, info.local_fp)
                    d = app.ConflictDialog("Game", flipped, setup=setup, machine="PC")
                    self.assertIn(i18n.tr("conflict.setup_other"), texts(d)[1])
                    self.assertNotIn(i18n.tr("conflict.newer"), texts(d)[1])
                    d.deleteLater()
                    # no setup record: as before
                    d = app.ConflictDialog("Game", info, first_sync=True)
                    self.assertIn(i18n.tr("conflict.newer"), texts(d)[0])
                    d.deleteLater()
                finally:
                    i18n.set_language("en")
            # read on the laptop after that: the files changed, the label goes
            (lap_saves / "save01.dat").write_bytes(b"real progress here")
            later = []
            lap.before_launch(g.game_id, lap_saves, lambda i: later.append(i) or None)
            self.assertNotEqual(later[0].local_fp, setup["Laptop"])

    def test_not_recorded_for_older_saves_or_registry(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root, cloud, lib, g, pc, lap_saves = self._two_devices(tmp)
            (lap_saves / "save01.dat").write_bytes(b"played before, outside Ikemen Reader")
            lap = SyncEngine(cloud, root / "lap_state")
            finish_first_play(lib.get(g.game_id), LocalSettings("Laptop", str(cloud)), lib, lap, str(lap_saves),
                              on_conflict=lambda info: None, started_at=time.time() + 60)
            self.assertEqual(lib.get(g.game_id).setup_saves, {})
            finish_first_play(lib.get(g.game_id), LocalSettings("Laptop", str(cloud)), lib, lap, str(lap_saves),
                              on_conflict=lambda info: None)                 # (no session time: nothing)
            self.assertEqual(lib.get(g.game_id).setup_saves, {})

    def test_damaged_field_is_ignored(self):
        from library import Game
        g = Game.from_json(json.dumps({"game_id": "g", "title": "G", "setup_saves": {"PC": 5, "Laptop": "ab", "Old": ""}}))
        self.assertEqual(g.setup_saves, {"Laptop": "ab"})
        self.assertEqual(Game.from_json(json.dumps({"game_id": "g", "title": "G", "setup_saves": []})).setup_saves, {})


class BarFolderButton(unittest.TestCase):
    """2026-10-19 (owner): on My Device nothing syncs, so the bar's folder button opens the GAME's folder (the one
    with the .exe); with two devices it stays Open Save Folder. The right-click menu keeps both either way."""

    def test_follows_the_mode(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            game_dir, saves = Path(tmp) / "game", Path(tmp) / "saves"
            game_dir.mkdir(); saves.mkdir()
            lib = Library(cloud)
            g = lib.add_game("Game", "PC", exe=str(game_dir / "g.exe"))
            lib.set_paths(g.game_id, "PC", save_dir=str(saves))
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            opened = []
            try:
                w.show()
                w.refresh()
                w._select(g.game_id)
                with mock.patch.object(app, "open_folder", lambda p: opened.append(p)):
                    self.assertEqual(w.saves_btn.text(), app.tr("bar.open_saves"))
                    w.saves_btn.click()
                    w.settings.solo = True
                    w._apply_mode()
                    end = time.monotonic() + 2                              # (the switch fades: done at its end)
                    while time.monotonic() < end and w._mode_anim is not None:
                        QApplication.processEvents()
                        time.sleep(0.01)
                    self.assertEqual(w.saves_btn.text(), app.tr("bar.open_game"))
                    self.assertTrue(w.saves_btn.isEnabled())
                    w.saves_btn.click()
                self.assertEqual([Path(p) for p in opened], [saves, game_dir])
            finally:
                w.close()
                app.wait_for_tasks(5)


class WheelGlide(unittest.TestCase):
    """2026-10-19 (owner): a mouse-wheel notch glides (it jumped 72 px); more notches extend the same glide; the scroll
    bar / keyboard / anything else moving the view takes over at once; touchpads (small steps) scroll directly."""

    def test_glide(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            from PySide6.QtGui import QWheelEvent
            from PySide6.QtCore import QPoint, QPointF, Qt
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            for i in range(40):
                lib.add_game(f"Game {i}", "PC", exe=str(Path(tmp) / f"g{i}" / "g.exe"))
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            try:
                w.show()
                w.refresh()
                vp, sb = w.grid.viewport(), w.grid.verticalScrollBar()
                self.assertGreater(sb.maximum(), 400)

                def wheel(dy, pixels=0):
                    c = QPointF(vp.width() / 2, vp.height() / 2)
                    QApplication.sendEvent(vp, QWheelEvent(c, c, QPoint(0, pixels), QPoint(0, dy), Qt.NoButton,
                                                           Qt.NoModifier, Qt.NoScrollPhase, False))

                def settle(sec=3.0):                               # (a busy full run can starve the clock)
                    end = time.monotonic() + sec
                    while time.monotonic() < end and w.grid._glide_to is not None:
                        QApplication.processEvents()
                        time.sleep(0.005)

                # (the window's own start-up work first: under a busy full run a library read landing mid-glide
                # rebuilt the grid, which rightly ends a glide - the test then saw it stop short)
                app.wait_for_tasks(5)
                end = time.monotonic() + 0.8
                while time.monotonic() < end:
                    QApplication.processEvents()
                    time.sleep(0.005)
                step = round(w.grid.notch_px())
                sb.setValue(0)
                wheel(-120)
                self.assertLess(sb.value(), step)                 # not there at once: it glides
                wheel(-120)                                        # a second notch extends the same glide
                settle()
                self.assertEqual(sb.value(), 2 * step)
                wheel(-120)
                QApplication.processEvents()
                sb.setValue(10)                                    # the scroll bar takes over mid-glide
                settle(0.3)
                self.assertEqual(sb.value(), 10)
                self.assertIsNone(w.grid._glide_to)
                wheel(-40)                                         # a fine wheel's part of a notch glides too
                self.assertIsNotNone(w.grid._glide_to)
                settle()
                self.assertEqual(sb.value(), 10 + round(step / 3))
                wheel(-40, pixels=-40)                             # a touchpad's step: straight away, no glide
                self.assertIsNone(w.grid._glide_to)
                self.assertGreater(sb.value(), 10 + round(step / 3))
                sb.setValue(0)
                wheel(120)                                         # already at the top: stays there
                settle()
                self.assertEqual(sb.value(), 0)
            finally:
                w.close()
                app.wait_for_tasks(5)


class GlideStrips(unittest.TestCase):
    """2026-10-19 (owner: "smooth as butter like Steam"): while a wheel glide runs, the covers are drawn once into
    strips that slide over the fixed background (LibraryGrid._layer). They must look exactly like normal drawing,
    also after a cover's hover began / settled (only its spot is patched); a flick the other way heads straight back;
    the cover under the pointer lifts only once scrolling has stopped."""

    def _window(self, tmp):
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        cloud = Path(tmp) / "cloud"
        cloud.mkdir()
        lib = Library(cloud)
        for i in range(40):
            lib.add_game(f"Game {i}", "PC", exe=str(Path(tmp) / f"g{i}" / "g.exe"))
        w = app.MainWindow(LocalSettings("PC", str(cloud)))
        w.show()
        w.refresh()
        return app, w

    @staticmethod
    def _worst(a, b) -> int:
        worst = 0
        for y in range(0, a.height(), 3):
            for x in range(0, a.width(), 3):
                p, q = a.pixel(x, y), b.pixel(x, y)
                if p != q:
                    worst = max(worst, max(abs(((p >> k) & 255) - ((q >> k) & 255)) for k in (0, 8, 16)))
        return worst

    def test_strips_look_like_normal_drawing(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            app, w = self._window(tmp)
            try:
                g, sb = w.grid, w.grid.verticalScrollBar()
                w._set_hovered = lambda gid: None              # (the real pointer may be anywhere)
                g.item(3).setSelected(True)
                app.hold_pulse(True)                           # (both pictures at the same moment of the breath)
                for v in (0, 137, sb.maximum()):
                    sb.setValue(v)
                    normal = g.viewport().grab().toImage()
                    g._glide_to = float(v)                     # a glide running (without its clock)
                    self.assertTrue(g.layer_active())
                    self.assertLessEqual(self._worst(normal, g.viewport().grab().toImage()), 3)
                    self.assertTrue(g._layer)
                    gid = g.item(g.visible_range()[2]).data(app.GAME_ID_ROLE)
                    w.hover[gid], w.hovered = 1.0, gid         # a cover lifts: its spot is patched out...
                    g.viewport().grab()
                    self.assertIn(gid, g._layer_skip)
                    w.hover.clear(); w.hovered = None          # ...and back in once it has settled
                    self.assertLessEqual(self._worst(normal, g.viewport().grab().toImage()), 3)
                    g._glide_to = None
                    g._drop_layer()
            finally:
                app.hold_pulse(False)
                w.close()
                app.wait_for_tasks(5)

    def test_drag_edge_scroll_glides(self):
        """2026-10-24 (owner: edge-scrolling with a cover in hand as smooth as the wheel): placed by the frame clock
        with eased speed (even steps, no jerks), drawn by the strips (the picked-up cover live, faded, looking as
        normal drawing does); easing out ends the glide, and so does dropping mid-scroll."""
        from unittest import mock
        from PySide6.QtCore import QPoint
        from PySide6.QtGui import QCursor
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            app, w = self._window(tmp)
            try:
                g, sb, vp = w.grid, w.grid.verticalScrollBar(), w.grid.viewport()
                sb.setValue(0)
                gid = g.item(1).data(app.GAME_ID_ROLE)
                w._start_game_drag(gid)
                w._drag_watch.stop(); w._drag_scroll.stop()    # (stepped by hand below)
                w.drag_dim = 1.0
                at = {"p": vp.mapToGlobal(QPoint(vp.width() // 4, vp.height() - 10)), "t": 100.0}

                class Cursor(QCursor):
                    pos = staticmethod(lambda: at["p"])
                with mock.patch.object(app, "QCursor", Cursor), mock.patch.object(app, "frame_time", lambda: at["t"]):
                    w._drag_scroll_t = at["t"]
                    values = []
                    for _ in range(40):
                        at["t"] += 1 / 60
                        w._drag_autoscroll()
                        values.append(sb.value())
                    self.assertTrue(g.layer_active())          # gliding with a cover in hand: the strips draw it
                    steps = [b - a for a, b in zip(values, values[1:])]
                    self.assertTrue(all(s >= 0 for s in steps) and values[-1] > 0)
                    self.assertTrue(all(abs(b - a) <= 2 for a, b in zip(steps, steps[1:])))   # eased, even
                    layered = vp.grab().toImage()
                    keep, g._glide_to = g._glide_to, None
                    normal = vp.grab().toImage()
                    g._glide_to = keep
                    self.assertLessEqual(self._worst(normal, layered), 3)
                    at["p"] = vp.mapToGlobal(QPoint(vp.width() // 4, vp.height() // 2))   # out of the zone
                    for _ in range(60):
                        at["t"] += 1 / 60
                        w._drag_autoscroll()
                    self.assertIsNone(g._glide_to)              # eased to a stop: normal drawing again
                    at["p"] = vp.mapToGlobal(QPoint(vp.width() // 4, vp.height() - 10))
                    for _ in range(5):
                        at["t"] += 1 / 60
                        w._drag_autoscroll()
                    self.assertIsNotNone(g._glide_to)
                    w._end_game_drag(at["p"], drop=False)       # let go mid-scroll
                    self.assertIsNone(g._glide_to)
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_drag_scroll_2_0_1(self):
        """2026-10-24 (2.0.1): held at the end past the edge and swung across the middle it stays a glide (the turns
        of a fast up-and-down drag stuttered); nothing else moves the library meanwhile (a Qt timer jumped it ~30 px
        as a game closed); the bars' changes wait until it stops; a game let go where it can't land fades back in
        at its place without keeping the strips off; a held cover while a session ends doesn't hand the mouse to Qt."""
        from unittest import mock
        from PySide6.QtCore import QPoint, QPointF, QEvent, Qt
        from PySide6.QtGui import QCursor, QMouseEvent
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            app, w = self._window(tmp)
            try:
                g, sb, vp = w.grid, w.grid.verticalScrollBar(), w.grid.viewport()
                sb.setValue(0)
                gid = g.item(1).data(app.GAME_ID_ROLE)
                w._start_game_drag(gid)
                w._drag_watch.stop(); w._drag_scroll.stop()
                at = {"p": vp.mapToGlobal(QPoint(vp.width() // 4, vp.height() + 150)), "t": 100.0}

                class Cursor(QCursor):
                    pos = staticmethod(lambda: at["p"])

                def run(n):
                    for _ in range(n):
                        at["t"] += 1 / 60
                        w._drag_autoscroll()
                with mock.patch.object(app, "QCursor", Cursor), mock.patch.object(app, "frame_time", lambda: at["t"]):
                    w._drag_scroll_t = at["t"]
                    run(10)
                    held = w._drag_scroll_pos
                    sb.setValue(sb.value() + 30)               # someone else moves it mid-scroll: put back at once
                    self.assertEqual(sb.value(), round(held))
                    self.assertIsNotNone(g._glide_to)
                    calls = []
                    with mock.patch.object(app.BarFader, "fade", lambda *a, **k: calls.append(a)):
                        w._update_details()                    # the bars wait
                    self.assertIn("details", w._bars_pending)
                    run(400)                                   # at the end, still pushed past it: still a glide
                    self.assertEqual(sb.value(), sb.maximum())
                    self.assertIsNotNone(g._glide_to)
                    at["p"] = vp.mapToGlobal(QPoint(vp.width() // 4, vp.height() // 2))
                    run(3)                                     # crossing the middle: a moment's grace...
                    self.assertIsNotNone(g._glide_to)
                    run(int(app.DRAG_REST_S * 60) + 5)         # ...then normal drawing, and the bars catch up
                    self.assertIsNone(g._glide_to)
                    self.assertEqual(w._bars_pending, {})
                    w._end_game_drag(vp.mapToGlobal(QPoint(vp.width() // 2, -60)), drop=True)   # over the top bar
                self.assertIn(gid, w.appearing)                # fades back in where it was...
                self.assertIn(gid, w._returning)
                self.assertAlmostEqual(w.appear_fade(gid), 0.75, delta=0.08)   # ...from how dimmed it was
                self.assertFalse(w._search_moving(returns=False))   # (the strips may still draw)
                self.assertTrue(w._search_moving())
                # a cover held while a session ends: no drag yet, and the move isn't Qt's (its drag-select scrolled)
                w._ghost = None
                w._press = (QPoint(10, 10), gid)
                w.worker = object()
                try:
                    ev = QMouseEvent(QEvent.MouseMove, QPointF(40, vp.height() - 2), QPointF(vp.mapToGlobal(QPoint(40, vp.height() - 2))),
                                     Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
                    self.assertTrue(w.eventFilter(vp, ev))
                    self.assertIsNone(w._ghost)
                finally:
                    w.worker = None
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_reversal_and_hover(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            app, w = self._window(tmp)
            try:
                g, sb = w.grid, w.grid.verticalScrollBar()
                sb.setValue(0)
                g._aim(400.0, app.WHEEL_GLIDE_S)               # heading down...
                g._glide_t0 -= 0.08                            # (part-way along)
                _, vel, _ = g._glide_state(time.perf_counter())
                self.assertGreater(vel, 0)
                g._aim(0.0, app.WHEEL_GLIDE_S)                 # ...a flick up: straight back, not down first
                self.assertEqual(g._glide_v0, 0.0)
                w.hovered = "x"
                w.scrolled()                                   # gliding: nothing lifts
                self.assertIsNone(w.hovered)
                g._end_glide()                                 # stopped: it lifts a moment later, not at once
                self.assertTrue(g._rehover_timer().isActive())
                self.assertTrue(g.scroll_settling())
            finally:
                w.close()
                app.wait_for_tasks(5)


class WheelPressScroll(unittest.TestCase):
    """2026-10-20 (owner: "make sure it works... as perfect as regular scroll"): wheel-press scrolling (_AutoScroll)
    and what its stress test found: Esc never stopped it (the window's Esc shortcut took the key), steering lifted
    covers, a press with nothing to scroll ate the next click, steps followed when they ran instead of the screen's
    frames, upward runs built strips behind instead of ahead, each arriving cover repainted whole rows, and a cover
    pushed out of memory mid-load counted as unreadable for good."""

    _window = GlideStrips._window

    def _run(self, body):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            app, w = self._window(tmp)
            try:
                body(app, w, tmp)
            finally:
                if w.grid.auto_scrolling():
                    w.grid._auto.stop()
                w.close()
                app.wait_for_tasks(5)

    @staticmethod
    def _press(app, w):
        from PySide6.QtCore import QEvent, QPointF
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication
        vp = w.grid.viewport()
        pt = QPointF(vp.width() / 2, vp.height() / 2)
        QApplication.sendEvent(vp, QMouseEvent(QEvent.MouseButtonPress, pt, QPointF(vp.mapToGlobal(pt.toPoint())),
                                               app.Qt.MiddleButton, app.Qt.MiddleButton, app.Qt.NoModifier))

    def test_keys_stop_it_and_esc_does_nothing_else(self):
        def body(app, w, tmp):
            from PySide6.QtCore import QEvent
            from PySide6.QtGui import QKeyEvent
            from PySide6.QtWidgets import QApplication
            for key in (app.Qt.Key_Escape, app.Qt.Key_Right):
                self._press(app, w)
                self.assertTrue(w.grid.auto_scrolling())
                ev = QKeyEvent(QEvent.ShortcutOverride, key, app.Qt.NoModifier)
                ev.ignore()
                QApplication.sendEvent(w.grid, ev)             # (what a real key sends first)
                self.assertFalse(w.grid.auto_scrolling())
                self.assertEqual(ev.isAccepted(), key == app.Qt.Key_Escape)   # Esc: no folder closing / Back
                self.assertIsNone(QApplication.overrideCursor())
        self._run(body)

    def test_nothing_to_scroll_nothing_starts(self):
        def body(app, w, tmp):
            from PySide6.QtWidgets import QApplication
            w.search.setText("Game 7")
            w._apply_filter()
            self.assertEqual(w.grid.verticalScrollBar().maximum(), 0)
            self._press(app, w)
            self.assertFalse(w.grid.auto_scrolling())
            self.assertIsNone(QApplication.overrideCursor())
        self._run(body)

    def test_no_cover_lifts_while_steering(self):
        def body(app, w, tmp):
            from PySide6.QtCore import QEvent, QPointF
            from PySide6.QtGui import QMouseEvent
            from PySide6.QtWidgets import QApplication
            g = w.grid
            w.hovered = g.item(0).data(app.GAME_ID_ROLE)
            self._press(app, w)
            self.assertIsNone(w.hovered)                       # (a lifted cover settles on the press)
            pt = QPointF(g.visualRect(g.model().index(1, 0)).center())
            QApplication.sendEvent(g.viewport(), QMouseEvent(QEvent.MouseMove, pt, QPointF(g.viewport().mapToGlobal(pt.toPoint())),
                                                             app.Qt.NoButton, app.Qt.MiddleButton, app.Qt.NoModifier))
            self.assertIsNone(w.hovered)
        self._run(body)

    def test_frame_time_follows_the_screen(self):
        import app
        t = time.perf_counter()
        f = app.frame_time()
        self.assertGreaterEqual(f, t - 0.001)
        self.assertLess(f, t + 0.1)                            # (the next refresh, or now where Windows can't say)

    def test_upward_run_builds_the_strip_above(self):
        def body(app, w, tmp):
            g, sb = w.grid, w.grid.verticalScrollBar()
            self.assertGreater(sb.maximum(), 2 * app.LAYER_TILE)
            sb.setValue(sb.maximum())
            g._drop_layer()
            v = sb.value()
            g._glide_to, g._heading = v + 0.4, -1              # (going up, the exact spot just past the shown one)
            g.viewport().grab()
            self.assertIn(v // app.LAYER_TILE - 1, g._layer)
            g._glide_to, g._heading = None, 0
        self._run(body)

    def test_patching_a_cover_paints_only_it(self):
        def body(app, w, tmp):
            g = w.grid
            g._glide_to = 0.0
            g.viewport().grab()                                # (strips made)
            painted = []
            real = app.CardDelegate.paint
            app.CardDelegate.paint = lambda d, p, o, i: (painted.append(i.data(app.GAME_ID_ROLE)), real(d, p, o, i))
            try:
                gid = g.item(g.visible_range()[3]).data(app.GAME_ID_ROLE)
                g._patch_layer({gid})
            finally:
                app.CardDelegate.paint = real
            self.assertTrue(painted)
            self.assertEqual(set(painted), {gid})
            g._layer_arrived = {gid}
            g._end_glide()                                     # (patches still queued are done on handing back)
            self.assertFalse(g._layer_arrived)
        self._run(body)

    def test_pushed_out_cover_is_loaded_again(self):
        def body(app, w, tmp):
            from PySide6.QtGui import QImage
            from PySide6.QtWidgets import QApplication
            img = QImage(60, 90, QImage.Format_RGB32)
            img.fill(0xFF336699)
            art = Path(tmp) / "c.png"
            img.save(str(art))
            gid = w.grid.item(0).data(app.GAME_ID_ROLE)
            w.library.set_artwork(gid, str(art))
            w.refresh()
            app.wait_for_tasks(5)
            game = w.games[gid]
            w.covers.get(w.art(game), (game.crop_zoom, game.crop_x, game.crop_y))
            self.assertTrue(w.cover_ready(gid))
            w._cover_wanted.add(gid)
            w._load_wanted_covers()                            # asked for while in memory (so not decoded)...
            w.covers._cache.clear()                            # ...and pushed out before the answer came
            w.covers._ready.clear()
            end = time.monotonic() + 5
            while time.monotonic() < end and not (w.cover_ready(gid) and not w._cover_loading):
                QApplication.processEvents()
                time.sleep(0.01)
            self.assertNotIn(gid, w._cover_given_up)
            self.assertTrue(w.cover_ready(gid))
        self._run(body)


class HoverFrames(unittest.TestCase):
    """2026-10-19 (PC, 143 Hz): a hover frame redrew every cover (~4.5 ms); now the still picture + the lights, and
    only the moving part drawn the usual way (~1.3 ms). It must look like a full redraw."""

    def test_hover_frame_looks_like_a_full_redraw(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            from PySide6.QtGui import QPixmap, QRegion
            from PySide6.QtCore import QPoint, QRect
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            for i in range(12):
                lib.add_game(f"Game {i}", "PC", exe=str(Path(tmp) / f"g{i}" / "g.exe"))
            w = app.MainWindow(LocalSettings("PC", str(cloud)))
            real = time.monotonic
            try:
                w.show()
                w.refresh()
                end = real() + 1.0
                while real() < end:
                    QApplication.processEvents()
                    time.sleep(0.005)
                grid, vp = w.grid, w.grid.viewport()
                items = [grid.item(i) for i in grid.visible_range()]
                target = items[min(6, len(items) - 1)].data(app.GAME_ID_ROLE)
                w._update_anim = lambda: None
                frozen = real()
                with mock.patch.object(app.time, "monotonic", lambda: frozen):
                    w.hover, w.hovered = {target: 0.2}, target
                    grid._still, grid._still_dirty = None, None
                    grid.prepare_still()
                    w.hover = {target: 0.6}
                    region, live, lights = QRegion(), QRegion(), []
                    for it in items:                       # (as MainWindow._tick builds a hover frame)
                        r = grid.visualItemRect(it)
                        iid = it.data(app.GAME_ID_ROLE)
                        if iid == target:
                            region += r.adjusted(-30, -30, 30, 36)
                            live += r.adjusted(-30, -30, 30, 36)
                        else:
                            c = app.CardDelegate.light_center(app.CardDelegate.cover_rect(r, 0.0))
                            rad = int(app.LIGHT_R * 2.9) + 2
                            sq = QRect(int(c.x()) - rad, int(c.y()) - rad, 2 * rad + 1, 2 * rad + 1)
                            region += sq
                            lights.append((c, iid, sq))
                    lights = [(c, iid) for c, iid, sq in lights if not live.intersects(sq)]
                    grid._still_dirty = QRegion(live)
                    self.assertTrue(grid.prepare_still(skip=live))
                    grid.light_frame = {"region": region, "lights": lights, "live": live}
                    mixed = QPixmap(vp.size())
                    mixed.fill()
                    vp.render(mixed, QPoint(0, 0), region)
                    self.assertIsNone(grid.light_frame)     # (the hover frame was drawn)
                    full = QPixmap(vp.size())
                    full.fill()
                    vp.render(full, QPoint(0, 0), region)
                a, b = mixed.toImage(), full.toImage()
                off = [max(abs(((pa >> k) & 255) - ((pb >> k) & 255)) for k in (0, 8, 16))
                       for rr in region for y in range(rr.top(), rr.bottom() + 1) for x in range(rr.left(), rr.right() + 1)
                       if (pa := a.pixel(x, y)) != (pb := b.pixel(x, y))]
                self.assertLessEqual(len(off), 60)                 # (a few anti-aliased edge pixels at most)
                self.assertLessEqual(max(off, default=0), 40)
            finally:
                w.close()
                app.wait_for_tasks(5)


class DragFolderInPlace(unittest.TestCase):
    """2026-10-19 (owner: "it'll snap back"): a game dropped onto another made a folder that My Arrangement put at the
    END of the library, and the view jumped there. Now it takes the target's place; a folder that does land out of
    view (an automatic sort) is glided to, not jumped to."""

    def test_folder_takes_the_targets_place(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtWidgets import QApplication
            import app
            QApplication.instance() or QApplication([])
            cloud = Path(tmp) / "cloud"
            cloud.mkdir()
            lib = Library(cloud)
            ids = [lib.add_game(f"Game {i:02d}", "PC", exe=str(Path(tmp) / f"g{i}" / "g.exe")).game_id for i in range(40)]
            lib.set_library_order(ids)
            s = LocalSettings("PC", str(cloud))
            s.sort = "custom"
            w = app.MainWindow(s)
            try:
                w.show()
                w.refresh()
                sb = w.grid.verticalScrollBar()
                sb.setValue(sb.maximum() // 2)
                before = sb.value()
                target, dragged = ids[20], ids[3]
                w._drop_game(dragged, target, False)
                order = lib.view_orders("PC")["library"]["here"]      # (This Device: this PC's own arrangement)
                folder = next(x for x in order if x.startswith(app.FOLDER_PREFIX))
                self.assertEqual(order.index(folder), 20 - 1)          # (the dragged game left a place before it)
                self.assertNotIn(dragged, order)
                self.assertNotIn(target, order)
                self.assertEqual(sb.value(), before)                    # the view stays put
                self.assertEqual(w.selected_folder(), folder[len(app.FOLDER_PREFIX):])
                # an item far out of view: glided to, not jumped to
                w.grid.setCurrentRow(0)
                sb.setValue(0)
                far = w.grid.count() - 1
                w._select_item(w.grid.item(far).data(app.GAME_ID_ROLE), glide=True)
                self.assertLess(sb.value(), 50)                         # not there at once
                end = time.monotonic() + 2
                while time.monotonic() < end and w.grid._glide_to is not None:
                    QApplication.processEvents()
                    time.sleep(0.005)
                self.assertGreater(sb.value(), 200)                     # ...but it got there
            finally:
                w.close()
                app.wait_for_tasks(5)


class SmoothWheelEverywhere(unittest.TestCase):
    """2026-10-20 (owner: "everything super smooth"): every scrolling area outside the library glides on the wheel
    like it does; touchpads keep Qt's own; at the end the wheel goes on to the area outside."""

    def test_scroll_area_and_list_glide(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtWidgets import QApplication, QListWidget, QScrollArea, QWidget
        import app
        qa = QApplication.instance() or QApplication([])
        app.install_smooth_wheel(qa)
        area = QScrollArea()
        inner = QWidget()
        inner.setFixedSize(200, 3000)
        area.setWidget(inner)
        area.resize(220, 300)
        lst = QListWidget()
        lst.addItems([f"row {i}" for i in range(200)])
        lst.resize(200, 300)
        area.show()
        lst.show()
        try:
            def wheel(w, dy, pixels=0):
                vp = w.viewport()
                c = QPointF(10, 10)
                QApplication.sendEvent(vp, QWheelEvent(c, c, QPoint(0, pixels), QPoint(0, dy), Qt.NoButton,
                                                       Qt.NoModifier, Qt.NoScrollPhase, False))

            def settle(sec=1.0):
                end = time.monotonic() + sec
                while time.monotonic() < end and app._SMOOTH_WHEEL.glides:
                    QApplication.processEvents()
                    time.sleep(0.005)
            for w in (area, lst):
                sb = w.verticalScrollBar()
                wheel(w, -120)
                self.assertEqual(sb.value(), 0)                       # not there at once: it glides
                self.assertIn(sb, app._SMOOTH_WHEEL.glides)
                settle()
                self.assertGreater(sb.value(), 50)
                at = sb.value()
                wheel(w, -40, pixels=-40)                             # a touchpad: Qt's own, no glide
                self.assertNotIn(sb, app._SMOOTH_WHEEL.glides)
                self.assertNotEqual(sb.value(), at)
                sb.setValue(0)
                wheel(w, 120)                                         # at the top: nothing to glide
                self.assertNotIn(sb, app._SMOOTH_WHEEL.glides)
                sb.setValue(sb.maximum() - 30)
                wheel(w, -120)                                        # gliding to the end...
                wheel(w, -120)                                        # ...a quick extra notch doesn't cut it off
                self.assertIn(sb, app._SMOOTH_WHEEL.glides)
                settle()
                self.assertEqual(sb.value(), sb.maximum())
            from PySide6.QtWidgets import QComboBox
            combo = QComboBox(inner)                                  # a drop-down inside: the wheel scrolls the area
            combo.addItems(["a", "b", "c"])
            combo.show()
            sb = area.verticalScrollBar()
            sb.setValue(0)
            QApplication.sendEvent(combo, QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
                                                      Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))
            self.assertEqual(combo.currentIndex(), 0)
            self.assertIn(sb, app._SMOOTH_WHEEL.glides)
        finally:
            area.close()
            lst.close()


class WheelPressEverywhere(unittest.TestCase):
    """2026-10-20 (owner): wheel-press scrolling in every scrolling area, not just the library - pressed over anything
    inside a window's scroll area it starts, moving away from the press scrolls, letting go (after moving) stops it."""

    def test_settings_like_window(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtWidgets import QApplication, QDialog, QLabel, QScrollArea, QVBoxLayout, QWidget
        import app
        qa = QApplication.instance() or QApplication([])
        app.install_smooth_wheel(qa)
        d = QDialog()
        lay = QVBoxLayout(d)
        area = QScrollArea()
        inner = QWidget()
        inner.setFixedSize(300, 3000)
        label = QLabel("a hint text", inner)
        label.move(10, 100)
        area.setWidget(inner)
        lay.addWidget(area)
        d.resize(340, 400)
        d.show()
        try:
            def mouse(kind):
                pt = QPointF(5, 5)
                QApplication.sendEvent(label, QMouseEvent(kind, pt, QPointF(label.mapToGlobal(pt.toPoint())),
                                                          Qt.MiddleButton, Qt.MiddleButton if kind ==
                                                          QEvent.MouseButtonPress else Qt.NoButton, Qt.NoModifier))
            mouse(QEvent.MouseButtonPress)
            auto = area.findChild(app._AreaAutoScroll)
            self.assertIsNotNone(auto)
            self.assertTrue(auto.active)
            self.assertTrue(area.viewport().autoFillBackground())        # solid while it runs (quick sliding)
            auto.anchor -= 150                                           # the pointer well below the press
            end = time.monotonic() + 0.4
            while time.monotonic() < end:
                QApplication.processEvents()
                time.sleep(0.005)
            self.assertGreater(area.verticalScrollBar().value(), 20)
            mouse(QEvent.MouseButtonRelease)                             # held and moved: letting go stops it
            self.assertFalse(auto.active)
            self.assertFalse(area.viewport().autoFillBackground())
            self.assertIsNone(QApplication.overrideCursor())
        finally:
            d.close()


class TitleBarPress(unittest.TestCase):
    def test_press_focuses_an_unfocused_window(self):
        """2026-10-24 (2.0.1): the title bar's press is kept from Windows (the refocus hitch), and Windows' handling of it
        was what focused the window - a click on the title bar of the unfocused window left it unfocused."""
        if sys.platform != "win32":
            self.skipTest("Windows only")
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        import app
        from PySide6.QtWidgets import QApplication, QWidget
        QApplication.instance() or QApplication([])

        class Win(app.KeepsTitleBar, QWidget):
            pass
        w = Win()
        try:
            for active, focused in ((False, 1), (True, 0)):
                with mock.patch.object(w, "isActiveWindow", lambda a=active: a), \
                        mock.patch.object(w, "activateWindow") as act, mock.patch.object(w, "raise_"):
                    self.assertTrue(w._caption_pressed())
                    self.assertEqual(act.call_count, focused)
                w._press_watch.stop()
        finally:
            w.close()


class FolderAddRenameAndRemovalFades(unittest.TestCase):
    """2026-10-25 (2.0.2): a game added while a folder is open goes into it; clicking the folder's name at the top
    renames it in place (like the bottom bar's name); a game dropped on the trash stays faded while Are You Sure?
    asks (Cancel fades it back); a removed game fades out first, then the others slide into the gap."""

    def _window(self, tmp, games=3):
        from PySide6.QtWidgets import QApplication
        import app
        QApplication.instance() or QApplication([])
        cloud = Path(tmp) / "cloud"
        cloud.mkdir()
        lib = Library(cloud)
        ids = [lib.add_game(f"Game {i}", "PC", exe=rf"C:\g{i}\g{i}.exe").game_id for i in range(games)]
        w = app.MainWindow(LocalSettings("PC", str(cloud)))
        w.resize(1200, 800)
        w.show()
        w.refresh()
        QApplication.processEvents()
        return app, w, lib, cloud, ids

    def test_added_inside_a_folder_goes_into_it(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            app, w, lib, cloud, ids = self._window(tmp, 2)
            try:
                fid = lib.create_folder("Box")
                for gid in ids:
                    lib.set_folder(gid, fid)
                w.refresh()
                w.open_folder(fid)

                class Dialog:                                   # (stands in for the game settings window: Save)
                    def __init__(self, library, settings, game=None, exe="", parent=None, args=""):
                        self.library, self.exe, self.saved_id = library, exe, None

                    def exec(self):
                        self.saved_id = self.library.add_game(Path(self.exe).stem, "PC", exe=self.exe).game_id
                        return True

                exe = Path(tmp) / "Novel.exe"
                exe.write_bytes(b"MZ")
                with mock.patch.object(app, "GameDialog", Dialog):
                    w._add_files([str(exe)])
                    new = Library(cloud).find_by_exe(str(exe))
                    self.assertEqual(new.folder, fid)
                    self.assertEqual(w.current_folder, fid)     # (still inside, the new game selected)
                    w.close_folder()
                    other = Path(tmp) / "Other.exe"
                    other.write_bytes(b"MZ")
                    w._add_files([str(other)])                   # from the library itself: no folder
                    self.assertEqual(Library(cloud).find_by_exe(str(other)).folder, "")
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_folder_name_at_the_top_renames(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtCore import QPoint, Qt
            from PySide6.QtTest import QTest
            from PySide6.QtWidgets import QApplication
            app, w, lib, cloud, ids = self._window(tmp, 1)
            try:
                fid = lib.create_folder("Box")
                lib.set_folder(ids[0], fid)
                w.refresh()
                w.open_folder(fid)
                QApplication.processEvents()
                e, lab = w.f_title_edit, w.folder_title

                def rename(text, key):
                    QTest.mouseClick(lab, Qt.LeftButton, pos=QPoint(10, lab.height() // 2))
                    QApplication.processEvents()
                    self.assertTrue(e.isVisible())
                    self.assertTrue(lab.blank)
                    e.selectAll()
                    QTest.keyClicks(e, text)
                    with mock.patch.object(w, "close_folder") as closed:
                        QTest.keyClick(e, key)                  # (Esc / Enter: the box's, not the window's)
                        QApplication.processEvents()
                        self.assertFalse(closed.called)
                    self.assertFalse(e.isVisible())
                    self.assertFalse(lab.blank)

                rename("Shelf", Qt.Key_Escape)
                self.assertEqual(Library(cloud).list_folders()[fid], "Box")
                rename("Shelf", Qt.Key_Return)
                self.assertEqual(Library(cloud).list_folders()[fid], "Shelf")
                self.assertEqual(lab.text(), "Shelf")
                self.assertEqual(w.current_folder, fid)
            finally:
                w.close()
                app.wait_for_tasks(5)

    def test_trash_drop_stays_faded_then_removal_fades_out(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, mock.patch.dict(os.environ, {"APPDATA": tmp}):
            from PySide6.QtCore import QPoint
            from PySide6.QtWidgets import QApplication
            app, w, lib, cloud, ids = self._window(tmp, 3)
            try:
                first = w.grid.item(0).data(app.GAME_ID_ROLE)
                seen = []

                def asking(*_a, **_k):                          # while Are You Sure? is open
                    seen.append((w._asking_gid, w._dim_gid))
                    return None

                w._dim_gid, w.drag_dim, w._asking_gid = first, 1.0, first   # (as after the drop on the trash)
                w._landed()
                self.assertEqual((w._dim_gid, w.drag_dim), (first, 1.0))      # landing keeps it faded
                with mock.patch.object(app, "confirm_remove", side_effect=asking):
                    w.remove_game(first, dropped=True)          # Cancel
                self.assertEqual(seen, [(first, first)])
                self.assertIsNone(w._asking_gid)
                self.assertIn(first, w.appearing)               # fades back in at its place
                self.assertIn(first, w._returning)
                self.assertIn(first, w.games)

                end = time.monotonic() + 0.5
                while time.monotonic() < end:
                    QApplication.processEvents()
                    time.sleep(0.01)
                with mock.patch.object(app, "confirm_remove", return_value="both"):
                    before = time.monotonic()
                    w.remove_game(first)                        # Confirm
                self.assertNotIn(first, w.games)
                self.assertTrue(w._leaving)                     # its cover fades out where it was...
                self.assertTrue(w.slides)
                self.assertGreater(w._slide_t0, before)         # ...before the others start closing the gap
                moving = next(iter(w.slides))
                self.assertEqual(w.slide_offset(moving), QPoint(*w.slides[moving]))   # (held in place meanwhile)
            finally:
                w.close()
                app.wait_for_tasks(5)


class PopUpsAreDeleted(unittest.TestCase):
    """2026-10-25 (laptop): pop-ups stayed as hidden children after closing - every one restyled on each theme change
    (+27 ms each), and the "already running" pop-up's process check went on every 0.4 s for the rest of the session."""

    def test_message_and_its_check_are_gone(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QDialog, QWidget
        from PySide6.QtCore import QTimer
        import app
        QApplication.instance() or QApplication([])
        parent = QWidget()
        calls = []

        def close_it():
            for d in parent.findChildren(QDialog):
                if d.isVisible():
                    d.reject()
        try:
            for _ in range(3):
                QTimer.singleShot(50, close_it)
                app.message(parent, "test", buttons=(("ok", "OK"),), done_when=lambda: calls.append(1) or False)
            self.assertEqual(parent.findChildren(QDialog), [])
            seen = len(calls)
            end = time.monotonic() + 1.0
            while time.monotonic() < end:
                QApplication.processEvents()
                time.sleep(0.01)
            self.assertEqual(len(calls), seen)          # no check left running after the pop-ups closed
        finally:
            parent.deleteLater()


class PathBoxesShowTheStart(unittest.TestCase):
    """2026-10-25 (PC, owner): path boxes show the START of a long path (Qt showed the end), ending in "..." where
    cut off, with the full path on hover (our own popup - Qt's flashed beside it); a popup too wide to move inside its
    window may use the screen's width (it sat pinned to the window's right edge)."""

    LONG = r"C:\Users\Jaden\Desktop\LunaHook\LunaTranslator\LunaTranslator.exe"

    def _run(self, seconds):
        from PySide6.QtWidgets import QApplication
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.004)

    def test_start_dots_and_popup_text(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
        import app
        QApplication.instance() or QApplication([])
        win = QWidget()
        lay = QVBoxLayout(win)
        long_box, short_box, own_tip = app.PathBox(self.LONG), app.PathBox(r"C:\a.exe"), app.PathBox(self.LONG)
        own_tip.setToolTip("mine")
        for b in (long_box, short_box, own_tip):
            b.setFixedWidth(200)
            lay.addWidget(b)
        win.show()
        self._run(0.05)
        try:
            self.assertEqual(long_box.cursorPosition(), 0)                 # the start shows
            long_box.setText(self.LONG + "x")                               # Browse sets it: back to the start
            self.assertEqual(long_box.cursorPosition(), 0)
            self.assertTrue(long_box._cut())
            self.assertFalse(short_box._cut())
            self.assertEqual(long_box.tip_at(QPoint(5, 5))[0], self.LONG + "x")
            self.assertEqual(short_box.tip_at(QPoint(5, 5))[0], "")        # fits: no popup
            self.assertEqual(own_tip.tip_at(QPoint(5, 5))[0], "mine")      # (Other Device's own tooltip wins)
            img = long_box.grab().toImage()                                 # drawn without error, "..." at the end
            self.assertFalse(img.isNull())
            long_box.setFocus()
            long_box.end(False)                                             # typing: a plain line edit
            long_box.clearFocus()
            long_box.editingFinished.emit()
            self.assertEqual(long_box.cursorPosition(), 0)
        finally:
            win.close()
            win.deleteLater()

    def test_wide_popup_may_leave_the_window(self):
        from unittest import mock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtGui import QHelpEvent
        from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
        import app
        qa = QApplication.instance() or QApplication([])
        app.install_smooth_tips(qa)
        win = QWidget()
        lay = QVBoxLayout(win)
        box = app.PathBox(r"C:\Users\Jaden\Desktop\Luna.exe")      # (short: room on the test's 800 px screen)
        box.setFixedWidth(100)
        lay.addWidget(box)
        win.resize(300, 120)
        win.show()
        win.move(200, 200)
        self._run(0.05)
        tip = app.SmoothTip.instance()
        try:
            mouse = {"pos": box.mapToGlobal(QPoint(30, box.height() // 2))}
            with mock.patch.object(app.QCursor, "pos", side_effect=lambda: QPoint(mouse["pos"])):
                here = box.mapFromGlobal(mouse["pos"])
                QApplication.sendEvent(box, QHelpEvent(QEvent.ToolTip, here, mouse["pos"]))
                self.assertTrue(tip.isVisible())
                self.assertGreater(tip.width(), win.width() // 2)
                self._run(0.3)
                start = tip.pos()
                mouse["pos"] = mouse["pos"] + QPoint(20, 0)               # follows sideways too
                self._run(0.4)
                self.assertEqual(tip.pos().x() - start.x(), 20)
                win_right = win.mapToGlobal(QPoint(win.width(), 0)).x()
                self.assertGreater(tip.pos().x() + tip.width(), win_right)  # (past the window's edge)
                mouse["pos"] = mouse["pos"] + QPoint(0, 300)
                self._run(0.3)
        finally:
            win.close()
            win.deleteLater()
