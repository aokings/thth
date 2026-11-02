"""5.7.0: トピックで絞った検索 `thth topics <account> --search <語> --tag [--keep]`（nigamilab の依頼 11/03）。

masaru「ちゃんと下調べして届けたい人に届く投稿をする必要があります」。キーワード検索（`--search`）は
本文に語を含む投稿を返すだけで、その語を**トピックに付けた**投稿かは分からない（topic_advice も数えない）。
`--tag` は Threads の keyword_search に `search_mode=TAG` を渡し、**数と時刻だけ**を返す。

ここで固定するもの:
  - API に `search_mode=TAG` が渡り、`--recent`・`--since`・`--until`・`--media` と組める
  - 件数・投稿者の数（重複なし）・最新と最古の時刻。**本文・username・post_id は画面にも json にも出ない**
  - 0 件（`status: empty`）と読めなかった（`status: cannot_say`・`counts: null`）を分ける
  - `--keep` は数と時刻だけを観測として残す（本文・username・post_id はディスクのどこにも無い）
  - topic_advice は残した観測をトピックの使用例として数え、キーワード検索の結果とは混ぜない
  - 数だけの観測の形は許可リスト（本文の入る鍵があれば保存しない）

本物の Threads API には触れない（`tests/test_threads_read_permissions.py` と同じ偽 API）。
"""
from __future__ import annotations

import contextlib
import datetime
import http.server
import json
import threading
import urllib.parse

import pytest

from tests.conftest import run_thth
from tests.test_b17_remote_reads import ThreadAdapter, call, thread  # noqa: F401  (thread は fixture)
from tests.test_v212_server_writes import env  # noqa: F401  (fixture)
from tests.test_v3140_server_reads import media  # noqa: F401  (fixture)
from tests.test_threads_read_permissions import _scan_for, _write_token
from thth import jst, topic_advice, topic_store
from thth import topic_models as models
from thth import threads_read_cli

TEXT_A = "トピック検索の本文その一・QZ7TAGA"
TEXT_B = "トピック検索の本文その二・QZ7TAGB"
ROWS = [
    {"id": "PIDTAG1", "text": TEXT_A, "username": "alice_tag", "timestamp": "2026-11-01T10:00:00+0000",
     "permalink": "https://t/t1", "topic_tag": "苦味"},
    {"id": "PIDTAG2", "text": TEXT_B, "username": "alice_tag", "timestamp": "2026-11-02T01:00:00+0000",
     "permalink": "https://t/t2"},                                   # 印なし（TAG の答えとして数える）
    {"id": "PIDTAG3", "text": "三つ目", "username": "bob_tag", "timestamp": "2026-10-30T00:00:00+0000",
     "permalink": "https://t/t3", "topic_tag": "#苦味"},
    {"id": "PIDTAG4", "text": "別の語", "username": "carol_tag", "timestamp": "2026-11-02T05:00:00+0000",
     "permalink": "https://t/t4", "topic_tag": "苦味の研究"},         # 別の語（数えない）
]


class _Fake(http.server.BaseHTTPRequestHandler):
    rows: list = []
    mode = "ok"
    requests: list = []

    def _json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.requests.append((parsed.path, params))
        if parsed.path == "/v1.0/keyword_search":
            if self.mode == "5xx":
                return self._json(503, {"error": {"message": "temporarily unavailable", "code": 2}})
            if self.mode == "permission":
                return self._json(400, {"error": {"message": "(#10) Application does not have permission "
                                                             "for this action", "code": 10}})
            return self._json(200, {"data": self.rows[:int(params.get("limit", "25"))]})
        return self._json(200, {"data": []})

    def log_message(self, format, *args):  # noqa: A002
        pass


@contextlib.contextmanager
def _server(rows=ROWS, mode="ok"):
    requests: list = []
    handler = type("H", (_Fake,), {"rows": list(rows), "mode": mode, "requests": requests})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture
def account(tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    acc = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path)
    acc["token_path"] = token_path
    return acc


def _cli(args, base_url):
    return run_thth(args, env={"THTH_THREADS_BASE_URL": base_url, "THTH_ACTOR": "test-tag"})


def _search_params(requests):
    (_p, params), = [r for r in requests if r[0] == "/v1.0/keyword_search"]
    return params


def _no_trace(*roots):
    for root in roots:
        for needle in (TEXT_A, TEXT_B, "alice_tag", "bob_tag", "carol_tag", "PIDTAG1", "https://t/t1"):
            assert _scan_for(root, needle) == [], f"検索の中身が保存されている: {needle} in {root}"


# ======================================================== 数と時刻

def test_tagはsearch_mode_TAGで問い_数と時刻だけを返す(account, thth_root):
    with _server() as (base_url, requests):
        r = _cli(["topics", account["name"], "--search", "#苦味", "--tag", "--json"], base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    params = _search_params(requests)
    assert params["search_mode"] == "TAG" and params["q"] == "苦味" and params["search_type"] == "TOP"
    data = json.loads(r.stdout)
    assert data["status"] == "ok" and data["cannot_say"] is None and data["topic"] == "苦味"
    assert data["counts"] == {"n": 3, "distinct_authors": 2,
                              "latest_at": "2026-11-02T10:00:00+09:00",
                              "oldest_at": "2026-10-30T09:00:00+09:00", "n_with_timestamp": 3,
                              "n_label_matched": 2, "n_label_other": 1, "n_label_absent": 1}
    assert data["n_returned"] == 4 and data["more_may_exist"] is False
    assert data["storage"] == "display_only" and data["observation_id"] is None
    assert "--note 苦味 --status ok" in data["note_hint"]
    # **他人の本文・username・post_id・permalink は出さない**（数と時刻だけ）。
    for needle in (TEXT_A, TEXT_B, "alice_tag", "bob_tag", "PIDTAG1", "https://t/"):
        assert needle not in r.stdout
    assert "posts" not in data
    _no_trace(thth_root, account["repo_dir"])


def test_tagの画面も数と時刻だけ(account):
    with _server() as (base_url, _requests):
        r = _cli(["topics", account["name"], "--search", "苦味", "--tag"], base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "件数            : 3" in out and "投稿者の数      : 2（重複なし）" in out
    assert "最新            : 2026-11-02T10:00:00+09:00　最古: 2026-10-30T09:00:00+09:00" in out
    assert "別の語 1（数に入れていない）" in out
    assert "保存していません。残すなら --keep" in out
    assert TEXT_A not in out and "alice_tag" not in out


def test_tagはrecent_since_until_mediaと組める(account):
    with _server() as (base_url, requests):
        r = _cli(["topics", account["name"], "--search", "苦味", "--tag", "--recent", "--since", "7d",
                  "--until", "1h", "--media", "text", "--limit", "10", "--json"], base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    params = _search_params(requests)
    assert params["search_mode"] == "TAG" and params["search_type"] == "RECENT"
    assert params["media_type"] == "TEXT" and params["limit"] == "10"
    assert int(params["since"]) < int(params["until"])
    data = json.loads(r.stdout)
    assert data["narrow"]["media_type"] == "TEXT" and data["search_type"] == "RECENT"


def test_0件はemptyで_読めなかったはcannot_sayに分ける(account):
    with _server(rows=[]) as (base_url, _requests):
        r = _cli(["topics", account["name"], "--search", "苦味", "--tag", "--json"], base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    empty = json.loads(r.stdout)
    assert empty["status"] == "empty" and empty["counts"]["n"] == 0 and empty["cannot_say"] is None
    with _server(mode="5xx") as (base_url, _requests):
        r = _cli(["topics", account["name"], "--search", "苦味", "--tag", "--json"], base_url)
    assert r.returncode == 1
    failed = json.loads(r.stdout)
    assert failed["status"] == "cannot_say" and failed["counts"] is None
    assert failed["cannot_say"]["kind"] == "upstream" and failed["n_returned"] is None
    with _server(mode="permission") as (base_url, _requests):
        r = _cli(["topics", account["name"], "--search", "苦味", "--tag"], base_url)
    assert r.returncode == 2
    assert "読めませんでした（0 件ではありません）" in r.stdout and "件数" not in r.stdout


def test_別の語の印だけなら0件(account):
    with _server(rows=[ROWS[3]]) as (base_url, _requests):
        data = json.loads(_cli(["topics", account["name"], "--search", "苦味", "--tag", "--json"],
                               base_url).stdout)
    assert data["status"] == "empty" and data["counts"]["n_label_other"] == 1 and data["counts"]["n"] == 0


def test_tagとkeepの組み合わせの誤りは断る(account, tmp_path, isolated_account_factory):
    with _server() as (base_url, requests):
        r1 = _cli(["topics", account["name"], "--search", "苦味", "--keep"], base_url)
        r2 = _cli(["topics", account["name"], "--tag"], base_url)
        assert requests == []
    assert r1.returncode == 2 and "--keep は --tag と一緒に" in r1.stderr
    assert r2.returncode == 2 and "--search と一緒に" in r2.stderr
    other = isolated_account_factory("tag-bluesky", media="bluesky", handle="x.bsky.social",
                                     token=str(tmp_path / "b.token"))
    r3 = run_thth(["topics", other["name"], "--search", "苦味", "--tag"])
    assert r3.returncode == 2 and "--tag は Threads の検索の引数です" in r3.stderr


# ======================================================== --keep と topic_advice

# 件数 6・投稿者 5（`--keep` で値が残る大きさ。5 未満は欠測）。
MANY = [{"id": f"PIDMANY{i}", "text": f"多い方の本文{i}・QZ7MANY", "username": f"many_user{i % 5}",
         "timestamp": f"2026-11-0{1 + i % 2}T0{i}:00:00+0000", "topic_tag": "苦味"} for i in range(6)]


def test_keepは数と日付だけを観測として残す(account, thth_root):
    with _server(rows=MANY) as (base_url, _requests):
        r = _cli(["topics", account["name"], "--search", "苦味", "--tag", "--keep", "--json"], base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["storage"] == "kept" and data["observation_id"].startswith("sha256:")
    assert data["observation_id"] in data["note_hint"]
    saved = topic_store.get("observations", data["observation_id"])
    assert saved["search_mode"] == "topic_tag" and saved["provider"] == "threads_api"
    assert saved["status"] == "ok" and saved["samples"] == [] and saved["account"] == account["name"]
    assert saved["counts"] == {"n": 6, "distinct_authors": 5, "latest_date": "2026-11-02",
                               "oldest_date": "2026-11-01", "n_with_timestamp": 6, "n_label_matched": 6,
                               "n_label_other": None, "n_label_absent": None}   # 0 も 5 未満なので欠測
    assert data["kept_counts"] == saved["counts"]
    assert saved["submitted_by"] == "test-tag"
    assert set(saved) <= models.TAG_COUNTS_OBSERVATION_KEYS | {"schema_version", "observation_id"}
    # 本文・username・post_id は残らない。**正確な時刻も残らない**（日付だけ）。
    for needle in ("QZ7MANY", "many_user", "PIDMANY", "T00:00", "T05:00"):
        assert _scan_for(thth_root, needle) == [], needle
    # 画面の数は正確なまま（表示だけ）。
    assert data["counts"]["latest_at"] == "2026-11-02T14:00:00+09:00"


def test_keepは5未満の数を欠測で残す(account, thth_root):
    with _server() as (base_url, _requests):
        r = _cli(["topics", account["name"], "--search", "苦味", "--tag", "--keep"], base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "残した数: 件数 欠測（5 未満）・投稿者 欠測（5 未満）・最新 —" in r.stdout
    (obs,), _broken, _taken = topic_store.load_all("observations")
    assert obs["counts"] == {k: None for k in models.TAG_COUNT_KEYS}
    _no_trace(thth_root, account["repo_dir"])


def test_keepは読めなかったことも0件と分けて残す(account):
    with _server(mode="5xx") as (base_url, _requests):
        r = _cli(["topics", account["name"], "--search", "苦味", "--tag", "--keep", "--json"], base_url)
    assert r.returncode == 1
    data = json.loads(r.stdout)
    saved = topic_store.get("observations", data["observation_id"])
    assert saved["status"] == "unavailable" and "counts" not in saved
    with _server(rows=[]) as (base_url, _requests):
        data = json.loads(_cli(["topics", account["name"], "--search", "苦味", "--tag", "--keep",
                                "--json"], base_url).stdout)
    saved = topic_store.get("observations", data["observation_id"])
    assert saved["status"] == "empty" and saved["counts"]["n"] == 0


def _tag_obs(n, authors, *, mode="topic_tag", provider="threads_api"):
    now = jst.now_jst()
    return {"topic": "苦味", "normalized_topic": "苦味", "search_mode": mode, "provider": provider,
            "retrieved_at": jst.iso(now - datetime.timedelta(hours=1)), "status": "ok", "samples": [],
            "observation_id": f"sha256:{n}{authors}{mode}{provider}",
            "counts": {"n": n, "distinct_authors": authors, "latest_date": None, "oldest_date": None,
                       "n_with_timestamp": n, "n_label_matched": n, "n_label_other": 0, "n_label_absent": 0}}


def _observation_shortfalls(*observations):
    context = {"profile_version": "p1", "profile_status": "confirmed"}
    chosen = {"topic": "苦味", "article_fit": 3, "conversation_fit": 3,
              "observation_refs": [o["observation_id"] for o in observations]}
    out, _notes = topic_advice._shortfalls(context, chosen, {o["observation_id"]: o for o in observations},
                                           now=jst.now_jst())
    return [text for kind, text in out if kind == "observation"]


def test_topic_adviceは数だけの観測をトピックの使用例として数える():
    assert _observation_shortfalls(_tag_obs(8, 5)) == []
    # 件数・投稿者が 5 未満で欠測なら、0 人とも author_key が無いとも言わず、残していないと言う。
    texts = _observation_shortfalls(_tag_obs(8, None))
    assert any("投稿者の数は 5 未満で残っていない" in t for t in texts) and not any("author_key" in t for t in texts)
    texts = _observation_shortfalls(_tag_obs(None, None))
    assert any("投稿例が 0 件" in t and "5 未満を残さない" in t for t in texts)
    # 数だけの観測どうしの投稿者は足さない（同じ人を二度数えうる）。いちばん多い 1 件。
    assert topic_advice._author_count([_tag_obs(6, 5), _tag_obs(7, 6)]) == 6


def test_topic_adviceはキーワード検索の数をトピックの使用例にしない():
    # 形の上で counts を持っていても、トピックで引いた観測でなければ数えない（読む側でも見る）。
    texts = _observation_shortfalls(_tag_obs(20, 10, mode="keyword"))
    assert any("keyword 検索の結果は tag の利用例になりません" in t for t in texts)
    texts = _observation_shortfalls(_tag_obs(20, 10, provider="browser"))
    assert any("投稿例が 0 件" in t for t in texts)


# ======================================================== 数だけの観測の形（柵）

def _counts_row(**over):
    row = threads_read_cli.tag_observation(
        threads_read_cli.tag_json([], "acct", "苦味", search_type="TOP", limit=25), by="x")
    row = {k: v for k, v in row.items() if k not in ("observation_id", "schema_version")}
    row.update(status="ok", counts=dict(row["counts"], n=6, distinct_authors=5, n_with_timestamp=6,
                                        n_label_matched=None, n_label_other=None, n_label_absent=6,
                                        latest_date="2026-11-02", oldest_date="2026-11-01"))
    row.update(over)
    return row


def test_数だけの観測は許可リストの鍵と5未満なしと日付だけ():
    models.build_observation(_counts_row())
    good = _counts_row()["counts"]
    for bad in ({"text": "本文"}, {"usernames": ["alice"]}, {"samples": [{"author_key": "k", "tagged": True}]},
                {"search_mode": "keyword"}, {"provider": "browser"}, {"status": "partial"},
                {"counts": {"n": 6}},
                {"counts": dict(good, distinct_authors=3)},                         # 5 未満の値
                {"counts": dict(good, latest_date="2026-11-02T10:00:00+09:00")},    # 正確な時刻
                {"counts": dict(good, n=None)},                                     # 件数欠測なのに内訳あり
                {"counts": dict(good, n=0, distinct_authors=0, n_with_timestamp=0, n_label_absent=0,
                                latest_date=None, oldest_date=None)}):              # ok なのに 0 件
        with pytest.raises(models.SchemaError):
            models.build_observation(_counts_row(**bad))


# ======================================================== 遠くの道（VM の読む口）

def test_サーバの読む口もtagとkeepを受けて数と時刻だけを返す(env, thread):
    got = call(env, "topics_search", query="苦味", tag=True, keep=True, limit=6)
    assert ThreadAdapter.seen["search_mode"] == "TAG"
    assert got["status"] == "ok" and got["counts"]["n"] == 6 and got["storage"] == "kept"
    assert got["kept_counts"]["n"] == 6 and got["kept_counts"]["distinct_authors"] == 6
    assert "posts" not in got and "検索の本文" not in json.dumps(got, ensure_ascii=False)
    assert "who0" not in json.dumps(got, ensure_ascii=False)

