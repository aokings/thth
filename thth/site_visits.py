"""サイト側の訪問の数（GA4・hub 経由）を受け取り、糸に結ぶ（5.7.0・設計 効果測定と連動 §3 段 3 の THTH 側）。

**THTH は GA4 を読まない。** hub（SEO レーン）が GA4 を読み、`hub.site_visits/1` の JSON を 1 サイト 1 ファイルで
渡す。THTH は `thth scenario site-import <project> --file <json>` で受け取り、`state/<project>/site_visits/` に
控える（サイトと窓の終わりの日で 1 ファイル・同じ物を二度入れても上書きで増えない）。

結び方: GA4 の `campaign`（= 原稿のファイル名・拡張子なし）→ queue の原稿 → 原稿の札 `thread:` → 糸。
原稿のパスの `/` を `-` にした形（MMR の `threads-drafts-features-2026-11-02` など）も試す。
結ばれない campaign（`""`・`(organic)`・`(direct)` などの GA4 の値・原稿が無い・札が無い・2 本の糸にまたがる）は
`untied_campaigns` に**数だけ**（名前は出さない）。

取れなかった窓（`rows`・`referrers` が null で `unknown_reason`）は 0 と混ぜず `cannot_say`。

受け取る形（hub 側の出口・media-hub cff3a44・#6516）:

```
{"schema": "hub.site_visits/1", "site": "kopicha.org", "generated_at": "...",
 "window": {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD"},
 "rows": [{"campaign", "source", "medium", "sessions", "engaged_sessions", "avg_duration_s", "landing_page"}] | null,
 "referrers": [{"host", "sessions"}] | null,
 "unknown_reason": "..."（取れなかったときだけ）}
```

本文・個人の値は無い。控えるのは上の鍵だけ（ほかの鍵は捨てる・`landing_page` は `?` と `#` から後を落とす）。

5.11.0 段 8（設計 §7.4・masaru 11/03）: hub は日ごとの窓（`window.from == window.to`・終わり D-2）を
1 サイト 1 日 1 ファイルで送る（media-hub `scripts/ga4.py site-visits --days 1 --lag 2 --out-dir`）。schema は
同じ。THTH は日ごとの控えを糸の期間（seeds は `--days` の窓）に重なる日だけ足す（「日ごとの控え」の節）。
"""
from __future__ import annotations

import datetime
import json
import os
import re

from . import accounts as accounts_mod
from . import jst, secrets_fs

SCHEMA_IN = "hub.site_visits/1"
STORE_SCHEMA = "thth.site_visits_store/1"
IMPORT_SCHEMA = "thth.site_visits_import/1"
DIRNAME = "site_visits"
LABEL = "本記事への訪問（GA4・hub 経由）"
SOURCE = "ga4_via_hub"
MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 20000
MAX_TEXT = 300
GENERATED_SLACK = datetime.timedelta(hours=1)
SITE_RE = re.compile(r"[a-z0-9][a-z0-9.\-]{0,252}(/[a-z0-9._\-]{1,64}){0,4}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
REASON_RE = re.compile(r"[a-z0-9_]{1,64}")
ROW_KEYS = ("campaign", "source", "medium", "sessions", "engaged_sessions", "avg_duration_s", "landing_page")
# 未結の理由（数だけ出す）。
UNTIED_REASONS = ("not_a_campaign", "no_manuscript", "no_thread", "ambiguous")


class SiteVisitsError(ValueError):
    """受け取れない JSON。`code` は静的な符丁。"""

    def __init__(self, code, detail=""):
        super().__init__(code)
        self.code = code
        self.detail = detail


# ------------------------------------------------------------------ 受け取る

def _text(value, key, *, allow_none=True):
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or len(value) > MAX_TEXT:
        raise SiteVisitsError("site_visits_invalid", key)
    return value


def _count(value, key):
    """数（0 以上の整数）か null。負の数・bool・小数は断る。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SiteVisitsError("site_visits_invalid", key)
    if value < 0:
        raise SiteVisitsError("site_visits_negative", key)
    return value


def _seconds(value, key):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise SiteVisitsError("site_visits_invalid", key)
    if value < 0:
        raise SiteVisitsError("site_visits_negative", key)
    return round(float(value), 1)


def _date(value, key):
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        raise SiteVisitsError("site_visits_window_invalid", key)
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise SiteVisitsError("site_visits_window_invalid", key) from None


def _path_only(value):
    if value is None:
        return None
    return re.split(r"[?#]", value, 1)[0][:MAX_TEXT]


def validate(data, *, now) -> dict:
    """`hub.site_visits/1` → 控える形。だめなら `SiteVisitsError`（schema 違い・未来の窓・負の数など）。"""
    if not isinstance(data, dict):
        raise SiteVisitsError("site_visits_invalid", "object ではない")
    if data.get("schema") != SCHEMA_IN:
        raise SiteVisitsError("site_visits_schema_unknown", str(data.get("schema"))[:40])
    site = data.get("site")
    if not isinstance(site, str) or not SITE_RE.fullmatch(site.strip().lower()):
        raise SiteVisitsError("site_visits_site_invalid", str(site)[:40])
    site = site.strip().lower()
    generated = jst.parse(data.get("generated_at")) if isinstance(data.get("generated_at"), str) else None
    if generated is None:
        raise SiteVisitsError("site_visits_invalid", "generated_at")
    if generated > now + GENERATED_SLACK:
        raise SiteVisitsError("site_visits_generated_in_future", data["generated_at"][:32])
    window = data.get("window")
    if not isinstance(window, dict):
        raise SiteVisitsError("site_visits_window_invalid", "window")
    start, end = _date(window.get("from"), "window.from"), _date(window.get("to"), "window.to")
    if start > end:
        raise SiteVisitsError("site_visits_window_invalid", "from が to より後")
    if end > jst.to_jst(now).date():
        raise SiteVisitsError("site_visits_window_future", end.isoformat())
    rows_in, refs_in = data.get("rows"), data.get("referrers")
    reason = data.get("unknown_reason")
    if reason is not None and not isinstance(reason, str):
        raise SiteVisitsError("site_visits_invalid", "unknown_reason")
    rows = None
    if rows_in is not None:
        if not isinstance(rows_in, list) or len(rows_in) > MAX_ROWS:
            raise SiteVisitsError("site_visits_invalid", "rows")
        rows = []
        for i, row in enumerate(rows_in):
            if not isinstance(row, dict):
                raise SiteVisitsError("site_visits_invalid", f"rows[{i}]")
            campaign = _text(row.get("campaign"), f"rows[{i}].campaign")
            rows.append({
                "campaign": campaign if campaign is not None else "",
                "source": _text(row.get("source"), f"rows[{i}].source"),
                "medium": _text(row.get("medium"), f"rows[{i}].medium"),
                "sessions": _count(row.get("sessions"), f"rows[{i}].sessions"),
                "engaged_sessions": _count(row.get("engaged_sessions"), f"rows[{i}].engaged_sessions"),
                "avg_duration_s": _seconds(row.get("avg_duration_s"), f"rows[{i}].avg_duration_s"),
                "landing_page": _path_only(_text(row.get("landing_page"), f"rows[{i}].landing_page"))})
    referrers = None
    if refs_in is not None:
        if not isinstance(refs_in, list) or len(refs_in) > MAX_ROWS:
            raise SiteVisitsError("site_visits_invalid", "referrers")
        referrers = []
        for i, row in enumerate(refs_in):
            if not isinstance(row, dict):
                raise SiteVisitsError("site_visits_invalid", f"referrers[{i}]")
            referrers.append({"host": _text(row.get("host"), f"referrers[{i}].host"),
                              "sessions": _count(row.get("sessions"), f"referrers[{i}].sessions")})
    if rows is None or referrers is None:
        # 取れなかった窓。理由は符丁の形（[a-z0-9_]）だけ控える（読めない形は unknown）。
        text = (reason or "").strip().lower()
        reason = text if REASON_RE.fullmatch(text) else "unknown"
    else:
        reason = None
    return {"schema": STORE_SCHEMA, "source_schema": SCHEMA_IN, "site": site,
            "generated_at": jst.iso(generated), "window": {"from": start.isoformat(), "to": end.isoformat()},
            "rows": rows, "referrers": referrers, "unknown_reason": reason}


def store_dir(project) -> str:
    if not accounts_mod.name_is_safe(project):
        raise SiteVisitsError("target_unknown", project)
    return os.path.join(accounts_mod.state_dir_for(project), DIRNAME)


def _file_name(record) -> str:
    """サイトと窓の終わりの日で 1 ファイル（二度入れても上書き・増えない）。"""
    return f"{record['site'].replace('/', '_')}_{record['window']['to']}.json"


def read_input(path) -> dict:
    try:
        if os.path.getsize(path) > MAX_BYTES:
            raise SiteVisitsError("site_visits_too_large", "")
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise SiteVisitsError("site_visits_file_missing", "") from None
    except (OSError, UnicodeDecodeError, ValueError):
        raise SiteVisitsError("site_visits_unreadable_input", "") from None


def import_file(target, path, *, now=None) -> dict:
    """`thth scenario site-import <project> --file <json>`。控えて、結んだ結果の数を返す。"""
    from . import scenario
    now = now if now is not None else jst.now_jst()
    project, names = scenario.resolve(target)
    if not names:
        raise SiteVisitsError("target_unknown", str(target)[:64])
    record = validate(read_input(path), now=now)
    record["imported_at"] = jst.iso(now)
    directory = store_dir(project)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    stored = os.path.join(directory, _file_name(record))
    replaced = os.path.exists(stored)
    secrets_fs.atomic_write_json(stored, record, mode=0o600)
    ledger = scenario.load_ledger(project, names, now=now)
    tied = tie_rows(record, ledger.items)
    return {"schema": IMPORT_SCHEMA, "report_type": "site_import", "project": project,
            "site": record["site"], "window": record["window"], "generated_at": record["generated_at"],
            "stored": f"state/{project}/{DIRNAME}/{os.path.basename(stored)}", "replaced": replaced,
            "rows_total": len(record["rows"]) if record["rows"] is not None else None,
            "tied": tied["tied_summary"], "untied_campaigns": tied["untied_campaigns"],
            "cannot_say": sorted(set(ledger.cannot_say) | ({"site_visits_unavailable"} if record["rows"] is None
                                                            else set())),
            "unknown_reason": record["unknown_reason"]}


# ------------------------------------------------------------------ 読む

def read_all(project) -> tuple:
    """控えを全部読む（日ごとも幅のある窓も）。`(記録の列, 符丁の集合)`。読めない控えは `site_visits_unreadable`。"""
    cannot = set()
    try:
        directory = store_dir(project)
        entries = sorted(n for n in os.listdir(directory) if n.endswith(".json"))
    except (FileNotFoundError, SiteVisitsError):
        return [], cannot
    except OSError:
        return [], {"site_visits_unreadable"}
    out = []
    for entry in entries:
        try:
            with open(os.path.join(directory, entry), encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, UnicodeDecodeError, ValueError):
            cannot.add("site_visits_unreadable")
            continue
        if not isinstance(record, dict) or record.get("schema") != STORE_SCHEMA:
            cannot.add("site_visits_unreadable")
            continue
        out.append(record)
    return out, cannot


def _latest_wide(records) -> list:
    """幅のある窓の控え（5.7.0 の形）のうちサイトごとに、数の取れた最新の窓（`window.to`・同じなら `generated_at`）。

    監査 N-3（11/03）: 最新の窓が「取れなかった窓」（rows が null）でも、前の取れた窓の数を隠さない。
    取れた控えがあればそれを返し、それより新しい取れなかった窓を `newer_unavailable`（窓・generated_at・
    理由）として添える（数は前の窓のもの・0 と混ぜない）。取れた控えが 1 つも無ければ最新の取れなかった窓。
    """
    latest, good = {}, {}
    for record in records:
        key = (record.get("window") or {}).get("to") or "", record.get("generated_at") or ""
        site = record.get("site")
        if site not in latest or key > latest[site][0]:
            latest[site] = (key, record)
        if record.get("rows") is not None and (site not in good or key > good[site][0]):
            good[site] = (key, record)
    out = []
    for site in sorted(latest):
        key, record = latest[site]
        if record.get("rows") is None and site in good:
            shown = dict(good[site][1])
            shown["newer_unavailable"] = {"window": record.get("window"), "generated_at": record.get("generated_at"),
                                          "unknown_reason": record.get("unknown_reason") or "unknown"}
            out.append(shown)
        else:
            out.append(record)
    return out


def load(project) -> tuple:
    """幅のある窓の控えのうちサイトごとの最新（`_latest_wide`）。`(記録の列, 符丁の集合)`。

    5.11.0 段 8: 日ごとの控え（`window.from == window.to`）はここに入れない（`daily_index` で日ごとに足す）。
    """
    records, cannot = read_all(project)
    return _latest_wide([r for r in records if not is_daily(r)]), cannot


def as_of(project):
    """いちばん新しい控えの `generated_at`（日ごとの控えも入れる・無ければ None）。"""
    records, _ = read_all(project)
    return max((r.get("generated_at") for r in records if r.get("generated_at")), default=None)


# ------------------------------------------------------------------ 日ごとの控え（5.11.0 段 8）
#
# hub は日ごとの窓（終わり D-2）を 1 サイト 1 日 1 ファイルで送る（設計 §7.4・masaru 11/03）。THTH は
# 糸の期間（seeds は `--days` の窓）に重なる日の控えだけを**足す**。
#
#   - 同じサイトの同じ日の控えが複数あれば、`generated_at` の新しい 1 つだけ（二重に足さない）。
#   - 期間のうち控えの無い日は `days_missing`（数だけ・0 と混ぜない）。D-2 より後の日は「まだ来ない日」として
#     `days_pending` に分ける。取れなかった日（rows が null）は `days_unavailable`。
#   - 幅のある窓（7 日など・5.7.0 の形）との混ざり: 期間に重なる日ごとの控えが 1 つでもあるサイトは日ごとだけを
#     足し、期間に重なる幅のある控えは使わず `mixed_windows` に数を言う（幅のある窓は日に割れないので、
#     一部の日だけ差し替えると二重に足すか欠けを隠すことになる）。日ごとの控えが期間に 1 つも無いサイトは、
#     今までどおり幅のある窓の最新を読む（`window_basis: latest_window`）。

LAG_DAYS = 2
BASIS_THREAD_DAILY = "thread_period_daily"
BASIS_SEEDS_DAILY = "seeds_days_daily"
BASIS_RECENT_DAILY = "recent_days_daily"
BASIS_LATEST = "latest_window"
REFERRERS_DAYS = 28
MAX_PERIOD_DAYS = 800


def is_daily(record) -> bool:
    window = record.get("window") or {}
    return bool(window.get("from")) and window.get("from") == window.get("to")


def _stamp(record):
    return record.get("generated_at") or "", record.get("imported_at") or ""


def daily_index(records) -> tuple:
    """日ごとの控え → `({site: {日: 控え}}, {site: 落とした重複の数})`。同じ日は `generated_at` の新しい 1 つ。"""
    by_site, dropped = {}, {}
    for record in records:
        if not is_daily(record):
            continue
        site, day = record.get("site"), record["window"]["to"]
        days = by_site.setdefault(site, {})
        if day in days:
            dropped[site] = dropped.get(site, 0) + 1
            if _stamp(record) <= _stamp(days[day]):
                continue
        days[day] = record
    return by_site, dropped


def last_due_day(now) -> datetime.date:
    """hub が送ってくるはずの最後の日（D-2）。これより後の日は「まだ来ない日」。"""
    return jst.to_jst(now).date() - datetime.timedelta(days=LAG_DAYS)


def daily_span(days, start, end, now, key="rows") -> dict:
    """`start`〜`end`（日・両端を含む）の日ごとの控え。`records` は数の取れた日の控え（日の順）。"""
    due = last_due_day(now)
    used, missing, pending, unavailable, total = [], 0, 0, 0, 0
    if start is not None and end is not None and start <= end:
        start = max(start, end - datetime.timedelta(days=MAX_PERIOD_DAYS - 1))
        day = start
        while day <= end:
            total += 1
            record = days.get(day.isoformat())
            if record is not None:
                if record.get(key) is None:
                    unavailable += 1
                else:
                    used.append(record)
            elif day > due:
                pending += 1
            else:
                missing += 1
            day += datetime.timedelta(days=1)
    return {"records": used, "days_in_period": total, "days_covered": len(used), "days_missing": missing,
            "days_pending": pending, "days_unavailable": unavailable}


def _overlaps(record, start, end) -> bool:
    window = record.get("window") or {}
    try:
        lo, hi = datetime.date.fromisoformat(window.get("from")), datetime.date.fromisoformat(window.get("to"))
    except (TypeError, ValueError):
        return False
    return start is not None and end is not None and lo <= end and hi >= start


def plan_sites(records, start, end, now, key="rows") -> list:
    """サイトごとに、日ごとに足すか（`daily`）幅のある窓の最新を読むか（`wide`）を決める。

    戻り値の 1 行: `{"site", "mode": "daily"|"wide", "span"（daily のとき）, "record"（wide のとき）,
    "mixed_windows"（期間に重なるのに使わなかった幅のある控えの数・0 なら None）, "duplicates_dropped"}`。
    """
    daily, dropped = daily_index(records)
    wide_by_site = {}
    for record in records:
        if not is_daily(record):
            wide_by_site.setdefault(record.get("site"), []).append(record)
    latest_wide = {r.get("site"): r for r in _latest_wide([r for rs in wide_by_site.values() for r in rs])}
    out = []
    for site in sorted(set(daily) | set(wide_by_site), key=lambda s: s or ""):
        span = daily_span(daily.get(site, {}), start, end, now, key)
        has_daily = (span["days_covered"] + span["days_unavailable"]) > 0
        if has_daily or site not in latest_wide:
            mixed = sum(1 for r in wide_by_site.get(site, []) if _overlaps(r, start, end))
            out.append({"site": site, "mode": "daily", "span": span, "record": None,
                        "mixed_windows": {"wide_ignored": mixed} if mixed and has_daily else None,
                        "duplicates_dropped": dropped.get(site, 0)})
        else:
            out.append({"site": site, "mode": "wide", "span": None, "record": latest_wide[site],
                        "mixed_windows": None, "duplicates_dropped": dropped.get(site, 0)})
    return out


def thread_period(ledger, thread_id, status=None) -> dict:
    """糸の期間（日）: `started`（無ければ最初の動き＝宣言の出来事と糸の原稿の出た時刻の早い方）から、
    閉じた日（`closed`）か済んだ日（`done` の最後の動き）、どちらでもなければ今日まで。"""
    from . import scenario
    today = jst.to_jst(ledger.now).date()
    decl = next((d for d in scenario.usable_declarations(ledger) if d.id == thread_id and d.kind == "thread"), None)
    period = {"from": None, "to": today.isoformat(), "from_basis": None, "to_basis": "today"}
    if decl is None:
        return period
    started = scenario._date_at(scenario._plain(decl.fm.get("started")))
    if started is not None:
        period.update({"from": started.date().isoformat(), "from_basis": "started"})
    else:
        moments = [scenario._date_at(e.get("at")) for e in decl.events]
        for item in ledger.items:
            if item.thread == thread_id and scenario._is_posted(item):
                moments.append(item.posted_at or item.publish_at)
        moments = [m for m in moments if m is not None and m <= ledger.now]
        if moments:
            period.update({"from": jst.to_jst(min(moments)).date().isoformat(), "from_basis": "first_movement"})
    closed = [scenario._date_at(e.get("at")) for e in decl.events if e.get("name") == "closed"]
    closed = [c for c in closed if c is not None and c <= ledger.now]
    if closed:
        period.update({"to": max(closed).date().isoformat(), "to_basis": "closed"})
        return period
    status = status if status is not None else scenario.thread_status(decl, ledger)
    since = jst.parse((status.get("done") or {}).get("since")) if status.get("state") == "done" else None
    if since is not None:
        period.update({"to": jst.to_jst(since).date().isoformat(), "to_basis": "done"})
    return period


def _day(value):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _span_keys(span) -> dict:
    return {k: span[k] for k in ("days_in_period", "days_covered", "days_missing", "days_pending",
                                 "days_unavailable")}


def campaign_keys(rel) -> set:
    """原稿 1 本が名乗れる campaign の値: ファイル名（拡張子なし）と、repo の相対パスの末尾を `-` でつないだ形。

    `docs/sns/threads/drafts/features-2026-11-02.md` → `features-2026-11-02`・`drafts-features-2026-11-02`・
    `threads-drafts-features-2026-11-02`・…（MMR の形）。小文字で比べる。
    """
    if not isinstance(rel, str) or not rel:
        return set()
    stem = rel[:-3] if rel.endswith(".md") else rel
    parts = [p for p in stem.replace("\\", "/").split("/") if p]
    return {"-".join(parts[i:]).lower() for i in range(len(parts))}


def _index(items) -> dict:
    out = {}
    for item in items:
        for key in campaign_keys(getattr(item, "rel", None) or item.file):
            out.setdefault(key, []).append(item)
    return out


def _is_campaign(value) -> bool:
    text = (value or "").strip()
    return bool(text) and not text.startswith("(")


def tie(campaign, index):
    """campaign → `(糸の id か None, 原稿, 未結の理由か None)`。"""
    if not _is_campaign(campaign):
        return None, None, "not_a_campaign"
    found = index.get(campaign.strip().lower())
    if not found:
        return None, None, "no_manuscript"
    threads = {item.thread for item in found if item.thread}
    # 2 本の糸にまたがる・同じ名前の原稿の一部にだけ札がある（監査 N-2: 札の無い口座の訪問まで糸に足さない）は
    # どちらも結ばない（ambiguous）。
    if len(threads) > 1 or (threads and any(not item.thread for item in found)):
        return None, None, "ambiguous"
    if not threads:
        return None, found[0], "no_thread"
    thread = next(iter(threads))
    item = next(item for item in found if item.thread == thread)
    return thread, item, None


def tie_rows(record, items) -> dict:
    """1 つの控えの行を糸に結ぶ。`{"by_thread": {id: [行]}, "untied_campaigns": {...}, "tied_summary": {...}}`。"""
    index = _index(items)
    by_thread, untied = {}, {}
    untied_sessions, untied_unknown = 0, False
    for row in record.get("rows") or []:
        thread, item, reason = tie(row.get("campaign"), index)
        if thread is None:
            name = (row.get("campaign") or "").strip().lower()
            untied.setdefault(name, reason)
            if isinstance(row.get("sessions"), int):
                untied_sessions += row["sessions"]
            else:
                untied_unknown = True
            continue
        by_thread.setdefault(thread, []).append(dict(row, file=item.file))
    by_reason = {r: 0 for r in UNTIED_REASONS}
    for reason in untied.values():
        by_reason[reason] += 1
    rows = record.get("rows")
    tied_campaigns = {r["campaign"].strip().lower() for rs in by_thread.values() for r in rs}
    return {"by_thread": by_thread,
            "untied_campaigns": None if rows is None else {
                "campaigns": len(untied), "sessions": None if untied_unknown else untied_sessions,
                "by_reason": by_reason},
            "tied_summary": None if rows is None else {
                "campaigns": len(tied_campaigns), "threads": len(by_thread),
                "campaigns_total": len(tied_campaigns) + len(untied)}}


# 5.11.0 段 7: サイトへの参照元（`referrers`）を project の dashboard にサイトの数として（糸には結ばない）。
REFERRERS_LABEL = "サイトへの参照元（GA4・hub 経由・サイトの数・糸には結ばない）"
REFERRERS_MAX = 20
_HOST_RE = re.compile(r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*")


def referrer_host(raw):
    """参照元の値をホスト名だけに整える（scheme・`user@`・path・`?`・`#`・port を落とす・小文字）。
    ホスト名の形に合わなければ None（捨てて数だけ数える）。"""
    if not isinstance(raw, str):
        return None
    text = raw.strip().lower()
    if "://" in text:
        text = text.split("://", 1)[1]
    elif text.startswith("//"):
        text = text[2:]
    for mark in ("/", "?", "#", "\\"):
        text = text.split(mark, 1)[0]
    text = text.rsplit("@", 1)[-1]
    if text.startswith("["):
        return None   # IP の文字列（IPv6）はホスト名として出さない
    text = text.split(":", 1)[0].rstrip(".")
    if not text or not _HOST_RE.fullmatch(text):
        return None
    return text


def referrers_block(project, records=None, cannot=None) -> dict:
    """project の「サイトへの参照元」: サイトごとに host と sessions だけ（本文・URL の path・username は持たない）。

    同じ host に整った行は足す。形の合わない値（`(direct)`・空・path だけ等）は捨てて、行数と sessions だけ
    `dropped` に数える。取れなかった窓は `cannot_say: referrers_unavailable`（0 と混ぜない）。

    5.11.0 段 8: 日ごとの控えを足すのは呼ぶ側（`site_referrers`）。ここは渡された控えを 1 つずつ読むだけ。
    """
    if records is None:
        records, cannot = load(project)
    cannot = set(cannot or ())
    block = {"label": REFERRERS_LABEL, "source": SOURCE, "as_of": None, "sites": [], "cannot_say": None}
    if not records:
        block["cannot_say"] = "site_visits_unreadable" if cannot else "site_visits_not_imported"
        return block
    block["as_of"] = max((r.get("generated_at") for r in records if r.get("generated_at")), default=None)
    for record in records:
        site = {"site": record.get("site"), "window": record.get("window"),
                "generated_at": record.get("generated_at"), "referrers": [],
                "dropped": {"rows": 0, "sessions": 0}, "stale": bool(record.get("newer_unavailable")),
                "cannot_say": None}
        refs = record.get("referrers")
        if refs is None:
            site["cannot_say"] = "referrers_unavailable"
            block["sites"].append(site)
            continue
        by_host, unknown = {}, False
        for row in refs:
            if not isinstance(row, dict):
                site["dropped"]["rows"] += 1
                continue
            host = referrer_host(row.get("host"))
            sessions = row.get("sessions")
            count = sessions if isinstance(sessions, int) and not isinstance(sessions, bool) else None
            if host is None:
                site["dropped"]["rows"] += 1
                if count is None:
                    unknown = True
                else:
                    site["dropped"]["sessions"] += count
                continue
            node = by_host.setdefault(host, {"host": host, "sessions": 0})
            node["sessions"] = None if count is None or node["sessions"] is None else node["sessions"] + count
        if unknown:
            site["dropped"]["sessions"] = None
        ranked = sorted(by_host.values(), key=lambda n: n["host"])
        ranked.sort(key=lambda n: -1 if n["sessions"] is None else n["sessions"], reverse=True)
        site["referrers"] = ranked[:REFERRERS_MAX]
        site["more"] = max(0, len(ranked) - REFERRERS_MAX)
        if not ranked:
            site["cannot_say"] = "no_referrers"
        block["sites"].append(site)
    if cannot:
        block["read_problems"] = sorted(cannot)
    return block


def site_referrers(project, *, now=None) -> dict:
    """dashboard の「サイトへの参照元」（5.11.0 段 8）。日ごとの足し算はここで組み、`referrers_block` に渡す。

    日ごとの控えのあるサイトは、ここ `REFERRERS_DAYS` 日（終わりは D-2・それより新しい日の控えがあればその日）の
    日ごとの控えの `referrers` を 1 つの控えにつないで渡す（host ごとの足し算は `referrers_block` がする・
    `window_basis: recent_days_daily`・`days_missing` など）。日ごとの控えが無いサイトは今までどおり幅のある窓の
    最新（`window_basis: latest_window`）。
    """
    now = now if now is not None else jst.now_jst()
    records, cannot = read_all(project)
    if not records:
        return referrers_block(project, [], cannot)
    daily, _dropped = daily_index(records)
    latest_day = max((_day(d) for days in daily.values() for d in days), default=None)
    end = max(d for d in (last_due_day(now), latest_day) if d is not None)
    start = end - datetime.timedelta(days=REFERRERS_DAYS - 1)
    plans = plan_sites(records, start, end, now, key="referrers")
    joined = []
    for plan in plans:
        if plan["mode"] == "wide":
            joined.append(plan["record"])
            continue
        used = plan["span"]["records"]
        joined.append({"site": plan["site"], "window": {"from": start.isoformat(), "to": end.isoformat()},
                       "generated_at": max((r.get("generated_at") for r in used if r.get("generated_at")),
                                           default=None),
                       "referrers": [row for r in used for row in r["referrers"]] if used else None})
    block = referrers_block(project, joined, cannot)
    for plan, site in zip(plans, block["sites"]):
        if plan["mode"] == "wide":
            site["window_basis"] = BASIS_LATEST
            continue
        span = plan["span"]
        site["window_basis"] = BASIS_RECENT_DAILY
        site.update(_span_keys(span))
        if plan["mixed_windows"]:
            site["mixed_windows"] = plan["mixed_windows"]
        if not span["records"] and not span["days_unavailable"]:
            site["cannot_say"] = "no_daily_in_window"   # 日ごとの控えはあるが、この窓には 1 日も無い
    return block


def _sum(rows, key):
    values = [r.get(key) for r in rows]
    if not values or any(not isinstance(v, int) for v in values):
        return None
    return sum(values)


CAMPAIGN_KEYS = ("campaign", "file", "source", "medium", "sessions", "engaged_sessions", "avg_duration_s",
                 "landing_page")


def _merge_campaigns(rows) -> list:
    """日ごとの行を、同じ campaign・原稿・source・medium・着地の頁ごとに足す（平均の滞在は sessions で重みづけ・
    重みの取れない行があれば null）。"""
    merged = {}
    for r in rows:
        key = ((r.get("campaign") or "").strip().lower(), r.get("file"), r.get("source"), r.get("medium"),
               r.get("landing_page"))
        node = merged.setdefault(key, {"rows": [], "first": r})
        node["rows"].append(r)
    out = []
    for node in merged.values():
        group = node["rows"]
        row = {k: node["first"].get(k) for k in CAMPAIGN_KEYS}
        row["sessions"] = _sum(group, "sessions")
        row["engaged_sessions"] = _sum(group, "engaged_sessions")
        weights = [(r.get("avg_duration_s"), r.get("sessions")) for r in group]
        if len(group) == 1:
            row["avg_duration_s"] = group[0].get("avg_duration_s")
        elif all(isinstance(a, (int, float)) and isinstance(s, int) for a, s in weights) and row["sessions"]:
            row["avg_duration_s"] = round(sum(a * s for a, s in weights) / row["sessions"], 1)
        else:
            row["avg_duration_s"] = None
        out.append(row)
    return out


def _fill_site(site, rows, *, merge=False):
    """糸に結べた行から、サイトの sessions・engaged・媒体ごと・campaign の列を埋める。足せたら True。"""
    picked = _merge_campaigns(rows) if merge else [{k: r.get(k) for k in CAMPAIGN_KEYS} for r in rows]
    site["campaigns"] = sorted(picked, key=lambda r: (r.get("file") or "", r.get("source") or "",
                                                      r.get("medium") or ""))
    if not rows:
        site["cannot_say"] = "no_tied_campaign"
        return False
    site["sessions"] = _sum(rows, "sessions")
    site["engaged_sessions"] = _sum(rows, "engaged_sessions")
    if site["sessions"] is None:
        site["cannot_say"] = "sessions_partly_unknown"
    for r in rows:
        medium = (r.get("medium") or "(none)").strip().lower() or "(none)"
        if isinstance(r.get("sessions"), int):
            site["by_medium"][medium] = site["by_medium"].get(medium, 0) + r["sessions"]
    return True


def thread_block(project, ledger, thread_id, records=None, cannot=None, *, period=None, status=None) -> dict:
    """糸 1 本の「本記事への訪問」。控えが無ければ `cannot_say: site_visits_not_imported`（0 と混ぜない）。

    5.11.0 段 8: 日ごとの控えのあるサイトは、糸の期間（`thread_period`）に重なる日の控えを足す
    （`window_basis: thread_period_daily`）。日ごとの控えが期間に無いサイトは今までどおり幅のある窓の最新
    （`window_basis: latest_window`）。`records` は `read_all` の形（全部の控え）。
    """
    if records is None:
        records, cannot = read_all(project)
    cannot = set(cannot or ())
    block = {"label": LABEL, "source": SOURCE, "as_of": None, "sessions": None, "engaged_sessions": None,
             "sites": [], "cannot_say": None}
    if not records:
        block["cannot_say"] = "site_visits_unreadable" if cannot else "site_visits_not_imported"
        return block
    block["as_of"] = max((r.get("generated_at") for r in records if r.get("generated_at")), default=None)
    period = period if period is not None else thread_period(ledger, thread_id, status)
    start, end = _day(period.get("from")), _day(period.get("to"))
    totals, windows, bases = [], set(), set()
    for plan in plan_sites(records, start, end, ledger.now):
        if plan["mode"] == "daily":
            span = plan["span"]
            used = span["records"]
            site = {"site": plan["site"], "window": {"from": period.get("from"), "to": period.get("to")},
                    "window_basis": BASIS_THREAD_DAILY, **_span_keys(span),
                    "generated_at": max((r.get("generated_at") for r in used if r.get("generated_at")), default=None),
                    "imported_at": max((r.get("imported_at") for r in used if r.get("imported_at")), default=None),
                    "sessions": None, "engaged_sessions": None, "by_medium": {}, "campaigns": [],
                    "rows_total": None, "untied_campaigns": None, "cannot_say": None}
            bases.add(BASIS_THREAD_DAILY)
            if plan["mixed_windows"]:
                site["mixed_windows"] = plan["mixed_windows"]
                block.setdefault("mixed_windows", []).append(plan["site"])
            if not used:
                if start is None:
                    site["cannot_say"] = "thread_period_unknown"
                elif span["days_unavailable"]:
                    site["cannot_say"] = "site_visits_unavailable"
                    site["unknown_reason"] = "daily_unavailable"
                else:
                    site["cannot_say"] = "no_daily_in_period"
                block["sites"].append(site)
                continue
            merged = {"rows": [row for r in used for row in r["rows"]]}
            tied = tie_rows(merged, ledger.items)
            site["rows_total"] = len(merged["rows"])
            site["untied_campaigns"] = tied["untied_campaigns"]
            if _fill_site(site, tied["by_thread"].get(thread_id) or [], merge=True):
                totals.append(site)
                windows.add(f"daily:{period.get('from')}/{period.get('to')}")
                if span["days_missing"] or span["days_unavailable"]:
                    block.setdefault("partial_sites", []).append(plan["site"])
            block["sites"].append(site)
            continue
        record = plan["record"]
        site = {"site": record.get("site"), "window": record.get("window"), "window_basis": BASIS_LATEST,
                "generated_at": record.get("generated_at"), "imported_at": record.get("imported_at"),
                "sessions": None, "engaged_sessions": None, "by_medium": {}, "campaigns": [],
                "rows_total": None, "untied_campaigns": None, "cannot_say": None}
        bases.add(BASIS_LATEST)
        if record.get("newer_unavailable"):
            # 新しい窓は取れなかった。数はその前の取れた窓のもの（注記して添える）。
            site["stale"] = True
            site["newer_unavailable"] = record["newer_unavailable"]
            block.setdefault("stale_sites", []).append(record.get("site"))
        if record.get("rows") is None:
            site["cannot_say"] = "site_visits_unavailable"
            site["unknown_reason"] = record.get("unknown_reason") or "unknown"
            block["sites"].append(site)
            continue
        tied = tie_rows(record, ledger.items)
        site["rows_total"] = len(record["rows"])
        site["untied_campaigns"] = tied["untied_campaigns"]
        if _fill_site(site, tied["by_thread"].get(thread_id) or []):
            totals.append(site)
            windows.add((site["window"] or {}).get("from", "") + "/" + (site["window"] or {}).get("to", ""))
        block["sites"].append(site)
    block["window_basis"] = next(iter(bases)) if len(bases) == 1 else "mixed"
    block["period"] = dict(period)
    if not totals:
        codes = {s["cannot_say"] for s in block["sites"]}
        if codes == {"site_visits_unavailable"}:
            block["cannot_say"] = "site_visits_unavailable"
        elif codes in ({"no_daily_in_period"}, {"thread_period_unknown"}):
            block["cannot_say"] = next(iter(codes))
        else:
            block["cannot_say"] = "no_tied_campaign"
    elif len(windows) > 1:
        block["cannot_say"] = "windows_differ"   # 窓の違うサイトは足さない（サイトごとに見る）
    elif any(s["sessions"] is None for s in totals):
        block["cannot_say"] = "sessions_partly_unknown"
    else:
        block["sessions"] = sum(s["sessions"] for s in totals)
        engaged = [s["engaged_sessions"] for s in totals]
        block["engaged_sessions"] = sum(engaged) if all(isinstance(v, int) for v in engaged) else None
    if cannot:
        block["read_problems"] = sorted(cannot)
    return block
