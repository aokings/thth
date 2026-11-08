"""断りの目録の項目（第 8 部: Search Console の口・`thth/gsc/`）。thth/refusal_catalog.py が読む。

`thth gsc auth|sites|query|compare|inspect`（読むだけ）が出す符丁（`thth/gsc/wire.py` の `REASONS`）。
Google の断り（HTTP の数・`error.status`・`error.details[].reason`・`error.errors[].reason`）は `thth/gsc/api.py` の
`refusal_for` がここの符丁に写し、Google の理由の名前を詳細として 1 行目の（）に添える。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.12.6"
_AUTH = "thth gsc auth"
_SITES = "thth gsc sites"
_QUERY = "thth gsc query --site <プロパティ> --days 28"
_INSPECT = "thth gsc inspect --site <プロパティ> --url <頁>"
_FILE = "続くなら、命令と時刻を添えて thth report file で開発のセッションへ知らせてください。"


def _entry(why, fix, command, better, kind="refusal"):
    return {"kind": kind, "why": why, "fix": fix, "command": command, "better": better, "doors": _C,
            "since": _SINCE}


ENTRIES = {
    # ------------------------------------------------------------------ client の JSON と認可の置き場
    "gsc_client_missing": _entry(
        "Google の OAuth の client の JSON（thth gads と同じもの）が見つからないので、認可を始められません。",
        "Cloud Console の「認証情報」で種類「デスクトップ アプリ」の OAuth クライアントの JSON を落とし、"
        "~/.config/thth/google-ads-client.json に置くか THTH_GOOGLE_ADS_CLIENT にそのパスを入れてから打ち直してください。",
        _AUTH,
        "同じ Cloud の project で「Google Search Console API」を有効にしておくと、認可のあとすぐ読めます。"),
    "gsc_client_invalid": _entry(
        "client の JSON が読めないか、端末のアプリの形（installed）でないか、client_id・auth_uri・token_uri が欠けています。",
        "Cloud Console で種類「デスクトップ アプリ」の OAuth クライアントを作り直し、落とした JSON をそのまま置いて打ち直してください。",
        _AUTH,
        "thth gads と同じ client を使うので、1 つ直せば両方の口が通ります。"),
    "gsc_token_missing": _entry(
        "Search Console の認可（refresh token）がまだ残っていないので、API を呼べません（Google 広告の認可とは別です）。",
        "端末で thth gsc auth を打ち、ブラウザで Search Console のプロパティを持つ Google アカウントを選んで許可してください。",
        _AUTH,
        "許可のあと thth gsc sites を一度打つと、--site に入れるプロパティの書き方がそろいます。"),
    "gsc_token_invalid": _entry(
        "Search Console の認可のファイルが読めないか、refresh token の欄がありません（中身は表示しません）。",
        "thth gsc auth をやり直すと、同じ置き場に新しい認可を書き直します。",
        _AUTH,
        "認可のファイルは手で直さず thth gsc auth で書き直すと、600 のまま保てます。"),
    "gsc_token_revoked": _entry(
        "Google が Search Console の refresh token を受け付けませんでした（取り消された・期限が切れた・パスワードを変えた など）。",
        "thth gsc auth をやり直して、新しい認可を残してください。",
        _AUTH,
        "OAuth の同意の画面が「テスト中」のままだと 7 日で切れるので、Cloud Console で「本番」にすると長く使えます。"),
    "gsc_token_refresh_failed": _entry(
        "Google の token の窓口から access token を受け取れませんでした（何も変えていません）。",
        "少し置いて同じ命令を打ち直し、続くなら thth gsc auth をやり直してください。" + _FILE,
        _SITES,
        "読むだけの thth gsc sites で先に通るかを確かめると、どこで止まるかを切り分けられます。"),
    # ------------------------------------------------------------------ 認可の流れ（thth gsc auth）
    "gsc_auth_denied": _entry(
        "ブラウザの許可の画面で許可されなかったか、戻りに認可の code がありませんでした。",
        "thth gsc auth をもう一度打ち、Search Console のプロパティを持つアカウントを選んで「許可」を押してください。",
        _AUTH,
        "「確認されていないアプリ」の画面は、自分の project の client なら「詳細」から進めると許可まで行けます。"),
    "gsc_auth_timeout": _entry(
        "決めた時間のうちにブラウザからの戻りが届きませんでした（何も残していません）。",
        "thth gsc auth を打ち直し、出た URL を同じ機械のブラウザで開いて許可してください（--timeout で待つ秒を延ばせます）。",
        "thth gsc auth --timeout 600",
        "ブラウザが開かない端末では --no-browser を付けて URL を手で貼ると、待つ間に迷いません。"),
    "gsc_auth_state_mismatch": _entry(
        "ブラウザからの戻りの state が、この端末で出した値と合いません（別の許可の戻りか、書き換えられた戻りです）。",
        "開いている許可の頁を閉じ、thth gsc auth を打ち直して、新しく出た URL から許可してください。",
        _AUTH,
        "許可は 1 回の thth gsc auth につき 1 つの頁で進めると、戻りが混ざりません。"),
    "gsc_auth_exchange_failed": _entry(
        "Google が認可の code を token に換えるのを断りました（code の使い回し・時間切れ・client の違い など）。",
        "thth gsc auth を打ち直して、もう一度許可してください。" + _FILE,
        _AUTH,
        "許可の画面は開いてすぐ進めると、code の時間切れに当たりません。"),
    "gsc_no_refresh_token": _entry(
        "Google の答えに refresh token が入っていなかったので、あとで呼ぶための認可を残せませんでした。",
        "https://myaccount.google.com/permissions でこのアプリの許可を外してから、thth gsc auth を打ち直してください。",
        _AUTH,
        "1 つの client で許可を重ねすぎない（1 アカウント 100 本まで）と、古い認可が勝手に消えません。"),
    # ------------------------------------------------------------------ 引数
    "gsc_site_invalid": _entry(
        "--site は sc-domain:example.com（ドメインのプロパティ）か https://example.com/（URL の頭・終わりは /）の形で受け取ります。",
        "thth gsc sites で出るプロパティの書き方を、そのまま --site に入れて打ち直してください。",
        _SITES,
        "ドメインのプロパティ（sc-domain:）を使うと、http・https・サブドメインをまとめて読めます。"),
    "gsc_site_unknown": _entry(
        "そのプロパティが Search Console に見つかりませんでした。",
        "thth gsc sites で見えるプロパティを確かめ、--site を直して打ち直してください。",
        _SITES,
        "Search Console の画面でサイトの所有権を確認すると、そのプロパティを読めるようになります。"),
    "gsc_permission_denied": _entry(
        "この Google アカウントには、そのプロパティを読む権限がありません（確認していない・ユーザーに足されていない など）。",
        "thth gsc sites で見えるプロパティを使うか、プロパティの所有者にこの Google アカウントをユーザーとして足してもらってから打ち直してください。",
        _SITES,
        "読むだけなら「制限付き」のユーザーで足りるので、小さい権限で足してもらうと安心です。"),
    "gsc_date_invalid": _entry(
        "期間は --days N（query は 1〜480・compare は 1〜240）か、--since YYYY-MM-DD（と --until YYYY-MM-DD）のどちらか 1 つで、終わりは始めより後にします。",
        "--days 28 か --since 2026-10-01 --until 2026-10-31 の形で打ち直してください。",
        _QUERY,
        "前の期間と並べたいときは thth gsc compare を使うと、同じ日数の 2 つの期間の差が 1 回で出ます。"),
    "gsc_dim_invalid": _entry(
        "--dim に使えない軸があるか、同じ軸を 2 度入れています（query は query・page・country・device・date、compare は query か page）。",
        "--dim を query・page・country・device・date から重ねずに選んで打ち直してください。",
        _QUERY + " --dim query page",
        "query と page を並べると、どの語でどの頁が出ているかが 1 つの表で見られます。"),
    "gsc_page_prefix_invalid": _entry(
        "--page-prefix は http か https の頁の URL（空白なし・2048 字まで）で受け取ります。",
        "--page-prefix に https:// から始まる URL を入れて打ち直すか、--page-prefix を外してください。",
        _QUERY + " --page-prefix https://example.com/blog/",
        "記事の一覧の頭（/blog/ など）を入れると、その種類の頁だけの成績を読めます。"),
    "gsc_query_contains_invalid": _entry(
        "--query-contains は空でない語（改行なし・4096 字まで）で受け取ります。",
        "--query-contains に含めたい語を 1 つ入れて打ち直すか、--query-contains を外してください。",
        _QUERY + " --query-contains <語>",
        "商品の名前や悩みの語を入れると、その語で探した人の成績だけを並べられます。"),
    "gsc_url_invalid": _entry(
        "--url は http か https の頁の URL（空白なし・2048 字まで）で受け取ります。",
        "--url に https:// から始まる頁の URL を入れて打ち直してください。",
        _INSPECT,
        "thth gsc query --dim page で出る頁の URL をそのまま使うと、写し間違えが起きません。"),
    "gsc_inspect_url_outside": _entry(
        "検査する URL が、--site のプロパティの中にありません（URL の検査はプロパティの中の頁だけ）。",
        "--site をその頁を含むプロパティにするか、--url をプロパティの中の頁にして打ち直してください。",
        _INSPECT,
        "ドメインのプロパティ（sc-domain:）を --site にすると、サブドメインの頁もまとめて検査できます。"),
    # ------------------------------------------------------------------ Google の断り
    "gsc_authentication_error": _entry(
        "Google が認可を受け付けませんでした（token の期限切れ・取り消し・scope が足りない など）。",
        "thth gsc auth をやり直してから打ち直してください。",
        _AUTH,
        "Search Console のプロパティを持つアカウントで許可すると、権限の取り違えが起きません。"),
    "gsc_api_not_enabled": _entry(
        "THTH の client の Cloud の project で、Google Search Console API がまだ有効になっていません。",
        "Cloud Console の「API とサービス」で「Google Search Console API」を有効にし、数分置いてから打ち直してください。",
        _SITES,
        "有効にしたあと thth gsc sites を一度打つと、読めるプロパティまで一度に確かめられます。"),
    "gsc_quota_exceeded": _entry(
        "Search Console の API の量の上限に当たりました（短い間の呼びすぎ・URL の検査は 1 プロパティ 1 日 2,000 回）。",
        "少し置いてから打ち直してください（日の上限なら翌日に戻ります）。",
        _QUERY,
        "同じ期間の成績は --json で手元に残しておくと、呼ぶ回数を減らせます。"),
    "gsc_request_rejected": _entry(
        "Google が依頼の形を受け付けませんでした（詳細の（）に Google の理由の名前があります）。",
        "引数を見直して打ち直してください。" + _FILE,
        _SITES,
        "（）の Google の理由の名前を添えて知らせると、開発の側がすぐ直せます。"),
    "gsc_unavailable": _entry(
        "Search Console の API に届かなかったか、Google の側がいま答えを返せません（何も変えていません）。",
        "網のつながりを確かめ、少し置いてから打ち直してください。" + _FILE,
        _SITES,
        "読むだけの命令なので、時間を置いて打ち直しても重ねて何かが起きる心配はありません。"),
    "gsc_response_invalid": _entry(
        "Google の答えが、THTH の読める形（JSON の値）ではありませんでした。",
        "少し置いてから打ち直してください。" + _FILE,
        _SITES,
        "API の形が変わった時期に起きやすいので、thth の版を新しくすると直ることがあります。"),
}
