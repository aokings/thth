"""4.0 S5 監査（Fable）の指摘の直し: 高-1・中-1・中-2・低-1〜低-6。

HTTP は偽物（`FakeBlog`）。はてなには出ない。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import pytest

from tests.hatena_s1_flow_helpers import call, first_stage_digest
from tests.hatena_s5_helpers import (ACCOUNT, NOW, FakeBlog, approve, approved, body_sha, fm_of, lane,  # noqa: F401
                                     published, states, throw, use_blog, world)
from thth import accounts, article_cli, deliveries, hatena_conflict, hatena_entry, hatena_publish, inflight, jst
from thth import principals, queuefile


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(hatena_conflict, "_SLEEP", lambda _s: None)


def _cfg():
    return accounts.load_account(ACCOUNT)


def _record(delivery):
    return deliveries.read(ACCOUNT, delivery)


def _resolve(delivery, resolution):
    first = hatena_conflict.resolve(ACCOUNT, _cfg(), delivery, resolution, by="tester")
    return hatena_conflict.resolve(ACCOUNT, _cfg(), delivery, resolution, by="tester", confirm=first["digest"])


# ---------------------------------------------------------------- 高-1

def test_高1_題だけの衝突をadoptすると承認済みの更新はapproval_stale_承認し直せば出る(lane, monkeypatch):
    """柵: select_problem の承認のあとの台帳の行の比べを消すと赤（再承認なしに PUT される）。"""
    _path, delivery, blog = published(lane)
    lane.write("articles/a.md", "# アラビカ\n\n直した本文。\n")
    upath, _ = approved(lane, "u.md", op="update", delivery=delivery)
    use_blog(monkeypatch, blog)
    blog.edit(title="はてなで直した題")
    assert throw(blog).action == "conflict"
    _resolve(delivery, "adopt")
    blog.calls.clear()
    result = throw(blog)
    assert {"file": "u.md", "reason": "approval_stale"} in (result.rejections or [])
    assert blog.calls == [] and blog.title == "はてなで直した題"
    # 承認し直しの 1 段目は題の差分（はてなの今の題 → 原稿の題）を見せる。古いことも言う
    _digest, out = first_stage_digest(upath)
    assert "題: はてなで直した題 → アラビカの値動き" in out
    assert hatena_entry.STALE_NOTE in out
    # 同じ秒の承認し直しは原稿が変わらず commit できないので、取り消してから承認し直す（人の手順と同じ）
    assert call(["revoke", upath, "--by", "tester"])[0] == 0
    approve(upath)
    assert throw(blog).action == "post" and blog.title == "アラビカの値動き"


def test_高1_1段目のあとに衝突を見つけたら2段目は承認しない(lane, monkeypatch):
    """柵: assign_delivery の 1 段目の seq との比べを消すと赤。"""
    _path, delivery, blog = published(lane)
    lane.write("articles/a.md", "# アラビカ\n\n直した本文。\n")
    upath = lane.queue("u.md", op="update", delivery=delivery)
    digest, _out = first_stage_digest(upath)
    use_blog(monkeypatch, blog)
    blog.edit(title="はてなで直した題")
    hatena_conflict.check(ACCOUNT, _cfg(), delivery)
    _resolve(delivery, "adopt")
    rc, out, err = call(["approve", upath, "--confirm", digest, "--by", "tester"])
    assert rc != 0 and "approval_stale" in out + err
    assert fm_of(upath)["status"] == "draft"


# ---------------------------------------------------------------- 中-1

def test_中1_削除を承認したあとにcheckで衝突が見つかればDELETEしない(lane, monkeypatch):
    _path, delivery, blog = published(lane)
    dpath = lane.queue("d.md", op="delete", delivery=delivery)
    approve(dpath)
    use_blog(monkeypatch, blog)
    blog.edit(body="はてなの画面で誰かが直した本文")
    hatena_conflict.check(ACCOUNT, _cfg(), delivery)
    blog.calls.clear()
    result = throw(blog)
    assert {"file": "d.md", "reason": "approval_stale"} in (result.rejections or [])
    assert blog.calls == [] and _record(delivery)["state"] == "conflict"
    # 承認し直すと「手直しも消えます」を見せたうえで消せる
    _digest, out = first_stage_digest(dpath)
    assert "消すと手直しも消えます" in out
    assert call(["revoke", dpath, "--by", "tester"])[0] == 0
    approve(dpath)
    assert throw(blog).action == "post" and _record(delivery)["state"] == "deleted"


def test_中1_承認の控えが無ければapproved_atより後の行で見る(lane, monkeypatch):
    _path, delivery, blog = published(lane)
    dpath = lane.queue("d.md", op="delete", delivery=delivery)
    approve(dpath)
    sha = fm_of(dpath)["approved_sha"]
    os.remove(hatena_entry._approval_seq_path(ACCOUNT, delivery, sha))
    ledger = _record(delivery)
    later = {"history": ledger["history"] + [{"seq": ledger["seq"] + 1, "state": "conflict", "op": "check",
                                              "at": "2099-01-01T00:00:00+09:00"}], "delivery": delivery}
    assert hatena_entry.changed_since_approval(ACCOUNT, later, sha, fm_of(dpath)["approved_at"]) is True
    assert hatena_entry.changed_since_approval(ACCOUNT, ledger, sha, fm_of(dpath)["approved_at"]) is False


# ---------------------------------------------------------------- 中-2

def test_中2_主体が止まっていれば候補のためのGETを打たない(lane, monkeypatch):
    """柵: candidates の頭の止めの段を消すと赤。"""
    path, delivery = approved(lane)
    blog = FakeBlog(result=hatena_publish.EntryResult(ok=False, ambiguous=True, error="hatena_transport_failed"))
    assert throw(blog).action == "inflight"
    blog.result = None
    use_blog(monkeypatch, blog)
    principals.stop("hatena-main", "owner", now=NOW)
    blog.calls.clear()
    found = hatena_publish.candidates(ACCOUNT, _cfg(), inflight.read(lane.state))
    assert found["error"] == "principal_stopped" and found["checked"] is False and blog.calls == []
    rc, out, err = call(["inflight", ACCOUNT, "show", "--json"])
    assert json.loads(out)["candidates_error"] == "principal_stopped" and blog.calls == []


# ---------------------------------------------------------------- 低-1

def test_低1_比べる先の無いreadback_filledは比べていない印と文(lane, monkeypatch):
    _path, delivery, blog = published(lane)
    loaded = deliveries.load(ACCOUNT, delivery)
    deliveries.advance(ACCOUNT, delivery, state="published", at=jst.iso(NOW), base=loaded,
                       fields={"readback_sha": None}, row={"op": "create", "resolution": "human_published"})
    use_blog(monkeypatch, blog)
    rc, out, err = call(["conflict", ACCOUNT, "check", delivery])
    assert rc == 0 and "比べる先（readback_sha）が無かったので、はてなの今の本文を正にしました（比べていない" in out
    last = _record(delivery)["history"][-1]
    assert last["resolution"] == "readback_filled" and last["compared"] is False


# ---------------------------------------------------------------- 低-2

def test_低2_遠くの道のinflight_showも候補を文で出す(monkeypatch, capsys):
    from thth import remote
    value = {"account": "a", "next": "次", "inflight": {"file": "x.md", "started": "2026-09-09T10:00:00+09:00",
             "age_hours": 1.0, "container_id_present": False, "post_id_present": False, "remote_state": None,
             "remote_checked_at": None, "mismatch_fields": None, "text_fingerprint_present": True,
             "media": False, "delivery": "dl_00000000000a", "seq": 3},
             "candidates": [{"entry_id": "tag:x-1", "title": "題", "published": "p", "edited": "e",
                             "body_same": True}],
             "candidates_checked": True, "candidates_error": None, "candidates_op": "create",
             "candidates_gone": None}
    monkeypatch.setattr(remote, "call", lambda op, account, **k: value)
    remote._inflight(argparse.Namespace(action="show", json=False), "a")
    out = capsys.readouterr().out
    assert "候補 1 件" in out and "resolve --published tag:x-1" in out


# ---------------------------------------------------------------- 低-3

def test_低3_checkの既定は5件_遠くの道はdelivery必須(lane):
    from thth import conflict_cli
    assert hatena_conflict.CHECK_LIMIT == 5
    parser = argparse.ArgumentParser()
    conflict_cli.register(parser.add_subparsers())
    assert parser.parse_args(["conflict", "a", "check"]).limit == 5
    from thth import server_writes
    with pytest.raises(Exception) as e:
        server_writes.conflict(None, {"operation": "conflict_status", "account": ACCOUNT, "check": True}, "mcp")
    assert str(e.value) == "invalid_request"


# ---------------------------------------------------------------- 低-4

def test_低4_draftの題とカテゴリはqueuefileの読みと往復する(lane):
    """柵: 題を引用符で囲む（往復しない）と赤。"""
    _path, delivery, _blog = published(lane)
    record = dict(_record(delivery), title="値動き: 10 月 #1 「速報」", categories=["コーヒー", "価格 #2"])
    text = article_cli.draft_text(record, NOW)
    fm = queuefile.parse_text(text, "x.md").front_matter
    assert fm["title"] == record["title"]
    assert hatena_entry.parse_categories(fm["categories"]) == record["categories"]
    bad = dict(record, categories=["a,b", "[x]", '"q"', "ok"])
    title, cats, notes = article_cli.draft_fields(bad)
    assert cats == ["ok"] and len(notes) == 3
    fm = queuefile.parse_text(article_cli.draft_text(bad, NOW), "x.md").front_matter
    assert hatena_entry.parse_categories(fm["categories"]) == ["ok"]


# ---------------------------------------------------------------- 低-5

def _mcp():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
    import server
    return server


@pytest.mark.parametrize("name, arguments, argv", [
    ("thth_article_diff", {"account": "a", "article": "-x", "delivery": "dl_1", "draft": True},
     ["article", "diff", "a", "--article=-x", "--delivery=dl_1", "--draft", "--json"]),
    ("thth_conflict_status", {"account": "a"}, ["conflict", "a", "--json"]),
    ("thth_conflict_status", {"account": "a", "delivery": "dl_1"}, ["conflict", "a", "dl_1", "show", "--json"]),
    ("thth_conflict_status", {"account": "a", "delivery": "dl_1", "check": True},
     ["conflict", "a", "check", "dl_1", "--json"]),
    ("thth_conflict_resolve", {"account": "a", "delivery": "dl_1", "resolution": "no_follow", "by": "-me",
                               "confirm": "-abc"},
     ["conflict", "a", "dl_1", "resolve", "--no-follow", "--by=-me", "--confirm=-abc", "--json"]),
])
def test_低5_MCPの手元の道の3つの道具はCLIを旗の形で呼ぶ(monkeypatch, name, arguments, argv):
    server = _mcp()
    seen = []
    monkeypatch.setattr(server, "server_mode", lambda: False)
    monkeypatch.setattr(server, "is_remote_tool", lambda n: False)
    monkeypatch.setattr(server, "run_cli", lambda args, **k: seen.append(args) or
                        subprocess.CompletedProcess(args, 0, '{"ok": true}', ""))
    result = server.call_tool(name, arguments)
    assert seen == [argv] and result["isError"] is False


def test_低5_位置引数がハイフンで始まれば断りCLIを呼ばない(monkeypatch):
    server = _mcp()
    monkeypatch.setattr(server, "server_mode", lambda: False)
    monkeypatch.setattr(server, "is_remote_tool", lambda n: False)
    monkeypatch.setattr(server, "run_cli", lambda *a, **k: pytest.fail("CLI を呼んだ"))
    result = server.call_tool("thth_conflict_status", {"account": "a", "delivery": "--json"})
    assert result["isError"] is True


def test_低5_resolveの1段目はエラーにしない_断りはエラー(monkeypatch):
    server = _mcp()
    monkeypatch.setattr(server, "server_mode", lambda: False)
    monkeypatch.setattr(server, "is_remote_tool", lambda n: False)
    args = {"account": "a", "delivery": "dl_1", "resolution": "adopt", "by": "me"}
    monkeypatch.setattr(server, "run_cli", lambda a, **k: subprocess.CompletedProcess(a, 1, '{"stage": 1}', ""))
    assert server.call_tool("thth_conflict_resolve", args)["isError"] is False
    monkeypatch.setattr(server, "run_cli",
                        lambda a, **k: subprocess.CompletedProcess(a, 1, '{"ok": false, "error": "x"}', ""))
    assert server.call_tool("thth_conflict_resolve", args)["isError"] is True


# ---------------------------------------------------------------- 低-6

def test_低6_取り込みの控えが読めなければ1段目に注記(lane, monkeypatch):
    """柵: 注記を消すと赤。"""
    _path, delivery, blog = published(lane)
    use_blog(monkeypatch, blog)
    blog.edit(body="はてなで直した本文")
    hatena_conflict.check(ACCOUNT, _cfg(), delivery)
    _resolve(delivery, "adopt")
    os.remove(hatena_entry.adopted_path(ACCOUNT, delivery, _record(delivery)["readback_sha"]))
    lane.write("articles/a.md", "# アラビカ\n\n直した本文。\n")
    upath = lane.queue("u.md", op="update", delivery=delivery)
    _digest, out = first_stage_digest(upath)
    assert "取り込みの控えが読めません" in out
