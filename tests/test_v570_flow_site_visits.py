"""5.7.0（設計 効果測定と連動 §3 段 1・段 3 の THTH 側）。

段 1: 糸の段の語彙
- 媒体 `ads`（広告の口座の記録 `ops/` の boost・activate を読むだけ）が書ける（`stage_medium_unknown` で断らない）。
- はてブの段の `target=own|external`（自分の記事か他人のページか）。`target=own` の段に external を数えない。
- 段の日本語の名（`promote` 告知・`selfbm` セルクマ・`boost` 広告）を `stages[].label` と表示に出す。
- `thth scenario`・`show`・`dashboard campaign` の出力の 1 行目が skill `thth-flow`（`--json` は `skill`）。

段 3: `thth scenario site-import <project> --file <json>`
- `hub.site_visits/1` を受け取り `state/<project>/site_visits/` に控える。schema 違い・未来の窓・負の数は断る。
- 二度入れても上書きで増えない。campaign → 原稿の名前（`/` を `-` にした形も）→ `thread:` で糸に結ぶ。
- 結ばれないもの（`""`・`(organic)` など）は `untied_campaigns` に数だけ（名前は出さない）。
- 取れなかった窓（rows が null・`unknown_reason`）は `cannot_say`。入っていない project は `site_visits_not_imported`。
- `dashboard campaign` の golden（`tests/fixtures/dashboard_campaign_v570_golden.json`）。
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

from tests.test_v500_scenario import NOW, PROJECT, _sns, _write, golden_world, kp  # noqa: F401
from thth import accounts as accounts_mod, cli, dashboard_cli, jst, scenario, sent, site_visits

DASH_GOLDEN = pathlib.Path(__file__).resolve().parent / "fixtures" / "dashboard_campaign_v570_golden.json"
THREAD = "num-04-sencha"

FLOW = """---
thth_scenario: 1
kind: thread
id: flow-01
title: 流れの見本
project: kp
started: 2026-10-20
---
- collect: hatena_bookmark target=external need=2
- promote: threads,x need=1
- selfbm: hatena_bookmark target=own need=1
- boost: ads need=1
"""


@pytest.fixture
def flow(golden_world, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    monkeypatch.setenv("THTH_ADACCOUNTS_DIR", str(tmp_path / "adaccounts"))
    _write(os.path.join(golden_world["scen"], "flow-01.md"), FLOW)
    q = golden_world["queue"]
    # 告知 1 本（Threads・公開済み・投稿の id は FB の形を真似る）と、X の下書き 1 本。
    _write(os.path.join(q, "th-flow-01.md"),
           _sns("kp-threads", status="posted", publish_at="2026-10-22T07:00:00+09:00", thread="flow-01",
                extra={"post_id": "111_222", "posted_at": "2026-10-22T07:00:03+09:00"}))
    _write(os.path.join(q, "x-flow-01.md"),
           _sns("kp-x", status="draft", publish_at="2026-11-02T07:00:00+09:00", thread="flow-01", media="x"))
    state = accounts_mod.state_dir_for("kp-hatenablog")
    # 他人のページ 2 本（タグ flow-01）・自分の記事 1 本（タグ flow-01・target own）。
    for i, target in enumerate(("external", "external", "own")):
        url = f"https://example.com/flow/{i}"
        sent.write(state, post_id=sent.bookmark_post_id(url), text="", body_hash="0" * 64,
                   sent_at=f"2026-10-2{1 + i}T09:00:00+09:00", kind="bookmark_add", url=url, url_canonical=url,
                   target=target, tags=["flow-01"], result="confirmed")
    return golden_world


def _ads_ledger(tmp_path, name="kp-ads", project=PROJECT):
    from thth.ads import ledger
    os.makedirs(ledger.adaccounts_dir(), exist_ok=True)
    data = {"adaccount": name, "act_id": "1234567890", "project": project, "currency": "JPY",
            "token": str(tmp_path / "kp-ads.token"), "pages": [], "ig_accounts": [],
            "ceilings": {"monthly": 0, "lifetime_budget_max": 0, "days_max": 0}}
    with open(ledger.path_for(name), "w", encoding="utf-8") as f:
        json.dump(data, f)
    return name


def _op(name, op_id, row):
    from thth.ads import ops
    os.makedirs(ops.ops_dir(name), exist_ok=True)
    with open(os.path.join(ops.ops_dir(name), f"{op_id}-{row['op']}.json"), "w", encoding="utf-8") as f:
        json.dump(dict(row, op_id=op_id), f)


def _boost(name, op_id, campaign_id, *, story=None, url_tags=None, lifetime=500, result="ok"):
    _op(name, op_id, {"op": "boost", "at": "2026-10-24T10:00:00+09:00", "result": result, "status": "PAUSED",
                      "plan": {"object_story_id": story or "999_1", "url_tags": url_tags, "lifetime_minor": lifetime,
                               "objective": "traffic", "platforms": ["facebook"]},
                      "created": {"campaign_id": campaign_id, "adset_id": "2", "creative_id": "3", "ad_id": "4"}})


def _stage(row, name):
    return next(s for s in row["stages"] if s["name"] == name)


# ------------------------------------------------------------------ 段 1: 語彙

def test_adsの段は断らず_広告の口座が無ければ読めないと言う(flow):
    decl = scenario.parse_declaration(FLOW, "docs/sns/scenarios/flow-01.md")
    assert not [p for p in decl.problems if p["code"] == "stage_medium_unknown"]
    assert [s["media"] for s in decl.stages][-1] == ["ads"]
    lint = scenario.lint(PROJECT, now=NOW)
    assert not [p for p in lint["problems"] if p["id"] == "flow-01" and p["code"].startswith("stage_")]
    row = next(t for t in scenario.build(PROJECT, now=NOW)["threads"] if t["id"] == "flow-01")
    boost = _stage(row, "boost")
    assert boost["done"] is None and boost["pending"] is None and "ads_account_missing" in row["cannot_say"]


def test_targetの形が違えば予告する():
    bad = FLOW.replace("target=own", "target=mine").replace("- boost: ads need=1", "- boost: ads target=own")
    codes = [p["code"] for p in scenario.parse_declaration(bad, "x/flow-01.md").problems]
    assert codes.count("stage_option_invalid") == 2


def test_自分の記事のセルクマと他人のページを分けて数える(flow):
    row = next(t for t in scenario.build(PROJECT, now=NOW)["threads"] if t["id"] == "flow-01")
    collect, selfbm = _stage(row, "collect"), _stage(row, "selfbm")
    # target=external の collect は他人のページ 2 本だけ、target=own の selfbm は自分の記事 1 本だけ。
    assert (collect["done"], collect["target"], collect["by_target"]) == (2, "external", {"own": 1, "external": 2})
    assert (selfbm["done"], selfbm["target"]) == (1, "own")
    # 既定の（target の無い）段は今までどおり全部を数え、内訳だけ足す。
    num04 = next(t for t in scenario.build(PROJECT, now=NOW)["threads"] if t["id"] == THREAD)
    assert _stage(num04, "collect")["done"] == 5 and "target" not in _stage(num04, "collect")
    show = scenario.show(PROJECT, "flow-01", now=NOW)
    marks = _stage(show["thread"], "selfbm")["items"]
    assert [m["target"] for m in marks] == ["own"]


def test_externalだけのはてブはselfbmに数えない(flow):
    state = accounts_mod.state_dir_for("kp-hatenablog")
    # 自分の記事のブックマークを外にしても selfbm は 0（target を見ている）。
    url = "https://example.com/flow/2"
    sent.write(state, post_id=sent.bookmark_post_id(url) + "-r", text="", body_hash="0" * 64,
               sent_at="2026-10-25T09:00:00+09:00", kind="bookmark_add", url=url + "?x", url_canonical=url + "?x",
               target="external", tags=["flow-01"], result="confirmed")
    row = next(t for t in scenario.build(PROJECT, now=NOW)["threads"] if t["id"] == "flow-01")
    assert _stage(row, "selfbm")["done"] == 1
    assert _stage(row, "collect")["done"] == 3


def test_広告の段は口座の記録から数え_使った額は持たない(flow, tmp_path):
    name = _ads_ledger(tmp_path)
    # 1: 告知の投稿（post_id 111_222）を押し出して始めた。2: 印の campaign が告知の原稿の名前（止めたまま）。
    # 3: 別の投稿（結ばれない）。4: 作るのに失敗した物は数えない。
    _boost(name, "20261024T100000-a", "c1", story="111_222", lifetime=500)
    _op(name, "20261024T110000-b", {"op": "activate", "at": "2026-10-24T11:00:00+09:00", "result": "ok",
                                     "campaign_id": "c1", "amount_minor": 500, "activated": ["c1"]})
    _boost(name, "20261025T100000-c", "c2", url_tags="utm_source=facebook&utm_medium=paid&utm_campaign=x-flow-01",
           lifetime=300)
    _boost(name, "20261025T110000-d", "c3", story="555_666")
    _boost(name, "20261025T120000-e", "c4", story="111_222", result="failed")
    row = next(t for t in scenario.build(PROJECT, now=NOW)["threads"] if t["id"] == "flow-01")
    boost = _stage(row, "boost")
    assert (boost["done"], boost["pending"], boost["need"], boost["satisfied"]) == (1, 1, 1, True)
    assert boost["budget_minor"] == 500 and boost["currency"] == "JPY"
    assert boost["spend_minor"] is None and boost["cannot_say"] == {"spend_minor": "ads_spend_not_recorded"}
    assert boost["label"] == "広告" and boost["medium"] == "ads"
    items = _stage(scenario.show(PROJECT, "flow-01", now=NOW)["thread"], "boost")["items"]
    assert [(i["campaign_id"], i["status"], i["file"]) for i in items] == [
        ("c1", "active_started", "th-flow-01.md"), ("c2", "paused", "x-flow-01.md")]
    assert all(isinstance(i["budget_minor"], int) for i in items)


def test_段の日本語の名を出す(flow):
    row = next(t for t in scenario.build(PROJECT, now=NOW)["threads"] if t["id"] == "flow-01")
    assert [s["label"] for s in row["stages"]] == ["リサーチ", "告知", "セルクマ", "広告"]
    lines = []
    from thth import scenario_cli
    scenario_cli.render(scenario.build(PROJECT, now=NOW), out=lines.append)
    text = "\n".join(lines)
    assert "promote（告知）" in text and "selfbm（セルクマ） 1/1" in text and "boost（広告）" in text


@pytest.mark.parametrize("argv", [["scenario", PROJECT], ["scenario", "show", PROJECT, THREAD],
                                  ["dashboard", PROJECT, "campaign", THREAD]])
def test_出力の1行目はskill_thth_flow(flow, capsys, argv):
    assert cli.main(argv) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith("skill: ") and "thth-flow" in first
    assert cli.main(argv + ["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["skill"]["name"] == "thth-flow" and payload["skill"]["command"] == "thth scenario skill --print"
    assert not payload["skill"]["path"].startswith("/")


def test_scenario_skillは場所と全文を出す(capsys):
    assert cli.main(["scenario", "skill", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["name"] == "thth-flow"
    assert cli.main(["scenario", "skill", "--print"]) == 0
    assert "name: thth-flow" in capsys.readouterr().out


# ------------------------------------------------------------------ 段 3: site-import

def _visits(tmp_path, rows, *, to="2026-10-27", frm="2026-10-21", site="kopicha.org", referrers=None,
            schema="hub.site_visits/1", reason=None, name="v.json", **extra):
    data = {"schema": schema, "site": site, "generated_at": "2026-10-28T06:00:00+09:00",
            "window": {"from": frm, "to": to}, "rows": rows,
            "referrers": referrers if referrers is not None else ([] if rows is not None else None)}
    if reason is not None:
        data["unknown_reason"] = reason
    data.update(extra)
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _row(campaign, sessions, *, medium="social", source="threads", engaged=None):
    return {"campaign": campaign, "source": source, "medium": medium, "sessions": sessions,
            "engaged_sessions": engaged, "avg_duration_s": 31.5, "landing_page": "/blog/sencha?fbclid=xyz"}


ROWS = [_row("x-num04-announce", 12, engaged=8),                       # 原稿の名前 → num-04-sencha
        _row("x-num04-announce", 3, medium="paid", source="facebook", engaged=2),
        _row("sns-queue-hb-num04", 4, source="t.co", engaged=1),       # パスの / を - にした形 → num-04-sencha
        _row("", 40), _row("(organic)", 30), _row("(direct)", 20),     # campaign ではない
        _row("nai-genkou", 2),                                         # 原稿が無い
        _row("th-free-1", 1)]                                          # 原稿はあるが thread: が無い


def _store(project=PROJECT):
    directory = site_visits.store_dir(project)
    return sorted(os.listdir(directory)) if os.path.isdir(directory) else []


def test_入っていないprojectは訪問をcannot_sayで言う(flow):
    block = scenario.show(PROJECT, THREAD, now=NOW)["site_visits"]
    assert block["sessions"] is None and block["cannot_say"] == "site_visits_not_imported"
    camp = dashboard_cli.build(PROJECT, "campaign", THREAD, now=NOW)
    out = camp["campaign"]["outcomes"]
    assert out["article_visits"]["cannot_say"] == "site_visits_not_imported"
    assert out["cannot_say"]["article_clicks"] == "article_clicks_not_connected"
    assert out["cannot_say"]["article_visits"] == "site_visits_not_imported"
    assert camp["as_of"]["site_visits"] is None


def test_site_importは控えて糸に結び_結ばれないものは数だけ(flow, tmp_path, capsys):
    path = _visits(tmp_path, ROWS, referrers=[{"host": "b.hatena.ne.jp", "sessions": 5}])
    assert cli.main(["scenario", "site-import", PROJECT, "--file", path, "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["stored"] == "state/kp/site_visits/kopicha.org_2026-10-27.json" and got["replaced"] is False
    assert got["rows_total"] == 8
    assert got["tied"] == {"campaigns": 2, "threads": 1, "campaigns_total": 7}
    assert got["untied_campaigns"] == {"campaigns": 5, "sessions": 93,
                                       "by_reason": {"not_a_campaign": 3, "no_manuscript": 1, "no_thread": 1,
                                                     "ambiguous": 0}}
    block = scenario.show(PROJECT, THREAD, now=NOW)["site_visits"]
    assert block["sessions"] == 19 and block["engaged_sessions"] == 11 and block["cannot_say"] is None
    assert block["as_of"] == "2026-10-28T06:00:00+09:00"
    site = block["sites"][0]
    assert site["by_medium"] == {"paid": 3, "social": 16}
    assert [c["file"] for c in site["campaigns"]] == ["hb-num04.md", "x-num04-announce.md", "x-num04-announce.md"]
    assert all(c["landing_page"] == "/blog/sencha" for c in site["campaigns"])   # ? から後は控えない
    text = json.dumps(block, ensure_ascii=False)
    assert "nai-genkou" not in text and "th-free-1" not in text and "(organic)" not in text   # 名前は出さない
    stored = json.loads((pathlib.Path(site_visits.store_dir(PROJECT)) / _store()[0]).read_text(encoding="utf-8"))
    assert "fbclid" not in json.dumps(stored)


def test_MMRの形_原稿のパスのスラッシュをハイフンにした形も結ぶ(flow, tmp_path):
    # MMR の `threads-drafts-features-2026-11-02` は `threads/drafts/features-2026-11-02.md` の形。
    assert site_visits.campaign_keys("threads/drafts/features-2026-11-02.md") >= {
        "features-2026-11-02", "drafts-features-2026-11-02", "threads-drafts-features-2026-11-02"}
    # この世界の原稿は docs/sns/queue/ にある。全部つないだ形・途中から切った形・ファイル名のどれでも結ぶ。
    for i, campaign in enumerate(("docs-sns-queue-x-num04-announce", "queue-x-num04-announce",
                                  "X-NUM04-ANNOUNCE")):
        site_visits.import_file(PROJECT, _visits(tmp_path, [_row(campaign, 7)], to=f"2026-10-2{i + 1}",
                                                 frm="2026-10-20", name=f"m{i}.json"), now=NOW)
        block = scenario.show(PROJECT, THREAD, now=NOW)["site_visits"]
        assert block["sessions"] == 7, campaign
    # 拡張子つき・途中の文字の違う形は結ばない。
    for campaign in ("x-num04-announce.md", "sns-x-num04-announce"):
        site_visits.import_file(PROJECT, _visits(tmp_path, [_row(campaign, 7)], to="2026-10-27",
                                                 name="n.json"), now=NOW)
        assert scenario.show(PROJECT, THREAD, now=NOW)["site_visits"]["cannot_say"] == "no_tied_campaign"


def test_二重importは上書きで増えない(flow, tmp_path):
    path = _visits(tmp_path, ROWS)
    first = site_visits.import_file(PROJECT, path, now=NOW)
    again = site_visits.import_file(PROJECT, path, now=NOW)
    assert (first["replaced"], again["replaced"]) == (False, True)
    assert _store() == ["kopicha.org_2026-10-27.json"]
    assert scenario.show(PROJECT, THREAD, now=NOW)["site_visits"]["sessions"] == 19
    # 窓の終わりが新しい控えがあれば、そのサイトはそちらを読む（古い窓と足さない）。
    site_visits.import_file(PROJECT, _visits(tmp_path, [_row("x-num04-announce", 2)], to="2026-10-28",
                                             frm="2026-10-22", name="w.json"), now=NOW)
    assert len(_store()) == 2
    assert scenario.show(PROJECT, THREAD, now=NOW)["site_visits"]["sessions"] == 2


@pytest.mark.parametrize("kwargs,code", [
    ({"schema": "hub.site_visits/2"}, "site_visits_schema_unknown"),
    ({"to": "2026-10-29"}, "site_visits_window_future"),
    ({"frm": "2026-10-28", "to": "2026-10-27"}, "site_visits_window_invalid"),
    ({"rows": [_row("x-num04-announce", -1)]}, "site_visits_negative"),
    ({"rows": [dict(_row("x-num04-announce", 1), engaged_sessions=-3)]}, "site_visits_negative"),
    ({"referrers": [{"host": "t.co", "sessions": -2}]}, "site_visits_negative"),
    ({"rows": [_row("x-num04-announce", 1.5)]}, "site_visits_invalid"),
    ({"rows": [_row("x-num04-announce", True)]}, "site_visits_invalid"),
    ({"site": "../etc"}, "site_visits_site_invalid"),
    ({"generated_at": "2026-11-30T00:00:00+09:00"}, "site_visits_generated_in_future"),
])
def test_形の違うJSONは断り何も控えない(flow, tmp_path, capsys, kwargs, code):
    args = {"rows": ROWS}
    args.update(kwargs)
    rows = args.pop("rows")
    path = _visits(tmp_path, rows, **args)
    assert cli.main(["scenario", "site-import", PROJECT, "--file", path, "--json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and out["code"] == code
    assert _store() == []


def test_取れなかった窓はcannot_sayで0と混ぜない(flow, tmp_path, capsys):
    path = _visits(tmp_path, None, reason="ga4_quota_exceeded")
    assert cli.main(["scenario", "site-import", PROJECT, "--file", path, "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["rows_total"] is None and got["unknown_reason"] == "ga4_quota_exceeded"
    assert "site_visits_unavailable" in got["cannot_say"]
    block = scenario.show(PROJECT, THREAD, now=NOW)["site_visits"]
    assert block["sessions"] is None and block["cannot_say"] == "site_visits_unavailable"
    assert block["sites"][0]["unknown_reason"] == "ga4_quota_exceeded"
    # 読めない形の理由は unknown にそろえる（文を控えない）。
    site_visits.import_file(PROJECT, _visits(tmp_path, None, reason="API が 429 を返した"), now=NOW)
    assert scenario.show(PROJECT, THREAD, now=NOW)["site_visits"]["sites"][0]["unknown_reason"] == "unknown"


def test_知らないprojectとファイルの無いimportは断る(flow, tmp_path, capsys):
    assert cli.main(["scenario", "site-import", "dare-mo-inai", "--file", _visits(tmp_path, ROWS), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "target_unknown"
    assert cli.main(["scenario", "site-import", PROJECT, "--file", str(tmp_path / "nai.json"), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == "site_visits_file_missing"
    assert cli.main(["scenario", "site-import", PROJECT, "--json"]) == 2
    capsys.readouterr()


def test_dashboard_campaignに訪問を出し_つないでいないの印を外す(flow, tmp_path, capsys):
    site_visits.import_file(PROJECT, _visits(tmp_path, ROWS), now=NOW)
    camp = dashboard_cli.build(PROJECT, "campaign", THREAD, now=NOW)
    out = camp["campaign"]["outcomes"]
    assert out["article_visits"]["sessions"] == 19
    assert "article_clicks" not in out["cannot_say"] and "article_visits" not in out["cannot_say"]
    assert camp["as_of"]["site_visits"] == "2026-10-28T06:00:00+09:00"
    assert dashboard_cli.build(PROJECT, now=NOW)["as_of"]["site_visits"] == "2026-10-28T06:00:00+09:00"
    # 結ばれない糸（sousa-026）は「—」と理由。
    other = dashboard_cli.build(PROJECT, "campaign", "sousa-026", now=NOW)["campaign"]["outcomes"]
    assert other["article_visits"]["sessions"] is None
    assert other["cannot_say"]["article_visits"] == "no_tied_campaign"
    assert cli.main(["dashboard", PROJECT, "campaign", THREAD]) == 0
    text = capsys.readouterr().out
    assert "本記事への訪問（GA4・hub 経由）・2026-10-28 06:00 時点: 19（エンゲージ 11）" in text
    payload = dashboard_cli.dumps(camp)
    for needle in (flow["repo"], "nai-genkou", "fbclid"):
        assert needle not in payload


def test_dashboard_campaignのgolden(flow, tmp_path):
    from thth.ads import ledger  # noqa: F401
    name = _ads_ledger(tmp_path)
    _boost(name, "20261024T100000-a", "c1", story="111_222", lifetime=500)
    site_visits.import_file(PROJECT, _visits(tmp_path, ROWS + [_row("th-flow-01", 6)]), now=NOW)
    payload = json.loads(dashboard_cli.dumps(dashboard_cli.build(PROJECT, "campaign", "flow-01", now=NOW)))
    if os.environ.get("THTH_WRITE_DASHBOARD_GOLDEN") == "1":
        DASH_GOLDEN.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    golden = json.loads(DASH_GOLDEN.read_text(encoding="utf-8"))
    assert payload == golden
    camp = golden["campaign"]
    assert [s["label"] for s in camp["steps"]] == ["リサーチ", "告知", "セルクマ", "広告"]
    assert camp["outcomes"]["article_visits"]["sessions"] == 6
    assert [d["head"] for d in camp["deliverables"] if d["stage"] == "boost"] == ["広告"]
    assert golden["skill"]["name"] == "thth-flow"


def test_読むだけの命令は控えを書かない(flow):
    scenario.build(PROJECT, now=NOW)
    scenario.show(PROJECT, THREAD, now=NOW)
    dashboard_cli.build(PROJECT, "campaign", THREAD, now=NOW)
    assert not os.path.exists(site_visits.store_dir(PROJECT))


def test_skill_thth_flowは予定でなく実際の命令と欄を書く():
    from thth import flow_skill
    text = flow_skill.read_text()
    assert "5.7）で入る予定" not in text and "stage_medium_unknown" not in text
    for needle in ("- boost: ads need=1", "- selfbm: hatena_bookmark target=own need=1", "target=external",
                   "thth scenario site-import <project> --file <json>", "hub.site_visits/1", "outcomes.article_visits",
                   "untied_campaigns", "site_visits_not_imported", "ads_spend_not_recorded", "thth scenario skill --print"):
        assert needle in text, needle
