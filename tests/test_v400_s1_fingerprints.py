"""4.0 S1 §6.1・§7.2・§10 T-4a〜d: はてなの承認の指紋 2 種と、v1・束の指紋を変えないこと。

T-4a・T-4d は `thth/approval.py` の実装を経由せずに期待値を組み立てて突き合わせる
（`tests/test_approval.py` の流儀・実装をなぞる tautology にしない）。
"""
from __future__ import annotations

import hashlib

import pytest

from thth import approval, fingerprint_golden

_SEP = "\x1f"
_SEG = "\x1e"

ENTRY = {
    "account": "kopicha-hatenablog", "blog_id": "kopicha.hatenablog.com", "op": "create",
    "entry_id": None, "base_readback_sha": None, "title": "アラビカの値動き（2026 年 10 月）",
    "categories": ["価格", "コーヒー"], "body": "本文です。\n\n![豆](images/arabica.jpg)\n",
    "images": [{"path": "images/arabica.jpg", "source_sha256": "7e" * 32}],
    "article": "kopicha/2026-10-arabica", "form": "full",
    "publish_at": "2026-10-20T08:00:00+09:00",
}
UPDATE = dict(ENTRY, op="update", entry_id="tag:blog.hatena.ne.jp,2013:blog-x-1-2",
              base_readback_sha="e5" * 32)
BOOKMARK = {
    "account": "kopicha-hatenablog", "principal": "hatena-masaru", "op": "add",
    "url": "https://kopicha.hatenablog.com/entry/2026/10/20/080000",
    "delivery": "dl_3f9c2a7e1b4d", "comment": "運営者です。今月の値動きをまとめました。",
    "tags": ["価格", "コーヒー"],
}


def _sha(parts):
    return hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ T-4a

def test_T4a_entryの指紋は14項目をこの順でつないだsha256():
    body = "本文です。\n\n![豆](images/arabica.jpg)"
    expected = _sha([
        "thth-hatena-entry-1", "kopicha-hatenablog", "kopicha.hatenablog.com", "create", "", "",
        "アラビカの値動き（2026 年 10 月）", "コーヒー" + _SEG + "価格",
        hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "images/arabica.jpg=" + "7e" * 32,
        "kopicha/2026-10-arabica", "full", "2026-10-20T08:00:00+09:00", "no",
    ])
    actual = approval.compute_hatena_entry_sha(**ENTRY)
    assert actual == expected
    assert len(actual) == 64


def test_T4a_entryの指紋の既知の値():
    # 64 桁を固定する（上の組み立てが実装と同時に変わっても、ここで止まる）。
    assert approval.compute_hatena_entry_sha(**ENTRY) == (
        "cba1a4d31c494712f8e9778148256ee875619391256507fd86965c9c6269b2ba")


def test_T4a_並べ替えと大小文字と空白では変わらない():
    base = approval.compute_hatena_entry_sha(**ENTRY)
    same = [
        dict(ENTRY, categories=["コーヒー", " 価格 ", ""]),
        dict(ENTRY, blog_id="  Kopicha.HatenaBlog.com "),
        dict(ENTRY, title="  アラビカの値動き（2026  年 10 月） "),
        dict(ENTRY, body="本文です。  \r\n\r\n![豆](images/arabica.jpg)\n\n\n"),
    ]
    for case in same:
        assert approval.compute_hatena_entry_sha(**case) == base, case


@pytest.mark.parametrize("change", [
    {"body": "本文です！\n\n![豆](images/arabica.jpg)"},
    {"images": [{"path": "images/arabica.jpg", "source_sha256": "7f" * 32}]},
    {"images": []},
    {"title": "別の題"},
    {"publish_at": "2026-10-20T09:00:00+09:00"},
    {"blog_id": "asmon-kanto.hatenablog.com"},
    {"account": "asmon-kanto-hatenablog"},
    {"categories": ["価格"]},
    {"article": "kopicha/2026-11-arabica"},
    {"form": "summary"},
])
def test_T4a_項目が1つ変わると指紋が変わる(change):
    assert approval.compute_hatena_entry_sha(**dict(ENTRY, **change)) != \
        approval.compute_hatena_entry_sha(**ENTRY)


def test_T4a_opとentry_idとbase_readback_shaで変わる():
    update = approval.compute_hatena_entry_sha(**UPDATE)
    assert update != approval.compute_hatena_entry_sha(**ENTRY)
    assert approval.compute_hatena_entry_sha(**dict(UPDATE, op="delete")) != update
    assert approval.compute_hatena_entry_sha(**dict(UPDATE, entry_id="tag:other")) != update
    assert approval.compute_hatena_entry_sha(**dict(UPDATE, base_readback_sha="00" * 32)) != update


@pytest.mark.parametrize("change,code", [
    ({"op": "publish"}, "invalid_op"),
    ({"form": "teaser"}, "invalid_form"),
    ({"entry_id": "tag:x"}, "entry_id_for_create"),
    ({"op": "update"}, "entry_id_required"),
    ({"blog_id": " "}, "blog_id_required"),
    ({"title": "題\x1f細工"}, "title_has_separator"),
    ({"categories": ["a\x1eb"]}, "categories_has_separator"),
    ({"categories": "コーヒー"}, "invalid_categories"),
    ({"images": [{"path": "a.jpg", "source_sha256": "1"}, {"path": "a.jpg", "source_sha256": "2"}]},
     "invalid_images"),
])
def test_T4a_入力が外れていれば断る(change, code):
    with pytest.raises(ValueError, match=code):
        approval.compute_hatena_entry_sha(**dict(ENTRY, **change))


def test_T4a_v1と束の指紋とは同じ材料でも一致しない():
    hatena = approval.compute_hatena_entry_sha(**ENTRY)
    v1 = approval.compute_approved_sha(section=ENTRY["body"], account=ENTRY["account"],
                                        reply_to=None, topic=None, publish_at=ENTRY["publish_at"])
    assert hatena != v1


# ------------------------------------------------------------------ T-4b

V1_GOLDEN = {
    "threads_plain": "16d01580fbaba0f2",
    "threads_topic": "971c85566f4b8a98",
    "bluesky_topic_tag": "3ca0242e299ff878",
    "bluesky_existing_tag": "ce218739f9998d82",
    "mastodon_hashtags_off": "c6c08e89e9308d5c",
    "mastodon_topic_tag": "5cf0735fecbe962c",
    "reply_to_post_id": "21f78f893623c5e0",
    "reply_to_file": "e4d307a73438a5f1",
    "threads_options": "2150188e6b2af272",
    "x_community": "d305d1c3e5cab9cc",
    "bundle_bluesky": "e1e13b8e4197f403",
}


def test_T4b_v1と束の指紋は1バイトも変わらない():
    assert approval.FINGERPRINT_VERSION == 2
    assert max(approval.FINGERPRINT_HISTORY) == 2
    # golden の行そのものも 4.0 S1 の前と同じ（golden を書き換えて drift を黙らせない）。4.0 S4 §9.3 で
    # 任意項目 `announces` の見本（`threads_announces`）を**足した**——既存の行は 1 つも変えていない。
    added = {"threads_announces"}
    assert {k: v for k, v in fingerprint_golden.GOLDEN[2].items() if k not in added} == V1_GOLDEN
    assert {k: v for k, v in fingerprint_golden.current().items() if k not in added} == V1_GOLDEN
    assert set(fingerprint_golden.GOLDEN[2]) - set(V1_GOLDEN) == added
    assert fingerprint_golden.drift() == []


def test_T4b_はてなのgoldenは版1にあり今の計算と一致する():
    assert approval.HATENA_ENTRY_VERSION == "thth-hatena-entry-1"
    assert approval.HATENA_BOOKMARK_VERSION == "thth-hatena-bookmark-1"
    for kind in ("hatena_entry", "hatena_bookmark"):
        assert fingerprint_golden.hatena_version(kind) == 1
        assert set(fingerprint_golden.GOLDEN[kind]) == {1}
        assert fingerprint_golden.hatena_current(kind) == fingerprint_golden.GOLDEN[kind][1]
    assert fingerprint_golden.hatena_drift() == []


def test_T4b_はてなの計算が変わるとdriftが名指しする(monkeypatch):
    original = approval.normalize_hatena_body
    monkeypatch.setattr(approval, "normalize_hatena_body", lambda text: original(text).strip() + "!")
    drift = fingerprint_golden.drift()
    assert "hatena_entry:entry_create" in drift
    assert not any(name.startswith("hatena_bookmark") for name in drift)
    assert "threads_plain" not in drift


def test_T4b_はてなの版の語を上げてgoldenを足さなければ落ちる(monkeypatch):
    monkeypatch.setattr(approval, "HATENA_ENTRY_VERSION", "thth-hatena-entry-2")
    assert fingerprint_golden.drift() == ["hatena_entry:no_golden_for_version"]


# ------------------------------------------------------------------ T-4c

def test_T4c_SNSの承認の書き戻しにapproved_kindもdeliveryも書かない():
    out = approval.approved_fields({"approved_sha": "a" * 64, "goal": None},
                                   "masaru", "2026-10-16T09:00:00+09:00")
    assert "approved_kind" not in out
    assert "delivery" not in out
    assert set(out) == {"status", "approved_sha", "approved_at", "approved_by", "revoked_at",
                        "revoked_by", "revoked_reason", "approved_goal"}


# ------------------------------------------------------------------ T-4d

def test_T4d_bookmarkの指紋は8項目をこの順でつないだsha256():
    expected = _sha([
        "thth-hatena-bookmark-1", "kopicha-hatenablog", "hatena-masaru", "add",
        "https://kopicha.hatenablog.com/entry/2026/10/20/080000", "dl_3f9c2a7e1b4d",
        "運営者です。今月の値動きをまとめました。", "コーヒー" + _SEG + "価格",
    ])
    assert approval.compute_hatena_bookmark_sha(**BOOKMARK) == expected


def test_T4d_bookmarkの指紋の既知の値():
    assert approval.compute_hatena_bookmark_sha(**BOOKMARK) == (
        "3604fb303bcc70592d43bc44563f8d39e38bfdc0f4912404bda44f6d20fd6c3f")


def test_T4d_改行と並べ替えでは変わらず_項目が変われば変わる():
    base = approval.compute_hatena_bookmark_sha(**BOOKMARK)
    assert approval.compute_hatena_bookmark_sha(**dict(
        BOOKMARK, comment="運営者です。\n今月の値動きをまとめました。".replace("\n", ""),
        tags=[" コーヒー", "価格", ""])) == base
    with_newline = approval.compute_hatena_bookmark_sha(**dict(BOOKMARK, comment="運営者です。\n今月"))
    assert with_newline == approval.compute_hatena_bookmark_sha(**dict(BOOKMARK, comment="運営者です。 今月"))
    for change in ({"op": "delete"}, {"url": BOOKMARK["url"] + "/"}, {"delivery": "dl_000000000000"},
                   {"comment": "別の文"}, {"tags": ["価格"]}, {"principal": "other"},
                   {"account": "asmon-kanto-hatenablog"}):
        assert approval.compute_hatena_bookmark_sha(**dict(BOOKMARK, **change)) != base, change


@pytest.mark.parametrize("change,code", [
    ({"op": "remove"}, "invalid_op"),
    ({"delivery": "dl_XYZ"}, "invalid_delivery"),
    ({"url": "  "}, "url_required"),
    ({"comment": "あ" * 101}, "comment_too_long"),
    ({"comment": " \n "}, "comment_required"),
    ({"tags": [str(i) for i in range(11)]}, "too_many_tags"),
])
def test_T4d_入力が外れていれば断る(change, code):
    with pytest.raises(ValueError, match=code):
        approval.compute_hatena_bookmark_sha(**dict(BOOKMARK, **change))


def test_T4d_コメントは100字ちょうどまで通る():
    approval.compute_hatena_bookmark_sha(**dict(BOOKMARK, comment="あ" * 100))
