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

def test_人向け表示は読めないを型無しと出さない(isolated_account, monkeypatch, capsys):
    """**外部レビュー・2026-09-12。** 原稿の不存在でも「（型無し）」と出ていた。"""
    import argparse
    from thth import cli as cli_mod, measured as measured_mod

    monkeypatch.setattr(measured_mod, "load", lambda _name: {
        "account": "x", "posts": [{"post_id": "P1", "topic": None,
                                    "form_now": None, "form_readable": False,
                                    "form_source": "current_draft",
                                    "posted_at": None, "file": "a.md", "rows": []}],
        "account_daily": [], "broken": [], "unknown_posts": [],
        "missing_post_metrics": None, "missing_account_daily_metrics": None,
        "posts_unknown_ownership": [], "files_seen": 1, "daily_files_seen": 0})
    cli_mod.cmd_measured(argparse.Namespace(account="x", json=False, post=None))
    out = capsys.readouterr().out
    assert "型未確認" in out
    assert "（型無し）" not in out, "**読めなかっただけなのに「型無し」と出している**"

def test_型と読めたかは1回の戻り値から取る(tmp_path, monkeypatch, isolated_account):
    """**外部レビュー・2026-09-12。** `_form_state()` を 2 回呼んでいたので、
    **途中で原稿が現れる／消えると `form_now` と `form_readable` が食い違った。**
    1 回の戻り値を 2 欄へ分ける。
    """
    from thth import measured as measured_mod

    返す = iter([(None, False), ("問いかけ", True)])
    monkeypatch.setattr(measured_mod, "_form_state",
                         lambda *_a, **_k: next(返す))

    post = {}
    型, 読めた = measured_mod._form_state("q", "a.md")
    post["form_now"], post["form_readable"] = 型, 読めた
    # **同じ 1 回の結果**なので、食い違いようがない。
    assert post["form_now"] is None and post["form_readable"] is False

    # **2 回呼ぶと食い違う**——それが起きない形にしてある、というのがこのテストの
    # 押さえどころ。`load()` が 2 回呼んでいないことを、呼び出し回数で見る。
    回数 = {"n": 0}

    def 数える(*_a, **_k):
        回数["n"] += 1
        return ("問いかけ", True)

    monkeypatch.setattr(measured_mod, "_form_state", 数える)
    monkeypatch.setattr(measured_mod, "_read_ndjson",
                         lambda _p: ([{"account": isolated_account["name"],
                                        "post_id": "P1", "collected_at": "2026-09-11T10:00:00+09:00",
                                        "metrics": {"views": 1}, "file": "a.md"}], False))
    import os as _os
    d = tmp_path / "posts"
    d.mkdir()
    (d / "P1.ndjson").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(_os.path, "isdir", lambda p: str(p).endswith("posts"))
    monkeypatch.setattr(_os, "listdir", lambda p: ["P1.ndjson"])
    measured_mod.load(isolated_account["name"])
    assert 回数["n"] == 1, f"**`_form_state` を {回数['n']} 回呼んでいる**（1 回であること）"
