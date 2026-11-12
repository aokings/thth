"""店の種類の表と、店に寄らない行の部品（設計 `docs/設計_ShopifyとSTORESの口_2026-11-12.md` §3・§6.2）。

柵:
  - 寄せのあとも BASE は同じ（台帳の欄・口・id の形・在庫）。今の `tests/test_store_base*.py` が 1 字も直さず緑なのが本体の証拠。
  - 商品の id が文字列の店（STORES・5.18.0）を受けられる: 写し・違い・下書きの選び・読み直しの出所は `str()` 同士で比べる。
  - 在庫が None（数えない）の商品は「在庫がある」側に数える（買える・節目の判定）。
  - 商品頁の URL が取れる店は、下書きにその URL を載せ `from_store_url: item` と書く。
本物の店は呼ばない。
"""
from __future__ import annotations

import pytest

from thth.shop import diff, draft, kinds, ledger, recheck, rows
from thth.shop import cli as store_cli
from thth.shop.wire import StoreRefused


def _row(i, **extra):
    row = {"id": i, "name": f"豆 {i}", "price": 1200, "stock": 3, "visible": True, "sales_status": "item_now_on_sale",
           "image_url": None, "item_url": None, "modified_at": "2026-09-08T00:00:00Z"}
    row.update(extra)
    return row


def test_BASEの種類は今の台帳の欄と口のまま():
    entry = kinds.KINDS["base"]
    assert entry["ledger_fields"] == ("store", "kind", "project", "token") == ledger.FIELDS
    assert "base" in ledger.KINDS
    assert entry["parse_item_id"]("12") == 12
    with pytest.raises(StoreRefused) as exc:
        entry["parse_item_id"]("abc")
    assert exc.value.code == "store_args_invalid"
    assert entry["not_by"] == "この道具（THTH）は BASE が提供するものではありません。"
    from thth.shop import base
    assert isinstance(entry["client"]({}, {"access_token": "x"}, None), base.Client)
    assert entry["read_cap"]() == base.PAGE_LIMIT * base.MAX_PAGES


def test_台帳は種類ごとの欄だけを受ける():
    good = ledger.build("a-base", kind="base", project="a")
    ledger.validate("a-base", good)
    for broken in (dict(good, kind="nope"), dict(good, shop="x"), {k: v for k, v in good.items() if k != "token"},
                   dict(good, kind=["base"])):
        with pytest.raises(StoreRefused) as exc:
            ledger.validate("a-base", broken)
        assert exc.value.code == "store_ledger_invalid"


def test_在庫が数えないなら買える側():
    assert rows.in_stock({"stock": None}) and rows.in_stock({"stock": 1})
    assert not rows.in_stock({"stock": 0}) and not rows.in_stock({"stock": -2})
    assert rows.buyable(_row(1, stock=None, sales_status="active"))
    assert not rows.buyable(_row(1, stock=0, sales_status="active"))
    assert not rows.buyable(_row(1, stock=None, sales_status="item_end_of_sale"))
    assert not rows.buyable({"id": 1, "visible": False})


def test_在庫の節目は在庫があるかの前と今():
    before = {"1": _row(1, stock=0), "2": _row(2, stock=None), "3": _row(3, stock=5)}
    now = [_row(1, stock=None), _row(2, stock=0), _row(3, stock=2)]
    found = {(c["kind"], c["id"]) for c in diff.compare(before, now, complete=True)}
    assert found == {("restock", 1), ("sold_out", 2)}


def test_文字列のidも写しと違いと下書きの選びが通る():
    before = {"aa1": _row("aa1")}
    now = [_row("aa1", price=980), _row("bb2")]
    changes = diff.compare(before, now, complete=True)
    assert {(c["kind"], c["id"]) for c in changes} == {("price_down", "aa1"), ("new", "bb2")}
    picked = draft.choose(changes, now, item="bb2")
    assert [row["id"] for _c, row in picked] == ["bb2"]
    # 整数の店で --item を文字列で渡されても同じ商品を選ぶ（str 同士で比べる）。
    changes = diff.compare({}, [_row(7)], complete=True)
    assert [row["id"] for _c, row in draft.choose(changes, [_row(7)], item="7")] == [7]
    assert rows.snapshot_row({"id": "aa1", "visible": False}) == {"id": "aa1", "visible": False}


def test_商品頁のURLが取れる店は下書きにそのURLを載せる():
    change = {"id": 1, "kind": "new", "draft_kind": "new"}
    common = dict(shop_url="https://shop.example.invalid", read_at="2026-11-12T10:00:00+09:00",
                  publish_at="2026-11-13T12:00:00+09:00")
    text = draft.text_for("s", "acct", "threads", change, _row(1, item_url="https://shop.example.invalid/p/1"), **common)
    assert "from_store_url: item" in text and "\nhttps://shop.example.invalid/p/1\n" in text
    text = draft.text_for("s", "acct", "threads", change, _row(1), **common)
    assert "from_store_url: shop" in text and "\nhttps://shop.example.invalid\n" in text
    # https でない URL は載せない（店の URL に戻す）。
    text = draft.text_for("s", "acct", "threads", change, _row(1, item_url="http://x.example/p"), **common)
    assert "from_store_url: shop" in text and "http://x.example/p" not in text


def test_命令のitemは英数字を受け_出所の形も英数字():
    assert store_cli._item_id("616950cd768f7263b4dab1a1") == "616950cd768f7263b4dab1a1"
    for bad in ("a-b", "", "x" * 65, "1 2"):
        with pytest.raises(Exception):
            store_cli._item_id(bad)
    assert recheck._ORIGIN.fullmatch("store/s-stores/item/616950cd768f7263b4dab1a1")
    assert not recheck._ORIGIN.fullmatch("store/s/item/a-b")


# ------------------------------------------------------------------ URL と名前の字（監査 5.18.0 直したほうがよい 1・2）
# 字そのものを貼らず chr() で作る（貼った字は道具の間で空白に化けうる・5.17.0 の寄せで起きた）。

@pytest.mark.parametrize("code", [0x2028, 0x2029, 0x00, 0x0A, 0x1F, 0x7F])
def test_https_urlは行を分ける字と制御文字を断る(code):
    # 空白を含めない（空白は別の検査で断るので、この字の検査を見たことにならない）。
    assert rows.https_url("https://shop.example.invalid/x" + chr(code) + "status:approved") is None
    assert rows.clean_text("豆" + chr(code) + "名") == "豆 名"


def test_https_urlはuserinfoを形を問わず断る():
    for url in ("https://u:p@h.example.invalid/x", "https://u@h.example.invalid/x", "https://:p@h.example.invalid/x",
                "https://@h.example.invalid/x"):
        assert rows.https_url(url) is None, url
    assert rows.https_url("https://h.example.invalid/x") == "https://h.example.invalid/x"


@pytest.mark.parametrize("code", [0x2028, 0x2029, 0x3000, 0x00A0, 0x200B, 0x202E, 0x0085, 0xFEFF, 0x3042])
def test_店の頁のURLはASCIIの見える字だけ(code):
    assert ledger.check_public_url("https://nigami.example.invalid/" + chr(code) + "x") is None
    assert ledger.check_public_url("https://nigami.example.invalid/x") == "https://nigami.example.invalid/x"
    assert ledger.check_public_url("https://xn--r8jz45g.jp/") == "https://xn--r8jz45g.jp/"
