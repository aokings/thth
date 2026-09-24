"""click を投稿単位で（設計 3.7.0 §A1・`basis: unique_url_72h`）。**読むだけ。**

Threads の account 日次 `clicks_by_url`（リンク先ごとの日次クリック）を使う。
**あるリンク先を、投稿の前後 72 時間で 1 本の投稿しか使っていないときだけ**、
そのリンク先の、投稿から 72 時間ぶんのクリックをその投稿のものとして出す。

規律:

  (a) **日次の粒度しか無い**。72 時間は「投稿日（JST）を含む 3 暦日」の和として
      数える（`window: post_day_plus_2`）。投稿が夜なら 72 時間より短く、朝なら
      長い——出力の `window_note` にそう書く。3 暦日がすべて閉じる（採取は閉じた
      日だけ記録する）までは値を出さない（`window_open`）。
  (b) **共有のリンク先ではクリックを出さない**（`url_shared_72h`）。同じリンク先を
      前後 72 時間に使う投稿が他にあれば、どの投稿が生んだクリックか決まらない
      （割らない・3.6.0 設計 D と同じ筋）。2 本の 3 暦日の窓が重なるのは投稿日の差が
      2 日以内のときで、そのとき時刻の差は必ず 72 時間未満——前後 72 時間で見れば
      窓の重なりも取りこぼさない。
  (c) **プロフィールのリンクと同じリンク先は通さない**（`profile_link`）。その
      リンク先の日次には、プロフィールから踏まれた分が混ざる。プロフィールの
      リンクは台帳の任意項目 `profile_links`（道具は推測しない）。
  (d) リンク先の照合は正規化してから: スキームを外す・末尾の `/` を外す・
      `utm_*` の引数を外す・大文字小文字を畳むのはホストだけ（パスは畳まない）。
  (e) 近くに**リンク先が記録に無い投稿**（段のリンク先を控える前の連投・THTH を
      通していない投稿・添付のリンク先を控える前の記録）があれば、共有でないとは言えない
      （`nearby_link_unrecorded`）。自分のリンク先が記録に無ければ
      `link_unrecorded`。
  (f) **本文に札を付けて書き換えない**（設計 E）。リンク先を投稿ごとに分ければ
      同じことが測れる——lint の知らせ（`lint_notes()`）がそれを促す。
"""
from __future__ import annotations

import datetime
import os
import re
import urllib.parse

from . import jst

BASIS = "unique_url_72h"
WINDOW = "post_day_plus_2"
WINDOW_DAYS = 3
WINDOW_NOTE = ("日次の粒度なので、72 時間は投稿日（JST）を含む 3 暦日の和"
               "（投稿の時刻によって 72 時間より短くも長くもなる）")
NEIGHBOR_HOURS = 72
RATE_BASIS = "clicks_72h / views_24h"
# 窓の前と後（設計 3.9.0 §A）。どちらも**窓の和には足さない**（参考の数）。
BEFORE_DAYS = 3
BEFORE_BASIS = ("投稿日より前の 3 暦日の、そのリンク先の日次クリック（同じリンク先を使う"
                "ほかの投稿の窓の日は数えない）")
AFTER_DAYS = 7
AFTER_BASIS = ("3 暦日の窓のあとの 7 暦日の、そのリンク先の日次クリック（参考・同じリンク先を"
               "使うほかの投稿の窓の日は数えない）")

# 投稿単位のクリックを言えない理由（静的な符丁・この 5 語だけ）。
URL_SHARED = "url_shared_72h"
NO_LINK = "no_link"
PROFILE_LINK = "profile_link"
LINK_UNRECORDED = "link_unrecorded"
NEARBY_LINK_UNRECORDED = "nearby_link_unrecorded"
CANNOT_SAY = (URL_SHARED, NO_LINK, PROFILE_LINK, LINK_UNRECORDED, NEARBY_LINK_UNRECORDED)
# 言える投稿でも値が無い理由（日次の側）。
WINDOW_OPEN = "window_open"
DAILY_MISSING = "daily_missing"
BY_URL_UNAVAILABLE = "clicks_by_url_unavailable"
# クリック率の分母が無い理由。
VIEWS_MISSING = "views_24h_missing"

# 本文の中の URL（ASCII の範囲だけ——「https://a.example/xを見て」の「を見て」を
# URL に含めない）。末尾の句読点・閉じ括弧は外す。
_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+")
_TRAILING = ".,!?;:)]}'"


def normalize_url(raw) -> str | None:
    """照合のための形（スキームなし・末尾 `/` なし・`utm_*` なし・ホストだけ小文字）。"""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parts = urllib.parse.urlsplit(raw.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    host = parts.netloc.lower()
    query = "&".join(item for item in parts.query.split("&")
                     if item and not item.split("=", 1)[0].lower().startswith("utm_"))
    path = parts.path.rstrip("/")
    out = host + path
    if query:
        out += "?" + query
    if parts.fragment:
        out += "#" + parts.fragment
    return out


def urls_in(text) -> list:
    """本文に書かれた URL（正規化の前・出た順・重複なし）。"""
    if not isinstance(text, str):
        return []
    out = []
    for match in _URL_RE.findall(text):
        value = match.rstrip(_TRAILING)
        if value and value not in out:
            out.append(value)
    return out


def manifest_links(manifest) -> list:
    """添付の manifest に書かれたリンク先（link の `url`・text 添付の `link`）。"""
    out = []
    for row in (manifest or {}).get("attachments") or ():
        if not isinstance(row, dict):
            continue
        value = row.get("url") if row.get("type") == "link" else (
            row.get("link") if row.get("type") == "text" else None)
        if isinstance(value, str) and value and value not in out:
            out.append(value)
    return out


def profile_links(cfg) -> set:
    """台帳の `profile_links`（正規化した形の集合）。無ければ空。"""
    raw = (cfg or {}).get("profile_links") or []
    if not isinstance(raw, list):
        return set()
    return {value for value in (normalize_url(item) for item in raw) if value}


def _day(at) -> datetime.date:
    return jst.to_jst(at).date()


def _neighbors(a, b) -> bool:
    """前後 72 時間か（3 暦日の窓の重なりはこれに含まれる・上の (b)）。"""
    return abs((a - b).total_seconds()) < NEIGHBOR_HOURS * 3600


# ------------------------------------------------------------ 記録から引く

def recorded_links(account_name: str) -> dict:
    """公開の時点の記録から、投稿ごとのリンク先 `{post_id: {"urls", "known"}}`。

    材料は 2 つ: `state/<account>/sent/`（送った本文そのものと、3.7.0 から控える
    添付のリンク先 `link_urls`）と、連投の実行記録（3.7.0 から段ごとに控える
    `link_urls`・裁定 09-24）。添付に link か text があるのに `link_urls` が無い sent
    （3.7.0 より前）は `known: False`。`link_urls` を持たない過去の連投の段はここに
    入らず、リンク先が記録に無い投稿として扱う（厳しい側のまま）。
    """
    from . import accounts, sent
    out = {}
    try:
        state_dir = accounts.state_dir_for(account_name)
    except accounts.AccountError:
        return out
    for row in sent.records(state_dir):
        urls = urls_in(row.get("text"))
        known = isinstance(row.get("text"), str)
        kinds = row.get("attachment_kinds") or []
        if isinstance(row.get("link_urls"), list):
            urls += [u for u in row["link_urls"] if isinstance(u, str) and u not in urls]
        elif "link" in kinds or "text" in kinds:
            # 添付のリンク先を控える前の記録——本文の URL だけでは足りない。
            known = False
        out[str(row["post_id"])] = {"urls": urls, "known": known}
    out.update(_thread_step_links(account_name))
    return out


def _thread_step_links(account_name: str) -> dict:
    """連投の実行記録の段ごとのリンク先（`link_urls` を持つ段だけ）。"""
    from . import threadrun
    out = {}
    try:
        names = sorted(n for n in os.listdir(threadrun.runs_dir()) if n.endswith(".json"))
    except OSError:
        return out
    for name in names:
        try:
            run = threadrun.load(name[:-len(".json")])
        except Exception:   # noqa: BLE001 — 読めない実行記録は公開の経路が止める
            continue
        if not isinstance(run, dict) or run.get("account") != account_name:
            continue
        for post in run.get("posts") or []:
            if (isinstance(post, dict) and post.get("post_id")
                    and isinstance(post.get("link_urls"), list)):
                out[str(post["post_id"])] = {
                    "urls": [u for u in post["link_urls"] if isinstance(u, str)],
                    "known": True}
    return out


def _day_index(account_daily) -> dict:
    out = {}
    for row in account_daily or []:
        if isinstance(row, dict) and isinstance(row.get("date"), str):
            out[row["date"]] = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    return out


def _by_url(metrics) -> dict | None:
    """その日の `clicks_by_url` を `{正規化した URL: クリック}` に（無ければ None）。

    採取は `[{link_url, value}]` の形で残す（adapter の `_link_values`）。`{url: value}`
    の形も読む。
    """
    raw = metrics.get("clicks_by_url")
    if isinstance(raw, list):
        rows = [(item.get("link_url"), item.get("value")) for item in raw if isinstance(item, dict)]
    elif isinstance(raw, dict):
        rows = list(raw.items())
    else:
        return None
    out = {}
    for url, value in rows:
        key = normalize_url(url)
        if key is None or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        out[key] = out.get(key, 0) + value
    return out


def _day_clicks(metrics, urls):
    """その日の、そのリンク先群のクリック（言えなければ `(None, 理由)`）。"""
    if metrics is None:
        return None, DAILY_MISSING
    by_url = _by_url(metrics)
    if by_url is not None:
        return sum(by_url.get(url, 0) for url in urls), None
    clicks = metrics.get("clicks")
    if isinstance(clicks, (int, float)) and not isinstance(clicks, bool) and clicks == 0:
        # その日は account 全体で 0——どのリンク先も 0。
        return 0, None
    return None, BY_URL_UNAVAILABLE


class Index:
    """1 account ぶんの材料（投稿の時刻・リンク先・日次・プロフィールのリンク）。"""

    def __init__(self, *, posts, links, account_daily, profile, now):
        # posts: [(post_id, posted_at)]——account の投稿の全部（根と返信を合わせて）。
        seen, rows = set(), []
        for pid, at in posts:
            if at is None or str(pid) in seen:
                continue
            seen.add(str(pid))
            rows.append((str(pid), at))
        self.posts = rows
        self.links = links
        self.daily = _day_index(account_daily)
        self.profile = set(profile or ())
        self.now = now

    @classmethod
    def for_account(cls, name, cfg, *, posts, account_daily, now):
        return cls(posts=posts, links=recorded_links(name), account_daily=account_daily,
                   profile=profile_links(cfg), now=now)

    def _urls(self, post_id):
        entry = self.links.get(str(post_id))
        if entry is None or not entry["known"]:
            return None
        return [value for value in (normalize_url(u) for u in entry["urls"]) if value]

    def attribute(self, post_id, posted, views_24h=None) -> dict:
        """1 本の投稿の、投稿単位のクリック（言えなければ `cannot_say`）。"""
        post_id = str(post_id)
        start = _day(posted)
        days = [(start + datetime.timedelta(days=i)).isoformat() for i in range(WINDOW_DAYS)]
        out = {"post_id": post_id, "basis": None, "window": WINDOW, "window_note": WINDOW_NOTE,
               "window_days": days, "urls": None, "clicks_72h": None, "clicks_missing": None,
               "views_24h": views_24h, "click_rate": None, "rate_basis": RATE_BASIS,
               "rate_missing": None, "cannot_say": None}
        mine = self._urls(post_id)
        if mine is None:
            out["cannot_say"] = LINK_UNRECORDED
            return out
        own = sorted(set(mine) - self.profile)
        if not own:
            out["cannot_say"] = PROFILE_LINK if mine else NO_LINK
            return out
        if set(mine) & self.profile:
            # 1 本の中にプロフィールのリンクも書いてある——そのリンク先の分は数えない。
            out["profile_link_excluded"] = True
        out["urls"] = own
        unknown_nearby = False
        for other_id, other_at in self.posts:
            if other_id == post_id or not _neighbors(posted, other_at):
                continue
            theirs = self._urls(other_id)
            if theirs is None:
                unknown_nearby = True
                continue
            if set(theirs) & set(own):
                out["cannot_say"] = URL_SHARED
                return out
        if unknown_nearby:
            out["cannot_say"] = NEARBY_LINK_UNRECORDED
            return out
        out["basis"] = BASIS
        closes = datetime.datetime.combine(start + datetime.timedelta(days=WINDOW_DAYS),
                                           datetime.time(0, 0), tzinfo=jst.JST)
        if self.now < closes:
            out["clicks_missing"] = WINDOW_OPEN
            return out
        total = 0
        for day in days:
            value, reason = _day_clicks(self.daily.get(day), own)
            if value is None:
                out["clicks_missing"] = reason
                return out
            total += value
        out["clicks_72h"] = total
        if isinstance(views_24h, (int, float)) and not isinstance(views_24h, bool) and views_24h > 0:
            out["click_rate"] = round(total / views_24h, 4)
        else:
            out["rate_missing"] = VIEWS_MISSING
        return out


    # ------------------------------------------------ 窓の前と後（設計 3.9.0 §A）

    def _other_windows(self, post_id, urls) -> set:
        """同じリンク先を使う**ほかの**投稿の 3 暦日の窓に入る日（その日は数えない）。

        72 時間より離れて同じリンク先を使った投稿があると、その投稿の窓のクリックが
        こちらの「前」や「後」に入って見える。その日は外して、外した日数を出す。
        """
        out = set()
        for other_id, other_at in self.posts:
            if other_id == str(post_id):
                continue
            theirs = self._urls(other_id)
            if not theirs or not set(theirs) & set(urls):
                continue
            first = _day(other_at)
            out.update((first + datetime.timedelta(days=i)).isoformat() for i in range(WINDOW_DAYS))
        return out

    def _sum_days(self, post_id, urls, days) -> dict:
        """日の並びの、そのリンク先のクリックの和。欠けた日・ほかの投稿の窓の日は数えない。"""
        today = jst.to_jst(self.now).date().isoformat()
        excluded = self._other_windows(post_id, urls)
        total, counted, skipped, missing, not_closed = 0, [], [], 0, 0
        for day in days:
            if day >= today:
                # 採取は閉じた日だけ記録する——まだ閉じていない日は数えない。
                not_closed += 1
                continue
            if day in excluded:
                skipped.append(day)
                continue
            value, _reason = _day_clicks(self.daily.get(day), urls)
            if value is None:
                missing += 1
                continue
            total += value
            counted.append(day)
        return {"clicks": total if counted else None, "days": list(days),
                "days_counted": len(counted), "days_missing": missing,
                "days_not_closed": not_closed,
                "days_excluded_other_post_window": len(skipped),
                "reason": None if counted else ("no_closed_day" if not_closed == len(days)
                                                else "no_countable_day")}

    def before_post(self, post_id, posted, urls) -> dict:
        """投稿日より前の 3 暦日に付いた、そのリンク先のクリック（`clicks_before_post`）。

        **窓の和（clicks_72h）には足さない**。1 以上なら、THTH を通していない投稿か
        Threads の外のクリックが混ざっている可能性がある（原因は言わない）。
        """
        start = _day(posted)
        days = [(start - datetime.timedelta(days=i)).isoformat()
                for i in range(BEFORE_DAYS, 0, -1)]
        return {**self._sum_days(post_id, urls, days), "basis": BEFORE_BASIS}

    def after_window(self, post_id, posted, urls) -> dict:
        """3 暦日の窓のあとの 7 暦日の、そのリンク先のクリック（参考・窓には足さない）。"""
        start = _day(posted) + datetime.timedelta(days=WINDOW_DAYS)
        days = [(start + datetime.timedelta(days=i)).isoformat() for i in range(AFTER_DAYS)]
        return {**self._sum_days(post_id, urls, days), "basis": AFTER_BASIS}


def summarize(rows, min_n) -> dict:
    """投稿ごとの結果を、中央値と分母に畳む（n が min_n に届かなければ null と理由）。"""
    from . import analytics_comparison as comparison
    clicks = [row["clicks_72h"] for row in rows if row["clicks_72h"] is not None]
    rates = [row["click_rate"] for row in rows if row["click_rate"] is not None]
    reasons = {}
    for row in rows:
        key = row["cannot_say"] or row["clicks_missing"]
        if key:
            reasons[key] = reasons.get(key, 0) + 1

    def stat(values):
        median = comparison._median(values) if values and len(values) >= min_n else None
        return {"median": median, "n_eligible": len(values), "n_total": len(rows),
                "reason": None if median is not None else ("below_min_n" if values
                                                           else "no_attributable_post")}
    return {"basis": BASIS, "window": WINDOW, "window_note": WINDOW_NOTE,
            "clicks_72h": stat(clicks),
            "click_rate": {**stat(rates), "rate_basis": RATE_BASIS},
            "reasons": dict(sorted(reasons.items()))}


# ------------------------------------------------------------ lint の知らせ

def _draft_urls(fm, body, cfg) -> list:
    from . import media as media_mod, queuefile
    media = cfg["media"] if cfg else "threads"
    section = queuefile.extract_section(body, media, allow_empty=True) or ""
    urls = urls_in(section)
    try:
        urls += [u for u in manifest_links(media_mod.manifest_for(fm, cfg)) if u not in urls]
    except media_mod.MediaError:
        pass
    return [value for value in (normalize_url(u) for u in urls) if value]


def _draft_time(fm):
    from . import queuefile
    for key in ("posted_at", "publish_at"):
        raw = fm.get(key)
        if isinstance(raw, str) and raw.strip():
            try:
                return queuefile.parse_publish_at(raw)
            except ValueError:
                at = jst.parse(raw)
                if at is not None:
                    return at
    return None


def shared_drafts(path, fm, body, cfg) -> list:
    """同じ account で、同じリンク先を前後 72 時間に使う click の原稿（自分を除く名前）。

    同じ queue の置き場の単発の原稿だけを見る。**断らない**——知らせるだけ。
    （3.6.0 の報告にあった「同じ日の click 2 本」はこの条件で置き換える——同じ日でも
    リンク先が違えば測れる。）
    """
    from . import goals as goals_mod, queuefile
    if goals_mod.normalize(fm.get(goals_mod.KEY)) != "click":
        return []
    account = fm.get("account")
    at = _draft_time(fm)
    mine = set(_draft_urls(fm, body, cfg)) - profile_links(cfg)
    if not account or at is None or not mine:
        return []
    folder = os.path.dirname(os.path.abspath(path))
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    out = []
    for name in names:
        other = os.path.join(folder, name)
        if not name.endswith(".md") or os.path.abspath(other) == os.path.abspath(path):
            continue
        try:
            qf = queuefile.parse(other)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        ofm = qf.front_matter
        if qf.malformed or ofm.get("account") != account or ofm.get("status") == "withdrawn":
            continue
        if goals_mod.normalize(ofm.get(goals_mod.KEY)) != "click":
            continue
        other_at = _draft_time(ofm)
        if other_at is None or not _neighbors(at, other_at):
            continue
        if mine & set(_draft_urls(ofm, qf.body, cfg)):
            out.append(name)
    return out


def lint_notes(path, fm, body, cfg) -> list:
    """lint の警告（`warning:` で始まる・落とさない）。無ければ空。"""
    names = shared_drafts(path, fm, body, cfg)
    if not names:
        return []
    return [f"warning: {URL_SHARED}: 同じリンク先を前後 72 時間に使う click の原稿が"
            f"ほかに {len(names)} 本あります（{'・'.join(names)}）——投稿ごとのクリックは"
            "測れません（リンク先を投稿ごとに分ければ測れます）"]
