"""5.0 段 1: シナリオ（キャンペーン＝糸・シリーズ＝型）の宣言を読み、現在地を毎回計算する（設計 5.0 §2〜§4・§6）。

見るのは:
- 宣言のファイル（`docs/sns/scenarios/<id>.md`・`thth_scenario: 1`）と原稿の札（`thread:`・`series:`）から、
  停滞のキャンペーン・予定ありのキャンペーン・ストック不足のシリーズ・未分類の原稿が出る。
- `--json` が golden（`tests/fixtures/scenario_v1_golden.json`）と同じ形（段 2 の書き手が同じファイルを入力に使う）。
- `thth events` の `scenario_stalled`・`series_low`（続いている状態・id の決め方）。
- 札は承認の指紋に入らない（札を足しても approved_sha が 1 バイトも変わらない）。
- lint は予告だけ（`thread_not_declared`・断らない）。
- 宣言を持たない口座は、events も lint も今までと同じ。
- 本文・絶対パスを出さない。読めない台帳は `cannot_say`（0 と混ぜない）。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import pathlib

import pytest

from tests.conftest import commit_and_push_path
from tests.hatena_s1_helpers import account_data, principal_data
from thth import (accounts as accounts_mod, approval, cli, deliveries, events_cli, fingerprint_golden, jst,
                  lint as lint_mod, scenario, sent)

NOW = datetime.datetime(2026, 10, 28, 11, 20, tzinfo=jst.JST)
PROJECT = "kp"
GOLDEN = pathlib.Path(__file__).resolve().parent / "fixtures" / "scenario_v1_golden.json"
SECRET_BODY = "本文はここに書く秘密の一文"


def _write(path, text, *, commit=True):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if commit:
        commit_and_push_path(path, message=f"test: {os.path.basename(path)}")
    return path


def _sns(account, *, status, publish_at, thread=None, series=None, extra=None, media="threads"):
    lines = ["---", "thth: 1", f"account: {account}", f"publish_at: {publish_at}", f"status: {status}"]
    if thread:
        lines.append(f"thread: {thread}")
    if series:
        lines.append(f"series: {series}")
    for key, value in (extra or {}).items():
        lines.append(f"{key}: {value}")
    lines += ["---", "", f"## {media}", "", SECRET_BODY, ""]
    return "\n".join(lines)


def _hb(*, status, publish_at, thread=None, delivery=None):
    lines = ["---", "thth: 1", "account: kp-hatenablog", f"status: {status}", f"publish_at: {publish_at}",
             "article: kp/sencha", "article_source: articles/sencha.md", "form: full", "title: 煎茶の数字",
             "op: create"]
    if delivery:
        lines.append(f"delivery: {delivery}")
    if thread:
        lines.append(f"thread: {thread}")
    lines += ["---", "", "## hatenablog", "", "（使わない）", ""]
    return "\n".join(lines)


THREAD_NUM04 = """---
thth_scenario: 1
kind: thread
id: num-04-sencha
title: 数字で淹れる No.4 煎茶
goal_text: 煎茶の湯温を数字で覚えてもらう
project: kp
stale_after_days: 7
---
# 段（上から順に満たす）

- collect: hatena_bookmark tag=num-04 need=5
- diary: hatenablog need=1
- article: site signal=article_published
- announce: threads,x need=2
- warm: threads,x replies_within_hours=6

# 出来事（窓口が書く・THTH は読むだけ）

- 2026-10-15 researched 全国の目安を 5 本はてブ
"""

THREAD_SOUSA = """---
thth_scenario: 1
kind: thread
id: sousa-026
title: 狭山の霜
project: kp
---
- collect: hatena_bookmark need=3
- diary: hatenablog need=1
- article: site
- announce: threads,x need=2
- warm: threads,x
"""

SERIES_NUM = """---
thth_scenario: 1
kind: series
id: num
title: 数字で淹れる
project: kp
accounts: kp-threads, kp-x
slots: tue 07:00, fri 07:00
stock_floor: 2
material_remaining: 4
material_written_at: 2026-10-23
---
"""


def _bookmark(state, url, tags, at):
    sent.write(state, post_id=sent.bookmark_post_id(url), text="", body_hash="0" * 64, sent_at=jst.iso(at),
               kind="bookmark_add", url=url, url_canonical=url, target="external", tags=tags, result="confirmed")


@pytest.fixture
def kp(isolated_account_factory, nigamilab_repo, thth_root):
    """project `kp`: Threads・X・はてなブログの 3 口座が 1 つの repo を共有する。"""
    th = isolated_account_factory("kp-threads", project=PROJECT)
    isolated_account_factory("kp-x", project=PROJECT, media="x", handle="kp")
    principals = pathlib.Path(th["accounts_dir"]).parent / "principals"   # 台帳の置き場の隣（principals.dir）
    principals.mkdir(mode=0o700, exist_ok=True)
    token = pathlib.Path(thth_root) / "secrets" / "kp.token"
    p = principals / "kp-hatena.json"
    p.write_text(json.dumps(principal_data(
        "kp-hatena", token=token, blogs=[{"blog_id": "kp.hatenablog.com", "lanes": [PROJECT], "syntax": "markdown"}],
        bookmark={"lanes": [PROJECT]}), ensure_ascii=False), encoding="utf-8")
    os.chmod(p, 0o600)
    data = account_data("kp-hatenablog", project=PROJECT, principal="kp-hatena", blog_id="kp.hatenablog.com",
                        token=token, repo_dir=nigamilab_repo)
    with open(os.path.join(th["accounts_dir"], "kp-hatenablog.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    repo = nigamilab_repo
    return {"repo": repo, "queue": os.path.join(repo, "docs", "sns", "queue"),
            "scen": os.path.join(repo, "docs", "sns", "scenarios")}


@pytest.fixture
def golden_world(kp):
    """golden の見本: 停滞のキャンペーン 1・予定ありのキャンペーン 1・ストック不足のシリーズ 1・未分類 3。"""
    q = kp["queue"]
    _write(os.path.join(kp["scen"], "num-04-sencha.md"), THREAD_NUM04)
    _write(os.path.join(kp["scen"], "sousa-026.md"), THREAD_SOUSA)
    _write(os.path.join(kp["scen"], "num.md"), SERIES_NUM)
    hb_state = accounts_mod.state_dir_for("kp-hatenablog")
    for i in range(5):
        _bookmark(hb_state, f"https://example.com/num04/{i}", ["num-04", "煎茶"],
                  datetime.datetime(2026, 10, 14, 9, i, tzinfo=jst.JST))
    for i in range(3):
        _bookmark(hb_state, f"https://example.com/sousa/{i}", ["sousa-026"],
                  datetime.datetime(2026, 10, 26, 9, i, tzinfo=jst.JST))
    # num-04 の日記: 配信の台帳で 10/19 に公開済み。
    record = deliveries.create("kp-hatenablog", principal="kp-hatena", blog_id="kp.hatenablog.com",
                               article="kp/sencha", at="2026-10-18T20:00:00+09:00", delivery="dl_00000000a004")
    deliveries.advance("kp-hatenablog", record["delivery"], state="published", at="2026-10-19T08:38:22+09:00")
    _write(os.path.join(q, "hb-num04.md"), _hb(status="posted", publish_at="2026-10-19T08:30:00+09:00",
                                                thread="num-04-sencha", delivery="dl_00000000a004"))
    # num-04 の告知の下書き（X・まだ承認していない）。
    _write(os.path.join(q, "x-num04-announce.md"),
           _sns("kp-x", status="draft", publish_at="2026-11-02T08:00:00+09:00", thread="num-04-sencha", media="x"))
    # sousa-026 の日記: 承認済みで 11/12 に出る予定（配信の台帳はまだ無い）。
    _write(os.path.join(q, "hb-sousa-026.md"), _hb(status="approved", publish_at="2026-11-12T12:00:00+09:00",
                                                    thread="sousa-026"))
    # シリーズ num: 承認済みの未来は 1 本（11/3 火 07:00）。次の枠 10/30 金 07:00 は空き。
    _write(os.path.join(q, "th-num-1103.md"),
           _sns("kp-threads", status="approved", publish_at="2026-11-03T07:00:00+09:00", series="num"))
    # 未分類 3（札の無い原稿）。
    _write(os.path.join(q, "th-free-1.md"), _sns("kp-threads", status="draft", publish_at="2026-11-01T12:00:00+09:00"))
    _write(os.path.join(q, "th-free-2.md"), _sns("kp-threads", status="draft", publish_at="2026-11-02T12:00:00+09:00"))
    _write(os.path.join(q, "x-free-3.md"),
           _sns("kp-x", status="approved", publish_at="2026-11-03T12:00:00+09:00", media="x"))
    return kp


def test_fixture_accounts_are_readable(kp):
    for name in ("kp-threads", "kp-x", "kp-hatenablog"):
        assert accounts_mod.load_account(name)["project"] == PROJECT


# ------------------------------------------------------------------ golden の形

def _shape(value):
    """値の形（dict は鍵ごと・list は要素ごと・それ以外は型の名前）。null は null。"""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(v) for v in value]
    if value is None:
        return None
    return "number" if isinstance(value, (int, float)) and not isinstance(value, bool) else type(value).__name__


def test_scenario_json_matches_golden_shape(golden_world):
    payload = scenario.build(PROJECT, now=NOW)
    if os.environ.get("THTH_WRITE_SCENARIO_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert golden["schema"] == "thth.scenario/1"
    assert _shape(payload) == _shape(golden)
    assert payload == golden   # 見本は時刻を固定してあるので、値まで同じ


def test_golden_contains_the_representative_cases():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    states = {t["id"]: t for t in golden["threads"]}
    assert states["num-04-sencha"]["state"] == "stalled"
    assert states["num-04-sencha"]["stage"] == {"name": "article", "index": 3, "of": 5}
    assert states["sousa-026"]["state"] == "active"
    assert any(s["pending"] for s in states["sousa-026"]["stages"] if "pending" in s)
    assert golden["series"][0]["stock"]["low"] is True
    assert golden["untied"] == {"drafts": 3, "deliveries": 0}
    # §4.2 の鍵（段 2 が読む）。名前を変えない・消さない。
    for key in ("schema", "report_type", "generated_at", "project", "threads", "series", "untied", "cannot_say"):
        assert key in golden
    for key in ("id", "title", "declared_in", "state", "stage", "stages", "next", "last_movement_at",
                "stalled", "events", "cannot_say"):
        assert key in golden["threads"][0]
    for key in ("id", "title", "accounts", "slots", "stock", "next_slot", "empty_slots_14d", "material",
                "deadline", "cannot_say"):
        assert key in golden["series"][0]


def test_stalled_campaign_details(golden_world):
    payload = scenario.build(PROJECT, now=NOW)
    row = next(t for t in payload["threads"] if t["id"] == "num-04-sencha")
    collect, diary, article, announce, warm = row["stages"]
    assert collect == {"name": "collect", "medium": "hatena_bookmark", "need": 5, "tag": "num-04", "done": 5,
                       "pending": 0, "satisfied": True, "satisfied_by": "count"}
    assert diary["done"] == 1 and diary["satisfied"] is True
    assert article["satisfied"] is False and article["signal"] == "article_published"
    assert announce["medium"] == ["threads", "x"] and announce["done"] == 0 and announce["drafts"] == 1
    assert warm["replies"] == 0 and warm["overdue"] == 0 and warm["replies_within_hours"] == 6
    assert row["last_movement_at"] == "2026-10-19T08:38:22+09:00"
    assert row["stalled"] == {"days": 9, "stale_after_days": 7, "since": "2026-10-19T08:38:22+09:00"}
    assert row["next"] == [{"stage": "article", "kind": "signal", "what": "article_published を糸のファイルに 1 行"}]
    assert row["declared_in"] == "docs/sns/scenarios/num-04-sencha.md"
    assert row["goal_text"] == "煎茶の湯温を数字で覚えてもらう"


def test_scheduled_campaign_is_not_stalled(golden_world):
    payload = scenario.build(PROJECT, now=NOW)
    row = next(t for t in payload["threads"] if t["id"] == "sousa-026")
    assert row["state"] == "active" and row["stalled"] is None
    assert row["stage"]["name"] == "diary"
    assert row["stages"][1]["pending"] == 1
    assert row["next"][0]["kind"] == "wait"


def test_series_low_stock_and_empty_slot(golden_world):
    row = scenario.build(PROJECT, now=NOW)["series"][0]
    assert row["stock"] == {"approved_future": 1, "floor": 2, "low": True, "days_covered": 5}
    assert row["next_slot"] == {"at": "2026-10-30T07:00:00+09:00", "filled": False}
    assert row["empty_slots_14d"] == 3   # 10/30 金・11/6 金・11/10 火（11/3 火は埋まっている）
    assert row["material"] == {"remaining": 4, "written_at": "2026-10-23", "floor": 2, "low": False}
    assert row["slots"] == [{"day": "tue", "time": "07:00"}, {"day": "fri", "time": "07:00"}]


def test_no_body_and_no_absolute_path_in_output(golden_world, thth_root):
    for payload in (scenario.build(PROJECT, now=NOW), scenario.show(PROJECT, "num-04-sencha", now=NOW),
                    scenario.lint(PROJECT, now=NOW)):
        text = json.dumps(payload, ensure_ascii=False)
        assert SECRET_BODY not in text
        assert golden_world["repo"] not in text and thth_root not in text
        assert '"body"' not in text


def test_account_target_resolves_to_its_project(golden_world):
    by_account = scenario.build("kp-x", now=NOW)
    assert by_account["project"] == PROJECT and len(by_account["threads"]) == 2


# ------------------------------------------------------------------ events

def test_events_carry_scenario_stalled_and_series_low(golden_world):
    payload = events_cli.build(PROJECT, since="24h", now=NOW)
    stalled = [e for e in payload["events"] if e["kind"] == "scenario_stalled"]
    low = [e for e in payload["events"] if e["kind"] == "series_low"]
    assert len(stalled) == 1 and len(low) == 1
    row = stalled[0]
    assert row["ongoing"] is True and row["ref"] == "num-04-sencha" and row["stage"] == "article"
    assert row["days"] == 9 and row["project"] == PROJECT and row["next"]
    raw = f"thth.scenario/1|{PROJECT}|num-04-sencha|article"
    assert row["event_id"] == "ev_" + hashlib.sha256(raw.encode()).hexdigest()[:24]
    assert low[0]["approved_future"] == 1 and low[0]["floor"] == 2
    assert low[0]["next_slot"] == "2026-10-30T07:00:00+09:00"
    assert payload["by_kind"]["scenario_stalled"] == 1 and payload["by_kind"]["series_low"] == 1
    assert not [c for c in payload["cannot_say"] if c.startswith("scenario")]


def test_stalled_event_id_changes_when_stage_moves(golden_world):
    before = next(e for e in events_cli.build(PROJECT, now=NOW)["events"] if e["kind"] == "scenario_stalled")
    path = os.path.join(golden_world["scen"], "num-04-sencha.md")
    _write(path, THREAD_NUM04 + "- 2026-10-20 article_published https://example.com/sencha\n")
    after = [e for e in events_cli.build(PROJECT, now=NOW)["events"] if e["kind"] == "scenario_stalled"]
    # 記事が出て announce の段へ。最後の動きは 10/20 なので、まだ止まっている——ただし別の段・別の id。
    assert len(after) == 1 and after[0]["stage"] == "announce"
    assert after[0]["event_id"] != before["event_id"]


def test_closed_campaign_is_quiet(golden_world):
    path = os.path.join(golden_world["scen"], "num-04-sencha.md")
    _write(path, THREAD_NUM04 + "- 2026-10-27 closed ここで終える\n")
    row = next(t for t in scenario.build(PROJECT, now=NOW)["threads"] if t["id"] == "num-04-sencha")
    assert row["state"] == "closed" and row["next"] == []
    assert not [e for e in events_cli.build(PROJECT, now=NOW)["events"] if e["kind"] == "scenario_stalled"]


def test_event_line_substitutes_for_count(kp):
    _write(os.path.join(kp["scen"], "t.md"), """---
thth_scenario: 1
kind: thread
id: t
---
- collect: hatena_bookmark need=5
- diary: hatenablog
- 2026-10-27 researched 調べた
""")
    row = scenario.build(PROJECT, now=NOW)["threads"][0]
    assert row["stages"][0]["done"] == 0 and row["stages"][0]["satisfied_by"] == "event"
    assert row["stage"]["name"] == "diary" and row["state"] == "active"


def test_project_without_declarations_is_unchanged(isolated_account, thth_root):
    """宣言の無い口座: scenario は空・events に新しい種類が出ず cannot_say も増えない。"""
    payload = scenario.build(isolated_account["name"], now=NOW)
    assert payload["threads"] == [] and payload["series"] == [] and payload["cannot_say"] == []
    events = events_cli.build(isolated_account["name"], now=NOW)
    assert events["by_kind"]["scenario_stalled"] == 0 and events["by_kind"]["series_low"] == 0
    assert events["cannot_say"] == []


def test_unreadable_delivery_ledger_is_cannot_say_not_zero(golden_world):
    path = deliveries.path_for("kp-hatenablog", "dl_00000000a004")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{broken")
    payload = scenario.build(PROJECT, now=NOW)
    assert "deliveries_unreadable" in payload["cannot_say"]


def test_hatena_stage_without_hatena_account_is_null(isolated_account_factory, nigamilab_repo):
    isolated_account_factory("solo", project="solo")
    _write(os.path.join(nigamilab_repo, "docs", "sns", "scenarios", "t.md"),
           "---\nthth_scenario: 1\nkind: thread\nid: t\n---\n- collect: hatena_bookmark need=2\n")
    row = scenario.build("solo", now=NOW)["threads"][0]
    assert row["stages"][0]["done"] is None and "hatena_account_missing" in row["cannot_say"]
    assert row["next"][0]["kind"] == "cannot_say"


# ------------------------------------------------------------------ lint

def test_scenario_lint_reports_problems_and_untied(golden_world):
    _write(os.path.join(golden_world["scen"], "Bad.md"),
           "---\nthth_scenario: 1\nkind: thread\nid: Bad\n---\n- collect: tiktok need=1\n")
    _write(os.path.join(golden_world["queue"], "th-orphan.md"),
           _sns("kp-threads", status="draft", publish_at="2026-11-05T08:00:00+09:00", thread="nope"))
    payload = scenario.lint(PROJECT, now=NOW)
    codes = {p["code"] for p in payload["problems"]}
    assert {"invalid_id", "stage_medium_unknown", "thread_not_declared"} <= codes
    assert len(payload["untied"]) == 3
    assert {row["file"] for row in payload["untied"]} == {"th-free-1.md", "th-free-2.md", "x-free-3.md"}


def test_manuscript_lint_warns_but_does_not_refuse(golden_world):
    good = _write(os.path.join(golden_world["queue"], "th-good.md"),
                  _sns("kp-threads", status="draft", publish_at="2026-11-05T08:00:00+09:00",
                       thread="num-04-sencha", series="num"), commit=False)
    bad = _write(os.path.join(golden_world["queue"], "th-bad.md"),
                 _sns("kp-threads", status="draft", publish_at="2026-11-05T08:00:00+09:00", thread="nope"),
                 commit=False)
    assert not [m for m in lint_mod.lint_file(good) if "_not_declared" in m]
    notes = lint_mod.lint_file(bad)
    warned = [m for m in notes if "thread_not_declared" in m]
    assert warned and all(lint_mod.is_warning(m) for m in warned)
    assert not [m for m in notes if not lint_mod.is_warning(m)]


def test_manuscript_without_tags_lints_as_before(golden_world):
    path = os.path.join(golden_world["queue"], "th-free-1.md")
    assert not [m for m in lint_mod.lint_file(path) if "declared" in m]


# ------------------------------------------------------------------ 指紋に入れない

def test_tags_do_not_change_the_fingerprint_golden():
    """承認の指紋の golden（`fingerprint_golden`）の見本に札を足しても、指紋が 1 バイトも変わらない。"""
    assert fingerprint_golden.drift() == []
    for case in fingerprint_golden.CASES:
        if "segments" in case:
            continue
        tagged = dict(case)
        tagged["front_matter"] = {**(case.get("front_matter") or {}), "thread": "num-04-sencha", "series": "num"}
        assert fingerprint_golden.compute(tagged) == fingerprint_golden.compute(case), case["name"]
    assert "thread" not in approval.publish_options({"thread": "a", "series": "b"})
    assert "series" not in approval.publish_options({"thread": "a", "series": "b"})


def test_tags_do_not_change_approved_sha_of_a_real_manuscript(golden_world):
    """`thth approve` の 1 段目と同じ道（`cli._prepare_one`）で、札の有無で approved_sha が同じ。"""
    q = golden_world["queue"]
    plain = _write(os.path.join(q, "th-plain.md"),
                   _sns("kp-threads", status="draft", publish_at="2026-11-05T08:00:00+09:00",
                        extra={"topic": "コーヒー"}), commit=False)
    tagged = _write(os.path.join(q, "th-tagged.md"),
                    _sns("kp-threads", status="draft", publish_at="2026-11-05T08:00:00+09:00",
                         thread="num-04-sencha", series="num", extra={"topic": "コーヒー"}), commit=False)
    a, why_a = cli._prepare_one(plain)
    b, why_b = cli._prepare_one(tagged)
    assert why_a is None and why_b is None
    assert a["approved_sha"] == b["approved_sha"]


# ------------------------------------------------------------------ CLI

def test_cli_json_and_text(golden_world, capsys):
    assert cli.main(["scenario", PROJECT, "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == "thth.scenario/1" and len(out["threads"]) == 2
    assert cli.main(["scenario", PROJECT]) == 0
    text = capsys.readouterr().out
    assert "キャンペーン num-04-sencha" in text and "シリーズ num" in text and SECRET_BODY not in text
    assert cli.main(["scenario", "show", PROJECT, "num-04-sencha", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["kind"] == "thread" and {r["file"] for r in shown["items"]} == {"hb-num04.md", "x-num04-announce.md"}
    assert cli.main(["scenario", "show", PROJECT, "num"]) == 0
    assert "th-num-1103.md" in capsys.readouterr().out
    assert cli.main(["scenario", "lint", PROJECT, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["report_type"] == "scenario_lint"


def test_cli_unknown_target_and_id(golden_world, capsys):
    assert cli.main(["scenario", "no-such-project", "--json"]) == 2
    assert "target_unknown" in capsys.readouterr().out
    assert cli.main(["scenario", "show", PROJECT, "nope", "--json"]) == 2
    assert "scenario_unknown" in capsys.readouterr().out


def test_reading_writes_nothing(golden_world, thth_root):
    def snapshot():
        out = {}
        for base in (golden_world["repo"], thth_root):
            for dirpath, _dirs, files in os.walk(base):
                if ".git" in dirpath.split(os.sep):
                    continue
                for name in files:
                    p = os.path.join(dirpath, name)
                    out[p] = os.path.getmtime(p)
        return out
    before = snapshot()
    scenario.build(PROJECT, now=NOW)
    scenario.show(PROJECT, "num-04-sencha", now=NOW)
    scenario.lint(PROJECT, now=NOW)
    assert snapshot() == before


def test_warm_counts_replies_and_overdue_without_their_text(kp):
    """エンゲージの段: 返信の数と、返さないまま `replies_within_hours` を越えたもの。他人の本文は出さない。"""
    _write(os.path.join(kp["scen"], "w.md"),
           "---\nthth_scenario: 1\nkind: thread\nid: w\n---\n- warm: threads replies_within_hours=6\n")
    _write(os.path.join(kp["queue"], "th-root.md"),
           _sns("kp-threads", status="posted", publish_at="2026-10-27T08:00:00+09:00", thread="w",
                extra={"post_id": "ROOT", "posted_at": "2026-10-27T08:00:00+09:00"}))
    cfg = accounts_mod.load_account("kp-threads")
    directory = pathlib.Path(accounts_mod.data_dirs(cfg, "kp-threads")["replies"])
    directory.mkdir(parents=True, exist_ok=True)
    rows = [{"id": "R1", "username": "outside", "text": "PRIVATE REPLY", "timestamp": "2026-10-27T20:00:00+09:00",
             "post_id": "ROOT"},
            {"id": "R2", "username": "outside", "text": "PRIVATE REPLY", "timestamp": "2026-10-27T21:00:00+09:00",
             "post_id": "ROOT"},
            {"kind": "fetch", "post_id": "ROOT", "collected_at": "2026-10-28T09:00:00+09:00"}]
    (directory / "ROOT.ndjson").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    sent.write(accounts_mod.state_dir_for("kp-threads"), post_id="ANSWER", text="PRIVATE SENT", body_hash="h",
               sent_at="2026-10-27T20:30:00+09:00", reply_to="R1")
    payload = scenario.build(PROJECT, now=NOW)
    row = payload["threads"][0]
    assert row["stages"][0]["replies"] == 2 and row["stages"][0]["overdue"] == 1
    assert row["state"] == "active" and row["next"][0]["kind"] == "replies"
    assert "PRIVATE" not in json.dumps(payload, ensure_ascii=False)
