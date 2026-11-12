"""断りの目録の項目（第 9 部: 店を読む口・`thth/shop/`・`thth shop …`）。thth/refusal_catalog.py が読む。

符丁は `thth/shop/wire.py` の `REASONS`。BASE の断り（Problem Details の `type`・HTTP の数）は
`thth/shop/base.py` の `refusal_for` がここの符丁に写し、`type` を詳細として 1 行目の（）に添える。
Shopify の断り（HTTP の数・GraphQL の `extensions.code` を検査した語）は `thth/shop/shopify.py` が写す。
文は店の種類で決め打ちせず、BASE と Shopify のどちらでも読めるように書く（設計 Shopify と STORES §8）。
since は版上げのときに直す（Shopify の分は `_SINCE_SHOPIFY`・5.17.0 の版上げで 5.17.0 にする）。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.15.0"
# 5.17.0 で足す符丁（版はまだ 5.16.0 なので、目録の柵「今の版を越えない」に合わせて今の版を置く・版上げで直す）。
_SINCE_SHOPIFY = "5.16.0"
_ADD = "thth shop add <店> --kind base --project <project> --by <名前>"
_TOKEN = "thth shop token set <店> --expires <日付> --by <名前>"
_WHOAMI = "thth shop whoami <店>"
_DIFF = "thth shop diff <店>"
_ITEMS = "thth shop items <店>"
_REMOVE = "thth shop remove <店> --by <名前>"
_PAT = "BASE Developers（https://developers.thebase.com/）で、読むだけの権限（items.read・user.read）の PAT を発行し"
_SHOPIFY_TOKEN = "thth shop token set <店> --auth client --by <名前>"
_SHOPIFY_KEY = ("Shopify なら、Dev Dashboard のアプリの版で scope を read_products だけにして店に入れ、Settings の Client ID と"
                " Client secret を " + _SHOPIFY_TOKEN + " で渡してください")
_NOT_BASE = "この道具は BASE や Shopify が提供するものではないので、店の窓口でなく THTH の開発のセッションへ thth report file で知らせると早く届きます。"


def _entry(why, fix, command, better, kind="refusal", since=_SINCE):
    return {"kind": kind, "why": why, "fix": fix, "command": command, "better": better, "doors": _C,
            "since": since}


ENTRIES = {
    "store_args_invalid": _entry(
        "thth shop に渡した値が決まりに合っていません（店の名前は英数字と _・.・-、期限は YYYY-MM-DD、--by は誰が頼んだか、"
        "draft は --item か --kind のどちらか 1 つ、Shopify の --shop は myshopify のラベル、Shopify の鍵に --expires は付けず、"
        "BASE の鍵に --auth は付けない）。",
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
        "台帳は店の種類ごとに決まった欄だけ（BASE は store・kind・project・token、Shopify はそれに shop を足した 5 つ）にしてください。"
        "台帳か写しが壊れたときは、thth shop remove <店> --by <名前> で"
        "外し（二段確認・鍵と写しと台帳を消します）、thth shop add から作り直します。",
        _REMOVE,
        "台帳は手で直さず thth shop add で作り直すと、欄の取り違えが起きません。"),
    "store_token_missing": _entry(
        "この店の鍵（BASE は PAT・Shopify は Dev Dashboard のアプリの client id と secret か、既にある固定の鍵）がまだ置かれて"
        "いないか、鍵のファイルが読めません。",
        "BASE なら" + _PAT + "、thth shop token set <店> --expires <期限の日> --by <名前> で渡してください。" + _SHOPIFY_KEY + "。",
        _TOKEN,
        "PAT の期限は発行のときに決まり、あとで変えられないので、期限の日を控えて --expires に入れると 14 日前から知らせます。"),
    "store_token_input_unsafe": _entry(
        "標準入力が端末ではないので、隠して読めません。このまま読むと鍵が画面に出るおそれがあります。",
        "パイプやファイルから渡すときは --stdin を付けてください。対話で入れるときは端末から打つか、ssh なら -t を付けてください。",
        _TOKEN + " --stdin",
        "鍵は引数では受けません。履歴に残らない標準入力か隠した入力で渡すのが安全です。"),
    "store_token_empty": _entry(
        "受け取った鍵が空か、空白や改行を含む形でした。",
        "表示された鍵（BASE の PAT・Shopify の Client ID と Client secret はそれぞれ 1 行）をそのまま貼り付けて、もう一度 thth shop token set を打ってください。",
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
        "店が鍵を受け付けませんでした（401）。BASE なら期限切れか取り消された PAT、Shopify なら取り消された固定の鍵か、取り直した鍵でも"
        "通らなかった（アプリが店から外された）ときです。鍵は保存していません（token set のとき）。",
        "BASE なら" + _PAT + "、thth shop token set で差し替えてください。" + _SHOPIFY_KEY + "。",
        _TOKEN,
        "差し替えたら thth shop whoami <店> で店の名前が出るかを確かめると、次の読みで止まりません。" + _NOT_BASE),
    "store_scope_missing": _entry(
        "鍵に、この読みに要る権限がありません（403・Shopify の ACCESS_DENIED・1 行目の（）が手がかり）。BASE は商品に items.read、"
        "店の名前と URL に user.read が要ります。Shopify は read_products が要ります。",
        "BASE なら" + _PAT + "、thth shop token set で差し替えてください。" + _SHOPIFY_KEY + "。",
        _TOKEN,
        "注文（orders.read・read_orders）や書く権限は付けないでください。THTH は読むだけなので、権限が少ないほど漏れたときの害が小さくなります。"),
    "store_shop_mismatch": _entry(
        "新しい鍵の店（shop_id）が、前に置いた鍵の店と違います（Shopify は、鍵の店の myshopify のドメインが台帳のラベルと違うときも）。"
        "別の店の写しと混ざらないように保存しませんでした。",
        "1 行目の（）が myshopify_domain なら、鍵の店と台帳の shop のラベルが違うので --force では通りません。台帳のラベルが違うなら"
        " thth shop remove <店> --by <名前> で外して thth shop add で正しいラベルの台帳を作り直し、鍵が違うならその店の鍵を渡してください。"
        "それ以外は、同じ店の鍵か確かめてください。別の店なら thth shop add で新しい名前の台帳を作るか、置き換えるなら --force を付けます。",
        _TOKEN + " --force",
        "店ごとに台帳を分けておくと、下書きの出所（store/<店>/item/<id>）で取り違えを見分けられます。"),
    "store_api_changed": _entry(
        "店の応答に、要る欄が無いか、型が変わっていました（BASE の v2 は β で予告なく変わることがあります。Shopify は API の版 2026-10 を読みます）。写しは進めていません。",
        "時間を置いて同じ命令を打ち直してください。続くなら、命令と時刻を添えて thth report file で開発のセッションへ知らせてください。",
        _ITEMS,
        "BASE Developers の変更履歴（https://apiv2.thebase.com/docs/changelog/）か Shopify の changelog（https://shopify.dev/changelog）"
        "を見ると、何が変わったかの手がかりになります。" + _NOT_BASE),
    "store_unavailable": _entry(
        "店（BASE・Shopify）に届かないか、一時的に応えませんでした（保守・5xx・混雑・Shopify の点数の上限 throttled・凍った店 shop_frozen）。再試行はしていません。",
        "少し時間を置いて打ち直してください。保守中なら、終わってから打ち直します。",
        _ITEMS,
        "定期に読むなら間を広めに空けると、上限に当たりにくくなります。1 行目の（）が shop_frozen なら、Shopify の管理画面で店の契約を確かめてください。"),
    "store_request_rejected": _entry(
        "店がこの問い合わせを受け付けなかったか、THTH の柵（決まった送り先の決まった道に決まった中身だけ・リダイレクトは追わない）が"
        "打たずに止めました（1 行目の（）が redirect なら、店がリダイレクトを返しました）。",
        "1 行目の（）の語を添えて、thth report file で開発のセッションへ知らせてください。",
        _WHOAMI,
        "thth shop whoami <店> が通るかを先に見ると、鍵の問題か問い合わせの問題かを切り分けられます。" + _NOT_BASE),
    "store_read_incomplete": _entry(
        "商品の全部を 1 回の読みで読み切れませんでした（1 回に読むのは BASE は 100 件ずつ 5 頁、Shopify は 50 件ずつ 10 頁で、どちらも 500 件まで。"
        "ちょうど 500 件の BASE の店もここに当たります）。"
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
    # ---- Shopify（5.17.0・設計 Shopify と STORES §4.2 の表）
    "store_org_mismatch": _entry(
        "Shopify が、この店では client id と secret で鍵を取らせませんでした（shop_not_permitted）。この取り方は、アプリと店が"
        "同じ Shopify の組織に属するときだけ通ります。鍵は保存していません。",
        "Dev Dashboard で、アプリと店が同じ組織に並んでいるか確かめてください。店の管理画面から作った開発用の店は通らないので、"
        "Dev Dashboard から店を作ります。組織を合わせられない店で、既にある管理画面のカスタムアプリの固定の鍵があるなら、"
        "thth shop token set <店> --auth token --by <名前> で渡してください。",
        "thth shop token set <店> --auth token --by <名前>",
        "Dev Dashboard のアプリを同じ組織の店に入れると、THTH が約 24 時間ごとに鍵を取り直すので、鍵を差し替える手間がなくなります。",
        since=_SINCE_SHOPIFY),
    "store_refresh_rejected": _entry(
        "Shopify が、置いてある client id と secret で鍵を取らせませんでした（1 行目の（）が HTTP の数）。secret を作り直したか、"
        "アプリを店から外したときに起きます。",
        "Dev Dashboard のアプリの Settings で Client ID と Client secret を写し直し、" + _SHOPIFY_TOKEN + " で渡してください。"
        "アプリが店に入っているかも確かめます。",
        _SHOPIFY_TOKEN,
        "渡したら thth shop whoami <店> で店の名前が出るかを確かめると、次の読みで止まりません。" + _NOT_BASE,
        since=_SINCE_SHOPIFY),
    "store_refresh_busy": _entry(
        "別の thth がこの店の鍵を取り直している最中で、20 秒待っても鎖が空きませんでした。同じ鍵を同時に 2 回取り直さないよう、打たずに止めました。",
        "少し待ってから、同じ命令を打ち直してください。",
        _WHOAMI,
        "同じ店を定期に読む仕組みを 2 つ置いているなら、1 つにまとめると待ちが起きにくくなります。",
        since=_SINCE_SHOPIFY),
    "store_shop_unknown": _entry(
        "Shopify にその店が見つかりませんでした（404）。台帳の shop のラベルが違うか、店が閉じられています。",
        "店の管理画面の URL（admin.shopify.com/store/<ここ>）の <ここ> を確かめ、違っていれば thth shop remove <店> --by <名前> で外してから、"
        "thth shop add <店> --kind shopify --project <project> --shop <ラベル> --by <名前> で作り直してください。",
        _REMOVE,
        "店の名前を変えても、myshopify のラベルは作ったときのまま残ります。管理画面のドメインの設定に出る <ここ>.myshopify.com の <ここ> を使うと迷いません。",
        since=_SINCE_SHOPIFY),
    "store_currency_unsupported": _entry(
        "店の通貨が円でないか、値段に小数がありました（1 行目の（）が fraction なら小数）。初めの版は円の店だけを読みます（値段を整数の円で持つため）。"
        "写しは進めていません。",
        "円の店なら、商品の値段を整数の円にそろえてから打ち直してください。円でない店を読みたいときは、命令と店の通貨を添えて thth report file で開発のセッションへ知らせてください。",
        _ITEMS,
        "知らせるときに店の通貨を添えると、どの通貨から足すかを決める材料になります。",
        since=_SINCE_SHOPIFY),
}
