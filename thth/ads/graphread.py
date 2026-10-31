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
- `/act_<id>/insights`: `spend`（**普通の単位の小数の文字列**【推】）・`impressions`・`clicks`・`reach`。
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
INSIGHT_FIELDS = "spend,impressions,clicks,reach"
# 段 5: 1 つのキャンペーンの木（digest の材料・D-2）。`account_id` で口座の物かを確かめる。
CAMPAIGN_TREE_FIELDS = ("id,name,account_id,objective,status,effective_status,lifetime_budget,daily_budget,"
                        "start_time,stop_time")
ADSET_TREE_FIELDS = ("id,name,campaign_id,status,effective_status,lifetime_budget,daily_budget,"
                     "start_time,end_time,targeting")
AD_TREE_FIELDS = ("id,name,campaign_id,adset_id,status,effective_status,"
                  "creative{id,object_story_id,source_instagram_media_id}")
ID_RE = re.compile(r"[0-9]{1,32}")
ADACCOUNTS_FIELDS = "id,account_id,name,currency,timezone_name,account_status"
# `date_preset`（L2: insights の参照の一覧から、THTH が使う物だけ）。
PRESETS = ("today", "yesterday", "last_7d", "last_14d", "last_30d", "this_month", "last_month")
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

    def insights(self, preset: str = "last_30d", *, level: str = "account") -> list:
        """`/act_<id>/insights`。**前回の使用率が線を越えていれば呼ばない**（`ads_insights_throttled`）。"""
        if preset not in PRESETS:
            raise AdsReadError("ads_preset_invalid")
        if level not in LEVELS:
            raise AdsReadError("ads_level_invalid")
        pct = self.insights_util_pct()
        if pct is not None and pct >= INSIGHTS_THROTTLE_PCT:
            raise AdsReadError("ads_insights_throttled", failure=meta_errors.RATE_LIMITED)
        fields = INSIGHT_FIELDS + ("" if level == "account" else f",{level}_id,{level}_name")
        rows, _ = self._list(f"/{self.act}/insights",
                             {"fields": fields, "date_preset": preset, "level": level})
        currency = self.cfg.get("currency")
        out = []
        for row in rows:
            item = {"spend": row.get("spend") if isinstance(row.get("spend"), str) else None,
                    "spend_minor": spend_minor(row.get("spend"), currency),
                    "impressions": _count(row.get("impressions")),
                    "clicks": _count(row.get("clicks")),
                    "reach": _count(row.get("reach")),
                    "date_start": row.get("date_start") if isinstance(row.get("date_start"), str) else None,
                    "date_stop": row.get("date_stop") if isinstance(row.get("date_stop"), str) else None}
            if level != "account":
                item["id"] = row.get(f"{level}_id")
                item["name"] = row.get(f"{level}_name")
            out.append(item)
        return out

    def adaccounts(self) -> list:
        """鍵で見える広告アカウント（`/me/adaccounts`）。"""
        rows, _ = self._list("/me/adaccounts", {"fields": ADACCOUNTS_FIELDS})
        return [adaccount_row(row) for row in rows]


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
