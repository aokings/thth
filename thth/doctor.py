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
from . import redact as redact_mod
from .adapters import threads as threads_mod

TIMEOUT_SECONDS = 20.0

# 返ってきた中で表示してよい鍵だけを通す（本文や個人情報を垂れ流さない）。
_KEEP = ("name", "id", "username", "total_value", "quota_usage", "config",
         "reply_quota_usage", "reply_config", "values", "timestamp", "permalink")


class _Probe:
    def __init__(self, label: str, permission: str, path: str, params: dict):
        self.label = label
        self.permission = permission
        self.path = path
        self.params = params


def _get(base_url: str, path: str, params: dict, token: str) -> dict:
    p = dict(params)
    p["access_token"] = token
    url = base_url.rstrip("/") + path + "?" + urllib.parse.urlencode(p)
    with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read() or b"{}")


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
        return {"label": probe.label, "permission": probe.permission,
                "ok": True, "detail": _summarize(body), "body": body}
    except urllib.error.HTTPError as e:
        message = ""
        try:
            message = (json.loads(e.read() or b"{}").get("error", {})
                       .get("message", ""))[:170]
        except Exception:
            pass
        return {"label": probe.label, "permission": probe.permission, "ok": False,
                "detail": redact_mod.redact(f"HTTP {e.code} {message}".strip()), "body": None}
    except Exception as e:  # ネットワーク層。例外文にトークンが混じらないよう型名だけ。
        return {"label": probe.label, "permission": probe.permission, "ok": False,
                "detail": type(e).__name__, "body": None}


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
        _Probe("自分の投稿一覧", "threads_basic", f"/v1.0/{user_id}/threads",
               {"fields": "id,permalink,timestamp", "limit": 3}),
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
    for r in results:
        if r["label"] == "自分の投稿一覧" and r["ok"] and r.get("body"):
            rows = r["body"].get("data") or []
            if rows:
                first_post_id = rows[0].get("id")
            break
    if first_post_id:
        results.append(_run_probe(base_url, _Probe(
            "返信の取得", "threads_read_replies", f"/v1.0/{first_post_id}/replies",
            {"fields": "id,username,timestamp", "limit": 3}), access_token))
    else:
        results.append({"label": "返信の取得", "permission": "threads_read_replies",
                        "ok": None, "detail": "投稿がまだ無いので試せない", "body": None})

    for r in results:
        r.pop("body", None)
    return {"account": account_name, "handle": account_cfg.get("handle"),
            "username": token.get("username"), "user_id": user_id, "probes": results}


def run_doctor(account_name: str, *, as_json: bool = False, log=print) -> int:
    report = diagnose(account_name)
    if as_json:
        log(json.dumps(report, ensure_ascii=False))
        return 0 if report.get("probes") and all(
            p["ok"] is not False for p in report["probes"]) else 1

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
    return 1 if failed else 0
