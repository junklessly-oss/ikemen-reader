"""
launcher.py - starts a game, waits until it is really finished, and wraps that in a save sync.

"Really finished" matters because many VNs start through a small launcher .exe that spawns the
actual game and exits straight away. So we don't just wait for the first process: we keep
watching for any process that is a child of it OR whose program lives inside the game's folder,
and only call the game finished once none have been seen for a short grace period (2 s).

When a game is launched through Luna, Luna itself stays open afterwards, so only processes from
the game's folder count ("folder-only" mode), and we allow time for Luna to start the game.

While the game runs we also note its window title - the most reliable source of a VN's real name.

Limitation: Windows hides details of programs running as administrator from normal programs.
If a game must run as admin, run Ikemen Reader as admin too.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import psutil

import detect
import luna
import outside
from library import Game, Library, LocalSettings
from sync_engine import Action, ConflictResolver, SyncEngine, save_folder_problem
from titles import clean_title, usable_window_title


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p))).rstrip("\\/")


def _inside(path: str, folder: Path) -> bool:
    """Is this program inside the folder? A plain path comparison: asking Windows to resolve every
    running program's path takes seconds the first time."""
    f = _norm(folder)
    n = _norm(path)
    return n == f or n.startswith(f + os.sep)


def _related_processes(root: Optional[psutil.Process], game_dir: Path,
                       family: Optional[dict[int, float]] = None, since: float = 0.0) -> dict[int, bool]:
    """pid -> "runs from the game folder", for the root process and everything it started (followed
    through parent ids, so a launcher that starts the game and exits straight away is still
    followed, even into another folder), plus anything running from inside the game folder.
    family: pid -> start time of processes known to belong to this launch (updated in place)."""
    found: dict[int, bool] = {}
    me = os.getpid()
    family = family if family is not None else {}
    if root is not None:
        try:
            if root.is_running() and root.status() != psutil.STATUS_ZOMBIE:
                found[root.pid] = False
                family.setdefault(root.pid, root.create_time())
        except psutil.Error:
            pass
    procs = []
    for p in psutil.process_iter(["pid", "ppid", "exe", "create_time"]):
        procs.append(p.info)
    # children of family members started after the launch (start time guards against reused pids)
    changed = True
    while changed:
        changed = False
        for info in procs:
            pid, ppid, ct = info.get("pid"), info.get("ppid"), info.get("create_time") or 0
            if pid not in family and ppid in family and ct >= since - 1 and ct >= family[ppid] - 1:
                family[pid] = ct
                changed = True
    for info in procs:
        pid, exe = info.get("pid"), info.get("exe")
        if pid == me:
            continue
        if exe and _inside(exe, game_dir):
            found[pid] = True
        elif pid in family and pid not in found:
            found[pid] = False
    return found


def warm_up() -> None:
    """The first look at every running program's details takes a few seconds on Windows (psutil
    caches them afterwards). Done in the background when the window opens, so Play reacts at once."""
    for _ in psutil.process_iter(["pid", "ppid", "exe", "create_time"]):
        time.sleep(0.01)      # a short pause per program, so the window's animations keep running smoothly


def already_running(game_dir: Path) -> bool:
    """Is something from this game's folder running right now?"""
    return bool(_related_processes(None, game_dir))


def _window_titles() -> dict[int, list[str]]:
    """pid -> titles of its visible top-level windows (Windows only)."""
    if sys.platform != "win32":
        return {}
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    out: dict[int, list[str]] = {}

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                out.setdefault(pid.value, []).append(buf.value)
        return True

    user32.EnumWindows(cb, 0)
    return out


def _ask_to_close(pids) -> int:
    """Ask these programs to close, like clicking their windows' X (the game can still ask "Quit?" and save
    its settings). Returns how many windows were asked."""
    if sys.platform != "win32":
        return 0
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    pids, targets = set(pids), []
    WM_CLOSE = 0x0010

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                targets.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    for hwnd in targets:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    return len(targets)


def _force_close(pids) -> None:
    """End these programs at once (only after the owner agreed: anything unsaved in the game is lost)."""
    for pid in pids:
        try:
            psutil.Process(pid).kill()
        except psutil.Error:
            pass        # already gone, or not ours to end (a game running as administrator)


class ElevationDeclined(OSError):
    """The game needs administrator rights and the owner said No to Windows' permission prompt."""


def _start_elevated(cmd: list[str], cwd: str) -> int:
    """Start cmd with administrator rights through Windows' own permission prompt ("Do you want to allow
    this app to make changes?"). Returns the new process id. (Normal starting can't do this: Windows
    answers error 740 instead.)"""
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]
    SEE_MASK_NOCLOSEPROCESS, SEE_MASK_NOASYNC, SW_SHOWNORMAL = 0x40, 0x100, 1
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb = "runas"
    info.lpFile = cmd[0]
    info.lpParameters = subprocess.list2cmdline(cmd[1:]) if len(cmd) > 1 else None
    info.lpDirectory = cwd
    info.nShow = SW_SHOWNORMAL
    shell32, kernel32 = ctypes.windll.shell32, ctypes.windll.kernel32
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    with outside.normal_dll_search():
        ok = shell32.ShellExecuteExW(ctypes.byref(info))
        err = ctypes.GetLastError()
    if not ok:
        if err == 1223:                         # ERROR_CANCELLED: the owner clicked No
            raise ElevationDeclined(err, "declined")
        raise OSError(err, "couldn't start with administrator rights", None, err)
    try:
        kernel32.GetProcessId.argtypes = [wintypes.HANDLE]
        return int(kernel32.GetProcessId(info.hProcess))
    finally:
        if info.hProcess:
            kernel32.CloseHandle(info.hProcess)


def _start(cmd: list[str], cwd: str):
    """(Popen, pid): start normally; if Windows says the program needs administrator rights, start it
    through the permission prompt instead (then there's no Popen, only the process id)."""
    try:
        return outside.popen(cmd, cwd=cwd), None
    except OSError as e:
        if getattr(e, "winerror", None) == 740 and sys.platform == "win32":
            return None, _start_elevated(cmd, cwd)
        raise


class _ExactClock:
    """Exact playtime on Windows. Windows records when every process started and exited (to a tenth of a
    microsecond), and keeps an "awake time" clock that stops while the PC sleeps or hibernates. Playtime is
    the time at least one of the game's processes was running, measured on that awake clock: the start
    and end are Windows' own, gaps between a launcher and the game don't count, and sleep never counts."""

    QUERY_LIMITED, SYNCHRONIZE = 0x1000, 0x00100000

    def __init__(self, not_before: float):
        import ctypes
        from ctypes import wintypes
        self.ok = sys.platform == "win32"
        self.not_before = not_before            # wall-clock time; nothing earlier counts (e.g. "watch" mode)
        self.handles: dict[int, int] = {}
        self.samples: list[tuple[float, float]] = []      # (wall clock, awake clock), taken as we go
        if not self.ok:
            return
        self.k32 = ctypes.windll.kernel32
        self.k32.OpenProcess.restype = wintypes.HANDLE
        self.k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
        self.k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._ull = ctypes.c_ulonglong
        self._byref = ctypes.byref
        self._awake_fn = getattr(self.k32, "QueryUnbiasedInterruptTimePrecise", None) or \
            getattr(self.k32, "QueryUnbiasedInterruptTime")
        self.sample()

    def awake(self) -> float:
        v = self._ull()
        self._awake_fn(self._byref(v))
        return v.value / 1e7

    def sample(self) -> None:
        if self.ok:
            self.samples.append((time.time(), self.awake()))

    def track(self, pids) -> None:
        """Keep a handle to each of the game's processes: Windows then keeps its start and exit times for us."""
        if not self.ok:
            return
        for pid in pids:
            if pid not in self.handles:
                h = self.k32.OpenProcess(self.QUERY_LIMITED | self.SYNCHRONIZE, False, pid)
                if h:
                    self.handles[pid] = h

    def _times(self) -> list[tuple[float, float]]:
        out = []
        now = time.time()
        for h in self.handles.values():
            c, e, k, u = self._ull(), self._ull(), self._ull(), self._ull()
            if self.k32.GetProcessTimes(h, self._byref(c), self._byref(e), self._byref(k), self._byref(u)):
                start = c.value / 1e7 - 11644473600            # FILETIME (1601) -> Unix time
                end = e.value / 1e7 - 11644473600 if e.value else now
                out.append((max(start, self.not_before), end))
        return [(a, b) for a, b in out if b > a]

    def _to_awake(self, wall: float) -> float:
        """The awake clock at a wall-clock moment, from the nearest sample taken at or after it (samples are
        a fraction of a second apart, so no sleep can hide in between)."""
        after = next((s for s in self.samples if s[0] >= wall), self.samples[-1])
        return after[1] - (after[0] - wall)

    def total(self) -> Optional[float]:
        if not self.ok or not self.handles:
            return None
        self.sample()
        spans = sorted((self._to_awake(a), self._to_awake(b)) for a, b in self._times())
        total, cur = 0.0, None
        for a, b in spans:                      # union: overlapping processes count once
            if cur is not None and a <= cur[1]:
                cur[1] = max(cur[1], b)
            else:
                if cur is not None:
                    total += cur[1] - cur[0]
                cur = [a, b]
        if cur is not None:
            total += cur[1] - cur[0]
        return max(0.0, total)

    def close(self) -> None:
        if self.ok:
            for h in self.handles.values():
                self.k32.CloseHandle(h)
            self.handles.clear()


def _wait_for_an_exit(pids, seconds: float, clock: Optional["_ExactClock"] = None) -> None:
    """Sleep up to `seconds`, but wake as soon as one of these processes ends (checking a few known
    processes is cheap; looking through every running program is not, so that happens less often)."""
    end = time.monotonic() + seconds
    while True:
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.15, left))
        if clock is not None:
            clock.sample()
        if any(not psutil.pid_exists(pid) for pid in pids):
            return


@dataclass
class RunInfo:
    seconds: float
    window_title: str = ""
    started: bool = True        # False if the game never appeared (e.g. Luna couldn't start it)
    cancelled: bool = False     # the owner pressed Stop before it appeared
    helper_failed: bool = False # the program meant to start it (Luna) quit with an error first


def run(cmd: Optional[list[str]], game_dir: Path, track_root: bool = True, poll: float = 1.0,
        grace: float = 2.0, startup_wait: float = 90.0, cwd: Optional[Path] = None,
        stop: Callable[[], bool] = lambda: False, force: Callable[[], bool] = lambda: False,
        on_found: Callable[[], None] = lambda: None) -> RunInfo:
    """Run cmd and block until the game (and anything it spawned) has finished.
    track_root=False: the started program isn't the game (Luna) - only the game folder counts,
    and we wait up to startup_wait seconds for the game to show up.
    cmd=None: start nothing, just watch a game that is already running.
    stop() -> True (once per press of Stop): before the game appears, stop waiting; after, ask it to close.
    force() -> True: end the game's processes at once. on_found() is called once when the game appears.
    Playtime only counts time the PC was awake (a long gap between checks = sleep/hibernate)."""
    launched_at = time.time()
    clock = _ExactClock(not_before=launched_at)          # nothing from before Play counts
    proc, pid = _start(cmd, str(cwd or game_dir)) if cmd else (None, None)
    if (proc or pid) and track_root:
        clock.track([proc.pid if proc else pid])      # at once: a launcher may hand over and exit instantly
    root = None
    if (proc or pid) and track_root:
        try:
            root = psutil.Process(proc.pid if proc else pid)
        except psutil.Error:            # it already exited (a launcher that hands over instantly, or a crash)
            root = None
    family: dict[int, float] = {}
    start = time.monotonic()
    first_seen: Optional[float] = None
    last_seen = prev = start
    active = 0.0
    was_found = False
    titles: Counter = Counter()
    while True:
        if proc:
            proc.poll()  # reap the root process if it has already exited
        found = _related_processes(root, game_dir, family, launched_at)
        clock.track(found)
        clock.sample()
        now = time.monotonic()
        if not found and was_found:
            # it ended since the last look: that last stretch was still playing (we look again within
            # 0.15 s of an exit, so this is accurate to a fraction of a second)
            active += min(now - prev, max(poll, 0.2) * 5 + 2)
        was_found = bool(found)
        if found:
            if first_seen is None:
                first_seen = now
                on_found()
            else:
                active += min(now - prev, max(poll, 0.2) * 5 + 2)   # a big jump = the PC was asleep
            last_seen = now
            wins = _window_titles()
            for pid, from_folder in found.items():
                for t in wins.get(pid, []):
                    if usable_window_title(t):
                        titles[t] += 2 if from_folder else 1
        elif first_seen is None:
            if now - start > startup_wait or not cmd:
                return RunInfo(0, "", started=False)
            if stop():
                return RunInfo(0, "", started=False, cancelled=True)
            if not track_root and proc and proc.returncode not in (None, 0):
                return RunInfo(0, "", started=False, helper_failed=True)   # Luna crashed: don't wait 90 s
        elif now - last_seen > grace:
            break
        prev = now
        if found:
            if force():
                _force_close(list(found))
            elif stop():
                _ask_to_close(list(found))
            # while it runs: react the moment one of its processes ends (check often in the first
            # seconds: a launcher may hand over to the game very quickly)
            _wait_for_an_exit(list(found), min(poll, 0.1) if now - start < 3 else poll, clock)
        elif first_seen is not None:
            time.sleep(0.25)             # it just closed: look for a hand-over to another process quickly
        else:
            time.sleep(min(poll, 0.1) if now - start < 3 else poll)   # waiting for it to appear
    title = titles.most_common(1)[0][0] if titles else ""
    if track_root and cmd:
        active += (first_seen or start) - start        # time before the first check counts too
    exact = None
    try:
        exact = clock.total()
    except (OSError, ValueError, AttributeError):
        exact = None
    finally:
        clock.close()
    return RunInfo(exact if exact is not None else active, title)   # (the estimate only where Windows can't say)


def run_game(exe: str, wrapper: str = "", poll: float = 1.0, grace: float = 2.0) -> float:
    """Start the game (optionally through a wrapper like LEProc.exe) and wait for it.
    Returns the number of seconds it was running."""
    exe_path = Path(exe).resolve()
    cmd = [wrapper, str(exe_path)] if wrapper else [str(exe_path)]
    return run(cmd, exe_path.parent, poll=poll, grace=grace).seconds


@dataclass
class PlayResult:
    launched: bool
    message: str                       # i18n key
    seconds: float = 0.0               # to the millisecond
    window_title: str = ""
    new_title: str = ""                # set if the library title was updated from the window title
    candidates: list = field(default_factory=list)   # detect.Candidate, when the save folder was unknown
    started_at: float = 0.0
    retry_without: str = ""            # "luna" / "le": the UI may offer to launch without that option


def launch_plan(game: Game, settings: LocalSettings) -> tuple[Optional[str], dict]:
    """Check the game's launch options against this device. Returns (problem_key or None, plan)."""
    mine = game.paths_for(settings.machine_name)
    plan = {"luna": None, "le": ""}
    if game.use_luna:
        lp = settings.luna_path
        if not lp or not Path(lp).is_file():
            return "play.luna_missing", plan
        if luna.kind(lp) == "translator":
            uid = luna.find_uid(lp, mine.exe)
            if uid is None:
                return "play.luna_unknown_game", plan
            if luna.running(lp):
                return "play.luna_running", plan
            plan["luna"] = ("translator", lp, uid)
        else:
            plan["luna"] = ("hook", lp, None)
    # with LunaTranslator, Luna's own per-game Locale Emulator setting applies instead
    if game.use_le and not (plan["luna"] and plan["luna"][0] == "translator"):
        if not settings.le_path or not Path(settings.le_path).is_file():
            return "play.le_missing", plan
        plan["le"] = settings.le_path
    return None, plan


def play(game: Game, settings: LocalSettings, library: Library, engine: SyncEngine,
         on_conflict: Optional[ConflictResolver] = None,
         status: Callable[[str], None] = print,
         poll: float = 1.0, grace: float = 2.0,
         on_phase: Callable[[str], None] = lambda phase: None,
         without: tuple[str, ...] = (), watch: bool = False,
         stop: Callable[[], bool] = lambda: False, force: Callable[[], bool] = lambda: False) -> PlayResult:
    """Sync saves down, run the game (through Luna / Locale Emulator if ticked), sync saves back up,
    record playtime and "last played". on_phase is told "syncing", "starting" (waiting for the game to
    appear), "playing" and "done". stop/force: the Stop button (see run()).
    without: launch options to skip this time ("luna", "le") after the UI asked the owner.
    If this device has no save folder yet, the game runs without syncing and the result carries
    detected save-location candidates for the owner to confirm (see finish_first_play)."""
    machine = settings.machine_name
    mine = game.paths_for(machine)
    if not mine.exe or not Path(mine.exe).is_file():
        return PlayResult(False, "play.no_exe")
    g = Game(**{**game.__dict__})
    if "luna" in without:
        g.use_luna = False
    if "le" in without:
        g.use_le = False
    problem, plan = launch_plan(g, settings)
    if problem and not watch:
        return PlayResult(False, problem, retry_without="luna" if "luna" in problem else "le")
    if mine.save_dir:
        unsafe = save_folder_problem(mine.save_dir, engine.cloud_root)
        if unsafe:
            return PlayResult(False, unsafe)
    exe_path = Path(mine.exe).resolve()
    game_dir = exe_path.parent
    if not watch and already_running(game_dir):
        return PlayResult(False, "play.already_running", retry_without="watch")

    detecting = not mine.save_dir
    if not detecting and not watch:          # watching: the running game has the saves loaded already
        on_phase("syncing")
        status("status.checking")
        try:
            before = engine.before_launch(game.game_id, mine.save_dir, on_conflict, game.registry_key)
        except (OSError, ValueError):       # a file in use, a damaged registry export, an unusable key...
            return PlayResult(False, "sync.failed")
        finally:
            on_phase("done")
        status(before.message)
        if before.action is Action.CONFLICT:
            return PlayResult(False, "play.not_launched_conflict")

    status("status.launching")
    on_phase("starting")
    started_at = time.time()
    waiting = dict(stop=stop, force=force, on_found=lambda: on_phase("playing"))
    try:
        if watch:
            info = run(None, game_dir, poll=poll, grace=grace, **waiting)
        elif plan["luna"] and plan["luna"][0] == "translator":
            _, lp, uid = plan["luna"]
            info = run([lp, "--Exec", uid], game_dir, track_root=False, poll=poll, grace=grace,
                       cwd=Path(lp).parent, **waiting)
        else:
            if plan["luna"]:
                luna.open_alongside(plan["luna"][1])
            cmd = [plan["le"], str(exe_path)] if plan["le"] else [str(exe_path)]
            info = run(cmd, game_dir, poll=poll, grace=grace, **waiting)
    except ElevationDeclined:
        on_phase("done")
        return PlayResult(False, "play.admin_declined")
    except OSError as e:
        on_phase("done")
        if getattr(e, "winerror", None) == 740:
            return PlayResult(False, "play.needs_admin")
        return PlayResult(False, "play.couldnt_start")
    if not info.started:
        on_phase("done")
        if info.cancelled:
            return PlayResult(False, "play.cancelled")
        if info.helper_failed:
            return PlayResult(False, "play.luna_failed", retry_without="luna")
        return PlayResult(False, "play.never_started")
    seconds = round(info.seconds, 3)          # kept to the millisecond (shown rounded, stored exactly)

    result = PlayResult(True, "", seconds, info.window_title, started_at=started_at)
    on_phase("syncing")
    try:
        if detecting:
            status("status.detecting")
            names = [game.title, exe_path.stem, game_dir.name, info.window_title]
            skip = [str(engine.cloud_root), str(engine.state_dir), settings.luna_path and str(Path(settings.luna_path).parent),
                    settings.le_path and str(Path(settings.le_path).parent)]
            try:
                result.candidates = detect.find_folders(str(game_dir), started_at - 2, names, skip) + \
                    detect.find_registry(started_at - 2, names)
            except (OSError, ValueError):
                result.candidates = []
            result.message = "play.detected" if result.candidates else "play.detect_none"
        else:
            status("status.closed_syncing")
            try:
                after = engine.after_exit(game.game_id, mine.save_dir, on_conflict, game.registry_key)
                result.message = after.message
            except (OSError, ValueError):
                result.message = "sync.failed"   # playtime is still recorded; it uploads next time
        try:
            library.record_session(game.game_id, machine, seconds)
            fresh = library.get(game.game_id)
            t = clean_title(info.window_title) if info.window_title else ""
            if fresh and t and t != fresh.auto_title:
                fresh = library.update(game.game_id, auto_title=t)   # remembered for the "Automatic" button
            if fresh and not fresh.title_locked and t and t != fresh.title:
                try:
                    library.update(game.game_id, title=t)
                    result.new_title = t
                except ValueError:
                    pass  # another game already has that title; keep the current one
        except OSError:
            pass   # the cloud folder was briefly unavailable: the saves part above still stands
    finally:
        on_phase("done")
    status(result.message)
    return result


def finish_first_play(game: Game, settings: LocalSettings, library: Library, engine: SyncEngine,
                      save_dir: str, registry_key: str = "",
                      on_conflict: Optional[ConflictResolver] = None):
    """The owner confirmed where this game saves: store it and do the first sync. If the other
    device already uploaded saves, this asks which to keep (nothing is lost either way)."""
    library.set_paths(game.game_id, settings.machine_name, save_dir=save_dir)
    if registry_key:
        library.update(game.game_id, registry_key=registry_key)
    return engine.before_launch(game.game_id, save_dir, on_conflict, registry_key)
