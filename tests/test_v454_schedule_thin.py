"""4.5.4: 1 日の本数が減ったら `thth events` に `schedule_thin`（続いている状態）を出す・
`thth measured` の日ごとの行に `collected_at`（いつ数えたか）を出す。

10/21 から関東の Threads は朝・夜の枠の原稿が尽き、1 日 4〜5 本が 2 本に減った。昼・夕の予約は
10/29 まであるので、4.5.2 の `schedule_low`（最後の予約が 48 時間より手前か）は鳴らなかった。
見るのは: 関東の形で鳴る・足りている口座で鳴らない・物差し 2 本未満は対象外・返信を数えない・
決め打ち（`stock_daily_min`）が効く・空いた時間の文・`--after` でも毎回返る・measured の collected_at。
"""
from __future__ import annotations

import datetime
import json
import os

from tests.conftest import write_queue_file
from thth import accounts as accounts_mod, cli, events_cli, jst, measured as measured_mod, sent

# 関東の現物を読んだ時刻（10/23 08:4x）。今日はまだ 1 本も出ていない（朝 8 時の枠が尽きている）。
NOW = datetime.datetime(2026, 10, 23, 8, 45, tzinfo=jst.JST)
FULL = ((8, 0), (12, 30), (17, 30), (19, 0), (21, 0))
NO_LATE = ((8, 0), (12, 30), (17, 30), (19, 0))
NOON = ((12, 30), (17, 30))
# 10/16〜22: 5・4・5・4・5・2・2 本＝27 本（1 日 3.9 本・6 割は 2.3 本）。
KANTO_PAST = {16: FULL, 17: NO_LATE, 18: FULL, 19: NO_LATE, 20: FULL, 21: NOON, 22: NOON}
# 10/23〜25 の承認済み予約: 2・2・3 本（10/25 だけ 20 時を足す）。
KANTO_FUTURE = {23: NOON, 24: NOON, 25: NOON + ((20, 0),)}


def _at(day, hm):
    return datetime.datetime(2026, 10, day, hm[0], hm[1], tzinfo=jst.JST)


def _published(account, past, *, reply_to=None):
    state = accounts_mod.state_dir_for(account["name"])
    for day, slots in past.items():
        for hm in slots:
            at = _at(day, hm)
            sent.write(state, post_id=f"p{'r' if reply_to else ''}-{day}-{hm[0]}{hm[1]:02d}",
                       text="公開した本文", body_hash="0" * 64, sent_at=jst.iso(at), reply_to=reply_to)


def _reserve(account, future, *, reply_to=None):
    for day, slots in future.items():
        for hm in slots:
            name = f"{'r' if reply_to else ''}{day}-{hm[0]:02d}{hm[1]:02d}.md"
            fm = {"account": account["name"], "status": "approved", "publish_at": jst.iso(_at(day, hm))}
            if reply_to:
                fm["reply_to"] = reply_to
            write_queue_file(account["queue_dir"], name, body="## threads\n\n予約の本文です。\n",
                             fm_overrides=fm)


def _thin(account, **kwargs):
    payload = events_cli.build(account["name"], since="7d", now=NOW, **kwargs)
    return [e for e in payload["events"] if e["kind"] == "schedule_thin"], payload


def test_kanto_shape_is_flagged_with_free_hours(isolated_account):
    _published(isolated_account, KANTO_PAST)
    _reserve(isolated_account, KANTO_FUTURE)
    rows, payload = _thin(isolated_account)
    assert len(rows) == 1
    row = rows[0]
    assert row["ongoing"] is True and row["ref"] == "daily"
    assert row["average_daily"] == 3.9 and row["floor_daily"] == 2.3 and row["window_days"] == 7
    assert row["thin_days"] == [{"date": "2026-10-23", "count": 2}, {"date": "2026-10-24", "count": 2}]
    assert row["free_hours"] == [8, 19, 21]
    assert row["summary"] == "10/23 は 2 本（直近 7 日は 1 日 3.9 本）。空いた時間: 8 時台・19 時台・21 時台"
    assert "stock_daily_min" not in row
    assert payload["by_kind"]["schedule_thin"] == 1
    # 予約は 10/25 まで届いているので、48 時間の schedule_low は鳴らない（今回の穴）。
    assert payload["by_kind"]["schedule_low"] == 0


def test_today_counts_already_published_roots(isolated_account):
    """今日は「もう出た根の投稿＋今日の残りの予約」。朝 8 時に出ていれば 10/23 は 3 本で足りる。"""
    _published(isolated_account, {**KANTO_PAST, 23: ((8, 0),)})
    _reserve(isolated_account, KANTO_FUTURE)
    rows, _ = _thin(isolated_account)
    assert len(rows) == 1
    assert rows[0]["thin_days"] == [{"date": "2026-10-24", "count": 2}]
    assert rows[0]["summary"].startswith("10/24 は 2 本")


def test_enough_per_day_is_quiet(isolated_account):
    _published(isolated_account, KANTO_PAST)
    _reserve(isolated_account, {23: NO_LATE, 24: NO_LATE, 25: NO_LATE})
    rows, _ = _thin(isolated_account)
    assert rows == []


def test_average_below_two_is_out_of_scope(isolated_account):
    """1 日 1 本の口座は、予約が 0 本でも schedule_thin の対象にしない（schedule_low の仕事）。"""
    _published(isolated_account, {day: ((12, 30),) for day in range(16, 23)})
    rows, _ = _thin(isolated_account)
    assert rows == []


def test_replies_are_not_counted(isolated_account):
    # 根は 1 日 1〜2 本（平均 1.9 本）・返信を足すと 2 本を超える → 返信を数えなければ対象外。
    roots = {16: NOON, 17: NOON, 18: NOON, 19: NOON, 20: NOON, 21: NOON, 22: ((12, 30),)}
    _published(isolated_account, roots)
    _published(isolated_account, {16: ((9, 0),), 17: ((9, 0),)}, reply_to="p-16-1230")
    rows, _ = _thin(isolated_account)
    assert rows == []
    # 関東の形の上に、10/24 の返信の予約を 2 本置いても、10/24 は 2 本のまま（足りない日に残る）。
    more = ((8, 0), (19, 0), (21, 0))
    _published(isolated_account, {16: more, 17: more[:2], 18: more, 19: more[:2], 20: more})
    _reserve(isolated_account, KANTO_FUTURE)
    _reserve(isolated_account, {24: ((9, 0), (10, 0))}, reply_to="p-16-1230")
    rows, _ = _thin(isolated_account)
    assert len(rows) == 1
    assert {"date": "2026-10-24", "count": 2} in rows[0]["thin_days"]


def test_fixed_floor_from_account_set(isolated_account, capsys):
    _published(isolated_account, KANTO_PAST)
    _reserve(isolated_account, KANTO_FUTURE)
    name = isolated_account["name"]
    # 窓口がわざと 2 本に減らした: 決め打ち 2 本なら 2・2・3 本は切っていない（6 割はかけない）。
    assert cli.main(["account", "set", name, "stock_daily_min", "2", "--by", "test", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["after"] == 2
    assert accounts_mod.load_account(name)["stock_daily_min"] == 2
    rows, _ = _thin(isolated_account)
    assert rows == []
    # 決め打ち 3 本: 10/23・10/24 で鳴る（平均の 6 割＝2.3 本ではなく 3 本で比べる）。
    assert cli.main(["account", "set", name, "stock_daily_min", "3", "--by", "test"]) == 0
    capsys.readouterr()
    rows, _ = _thin(isolated_account)
    assert len(rows) == 1
    assert rows[0]["stock_daily_min"] == 3 and rows[0]["floor_daily"] == 3
    assert [d["date"] for d in rows[0]["thin_days"]] == ["2026-10-23", "2026-10-24"]
    assert "決め打ちは 1 日 3 本" in rows[0]["summary"]
    # 外すと平均に戻る。
    assert cli.main(["account", "set", name, "stock_daily_min", "-", "--by", "test"]) == 0
    capsys.readouterr()
    assert "stock_daily_min" not in accounts_mod.load_account(name)
    assert cli.main(["account", "set", name, "stock_daily_min", "abc", "--by", "test"]) == 2


def test_returned_every_time_with_after(isolated_account):
    _published(isolated_account, KANTO_PAST)
    _reserve(isolated_account, KANTO_FUTURE)
    first = events_cli.build(isolated_account["name"], since="7d", now=NOW)
    again = events_cli.build(isolated_account["name"], after=first["next_cursor"], now=NOW)
    ids = [e["event_id"] for e in first["events"] if e["kind"] == "schedule_thin"]
    assert ids and ids == [e["event_id"] for e in again["events"] if e["kind"] == "schedule_thin"]


def test_reading_writes_nothing(isolated_account):
    _published(isolated_account, KANTO_PAST)
    _reserve(isolated_account, KANTO_FUTURE)
    before = sorted(os.listdir(isolated_account["queue_dir"]))
    _thin(isolated_account)
    assert sorted(os.listdir(isolated_account["queue_dir"])) == before


# ------------------------------------------------------------------ measured の collected_at

def _daily_path(account):
    return os.path.join(account["repo_dir"], "data", "sns", "insights", "account",
                        f"{account['name']}-2026-10.ndjson")


def test_measured_daily_rows_carry_collected_at(isolated_account, capsys):
    path = _daily_path(isolated_account)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"account": isolated_account["name"], "date": "2026-10-21",
                            "metrics": {"views": 14450}}) + "\n")   # 古い行（数えた時刻が無い）
        f.write(json.dumps({"account": isolated_account["name"], "date": "2026-10-22",
                            "collected_at": "2026-10-23T00:03:12+09:00",
                            "metrics": {"views": 1153}}) + "\n")
    result = measured_mod.load(isolated_account["name"])
    old, new = result["account_daily"]
    assert "collected_at" not in old
    assert new["collected_at"] == "2026-10-23T00:03:12+09:00"
    cli.show_measured(result)
    out = capsys.readouterr().out
    lines = {line.split()[0]: line for line in out.splitlines() if line.startswith("  2026-10-2")}
    assert "数えた 10/23 00:03" in lines["2026-10-22"]
    assert "数えた" not in lines["2026-10-21"]
