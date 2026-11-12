"""断りの目録の項目（金庫と庭の口・段 1・仮）。thth/refusal_catalog.py が読む。

`thth sync`（台帳を monban の金庫と合わせる）と `thth garden`（庭の静的な頁）が出す符丁（`thth/vault/wire.py` の
`REASONS`）。monban の道具（git-remote-monban・monban-vault）の断りは、符丁だけ読んでここの符丁に写し、monban の符丁を
1 行目の（）に添える（文は写さない）。**masaru の答えを待たずに推しで作った・朝に覆せる**。

`since` はまとめる者が版を上げるまで今の版（`thth/VERSION` を越えない柵）。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.16.0"
_SYNC = "thth sync"
_INSTALL = "monban の checkout で pip install '.[vault]' を打ち、monban-vault と git-remote-monban を入れてください"
_LOGIN = "monban login --house <ハウス> を人の端末で打ち直してから（THTH は代わりに打ちません）"


def _entry(why, fix, command, better, kind="refusal"):
    return {"kind": kind, "why": why, "fix": fix, "command": command, "better": better, "doors": _C,
            "since": _SINCE}


ENTRIES = {
    # ------------------------------------------------------------------ thth sync（金庫）
    "sync_args_invalid": _entry(
        "thth sync に渡した値が決まりに合っていません（枝の名前は英数字と . _ / - だけ）。",
        "1 行目の（）が指す引数を直して打ち直してください。",
        _SYNC,
        "合わせる枝は main 1 本にそろえると、端末どうしで取り違えが起きにくくなります。"),
    "sync_tool_missing": _entry(
        "monban の道具（1 行目の（）の名前）か git が、この端末に見つかりません。金庫の暗号は monban の道具が持つので、"
        "THTH だけでは合わせられない作りです。",
        f"{_INSTALL}（git が無いときは git を入れてください）。",
        None,
        "入れたあと thth sync status で金庫の様子が読めれば、道具と鍵の置き場がそろっています。"),
    "sync_not_repo": _entry(
        "--repo（既定は今のフォルダ）が git の repo ではありません。台帳は git の repo として持つ決まりです（構想 §5）。",
        "台帳のフォルダで git init と最初の commit をしてから、そのフォルダで打ち直してください。",
        _SYNC,
        "台帳を 1 行 1 枚の .md で持つと、ぶつかったときも行ごとに見比べて直せます。"),
    "sync_register_unclean": _entry(
        "台帳の作業木に、commit していない変更か追跡していないファイルがあります（段 1 の点検は clean な作業木だけを通します）。",
        "git status で見て、残す物は commit し、要らない物は片づけてから thth sync を打ち直してください。",
        _SYNC,
        "生成物（頁の出来上がり・画像の束）を .gitignore に入れておくと、台帳が小さく保てて点検も通りやすくなります。"),
    "sync_in_progress": _entry(
        "台帳の repo が merge か rebase などの途中です（1 行目の（）が途中の印）。途中のまま合わせると、半端な版が金庫に入ります。",
        "git status で途中の操作を終えるか（ぶつかりを直して commit）、git merge --abort などで戻してから打ち直してください。",
        _SYNC,
        "thth sync は merge で合わせる（rebase しない）ので、途中の操作が終わってからなら履歴がそのまま残ります。"),
    "sync_branch_mismatch": _entry(
        "今いる枝が、合わせる枝（1 行目の（）・既定 main）と違います。",
        "git switch <枝> で合わせる枝に移るか、--branch に今の枝を渡して打ち直してください。",
        _SYNC,
        "端末どうしで同じ枝を合わせる決まりにしておくと、版が枝ごとに分かれて迷うことがなくなります。"),
    "sync_remote_missing": _entry(
        "台帳の repo に remote monban がありません。",
        "1 台目なら thth sync init <金庫> を、2 台目からなら thth sync join <金庫> を打つと remote を足します"
        "（自分で足すなら git remote add monban monban://<ハウス>/<アカウント>/ledger）。",
        "thth sync join <金庫>",
        "金庫の名前を ledger にそろえると、どの端末でも同じ URL で足せます。"),
    "sync_url_invalid": _entry(
        "金庫の URL が monban://<ハウス>/<アカウント>/<金庫> の形ではありません。",
        "monban://<ハウス>/<アカウント>/ledger の形（ハウスと金庫は英小文字・数字・-）で打ち直してください。",
        None,
        "URL はハウスの持ち主が配る形をそのまま貼ると、打ち間違いが起きません。"),
    "sync_conflict": _entry(
        "金庫の版と手元の版が同じ行を別々に直していて、git が自動で合わせられませんでした。段 1 は自動で解かず、人が決めます。",
        "git status でぶつかったファイルを開き、残す方に直して commit してから thth sync を打ち直してください"
        "（やめるなら git merge --abort）。",
        _SYNC,
        "同じ行を 2 台で同時に直さないよう、行の持ち主（どの端末・どのセッションが直すか）を頭の項目に書いておくとぶつかりが減ります。"),
    "sync_retry_exhausted": _entry(
        "合わせている間に、ほかの端末が続けて金庫に置いたので、3 回やり直しても置けませんでした（ほかの端末の版は消していません）。",
        "数分置いてから thth sync を打ち直してください。",
        _SYNC,
        "端末ごとに合わせる時刻をずらす（片方は朝・片方は夜など）と、続けて当たることがなくなります。"),
    "sync_tampered": _entry(
        "金庫の箱が書き換わったか、前に見た版より古い版を見せられました（monban の確かめが止めました）。自動では直しません。",
        "何も書き換えずに止め、monban の運営者（ハウスの持ち主）に、1 行目の（）と時刻を知らせてください。",
        None,
        "THTH は手元の台帳の repo を書き換えていないので、知らせを待つ間も手元で書き続けられます（あとで合わせます）。"),
    "sync_busy": _entry(
        "金庫に置きかけの箱が多いか、置く回数の上限に当たりました（1 行目の（）が monban の符丁）。置いた物は消えていません。",
        "数分待ってから thth sync を打ち直してください。",
        _SYNC,
        "合わせるのを 1 日に数回のまとまった時刻にすると、待ちに当たらなくなります。"),
    "sync_box_too_large": _entry(
        "1 回に送る量が monban の箱 1 つ（25 MB）を超えました。段 1 の monban は大きな送りを分けません。",
        "画像や頁の出来上がりなどの生成物を台帳の repo から外し（.gitignore と git rm --cached）、commit してから打ち直してください。",
        _SYNC,
        "台帳は行の .md と由来だけにし、生成物は作り直せる形にすると、送る量も金庫の費用も小さく保てます。"),
    "sync_vault_full": _entry(
        "金庫の量の上限（1 GB）に当たりました。",
        "生成物や大きなファイルを台帳の repo から外して commit し、monban の運営者に量の扱いを相談してください。",
        None,
        "台帳を行の .md と由来だけに絞ると、何年分の履歴でも上限に届きにくくなります。"),
    "sync_not_joined": _entry(
        "この端末に、この金庫の鍵がありません。",
        "thth sync join <金庫> を人の端末で打ち、戻しの文字列を入れてください。",
        "thth sync join <金庫>",
        "戻しの文字列をパスワード管理に置いておくと、新しい端末もすぐ足せます。"),
    "sync_login_required": _entry(
        "monban のアカウントのキーが無いか、失効か期限切れです（1 行目の（）が monban の符丁）。",
        f"{_LOGIN}、thth sync を打ち直してください。",
        _SYNC,
        "使わなくなった端末は monban の側で失効させておくと、失くした端末から版を取られる心配がなくなります。"),
    "sync_unavailable": _entry(
        "monban の金庫に届きませんでした（網か monban の側の様子）。何も書き換えていません。",
        "少し置いてから thth sync を打ち直してください。",
        _SYNC,
        "手元の台帳は git の repo なので、届かない間も書き続けて、あとで 1 回で合わせられます。"),
    "sync_vault_missing": _entry(
        "その名前の金庫が monban にありません。",
        "1 台目なら thth sync init <金庫> で作ってください（名前の綴りも確かめてください）。",
        "thth sync init <金庫>",
        "金庫の名前を ledger にそろえると、端末ごとの綴り違いが起きません。"),
    "sync_remote_redirected": _entry(
        "remote monban の字面は monban:// でも、git の設定（url.….insteadOf・pushInsteadOf・remote.monban.pushurl）で、"
        "実際に取り込む先か送る先（1 行目の（））が monban でない所に替わっています。そのまま送ると、台帳が暗号なしで別の所へ出ます。",
        "git config --get-regexp 'url\\..*insteadof|remote\\.monban\\.pushurl' で替えている設定を見て外してから、"
        "thth sync を打ち直してください。",
        _SYNC,
        "別の置き場（GitHub など）にも置きたいときは、monban と別の名前の remote にすると、金庫の道と混ざりません。"),
    "sync_join_failed": _entry(
        "戻しの文字列（か合言葉）で金庫を開けませんでした（1 行目の（）が monban の符丁）。",
        "金庫を作ったときの戻しの文字列を 1 行で、もう一度 thth sync join <金庫> に入れてください。",
        "thth sync join <金庫>",
        "戻しの文字列はパスワード管理から貼ると、打ち間違いが起きません。"),
    "sync_init_needs_terminal": _entry(
        "thth sync init は戻しの文字列を 1 度だけ画面に出すので、人の端末でだけ動きます（出力がファイルや会話に流れると秘密が残ります）。",
        "自分の端末（Terminal など）で thth sync init <金庫> を打ち、出た戻しの文字列をパスワード管理に置いてください。",
        "thth sync init <金庫>",
        "作ったあとの thth sync は端末でなくても動くので、日々の合わせは LLM や timer に任せられます。"),
    "sync_failed": _entry(
        "monban の道具か git が、THTH の知らない形で断りました（1 行目の（）が段と monban の符丁）。何も書き換えずに止めました。",
        "thth sync status で金庫の様子を見て、直らなければ 1 行目の（）を添えて thth report file で THTH の開発へ知らせてください。",
        "thth sync status",
        "知らせに 1 行目の（）をそのまま添えると、monban の側の符丁から早く辿れます。"),
    # ------------------------------------------------------------------ thth garden（庭）
    "garden_args_invalid": _entry(
        "thth garden に渡した値が決まりに合っていません（1 行目の（）が指す値。庭の URL は monban://<ハウス>/<アカウント>/<庭>、"
        "庭の名前は英小文字・数字・-（-- と www・login などの予約語は不可）、--shop-url は https の URL）。",
        "1 行目の（）が指す引数を直して打ち直してください。",
        None,
        "庭の名前を店の名前にそろえると、配られる名前（<庭>--<ハウス>.<公開用ドメイン>）が見て分かります。"),
    "garden_tool_missing": _entry(
        "monban の道具（monban-vault）が、この端末に見つかりません。庭に置くのは monban の道具の仕事です。",
        f"{_INSTALL}。",
        None,
        "置く前の検査（thth garden check）は道具なしで動くので、先に頁を仕上げておけます。"),
    "garden_snapshot_missing": _entry(
        "その店の写しがありません（か読めません）。頁は thth shop diff が書く写しから作ります。",
        "thth shop diff <店> を打って写しを作ってから、打ち直してください。",
        "thth shop diff <店>",
        "頁を作る前に thth shop diff で違いを見ておくと、新しく載る商品を先に確かめられます。"),
    "garden_out_not_empty": _entry(
        "--out のフォルダに、thth garden build が作る 3 つ（index.html・items.html・style.css）以外の物があります。"
        "上書きで別の物を消さないよう、書きませんでした。",
        "空のフォルダか、前に thth garden build で作ったフォルダを --out に渡してください。",
        None,
        "庭ごとに出来上がりのフォルダを 1 つ決めておくと、置く物と置かない物が混ざりません。"),
    "garden_dir_missing": _entry(
        "渡したフォルダがありません（か symlink です）。",
        "thth garden build で作ったフォルダの道を渡してください。",
        "thth garden build <店> --out <フォルダ>",
        "build と publish に同じフォルダを渡す決まりにしておくと、取り違えが起きません。"),
    "garden_empty": _entry(
        "フォルダに置くファイルがありません。",
        "thth garden build で頁を作ってから打ち直してください。",
        "thth garden build <店> --out <フォルダ>",
        "build のあと thth garden check で検査してから置くと、一段目の一覧がそのまま見本になります。"),
    "garden_kind_refused": _entry(
        "庭に置けない種類か名前のファイルがあります（1 行目の（）がその道と理由: kind は種類・name は字・dot or link は"
        "点で始まる名前か symlink）。置けるのは html・css・png・jpg・webp・gif・svg・ico・woff2・txt・xml で、名前は英数字と . _ ~ - だけです。",
        "そのファイルを外すか、置ける種類と名前に替えてから打ち直してください。",
        None,
        "画像は webp か png にそろえると、どの端末でも同じに見えて量も小さくなります。"),
    "garden_size_refused": _entry(
        "庭の量の決まりを超えました（1 行目の（）: HTML・SVG・XML は 1 枚 1 MB・1 個 25 MB・全部で 100 MB・2000 個まで）。",
        "大きな画像を縮めるか、頁を分けてから打ち直してください。",
        None,
        "画像を 1 枚 200 KB ほどに縮めると、見る人の待ち時間も短くなります。"),
    "garden_text_refused": _entry(
        "HTML・SVG・XML・CSS が UTF-8 でないか、HTML・SVG・XML が http-equiv・制御文字・UTF-8 以外の文字コードの宣言を"
        "含んでいます（1 行目の（）: utf-8 は UTF-8 でない字か BOM・NUL、http-equiv は meta の http-equiv、control は"
        "タブ・改行・CR 以外の制御文字（ESC など）、encoding は UTF-8 以外の encoding= か charset= の宣言）。http-equiv の"
        "refresh は CSP でも止まらず、偽の頁へ飛ばせます。制御文字と別の文字コードの宣言があると、検査とブラウザとで"
        "別の名前に見えうる（ISO-2022-JP の ESC など）ので、monban の配る側と同じく置けません。",
        "UTF-8（BOM なし）で保存し直し、http-equiv の meta と制御文字を外し、宣言は <meta charset=\"utf-8\"> か"
        " <?xml version=\"1.0\" encoding=\"utf-8\"?> にしてから打ち直してください。本文で charset=… や encoding=… の字を"
        "見せたいときは、= を &#61; と書けば通ります（見た目は同じ）。",
        None,
        "文字コードは UTF-8 の宣言 1 つに任せると、どの端末でも崩れません。エディタの「UTF-8 で保存」と、制御文字を"
        "見せる設定を使うと、混ざった字に先に気づけます。"),
    "garden_active_refused": _entry(
        "頁に、動く部品（script・form・iframe・入力欄・on…= の差し込み・javascript: のリンク）があります（1 行目の（））。"
        "段 1 の庭は静的な頁だけで、偽の sign-in の頁や入力を盗む頁を作れなくしてあります。script or form は字面で"
        "見つけたもので、注（<!-- -->）や CDATA の中の字でも止めます（読み手どうしで注の終わりの読み方が違うため）。",
        "その部品を外し、買う・申し込むは店の頁（https）へのリンクにしてから打ち直してください。部品の名前を字として"
        "見せたいときは &lt;form&gt; のように書けば通ります。",
        None,
        "thth garden build で作ると、決まりに合った頁が最初から出来ます。"),
    "garden_external_refused": _entry(
        "頁が外の資源（外の URL の画像・CSS・フォントなど）を読みに行きます（1 行目の（））。庭は来た人の情報を外へ渡さない"
        "決まりで、配る側の CSP でも外の資源は読めません。",
        "使う画像や CSS をフォルダの中に置き、相対の道（images/a.webp など）で指してから打ち直してください。",
        None,
        "リンク（<a href>）・<link rel=\"canonical\">・rel=\"alternate\"・rel=\"me\"・itemtype・cite は読みに行かないので外へ"
        "向けてかまいません。店や SNS へはリンクで案内できます。"),
    "garden_markup_refused": _entry(
        "頁の書き方に、配る側で効かないか、読み違えられる所があります（1 行目の（）: broken tag は閉じないタグか引用符の"
        "合わないタグ・style は inline の style か <style>（monban の CSP は style-src 'self' なので効きません）・entity は DTD の entity・"
        "comment は <!--> や <!---> や --!> や -- > の注（ブラウザと検査で注の終わりが割れる）・cdata は HTML の <![CDATA[ と "
        "<![if …]> か閉じない CDATA・cdata lt は XML と SVG の CDATA の中の <（検査の読み手が次のタグを見落とすため）・title は <title> の中のタグに見える字・old raw tag は xmp などの古いタグ・"
        "xml-stylesheet は XML と SVG の <?xml-stylesheet（XSLT で頁を組み直せるため））。",
        "タグを閉じて引用符をそろえ、見た目は style.css に移し、DTD の entity を外してから打ち直してください。注は "
        "<!-- 文 --> の形だけにし、字として見せる < は &lt; と書いてください。SVG は style を fill=\"#c00\" などの属性に"
        "書き直すと通ります（Inkscape は「最適化した SVG」で保存、SVGO なら convertStyleToAttrs）。CDATA の中の < は "
        "CDATA を使わず &lt; と書いてください（RSS の本文の HTML も &lt;p&gt; のように escape すれば、読む側には同じに届きます）。",
        None,
        "見た目を style.css 1 枚にまとめると、頁を足しても同じ見た目にそろいます。"),
    "garden_guide_missing": _entry(
        "HTML に「THTH の案内」の枠（data-thth-guide=\"free\" の印）がちょうど 1 つありません（1 行目の（）がその頁）。"
        "無料版の庭は各頁に案内の枠を 1 つ置く決まりです（原則_庭の来訪者と広告 の 7）。この検査はうっかり落とすのを見つけるためのものです。",
        "thth garden build で作り直すか、build が作る枠をその頁に 1 つ入れてから打ち直してください。",
        "thth garden build <店> --out <フォルダ>",
        "枠は build の見本と同じ場所に置くと、承認する姿と出る姿が同じになります。"),
    "garden_publish_unconfirmed": _entry(
        "--confirm の digest が、今のフォルダの中身から作った digest と違います（一段目のあとにファイルが変わったか、別の庭の digest）。",
        "--confirm を付けずに打ち直し、出た一覧を見てから、その digest で二段目を打ってください。",
        "thth garden publish <フォルダ> --to <庭>",
        "一段目と二段目の間にフォルダを触らないと、見た一覧のとおりに出ます。"),
    "garden_not_ready": _entry(
        "この端末に、その庭の札が無いか、庭がまだありません（1 行目の（）が monban の符丁）。庭を作るにはハウスの持ち主の"
        "secret が要ります（招待制の間は運営者が作ります）。",
        "ハウスの持ち主に monban-vault garden init <庭> を頼み、この端末に札を置いてから打ち直してください。",
        None,
        "庭は店ごとに 1 つ作っておくと、止めるときも店ごとに止められます。"),
    "garden_busy": _entry(
        "その間にほかの端末が庭に置いたか、置く回数の上限に当たりました（1 行目の（））。",
        "--confirm を付けずに打ち直し、一覧を見てから置き直してください。",
        "thth garden publish <フォルダ> --to <庭>",
        "庭に置く端末を 1 台に決めておくと、置き合いになりません。"),
    "garden_full": _entry(
        "庭の量の上限（100 MB）に当たりました。",
        "使わない画像を外すか縮めてから打ち直してください。",
        None,
        "画像を縮めて数を絞ると、見る人の待ち時間も短くなります。"),
    "garden_login_required": _entry(
        "monban のアカウントのキーが無いか、失効か期限切れです（1 行目の（））。",
        f"{_LOGIN}、打ち直してください。",
        None,
        "庭に置く端末を決めて、その端末だけに login しておくと、扉が薄くなりません。"),
    "garden_unavailable": _entry(
        "monban の庭に届きませんでした（網か monban の側の様子）。",
        "少し置いてから打ち直してください。",
        None,
        "出来上がりのフォルダは手元に残るので、届いたときに同じ物をそのまま置けます。"),
    "garden_failed": _entry(
        "monban の道具が、THTH の知らない形で断りました（1 行目の（）が段と monban の符丁）。",
        "thth garden status <庭> で様子を見て、直らなければ 1 行目の（）を添えて thth report file で THTH の開発へ知らせてください。",
        "thth garden status <庭>",
        "知らせに 1 行目の（）をそのまま添えると、monban の側の符丁から早く辿れます。"),
}
