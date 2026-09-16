"""`thth topics <account> --search <語>` が**絡みに行く先**を指せる（設計 v2 §4.4）。

引継ぎ_開発セッション_2026-09-15 §3-B: `--search` は観測の口として作ったので
出力に `post_id` が無く、「絡みに行く先」を指せなかった。ここで固定するもの:

  (a) 一覧に `post_id`・`permalink`・**投稿ごとの返信**が出る（`--json` は `posts[]`）
  (b) 返信の数を返さない媒体は画面 `—` ／ `--json` `null`（**0 と混ぜない**）
  (c) 同じ account の queue に `reply_to: <post_id>` があれば印
      （`posted` / `approved` / `draft` の 3 状態・`withdrawn` は印にしない）
  (d) 本文は画面の先頭 60 字だけ。**`post_id` も本文もディスクに 1 バイトも落ちない**
      （観測の棚 `topics.json` に post_id を落とさない・設計 v2 §2 の範囲を広げない）
  (e) `keyword_search` を持たない媒体は **1 行言って rc≠0**（黙って空にしない）

**本物の API は 1 つも叩かない**——`tests/helpers/fake_search_adapter.py` の偽の
媒体を `REGISTRY` に 1 行足すだけ（境界の証明でもある・§4.2）。
"""
from __future__ import annotations

import json
import os

import pytest

from tests.conftest import write_queue_file
from tests.helpers import fake_search_adapter as fake
from thth.adapters import base
from thth import adapters as adapters_mod
from thth import cli as cli_mod
from thth import threads_read_cli

MEDIUM = fake.MEDIUM
POST_IDS = [r["message_id"] for r in fake.ROWS_HAS_REPLIES]


@pytest.fixture
def 偽の検索媒体(monkeypatch):
    """`REGISTRY` に 1 行足すだけ。**core も棚も触らない。**"""
    monkeypatch.setitem(adapters_mod.REGISTRY, MEDIUM, fake.FakeSearchAdapter)
    fake.FakeSearchAdapter.calls = []
    return MEDIUM


@pytest.fixture
def account(偽の検索媒体, isolated_account_factory):
    return isolated_account_factory(media=MEDIUM)


def _rows(monkeypatch, rows):
    monkeypatch.setattr(fake.FakeSearchAdapter, "rows", rows)


def _run(capsys, args):
    rc = cli_mod.main(args)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _search(capsys, account, *extra):
    return _run(capsys, ["topics", account["name"], "--search", "お茶", *extra])


def _scan_for(root: str, needle: str) -> list:
    """`root` 配下の全ファイルを読んで `needle` を含むものを返す。"""
    hits = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(dirpath, name)
            try:
                with open(path, "rb") as f:
                    if needle.encode("utf-8") in f.read():
                        hits.append(path)
            except OSError:
                continue
    return hits


# ===================================================== (a) 欄が出る

def test_a_画面にpost_idとpermalinkと返信が出る(capsys, account):
    rc, out, err = _search(capsys, account)
    assert rc == 0, out + err
    assert "絡みに行く先" in out
    for post_id in POST_IDS:
        assert post_id in out, f"post_id が出ていない: {post_id}\n{out}"
    assert "https://fake/POST7QXA1" in out, out
    # `has_replies` しか返らない媒体なので **有 / 無**（数は作らない）。
    assert "返信 有" in out and "返信 無" in out, out
    # 従来の材料の行は残っている（観測の口を潰していない）。
    assert "投稿者の異なり数: 3" in out
    assert "記録するのは人" in out
    # **指図しない**（既存の作法）。
    assert "すべき" not in out
    assert "してください" not in out.replace("thth auth", "")


def test_a_jsonのpostsの形(capsys, account):
    rc, out, err = _search(capsys, account, "--json")
    assert rc == 0, out + err
    data = json.loads(out)
    # 従来の材料はそのまま（上に足しただけ）。
    assert data["n"] == 3 and data["search_type"] == "TOP"
    assert [p["post_id"] for p in data["posts"]] == POST_IDS
    first = data["posts"][0]
    assert first == {"post_id": "POST7QXA1", "permalink": "https://fake/POST7QXA1",
                     "timestamp": "2026-09-13T10:00:00+0000", "author": "alice",
                     "author_key": base.author_key(MEDIUM, "alice"),
                     "replies": None, "has_replies": True, "replied": None}
    # **本文は `--json` に入らない**（指す先を渡すのが仕事）。
    for post in data["posts"]:
        assert "text" not in post
    assert fake.TEXT_A not in out and fake.TEXT_B not in out
    assert data["replied_lookup"]["available"] is True
    assert data["replied_lookup"]["n"] == 0


def test_a_author_keyは16hexでauthorと対応する(capsys, account):
    """設計「自分の泉」§4・T0-3: LLM が `reply_to_author_key` に写せるように。"""
    rc, out, err = _search(capsys, account, "--json")
    assert rc == 0, out + err
    posts = json.loads(out)["posts"]
    assert len(posts) == 3
    for post in posts:
        author_key = post["author_key"]
        assert isinstance(author_key, str) and len(author_key) == 16
        int(author_key, 16)  # 16 進として読めること
        assert author_key == base.author_key(MEDIUM, post["author"])


def test_a_並び順は媒体が返した順のまま(capsys, account, monkeypatch):
    """TOP／`--recent` の順は媒体が決める。**道具は並べ替えない。**"""
    _rows(monkeypatch, list(reversed(fake.ROWS_HAS_REPLIES)))
    rc, out, _err = _search(capsys, account, "--json")
    assert rc == 0
    assert [p["post_id"] for p in json.loads(out)["posts"]] == list(reversed(POST_IDS))
    # `--recent` はそのままアダプタへ渡る。
    _search(capsys, account, "--recent", "--json")
    assert fake.FakeSearchAdapter.calls[-1][1] == "RECENT"


# ===================================================== (b) 数の無い媒体

def test_b_返信の数も真偽も返らない媒体はダッシュとnull(capsys, account, monkeypatch):
    """**`n/a` を 0 と混ぜない**（設計 v2 §4.4）。"""
    _rows(monkeypatch, fake.ROWS_SILENT)
    rc, out, _err = _search(capsys, account)
    assert rc == 0
    assert "返信 —" in out, out
    assert "返信 0" not in out, "**判らないものを 0 と出している**"
    rc, out, _err = _search(capsys, account, "--json")
    post, = json.loads(out)["posts"]
    assert post["replies"] is None and post["has_replies"] is None


def test_b_数を返す媒体はその数を出す(capsys, account, monkeypatch):
    _rows(monkeypatch, fake.ROWS_WITH_COUNT)
    rc, out, _err = _search(capsys, account)
    assert rc == 0 and "返信 12" in out, out
    rc, out, _err = _search(capsys, account, "--json")
    post, = json.loads(out)["posts"]
    assert post["replies"] == 12 and post["has_replies"] is True


def test_b_has_repliesの真偽を数に化けさせない():
    """`True` は `int` の仲間。**数の欄に 1 と出さない**（純粋関数の直試験）。"""
    assert threads_read_cli.reply_count({"has_replies": True}) is None
    assert threads_read_cli.reply_count({"replies": True}) is None
    assert threads_read_cli.reply_count({"replies": 0}) == 0
    assert threads_read_cli.reply_count({"replies": {"count": 4}}) == 4


# ===================================================== (c) 「もう返した」印

def _draft(account, name, *, reply_to, status):
    return write_queue_file(account["queue_dir"], name, commit=False,
                            fm_overrides={"status": status, "reply_to": reply_to,
                                          "approved_sha": None})


def test_c_queueのreply_toが3状態の印になる(capsys, account):
    _draft(account, "a.md", reply_to="POST7QXA1", status="posted")
    _draft(account, "b.md", reply_to="POST7QXA2", status="approved")
    _draft(account, "c.md", reply_to="POST7QXA3", status="draft")
    rc, out, _err = _search(capsys, account)
    assert rc == 0
    assert "[返信済]" in out and "[承認済]" in out and "[下書き]" in out, out
    rc, out, _err = _search(capsys, account, "--json")
    posts = {p["post_id"]: p["replied"] for p in json.loads(out)["posts"]}
    assert posts["POST7QXA1"] == {"status": "posted", "file": "a.md"}
    assert posts["POST7QXA2"] == {"status": "approved", "file": "b.md"}
    assert posts["POST7QXA3"] == {"status": "draft", "file": "c.md"}
    assert json.loads(out)["replied_lookup"] == {
        "available": True, "reason": None, "n": 3,
        "statuses": ["posted", "approved", "draft"]}


def test_c_同じ先に複数あれば強い方を出す(capsys, account):
    _draft(account, "a-draft.md", reply_to="POST7QXA1", status="draft")
    _draft(account, "b-posted.md", reply_to="POST7QXA1", status="posted")
    _draft(account, "c-approved.md", reply_to="POST7QXA1", status="approved")
    rc, out, _err = _search(capsys, account, "--json")
    assert rc == 0
    posts = {p["post_id"]: p["replied"] for p in json.loads(out)["posts"]}
    assert posts["POST7QXA1"] == {"status": "posted", "file": "b-posted.md"}


def test_c_withdrawnと他アカウントの原稿は印にしない(capsys, account):
    _draft(account, "a.md", reply_to="POST7QXA1", status="withdrawn")
    write_queue_file(account["queue_dir"], "b.md", commit=False,
                     fm_overrides={"status": "posted", "reply_to": "POST7QXA2",
                                   "account": "よその-account", "approved_sha": None})
    rc, out, _err = _search(capsys, account, "--json")
    assert rc == 0
    posts = {p["post_id"]: p["replied"] for p in json.loads(out)["posts"]}
    assert posts["POST7QXA1"] is None and posts["POST7QXA2"] is None
    assert json.loads(out)["replied_lookup"]["n"] == 0


def test_c_queueが読めなければ印を出さず読めなかったと言う(capsys, isolated_account_factory,
                                                     偽の検索媒体, tmp_path):
    """**印が無いことを「返していない」にしない**（設計 v2 §4.4）。"""
    acc = isolated_account_factory(media=MEDIUM, repo_dir=str(tmp_path / "ない repo"))
    rc, out, _err = _search(capsys, acc)
    # 検索そのものは通る（rc=0）が、印は出せないと言う。
    assert rc == 0, out
    assert "印: 出せません" in out and "queue が読めない" in out, out
    assert "[返信済]" not in out
    rc, out, _err = _search(capsys, acc, "--json")
    data = json.loads(out)
    assert data["replied_lookup"]["available"] is False
    assert data["replied_lookup"]["reason"]
    assert all(p["replied"] is None for p in data["posts"])


# ===================================================== (d) 本文と棚

def test_d_本文は60字で切れ棚にもディスクにも落ちない(capsys, account, thth_root):
    rc, out, _err = _search(capsys, account)
    assert rc == 0
    # 80 字の本文は 60 字＋`…` で切れる。
    assert "長" * 60 + "…" in out, out
    assert "長" * 61 not in out
    # **本文も post_id も 1 バイトも書かれていない**（棚 `topics.json` も含む）。
    for root in (thth_root, account["repo_dir"], account["accounts_dir"]):
        for needle in (fake.TEXT_A, fake.TEXT_B, *POST_IDS):
            assert _scan_for(root, needle) == [], \
                f"**検索の結果が保存されている**: {needle} @ {root}"


def test_d_json経路でも棚に何も落ちない(capsys, account, thth_root):
    rc, out, _err = _search(capsys, account, "--json")
    assert rc == 0
    for root in (thth_root, account["repo_dir"], account["accounts_dir"]):
        for needle in (fake.TEXT_A, *POST_IDS):
            assert _scan_for(root, needle) == []


# ===================================================== (e) 口の無い媒体

def test_e_検索の口を持たない媒体は1行言ってrc1(capsys, isolated_account_factory):
    """**黙って空にしない**（設計 v2 §4.4「媒体差」）。

    Bluesky・Mastodon は T2-1（設計「自分の泉」§2.3・§3・2026-09-16）で
    `keyword_search` を持ったので、ここでは**知らない媒体**
    （`test_adapter_boundary.py` と同じ例）で「口が無い」を確かめる。
    """
    acc = isolated_account_factory(media="carrier-pigeon")
    rc, out, err = _run(capsys, ["topics", acc["name"], "--search", "お茶"])
    assert rc == 1, out + err
    assert "carrier-pigeon" in err and "未対応" in err, err
    assert "keyword_search" in err
    assert out == "", "**0 件の一覧を出している**"


def test_e_json経路でも黙って空の一覧にしない(capsys, isolated_account_factory):
    acc = isolated_account_factory(media="carrier-pigeon")
    rc, out, err = _run(capsys, ["topics", acc["name"], "--search", "お茶", "--json"])
    assert rc == 1
    assert "未対応" in err
    assert "posts" not in out
