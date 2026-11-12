"""断りの目録の項目（第 9 部: 店を読む口・`thth/shop/`・`thth shop …`）。thth/refusal_catalog.py が読む。

符丁は `thth/shop/wire.py` の `REASONS`。BASE の断り（Problem Details の `type`・HTTP の数）は
`thth/shop/base.py` の `refusal_for` がここの符丁に写し、`type` を詳細として 1 行目の（）に添える。
since は版上げのときに直す。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.15.0"
_ADD = "thth shop add <店> --kind base --project <project> --by <名前>"
_TOKEN = "thth shop token set <店> --expires <日付> --by <名前>"
_WHOAMI = "thth shop whoami <店>"
_DIFF = "thth shop diff <店>"
_ITEMS = "thth shop items <店>"
_REMOVE = "thth shop remove <店> --by <名前>"
_PAT = "BASE Developers（https://developers.thebase.com/）で、読むだけの権限（items.read・user.read）の PAT を発行し"
_NOT_BASE = "この道具は BASE が提供するものではないので、BASE の窓口でなく THTH の開発のセッションへ thth report file で知らせると早く届きます。"


def _entry(why, fix, command, better, kind="refusal"):
    return {"kind": kind, "why": why, "fix": fix, "command": command, "better": better, "doors": _C,
            "since": _SINCE}


ENTRIES = {
    "store_args_invalid": _entry(
        "thth shop に渡した値が決まりに合っていません（店の名前は英数字と _・.・-、期限は YYYY-MM-DD、--by は誰が頼んだか、"
        "draft は --item か --kind のどちらか 1 つ）。",
        "1 行目の（）が指す引数を直して打ち直してください。",
        _ADD,
        "店の名前は <project>-base の形にそろえると、どの project の店か名前だけで分かります。"),
    "store_name_taken": _entry(
        "その名前の店の台帳が既にあります。上書きすると前の鍵の置き場と写しを取り違えるので、書きませんでした。",
        "別の名前で thth shop add を打ち直すか、今の台帳のまま thth shop whoami <店> で中身を確かめてください。",
        _WHOAMI,
        "同じ店をつなぎ直したいだけなら、台帳はそのままで thth shop token set で鍵だけ差し替えれば済みます。"),
    "store_missing": _entry(
        "その名前の店の台帳がありません。",
        "先に thth shop add で台帳を書いてください（名前の綴りも確かめてください）。",
        _ADD,
        "台帳を書いたら、続けて thth shop token set で鍵を渡すと、すぐ thth shop whoami で確かめられます。"),
    "store_ledger_invalid": _entry(
        "店の台帳か写し（store_state/<店>/snapshot.json）が読めないか、知らない欄・足りない欄があります。送り先を台帳で変える欄は受けません。",
        "台帳は store・kind・project・token の 4 つだけにしてください。台帳か写しが壊れたときは、thth shop remove <店> --by <名前> で"
        "外し（二段確認・鍵と写しと台帳を消します）、thth shop add から作り直します。",
        _REMOVE,
        "台帳は手で直さず thth shop add で作り直すと、欄の取り違えが起きません。"),
    "store_token_missing": _entry(
        "この店の鍵（BASE の PAT）がまだ置かれていないか、鍵のファイルが読めません。",
        _PAT + "、thth shop token set <店> --expires <期限の日> --by <名前> で渡してください。",
        _TOKEN,
        "PAT の期限は発行のときに決まり、あとで変えられないので、期限の日を控えて --expires に入れると 14 日前から知らせます。"),
    "store_token_input_unsafe": _entry(
        "標準入力が端末ではないので、隠して読めません。このまま読むと鍵が画面に出るおそれがあります。",
        "パイプやファイルから渡すときは --stdin を付けてください。対話で入れるときは端末から打つか、ssh なら -t を付けてください。",
        _TOKEN + " --stdin",
        "鍵は引数では受けません。履歴に残らない標準入力か隠した入力で渡すのが安全です。"),
    "store_token_empty": _entry(
        "受け取った鍵が空か、空白や改行を含む形でした。",
        "BASE Developers で表示された PAT を 1 行のまま貼り付けて、もう一度 thth shop token set を打ってください。",
        _TOKEN,
        "パスワードの道具から貼ると、前後の空白や改行が混ざりにくくなります。"),
    "store_token_expires_past": _entry(
        "--expires に渡した期限の日が、今日より前の日です（1 行目の（）がその日）。鍵は保存していません。",
        "BASE Developers で PAT を発行したときに決めた期限の日（今日か、それより先の日）を YYYY-MM-DD で渡して、打ち直してください。",
        _TOKEN,
        "期限の日は発行の画面に出ます。控えておくと、THTH が 14 日前から知らせるので、切れる前に差し替えられます。"),
    "store_token_expired": _entry(
        "置いてある鍵（BASE の PAT）の期限を過ぎています。BASE の PAT は期限を発行のあとに延ばせません。",
        _PAT + "、新しい期限の日と一緒に thth shop token set で差し替えてください。",
        _TOKEN,
        "期限は長めにして控えておくと、THTH が 14 日前から thth morning で知らせるので、切れる前に差し替えられます。"),
    "store_token_rejected": _entry(
        "BASE が鍵を受け付けませんでした（401）。期限切れか、取り消された PAT です。鍵は保存していません（token set のとき）。",
        _PAT + "、thth shop token set で差し替えてください。",
        _TOKEN,
        "差し替えたら thth shop whoami <店> で店の名前が出るかを確かめると、次の読みで止まりません。" + _NOT_BASE),
    "store_scope_missing": _entry(
        "鍵に、この読みに要る権限がありません（403）。商品は items.read、店の名前と URL は user.read が要ります。",
        _PAT + "、thth shop token set で差し替えてください。",
        _TOKEN,
        "注文（orders.read）や書く権限は付けないでください。THTH は読むだけなので、権限が少ないほど漏れたときの害が小さくなります。"),
    "store_shop_mismatch": _entry(
        "新しい鍵の店（shop_id）が、前に置いた鍵の店と違います。別の店の写しと混ざらないように保存しませんでした。",
        "同じ店の PAT か確かめてください。別の店なら thth shop add で新しい名前の台帳を作るか、置き換えるなら --force を付けます。",
        _TOKEN + " --force",
        "店ごとに台帳を分けておくと、下書きの出所（store/<店>/item/<id>）で取り違えを見分けられます。"),
    "store_api_changed": _entry(
        "BASE の応答に、要る欄が無いか、型が変わっていました（v2 は β で予告なく変わることがあります）。写しは進めていません。",
        "時間を置いて同じ命令を打ち直してください。続くなら、命令と時刻を添えて thth report file で開発のセッションへ知らせてください。",
        _ITEMS,
        "BASE Developers の変更履歴（https://apiv2.thebase.com/docs/changelog/）を見ると、何が変わったかの手がかりになります。" + _NOT_BASE),
    "store_unavailable": _entry(
        "BASE に届かないか、BASE が一時的に応えませんでした（保守・5xx・混雑）。再試行はしていません。",
        "少し時間を置いて打ち直してください。保守中なら、終わってから打ち直します。",
        _ITEMS,
        "定期に読むなら間を広めに空けると、β の上限が入ったときにも止まりにくくなります。"),
    "store_request_rejected": _entry(
        "BASE がこの問い合わせを受け付けなかったか、THTH の柵（GET だけ・apiv2.thebase.com だけ・許した道だけ）が打たずに止めました。",
        "1 行目の（）の語を添えて、thth report file で開発のセッションへ知らせてください。",
        _WHOAMI,
        "thth shop whoami <店> が通るかを先に見ると、鍵の問題か問い合わせの問題かを切り分けられます。" + _NOT_BASE),
    "store_read_incomplete": _entry(
        "商品の全部を 1 回の読みで読み切れませんでした（1 回に読むのは 100 件ずつ 5 頁・500 件まで。ちょうど 500 件の店もここに当たります）。"
        "読み切れないまま写しにすると、読めなかった商品を「消えた」と取り違えます。",
        "写しは進めていません。商品が上限より多い店は、開発のセッションへ thth report file で上限の相談をしてください。",
        _ITEMS,
        "thth shop items <店> で今読める分の一覧は見られます。違いを見るだけなら thth shop diff <店>（--accept なし）は通ります。"),
    "store_draft_nothing": _entry(
        "選んだ商品・種類に、下書きにできる違いがありません（公開で買える商品の新着・再入荷・値下げだけを下書きにします。"
        "在庫 0・販売前・販売終了・名前が空の商品は作りません）。",
        "thth shop diff <店> で今の違いを確かめてから、--item か --kind を選び直してください。",
        _DIFF,
        "売り切れ・値上げの下書きは作りません。新着や再入荷のときに作ると、見た人がそのまま買えるお知らせになります。"),
    "store_draft_no_baseline": _entry(
        "比べる写しがまだ無いので、違いが分かりません（初めての店か、別の店の鍵に替えて写しを捨てたあと）。",
        "先に thth shop diff <店> で基準を取ってください。そのあとに増えた・戻った・下がった商品から下書きを作れます。",
        _DIFF,
        "基準を取った直後は違いが 0 なので、新着を足したり在庫を戻したりしたあとに thth shop draft を打つと、お知らせにできます。"),
    "store_not_open": _entry(
        "店がまだ公開されていません（BASE の able_to_business が偽）。見た人が買えないお知らせになるので、下書きを作りませんでした。",
        "BASE の管理画面で店を公開してから、もう一度 thth shop draft を打ってください。",
        _WHOAMI,
        "thth shop whoami <店> の「公開」が「はい」になってから作ると、お知らせを見た人がそのまま買えます。"),
    "store_item_changed": _entry(
        "下書きを作ったあとに、その商品の値段・公開・在庫が変わっていました（1 行目の（）が変わった所）。今のまま出すと中身が店と食い違うので、承認しませんでした。",
        "thth shop items <店> で今の商品を確かめ、下書きの本文と頭（from_store_price）を今に合わせて直すか、出さないなら下書きを閉じてください。",
        _ITEMS,
        "売り切れなら、再入荷のあとの thth shop draft --kind restock で作り直すと、見た人がそのまま買えるお知らせになります。"),
    "store_draft_exists": _entry(
        "同じ店・同じ商品・同じ違いの下書きが、もう queue にあります（日をまたいでも、写しを進めていない間の同じ違いは 1 本だけ）。上書きはしません。",
        "queue にある下書きを開いて整えてください。作り直したいときは、その下書きを消してから打ち直します。",
        _DIFF,
        "下書きを整えたら thth shop diff <店> --accept で写しを進めると、次の違いだけが下書きの元になります。"),
    "store_draft_account": _entry(
        "--to の口座の台帳が読めないか、その口座の queue（repo_dir と queue_dir）が見つかりません。",
        "thth account <口座> で口座と queue の置き場を確かめてから、--to <口座> を直して打ち直してください。",
        "thth account <口座>",
        "店と同じ project の口座を選ぶと、出す先の取り違えが起きにくくなります。"),
    "store_draft_rejected": _entry(
        "作った下書きが queue の lint を通りませんでした（1 行目の（）が lint の符丁）。queue には置いていません。",
        "口座の媒体の文字数や決まりを thth lint で確かめ、続くなら thth report file で開発のセッションへ知らせてください。",
        _DIFF,
        "1 行目の（）が文字数なら、BASE の管理画面で商品名を短くしてください。hashtags の符丁なら、商品名の # がハッシュタグと"
        "読まれています（ハッシュタグを使わない口座）。BASE で商品名から # を外すと、どの媒体の下書きにも収まります。"),
    "store_remove_unconfirmed": _entry(
        "--confirm の digest が、今消す物の一覧と合いません（1 行目の（）が今の digest）。表示したあとに台帳・写し・鍵のどれかが変わりました。何も消していません。",
        "--confirm を付けずに thth shop remove <店> --by <名前> を打ち直し、消す物の一覧を見てから、出た digest で二段目を打ってください。",
        _REMOVE,
        "一段目の一覧に「残す」と出た鍵は、既定の置き場でないので THTH は消しません。店の鍵なら手で消すと、手元に鍵が残りません。"),
    "store_remove_unsafe": _entry(
        "消す物の置き場が、symlink か普通のファイルでない物でした（1 行目の（）が分類）。店のものでない物を消さないように、何も消していません。",
        "その置き場を ls -l で確かめ、店の台帳・写し・鍵の本物のファイルに戻してから、thth shop remove を打ち直してください。",
        _REMOVE,
        "台帳と鍵は thth shop add と thth shop token set で作ると、既定の置き場に普通のファイルとしてでき、外すときも迷いません。"),
    "store_log_unavailable": _entry(
        "変更の記録（admin_log）に書けませんでした。誰が店の台帳・鍵を変えたかを残せないので、何も変えていません（1 行目の（）が after_change なら、変更はしたが記録だけ書けていません）。",
        "THTH_ROOT の state/_admin の置き場の権限（0700・記録のファイルは 0600）を確かめてから、打ち直してください。",
        "thth doctor",
        "記録の置き場は thth doctor で確かめられます。直ってから打てば、変更と記録がそろって残ります。"),
}
