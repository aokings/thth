"""遠くの道とサーバ型 MCP で使えなかった命令・口を通す（点検_絞りすぎ_2026-10-15 §3.3 B-17・B-18）。

- VM の読む口（`server_reads`）: 枝（`thread_read`）・絡みに行く先（`where_to_appear`）・仮名の履歴
  （`who_is_this`）・予定の一覧（`schedule_list`）。CLI の `--json` と同じ形・本文は全文・保存しない。
  `mentions` の `since`・`topics_search` の `recent`。
- 答えが thth.me の 1 件の上限に当たる長い枝・長い一覧は後ろから落として続きの印を付ける。
- 遠くの道の CLI（`thth/remote.py`）: `thread`・`where`・`who`・`mentions --since`・`topics --recent`・
  `schedule <口座>`（一覧）・`send --media`（置いて確定して出す）。
- サーバ型 MCP: `thread_read`・`where_to_appear`・`who_is_this`（`writes: false` の鍵でも出る）。
- 書く口: `send_request` の添付（安全装置と二段の digest はそのまま）。
"""
from __future__ import annotations

import argparse
import json
import types

import pytest

from thth import adapters, api_requests, server_reads
from thth.report_service import ReportServiceError, execute_report
from tests.test_v212_server_writes import env  # noqa: F401
from tests.test_v3140_remote_cli import ACCOUNT, KEY, FakeWorker, _echo, home, run, worker  # noqa: F401
from tests.test_v3140_server_reads import SECRET_URL, FakeAdapter, media  # noqa: F401

ROOT_ID = "17890000000000001"


def call(env, operation, **fields):
    return execute_report(env["context"], dict(operation=operation, account="alpha", **fields))


def refused(env, operation, **fields):
    with pytest.raises(ReportServiceError) as caught:
        call(env, operation, **fields)
    return caught.value


class ThreadAdapter(FakeAdapter):
    """枝と検索の偽物。本文は長め（全文が届くかを見る）。"""
    replies = 3
    text_chars = 300
    fail_conversation = None
    seen = None

    def fetch_post(self, post_id):
        return {"message_id": post_id, "username": "rootuser", "timestamp": "2026-10-15T01:00:00+0000",
                "text": "根の本文" + "あ" * self.text_chars, "root_post": post_id}

    def conversation(self, post_id, since=None, **kwargs):
        if self.fail_conversation is not None:
            raise self.fail_conversation
        ThreadAdapter.seen = {"since": since, **kwargs}
        return [{"id": str(9000 + i), "username": f"user{i}", "timestamp": f"2026-10-15T02:{i % 60:02d}:00+0000",
                 "text": f"返信 {i} " + "い" * self.text_chars, "replied_to": {"id": post_id},
                 "root_post": {"id": post_id}} for i in range(self.replies)]

    def keyword_search(self, q, search_type="TOP", limit=25, **kwargs):
        self._maybe_fail()
        ThreadAdapter.seen = {"search_type": search_type, "limit": limit, **kwargs}
        return [dict(message_id=str(500 + i), username=f"who{i}", timestamp="2026-10-15T03:00:00+0000",
                     text="検索の本文 " + "う" * self.text_chars,
                     permalink=f"https://www.threads.net/@who{i}/post/{i}", has_replies=False)
                for i in range(min(limit, 100))]

    def mentions(self, since=None):
        ThreadAdapter.seen = {"since": since}
        return super().mentions(since=since)


@pytest.fixture
def thread(env, media, monkeypatch):
    fake = ThreadAdapter()
    monkeypatch.setattr(adapters, "make_adapter", lambda *a, **k: fake)
    return fake


def _cli_json(capsys, func, **fields):
    rc = func(argparse.Namespace(json=True, **fields))
    return rc, json.loads(capsys.readouterr().out)


def _runs(env):
    return [json.loads(line) for path in (env["root"] / "state/alpha").glob("runs-*.ndjson")
            for line in path.read_text().splitlines()]


# --------------------------------------------------------------------------
# VM の読む口
# --------------------------------------------------------------------------

def test_thread_readはthth_threadのjsonと同じ形で本文は全文_runsには件数だけ(env, thread, capsys):
    from thth import thread_read
    got = call(env, "thread_read", post_id=ROOT_ID)
    assert got["root"]["text"] == "根の本文" + "あ" * 300
    assert [m["text"] for m in got["messages"]] == ["返信 %d " % i + "い" * 300 for i in range(3)]
    rc, printed = _cli_json(capsys, thread_read.cmd_thread, account="alpha", post_id=ROOT_ID, since=None,
                            max_messages=thread_read.DEFAULT_MAX_MESSAGES, limit=None)
    assert rc == 0
    for key in ("root", "messages", "counts", "you_and_them"):
        assert printed[key] == got[key], key
    rows = [row for row in _runs(env) if row.get("action") == "thread_read"]
    assert len(rows) == 2 and rows[0]["messages"] == 3 and rows[0]["status"] == "ok"
    assert "あ" * 50 not in json.dumps(rows, ensure_ascii=False) and "user0" not in json.dumps(rows)
    call(env, "thread_read", post_id=ROOT_ID, since="2026-10-15T02:00:00+0000", max_messages=2)
    assert ThreadAdapter.seen["since"] == "2026-10-15T02:00:00+0000"


def test_thread_readの問いの断りはinvalid_requestと理由_範囲外はscope_unavailable(env, thread):
    exc = refused(env, "thread_read", post_id=ROOT_ID, max_messages=5000)
    assert str(exc) == "invalid_request" and "max_messages" in exc.reason
    assert str(refused(env, "thread_read", post_id="  ")) == "invalid_request"
    assert str(refused(env, "thread_read", post_id=ROOT_ID, max_messages="5")) == "invalid_request"
    with pytest.raises(ReportServiceError) as caught:
        execute_report(env["context"], dict(operation="thread_read", account="beta", post_id=ROOT_ID))
    assert str(caught.value) == "scope_unavailable"


def test_thread_readの媒体の失敗は短い理由で秘密を含めない(env, thread):
    from thth.adapters import base
    thread.fail_conversation = base.AdapterError("HTTP 500 " + SECRET_URL)
    exc = refused(env, "thread_read", post_id=ROOT_ID)
    assert str(exc) == "upstream_unavailable" and "SECRETSECRET" not in exc.reason
    assert api_requests._failure(exc)["reason"] == exc.reason
    thread.fail_conversation = base.PermissionMissing("threads_read_replies", "not granted")
    assert str(refused(env, "thread_read", post_id=ROOT_ID)) == "permission_unavailable"


def test_長い枝は上限の内に後ろから落とし続きのsinceを付ける(env, thread):
    thread.replies, thread.text_chars = 400, 480
    got = call(env, "thread_read", post_id=ROOT_ID, max_messages=1000)
    assert api_requests._size(got) <= server_reads.RESULT_BUDGET < api_requests.RESULT_MAX
    kept = got["messages"]
    assert 0 < len(kept) < 400 and got["counts"]["truncated"] is True
    assert got["counts"]["messages"] == len(kept)
    assert got["provenance"]["size_trimmed"] == 400 - len(kept)
    assert got["provenance"]["continue_from"] == kept[-1]["timestamp"]
    # 短い枝は落とさない。
    thread.replies, thread.text_chars = 3, 10
    small = call(env, "thread_read", post_id=ROOT_ID)
    assert small["counts"]["truncated"] is False and "size_trimmed" not in small["provenance"]


def test_where_to_appearはthth_whereのjsonと同じ形で本文は全文(env, thread, capsys):
    from thth import where_cli
    got = call(env, "where_to_appear", words=["お茶"], limit=5)
    posts = got["by_account"]["alpha"]["by_word"]["お茶"]["posts"]
    assert len(posts) == 5 and posts[0]["text"] == "検索の本文 " + "う" * 300
    result = where_cli.answer(account_name="alpha", words=["お茶"], limit=5)
    assert result["by_account"]["alpha"]["by_word"]["お茶"]["posts"] == posts
    got = call(env, "where_to_appear", words=["お茶"], recent=True, aggregate=True,
               lexicon={"問い": ["どこ"]})
    assert got["mode"] == "aggregate" and ThreadAdapter.seen["search_type"] == "RECENT"
    for bad in ({"words": []}, {"words": ["a"] * 6}, {"words": "お茶"}, {"words": ["お茶"], "lexicon": {"x": []}},
                {"words": ["お茶"], "project": "alpha"}):
        assert str(refused(env, "where_to_appear", **bad)) == "invalid_request", bad


def test_where_to_appearの言えない理由からURLと秘密を落とす(env, thread):
    from thth.adapters import base
    thread.fail = base.AdapterError("HTTP 400 " + SECRET_URL)
    got = call(env, "where_to_appear", words=["お茶"])
    text = json.dumps(got, ensure_ascii=False)
    assert "SECRETSECRET" not in text and "graph.threads.net" not in text
    assert any("HTTP 400" in line for line in got["by_account"]["alpha"]["cannot_say"])


def test_長い検索の一覧は上限の内に後ろから落として言う(env, thread):
    thread.text_chars = 480
    got = call(env, "where_to_appear", words=["お茶", "紅茶"], limit=100)
    assert api_requests._size(got) <= server_reads.RESULT_BUDGET
    node = got["by_account"]["alpha"]
    trimmed = {word: entry.get("trimmed") for word, entry in node["by_word"].items()}
    assert all(trimmed.values()), trimmed
    assert sum(1 for line in node["cannot_say"] if "128 KB" in line) == 2


def test_who_is_thisはthth_whoのjsonと同じ形(env, thread):
    from thth import who_cli
    got = call(env, "who_is_this", username="someone")
    assert got == who_cli.answer(account_name="alpha", username="someone")
    assert got["met"] == 0
    assert str(refused(env, "who_is_this")) == "invalid_request"
    assert str(refused(env, "who_is_this", author_key="zz")) == "invalid_request"


def test_schedule_listは_thth_schedule_のjsonと同じ行(env, media, monkeypatch):
    from thth import report
    rows = [{"publish_at": "2030-01-01T12:00:00+09:00", "status": "draft", "account": "alpha", "file": "a.md",
             "topic": None, "head": "書き出し", "past": False}]
    seen = {}

    def schedule(account, *, days=None, now=None):
        seen.update(account=account, days=days)
        return rows
    monkeypatch.setattr(report, "schedule", schedule)
    assert call(env, "schedule_list", days=7) == {"account": "alpha", "schedule": rows}
    assert seen == {"account": "alpha", "days": 7}
    assert str(refused(env, "schedule_list", days=0)) == "invalid_options"


def test_mentionsのsinceとtopics_searchのrecentが媒体まで届く(env, thread):
    call(env, "mentions", since="2026-10-01")
    assert ThreadAdapter.seen == {"since": "2026-10-01"}
    got = call(env, "topics_search", query="お茶", recent=True, limit=3)
    assert ThreadAdapter.seen["search_type"] == "RECENT" and got["posts"][0]["text"].startswith("検索の本文")
    assert str(refused(env, "topics_search", query="お茶", recent="yes")) == "invalid_request"


# --------------------------------------------------------------------------
# 書く口: send_request の添付
# --------------------------------------------------------------------------

@pytest.fixture
def fake_send(monkeypatch):
    from thth import core, media_uploads
    calls = []

    def send_once(account, **kwargs):
        calls.append(kwargs)
        if kwargs.get("production_flag"):
            return types.SimpleNamespace(action="published", exit_code=0, mode="production", post_id="42",
                                         url="https://www.threads.net/@demo/post/42", digest="d1")
        return types.SimpleNamespace(action="rehearsal", exit_code=0, mode="rehearsal", post_id=None,
                                     url=None, digest="d1")
    monkeypatch.setattr(core, "send_once", send_once)
    monkeypatch.setattr(media_uploads, "draft_rows",
                        lambda account, actor, rows: [{"file": "docs/sns/media/" + r["media_id"] + ".png",
                                                       "alt": r["alt"]} for r in rows])
    return calls


def test_send_requestは添付をrehearsalと本番の両方に渡す_本文は空でもよい(env, fake_send):
    from thth import server_writes
    got = server_writes.execute(env["context"], dict(operation="send_request", account="alpha", body="",
                                                     media=[{"media_id": "m1", "alt": "猫の写真"}]))
    assert got["status"] == "published" and got["post_id"] == "42"
    rows = [{"file": "docs/sns/media/m1.png", "alt": "猫の写真"}]
    assert [c.get("media_rows") for c in fake_send] == [rows, rows]
    assert fake_send[1]["confirm"] == "d1"
    # 添付が無ければ本文の空は従来どおり断る。
    with pytest.raises(ReportServiceError) as caught:
        server_writes.execute(env["context"], dict(operation="send_request", account="alpha", body="  "))
    assert str(caught.value) == "invalid_request"


def test_止まった口座は添付つきでも出さない(env, fake_send):
    from thth import guard, server_writes
    guard.stop("alpha", "burst")
    with pytest.raises(ReportServiceError) as caught:
        server_writes.execute(env["context"], dict(operation="send_request", account="alpha", body="x",
                                                   media=[{"media_id": "m1", "alt": "a"}]))
    assert str(caught.value) == "account_stopped" and fake_send == []


# --------------------------------------------------------------------------
# サーバ型 MCP
# --------------------------------------------------------------------------

def _mcp(env, monkeypatch):
    from tests.test_mcp import _load_server_module
    server = _load_server_module()
    monkeypatch.setenv("THTH_REPORT_CREDENTIALS", str(env["path"]))
    monkeypatch.setenv("THTH_REPORT_TOKEN", env["bearer"])
    return server


@pytest.mark.parametrize("writes", [True, False])
def test_サーバ型mcpに枝とwhereとwhoが出る_projectは出さない(env, monkeypatch, writes):
    if not writes:
        env["value"]["credentials"][0]["writes"] = False
        env["value"]["credentials"][0].pop("actor")
        env["path"].write_text(json.dumps(env["value"]))
    server = _mcp(env, monkeypatch)
    tools = {t["name"]: t for t in server._handle_request({"id": 1, "method": "tools/list"})["result"]["tools"]}
    for name in ("thread_read", "where_to_appear", "who_is_this"):
        assert name in tools, name
        assert "project" not in tools[name]["inputSchema"]["properties"]
        assert tools[name]["inputSchema"]["required"][0] == "account"
    assert tools["thth_mentions"]["inputSchema"]["properties"]["since"]["type"] == "string"
    assert tools["thth_topics_search"]["inputSchema"]["properties"]["recent"]["type"] == "boolean"
    assert ("media" in tools.get("thth_send_request", {"inputSchema": {"properties": {"media": 1}}})
            ["inputSchema"]["properties"])


def test_サーバ型mcpから枝とwhereとwhoを呼べる(env, thread, monkeypatch):
    server = _mcp(env, monkeypatch)
    got = server.call_tool("thread_read", {"account": "alpha", "post_id": ROOT_ID})
    assert not got.get("isError"), got
    assert json.loads(got["content"][0]["text"])["root"]["text"].startswith("根の本文")
    got = server.call_tool("where_to_appear", {"account": "alpha", "words": ["お茶"], "limit": 2})
    assert len(json.loads(got["content"][0]["text"])["by_account"]["alpha"]["by_word"]["お茶"]["posts"]) == 2
    got = server.call_tool("who_is_this", {"account": "alpha", "username": "someone"})
    assert json.loads(got["content"][0]["text"])["met"] == 0
    bad = server.call_tool("thread_read", {"account": "alpha", "post_id": ROOT_ID, "max_messages": 5000})
    assert bad["isError"] and bad["content"][0]["text"].startswith("invalid_request: ")
    assert server.call_tool("where_to_appear", {"account": "alpha", "project": "alpha",
                                                "words": ["x"]})["content"][0]["text"] == "invalid_request"
    assert server.call_tool("thread_read", {"account": "beta", "post_id": ROOT_ID})["content"][0]["text"] \
        == "scope_unavailable"


# --------------------------------------------------------------------------
# 遠くの道の CLI
# --------------------------------------------------------------------------

@pytest.mark.parametrize("argv, operation, body", [
    (["thread", ACCOUNT, ROOT_ID], "thread_read", {"post_id": ROOT_ID}),
    (["thread", ACCOUNT, ROOT_ID, "--since", "2026-10-15T00:00:00+0000", "--max-messages", "50", "--limit", "20"],
     "thread_read", {"post_id": ROOT_ID, "since": "2026-10-15T00:00:00+0000", "max_messages": 50, "limit": 20}),
    (["where", ACCOUNT, "お茶", "紅茶", "--recent", "--limit", "5", "--also", "どこ", "--ja"], "where_to_appear",
     {"words": ["お茶", "紅茶"], "recent": True, "limit": 5, "also": ["どこ"], "ja": True}),
    (["who", ACCOUNT, "@someone", "--profile"], "who_is_this", {"username": "someone", "profile": True}),
    (["who", ACCOUNT, "0123456789abcdef"], "who_is_this", {"author_key": "0123456789abcdef"}),
    (["mentions", ACCOUNT, "--since", "2026-10-01"], "mentions", {"since": "2026-10-01"}),
    (["topics", ACCOUNT, "--search", "お茶", "--recent"], "topics_search",
     {"query": "お茶", "recent": True, "limit": 25}),
])
def test_遠くの道で使えるようになった命令は_operation_と引数で届く(worker, capsys, argv, operation, body):
    rc, out, err = run(capsys, *argv, "--json")
    assert rc == 0, err
    call_ = worker.calls[-1]
    assert call_["path"] == "/api/v1/" + operation
    assert call_["body"] == {"account": ACCOUNT, **body}
    assert json.loads(out) == {"operation": operation, "received": call_["body"]}


def test_threadは手元と同じ画面で長い枝は続きの命令を添える(worker, capsys):
    message = {"message_id": "9001", "post_id": "9001", "replied_to": ROOT_ID, "depth": 1, "author_key": "k" * 16,
               "username": "user1", "author_followers": None, "author_posts": None, "is_own": False,
               "timestamp": "2026-10-15T02:01:00+0000", "text": "返信の本文", "already_replied": False}
    value = {"root": {"post_id": ROOT_ID, "author_key": "r" * 16, "is_own": False, "timestamp": "t",
                      "text": "根の本文", "username": "rootuser", "author_followers": None, "author_posts": None},
             "messages": [message], "counts": {"messages": 1, "participants": 2, "own": 0, "truncated": True},
             "you_and_them": [], "provenance": {"continue_from": "2026-10-15T02:01:00+0000"}}
    worker.push(200, value)
    rc, out, err = run(capsys, "thread", ACCOUNT, ROOT_ID)
    assert rc == 0, err
    assert "根の本文" in out and "返信の本文" in out and "id=9001" in out
    assert f"続き: thth thread {ACCOUNT} {ROOT_ID} --since 2026-10-15T02:01:00+0000" in out


def test_whereとwhoの人向けの表示は手元の関数(worker, capsys):
    worker.push(200, {"account": ACCOUNT, "project": None, "words": ["お茶"], "by_tag": [], "cannot_say": [],
                      "by_account": {ACCOUNT: {"medium": "threads", "by_tag": [], "you_and_them": {},
                                               "cannot_say": [], "by_word": {"お茶": {
                                                   "posts": [{"replied": None, "timestamp": "t", "author": "who0",
                                                              "author_key": "k" * 16, "text": "お茶の全文です",
                                                              "permalink": None, "post_id": "500"}],
                                                   "material": {"n": 1, "authors": {"distinct": 1},
                                                                "latest_timestamp": "t"},
                                                   "my_history": None, "dropped": {}}}}},
                      "provenance": {"notes": []}})
    rc, out, err = run(capsys, "where", ACCOUNT, "お茶")
    assert rc == 0, err
    assert "お茶の全文です" in out and "post_id: 500" in out
    worker.push(200, {"author_key": "k" * 16, "met": 0, "first": None, "last": None, "threads": [],
                      "profile": None, "cannot_say": [], "provenance": {}})
    rc, out, err = run(capsys, "who", ACCOUNT, "@someone")
    assert rc == 0 and "接触 0 回" in out
    rc, out, err = run(capsys, "who", ACCOUNT, "@a", "@b")
    assert rc == 2


def test_schedule_の一覧は遠くの道で_schedule_list(worker, capsys):
    rows = [{"publish_at": "2030-01-01T12:00:00+09:00", "status": "approved", "account": ACCOUNT, "file": "a.md",
             "topic": "お茶", "head": "書き出し", "past": False}]
    worker.push(200, {"account": ACCOUNT, "schedule": rows})
    rc, out, err = run(capsys, "schedule", ACCOUNT, "--days", "7", "--json")
    assert rc == 0 and json.loads(out) == rows
    assert worker.calls[-1]["path"] == "/api/v1/schedule_list" and worker.calls[-1]["body"]["days"] == 7
    worker.push(200, {"account": ACCOUNT, "schedule": rows})
    rc, out, err = run(capsys, "schedule", ACCOUNT)
    assert rc == 0 and "書き出し" in out and "[お茶]" in out and "承認済み 1" in out


def _media_worker(worker, url=None):
    upload = url or worker.url + "/media-upload/" + "S" * 43

    def answer(call_):
        name = call_["path"].rsplit("/", 1)[-1]
        if call_["method"] == "PUT":
            return 200, {"status": "uploaded"}, None
        if name == "media_upload_url":
            return 200, {"media_id": "m" * 64, "upload_url": upload, "expires_at": 1, "part_size": None}, None
        if name == "media_complete":
            return 200, {"media_id": "m" * 64, "public_sha256": "p" * 64}, None
        return 200, {"account": ACCOUNT, "status": "published", "post_id": "77", "permalink": None}, None
    worker.default = answer


def test_send_の添付は置いて確定して出す_一回限りのURLは画面に出さない(worker, capsys, tmp_path):
    import hashlib
    picture = tmp_path / "cat.png"
    picture.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    _media_worker(worker)
    rc, out, err = run(capsys, "send", ACCOUNT, "--production", "--text", "猫です", "--media", str(picture), "--alt", "猫の写真")
    assert rc == 0, err
    assert [(c["method"], c["path"].split("/")[-1]) for c in worker.calls] == [
        ("POST", "media_upload_url"), ("PUT", "S" * 43), ("POST", "media_complete"), ("POST", "send_request")]
    made = worker.calls[0]["body"]
    assert made == {"account": ACCOUNT, "kind": "image", "mime": "image/png", "size": 108,
                    "sha256": hashlib.sha256(picture.read_bytes()).hexdigest()}
    assert worker.calls[1]["raw"] == picture.read_bytes() and worker.calls[1]["auth"] is None
    assert worker.calls[3]["body"] == {"account": ACCOUNT, "body": "猫です",
                                       "media": [{"media_id": "m" * 64, "alt": "猫の写真"}]}
    assert "S" * 43 not in out + err


def test_send_の添付だけなら本文は空で_置き場が別の所なら中身を送らない(worker, capsys, tmp_path):
    picture = tmp_path / "cat.jpg"
    picture.write_bytes(b"\xff\xd8\xff" + b"x" * 10)
    _media_worker(worker)
    rc, out, err = run(capsys, "send", ACCOUNT, "--production", "--media", str(picture), "--alt", "猫")
    assert rc == 0, err
    assert worker.calls[-1]["body"]["body"] == ""
    worker.calls.clear()
    _media_worker(worker, url="https://elsewhere.example/media-upload/" + "S" * 43)
    rc, out, err = run(capsys, "send", ACCOUNT, "--text", "x", "--media", str(picture), "--alt", "猫")
    assert rc == 2 and err.startswith("remote_unavailable")
    assert [c["method"] for c in worker.calls] == ["POST"]  # PUT は出ていない
    rc, out, err = run(capsys, "send", ACCOUNT, "--text", "x", "--media", str(picture))
    assert rc == 2 and err.startswith("invalid_request")
    rc, out, err = run(capsys, "send", ACCOUNT, "--text", "x", "--media", str(tmp_path / "a.txt"), "--alt", "a")
    assert rc == 2 and err.startswith("media_kind_unsupported")


def test_schedule_の添付は下書きに載せて予約する(worker, capsys, tmp_path):
    picture = tmp_path / "cat.png"
    picture.write_bytes(b"\x89PNG" + b"x" * 10)
    _media_worker(worker)
    answer = worker.default

    def with_draft(call_):
        name = call_["path"].rsplit("/", 1)[-1]
        if name == "draft_put":
            return 200, {"draft_id": "d" * 64, "account": ACCOUNT, "status": "draft", "revision": "r" * 64}, None
        if name == "schedule_request":
            return 200, {"account": ACCOUNT, "draft_id": "d" * 64, "status": "approved",
                         "publish_at": "2030-01-01T09:00:00+09:00"}, None
        return answer(call_)
    worker.default = with_draft
    rc, out, err = run(capsys, "schedule", ACCOUNT, "--text", "朝", "--at", "2030-01-01T09:00+09:00",
                       "--media", str(picture), "--alt", "猫")
    assert rc == 0, err
    put = next(c for c in worker.calls if c["path"].endswith("/draft_put"))
    assert put["body"]["media"] == [{"media_id": "m" * 64, "alt": "猫"}]


def test_schedule_の添付は手元の道では断る(isolated_account, capsys, tmp_path):
    rc, out, err = run(capsys, "schedule", isolated_account["name"], "--media", "a.png", "--alt", "a")
    assert rc == 2 and err.startswith("remote_only")


# --------------------------------------------------------------------------
# 手元の MCP: X の読み取り 3 本と doctor（B-22・B-23）
# --------------------------------------------------------------------------

def _local_server(monkeypatch):
    from tests.test_mcp import _load_server_module
    import subprocess
    server = _load_server_module()
    monkeypatch.delenv("THTH_REPORT_CREDENTIALS", raising=False)
    monkeypatch.delenv("THTH_REPORT_TOKEN", raising=False)
    seen = []

    def fake_run(args, *, stdin_text=None):
        seen.append(args)
        rc = 1 if args[0] == "trends" else 0
        return subprocess.CompletedProcess(args, rc, stdout='{"ok": true}\n', stderr="")
    monkeypatch.setattr(server, "run_cli", fake_run)
    return server, seen


def test_手元のmcpのxの読み取りとdoctorはcliをjsonで呼ぶだけ(monkeypatch):
    server, seen = _local_server(monkeypatch)
    names = {t["name"] for t in server.TOOLS}
    assert {"thth_trends", "thth_counts", "thth_community_search", "thth_doctor"} <= names
    assert not any(name.startswith("thth_list") for name in names)  # list の書き込みは出さない
    got = server.call_tool("thth_trends", {"account": "x1", "woeid": 1118370, "max": 5})
    assert seen[-1] == ["trends", "x1", "--json", "--woeid", "1118370", "--max", "5"] and got["isError"] is True
    got = server.call_tool("thth_counts", {"account": "x1", "word": "お茶 -is:retweet", "granularity": "hour"})
    assert seen[-1] == ["counts", "x1", "お茶 -is:retweet", "--json", "--granularity", "hour"]
    assert got["isError"] is False
    server.call_tool("thth_community_search", {"account": "x1", "words": ["お茶", "紅茶"], "limit": 12})
    assert seen[-1] == ["community", "search", "x1", "お茶", "紅茶", "--json", "--limit", "12"]
    server.call_tool("thth_doctor", {"account": "a1"})
    assert seen[-1] == ["doctor", "a1", "--json"]
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("thth_community_search", {"account": "x1", "words": ["--help"]})
    with pytest.raises(server.ToolInputError):
        server.validate_arguments("thth_counts", {"account": "x1"})


def test_手元のmcpのdoctorはトークンの値を出さない(tmp_path, monkeypatch, isolated_account_factory):
    """媒体の断りが要求の URL（access_token 入り）をそのまま返しても、MCP の答えに値が出ない（B-23）。"""
    import http.server
    import threading
    from tests.test_mcp import _load_server_module

    class Echo(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"error": {"message": "bad request " + self.path, "code": 100}}).encode()
            self.send_response(200)  # HTTP 200 の error（doctor が本文を読んで断る道）
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    token_path = str(tmp_path / "a.token")
    account = isolated_account_factory(token=token_path, handle="nigamilab")
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump({"access_token": "MCP-DOCTOR-SECRET-0123456789", "user_id": "999999", "username": "nigamilab",
                   "scopes": None}, f)
    http_server = http.server.HTTPServer(("127.0.0.1", 0), Echo)
    serving = threading.Thread(target=http_server.serve_forever, daemon=True)
    serving.start()
    try:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", f"http://127.0.0.1:{http_server.server_port}")
        monkeypatch.delenv("THTH_REPORT_CREDENTIALS", raising=False)
        monkeypatch.delenv("THTH_REPORT_TOKEN", raising=False)
        server = _load_server_module()
        got = server.call_tool("thth_doctor", {"account": account["name"]})
    finally:
        http_server.shutdown()
        serving.join(timeout=5)
    text = json.dumps(got, ensure_ascii=False)
    assert "bad request" in text, text  # 媒体の断りは届いている（値だけが伏せてある）
    assert "MCP-DOCTOR-SECRET-0123456789" not in text
