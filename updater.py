"""
updater.py - new versions of Ikemen Reader from its GitHub releases.

    check()            -> the newest release, if it is newer than this copy (None otherwise / offline)
    download(release)  -> the release zip in <program folder>\\update, checked against GitHub's SHA-256
    stage(zip)         -> unpacked to <program folder>\\update\\new (must contain "Ikemen Reader.exe")
    install_on_exit()  -> a small helper waits for Ikemen Reader to close, swaps the new files in (the old
                          ones go to update\\previous, so a failed swap is put back), then starts it again

Only the packaged program updates itself (running from source there is nothing to swap). Only the files the
release contains are replaced (Ikemen Reader.exe, _internal): anything else the owner keeps in the program
folder stays. Settings, the library and saves live elsewhere (%APPDATA%, the cloud folder) and are never
touched. If the program folder can't be written (e.g. inside Program Files), %APPDATA%\\IkemenReader\\update
is used for the download instead, and the swap is still attempted.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

REPO = "junklessly-oss/ikemen-reader"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
EXE = "Ikemen Reader.exe"
FROZEN = bool(getattr(sys, "frozen", False))

try:                                            # check certificates the way Windows does (see vndb.py)
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass


@dataclass
class Release:
    version: str                 # "1.0.1"
    notes: str                   # the release's description (Markdown)
    url: str                     # the zip's download link
    name: str                    # "Ikemen-Reader-1.0.1.zip"
    size: int
    sha256: str = ""             # from GitHub, when it has one ("" = can't check)


def parse_version(text: str) -> tuple[int, ...]:
    """'1.0.1' / 'v1.0.1' / 'Ikemen Reader 1.2' -> (1, 0, 1). () if there's no version in it."""
    m = re.search(r"(\d+(?:\.\d+)*)", text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    if not a or not b:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def program_dir() -> Optional[Path]:
    """The folder holding Ikemen Reader.exe (None when running from source)."""
    return Path(sys.executable).resolve().parent if FROZEN else None


def update_dir(app_dir: Path, program: Optional[Path] = None) -> Path:
    """<program folder>\\update when it can be written, else %APPDATA%\\IkemenReader\\update."""
    program = program if program is not None else program_dir()
    if program is not None:
        d = program / "update"
        try:
            d.mkdir(exist_ok=True)
            probe = d / f".write-test-{os.getpid()}"
            probe.write_bytes(b"")
            probe.unlink()
            return d
        except OSError:
            pass
    d = app_dir / "update"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers={"User-Agent": "IkemenReader-updater",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context())


def pick_release(data: dict, current: str) -> Optional[Release]:
    """The release in GitHub's answer, if it's newer than `current` and has a program zip."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    version = str(data.get("tag_name") or "")
    if not is_newer(version, current):
        return None
    for a in data.get("assets") or []:
        name = str(a.get("name") or "")
        if name.lower().endswith(".zip") and "ikemen" in name.lower():
            digest = str(a.get("digest") or "")
            return Release(".".join(map(str, parse_version(version))), str(data.get("body") or ""),
                           str(a.get("browser_download_url") or ""), name, int(a.get("size") or 0),
                           digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else "")
    return None


def check(current: str) -> Optional[Release]:
    """The newest release if it's newer than this copy. Raises OSError / ValueError when GitHub can't be
    reached or answers oddly (the caller stays quiet about it)."""
    with _get(API) as r:
        data = json.loads(r.read().decode("utf-8"))
    return pick_release(data, current)


def download(release: Release, folder: Path, progress: Callable[[int, int], None] = lambda done, total: None,
             cancelled: Callable[[], bool] = lambda: False) -> Path:
    """The release zip, saved in `folder` (kept there if it's already been downloaded and checks out).
    Checked against GitHub's SHA-256; a damaged or cut-off download is deleted and raises ValueError."""
    dest = folder / release.name
    if dest.is_file() and _matches(dest, release):
        return dest
    part = dest.with_name(dest.name + ".part")
    h, done = hashlib.sha256(), 0
    with _get(release.url, timeout=30) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or release.size or 0)
        while True:
            if cancelled():
                f.close()
                part.unlink(missing_ok=True)
                raise InterruptedError("cancelled")
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            progress(done, total)
    if (release.size and done != release.size) or (release.sha256 and h.hexdigest() != release.sha256):
        part.unlink(missing_ok=True)
        raise ValueError("update.damaged")
    part.replace(dest)
    return dest


def _matches(path: Path, release: Release) -> bool:
    if release.size and path.stat().st_size != release.size:
        return False
    if not release.sha256:
        return True
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest() == release.sha256


def stage(zip_path: Path, folder: Path) -> Path:
    """Unpack the release into folder\\new and return the folder that holds Ikemen Reader.exe. Refuses zips
    with paths that would land outside it, and zips without the program (ValueError)."""
    new = folder / "new"
    if new.exists():
        shutil.rmtree(new, ignore_errors=True)
    new.mkdir(parents=True)
    root = new.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            target = (new / info.filename).resolve()
            if root != target and root not in target.parents:
                raise ValueError("update.damaged")
        z.extractall(new)
    hits = [p.parent for p in new.rglob(EXE)]
    if not hits:
        raise ValueError("update.damaged")
    return min(hits, key=lambda p: len(p.parts))          # the top one (Ikemen Reader\\Ikemen Reader.exe)


def staged(folder: Path) -> Optional[Path]:
    """A release already unpacked and waiting to be installed (folder\\new\\...\\Ikemen Reader.exe)."""
    new = folder / "new"
    hits = list(new.rglob(EXE)) if new.is_dir() else []
    return min((p.parent for p in hits), key=lambda p: len(p.parts)) if hits else None


def _ps(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def helper_script(program: Path, new: Path, folder: Path, pid: int, restart: bool) -> str:
    """PowerShell that waits for Ikemen Reader (pid) to close, then moves each item of `new` into the program
    folder; whatever it replaces goes to folder\\previous first. Any failure puts the old files back. Then
    (restart=True) starts Ikemen Reader again and tidies up the unpacked files."""
    return f"""$ErrorActionPreference = 'Stop'
$program = {_ps(program)}
$new = {_ps(new)}
$previous = {_ps(folder / 'previous')}
$log = {_ps(folder / 'update.log')}
function Note($m) {{ Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + ' ' + $m) -Encoding UTF8 }}
function Move-Patiently($from, $to) {{
  for ($i = 0; $i -lt 40; $i++) {{
    try {{ Move-Item -LiteralPath $from -Destination $to -Force; return }} catch {{ Start-Sleep -Milliseconds 250 }}
  }}
  Move-Item -LiteralPath $from -Destination $to -Force
}}
try {{ Wait-Process -Id {int(pid)} -Timeout 120 -ErrorAction SilentlyContinue }} catch {{}}
Start-Sleep -Milliseconds 400
if (Test-Path -LiteralPath $previous) {{ Remove-Item -LiteralPath $previous -Recurse -Force -ErrorAction SilentlyContinue }}
New-Item -ItemType Directory -Path $previous -Force | Out-Null
$moved = @()
$placed = @()
$ok = $true
try {{
  foreach ($item in Get-ChildItem -LiteralPath $new -Force) {{
    if ($item.Name -eq 'update') {{ continue }}
    $target = Join-Path $program $item.Name
    if (Test-Path -LiteralPath $target) {{
      Move-Patiently $target (Join-Path $previous $item.Name)
      $moved += $item.Name
    }}
    Move-Patiently $item.FullName $target
    $placed += $item.Name
  }}
  Note 'updated'
}} catch {{
  $ok = $false
  Note ('update failed, putting the old version back: ' + $_)
  foreach ($name in $placed) {{
    $t = Join-Path $program $name
    if ($moved -contains $name) {{ Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction SilentlyContinue }}
  }}
  foreach ($name in $moved) {{
    $t = Join-Path $program $name
    if (-not (Test-Path -LiteralPath $t)) {{ Move-Item -LiteralPath (Join-Path $previous $name) -Destination $t -Force }}
  }}
}}
if ($ok) {{
  Remove-Item -LiteralPath {_ps(folder / 'new')} -Recurse -Force -ErrorAction SilentlyContinue
  Get-ChildItem -LiteralPath {_ps(folder)} -Filter '*.zip' | Remove-Item -Force -ErrorAction SilentlyContinue
}}
{"Start-Process -FilePath (Join-Path $program " + _ps(EXE) + ")" if restart else ""}
"""


def install_on_exit(folder: Path, new: Path, restart: bool, program: Optional[Path] = None,
                    pid: Optional[int] = None, run: bool = True) -> Path:
    """Write the helper next to the update and start it (hidden); it waits until this process has ended.
    Returns the script's path."""
    program = program if program is not None else program_dir()
    if program is None:
        raise ValueError("update.not_packaged")
    script = folder / "install-update.ps1"
    # UTF-8 with a BOM: Windows PowerShell 5 reads it correctly (Japanese folder names included)
    script.write_text(helper_script(program, new, folder, pid or os.getpid(), restart), "utf-8-sig")
    if run:
        import outside
        # a hidden console (not DETACHED_PROCESS: PowerShell quits at once without any console). It keeps running
        # after Ikemen Reader exits.
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        outside.popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                       "-File", str(script)], cwd=str(folder), creationflags=flags)
    return script


def note_version(app_dir: Path, current: str) -> Optional[str]:
    """Remember which version ran last; returns the previous one if this copy is newer (just updated)."""
    f = app_dir / "last_version.txt"
    try:
        before = f.read_text("utf-8").strip()
    except OSError:
        before = ""
    try:
        if before != current:
            f.write_text(current, "utf-8")
    except OSError:
        pass
    return before if before and is_newer(current, before) else None
