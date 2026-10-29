"""Meta 計画 段 3: Instagram（`thth/adapters/instagram.py`・`instagram_media.py`・`auth_instagram.py`）。

**本物の Graph API は叩かない。** loopback の偽 Graph と、試験の中の偽の relay だけ。ここで通ったことは
「読んだ文書のとおりに投げ、返った形を正しく写す」の証明で、Meta の仕様と合っていることの証明ではない
（それは本番の読むだけの確かめと試しの 1 本で見る）。

柵（計画 段 3）: 2 段の公開と `status_code` の遷移（IN_PROGRESS→FINISHED・ERROR・5 分で諦める）・PNG を断る・
カルーセル 21 枚を断る・`quota_total` を 50 で読み THTH の post.daily が内側・入れ物の id の照合・relay の URL の照合。
"""
from __future__ import annotations

import hashlib
import inspect
import json
import secrets
import time
import urllib.parse
from pathlib import Path

import pytest

from thth import (accounts, adapters as adapters_mod, approval, edit_cli, fingerprint_golden, inflight,
                  leave_gate, maintain, media, media_delivery, media_relay, media_traits, oauth, authflow)
from thth.adapters import base
from thth.adapters import facebook as fb
from thth.adapters import instagram as ig
from thth.adapters import instagram_media as igm
from thth.adapters import auth_instagram as aig
from tests.test_facebook_adapter import fake_graph
from tests.test_v213_media_foundation import jpeg, png

TOKEN = "ig-page-token-do-not-leak-0123456789"
IG = "17841437318552220"
PAGE = "1361492233719922"
V = "/" + fb.GRAPH_VERSION


def adapter(base_url, **kw):
    a = ig.InstagramAdapter(base_url=base_url, access_token=TOKEN, ig_user_id=IG, page_id=PAGE, **kw)
    a.poll_interval = 0
    a.poll_total = 5
    return a


def limit_route(usage=0, total=100):
    return (200, {"data": [{"quota_usage": usage, "config": {"quota_total": total, "quota_duration": 86400}}]})


# ================================================================== 境界・表

def test_REGISTRYにinstagramが載り境界の語彙に収まる():
    cls = adapters_mod.REGISTRY["instagram"]
    assert cls is ig.InstagramAdapter
    assert set(cls.CAPABILITIES) <= base.KNOWN_CAPABILITIES
    assert cls.capabilities() == {"recent_posts", "thread_read", "views", "account_insights", "carousel",
                                  "hide_reply", "comment_control", "quota"}
    assert "edit_post" not in cls.CAPABILITIES
    assert cls.TOKEN_NO_EXPIRY is True and cls.AUTH_NEEDS_APP_ENV is True and cls.META_GRAPH is True
    assert cls.DELETE_PERMISSION == "instagram_manage_contents"
    assert cls.is_post_id(IG) and not cls.is_post_id(PAGE + "_1") and not cls.is_post_id("1/../2")
    assert "quota" in adapters_mod.REGISTRY["facebook"].CAPABILITIES


def test_新しい口はleave_gateのbindが包み_止めた口座では呼べない():
    src = inspect.getsource(leave_gate.bind)
    native = next(row for row in src.splitlines() if row.strip().startswith("native="))
    assert "InstagramAdapter" in native
    for name in ("publish", "publishing_limit", "set_comments_enabled", "fetch_reply", "hide_reply",
                 "delete_reply", "delete_post", "conversation", "insights", "account_insights",
                 "followers_count", "quota", "probe", "_request", "_get"):
        assert f"'{name}'" in src, f"leave_gate.bind が {name} を包んでいない"


def test_コメント欄の開け閉めを呼ぶのはedit_cliだけ():
    from thth import cli as cli_mod, collect as collect_mod, core, doctor, maintain as maintain_mod, retract_cli
    for mod in (cli_mod, core, collect_mod, doctor, maintain_mod, retract_cli):
        assert ".set_comments_enabled(" not in inspect.getsource(mod)
    assert inspect.getsource(edit_cli).count(".set_comments_enabled(") == 1
    assert inspect.getsource(ig).count('"DELETE"') == 2   # 投稿の取り下げとコメントの削除だけ


def test_台帳の性質_上限の根拠は本番の100で_THTHのpostdailyはその内側():
    doc = media_traits.load("instagram")
    limit = next(t for t in doc["traits"] if t["id"] == "instagram.publish_limit")
    assert limit["evidence"]["type"] == "observed" and "quota_total 100" in limit["evidence"]["record"]
    assert limit["confidence"] == "high"
    daily = ig.thth_post_daily()
    assert daily is not None and daily < 50 <= 100


# ================================================================== 添付の検査

def _manifest(n=1, **row_over):
    row = {"file": "a.jpg", "alt": "赤い点", "source_sha256": "0" * 64, "size": 100, "role": "media",
           "index": 1, "public_sha256": "1" * 64, "public_size": 100, "format": "jpeg", "kind": "image",
           "width": 1080, "height": 1080, "duration": None, "orientation": 1}
    row.update(row_over)
    return {"files": [dict(row) for _ in range(n)], "attachments": [], "post_options": {}, "captions": []}


@pytest.mark.parametrize("over,reason", [
    ({}, None),
    ({"format": "png"}, "unsupported_attachment: instagram/png"),
    ({"format": "gif"}, "unsupported_attachment: instagram/gif"),
    ({"format": "mp4", "kind": "video"}, "unsupported_attachment: instagram/mp4"),
    ({"public_size": 8_000_000}, None),
    ({"public_size": 8_000_001}, "media_limit_exceeded: bytes (SI 8000000)"),
    ({"width": 800, "height": 1000}, None),                     # 4:5
    ({"width": 799, "height": 1000}, "media_limit_exceeded: aspect_ratio (4:5〜1.91:1)"),
    ({"width": 1910, "height": 1000}, None),                    # 1.91:1
    ({"width": 1920, "height": 1000}, "media_limit_exceeded: aspect_ratio (4:5〜1.91:1)"),
    ({"width": 1000, "height": 1910, "orientation": 6}, None),  # 回転で 1.91:1
    ({"alt": "あ" * 1001}, "media_limit_exceeded: alt_text (1000)"),
])
def test_画像はJPEGだけ_8MB_縦横比_altの長さ(over, reason):
    assert igm.intent_error(_manifest(**over)) == reason


@pytest.mark.parametrize("n,ok", [(1, True), (2, True), (20, True), (21, False)])
def test_カルーセルは20枚まで_21枚を断る(n, ok):
    assert (igm.intent_error(_manifest(n)) is None) == ok
    if not ok:
        assert igm.intent_error(_manifest(n)) == "media_limit_exceeded: count (20)"


def test_型つきの添付と任意項目は断り_is_ai_generatedだけ通す():
    m = _manifest()
    m["post_options"] = {"is_ai_generated": True}
    assert igm.intent_error(m) is None
    m["post_options"] = {"reply_control": "everyone"}
    assert igm.intent_error(m) == "unsupported_attachment: instagram/post_options"
    m = _manifest()
    m["files"] = []
    assert igm.intent_error(m) == "unsupported_attachment: instagram/no_media"


def test_原稿のpost_optionsはinstagramにis_ai_generatedだけを許す():
    media.validate_declarations({"media": [{"file": "a.jpg", "alt": "a"}],
                                 "post_options": {"is_ai_generated": True}}, "instagram")
    with pytest.raises(media.MediaError):
        media.validate_declarations({"media": [{"file": "a.jpg", "alt": "a"}],
                                     "post_options": {"is_ai_generated": "yes"}}, "instagram")
    with pytest.raises(media.MediaError):
        media.validate_declarations({"media": [{"file": "a.jpg", "alt": "a"}],
                                     "post_options": {"is_ai_generated": True}}, "facebook")


# ================================================================== 指紋

def test_指紋の版は上げず_既存の原稿の指紋はgoldenのまま():
    assert approval.FINGERPRINT_VERSION == 2
    assert fingerprint_golden.drift() == []


def test_is_ai_generatedは添付の指紋に入る_変えれば承認が古くなる():
    plain = _manifest()
    marked = _manifest()
    marked["post_options"] = {"is_ai_generated": True}
    args = dict(section="本文", account="kopicha-ig", reply_to=None, topic=None,
                publish_at="2030-01-01T12:00:00+09:00")
    a = approval.compute_approved_sha(**args, media_manifest=plain)
    b = approval.compute_approved_sha(**args, media_manifest=marked)
    assert a != b
    # 添付の無い原稿の指紋は段 3 の前と同じ入力バイト列（任意項目が無ければ何も足さない）。
    assert approval.compute_approved_sha(**args) == approval.compute_approved_sha(**args, media_manifest=None)


# ================================================================== 公開（入れ物の 2 段）

class _Relay:
    def __init__(self, store, *, url_base=None):
        self.store = store
        self.url_base = url_base

    def upload_prepared(self, item):
        data = b"".join(item.chunks())
        assert hashlib.sha256(data).hexdigest() == item.manifest["public_sha256"]
        self.store["upload"].append(data)
        return secrets.token_urlsafe(32)

    def grant(self, item, source):
        base_url = self.url_base or media_relay.origin()
        url = base_url + "/m/" + secrets.token_urlsafe(32)
        value = {"url": url, "expires_at": int(time.time() * 1000) + 600000}
        self.store["grants"].append(value)
        return value

    def result(self, grant, *, published):
        self.store["results"].append(published)
        return {"status": "acknowledged" if published else "retired"}


@pytest.fixture
def wire(tmp_path, monkeypatch):
    state = {"upload": [], "grants": [], "results": [], "status": [], "limit": limit_route(),
             "create": None, "publish": None, "status_id": None, "n": 0}

    def limit(query, form, raw):
        return state["limit"]

    def create(query, form, raw):
        if state["create"]:
            return state["create"]
        state["n"] += 1
        return 200, {"id": str(9000 + state["n"])}

    def publish(query, form, raw):
        return state["publish"] or (200, {"id": "18000000000000001"})

    routes = {("GET", f"{V}/{IG}/content_publishing_limit"): limit,
              ("POST", f"{V}/{IG}/media"): create,
              ("POST", f"{V}/{IG}/media_publish"): publish,
              ("GET", f"{V}/18000000000000001"): (200, {"permalink": "https://www.instagram.com/p/abc/"})}
    for n in range(1, 30):
        cid = str(9000 + n)

        def status(query, form, raw, cid=cid):
            code = state["status"].pop(0) if state["status"] else "FINISHED"
            return 200, {"id": state["status_id"] or cid, "status_code": code}
        routes[("GET", f"{V}/{cid}")] = status
    root = tmp_path.resolve() / "root"
    root.mkdir(mode=0o700)
    repo = root / "repo"
    repo.mkdir()
    (repo / "a.jpg").write_bytes(jpeg())
    (repo / "b.jpg").write_bytes(jpeg())
    (repo / "c.png").write_bytes(png())
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.delenv("THTH_ACCOUNTS_DIR", raising=False)
    cfg = {"account": "alpha", "media": "instagram", "repo_dir": str(repo), "production": True,
           "hashtags": False, "ig_user_id": IG, "page_id": PAGE}
    monkeypatch.setattr(accounts, "load_account", lambda n: cfg)
    monkeypatch.setattr(media_relay, "MediaRelay", lambda account, actor: _Relay(state, url_base=state.get("relay_base")))
    with fake_graph(routes) as (url, calls):
        state["url"], state["calls"], state["cfg"] = url, calls, cfg
        yield state


def _calls(wire, method, suffix):
    return [c for c in wire["calls"] if c["method"] == method and c["path"].endswith(suffix)]


def invoke(wire, fm=None, post=None, a=None):
    cfg = wire["cfg"]
    fm = fm or {"media": [{"file": "a.jpg", "alt": "赤い点"}]}
    manifest = media.manifest_for(fm, cfg)
    state = accounts.state_dir_for("alpha")
    inflight.write(state, file="fixture", started="2030-01-01T00:00:00+09:00")
    a = a or adapter(wire["url"])
    result = media_delivery.publish(a, post or base.Post("キャプション #こぴちゃ"), cfg=cfg, fm=fm,
                                    manifest=manifest, state_dir=state)
    return result, inflight.read(state)


def test_画像1枚_上限を読み_relayのURLで入れ物を作り_FINISHEDを待って公開する(wire):
    result, journal = invoke(wire, {"media": [{"file": "a.jpg", "alt": "赤い点"}],
                                    "post_options": {"is_ai_generated": True}})
    assert result.failure == "none" and result.post_id == "18000000000000001"
    assert result.url == "https://www.instagram.com/p/abc/"
    assert journal["media"]["phase"] == "published"
    order = [(c["method"], c["path"].rsplit("/", 1)[-1]) for c in wire["calls"]]
    assert order[:4] == [("GET", "content_publishing_limit"), ("POST", "media"), ("GET", "9001"),
                         ("POST", "media_publish")]
    create = _calls(wire, "POST", f"/{IG}/media")[0]["form"]
    assert create["image_url"] == [wire["grants"][0]["url"]]
    assert create["image_url"][0].startswith(media_relay.origin() + "/m/")
    assert create["alt_text"] == ["赤い点"] and create["caption"] == ["キャプション #こぴちゃ"]
    assert create["is_ai_generated"] == ["true"] and "is_carousel_item" not in create
    assert _calls(wire, "POST", "/media_publish")[0]["form"]["creation_id"] == ["9001"]
    assert wire["results"] == [True]
    serialized = json.dumps(journal, ensure_ascii=False)
    assert TOKEN not in serialized and all(g["url"] not in serialized for g in wire["grants"])


def test_カルーセルは子に印_親にだけキャプションとAIの印_順番どおり(wire):
    fm = {"media": [{"file": "a.jpg", "alt": "一枚目"}, {"file": "b.jpg", "alt": "二枚目"}],
          "post_options": {"is_ai_generated": True}}
    result, _ = invoke(wire, fm)
    assert result.failure == "none"
    create = [c["form"] for c in _calls(wire, "POST", f"/{IG}/media")]
    assert len(create) == 3
    assert [c["alt_text"][0] for c in create[:2]] == ["一枚目", "二枚目"]
    assert all(c["is_carousel_item"] == ["true"] and "caption" not in c and "is_ai_generated" not in c
               for c in create[:2])
    assert create[2]["media_type"] == ["CAROUSEL"] and create[2]["children"] == ["9001,9002"]
    assert create[2]["caption"] == ["キャプション #こぴちゃ"] and create[2]["is_ai_generated"] == ["true"]
    assert _calls(wire, "POST", "/media_publish")[0]["form"]["creation_id"] == ["9003"]
    assert [m["remote_id"] for m in result.media] == ["9001", "9002"]


def test_状態はIN_PROGRESSからFINISHEDへ_待ってから公開する(wire):
    wire["status"] = ["IN_PROGRESS", "IN_PROGRESS", "FINISHED"]
    result, _ = invoke(wire)
    assert result.failure == "none"
    assert len(_calls(wire, "GET", "/9001")) == 3


@pytest.mark.parametrize("status", ["ERROR", "EXPIRED"])
def test_状態がERROR_EXPIREDなら公開せず入れ物を残してinflightに(wire, status):
    wire["status"] = [status]
    result, journal = invoke(wire)
    assert result.failure == "media_held" and result.error == "media_container_" + status.lower()
    assert not _calls(wire, "POST", "/media_publish")
    assert journal["media"]["phase"] == "held" and journal["media"]["remote_ids"] == ["9001"]
    assert wire["results"] == [False]          # 貸した URL は取り消す


def test_5分でFINISHEDにならなければ諦めて結果不明_公開しない(wire):
    wire["status"] = ["IN_PROGRESS"] * 50
    a = adapter(wire["url"])
    a.poll_total = 0
    result, journal = invoke(wire, a=a)
    assert result.failure == "media_ambiguous" and result.error == "media_processing_timeout"
    assert not _calls(wire, "POST", "/media_publish")
    assert journal["media"]["phase"] == "unknown"
    assert wire["results"] == []               # 分からないときは URL を取り消さない
    assert igm.CREATE_TIMEOUT_SECONDS == 90.0 and ig.InstagramAdapter.poll_total == 300.0


def test_入れ物のidの照合_状態の答えのidが違えば公開しない(wire):
    """柵（わざと壊す対象）: `wait()` の `value.get("id") == container` を外すとこの試験が赤になる。"""
    wire["status_id"] = "9999"
    result, _ = invoke(wire)
    assert result.failure == "media_held" and result.error == "media_container_id_mismatch"
    assert not _calls(wire, "POST", "/media_publish")


def test_relayのURLの照合_relayの外の宛先はMetaに渡さない(wire):
    """柵（わざと壊す対象）: `_grant_url()` の origin の検査を外すとこの試験が赤になる。"""
    wire["relay_base"] = "https://evil.example"
    result, _ = invoke(wire)
    assert result.failure == "publish_definite" and result.error == "media_relay_url_invalid"
    assert not _calls(wire, "POST", f"/{IG}/media")


def test_上限をquota_total50で読み_届いていれば入れ物を作らない(wire):
    wire["limit"] = limit_route(usage=50, total=50)
    result, _ = invoke(wire)
    assert result.failure == "publish_definite"
    assert result.error.startswith("instagram_publish_limit_reached") and "50" in result.error
    assert not _calls(wire, "POST", f"/{IG}/media") and wire["upload"] == []
    # THTH の post.daily は 50 の内側（計画の柵: 安全側の 50 で読んでも内側）。
    assert ig.thth_post_daily() < 50


def test_PNGは媒体に何も投げずに断る(wire):
    result, _ = invoke(wire, {"media": [{"file": "c.png", "alt": "点"}]})
    assert result.failure == "publish_definite" and result.error == "unsupported_attachment: instagram/png"
    assert wire["calls"] == []


def test_公開の要求が5xxなら結果不明_投げ直さない(wire):
    wire["publish"] = (500, {"error": {"message": "x", "code": 2}})
    result, journal = invoke(wire)
    assert result.failure == "media_ambiguous" and len(_calls(wire, "POST", "/media_publish")) == 1
    assert journal["media"]["phase"] == "unknown"


def test_作成が4xxなら出ていない_URLは取り消す(wire):
    wire["create"] = (400, {"error": {"message": "Only photo or video can be accepted", "code": 9004}})
    result, journal = invoke(wire)
    assert result.failure == "publish_definite" and result.error == "media_creating_http_400_code_9004"
    assert wire["results"] == [False]


def test_画像の無い投稿は出さない():
    a = adapter("http://127.0.0.1:9")
    result = a.publish(base.Post("文だけ"), dry_run=False)
    assert result.failure == "publish_definite" and "instagram/no_media" in result.error


def test_返事はコメントのidへPOST_replies():
    with fake_graph({("POST", f"{V}/17900000000000002/replies"): (200, {"id": "17900000000000003"})}) as (url, calls):
        result = adapter(url).publish(base.Post("ありがとう", reply_to="17900000000000002"), dry_run=False)
    assert result.failure == "none" and result.post_id == "17900000000000003"
    assert calls[0]["form"]["message"] == ["ありがとう"] and calls[0]["form"]["access_token"] == [TOKEN]


def test_appsecret_proofが付く():
    secret = "s3cret-for-test"
    with fake_graph({("GET", f"{V}/{IG}/content_publishing_limit"): limit_route()}) as (url, calls):
        adapter(url, app_secret=secret).publishing_limit()
    assert calls[0]["query"]["appsecret_proof"] == [fb.meta_graph.appsecret_proof(TOKEN, secret)]


# ================================================================== 読む

def test_会話はコメントと返事を全階層で辿る():
    routes = {
        ("GET", f"{V}/18000000000000001/comments"): (200, {"data": [
            {"id": "17900000000000001", "text": "かわいい", "timestamp": "2026-10-29T01:00:00+0000",
             "username": "fan", "from": {"id": "555", "username": "fan"}}]}),
        ("GET", f"{V}/17900000000000001/replies"): (200, {"data": [
            {"id": "17900000000000002", "text": "ありがとう", "timestamp": "2026-10-29T02:00:00+0000",
             "username": "kopi_chaba", "from": {"id": IG, "username": "kopi_chaba"},
             "parent_id": "17900000000000001"}]}),
    }
    with fake_graph(routes) as (url, calls):
        rows = adapter(url).conversation("18000000000000001")
    assert [r["message_id"] for r in rows] == ["17900000000000001", "17900000000000002"]
    assert rows[0]["replied_to"] == "18000000000000001" and rows[1]["replied_to"] == "17900000000000001"
    assert all(r["medium"] == "instagram" and r["root_post"] == "18000000000000001" for r in rows)
    assert rows[0]["author_key"] == base.author_key("instagram", "555")


def test_投稿の数は取れなかった指標を0で埋めずunavailableに書く():
    def insights(query, form, raw):
        names = query["metric"][0].split(",")
        if len(names) > 1:
            return 400, {"error": {"message": "metric saved not supported", "code": 100}}
        if names == ["saved"]:
            return 400, {"error": {"message": "metric saved not supported", "code": 100}}
        return 200, {"data": [{"name": names[0], "period": "lifetime", "values": [{"value": 7}]}]}
    routes = {("GET", f"{V}/18000000000000001"): (200, {"like_count": 3, "comments_count": 1}),
              ("GET", f"{V}/18000000000000001/insights"): insights}
    with fake_graph(routes) as (url, _):
        result = adapter(url).insights("18000000000000001")
    metrics, available = base.metrics_of(result)
    assert metrics["likes"] == 3 and metrics["replies"] == 1 and metrics["views"] == 7 and metrics["shares"] == 7
    assert "saved" not in metrics and "saved" in result["unavailable"]
    assert "views" in available


def test_口座の日次はtotal_valueと内訳を読み_取れないものはunavailable():
    def insights(query, form, raw):
        name = query["metric"][0]
        if name == "follower_demographics":
            assert query["period"] == ["lifetime"] and "since" not in query
            return 200, {"data": [{"name": name, "total_value": {"breakdowns": [
                {"dimension_keys": ["country"], "results": [{"dimension_values": ["JP"], "value": 40},
                                                            {"dimension_values": ["TW"], "value": 2}]}]}}]}
        if name == "accounts_engaged":
            return 400, {"error": {"message": "Unsupported metric", "code": 100}}
        assert query["period"] == ["day"] and query["metric_type"] == ["total_value"] and "since" in query
        return 200, {"data": [{"name": name, "period": "day", "total_value": {"value": 11}}]}
    routes = {("GET", f"{V}/{IG}/insights"): insights,
              ("GET", f"{V}/{IG}"): (200, {"followers_count": 123, "media_count": 0})}
    with fake_graph(routes) as (url, _):
        out = adapter(url).account_insights(IG, since="1", until="2")
    assert out["reach"] == 11 and out["views"] == 11 and out["followers_count"] == 123
    assert out["follower_demographics"] == {"JP": 40, "TW": 2}
    assert "accounts_engaged" in out["unavailable"] and "accounts_engaged" not in out
    from thth import measured
    assert set(ig.ACCOUNT_DAILY_METRICS) | {"followers_count"} == set(measured.account_daily_metric_names("instagram"))


def test_quotaは公開の上限を返す(wire):
    with fake_graph({("GET", f"{V}/{IG}/content_publishing_limit"): limit_route(usage=3, total=100)}) as (url, _):
        a = adapter(url)
        a.usage_account = "alpha"
        value = a.quota()
    assert value["medium"] == "instagram" and value["publishing"] == {"usage": 3, "total": 100, "duration": 86400}


def test_probeは読むだけで_上限とpost_dailyを比べる():
    routes = {("GET", f"{V}/{IG}"): (200, {"id": IG, "username": "kopi_chaba"}),
              ("GET", f"{V}/debug_token"): (200, {"data": {"scopes": ig.SCOPES, "expires_at": 0,
                                                            "data_access_expires_at": int(time.time()) + 80 * 86400}}),
              ("GET", f"{V}/{IG}/content_publishing_limit"): limit_route(),
              ("GET", f"{V}/{IG}/media"): (200, {"data": []}),
              ("GET", f"{V}/{IG}/insights"): (200, {"data": []})}
    with fake_graph(routes) as (url, calls):
        rows = {r["key"]: r for r in adapter(url).probe()}
    assert all(c["method"] == "GET" for c in calls)
    assert rows["whoami"]["ok"] is True and rows["instagram_debug_token"]["ok"] is True
    assert rows["publishing_limit"]["ok"] is True and "上限 100" in rows["publishing_limit"]["detail"]
    assert rows["instagram_data_access"]["ok"] is True
    assert rows["retract"]["ok"] is True


# ================================================================== 直す・隠す・消す

def test_隠す_消す_コメント欄の開け閉め():
    routes = {("POST", f"{V}/17900000000000001"): (200, {"success": True}),
              ("DELETE", f"{V}/17900000000000001"): (200, {"success": True}),
              ("POST", f"{V}/18000000000000001"): (200, {"success": True}),
              ("DELETE", f"{V}/18000000000000001"): (200, {"success": True})}
    with fake_graph(routes) as (url, calls):
        a = adapter(url)
        assert a.hide_reply("17900000000000001", hidden=True) == {"hidden": True, "comment_id": "17900000000000001"}
        assert a.delete_reply("17900000000000001")["deleted"] is True
        assert a.set_comments_enabled("18000000000000001", False)["comment_enabled"] is False
        assert a.delete_post("18000000000000001") == {"deleted": True, "post_id": "18000000000000001"}
    assert calls[0]["form"]["hide"] == ["true"]
    assert calls[2]["form"]["comment_enabled"] == ["false"]
    with pytest.raises(base.AdapterError):
        adapter("http://127.0.0.1:9").edit_post("18000000000000001", "x")


# ================================================================== 台帳・認可・期限

def test_account_addの台帳はig_user_idとpage_idを持つ():
    from thth import account_cli
    data = account_cli.build_ledger("kopicha-ig", media="instagram", project="kopicha", handle=IG, page_id=PAGE)
    assert data["ig_user_id"] == IG and data["handle"] == IG and data["page_id"] == PAGE
    assert data["production"] is False and data["scheduled"] is False


def test_from_accountは台帳の口座と鍵の口座が違えば断る():
    cfg = {"account": "alpha", "media": "instagram", "ig_user_id": IG, "page_id": PAGE}
    with pytest.raises(ValueError, match="instagram_account_mismatch"):
        ig.InstagramAdapter.from_account(cfg, {"access_token": TOKEN, "user_id": "1"})
    with pytest.raises(ValueError, match="ig_user_id"):
        ig.InstagramAdapter.from_account({"account": "alpha", "media": "instagram"}, {})


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    home = tmp_path / "home"; home.mkdir()
    ledgers = tmp_path / "accounts"; ledgers.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home),
                       ("THTH_ACCOUNTS_DIR", ledgers)]:
        monkeypatch.setenv(key, str(value))
    d = {k: secrets.token_urlsafe(32) for k in ("app_secret", "code", "short", "long", "page_token")}
    d.update(app_id=str(secrets.randbelow(10**15)), linked=(200, {"instagram_business_account": {
        "id": IG, "username": "kopi_chaba"}, "connected_instagram_account": {"id": IG}}), lines=[], urls=[])
    app = tmp_path / "app.env"
    app.write_text(f"META_APP_ID={d['app_id']}\nMETA_APP_SECRET={d['app_secret']}\n"); app.chmod(0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app))
    cfg = json.loads((Path(__file__).parents[1] / "accounts.example/instagram.json").read_text())
    cfg.update(account="alpha", project="demo", handle=IG, ig_user_id=IG, page_id=PAGE,
               repo_dir=str(root / "repos/_none"), token=str(root / "alpha.token"), env=str(root / "alpha.env"))
    (ledgers / "alpha.json").write_text(json.dumps(cfg))
    d["cfg"] = accounts.load_account("alpha")

    def token_route(query, form, raw):
        if query.get("code") == [d["code"]]:
            return 200, {"access_token": d["short"]}
        if query.get("fb_exchange_token") == [d["short"]]:
            return 200, {"access_token": d["long"]}
        return 400, {"error": {"code": 100}}

    def linked(query, form, raw):
        assert query["access_token"] == [d["page_token"]] and "appsecret_proof" in query
        return d["linked"]
    routes = {("GET", V + "/oauth/access_token"): token_route,
              ("GET", V + "/me/accounts"): (200, {"data": [{"id": PAGE, "name": "こぴちゃ",
                                                             "access_token": d["page_token"], "tasks": ["MANAGE"]}]}),
              ("GET", V + "/debug_token"): (200, {"data": {"app_id": d["app_id"], "is_valid": True,
                                                            "profile_id": PAGE, "expires_at": 0,
                                                            "data_access_expires_at": 1800000000,
                                                            "scopes": list(ig.SCOPES)}}),
              ("GET", V + "/me"): (200, {"id": PAGE, "name": "こぴちゃ"}),
              ("GET", f"{V}/{PAGE}"): linked}
    with fake_graph(routes) as (url, calls):
        monkeypatch.setenv(fb.BASE_URL_ENV, url)
        d["calls"] = calls
        yield d


def _auth(d):
    def entered():
        session = authflow._read_session("alpha")
        return aig.auth_facebook.CALLBACK + "?" + urllib.parse.urlencode({"state": session["state"], "code": d["code"]})
    return oauth.run_auth("alpha", by="operator", input_func=entered, human_output=d["urls"].append,
                          log=d["lines"].append)


def test_認可はページのinstagram_business_accountを照合して口座ごとの鍵を保存する(auth_env):
    assert _auth(auth_env) == 0
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(auth_env["urls"][0]).query)
    assert query["scope"] == [",".join(ig.SCOPES)]
    token = json.loads(Path(auth_env["cfg"]["token"]).read_text())
    assert token["access_token"] == auth_env["page_token"]
    assert token["user_id"] == IG and token["username"] == IG and token["ig_username"] == "kopi_chaba"
    assert token["page_id"] == PAGE and token["data_access_expires_at"] == 1800000000
    assert token["no_expiry"] is True
    raw = Path(auth_env["cfg"]["token"]).read_text()
    assert auth_env["long"] not in raw and auth_env["short"] not in raw


@pytest.mark.parametrize("linked,reason", [
    ((200, {"instagram_business_account": {"id": "17841400000000000", "username": "other"}}),
     "instagram_account_mismatch"),
    ((200, {}), "instagram_account_not_linked"),
])
def test_ページのIGが台帳と違えば保存しない(auth_env, linked, reason):
    auth_env["linked"] = linked
    assert _auth(auth_env) != 0
    assert not Path(auth_env["cfg"]["token"]).exists()
    assert any(reason in line for line in auth_env["lines"])


def test_データの期限はinstagramの口座でもmaintainが14日前に知らせる(tmp_path, monkeypatch):
    root = tmp_path / "root"; root.mkdir()
    ledgers = tmp_path / "accounts"; ledgers.mkdir()
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(ledgers))
    cfg = json.loads((Path(__file__).parents[1] / "accounts.example/instagram.json").read_text())
    cfg.update(account="alpha", ig_user_id=IG, page_id=PAGE, handle=IG, token=str(root / "alpha.token"),
               env=str(root / "alpha.env"), repo_dir=str(root))
    (ledgers / "alpha.json").write_text(json.dumps(cfg))
    from thth import jst
    now = jst.now_jst()
    for days, state in ((10, maintain.DATA_ACCESS_EXPIRING), (-1, maintain.DATA_ACCESS_EXPIRED)):
        Path(cfg["token"]).write_text(json.dumps({"access_token": TOKEN, "user_id": IG, "no_expiry": True,
                                                  "obtained_at": jst.iso(now),
                                                  "data_access_expires_at": int(now.timestamp()) + days * 86400}))
        Path(cfg["token"]).chmod(0o600)
        assert maintain.inspect("alpha", now=now)["state"] == state


def test_lintは文だけとPNGを断り_返事は画像なしで通す(wire):
    cfg = wire["cfg"]
    assert media_delivery.lint_notes(cfg, {}) == ["unsupported_attachment: instagram/no_media"]
    assert media_delivery.lint_notes(cfg, {"reply_to": "17900000000000002"}) == []
    assert media_delivery.lint_notes(cfg, {"media": [{"file": "c.png", "alt": "点"}]}) == [
        "unsupported_attachment: instagram/png"]
    notes = media_delivery.lint_notes(cfg, {"media": [{"file": "a.jpg", "alt": "点"}]})
    assert notes == ["warning: instagram scales image width outside 320〜1440"]
