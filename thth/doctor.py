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
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import accounts as accounts_mod
from . import appenv as appenv_mod
from . import redact as redact_mod
from . import topics as topics_mod
from .adapters import threads as threads_mod

TIMEOUT_SECONDS = 20.0

# 導入手順のどこが足りないかを、節番号で指す（設計 §2 の C1・節番号は
# Track C2 の導入文書と合わせて決め打ち: §2 Meta アプリ・§3 app.env・
# §4 アカウント台帳・§5 トークン・§6 timer）。
NEXT_STEP_APP_ENV = "次の一手: 導入文書 §3 app.env を見てください。"
NEXT_STEP_ACCOUNT = "次の一手: 導入文書 §4 アカウント台帳 を見てください。"
NEXT_STEP_TOKEN = "次の一手: 導入文書 §5 トークン を見てください。"

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

# 返ってきた中で表示してよい鍵だけを通す（本文や個人情報を垂れ流さない）。
_KEEP = ("name", "id", "username", "total_value", "quota_usage", "config",
         "reply_quota_usage", "reply_config", "values", "timestamp", "permalink")


class _Probe:
    # **`key` はラベルの表示文言とは独立**（監査 2026-09-11・掃討で検出。
    # 運用セッションが VM の `thth doctor` 出力で再現を確認）。以前は判定側が `label == "自分の投稿一覧"` と文字列で
    # 突き合わせていたが、ラベルに注記を足した（2026-09-10）ときに直し忘れ、
    # `==` が永久に偽になって「返信の取得」probe が一度も HTTP を叩かなくなった。
    # 表示用のラベルは今後も変わりうるので、突き合わせには変わらない `key` を使う。
    def __init__(self, label: str, permission: str, path: str, params: dict,
                 key: str | None = None):
        self.label = label
        self.permission = permission
        self.path = path
        self.params = params
        self.key = key


class _ApiBodyError(RuntimeError):
    """HTTP 200 だが本文に `error` が入っている（`thth/adapters/threads.py` の
    `_get()` と同じ考え方——200 でも「取れた」ことにしない）。"""


def _get(base_url: str, path: str, params: dict, token: str) -> dict:
    p = dict(params)
    p["access_token"] = token
    url = base_url.rstrip("/") + path + "?" + urllib.parse.urlencode(p)
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as resp:
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


def _summarize(body: dict) -> str:
    rows = body.get("data")
    if isinstance(rows, list):
        trimmed = [{k: v for k, v in row.items() if k in _KEEP} for row in rows[:3]]
        return f"{len(rows)} 件 " + json.dumps(trimmed, ensure_ascii=False)[:220]
    kept = {k: v for k, v in body.items() if k in _KEEP}
    return json.dumps(kept or body, ensure_ascii=False)[:220]


def _run_probe(base_url: str, probe: _Probe, token: str) -> dict:
    try:
        body = _get(base_url, probe.path, probe.params, token)
        return {"label": probe.label, "permission": probe.permission, "key": probe.key,
                "ok": True, "detail": _summarize(body), "body": body}
    except urllib.error.HTTPError as e:
        message = ""
        try:
            message = (json.loads(e.read() or b"{}").get("error", {})
                       .get("message", ""))[:170]
        except Exception:
            pass
        return {"label": probe.label, "permission": probe.permission, "key": probe.key,
                "ok": False,
                "detail": redact_mod.redact(f"HTTP {e.code} {message}".strip()), "body": None}
    except _ApiBodyError as e:
        # **HTTP は 200 だが本文が error**。メッセージは `_get()` で既に
        # redact 済みなので、そのまま出してよい。
        return {"label": probe.label, "permission": probe.permission, "key": probe.key,
                "ok": False, "detail": str(e)[:220], "body": None}
    except Exception as e:  # ネットワーク層。例外文にトークンが混じらないよう型名だけ。
        return {"label": probe.label, "permission": probe.permission, "key": probe.key,
                "ok": False, "detail": type(e).__name__, "body": None}


def diagnose(account_name: str) -> dict:
    """読み取りだけで能力を測る。トークンの値は返り値にも入れない。"""
    account_cfg = accounts_mod.load_account(account_name)
    token = accounts_mod.load_token(account_cfg)
    if not token or not token.get("access_token"):
        return {"account": account_name, "error": "トークンが無い（thth token set を先に）",
                "probes": []}

    access_token = token["access_token"]
    user_id = token.get("user_id") or account_cfg.get("user_id") or ""
    base_url = os.environ.get("THTH_THREADS_BASE_URL", threads_mod.DEFAULT_BASE_URL)

    now = int(time.time())
    since = now - 7 * 86400

    probes = [
        _Probe("本人の確認", "threads_basic", "/v1.0/me", {"fields": "id,username"}),
        # **「直近 3 件まで」と名前に書く**（kopicha セッション指摘 2026-09-10）。
        # 「3 件」と返ったのを投稿総数だと読まれ、masaru に「3 本しか投稿して
        # いない」と報告しかけた、という報告を受けた。実際は 4 件あった。
        # 全部を読む口は `thth posts`（切り詰めない）。
        _Probe("自分の投稿一覧（直近 3 件まで・総数ではありません→ thth posts）",
               "threads_basic", f"/v1.0/{user_id}/threads",
               {"fields": "id,permalink,timestamp", "limit": 3}, key="my_posts"),
        _Probe("投稿の残量", "threads_content_publish",
               f"/v1.0/{user_id}/threads_publishing_limit",
               {"fields": "quota_usage,config,reply_quota_usage,reply_config"}),
        _Probe("数（views・likes・followers）", "threads_manage_insights",
               f"/v1.0/{user_id}/threads_insights",
               {"metric": "views,likes,followers_count", "since": since, "until": now}),
        _Probe("数（リンクのクリック）", "threads_manage_insights",
               f"/v1.0/{user_id}/threads_insights",
               {"metric": "clicks", "since": since, "until": now}),
    ]

    results = [_run_probe(base_url, p, access_token) for p in probes]

    # 返信の取得は投稿が 1 本要る。上で拾えた最初の投稿で試す（無ければ飛ばす）。
    first_post_id = None
    my_posts_ok = None
    for r in results:
        if r["key"] == "my_posts":
            my_posts_ok = r["ok"]
            if r["ok"] and r.get("body"):
                rows = r["body"].get("data") or []
                if rows:
                    first_post_id = rows[0].get("id")
            break
    if first_post_id:
        results.append(_run_probe(base_url, _Probe(
            # **採取が実際に叩く口を probe する**（2026-09-12）。`/replies` を
            # probe していたが、採取は `/conversation`（全階層）を叩く。**違う口の
            # 疎通を確かめて「返信の取得は通る」と言っていた。**
            # **件数の意味を書く**（外部レビュー C・2026-09-12）。**選んだ 1 投稿の
            # 先頭ページ・要求上限 3 件**であって、全投稿でも全返信数でもない。
            # `limit` は**要求値**で、総数でも完全性の証明でもない。
            "返信の取得（疎通確認・**選んだ 1 投稿の先頭ページ・要求上限 3 件**。"
            "**全返信数ではありません**）",
            "threads_read_replies",
            f"/v1.0/{first_post_id}/conversation",
            {"fields": "id,username,timestamp", "limit": 3}), access_token))
    elif my_posts_ok is False:
        # **「投稿がまだ無いので試せない」は、投稿一覧が実際に取れたときだけ
        # 言ってよい**（外部レビュー再々判定 N7・2026-09-12）。投稿一覧の
        # 取得そのものが失敗していたら、理由はそちらであって「未投稿」ではない
        # ——事実と違う表示を作らない。`ok: False` にして失敗数にも数える。
        results.append({"label": "返信の取得", "key": "replies",
                        "permission": "threads_read_replies",
                        "ok": False,
                        "detail": "投稿一覧の取得に失敗したため試せていません（上の「自分の投稿一覧」参照）",
                        "body": None})
    else:
        results.append({"label": "返信の取得", "key": "replies",
                        "permission": "threads_read_replies",
                        "ok": None, "detail": "投稿がまだ無いので試せない", "body": None})

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
    if app_env_state == appenv_mod.ABSENT:
        notices.append(APP_ENV_ABSENT_NOTICE)
    elif app_env_state == appenv_mod.BROKEN:
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
        accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        notices.append(f"アカウント台帳: {e}")
        notices.append(NEXT_STEP_ACCOUNT)
        if as_json:
            payload = {"account": account_name, "error": str(e), "probes": [],
                       "notices": notices, "app_env": app_env_state}
            if topics_shelf:
                payload["topics_shelf_broken"] = topics_shelf
            log(json.dumps(payload, ensure_ascii=False))
        else:
            for n in notices:
                log(n)
        return 2

    report = diagnose(account_name)
    if report.get("error"):
        notices.append(NEXT_STEP_TOKEN)
        if app_env_state == appenv_mod.ABSENT:
            # トークンを入れる道は 2 つある。`thth token set`（app.env 不要）と
            # `thth auth`（app.env が要る）。後者を選ぶ人が §3 へ戻れる 1 行。
            notices.append(NEXT_STEP_AUTH_NEEDS_APP_ENV)

    if as_json:
        report["notices"] = notices
        report["app_env"] = app_env_state
        if topics_shelf:
            report["topics_shelf_broken"] = topics_shelf
        log(json.dumps(report, ensure_ascii=False))
        if topics_shelf:
            # **壊れた台帳を「異常なし」で返さない**（独立監査 1・P1-1）。
            return 2
        return 0 if report.get("probes") and all(
            p["ok"] is not False for p in report["probes"]) else 1

    for n in notices:
        log(n)
    if notices:
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
    return 1 if failed else 0
