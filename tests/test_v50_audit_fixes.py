"""5.0 段 1＋2 の監査の直し（Fable の監査・2026-10-28）と画面の読みやすさの柵。

- A1 守り: 鍵の範囲（`allowed_names`）の外の口座の原稿名・状態は、シナリオ経由でも出ない。
- A2 シリーズは「回」で数える（同じ分は口座をまたいで 1 回・`filled` は型の全口座がそろったとき）。
- A3 `events_cli.reaction_events` は公開の名前（旧名も同じもの）。
- A4 `started` も動きも無い糸は、宣言のファイルの更新時刻から停滞を数える（lint は予告）。
- A5 期日の型は `stock_floor` を明示したときだけストック不足を言う。
- A6 階層 1 の合計は、数えられなかったメディアを `totals_cannot_say` で言う。
- A7 `--json` は `</` を `<\\/` にして出す（型に貼っても script が閉じない）。
- A8 枠の一致は前後 `SLOT_TOLERANCE_MINUTES` 分。
- B  画面（型）の言葉: 符丁の表に日本語が全部ある・描いた文字に中の名前や符丁が出ない。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import types

import pytest

from tests.test_v500_scenario import NOW, PROJECT, THREAD_NUM04, _sns, _write, golden_world, kp  # noqa: F401
from thth import cli, dashboard_cli, events_cli, jst, scenario, server_reads

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = os.path.join(ROOT, "skills", "thth", "dashboard")


@pytest.fixture
def now_fixed(monkeypatch):
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)


# ------------------------------------------------------------------ A1 守り（鍵の範囲の外の口座）

AB_THREAD = """---
thth_scenario: 1
kind: thread
id: camp
title: 春の新茶
project: ab
started: 2026-10-20
---
- announce: threads,x need=2
"""

AB_SERIES = """---
thth_scenario: 1
kind: series
id: weekly
title: 週の一杯
project: ab
accounts: alpha, beta
slots: tue 07:00, fri 07:00
---
"""


@pytest.fixture
def ab(isolated_account_factory, nigamilab_repo, now_fixed):
    """project `ab`: alpha（Threads）と beta（X）が 1 つの repo を共有する。beta だけに `thread:` の原稿を置く。"""
    isolated_account_factory("alpha", project="ab")
    isolated_account_factory("beta", project="ab", media="x", handle="ab")
    q = os.path.join(nigamilab_repo, "docs", "sns", "queue")
    scen = os.path.join(nigamilab_repo, "docs", "sns", "scenarios")
    _write(os.path.join(scen, "camp.md"), AB_THREAD)
    _write(os.path.join(scen, "weekly.md"), AB_SERIES)
    _write(os.path.join(q, "th-alpha-camp.md"),
           _sns("alpha", status="draft", publish_at="2026-11-02T08:00:00+09:00", thread="camp"))
    _write(os.path.join(q, "x-beta-secret-camp.md"),
           _sns("beta", status="draft", publish_at="2026-11-02T09:00:00+09:00", thread="camp", media="x"))
    _write(os.path.join(q, "x-beta-secret-weekly.md"),
           _sns("beta", status="approved", publish_at="2026-11-03T07:00:00+09:00", series="weekly", media="x"))
    _write(os.path.join(q, "x-beta-secret-free.md"),
           _sns("beta", status="draft", publish_at="2026-11-04T09:00:00+09:00", media="x"))
    return {"repo": nigamilab_repo, "queue": q, "scen": scen}


def _text(value):
    return json.dumps(value, ensure_ascii=False)


def test_鍵の範囲の外の口座の原稿はシナリオ経由でも見えない(ab):
    allowed = ("alpha",)
    payloads = [
        dashboard_cli.build("ab", now=NOW, allowed_names=allowed, full=True),
        dashboard_cli.build("ab", "campaign", "camp", now=NOW, allowed_names=allowed),
        dashboard_cli.build("ab", "series", "weekly", now=NOW, allowed_names=allowed),
        dashboard_cli.build(now=NOW, allowed_names=allowed),
        scenario.build("ab", now=NOW, allowed_names=allowed),
        scenario.show("ab", "camp", now=NOW, allowed_names=allowed),
        scenario.show("ab", "weekly", now=NOW, allowed_names=allowed),
    ]
    for payload in payloads:
        text = _text(payload)
        assert "x-beta-secret" not in text, payload.get("level") or payload.get("report_type")
        assert '"beta"' not in text
    project = payloads[0]
    camp = next(c for c in project["campaigns"] if c["id"] == "camp")
    # 状態も数えない: 告知の下書きは alpha の 1 本だけ（beta の下書きは段の数にも入らない）。
    assert camp["stages"][0]["drafts"] == 1
    assert project["scenario"]["untied"] == {"drafts": 0, "deliveries": 0}
    detail = payloads[1]["campaign"]
    assert [d["ref"] for d in detail["deliverables"]] == ["th-alpha-camp.md"]
    series = payloads[2]["series"]
    assert series["accounts"] == ["alpha"] and series["items"] == []
    assert series["stock"]["approved_future"] == 0
    assert "series_account_out_of_scope" in series["cannot_say"]
    # 鍵の範囲が無い（手元）なら beta も読む（範囲で絞っただけで、数え方は同じ）。
    whole = dashboard_cli.build("ab", "campaign", "camp", now=NOW)["campaign"]
    assert {d["ref"] for d in whole["deliverables"]} == {"th-alpha-camp.md", "x-beta-secret-camp.md"}


def test_遠くの道の読む口もシナリオを鍵の範囲で絞る(ab):
    from thth import accounts as accounts_mod
    cfg = accounts_mod.load_account("alpha")
    context = types.SimpleNamespace(allowed_accounts=("alpha",))
    for request in ({}, {"full": True}, {"level": "campaign", "id": "camp"}, {"level": "series", "id": "weekly"}):
        got = server_reads._dashboard("alpha", request, cfg, context)
        assert "x-beta-secret" not in _text(got), request


# ------------------------------------------------------------------ A2・A8 シリーズは回で数える

def _series(world, file_name, account, at, media):
    _write(os.path.join(world["queue"], file_name),
           _sns(account, status="approved", publish_at=at, series="num", media=media))


def test_同じ分の2口座は1回で型の全口座がそろえば埋まる(golden_world):
    _series(golden_world, "x-num-1103.md", "kp-x", "2026-11-03T07:00:00+09:00", "x")
    row = scenario.build(PROJECT, now=NOW)["series"][0]
    assert row["stock"]["approved_future"] == 1 and row["stock"]["approved_future_items"] == 2
    up = scenario.show(PROJECT, "num", now=NOW)["series"]["slots_upcoming"]
    assert up[1]["filled"] is True and up[1]["filled_by"] == ["kp-threads", "kp-x"]
    assert row["empty_slots_14d"] == 3


def test_片方の口座だけなら埋まっていない(golden_world):
    row = scenario.build(PROJECT, now=NOW)["series"][0]
    gap = next(s for s in row["empty_slots"] if s["at"] == "2026-11-03T07:00:00+09:00")
    assert gap["filled_by"] == ["kp-threads"]
    assert row["stock"]["approved_future"] == 1


def test_枠の前後15分は同じ回で外れれば別の回(golden_world):
    assert scenario.SLOT_TOLERANCE_MINUTES == 15
    _series(golden_world, "x-num-1103.md", "kp-x", "2026-11-03T07:10:00+09:00", "x")
    row = scenario.build(PROJECT, now=NOW)["series"][0]
    assert row["stock"]["approved_future"] == 1 and row["empty_slots_14d"] == 3
    _series(golden_world, "x-num-1103.md", "kp-x", "2026-11-03T07:20:00+09:00", "x")
    row = scenario.build(PROJECT, now=NOW)["series"][0]
    assert row["stock"]["approved_future"] == 2 and row["empty_slots_14d"] == 4


# ------------------------------------------------------------------ A3

def test_反応の出来事は公開の名前で旧名も同じもの():
    assert events_cli.reaction_events is events_cli._reaction_events
    assert "events_cli.reaction_events(" in open(dashboard_cli.__file__, encoding="utf-8").read()


# ------------------------------------------------------------------ A4 started も動きも無い糸

def test_startedも動きも無い糸は宣言のファイルの時刻から停滞する(kp):
    path = _write(os.path.join(kp["scen"], "quiet.md"),
                  "---\nthth_scenario: 1\nkind: thread\nid: quiet\ntitle: 静かな糸\n---\n"
                  "- announce: threads need=1\n")
    old = (NOW - datetime.timedelta(days=10)).timestamp()
    os.utime(path, (old, old))
    row = scenario.build(PROJECT, now=NOW)["threads"][0]
    assert row["state"] == "stalled"
    assert row["stalled"]["since_from"] == "file_mtime" and row["stalled"]["days"] == 10
    codes = [(p["code"], p["id"]) for p in scenario.lint(PROJECT, now=NOW)["problems"]]
    assert ("started_missing", "quiet") in codes
    # 新しく置いたばかりなら停滞しない。
    os.utime(path, (NOW.timestamp(), NOW.timestamp()))
    assert scenario.build(PROJECT, now=NOW)["threads"][0]["state"] == "active"


# ------------------------------------------------------------------ A5 期日の型

DEADLINE = """---
thth_scenario: 1
kind: series
id: news
title: 速報
project: kp
deadline: true
{floor}---
"""


def test_期日の型はstock_floorを明示したときだけストック不足(kp):
    path = os.path.join(kp["scen"], "news.md")
    _write(path, DEADLINE.format(floor=""))
    row = scenario.build(PROJECT, now=NOW)["series"][0]
    assert row["stock"]["low"] is False and row["stock"]["floor"] is None
    assert not [e for e in events_cli.build(PROJECT, now=NOW)["events"] if e["kind"] == "series_low"]
    _write(path, DEADLINE.format(floor="stock_floor: 1\n"))
    row = scenario.build(PROJECT, now=NOW)["series"][0]
    assert row["stock"]["low"] is True and row["stock"]["floor"] == 1
    assert [e for e in events_cli.build(PROJECT, now=NOW)["events"] if e["kind"] == "series_low"]


# ------------------------------------------------------------------ A6 合計の数えられなかったメディア

def test_合計は読めないメディアを黙って飛ばさない(golden_world, now_fixed, monkeypatch):
    whole = dashboard_cli.build(now=NOW)
    assert whole["totals_cannot_say"] == {}
    from thth import summary_cli

    def broken(*a, **k):
        raise OSError("summary")
    monkeypatch.setattr(summary_cli, "build", broken)
    got = dashboard_cli.build(now=NOW)
    assert got["totals_cannot_say"]["published_7d"] == [PROJECT]


# ------------------------------------------------------------------ A7 --json は </ を <\/ に

def test_jsonの出力にscriptを閉じる字を生で出さない(golden_world, now_fixed, capsys):
    path = os.path.join(golden_world["scen"], "num-04-sencha.md")
    _write(path, THREAD_NUM04.replace("title: 数字で淹れる No.4 煎茶", "title: 煎茶</script><b>x"))
    for argv in (["dashboard", PROJECT, "--json"], ["dashboard", PROJECT, "campaign", "num-04-sencha", "--json"],
                 ["scenario", PROJECT, "--json"], ["scenario", "show", PROJECT, "num-04-sencha", "--json"]):
        assert cli.main(argv) == 0
        out = capsys.readouterr().out
        assert "</script>" not in out and "<\\/script>" in out, argv
        assert "煎茶</script><b>x" in out.replace("<\\/", "</")
        json.loads(out)   # JSON としては正しいまま（`\/` は `/`）
    assert dashboard_cli.dumps({"t": "a</b"}) == '{"t": "a<\\/b"}'
    assert json.loads(dashboard_cli.dumps({"t": "a</b"})) == {"t": "a</b"}


# ------------------------------------------------------------------ B 画面の言葉

def _template(level):
    with open(os.path.join(TEMPLATES, f"{level}.html"), encoding="utf-8") as f:
        return f.read()


def _table(name):
    body = re.search(r"const " + name + r"=\{(.*?)\};", _template("project")).group(1)
    return set(re.findall(r'(\w+):"', body))


def _source_codes():
    """THTH が `cannot_say` に入れる符丁（events・summary・scenario・dashboard の源から拾う）。"""
    codes = set()
    for module in ("events_cli", "summary_cli", "scenario", "dashboard_cli"):
        with open(os.path.join(ROOT, "thth", module + ".py"), encoding="utf-8") as f:
            text = f.read()
        codes |= set(re.findall(r'(?:cannot|cannot_say|local)\.(?:add|append)\("([a-z_]+)"\)', text))
        codes |= set(re.findall(r'why\["[a-z_]+"\] = "([a-z_]+)"', text))
        codes |= {c + "_unreadable" for c in re.findall(r'_cell\("([a-z_]+)"', text)}
    codes |= {f"{label}_unreadable" for label in ("summary", "summary_previous")}
    codes |= set(dashboard_cli.NOT_COLLECTED.values())
    codes |= {"no_declarations", "scenario_not_installed", "scenario_schema_unknown", "full_trimmed",
              "needs_scenario_show", "accounts_not_summed"}
    return codes


def test_型の符丁の表は理由を全部日本語で言える():
    reasons = _table("THTH_REASON")
    codes = _source_codes()
    assert len(codes) > 30          # 拾い損ねていない（拾う式が壊れたら気づく）
    missing = {c for c in codes if c not in reasons and not (c.startswith("scenario_") and c[9:] in reasons)}
    assert not missing, missing
    # 要対応に出る種類（続いている状態と期限切れ）は、型の表と THTH の表の両方に日本語がある。
    kinds = _table("THTH_KIND")
    assert set(dashboard_cli.KIND_JA) == kinds
    points = {"published", "retracted", "reply_received", "mention_received", "delivery_published",
              "delivery_updated", "delivery_deleted", "delivery_unknown", "delivery_conflict", "bookmarked",
              "bookmark_deleted", "delivery_resolved", "bookmark_received"}
    assert set(events_cli.KINDS) - points <= kinds


FORBIDDEN = (re.compile(r"[A-Za-z0-9]+_[A-Za-z0-9_]+"), re.compile(r"\.md\b"),
             re.compile(r"num-04-sencha|sousa-026|kp-threads|kp-x|kp-hatenablog|dl_0"))


def test_要対応の文に中の名前や符丁を出さない(golden_world, now_fixed):
    got = dashboard_cli.build(PROJECT, now=NOW, full=True)
    texts = [row["title"] for row in got["attention"]] + [row["next"] for row in got["attention"] if row["next"]]
    texts += [row["head"] for row in got["pending_approval"] if row.get("head")]
    for day in got["calendar"]:
        texts += [item["head"] for item in day["items"] if item.get("head")]
    for text in texts:
        for pattern in FORBIDDEN:
            assert not pattern.search(text), text
    assert any(t == "数字で淹れる No.4 煎茶 — 本記事が未公開" for t in texts)


def test_カレンダーの橙はシリーズの空き枠と薄い日だけ(golden_world, now_fixed):
    got = dashboard_cli.build(PROJECT, now=NOW)
    gaps = {d["date"] for d in got["calendar"] if d["gap"]}
    # 次の 14 日窓のうち、この 2 週間の暦に入る空き枠（10/30・11/3・11/6）だけ。11/3 は X が未。
    assert gaps == {"2026-10-30", "2026-11-03", "2026-11-06"}
    empty = [d for d in got["calendar"] if d.get("empty") and not d["gap"]]
    assert empty and all("gap" not in [i["status"] for i in d["items"]] for d in empty)
    gap_items = [i for d in got["calendar"] for i in d["items"] if i["status"] == "gap"]
    assert any(i["filled_by"] == ["kp-threads"] and "Threads だけ" in i["head"] for i in gap_items)
    week = dashboard_cli.build(now=NOW)["projects"][0]["week"]
    assert {w["date"] for w in week if w["gap"]} <= gaps and all("empty" in w for w in week)


def _render_texts(levels):
    """型に本物の答えを貼り、chromium で描いた文字（innerText）。playwright が無ければ飛ばす。"""
    sync_api = pytest.importorskip("playwright.sync_api")
    out = {}
    try:
        with sync_api.sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception:  # noqa: BLE001 — 同梱の chromium が無ければ手元の Chrome で
                browser = p.chromium.launch(channel="chrome")
            for level, html in levels.items():
                page = browser.new_page()     # 型ごとに新しい頁（const を 2 度宣言しない）
                errors = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                page.set_content(html)
                out[level] = (page.inner_text("#thth-root"), errors)
                page.close()
            browser.close()
    except Exception as exc:  # noqa: BLE001 — 描く道具が無い環境（CI）では飛ばす
        pytest.skip(f"chromium が使えない: {type(exc).__name__}")
    return out


@pytest.mark.skipif(not os.environ.get("THTH_DASHBOARD_RENDER"), reason="描いて確かめるときだけ（chromium を使う）")
def test_描いた画面に中の名前や符丁が出ない(golden_world, now_fixed):
    from tests.test_v50_joined import LEVELS, _paste, _with_outcomes
    _with_outcomes(golden_world)
    levels = {level: _paste(level, dashboard_cli.build(*args, now=NOW))[1] for level, args in LEVELS.items()}
    texts = _render_texts(levels)
    for level, (text, errors) in texts.items():
        assert not errors, (level, errors)
        assert len(text) > 100 and "貼ってください" not in text, level
        for pattern in FORBIDDEN:
            found = pattern.findall(text)
            assert not found, (level, found)
    project = texts["project"][0]
    assert "数字で淹れる No.4 煎茶" in project and "本記事が未公開" in project
