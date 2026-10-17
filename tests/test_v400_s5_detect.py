"""4.0 S5 の試験（1）: 台帳の題とカテゴリ・戻し先・正本の修正の検出・差分の承認・指紋（T-S5-1・2・3・10）。

HTTP は打たない（偽物の adapter `FakeBlog`）。repo は `tmp_path` の本物の git。はてなには出ない。
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from tests.conftest import run_git
from tests.hatena_s1_flow_helpers import IMAGE_BYTES, call, first_stage_digest
from tests.hatena_s5_helpers import (ACCOUNT, NOW, FakeBlog, approve, approved, fm_of, lane, published,  # noqa: F401
                                     states, throw, world)
from thth import (accounts, approval, article_cli, deliveries, deliveries_cli, fingerprint_golden, hatena_entry,
                  jst, lint, sent)


def _lint_lines(path):
    return [line for line in lint.lint_file(path) if not lint.is_warning(line)]


def _cfg():
    return accounts.load_account(ACCOUNT)


def _record(delivery):
    return deliveries.read(ACCOUNT, delivery)


# ---------------------------------------------------------------- T-S5-1a 題とカテゴリを台帳に

def test_T_S5_1a_題とカテゴリはwantedとpublishedで台帳に入り_deleteの行には無い(lane):
    path = lane.queue()
    approve(path)
    delivery = fm_of(path)["delivery"]
    record = _record(delivery)
    assert record["state"] == "wanted"
    assert (record["title"], record["categories"]) == ("アラビカの値動き", ["コーヒー", "価格"])
    blog = FakeBlog()
    assert throw(blog).action == "post"
    record = _record(delivery)
    assert record["state"] == "published" and record["title"] == "アラビカの値動き"
    assert record["categories"] == ["コーヒー", "価格"]
    assert record["readback_title"] == "アラビカの値動き"
    # 更新で題とカテゴリが変わる
    lane.write("articles/a.md", "# アラビカ\n\n直した本文。\n")
    approved(lane, "u.md", op="update", delivery=delivery, title="アラビカの値動き（改）",
             categories="[コーヒー, 相場]")
    assert throw(blog).action == "post"
    record = _record(delivery)
    assert (record["title"], record["categories"]) == ("アラビカの値動き（改）", ["コーヒー", "相場"])
    # 削除の行には題とカテゴリが無い（台帳の欄は最後に出した値のまま）
    approved(lane, "d.md", op="delete", delivery=delivery)
    assert throw(blog).action == "post"
    record = _record(delivery)
    assert record["state"] == "deleted"
    assert "title" not in record["history"][-1] and "categories" not in record["history"][-1]
    assert record["title"] == "アラビカの値動き（改）"


def test_T_S5_1a_S5より前の台帳も読め_1段目は記録なしと言う(lane):
    _path, delivery, blog = published(lane)
    raw_path = deliveries.path_for(ACCOUNT, delivery)
    with open(raw_path, encoding="utf-8") as f:
        record = json.load(f)
    for key in ("title", "categories", "readback_title", "readback_categories"):
        record.pop(key, None)
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(record, f)
    assert "title" not in deliveries.read(ACCOUNT, delivery)
    lane.write("articles/a.md", "# アラビカ\n\n直した本文。\n")
    path = lane.queue("u.md", op="update", delivery=delivery)
    _digest, out = first_stage_digest(path)
    assert "題: アラビカの値動き（台帳に記録なし・比べません）" in out
    assert "カテゴリ: コーヒー・価格（台帳に記録なし・比べません）" in out


# ---------------------------------------------------------------- T-S5-1b 送る前の状態

@pytest.mark.parametrize("states_, expected", [
    (["published", "conflict", "inflight", "unknown"], "conflict"),
    (["wanted", "inflight", "published", "inflight"], "published"),
    (["wanted", "inflight"], "wanted"),
    (["wanted", "inflight", "published", "no_follow", "inflight", "unknown"], "no_follow"),
])
def test_T_S5_1b_state_before_inflightは台帳のinflightの前の状態(states_, expected):
    """柵: inflight・unknown を飛ばさない（最後の行の状態をそのまま返す）と赤。"""
    record = {"history": [{"seq": i + 1, "state": s} for i, s in enumerate(states_)]}
    assert deliveries.state_before_inflight(record) == expected


# ---------------------------------------------------------------- T-S5-2a 出した版と HEAD の版

def test_T_S5_2a_記事を直してcommitすると_body(lane):
    _path, delivery, _blog = published(lane)
    assert hatena_entry.source_status(_record(delivery), _cfg())["behind"] is False
    lane.write("articles/a.md", "# アラビカ\n\n直した本文。\n\n![豆](../images/bean.png)\n")
    s = hatena_entry.source_status(_record(delivery), _cfg())
    assert s["behind"] is True and s["reasons"] == ["body"]
    assert s["checked_rev"] == _record(delivery)["checked_rev"] and s["current_rev"] != s["checked_rev"]
    assert s["kind"] == "git" and s["head_commit"]


def test_T_S5_2a_画像だけ差し替えてcommitすると_images_changed_1(lane):
    _path, delivery, _blog = published(lane)
    lane.write("images/bean.png", IMAGE_BYTES + b" v2")
    s = hatena_entry.source_status(_record(delivery), _cfg())
    assert s["behind"] is True and s["reasons"] == ["images"]
    assert s["images"] == {"same": 0, "changed": 1, "missing": 0}
    assert s["image_paths"]["changed"] == ["images/bean.png"]


def test_T_S5_2a_作業ツリーだけ直してcommitしなければ同じ(lane):
    """柵: 作業ツリーで比べると赤（HEAD の blob で比べる）。"""
    _path, delivery, _blog = published(lane)
    lane.write("articles/a.md", "# アラビカ\n\n未 commit の直し。\n", commit=False)
    lane.write("images/bean.png", IMAGE_BYTES + b" wip", commit=False)
    s = hatena_entry.source_status(_record(delivery), _cfg())
    assert s["behind"] is False and s["reasons"] == []
    assert s["images"] == {"same": 1, "changed": 0, "missing": 0}


# ---------------------------------------------------------------- T-S5-2b 対象と言えないとき

def test_T_S5_2b_no_followとdeletedとchecked_revの無い台帳(lane):
    _path, delivery, _blog = published(lane)
    cfg = _cfg()
    record = _record(delivery)
    assert hatena_entry.source_status(dict(record, state="no_follow"), cfg)["reasons"] == ["no_follow"]
    assert hatena_entry.source_status(dict(record, state="no_follow"), cfg)["behind"] is None
    assert hatena_entry.source_status(dict(record, state="deleted"), cfg) is None
    assert hatena_entry.source_status(dict(record, state="wanted"), cfg) is None
    old = {k: v for k, v in record.items() if k != "checked_rev"}
    s = hatena_entry.source_status(old, cfg)
    assert s["behind"] is None and s["reasons"] == ["no_checked_rev"]


def test_T_S5_2b_repoが読めなければerror_cannot_sayにsource_unreadable_他は出す(lane):
    _path, delivery, _blog = published(lane)
    other = deliveries.create(ACCOUNT, principal="hatena-main", blog_id="a.hatenablog.com", article="lane-a/x",
                              at=jst.iso(NOW), state="wanted")["delivery"]
    data = lane.world.account(ACCOUNT)
    data["repo_dir"] = str(lane.world.root / "repos" / "gone")
    lane.world.write_account(data)
    s = hatena_entry.source_status(_record(delivery), _cfg())
    assert s["behind"] is None and s["error"] == "repo_unreadable"
    payload = deliveries_cli.build(ACCOUNT)
    assert "source_unreadable" in payload["cannot_say"]
    assert {r["delivery"] for r in payload["deliveries"]} == {delivery, other}


# ---------------------------------------------------------------- T-S5-2c thth article diff

def _tree_snapshot(root):
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = hashlib.sha256(f.read()).hexdigest()
    return out


def _sns_account(lane, name="lane-a-threads"):
    from tests.hatena_s1_helpers import account_data
    data = account_data(name, project="lane-a", principal="hatena-main", blog_id="a.hatenablog.com",
                        token=lane.world.tmp / "t.token", handle="who")
    data["media"] = "threads"
    data.pop("principal", None)
    lane.world.write_account(data)
    return name


def test_T_S5_2c_直した配信の差分と画像の内訳と告知と_draftの文面_ファイルは書かない(lane):
    """柵: `--draft` でファイルを書くと赤（repo と状態の置き場が 1 バイトも変わらない）。"""
    _path, delivery, _blog = published(lane)
    sns = _sns_account(lane)
    sent.write(lane.world.state(sns), post_id="1789", text="告知", body_hash="h", sent_at=jst.iso(NOW),
               announces=delivery, article="lane-a/arabica")
    lane.write("articles/a.md", "# アラビカ\n\n値動きのまとめ（直した）。\n\n![豆](../images/bean.png)\n\n終わり。\n")
    lane.write("images/bean.png", IMAGE_BYTES + b" v2")
    before_repo = _tree_snapshot(lane.repo)
    before_state = _tree_snapshot(lane.state)
    rc, out, err = call(["article", "diff", ACCOUNT, "--json", "--draft"])
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["schema"] == "thth.article_diff/1"
    assert payload["counts"] == {"behind": 1, "same": 0, "no_follow": 0, "cannot_say": 0}
    (row,) = payload["deliveries"]
    assert row["delivery"] == delivery and row["source"]["reasons"] == ["body", "images"]
    assert "-値動きのまとめ。" in row["diff_lines"] and "+値動きのまとめ（直した）。" in row["diff_lines"]
    assert row["diff_from"] == "approved"
    assert row["images"] == {"same": [], "changed": ["images/bean.png"], "added": [], "removed": []}
    assert [(t["account"], t["post_id"]) for t in row["announcements"]] == [(sns, "1789")]
    draft = row["draft_text"].split("\n")
    assert f"delivery: {delivery}" in draft and "article_source: articles/a.md" in draft
    assert "title: アラビカの値動き" in draft and "categories: [コーヒー, 価格]" in draft
    assert "status: draft" in draft and "op: update" in draft and f"account: {ACCOUNT}" in draft
    assert row["draft_name"].startswith("docs/sns/queue/") and row["draft_name"].endswith("-arabica-update.md")
    # 人向けの表示
    rc, out, err = call(["article", "diff", ACCOUNT, "--draft"])
    assert rc == 0 and "記事の修正  lane-a-hatenablog  直した 1 件" in out
    assert "→ 書き換えません（訂正を出すなら返信の原稿で）" in out
    assert "画像: 変わらず 0・差し替え 1（images/bean.png）・追加 0・外した 0" in out
    # 何も書いていない
    assert _tree_snapshot(lane.repo) == before_repo
    assert _tree_snapshot(lane.state) == before_state
    status = run_git(lane.repo, ["status", "--porcelain"])
    assert (status.stdout if hasattr(status, "stdout") else status).strip() == ""


def test_T_S5_2c_draftの文面はそのまま保存して承認できる(lane):
    _path, delivery, blog = published(lane)
    lane.write("articles/a.md", "# アラビカ\n\n値動きのまとめ（直した）。\n\n![豆](../images/bean.png)\n\n終わり。\n")
    import datetime
    # publish_at は今＋10 分。timer の時刻（NOW）に間に合うよう 20 分前に文面を出したことにする。
    payload = article_cli.build(ACCOUNT, draft=True, now=NOW - datetime.timedelta(minutes=20))
    text = payload["deliveries"][0]["draft_text"]
    assert "publish_at: 2026-09-09T09:50:00+09:00" in text
    path = lane.write("docs/sns/queue/u.md", text)
    assert _lint_lines(path) == []
    approve(path)
    assert throw(blog).action == "post"
    assert states(delivery)[-1] == "published"
    assert article_cli.build(ACCOUNT, now=NOW)["counts"]["behind"] == 0


# ---------------------------------------------------------------- T-S5-2d thth deliveries

def test_T_S5_2d_deliveriesの1行に正本の比べ_JSONにsource_adapterは呼ばない(lane):
    """柵: deliveries に GET を足すと赤（既定の柵 `no_real_hatena_adapter` が落ちる）。"""
    _path, delivery, _blog = published(lane)
    rc, out, err = call(["deliveries", ACCOUNT])
    assert rc == 0 and "正本: 同じ" in out
    lane.write("articles/a.md", "# アラビカ\n\n直した本文。\n")
    rc, out, err = call(["deliveries", ACCOUNT])
    record = _record(delivery)
    assert f"正本: 直した（{record['checked_rev'][:12]}…" in out
    rc, out, err = call(["deliveries", ACCOUNT, "--json"])
    (row,) = json.loads(out)["deliveries"]
    # 記事から画像の参照を外しただけで、画像ファイルは HEAD に同じまま（画像の比べは台帳の images[] と HEAD）
    assert row["source"]["behind"] is True and row["source"]["reasons"] == ["body"]


# ---------------------------------------------------------------- T-S5-3a update の 1 段目

def test_T_S5_3a_1段目に題とカテゴリと画像と正本の版(lane):
    _path, delivery, _blog = published(lane)
    lane.write("images/leaf.png", IMAGE_BYTES + b" leaf")
    lane.write("images/bean.png", IMAGE_BYTES + b" v2")
    lane.write("articles/a.md", "# アラビカ\n\n値動き。\n\n![豆](../images/bean.png)\n\n![葉](../images/leaf.png)\n")
    path = lane.queue("u.md", op="update", delivery=delivery, title="アラビカ（改）", categories="[コーヒー, 相場]")
    _digest, out = first_stage_digest(path)
    record = _record(delivery)
    assert f"正本の版: 出した {record['checked_rev'][:12]}…" in out
    assert "題: アラビカの値動き → アラビカ（改）" in out
    assert "カテゴリ: コーヒー・相場（足した: 相場／外した: 価格）" in out
    assert "画像の変化: 変わらず 0・差し替え 1（images/bean.png）・追加 1（images/leaf.png）・外した 0" in out
    assert "---- 本文の差分（前に送った本文 → 今回） ----" in out


def test_T_S5_3a_前の本文は取り込みの控え_承認の控え_sentの順(lane):
    """柵: 順を変えると赤。"""
    _path, delivery, _blog = published(lane)
    record = _record(delivery)
    adopted = hatena_entry.keep_adopted_body(ACCOUNT, delivery, record["readback_sha"], "取り込んだ本文")
    assert hatena_entry.previous_body(ACCOUNT, record)[:2] == ("取り込んだ本文", "adopted")
    os.remove(adopted)
    before, origin, _note = hatena_entry.previous_body(ACCOUNT, record)
    assert origin == "approved" and "値動きのまとめ。" in before
    sha = record["history"][-1]["approved_sha"]
    os.remove(os.path.join(deliveries.dir_for(ACCOUNT), f"{delivery}.{sha[:12]}.body.md"))
    before, origin, note = hatena_entry.previous_body(ACCOUNT, record, has_images=True)
    assert origin == "sent" and "f:id:" in before and "承認の控えが無いので" in note
    sent_dir = os.path.join(lane.state, "sent")
    for name in os.listdir(sent_dir):
        os.remove(os.path.join(sent_dir, name))
    assert hatena_entry.previous_body(ACCOUNT, record) == (None, None, None)


# ---------------------------------------------------------------- T-S5-3b 何も変わっていない更新

def test_T_S5_3b_何も変わっていない更新はlintと1段目とtimerで断る(lane):
    """柵: 3 か所のどれかを消すと赤（timer は approval_stale に丸めない）。"""
    _path, delivery, blog = published(lane)
    path = lane.queue("u.md", op="update", delivery=delivery)
    assert _lint_lines(path) == [hatena_entry.message("update_no_change")]
    assert lint.reason_code(lint.lint_file(path)) == "update_no_change"
    rc, out, err = call(["approve", path])
    assert rc != 0 and "update_no_change: 出した版と同じです" in out + err
    # 直して承認したあとで記事を元に戻す → timer は update_no_change（PUT しない）
    original = open(os.path.join(lane.repo, "articles/a.md"), encoding="utf-8").read()
    lane.write("articles/a.md", original + "\n追記。\n")
    approve(path)
    lane.write("articles/a.md", original)
    blog.calls.clear()
    result = throw(blog)
    assert {"file": "u.md", "reason": "update_no_change"} in result.rejections
    assert blog.calls == []
    assert states(delivery)[-1] == "published"


@pytest.mark.parametrize("what", ["title", "categories", "image"])
def test_T_S5_3b_題かカテゴリか画像が変われば通す(lane, what):
    _path, delivery, _blog = published(lane)
    kwargs = {}
    if what == "title":
        kwargs["title"] = "別の題"
    elif what == "categories":
        kwargs["categories"] = "[価格, コーヒー, 相場]"
    else:
        lane.write("images/bean.png", IMAGE_BYTES + b" v2")
    path = lane.queue("u.md", op="update", delivery=delivery, **kwargs)
    assert _lint_lines(path) == []
    approve(path)


def test_T_S5_3b_カテゴリの並びだけ変えても同じとみなす(lane):
    _path, delivery, _blog = published(lane)
    path = lane.queue("u.md", op="update", delivery=delivery, categories="[価格, コーヒー]")
    assert _lint_lines(path) == [hatena_entry.message("update_no_change")]


def test_T_S5_3b_承認の控えが無ければ記事の版で比べ_版も無ければ通す(lane):
    # 実機の確かめ 10/17 の 11: 控えが無くても本文（記事の版）・画像・題が同じなら断る。
    _path, delivery, _blog = published(lane)
    sha = _record(delivery)["history"][-1]["approved_sha"]
    os.remove(os.path.join(deliveries.dir_for(ACCOUNT), f"{delivery}.{sha[:12]}.body.md"))
    path = lane.queue("u.md", op="update", delivery=delivery)
    assert _lint_lines(path) == [hatena_entry.message("update_no_change")]
    record = json.loads(open(deliveries.path_for(ACCOUNT, delivery), encoding="utf-8").read())
    record.pop("checked_rev")
    with open(deliveries.path_for(ACCOUNT, delivery), "w", encoding="utf-8") as f:
        json.dump(record, f)
    assert _lint_lines(path) == []


# ---------------------------------------------------------------- T-S5-3c lint の文

def test_T_S5_3c_conflictの配信へのupdateはdelivery_state_conflict_無い台帳はdelivery_unknown(lane):
    _path, delivery, _blog = published(lane)
    deliveries.advance(ACCOUNT, delivery, state="conflict", at=jst.iso(NOW),
                       row={"op": "update", "reason": "readback_changed"})
    lane.write("articles/a.md", "# アラビカ\n\n直した。\n")
    path = lane.queue("u.md", op="update", delivery=delivery)
    lines = _lint_lines(path)
    assert [line.split(":", 1)[0] for line in lines] == ["delivery_state_conflict"]
    assert "thth conflict <口座>" in lines[0]
    other = lane.queue("x.md", op="update", delivery="dl_000000000000")
    assert [line.split(":", 1)[0] for line in _lint_lines(other)] == ["delivery_unknown"]


# ---------------------------------------------------------------- T-S5-10 承認の指紋は変えない

def test_T_S5_10_承認の指紋は1バイトも変わらない():
    """柵: 指紋に項目を足すと赤（golden と既知の 64 桁）。"""
    from tests.test_v400_s1_fingerprints import ENTRY
    assert fingerprint_golden.drift() == []
    assert fingerprint_golden.hatena_drift() == []
    assert approval.FINGERPRINT_VERSION == 2
    assert approval.HATENA_ENTRY_VERSION == "thth-hatena-entry-1"
    assert approval.compute_hatena_entry_sha(**ENTRY) == (
        "cba1a4d31c494712f8e9778148256ee875619391256507fd86965c9c6269b2ba")


def test_T_S5_10_更新の承認の指紋は14項目のまま_台帳の題とカテゴリは入らない(lane):
    _path, delivery, _blog = published(lane)
    lane.write("articles/a.md", "# アラビカ\n\n直した。\n")
    path, _ = approved(lane, "u.md", op="update", delivery=delivery)
    m = hatena_entry.material(fm_of(path), _cfg(), ACCOUNT)
    record = _record(delivery)
    expected = approval.compute_hatena_entry_sha(
        account=ACCOUNT, blog_id="a.hatenablog.com", op="update", entry_id=record["entry_id"],
        base_readback_sha=record["readback_sha"], title=m["title"], categories=m["categories"], body=m["body"],
        images=[], article="lane-a/arabica", form="full", publish_at=fm_of(path)["publish_at"])
    assert fm_of(path)["approved_sha"] == m["approved_sha"] == expected
    # 台帳の題を書き換えても指紋は変わらない（記録であって材料ではない）
    deliveries.advance(ACCOUNT, delivery, state="published", at=jst.iso(NOW), fields={"title": "別の題"},
                       row={"op": "check", "resolution": "readback_filled"})
    assert hatena_entry.material(fm_of(path), _cfg(), ACCOUNT)["approved_sha"] == expected


# ---------------------------------------------------------------- §3.4・§5.5 板と朝

def test_板と朝に正本を直した記事と衝突の数(lane):
    from thth import morning, report
    _path, delivery, _blog = published(lane)
    lane.write("articles/a.md", "# アラビカ\n\n直した。\n")
    row = next(r for r in report.board_summary()["accounts"] if r["account"] == ACCOUNT)
    assert row["deliveries_behind_count"] == 1 and row["deliveries_conflict_count"] == 0
    assert row["deliveries_behind"] == [{"delivery": delivery, "article": "lane-a/arabica"}]
    text = json.dumps(morning.build(ACCOUNT, now=NOW, mark=False, no_world=True), ensure_ascii=False)
    assert '"behind_items"' in text and '"kind": "article_behind"' in text
    assert f"thth article diff {ACCOUNT}" in text
    deliveries.advance(ACCOUNT, delivery, state="conflict", at=jst.iso(NOW),
                       row={"op": "check", "reason": "readback_changed", "detail": ["body"], "via": "check"})
    row = next(r for r in report.board_summary()["accounts"] if r["account"] == ACCOUNT)
    assert row["deliveries_conflict_count"] == 1
