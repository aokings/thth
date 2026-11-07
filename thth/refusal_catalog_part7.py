"""断りの目録の項目（第 7 部: Google 広告の口・`thth/gads/`）。thth/refusal_catalog.py が読む。

`thth gads auth|accounts|keywords|report`（読むだけ）が出す符丁（`thth/gads/wire.py` の `REASONS`）。
Google の断り（`authenticationError`・`authorizationError`・`quotaError` など）は `thth/gads/api.py` の
`refusal_for` がここの符丁に写し、Google の enum の名前を詳細として 1 行目の（）に添える。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.12.6"
_AUTH = "thth gads auth"
_ACCOUNTS = "thth gads accounts"
_KEYWORDS = "thth gads keywords --customer <id> --seed <語>"
_REPORT = "thth gads report --customer <id> --days 30"
_FILE = "続くなら、命令と時刻を添えて thth report file で開発のセッションへ知らせてください。"


def _entry(why, fix, command, better, kind="refusal"):
    return {"kind": kind, "why": why, "fix": fix, "command": command, "better": better, "doors": _C,
            "since": _SINCE}


ENTRIES = {
    # ------------------------------------------------------------------ client の JSON と認可の置き場
    "gads_client_missing": _entry(
        "Google 広告の OAuth の client の JSON が見つからないので、認可を始められません。",
        "Cloud Console の「認証情報」で種類「デスクトップ アプリ」の OAuth クライアントの JSON を落とし、"
        "~/.config/thth/google-ads-client.json に置くか THTH_GOOGLE_ADS_CLIENT にそのパスを入れてから打ち直してください。",
        _AUTH,
        "client の JSON は 600 にして repo の外に置くと、誤って commit する心配がなくなります。"),
    "gads_client_invalid": _entry(
        "client の JSON が読めないか、端末のアプリの形（installed）でないか、client_id・auth_uri・token_uri が欠けています。",
        "Cloud Console で種類「デスクトップ アプリ」の OAuth クライアントを作り直し、落とした JSON をそのまま置いて打ち直してください。",
        _AUTH,
        "ウェブ アプリの形の client は loopback の戻り先を持たないので、端末で使う client は別に 1 つ作ると混ざりません。"),
    "gads_token_missing": _entry(
        "Google 広告の認可（refresh token）がまだ残っていないので、API を呼べません。",
        "端末で thth gads auth を打ち、ブラウザで Google アカウントを選んで許可してください。",
        _AUTH,
        "許可のあと thth gads accounts を一度打つと、結べる口座の id がそろいます。"),
    "gads_token_invalid": _entry(
        "認可のファイルが読めないか、refresh token の欄がありません（中身は表示しません）。",
        "thth gads auth をやり直すと、同じ置き場に新しい認可を書き直します。",
        _AUTH,
        "認可のファイルは手で直さず thth gads auth で書き直すと、600 のまま保てます。"),
    "gads_token_revoked": _entry(
        "Google が refresh token を受け付けませんでした（取り消された・期限が切れた・パスワードを変えた など）。",
        "thth gads auth をやり直して、新しい認可を残してください。",
        _AUTH,
        "OAuth の同意の画面が「テスト中」のままだと 7 日で切れるので、Cloud Console で「本番」にすると長く使えます。"),
    "gads_token_refresh_failed": _entry(
        "Google の token の窓口から access token を受け取れませんでした（何も変えていません）。",
        "少し置いて同じ命令を打ち直し、続くなら thth gads auth をやり直してください。" + _FILE,
        _ACCOUNTS,
        "読むだけの thth gads accounts で先に通るかを確かめると、どこで止まるかを切り分けられます。"),
    # ------------------------------------------------------------------ 認可の流れ（thth gads auth）
    "gads_auth_denied": _entry(
        "ブラウザの許可の画面で許可されなかったか、戻りに認可の code がありませんでした。",
        "thth gads auth をもう一度打ち、Google 広告の口座を持つアカウントを選んで「許可」を押してください。",
        _AUTH,
        "「確認されていないアプリ」の画面は、自分の project の client なら「詳細」から進めると許可まで行けます。"),
    "gads_auth_timeout": _entry(
        "決めた時間のうちにブラウザからの戻りが届きませんでした（何も残していません）。",
        "thth gads auth を打ち直し、出た URL を同じ機械のブラウザで開いて許可してください（--timeout で待つ秒を延ばせます）。",
        "thth gads auth --timeout 600",
        "ブラウザが開かない端末では --no-browser を付けて URL を手で貼ると、待つ間に迷いません。"),
    "gads_auth_state_mismatch": _entry(
        "ブラウザからの戻りの state が、この端末で出した値と合いません（別の許可の戻りか、書き換えられた戻りです）。",
        "開いている許可の頁を閉じ、thth gads auth を打ち直して、新しく出た URL から許可してください。",
        _AUTH,
        "許可は 1 回の thth gads auth につき 1 つの頁で進めると、戻りが混ざりません。"),
    "gads_auth_exchange_failed": _entry(
        "Google が認可の code を token に換えるのを断りました（code の使い回し・時間切れ・client の違い など）。",
        "thth gads auth を打ち直して、もう一度許可してください。" + _FILE,
        _AUTH,
        "許可の画面は開いてすぐ進めると、code の時間切れに当たりません。"),
    "gads_no_refresh_token": _entry(
        "Google の答えに refresh token が入っていなかったので、あとで呼ぶための認可を残せませんでした。",
        "https://myaccount.google.com/permissions でこのアプリの許可を外してから、thth gads auth を打ち直してください。",
        _AUTH,
        "1 つの client で許可を重ねすぎない（1 アカウント 100 本まで）と、古い認可が勝手に消えません。"),
    # ------------------------------------------------------------------ 引数
    "gads_customer_invalid": _entry(
        "広告の口座の id は 10 桁の数字（226-006-0755 か 2260060755 の形）で受け取ります。",
        "thth gads accounts で出る id を --customer にそのまま入れて打ち直してください。",
        _ACCOUNTS,
        "よく使う口座の id は台帳やメモに 1 か所だけ書いておくと、写し間違えが減ります。"),
    "gads_customer_unknown": _entry(
        "その id の広告の口座が Google 広告に見つかりませんでした。",
        "thth gads accounts で見える口座の id を確かめ、--customer を直して打ち直してください。",
        _ACCOUNTS,
        "管理者の口座（MCC）の下の口座は、その口座に直接招待してもらうと id で呼べます。"),
    "gads_customer_not_enabled": _entry(
        "その広告の口座はまだ有効になっていないか、閉じられています。",
        "Google 広告の画面でその口座の設定（支払いの情報・初めのキャンペーン）を終えてから打ち直してください。",
        _ACCOUNTS,
        "待つ間は、有効な別の口座の id で thth gads keywords を使うと、語の案を先に集められます。"),
    "gads_seed_empty": _entry(
        "キーワードの案の種になる語が空です。",
        "--seed に 1 つ以上の語を入れて打ち直してください。",
        _KEYWORDS,
        "売りたいものの名前と、それを探す人が打ちそうな悩みの語を 1 つずつ入れると、案の幅が広がります。"),
    "gads_seed_too_many": _entry(
        "Google が 1 回に受け付ける種の語は 20 までです。",
        "--seed の語を 20 以下に絞って打ち直してください（多いときは分けて 2 回打ちます）。",
        _KEYWORDS,
        "近い語は 1 つにまとめ、--url に紹介の頁を足すと、少ない種でも案が増えます。"),
    "gads_url_invalid": _entry(
        "--url は http か https の頁の URL で受け取ります。",
        "--url に https:// から始まる頁の URL を入れて打ち直すか、--url を外してください。",
        _KEYWORDS,
        "売りたいものを説明している頁を渡すと、その頁の言葉から案が出ます。"),
    "gads_lang_unknown": _entry(
        "--lang に THTH の表に無い言語が入っています（今は ja と en）。",
        "--lang を ja か en にして打ち直してください。",
        _KEYWORDS + " --lang ja",
        "ほかの言語が要るときは thth report file で頼むと、公式の表の id を確かめて表に足します。"),
    "gads_geo_unknown": _entry(
        "--geo に THTH の表に無い地域が入っています（今は JP と US）。",
        "--geo を JP か US にして打ち直してください。",
        _KEYWORDS + " --geo JP",
        "ほかの地域が要るときは thth report file で頼むと、公式の表の id を確かめて表に足します。"),
    "gads_date_invalid": _entry(
        "期間は --days N（1〜3650）か、--since YYYY-MM-DD（と --until YYYY-MM-DD）のどちらか 1 つで、終わりは始めより後にします。",
        "--days 30 か --since 2026-10-01 --until 2026-10-31 の形で打ち直してください。",
        _REPORT,
        "前の期間と並べたいときは、同じ日数で 2 回打つと比べやすくなります。"),
    # ------------------------------------------------------------------ Google の断り
    "gads_authentication_error": _entry(
        "Google が認可を受け付けませんでした（token の期限切れ・取り消し・2 段階認証が要る など）。",
        "thth gads auth をやり直してから打ち直してください（Google のアカウントに 2 段階認証が要るときは先に設定します）。",
        _AUTH,
        "Google 広告の口座を持つアカウントで許可すると、口座の権限の取り違えが起きません。"),
    "gads_authorization_error": _entry(
        "その Google アカウントには、その広告の口座を読む権限がありません（招待されていない・権限が足りない など）。",
        "thth gads accounts で見える口座を使うか、口座の管理者にこの Google アカウントを招待してもらってから打ち直してください。",
        _ACCOUNTS,
        "口座の権限は「閲覧」以上で読めるので、読むだけなら小さい権限で招待してもらうと安心です。"),
    "gads_project_not_approved": _entry(
        "THTH の Cloud の project が、その口座を呼べる段（Explorer 以上）にまだ上がっていません。",
        "Cloud Console の「Google Ads API」の頁で段を確かめ、上がるのを待ってから打ち直してください（段は project の持ち主が申請します）。",
        _ACCOUNTS,
        "待つ間はテストの口座で命令の形を確かめておくと、上がった日にすぐ本番の口座を読めます。"),
    "gads_quota_exceeded": _entry(
        "Google 広告の API の量の上限に当たりました（project 全体の直近 24 時間の数・短い間の呼びすぎ）。",
        "少し置いてから打ち直してください（日の上限なら翌日に戻ります）。",
        _REPORT,
        "同じ期間の成績は --json で手元に残しておくと、呼ぶ回数を減らせます。"),
    "gads_request_rejected": _entry(
        "Google が依頼の形を受け付けませんでした（詳細の（）に Google の理由の名前があります）。",
        "引数を見直して打ち直してください。" + _FILE,
        _ACCOUNTS,
        "（）の Google の理由の名前を添えて知らせると、開発の側がすぐ直せます。"),
    "gads_unavailable": _entry(
        "Google 広告の API に届かなかったか、Google の側がいま答えを返せません（何も変えていません）。",
        "網のつながりを確かめ、少し置いてから打ち直してください。" + _FILE,
        _ACCOUNTS,
        "読むだけの命令なので、時間を置いて打ち直しても重ねて何かが起きる心配はありません。"),
    "gads_response_invalid": _entry(
        "Google の答えが、THTH の読める形（JSON の値）ではありませんでした。",
        "少し置いてから打ち直してください。" + _FILE,
        _ACCOUNTS,
        "API の版が上がった時期に起きやすいので、thth の版を新しくすると直ることがあります。"),
}
