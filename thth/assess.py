"""頼みごとの見立て（設計 `docs/設計_上限と連投の柵_2026-10-17.md` §5・§7.1・段 1）。

道: 頼み（`Request`）と材料（`Materials`・今の記録だけ）と行（`limits_policy.rules_for`）から、行ごとに
信号の値を出し、行の `on_exceed` で結論にする。結論の構造が `assessment`（§7.1）。

**段 1 は今の振る舞いを写す**: 行は表の順に当て、最初に当たった行が結論（今の `guard.check` の `if` の順・
first-hit）。`advise` の行は結論にせず `advisories` に積む（断らない・止めない）。確率・重み・学習は入れない
（§5.4）。印を置く・断るのは `thth/guard.py`（ここは値を出すだけで、何も書かない）。

信号（最初の版は 7 つ・§5.2）:

- `frequency`: 単位 × 窓 × 鍵の数（日の上限・burst・反応の日の上限）
- `same_target`: 同じ対象への重なり（ブックマークの 1 URL に 1 回）
- `same_host`: ブックマークの URL のホストが今日すでに何件か
- `self_reference`: 自分の記事（`target: own`）へのブックマーク
- `counterpart`: 返信の相手との関わり（段 1 は予告だけ・呼び手が `counterpart_engaged=False` を渡したとき）
- `recent_platform_refusal`: 媒体の断りで置いた印（`publish_hold`）
- `quiet_and_interval`: 夜間・前の公開からの間隔
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field

from . import jst

SIGNALS = ("frequency", "same_target", "same_host", "self_reference", "counterpart",
           "recent_platform_refusal", "quiet_and_interval")
OUTCOMES = ("pass", "advise", "wait", "refuse", "stopped")
# 強さ（§5.3）。段 1 の結論は表の順の first-hit で、強さは `strongest` で読めるだけ（設計の末尾）。
STRENGTH = {"pass": 0, "advise": 1, "wait": 2, "refuse": 3, "stopped": 4}
_OUTCOME_OF = {"stop": "stopped", "refuse": "refuse", "wait": "wait", "advise": "advise", "hold": "wait",
               "halted": "stopped"}


@dataclass
class Request:
    """頼み 1 件（口座・動作・対象）。`action` は `limits_policy` の動作名（`post`・`reply`・`bookmark_add` など）。"""

    account: str
    cfg: dict
    action: str
    now: datetime.datetime
    family: str
    publish_at: datetime.datetime | None = None
    scheduled_same_day: int = 0
    principal: str | None = None
    principal_scheduled_same_day: int | None = None
    blog_id: str | None = None
    bookmark_url: str | None = None
    bookmark_target: str | None = None
    bookmark_once: bool = True
    delivery: str | None = None
    pending_replies: tuple = ()
    counterpart_engaged: bool | None = None


@dataclass
class Materials:
    """材料（今の記録だけ）。値は最初に要ったときに 1 回だけ作る（`loaders`）。`fns` は引数をとる計算。"""

    loaders: dict = field(default_factory=dict)
    fns: dict = field(default_factory=dict)
    _cache: dict = field(default_factory=dict)

    def get(self, name):
        if name not in self._cache:
            value = self.loaders[name]
            self._cache[name] = value() if callable(value) else value
        return self._cache[name]

    def call(self, name, *args, **kwargs):
        return self.fns[name](*args, **kwargs)


def _midnight_after(day):
    return datetime.datetime.combine(day + datetime.timedelta(days=1), datetime.time(0, 0), tzinfo=jst.JST)


def _quiet_end(now, quiet_hours):
    hour, minute = (int(x) for x in quiet_hours[1].split(":"))
    now = jst.to_jst(now)
    end = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return end if end > now else end + datetime.timedelta(days=1)


def _hit(used, adds, count, next_at=None, **extra):
    out = {"hit": used + adds > count, "used": used, "count": count, "next_at": next_at}
    out.update(extra)
    return out


# --------------------------------------------------------------------------
# 信号
# --------------------------------------------------------------------------

def _frequency(rule, req, m):
    count, scope, window = rule["count"], rule["scope"], rule["window"]
    rid = rule["id"]
    if window == "day":
        day = (jst.to_jst(req.publish_at).date() if req.action == "schedule" and req.publish_at is not None
               else req.now.date())
        nxt = _midnight_after(day)
        if scope == "account" and rule["param"][1] == "daily_max_reactions":
            used = m.get("reactions_today")
        elif scope == "account":
            counts = m.get("counts")
            source = counts["posts"] if rule["param"][1] == "daily_max_posts" else None
            if source is None:
                used = counts["retracts_today"]
            elif req.action == "schedule":
                used = sum(1 for at in source if at.date() == day) + req.scheduled_same_day
            else:
                used = counts["posts_today"]
        elif scope == "blog":
            whole = m.get("whole")
            rows = whole["posts"] if rule["param"][1] == "daily_max_posts" else whole["retracts"]
            used = sum(1 for at, b in rows if b == req.blog_id and at.date() == day)
        elif scope == "principal":
            whole = m.get("whole")
            what = rule["param"][1]
            if what == "daily_max_bookmarks":
                used = whole["bookmarks_today"]
            elif what == "daily_max_retracts":
                used = whole["retracts_today"]
            elif req.action == "schedule":
                scheduled = req.principal_scheduled_same_day
                if scheduled is None:
                    scheduled = m.call("principal_scheduled_same_day", day)
                used = sum(1 for at, _ in whole["posts"] if at.date() == day) + scheduled
            else:
                used = whole["posts_today"]
        else:
            raise ValueError(f"unknown_scope:{rid}")
        return _hit(used, 1, count, jst.iso(nxt))
    # 分の窓（burst）
    if rule["detail"] == "bookmark_burst":
        whole = m.get("whole")
        used = whole["bookmark_recent"]
        result = _hit(used, 1, count)
        if result["hit"]:
            result["next_at"] = jst.iso(m.call("bookmark_burst_slot"))
        return result
    materials = m.get("counts") if scope == "account" else m.get("whole")
    adds = 1
    if rule["key"] == "target" and isinstance(req.delivery, str) and req.delivery:
        # 同じ配信は窓の中で 1 件（4.0 S5 §6）。
        adds = 0 if ("delivery", req.delivery) in materials["recent_keys"] else 1
    used = materials["recent"]
    if rule["on_exceed"] == "wait" and req.action == "reply":
        # 返信の順番待ち（3.25.0）: 並んでいる返信がまだ先にあれば、窓が空いていても後ろに並ぶ。
        ahead = [jst.to_jst(at) for at in (req.pending_replies or ()) if jst.to_jst(at) > req.now]
        result = _hit(used, adds, count, pending=len(ahead))
        if ahead or result["hit"]:
            result["hit"] = True
            result["next_at"] = jst.iso(m.call("burst_slot", ahead))
        return result
    return _hit(used, adds, count)


def _same_target(rule, req, m):
    if not req.bookmark_once:
        # 呼び手が owner のロックの中で見る（`hatena_bookmark.execute_locked`）。
        return {"hit": False, "used": 0, "count": rule["count"], "next_at": None}
    detail = m.get("owner_bookmarked")
    return {"hit": detail is not None, "used": 0 if detail is None else 1, "count": rule["count"],
            "next_at": None, "detail": detail}


def _same_host(rule, req, m):
    marks = m.get("marks")
    return _hit(marks["same_host_today"], 1, rule["count"], jst.iso(_midnight_after(req.now.date())),
                host=marks.get("host"))


def _self_reference(rule, req, m):
    marks = m.get("marks")
    if req.bookmark_target != "own":
        return {"hit": False, "used": marks["own_today"], "count": rule["count"], "next_at": None}
    return _hit(marks["own_today"], 1, rule["count"], jst.iso(_midnight_after(req.now.date())))


def _counterpart(rule, req, m):
    return {"hit": req.counterpart_engaged is False, "used": None, "count": None, "next_at": None}


def _recent_platform_refusal(rule, req, m):
    held = m.get("publish_hold")
    return {"hit": held is not None, "used": None, "count": None, "next_at": None}


def _quiet_and_interval(rule, req, m):
    if rule["window"] == "quiet_hours" or "quiet_hours" in (rule["window"] or {}):
        quiet = req.cfg.get("quiet_hours")
        from .select import in_quiet_hours
        if quiet and in_quiet_hours(req.now, quiet):
            return {"hit": True, "used": None, "count": None, "next_at": jst.iso(_quiet_end(req.now, quiet))}
        return {"hit": False, "used": None, "count": None, "next_at": None}
    hours = rule["window"]["hours"]
    last = m.get("counts")["last_post_at"]
    if last is not None:
        ready = last + datetime.timedelta(hours=hours)
        if req.now < ready:
            return {"hit": True, "used": None, "count": None, "next_at": jst.iso(ready)}
    return {"hit": False, "used": None, "count": None, "next_at": None}


SIGNAL_FUNCS = {
    "frequency": _frequency,
    "same_target": _same_target,
    "same_host": _same_host,
    "self_reference": _self_reference,
    "counterpart": _counterpart,
    "recent_platform_refusal": _recent_platform_refusal,
    "quiet_and_interval": _quiet_and_interval,
}
assert set(SIGNAL_FUNCS) == set(SIGNALS)


# --------------------------------------------------------------------------
# 結論
# --------------------------------------------------------------------------

def finding(rule, value) -> dict:
    """行 1 つと信号の値から `assessment`（§7.1）。文（§7.2）と `how_to_pass` は段 4。"""
    outcome = _OUTCOME_OF[rule["on_exceed"]]
    detail = value.get("detail", rule["detail"])
    return {
        "outcome": outcome,
        "rule": rule["id"],
        "signal": rule["signal"],
        "phase": rule["phase"],
        "purpose": rule["purpose"],
        "scope": rule["scope"],
        "used": value.get("used"),
        "count": value.get("count", rule.get("count")),
        "window": rule.get("window"),
        "next_at": value.get("next_at"),
        "trait": {"id": rule["trait"]},
        "origin": rule.get("origin", "default"),
        "how_to_pass": None,
        "code": None if outcome == "advise" else rule["refusal"],
        "detail": detail,
    }


def stopped_assessment(scope, reason) -> dict:
    """止めの印（口座・主体）で断るときの `assessment`（行は持たない）。"""
    return {"outcome": "stopped", "rule": f"{scope}.stopped", "signal": None, "phase": "halt",
            "purpose": "safety", "scope": scope, "used": None, "count": None, "window": None, "next_at": None,
            "trait": None, "origin": None, "how_to_pass": None,
            "code": "principal_stopped" if scope == "principal" else "account_stopped", "detail": reason}


def evaluate(rules, req, m) -> dict:
    """行を表の順に当て、最初に当たった行の `assessment`（`advisories` つき）。どれも当たらなければ `pass`。"""
    advisories = []
    for rule in rules:
        if rule["count"] is None and rule["signal"] not in ("counterpart", "recent_platform_refusal"):
            continue  # 数えない行（ブログの欄が無い・夜間や間隔が台帳に無い）
        value = SIGNAL_FUNCS[rule["signal"]](rule, req, m)
        if not value["hit"]:
            continue
        result = finding(rule, value)
        if result["outcome"] == "advise":
            advisories.append(result)
            continue
        result["advisories"] = advisories
        return result
    return {"outcome": "pass", "rule": None, "code": None, "next_at": None, "advisories": advisories}


def strongest(results) -> dict | None:
    """結論の list のうち最も強いもの（§5.3 の `refuse` > `wait` > `advise` > `pass`・止めは別で最上）。"""
    best = None
    for item in results:
        if best is None or STRENGTH[item["outcome"]] > STRENGTH[best["outcome"]]:
            best = item
    return best
