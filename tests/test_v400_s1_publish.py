"""4.0 S1 §9 の順 8: `select_one` のはてなの枝と、timer が出す順（§6.5）。送信は fake の adapter。

見るのは:
  - 承認 → timer: 台帳 wanted(1) → inflight(2) → published(3)・`sent/` に kind・blog_id・delivery・url。
  - adapter が無ければ `unsupported_medium` で止まる（inflight も台帳も書かない）。dry-run は何も書かない。
  - T-5b: 承認のあとに主体の台帳から lanes を外す → select は通るが、ロックの中の確かめで止まる。
  - T-5c: 承認のあとに口座の blog_id を変える → `approval_stale`。
  - T-5d: 応答のホストが blog_id と違う → 台帳 conflict（published_elsewhere）・出し直さない。
  - T-7a: update の前の読み戻しが違えば conflict・PUT は呼ばれない。
  - 結果不明は inflight を残して unknown（出し直さない）・確かな断りは元の状態に戻す。
  - SNS の口座の原稿に `approved_kind` があれば `approval_stale`。
"""
from __future__ import annotations



import pytest

from tests.conftest import parse_verified
from tests.hatena_s1_flow_helpers import (ACCOUNT, NOW, FakeAdapter, approve, factory_for, fm_of, lane,  # noqa: F401
                                          world)
from thth import accounts, core, deliveries, inflight, jst, principals, select, sent
from thth.hatena_publish import EntryResult


def throw(adapter=None, *, production=True):
    factory = factory_for(adapter) if adapter is not None else None
    return core.throw_once(ACCOUNT, production_flag=production, adapter_factory=factory, now=NOW)


def approved(lane, name="a.md", **kwargs):
    path = lane.queue(name, **kwargs)
    approve(path)
    return path, fm_of(path)["delivery"]


def states(delivery):
    return [row["state"] for row in deliveries.read(ACCOUNT, delivery)["history"]]


def test_承認からtimerで出て台帳と記録と書き戻し(lane):
    path, delivery = approved(lane)
    adapter = FakeAdapter()
    result = throw(adapter)
    assert (result.action, result.exit_code) == ("post", 0)
    assert adapter.calls == [("create", "アラビカの値動き")]
    record = deliveries.read(ACCOUNT, delivery)
    assert states(delivery) == ["wanted", "inflight", "published"]
    assert record["entry_id"] == adapter.entry_id and record["published_url"] == adapter.url
    assert record["readback_sha"] and record["checked_rev"] == record["wanted_rev"]
    (row,) = sent.records(lane.state)
    assert (row["kind"], row["blog_id"], row["delivery"], row["url"]) == (
        "entry_create", "a.hatenablog.com", delivery, adapter.url)
    assert row["post_id"] == adapter.entry_id
    fm = fm_of(path)
    assert (fm["status"], fm["post_id"], fm["url"]) == ("posted", adapter.entry_id, adapter.url)
    assert inflight.read(lane.state) is None
    # 次の run は出すものが無い（post_id がある）
    assert throw(FakeAdapter()).action == "none"


def test_adapterが無ければunsupported_mediumで止まり何も書かない(lane):
    path, delivery = approved(lane)
    result = throw(None)
    assert result.action == "unsupported_medium" and result.error == "unsupported_medium"
    assert result.exit_code == 2
    assert inflight.read(lane.state) is None
    assert states(delivery) == ["wanted"]
    assert sent.records(lane.state) == []
    assert fm_of(path)["status"] == "approved"


def test_dry_runは見せるだけで何も書かない(lane):
    path, delivery = approved(lane)
    adapter = FakeAdapter()
    result = throw(adapter, production=False)
    assert result.action == "skip"
    assert adapter.calls == [] and states(delivery) == ["wanted"] and inflight.read(lane.state) is None


# --------------------------------------------------------------------------
# T-5b 送信の段の誤送信（ロックの中の確かめ）
# --------------------------------------------------------------------------

@pytest.mark.parametrize("production", [True, False])
def test_T5b_承認のあとにlanesから外すとロックの中で止まる(lane, production):
    """柵: `hatena_publish.throw_chosen` のロックの中の確かめ（主体の台帳の読み直し）を消すと赤。"""
    path, delivery = approved(lane)
    stale_cfg = accounts.load_account(ACCOUNT)       # 承認のときに読んだ台帳（run の始まり）
    principal = lane.world.principal("hatena-main")
    principal["blogs"][0]["lanes"] = ["lane-b"]
    lane.world.write_principal(principal)
    # select は通る（指紋に入るのは口座の blog_id で、主体の台帳の lanes ではない）
    files = core.list_queue_files(stale_cfg, tree_sha=None)
    for qf in files:
        qf.verified = True
    chosen = select.select_one(files, account_name=ACCOUNT, account_cfg=stale_cfg, now=NOW,
                               last_post_at=None, recent_texts=set()).chosen
    assert chosen is not None and chosen.path == path
    adapter = FakeAdapter()
    result = core._throw_locked(ACCOUNT, stale_cfg, lane.state, "run1", production_flag=production,
                                bypass_pace=False, adapter_factory=factory_for(adapter), log=lambda _l: None,
                                now=NOW)
    assert result.error == "blog_not_allowed_for_lane", result
    assert adapter.calls == []
    assert inflight.read(lane.state) is None
    assert sent.records(lane.state) == []
    assert states(delivery) == ["wanted"]


def test_T5c_承認のあとに口座のblog_idを変えるとapproval_stale(lane):
    path, delivery = approved(lane)
    data = lane.world.account(ACCOUNT)
    data["blog_id"] = "shared.hatenablog.com"      # lane-a に許されている別のブログ
    lane.world.write_account(data)
    adapter = FakeAdapter()
    result = throw(adapter)
    assert result.action == "none"
    assert {"file": "a.md", "reason": "approval_stale"} in result.rejections
    assert adapter.calls == []


def test_T5d_応答のホストが違えば台帳conflictで出し直さない(lane):
    path, delivery = approved(lane)
    adapter = FakeAdapter(url="https://elsewhere.example/entry/1")
    result = throw(adapter)
    assert result.error == "published_elsewhere"
    record = deliveries.read(ACCOUNT, delivery)
    assert record["state"] == "conflict" and record["history"][-1]["reason"] == "published_elsewhere"
    (row,) = sent.records(lane.state)
    assert row["kind"] == "entry_create" and "url" not in row
    assert fm_of(path)["status"] == "posted"
    again = FakeAdapter()
    assert throw(again).action == "none" and again.calls == []


# --------------------------------------------------------------------------
# T-7a 更新の前の読み戻し
# --------------------------------------------------------------------------

def _published(lane):
    path, delivery = approved(lane)
    first = FakeAdapter()
    assert throw(first).action == "post"
    return delivery, first


def _update_manuscript(lane, delivery):
    lane.write("articles/a.md", "# アラビカ\n\n直した本文。\n")
    path, _ = approved(lane, "u.md", op="update", delivery=delivery)
    return path


def test_T7a_読み戻しが承認した変更前の版と違えばconflictでPUTしない(lane):
    """柵: 読み戻しの比べを消すと赤（update_entry が呼ばれる）。"""
    delivery, _ = _published(lane)
    path = _update_manuscript(lane, delivery)
    adapter = FakeAdapter(current_body="はてなの画面で誰かが直した本文")
    result = throw(adapter)
    assert result.action == "conflict" and result.error == "delivery_conflict"
    assert adapter.calls == [("get", deliveries.read(ACCOUNT, delivery)["entry_id"])]
    record = deliveries.read(ACCOUNT, delivery)
    assert record["state"] == "conflict" and record["history"][-1]["reason"] == "readback_changed"
    assert inflight.read(lane.state) is None
    # conflict の配信は timer が拾わない（人が見る）
    later = FakeAdapter(current_body="x")
    result = throw(later)
    assert later.calls == [] and {"file": "u.md", "reason": "delivery_state_conflict"} in result.rejections


def test_T7a_読み戻しが同じならPUTして更新の行と別の記録の鍵(lane):
    delivery, first = _published(lane)
    sent_body = sent.records(lane.state)[0]["text"]
    path = _update_manuscript(lane, delivery)
    adapter = FakeAdapter(current_body=sent_body)
    result = throw(adapter)
    assert result.action == "post" and result.exit_code == 0, result
    assert [c[0] for c in adapter.calls] == ["get", "update"]
    record = deliveries.read(ACCOUNT, delivery)
    assert states(delivery) == ["wanted", "inflight", "published", "inflight", "published"]
    assert record["history"][-1]["op"] == "update"
    kinds = sorted((row["kind"], row["post_id"]) for row in sent.records(lane.state))
    assert kinds == [("entry_create", first.entry_id), ("entry_update", f"{first.entry_id}#u5")]


def test_削除は作成の行にretracted_atを足し台帳deleted(lane):
    delivery, first = _published(lane)
    path, _ = approved(lane, "d.md", op="delete", delivery=delivery)
    adapter = FakeAdapter()
    result = throw(adapter)
    assert result.action == "post", result
    assert adapter.calls == [("delete", first.entry_id)]
    assert deliveries.read(ACCOUNT, delivery)["state"] == "deleted"
    (row,) = sent.records(lane.state)
    assert row["retracted_at"] and row["kind"] == "entry_create"


# --------------------------------------------------------------------------
# 結果不明・断り・安全装置
# --------------------------------------------------------------------------

class Boom(FakeAdapter):
    def create_entry(self, **kwargs):
        self.calls.append(("create", kwargs["title"]))
        raise TimeoutError("no answer")


def test_結果不明はinflightを残しunknown_次のrunは止まる(lane):
    path, delivery = approved(lane)
    adapter = Boom()
    result = throw(adapter)
    assert result.action == "inflight" and result.exit_code == 1
    record = deliveries.read(ACCOUNT, delivery)
    assert states(delivery) == ["wanted", "inflight", "unknown"]
    held = inflight.read(lane.state)
    assert held["delivery"] == delivery and held["seq"] == record["seq"] + 1
    again = FakeAdapter()
    assert throw(again).action == "inflight" and again.calls == []


def test_確かな断りは元の状態に戻しinflightを消す(lane):
    path, delivery = approved(lane)
    adapter = FakeAdapter(result=EntryResult(ok=False, error="hatena_400"))
    result = throw(adapter)
    assert result.error == "hatena_400"
    assert states(delivery) == ["wanted", "inflight", "wanted"]
    assert inflight.read(lane.state) is None and sent.records(lane.state) == []


def test_主体が止まっていればguardで断り何も書かない(lane):
    path, delivery = approved(lane)
    principals.stop("hatena-main", "owner", now=NOW)
    adapter = FakeAdapter()
    result = throw(adapter)
    assert result.action == "guard_refused" and result.error == "principal_stopped"
    assert adapter.calls == [] and states(delivery) == ["wanted"] and inflight.read(lane.state) is None


def test_主体の上限は2レーン合計で効く(lane):
    path, delivery = approved(lane)
    for i in range(6):
        sent.write(lane.world.state("lane-b-hatenablog"), post_id=f"tag:x-{i}", text="t", body_hash="0" * 64,
                   sent_at=jst.iso(NOW), kind="entry_create", blog_id="b.hatenablog.com")
    result = throw(FakeAdapter())
    assert result.action == "guard_refused" and result.error == "principal_daily_limit"
    assert result.exit_code == 0


def test_SNSの口座の原稿にapproved_kindがあればapproval_stale(isolated_account):
    from tests.conftest import write_queue_file
    path = write_queue_file(isolated_account["queue_dir"], "a.md", commit=False)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.replace("status: approved", "status: approved\napproved_kind: hatena_entry"))
    cfg = accounts.load_account(isolated_account["name"])
    result = select.select_one([parse_verified(path)], account_name=isolated_account["name"], account_cfg=cfg,
                               now=jst.now_jst(), last_post_at=None, recent_texts=set())
    assert result.chosen is None
    assert [r.reason for r in result.rejections] == ["approval_stale"]


def test_SNSの口座の原稿はapproved_kindが無ければ今までどおり選ばれる(isolated_account):
    from tests.conftest import write_queue_file
    path = write_queue_file(isolated_account["queue_dir"], "a.md", commit=False)
    cfg = accounts.load_account(isolated_account["name"])
    result = select.select_one([parse_verified(path)], account_name=isolated_account["name"], account_cfg=cfg,
                               now=jst.now_jst(), last_post_at=None, recent_texts=set())
    assert result.chosen is not None
