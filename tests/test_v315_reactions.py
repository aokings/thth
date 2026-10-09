"""3.15.0: 再投稿といいね（`thth repost|unrepost|like|unlike`・設計 3.15.0）。

**本物の SNS は叩かない。** loopback の偽サーバが受けた要求の形と回数を数える。確かめること:
- 4 つの媒体の要求の形（Threads・Bluesky・Mastodon・X）と、取り消しが記録の id を使うこと。
- 冪等（同じ反応の 2 回目は SNS に送らない）・記録の無い取り消しは not_found。
- ガード: `daily_max_reactions`・公開の枠と別に数える・夜間は再投稿にだけ。
- production の門・scope_missing・Threads のいいねは unsupported_on_platform・URL の受け取り。
"""
import argparse
import datetime
import http.server
import json
import os
import stat
import threading
import urllib.parse

import pytest

from thth import accounts, account_settings, guard, jst, reaction_cli, reactions, sent as sent_mod
from thth.adapters import auth_mastodon, auth_x, base as adapter_base, threads as threads_mod
from thth import authclients


# --------------------------------------------------------------------------
# 偽サーバ
# --------------------------------------------------------------------------

class Fake:
    def __init__(self):
        self.calls = []
        self.routes = {}

    def on(self, method, path, status=200, body=None):
        self.routes[(method, path)] = (status, body if body is not None else {})


@pytest.fixture
def fake():
    state = Fake()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _handle(self):
            parsed = urllib.parse.urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            state.calls.append({"method": self.command, "path": parsed.path,
                                "query": urllib.parse.parse_qs(parsed.query),
                                "auth": self.headers.get("Authorization"),
                                "body": raw.decode("utf-8")})
            status, body = state.routes.get((self.command, parsed.path), (404, {"error": "no route"}))
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_DELETE = _handle

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.base = "http://127.0.0.1:" + str(server.server_port)
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def sns_calls(fake, *, skip=("com.atproto.server.createSession",)):
    return [c for c in fake.calls if not any(c["path"].endswith(s) for s in skip)]


# --------------------------------------------------------------------------
# 台帳とトークン
# --------------------------------------------------------------------------

def _token(tmp_path, name, data):
    path = tmp_path / f"{name}.token"
    path.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(path, 0o600)
    return str(path)


@pytest.fixture
def make(isolated_account_factory, tmp_path, fake, monkeypatch):
    def _make(media, *, name="alpha", token=None, **overrides):
        if media == "threads":
            monkeypatch.setenv("THTH_THREADS_BASE_URL", fake.base)
            token = token or {"access_token": "THREADS-SECRET-TOKEN", "user_id": "123"}
        elif media == "bluesky":
            overrides.setdefault("service", fake.base)
            token = token or {"identifier": "me.bsky.social", "app_password": "aaaa-bbbb-cccc-dddd"}
            fake.on("POST", "/xrpc/com.atproto.server.createSession",
                    body={"accessJwt": "JWT-ACCESS", "refreshJwt": "JWT-REFRESH",
                          "handle": "me.bsky.social", "did": "did:plc:me"})
        elif media == "mastodon":
            monkeypatch.delenv("THTH_MASTODON_INSTANCE", raising=False)
            overrides.setdefault("instance", fake.base)
            token = token or {"access_token": "MASTO-SECRET", "user_id": "9000",
                              "scopes": ["write:statuses", "write:favourites"],
                              "scopes_source": "response"}
        elif media == "x":
            monkeypatch.setenv("THTH_X_BASE_URL", fake.base)
            token = token or {"access_token": "X-SECRET", "user_id": "777", "username": "me",
                              "scopes": ["tweet.read", "tweet.write", "users.read", "like.write"],
                              "scopes_source": "response"}
        overrides.setdefault("production", True)
        isolated_account_factory(name, media=media, token=_token(tmp_path, name, token), **overrides)
        return accounts.load_account(name)
    return _make


def ns(command, post, **kw):
    base = dict(reaction_command=command, account="alpha", post=post, by="masaru", json=True,
                wait=0, via="cli")
    base.update(kw)
    return argparse.Namespace(**base)


def run(capsys, command, post, **kw):
    rc = reaction_cli.cmd_reaction(ns(command, post, **kw))
    out = capsys.readouterr()
    payload = json.loads(out.out) if kw.get("json", True) and out.out.strip() else None
    return rc, payload, out


def state_rows():
    return reactions.records(accounts.state_dir_for("alpha"))


# --------------------------------------------------------------------------
# Threads
# --------------------------------------------------------------------------

def test_threads_repost_and_undo_use_the_repost_id(make, fake, capsys):
    make("threads")
    fake.on("POST", "/v1.0/18026804600919070/repost", body={"id": "17999000000000001"})
    fake.on("DELETE", "/v1.0/17999000000000001",
            body={"success": True, "deleted_id": "17999000000000001"})
    rc, payload, _ = run(capsys, "repost", "18026804600919070")
    assert rc == 0 and payload["platform_id"] == "17999000000000001"
    assert payload["already_done"] is False and payload["kind"] == "repost"
    call = sns_calls(fake)[0]
    assert call["method"] == "POST" and call["path"] == "/v1.0/18026804600919070/repost"
    assert urllib.parse.parse_qs(call["body"])["access_token"] == ["THREADS-SECRET-TOKEN"]
    assert "THREADS-SECRET-TOKEN" not in json.dumps(payload)

    rc, payload, _ = run(capsys, "unrepost", "18026804600919070")
    assert rc == 0 and payload["undo"] is True and payload["record"]["undone_at"]
    delete = sns_calls(fake)[1]
    # 取り消しは**再投稿の id** に DELETE（元の投稿には触らない）。
    assert delete["method"] == "DELETE" and delete["path"] == "/v1.0/17999000000000001"
    assert len(sns_calls(fake)) == 2


def test_threads_like_is_unsupported_on_platform_without_any_request(make, fake, capsys):
    make("threads")
    for command in ("like", "unlike"):
        rc, payload, _ = run(capsys, command, "18026804600919070")
        assert rc == 2 and payload["error"] == "unsupported_on_platform"
        assert payload["message"] == "Threads の API にいいねがありません"
    assert fake.calls == []
    adapter = threads_mod.ThreadsAdapter(base_url=fake.base, access_token="x", user_id="1")
    with pytest.raises(adapter_base.UnsupportedOnPlatform, match="Threads の API にいいねがありません"):
        adapter.like("1")


def test_threads_repost_permission_error_is_scope_missing(make, fake, capsys):
    make("threads")
    fake.on("POST", "/v1.0/555/repost", status=403,
            body={"error": {"message": "Application does not have permission", "code": 10}})
    rc, payload, _ = run(capsys, "repost", "555")
    assert rc == 2 and payload["error"] == "scope_missing"
    assert payload["permission"] == "threads_content_publish"
    assert state_rows() == []


def test_threads_unrepost_refuses_the_original_post_id():
    adapter = threads_mod.ThreadsAdapter(base_url="http://127.0.0.1:9", access_token="x",
                                         user_id="1", timeout=0.5)
    with pytest.raises(adapter_base.AdapterError):
        adapter.unrepost("555", post_id="555")
    with pytest.raises(adapter_base.AdapterError):
        adapter.unrepost("../me", post_id="1")


def test_threads_url_input_resolves_through_the_reply_ledger(make, fake, capsys):
    cfg = make("threads")
    replies = accounts.data_dirs(cfg, "alpha")["replies"]
    os.makedirs(replies, exist_ok=True)
    with open(os.path.join(replies, "2026-10.ndjson"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"id": "18026804600919070",
                            "permalink": "https://www.threads.com/@kopicha/post/DAbcdEFghij"}) + "\n")
    fake.on("POST", "/v1.0/18026804600919070/repost", body={"id": "17999000000000002"})
    rc, payload, _ = run(capsys, "repost", "https://www.threads.com/@kopicha/post/DAbcdEFghij")
    assert rc == 0 and payload["post_id"] == "18026804600919070"


# --------------------------------------------------------------------------
# Bluesky
# --------------------------------------------------------------------------

URI = "at://did:plc:other/app.bsky.feed.post/3kpost"


@pytest.mark.parametrize("kind,collection", [("repost", "app.bsky.feed.repost"),
                                             ("like", "app.bsky.feed.like")])
def test_bluesky_create_and_delete_record(make, fake, capsys, kind, collection):
    make("bluesky")
    fake.on("GET", "/xrpc/app.bsky.feed.getPosts",
            body={"posts": [{"uri": URI, "cid": "bafycid"}]})
    created = f"at://did:plc:me/{collection}/3kreact"
    fake.on("POST", "/xrpc/com.atproto.repo.createRecord", body={"uri": created, "cid": "c2"})
    fake.on("POST", "/xrpc/com.atproto.repo.deleteRecord", body={})
    rc, payload, _ = run(capsys, kind, "https://bsky.app/profile/did:plc:other/post/3kpost")
    assert rc == 0 and payload["post_id"] == URI and payload["platform_id"] == created
    calls = sns_calls(fake)
    assert [c["path"] for c in calls] == ["/xrpc/app.bsky.feed.getPosts",
                                          "/xrpc/com.atproto.repo.createRecord"]
    body = json.loads(calls[1]["body"])
    assert body["repo"] == "did:plc:me" and body["collection"] == collection
    assert body["record"]["$type"] == collection
    assert body["record"]["subject"] == {"uri": URI, "cid": "bafycid"}
    assert body["record"]["createdAt"].endswith("Z")
    assert calls[1]["auth"] == "Bearer JWT-ACCESS"

    rc, payload, _ = run(capsys, "un" + kind, URI)
    assert rc == 0 and payload["record"]["undone_at"]
    delete = sns_calls(fake)[-1]
    assert delete["path"] == "/xrpc/com.atproto.repo.deleteRecord"
    assert json.loads(delete["body"]) == {"repo": "did:plc:me", "collection": collection,
                                          "rkey": "3kreact"}


def test_bluesky_undo_refuses_a_record_that_is_not_ours(make, fake):
    cfg = make("bluesky")
    from thth.adapters import make_adapter
    adapter = make_adapter(cfg, accounts.load_token(cfg))
    with pytest.raises(adapter_base.AdapterError, match="取り消しません"):
        adapter.unlike("at://did:plc:other/app.bsky.feed.like/3k")
    assert not any(c["path"].endswith("deleteRecord") for c in fake.calls)


# --------------------------------------------------------------------------
# Mastodon
# --------------------------------------------------------------------------

def test_mastodon_reblog_and_unreblog_on_the_original_id(make, fake, capsys):
    make("mastodon")
    fake.on("POST", "/api/v1/statuses/113000000000000001/reblog",
            body={"id": "113000000000000555", "reblog": {"id": "113000000000000001"}})
    fake.on("POST", "/api/v1/statuses/113000000000000001/unreblog",
            body={"id": "113000000000000001", "reblogged": False})
    rc, payload, _ = run(capsys, "repost", "113000000000000001")
    assert rc == 0 and payload["platform_id"] == "113000000000000555"
    assert sns_calls(fake)[0]["auth"] == "Bearer MASTO-SECRET"
    rc, payload, _ = run(capsys, "unrepost", "113000000000000001")
    assert rc == 0
    assert [c["path"] for c in sns_calls(fake)] == [
        "/api/v1/statuses/113000000000000001/reblog",
        "/api/v1/statuses/113000000000000001/unreblog"]


def test_mastodon_favourite_without_scope_is_scope_missing_before_any_request(make, fake, capsys):
    make("mastodon", token={"access_token": "MASTO-SECRET", "user_id": "9000",
                            "scopes": ["read:statuses", "write:statuses", "write:media"],
                            "scopes_source": "response"})
    rc, payload, _ = run(capsys, "like", "113000000000000001")
    assert rc == 2 and payload["error"] == "scope_missing"
    assert payload["permission"] == "write:favourites"
    assert "thth auth alpha --by <名前>" in payload["message"]
    assert fake.calls == []
    # 再投稿は write:statuses で足りる。
    fake.on("POST", "/api/v1/statuses/113000000000000001/reblog",
            body={"id": "113000000000000556", "reblog": {"id": "113000000000000001"}})
    rc, _, _ = run(capsys, "repost", "113000000000000001")
    assert rc == 0


def test_mastodon_favourite_with_umbrella_write_and_same_instance_url(make, fake, capsys):
    make("mastodon", token={"access_token": "MASTO-SECRET", "user_id": "9000",
                            "scopes": ["read", "write"], "scopes_source": "response"})
    fake.on("POST", "/api/v1/statuses/113000000000000001/favourite",
            body={"id": "113000000000000001", "favourited": True})
    rc, payload, _ = run(capsys, "like", fake.base + "/@someone/113000000000000001")
    assert rc == 0 and payload["post_id"] == "113000000000000001"
    assert sns_calls(fake)[0]["path"] == "/api/v1/statuses/113000000000000001/favourite"
    rc, payload, _ = run(capsys, "like", "https://elsewhere.example/@someone/1")
    assert rc == 2 and payload["error"] == "invalid_post"


def test_mastodon_scopes_ask_for_favourites_and_keep_the_previous_client_generation():
    from thth import scopes
    assert "write:favourites" in scopes.MASTODON_SCOPES
    assert "write:favourites" not in auth_mastodon.PREVIOUS_SCOPES
    assert "write:media" in auth_mastodon.PREVIOUS_SCOPES
    assert set(auth_mastodon.LEGACY_SCOPES) == set(auth_mastodon.PREVIOUS_SCOPES) - {"write:media"}
    assert (authclients.scope_generation(auth_mastodon.PREVIOUS_SCOPES)
            != authclients.scope_generation(auth_mastodon.SCOPES))


# --------------------------------------------------------------------------
# X
# --------------------------------------------------------------------------

def test_x_retweet_and_undo_with_cost_estimate(make, fake, capsys):
    make("x")
    fake.on("POST", "/2/users/777/retweets", body={"data": {"retweeted": True}})
    fake.on("DELETE", "/2/users/777/retweets/1800000000000000001", body={"data": {"retweeted": False}})
    rc, payload, _ = run(capsys, "repost", "https://x.com/someone/status/1800000000000000001")
    assert rc == 0 and payload["post_id"] == "1800000000000000001"
    assert payload["x_cost_estimate"]["usd"] == "0.015"
    call = sns_calls(fake)[0]
    assert json.loads(call["body"]) == {"tweet_id": "1800000000000000001"}
    assert call["auth"] == "Bearer X-SECRET"
    rc, payload, _ = run(capsys, "unrepost", "1800000000000000001")
    assert rc == 0 and payload["x_cost_estimate"]["usd"] == "0.010"
    assert sns_calls(fake)[1]["method"] == "DELETE"
    assert sns_calls(fake)[1]["path"] == "/2/users/777/retweets/1800000000000000001"


def test_x_like_needs_like_write(make, fake, capsys):
    make("x", token={"access_token": "X-SECRET", "user_id": "777", "username": "me",
                     "scopes": ["tweet.read", "tweet.write", "users.read", "offline.access"],
                     "scopes_source": "response"})
    rc, payload, _ = run(capsys, "like", "1800000000000000001")
    assert rc == 2 and payload["error"] == "scope_missing" and payload["permission"] == "like.write"
    assert fake.calls == []


def test_x_like_and_unlike_shape(make, fake, capsys):
    make("x")
    fake.on("POST", "/2/users/777/likes", body={"data": {"liked": True}})
    fake.on("DELETE", "/2/users/777/likes/1800000000000000001", body={"data": {"liked": False}})
    rc, _, out = run(capsys, "like", "1800000000000000001", json=False)
    assert rc == 0 and "X の料金の目安: 0.015 USD" in out.out
    assert json.loads(sns_calls(fake)[0]["body"]) == {"tweet_id": "1800000000000000001"}
    rc, _, _ = run(capsys, "unlike", "1800000000000000001")
    assert rc == 0 and sns_calls(fake)[1]["path"] == "/2/users/777/likes/1800000000000000001"


def test_x_like_write_is_requested_but_not_part_of_the_client_generation():
    assert "like.write" not in auth_x.SCOPES and "like.write" in auth_x.OPTIONAL_SCOPES
    profile = auth_x.XAuthProfile("cid", "csecret", auth_x.CALLBACK, list(auth_x.SCOPES))
    url = profile.authorize({"state": "s", "code_verifier": "v" * 43})
    scope = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["scope"][0].split()
    assert scope == list(auth_x.SCOPES) + ["like.write"]


# --------------------------------------------------------------------------
# 冪等・記録
# --------------------------------------------------------------------------

def test_second_repost_returns_the_record_without_calling_the_sns(make, fake, capsys):
    make("threads")
    fake.on("POST", "/v1.0/555/repost", body={"id": "777"})
    rc, first, _ = run(capsys, "repost", "555")
    rc2, second, _ = run(capsys, "repost", "555")
    assert rc == rc2 == 0 and second["already_done"] is True
    assert second["platform_id"] == first["platform_id"] == "777"
    assert len(sns_calls(fake)) == 1 and len(state_rows()) == 1


def test_undo_without_record_is_not_found_and_sends_nothing(make, fake, capsys):
    make("threads")
    rc, payload, _ = run(capsys, "unrepost", "555")
    assert rc == 1 and payload["error"] == "not_found" and fake.calls == []


def test_record_shape_and_private_mode(make, fake, capsys):
    make("threads")
    fake.on("POST", "/v1.0/555/repost", body={"id": "777"})
    run(capsys, "repost", "555", via="mcp")
    [row] = state_rows()
    assert {k: row[k] for k in ("kind", "post_id", "platform_id", "undone_at", "by", "via")} == {
        "kind": "repost", "post_id": "555", "platform_id": "777", "undone_at": None,
        "by": "masaru", "via": "mcp"}
    assert jst.parse(row["at"]) is not None
    assert stat.S_IMODE(os.stat(row["path"]).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(row["path"])).st_mode) == 0o700
    assert "text" not in row


def test_upstream_failure_writes_no_record(make, fake, capsys):
    make("threads")
    fake.on("POST", "/v1.0/555/repost", status=500, body={"error": {"message": "boom"}})
    rc, payload, _ = run(capsys, "repost", "555")
    assert rc == 1 and payload["error"] == "upstream_refused" and state_rows() == []
    assert "THREADS-SECRET-TOKEN" not in payload["message"]


# --------------------------------------------------------------------------
# ガード
# --------------------------------------------------------------------------

def test_daily_max_reactions_counts_reactions_and_undos(make, fake, capsys):
    make("threads", daily_max_reactions=2)
    fake.on("POST", "/v1.0/1/repost", body={"id": "901"})
    fake.on("DELETE", "/v1.0/901", body={"success": True})
    fake.on("POST", "/v1.0/2/repost", body={"id": "902"})
    assert run(capsys, "repost", "1")[0] == 0
    assert run(capsys, "unrepost", "1")[0] == 0
    rc, payload, _ = run(capsys, "repost", "2")
    assert rc == 1 and payload["error"] == "reaction_limit"
    assert payload["next_at"] == "2026-09-10T00:00:00+09:00"
    assert len(sns_calls(fake)) == 2


def test_reactions_do_not_count_toward_post_limits_or_burst(make, fake, capsys):
    cfg = make("threads", daily_max_posts=1, burst={"count": 1, "minutes": 10})
    for n in range(3):
        fake.on("POST", f"/v1.0/{n + 10}/repost", body={"id": str(900 + n)})
        assert run(capsys, "repost", str(n + 10))[0] == 0
    counts = guard.summary("alpha", cfg)
    assert counts["posts_today"] == 0 and counts["recent"] == 0
    guard.check("alpha", cfg, "publish")          # 断らない・止めない
    assert guard.stopped("alpha") is None
    assert guard.reactions_today("alpha") == 3


def test_reaction_limit_ignores_sent_posts(make, fake, capsys):
    make("threads", daily_max_reactions=1)
    sent_mod.write(accounts.state_dir_for("alpha"), post_id="42", text="本文", body_hash="h",
                   sent_at="2026-09-09T09:00:00+09:00")
    fake.on("POST", "/v1.0/1/repost", body={"id": "901"})
    assert run(capsys, "repost", "1")[0] == 0


def test_quiet_hours_refuse_repost_but_not_like(make, fake, capsys):
    make("mastodon", quiet_hours=["09:00", "11:00"])      # いまは 10:00（conftest）
    fake.on("POST", "/api/v1/statuses/1/favourite", body={"id": "1"})
    rc, payload, _ = run(capsys, "repost", "1")
    assert rc == 1 and payload["error"] == "quiet_hours"
    assert payload["next_at"] == "2026-09-09T11:00:00+09:00"
    rc, _, _ = run(capsys, "like", "1")
    assert rc == 0
    assert [c["path"] for c in sns_calls(fake)] == ["/api/v1/statuses/1/favourite"]


def test_stopped_account_refuses_reactions(make, fake, capsys):
    make("threads")
    guard.stop("alpha", "owner")
    rc, payload, _ = run(capsys, "repost", "1")
    assert rc == 1 and payload["error"] == "account_stopped" and fake.calls == []


def test_daily_max_reactions_is_a_setting(make):
    make("threads")
    assert account_settings.current(accounts.load_account("alpha"))["daily_max_reactions"] == 50
    row = account_settings.change("alpha", "daily_max_reactions", "20", by="masaru")
    assert row["before"] == 50 and row["after"] == 20
    assert accounts.guard_limits(accounts.load_account("alpha"))["daily_max_reactions"] == 20
    with pytest.raises(account_settings.SettingsError, match="settings_loosen_requires_owner"):
        account_settings.change("alpha", "daily_max_reactions", "30", by="llm", tighten_only=True)
    assert account_settings.status("alpha")["today"]["reactions"] == 0


# --------------------------------------------------------------------------
# 門
# --------------------------------------------------------------------------

def test_production_false_sends_nothing(make, fake, capsys):
    make("threads", production=False)
    rc, payload, _ = run(capsys, "repost", "1")
    assert rc == 1 and payload["error"] == "production_disabled" and fake.calls == []
    assert state_rows() == []


def test_by_is_required(make, fake, capsys, monkeypatch):
    make("threads")
    monkeypatch.delenv("THTH_ACTOR", raising=False)
    rc, payload, _ = run(capsys, "repost", "1", by=None)
    assert rc == 1 and payload["error"] == "by_required" and fake.calls == []


def test_bad_post_value_is_refused_before_the_network(make, fake, capsys):
    make("x")
    for value in ("https://x.com/someone/likes", "abc", "https://evil.example/a/status/1"):
        rc, payload, _ = run(capsys, "repost", value)
        assert rc == 2 and payload["error"] == "invalid_post"
    assert fake.calls == []


def test_cli_parser_has_the_four_commands():
    from thth import cli
    parser = cli.build_parser()
    for command in ("repost", "unrepost", "like", "unlike"):
        args = parser.parse_args([command, "alpha", "123", "--by", "m", "--json"])
        assert args.func is reaction_cli.cmd_reaction and args.reaction_command == command


# --------------------------------------------------------------------------
# MCP
# --------------------------------------------------------------------------

def test_mcp_reaction_tools_call_the_cli(monkeypatch):
    from tests.test_mcp import _load_server_module
    for key in ("THTH_REPORT_CREDENTIALS", "THTH_REPORT_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    server = _load_server_module()
    seen = []

    class Proc:
        returncode = 0
        stdout = '{"ok": true}'
        stderr = ""

    monkeypatch.setattr(server, "run_cli", lambda args, **k: seen.append(args) or Proc())
    names = {tool["name"] for tool in server.TOOLS}
    assert {"thth_repost", "thth_like"} <= names
    result = server.call_tool("thth_repost", server.validate_arguments(
        "thth_repost", {"account": "alpha", "post": "123", "by": "masaru"}))
    assert not result["isError"]
    server.call_tool("thth_like", {"account": "alpha", "post": "123", "by": "masaru", "undo": True})
    assert seen == [["repost", "alpha", "123", "--by=masaru", "--via=mcp", "--json"],
                    ["unlike", "alpha", "123", "--by=masaru", "--via=mcp", "--json"]]
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("thth_like", {"account": "alpha", "post": "--help", "by": "m"})
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("thth_like", {"account": "alpha", "post": "1"})


# --------------------------------------------------------------------------
# Mastodon の 1 つ前の client 世代（write:favourites が無い）を壊さない
# --------------------------------------------------------------------------

from tests.test_v211_mastodon_auth import env as masto_env  # noqa: E402,F401


def _previous_generation(env):
    import secrets as secrets_mod
    from pathlib import Path
    cfg, base = env['cfg'], env['base']
    path = authclients.path_for('mastodon', base, cfg, required_scopes=auth_mastodon.PREVIOUS_SCOPES)
    metadata = {'issuer': base, **{k: base + v for k, v in auth_mastodon.ENDPOINTS.items()},
                'code_challenge_methods_supported': ['S256'],
                'grant_types_supported': ['authorization_code'], 'response_types_supported': ['code'],
                'scopes_supported': list(auth_mastodon.PREVIOUS_SCOPES),
                'token_endpoint_auth_methods_supported': ['client_secret_post']}
    data = dict(client_id=secrets_mod.token_urlsafe(20), client_secret=secrets_mod.token_urlsafe(30),
                instance=base, redirect_uri=auth_mastodon.CALLBACK,
                scopes=list(auth_mastodon.PREVIOUS_SCOPES), metadata=metadata, created_at=jst.iso())
    authclients.write(path, data)
    token = {'access_token': secrets_mod.token_urlsafe(30), 'scopes': list(auth_mastodon.PREVIOUS_SCOPES),
             'scopes_source': 'response',
             'client_scope_generation': authclients.scope_generation(auth_mastodon.PREVIOUS_SCOPES)}
    tokenpath = Path(cfg['token']); tokenpath.parent.mkdir(parents=True, exist_ok=True)
    tokenpath.write_text(json.dumps(token)); tokenpath.chmod(0o600)
    return data, token


def test_previous_mastodon_generation_still_revokes_and_rehearses(masto_env, monkeypatch):
    from thth import leave
    data, token = _previous_generation(masto_env)
    base, client = auth_mastodon.client_for_token(masto_env['cfg'], token)
    assert client['client_id'] == data['client_id']
    calls = []
    monkeypatch.setattr(auth_mastodon, 'request', lambda b, route, **kw: calls.append(kw) or {})
    assert leave.revoke(masto_env['cfg'], token, [], lambda: None) == 'confirmed'
    assert calls[0]['data']['client_id'] == data['client_id']
    profile = auth_mastodon.MastodonAuthProfile.prepare(masto_env['cfg'], rehearse=True)
    assert profile.scopes == auth_mastodon.PREVIOUS_SCOPES and profile.client_id == data['client_id']
