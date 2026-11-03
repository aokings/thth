"""5.11.0 段 8（設計 効果測定と連動 §7.1 の 3・§7.3 の段 8・§7.4）: 訪問を糸の一生の数にする。

hub は日ごとの窓（`window.from == window.to`・終わり D-2）を 1 サイト 1 日 1 ファイルで送る（schema は
`hub.site_visits/1` のまま）。THTH は糸の期間（started → closed か done の最後の動き・無ければ今日）に
重なる日の控えだけを**足す**。

見るのは（柵）:
  - 3 日分の日ごとの控えを足す（`window_basis: thread_period_daily`）。
  - 同じサイトの同じ日の控えが 2 つあれば、`generated_at` の新しい 1 つだけ（二重に足さない）。
  - 控えの無い日は `days_missing`（数だけ・0 と混ぜない）。D-2 より後の日は `days_pending` に分ける。
    取れなかった日（rows が null）は `days_unavailable`。
  - 幅のある窓（7 日）の控えだけなら今までどおり（`window_basis: latest_window`）。
  - 混ざったとき: 期間に日ごとの控えがあるサイトは日ごとだけを足し、幅のある窓は使わず `mixed_windows`。
  - 糸の期間の外の日は足さない（閉じた糸）。
  - `referrers` も日ごとを host ごとに足す。seeds の列 2 は `--days` の窓で足す。
"""
from __future__ import annotations

import json
import os
import pathlib

from tests.test_v500_scenario import NOW, PROJECT, _sns, _write, golden_world, kp  # noqa: F401
from tests.test_v570_flow_site_visits import THREAD, _row, _visits, flow  # noqa: F401
from thth import cli, dashboard_cli, plaza_from, scenario, scenario_seeds, site_visits

# NOW は 2026-10-28 11:20 JST。D-2 は 10-26（10-27・10-28 は「まだ来ない日」）。
# 糸 flow-01 は started 2026-10-20・閉じていない → 期間は 10-20〜10-28（9 日）。

FLOW02 = """---
thth_scenario: 1
kind: thread
id: flow-02
title: 閉じた糸
project: kp
started: 2026-10-21
---
- promote: threads need=1

# 出来事

- 2026-10-23 closed 区切り
"""


def _hub_day(tmp_path, day, rows, *, generated="2026-10-28T06:00:00+09:00", referrers=None, site="kopicha",
             name=None):
    """hub の `ga4.py site-visits --days 1 --lag 2 --out-dir` の 1 ファイルの形（site は hub のサイトの鍵）。"""
    return _visits(tmp_path, rows, frm=day, to=day, site=site, referrers=referrers,
                   name=name or f"{site}_{day}.json", generated_at=generated)


def _import(path):
    return site_visits.import_file(PROJECT, path, now=NOW)


def _place(stored_name, path, **override):
    """控えの置き場に、別の名前で控えを 1 つ置く（同じ日の 2 つ目の控え）。"""
    record = site_visits.validate(json.loads(pathlib.Path(path).read_text(encoding="utf-8")), now=NOW)
    record.update(override)
    target = pathlib.Path(site_visits.store_dir(PROJECT)) / stored_name
    target.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def _show(thread="flow-01"):
    return scenario.show(PROJECT, thread, now=NOW)["site_visits"]


def _site(block, name="kopicha"):
    return next(s for s in block["sites"] if s["site"] == name)


def test_日ごとの控えを3日分足す(flow, tmp_path):
    for day, n in (("2026-10-20", 5), ("2026-10-21", 6), ("2026-10-22", 7)):
        _import(_hub_day(tmp_path, day, [_row("th-flow-01", n, engaged=1)]))
    block = _show()
    assert block["window_basis"] == "thread_period_daily"
    assert block["period"] == {"from": "2026-10-20", "to": "2026-10-28", "from_basis": "started",
                               "to_basis": "today"}
    assert block["sessions"] == 18 and block["engaged_sessions"] == 3
    site = _site(block)
    assert site["window"] == {"from": "2026-10-20", "to": "2026-10-28"}
    assert (site["days_in_period"], site["days_covered"], site["days_missing"], site["days_pending"]) == (9, 3, 4, 2)
    assert site["by_medium"] == {"social": 18} and site["rows_total"] == 3
    # 同じ campaign・原稿・source・medium・着地の頁の行は 1 行に足す。
    assert [(c["file"], c["sessions"]) for c in site["campaigns"]] == [("th-flow-01.md", 18)]
    assert block["partial_sites"] == ["kopicha"]


def test_同じ日の2ファイルは新しいgenerated_atの方だけ(flow, tmp_path):
    _import(_hub_day(tmp_path, "2026-10-24", [_row("th-flow-01", 4)]))
    # 同じ日の 2 つ目の控え（名前の違うファイル・新しい generated_at）。名前の順では先に読まれる。
    again = _hub_day(tmp_path, "2026-10-24", [_row("th-flow-01", 9)], generated="2026-10-28T09:00:00+09:00",
                     name="again.json")
    _place("a-copy_2026-10-24.json", again)
    block = _show()
    assert block["sessions"] == 9, "同じ日は新しい 1 つだけ（4＋9 にしない）"
    assert _site(block)["days_covered"] == 1
    # 2 つ目の控えの方が古ければ使わない（generated_at で決める・ファイル名の順でない）。
    _place("a-copy_2026-10-24.json", again, generated_at="2026-10-28T05:00:00+09:00")
    assert _show()["sessions"] == 4


def test_抜けた日はdays_missingで0にしない_期間に1日も無ければ言えない(flow, tmp_path):
    _import(_hub_day(tmp_path, "2026-10-20", [_row("th-flow-01", 3)]))
    _import(_hub_day(tmp_path, "2026-10-26", [_row("th-flow-01", 2)]))
    site = _site(_show())
    assert site["sessions"] == 5 and site["days_missing"] == 5 and site["days_covered"] == 2
    # 糸の期間に日ごとの控えが 1 日も無ければ 0 でなく「言えない」。
    _write(os.path.join(flow["scen"], "flow-02.md"), FLOW02)
    _write(os.path.join(flow["queue"], "th-flow-02.md"),
           _sns("kp-threads", status="posted", publish_at="2026-10-22T07:00:00+09:00", thread="flow-02",
                extra={"post_id": "333_444", "posted_at": "2026-10-22T07:00:03+09:00"}))
    block = _show("flow-02")
    assert block["sessions"] is None and block["cannot_say"] == "no_daily_in_period"
    assert _site(block)["days_missing"] == 3 and _site(block)["days_covered"] == 0


def test_D2より後の日はdays_pending_来ていればその日も足す(flow, tmp_path):
    for day in ("2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25",
                "2026-10-26"):
        _import(_hub_day(tmp_path, day, [_row("th-flow-01", 1)]))
    site = _site(_show())
    assert (site["days_covered"], site["days_missing"], site["days_pending"]) == (7, 0, 2)
    assert "partial_sites" not in _show(), "まだ来ない日は欠けではない"
    # hub が lag 1 で 10-27 を送っていれば、その日も足す（まだ来ない日から外れる）。
    _import(_hub_day(tmp_path, "2026-10-27", [_row("th-flow-01", 10)]))
    block = _show()
    assert block["sessions"] == 17 and (_site(block)["days_covered"], _site(block)["days_pending"]) == (8, 1)


def test_取れなかった日はdays_unavailable_欠けとも0とも混ぜない(flow, tmp_path):
    _import(_hub_day(tmp_path, "2026-10-21", [_row("th-flow-01", 4)]))
    # hub は取れなかった日を rows・referrers とも null と unknown_reason で送る（ga4.py build_site_visits）。
    _import(_visits(tmp_path, None, frm="2026-10-22", to="2026-10-22", site="kopicha", reason="ga4_error",
                    name="failed.json"))
    block = _show()
    site = _site(block)
    assert site["sessions"] == 4 and (site["days_covered"], site["days_unavailable"], site["days_missing"]) == (1, 1, 5)
    assert block["partial_sites"] == ["kopicha"]
    ref = _site(dashboard_cli.build(PROJECT, now=NOW)["site_referrers"])
    assert ref["days_unavailable"] == 1 and ref["days_covered"] == 1


def test_古い7日の窓だけなら今までどおり(flow, tmp_path):
    _import(_visits(tmp_path, [_row("th-flow-01", 6)], frm="2026-10-21", to="2026-10-27"))
    block = _show()
    assert block["sessions"] == 6 and block["window_basis"] == "latest_window" and block["cannot_say"] is None
    site = _site(block, "kopicha.org")
    assert site["window"] == {"from": "2026-10-21", "to": "2026-10-27"} and "days_missing" not in site


def test_混ざったら日ごとだけ足し_幅のある窓は使わずmixed_windowsと言う(flow, tmp_path):
    _import(_visits(tmp_path, [_row("th-flow-01", 100)], frm="2026-10-20", to="2026-10-26", site="kopicha",
                    name="w.json"))
    _import(_hub_day(tmp_path, "2026-10-25", [_row("th-flow-01", 5)]))
    block = _show()
    assert block["sessions"] == 5, "7 日の窓（100）と日ごと（5）を重ねて足さない"
    site = _site(block)
    assert site["window_basis"] == "thread_period_daily" and site["mixed_windows"] == {"wide_ignored": 1}
    assert block["mixed_windows"] == ["kopicha"]
    # 日ごとの控えが期間に無い糸（10-21〜10-23 で閉じた）は、幅のある窓の最新を今までどおり読む。
    _write(os.path.join(flow["scen"], "flow-02.md"), FLOW02)
    _write(os.path.join(flow["queue"], "th-flow-02.md"),
           _sns("kp-threads", status="posted", publish_at="2026-10-22T07:00:00+09:00", thread="flow-02"))
    _import(_visits(tmp_path, [_row("th-flow-02", 8)], frm="2026-10-14", to="2026-10-20", site="kopicha",
                    name="w2.json"))
    two = _show("flow-02")
    assert two["window_basis"] == "latest_window" and two["sessions"] is None   # 最新の 7 日の窓には flow-02 が無い
    assert "mixed_windows" not in two


def test_糸の期間の外の日は足さない(flow, tmp_path):
    _write(os.path.join(flow["scen"], "flow-02.md"), FLOW02)
    _write(os.path.join(flow["queue"], "th-flow-02.md"),
           _sns("kp-threads", status="posted", publish_at="2026-10-22T07:00:00+09:00", thread="flow-02"))
    for day, n in (("2026-10-20", 100), ("2026-10-21", 1), ("2026-10-22", 2), ("2026-10-23", 3),
                   ("2026-10-24", 200)):
        _import(_hub_day(tmp_path, day, [_row("th-flow-02", n)]))
    block = _show("flow-02")
    assert block["period"]["to_basis"] == "closed" and block["period"]["to"] == "2026-10-23"
    assert block["sessions"] == 6, "期間（10-21〜10-23）の外の 10-20・10-24 は足さない"
    site = _site(block)
    assert (site["days_in_period"], site["days_covered"], site["days_missing"], site["days_pending"]) == (3, 3, 0, 0)


def test_referrersも日ごとをhostごとに足す(flow, tmp_path):
    _import(_hub_day(tmp_path, "2026-10-24", [], referrers=[{"host": "b.hatena.ne.jp", "sessions": 3},
                                                            {"host": "t.co", "sessions": 1}]))
    _import(_hub_day(tmp_path, "2026-10-25", [], referrers=[{"host": "b.hatena.ne.jp", "sessions": 2},
                                                            {"host": "(direct)", "sessions": 9}]))
    # 同じ日の重複（古い generated_at）は足さない。
    _place("z-old_2026-10-25.json", _hub_day(tmp_path, "2026-10-25", [],
                                             referrers=[{"host": "b.hatena.ne.jp", "sessions": 50}],
                                             generated="2026-10-27T06:00:00+09:00", name="old.json"))
    block = dashboard_cli.build(PROJECT, now=NOW)["site_referrers"]
    site = _site(block)
    assert site["window_basis"] == "recent_days_daily"
    assert site["referrers"] == [{"host": "b.hatena.ne.jp", "sessions": 5}, {"host": "t.co", "sessions": 1}]
    assert site["dropped"] == {"rows": 1, "sessions": 9}
    assert site["window"] == {"from": "2026-09-29", "to": "2026-10-26"} and site["days_covered"] == 2
    text = json.dumps(block, ensure_ascii=False)
    assert "campaign" not in text and "thread" not in text, "糸には結ばない"


def test_seedsの列2は日ごとをdaysの窓で足す(flow, tmp_path):
    _import(_hub_day(tmp_path, "2026-10-10", [_row("th-flow-01", 50)]))   # --days 7 の窓（10-21〜）の外
    _import(_hub_day(tmp_path, "2026-10-22", [_row("th-flow-01", 4)]))
    _import(_hub_day(tmp_path, "2026-10-23", [_row("th-flow-01", 3)]))
    column = next(c for c in scenario_seeds.build(PROJECT, days=7, now=NOW)["columns"] if c["key"] == "visits")
    site = column["sites"][0]
    assert site["window_basis"] == "seeds_days_daily"
    assert site["window"] == {"from": "2026-10-21", "to": "2026-10-28"}
    assert [(r["file"], r["sessions"]) for r in site["rows"]] == [("th-flow-01.md", 7)]
    assert (site["days_covered"], site["days_missing"], site["days_pending"]) == (2, 4, 2)


def test_show_dashboard_plazaは糸の期間の数を出す(flow, tmp_path, capsys):
    for day, n in (("2026-10-21", 2), ("2026-10-22", 3)):
        _import(_hub_day(tmp_path, day, [_row("th-flow-01", n)]))
    camp = dashboard_cli.build(PROJECT, "campaign", "flow-01", now=NOW)["campaign"]["outcomes"]
    assert camp["article_visits"]["sessions"] == 5
    assert camp["article_visits"]["window_basis"] == "thread_period_daily"
    ledger = scenario.load_ledger(PROJECT, [n for n in ("kp-threads", "kp-x", "kp-hatenablog")], now=NOW)
    visits = plaza_from._visits_block(site_visits.thread_block(PROJECT, ledger, "flow-01"))
    assert visits["sessions"] == 5 and visits["window_basis"] == "thread_period_daily"
    assert visits["sites"][0]["days_missing"] == 5 and visits["sites"][0]["days_pending"] == 2
    assert cli.main(["scenario", "show", PROJECT, "flow-01"]) == 0
    assert "日ごとに足した日 2/9（欠け 5・まだ来ない 2）" in capsys.readouterr().out


# ---- 段 8 の監査（Fable）の直し ----

FLOW03 = """---
thth_scenario: 1
kind: thread
id: flow-03
title: とても長い糸
project: kp
started: 2024-01-01
---
- promote: threads need=5
"""

FLOW04 = """---
thth_scenario: 1
kind: thread
id: flow-04
title: 始まりの分からない糸
project: kp
---
- promote: threads need=1
"""


def _flow02(flow):
    _write(os.path.join(flow["scen"], "flow-02.md"), FLOW02)
    _write(os.path.join(flow["queue"], "th-flow-02.md"),
           _sns("kp-threads", status="posted", publish_at="2026-10-22T07:00:00+09:00", thread="flow-02"))


def test_監査2a_期間の外の幅のある窓は足さずoutside_period(flow, tmp_path, capsys):
    # 閉じた糸（10-21〜10-23）に日ごとの控えが無く、期間の後の 7 日の窓（10-24〜10-27）だけがある。
    _flow02(flow)
    _import(_visits(tmp_path, [_row("th-flow-02", 40)], frm="2026-10-24", to="2026-10-27"))
    block = _show("flow-02")
    assert block["period"]["from"] == "2026-10-21" and block["period"]["to"] == "2026-10-23"
    assert block["sessions"] is None, "期間の外の窓の数を糸の訪問に足さない"
    assert block["cannot_say"] == "outside_period"
    site = _site(block, "kopicha.org")
    assert site["cannot_say"] == "outside_period" and site["sessions"] is None and site["campaigns"] == []
    assert cli.main(["scenario", "show", PROJECT, "flow-02"]) == 0
    assert "outside_period" in capsys.readouterr().out


def test_監査2a_期間をはみ出す窓は足してwindow_exceeds_period(flow, tmp_path):
    # 窓 10-20〜10-26 は期間 10-21〜10-23 に重なるが、両側にはみ出す。幅のある窓は日に割れないので足して言う。
    _flow02(flow)
    _import(_visits(tmp_path, [_row("th-flow-02", 6)], frm="2026-10-20", to="2026-10-26"))
    block = _show("flow-02")
    assert block["sessions"] == 6 and block["cannot_say"] is None
    assert _site(block, "kopicha.org")["window_exceeds_period"] is True
    assert block["window_exceeds_period"] == ["kopicha.org"]
    # 期間の中に収まる窓には付けない。
    _import(_visits(tmp_path, [_row("th-flow-01", 6)], frm="2026-10-21", to="2026-10-27", name="in.json"))
    one = _show()
    assert "window_exceeds_period" not in _site(one, "kopicha.org") and "window_exceeds_period" not in one


def test_監査2b_800日を越える期間はperiod_truncatedと切る前の日数(flow, tmp_path):
    import datetime
    _write(os.path.join(flow["scen"], "flow-03.md"), FLOW03)
    _write(os.path.join(flow["queue"], "th-flow-03.md"),
           _sns("kp-threads", status="posted", publish_at="2026-10-22T07:00:00+09:00", thread="flow-03"))
    _import(_hub_day(tmp_path, "2026-10-22", [_row("th-flow-03", 3)]))
    block = _show("flow-03")
    assert (block["period"]["from"], block["period"]["to"]) == ("2024-01-01", "2026-10-28")
    site = _site(block)
    full = (datetime.date(2026, 10, 28) - datetime.date(2024, 1, 1)).days + 1
    assert full > site_visits.MAX_PERIOD_DAYS
    assert site["days_in_period"] == site_visits.MAX_PERIOD_DAYS
    assert site["period_truncated"] is True and site["days_before_truncation"] == full
    # 800 日に収まる糸には付けない。
    _import(_hub_day(tmp_path, "2026-10-21", [_row("th-flow-01", 1)]))
    assert "period_truncated" not in _site(_show())


def test_監査2c_同じ日の重複を落とした数を出す(flow, tmp_path):
    _import(_hub_day(tmp_path, "2026-10-24", [_row("th-flow-01", 4)],
                     referrers=[{"host": "t.co", "sessions": 1}]))
    assert "duplicates_dropped" not in _site(_show()), "落としていなければ出さない"
    _place("a-copy_2026-10-24.json", _hub_day(tmp_path, "2026-10-24", [_row("th-flow-01", 9)],
                                               referrers=[{"host": "t.co", "sessions": 2}],
                                               generated="2026-10-28T09:00:00+09:00", name="again.json"))
    assert _site(_show())["duplicates_dropped"] == 1
    assert _site(dashboard_cli.build(PROJECT, now=NOW)["site_referrers"])["duplicates_dropped"] == 1
    column = next(c for c in scenario_seeds.build(PROJECT, days=7, now=NOW)["columns"] if c["key"] == "visits")
    assert column["sites"][0]["duplicates_dropped"] == 1
    # 数える期間の外の日の重複は数えない（seeds の --days 1 の窓は 10-27〜）。
    column = next(c for c in scenario_seeds.build(PROJECT, days=1, now=NOW)["columns"] if c["key"] == "visits")
    assert "duplicates_dropped" not in column["sites"][0]


def test_監査2g_始まりの分からない糸はNoneと出さない(flow, tmp_path, capsys):
    _write(os.path.join(flow["scen"], "flow-04.md"), FLOW04)
    _import(_hub_day(tmp_path, "2026-10-24", [_row("th-flow-01", 4)]))
    block = _show("flow-04")
    assert block["period"]["from"] is None and block["cannot_say"] == "thread_period_unknown"
    assert cli.main(["scenario", "show", PROJECT, "flow-04"]) == 0
    out = capsys.readouterr().out
    assert "始まり不明〜2026-10-28" in out and "None〜" not in out


def test_hubの日ごとの1ファイルをsite_importで受け取る_ファイル名は日付で1日1つ(flow, tmp_path, capsys):
    path = _hub_day(tmp_path, "2026-10-26", [_row("th-flow-01", 2)],
                    referrers=[{"host": "threads", "sessions": 2}])
    assert cli.main(["scenario", "site-import", PROJECT, "--file", path, "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["stored"] == "state/kp/site_visits/kopicha_2026-10-26.json" and got["replaced"] is False
    assert got["window"] == {"from": "2026-10-26", "to": "2026-10-26"}
    assert _import(path)["replaced"] is True
    assert sorted(os.listdir(site_visits.store_dir(PROJECT))) == ["kopicha_2026-10-26.json"]
    assert _show()["sessions"] == 2
