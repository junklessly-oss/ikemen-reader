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
- On app start: games with "this device has newer saves" are synced if safe (`sync_if_safe`: upload when only this side changed, download when only the cloud did).

## Files
- `app.py` — PySide6 UI (`py app.py`). Palette + sizes/timings at the top. Play runs on a QThread;
  conflicts asked via a blocking signal. Drag-and-drop .exe/.lnk to add.
- `i18n.py` — ALL interface text, English + Japanese side by side (`tr(key, **kw)`).
- `sync_engine.py`, `regsync.py` (registry export/import + safety rules), `library.py` (settings, games,
  device slots, sorting), `launcher.py` (run/watch, window titles, Luna/LE, first-play detection),
  `detect.py` (save-location detection), `luna.py`, `vndb.py`, `titles.py` (title cleaning), `lnk.py`
  (shortcut reader; follows Luna `--Exec` shortcuts to the game; keeps a shortcut's arguments, e.g. a loader's
  "AlphaROMdiE.exe malie.exe", stored as MachinePaths.args, else taken from a shortcut beside the .exe), `vn.py` (small CLI), `themes.py` (saved custom
  themes in `<library root>/themes/`), `outside.py` (starts
  every outside program: games, Luna, LE, admin prompt), `taskicon.py` (pinned taskbar button + desktop + Start menu shortcuts
  take the theme's icon; packaged program only).
- `assets/`: `Logo.png`, `Icon.png`, `Font.otf` (Hiragino Maru Gothic ProN W4, owner-supplied; the owner has legal permission for all
  bundled fonts — don't raise licensing), `ZenMaruGothic-Regular.ttf` (+OFL, fallback), `check.svg`.
- LocalSettings: add new fields at the END (SettingsDialog._save fills earlier ones by position;
  test_features.SettingsSave guards this).
- Tests: `test_sync_engine.py`, `test_launcher.py`, `test_features.py` — 322 tests (2.0.9: Luna adds a game new to it; 2.0.8: + stuck copy / exit watchdog / helper ends stuck copy / broken update undone / spare settings / frozen copy not answering; 2.0.7: + search message words, + theme warning places, sharp folder mosaics; 2.0.6: + colour code boxes, Discord device used last, themed shortcut icons; 2.0.5: + path boxes / wide pop-ups, pop-ups deleted, closed test windows deleted per test; 2.0.4: + arrangement per view, Language list fade; 2.0.3: + shortcut
  arguments, Choose VNDB Cover; 2.0.2: + add into open folder / folder-bar rename / trash-drop fade + removal fade; full run now ~7-8 min; 2.0.1: + drag-scroll turns / scroll guard / bars wait / fade-back / held cover while a
  session ends, title-bar press focuses; 2.0.0: notes, saved themes, closed-window timers), all pass on
  the PC (Python 3.14, `py -m unittest`; ~7-8 min with the app open) and the laptop (Python 3.15, ~8-9 min). Older notes below give the count at the time. Process/window/registry tests run for real on Windows.
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
- My Arrangement belongs to the view (owner, 2026-10-25): This Device / Other Device are each computer's own
  (`<cloud>/views/<slot>.json`, written only by that device; folder sorts per device too); only Both Devices uses the
  shared order.json / folders.json orders. See NOTES.md "Arrangement per view".
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
  Saved Themes (2.0.0): Custom Theme tabs "Colours | Saved Themes" (left, 18 px above and below). ThemeGrid (one
  widget) of previews, 2 a row, + last (asks a name). A saved theme = colours + background (picture/opacity/crop; none =
  removes yours) + fonts; shared via the cloud (themes.py), solo copies/merges it. Click previews all, Save keeps,
  Cancel restores; right-click Rename / Delete. Cloud only touched in the background (theme_cache). See NOTES.md.
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
- Bottom bar: Read (was Play; JA 読む, 17.5pt since 2026-10-17; "Now Reading"/読書中 while running; i18n bar.play), title + sync line, stats block (see Statistics restyle),
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
  (PreciseTimer) only sets the amplitude (scaled by the box's growth so it eases in/out); MainWindow._tick moves
  rumble_t and redraws (2026-10-17: both timers redrawing gave uneven frames, 3-7 hitches of 26-76 ms per 4 s -> 0). CardDelegate.paint while rumbling: each tile
  drawn upright once into CardDelegate._tiles (lights skipped via _skip_light), then only rotated; lights drawn
  live on top; covers fully out of the viewport aren't drawn while rocking (their corners swung into view). ~7.8 ms/frame vs ~5 ms still (1280x800, 40 covers; was 11 ms drawing tiles rotated).
  warm_up (psutil's first look, holds the GIL ~10 ms per step) is paused while the covers rock
  (launcher.hold_warm_up): with it 45 fps + stutter near startup, held 125 fps steady.
  Egg boxes react only over the box as drawn (BoxZone mixin: mouse tracking, _zone() / _arrive() / _depart(),
  4 px leeway once inside); clicks in their spare margin pass through to the overlay (= dismiss).
- Settings fits a 1080p screen (needs 990 of 1000 px): headings inside Settings have padding-top 5 px
  (QDialog#settings_dialog QLabel#section; 8 px elsewhere), 6 px after each divider keeps the gaps equal.
  Click elsewhere / Esc (close_folder checks it first) dismisses. APP_VERSION = "2.0.9" in app.py (build.py reads it).
- Notes (2.0.0): right-click a game -> "Add Notes" / "Notes" (under Edit) -> NotesLayer (DimLayer, like the easter
  egg): 72.5 % text box + elided title, saved on close only (owner), in Game.notes (synced with the game file), saved in
  the background in order, flushed on exit; every key but Esc stays in the box. Removal warns about notes.
- Safeguards (2.0.8, owner): closing ends itself after EXIT_GUARD_S (exit.log); update helper ends a stuck old copy
  (same start time only), undoes an update whose new version closes before confirm_started; a start finding a copy
  that doesn't REPLY asks "Isn't Responding" (End It and Open); settings.backup.json; crash.log. See NOTES.md.
- Removing a game: right-click "Remove From Library", the trash button, or Edit's red button — all use
  confirm_remove(): "Are You Sure?" heading, Cancel (default, also on close) | Confirm. Saves untouched.
- Text that appears on a click / choice / background answer never moves the layout (2.0.9, owner): keep_place() /
  SteadyHint reserve its room; controls without a use are greyed, not hidden. See NOTES.md (2026-10-31).
- Buttons never end in "..." (menus still may); all buttons size to their text.
- Path boxes (Settings: Cloud Folder / LE / Luna / Anki; game window: exe + save folder) = PathBox: the START of a
  long path shows, "..." where cut off (three full stops, not "…"), full path in our hover pop-up (2.0.5, owner). Hover
  pop-ups wider than half their window may use the screen's width; never use QToolTip (it fights SmoothTip).
- Capitalisation (owner, 2026-10-25): labels / buttons / headings / box placeholders / short progress messages = Title
  Case (a, an, the, and, or, but + prepositions of 3 letters or fewer lowercase; With / From / Without capitalised);
  full sentences = sentence case with a full stop; "This Device" capitalised mid-sentence only when pointing at the
  Edit row. Details in NOTES.md (Shortcut arguments, 2026-10-25).
- Edit's "Choose VNDB Cover": shown while the cover is from VNDB and Game.vndb_multi (2+ VNDB covers); fades in only
  when first discovered. See NOTES.md.
- Text pass 2026-10-14: every EN/JA string in i18n.py + guide.py reviewed. Owner-chosen wording kept as is
  (Ikemen Sync messages, Are You Sure?/Confirm, Built In/User Added, Launch Program on Startup, status names).
- Per-game checkboxes "Launch With Luna" / "Launch With Locale Emulator" (replace the old per-launch LE
  button). LunaTranslator: `LunaTranslator.exe --Exec <uid>` (uid looked up by exe path in
  `userconfig\savegamedata_*.json`, per device, never stored); Luna's own LE setting then applies. A game Luna
  doesn't know is added to that list by us (2.0.9, luna.add_game, only while Luna is closed, backup .ikemen.bak).
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

## Detailed notes (NOTES.md)
- `NOTES.md` holds the full dated history: hardening + audits, Luna fix, 1.0.1 plan, search / fade work, statistics,
  bottom-bar fades, pop-ups to the front, launch / opening flash, solo mode (My Device), library views, menus,
  smooth scrolling (strips, wheel glide, wheel-press), drag fixes, refocus hitch, ideas for later.
- Before changing any area, grep NOTES.md for it: most owner decisions, rejected designs ("don't re-propose") and
  measured gotchas live there. New dated notes go to NOTES.md; keep this file to the brief + standing decisions.

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

