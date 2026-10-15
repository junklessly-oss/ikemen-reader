"""Run with:  python -m unittest -v"""
import os
import tempfile
import unittest
from pathlib import Path

from sync_engine import Action, SyncEngine, fingerprint


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


GAME = "Fake VN"


def write(folder: Path, name: str, text: str) -> None:
    p = folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, "utf-8")


def read_all(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): p.read_text("utf-8")
            for p in folder.rglob("*") if p.is_file()}


class TwoMachines(unittest.TestCase):
    """PC and laptop share one 'cloud' folder but have their own saves and bookkeeping."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.cloud = root / "OneDrive" / "VNSaves"
        self.pc_saves, self.laptop_saves = root / "pc_saves", root / "laptop_saves"
        self.pc = SyncEngine(self.cloud, root / "pc_state")
        self.laptop = SyncEngine(self.cloud, root / "laptop_state")

    def tearDown(self):
        self._tmp.cleanup()

    def test_status_reports_what_would_happen_without_changing_anything(self):
        write(self.pc_saves, "slot1.sav", "chapter 1")
        self.pc.before_launch(GAME, self.pc_saves)
        self.laptop.before_launch(GAME, self.laptop_saves)       # laptop now has chapter 1
        write(self.pc_saves, "slot1.sav", "chapter 2")            # PC plays without syncing
        self.assertEqual(self.pc.status(GAME, self.pc_saves), Action.PUSHED)
        self.assertEqual(self.laptop.status(GAME, self.laptop_saves), Action.NOTHING)
        self.assertEqual(read_all(self.pc.cloud_dir(GAME)), {"slot1.sav": "chapter 1"})  # untouched

    def test_first_launch_uploads_existing_saves(self):
        write(self.pc_saves, "slot1.sav", "chapter 1")
        r = self.pc.before_launch(GAME, self.pc_saves)
        self.assertEqual(r.action, Action.PUSHED)
        self.assertEqual(read_all(self.pc.cloud_dir(GAME)), {"slot1.sav": "chapter 1"})

    def test_progress_travels_between_machines(self):
        write(self.pc_saves, "slot1.sav", "chapter 1")
        self.pc.before_launch(GAME, self.pc_saves)

        # laptop starts the game: should receive the PC's save
        r = self.laptop.before_launch(GAME, self.laptop_saves)
        self.assertEqual(r.action, Action.PULLED)
        self.assertEqual(read_all(self.laptop_saves), {"slot1.sav": "chapter 1"})

        # laptop plays on, then quits
        write(self.laptop_saves, "slot1.sav", "chapter 2")
        write(self.laptop_saves, "slot2.sav", "extra")
        self.assertEqual(self.laptop.after_exit(GAME, self.laptop_saves).action, Action.PUSHED)

        # back on the PC
        r = self.pc.before_launch(GAME, self.pc_saves)
        self.assertEqual(r.action, Action.PULLED)
        self.assertEqual(read_all(self.pc_saves), {"slot1.sav": "chapter 2", "slot2.sav": "extra"})

    def test_nothing_to_do_when_in_sync(self):
        write(self.pc_saves, "a.sav", "x")
        self.pc.before_launch(GAME, self.pc_saves)
        self.assertEqual(self.pc.before_launch(GAME, self.pc_saves).action, Action.NOTHING)
        self.assertEqual(self.pc.after_exit(GAME, self.pc_saves).action, Action.NOTHING)

    def test_deleted_save_propagates(self):
        write(self.pc_saves, "a.sav", "x")
        write(self.pc_saves, "b.sav", "y")
        self.pc.before_launch(GAME, self.pc_saves)
        self.laptop.before_launch(GAME, self.laptop_saves)
        (self.laptop_saves / "b.sav").unlink()
        self.laptop.after_exit(GAME, self.laptop_saves)
        self.pc.before_launch(GAME, self.pc_saves)
        self.assertEqual(read_all(self.pc_saves), {"a.sav": "x"})

    def test_conflict_is_detected_and_nothing_is_overwritten(self):
        write(self.pc_saves, "a.sav", "start")
        self.pc.before_launch(GAME, self.pc_saves)
        self.laptop.before_launch(GAME, self.laptop_saves)

        write(self.pc_saves, "a.sav", "pc progress")
        self.pc.after_exit(GAME, self.pc_saves)               # PC uploads first
        write(self.laptop_saves, "a.sav", "laptop progress")  # laptop played offline

        r = self.laptop.after_exit(GAME, self.laptop_saves)
        self.assertEqual(r.action, Action.CONFLICT)
        self.assertEqual(read_all(self.laptop_saves), {"a.sav": "laptop progress"})
        self.assertEqual(read_all(self.laptop.cloud_dir(GAME)), {"a.sav": "pc progress"})

    def test_conflict_resolved_with_cloud_keeps_a_backup(self):
        write(self.pc_saves, "a.sav", "start")
        self.pc.before_launch(GAME, self.pc_saves)
        self.laptop.before_launch(GAME, self.laptop_saves)
        write(self.pc_saves, "a.sav", "pc progress")
        self.pc.after_exit(GAME, self.pc_saves)
        write(self.laptop_saves, "a.sav", "laptop progress")

        r = self.laptop.before_launch(GAME, self.laptop_saves, on_conflict=lambda info: "cloud")
        self.assertEqual(r.action, Action.PULLED)
        self.assertEqual(read_all(self.laptop_saves), {"a.sav": "pc progress"})
        backups = list(self.laptop._backup_root(GAME).iterdir())
        self.assertEqual(len(backups), 1)
        self.assertEqual(read_all(backups[0]), {"a.sav": "laptop progress"})

    def test_conflict_resolved_with_local_overwrites_cloud(self):
        write(self.pc_saves, "a.sav", "pc")
        write(self.laptop_saves, "a.sav", "laptop")   # never synced, both differ
        self.pc.before_launch(GAME, self.pc_saves)
        r = self.laptop.before_launch(GAME, self.laptop_saves, on_conflict=lambda i: "local")
        self.assertEqual(r.action, Action.PUSHED)
        self.assertEqual(read_all(self.laptop.cloud_dir(GAME)), {"a.sav": "laptop"})

    def test_unresolved_conflict_changes_nothing(self):
        write(self.pc_saves, "a.sav", "pc")
        write(self.laptop_saves, "a.sav", "laptop")
        self.pc.before_launch(GAME, self.pc_saves)
        r = self.laptop.before_launch(GAME, self.laptop_saves)
        self.assertEqual(r.action, Action.CONFLICT)
        self.assertEqual(read_all(self.laptop_saves), {"a.sav": "laptop"})

    def test_junk_files_do_not_count_as_changes(self):
        write(self.pc_saves, "a.sav", "x")
        self.pc.before_launch(GAME, self.pc_saves)
        write(self.pc_saves, "Thumbs.db", "junk")
        self.assertEqual(self.pc.after_exit(GAME, self.pc_saves).action, Action.NOTHING)

    def test_nested_save_folders_work(self):
        write(self.pc_saves, "profile/slot1/data.sav", "deep")
        self.pc.before_launch(GAME, self.pc_saves)
        self.laptop.before_launch(GAME, self.laptop_saves)
        self.assertEqual(read_all(self.laptop_saves), {"profile/slot1/data.sav": "deep"})
        self.assertEqual(fingerprint(self.pc_saves), fingerprint(self.laptop_saves))

    def test_unsafe_game_names_stay_inside_cloud_folder(self):
        path = self.pc.cloud_dir("../../evil")
        self.assertTrue(str(path).startswith(str(self.cloud)))

    def test_old_backups_are_pruned(self):
        write(self.pc_saves, "a.sav", "0")
        self.pc.before_launch(GAME, self.pc_saves)
        for i in range(1, 16):
            write(self.pc_saves, "a.sav", str(i))
            self.pc.after_exit(GAME, self.pc_saves)
        self.assertLessEqual(len(list(self.pc._backup_root(GAME).iterdir())), 10)


if __name__ == "__main__":
    unittest.main()

class Attacks(unittest.TestCase):
    """Things that could go wrong in real life. Each must end with no save lost."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.root = root
        self.cloud = root / "OneDrive" / "VNSaves"
        self.pc_saves, self.laptop_saves = root / "pc_saves", root / "laptop_saves"
        self.pc = SyncEngine(self.cloud, root / "pc_state", computers=lambda: ["PC-1", "LAPTOP-2"])
        self.laptop = SyncEngine(self.cloud, root / "laptop_state", computers=lambda: ["PC-1", "LAPTOP-2"])

    def tearDown(self):
        self._tmp.cleanup()

    def test_game_files_that_look_like_sync_app_copies_are_never_deleted(self):
        # a game that really uses both names (Google Drive's copies look like "save (1).dat")
        write(self.pc_saves, "save.dat", "a")
        write(self.pc_saves, "save (1).dat", "b")
        write(self.pc_saves, "data-PC-1.sav", "c")          # looks like a OneDrive copy too
        self.pc.before_launch(GAME, self.pc_saves)
        for _ in range(3):                                   # play, sync, play, sync...
            self.pc.after_exit(GAME, self.pc_saves)
            self.assertEqual(self.pc.before_launch(GAME, self.pc_saves).action, Action.NOTHING)
        self.assertEqual(read_all(self.pc_saves), {"save.dat": "a", "save (1).dat": "b", "data-PC-1.sav": "c"})

    def test_real_sync_app_copies_are_still_ignored(self):
        write(self.pc_saves, "save.dat", "mine")
        self.pc.before_launch(GAME, self.pc_saves)
        write(self.pc.cloud_dir(GAME), "save (1).dat", "copy made by Google Drive")
        write(self.pc.cloud_dir(GAME), "save-LAPTOP-2.dat", "copy made by OneDrive")
        self.assertEqual(self.pc.status(GAME, self.pc_saves), Action.NOTHING)
        self.laptop.before_launch(GAME, self.laptop_saves)
        self.assertEqual(read_all(self.laptop_saves), {"save.dat": "mine"})

    def test_a_games_own_empty_folders_survive_syncing(self):
        write(self.pc_saves, "slot1.sav", "1")
        (self.pc_saves / "screenshots").mkdir()             # the game made it and expects it
        self.pc.before_launch(GAME, self.pc_saves)
        self.laptop.before_launch(GAME, self.laptop_saves)
        write(self.laptop_saves, "slot1.sav", "2")
        self.laptop.after_exit(GAME, self.laptop_saves)
        self.assertEqual(self.pc.before_launch(GAME, self.pc_saves).action, Action.PULLED)
        self.assertTrue((self.pc_saves / "screenshots").is_dir())

    def test_folders_emptied_by_a_sync_are_tidied(self):
        write(self.pc_saves, "chapter2/slot.sav", "1")
        self.pc.before_launch(GAME, self.pc_saves)
        (self.pc_saves / "chapter2" / "slot.sav").unlink()
        write(self.pc_saves, "slot.sav", "moved")
        self.pc.after_exit(GAME, self.pc_saves)
        self.assertFalse((self.pc.cloud_dir(GAME) / "chapter2").exists())

    def test_damaged_sync_records_never_crash_and_only_ever_ask(self):
        write(self.pc_saves, "slot1.sav", "1")
        self.pc.before_launch(GAME, self.pc_saves)
        write(self.pc_saves, "slot1.sav", "2")                   # played without syncing
        state = self.root / "pc_state" / "state" / f"{GAME}.json"
        for junk in ("", "{", "[1, 2]", '{"base": 5, "files": "x"}', "\\x00\\xff", '"just text"'):
            state.write_text(junk, "utf-8")
            asked = []
            result = self.pc.before_launch(GAME, self.pc_saves, on_conflict=lambda i: asked.append(i) or None)
            self.assertEqual(result.action, Action.CONFLICT, junk)
            self.assertEqual(len(asked), 1)
            self.assertEqual(read_all(self.pc_saves), {"slot1.sav": "2"})      # nothing overwritten
        self.assertEqual([p.name for p in state.parent.iterdir()], [state.name])  # no temp files left

    def test_changed_files_are_always_noticed(self):
        # the remembered hashes must never hide a real change
        f = self.pc_saves / "slot1.sav"
        write(self.pc_saves, "slot1.sav", "AAAA")
        first = fingerprint(self.pc_saves)
        write(self.pc_saves, "slot1.sav", "BBBB")                # same size
        import os
        st = f.stat()
        os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1))
        self.assertNotEqual(fingerprint(self.pc_saves), first)

    def test_conflict_choices_always_keep_a_backup_of_the_loser(self):
        write(self.pc_saves, "slot1.sav", "base")
        self.pc.before_launch(GAME, self.pc_saves)
        self.laptop.before_launch(GAME, self.laptop_saves)
        write(self.laptop_saves, "slot1.sav", "laptop route")
        self.laptop.after_exit(GAME, self.laptop_saves)
        write(self.pc_saves, "slot1.sav", "pc route")
        self.pc.before_launch(GAME, self.pc_saves, on_conflict=lambda i: "cloud")
        self.assertEqual(read_all(self.pc_saves), {"slot1.sav": "laptop route"})
        backups = self.pc.list_backups(GAME)
        self.assertEqual(read_all(backups[0].path), {"slot1.sav": "pc route"})
        self.pc.restore(GAME, backups[0].name, self.pc_saves)               # and it can come back
        self.assertEqual(read_all(self.pc_saves), {"slot1.sav": "pc route"})

    def test_only_ten_backups_are_kept_newest_first(self):
        write(self.pc_saves, "slot1.sav", "0")
        self.pc.before_launch(GAME, self.pc_saves)
        for i in range(1, 15):
            write(self.pc_saves, "slot1.sav", str(i))
            self.pc.after_exit(GAME, self.pc_saves)
        backups = self.pc.list_backups(GAME)
        self.assertEqual(len(backups), 10)
        self.assertEqual(read_all(backups[0].path), {"slot1.sav": "13"})      # the cloud copy before the last push

    def test_unsafe_folders_are_refused_everywhere(self):
        from sync_engine import UnsafeSaveFolder
        for bad in ("", "  ", ".", "relative/path", str(Path.home()), str(self.cloud), str(self.cloud.parent),
                    str(self.cloud / "saves" / "x"), Path.home().anchor):
            for call in (lambda: self.pc.before_launch(GAME, bad), lambda: self.pc.after_exit(GAME, bad),
                         lambda: self.pc.status(GAME, bad), lambda: self.pc.push_if_safe(GAME, bad)):
                with self.assertRaises(UnsafeSaveFolder, msg=repr(bad)):
                    call()

    def test_a_file_the_game_holds_open_briefly_is_retried(self):
        import threading, os
        write(self.pc_saves, "slot1.sav", "1")
        self.pc.before_launch(GAME, self.pc_saves)
        self.laptop.before_launch(GAME, self.laptop_saves)
        write(self.laptop_saves, "slot1.sav", "2")
        self.laptop.after_exit(GAME, self.laptop_saves)
        target = self.pc_saves / "slot1.sav"
        os.chmod(target, 0o444)                                    # read-only, as some games leave it
        self.assertEqual(self.pc.before_launch(GAME, self.pc_saves).action, Action.PULLED)
        self.assertEqual(read_all(self.pc_saves), {"slot1.sav": "2"})

    def test_backups_made_in_the_same_second_keep_their_order(self):
        write(self.pc_saves, "slot1.sav", "before restore")
        self.pc.before_launch(GAME, self.pc_saves)
        write(self.pc_saves, "slot1.sav", "played")
        self.pc.after_exit(GAME, self.pc_saves)                 # backup 1: cloud copy before the upload
        first = self.pc.list_backups(GAME)[0]
        self.pc.restore(GAME, first.name, self.pc_saves)        # backup 2 (local) and 3 (cloud), same second
        kinds = [b.reason for b in self.pc.list_backups(GAME)]
        self.assertEqual(kinds, ["cloud-before-push", "local-before-restore", "cloud-before-push"])

    def test_two_syncs_of_one_game_never_overlap(self):
        import threading
        write(self.pc_saves, "slot1.sav", "1")
        for i in range(40):
            write(self.pc_saves, f"cg/{i}.png", "x" * 2000)
        self.pc.before_launch(GAME, self.pc_saves)
        write(self.pc_saves, "slot1.sav", "2")
        inside, overlaps = [0], []
        real = self.pc._push

        def watched(*a, **k):
            inside[0] += 1
            if inside[0] > 1:
                overlaps.append(True)
            try:
                return real(*a, **k)
            finally:
                inside[0] -= 1
        self.pc._push = watched
        threads = [threading.Thread(target=lambda: self.pc.push_if_safe(GAME, self.pc_saves)),
                   threading.Thread(target=lambda: self.pc.before_launch(GAME, self.pc_saves)),
                   threading.Thread(target=lambda: self.pc.after_exit(GAME, self.pc_saves))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(overlaps, [])
        self.assertEqual(self.pc.status(GAME, self.pc_saves), Action.NOTHING)
        self.assertEqual(read_all(self.pc.cloud_dir(GAME))["slot1.sav"], "2")

    def test_separate_engines_for_one_cloud_folder_share_the_lock(self):
        other = SyncEngine(self.cloud, self.root / "pc_state")
        self.assertIs(self.pc._lock(GAME), other._lock(GAME))
        self.assertIsNot(self.pc._lock(GAME), self.pc._lock("Another Game"))


class SilentSync(unittest.TestCase):
    """sync_if_safe: background sync without a launch. Uploads or downloads only when one side changed."""
    setUp, tearDown = TwoMachines.setUp, TwoMachines.tearDown

    def test_downloads_when_only_the_other_device_changed(self):
        write(self.pc_saves, "slot1.sav", "chapter 1")
        self.pc.before_launch(GAME, self.pc_saves)
        self.laptop.before_launch(GAME, self.laptop_saves)
        write(self.laptop_saves, "slot1.sav", "chapter 2")
        self.assertEqual(self.laptop.sync_if_safe(GAME, self.laptop_saves), Action.PUSHED)   # laptop uploads
        self.assertEqual(self.pc.sync_if_safe(GAME, self.pc_saves), Action.PULLED)           # PC downloads
        self.assertEqual(read_all(self.pc_saves), {"slot1.sav": "chapter 2"})
        self.assertEqual(self.pc.list_backups(GAME)[0].reason, "local-before-pull")         # old one kept
        self.assertEqual(self.pc.sync_if_safe(GAME, self.pc_saves), Action.NOTHING)

    def test_changed_on_both_sides_is_left_alone(self):
        write(self.pc_saves, "slot1.sav", "chapter 1")
        self.pc.before_launch(GAME, self.pc_saves)
        self.laptop.before_launch(GAME, self.laptop_saves)
        write(self.laptop_saves, "slot1.sav", "laptop route")
        self.laptop.sync_if_safe(GAME, self.laptop_saves)
        write(self.pc_saves, "slot1.sav", "pc route")
        self.assertEqual(self.pc.sync_if_safe(GAME, self.pc_saves), Action.CONFLICT)
        self.assertEqual(read_all(self.pc_saves), {"slot1.sav": "pc route"})
        self.assertEqual(read_all(self.pc.cloud_dir(GAME)), {"slot1.sav": "laptop route"})

    def test_download_can_be_held_back(self):
        write(self.pc_saves, "slot1.sav", "chapter 1")
        self.pc.before_launch(GAME, self.pc_saves)
        self.assertEqual(self.laptop.sync_if_safe(GAME, self.laptop_saves, allow_pull=False), Action.NOTHING)
        self.assertFalse(self.laptop_saves.exists())
