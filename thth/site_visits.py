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

def load(project) -> tuple:
    """控えのうちサイトごとに最新の窓（`window.to`・同じなら `generated_at`）。`(記録の列, 符丁の集合)`。"""
    cannot = set()
    try:
        directory = store_dir(project)
        entries = sorted(n for n in os.listdir(directory) if n.endswith(".json"))
    except (FileNotFoundError, SiteVisitsError):
        return [], cannot
    except OSError:
        return [], {"site_visits_unreadable"}
    latest = {}
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
        key = (record.get("window") or {}).get("to") or "", record.get("generated_at") or ""
        site = record.get("site")
        if site not in latest or key > latest[site][0]:
            latest[site] = (key, record)
    return [latest[s][1] for s in sorted(latest)], cannot


def as_of(project):
    """いちばん新しい控えの `generated_at`（無ければ None）。"""
    records, _ = load(project)
    return max((r.get("generated_at") for r in records if r.get("generated_at")), default=None)


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
    if len(threads) > 1:
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


def _sum(rows, key):
    values = [r.get(key) for r in rows]
    if not values or any(not isinstance(v, int) for v in values):
        return None
    return sum(values)


def thread_block(project, ledger, thread_id, records=None, cannot=None) -> dict:
    """糸 1 本の「本記事への訪問」。控えが無ければ `cannot_say: site_visits_not_imported`（0 と混ぜない）。"""
    if records is None:
        records, cannot = load(project)
    cannot = set(cannot or ())
    block = {"label": LABEL, "source": SOURCE, "as_of": None, "sessions": None, "engaged_sessions": None,
             "sites": [], "cannot_say": None}
    if not records:
        block["cannot_say"] = "site_visits_unreadable" if cannot else "site_visits_not_imported"
        return block
    block["as_of"] = max((r.get("generated_at") for r in records if r.get("generated_at")), default=None)
    totals, windows = [], set()
    for record in records:
        site = {"site": record.get("site"), "window": record.get("window"),
                "generated_at": record.get("generated_at"), "imported_at": record.get("imported_at"),
                "sessions": None, "engaged_sessions": None, "by_medium": {}, "campaigns": [],
                "rows_total": None, "untied_campaigns": None, "cannot_say": None}
        if record.get("rows") is None:
            site["cannot_say"] = "site_visits_unavailable"
            site["unknown_reason"] = record.get("unknown_reason") or "unknown"
            block["sites"].append(site)
            continue
        tied = tie_rows(record, ledger.items)
        rows = tied["by_thread"].get(thread_id) or []
        site["rows_total"] = len(record["rows"])
        site["untied_campaigns"] = tied["untied_campaigns"]
        site["campaigns"] = [{k: r.get(k) for k in ("campaign", "file", "source", "medium", "sessions",
                                                      "engaged_sessions", "avg_duration_s", "landing_page")}
                             for r in sorted(rows, key=lambda r: (r.get("file") or "", r.get("source") or "",
                                                                    r.get("medium") or ""))]
        if not rows:
            site["cannot_say"] = "no_tied_campaign"
        else:
            site["sessions"] = _sum(rows, "sessions")
            site["engaged_sessions"] = _sum(rows, "engaged_sessions")
            if site["sessions"] is None:
                site["cannot_say"] = "sessions_partly_unknown"
            for r in rows:
                medium = (r.get("medium") or "(none)").strip().lower() or "(none)"
                if isinstance(r.get("sessions"), int):
                    site["by_medium"][medium] = site["by_medium"].get(medium, 0) + r["sessions"]
            totals.append(site)
            windows.add((site["window"] or {}).get("from", "") + "/" + (site["window"] or {}).get("to", ""))
        block["sites"].append(site)
    if not totals:
        unavailable = all(s["cannot_say"] == "site_visits_unavailable" for s in block["sites"])
        block["cannot_say"] = "site_visits_unavailable" if unavailable else "no_tied_campaign"
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
