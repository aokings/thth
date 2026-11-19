"""連動 段 2: ビジネス プロフィールのイベントの投稿（`thth gbp post --event`）。偽の Google で回す（網に出ない）。

縛ること: 一段目は何も送らずに題と時刻を見せる・二段目で `topicType: EVENT` と `event`（題・始まりと終わり）が
そのまま送る値に入る・3 つの引数の片方だけ・逆の時刻・長すぎる題は送る前に前向きに断る。
"""
from __future__ import annotations

import json

from tests.test_gbp import ACC, LOC, google, run  # noqa: F401
from thth import refusal_catalog

ARGS = ["gbp", "post", f"{ACC}/{LOC}", "--text", "冬のブレンドを少しずつ味見する会です", "--event", "試食会",
        "--start", "2026-12-14 13:00", "--end", "2026-12-14 15:00", "--by", "店主"]


def test_イベントの投稿は一段目で題と時刻を見せて何も送らない(google, capsys):
    rc, out = run(ARGS, capsys)
    assert rc == 1, out
    assert "イベントの投稿（EVENT）: 試食会（2026-12-14 13:00〜15:00）" in out.out
    assert "--event '試食会' --start '2026-12-14 13:00' --end '2026-12-14 15:00'" in out.out
    assert google.writes() == []


def test_イベントの投稿は二段目でEVENTと題と時刻を送る(google, capsys):
    rc, out = run(ARGS + ["--json"], capsys)
    first = json.loads(out.out)
    assert rc == 1 and first["body"]["topicType"] == "EVENT"
    rc, out = run(ARGS + ["--confirm", first["digest"], "--json"], capsys)
    assert rc == 0, out
    [call] = google.writes()
    assert call["body"]["topicType"] == "EVENT" and call["body"]["summary"].startswith("冬のブレンド")
    assert call["body"]["event"] == {"title": "試食会", "schedule": {
        "startDate": {"year": 2026, "month": 12, "day": 14}, "startTime": {"hours": 13, "minutes": 0},
        "endDate": {"year": 2026, "month": 12, "day": 14}, "endTime": {"hours": 15, "minutes": 0}}}


def test_ふつうの投稿の送る値は今までと同じ(google, capsys):
    plain = ["gbp", "post", f"{ACC}/{LOC}", "--text", "冬の講座", "--by", "店主", "--json"]
    rc, out = run(plain, capsys)
    body = json.loads(out.out)["body"]
    assert rc == 1 and body == {"languageCode": "ja", "summary": "冬の講座", "topicType": "STANDARD"}


def test_イベントの引数が欠ける_逆の時刻_長い題は送る前に断る(google, capsys):
    for argv in (ARGS[:7] + ARGS[9:],                                               # --start が無い
                 [a if a != "2026-12-14 15:00" else "2026-12-14 12:00" for a in ARGS],  # 終わりが前
                 [a if a != "試食会" else "あ" * 59 for a in ARGS],                       # 題が長い
                 [a if a != "2026-12-14 13:00" else "12/14 13:00" for a in ARGS]):      # 時刻の形
        rc, out = run(argv, capsys)
        assert rc == 2 and "gbp_event_invalid" in out.out + out.err, argv
    assert google.writes() == []
    entry = refusal_catalog.CATALOG["gbp_event_invalid"]
    assert entry["why"] and entry["fix"]["text"] and entry["better"]
