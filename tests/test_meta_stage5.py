"""Meta 計画 段 5: 広告（書く）。お金の柵（D-1〜D-5・監査 A-1〜A-6）。

偽の Graph API（loopback・状態を持つ）と試験の中で作った偽の app id・secret だけを使う。**本物の Meta は叩かない。**

縛ること（計画 段 5 の「柵」）:
- 月の上限 0・`spend_cap` 0・鍵の残り 13 日・`end_time` 無し・`currency: USD` の断り。
- **木の読み直し**: 1 段目のあと広告セットを足す・予算を変える → 2 段目が `ads_tree_changed`。
- `activate` は digest の id 以外に `status` を書かない（偽サーバの受けた要求を数える）。
- digest の golden（金額・期間・id・木の子のどれかを変えると変わる）。
- 非対称: 止めた口座で boost・activate・extend は断り、pause・stop・lower・end は通る。
- `stop`: pause を受けたのに `ACTIVE` のまま → 「止まった」と言わず hub への 1 行と出来事。80004 → 待たずに失敗。
- CLI と MCP に `spend_cap`・上限を上げる引数・道具が無い。書く口を呼ぶのは決まった関数だけ。
"""
from __future__ import annotations

import argparse
import ast
import datetime
import json
import pathlib
import re
import secrets
import sys
import time
import urllib.parse

import pytest

from thth import accounts, cli, jst
from thth.ads import budget, digest, graphwrite, ledger, ops, watch
from thth.ads import cli as ads_cli
from thth.meta import graph as meta_graph
from thth.meta import usage as meta_usage
from tests.test_facebook_adapter import fake_graph

V = "/" + meta_graph.GRAPH_VERSION
ACT = "1234567890"
NAME = "kopicha-ads"
PAGE = "1361492233719922"
STORY = PAGE + "_122096173233535853"
REPO = pathlib.Path(__file__).resolve().parents[1]
ADS_DIR = REPO / "thth" / "ads"


def opaque():
    return secrets.token_urlsafe(32)


class Meta:
    """状態を持つ偽の Meta（キャンペーン・広告セット・クリエイティブ・広告）。"""

    def __init__(self):
        self.objects = {}
        self.next_id = 900000
        self.spend_cap = "30000"
        self.amount_spent = "1200"
        self.currency = "JPY"
        self.account_status = 1
        self.spend = {}          # campaign_id → 今月の実支出（円の文字列）
        self.fail = {}           # (method, path) → (status, body)（1 回で消える）
        self.stuck = set()       # pause を受けても ACTIVE のまま返す id
        self.minimum_budgets = [{"currency": "JPY", "min_daily_budget_imp": 159},
                                {"currency": "USD", "min_daily_budget_imp": 100}]

    def new(self, kind, **fields):
        self.next_id += 1
        oid = str(self.next_id)
        self.objects[oid] = dict(fields, id=oid, kind=kind, account_id=ACT)
        return oid

    def effective(self, obj):
        oid = obj["id"]
        if oid in self.stuck:
            return "ACTIVE"
        status = obj.get("status")
        if status != "ACTIVE":
            return status
        if obj["kind"] in ("adset", "ad"):
            camp = self.objects.get(obj.get("campaign_id"))
            if camp and camp.get("status") != "ACTIVE":
                return "CAMPAIGN_PAUSED"
        if obj["kind"] == "ad":
            adset = self.objects.get(obj.get("adset_id"))
            if adset and adset.get("status") != "ACTIVE":
                return "ADSET_PAUSED"
        return "ACTIVE"

    def view(self, obj):
        out = {k: v for k, v in obj.items() if k not in ("kind",)}
        out["effective_status"] = self.effective(obj)
        for key in ("lifetime_budget", "daily_budget"):
            if key in out:
                out[key] = str(out[key])
        if obj["kind"] == "ad":
            creative = self.objects.get(obj.get("creative_id")) or {}
            out["creative"] = {"id": creative.get("id"), "object_story_id": creative.get("object_story_id")}
            out.pop("creative_id", None)
        return out

    def of_kind(self, kind, **match):
        return [self.view(o) for o in self.objects.values()
                if o["kind"] == kind and all(o.get(k) == v for k, v in match.items())]

    def campaign(self, *, status="PAUSED", lifetime=500, days=1, adsets=1, end=True, start_offset=0):
        now = int(jst.now_jst().timestamp())
        cid = self.new("campaign", name="c", objective="OUTCOME_ENGAGEMENT", status=status)
        for _ in range(adsets):
            fields = dict(name="s", campaign_id=cid, status=status, lifetime_budget=lifetime,
                          start_time=_ts(now + start_offset),
                          targeting={"geo_locations": {"countries": ["JP"]}, "publisher_platforms": ["facebook"]})
            if end:
                fields["end_time"] = _ts(now + days * 86400 - 60)
            sid = self.new("adset", **fields)
            crid = self.new("creative", object_story_id=STORY)
            self.new("ad", name="a", campaign_id=cid, adset_id=sid, creative_id=crid, status=status)
        return cid


def _ts(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone(datetime.timedelta(hours=9))).strftime(
        "%Y-%m-%dT%H:%M:%S+0900")


class Router(dict):
    """`fake_graph` の routes。決まった道は dict、id の道はここで解く。"""

    def __init__(self, meta, base):
        super().__init__(base)
        self.meta = meta

    def get(self, key, default=None):
        method, path = key
        fail = self.meta.fail.pop((method, path), None)
        if fail is not None:
            return lambda q, f, r: fail
        if key in self:
            return dict.get(self, key)
        m = re.fullmatch(re.escape(V) + r"/([0-9]+)(/adsets|/ads)?", path)
        if not m:
            return default
        oid, edge = m.group(1), m.group(2)
        meta = self.meta
        if method == "GET" and edge:
            kind = "adset" if edge == "/adsets" else "ad"
            return lambda q, f, r: (200, {"data": meta.of_kind(kind, campaign_id=oid)})
        if method == "GET":
            obj = meta.objects.get(oid)
            return (lambda q, f, r: (200, meta.view(obj))) if obj else default
        if method == "POST":
            def update(q, f, r):
                obj = meta.objects.get(oid)
                if obj is None:
                    return 400, {"error": {"code": 100, "message": "no"}}
                for k in ("status", "end_time"):
                    if k in f:
                        obj[k] = f[k][0]
                for k in ("lifetime_budget", "daily_budget"):
                    if k in f:
                        obj[k] = int(f[k][0])
                return 200, {"success": True}
            return update
        return default


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    home = tmp_path / "home"; home.mkdir()
    ledgers = tmp_path / "accounts"; ledgers.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home),
                       ("THTH_ACCOUNTS_DIR", ledgers)]:
        monkeypatch.setenv(key, str(value))
    data = {"app_id": str(secrets.randbelow(10**15)), "app_secret": opaque(), "token": opaque()}
    app = tmp_path / "app.env"
    app.write_text("META_APP_ID=" + data["app_id"] + "\nMETA_APP_SECRET=" + data["app_secret"] + "\n")
    app.chmod(0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app))
    meta = Meta()

    def account(q, f, r):
        fields = (q.get("fields") or [""])[0]
        if fields == "minimum_budgets":
            return 200, {"id": "act_" + ACT, "minimum_budgets": meta.minimum_budgets}
        if "," not in fields:
            return 200, {"id": "act_" + ACT, fields: "159" if fields == "min_daily_budget" else "0"}
        return 200, {"id": "act_" + ACT, "name": "こぴちゃ", "currency": meta.currency, "timezone_name": "Asia/Tokyo",
                     "account_status": meta.account_status, "amount_spent": meta.amount_spent,
                     "spend_cap": meta.spend_cap}

    def set_cap(q, f, r):
        meta.spend_cap = f["spend_cap"][0]
        return 200, {"success": True}

    def creator(kind):
        def create(q, f, r):
            fields = {k: v[0] for k, v in f.items() if k not in ("access_token", "appsecret_proof")}
            if kind == "ad":
                fields["creative_id"] = json.loads(fields.pop("creative"))["creative_id"]
                fields["campaign_id"] = meta.objects[fields["adset_id"]]["campaign_id"]
            if kind == "adset":
                fields["targeting"] = json.loads(fields["targeting"])
                fields["lifetime_budget"] = int(fields["lifetime_budget"])
            return 200, {"id": meta.new(kind, **fields)}
        return create

    def insights(q, f, r):
        level = (q.get("level") or ["account"])[0]
        if level == "campaign":
            return 200, {"data": [{"campaign_id": cid, "campaign_name": "c", "spend": str(v)}
                                  for cid, v in meta.spend.items()]}
        return 200, {"data": [{"spend": str(sum(meta.spend.values())), "impressions": "1", "clicks": "0",
                               "reach": "1"}]}

    base = {("GET", V + "/act_" + ACT): account, ("POST", V + "/act_" + ACT): set_cap,
            ("GET", V + f"/act_{ACT}/campaigns"): lambda q, f, r: (200, {"data": meta.of_kind("campaign")}),
            ("GET", V + f"/act_{ACT}/adsets"): lambda q, f, r: (200, {"data": meta.of_kind("adset")}),
            ("GET", V + f"/act_{ACT}/ads"): lambda q, f, r: (200, {"data": meta.of_kind("ad")}),
            ("GET", V + f"/act_{ACT}/insights"): insights,
            ("POST", V + f"/act_{ACT}/campaigns"): creator("campaign"),
            ("POST", V + f"/act_{ACT}/adsets"): creator("adset"),
            ("POST", V + f"/act_{ACT}/adcreatives"): creator("creative"),
            ("POST", V + f"/act_{ACT}/ads"): creator("ad")}
    with fake_graph(Router(meta, base)) as (url, calls):
        monkeypatch.setenv(meta_graph.BASE_URL_ENV, url)
        data.update(meta=meta, calls=calls, url=url, root=root)
        yield data


def setup_ledger(env, *, currency="JPY", production=True, ads_enabled=True, monthly=10000, lifetime_max=5000,
                 days_max=7, data_access_days=80, no_expiry=True, require_gate=None):
    assert cli.main(["ads", "add", NAME, "--act-id", ACT, "--project", "kopicha", "--page", "kopicha-fb",
                     "--by", "operator"]) == 0
    path = ledger.path_for(NAME)
    raw = json.loads(open(path, encoding="utf-8").read())
    # VM で運用が台帳を直接書く（THTH の CLI に上げる口は無い・D-1 の 3）。
    raw.update(currency=currency, timezone_name="Asia/Tokyo", production=production, ads_enabled=ads_enabled,
               ceilings={"monthly": monthly, "lifetime_budget_max": lifetime_max, "days_max": days_max},
               writes_refused=None if currency == "JPY" else ledger.CURRENCY_UNVERIFIED)
    if require_gate is not None:
        raw["require_gate"] = require_gate
    open(path, "w", encoding="utf-8").write(json.dumps(raw))
    cfg = ledger.load(NAME)
    token = {"access_token": env["token"], "token_type": "user", "obtained_at": jst.iso(),
             "scopes": ["ads_read", "ads_management", "business_management"],
             "data_access_expires_at": int(jst.now_jst().timestamp()) + int(data_access_days * 86400)}
    if no_expiry:
        token["no_expiry"] = True
    ledger.write_token(cfg, token)
    return cfg


def posts(env, path_part=""):
    return [c for c in env["calls"] if c["method"] == "POST" and path_part in c["path"]]


def status_posts(env):
    return {c["path"].rsplit("/", 1)[-1]: c["form"].get("status", [None])[0] for c in posts(env)
            if re.fullmatch(re.escape(V) + r"/[0-9]+", c["path"]) and "status" in c["form"]}


def end_in(days=1):
    return jst.iso(jst.now_jst() + datetime.timedelta(days=days) - datetime.timedelta(minutes=5))


def two_stage(cid, approval="A261031-1"):
    first = ops.activate(NAME, cid, by="masaru")
    assert first["stage"] == 1
    return first, ops.activate(NAME, cid, by="masaru", confirm=first["digest"], approval=approval)


# ================================================================== 書く口の縛り

def _ads_parser():
    parser = cli.build_parser()
    for action in parser._subparsers._group_actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices["ads"]


def _walk(parser, path=()):
    yield path, parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, child in action.choices.items():
                yield from _walk(child, (*path, name))


def test_CLIにspend_capや上限を上げる引数が無く_上限の名の引数はbudget_lowerだけ():
    seen = {}
    for path, parser in _walk(_ads_parser()):
        for action in parser._actions:
            for option in action.option_strings:
                seen.setdefault(option, set()).add(path)
                assert not re.search(r"spend|cap|raise|increase|enable|production|ceiling|resume|unstop", option), \
                    f"上げる引数がある: {path} {option}"
                if re.search(r"monthly|lifetime-max|days-max", option):
                    assert path == ("budget", "lower"), f"上限の名の引数が budget lower の外にある: {path} {option}"
    verbs = {p[0] for p in (path for path, _ in _walk(_ads_parser())) if p}
    assert not verbs & {"resume", "cap", "set", "enable", "raise", "unstop"}


def test_MCPの道具はshow_boost_pause_stopだけ_activateもextendも上げる引数も無い():
    sys.path.insert(0, str(REPO / "mcp"))
    import server
    names = {t["name"] for t in server.TOOLS if "ads" in t["name"]}
    assert names == set(ads_cli.MCP_TOOLS) == {"thth_ads_show", "thth_ads_boost", "thth_ads_pause", "thth_ads_stop"}
    for tool in server.TOOLS:
        assert not re.search(r"activate|extend|spend_cap|budget_lower|ads_end", tool["name"])
        if "ads" in tool["name"]:
            props = set((tool.get("inputSchema") or {}).get("properties") or {})
            assert not props & {"spend_cap", "monthly", "lifetime_max", "days_max", "ads_enabled", "production",
                                "confirm", "approval"}


def _calls_of(tree, wanted):
    """`(呼んだ関数名, 囲む関数名)` の並び。"""
    out = []

    def visit(node, owner):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, child.name if owner is None or isinstance(node, ast.ClassDef) else owner)
                continue
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr in wanted:
                out.append((child.func.attr, owner))
            visit(child, owner)
    visit(tree, None)
    return out


def test_書く口を呼ぶのは決まった命令の決まった関数だけ():
    wanted = set(graphwrite.ALLOWED_CALLERS)
    found = {}
    for path in (REPO / "thth").rglob("*.py"):
        if path.name == "graphwrite.py" and path.parent.name == "ads":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for method, owner in _calls_of(tree, wanted):
            assert path.name == "ops.py" and path.parent.name == "ads", f"{path} が {method} を呼んでいる"
            assert owner in graphwrite.ALLOWED_CALLERS[method], f"{method} を {owner} が呼んでいる"
            found.setdefault(method, set()).add(owner)
    assert found == graphwrite.ALLOWED_CALLERS
    # 作る口は status=PAUSED に決め打ち（引数で変えられない）・ACTIVE を書くのは set_active だけ。
    src = (ADS_DIR / "graphwrite.py").read_text(encoding="utf-8")
    assert src.count('"status": ACTIVE') == 1 and src.count('"status": PAUSED') == 4
    import inspect
    for fn in (graphwrite.Writer.create_campaign, graphwrite.Writer.create_adset, graphwrite.Writer.create_ad):
        assert "status" not in inspect.signature(fn).parameters


# ================================================================== boost（PAUSED で作る）

def test_boostは4つを全部PAUSEDで作り_終了日と通しの予算を送り_記録を残す(env):
    setup_ledger(env)
    out = ops.boost(NAME, by="masaru", object_story_id=STORY, lifetime_minor=500, start="now", end=end_in(1))
    created = out["created"]
    assert set(created) == {"campaign_id", "adset_id", "creative_id", "ad_id"}
    for c in posts(env, f"/act_{ACT}/"):
        if not c["path"].endswith("adcreatives"):
            assert c["form"]["status"] == ["PAUSED"], c["path"]
    adset = posts(env, "/adsets")[0]["form"]
    assert adset["lifetime_budget"] == ["500"] and adset["end_time"][0]
    targeting = json.loads(adset["targeting"][0])
    assert targeting["geo_locations"] == {"countries": ["JP"]} and targeting["age_min"] == 18
    assert targeting["targeting_automation"] == {"advantage_audience": 0}
    assert posts(env, "/campaigns")[0]["form"]["special_ad_categories"] == ["[]"]
    rows = [r for r in ops.read_ops(NAME) if r["op"] == "boost"]
    assert rows[-1]["result"] == "ok" and rows[-1]["created"] == created
    assert all(o.get("status") in ("PAUSED", None) for o in env["meta"].objects.values())


@pytest.mark.parametrize("kwargs,code", [
    ({"end": None}, "ads_end_time_required"),
    ({"lifetime_minor": 5001}, "ads_lifetime_over_ceiling"),
    ({"end": "+8d"}, "ads_days_over_ceiling"),
    ({"platforms": ("threads",)}, "ads_platform_unsupported"),
    ({"object_story_id": "123"}, "ads_object_story_id_invalid"),
])
def test_boostは終了日なし_上限越え_期間越え_形違いを作らない(env, kwargs, code):
    setup_ledger(env)
    args = dict(by="masaru", object_story_id=STORY, lifetime_minor=500, start="now", end=end_in(1))
    if kwargs.get("end") == "+8d":
        kwargs = dict(kwargs, end=end_in(8))
    args.update(kwargs)
    with pytest.raises(ops.OpsRefused) as exc:
        ops.boost(NAME, **args)
    assert exc.value.code == code
    assert not posts(env)


def test_boostは上限0の口座では作らない_productionがfalseでも作らない(env):
    setup_ledger(env, lifetime_max=0)
    with pytest.raises(ops.OpsRefused) as exc:
        ops.boost(NAME, by="m", object_story_id=STORY, lifetime_minor=500, start="now", end=end_in(1))
    assert exc.value.code == "ads_lifetime_over_ceiling"
    raw = json.loads(open(ledger.path_for(NAME)).read()); raw["production"] = False
    open(ledger.path_for(NAME), "w").write(json.dumps(raw))
    with pytest.raises(ops.OpsRefused) as exc:
        ops.boost(NAME, by="m", object_story_id=STORY, lifetime_minor=500, start="now", end=end_in(1))
    assert exc.value.code == "ads_not_production"
    assert not posts(env)


def test_boostのページを使えない断りは_共有と割り当てを案内する(env, capsys):
    setup_ledger(env)
    env["meta"].fail[("POST", V + f"/act_{ACT}/adcreatives")] = (
        400, {"error": {"code": 100, "error_subcode": 1487194, "message": "page not usable"}})
    rc = cli.main(["ads", "boost", NAME, "--object-story-id", STORY, "--lifetime", "500", "--end", end_in(1),
                   "--by", "masaru"])
    err = capsys.readouterr().err
    assert rc == 2 and "ads_boost_failed" in err and "ページの共有" in err and "割り当て" in err
    row = [r for r in ops.read_ops(NAME) if r["op"] == "boost"][-1]
    assert row["result"] == "failed" and row["failed_step"] == "creative" and "adset_id" in row["created"]


def test_boostのpostは結んだページの口座の送った記録から引く(env, monkeypatch):
    setup_ledger(env)
    monkeypatch.setattr(ops.sent, "read", lambda state, post: {"post_id": post} if post == STORY else None)
    monkeypatch.setattr(ops.accounts, "load_account", lambda name: {"media": "facebook", "page_id": PAGE})
    out = ops.boost(NAME, by="m", post=STORY, lifetime_minor=500, start="now", end=end_in(1))
    assert out["plan"]["source"] == "sent:kopicha-fb"
    with pytest.raises(ops.OpsRefused) as exc:
        ops.boost(NAME, by="m", post=PAGE + "_999", lifetime_minor=500, start="now", end=end_in(1))
    assert exc.value.code == "ads_post_not_found"


# ================================================================== activate（2 段・木の読み直し）

def test_activateは2段で_digestの木だけをACTIVEにし_外のキャンペーンに触らない(env):
    setup_ledger(env)
    other = env["meta"].campaign()            # 同じ口座の別のキャンペーン（触ってはいけない）
    cid = env["meta"].campaign()
    first, second = two_stage(cid)
    assert second["stage"] == 2 and second["amount_minor"] == 500
    tree_ids = {o["id"] for o in env["meta"].objects.values()
                if o.get("campaign_id") == cid and o["kind"] in ("adset", "ad")} | {cid}
    written = status_posts(env)
    assert set(written) == tree_ids and set(written.values()) == {"ACTIVE"}
    assert not any(o.get("status") == "ACTIVE" for o in env["meta"].objects.values() if o.get("campaign_id") == other
                   or o["id"] == other)
    # 記録: THTH が始めた物（watch は外の配信と言わない・A-6）・予約・hub の番号の記録。
    assert set(watch._thth_activated(NAME)) == tree_ids
    month = watch.local_month(ledger.load(NAME), jst.now_jst())
    assert budget.load(NAME, month)["reserved_minor"] == 500
    row = [r for r in ops.read_ops(NAME) if r["op"] == "activate"][-1]
    assert row["approval"] == "A261031-1" and row["digest"] == first["digest"] and row["result"] == "ok"


def test_1段目のあと広告セットを足すと2段目はads_tree_changedで何も始めない(env):
    setup_ledger(env)
    meta = env["meta"]
    cid = meta.campaign()
    first = ops.activate(NAME, cid, by="masaru")
    meta.new("adset", name="足された", campaign_id=cid, status="ACTIVE", lifetime_budget=400,
             start_time=_ts(int(jst.now_jst().timestamp())), end_time=_ts(int(jst.now_jst().timestamp()) + 3600), targeting={})
    with pytest.raises(ops.OpsRefused) as exc:
        ops.activate(NAME, cid, by="masaru", confirm=first["digest"], approval="A261031-1")
    assert exc.value.code == "ads_tree_changed"
    assert not status_posts(env)


def test_1段目のあと予算を変えると2段目はads_tree_changed(env):
    setup_ledger(env)
    meta = env["meta"]
    cid = meta.campaign()
    first = ops.activate(NAME, cid, by="masaru")
    adset = next(o for o in meta.objects.values() if o["kind"] == "adset" and o["campaign_id"] == cid)
    adset["lifetime_budget"] = 4000
    with pytest.raises(ops.OpsRefused) as exc:
        ops.activate(NAME, cid, by="masaru", confirm=first["digest"], approval="A261031-1")
    assert exc.value.code == "ads_tree_changed"
    assert not status_posts(env)


@pytest.mark.parametrize("setup,code", [
    (dict(monthly=0), "ads_budget_exhausted"),
    (dict(ads_enabled=False), "ads_disabled"),
    (dict(production=False), "ads_not_production"),
    (dict(data_access_days=13), "ads_token_expiring"),
    (dict(currency="USD"), "ads_currency_unverified"),
])
def test_activateの柵_月の上限0_旗_鍵の残り13日_通貨(env, setup, code):
    setup_ledger(env, **setup)
    cid = env["meta"].campaign()
    with pytest.raises((ops.OpsRefused, budget.BudgetRefused)) as exc:
        two_stage(cid)
    assert exc.value.code == code
    assert not status_posts(env)


def test_activateはspend_capが0なら断る(env):
    setup_ledger(env)
    env["meta"].spend_cap = "0"
    cid = env["meta"].campaign()
    with pytest.raises(ops.OpsRefused) as exc:
        two_stage(cid)
    assert exc.value.code == "ads_spend_cap_missing"
    assert not status_posts(env)


def test_activateは終了日の無い広告セットと期間越えと上限越えを始めない(env):
    setup_ledger(env)
    for kwargs, code in ((dict(end=False), "ads_end_time_missing"), (dict(days=8), "ads_days_over_ceiling"),
                         (dict(lifetime=5001), "ads_lifetime_over_ceiling")):
        cid = env["meta"].campaign(**kwargs)
        with pytest.raises(ops.OpsRefused) as exc:
            ops.activate(NAME, cid, by="masaru")
        assert exc.value.code == code
    assert not status_posts(env)


def test_activateはhubの番号が無いか形が違えば断る_番号は記録として残る(env):
    setup_ledger(env)
    cid = env["meta"].campaign()
    first = ops.activate(NAME, cid, by="masaru")
    for approval in (None, "ok", "A2610-1"):
        with pytest.raises(ops.OpsRefused) as exc:
            ops.activate(NAME, cid, by="masaru", confirm=first["digest"], approval=approval)
        assert exc.value.code == "approval_gate_required"
    assert not status_posts(env)


def test_月の上限は予約の外の実支出も数える(env):
    setup_ledger(env, monthly=10000)
    env["meta"].spend["777"] = 9600            # THTH の外のキャンペーンの今月の支出
    cid = env["meta"].campaign(lifetime=500)
    with pytest.raises(budget.BudgetRefused) as exc:
        two_stage(cid)
    assert exc.value.code == "ads_budget_exhausted"
    env["meta"].spend["777"] = 9400
    _first, second = two_stage(cid)
    assert second["stage"] == 2


def test_予約は月の上限を越えない_2本目は断る(env):
    setup_ledger(env, monthly=800)
    two_stage(env["meta"].campaign(lifetime=500))
    with pytest.raises(budget.BudgetRefused) as exc:
        two_stage(env["meta"].campaign(lifetime=500))
    assert exc.value.code == "ads_budget_exhausted"


# ================================================================== digest の golden

GOLDEN_TREE = {
    "campaign": {"id": "10", "objective": "OUTCOME_ENGAGEMENT", "status": "PAUSED", "effective_status": "PAUSED",
                 "start_time": None, "stop_time": None},
    "adsets": [{"id": "20", "campaign_id": "10", "status": "PAUSED", "effective_status": "PAUSED",
                "lifetime_budget": 500, "start_time": "2026-11-01T10:00:00+0900",
                "end_time": "2026-11-02T10:00:00+0900", "targeting_sha": "ab" * 32,
                "publisher_platforms": ["facebook"]}],
    "ads": [{"id": "30", "adset_id": "20", "status": "PAUSED", "effective_status": "PAUSED",
             "creative": {"object_story_id": STORY}}],
}
GOLDEN = "2edaa84b9b19"


def _golden(tree=None, **kw):
    return digest.compute_ads_digest(op=kw.get("op", "activate"), act_id=kw.get("act_id", ACT),
                                     currency=kw.get("currency", "JPY"), tree=tree or GOLDEN_TREE,
                                     change=kw.get("change"))


def test_digestのgolden_金額_期間_id_木の子_通貨のどれを変えても変わる():
    import copy
    base = _golden()
    assert base == GOLDEN, base
    variants = []
    for path, value in ((("adsets", 0, "lifetime_budget"), 501), (("adsets", 0, "end_time"), "2026-11-03T10:00:00+0900"),
                        (("adsets", 0, "start_time"), "2026-11-01T11:00:00+0900"), (("ads", 0, "id"), "31"),
                        (("adsets", 0, "targeting_sha"), "cd" * 32), (("ads", 0, "status"), "ACTIVE"),
                        (("campaign", "id"), "11")):
        tree = copy.deepcopy(GOLDEN_TREE)
        target = tree
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        variants.append(_golden(tree))
    extra = copy.deepcopy(GOLDEN_TREE)
    extra["adsets"].append(dict(GOLDEN_TREE["adsets"][0], id="21"))
    variants += [_golden(extra), _golden(currency="USD"), _golden(act_id="999"), _golden(op="extend"),
                 _golden(change={"adset_id": "20", "lifetime_budget": 600})]
    assert base not in variants and len(set(variants)) == len(variants)


# ================================================================== 非対称（止めた口座）

def test_止めた口座は_boost_activate_extendを断り_pause_stop_lower_endは通す(env, capsys):
    setup_ledger(env)
    meta = env["meta"]
    cid = meta.campaign(status="ACTIVE", days=3)
    adset = next(o["id"] for o in meta.objects.values() if o["kind"] == "adset" and o["campaign_id"] == cid)
    ledger.stop(NAME, "owner", by="masaru")
    for call in (lambda: ops.boost(NAME, by="m", object_story_id=STORY, lifetime_minor=500, start="now",
                                   end=end_in(1)),
                 lambda: ops.activate(NAME, cid, by="m"),
                 lambda: ops.extend(NAME, adset, by="m", lifetime_minor=600)):
        with pytest.raises(accounts.AccountStopped):
            call()
    rc = cli.main(["ads", "boost", NAME, "--object-story-id", STORY, "--lifetime", "500", "--end", end_in(1),
                   "--by", "m"])
    assert rc == 2 and "adaccount_stopped" in capsys.readouterr().err
    assert not posts(env)
    assert ops.lower_budget(NAME, adset, by="m", lifetime_minor=400)["result"] == "ok"
    assert ops.end(NAME, adset, by="m", end_at=end_in(2))["result"] == "ok"
    assert ops.pause(NAME, by="m", ids=(adset,))["result"] == "ok"
    assert ops.stop(NAME, by="m")["confirmed"] is True
    assert {c["path"] for c in posts(env)} >= {V + "/" + adset, V + "/" + cid}


def test_量の制限の待ちの間_作るは網に出ず_止めるは待たずに出る(env):
    setup_ledger(env)
    cid = env["meta"].campaign(status="ACTIVE")
    meta_usage.note_rate_limited(None, None, 80004, path=ledger.usage_path_for(NAME))
    with pytest.raises(ops.OpsRefused) as exc:
        ops.boost(NAME, by="m", object_story_id=STORY, lifetime_minor=500, start="now", end=end_in(1))
    assert "meta_rate_limited" in exc.value.message
    assert not posts(env)
    row = ops.stop(NAME, by="m")
    assert row["confirmed"] is True and status_posts(env)[cid] == "PAUSED"


def test_通貨がUSDなら書く操作は全部断り_止めるは通る(env):
    setup_ledger(env, currency="USD")
    env["meta"].currency = "USD"
    cid = env["meta"].campaign(status="ACTIVE")
    adset = next(o["id"] for o in env["meta"].objects.values() if o["kind"] == "adset")
    for call in (lambda: ops.boost(NAME, by="m", object_story_id=STORY, lifetime_minor=500, start="now",
                                   end=end_in(1)),
                 lambda: ops.activate(NAME, cid, by="m"),
                 lambda: ops.extend(NAME, adset, by="m", lifetime_minor=600)):
        with pytest.raises(ops.OpsRefused) as exc:
            call()
        assert exc.value.code == ledger.CURRENCY_UNVERIFIED
    assert not posts(env)
    assert ops.pause(NAME, by="m", all_=True)["result"] == "ok"


# ================================================================== stop

def test_stopはpauseを受けたのにACTIVEのままなら止まったと言わず_hubの1行と最優先の出来事(env, capsys):
    setup_ledger(env)
    meta = env["meta"]
    cid = meta.campaign(status="ACTIVE")
    meta.stuck.add(cid)
    rc = cli.main(["ads", "stop", NAME, "--by", "masaru"])
    out = capsys.readouterr().out
    assert rc == 3 and "止まったと確かめられません" in out and "waiting_on_masaru" in out and "act_" + ACT in out
    row = [r for r in ops.read_ops(NAME) if r["op"] == "stop"][-1]
    assert row["confirmed"] is False and row["steps"]["verify"]["ok"] is False
    assert row["steps"]["thth_stop"]["ok"] is True and ledger.stopped(NAME)
    events = watch.events(NAME, ledger.load(NAME), None, now=jst.now_jst(),
                          make=lambda kind, ref, summary, **extra: kind)
    assert events[0] == "ads_stop_unconfirmed"
    meta.stuck.clear()
    assert ops.stop(NAME, by="masaru")["confirmed"] is True
    assert "ads_stop_unconfirmed" not in watch.events(NAME, ledger.load(NAME), None, now=jst.now_jst(),
                                                      make=lambda kind, ref, summary, **extra: kind)


def test_stopは80004で断られたら待たずにすぐ失敗とし_止まったと言わない(env):
    setup_ledger(env)
    meta = env["meta"]
    cid = meta.campaign(status="ACTIVE")
    meta.fail[("POST", V + "/" + cid)] = (400, {"error": {"code": 80004, "message": "too many"}})
    started = time.monotonic()
    row = ops.stop(NAME, by="masaru")
    assert time.monotonic() - started < 5
    assert row["confirmed"] is False and row["steps"]["pause"]["failed"][0]["id"] == cid
    assert "hub_line" in row


def test_stopはspend_capを使った額まで下げる_0は書かない(env):
    setup_ledger(env)
    meta = env["meta"]
    meta.campaign(status="ACTIVE")
    row = ops.stop(NAME, by="masaru")
    assert row["steps"]["spend_cap"] == {"ok": True, "before": 30000, "after": 1200}
    assert meta.spend_cap == "1200"
    meta.amount_spent = "0"
    row = ops.stop(NAME, by="masaru")
    assert row["steps"]["spend_cap"]["ok"] is None   # 0 は「上限なし」になるので書かない


def test_lower_spend_capは0と今以上を書かない(env):
    cfg = setup_ledger(env)
    writer = graphwrite.Writer(NAME, cfg, ledger.load_token(cfg))
    for amount, current, code in ((0, 30000, "ads_spend_cap_zero_refused"), (0, 0, "ads_spend_cap_zero_refused"),
                                  (30000, 30000, "ads_not_lower"), (30001, 30000, "ads_not_lower")):
        with pytest.raises(graphwrite.AdsWriteError) as exc:
            writer.lower_spend_cap(amount, current_minor=current)
        assert exc.value.code == code   # 0 は「上限なし」＝上げるのと同じ。金額の直しより前に名指しで断る
    assert not posts(env)
    writer.lower_spend_cap(1000, current_minor=0)   # 上限なし（0）からはどの正の値も「下げる」
    assert env["meta"].spend_cap == "1000"


# ================================================================== 減らす・早める・延ばす・台帳の上限

def test_budget_lowerは下げるだけ_上げる値は断る(env, capsys):
    setup_ledger(env)
    assert cli.main(["ads", "budget", "lower", NAME, "--monthly", "8000", "--by", "m"]) == 0
    assert ledger.load(NAME)["ceilings"]["monthly"] == 8000
    assert cli.main(["ads", "budget", "lower", NAME, "--monthly", "9000", "--by", "m"]) == 2
    assert "ads_not_lower" in capsys.readouterr().err
    assert ledger.load(NAME)["ceilings"]["monthly"] == 8000
    with pytest.raises(ledger.AdAccountError):
        ledger.lower_ceiling(NAME, "days_max", 7, by="m")
    cid = env["meta"].campaign(lifetime=500)
    adset = next(o["id"] for o in env["meta"].objects.values() if o["kind"] == "adset" and o["campaign_id"] == cid)
    assert ops.lower_budget(NAME, adset, by="m", lifetime_minor=300)["result"] == "ok"
    with pytest.raises(ops.OpsRefused) as exc:
        ops.lower_budget(NAME, adset, by="m", lifetime_minor=400)
    assert exc.value.code == "ads_not_lower"


def test_endは早めるだけ_延ばすのはextendで2段(env):
    setup_ledger(env)
    cid = env["meta"].campaign(days=2)
    adset = next(o["id"] for o in env["meta"].objects.values() if o["kind"] == "adset" and o["campaign_id"] == cid)
    with pytest.raises(ops.OpsRefused) as exc:
        ops.end(NAME, adset, by="m", end_at=end_in(5))
    assert exc.value.code == "ads_not_earlier"
    assert ops.end(NAME, adset, by="m", end_at=end_in(1))["result"] == "ok"
    first = ops.extend(NAME, adset, by="m", end=end_in(3))
    assert first["stage"] == 1 and not [c for c in posts(env) if "end_time" in c["form"] and c["form"]["end_time"][0] == first["change"]["end_time"]]
    second = ops.extend(NAME, adset, by="m", end=end_in(3), confirm=first["digest"], approval="A261031-2")
    assert second["stage"] == 2
    with pytest.raises(ops.OpsRefused) as exc:
        ops.extend(NAME, adset, by="m", end=end_in(9))
    assert exc.value.code == "ads_days_over_ceiling"
    with pytest.raises(ops.OpsRefused) as exc:
        ops.extend(NAME, adset, by="m", lifetime_minor=400)
    assert exc.value.code == "ads_not_higher"


def test_extendの1段目のあと予算が変われば2段目はads_tree_changed(env):
    setup_ledger(env)
    meta = env["meta"]
    cid = meta.campaign(days=2)
    adset = next(o for o in meta.objects.values() if o["kind"] == "adset" and o["campaign_id"] == cid)
    first = ops.extend(NAME, adset["id"], by="m", lifetime_minor=800)
    adset["lifetime_budget"] = 600
    with pytest.raises(ops.OpsRefused) as exc:
        ops.extend(NAME, adset["id"], by="m", lifetime_minor=800, confirm=first["digest"], approval="A261031-2")
    assert exc.value.code == "ads_tree_changed"


# ================================================================== 鍵の期限・show（本番 10/30〜31 の事実）

def test_鍵は期限なしと言い_14日の柵はデータに触れる期限で効く(env):
    cfg = setup_ledger(env, data_access_days=80)
    state = watch.token_state(cfg, now=jst.now_jst())
    assert state["no_expiry"] is True and state["basis"] == "data_access" and 79 < state["remaining_days"] < 81
    assert "期限なし" in watch.token_text(state)
    token = ledger.load_token(cfg)
    token["data_access_expires_at"] = int(jst.now_jst().timestamp()) + 13 * 86400
    ledger.write_token(cfg, token)
    assert watch.token_state(cfg, now=jst.now_jst())["state"] == watch.ADS_TOKEN_EXPIRING
    # 鍵の期限とデータの期限の早いほう。
    limits = ledger.token_limits({"expires_at": 100, "data_access_expires_at": 50})
    assert limits["effective_at"] == 50 and limits["basis"] == "data_access"
    limits = ledger.token_limits({"expires_at": 0, "data_access_expires_at": 50})
    assert limits["no_expiry"] and limits["effective_at"] == 50


def test_doctorはdebug_tokenの今の値で鍵の期限の欄を書き直す(env):
    cfg = setup_ledger(env)
    token = ledger.load_token(cfg)
    token.pop("no_expiry"); token.pop("data_access_expires_at")
    ledger.write_token(cfg, token)
    assert watch.token_state(cfg, now=jst.now_jst())["remaining_days"] is None
    later = int(jst.now_jst().timestamp()) + 90 * 86400
    assert ledger.refresh_token_expiry(cfg, {"expires_at": 0, "data_access_expires_at": later})
    saved = ledger.load_token(cfg)
    assert saved["no_expiry"] is True and saved["data_access_expires_at"] == later
    assert saved["access_token"] == env["token"]


def test_showのminimum_budgetsは口座の通貨の行だけ(env, capsys):
    setup_ledger(env)
    capsys.readouterr()
    assert cli.main(["ads", "show", NAME, "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    mins = value["account_extras"]["minimum_budgets"]
    assert mins["currency"] == "JPY" and mins["of"] == 2 and [r["currency"] for r in mins["rows"]] == ["JPY"]
    assert value["token"]["no_expiry"] is True


def test_THTHが始めた物は外の配信と言わず_外で始めた物は言う(env):
    setup_ledger(env)
    meta = env["meta"]
    ours = meta.campaign()
    two_stage(ours)
    theirs = meta.campaign(status="ACTIVE")
    watch.collect(NAME, force=True)
    kinds = {e["kind"]: e for e in watch.events(NAME, ledger.load(NAME), watch.read_snapshot(NAME),
                                                now=jst.now_jst(),
                                                make=lambda kind, ref, summary, **extra: dict(extra, kind=kind))}
    assert set(kinds["ads_active_outside_thth"]["ids"]) == {o["id"] for o in meta.objects.values()
                                                            if o.get("campaign_id") == theirs
                                                            and o["kind"] in ("adset", "ad")} | {theirs}


def test_MCPのboostはviaにmcpを付けてCLIを呼ぶ_pauseはallかidsのどちらか(monkeypatch):
    sys.path.insert(0, str(REPO / "mcp"))
    import server
    seen = []
    monkeypatch.setattr(server, "run_cli", lambda args, **kw: (seen.append(args), type(
        "P", (), {"stdout": "{}", "stderr": "", "returncode": 0})())[1])
    server.call_tool("thth_ads_boost", {"adaccount": NAME, "object_story_id": STORY, "lifetime": 500,
                                        "end": "2026-11-02T10:00+09:00", "by": "llm"})
    assert seen[-1][:3] == ["ads", "boost", NAME] and "--via" in seen[-1] and "mcp" in seen[-1]
    with pytest.raises(server.ToolInputError):
        server.call_tool("thth_ads_pause", {"adaccount": NAME, "by": "llm"})
    server.call_tool("thth_ads_stop", {"adaccount": NAME, "by": "llm"})
    assert seen[-1][:3] == ["ads", "stop", NAME]
