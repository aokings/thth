"""4.0 S6 の監査（Fable）の直しを縛る（高-1・高-2・中-1〜6・低-1〜4）。

はてなの公開の読む口は偽物（`FakeHatena`）だけ。**はてなには一切つながない。**
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess

import pytest

from tests import conftest
from tests.conftest import run_git
from tests.hatena_s4_helpers import record_row
from tests.hatena_s6_helpers import (ACCOUNT, NOW, SELF, URL1, URL2, bm, collect, fake_http, lane,  # noqa: F401
                                     ledger_file, ledger_rows, put_delivery, s6, world)
from tests.test_v400_s6_bundle import THREADS, article  # noqa: F401 — fixture
from thth import (accounts, doctor, hatena_reactions, httpsafe, jst, morning, principals, report, runs,
                  server_reads, writeback)
from thth.adapters import hatenastar


def _at(hours):
    return NOW + datetime.timedelta(hours=hours)


def _hatena_runs(lane_):
    return [r for r in runs.read_runs(lane_.state) if r.get("action") == "hatena_collect"]


# ---------------------------------------------------------------- 高-1 終わりがある

def test_高1_スターの404は読み直さない_監査の再現と同じ筋道(s6):
    """柵: 404 を `not_supported` に寄せないと赤（6 run で 6 行・毎回 HTTP）。"""
    dl = put_delivery(URL1, hours_ago=1.5)
    s6.hatena.counts[URL1] = 1
    s6.hatena.fail["stars"] = 404
    seen = []
    for i in range(6):   # 10 分ごとの run を 6 回
        hatena_reactions.run_collect(ACCOUNT, now=_at(i / 6), log=lambda *_a: None)
        seen.append((len(ledger_rows(dl)), len(s6.hatena.calls)))
    assert seen[-1] == seen[0] == (1, 3)
    (row,) = ledger_rows(dl)
    assert {"what": "star", "reason": "not_supported", "detail": "hatena_http_404"} in row["missing"]


def test_高1_取れない出所は3回で諦めてgave_upを書き_それ以上読まない(s6):
    """柵: 再試行の上限を外すと赤（20 run で 20 行・HTTP が止まらない）。"""
    dl = put_delivery(URL1, hours_ago=1.2)
    s6.hatena.counts[URL1] = 1
    s6.hatena.fail["stars"] = 500
    for i in range(20):   # 1.2h〜4.4h（次の刻み 6h には達しない）
        hatena_reactions.run_collect(ACCOUNT, now=_at(i / 6), log=lambda *_a: None)
    rows = ledger_rows(dl)
    assert len(rows) == hatena_reactions.MAX_ATTEMPTS + 1
    assert rows[-1]["marks"] == [1] and rows[-1]["missing"] == [{"what": "star", "reason": "not_fetched:gave_up"}]
    assert "bookmark" not in rows[-1] and "star" not in rows[-1]
    assert len(s6.hatena.calls) == 3 * hatena_reactions.MAX_ATTEMPTS
    assert hatena_reactions.due(__import__("thth").deliveries.read(ACCOUNT, dl), rows, _at(4)) == []


def test_高1_1回取れずに次の刻みに達したら前の刻みは諦めて次を読む(s6):
    dl = put_delivery(URL1, hours_ago=1.2)
    s6.hatena.counts[URL1] = 1
    s6.hatena.fail["stars"] = 500
    collect()                                 # 刻み 1 を 1 回試して取れない
    del s6.hatena.fail["stars"]
    collect(_at(5))                           # 6.2h: 刻み 1 は諦め、刻み 6 を読む
    rows = ledger_rows(dl)
    gave = [r for r in rows if r["marks"] == [1] and {"what": "star", "reason": "not_fetched:gave_up"} in r["missing"]]
    assert len(gave) == 1 and rows[-1]["marks"] == [6] and rows[-1]["star"]["others"] == 0


# ---------------------------------------------------------------- 高-2 飢え

def _twelve(s6, hours_ago=30):  # noqa: F811
    urls = [f"https://a.hatenablog.com/entry/2026/09/0{i % 9 + 1}/09000{i}" for i in range(12)]
    for i, u in enumerate(urls):
        put_delivery(u, hours_ago=hours_ago - i)   # 全部 24h の刻みを跨いでいる
        s6.hatena.counts[u] = 1
    return urls


def _entries_read(s6):  # noqa: F811
    return {c[1] for c in s6.hatena.calls if c[0] == "entry"}


def test_高2_監査の再現_4runのあと読まれない配信は無い(s6):
    urls = _twelve(s6)
    s6.hatena.fail["stars"] = 404
    for i in range(4):
        hatena_reactions.run_collect(ACCOUNT, now=_at(i / 6), log=lambda *_a: None)
    assert [u for u in urls if u not in _entries_read(s6)] == []


def test_高2_読み直しより一度も試していない配信を先に(s6):
    """柵: 候補を公開の古い順だけで並べると赤（読み直しが 10 本の枠を埋めて 11・12 本目が飢える）。"""
    urls = _twelve(s6)
    s6.hatena.fail["stars"] = 500             # どの配信も読み直しが要る
    collect()
    first = _entries_read(s6)
    assert len(first) == 10
    s6.hatena.calls.clear()
    collect(_at(1 / 6))
    assert set(urls) - first <= _entries_read(s6)   # 残りの 2 本が 2 回目に読まれる


# ---------------------------------------------------------------- 中-1・中-2 自分の分が引けない

def test_中1_自分のIDが読めなければothersはnullで第三者の一覧も書かない(s6, monkeypatch):
    """柵: 自分の ID が空でも `total` から引いて書くと赤。"""
    dl = put_delivery(URL1, hours_ago=2)
    s6.hatena.counts[URL1] = 2
    s6.hatena.entries[URL1] = [bm(SELF), bm("alice", "c")]
    monkeypatch.setattr(hatena_reactions, "self_ids", lambda cfg: set())
    collect()
    (row,) = ledger_rows(dl)
    assert row["bookmark"]["others"] is None and "bookmarks" not in row["bookmark"]
    assert {"what": "bookmark_others", "reason": "own_unknown", "detail": "self_unknown"} in row["missing"]


def test_中1_主体の台帳が読めないとself_idsは空(s6, monkeypatch):
    def broken(name):
        raise principals.PrincipalError("principal_unreadable", "x")
    monkeypatch.setattr(principals, "siblings", broken)
    monkeypatch.setattr(principals, "load_principal", broken)
    assert hatena_reactions.self_ids({"principal": "hatena-main"}) == set()


def test_中2_自分の登録があるのにキャッシュに自分が無ければothersはnull(s6):
    """柵: 照らし合わせを外すと赤（自分の分が件数に入ったまま第三者に数える）。"""
    dl = put_delivery(URL1, hours_ago=2)
    record_row(s6.world, ACCOUNT, URL1, target="own", delivery=dl)
    s6.hatena.counts[URL1] = 2
    s6.hatena.entries[URL1] = [bm("alice", "c")]          # キャッシュに自分がまだ無い
    collect()
    (row,) = ledger_rows(dl)
    assert row["bookmark"]["others"] is None and [b["user"] for b in row["bookmark"]["bookmarks"]] == ["alice"]
    assert {"what": "bookmark_others", "reason": "own_unknown", "detail": "self_not_in_cache"} in row["missing"]


def test_中2_キャッシュに自分が載っていればothersを引く_消した登録は照らさない(s6):
    a = put_delivery(URL1, hours_ago=2)
    b = put_delivery(URL2, hours_ago=2)
    record_row(s6.world, ACCOUNT, URL1, target="own", delivery=a)
    record_row(s6.world, ACCOUNT, URL2, target="own", delivery=b, retracted=True)
    s6.hatena.counts.update({URL1: 2, URL2: 1})
    s6.hatena.entries[URL1] = [bm(SELF), bm("alice")]
    s6.hatena.entries[URL2] = [bm("bob")]
    collect()
    assert ledger_rows(a)[0]["bookmark"]["others"] == 1
    assert ledger_rows(b)[0]["bookmark"]["others"] == 1


# ---------------------------------------------------------------- 中-3 morning の範囲

def test_中3_morningのarticlesの告知の数は範囲の中だけ(article):
    """柵: `announcements` に `allowed_names` を渡さないと赤（範囲外の口座の告知を数える）。"""
    later = NOW + datetime.timedelta(days=1)
    full = morning.build(ACCOUNT, now=later, mark=False, no_world=True)
    scoped = morning.build(ACCOUNT, now=later, mark=False, no_world=True, allowed_names=(ACCOUNT, THREADS))
    pick = lambda p: p["sections_by_name"]["yesterday"]["value"]["by_account"][ACCOUNT]["value"]["articles"][0]  # noqa: E731
    assert pick(full)["announcements_n"] == 2 and pick(scoped)["announcements_n"] == 1


# ---------------------------------------------------------------- 中-4 柵を外すのは fake_http だけ

def test_中4_restore_real_hatena_adaptersは公開の読む口の柵を外さない(monkeypatch):
    """柵: `restore_real_hatena_adapters` が `FETCH` も None に戻すと赤。"""
    conftest.restore_real_hatena_adapters(monkeypatch)
    assert hatena_reactions.FETCH is conftest.no_real_hatena_fetch


def test_中4_fake_httpを置けば柵が外れる(fake_http):
    fake_http(lambda *a: (200, {}, b"{}"))
    assert hatena_reactions.FETCH is None


# ---------------------------------------------------------------- 中-5 退出中の口座

def test_中5_退出中の口座では読まない書かない(s6):
    """柵: lease で包まないと赤（退出中でも HTTP を打ち、台帳と runs を書く）。"""
    dl = put_delivery(URL1, hours_ago=2)
    s6.hatena.counts[URL1] = 1
    path = ledger_file(dl)
    leave_dir = pathlib.Path(s6.world.root) / "state" / "_leave"
    leave_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(leave_dir, 0o700)
    (leave_dir / f"{ACCOUNT}.json").write_text("{}")
    assert collect() == 2
    assert s6.hatena.calls == []
    assert not os.path.exists(path)
    assert _hatena_runs(s6) == []


def test_中5_採取のHTTPは口座のleaseを握ったまま打つ(s6):
    """柵: lease で包まないと赤（読んでいる間に退出が始まれば、退出の片づけと競う）。"""
    from thth import leave_gate
    put_delivery(URL1, hours_ago=2)
    s6.hatena.counts[URL1] = 1
    held = []
    real = s6.http.handler

    def spy(method, url, headers, body):
        held.append(ACCOUNT in leave_gate._held.get())
        return real(method, url, headers, body)
    s6.http.handler = spy
    assert collect() == 0
    assert held and all(held)


# ---------------------------------------------------------------- 中-6 答えに無い URL を 0 と読まない

def test_中6_件数の鍵が無くエントリー情報に件数があれば書かない(s6):
    """柵: 鍵の無い URL を 0 と読むと赤。"""
    dl = put_delivery(URL1, hours_ago=2)
    s6.hatena.entries[URL1] = [bm("alice")]               # counts には URL1 を載せない
    collect()
    (row,) = ledger_rows(dl)
    assert "bookmark" not in row
    assert {"what": "bookmark", "reason": "not_fetched:response_invalid", "detail": "count_absent"} in row["missing"]


def test_中6_件数の鍵が無くエントリーも無ければ0(s6):
    dl = put_delivery(URL1, hours_ago=2)
    collect()
    (row,) = ledger_rows(dl)
    assert row["bookmark"]["total"] == 0 and row["bookmark"]["others"] == 0


def test_中6_スターの答えのURIが1つも合わなければresponse_invalid(s6):
    """柵: 合わない答えを「スター 0」と読むと赤。"""
    dl = put_delivery(URL1, hours_ago=2)
    s6.hatena.counts[URL1] = 0
    s6.hatena.stars["https://a.hatenablog.com/other"] = {"stars": [{"name": "x"}]}
    real = s6.hatena.__call__

    def answer_other(method, url, headers, body):
        if url.startswith("https://s.hatena.ne.jp/"):
            return 200, {}, json.dumps({"entries": [{"uri": "https://a.hatenablog.com/other",
                                                     "stars": [{"name": "x"}]}]}).encode()
        return real(method, url, headers, body)
    s6.http.handler = answer_other
    collect()
    (row,) = ledger_rows(dl)
    assert "star" not in row and {"what": "star", "reason": "not_fetched:response_invalid"} in row["missing"]


# ---------------------------------------------------------------- 低-1 取り消した行を次の run で送る

def test_低1_pushに失敗して残った行は次のrunのcommitに入る(s6, monkeypatch):
    """柵: 作業ツリーに残った台帳を拾わないと赤（未追跡のまま残る）。"""
    dl = put_delivery(URL1, hours_ago=2)
    s6.hatena.counts[URL1] = 1
    real = writeback.commit_and_push

    def fail_push(repo_dir, *, rel_path, message, validate=None):
        writeback.commit_local(repo_dir, rel_path=rel_path, message=message)
        return False, "push_rejected"
    monkeypatch.setattr(writeback, "commit_and_push", fail_push)
    assert collect() == 1
    monkeypatch.setattr(writeback, "commit_and_push", real)
    assert collect(_at(1 / 6)) == 0                     # 跨いだ刻みは無い・残った行だけを送る
    assert s6.hatena.calls and len(ledger_rows(dl)) == 1
    assert "data/hatena" not in run_git(s6.repo, ["status", "--porcelain", "--untracked-files=all"]).stdout
    assert "反応の採取" in run_git(s6.repo, ["log", "-1", "--format=%s", "origin/main"]).stdout


# ---------------------------------------------------------------- 低-2 errors_n は項目の数

def test_低2_boardのerrors_nは項目の数(s6):
    """柵: `error` の文字列の区切りで数えると赤（220 字で切れて少なく数える）。"""
    _twelve(s6)
    s6.hatena.fail.update({"counts": 503, "stars": 503})
    collect()
    (row,) = _hatena_runs(s6)
    assert row["n"] == 20 and len(row["error"]) <= 220
    assert report._hatena_collect_cells(ACCOUNT)["hatena_collect_errors_n"] == 20


# ---------------------------------------------------------------- 低-3 doctor は 1 ファイルずつ

def test_低3_doctorは名前の違うファイル1つで全部を読めないにしない(s6, monkeypatch):
    dl = put_delivery(URL1, hours_ago=2)
    s6.hatena.counts[URL1] = 1
    collect()
    # 名前の順で台帳より先に読まれる名前（1 本で全部を止めないかを見る）。
    with open(os.path.join(os.path.dirname(ledger_file(dl)), "0junk.ndjson"), "w") as f:
        f.write("{}\n")
    monkeypatch.setattr(hatena_reactions, "FETCH", lambda kind, arg: {u: 0 for u in arg})
    cfg = accounts.load_account(ACCOUNT)
    out = doctor._diagnose_reactions(ACCOUNT, cfg, principals.load_principal(cfg["principal"]))
    assert (out["deliveries"], out["unreadable"], out["count_ok"]) == (1, 1, True)


# ---------------------------------------------------------------- 低-4 comments も縮める

def test_低4_大きい答えはcommentsも縮める(monkeypatch):
    rows = [{"collected_at": "x"}]
    comments = [{"user": f"u{i}", "comment": "あ" * 200} for i in range(50)]
    result = {"deliveries": [{"rows": list(rows), "comments": list(comments)}], "announcements": [],
              "cannot_say": []}
    monkeypatch.setattr(server_reads, "RESULT_BUDGET", server_reads._size(result) - 2000)
    small = server_reads._fit_article_reactions(result)
    kept = small["deliveries"][0]["comments"]
    assert small["trimmed"] >= 2 and kept == comments[len(comments) - len(kept):] and len(kept) < 50


# ---------------------------------------------------------------- 中-1 の足し: サブプロセスは loopback だけ

def test_サブプロセスのththはloopback以外へ出ない_既定のenvで立てる(monkeypatch):
    """柵: `run_thth` の既定の env から外すと赤。"""
    seen = {}

    def spy(argv, **kwargs):
        seen.update(kwargs["env"])
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(conftest.subprocess, "run", spy)
    conftest.run_thth(["--version"])
    assert seen.get(httpsafe.TEST_LOOPBACK_ONLY_ENV) == "1"


def test_loopbackだけの印は本番では立たず_立てば外のホストを断る(monkeypatch):
    """柵: 印を見ないと赤。"""
    monkeypatch.delenv(httpsafe.TEST_LOOPBACK_ONLY_ENV, raising=False)
    httpsafe._test_loopback_only("https://b.hatena.ne.jp/entry/jsonlite/?url=x")      # 本番の既定は何もしない
    monkeypatch.setenv(httpsafe.TEST_LOOPBACK_ONLY_ENV, "1")
    with pytest.raises(httpsafe.EndpointRejected):
        httpsafe.build_opener().open("https://b.hatena.ne.jp/entry/jsonlite/?url=x", timeout=0.01)
    httpsafe._test_loopback_only("http://127.0.0.1:9/x")
    # 本番のコード（thth/・bin/）でこの印を立てる所は無い（httpsafe の定義だけ）。
    root = pathlib.Path(__file__).resolve().parent.parent
    hits = [p for d in ("thth", "bin") for p in (root / d).rglob("*") if p.is_file() and p.suffix in ("", ".py")
            and httpsafe.TEST_LOOPBACK_ONLY_ENV in p.read_text(encoding="utf-8", errors="ignore")]
    assert [p.name for p in hits] == ["httpsafe.py"]
    assert os.environ.get("THTH_TEST_LOOPBACK_ONLY") == "1"   # この試験の中で立てたものだけ
