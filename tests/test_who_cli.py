"""`thth who` / MCP `who_is_this`（T3-1・設計「自分の泉」§2.4）。

芯: この仮名（`author_key`）と自分のアカウントが**何度・いつ・どんな反応で**
接触したかを返す。**発言内容は 1 文字も持たない。** 人物像は作らない。

確かめるもの（T3-1 発注書のとおり）:
  - 絡みの台帳 2 行（同じ仮名・うち 1 本は measured に 24 の刻みあり）＋
    返信台帳に他者の返信 2 行（1 本はその仮名の username・1 本は別人）→
    `met==3`・`threads` の順序と `role`・`reaction` の `covered` と `null`・
    別人が混ざらない
  - `@username` で同じ結果と `resolved_from: "username"`
  - `--project` で 2 account の節
  - `--profile` を Threads の偽サーバで（公開 field が出る・`data/` 不変）
  - `PermissionMissing` は `cannot_say`
  - `own is False` の絞り（自分の返信を「相手→自分」に混ぜない）
  - 出力・runs に `text`・`username` が無い
"""
from __future__ import annotations

import json
import os

import pytest

from tests.conftest import run_thth
from tests.test_threads_read_permissions import PROFILE, _server
from thth import accounts as accounts_mod
from thth import engagements as engagements_mod
from thth import jst
from thth import runs as runs_mod
from thth import who_cli
from thth.adapters import base as adapter_base

BOB_KEY = adapter_base.author_key("threads", "bob")
CAROL_KEY = adapter_base.author_key("threads", "carol")


def _insight_path(account, post_id: str) -> str:
    # **`accounts.data_dirs()` を通す**（`account["repo_dir"]` 直組みだと、
    # `repo_dir` が `REPO_NONE`（`repos/_none`）の account では実際の置き場
    # （`state/<account>/data/sns/…`）と食い違う）。
    cfg = accounts_mod.load_account(account["name"])
    d = accounts_mod.data_dirs(cfg, account["name"])["insights_posts"]
    return os.path.join(d, f"{post_id}.ndjson")


def _write_ndjson(path: str, rows: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_reply_row(account_name: str, post_id: str, row: dict) -> None:
    """返信台帳に 1 行を直に書く（`replies.py` は読むだけの口しか持たないので、
    `collect.py` の書き先と同じ置き場に直接書く。テストの下ごしらえ専用）。"""
    cfg = accounts_mod.load_account(account_name)
    replies_dir = accounts_mod.data_dirs(cfg, account_name)["replies"]
    os.makedirs(replies_dir, exist_ok=True)
    path = os.path.join(replies_dir, f"{post_id}.ndjson")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _seed(account):
    """絡みの台帳 2 行（同じ仮名 BOB_KEY・P1 は 24h の刻みあり・P2 は無し）＋
    返信台帳に他者の返信 2 行（bob 1 本・carol 1 本＝別人）。"""
    cfg = accounts_mod.load_account(account["name"])
    engagements_mod.append(cfg, account["name"], {
        "post_id": "P1", "reply_to": "R1", "root_post": "ROOT1",
        "author_key": BOB_KEY, "account": account["name"], "medium": "threads",
        "topic": None, "kind": None, "hour_band": "朝",
        "posted_at": "2026-09-01T08:00:00+09:00", "found_by": "manual"})
    engagements_mod.append(cfg, account["name"], {
        "post_id": "P2", "reply_to": "R2", "root_post": "ROOT2",
        "author_key": BOB_KEY, "account": account["name"], "medium": "threads",
        "topic": None, "kind": None, "hour_band": "夕",
        "posted_at": "2026-09-02T18:00:00+09:00", "found_by": "manual"})
    _write_ndjson(_insight_path(account, "P1"), [
        {"post_id": "P1", "account": account["name"], "topic": None,
         "posted_at": "2026-09-01T08:00:00+09:00",
         "collected_at": "2026-09-02T08:10:00+09:00", "age_hours": 24.1,
         "marks": [24], "metrics": {"views": 40, "likes": 3, "replies": 1}}])
    # P2: 実測なし（まだ 1 度も採取されていない）。

    _write_reply_row(account["name"], "SELFPOST1", {
        "kind": "reply", "post_id": "SELFPOST1", "message_id": "M-BOB-1",
        "username": "bob", "timestamp": "2026-09-03T10:00:00+09:00",
        "text": "内緒の本文その一・ZQXJ9B", "collected_at": "2026-09-03T10:05:00+09:00"})
    _write_reply_row(account["name"], "SELFPOST1", {
        "kind": "reply", "post_id": "SELFPOST1", "message_id": "M-CAROL-1",
        "username": "carol", "timestamp": "2026-09-03T11:00:00+09:00",
        "text": "内緒の本文その二・ZQXJ9C", "collected_at": "2026-09-03T11:05:00+09:00"})
    return cfg


def _data_snapshot(account_cfg: dict, account_name: str) -> dict:
    snap: dict = {}
    for d in accounts_mod.data_dirs(account_cfg, account_name).values():
        if not os.path.isdir(d):
            continue
        for root, _dirs, names in os.walk(d):
            for name in names:
                path = os.path.join(root, name)
                snap[path] = os.path.getsize(path)
    return snap


# ===================================================== met / 順序 / 別人が混ざらない


def test_met3_順序とrole_covered(isolated_account_factory):
    account = isolated_account_factory(media="threads", handle="nigamilab")
    _seed(account)

    result = who_cli.answer(account_name=account["name"], author_key=BOB_KEY)
    assert result["author_key"] == BOB_KEY
    assert result["met"] == 3
    assert result["first"] == "2026-09-01T08:00:00+09:00"
    assert result["last"] == "2026-09-03T10:00:00+09:00"

    roles = [t["role"] for t in result["threads"]]
    assert roles == ["i_replied_to_them", "i_replied_to_them", "they_replied_to_me"]

    p1, p2, reply = result["threads"]
    assert p1["post_id"] == "P1" and p1["reaction"]["covered"] is True
    assert p1["reaction"] == {"views_24h": 40, "likes_24h": 3,
                              "replies_back_24h": 1, "covered": True}
    assert p2["post_id"] == "P2" and p2["reaction"]["covered"] is False
    assert p2["reaction"] == {"views_24h": None, "likes_24h": None,
                              "replies_back_24h": None, "covered": False}
    assert reply["message_id"] == "M-BOB-1"
    assert reply["reaction"] is None
    assert reply["root"] == "SELFPOST1"

    # 別人（carol）が混ざらない。
    assert all(t.get("message_id") != "M-CAROL-1" for t in result["threads"])

    assert any("24h の刻みが未採取: 1 本" in c for c in result["cannot_say"])
    assert result["profile"] is None


def test_別の仮名で問えばcarolの1本だけ(isolated_account_factory):
    account = isolated_account_factory(media="threads", handle="nigamilab")
    _seed(account)
    result = who_cli.answer(account_name=account["name"], author_key=CAROL_KEY)
    assert result["met"] == 1
    assert result["threads"][0]["message_id"] == "M-CAROL-1"


# ===================================================== @username


def test_usernameでも同じ結果でresolved_fromがusername(isolated_account_factory):
    account = isolated_account_factory(media="threads", handle="nigamilab")
    _seed(account)
    by_key = who_cli.answer(account_name=account["name"], author_key=BOB_KEY)
    by_username = who_cli.answer(account_name=account["name"], username="bob")
    assert by_username["author_key"] == BOB_KEY
    assert by_username["threads"] == by_key["threads"]
    assert by_username["provenance"]["resolved_from"] == "username"
    assert by_key["provenance"]["resolved_from"] == "author_key"

    # CLI でも `@username` を通す。
    r = run_thth(["who", account["name"], "@bob", "--json"])
    assert r.returncode == 0, r.stdout + r.stderr
    cli_result = json.loads(r.stdout)
    assert cli_result["provenance"]["resolved_from"] == "username"
    assert cli_result["met"] == 3


# ===================================================== own is False の絞り


def test_own_is_falseの行だけを相手からの返信に数える(isolated_account_factory):
    account = isolated_account_factory(media="threads", handle="nigamilab")
    cfg = accounts_mod.load_account(account["name"])
    self_key = adapter_base.author_key("threads", "nigamilab")
    # 自分自身（account の handle と同じ username）からの「返信」が台帳に
    # 紛れ込んでいても、`own is False` の絞りで除かれる——**自分の返信を
    # 「相手→自分」に混ぜない**（発注 T3-1 の変異対象）。
    _write_reply_row(account["name"], "SELFPOST2", {
        "kind": "reply", "post_id": "SELFPOST2", "message_id": "M-SELF-1",
        "username": "nigamilab", "timestamp": "2026-09-04T09:00:00+09:00",
        "text": "自分の返信"})
    result = who_cli.answer(account_name=account["name"], author_key=self_key)
    assert result["met"] == 0, result["threads"]


# ===================================================== --project


def test_projectで2accountの節(isolated_account_factory, tmp_path):
    # **repo_dir を account ごとに分ける**（`repos/_none` 基名＝`accounts.
    # REPO_NONE`）——既定の `isolated_account_factory` は 1 本の `nigamilab_repo`
    # を全 account で共有するので、それだと `data_dirs()` の置き場（＝
    # 絡みの台帳・返信台帳の場所）まで共有してしまい、a2 の「まだ何も無い」を
    # 試せない（`tests/test_collect_sent.py` 等と同じ隔離の作法）。
    a1 = isolated_account_factory(
        "kopicha-who-1", media="threads", handle="nigamilab",
        project="kopicha-who", repo_dir=str(tmp_path / "repos" / "_none"))
    a2 = isolated_account_factory(
        "kopicha-who-2", media="threads", handle="nigamilab2",
        project="kopicha-who", repo_dir=str(tmp_path / "repos" / "_none"))
    _seed(a1)

    result = who_cli.answer(project="kopicha-who", author_key=BOB_KEY)
    assert result["project"] == "kopicha-who"
    assert set(result["by_account"]) == {a1["name"], a2["name"]}
    assert result["by_account"][a1["name"]]["met"] == 3
    assert result["by_account"][a2["name"]]["met"] == 0


# ===================================================== 禁止鍵・runs・data/ 不変


def _collect_dict_keys(obj, out: set) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            _collect_dict_keys(v, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_dict_keys(item, out)


def test_出力にusernameとtextが無い(isolated_account_factory):
    account = isolated_account_factory(media="threads", handle="nigamilab")
    _seed(account)
    result = who_cli.answer(account_name=account["name"], author_key=BOB_KEY)
    keys: set = set()
    _collect_dict_keys(result, keys)
    assert "username" not in keys and "text" not in keys
    dumped = json.dumps(result, ensure_ascii=False)
    assert "内緒の本文その一・ZQXJ9B" not in dumped
    assert "内緒の本文その二・ZQXJ9C" not in dumped
    assert "\"username\"" not in dumped


def test_runsにusernameが無い(isolated_account_factory):
    account = isolated_account_factory(media="threads", handle="nigamilab")
    _seed(account)
    who_cli.answer(account_name=account["name"], username="bob")

    state_dir = accounts_mod.state_dir_for(account["name"])
    rows = [row for row in runs_mod.read_runs(state_dir) if row.get("action") == "who_is_this"]
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["account"] == account["name"]
    assert row["author_key"] == BOB_KEY
    assert row["met"] == 3
    assert row["profile_fetched"] is False
    assert row["status"] == "ok"
    hit = sorted(engagements_mod.FORBIDDEN_KEYS & set(row.keys()))
    assert not hit, hit
    assert "username" not in row


# ===================================================== --profile（Threads）


def _write_threads_token(path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"access_token": "FAKE-SECRET", "user_id": "999999",
                  "username": "nigamilab", "scopes": None,
                  "obtained_at": "2026-09-16T09:00:00+09:00"}, f)


def test_profileはthreadsの偽サーバで公開fieldが出てdata不変(isolated_account_factory, tmp_path):
    token_path = str(tmp_path / "threads.token")
    _write_threads_token(token_path)
    account = isolated_account_factory(media="threads", handle="nigamilab",
                                       token=token_path)
    _seed(account)
    cfg = accounts_mod.load_account(account["name"])
    before = _data_snapshot(cfg, account["name"])

    with _server() as (base_url, _requests):
        # 向き先は環境変数 `THTH_THREADS_BASE_URL`（`ThreadsAdapter.from_account()`
        # の既定・他のテストと同じ流儀）。
        os.environ["THTH_THREADS_BASE_URL"] = base_url
        try:
            result = who_cli.answer(account_name=account["name"], username="threads",
                                    profile=True)
        finally:
            del os.environ["THTH_THREADS_BASE_URL"]

    assert result["profile"] is not None
    assert result["profile"]["username"] == PROFILE["username"]
    assert result["profile"]["biography"] == PROFILE["biography"]

    after = _data_snapshot(cfg, account["name"])
    assert after == before


def test_profileのPermissionMissingはcannot_sayに(isolated_account_factory, tmp_path):
    token_path = str(tmp_path / "threads.token")
    _write_threads_token(token_path)
    account = isolated_account_factory(media="threads", handle="nigamilab",
                                       token=token_path)
    _seed(account)

    with _server({"profile_lookup": "permission"}) as (base_url, _requests):
        os.environ["THTH_THREADS_BASE_URL"] = base_url
        try:
            result = who_cli.answer(account_name=account["name"], username="threads",
                                    profile=True)
        finally:
            del os.environ["THTH_THREADS_BASE_URL"]

    assert result["profile"] is None
    assert any("threads_profile_discovery" in c for c in result["cannot_say"]), \
        result["cannot_say"]


# ===================================================== 問いの受け取り拒否


def test_author_keyもusernameも無ければ断る(isolated_account_factory):
    account = isolated_account_factory()
    with pytest.raises(who_cli.WhoError):
        who_cli.answer(account_name=account["name"])


def test_accountとprojectを両方は断る():
    with pytest.raises(who_cli.WhoError):
        who_cli.answer(account_name="a", project="p", author_key=BOB_KEY)
