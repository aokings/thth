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
import os
import threading
import urllib.parse

import pytest

from tests.conftest import run_thth
from tests.test_b17_remote_reads import ThreadAdapter, call, thread  # noqa: F401  (thread は fixture)
from tests.test_v212_server_writes import env  # noqa: F401  (fixture)
from tests.test_v3140_server_reads import media  # noqa: F401  (fixture)
from tests.test_threads_read_permissions import _scan_for, _write_token
from thth import jst, topic_advice, topic_store, where_keep
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
    assert data["storage"] == "display_only" and data["kept"] is None
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


# ======================================================== --keep（project の棚）と topic_advice

# 件数 6・投稿者 5（`--keep` で値が残る大きさ。5 未満は欠測）。
MANY = [{"id": f"PIDMANY{i}", "text": f"多い方の本文{i}・QZ7MANY", "username": f"many_user{i % 5}",
         "timestamp": f"2026-11-0{1 + i % 2}T0{i}:00:00+0000", "topic_tag": "苦味"} for i in range(6)]


def _shelf_rows(project="nigamilab"):
    rows, broken = where_keep.read(project)
    assert broken == 0
    return [row for row in rows if row["kind"] == "topic_tag"]


def test_keepはwhere_keepと同じprojectの棚に数と日付だけを残す(account, thth_root):
    with _server(rows=MANY) as (base_url, _requests):
        r = _cli(["topics", account["name"], "--search", "苦味", "--tag", "--keep", "--json"], base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(r.stdout)
    assert data["storage"] == "kept" and "state/_map/<project>/" in data["kept_place"]
    (row,) = _shelf_rows()
    assert data["kept"] == row
    assert row["kind"] == "topic_tag" and row["word"] == "苦味" and row["medium"] == "threads"
    assert row["status"] == "ok" and row["n"] == 6 and row["distinct_authors"] == 5
    assert row["latest_date"] == "2026-11-02" and row["oldest_date"] == "2026-11-01"
    assert row["n_label_other"] is None and row["n_label_absent"] is None   # 0 も 5 未満なので欠測
    assert row["submitted_by"] == "test-tag"
    # 置き場は project の棚だけ。**観測の棚（project をまたぐ）には何も置かない。**
    assert topic_store.load_all("observations")[0] == []
    assert os.path.isdir(os.path.join(thth_root, "state", "_map", "nigamilab"))
    # 本文・username・post_id・正確な時刻は残らない（日付だけ）。
    for needle in ("QZ7MANY", "many_user", "PIDMANY", "T00:00", "T05:00"):
        assert _scan_for(thth_root, needle) == [], needle
    assert data["counts"]["latest_at"] == "2026-11-02T14:00:00+09:00"   # 画面の数は正確なまま（表示だけ）
    # where-log で読める（project の中だけ）。
    log = run_thth(["where-log", "nigamilab", "--json"])
    assert log.returncode == 0, log.stderr
    assert json.loads(log.stdout)["topic_tag"][0]["days"][0]["n"] == 6


def test_keepは5未満と0件を欠測で残し_読めなかったことも残す(account):
    with _server() as (base_url, _requests):
        r = _cli(["topics", account["name"], "--search", "苦味", "--tag", "--keep"], base_url)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "残した数: 件数 欠測（5 未満）・投稿者 欠測（5 未満）・最新 —" in r.stdout
    (row,) = _shelf_rows()
    assert row["reason"] == "small_sample" and all(row[k] is None for k in where_keep.TOPIC_COUNT_KEYS)
    with _server(rows=[]) as (base_url, _requests):
        _cli(["topics", account["name"], "--search", "苦味", "--tag", "--keep"], base_url)
    (row,) = _shelf_rows()   # 同じ日・同じ語は置き換え
    assert row["status"] == "empty" and row["n"] is None and row["reason"] == "zero_or_filtered"
    with _server(mode="5xx") as (base_url, _requests):
        r = _cli(["topics", account["name"], "--search", "苦味", "--tag", "--keep", "--json"], base_url)
    assert r.returncode == 1
    (row,) = _shelf_rows()
    assert row["status"] == "unavailable" and row["n"] is None and row["reason"] is None


def test_projectの無いaccountはkeepを断る(tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "np.token")
    acc = isolated_account_factory("tag-noproject", token=token_path, handle="nigamilab", project=None)
    _write_token(token_path)
    with _server(rows=MANY) as (base_url, _requests):
        r = _cli(["topics", acc["name"], "--search", "苦味", "--tag", "--keep", "--json"], base_url)
    assert r.returncode == 1
    data = json.loads(r.stdout)
    assert data["storage"] == "display_only" and "project がありません" in data["keep_error"]


def _context(account):
    return {"profile_version": "p1", "profile_status": "confirmed", "account": account}


def _shortfalls(account, *observations):
    chosen = {"topic": "苦味", "article_fit": 3, "conversation_fit": 3,
              "observation_refs": [o["observation_id"] for o in observations]}
    out, notes = topic_advice._shortfalls(_context(account), chosen,
                                          {o["observation_id"]: o for o in observations}, now=jst.now_jst())
    return [text for kind, text in out if kind == "observation"], notes


def _keep_row(project, *, n, authors, word="苦味", date=None, status="ok"):
    material = {"account": "x", "topic": word, "search_type": "RECENT", "requested_limit": 25, "narrow": None,
                "status": status, "cannot_say": None,
                "counts": {"n": n, "distinct_authors": authors, "latest_at": "2026-11-02T10:00:00+09:00",
                           "oldest_at": "2026-11-01T10:00:00+09:00", "n_with_timestamp": n,
                           "n_label_matched": n, "n_label_other": 0, "n_label_absent": 0}}
    row = where_keep.topic_tag_row(date or jst.now_jst().date().isoformat(), material, by="t")
    where_keep.write(project, [row])


def test_topic_adviceは自分のprojectの棚の行をトピックの使用例として数える(account):
    _keep_row("nigamilab", n=8, authors=6)
    texts, _notes = _shortfalls(account["name"])
    assert texts == []
    # 5 未満は欠測で残る → 「残していない」と言う（0 人とも author_key が無いとも言わない）。
    _keep_row("nigamilab", n=8, authors=3)
    texts, _notes = _shortfalls(account["name"])
    assert any("投稿者の数は 5 未満で残っていない" in t for t in texts)
    assert not any("author_key" in t for t in texts)


def test_topic_adviceはほかのprojectと古い行を読まない(account, tmp_path, isolated_account_factory):
    _keep_row("otherproj", n=20, authors=10)                     # ほかの project の棚
    old = (jst.now_jst().date() - datetime.timedelta(days=topic_advice.FRESH_DAYS + 1)).isoformat()
    _keep_row("nigamilab", n=20, authors=10, date=old)           # 自分の棚でも 7 日より古い
    texts, _notes = _shortfalls(account["name"])
    assert any("観測の参照がありません" in t for t in texts), texts


def test_topic_adviceはキーワード検索の数と混ぜない(account):
    # 観測の棚にキーワード検索の観測があっても、トピックの使用例にはならない（棚の行が無ければ足りない）。
    obs = {"topic": "苦味", "normalized_topic": "苦味", "search_mode": "keyword", "provider": "threads_api",
           "retrieved_at": jst.iso(jst.now_jst() - datetime.timedelta(hours=1)), "status": "ok",
           "samples": [{"author_key": f"k{i}", "tagged": True} for i in range(10)], "observation_id": "sha256:kw"}
    texts, _notes = _shortfalls(account["name"], obs)
    assert any("keyword 検索の結果は tag の利用例になりません" in t for t in texts)
    # 棚の行があっても、キーワード検索の 10 件・10 人は足さずに棚の数だけで判定する。
    _keep_row("nigamilab", n=8, authors=3)   # 投稿者は 5 未満で欠測
    texts, _notes = _shortfalls(account["name"], obs)
    assert any("投稿者の数は 5 未満で残っていない" in t for t in texts), texts


# ======================================================== 棚の行の形（柵）

def _valid_row(**over):
    material = {"account": "x", "topic": "苦味", "search_type": "TOP", "requested_limit": 25, "narrow": None,
                "status": "ok", "cannot_say": None,
                "counts": {"n": 6, "distinct_authors": 5, "latest_at": "2026-11-02T10:00:00+09:00",
                           "oldest_at": "2026-11-01T10:00:00+09:00", "n_with_timestamp": 6,
                           "n_label_matched": 6, "n_label_other": 0, "n_label_absent": 0}}
    row = where_keep.topic_tag_row("2026-11-03", material, by="t")
    row.update(over)
    return row


def test_棚の行は許可リストの鍵と5未満なしと日付だけ():
    assert where_keep.valid_row(_valid_row())
    for bad in ({"text": "本文"}, {"username": "alice"}, {"distinct_authors": 3}, {"n": 0},
                {"latest_date": "2026-11-02T10:00:00+09:00"}, {"medium": "x"}, {"status": "partial"},
                {"n": None}, {"window": {"since": None}}, {"submitted_by": "x" * 200}):
        assert not where_keep.valid_row(_valid_row(**bad)), bad
    # 観測の棚（project をまたぐ）には数だけの観測を入れない。
    with pytest.raises(models.SchemaError):
        models.build_observation({"topic": "苦味", "search_mode": "topic_tag", "provider": "threads_api",
                                  "retrieved_at": "2026-11-03T10:00:00+09:00", "status": "ok", "samples": [],
                                  "counts": {"n": 6}})


# ======================================================== 遠くの道（VM の読む口）

def test_サーバの読む口もtagとkeepを受けて数と時刻だけを返す(env, thread):
    got = call(env, "topics_search", query="苦味", tag=True, keep=True, limit=6)
    assert ThreadAdapter.seen["search_mode"] == "TAG"
    assert got["status"] == "ok" and got["counts"]["n"] == 6 and got["storage"] == "kept"
    assert got["kept"]["n"] == 6 and got["kept"]["distinct_authors"] == 6
    # 残した人は鍵の主（監査 N-4）。
    assert got["kept"]["submitted_by"] == env["context"].actor and got["kept"]["submitted_by"]
    assert "posts" not in got and "検索の本文" not in json.dumps(got, ensure_ascii=False)
    assert "who0" not in json.dumps(got, ensure_ascii=False)

