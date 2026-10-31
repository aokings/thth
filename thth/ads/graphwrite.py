"""広告の書く口（計画 段 5・D-1〜D-4）。**Meta に書く要求はこの包みの `_post()` 1 か所だけ。**

`tests/test_meta_stage5.py` が縛ること:

- `thth/ads/` の綴りで `.request("POST"` はここの `_post()` の 1 回だけ。DELETE・PUT・PATCH はどこにも無い。
- ここの公開の関数（下の表）を呼んでよいのは `ops.py` の決まった関数だけ（`ALLOWED_CALLERS`）。
- 作る口は **status=PAUSED に決め打ち**（引数で変えられない）。`ACTIVE` を書くのは `set_active()` だけで、
  呼ぶのは `ops.activate` だけ。
- `spend_cap` は**下げる**だけ（`lower_spend_cap()`）。0（Meta では「上限なし」）と今以上の値は書かない。

| 関数 | 書くもの | 呼ぶ所 | 量の制限の待ち |
|---|---|---|---|
| `create_campaign`・`create_adset`・`create_creative`・`create_ad` | 作る（全部 `PAUSED`） | `ops.boost` | 待つ |
| `set_active` | `status=ACTIVE` | `ops.activate` | 待つ |
| `set_paused` | `status=PAUSED` | `ops.pause_targets`（`pause`・`stop`） | **待たない** |
| `set_adset_budget` | 通しの予算・日予算 | `ops.lower_budget`（減らす・待たない）・`ops.extend`（増やす・待つ） | 呼び手が決める |
| `set_adset_end` | 終了日 | `ops.end`（早める・待たない）・`ops.extend`（延ばす・待つ） | 呼び手が決める |
| `lower_spend_cap` | `spend_cap`（下げるだけ） | `ops.stop` | **待たない** |

**金額**: THTH の中は最小単位の整数。Meta に書く値への直しは `write_amount()` の 1 か所（B-13・D-3）で、
**JPY だけ**（円は小数を持たないので、最小単位の整数＝普通の単位）。ほかの通貨は書かない
（`ads_currency_unverified`）。本番 10/30 に `spend_cap` 30000＝管理画面の 30,000 円を確かめた（G-1）。

作る口の欄（**L2**: developers.facebook.com/docs/marketing-api/get-started/basic-ad-creation ・
reference/ad-campaign-group・reference/ad-campaign・reference/ad-creative。本物での確かめは段 5 の本番・
G-11・G-13）。`special_ad_categories=[]`・`is_adset_budget_sharing_enabled=false`（広告セットの予算を使う
キャンペーンで v24 から要る【推】）は、違っていれば Meta が 4xx で断るだけ（`PAUSED` で作る所なので
お金は動かない）。
"""
from __future__ import annotations

import json
import urllib.error

from ..adapters import base
from ..meta import errors as meta_errors
from ..meta import graph as meta_graph
from . import graphread, ledger

PAUSED = "PAUSED"
ACTIVE = "ACTIVE"
# 作る口の目的（D-2・段 5 の 2）。名前 → (objective, optimization_goal)。
OBJECTIVES = {"engagement": ("OUTCOME_ENGAGEMENT", "POST_ENGAGEMENT"),
              "traffic": ("OUTCOME_TRAFFIC", "LINK_CLICKS")}
# 広告セットの destination_type。OUTCOME_ENGAGEMENT＋POST_ENGAGEMENT は ON_POST が無いと
# 「この目的では選んだ成果の目標を使えない」（code 100・subcode 2490408）で断られる
# （2026-10-31 本番の口座で validate_only で確かめた）。traffic は本番で未確認。
DESTINATIONS = {"engagement": "ON_POST"}
# 公開の関数 → 呼んでよい `ops.py` の関数（試験がこの表と実際の呼び出しの一致を縛る）。
ALLOWED_CALLERS = {
    "create_campaign": {"boost"}, "create_adset": {"boost"}, "create_creative": {"boost"},
    "create_ad": {"boost"},
    "set_active": {"activate"},
    "set_paused": {"pause_targets"},
    "set_adset_budget": {"lower_budget", "extend"},
    "set_adset_end": {"end", "extend"},
    "lower_spend_cap": {"stop"},
}


class AdsWriteError(base.AdapterError):
    """書けなかった。`code` は静的な符丁（本文・鍵は入れない）。`failure` は 4 つの語のどれか
    （`definite`＝Meta が断った・変わっていない／`ambiguous`＝変わったか分からない ほか）。"""

    def __init__(self, code, *, failure=None, http_status=None, graph_code=None, subcode=None):
        super().__init__(code)
        self.code = code
        self.failure = failure
        self.http_status = http_status
        self.graph_code = graph_code
        self.subcode = subcode


def write_amount(amount_minor: int, currency: str | None) -> str:
    """最小単位の整数 → Meta に書く値。**JPY だけ**（ほかは `ads_currency_unverified`）。"""
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor <= 0:
        raise AdsWriteError("ads_amount_invalid", failure=meta_errors.DEFINITE)
    if currency not in ledger.WRITE_CURRENCIES:
        raise AdsWriteError(ledger.CURRENCY_UNVERIFIED, failure=meta_errors.DEFINITE)
    return str(amount_minor)


class Writer:
    """1 つの広告の口座に書く。鍵はその口座の `.token`。"""

    def __init__(self, name: str, cfg: dict, token: dict, *, base_url: str | None = None,
                 app_secret: str | None = None):
        access = token.get("access_token") if isinstance(token, dict) else None
        if not isinstance(access, str) or not access:
            raise AdsWriteError("ads_token_missing", failure=meta_errors.DEFINITE)
        refusal = ledger.write_refusal(cfg)
        if refusal:
            raise AdsWriteError(refusal, failure=meta_errors.DEFINITE)
        self.name = name
        self.cfg = cfg
        self.act = "act_" + cfg["act_id"]
        self.currency = cfg.get("currency")
        if app_secret is None:
            app_secret = meta_graph.load_app_secret()
        self.client = meta_graph.Client(base_url or meta_graph.origin(), access, app_secret=app_secret,
                                        usage_path=ledger.usage_path_for(name))
        ledger.ensure_state_dir(name)

    # ------------------------------------------------------------------ 1 か所

    def _post(self, path: str, form: dict, *, urgent: bool) -> dict:
        """**唯一の書く口。** 失敗は静的な符丁の `AdsWriteError`。量の制限は `RateLimited` のまま上げる。"""
        try:
            body = self.client.request("POST", path, form=form, urgent=urgent)
        except meta_errors.RateLimited:
            raise
        except urllib.error.HTTPError as exc:
            err = meta_errors.graph_error(exc)
            code = err.get("code") if isinstance(err.get("code"), int) else None
            sub = err.get("error_subcode") if isinstance(err.get("error_subcode"), int) else None
            raise AdsWriteError(f"ads_http_{exc.code}" + (f"_code_{code}" if code is not None else ""),
                                failure=meta_errors.classify(exc.code, err), http_status=exc.code,
                                graph_code=code, subcode=sub) from None
        except (OSError, ValueError):
            raise AdsWriteError("ads_response_unavailable", failure=meta_errors.AMBIGUOUS) from None
        if not isinstance(body, dict):
            raise AdsWriteError("ads_response_invalid", failure=meta_errors.AMBIGUOUS)
        return body

    @staticmethod
    def _new_id(body: dict) -> str:
        value = body.get("id")
        if not isinstance(value, str) or not graphread.ID_RE.fullmatch(value):
            # 作れたかは分からない（応答に id が無い）。打ち直さない。
            raise AdsWriteError("ads_response_invalid", failure=meta_errors.AMBIGUOUS)
        return value

    @staticmethod
    def _check_id(object_id) -> str:
        if not isinstance(object_id, str) or not graphread.ID_RE.fullmatch(object_id):
            raise AdsWriteError("ads_id_invalid", failure=meta_errors.DEFINITE)
        return object_id

    # ------------------------------------------------------------------ 作る（全部 PAUSED）

    def create_campaign(self, *, name: str, objective: str) -> str:
        if objective not in OBJECTIVES:
            raise AdsWriteError("ads_objective_invalid", failure=meta_errors.DEFINITE)
        body = self._post(f"/{self.act}/campaigns", {
            "name": name[:200], "objective": OBJECTIVES[objective][0], "status": PAUSED,
            "special_ad_categories": "[]", "is_adset_budget_sharing_enabled": "false"}, urgent=False)
        return self._new_id(body)

    def create_adset(self, *, name: str, campaign_id: str, lifetime_minor: int, start: str, end: str,
                     objective: str, targeting: dict, page_id: str) -> str:
        if not end:
            raise AdsWriteError("ads_end_time_required", failure=meta_errors.DEFINITE)
        body = self._post(f"/{self.act}/adsets", {
            "name": name[:200], "campaign_id": self._check_id(campaign_id),
            "lifetime_budget": write_amount(lifetime_minor, self.currency),
            "start_time": start, "end_time": end,
            "billing_event": "IMPRESSIONS", "optimization_goal": OBJECTIVES[objective][1],
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "promoted_object": json.dumps({"page_id": self._check_id(page_id)}),
            "targeting": json.dumps(targeting, ensure_ascii=False, separators=(",", ":")),
            "status": PAUSED, **({"destination_type": DESTINATIONS[objective]} if objective in DESTINATIONS else {})},
            urgent=False)
        return self._new_id(body)

    def create_creative(self, *, name: str, object_story_id: str, instagram_user_id: str | None = None) -> str:
        form = {"name": name[:200], "object_story_id": object_story_id}
        if instagram_user_id:
            form["instagram_user_id"] = self._check_id(instagram_user_id)
        body = self._post(f"/{self.act}/adcreatives", form, urgent=False)
        return self._new_id(body)

    def create_ad(self, *, name: str, adset_id: str, creative_id: str) -> str:
        body = self._post(f"/{self.act}/ads", {
            "name": name[:200], "adset_id": self._check_id(adset_id),
            "creative": json.dumps({"creative_id": self._check_id(creative_id)}), "status": PAUSED}, urgent=False)
        return self._new_id(body)

    # ------------------------------------------------------------------ 始める（ACTIVE を書く唯一の口）

    def set_active(self, object_id: str) -> None:
        self._post("/" + self._check_id(object_id), {"status": ACTIVE}, urgent=False)

    # ------------------------------------------------------------------ 止める（待たない）

    def set_paused(self, object_id: str) -> None:
        self._post("/" + self._check_id(object_id), {"status": PAUSED}, urgent=True)

    # ------------------------------------------------------------------ 予算・終了日

    def set_adset_budget(self, adset_id: str, *, lifetime_minor: int | None = None,
                         daily_minor: int | None = None, urgent: bool) -> None:
        form = {}
        if lifetime_minor is not None:
            form["lifetime_budget"] = write_amount(lifetime_minor, self.currency)
        if daily_minor is not None:
            form["daily_budget"] = write_amount(daily_minor, self.currency)
        if not form:
            raise AdsWriteError("ads_nothing_to_write", failure=meta_errors.DEFINITE)
        self._post("/" + self._check_id(adset_id), form, urgent=urgent)

    def set_adset_end(self, adset_id: str, end: str, *, urgent: bool) -> None:
        if not isinstance(end, str) or not end:
            raise AdsWriteError("ads_end_time_required", failure=meta_errors.DEFINITE)
        self._post("/" + self._check_id(adset_id), {"end_time": end}, urgent=urgent)

    # ------------------------------------------------------------------ spend_cap（下げるだけ）

    def lower_spend_cap(self, amount_minor: int, *, current_minor: int | None) -> None:
        """`spend_cap` を**下げる**。0（上限なし＝上げるのと同じ）と、今の値以上は書かない（D-1 の 1）。

        今の値が 0（上限なし）なら、正の値はどれも「下げる」。
        """
        if isinstance(amount_minor, bool) or not isinstance(amount_minor, int) or amount_minor <= 0:
            raise AdsWriteError("ads_spend_cap_zero_refused", failure=meta_errors.DEFINITE)
        if not isinstance(current_minor, int) or isinstance(current_minor, bool) or current_minor < 0:
            raise AdsWriteError("ads_spend_cap_unknown", failure=meta_errors.DEFINITE)
        if current_minor != 0 and amount_minor >= current_minor:
            raise AdsWriteError("ads_not_lower", failure=meta_errors.DEFINITE)
        self._post("/" + self.act, {"spend_cap": write_amount(amount_minor, self.currency)}, urgent=True)
