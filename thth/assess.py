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
from .media_traits import SIGNALS_V1 as SIGNALS  # 語彙の正本は media_traits（段 2・監査 中-1）

OUTCOMES = ("pass", "advise", "wait", "refuse", "stopped")
# 強さ（§5.3）。結論は表の順の first-hit で、強さは `strongest` で読めるだけ（段 1・段 2 で決めたこと）。
STRENGTH = {"pass": 0, "advise": 1, "wait": 2, "refuse": 3, "stopped": 4}
# 当てる順（段 2 で決めたこと）: `table`（表の順の first-hit・今の振る舞い）。`safety_first` は設計 §6.6 の
# 順（止めの印・安全装置が先・残りは強い方）で、変えたら何が変わるかを試験で見せるためだけに置く。
ORDERS = ("table", "safety_first")
ORDER = "table"
# 記録の出来事の動作（`guard._action_rows`）と行の `actions` の対応。SNS の記録は投稿と返信を分けないので
# `post` は投稿・返信の行に数え、`kind` の無い・知らない記録ははてなの記事の行にも数える（数え漏らさない・今のまま）。
EVENT_ALIASES = {"post": ("post", "reply", "entry_create", "entry_update"),
                 "retract": ("retract", "entry_delete"),
                 # ブックマークの直し（S4 §21.4）: 追加の行の分の窓（連投・止め線・最低線）に数える。
                 "bookmark_edit": ("bookmark_add",)}
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


def counts_as(event_action, actions) -> bool:
    """記録の出来事 1 つが、行の `actions`（数える動作）に入るか。"""
    if event_action in actions:
        return True
    return any(a in actions for a in EVENT_ALIASES.get(event_action, ()))


def window_slot(times, count, minutes, now) -> datetime.datetime:
    """窓 `(t - 分, t]` に 1 件入る余地ができる最初の時刻（窓の中の古い順に `件数 + 1 - count` 番目が出る時刻・
    秒に切り上げ）。4.0.1 の `guard.bookmark_burst_slot` と同じ計算を行の窓と数で。"""
    times = sorted(jst.to_jst(at) for at in times)
    leave = len(times) + 1 - count
    if leave <= 0:
        return jst.to_jst(now)
    at = times[leave - 1] + datetime.timedelta(minutes=minutes)
    if at.microsecond:
        at = at.replace(microsecond=0) + datetime.timedelta(seconds=1)
    return at


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
    # 分の窓（burst・止め線・最低線）。**数える出来事は行の `actions` から作る**（段 2・T-P-2）: 材料は
    # 記録の出来事（`events`＝この口座・`principal_events`＝主体の全口座）の (動作, 時刻, 鍵) で、行の
    # `actions` に入るものだけを窓の中で数える。鍵 `target` は同じ鍵（同じ配信）を 1 件、`op` は出来事ごと。
    events = m.get("events") if scope == "account" else m.get("principal_events")
    minutes = window["minutes"]
    start = req.now - datetime.timedelta(minutes=minutes)
    inside = [(at, key) for action, at, key in events if counts_as(action, rule["actions"]) and start < at <= req.now]
    adds = 1
    if rule["key"] == "target":
        keys = {key for _at, key in inside}
        used = len(keys)
        if isinstance(req.delivery, str) and req.delivery and ("delivery", req.delivery) in keys:
            # 同じ配信は窓の中で 1 件（4.0 S5 §6）。
            adds = 0
    else:
        used = len(inside)
    if rule["on_exceed"] == "wait" and req.action != "reply":
        # ブックマークの窓（4.0.1）: 主体は止めず、窓が空く時刻を添えて断る。
        result = _hit(used, adds, count)
        if result["hit"]:
            result["next_at"] = jst.iso(window_slot([at for at, _k in inside], count, minutes, req.now))
        return result
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
        # 根拠（段 2・中-2）: 項目の確かさと、数の根拠・数の確かさ（推測の数は low・THTH の線は None）。
        "trait": {"id": rule["trait"], "confidence": rule.get("confidence"),
                  "count_evidence": rule.get("count_evidence"), "count_confidence": rule.get("count_confidence")},
        "origin": rule.get("origin", "default"),
        "how_to_pass": None,
        "code": None if outcome == "advise" else rule["refusal"],
        "detail": detail,
    }


SCOPE_JA = {"account": "口座", "principal": "主体", "owner": "owner", "blog": "ブログ", "app": "アプリ"}


def resume_hint(scope, name) -> str:
    """止めを戻す命令（§7.2 の `stopped` の文）。"""
    if scope == "principal":
        return f"thth principal resume {name} --by <名前>"
    return f"thth account resume {name} --by <名前>"


def _set_hint(result, *, account, principal) -> str | None:
    """数を変える命令（`scope` から 1 回だけ・§7.2 の「数は … で変えられます」）。変えられない行は None。"""
    rule = result.get("rule") or ""
    if result.get("scope") == "owner" or rule in ("bookmark.once",) or not rule:
        return None
    window = result.get("window")
    value = "<件数> <分>" if isinstance(window, dict) and window.get("minutes") else "<値>"
    if result.get("scope") in ("principal", "blog") and principal:
        return f"thth principal set {principal} {rule} {value} --by <名前>"
    if result.get("scope") == "account" and account:
        return f"thth account set {account} {rule} {value} --by <名前>"
    return None


def pass_hint(result, *, account=None, principal=None) -> str | None:
    """断り・待たせ・止めの `how_to_pass`（段 4・§7.1）。決まった文だけ（推測や採点はしない）。"""
    outcome = result.get("outcome")
    when = result.get("next_at")
    stamp = None
    if when:
        parsed = jst.parse(when)
        stamp = jst.to_jst(parsed).strftime("%m/%d %H:%M") if parsed else when
    setter = _set_hint(result, account=account, principal=principal)
    if outcome == "stopped":
        scope = result.get("scope")
        name = principal if scope == "principal" else account
        return resume_hint(scope, name) if name else None
    parts = []
    if stamp:
        parts.append(f"{stamp} 以降に打ち直す")
    if setter:
        parts.append(("急ぐなら " if stamp else "数を変えるなら ") + setter)
    if not parts and result.get("rule") == "bookmark.once":
        parts.append("同じページは 1 回だけです（変えられません）")
    return "。".join(parts) or None


def foresight_line(found) -> str | None:
    """1 段目の「見立て」の 1 行（§7.3・段 4）。断らない・止めない（決めるのは 2 段目）。"""
    if not isinstance(found, dict):
        return None
    if found.get("outcome") in ("pass", "advise"):
        return "見立て: 今の数では 2 段目で当たる柵はありません（打つときに数え直します）"
    code = found.get("code") or "?"
    rule = found.get("rule")
    where = ""
    if rule:
        where = f"（{rule}"
        if isinstance(found.get("used"), int) and isinstance(found.get("count"), int):
            where += f"・{found['used']}/{found['count']}"
        where += "）"
    out = f"見立て: このまま 2 段目を打つと {code}{where}になる見込みです"
    if found.get("how_to_pass"):
        out += f"。通すには: {found['how_to_pass']}"
    return out


def stopped_assessment(scope, reason) -> dict:
    """止めの印（口座・主体）で断るときの `assessment`（行は持たない）。"""
    return {"outcome": "stopped", "rule": f"{scope}.stopped", "signal": None, "phase": "halt",
            "purpose": "safety", "scope": scope, "used": None, "count": None, "window": None, "next_at": None,
            "trait": None, "origin": None, "how_to_pass": None,
            "code": "principal_stopped" if scope == "principal" else "account_stopped", "detail": reason}


def evaluate(rules, req, m, order=None) -> dict:
    """行を表の順に当て、最初に当たった行の `assessment`（`advisories` つき）。どれも当たらなければ `pass`。

    `order`（既定 `ORDER`）が `safety_first` なら、すべての行を当てたうえで、止めの印・安全装置の行の最初の
    当たりを、無ければ最も強い当たり（同じ強さは表の順）を結論にする（設計 §6.6・段 2 では使わない）。
    """
    order = order or ORDER
    if order not in ORDERS:
        raise ValueError("invalid_order")
    advisories = []
    hits = []
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
        if order == "table":
            result["advisories"] = advisories
            return result
        hits.append(result)
    if hits:
        first = next((h for h in hits if h["phase"] in ("halt", "safety")), None) or strongest(hits)
        first["advisories"] = advisories
        return first
    return {"outcome": "pass", "rule": None, "code": None, "next_at": None, "advisories": advisories}


def strongest(results) -> dict | None:
    """結論の list のうち最も強いもの（§5.3 の `refuse` > `wait` > `advise` > `pass`・止めは別で最上）。"""
    best = None
    for item in results:
        if best is None or STRENGTH[item["outcome"]] > STRENGTH[best["outcome"]]:
            best = item
    return best
