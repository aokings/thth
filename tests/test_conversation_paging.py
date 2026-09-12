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
