"""verify の赤・知らせ・断りで並びを切るとき、全部の数と省いた数を添える（苦味 第11 #8258・11/17）。

苦味の案A 第 3 束: 直した記録 165 件のうち `narrowed_after_seal` の items に挙がったのは 50 件だけで、全部の数が
出ていなかった。使う側は 50 件に判断を足して accept し、残り 115 件が leaving に回った。
"""
from __future__ import annotations

from thth.register import route as R
from thth.register import route_cli as RC
from tests.test_register_route import env, verify, words, _with_until, SELIMBONG  # noqa: F401


def test_切らない並びも全部の数と省いた数0を出す():
    assert R.capped("items", [1, 2, 3]) == {"items": [1, 2, 3], "items_total": 3, "items_omitted": 0}


def test_上限を超えた並びは先の分と全部の数と省いた数():
    got = R.capped("files", (f"f{i}" for i in range(165)))
    assert got["files"] == [f"f{i}" for i in range(R.LIST_CAP)]
    assert got["files_total"] == 165 and got["files_omitted"] == 165 - R.LIST_CAP


def test_route_の中に数を添えずに切る所が残っていない():
    src = (R.__file__ and open(R.__file__, encoding="utf-8").read())
    assert "[:50]" not in src


def test_文の出力は省いた数を言う():
    note = RC._omitted_note({"word": "narrowed_after_seal", "items": [1] * 50, "items_total": 165,
                             "items_omitted": 115})
    assert "165" in note and "115" in note
    assert RC._omitted_note({"word": "x", "items": [1], "items_total": 1, "items_omitted": 0}) == ""


def test_verify_の赤に全部の数が載り_文の出力でも言う(env, capsys, monkeypatch):
    led, site = _with_until(env, capsys)
    monkeypatch.setattr(R, "LIST_CAP", 0)
    rc, v = verify(capsys, led, "facts", site, "2027-04-01", "--no-previous")
    assert rc != 0 and "narrowed_after_seal" in words(v)
    p = next(p for p in v["problems"] if p["word"] == "narrowed_after_seal")
    assert p["items"] == [] and p["items_total"] == 1 and p["items_omitted"] == 1, p
    from thth import cli
    rc = cli.main(["register", "verify", str(led), "--outlet", "facts", "--material", str(site),
                   "--now", "2027-04-01", "--no-previous"])
    out, err = capsys.readouterr()
    assert rc != 0 and "全部で 1 件" in out + err and "残り 1 件" in out + err, out + err
