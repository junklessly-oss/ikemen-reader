"""
guide.py - the "Help and Guidance" text (Settings > About), in English and Japanese.

Plain sections of (heading, paragraphs). A paragraph starting with "- " is a list item;
{green} {amber} {blue} {red} {grey} {lavender} become sync-light dots in the theme's colours.
"""
from __future__ import annotations

import html

GUIDE = {
    "en": [
        ("Welcome", [
            "Ikemen Reader is a library for your visual novels that keeps their saves in step across two "
            "computers. Play on one, then pick up right where you left off on the other.",
        ]),
        ("Getting Started", [
            "- Saves travel through a cloud folder: any folder that a cloud service of your choice (Google Drive, "
            "Dropbox, OneDrive and so on) keeps in sync on both computers. In Settings, choose that same folder on "
            "both.",
            "- In Settings, set \"This Is\" to Device 1 on one computer and Device 2 on the other.",
            "- Your games, covers, folders and reading statuses are shared through the cloud folder "
            "automatically.",
            "- With both devices open, changes made on the other device (new games, covers, folders, newer "
            "saves) appear here by themselves a few seconds after your cloud folder has synced them.",
        ]),
        ("Adding Games", [
            "- Drag a game's .exe (or a shortcut to it) onto the window, or click Add Game.",
            "- Already added on the other device? Add it here the same way and choose Set Up on This Device: "
            "both devices then share one set of saves.",
            "- The title is filled in from the game's folder or file name (engine names like KIRIKIRI are skipped). "
            "In the game's settings, you can change it (Automatic uses the title of the game's own window), choose "
            "a cover (Choose Cover or Find on VNDB), and crop it by dragging and scrolling.",
            "- With automatic covers from VNDB turned on in Settings, a matching VNDB entry is suggested as you add "
            "a game (Use This takes its cover). A game still without a cover after its first session gets one by "
            "itself when VNDB has an exact title match.",
            "- This Device holds the .exe and save folder for the computer you're using. Other Device is greyed "
            "out, because it's filled in on the other computer.",
            "- The save folder is often filled in for you as a suggestion: the same place the other device uses "
            "(adjusted for this computer), or a save folder the game already made here. A folder is only suggested "
            "when it's clearly this game's (named after the whole game, with save files in it); when there's any "
            "doubt, the box stays empty. Check it, then Save.",
            "- Pressing Read on a game with no save folder does the same check first and asks whether to use what "
            "it found, so the other device's saves come across before the game starts.",
            "- If nothing is found, you can leave the save folder empty. The first time you play, Ikemen Reader "
            "looks for where the game saves and asks you to confirm it. Just save at least once during that first "
            "session. If the game also keeps data in the registry under its own name, that's included "
            "automatically; a registry key that might belong to it is offered separately, unticked.",
            "- If you've already played it on the other device, make sure the save folder is set before you first "
            "read it on this device. Then the other device's saves are put in place before the game starts, and "
            "you carry on where you left off. If it's left empty, Ikemen Reader doesn't yet know where to put them, "
            "so that first session starts without your progress. Afterwards you're asked which saves to keep: "
            "choose the other device's to get your progress back (the saves from that first session are backed up).",
            "- Launch With Luna and Launch With Locale Emulator start the game through those programs (set their "
            "locations in Settings first).",
        ]),
        ("Playing", [
            "- Select a game and press Read (or double-click it, or press Enter).",
            "- While the game runs, the Read button says Stop: press it to close the game, like its own X button "
            "(the game may still ask to confirm). If it doesn't close, you can choose to force it closed.",
            "- You can browse other games while reading: selecting one shows its details at the bottom. Click an "
            "empty space (or the game you're reading) to bring back Now Reading and Stop.",
            "- Before the game starts, any newer saves from the other device are downloaded. When the game "
            "closes, your saves are uploaded and your playtime is updated.",
            "- You don't need to start a game for its saves to sync. While Ikemen Reader is open, newer saves from "
            "the other device are downloaded a few seconds after they arrive, and saves made on this device "
            "without Ikemen Reader are uploaded. A game that's open outside Ikemen Reader is left alone until "
            "you close it, so it can't overwrite what was downloaded.",
            "- It never matters which device added a game first. The first time a game syncs on a device, the "
            "other device's saves are downloaded if this one has none yet. If both have saves, you choose which "
            "to keep; the newer ones are marked, and the others are backed up first. (Some games create a save "
            "or settings file as soon as they're installed or first opened, so you may be asked even on a fresh "
            "setup. Choose the device you actually played on.)",
            "- If the saves changed on both devices since they last synced (for example, you read on both without "
            "syncing in between), you'll be asked which ones to keep. Ikemen Reader asks as soon as it notices "
            "(choose now, or Later), and again when you press Read; the game starts once you've chosen. "
            "Whatever gets replaced is backed up first.",
            "- Right-click a game and choose Sync Now to sync it straight away.",
            "- Closing a game only ever uploads. If the other device uploaded newer saves while you were reading, "
            "you're asked which to keep instead of either being overwritten.",
            "- Right-click a game and choose Restore an Earlier Save to bring back one of the last 10 backups.",
            "- Settings can't be opened while a game is running. If you close Ikemen Reader during play, it asks "
            "first; the game keeps running, and its saves upload the next time you open Ikemen Reader.",
        ]),
        ("The Sync Lights", [
            "Each cover has a small light in its bottom-right corner:",
            "- {green} Saves are in sync.",
            "- {amber} This device has newer saves. They upload automatically.",
            "- {blue} Newer saves are in the cloud. They download automatically (for a game that's open outside "
            "Ikemen Reader, once you close it).",
            "- {red} Saves changed on both devices. You'll be asked which to keep.",
            "- {grey} Not set up on this device yet, no saves yet, or still being checked.",
            "- {lavender} Saves are in sync, but the game isn't set up on the other device yet.",
            "A folder's light is split into equal slices, one for each colour among its games, with the most "
            "important first (starting at the top and going clockwise).",
            "The top bar sums it all up: Synced, Syncing, or Error when saves changed on both devices (click "
            "Error to jump to those games).",
        ]),
        ("Your Library", [
            "- Search by title, sort by Title, Most Played, Recently Played or My Arrangement, and filter by reading "
            "status. Searching and filtering also show the matching games inside folders.",
            "- Folders First / Folders Last / Folders Mixed (in the same menu) choose where folders go in Title, Most "
            "Played and Recently Played: grouped at the front or back, or among the games, always in that sort's order. "
            "Your choice stays until you press another one. With no folders, they're greyed out.",
            "- My Arrangement is your own order: drag a cover into the gap between two others to move it there (a "
            "thin line shows where it will land). Doing that switches to My Arrangement, starting from exactly what "
            "you were looking at. It's kept when you switch to another sort: pick My Arrangement to get it back. New "
            "games go at the end; folders are dragged the same way. While dragging, hold the cover near the top or "
            "bottom edge to scroll (the closer to the edge, the faster).",
            "- In My Arrangement, the folder options re-place your folders, sorted by the sort you used last (Title, "
            "Most Played or Recently Played), while your games keep your order: First and Last group them, Mixed puts "
            "them in the spots that sort would give them. You can move them by hand afterwards.",
            "- Each folder has its own sort, found in the same menu while the folder is open. It starts as Same as "
            "Library and follows the main page until you pick something else for it. Dragging a game to a new spot "
            "inside the folder gives the folder its own My Arrangement. A folder's own order and sort are shared "
            "with the other device.",
            "- Hide Statistics (next to the menus at the top) hides playtime and last-played dates on the covers "
            "and in the bottom bar. Press Show Statistics to bring them back; your choice is remembered.",
            "- Set a game's reading status (Reading, Finished, Plan to Read, Dropped) in the bottom bar or by "
            "right-clicking it.",
            "- A game with a reading status gets a little bookmark in that status's colour in the top-right corner "
            "of its cover (also inside folders and on a folder's cover). The bookmark button next to the trash can "
            "hides or shows them; your choice is remembered. The colours can be changed in a custom theme.",
            "- Folders: drag a game onto the middle of another game to put them both in a new folder, or onto a folder to add "
            "it. Inside a folder, drag a game onto \"‹ Library\" to take it out. Right-click a folder to rename or "
            "remove it (its games go back to the library).",
            "- Click an empty space or press Esc to deselect.",
        ]),
        ("Removing Games", [
            "- Drag a game onto the trash can in the bottom-right corner, or select it and click the trash can.",
            "- You can also right-click it and choose Remove From Library, or use the red button in its Edit "
            "window.",
            "- You'll always be asked first. The game is removed from the library on both devices, but its save "
            "files are never deleted.",
        ]),
        ("Making It Yours", [
            "In Settings:",
            "- Language switches the whole program at once.",
            "- Font: pick a separate font for English and for Japanese, or add your own with Add Font.",
            "- Accessibility: pulsing sync lights, launching on startup, automatic updates, automatic covers from "
            "VNDB, and safe covers only.",
            "- Custom Library Background: choose a picture, set its opacity and zoom, and drag it into place.",
            "- Theme: Dark, Light, or Custom, where you choose every colour yourself.",
            "- Updates: when a new version of Ikemen Reader is out, you're asked whether to update when Ikemen "
            "Reader opens. With Install Updates Automatically ticked, it downloads by itself and offers Restart Now "
            "(otherwise it's installed when you close Ikemen Reader). Updates never touch your library, saves or "
            "settings, and never happen while a game is running.",
        ]),
        ("Ikemen Sync", [
            "Device Settings Sync copies the other device's look and preferences (theme, fonts, background, "
            "language and more) onto this one. The device number, cloud folder, Locale Emulator, Luna and Launch "
            "Program on Startup stay as they are, and your games and saves aren't affected.",
        ]),
        ("Handy Shortcuts", [
            "- Enter: play the selected game, or open the selected folder.",
            "- Esc: leave a folder, deselect, or cancel a drag.",
            "- Backspace: leave a folder.",
            "- F5: refresh the library.",
        ]),
    ],
    "ja": [
        ("はじめに", [
            "イケメンリーダーは、2台のパソコンでビジュアルノベルのセーブを同じ状態に保つライブラリです。片方のパソコンで遊んだ続きを、もう片方でそのまま楽しめます。",
        ]),
        ("最初の設定", [
            "- セーブはクラウドフォルダーを通してやり取りされます。お好みのクラウドサービス（Googleドライブ、Dropbox、OneDriveなど）で両方のパソコンに同期されているフォルダーなら、どれでも使えます。"
            "設定で、両方のパソコンに同じフォルダーを選んでください。",
            "- 設定の「この端末は」で、片方を端末1、もう片方を端末2にしてください。",
            "- ゲーム・カバー画像・フォルダー・プレイ状況は、クラウドフォルダーを通して自動で共有されます。",
            "- 両方の端末で開いている場合、もう一方の端末での変更（ゲームの追加、カバー画像、フォルダー、新しいセーブ）は、クラウドフォルダーの同期が終わってから数秒で自動的に反映されます。",
        ]),
        ("ゲームの追加", [
            "- ゲームの .exe（またはそのショートカット）をウィンドウにドラッグするか、「ゲームを追加」をクリックします。",
            "- もう一方の端末ですでに追加したゲームは、同じ方法で追加して「この端末で設定する」を選びます。両方の端末で同じセーブを共有できます。",
            "- タイトルは、ゲームのフォルダー名やファイル名から自動で入力されます（KIRIKIRIなどのエンジン名は使われません）。"
            "ゲームの設定画面では、タイトルの変更（「自動」にするとゲーム画面のタイトルを使います）やカバー画像の設定（「カバー画像を選択」または「VNDBで探す」）ができます。"
            "カバーはドラッグとスクロールで切り抜けます。",
            "- 設定でVNDBからのカバー画像の自動取得をオンにしていると、ゲームの追加時にVNDBで見つかった候補が表示されます（「これを使う」でそのカバーを使います）。"
            "初回プレイのあともカバーがないゲームは、VNDBにタイトルが完全に一致する作品があれば、自動でカバーが設定されます。",
            "- 「この端末」には、今使っているパソコンでの .exe とセーブフォルダーを入力します。「もう一方の端末」は灰色で表示され、もう一方のパソコンで入力します。",
            "- セーブフォルダーは、提案として自動で入力されることがあります（もう一方の端末と同じ場所をこのパソコン用に置き換えたもの、またはゲームがすでに作ったセーブフォルダー）。フォルダーは、明らかにそのゲームのもの（ゲーム名そのものの名前で、セーブファイルが入っている）ときだけ提案されます。少しでも迷う場合は空欄のままです。確認してから保存してください。",
            "- セーブフォルダーが未設定のゲームで「読む」を押したときも、先に同じ確認をして、見つかった場所を使うか尋ねます。これで、起動前にもう一方の端末のセーブを持ってこられます。",
            "- 何も見つからない場合は、セーブフォルダーは空欄のままでも大丈夫です。初めてプレイしたときに、セーブの場所を自動で探して確認を求めます。最初のプレイ中に、一度はセーブしてください。"
            "ゲームが自分の名前でレジストリにもデータを保存している場合は、自動で含まれます。ゲームのものかもしれないレジストリキーは、チェックなしで別に表示されます。",
            "- もう一方の端末ですでにプレイしているゲームは、ここで初めて読む前に、この端末のセーブフォルダーが設定されていることを確認してください。そうすれば、起動前にもう一方の端末のセーブが配置され、続きから遊べます。"
            "空欄のままにすると、セーブをどこに置けばよいか分からないため、最初のプレイは進行状況なしで始まります。そのあと、どちらのセーブを残すか尋ねられたら、"
            "もう一方の端末のセーブを選ぶと進行状況が戻ります（最初のプレイのセーブはバックアップされます）。",
            "- 「Lunaで起動」「Locale Emulatorで起動」を使うと、それぞれのプログラムを通してゲームを起動します（先に設定で場所を指定してください）。",
        ]),
        ("プレイ", [
            "- ゲームを選んで「読む」を押します（ダブルクリックやEnterキーでも起動できます）。",
            "- ゲームの実行中、「読む」ボタンは「終了」になります。押すと、ゲームの×ボタンと同じようにゲームを閉じます（ゲーム側で確認が表示される場合があります）。"
            "閉じない場合は、強制終了を選べます。",
            "- 読んでいる間も、ほかのゲームを選ぶと下部にその情報が表示されます。空いている場所（または読んでいるゲーム）をクリックすると、「読書中」と「終了」ボタンに戻ります。",
            "- 起動前に、もう一方の端末の新しいセーブがあればダウンロードします。ゲームを終了すると、セーブをアップロードしてプレイ時間を記録します。",
            "- セーブを同期するためにゲームを起動する必要はありません。イケメンリーダーを開いている間は、もう一方の端末の新しいセーブが届いてから数秒でダウンロードされ、"
            "イケメンリーダーを使わずにこの端末で作ったセーブはアップロードされます。イケメンリーダー以外から開いているゲームは、ダウンロードした内容を上書きされないよう、閉じるまでそのままにします。",
            "- どちらの端末で先にゲームを追加したかは関係ありません。ある端末で初めて同期するとき、その端末にセーブがなければ、もう一方の端末のセーブがダウンロードされます。両方にセーブがある場合は、"
            "どちらを残すか選びます（新しい方に印が付き、もう片方は先にバックアップされます）。"
            "（インストール直後や初回起動時にセーブや設定ファイルを作るゲームもあるため、新しく設定した場合でも尋ねられることがあります。実際に遊んだ端末を選んでください。）",
            "- 前回の同期以降に両方の端末でセーブが変更されていた場合（例：同期しないまま両方で読んだとき）は、どちらを残すか尋ねます。気づいた時点で尋ね（今すぐ選ぶ／あとで）、「読む」を押したときにも尋ねます。選ぶとゲームが起動します。"
            "置き換えられる方は先にバックアップされます。",
            "- ゲームを右クリックして「今すぐ同期」を選ぶと、すぐに同期できます。",
            "- ゲームを終了したときは、アップロードだけを行います。読んでいる間にもう一方の端末が新しいセーブをアップロードしていた場合は、どちらかを上書きせずに、どちらを残すか尋ねます。",
            "- ゲームを右クリックして「以前のセーブを復元」を選ぶと、直近10件のバックアップから戻せます。",
            "- ゲームの実行中は設定を開けません。プレイ中にイケメンリーダーを閉じようとすると、確認が表示されます。ゲームはそのまま動き続け、セーブは次にイケメンリーダーを起動したときにアップロードされます。",
        ]),
        ("同期ランプ", [
            "各カバーの右下に小さなランプがあります：",
            "- {green} セーブは同期済みです。",
            "- {amber} この端末のセーブの方が新しいです。自動でアップロードされます。",
            "- {blue} クラウドのセーブの方が新しいです。自動でダウンロードされます（イケメンリーダー以外から開いているゲームは、閉じたあと）。",
            "- {red} 両方の端末でセーブが変更されています。どちらを残すか尋ねます。",
            "- {grey} この端末ではまだ未設定、セーブがまだない、または確認中です。",
            "- {lavender} セーブは同期済みですが、もう一方の端末ではまだこのゲームが設定されていません。",
            "フォルダーのランプは、中のゲームのランプの色ごとに等分され、重要なものから順に（上から時計回りに）並びます。",
            "上部のバーにもまとめて表示されます：同期済み、同期中、または両方の端末でセーブが変更されたときは「エラー」（クリックするとそのゲームに移動します）。",
        ]),
        ("ライブラリ", [
            "- タイトルで検索、「タイトル順」「プレイ時間が長い順」「最近プレイした順」「自分の並び」で並べ替え、プレイ状況で絞り込みができます。検索や絞り込みでは、フォルダーの中にある該当ゲームも表示されます。",
            "- 同じメニューの「フォルダーを先に」「フォルダーを後に」「フォルダーを混在」で、タイトル順・プレイ時間順・最近プレイした順でのフォルダーの位置を選べます（前・後ろにまとめる、またはゲームの間に混ぜる。どれもその並び順で並びます）。別のものを押すまで保持されます。フォルダーがないときは選べません。",
            "- 「自分の並び」は自分で決める並び順です。カバーを他の2つのカバーの間にドラッグすると、そこへ移動します（移動先に細い線が表示されます）。これを行うと、表示していた順番のまま「自分の並び」に切り替わります。他の並び順に切り替えても保存されているので、「自分の並び」を選ぶと元に戻ります。新しいゲームは最後に追加されます。フォルダーも同じようにドラッグできます。ドラッグ中にカバーを上端や下端に近づけるとスクロールします（端に近いほど速くなります）。",
            "- 「自分の並び」でフォルダーの位置を押すと、最後に使った並び順（タイトル順・プレイ時間順・最近プレイした順）でフォルダーを並べ直します。ゲームは自分の並びのままです。「先に」「後に」はまとめて、「混在」はその並び順でのフォルダーの位置に置きます。そのあとは自由に動かせます。",
            "- フォルダーごとに並び順があり、フォルダーを開いているときに同じメニューで選べます。最初は「ライブラリと同じ」で、別の並び順を選ぶまではメインページに合わせます。"
            "フォルダーの中でゲームをドラッグして動かすと、そのフォルダー専用の「自分の並び」になります。フォルダーの並び順はもう一方の端末とも共有されます。",
            "- 上部のメニューの右にある「統計を非表示」を押すと、カバーと下部のバーのプレイ時間と最終プレイ日が非表示になります。「統計を表示」で元に戻せます。この設定は次回以降も保持されます。",
            "- プレイ状況（プレイ中・読了・積みゲー・中断）は、下部のバーか右クリックで設定します。",
            "- プレイ状況を設定したゲームには、カバーの右上にその状況の色のしおりが付きます（フォルダーの中やフォルダーのカバーにも表示されます）。"
            "ゴミ箱の隣にあるしおりのボタンで表示・非表示を切り替えられます。この設定は次回以降も保持されます。色はカスタムテーマで変更できます。",
            "- フォルダー：ゲームを別のゲームの中央にドラッグすると、2つをまとめた新しいフォルダーができます。フォルダーにドラッグすると追加されます。フォルダーの中では、ゲームを「‹ ライブラリ」"
            "にドラッグすると外に出せます。フォルダーを右クリックすると、名前の変更や解除ができます（中のゲームはライブラリに戻ります）。",
            "- 何もないところをクリックするか、Escキーを押すと選択を解除できます。",
        ]),
        ("ゲームの削除", [
            "- ゲームを右下のゴミ箱にドラッグするか、ゲームを選んでゴミ箱をクリックします。",
            "- 右クリックの「ライブラリから削除」や、編集画面の赤いボタンからも削除できます。",
            "- 削除の前には必ず確認が表示されます。ゲームは両方の端末のライブラリから削除されますが、セーブファイルは削除されません。",
        ]),
        ("自分好みに", [
            "設定画面で：",
            "- 言語：プログラム全体がすぐに切り替わります。",
            "- フォント：英語と日本語で別々のフォントを選べます。「フォントを追加」で自分のフォントも使えます。",
            "- アクセシビリティ：同期ランプの点滅、Windows起動時の自動起動、アップデートの自動インストール、VNDBからのカバー画像の自動取得、安全なカバーのみの表示を設定できます。",
            "- ライブラリの背景をカスタマイズ：画像を選び、不透明度とズームを調整して、ドラッグで位置を合わせます。",
            "- テーマ：ダーク、ライト、またはすべての色を自分で選べるカスタムから選べます。",
            "- アップデート：イケメンリーダーの新しいバージョンが出ると、起動時にアップデートするか尋ねます。「アップデートを自動でインストール」をオンにすると、"
            "自動でダウンロードして「今すぐ再起動」を提案します（再起動しない場合は、イケメンリーダーを閉じたときにインストールされます）。"
            "アップデートでライブラリ・セーブ・設定が変わることはなく、ゲームの実行中に行われることもありません。",
        ]),
        ("イケメン同期", [
            "「端末設定の同期」を押すと、もう一方の端末の見た目と設定（テーマ・フォント・背景・言語など）をこの端末にコピーします。端末番号、クラウドフォルダー、Locale Emulator、Luna、"
            "Windows起動時の自動起動はそのまま変わらず、ゲームとセーブにも影響しません。",
        ]),
        ("便利なキー操作", [
            "- Enter：選んだゲームをプレイ、または選んだフォルダーを開く。",
            "- Esc：フォルダーから戻る、選択を解除する、ドラッグをやめる。",
            "- Backspace：フォルダーから戻る。",
            "- F5：ライブラリを再読み込みする。",
        ]),
    ],
}


UL = '<ul style="margin-left:-14px; margin-top:2px">'


def guide_html(lang: str, colors: dict, lights: dict) -> str:
    """The whole guide as simple HTML, coloured for the current theme."""
    dots = {k: f'<span style="color:{lights[k]}">●</span>' for k in ("green", "amber", "blue", "red", "grey", "lavender")}
    out = [f'<div style="color:{colors["text"]}">']
    for heading, paras in GUIDE.get(lang, GUIDE["en"]):
        out.append(f'<h3 style="margin-top:18px; margin-bottom:6px">{html.escape(heading)}</h3>')
        items = []
        for p in paras:
            text = html.escape(p[2:] if p.startswith("- ") else p).format(**dots)
            if p.startswith("- "):
                items.append(f'<li style="margin-bottom:5px">{text}</li>')
            else:
                if items:
                    out.append(UL + "".join(items) + "</ul>")
                    items = []
                out.append(f'<p style="margin-top:4px; margin-bottom:6px">{text}</p>')
        if items:
            out.append(UL + "".join(items) + "</ul>")
    out.append("</div>")
    return "".join(out)
