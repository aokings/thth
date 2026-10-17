"""4.2（設計 S4 §22・kopicha #2950）: はてなブックマークのタグの長さ（31 バイト）の柵。

31 バイトを超えるタグを、`thth bookmark add`・`edit`・`batch` の 1 段目と `thth lint <札のファイル>` で
予告して断る（`bookmark_tag_too_long`・どのタグが何バイトか）。黙って切らない。台帳に観測の項目がある。
HTTP は偽物。はてなには出ない。
"""
from __future__ import annotations

import json

from tests.hatena_s1_flow_helpers import call, lane  # noqa: F401
from tests.hatena_s4_helpers import ACCOUNT, EXTERNAL, EXTERNAL_2, bm, bookmark, calls, fake_http, world  # noqa: F401
from tests.test_v420_bookmark_edit import bb  # noqa: F401
from thth import hatena_bookmark, lint, media_traits

TAG_31 = "あいうえおかきくけこ" + "a"          # 30 + 1 = 31 バイト
TAG_34 = "あいうえおかきくけこさ" + "a"        # 33 + 1 = 34 バイト


def test_境目は31バイト():
    assert len(TAG_31.encode()) == 31 and len(TAG_34.encode()) == 34
    assert hatena_bookmark.long_tags([TAG_31, "短い"]) == []
    assert hatena_bookmark.long_tags([TAG_31, TAG_34]) == [(TAG_34, 34)]


def test_addの1段目で予告して断り_どのタグが何バイトかを言う(bm):
    """柵: `check_tag_bytes` を外すと赤（34 バイトのタグが 1 段目を通る）。"""
    rc, out, err = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--tags",
                         f"{TAG_31},{TAG_34}", "--json"])
    body = json.loads(out)
    assert rc == 2 and body["error"] == "bookmark_tag_too_long"
    assert f"「{TAG_34}」34 バイト" in body["message"] and "31 バイト" in body["message"]
    assert TAG_31 not in body["message"].split("。")[0].replace(TAG_34, "")
    rc, out, _ = call(["bookmark", "add", ACCOUNT, "--url", EXTERNAL, "--comment", "c", "--tags", TAG_31, "--json"])
    assert rc == 1 and json.loads(out)["stage"] == 1
    assert calls(bm, "POST") == []


def test_editは直したあとのタグだけを見る(bm):
    bookmark(ACCOUNT, url=EXTERNAL, comment="c", tags=["旧名"])
    rc, out, _ = call(["bookmark", "edit", ACCOUNT, "--url", EXTERNAL, "--tags", TAG_34, "--json"])
    assert rc == 2 and json.loads(out)["error"] == "bookmark_tag_too_long"
    rc, out, _ = call(["bookmark", "edit", ACCOUNT, "--url", EXTERNAL, "--tags", TAG_31, "--json"])
    assert rc == 1 and json.loads(out)["stage"] == 1


def test_batchはその札だけ除き_理由と詳細を見せる(bb):
    path = bb.tmp / "day.jsonl"
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in [
        {"url": EXTERNAL, "comment": "c", "tags": [TAG_34]},
        {"url": EXTERNAL_2, "comment": "c", "tags": [TAG_31]}]) + "\n", encoding="utf-8")
    rc, out, _ = call(["bookmark", "batch", ACCOUNT, str(path), "--json"])
    items = json.loads(out)["preview"]["items"]
    assert [r["reason"] for r in items] == ["bookmark_tag_too_long", None]
    assert "34 バイト" in items[0]["detail"]
    rc, out, _ = call(["bookmark", "batch", ACCOUNT, str(path)])
    assert "除く: bookmark_tag_too_long" in out


def test_lintは札のファイルのタグの長さをerrorで言う(tmp_path):
    path = tmp_path / "day.jsonl"
    path.write_text("\n".join([json.dumps({"url": EXTERNAL, "comment": "c", "tags": f"{TAG_31},{TAG_34}"},
                                          ensure_ascii=False),
                               json.dumps({"url": EXTERNAL_2, "comment": "c", "tags": [TAG_31]}, ensure_ascii=False),
                               "{壊れ"]) + "\n", encoding="utf-8")
    problems = lint.lint_file(str(path))
    assert problems[0].startswith("bookmark_tag_too_long: 札 1（1 行目）") and "34 バイト" in problems[0]
    assert problems[1] == "invalid_json: 札 3（3 行目）" and len(problems) == 2
    assert lint.reason_code(problems) == "bookmark_tag_too_long"
    rc, out, err = call(["lint", str(path)])
    assert rc == 1 and "bookmark_tag_too_long" in out


def test_媒体の台帳に観測の項目がある():
    doc = media_traits.load("hatenabookmark")
    item = next(t for t in doc["traits"] if t["id"] == "hatenabookmark.tag_length_observed")
    assert item["kind"] == "observation" and item["confidence"] == "low"
    assert item["evidence"]["type"] == "observed" and item["evidence"]["observed_at"] == "2026-10-18"
    assert "kopicha" in item["evidence"]["record"]
