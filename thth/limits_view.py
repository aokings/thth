"""柵の表を口座・主体ごとに見せる（`thth account status`・`thth principal show` の「上限と連投の柵」・段 3・§8.3）。

行ごとに「今効いている数・窓・今の数（使った数）・超えたらどうするか・どこから来たか（媒体の台帳の既定／THTH の
既定／口座の明示／主体の明示・今の鍵か `limits` か）・根拠と確かさ」を出す。読むだけ（何も書かない）。
使った数は柵と同じ材料（`guard.summary`・`principal_summary`・`sent/` の出来事）から数える。URL で決まる
行（同じホスト・1 URL に 1 回）は依頼が無いと数えられないので `used` は None。
"""
from __future__ import annotations

import datetime

from . import assess, jst, limits_config, limits_policy

ORIGIN_WORDS = {"account": "口座の明示", "principal": "主体の明示"}
CONFIDENCE_WORDS = {"high": "高", "medium": "中", "low": "低"}
SCOPE_WORDS = {"account": "口座", "principal": "主体", "blog": "ブログ", "owner": "owner", "app": "app"}
PURPOSE_WORDS = {"platform": "(a)", "safety": "(b)", "preference": "(c)"}


def _window_words(row) -> str:
    window = row.get("window")
    if window == "day":
        return "今日"
    if window == "forever":
        return "永久"
    if isinstance(window, dict):
        if window.get("minutes"):
            return f"{window['minutes']} 分"
        if window.get("hours"):
            return f"{window['hours']} 時間"
        if "quiet_hours" in window:
            return "夜間 " + "〜".join(window["quiet_hours"])
    return "—"


def _origin_words(row) -> str:
    if row["origin"] in ORIGIN_WORDS:
        via = row.get("via") or ""
        if via.startswith("alias:"):
            return f"{ORIGIN_WORDS[row['origin']]}（今の鍵 {via.split(':', 1)[1]}・別名）"
        if via == "limits":
            return f"{ORIGIN_WORDS[row['origin']]}（limits）"
        return ORIGIN_WORDS[row["origin"]]
    if str(row.get("trait") or "").startswith("thth:"):
        return "THTH の既定"
    return "媒体の台帳の既定"


def _confidence_words(row) -> str:
    words = []
    if row.get("confidence"):
        words.append(f"確かさ {CONFIDENCE_WORDS.get(row['confidence'], row['confidence'])}")
    if row.get("count_evidence") == "inferred":
        words.append("数は推測")
    elif row.get("count_evidence") == "thth_line":
        words.append("数は THTH の線")
    return "・".join(words)


def _minutes_used(row, events, now) -> int:
    start = now - datetime.timedelta(minutes=row["window"]["minutes"])
    inside = [(at, key) for action, at, key in events
              if assess.counts_as(action, row["actions"]) and start < at <= now]
    if row["key"] == "target":
        return len({key for _at, key in inside})
    return len(inside)


def _used(row, req, m, now):
    """使った数（数えられない行は None）。"""
    try:
        if row["count"] is None:
            return None
        if row["param"][0] == "fotolife":
            # フォトライフの上げは画像の台帳で数える（柵と同じ `uploads_today`）
            from . import hatena_fotolife
            return hatena_fotolife.uploads_today(req.principal, now) if req.principal else None
        if row["signal"] == "frequency":
            if isinstance(row["window"], dict) and row["window"].get("minutes"):
                events = m.get("events") if row["scope"] == "account" else m.get("principal_events")
                return _minutes_used(row, events, now)
            if row["window"] == "day":
                actions = [a for a in (row["applies_to"] if row["applies_to"] != "*" else row["actions"])
                           if a not in ("schedule", "reply")] or ["post"]
                req.action = actions[0]
                return assess.SIGNAL_FUNCS["frequency"](row, req, m)["used"]
        if row["signal"] == "self_reference":
            return m.get("marks")["own_today"]
    except (KeyError, TypeError, ValueError, OSError):
        return None
    return None


def _materials(account, cfg, principal, principal_cfg, now):
    from . import guard
    loaders = {}
    if account is not None:
        loaders["counts"] = lambda: guard.summary(account, cfg, now=now)
        loaders["events"] = lambda: guard._events_of(account)  # noqa: SLF001 — 柵と同じ材料
        loaders["reactions_today"] = lambda: guard.reactions_today(account, now=now)
    if principal is not None:
        loaders["whole"] = lambda: guard.principal_summary(principal, principal_cfg, now=now)
        loaders["principal_events"] = lambda: guard.principal_events(principal)
        loaders["marks"] = lambda: guard.bookmark_counts(principal, now=now)
    return assess.Materials(loaders=loaders, fns={"principal_scheduled_same_day": lambda day: 0})


def _shape(row, used) -> dict:
    holder = limits_config.holder_of(row["id"])
    out = {"id": row["id"], "scope": row["scope"], "window": row["window"], "count": row["count"], "used": used,
           "on_exceed": row["on_exceed"], "phase": row["phase"], "purpose": row["purpose"],
           "origin": row["origin"], "via": row.get("via"), "trait": row["trait"],
           "confidence": row.get("confidence"), "count_evidence": row.get("count_evidence"),
           "count_confidence": row.get("count_confidence"), "settable": holder,
           "alias": limits_config.alias_of(row["id"])}
    if row["id"] == limits_config.FLOOR_ID:
        out["floor_raised"] = bool(row.get("floor_raised"))
        out["floor_reason"] = row.get("floor_reason")
    return out


def account_table(account, cfg, *, now=None) -> list:
    """口座に掛かる行（表の順）。はてなの口座は主体の行も（数は主体の台帳から）。"""
    from . import principals
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    principal = principal_cfg = None
    if principals.uses_principal(cfg):
        principal = cfg.get("principal")
        principal_cfg = principals.load_principal(principal)
        families = ("hatenablog", "hatenabookmark")
    else:
        families = ("sns", "reaction")
    blog = None
    if principal_cfg is not None:
        blog = principals.blog_entry(principal_cfg, str(cfg.get("blog_id") or "").strip().lower()) or {}
    m = _materials(account, cfg, principal, principal_cfg, now)
    req = assess.Request(account=account, cfg=cfg, action="post", now=now, family=families[0],
                         principal=principal, blog_id=str(cfg.get("blog_id") or "").strip().lower() or None)
    out = []
    seen = set()
    for family in families:
        media = ("hatenabookmark" if family == "hatenabookmark" else "hatenablog") if principal else None
        for row in limits_policy.policy():
            if row["id"] in seen or family not in row["family"] or row["phase"] == "halt":
                continue
            if not limits_policy._applies_to_media(row, family, media or cfg.get("media")):  # noqa: SLF001
                continue
            seen.add(row["id"])
            resolved = limits_policy.resolve(row, cfg=cfg, principal_cfg=principal_cfg, blog=blog,
                                             media=media)
            req.family = family
            out.append(_shape(resolved, _used(resolved, req, m, now)))
    return out


def principal_table(principal, principal_cfg, *, now=None) -> list:
    """主体の台帳で決まる行（主体・owner の単位とフォトライフ）。"""
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    m = _materials(None, {}, principal, principal_cfg, now)
    req = assess.Request(account="", cfg={}, action="entry_create", now=now, family="hatenablog",
                         principal=principal)
    out = []
    for row in limits_policy.policy():
        if row["scope"] not in ("principal", "owner"):
            continue
        media = "hatenabookmark" if "hatenabookmark" in row["family"] else (
            "fotolife" if "fotolife" in row["family"] else "hatenablog")
        resolved = limits_policy.resolve(row, cfg={}, principal_cfg=principal_cfg, media=media)
        out.append(_shape(resolved, _used(resolved, req, m, now)))
    return out


def lines(rows, *, legacy=(), set_hint="") -> list:
    """人向けの表（`show` の節）。"""
    out = ["  上限と連投の柵（効く順・数／窓／今の数／超えたら／どこから・根拠）:"]
    for row in rows:
        count = "数えない" if row["count"] is None else str(row["count"])
        used = "" if row["used"] is None else f"今 {row['used']}／"
        where = _origin_words(row)
        why = row["trait"] + (f"（{_confidence_words(row)}）" if _confidence_words(row) else "")
        out.append(f"    {row['id']:<28} {SCOPE_WORDS.get(row['scope'], row['scope'])} {_window_words(row)}"
                   f"  {used}{count}  {row['on_exceed']} {PURPOSE_WORDS.get(row['purpose'], '')}"
                   f"  {where}  {why}")
        if row.get("floor_raised"):
            out.append(f"    → **最低線（write.floor）を持ち主が上げています**: {row['window']['minutes']} 分に "
                       f"{row['count']}（理由: {row.get('floor_reason')}）")
    if legacy:
        out.append(f"  古い書き方（別名・まだ読みます・段 5 で撤去）: {'・'.join(legacy)}"
                   + (f"。新しい書き方は {set_hint}" if set_hint else ""))
    return out
