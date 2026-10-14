"""
regsync.py - export / import one registry key, for the few games that keep saves in the registry.

Safety rules (enforced here, not just in the UI):
  * only HKEY_CURRENT_USER (this Windows user), never anything machine-wide
  * the key must be at least two levels below HKCU\\Software (e.g. Software\\Maker\\Game)
  * never a Windows / Microsoft / system area

Exports are plain data ({"values": {...}, "keys": {...}}), serialised deterministically so the
same registry contents always give the same fingerprint.
"""
from __future__ import annotations

import json
import sys
from typing import Optional

if sys.platform == "win32":
    import winreg
else:  # the rest of the program still works elsewhere; registry sync simply isn't available
    winreg = None

_ROOT_NAMES = ("HKEY_CURRENT_USER\\", "HKCU\\")
_FORBIDDEN = {"microsoft", "classes", "policies", "wow6432node", "clients", "appdatalow",
              "registeredapplications", "intel", "nvidia corporation", "amd", "google"}


class RegistryKeyNotAllowed(ValueError):
    pass


def normalize(key: str) -> str:
    """'HKCU\\Software\\Maker\\Game' / 'HKEY_CURRENT_USER\\Software\\...' -> 'Software\\Maker\\Game'.
    Raises RegistryKeyNotAllowed for anything outside the safe area."""
    k = key.strip().replace("/", "\\").strip("\\")
    for root in _ROOT_NAMES:
        if k.upper().startswith(root.upper()):
            k = k[len(root):]
            break
    else:
        raise RegistryKeyNotAllowed(key)
    parts = [p for p in k.split("\\") if p]
    if len(parts) < 3 or parts[0].lower() != "software" or parts[1].lower() in _FORBIDDEN:
        raise RegistryKeyNotAllowed(key)
    return "\\".join(parts)


def display(key: str) -> str:
    return "HKEY_CURRENT_USER\\" + normalize(key)


def _value_to_json(kind: int, data):
    if isinstance(data, bytes):
        return [kind, data.hex()]
    return [kind, data]


def _value_from_json(item):
    kind, data = item
    # text values stay text; everything else that arrives as a string was raw bytes, stored as hex
    if isinstance(data, str) and kind not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
        return kind, bytes.fromhex(data)
    return kind, data


def _valid(data) -> bool:
    """Is this a well-formed export? (Checked before anything in the registry is touched.)"""
    if not isinstance(data, dict) or not isinstance(data.get("values", {}), dict) \
            or not isinstance(data.get("keys", {}), dict):
        return False
    for item in data.get("values", {}).values():
        if not (isinstance(item, list) and len(item) == 2 and isinstance(item[0], int)
                and isinstance(item[1], (str, int, list, type(None)))):
            return False
        if isinstance(item[1], str) and item[0] not in (1, 2):      # hex for raw bytes
            try:
                bytes.fromhex(item[1])
            except ValueError:
                return False
    return all(_valid(sub) for sub in data.get("keys", {}).values())


def _read(h) -> dict:
    values, keys = {}, {}
    n_keys, n_values, _ = winreg.QueryInfoKey(h)
    for i in range(n_values):
        name, data, kind = winreg.EnumValue(h, i)
        values[name] = _value_to_json(kind, data)
    for i in range(n_keys):
        sub = winreg.EnumKey(h, i)
        with winreg.OpenKey(h, sub) as sh:
            keys[sub] = _read(sh)
    return {"values": values, "keys": keys}


def export_key(key: str) -> Optional[dict]:
    """The key's contents, or None if it doesn't exist (or registry isn't available)."""
    if winreg is None:
        return None
    path = normalize(key)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as h:
            return _read(h)
    except FileNotFoundError:
        return None


def _delete_tree(parent, name: str) -> None:
    with winreg.OpenKey(parent, name, 0, winreg.KEY_ALL_ACCESS) as h:
        while True:
            try:
                sub = winreg.EnumKey(h, 0)
            except OSError:
                break
            _delete_tree(h, sub)
    winreg.DeleteKey(parent, name)


def _write(h, data: dict) -> None:
    for name, item in data.get("values", {}).items():
        kind, value = _value_from_json(item)
        winreg.SetValueEx(h, name, 0, kind, value)
    for sub, sdata in data.get("keys", {}).items():
        with winreg.CreateKey(h, sub) as sh:
            _write(sh, sdata)


def import_key(key: str, data: dict) -> None:
    """Replace the key's contents with data (the caller backs up the old contents first)."""
    if winreg is None:
        return
    if not _valid(data):
        raise ValueError("damaged registry export")     # leave the registry exactly as it is
    path = normalize(key)
    parent_path, _, name = path.rpartition("\\")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, parent_path) as parent:
        try:
            _delete_tree(parent, name)
        except FileNotFoundError:
            pass
        with winreg.CreateKey(parent, name) as h:
            _write(h, data)


def dumps(data: dict) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, indent=1)


def loads(text: str) -> dict:
    return json.loads(text)
