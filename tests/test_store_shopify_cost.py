"""Shopify の商品の問い合わせの点数（query cost）と、見込みが外れたときの読み方（5.18.1）。

柵:
  - 点数は `wire.QUERIES["products"]` の文そのものから、公式の決まりで数え直す（数え方と出所は `thth/shop/shopify.py`
    の頭）。scalar と enum は 0・選びのある欄（物・interface）は 1・`first` のある欄は connection で
    「2 ＋ first ×（物 1 ＋ その中の点）」・pageInfo は多めに見て 1。頁 25 件で 728 点、上限の 8 割（800）の内。
    欄を足して点が増えれば、ここが赤になる。
  - 見込みが外れても止まらない: `MAX_COST_EXCEEDED` は `extensions.cost` との比で頁を小さくして同じ文で打ち直す
    （読めなければ半分）・`INTERNAL_SERVER_ERROR` は半分・応答の `requestedQueryCost` が 800 を超えれば次の頁から
    小さく・`THROTTLED` は足りない点が戻るまで待って 2 回まで。どれも回数に上限があり、上限のあとは前向きに断る。
  - 1 回の読み全体にも上限（監査 5.18.1 の 2）: 打ち直しの和は `READ_RESENDS_MAX`・眠る秒の和は `READ_WAIT_SECONDS_MAX`。
    どの頁も混んでいる店でも、そこで `store_unavailable（read_budget）` で断る。承認の読み直しも同じ。
  - 打ち直しも柵を通る（文は同じ・変数は first と after だけ・first は 1〜50）。読める商品の数 500 は頁を小さくしても保つ。
本物の店は呼ばない（偽の transport・`tests/test_store_shopify.py` の env）。
"""
from __future__ import annotations

import json
import re

import pytest

from thth import refusal
from thth.shop import cli as store_cli
from thth.shop import shopify, wire
from tests import test_store_shopify as sh
from tests.test_store_shopify import env  # noqa: F401 — Shopify の偽の店（同じ fixture を使う）

# ------------------------------------------------------------------ 文から点を数える（公式の決まり）

_TOKEN = re.compile(r"\$?[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[{}():,!]")


def _parse(tokens, i):
    """選びの集まり `{ … }` を読む。`[(名前, 引数の dict, 子の選び or None)]` と次の位置。"""
    assert tokens[i] == "{"
    i += 1
    fields = []
    while tokens[i] != "}":
        name = tokens[i]
        i += 1
        args = {}
        if tokens[i] == "(":
            i += 1
            while tokens[i] != ")":
                key = tokens[i]
                assert tokens[i + 1] == ":"
                args[key] = tokens[i + 2]
                i += 3
                if tokens[i] == ",":
                    i += 1
            i += 1
        children = None
        if tokens[i] == "{":
            children, i = _parse(tokens, i)
        fields.append((name, args, children))
    return fields, i + 1


def _cost(fields, variables, page_info):
    total = 0
    for name, args, children in fields:
        if children is None:
            continue                                   # scalar・enum は 0 点
        if name == "pageInfo":
            total += page_info                         # 記事では無料・今の表では物（多めに 1）
            continue
        if "first" in args:
            size = args["first"]
            size = variables[size[1:]] if size.startswith("$") else int(size)
            per = 0
            rest = []
            for child in children:
                if child[0] in ("nodes", "edges"):
                    inner = child[2]
                    if child[0] == "edges":          # edges { node { … } cursor }
                        inner = next(c[2] for c in inner if c[0] == "node")
                    per += 1 + _cost(inner, variables, page_info)
                else:
                    rest.append(child)
            total += 2 + size * per + _cost(rest, variables, page_info)
            continue
        total += 1 + _cost(children, variables, page_info)   # 物・interface は 1 点＋中の点
    return total


def requested_cost(text, first, *, page_info=1):
    tokens = _TOKEN.findall(text)
    start = tokens.index("{")
    fields, _ = _parse(tokens, start)
    return _cost(fields, {"first": first}, page_info)


def test_公式の例の数え方が合う():
    # 公式の記事: 5 つ返すと見込む connection は 7 点・shop は 1 点。公式の頁の例: products(first: 1){edges{node{title}}} は 3。
    assert requested_cost("query { products(first: 5) { edges { node { title } } } }", 5, page_info=0) == 7
    assert requested_cost("query { shop { id name } }", 1) == 1
    assert requested_cost("{ products(first: 1) { edges { node { title } } } }", 1, page_info=0) == 3


def test_商品の問い合わせは文から数えて728点で上限の8割の内():
    text = wire.QUERIES["products"]
    assert requested_cost(text, shopify.PAGE_FIRST) == 728          # pageInfo を 1 点と見た多めの数
    assert requested_cost(text, shopify.PAGE_FIRST, page_info=0) == 702
    assert shopify.COST_BUDGET == 800 == wire.SHOPIFY_QUERY_COST_MAX * 8 // 10
    assert requested_cost(text, shopify.PAGE_FIRST) <= shopify.COST_BUDGET
    # 1 商品 29 点（頭の説明の数え方）。種類の数を増やせば頁を小さくしないと上限を超える。
    assert requested_cost(text, 2) - requested_cost(text, 1) == 6 + 3 + shopify.VARIANTS_FIRST == 29
    # 読める商品の数は 500 のまま・頁を最小にしても 500 まで読める頁の数がある。
    assert shopify.MAX_ITEMS == 500 and shopify.MIN_PAGE_FIRST * shopify.MAX_PAGES >= shopify.MAX_ITEMS
    assert shopify.PAGE_FIRST * 20 >= shopify.MAX_ITEMS


def test_頁を小さくする大きさ():
    assert shopify.smaller_first(25, 2003) == 9          # 25 × 800 / 2003
    assert shopify.smaller_first(25, None) == 12          # 点が読めなければ半分
    assert shopify.smaller_first(25, 801) == 24           # 必ず前より小さい
    assert shopify.smaller_first(9, 100) == 8
    for first in (9, 12, 24):
        assert requested_cost(wire.QUERIES["products"], first) <= shopify.COST_BUDGET


# ------------------------------------------------------------------ 見込みが外れたとき（偽の店）

def _errors(code, **extensions):
    return json.dumps({"errors": [{"message": "secret-message-marker", "extensions": {"code": code, **extensions}}],
                       "extensions": {"cost": {"requestedQueryCost": 728, "actualQueryCost": 0,
                                               "throttleStatus": {"maximumAvailable": 2000.0,
                                                                  "currentlyAvailable": 128,
                                                                  "restoreRate": 100.0}}}}).encode()


def _inject(monkeypatch, env, bodies, cost_per_first=None):
    """商品の問い合わせの前から順に、偽の 200 の誤りを返す（尽きたら偽の店のまま）。打った first を env に残す。"""
    real = store_cli.TRANSPORT
    env["firsts"] = []

    def transport(method, url, headers, body=None):
        if body is not None and url.endswith(sh.GQL_PATH):
            payload = json.loads(body.decode())
            if payload["query"] == wire.QUERIES["products"]:
                wire.check_request(method, url, body)     # 打ち直しも同じ柵
                assert set(payload.get("variables", {})) <= {"first", "after"}
                env["firsts"].append(payload["variables"]["first"])
                if cost_per_first:
                    env["extensions"] = sh.cost(requested=cost_per_first * payload["variables"]["first"])
                if bodies:
                    return 200, bodies.pop(0)
        return real(method, url, headers, body)

    monkeypatch.setattr(store_cli, "TRANSPORT", transport)


def test_MAX_COST_EXCEEDEDは点の比で頁を小さくし同じ文で読み続ける(capsys, env, monkeypatch):
    env["products"] = [sh.product(i) for i in range(1, 31)]
    sh.setup_store(capsys, env)
    _inject(monkeypatch, env, [_errors("MAX_COST_EXCEEDED", cost=2003, maxCost=1000)])
    rc_, value, _ = sh.jrun(capsys, "shop", "items", sh.STORE)
    assert rc_ == 0 and value["complete"] is True and len(value["items"]) == 30
    assert env["firsts"] == [25, 9, 9, 9, 9]
    assert value["read_adjusted"]["shrinks"] == [{"from": 25, "to": 9, "why": "max_cost_exceeded"}]
    assert "25 件から 9 件に減らして" in value["read_note"] and "secret-message-marker" not in json.dumps(value)
    _, out, _ = sh.run(capsys, "shop", "items", sh.STORE)
    assert "減らして" not in out                               # 次の読みは元の頁の大きさから・何も無ければ黙る


def test_点が読めなければ半分にし内部の誤りも半分にする(capsys, env, monkeypatch):
    env["products"] = [sh.product(i) for i in range(1, 31)]
    sh.setup_store(capsys, env)
    _inject(monkeypatch, env, [_errors("MAX_COST_EXCEEDED"), _errors("INTERNAL_SERVER_ERROR")])
    rc_, value, _ = sh.jrun(capsys, "shop", "items", sh.STORE)
    assert rc_ == 0 and len(value["items"]) == 30
    assert env["firsts"][:3] == [25, 12, 6]
    assert [s["why"] for s in value["read_adjusted"]["shrinks"]] == ["max_cost_exceeded", "internal_error"]


@pytest.mark.parametrize("code,want,detail", [("MAX_COST_EXCEEDED", "store_api_changed", "max_cost_exceeded"),
                                              ("INTERNAL_SERVER_ERROR", "store_unavailable", "internal_error")])
def test_小さくしても続けば回数の上限で前向きに断る(capsys, env, monkeypatch, code, want, detail):
    sh.setup_store(capsys, env)
    _inject(monkeypatch, env, [_errors(code)] * 10)
    rc_, value, err = sh.jrun(capsys, "shop", "items", sh.STORE)
    assert rc_ == 2 and value["code"] == want and value["detail"] == detail
    assert env["firsts"] == [25, 12, 6]                         # 小さくするのは 2 回まで
    assert value["why"] and value["fix"] and value["better"]
    found = refusal.parts(want, reason=detail)
    assert found["fix"] == value["fix"] and "thth report file" in found["fix"] + found["better"]
    assert "secret-message-marker" not in json.dumps(value) + err


def test_小さくするのは5件より上でも2回まで(capsys, env, monkeypatch):
    sh.setup_store(capsys, env)
    _inject(monkeypatch, env, [_errors("MAX_COST_EXCEEDED", cost=900)] * 10)
    rc_, value, _ = sh.jrun(capsys, "shop", "items", sh.STORE)
    assert rc_ == 2 and value["detail"] == "max_cost_exceeded" and env["firsts"] == [25, 22, 19]


def test_頁を小さくしても5件より下にはしない(capsys, env, monkeypatch):
    sh.setup_store(capsys, env)
    _inject(monkeypatch, env, [_errors("MAX_COST_EXCEEDED", cost=100_000)] * 3)
    rc_, value, _ = sh.jrun(capsys, "shop", "items", sh.STORE)
    assert rc_ == 2 and value["detail"] == "max_cost_exceeded" and env["firsts"] == [25]


def test_THROTTLEDは足りない点が戻るまで待って2回まで打ち直す(capsys, env, monkeypatch):
    sh.setup_store(capsys, env)
    _inject(monkeypatch, env, [_errors("THROTTLED")] * 2)
    rc_, value, _ = sh.jrun(capsys, "shop", "items", sh.STORE)
    assert rc_ == 0 and len(value["items"]) == 3 and env["firsts"] == [25, 25, 25]
    assert env["slept"] == [6.0, 6.0]                           # (728 − 128) / 100
    assert value["read_adjusted"]["throttled_retries"] == 2 and "2 回待って" in value["read_note"]
    _inject(monkeypatch, env, [_errors("THROTTLED")] * 3)
    rc_, value, _ = sh.jrun(capsys, "shop", "items", sh.STORE)
    assert rc_ == 2 and value["code"] == "store_unavailable" and value["detail"] == "throttled"
    assert env["firsts"] == [25, 25, 25] and "1 分ほど置いて" in value["fix"]


def test_待ちは読めなければ1秒で長くても10秒():
    assert shopify.throttled_wait({}) == 1.0
    assert shopify.throttled_wait({"extensions": sh.cost(requested=728, available=700, restore=100.0)}) == 1.0
    assert shopify.throttled_wait({"extensions": sh.cost(requested=728, available=0, restore=50.0)}) == 10.0


def test_応答の頼んだ点が800を超えれば次の頁から小さくする(capsys, env, monkeypatch):
    env["products"] = [sh.product(i) for i in range(1, 41)]
    sh.setup_store(capsys, env)
    _inject(monkeypatch, env, [], cost_per_first=64)            # 25 件で 1600 点（手で付けた点が多い店）
    rc_, value, _ = sh.jrun(capsys, "shop", "items", sh.STORE)
    assert rc_ == 0 and len(value["items"]) == 40
    assert env["firsts"] == [25, 12, 12]                        # 打ち直しは無い・12 件で 768 点
    assert value["read_adjusted"]["requested_cost_max"] == 1600
    assert value["read_adjusted"]["shrinks"] == [{"from": 25, "to": 12, "why": "requested_cost"}]


def test_頁を小さくしても500件まで読み501件目があれば読み切れない(capsys, env, monkeypatch):
    env["products"] = [sh.product(i) for i in range(1, 501)]
    sh.setup_store(capsys, env)
    _inject(monkeypatch, env, [_errors("MAX_COST_EXCEEDED", cost=2003)])
    rc_, value, _ = sh.jrun(capsys, "shop", "items", sh.STORE)
    assert rc_ == 0 and value["complete"] is True and len(value["items"]) == 500
    assert max(env["firsts"][1:]) == 9 and sum(env["firsts"][1:]) == 500     # 最後の頁は残りの数だけ頼む
    env["products"].append(sh.product(501))
    _inject(monkeypatch, env, [_errors("MAX_COST_EXCEEDED", cost=2003)])
    rc_, value, _ = sh.jrun(capsys, "shop", "items", sh.STORE)
    assert rc_ == 0 and value["complete"] is False and len(value["items"]) == 500
    assert "500 件の上限" in value["note"]


# ------------------------------------------------------------------ 1 回の読み全体の上限（監査 5.18.1 直したほうがよい 2）

def _every_page_throttled(monkeypatch, env, times):
    """どの頁も `times` 回 `THROTTLED` を返してから通す偽の店。打った回数と頁を env に残す。"""
    real = store_cli.TRANSPORT
    env["firsts"], env["afters"], seen = [], [], {}

    def transport(method, url, headers, body=None):
        if body is not None and url.endswith(sh.GQL_PATH):
            payload = json.loads(body.decode())
            if payload["query"] == wire.QUERIES["products"]:
                wire.check_request(method, url, body)
                after = payload["variables"].get("after")
                env["firsts"].append(payload["variables"]["first"])
                env["afters"].append(after)
                seen[after] = seen.get(after, 0) + 1
                if seen[after] <= times:
                    return 200, _errors("THROTTLED")
        return real(method, url, headers, body)

    monkeypatch.setattr(store_cli, "TRANSPORT", transport)


def test_柵_どの頁も混んでいる店でも1回の読みの打ち直しは合わせて上限まで(capsys, env, monkeypatch):
    env["products"] = [sh.product(i) for i in range(1, 501)]
    sh.setup_store(capsys, env)
    _every_page_throttled(monkeypatch, env, 2)
    rc_, value, err = sh.jrun(capsys, "shop", "items", sh.STORE)
    assert rc_ == 2 and value["code"] == "store_unavailable" and value["detail"] == "read_budget"
    pages = len(set(env["afters"]))
    assert len(env["firsts"]) - pages <= shopify.READ_RESENDS_MAX         # 打ち直しは読み全体で上限まで
    assert sum(env["slept"]) <= shopify.READ_WAIT_SECONDS_MAX
    assert value["why"] and value["fix"] and value["better"]
    found = refusal.parts("store_unavailable", reason="read_budget")
    assert found["fix"] == value["fix"]
    n, seconds = shopify.READ_RESENDS_MAX, int(shopify.READ_WAIT_SECONDS_MAX)
    assert f"合わせて {n} 回まで" in found["fix"] and f"合わせて {seconds} 秒まで" in found["fix"]
    pace, ceil = int(shopify.READ_PACE_SECONDS_MAX), int(shopify.READ_PACE_SECONDS_CEIL)
    assert f"合わせて {n} 回まで・混んでいると言われて待つのは合わせて {seconds} 秒まで" in found["why"]
    assert f"頁の間に点の戻りを待つのは合わせて {pace} 秒まで（点の戻りが遅い店では、戻る速さに合わせて長くて {ceil} 秒まで）" in found["why"]
    assert f"合わせて {pace} 秒まで・点の戻りが遅い店では長くて {ceil} 秒まで" in found["fix"]
    # 5.18.2（監査 5.18.1 の確かめの直したほうがよい 10）: 前の「店の大きさだけでは届きません」は毎秒 50 点の店で事実と
    # 違った。今は言える条件（毎秒 50 点以上・混んでいない）を添えて言う（数は test_v5182_shopify_slow_store… が結ぶ）。
    assert "店の大きさだけでは届きません" not in found["fix"] and "毎秒 50 点以上戻る店なら" in found["fix"]
    assert "secret-message-marker" not in json.dumps(value) + err


def test_柵_頁の間の待ちが重なっても1回の読みで眠る合計は別の上限まで(capsys, env, monkeypatch):
    """頁の間の待ちは混んだせいの待ち（60 秒）には数えないが、別の上限で止まる。上限は戻る速さで
    `READ_PACE_SECONDS_MAX` から広がるが、長くて `READ_PACE_SECONDS_CEIL`（5.18.2）。"""
    env["products"] = [sh.product(i) for i in range(1, 501)]
    sh.setup_store(capsys, env)
    env["extensions"] = sh.cost(requested=728, available=0, restore=10.0)    # 頁の間はいつも 10 秒
    rc_, value, _ = sh.jrun(capsys, "shop", "items", sh.STORE)
    assert rc_ == 2 and value["code"] == "store_unavailable" and value["detail"] == "read_budget"
    assert sum(env["slept"]) <= shopify.READ_PACE_SECONDS_CEIL


def test_上限は小さくした頁で500件を読み切る分は通す(capsys, env, monkeypatch):
    """頁を 5 件まで小さくしても、点数に余りのある店なら 500 件を最後まで読める（頁の間 0.5 秒 × 99）。"""
    assert (shopify.MAX_PAGES - 1) * shopify.MIN_INTERVAL_SECONDS <= shopify.READ_PACE_SECONDS_MAX
    assert 0 < shopify.READ_RESENDS_MAX <= 4 and shopify.READ_WAIT_SECONDS_MAX <= 60


def test_頁の間の待ちは混んだせいの待ちに数えない(monkeypatch):
    """`_pace` は `slept`（60 秒の上限）を増やさず、`_sleep` だけが増やす（privacy の文と結ぶ）。"""
    seen = []
    monkeypatch.setattr(shopify, "SLEEP", seen.append)
    client = shopify.Client({"shop": "x", "store": "x"}, {})
    client._pace(5.0)
    assert client.slept == 0.0 and client.paced == 5.0
    client._sleep(2.0)
    assert client.slept == 2.0 and client.paced == 5.0 and seen == [5.0, 2.0]


def test_承認の読み直しも1回の読みの上限で止まる(capsys, env, monkeypatch):
    from thth.shop import recheck
    env["products"] = [sh.product(i) for i in range(1, 501)]
    sh.setup_store(capsys, env)
    _every_page_throttled(monkeypatch, env, 2)
    front = {"from_store": f"store/{sh.STORE}/item/1", "from_store_price": "1200"}
    line, _ = recheck.check("x.md", front)
    assert line and "store_unavailable" in line and "数分置いて" in line
    assert sum(env["slept"]) <= shopify.READ_WAIT_SECONDS_MAX


# ------------------------------------------------------------------ 混んでいない店は上限で止めない（監査 5.18.1 確かめの危ない 1）

def _bucket(monkeypatch, env, *, maximum=2000.0, restore=100.0, per_product=29, latency=0.0, others=0.0):
    """点数のバケツを真似る偽の店（調べと試験の土台の数: 2,000 点・毎秒 100 点戻る・頼む点は first × 29 ＋ 3）。

    Shopify は頼んだ点を先に引き、実際の点との差を戻すので、応答の currentlyAvailable は実際の点を引いたあとの数。
    頼む点に足りなければ `THROTTLED`（点は引かない）。`others` は、ほかのアプリが毎秒使う点。
    """
    real = store_cli.TRANSPORT
    b = {"avail": maximum, "throttled": 0, "clock": 0.0}

    def tick(seconds):
        b["clock"] += seconds
        b["avail"] = max(0.0, min(maximum, b["avail"] + (restore - others) * seconds))

    def sleep(seconds):
        env.setdefault("slept", []).append(seconds)
        tick(seconds)

    def ext(requested, actual):
        return {"cost": {"requestedQueryCost": requested, "actualQueryCost": actual,
                         "throttleStatus": {"maximumAvailable": maximum, "currentlyAvailable": int(b["avail"]),
                                            "restoreRate": restore}}}

    def transport(method, url, headers, body=None):
        if body is not None and url.endswith(sh.GQL_PATH):
            payload = json.loads(body.decode())
            if payload["query"] == wire.QUERIES["products"]:
                wire.check_request(method, url, body)
                tick(latency)
                requested = 3 + 29 * payload["variables"]["first"]
                if b["avail"] < requested:
                    b["throttled"] += 1
                    return 200, json.dumps({"errors": [{"message": "x", "extensions": {"code": "THROTTLED"}}],
                                            "extensions": ext(requested, 0)}).encode()
                status, raw = real(method, url, headers, body)
                out = json.loads(raw.decode())
                actual = 3 + per_product * len(out["data"]["products"]["nodes"])
                b["avail"] -= actual
                out["extensions"] = ext(requested, actual)
                return status, json.dumps(out).encode()
        return real(method, url, headers, body)

    monkeypatch.setattr(store_cli, "TRANSPORT", transport)
    monkeypatch.setattr(shopify, "SLEEP", sleep)
    return b


@pytest.mark.parametrize("per_product,latency", [(29, 0.0), (29, 0.5), (20, 0.0), (15, 0.0), (10, 0.0)])
def test_柵_混んでいない店で種類20の商品500件を最後まで読める(capsys, env, monkeypatch, per_product, latency):
    """ほかに誰も読んでいない標準の店（2,000 点・毎秒 100 点）。直す前（ae6413c〜）は 300〜500 件で毎回 read_budget。"""
    env["products"] = [sh.product(i) for i in range(1, 501)]
    sh.setup_store(capsys, env)
    env["slept"] = []
    b = _bucket(monkeypatch, env, per_product=per_product, latency=latency)
    rc_, value, err = sh.jrun(capsys, "shop", "items", sh.STORE)
    assert rc_ == 0, value
    assert value["complete"] is True and len(value["items"]) == 500
    assert b["throttled"] == 0                     # 頁の間で足りない点が戻るのを待つので、混んだとは言われない


def test_柵_ほかのアプリが少し使う店でも種類20の商品500件を読める(capsys, env, monkeypatch):
    env["products"] = [sh.product(i) for i in range(1, 501)]
    sh.setup_store(capsys, env)
    env["slept"] = []
    _bucket(monkeypatch, env, per_product=29, others=20.0)
    rc_, value, _ = sh.jrun(capsys, "shop", "items", sh.STORE)
    assert rc_ == 0 and value["complete"] is True and len(value["items"]) == 500


def test_柵_どの頁も足りない店ではTHROTTLEDの上限で止まる(capsys, env, monkeypatch):
    """ほかのアプリが戻る点をほとんど使う店（毎秒 95 点）。頁の間の待ちでは足りず THROTTLED が続き、上限で止まる。"""
    env["products"] = [sh.product(i) for i in range(1, 501)]
    sh.setup_store(capsys, env)
    env["slept"] = []
    b = _bucket(monkeypatch, env, per_product=29, others=95.0)
    rc_, value, _ = sh.jrun(capsys, "shop", "items", sh.STORE)
    # 問い合わせごとの回数（throttled）か、読み全体の上限（read_budget）の先に届いた方で止まる。
    assert rc_ == 2 and value["code"] == "store_unavailable" and value["detail"] in ("throttled", "read_budget")
    assert b["throttled"] <= shopify.READ_RESENDS_MAX + 1
    assert sum(env["slept"]) <= shopify.READ_WAIT_SECONDS_MAX + shopify.READ_PACE_SECONDS_MAX
