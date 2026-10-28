"""5.0 段 2: `thth dashboard`（`thth.dashboard/1`・会話の中の 3 階層のダッシュボード・設計 5.0 §5）。

- 既存の計算（summary・events・schedule・承認待ち）を束ねるだけ。書かない・媒体を呼ばない。
- シナリオは段 1 の `thth.scenario/1`。入力の見本は `tests/fixtures/scenario_v1_golden.json`（段 1 と同じファイル）。
- 要対応は days の大きい順だけ。押すところの指示文は THTH が作り `[project/種類:id]` を入れる。
- 階層 1 の合計は本数だけ（インプレッションは口座をまたいで足さない）。
- 他人の本文・絶対パスを出さない。題・head・next は 60 字まで。
- 型（`skills/thth/dashboard/*.html`）は `--json` の答えを貼るだけで描ける（差し込み口は 1 つ・鍵を参照）。
"""
from __future__ import annotations

import json
import os
import re

import pytest

from thth import cli, dashboard_cli, jst
from tests.test_v3111_observe_counts_only import rich  # noqa: F401
from tests.test_v310_morning import ACCOUNT, NOW, PROJECT, one  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(ROOT, "tests", "fixtures", "scenario_v1_golden.json")
TEMPLATES = os.path.join(ROOT, "skills", "thth", "dashboard")
LEVEL_FILES = {"all": "all.html", "project": "project.html", "campaign": "campaign.html", "series": "series.html"}
SLOT = re.compile(r'<script type="application/json" id="thth-data">(.*?)</script>', re.S)


def golden():
    with open(GOLDEN, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def with_scenario(rich, monkeypatch):  # noqa: F811
    monkeypatch.setattr(dashboard_cli, "_load_scenario", lambda project, now=None, allowed_names=None: (golden(), None))
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    return rich


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def keys(value, found=None):
    found = set() if found is None else found
    if isinstance(value, dict):
        for key, child in value.items():
            found.add(key)
            keys(child, found)
    elif isinstance(value, list):
        for child in value:
            keys(child, found)
    return found


# ------------------------------------------------------------------ 1. 契約

def test_goldenは段1の契約の鍵を持つ():
    data = golden()
    assert data["schema"] == "thth.scenario/1" == dashboard_cli.SCENARIO_SCHEMA
    thread, series = data["threads"][0], data["series"][0]
    assert {"id", "title", "state", "stage", "stages", "next", "last_movement_at", "stalled", "events"} <= set(thread)
    assert {"id", "title", "accounts", "slots", "stock", "next_slot", "empty_slots_14d", "material"} <= set(series)
    assert set(data["untied"]) == {"drafts", "deliveries"}


def test_メディアの階層は既存の計算を束ねる(with_scenario):
    got = dashboard_cli.build(PROJECT, now=NOW)
    assert got["schema"] == "thth.dashboard/1" and got["level"] == "project" and got["project"] == PROJECT
    assert got["accounts"] == [ACCOUNT]
    assert got["scenario"]["available"] is True and got["scenario"]["n_campaigns_stalled"] == 1
    kinds = [row["kind"] for row in got["attention"]]
    # 段 1 の答えから写した停滞・ストック不足・未分類、summary の未返信。
    assert {"scenario_stalled", "series_low", "untied_drafts", "unanswered_replies"} <= set(kinds)
    assert got["sort_basis"] == "days_desc"
    days = [row["days"] for row in got["attention"] if row["days"] is not None]
    assert days == sorted(days, reverse=True)
    # 未返信の数は summary と同じ（二重に数えない）。
    from thth import summary_cli
    n = summary_cli.build(PROJECT, now=NOW)["by_account"][ACCOUNT]["unanswered_replies"]["n"]
    assert next(r for r in got["attention"] if r["kind"] == "unanswered_replies")["title"].endswith(f"{n} 件")
    # 時刻超過の下書きは承認済みなので承認待ちには入らない。KPI は本数。
    assert got["kpi"]["pending_approval"]["n"] == len(got["pending_approval"])
    assert got["kpi"]["impressions_7d"]["summed"] is False
    assert set(got["as_of"]) >= {"generated_at", "measured", "schedule", "events"}
    assert len(got["calendar"]) == dashboard_cli.CALENDAR_DAYS
    assert [c["id"] for c in got["campaigns"]] == ["num-04-sencha", "sousa-026"]
    assert [s["id"] for s in got["series"]] == ["num"]


def test_要対応はdaysの大きい順だけで日数の無いものは後ろ(with_scenario, monkeypatch):
    data = golden()
    second = json.loads(json.dumps(data["threads"][0]))
    second.update(id="sayama", title="狭山の霜", stalled={"days": 12, "stale_after_days": 7, "since": None})
    third = json.loads(json.dumps(data["threads"][0]))
    third.update(id="wakocha", title="和紅茶", stalled={"days": 2, "stale_after_days": 7, "since": None})
    data["threads"] += [second, third]
    monkeypatch.setattr(dashboard_cli, "_load_scenario", lambda project, now=None, allowed_names=None: (data, None))
    rows = dashboard_cli.build(PROJECT, now=NOW)["attention"]
    assert [r["ref"] for r in rows[:3]] == ["sayama", "num-04-sencha", "wakocha"]
    assert all(r["days"] is None for r in rows[3:])


def test_全メディアはCLIからも引ける(with_scenario, capsys):
    assert cli.main(["dashboard", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["level"] == "all"


def test_押すところの指示文はTHTHが作り角括弧のrefを入れる(with_scenario):
    got = dashboard_cli.build(PROJECT, now=NOW, full=True)
    prompts = [a["prompt"] for a in _actions(got)]
    assert prompts and all(p.startswith("ダッシュボードから: [" + PROJECT) for p in prompts)
    assert any("[kopicha/campaign:num-04-sencha]" in p for p in prompts)
    assert any("[kopicha/series:num]" in p for p in prompts)
    # 取り下げと承認の確定は置かない。戻せないものには印。
    assert not any(re.search(r"retract|取り下げ|--confirm", p) for p in prompts)
    irreversible = [a for a in _actions(got) if a["irreversible"]]
    assert irreversible and all("戻せない" in a["prompt"] for a in irreversible)


def _actions(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "actions" and isinstance(child, list):
                yield from child
            else:
                yield from _actions(child)
    elif isinstance(value, list):
        for child in value:
            yield from _actions(child)


def test_他人の本文と絶対パスを出さず題は60字まで(with_scenario, monkeypatch):
    data = golden()
    data["threads"][0]["title"] = "あ" * 200
    data["threads"][0]["declared_in"] = "/Users/someone/repo/docs/sns/scenarios/x.md"
    data["threads"][0]["events"][0]["comment"] = "他人のコメント"
    monkeypatch.setattr(dashboard_cli, "_load_scenario", lambda project, now=None, allowed_names=None: (data, None))
    got = dashboard_cli.build(PROJECT, now=NOW, full=True)
    text = list(strings(got))
    assert "他人のコメント" not in text and "返信" not in [s for s in text if s == "返信"]
    assert not keys(got) & {"body", "text", "comment", "comments", "username", "permalink"}
    assert not [s for s in text if s.startswith("/") or "/Users/" in s]
    for row in got["campaigns"] + got["campaign_details"]:
        assert len(row["title"]) <= dashboard_cli.TEXT_CHARS
    for row in got["attention"]:
        assert row["title"] is None or len(row["title"]) <= 60
        assert row["next"] is None or len(row["next"]) <= 60
    for day in got["calendar"]:
        for item in day["items"]:
            assert item["head"] is None or len(item["head"]) <= 60


def test_段1が無ければavailable_falseで他は出す(rich, monkeypatch):  # noqa: F811
    monkeypatch.setattr(dashboard_cli, "_scenario_module", lambda: None)
    got = dashboard_cli.build(PROJECT, now=NOW)
    assert got["scenario"] == {"available": False, "cannot_say": "scenario_not_installed"}
    assert got["campaigns"] == [] and got["series"] == []
    assert "kpi" in got and got["calendar"]
    detail = dashboard_cli.build(PROJECT, "campaign", "num-04-sencha", now=NOW)
    assert detail["campaign"] is None and "scenario_not_installed" in detail["cannot_say"]


def test_全メディアの合計は本数だけ(with_scenario):
    got = dashboard_cli.build(now=NOW)
    assert got["level"] == "all" and got["n_projects"] == 1
    assert set(got["totals"]) == {"attention", "pending_approval", "scheduled_14d", "published_7d"}
    assert all(isinstance(v, int) for v in got["totals"].values())
    card = got["projects"][0]
    assert card["project"] == PROJECT and len(card["week"]) == 7
    assert card["kpi"]["impressions_7d"]["summed"] is False
    assert card["first_attention"]["kind"] == got["projects"][0]["first_attention"]["kind"]
    assert "[kopicha]" in card["actions"][0]["prompt"]


def test_キャンペーンとシリーズの階層(with_scenario):
    camp = dashboard_cli.build(PROJECT, "campaign", "num-04-sencha", now=NOW)
    assert camp["level"] == "campaign" and camp["campaign"]["id"] == "num-04-sencha"
    out = camp["campaign"]["outcomes"]
    # 今の THTH で取れない数は null と理由（0 と混ぜない）。
    assert out["blog_pv"] is None and out["cannot_say"]["blog_pv"] == "blog_pv_not_collected"
    assert out["article_clicks"] is None
    assert [a["label"] for a in camp["campaign"]["actions"]][0] == "公開した"
    ser = dashboard_cli.build(PROJECT, "series", "num", now=NOW)
    assert ser["series"]["stock"]["low"] is True and ser["series"]["next_slot"]["filled"] is False
    with pytest.raises(dashboard_cli.DashboardError):
        dashboard_cli.build(PROJECT, "campaign", "nai", now=NOW)


def test_CLIのjsonと端末の表示(with_scenario, capsys):
    assert cli.main(["dashboard", PROJECT, "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["level"] == "project"
    assert cli.main(["dashboard", PROJECT, "series", "num", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["series"]["id"] == "num"
    assert cli.main(["dashboard", PROJECT]) == 0
    assert "ダッシュボード" in capsys.readouterr().out
    assert cli.main(["dashboard", "dare-mo-inai", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["target_unknown"]


def test_何も書かない(with_scenario):
    from tests.test_v3111_observe_counts_only import ACCOUNT as _  # noqa: F401
    from thth import accounts
    root = accounts.state_dir_for(ACCOUNT)

    def snapshot():
        out = {}
        for base, _dirs, files in os.walk(root):
            for name in files:
                path = os.path.join(base, name)
                out[path] = os.path.getmtime(path)
        return out
    before = snapshot()
    dashboard_cli.build(PROJECT, now=NOW, full=True)
    dashboard_cli.build(now=NOW)
    assert snapshot() == before


# ------------------------------------------------------------------ 2. 遠くの道

def test_サーバの読む口はdashboardを鍵の範囲で返し宣言が無ければno_declarations(env, monkeypatch):  # noqa: F811
    from thth.report_service import execute_report
    monkeypatch.setattr(dashboard_cli, "_scenario_module", lambda: None)
    got = execute_report(env["context"], {"operation": "dashboard", "account": "alpha"})
    assert got["schema"] == "thth.dashboard/1" and got["level"] == "project"
    assert "beta" not in got["accounts"]
    assert got["scenario"] == {"available": False, "cannot_say": "no_declarations"}


def test_遠くの道のdashboardは同じ口を呼ぶ(home, worker, capsys):  # noqa: F811
    from tests.test_v3140_remote_cli import run as remote_run
    rc, out, _ = remote_run(capsys, "dashboard", RACCOUNT, "campaign", "c1", "--json")
    assert rc == 0 and json.loads(out)["operation"] == "dashboard"
    assert worker.calls[-1]["path"] == "/api/v1/dashboard"
    assert worker.calls[-1]["body"] == {"account": RACCOUNT, "level": "campaign", "id": "c1"}


from tests.test_v212_server_writes import env  # noqa: E402,F401
from tests.test_v3140_remote_cli import ACCOUNT as RACCOUNT, home, worker  # noqa: E402,F401


# ------------------------------------------------------------------ 3. 型（JSON を貼るだけで描ける）

def _template(level):
    with open(os.path.join(TEMPLATES, LEVEL_FILES[level]), encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("level", list(LEVEL_FILES))
def test_型の差し込み口は1つで描くのは型のJS(level):
    html = _template(level)
    slots = SLOT.findall(html)
    assert len(slots) == 1, "差し込み口（thth-data）はちょうど 1 つ"
    # 型は数字を持たない（差し込み口の中は空の見本）。
    assert json.loads(slots[0] or "{}").get("schema") in (None, "thth.dashboard/1")
    assert 'getElementById("thth-data")' in html
    assert f'"{level}"' in html                 # 階層を確かめてから描く
    assert "thth.dashboard/1" in html
    assert "sendPrompt" in html and "clipboard" in html   # sendPrompt の無い環境の退路
    # 色は会話の側の変数だけ（16 進の色を直書きしない）。
    style = "".join(re.findall(r"<style>(.*?)</style>", html, re.S))
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", style)


def test_型の共通の部分は4つで同じ():
    marks = (("/* ---- 共通の見た目（4 つの型で同じ） ---- */", "/* ---- 共通ここまで ---- */"),
             ("/* ---- 共通（4 つの型で同じ。直すなら 4 つとも同じに） ---- */", "/* ---- 共通ここまで ---- */"))
    for start, end in marks:
        blocks = set()
        for level in LEVEL_FILES:
            html = _template(level)
            i = html.index(start)
            blocks.add(html[i:html.index(end, i)])
        assert len(blocks) == 1, start


# 型が参照する `--json` の鍵（階層ごと）。golden を通した答えにこの鍵が無ければ型は描けない。
TEMPLATE_KEYS = {
    "all": ("totals", "projects", "first_attention", "week", "kpi", "mediums", "actions", "as_of"),
    "project": ("kpi", "attention", "pending_approval", "campaigns", "series", "calendar", "scenario", "as_of"),
    "campaign": ("campaign", "steps", "outcomes", "deliverables", "history", "actions"),
    "series": ("series", "stock", "next_slot", "material", "outcomes", "items", "actions"),
}


@pytest.mark.parametrize("level", list(LEVEL_FILES))
def test_型が読む鍵はgoldenを通した答えにある(with_scenario, level):
    args = {"all": (), "project": (PROJECT,), "campaign": (PROJECT, "campaign", "num-04-sencha"),
            "series": (PROJECT, "series", "num")}[level]
    payload = dashboard_cli.build(*args, now=NOW)
    present = keys(payload)
    html = _template(level)
    for key in TEMPLATE_KEYS[level]:
        assert key in present, f"{level}: --json に {key} が無い"
        assert re.search(r"\b" + re.escape(key) + r"\b", html), f"{level}: 型が {key} を読んでいない"


@pytest.mark.skipif(not os.environ.get("THTH_DASHBOARD_DUMP"), reason="見本を書き出すときだけ（人が型を目で見る用）")
def test_goldenを型に貼った見本を書き出す(with_scenario):
    out = os.environ["THTH_DASHBOARD_DUMP"]
    # 見た目を確かめるための予定と公開を 1 本ずつ足す（自分の原稿・数字は例）。
    import datetime
    from tests.conftest import write_queue_file
    from thth import accounts, sent
    write_queue_file(with_scenario["queue_dir"], "next.md",
                     fm_overrides={"account": ACCOUNT, "status": "approved",
                                   "publish_at": jst.iso(NOW + datetime.timedelta(days=1, hours=5))},
                     body="## threads\n\n煎茶は 70℃ で 1 分。数字で淹れる No.4\n")
    write_queue_file(with_scenario["queue_dir"], "draft.md",
                     fm_overrides={"account": ACCOUNT, "status": "draft",
                                   "publish_at": jst.iso(NOW + datetime.timedelta(days=3))},
                     body="## threads\n\n今日の一杯の下書き\n")
    sent.write(accounts.state_dir_for(ACCOUNT), post_id="P-RECENT", text="ほうじ茶は 90℃ で 30 秒",
               body_hash="h2", sent_at=jst.iso(NOW - datetime.timedelta(days=1)))
    for level, args in {"all": (), "project": (PROJECT,), "campaign": (PROJECT, "campaign", "num-04-sencha"),
                        "series": (PROJECT, "series", "num")}.items():
        payload = json.dumps(dashboard_cli.build(*args, now=NOW), ensure_ascii=False)
        html = SLOT.sub(lambda m: '<script type="application/json" id="thth-data">' + payload.replace("</", "<\\/")
                        + "</script>", _template(level), count=1)
        with open(os.path.join(out, level + ".json"), "w", encoding="utf-8") as f:
            f.write(payload)
        with open(os.path.join(out, level + ".html"), "w", encoding="utf-8") as f:
            f.write(html)
