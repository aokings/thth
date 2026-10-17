"""記事の名で反応を束ねる（設計 4.0 S6 §5・`thth article reactions`）。

**読むだけ。** 配信の台帳（はてなブログ）→ 告知（同じ project の SNS の `sent/` の `announces`）→
ブックマーク（`sent/bm:` の自分の分＝登録の成立・台帳 `data/hatena/reactions/` の第三者の分）の線を
たどり、出所ごとの数を `source`・`attribution`・`window` つきで**並べる**。足さない（`total` の鍵は作らない）・
順位を付けない（合計と順位は hub）。サイトの訪問は THTH に無い（`cannot_say: site_not_in_thth`）。

媒体の API は呼ばない。`fetch=True`（`--fetch`・手元の CLI だけ）のときだけ、はてなの 3 つを今 1 回読んで
`window.kind: now` の行を添える（台帳には書かない）。

本文（第三者のブックマークのコメント）と `user` は既定で出さない。`comments=True`（`--comments`）のときだけ
`deliveries[].comments[]` に出す（masaru の裁定 10/17: 台帳には残す・見せる口は `--comments` に絞る）。
"""
from __future__ import annotations

import json
import sys

from . import accounts as accounts_mod
from . import jst

SCHEMA = "thth.article_reactions/1"
MARK_24 = 24
BOOKMARK_METRICS = ("bookmark_total", "bookmark_others", "bookmark_comments_n")
LIMITATIONS = ["足さない・順位を付けない（合計は hub）", "自分の主体のブックマークとスターは others に入れない",
               "エントリー情報はキャッシュ（cached: true）", "サイトの訪問は THTH に無い（hub の SC・CF）",
               "THTH を通した配信・告知・ブックマークだけ"]


class ArticleReactionsError(ValueError):
    """問いが受け取れない（`article_required`・`target_unknown`）。"""


# --------------------------------------------------------------------------
# 行の形（§5.3）
# --------------------------------------------------------------------------

def _reaction(source, metric, value, obj, *, attribution="direct", basis=None, window) -> dict:
    return {"source": source, "metric": metric, "value": value, "object": obj, "attribution": attribution,
            "attribution_basis": basis, "window": window}


def _window(kind, *, mark=None, age_hours=None, at=None, origin_at=None, cached=False, missing=None) -> dict:
    return {"kind": kind, "mark": mark, "age_hours": age_hours, "at": at, "origin_at": origin_at,
            "cached": cached, "missing": missing}


def _hours(origin, now):
    return round((now - origin).total_seconds() / 3600, 2) if origin is not None else None


def _strip(row) -> dict:
    """台帳の行の写し（第三者の `bookmarks[]`＝本文と user を落とす）。"""
    out = dict(row)
    if isinstance(out.get("bookmark"), dict):
        out["bookmark"] = {k: v for k, v in out["bookmark"].items() if k != "bookmarks"}
    return out


def _missing_reason(rows_for_mark, what):
    """その刻みの行に残っている `what` の欠けの理由（無ければ None）。"""
    for row in reversed(rows_for_mark):
        for m in row.get("missing") or []:
            if m.get("what") == what:
                return m.get("reason")
    return None


def _hatena_values(row, metric):
    """`(value, missing, cached)`。"""
    bookmark, star = row.get("bookmark"), row.get("star")
    if metric == "bookmark_total":
        return bookmark.get("total"), None, False
    if metric == "bookmark_others":
        value = bookmark.get("others")
        return value, (None if value is not None else "own_unknown"), True
    if metric == "bookmark_comments_n":
        value = bookmark.get("comments_n")
        return value, (None if value is not None else "own_unknown"), True
    if metric == "star_others":
        return star.get("others"), None, False
    raise ValueError(metric)


def hatena_reactions(obj, rows, *, origin, now) -> list:
    """配信 1 本のはてなの行（各出所の 24h の刻みと、いちばん新しい刻みの 2 行・§5.3）。"""
    from . import hatena_reactions as ledger
    slots = ledger.by_mark(rows)
    rows_by_mark = {}
    for row in rows:
        for mark in row.get("marks") or []:
            rows_by_mark.setdefault(mark, []).append(row)
    age_now = _hours(origin, now)
    out = []
    for source, key, metrics in (("hatena_bookmark", "bookmark", BOOKMARK_METRICS),
                                 ("hatena_star", "star", ("star_others",))):
        have = sorted(m for m, slot in slots.items() if key in slot)
        chosen = [MARK_24] + ([have[-1]] if have and have[-1] != MARK_24 else [])
        for mark in chosen:
            row = slots.get(mark, {}).get(key)
            for metric in metrics:
                inferred = metric == "bookmark_others"
                kwargs = {"attribution": "inferred" if inferred else "direct",
                          "basis": "total_minus_self" if inferred else None}
                if row is not None:
                    value, missing, cached = _hatena_values(row, metric)
                    window = _window("age_mark", mark=mark, age_hours=row.get("age_hours"),
                                     at=row.get("collected_at"), origin_at=jst.iso(origin) if origin else None,
                                     cached=cached, missing=missing)
                else:
                    reason = _missing_reason(rows_by_mark.get(mark, []), key)
                    if reason == "not_supported":
                        missing = "not_supported"
                    elif age_now is not None and age_now < mark:
                        missing = "not_yet"
                    else:
                        missing = "not_fetched" + (f":{reason.split(':', 1)[1]}" if reason and ":" in reason else "")
                    value = None
                    window = _window("age_mark", mark=mark, origin_at=jst.iso(origin) if origin else None,
                                     cached=metric in ("bookmark_others", "bookmark_comments_n"), missing=missing)
                out.append(_reaction(source, metric, value, obj, window=window, **kwargs))
    return out


def now_reactions(obj, row, *, origin) -> list:
    """`--fetch` の今の行（`window.kind: now`・台帳には書かない）。"""
    out = []
    for source, key, metrics in (("hatena_bookmark", "bookmark", BOOKMARK_METRICS),
                                 ("hatena_star", "star", ("star_others",))):
        for metric in metrics:
            inferred = metric == "bookmark_others"
            kwargs = {"attribution": "inferred" if inferred else "direct",
                      "basis": "total_minus_self" if inferred else None}
            if key in row:
                value, missing, cached = _hatena_values(row, metric)
            else:
                reason = _missing_reason([row], key) or "not_fetched"
                value, missing, cached = None, reason, metric in ("bookmark_others", "bookmark_comments_n")
            out.append(_reaction(source, metric, value, obj, window=_window(
                "now", age_hours=row.get("age_hours"), at=row.get("collected_at"),
                origin_at=jst.iso(origin) if origin else None, cached=cached, missing=missing), **kwargs))
    return out


def _comments(rows) -> list:
    """第三者のブックマーク（user ごとに新しい行の値・`--comments` のときだけ出す）。"""
    seen = {}
    for row in sorted(rows, key=lambda r: r.get("collected_at") or ""):
        for b in ((row.get("bookmark") or {}).get("bookmarks") or []):
            seen[b.get("user")] = {"user": b.get("user"), "timestamp": b.get("timestamp"),
                                   "comment": b.get("comment") or "", "tags": list(b.get("tags") or []),
                                   "first_seen_at": (seen.get(b.get("user")) or {}).get("first_seen_at")
                                   or row.get("collected_at")}
    return sorted(seen.values(), key=lambda b: (b["first_seen_at"] or "", b["user"] or ""))


# --------------------------------------------------------------------------
# 告知（SNS）
# --------------------------------------------------------------------------

class _SNS:
    """告知の口座ごとの材料（measured・click の Index）を 1 度だけ読む。"""

    def __init__(self, now, allowed_names):
        self.now = now
        self.allowed_names = allowed_names
        self.cache = {}

    def get(self, name):
        if name not in self.cache:
            from . import click_attribution, measured
            cfg = accounts_mod.load_account(name)
            loaded = measured.load(name, observation_metadata=True)
            posts = {str(p["post_id"]): p for p in loaded["posts"]}
            index = None
            if cfg.get("media") == "threads":
                items = [(pid, jst.parse(p.get("posted_at"))) for pid, p in posts.items()]
                index = click_attribution.Index.for_account(name, cfg, posts=[i for i in items if i[1]],
                                                            account_daily=loaded.get("account_daily"), now=self.now)
            self.cache[name] = (cfg, posts, index)
        return self.cache[name]


def _sns_rows(post) -> list:
    return [{"marks": r.get("marks"), "age_hours": r.get("age_hours"), "collected_at": r.get("collected_at"),
             "metrics": r.get("metrics")} for r in (post or {}).get("rows") or []]


def announcement_reactions(told, rows, *, replies_n, clicks, posted, now) -> list:
    from . import measured
    obj = {"kind": "announcement", "id": told["post_id"], "account": told["account"],
           "announces": told["announces"], "url": None}
    slots = {}
    for row in sorted(rows, key=lambda r: r.get("collected_at") or ""):
        for mark in row.get("marks") or []:
            slots[mark] = row
    names = []
    for row in rows:
        for name in (row.get("metrics") or {}):
            if name not in names:
                names.append(name)
    names = names or list(measured.POST_METRIC_NAMES)
    age_now = _hours(posted, now)
    have = sorted(slots)
    chosen = [MARK_24] + ([have[-1]] if have and have[-1] != MARK_24 else [])
    out = []
    for mark in chosen:
        row = slots.get(mark)
        for name in names:
            if row is not None:
                value = (row.get("metrics") or {}).get(name)
                window = _window("age_mark", mark=mark, age_hours=row.get("age_hours"), at=row.get("collected_at"),
                                 origin_at=jst.iso(posted) if posted else None,
                                 missing=None if value is not None else "not_fetched:metric_missing")
            else:
                value = None
                window = _window("age_mark", mark=mark, origin_at=jst.iso(posted) if posted else None,
                                 missing="not_yet" if age_now is not None and age_now < mark else "not_fetched")
            out.append(_reaction("sns", name, value, obj, window=window))
    if replies_n is not None:
        out.append(_reaction("sns", "replies_received", replies_n, obj, window=_window(
            "ledger", at=jst.iso(now), origin_at=jst.iso(posted) if posted else None)))
    if clicks is not None:
        from . import click_attribution
        missing = clicks.get("cannot_say") or clicks.get("clicks_missing")
        out.append(_reaction("sns_clicks", "clicks_72h", clicks.get("clicks_72h"), obj, attribution="inferred",
                             basis=click_attribution.BASIS, window=_window(
                                 click_attribution.WINDOW, origin_at=jst.iso(posted) if posted else None,
                                 missing=missing if clicks.get("clicks_72h") is None else None)))
    return out


# --------------------------------------------------------------------------
# 束ね（§5.2）
# --------------------------------------------------------------------------

def _out_of_scope(project, allowed_names) -> bool:
    """同じ project の SNS の口座に、鍵の範囲の外のものがあるか（読まずに言う）。"""
    if allowed_names is None:
        return False
    from . import announce
    return any(data.get("project") == project and data.get("media") != "hatenablog" and name not in allowed_names
               for name, data in announce._raw_accounts())


def build(target, *, article=None, delivery=None, now=None, allowed_names=None, fetch=False,
          comments=False) -> dict:
    from . import deliveries_cli, hatena_reactions as ledger, replies as replies_mod
    now = now if now is not None else jst.now_jst()
    if not isinstance(article, str) or not article.strip():
        raise ArticleReactionsError("article_required")
    try:
        listing = deliveries_cli.build(target, article=article, now=now, allowed_names=allowed_names)
    except deliveries_cli.DeliveriesError:
        raise ArticleReactionsError("target_unknown") from None
    cannot_say = {"site_not_in_thth"}
    for code in listing["cannot_say"]:
        if code in ("deliveries_unreadable", "delivery_unreadable"):
            cannot_say.add("deliveries_unreadable")
        elif code == "sent_unreadable":
            cannot_say.add("sent_unreadable")
    unreadable = [u for u in listing["unreadable"]]
    configs, deliveries_out, reactions = {}, [], []
    chosen_ids = set()
    project = None
    for record in listing["deliveries"]:
        if delivery is not None and record["delivery"] != delivery:
            continue
        name = record["account"]
        if name not in configs:
            configs[name] = accounts_mod.load_account(name)
        cfg = configs[name]
        project = project or cfg.get("project")
        chosen_ids.add(record["delivery"])
        origin = ledger.origin_at(record)
        rows, broken = ([], False) if record["state"] == "deleted" else ledger.read_rows(cfg, name, record["delivery"])
        if broken:
            cannot_say.add("reactions_unreadable")
            unreadable.append({"account": name, "delivery": record["delivery"], "reason": "reactions_unreadable"})
        mark = deliveries_cli.bookmark_of(record)
        entry = {"delivery": record["delivery"], "account": name, "blog_id": record.get("blog_id"),
                 "state": record["state"], "published_url": record.get("published_url"),
                 "published_at": jst.iso(origin) if origin else None,
                 "registration": ({"result": mark.get("result"), "sent_at": mark.get("sent_at"),
                                   "retracted_at": mark.get("retracted_at")} if mark else None),
                 "rows": [_strip(r) for r in sorted(rows, key=lambda r: r.get("collected_at") or "")]}
        if comments:
            entry["comments"] = _comments(rows)
        obj = {"kind": "delivery", "id": record["delivery"], "account": name, "url": record.get("published_url")}
        if record["state"] != "deleted":
            reactions += hatena_reactions(obj, rows, origin=origin, now=now)
            if fetch and record.get("published_url") and record["state"] in ledger.TARGET_STATES:
                got = ledger.observe([record], own=ledger.self_ids(cfg), contact=ledger.contact_of(cfg))
                row = ledger.make_row(record, name, got.get(record["delivery"]) or {"missing": []}, now=now, marks=[])
                entry["now"] = _strip(row)
                if comments:
                    entry["now_comments"] = _comments([row])
                reactions += now_reactions(obj, row, origin=origin)
        deliveries_out.append(entry)

    # 告知（同じ project の SNS・`allowed_names` の中だけ）。
    announcements_out = []
    sns = _SNS(now, allowed_names)
    for told in listing.get("announcements") or []:
        if told["announces"] not in chosen_ids:
            continue
        try:
            cfg, posts, index = sns.get(told["account"])
        except Exception:  # noqa: BLE001 — 1 つの口座で全部を落とさない
            cannot_say.add("measured_unreadable")
            unreadable.append({"account": told["account"], "delivery": None, "reason": "measured_unreadable"})
            continue
        post = posts.get(str(told["post_id"]))
        rows = _sns_rows(post)
        posted = jst.parse((post or {}).get("posted_at")) or jst.parse(told.get("sent_at"))
        try:
            loaded = replies_mod.load(told["account"], post_id=told["post_id"], allowed_names=allowed_names)
            replies_n = sum(1 for r in loaded["replies"] if r.get("own") is not True)
        except Exception:  # noqa: BLE001
            replies_n = None
            cannot_say.add("replies_unreadable")
        clicks = None
        if index is not None and posted is not None:
            views = None
            for r in rows:
                if MARK_24 in (r.get("marks") or []):
                    views = (r.get("metrics") or {}).get("views")
            clicks = index.attribute(told["post_id"], posted, views)
        announcements_out.append({"account": told["account"], "post_id": told["post_id"],
                                  "announces": told["announces"], "sent_at": told.get("sent_at"),
                                  "retracted_at": told.get("retracted_at"), "medium": cfg.get("media"),
                                  "rows": rows, "replies_received": replies_n, "clicks": clicks})
        reactions += announcement_reactions(told, rows, replies_n=replies_n, clicks=clicks, posted=posted, now=now)
    if project is None:
        project = deliveries_cli._project_of(target, listing["accounts"])
    if _out_of_scope(project, allowed_names):
        cannot_say.add("scope_unavailable")
    return {"schema": SCHEMA, "report_type": "article_reactions", "generated_at": jst.iso(now),
            "target": target, "target_kind": listing["target_kind"], "article": article, "delivery": delivery,
            "fetched": bool(fetch), "comments_included": bool(comments),
            "deliveries": deliveries_out, "announcements": announcements_out, "reactions": reactions,
            "cannot_say": sorted(cannot_say), "unreadable": unreadable, "limitations": list(LIMITATIONS)}


# --------------------------------------------------------------------------
# 表示（§5.5）
# --------------------------------------------------------------------------

def _short(at) -> str:
    from . import hatena_entry
    return hatena_entry.short_at(at)


def _num(value) -> str:
    return "?" if value is None else str(value)


def _hatena_line(row, label) -> str:
    bm, star = row.get("bookmark"), row.get("star")
    parts = []
    if bm is not None:
        others = "言えない（自分の分が引けない）" if bm.get("others") is None else str(bm["others"])
        comment = f"（コメント {bm['comments_n']}）" if bm.get("comments_n") is not None else ""
        parts.append(f"ブックマーク: 全体 {_num(bm.get('total'))}・第三者 {others}{comment}")
    else:
        parts.append("ブックマーク: 取れていない")
    if star is not None:
        parts.append(f"スター: {_num(star.get('others'))}")
    else:
        reasons = [m.get("reason") for m in row.get("missing") or [] if m.get("what") == "star"]
        parts.append("スター: " + ("取らない（https で読めない）" if "not_supported" in reasons else "取れていない"))
    cached = "・キャッシュ" if bm is not None else ""
    marks = row.get("marks") or []
    when = f"{max(marks)}h の刻み" if marks else "今"
    return f"    {label}" + "  ".join(parts) + f"   [{when}・{_short(row.get('collected_at'))} 採取{cached}]"


def render(payload, out=print) -> None:
    from . import hatena_reactions as ledger
    out(f"記事の反応  {payload['article']}（{payload['target']}）")
    for entry in payload["deliveries"]:
        when = f"  公開 {_short(entry['published_at'])}" if entry.get("published_at") else ""
        out(f"  配信 {entry['delivery']}  {entry['account']}  {entry['state']}  {entry.get('published_url') or ''}{when}")
        if entry["state"] == "deleted":
            out("    消えた記事です（反応は読みません）")
            continue
        slots = ledger.by_mark(entry["rows"])
        row24 = (slots.get(MARK_24) or {}).get("_last")
        latest = ledger.latest_row(entry["rows"])
        if row24 is not None:
            out(_hatena_line(row24, "24h: "))
        if latest is not None and latest is not row24:
            out(_hatena_line(latest, "最新: "))
        if row24 is None and latest is None:
            out("    反応: 未採取")
        if entry.get("now") is not None:
            out(_hatena_line(entry["now"], "今: "))
        reg = entry.get("registration")
        if reg is None:
            out("    登録の成立: 自分の分 未")
        elif reg.get("retracted_at"):
            out(f"    登録の成立: 自分の分 消した（{_short(reg['retracted_at'])}）")
        else:
            out(f"    登録の成立: 自分の分 {reg.get('result')}（{_short(reg.get('sent_at'))}）")
        for c in entry.get("comments") or []:
            text = c["comment"].replace("\n", " ") or "（コメントなし）"
            out(f"    コメント {c['user']}  {c.get('timestamp') or ''}  {text}")
    for told in payload["announcements"]:
        out(f"  告知 {told['account']} {_short(told.get('sent_at'))}（post_id {told['post_id']}）")
        lines = {}
        for r in payload["reactions"]:
            o = r["object"]
            if o["kind"] != "announcement" or o["id"] != told["post_id"] or o["account"] != told["account"]:
                continue
            if r["source"] == "sns" and r["metric"] != "replies_received" and r["value"] is not None:
                lines.setdefault(r["window"]["mark"], []).append(f"{r['metric']} {r['value']}")
        for mark, parts in sorted(lines.items()):
            out(f"    {mark}h: " + "・".join(parts))
        tail = []
        if told.get("replies_received") is not None:
            tail.append(f"返信 {told['replies_received']} 件")
        clicks = told.get("clicks")
        if clicks is not None:
            if clicks.get("clicks_72h") is not None:
                tail.append(f"クリック 72h: {clicks['clicks_72h']}（{clicks.get('basis')}）")
            else:
                tail.append(f"クリック: 言えない（{clicks.get('cannot_say') or clicks.get('clicks_missing')}）")
        if tail:
            out("    " + "   ".join(tail))
    notes = {"site_not_in_thth": "サイトの訪問は THTH にありません（hub の SC・CF）",
             "scope_unavailable": "鍵の範囲の外の告知の口座は読んでいません",
             "measured_unreadable": "告知の口座の実測が読めませんでした",
             "reactions_unreadable": "反応の台帳が読めない配信があります",
             "sent_unreadable": "告知の記録（sent/）が読めない口座があります",
             "deliveries_unreadable": "配信の台帳が読めない口座があります",
             "replies_unreadable": "返信の台帳が読めない告知があります"}
    for code in payload["cannot_say"]:
        out(f"  言えないこと: {notes.get(code, code)}")


def register(inner) -> None:
    r = inner.add_parser("reactions", help="記事の名で、配信・告知・ブックマークの反応を出所つきで並べる（読むだけ・4.0 S6）")
    r.add_argument("account", metavar="target", help="account 名か project 名")
    r.add_argument("--article", default=None, help="記事の id（必須・例 kopicha/2026-10-arabica）")
    r.add_argument("--delivery", default=None, help="配信 id で絞る")
    r.add_argument("--fetch", action="store_true",
                   help="はてなの件数・エントリー情報・スターを今 1 回読んで添える（台帳には書かない）")
    r.add_argument("--comments", action="store_true", help="第三者のブックマークのコメントの本文も出す")
    r.add_argument("--json", action="store_true")
    r.set_defaults(func=cmd)


def cmd(args) -> int:
    try:
        payload = build(args.account, article=args.article, delivery=args.delivery, fetch=args.fetch,
                        comments=args.comments)
    except (ArticleReactionsError, accounts_mod.AccountError) as exc:
        print(str(exc), file=sys.stderr)
        if args.json:
            print(json.dumps({"schema": SCHEMA, "cannot_say": [str(exc)]}, ensure_ascii=False))
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    render(payload)
    return 0
