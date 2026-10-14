"""
vn.py - small command-line front end (the window, app.py, is the main way to use Ikemen Reader).

    py vn.py setup --device 1 --cloud "C:\\Users\\You\\OneDrive\\VNSaves" [--le "C:\\LE\\LEProc.exe"] [--luna "...\\LunaTranslator.exe"]
    py vn.py add "Game Title" --exe "D:\\VN\\game.exe" [--save "D:\\VN\\savedata"] [--art cover.jpg]
    py vn.py paths "Game Title" [--exe ...] [--save ...]   (sets paths for THIS device)
    py vn.py list
    py vn.py play "Game Title"   (uses the game's "Launch With Luna / Locale Emulator" options)
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Optional

from i18n import playtime, tr
from launcher import play
from library import (SLOTS, Library, LocalSettings, default_app_dir, load_local_settings,
                     save_local_settings)
from sync_engine import ConflictInfo, SyncEngine


def ask_conflict(info: ConflictInfo) -> Optional[str]:
    def fmt(t):
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "no saves"
    print(f"\nBoth devices changed the saves for '{info.game_id}'.")
    print(f"  This Device's newest save: {fmt(info.local_mtime)}")
    print(f"  Cloud copy's newest save:  {fmt(info.cloud_mtime)}")
    while True:
        a = input("Keep [l]ocal, keep [c]loud, or [s]kip? ").strip().lower()
        if a in ("l", "local"):
            return "local"
        if a in ("c", "cloud"):
            return "cloud"
        if a in ("s", "skip"):
            return None


def need_settings() -> LocalSettings:
    s = load_local_settings()
    if s is None:
        sys.exit('Run setup first:  py vn.py setup --device 1 --cloud "<path to your synced VNSaves folder>"')
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description="Ikemen Reader: VN launcher with cloud save sync")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("setup")
    p.add_argument("--device", required=True, choices=["1", "2"], help="1 on one computer, 2 on the other")
    p.add_argument("--cloud", required=True, help="the synced folder (OneDrive/Drive/Dropbox)")
    p.add_argument("--le", default="", help="path to Locale Emulator's LEProc.exe on this device")
    p.add_argument("--luna", default="", help="path to LunaTranslator.exe (or LunaHook) on this device")

    p = sub.add_parser("add")
    p.add_argument("title")
    p.add_argument("--exe", default="")
    p.add_argument("--save", default="")
    p.add_argument("--art")

    p = sub.add_parser("paths")
    p.add_argument("title")
    p.add_argument("--exe")
    p.add_argument("--save")
    p.add_argument("--art")

    sub.add_parser("list")

    p = sub.add_parser("play")
    p.add_argument("title")

    args = ap.parse_args()

    if args.cmd == "setup":
        old = load_local_settings()
        s = old or LocalSettings(SLOTS[0], args.cloud)
        s.machine_name, s.cloud_root, s.le_path, s.luna_path = SLOTS[int(args.device) - 1], args.cloud, args.le, args.luna
        save_local_settings(s)
        Library(args.cloud).claim_slot(s.machine_name)
        print(f"Saved. This is Device {args.device}, syncing through {args.cloud}")
        return

    settings = need_settings()
    library = Library(settings.cloud_root)

    if args.cmd == "add":
        g = library.add_game(args.title, settings.machine_name, args.exe, args.save, args.art)
        print(f"Added '{g.title}'.")
    elif args.cmd == "paths":
        g = library.find(args.title) or sys.exit(f"No game called '{args.title}'.")
        library.set_paths(g.game_id, settings.machine_name, args.exe, args.save)
        if args.art:
            library.set_artwork(g.game_id, args.art)
        print("Updated.")
    elif args.cmd == "list":
        for g in library.list_games():
            print(f"{g.title}  [{playtime(g.total_playtime)} played]")
            for slot, mp in g.paths.items():
                here = "  <-- This Device" if slot == settings.machine_name else ""
                print(f"    Device {SLOTS.index(slot) + 1 if slot in SLOTS else slot}: "
                      f"exe={mp.exe or '(not set)'}  saves={mp.save_dir or '(not set)'}{here}")
    elif args.cmd == "play":
        g = library.find(args.title) or sys.exit(f"No game called '{args.title}'.")
        engine = SyncEngine(settings.cloud_root, default_app_dir(), computers=library.known_computers)
        result = play(g, settings, library, engine, on_conflict=ask_conflict, status=lambda k: print(tr(k)))
        if not result.launched:
            print(tr(result.message))
        else:
            print(f"Played for {playtime(result.seconds)}.")
            if result.candidates:
                print("Possible save locations (set one with: py vn.py paths ... --save ...):")
                for c in result.candidates:
                    print(f"    {c.path}   ({c.files} changed)")


if __name__ == "__main__":
    main()
