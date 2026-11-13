"""断りの目録の項目（第 9 部: 店を読む口・`thth/shop/`・`thth shop …`）。thth/refusal_catalog.py が読む。

符丁は `thth/shop/wire.py` の `REASONS`。BASE の断り（Problem Details の `type`・HTTP の数）は
`thth/shop/base.py` の `refusal_for` がここの符丁に写し、`type` を詳細として 1 行目の（）に添える。
Shopify の断り（HTTP の数・GraphQL の `extensions.code` を検査した語）は `thth/shop/shopify.py` が写す。
STORES の断り（HTTP の数だけ・5.18.0）は `thth/shop/stores.py` が写す（新しい符丁は無い）。
文は店の種類で決め打ちせず、BASE・Shopify・STORES のどれでも読めるように書く（設計 Shopify と STORES §8）。
since は版上げのときに直す（Shopify の分は `_SINCE_SHOPIFY`・5.17.0 の版上げで 5.17.0 にする）。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.15.0"
# 5.17.0 で足す符丁（版はまだ 5.16.0 なので、目録の柵「今の版を越えない」に合わせて今の版を置く・版上げで直す）。
_SINCE_SHOPIFY = "5.17.0"
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
_STORES_TOKEN = "thth shop token set <店> --by <名前>"
_STORES_KEY = ("STORES なら、開発パートナーの登録でプライベートアプリとして発行されたクレデンシャルを " + _STORES_TOKEN
               + " で渡してください（期限の日を知っていれば --expires <日>）")
_NOT_BASE = "この道具は BASE・Shopify・STORES が提供するものではないので、店の窓口でなく THTH の開発のセッションへ thth report file で知らせると早く届きます。"


def _entry(why, fix, command, better, kind="refusal", since=_SINCE, by_detail=None):
    entry = {"kind": kind, "why": why, "fix": fix, "command": command, "better": better, "doors": _C,
             "since": since}
    if by_detail:
        entry["by_detail"] = by_detail
    return entry


ENTRIES = {
    "store_args_invalid": _entry(
        "thth shop に渡した値が決まりに合っていません（店の名前は英数字と _・.・-、期限は YYYY-MM-DD、--by は誰が頼んだか、"
        "draft は --item か --kind のどちらか 1 つ、Shopify の --shop は myshopify のラベル、Shopify の鍵に --expires は付けず、"
        "BASE と STORES の鍵に --auth は付けない、STORES の --url は店の頁の https の URL を ASCII の字だけで、ユーザー名・? の"
        "問い合わせ・# のない形）。",
        "1 行目の（）が指す引数を直して打ち直してください。",
        "thth shop add <店> --kind <base|shopify|stores> --project <project> --by <名前>",
        "店の名前は <project>-base・<project>-shopify・<project>-stores のように project と店の種類をつなげると、どの店か名前だけで分かります。",
        by_detail={
            # 1 行目の（）の語ごとの通し方（`thth/shop/` が添える語・`tests/test_store_args_refusal.py` が縛る）。
            "store_name": {
                "fix": "店の名前を英数字と _・.・- だけで付け直して打ち直してください（空白・斜線・日本語は使えません）。",
                "command": "thth shop add <店> --kind <base|shopify|stores> --project <project> --by <名前>",
                "better": "店の名前は <project>-base・<project>-shopify・<project>-stores のように project と店の種類をつなげると、どの店か名前だけで分かります。"},
            "project": {
                "fix": "--project に thth の project の名前（英数字と _・.・-）を入れて打ち直してください。",
                "command": "thth shop add <店> --kind <base|shopify|stores> --project <project> --by <名前>",
                "better": "口座の台帳と同じ project の名前にそろえると、下書きをどの project の queue に置くかで迷いません。"},
            "by": {
                "fix": "--by に、頼んだ人の名前を入れて打ち直してください（変更の記録に残ります）。",
                "command": "thth shop token set <店> --by <名前>",
                "better": "いつも同じ名前で --by を付けると、記録をあとで読んだとき誰が足した・差し替えたかがすぐ分かります。"},
            "shop": {
                "fix": "--shop は Shopify の店だけに付けます。Shopify なら myshopify のラベル（admin.shopify.com/store/<ここ> か <ここ>.myshopify.com の <ここ>・英小文字と数字と -）を入れ、BASE・STORES なら --shop を外して打ち直してください。",
                "command": "thth shop add <店> --kind shopify --project <project> --shop <ラベル> --by <名前>",
                "better": "独自ドメインでなく管理画面の番地にあるラベルを写すと、THTH が送る先が店の myshopify のホストに決まります。"},
            "url": {
                "fix": "--url は STORES の店だけに付けます。STORES なら、店の頁の https の URL を ASCII の字だけで入れてください。日本語のドメインは punycode（xn--…）、日本語の道は % で包んだ形にします。ブラウザの番地の欄から写すと、その形になります。ユーザー名・? の問い合わせ・# は外します。BASE・Shopify なら --url を外して打ち直してください。",
                "command": "thth shop add <店> --kind stores --project <project> --url <店の頁の URL> --by <名前>",
                "better": "お客さんが開く店の頁（トップの頁）の URL にすると、下書きを見た人がそのまま店に着きます。"},
            "auth": {
                "fix": "--auth は Shopify の鍵だけに付けます（Dev Dashboard の Client ID と secret なら client、既にあるカスタムアプリの固定の鍵なら token）。BASE と STORES の鍵は --auth を外して打ち直してください。",
                "command": "thth shop token set <店> --auth client --by <名前>",
                "better": "Shopify は client にすると、短い鍵を THTH が取り直すので、固定の鍵を手で差し替える手間がありません。"},
            "expires": {
                "fix": "--expires は店の種類で付け方が違います。BASE は PAT を発行したときに決めた期限の日を YYYY-MM-DD で付け、STORES は知っていれば付け（任意）、Shopify の鍵には付けないで打ち直してください。",
                "command": "thth shop token set <店> --expires <日付> --by <名前>",
                "better": "期限の日を入れておくと、thth morning が 14 日前から知らせるので、鍵が切れて読めなくなる前に差し替えられます。"},
            "item": {
                "fix": "--item に商品の id を入れて打ち直してください（BASE と Shopify は数字、STORES は英数字）。id は thth shop items <店> の行の頭にあります。",
                "command": "thth shop items <店>",
                "better": "thth shop diff <店> の違いの行の id を写すと、下書きにできる違いのある商品をそのまま選べます。"},
            "item_or_kind": {
                "fix": "thth shop draft には --item <id> か --kind new|restock|price_down のどちらか 1 つだけを付けて打ち直してください。",
                "command": "thth shop draft <店> --to <口座> --kind new",
                "better": "まず --kind で種類ごとに作り（3 本まで）、残りを --item で 1 つずつ選ぶと、出しすぎずに済みます。"},
        }),
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
        "台帳は店の種類ごとに決まった欄だけ（BASE は store・kind・project・token、Shopify はそれに shop を、STORES はそれに"
        " public_url（店の頁の https の URL）を足した 5 つ）にしてください。"
        "台帳か写しが壊れたときは、thth shop remove <店> --by <名前> で"
        "外し（二段確認・鍵と写しと台帳を消します）、thth shop add から作り直します。",
        _REMOVE,
        "台帳は手で直さず thth shop add で作り直すと、欄の取り違えが起きません。"),
    "store_token_missing": _entry(
        "この店の鍵（BASE は PAT・Shopify は Dev Dashboard のアプリの client id と secret か、既にある固定の鍵・STORES は"
        "クレデンシャル）がまだ置かれていないか、鍵のファイルが読めないか、店の種類と鍵の形が合いません。",
        "BASE なら" + _PAT + "、thth shop token set <店> --expires <期限の日> --by <名前> で渡してください。" + _SHOPIFY_KEY + "。"
        + _STORES_KEY + "。",
        _TOKEN,
        "PAT の期限は発行のときに決まり、あとで変えられないので、期限の日を控えて --expires に入れると 14 日前から知らせます。"),
    "store_token_input_unsafe": _entry(
        "標準入力が端末ではないので、隠して読めません。このまま読むと鍵が画面に出るおそれがあります。",
        "パイプやファイルから渡すときは --stdin を付けてください。対話で入れるときは端末から打つか、ssh なら -t を付けてください。",
        _TOKEN + " --stdin",
        "鍵は引数では受けません。履歴に残らない標準入力か隠した入力で渡すのが安全です。"),
    "store_token_empty": _entry(
        "受け取った鍵が空か、空白や改行を含む形でした。",
        "表示された鍵（BASE の PAT・Shopify の Client ID と Client secret はそれぞれ 1 行・STORES のクレデンシャル）をそのまま貼り付けて、もう一度 thth shop token set を打ってください。",
        _TOKEN,
        "パスワードの道具から貼ると、前後の空白や改行が混ざりにくくなります。"),
    "store_token_expires_past": _entry(
        "--expires に渡した期限の日が、今日より前の日です（1 行目の（）がその日）。鍵は保存していません。",
        "BASE Developers で PAT を発行したときに決めた期限の日（今日か、それより先の日）を YYYY-MM-DD で渡して、打ち直してください。",
        _TOKEN,
        "期限の日は発行の画面に出ます。控えておくと、THTH が 14 日前から知らせるので、切れる前に差し替えられます。"),
    "store_token_expired": _entry(
        "置いてある鍵（BASE の PAT か、--expires を付けて置いた STORES のクレデンシャル）の期限を過ぎています。"
        "BASE の PAT は期限を発行のあとに延ばせません。",
        "BASE なら" + _PAT + "、新しい期限の日と一緒に thth shop token set で差し替えてください。STORES なら、"
        "STORES から新しいクレデンシャルを受け取って " + _STORES_TOKEN + " で差し替えてください。STORES の期限の日は"
        "持ち主が入れた日なので、今のクレデンシャルがまだ使えるなら、同じものを新しい日を付けて（か日を付けずに）"
        + _STORES_TOKEN + " で置き直せば通ります。",
        _TOKEN,
        "期限は長めにして控えておくと、THTH が 14 日前から thth morning で知らせるので、切れる前に差し替えられます。"),
    "store_token_rejected": _entry(
        "店が鍵を受け付けませんでした（1 行目の（）が HTTP の数）。BASE なら期限切れか取り消された PAT、Shopify なら取り消された固定の鍵か、"
        "取り直した鍵でも通らなかった（アプリが店から外された）とき、STORES（401 か 403）ならクレデンシャルが違う・取り消された・"
        "スタンダードプランでなくなった、のどれかです。鍵は保存していません（token set のとき）。",
        "BASE なら" + _PAT + "、thth shop token set で差し替えてください。" + _SHOPIFY_KEY + "。STORES なら、店の契約が"
        "スタンダードプランかを確かめ、クレデンシャルを " + _STORES_TOKEN + " で渡し直してください。",
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
        "店の応答に、要る欄が無いか、型が変わっていました（BASE の v2 は β で予告なく変わることがあります。Shopify は API の版 2026-10、"
        "STORES は版 202211 を読みます）。写しは進めていません。",
        "時間を置いて同じ命令を打ち直してください。続くなら、命令と時刻を添えて thth report file で開発のセッションへ知らせてください。",
        _ITEMS,
        "BASE Developers の変更履歴（https://apiv2.thebase.com/docs/changelog/）・Shopify の changelog（https://shopify.dev/changelog）・"
        "STORES の API の頁（https://heyinc.github.io/retail-api-docs/）を見ると、何が変わったかの手がかりになります。" + _NOT_BASE,
        by_detail={
            # Shopify の 1 回の点数の上限（5.18.1・`thth/shop/shopify.py` が添える語）。型の変わりでなく、点数の見込みの外れ。
            "max_cost_exceeded": {
                "fix": "Shopify の商品の問い合わせが 1 回の点数の上限（1000 点）を超え、1 頁を小さくして打ち直しても収まりませんでした。打ち直しても同じになるので、命令と時刻と --json の出力を添えて thth report file で開発のセッションへ知らせてください。",
                "command": _ITEMS + " --json",
                "better": "知らせが届けば、1 頁の件数と 1 商品で読む種類の数を Shopify の今の点数に合わせて直せます。それまでも thth shop whoami <店> で鍵と店の様子は確かめられます。"},
        }),
    "store_unavailable": _entry(
        "店（BASE・Shopify・STORES）に届かないか、一時的に応えませんでした（保守・5xx・混雑 429・Shopify の点数の上限 throttled・Shopify の中の誤り internal_error・凍った店 shop_frozen）。"
        "Shopify の throttled は点数が戻るまで待って 2 回まで、商品を読むときの internal_error は頁を小さくして 2 回まで打ち直しました"
        "（1 回の読みで打ち直すのは合わせて 4 回まで・混んでいると言われて待つのは合わせて 60 秒まで・頁の間に点の戻りを待つのは合わせて 180 秒まで（点の戻りが遅い店では、戻る速さに合わせて長くて 300 秒まで）。"
        "超えそうなら read_budget で打ち切ります）。ほかは再試行していません。",
        "少し時間を置いて打ち直してください。保守中なら、終わってから打ち直します。",
        _ITEMS,
        "定期に読むなら間を広めに空けると、上限に当たりにくくなります。1 行目の（）が shop_frozen なら、Shopify の管理画面で店の契約を確かめてください。",
        by_detail={
            # Shopify の点数と中の誤り（5.18.1・`thth/shop/shopify.py` が添える語）。
            "throttled": {
                "fix": "Shopify の点数（同じアプリと店の 1 秒あたりの枠）が戻るまで、1 分ほど置いて打ち直してください。同じ店を読むほかの道具が動いていれば、終わってからにします。",
                "command": _ITEMS,
                "better": "定期に読むなら、ほかのアプリが店を読む時刻と重ならない時刻にすると、待たずに読めます（枠はアプリと店の組ごとで、プランが上がると大きくなります）。"},
            "read_budget": {
                "fix": "Shopify が混んだままで（ほかのアプリが店の点数を使っていて）、1 回の読みの打ち直し（合わせて 4 回まで）か、混んでいると言われての待ち（合わせて 60 秒まで）か、頁の間に点の戻りを待つ時間（合わせて 180 秒まで・点の戻りが遅い店では長くて 300 秒まで）の上限に届いたので、長く止まらないよう打ち切りました。点が毎秒 50 点以上戻る店なら、混んでいなければ、種類の多い商品 500 件も読み切れます（毎秒 100 点の店で 2 分ほど・毎秒 50 点の店で 5 分ほど）。何も書き換えていません。数分置いて打ち直してください。同じ店を読むほかのアプリが動いていれば、終わってからにします。",
                "command": _ITEMS,
                "better": "定期に読むなら、ほかのアプリが店を読む時刻と重ならない時刻にすると、待たずに 1 回で読み切れます。承認の確定で出たときも、少し置いて確定を打ち直せば読み直します。"},
            "internal_error": {
                "fix": "Shopify の中の誤りが続きました（商品を読むときは、頁を小さくして打ち直したあとです）。少し時間を置いて打ち直し、続くなら https://www.shopifystatus.com で Shopify の様子を確かめてください。",
                "command": _ITEMS,
                "better": "種類の多い商品が多い店でこれが続くなら、命令と時刻を添えて thth report file で開発のセッションへ知らせてください。1 頁の件数を店に合わせて直せます。"},
        }),
    "store_request_rejected": _entry(
        "店がこの問い合わせを受け付けなかったか、THTH の柵（決まった送り先の決まった道に決まった中身だけ・リダイレクトは追わない）が"
        "打たずに止めました（1 行目の（）が redirect なら、店がリダイレクトを返しました）。",
        "1 行目の（）の語を添えて、thth report file で開発のセッションへ知らせてください。",
        _WHOAMI,
        "thth shop whoami <店> が通るかを先に見ると、鍵の問題か問い合わせの問題かを切り分けられます。" + _NOT_BASE),
    "store_read_incomplete": _entry(
        "商品の全部を 1 回の読みで読み切れませんでした（1 回に読むのは BASE と STORES は 100 件ずつ 5 頁、Shopify は 25 件ずつ 20 頁"
        "（点数が見込みより大きければ頁を小さくして頁の数を増やす）で、"
        "どれも 500 件まで。ちょうど 500 件の BASE と STORES の店もここに当たります）。"
        "読み切れないまま写しにすると、読めなかった商品を「消えた」と取り違えます。",
        "写しは進めていません。商品が上限より多い店は、開発のセッションへ thth report file で上限の相談をしてください。",
        _ITEMS,
        "thth shop items <店> で今読める分の一覧は見られます。違いを見るだけなら thth shop diff <店>（--accept なし）は通ります。"),
    "store_draft_nothing": _entry(
        "選んだ商品・種類に、下書きにできる違いがありません（公開で買える商品の新着・再入荷・値下げだけを下書きにします。"
        "在庫 0・販売前・販売終了・予約販売・名前が空の商品は作りません）。",
        "thth shop diff <店> で今の違いを確かめてから、--item か --kind を選び直してください。",
        _DIFF,
        "売り切れ・値上げの下書きは作りません。販売前や予約販売の商品は、販売が始まると diff に sale_started が出て、--kind new で新着と同じように作れます。新着・再入荷・販売の開始のときに作ると、見た人がそのまま買えるお知らせになります。"),
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
        "下書きを作ったあとに、その商品の値段・公開・在庫・種類が変わっていました（1 行目の（）が変わった所。variant は種類ごとの値段、variants_changed は値下げの下書きの種類が減ったこと）。今のまま出すと中身が店と食い違うので、承認しませんでした。",
        "thth shop items <店> で今の商品を確かめ、下書きの本文と頭（from_store_price・from_store_variant_prices）を今に合わせて直すか、出さないなら下書きを閉じてください。",
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
        "1 行目の（）が文字数なら、店の管理画面で商品名を短くしてください。hashtags の符丁なら、商品名の # がハッシュタグと"
        "読まれています（ハッシュタグを使わない口座）。店の管理画面で商品名から # を外すと、どの媒体の下書きにも収まります。"),
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
