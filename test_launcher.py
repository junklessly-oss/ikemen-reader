"""Run with:  py -m unittest -v   (uses fake games)"""
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from launcher import play, run_game
from library import Library, LocalSettings, MachinePaths, load_local_settings
from sync_engine import SyncEngine

IS_WINDOWS = sys.platform == "win32"


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.lib = Library(self.root / "cloud")

    def tearDown(self):
        self._tmp.cleanup()

    def test_each_machine_keeps_its_own_paths_and_both_are_visible(self):
        g = self.lib.add_game("Fake VN", "PC", exe=r"D:\VN\game.exe", save_dir=r"D:\VN\save")
        self.lib.set_paths(g.game_id, "Laptop", exe=r"C:\Games\game.exe", save_dir=r"C:\Users\Me\AppData\save")
        g = self.lib.find("fake vn")
        self.assertEqual(g.paths["PC"].save_dir, r"D:\VN\save")
        self.assertEqual(g.paths["Laptop"].save_dir, r"C:\Users\Me\AppData\save")

    def test_paths_for_unknown_machine_is_empty_not_an_error(self):
        g = self.lib.add_game("Fake VN", "PC")
        self.assertEqual(g.paths_for("Laptop").exe, "")

    def test_playtime_adds_up_across_machines(self):
        g = self.lib.add_game("Fake VN", "PC")
        self.lib.add_playtime(g.game_id, "PC", 3600)
        self.lib.add_playtime(g.game_id, "Laptop", 1800)
        self.lib.add_playtime(g.game_id, "PC", 60)
        self.assertEqual(self.lib.get(g.game_id).total_playtime, 5460)

    def test_artwork_is_copied_into_the_cloud_folder(self):
        art = self.root / "cover.JPG"
        art.write_bytes(b"fake image")
        g = self.lib.add_game("Fake VN", "PC", artwork_src=str(art))
        self.assertTrue(self.lib.artwork_path(g).is_file())

    def test_duplicate_title_rejected(self):
        self.lib.add_game("Fake VN", "PC")
        with self.assertRaises(ValueError):
            self.lib.add_game("Fake VN", "Laptop")

    def test_corrupt_game_file_is_skipped(self):
        self.lib.add_game("Good", "PC")
        (self.lib.games_dir / "broken.json").write_text("{not json")
        self.assertEqual([g.title for g in self.lib.list_games()], ["Good"])


class FakeGameFolder:
    """A game folder with a real program inside it, so the launcher's "is it running from the
    game folder?" check works on every OS. On Windows the program is a venv's python.exe (a small
    launcher that runs the real Python as a child, like many VN launchers do); elsewhere /bin/sh."""

    def __init__(self, game_dir: Path):
        self.dir = game_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        if IS_WINDOWS:
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(self.dir / "rt")], check=True)
            self.runtime = str(self.dir / "rt" / "Scripts" / "python.exe")
        else:
            self.runtime = str(self.dir / "sh")
            shutil.copy("/bin/sh", self.runtime)

    def game(self, name: str, python_code: str, shell_code: str) -> tuple[str, str]:
        """Write a fake game. Returns (exe, wrapper) to pass to run_game/play."""
        p = self.dir / name
        p.write_text(python_code if IS_WINDOWS else shell_code, "utf-8")
        return str(p), self.runtime


class RunGameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.folder = FakeGameFolder(Path(cls._tmp.name) / "game")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_simple_game_returns_when_it_exits(self):
        exe, runtime = self.folder.game("game1", "import time; time.sleep(1)", "sleep 1")
        t = run_game(exe, runtime, poll=0.1, grace=0.3)
        self.assertGreaterEqual(t, 0.9)
        self.assertLess(t, 4)

    @unittest.skipUnless(IS_WINDOWS, "Windows' administrator prompt")
    def test_game_needing_admin_goes_through_the_permission_prompt(self):
        import launcher
        exe, runtime = self.folder.game("admingame", "import time; time.sleep(2)", "sleep 2")
        real_popen, real_elevated = launcher.subprocess.Popen, launcher._start_elevated
        asked = []

        def needs_admin(cmd, **kw):            # what Windows does for an "always run as administrator" game
            e = OSError("The requested operation requires elevation")
            e.winerror = 740
            raise e

        def owner_says_yes(cmd, cwd):           # stands in for the prompt: start it and return its id
            asked.append(cmd)
            return real_popen(cmd, cwd=cwd).pid
        launcher.subprocess.Popen, launcher._start_elevated = needs_admin, owner_says_yes
        try:
            info = launcher.run([runtime, exe], Path(exe).parent, poll=0.1, grace=0.3)
        finally:
            launcher.subprocess.Popen, launcher._start_elevated = real_popen, real_elevated
        self.assertEqual(len(asked), 1)
        self.assertTrue(info.started)
        self.assertGreaterEqual(info.seconds, 1.8)        # waited for the game, not just the prompt
        self.assertLess(info.seconds, 6)

    @unittest.skipUnless(IS_WINDOWS, "Windows' administrator prompt")
    def test_saying_no_to_the_prompt_starts_nothing(self):
        import launcher
        exe, runtime = self.folder.game("admingame2", "import time; time.sleep(2)", "sleep 2")
        real_popen, real_elevated = launcher.subprocess.Popen, launcher._start_elevated

        def needs_admin(cmd, **kw):
            e = OSError("elevation")
            e.winerror = 740
            raise e

        def owner_says_no(cmd, cwd):
            raise launcher.ElevationDeclined(1223, "declined")
        launcher.subprocess.Popen, launcher._start_elevated = needs_admin, owner_says_no
        try:
            with self.assertRaises(launcher.ElevationDeclined):
                launcher.run([runtime, exe], Path(exe).parent, poll=0.1, grace=0.3)
        finally:
            launcher.subprocess.Popen, launcher._start_elevated = real_popen, real_elevated

    def test_waits_for_a_game_spawned_by_a_launcher_that_exits_immediately(self):
        self.folder.game("realgame", "import time; time.sleep(3)", "sleep 3")
        exe, runtime = self.folder.game(
            "launcher",
            "import subprocess, sys\n"
            "subprocess.Popen([sys.executable, 'realgame'], creationflags=subprocess.DETACHED_PROCESS)\n",
            f'"{self.folder.runtime}" ./realgame &\nexit 0\n')
        t = run_game(exe, runtime, poll=0.1, grace=0.3)
        self.assertGreaterEqual(t, 2.5)


class PlayFlowTests(unittest.TestCase):
    def test_full_cycle_syncs_down_runs_syncs_up_and_records_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cloud = root / "cloud"
            # PC: has an existing save, plays and changes it
            pc_saves = root / "pc_saves"
            pc_saves.mkdir()
            (pc_saves / "slot1.sav").write_text("old")
            save_file = pc_saves / "slot1.sav"
            folder = FakeGameFolder(root / "game")
            exe, runtime = folder.game(
                "game",
                f"import time, pathlib; pathlib.Path(r'{save_file}').write_text('new progress'); time.sleep(1.5)\n",
                f'echo "new progress" > "{save_file}"\nsleep 1.5\n')

            lib = Library(cloud)
            # the fake game needs a runtime to start it, which is exactly what the LE option provides
            g = lib.add_game("Fake VN", "PC", exe=exe, save_dir=str(pc_saves), use_le=True)
            pc = LocalSettings("PC", str(cloud), le_path=runtime)
            engine = SyncEngine(cloud, root / "pc_state")

            msgs = []
            r = play(g, pc, lib, engine, status=msgs.append, poll=0.1, grace=0.3)
            self.assertTrue(r.launched, r.message)
            self.assertEqual((engine.cloud_dir(g.game_id) / "slot1.sav").read_text().strip(), "new progress")
            self.assertGreaterEqual(lib.get(g.game_id).playtime["PC"], 1)

            # Laptop opens the same game later and receives the PC's save on launch
            laptop_saves = root / "laptop_saves"
            lib.set_paths(g.game_id, "Laptop", exe=exe, save_dir=str(laptop_saves))
            laptop = LocalSettings("Laptop", str(cloud), le_path=runtime)
            engine2 = SyncEngine(cloud, root / "laptop_state")
            r2 = play(lib.get(g.game_id), laptop, lib, engine2, status=msgs.append, poll=0.1, grace=0.3)
            self.assertTrue(r2.launched, r2.message)
            self.assertEqual((laptop_saves / "slot1.sav").read_text().strip(), "new progress")

    def test_refuses_to_launch_if_exe_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = Library(Path(tmp) / "cloud")
            g = lib.add_game("Fake VN", "PC", exe="/nope/game.exe", save_dir=tmp)
            r = play(g, LocalSettings("PC", tmp), lib, SyncEngine(Path(tmp) / "cloud", Path(tmp) / "st"),
                     status=lambda m: None)
            self.assertFalse(r.launched)

    def test_play_with_le_refuses_if_le_not_set_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "game.exe"
            exe.write_bytes(b"")
            lib = Library(Path(tmp) / "cloud")
            g = lib.add_game("Fake VN", "PC", exe=str(exe), save_dir=tmp, use_le=True)
            r = play(g, LocalSettings("PC", tmp), lib, SyncEngine(Path(tmp) / "cloud", Path(tmp) / "st"),
                     status=lambda m: None)
            self.assertFalse(r.launched)
            self.assertEqual(r.message, "play.le_missing")
            self.assertEqual(r.retry_without, "le")


class EditAndCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.lib = Library(self.root / "cloud")

    def tearDown(self):
        self._tmp.cleanup()

    def test_game_files_from_older_versions_still_load(self):
        self.lib.games_dir.mkdir(parents=True)
        (self.lib.games_dir / "Old.json").write_text(
            '{"game_id": "Old", "title": "Old", "paths": {"PC": {"exe": "a.exe", "save_dir": "s", "wrapper": ""}}}',
            "utf-8")
        self.assertEqual(self.lib.get("Old").paths["PC"].exe, "a.exe")

    def test_settings_from_older_versions_still_load(self):
        (self.root / "settings.json").write_text('{"machine_name": "PC", "cloud_root": "x"}', "utf-8")
        s = load_local_settings(self.root)
        self.assertEqual((s.machine_name, s.le_path), ("PC", ""))

    def test_rename_keeps_id_playtime_and_other_machine_paths(self):
        g = self.lib.add_game("サクラノ刻", "PC", exe=r"D:\a.exe", save_dir=r"D:\s")
        self.lib.add_playtime(g.game_id, "PC", 100)
        self.lib.edit(g.game_id, "Sakura no Toki", {"Laptop": MachinePaths(r"C:\b.exe", r"C:\s")})
        g2 = self.lib.get(g.game_id)
        self.assertEqual(g2.title, "Sakura no Toki")
        self.assertEqual(g2.playtime["PC"], 100)
        self.assertEqual(g2.paths["PC"].exe, r"D:\a.exe")
        self.assertEqual(g2.paths["Laptop"].exe, r"C:\b.exe")

    def test_rename_to_an_existing_title_is_rejected(self):
        self.lib.add_game("A", "PC")
        b = self.lib.add_game("B", "PC")
        with self.assertRaises(ValueError):
            self.lib.edit(b.game_id, "a", {})

    def test_remove_forgets_game_but_not_saves(self):
        g = self.lib.add_game("A", "PC")
        saves = self.root / "cloud" / "saves" / "A"
        saves.mkdir(parents=True)
        (saves / "s.dat").write_text("x")
        self.lib.remove(g.game_id)
        self.assertIsNone(self.lib.get("A"))
        self.assertTrue((saves / "s.dat").is_file())


if __name__ == "__main__":
    unittest.main()
