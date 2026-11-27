"""断りの目録の項目（括り＝THTH アカウントの器・`thth admin ws …`・`thth/ws_admin.py`・`accounts.workspace_dir`）。
thth/refusal_catalog.py が読む。

設計: docs/設計_THTHアカウント（括り）_叩き台_2026-11-26.md の段 1。原則 `docs/原則_前向きな断り.md`。
`since` は今の版（`thth/VERSION` を越えない柵）。版上げのとき、次の版に直す。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.31.0"
_LIST = "thth admin ws list"


def _entry(why, fix, command, better):
    return {"kind": "refusal", "why": why, "fix": fix, "command": command, "better": better, "doors": _C,
            "since": _SINCE}


ENTRIES = {
    "pass_unsupported": {
        "kind": "refusal",
        "why": "その命令か旗は、鍵で括りの中に通す表にありません。何も打っていません。",
        "fix": "表にある命令と旗で打ち直してください。読むだけの物は board・morning・observe・schedule の一覧・where-log・handoff-report・analytics-report・unanswered・approvals、書く物は approve・revoke・withdraw・hold・reply approve／ignore です（`reply pending` は読むだけ。`--by` は付けません。記録の名乗りは鍵から付きます）。",
        "command": None,
        "better": "表に無い命令や目印を付ける旗が要るときは、括りの窓口のレーンか管理者に頼むと、記録に誰が打ったかが残ります。",
        "doors": ("remote",), "since": _SINCE},
    "pass_target_invalid": {
        "kind": "refusal",
        "why": "その口座か原稿は、この鍵で書ける所にありません（鍵の口座でない・括りの外・口座の repo に無いパス）。何も書いていません。",
        "fix": "`--account <口座>` に鍵の口座を名指しし、原稿は口座の repo の根からの相対パス（例 `queue/2026-12-01.md`）で渡してください。",
        "command": "thth --lane <レーン> approve --account <口座> <repo の中の相対パス>",
        "better": "ディレクトリ（例 `queue/`）を渡すと、承認なら下書きだけ、取り消しなら承認済みだけを VM が拾います。",
        "doors": ("remote",), "since": _SINCE},
    "lane_invalid": {
        "kind": "refusal",
        "why": "レーンの名の形が違います（鍵のファイルの名前になる所です）。何も読んでいません。",
        "fix": "レーンの名は英小文字・数字・`-` で、`<repo>` か `<repo>-<レーン>` にしてください（hub の名乗りのファイルと同じ付け方）。",
        "command": "thth login --lane <レーン>",
        "better": "レーンの入口の文書に `thth --lane <レーン>` の 1 行を書いておくと、毎回の名指しを取り違えません。",
        "doors": ("cli", "remote"), "since": _SINCE},
    # ---- レーンの鍵（括りの段 2・`thth login --lane`・`thth/activity.py` の issue_lane_key・`thth/remote.py`）
    "workspace_mismatch": {
        "kind": "refusal",
        "why": "鍵のファイルに控えた括りと、VM が答えた括りの名が違います。答えの中身は出していません。",
        "fix": "`thth login --lane <レーン>` をやり直して、鍵と括りの控えを揃えてください（前の鍵は VM が取り消します）。",
        "command": "thth login --lane <レーン>",
        "better": "括りが 2 つ以上あるときは `--workspace <括り>` で名指しして login すると、取り違えが起きません。",
        "fix_by_door": {
            "worker": {"why": "コネクタが結ばれた括りと、VM が答えた括りの名が違います。答えの中身は出していません。",
                       "text": "https://thth.me/connectors でこのコネクタを取り消し、同意の頁で括りを名指しして許し直してください", "command": None},
        },
        "doors": ("remote", "worker"), "since": _SINCE},
    "lane_revoke_unconfirmed": {
        "kind": "refusal",
        "why": "手元の鍵のファイルは消しましたが、サーバーでレーンの鍵を止める頼みが届いたかを確かめられていません（網が切れた・Worker が古い・鍵がもう表に無い）。",
        "fix": "`thth login --lane <レーン>` をやり直してください。同じレーンの前の鍵はそのとき VM が取り消します。要らないレーンなら、そのあと `thth logout --lane <レーン>` をもう一度打ちます。",
        "command": "thth login --lane <レーン>",
        "better": "レーンをやめるときは網のつながる所で logout を打つと、サーバーの鍵も数十秒で止まり、管理者の一覧に生きた鍵が残りません。",
        "doors": ("remote",), "since": _SINCE},
    "lane_key_not_found": {
        "kind": "refusal",
        "why": "取り消すように頼まれたレーンの鍵が、その持ち主の括りの表にありません（もう消えたか、ほかの持ち主の鍵です）。何も変えていません。",
        "fix": "コネクタなら https://thth.me/connectors の一覧を、レーンなら `thth --lane <レーン> board` が通るかを確かめてください。通るなら `thth login --lane <レーン>` をやり直すと前の鍵が止まります。",
        "command": "thth login --lane <レーン>",
        "better": "取り消しは持ち主の頁（/connectors）か `thth logout --lane` の 1 か所から打つと、どの鍵を止めたかが記録に揃います。",
        "doors": ("remote",), "since": _SINCE},
    "workspace_not_owned": {
        "kind": "refusal",
        "why": "入れた持ち主は、その括りの持ち主ではありません（または、その名の括りはありません）。VM は鍵を作っていません。",
        "fix": "括りの持ち主の名前と持ち主の secret で、`thth login --lane <レーン>` をやり直してください。括りの名は持ち主か管理者に確かめてください。",
        "command": "thth login --lane <レーン> --workspace <括り>",
        "better": "レーンの入口の文書に括りの名と `thth --lane <レーン>` を 1 行書いておくと、次の login で迷いません。",
        "doors": ("remote",), "since": _SINCE},
    "workspace_required": {
        "kind": "refusal",
        "why": "持ち主の括りが 2 つ以上あるので、どの括りに鍵を結び付けるか決められません。VM は鍵を作っていません。",
        "fix": "`--workspace <括り>` を付けて login をやり直してください。",
        "command": "thth login --lane <レーン> --workspace <括り>",
        "better": "括りごとにレーンの名を分けておく（例 `<repo>-<レーン>`）と、鍵のファイルも取り違えません。",
        "doors": ("remote",), "since": _SINCE},
    "workspace_has_no_accounts": {
        "kind": "refusal",
        "why": "その括りにはまだ口座が 1 つもないので、レーンの鍵を結び付ける先がありません。VM は鍵を作っていません。",
        "fix": "管理者に括りの中へ口座を足してもらってから（`thth account add`）、`thth login --lane <レーン>` をやり直してください。",
        "command": "thth login --lane <レーン> --workspace <括り>",
        "better": "口座を足すときに、どのレーンが使うかを入口の文書に書いておくと、login の順が決まります。",
        "doors": ("remote",), "since": _SINCE},
    "lane_key_unconfirmed": {
        "kind": "refusal",
        "why": "受け取った鍵が VM で生きたことを、まだ確かめられていません（VM の巡が遅れている・login の期限が切れた）。",
        "fix": "少し置いて `thth --lane <レーン> board` を打ち、通らなければ `thth login --lane <レーン>` をやり直してください。",
        "command": "thth login --lane <レーン>",
        "better": "login は VM が動いている時間に打つと、数十秒で鍵が生きて括りの名まで確かめられます。",
        "doors": ("remote",), "since": _SINCE},
    "workspace_name_invalid": {**_entry(
        "括りの名の形が違います。何も書いていません。",
        "括りの名は小文字で始まる英小文字・数字・`-`（32 字まで）にしてください。",
        _LIST,
        "媒体群の呼び名に近い短い名にすると、一覧と置き場の名前が読みやすくなります。"),
        "doors": ("cli", "remote")},  # `thth login --lane --workspace <括り>` の形の断りも同じ文
    "workspace_unknown": _entry(
        "台帳にその名の括りがありません。",
        "`thth admin ws list` で括りの名を確かめてください。",
        _LIST,
        "括りを作るときは `thth admin ws add <括り> --lead <媒体>` で、台帳と置き場が一緒にできます。"),
    "workspace_exists": _entry(
        "同じ名の括りがもうあります。何も書いていません。",
        "`thth admin ws show <括り>` で中を見てください。別の括りなら別の名にしてください。",
        "thth admin ws show <括り>",
        "媒体を足すだけなら `thth admin ws media add <括り> <媒体>` で済みます。"),
    "workspace_folder_exists": _entry(
        "台帳に無いのに、括りのフォルダ `ws/<括り>/` が残っています。何も書いていません。",
        "フォルダの中を確かめ、要らなければ名前を替えて退けてから足し直してください。",
        _LIST,
        "括りは `thth admin ws add` でだけ作ると、台帳とフォルダが食い違いません。"),
    "workspace_unavailable": _entry(
        "その括りのフォルダがありません（作られていないか、symlink になっています）。",
        "`thth admin ws list` で、フォルダのある括りか確かめてください。",
        _LIST,
        "括りの名は鍵か `THTH_WS` で決まります。打つ人が名指しするのは管理者だけにすると取り違えません。"),
    "medium_name_invalid": _entry(
        "媒体の名前の形が違います。何も書いていません。",
        "媒体の名前は repo の名前と同じ綴り（英小文字・数字・`-`）にしてください。",
        _LIST,
        "hub の project と h2d の owner と同じ綴りにすると、3 つの部品が同じ括りを引けます。"),
    "medium_in_other_workspace": _entry(
        "その媒体は、ほかの括りに結ばれています。台帳の書き換えでは括りを移しません（置き場のデータが付いてこないため）。",
        "括りを移すのは、置き場ごと移す管理者の段取りで行ってください。",
        _LIST,
        "新しい媒体群は新しい媒体の名前（新しい repo）で始めると、移す作業が要りません。"),
    "workspace_secret_outside": _entry(
        "main でない括りの台帳が、括りの secrets/ の外（`~` や、ほかの括り・共通の根）の秘密を指しています。読んでいません。",
        "秘密のファイルを `ws/<括り>/secrets/` に移し、台帳の env・token（主体なら token・consumer）を `$THTH_ROOT/secrets/<名前>.<種類>` に直してください。",
        "thth admin ws show <括り>",
        "新しい括りの口座は、その括りの中で `thth account add` すると、初めから括りの secrets/ を指す台帳ができます。"),
    "account_in_two_workspaces": _entry(
        "同じ名前の口座の台帳が 2 つの括りにあります。どちらにも入らずに止めました。",
        "どちらの括りの口座かを確かめ、もう一方の台帳の名前を替えて退けてください（口座の名前は全部の括りを通して 1 つ）。",
        _LIST,
        "口座は `thth account add` でだけ作ると、ほかの括りにある名前を先に断れます。"),
    "project_in_two_workspaces": _entry(
        "同じ project の口座が 2 つの括りにあります。どちらの括りの地図を集めるか決められないので、どちらにも入らずに止めました。",
        "どちらの括りの project かを決め、もう一方の括りの口座の project を別の名に直してください（project は 1 つの括りの中に置く）。",
        _LIST,
        "新しい括りの project は新しい媒体の名前で始めると、ほかの括りとぶつかりません。"),
    "account_name_in_other_workspace": _entry(
        "同じ名前の口座が、ほかの括りにもうあります（口座の名前は全部の括りを通して 1 つ）。何も書いていません。",
        "別の名前にしてください（例 `<媒体>-<括り>`）。その口座をこの括りに移したいなら、置き場ごと移す管理者の段取りで行ってください。",
        _LIST,
        "名前に媒体の名を頭に付けておくと、括りが増えても名前がぶつかりません。"),
    "workspaces_map_invalid": _entry(
        "括りの台帳（workspaces.json）が読めないか、形が違います。新しく書く操作はしていません。",
        "`tools/workspaces_check.py` で台帳の形を確かめ、誤りの行を直してください。",
        _LIST,
        "台帳は手で直さず `thth admin ws …` だけで書くと、途中の姿が読まれません。"),
}
