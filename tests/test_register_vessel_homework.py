"""台帳の器 段 1 の宿題 — Codex の六回めの検収（`~/Documents/THTH/codex/答え_段1の六回め_2026-11-14.md`）の
末尾の結論の 1〜4 を突く試験。穴ごとに、直す前に赤を見てから直した。

1. 範囲の札が、範囲の軸の辞書の並びだけで変わる（並びの半分。区切りの半分は設計 §5.1 の札の決まりに当たるので
   直さずに持ち帰った）
2. 形の崩れた判断の行（必須の欄が無い・型が違う）を読み取りが受け入れ、`check` が例外で落ちる
3. 時点の改行だけ・辞書の時点のキーの並びだけが違うと、依る所の指紋は同じなのに「消えた」になる
4. `i:` の番号の中の点を区切りと取り違え、別の番号を結びの候補に出す
"""
from __future__ import annotations

import json

import pytest

from thth.register import judgment as J, vessel as V, vessel_cli
from tests.test_register_vessel import env  # noqa: F401
from tests.test_register_vessel_holes3 import _tiny3, _write, _row_j, _val_j


def _value(v, pointer, name="value"):
    rec = v.get("t:a")
    unit = V.unit_at(rec, pointer)
    return next(x for x in V.values_of(rec, v.kinds["t"], unit) if x.name == name)


# ---- 1. 範囲の札は、辞書の並びだけでは変わらない

def test_宿1_範囲の軸の辞書の並びだけを変えても前の札で尋ねて使える(env):
    row = {"uid": "r", "a": {"x": 1, "y": 2}, "value": 10}
    root = _tiny3(env, [{"year": 2026, "rows": [row]}], uid=True, scope={"key_fields": ["a"]}, depends=["value"])
    v0 = V.open_vessel(str(root))
    j = _val_j(v0, "i:r", "value")
    label0 = _value(v0, "i:r").scope
    _write(root, [{"year": 2026, "rows": [dict(row, a={"y": 2, "x": 1})]}])
    v1 = V.open_vessel(str(root))
    assert J.evaluate(v1, j) == "effective"
    assert _value(v1, "i:r").scope == label0
    ans = J.answer_value(v1, [j], "t:a", "i:r", "value", as_scope=label0)
    assert ans["usable"] is True and ans["conclusion"] == "good"


# ---- 2. 形の崩れた判断の行は、行の番号と欄を添えて断る

def _judgments_file(root, lines):
    path = root / ".thth" / "judgments.jsonl"
    path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines), encoding="utf-8")


def _good_line(v):
    return _val_j(v, "i:r", "value")


@pytest.mark.parametrize("broken, word", [
    ({"schema": "thth.judgment/1", "target": {"record": "t:a"}}, "id"),
    ("question=1", "question"),
    ("answer=1", "answer"),
    ("reason=x", "reason"),
    ("target.record=1", "target.record"),
    ("target.record=missing", "target.record"),
    ("target.unit=1", "target.unit"),
    ("target.value=[]", "target.value"),
    ("seen=1", "seen"),
    ("content=[]", "content"),
    ("at=5", "at"),
    ("siblings=x", "siblings"),
    ("id=", "id"),
])
def test_宿2_形の崩れた判断の行は行の番号と欄で断る(env, broken, word):
    root = _tiny3(env, [{"year": 2026, "rows": [{"uid": "r", "key": "a", "value": 10}]}], uid=True,
                  depends=["value"])
    v = V.open_vessel(str(root))
    good = _good_line(v)
    if isinstance(broken, dict):
        bad = broken
    else:
        path, _, raw = broken.partition("=")
        val = {"1": 1, "x": "x", "5": 5, "[]": [], "": "", "missing": None}[raw]
        bad = json.loads(json.dumps(good))
        if path.startswith("target."):
            if raw == "missing":
                bad["target"].pop(path.split(".")[1])
            else:
                bad["target"][path.split(".")[1]] = val
        else:
            bad[path] = val
    _judgments_file(root, [good, bad])
    with pytest.raises(V.RegisterRefused) as caught:
        J.read(str(root))
    assert caught.value.code == "register_judgment_invalid"
    assert caught.value.detail == f"line 2 {word}"
    with pytest.raises(V.RegisterRefused) as caught:
        vessel_cli.check(str(root))
    assert caught.value.code == "register_judgment_invalid"


def test_宿2_形の合う判断の行は今までどおり読む(env):
    root = _tiny3(env, [{"year": 2026, "rows": [{"uid": "r", "key": "a", "value": 10}]}], uid=True,
                  depends=["value"])
    v = V.open_vessel(str(root))
    good = _good_line(v)
    rj = _row_j(v, "i:r")
    _judgments_file(root, [good, rj])
    assert len(J.read(str(root))) == 2
    assert vessel_cli.check(str(root))["judgments"]["effective"] == 2


# ---- 3. 時点の比べは、指紋と同じ正規化に通す

@pytest.mark.parametrize("before, after", [
    ("2026年度\r\n前期", "2026年度\n前期"),
    ("2026年度\r前期", "2026年度\n前期"),
    ({"y": 2026, "term": "前期"}, {"term": "前期", "y": 2026}),
])
def test_宿3_時点の改行や辞書の並びだけが違っても判断は消えない(env, before, after):
    row = {"uid": "r", "key": "a", "value": 10}
    root = _tiny3(env, [{"year": before, "rows": [row]}], uid=True, depends=["value"],
                  scope={"key_fields": []})
    v0 = V.open_vessel(str(root))
    j = _val_j(v0, "i:r", "value")
    _write(root, [{"year": after, "rows": [row]}])
    v1 = V.open_vessel(str(root))
    assert _value(v0, "i:r").depend() == _value(v1, "i:r").depend()
    assert J.evaluate(v1, j) == "effective"
    ans = J.answer_value(v1, [j], "t:a", "i:r", "value")
    assert ans["usable"] is True and ans["conclusion"] == "good"


def test_宿3_時点の中身が違えば今までどおり消えた指し先(env):
    row = {"uid": "r", "key": "a", "value": 10}
    root = _tiny3(env, [{"year": "2026年度", "rows": [row]}], uid=True, depends=["value"])
    v0 = V.open_vessel(str(root))
    j = _val_j(v0, "i:r", "value")
    _write(root, [{"year": "2027年度", "rows": [row]}])
    assert J.evaluate(V.open_vessel(str(root)), j) == "vanished"


# ---- 4. 番号の中の点は区切りではない

def test_宿4_番号の中の点を区切りと取り違えず別の番号を候補に出さない(env):
    root = _tiny3(env, [{"year": 2026, "rows": [{"uid": "row.one", "key": "one", "value": 10}]}], uid=True,
                  depends=["value"])
    v0 = V.open_vessel(str(root))
    j = _val_j(v0, "i:row.one", "value")
    _write(root, [{"year": 2026, "rows": [{"uid": "row.two", "key": "two", "value": 10}]}])
    v1 = V.open_vessel(str(root))
    assert J.evaluate(v1, j) == "vanished"
    [c] = J.candidates(v1, [j])
    assert c["appeared"] == [] and c["same_depend"] == []


# ---- 1. 範囲の札は、語の中の区切りの字を逃がして書くので範囲ごとに 1 つ

def test_宿1_札の区切りを語の中へ動かしても違う札になり相手の値には及ばない(env):
    rows = [{"uid": "p", "a": "x,b=y", "b": "z", "value": 10}, {"uid": "q", "a": "x", "b": "y,b=z", "value": 10}]
    root = _tiny3(env, [{"year": 2026, "rows": rows}], uid=True, scope={"key_fields": ["a", "b"]},
                  depends=["value"])
    v = V.open_vessel(str(root))
    lp, lq = _value(v, "i:p").scope, _value(v, "i:q").scope
    assert lp != lq
    assert lp == "a=x\\,b\\=y,b=z" and lq == "a=x,b=y\\,b\\=z"
    jp = _val_j(v, "i:p", "value")
    assert J.answer_value(v, [jp], "t:a", "i:p", "value", as_scope=lp)["usable"] is True
    ans = J.answer_value(v, [jp], "t:a", "i:p", "value", as_scope=lq)
    assert ans["usable"] is False and ans["reason_kind"] == "scope_mismatch"
    assert J.answer_value(v, [jp], "t:a", "i:p", "value", as_scope="a=x,b=y,b=z")["usable"] is False


def test_宿1_札の語の中の逃がしの字も逃がす(env):
    rows = [{"uid": "p", "a": "x\\", "b": "z", "value": 1}, {"uid": "q", "a": "x", "b": "\\z", "value": 1}]
    root = _tiny3(env, [{"year": 2026, "rows": rows}], uid=True, scope={"key_fields": ["a", "b"]},
                  depends=["value"])
    v = V.open_vessel(str(root))
    assert _value(v, "i:p").scope == "a=x\\\\,b=z" and _value(v, "i:q").scope == "a=x,b=\\\\z"
