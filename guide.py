"""
guide.py - the "Help and Guidance" text (Settings > About), in English and Japanese.

Plain sections of (heading, paragraphs). A paragraph starting with "- " is a list item;
{green} {amber} {blue} {red} {grey} become sync-light dots in the theme's colours.
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
            "- In the game's settings, you can set its title (Automatic uses the title of the game's own "
            "window), choose a cover (Choose Cover or Find on VNDB), and crop it by dragging and scrolling.",
            "- This Device holds the .exe and save folder for the computer you're using. Other Device is greyed "
            "out, because it's filled in on the other computer.",
            "- You can leave the save folder empty. The first time you play, Ikemen Reader looks for where the "
            "game saves and asks you to confirm it. Just save at least once during that first session.",
            "- If you've already played it on the other device, fill in its save folder before you first read it "
            "on this device. Then the other device's saves are put in place before the game starts, and you carry "
            "on where you left off. If you leave it empty, Ikemen Reader doesn't yet know where to put them, so "
            "that first session starts without your progress. Afterwards you're asked which saves to keep: choose "
            "the other device's to get your progress back (the saves from that first session are backed up).",
            "- Launch With Luna and Launch With Locale Emulator start the game through those programs (set their "
            "locations in Settings first).",
        ]),
        ("Playing", [
            "- Select a game and press Read (or double-click it, or press Enter).",
            "- While the game runs, the Read button says Stop: press it to close the game, like its own X button "
            "(the game may still ask to confirm). If it doesn't close, you can choose to force it closed.",
            "- Before the game starts, any newer saves from the other device are downloaded. When the game "
            "closes, your saves are uploaded and your playtime is updated.",
            "- It never matters which device added a game first. The first time a game syncs on a device, the "
            "other device's saves are downloaded if this one has none yet. If both have saves, you choose which "
            "to keep; the newer ones are marked, and the others are backed up first. (Some games create a save "
            "or settings file as soon as they're installed or first opened, so you may be asked even on a fresh "
            "setup. Choose the device you actually played on.)",
            "- If the saves changed on both devices since they last synced (for example, you read on both without "
            "syncing in between), you'll be asked which ones to keep, and the game starts once you've chosen. "
            "Whatever gets replaced is backed up first.",
            "- Closing a game only ever uploads. If the other device uploaded newer saves while you were reading, "
            "you're asked which to keep instead of either being overwritten.",
            "- Right-click a game and choose Restore an Earlier Save to bring back one of the last 10 backups.",
            "- Settings can't be opened while a game is running. If you close Ikemen Reader during play, it asks "
            "first; the game keeps running, and its saves upload the next time you open Ikemen Reader.",
        ]),
        ("The Sync Lights", [
            "Each cover has a small light in its bottom-right corner:",
            "- {green} Saves are in sync.",
            "- {amber} This device has newer saves. They upload the next time you play or open Ikemen Reader.",
            "- {blue} Newer saves are in the cloud. They download when you press Read.",
            "- {red} Saves changed on both devices. You'll choose which to keep when you press Read.",
            "- {grey} Not set up on this device yet, no saves yet, or still being checked.",
            "The top bar sums it all up: Synced, Syncing, or Error when saves changed on both devices (click "
            "Error to jump to those games).",
        ]),
        ("Your Library", [
            "- Search by title, sort by Recently Played, Most Played or Title, and filter by reading status.",
            "- Hide Statistics (next to the menus at the top) hides playtime and last-played dates on the covers "
            "and in the bottom bar. Press Show Statistics to bring them back; your choice is remembered.",
            "- Set a game's reading status (Reading, Finished, Plan to Read, Dropped) in the bottom bar or by "
            "right-clicking it.",
            "- Folders: drag a game onto another game to put them both in a new folder, or onto a folder to add "
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
            "- Custom Library Background: choose a picture, set its opacity and zoom, and drag it into place.",
            "- Theme: Dark, Light, or Custom, where you choose every colour yourself.",
            "- Accessibility: pulsing sync lights, launching on startup, automatic covers from VNDB, and safe "
            "covers only.",
        ]),
        ("Ikemen Sync", [
            "Device Settings Sync copies the other device's look and preferences (theme, fonts, background, "
            "language and more) onto this one. The device number, cloud folder, Locale Emulator, Luna and Launch "
            "Program on Startup stay as they are, and your games and saves aren't affected.",
        ]),
        ("Handy Shortcuts", [
            "- Enter: play the selected game, or open the selected folder.",
            "- Esc: leave a folder, deselect, or cancel a drag.",
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
            "- ゲームの設定画面では、タイトル（「自動」にするとゲーム画面のタイトルを使います）やカバー画像（「カバー画像を選択」または「VNDBで探す」）を設定できます。"
            "カバーはドラッグとスクロールで切り抜けます。",
            "- 「この端末」には、今使っているパソコンでの .exe とセーブフォルダーを入力します。「もう一方の端末」は灰色で表示され、もう一方のパソコンで入力します。",
            "- セーブフォルダーは空欄のままでも大丈夫です。初めてプレイしたときに、セーブの場所を自動で探して確認を求めます。最初のプレイ中に、一度はセーブしてください。",
            "- もう一方の端末ですでにプレイしているゲームは、ここで初めて読む前に、この端末のセーブフォルダーを入力してください。そうすれば、起動前にもう一方の端末のセーブが配置され、続きから遊べます。"
            "空欄のままにすると、セーブをどこに置けばよいか分からないため、最初のプレイは進行状況なしで始まります。そのあと、どちらのセーブを残すか尋ねられたら、"
            "もう一方の端末のセーブを選ぶと進行状況が戻ります（最初のプレイのセーブはバックアップされます）。",
            "- 「Lunaで起動」「Locale Emulatorで起動」を使うと、それぞれのプログラムを通してゲームを起動します（先に設定で場所を指定してください）。",
        ]),
        ("プレイ", [
            "- ゲームを選んで「読む」を押します（ダブルクリックやEnterキーでも起動できます）。",
            "- ゲームの実行中、「読む」ボタンは「終了」になります。押すと、ゲームの×ボタンと同じようにゲームを閉じます（ゲーム側で確認が表示される場合があります）。"
            "閉じない場合は、強制終了を選べます。",
            "- 起動前に、もう一方の端末の新しいセーブがあればダウンロードします。ゲームを終了すると、セーブをアップロードしてプレイ時間を記録します。",
            "- どちらの端末で先にゲームを追加したかは関係ありません。ある端末で初めて同期するとき、その端末にセーブがなければ、もう一方の端末のセーブがダウンロードされます。両方にセーブがある場合は、"
            "どちらを残すか選びます（新しい方に印が付き、もう片方は先にバックアップされます）。"
            "（インストール直後や初回起動時にセーブや設定ファイルを作るゲームもあるため、新しく設定した場合でも尋ねられることがあります。実際に遊んだ端末を選んでください。）",
            "- 前回の同期以降に両方の端末でセーブが変更されていた場合（例：同期しないまま両方で読んだとき）は、どちらを残すか尋ねます。選ぶとゲームが起動します。"
            "置き換えられる方は先にバックアップされます。",
            "- ゲームを終了したときは、アップロードだけを行います。読んでいる間にもう一方の端末が新しいセーブをアップロードしていた場合は、どちらかを上書きせずに、どちらを残すか尋ねます。",
            "- ゲームを右クリックして「以前のセーブを復元」を選ぶと、直近10件のバックアップから戻せます。",
            "- ゲームの実行中は設定を開けません。プレイ中にイケメンリーダーを閉じようとすると、確認が表示されます。ゲームはそのまま動き続け、セーブは次にイケメンリーダーを起動したときにアップロードされます。",
        ]),
        ("同期ランプ", [
            "各カバーの右下に小さなランプがあります：",
            "- {green} セーブは同期済みです。",
            "- {amber} この端末のセーブの方が新しいです。次にプレイするか、イケメンリーダーを起動したときにアップロードされます。",
            "- {blue} クラウドのセーブの方が新しいです。「読む」を押すとダウンロードされます。",
            "- {red} 両方の端末でセーブが変更されています。「読む」を押したときに、どちらを残すか選びます。",
            "- {grey} この端末ではまだ未設定、セーブがまだない、または確認中です。",
            "上部のバーにもまとめて表示されます：同期済み、同期中、または両方の端末でセーブが変更されたときは「エラー」（クリックするとそのゲームに移動します）。",
        ]),
        ("ライブラリ", [
            "- タイトルで検索、「最近プレイした順」「プレイ時間が長い順」「タイトル順」で並べ替え、プレイ状況で絞り込みができます。",
            "- 上部のメニューの右にある「統計を非表示」を押すと、カバーと下部のバーのプレイ時間と最終プレイ日が非表示になります。「統計を表示」で元に戻せます。この設定は次回以降も保持されます。",
            "- プレイ状況（プレイ中・読了・積みゲー・中断）は、下部のバーか右クリックで設定します。",
            "- フォルダー：ゲームを別のゲームにドラッグすると、2つをまとめた新しいフォルダーができます。フォルダーにドラッグすると追加されます。フォルダーの中では、ゲームを「‹ ライブラリ」"
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
            "- ライブラリの背景をカスタマイズ：画像を選び、不透明度とズームを調整して、ドラッグで位置を合わせます。",
            "- テーマ：ダーク、ライト、またはすべての色を自分で選べるカスタムから選べます。",
            "- アクセシビリティ：同期ランプの点滅、Windows起動時の自動起動、VNDBからのカバー画像の自動取得、安全なカバーのみの表示を設定できます。",
        ]),
        ("イケメン同期", [
            "「端末設定の同期」を押すと、もう一方の端末の見た目と設定（テーマ・フォント・背景・言語など）をこの端末にコピーします。端末番号、クラウドフォルダー、Locale Emulator、Luna、"
            "Windows起動時の自動起動はそのまま変わらず、ゲームとセーブにも影響しません。",
        ]),
        ("便利なキー操作", [
            "- Enter：選んだゲームをプレイ、または選んだフォルダーを開く。",
            "- Esc：フォルダーから戻る、選択を解除する、ドラッグをやめる。",
            "- F5：ライブラリを再読み込みする。",
        ]),
    ],
}


UL = '<ul style="margin-left:-14px; margin-top:2px">'


def guide_html(lang: str, colors: dict, lights: dict) -> str:
    """The whole guide as simple HTML, coloured for the current theme."""
    dots = {k: f'<span style="color:{lights[k]}">●</span>' for k in ("green", "amber", "blue", "red", "grey")}
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
