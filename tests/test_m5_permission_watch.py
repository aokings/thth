"""段 M5 の柵: 権限の不使用の見張り（計画 Meta 全部盛り §3.3・§4 の M5 の (a)(b)(c)）。

守ること:
1. 権限ごとの最後に成功した日は今ある記録から出す。失敗の行は成功に数えない。本文を新しく残さない。
2. 線は 21・25・28 日（利用者ごとは 75・80・90 日）。Standard の権限は鳴らない。
3. 知らせの先は board の `stuck` と observe の 1 行。本物の止まりが先。既存の鍵は変えない。
4. 最小の読みは読むだけの権限だけ・GET だけ。書く権限は知らせだけ。招待の口座の鍵を使わない。X に触らない。
   Meta の待ちと高い使用率の間は打たない。
5. 広告の 15 日の数とエラー率・データの期限・Data Use Checkup の期日の台帳。

偽の Graph API（loopback）と試験の中で作った値だけを使う。本物の Meta には触らない。
"""
from __future__ import annotations

import datetime
import json
import os

import pytest

from tests.test_maintain import _account, _write_token
from tests.test_meta_stage2 import fake_graph
from tests.test_v340_plaza_morning import quiet  # noqa: F401
from tests.test_v340_plaza_store import owners  # noqa: F401
from thth import accounts, cli, jst, morning, permission_watch as pw, report, runs, scopes, systemd_gen
from thth.adapters import facebook, instagram, threads
from thth.ads import auth as ads_auth
from thth.ads import ledger as ads_ledger
from thth.meta import calls, graph as meta_graph, usage as meta_usage

NOW = datetime.datetime(2026, 11, 20, 10, 0, tzinfo=jst.JST)
V = "/" + meta_graph.GRAPH_VERSION
TOKEN = "M5-SECRET-TOKEN-value-0123456789"
BODY = "本文は残さない-M5"
THREADS_READS = sorted(threads.ThreadsAdapter.KEEPALIVE_READS)


def ago(days):
    return NOW - datetime.timedelta(days=days)


class World:
    """偽の台帳。口座の鍵は本物のファイル、状態は `THTH_ROOT` の下。"""

    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.cfgs = {}
        self.ads = {}
        monkeypatch.setattr(accounts, "list_account_names", lambda: sorted(self.cfgs))
        monkeypatch.setattr(accounts, "load_account", lambda name: self.cfgs[name])
        monkeypatch.setattr(ads_ledger, "list_names", lambda: sorted(self.ads))
        monkeypatch.setattr(ads_ledger, "load", lambda name: self.ads[name])

    def _token(self, name, obtained, **extra):
        path = self.tmp / f"{name}.token"
        path.write_text(json.dumps({"access_token": TOKEN, "user_id": "999", "expires_in": 5184000,
                                    "obtained_at": jst.iso(ago(obtained)), **extra}))
        return str(path)

    def add(self, name, media="threads", *, obtained=1, invite=False, ledger=None, **token):
        self.cfgs[name] = {"account": name, "media": media, "token": self._token(name, obtained, **token),
                           **({"provenance": {"created_via": "invite"}} if invite else {}), **(ledger or {})}
        os.makedirs(accounts.state_dir_for(name), exist_ok=True)
        return name

    def add_ads(self, name, *, obtained=1, **token):
        self.ads[name] = {"act_id": "123", "token": self._token(name, obtained, **token)}
        return name

    def run(self, name, action, days, *, status="ok", state=None, **extra):
        at = ago(days)
        runs.append_run(state or accounts.state_dir_for(name),
                        {"account": name, "run_id": f"{action}-{jst.iso(at)}", "mode": "read", "action": action,
                         "status": status, "error": None if status == "ok" else "x", **extra}, jst.month_str(at))

    def sent(self, name, post_id, days, **row):
        path = os.path.join(accounts.state_dir_for(name), "sent")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, post_id + ".json"), "w", encoding="utf-8") as stream:
            json.dump({"post_id": post_id, "sent_at": jst.iso(ago(days)), "text": BODY, **row}, stream)


@pytest.fixture
def world(tmp_path, monkeypatch, thth_root, frozen_now_jst):
    frozen_now_jst(NOW)
    return World(tmp_path, monkeypatch)


def row_of(view, permission):
    return next(row for row in view["permissions"] if row["permission"] == permission)


def fresh_all(world, name, days=1):
    """Threads の見張る権限を全部 `days` 日前に使った形にする。"""
    world.run(name, "collect", days, collected=1)
    world.run(name, "where_to_appear", days)
    world.run(name, "who_is_this", days, profile_fetched=True)
    world.run(name, pw.ACTION, days, permission="threads_location_tagging")
    world.sent(name, "1", days, retracted_at=jst.iso(ago(days)))
    world.sent(name, "2", days, reply_to="1")


# ---------------------------------------------------------------- (a) 最後に成功した日

def test_線の数は決めたとおり():
    assert (pw.WARN_DAYS, pw.KEEP_DAYS, pw.LIMIT_DAYS) == (21, 25, 28)
    assert (pw.USER_WARN_DAYS, pw.USER_KEEP_DAYS, pw.USER_LIMIT_DAYS) == (75, 80, 90)


@pytest.mark.parametrize("days, stage", [(20, "quiet"), (21, "warn"), (24, "warn"), (25, "keep"), (27, "keep"),
                                         (28, "over")])
def test_線の前後の段(world, days, stage):
    name = world.add("a-threads")
    world.run(name, "collect", days, collected=1)
    row = row_of(pw.build(), "threads_basic")
    assert (row["days_idle"], row["stage"]) == (days, stage)


def test_runsとsentから権限ごとの日を出す(world):
    name = world.add("a-threads")
    world.run(name, "collect", 3, collected=1)
    world.run(name, "collect", 1, collected=0)
    world.run(name, "where_to_appear", 4)
    world.run(name, "who_is_this", 6, profile_fetched=True)
    world.run(name, "who_is_this", 2, profile_fetched=False)
    world.sent(name, "1", 7)
    world.sent(name, "2", 9, reply_to="1")
    world.sent(name, "3", 12, retracted_at=jst.iso(ago(5)))
    view = pw.build()
    assert row_of(view, "threads_basic")["days_idle"] == 1
    assert row_of(view, "threads_manage_insights")["days_idle"] == 3   # 0 本の回は投稿を読んでいない
    assert row_of(view, "threads_keyword_search")["days_idle"] == 4
    assert row_of(view, "threads_profile_discovery")["days_idle"] == 6  # 引けなかった回は数えない
    assert row_of(view, "threads_content_publish")["days_idle"] == 7
    assert row_of(view, "threads_manage_replies")["days_idle"] == 9
    assert row_of(view, "threads_delete")["days_idle"] == 5
    assert row_of(view, "threads_basic")["last_success_account"] == name


def test_失敗の行は成功に数えない(world):
    name = world.add("a-threads", obtained=40)
    world.run(name, "collect", 1, status="error", collected=1)
    world.run(name, pw.ACTION, 1, status="error", permission="threads_basic")
    row = row_of(pw.build(), "threads_basic")
    assert row["last_success_at"] is None and row["counted_from"] == "token_obtained"
    assert row["days_idle"] == 40 and row["keepalive_error"] == "x"


def test_記録が無ければ鍵の取得から数え_鍵が無ければ言わない(world):
    world.add("a-threads", obtained=22)
    view = pw.build()
    assert row_of(view, "threads_delete")["stage"] == "warn"
    assert row_of(view, "ads_read")["stage"] == "not_held"


def test_招待の口座の使用もアプリの使用に数える(world):
    world.add("a-threads", obtained=40)
    world.add("guest-threads", invite=True)
    world.run("guest-threads", "collect", 2, collected=1)
    row = row_of(pw.build(), "threads_basic")
    assert (row["days_idle"], row["last_success_account"]) == (2, "guest-threads")


def test_広告はsnapshotとopsから(world):
    name = world.add_ads("a-ads", obtained=50)
    state = ads_ledger.state_dir_for(name)
    os.makedirs(os.path.join(state, "ops"))
    with open(os.path.join(state, "snapshot.json"), "w", encoding="utf-8") as stream:
        json.dump({"last_ok_at": jst.iso(ago(3))}, stream)
    with open(os.path.join(state, "ops", "1-boost.json"), "w", encoding="utf-8") as stream:
        json.dump({"op": "boost", "at": jst.iso(ago(8)), "result": "ok"}, stream)
    with open(os.path.join(state, "ops", "2-boost.json"), "w", encoding="utf-8") as stream:
        json.dump({"op": "boost", "at": jst.iso(ago(1)), "result": "failed"}, stream)
    view = pw.build()
    assert row_of(view, "ads_read")["days_idle"] == 3
    assert row_of(view, "ads_management")["days_idle"] == 8


def test_認可が求める権限は全部表にある():
    wanted = {*scopes.DEFAULT_SCOPES, *facebook.SCOPES, *instagram.SCOPES, *ads_auth.SCOPES}
    for rows in scopes.META_INVITE_EXTRA.values():
        wanted |= set(rows)
    assert sorted(wanted - set(pw.PERMISSIONS)) == []


def test_Standardの権限は鳴らず_審査が通って足すと鳴る(world):
    world.add("a-fb", "facebook", obtained=40, ledger={"page_id": "55"},
              scopes=["pages_read_engagement"])
    assert pw.build()["notices"] == []
    assert cli.main(["admin", "meta", "set", "advanced", "pages_read_engagement", "--by", "t"]) == 0
    view = pw.build()
    assert row_of(view, "pages_read_engagement")["watched"] is True
    assert [row["code"] for row in view["notices"]] == ["permission_unused"]


def test_鍵に乗っていない権限は見ない(world):
    world.add("a-threads", obtained=40, scopes=["threads_basic"])
    view = pw.build()
    assert row_of(view, "threads_delete")["stage"] == "not_held"
    assert view["notices"][0]["permissions"] == ["threads_basic"]


def test_利用者ごとの90日は持ち主の口座の行に出る(world):
    world.add("a-threads", obtained=76)
    world.add("b-threads")
    fresh_all(world, "b-threads")
    view = pw.build()
    assert {row["account"] for row in view["accounts"]} == {"a-threads"}
    assert {row["stage"] for row in view["accounts"]} == {"warn"}
    assert pw.stuck_rows(view)["a-threads"]["code"] == "permission_unused"
    assert "b-threads" not in pw.stuck_rows(view)


# ---------------------------------------------------------------- 知らせの先（board・observe）

def test_stuckの形はhubの読む形に足すだけ(world):
    world.add("a-threads", obtained=22)
    row = pw.stuck_rows(pw.build())["a-threads"]
    assert {"code", "since", "what", "fix"} <= set(row) and row["level"] == "yellow"
    assert row["code"] == "permission_unused" and row["fix"] and row["better"]
    assert TOKEN not in json.dumps(row, ensure_ascii=False)


def test_28日を越えると赤で_アプリの話は1口座にだけ載る(world):
    world.add("a-threads", obtained=29)
    world.add("b-threads", obtained=29)
    rows = pw.stuck_rows(pw.build())
    assert list(rows) == ["a-threads"]
    assert rows["a-threads"]["level"] == "red"


def test_知らせが重なると重いほうを載せて残りの数を添える(world):
    world.add("a-threads", obtained=22)
    pw.set_ledger("duc_due", "2026-11-30", by="t")
    row = pw.stuck_rows(pw.build())["a-threads"]
    assert row["code"] == "permission_unused" and "ほか 1 件" in row["what"]


@pytest.fixture
def board_account(tmp_path, isolated_account_factory, thth_root, frozen_now_jst):
    frozen_now_jst(NOW)
    return _account(isolated_account_factory, tmp_path, age_days=22)


def _board_row(account):
    summary = report.board_summary(now=account["now"])
    return summary, next(row for row in summary["accounts"] if row["account"] == account["name"])


def test_boardのstuckに出て_使えば消える(board_account):
    summary, row = _board_row(board_account)
    assert row["stuck"]["code"] == "permission_unused"
    assert summary["meta_watch"]["notices"][0]["code"] == "permission_unused"
    state = accounts.state_dir_for(board_account["name"])
    runs.append_run(state, {"account": board_account["name"], "run_id": f"collect-{jst.iso(NOW)}", "mode": "collect",
                            "action": "collect", "status": "ok", "error": None}, jst.month_str(NOW))
    assert _board_row(board_account)[1]["stuck"] is None


def test_本物の止まりが先に出る(board_account, monkeypatch):
    from thth import incident
    real = {"code": "token_expired", "since": None, "what": "w", "fix": "f"}
    monkeypatch.setattr(incident, "stuck", lambda name, state: real)
    assert _board_row(board_account)[1]["stuck"] == real


def test_見張りが読めなくてもboardは落ちない(board_account, monkeypatch):
    monkeypatch.setattr(pw, "build", lambda **kw: 1 / 0)
    summary, row = _board_row(board_account)
    assert summary["meta_watch"] == {"cannot_say": "unavailable"} and row["stuck"] is None


def _today(payload):
    return next(s for s in payload["sections"] if s["section"] == "today")["value"]


def test_observeの1行は管理者の1枚だけ(quiet, monkeypatch, capsys):  # noqa: F811
    now = jst.now_jst()
    monkeypatch.setattr(pw, "observe_line", lambda view: None)
    assert "meta_watch" not in _today(morning.build("kopicha", now=now, mark=False))
    monkeypatch.setattr(pw, "observe_line", lambda view: "Meta の見張り: 例")
    admin = morning.build("kopicha", now=now, mark=False)
    assert _today(admin)["meta_watch"] == "Meta の見張り: 例"
    morning.render(admin)
    assert "Meta の見張り: 例" in capsys.readouterr().out
    tenant = morning.build("kopicha", now=now, mark=False, allowed_names=("kopicha-bsky",))
    assert "meta_watch" not in _today(tenant)


def test_observeの文は1行で_何も無ければ黙る(world):
    assert pw.observe_line(pw.build()) is None
    world.add("a-threads")
    fresh_all(world, "a-threads")
    pw.set_ledger("duc_due", "2027-10-09", by="t")
    assert pw.observe_line(pw.build()) is None
    world.add("b-threads", obtained=30)
    pw.set_ledger("duc_due", "2026-11-30", by="t")
    line = pw.observe_line(pw.build())
    assert "\n" not in line and "thth admin meta watch" in line and "Data Use Checkup" in line


# ---------------------------------------------------------------- 最小の読み

def threads_routes(rows=None):
    good = (200, {"data": rows if rows is not None else [{"id": "42", "text": BODY}]})
    return {("GET", "/v1.0/me"): (200, {"id": "999"}),
            ("GET", "/v1.0/999/threads"): good,
            ("GET", "/v1.0/42/conversation"): (200, {"data": [{"id": "1", "text": BODY}]}),
            ("GET", "/v1.0/999/threads_insights"): (200, {"data": []}),
            ("GET", "/v1.0/keyword_search"): (200, {"data": []}),
            ("GET", "/v1.0/999/mentions"): (200, {"data": []}),
            ("GET", "/v1.0/profile_lookup"): (200, {"username": "threads"}),
            ("GET", "/v1.0/location_search"): (200, {"data": []})}


def test_打てる権限は読むだけの権限に限る():
    from thth.adapters import REGISTRY
    doors = set(pw.ADS_KEEPALIVE_READS)
    for name in pw.META_MEDIA:
        doors |= set(REGISTRY[name].KEEPALIVE_READS)
    assert sorted(door for door in doors if pw.PERMISSIONS[door]["kind"] != pw.READ) == []


def test_読むだけの権限にだけ打ち_書く権限は打たず本文を残さない(world, monkeypatch):
    name = world.add("a-threads", obtained=26)
    with fake_graph(threads_routes()) as (base, seen):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base)
        result = pw.keepalive()
    assert sorted(row["permission"] for row in result["results"]) == THREADS_READS
    assert {row["result"] for row in result["results"]} == {"ok"}
    assert {call["method"] for call in seen} == {"GET"}
    view = pw.build()
    for permission in THREADS_READS:
        assert row_of(view, permission)["days_idle"] == 0
    assert row_of(view, "threads_content_publish")["days_idle"] == 26   # 書く権限は知らせに残る
    assert row_of(view, "threads_delete")["stage"] == "keep"
    text = open(runs.path_for(accounts.state_dir_for(name), "2026-11"), encoding="utf-8").read()
    assert BODY not in text and TOKEN not in text
    assert sorted(json.loads(line)["permission"] for line in text.splitlines()) == THREADS_READS


def test_25日の手前では打たない(world, monkeypatch):
    world.add("a-threads", obtained=24)
    with fake_graph({}) as (base, seen):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base)
        assert pw.keepalive()["results"] == []
    assert seen == []


def test_同じ日の2回目は打たない(world, monkeypatch):
    world.add("a-threads", obtained=26)
    with fake_graph({}) as (base, seen):   # 全部 404 で失敗の行が残る
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base)
        first = pw.keepalive()
        count = len(seen)
        second = pw.keepalive()
    assert {row["result"] for row in first["results"]} == {"error"}
    assert {row["reason"] for row in second["results"]} == {"keepalive_tried_today"}
    assert len(seen) == count


def test_失敗は符丁で残り_rcが1になる(world, monkeypatch, capsys):
    world.add("a-threads", obtained=26)
    with fake_graph({}) as (base, _seen):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base)
        assert cli.main(["admin", "meta", "keepalive"]) == 1
    assert "permission_keepalive_denied" in capsys.readouterr().out
    assert row_of(pw.build(), "threads_basic")["keepalive_error"] == "permission_keepalive_denied"


def test_投稿が無ければ打てないと言う(world, monkeypatch):
    world.add("a-threads", obtained=26)
    with fake_graph(threads_routes(rows=[])) as (base, _seen):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base)
        result = pw.keepalive()
    row = next(item for item in result["results"] if item["permission"] == "threads_read_replies")
    assert row["error"] == "permission_keepalive_no_target"


def test_dry_runは網に出ず何も書かない(world, monkeypatch, capsys):
    name = world.add("a-threads", obtained=26)
    with fake_graph({}) as (base, seen):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base)
        assert cli.main(["admin", "meta", "keepalive", "--dry-run", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert {row["result"] for row in out["results"]} == {"would_call"} and out["called"] == 0
    assert seen == [] and not os.path.exists(runs.path_for(accounts.state_dir_for(name), "2026-11"))


def test_1回の本数に上限がある(world, monkeypatch):
    world.add("a-threads", obtained=26)
    monkeypatch.setattr(pw, "MAX_CALLS_PER_RUN", 2)
    with fake_graph(threads_routes()) as (base, seen):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base)
        result = pw.keepalive()
    assert result["called"] == 2
    assert {row["reason"] for row in result["results"] if row["result"] == "skipped"} == {"keepalive_next_run"}


def test_招待の口座の鍵では打たない(world, monkeypatch):
    world.add("guest-threads", obtained=26, invite=True)
    with fake_graph({}) as (base, seen):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base)
        assert pw.keepalive()["results"] == []
    assert seen == []


def test_持ち主の口座は80日で打つ(world, monkeypatch):
    world.add("a-threads")
    fresh_all(world, "a-threads")
    world.add("b-threads", obtained=81)
    with fake_graph(threads_routes()) as (base, _seen):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base)
        result = pw.keepalive()
    assert {row["account"] for row in result["results"]} == {"b-threads"}


def test_Xの口座は見ず予算も触らない(world, monkeypatch):
    from thth import budget_x, budget_x_posts
    world.add("a-x", "x", obtained=40)
    monkeypatch.setattr(budget_x, "report", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(budget_x_posts, "report", lambda *a, **k: 1 / 0)
    with fake_graph({}) as (base, seen):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base)
        assert pw.keepalive()["results"] == []
    assert seen == []
    assert all(row["holders"] == 0 for row in pw.build()["permissions"])


def facebook_world(world, monkeypatch, name="a-fb", media="facebook", **ledger):
    pw.set_ledger("advanced", ",".join(sorted(facebook.FacebookAdapter.KEEPALIVE_READS
                                              | instagram.InstagramAdapter.KEEPALIVE_READS)), by="t")
    world.add(name, media, obtained=26, ledger={"page_id": "55", **ledger}, user_id="55")
    monkeypatch.setattr(meta_graph, "load_app_secret", lambda: None)
    return name


def test_Metaの待ちの間は打たない(world, monkeypatch):
    name = facebook_world(world, monkeypatch)
    meta_usage.note_rate_limited(name, {}, 80001, now=NOW)
    with fake_graph({}) as (base, seen):
        monkeypatch.setenv(meta_graph.BASE_URL_ENV, base)
        result = pw.keepalive()
    assert {row["reason"] for row in result["results"]} == {"meta_rate_waiting"}
    assert seen == []


@pytest.mark.parametrize("minutes, skipped", [(5, True), (120, False)])
def test_使用率が高い間は打たず_古い値では止めない(world, monkeypatch, minutes, skipped):
    name = facebook_world(world, monkeypatch)
    headers = {"x-app-usage": json.dumps({"call_count": 50, "total_cputime": 1, "total_time": 1})}
    meta_usage.observe(name, headers, now=NOW - datetime.timedelta(minutes=minutes))
    with fake_graph({("GET", V + "/me"): (200, {"id": "55"})}) as (base, seen):
        monkeypatch.setenv(meta_graph.BASE_URL_ENV, base)
        result = pw.keepalive()
    assert ({row.get("reason") for row in result["results"]} == {"keepalive_quota_high"}) is skipped
    assert (seen == []) is skipped


def test_Facebookの読みは1回か2回で本文を残さない(world, monkeypatch):
    name = facebook_world(world, monkeypatch)
    post = {"id": "55_1", "message": BODY, "created_time": "2026-11-01T00:00:00+0000"}
    routes = {("GET", V + "/me"): (200, {"id": "55", "name": "n"}),
              ("GET", V + "/55/posts"): (200, {"data": [post]}),
              ("GET", V + "/55_1/comments"): (200, {"data": [{"id": "9", "message": BODY}]}),
              ("GET", V + "/55/insights"): (200, {"data": []}),
              ("GET", V + "/55/subscribed_apps"): (200, {"data": []})}
    with fake_graph(routes) as (base, seen):
        monkeypatch.setenv(meta_graph.BASE_URL_ENV, base)
        result = pw.keepalive()
    assert {row["permission"] for row in result["results"]} == set(facebook.FacebookAdapter.KEEPALIVE_READS)
    assert {row["result"] for row in result["results"]} == {"ok"}
    assert {call["method"] for call in seen} == {"GET"}
    comments = next(call for call in seen if call["path"].endswith("/comments"))
    assert comments["query"]["limit"] == ["1"]
    assert BODY not in open(runs.path_for(accounts.state_dir_for(name), "2026-11"), encoding="utf-8").read()


def test_Instagramの読み(world, monkeypatch):
    facebook_world(world, monkeypatch, "a-ig", "instagram", ig_user_id="77")
    world.cfgs["a-ig"]["user_id"] = "77"
    path = world.cfgs["a-ig"]["token"]
    token = json.load(open(path, encoding="utf-8"))
    token["user_id"] = "77"
    open(path, "w", encoding="utf-8").write(json.dumps(token))
    post = {"id": "1", "caption": BODY, "timestamp": "2026-11-01T00:00:00+0000"}
    routes = {("GET", V + "/77"): (200, {"id": "77", "username": "u"}),
              ("GET", V + "/me"): (200, {"id": "77", "username": "u"}),
              ("GET", V + "/77/media"): (200, {"data": [post]}),
              ("GET", V + "/1/comments"): (200, {"data": []}),
              ("GET", V + "/77/insights"): (200, {"data": []})}
    with fake_graph(routes) as (base, seen):
        monkeypatch.setenv(meta_graph.BASE_URL_ENV, base)
        result = pw.keepalive()
    assert {row["permission"]: row["result"] for row in result["results"]} == {
        name: "ok" for name in instagram.InstagramAdapter.KEEPALIVE_READS}
    assert {call["method"] for call in seen} == {"GET"}


def test_広告はads_readだけ打ち_ads_managementは打たない(world, monkeypatch):
    name = world.add_ads("a-ads", obtained=26)
    monkeypatch.setattr(meta_graph, "load_app_secret", lambda: None)
    with fake_graph({("GET", V + "/act_123"): (200, {"id": "act_123", "name": "n", "currency": "JPY",
                                                     "timezone_name": "Asia/Tokyo", "account_status": 1})}) as (base, seen):
        monkeypatch.setenv(meta_graph.BASE_URL_ENV, base)
        result = pw.keepalive()
    assert [(row["permission"], row["result"]) for row in result["results"]] == [("ads_read", "ok")]
    assert {call["method"] for call in seen} == {"GET"}
    view = pw.build()
    assert row_of(view, "ads_read")["days_idle"] == 0
    assert row_of(view, "ads_management")["days_idle"] == 26
    assert view["ads_tier"]["calls_15d"] == 1


def test_timerは1日1回でkeepaliveを呼ぶ(capsys):
    assert cli.main(["systemd", "--meta-keepalive"]) == 0
    timer = capsys.readouterr().out
    assert timer == systemd_gen.render_meta_keepalive_timer()
    assert "Unit=thth-meta-keepalive.service" in timer and "OnCalendar=*-*-* 04:47:00" in timer
    assert cli.main(["systemd", "--meta-keepalive", "--service"]) == 0
    assert "ExecStart=/srv/thth/app/bin/thth admin meta keepalive\n" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["systemd", "a", "--meta-keepalive"], ["systemd", "--maintain", "--meta-keepalive"]])
def test_timerは他の口と併用しない(argv, capsys):
    assert cli.main(argv) == 2


# ---------------------------------------------------------------- (b) 広告の数

def call_file(tmp_path, name="a"):
    return str(tmp_path / name / "meta_usage.json")


def test_呼び出しを数え_本文も宛先も残さない(tmp_path, monkeypatch, thth_root, frozen_now_jst):
    frozen_now_jst(NOW)
    usage_path = call_file(tmp_path)
    routes = {("GET", V + "/act_1"): (200, {"name": BODY}),
              ("GET", V + "/act_2"): (400, {"error": {"message": BODY, "code": 100}})}
    with fake_graph(routes) as (base, _seen):
        client = meta_graph.Client(base, TOKEN, usage_path=usage_path)
        client.request("GET", "/act_1")
        with pytest.raises(Exception):
            client.request("GET", "/act_2")
    text = open(calls.path_beside(usage_path), encoding="utf-8").read()
    assert BODY not in text and TOKEN not in text and "act_" not in text
    value = calls.summary([calls.path_beside(usage_path)], now=NOW)
    assert (value["calls_15d"], value["errors_15d"], value["last_n"], value["error_rate"]) == (2, 1, 2, 0.5)
    assert value["counting_since"] == NOW.date().isoformat()


def test_ページの口座の呼び出しは数えない(tmp_path, thth_root, frozen_now_jst):
    frozen_now_jst(NOW)
    with fake_graph({("GET", V + "/me"): (200, {"id": "1"})}) as (base, _seen):
        meta_graph.Client(base, TOKEN, account="a-fb").request("GET", "/me")
    assert not os.path.exists(os.path.join(accounts.state_dir_for("a-fb"), calls.FILE))


def test_待ちで網に出なかった回は数えない(tmp_path, thth_root, frozen_now_jst):
    frozen_now_jst(NOW)
    usage_path = call_file(tmp_path)
    os.makedirs(os.path.dirname(usage_path))
    meta_usage.note_rate_limited(None, {}, 80001, path=usage_path)
    with fake_graph({}) as (base, seen):
        with pytest.raises(Exception):
            meta_graph.Client(base, TOKEN, usage_path=usage_path).request("GET", "/act_1")
    assert seen == [] and not os.path.exists(calls.path_beside(usage_path))


def write_calls(path, *, total=500, errors=0, day=NOW):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    stamp = int(day.timestamp())
    last = [[stamp + i, 1 if i < errors else 0] for i in range(min(total, calls.RATE_WINDOW))]
    with open(path, "w", encoding="utf-8") as stream:
        json.dump({"version": 1, "since": "2026-11-01", "days": {day.date().isoformat(): [total, errors]},
                   "last": last}, stream)


@pytest.mark.parametrize("total, errors, meets", [(500, 74, True), (500, 75, False), (499, 0, False)])
def test_Fullの条件は500回とエラー率15パーセント未満(tmp_path, total, errors, meets):
    path = str(tmp_path / "calls.json")
    write_calls(path, total=total, errors=errors)
    assert calls.summary([path], now=NOW)["meets"] is meets


def test_15日の窓の外は数えない_口座を足して数える(tmp_path):
    first, second = str(tmp_path / "1.json"), str(tmp_path / "2.json")
    write_calls(first, total=300, day=NOW)
    write_calls(second, total=200, day=NOW - datetime.timedelta(days=14))
    assert calls.summary([first, second], now=NOW)["calls_15d"] == 500
    write_calls(second, total=200, day=NOW - datetime.timedelta(days=15))
    assert calls.summary([first, second], now=NOW)["calls_15d"] == 300


def test_控えの大きさに上限がある(tmp_path, frozen_now_jst):
    usage_path = call_file(tmp_path)
    for index in range(calls.RATE_WINDOW + 5):
        calls.note(usage_path, True, now=NOW + datetime.timedelta(seconds=index))
    calls.note(usage_path, True, now=NOW + datetime.timedelta(days=30))
    row = calls.read(calls.path_beside(usage_path))
    assert len(row["last"]) == calls.RATE_WINDOW and len(row["days"]) == 1


def test_条件に届いたら押す知らせ_Fullで割ったら知らせ(world):
    name = world.add_ads("a-ads")
    world.add("a-threads")
    fresh_all(world, "a-threads")
    write_calls(os.path.join(ads_ledger.state_dir_for(name), calls.FILE))
    world.run(name, "permission_keepalive", 1, state=ads_ledger.state_dir_for(name), permission="ads_read")
    world.run(name, "permission_keepalive", 1, state=ads_ledger.state_dir_for(name), permission="ads_management")
    view = pw.build()
    assert "ads_tier_ready" in [row["code"] for row in view["notices"]]
    assert pw.stuck_rows(view)["a-threads"]["code"] == "ads_tier_ready"
    pw.set_ledger("ads_tier", "full", by="t")
    assert "ads_tier_ready" not in [row["code"] for row in pw.build()["notices"]]
    write_calls(os.path.join(ads_ledger.state_dir_for(name), calls.FILE), total=100)
    assert "ads_tier_at_risk" in [row["code"] for row in pw.build()["notices"]]


# ---------------------------------------------------------------- (c) 期限の台帳

def expires(days):
    return int((NOW + datetime.timedelta(days=days)).timestamp())


@pytest.mark.parametrize("days, code, level", [(30, None, None), (13, "data_access_expiring", "yellow"),
                                               (-1, "data_access_expired", "red")])
def test_データの期限はstuckに出る(world, days, code, level):
    world.add("a-ig", "instagram", data_access_expires_at=expires(days), scopes=[])
    view = pw.build()
    assert view["data_access"][0]["account"] == "a-ig"
    row = pw.stuck_rows(view).get("a-ig")
    assert (row or {}).get("code") == code and (row or {}).get("level") == level


def test_広告の口座のデータの期限は代表の口座に載り_ads_authを案内する(world):
    world.add("a-threads")
    fresh_all(world, "a-threads")
    world.add_ads("a-ads", data_access_expires_at=expires(5), scopes=[])
    row = pw.stuck_rows(pw.build())["a-threads"]
    assert row["code"] == "data_access_expiring" and "a-ads" in row["what"]
    assert "thth ads auth a-ads" in row["fix"]


def test_招待の口座の期限は持ち主に知らせない(world):
    world.add("guest-ig", "instagram", invite=True, data_access_expires_at=expires(5), scopes=[])
    assert pw.build()["notices"] == []


@pytest.mark.parametrize("due, state, code", [("2027-10-09", "ok", None), ("2026-12-21", "ok", None),
                                              ("2026-12-20", "due_soon", "duc_due_soon"),
                                              ("2026-11-19", "overdue", "duc_overdue")])
def test_DUCの期日(world, due, state, code):
    world.add("a-threads")
    fresh_all(world, "a-threads")
    assert cli.main(["admin", "meta", "set", "duc_due", due, "--by", "t"]) == 0
    view = pw.build()
    assert view["duc"]["state"] == state
    assert [row["code"] for row in view["notices"]] == ([code] if code else [])


def test_台帳は誰がいつ書いたかを残す(world):
    pw.set_ledger("ads_tier", "full", by="masaru")
    row = pw.read_ledger()
    assert row["ads_tier"] == "full" and row["updated"]["ads_tier"]["by"] == "masaru"


@pytest.mark.parametrize("key, value, code", [("nope", "x", "meta_ledger_key_unknown"),
                                              ("duc_due", "来年", "meta_ledger_value_invalid"),
                                              ("ads_tier", "gold", "meta_ledger_value_invalid"),
                                              ("advanced", "no_such_permission", "meta_ledger_value_invalid")])
def test_台帳の断りは3つを出す(world, capsys, key, value, code):
    assert cli.main(["admin", "meta", "set", key, value, "--by", "t"]) == 2
    err = capsys.readouterr().err
    assert err.startswith(code + ": ") and "通すには: " in err and "もっと良く: " in err
    assert not os.path.exists(pw.ledger_path())


def test_読めない台帳は上書きせず言えないと言う(world, capsys):
    os.makedirs(os.path.dirname(pw.ledger_path()))
    open(pw.ledger_path(), "w", encoding="utf-8").write("{壊れた")
    assert cli.main(["admin", "meta", "set", "ads_tier", "full", "--by", "t"]) == 2
    assert "meta_ledger_unreadable" in capsys.readouterr().err
    assert pw.build()["cannot_say"] == "meta_ledger_unreadable"
    assert open(pw.ledger_path(), encoding="utf-8").read() == "{壊れた"


def test_watchとledgerの口は読むだけで何も書かない(world, capsys):
    name = world.add("a-threads", obtained=22, data_access_expires_at=expires(30))
    before = sorted(os.listdir(accounts.state_dir_for(name)))
    assert cli.main(["admin", "meta", "watch"]) == 0
    text = capsys.readouterr().out
    assert "threads_basic" in text and "知らせ permission_unused" in text and "通すには: " in text
    assert TOKEN not in text
    assert cli.main(["admin", "meta", "watch", "--json"]) == 0
    assert TOKEN not in capsys.readouterr().out
    assert cli.main(["admin", "meta", "ledger"]) == 0
    assert "データに触れる期限 a-threads" in capsys.readouterr().out
    assert cli.main(["admin", "meta", "ledger", "--json"]) == 0
    assert "ads_tier" in json.loads(capsys.readouterr().out)
    assert sorted(os.listdir(accounts.state_dir_for(name))) == before
    assert not os.path.exists(pw.ledger_path())
