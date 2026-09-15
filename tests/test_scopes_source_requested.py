"""`scopes_source: "requested"` を「付与済み」として返さない（発注 A-3・
セキュリティ監査 2026-09-16・P3-1）。

**破れていたもの**: `ThreadsAdapter.from_account()` は `.token` の `scopes`
だけを渡して `scopes_source` を捨てていた。`thth auth`（`thth/oauth.py::
run_auth`）は `/debug_token` に訊けなかったとき、**要求した一覧をそのまま**
`scopes` に書き `scopes_source: "requested"` を添える——**一度も API に
確かめていない**。`ThreadsAdapter.granted_scopes()` はそれをそのまま
「一覧」として返し、`_scopes_source()` は「.token の scopes」と言っていた。
`_require_scope()`（無いと分かっている権限は HTTP を叩かない）や
`threads_read_cli._granted_source()`（「トークンに乗っています」の文言）が、
**要求しただけ**の一覧を「確かめて分かった」ものとして扱ってしまう。

ここで固定するもの:
  - `scopes_source: "requested"` の `.token` から組み立てると、
    `granted_scopes()` は一覧をそのまま返さず `_fetch_debug_scopes()`
    （＝`/debug_token`）を呼ぶ
  - `scopes_source: "response"` なら従来どおり一覧を返し、`/debug_token` は
    呼ばない
  - `scopes_source` が無い（旧い `.token`・`thth token set` が書く
    `"unknown"` 等）も、一覧が有れば従来どおり信頼する（**挙動を変えない**
    ——`"requested"` だけが特別）
  - `_scopes_source()` の文言も一覧の出どころに合わせて変わる
"""
from __future__ import annotations

from thth.adapters import threads as threads_mod

FAKE_TOKEN = "FAKE-TOKEN-VALUE-ABCDEFG"


def _adapter_from_account(scopes, scopes_source):
    return threads_mod.ThreadsAdapter.from_account(
        {}, {"access_token": FAKE_TOKEN, "scopes": scopes, "scopes_source": scopes_source})


# ==================================================== requested は付与済みとして使わない

def test_requestedならgranted_scopesは一覧を返さずdebug_tokenを呼ぶ(monkeypatch):
    adapter = _adapter_from_account(["threads_delete"], "requested")
    calls = []
    monkeypatch.setattr(adapter, "_fetch_debug_scopes",
                        lambda: (calls.append(1), ["threads_basic"])[1])

    result = adapter.granted_scopes()

    assert calls == [1], "requested の一覧をそのまま返し、debug_token を引いていない"
    assert result == ["threads_basic"]


def test_requestedならdebug_tokenも取れなければNone(monkeypatch):
    adapter = _adapter_from_account(["threads_delete"], "requested")
    monkeypatch.setattr(adapter, "_fetch_debug_scopes", lambda: None)
    assert adapter.granted_scopes() is None


def test_requestedならscopes_sourceの文言はdebug_token側(monkeypatch):
    adapter = _adapter_from_account(["threads_delete"], "requested")
    monkeypatch.setattr(adapter, "_fetch_debug_scopes", lambda: ["threads_basic"])
    assert adapter.scopes_source() == threads_mod.ThreadsAdapter.SCOPES_FROM_DEBUG_TOKEN


def test_requestedならdebug_tokenは1回だけ引く(monkeypatch):
    adapter = _adapter_from_account(["threads_delete"], "requested")
    calls = []
    monkeypatch.setattr(adapter, "_fetch_debug_scopes",
                        lambda: (calls.append(1), ["threads_basic"])[1])
    adapter.granted_scopes()
    adapter.granted_scopes()
    assert len(calls) == 1, "requested を不明扱いにしても、debug_token は毎回引き直さない"


# ==================================================== response は従来どおり信頼する

def test_responseなら一覧をそのまま返しdebug_tokenを呼ばない(monkeypatch):
    adapter = _adapter_from_account(["threads_basic", "threads_delete"], "response")
    calls = []
    monkeypatch.setattr(adapter, "_fetch_debug_scopes", lambda: calls.append(1))

    result = adapter.granted_scopes()

    assert calls == []
    assert result == ["threads_basic", "threads_delete"]


def test_responseならscopes_sourceの文言はtoken側(monkeypatch):
    adapter = _adapter_from_account(["threads_basic"], "response")
    assert adapter.scopes_source() == threads_mod.ThreadsAdapter.SCOPES_FROM_TOKEN


# ==================================================== scopes_source が無い（旧い .token）は変えない

def test_scopes_sourceが無くscopesが一覧なら従来どおり信頼する(monkeypatch):
    """**`"requested"` だけが特別**——`None`（旧い `.token`）・`"unknown"`
    （`thth token set`。ただし `scopes` は null なのでここには来ない）は
    従来の挙動のまま変えない（既存テストの回帰防止）。"""
    adapter = threads_mod.ThreadsAdapter.from_account(
        {}, {"access_token": FAKE_TOKEN, "scopes": ["threads_basic"]})
    calls = []
    monkeypatch.setattr(adapter, "_fetch_debug_scopes", lambda: calls.append(1))
    assert adapter.granted_scopes() == ["threads_basic"]
    assert calls == []


def test_scopesがnullなら従来どおりdebug_tokenに落ちる(monkeypatch):
    adapter = threads_mod.ThreadsAdapter.from_account(
        {}, {"access_token": FAKE_TOKEN, "scopes": None, "scopes_source": "unknown"})
    monkeypatch.setattr(adapter, "_fetch_debug_scopes", lambda: ["threads_basic"])
    assert adapter.granted_scopes() == ["threads_basic"]


# ==================================================== 直接 __init__ を渡すときも同じ

def test_init直接でもscopes_source_requestedはNoneに落ちる():
    adapter = threads_mod.ThreadsAdapter(
        access_token=FAKE_TOKEN, scopes=["threads_delete"], scopes_source="requested")
    assert adapter._token_scopes is None


def test_init直接でscopes_source省略なら従来どおり一覧を持つ():
    adapter = threads_mod.ThreadsAdapter(access_token=FAKE_TOKEN, scopes=["threads_delete"])
    assert adapter._token_scopes == ["threads_delete"]
