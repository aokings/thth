"""記事別トピック提案の保存（設計 §8・§9・工程 4＝P1）。

受け入れ: T13（同時書込・同じ入力の再送）・T14（保存途中の停止・破損ファイル）。
"""
from __future__ import annotations

import json
import os

import pytest

from thth import topic_models as models
from thth import topic_store as store

ARTICLE = {
    "requested_url": "https://x.example/a", "final_url": "https://x.example/a",
    "retrieved_at": "2026-09-11T06:00:00+09:00", "provider": "browser",
    "submitted_by": "テスト", "retrieval_status": "ok", "title": "記事",
    "language": "ja", "content_text": "本文です。", "coverage": "full",
    "source_locator": "https://x.example/a",
}


def _profile(**over):
    row = {"account": "kopicha-threads", "language": "ja",
           "primary_goal": "article_visits", "editorial_scope": "読み物",
           "intended_interests": ["豆を買う人"], "avoid_misrepresentation": [],
           "status": "confirmed", "confirmed_by": "claude（kopicha セッション）",
           "basis": "docs/編集方針.md"}
    row.update(over)
    return models.build_profile(row)


def test_同じ入力の再送は重複しない(thth_root):
    """受け入れ T13。**中身から ID が決まるので、同じ入力は同じファイル。**"""
    a = models.build_article(ARTICLE)
    first, wrote1 = store.put("articles", a, id_key="article_id")
    second, wrote2 = store.put("articles", dict(a), id_key="article_id")

    assert wrote1 is True and wrote2 is False
    assert first["article_id"] == second["article_id"]
    rows, broken = store.load_all("articles")
    assert len(rows) == 1 and broken == []


def test_違う入力は別の記録として両方残る(thth_root):
    a = models.build_article(ARTICLE)
    b = models.build_article(dict(ARTICLE, content_text="別の本文です。"))
    store.put("articles", a, id_key="article_id")
    store.put("articles", b, id_key="article_id")
    rows, _ = store.load_all("articles")
    assert len(rows) == 2


def test_壊れたファイルを観測なしと偽らない(thth_root):
    """受け入れ T14。**読めなかったものは「壊れた」として名前を返す。**

    「観測が無い」と「観測が読めない」を混ぜると、**判らないことを判った形で**
    次の判断へ渡してしまう。
    """
    a = models.build_article(ARTICLE)
    store.put("articles", a, id_key="article_id")
    broken_path = os.path.join(store.root(), "articles", "f" * 64 + ".json")
    with open(broken_path, "w", encoding="utf-8") as f:
        f.write("{壊れている")

    rows, broken = store.load_all("articles")
    assert len(rows) == 1, "読めた記録まで失っている"
    assert broken == ["sha256:" + "f" * 64], broken


def test_保存途中の一時ファイルは読まれない(thth_root):
    """rename でしか本体にならないので、途中で止まっても半端な記録は見えない。"""
    a = models.build_article(ARTICLE)
    store.put("articles", a, id_key="article_id")
    tmp = os.path.join(store.root(), "articles", "abc.json.1234.deadbeef.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("{途中")
    rows, broken = store.load_all("articles")
    assert len(rows) == 1 and broken == []


def test_任意のパスを連結させない(thth_root):
    a = models.build_article(ARTICLE)
    with pytest.raises(store.StoreError, match="ID の形"):
        store.put("articles", dict(a, article_id="../../逃げる"), id_key="article_id")
    with pytest.raises(store.StoreError, match="account の形"):
        store.get_profile("../../逃げる")


def test_知らない種類は拒否する(thth_root):
    with pytest.raises(store.StoreError, match="知らない種類"):
        store.load_all("なにか")


def test_profileは履歴を残して更新する(thth_root):
    """設計 §8。**active だけが動き、旧版は消えない。**"""
    first = _profile()
    store.set_profile(first)
    second = _profile(primary_goal="relevant_conversation")
    store.set_profile(second)

    active = store.get_profile("kopicha-threads")
    assert active["profile_version"] == second["profile_version"]
    history = os.listdir(os.path.join(store.root(), "profile_history"))
    assert len(history) == 1, history


def test_旧記録は参考証拠として読めて元データを変えない(thth_root):
    """設計 §9。**読むだけ。account を `by` から推測して埋めない。**"""
    from thth import topics as topics_mod
    topics_mod.record("精製", verdict="mismatch", audience="レアアース",
                       by="claude（kopicha セッション）")
    before = topics_mod.load()

    rows = store.legacy_observations()
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "legacy_note" and row["provenance"] == "legacy"
    assert row["legacy_verdict"] == "mismatch"
    assert row["audience"] == "レアアース"
    assert row["account"] is None, "by から account を推測して埋めている"
    assert row["status"] is None, "取得結果が無いのに埋めている"
    assert topics_mod.load() == before, "旧データを書き換えた"


def test_同時に保存しても壊れない(thth_root):
    """受け入れ T13。別プロセスから同じ store に書く。"""
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {os.path.dirname(os.path.dirname(os.path.abspath(store.__file__)))!r})
        from thth import topic_models as m, topic_store as s
        a = m.build_article(dict({ARTICLE!r}, content_text="別プロセスの本文"))
        s.put("articles", a, id_key="article_id")
    """)
    store.put("articles", models.build_article(ARTICLE), id_key="article_id")
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                           env=dict(os.environ))
    assert proc.returncode == 0, proc.stderr

    rows, broken = store.load_all("articles")
    assert len(rows) == 2 and broken == []
    assert all(isinstance(r, dict) and "article_id" in r for r in rows)
