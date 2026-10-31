"""Meta 計画 段 4: 広告（読むだけ）。`thth/ads/`・maintain・events・dashboard・morning・IG の probe。

偽の Graph API（loopback）と、試験の中で作った偽の app id・secret だけを使う。**本物の Meta は叩かない。**

一番大事な柵（段 5 で書き直した）: **読む命令は Meta に書かない**。書く口は段 5 の決まった命令の決まった関数だけ:
1. 命令の一覧（parser の `ads` の下）が `ads_cli.READ_VERBS`＋`WRITE_VERBS` と一致し、読む命令に書く語
   （boost・activate・pause・stop・budget・cap・extend・end…）と上限・旗を上げる引数が無い。
2. `thth/ads/` の綴りで POST は `graphwrite.Writer._post` の 1 か所だけ、DELETE・PUT・PATCH はどこにも無い。
   Graph の読む口は `Reader._get` の GET だけ。（書く口を呼ぶ関数の縛りは `tests/test_meta_stage5.py`）
3. 読む命令を全部、偽の Graph にかけて、届いた要求の method が GET だけ。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import secrets
import time
import urllib.parse

import pytest

from thth import accounts, cli, events_cli, jst, maintain
from thth.ads import auth as ads_auth
from thth.ads import cli as ads_cli
from thth.ads import graphread, ledger, watch
from thth.meta import graph as meta_graph
from thth.meta import usage as meta_usage
from tests.test_facebook_adapter import fake_graph

V = "/" + meta_graph.GRAPH_VERSION
ACT = "1234567890"
NAME = "kopicha-ads"
ADS_DIR = pathlib.Path(__file__).resolve().parents[1] / "thth" / "ads"
# 書く語（この段の `thth ads` の命令・引数に現れてはならない）。
WRITE_VERBS = ("boost", "activate", "pause", "stop", "resume", "budget", "cap", "extend", "end", "set",
               "create", "update", "delete", "lower", "raise", "enable", "spend-cap", "spend_cap")


def opaque():
    return secrets.token_urlsafe(32)


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    home = tmp_path / "home"; home.mkdir()
    ledgers = tmp_path / "accounts"; ledgers.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home),
                       ("THTH_ACCOUNTS_DIR", ledgers)]:
        monkeypatch.setenv(key, str(value))
    data = dict(app_id=str(secrets.randbelow(10**15)), app_secret=opaque(), code=opaque(), short=opaque(),
                long=opaque(), exchanged=opaque(), lines=[], human=[], root=root, ledgers=ledgers,
                expires_at=int(time.time()) + 59 * 86400, exchanged_expires_at=int(time.time()) + 60 * 86400,
                scopes=["ads_read", "ads_management", "business_management", "public_profile"],
                currency="JPY", account_status=1, amount_spent="12345", spend_cap="0",
                campaigns=[], adsets=[], ads=[], adaccounts=None)
    app = tmp_path / "app.env"
    app.write_text("\n".join(["META_APP_ID=" + data["app_id"], "META_APP_SECRET=" + data["app_secret"]]) + "\n")
    app.chmod(0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app))

    def token_route(query, form, raw):
        if query.get("code") == [data["code"]]:
            return 200, {"access_token": data["short"], "token_type": "bearer"}
        if query.get("fb_exchange_token") == [data["short"]]:
            return 200, {"access_token": data["long"], "token_type": "bearer", "expires_in": 5183944}
        if query.get("fb_exchange_token") == [data["long"]]:
            return 200, {"access_token": data["exchanged"], "token_type": "bearer", "expires_in": 5183944}
        return 400, {"error": {"message": "bad", "code": 100}}

    def adaccounts_route(query, form, raw):
        rows = data["adaccounts"] if data["adaccounts"] is not None else [
            {"id": "act_999", "account_id": "999", "name": "別の広告", "currency": "JPY",
             "timezone_name": "Asia/Tokyo", "account_status": 1},
            {"id": "act_" + ACT, "account_id": ACT, "name": "こぴちゃ広告", "currency": data["currency"],
             "timezone_name": "Asia/Tokyo", "account_status": 1}]
        return 200, {"data": rows}

    def debug_route(query, form, raw):
        token = (query.get("input_token") or [None])[0]
        at = data["exchanged_expires_at"] if token == data["exchanged"] else data["expires_at"]
        return 200, {"data": {"app_id": data["app_id"], "is_valid": True, "type": "USER", "user_id": "777",
                              "expires_at": at, "data_access_expires_at": int(time.time()) + 80 * 86400,
                              "scopes": data["scopes"]}}

    def act_route(query, form, raw):
        fields = (query.get("fields") or [""])[0]
        if fields == graphread.ACCOUNT_FIELDS:
            return 200, {"id": "act_" + ACT, "name": "こぴちゃ広告", "currency": data["currency"],
                         "timezone_name": "Asia/Tokyo", "account_status": data["account_status"],
                         "amount_spent": data["amount_spent"], "spend_cap": data["spend_cap"]}
        if fields == "minimum_budgets":
            return 400, {"error": {"message": "(#100) Tried accessing nonexisting field", "code": 100}}
        return 200, {"id": "act_" + ACT, fields: "100" if fields in ("balance", "min_daily_budget") else
                     {"id": "42", "name": "吾徳"}}

    def insights_route(query, form, raw):
        return 200, {"data": [{"spend": "1234", "impressions": "100", "clicks": "7", "reach": "80",
                               "date_start": "2026-10-01", "date_stop": "2026-10-29"}]}

    def posted(query, form, raw):
        return 200, {"success": True}

    routes = {("GET", V + "/oauth/access_token"): token_route,
              ("GET", V + "/me/adaccounts"): adaccounts_route,
              ("GET", V + "/debug_token"): debug_route,
              ("GET", V + "/act_" + ACT): act_route,
              ("GET", V + f"/act_{ACT}/campaigns"): lambda q, f, r: (200, {"data": data["campaigns"]}),
              ("GET", V + f"/act_{ACT}/adsets"): lambda q, f, r: (200, {"data": data["adsets"]}),
              ("GET", V + f"/act_{ACT}/ads"): lambda q, f, r: (200, {"data": data["ads"]}),
              ("GET", V + f"/act_{ACT}/insights"): insights_route,
              # 書く要求が来たら受けて記録する（来ないことを試験が見る）。
              ("POST", V + "/act_" + ACT): posted, ("POST", V + f"/act_{ACT}/campaigns"): posted,
              ("DELETE", V + "/act_" + ACT): posted}
    with fake_graph(routes) as (url, calls):
        monkeypatch.setenv(meta_graph.BASE_URL_ENV, url)
        data["calls"] = calls
        data["url"] = url
        yield data


def add(env, *extra, name=NAME):
    return cli.main(["ads", "add", name, "--act-id", "act_" + ACT, "--project", "kopicha",
                     "--page", "kopicha-fb", "--ig", "kopicha-ig", "--by", "operator", *extra])


def auth(env, name=NAME):
    def entered():
        session = ads_auth._read_session(name)
        return ads_auth.CALLBACK + "?" + urllib.parse.urlencode({"state": session["state"], "code": env["code"]})
    return ads_auth.run_auth(name, by="operator", input_func=entered, human_output=env["human"].append,
                             log=env["lines"].append)


def ready(env):
    assert add(env) == 0
    assert auth(env) == 0
    return ledger.load(NAME)


def secrets_of(env):
    return [env[k] for k in ("app_secret", "code", "short", "long", "exchanged")]


# ================================================================== 書く命令が無い（一番大事な柵）

def _ads_parser():
    parser = cli.build_parser()
    for action in parser._subparsers._group_actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices["ads"]
    raise AssertionError("ads が parser に無い")


def _walk(parser, path=()):
    yield path, parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                yield from _walk(child, (*path, name))


def test_命令の一覧は読む7つと書く7つだけ_読む命令に書く語も上限を上げる引数も無い():
    """段 5 で書き直した（段 4 の「書く命令が無い」）。書く命令は決まった 7 つだけ、読む命令は段 4 のまま。"""
    ads = _ads_parser()
    verbs = set()
    for action in ads._actions:
        if isinstance(action, argparse._SubParsersAction):
            verbs |= set(action.choices)
    assert set(ads_cli.READ_VERBS) == {"accounts", "add", "auth", "doctor", "show", "insights", "collect"}
    assert set(ads_cli.WRITE_VERBS) == {"boost", "activate", "extend", "pause", "stop", "budget", "end"}
    assert verbs == set(ads_cli.VERBS)
    for path, parser in _walk(ads):
        if path and path[0] in ads_cli.WRITE_VERBS:
            continue   # 書く命令の引数は tests/test_meta_stage5.py が縛る（上げる引数が無い）
        for word in path:
            assert not any(w in word for w in WRITE_VERBS), f"読む命令に書く語がある: {path}"
        for action in parser._actions:
            for option in action.option_strings:
                assert not re.search(r"ceiling|monthly|budget|cap|enable|production|days", option), \
                    f"上限・旗を上げる引数がある: {path} {option}"


def test_書く口は1か所_POSTはgraphwriteの_postだけ_DELETEはどこにも無い():
    """段 5 で書き直した。Graph への書く要求は `graphwrite.Writer._post` の 1 か所、読む要求は `Reader._get` の 1 か所。"""
    sources = {p.name: p.read_text(encoding="utf-8") for p in ADS_DIR.glob("*.py")}
    assert set(sources) >= {"__init__.py", "ledger.py", "graphread.py", "graphwrite.py", "auth.py", "watch.py",
                            "cli.py", "ops.py", "budget.py", "digest.py"}
    strip = lambda src: re.sub(r'"""[\s\S]*?"""', "", src)   # 説明文は除く（語として出てくる）
    posts = {}
    for name, src in sources.items():
        code = strip(src)
        assert not re.search(r"""["'](DELETE|PUT|PATCH)["']""", code), f"{name} に DELETE などがある"
        assert "urlopen" not in code and "Request(" not in code, f"{name} が網に直に出ている"
        for call in re.findall(r"\.request\(\s*([^,\)]+)", code):
            assert call.strip() in ('"GET"', '"POST"'), f"{name} の request の method: {call}"
        posts[name] = code.count('.request("POST"')
        if name != "graphwrite.py":
            assert not re.search(r"""["']POST["']""", code), f"{name} に POST がある"
    assert posts["graphwrite.py"] == 1 and sum(posts.values()) == 1
    assert strip(sources["graphread.py"]).count('.request("GET"') == 1
    # 認可の口は `auth_facebook.graph_get`（GET だけを組む）を通る。
    from thth.adapters import auth_facebook
    import inspect
    assert 'method="GET"' in inspect.getsource(auth_facebook.graph_get)


def test_読む命令を全部かけても_Metaへの要求はGETだけ(env, capsys):
    env["campaigns"] = [{"id": "c1", "status": "ACTIVE", "effective_status": "ACTIVE"}]
    cfg = ready(env)
    for argv in (["ads", "accounts", NAME, "--json"], ["ads", "show", NAME, "--json"],
                 ["ads", "show", NAME, "--offline", "--json"], ["ads", "insights", NAME, "--json"],
                 ["ads", "insights", NAME, "--level", "campaign", "--preset", "yesterday", "--json"],
                 ["ads", "collect", NAME, "--json"], ["ads", "collect", "--json"], ["ads", "doctor", NAME, "--json"],
                 ["maintain", "--json"], ["ads", "auth", NAME, "--exchange", "--by", "operator"]):
        cli.main(argv)
    methods = {c["method"] for c in env["calls"]}
    assert methods == {"GET"}, methods
    assert not any(c["method"] != "GET" for c in env["calls"])
    output = capsys.readouterr()
    text = output.out + output.err + "\n".join(env["lines"])
    for value in secrets_of(env):
        assert value not in text
    assert cfg["ceilings"] == {"monthly": 0, "lifetime_budget_max": 0, "days_max": 0}


# ================================================================== 台帳

def test_addは上限0_旗false_0600で書き_口座の名前とぶつかれば断る(env, capsys):
    assert add(env, "--json") == 0
    path = pathlib.Path(ledger.path_for(NAME))
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    raw = json.loads(path.read_text())
    assert raw["act_id"] == ACT and raw["ceilings"] == {"monthly": 0, "lifetime_budget_max": 0, "days_max": 0}
    assert raw["production"] is False and raw["ads_enabled"] is False and raw["writes_refused"] is None
    assert raw["pages"] == ["kopicha-fb"] and raw["ig_accounts"] == ["kopicha-ig"]
    assert raw["currency"] is None and raw["timezone_name"] is None
    assert str(path.parent).endswith("adaccounts") and str(env["ledgers"]) not in str(path)
    assert add(env) == 2   # 上書きしない
    (env["ledgers"] / "kopicha-fb.json").write_text("{}")
    assert add(env, name="kopicha-fb") == 2
    assert "adaccount_name_taken" in capsys.readouterr().err


def test_台帳の検査_上限は3つの0以上の整数_通貨は3字():
    data = ledger.build(NAME, act_id=ACT, project="kopicha")
    ledger.validate(NAME, data)
    for bad in ({"ceilings": {"monthly": -1, "lifetime_budget_max": 0, "days_max": 0}},
                {"ceilings": {"monthly": 0}}, {"currency": "yen"}, {"act_id": "act_1"},
                {"production": "yes"}, {"pages": ["../x"]}, {"writes_refused": "whatever"}):
        with pytest.raises(ledger.AdAccountError):
            ledger.validate(NAME, {**data, **bad})


def test_書く操作を断る印_通貨が分からない_JPY以外は断る():
    base = ledger.build(NAME, act_id=ACT, project="kopicha")
    assert ledger.write_refusal(base) == ledger.CURRENCY_UNKNOWN
    assert ledger.write_refusal({**base, "currency": "JPY"}) is None
    assert ledger.write_refusal({**base, "currency": "USD"}) == ledger.CURRENCY_UNVERIFIED
    assert ledger.write_refusal({**base, "currency": "JPY", "writes_refused": ledger.CURRENCY_UNVERIFIED}) \
        == ledger.CURRENCY_UNVERIFIED


def test_止めた口座は_作る始める側のrequire_activeが断り_読むshowは通る(env):
    ready(env)
    ledger.require_active(NAME)
    guard = pathlib.Path(ledger.state_dir_for(NAME)) / "guard"
    guard.mkdir(mode=0o700)
    (guard / "stopped.json").write_text(json.dumps({"reason": "owner", "stopped_at": "2026-10-29T10:00:00+09:00"}))
    (guard / "stopped.json").chmod(0o600)
    with pytest.raises(accounts.AccountStopped):
        ledger.require_active(NAME)
    assert ledger.stopped(NAME)["reason"] == "owner"
    assert cli.main(["ads", "show", NAME, "--json"]) == 0   # 読むのは止まっていても通す（D-1 の 4）


# ================================================================== 認可

def test_authはユーザーの長い鍵を保存し_通貨と時間帯を台帳に写す(env):
    cfg = ready(env)
    token = json.loads(pathlib.Path(cfg["token"]).read_text())
    assert oct(pathlib.Path(cfg["token"]).stat().st_mode & 0o777) == "0o600"
    assert token["access_token"] == env["long"] and token["token_type"] == "user"
    assert token["expires_at"] == env["expires_at"] and token["expiry_source"] == "debug_token"
    assert token["scopes"] == env["scopes"] and token["act_id"] == ACT and "data_access_expires_at" in token
    assert cfg["currency"] == "JPY" and cfg["timezone_name"] == "Asia/Tokyo" and cfg["writes_refused"] is None
    assert cfg["ceilings"]["monthly"] == 0 and cfg["ads_enabled"] is False
    url = env["human"][0]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert query["scope"] == [",".join(ads_auth.SCOPES)] and query["redirect_uri"] == [ads_auth.CALLBACK]
    raw = pathlib.Path(cfg["token"]).read_text()
    assert env["short"] not in raw
    assert not os.path.exists(ads_auth._session_path(NAME))


def test_authは台帳のact_idが一覧に無ければ保存せず_見えた一覧を名前で言う(env):
    assert add(env) == 0
    env["adaccounts"] = [{"id": "act_999", "account_id": "999", "name": "別の広告", "currency": "JPY"}]
    assert auth(env) == 2
    assert not os.path.exists(ledger.load(NAME)["token"])
    text = "\n".join(env["lines"])
    assert "ads_adaccount_not_granted" in text and "別の広告 act_999" in text


def test_authは通貨がJPY以外なら台帳に書く操作を断る印を残す(env):
    env["currency"] = "USD"
    cfg = ready(env)
    assert cfg["currency"] == "USD" and cfg["writes_refused"] == ledger.CURRENCY_UNVERIFIED
    assert ledger.write_refusal(cfg) == ledger.CURRENCY_UNVERIFIED
    assert "書く操作を断る印" in "\n".join(env["lines"])


def test_authは台帳の通貨とMetaの答えが違えば保存しない(env):
    ready(env)
    env["currency"] = "USD"
    assert auth(env) == 2
    assert "adaccount_changed" in "\n".join(env["lines"])
    assert ledger.load(NAME)["currency"] == "JPY"


def test_exchangeは延びれば保存し試しを残す_延びなければ保存しない(env):
    cfg = ready(env)
    assert ads_auth.run_exchange(NAME, by="operator", log=env["lines"].append) == 0
    token = json.loads(pathlib.Path(cfg["token"]).read_text())
    assert token["access_token"] == env["exchanged"] and token["expires_at"] == env["exchanged_expires_at"]
    trials = pathlib.Path(ledger.state_dir_for(NAME), ads_auth.TRIALS_FILE).read_text().splitlines()
    assert json.loads(trials[-1])["extended"] is True
    assert oct(pathlib.Path(ledger.state_dir_for(NAME), ads_auth.TRIALS_FILE).stat().st_mode & 0o777) == "0o600"
    assert "延びました" in "\n".join(env["lines"])


def test_exchangeは延びない答えなら鍵を替えない(env):
    cfg = ready(env)
    env["exchanged_expires_at"] = env["expires_at"]
    assert ads_auth.run_exchange(NAME, by="operator", log=env["lines"].append) == 0
    assert json.loads(pathlib.Path(cfg["token"]).read_text())["access_token"] == env["long"]
    assert "延びませんでした" in "\n".join(env["lines"])
    last = pathlib.Path(ledger.state_dir_for(NAME), ads_auth.TRIALS_FILE).read_text().splitlines()[-1]
    assert json.loads(last)["extended"] is False


# ================================================================== 読む

def test_showは金額を最小単位の整数で持ち_確かめていない欄は別に訊く(env, capsys):
    ready(env)
    capsys.readouterr()
    assert cli.main(["ads", "show", NAME, "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert value["account"]["amount_spent"] == 12345 and value["account"]["spend_cap"] == 0
    assert value["account"]["currency"] == "JPY" and value["account"]["timezone_name"] == "Asia/Tokyo"
    extras = value["account_extras"]
    assert extras["balance"] == 100 and extras["min_daily_budget"] == 100 and extras["unavailable"] == ["minimum_budgets"]
    assert value["ledger"]["writes_refused"] is None and value["ledger"]["ceilings"]["monthly"] == 0
    assert any(e["kind"] == "ads_spend_cap_unset" for e in value["events"])


def test_insightsのspendはJPYだけ最小単位に直す():
    assert graphread.spend_minor("1234", "JPY") == 1234
    assert graphread.spend_minor("12.34", "JPY") is None   # 端数は単位の読み違い（埋めない）
    assert graphread.spend_minor("12.34", "USD") is None
    assert graphread.minor("12345") == 12345 and graphread.minor("1.5") is None and graphread.minor(True) is None


def test_insightsは前回の使用率が線を越えていれば網に出ない(env, capsys):
    ready(env)
    path = ledger.usage_path_for(NAME)
    meta_usage._write(None, {"version": 1, "observed_at": "2026-10-29T10:00:00+09:00",
                             "headers": {"ads_insights_throttle": {"app_id_util_pct": 10.0, "acc_id_util_pct": 95.0}}},
                      path)
    before = len(env["calls"])
    assert cli.main(["ads", "insights", NAME]) == 2
    assert "ads_insights_throttled" in capsys.readouterr().err
    assert len(env["calls"]) == before
    assert meta_usage.insights_throttle_pct({"ads_insights_throttle": {"app_id_util_pct": 50}}) == 50.0


def test_量の写しは広告の口座の置き場に残り_口座のstateを作らない(env):
    ready(env)
    reader = graphread.Reader(NAME, ledger.load(NAME), ledger.load_token(ledger.load(NAME)))
    assert reader.client.usage_path == ledger.usage_path_for(NAME) and reader.client.account is None
    meta_usage.observe(None, {"x-fb-ads-insights-throttle": json.dumps({"app_id_util_pct": 3})},
                       path=ledger.usage_path_for(NAME))
    assert meta_usage.summary(None, path=ledger.usage_path_for(NAME))["ads_insights_util_pct"] == 3.0
    assert not (env["root"] / "state" / NAME).exists()


def test_doctorは読むだけで_権限が足りなければNOT_GRANTEDを名指しする(env, capsys):
    env["scopes"] = ["ads_read", "public_profile"]
    ready(env)
    capsys.readouterr()
    rc = cli.main(["ads", "doctor", NAME, "--json"])
    value = json.loads(capsys.readouterr().out)
    rows = {r["key"]: r for r in value["rows"]}
    assert rows["debug_token"]["ok"] is True and "NOT_GRANTED: ads_management,business_management" in rows["debug_token"]["detail"]
    assert rows["token"]["ok"] is True and rows["account"]["ok"] is True and rows["insights"]["ok"] is True
    assert "spend_cap 0" in rows["account"]["detail"]
    assert rc == 0
    env["scopes"] = ["public_profile"]
    cli.main(["ads", "doctor", NAME, "--json"])
    rows = {r["key"]: r for r in json.loads(capsys.readouterr().out)["rows"]}
    assert rows["debug_token"]["ok"] is False and "ads_read" in rows["debug_token"]["detail"]


# ================================================================== 見張り（collect・events）

def test_collectは木を読み_THTHの外で動いている物をads_active_outside_ththにする(env):
    env["campaigns"] = [{"id": "c1", "status": "ACTIVE", "effective_status": "ACTIVE", "stop_time": "2026-11-05T00:00:00+0900"},
                        {"id": "c2", "status": "PAUSED", "effective_status": "PAUSED"}]
    env["adsets"] = [{"id": "s1", "campaign_id": "c1", "effective_status": "ACTIVE", "lifetime_budget": "5000"}]
    env["ads"] = [{"id": "a1", "adset_id": "s1", "effective_status": "ACTIVE"}]
    ready(env)
    result = watch.collect(NAME, force=True)
    snap = result["snapshot"]
    assert snap["error"] is None and snap["tree"]["complete"] is True
    assert snap["tree"]["adsets"][0]["lifetime_budget"] == 5000
    assert snap["insights"]["this_month"]["spend_minor"] == 1234
    assert oct(pathlib.Path(watch.snapshot_path(NAME)).stat().st_mode & 0o777) == "0o600"
    cfg = ledger.load(NAME)
    rows = watch.events(NAME, cfg, watch.read_snapshot(NAME), now=jst.now_jst(),
                        make=lambda kind, ref, summary, **extra: {"kind": kind, "ref": ref, **extra})
    kinds = {r["kind"]: r for r in rows}
    assert kinds["ads_active_outside_thth"]["ids"] == ["a1", "c1", "s1"]
    assert "ads_no_end_time" not in kinds   # キャンペーンに stop_time がある
    assert "ads_spend_cap_unset" in kinds
    # 同じ刻みでは読み直さない。
    before = len(env["calls"])
    assert watch.collect(NAME)["skipped"] == "fresh" and len(env["calls"]) == before


def test_THTHのactivateの記録にある物は外の配信と言わない_終了日の無い物は言う(env):
    env["campaigns"] = [{"id": "c1", "effective_status": "ACTIVE"}]
    env["adsets"] = [{"id": "s1", "campaign_id": "c1", "effective_status": "ACTIVE"}]
    ready(env)
    watch.collect(NAME, force=True)
    ops = pathlib.Path(ledger.state_dir_for(NAME)) / "ops"
    ops.mkdir()
    (ops / "1.json").write_text(json.dumps({"op": "activate", "ids": ["c1", "s1"]}))
    rows = watch.events(NAME, ledger.load(NAME), watch.read_snapshot(NAME), now=jst.now_jst(),
                        make=lambda kind, ref, summary, **extra: {"kind": kind, **extra})
    kinds = {r["kind"]: r for r in rows}
    assert "ads_active_outside_thth" not in kinds and kinds["ads_no_end_time"]["ids"] == ["s1"]


def test_支出の物差し_spend_capの80パーセント_月の上限の90パーセント_状態(env):
    env["spend_cap"] = "10000"
    env["amount_spent"] = "8000"
    env["account_status"] = 3
    ready(env)
    watch.collect(NAME, force=True)
    data = ledger.load_raw(NAME)
    data["ceilings"]["monthly"] = 1300   # VM の台帳を直接書く（THTH の CLI には口が無い）
    import thth.secrets_fs as sfs
    sfs.atomic_write_json(ledger.path_for(NAME), data)
    rows = watch.events(NAME, ledger.load(NAME), watch.read_snapshot(NAME), now=jst.now_jst(),
                        make=lambda kind, ref, summary, **extra: {"kind": kind, **extra})
    kinds = {r["kind"]: r for r in rows}
    assert kinds["ads_spend_cap_near"]["amount_spent"] == 8000
    assert kinds["ads_budget_low"]["spend_minor"] == 1234 and kinds["ads_account_status"]["account_status"] == 3
    assert "ads_spend_cap_unset" not in kinds


def test_読めなければads_read_failedで_前の木は消さない(env):
    env["campaigns"] = [{"id": "c1", "effective_status": "ACTIVE"}]
    ready(env)
    watch.collect(NAME, force=True)
    env["account_status"] = 1
    pathlib.Path(ledger.load(NAME)["token"]).unlink()
    snap = watch.collect(NAME, force=True)["snapshot"]
    assert snap["error"] == "ads_token_missing" and snap["tree"]["campaigns"][0]["id"] == "c1"
    kinds = {r["kind"] for r in watch.events(NAME, ledger.load(NAME), snap, now=jst.now_jst(),
                                             make=lambda kind, ref, summary, **extra: {"kind": kind})}
    assert {"ads_read_failed", "ads_active_outside_thth"} <= kinds


def test_eventsとdashboardの要対応に広告の口座が載り_絞った読みには出ない(env):
    env["campaigns"] = [{"id": "c1", "effective_status": "ACTIVE"}]
    ready(env)
    watch.collect(NAME, force=True)
    acct = json.loads((pathlib.Path(__file__).parents[1] / "accounts.example/facebook.json").read_text())
    acct.update(account="kopicha-fb", project="kopicha", handle="1", page_id="1",
                repo_dir=str(env["root"] / "repos/_none"), token=str(env["root"] / "fb.token"),
                env=str(env["root"] / "fb.env"))
    (env["ledgers"] / "kopicha-fb.json").write_text(json.dumps(acct))
    payload = events_cli.build("kopicha")
    kinds = {row["kind"] for row in payload["events"]}
    assert "ads_active_outside_thth" in kinds and "ads_spend_cap_unset" in kinds
    assert payload["by_kind"]["ads_active_outside_thth"] == 1
    row = next(r for r in payload["events"] if r["kind"] == "ads_active_outside_thth")
    assert row["ongoing"] is True and row["account"] == NAME
    assert {r["kind"] for r in events_cli.build("kopicha-fb")["events"]} >= {"ads_active_outside_thth"}
    limited = events_cli.build("kopicha", allowed_names=["kopicha-fb"])
    assert not any(r["kind"].startswith("ads_") for r in limited["events"])
    from thth import dashboard_cli
    title, next_text = dashboard_cli._event_text(row, {}, {}, {}, {})
    assert "THTH の外で広告が配信中" in title and "広告マネージャ" in next_text
    assert set(watch.KINDS) <= set(events_cli.KINDS) and set(watch.KINDS) <= set(dashboard_cli.KIND_JA)


def test_collect_linkedは結んだ口座の採取で回り_失敗で採取を落とさない(env):
    ready(env)
    assert watch.collect_linked("kopicha-fb", log=env["lines"].append) == [NAME]
    assert watch.read_snapshot(NAME)["error"] is None
    assert watch.collect_linked("someone-else") == []
    pathlib.Path(ledger.path_for(NAME)).write_text("{broken")
    assert watch.collect_linked("kopicha-fb") == []


def test_thth_collectの最後に結んだ広告の口座を読む(env, monkeypatch):
    from thth import collect
    ready(env)
    monkeypatch.setattr(collect, "_collect_entry", lambda name, **kw: 0)
    monkeypatch.setattr(collect, "_record_collect_run", lambda *a, **kw: None)
    assert collect.run_collect("kopicha-fb", log=env["lines"].append) == 0
    assert watch.read_snapshot(NAME)["error"] is None
    assert any("広告の口座 kopicha-ads を読みました" in line for line in env["lines"])
    # 読めなくても採取の終了コードは変えない。
    monkeypatch.setattr(watch, "collect", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("x")))
    assert collect.run_collect("kopicha-fb", log=env["lines"].append) == 0


def test_morningの今日の段に広告の口座の行(env):
    env["campaigns"] = [{"id": "c1", "effective_status": "ACTIVE"}]
    ready(env)
    watch.collect(NAME, force=True)
    from thth import morning
    rows = morning._ads_rows(morning._ads_names("kopicha", "project"), jst.now_jst())
    assert rows[0]["adaccount"] == NAME and rows[0]["active"] == 1 and rows[0]["token"]["state"] == "ok"
    assert any(e["kind"] == "ads_active_outside_thth" for e in rows[0]["events"])
    lines = []
    morning._render_section({"section": "today", "value": {"ads": {"value": rows, "cannot_say": None},
                                                           "budget": {"cannot_say": "not_supported"}}},
                            lines.append)
    assert any("広告 kopicha-ads" in line for line in lines) and any("要対応 ads_active_outside_thth" in l for l in lines)


# ================================================================== maintain（鍵の残り 14 日）

def _set_expiry(cfg, days):
    path = pathlib.Path(cfg["token"])
    token = json.loads(path.read_text())
    token["expires_at"] = int(jst.now_jst().timestamp() + days * 86400)   # 試験の壁時計（conftest が固定）
    path.write_text(json.dumps(token))


def test_maintainは広告の鍵の残り13日でads_token_expiring_15日でok(env):
    cfg = ready(env)
    _set_expiry(cfg, 13)
    row = maintain.inspect_adaccount(NAME, now=jst.now_jst())
    assert row["state"] == maintain.ADS_TOKEN_EXPIRING and maintain.needs_attention(row)
    assert f"thth ads auth {NAME}" in row["message"]
    lines = []
    assert maintain.run_maintain(log=lines.append) == 1
    assert any("広告の口座 kopicha-ads: ads_token_expiring" in line for line in lines)
    _set_expiry(cfg, 15)
    assert maintain.inspect_adaccount(NAME, now=jst.now_jst())["state"] == maintain.OK
    assert maintain.run_maintain(NAME, log=lines.append) == 0
    _set_expiry(cfg, -1)
    assert maintain.inspect_adaccount(NAME, now=jst.now_jst())["state"] == maintain.EXPIRED
    runs = list(pathlib.Path(ledger.state_dir_for(NAME)).glob("runs-*.ndjson"))
    assert runs


def test_maintainは記録した権限にads_readが無ければads_not_granted(env):
    cfg = ready(env)
    path = pathlib.Path(cfg["token"])
    token = json.loads(path.read_text())
    token["scopes"] = ["public_profile"]
    path.write_text(json.dumps(token))
    assert maintain.inspect_adaccount(NAME, now=jst.now_jst())["state"] == maintain.ADS_NOT_GRANTED
    path.unlink()
    assert maintain.inspect_adaccount(NAME, now=jst.now_jst())["state"] == maintain.NO_TOKEN


def test_広告の鍵の線は14日で_maintainの7日とは別():
    assert watch.TOKEN_WARN_DAYS == 14.0 and maintain.WARN_REMAINING_DAYS == 7.0


# ================================================================== 媒体にしない・IG の下地

def test_広告は媒体にしない_registryとREGISTRYに無く理由がある():
    from thth import adapters, media_registry
    assert "ads" not in adapters.REGISTRY and "ads" not in media_registry.MEDIA
    assert media_registry.NOT_MEDIA["ads"] and "adaccounts/" in media_registry.NOT_MEDIA["ads"]
    assert ledger.MEDIA == "ads"


def test_IGのprobeにconnected_threads_userが読むだけで載る(monkeypatch):
    from tests.test_meta_stage3 import IG, adapter as ig_adapter, limit_route
    from thth.adapters import instagram as ig
    routes = {("GET", f"{V}/{IG}"): (200, {"id": IG, "username": "kopi_chaba"}),
              ("GET", f"{V}/debug_token"): (200, {"data": {"scopes": ig.SCOPES, "expires_at": 0}}),
              ("GET", f"{V}/{IG}/content_publishing_limit"): limit_route(),
              ("GET", f"{V}/{IG}/media"): (200, {"data": []}),
              ("GET", f"{V}/{IG}/insights"): (200, {"data": []}),
              ("GET", f"{V}/{IG}/connected_threads_user"): (200, {"data": [{"threads_user_id": "31415"}]})}
    with fake_graph(routes) as (url, calls):
        rows = {r["key"]: r for r in ig_adapter(url).probe()}
    assert all(c["method"] == "GET" for c in calls)
    assert rows["connected_threads_user"]["ok"] is True and "31415" in rows["connected_threads_user"]["detail"]
    with fake_graph({k: v for k, v in routes.items() if "connected_threads_user" not in k[1]}) as (url, _calls):
        rows = {r["key"]: r for r in ig_adapter(url).probe()}
    assert rows["connected_threads_user"]["ok"] is None   # 読めなくても doctor を赤にしない
