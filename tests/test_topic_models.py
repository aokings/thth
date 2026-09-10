"""記事別トピック提案の型と ID（設計 §4.1〜4.5・§10・工程 3＝P0）。

受け入れ: T02（切り口が変われば別 context）・T03（記事本文が変われば別 article）・
T26（profile が変われば context 不一致）・T10 の一部（引用・参照の検査）。
"""
from __future__ import annotations

import pytest

from thth import topic_models as m

ARTICLE = {
    "requested_url": "https://kopicha.example/processing",
    "final_url": "https://kopicha.example/processing",
    "retrieved_at": "2026-09-11T06:00:00+09:00",
    "provider": "browser",
    "submitted_by": "claude（kopicha セッション）",
    "retrieval_status": "ok",
    "title": "コーヒーの精製方法",
    "language": "ja",
    "content_text": "収穫後の処理で味が変わります。ナチュラルはベリーの香り。",
    "coverage": "full",
    "source_locator": "https://kopicha.example/processing",
}


def _context(**over):
    row = {
        "account": "kopicha-threads", "profile_version": "sha256:" + "a" * 64,
        "draft_bytes_sha256": "b" * 64, "section": "同じ農園の豆なのに…",
        "reply_to": None, "topic": "コーヒー",
        "publish_at": "2026-09-12T08:00:00+09:00",
        "article_id": "sha256:" + "c" * 64,
        "article_content_sha256": "d" * 64,
        "observation_ids": ["sha256:" + "e" * 64], "policy_version": "1",
        "main_article_url": "https://kopicha.example/processing",
    }
    row.update(over)
    return m.build_context(row)


def test_同じ入力なら同じid_違えば違うid():
    a = m.build_article(ARTICLE)
    b = m.build_article(dict(ARTICLE))
    assert a["article_id"] == b["article_id"]
    assert a["article_id"].startswith("sha256:") and len(a["article_id"]) == 71


def test_T03_記事本文が変われば別のarticle_id():
    """URL は同じ、取得した本文が更新された。**旧判断を最新として返さない。**"""
    a = m.build_article(ARTICLE)
    updated = dict(ARTICLE, content_text=ARTICLE["content_text"] + "（追記）")
    b = m.build_article(updated)
    assert a["article_id"] != b["article_id"]
    assert a["content_sha256"] != b["content_sha256"]


def test_T02_切り口が変われば別のcontext():
    """同じ記事 URL でも、投稿の本文が違えば別の判断。"""
    a = _context()
    b = _context(section="加工技術の話をします")
    assert a["context_id"] != b["context_id"]


def test_T26_profileが変われば別のcontext():
    a = _context()
    b = _context(profile_version="sha256:" + "9" * 64)
    assert a["context_id"] != b["context_id"]


def test_topicや予約時刻が変われば別のcontext():
    assert _context()["context_id"] != _context(topic="お茶")["context_id"]
    assert _context()["context_id"] != _context(
        publish_at="2026-09-12T20:00:00+09:00")["context_id"]


def test_表示用のパスはidに影響しない():
    """同じ中身なら、置き場所が違っても同じ判断。"""
    a = m.build_context(dict(_context(), draft_path="/a/x.md"))
    b = m.build_context(dict(_context(), draft_path="/b/y.md"))
    assert a["context_id"] == b["context_id"]


def test_観測idの順序はidに影響しない():
    ids = ["sha256:" + "1" * 64, "sha256:" + "2" * 64]
    a = _context(observation_ids=ids)
    b = _context(observation_ids=list(reversed(ids)))
    assert a["context_id"] == b["context_id"]


# --- 引用と参照の検査（受け入れ T10）

def _proposal(**over):
    row = {
        "context_id": "sha256:" + "f" * 64, "prompt_version": "1",
        "intended_reader": "豆を買う人", "article_value": "処理で味が変わる",
        "post_angle": "家で違いを楽しむ",
        "candidates": [{
            "topic": "コーヒー", "article_fit": 3, "conversation_fit": 2,
            "article_quotes": ["ナチュラルはベリーの香り"],
            "observation_refs": ["sha256:" + "e" * 64],
            "rationale": "記事の主題と一致", "counterevidence": "重大な反証を確認できず",
            "uncertainties": "標本が少ない", "fit": "suitable"}],
        "selected_topic": "コーヒー", "selection_reason": "人がいて話が合う",
    }
    row.update(over)
    return row


def test_引用が本文に無ければ拒否する():
    """**LLM が作った引用をそのまま信じない**（設計 §4.4・T10）。"""
    article = m.build_article(ARTICLE)
    bad = _proposal(candidates=[dict(_proposal()["candidates"][0],
                                      article_quotes=["書かれていない一文"])])
    with pytest.raises(m.SchemaError, match="引用が記事本文にありません"):
        m.validate_proposal(bad, article=article,
                             known_observation_ids={"sha256:" + "e" * 64})


def test_引用は空白を整形せずそのまま照合する():
    """**整形して合わせると検査にならない。**"""
    article = m.build_article(ARTICLE)
    assert m.quote_is_in_article(article, "ナチュラルはベリーの香り")
    assert not m.quote_is_in_article(article, "ナチュラル は ベリー の 香り")


def test_実在しない観測idを拒否する():
    article = m.build_article(ARTICLE)
    with pytest.raises(m.SchemaError, match="観測 ID が実在しません"):
        m.validate_proposal(_proposal(), article=article, known_observation_ids=set())


def test_値域の外の点数を拒否する():
    article = m.build_article(ARTICLE)
    bad = _proposal(candidates=[dict(_proposal()["candidates"][0], article_fit=5)])
    with pytest.raises(m.SchemaError, match="0〜3"):
        m.validate_proposal(bad, article=article,
                             known_observation_ids={"sha256:" + "e" * 64})


def test_正しい候補比較は通る():
    article = m.build_article(ARTICLE)
    out = m.validate_proposal(_proposal(), article=article,
                               known_observation_ids={"sha256:" + "e" * 64})
    assert out["proposal_id"].startswith("sha256:")


# --- profile（設計 §10・masaru 裁定 2026-09-11）

def _profile(**over):
    row = {"account": "kopicha-threads", "language": "ja",
           "primary_goal": "article_visits", "editorial_scope": "コーヒーと茶の読み物",
           "intended_interests": ["豆を買う人"], "avoid_misrepresentation": ["健康効能"],
           "status": "confirmed", "confirmed_by": "claude（kopicha セッション）",
           "basis": "docs/編集方針.md"}
    row.update(over)
    return row


def test_根拠が無いままconfirmedにできない():
    """**根拠なしの確定を作らない**（masaru 裁定 2026-09-11）。"""
    with pytest.raises(m.SchemaError, match="根拠"):
        m.build_profile(_profile(basis=""))


def test_方針が不明なら仮のまま置ける():
    """不明・矛盾は推測で確定せず `provisional` のまま。"""
    out = m.build_profile(_profile(status="provisional", basis=""))
    assert out["status"] == "provisional"


def test_確認者が要る():
    with pytest.raises(m.SchemaError, match="confirmed_by"):
        m.build_profile(_profile(confirmed_by=""))


def test_profileが変わればversionが変わる():
    a = m.build_profile(_profile())
    b = m.build_profile(_profile(primary_goal="relevant_conversation"))
    assert a["profile_version"] != b["profile_version"]


def test_足りない項目は推測で補わずに止まる():
    row = dict(ARTICLE)
    del row["content_text"]
    with pytest.raises(m.SchemaError, match="足りません"):
        m.build_article(row)


def test_主対象の記事が変われば別のcontext():
    """独立レビュー 2026-09-11・指摘 1。

    以前は `main_article_url` を `context_id` の計算の**外**に置いていたので、
    `--article-url` を別の記事に変えても ID が動かなかった——**主対象を
    変えても同じ判断のまま通る**形だった。
    """
    a = _context()
    b = _context(main_article_url="https://kopicha.example/other")
    assert a["context_id"] != b["context_id"]
