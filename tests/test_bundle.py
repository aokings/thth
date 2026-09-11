"""スレッド連投（`thth: 2`）の解析と検査（設計 §1・§7・工程 1）。

**v1 の解析と指紋を壊さないこと**が最初の条件（Codex 最終条件 5）。
"""
from __future__ import annotations

import pytest

from thth import bundle, queuefile

FM = """thth: 2
account: nigamilab-threads
publish_at: 2026-09-15T19:00:00+09:00
continue_until: 2026-09-15T20:00:00+09:00
status: draft
topic: コーヒー
form: 問い→答え
outlet: 記事へ
posts:
  - index: 1
  - index: 2
  - index: 3
"""

BODY = """# 経緯（THTH は読みません）

メモ。

## threads

コーヒーが苦いのは、カフェインのせいでしょうか。

<!-- thth: 2/3 -->

カフェインを抜いたコーヒーに、カフェインだけを戻す実験があります。

<!-- thth: 3/3 -->

結果は予想と違いました。根拠はこちら。
https://nigamilab.com/topics/coffee-bitterness/
"""


def make(fm: str = FM, body: str = BODY) -> str:
    return f"---\n{fm}---\n{body}"


def account_cfg(**over):
    cfg = {"media": "threads", "hashtags": False}
    cfg.update(over)
    return cfg


# --- v1 を壊さない ---------------------------------------------------------

def test_旧版の解析はthth2を型外にする():
    """**旧コードは v2 を拒否する**（Codex の検収条件）。

    `queuefile.parse_text()` は `thth: 1` 以外を `malformed` にする。
    select は malformed を候補にしないので、**旧版の実行経路が v2 の原稿を
    単発として送ることはない。** 新しく関門を作らず、既にある fail-closed を使う。
    """
    qf = queuefile.parse_text(make(), "b.md")
    assert qf.malformed is True


def test_v1の原稿はv2として読めない():
    v1 = ("---\nthth: 1\naccount: nigamilab-threads\n"
          "publish_at: 2026-09-15T19:00:00+09:00\nstatus: draft\n---\n"
          "## threads\n\n本文。\n")
    assert bundle.is_bundle_text(v1) is False
    assert bundle.parse_text(v1, "a.md").malformed is True
    # v1 側は今までどおり読める。
    assert queuefile.parse_text(v1, "a.md").malformed is False


def test_版を見分ける():
    assert bundle.is_bundle_text(make()) is True
    assert bundle.is_bundle_text("front matter が無い") is False


# --- front matter の配列 ----------------------------------------------------

def test_posts配列を読む():
    b = bundle.parse_text(make(), "b.md")
    assert b.malformed is False
    assert b.front_matter["account"] == "nigamilab-threads"
    assert [p["index"] for p in b.posts] == ["1", "2", "3"]


def test_段ごとの記録を読む():
    fm = FM.replace("""  - index: 1
""", """  - index: 1
    post_id: "18016228439944497"
    posted_at: 2026-09-15T19:00:12+09:00
    run_id: run-20260915T190000-7f3a
""")
    b = bundle.parse_text(make(fm), "b.md")
    assert b.posts[0]["post_id"] == '"18016228439944497"'
    assert b.posts[0]["run_id"] == "run-20260915T190000-7f3a"
    assert b.posts[1].get("post_id") is None


@pytest.mark.parametrize("broken", [
    "posts: 3\n",                                  # 配列でない
    "posts:\n  index: 1\n",                        # `- ` が無い
    "  index: 1\n",                                # 配列の外の字下げ
    "thth: 2\nコロンの無い行\n",                     # 読めない行
])
def test_曖昧な形は拒否する(broken):
    """**曖昧な解釈を残さない**（Codex 最終条件 5）。"""
    b = bundle.parse_text(make(fm=broken), "b.md")
    assert b.malformed is True


# --- 段の区切り -------------------------------------------------------------

def test_段に割る():
    b = bundle.parse_text(make(), "b.md")
    segments, problems = bundle.load_segments(b, "threads")
    assert problems == []
    assert len(segments) == 3
    assert segments[0].startswith("コーヒーが苦いのは")
    assert segments[2].endswith("coffee-bitterness/")
    # **連結しない。順序を保った配列。**
    assert b.segments == segments


def test_区切りは独立行だけ():
    body = BODY.replace("<!-- thth: 2/3 -->",
                         "文中に <!-- thth: 2/3 --> と書いても区切りにしない")
    b = bundle.parse_text(make(body=body), "b.md")
    segments, _ = bundle.load_segments(b, "threads")
    assert len(segments) == 2, segments


@pytest.mark.parametrize("bad,expect", [
    ("<!-- thth: 3/3 -->", "順番どおりではありません"),      # 2 番目に 3 と書く
    ("<!-- thth: 2/9 -->", "総数が段の数と合いません"),
])
def test_番号や総数が不正なら拒否する(bad, expect):
    body = BODY.replace("<!-- thth: 2/3 -->", bad)
    b = bundle.parse_text(make(body=body), "b.md")
    _segments, problems = bundle.load_segments(b, "threads")
    assert any(expect in p for p in problems), problems


def test_空の段を拒否する():
    body = BODY.replace("""<!-- thth: 2/3 -->

カフェインを抜いたコーヒーに、カフェインだけを戻す実験があります。
""", "<!-- thth: 2/3 -->\n")
    b = bundle.parse_text(make(body=body), "b.md")
    _segments, problems = bundle.load_segments(b, "threads")
    assert any("2 段目が空です" in p for p in problems), problems


# --- 検査 -------------------------------------------------------------------

def test_正しい原稿は問題なし():
    b = bundle.parse_text(make(), "b.md")
    assert bundle.check(b, account_cfg=account_cfg()) == []


def test_段の数とpostsの件数が食い違えば断る():
    """**第 2 稿の設計例がまさにこれだった**（3 段なのに posts が 2 件）。"""
    fm = FM.replace("  - index: 3\n", "")
    b = bundle.parse_text(make(fm), "b.md")
    errors = bundle.check(b, account_cfg=account_cfg())
    assert any("posts: の件数が段の数と合いません" in e for e in errors), errors


def test_continue_untilが要る():
    fm = FM.replace("continue_until: 2026-09-15T20:00:00+09:00\n", "")
    b = bundle.parse_text(make(fm), "b.md")
    errors = bundle.check(b, account_cfg=account_cfg())
    assert any("continue_until: 必須です" in e for e in errors), errors


def test_continue_untilはpublish_atより後():
    fm = FM.replace("continue_until: 2026-09-15T20:00:00+09:00",
                     "continue_until: 2026-09-15T18:00:00+09:00")
    b = bundle.parse_text(make(fm), "b.md")
    errors = bundle.check(b, account_cfg=account_cfg())
    assert any("publish_at より後" in e for e in errors), errors


def test_2段目以降のtopicは黙って捨てずに断る():
    """**API の制約ではなく初版の製品方針**（設計 §7・Codex 指摘）。"""
    fm = FM.replace("  - index: 2\n", "  - index: 2\n    topic: 焙煎\n")
    b = bundle.parse_text(make(fm), "b.md")
    errors = bundle.check(b, account_cfg=account_cfg())
    hit = [e for e in errors if "topic が指定されています" in e]
    assert hit, errors
    assert "初版では未対応" in hit[0]


def test_段の数の上下限():
    one = FM.replace("  - index: 2\n  - index: 3\n", "")
    body = BODY.split("<!-- thth: 2/3 -->")[0]
    b = bundle.parse_text(make(one, body), "b.md")
    errors = bundle.check(b, account_cfg=account_cfg())
    assert any("段の数は 2〜4 です" in e for e in errors), errors


def test_段ごとに文字数を数える():
    long_body = BODY.replace("結果は予想と違いました。根拠はこちら。",
                              "あ" * 520)
    b = bundle.parse_text(make(body=long_body), "b.md")
    errors = bundle.check(b, account_cfg=account_cfg())
    assert any("3 段目は 500 字以内" in e for e in errors), errors


def test_段ごとにハッシュタグを見る():
    body = BODY.replace("結果は予想と違いました。", "結果は違いました #コーヒー ")
    b = bundle.parse_text(make(body=body), "b.md")
    errors = bundle.check(b, account_cfg=account_cfg())
    assert any("3 段目にハッシュタグ" in e for e in errors), errors


def test_知らない項目を拒否する():
    fm = FM.replace("  - index: 2\n", "  - index: 2\n    parent: 1\n")
    b = bundle.parse_text(make(fm), "b.md")
    errors = bundle.check(b, account_cfg=account_cfg())
    assert any("知らない項目" in e for e in errors), errors


# --- lint / preview の入口（セッションに渡す前に確かめる） -------------------

def test_lintが束を束として検査する(tmp_path, isolated_account):
    """**「thth: 1 が無い」とだけ言わない。**

    2026-09-11 に 3 回「動かないコマンドを配る」失敗をしたので、
    **渡す前に実際に叩く。**
    """
    from thth import lint
    path = tmp_path / "t.md"
    text = make().replace("nigamilab-threads", isolated_account["name"])
    path.write_text(text, encoding="utf-8")
    assert lint.lint_file(str(path)) == []

    broken = str(tmp_path / "bad.md")
    open(broken, "w", encoding="utf-8").write(
        text.replace("<!-- thth: 2/3 -->", "<!-- thth: 3/3 -->"))
    errors = lint.lint_file(broken)
    assert any("順番どおりではありません" in e for e in errors), errors
    assert not any("thth: 1" in e for e in errors), errors


def test_previewは段ごとに分けて出す(tmp_path, isolated_account):
    """**区切り行を本文として見せない**（それが投稿されるように読める）。"""
    from thth import lint
    path = tmp_path / "t.md"
    path.write_text(make().replace("nigamilab-threads", isolated_account["name"]),
                     encoding="utf-8")
    out = lint.preview_file(str(path))
    assert "<!-- thth:" not in out, out
    assert "1/3　返信先なし（先頭）" in out
    assert "2/3　1 段目への返信" in out
    assert "コーヒーが苦いのは、カフェインのせいでしょうか。" in out
