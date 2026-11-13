"""外の場所に手で貼る束（計画 `docs/計画_実店舗_2026-11-13.md` 段 4・叩き台 §10）。

見本の店（`registers.example/cafe-sample` の `外の場所/` に Google マップと食べログの 2 枚）を一時の置き場に写して使う。
時刻は 2026-12-08 10:00 JST に止める。

柵:
  - 推測の行からは貼る文を作らない（柵を通った行だけ）。貼る文に出さない欄（休みの理由・場所の URL・管理する人・
    住所・電話）を入れない。
  - 貼る束と比べは網に出ない（外の場所の頁を読まず、管理画面に入らない）。
  - 「貼った」の記録は、場所の行の頭の決まった欄（synced_on・synced_row）だけを書く。
  - まだ貼っていない変わりは穴に出て、貼ると消え、中身が変わればまた出る。
  - 比べは違いを見せるだけで、どちらが正しいかを決めず、写した文を残さない。
"""
from __future__ import annotations

import datetime
import io
import pathlib
import socket
import sys
import urllib.request

import pytest

from tests.test_register_base import add, env, jrun, run, write  # noqa: F401
from tests.test_register_store import NOW, clock, confirm, edit  # noqa: F401
from thth import morning
from thth.register import form

SPECIAL = "cafe-special-2026-12-12"
PLACE_FILE = pathlib.Path("外の場所") / "食べログ.md"


@pytest.fixture
def cafe(capsys, env, clock):
    return add(capsys, env, "cafe-sample", kind="store")


def unpasted(capsys) -> set:
    rc_, value, err = jrun(capsys, "register", "holes", "cafe-sample")
    assert rc_ == 0, err
    return {h["hole"] for h in value["holes"] if h["kind"] == "unpasted"}


def compare(capsys, monkeypatch, text, row=SPECIAL, place="place-cafe-tabelog"):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    return jrun(capsys, "register", "compare", "cafe-sample", place, "--for", row)


def test_見本の店の外の場所の行は形の点検を通る(capsys, cafe):
    rc_, value, err = jrun(capsys, "register", "check", "cafe-sample")
    assert rc_ == 0 and value["problems"] == [], err
    shown = {row["id"]: row for row in jrun(capsys, "register", "show", "cafe-sample")[1]["rows"]}
    assert shown["place-cafe-google"]["kind"] == "place" and shown["place-cafe-tabelog"]["kind"] == "place"


def test_柵_推測の行からは貼る文を作らない(capsys, cafe):
    rc_, value, _ = jrun(capsys, "register", "paste", "cafe-sample", SPECIAL)
    assert rc_ == 2 and value["code"] == "register_row_guess"
    rc_, value, _ = jrun(capsys, "register", "pasted", "cafe-sample", "place-cafe-tabelog", "--for", SPECIAL,
                         "--by", "m")
    assert rc_ == 2 and value["code"] == "register_row_guess"
    assert "synced_on" not in (cafe / PLACE_FILE).read_text(encoding="utf-8")


def test_柵_貼る文は場所ごとに出て出さない欄を含まない(capsys, cafe):
    confirm(capsys, "cafe-sample", SPECIAL)
    rc_, value, err = jrun(capsys, "register", "paste", "cafe-sample", SPECIAL)
    assert rc_ == 0, err
    places = {one["place"]: one for one in value["places"]}
    assert set(places) == {"place-cafe-google", "place-cafe-tabelog"}
    for one in places.values():
        text = one["text"]
        assert "12月12日" in text and "14:00" in text and "18:00" in text
        for hidden in ("設備の点検", "example.invalid", "店主", "000-0000", "みほん町"):
            assert hidden not in text, (hidden, text)
        assert one["pasted"] is False and one["fits"] is True and one["field"]
    assert places["place-cafe-tabelog"]["max_chars"] == 200
    # 場所の行の URL と管理する人は、点検表には出る（持ち主がどこに貼るか分かるように）。
    assert places["place-cafe-tabelog"]["url"].startswith("https://") and places["place-cafe-tabelog"]["admin_by"]


def test_営業の時間と催しも貼る文になり品は断る(capsys, cafe):
    for row in ("cafe-hours", "cafe-hours-2026-12-15", "cafe-event-2026-12-14", "cafe-item-winter-blend"):
        confirm(capsys, "cafe-sample", row)
    hours = jrun(capsys, "register", "paste", "cafe-sample", "cafe-hours")[1]["places"][0]["text"]
    assert hours.splitlines()[0].startswith("月 ") and len(hours.splitlines()) >= 7
    assert "12月15日" in jrun(capsys, "register", "paste", "cafe-sample", "cafe-hours-2026-12-15")[1]["places"][0]["text"]
    assert jrun(capsys, "register", "paste", "cafe-sample", "cafe-event-2026-12-14")[1]["places"][0]["text"].startswith("【催し】")
    rc_, value, _ = jrun(capsys, "register", "paste", "cafe-sample", "cafe-item-winter-blend")
    assert rc_ == 2 and value["code"] == "register_paste_kind"


def test_場所の行が無ければ断る_場所でない行に貼ったとは書かない(capsys, cafe):
    confirm(capsys, "cafe-sample", SPECIAL)
    rc_, value, _ = jrun(capsys, "register", "pasted", "cafe-sample", "cafe-store", "--for", SPECIAL, "--by", "m")
    assert rc_ == 2 and value["code"] == "register_place_not_place"
    for name in ("Google マップ.md", "食べログ.md"):
        (cafe / "外の場所" / name).rename(cafe / "外の場所" / (name + ".txt"))
    rc_, value, _ = jrun(capsys, "register", "paste", "cafe-sample", SPECIAL)
    assert rc_ == 2 and value["code"] == "register_paste_nothing"


@pytest.mark.parametrize("change, problem", [
    (("place: tabelog", "place: tabelog_x"), "place_name"),
    (("url: https://example.invalid/tabelog/cafe", "url: http://example.invalid/tabelog/cafe"), "place_url"),
])
def test_柵_形の違う場所の行は直し方を添えて断る(capsys, cafe, change, problem):
    edit(cafe / PLACE_FILE, *change)
    rc_, value, _ = jrun(capsys, "register", "check", "cafe-sample")
    assert rc_ == 2 and value["code"] == "register_row_invalid" and problem in str(value), value
    confirm(capsys, "cafe-sample", SPECIAL)
    rc_, value, _ = jrun(capsys, "register", "pasted", "cafe-sample", "place-cafe-tabelog", "--for", SPECIAL,
                         "--by", "m")
    assert rc_ == 2 and value["code"] == "register_row_invalid"


def test_柵_貼ったの記録は場所の行の頭の決まった欄だけ書く(capsys, cafe):
    confirm(capsys, "cafe-sample", SPECIAL)
    before = (cafe / PLACE_FILE).read_text(encoding="utf-8")
    rc_, value, err = jrun(capsys, "register", "pasted", "cafe-sample", "place-cafe-tabelog", "--for", SPECIAL,
                           "--by", "m")
    assert rc_ == 0, err
    after = (cafe / PLACE_FILE).read_text(encoding="utf-8")
    added = [line for line in after.splitlines() if line not in before.splitlines()]
    assert [line.split(":", 1)[0] for line in added] == ["synced_on", "synced_row"]
    assert set(form.MANAGED) >= {"synced_on", "synced_row"}
    assert [line for line in before.splitlines() if line not in after.splitlines()] == []
    assert after.split("---\n", 2)[2] == before.split("---\n", 2)[2]          # 本文は同じ
    assert "synced_on: 2026-12-08" in after and f"synced_row: {SPECIAL}@" in after
    # 場所の行は形の点検を通ったままで、認めた記録も外れない（MANAGED の欄は指紋に入らない）。
    assert jrun(capsys, "register", "check", "cafe-sample")[1]["problems"] == []


def test_まだ貼っていない変わりは穴に出て貼ると消え中身が変わればまた出る(capsys, cafe, clock):
    assert unpasted(capsys) == set()                                          # 推測の行は貼るべき物にならない
    confirm(capsys, "cafe-sample", SPECIAL)
    google, tabelog = f"unpasted:place-cafe-google:{SPECIAL}", f"unpasted:place-cafe-tabelog:{SPECIAL}"
    assert unpasted(capsys) == {google, tabelog}
    assert jrun(capsys, "register", "pasted", "cafe-sample", "place-cafe-google", "--for", SPECIAL, "--by", "m")[0] == 0
    assert unpasted(capsys) == {tabelog}
    paste = {one["place"]: one["pasted"] for one in jrun(capsys, "register", "paste", "cafe-sample", SPECIAL)[1]["places"]}
    assert paste == {"place-cafe-google": True, "place-cafe-tabelog": False}
    # 中身を変えて認め直すと、貼った物は古いので、また穴になる。
    edit(cafe / "臨時" / "2026-12-12_設備の点検.md", "close: 18:00", "close: 17:00")
    confirm(capsys, "cafe-sample", SPECIAL)
    assert unpasted(capsys) == {google, tabelog}
    # 臨時の日を過ぎたら、もう貼るべき物ではない。
    clock["now"] = NOW + datetime.timedelta(days=5)
    assert unpasted(capsys) == set()


def test_朝の知らせに貼っていない数が出る(capsys, cafe):
    assert morning._register_paste_lines({"sample"}, NOW) == []
    confirm(capsys, "cafe-sample", SPECIAL)
    [line] = morning._register_paste_lines({"sample"}, NOW)
    assert "cafe-sample" in line and "2 件" in line and "paste" in line
    assert morning._register_paste_lines({"other"}, NOW) == []


def test_外の場所の見直し日を過ぎたら穴にする(capsys, cafe, clock):
    clock["now"] = datetime.datetime(2027, 3, 2, 10, 0, tzinfo=NOW.tzinfo)
    found = {h["hole"]: h for h in jrun(capsys, "register", "holes", "cafe-sample")[1]["holes"]}
    assert "place:place-cafe-google" in found and "compare" in found["place:place-cafe-google"]["hint"]


def test_柵_比べは違いを見せ写した文を残さない(capsys, cafe, monkeypatch, env):
    confirm(capsys, "cafe-sample", SPECIAL)
    rc_, value, err = compare(capsys, monkeypatch, "12/12 は 14:00-19:30 の営業です（目印 ZQXV）")
    assert rc_ == 0, err
    assert value["same"] is False
    assert value["only_register"]["times"] == ["18:00"] and value["only_outside"]["times"] == ["19:30"]
    assert value["only_register"]["dates"] == [] and value["only_outside"]["dates"] == []
    assert "決めません" in value["notes"][0]
    rc_, value, err = compare(capsys, monkeypatch, "12月12日 14:00〜18:00 営業")
    assert rc_ == 0 and value["same"] is True, err
    for base in (cafe, env["root"], env["tmp"] / "home"):
        for path in pathlib.Path(base).rglob("*"):
            if path.is_file():
                assert "ZQXV" not in path.read_text(encoding="utf-8", errors="ignore"), path


def test_比べる文が空か長すぎれば断る(capsys, cafe, monkeypatch):
    confirm(capsys, "cafe-sample", SPECIAL)
    for text in ("", "あ" * 5001):
        rc_, value, _ = compare(capsys, monkeypatch, text)
        assert rc_ == 2 and value["code"] == "register_compare_empty"


def test_柵_貼る束と比べと貼ったの記録は網に出ない(capsys, cafe, monkeypatch):
    confirm(capsys, "cafe-sample", SPECIAL)

    def refuse(*_a, **_k):
        raise AssertionError("網に出ようとした")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert jrun(capsys, "register", "paste", "cafe-sample", SPECIAL)[0] == 0
    assert jrun(capsys, "register", "pasted", "cafe-sample", "place-cafe-tabelog", "--for", SPECIAL, "--by", "m")[0] == 0
    assert compare(capsys, monkeypatch, "12月12日 14:00-18:00")[0] == 0
    assert jrun(capsys, "register", "holes", "cafe-sample")[0] == 0


def test_貼る文は人の読む形でも出る(capsys, cafe):
    confirm(capsys, "cafe-sample", SPECIAL)
    rc_, out, err = run(capsys, "register", "paste", "cafe-sample", SPECIAL)
    assert rc_ == 0, err
    assert "[まだ] 食べログ" in out and "thth register pasted" in out and "設備の点検" not in out
