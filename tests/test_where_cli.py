"""`thth where` / MCP `where_to_appear`（T2-2・設計「自分の泉」§2.3・§2.6）。

芯: 検索の一覧に自分の履歴を**重ねて並べる**。account ごと（＝媒体ごと）の
節を並べるだけ——**account をまたぐ集計は一切作らない**（トップレベルに
`n` を置かない）。

確かめるもの（T2-2 発注書のとおり）:
  - 偽サーバ 2 媒体（Threads・Bluesky）を同じ project に置き、2 語で呼ぶ
    → `by_account` に 2 節・`by_word` に 2 語・`posts` に `author_key`
  - 絡みの台帳に 1 行置くと `my_history.n==1` と `you_and_them` に出る
  - Threads を `PermissionMissing` にすると Threads の節だけ `cannot_say`
    で Bluesky は出る
  - トップレベルに数が無い・`data/` 不変・runs に本文が無い
  - `--project` で読めない台帳が 1 本混ざっていても他が出る
"""
from __future__ import annotations

import json
import os

import pytest

from tests.conftest import run_thth
from tests.test_bluesky_adapter import APP_PASSWORD, DID, HANDLE, _post_view, fake_bluesky
from tests.test_threads_read_permissions import SEARCH_ROWS, SEARCH_TEXT_A, SEARCH_TEXT_B, _server
from thth import accounts as accounts_mod
from thth import engagements as engagements_mod
from thth import jst
from thth import runs as runs_mod
from thth.adapters import base as adapter_base

PROJECT = "kopicha"
ALICE_KEY = adapter_base.author_key("threads", "alice")

BSKY_BOB_DID = "did:plc:wherebob"
BSKY_BOB_HANDLE = "wherebob.bsky.social"
BSKY_SEARCH_ROWS = [
    _post_view(f"at://{BSKY_BOB_DID}/app.bsky.feed.post/w1", "bafyw1",
              handle=BSKY_BOB_HANDLE, did=BSKY_BOB_DID, text="苦いコーヒーの話",
              created_at="2026-09-16T04:00:00.000Z", counts={"replyCount": 1}),
]


def _write_threads_token(path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"access_token": "FAKE-SECRET", "user_id": "999999",
                  "username": "nigamilab", "scopes": None,
                  "obtained_at": "2026-09-16T09:00:00+09:00"}, f)


def _write_bluesky_token(path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"identifier": HANDLE, "app_password": APP_PASSWORD,
                  "did": DID, "handle": HANDLE, "no_expiry": True,
                  "user_id": DID, "username": HANDLE,
                  "obtained_at": "2026-09-16T09:00:00+09:00"}, f)


@pytest.fixture
def two_media_project(tmp_path, isolated_account_factory):
    """同じ project（`kopicha`）に Threads・Bluesky の account を 1 本ずつ置く。"""
    threads_token = str(tmp_path / "threads.token")
    _write_threads_token(threads_token)
    threads_acc = isolated_account_factory(
        "kopicha-threads", media="threads", handle="nigamilab",
        project=PROJECT, token=threads_token, production=False)

    with fake_bluesky(search=BSKY_SEARCH_ROWS) as service:
        bluesky_token = str(tmp_path / "bluesky.token")
        _write_bluesky_token(bluesky_token)
        bluesky_acc = isolated_account_factory(
            "kopicha-bluesky", media="bluesky", handle=HANDLE,
            service=str(service), project=PROJECT, token=bluesky_token,
            production=False)
        yield threads_acc, bluesky_acc


def _data_snapshot(account_cfg: dict, account_name: str) -> dict:
    """`accounts.data_dirs()` の全 dir のファイル一覧とサイズ（規約 (a) の検査用・
    `test_thread_read.py::_data_snapshot` と同じ）。"""
    snap: dict = {}
    for d in accounts_mod.data_dirs(account_cfg, account_name).values():
        if not os.path.isdir(d):
            continue
        for root, _dirs, names in os.walk(d):
            for name in names:
                path = os.path.join(root, name)
                snap[path] = os.path.getsize(path)
    return snap


def test_2媒体をproject単位で並べる(two_media_project):
    threads_acc, bluesky_acc = two_media_project
    threads_cfg = accounts_mod.load_account(threads_acc["name"])
    # 絡みの台帳に 1 行（Threads・topic=コーヒー・alice への返信）。**where の
    # 呼び出しより前に置く**——ここは試験の下ごしらえで、`data/` 不変の検査は
    # 「`where` を呼ぶ前後で変わらないか」を見るためのもの。
    engagements_mod.append(threads_cfg, threads_acc["name"], {
        "post_id": "MYREPLY1", "reply_to": "S1", "root_post": "S1",
        "author_key": ALICE_KEY, "account": threads_acc["name"],
        "medium": "threads", "topic": "コーヒー", "kind": None, "hour_band": "朝",
        "posted_at": jst.iso(jst.now_jst()), "found_by": "manual",
    })

    before = {
        threads_acc["name"]: _data_snapshot(threads_cfg, threads_acc["name"]),
        bluesky_acc["name"]: _data_snapshot(
            accounts_mod.load_account(bluesky_acc["name"]), bluesky_acc["name"]),
    }

    with _server() as (base_url, _requests):
        r = run_thth(["where", "--project", PROJECT, "コーヒー", "苦い", "--json"],
                     env={"THTH_THREADS_BASE_URL": base_url})
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)

    # **トップレベルに数が無い**（設計「自分の泉」§2.6・T2-2 規約）。
    assert "n" not in result
    assert set(result.keys()) == {"account", "project", "words", "by_account",
                                  "cannot_say", "provenance"}

    by_account = result["by_account"]
    assert set(by_account) == {threads_acc["name"], bluesky_acc["name"]}

    threads_node = by_account[threads_acc["name"]]
    assert threads_node["medium"] == "threads"
    assert set(threads_node["by_word"]) == {"コーヒー", "苦い"}

    bluesky_node = by_account[bluesky_acc["name"]]
    assert bluesky_node["medium"] == "bluesky"
    assert set(bluesky_node["by_word"]) == {"コーヒー", "苦い"}

    # posts に author_key。
    t_posts = threads_node["by_word"]["コーヒー"]["posts"]
    assert t_posts and all(p["author_key"] for p in t_posts)
    b_posts = bluesky_node["by_word"]["コーヒー"]["posts"]
    assert b_posts and all(p["author_key"] for p in b_posts)
    # 本文は 1 行プレビューだけ（`preview` 鍵・`text` 鍵は無い）。
    assert all("preview" in p and "text" not in p for p in t_posts)

    # 絡みの台帳の 1 行が my_history と you_and_them に出る。
    coffee = threads_node["by_word"]["コーヒー"]
    assert coffee["my_history"]["n"] == 1
    assert coffee["my_history"]["reacted"] == 0
    bitter = threads_node["by_word"]["苦い"]
    assert bitter["my_history"]["n"] == 0
    assert threads_node["you_and_them"].get(ALICE_KEY, {}).get("met") == 1

    # **account をまたぐ集計を作らない**——各節の中にしか数が無い。
    assert "n" not in threads_node and "n" not in bluesky_node

    # **data/ 不変**。
    after_threads = _data_snapshot(threads_cfg, threads_acc["name"])
    assert after_threads == before[threads_acc["name"]]
    after_bluesky = _data_snapshot(
        accounts_mod.load_account(bluesky_acc["name"]), bluesky_acc["name"])
    assert after_bluesky == before[bluesky_acc["name"]]

    # runs に本文が無い（account ごとに 1 行）。
    for acc in (threads_acc, bluesky_acc):
        state_dir = accounts_mod.state_dir_for(acc["name"])
        rows = [row for row in runs_mod.read_runs(state_dir)
               if row.get("action") == "where_to_appear"]
        assert len(rows) == 1, rows
        row = rows[0]
        assert row["account"] == acc["name"]
        assert row["words"] == ["コーヒー", "苦い"]
        assert row["status"] == "ok"
        assert row["error"] is None
        hit = sorted(engagements_mod.FORBIDDEN_KEYS & set(row.keys()))
        assert not hit, hit
        dumped = json.dumps(row, ensure_ascii=False)
        assert SEARCH_TEXT_A not in dumped and SEARCH_TEXT_B not in dumped
        assert "苦いコーヒーの話" not in dumped


def test_Threadsの権限が無くてもBlueskyは出る(two_media_project):
    threads_acc, bluesky_acc = two_media_project
    with _server({"keyword_search": "permission"}) as (base_url, _requests):
        r = run_thth(["where", "--project", PROJECT, "コーヒー", "苦い", "--json"],
                     env={"THTH_THREADS_BASE_URL": base_url})
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)

    threads_node = result["by_account"][threads_acc["name"]]
    # **PermissionMissing の語を空の posts:[] に化かさない**——語そのものが
    # `by_word` に無く、`cannot_say` に出る（変異テストの対象）。
    assert threads_node["by_word"] == {}
    assert any("コーヒー" in line for line in threads_node["cannot_say"])
    assert any("苦い" in line for line in threads_node["cannot_say"])

    bluesky_node = result["by_account"][bluesky_acc["name"]]
    assert set(bluesky_node["by_word"]) == {"コーヒー", "苦い"}

    # runs にも Threads は status=ok のまま（口自体は叩けている・語ごとの
    # 失敗は cannot_say の役目で、runs の 1 行は account 単位）。
    state_dir = accounts_mod.state_dir_for(threads_acc["name"])
    rows = [row for row in runs_mod.read_runs(state_dir)
           if row.get("action") == "where_to_appear"]
    assert rows[-1]["status"] == "ok"
    assert rows[-1]["n"] == 0


def test_単一accountでも同じ形(two_media_project):
    threads_acc, _bluesky_acc = two_media_project
    with _server() as (base_url, _requests):
        r = run_thth(["where", threads_acc["name"], "コーヒー", "--json"],
                     env={"THTH_THREADS_BASE_URL": base_url})
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)
    assert result["account"] == threads_acc["name"]
    assert result["project"] is None
    assert set(result["by_account"]) == {threads_acc["name"]}


def test_projectで読めない台帳が1本混ざっていても他が出る(two_media_project):
    threads_acc, bluesky_acc = two_media_project
    broken_path = os.path.join(threads_acc["accounts_dir"], "kopicha-broken.json")
    with open(broken_path, "w", encoding="utf-8") as f:
        f.write("{not valid json")

    with _server() as (base_url, _requests):
        r = run_thth(["where", "--project", PROJECT, "コーヒー", "--json"],
                     env={"THTH_THREADS_BASE_URL": base_url})
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)
    assert set(result["by_account"]) == {threads_acc["name"], bluesky_acc["name"]}
    assert any("kopicha-broken" in line for line in result["cannot_say"])


def test_語が0または6個は断る(two_media_project):
    threads_acc, _bluesky_acc = two_media_project
    r = run_thth(["where", threads_acc["name"]])
    assert r.returncode == 2, r.stdout + r.stderr

    many = ["a", "b", "c", "d", "e", "f"]
    r = run_thth(["where", threads_acc["name"], *many])
    assert r.returncode == 2, r.stdout + r.stderr


def test_accountとprojectを両方は断る(two_media_project):
    from thth import where_cli
    with pytest.raises(where_cli.WhereError):
        where_cli.answer(account_name="a", project="p", words=["x"])


def test_accountもprojectも無ければ断る():
    from thth import where_cli
    with pytest.raises(where_cli.WhereError):
        where_cli.answer(words=["x"])
