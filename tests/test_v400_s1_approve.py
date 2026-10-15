"""4.0 S1 §9 の順 7: 配信原稿の lint・`thth approve` の 1 段目（§5.1・§6.3）・2 段目の書き戻しと配信の台帳。

見るのは:
  - lint の符丁（§3.5）が 1 つずつ出て、`lint.reason_code` が同じ符丁に畳む。
  - T-5a: 原稿に `blog_id:` があれば lint が断る。口座の blog_id が lanes に無ければ 1 段目に届かない。
  - T-6a: 1 段目に blog_id・主体・レーン・操作・題・カテゴリ・画像・本文の全文・digest。update は差分・delete は URL。
  - 2 段目: `approved_kind: hatena_entry` と `delivery` を書き戻し、配信の台帳を `wanted`（seq 1）で作る。
  - 取り消した予約を承認し直すと、同じ配信の台帳に `wanted` の行を足す（振り直さない）。
"""
from __future__ import annotations

import json
import os

import pytest

from tests.hatena_s1_flow_helpers import (ACCOUNT, NOW, FakeAdapter, approve, call, factory_for, first_stage_digest,
                                          fm_of, lane, manuscript, world)  # noqa: F401
from thth import approval, core, deliveries, hatena_entry, lint, queuefile


def lint_codes(path):
    return [line.split(":", 1)[0] for line in lint.lint_file(path) if not lint.is_warning(line)]


# --------------------------------------------------------------------------
# lint（§3.5）
# --------------------------------------------------------------------------

def test_lint_正しい配信原稿は通る(lane):
    path = lane.queue()
    assert lint.lint_file(path) == []


@pytest.mark.parametrize("kwargs,code", [
    ({"extra": {"blog_id": "a.hatenablog.com"}}, "blog_id_in_manuscript"),
    ({"op": "publish"}, "invalid_op"),
    ({"title": ""}, "title_required"),
    ({"article_source": "articles/none.md"}, "article_source_missing"),
    ({"article_source": "../outside.md"}, "invalid_article_source"),
    ({"article_source": ".hidden/a.md"}, "invalid_article_source"),
    ({"categories": "[" + ", ".join(f"c{i}" for i in range(11)) + "]"}, "categories_invalid"),
    ({"categories": "[" + "あ" * 41 + "]"}, "categories_invalid"),
    ({"op": "update"}, "delivery_required"),
    ({"delivery": "dl_000000000000"}, "delivery_unknown"),
    ({"extra": {"form": "poem"}}, "invalid_form"),
])
def test_lint_符丁(lane, kwargs, code):
    text = manuscript(**kwargs)
    if "form" in (kwargs.get("extra") or {}):
        text = text.replace("form: full\n", "")
    path = lane.write("docs/sns/queue/x.md", text)
    codes = lint_codes(path)
    assert code in codes, codes
    assert lint.reason_code(lint.lint_file(path)) in lint.REASONS


def test_lint_articleが無ければarticle_required(lane):
    path = lane.write("docs/sns/queue/x.md", manuscript().replace("article: lane-a/arabica\n", ""))
    assert "article_required" in lint_codes(path)


def test_lint_画像がrepoに無ければimage_missing(lane):
    lane.write("articles/b.md", "本文\n\n![無い](../images/none.png)\n")
    path = lane.queue(article_source="articles/b.md")
    assert lint_codes(path) == ["image_missing"]


def test_lint_reason_codeは配信原稿の符丁を畳む(lane):
    path = lane.write("docs/sns/queue/x.md", manuscript(extra={"blog_id": "a.hatenablog.com"}))
    assert lint.reason_code(lint.lint_file(path)) == "blog_id_in_manuscript"


# --------------------------------------------------------------------------
# T-5a 承認の段の誤送信
# --------------------------------------------------------------------------

def test_T5a_原稿にblog_idがあれば承認の1段目に届かない(lane):
    path = lane.write("docs/sns/queue/x.md", manuscript(extra={"blog_id": "b.hatenablog.com"}))
    rc, out, err = call(["approve", path])
    assert rc == 1 and "digest:" not in out
    assert "blog_id_in_manuscript" in err


def test_T5a_口座のblog_idがlanesに無ければ承認の1段目に届かない(lane):
    path = lane.queue()
    principal = lane.world.principal("hatena-main")
    principal["blogs"][0]["lanes"] = ["lane-b"]
    lane.world.write_principal(principal)
    rc, out, err = call(["approve", path])
    assert rc == 1 and "digest:" not in out
    assert fm_of(path)["status"] == "draft"


# --------------------------------------------------------------------------
# T-6a 1 段目の表示
# --------------------------------------------------------------------------

def test_T6a_1段目に行き先と記事と本文の全文とdigest(lane):
    path = lane.queue()
    digest, out = first_stage_digest(path)
    assert "配信原稿  lane-a-hatenablog → a.hatenablog.com（主体 hatena-main・レーン lane-a）" in out
    assert "操作: 新規" in out and "timer が draft=no で送る" in out
    assert "記事: lane-a/arabica（articles/a.md・版 " in out and " git）" in out
    assert "題: アラビカの値動き   カテゴリ: コーヒー・価格" in out
    assert "images/bean.png（21 bytes・sha256 " in out
    for line in ("# アラビカ", "値動きのまとめ。", "![豆](../images/bean.png)", "終わり。"):
        assert line in out
    assert "---- 変換したあとの本文（全文） ----" in out and "---- ここまで ----" in out
    assert len(digest) == 12
    assert f"thth approve {path} --confirm {digest}" in out
    # 1 段目は何も書かない（原稿も配信の台帳も）
    assert fm_of(path)["status"] == "draft"
    assert deliveries.list_ids(ACCOUNT) == []


def test_T6a_1段目のjsonにはてなの欄(lane):
    path = lane.queue()
    rc, out, err = call(["approve", path, "--json"])
    assert rc == 1
    value = json.loads(out)
    one = value["files"][0]
    assert one["hatena"]["blog_id"] == "a.hatenablog.com" and one["hatena"]["op"] == "create"
    assert one["hatena"]["images"][0]["path"] == "images/bean.png"
    assert one["text"].startswith("# アラビカ")


def test_T6a_1段目は見せた本文の控えをstateに置く(lane):
    path = lane.queue()
    first_stage_digest(path)
    pending = os.path.join(lane.state, "approve_pending", "hatena")
    (only,) = os.listdir(pending)
    with open(os.path.join(pending, only, "body.md"), encoding="utf-8") as f:
        assert f.read().startswith("# アラビカ")


def _publish_create(lane):
    path = lane.queue()
    approve(path)
    adapter = FakeAdapter()
    result = core.throw_once(ACCOUNT, production_flag=True, adapter_factory=factory_for(adapter), now=NOW)
    assert result.action == "post" and result.exit_code == 0, result
    return path, fm_of(path)["delivery"]


def test_T6a_updateは変更前の版と差分を見せる(lane):
    _path, delivery = _publish_create(lane)
    lane.write("articles/a.md", "# アラビカ\n\n値動きのまとめ（直した）。\n\n![豆](../images/bean.png)\n\n終わり。\n")
    path = lane.queue("u.md", op="update", delivery=delivery)
    digest, out = first_stage_digest(path)
    assert "操作: 更新" in out
    assert "変更前の版 readback_sha " in out
    assert "---- 本文の差分（前に送った本文 → 今回） ----" in out
    assert "-値動きのまとめ。" in out and "+値動きのまとめ（直した）。" in out


def test_T6a_deleteは題と公開URLとentry_id(lane):
    _path, delivery = _publish_create(lane)
    path = lane.queue("d.md", op="delete", delivery=delivery)
    digest, out = first_stage_digest(path)
    assert "操作: 削除" in out
    assert "公開 URL: https://a.hatenablog.com/entry/2026/09/09/100000" in out
    assert "entry_id: tag:blog.hatena.ne.jp,2013:blog-lane-owner-1-99" in out


# --------------------------------------------------------------------------
# 2 段目: 書き戻しと配信の台帳
# --------------------------------------------------------------------------

def test_2段目はapproved_kindとdeliveryを書き戻し台帳をwantedで作る(lane):
    path = lane.queue()
    digest = approve(path)
    fm = fm_of(path)
    assert fm["status"] == "approved" and fm["approved_kind"] == "hatena_entry"
    assert fm["approved_sha"].startswith(digest)
    record = deliveries.read(ACCOUNT, fm["delivery"])
    assert record["state"] == "wanted" and record["seq"] == 1
    assert record["blog_id"] == "a.hatenablog.com" and record["principal"] == "hatena-main"
    assert record["history"][0]["approved_sha"] == fm["approved_sha"]
    assert record["history"][0]["approved_by"] == "tester"
    assert record["queue_file"] == "docs/sns/queue/a.md"
    assert record["wanted_rev"] and record["article_rev_kind"] == "git"
    assert record["images"] == [{"path": "images/bean.png",
                                 "source_sha256": hatena_entry.hatena_convert.file_sha256(
                                     os.path.join(lane.repo, "images/bean.png"))[0]}]
    # 1 段目の控えは消え、承認した本文の控えが配信の台帳の隣に残る
    assert not os.listdir(os.path.join(lane.state, "approve_pending", "hatena"))
    kept = [n for n in os.listdir(deliveries.dir_for(ACCOUNT)) if n.endswith(".body.md")]
    assert kept == [f"{fm['delivery']}.{fm['approved_sha'][:12]}.body.md"]


def test_2段目の書き戻しは指紋を照合しなおせる(lane):
    path = lane.queue()
    approve(path)
    qf = queuefile.parse(path)
    qf.verified = True
    reason, material = hatena_entry.select_problem(qf, lane.world.account(ACCOUNT) | {"repo_dir": lane.repo},
                                                  ACCOUNT)
    assert reason is None and material["approved_sha"] == qf.front_matter["approved_sha"]


def test_SNSの書き戻しは今までと1バイトも同じ():
    prepared = {"approved_sha": "a" * 64, "goal": None}
    assert list(approval.approved_fields(prepared, "x", "t")) == [
        "status", "approved_sha", "approved_at", "approved_by", "revoked_at", "revoked_by", "revoked_reason",
        "approved_goal"]


def test_はてなの書き戻しはdeliveryが無ければ断る():
    with pytest.raises(ValueError):
        approval.approved_fields({"approved_sha": "a" * 64, "kind": "hatena_entry"}, "x", "t")


def test_取り消した予約を承認し直すと同じ配信に行を足す(lane):
    from thth import writeback
    path = lane.queue()
    approve(path)
    delivery = fm_of(path)["delivery"]
    # cancel_schedule と同じ書き戻し（draft に戻す・delivery は残る）
    writeback.set_front_matter_fields(path, {"status": "draft", "approved_sha": None, "approved_at": None,
                                             "approved_by": None})
    lane.commit("docs/sns/queue/a.md")
    approve(path, by="second")
    fm = fm_of(path)
    assert fm["delivery"] == delivery
    record = deliveries.read(ACCOUNT, delivery)
    assert record["seq"] == 2 and [r["state"] for r in record["history"]] == ["wanted", "wanted"]
    assert record["history"][1]["approved_by"] == "second"
    assert deliveries.list_ids(ACCOUNT) == [delivery]


def test_confirm_fileでもはてなの台帳を作る(lane, tmp_path):
    path = lane.queue()
    digest, _ = first_stage_digest(path)
    listing = tmp_path / "confirm.txt"
    listing.write_text(f"{path} {digest}\n", encoding="utf-8")
    rc, out, err = call(["approve", "--confirm-file", str(listing), "--by", "tester"])
    assert rc == 0, (out, err)
    fm = fm_of(path)
    assert fm["approved_kind"] == "hatena_entry"
    assert deliveries.read(ACCOUNT, fm["delivery"])["state"] == "wanted"


def test_preview_は変換したあとの本文(lane):
    path = lane.queue()
    assert lint.preview_file(path).startswith("# アラビカ\n\n値動きのまとめ。\n")
