# Ikemen Reader (イケメンリーダー) — project brief

A minimalist, Steam-library-style launcher for visual novels that keeps save files in sync
between two Windows machines: **PC (Windows 10)** and **Laptop (Windows 11)**.
The owner is a Japanese learner who reads VNs for immersion and switches machines often.
Both machines use a Japanese system locale. (Formerly "VN Launcher".)

## How sync works
- No cloud APIs. Saves are mirrored into a folder synced by a desktop client. Owner switched from OneDrive (its app
  wouldn't sign in: error 0x8004de80, custom-domain account) to Google Drive for desktop on 2026-10-14:
  cloud folder `G:\My Drive\VNSaves` (streaming mode; VNSaves should be "Available offline"). The old
  `C:\Users\Jaden\OneDrive\VNSaves` is no longer used; a verified safety copy is in `..\save-backups\`.
- `sync_engine.py`: three-way fingerprint compare (local / cloud / last-synced base, base stored locally
  per device). Pull, push, or CONFLICT. On conflict nothing changes until the user picks a side.
  Anything overwritten is backed up locally (last 10 kept) and can be restored. Atomic writes; junk files
  ignored; deletions propagate. Refuses blank/relative save folders.
- Cloud side only: sync-app duplicates are ignored (Dropbox "conflicted copy"/競合コピー, OneDrive
  "name-COMPUTER.ext" for known computer names, Google Drive "name (1).ext" when "name.ext" exists).
- Registry saves: optional per-game HKCU key (only `HKCU\Software\<maker>\<game>`, system areas refused),
  exported deterministically as a virtual `__registry__.json` that travels with the save files.
- On launch: sync down → run game (optionally through Luna / Locale Emulator) → wait until it has
  *really* exited → sync up → playtime + last played; window title → cleaned title (unless user-locked).
- No save folder yet on this device → play without sync, then detect the save location (files/registry
  changed during the session), the owner confirms, then first sync.
- On app start: games with "this device has newer saves" are uploaded if safe (`push_if_safe`).

## Files
- `app.py` — PySide6 UI (`py app.py`). Palette + sizes/timings at the top. Play runs on a QThread;
  conflicts asked via a blocking signal. Drag-and-drop .exe/.lnk to add.
- `i18n.py` — ALL interface text, English + Japanese side by side (`tr(key, **kw)`).
- `sync_engine.py`, `regsync.py` (registry export/import + safety rules), `library.py` (settings, games,
  device slots, sorting), `launcher.py` (run/watch, window titles, Luna/LE, first-play detection),
  `detect.py` (save-location detection), `luna.py`, `vndb.py`, `titles.py` (title cleaning), `lnk.py`
  (shortcut reader; follows Luna `--Exec` shortcuts to the game), `vn.py` (small CLI), `outside.py` (starts
  every outside program: games, Luna, LE, admin prompt).
- `assets/`: `Logo.png`, `Icon.png`, `Font.otf` (Hiragino Maru Gothic ProN W4, owner-supplied; the owner has legal permission for all
  bundled fonts — don't raise licensing), `ZenMaruGothic-Regular.ttf` (+OFL, fallback), `check.svg`.
- LocalSettings: add new fields at the END (SettingsDialog._save fills earlier ones by position;
  test_features.SettingsSave guards this).
- Tests: `test_sync_engine.py`, `test_launcher.py`, `test_features.py` — 71 tests, all pass on the PC
  (Python 3.14, `py -m unittest`). (Now 94 tests.) Process/window/registry tests run for real on Windows.
- On this PC `python` isn't on PATH; use `py`.
- Local data: `%APPDATA%\IkemenReader` (settings.json, state/, backups/, registry/, downloads/). On first
  run it is copied from the old `%APPDATA%\VNLauncher` (old folder left as a safety copy).

## Decisions (owner-approved)
- Device naming: Settings "This is: Device 1 / Device 2" (1 = PC, 2 = laptop). Internally Device 1 = "PC"
  key, Device 2 = "Laptop" key. UI elsewhere says only **This Device** (top, Browse) / **Other Device**
  (greyed, text only). Safeguard: `<cloud>/devices.json` records which computer uses each slot; Settings
  warns if another computer already has it.
- Name: window title "Ikemen Reader" (EN) / イケメンリーダー (JA); `.exe` always `Ikemen Reader.exe`.
- Folders (phone-style): `<cloud>/folders.json` {id: {name}} + `Game.folder`. One folder per game; a folder
  emptied by this device is removed. Grid tile = 2x2 mosaic of its covers (name + count on hover), light =
  most important state inside. Make one by dragging a game onto another; drag onto a folder to add;
  right-click a game → Add to Folder (existing / New Folder...) or Remove From Folder; right-click a folder →
  Open / Rename... / Remove Folder (games go back). Double-click/Enter/the Open button opens it; "‹ Library"
  (also Esc/Backspace) goes back, and dropping a game on it takes it out. Search/status filter show games
  from inside folders directly.
- Settings layout: ... Luna, then "Accessibility" (Pulsing Sync Lights, Launch Program on Startup, VNDB auto
  covers, Safe Covers Only), then "Display" with segmented buttons Light | Dark | Custom (selected one stays
  highlighted; Light/Dark preview at once; Settings Cancel always restores the original look).
- Themes: LocalSettings.theme ("dark"/"light"/"custom") + custom_colors {role: "#RRGGBB"} (per device; old
  light_mode=true migrates to "light"). app.py: DARK, LIGHT (= DARK mirrored), BASE_LIGHTS, LOGO_ORIGINAL;
  theme_colors()/apply_theme() fill COLORS/LIGHTS/LOGO in place; restyle_all() re-applies the style sheet and
  calls each window's restyle(). Tick/chevron SVGs are generated per colour into %APPDATA%\IkemenReader\theme.
  Custom → ThemeDialog: THEME_GROUPS rows (swatch + hex), Start From Dark/Light, live preview debounced 120 ms,
  low-contrast warning (WCAG < 3:1), Qt QColorDialog (Japanese via qtbase_ja.qm, set_qt_language()).
  Logo/star recolour: themed_image() + _recolour() (greyscale → 256-entry colour table → original alpha;
  on-screen identical to per-pixel within 1/255, ~0.4 s); used by top-bar logo, StarButtons, window icon.
- Library background (Settings → Theme → "Library Background..."): BackgroundDialog (Choose/Change Image,
  Remove, Opacity slider shown once an image is chosen, preview box + live window preview). Per device:
  LocalSettings.bg_image (our copy in %APPDATA%\IkemenReader\background\) + bg_opacity. Drawn by Backdrop
  (central widget) and LibraryGrid (draws its own backdrop patch, opaque viewport, full redraw on scroll so
  the picture stays fixed). blended_backdrop() pre-mixes picture + bg colour at the opacity (cached).
  Crop like covers: bg_zoom/bg_x/bg_y; BgPreview (shaped like the library area) drag/wheel/double-click
  reset + Zoom slider under Opacity; crop_rect(aspect=...) shared with covers; window preview throttled 60 ms.
  Save in the Library Background / Custom Theme windows saves immediately (SettingsDialog._persist).
- Fonts: Settings > Font (under Language) has English / 日本語 buttons -> FontDialog (drop-down of FONTS =
  built-in + user-added, each in its own face; Add Font copies the file to %APPDATA%\IkemenReader\fonts).
  LocalSettings font_en/font_ja; ui_families() puts the current language's font first, the other second
  (fallback for missing characters); build_style uses it. Saves immediately like the theme windows.
- Performance: NEVER touch the cloud drive while painting. Google Drive's G: answers a file stat in ~0.3 ms when idle
  and can stall for seconds while busy; the delegate used to stat every cover (CoverCache.get + artwork_path) on
  every frame (~580/s with 20 covers) -> lag at launch, laggy drags, dropped drags, a 7 s delay on the sort
  drop-down's tick. Now MainWindow.art() + CoverCache._mtimes remember them until refresh() (_forget_files).
  Drops/rearranging update memory and write once (_reshow(changed ids) / in-memory order), no full reload.
- Animation pass (2026-10-15, measured under the real event loop; frames are a steady 16.0 ms there):
  CoverCache.prepare/accept: MainWindow._prefetch_covers decodes covers (shown order, then the rest of each
  folder) + checks artwork files in a background task after every _rebuild -> first scroll 84 -> 5 ms, first
  folder open 180 -> 20-30 ms; it starts PREFETCH_QUIET_MS (500 ms) after the last rebuild (starting at once
  slowed opening a 600-game library by 0.75 s), and waiting covers + drawn covers stay within MAX_COVERS (_trim_ready).
  LibraryGrid.visible_range() (binary search) for the per-frame loops (_tick,
  _drop_spot): tick cost no longer grows with the library. DragGhost shrinks its window after the lift.
  Trash/bookmark icons brighten with their growth (no opacity jump); Read pulse settles by time.
  Additions: search/filter changes glide like sorts (_apply_filter: _positions incl. glide offsets, new covers
  fade in = appearing/appear_fade, removed ones fade out from a _grid_snapshot = _leaving/leaving_frame, ~8 ms
  per keystroke); bookmarks fade on toggle (bookmark_alpha); a game's light cross-fades when its colour
  changes (CardDelegate._light_fx, LIGHT_FADE_S 0.45, only while lights pulse). Bench scripts must call
  app.wait_for_tasks() before exiting (else exit code 127 at shutdown - not a program bug).
- Performance (older): the animation timer only redraws what moves (hovered tile + shadow; small squares around
  each visible light), covers without art are cached pixmaps (CardDelegate._placeholder). Measured ~62 fps,
  ~2 ms/frame with 120 games + background. warm_up() sleeps 10 ms per process so startup stays smooth
  (2 → 35 fps during the first seconds). Theme editor previews throttled to every 80 ms while dragging.
- Look: monochrome; #3B3B3B main, #1C1C1C bands; sync lights are the only colour (bottom-right of each
  cover, breathing in step; calm when fine, quicker while syncing, steady for conflict; on/off in
  Settings). Placeholder covers: one identical grey gradient. Hover: lift 6 px, +3%, soft shadow, stays
  still; covers with art show no text until hover (title + playtime fade in, Steam style).
- Top bar: logo, search, sort (recent / hours / title, kana-aware), status filter, Hide/Show Statistics, then the
  status: only "Synced" / "Syncing" / "Error" (JA 同期済み / 同期中 / エラー; owner wants it this simple; Error =
  conflict, red, tooltip explains, click cycles conflicts), + Add game, Settings.
- Bottom bar: Read (was Play; JA 読む, 14pt; "Now Reading"/読書中 while running; i18n bar.play), title, total playtime only, "Last played: <date>" (no device), long status line,
  reading status (Reading / Finished / Plan to read / Dropped), Open save folder, Edit, trash button
  (TrashButton, assets/Trash.png recoloured like the star; click = remove selected game, or drop a game
  on it; bare icon, no button box; hover = rise + grow 1.08 + one 0.5 s wiggle; game held over it = grow
  1.2 + steady rocking around its base; timer stops when still; disabled for folders / while a game runs).
- Dragging games is our own (no QDrag): the grid viewport keeps the mouse; DragGhost (translucent,
  click-through tool window) follows it; _drag_hit() finds trash / "‹ Library" / grid target; release drops,
  Esc cancels (EscWatcher, app-wide only during a drag). Over the trash the cover fades 0.85 → 0.1.
  The picked-up game fades in place (drag_dim, ~0.12 s; CardDelegate._fade_into_background draws the
  backdrop back over it at 75%) and fades back on drop / Esc.
  Files dragged in from Explorer still use Qt's drag events.
- Ikemen Sync (devsync.py): each device shares its personal settings (devsync.SHARED + background picture
  + added font files) into `<cloud>/device_settings/<slot>/` on start and after every settings change
  (MainWindow.share_settings, background thread). Settings → "Device Settings Sync" (row "Font | Ikemen Sync",
  both centred) asks "Copy Settings From Device N?" (lists what changes / what stays), then applies + saves at
  once. Never shared: device slot, cloud folder, LE/Luna paths, startup. Missing font file → keeps own font.
- Settings row under Language: Font | Ikemen Sync | About (three centred columns). About → "Help and
  Guidance" opens GuideDialog (guide.py: GUIDE[en/ja] sections; keep it accurate when features change).
- Settings window is ~888 px tall (903 in Japanese). Owner chose: scroll when it doesn't fit. Contents sit in a
  QScrollArea (#settings_scroll); Cancel/Save are fixed below; _fit_height() sizes to content, capped to the
  screen's available height (_screen_room), and keeps the window on screen.
- Easter egg (keep out of the guide): click the top-bar logo → EasterEgg overlay darkens the whole window
  (alpha 175, fades) with two WobbleBoxes (subclass TrashButton, same motion constants, centre pivot):
  "Ver. {APP_VERSION}" on top nudges once on hover, "Easter Egg ^_^" (JA イースターエッグ ^_^, i18n egg.title)
  below rocks while hovered; neither clickable. Settings "Anki (Optional)" (under Luna; LocalSettings.anki_path,
  per device, not shared; hint "You may be rewarded for hunting...") adds a clickable box ABOVE the version:
  "Anki" (JA 暗記) = AnkiBox (same footprint as a WobbleBox; on hover the box beats 10 % every 1.2 s, text
  still, like the Read button but stronger); closes the egg and, if Anki is already open, brings its window
  forward (outside.show_anki / anki_window: visible unowned window of anki.exe itself or of a python(w) under an
  "anki" path running aqt = the new uv launcher's AnkiProgramFiles; ~17 ms), else starts it via outside.popen.
  (Investigated 2026-10-15 "slow Anki": not us - starting anki.exe takes 0.03 s, same in any environment; a second
  copy needs ~2 s to hand over; a fresh start is Anki itself: 186 MB collection + 11 add-ons incl. AnkiMorphs.)
  Hovering "Easter Egg ^_^" rocks every library cover, each OUT OF STEP (phase + 0.9-1.1x speed from crc32 of
  its id). MainWindow.rumble = amplitude (deg) + rumble_t (one clock per frame); EasterEgg._rumble_step
  (PreciseTimer) scales it by the box's growth so it eases in/out. CardDelegate.paint while rumbling: each tile
  drawn upright once into CardDelegate._tiles (lights skipped via _skip_light), then only rotated; lights drawn
  live on top; covers fully out of the viewport aren't drawn while rocking (their corners swung into view). ~7.8 ms/frame vs ~5 ms still (1280x800, 40 covers; was 11 ms drawing tiles rotated).
  warm_up (psutil's first look, holds the GIL ~10 ms per step) is paused while the covers rock
  (launcher.hold_warm_up): with it 45 fps + stutter near startup, held 125 fps steady.
  Egg boxes react only over the box as drawn (BoxZone mixin: mouse tracking, _zone() / _arrive() / _depart(),
  4 px leeway once inside); clicks in their spare margin pass through to the overlay (= dismiss).
- Settings fits a 1080p screen (needs 990 of 1000 px): headings inside Settings have padding-top 5 px
  (QDialog#settings_dialog QLabel#section; 8 px elsewhere), 6 px after each divider keeps the gaps equal.
  Click elsewhere / Esc (close_folder checks it first) dismisses. APP_VERSION = "1.0.2" in app.py (build.py reads it).
- Removing a game: right-click "Remove From Library", the trash button, or Edit's red button — all use
  confirm_remove(): "Are You Sure?" heading, Cancel (default, also on close) | Confirm. Saves untouched.
- Buttons never end in "..." (menus still may); all buttons size to their text.
- Text pass 2026-10-14: every EN/JA string in i18n.py + guide.py reviewed. Owner-chosen wording kept as is
  (Ikemen Sync messages, Are You Sure?/Confirm, Built In/User Added, Launch Program on Startup, status names).
- Per-game checkboxes "Launch With Luna" / "Launch With Locale Emulator" (replace the old per-launch LE
  button). LunaTranslator: `LunaTranslator.exe --Exec <uid>` (uid looked up by exe path in
  `userconfig\savegamedata_*.json`, per device, never stored); Luna's own LE setting then applies.
  Standalone LunaHook: opened alongside, game launched normally. If Luna is already open, ask the owner
  to close it (a second --Exec instance would open a second Luna). `_admin` Luna builds refused.
  Owner's Luna: `C:\Users\Jaden\Desktop\LunaHook\LunaTranslator\LunaTranslator.exe`. No "import from Luna".
- Titles: window title, cleaned of trailing version/edition tags (EN + JA); typed or VNDB-chosen title is
  locked; "Automatic" button unlocks. VNDB toggle in Settings: suggestion banner when adding; after a first
  play, exact-title match with a cover is applied automatically. Settings "Safe Covers Only" (default
  OFF, owner's choice) hides covers VNDB rates sexual/violent. Cover picker lists the VN's main cover +
  every release's package front / digital cover (deduplicated).
- Library is one continuous grid: no pages, no game limit. Cover memory is capped (LRU of 120 drawn
  covers; images decoded at the size needed). Tested 600 games with 1500x2100 covers: opens in ~2 s,
  ~190 MB peak.
- Adding: drag-and-drop or "+ Add game" (file picker) → game settings dialog pre-filled.
- Playtime stored per device, shown only as the total.
- No "friends version".

## Hardening (stress test, 2026-10-13)
- `save_folder_problem()` (sync_engine) refuses: blank/relative paths, files, whole drives, broad folders
  (home, Documents, Desktop, AppData, ProgramData, Program Files, OneDrive root, ...), and anything that is,
  contains, or sits inside the cloud folder. Enforced in the engine, play(), game settings, detection dialog.
  Game settings also warns when another game uses the same/nested save folder on This Device.
- Copies retry ~3 s on PermissionError (games holding files briefly), clear read-only flags; sync errors
  become "sync.failed" (playtime still recorded; startup upload retries). Closing is blocked while syncing.
- Library tolerates damaged/half-synced files: one bad game JSON is skipped, wrong-typed fields fall back to
  defaults, duplicate game ids (sync-app copies) are ignored; devices.json/folders.json/settings validated.
- Game ids: reserved Windows names prefixed with "_", max 80 chars, "game" if empty.
- Launch: processes followed through parent ids (launcher that starts the game from a sibling folder and
  exits), fast polling for the first 3 s, sleep/hibernate gaps not counted as playtime, "already running"
  detection with a "Wait for It to Close" mode (was "Watch It") (no pre-sync; syncs after it closes). psutil is slow on its first look at
  all processes (~4 s here) → `warm_up()` runs in the background when the window opens.
- UI: drop onto itself / vanished games ignored; window min height 520; message dialogs wrap (no QMessageBox).

## Full audit (2026-10-14) — what was hardened, keep it that way
- sync_engine: state file atomic + validated (`_load_state`), remembers synced file names (`files`) so a game's own
  "save (1).dat" / "x-COMPUTER.sav" is never taken for a sync-app duplicate (was a delete loop); only folders our
  deletions emptied are removed; content hashes cached by (path, size, mtime_ns); backups ordered by
  `_backup_order` (time, then number; "-10" sorted before "-2" and the newest got pruned); same-second backups
  numbered in creation order; registry export validated BEFORE any file/registry change; per-game RLock shared by
  all engines (`_LOCKS`, keyed by cloud folder + game) so startup upload / Play / restore never overlap.
- regsync: every non-text value round-trips (hex -> bytes for all kinds but SZ/EXPAND_SZ); `_valid()` before import.
- launcher: instant-exit games (psutil.Process may fail) handled; sync errors incl. ValueError -> "sync.failed";
  after a session each step (sync / playtime / title) fails independently.
- Admin games: Popen error 740 -> launcher._start_elevated() (ShellExecuteExW "runas" = Windows' permission
  prompt), then tracked by pid + game folder as usual; No -> ElevationDeclined -> "play.admin_declined".
  Mocked in tests; the real prompt is untested (needs the owner to click it).
- lnk: unterminated paths can't loop forever. luna: unknown game-list shapes -> "not found".
- library: `_atomic_write` retries ~3 s on PermissionError; artwork must be a plain file name (no ../); folders /
  devices files of any shape; NaN/inf in settings or games clamped; `_free_id` leaves room for " (n)" (long titles
  looped forever).
- devsync: shared names must be plain file names; colours validated; one publish at a time; unique temp names.
- app: `report_failure` / `@guarded` on every action that writes (message instead of silently doing nothing);
  `install_error_net()` logs anything uncaught to %APPDATA%\IkemenReader\error.log + one message; single instance
  (QLocalServer "IkemenReader-<user>": a second launch raises the first window); background tasks are parentless
  (`_LIVE_TASKS`) and drop results for closed windows (closing never freezes / aborts); exit waits <= 3 s;
  missing background picture not retried every frame; damaged cover images rejected.
- Round 2 (2026-10-14): close detection ~2 s (watch the game's own pids, rescan 4x/s, grace 2 s; was ~5 s) and the
  last stretch before exit now counts as playtime; "Synced" group balanced (+9 px after the text); message()
  is plain text unless rich=True and titles in <b>{title}</b> are html-escaped; titles shown as plain text;
  stuck-drag watchdog (_check_drag_button); adding an .exe the other device already added offers
  "Set Up on This Device" (_added_on_other_device: same exe file name or title) instead of a duplicate.
- VNDB (2026-10-14): vndb._request keeps HTTPS connections open (pool per host), fetch()/search()/covers()
  remembered per session, thumbnails (image.thumbnail / images.thumbnail) for previews, covers() asks VN +
  releases in parallel; VndbDialog prefetches the selected result's covers and searches as you type (300 ms
  pause, >= 2 letters or 1 Japanese character; `_gen` drops late answers). Playtime: launcher._ExactClock
  (GetProcessTimes + QueryUnbiasedInterruptTime(Precise)), stored to the millisecond (float seconds).
- Grid layout: covers start top-left (viewport margins 14/8/10/8: left edge = 29 px = the gap between covers);
  the owner prefers it to a centred grid.
- Hide/Show Statistics button (top bar, right of the status filter; LocalSettings.show_stats, default True, shared by
  Ikemen Sync, kept by Settings Save): hides playtime + last played on covers and the bottom bar (status stays).
  The sort/filter drop-downs are sized from Qt's own edit-field width (SC_ComboBoxEditField) + longest item + 6. Settings button: "Custom Library Background".
- One unexplained test error on 2026-10-14 (not reproduced in 4 further runs; likely a timing-sensitive
  process test in test_launcher). If it recurs, run `py -m unittest -v` to see which one.
- Live refresh (done): MainWindow._live_poll every 4 s runs Library.change_signature() in the background
  (games/, artwork/, saves/ (deep) and folders.json: names, sizes, mtimes); a change not made here refreshes
  the library (waits while dragging, playing/syncing, or a menu / modal window is open) with a short note.
  refresh() stores the signature after our own changes; _rebuild keeps scroll position + selection.
- Tests: 101 (13 sync attacks, registry/damaged-file/long-title/shortcut attacks). Random UI stress test (700-800
  actions incl. 162 drags, odd search text, theme/language switches) ran clean.

## Luna fix (2026-10-14)
- In the packaged .exe, Luna quit at once (exit code 1) and the app sat in "playing" for 90 s with no way out.
  Cause: PyInstaller's SetDllDirectory(_internal) is inherited by child programs, so Luna loaded our
  python314.dll. `outside.popen()` / `outside.normal_dll_search()` reset it for the moment of starting (then put
  it back) and drop _PYI*/QT_PLUGIN_PATH/QML2_IMPORT_PATH/_MEIPASS-in-PATH. Running from source is unaffected,
  so only a packaged test shows this. Verified with a packaged probe: Luna --Exec starts 大穢 again.
- Phase "starting" until the game appears (bottom line "Starting the Game..."), then "playing" ("Now Reading..." /
  読書中…). Luna exiting non-zero before the game appears -> play.luna_failed at once (offers Read Without Luna).
- Stop button (owner's request): while starting/playing the Read button says Stop (JA 終了). Before the game
  appears = stop waiting (play.cancelled, no dialog). Once running = WM_CLOSE to the game's visible windows
  (launcher._ask_to_close, like its X; the game may ask "Quit?"); still open after STOP_PATIENCE_MS (6 s) ->
  "Force Close / Keep Open" (launcher._force_close = kill; Keep Open re-enables Stop). Saves sync as usual after.
  Verified on 大穢: closes on the first request.
- Bottom bar while a game runs (MainWindow.bar_game / _bar_shows_running): nothing or the running game selected
  -> running game (Now Reading, Stop); another game selected -> that game (Read greyed, "Another game is running");
  bar buttons act on bar_game(). test_features.BottomBarWhilePlaying.
- Save folder suggestions (2026-10-14, owner's request): detect.suggest_save_folder() - the other device's save
  folder translated (inside its game folder -> this game folder; C:\Users\<other>\... -> this profile; else the
  same path), existing or not (a game never run here gets the folder created by its first sync); otherwise an
  existing save-named folder in the game folder, or a folder named after the game (title, exe stem, folder name;
  _GENERIC names like Start/BGI ignored) in the usual roots, going down single-child chains (Maker\Game\1.10),
  newest first. app.suggest_save() filters with save_folder_problem and skips cloud/app data/Luna/LE. GameDialog
  fills the empty box + hint (game.saves_from_other / game.saves_found); Read with no save folder ->
  _offer_save_folder() asks (Use This Folder / Find It While Reading / Cancel). Matches every hand-set folder.
- Silent sync (2026-10-14; owner chose silent over a Sync Now/Later pop-up): StatusChecker syncs on every check via
  SyncEngine.sync_if_safe (push when only this side changed, pull when only the cloud did; backups as usual); never
  the game being read (skip), never pulls into a game open outside us (launcher.already_running -> stays "pull").
  Checks: refresh / live change / after play, plus quiet re-checks from _live_poll (every poll while a "pull" waits,
  else every RECHECK_POLLS=15 polls, ~1 min; _quiet_check keeps the top bar from flashing Syncing). Status line:
  status.startup_uploaded / status.auto_downloaded. Conflicts: _offer_conflicts() pop-up (Choose Now / Later) when
  idle; games asked about go into _conflicts_snoozed until _live_poll sees a new remote change. SyncWorker runs
  before_launch with ConflictDialog (Choose Now, and right-click "Sync Now" for push/pull/conflict); Read and
  closing wait for it.
- Tests: 121.
- Wording (owner, 2026-10-14): the interface never names one cloud service; it says "a cloud service of your
  choice" / "your cloud service's app" (guide lists Google Drive, Dropbox, OneDrive only as examples). Code that
  handles OneDrive specifically (duplicate names, refused folders, save search) stays. Guide now also covers: what
  to pick after a blank-save-folder first session, games that make files on install, game waits for the choice,
  closing only uploads.

## Version 1.0.1 plan (agreed 2026-10-15; work through in this order, owner tests each step)
1. Quick fixes: right-click menu: "Restore an Earlier Save" below Open Save Folder + Open Game Folder; remove
   "..." from menu items / buttons (done); progress + status texts KEEP theirs ("Searching...", "Now Reading...",
   "Search..." placeholder) - owner's call. Button order: Cancel
   always far left, main action far right (ask() helper + multi-choice pop-ups put Cancel last today). Read /
   Stop button: slow slight rhythmic pulse on hover (~3%, ~1.6 s), nothing when greyed. Library scrollbar
   fades sooner (~0.6 s). Edit window scrolls like Settings. VNDB "not connected" on first try: retry once
   on a fresh connection. Drop-down flicker (laptop only): owner thinks it was a one-off glitch;
   keep Qt's own style + the sliding-open animation (owner likes it). Forcing windowsvista + no animation was
   tried and reverted. Applied: QComboBoxPrivateContainer gets the input colour (no pale frame while
   sliding open); the bottom-bar status drop-down isn't touched by refreshes while its list is open.
2. Grid: Steam-style covers - today's size = minimum, covers stretch to fill each row, a new column when
   one more fits at minimum, equal gaps left/right, crops unchanged. Drag auto-scroll near top/bottom edge,
   faster the further you push (DRAG_SCROLL_*; not over trash / "‹ Library"). DONE: MainWindow._fit_grid (Qt's
   grid always reserves PM_ScrollBarExtent + 1 px, taken from the right edge; viewport margins top/bottom 0 so covers
   slide under the bars). First row was 12 px below the top bar (hover + selection outline touched/crossed it);
   now first row and last row sit ROW_GAP (36 px = the gap between rows) from the bars: Qt ignores spacing with a
   grid size, so LibraryGrid overrides visualRect/indexAt (+GRID_TOP_SHIFT 24), updateGeometries (scroll range
   + GRID_END_EXTRA), scrollTo + scrollToItem (Qt's scrollToItem bypasses the virtual scrollTo).
   Opening size: width 1200, height fitted on first show (MainWindow._fit_start_height) to START_ROWS = 2 rows with
   ROW_GAP above/between/below (1200x833 on the PC; at 800 the 2nd row sat 3 px above the bottom bar), capped
   to the screen. Background picture: KEEP as before (pinned-crop trial rejected: laggy,
   disorienting). Cover-size slider: not now (future idea).
3. Titles: hover titles - English breaks only between words; Japanese shrinks one font size when it
   overflows by 1-2 characters, else wraps (kinsoku: no line starts with 。、ー」 etc.). Added-game title:
   ignore engine names (AdvGame Module, codeX RScript, TVP(KIRIKIRI)..., Stuff スクリプトエンジン, AVGEngineV2,
   words engine/エンジン/module/script/platform/core), then best of folder/exe name (prefer Japanese,
   prefer the fuller one: Reli vs Relirium), strip edition tags and 『』 around the whole title. DONE (app.wrap_title / fit_title,
   titles.guess_title / is_engine_name; tests AddedGameTitles, HoverTitles).
4. Lights + bookmarks: new LAVENDER light (~#A98BD9) = synced, set up on this device, not yet on the other
   (top bar still "Synced"; Custom Theme + guide). Folder light = pie with EQUAL slices, one per colour
   present (max 6), most important first from 12 o'clock clockwise; whole pie breathes at the normal calm
   pace (no busy/steady per slice); each slice's glow in its own colour. Bookmarks: ribbon in the shape of
   assets/Bookmark.png, solid colour, no outline, top-right corner starting exactly at the cover's top edge
   (not above: clashes with the selection outline); no reading status = no bookmark; also on covers inside
   folders and in the folder mosaic. Colours (Custom Theme): Finished muted gold, Reading muted silver,
   Plan to Read muted bronze, Dropped muted rose. Toggle: bare icon button (Bookmark.png recoloured) between
   Edit and the trash can, like show_stats (setting at END of LocalSettings); hover: off = one nudge,
   on = steady rocking (TrashButton motions). In the guide. DONE: bookmark = cached pixmap (_bookmark_pixmap; drawing the
   shape per frame tanked the 60 fps light redraws), flat fill + chunky border (~1/7 of its width, colour role
   mark_border, default #1C1C1C), rounded outer tips, SHARP notch point, width 12.5% of the grid cover width.
   BookmarkButton has 13 px extra spacing before it so it sits centred. Status changes (_set_status) repaint
   at once and record the signature (_own_change) so the live poll doesn't reload.
5. Detection: save-folder suggestion must match the WHOLE title / full exe name / game folder name (not one
   word: "black" matched Documents\BLACKcyc\SaDisticBlooD_savefiles for Black Sheep Town), folder must hold
   save-like files, more than one candidate or any doubt -> suggest nothing. Other-device translation stays.
   Registry: key clearly named after the game/maker/exe -> filled in automatically, not asked; obvious noise
   ignored; unsure -> the pop-up gets separate "Save Folder" and "Registry" sections (unsure keys unticked).
   DONE: detect._whole_names/_same_game/_looks_like_saves/_save_subfolder (engine clutter -> the one save-named
   folder below), 2+ candidates -> nothing (PC check: 12/14 exact, 2 ambiguous empty, 0 wrong).
   detect.registry_confidence: "sure" (key named after the whole game) -> stored, shown as a note;
   "maybe" -> unticked checkbox (ticked when no folder changed at all); our own / Luna / LE keys ignored.
6. Folder sorting: in the sort drop-down under a divider: Folders First / Mixed / Folders Last (independent of
   the sort; folders sorted by the same rule: latest game / total hours / name). Custom order inside folders:
   drag to rearrange (drop marker), synced, new games go last, "Custom" in the sort drop-down inside a folder
   (starts from the order being viewed); mosaic shows the first four.
   DONE + extended (owner): Custom is also a LIBRARY sort (SORTS gains "custom"); drag between covers anywhere
   (gap / outer 20% of a cover = move, middle = folder as before; folders draggable to rearrange); doing it in
   another sort switches to Custom from the visible order (status.now_custom). Storage (cloud, shared):
   order.json {"library": [ids, "folder:<id>"]}, folders.json per folder "sort" ("" = Same as Library) + "order".
   Folder placement LocalSettings.folder_place (END field, Ikemen Sync shared), greyed while Custom; sort drop-down
   items via _sort_choices/_fill_sort (tick icon); in a folder: Same as Library + 4 sorts. Library.set_folder takes a
   game out of the list it leaves (so it lands at the end of the new one). Search/filter: no rearranging.
   FINAL owner decision (2026-10-15, after several rounds): menu = Title / Most Played / Recently Played |
   Folders First / Folders Last / Folders Mixed | My Arrangement (three groups, visible lines); NO ticks; nothing
   highlighted when a drop-down opens; folder options greyed when there are no
   folders. Folder options = a remembered setting (folder_place, saved, Ikemen Sync) for the three automatic sorts
   (folders mixed in / grouped front / back, always in that sort's order; folders placed by name / total hours /
   latest game - their inner order never matters). They never switch to My Arrangement. MY ARRANGEMENT (sort
   "custom", 自分の並び): hand-made order, kept when you switch away, restored when picked; dragging anything
   switches to it from what's on screen. In My Arrangement the folder options re-place the folders by
   LocalSettings.last_sort (the automatic sort used last, default hours; set when picking a sort, dragging away
   from one, or picking My Arrangement), games keep your order: First/Last group them, Mixed puts them in the
   exact slots that sort would give them (_place_folders). Inside a folder: 3 sorts | Same as Library / My Arrangement. Sort/placement picks go
   through activated -> choose_sort (deferred) and glide (_positions/_slide_from); no refresh/save check.
   Drop-downs are ThemedCombo (layered popup + own mask slide: no white first frame). Top-bar status label has
   a fixed width (longest of Synced/Syncing/Error) so the bar never shifts.
   ThemedCombo's layered popup keeps showing its LAST picture: it repaints every slide frame (the repaint at
   show time doesn't reach the screen). Sort picks only move marks (_mark_sort; list rebuilt only for folder
   in/out + language); the tick moves on view.pressed (Qt blinks + closes before activated), without moving
   the highlight (Qt picks the highlighted row on release). Diagnostics: IKEMEN_DEBUG_STALLS=1 writes freezes
   > 0.5 s (all threads' stacks) to %APPDATA%\IkemenReader\stalls.log; diag(text) adds lines there.

- Folder open/close (2026-10-16, owner chose cohesion over the old zoom): the SAME fade as search. open_folder /
  close_folder -> _apply_filter(select, after_rebuild): library fades out, then the folder's games fade in (and
  back); closing brings the folder's own tile in first (selected). FolderTransition / _covers_snapshot / _tile_rect
  removed. Leaving pictures carry a shift (the "‹ Library" bar moves the grid) so they stay put on screen.
  Read button: only the bevel scales, the label stays.

- Auto-updater (owner's request, 2026-10-15; updater.py): checks GitHub's latest release on every start (packaged
  program only; IKEMEN_UPDATE_CHECK=1 shows the question from source). Settings "Install Updates Automatically"
  (LocalSettings.auto_update, default off, per device, under Launch Program on Startup). Off: "Later | Update Now" with
  release notes -> UpdateDialog (progress, Cancel) -> restart. On: downloads in the background, "Later | Restart
  Now"; Later = installed when Ikemen Reader closes. Download verified against GitHub's asset SHA-256 digest;
  unpacked into <program folder>\update\new (build.py creates update\; falls back to %APPDATA%\IkemenReader\update
  if not writable). install-update.ps1 (UTF-8 BOM, hidden PowerShell) waits for our pid, moves each item of the
  release (exe, _internal) into place, old ones to update\previous, rolls back on failure, logs update\update.log,
  restarts. Only the release's own items are replaced (owner's Google Drive.lnk etc. stay). Never while a game
  runs / saves sync. last_version.txt -> "Updated to X" note. Release zips must keep the layout
  "Ikemen Reader/Ikemen Reader.exe" and be named *Ikemen*.zip; tag = version ("1.0.1").
  1.0.0 has no updater: 1.0.1 is installed by hand; 1.0.1 -> 1.0.2 is the first automatic update.

## Sweep before 1.0.1 (2026-10-15)
- Guide: title guessing, VNDB suggestions, edge-scrolling drags, a folder's own My Arrangement, search showing games
  inside folders, Backspace; "Making It Yours" follows the Settings order. Still nothing about the easter egg / Anki.
- Text: tooltips that are sentences end with a full stop (EN + JA); JA fixes: フォルダーを先頭に / 末尾に / 混ぜる
  (混在 can't take を), no まだ+未 doubling, admin hint says right-click the icon, 後で everywhere, 元にするテーマ.
- Animations: see "Animation pass" above. Fixes found while stress testing: Edit window's Cancel/Save touched
  (BUTTON_GAP = 8 on every button row, pop-ups were 16, others 6-11); status drop-down shows No Status with
  nothing selected. Random UI stress (21 action kinds incl. drags, other-device changes, language/theme switches,
  300 games + background, no delays): ~13,000 actions, 0 errors. Packaged 1.0.1 built and run; the real updater
  swap rehearsed on the 134 MB build (updated, previous kept, extra files kept, restarted). Tests: 161 (162 after the tweaks).

## Tweaks after 1.0.1 (2026-10-15)
- Ikemen Sync folders are `<cloud>/device_settings/Device 1|Device 2` (devsync.FOLDER_NAMES; old PC/Laptop folders
  renamed on publish). A computer that switched numbers swaps the two folders (devsync._tidy_folders, by the
  "computer" in each settings.json) so nothing is overwritten.
- Mixed versions (other device on 1.0.1): saves sync as before (sync_engine unchanged). 1.0.1 still shares into
  device_settings/PC|Laptop: _tidy_folders keeps whichever copy has the newer shared_at. 1.0.1 drops Game.names /
  names_for whenever it saves a game (unknown fields); the new version just fetches them again. 1.0.1 can't read
  the new Device 1/2 folders, so its "Copy Settings From Device N" finds nothing until it's updated.
- Hover text drawn with Qt.TextDontClip (Hiragino's descenders touch the bottom of the line box: the g of Reading).
- FitsScreen._top_up: after laying out, grows the window by whatever the scroll bar still has (estimate was 1-16 px
  short: a scroll bar with nothing to scroll). Scrolls only when the screen really is too short.
- Menu / drop-down dividers: crisp_line() snaps to screen pixels; right-click menus drawn by the MenuLines app event
  filter (style sheet separator transparent). At 125 % / 150 % scaling the two dividers were 1 vs 2 px.
- Save-location pop-up: Ask Me Next Time far left, Somewhere Else | Use This Folder right.
- SmartScreen ("Windows protected your PC") on a fresh download stays: owner won't pay for code signing. Updates
  via the updater don't trigger it (no Mark of the Web); manual install: zip Properties -> Unblock.
- Grid evenness checked at 100/125/150/175/200 % scaling (red-cover pixel probe): gaps between covers identical,
  outer edges +1 screen px (leftover of whole-pixel covers), rows top/between/bottom within 1 screen px. No change.
- Search pass 2 (2026-10-15, owner: "abuse the search"): bench = scratch searchbench.py (owner's library copy +
  look, keystrokes from a precise timer, every grid paint timed), filmstrips, random fuzz (3,900 actions, 0 errors).
  * Main cause of search stutter: launcher.warm_up / _related_processes asked psutil for each process's ppid =
    psutil rebuilds the whole ppid table per process (~10 ms GIL each, 3.5 s for ~340 programs). Now _parents() =
    ONE psutil._pswindows.cext.ppid_map() call (fallback: per process) via _scan(); warm_up no longer asks ppid.
    First scan 5.3 -> 2.4 s. hold_warm_up(hold, who) = set of holders (egg + "grid": slides/springs/drags).
  * Late frames (>20 ms) per run: ~100 -> ~25 (rest = idle light-pulse paints); on 1 core at 150 %: 149 -> 31.
  * title_sort_key lru_cached (34k calls per run at 220 games). Main anim QTimer now PreciseTimer.
  * SEARCH MOTION (owner, final 2026-10-16: "no more sliding"): nothing slides in search. A cover whose place
    changes fades out there and back in at its new place; covers keeping their place stay still. OUT (LEAVE_S 0.16,
    shrink 0.12) then IN (ARRIVE_WAIT 0.1 later; APPEAR_S 0.36 fade + grow + rise, stagger 25 ms, max 0.15 s).
    Grid changes once typing pauses (SEARCH_PAUSE_MS 120, MainWindow._search_pause; status/sort changes at once;
    tests use search_now()). Leaving pictures = list of groups (MAX_LEAVING_GROUPS 4): a new change never cuts an
    earlier fade short (was ~27 pops/run); snapshots include arriving covers (bare paint draws them), so a cover
    taken away mid-arrival fades from how it looked. Arrivals drawn beneath (under_pass), held while a leaving
    picture still shows in their place (_hold_blocked_arrivals). Rejected on the way: spring glides, row-change
    swaps + gliding mix ("sliding on some things and not on others is awful"). Drag-and-drop slides unchanged. SORT changes fade the same way
    (choose_sort -> _apply_filter; measured: no late frames during the fade, slides had 34-54). View choices are
    saved VIEW_SAVE_DELAY_MS 900 later (_save_view_choice; closeEvent flushes) so saving never competes with a fade.
    Checks: overlap.py 0 blended frames, pops.py 0, searchfuzz 0 errors.
  * "No Results Found" / 見つかりませんでした (grid.no_results): drawn faint in LibraryGrid.paintEvent, fades
    (no_results 0..1, NO_RESULTS_S) when searching/filtering and nothing matches. 2.7x the UI font; centred on
    the grid widget's own rect (NOT the viewport: its margins change with the scroll bar / re-centring).
  * "No Results Found" only once the last cover has gone (_no_results_target), vanishes in the frame covers return,
    sits at NO_RESULTS_Y 0.44 (optical centre), rises NO_RESULTS_RISE 8 px as it fades in (0.3 s).
  * Search arrivals never decode covers mid-frame: an arriving tile whose cover isn't in memory
    (MainWindow.cover_ready / CoverCache.ready) stays hidden, its delay sliding on, while want_cover ->
    _load_wanted_covers decodes it in the background (accept(urgent=True): kept past the budget until drawn);
    unreadable ones -> _cover_given_up (drawn the usual way). Cold reveal of 22 covers: 100-120 ms freeze -> none.
  * Matching (owner's rules): kanji in query -> substring anywhere; kana -> title starts with it; Latin/numbers ->
    start of a word (_words: Latin/number runs; one query word may run on across words: "sakuranotoki"; several
    = each starts a later word, in order, gaps allowed: "girls 1", "sakura toki"); numbers = that whole number anywhere ("1" = volume 1, not "11月"; in multi-word queries
    a number word must match exactly); ONE letter = names that begin with it only (owner: "a" found Bunny's via "Rent-a-Mom Agency").
    Over title + VNDB names (library.matches_names; folders too: a folder whose name matches shows as its tile
    in search results, placed by folder_place / its My Arrangement spot; with a status filter only if it holds one). Fold: NFKC, casefold, katakana->hiragana, apostrophes dropped.
- Search by other names: Game.names / names_for (VNDB title, alttitle, every titles[].title/latin, aliases; max 40)
  fetched by MainWindow._fetch_names after refresh (only with Settings VNDB on; one batched vndb.names() request;
  written into the cloud game file so both devices have them; failures retried next start). library.matches_search
  + search_key (NFKC, casefold, katakana -> hiragana, no spaces/punctuation). In the guide.

- Folder light checked: one equal slice per COLOUR present (99 lavender + 1 green = halves, verified). A "2/3 lavender"
  sighting was blue (playing/pull) + lavender + green: blue #6FA8DC and lavender #A98BD9 look alike at dot size.
  Owner REJECTED (2026-10-15) a redesign preview: shadow instead of the ring, slice gaps, lavender #BE7FD8,
  borderless slimmer bookmarks, slide/swell animations. Keep lights + bookmarks as they are; don't re-propose.

## Sweep after the search / fade work (2026-10-16)
- Fixed: i18n key "conflict.later" was defined twice (the Choose Now / Later pop-up showed "Decide Later (Don't
  Change Anything)"): the pop-up now uses "conflict.ask_later". Unused imports removed (pyflakes clean on the
  touched modules), dead _settle_grid removed, lazily-created MainWindow state (_names_busy/_names_tried/_view_save)
  now set up in __init__. Guide's search paragraph rewritten (it had grown into one run-on sentence).
- Checks: 169 tests; random stress 3,200 actions (0 app errors; the 220-game copy shares save folders between
  duplicates, so its conflict pop-up is expected); overlap 0 blended frames; all-pairs 840 changes clean.
- ConflictDialog(other_set_up=...): when the game isn't set up on the other device, the cloud copy can only have come
  from this device (sync record lost / files moved): conflict.body_solo + conflict.cloud_solo ("Uploaded From This
  Device Earlier") instead of "(Other Device)". The date shown = newest mtime in the cloud copy (copy2 keeps mtimes).
  Same rule everywhere (owner: "not changing it would just be wrong"): MainWindow.only_here(g) / solo_key(key, g)
  pick "<key>_solo" wording for the status line + tip, Sync Now's "nothing was changed", Read refused
  (play.not_launched_conflict_solo); lists of games (Choose Now / Later pop-up, top-bar Error tooltip) use the
  "_mixed" wording when any game in them is set up only here. Guide: one added point (EN + JA).
- Pop-up text pass (2026-10-16, EN + JA, every dialog / message box): natural overall; fixed conflict.body_solo
  ("It" -> "The game"; JA double の, この->その), sync.tip.conflict_solo (split the bracketed aside), update.ready JA
  (〜するか、〜されます mismatch -> two sentences). Owner-chosen wording untouched.
- Lights (owner, 2026-10-16): "Not Set Up on This Device Yet" got its own YELLOW (#E6D95A pale lemon, kept clear of
  the golden amber; preview checked at real size), steady, second in LIGHT_ORDER (after red: it needs you). Grey now
  = no saves yet / save at least once / checking. Theme role light_yellow; guide updated (EN + JA). Folder slices:
  colours are pixel-identical in a split light (owner's "purple looks different" = simultaneous contrast, no change).
- First sync on a device (the mirror of _solo): SyncEngine.has_synced(gid); MainWindow.first_sync(g) = set up on both
  but never synced HERE -> a mismatch means this device already had files (often made by the game on install).
  solo_key() picks "_solo" first, else "_first": conflict.body_first, sync.state/tip.conflict_first ("First Sync:
  Choose Which Saves to Keep"), sync.conflict_unresolved_first, play.not_launched_conflict_first; lists use "_mixed".
- Test libraries are copies of the real games: their PC save folders are REAL. Syncing in a temp cloud only uploads
  into the temp copy, but keep it that way (never point a test at G:).

## Ideas for later (owner interested, not scheduled)
- Gradient options in the colour selector (Custom Theme): a colour role could be a gradient (two or more stops,
  direction) instead of one flat colour (owner's idea, 2026-10-15). Assessed 2026-10-16, skipped for now: 33 roles,
  86 style-sheet uses (QSS takes qlineargradient: easy), 55 painted uses (gradient per shape), 19 colour-maths spots
  (lighter / alpha / contrast warning / blended backdrop / logo recolour: no single answer), gradient editor, and
  Ikemen Sync only accepts #RRGGBB (store gradients in a new field old versions ignore). Recommended scope if
  revived: SURFACES only (library background, bars, buttons, placeholder covers) = ~1/3 of the work.
- macOS / Linux support: UI, sync engine, library already portable; games would run through Wine (start/track via
  wine, save paths inside the Wine prefix + translation between devices), Wine registry for registry saves,
  LE/Luna don't apply, startup/.lnk/window titles/playtime need equivalents, build per OS (Mac signing ~$99/yr).
  Can't be tested from the owner's Windows machines.
- Cover size changer (Ctrl+wheel and/or slider in Settings → Display).
- Theme editor (low priority): Settings → "Customise Theme..." window listing every colour in COLORS/LIGHTS
  under friendly grouped names, swatch + hex box each, Qt colour picker (styled), live preview through
  apply_theme, start from Dark/Light, per-colour + full reset, optional "Simple" mode (3-4 base colours →
  derived shades), low-contrast warning. Tick/chevron SVGs regenerated from colours; hover text over art
  stays light. Decide: per-device or synced via the cloud folder.
  Logo + star CAN follow the theme: both images are two-tone (~51% #F0F0F0 fill, ~48% #303030 outline,
  soft edges between) → recolour per pixel by lightness between 0x30 and 0xF0, lerp outline→fill, keep alpha
  (prototype verified 2026-10-13, edges stay smooth, can also invert). Adds "Logo Fill" / "Logo Outline"
  pickers; applies to top-bar logo, button stars, welcome logo, window/taskbar icon (set at runtime). The .exe
  file icon (Explorer/shortcuts) is fixed at build time. Optional: let a theme use custom logo/star images.
- From the stress test: undo for "Remove From Library"; cache fingerprints for big save folders; friendlier message when a game
  closes instantly; offer to close Luna automatically.

## Build order
1. Sync engine — done.
2. Launch/exit handling — done; tested 2026-10-13 with Sakura no Toki (saves `%APPDATA%\枕\サクラノ刻`).
3. UI — redesign built 2026-10-13 (all items above). Needs the owner's real-world test: Luna launch
   (incl. Luna-already-open behaviour), LE, first-play detection on a real new game, laptop side.
4. Packaging — done 2026-10-14 (v1.0.0). `py make_icon.py` builds assets/Icon.ico (10 sizes) from Icon.png;
   `py build.py` runs PyInstaller (folder build, windowed, icon, version info, assets bundled incl. Font.otf)
   -> dist\Ikemen Reader\Ikemen Reader.exe (~128 MB folder). Tested on the PC: opens, library + theme + fonts OK.
   Git: local repo (branch main), build/ and dist/ ignored. Laptop live test over Google Drive passed
   2026-10-14 (owner: "Everything worked").

## Notes
- Owner is not very experienced with terminals; explain commands plainly.
- Back up real save folders before testing on a real game (`..\save-backups\`).
