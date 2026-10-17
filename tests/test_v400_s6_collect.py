"""4.0 S6 §4・§10: 反応の採取と台帳（T-S6-1a〜3c・7）。

はてなの公開の読む口は偽物（`FakeHatena`・`httpsafe.urlopen` の差し替え）と、127.0.0.1 に立てた本物の
HTTP サーバ（本物の urllib の経路）だけ。**はてなには一切つながない。** repo は `tmp_path` の本物の git。
"""
from __future__ import annotations

import datetime
import http.server
import json
import os
import subprocess
import threading
import urllib.parse

import pytest

from tests.conftest import run_git
from tests.hatena_s1_helpers import account_data
from tests.hatena_s2_http import put_secrets
from tests.hatena_s6_helpers import (ACCOUNT, NOW, SELF, SIBLING, URL1, URL2, URL3, bm, call, collect,  # noqa: F401
                                     fake_http, lane, ledger_file, ledger_rows, put_delivery, s6, snapshot, world)
from thth import accounts, cli, collect as collect_mod, core, deliveries, hatena_reactions, jst, principals, runs
from thth.adapters import hatena_common, hatenabookmark, hatenastar


def _hours(h):
    return NOW + datetime.timedelta(hours=h)


# ---------------------------------------------------------------- T-S6-1a 置き場

def test_1a_置き場はrepoのdata_hatena_reactionsでいいねの台帳とは別の木(s6):
    """柵: 置き場を `state/<口座>/reactions/` にすると赤。"""
    dl = put_delivery(URL1, hours_ago=2)
    s6.hatena.counts[URL1] = 1
    assert collect() == 0
    expected = os.path.join(s6.repo, "data", "hatena", "reactions", f"{dl}.ndjson")
    assert os.path.realpath(ledger_file(dl)) == os.path.realpath(expected)
    assert os.path.exists(expected)
    assert not os.path.exists(os.path.join(s6.state, "reactions"))


def test_1a_repoの無い口座はstateのdata_hatena_reactions(s6):
    world = s6.world
    data = account_data("lane-a-norepo", project="lane-a", principal="hatena-main", blog_id="a.hatenablog.com",
                        token=world.main_token)
    world.write_account(data)
    dl = put_delivery(URL1, hours_ago=2, account="lane-a-norepo")
    s6.hatena.counts[URL1] = 0
    assert collect(account="lane-a-norepo") == 0
    path = os.path.join(world.state("lane-a-norepo"), "data", "hatena", "reactions", f"{dl}.ndjson")
    assert os.path.realpath(ledger_file(dl, "lane-a-norepo")) == os.path.realpath(path)
    assert os.path.exists(path)
    assert not os.path.exists(os.path.join(world.state("lane-a-norepo"), "reactions"))


# ---------------------------------------------------------------- T-S6-1b 追記と冪等

def test_1b_同じ刻みで2度採取しても1行で2度目はHTTPを呼ばない(s6):
    """柵: `due`（`due_marks`）を外して跨いだ刻みを毎回読むと赤。"""
    dl = put_delivery(URL1, hours_ago=25)
    s6.hatena.counts[URL1] = 2
    s6.hatena.entries[URL1] = [bm("alice"), bm("bob")]
    assert collect() == 0
    calls = len(s6.hatena.calls)
    assert calls == 3   # 件数・スター・エントリー情報が 1 回ずつ
    assert collect() == 0
    assert len(s6.hatena.calls) == calls
    rows = ledger_rows(dl)
    assert len(rows) == 1 and rows[0]["marks"] == [1, 6, 24]
    # repo に commit して push してある（作業ツリーに残っていない）。
    status = run_git(s6.repo, ["status", "--porcelain"]).stdout
    assert "data/hatena" not in status
    assert "反応の採取" in run_git(s6.repo, ["log", "-1", "--format=%s", "origin/main"]).stdout


def test_1b_刻みを跨いでいなければHTTPを呼ばない(s6):
    put_delivery(URL1, hours_ago=0.5)
    assert collect() == 0
    assert s6.hatena.calls == []


# ---------------------------------------------------------------- T-S6-1c 取れなかった指標

def test_1c_スターが500なら行にstarが無く次のrunで同じ刻みを試す(s6):
    """柵: 500 で `star: {others: 0}` を書くと赤。"""
    dl = put_delivery(URL1, hours_ago=1.5)
    s6.hatena.counts[URL1] = 1
    s6.hatena.fail["stars"] = 500
    assert collect() == 1
    (row,) = ledger_rows(dl)
    assert "star" not in row and row["bookmark"]["total"] == 1
    assert {"what": "star", "reason": "not_fetched:hatena_http_500"} in row["missing"]
    # 次の run（10 分後）で同じ刻みをもう一度試す。
    del s6.hatena.fail["stars"]
    s6.hatena.stars[URL1] = {"stars": [{"name": "alice"}]}
    assert collect(_hours(1 / 6)) == 0
    rows = ledger_rows(dl)
    assert len(rows) == 2 and rows[1]["marks"] == [1] and rows[1]["star"]["others"] == 1
    before = len(s6.hatena.calls)
    assert collect(_hours(2 / 6)) == 0
    assert len(s6.hatena.calls) == before   # 刻み 1 は済んだ


def test_1c_何も取れなければ指標の鍵の無い欠けの行だけ(s6):
    """監査 高-1 のあと: 取れなかった試みも欠けの行として残す（再試行の回数を数える材料）。指標の鍵は置かない。"""
    dl = put_delivery(URL1, hours_ago=1.5)
    s6.hatena.fail.update({"counts": 503, "stars": 503})
    assert collect() == 1
    (row,) = ledger_rows(dl)
    assert "bookmark" not in row and "star" not in row and row["marks"] == [1]
    assert {m["what"]: m["reason"] for m in row["missing"]} == {"bookmark": "not_fetched:hatena_http_503",
                                                                "star": "not_fetched:hatena_http_503"}
    rows = [r for r in runs.read_runs(s6.state) if r.get("action") == "hatena_collect"]
    assert rows[-1]["status"] == "error" and "hatena_http_503" in rows[-1]["error"]


# ---------------------------------------------------------------- T-S6-2a 件数をまとめる

def test_2a_跨いだ3配信の件数はcount_entries1回にまとめる(s6, monkeypatch):
    """柵: まとめずに 1 件ずつ呼ぶと赤。"""
    slept = []
    monkeypatch.setattr(hatena_reactions, "SLEEP", slept.append)
    ids = [put_delivery(u, hours_ago=2 + i) for i, u in enumerate((URL1, URL2, URL3))]
    s6.hatena.counts.update({URL1: 1, URL2: 2, URL3: 3})
    assert collect() == 0
    counts = [c for c in s6.hatena.calls if c[0] == "counts"]
    assert len(counts) == 1 and sorted(counts[0][1]) == sorted([URL1, URL2, URL3])
    assert [ledger_rows(dl)[0]["bookmark"]["total"] for dl in ids] == [1, 2, 3]
    # 件数 1・スター 1・エントリー情報 3 → 要求の間は 4 回とも 2 秒。
    assert slept == [2] * (len(s6.hatena.calls) - 1)


def test_2a_51件は2回に分ける(s6, monkeypatch):
    slept = []
    monkeypatch.setattr(hatena_reactions, "SLEEP", slept.append)
    urls = [f"https://a.hatenablog.com/entry/2026/09/01/{i:06d}" for i in range(51)]
    got, failed = hatena_reactions.fetch_counts(urls)
    assert failed == {} and got == {u: None for u in urls}   # 答えに鍵の無い URL は None（0 と混ぜない・監査 中-6）
    sizes = [len(c[1]) for c in s6.hatena.calls]
    assert sizes == [50, 1] and slept == [2]


# ---------------------------------------------------------------- T-S6-2b 自分の分

def test_2b_同じownerの主体のIDはselfに分けothersから引く(s6):
    """柵: エントリー情報が取れないときに `others` を `total` に倒すと赤。"""
    dl = put_delivery(URL1, hours_ago=2)
    s6.hatena.counts[URL1] = 3
    s6.hatena.entries[URL1] = [bm(SELF, "自分"), bm("alice", "いい記事", ["コーヒー"]), bm("bob")]
    assert collect() == 0
    (row,) = ledger_rows(dl)
    b = row["bookmark"]
    assert (b["total"], b["others"], b["self"], b["comments_n"], b["cached"]) == (3, 2, [SELF], 1, True)
    assert [x["user"] for x in b["bookmarks"]] == ["alice", "bob"]
    assert b["bookmarks"][0] == {"user": "alice", "timestamp": "2026/09/08 21:03", "comment": "いい記事",
                                 "tags": ["コーヒー"]}


def test_2b_別の主体のIDもselfに数える(s6):
    dl = put_delivery(URL1, hours_ago=2)
    s6.hatena.counts[URL1] = 2
    s6.hatena.entries[URL1] = [bm(SIBLING), bm("alice")]
    collect()
    b = ledger_rows(dl)[0]["bookmark"]
    assert (b["others"], b["self"]) == (1, [SIBLING])


def test_2b_エントリー情報が失敗ならothersはnullでown_unknown(s6):
    dl = put_delivery(URL1, hours_ago=2)
    s6.hatena.counts[URL1] = 3
    s6.hatena.fail["entry"] = 500
    collect()
    (row,) = ledger_rows(dl)
    assert row["bookmark"]["total"] == 3 and row["bookmark"]["others"] is None
    assert "bookmarks" not in row["bookmark"] and "self" not in row["bookmark"]
    assert {"what": "bookmark_others", "reason": "own_unknown", "detail": "hatena_http_500"} in row["missing"]


# ---------------------------------------------------------------- T-S6-2c スター

def test_2c_スターはnameでselfとothersに分けcount省略は1でquoteは台帳に無い(s6):
    """柵: `quote` を台帳に書くと赤。"""
    dl = put_delivery(URL1, hours_ago=2)
    s6.hatena.counts[URL1] = 0
    s6.hatena.stars[URL1] = {
        "stars": [{"name": "alice", "quote": "引用の文"}, {"name": SELF, "quote": ""}, {"name": "bob", "count": 3}],
        "colored_stars": [{"color": "green", "stars": [{"name": "carol", "quote": "緑の引用"}]}]}
    collect()
    (row,) = ledger_rows(dl)
    assert row["star"] == {"others": 5, "self": 1, "by_color": {"yellow": 4, "green": 1}}
    with open(ledger_file(dl), encoding="utf-8") as f:
        text = f.read()
    assert "quote" not in text and "引用" not in text


# ---------------------------------------------------------------- T-S6-2d 平文に出ない・本物の経路

def test_2d_スターのBASEがhttpsでなければimportでValueError():
    path = hatenastar.__file__
    with open(path, encoding="utf-8") as f:
        source = f.read()
    broken = source.replace('BASE = "https://s.hatena.ne.jp"', 'BASE = "http://s.hatena.com"')
    assert broken != source
    with pytest.raises(ValueError, match="hatenastar_base_not_https"):
        exec(compile(broken, path, "exec"), {"__name__": "thth.adapters.hatenastar_broken",
                                              "__package__": "thth.adapters"})


class _Server:
    """127.0.0.1 の本物の HTTP サーバ。`answers[path] = (status, bytes)`（path は query を除いた形）。"""

    def __init__(self):
        self.answers, self.requests = {}, []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                parts = urllib.parse.urlsplit(self.path)
                server.requests.append({"path": parts.path, "query": urllib.parse.parse_qs(parts.query),
                                        "headers": {k.lower(): v for k, v in self.headers.items()}})
                status, raw = server.answers.get(parts.path, (404, b"no"))
                if callable(raw):
                    raw = raw(urllib.parse.parse_qs(parts.query))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        return False


def test_2d_本物のurllibの経路で認証なしUAに連絡先(lane, monkeypatch):  # noqa: F811
    """`httpsafe.urlopen` は差し替えない。柵: 公開の読む口に `Authorization` を付けると赤。"""
    from tests.conftest import restore_real_hatena_adapters
    monkeypatch.setenv("THTH_TEST_ALLOW_HTTP", "1")
    restore_real_hatena_adapters(monkeypatch)
    # 偽物の HTTP は置かず 127.0.0.1 の本物のサーバへ出るので、公開の読む口の柵を明示して外す（S6 監査 中-4）。
    monkeypatch.setattr(hatena_reactions, "FETCH", None)
    lane.world.edit_principal("hatena-main", owner="owner-m", user_agent_contact="https://example.test/contact")
    put_secrets(lane.world)   # token があっても公開の読む口には付けない
    record = {"delivery": "dl_000000000001", "published_url": URL1}
    with _Server() as srv:
        base = f"http://127.0.0.1:{srv.port}"
        monkeypatch.setattr(hatenabookmark, "BASE", base)
        monkeypatch.setattr(hatenabookmark, "PUBLIC_BASE", base)
        monkeypatch.setattr(hatenastar, "BASE", base)
        srv.answers["/count/entries"] = (200, lambda q: json.dumps({u: 4 for u in q["url"]}).encode())
        srv.answers["/entry/jsonlite/"] = (200, json.dumps({"count": 2, "bookmarks": [
            {"user": SELF, "comment": "", "tags": [], "timestamp": "2026/09/08 10:00"},
            {"user": "alice", "comment": "c", "tags": [], "timestamp": "2026/09/08 11:00"}]}).encode())
        srv.answers["/entry.json"] = (200, json.dumps({"entries": [
            {"uri": URL1, "stars": [{"name": "alice", "quote": "q"}]}]}).encode())
        cfg = accounts.load_account(ACCOUNT)
        got = hatena_reactions.observe([record], own=hatena_reactions.self_ids(cfg),
                                       contact=hatena_reactions.contact_of(cfg))
        assert got["dl_000000000001"]["bookmark"]["others"] == 3
        assert got["dl_000000000001"]["star"]["others"] == 1
        assert [r["path"] for r in srv.requests] == ["/count/entries", "/entry.json", "/entry/jsonlite/"]
        for row in srv.requests:
            assert "authorization" not in row["headers"]
            assert row["headers"]["user-agent"].endswith("(+https://example.test/contact)")
        # 5xx は符丁で（再試行しない）。
        srv.answers["/entry.json"] = (500, b"oops")
        before = len(srv.requests)
        _got, failed = hatena_reactions.fetch_stars([URL1])
        assert failed == {URL1: "hatena_http_500"} and len(srv.requests) == before + 1


def test_2d_平文のhttpは本番の既定で断る(monkeypatch):
    monkeypatch.delenv("THTH_TEST_ALLOW_HTTP", raising=False)
    with pytest.raises(hatena_common.PublicHTTPError) as exc:
        hatena_common.public_get("http://s.hatena.com/entry.json?uri=x")
    assert exc.value.code == "endpoint_rejected"


# ---------------------------------------------------------------- T-S6-3a run の末尾

def _run(lane, monkeypatch):  # noqa: F811
    put_secrets(lane.world)
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    monkeypatch.setattr(cli.selfupdate_mod, "pull_and_reexec", lambda *_a, **_k: None)
    monkeypatch.setattr(cli.core, "throw_once",
                        lambda *_a, **_k: core.ThrowResult(0, "production", "skip", "出すものが無い"))
    return call(["run", ACCOUNT])


def test_3a_はてなの口座のrunは反応を採りrunsにhatena_collect(s6, monkeypatch):
    dl = put_delivery(URL1, hours_ago=1.2)
    s6.hatena.counts[URL1] = 1
    sns_called = []
    monkeypatch.setattr(cli.collect_mod, "run_collect", lambda *a, **k: sns_called.append(a) or 0)
    rc, out, err = _run(s6, monkeypatch)
    assert rc == 0, (out, err)
    assert sns_called == []                 # SNS の採取は呼ばない（今までどおり）
    assert len(ledger_rows(dl)) == 1
    rows = [r for r in runs.read_runs(s6.state) if r.get("action") == "hatena_collect"]
    assert len(rows) == 1 and rows[0]["trigger"] == "run" and rows[0]["collected"] == 1
    assert rows[0]["status"] == "ok" and rows[0]["mode"] == "collect"


def test_3a_SNSの口座のrunは今までどおりcollectだけ(monkeypatch):
    called = {"sns": 0, "hatena": 0}
    monkeypatch.setattr(collect_mod, "run_collect", lambda *a, **k: called.__setitem__("sns", called["sns"] + 1) or 0)
    monkeypatch.setattr(hatena_reactions, "run_collect",
                        lambda *a, **k: called.__setitem__("hatena", called["hatena"] + 1) or 0)
    assert cli.collect_applies({"media": "threads"}) is True
    assert cli.principals_mod_uses({"media": "threads"}) is False
    assert cli.principals_mod_uses({"media": "hatenablog", "principal": "p"}) is True


# ---------------------------------------------------------------- T-S6-3b 対象

def test_3b_deletedは読まずconflictとno_followは読む_38日を過ぎたら読まない(s6):
    """柵: `deleted` を対象に入れると赤。"""
    gone = put_delivery(URL1, hours_ago=2, state="deleted")
    conflict = put_delivery(URL2, hours_ago=2, state="conflict")
    no_follow = put_delivery(URL3, hours_ago=2, state="no_follow")
    old = put_delivery("https://a.hatenablog.com/entry/old", hours_ago=24 * 39)
    s6.hatena.counts.update({URL1: 1, URL2: 1, URL3: 1})
    assert collect() == 0
    counted = [u for c in s6.hatena.calls if c[0] == "counts" for u in c[1]]
    assert sorted(counted) == sorted([URL2, URL3])
    assert ledger_rows(gone) == [] and ledger_rows(old) == []
    assert len(ledger_rows(conflict)) == 1 and len(ledger_rows(no_follow)) == 1


def test_3b_起点は最初のpublishedの行(s6):
    dl = put_delivery(URL1, hours_ago=30, history=[("conflict", 5), ("published", 2)])
    s6.hatena.counts[URL1] = 1
    collect()
    (row,) = ledger_rows(dl)
    assert row["marks"] == [1, 6, 24] and row["published_at"] == jst.iso(NOW - datetime.timedelta(hours=30))


# ---------------------------------------------------------------- T-S6-3c 止めと push できない repo

def test_3c_主体を止めても採取は走る(s6):
    dl = put_delivery(URL1, hours_ago=2)
    s6.hatena.counts[URL1] = 1
    principals.stop("hatena-main", "owner", actor="tester")
    assert principals.stopped("hatena-main")
    assert collect() == 0
    assert len(ledger_rows(dl)) == 1


def test_3c_upstreamの無いrepoでは読まない書かない(s6):
    dl = put_delivery(URL1, hours_ago=2)
    s6.hatena.counts[URL1] = 1
    run_git(s6.repo, ["branch", "--unset-upstream"])
    run_git(s6.repo, ["remote", "remove", "origin"])
    assert collect() == 2
    assert s6.hatena.calls == []
    assert not os.path.exists(ledger_file(dl))
    rows = [r for r in runs.read_runs(s6.state) if r.get("action") == "hatena_collect"]
    assert rows[-1]["error"] == "not_synced" and rows[-1]["collected"] is None


def test_3c_pushできなければcommitを取り消してファイルに残す(s6, monkeypatch):
    from thth import writeback
    dl = put_delivery(URL1, hours_ago=2)
    s6.hatena.counts[URL1] = 1
    head = run_git(s6.repo, ["rev-parse", "HEAD"]).stdout.strip()

    def fail_push(repo_dir, *, rel_path, message, validate=None):
        writeback.commit_local(repo_dir, rel_path=rel_path, message=message)
        return False, "push_rejected"
    monkeypatch.setattr(writeback, "commit_and_push", fail_push)
    assert collect() == 1
    assert run_git(s6.repo, ["rev-parse", "HEAD"]).stdout.strip() == head
    assert len(ledger_rows(dl)) == 1


# ---------------------------------------------------------------- hatena collect（手で 1 回）

def test_hatena_collectは跨いでいなければ次の刻みを言って読まない(s6):
    put_delivery(URL1, hours_ago=0.5)
    rc, out, err = call(["hatena", "collect", ACCOUNT, "--by", "tester"])
    assert rc == 0, (out, err)
    assert "跨いでいない（次は 1h・9/9 10:30 ごろ）" in out
    assert s6.hatena.calls == []
    rows = [r for r in runs.read_runs(s6.state) if r.get("action") == "hatena_collect"]
    assert rows[-1]["trigger"] == "manual" and rows[-1]["collected"] == 0


def test_hatena_collectはdeliveryで1本だけ(s6):
    a = put_delivery(URL1, hours_ago=2)
    b = put_delivery(URL2, hours_ago=2)
    s6.hatena.counts.update({URL1: 1, URL2: 1})
    rc, out, err = call(["hatena", "collect", ACCOUNT, "--delivery", a])
    assert rc == 0, (out, err)
    assert len(ledger_rows(a)) == 1 and ledger_rows(b) == []


def test_hatena_collectはSNSの口座を断る(world, tmp_path):  # noqa: F811
    from tests.conftest import make_account_json
    make_account_json(str(world.accounts_dir), "lane-a-threads", repo_dir="$THTH_ROOT/repos/_none", project="lane-a")
    rc, out, err = call(["hatena", "collect", "lane-a-threads"])
    assert rc == 2 and "not_hatena" in err


# ---------------------------------------------------------------- T-S6-7 指紋・sent/・配信の台帳

def test_7_採取の前後で指紋とsentと配信の台帳が1バイトも変わらない(s6):
    from thth import approval, fingerprint_golden
    from tests.hatena_s4_helpers import record_row
    put_delivery(URL1, hours_ago=30)
    put_delivery(URL2, hours_ago=2, state="conflict")
    record_row(s6.world, ACCOUNT, URL1, target="own")
    s6.hatena.counts.update({URL1: 2, URL2: 1})
    s6.hatena.entries[URL1] = [bm(SELF), bm("alice", "c")]
    s6.hatena.stars[URL1] = {"stars": [{"name": "alice"}]}
    before = snapshot(os.path.join(s6.state, "sent")), snapshot(os.path.join(s6.state, "deliveries"))
    assert collect() == 0
    rc, out, err = call(["article", "reactions", ACCOUNT, "--article", "lane-a/arabica", "--fetch", "--json"])
    assert rc == 0, (out, err)
    after = snapshot(os.path.join(s6.state, "sent")), snapshot(os.path.join(s6.state, "deliveries"))
    assert before == after and before[0] and before[1]
    assert fingerprint_golden.drift() == []
    assert approval.FINGERPRINT_VERSION == 2


def test_2c_adapterの返りにもquoteが無い(s6):
    s6.hatena.stars[URL1] = {"stars": [{"name": "alice", "quote": "引用の文"}, 2],
                             "colored_stars": [{"color": "red", "stars": [{"name": "bob", "quote": "q", "count": 2}]}]}
    got = hatenastar.entry_stars([URL1, URL2])
    assert got == {URL1: [{"name": "alice", "count": 1, "color": "yellow"},
                          {"name": None, "count": 2, "color": "yellow"},
                          {"name": "bob", "count": 2, "color": "red"}], URL2: []}


def test_既定の柵_偽物なしで公開の読む口を呼ぶと落ちる(monkeypatch):
    """柵: conftest の `hatena_reactions.FETCH` の既定の柵を外すと赤（本物のはてなへ出て行く道が開く）。

    柵を外して赤を見るときにも本物のはてなへ出ないよう、HTTP の口そのものも塞いでおく（塞いだ口で
    落ちれば、既定の柵の文が出ないので赤になる）。"""
    from thth import httpsafe

    def no_network(req, *, timeout):
        raise AssertionError("HTTP の口まで来た（既定の柵が効いていない）")
    monkeypatch.setattr(httpsafe, "urlopen", no_network)
    with pytest.raises(pytest.fail.Exception, match="公開の読む口"):
        hatena_reactions.fetch_counts([URL1])
