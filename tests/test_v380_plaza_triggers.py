"""3.8.0 D1・D2 置くきっかけと追試の予定日（設計 3.8.0 §D1・§D2）。

見るのは:
  - finding に `trial_due`（追試の予定日）。期日で observe の次の一手に「追試の結果を足す」
    （同じ持ち主の追試が期日のあとに付けば消える）。
  - observe の次の一手に「広場に置く」（下書きの命令つき）: 施策の期間が終わった／配分を変えた
    週が閉じた（週の表 3.7.0 A3）。候補の列挙だけ・本文は作らない。
"""
from __future__ import annotations

import datetime
import json

import pytest

from thth import analytics_weekly, cli, jst, morning, plaza, plaza_moments
from tests.test_v340_plaza_store import SCOPE, owners, post, reply  # noqa: F401  (fixture)
from tests.test_v380_plaza_owner import viewer


@pytest.fixture
def quiet(owners, monkeypatch):
    monkeypatch.setattr(morning, "_world", lambda *a, **k: morning.cell(cannot_say="no_watch_words"))
    return owners


def _steps(payload, candidate):
    node = next(s for s in payload["sections"] if s["section"] == "next_steps")["value"]
    return [s for s in node["steps"] if s["kind"] == "plaza" and s["candidate"] == candidate]


def test_追試の予定日はfindingだけ_日付か時刻(owners):
    result = post(title="夜より朝", trial_due="2026-10-08")
    record = plaza.STORE.get(result["plaza_id"])
    assert record["trial_due"] == "2026-10-08T00:00:00+09:00"
    shown = plaza.show(result["plaza_id"], viewer("kopicha-bsky"))
    assert shown["trial_due"] == "2026-10-08T00:00:00+09:00"
    assert plaza.list_posts(viewer("kopicha-bsky"))["posts"][0]["trial_due"] == record["trial_due"]
    timed = post(title="時刻で", trial_due="2026-10-08T09:00:00+09:00")
    assert plaza.STORE.get(timed["plaza_id"])["trial_due"] == "2026-10-08T09:00:00+09:00"
    for bad in ("2026-13-01", "来週", "2026-10-08T09:00:00"):
        with pytest.raises(plaza.PlazaError, match="^invalid_trial_due$"):
            post(title=f"bad {bad}", trial_due=bad)
    with pytest.raises(plaza.PlazaError, match="^invalid_trial_due$"):
        post(kind="measure", title="施策には付けない", trial_due="2026-10-08")
    assert "invalid_trial_due" in plaza.REASONS and "invalid_trial_due" in plaza.NEXT


def test_予定日が来たら追試の結果を足す_同じ持ち主の追試が付けば消える(quiet):
    now = jst.now_jst()
    due = post(title="朝の問い", trial_due=jst.iso(now - datetime.timedelta(hours=1)))
    later = post(title="まだ先", trial_due=jst.iso(now + datetime.timedelta(days=3)))
    steps = _steps(morning.build("kopicha-threads", now=now, mark=False), "add_trial")
    assert [s["plaza_id"] for s in steps] == [due["plaza_id"]]
    step = steps[0]
    assert step["command"] == (f"thth plaza reply {due['plaza_id']} --as kopicha-threads --kind trial "
                               "--result <reproduced|not_reproduced|not_tried> --by <名前>")
    assert "body" not in step and later["plaza_id"]
    # 同じ持ち主（同じ project の別の媒体）の追試が期日のあとに付けば消える。
    reply(due["plaza_id"], account="kopicha-bsky", kind="trial", result="not_reproduced",
          text=None, by="b", viewer=viewer("kopicha-bsky"), now=now)
    assert _steps(morning.build("kopicha-threads", now=now, mark=False), "add_trial") == []


def test_人向けの次の一手に命令の1行(quiet, capsys):
    now = jst.now_jst()
    due = post(title="朝の問い", trial_due=jst.iso(now - datetime.timedelta(hours=1)))
    morning.render(morning.build("kopicha-threads", now=now, mark=False), out=print)
    out = capsys.readouterr().out
    assert f"追試の結果を足す（予定日 {jst.iso(now - datetime.timedelta(hours=1))}）  kopicha-threads  " \
           f"{due['plaza_id']}  朝の問い" in out
    assert f"    thth plaza reply {due['plaza_id']} --as kopicha-threads --kind trial" in out


def test_施策の期間が前回の観測より後に終わったら広場に置く(quiet):
    now = jst.now_jst()
    ended = post(kind="measure", title="期間が終わった", until=jst.iso(now - datetime.timedelta(hours=2)),
                 now=now - datetime.timedelta(days=7))
    long_ago = post(kind="measure", title="前に終わった", until=jst.iso(now - datetime.timedelta(days=5)),
                    now=now - datetime.timedelta(days=9))
    running = post(kind="measure", title="まだ続く", until=jst.iso(now + datetime.timedelta(days=2)),
                   now=now - datetime.timedelta(days=1))
    steps = _steps(morning.build("kopicha-threads", now=now, mark=False), "post_result")
    assert [s["plaza_id"] for s in steps] == [ended["plaza_id"]]
    # 宣言が無い施策は --from after で道具の数字を付けて置く命令。
    assert steps[0]["command"].startswith("thth plaza post kopicha-threads --kind finding --from after "
                                          "kopicha-threads")
    assert long_ago["plaza_id"] and running["plaza_id"]
    # 他の account の施策は、その account の observe に出す（自分の account の observe には出さない）。
    assert _steps(morning.build("kopicha-bsky", now=now, mark=False), "post_result") == []


def test_宣言のある施策は観測の取り直しの命令(owners):
    now = jst.now_jst()
    declaration = {"schema_version": 1, "id": "d", "account": "kopicha-threads", "hypothesis": "h",
                   "change": "c", "decision": {"status": "proposed"},
                   "baseline_post_ids": [], "changed_post_ids": []}
    ended = post(kind="measure", title="宣言つき", declarations=[declaration],
                 until=jst.iso(now - datetime.timedelta(hours=1)), now=now - datetime.timedelta(days=3))
    configs = {"kopicha-threads": {"project": "kopicha", "media": "threads"}}
    steps = [s for s in plaza_moments.trigger_steps(configs, since=now - datetime.timedelta(days=1),
                                                     now=now) if s["candidate"] == "post_result"]
    assert steps[0]["command"] == (f"thth plaza update {ended['plaza_id']} --as kopicha-threads "
                                   "--refresh --by <名前>")


def _table(before, after, *, last_end="2026-09-06"):
    end = datetime.date.fromisoformat(last_end)
    rows = []
    for week_end, posts in ((end - datetime.timedelta(days=7), before), (end, after)):
        rows.append({"week_start": (week_end - datetime.timedelta(days=6)).isoformat(),
                     "week_end": week_end.isoformat(), "partial": False,
                     "posts": {"reach": 0, "click": 0, "follow": 0, "reply": 0, "none": 0,
                               "unrecorded": 0, **posts}})
    rows.append({"week_start": (end + datetime.timedelta(days=1)).isoformat(),
                 "week_end": (end + datetime.timedelta(days=7)).isoformat(), "partial": True,
                 "posts": {}})
    return {"weeks": rows}


def test_配分を変えた週が閉じたら広場に置く(owners, monkeypatch):
    monkeypatch.setattr(analytics_weekly, "answer",
                        lambda name, weeks, now: _table({"reach": 6, "click": 1}, {"reach": 2, "click": 5}))
    configs = {"kopicha-threads": {"project": "kopicha", "media": "threads"}}
    now = jst.parse("2026-09-07T08:00:00+09:00")
    steps = plaza_moments.trigger_steps(configs, since=jst.parse("2026-09-06T08:00:00+09:00"), now=now)
    week = [s for s in steps if s["candidate"] == "post_week"]
    assert len(week) == 1 and week[0]["week_start"] == "2026-08-31"
    assert week[0]["allocation"]["before"]["reach"] == 6 and week[0]["allocation"]["after"]["click"] == 5
    assert week[0]["allocation"]["denominators"] == [7, 7]
    assert week[0]["command"].startswith("thth plaza post kopicha-threads --kind finding --from "
                                         "analytics-report kopicha-threads --from-window-days 7")
    # 前回の観測がその週が閉じたあとなら出さない（一度だけ）。
    assert not [s for s in plaza_moments.trigger_steps(
        configs, since=jst.parse("2026-09-07T01:00:00+09:00"), now=now) if s["candidate"] == "post_week"]


def test_配分が変わらなければ出さない(owners, monkeypatch):
    monkeypatch.setattr(analytics_weekly, "answer",
                        lambda name, weeks, now: _table({"reach": 6, "click": 2}, {"reach": 5, "click": 2}))
    configs = {"kopicha-threads": {"project": "kopicha", "media": "threads"}}
    steps = plaza_moments.trigger_steps(configs, since=jst.parse("2026-09-06T08:00:00+09:00"),
                                        now=jst.parse("2026-09-07T08:00:00+09:00"))
    assert [s for s in steps if s["candidate"] == "post_week"] == []


def test_CLIで予定日を付けて置ける(owners, capsys, tmp_path):
    body = tmp_path / "b.md"
    body.write_text("夜より朝が伸びる", encoding="utf-8")
    rc = cli.main(["plaza", "post", "kopicha-threads", "--kind", "finding", "--title", "朝",
                   "--body-file", str(body), "--scope", SCOPE, "--trial-due", "2026-10-01",
                   "--by", "k", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert rc == 0
    rc = cli.main(["plaza", "show", result["plaza_id"], "--as", "kopicha-bsky"])
    assert rc == 0 and "追試の予定日: 2026-10-01T00:00:00+09:00" in capsys.readouterr().out
