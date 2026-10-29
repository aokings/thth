"""Meta の量の制限のヘッダを読み、口座の state に残す（設計 MetaAPI 計画 B-1・段 2）。

読むヘッダ（**L2**: developers.facebook.com/docs/graph-api/overview/rate-limiting）:

- `X-App-Usage` — アプリの使用率（`call_count`・`total_cputime`・`total_time`・％）。
- `X-Business-Use-Case-Usage` — 事業の物ごとの使用率。`{"<id>": [{"type", "call_count",
  "total_cputime", "total_time", "estimated_time_to_regain_access"（分）}]}`。
- `x-fb-ads-insights-throttle` — 広告の成績の読みの使用率（段 4 から毎回見る）。

置き場: `state/<口座>/meta_usage.json`（0600・置き場 0700）。中身:

```
{"version": 1, "observed_at": "...", "headers": {"app_usage": …, "business_use_case_usage": …,
 "ads_insights_throttle": …}, "wait": {"until": "...", "minutes": 19, "code": 80001, "since": "..."}}
```

**待ち（`wait`）**: 量の制限の code（`errors.RATE_LIMIT_CODES`）で断られたら、
`estimated_time_to_regain_access` の分（無い・0 なら `DEFAULT_WAIT_MINUTES`）だけ、その口座の
Meta への呼び出しをこちらで止める（`graph.Client.request` が網に出る前に断る・timer は
`core.throw_once` で出さずに次の回へ）。**今の FB は再試行しない**ので、待つのは timer の側。
「待たない」印の呼び出し（`urgent`・広告を止める操作）はこの待ちを見ない。

**読めない置き場は「待っていない」に倒す**（安全装置の止めとは違い、これは Meta の断りの写し。
読めなくても Meta がもう一度断るだけで、出てはいけないものが出る筋ではない）。
"""
from __future__ import annotations

import datetime
import json
import os

from .. import accounts, jst, secrets_fs

FILE = "meta_usage.json"
VERSION = 1
# 量の制限で断られたが、戻るまでの分が書かれていないときに待つ分（**THTH の線**。アプリの
# 量は 1 時間の窓で数える・L2）。
DEFAULT_WAIT_MINUTES = 60
# 待つ分の上限（壊れた値で口座を何日も黙らせない）。
MAX_WAIT_MINUTES = 24 * 60

HEADERS = (
    ("x-app-usage", "app_usage"),
    ("x-business-use-case-usage", "business_use_case_usage"),
    ("x-fb-ads-insights-throttle", "ads_insights_throttle"),
)
# ヘッダの値の上限（壊れた応答で state を太らせない）。
_MAX_HEADER = 16384
_MAX_TEXT = 64


def _clean(value, depth=0):
    """ヘッダの JSON を、数・短い文字列・それの dict／list だけに絞る（ほかは落とす）。"""
    if depth > 4:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value[:_MAX_TEXT]
    if isinstance(value, list):
        return [v for v in (_clean(v, depth + 1) for v in value[:50]) if v is not None]
    if isinstance(value, dict):
        out = {}
        for key, v in list(value.items())[:50]:
            if isinstance(key, str):
                cleaned = _clean(v, depth + 1)
                if cleaned is not None:
                    out[key[:_MAX_TEXT]] = cleaned
        return out
    return None


def parse(headers) -> dict:
    """応答のヘッダから 3 つを読む。無い・JSON でないヘッダは入れない（空の dict もありうる）。"""
    out = {}
    if headers is None:
        return out
    for name, key in HEADERS:
        try:
            raw = headers.get(name)
        except Exception:   # noqa: BLE001 — ヘッダの形が違えば読まない
            raw = None
        if not isinstance(raw, str) or not raw or len(raw) > _MAX_HEADER:
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        cleaned = _clean(value)
        if cleaned is not None:
            out[key] = cleaned
    return out


def regain_minutes(parsed: dict) -> int | None:
    """`X-Business-Use-Case-Usage` の `estimated_time_to_regain_access` の最大（分）。無ければ None。"""
    buc = parsed.get("business_use_case_usage") if isinstance(parsed, dict) else None
    best = None
    if isinstance(buc, dict):
        for rows in buc.values():
            for row in rows if isinstance(rows, list) else []:
                value = row.get("estimated_time_to_regain_access") if isinstance(row, dict) else None
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                    best = max(best or 0, int(-(-value // 1)))
    return best


def path_for(account: str) -> str:
    if not accounts.name_is_safe(account):
        raise ValueError("invalid_account")
    return os.path.join(accounts.state_dir_for(account), FILE)


def read(account: str) -> dict | None:
    """置き場の中身（無ければ None・読めなければ `{"unreadable": True}`）。"""
    try:
        with open(path_for(account), encoding="utf-8") as stream:
            value = json.loads(stream.read(262145))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"unreadable": True}
    return value if isinstance(value, dict) else {"unreadable": True}


def _write(account: str, value: dict) -> None:
    secrets_fs.atomic_write_json(path_for(account), value)


def _now(now):
    return now or jst.now_jst()


def _until(row, now) -> datetime.datetime | None:
    wait = row.get("wait") if isinstance(row, dict) else None
    raw = wait.get("until") if isinstance(wait, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        until = datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if until.tzinfo is None:
        return None
    return until


def waiting(account: str, *, now=None) -> dict | None:
    """待ちの間なら `{"until", "minutes", "code", "since", "remaining_minutes"}`、でなければ None。"""
    if not accounts.name_is_safe(account):
        return None
    row = read(account)
    now = _now(now)
    until = _until(row, now)
    if until is None or until <= now:
        return None
    out = dict(row["wait"])
    out["remaining_minutes"] = max(1, int(-(-(until - now).total_seconds() // 60)))
    return out


def observe(account: str, headers, *, now=None) -> dict | None:
    """成功・失敗のどちらの応答でも呼ぶ。読めたヘッダがあれば残す（待ちは消さない）。"""
    parsed = parse(headers)
    if not parsed or not accounts.name_is_safe(account):
        return parsed or None
    now = _now(now)
    row = read(account)
    value = {"version": VERSION, "observed_at": jst.iso(now), "headers": parsed}
    until = _until(row, now)
    if until is not None and until > now:
        value["wait"] = row["wait"]
    try:
        _write(account, value)
    except (OSError, ValueError):
        pass   # 残せなくても呼び出しは止めない（量の写しであって柵ではない）
    return parsed


def note_rate_limited(account: str | None, headers, code, *, now=None) -> tuple[int, str]:
    """量の制限で断られた。待つ分と、いつまでかを返し、口座が分かれば state に残す。"""
    parsed = parse(headers)
    minutes = regain_minutes(parsed) or DEFAULT_WAIT_MINUTES
    minutes = max(1, min(MAX_WAIT_MINUTES, minutes))
    now = _now(now)
    until = jst.iso(now + datetime.timedelta(minutes=minutes))
    if account and accounts.name_is_safe(account):
        value = {"version": VERSION, "observed_at": jst.iso(now), "headers": parsed,
                 "wait": {"until": until, "minutes": minutes,
                          "code": code if isinstance(code, int) and not isinstance(code, bool) else None,
                          "since": jst.iso(now)}}
        try:
            _write(account, value)
        except (OSError, ValueError):
            pass
    return minutes, until


def summary(account: str, *, now=None) -> dict:
    """`thth quota` が出す形（読むだけ・網に出ない）。"""
    row = read(account) or {}
    headers = row.get("headers") if isinstance(row.get("headers"), dict) else {}
    return {
        "observed_at": row.get("observed_at"),
        "app_usage": headers.get("app_usage"),
        "business_use_case_usage": headers.get("business_use_case_usage"),
        "ads_insights_throttle": headers.get("ads_insights_throttle"),
        "regain_minutes": regain_minutes(headers),
        "waiting": waiting(account, now=now),
        "unreadable": bool(row.get("unreadable")),
    }
