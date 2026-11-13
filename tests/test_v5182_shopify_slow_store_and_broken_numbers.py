"""5.18.2: Shopify の読みの待ち（監査 5.18.1 の「直しの確かめ（9919b8a・d4ae20b）」の直したほうがよい 10 と 11）。

10. 点の戻りが毎秒 50 点の店（バケツ 1,000 点）では、頁 1 つの頼む点（728）が戻るのに 14.6 秒かかる。1 回 10 秒・
    合わせて 180 秒の上限では、混んでいなくても種類の多い商品を読めず、断りは「店の大きさだけでは届きません」と
    事実と違うことを言った。上限を応答の戻る速さから決める（1 回は長くて 30 秒・合わせては長くて 300 秒）。
11. 点の欄に壊れた数（NaN・無限大・桁の大きな整数）が来ると、`time.sleep` が ValueError・OverflowError で落ちた。
    外れた数は「読めない」（1 秒）に落とす。

送る先は偽の店（`tests/test_store_shopify.py` の土台）。本物の店には何も送らない。眠りは差し替えて数えるだけ。
"""
from __future__ import annotations

import json
import math

import pytest

from tests import test_store_shopify as sh
from tests.test_store_shopify import env  # noqa: F401
from tests.test_store_shopify_cost import _bucket
from thth import refusal
from thth.shop import cli as store_cli
from thth.shop import shopify, wire


def _read_500(capsys, env, monkeypatch, **bucket):
    env["products"] = [sh.product(i) for i in range(1, 501)]
    sh.setup_store(capsys, env)
    env["slept"] = []
    b = _bucket(monkeypatch, env, **bucket)
    rc_, value, err = sh.jrun(capsys, "shop", "items", sh.STORE)
    return rc_, value, err, b


@pytest.mark.parametrize("per_product", [29, 20, 10])
def test_柵_点の戻りが毎秒50点の店でも混んでいなければ種類20の商品500件を読み切る(capsys, env, monkeypatch,
                                                                    per_product):
    rc_, value, _, b = _read_500(capsys, env, monkeypatch, maximum=1000.0, restore=50.0, per_product=per_product)
    assert rc_ == 0, value
    assert value["complete"] is True and len(value["items"]) == 500
    assert b["throttled"] == 0                                    # 頁の間に足りない点が戻るのを待つ
    assert sum(env["slept"]) <= shopify.READ_PACE_SECONDS_CEIL


@pytest.mark.parametrize("maximum,restore,low,high", [(1000.0, 50.0, 3.5 * 60, 5.5 * 60),
                                                       (2000.0, 100.0, 1.5 * 60, 2.5 * 60)],
                         ids=["毎秒50点で5分ほど", "毎秒100点で2分ほど"])
def test_読む時間は断りの言う分ほど(capsys, env, monkeypatch, maximum, restore, low, high):
    """断りの目録の「毎秒 100 点の店で 2 分ほど・毎秒 50 点の店で 5 分ほど」と、偽の店の時計を結ぶ。"""
    rc_, value, _, b = _read_500(capsys, env, monkeypatch, maximum=maximum, restore=restore, per_product=29)
    assert rc_ == 0 and value["complete"] is True, value
    assert low <= b["clock"] <= high, b["clock"]
    fix = refusal.parts("store_unavailable", reason="read_budget")["fix"]
    assert "毎秒 100 点の店で 2 分ほど・毎秒 50 点の店で 5 分ほど" in fix
    assert "毎秒 50 点以上戻る店なら" in fix


def test_標準の店の1回の待ちは前と同じ10秒まで():
    payload = {"extensions": sh.cost(requested=728, available=0, restore=100.0)}
    assert shopify.throttle_wait(payload) == pytest.approx(7.78)
    for factor in (1.5, shopify.PACE_FACTOR_MAX):                 # 余裕を掛けても 1 回は 10 秒まで（前と同じ）
        assert shopify.throttle_wait(payload, factor) == shopify.MAX_THROTTLE_WAIT_SECONDS
    assert shopify.pace_limit(payload, 25) == pytest.approx((728 * 20 - 2000) / 100 * shopify.PACE_LIMIT_MARGIN)


def test_戻りの遅い店でも1回の待ちと合計には天井がある():
    slow = {"extensions": sh.cost(requested=728, available=0, restore=50.0)}
    assert shopify.throttle_wait(slow) == pytest.approx(728 / 50 + 0.5)            # 頼む点が戻るまで待つ
    assert shopify.throttle_wait(slow, shopify.PACE_FACTOR_MAX) == pytest.approx(728 / 50 + 0.5)
    payload = {"extensions": sh.cost(requested=728, available=0, restore=1.0)}
    assert shopify.throttle_wait(payload, shopify.PACE_FACTOR_MAX) == shopify.MAX_THROTTLE_WAIT_CEIL
    assert shopify.pace_limit(payload, 25) == shopify.READ_PACE_SECONDS_CEIL


def test_柵_戻りがとても遅い店は天井で打ち切り断りの3つがそろう(capsys, env, monkeypatch):
    rc_, value, err, _ = _read_500(capsys, env, monkeypatch, maximum=1000.0, restore=10.0, per_product=29)
    # 頁の間の待ちの天井（read_budget）か、天井まで待っても点が足りず混んだと言われ続ける（throttled）か、先に届いた方。
    assert rc_ == 2 and value["code"] == "store_unavailable" and value["detail"] in ("read_budget", "throttled"), value
    assert sum(env["slept"]) <= shopify.READ_PACE_SECONDS_CEIL + shopify.READ_WAIT_SECONDS_MAX
    assert value["why"] and value["fix"] and value["better"]


# ------------------------------------------------------------------ 壊れた数（直したほうがよい 11）

BROKEN = [
    ("restoreRate が NaN", {"restore": float("nan")}),
    ("restoreRate が無限大", {"restore": float("inf")}),
    ("restoreRate が負の無限大", {"restore": float("-inf")}),
    ("currentlyAvailable が桁の大きな負の整数", {"available": -10 ** 400}),
    ("currentlyAvailable が NaN", {"available": float("nan")}),
    ("requestedQueryCost が桁の大きな整数", {"requested": 10 ** 400}),
    ("requestedQueryCost が無限大", {"requested": float("inf")}),
    ("restoreRate が桁の大きな整数", {"restore": 10 ** 400}),
]


def _payload(**broken):
    base = {"requested": 728, "available": 100, "restore": 100.0}
    base.update(broken)
    return {"extensions": sh.cost(**base)}


@pytest.mark.parametrize("what,broken", BROKEN, ids=[b[0] for b in BROKEN])
def test_柵_点の欄が壊れた数でも待ちは有限で範囲の内(what, broken):
    payload = _payload(**broken)
    for factor in (1.0, shopify.PACE_FACTOR_MAX):
        wait = shopify.throttle_wait(payload, factor)
        assert math.isfinite(wait) and shopify.MIN_INTERVAL_SECONDS <= wait <= shopify.MAX_THROTTLE_WAIT_CEIL, what
    wait = shopify.throttled_wait(payload)
    assert math.isfinite(wait) and 1.0 <= wait <= shopify.MAX_THROTTLED_WAIT_SECONDS, what
    limit = shopify.pace_limit(payload, 25)
    assert math.isfinite(limit) and shopify.READ_PACE_SECONDS_MAX <= limit <= shopify.READ_PACE_SECONDS_CEIL, what


def test_眠る秒が壊れた数でも合計の上限は効く(monkeypatch):
    seen = []
    monkeypatch.setattr(shopify, "SLEEP", seen.append)
    client = shopify.Client({"shop": "x", "store": "x"}, {})
    client._pace(float("nan"))
    client._sleep(float("nan"))
    assert all(math.isfinite(s) for s in seen) and math.isfinite(client.paced) and math.isfinite(client.slept)


def _nan_store(monkeypatch, env, ext_text):
    """頁ごとの応答の extensions を字のまま差し替える（Python の json.loads は NaN と桁の大きな整数を読む）。"""
    real = store_cli.TRANSPORT

    def transport(method, url, headers, body=None):
        status, raw = real(method, url, headers, body)
        if body is not None and url.endswith(sh.GQL_PATH) and json.loads(body.decode())["query"] == wire.QUERIES["products"]:
            out = json.loads(raw.decode())
            out.pop("extensions", None)
            text = json.dumps(out)
            return status, (text[:-1] + ', "extensions": ' + ext_text + "}").encode()
        return status, raw

    monkeypatch.setattr(store_cli, "TRANSPORT", transport)


@pytest.mark.parametrize("ext_text", [
    '{"cost": {"requestedQueryCost": 728, "throttleStatus": {"maximumAvailable": 2000.0, '
    '"currentlyAvailable": 100, "restoreRate": NaN}}}',
    '{"cost": {"requestedQueryCost": 728, "throttleStatus": {"maximumAvailable": 2000.0, '
    '"currentlyAvailable": -1' + "0" * 400 + ', "restoreRate": 100.0}}}',
    '{"cost": {"requestedQueryCost": 1' + "0" * 400 + ', "throttleStatus": {"maximumAvailable": 2000.0, '
    '"currentlyAvailable": 100, "restoreRate": 100.0}}}',
], ids=["restoreRate が NaN", "currentlyAvailable が桁の大きな整数", "requestedQueryCost が桁の大きな整数"])
def test_柵_壊れた数の応答でもshop_itemsは例外で落ちず読み切る(capsys, env, monkeypatch, ext_text):
    env["products"] = [sh.product(i) for i in range(1, 8)]
    sh.setup_store(capsys, env)
    env["page_size"] = 3
    env["slept"] = []
    _nan_store(monkeypatch, env, ext_text)
    rc_, value, err = sh.jrun(capsys, "shop", "items", sh.STORE)
    assert rc_ == 0, (value, err)
    assert value["complete"] is True and len(value["items"]) == 7
    assert "Traceback" not in err
    assert all(math.isfinite(s) and 0 < s <= shopify.MAX_THROTTLE_WAIT_CEIL for s in env["slept"]), env["slept"]
