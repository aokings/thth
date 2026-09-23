"""観測の地図 第 3 段——広場の層（設計 3.5.0 §1 の層 3）。

見るのは:
  - 点の語が title か scope_note に含まれる書き込みを、その点に結ぶ（広場の記録に欄は足さない）。
  - 種類ごとの数・施策の判定・追試の数（再現しなかった報告も同じ重さ）・分母。
  - 他の持ち主の project 範囲の書き込みは数えない。open は参加した持ち主どうしで写しだけ。
  - 非表示は数えない。広場の置き場が読めなければ null と理由。
  - 地図から広場へは何も書かない（一方向）。
"""
from __future__ import annotations

import os
from pathlib import Path

from thth import map_view, plaza
from tests.test_v340_plaza_store import owners, post, viewer  # noqa: F401  (fixture)


def _kopicha():
    return viewer("kopicha-threads", "kopicha-bsky", "kopicha-mstdn")


def test_語の一致で結び_種類と判定と追試を数える(owners):
    measure = post(kind="measure", title="コーヒーの朝投稿を問いにする", body="試した")
    post(kind="finding", title="冒頭の一文", scope_note="Threads のコーヒーの話題", body="気づき")
    post(kind="question", title="紅茶はどうか", body="問い")
    plaza.update(measure["plaza_id"], account="kopicha-threads", by="k", viewer=_kopicha(),
                 verdict="adopted", reason="伸びた")
    for result in ("reproduced", "not_reproduced", "not_reproduced"):
        plaza.reply(measure["plaza_id"], account="kopicha-bsky", kind="trial", result=result,
                    by="k", viewer=_kopicha())
    layer = map_view.plaza_layer(_kopicha(), ["コーヒー", "紅茶", "緑茶"])
    coffee = layer["コーヒー"]["own"]
    assert (coffee["measure"], coffee["finding"], coffee["question"]) == (1, 1, 0)
    assert coffee["denominator"] == 3
    assert coffee["verdicts"] == {"adopted": 1, "dropped": 0, "inconclusive": 0, "none": 0}
    assert coffee["trials"] == {"reproduced": 1, "not_reproduced": 2, "not_tried": 0,
                                "denominator": 3}
    assert measure["plaza_id"] in coffee["recent"]
    assert layer["コーヒー"]["link_basis"] == "title_or_scope_note_contains_word"
    assert layer["紅茶"]["own"]["question"] == 1
    assert layer["緑茶"]["own"]["measure"] == 0 and layer["緑茶"]["own"]["denominator"] == 3
    # 参加していなければ open は null と理由。
    assert layer["コーヒー"]["open"] is None and layer["コーヒー"]["open_reason"] == "plaza_not_joined"


def test_他の持ち主のproject範囲は数えない_openは参加どうしで写しだけ(owners):
    post(account="other-threads", title="コーヒー豆の話・他の持ち主", body="他", by="o")
    layer = map_view.plaza_layer(_kopicha(), ["コーヒー"])
    assert layer["コーヒー"]["own"]["finding"] == 0 and layer["コーヒー"]["own"]["denominator"] == 0
    plaza.set_membership("kopicha", joined=True, by="admin")
    plaza.set_membership("other", joined=True, by="admin")
    opened = post(account="other-threads", title="コーヒーの淹れ方", body="open の本文",
                  by="o", visibility="open")
    layer = map_view.plaza_layer(_kopicha(), ["コーヒー"])
    assert layer["コーヒー"]["own"]["finding"] == 0
    assert layer["コーヒー"]["open"]["finding"] == 1
    assert layer["コーヒー"]["open"]["recent"] == [opened["plaza_id"]]
    assert layer["コーヒー"]["open"]["denominator"] == 1
    # 置いた持ち主が参加から外れたら数えない。
    plaza.set_membership("other", joined=False, by="admin")
    assert map_view.plaza_layer(_kopicha(), ["コーヒー"])["コーヒー"]["open"]["finding"] == 0


def test_非表示は数えない_置き場が読めなければnullと理由(owners, monkeypatch):
    hidden = post(title="コーヒーの話", body="消す")
    plaza.hide(hidden["plaza_id"], by="admin", reason="重複")
    assert map_view.plaza_layer(_kopicha(), ["コーヒー"])["コーヒー"]["own"]["finding"] == 0

    def broken():
        raise plaza.PlazaError("plaza_store_unavailable")
    monkeypatch.setattr(plaza, "load_all", broken)
    layer = map_view.plaza_layer(_kopicha(), ["コーヒー"])
    assert layer["コーヒー"]["own"] is None and layer["コーヒー"]["cannot_say"] == "plaza_store_unavailable"


def test_地図から広場へは何も書かない(owners):
    post(title="コーヒーの話", body="本文")
    directory = Path(os.environ["THTH_ROOT"]) / "state" / "_plaza"
    before = {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    map_view.plaza_layer(_kopicha(), ["コーヒー"])
    assert before == {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    # 広場の口は地図の置き場を読まない（世間の層を project の外へ出さない・照合 §6-7）。
    root = Path(__file__).resolve().parent.parent / "thth"
    for name in ("plaza.py", "plaza_redact.py", "plaza_observe.py", "plaza_cli.py"):
        source = (root / name).read_text(encoding="utf-8")
        assert "map_" not in source and "_map" not in source, name
