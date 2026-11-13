"""断りの目録の項目（実店舗のレジ: Square・スマレジ・`thth/shop/square.py`・`thth/shop/smaregi.py`・計画 実店舗 段 5）。
thth/refusal_catalog.py が読む。

符丁は `thth/shop/wire.py` の `REASONS` の `pos_*`。HTTP の数から符丁への写しは `thth/shop/pos.py` の `refusal_for`。
文は前向きな断り（`docs/原則_前向きな断り.md`）: なぜ・通る直し方・より良くする一手。**まだ確かめていない所は、そう言う**
（スマレジの鍵の取り方・本物の応答・`thth/shop/pos.py` の `UNVERIFIED`）。
`since` は今の版（`thth/VERSION` を越えない柵）。版上げのときに直す。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.18.1"
_DOCTOR = "thth shop doctor <店>"
_TOKEN = "thth shop token set <店> --by <名前>"
_SQUARE_SCOPES = "ITEMS_READ・INVENTORY_READ・MERCHANT_PROFILE_READ"
_SMAREGI_SCOPES = "pos.products:read・pos.stores:read・pos.stock:read"
_NOT_BY = ("この道具は Square・スマレジが提供するものではないので、レジの会社の窓口でなく THTH の開発のセッションへ "
           "thth report file で知らせると早く届きます。")


def _entry(why, fix, command, better, kind="refusal", by_detail=None):
    entry = {"kind": kind, "why": why, "fix": fix, "command": command, "better": better, "doors": _C,
             "since": _SINCE}
    if by_detail:
        entry["by_detail"] = by_detail
    return entry


ENTRIES = {
    "pos_args_invalid": _entry(
        "実店舗のレジの欄に渡した値が決まりに合っていません（1 行目の（）が指す欄）。",
        "1 行目の（）の欄を直して打ち直してください。",
        "thth shop add <店> --kind square --project <project> --url <店の頁の URL> --location <店舗の id> --by <名前>",
        "店の名前は <project>-square・<project>-smaregi のように project とレジをつなげると、どの店か名前だけで分かります。",
        by_detail={
            "location": {
                "fix": "--location は Square とスマレジの店だけに付けます。Square なら Location の id（英大文字と数字）、"
                       "スマレジなら店舗 ID（数字 9 桁まで）を入れて打ち直してください。BASE・Shopify・STORES なら外します。",
                "command": "thth shop add <店> --kind square --project <project> --url <店の頁の URL> --location <店舗の id> --by <名前>",
                "better": "店舗の id が分からなければ、まず思い当たる値で台帳を作って thth shop token set を打つと、"
                          "Square はこの鍵で見える店舗の id と名前を並べて断るので、そこから写せます。"},
            "contract": {
                "fix": "--contract はスマレジの店だけに付けます。スマレジの契約 ID（英数字と _）を入れて打ち直してください。"
                       "Square・BASE・Shopify・STORES なら外します。",
                "command": "thth shop add <店> --kind smaregi --project <project> --url <店の頁の URL> --contract <契約 ID> --location <店舗 ID> --by <名前>",
                "better": "契約 ID はスマレジの管理画面とマイページに出ます。写し間違えると鍵を取る所で断られるので、画面から写すと確かです。"},
            "candidates": {
                "fix": "--candidates は実店舗のレジ（Square・スマレジ）の店だけで使えます。ネットショップでは --candidates を外して"
                       "打ち直してください。",
                "command": "thth shop items <店>",
                "better": "ネットショップは公開の商品がそのまま材料になるので、選ぶ一覧は要りません。"},
            "publish_kind": {
                "fix": "thth shop publish は実店舗のレジ（Square・スマレジ）の店だけで使えます。ネットショップは店の管理画面の"
                       "公開と非公開で決まります。",
                "command": "thth shop items <店>",
                "better": "ネットショップで出したくない商品は、店の管理画面で非公開にすると THTH の下書きの材料からも外れます。"},
            "item_or_category": {
                "fix": "thth shop publish には --item <id> か --category <id> のどちらか 1 つだけを付けて打ち直してください。",
                "command": "thth shop publish <店> --item <id> --by <名前>",
                "better": "部門ごと（--category）にすると、その部門に新しく足した品も材料になります。品ごと（--item）なら、"
                          "出す品を 1 つずつ確かめられます。"},
            "publish_full": {
                "fix": "出してよい品か部門の表が上限（それぞれ 200）に達しています。品を 1 つずつ足す代わりに部門で足すか、"
                       "--off で要らない品を外してから足してください。",
                "command": "thth shop publish <店> --category <id> --by <名前>",
                "better": "店頭で出したい品をレジの部門にまとめておくと、部門 1 つで足りて、表も短く保てます。"},
        }),
    "pos_token_rejected": _entry(
        "レジが鍵を受け付けませんでした（1 行目の（）が HTTP の数・401 は鍵が違うか取り消された）。Square なら"
        "アクセストークンが違う・作り直された・OAuth の鍵の期限（30 日）が過ぎた、スマレジなら client id か secret が違う・"
        "作り直された・プレミアムプランでなくなった、のどれかです。スマレジでは、マイページで発行した認証情報がこの取り方"
        "（id.smaregi.jp への client credentials）で通るかを、まだ本物で確かめていません。鍵は保存していません（token set のとき）。",
        "Square なら Developer Console の本番（Production）のアクセストークンを、スマレジならマイページのプラットフォーム API "
        "設定の本番の client id と secret を、" + _TOKEN + " で渡し直してください（サンドボックスの値では通りません）。",
        _TOKEN,
        "渡し直したら thth shop doctor <店> で店舗の名前が出るかを確かめると、次の読みで止まりません。スマレジで正しい値でも"
        "通らないなら、この取り方がマイページの認証情報に合っていない見込みが高いので、thth report file で開発のセッションへ"
        "知らせてください。" + _NOT_BY),
    "pos_scope_missing": _entry(
        "鍵に、この読みに要る権限がありません（1 行目の（）が HTTP 403 か scope）。Square は品に ITEMS_READ、在庫に "
        "INVENTORY_READ、店舗に MERCHANT_PROFILE_READ が要ります（個人のアクセストークンは全部の権限を持つので、ここに当たるのは"
        "OAuth の鍵のとき）。スマレジは鍵を取るときに " + _SMAREGI_SCOPES + " を頼みますが、マイページのスコープの設定で"
        "オンになっていない物は付きません。",
        "Square の OAuth の鍵なら " + _SQUARE_SCOPES + " の 3 つで作り直し、スマレジならマイページのスコープの設定で "
        + _SMAREGI_SCOPES + " をオンにしてから、" + _TOKEN + " で渡し直してください。",
        _TOKEN,
        "売上・取引・会員・顧客・支払いの権限は付けないでください。THTH は読むだけで、権限が少ないほど鍵が漏れたときの害が"
        "小さくなります。"),
    "pos_location_unknown": _entry(
        "台帳の --location の店舗が、この鍵で見える店舗の中にありません（Square なら Location の id、スマレジなら店舗 ID）。"
        "別の店舗の品や在庫を読まないように、読みませんでした。",
        "Square なら、断りの下に並んだ「この鍵で見える店舗」（--json なら locations）から読む店舗の id を選び、"
        "thth shop remove <店> --by <名前> で外して正しい --location で thth shop add し直してください。スマレジなら"
        "管理画面の店舗の一覧の店舗 ID を写して同じようにします。",
        "thth shop add <店> --kind square --project <project> --url <店の頁の URL> --location <店舗の id> --by <名前>",
        "店舗が 2 つ以上あるなら、店舗ごとに台帳を分けると（<project>-square-honten など）、在庫と営業の時間を店舗ごとに"
        "取り違えずに読めます。"),
    "pos_api_changed": _entry(
        "レジの応答に、要る欄が無いか、形が変わっていました（1 行目の（）が欄の手がかり）。Square は API の版 "
        "2026-09-16 を決めて読みます。THTH のレジの口は、本物の店ではまだ 1 度も動かしておらず、応答の形は公式の頁の欄の"
        "名前から作ったので、最初の本物の読みでここに当たることがあります。写しは進めていません。",
        "時間を置いて同じ命令を打ち直してください。続くなら、命令と時刻と 1 行目の（）を添えて thth report file で開発の"
        "セッションへ知らせてください。",
        _DOCTOR,
        "知らせが届けば、本物の応答の形に合わせて読み方を直せます。Square の変更履歴（https://developer.squareup.com/docs/changelog/connect）"
        "と、スマレジの API の頁（https://developers.smaregi.dev/platform-api-reference/）が手がかりになります。" + _NOT_BY),
    "pos_unavailable": _entry(
        "レジ（Square・スマレジ）に届かないか、一時的に応えませんでした（1 行目の（）が HTTP の数・429 は混雑・5xx は保守か"
        "レジの側の誤り）。THTH は打ち直していません。",
        "数分おいて同じ命令を打ち直してください。",
        _DOCTOR,
        "混雑（429）が続くなら、出してよい品を部門でまとめて在庫の数を読む品を減らすと、1 回の読みで打つ数が減ります。"),
    "pos_currency_unsupported": _entry(
        "レジの値段が円（JPY）ではありません（1 行目の（）が通貨）。下書きの値段を取り違えないように、読みませんでした。",
        "日本の店舗（円の店舗）を --location に選び直してください。円でない店舗を読むなら、thth report file で開発の"
        "セッションへ相談してください。",
        "thth shop items <店>",
        "店舗の通貨は Square の店舗の設定で決まります。日本の店舗なら、そのまま円で読めます。"),
    "pos_publish_empty": _entry(
        "出してよい品がまだ 1 つもありません。レジの品は店の中の事情の品（その他・袋・取り置きなど）も混ざるので、"
        "THTH は既定でどの品も下書きの材料にしません。",
        "thth shop items <店> --candidates で品と部門の一覧を見て、出してよい品を thth shop publish <店> --item <id> "
        "--by <名前>（部門ごとなら --category <id>）で足してから、thth shop diff <店> で基準を取り、もう一度 thth shop draft "
        "を打ってください。",
        "thth shop items <店> --candidates",
        "店頭で推したい品の部門を 1 つ作って、その部門を --category で足しておくと、新しく入れた品もそのまま下書きの材料に"
        "なります。"),
    "pos_read_limit": _entry(
        "出してよい品のうち在庫を数える種類が多く、1 回の読みで在庫の数を読む上限（50）を超えました（在庫をまとめて読む道は"
        "POST なので THTH は使わず、種類ごとに 1 回ずつ GET で読みます）。読み切れないまま写しにすると在庫の節目を取り違える"
        "ので、写しは進めていません。",
        "thth shop publish <店> --item <id> --off で出す品を絞るか、在庫を数えない品にしてから打ち直してください。上限を"
        "上げたいときは thth report file で開発のセッションへ相談してください。",
        "thth shop publish <店> --item <id> --off --by <名前>",
        "お知らせにしたい品に絞って表に入れると、読みも速く、下書きも選びやすくなります。"),
}
