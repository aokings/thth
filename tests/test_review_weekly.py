"""週の振り返りの材料（`thth review weekly`・依頼 docs/依頼_THTH開発_週次の振り返りの材料とskill_2026-11-25.md）。

できあがりの条件:
  - 関東・こぴちゃの今の数字（依頼の表・THTH運用 第8 が 11/25 に集計）で「11/16 の週は drop: true」を出す。
  - 欠測と未成熟が 0 に混ざらない。
  - `--json` の形。
  - `study add` の期間の形で宣言し、その結果が同じ束に入る。
"""
from __future__ import annotations

import datetime
import json
import os

import pytest

from tests.test_after_cli import _insight_path, _write_ndjson
from tests.test_v360_goal_analytics import _seed_post
from thth import accounts as accounts_mod
from thth import cli, jst, refusal, review_weekly, study_cli

NOW = jst.parse("2026-11-25T12:00:00+09:00")       # 水曜。閉じた最後の週は 11/16 の週
PROJECT = "pdca"

# 依頼の表（週の始まり → 表示・いいね・フォロワーの増え）。こぴちゃのフォロワーは表に無い。
KANTO = {"10-26": (29_835, 45, 18), "11-02": (60_013, 53, 20),
         "11-09": (29_141, 68, 17), "11-16": (8_526, 18, 5)}
KOPICHA = {"10-26": (18_716, 101, None), "11-02": (2_631, 20, None),
           "11-09": (1_509, 19, None), "11-16": (686, 6, None)}


def _split(total, parts=5):
    base = total // parts
    return [base + (1 if i < total - base * parts else 0) for i in range(parts)]


def _seed_daily(account, rows):
    cfg = accounts_mod.load_account(account["name"])
    folder = accounts_mod.data_dirs(cfg, account["name"])["insights_account"]
    by_month = {}
    for date, count in rows:
        by_month.setdefault(date[:7], []).append(
            {"account": account["name"], "date": date, "metrics": {"followers_count": count}})
    for month, lines in by_month.items():
        _write_ndjson(os.path.join(folder, f"{account['name']}-{month}.ndjson"), lines)


def _seed_weeks(account, table, prefix):
    followers = 1000
    sundays = [("2026-10-25", followers)]
    for week, (views, likes, gained) in table.items():
        monday = datetime.date.fromisoformat(f"2026-{week}")
        for i, (v, l) in enumerate(zip(_split(views), _split(likes))):
            posted = jst.parse(f"{(monday + datetime.timedelta(days=i)).isoformat()}T09:00:00+09:00")
            _seed_post(account, f"{prefix}{week}-{i}", posted, goal="reach", views=v, likes=l)
        if gained is not None:
            followers += gained
            sundays.append(((monday + datetime.timedelta(days=6)).isoformat(), followers))
    if len(sundays) > 1:
        _seed_daily(account, sundays)


@pytest.fixture
def two(isolated_account_factory):
    kanto = isolated_account_factory("kanto-threads", project=PROJECT)
    kopicha = isolated_account_factory("kopicha-threads", project=PROJECT)
    _seed_weeks(kanto, KANTO, "K")
    _seed_weeks(kopicha, KOPICHA, "P")
    return kanto, kopicha


def test_関東の11月16日の週はdrop_表示とフォロワーの増えがどれだけ下がったか(two):
    payload = review_weekly.answer("kanto-threads", now=NOW)
    assert payload["week"]["start"] == "2026-11-16" and payload["scope"] == "account"
    node = payload["by_account"]["kanto-threads"]
    assert [row["totals"]["views"]["sum"] for row in node["weeks"]] == [None, 29_835, 60_013, 29_141, 8_526]
    assert [row["followers"]["delta"] for row in node["weeks"]][1:] == [18, 20, 17, 5]
    assert node["drop"] is True and payload["drop"] is True and payload["drop_accounts"] == ["kanto-threads"]
    views, followers = node["drop_detail"]["signals"]
    assert views["metric"] == "views" and views["previous_2_weeks"] == [29_141, 60_013]
    assert views["previous_average"] == 44_577 and views["dropped"] is True
    assert views["ratio"] == round(8_526 / 44_577, 3) and views["change"] == 8_526 - 44_577
    assert followers["value"] == 5 and followers["previous_average"] == 18.5 and followers["dropped"] is True
    # 11/9 の週は半分を切っていない（29,141 ≧ 44,924 の半分・17 ≧ 19 の半分）ので、続きは 1 週。
    assert [row["drop"] for row in node["weeks"]] == [None, None, None, False, True]
    assert node["drop_streak"] == 1


def test_こぴちゃは2週続けてdrop_streakが2(two):
    node = review_weekly.answer("kopicha-threads", now=NOW)["by_account"]["kopicha-threads"]
    assert node["drop"] is True and node["drop_streak"] == 2
    views, followers = node["drop_detail"]["signals"]
    assert views["value"] == 686 and views["previous_average"] == (2_631 + 1_509) / 2
    assert followers["dropped"] is None and followers["reason"] == "value_missing"   # 日次が無い・0 にしない


def test_projectで束ねると落ち込みの口座が並ぶ_週の指定は月曜にそろう(two):
    payload = review_weekly.answer(PROJECT, now=NOW)
    assert payload["scope"] == "project" and set(payload["by_account"]) == {"kanto-threads", "kopicha-threads"}
    assert payload["drop_accounts"] == ["kanto-threads", "kopicha-threads"]
    earlier = review_weekly.answer("kanto-threads", week="2026-11-12", now=NOW)   # 木曜 → 11/9 の週
    assert earlier["week"]["start"] == "2026-11-09"
    assert earlier["by_account"]["kanto-threads"]["drop"] is False


def test_当たりと外れは5本ずつ_原稿の型と数を持つ(two):
    node = review_weekly.answer("kanto-threads", now=NOW)["by_account"]["kanto-threads"]
    top, bottom = node["top"], node["bottom"]
    assert len(top) == 5 and bottom == []          # 5 本しか無い週は外れに重ねて出さない
    assert [row["metrics_24h"]["views"] for row in top] == sorted(_split(8_526), reverse=True)
    assert set(top[0]) >= {"post_id", "file", "topic", "kind", "goal", "hour_band", "has_link",
                           "attachment_kinds", "metrics_24h"}
    assert top[0]["goal"] == "reach" and top[0]["hour_band"] == "朝"
    assert top[0]["has_link"] is None              # 添付の記録が無い投稿は「なし」でなく不明
    strata = node["strata"]
    assert set(strata) == {"kind", "hour_band", "goal", "attachment_kind"}
    assert strata["goal"]["strata"]["reach"]["this_week"]["views_median"] == sorted(_split(8_526))[2]
    assert strata["goal"]["strata"]["reach"]["previous_week"]["views_median"] == sorted(_split(29_141))[2]


def test_欠測と未成熟は0に混ぜず別に並べる(isolated_account_factory):
    account = isolated_account_factory("solo", project="solo")
    now = jst.parse("2026-11-23T10:00:00+09:00")   # 月曜の朝。日曜の夜の投稿は 24 時間に満たない
    for i, views in enumerate((100, 200, 300, 400, 500)):
        _seed_post(account, f"M{i}", jst.parse(f"2026-11-{16 + i}T09:00:00+09:00"), views=views)
    _seed_post(account, "Z0", jst.parse("2026-11-21T09:00:00+09:00"), views=0)   # 実測の 0 は 0
    for post_id, posted in (("IMMATURE", "2026-11-22T20:00:00+09:00"),
                            ("MISSING", "2026-11-18T09:00:00+09:00")):
        at = jst.parse(posted)
        _write_ndjson(_insight_path(account, post_id), [{
            "account": "solo", "post_id": post_id, "posted_at": jst.iso(at),
            "collected_at": jst.iso(at + datetime.timedelta(hours=7)), "age_hours": 7, "marks": [6],
            "reply_to": None, "topic": None,
            "metrics": {"views": 9_999, "likes": 0, "replies": 0, "reposts": 0}}])
    payload = review_weekly.answer("solo", now=now)
    week = payload["by_account"]["solo"]["week"]
    assert week["posts"]["roots"] == 8
    assert week["totals"]["views"] == {"sum": 1_500, "n": 6, "reason": None}
    assert week["n_counted"] == 6 and week["n_immature"] == 1 and week["n_incomplete"] == 1
    assert week["not_counted"] == {"immature": ["IMMATURE"], "incomplete": ["MISSING"]}
    assert week["per_post_24h"]["views_median"] == 250 and week["per_post_24h"]["views_max"] == 500
    node = payload["by_account"]["solo"]
    listed = {row["post_id"] for row in node["top"] + node["bottom"]}
    assert "IMMATURE" not in listed and "MISSING" not in listed and "Z0" in listed
    assert any("0 とは数えていない" in line for line in node["cannot_say"])
    text = review_weekly.render_text(payload)
    assert "24 時間に満たない（未成熟）: 1 本（IMMATURE）" in text
    assert "24 時間の数が採れていない（欠測）: 1 本（MISSING）" in text
    # 根の投稿が 1 本も無い週の和は 0 でなく null（採っていない週と区別できないので）。
    first = node["weeks"][0]
    assert first["totals"]["views"] == {"sum": None, "n": 0, "reason": "no_root_posts_in_week"}


def test_jsonの形(two, monkeypatch, capsys):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["review", "weekly", PROJECT, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"schema_version", "report_type", "generated_at", "target", "scope", "week",
                            "min_n", "drop_rule", "drop", "drop_accounts", "drop_units", "by_account",
                            "series", "campaigns", "seeds", "studies",
                            "cannot_say", "cannot_say_details", "limitations", "provenance"}
    assert payload["schema_version"] == 1 and payload["report_type"] == "weekly_review"
    assert payload["provenance"]["advice"] == "excluded"
    node = payload["by_account"]["kanto-threads"]
    assert set(node) == {"medium", "week", "weeks", "drop", "drop_streak", "drop_detail", "top", "bottom",
                         "strata", "cannot_say", "cannot_say_details"}
    assert len(node["weeks"]) == review_weekly.WEEKS_BEFORE + 1
    assert set(node["week"]) == {"week_start", "week_end", "partial", "posts", "totals", "per_post_24h",
                                 "n_counted", "n_immature", "n_incomplete", "not_counted", "followers",
                                 "clicks_72h", "drop"}
    assert set(node["week"]["posts"]) == {"roots", "own_thread_replies", "replies_to_others"}
    assert set(node["week"]["totals"]) == {"views", "likes", "replies", "reposts"}
    assert node["week"]["totals"]["likes"]["sum"] == 18
    assert set(node["week"]["clicks_72h"]) == {"sum", "n", "reason"}


def test_人の読む形は平易な日本語で落ち込みを言う(two, monkeypatch, capsys):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(["review", "weekly", "kopicha-threads"]) == 0
    text = capsys.readouterr().out
    assert "**落ち込みの印: あり**（前の 2 週の平均の半分を切りました・2 週続いています）" in text
    assert "- 表示: 686。前の 2 週の平均 2,070 の 0.331 倍（-1,384・半分を切った）" in text
    assert "原因と次の手は出しません" in text and "## 先週の宣言（study）" in text


@pytest.mark.parametrize("argv,code", [
    (["review", "weekly", "no-such"], "review_target_unknown"),
    (["review", "weekly", "kanto-threads", "--week", "11/16"], "review_week_invalid"),
    (["review", "weekly", "kanto-threads", "--week", "2026-12-07"], "review_week_invalid"),
])
def test_断りは前向きな3つを出す(two, monkeypatch, capsys, argv, code):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    assert cli.main(argv) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"{code}: ")
    found = refusal.read(err, code)
    assert found["why"] and found["fix"] and found["better"]
    assert cli.main(argv + ["--json"]) == 2
    body = json.loads(capsys.readouterr().out)
    assert body["error"] == code and body["why"] and body["fix"] and body["better"]


def test_study_addの期間の形で宣言し_結果が同じ束に入る(two, tmp_path, monkeypatch, capsys):
    path = tmp_path / "2026-11-16_朝の型.json"
    monkeypatch.setattr(jst, "now_jst", lambda: jst.parse("2026-11-15T20:00:00+09:00"))
    assert cli.main(["study", "add", str(path), "--since", "2026-11-16", "--account", "kanto-threads",
                     "--hypothesis", "朝の型を短くすると表示が増える", "--change", "朝の投稿を 3 行に",
                     "--by", "tester", "--json"]) == 0
    made = json.loads(capsys.readouterr().out)
    assert made["period"] == {"basis": "posted_at", "start": "2026-11-16T00:00:00+09:00", "end": None,
                              "baseline_start": "2026-11-09T00:00:00+09:00"}
    declared = json.loads(path.read_text(encoding="utf-8"))
    assert declared["baseline_post_ids"] == [] and declared["decision"]["status"] == "adopted"
    # 同じ名前は上書きしない・投稿の id と混ぜない。
    assert cli.main(["study", "add", str(path), "--since", "2026-11-16", "--account", "kanto-threads",
                     "--hypothesis", "x", "--change", "y", "--by", "tester"]) == 2
    assert "既にあります" in capsys.readouterr().err
    assert cli.main(["study", "add", str(tmp_path / "b.json"), "P1", "--since", "2026-11-16",
                     "--by", "tester"]) == 2
    assert "一緒に使えません" in capsys.readouterr().err
    assert cli.main(["study", "add", str(path), "P1", "--by", "tester"]) == 2   # 期間の宣言に id は足さない
    capsys.readouterr()

    payload = review_weekly.answer("kanto-threads", now=NOW, studies=[str(path)])
    (study,) = payload["studies"]
    assert study["id"] == "2026-11-16_朝の型" and study["selection"]["basis"] == "declared_period_and_posted_at"
    assert study["groups"]["baseline"]["n_total"] == 5 and study["groups"]["changed"]["n_total"] == 5
    assert study["comparison"]["views"]["absolute_median_change"] == \
        sorted(_split(8_526))[2] - sorted(_split(29_141))[2]
    text = review_weekly.render_text(payload)
    assert "変えたこと「朝の投稿を 3 行に」" in text


def test_study_reportは期間がまだ始まっていない宣言を0本と言う(two, tmp_path):
    from thth import study_report
    path = tmp_path / "next.json"
    study_cli.declare(path, account="kanto-threads", hypothesis="h", change="c", since="2026-11-30",
                      baseline_days=14, by="tester", now=NOW)
    result = study_report.answer(str(path), now=NOW)
    assert result["observations"]["changed"]["n_total"] == 0
    assert result["observations"]["baseline"]["n_total"] == 5    # 11/16〜11/29（出したのは 11/16 の週の 5 本）
    assert any("まだ始まっていない" in line for line in result["cannot_say"])


# ------------------------------------------------------------ シリーズ・未分類・閉じた糸（依頼 §1-2）

SERIES_NAYAMI = """---
thth_scenario: 1
kind: series
id: nayami
title: 悩み相談
project: pdca
accounts: kanto-threads
---
"""

THREAD_MATCHA = """---
thth_scenario: 1
kind: thread
id: num-04-matcha
title: 数字で淹れる No.4 抹茶
goal_text: 抹茶の湯温を数字で覚えてもらう
project: pdca
started: 2026-10-26
---
- promote: threads need=2

- 2026-11-01 closed 見届けた
"""


THREAD_ODAI = """---
thth_scenario: 1
kind: thread
id: odai-run
title: お題の糸
project: pdca
started: 2026-11-16
---
- promote: threads need=3
"""


@pytest.fixture
def units(two, nigamilab_repo):
    from tests.test_v500_scenario import _sns, _write
    scen = os.path.join(nigamilab_repo, "docs", "sns", "scenarios")
    queue = os.path.join(nigamilab_repo, "docs", "sns", "queue")
    _write(os.path.join(scen, "nayami.md"), SERIES_NAYAMI, commit=False)
    _write(os.path.join(scen, "num-04-matcha.md"), THREAD_MATCHA, commit=False)
    _write(os.path.join(scen, "odai-run.md"), THREAD_ODAI, commit=False)
    for account, table, prefix in (("kanto-threads", KANTO, "K"), ("kopicha-threads", KOPICHA, "P")):
        for week in table:
            monday = datetime.date.fromisoformat(f"2026-{week}")
            for i in range(5):
                at = f"{(monday + datetime.timedelta(days=i)).isoformat()}T09:00:00+09:00"
                series = "nayami" if prefix == "K" and i < 3 else None
                thread = ("num-04-matcha" if prefix == "P" and week == "10-26" and i < 2 else
                          "odai-run" if prefix == "K" and week == "11-16" and i == 4 else None)
                _write(os.path.join(queue, f"{prefix}{week}-{i}.md"),
                       _sns(account, status="posted", publish_at=at, series=series, thread=thread,
                            extra={"post_id": f"{prefix}{week}-{i}", "posted_at": at}), commit=False)
    return two


def test_シリーズごとに材料と印が出る_札の無い原稿は未分類に束ねる(units):
    payload = review_weekly.answer(PROJECT, now=NOW)
    series = {entry["id"]: entry for entry in payload["series"]}
    assert set(series) == {"nayami", None}
    nayami = series["nayami"]
    assert nayami["declared"] and nayami["label"] == "悩み相談" and set(nayami["by_account"]) == {"kanto-threads"}
    node = nayami["by_account"]["kanto-threads"]
    assert [row["totals"]["views"]["sum"] for row in node["weeks"]] == \
        [None] + [sum(_split(KANTO[w][0])[:3]) for w in KANTO]
    assert node["week"]["posts"] == {"roots": 3, "own_thread_replies": 0, "replies_to_others": None}
    (signal,) = node["drop_detail"]["signals"]          # シリーズは表示の和だけで見る
    assert signal["metric"] == "views" and signal["dropped"] is True and node["drop"] is True
    assert nayami["drop"] is True
    unclassified = series[None]
    assert unclassified["unclassified"] and unclassified["label"] == "未分類"
    kanto = unclassified["by_account"]["kanto-threads"]
    assert kanto["week"]["totals"]["views"]["sum"] == _split(8_526)[3]     # 4 本目は糸の原稿
    kopicha = unclassified["by_account"]["kopicha-threads"]
    # こぴちゃの 10/26 の週の 5 本のうち 2 本は糸の原稿なので、未分類には 3 本。
    assert [row["posts"]["roots"] for row in kopicha["weeks"]] == [0, 3, 5, 5, 5]
    units_with_drop = {(u["unit"], u["id"], u["account"]) for u in payload["drop_units"]}
    assert ("series", "nayami", "kanto-threads") in units_with_drop
    assert ("account", "kanto-threads", "kanto-threads") in units_with_drop
    assert payload["drop"] is True


def test_閉じた糸は宣言した段と結果を並べ_印は付けない(units):
    payload = review_weekly.answer(PROJECT, now=NOW)
    matcha = next(entry for entry in payload["campaigns"] if entry["id"] == "num-04-matcha")
    assert matcha["id"] == "num-04-matcha" and matcha["state"] == "closed"
    assert matcha["drop"] is None and matcha["drop_reason"] == "closed"
    assert [(s["name"], s["need"], s["done"], s["satisfied"]) for s in matcha["stages"]] == \
        [("promote", 2, 2, True)]
    result = matcha["result"]
    assert result["by_account"]["kopicha-threads"]["posts"] == 2
    assert result["by_account"]["kopicha-threads"]["views_sum"] == sum(_split(18_716)[:2])
    assert result["visits"]["cannot_say"] == "site_visits_not_imported"   # 控えが無いのは 0 でなく言えない
    assert matcha["period"]["from"] == "2026-10-26" and matcha["period"]["to"] == "2026-11-01"
    assert "columns" in payload["seeds"]                                 # scenario seeds も同じ束
    text = review_weekly.render_text(payload)
    assert "### num-04-matcha「数字で淹れる No.4 抹茶」（閉じた・段 1/1 promote）" in text
    assert "- 結果 kopicha-threads: 2 本・表示の和 7,487" in text
    assert "| 未分類 | kopicha-threads | 5 |" in text
    assert "## 次の糸の種（thth scenario seeds）" in text


def test_口座で絞ると_その口座の原稿の無い糸とシリーズは出さない(units):
    payload = review_weekly.answer("kanto-threads", now=NOW)
    assert [entry["id"] for entry in payload["campaigns"]] == ["odai-run"]
    assert {entry["id"] for entry in payload["series"] if entry["by_account"]} == {"nayami", None}
    assert all(set(entry["by_account"]) <= {"kanto-threads"} for entry in payload["series"])


def test_動いている糸は段の進みにその週の数を添える(units):
    payload = review_weekly.answer(PROJECT, now=NOW)
    odai = next(entry for entry in payload["campaigns"] if entry["id"] == "odai-run")
    assert odai["state"] in ("active", "stalled") and odai["stage"]["name"] == "promote"
    assert [(s["need"], s["done"]) for s in odai["stages"]] == [(3, 1)]
    node = odai["by_account"]["kanto-threads"]
    assert node["week"]["posts"]["roots"] == 1 and node["week"]["totals"]["views"]["sum"] == _split(8_526)[4]
    assert node["drop"] is None                      # 前の 2 週に糸の投稿が無いので比べない（0 にしない）
    assert set(odai["this_week"]) == {"clicks_72h", "visits"}
    assert odai["this_week"]["visits"]["cannot_say"] == "site_visits_not_imported"
    assert "### odai-run「お題の糸」" in review_weekly.render_text(payload)
