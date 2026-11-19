"""断りの目録の項目（Google ビジネス プロフィールの口・`thth/gbp/`）。thth/refusal_catalog.py が読む。

`thth gbp auth|doctor|locations|show|special|posts|post` が出す符丁（`thth/gbp/wire.py` の `REASONS`）。
Google の断り（HTTP の数・`error.status`・`error.details[].reason`・上限の値）は `thth/gbp/api.py` の `refusal_for` が
ここの符丁に写し、Google の理由の名前を詳細として 1 行目の（）に添える。

`since` は今の版（`thth/VERSION` を越えない柵）。版上げのとき、次の小版（5.27.0）に直す。
"""
from __future__ import annotations

_C = ("cli",)
_SINCE = "5.26.0"
_AUTH = "thth gbp auth"
_DOCTOR = "thth gbp doctor"
_LOCATIONS = "thth gbp locations"
_SHOW = "thth gbp show <店>"
_FILE = "続くなら、命令と時刻を添えて thth report file で開発のセッションへ知らせてください。"


def _entry(why, fix, command, better, kind="refusal"):
    return {"kind": kind, "why": why, "fix": fix, "command": command, "better": better, "doors": _C,
            "since": _SINCE}


ENTRIES = {
    # ------------------------------------------------------------------ client の JSON と認可の置き場
    "gbp_client_missing": _entry(
        "Google の OAuth の client の JSON（thth gads と同じもの）が見つからないので、認可を始められません。",
        "Cloud Console の「認証情報」で種類「デスクトップ アプリ」の OAuth クライアントの JSON を落とし、"
        "~/.config/thth/google-ads-client.json に置くか THTH_GOOGLE_ADS_CLIENT にそのパスを入れてから打ち直してください。",
        _AUTH,
        "ビジネス プロフィールの API の許可リストに載った Cloud の project の client を使うと、認可のあとすぐ読めます。"),
    "gbp_client_invalid": _entry(
        "client の JSON が読めないか、端末のアプリの形（installed）でないか、client_id・auth_uri・token_uri が欠けています。",
        "Cloud Console で種類「デスクトップ アプリ」の OAuth クライアントを作り直し、落とした JSON をそのまま置いて打ち直してください。",
        _AUTH,
        "thth gads・thth gsc と同じ client を使うので、1 つ直せば 3 つの口が通ります。"),
    "gbp_token_missing": _entry(
        "ビジネス プロフィールの認可（refresh token）がまだ残っていないので、API を呼べません（広告・Search Console の認可とは別です）。",
        "端末で thth gbp auth を打ち、ブラウザで店のオーナーか管理者の Google アカウントを選んで許可してください。",
        _AUTH,
        "許可のあと thth gbp doctor を一度打つと、アカウントが読めるところまで一度に確かめられます。"),
    "gbp_token_invalid": _entry(
        "ビジネス プロフィールの認可のファイルが読めないか、refresh token の欄がありません（中身は表示しません）。",
        "thth gbp auth をやり直すと、同じ置き場に新しい認可を書き直します。",
        _AUTH,
        "認可のファイルは手で直さず thth gbp auth で書き直すと、600 のまま保てます。"),
    "gbp_token_revoked": _entry(
        "Google がビジネス プロフィールの refresh token を受け付けませんでした（取り消された・期限が切れた・パスワードを変えた など）。",
        "thth gbp auth をやり直して、新しい認可を残してください。",
        _AUTH,
        "OAuth の同意の画面が「テスト中」のままだと 7 日で切れるので、Cloud Console で「本番」にすると長く使えます。"),
    "gbp_token_refresh_failed": _entry(
        "Google の token の窓口から access token を受け取れませんでした（何も変えていません）。",
        "少し置いて同じ命令を打ち直し、続くなら thth gbp auth をやり直してください。" + _FILE,
        _DOCTOR,
        "thth gbp doctor で先に通るかを確かめると、どこで止まるかを切り分けられます。"),
    "gbp_scope_missing": _entry(
        "残っている認可に、ビジネス プロフィールの scope（business.manage）が入っていません（許可の画面で外されたなど）。",
        "thth gbp auth をやり直し、許可の画面で「ビジネス プロフィールの管理」の項目に印を付けたまま許可してください。",
        _AUTH,
        "許可のあと thth gbp doctor の scope の行が「済」になれば、店を読む命令まで通ります。"),
    # ------------------------------------------------------------------ 認可の流れ（thth gbp auth）
    "gbp_auth_denied": _entry(
        "ブラウザの許可の画面で許可されなかったか、戻りに認可の code がありませんでした。",
        "thth gbp auth をもう一度打ち、店のオーナーか管理者のアカウントを選んで「許可」を押してください。",
        _AUTH,
        "店を持つアカウントで許可すると、thth gbp locations にその店がそのまま出ます。"),
    "gbp_auth_timeout": _entry(
        "決めた時間のうちに、ブラウザからの戻りが来ませんでした（認可は残していません）。",
        "thth gbp auth を打ち直し、出た頁で許可を押してください。時間が足りなければ --timeout を大きくして打ってください。",
        _AUTH,
        "ブラウザが開かない端末では --no-browser で URL を出し、同じ端末のブラウザに貼ると速く済みます。"),
    "gbp_auth_state_mismatch": _entry(
        "戻りの state が、この命令が出した値と違いました（別の認可の戻りか、書き換えられた戻り）。何も残していません。",
        "thth gbp auth を打ち直し、その命令が出した URL で許可してください。",
        _AUTH,
        "古いタブの許可の画面を閉じてから打つと、取り違えが起きません。"),
    "gbp_auth_exchange_failed": _entry(
        "Google が認可の code を token に換えるのを受け付けませんでした（詳細の（）に Google の理由の名前があります）。",
        "thth gbp auth を打ち直してください。続くなら client の JSON が正しい project のものかを確かめてください。",
        _AUTH,
        "client の JSON を落とし直して置き換えると、取り違えの多くが片づきます。"),
    "gbp_no_refresh_token": _entry(
        "Google の答えに refresh token がありませんでした（端末のアプリの形の client でないとき などに起きます）。",
        "Cloud Console で種類「デスクトップ アプリ」の OAuth クライアントを使い、thth gbp auth を打ち直してください。",
        _AUTH,
        "Google アカウントの「サードパーティのアクセス」から THTH の古い許可を外してから打つと、新しい token が返ります。"),
    # ------------------------------------------------------------------ 引数の形
    "gbp_location_invalid": _entry(
        "店の書き方が違います（locations/ に数字の id か、accounts/…/locations/… の形）。",
        "thth gbp locations で出る locations/… をそのまま写して打ち直してください。",
        _LOCATIONS,
        "thth gbp locations --json の location の欄を使うと、写し間違えが起きません。"),
    "gbp_date_invalid": _entry(
        "--date の日付が YYYY-MM-DD の形でないか、ありえない日付です。",
        "--date 2026-12-31 のように、年・月・日を - でつないで打ち直してください。",
        None,
        "祝日や年末年始は、まとめて先に 1 日ずつ入れておくと、当日に慌てずに済みます。"),
    "gbp_date_past": _entry(
        "--date が今日より前の日です（臨時の時間は今日か先の日に書きます・日本の日付で数えます）。",
        "今日か先の日付を --date に入れて打ち直してください。",
        None,
        "済んだ日の臨時の時間は Google が自然に表示から外すので、先の日だけを足していけば足ります。"),
    "gbp_time_invalid": _entry(
        "--open・--close の時刻が HH:MM の形でないか、00:00〜24:00 の外か、開く時刻と閉める時刻が同じです。",
        "--open 10:00 --close 15:00 のように打ち直してください（閉める時刻が開く時刻より前なら、翌日の時刻として扱います）。",
        None,
        "日をまたぐ営業は --close に翌日の時刻（例 02:00）を入れると、そのまま 1 行で書けます。"),
    "gbp_hours_args_invalid": _entry(
        "--closed と --open・--close を一緒に渡したか、どちらも渡していないか、--open と --close の片方だけです。",
        "終日休みなら --closed だけ、時間を変えるなら --open と --close の 2 つを付けて打ち直してください。",
        None,
        "一段目の表示で送る中身が出るので、確かめてから --confirm を付けると取り違えが起きません。"),
    "gbp_post_text_invalid": _entry(
        "投稿の本文が空か、1,500 字を超えるか、改行とタブのほかの制御文字が入っています（詳細の（）に字の数があります）。",
        "本文を 1〜1,500 字にして --text に入れて打ち直してください。",
        None,
        "最初の 2 行に要点を置くと、Google の画面で畳まれても伝わります。"),
    "gbp_post_url_invalid": _entry(
        "--url が http か https の頁の URL でないか、--url なしで --action を渡しました。",
        "--url に https:// から始まる店の頁の URL を入れて打ち直してください（ボタンが要らなければ --url と --action を外します）。",
        None,
        "その店の頁（予約や問い合わせの頁）を行き先にすると、押した人が迷いません。"),
    "gbp_event_invalid": _entry(
        "イベントの投稿の値が決まりに合っていません（--event・--start・--end の 3 つがそろっていない、題が空か 58 字を超える、"
        "時刻が YYYY-MM-DD HH:MM でない、終わりが始まりより前のどれか。詳細の（）に語があります）。",
        "--event に題、--start と --end に \"2026-12-14 13:00\" の形の時刻を入れ、終わりを始まりより後にして打ち直してください。",
        None,
        "題に催しの名前、本文に場所と参加の仕方を書くと、Google マップで見た人がそのまま来られます。"),
    # ------------------------------------------------------------------ 店の状態・二段確認
    "gbp_regular_hours_missing": _entry(
        "この店にはふだんの営業の時間が入っていないので、Google は臨時の時間を受け付けません（何も変えていません）。",
        "ビジネス プロフィールの画面で営業の時間を入れてから、同じ命令を打ち直してください。",
        _SHOW,
        "営業の時間を入れたあと thth gbp show で出るのを確かめると、臨時の時間もそのまま通ります。"),
    "gbp_digest_mismatch": _entry(
        "--confirm の digest が、いまの中身と一致しません（一段目のあとに中身か Google の側の臨時の時間が変わりました）。"
        "何も送っていません。",
        "--confirm を外して一段目からやり直し、出た digest で打ち直してください。",
        None,
        "一段目と二段目を続けて打つと、その間に店の情報が変わる見込みが小さくなります。"),
    "gbp_write_unknown": _entry(
        "書く依頼を送ったあと、Google の答えが届かなかったか、Google の側の故障の答えでした。変わったかどうかは分かりません。",
        "打ち直す前に thth gbp show（投稿なら thth gbp posts）で今の様子を確かめ、変わっていなければ一段目からやり直してください。",
        _SHOW,
        "確かめてから打ち直すと、同じ投稿が 2 つ並ぶことを避けられます。"),
    # ------------------------------------------------------------------ Google の断り
    "gbp_location_unknown": _entry(
        "その店が見つからないか、この Google アカウントの見える店の中にありません。",
        "thth gbp locations で、このアカウントが持つか任された店の id を確かめて打ち直してください。",
        _LOCATIONS,
        "店のオーナーに、このアカウントを管理者として足してもらうと、ここに出るようになります。"),
    "gbp_permission_denied": _entry(
        "Google が拒みました（このアカウントがその店のオーナーか管理者でない・project がまだ API の許可を受けていない など）。",
        "thth gbp locations にその店が出るアカウントで thth gbp auth をやり直してください。出ていれば、Cloud の project の"
        "許可（毎分の上限が 300）を確かめてください。",
        _LOCATIONS,
        "thth gbp doctor で、どの段で止まるかを先に切り分けられます。"),
    "gbp_not_allowlisted": _entry(
        "Cloud の project の毎分の上限が 0 です（ビジネス プロフィールの API の利用の申請がまだ通っていないか、別の project の client です）。",
        "許可を受けた project の client の JSON を置くか、Google の申請の書式で「Application for Basic API Access」を出し、"
        "上限が 300 になってから打ち直してください。",
        _DOCTOR,
        "Cloud Console の「割り当て」で上限が 300 になったのを見てから thth gbp doctor を打つと、一度で通ります。"),
    "gbp_api_not_enabled": _entry(
        "THTH の client の Cloud の project で、ビジネス プロフィールの API のどれかがまだ有効になっていません（詳細の（）に名前があります）。",
        "Cloud Console の「API とサービス」で（）の API を有効にし、数分置いてから打ち直してください。",
        _DOCTOR,
        "Account Management・Business Information・My Business（v4）・Performance の 4 つをまとめて有効にすると、全部の命令が通ります。"),
    "gbp_authentication_error": _entry(
        "Google が認可を受け付けませんでした（token の期限切れ・取り消し・scope が足りない など）。",
        "thth gbp auth をやり直してから打ち直してください。",
        _AUTH,
        "店のオーナーか管理者のアカウントで許可すると、権限の取り違えが起きません。"),
    "gbp_quota_exceeded": _entry(
        "ビジネス プロフィールの API の量の上限に当たりました（project ごとに毎分 300 回・店ごとの書き換えは毎分 10 回）。",
        "1 分ほど置いてから打ち直してください。",
        _SHOW,
        "臨時の時間は 1 日ずつ送るより、先の日の分を間を置いて入れると上限に当たりにくくなります。"),
    "gbp_request_rejected": _entry(
        "Google が依頼の形を受け付けませんでした（詳細の（）に Google の理由の名前があります）。何も変えていません。",
        "引数を見直して打ち直してください。" + _FILE,
        _SHOW,
        "（）の Google の理由の名前を添えて知らせると、開発の側がすぐ直せます。"),
    "gbp_unavailable": _entry(
        "ビジネス プロフィールの API に届かなかったか、Google の側がいま答えを返せません（読む命令なので何も変えていません）。",
        "網のつながりを確かめ、少し置いてから打ち直してください。" + _FILE,
        _DOCTOR,
        "thth gbp doctor で、届くかどうかだけを先に確かめられます。"),
    "gbp_response_invalid": _entry(
        "Google の答えが、THTH の読める形（JSON の値）ではありませんでした。",
        "少し置いてから打ち直してください。" + _FILE,
        _DOCTOR,
        "API の形が変わった時期に起きやすいので、thth の版を新しくすると直ることがあります。"),
}
