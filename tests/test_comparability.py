"""比較材料に採用できる条件（masaru 裁定 2026-09-12・3 番）。

**投稿できる条件と、比較材料に採用できる条件は別。** 型が無くても投稿は止めない
（裁定 3 番）。分母に入れるかどうかはここで別に決める。

**いちばん大事なのは「確かめていない条件を、満たしたことにしない」こと。**
機械で見られるのは 3 つだけで、残り 2 つは見る口が無い。黙って通すと「宣言だけで
条件が守られた」ことになる。
"""
from __future__ import annotations

from thth.measured import comparability


def _そろった投稿(**上書き):
    post = {"post_id": "1", "form_now": "問いかけ", "form_readable": True,
             "rows": [{"missing": []}]}
    post.update(上書き)
    return post


def test_全部そろっていても判らないと答える():
    """**見る口が無い条件が残っているうちは `True` にしない。**"""
    out = comparability(_そろった投稿())
    assert out["ok"] is None, "**確かめていない条件を、満たしたことにしている**"
    assert out["blockers"] == []
    assert out["unchecked"], "確かめていない条件が消えている"


def test_読めたうえで型が無ければ採用しない():
    out = comparability(_そろった投稿(form_now=None))
    assert out["ok"] is False
    assert any("型" in b for b in out["blockers"])


def test_原稿が読めないときは型が無いと言わない():
    """**外部レビュー C2**（P2・2026-09-12）。`_form_for()` は「読めなかった」も
    「型が無い」も `None` で返す——**その但し書きを自分で書いておきながら、
    自分で踏んだ。** 読めたときだけ「型が無い」と言える。"""
    out = comparability(_そろった投稿(form_now=None, form_readable=False))
    assert out["ok"] is None, "**読めなかっただけなのに、採用不可と言い切っている**"
    assert not any("型（`form`）が無い" in b for b in out["blockers"])
    assert any("型を確認できない" in u for u in out["unchecked"])


def test_読めなくても確定した理由があればFalseのまま():
    """**不明が 1 つあるからといって、確定した駄目を消さない。**"""
    out = comparability(_そろった投稿(form_now=None, form_readable=False,
                                      post_id=None))
    assert out["ok"] is False
    assert any("公開" in b for b in out["blockers"])


def test_公開されていなければ採用しない():
    out = comparability(_そろった投稿(post_id=None))
    assert out["ok"] is False
    assert any("公開" in b for b in out["blockers"])


def test_実測が無ければ採用しない():
    out = comparability(_そろった投稿(rows=[]))
    assert out["ok"] is False
    assert any("実測" in b for b in out["blockers"])


def test_実測が全行欠けていれば採用しない():
    """**行があることと、値が揃っていることは違う。**"""
    out = comparability(_そろった投稿(rows=[{"missing": ["views"]},
                                             {"missing": ["likes"]}]))
    assert out["ok"] is False
    assert any("実測" in b for b in out["blockers"])


def test_1行でも揃っていれば実測はblockerにしない():
    out = comparability(_そろった投稿(rows=[{"missing": ["views"]}, {"missing": []}]))
    assert not any("実測" in b for b in out["blockers"])


def test_採用できない理由は全部並べる():
    """**1 つ見つけて打ち切らない**——直す側が 2 往復することになる。"""
    out = comparability({"post_id": None, "form_now": None,
                          "form_readable": True, "rows": []})
    assert out["ok"] is False
    assert len(out["blockers"]) == 3


def test_okのFalseとNoneを混ぜない():
    """**`False`（採用できない）と `None`（判らない）は別。**"""
    駄目 = comparability(_そろった投稿(form_now=None))   # 読めたうえで型が無い
    判らない = comparability(_そろった投稿())
    assert 駄目["ok"] is False
    assert 判らない["ok"] is None
    assert 駄目["ok"] != 判らない["ok"]
