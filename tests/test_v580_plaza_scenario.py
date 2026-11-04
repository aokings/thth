"""5.8.0（設計 効果測定と連動 §3 段 4）: 効いたかをプラザに置く。

見るのは:
- `thth plaza post <account> --kind measure --from scenario <project> <糸の id>`: 糸の段ごとの本数・媒体ごとの反応
  （`thth after` と同じ 1 本の計算）・本記事への訪問（5.7.0 の site_visits）・広告の段を、**道具が観測として**
  付ける（`tool_numbers`・observed は道具だけ）。人や LLM の解釈は `--body-file`。
- 柵: 観測の欄に本文の数字が混ざらない・分母と窓が付く・段 3（訪問の取り込み）が無いとき訪問は null と理由
  （0 と言わない）・他人の本文や username・原稿の本文を観測に入れない。
- 糸を閉じたら observe の次の一手に「広場に置く」とこの命令の 1 行。組が無ければ `owner set` の案内。
- `plaza digest` で、糸 1 本の結果が別の project（同じ持ち主の組）に「試していない媒体」つきで出る。
- MCP の `thth_plaza_post` に同じ引数（from_tool scenario・from_project・from_thread）。
"""
from __future__ import annotations

import datetime
import json
import os

import pytest

from tests.test_v500_scenario import NOW, PROJECT, SECRET_BODY, _write, golden_world, kp  # noqa: F401
from tests.test_v570_flow_site_visits import FLOW, _ads_ledger, _boost, _op, _row, _visits, flow  # noqa: F401
from thth import after_cli, cli, jst, morning, plaza, plaza_from, plaza_moments, site_visits

HUMAN = "人の解釈: 告知の朝が効いた気がする。n=999 と書いておく"
SCOPE = "煎茶の糸・Threads と X とはてブ"
CLOSED = FLOW + "\n- 2026-10-27 closed 全段を見届けた\n"
METRICS = {"views_24h": 340, "likes_24h": 12, "replies_back_24h": 3, "covered": True,
           # 計算の外の鍵（他人の本文・username）を混ぜても観測に写さない。
           "username": "SOMEONE-ELSE", "text": "OTHER-REPLY-TEXT"}


@pytest.fixture
def closed(flow, isolated_account_factory, monkeypatch, tmp_path):  # noqa: F811
    """flow-01 を閉じる。広告 1 本（始めた）。別の project `kt`（関東）に Threads と Bluesky の口座。"""
    _write(os.path.join(flow["scen"], "flow-01.md"), CLOSED)
    name = _ads_ledger(tmp_path)
    _boost(name, "20261024T100000-a", "c1", story="111_222", lifetime=500)
    _op(name, "20261024T110000-b", {"op": "activate", "at": "2026-10-24T11:00:00+09:00", "result": "ok",
                                     "campaign_id": "c1", "amount_minor": 500, "activated": ["c1"]})
    from tests.conftest import init_real_repo
    kt_repo = init_real_repo(tmp_path, "kt")     # 関東は別の repo（kp の糸の宣言を持たない）
    isolated_account_factory("kt-threads", project="kt", handle="kt_tea", repo_dir=kt_repo)
    isolated_account_factory("kt-bsky", project="kt", media="bluesky", handle="kt.bsky.social", repo_dir=kt_repo)
    calls = []

    def lookup(account):
        calls.append(account)
        return lambda post_id: dict(METRICS) if post_id == "111_222" else {"covered": False}
    monkeypatch.setattr(after_cli, "reaction_lookup", lookup)
    flow["lookups"] = calls
    return flow


def _post(**kwargs):
    kwargs.setdefault("from_source", ("scenario", PROJECT, "flow-01"))
    kwargs.setdefault("body", HUMAN)
    return plaza.post("kp-threads", kind=kwargs.pop("kind", "measure"), title=kwargs.pop("title", None),
                      by="kp-session", scope_note=SCOPE, **kwargs)


def _stage(numbers, name):
    return next(s for s in numbers["numbers"]["stages"] if s["name"] == name)


# ------------------------------------------------------------------ 観測

def test_糸の結果を道具が観測として付ける_分母と窓(closed):
    result = _post()
    assert result["from"] == "scenario" and result["evidence_level"] == "observed"
    record = plaza.STORE.get(result["plaza_id"])
    assert record["kind"] == "measure" and record["title"] == "糸「流れの見本」の結果"
    assert record["how"] == "thth scenario show kp flow-01"
    assert record["source"] == {"kind": "scenario", "command": "thth scenario show kp flow-01",
                                "project": "kp", "thread": "flow-01"}
    numbers = record["tool_numbers"]
    assert numbers["source"] == "scenario" and numbers["observed"] is True and numbers["target"] == "flow-01"
    # 窓: 糸の始まり（started）から閉じた日まで。
    assert numbers["period"] == {"start": "2026-10-20T00:00:00+09:00", "end": "2026-10-27T00:00:00+09:00",
                                 "basis": "thread_started_to_closed", "timezone": "Asia/Tokyo"}
    assert numbers["numbers"]["state"] == "closed"
    # 段ごとの本数（済み/要る）。
    assert (_stage(numbers, "collect")["done"], _stage(numbers, "collect")["need"]) == (2, 2)
    assert _stage(numbers, "promote")["by_medium"] == {"threads": {"done": 1, "pending": 0},
                                                        "x": {"done": 0, "pending": 0}}
    assert _stage(numbers, "selfbm")["done"] == 1
    # 媒体ごとの反応（after と同じ 1 本の計算・分母は糸のその媒体の投稿の本数・n<5 は中央値を言わない）。
    threads = numbers["numbers"]["reactions"]["threads"]
    assert threads["posts"] == 1 and threads["covered_24h"] == 1 and threads["min_n"] == 5
    assert threads["likes_24h"] == {"n": 1, "of": 1, "sum": 12, "median": None}
    assert closed["lookups"] == ["kp-threads"]
    # 広告の段: 始めた本数と通しの予算。使った額と link_click は記録に無いので null と理由。
    ads = numbers["numbers"]["ads"]
    assert (ads["declared"], ads["started"], ads["paused"], ads["budget_minor"], ads["currency"]) == (
        True, 1, 0, 500, "JPY")
    assert ads["spend_minor"] is None and ads["link_clicks"] is None
    assert ads["cannot_say"] == {"spend_minor": "ads_spend_not_recorded", "link_clicks": "ads_clicks_not_recorded"}
    # 使った媒体と、宣言したのに 0 本の媒体（X）。
    assert numbers["numbers"]["media_tried"] == ["ads", "hatena_bookmark", "threads"]
    assert numbers["numbers"]["media_declared_untried"] == ["x"]
    # 下書きは本文の先頭・人の解釈はその下。
    assert record["body"].startswith(plaza_from.DRAFT_HEAD) and record["body"].endswith(HUMAN)
    assert "likes 計 12（1/1 本・中央値は言えない（n<5））" in record["body"]


def test_段3が無いとき訪問はnullと理由_0と言わない(closed):
    numbers = plaza.STORE.get(_post()["plaza_id"])["tool_numbers"]
    visits = numbers["numbers"]["site_visits"]
    assert visits["sessions"] is None and visits["engaged_sessions"] is None
    assert visits["cannot_say"] == "site_visits_not_imported"
    assert "site_visits_not_imported" in numbers["cannot_say"]
    body = plaza.STORE.get(plaza.list_posts(plaza.Viewer({"kp-threads": "kp"}))["posts"][0]["plaza_id"])["body"]
    assert "本記事への訪問: 言えない（site_visits_not_imported）" in body
    assert "本記事への訪問: 0" not in body


def test_訪問を取り込めば数と窓が付く(closed, tmp_path):
    path = _visits(tmp_path, [_row("th-flow-01", 40, engaged=30), _row("(organic)", 99)])
    site_visits.import_file(PROJECT, path, now=NOW)
    visits = plaza.STORE.get(_post()["plaza_id"])["tool_numbers"]["numbers"]["site_visits"]
    assert visits["sessions"] == 40 and visits["engaged_sessions"] == 30 and visits["cannot_say"] is None
    site = visits["sites"][0]
    assert site["window"] == {"from": "2026-10-21", "to": "2026-10-27"} and site["n_campaigns"] == 1
    assert visits["source"] == "ga4_via_hub" and visits["as_of"] == "2026-10-28T06:00:00+09:00"
    # 原稿の名前（campaign）・着地の頁は観測に写さない（数と窓だけ）。
    text = json.dumps(visits, ensure_ascii=False)
    assert "th-flow-01" not in text and "/blog/" not in text and "99" not in text


def test_観測の欄に本文の数字と他人の本文やusernameが混ざらない(closed):
    result = _post()
    record = plaza.STORE.get(result["plaza_id"])
    observed = json.dumps(record["tool_numbers"], ensure_ascii=False)
    assert "999" not in observed
    whole = json.dumps(record, ensure_ascii=False)
    assert "SOMEONE-ELSE" not in whole and "OTHER-REPLY-TEXT" not in whole
    # 原稿の本文（SNS の節の 1 行目＝題）も写さない。
    assert SECRET_BODY not in whole
    shown = plaza.show(result["plaza_id"], plaza.Viewer({"kp-x": "kp"}))
    assert any("999" in value for value in shown["text_numbers"]["values"])
    assert not any("340" in value or "12" == value for value in shown["text_numbers"]["values"])


def test_from_scenarioの断り(closed):
    with pytest.raises(plaza.PlazaError, match="^from_out_of_scope$"):
        _post(from_source=("scenario", "kt", "flow-01"))
    with pytest.raises(plaza.PlazaError, match="^from_scenario_not_found$"):
        _post(from_source=("scenario", PROJECT, "nai-ito"))
    with pytest.raises(plaza.PlazaError, match="^from_scenario_not_found$"):
        _post(from_source=("scenario", PROJECT, "num"))      # 型（series）は糸ではない
    with pytest.raises(plaza.PlazaError, match="^invalid_from$"):
        _post(from_source=("scenario", PROJECT, "../x"))
    with pytest.raises(plaza.PlazaError, match="^not_a_measure$"):
        _post(kind="finding")
    assert "from_scenario_not_found" in plaza.REASONS and "from_scenario_not_found" in plaza.NEXT


# ------------------------------------------------------------------ 監査 段4 の直し

EVT = """---
thth_scenario: 1
kind: thread
id: evt-01
title: 出来事だけの糸
project: kp
started: 2026-10-20
---
- article: site signal=article_published
- himitsu_dan: threads need=1

- 2026-10-21 article_published 公開した
- 2026-10-27 closed 閉じた
"""


@pytest.fixture
def evt(closed):
    """人が書く出来事と、status: posted だけの原稿（実測なし）の糸。訪問も広告も無い。"""
    from tests.test_v500_scenario import _sns
    _write(os.path.join(closed["scen"], "evt-01.md"), EVT)
    _write(os.path.join(closed["queue"], "th-evt-01.md"),
           _sns("kp-threads", status="posted", publish_at="2026-10-22T08:00:00+09:00", thread="evt-01",
                extra={"post_id": "333_444", "posted_at": "2026-10-22T08:00:02+09:00"}))
    return closed


def test_出来事とstatus_postedだけの糸はobservedにしない(evt):
    result = _post(from_source=("scenario", PROJECT, "evt-01"))
    record = plaza.STORE.get(result["plaza_id"])
    numbers = record["tool_numbers"]
    # 段の本数は付く（観測の欄に残す）が、印の根拠にはしない。
    assert [s["done"] for s in numbers["numbers"]["stages"]] == [1, 1]
    assert numbers["numbers"]["reactions"]["threads"]["covered_24h"] == 0
    assert numbers["observed"] is False
    assert result["evidence_level"] == "stated" and record["evidence_level"] == "stated"


def test_訪問の控えが取れればobserved(evt, tmp_path):
    site_visits.import_file(PROJECT, _visits(tmp_path, [_row("th-evt-01", 7, engaged=5)]), now=NOW)
    result = _post(from_source=("scenario", PROJECT, "evt-01"))
    assert result["evidence_level"] == "observed"
    assert plaza.STORE.get(result["plaza_id"])["tool_numbers"]["numbers"]["site_visits"]["sessions"] == 7


def test_openの写しに糸のidとサイトのホスト名と自由な段の名を出さない(evt, tmp_path):
    site_visits.import_file(PROJECT, _visits(tmp_path, [_row("th-evt-01", 7, engaged=5)]), now=NOW)
    plaza.set_membership("kp", joined=True, by="operator")
    plaza.set_membership("kt", joined=True, by="operator")
    preview = _post(from_source=("scenario", PROJECT, "evt-01"), visibility="open", title="出来事の糸")
    assert preview["report_type"] == "plaza_open_preview"
    result = _post(from_source=("scenario", PROJECT, "evt-01"), visibility="open", title="出来事の糸",
                   confirm=preview["digest"])
    shown = plaza.show(result["plaza_id"], plaza.Viewer({"kt-threads": "kt"}))
    assert shown["view"] == "open"
    text = json.dumps(shown["tool_numbers"], ensure_ascii=False)
    assert "evt-01" not in text and "kopicha.org" not in text and "himitsu" not in text
    stages = shown["tool_numbers"]["numbers"]["stages"]
    assert [s["label"] for s in stages] == ["本記事", "その他"] and [s["done"] for s in stages] == [1, 1]
    assert shown["tool_numbers"]["numbers"]["site_visits"]["sessions"] == 7
    assert shown["tool_numbers"]["numbers"]["site_visits"]["n_sites"] == 1
    assert "evt-01" not in (shown["how"] or "")
    # 持ち主の側（原本）には今までどおり全部ある。
    own = plaza.show(result["plaza_id"], plaza.Viewer({"kp-x": "kp"}))
    assert own["tool_numbers"]["numbers"]["thread"] == "evt-01"


def test_原稿の台帳が読めないとき反応を0本と言わない(closed, monkeypatch, capsys):
    from thth import scenario
    real = scenario.load_ledger

    def broken(project, names, **kwargs):
        ledger = real(project, names, **kwargs)
        ledger.items = [item for item in ledger.items if item.thread != "flow-01"]
        ledger.cannot_say.add("queue_unreadable")
        return ledger
    monkeypatch.setattr(scenario, "load_ledger", broken)
    plaza.set_owner("masaru", ["kp", "kt"], by="operator")
    record = plaza.STORE.get(_post()["plaza_id"])
    assert record["tool_numbers"]["numbers"]["posts_cannot_say"] == "queue_unreadable"
    assert "反応: 言えない（queue_unreadable）" in record["body"]
    draft = record["body"].split("\n\n")[0]
    assert "（0 本）" not in draft and "宣言したが 0 本" not in draft
    assert "宣言したが読めた原稿では 0 本（言えない・queue_unreadable）" in draft
    assert cli.main(["plaza", "digest", "kt"]) == 0
    out = capsys.readouterr().out
    assert "SNS 言えない（queue_unreadable）" in out and "SNS 0 本" not in out


def test_observeの糸の候補は宣言が無いprojectの台帳を読まず_1つの例外で他を消さない(closed, monkeypatch):
    from thth import scenario
    read = []
    real = scenario.load_ledger
    monkeypatch.setattr(scenario, "load_ledger",
                        lambda project, names, **kw: (read.append(project), real(project, names, **kw))[1])
    configs = {"kp-threads": {"project": "kp", "media": "threads"},
               "kt-threads": {"project": "kt", "media": "threads"}}
    steps = [s for s in plaza_moments.trigger_steps(configs, since=NOW, now=NOW)
             if s["candidate"] == "post_scenario"]
    assert [s["project"] for s in steps] == ["kp"] and read == ["kp"]
    real_steps = plaza_moments._project_scenario_steps

    def flaky(project, *args, **kwargs):
        if project == "aa":
            raise RuntimeError("壊れた project")
        return real_steps(project, *args, **kwargs)
    monkeypatch.setattr(plaza_moments, "_project_scenario_steps", flaky)
    configs["aa-threads"] = {"project": "aa", "media": "threads"}
    steps = [s for s in plaza_moments.trigger_steps(configs, since=NOW, now=NOW)
             if s["candidate"] == "post_scenario"]
    assert [s["project"] for s in steps] == ["kp"]


def test_CLIでaccountをfromの後ろに書いても飲み込まれない(closed, capsys):
    rc = cli.main(["plaza", "post", "--from", "scenario", PROJECT, "flow-01", "kp-threads",
                   "--scope", SCOPE, "--by", "k", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert rc == 0 and result["account"] == "kp-threads" and result["from"] == "scenario"
    rc = cli.main(["plaza", "post", "--from", "scenario", PROJECT, "flow-01", "--scope", SCOPE, "--by", "k"])
    assert rc == 2 and "account が要ります" in capsys.readouterr().err


# ------------------------------------------------------------------ observe の次の一手

def test_閉じた糸はobserveの次の一手に置く命令_組が無ければ案内(closed):
    configs = {"kp-threads": {"project": "kp", "media": "threads"}, "kp-x": {"project": "kp", "media": "x"}}
    since = NOW - datetime.timedelta(days=1)
    steps = [s for s in plaza_moments.trigger_steps(configs, since=since, now=NOW)
             if s["candidate"] == "post_scenario"]
    assert [s["thread"] for s in steps] == ["flow-01"]
    step = steps[0]
    assert step["command"] == ('thth plaza post kp-threads --kind measure --from scenario kp flow-01 '
                               '--scope "<媒体・企画の範囲>" --by <名前>')
    assert step["owner_group"] is None
    assert step["owner_hint"].startswith("thth admin plaza owner set <組の名前> kp ")
    plaza.set_owner("masaru", ["kp", "kt"], by="operator")
    step = [s for s in plaza_moments.trigger_steps(configs, since=since, now=NOW)
            if s["candidate"] == "post_scenario"][0]
    assert step["owner_group"] == "masaru" and "owner_hint" not in step
    # 置けば消える。
    _post()
    assert not [s for s in plaza_moments.trigger_steps(configs, since=since, now=NOW)
                if s["candidate"] == "post_scenario"]


def test_閉じていない糸と昔に閉じた糸は出さない(closed):
    configs = {"kp-threads": {"project": "kp", "media": "threads"}}
    later = NOW + datetime.timedelta(days=40)
    assert not [s for s in plaza_moments.trigger_steps(configs, since=later, now=later)
                if s["candidate"] == "post_scenario"]
    _write(os.path.join(closed["scen"], "flow-01.md"), FLOW)
    assert not [s for s in plaza_moments.trigger_steps(configs, since=NOW, now=NOW)
                if s["candidate"] == "post_scenario"]


def test_observeの人向けの次の一手に命令の1行(closed, monkeypatch, capsys):
    monkeypatch.setattr(morning, "_world", lambda *a, **k: morning.cell(cannot_say="no_watch_words"))
    morning.render(morning.build("kp-threads", now=NOW, mark=False), out=print)
    out = capsys.readouterr().out
    assert "広場に置く: 糸「流れの見本」を閉じた（2026-10-27・効いたかを共通の知に）  kp-threads" in out
    assert "    thth plaza post kp-threads --kind measure --from scenario kp flow-01" in out
    assert "持ち主の組がまだ無い（別の project から読めない）。管理者が: thth admin plaza owner set" in out


# ------------------------------------------------------------------ digest

def test_digestで別のprojectに試していない媒体つきで出る(closed, capsys):
    plaza.set_owner("masaru", ["kp", "kt"], by="operator")
    result = _post()
    assert result["scope"] == "owner"
    viewer, basis = plaza.viewer_for_digest("kt")
    payload = plaza.digest(viewer, target="kt", basis=basis)
    assert payload["n"] == 0          # 2 媒体で再現した数え方には入らない（追試がまだ無い）
    assert payload["n_scenario_results"] == 1 and payload["reader_media"] == ["bluesky", "threads"]
    row = payload["scenario_results"][0]
    assert row["plaza_id"] == result["plaza_id"] and row["owner"] == "kp" and row["view"] == "owner"
    assert row["media"] == {"tried": ["ads", "hatena_bookmark", "threads"], "not_tried": ["bluesky", "x"],
                            "reproduced": [], "not_reproduced": []}
    assert row["posts_by_medium"] == {"threads": 1} and row["ads_started"] == 1
    assert row["site_sessions"] is None and row["site_cannot_say"] == "site_visits_not_imported"
    assert "body" not in row and HUMAN not in json.dumps(payload, ensure_ascii=False)
    assert cli.main(["plaza", "digest", "kt"]) == 0
    out = capsys.readouterr().out
    assert "糸の結果（効いたか・道具の観測つき）1 件" in out
    assert "試していない媒体: bluesky・x" in out and "訪問 言えない（site_visits_not_imported）" in out
    # 組の外の project（other）からは見えない。
    plaza.unset_owner("masaru", by="operator")
    viewer, basis = plaza.viewer_for_digest("kt")
    assert plaza.digest(viewer, target="kt", basis=basis)["n_scenario_results"] == 0


def test_追試を付けた媒体は試していないから外れる(closed):
    plaza.set_owner("masaru", ["kp", "kt"], by="operator")
    result = _post()
    plaza.reply(result["plaza_id"], account="kt-bsky", kind="trial", result="not_reproduced", text=None,
                by="kt-session", viewer=plaza.viewer_for_target("kt-bsky"))
    viewer, basis = plaza.viewer_for_digest("kt")
    row = plaza.digest(viewer, target="kt", basis=basis)["scenario_results"][0]
    assert row["media"]["not_tried"] == ["x"] and row["media"]["not_reproduced"] == ["bluesky"]


# ------------------------------------------------------------------ CLI と MCP

def test_CLIの口(closed, capsys, tmp_path):
    body = tmp_path / "b.md"
    body.write_text(HUMAN, encoding="utf-8")
    rc = cli.main(["plaza", "post", "kp-threads", "--kind", "measure", "--from", "scenario", PROJECT, "flow-01",
                   "--body-file", str(body), "--scope", SCOPE, "--by", "k", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert rc == 0 and result["from"] == "scenario" and result["kind"] == "measure"
    rc = cli.main(["plaza", "show", result["plaza_id"], "--as", "kp-x"])
    out = capsys.readouterr().out
    assert rc == 0 and "[観測（道具が付けた数字）] scenario" in out
    assert "本記事への訪問: 言えない（site_visits_not_imported）" in out
    assert "広告: 始めた 1 本・止めたまま 0・通しの予算 500 JPY・使った額 言えない（ads_spend_not_recorded）" in out
    assert "宣言が無いので道具は数字を付けていません" not in out
    for argv in (["--from", "scenario", PROJECT], ["--from", "scenario", PROJECT, "flow-01", "--from-window-days", "7"],
                 ["--from", "after", "kp-threads", "x"]):
        rc = cli.main(["plaza", "post", "kp-threads", *argv, "--scope", SCOPE, "--by", "k"])
        assert rc == 2 and "invalid_from" in capsys.readouterr().err


def test_MCPの口は同じ引数(tmp_path, monkeypatch):
    from tests.test_v340_plaza_mcp import _post as mcp_post, _tenant, _text
    _root, server = _tenant(tmp_path, monkeypatch)
    tools = {t["name"]: t for t in server.server_tools(server.authenticated_context())}
    props = tools["thth_plaza_post"]["inputSchema"]["properties"]
    assert "scenario" in props["from_tool"]["enum"] and {"from_project", "from_thread"} <= set(props)
    missing = mcp_post(server, kind="measure", from_tool="scenario", from_project="kopicha")
    assert missing["isError"] and _text(missing).splitlines()[0] == "invalid_from"
    other = mcp_post(server, kind="measure", from_tool="scenario", from_project="other", from_thread="flow-01")
    assert other["isError"] and _text(other).splitlines()[0] == "from_out_of_scope"
    none = mcp_post(server, kind="measure", from_tool="scenario", from_project="kopicha", from_thread="nai-ito")
    assert none["isError"] and _text(none).splitlines()[0] == "from_scenario_not_found"
    mixed = mcp_post(server, kind="measure", from_tool="after", from_account="first", from_thread="x")
    assert mixed["isError"] and _text(mixed).splitlines()[0] == "invalid_from"
