"""実機の確かめ 10/17（S5）の 11〜13 の直し。S5 より前に出した配信（台帳に題とカテゴリが無い）を扱う。

HTTP は偽物（`FakeBlog`）。はてなには出ない。
"""
from __future__ import annotations

import json
import os

from tests.hatena_s1_flow_helpers import first_stage_digest
from tests.hatena_s5_helpers import (ACCOUNT, NOW, FakeBlog, approve, approved, fm_of, lane,  # noqa: F401
                                     throw, world)
from thth import accounts, article_cli, deliveries, hatena_entry, lint, queuefile

# 先頭の `# 題` が原稿の題と同じ（変換で外れる）記事。
ARTICLE = "# アラビカ\n\n値動きのまとめ。\n\n![豆](../images/bean.png)\n\n終わり。\n"


def _lint_lines(path):
    return [line for line in lint.lint_file(path) if not lint.is_warning(line)]


def _old_ledger(lane):
    """題が H1 と同じ記事を出し、台帳から S5 で足した欄を消す（S5 より前に出した配信の形）。"""
    lane.write("articles/a.md", ARTICLE)
    path, delivery = approved(lane, title="アラビカ")
    blog = FakeBlog()
    assert throw(blog).action == "post"
    raw = deliveries.path_for(ACCOUNT, delivery)
    with open(raw, encoding="utf-8") as f:
        record = json.load(f)
    for key in ("title", "categories", "readback_title", "readback_categories"):
        record.pop(key, None)
    with open(raw, "w", encoding="utf-8") as f:
        json.dump(record, f)
    return path, delivery, blog


# ---------------------------------------------------------------- 11

def test_11_台帳に題の無い配信でも何も変えない更新は断る(lane):
    """柵: 題とカテゴリの比べ先を台帳だけに戻すと赤。"""
    _path, delivery, _blog = _old_ledger(lane)
    upath = lane.queue("u.md", op="update", delivery=delivery, title="アラビカ")
    assert _lint_lines(upath) == [hatena_entry.message("update_no_change")]


def test_11_前に送った原稿と題が違えば通す(lane):
    _path, delivery, _blog = _old_ledger(lane)
    upath = lane.queue("u.md", op="update", delivery=delivery, title="アラビカ（改）")
    assert _lint_lines(upath) == []


def test_11_前の原稿が読めなければ題は比べず_本文と版と画像で断る(lane):
    path, delivery, _blog = _old_ledger(lane)
    os.remove(path)
    assert hatena_entry.previous_manuscript(accounts.load_account(ACCOUNT), deliveries.read(ACCOUNT, delivery)) is None
    # 題は比べない（分からない）。本文（題の行を外した変換のあと）・版・画像が同じなので断る
    upath = lane.queue("u.md", op="update", delivery=delivery, title="アラビカ")
    assert _lint_lines(upath) == [hatena_entry.message("update_no_change")]


def test_11_承認の控えが無ければ記事の版で比べる(lane):
    """柵: 版の比べを消すと赤（控えが無いと何も比べずに通していた）。"""
    _path, delivery, _blog = _old_ledger(lane)
    record = deliveries.read(ACCOUNT, delivery)
    sha = record["history"][-1]["approved_sha"]
    os.remove(os.path.join(deliveries.dir_for(ACCOUNT), f"{delivery}.{sha[:12]}.body.md"))
    upath = lane.queue("u.md", op="update", delivery=delivery, title="アラビカ")
    assert _lint_lines(upath) == [hatena_entry.message("update_no_change")]
    lane.write("articles/a.md", ARTICLE + "\n追記。\n")
    assert _lint_lines(upath) == []


# ---------------------------------------------------------------- 12

def test_12_article_diffは両方を同じ変換に通す_題の行の偽の差分が出ない(lane):
    """柵: 今の版の変換に題を渡さないと赤（`+# アラビカ` が出る）。"""
    _path, delivery, _blog = _old_ledger(lane)
    lane.write("articles/a.md", ARTICLE.replace("値動きのまとめ。", "値動きのまとめ（直した）。"))
    row = article_cli.build(ACCOUNT, now=NOW)["deliveries"][0]
    assert "+# アラビカ" not in row["diff_lines"] and "-# アラビカ" not in row["diff_lines"]
    changed = [line for line in row["diff_lines"] if line[:1] in "+-" and line[:3] not in ("+++", "---")]
    assert changed == ["-値動きのまとめ。", "+値動きのまとめ（直した）。"]


def test_12_前の本文がsentのときは画像の行も置き換えて比べる(lane):
    _path, delivery, _blog = _old_ledger(lane)
    record = deliveries.read(ACCOUNT, delivery)
    sha = record["history"][-1]["approved_sha"]
    os.remove(os.path.join(deliveries.dir_for(ACCOUNT), f"{delivery}.{sha[:12]}.body.md"))
    lane.write("articles/a.md", ARTICLE.replace("終わり。", "終わり（直した）。"))
    row = article_cli.build(ACCOUNT, now=NOW)["deliveries"][0]
    assert row["diff_from"] == "sent"
    changed = [line for line in row["diff_lines"] if line[:1] in "+-" and line[:3] not in ("+++", "---")]
    assert changed == ["-終わり。", "+終わり（直した）。"]


# ---------------------------------------------------------------- 13

def test_13_draftの題とカテゴリは前に送った原稿から引く_保存して承認できる(lane):
    """柵: 前の原稿から引かないと赤（title が空で title_required）。"""
    import datetime
    _path, delivery, blog = _old_ledger(lane)
    lane.write("articles/a.md", ARTICLE + "\n追記。\n")
    row = article_cli.build(ACCOUNT, draft=True, now=NOW - datetime.timedelta(minutes=20))["deliveries"][0]
    fm = queuefile.parse_text(row["draft_text"], "x.md").front_matter
    assert fm["title"] == "アラビカ" and hatena_entry.parse_categories(fm["categories"]) == ["コーヒー", "価格"]
    upath = lane.write("docs/sns/queue/u.md", row["draft_text"])
    assert _lint_lines(upath) == []
    approve(upath)
    assert throw(blog).action == "post"


def test_13_前の原稿も無ければ題を書いてくださいの注記(lane):
    path, delivery, _blog = _old_ledger(lane)
    os.remove(path)
    lane.write("articles/a.md", ARTICLE + "\n追記。\n")
    row = article_cli.build(ACCOUNT, draft=True, now=NOW)["deliveries"][0]
    assert any("title: に題を書いてください" in note for note in row["notes"])
    assert accounts.load_account(ACCOUNT)  # 口座は読める
