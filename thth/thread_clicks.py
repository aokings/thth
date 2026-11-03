"""糸と seeds とプラザに出す「Threads のリンクのクリック」（5.11.0 段 7・設計 効果測定と連動 §7.2「訪問が来ない間」）。

**読むだけ。** 1 本の数は `click_attribution.Index.for_account(...).attribute(post_id, posted)` の 1 か所
（`thth map`・observe と同じ計算・`basis: unique_url_72h`）。ここは糸の原稿に当てて数え、分母を添えるだけ。

規律:

  - GA4 の訪問（`site_visits`）とは**別の欄**。足さない・混ぜない（出所が違う: こちらは THTH が Threads から
    採ったリンク先ごとの日次クリック、あちらは hub 経由の GA4）。
  - 数に入れるのは `basis` があり `clicks_72h` が null でない投稿だけ。言えない投稿は理由ごとの本数
    （`reasons`・`click_attribution` の符丁と、`post_id_missing`・`posted_at_missing`・`measured_unreadable`）。
  - 連投は先頭の `post_id` で数える（段の本数・反応と同じ分母）。
  - Threads 以外の媒体は投稿ごとのクリックを持たない: `clicks_72h: null` と `per_post_clicks_unavailable:<媒体>`。
  - 分母は「クリックを言える本数（`n`）／糸の Threads の本数（`of`）」。
"""
from __future__ import annotations

LABEL = "Threads のリンクのクリック（THTH の実測・投稿から 72h）"
SOURCE = "threads_clicks_by_url"
MEDIUM = "threads"
UNAVAILABLE = "per_post_clicks_unavailable"
DENOMINATOR = "クリックを言える本数（n）／糸の Threads の本数（of）"
SEPARATE_FROM = "site_visits（GA4・hub 経由）とは別の欄・足さない"


def unavailable_code(medium) -> str:
    return f"{UNAVAILABLE}:{medium}"


def _head_post_id(item):
    return next((pid for pid in item.post_ids if isinstance(pid, str) and pid), None)


class Clicks:
    """project の台帳 1 つぶんの材料（account ごとに 1 回だけ実測を読む）。"""

    def __init__(self, ledger, now):
        self.ledger = ledger
        self.now = now
        self._cache = {}

    def _material(self, account, extra):
        if account in self._cache:
            return self._cache[account]
        from . import analytics_comparison as comparison, click_attribution, measured
        try:
            loaded = measured.load(account)
            posted = {str(p["post_id"]): comparison._timestamp(p.get("posted_at"))
                      for p in loaded["posts"]}
            posts = list(posted.items())
            # 台帳にまだ無い糸の投稿も、近くの投稿との照合に入れる（Index は重複を落とす）。
            posts += [(pid, at) for pid, at in extra if pid not in posted]
            index = click_attribution.Index.for_account(
                account, self.ledger.accounts.get(account) or {}, posts=posts,
                account_daily=loaded.get("account_daily"), now=self.now)
            material = (index, posted)
        except Exception:  # noqa: BLE001 — 読めない実測は数えない（0 と混ぜない）
            material = None
        self._cache[account] = material
        return material

    def row(self, item, extra=()):
        """原稿 1 本の行（`clicks_72h` は言えるときだけ数・言えなければ null と `cannot_say`）。"""
        out = {"file": item.file, "account": item.account, "medium": item.medium, "thread": item.thread,
               "posted_at": None, "clicks_72h": None, "window_days": None, "cannot_say": None}
        if item.medium != MEDIUM:
            out["cannot_say"] = unavailable_code(item.medium)
            return out
        post_id = _head_post_id(item)
        if post_id is None:
            out["cannot_say"] = "post_id_missing"
            return out
        material = self._material(item.account, extra)
        if material is None:
            out["cannot_say"] = "measured_unreadable"
            return out
        index, posted_by_id = material
        posted = posted_by_id.get(post_id) or item.posted_at
        if posted is None:
            out["cannot_say"] = "posted_at_missing"
            return out
        from . import jst
        out["posted_at"] = jst.iso(posted)
        result = index.attribute(post_id, posted)
        out["window_days"] = result.get("window_days")
        if result.get("basis") and result.get("clicks_72h") is not None:
            out["clicks_72h"] = result["clicks_72h"]
        else:
            out["cannot_say"] = result.get("cannot_say") or result.get("clicks_missing") or "clicks_unavailable"
        return out


def _extra(items):
    """糸の Threads の投稿（先頭の post_id と時刻）——近くの投稿との照合に足す分。"""
    out = []
    for item in items:
        pid = _head_post_id(item)
        if item.medium == MEDIUM and pid and item.posted_at is not None:
            out.append((pid, item.posted_at))
    return out


def summarize(rows) -> dict:
    """行の列を、Threads の和と分母・理由ごとの本数・Threads 以外の媒体の null に畳む（本文・URL は持たない）。"""
    threads = [r for r in rows if r["medium"] == MEDIUM]
    said = [r["clicks_72h"] for r in threads if r["clicks_72h"] is not None]
    reasons = {}
    for row in threads:
        if row["clicks_72h"] is None:
            reasons[row["cannot_say"]] = reasons.get(row["cannot_say"], 0) + 1
    others = {}
    for row in rows:
        if row["medium"] != MEDIUM:
            # 媒体の節は媒体名が鍵なので、理由は媒体名を付けない符丁（型の理由の表で引ける形）。
            # 媒体名つきの `per_post_clicks_unavailable:<媒体>` は塊の `cannot_say` の列に出す。
            node = others.setdefault(row["medium"], {"posts": 0, "clicks_72h": None, "cannot_say": UNAVAILABLE})
            node["posts"] += 1
    cannot = sorted(unavailable_code(medium) for medium in others)
    if not threads:
        cannot.insert(0, "no_threads_posts")
    elif not said:
        cannot.insert(0, "no_attributable_post")
    from . import click_attribution
    return {"label": LABEL, "source": SOURCE, "medium": MEDIUM, "basis": click_attribution.BASIS,
            "window": click_attribution.WINDOW, "window_note": click_attribution.WINDOW_NOTE,
            "separate_from": SEPARATE_FROM,
            "clicks_72h": sum(said) if said else None, "n": len(said), "of": len(threads),
            "denominator": DENOMINATOR,
            "reasons": dict(sorted(reasons.items())),
            "other_media": dict(sorted(others.items())),
            "cannot_say": cannot}


def thread_block(ledger, thread_id, *, with_posts=True) -> dict:
    """糸 1 本の「Threads のリンクのクリック」。`with_posts` は投稿ごとの行（原稿の名前と数）も添える。"""
    from . import scenario
    items = [item for item in ledger.items if item.thread == thread_id
             and item.medium in scenario.SNS_MEDIA and scenario._is_posted(item)]
    clicks = Clicks(ledger, ledger.now)
    extra = _extra(items)
    rows = [clicks.row(item, extra) for item in sorted(items, key=lambda i: (i.posted_at or ledger.now, i.file))]
    block = summarize(rows)
    if with_posts:
        block["posts"] = [{key: row[key] for key in ("file", "account", "posted_at", "clicks_72h", "window_days",
                                                     "cannot_say")}
                          for row in rows if row["medium"] == MEDIUM]
    return block
