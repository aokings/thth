"""4.0 S5 の試験（2）: 衝突を見る・確かめる・解く（T-S5-4）・削除を衝突のまま・timer の題とカテゴリ（T-S5-5）。

HTTP は打たない（偽物の adapter `FakeBlog`）。はてなには出ない。
"""
from __future__ import annotations

import json
import os

import pytest

from tests.hatena_s1_flow_helpers import call, first_stage_digest
from tests.hatena_s5_helpers import (ACCOUNT, NOW, FakeBlog, approve, approved, body_sha, fm_of, lane,  # noqa: F401
                                     published, states, throw, use_blog, world)
from thth import (accounts, admin_log, article_cli, deliveries, events_cli, hatena_conflict, hatena_entry,
                  inflight, jst, principals, sent)
from thth.hatena_publish import EntryResult


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    slept = []
    monkeypatch.setattr(hatena_conflict, "_SLEEP", slept.append)
    return slept


def _cfg():
    return accounts.load_account(ACCOUNT)


def _record(delivery):
    return deliveries.read(ACCOUNT, delivery)


def _raw(delivery):
    with open(deliveries.path_for(ACCOUNT, delivery), "rb") as f:
        return f.read()


def _conflicted(lane, monkeypatch, *, body="はてなの画面で誰かが直した本文"):
    _path, delivery, blog = published(lane)
    use_blog(monkeypatch, blog)
    blog.edit(body=body)
    hatena_conflict.check(ACCOUNT, _cfg(), delivery)
    assert _record(delivery)["state"] == "conflict"
    return delivery, blog


def _resolve(delivery, resolution, *, confirm=True):
    first = hatena_conflict.resolve(ACCOUNT, _cfg(), delivery, resolution, by="tester")
    if not confirm:
        return first
    return hatena_conflict.resolve(ACCOUNT, _cfg(), delivery, resolution, by="tester", confirm=first["digest"])


# ---------------------------------------------------------------- T-S5-4a 一覧と show

def test_T_S5_4a_一覧は理由といつから_showはGET1回で差分_台帳は書かない(lane, monkeypatch):
    delivery, blog = _conflicted(lane, monkeypatch)
    rc, out, err = call(["conflict", ACCOUNT])
    assert rc == 0 and "conflict 1 件" in out and f"{delivery}" in out and "readback_changed・本文" in out
    rc, out, err = call(["conflict", ACCOUNT, "--json"])
    (row,) = json.loads(out)["conflicts"]
    assert row["reason"] == "readback_changed" and row["detail"] == ["body"] and row["since"]
    before = _raw(delivery)
    blog.calls.clear()
    rc, out, err = call(["conflict", ACCOUNT, delivery, "show"])
    assert rc == 0, err
    assert blog.calls == [("read", blog.entry_id)]
    assert "+はてなの画面で誰かが直した本文" in out and "---- 本文の差分（最後に送った本文 → はてなの今の本文） ----" in out
    assert _raw(delivery) == before


# ---------------------------------------------------------------- T-S5-4b check

def test_T_S5_4b_本文が違えばconflict_body_via_check(lane, monkeypatch):
    delivery, _blog = _conflicted(lane, monkeypatch)
    last = _record(delivery)["history"][-1]
    assert (last["state"], last["reason"], last["detail"], last["via"], last["op"]) == (
        "conflict", "readback_changed", ["body"], "check", "check")


def test_T_S5_4b_題だけ違えばdetail_title(lane, monkeypatch):
    _path, delivery, blog = published(lane)
    use_blog(monkeypatch, blog)
    blog.edit(title="はてなで直した題")
    result = hatena_conflict.check(ACCOUNT, _cfg(), delivery)
    assert result["results"] == [{"delivery": delivery, "result": "readback_changed", "detail": ["title"]}]
    assert _record(delivery)["history"][-1]["detail"] == ["title"]


def test_T_S5_4b_同じなら台帳は1バイトも変わらない(lane, monkeypatch):
    """柵: 同じときに行を書くと赤。"""
    _path, delivery, blog = published(lane)
    use_blog(monkeypatch, blog)
    before = _raw(delivery)
    result = hatena_conflict.check(ACCOUNT, _cfg(), delivery)
    assert result["results"][0]["result"] == "same"
    assert _raw(delivery) == before


def test_T_S5_4b_404はentry_missing(lane, monkeypatch):
    _path, delivery, blog = published(lane)
    use_blog(monkeypatch, blog)
    blog.gone = True
    hatena_conflict.check(ACCOUNT, _cfg(), delivery)
    last = _record(delivery)["history"][-1]
    assert (last["state"], last["reason"]) == ("conflict", "entry_missing")


def test_T_S5_4b_published_urlの無いpublishedは埋まる_seqが1進む(lane, monkeypatch):
    _path, delivery, blog = published(lane)
    use_blog(monkeypatch, blog)
    raw_path = deliveries.path_for(ACCOUNT, delivery)
    record = json.loads(_raw(delivery))
    for key in ("published_url", "edit_uri", "readback_sha"):
        record.pop(key)
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(record, f)
    seq = record["seq"]
    result = hatena_conflict.check(ACCOUNT, _cfg(), delivery)
    assert result["results"][0]["result"] == "readback_filled"
    record = _record(delivery)
    assert record["seq"] == seq + 1 and record["state"] == "published"
    assert record["published_url"] == blog.url and record["edit_uri"] and record["readback_sha"] == body_sha(blog.body)
    assert record["history"][-1]["resolution"] == "readback_filled"
    # 出来事には出さない
    assert events_cli._delivery_kind(record["history"][-1]) is None


def test_T_S5_4b_1件ごとに2秒空ける(lane, monkeypatch, no_wait):
    _path, d1, blog = published(lane)
    use_blog(monkeypatch, blog)
    d2 = deliveries.create(ACCOUNT, principal="hatena-main", blog_id="a.hatenablog.com", article="lane-a/b",
                           at=jst.iso(NOW), state="published",
                           fields=dict({k: _record(d1)[k] for k in ("entry_id", "edit_uri", "readback_sha",
                                                                    "published_url", "title", "categories")}))["delivery"]
    result = hatena_conflict.check(ACCOUNT, _cfg())
    assert {r["delivery"] for r in result["results"]} == {d1, d2}
    assert no_wait == [hatena_conflict.CHECK_INTERVAL_SECONDS]


# ---------------------------------------------------------------- T-S5-4c --adopt

def test_T_S5_4c_adoptは2段_digestはseqを含む_取り込んだ本文が1段目の前になる(lane, monkeypatch):
    """柵: `checked_rev` を触ると赤。"""
    _path, delivery, blog = published(lane)
    lane.write("articles/a.md", "# アラビカ\n\n直した本文。\n")
    upath, _ = approved(lane, "u.md", op="update", delivery=delivery)
    use_blog(monkeypatch, blog)
    blog.edit(body="はてなで直した本文", title="はてなで直した題", categories=["コーヒー"])
    assert throw(blog).action == "conflict"
    record = _record(delivery)
    checked_rev = record["checked_rev"]
    # 1 段目: 何も書かない
    before = _raw(delivery)
    first = hatena_conflict.resolve(ACCOUNT, _cfg(), delivery, "adopt", by="tester")
    assert first["resolved"] is False and first["stage"] == 1
    assert _raw(delivery) == before
    assert first["digest"] == hatena_conflict.digest(ACCOUNT, delivery, record["seq"], "adopt", body_sha(blog.body))
    assert first["digest"] != hatena_conflict.digest(ACCOUNT, delivery, record["seq"] + 1, "adopt",
                                                       body_sha(blog.body))
    # 違う digest は断る
    with pytest.raises(hatena_conflict.ConflictRefused) as e:
        hatena_conflict.resolve(ACCOUNT, _cfg(), delivery, "adopt", by="tester", confirm="000000000000")
    assert e.value.code == "confirm_mismatch"
    # 2 段目（CLI）
    rc, out, err = call(["conflict", ACCOUNT, delivery, "resolve", "--adopt", "--by", "tester",
                         "--confirm", first["digest"]])
    assert rc == 0, (out, err)
    record = _record(delivery)
    assert record["state"] == "published" and record["checked_rev"] == checked_rev
    assert record["readback_sha"] == body_sha("はてなで直した本文")
    assert (record["title"], record["categories"]) == ("はてなで直した題", ["コーヒー"])
    last = record["history"][-1]
    assert (last["op"], last["resolution"], last["resolved_by"]) == ("resolve", "adopt_remote", "tester")
    rb = hatena_entry.adopted_path(ACCOUNT, delivery, record["readback_sha"])
    assert open(rb, encoding="utf-8").read() == "はてなで直した本文\n"
    assert oct(os.stat(rb).st_mode & 0o777) == "0o600"
    rows, _ = admin_log.read(event="delivery_resolved")
    assert len(rows) == 1 and rows[0]["account"] == ACCOUNT
    assert "はてなで直した本文" not in json.dumps(rows[0], ensure_ascii=False)
    # 承認済みの更新は approval_stale（変更前の版が変わった）
    result = throw(blog)
    assert {"file": "u.md", "reason": "approval_stale"} in result.rejections
    # 承認し直すと 1 段目の「前」は取り込んだ本文（上書きの承認）
    _digest, out = first_stage_digest(upath)
    assert "はてなの今の本文を" in out and "-はてなで直した本文" in out and "+直した本文。" in out
    assert "題: はてなで直した題 → アラビカの値動き" in out
    approve(upath)
    assert throw(blog).action == "post"
    assert blog.body.startswith("# アラビカ") or "直した本文。" in blog.body
    assert _record(delivery)["checked_rev"] != checked_rev


# ---------------------------------------------------------------- T-S5-4d --gone

def test_T_S5_4d_goneはentry_missingをdeletedに_sentは触らない(lane, monkeypatch):
    _path, delivery, blog = published(lane)
    use_blog(monkeypatch, blog)
    blog.gone = True
    hatena_conflict.check(ACCOUNT, _cfg(), delivery)
    sent_before = sent.records(lane.state)
    with pytest.raises(hatena_conflict.ConflictRefused) as e:
        _resolve(delivery, "adopt")
    assert e.value.code == "resolution_not_applicable" and "--gone" in e.value.message
    _resolve(delivery, "gone")
    record = _record(delivery)
    assert record["state"] == "deleted" and record["history"][-1]["resolution"] == "remote_deleted"
    assert sent.records(lane.state) == sent_before


def test_T_S5_4d_adoptのGETが404ならentry_missingに書き換えてgoneを案内(lane, monkeypatch):
    delivery, blog = _conflicted(lane, monkeypatch)
    blog.gone = True
    with pytest.raises(hatena_conflict.ConflictRefused) as e:
        hatena_conflict.resolve(ACCOUNT, _cfg(), delivery, "adopt", by="tester")
    assert e.value.code == "entry_missing" and "resolve --gone" in e.value.message
    last = _record(delivery)["history"][-1]
    assert (last["state"], last["reason"]) == ("conflict", "entry_missing")


def test_T_S5_4d_adoptのGETが失敗なら何も書かない(lane, monkeypatch):
    delivery, blog = _conflicted(lane, monkeypatch)
    blog.fail_read = True
    before = _raw(delivery)
    with pytest.raises(hatena_conflict.ConflictRefused) as e:
        hatena_conflict.resolve(ACCOUNT, _cfg(), delivery, "adopt", by="tester")
    assert e.value.code == "readback_failed" and _raw(delivery) == before


# ---------------------------------------------------------------- T-S5-4e --no-follow

def test_T_S5_4e_no_followは更新を断り削除は通しadoptで戻る(lane, monkeypatch):
    _path, delivery, blog = published(lane)
    use_blog(monkeypatch, blog)
    _resolve(delivery, "no_follow")
    assert _record(delivery)["state"] == "no_follow"
    lane.write("articles/a.md", "# アラビカ\n\n直した。\n")
    assert article_cli.build(ACCOUNT, now=NOW)["counts"]["no_follow"] == 1
    upath = lane.queue("u.md", op="update", delivery=delivery)
    rc, out, err = call(["approve", upath])
    assert rc != 0 and "delivery_state_no_follow" in out + err
    dpath = lane.queue("d.md", op="delete", delivery=delivery)
    _digest, out = first_stage_digest(dpath)
    assert "追従をやめています（no_follow）" in out
    _resolve(delivery, "adopt")
    assert _record(delivery)["state"] == "published"


# ---------------------------------------------------------------- T-S5-4f inflight と主体の止め

def test_T_S5_4f_inflightが残ればcheckとresolveはinflight_open(lane, monkeypatch):
    delivery, blog = _conflicted(lane, monkeypatch)
    inflight.write(lane.state, file="x.md", started=jst.iso(NOW), container_id=None, body_hash="h",
                   approved_fingerprint="f", delivery=delivery, seq=99)
    for call_ in (lambda: hatena_conflict.check(ACCOUNT, _cfg()),
                  lambda: hatena_conflict.resolve(ACCOUNT, _cfg(), delivery, "adopt", by="tester")):
        with pytest.raises(hatena_conflict.ConflictRefused) as e:
            call_()
        assert e.value.code == "inflight_open"


def test_T_S5_4f_主体が止まっていればGETを打たない(lane, monkeypatch):
    delivery, blog = _conflicted(lane, monkeypatch)
    principals.stop("hatena-main", "owner", now=NOW)
    blog.calls.clear()
    for call_ in (lambda: hatena_conflict.check(ACCOUNT, _cfg()),
                  lambda: hatena_conflict.show(ACCOUNT, _cfg(), delivery),
                  lambda: hatena_conflict.resolve(ACCOUNT, _cfg(), delivery, "adopt", by="tester")):
        with pytest.raises(hatena_conflict.ConflictRefused) as e:
            call_()
        assert e.value.code == "principal_stopped"
    assert blog.calls == []


# ---------------------------------------------------------------- T-S5-4g 出来事

def test_T_S5_4g_resolveの行はdelivery_resolved_checkのconflictはdelivery_conflict(lane, monkeypatch):
    delivery, blog = _conflicted(lane, monkeypatch)
    _resolve(delivery, "adopt")
    rows = events_cli.build(ACCOUNT, since="7d", now=jst.now_jst())["events"]
    kinds = [(e["kind"], e["seq"]) for e in rows if e.get("ref") == delivery and "seq" in e]
    record = _record(delivery)
    assert ("delivery_conflict", record["seq"] - 1) in kinds
    assert ("delivery_resolved", record["seq"]) in kinds
    assert ("delivery_published", record["seq"]) not in kinds
    assert events_cli._delivery_kind({"state": "deleted", "op": "resolve"}) == "delivery_resolved"
    assert events_cli._delivery_kind({"state": "no_follow", "op": "resolve"}) == "delivery_resolved"


def test_T_S5_4g_delivery_behindはongoingで_idは配信と版の組_更新すると消える(lane):
    """柵: 時刻を id に混ぜると赤。"""
    import hashlib
    _path, delivery, blog = published(lane)
    lane.write("articles/a.md", "# アラビカ\n\n直した。\n")

    def behind():
        return [e for e in events_cli.build(ACCOUNT, since="1h", now=NOW)["events"] if e["kind"] == "delivery_behind"]
    (row,) = behind()
    s = hatena_entry.source_status(_record(delivery), _cfg())
    expected = "ev_" + hashlib.sha256(
        f"thth.behind/1|{delivery}|{s['checked_rev']}|{s['current_rev']}".encode()).hexdigest()[:24]
    assert row["event_id"] == expected and row["ongoing"] is True and row["ref"] == delivery
    assert [e["event_id"] for e in behind()] == [expected]       # 何度読んでも同じ
    approved(lane, "u.md", op="update", delivery=delivery)
    assert throw(blog).action == "post"
    assert behind() == []


# ---------------------------------------------------------------- T-S5-5a 削除を衝突のまま

def test_T_S5_5a_readback_changedの配信も削除を承認でき_timerがDELETEしてdeleted(lane, monkeypatch):
    delivery, blog = _conflicted(lane, monkeypatch)
    path = lane.queue("d.md", op="delete", delivery=delivery)
    _digest, out = first_stage_digest(path)
    assert "この記事は外で手直しされています" in out and "消すと手直しも消えます" in out
    approve(path)
    blog.calls.clear()
    assert throw(blog).action == "post"
    assert blog.calls == [("delete", blog.entry_id)]
    assert _record(delivery)["state"] == "deleted"


def test_T_S5_5a_entry_missingの配信の削除はdelivery_state_conflict(lane, monkeypatch):
    _path, delivery, blog = published(lane)
    use_blog(monkeypatch, blog)
    blog.gone = True
    hatena_conflict.check(ACCOUNT, _cfg(), delivery)
    path = lane.queue("d.md", op="delete", delivery=delivery)
    rc, out, err = call(["approve", path])
    assert rc != 0 and "delivery_state_conflict" in out + err and "resolve --gone" in out + err


def test_T_S5_5a_断られた削除はconflictに戻る(lane, monkeypatch):
    """柵: `BEFORE["delete"]`（固定の published）に戻すと赤。"""
    delivery, blog = _conflicted(lane, monkeypatch)
    path = lane.queue("d.md", op="delete", delivery=delivery)
    approve(path)
    refusing = FakeBlog(result=EntryResult(ok=False, ambiguous=False, error="hatena_http_400"))
    assert throw(refusing).action == "post"   # 断り（公開に失敗しました）
    record = _record(delivery)
    assert states(delivery)[-2:] == ["inflight", "conflict"]
    assert record["history"][-1].get("restored") is True
    assert events_cli._delivery_kind(record["history"][-1]) is None


# ---------------------------------------------------------------- T-S5-5b timer の読み戻しで題とカテゴリ

def test_T_S5_5b_本文が同じで題だけ違えばconflict_PUTしない(lane):
    """柵: 題の比べを消すと赤。"""
    _path, delivery, blog = published(lane)
    lane.write("articles/a.md", "# アラビカ\n\n直した。\n")
    approved(lane, "u.md", op="update", delivery=delivery)
    blog.edit(title="はてなで直した題")
    blog.calls.clear()
    result = throw(blog)
    assert result.action == "conflict"
    assert [c[0] for c in blog.calls] == ["read"]
    last = _record(delivery)["history"][-1]
    assert (last["reason"], last["detail"]) == ("readback_changed", ["title"])


def test_T_S5_5b_カテゴリだけ違ってもconflict_並びだけなら通す(lane):
    _path, delivery, blog = published(lane)
    lane.write("articles/a.md", "# アラビカ\n\n直した。\n")
    approved(lane, "u.md", op="update", delivery=delivery)
    blog.edit(categories=["価格", "コーヒー"])        # 並びだけ
    assert throw(blog).action == "post"
    lane.write("articles/a.md", "# アラビカ\n\nもう一度直した。\n")
    approved(lane, "u2.md", op="update", delivery=delivery)
    blog.edit(categories=["コーヒー"])
    assert throw(blog).action == "conflict"
    assert _record(delivery)["history"][-1]["detail"] == ["categories"]


def test_T_S5_5b_台帳に題が無ければ本文だけで比べる(lane):
    _path, delivery, blog = published(lane)
    raw_path = deliveries.path_for(ACCOUNT, delivery)
    record = json.loads(_raw(delivery))
    for key in ("title", "categories", "readback_title", "readback_categories"):
        record.pop(key, None)
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(record, f)
    lane.write("articles/a.md", "# アラビカ\n\n直した。\n")
    approved(lane, "u.md", op="update", delivery=delivery)
    blog.edit(title="はてなで直した題")
    assert throw(blog).action == "post"
