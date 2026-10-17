"""4.0 S5 の試験（3）: burst の数え方（T-S5-6）・告知（T-S5-7）・取り消しと孤児と結果不明の候補（T-S5-8）・
遠くの道と MCP（T-S5-9a）・127.0.0.1 の本物の HTTP の経路（衝突の check と show）。

HTTP は偽物（`FakeBlog`）か 127.0.0.1 のサーバ。はてなには出ない。秘密の値は偽物だけ。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import secrets
import sys

import pytest

from tests.hatena_s1_flow_helpers import IMAGE_BYTES, call
from tests.hatena_s5_helpers import (ACCOUNT, NOW, FakeBlog, approve, approved, body_sha, fm_of, lane,  # noqa: F401
                                     published, states, throw, use_blog, world)
from thth import (accounts, core, deliveries, deliveries_cli, guard, hatena_conflict, hatena_publish, inflight,
                  jst, principals, sent)
from thth import server_writes as writes
from thth.report_service import ReportServiceError


def _cfg(name=ACCOUNT):
    return accounts.load_account(name)


def _record(delivery):
    return deliveries.read(ACCOUNT, delivery)


# ---------------------------------------------------------------- T-S5-6 burst

def _entry_row(state, post_id, *, kind, minutes_ago, delivery=None):
    sent.write(state, post_id=post_id, text="t", body_hash="h",
               sent_at=jst.iso(NOW - datetime.timedelta(minutes=minutes_ago)), kind=kind,
               blog_id="a.hatenablog.com", delivery=delivery)


def _loose_daily(lane):
    """日の上限は burst と別に数える。burst だけを見るために日の上限を広げる（口座と主体）。"""
    data = lane.world.account(ACCOUNT)
    data.update({"daily_max_posts": 20, "daily_max_retracts": 20})
    lane.world.write_account(data)
    lane.world.edit_principal("hatena-main", daily_max_posts=20, daily_max_retracts=20)


DL1, DL2, DL3, DL4 = "dl_00000000000a", "dl_00000000000b", "dl_00000000000c", "dl_00000000000d"
TAG = "tag:blog.hatena.ne.jp,2013:blog-lane-owner-1-"


def test_T_S5_6a_同じ配信の公開_更新_削除は口座も主体も止めない(lane):
    """柵: 鍵（delivery）を外すと赤（3 件目で止まる）。"""
    _loose_daily(lane)
    state = lane.state
    _entry_row(state, TAG + "1", kind="entry_create", minutes_ago=5, delivery=DL1)
    _entry_row(state, TAG + "1#u5", kind="entry_update", minutes_ago=3, delivery=DL1)
    counts = guard.summary(ACCOUNT, _cfg(), now=NOW)
    assert counts["recent"] == 1 and counts["posts_today"] == 2
    guard.check(ACCOUNT, _cfg(), "entry_delete", now=NOW, delivery=DL1)
    sent.mark_retracted(state, TAG + "1", retracted_at=jst.iso(NOW - datetime.timedelta(minutes=1)),
                        retracted_by="thth", retract_reason="hatena_entry_delete")
    assert guard.summary(ACCOUNT, _cfg(), now=NOW)["recent"] == 1
    assert guard.principal_summary("hatena-main", principals.load_principal("hatena-main"), now=NOW)["recent"] == 1
    assert guard.stopped(ACCOUNT) is None and principals.stopped("hatena-main") is None


def test_T_S5_6a_別の配信3つのあとの4つ目で止まる_3つのどれかを直すのは止めない(lane):
    _loose_daily(lane)
    state = lane.state
    for i, dl in enumerate((DL1, DL2, DL3)):
        _entry_row(state, TAG + str(i), kind="entry_create", minutes_ago=6 - i, delivery=dl)
    assert guard.summary(ACCOUNT, _cfg(), now=NOW)["recent"] == 3
    guard.check(ACCOUNT, _cfg(), "entry_update", now=NOW, delivery=DL2)      # 窓の中の配信を直す
    with pytest.raises(guard.GuardRefused) as e:
        guard.check(ACCOUNT, _cfg(), "entry_create", now=NOW, delivery=DL4)
    assert str(e.value) == "account_stopped" and e.value.reason == "burst"


def test_T_S5_6a_deliveryの無い古いはてなの行は別々に数える(lane):
    _loose_daily(lane)
    state = lane.state
    _entry_row(state, TAG + "7", kind="entry_create", minutes_ago=5)
    sent.mark_retracted(state, TAG + "7", retracted_at=jst.iso(NOW - datetime.timedelta(minutes=4)),
                        retracted_by="thth", retract_reason="hatena_entry_delete")
    _entry_row(state, TAG + "7#u3", kind="entry_update", minutes_ago=3)
    assert guard.summary(ACCOUNT, _cfg(), now=NOW)["recent"] == 3
    with pytest.raises(guard.GuardRefused):
        guard.check(ACCOUNT, _cfg(), "entry_create", now=NOW, delivery=DL4)


def test_T_S5_6a_timerは配信idを安全装置に渡す(lane, monkeypatch):
    """柵: `throw_chosen` が delivery を渡さないと赤。"""
    seen = []
    real = guard.check

    def spy(*args, **kwargs):
        seen.append(kwargs.get("delivery"))
        return real(*args, **kwargs)
    monkeypatch.setattr(guard, "check", spy)
    path, delivery = approved(lane)
    assert throw(FakeBlog()).action == "post"
    assert seen == [delivery]


def test_T_S5_6b_SNSの公開と取り下げは今までどおり別の2件(world, tmp_path):
    from tests.hatena_s1_helpers import account_data
    data = account_data("lane-a-threads", project="lane-a", principal="hatena-main",
                        blog_id="a.hatenablog.com", token=tmp_path / "t.token", handle="who")
    data["media"] = "threads"
    world.write_account(data)
    state = world.state("lane-a-threads")
    sent.write(state, post_id="1", text="t", body_hash="h", sent_at=jst.iso(NOW - datetime.timedelta(minutes=5)))
    sent.mark_retracted(state, "1", retracted_at=jst.iso(NOW - datetime.timedelta(minutes=3)),
                        retracted_by="t", retract_reason="r")
    s = guard.summary("lane-a-threads", _cfg("lane-a-threads"), now=NOW)
    assert (s["posts_today"], s["retracts_today"], s["recent"]) == (1, 1, 2)


def test_T_S5_6c_日の数えは操作1回を1件のまま(lane):
    state = lane.state
    _entry_row(state, TAG + "1", kind="entry_create", minutes_ago=50, delivery=DL1)
    _entry_row(state, TAG + "1#u3", kind="entry_update", minutes_ago=40, delivery=DL1)
    _entry_row(state, TAG + "1#u5", kind="entry_update", minutes_ago=30, delivery=DL1)
    counts = guard.summary(ACCOUNT, _cfg(), now=NOW)
    assert counts["posts_today"] == 3 and counts["recent"] == 0
    with pytest.raises(guard.GuardRefused) as e:
        guard.check(ACCOUNT, _cfg(), "entry_update", now=NOW, delivery=DL1)
    assert str(e.value) == "daily_limit"


# ---------------------------------------------------------------- T-S5-7a 告知は書き換えない

def _sns(lane, name="lane-a-threads"):
    from tests.hatena_s1_helpers import account_data
    data = account_data(name, project="lane-a", principal="hatena-main", blog_id="a.hatenablog.com",
                        token=lane.world.tmp / "t.token", handle="who")
    data["media"] = "threads"
    data.pop("principal", None)
    lane.world.write_account(data)
    return name


def _sent_bytes(state):
    out = {}
    d = os.path.join(state, "sent")
    for name in sorted(os.listdir(d)):
        with open(os.path.join(d, name), "rb") as f:
            out[name] = f.read()
    return out


def test_T_S5_7a_更新のrunのログに告知の1行_SNSのsentは1バイトも変わらない(lane):
    """柵: 告知を書き換えると赤。"""
    _path, delivery, blog = published(lane)
    sns = _sns(lane)
    sent.write(lane.world.state(sns), post_id="1789", text="告知の本文", body_hash="h", sent_at=jst.iso(NOW),
               announces=delivery, article="lane-a/arabica")
    before = _sent_bytes(lane.world.state(sns))
    lane.write("articles/a.md", "# アラビカ\n\n直した。\n")
    approved(lane, "u.md", op="update", delivery=delivery)
    logs = []
    assert throw(blog, log=logs.append).action == "post"
    lines = [str(x) for x in logs if "告知" in str(x)]
    assert len(lines) == 1 and "書き換えません" in lines[0] and "1789" in lines[0] and "reply_to" in lines[0]
    assert _sent_bytes(lane.world.state(sns)) == before


def test_T_S5_7a_消したあとのdeliveries_articleの告知の行に記事は消えています(lane):
    _path, delivery, blog = published(lane)
    sns = _sns(lane)
    sent.write(lane.world.state(sns), post_id="1789", text="告知", body_hash="h", sent_at=jst.iso(NOW),
               announces=delivery, article="lane-a/arabica")
    approved(lane, "d.md", op="delete", delivery=delivery)
    assert throw(blog).action == "post"
    rc, out, err = call(["deliveries", ACCOUNT, "--article", "lane-a/arabica"])
    assert rc == 0 and "→ 記事は消えています" in out


# ---------------------------------------------------------------- T-S5-8a 取り消し

def test_T_S5_8a_revokeした承認済みの更新はPUTを打たず台帳はpublishedのまま(lane):
    _path, delivery, blog = published(lane)
    lane.write("articles/a.md", "# アラビカ\n\n直した。\n")
    upath, _ = approved(lane, "u.md", op="update", delivery=delivery)
    rc, out, err = call(["revoke", upath, "--by", "tester", "--reason", "やめた"])
    assert rc == 0, (out, err)
    assert fm_of(upath)["status"] == "draft"
    blog.calls.clear()
    throw(blog)
    assert blog.calls == [] and _record(delivery)["state"] == "published"


def test_T_S5_8a_activityの取り消しも同じ(lane):
    _path, delivery, blog = published(lane)
    lane.write("articles/a.md", "# アラビカ\n\n直した。\n")
    upath, _ = approved(lane, "u.md", op="update", delivery=delivery)
    draft_id = hashlib.sha256(os.path.basename(upath).encode()).hexdigest()
    assert writes.cancel_schedule(ACCOUNT, draft_id, by="owner") == "cancelled"
    blog.calls.clear()
    throw(blog)
    assert blog.calls == [] and _record(delivery)["state"] == "published"


# ---------------------------------------------------------------- T-S5-8b 孤児の wanted

def test_T_S5_8b_原稿の無いwantedは1行に原稿なし_createにdeliveryを書けば同じ台帳(lane):
    path = lane.queue()
    approve(path)
    delivery = fm_of(path)["delivery"]
    os.remove(path)
    lane.commit("docs/sns/queue/a.md")
    rc, out, err = call(["deliveries", ACCOUNT])
    assert "原稿なし（create で delivery: を書けば使える）" in out
    again = lane.queue("b.md", delivery=delivery)
    approve(again)
    record = _record(delivery)
    assert [r["state"] for r in record["history"]] == ["wanted", "wanted"]
    rc, out, err = call(["deliveries", ACCOUNT])
    assert "原稿なし" not in out


# ---------------------------------------------------------------- T-S5-8c 結果不明の候補

def _unknown_create(lane, monkeypatch):
    path, delivery = approved(lane)
    blog = FakeBlog(result=hatena_publish.EntryResult(ok=False, ambiguous=True, error="hatena_transport_failed"))
    # 送った（はてなには届いた）が、答えが分からなかった
    original = blog.create_entry

    def create(**kwargs):
        original(**kwargs)
        return blog.result
    blog.create_entry = create
    assert throw(blog).action == "inflight"
    blog.result = None
    use_blog(monkeypatch, blog)
    return path, delivery, blog


def test_T_S5_8c_一覧の題と時刻が合えば候補と命令_自動では解かない(lane, monkeypatch):
    """柵: 自動で解くと赤（台帳も inflight も変わらない）。"""
    path, delivery, blog = _unknown_create(lane, monkeypatch)
    record = inflight.read(lane.state)
    blog.published = record["started"]
    before_ledger = open(deliveries.path_for(ACCOUNT, delivery), "rb").read()
    rc, out, err = call(["inflight", ACCOUNT, "show", "--json"])
    payload = json.loads(out)
    assert payload["candidates_checked"] is True
    assert payload["candidates"] == [{"entry_id": blog.entry_id, "title": "アラビカの値動き",
                                      "published": record["started"], "edited": record["started"],
                                      "body_same": True}]
    rc, out, err = call(["inflight", ACCOUNT])
    assert f"thth inflight {ACCOUNT} resolve --published {blog.entry_id} --by <名前>" in out
    assert "本文も同じ" in out and "3.3.1 より前" not in out and f"はてなの配信 {delivery}" in out
    assert open(deliveries.path_for(ACCOUNT, delivery), "rb").read() == before_ledger
    assert inflight.read(lane.state) == record
    assert states(delivery)[-1] == "unknown"


def test_T_S5_8c_合わなければありません_GETの失敗は今の表示(lane, monkeypatch):
    path, delivery, blog = _unknown_create(lane, monkeypatch)
    blog.published = "2026-01-01T10:00:00+09:00"
    rc, out, err = call(["inflight", ACCOUNT])
    assert "一覧の 7 件に合う記事はありません" in out
    blog.fail_read = True
    rc, out, err = call(["inflight", ACCOUNT])
    assert rc == 0 and "照合できませんでした（hatena_http_503）" in out and "次の一手" in out


def test_T_S5_8c_updateのinflightは一覧でなく記事を読む(lane, monkeypatch):
    _path, delivery, blog = published(lane)
    lane.write("articles/a.md", "# アラビカ\n\n直した。\n")
    approved(lane, "u.md", op="update", delivery=delivery)
    original = blog.update_entry

    def update(**kwargs):
        original(**kwargs)
        return hatena_publish.EntryResult(ok=False, ambiguous=True, error="hatena_transport_failed")
    blog.update_entry = update
    assert throw(blog).action == "inflight"
    use_blog(monkeypatch, blog)
    blog.calls.clear()
    found = hatena_publish.candidates(ACCOUNT, _cfg(), inflight.read(lane.state))
    assert [c[0] for c in blog.calls] == ["read"]
    assert found["op"] == "update" and found["candidates"][0]["body_same"] is True


# ---------------------------------------------------------------- T-S5-9a 遠くの道と MCP

@pytest.fixture
def key(lane, tmp_path, monkeypatch):
    from thth import report_http, report_isolation
    monkeypatch.setattr(report_isolation, "validate_environment", lambda *a, **k: None)

    def make(accounts_):
        bearer = secrets.token_urlsafe(32)
        config = tmp_path / f"credentials-{secrets.token_hex(2)}.json"
        value = dict(schema_version=1, root=str(lane.world.root), credentials=[dict(
            sha256=hashlib.sha256(bearer.encode()).hexdigest(),
            expires_at=(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat(),
            revoked=False, accounts=accounts_, writes=True, actor="person")])
        config.write_text(json.dumps(value))
        config.chmod(0o600)
        return report_http.load_credentials(config)[1][0][3]
    return make


def _serve(context, **body):
    return writes.serve(context, dict(account=ACCOUNT, **body), via="mcp")


def _refused(context, account=ACCOUNT, **body):
    with pytest.raises(ReportServiceError) as caught:
        writes.serve(context, dict(account=account, **body), via="mcp")
    return str(caught.value)


def test_T_S5_9a_article_diffは読む口_範囲外はscope_unavailable_大きければ後ろから落とす(lane, key, monkeypatch):
    from thth import server_reads
    _path, delivery, _blog = published(lane)
    lane.write("articles/a.md", "# アラビカ\n\n" + "\n".join(f"直した {i} 行目" for i in range(400)) + "\n")
    context = key({ACCOUNT: "lane-a"})
    result = _serve(context, operation="article_diff", draft=True)
    assert result["schema"] == "thth.article_diff/1" and result["deliveries"][0]["delivery"] == delivery
    assert "draft_text" in result["deliveries"][0]
    assert _refused(key({"lane-b-hatenablog": "lane-b"}), operation="article_diff") == "scope_unavailable"
    assert _refused(context, operation="article_diff", draft="yes") == "invalid_request"
    monkeypatch.setattr(server_reads, "RESULT_BUDGET", 4000)
    small = _serve(context, operation="article_diff")
    assert small["trimmed"] > 0 and len(small["deliveries"][0]["diff_lines"]) < len(result["deliveries"][0]["diff_lines"])
    assert any("後ろから落としました" in line for line in small["cannot_say"])


def test_T_S5_9a_conflict_statusとconflict_resolveは2段(lane, key, monkeypatch):
    _path, delivery, blog = published(lane)
    use_blog(monkeypatch, blog)
    context = key({ACCOUNT: "lane-a"})
    blog.edit(body="はてなで直した")
    checked = _serve(context, operation="conflict_status", check=True)
    assert checked["results"][0]["result"] == "readback_changed"
    listing = _serve(context, operation="conflict_status")
    assert [r["delivery"] for r in listing["conflicts"]] == [delivery]
    shown = _serve(context, operation="conflict_status", delivery=delivery)
    assert shown["remote"]["readback_sha"] == body_sha("はてなで直した")
    first = _serve(context, operation="conflict_resolve", delivery=delivery, resolution="adopt")
    assert first["stage"] == 1 and first["resolved"] is False and _record(delivery)["state"] == "conflict"
    assert _refused(context, operation="conflict_resolve", delivery=delivery, resolution="adopt",
                    confirm="000000000000") == "confirm_mismatch"
    assert _refused(context, operation="conflict_resolve", delivery=delivery, resolution="nope") == "resolution_required"
    done = _serve(context, operation="conflict_resolve", delivery=delivery, resolution="adopt", confirm=first["digest"])
    assert done["resolved"] is True and _record(delivery)["state"] == "published"
    assert _record(delivery)["history"][-1]["resolved_by"] == "person"     # 資格の actor


def _mcp_server():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
    import server
    return server


def test_T_S5_9a_MCPに3つの道具があり_thth_inflightは無い():
    server = _mcp_server()
    local = {t["name"] for t in server.TOOLS}
    remote = {t["name"] for t in server.SERVER_TOOLS}
    for names in (local, remote):
        assert {"thth_article_diff", "thth_conflict_status", "thth_conflict_resolve"} <= names
        assert not [n for n in names if "inflight" in n]
    resolve = next(t for t in server.SERVER_TOOLS if t["name"] == "thth_conflict_resolve")
    assert "by" not in resolve["inputSchema"]["properties"]             # 解いた人は資格の actor


def test_T_S5_9a_inflight_statusは候補を返す(lane, key, monkeypatch):
    path, delivery, blog = _unknown_create(lane, monkeypatch)
    blog.published = inflight.read(lane.state)["started"]
    context = key({ACCOUNT: "lane-a"})
    status = _serve(context, operation="inflight_status")
    assert status["candidates_checked"] is True and status["candidates"][0]["entry_id"] == blog.entry_id
    assert _record(delivery)["state"] == "unknown"


# ---------------------------------------------------------------- 127.0.0.1 の本物の HTTP の経路

def test_本物のurllibの経路で衝突のcheckとshow_題の手直しをconflictに(world, monkeypatch):  # noqa: F811
    """`httpsafe.urlopen` は差し替えない。はてなの代わりに 127.0.0.1 のサーバ。署名は OAuth。"""
    from tests.conftest import restore_real_hatena_adapters
    from tests.hatena_s2_http import BLOG, FAKE_TOKEN, HATENA_ID, entry_id, entry_xml, put_secrets
    from tests.test_v400_s2_audit_fixes import _Server
    from thth.adapters import hatenablog
    restore_real_hatena_adapters(monkeypatch)
    monkeypatch.setenv("THTH_TEST_ALLOW_HTTP", "1")
    put_secrets(world)
    eid = entry_id("99")
    with _Server() as srv:
        monkeypatch.setattr(hatenablog, "BASE", f"http://127.0.0.1:{srv.port}")
        member = f"/{HATENA_ID}/{BLOG}/atom/entry/99"
        delivery = deliveries.create(
            ACCOUNT, principal="hatena-main", blog_id=BLOG, article="lane-a/arabica", at=jst.iso(NOW),
            state="published",
            fields={"entry_id": eid, "readback_sha": body_sha("本文"), "title": "題", "readback_title": "題",
                    "categories": [], "readback_categories": [], "edit_uri": f"http://127.0.0.1:{srv.port}{member}",
                    "published_url": f"https://{BLOG}/entry/2026/09/09/100000"})["delivery"]
        atom = {"Content-Type": "application/atom+xml"}
        srv.answers[("GET", member)] = (200, atom, entry_xml(num="99", content="本文", title="題"))
        assert hatena_conflict.check(ACCOUNT, _cfg(), delivery)["results"][0]["result"] == "same"
        srv.answers[("GET", member)] = (200, atom, entry_xml(num="99", content="本文", title="はてなで直した題"))
        result = hatena_conflict.check(ACCOUNT, _cfg(), delivery)
        assert result["results"][0] == {"delivery": delivery, "result": "readback_changed", "detail": ["title"]}
        shown = hatena_conflict.show(ACCOUNT, _cfg(), delivery)
        assert shown["remote"]["title"] == "はてなで直した題" and shown["remote"]["title_differs"] is True
        assert [(r["method"], r["path"]) for r in srv.requests] == [("GET", member)] * 3
        for row in srv.requests:
            assert row["authorization"].startswith("OAuth ")
            assert FAKE_TOKEN["oauth_token_secret"] not in row["authorization"]
    assert deliveries.read(ACCOUNT, delivery)["state"] == "conflict"
    assert eid.endswith("-99")
