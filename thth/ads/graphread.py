"""広告の読むだけの口（計画 段 4・D-5）。**GET しか持たない。**

Graph への要求はすべて `_get()` を通り、`_get()` は `meta.graph.Client.request("GET", …)` だけを呼ぶ。
POST・DELETE を投げる綴りはこの包みに無い（`tests/test_meta_stage4.py` が綴りと実際の method の
両方で縛る）。

読むもの（**L2**: developers.facebook.com/docs/marketing-api/reference/ad-account ・
/docs/marketing-api/insights。読んだのは計画の調査 §3。本物での確かめは段 4 の本番の確かめ・G-1）:

- `/act_<id>`: `name`・`currency`・`timezone_name`・`account_status`・`amount_spent`・`spend_cap`
  （**読む欄は最小単位の文字列**【確・原文】。最小単位の整数で持つ）。`balance`・`min_daily_budget`・
  `minimum_budgets`・`business` は**別の呼び出し**で訊く（欄の名前が 1 つでも違うと全体がエラーになる
  ので、確かめていない欄で本筋の読みを落とさない）。
- `/act_<id>/campaigns|adsets|ads`: `status`・`effective_status`・予算・期間（D-5 の照合の木）。
- `/act_<id>/insights`: `spend`（**普通の単位の小数の文字列**【推】）・`impressions`・`clicks`・`reach`・
  `actions`（反応の種類ごとの数の並び）・`inline_link_clicks`（11/03 本番の試しの広告で返るのを確かめた）。
  反応の名前は Ads Action Stats v26.0（developers.facebook.com/docs/marketing-api/reference/ads-action-stats/）。
  応答の `x-fb-ads-insights-throttle` は共通層が `meta_usage.json` に残し、次に読む前に使用率を見る。
- `/me/adaccounts`: 鍵で見える広告アカウントの一覧。

量の制限（80000・80004 ほか）は共通層が `rate_limited` に振り分け、待ちの間は網に出ない。
"""
from __future__ import annotations

import decimal
import json
import hashlib
import re
import urllib.error

from ..adapters import base
from ..meta import errors as meta_errors
from ..meta import graph as meta_graph
from ..meta import usage as meta_usage
from . import ledger

ACCOUNT_FIELDS = "id,name,currency,timezone_name,account_status,amount_spent,spend_cap"
ACCOUNT_EXTRA_FIELDS = ("balance", "min_daily_budget", "minimum_budgets", "business")
CAMPAIGN_FIELDS = "id,name,status,effective_status,lifetime_budget,daily_budget,start_time,stop_time"
ADSET_FIELDS = ("id,name,campaign_id,status,effective_status,lifetime_budget,daily_budget,"
                "start_time,end_time")
AD_FIELDS = "id,name,campaign_id,adset_id,status,effective_status"
# `actions`・`inline_link_clicks` は 11/03 本番の試しの広告で返るのを確かめた。残り（INSIGHT_FIELDS_EXTRA）は
# 文書だけで確かめた欄（ad-account/insights の参照の欄の表・v26.0・
# developers.facebook.com/documentation/ads-commerce/marketing-api/reference/ad-account/insights.md）なので、
# 断られたら（`#100`）外して 1 度だけ読み直す。**本番未確認。**
INSIGHT_FIELDS_BASE = "spend,impressions,clicks,reach,actions,inline_link_clicks"
# 効率（Meta の計算済みの値・numeric string）: frequency・cpm・ctr・cpc・cost_per_unique_click・
# inline_link_click_ctr・cost_per_inline_link_click。list<AdsActionStats>: outbound_clicks・outbound_clicks_ctr・
# cost_per_outbound_click。動画（list<AdsActionStats>）: video_play_actions・video_p25〜p100_watched_actions・
# video_avg_time_watched_actions・cost_per_thruplay。
# **足さなかった名前**（欄の表に無い）: quality_ranking・engagement_rate_ranking・conversion_rate_ranking・
# unique_clicks・unique_ctr・unique_inline_link_clicks・video_thruplay_watched_actions。
EFFICIENCY_NUMERIC = ("frequency", "cpm", "ctr", "cpc", "cost_per_unique_click", "inline_link_click_ctr",
                      "cost_per_inline_link_click")
EFFICIENCY_LISTS = ("outbound_clicks_ctr", "cost_per_outbound_click")
VIDEO_FIELDS = (("plays", "video_play_actions"), ("p25", "video_p25_watched_actions"),
                ("p50", "video_p50_watched_actions"), ("p75", "video_p75_watched_actions"),
                ("p95", "video_p95_watched_actions"), ("p100", "video_p100_watched_actions"),
                ("avg_time_watched", "video_avg_time_watched_actions"), ("cost_per_thruplay", "cost_per_thruplay"))
INSIGHT_FIELDS_EXTRA = ",".join(("outbound_clicks",) + EFFICIENCY_NUMERIC + EFFICIENCY_LISTS
                                + tuple(f for _k, f in VIDEO_FIELDS))
INSIGHT_FIELDS = INSIGHT_FIELDS_BASE + "," + INSIGHT_FIELDS_EXTRA
# 内訳（`breakdowns`・Insights API Breakdowns の文書）。内訳を付けた読みは INSIGHT_FIELDS_BASE だけを頼む
# （hourly では unique 系・video 系を頼めない・region では video_p* を頼めない【文書】ので一律に絞る）。
# hourly では reach が 0 で返る【文書】。
BREAKDOWNS = {"age-gender": "age,gender", "region": "region", "country": "country",
              "placement": "publisher_platform,platform_position", "device": "impression_device",
              "hour": "hourly_stats_aggregated_by_advertiser_time_zone"}
# ピクセル（/act_<id>/adspixels・AdsPixel の欄・文書）。読むだけ。作る口は作らない。
PIXEL_FIELDS = "id,name,last_fired_time,is_unavailable,creation_time"
# 出す前の見積もり（/act_<id>/reachestimate・/act_<id>/delivery_estimate・文書）。
OPTIMIZATION_GOALS = ("POST_ENGAGEMENT", "LINK_CLICKS", "LANDING_PAGE_VIEWS", "REACH", "IMPRESSIONS")
# 反応の内訳とサイトへの誘導（5.6.3・masaru 11/03）。`actions` の `action_type` のうち人に見せる物だけ。
# 名前は Meta の公式の文書 Ads Action Stats（v26.0・
# developers.facebook.com/docs/marketing-api/reference/ads-action-stats/）で確かめた: post_reaction＝Post
# Reactions・comment＝Post Comments・post＝Post Shares・photo_view＝Page Photo Views・like＝Page Likes・
# link_click＝Link Clicks・outbound_click＝Outbound Clicks・landing_page_view＝Landing Page Views。
# **`clicks` は広告のどこを押しても数える**（文書「The number of clicks on your ads」）のでリンクのクリックでは
# ない。
# (JSON の鍵, action_type, 人に見せる名前)
ENGAGEMENT_ACTIONS = (("reactions", "post_reaction", "いいね・リアクション"),
                      ("comments", "comment", "コメント"),
                      ("shares", "post", "シェア"),
                      ("photo_views", "photo_view", "写真を開いた"),
                      ("page_likes", "like", "ページへのいいね"))
SITE_LABELS = (("link_clicks", "リンククリック"), ("outbound_clicks", "サイトへ出たクリック"),
               ("landing_page_views", "頁が開いた"))
# 段 5: 1 つのキャンペーンの木（digest の材料・D-2）。`account_id` で口座の物かを確かめる。
CAMPAIGN_TREE_FIELDS = ("id,name,account_id,objective,status,effective_status,lifetime_budget,daily_budget,"
                        "start_time,stop_time")
ADSET_TREE_FIELDS = ("id,name,campaign_id,status,effective_status,lifetime_budget,daily_budget,"
                     "start_time,end_time,targeting")
AD_TREE_FIELDS = ("id,name,campaign_id,adset_id,status,effective_status,"
                  "creative{id,object_story_id,source_instagram_media_id}")
ID_RE = re.compile(r"[0-9]{1,32}")
ADACCOUNTS_FIELDS = "id,account_id,name,currency,timezone_name,account_status"
# 計画 Meta 全部盛り M3（読むだけ・保存しない）。L2: reference/product-catalog・guides/lead-ads/retrieving・
# audience-network/optimization/report-api/guide-v2。`leadgen_forms` と `commerce_merchant_settings` の欄は未確認。
CATALOG_FIELDS = "id,name,product_count"
PRODUCT_FIELDS = "id,name,retailer_id"
LEAD_FORM_FIELDS = "id,name,status,leads_count"
LEAD_FIELDS = "id,created_time,field_data"
AUDIENCE_METRICS = ["fb_ad_network_imp", "fb_ad_network_click", "fb_ad_network_revenue"]
SHOP_FIELDS = "id,cta,shop_setup"
# `date_preset`（L2: insights の参照の一覧から、THTH が使う物だけ）。`maximum` は全期間（最大 37 か月・
# developers.facebook.com/docs/marketing-api/reference/ad-account/insights）。
PRESETS = ("today", "yesterday", "last_7d", "last_14d", "last_30d", "this_month", "last_month", "maximum")
# **今日を含まない**期間（11/01 本番: 今日だけ配信した広告が last_7d で「行がありません」になった）。
# this_month は今日を含むと読むが【未】、言い切らないので両方の組に入れない。
PRESETS_WITHOUT_TODAY = frozenset({"yesterday", "last_7d", "last_14d", "last_30d", "last_month"})
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
# 配信の診断で読む広告セットの欄（段 5・11/02）。`budget_remaining` は最小単位の文字列【文書】、
# `issues_info` は配信を妨げた問題の並び【文書】。
ADSET_DELIVERY_FIELDS = ("id,name,campaign_id,status,effective_status,lifetime_budget,daily_budget,"
                         "budget_remaining,start_time,end_time,issues_info")
LEVELS = ("account", "campaign", "adset", "ad")
PAGE_LIMIT = 100
MAX_PAGES = 10
# 成績の読みを見送る使用率（**THTH の線**・L2「100% に近づいたら待つ」）。
INSIGHTS_THROTTLE_PCT = 90.0
# 金額の欄（最小単位の文字列 → 整数）。
MINOR_FIELDS = ("amount_spent", "spend_cap", "balance", "min_daily_budget", "lifetime_budget", "daily_budget")
# insights の `spend`（普通の単位）を最小単位に直せる通貨と、その倍率（**JPY だけ**。ほかは
# 単位を本物で確かめるまで直さない・B-13。直さない値は `spend` の文字列のまま残す）。
CURRENCY_OFFSETS = {"JPY": 1}


class AdsReadError(base.AdapterError):
    """読めなかった。`code` は静的な符丁（本文・URL・鍵は入れない）。"""

    def __init__(self, code, *, failure=None, http_status=None):
        super().__init__(code)
        self.code = code
        self.failure = failure
        self.http_status = http_status


def minor(value) -> int | None:
    """最小単位の文字列（`"12345"`）か整数 → 整数。形が違えば None（推して埋めない）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit() and len(value) <= 20:
        return int(value)
    return None


def spend_minor(value, currency) -> int | None:
    """insights の `spend`（普通の単位の小数の文字列）→ 最小単位の整数。直せない通貨・形は None。"""
    offset = CURRENCY_OFFSETS.get(currency)
    if offset is None or not isinstance(value, str) or len(value) > 32:
        return None
    try:
        amount = decimal.Decimal(value)
    except decimal.InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    scaled = amount * offset
    if scaled != scaled.to_integral_value():
        return None   # JPY で端数が出たら単位の読みが違う（G-1）。埋めずに言う
    return int(scaled)


def _count(value) -> int | None:
    if isinstance(value, str) and value.isdigit() and len(value) <= 20:
        return int(value)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _action_counts(actions) -> dict | None:
    """`[{action_type, value}]` → `{action_type: 数}`。並びでなければ None。値が数でない・同じ種類が 2 行ある
    種類は None（推して埋めない）。"""
    if not isinstance(actions, list):
        return None
    seen: dict = {}
    for row in actions:
        if not isinstance(row, dict) or not isinstance(row.get("action_type"), str):
            continue
        kind = row["action_type"]
        seen[kind] = None if kind in seen else _count(row.get("value"))
    return seen


def engagement(actions) -> tuple[dict | None, str | None]:
    """`actions` の並び → (`{reactions: n, comments: n, …}`, 不明の理由)。

    Meta は数が 0 の種類の行を出さない（11/03 本番: シェア・コメントの行が無かった）ので、**並びが読めて行が
    無い＝0**。`actions` の欄そのものが無い・並びでないときは全部 None（不明）にし、理由を返す。
    """
    if actions is None:
        return None, "actions_missing"
    seen = _action_counts(actions)
    if seen is None:
        return None, "actions_unreadable"
    return {key: seen.get(kind, 0) for key, kind, _label in ENGAGEMENT_ACTIONS}, None


def site(actions, inline_link_clicks, outbound, *, outbound_asked: bool = True,
         actions_absent_as_zero: bool = False) -> dict:
    """サイトへの誘導（リンククリック・サイトへ出たクリック・頁が開いた）。

    数ごとに `why`（なぜその値か）を付け、**「計測が無いから出ない」と「本当に 0」を分ける**:
    - `reported`: Meta が数を返した。
    - `zero_reported_list`: 並びは読めて、その種類の行が無い（Meta は 0 の行を出さない）。
    - `no_link_clicks`: リンクが 1 度も押されていないので 0（本当に 0）。
    - `not_measured_or_left`: リンクは押されたのに頁が開いた記録が無い。頁の側の計測（Meta の pixel など）が
      無いか、開く前に離れた。**0 とは言わない**（None）。
    - `actions_missing`／`actions_unreadable`／`outbound_clicks_missing`／`outbound_clicks_unreadable`／
      `outbound_clicks_not_asked`: 欄が無い・読めない・頼めなかった（None）。
    - `actions_absent_as_zero`: その行に `actions` の欄が無いが、同じ読みのほかの行には返っている
      （`insights()` が決める）。Meta は反応が 1 件も無い区分では欄ごと返さないので、リンククリックも
      頁が開いた数も 0。
    """
    if actions_absent_as_zero and (_count(inline_link_clicks) or 0) > 0:
        # 広告の中のリンクは押されている（inline が 1 以上）。欄が無いのを 0 と見なすと「押されたのに
        # リンククリック 0・頁が開いた 0」と食い違うので、この行は 0 と見なさない（不明のまま・監査 N-1）。
        actions_absent_as_zero = False
    if actions_absent_as_zero:
        actions = []
    counts = _action_counts(actions) if actions is not None else None
    links = counts.get("link_click", 0) if counts is not None else None
    inline = _count(inline_link_clicks)
    known = [n for n in (links, inline) if isinstance(n, int)]
    if any(n > 0 for n in known):
        clicked = True
    elif known and len(known) == 2:
        clicked = False   # 両方読めて両方 0 のときだけ「押されていない」と言う
    else:
        clicked = None
    out, why = {}, {}
    if counts is None:
        out["link_clicks"] = None
        why["link_clicks"] = "actions_missing" if actions is None else "actions_unreadable"
    else:
        out["link_clicks"] = links
        why["link_clicks"] = "reported" if "link_click" in counts else "zero_reported_list"
    out["inline_link_clicks"] = inline
    # サイトへ出たクリック（`outbound_clicks` の並びの `outbound_click`）
    out_counts = _action_counts(outbound) if outbound is not None else None
    if out_counts is not None:
        out["outbound_clicks"] = out_counts.get("outbound_click", 0)
        why["outbound_clicks"] = "reported" if "outbound_click" in out_counts else "zero_reported_list"
    elif clicked is False:
        out["outbound_clicks"], why["outbound_clicks"] = 0, "no_link_clicks"
    else:
        out["outbound_clicks"] = None
        why["outbound_clicks"] = ("outbound_clicks_not_asked" if not outbound_asked else
                                  "outbound_clicks_unreadable" if outbound is not None else "outbound_clicks_missing")
    # 頁が開いた（`actions` の `landing_page_view`）
    if counts is None:
        out["landing_page_views"], why["landing_page_views"] = None, why["link_clicks"]
    elif "landing_page_view" in counts:
        out["landing_page_views"], why["landing_page_views"] = counts["landing_page_view"], "reported"
    elif clicked is False:
        out["landing_page_views"], why["landing_page_views"] = 0, "no_link_clicks"
    else:
        out["landing_page_views"], why["landing_page_views"] = None, "not_measured_or_left"
    if actions_absent_as_zero:
        # 欄ごと無い＝どの種類の行も無い。リンククリックも頁が開いた数も 0（並びの行が無いのと同じ扱い）。
        out["landing_page_views"] = 0
        why["link_clicks"] = why["landing_page_views"] = "actions_absent_as_zero"
    out["why"] = why
    return out


def _decimal_text(value) -> str | None:
    """Meta の numeric string をそのまま（数として読める物だけ）。"""
    if not isinstance(value, str) or len(value) > 32:
        return None
    try:
        amount = decimal.Decimal(value)
    except decimal.InvalidOperation:
        return None
    return value if amount.is_finite() and amount >= 0 else None


def _stat_value(stats, prefer: str):
    """list<AdsActionStats> から 1 つの値（`prefer` の action_type、無ければ行が 1 つだけのときその値）。
    並びでない・選べないときは None。値は Meta の文字列のまま（数として読める物だけ）。"""
    if not isinstance(stats, list):
        return None
    rows = [r for r in stats if isinstance(r, dict)]
    chosen = [r for r in rows if r.get("action_type") == prefer]
    if not chosen and len(rows) == 1:
        chosen = rows
    if len(chosen) != 1:
        return None
    return _decimal_text(chosen[0].get("value")) if not isinstance(chosen[0].get("value"), int) \
        else str(chosen[0]["value"])


def efficiency(row: dict, *, asked: bool = True) -> dict | None:
    """効率（Meta の計算済みの値・文字列のまま）。頼んでいなければ None。欄が無ければその欄は None。"""
    if not asked:
        return None
    out = {key: _decimal_text(row.get(key)) for key in EFFICIENCY_NUMERIC}
    out["outbound_clicks_ctr"] = _stat_value(row.get("outbound_clicks_ctr"), "outbound_click")
    out["cost_per_outbound_click"] = _stat_value(row.get("cost_per_outbound_click"), "outbound_click")
    return out


def video(row: dict) -> dict | None:
    """動画の再生と見られた深さ。**動画の欄が 1 つも返らなければ None**（動画でない広告では出さない）。"""
    if not any(field in row for _key, field in VIDEO_FIELDS):
        return None
    return {key: _stat_value(row.get(field), "video_view") for key, field in VIDEO_FIELDS}


SITE_WHY_TEXT = {"not_measured_or_left": "記録なし（リンクは押された。頁の側の計測が無いか、開く前に離れた）",
                 "actions_missing": "不明（Meta が actions を返さなかった）",
                 "actions_unreadable": "不明（actions が読めなかった）",
                 "outbound_clicks_missing": "不明（Meta が outbound_clicks を返さなかった）",
                 "outbound_clicks_unreadable": "不明（outbound_clicks が読めなかった）",
                 "outbound_clicks_not_asked": "不明（Meta が outbound_clicks の欄を断ったので頼んでいない）"}


def cost_per(spend, counts, keys) -> dict | None:
    """1 件あたりの額（`spend` と同じ普通の単位・小数 2 桁の文字列）。0 件・不明・spend が読めない物は None。"""
    if not isinstance(counts, dict):
        return None
    try:
        amount = decimal.Decimal(spend) if isinstance(spend, str) and len(spend) <= 32 else None
    except decimal.InvalidOperation:
        amount = None
    if amount is not None and (not amount.is_finite() or amount < 0):
        amount = None
    out = {}
    for key in keys:
        n = counts.get(key)
        out[key] = (str((amount / n).quantize(decimal.Decimal("0.01"), rounding=decimal.ROUND_HALF_UP))
                    if amount is not None and isinstance(n, int) and not isinstance(n, bool) and n > 0 else None)
    return out


def raw_actions(actions) -> list | None:
    """`actions` の生の行（`action_type`・`value` だけ・文字列のまま）。並びでなければ None。"""
    if not isinstance(actions, list):
        return None
    return [{"action_type": row["action_type"], "value": row.get("value")} for row in actions
            if isinstance(row, dict) and isinstance(row.get("action_type"), str)]


def engagement_text(row: dict, currency: str | None = None) -> list:
    """人の読む行（反応・サイトへの誘導・1 件あたり）。`row` は `Reader.insights` の 1 行。"""
    def num(value):
        return "不明" if value is None else str(value)
    lines = []
    counts = row.get("engagement")
    if isinstance(counts, dict):
        lines.append("反応: " + "・".join(f"{label} {num(counts.get(key))}" for key, _kind, label in ENGAGEMENT_ACTIONS))
    elif "engagement" in row:
        why = {"actions_missing": "Meta が反応の欄（actions）を返しませんでした",
               "actions_unreadable": "反応の欄（actions）が読めませんでした"}.get(row.get("engagement_unknown"),
                                                                              "読めませんでした")
        lines.append(f"反応: 不明（{why}）")
    s = row.get("site")
    if isinstance(s, dict):
        why = s.get("why") or {}
        parts = []
        for key, label in SITE_LABELS:
            value, reason = s.get(key), why.get(key) or ""
            if value is None:
                parts.append(f"{label} " + SITE_WHY_TEXT.get(reason, "不明"))
            else:
                parts.append(f"{label} {value}" + ("（リンクが押されていないので 0）" if reason == "no_link_clicks" else ""))
        lines.append("サイトへの誘導: " + "・".join(parts)
                     + f"（広告の中のリンクのクリック {num(s.get('inline_link_clicks'))}）")
    eff = row.get("efficiency")
    if isinstance(eff, dict):
        names = (("frequency", "1 人あたりの表示"), ("cpm", "1000 表示あたり"), ("ctr", "クリック率（全部・%）"),
                 ("cpc", "1 クリックあたり（全部）"), ("cost_per_unique_click", "1 人のクリックあたり（推計）"),
                 ("inline_link_click_ctr", "リンクのクリック率（%）"),
                 ("cost_per_inline_link_click", "1 リンククリックあたり"),
                 ("outbound_clicks_ctr", "サイトへ出たクリック率（%）"),
                 ("cost_per_outbound_click", "1 サイトへ出たクリックあたり"))
        shown = [f"{label} {eff[key]}" for key, label in names if eff.get(key) is not None]
        if shown:
            lines.append("効率（Meta の計算）: " + "・".join(shown))
    vid = row.get("video")
    if isinstance(vid, dict):
        names = (("plays", "再生"), ("p25", "25%"), ("p50", "50%"), ("p75", "75%"), ("p95", "95%"), ("p100", "100%"),
                 ("avg_time_watched", "平均の再生秒"), ("cost_per_thruplay", "1 ThruPlay あたり"))
        lines.append("動画: " + "・".join(f"{label} {num(vid.get(key))}" for key, label in names))
    costs = row.get("cost_per") or {}
    labels = [(key, label) for key, _kind, label in ENGAGEMENT_ACTIONS] + list(SITE_LABELS)
    priced = [f"{label} {costs[key]}" for key, label in labels if costs.get(key)]
    if priced:
        lines.append("1 件あたり（" + (currency or "spend と同じ単位") + "）: " + "・".join(priced))
    return lines


class Reader:
    """1 つの広告の口座を読む。鍵はその口座の `.token`（ユーザーの長い鍵）。"""

    def __init__(self, name: str, cfg: dict, token: dict, *, base_url: str | None = None,
                 app_secret: str | None = None):
        access = token.get("access_token") if isinstance(token, dict) else None
        if not isinstance(access, str) or not access:
            raise AdsReadError("ads_token_missing")
        self.name = name
        self.cfg = cfg
        self.act = "act_" + cfg["act_id"]
        if app_secret is None:
            app_secret = meta_graph.load_app_secret()
        self.client = meta_graph.Client(base_url or meta_graph.origin(), access, app_secret=app_secret,
                                        usage_path=ledger.usage_path_for(name))
        ledger.ensure_state_dir(name)

    # ------------------------------------------------------------------ 1 か所

    def _get(self, path: str, params: dict | None = None, *, urgent: bool = False) -> dict:
        """**唯一の網の口。GET だけ。** 失敗は静的な符丁の `AdsReadError` にする。

        `urgent=True` は止める操作（`thth ads stop` の読み直し）の読み: 量の制限の待ちを見ず、断られたら
        すぐ返す（A-4・止める道を待たせない）。
        """
        try:
            body = self.client.request("GET", path, params=params or {}, urgent=urgent)
        except meta_errors.RateLimited:
            raise
        except urllib.error.HTTPError as exc:
            err = meta_errors.graph_error(exc)
            code = err.get("code") if isinstance(err.get("code"), int) else None
            failure = meta_errors.classify(exc.code, err)
            raise AdsReadError(f"ads_http_{exc.code}" + (f"_code_{code}" if code is not None else ""),
                               failure=failure, http_status=exc.code) from None
        except (OSError, ValueError):
            raise AdsReadError("ads_response_unavailable", failure=meta_errors.AMBIGUOUS) from None
        if not isinstance(body, dict):
            raise AdsReadError("ads_response_invalid")
        return body

    def _list(self, path: str, params: dict, *, urgent: bool = False) -> tuple[list, bool]:
        """頁を辿る。`(行, 全部読めたか)`。`MAX_PAGES` を越えたら読めた所までで False。"""
        rows, after = [], None
        for _ in range(MAX_PAGES):
            query = dict(params, limit=PAGE_LIMIT)
            if after:
                query["after"] = after
            body = self._get(path, query, urgent=urgent)
            data = body.get("data")
            if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
                raise AdsReadError("ads_list_invalid")
            rows.extend(data)
            paging = body.get("paging") if isinstance(body.get("paging"), dict) else {}
            if not paging.get("next"):
                return rows, True
            cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
            after = cursors.get("after")
            if not isinstance(after, str) or not after:
                return rows, False
        return rows, False

    # ------------------------------------------------------------------ 読む口

    def account(self, *, urgent: bool = False) -> dict:
        """`/act_<id>` の本筋の欄（金額は最小単位の整数）。"""
        body = self._get("/" + self.act, {"fields": ACCOUNT_FIELDS}, urgent=urgent)
        if str(body.get("id") or "") != self.act:
            raise AdsReadError("ads_account_mismatch")
        status = body.get("account_status")
        return {
            "act_id": self.cfg["act_id"],
            "name": body.get("name") if isinstance(body.get("name"), str) else None,
            "currency": body.get("currency") if isinstance(body.get("currency"), str) else None,
            "timezone_name": body.get("timezone_name") if isinstance(body.get("timezone_name"), str) else None,
            "account_status": status if isinstance(status, int) and not isinstance(status, bool) else None,
            "amount_spent": minor(body.get("amount_spent")),
            "spend_cap": minor(body.get("spend_cap")),
        }

    def account_extras(self) -> dict:
        """確かめていない欄を 1 つずつ訊く。`{欄: 値}` と `{"unavailable": [欄…]}`。"""
        out, unavailable = {}, []
        for field in ACCOUNT_EXTRA_FIELDS:
            try:
                body = self._get("/" + self.act, {"fields": field})
            except AdsReadError:
                unavailable.append(field)
                continue
            value = body.get(field)
            if field in MINOR_FIELDS:
                out[field] = minor(value)
            elif field == "business":
                out[field] = ({"id": value.get("id"), "name": value.get("name")}
                              if isinstance(value, dict) else None)
            elif field == "minimum_budgets":
                # 通貨ごとの最低額の並び（本番 10/30: 全通貨が返る）。**口座の通貨の行だけ**を写す
                # （通貨が分からないときは並べない・数だけ）。
                out[field] = _minimum_budgets_for(value, self.cfg.get("currency"))
            else:
                out[field] = value
        out["unavailable"] = unavailable
        return out

    def tree(self, *, urgent: bool = False) -> dict:
        """キャンペーン・広告セット・広告の `status`・`effective_status`・予算・期間。"""
        out, complete = {}, True
        for key, edge, fields in (("campaigns", "campaigns", CAMPAIGN_FIELDS),
                                  ("adsets", "adsets", ADSET_FIELDS),
                                  ("ads", "ads", AD_FIELDS)):
            rows, whole = self._list(f"/{self.act}/{edge}", {"fields": fields}, urgent=urgent)
            complete = complete and whole
            out[key] = [_node(row) for row in rows]
        out["complete"] = complete
        return out

    def campaign_tree(self, campaign_id: str, *, urgent: bool = False) -> dict:
        """1 つのキャンペーンの木の全体（段 5・D-2 の digest の材料・A-5）。

        キャンペーン・その下の**全部の**広告セット（`targeting` つき）と広告（クリエイティブの
        `object_story_id`・`source_instagram_media_id` つき）。キャンペーンが口座の物でなければ断る。
        """
        if not isinstance(campaign_id, str) or not ID_RE.fullmatch(campaign_id):
            raise AdsReadError("ads_id_invalid")
        body = self._get("/" + campaign_id, {"fields": CAMPAIGN_TREE_FIELDS}, urgent=urgent)
        if str(body.get("id") or "") != campaign_id:
            raise AdsReadError("ads_campaign_mismatch")
        if str(body.get("account_id") or "") != self.cfg["act_id"]:
            raise AdsReadError("ads_campaign_other_account")
        adsets, whole_a = self._list(f"/{campaign_id}/adsets", {"fields": ADSET_TREE_FIELDS}, urgent=urgent)
        ads, whole_b = self._list(f"/{campaign_id}/ads", {"fields": AD_TREE_FIELDS}, urgent=urgent)
        return {"campaign": _node(body), "adsets": [_node(row) for row in adsets],
                "ads": [_node(row) for row in ads], "complete": whole_a and whole_b}

    def node(self, object_id: str, kind: str, *, urgent: bool = False) -> dict:
        """広告セット・キャンペーン 1 つ（`lower`・`end`・`extend` が今の値を読む）。"""
        if not isinstance(object_id, str) or not ID_RE.fullmatch(object_id):
            raise AdsReadError("ads_id_invalid")
        fields = {"adset": ADSET_TREE_FIELDS + ",account_id", "campaign": CAMPAIGN_TREE_FIELDS}.get(kind)
        if fields is None:
            raise AdsReadError("ads_kind_invalid")
        body = self._get("/" + object_id, {"fields": fields}, urgent=urgent)
        if str(body.get("id") or "") != object_id:
            raise AdsReadError("ads_object_mismatch")
        if str(body.get("account_id") or "") != self.cfg["act_id"]:
            raise AdsReadError("ads_object_other_account")
        return _node(body)

    def insights_util_pct(self) -> float | None:
        row = meta_usage.read(None, path=ledger.usage_path_for(self.name)) or {}
        headers = row.get("headers") if isinstance(row.get("headers"), dict) else {}
        return meta_usage.insights_throttle_pct(headers)

    def insights(self, preset: str = "last_30d", *, level: str = "account", since: str | None = None,
                 until: str | None = None, object_id: str | None = None, breakdown: str | None = None) -> list:
        """`/act_<id>/insights`（`object_id` を渡せばそのキャンペーン・広告セットの `/insights`）。
        **前回の使用率が線を越えていれば呼ばない**（`ads_insights_throttled`）。

        `since`・`until`（`YYYY-MM-DD`・広告アカウントの時間帯の日・両端を含む）を渡すと `time_range` で
        読み、`date_preset` は送らない（Meta は time_range があると date_preset を無視する【文書】）。
        """
        if since is None and until is None and preset not in PRESETS:
            raise AdsReadError("ads_preset_invalid")
        if level not in LEVELS:
            raise AdsReadError("ads_level_invalid")
        params = {"level": level}
        if since is not None or until is not None:
            if not (isinstance(since, str) and DATE_RE.fullmatch(since) and isinstance(until, str)
                    and DATE_RE.fullmatch(until)) or since > until:
                raise AdsReadError("ads_time_range_invalid")
            params["time_range"] = json.dumps({"since": since, "until": until}, separators=(",", ":"))
        else:
            params["date_preset"] = preset
        if object_id is not None and (not isinstance(object_id, str) or not ID_RE.fullmatch(object_id)):
            raise AdsReadError("ads_id_invalid")
        pct = self.insights_util_pct()
        if pct is not None and pct >= INSIGHTS_THROTTLE_PCT:
            raise AdsReadError("ads_insights_throttled", failure=meta_errors.RATE_LIMITED)
        if breakdown is not None:
            if breakdown not in BREAKDOWNS:
                raise AdsReadError("ads_breakdown_invalid")
            params["breakdowns"] = BREAKDOWNS[breakdown]
        head = INSIGHT_FIELDS_BASE if breakdown is not None else INSIGHT_FIELDS
        fields = head + ("" if level == "account" else f",{level}_id,{level}_name")
        path = f"/{object_id}/insights" if object_id else f"/{self.act}/insights"
        extras_asked = breakdown is None
        try:
            rows, _ = self._list(path, dict(params, fields=fields))
        except AdsReadError as exc:
            # 文書だけで確かめた欄（INSIGHT_FIELDS_EXTRA）を断られたら、外して 1 度だけ読み直す（本筋の成績と、
            # それを使う watch・予算の照合を、確かめていない欄で落とさない）。
            if not extras_asked or not exc.code.startswith("ads_http_400_code_100"):
                raise
            extras_asked = False
            rows, _ = self._list(path, dict(params, fields=fields.replace(INSIGHT_FIELDS, INSIGHT_FIELDS_BASE, 1)))
        outbound_asked = extras_asked
        currency = self.cfg.get("currency")
        # **反応が 1 件も無い区分は `actions` の欄ごと返らない**（11/03 本番の `--by age-gender`）。頼んだ読みで、
        # ほかの行に `actions` が返っているなら、欄の無い行は 0 と見なす。読み全体で 1 行も返らないときは
        # 頼めたかどうかも分からないので「不明」のまま（内訳なしの 1 行の読みもここに入る）。
        actions_in_read = any(isinstance(row.get("actions"), list) for row in rows)
        out = []
        for row in rows:
            # ただし広告の中のリンクが押された行（inline_link_clicks が 1 以上）は 0 と見なさない（監査 N-1）。
            absent_as_zero = (actions_in_read and row.get("actions") is None
                              and not (_count(row.get("inline_link_clicks")) or 0) > 0)
            item = {"spend": row.get("spend") if isinstance(row.get("spend"), str) else None,
                    "spend_minor": spend_minor(row.get("spend"), currency),
                    "impressions": _count(row.get("impressions")),
                    "clicks": _count(row.get("clicks")),
                    "reach": _count(row.get("reach")),
                    "inline_link_clicks": _count(row.get("inline_link_clicks")),
                    "date_start": row.get("date_start") if isinstance(row.get("date_start"), str) else None,
                    "date_stop": row.get("date_stop") if isinstance(row.get("date_stop"), str) else None}
            counts, unknown = engagement([] if absent_as_zero else row.get("actions"))
            item["engagement"] = counts
            item["engagement_unknown"] = unknown
            item["actions_absent_as_zero"] = absent_as_zero
            item["site"] = site(row.get("actions"), row.get("inline_link_clicks"), row.get("outbound_clicks"),
                                outbound_asked=outbound_asked, actions_absent_as_zero=absent_as_zero)
            item["cost_per"] = cost_per(item["spend"], {**(counts or {}), **item["site"]},
                                        [k for k, _a, _l in ENGAGEMENT_ACTIONS] + [k for k, _l in SITE_LABELS])
            item["actions"] = raw_actions(row.get("actions"))
            item["outbound_clicks_raw"] = raw_actions(row.get("outbound_clicks"))
            item["efficiency"] = efficiency(row, asked=extras_asked)
            item["video"] = video(row) if extras_asked else None
            item["extras_asked"] = extras_asked
            if breakdown is not None:
                item["breakdown"] = {key: (str(row.get(key))[:100] if row.get(key) is not None else None)
                                     for key in BREAKDOWNS[breakdown].split(",")}
            if level != "account":
                item["id"] = row.get(f"{level}_id")
                item["name"] = row.get(f"{level}_name")
            out.append(item)
        return out

    def adset_delivery(self, adset_id: str) -> dict:
        """配信の診断（段 5・11/02）: 広告セットの `budget_remaining`・`issues_info`・予算・期間と、
        `delivery_estimate` の辺（別の呼び出し・読めなくても本筋は落とさない）。"""
        if not isinstance(adset_id, str) or not ID_RE.fullmatch(adset_id):
            raise AdsReadError("ads_id_invalid")
        body = self._get("/" + adset_id, {"fields": ADSET_DELIVERY_FIELDS})
        if str(body.get("id") or "") != adset_id:
            raise AdsReadError("ads_object_mismatch")
        out = _node(body)
        out["budget_remaining"] = minor(body.get("budget_remaining"))
        issues = body.get("issues_info") if isinstance(body.get("issues_info"), list) else []
        out["issues"] = [{k: str(row.get(k))[:200] for k in ("level", "error_code", "error_summary", "error_type",
                                                             "mid")
                          if row.get(k) is not None} for row in issues[:5] if isinstance(row, dict)]
        try:
            est = self._get(f"/{adset_id}/delivery_estimate", {})
            data = est.get("data") if isinstance(est.get("data"), list) else []
            first = data[0] if data and isinstance(data[0], dict) else {}
            out["delivery_estimate"] = {k: first.get(k) for k in ("estimate_ready", "estimate_dau",
                                                                  "estimate_mau_lower_bound",
                                                                  "estimate_mau_upper_bound")
                                        if isinstance(first.get(k), (int, bool))}
        except (AdsReadError, meta_errors.RateLimited) as exc:
            out["delivery_estimate"] = {"unavailable": getattr(exc, "code", type(exc).__name__)}
        return out

    def pixels(self) -> list:
        """広告アカウントのピクセル（`/act_<id>/adspixels`・読むだけ）。`last_fired_time` は最後に動いた時刻。"""
        rows, _ = self._list(f"/{self.act}/adspixels", {"fields": PIXEL_FIELDS})
        out = []
        for row in rows:
            pid = str(row.get("id") or "")
            if not ID_RE.fullmatch(pid):
                continue
            out.append({"id": pid, "name": str(row.get("name"))[:200] if row.get("name") is not None else None,
                        "last_fired_time": row.get("last_fired_time") if isinstance(row.get("last_fired_time"), str)
                        else None,
                        "is_unavailable": row.get("is_unavailable") if isinstance(row.get("is_unavailable"), bool)
                        else None,
                        "creation_time": row.get("creation_time") if isinstance(row.get("creation_time"), str)
                        else None})
        return out

    def reach_estimate(self, targeting: dict) -> dict:
        """狙う相手の大きさ（`/act_<id>/reachestimate`・読むだけ）。`users_lower_bound`〜`users_upper_bound`。
        -1 は「出せない」（一部のカスタムオーディエンス・文書）で、配信が止まる意味ではない。"""
        body = self._get(f"/{self.act}/reachestimate",
                         {"targeting_spec": json.dumps(targeting, ensure_ascii=False, separators=(",", ":"))})
        data = body.get("data")
        node = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else \
            (data if isinstance(data, dict) else body)
        out = {}
        for key in ("users_lower_bound", "users_upper_bound"):
            value = node.get(key)
            out[key] = value if isinstance(value, int) and not isinstance(value, bool) else None
        out["estimate_ready"] = node.get("estimate_ready") if isinstance(node.get("estimate_ready"), bool) else None
        return out

    def delivery_estimate(self, targeting: dict, goal: str) -> dict:
        """予算あたりの見込み（`/act_<id>/delivery_estimate`・読むだけ）。`daily_outcomes_curve` は確度の高い
        予測があるときだけ中身がある（無いときは全部 0 の 1 点・文書）。"""
        if goal not in OPTIMIZATION_GOALS:
            raise AdsReadError("ads_goal_invalid")
        body = self._get(f"/{self.act}/delivery_estimate",
                         {"optimization_goal": goal,
                          "targeting_spec": json.dumps(targeting, ensure_ascii=False, separators=(",", ":"))})
        data = body.get("data") if isinstance(body.get("data"), list) else []
        first = data[0] if data and isinstance(data[0], dict) else {}
        out = {k: first.get(k) for k in ("estimate_ready", "estimate_dau", "estimate_mau_lower_bound",
                                         "estimate_mau_upper_bound") if isinstance(first.get(k), (int, bool))}
        curve = first.get("daily_outcomes_curve") if isinstance(first.get("daily_outcomes_curve"), list) else []
        points = []
        for point in curve[:30]:
            if not isinstance(point, dict):
                continue
            points.append({k: point.get(k) for k in ("spend", "reach", "impressions", "actions")
                           if isinstance(point.get(k), (int, float)) and not isinstance(point.get(k), bool)})
        out["daily_outcomes_curve"] = points
        out["confident"] = any(any(v for v in p.values()) for p in points)
        return out

    def adaccounts(self) -> list:
        """鍵で見える広告アカウント（`/me/adaccounts`）。"""
        rows, _ = self._list("/me/adaccounts", {"fields": ADACCOUNTS_FIELDS})
        return [adaccount_row(row) for row in rows]

    # ------------------------------------------------------------------ 計画 Meta 全部盛り M3（読むだけ）

    def business_id(self) -> str:
        """台帳の `business_id`。無ければ広告アカウントの `business` を訊く（台帳には書かない）。"""
        value = self.cfg.get("business_id")
        if not value:
            business = self._get("/" + self.act, {"fields": "business"}).get("business")
            value = business.get("id") if isinstance(business, dict) else None
        if not isinstance(value, str) or not ID_RE.fullmatch(value):
            raise AdsReadError("ads_business_unknown")
        return value

    def catalogs(self) -> list:
        """ビジネスの商品カタログ（`/{business}/owned_product_catalogs`）。名前と商品数だけ。"""
        rows, _ = self._list(f"/{self.business_id()}/owned_product_catalogs", {"fields": CATALOG_FIELDS})
        return [{"id": str(row.get("id")), "name": str(row.get("name"))[:200] if row.get("name") is not None else None,
                 "product_count": row.get("product_count") if type(row.get("product_count")) is int else None}
                for row in rows if row.get("id")]

    def catalog_products(self, catalog_id: str, limit: int) -> list:
        """カタログの商品（`/{catalog}/products`・1 頁だけ）。ビジネスのカタログでなければ断る。"""
        if not any(row["id"] == catalog_id for row in self.catalogs()):
            raise AdsReadError("ads_catalog_other_business")
        body = self._get(f"/{catalog_id}/products", {"fields": PRODUCT_FIELDS, "limit": limit})
        return [{key: str(row[key])[:200] for key in PRODUCT_FIELDS.split(",") if row.get(key) is not None}
                for row in _data(body)]

    def lead_forms(self, page_id: str) -> list:
        """ページのリードのフォーム（`/{page}/leadgen_forms`）。名前と件数だけ。"""
        if not ID_RE.fullmatch(page_id):
            raise AdsReadError("ads_id_invalid")
        rows, _ = self._list(f"/{page_id}/leadgen_forms", {"fields": LEAD_FORM_FIELDS})
        return [{"id": str(row.get("id")), "name": str(row.get("name"))[:200] if row.get("name") is not None else None,
                 "status": row.get("status") if isinstance(row.get("status"), str) else None,
                 "leads_count": row.get("leads_count") if type(row.get("leads_count")) is int else None}
                for row in rows if row.get("id")]

    def leads(self, page_id: str, form_id: str, limit: int) -> list:
        """フォームのリード（`/{form}/leads`・1 頁だけ）。**名前・連絡先が入る。戻り値は端末へ出すだけで、
        snapshot・台帳・runs のどこにも渡さない**（`tests/test_m3_public_reads.py` が縛る）。"""
        if not any(row["id"] == form_id for row in self.lead_forms(page_id)):
            raise AdsReadError("ads_form_other_page")
        body = self._get(f"/{form_id}/leads", {"fields": LEAD_FIELDS, "limit": limit})
        return [{"id": str(row.get("id")), "created_time": row.get("created_time"),
                 "fields": {str(item.get("name")): item.get("values") for item in row.get("field_data") or []
                            if isinstance(item, dict)}}
                for row in _data(body)]

    def audience_network(self, since: str | None, until: str | None) -> dict:
        """Audience Network の数（`/{business}/adnetworkanalytics`・同期の読みは 8 日まで）。"""
        params = {"metrics": json.dumps(AUDIENCE_METRICS), "aggregation_period": "total"}
        for key, value in (("since", since), ("until", until)):
            if value is not None:
                if not DATE_RE.fullmatch(value):
                    raise AdsReadError("ads_date_invalid")
                params[key] = value
        first = (_data(self._get(f"/{self.business_id()}/adnetworkanalytics", params)) or [{}])[0]
        results = first.get("results") if isinstance(first.get("results"), list) else []
        omitted = first.get("omitted_results") if isinstance(first.get("omitted_results"), list) else []
        return {"rows": [{"metric": row.get("metric"), "time": row.get("time"), "value": row.get("value")}
                         for row in results if isinstance(row, dict)], "omitted": len(omitted)}

    def shop_settings(self) -> list:
        """ビジネスのショップの設定（`/{business}/commerce_merchant_settings`）。**欄と path は本物で未確認。**"""
        rows, _ = self._list(f"/{self.business_id()}/commerce_merchant_settings", {"fields": SHOP_FIELDS})
        return [{key: row[key] for key in SHOP_FIELDS.split(",") if isinstance(row.get(key), str)} for row in rows]


def _data(body: dict) -> list:
    """`data` が object の並びのときだけ「取れた」（無い・形違いは 0 件と混ぜない）。"""
    rows = body.get("data")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise AdsReadError("ads_list_invalid")
    return rows


def adaccount_row(row: dict) -> dict:
    status = row.get("account_status")
    return {"id": row.get("id") if isinstance(row.get("id"), str) else None,
            "act_id": row.get("account_id") if isinstance(row.get("account_id"), str) else None,
            "name": row.get("name") if isinstance(row.get("name"), str) else None,
            "currency": row.get("currency") if isinstance(row.get("currency"), str) else None,
            "timezone_name": row.get("timezone_name") if isinstance(row.get("timezone_name"), str) else None,
            "account_status": status if isinstance(status, int) and not isinstance(status, bool) else None}


def _minimum_budgets_for(value, currency) -> dict:
    """`minimum_budgets` から口座の通貨の行だけ（`{"rows": [...], "of": 全部の行の数}`）。"""
    rows = value if isinstance(value, list) else []
    mine = [json.loads(json.dumps(row)) for row in rows
            if isinstance(row, dict) and currency and row.get("currency") == currency][:5]
    return {"currency": currency, "rows": mine, "of": len(rows)}


def targeting_sha(value) -> str | None:
    """`targeting` の sha（並びを決めた JSON・digest の材料）。無ければ None。"""
    if not isinstance(value, dict):
        return None
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _node(row: dict) -> dict:
    out = {}
    for key in ("id", "name", "account_id", "objective", "campaign_id", "adset_id", "status", "effective_status",
                "start_time", "stop_time", "end_time"):
        value = row.get(key)
        if isinstance(value, str):
            out[key] = value[:200]
    for key in ("lifetime_budget", "daily_budget"):
        if key in row:
            out[key] = minor(row.get(key))
    if "targeting" in row:
        targeting = row.get("targeting")
        out["targeting_sha"] = targeting_sha(targeting)
        platforms = targeting.get("publisher_platforms") if isinstance(targeting, dict) else None
        out["publisher_platforms"] = (sorted(p for p in platforms if isinstance(p, str))
                                      if isinstance(platforms, list) else None)
    creative = row.get("creative")
    if isinstance(creative, dict):
        out["creative"] = {k: creative[k][:200] for k in ("id", "object_story_id", "source_instagram_media_id")
                           if isinstance(creative.get(k), str)}
    return out
