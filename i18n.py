"""
i18n.py - every piece of interface text, English and Japanese side by side.

To fix a wording, edit the line here; nothing else needs to change.
{name} placeholders are filled in by the program.
"""
from __future__ import annotations

_lang = "en"

S: dict[str, tuple[str, str]] = {
    # ------------------------------------------------------------------ app
    "app.name":                 ("Ikemen Reader", "イケメンリーダー"),

    # ------------------------------------------------------------------ top bar
    "top.search":               ("Search...", "検索…"),
    "top.add":                  ("Add Game", "ゲームを追加"),
    "top.settings":             ("Settings", "設定"),
    "top.synced":               ("Synced", "同期済み"),
    "top.syncing":              ("Syncing", "同期中"),
    "top.attention_one":        ("Error", "エラー"),
    "top.attention_many":       ("Error", "エラー"),
    "top.attention_tip_mixed":   ("Some games' saves don't match the cloud copy. Click to go to each one in turn.",
                                 "一部のゲームのセーブがクラウドのコピーと一致しません。クリックすると、該当するゲームに順に移動します。"),
    "top.attention_tip":         ("Saves changed on both devices. Click to go to each game in turn.",
                                 "両方の端末でセーブが変更されています。クリックすると、該当するゲームに順に移動します。"),
    "top.hide_stats":           ("Hide Statistics", "統計を非表示"),
    "top.show_stats":           ("Show Statistics", "統計を表示"),
    "top.stats_tip":            ("Show or hide time read and last read on the covers and the bottom bar.",
                                 "カバーと下部のバーに表示される読書時間と最後に読んだ日を、表示または非表示にします。"),
    "sort.recent":              ("Recently Played", "最近プレイした順"),
    "sort.hours":               ("Most Played", "プレイ時間が長い順"),
    "sort.title":               ("Title", "タイトル順"),
    "sort.custom":              ("My Arrangement", "自分の並び"),
    "sort.follow":              ("Same as Library", "ライブラリと同じ"),
    "place.mixed":              ("Folders Mixed", "フォルダーを混ぜる"),
    "place.first":              ("Folders First", "フォルダーを先頭に"),
    "place.last":               ("Folders Last", "フォルダーを末尾に"),
    "status.now_custom":        ("Now showing My Arrangement: your own order. Drag covers between others to arrange them.",
                                 "「自分の並び」に切り替えました。カバーを他のカバーの間にドラッグすると並べ替えられます。"),
    "filter.all":               ("All Games", "すべてのゲーム"),

    # ------------------------------------------------------------------ reading status
    "status.none":              ("No Status", "未設定"),
    "status.reading":           ("Reading", "プレイ中"),
    "status.finished":          ("Finished", "読了"),
    "status.plan":              ("Plan to Read", "積みゲー"),
    "status.dropped":           ("Dropped", "中断"),

    # ------------------------------------------------------------------ grid / bottom bar
    "grid.no_results":          ("No Results Found", "見つかりませんでした"),
    "grid.empty":               ("No Games Yet", "ゲームがまだありません"),
    "grid.no_cloud":            ("The cloud folder can't be found:\n{path}\n\nIs your cloud service's app running? "
                                 "You can change the folder in Settings.",
                                 "クラウドフォルダーが見つかりません：\n{path}\n\nクラウドサービスのアプリは起動していますか？"
                                 "フォルダーは設定で変更できます。"),
    "grid.drop_hint":           ("Drop to add", "ドロップして追加"),
    "card.never":               ("Not Started", "未読"),
    "stats.never_played":       ("Never Played", "未プレイ"),   # last read per device: never on that one
    "stats.not_recorded":       ("Not Recorded", "記録なし"),   # read there before dates were kept per device
    "bar.play":                 ("Read", "読む"),
    "bar.playing":              ("Now Reading", "読書中"),
    "bar.stop":                 ("Stop", "終了"),
    "bar.tip_stop":              ("Closes the game. It may ask you to confirm in its own window.",
                                 "ゲームを終了します。ゲームの画面で確認が表示される場合があります。"),
    "stop.title":               ("Game Still Open", "ゲームがまだ開いています"),
    "stop.body":                ("{title} is still open. It may be waiting for an answer in its own window "
                                 "(such as \"Quit the game?\"). Force it to close? Anything you haven't saved in the game "
                                 "will be lost.",
                                 "{title} はまだ開いています。ゲームの画面で確認（「終了しますか？」など）が表示されているかもしれません。"
                                 "強制終了しますか？ゲーム内でセーブしていない内容は失われます。"),
    "stop.force":               ("Force Close", "強制終了"),
    "stop.keep":                ("Keep Open", "開いたままにする"),
    "bar.time_read":            ("Time Read", "読書時間"),
    "bar.last_read":            ("Last Read", "最後に読んだ日"),
    "bar.edit":                 ("Edit", "編集"),
    "bar.open_saves":           ("Open Save Folder", "セーブフォルダーを開く"),
    "bar.tip_busy":             ("Another game is running.", "別のゲームが起動中です。"),
    "bar.tip_no_exe":           ("Set This Device's .exe in Edit first.",
                                 '先に「編集」で、この端末の .exe を設定してください。'),

    # sync lights / status lines (bottom bar)
    "sync.state.synced":        ("Saves Are in Sync", "セーブは同期済みです"),
    "sync.state.solo":          ("Saves Are in Sync (Not Set Up on the Other Device Yet)",
                                 "セーブは同期済みです（もう一方の端末では未設定）"),
    "sync.tip.solo":            ("The saves are safe in the cloud. Set this game up on the other device to read it there too.",
                                 "セーブはクラウドに保存されています。もう一方の端末でもこのゲームを設定すると、そちらでも読めます。"),
    "sync.state.push":          ("Newer Saves on This Device", "この端末のセーブの方が新しいです"),
    "sync.state.pull":          ("Newer Saves in the Cloud", "クラウドのセーブの方が新しいです"),
    "sync.state.conflict":      ("Saves Have Changed on Both Devices", "両方の端末でセーブが変更されています"),
    # (_solo: the game isn't set up on the other device, so the cloud copy was uploaded from this one; see solo_key)
    # (_first: this device has never synced the game, but already has files of its own - often made by the game
    #  itself when installed or first opened; the cloud has the other device's saves. See MainWindow.variant_key)
    "sync.state.conflict_first": ("First Sync: Choose Which Saves to Keep", "初回同期：どちらのセーブを残すか選んでください"),
    "sync.tip.conflict_first":   ("This device already has its own save files for this game (some games make them when installed or "
                                 "first opened), and they don't match the cloud. Choose which to keep when asked, with right-click "
                                 "Sync Now, or when you press Read.",
                                 "この端末にはすでにこのゲームのセーブファイルがあり（インストール時や初回起動時にファイルを作るゲームもあります）"
                                 "、クラウドのセーブと一致しません。尋ねられたとき、右クリックの「今すぐ同期」、または「読む」を押したときに、どち"
                                 "らを残すか選べます。"),
    "sync.state.conflict_solo": ("This Device's Saves Don't Match the Cloud Copy", "この端末のセーブがクラウドのコピーと一致しません"),
    # full explanations, shown when hovering the status line
    "sync.tip.push":            ("This device has newer saves. They upload automatically.",
                                 "この端末のセーブの方が新しいです。自動でアップロードされます。"),
    "sync.tip.pull":             ("Newer saves are in the cloud. They download automatically when the game isn't running.",
                                 "クラウドに新しいセーブがあります。ゲームが起動していないときに、自動でダウンロードされます。"),
    "sync.tip.conflict":        ("The saves have changed on both devices. Choose which to keep when asked, with right-click Sync Now, or when you press Read.",
                                 "両方の端末でセーブが変更されています。尋ねられたとき、右クリックの「今すぐ同期」、または「読む」を押したときに、どちらを残すか選べます。"),
    "sync.tip.conflict_solo":   ("This device's saves don't match the copy in the cloud. The game isn't set up on the other device, "
                                 "so that copy was uploaded from this device earlier. Choose which to keep when asked, with "
                                 "right-click Sync Now, or when you press Read.",
                                 "この端末のセーブが、クラウドにあるコピーと一致しません。もう一方の端末では設定されていないため、"
                                 "そのコピーは以前この端末からアップロードされたものです。尋ねられたとき、右クリックの「今すぐ同期」、"
                                 "または「読む」を押したときに、どちらを残すか選べます。"),
    "sync.tip.unset":           ("Click Edit to set This Device's .exe for this game.",
                                 '「編集」から、この端末でのゲームの .exe を設定してください。'),
    "sync.state.empty":         ("No Saves Yet", "セーブはまだありません"),
    "sync.state.unset":         ("Not Set Up on This Device Yet", "この端末ではまだ設定されていません"),
    "sync.state.detect":        ("Save at Least Once", "一度はセーブしてください"),
    "sync.tip.detect":           ("Save at least once while reading, and the save location is found automatically.",
                                 "読んでいる間に一度セーブすると、セーブの場所が自動で見つかります。"),
    "sync.state.checking":      ("Checking Saves...", "セーブを確認中…"),
    "sync.state.playing":       ('Now Reading...',
                                 '読書中…'),
    "sync.state.starting":      ("Starting the Game...", "ゲームを起動中…"),
    "sync.tip.playing":          ("Saves upload when you close the game.",
                                 "ゲームを閉じると、セーブがアップロードされます。"),

    # messages from the sync engine / launcher (status bar)
    "sync.pulled":              ('Downloaded newer saves from the cloud.',
                                 'クラウドから新しいセーブをダウンロードしました。'),
    "sync.pushed":              ("Uploaded saves to the cloud.", "セーブをクラウドにアップロードしました。"),
    "sync.up_to_date":          ('Saves are already up to date.',
                                 'セーブはすでに最新です。'),
    "sync.nothing_to_upload":   ("No new saves to upload.", "アップロードする新しいセーブはありません。"),
    "sync.conflict_unresolved": ('The saves differ between the two devices, so nothing was changed.',
                                 '両方の端末のセーブが異なるため、何も変更しませんでした。'),
    "sync.conflict_unresolved_first": ("This device's files don't match the saves in the cloud, so nothing was changed.",
                                 "この端末のファイルがクラウドのセーブと一致しないため、何も変更しませんでした。"),
    "sync.conflict_unresolved_solo": ("This device's saves don't match the copy in the cloud, so nothing was changed.",
                                 "この端末のセーブがクラウドのコピーと一致しないため、何も変更しませんでした。"),
    "status.checking":          ("Checking saves...", "セーブを確認中…"),
    "status.launching":         ("Starting the game...", "ゲームを起動中…"),
    "status.closed_syncing":    ("Game closed. Syncing saves...", "ゲームを終了しました。セーブを同期中…"),
    "status.detecting":         ("Game closed. Looking for its save files...", "ゲームを終了しました。セーブファイルを探しています…"),
    "status.played_for":        ("{msg}  Read for {time}.", "{msg}　読書時間：{time}"),
    "status.removed":           ('Removed "{title}".', "「{title}」を削除しました。"),
    "status.removed_here":      ('Removed "{title}" from this device.', "「{title}」をこの端末から削除しました。"),
    "status.undo":              ("Undo", "元に戻す"),
    "status.restored":          ('"{title}" is back in the library.', "「{title}」をライブラリに戻しました。"),
    "status.closed_at_once":    ("The game closed right after starting.", "ゲームは起動直後に終了しました。"),
    "status.auto_downloaded":   ("Downloaded newer saves for: {games}", "新しいセーブをダウンロードしました：{games}"),
    "status.save_filled":       ("Save folder set up from the other device's for: {games}",
                                 "もう一方の端末に合わせてセーブフォルダーを設定しました：{games}"),
    "status.startup_uploaded":  ("Uploaded newer saves for: {games}", "新しいセーブをアップロードしました：{games}"),
    "status.title_updated":     ('Title updated to "{title}" from the game window.', "ゲーム画面からタイトルを「{title}」に更新しました。"),
    "status.live_update":       ("Library updated from the other device.", "もう一方の端末の変更を反映しました。"),
    "status.cover_found":       ("Found a cover on VNDB.", "VNDBでカバー画像が見つかりました。"),

    # launch problems
    "play.no_exe":              ("This game's .exe wasn't found on This Device. Check it in Edit.",
                                 'この端末に設定された .exe が見つかりません。「編集」で確認してください。'),
    "play.luna_missing":        ('"Launch With Luna" is on, but Luna isn\'t set up on This Device. Set its location in Settings.',
                                 "「Lunaで起動」がオンですが、この端末ではLunaが設定されていません。設定で場所を指定してください。"),
    "play.luna_unknown_game":   ("Luna doesn't know this game yet. Drag the game's .exe into Luna once, then try again.",
                                 "Lunaにこのゲームがまだ登録されていません。一度ゲームの .exe をLunaにドラッグしてから、もう一度お試しください。"),
    "play.luna_running":         ("Luna is already open. Close it first, so the game can start through it.",
                                 "Lunaがすでに起動しています。ゲームをLuna経由で起動できるよう、先にLunaを閉じてください。"),
    "play.le_missing":          ('"Launch With Locale Emulator" is on, but Locale Emulator isn\'t set up on This Device. Set LEProc.exe in Settings.',
                                 "「Locale Emulatorで起動」がオンですが、この端末ではLocale Emulatorが設定されていません。設定で LEProc.exe を指定してください。"),
    "play.needs_admin":         ('This game needs administrator rights. Close Ikemen Reader, right-click its icon, choose "Run as administrator", then try again.',
                                 'このゲームには管理者権限が必要です。イケメンリーダーを閉じてからアイコンを右クリックし、「管理者として実行」を選んで、もう一度お試しください。'),
    "play.admin_declined":     ("This game needs administrator rights, and permission wasn't given, so it didn't start. "
                                 "Nothing was changed.",
                                 "このゲームには管理者権限が必要ですが、許可されなかったため起動しませんでした。何も変更されていません。"),
    "play.couldnt_start":       ("The game couldn't be started.", "ゲームを起動できませんでした。"),
    "play.cancelled":           ("Stopped waiting for the game. If it opens later, press Read and choose Wait for It to Close.",
                                 "ゲームの起動を待つのをやめました。あとでゲームが開いた場合は、「読む」を押して「終了まで待つ」を選んでください。"),
    "play.luna_failed":         ("Luna closed before the game started. Try starting the game from Luna itself to see what's wrong, or read without Luna.",
                                 "ゲームが起動する前にLunaが終了しました。Lunaから直接ゲームを起動して原因を確認するか、Lunaなしで読んでください。"),
    "sync.skipped_running":     ("The game is open, so its saves weren't changed. Close it, then try again.",
                                 "ゲームが開いているため、セーブは変更しませんでした。ゲームを閉じてから、もう一度お試しください。"),
    "conflict.waiting_title":   ("Saves Changed on Both Devices", "両方の端末でセーブが変更されました"),
    # (_mixed: at least one of the games isn't set up on the other device - true for every game in the list)
    "conflict.waiting_title_mixed": ("Saves Don't Match the Cloud Copy", "セーブがクラウドのコピーと一致しません"),
    "conflict.waiting_body_mixed": ("These games' saves on This Device don't match the copy in the cloud:\n\n{games}\n\n"
                                 "Choose which saves to keep now? Nothing changes until you choose.",
                                 "次のゲームは、この端末のセーブがクラウドのコピーと一致しません：\n\n{games}\n\n"
                                 "今すぐ、どちらのセーブを残すか選びますか？選ぶまで何も変更されません。"),
    "conflict.waiting_body":    ("These games have different saves on each device:\n\n{games}\n\n"
                                 "Choose which saves to keep now? Nothing changes until you choose.",
                                 "次のゲームは、端末ごとにセーブが異なります：\n\n{games}\n\n"
                                 "今すぐ、どちらのセーブを残すか選びますか？選ぶまで何も変更されません。"),
    "conflict.choose_now":      ("Choose Now", "今すぐ選ぶ"),
    "conflict.ask_later":       ("Later", "後で"),
    "menu.sync_now":            ("Sync Now", "今すぐ同期"),
    "play.suggest_title":       ("Save Folder", "セーブフォルダー"),
    "play.suggest_body":         ("{title} has no save folder on This Device yet. It probably saves here:\n\n{path}\n\n{why}\n\nUse this "
                                 "folder? Its saves sync from this session on.",
                                 "{title} には、この端末のセーブフォルダーがまだ設定されていません。おそらく次の場所に保存されます：\n\n{"
                                 "path}\n\n{why}\n\nこのフォルダーを使いますか？今回から、セーブが同期されます。"),
    "play.suggest_other":        ("It's the same place as on the other device, so its saves come across before the game starts.",
                                 "もう一方の端末と同じ場所なので、ゲームの起動前にそのセーブを取り込めます。"),
    "play.suggest_found":       ("An existing save folder for this game was found there.",
                                 "このゲームの既存のセーブフォルダーが見つかりました。"),
    "play.suggest_use":         ("Use This Folder", "このフォルダーを使う"),
    "play.suggest_skip":        ("Find It While Reading", "読みながら探す"),
    "play.never_started":       ("The game didn't start. If Luna shows an error, please check it there.",
                                 'ゲームが起動しませんでした。Lunaにエラーが表示されている場合は、そちらを確認してください。'),
    "play.not_launched_conflict": ("The saves differ between the two devices, so the game wasn't started. Choose which to keep first.",
                                 "両方の端末のセーブが異なるため、ゲームを起動しませんでした。先にどちらを残すか選んでください。"),
    "play.not_launched_conflict_first": ("This device already has save files that don't match the cloud, so the game wasn't started. Choose "
                                 "which to keep first.",
                                 "この端末にクラウドと一致しないセーブファイルがあるため、ゲームを起動しませんでした。先にどちらを残すか選んでくだ"
                                 "さい。"),
    "play.not_launched_conflict_solo": ("This device's saves don't match the cloud copy, so the game wasn't started. Choose which to keep "
                                 "first.",
                                 "この端末のセーブがクラウドのコピーと一致しないため、ゲームを起動しませんでした。先にどちらを残すか選んでください"
                                 "。"),
    "play.detected":            ("Found where the game saves.", "セーブの場所が見つかりました。"),
    "play.detect_none":         ("Couldn't find where the game saves. Did you save at least once?",
                                 "セーブの場所が見つかりませんでした。一度はセーブしましたか？"),
    "play.title":               ("Can't Start the Game", "ゲームを起動できません"),
    "play.title_running":       ("Game Already Running", "ゲームはすでに起動中です"),
    "play.ok":                  ("OK", "OK"),
    "play.retry_without_luna":  ("Read Without Luna", "Lunaなしで読む"),
    "play.retry_without_le":    ("Read Without Locale Emulator", "Locale Emulatorなしで読む"),
    "play.retry":               ("Try Again", "もう一度試す"),
    "play.close_luna":          ("Close Luna", "Lunaを閉じる"),
    "status.closing_luna":      ("Closing Luna...", "Lunaを閉じています…"),
    "status.luna_still_open":   ("Luna didn't close. Please close it yourself, then press Read.",
                                 "Lunaが閉じませんでした。手動で閉じてから「読む」を押してください。"),
    "play.go_settings":         ("Open Settings", "設定を開く"),

    # ------------------------------------------------------------------ folders
    "folder.new_name":          ("New Folder", "新しいフォルダー"),
    "folder.count_one":         ("1 Game", "1ゲーム"),
    "folder.count_many":        ("{n} Games", "{n}ゲーム"),
    "folder.back":              ("‹  Library", "‹  ライブラリ"),
    "folder.back_tip":          ("Back to the library. Drop a game here to take it out of this folder.",
                                 "ライブラリに戻ります。ゲームをここにドロップすると、フォルダーから出せます。"),
    "folder.rename_title":      ("Rename Folder", "フォルダー名を変更"),
    "folder.name":              ("Folder Name", "フォルダー名"),
    "folder.remove_confirm":    ("Remove the folder \"{name}\"?\n\nThe games inside go back to the library. Nothing is deleted.",
                                 "フォルダー「{name}」を解除しますか？\n\n中のゲームはライブラリに戻ります。何も削除されません。"),
    "bar.open":                 ("Open", "開く"),
    "bar.rename":               ("Rename", "名前を変更"),
    "menu.open_folder_tile":    ("Open", "開く"),
    "menu.rename_folder":       ("Rename", "名前を変更"),
    "menu.remove_folder":       ("Remove Folder", "フォルダーを解除"),
    "menu.add_to_folder":       ("Add to Folder", "フォルダーに追加"),
    "menu.new_folder":          ("New Folder", "新しいフォルダー"),
    "menu.remove_from_folder":  ("Remove From Folder", "フォルダーから出す"),

    # ------------------------------------------------------------------ context menu
    "menu.play":                ("Read", "読む"),
    "menu.edit":                ("Edit", "編集"),
    "menu.status":              ("Reading Status", "プレイ状況"),
    "menu.restore":             ("Restore an Earlier Save", "以前のセーブを復元"),
    "menu.open_saves":          ("Open Save Folder", "セーブフォルダーを開く"),
    "menu.open_game":           ("Open Game Folder", "ゲームフォルダーを開く"),

    # ------------------------------------------------------------------ game settings dialog
    "game.title_add":           ("Add Game", "ゲームを追加"),
    "game.title_edit":          ("Game Settings", "ゲーム設定"),
    "game.title":               ("Title", "タイトル"),
    "game.title_auto":          ("Automatic", "自動"),
    "game.title_auto_on_tip":   ("The title is taken automatically from the game's window when you play. "
                                 "Click to switch back to your own title, or type a new one.",
                                 "タイトルはプレイ時にゲーム画面から自動で取得されます。クリックすると、自分で付けたタイトルに戻ります。新しく入力することもできます。"),
    "game.title_auto_off_tip":  ("You've typed your own title. Click to go back to the automatic one.",
                                 "自分で入力したタイトルです。クリックすると、自動のタイトルに切り替わります。"),
    "game.cover":               ("Choose Cover", "カバー画像を選択"),
    "game.no_cover":            ("No Cover", "カバーなし"),
    "game.remove_cover":        ("Remove Cover", "カバーを削除"),
    "game.zoom":                ("Zoom", "ズーム"),
    "game.crop_tip":            ("Drag to move the cover. Scroll or use the slider to zoom. Double-click to reset.",
                                 "ドラッグで位置を調整し、ホイールまたはスライダーで拡大・縮小できます。ダブルクリックで元に戻ります。"),
    "game.vndb":                ("Find on VNDB", "VNDBで探す"),
    "game.vndb_suggest":        ("Found on VNDB: {title}", "VNDBで見つかりました：{title}"),
    "game.vndb_use":            ("Use This", "これを使う"),
    "game.launch":              ("Launch", "起動方法"),
    "game.use_luna":            ("Launch With Luna", "Lunaで起動"),
    "game.use_le":              ("Launch With Locale Emulator", "Locale Emulatorで起動"),
    "game.luna_note":           ("With LunaTranslator, the game's Locale Emulator setting in Luna is used instead.",
                                 'LunaTranslatorの場合は、Luna側でゲームごとに設定したLocale Emulatorの設定が使われます。'),
    "game.status":              ("Reading Status", "プレイ状況"),
    "game.this_device":         ("This Device", "この端末"),
    "game.other_device":        ("Other Device", "もう一方の端末"),
    "game.exe":                 ("Game .exe", "ゲームの .exe"),
    "game.saves":               ("Save Folder", "セーブフォルダー"),
    "game.saves_auto":          ('Leave empty to detect it on first play',
                                 '空欄なら初回プレイ時に自動で検出'),
    "game.saves_from_other":    ("Suggested: the same place as on the other device. Check it, then Save.",
                                 "提案：もう一方の端末と同じ場所です。確認してから保存してください。"),
    "game.saves_found":         ("Suggested: an existing save folder found for this game. Check it, then Save.",
                                 "提案：このゲームの既存のセーブフォルダーが見つかりました。確認してから保存してください。"),
    "game.registry":            ("Registry Saves", "レジストリのセーブ"),
    "game.registry_hint":       ("Only for games that keep saves in the registry. Usually found automatically.",
                                 "レジストリにセーブを保存するゲーム用です。通常は自動で見つかります。"),
    "game.browse":              ("Browse", "参照"),
    "game.other_tip":           ("Path on the Other Device. Browse only works on that device, but you can type or paste it here.",
                                 "もう一方の端末でのパスです。参照はその端末でしか使えませんが、ここに直接入力したり貼り付けたりできます。"),
    "game.saves_hint":          ("The save folder is where the game writes its save files: often in the game folder, or under %APPDATA% or Documents.",
                                 "セーブフォルダーは、ゲームがセーブファイルを書き込む場所です。ゲームフォルダー内や %APPDATA%、ドキュメントの中にあることが多いです。"),
    "game.remove":              ("Remove From Library", "ライブラリから削除"),
    "bar.bookmarks_hide":       ("Hide Bookmarks", "しおりを非表示"),
    "bar.bookmarks_show":       ("Show Bookmarks", "しおりを表示"),
    "bar.trash_tip":            ("Remove From Library (or drag a game here)", "ライブラリから削除（ゲームをここにドラッグしても削除できます）"),
    "game.remove_sure":         ("Are You Sure?", "よろしいですか？"),
    "game.remove_yes":          ("Confirm", "削除する"),
    "game.remove_confirm":      ('Remove "{title}" from the library?\n\nYour save files aren\'t touched.',
                                 '「{title}」をライブラリから削除しますか？\n\nセーブファイルは削除されません。'),
    # set up on the other device too: only here, or on both (owner, 2026-10-17)
    "game.remove_choose":       ('Remove "{title}" from this device, or from both devices?\n\n'
                                 "Your save files aren't touched either way.",
                                 "「{title}」をこの端末から削除しますか？それとも両方の端末から削除しますか？\n\n"
                                 "どちらの場合も、セーブファイルは削除されません。"),
    "game.remove_here":         ("Remove From This Device", "この端末から削除"),
    "game.remove_both":         ("Remove From Both Devices", "両方の端末から削除"),
    "game.save":                ("Save", "保存"),
    "game.cancel":              ("Cancel", "キャンセル"),
    "game.exe_missing":         ("This Device's .exe wasn't found:\n{path}", "この端末の .exe が見つかりません：\n{path}"),
    "add.existing":             ("\"{title}\" is already in your library from the other device. Set it up on this device "
                                 "so both devices share the same saves?",
                                 "「{title}」は、もう一方の端末からすでにライブラリに追加されています。この端末でも設定して、"
                                 "同じセーブを共有しますか？"),
    "add.set_up":               ("Set Up on This Device", "この端末で設定する"),
    "add.as_new":               ("Add as a New Game", "別のゲームとして追加"),
    "game.already_added":       ('This game is already in your library as "{title}".', "このゲームはすでに「{title}」としてライブラリにあります。"),
    "game.not_exe":             ("That isn't a program (.exe) or a shortcut to one.",
                                 'そのファイルはプログラム（.exe）でも、そのショートカットでもありません。'),
    "error.title_empty":        ("The title can't be empty.", "タイトルを入力してください。"),
    "error.title_taken":        ("Another game already has that title.", "同じタイトルのゲームがすでにあります。"),
    "error.save_empty":         ("No save folder is set.", "セーブフォルダーが設定されていません。"),
    "error.save_relative":      ("The save folder must be a full path, like C:\\Users\\...\\Saves.",
                                 "セーブフォルダーは C:\\Users\\...\\Saves のような完全なパスで指定してください。"),
    "error.save_file":          ("That save location is a file, not a folder.", "そのセーブの場所はフォルダーではなくファイルです。"),
    "error.save_broad":         ("That folder is far too big to be a game's save folder (a whole drive, Documents, AppData and so on). Syncing makes the folder match the other device exactly, so everything else in it would be removed. Please choose the game's own save folder inside it.",
                                 'そのフォルダーはゲームのセーブフォルダーとしては大きすぎます（ドライブ全体、ドキュメント、AppData など）。同期するとフォルダーの中身がもう一方の端末と完全に同じになるため、それ以外のファイルは消えてしまいます。その中にある、ゲーム専用のセーブフォルダーを選んでください。'),
    "error.save_cloud":         ("The save folder can't be the cloud folder or contain it, because the saves would be copied into themselves.",
                                 'セーブフォルダーには、クラウドフォルダー自体やそれを含むフォルダーは選べません（セーブが自分自身の中にコピーされてしまいます）。'),
    "error.save_shared":        ('"{title}" already uses this save folder on This Device. If two games sync the same folder, they can overwrite each other\'s saves. Use it anyway?',
                                 'このセーブフォルダーは、この端末ですでに「{title}」が使っています。2つのゲームで同じフォルダーを同期すると、お互いのセーブを上書きしてしまう恐れがあります。それでも使いますか？'),
    "error.cloud_write":        ("Couldn't finish that because a file is in use or the cloud folder isn't available right now. "
                                 "Nothing was lost. Please check that your cloud service's app is running, then try again.",
                                 "ファイルが使用中か、クラウドフォルダーが現在使用できないため、完了できませんでした。何も失われていません。"
                                 "クラウドサービスのアプリが起動しているか確認してから、もう一度お試しください。"),
    "error.game_gone":          ("This game is no longer in the library. It may have just been removed on the other device.",
                                 "このゲームはライブラリにありません。もう一方の端末で削除された可能性があります。"),
    "error.unexpected":         ("Something unexpected went wrong, but nothing was lost. The details were saved to:\n{path}",
                                 "予期しない問題が発生しましたが、何も失われていません。詳細は次の場所に保存されました：\n{path}"),
    "error.use_anyway":         ("Use Anyway", "このまま使う"),
    "error.save_shared_title":  ("Save Folder Already in Use", "このセーブフォルダーは使用中です"),
    "sync.failed":               ("Couldn't sync the saves because a file is in use or can't be written. Nothing was lost, and the next "
                                 "sync tries again.",
                                 "ファイルが使用中か書き込めないため、セーブを同期できませんでした。何も失われていません。次の同期でもう一度試しま"
                                 "す。"),
    "play.already_running":     ("This game is already running outside Ikemen Reader. When you close it, it starts here.\n\n"
                                 "Force Close ends it right away (anything you haven't saved in the game will be lost). "
                                 "Wait for It to Close keeps it running, and the saves sync when you close it.",
                                 "このゲームはイケメンリーダーの外ですでに起動しています。ゲームを閉じると、ここで起動します。\n\n"
                                 "「強制終了」を選ぶと、すぐに終了します（ゲーム内でセーブしていない内容は失われます）。"
                                 "「終了まで待つ」を選ぶと、そのまま遊べて、閉じたときにセーブが同期されます。"),
    "play.already_running_luna": ("This game is already running through Luna. When you close it, Luna closes too, and the "
                                 "game starts here.\n\n"
                                 "Force Close ends it right away (anything you haven't saved in the game will be lost). "
                                 "Wait for It to Close keeps it running, and the saves sync when you close it.",
                                 "このゲームはLuna経由ですでに起動しています。ゲームを閉じると、Lunaも閉じて、ここでゲームが"
                                 "始まります。\n\n"
                                 "「強制終了」を選ぶと、すぐに終了します（ゲーム内でセーブしていない内容は失われます）。"
                                 "「終了まで待つ」を選ぶと、そのまま遊べて、閉じたときにセーブが同期されます。"),
    "play.watch":               ('Wait for It to Close',
                                 '終了まで待つ'),
    "close.syncing":            ('Your saves are syncing right now. Please wait a moment, then close Ikemen Reader.',
                                 'セーブを同期しています。少し待ってから、イケメンリーダーを閉じてください。'),
    "error.registry":           ("That registry location isn't allowed. Only keys under HKEY_CURRENT_USER\\Software\\<maker>\\<game> can be synced.",
                                 "そのレジストリの場所は使えません。同期できるのは HKEY_CURRENT_USER\\Software\\<メーカー>\\<ゲーム> 以下のキーだけです。"),

    # ------------------------------------------------------------------ VNDB dialog
    "vndb.title":               ("Find on VNDB", "VNDBで探す"),
    "vndb.search":              ("Search", "検索"),
    "vndb.searching":           ("Searching...", "検索中…"),
    "vndb.none":                ('Nothing found. Try a different spelling (Japanese titles usually work best).',
                                 '見つかりませんでした。別の表記で試してください（日本語のタイトルで検索すると見つかりやすいです）。'),
    "vndb.error":               ("Couldn't reach VNDB. Check your internet connection.", "VNDBに接続できません。インターネット接続を確認してください。"),
    "vndb.no_safe_cover":       ("(No Cover)", "（カバーなし）"),
    "vndb.use":                 ("Use Cover and Title", "カバーとタイトルを使う"),
    "vndb.use_cover":           ("Use Cover Only", "カバーだけ使う"),
    "vndb.pick_cover":          ("Choose a Cover", "カバーを選択"),
    "vndb.pick_body":           ("VNDB has {n} covers for <b>{title}</b>. Pick the one you like.",
                                 "VNDBには <b>{title}</b> のカバーが{n}枚あります。好きなものを選んでください。"),
    "vndb.downloading":         ("Downloading the cover...", "カバー画像をダウンロード中…"),
    "vndb.loading_covers":      ("Loading covers...", "カバーを読み込み中…"),
    "vndb.cover_main":          ("Main Cover", "メインカバー"),
    "vndb.cover_pkgfront":      ("Package", "パッケージ"),
    "vndb.cover_dig":           ("Digital", "ダウンロード版"),
    "vndb.use_selected":        ("Use This Cover", "このカバーを使う"),

    # ------------------------------------------------------------------ conflict dialog
    "conflict.title":           ("Which Saves Do You Want to Keep?", "どちらのセーブを残しますか？"),
    "conflict.body":            ('<b>{title}</b> was played on both devices since the last sync, so their saves are different.',
                                 '<b>{title}</b> は前回の同期以降に両方の端末でプレイされたため、セーブが異なります。'),
    # (the game isn't set up on the other device: the cloud copy can only have been uploaded from this one)
    "conflict.body_first":      ("This device hasn't synced <b>{title}</b> before, but it already has save files of its own (some "
                                 "games create them when installed or first opened). The cloud has the saves from the other device.",
                                 "この端末では <b>{title}</b> をまだ同期したことがありませんが、すでにセーブファイルがあります（インストール"
                                 "時や初回起動時にファイルを作るゲームもあります）。クラウドには、もう一方の端末のセーブがあります。"),
    "conflict.body_solo":       ("<b>{title}</b>'s saves on This Device are different from the copy in the cloud. The game isn't "
                                 "set up on the other device, so that copy was uploaded from This Device earlier.",
                                 "この端末にある <b>{title}</b> のセーブが、クラウドにあるコピーと異なります。もう一方の端末では"
                                 "設定されていないため、そのコピーは以前この端末からアップロードされたものです。"),
    "conflict.cloud_solo":      ("Keep the Saves From the Cloud (Uploaded From This Device Earlier)",
                                 "クラウドのセーブを残す（以前この端末からアップロードしたもの）"),
    "conflict.local":           ("Keep This Device's Saves", "この端末のセーブを残す"),
    "conflict.cloud":           ("Keep the Saves From the Cloud (Other Device)", "クラウドのセーブを残す（もう一方の端末）"),
    "conflict.last_saved":      ('Last Saved: {date}',
                                 '最終セーブ：{date}'),
    "conflict.newer":           ("(Newer)", "（新しい方）"),
    "conflict.none":            ("No Saves", "セーブなし"),
    "conflict.backup_note":     ("Whichever saves you don't keep are backed up on This Device first, so nothing is lost.",
                                 '残さなかった方のセーブは、先にこの端末にバックアップされます。何も失われません。'),
    "conflict.later":           ("Decide Later (Don't Change Anything)", "後で決める（何も変更しない）"),

    # ------------------------------------------------------------------ detection dialog
    "detect.title":             ("Where Does This Game Save?", "このゲームのセーブはどこにありますか？"),
    "detect.body_one":          ('While you played <b>{title}</b>, files changed in this location. Is this where the game saves?',
                                 '<b>{title}</b> のプレイ中に、この場所のファイルが変更されました。ここがセーブの場所ですか？'),
    "detect.body_many":         ('While you played <b>{title}</b>, files changed in these locations. Which one is where the game saves?',
                                 '<b>{title}</b> のプレイ中に、次の場所のファイルが変更されました。どれがセーブの場所ですか？'),
    "detect.files":             ('Files Changed: {n}',
                                 '変更されたファイル：{n}個'),
    "detect.registry":          ("Registry: {key}", "レジストリ：{key}"),
    "detect.section_saves":     ("Save Folder", "セーブフォルダー"),
    "detect.section_registry":  ("Registry", "レジストリ"),
    "detect.registry_auto":     ("This game also keeps data in the registry, so it will be synced too: {key}",
                                 "このゲームはレジストリにもデータを保存しているため、それも同期します：{key}"),
    "detect.registry_tip":      ('This game also wrote to the registry. Tick the box to sync that too.',
                                 'このゲームはレジストリにも書き込んでいます。そちらも同期する場合は、チェックを入れてください。'),
    "detect.browse":            ("Somewhere Else", "別の場所"),
    "detect.use":               ("Use This", "これを使う"),
    "detect.later":             ("Ask Me Next Time", "次回また確認する"),
    "detect.none_body":          ("Couldn't tell where <b>{title}</b> saves. If you didn't save this time, it looks again the next time "
                                 "you read. You can also choose the folder yourself.",
                                 "<b>{title}</b> のセーブの場所を特定できませんでした。今回セーブしていなかった場合は、次に読むとき"
                                 "にもう一度探します。自分でフォルダーを選ぶこともできます。"),
    "detect.registry_only":     ("Registry Only (No Save Folder)", "レジストリのみ（セーブフォルダーなし）"),

    # ------------------------------------------------------------------ restore dialog
    "restore.title":            ("Restore an Earlier Save", "以前のセーブを復元"),
    "restore.body":             ("Choose a backup of <b>{title}</b>'s saves to restore. Your current saves are backed up first, so you can undo this the same way.",
                                 '<b>{title}</b> のセーブを、どのバックアップから復元するか選んでください。現在のセーブは先にバックアップされるので、同じ方法で元に戻せます。'),
    "restore.none":              ("No backups yet. One is made automatically whenever saves are about to be replaced.",
                                 "バックアップはまだありません。セーブが置き換えられる前に、自動で作成されます。"),
    "restore.reason.local-before-pull":   ("Before Downloading Newer Saves", "新しいセーブをダウンロードする前"),
    "restore.reason.cloud-before-push":   ("Cloud Copy Before Uploading", "アップロード前のクラウドのセーブ"),
    "restore.reason.local-before-restore": ("Before a Restore", "復元する前"),
    "restore.restore":          ("Restore", "復元"),
    "restore.done":             ("Restored and uploaded.", "復元してアップロードしました。"),
    "restore.confirm":          ("Replace {title}'s current saves with the backup from {date}?",
                                 '{title} の現在のセーブを、{date} のバックアップで置き換えますか？'),

    # ------------------------------------------------------------------ settings
    "settings.title":           ("Settings", "設定"),
    "settings.welcome":         ("Welcome! Let's Get You Set Up",
                                 'ようこそ！まずは簡単な設定から'),
    "settings.language":        ("Language", "言語"),
    "settings.font":            ("Font", "フォント"),
    "settings.isync":           ("Ikemen Sync", "イケメン同期"),
    "egg.title":                ("Easter Egg ^_^", "イースターエッグ ^_^"),
    "egg.anki":                 ("Anki", "暗記"),
    "egg.anki_failed":          ("Anki couldn't be opened:\n{error}", "Ankiを開けませんでした：\n{error}"),
    "settings.about":           ("About", "情報"),
    "guide.button":             ("Help and Guidance", "ヘルプとガイド"),
    "guide.title":              ("Help and Guidance", "ヘルプとガイド"),
    "guide.close":              ("Close", "閉じる"),
    "isync.button":             ("Device Settings Sync", "端末設定の同期"),
    "isync.tip":                ("Copy the theme, fonts, background and other preferences from the other device.",
                                 "もう一方の端末から、テーマ・フォント・背景などの設定をコピーします。"),
    "isync.confirm_title":      ("Copy Settings From the Other Device Connected to Ikemen?",
                                 "イケメンに接続しているもう一方の端末から設定をコピーしますか？"),
    "isync.confirm":            ("This replaces this device's look and preferences with those of the other device connected to Ikemen.\n"
                                 "Last Shared: {when}",
                                 "この端末の見た目と設定を、イケメンに接続しているもう一方の端末のものに置き換えます。\n"
                                 "最終共有：{when}"),
    "isync.go":                 ("Sync", "同期する"),
    "isync.none":               ("There is no other device connected to Ikemen yet.\n\nOpen Ikemen Reader on another device once "
                                 "(this version or newer) and let the cloud folder finish syncing, then try again.",
                                 "イケメンに接続している他の端末はまだありません。\n\n別の端末でイケメンリーダー（このバージョン以降）を"
                                 "一度起動し、クラウドフォルダーの同期が終わってから、もう一度お試しください。"),
    "isync.no_cloud":           ('Please choose a cloud folder first. Settings are shared through it.',
                                 '先にクラウドフォルダーを選んでください。設定はこのフォルダーを通して共有されます。'),
    "isync.done":               ("Done! This device now uses the settings from the other device connected to Ikemen.",
                                 "完了しました。この端末は、イケメンに接続しているもう一方の端末の設定になりました。"),
    "isync.font_missing":       ("These fonts couldn't be copied, so this device keeps its own: {fonts}",
                                 "次のフォントはコピーできなかったため、この端末のフォントのままです：{fonts}"),
    "font.btn_en":              ("English", "英語"),
    "font.btn_ja":              ("日本語", "日本語"),
    "font.title_en":            ("English Font", "英語のフォント"),
    "font.title_ja":            ("Japanese Font", "日本語のフォント"),
    "font.hint_en":             ("Used when the interface is in English. Any character it doesn't have comes from the Japanese font.",
                                 "英語表示のときに使われます。このフォントにない文字は、日本語のフォントで表示されます。"),
    "font.hint_ja":             ("Used when the interface is in Japanese. Any character it doesn't have comes from the English font.",
                                 "日本語表示のときに使われます。このフォントにない文字は、英語のフォントで表示されます。"),
    "font.add":                 ("Add Font", "フォントを追加"),
    "font.pick":                ("Choose a Font File", "フォントファイルを選択"),
    "font.files":               ("Fonts (*.ttf *.otf *.ttc)", "フォント (*.ttf *.otf *.ttc)"),
    "font.bad":                 ("That file couldn't be loaded as a font.", "そのファイルはフォントとして読み込めませんでした。"),
    "font.builtin":             ("Built In", "標準"),
    "font.user":                ("User Added", "追加したフォント"),
    "font.sample_en":           ("The quick brown fox jumps over the lazy dog. 0123456789",
                                 "The quick brown fox jumps over the lazy dog. 0123456789"),
    "font.sample_ja":           ("いろはにほへと ちりぬるを サクラノ刻 0123456789", "いろはにほへと ちりぬるを サクラノ刻 0123456789"),
    "lang.en":                  ("English", "英語"),
    "lang.ja":                  ("日本語", "日本語"),
    "settings.device":          ("This Is", "この端末は"),
    "settings.device_n":        ("Device {n}", "端末{n}"),
    "settings.device_hint":     ("Choose Device 1 on one computer and Device 2 on the other. That's how Ikemen Reader tells them apart.",
                                 '片方のパソコンでは端末1、もう片方では端末2を選んでください。イケメンリーダーはこれで端末を区別します。'),
    "settings.device_taken":    ('Device {n} is already used by another computer ({computer}). Use Device {other} instead?',
                                 '端末{n}はすでに別のパソコン（{computer}）で使われています。端末{other}に変更しますか？'),
    "settings.device_taken_keep": ("Use Device {n} Anyway", "このまま端末{n}を使う"),
    "settings.device_use":      ("Use Device {n}", "端末{n}を使う"),
    "settings.device_taken_title": ("Device Number Already Used", "この端末番号は使用中です"),
    "settings.cloud":           ("Cloud Folder", "クラウドフォルダー"),
    "settings.cloud_hint":      ('Any folder that a cloud service of your choice keeps in sync. Choose the same folder on both devices.',
                                 'お好みのクラウドサービスで同期されるフォルダーなら、どれでも使えます。両方の端末で同じフォルダーを選んでください。'),
    "settings.cloud_placeholder": ("A folder synced by your cloud service", "クラウドサービスで同期されるフォルダー"),
    "settings.le":              ("Locale Emulator (Optional)", "Locale Emulator（任意）"),
    "settings.le_placeholder":  ("LEProc.exe on This Device", "この端末の LEProc.exe"),
    "settings.luna":            ("Luna (Optional)", "Luna（任意）"),
    "settings.luna_placeholder": ("LunaTranslator.exe (or a LunaHook program) on This Device",
                                  "この端末の LunaTranslator.exe（またはLunaHook）"),
    "settings.luna_hint":       ('LunaTranslator is recommended: it includes LunaHook and can start games by itself.',
                                 'LunaTranslatorがおすすめです。LunaHookが含まれていて、ゲームを直接起動できます。'),
    "settings.luna_admin":      ('Please choose LunaTranslator.exe rather than the _admin version, so Ikemen Reader can see the game.',
                                 'イケメンリーダーがゲームを認識できるよう、_admin版ではなく LunaTranslator.exe を選んでください。'),
    "settings.anki":            ("Anki (Optional)", "Anki（任意）"),
    "settings.anki_placeholder": ("anki.exe on This Device", "この端末の anki.exe"),
    "settings.anki_hint":       ("You may be rewarded for hunting...", "探してみると、いいことがあるかも…"),
    "settings.display":         ("Theme", "テーマ"),
    "settings.accessibility":   ("Accessibility", "アクセシビリティ"),
    "settings.background":      ("Custom Library Background", "ライブラリの背景をカスタマイズ"),
    "settings.background_head": ("Background", "背景"),
    "bg.title":                 ("Library Background", "ライブラリの背景"),
    "bg.choose":                ("Choose Image", "画像を選択"),
    "bg.change":                ("Change Image", "画像を変更"),
    "bg.remove":                ("Remove Image", "画像を削除"),
    "bg.opacity":               ("Opacity", "不透明度"),
    "bg.zoom":                  ("Zoom", "ズーム"),
    "bg.crop_tip":              ("Drag to move the picture. Scroll or use the Zoom slider to zoom. Double-click to reset.",
                                 "ドラッグで位置を調整し、ホイールまたはズームのスライダーで拡大・縮小できます。ダブルクリックで元に戻ります。"),
    "bg.none":                  ("No background image. Choose one to show it behind your library.",
                                 "背景画像はありません。画像を選ぶと、ライブラリの後ろに表示されます。"),
    "bg.bad_image":             ("That file couldn't be opened as an image.", "そのファイルは画像として開けませんでした。"),
    "theme.light":              ("Light", "ライト"),
    "theme.dark":               ("Dark", "ダーク"),
    "theme.custom":             ("Custom", "カスタム"),
    "theme.custom_tip":         ("Choose your own colours", "自分で色を選ぶ"),
    "theme.title":              ("Custom Theme", "カスタムテーマ"),
    "theme.start_from":         ("Start From", "元にするテーマ"),
    "theme.start_tip":          ("Replace every colour with this theme's colours as a starting point.",
                                 'すべての色をこのテーマの色に置き換えて、そこから調整します。'),
    "theme.pick":               ("Choose a Colour", "色を選択"),
    "theme.more":               ("Show All Colours", "すべての色を表示"),
    "theme.less":               ("Show Fewer Colours", "主な色だけ表示"),
    "theme.swatch_tip":         ("Click to choose a colour", "クリックして色を選ぶ"),
    "theme.hex_tip":            ("Colour code, e.g. #3B3B3B", "カラーコード（例：#3B3B3B）"),
    "theme.g_basics":           ("Basics", "基本"),
    "theme.g_highlights":       ("Highlights", "アクセント"),
    "theme.g_fields":           ("Text Boxes & Borders", "入力欄と枠線"),
    "theme.g_states":           ("Button States", "ボタンの状態"),
    "theme.hint":               ('Click a swatch to choose a colour, or type a colour code. The window behind updates as you go.',
                                 '色見本をクリックして選ぶか、カラーコードを入力してください。後ろのウィンドウにすぐ反映されます。'),
    "theme.low_contrast":       ("Some text may be hard to read with these colours: {where}.",
                                 "この配色では一部の文字が読みにくいかもしれません：{where}"),
    "theme.where_main":         ("text on the main area", "メインエリアの文字"),
    "theme.where_bars":         ("text on the bars", "バーの文字"),
    "theme.where_play":         ("the Read button", "「読む」ボタン"),
    "theme.where_buttons":      ("text on buttons", "ボタンの文字"),
    "theme.g_backgrounds":      ("Backgrounds", "背景"),
    "theme.g_text":             ("Text", "文字"),
    "theme.g_buttons":          ("Buttons", "ボタン"),
    "theme.g_play":             ("Read Button", "「読む」ボタン"),
    "theme.g_covers":           ("Covers", "カバー"),
    "theme.g_lights":           ("Sync Lights", "同期ランプ"),
    "theme.g_bookmarks":        ("Bookmarks", "しおり"),
    "theme.g_logo":             ("Logo & Icons", "ロゴとアイコン"),
    "theme.c_bg":               ("Main Area", "メインエリア"),
    "theme.c_band":             ("Top & Bottom Bars", "上下のバー"),
    "theme.c_input":            ("Text Boxes & Menus", "入力欄とメニュー"),
    "theme.c_other_bg":         ("Other Device Fields", "もう一方の端末の欄"),
    "theme.c_text":             ("Text", "文字"),
    "theme.c_muted":            ("Secondary Text", "補足の文字"),
    "theme.c_other_text":       ("Other Device Text", "もう一方の端末の文字"),
    "theme.c_danger":           ("Warnings & Remove", "警告と削除"),
    "theme.c_button":           ("Buttons", "ボタン"),
    "theme.c_button_hover":     ("Buttons (Hover)", "ボタン（ホバー時）"),
    "theme.c_border":           ("Borders", "枠線"),
    "theme.c_focus":            ("Focus Outline", "選択中の入力欄の枠"),
    "theme.c_play":             ("Read Button", "「読む」ボタン"),
    "theme.c_play_hover":       ("Read Button (Hover)", "「読む」ボタン（ホバー時）"),
    "theme.c_play_text":        ("Read Button Text", "「読む」ボタンの文字"),
    "theme.c_play_disabled":    ("Read Button (Unavailable)", "「読む」ボタン（無効時）"),
    "theme.c_play_disabled_text":("Read Button Text (Unavailable)", "「読む」ボタンの文字（無効時）"),
    "theme.c_select":           ("Selection Ring", "選択中のカバーの枠"),
    "theme.c_placeholder_top":  ("Covers Without Art (Top)", "画像なしのカバー（上）"),
    "theme.c_placeholder_bottom": ("Covers Without Art (Bottom)", "画像なしのカバー（下）"),
    "theme.c_light_green":      ("In Sync", "同期済み"),
    "theme.c_light_blue":       ("Syncing / Newer in the Cloud", "同期中／クラウドの方が新しい"),
    "theme.c_light_amber":      ("Newer on This Device", "この端末の方が新しい"),
    "theme.c_light_red":        ("Needs Attention", "確認が必要"),
    "theme.c_light_grey":       ("No Saves Yet / Checking", "セーブなし／確認中"),
    "theme.c_light_yellow":     ("Not Set Up on This Device", "この端末で未設定"),
    "theme.c_light_lavender":   ("In Sync, Set Up on This Device Only", "同期済み（この端末のみ設定済み）"),
    "theme.c_mark_finished":    ("Finished", "読了"),
    "theme.c_mark_reading":     ("Reading", "プレイ中"),
    "theme.c_mark_plan":        ("Plan to Read", "積みゲー"),
    "theme.c_mark_dropped":     ("Dropped", "中断"),
    "theme.c_mark_border":      ("Bookmark Border", "しおりの枠線"),
    "theme.c_logo_fill":        ('Logo Fill',
                                 'ロゴの塗りの色'),
    "theme.c_logo_outline":     ("Logo Outline", "ロゴの縁取りの色"),
    "settings.startup":         ("Launch Program on Startup", "Windowsの起動時に自動で起動"),
    "settings.pulse":           ("Pulsing Sync Lights", "同期ランプの点滅"),
    "settings.auto_update":     ("Install Updates Automatically", "アップデートを自動でインストール"),
    "settings.discord":         ("Show Activity on Discord", "Discordにアクティビティを表示"),
    # Discord presence (the status on the owner's Discord profile; owner, 2026-10-17)
    "presence.browsing":        ("Browsing...", "ライブラリを閲覧中…"),
    "presence.reading":         ("Reading...", "読書中…"),
    "presence.tweaking":        ("Tweaking...", "設定中…"),
    "presence.away":            ("Away", "離席中"),
    "presence.coding":          ("Coding...", "コーディング中…"),     # running from the project folder (the owner)
    "presence.games":           ("{n} Games in Library", "ライブラリに{n}ゲーム"),
    "update.available_title":   ("Update Available", "アップデートがあります"),
    "update.available":         ("Ikemen Reader {version} is available. You have {current}.",
                                 "イケメンリーダー {version} が利用できます（現在のバージョン：{current}）。"),
    "update.now":               ("Update Now", "今すぐアップデート"),
    "update.later":             ("Later", "後で"),
    "update.downloading":       ("Downloading Ikemen Reader {version}... {done} / {total} MB",
                                 "イケメンリーダー {version} をダウンロード中… {done} / {total} MB"),
    "update.ready_title":       ("Update Ready", "アップデートの準備ができました"),
    "update.ready":              ("Ikemen Reader {version} is ready. Restart now to use it, or it installs the next time you close "
                                 "Ikemen Reader.",
                                 "イケメンリーダー {version} の準備ができました。今すぐ再起動すると使えます。再起動しない場合は、次にイ"
                                 "ケメンリーダーを閉じたときにインストールされます。"),
    "update.restart":           ("Restart Now", "今すぐ再起動"),
    "update.failed":             ("Couldn't download the update right now. It tries again the next time you open Ikemen Reader.",
                                 "アップデートをダウンロードできませんでした。次にイケメンリーダーを起動したときに、もう一度試します。"),
    "update.damaged":            ("The downloaded update was damaged, so it wasn't installed. It tries again the next time you open "
                                 "Ikemen Reader.",
                                 "ダウンロードしたアップデートが破損していたため、インストールしませんでした。次にイケメンリーダーを起動したときに"
                                 "、もう一度試します。"),
    "update.wait_game":         ("The update will be installed when you close Ikemen Reader (not while a game is "
                                 "running or saves are syncing).",
                                 "アップデートはイケメンリーダーを閉じたときにインストールされます（ゲームの実行中や"
                                 "セーブの同期中は行いません）。"),
    "update.done":              ("Updated to Ikemen Reader {version}.", "イケメンリーダー {version} にアップデートしました。"),
    "update.not_packaged":      ("Updates install themselves only in the packaged program (Ikemen Reader.exe).",
                                 "アップデートの自動インストールは、パッケージ版（Ikemen Reader.exe）でのみ行えます。"),
    "settings.vndb":            ("Automatically Fetch Covers and Titles From VNDB", "VNDBからカバー画像とタイトルを自動取得"),
    "settings.vndb_safe":       ("Safe Covers Only (Hide Covers VNDB Marks as Explicit)",
                                 "安全なカバーのみ（VNDBで成人向けとされるカバーを除外）"),
    "settings.save":            ("Save", "保存"),
    "settings.cancel":          ("Cancel", "キャンセル"),
    "settings.need_cloud":      ("Please choose a cloud folder.", "クラウドフォルダーを選んでください。"),
    "settings.bad_file":        ("That path doesn't point to a program:\n{path}", "そのパスはプログラムを指していません：\n{path}"),
    "settings.bad_cloud":       ("Can't use that cloud folder:\n{error}", "そのクラウドフォルダーは使えません：\n{error}"),
    "settings.close_game":      ("Close the game first, then change settings.", "先にゲームを終了してから、設定を変更してください。"),
    "settings.pick_cloud":      ('Choose the Cloud Folder',
                                 'クラウドフォルダーを選択'),
    "settings.pick_anki":       ("Find Anki", "Ankiを探す"),
    "settings.pick_le":         ("Find LEProc.exe", "LEProc.exe を探す"),
    "settings.pick_luna":       ("Find Luna", "Lunaを探す"),

    # ------------------------------------------------------------------ misc
    "close.title":              ("Game Still Running", "ゲームが起動中です"),
    "close.continue":           ("Close Anyway", "それでも閉じる"),
    "close.body":                ("{title} is still running. If you close Ikemen Reader now, this session's reading time isn't "
                                 "recorded, and the saves upload the next time you open it.\n\nClose anyway?",
                                 "{title} はまだ起動中です。今イケメンリーダーを閉じると、今回の読書時間は記録されず、セーブは次に起動した"
                                 "ときにアップロードされます。\n\nそれでも閉じますか？"),
    "file.programs":            ("Programs (*.exe *.lnk)", "プログラム (*.exe *.lnk)"),
    "file.pick_exe":            ("Choose the Game's .exe", "ゲームの .exe を選択"),
    "file.pick_saves":          ("Choose the Save Folder", "セーブフォルダーを選択"),
    "file.pick_cover":          ("Choose a Cover Image", "カバー画像を選択"),
    "file.images":              ("Images (*.png *.jpg *.jpeg *.webp *.bmp)", "画像 (*.png *.jpg *.jpeg *.webp *.bmp)"),
    "time.h_m":                 ("{h}h {m:02d}m", "{h}時間{m}分"),
    "time.m":                   ("{m}m", "{m}分"),
    "time.s":                   ("{s}s", "{s}秒"),
    "date.format":              ("%b %d, %Y", "%Y年%m月%d日"),
    "date.today":               ("Today", "今日"),
    "date.yesterday":           ("Yesterday", "昨日"),
    "date.days_ago":            ("{n} Days Ago", "{n}日前"),
    "date.weeks_ago":           ("{n} Weeks Ago", "{n}週間前"),
    "datetime.format":          ("%Y-%m-%d %H:%M", "%Y年%m月%d日 %H:%M"),

    # ------------------------------------------------------------------ status line (the strip at the very bottom)
    # Short versions ("<key>:line") of messages that also appear there: the full text belongs in pop-ups and tips; the
    # one-line strip only says what happened (owner: the long ones didn't fit the window). See line().
    "play.no_exe:line":          ("The game's .exe wasn't found on This Device.", "この端末でゲームの .exe が見つかりません。"),
    "play.luna_missing:line":    ("Luna isn't set up on This Device.", "この端末ではLunaが設定されていません。"),
    "play.luna_unknown_game:line": ("Luna doesn't know this game yet.", "Lunaにこのゲームがまだ登録されていません。"),
    "play.luna_running:line":    ("Luna is already open.", "Lunaがすでに起動しています。"),
    "play.le_missing:line":      ("Locale Emulator isn't set up on This Device.", "この端末ではLocale Emulatorが設定されていません。"),
    "play.needs_admin:line":     ("The game needs administrator rights.", "このゲームには管理者権限が必要です。"),
    "play.admin_declined:line":  ("Not started: permission wasn't given.", "許可されなかったため、起動しませんでした。"),
    "play.cancelled:line":       ("Stopped waiting for the game.", "ゲームの起動を待つのをやめました。"),
    "play.luna_failed:line":     ("Luna closed before the game started.", "ゲームの起動前にLunaが終了しました。"),
    "play.never_started:line":   ("The game didn't start.", "ゲームが起動しませんでした。"),
    "play.already_running:line": ("The game is already running outside Ikemen Reader.",
                                  "ゲームはイケメンリーダーの外ですでに起動しています。"),
    "play.already_running_luna:line": ("The game is already running through Luna.",
                                  "ゲームはLuna経由ですでに起動しています。"),
    "play.not_launched_conflict:line": ("Not started: choose which saves to keep first.",
                                  "起動しませんでした：先に残すセーブを選んでください。"),
    "play.not_launched_conflict_first:line": ("Not started: choose which saves to keep first.",
                                  "起動しませんでした：先に残すセーブを選んでください。"),
    "play.not_launched_conflict_solo:line": ("Not started: choose which saves to keep first.",
                                  "起動しませんでした：先に残すセーブを選んでください。"),
    "play.detect_none:line":     ("Couldn't find where the game saves.", "セーブの場所が見つかりませんでした。"),
    "sync.failed:line":          ("Couldn't sync the saves. Trying again next time.", "セーブを同期できませんでした。次回もう一度試します。"),
    "sync.conflict_unresolved:line": ("The saves differ, so nothing was changed.", "セーブが異なるため、何も変更しませんでした。"),
    "sync.conflict_unresolved_first:line": ("The saves differ, so nothing was changed.", "セーブが異なるため、何も変更しませんでした。"),
    "sync.conflict_unresolved_solo:line": ("The saves differ, so nothing was changed.", "セーブが異なるため、何も変更しませんでした。"),
    "error.save_broad:line":     ("That save folder can't be used. Check it in Edit.", "そのセーブフォルダーは使えません。「編集」で確認してください。"),
    "error.save_cloud:line":     ("That save folder can't be used. Check it in Edit.", "そのセーブフォルダーは使えません。「編集」で確認してください。"),
    "error.save_relative:line":  ("That save folder can't be used. Check it in Edit.", "そのセーブフォルダーは使えません。「編集」で確認してください。"),
    "status.now_custom:line":    ("Switched to My Arrangement.", "「自分の並び」に切り替えました。"),
    "status.title_updated:line": ('Title updated to "{title}".', "タイトルを「{title}」に更新しました。"),
    "status.n_games":            ("{n} games", "{n}ゲーム"),
}


def set_language(lang: str) -> None:
    global _lang
    _lang = "ja" if lang == "ja" else "en"


def language() -> str:
    return _lang


def tr(key: str, /, **kw) -> str:
    pair = S.get(key)
    if pair is None:
        return key
    text = pair[1] if _lang == "ja" else pair[0]
    return text.format(**kw) if kw else text


def line(key: str, /, **kw) -> str:
    """The text for the status line: the short "<key>:line" version when there is one, else the usual text."""
    return tr(key + ":line", **kw) if key + ":line" in S else tr(key, **kw)


def games_list(titles: list[str]) -> str:
    """Game titles for the status line: one or two by name, more as a count (a long list ran off the window)."""
    if len(titles) <= 2:
        return ("、" if _lang == "ja" else ", ").join(titles)
    return tr("status.n_games", n=len(titles))


def playtime(seconds: int) -> str:
    seconds = int(seconds)
    if 0 < seconds < 60:
        return tr("time.s", s=seconds)
    h, rem = divmod(seconds, 3600)
    if h == 0:
        return tr("time.m", m=rem // 60)        # under an hour: minutes only
    return tr("time.h_m", h=h, m=rem // 60)


def playtime_parts(seconds: float) -> list[tuple[str, str]]:
    """Playtime as (number, unit) pairs, for drawing the numbers bright and the units small:
    [("12", "h"), ("05", "m")] / [("12", "時間"), ("5", "分")]. Same rules as playtime()."""
    seconds = int(seconds)
    ja = _lang == "ja"
    if 0 < seconds < 60:
        return [(str(seconds), "秒" if ja else "s")]
    h, rem = divmod(seconds, 3600)
    if h == 0:
        return [(str(rem // 60), "分" if ja else "m")]
    return [(str(h), "時間" if ja else "h"), (str(rem // 60) if ja else f"{rem // 60:02d}", "分" if ja else "m")]


def date(ts: float) -> str:
    """The calendar date: Oct 3, 2026 / 2026年10月3日."""
    import time as _t
    from datetime import datetime as _dt
    d = _dt.fromtimestamp(ts).date()
    if _lang == "ja":
        return f"{d.year}年{d.month}月{d.day}日"            # 2026年10月3日 (no zero padding)
    return _t.strftime(tr("date.format"), _t.localtime(ts)).replace(" 0", " ")


def ago(ts: float, today=None) -> str:
    """When, the way people say it: Today, Yesterday, 3 Days Ago, 2 Weeks Ago, then the date (Sep 12 this
    year, Dec 3, 2025 before). A time slightly in the future (the other device's clock) counts as today."""
    from datetime import date as _d, datetime as _dt
    d = _dt.fromtimestamp(ts).date()
    today = today or _d.today()
    days = (today - d).days
    if days <= 0:
        return tr("date.today")
    if days == 1:
        return tr("date.yesterday")
    if days < 14:
        return tr("date.days_ago", n=days)
    if days <= 31:
        return tr("date.weeks_ago", n=days // 7)
    if d.year != today.year:
        return date(ts)
    if _lang == "ja":
        return f"{d.month}月{d.day}日"
    return f"{d.strftime('%b')} {d.day}"


def datetime_str(ts: float) -> str:
    import time as _t
    lt = _t.localtime(ts)
    if _lang == "ja":
        return f"{lt.tm_year}年{lt.tm_mon}月{lt.tm_mday}日 {lt.tm_hour}:{lt.tm_min:02d}"
    return _t.strftime(tr("datetime.format"), lt)
