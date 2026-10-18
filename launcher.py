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
import threading
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
from titles import clean_title, session_title, shared_part, usable_window_title


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(str(p))).rstrip("\\/")


def _inside(path: str, folder: Path) -> bool:
    """Is this program inside the folder? A plain path comparison: asking Windows to resolve every
    running program's path takes seconds the first time."""
    f = _norm(folder)
    n = _norm(path)
    return n == f or n.startswith(f + os.sep)


_NT_BUF: list = [None]          # the buffer NtQuerySystemInformation fills (kept: it's asked many times a second)
_NT_LOCK = threading.Lock()     # (the launch watch, the save check and Luna's check may all ask at once)


def _snapshot_nt() -> Optional[list[tuple[int, int]]]:
    """(pid, parent pid) of every running program in ONE call to Windows (NtQuerySystemInformation), read back with
    struct: Python is let go during the call and held only for a quick read of the result. (Toolhelp needs one call per
    program - ~320 - and each one waits to get Python back: with the window busy, one look took up to a second.)"""
    import ctypes
    import struct
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return None                                        # (the offsets below are the 64-bit layout)
    ntdll = ctypes.windll.ntdll
    query = ntdll.NtQuerySystemInformation
    query.restype = ctypes.c_long
    query.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    with _NT_LOCK:
        raw = _nt_query(query)
    if raw is None:
        return None
    out, at = [], 0
    while True:
        nxt = struct.unpack_from("<I", raw, at)[0]
        pid, ppid = struct.unpack_from("<QQ", raw, at + 0x50)    # UniqueProcessId, InheritedFromUniqueProcessId
        out.append((pid, ppid))
        if not nxt:
            return out
        at += nxt


def _nt_query(query) -> Optional[bytes]:
    import ctypes
    size = 1 << 20
    for _ in range(6):
        buf = _NT_BUF[0]
        if buf is None or len(buf) < size:
            buf = _NT_BUF[0] = ctypes.create_string_buffer(size)
        need = ctypes.c_ulong(0)
        status = query(5, buf, len(buf), ctypes.byref(need))     # SystemProcessInformation
        if status == 0:
            break
        if status & 0xFFFFFFFF != 0xC0000004:                     # (not "buffer too small": give up)
            return None
        size = max(size * 2, need.value + (1 << 16))
    else:
        return None
    return buf.raw[:need.value or len(buf)]                    # (a copy: the buffer is used again by the next look)


def _snapshot() -> Optional[list[tuple[int, int]]]:
    """(pid, parent pid) of every running program, straight from Windows through ctypes - which lets go of Python while
    Windows works, so the window keeps drawing (psutil's own table held Python ~7 ms each time, and the launch / close
    watch asks several times a second: the sync light's fade stuttered). One call (_snapshot_nt), else Toolhelp."""
    if sys.platform != "win32":
        return None
    try:
        quick = _snapshot_nt()
        if quick:
            return quick
    except Exception:  # noqa: BLE001 - Toolhelp instead
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class ENTRY(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

        k32 = ctypes.windll.kernel32
        k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)            # TH32CS_SNAPPROCESS
        if not snap or snap == wintypes.HANDLE(-1).value:
            return None
        try:
            e = ENTRY()
            e.dwSize = ctypes.sizeof(ENTRY)
            out = []
            ok = k32.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                out.append((e.th32ProcessID, e.th32ParentProcessID))
                ok = k32.Process32NextW(snap, ctypes.byref(e))
            return out
        finally:
            k32.CloseHandle(snap)
    except Exception:  # noqa: BLE001 - fall back to psutil
        return None


_DETAILS: dict[int, tuple[int, Optional[str], float]] = {}   # pid -> (parent, exe, start time): asked once each


def _details_win(pid: int) -> Optional[tuple[Optional[str], float]]:
    """(exe, start time) straight from Windows through ctypes - which lets go of Python while Windows answers, so
    the window keeps drawing (psutil's exe() held it: a launch right after many programs had started hitched).
    None: couldn't ask this way (psutil is tried instead)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        # (the same argument types as _ExactClock's: windll's functions are shared by the whole program, and a
        # mismatch made every lookup fail - silently falling back to psutil - after the first launch)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None, 0.0                             # (a protected system program: never a game of ours)
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            exe = buf.value if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else None
            created, ex, kt, ut = (ctypes.c_ulonglong() for _ in range(4))
            ct = 0.0
            if k32.GetProcessTimes(h, ctypes.byref(created), ctypes.byref(ex), ctypes.byref(kt), ctypes.byref(ut)):
                ct = created.value / 1e7 - 11644473600.0        # 100 ns since 1601 -> seconds since 1970
            return exe, ct
        finally:
            k32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        return None


def _details(pid: int) -> tuple[Optional[str], float]:
    """(exe, start time) of one program (None / 0 when it's gone or not ours to look at)."""
    quick = _details_win(pid)
    if quick is not None:
        return quick
    exe, ct = None, 0.0
    try:
        proc = psutil.Process(pid)
        with proc.oneshot():
            ct = proc.create_time()
            try:
                exe = proc.exe()
            except psutil.Error:
                exe = None
    except psutil.Error:
        pass
    return exe, ct


def _parents() -> Optional[dict[int, int]]:
    """pid -> parent pid for every running program, in ONE call. Asking psutil for each process's parent separately
    rebuilds that same table every time: ~10 ms per process, 3+ s for a first look at ~300 programs."""
    snap = _snapshot()
    if snap is not None:
        return dict(snap)
    try:
        from psutil import _pswindows
        table = _pswindows.cext.ppid_map()
        return table if isinstance(table, dict) else None
    except Exception:  # noqa: BLE001 - not Windows, or psutil changed: ask per process instead
        return None


def _scan() -> list[dict]:
    """pid, ppid, exe and create_time of every running program. A program's exe and start time are asked once and
    remembered (a pid whose parent changed was reused: asked again), so a scan is mostly the quick snapshot."""
    snap = _snapshot()
    if snap is None:
        parents = _parents()
        if parents is None:
            return [p.info for p in psutil.process_iter(["pid", "ppid", "exe", "create_time"])]
        out = []
        for p in psutil.process_iter(["pid", "exe", "create_time"]):
            info = p.info
            info["ppid"] = parents.get(info.get("pid"))
            out.append(info)
        return out
    out, alive = [], set()
    for pid, ppid in snap:
        alive.add(pid)
        known = _DETAILS.get(pid)
        if known is None or known[0] != ppid:
            known = _DETAILS[pid] = (ppid, *_details(pid))
        out.append({"pid": pid, "ppid": ppid, "exe": known[1], "create_time": known[2]})
    for pid in [p for p in list(_DETAILS) if p not in alive]:   # (a copy: warm_up may be adding meanwhile)
        _DETAILS.pop(pid, None)
    return out


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
    procs = _scan()
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


_warm_go = threading.Event()
_warm_go.set()
_warm_holds: set = set()


def hold_warm_up(hold: bool, who: str = "egg") -> None:
    """Pause warm_up (True) while something must animate perfectly smoothly, then let it go on (False).
    Each of its steps keeps Python busy for a few ms, which shows as stutter in a full-window animation.
    who: several things may hold it at once (the easter egg, the library's covers); it goes on once none do."""
    _warm_holds.add(who) if hold else _warm_holds.discard(who)
    _warm_go.clear() if _warm_holds else _warm_go.set()


def warm_up() -> None:
    """The first look at every running program's details takes a few seconds on Windows. Done in the background
    when the window opens (remembered in _DETAILS, which _scan then uses), so Read reacts at once and the launch
    watch's scans are quick."""
    snap = _snapshot()
    if snap is None:
        for _ in psutil.process_iter(["exe", "create_time"]):
            time.sleep(0.01)  # a short pause per program, so the window's animations keep running smoothly
            _warm_go.wait(10)  # (held: waits, but never forever)
        return
    for pid, ppid in snap:
        if pid not in _DETAILS:
            _DETAILS.setdefault(pid, (ppid, *_details(pid)))
        time.sleep(0.01)      # a short pause per program, so the window's animations keep running smoothly
        _warm_go.wait(10)     # (held: waits, but never forever)


def already_running(game_dir: Path) -> bool:
    """Is something from this game's folder running right now?"""
    return bool(_related_processes(None, game_dir))


def running_pids(game_dir: Path) -> list[int]:
    """The programs from this game's folder running right now (to force them closed)."""
    return list(_related_processes(None, game_dir))


def force_close_running(game_dir: Path) -> None:
    """End whatever runs from this game's folder (the owner pressed Force Close: anything unsaved is lost)."""
    _force_close(running_pids(game_dir))


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


# how often programs are looked at in a launch's first 3 seconds. A launcher may hand over through a helper that
# lives only ~0.1 s (born after one look and gone before the next, it broke the chain from the launcher to the game:
# the session "ended" at once). A look is cheap now (asked through Windows directly, letting go of Python).
STARTUP_POLL = 0.03


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
            _wait_for_an_exit(list(found), min(poll, STARTUP_POLL) if now - start < 3 else poll, clock)
        elif first_seen is not None:
            # it just closed: look for a hand-over to another process quickly (very quickly while starting)
            time.sleep(STARTUP_POLL if now - start < 3 else 0.25)
        else:
            time.sleep(min(poll, STARTUP_POLL) if now - start < 3 else poll)   # waiting for it to appear
    title = session_title(titles)           # (a title that kept changing: only the part that stayed the same)
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


def play(game: Game, settings: LocalSettings, library: Library, engine: Optional[SyncEngine],
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
    detected save-location candidates for the owner to confirm (see finish_first_play).
    engine None = a solo device: no syncing at all (and so nothing to detect) - the game just runs."""
    machine = settings.machine_name
    mine = game.paths_for(machine)
    if not mine.exe or not Path(mine.exe).is_file():
        return PlayResult(False, "play.no_exe")
    g = Game(**{**game.__dict__})
    if "luna" in without:
        g.use_luna = False
    if "le" in without:
        g.use_le = False
    exe_path = Path(mine.exe).resolve()
    game_dir = exe_path.parent
    # the game itself already open (maybe through Luna) comes first: "Luna is already open" would be the wrong
    # answer then - closing Luna wouldn't close the game (owner, 2026-10-17)
    if not watch and already_running(game_dir):
        lp = settings.luna_path
        if g.use_luna and lp and luna.kind(lp) == "translator" and luna.busy_only_with(lp, str(game_dir)):
            # open through Luna, and Luna busy with nothing else: closing the game closes Luna too (the pop-up says
            # so), then it starts here through Luna - one pop-up, not two in a row
            return PlayResult(False, "play.already_running_luna", retry_without="watch")
        return PlayResult(False, "play.already_running", retry_without="watch")
    problem, plan = launch_plan(g, settings)
    if problem and not watch:
        if problem == "play.luna_running" and luna.idle(settings.luna_path):
            # Luna is open and certainly not busy with a game: it can be closed for you (Close Luna)
            return PlayResult(False, problem, retry_without="close_luna")
        return PlayResult(False, problem, retry_without="luna" if "luna" in problem else "le")
    if mine.save_dir and engine is not None:
        unsafe = save_folder_problem(mine.save_dir, engine.cloud_root)
        if unsafe:
            return PlayResult(False, unsafe)

    detecting = not mine.save_dir and engine is not None
    if not detecting and not watch and engine is not None:   # watching: the running game has its saves loaded
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
        elif engine is not None:
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
            if fresh and t and fresh.auto_title and t != fresh.auto_title:
                # different from last session: a chapter / scene name in the title, or a new name? Only the part
                # both sessions share is the game's name (nothing shared: the new one, e.g. a renamed release)
                t = shared_part([t, fresh.auto_title]) or t
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
