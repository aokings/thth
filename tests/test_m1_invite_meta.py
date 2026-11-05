"""計画 Meta 全部盛り申請 M1: 招待の道を Facebook・Instagram・広告に。

見るのは:
  - 3 媒体の招待が、偽の Meta で口座を作り、doctor が通る（既定の招待と `--review-scopes` の招待の両方）。
  - 未承認の権限は旗で閉じる: 既定の招待の scope に足さない（Threads は今のまま）。
  - doctor の突き合わせは招待で求めた一覧。権限が 1 つ欠ければ NOT_GRANTED。
  - 資産が 1 つに決まらなければ保存せず、理由を招待のページへ返す（招待は使い直せる）。
  - 広告の招待は `--production` でも `ads_enabled`・`production`・上限を上げない。
偽の Graph API（loopback）・Worker・預かり所だけを使う。本物の Meta は叩かない。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import socket
import urllib.parse

import pytest

from thth import accounts, activity, admin_log, authflow, cli, doctor, invites
from thth import relay as vm_relay
from thth.adapters import facebook as fb
from thth.ads import cli as ads_cli
from thth.ads import graphread, ledger
from tests.test_facebook_adapter import fake_graph
from tests.test_v3100_invite_create import env as _env  # noqa: F401
from tests.test_v3100_invite_worker import Relay, Worker

REAL_CONNECT = socket.socket.connect
V = "/" + fb.GRAPH_VERSION
PAGE, IG_ID, ACT = "61596075596464", "17841437318552220", "1234567890"
# 計画 §1 の表の写し。`thth/scopes.py` を直しても、ここが一致しなければ赤（口を作る権限の取りこぼしを防ぐ）。
REVIEW = {
    "facebook": {"pages_manage_ads", "pages_manage_cta", "publish_video", "pages_messaging", "pages_utility_messaging"},
    "instagram": {"instagram_manage_engagement", "instagram_manage_messages", "pages_read_user_content",
                  "threads_business_basic", "instagram_manage_upcoming_events"},
    "ads": {"catalog_management", "leads_retrieval", "ads_mcp_management", "pages_manage_ads", "pages_show_list",
            "read_audience_network_insights", "commerce_account_read_settings"},
}


class Meta:
    """偽の Meta。認可の URL で求められた権限だけを、鍵に乗せて返す（`config_id` のときは構成の一覧）。"""

    def __init__(self):
        self.app_id, self.app_secret = str(secrets.randbelow(10**15)), secrets.token_urlsafe(32)
        self.long, self.page_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.pages = [{"id": PAGE, "name": "審査のページ", "access_token": self.page_token, "tasks": ["MANAGE"]}]
        self.linked = {PAGE: {"id": IG_ID, "username": "reviewer_ig"}}
        self.adaccounts = [{"id": "act_" + ACT, "account_id": ACT, "name": "審査の広告", "currency": "JPY",
                            "timezone_name": "Asia/Tokyo", "account_status": 1}]
        self.granted = []

    def routes(self):
        def token(query, form, raw):
            if query.get("code"):
                return 200, {"access_token": "short-" + self.long}
            return 200, {"access_token": self.long, "expires_in": 5183944}

        def debug(query, form, raw):
            return 200, {"data": {"app_id": self.app_id, "is_valid": True, "user_id": "777", "expires_at": 0,
                                  "data_access_expires_at": 1900000000, "scopes": list(self.granted)}}

        def me(query, form, raw):
            return 200, {"id": PAGE, "name": "審査のページ"}

        def page(query, form, raw):
            return 200, {"id": PAGE, "instagram_business_account": self.linked.get(PAGE)}

        def act(query, form, raw):
            if (query.get("fields") or [""])[0] == graphread.ACCOUNT_FIELDS:
                return 200, {"id": "act_" + ACT, "name": "審査の広告", "currency": "JPY", "timezone_name": "Asia/Tokyo",
                             "account_status": 1, "amount_spent": "0", "spend_cap": "0"}
            return 200, {"id": "act_" + ACT}

        empty = lambda q, f, r: (200, {"data": []})  # noqa: E731
        return {("GET", V + "/oauth/access_token"): token, ("GET", V + "/debug_token"): debug,
                ("GET", V + "/me"): me, ("GET", V + "/" + PAGE): page,
                ("GET", V + "/111"): lambda q, f, r: (200, {"id": "111"}),
                ("GET", V + "/me/accounts"): lambda q, f, r: (200, {"data": self.pages}),
                ("GET", V + "/me/adaccounts"): lambda q, f, r: (200, {"data": self.adaccounts}),
                ("GET", V + "/act_" + ACT): act, ("GET", V + f"/{PAGE}/feed"): empty,
                ("GET", V + f"/{PAGE}/published_posts"): empty, ("GET", V + f"/{PAGE}/insights"): empty,
                ("GET", V + f"/act_{ACT}/campaigns"): empty, ("GET", V + f"/act_{ACT}/adsets"): empty,
                ("GET", V + f"/act_{ACT}/ads"): empty}


@pytest.fixture
def world(_env, tmp_path, monkeypatch):
    root, tty, _ = _env
    # 偽の Graph（loopback）だけ通す。ほかの宛先は今までどおり赤。
    monkeypatch.setattr(socket.socket, "connect", lambda self, address: REAL_CONNECT(self, address)
                        if address[0] == "127.0.0.1" else pytest.fail("external network"))
    worker, relay, meta = Worker(), Relay(), Meta()
    monkeypatch.setattr(vm_relay, "signed_request", worker)
    monkeypatch.setattr(authflow, "relay_request", relay)
    monkeypatch.setattr(invites, "OPEN_POLL_SECONDS", 0)
    invites._next_poll.clear()
    app = tmp_path / "app.env"
    app.write_text(f"META_APP_ID={meta.app_id}\nMETA_APP_SECRET={meta.app_secret}\n")
    app.chmod(0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app))
    with fake_graph(meta.routes()) as (url, calls):
        monkeypatch.setenv(fb.BASE_URL_ENV, url)
        yield root, tty, worker, relay, meta, app


def make(tty, **overrides):
    values = dict(media="facebook", project="meta-review", label=None, expires="30d", production=True, by="masaru")
    values.update(overrides)
    row = invites.create(**values)
    return row, hashlib.sha256(tty.codes()[-1].encode()).hexdigest()


def authorize(world, digest):
    """押す → 常駐が認可 URL を作る → 本人が許可する → 常駐が口座を用意する。戻りは URL の引数。"""
    _, _, worker, relay, meta, _ = world
    worker.click(digest)
    invites.run_once()
    url = urllib.parse.urlsplit(worker.invites[digest]["authorize_url"])
    assert (url.scheme, url.netloc, url.path) == ("https", "www.facebook.com", f"/{fb.GRAPH_VERSION}/dialog/oauth")
    params = dict(urllib.parse.parse_qsl(url.query))
    meta.granted = params.get("scope", "").split(",")
    relay.deliver(params["state"])
    invites.run_once()
    return params


def probes(name):
    media = accounts.load_account(name)["media"]
    rows = {row["key"]: row for row in doctor._diagnose(name)["probes"]}
    rows["debug_token"] = rows[media + "_debug_token"]
    return rows


# --- 旗 ---------------------------------------------------------------------

def test_既定の招待のscopeは今のままで_旗のときだけ未承認の権限が足される(world):
    assert "threads_share_to_instagram" not in invites.invite_scopes("threads")
    assert "threads_share_to_instagram" in invites.invite_scopes("threads", True)
    for media, extra in REVIEW.items():
        assert not extra & set(invites.invite_scopes(media)), media
        assert extra <= set(invites.invite_scopes(media, True)), media
        assert len(invites.invite_scopes(media, True)) <= 16, "Worker の上限"


def test_旗はWorkerへの登録と台帳に届き変更ログに残る(world):
    _, tty, worker, _, _, _ = world
    row, digest = make(tty, review_scopes=True)
    assert worker.invites[digest]["scopes"] == invites.invite_scopes("facebook", True)
    authorize(world, digest)
    assert accounts.load_account(row["account"])["review_scopes"] is True
    created, _ = admin_log.read(event="invite_created")
    assert created[-1]["diff"]["review_scopes"] == [None, True]


def test_旗はMastodonには付かず_前向きに断る(world, capsys):
    argv = ["admin", "invite", "create", "--media", "mastodon", "--instance", "https://m.example", "--project", "p",
            "--review-scopes", "--by", "masaru"]
    assert cli.main(argv) == 2
    assert "invite_review_scopes_unsupported: " in capsys.readouterr().err


# --- 3 媒体の通し -----------------------------------------------------------

@pytest.mark.parametrize("review", [False, True])
def test_Facebookの招待が口座を作りdoctorが通る(world, review):
    _, tty, worker, _, _, _ = world
    row, digest = make(tty, review_scopes=review)
    params = authorize(world, digest)
    assert params["redirect_uri"] == "https://thth.me/callback/" and "config_id" not in params
    assert invites.STORE.get(row["invite_id"])["status"] == "used"
    cfg = accounts.load_account(row["account"])
    assert (cfg["media"], cfg["page_id"], cfg["handle"], cfg["production"]) == ("facebook", PAGE, PAGE, True)
    token = accounts.load_token(cfg)
    assert token["access_token"] == world[4].page_token and token["auth_via"] == "relay"
    assert worker.ops("complete")[-1][2]["handle"] == PAGE
    rows = probes(row["account"])
    assert rows["whoami"]["ok"] is True and rows["debug_token"]["ok"] is True, rows["debug_token"]
    assert rows["debug_token"]["missing"] == []


@pytest.mark.parametrize("review", [False, True])
def test_Instagramの招待がページのIGで口座を作りdoctorが通る(world, review):
    _, tty, _, _, meta, _ = world
    meta.pages = [{"id": "111", "name": "IG の無いページ", "access_token": secrets.token_urlsafe(32)}, *meta.pages]
    row, digest = make(tty, media="instagram", review_scopes=review)
    authorize(world, digest)
    cfg = accounts.load_account(row["account"])
    assert (cfg["media"], cfg["ig_user_id"], cfg["page_id"], cfg["handle"]) == ("instagram", IG_ID, PAGE, IG_ID)
    assert accounts.load_token(cfg)["ig_username"] == "reviewer_ig"
    rows = probes(row["account"])
    assert rows["debug_token"]["ok"] is True and rows["debug_token"]["missing"] == []


@pytest.mark.parametrize("review", [False, True])
def test_広告の招待が口座を作りads_doctorが通り_productionでも旗と上限を上げない(world, review):
    root, tty, worker, _, _, _ = world
    row, digest = make(tty, media="ads", production=True, review_scopes=review)
    authorize(world, digest)
    assert invites.STORE.get(row["invite_id"])["status"] == "used"
    cfg = ledger.load(row["account"])
    assert (cfg["act_id"], cfg["currency"], cfg["timezone_name"]) == (ACT, "JPY", "Asia/Tokyo")
    assert cfg["ads_enabled"] is False and cfg["production"] is False
    assert set(cfg["ceilings"].values()) == {0}
    assert not (root / "accounts" / (row["account"] + ".json")).exists(), "口座の台帳には置かない"
    token = ledger.load_token(cfg)
    assert token["user_id"] == "777" and token["act_id"] == ACT and token["auth_via"] == "relay"
    assert worker.ops("complete")[-1][2]["handle"] == ACT
    assert admin_log.read(event="production_enabled")[0] == []
    rows = {r["key"]: r for r in ads_cli.doctor_rows(row["account"])}
    assert rows["debug_token"]["ok"] is True and rows["debug_token"]["missing"] == []


def test_広告の招待は資格情報の行を足さず_常駐の検査を壊さない(world, tmp_path):
    """`accounts/` に無い名前の行が 1 つあると、常駐の毎巡の `validate_environment` が全部を止める。"""
    root, tty, _, relay, _, _ = world
    credentials = credentials_file(root, tmp_path)
    before = credentials.read_bytes()
    row, digest = make(tty, media="ads")
    worker = world[2]
    worker.click(digest)
    invites.run_once(credentials)
    url = urllib.parse.urlsplit(worker.invites[digest]["authorize_url"])
    params = dict(urllib.parse.parse_qsl(url.query))
    world[4].granted = params["scope"].split(",")
    relay.deliver(params["state"])
    invites.run_once(credentials)
    record = invites.STORE.get(row["invite_id"])
    assert record["status"] == "used" and "credential_sha256" not in record
    assert credentials.read_bytes() == before
    with invites.STORE.locked() as directory:
        invites._save(directory, record, remote_ready=True, approver_set=True)
    token = invites._credentials.set(str(credentials))
    try:
        assert invites._active(invites.STORE.get(row["invite_id"])) is False, "見届けが済んだら巡回から外れる"
    finally:
        invites._credentials.reset(token)


def credentials_file(root, tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"schema_version": 1, "root": str(root), "credentials": [{
        "sha256": "b" * 64, "expires_at": "2099-01-01T00:00:00+09:00", "revoked": False, "accounts": {},
        "scope": "admin"}]}))
    path.chmod(0o600)
    return path


def test_広告の口座の要約はWorkerの形で_止める操作は広告の止めの印を書く(world, tmp_path):
    root, tty, _, _, _, _ = world
    row, digest = make(tty, media="ads")
    authorize(world, digest)
    name = row["account"]
    credentials = credentials_file(root, tmp_path)
    assert activity.owners(credentials)[name] == {name: "meta-review"}
    summary = activity.summary(name, name, credentials)
    assert summary["stopped"] is None and summary["rows"] == [] and summary["credential"] is None
    done = activity.apply(name, {"id": "a" * 43, "account": name, "kind": "stop"}, credentials)
    assert done["outcome"] == "done", done
    assert ledger.stopped(name)["reason"] == "owner"
    assert activity.summary(name, name, credentials)["stopped"]["reason"] == "owner"


def test_構成があるときは認可のURLにconfig_idだけを載せる(world):
    _, tty, worker, _, _, app = world
    app.write_text(app.read_text() + "META_LOGIN_CONFIG_ID=4242\n")
    _, digest = make(tty)
    worker.click(digest)
    invites.run_once()
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(worker.invites[digest]["authorize_url"]).query))
    assert params["config_id"] == "4242" and "scope" not in params


# --- 柵: 権限が 1 つ欠けたら NOT_GRANTED ---------------------------------------

@pytest.mark.parametrize("media", ["facebook", "instagram"])
def test_権限が1つ欠けた鍵はdoctorがNOT_GRANTEDと言う(world, media):
    _, tty, _, _, meta, _ = world
    row, digest = make(tty, media=media, review_scopes=True)
    authorize(world, digest)
    meta.granted = [name for name in meta.granted if name != sorted(REVIEW[media])[0]]
    found = probes(row["account"])["debug_token"]
    assert found["ok"] is False and found["missing"] == [sorted(REVIEW[media])[0]]
    assert "NOT_GRANTED" in found["detail"]


def test_権限が1つ欠けた広告の鍵はads_doctorがNOT_GRANTEDと言う(world):
    _, tty, _, _, meta, _ = world
    row, digest = make(tty, media="ads", review_scopes=True)
    authorize(world, digest)
    meta.granted.remove("leads_retrieval")
    found = {r["key"]: r for r in ads_cli.doctor_rows(row["account"])}["debug_token"]
    assert found["missing"] == ["leads_retrieval"] and "NOT_GRANTED: leads_retrieval" in found["detail"]


def test_招待でない口座のdoctorの突き合わせは今のまま(world):
    adapter = fb.FacebookAdapter.from_account({"account": "alpha", "page_id": PAGE}, {})
    assert adapter.expected_scopes == fb.SCOPES


# --- 資産が 1 つに決まらないとき ---------------------------------------------

@pytest.mark.parametrize("media,change,reason", [
    ("facebook", lambda m: setattr(m, "pages", []), "invite_asset_missing"),
    ("facebook", lambda m: m.pages.append({"id": "222", "access_token": "t" * 20}), "invite_asset_ambiguous"),
    ("instagram", lambda m: m.linked.clear(), "invite_asset_missing"),
    ("ads", lambda m: setattr(m, "adaccounts", []), "invite_asset_missing"),
    ("ads", lambda m: m.adaccounts.append({"id": "act_9", "account_id": "9"}), "invite_asset_ambiguous"),
])
def test_資産が決まらなければ保存せず理由をページへ返し_招待は使い直せる(world, media, change, reason, capsys):
    root, tty, worker, _, meta, _ = world
    row, digest = make(tty, media=media)
    change(meta)
    authorize(world, digest)
    record = invites.STORE.get(row["invite_id"])
    assert record["status"] == "open" and record["reason"] == reason
    assert worker.invites[digest]["status"] == "open" and worker.invites[digest]["reason"] == reason
    assert not (root / "secrets" / (row["account"] + ".token")).exists()
    assert cli.main(["admin", "invite", "list"]) == 0
    assert reason + ": " in capsys.readouterr().out


@pytest.mark.parametrize("media", ["facebook", "instagram", "ads"])
def test_同じ資産に2つ目の口座は作らない(world, media):
    _, tty, worker, _, _, _ = world
    _, first = make(tty, media=media)
    authorize(world, first)
    row, second = make(tty, media=media)
    authorize(world, second)
    assert invites.STORE.get(row["invite_id"])["reason"] == "account_exists"


def test_口座を書けたのに記録が追いつかなくても広告の招待は台帳から続ける(world):
    _, tty, worker, relay, _, _ = world
    row, digest = make(tty, media="ads")
    authorize(world, digest)
    with invites.STORE.locked() as directory:
        record = invites.STORE.find(directory, row["invite_id"])
        assert invites._recovered(directory, record) is True
    assert invites.STORE.get(row["invite_id"])["handle"] == ACT


def test_鍵の値は出力と変更ログに出ない(world, capsys):
    root, tty, _, _, meta, _ = world
    _, digest = make(tty)
    authorize(world, digest)
    seen = capsys.readouterr()
    log = (root / "state" / "_admin" / "accounts.ndjson").read_text()
    for value in (meta.page_token, meta.long, meta.app_secret):
        assert value not in seen.out + seen.err + log
