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
  (shortcut reader; follows Luna `--Exec` shortcuts to the game), `vn.py` (small CLI).
- `assets/`: `Logo.png`, `Icon.png`, `Font.otf` (Hiragino Maru Gothic ProN W4, owner-supplied, licence is
  likely Mac-only — owner's personal use), `ZenMaruGothic-Regular.ttf` (+OFL, fallback), `check.svg`.
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
- Performance: the animation timer only redraws what moves (hovered tile + shadow; small squares around
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
  below rocks while hovered; neither clickable.
  Click elsewhere / Esc (close_folder checks it first) dismisses. APP_VERSION = "1.0.0" in app.py.
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

## Ideas for later (owner interested, not scheduled)
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
   Git: local repo (branch main), build/ and dist/ ignored. Next: live test on the laptop.

## Notes
- Owner is not very experienced with terminals; explain commands plainly.
- Back up real save folders before testing on a real game (`..\save-backups\`).
