"""`thth doctor <account>`: そのトークンで実際に何ができるかを読み取りだけで測る。

なぜ要るか（2026-09-09）: Meta の管理画面で権限を 11 個「アプリレビューに追加」しても、
tester の認可画面に降りてくるのは 5 つだけだった（設計 §2.2 の訂正 3）。**画面の表示と
トークンの実力が一致しない**ので、実際に叩いて確かめる道具を持つ。アカウントを 1 本
足すたびに走らせる。

守ること:
  - **読み取りだけ。投稿・返信・削除は絶対に呼ばない**（doctor が副作用を持つと、
    「様子を見るつもりが出てしまった」が起きうる）。
  - **トークンの値を出力に出さない。** エラー文も `redact()` を通す。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from . import account_cli as account_cli_mod
from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import appenv as appenv_mod
from . import httpsafe
from . import redact as redact_mod
from . import topics as topics_mod
from .adapters import base as adapter_base
from .adapters import threads as threads_mod

TIMEOUT_SECONDS = 20.0

# 導入手順のどこが足りないかを、節番号で指す（設計 §2 の C1・節番号は
# Track C2 の導入文書と合わせて決め打ち: §2 Meta アプリ・§3 app.env・
# §4 アカウント台帳・§5 トークン・§6 timer）。
NEXT_STEP_APP_ENV = "次の一手: 導入文書 §3 app.env を見てください。"
NEXT_STEP_ACCOUNT = "次の一手: 導入文書 §4 アカウント台帳 を見てください。"
NEXT_STEP_TOKEN = "次の一手: 導入文書 §5 トークン を見てください。"

# **雛形のダミーが残っている台帳を「異常なし」で返さない**（監査 2・C10・
# masaru 裁定 2026-09-13「手がかかっても最善を」）。`thth account add` が写す
# 雛形には、そのままでは通らない値が入っている（`redirect_uri` は
# `https://example.invalid/`・Mastodon の `instance` は `https://mastodon.example`・
# handle は `demo`）。**`add` と `thth auth` の間に、どこにも書かれていない手作業が
# 挟まっていた**——しかも診断の道具が黙っていた。どの欄がダミーかは
# `accounts.dummy_fields()` の 1 か所が知っている（`thth auth` も同じ知識で断る）。
DUMMY_LABEL = "**ダミーのままです**"

# **app.env が「無い」のは、直すべき欠落ではない**（masaru 裁定 2026-09-13）。
# app.env（`THREADS_APP_ID`・`THREADS_APP_SECRET`）を使うのは `thth auth` だけで、
# 管理画面で発行したトークンを `thth token set` で入れる運用——いまの本番 4 本が
# そう——では VM に置かなくても正しく動く（延長 `refresh_access_token` は app
# secret を使わない・`thth/oauth.py`）。それなのに doctor は毎回「次の一手: 導入
# 文書 §3」と言っていた。**正しい状態を毎回「足りない」と言う道具は、本当に
# 足りないときに読まれなくなる。** 無いときは「任意」と言うだけにして、§3 は
# **置いたのに使えないとき**（項目が空・読めない）だけに残す。
APP_ENV_ABSENT_NOTICE = (
    "app.env: 無し（任意。`thth auth` を使うときだけ要ります。"
    "管理画面で発行したトークンを `thth token set` で入れる運用なら不要。"
    "置くなら `thth app set`）")
# トークンが無い／使えないときだけ、`thth auth` へ進む道も一応示す（そちらを
# 選ぶなら app.env が先に要る）。**app.env が無いときにしか出さない。**
NEXT_STEP_AUTH_NEEDS_APP_ENV = "`thth auth` を使うなら先に `thth app set`（導入文書 §3）。"

# **probe の中身は媒体側へ移した**（設計 v2 §4.2・T-B5）。どの口を叩けばどの
# 権限が確かめられるかは媒体の知識で、doctor の知識ではない。doctor に残るのは
# 「**読み取りしか呼ばない取得口**」（下の `_get`）と、結果の並べ方だけ。
# 表示してよい鍵の一覧・`_Probe`・結果の要約は `thth/adapters/threads.py` にある。
_Probe = threads_mod._Probe
_KEEP = threads_mod.PROBE_KEEP
_ApiBodyError = threads_mod.ProbeBodyError


def _get(base_url: str, path: str, params: dict, token: str) -> dict:
    p = dict(params)
    p["access_token"] = token
    url = base_url.rstrip("/") + path + "?" + urllib.parse.urlencode(p)
    with httpsafe.urlopen(url, timeout=TIMEOUT_SECONDS) as resp:
        body = json.loads(resp.read() or b"{}")
    # **doctor 自前の `_get()` には、この直しがまだ入っていなかった**
    # （外部レビュー再々判定 N7・2026-09-12）。`thth/adapters/threads.py` の
    # `_get()` は HTTP 200 でも body に `error` があれば失敗として上げるように
    # 直してあったが、doctor は別の `_get()` を持っていて、そちらは素通しの
    # ままだった。全 GET が HTTP 200 の `error` を返す fixture で、5 probe が
    # ○になり返信 probe が「未投稿のため検査できない」になった（事実と違う）。
    if isinstance(body, dict) and body.get("error"):
        raise _ApiBodyError(
            f"API が error を返しました（HTTP 200）: "
            f"{redact_mod.redact(str(body['error']))[:200]}")
    return body


def _auth_needs_app_env(account_name: str) -> bool:
    """その媒体の `thth auth` が Meta の app.env を要るか（既定は「要らない」）。

    台帳が読めない・媒体を知らないときは False——**判らないときに、要るとは
    言わない**（要らない一手を勧めるほうが、黙っているより手を止める）。
    """
    try:
        cfg = accounts_mod.load_account(account_name)
        return bool(adapters_mod.adapter_class(cfg.get("media")).AUTH_NEEDS_APP_ENV)
    except Exception:   # noqa: BLE001 — 診断の付け足しで診断を止めない
        return False


def diagnose(account_name: str) -> dict:
    """読み取りだけで能力を測る。トークンの値は返り値にも入れない。

    **probe は媒体が持つ**（設計 v2 §4.2・T-B5）。doctor は台帳の `media` から
    アダプタを作り、`probe()` に自分の取得口（`_get`——読み取りしか呼ばないこと
    を source の検査で担保している）を渡して、結果を並べるだけ。
    **Threads の出力は現行と同じ。**
    """
    account_cfg = accounts_mod.load_account(account_name)
    token = accounts_mod.load_token(account_cfg)

    # **媒体を先に引く。** トークンの「在る／無い」の判定が媒体ごとに違うので
    # （下）、知らない媒体をここで loud に断らないと、`media` の誤字が
    # 「トークンが無い」という**別の理由**に化けて出る（T-B0）。
    try:
        adapter_cls = adapters_mod.adapter_class(account_cfg.get("media"))
    except adapter_base.AdapterError as e:
        # 「probe が 0 件で異常なし」にしない——`run_doctor()` が `error` を見て rc=2。
        return {"account": account_name, "error": str(e), "probes": []}

    # **トークンの鍵は媒体が知っている**（`TOKEN_KEYS`・T3 の配線 2026-09-13）。
    # 以前はここが `token.get("access_token")` 固定だったので、**Bluesky は
    # `thth auth` で正しく認可しても「トークンが無い」と言われた**（`.token` に
    # 入るのは `identifier` と `app_password`）。次の一手も媒体ごとに違う
    # （`TOKEN_SETUP_HINT`）——Bluesky に `thth token set` を勧めても入らない。
    if not adapter_cls.has_token(token):
        return {"account": account_name,
                "error": f"トークンが無い（{adapter_cls.TOKEN_SETUP_HINT} を先に）",
                "probes": []}

    user_id = token.get("user_id") or account_cfg.get("user_id") or ""
    try:
        adapter = adapters_mod.make_adapter(account_cfg, token)
    except adapter_base.AdapterError as e:
        return {"account": account_name, "error": str(e), "probes": []}

    results = adapter.probe(get=_get)

    for r in results:
        r.pop("body", None)
    return {"account": account_name, "handle": account_cfg.get("handle"),
            "username": token.get("username"), "user_id": user_id, "probes": results}


def run_doctor(account_name: str, *, as_json: bool = False, log=print) -> int:
    """導入手順のどこが足りないかを、実際にトークンを叩く前に机上で見る（設計
    §2 の C1）。順番は app.env → accounts/<account>.json → token。**既存の検査
    （トークンを実際に叩く診断）は壊さない・その手前に足す。** 足りないものごとに
    「次の一手」（導入文書の節番号）を 1 行言う。値は一切出力しない——app.env は
    存在と項目の有無だけを見て、中身は読み捨てる。
    """
    notices: list[str] = []

    # **台帳の置き場を 1 行で言う**（設計 v2 §3・v2-2a）。台帳が「外」なのか
    # 「repo の中（互換）」なのかで、`thth account add` した 1 本が見えたり
    # 見えなかったりする。**どこを読んだかを言わない診断は、台帳が見つからない
    # ときに役に立たない。** notices（＝足りないもの）とは別枠で常に出す。
    どこ = account_cli_mod.where_line()
    accounts_dir_info = accounts_mod.accounts_dir_info()

    # `--json` のときは stdout を JSON 1 個だけにする（設計 §6）ので、
    # パーミッション直しの警告（`secrets_fs.ensure_mode_600`）を **stdout へ
    # 素通しできない。** そこで以前は `--json` のとき捨てていたが、それだと
    # **道具がファイルを 600 に書き換えた事実が、機械の読み手からは完全に
    # 消える**（独立監査 1・P3-11）。**捨てずに `notices` へ回す。**
    # 値そのものはどちらにしても出さない（出るのは path と「直した」だけ）。
    直した: list[str] = []
    def env_log(msg):
        直した.append(str(msg))
    # `absent` / `ok` / `broken`。**無い（任意）と、置いたのに使えない（要修理）を
    # 分ける**（masaru 裁定 2026-09-13・上の `APP_ENV_ABSENT_NOTICE` の理由）。
    app_env_state, app_env_detail = appenv_mod.probe(log=env_log)
    # **app.env が要るのはその媒体の `thth auth` だけ**（`AUTH_NEEDS_APP_ENV`・
    # 独立監査 1・P2-3・2026-09-13）。以前はここが媒体を見ずに出していたので、
    # Bluesky の診断に「`thth auth` を使うときだけ app.env が要ります」と出た
    # ——Bluesky の `thth auth` は App Password を対話で受けるだけで app.env を
    # 読まない。**要らない準備を勧める道具は、そこで人の手を止める**（NEXT_STEP_
    # AUTH_NEEDS_APP_ENV を媒体で絞ったのと同じ理由・同じ判定を使う）。
    # 「置いたのに壊れている」は媒体に関わらず言う（実在するファイルの異常）。
    if app_env_state == appenv_mod.ABSENT and _auth_needs_app_env(account_name):
        notices.append(APP_ENV_ABSENT_NOTICE)
    if app_env_state == appenv_mod.BROKEN:
        notices.append(f"app.env: {app_env_detail}")
        notices.append(NEXT_STEP_APP_ENV)
    if 直した:
        notices.append("app.env のパーミッションを 600 に直しました"
                        f"（{appenv_mod.default_path()}）")

    # **トピックの台帳が壊れていたら、それも診断で言う**（独立監査 1・P1-1）。
    # 台帳は `--advise` と承認の一段目が毎回読む。**壊れていることに気づける
    # 場所が「壊れたあと」しか無かった。** 診断の道具が黙っているのが穴。
    topics_shelf = None
    try:
        topics_mod.load()
    except topics_mod.ShelfBroken as e:
        topics_shelf = {"error": "topics_shelf_broken", "path": e.path,
                        "detail": e.detail}
        notices.append(str(e))

    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        notices.append(f"アカウント台帳: {e}")
        notices.append(NEXT_STEP_ACCOUNT)
        if as_json:
            payload = {"account": account_name, "error": str(e), "probes": [],
                       "notices": notices, "app_env": app_env_state,
                       "accounts_dir": accounts_dir_info, "dummy_fields": []}
            if topics_shelf:
                payload["topics_shelf_broken"] = topics_shelf
            log(json.dumps(payload, ensure_ascii=False))
        else:
            # **台帳が読めなかったときこそ、どこを読んだかを言う。**
            log(どこ)
            for n in notices:
                log(n)
        return 2

    # **台帳は読めた。その中身が雛形のダミーのままなら、名指しで言う**（C10）。
    # rc の意味づけは変えない——ここは「台帳が読めない」（2）でも「トークンが
    # 無い」（2）でもなく、**診断はできたが×がある**ので、probe が落ちたときと
    # 同じ 1。台帳の値は人が直すもので、道具は測るだけ（設計 §4.2）。
    dummies = accounts_mod.dummy_fields(account_cfg)
    for d in dummies:
        notices.append(f"台帳の `{d['field']}` が{DUMMY_LABEL}: {d['value']}")
        notices.append(f"次の一手: {d['next']}")

    report = diagnose(account_name)
    if report.get("error"):
        notices.append(NEXT_STEP_TOKEN)
        if app_env_state == appenv_mod.ABSENT and _auth_needs_app_env(account_name):
            # トークンを入れる道は 2 つある。`thth token set`（app.env 不要）と
            # `thth auth`（app.env が要る）。後者を選ぶ人が §3 へ戻れる 1 行。
            # **要らない媒体には勧めない**（T3・2026-09-13）——Bluesky の
            # `thth auth` は App Password を対話で受けるだけで app.env を読まない。
            # 診断の道具が「要らない準備」を指図すると、そこで手が止まる。
            notices.append(NEXT_STEP_AUTH_NEEDS_APP_ENV)

    if as_json:
        report["notices"] = notices
        report["app_env"] = app_env_state
        report["accounts_dir"] = accounts_dir_info
        report["dummy_fields"] = dummies
        if topics_shelf:
            report["topics_shelf_broken"] = topics_shelf
        log(json.dumps(report, ensure_ascii=False))
        if topics_shelf:
            # **壊れた台帳を「異常なし」で返さない**（独立監査 1・P1-1）。
            return 2
        if dummies:
            # 同じ筋（C10）——**ダミーの残った台帳を 0 で返さない。**
            return 1
        return 0 if report.get("probes") and all(
            p["ok"] is not False for p in report["probes"]) else 1

    log(どこ)
    for n in notices:
        log(n)
    log("")

    if report.get("error"):
        log(report["error"])
        return 2
    log(f"{report['account']}（{report['username']}・user_id={report['user_id']}）")
    log("")
    failed = 0
    for p in report["probes"]:
        mark = "○" if p["ok"] else ("－" if p["ok"] is None else "×")
        if p["ok"] is False:
            failed += 1
        log(f"  {mark} {p['label']}（{p['permission']}）")
        log(f"      {p['detail']}")
    log("")
    log("読み取りだけを試しました。投稿・返信・削除は呼んでいません。")
    # **診断は最後まで出す。ただし壊れた台帳を「異常なし」で返さない**
    # （独立監査 1・P1-1）。probe が全部○でも rc=2。
    if topics_shelf:
        return 2
    return 1 if (failed or dummies) else 0
