"""会話の取得は頁を最後まで辿る（外部レビュー C1・P2・2026-09-12）。

**1 頁目だけ取って「取れた」と記録していた。** `/conversation` は頁分割された
一覧なので、**2 頁目にあった返信への返信は永久に入らない**——同じ刻みで再実行
しても、取得済の印が付いている。**「会話全体を残す」という目的そのものを外して
いた。**

閉鎖条件として挙がった 4 つを、それぞれ見る。
"""
from __future__ import annotations

import pytest

from thth.adapters import threads as threads_mod


class _頁を返す口(threads_mod.ThreadsAdapter):
    def __init__(self, 頁: list):
        self.頁 = list(頁)
        self.叩いた: list = []
        # `_all_pages()` は次の頁の指し先が**同じサーバの https** かを見る
        # （セキュリティ監査 2026-09-14・P1-2）。この偽の口は `__init__` を
        # 呼ばないので、比べる先をここで持たせる（頁を辿る筋書きは変えない）。
        self.base_url = "https://例"

    def _get(self, path, params, *, absolute_url=None):
        self.叩いた.append(absolute_url or path)
        if not self.頁:
            raise AssertionError("**用意した頁より多く叩いている**")
        return self.頁.pop(0)


def _頁(rows, next_url=None):
    body = {"data": rows}
    if next_url:
        body["paging"] = {"next": next_url}
    return body


def test_2頁目のネスト返信も取れる():
    口 = _頁を返す口([
        _頁([{"id": "R1"}], next_url="https://例/次"),
        _頁([{"id": "R2", "replied_to": {"id": "R1"}}]),
    ])
    assert [r["id"] for r in 口.conversation("POST1")] == ["R1", "R2"]
    assert len(口.叩いた) == 2, "2 頁目を叩いていない"


def test_次の頁は返ってきたURLをそのまま使う():
    """**cursor を自前で組み立てない。** 仕様が変わったとき黙って 1 頁で止まる。"""
    口 = _頁を返す口([_頁([{"id": "R1"}], next_url="https://例/次?after=abc"),
                      _頁([{"id": "R2"}])])
    口.conversation("POST1")
    assert 口.叩いた[1] == "https://例/次?after=abc"


def test_同じ頁を指し続けたら止める():
    """**黙って回り続けない。**"""
    同じ = "https://例/ぐるぐる"
    口 = _頁を返す口([_頁([{"id": "R1"}], next_url=同じ),
                      _頁([{"id": "R1"}], next_url=同じ)])
    with pytest.raises(RuntimeError) as e:
        口.conversation("POST1")
    assert "循環" in str(e.value)


def test_頁が多すぎたら途中までを取れたことにしない():
    頁 = [_頁([{"id": f"R{i}"}], next_url=f"https://例/{i}") for i in range(60)]
    口 = _頁を返す口(頁)
    with pytest.raises(RuntimeError) as e:
        口.conversation("POST1")
    assert "取れたことにしません" in str(e.value)


def test_途中で失敗したら部分を返さない():
    """**採取側は「例外なら記録を書かない」で成功と失敗を分けている。**
    部分を返すと、**取得済の印が付いて次の刻みでやり直せなくなる。**"""
    class _途中で落ちる(_頁を返す口):
        def _get(self, path, params, *, absolute_url=None):
            if absolute_url:
                raise RuntimeError("2 頁目が取れない")
            return _頁([{"id": "R1"}], next_url="https://例/次")

    with pytest.raises(RuntimeError):
        _途中で落ちる([]).conversation("POST1")

@pytest.mark.parametrize("だめな値", [123, ["https://例"], {"url": "x"}, ""])
def test_次の頁の指し先が読めなければ終端と読まない(だめな値):
    """**「正常な終端」と「型が違う」を分ける**（外部レビュー・2026-09-12）。

    文字列でなければ終端として返していたので、**数値・配列・辞書が来ると部分結果を
    成功として返していた**——取得済の印が付き、次の刻みでやり直せない。
    **読めない形は、終わりではない。**
    """
    口 = _頁を返す口([{"data": [{"id": "R1"}], "paging": {"next": だめな値}}])
    with pytest.raises(RuntimeError) as e:
        口.conversation("POST1")
    assert "取れたことにしません" in str(e.value)


@pytest.mark.parametrize("body", [
    {"data": [{"id": "R1"}]},                       # paging が無い
    {"data": [{"id": "R1"}], "paging": {}},          # next が無い
    {"data": [{"id": "R1"}], "paging": {"next": None}},
])
def test_正常な終端は終端として扱う(body):
    assert [r["id"] for r in _頁を返す口([body]).conversation("POST1")] == ["R1"]


def test_pagingの形が違えば終端と読まない():
    口 = _頁を返す口([{"data": [{"id": "R1"}], "paging": "つぎ"}])
    with pytest.raises(RuntimeError) as e:
        口.conversation("POST1")
    assert "`paging` の形が違います" in str(e.value)


# ---------------------------------------------------------------------------
# 次の頁の指し先（セキュリティ監査 2026-09-14・P1-2）
# ---------------------------------------------------------------------------
# `paging.next` は**サーバが自由に書ける文字列**で、`_get(absolute_url=...)` は
# それに `access_token` を付けて叩いていた。1 度返すだけで**トークンが第三者の
# ログに載る**。型が違うときと同じ扱い（`RuntimeError`・部分を成功にしない）。

@pytest.mark.parametrize("よそのURL", [
    "https://attacker.example/次",
    "http://例/次",                        # 同じホストでも平文への格下げ
    "https://例.attacker.example/次",       # 似た綴りの別ホスト
    "//attacker.example/次",               # scheme 相対
])
def test_次の頁が別のホストなら追わずに例外(よそのURL):
    口 = _頁を返す口([_頁([{"id": "R1"}], next_url=よそのURL)])
    with pytest.raises(RuntimeError) as e:
        口.conversation("POST1")
    assert "別のホスト" in str(e.value)
    # **1 頁目しか叩いていない**（よそへは行っていない）。
    assert len(口.叩いた) == 1


def test_別ホストのnextにaccess_tokenを載せない():
    """偽サーバ 1 本（127.0.0.1）に**1 件も届かない**ことを実測する。"""
    from tests.helpers.fake_redirect_server import recording_server

    with recording_server() as (よそ, 届いたもの):
        class 一頁目だけ偽物(threads_mod.ThreadsAdapter):
            def _get(self, path, params, *, absolute_url=None):
                if absolute_url is None:
                    return {"data": [{"id": "R1"}],
                            "paging": {"next": よそ + "/次"}}
                return super()._get(path, params, absolute_url=absolute_url)

        口 = 一頁目だけ偽物(base_url="https://graph.threads.net",
                            access_token="FAKE-TOKEN-never-a-real-secret",
                            user_id="1", timeout=5)
        with pytest.raises(RuntimeError):
            口.conversation("POST1")
    assert 届いたもの == [], 届いたもの
