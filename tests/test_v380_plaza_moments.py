"""3.8.0 B 読む瞬間を道具が作る（設計 3.8.0 §B）。

見るのは:
  1. observe の 0 段に「同じ持ち主の他の媒体の新しい書き込み 1 件」——**1 日 1 件・
     読んだものは出さない**。選び方は地図の点・原稿の goal・topic の重なり。組に登録して
     いない project の書き込みは出さない。
  2. approve の 1 段目と lint に、同じ goal か同じ topic の書き込みを 1〜3 件——**題と id
     だけ（本文は出さない）**。
  3. `plaza list` が 0 件か自分の書き込みだけのとき、管理者に頼む命令の形の案内 1 行。
  4. `ask before-you-post --goal` に「同じ goal で他の媒体ではこうだった」の 1 行
     （広場の observed の書き込みがあるときだけ）。
  「読んだ」の記録は持ち主ごと・account ごとに私有の置き場（id と時刻だけ）。
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import stat

import pytest

from thth import ask as ask_mod, cli, jst, map_store, morning, plaza, plaza_moments, plaza_reads
from tests.conftest import run_thth, write_queue_file
from tests.test_v340_plaza_store import owners, post  # noqa: F401  (fixture)
from tests.test_v380_plaza_owner import grouped, viewer  # noqa: F401  (fixture)

BODY_SECRET = "PLAZA-BODY-SECRET 本文はここだけ"


@pytest.fixture
def quiet(grouped, monkeypatch):
    monkeypatch.setattr(morning, "_world", lambda *a, **k: morning.cell(cannot_say="no_watch_words"))
    return grouped


def _pick(payload):
    return next(s for s in payload["sections"] if s["section"] == "tool")["value"]["plaza"]["pick"]


# ------------------------------------------------------------ observe の 1 件

def test_observeに同じ持ち主の他の媒体の1件_自分の書いたものと組の外は出さない(quiet):
    now = jst.now_jst()
    mine = post(account="kopicha-threads", title="自分の気づき", now=now - datetime.timedelta(hours=3))
    theirs = post(account="kopicha-bsky", title="Bluesky の気づき", body=BODY_SECRET,
                  now=now - datetime.timedelta(hours=2))
    post(account="other-threads", title="OTHER-PROJECT-ONLY", by="o",
         now=now - datetime.timedelta(hours=1))
    cell = _pick(morning.build("kopicha-threads", now=now, mark=False))
    assert cell["pick"]["plaza_id"] == theirs["plaza_id"] and cell["denominator"] == 1
    assert cell["pick"]["command"] == f"thth plaza show {theirs['plaza_id']} --as kopicha-threads"
    text = json.dumps(cell, ensure_ascii=False)
    assert BODY_SECRET not in text and mine["plaza_id"] not in text
    assert "OTHER-PROJECT-ONLY" not in text


def test_組に登録した他のprojectの書き込みは出る_組に無ければ出ない(quiet):
    now = jst.now_jst()
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    owner_post = post(account="other-threads", title="組の気づき", by="o", visibility="owner",
                      now=now - datetime.timedelta(hours=1))
    cell = _pick(morning.build("kopicha-threads", now=now, mark=False))
    assert cell["pick"]["plaza_id"] == owner_post["plaza_id"] and cell["pick"]["view"] == "owner"
    third = _pick(morning.build("third-threads", now=now, mark=False))
    assert third["pick"] is None and third["denominator"] == 0
    assert owner_post["plaza_id"] not in json.dumps(third, ensure_ascii=False)


def test_1日1件_読んだものは翌日も出さない(quiet):
    now = jst.now_jst()
    first = post(account="kopicha-bsky", title="1 件目", now=now - datetime.timedelta(hours=3))
    second = post(account="kopicha-mstdn", title="2 件目", now=now - datetime.timedelta(hours=2))
    shown = _pick(morning.build("kopicha-threads", now=now, mark=True))
    assert shown["pick"]["plaza_id"] == second["plaza_id"] and shown["recorded"] is True
    # 同じ日にもう一度: 出さない（1 日 1 件）。
    again = _pick(morning.build("kopicha-threads", now=now + datetime.timedelta(hours=1), mark=True))
    assert again["pick"] is None and again["reason"] == "picked_today"
    # 翌日: 出した 1 件は読んだものなので出さず、残りの 1 件。
    tomorrow = now + datetime.timedelta(days=1)
    next_day = _pick(morning.build("kopicha-threads", now=tomorrow, mark=True))
    assert next_day["pick"]["plaza_id"] == first["plaza_id"]
    # その次の日: 読んでいないものが無い。
    last = _pick(morning.build("kopicha-threads", now=tomorrow + datetime.timedelta(days=1),
                               mark=True))
    assert last["pick"] is None and last["reason"] == "no_unread_posts"


def test_showで読んだものはobserveに出さない(quiet, capsys):
    now = jst.now_jst()
    read_one = post(account="kopicha-bsky", title="読んだ", now=now - datetime.timedelta(hours=1))
    unread = post(account="kopicha-mstdn", title="読んでいない", now=now - datetime.timedelta(hours=2))
    rc = cli.main(["plaza", "show", read_one["plaza_id"], "--as", "kopicha-threads", "--json"])
    assert rc == 0 and json.loads(capsys.readouterr().out)["read_recorded"] is True
    cell = _pick(morning.build("kopicha-threads", now=now, mark=False))
    assert cell["pick"]["plaza_id"] == unread["plaza_id"] and cell["denominator"] == 1
    # 控えは account ごと: kopicha-bsky の observe では、bsky 自身の書き込みを除いて未読の 1 件。
    other = _pick(morning.build("kopicha-bsky", now=now, mark=False))
    assert other["pick"]["plaza_id"] == unread["plaza_id"]


def test_読んだの記録はidと時刻だけ_持ち主ごとに私有(quiet):
    now = jst.now_jst()
    theirs = post(account="kopicha-bsky", title="TITLE-NOT-STORED", body=BODY_SECRET,
                  now=now - datetime.timedelta(hours=1))
    morning.build("kopicha-threads", now=now, mark=True)
    directory = Path(os.environ["THTH_ROOT"]) / "state" / "_plaza_reads"
    path = directory / "p-kopicha.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    raw = path.read_text(encoding="utf-8")
    assert "TITLE-NOT-STORED" not in raw and "PLAZA-BODY" not in raw
    data = json.loads(raw)
    row = data["accounts"]["kopicha-threads"]
    assert set(row) == {"read", "picked"} and list(row["read"]) == [theirs["plaza_id"]]
    assert list(row["picked"].values()) == [theirs["plaza_id"]]
    assert sorted(p.name for p in directory.glob("*.json")) == ["p-kopicha.json"]


def test_点とgoalとtopicの重なりで選ぶ(quiet):
    now = jst.now_jst()
    map_store.add_node("kopicha", "紅茶", by="operator")
    plain = post(account="kopicha-bsky", title="朝の投稿", now=now - datetime.timedelta(minutes=10))
    tea = post(account="kopicha-mstdn", title="紅茶の問い", now=now - datetime.timedelta(hours=5))
    cell = _pick(morning.build("kopicha-threads", now=now, mark=False))
    assert cell["pick"]["plaza_id"] == tea["plaza_id"] and cell["pick"]["matched"] == ["map:紅茶"]
    assert plain["plaza_id"]
    lines = []
    morning.render(morning.build("kopicha-threads", now=now, mark=False), out=lines.append)
    assert any(line.strip().startswith("広場の 1 件（同じ持ち主の他の媒体・未読 2 件から今日の 1 件）: "
                                       + tea["plaza_id"]) for line in lines)


def test_原稿のgoalと重なる書き込みを先に(quiet):
    now = jst.now_jst()
    account = quiet["kopicha-threads"]
    write_queue_file(account["queue_dir"], "g.md",
                     fm_overrides={"account": "kopicha-threads", "status": "draft",
                                   "approved_sha": None, "goal": "click",
                                   "publish_at": "2026-09-30T08:00:00+09:00"})
    post(account="kopicha-bsky", title="新しい", now=now - datetime.timedelta(minutes=5))
    goal_post = post(account="kopicha-mstdn", title="古いが同じ目的", goal="click",
                     now=now - datetime.timedelta(hours=6))
    cell = _pick(morning.build("kopicha-threads", now=now, mark=False))
    assert cell["pick"]["plaza_id"] == goal_post["plaza_id"] and "goal:click" in cell["pick"]["matched"]


# ------------------------------------------------------------ approve と lint

def _draft(account, name="a.md", goal="reach", topic="紅茶"):
    return write_queue_file(account["queue_dir"], name,
                            body="## threads\n\n承認する本文です。\n",
                            fm_overrides={"account": account["name"], "status": "draft",
                                          "approved_sha": None, "goal": goal, "topic": topic,
                                          "publish_at": "2026-09-30T08:00:00+09:00"})


def test_approveの1段目に同じgoalとtopicの書き込みを題とidだけ(quiet):
    same_goal = post(account="kopicha-bsky", title="REACH-TITLE", goal="reach", body=BODY_SECRET)
    same_topic = post(account="kopicha-mstdn", title="紅茶は朝", body=BODY_SECRET)
    post(account="kopicha-mstdn", title="関係ない", body=BODY_SECRET)
    post(account="other-threads", title="OTHER-REACH", goal="reach", by="o", body=BODY_SECRET)
    post(account="kopicha-threads", title="自分の reach", goal="reach", body=BODY_SECRET)
    path = _draft(quiet["kopicha-threads"])
    first = run_thth(["approve", path])
    assert first.returncode == 1, first.stderr
    out = first.stdout
    assert "広場: 同じ goal・topic の書き込み 2 件（2 件のうち・題と id だけ）" in out
    assert f"{same_goal['plaza_id']}  REACH-TITLE（bluesky・goal reach）" in out
    assert f"{same_topic['plaza_id']}  紅茶は朝（mastodon・topic 紅茶）" in out
    assert "PLAZA-BODY" not in out and "OTHER-REACH" not in out and "自分の reach" not in out
    as_json = json.loads(run_thth(["approve", path, "--json"]).stdout)
    related = as_json["files"][0]["plaza_related"]
    assert [set(item) for item in related["items"]] == [{"plaza_id", "title", "medium", "match"}] * 2
    assert "PLAZA-BODY" not in json.dumps(as_json, ensure_ascii=False)


def test_lintにも題とidだけ_無ければ何も足さない(quiet, capsys):
    path = _draft(quiet["kopicha-threads"], goal="follow", topic="緑茶")
    rc = cli.main(["lint", path])
    assert rc == 0 and capsys.readouterr().out == "OK\n"
    found = post(account="kopicha-bsky", title="FOLLOW-TITLE", goal="follow", body=BODY_SECRET)
    rc = cli.main(["lint", path])
    out = capsys.readouterr().out
    assert rc == 0 and out.startswith("OK\n")
    assert f"  {found['plaza_id']}  FOLLOW-TITLE（bluesky・goal follow）" in out
    assert "PLAZA-BODY" not in out
    rc = cli.main(["lint", path, "--json"])
    row = json.loads(capsys.readouterr().out)
    assert row["ok"] is True and row["plaza_related"]["items"][0]["plaza_id"] == found["plaza_id"]


def test_組の相手の書き込みもapproveに並ぶ_組の外は並ばない(quiet):
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    theirs = post(account="other-threads", title="組の reach", goal="reach", by="o",
                  visibility="owner", body=BODY_SECRET)
    cell = plaza_moments.related("kopicha-threads", goal="reach", topic=None)
    assert [item["plaza_id"] for item in cell["items"]] == [theirs["plaza_id"]]
    assert plaza_moments.related("third-threads", goal="reach", topic=None)["items"] == []


# ------------------------------------------------------------ list の案内

def test_listが自分の書き込みだけなら管理者に頼む命令の形の案内(quiet, capsys):
    rc = cli.main(["plaza", "list", "kopicha"])
    out = capsys.readouterr().out
    assert rc == 0 and "広場: 読めるのはまだ 0 件です" in out
    assert "thth admin plaza owner set <組の名前> kopicha <同じ持ち主の他の project> --by <名前>" in out
    assert "thth admin plaza join kopicha --by <名前>" in out
    post(account="kopicha-bsky", title="自分の持ち主の")
    payload = plaza.list_posts(viewer("kopicha-threads"))
    assert payload["hint"]["reason"] == "only_own_posts"
    # 組と参加がそろえば、頼むことが無いので何も言わない。
    plaza.set_owner("masaru", ["kopicha", "other"], by="operator")
    plaza.set_membership("kopicha", joined=True, by="operator")
    assert plaza.list_posts(viewer("kopicha-threads"))["hint"] is None
    # 組の相手の書き込みが読めるなら案内は出さない。
    plaza.set_membership("kopicha", joined=False, by="operator")
    post(account="other-threads", title="組", by="o", visibility="owner")
    assert plaza.list_posts(viewer("kopicha-threads"))["hint"] is None


# ------------------------------------------------------------ ask before-you-post

def _observed_measure(account, goal):
    """道具が付けた観測を持つ施策（観測の列を直接置く・計算は 3.4.0 の試験が見ている）。"""
    result = post(account=account, kind="measure", title=f"{account} の施策", goal=goal,
                  body="本文には views 999 と書いた")
    record = plaza.STORE.get(result["plaza_id"])
    record["observations"] = [{"at": record["at"], "trigger": "posted", "by": "t", "min_n": 5,
                               "columns": [{"account": account, "medium": record["medium"],
                                            "observed": True, "metrics": {"views_24h": {
                                                "observational_difference": 12,
                                                "baseline_n_eligible": 5, "changed_n_eligible": 6,
                                                "baseline_median": 100, "changed_median": 112,
                                                "reason": None}}}]}]
    with plaza.STORE.locked() as directory:
        plaza.STORE.write(directory, record)
    return result


def test_askに同じgoalの他の媒体の観測_observedがあるときだけ(quiet):
    stated = post(account="kopicha-bsky", title="観測なし", goal="reach")
    measure = _observed_measure("kopicha-mstdn", "reach")
    same = plaza_moments.same_goal("kopicha-threads", "reach", medium="threads")
    assert same["n"] == 1 and same["items"][0]["plaza_id"] == measure["plaza_id"]
    assert same["line"].startswith(f"広場: 同じ goal（reach）で他の媒体では mastodon {measure['plaza_id']}")
    assert "views_24h +12（n=5/6）" in same["line"] and "999" not in same["line"]
    assert stated["plaza_id"] not in json.dumps(same, ensure_ascii=False)
    # 同じ媒体の書き込みは「他の媒体」ではない。goal が違えば出さない。
    assert plaza_moments.same_goal("kopicha-threads", "reach", medium="mastodon") is None
    assert plaza_moments.same_goal("kopicha-threads", "click", medium="threads") is None


def test_askのCLIとMCPにgoalを渡すと1行(quiet, capsys):
    _observed_measure("kopicha-mstdn", "reach")
    rc = cli.main(["ask", "before-you-post", "kopicha-threads", "--topic", "紅茶", "--goal", "reach"])
    out = capsys.readouterr().out
    assert rc == 0 and "広場: 同じ goal（reach）で他の媒体では mastodon" in out
    rc = cli.main(["ask", "before-you-post", "kopicha-threads", "--topic", "紅茶", "--json"])
    assert rc == 0 and "plaza_same_goal" not in json.loads(capsys.readouterr().out)
    answer = ask_mod.before_you_post("kopicha-threads", topic="紅茶")
    assert "plaza_same_goal" not in answer
    from tests.test_mcp import _load_server_module
    tool = next(t for t in _load_server_module().TOOLS if t["name"] == "before_you_post")
    assert tool["inputSchema"]["properties"]["goal"]["enum"] == ["reach", "click", "follow", "reply"]


def test_読んだの記録は壊れていれば言えないと言う(quiet):
    directory = Path(os.environ["THTH_ROOT"]) / "state" / "_plaza_reads"
    post(account="kopicha-bsky", title="何か")
    morning.build("kopicha-threads", now=jst.now_jst(), mark=True)
    (directory / "p-kopicha.json").write_text("{broken", encoding="utf-8")
    cell = _pick(morning.build("kopicha-threads", now=jst.now_jst(), mark=False))
    assert cell["pick"] is None and cell["cannot_say"] == "plaza_store_unavailable"
    with pytest.raises(plaza.PlazaError, match="^plaza_store_unavailable$"):
        plaza_reads.load({"kopicha-threads": "kopicha"})
