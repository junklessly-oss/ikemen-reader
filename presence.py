"""
presence.py - Discord Rich Presence ("Playing Ikemen Reader" on the owner's Discord profile).

Talks to the Discord app on this computer through pypresence, always on its own background thread: Discord being
slow, closed or restarted never holds up the window, Read or a sync. The window only says what should show
(show(...) / clear()); this thread connects when needed, sends changes, retries every 10 s (RETRY_S) while Discord
isn't there, and clears the status when Ikemen Reader closes (stop()).
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Optional

# The Discord application made for Ikemen Reader (its name is what shows as "Playing ..."; the logo images are
# uploaded to it as art assets - Discord can't read pictures from this computer). Not a secret.
CLIENT_ID = "1561206702357872735"            # "Ikemen Reader"
# One application per interface language: Discord shows the application's NAME ("Playing ...") and nothing can
# change it, so Japanese uses a second application named イケメンリーダー (owner, 2026-10-17). Same pictures in both.
CLIENT_IDS = {"en": CLIENT_ID, "ja": "1561208897988071475"}

RETRY_S = 10.0                 # Discord not running: try again this often (owner: picked up within ~10 s)


class Presence:
    rpc_class = None                             # (tests put a stand-in for pypresence's Presence here)

    def __init__(self, client_id: str = CLIENT_ID):
        self.client_id = client_id
        self._want: Optional[dict] = None          # what should show (None = nothing)
        self._sent: Optional[dict] = None          # what Discord was last told
        self._wake = queue.Queue()
        self._rpc = None
        self._next_try = 0.0
        self._stop = False
        self._thread: Optional[threading.Thread] = None

    # -- from the window (any thread)
    def show(self, details: str, state: str = "", start: Optional[float] = None, image: str = "logo",
             image_text: str = "") -> None:
        want = {"details": details, "state": state or None, "start": int(start) if start else None,
                "large_image": image, "large_text": image_text or None}
        if want != self._want:
            self._want = want
            self._kick()

    def use_application(self, client_id: str) -> None:
        """Show the status under this Discord application from now on (the language changed): the old one is let go
        and the new one connected - the status blinks for a moment, the timer carries on."""
        if client_id and client_id != self.client_id:
            self.client_id = client_id
            self._switch = True
            self._kick()

    _switch = False                              # (the application changed: reconnect under the new one)

    def clear(self) -> None:
        if self._want is not None:
            self._want = None
            self._kick()

    def stop(self, wait_s: float = 2.0) -> None:
        """Clear the status and let go of Discord (Ikemen Reader is closing)."""
        self._want = None
        self._stop = True
        self._kick()
        if self._thread is not None:
            self._thread.join(wait_s)

    def _kick(self) -> None:
        if not self.client_id:
            return                                   # (no Discord application yet: nothing to do)
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="discord")
            self._thread.start()
        self._wake.put(1)

    # -- the background thread
    def _run(self) -> None:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)                       # (pypresence needs a loop of its own on this thread)
        try:
            self._loop_until_stopped()
        finally:
            loop.close()

    def _loop_until_stopped(self) -> None:
        while True:
            try:
                self._wake.get(timeout=RETRY_S)
            except queue.Empty:
                pass
            while not self._wake.empty():                # (several changes at once: only the latest matters)
                self._wake.get_nowait()
            self._sync()
            if self._stop:
                self._disconnect()
                return

    def _sync(self) -> None:
        if self._switch:
            self._switch = False
            self._disconnect()                       # (off the old application's name...)
            self._next_try = 0.0                     # (...and straight onto the new one)
        want = self._want
        if want == self._sent and (want is None or self._rpc is not None):
            return
        if want is None:
            if self._rpc is not None:
                try:
                    self._rpc.clear()
                except Exception:                    # noqa: BLE001 - Discord gone: nothing left to clear
                    self._disconnect()
            self._sent = None
            return
        if self._rpc is None and not self._connect():
            return
        try:
            self._rpc.update(**{k: v for k, v in want.items() if v is not None})
            self._sent = want
        except Exception:                            # noqa: BLE001 - Discord closed meanwhile: retry later
            self._disconnect()

    def _connect(self) -> bool:
        if time.monotonic() < self._next_try:
            return False
        try:
            if self.rpc_class is not None:
                RPC = self.rpc_class                 # (tests: a stand-in for Discord)
            else:
                from pypresence import Presence as RPC
            rpc = RPC(self.client_id)
            rpc.connect()
            self._rpc = rpc
            return True
        except Exception:                            # noqa: BLE001 - Discord not running / not installed
            self._rpc = None
            self._next_try = time.monotonic() + RETRY_S
            return False

    def _disconnect(self) -> None:
        rpc, self._rpc, self._sent = self._rpc, None, None
        if rpc is not None:
            try:
                rpc.clear()
                rpc.close()
            except Exception:                        # noqa: BLE001
                pass
