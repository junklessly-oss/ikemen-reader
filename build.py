"""
build.py - package Ikemen Reader into a folder with "Ikemen Reader.exe" (no Python needed to run it).

    py build.py

The result is dist\\Ikemen Reader\\ : copy that whole folder to wherever you keep programs (not inside the
cloud save folder) and start "Ikemen Reader.exe". Settings and saves live in %APPDATA%\\IkemenReader and the
cloud folder, never in the program folder, so a new build can simply replace the old folder.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def version() -> str:
    m = re.search(r'^APP_VERSION = "([\d.]+)"', (HERE / "app.py").read_text("utf-8"), re.M)
    return m.group(1) if m else "1.0.0"


def version_file(v: str) -> Path:
    """Windows' "Details" tab for the .exe (product name, version)."""
    parts = (v.split(".") + ["0", "0", "0"])[:4]
    nums = ", ".join(parts)
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({nums}), prodvers=({nums}), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('ProductName', 'Ikemen Reader'),
      StringStruct('FileDescription', 'Ikemen Reader'),
      StringStruct('ProductVersion', '{v}'),
      StringStruct('FileVersion', '{v}'),
      StringStruct('OriginalFilename', 'Ikemen Reader.exe'),
      StringStruct('InternalName', 'Ikemen Reader')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    out = HERE / "build" / "version_info.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text(text, "utf-8")
    return out


def main() -> None:
    import PyInstaller.__main__
    v = version()
    if not (HERE / "assets" / "Icon.ico").is_file():
        sys.exit("assets/Icon.ico is missing: run  py make_icon.py  first")
    PyInstaller.__main__.run([
        str(HERE / "app.py"),
        "--name", "Ikemen Reader",
        "--windowed",                                   # no console window
        "--noconfirm", "--clean",
        "--icon", str(HERE / "assets" / "Icon.ico"),
        "--add-data", f"{HERE / 'assets'};assets",      # fonts, logo, icons, trash can
        "--version-file", str(version_file(v)),
        "--exclude-module", "tkinter",
        "--distpath", str(HERE / "dist"),
        "--workpath", str(HERE / "build"),
        "--specpath", str(HERE / "build"),
    ])
    # where new versions are downloaded and unpacked before they're swapped in (see updater.py)
    (HERE / "dist" / "Ikemen Reader" / "update").mkdir(exist_ok=True)
    print(f"\nBuilt Ikemen Reader {v}: {HERE / 'dist' / 'Ikemen Reader' / 'Ikemen Reader.exe'}")


if __name__ == "__main__":
    main()
