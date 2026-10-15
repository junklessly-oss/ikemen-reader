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

IS_WINDOWS = sys.platform == "win32"
GAME = "Fake VN"


def write(folder: Path, name: str, text: str) -> None:
    p = folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def read_all(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): p.read_text("utf-8") for p in folder.rglob("*") if p.is_file()}


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
