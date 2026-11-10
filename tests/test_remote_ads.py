"""設計 広告の資格と遠くの道（2026-11-06）段 3・段 4: 遠くの道（`thth login` のあと）の広告の口。

見るのは:
  - 段 3（VM の受け口 `thth/server_ads.py`）: `api_requests.handle`（本物の資格の確かめ）→ 手元の `thth ads` の関数 →
    偽の Graph で 1 本ずつ通る。柵は手元の道と同じ: `production`・上限・止めの印・leave_gate。`by` は鍵の持ち主。
  - リード・商品・狙う相手・Meta の断りの文は VM の依頼の印に残らない（答えで 1 回だけ端末に届く）。
  - 配信を始める・延ばす・止めの印を外す口（`activate`・`extend`・`resume`）は遠くの道に無い（masaru 11/06）。
  - 段 4（CLI の口 `thth/remote.py`）: 台帳が手元に無く `thth login` 済みなら、出す 12 の口が `/api/v1/ads_*` に
    届く。手元に `adaccounts/` の台帳があれば遠くへ送らない。出さない口は `ads_remote_unsupported` で断る。
偽の Graph と偽の Worker は loopback だけ。本物の Meta・thth.me は叩かない。
"""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import pathlib
import secrets
import socket
import sys

import pytest

from thth import accounts, api_requests, jst, leave_gate, refusal_catalog, remote, server_ads, server_files
from thth import server_writes
from thth.ads import ledger, ops
from thth.meta import graph as meta_graph
from tests.test_facebook_adapter import fake_graph
from tests.test_meta_stage5 import ACT, STORY, Meta, Router
from tests.test_v212_server_writes import env, save_config  # noqa: F401
from tests.test_v3140_remote_cli import FakeWorker
from tests.kept_bytes import kept_bytes

V = "/" + meta_graph.GRAPH_VERSION
NAME = "rev-ads"
PROJECT = "meta-review"
PAGE = STORY.split("_")[0]
FORM, BIZ, CATALOG = "7001", "8001", "9001"
# 残ってはいけない目印。
LEAD_NAME, PRODUCT_NAME, META_SAYS = "審査員のリードの目印", "商品の名前の目印", "Meta の断りの文の目印"


def end_in(days):
    return jst.iso(jst.now_jst() + datetime.timedelta(days=days) - datetime.timedelta(minutes=5))


def set_ledger(**changes):
    path = pathlib.Path(ledger.path_for(NAME))
    raw = json.loads(path.read_text())
    raw.update(changes)
    path.write_text(json.dumps(raw))


@pytest.fixture
def vm(env, monkeypatch, tmp_path):  # noqa: F811
    """VM の側: 招待で作った形の広告の台帳（production false・上限 0）と、その名前だけの資格の行。偽の Graph。"""
    monkeypatch.setattr(socket.socket, "connect", env["connect"])
    app = tmp_path / "app.env"
    app_id = str(secrets.randbelow(10 ** 15))
    app.write_text("META_APP_ID=" + app_id + "\nMETA_APP_SECRET=" + secrets.token_urlsafe(32) + "\n")
    app.chmod(0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app))
    root = env["root"]
    data = ledger.build(NAME, act_id=ACT, project=PROJECT, business_id=BIZ)
    data.update(token=str(root / "secrets" / (NAME + ".token")), currency="JPY", timezone_name="Asia/Tokyo",
                invite_id="i" * 20)
    ledger.write_new(NAME, data)
    token = {"access_token": "tok-" + secrets.token_hex(12), "obtained_at": jst.iso(), "no_expiry": True,
             "scopes": ["ads_read", "ads_management"],
             "data_access_expires_at": int(jst.now_jst().timestamp()) + 80 * 86400}
    ledger.write_token(ledger.load(NAME), token)
    env["value"]["credentials"][0]["accounts"] = {NAME: PROJECT}
    save_config(env)
    meta = Meta()

    def account(q, f, r):
        fields = (q.get("fields") or [""])[0]
        if fields == "minimum_budgets":
            return 200, {"id": "act_" + ACT, "minimum_budgets": meta.minimum_budgets}
        if "," not in fields:
            return 200, {"id": "act_" + ACT, fields: "159" if fields == "min_daily_budget" else "0"}
        return 200, {"id": "act_" + ACT, "name": "審査の広告", "currency": "JPY", "timezone_name": "Asia/Tokyo",
                     "account_status": 1, "amount_spent": "0", "spend_cap": "0"}

    def creator(kind):
        def create(q, f, r):
            fields = {k: v[0] for k, v in f.items() if k not in ("access_token", "appsecret_proof")}
            if kind == "ad":
                fields["creative_id"] = json.loads(fields.pop("creative"))["creative_id"]
                fields["campaign_id"] = meta.objects[fields["adset_id"]]["campaign_id"]
            return 200, {"id": meta.new(kind, **fields)}
        return create

    def debug(q, f, r):
        return 200, {"data": {"app_id": app_id, "is_valid": True, "user_id": "777", "expires_at": 0,
                              "data_access_expires_at": int(jst.now_jst().timestamp()) + 80 * 86400,
                              "scopes": ["ads_read", "ads_management", "business_management"]}}

    empty = lambda q, f, r: (200, {"data": []})  # noqa: E731
    base = {("GET", V + "/act_" + ACT): account, ("GET", V + "/debug_token"): debug,
            ("GET", V + f"/act_{ACT}/campaigns"): lambda q, f, r: (200, {"data": meta.of_kind("campaign")}),
            ("GET", V + f"/act_{ACT}/adsets"): lambda q, f, r: (200, {"data": meta.of_kind("adset")}),
            ("GET", V + f"/act_{ACT}/ads"): lambda q, f, r: (200, {"data": meta.of_kind("ad")}),
            ("GET", V + f"/act_{ACT}/insights"): lambda q, f, r: (200, {"data": [
                {"spend": "0", "impressions": "0", "clicks": "0", "reach": "0", "date_start": "2026-11-01",
                 "date_stop": "2026-11-05"}]}),
            ("GET", V + f"/act_{ACT}/adspixels"): empty,
            ("GET", V + f"/act_{ACT}/reachestimate"): lambda q, f, r: (200, {"data": {
                "users_lower_bound": 1000, "users_upper_bound": 2000}}),
            ("GET", V + f"/act_{ACT}/delivery_estimate"): empty,
            ("GET", V + f"/{PAGE}/leadgen_forms"): lambda q, f, r: (200, {"data": [
                {"id": FORM, "name": "問い合わせ", "status": "ACTIVE", "leads_count": 1}]}),
            ("GET", V + f"/{FORM}/leads"): lambda q, f, r: (200, {"data": [
                {"id": "1", "created_time": "2026-11-05T10:00:00+0000",
                 "field_data": [{"name": "full_name", "values": [LEAD_NAME]}]}]}),
            ("GET", V + f"/{BIZ}/owned_product_catalogs"): lambda q, f, r: (200, {"data": [
                {"id": CATALOG, "name": "カタログ", "product_count": 1}]}),
            ("GET", V + f"/{CATALOG}/products"): lambda q, f, r: (200, {"data": [
                {"id": "1", "retailer_id": "r1", "name": PRODUCT_NAME}]}),
            ("GET", V + f"/{BIZ}/adnetworkanalytics"): empty,
            ("GET", V + f"/{BIZ}/commerce_merchant_settings"): empty,
            ("POST", V + f"/act_{ACT}/campaigns"): creator("campaign"),
            ("POST", V + f"/act_{ACT}/adsets"): creator("adset"),
            ("POST", V + f"/act_{ACT}/adcreatives"): creator("creative"),
            ("POST", V + f"/act_{ACT}/ads"): creator("ad")}
    with fake_graph(Router(meta, base)) as (url, calls):
        monkeypatch.setenv(meta_graph.BASE_URL_ENV, url)
        yield {**env, "meta": meta, "calls": calls, "vm_log": io.StringIO()}


def vm_call(vm, operation, **body):
    """Worker が置いた依頼 1 件を VM が行う（本物の資格の確かめ）。戻りは依頼の印の答え。"""
    sha = hashlib.sha256(vm["bearer"].encode()).hexdigest()
    item = {"request_id": secrets.token_urlsafe(32) + "." + NAME, "account": body.get("account", NAME),
            "operation": operation, "key_sha256": sha, "body": {"account": NAME, **body}}
    item["request_id"] = item["request_id"].rsplit(".", 1)[0] + "." + item["account"]
    real = sys.stderr
    sys.stderr = vm["vm_log"]
    try:
        entry = api_requests.handle("person", item, str(vm["path"]))
    finally:
        sys.stderr = real
    return entry["result"]


def records(account=NAME):
    folder = pathlib.Path(accounts.state_dir_for(account)) / "requests"
    return [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))]


def writes(vm):
    return [c for c in vm["calls"] if c["method"] == "POST"]


def nothing_kept(vm, *markers):
    for path in pathlib.Path(vm["root"]).rglob("*"):
        if path.is_file():
            raw = kept_bytes(path)
            for marker in markers:
                assert marker.encode() not in raw, f"{path} に {marker} が残っている"
    for marker in markers:
        assert marker not in vm["vm_log"].getvalue()


def production(days=3, lifetime=1000):
    """伺い (1) の推し: masaru が VM の台帳で production と 1 操作の上限だけを上げる（ads_enabled・monthly は 0 のまま）。"""
    set_ledger(production=True, ceilings={"monthly": 0, "lifetime_budget_max": lifetime, "days_max": days})


# ================================================================== 段 3: VM の受け口

def test_ads_doctorとshowは読む資格だけの鍵で通る(vm):
    vm["value"]["credentials"][0]["writes"] = False
    save_config(vm)
    doctor = vm_call(vm, "ads_doctor")
    assert "error" not in doctor, doctor
    rows = {row["key"]: row for row in doctor["rows"]}
    assert rows["ledger"]["ok"] is True and rows["debug_token"]["ok"] is True
    show = vm_call(vm, "ads_show")
    assert "error" not in show, show
    assert show["ledger"]["production"] is False and show["account"]["currency"] == "JPY"
    text = json.dumps([doctor, show], ensure_ascii=False)
    assert str(vm["root"]) not in text and "SKILL.md" not in text, "VM の置き場を返さない"
    assert {c["method"] for c in vm["calls"]} == {"GET"}
    # 書く口は writes の無い鍵では網に出ない。
    assert vm_call(vm, "ads_stop") == {"error": "writes_not_allowed"}
    assert writes(vm) == []


@pytest.mark.parametrize("operation,body,key", [
    ("ads_insights", {"preset": "last_7d"}, "rows"),
    ("ads_estimate", {"targeting": json.dumps({"geo_locations": {"countries": ["JP"]}})}, "reach"),
    ("ads_catalog_list", {}, "catalogs"),
    ("ads_catalog_products", {"catalog": CATALOG, "limit": 5}, "products"),
    ("ads_network", {}, "rows"),
    ("ads_shop", {}, "shops"),
    ("ads_leads", {"page": PAGE}, "forms"),
])
def test_読む口はそれぞれ手元の関数の答えを返す(vm, operation, body, key):
    value = vm_call(vm, operation, **body)
    assert "error" not in value and key in value, value
    assert value["adaccount"] == NAME
    assert {c["method"] for c in vm["calls"]} == {"GET"}


def test_ads_leadsの本文はVMの印に残らない(vm):
    value = vm_call(vm, "ads_leads", page=PAGE, form=FORM, limit=5)
    assert value["leads"][0]["fields"]["full_name"] == [LEAD_NAME], "答えには 1 回だけ載る"
    (record,) = records()
    assert record["result"]["leads"] is None and record["result"]["not_kept"] == ["leads"]
    products = vm_call(vm, "ads_catalog_products", catalog=CATALOG)
    assert products["products"][0]["name"] == PRODUCT_NAME
    nothing_kept(vm, LEAD_NAME, PRODUCT_NAME)
    runs = list(pathlib.Path(ledger.state_dir_for(NAME)).glob("runs-*.ndjson"))
    assert runs and "ads_leads" in runs[0].read_text(), "読む口の runs の 1 行は手元と同じく書く（審査の 30 日）"


def test_ads_boostは全部PAUSEDで作りproductionが無ければads_not_productionで断る(vm):
    body = {"object_story_id": STORY, "lifetime": 500, "end": end_in(1)}
    assert vm_call(vm, "ads_boost", **body)["error"] == "ads_not_production"
    assert writes(vm) == []
    production()
    value = vm_call(vm, "ads_boost", **body)
    assert "error" not in value, value
    assert value["status"] == "PAUSED" and set(value["created"]) == {"campaign_id", "adset_id", "creative_id", "ad_id"}
    for call in writes(vm):
        if not call["path"].endswith("adcreatives"):
            assert call["form"]["status"] == ["PAUSED"], call["path"]
    assert all(o.get("status") in ("PAUSED", None) for o in vm["meta"].objects.values())
    raw = json.loads(pathlib.Path(ledger.path_for(NAME)).read_text())
    assert raw["ads_enabled"] is False and raw["ceilings"]["monthly"] == 0, "遠くの道は旗も上限も触らない"


def test_ads_boostは上限0ならads_days_over_ceilingで断る(vm):
    set_ledger(production=True)  # 上限は招待のとき（0）のまま
    value = vm_call(vm, "ads_boost", object_story_id=STORY, lifetime=500, end=end_in(1))
    assert value["error"] == "ads_days_over_ceiling" and writes(vm) == []
    production(days=3, lifetime=100)
    assert vm_call(vm, "ads_boost", object_story_id=STORY, lifetime=500, end=end_in(1))["error"] \
        == "ads_lifetime_over_ceiling"
    assert writes(vm) == []


def test_ads_boostのinstagram無しのthreadsは手元と同じ符丁で断る(vm):
    production()
    value = vm_call(vm, "ads_boost", object_story_id=STORY, lifetime=500, end=end_in(1), platforms="facebook,threads")
    assert value["error"] == "ads_threads_needs_instagram" and writes(vm) == []
    # IG の口座を結んでいない台帳なら instagram,threads は ads_instagram_unlinked（何も作らない）
    value = vm_call(vm, "ads_boost", object_story_id=STORY, lifetime=500, end=end_in(1), platforms="instagram,threads")
    assert value["error"] == "ads_instagram_unlinked" and writes(vm) == []


def test_止めた口座でads_boostは網に出ずads_pauseとads_stopは通る(vm):
    production()
    vm["meta"].campaign(status="ACTIVE")
    ledger.stop(NAME, "owner", by="masaru")
    assert vm_call(vm, "ads_boost", object_story_id=STORY, lifetime=500, end=end_in(1))["error"] == "account_stopped"
    assert not any(c["path"].endswith(("/campaigns", "/adsets", "/ads")) for c in writes(vm))
    paused = vm_call(vm, "ads_pause", all=True)
    assert "error" not in paused and paused["targets"], paused
    stopped = vm_call(vm, "ads_stop")
    assert "error" not in stopped and stopped["confirmed"] is True, stopped
    assert all(o.get("status") != "ACTIVE" for o in vm["meta"].objects.values())


def stop_leave(account):
    with leave_gate.lease(account, exclusive=True):
        with server_files.directory(leave_gate.location(), create=True, private=True) as fd:
            server_files.replace_at(fd, account + ".json",
                                    server_files.encode({"account": account, "phase": "stopped"}), private=True)


def test_退出で止めた広告の口座はaccount_stopped(vm):
    stop_leave(NAME)
    for operation, body in (("ads_show", {}), ("ads_stop", {})):
        value = vm_call(vm, operation, **body)
        assert value["error"] in ("account_stopped", "scope_unavailable"), value
    assert writes(vm) == []


def test_ads_activateとextendとresumeは遠くの道に無い(vm):
    for operation in ("ads_activate", "ads_extend", "ads_resume", "ads_end", "ads_budget", "ads_watch",
                      "ads_auth", "ads_add", "ads_collect"):
        assert operation not in server_ads.OPERATIONS
        assert vm_call(vm, operation)["error"] == "unsupported_operation", operation
    assert writes(vm) == []


def test_byは依頼の欄から名乗れず鍵の持ち主が記録される(vm):
    production()
    assert vm_call(vm, "ads_stop", by="someone-else")["error"] == "invalid_request"
    assert vm_call(vm, "ads_boost", object_story_id=STORY, lifetime=500, end=end_in(1), via="cli")["error"] \
        == "invalid_request"
    value = vm_call(vm, "ads_boost", object_story_id=STORY, lifetime=500, end=end_in(1))
    row = [r for r in ops.read_ops(NAME) if r["op"] == "boost"][-1]
    assert row["op_id"] == value["op_id"] and row["by"] == "person" and row["via"] == "api"


@pytest.mark.parametrize("operation,body", [
    ("ads_show", {"offline": True}),                                  # 出さない旗
    ("ads_catalog_products", {"catalog": CATALOG, "limit": "5"}),     # 型の違い
    ("ads_catalog_products", {"catalog": CATALOG, "limit": 500}),
    ("ads_insights", {"preset": "last_9000d"}),
    ("ads_estimate", {"targeting": "@/etc/passwd"}),                  # VM のファイルを読ませない
    ("ads_boost", {"object_story_id": STORY, "lifetime": True, "end": "2030-01-01T00:00:00+09:00"}),
    ("ads_boost", {"object_story_id": STORY, "lifetime": 500}),       # 必須の end が無い
    ("ads_boost", {"post": "1_2", "lifetime": 500, "end": "2030-01-01T00:00:00+09:00"}),
    ("ads_pause", {"ids": ["12", "../x"]}),
    ("ads_pause", {"all": "yes"}),
])
def test_知らない欄と型の違う欄は断る(vm, operation, body):
    production()
    assert vm_call(vm, operation, **body)["error"] == "invalid_request"
    assert writes(vm) == []


def test_pauseの対象が無ければ手元と同じ符丁で断る(vm):
    value = vm_call(vm, "ads_pause")
    assert value["error"] == "ads_target_missing" and writes(vm) == []


def test_MCPの書く口の表には足さない():
    assert not set(server_ads.OPERATIONS) & set(server_writes.WRITE_OPERATIONS)
    assert not set(server_ads.OPERATIONS) & set(server_writes.READ_OPERATIONS)


def test_Metaの断りの文は依頼の印に残さない(vm):
    vm["meta"].fail[("GET", V + f"/{CATALOG}/products")] = (400, {"error": {"code": 100, "message": META_SAYS}})
    value = vm_call(vm, "ads_catalog_products", catalog=CATALOG)
    # 手元の読む口は Meta の文を符丁に直して出す（`ads_http_400_code_100`）。答えには理由の 1 行が載る。
    assert value["error"] == "remote_command_refused" and value.get("reason"), value
    (record,) = records()
    assert record["result"]["reason"] is None and "reason" in record["result"]["not_kept"]
    nothing_kept(vm, META_SAYS)


def test_広告の口は口座の台帳の名前には効かず_口座の口は広告の名前に効かない(vm):
    vm["value"]["credentials"][0]["accounts"] = {NAME: PROJECT, "alpha": "alpha"}
    save_config(vm)
    assert vm_call(vm, "ads_show", account="alpha")["error"] == "scope_unavailable"
    assert vm_call(vm, "doctor")["error"] == "scope_unavailable"
    assert vm_call(vm, "posts")["error"] not in (None, "")


def test_新しい符丁は3つがそろい遠くの道の口を持つ():
    for code in sorted(server_ads.REASONS | {"ads_remote_unsupported"}):
        entry = refusal_catalog.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"] and "remote" in entry["doors"], code
    for code in ("ads_remote_unsupported", "account_registry_ambiguous"):
        assert refusal_catalog.CATALOG[code]["since"] == "5.13.0"


# ================================================================== 段 4: CLI の口（`thth/remote.py`）

from tests.test_v3140_remote_cli import home, run, worker  # noqa: E402,F401

STORY_END = "2030-01-02T00:00:00+09:00"
# 命令 → (operation, VM に届く欄)。`--by` は送らない（鍵の持ち主が記録される）。
ROUTES = [
    (("ads", "doctor", NAME, "--json"), "ads_doctor", {}),
    (("ads", "show", NAME, "--json"), "ads_show", {}),
    (("ads", "insights", NAME, "--preset", "last_7d", "--level", "campaign", "--by", "age-gender", "--json"),
     "ads_insights", {"preset": "last_7d", "level": "campaign", "breakdown": "age-gender"}),
    (("ads", "estimate", NAME, "--targeting", '{"age_min":18}', "--json"), "ads_estimate",
     {"targeting": '{"age_min":18}', "goal": "LINK_CLICKS"}),
    (("ads", "catalog", "list", NAME, "--json"), "ads_catalog_list", {}),
    (("ads", "catalog", "products", NAME, "--catalog", CATALOG, "--limit", "5", "--json"), "ads_catalog_products",
     {"catalog": CATALOG, "limit": 5}),
    (("ads", "leads", NAME, "--page", PAGE, "--form", FORM, "--json"), "ads_leads",
     {"page": PAGE, "form": FORM, "limit": 25}),
    (("ads", "network", NAME, "--since", "2026-11-01", "--json"), "ads_network", {"since": "2026-11-01"}),
    (("ads", "shop", NAME, "--json"), "ads_shop", {}),
    (("ads", "boost", NAME, "--object-story-id", STORY, "--lifetime", "500", "--end", STORY_END, "--by", "m",
      "--json"), "ads_boost",
     {"object_story_id": STORY, "lifetime": 500, "start": "now", "end": STORY_END, "platforms": "facebook",
      "objective": "engagement"}),
    (("ads", "pause", NAME, "--id", "111", "--id", "222", "--by", "m", "--json"), "ads_pause", {"ids": ["111", "222"]}),
    (("ads", "stop", NAME, "--by", "m", "--json"), "ads_stop", {}),
]


def test_出す口は12で設計の表と同じ():
    assert len(ROUTES) == len(remote.ADS_COMMANDS) == len(server_ads.OPERATIONS) == 12
    assert {op for _argv, op, _f in ROUTES} == set(server_ads.OPERATIONS)


@pytest.mark.parametrize("argv,operation,fields", ROUTES)
def test_台帳が無くloginしていればads各命令が遠くの道に届く(worker, capsys, argv, operation, fields):  # noqa: F811
    rc, _out, err = run(capsys, *argv)
    assert "台帳" not in err, err
    call = worker.calls[-1]
    assert call["path"] == "/api/v1/" + operation
    assert call["body"] == {"account": NAME, **fields}


def test_手元にadaccountsの台帳があれば遠くへ送らない(home, worker, capsys):  # noqa: F811
    data = ledger.build(NAME, act_id=ACT, project=PROJECT)
    ledger.write_new(NAME, data)
    rc, _out, _err = run(capsys, "ads", "show", NAME, "--json")
    assert worker.calls == [], "手元の台帳があれば手元の道"
    assert remote.has_ledger(NAME) is True


@pytest.mark.parametrize("argv", [
    ("ads", "activate", NAME, "123", "--by", "m"),
    ("ads", "extend", NAME, "123", "--end", STORY_END, "--by", "m"),
    ("ads", "resume", NAME, "--by", "m"),
    ("ads", "budget", "show", NAME),
])
def test_ads_activateは遠くの道でremote_unsupported(worker, capsys, argv):  # noqa: F811
    rc, _out, err = run(capsys, *argv)
    assert rc == 2 and err.startswith("ads_remote_unsupported"), err
    assert "通すには" in err and "もっと良く" in err
    assert worker.calls == []


def test_手元だけの旗は遠くの道で黙って捨てずに断る(worker, capsys):  # noqa: F811
    rc, _out, err = run(capsys, "ads", "show", NAME, "--offline")
    assert rc == 2 and err.startswith("remote_unsupported") and worker.calls == []


@pytest.fixture
def far(vm, tmp_path, monkeypatch):
    """CLI の側: `thth login` 済みの remote.json と、届いた依頼を VM の `api_requests.handle` で行う偽の Worker。
    VM と CLI は同じ THTH_ROOT を見るので、`--remote` で遠くの道を指す。"""
    fake = FakeWorker()

    def answer(call):
        op = call["path"].rsplit("/", 1)[-1]
        return 200, vm_call(vm, op, **{k: v for k, v in call["body"].items() if k != "account"}), None

    fake.default = answer
    config = tmp_path / "cfg" / "remote.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"url": fake.url, "key": "k3y_" + "B" * 39}))
    config.chmod(0o600)
    monkeypatch.setenv("THTH_REMOTE_CONFIG", str(config))
    monkeypatch.setattr(remote, "_sleep", lambda seconds: None)
    yield {**vm, "worker": fake}
    fake.close()


def cli_run(capsys, *argv):
    from thth import cli as cli_mod
    rc = cli_mod.main(list(argv))
    out, err = capsys.readouterr()
    return rc, out, err


def test_ads_boostの人向けの画面はお金が動いていないと言う(far, capsys):
    rc, out, err = cli_run(capsys, "ads", "boost", NAME, "--object-story-id", STORY, "--lifetime", "500",
                           "--end", end_in(1), "--by", "m", "--remote")
    assert rc != 0 and err.startswith("ads_not_production"), err
    assert "通すには" in err and writes(far) == []
    production()
    rc, out, err = cli_run(capsys, "ads", "boost", NAME, "--object-story-id", STORY, "--lifetime", "500",
                           "--end", end_in(1), "--by", "m", "--remote")
    assert rc == 0, err
    assert "全部 PAUSED・お金は動いていません" in out
    row = [r for r in ops.read_ops(NAME) if r["op"] == "boost"][-1]
    assert row["by"] == "person" and row["via"] == "api", "--by は送らない（鍵の持ち主）"


def test_遠くの道のleadsは端末に出してVMに残さない(far, capsys):
    rc, out, err = cli_run(capsys, "ads", "leads", NAME, "--page", PAGE, "--form", FORM, "--remote")
    assert rc == 0, err
    assert LEAD_NAME in out and "端末に出すだけで残しません" in out
    nothing_kept(far, LEAD_NAME)
    rc, out, err = cli_run(capsys, "ads", "show", NAME, "--remote")
    assert rc == 0 and "広告の口座 " + NAME in out, err
    rc, out, err = cli_run(capsys, "ads", "doctor", NAME, "--remote")
    assert "広告の口座の点検 " + NAME in out, err
