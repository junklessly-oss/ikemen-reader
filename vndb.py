"""
vndb.py - look up covers and proper titles on VNDB (https://vndb.org), using its free public API.

With safe_only=True, only covers VNDB rates as safe (no sexual content, no violence) are offered
(a Settings switch; off by default). Nothing is applied without the owner confirming, except after a
first play when the game's own window title matches a VNDB title exactly.
"""
from __future__ import annotations

import http.client
import json
import queue
import re
import ssl
import threading
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

try:
    # Check HTTPS certificates the way Windows does. Python's own check can build an outdated
    # certificate chain and wrongly report "certificate has expired" for sites Windows trusts.
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

API = "https://api.vndb.org/kana/vn"
HEADERS = {"Content-Type": "application/json", "User-Agent": "IkemenReader/1.0 (personal VN launcher)"}

# -------------------------------------------------------------------------- fast, reused connections
# Opening a secure connection costs far more than a cover thumbnail itself, so connections are kept open
# and reused (a few per server, shared by every background task). Answers are remembered for the session.
_IDLE: dict[str, "queue.LifoQueue"] = {}
_IDLE_LOCK = threading.Lock()
_CONTEXT = ssl.create_default_context()
_IDLE_MAX_S = 20.0                       # older idle connections aren't trusted
_BYTES: dict[str, bytes] = {}           # url -> picture, for this session
_BYTES_SIZE = [0]
_BYTES_LIMIT = 40 * 1024 * 1024
_ANSWERS: dict[tuple, object] = {}      # (kind, ...) -> search results / cover lists, for this session
_ANSWERS_LOCK = threading.Lock()


def _idle(host: str) -> "queue.LifoQueue":
    with _IDLE_LOCK:
        return _IDLE.setdefault(host, queue.LifoQueue(maxsize=6))


def _drop_idle(pool: "queue.LifoQueue") -> None:
    """Close every kept-open connection to a server (after a pause the server has closed them all)."""
    while True:
        try:
            conn, _ = pool.get_nowait()
        except queue.Empty:
            return
        conn.close()


def _request(method: str, url: str, body: Optional[bytes] = None, headers: Optional[dict] = None,
             timeout: float = 15.0) -> bytes:
    """One HTTPS request over a kept-open connection. Connections left unused for a while are dropped
    (the server will have closed them); if a reused one fails anyway, all the idle ones are dropped and a
    fresh connection is tried, and a fresh one that fails is tried once more after a short pause."""
    u = urllib.parse.urlsplit(url)
    path = u.path + (f"?{u.query}" if u.query else "")
    hdrs = {"User-Agent": HEADERS["User-Agent"], "Connection": "keep-alive", **(headers or {})}
    pool = _idle(u.netloc)
    fresh_failures = 0
    while True:
        conn = None
        try:
            conn, since = pool.get_nowait()
            if time.monotonic() - since > _IDLE_MAX_S:
                conn.close()
                _drop_idle(pool)
                conn = None
        except queue.Empty:
            pass
        fresh = conn is None
        if fresh:
            conn = http.client.HTTPSConnection(u.netloc, timeout=timeout, context=_CONTEXT)
        conn.timeout = timeout
        try:
            conn.request(method, path, body=body, headers=hdrs)
            r = conn.getresponse()
            data = r.read()
        except (http.client.HTTPException, OSError):
            conn.close()
            if not fresh:
                _drop_idle(pool)        # the server closed the idle connections: start over with a fresh one
                continue
            fresh_failures += 1
            if fresh_failures >= 2:
                raise
            time.sleep(0.5)             # e.g. the network was just waking up
            continue
        if r.will_close:
            conn.close()
        else:
            try:
                pool.put_nowait((conn, time.monotonic()))
            except queue.Full:
                conn.close()
        if r.status >= 400:
            raise OSError(f"VNDB answered {r.status} for {url}")
        return data


def fetch(url: str, timeout: float = 20.0) -> bytes:
    """A picture's bytes (remembered for the session, so reopening a list is instant)."""
    data = _BYTES.get(url)
    if data is None:
        data = _request("GET", url, timeout=timeout)
        with _ANSWERS_LOCK:
            if _BYTES_SIZE[0] + len(data) > _BYTES_LIMIT:
                _BYTES.clear()
                _BYTES_SIZE[0] = 0
            _BYTES[url] = data
            _BYTES_SIZE[0] += len(data)
    return data


def _remembered(key: tuple, make):
    with _ANSWERS_LOCK:
        if key in _ANSWERS:
            return _ANSWERS[key]
    value = make()
    with _ANSWERS_LOCK:
        if len(_ANSWERS) > 200:
            _ANSWERS.clear()
        _ANSWERS[key] = value
    return value


@dataclass
class Match:
    vndb_id: str
    title: str            # romanised / main title
    original: str         # title in the original script (usually Japanese), may be ""
    released: str
    image_url: str        # "" if no safe cover
    thumb_url: str = ""   # VNDB's small version of the cover (quick to show in lists)

    @property
    def display_title(self) -> str:
        return self.original or self.title


def _ok(img: Optional[dict], safe_only: bool) -> bool:
    if not (img and img.get("url")):
        return False
    return not safe_only or ((img.get("sexual") or 0) < 0.5 and (img.get("violence") or 0) < 0.5)


def search(query: str, limit: int = 6, timeout: float = 10.0, safe_only: bool = False) -> list[Match]:
    def ask() -> list[Match]:
        data = _post("vn", {
            "filters": ["search", "=", query],
            "fields": "title, alttitle, released, image.url, image.thumbnail, image.sexual, image.violence",
            "sort": "searchrank",
            "results": limit,
        }, timeout)
        out = []
        for vn in data.get("results", []):
            img = vn.get("image")
            ok = _ok(img, safe_only)
            out.append(Match(vn.get("id", ""), vn.get("title") or "", vn.get("alttitle") or "",
                             vn.get("released") or "", img["url"] if ok else "",
                             (img.get("thumbnail") or "") if ok else ""))
        return out
    return list(_remembered(("search", query.strip().casefold(), limit, safe_only), ask))


@dataclass
class Cover:
    url: str
    kind: str             # "main" (the VN's own cover), "pkgfront" (package), "dig" (digital release)
    release: str          # release title ("" for the main cover)
    released: str         # release date
    thumb_url: str = ""   # VNDB's small version (for the picker)


def _post(path: str, body: dict, timeout: float) -> dict:
    data = _request("POST", "https://api.vndb.org/kana/" + path, json.dumps(body).encode(),
                    {"Content-Type": "application/json"}, timeout)
    answer = json.loads(data.decode("utf-8"))
    if not isinstance(answer, dict):
        raise ValueError("unexpected answer from VNDB")
    return answer


def covers(vndb_id: str, timeout: float = 15.0, safe_only: bool = False) -> list[Cover]:
    """Every cover VNDB has for a VN (only safe-rated ones with safe_only): its main cover, then each release's package front and
    digital cover (photos of box contents, backs, discs etc. are left out). Duplicates removed."""
    return list(_remembered(("covers", vndb_id, safe_only), lambda: _covers(vndb_id, timeout, safe_only)))


def _covers(vndb_id: str, timeout: float, safe_only: bool) -> list[Cover]:
    def releases(page: int) -> dict:
        return _post("release", {"filters": ["vn", "=", ["id", "=", vndb_id]],
                                 "fields": "title, released, images.url, images.thumbnail, images.type, images.sexual, "
                                           "images.violence, images.photo",
                                 "sort": "released", "results": 100, "page": page}, timeout)
    # the VN's own cover and its releases' covers are asked for at the same time
    with ThreadPoolExecutor(2) as pool:
        main_q = pool.submit(_post, "vn", {"filters": ["id", "=", vndb_id],
                                           "fields": "image.url, image.thumbnail, image.sexual, image.violence"}, timeout)
        first_q = pool.submit(releases, 1)
        vn, data = main_q.result().get("results", []), first_q.result()
    out: list[Cover] = []
    seen: set[str] = set()
    if vn and _ok(vn[0].get("image"), safe_only):
        img = vn[0]["image"]
        out.append(Cover(img["url"], "main", "", "", img.get("thumbnail") or ""))
        seen.add(img["url"])
    page = 1
    while True:
        for rel in data.get("results", []):
            for im in rel.get("images") or []:
                if im.get("type") in ("pkgfront", "dig") and not im.get("photo") and _ok(im, safe_only) \
                        and im["url"] not in seen:
                    seen.add(im["url"])
                    out.append(Cover(im["url"], im["type"], rel.get("title") or "", rel.get("released") or "",
                                     im.get("thumbnail") or ""))
        if not data.get("more") or page >= 5:
            break
        page += 1
        data = releases(page)
    return out


def download(url: str, dest: str, timeout: float = 20.0) -> str:
    data = fetch(url, timeout)
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold()
    return re.sub(r"[\s\-‐―－–—~～・:：!！?？'\"「」『』【】()（）\[\]]", "", s)


def exact(match: Match, title: str) -> bool:
    """Does this VNDB entry clearly name the same game (ignoring spacing, dashes, width)?"""
    t = _norm(title)
    return bool(t) and t in (_norm(match.title), _norm(match.original))
