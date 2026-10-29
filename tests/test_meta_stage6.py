"""Meta 計画 段 6: Webhook の VM 側（`thth/webhook.py`・FB の inbox／mentions・collect・events・購読）。

**本物の Meta も本物の Worker も叩かない。** Worker は試験の中の偽物（`FakeWorker`・`relay.signed_request` の
差し替え）、Graph は loopback の偽物（`fake_graph`）。Worker そのものの試験（署名の照合・重複・1000 件の束）は
`callback/test/webhook.test.mjs`、本物の Worker と本物の VM の Python の通しは `callback/test/webhook-vm.test.mjs`。

柵（わざと壊して赤を見る対象）: ack が届かず同じ行がもう一度来ても 2 行にしない（key で冪等）・知らない id は
捨てて数だけ・止めた口座は購読を断り取り込みも捨てる・購読は欄を外さない・形の違う頁は 1 行も落とさない。
"""
from __future__ import annotations

import datetime
import json
import os
import secrets

import pytest

from thth import accounts, admin_log, adapters, collect, events_cli, jst, leave_gate, relay, replies, webhook
from thth.adapters import base
from thth.adapters import facebook as fb
from tests.test_facebook_adapter import fake_graph

PAGE = "1361492233719922"
IG = "17841437318552220"
OTHER_PAGE = "999000999000"
V = "/" + fb.GRAPH_VERSION
FB_ACCOUNT, IG_ACCOUNT = "kopicha-fb", "kopicha-ig"


class FakeWorker:
    """`/relay/v/webhook/meta/{pull,ack}` の偽物（`callback/src/webhook-object.js` と同じ形の頁）。"""

    def __init__(self):
        self.rows, self.acked, self.calls = [], [], []
        self.fail_ack = 0
        self.stats = {"received": 0, "duplicates": 0, "dropped": 0, "expired": 0, "overflow": 0, "pending": 0,
                      "last_received_at": None}
        self.page_size = 100
        self.broken_page = None

    def add(self, obj, entry_id, field, value, *, at=None):
        key = secrets.token_hex(32)
        received = at or int(datetime.datetime(2026, 10, 30, 1, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000) + len(self.rows) + len(self.acked)
        self.rows.append({"key": key, "object": obj, "entry_id": entry_id, "time": 1793000000, "field": field,
                          "value": json.dumps(value, ensure_ascii=False), "oversize": False, "received_at": received})
        self.stats["received"] += 1
        return key

    def __call__(self, kind, subject, operation, body):
        assert (kind, subject) == ("webhook", "meta")
        self.calls.append((operation, body))
        if operation == "pull":
            if self.broken_page is not None:
                return self.broken_page
            rows = sorted(self.rows, key=lambda r: (r["received_at"], r["key"]))
            if body["after"]:
                rows = [r for r in rows if f"{r['received_at']:013d}:{r['key']}" > body["after"]]
            page = rows[:self.page_size]
            more = len(rows) > self.page_size
            return {"rows": page, "next": f"{page[-1]['received_at']:013d}:{page[-1]['key']}" if more else None,
                    "stats": dict(self.stats, pending=len(self.rows)), "now": 0}
        if operation == "ack":
            if self.fail_ack:
                self.fail_ack -= 1
                raise relay.RelayError("relay_outcome_unknown")
            keys = set(body["keys"])
            self.acked += body["keys"]
            self.rows = [r for r in self.rows if r["key"] not in keys]
            return {"acked": len(keys)}
        raise AssertionError(operation)


@pytest.fixture
def env(isolated_account_factory, tmp_path, monkeypatch):
    app = tmp_path / "app.env"
    app.write_text("META_APP_ID=4242\nMETA_APP_SECRET=" + secrets.token_urlsafe(32) + "\n")
    app.chmod(0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(app))
    tokens = {}
    for name, extra in ((FB_ACCOUNT, {"media": "facebook", "page_id": PAGE, "handle": PAGE, "user_id": PAGE}),
                        (IG_ACCOUNT, {"media": "instagram", "ig_user_id": IG, "page_id": PAGE, "handle": IG,
                                      "user_id": IG})):
        token = tmp_path / f"{name}.token"
        token.write_text(json.dumps({"access_token": "page-token-" + secrets.token_hex(8),
                                     "user_id": extra["user_id"]}))
        token.chmod(0o600)
        tokens[name] = str(token)
        isolated_account_factory(name, token=str(token), **extra)
    worker = FakeWorker()
    monkeypatch.setattr(webhook.relay, "signed_request", worker)
    webhook._last_run[0] = None
    return {"worker": worker, "tokens": tokens, "monkeypatch": monkeypatch}


def comment(n, *, post=f"{PAGE}_9001", parent=None, author="555", verb="add"):
    return {"item": "comment", "verb": verb, "post_id": post, "comment_id": f"9001_{n}",
            "parent_id": parent or post, "from": {"id": author, "name": "訪問者"},
            "message": f"こんにちは {n}", "created_time": 1793000000 + n}


def mention(n):
    return {"item": "post", "verb": "add", "post_id": f"77_{n}", "sender_id": "556",
            "sender_name": "言及した人", "message": "@こぴちゃ", "created_time": 1793000100 + n}


def stop(name):
    """口座を止めた印（`state/_leave/<口座>.json`・`leave_gate.stopped` が見るもの）。"""
    folder = leave_gate.location()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    (folder / f"{name}.json").write_text("{}")


# ================================================================== 写し方（Meta の形 → THTH の 1 行）

def test_feedのコメントは返信_mentionは言及_他人の投稿は訪問者の投稿_それ以外は写さない():
    rec = lambda field, value: {"object": "page", "entry_id": PAGE, "field": field, "value": value}
    kind, row = fb.webhook_message(rec("feed", comment(1)), PAGE)
    assert kind == "reply" and row["message_id"] == "9001_1" and row["root_post"] == f"{PAGE}_9001"
    assert row["replied_to"] == f"{PAGE}_9001" and row["via"] == "webhook" and row["medium"] == "facebook"
    assert row["timestamp"] == "2026-10-26T07:33:21+0000"
    assert row["author_key"] == base.author_key("facebook", "555")
    kind, row = fb.webhook_message(rec("mention", mention(1)), PAGE)
    assert kind == "mention" and row["message_id"] == "77_1" and row["mention_kind"] == "post"
    assert row["username"] == "言及した人"
    visitor = {"item": "status", "verb": "add", "post_id": f"{PAGE}_5", "from": {"id": "557", "name": "客"},
               "message": "質問です", "created_time": 1793000200}
    assert fb.webhook_message(rec("feed", visitor), PAGE)[0] == "visitor_post"
    own = dict(visitor, **{"from": {"id": PAGE, "name": "こぴちゃ"}})
    assert fb.webhook_message(rec("feed", own), PAGE) is None
    assert fb.webhook_message(rec("feed", comment(2, verb="edited")), PAGE) is None
    assert fb.webhook_message(rec("feed", {"item": "reaction", "verb": "add", "post_id": "1_2"}), PAGE) is None
    assert fb.webhook_message({"object": "page", "field": "feed", "value": None}, PAGE) is None


# ================================================================== 取り込み（pull）

def test_取り込みは口座に振り分け_知らないidは捨てて数だけ_全部ackする(env):
    w = env["worker"]
    k1 = w.add("page", PAGE, "feed", comment(1))
    w.add("page", PAGE, "mention", mention(1))
    w.add("instagram", IG, "mentions", {"media_id": "1799", "comment_id": "1800"})
    w.add("page", OTHER_PAGE, "feed", comment(2, post=f"{OTHER_PAGE}_1"))
    result = webhook.pull_once()
    assert result["pulled"] == 4 and result["routed"] == {FB_ACCOUNT: 2, IG_ACCOUNT: 1}
    assert result["unrouted"] == {"page": 1}
    assert w.rows == [] and len(w.acked) == 4
    rows = webhook.rows_for(FB_ACCOUNT)
    assert [r["field"] for r in rows] == ["feed", "mention"] and rows[0]["key"] == k1
    assert rows[0]["value"]["comment_id"] == "9001_1"
    assert [r["field"] for r in webhook.rows_for(IG_ACCOUNT)] == ["mentions"]
    folder = webhook.inbox_dir(FB_ACCOUNT)
    assert all(os.stat(folder / n).st_mode & 0o077 == 0 for n in os.listdir(folder) if n.endswith(".ndjson"))
    assert os.stat(webhook.home()).st_mode & 0o077 == 0
    state = webhook.status()
    assert state["unrouted"] == {"page": 1} and state["routed"] == {FB_ACCOUNT: 2, IG_ACCOUNT: 1}
    assert "down_since" not in state and state["last_ok_at"]
    # 知らない id の中身はどこにも残らない（数だけ）。
    assert OTHER_PAGE not in json.dumps(state) and not os.path.exists(os.path.join(accounts.thth_root(), "state", "_unknown"))


def test_ackが届かず同じ行がもう一度来ても2行にしない_取り込めない状態はstatusに残る(env):
    w = env["worker"]
    w.add("page", PAGE, "feed", comment(1))
    w.fail_ack = 1
    with pytest.raises(webhook.WebhookError, match="webhook_pull_failed"):
        webhook.pull_once()
    assert len(webhook.rows_for(FB_ACCOUNT)) == 1 and len(w.rows) == 1
    assert webhook.status()["down_since"]
    assert webhook.live(FB_ACCOUNT) is False
    result = webhook.pull_once()
    assert result["pulled"] == 1 and w.rows == []
    assert len(webhook.rows_for(FB_ACCOUNT)) == 1, "同じ key を 2 行にした"
    assert "down_since" not in webhook.status()


def test_形の違う頁は1行も落とさず_取り込めない状態にする(env):
    w = env["worker"]
    w.add("page", PAGE, "feed", comment(1))
    good = w("webhook", "meta", "pull", {"after": None})
    for broken in ({**good, "rows": [dict(good["rows"][0], key="x" * 64)]},
                   {**good, "rows": [dict(good["rows"][0], object="user")]},
                   {**good, "next": "../x"}, {"rows": "x", "next": None, "stats": {}}, ["not", "dict"]):
        w.broken_page = broken
        with pytest.raises(webhook.WebhookError):
            webhook.pull_once()
        assert webhook.rows_for(FB_ACCOUNT) == []
    assert not any(op == "ack" for op, _ in w.calls)


def test_頁を辿って全部取り込む(env):
    w = env["worker"]
    w.page_size = 3
    for n in range(10):
        w.add("page", PAGE, "feed", comment(n), at=1793000000000 + n)
    assert webhook.pull_once()["pulled"] == 10
    assert len(webhook.rows_for(FB_ACCOUNT)) == 10 and w.rows == []


def test_止めた口座の通知は捨てて数え_購読も断る_Metaに要求を出さない(env):
    w = env["worker"]
    w.add("page", PAGE, "feed", comment(1))
    stop(FB_ACCOUNT)
    result = webhook.pull_once()
    assert result["stopped"] == 1 and result["routed"] == {} and w.rows == []
    assert webhook.rows_for(FB_ACCOUNT) == []
    with fake_graph({}) as (url, calls):
        env["monkeypatch"].setenv(fb.BASE_URL_ENV, url)
        with pytest.raises(accounts.AccountStopped):
            webhook.subscribe(FB_ACCOUNT, by="masaru")
    assert calls == []


def test_worker_の1巡は購読が無ければWorkerを叩かず_あれば30秒に1回(env, monkeypatch):
    w = env["worker"]
    assert webhook.run_due() is None and w.calls == []
    _subscribe(env, FB_ACCOUNT, existing=["feed", "mention"])
    first = webhook.run_due()
    assert first is not None and w.calls[0][0] == "pull"
    assert webhook.run_due() is None, "30 秒の間を空けずに叩いた"
    webhook._last_run[0] -= webhook.PULL_EVERY_SECONDS
    w.fail_ack = 0
    w.broken_page = ["x"]
    assert webhook.run_due() is None, "失敗を上げた（常駐の他の仕事を止める）"
    assert webhook.status()["down_since"]


# ================================================================== 購読（subscribed_apps）

def _subscribe(env, name, *, existing, app_id="4242", status=200):
    posted = []

    def post(query, form, raw):
        posted.append(form)
        return status, ({"success": True} if status == 200 else {"error": {"message": "no", "code": 200}})
    page_id = PAGE
    routes = {("GET", f"{V}/{page_id}/subscribed_apps"):
              (200, {"data": [{"id": app_id, "name": "THTH", "subscribed_fields": existing},
                              {"id": "1", "name": "別のアプリ", "subscribed_fields": ["ratings"]}]}),
              ("POST", f"{V}/{page_id}/subscribed_apps"): post}
    with fake_graph(routes) as (url, calls):
        env["monkeypatch"].setenv(fb.BASE_URL_ENV, url)
        result = webhook.subscribe(name, by="masaru")
    return result, posted, calls


def test_購読は今の欄を読んでから足す_外さない_別のアプリの欄は混ぜない(env):
    result, posted, calls = _subscribe(env, FB_ACCOUNT, existing=["feed"])
    assert posted == [{"subscribed_fields": ["feed,mention"], **{k: v for k, v in posted[0].items()
                                                                 if k != "subscribed_fields"}}]
    assert result["fields"] == ["feed", "mention"] and result["added"] == ["mention"]
    assert "ratings" not in posted[0]["subscribed_fields"][0]
    assert webhook.subscriptions()[FB_ACCOUNT]["fields"] == ["feed", "mention"]
    rows, _ = admin_log.read(account=FB_ACCOUNT, event="webhook_subscribed")
    assert len(rows) == 1 and rows[0]["diff"]["subscribed_fields"] == [["feed"], ["feed", "mention"]]
    # 足す欄が無ければ POST しない・ある欄（ratings を人が足していた）は外さない。
    result, posted, _ = _subscribe(env, FB_ACCOUNT, existing=["feed", "mention", "ratings"])
    assert posted == [] and result["fields"] == ["feed", "mention", "ratings"]


def test_IGの購読は親のページのsubscribed_appsにfeedを足す(env):
    result, posted, calls = _subscribe(env, IG_ACCOUNT, existing=[])
    assert [c["path"] for c in calls] == [f"{V}/{PAGE}/subscribed_apps"] * 2
    assert posted[0]["subscribed_fields"] == ["feed"]
    sub = webhook.subscriptions()[IG_ACCOUNT]
    assert sub["object"] == "instagram" and sub["entry_id"] == IG and sub["page_id"] == PAGE


def test_購読の権限が無ければ断り_記録しない(env, capsys):
    import argparse
    routes = {("GET", f"{V}/{PAGE}/subscribed_apps"):
              (403, {"error": {"message": "(#200) pages_manage_metadata", "code": 200}})}
    with fake_graph(routes) as (url, calls):
        env["monkeypatch"].setenv(fb.BASE_URL_ENV, url)
        rc = webhook.cmd_subscribe(argparse.Namespace(account=FB_ACCOUNT, by="masaru", json=False))
    assert rc == 2 and "pages_manage_metadata" in capsys.readouterr().err
    assert webhook.subscriptions() == {}


def test_threadsの口座は購読を断る(env, isolated_account_factory):
    isolated_account_factory("kopicha-threads")
    with pytest.raises(webhook.WebhookError, match="webhook_unsupported_medium"):
        webhook.subscribe("kopicha-threads", by="masaru")


# ================================================================== 読む側（inbox・mentions・replies・events）

def _adapter(name=FB_ACCOUNT):
    cfg = accounts.load_account(name)
    os.environ[fb.BASE_URL_ENV] = "http://127.0.0.1:9"
    try:
        return adapters.make_adapter(cfg, accounts.load_token(cfg))
    finally:
        del os.environ[fb.BASE_URL_ENV]


def test_FBのinboxとmentionsはWebhookの記録から_Metaを呼ばない_IGには出さない(env):
    w = env["worker"]
    w.add("page", PAGE, "feed", comment(1))
    w.add("page", PAGE, "mention", mention(1))
    w.add("page", PAGE, "feed", {"item": "status", "verb": "add", "post_id": f"{PAGE}_5",
                                 "from": {"id": "557", "name": "客"}, "message": "質問", "created_time": 1793000200})
    w.add("instagram", IG, "mentions", {"media_id": "1799"})
    webhook.pull_once()
    a = _adapter()
    assert {"inbox", "mentions"} <= a.capabilities()
    assert [m["message_id"] for m in a.mentions()] == ["77_1"]
    assert sorted(m["mention_kind"] for m in a.inbox()) == ["post", "visitor_post"]
    assert [m["message_id"] for m in a.webhook_replies()] == ["9001_1"]
    assert a.mentions(since="2027-01-01T00:00:00+09:00") == []
    ig = _adapter(IG_ACCOUNT)
    assert "inbox" not in ig.capabilities() and ig.webhook_replies() == [] and ig.inbox() == []


def _collect(name, adapter, monkeypatch, *, now):
    calls = []

    def conversation(post_id, **kw):
        calls.append(post_id)
        return []

    def insights(post_id):
        raise base.AdapterError("試験では数を取らない")
    monkeypatch.setattr(adapter, "conversation", conversation, raising=False)
    monkeypatch.setattr(adapter, "insights", insights, raising=False)
    monkeypatch.setattr(adapter, "account_insights", lambda *a, **k: {}, raising=False)
    adapter._thth_account_bound = None
    result = collect.collect_once(name, adapter=adapter, now=now)
    return result, calls


def test_collectは自分の投稿へのコメントを返信の台帳に写し_二度目は増やさず_他人の投稿は写さない(env, monkeypatch):
    from thth import sent as sent_mod
    now = jst.parse("2026-10-30T12:00:00+09:00")
    mine = f"{PAGE}_9001"
    sent_mod.write(accounts.state_dir_for(FB_ACCOUNT), post_id=mine, text="お知らせ", body_hash="0" * 64,
                   sent_at=jst.iso(now - datetime.timedelta(hours=30)))
    w = env["worker"]
    w.add("page", PAGE, "feed", comment(1))
    w.add("page", PAGE, "feed", comment(2, post=f"{PAGE}_8000"))
    w.add("page", PAGE, "mention", mention(1))
    webhook.pull_once()
    for _ in range(2):
        result, _calls = _collect(FB_ACCOUNT, _adapter(), monkeypatch, now=now)
    data = replies.load(FB_ACCOUNT, owned_only=True)
    assert [r["reply_id"] for r in data["replies"]] == ["9001_1"]
    # 他人の投稿（THTH で出していない）へのコメントは台帳のファイルそのものを作らない（owned_only で隠れるだけにしない）。
    everything = replies.load(FB_ACCOUNT)
    assert [r["reply_id"] for r in everything["replies"]] == ["9001_1"]
    assert data["replies"][0]["via"] == "webhook" and data["replies"][0]["post_id"] == mine
    # 取得の記録（kind: fetch）は Webhook の分では書かない。
    assert all(f.get("trigger") != "webhook" for f in data["fetches"])
    inbox = os.path.join(accounts.data_dirs(accounts.load_account(FB_ACCOUNT), FB_ACCOUNT)["inbox"])
    lines = [json.loads(l) for f in os.listdir(inbox) for l in open(os.path.join(inbox, f)) if l.strip()]
    assert [r["message_id"] for r in lines] == ["77_1"] and lines[0]["kind"] == "inbox"
    events = events_cli.build(FB_ACCOUNT, since="30d", now=now)
    kinds = [e["kind"] for e in events["events"]]
    assert "mention_received" in kinds and "reply_received" in kinds


def test_Webhookが届いている口座は巡回の刻みを減らし_届かなくなれば戻す(env, monkeypatch):
    from thth import sent as sent_mod
    now = jst.parse("2026-10-30T12:00:00+09:00")
    post = f"{PAGE}_7000"
    sent_mod.write(accounts.state_dir_for(FB_ACCOUNT), post_id=post, text="お知らせ", body_hash="0" * 64,
                   sent_at=jst.iso(now - datetime.timedelta(hours=7)))
    _subscribe(env, FB_ACCOUNT, existing=["feed", "mention"])
    webhook.pull_once(now=now)
    assert webhook.live(FB_ACCOUNT, now=now)
    _result, calls = _collect(FB_ACCOUNT, _adapter(), monkeypatch, now=now)
    assert calls == [], "Webhook が届いているのに 1・6 時間の刻みで巡回した"
    later = now + datetime.timedelta(hours=2)
    assert webhook.live(FB_ACCOUNT, now=later) is False
    _result, calls = _collect(FB_ACCOUNT, _adapter(), monkeypatch, now=later)
    assert calls == [post], "届かなくなったのに巡回に戻らない"


def test_eventsに取り込めなかった時間と消えた数を出し_今も続けばongoing(env):
    w = env["worker"]
    _subscribe(env, FB_ACCOUNT, existing=["feed", "mention"])
    t0 = jst.parse("2026-10-30T10:00:00+09:00")
    webhook.pull_once(now=t0)
    w.broken_page = ["x"]
    with pytest.raises(webhook.WebhookError):
        webhook.pull_once(now=t0 + datetime.timedelta(minutes=1))
    ongoing = events_cli.build(FB_ACCOUNT, since="1d", now=t0 + datetime.timedelta(minutes=20))
    down = [e for e in ongoing["events"] if e["kind"] == "webhook_down"]
    assert len(down) == 1 and down[0]["ongoing"] is True and down[0]["ref"] == "pull_failed"
    w.broken_page = None
    w.stats["expired"] = 3
    webhook.pull_once(now=t0 + datetime.timedelta(minutes=41))
    payload = events_cli.build(FB_ACCOUNT, since="1d", now=t0 + datetime.timedelta(minutes=45))
    down = [e for e in payload["events"] if e["kind"] == "webhook_down"]
    assert sorted(e["ref"] for e in down) == ["expired", "pull_failed"]
    gap = next(e for e in down if e["ref"] == "pull_failed")
    assert gap.get("ongoing") is None and gap["occurred_at"] == jst.iso(t0 + datetime.timedelta(minutes=1))
    assert "36 時間" in gap["summary"] and "3 件" in next(e for e in down if e["ref"] == "expired")["summary"]
    assert payload["by_kind"]["webhook_down"] == 2
    # 購読していない口座には出ない。
    assert not [e for e in events_cli.build(IG_ACCOUNT, since="1d", now=t0)["events"] if e["kind"] == "webhook_down"]


def test_5分に満たない取り込みの途切れはeventsに出さない(env):
    w = env["worker"]
    _subscribe(env, FB_ACCOUNT, existing=["feed", "mention"])
    t0 = jst.parse("2026-10-30T10:00:00+09:00")
    w.broken_page = ["x"]
    with pytest.raises(webhook.WebhookError):
        webhook.pull_once(now=t0)
    w.broken_page = None
    webhook.pull_once(now=t0 + datetime.timedelta(minutes=2))
    assert webhook.outages() == []


def test_relayの口はwebhookのpullとackだけ():
    for op in ("list", "read", "delete"):
        with pytest.raises(relay.RelayError, match="invalid_relay_operation"):
            relay.signed_request("webhook", "meta", op, {})
    with pytest.raises(relay.RelayError, match="invalid_relay_operation"):
        relay.signed_request("webhook", "other", "pull", {"after": None})


def test_新しい口はleave_gateのbindが包む():
    import inspect
    src = inspect.getsource(leave_gate.bind)
    for name in ("subscribe_webhooks", "subscribed_webhooks", "webhook_replies", "inbox", "mentions"):
        assert f"'{name}'" in src, name


def test_CLIにwebhookのsubscribe_pull_statusがある(env, capsys):
    from thth import cli
    parser = cli.build_parser() if hasattr(cli, "build_parser") else None
    if parser is None:
        pytest.skip("parser を作る口が無い")
    args = parser.parse_args(["webhook", "status", "--json"])
    assert args.func(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["subscriptions"] == {} and out["outages"] == []
