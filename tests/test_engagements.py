"""絡みの台帳（`thth/engagements.py`・設計「自分の泉」§4・発注 T0-1）。

確かめるもの:
  (a) `append` → `records` で 1 行戻る。年月でファイルが分かれる。
  (b) 禁止鍵（`username`・`text`）や 16hex でない `author_key` を含む行は
      `EngagementError` で**ファイルが作られない**。
  (c) 公開の経路（`core.throw_once()`・偽 API）: `reply_to` 付きの原稿を出すと
      台帳に 1 行・無しなら 0 行・`reply_to_author_key` が front-matter にあれば
      行に写る。
  (d) `lint` が `reply_to_author_key: zzz` を error にし、正しい 16hex を通す。
"""
from __future__ import annotations

import os

import pytest

from tests.conftest import commit_and_push_path, make_queue_text, write_queue_file
from tests.test_fake_api import NOW, _adapter, fake_threads_server
from thth import accounts as accounts_mod
from thth import core
from thth import engagements as engagements_mod
from thth import lint as lint_mod


def _row(**over):
    row = {
        "schema": engagements_mod.SCHEMA,
        "post_id": "111",
        "reply_to": "222",
        "root_post": None,
        "author_key": None,
        "account": "nigamilab-threads",
        "medium": "threads",
        "topic": None,
        "form": None,
        "hour_band": "朝",
        "posted_at": "2026-09-09T08:00:00+09:00",
        "found_by": None,
    }
    row.update(over)
    return row


def _insert_front_matter_lines(text: str, extra: dict) -> str:
    """front-matter の閉じ `---` の手前に鍵を足す（`make_queue_text()` の
    `render_front_matter()` は `FM_ORDER` の鍵しか出さないので、`reply_to_root` 等の
    新しい任意鍵はこれで足す）。"""
    lines = text.split("\n")
    close_idx = lines.index("---", 1)
    insert = [f"{k}: {v}" for k, v in extra.items()]
    return "\n".join(lines[:close_idx] + insert + lines[close_idx:])


# ===================================================== (a) append → records


def test_a_appendしてrecordsで1行戻る(isolated_account_factory):
    account = isolated_account_factory()
    cfg = accounts_mod.load_account(account["name"])
    engagements_mod.append(cfg, account["name"], _row())
    rows = engagements_mod.records(cfg, account["name"])
    assert len(rows) == 1
    assert rows[0]["post_id"] == "111"
    assert rows[0]["schema"] == engagements_mod.SCHEMA
    assert "recorded_at" in rows[0]


def test_a_年月でファイルが分かれる(isolated_account_factory):
    account = isolated_account_factory()
    cfg = accounts_mod.load_account(account["name"])
    engagements_mod.append(cfg, account["name"],
                            _row(post_id="A1", posted_at="2026-08-20T08:00:00+09:00"))
    engagements_mod.append(cfg, account["name"],
                            _row(post_id="A2", posted_at="2026-09-09T08:00:00+09:00"))
    rows = engagements_mod.records(cfg, account["name"])
    assert {r["post_id"] for r in rows} == {"A1", "A2"}
    d = accounts_mod.data_dirs(cfg, account["name"])["engagements"]
    assert sorted(os.listdir(d)) == ["2026-08.ndjson", "2026-09.ndjson"]


# ===================================================== (b) 禁止鍵・author_key の形


def test_b_禁止鍵はEngagementErrorでファイルが作られない(isolated_account_factory):
    account = isolated_account_factory()
    cfg = accounts_mod.load_account(account["name"])
    d = accounts_mod.data_dirs(cfg, account["name"])["engagements"]

    with pytest.raises(engagements_mod.EngagementError):
        engagements_mod.append(cfg, account["name"], _row(username="alice"))
    assert not os.path.isdir(d) or os.listdir(d) == []

    with pytest.raises(engagements_mod.EngagementError):
        engagements_mod.append(cfg, account["name"], _row(text="本文だよ"))
    assert not os.path.isdir(d) or os.listdir(d) == []


def test_b_16hexでないauthor_keyはEngagementErrorでファイルが作られない(isolated_account_factory):
    account = isolated_account_factory()
    cfg = accounts_mod.load_account(account["name"])
    d = accounts_mod.data_dirs(cfg, account["name"])["engagements"]

    with pytest.raises(engagements_mod.EngagementError):
        engagements_mod.append(cfg, account["name"], _row(author_key="not-hex"))
    assert not os.path.isdir(d) or os.listdir(d) == []

    with pytest.raises(engagements_mod.EngagementError):
        engagements_mod.append(cfg, account["name"], _row(author_key="0123456789abcde"))  # 15 桁
    assert not os.path.isdir(d) or os.listdir(d) == []

    # 正しい 16hex は通る。
    engagements_mod.append(cfg, account["name"], _row(author_key="0123456789abcdef"))
    rows = engagements_mod.records(cfg, account["name"])
    assert rows[0]["author_key"] == "0123456789abcdef"


# ===================================================== (c) 公開の経路（core.throw_once）


def test_c_reply_to付きの原稿を出すと台帳に1行_author_keyも写る(isolated_account_factory):
    account = isolated_account_factory(production=True)
    text = make_queue_text(fm_overrides={"reply_to": "THIRDPARTY777", "topic": None})
    text = _insert_front_matter_lines(text, {"reply_to_author_key": "a" * 16})
    os.makedirs(account["queue_dir"], exist_ok=True)
    path = os.path.join(account["queue_dir"], "with-reply.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    commit_and_push_path(path, message="test: with-reply")

    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        def factory(_cfg, _token):
            return _adapter(base_url)

        result = core.throw_once(account["name"], production_flag=True,
                                  adapter_factory=factory, now=NOW)

    assert result.exit_code == 0 and result.action == "post", result

    cfg = accounts_mod.load_account(account["name"])
    rows = engagements_mod.records(cfg, account["name"])
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["reply_to"] == "THIRDPARTY777"
    assert row["post_id"] == result.post_id
    assert row["author_key"] == "a" * 16
    assert row["account"] == account["name"]
    assert row["medium"] == "threads"
    # 本文・username は 1 バイトも書かれていない。
    for forbidden in ("text", "body", "content", "username", "author", "handle"):
        assert forbidden not in row, row


def test_c_reply_to無しなら0行(isolated_account_factory):
    account = isolated_account_factory(production=True)
    write_queue_file(account["queue_dir"], "no-reply.md")

    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        def factory(_cfg, _token):
            return _adapter(base_url)

        result = core.throw_once(account["name"], production_flag=True,
                                  adapter_factory=factory, now=NOW)

    assert result.exit_code == 0 and result.action == "post", result
    cfg = accounts_mod.load_account(account["name"])
    assert engagements_mod.records(cfg, account["name"]) == []


# ===================================================== (d) lint の形式検査


def test_d_lintがreply_to_author_keyの形を検査する(isolated_account_factory, tmp_path):
    isolated_account_factory()  # THTH_ACCOUNTS_DIR の隔離だけ要る

    bad = make_queue_text(fm_overrides={"reply_to": "T1", "topic": None})
    bad = _insert_front_matter_lines(bad, {"reply_to_author_key": "zzz"})
    bad_path = tmp_path / "bad.md"
    bad_path.write_text(bad, encoding="utf-8")
    errors = lint_mod.lint_file(str(bad_path))
    assert any("reply_to_author_key" in e for e in errors), errors

    ok = make_queue_text(fm_overrides={"reply_to": "T1", "topic": None})
    ok = _insert_front_matter_lines(ok, {"reply_to_author_key": "0123456789abcdef"})
    ok_path = tmp_path / "ok.md"
    ok_path.write_text(ok, encoding="utf-8")
    errors_ok = lint_mod.lint_file(str(ok_path))
    assert not any("reply_to_author_key" in e for e in errors_ok), errors_ok


def test_d_lintがreply_to_rootとfound_byの形を検査する(isolated_account_factory, tmp_path):
    isolated_account_factory()

    bad = make_queue_text(fm_overrides={"reply_to": "T1", "topic": None})
    bad = _insert_front_matter_lines(bad, {"reply_to_root": "..",
                                            "found_by": "誰かのすいせん"})
    bad_path = tmp_path / "bad2.md"
    bad_path.write_text(bad, encoding="utf-8")
    errors = lint_mod.lint_file(str(bad_path))
    assert any("reply_to_root" in e for e in errors), errors
    assert any("found_by" in e for e in errors), errors

    ok = make_queue_text(fm_overrides={"reply_to": "T1", "topic": None})
    ok = _insert_front_matter_lines(ok, {"reply_to_root": "999", "found_by": "manual"})
    ok_path = tmp_path / "ok2.md"
    ok_path.write_text(ok, encoding="utf-8")
    errors_ok = lint_mod.lint_file(str(ok_path))
    assert not any("reply_to_root" in e or "found_by" in e for e in errors_ok), errors_ok
