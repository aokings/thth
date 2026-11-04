"""段 E の柵: 共有アプリの柵（設計「監査と審査の機械化と前向きな断り」§2 の (2)・(3)・§7 の段 E）。

決まった数の既定（masaru 2026-11-04・変えない）:
1. 全テナントの合計が媒体のアプリ全体の上限の 8 割に達したら、管理者の observe に 1 行出すだけ（止めない）。
2. 招待で入った口座の最初の 14 日は、1 日の公開の上限を台帳の値の半分（切り上げ・1 未満にしない）で読む。
   止めはしない。断りの文に「慣らしの期間・あと何日」を出す。台帳は書き換えない。
"""
from __future__ import annotations

import datetime
import json

import pytest

from thth import core, guard, jst, refusal, refusal_catalog, shared_app
from thth.report_service import ReportServiceError
from tests.test_v212_server_writes import env, remote  # noqa: F401
from tests.test_v3120_guard import call, ledger, publisher  # noqa: F401
from tests.test_v340_plaza_morning import quiet  # noqa: F401
from tests.test_v340_plaza_store import owners  # noqa: F401


@pytest.fixture(autouse=True)
def after_ramp_since(frozen_now_jst):
    """conftest は今を 2026-09-09 に固定する（`RAMP_SINCE` より前で慣らしが掛からない）。入れた日のあとに置く。"""
    frozen_now_jst(datetime.datetime(2026, 11, 10, 10, 0, tzinfo=jst.JST))
    return frozen_now_jst


def _invited(env, *, days_ago=0, via="invite", daily=4):
    created = jst.now_jst() - datetime.timedelta(days=days_ago)
    ledger(env, daily_max_posts=daily,
           provenance={"created_at": jst.iso(created), "created_by": "op", "created_via": via, "created_host": "h"})


def _refused(env, **request):
    with pytest.raises(ReportServiceError) as caught:
        call(env, **request)
    return caught.value


# ---------------------------------------------------------------- 慣らしの数

@pytest.mark.parametrize("ledger_count, ramp", [(8, 4), (7, 4), (3, 2), (2, 1), (1, 1), (0, 0)])
def test_半分は切り上げで1未満にせず台帳より緩めない(ledger_count, ramp):
    assert shared_app.ramp_count(ledger_count) == ramp


def test_起点は招待の口座の台帳の作成の時刻だけ():
    day = datetime.date(2026, 11, 10)
    invited = {"provenance": {"created_via": "invite", "created_at": "2026-11-10T09:00:00+09:00"}}
    assert shared_app.ramp_for(invited, day) == {"started": "2026-11-10", "until": "2026-11-24", "days_left": 14}
    assert shared_app.ramp_for(invited, day + datetime.timedelta(days=13))["days_left"] == 1
    assert shared_app.ramp_for(invited, day + datetime.timedelta(days=14)) is None
    # 起点が取れない・運営者が作った口座・入れる前に入った口座には掛けない（既存の利用者を急に絞らない）。
    assert shared_app.ramp_for({}, day) is None
    assert shared_app.ramp_for({"provenance": {"created_via": "invite"}}, day) is None
    assert shared_app.ramp_for({"provenance": {"created_via": "cli", "created_at": "2026-11-10T09:00:00+09:00"}},
                               day) is None
    old = {"provenance": {"created_via": "invite", "created_at": "2026-11-03T23:59:00+09:00"}}
    assert shared_app.ramp_for(old, datetime.date(2026, 11, 4)) is None


# ---------------------------------------------------------------- 安全装置で

def test_慣らしの間は半分で断り_文にあと何日を出す(env, publisher):
    _invited(env, days_ago=3, daily=4)
    call(env, operation='send_request', body='一本目')
    call(env, operation='send_request', body='二本目')
    exc = _refused(env, operation='send_request', body='三本目')
    assert str(exc) == 'ramp_daily_limit' and jst.parse(exc.next_at).time() == datetime.time(0, 0)
    assert exc.assessment["ramp"]["days_left"] == 11 and exc.assessment["count"] == 2
    assert exc.assessment["ramp"]["ledger_count"] == 4
    assert guard.stopped('alpha') is None  # 止めはしない
    line = core.guard_refusal_message('alpha', exc)
    assert line.startswith("送信しません（安全装置）: 入ってから 14 日の慣らしの期間なので")
    assert "（慣らしの期間・あと 11 日・今日の上限 2 件・" in line and "台帳の 4 件に戻ります）" in line
    # 台帳は書き換えない。
    data = json.loads((env['root'] / 'accounts/alpha.json').read_text())
    assert data['daily_max_posts'] == 4
    # 遠くの道に返す見立てにも残りの日数（数と日付だけ）。
    assert core.public_assessment(exc.assessment)["ramp"]["days_left"] == 11
    assert len(publisher) == 2


def test_台帳の数でも当たるなら今までのdaily_limit(env, publisher):
    _invited(env, daily=1)
    call(env, operation='send_request', body='一本目')
    exc = _refused(env, operation='send_request', body='二本目')
    assert str(exc) == 'daily_limit' and "ramp" not in exc.assessment


def test_慣らしの外と運営者の口座は今のまま(env, publisher, after_ramp_since):
    after_ramp_since(datetime.datetime(2026, 11, 20, 10, 0, tzinfo=jst.JST))
    _invited(env, days_ago=14, daily=2)  # 11/06 に入った口座の 15 日目
    call(env, operation='send_request', body='一本目')
    call(env, operation='send_request', body='二本目')
    assert str(_refused(env, operation='send_request', body='三本目')) == 'daily_limit'


def test_運営者が作った口座には掛けない(env, publisher):
    _invited(env, via="cli", daily=2)
    call(env, operation='send_request', body='一本目')
    call(env, operation='send_request', body='二本目')
    assert len(publisher) == 2


def test_慣らしが明ける日の予約は台帳の数で数える(env):
    from tests.test_v3120_guard import draft
    _invited(env, days_ago=0, daily=2)
    # 今日から 14 日目（明けた日）の予約は 2 本とも通り、今日の予約は 1 本で断る。
    later = (jst.now_jst() + datetime.timedelta(days=14)).replace(hour=12, minute=0, second=0, microsecond=0)
    for body in ("明けた日の一本目", "明けた日の二本目"):
        made = draft(env, body=body, publish_at=jst.iso(later))
        call(env, operation='schedule_request', draft_id=made['draft_id'])
    soon = (jst.now_jst() + datetime.timedelta(minutes=30)).replace(second=0, microsecond=0)
    first = draft(env, body='今日の一本目', publish_at=jst.iso(soon))
    call(env, operation='schedule_request', draft_id=first['draft_id'])
    second = draft(env, body='今日の二本目', publish_at=jst.iso(soon))
    assert str(_refused(env, operation='schedule_request', draft_id=second['draft_id'])) == 'ramp_daily_limit'


def test_慣らしの断りは目録にあり3つがそろう():
    entry = refusal_catalog.CATALOG[shared_app.RAMP_CODE]
    assert entry["kind"] == "refusal" and set(entry["doors"]) == {"cli", "mcp", "remote"}
    assert entry["why"] and entry["fix"]["text"] and entry["better"] and entry["no_better"] is None
    for door in ("cli", "remote", "mcp"):
        found = refusal.parts(shared_app.RAMP_CODE, account="alpha", door=door,
                              next_at="2030-01-02T00:00:00+09:00")
        assert found["why"] and found["fix"] and found["better"], door
    assert shared_app.RAMP_CODE in guard.REFUSALS


def test_安全装置の断りの符丁はどれも目録に3つがある():
    """安全装置の符丁（`guard.REFUSALS`）を足したら、目録に なぜ・通すには・もっと良く（か no_better）が要る。"""
    missing = []
    for code in sorted(guard.REFUSALS):
        entry = refusal_catalog.CATALOG.get(code)
        if entry is None or not entry["why"] or not entry["fix"]["text"]:
            missing.append(code)
        elif not entry["better"] and not entry["no_better"]:
            missing.append(code)
    assert missing == []


def test_MCPの1行目は安全装置の形で残りの日数を足す(env, monkeypatch):
    from tests.test_refusal_doors import _mcp
    from thth import server_writes
    module = _mcp(env, monkeypatch)
    ramp = {"days_left": 5, "until": "2030-01-06", "count": 4, "ledger_count": 8}

    def refuse(*_args, **_kwargs):
        raise guard.GuardRefused("ramp_daily_limit", next_at="2030-01-02T00:00:00+09:00",
                                 assessment={"rule": "post.daily", "ramp": ramp})
    monkeypatch.setattr(server_writes, "execute", refuse)
    result = module.server_call("thth_draft_put", {"account": "alpha", "body": "本文",
                                                   "publish_at": "2030-01-01T12:00:00+09:00"})
    rows = result["content"][0]["text"].splitlines()
    assert rows[0] == "ramp_daily_limit: next_at=2030-01-02T00:00:00+09:00: days_left=5"
    assert any(row.startswith(refusal.FIX) for row in rows) and any(row.startswith(refusal.BETTER) for row in rows)


def test_遠くの道のCLIは残りの日数を1行目に添える(capsys):
    from thth import remote
    value = {"error": "ramp_daily_limit", "next_at": "2030-01-02T00:00:00+09:00",
             "assessment": {"rule": "post.daily", "ramp": {"days_left": 3, "until": "2030-01-04"}}}
    with pytest.raises(remote.RemoteError) as caught:
        remote._answer(200, value)
    assert caught.value.days_left == 3
    remote.refuse(caught.value, account="alpha")
    first = capsys.readouterr().err.splitlines()[0]
    assert first == "ramp_daily_limit（次は 2030-01-02 00:00 JST から）（慣らしの期間・あと 3 日）"
    # 慣らしでない断りには足さない・形の合わない値は読まない。
    with pytest.raises(remote.RemoteError) as other:
        remote._answer(200, {"error": "daily_limit", "assessment": {"ramp": {"days_left": 3}}})
    assert other.value.days_left is None
    with pytest.raises(remote.RemoteError) as odd:
        remote._answer(200, {"error": "ramp_daily_limit", "assessment": {"ramp": {"days_left": "3"}}})
    assert odd.value.days_left is None


# ---------------------------------------------------------------- 全テナントの合計

def test_アプリ全体の数は媒体の台帳の文にある():
    from thth import media_traits
    for limit in shared_app.APP_LIMITS:
        if limit.get("trait") is None:
            continue
        trait = next(t for t in media_traits.load(limit["media"])["traits"] if t["id"] == limit["trait"])
        assert limit["statement"] in trait["statement"]
        assert f"{limit['count']:,}" in limit["statement"]


def _rows(monkeypatch, *, posts=0, partial=False, budget=None):
    monkeypatch.setattr(shared_app, "_x_posts", lambda now, hours: (posts, partial, 2))
    monkeypatch.setattr(shared_app, "_x_read_budget", lambda: budget)


def test_8割の下では何も出さない(monkeypatch):
    from decimal import Decimal
    _rows(monkeypatch, posts=7999, budget=(Decimal("7.99"), Decimal("10")))
    assert shared_app.alert_lines() == []
    _rows(monkeypatch, posts=0, budget=None)
    assert shared_app.alert_lines() == []


def test_8割に達したら1行で_名前と本文は出さない(monkeypatch):
    from decimal import Decimal
    _rows(monkeypatch, posts=8000, budget=(Decimal("9.5"), Decimal("10")))
    lines = shared_app.alert_lines()
    assert lines == [
        "共有アプリの合計: X の投稿（POST /2/tweets・24 時間） 8,000／10,000（80%・8 割を超えました。止めてはいません）",
        "共有アプリの合計: X の読み取りの月の予算（推定 USD・全口座で 1 本） 9.50 USD／10.00 USD"
        "（95%・8 割を超えました。止めてはいません）"]


def test_上限が分からない媒体は合計を出さずにそう言う(monkeypatch, capsys):
    _rows(monkeypatch, posts=10)
    rows = shared_app.usage()
    unknown = {row["media"] for row in rows if row["cannot_say"] == "app_limit_unknown"}
    assert {"threads", "instagram", "facebook", "bluesky", "mastodon", "hatenablog"} <= unknown
    assert all(row["used"] is None for row in rows if row["cannot_say"] == "app_limit_unknown")
    shared_app.render(rows)
    out = capsys.readouterr().out
    assert "threads: 上限が分からない（" in out and "x X の投稿（POST /2/tweets・24 時間）: 10／10,000（0%）" in out


def test_Xの投稿の合計は全口座の24時間の投稿と返信(monkeypatch):
    from thth import accounts
    now = jst.now_jst()
    rows = {"x1": [("post", "post", now - datetime.timedelta(hours=1), None, ("post", "1")),
                   ("post", "reply", now - datetime.timedelta(hours=2), None, ("post", "2")),
                   ("retract", "retract", now - datetime.timedelta(hours=1), None, ("retract", "1")),
                   ("post", "post", now - datetime.timedelta(hours=25), None, ("post", "3"))],
            "x2": [("post", "post", now - datetime.timedelta(minutes=5), None, ("post", "4"))],
            "th": [("post", "post", now, None, ("post", "5"))]}
    media = {"x1": "x", "x2": "x", "th": "threads"}
    monkeypatch.setattr(accounts, "list_account_names", lambda: ["x1", "x2", "th", "broken"])

    def load(name):
        if name == "broken":
            raise ValueError("unreadable")
        return {"media": media[name]}
    monkeypatch.setattr(accounts, "load_account", load)
    monkeypatch.setattr(guard, "_action_rows", lambda name: rows[name])
    assert shared_app._x_posts(now, 24) == (3, True, 2)


def _today(payload):
    return next(s for s in payload["sections"] if s["section"] == "today")["value"]


def test_observeの行は管理者の1枚だけ_8割の下では鍵も足さない(quiet, monkeypatch, capsys):
    from thth import morning
    now = jst.now_jst()
    monkeypatch.setattr(shared_app, "alert_lines", lambda now=None: [])
    assert "app_usage" not in _today(morning.build("kopicha", now=now, mark=False))
    monkeypatch.setattr(shared_app, "alert_lines", lambda now=None: ["共有アプリの合計: 例"])
    admin = morning.build("kopicha", now=now, mark=False)
    assert _today(admin)["app_usage"] == ["共有アプリの合計: 例"]
    morning.render(admin)
    assert "  共有アプリの合計: 例（thth media app-usage）" in capsys.readouterr().out.splitlines()
    # サーバ型の利用者（招待された側の MCP）の 1 枚には出さない（他のテナントの量を渡さない）。
    tenant = morning.build("kopicha", now=now, mark=False, allowed_names=("kopicha-bsky",))
    assert "app_usage" not in _today(tenant)


def test_手元のsendにも同じに掛かる(env, publisher):
    """運営者の CLI の `thth send`（`core.send_once` の `before_execute` 無し・`guard.check(cli=True)`）。"""
    _invited(env, daily=2)
    results = []
    for body in ("一本目", "二本目"):
        dry = core.send_once('alpha', text=body, log=lambda _: None)
        results.append(core.send_once('alpha', text=body, production_flag=True, confirm=dry.digest,
                                      log=lambda _: None))
    assert [r.action for r in results] == ["post", "guard"]
    assert results[1].error == "ramp_daily_limit"
    assert "（慣らしの期間・あと 14 日・今日の上限 1 件・11/24 から台帳の 2 件に戻ります）" in results[1].message
