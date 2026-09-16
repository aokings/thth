"""読む口 4 つ（`thread`・`where`・`who`・`threads_read_cli` の口）が、adapter の
素の `RuntimeError` で traceback にならない（T9-2・照合「X Developer Agreement
と自分の泉」検収 5）。

**事実**: `thth/adapters/base.py` の `AdapterError` は `RuntimeError` の子。
Bluesky（`bluesky.py` の `_request` 周り・`session()` の 367・374 行、`_xrpc()` の
260・262 行）は素の `RuntimeError` を投げる箇所がある。`thth/thread_read.py`・
`thth/where_cli.py`・`thth/who_cli.py`・`thth/threads_read_cli.py::_run` はどれも
`AdapterError` と `PermissionMissing` だけを捕まえていたので、素の `RuntimeError`
は traceback になっていた。

ここでは実際の Bluesky・Threads の通信は起こさず、`adapters.make_adapter()` を
差し替えた偽アダプタで「adapter が素の `RuntimeError` を投げたら」を作る——
4 つの口それぞれの捕捉の**位置**（`thread_read.cmd_thread`・
`where_cli._account_node` の語ごとの try・`who_cli._profile_for`・
`threads_read_cli._run`）を直に確かめる。

固定するのは:
  - `thread`・`threads_read_cli`（`topics --search`）: rc=1・`--json` は
    `{"error", "account"}`・**adapter の例外の型は変えていない**ので
    `AdapterError` の枝はそのまま通る。
  - `where`・`who`: その account（語・プロフィール）の `cannot_say` に入り、
    他の語・他の account は続く（`AdapterError` と同じ既存の筋のまま）。
"""
from __future__ import annotations

import argparse
import json

import pytest

from thth import accounts as accounts_mod
from thth import adapters as adapters_mod
from thth import thread_read as thread_read_mod
from thth import threads_read_cli as threads_read_cli_mod
from thth import where_cli as where_cli_mod
from thth import who_cli as who_cli_mod

BARE_MESSAGE = "素の RuntimeError（fake・T9-2・bluesky._request 相当）"


class _BareRuntimeErrorAdapter:
    """能力だけ答え、実際に叩かれたら**`AdapterError` ではない**素の
    `RuntimeError` を投げる偽アダプタ。Bluesky の `_request`／`session()` が
    本番で投げる形を模す。"""

    def capabilities(self):
        return {"thread_read", "keyword_search", "profile_lookup", "mentions"}

    def fetch_post(self, post_id):
        raise RuntimeError(BARE_MESSAGE)

    def conversation(self, post_id, *, since=None):
        raise RuntimeError(BARE_MESSAGE)

    def keyword_search(self, word, *, search_type, limit):
        raise RuntimeError(BARE_MESSAGE)

    def profile_lookup(self, username):
        raise RuntimeError(BARE_MESSAGE)

    def mentions(self, *, since=None):
        raise RuntimeError(BARE_MESSAGE)

    def granted_scopes(self):
        return []


def _write_token(path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"access_token": "FAKE-SECRET", "user_id": "999999",
                  "username": "nigamilab", "scopes": None,
                  "obtained_at": "2026-09-16T09:00:00+09:00"}, f)


@pytest.fixture
def bare_runtimeerror_account(tmp_path, isolated_account_factory, monkeypatch):
    """Threads 台帳（4 つの capability を全部持つ実クラスの検査は通す）だが、
    `adapters.make_adapter()` を差し替えて、実際に返す adapter は素の
    `RuntimeError` しか投げない偽物にする。"""
    token_path = str(tmp_path / "a.token")
    _write_token(token_path)
    account = isolated_account_factory(token=token_path, handle="nigamilab")
    monkeypatch.setattr(adapters_mod, "make_adapter",
                        lambda cfg, token: _BareRuntimeErrorAdapter())
    return account


# ---------------------------------------------------------------- thread_read

def test_thread読む口はrc1でtracebackにならない(bare_runtimeerror_account, capsys):
    args = argparse.Namespace(account=bare_runtimeerror_account["name"], post_id="POST1",
                              since=None, max_messages=thread_read_mod.DEFAULT_MAX_MESSAGES,
                              json=True)
    rc = thread_read_mod.cmd_thread(args)
    out = capsys.readouterr()

    assert rc == 1
    assert "Traceback" not in out.err
    payload = json.loads(out.out)
    assert set(payload) == {"error", "account"}
    assert payload["account"] == bare_runtimeerror_account["name"]
    assert BARE_MESSAGE in payload["error"]


# ------------------------------------------------------------ threads_read_cli

def test_threads_read_cliの口はrc1でtracebackにならない(bare_runtimeerror_account, capsys):
    args = argparse.Namespace(account=bare_runtimeerror_account["name"], search="語",
                              recent=False, limit=25, json=True)
    rc = threads_read_cli_mod.cmd_topics_search(args)
    out = capsys.readouterr()

    assert rc == 1
    assert "Traceback" not in out.err
    payload = json.loads(out.out)
    assert set(payload) == {"error", "account"}
    assert payload["account"] == bare_runtimeerror_account["name"]
    assert BARE_MESSAGE in payload["error"]


# --------------------------------------------------------------------- where

def test_where読む口はcannot_sayに入り他の語は続く(bare_runtimeerror_account, capsys):
    args = argparse.Namespace(project=None,
                              targets=[bare_runtimeerror_account["name"], "苦い", "コーヒー"],
                              recent=False, limit=where_cli_mod.DEFAULT_LIMIT, json=True)
    rc = where_cli_mod.cmd_where(args)
    out = capsys.readouterr()

    assert rc == 0, out.err  # `AdapterError` と同じ筋（account 単位の失敗にしない）
    assert "Traceback" not in out.err
    result = json.loads(out.out)
    node = result["by_account"][bare_runtimeerror_account["name"]]
    # 2 語とも by_word には入らず、node の cannot_say に落ちる（PermissionMissing/
    # AdapterError と同じ扱い・T9-2 で RuntimeError も同じ列に並んだ）。
    assert node["by_word"] == {}
    assert any("苦い" in line and BARE_MESSAGE in line for line in node["cannot_say"])
    assert any("コーヒー" in line and BARE_MESSAGE in line for line in node["cannot_say"])


# ---------------------------------------------------------------------- who

def test_whoの読む口はcannot_sayに入る(bare_runtimeerror_account, capsys):
    args = argparse.Namespace(project=None,
                              targets=[bare_runtimeerror_account["name"], "@bob"],
                              profile=True, json=True)
    rc = who_cli_mod.cmd_who(args)
    out = capsys.readouterr()

    assert rc == 0, out.err
    assert "Traceback" not in out.err
    result = json.loads(out.out)
    assert result["profile"] is None
    assert any(BARE_MESSAGE in line for line in result["cannot_say"])


# ----------------------------------------------------- adapter の例外の型は不変

def test_AdapterErrorの枝は素のRuntimeErrorに食われない():
    """`except AdapterError` が先・`except RuntimeError` が後——`AdapterError`
    は `RuntimeError` の子なので、順序を間違えると `AdapterError` の枝が
    死んでいてもテストは気づかない。ここは 4 箇所のソースを直に読んで、
    `AdapterError` の except が `RuntimeError` の except より**前**にあることを
    確かめる（読み違いで壊す変異にも強くする）。"""
    import inspect

    for mod, func_name in (
        (thread_read_mod, "cmd_thread"),
        (threads_read_cli_mod, "_run"),
        (where_cli_mod, "_account_node"),
        (who_cli_mod, "_profile_for"),
    ):
        src = inspect.getsource(getattr(mod, func_name))
        adapter_error_at = src.index("except adapter_base.AdapterError")
        runtime_error_at = src.index("except RuntimeError")
        assert adapter_error_at < runtime_error_at, (
            f"{mod.__name__}.{func_name}: AdapterError の except が "
            f"RuntimeError より後にある——AdapterError の枝が死ぬ")
