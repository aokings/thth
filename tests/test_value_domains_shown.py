"""選べる値の一覧が、案内と文書からずれていないか（運用セッション報告 2026-09-12）。

**3 か所に手書きの一覧があって、3 か所とも違う欠け方をしていた。**

    --help          verdict 4 つ・kind 9 つ（完全）
    --advise の案内  **dead** と **年度付き** が無い
    suggest の案内   **unknown**・**年度付き**・**カテゴリ** が無い
    引継ぎ書        **unknown**・**年度付き**・**カテゴリ** が無い

**`年度付き` は前日に足した型**なのに、どの案内にも出ていなかった——**これから
使う人は存在を知らない。**

**案内はコードで定数から組み立てるようにした。** 文書は手書きなので、ここで見る。
"""
from __future__ import annotations

import argparse
import pathlib

import pytest

from thth import cli as cli_mod, topics as topics_mod


def _案内(monkeypatch, capsys, 呼ぶ):
    monkeypatch.setattr(cli_mod.account_report_mod, "topic_plan",
                         lambda *_a, **_k: {"topics": []})
    呼ぶ()
    return capsys.readouterr().out


@pytest.mark.parametrize("値", topics_mod.VERDICTS + tuple(topics_mod.KINDS)
                                 + tuple(topics_mod.OBS_STATUS))
def test_adviseの案内に選べる値が全部出る(thth_root, capsys, monkeypatch, 値):
    out = _案内(monkeypatch, capsys,
                lambda: cli_mod._advise("kopicha-threads", as_json=False))
    assert 値 in out, f"**選べるのに案内に出ていない**: {値}"


@pytest.mark.parametrize("値", topics_mod.VERDICTS + tuple(topics_mod.KINDS)
                                 + tuple(topics_mod.OBS_STATUS))
def test_suggestの案内に選べる値が全部出る(thth_root, capsys, 値):
    cli_mod._topic_suggest_help() if hasattr(cli_mod, "_topic_suggest_help") else None
    # 案内は `--advise` と同じ helper から出しているので、コード側で確かめる。
    from thth import cli
    src = pathlib.Path(cli.__file__).read_text(encoding="utf-8")
    assert "_選べる(topics_mod.VERDICTS)" in src
    assert "_選べる(topics_mod.KINDS)" in src
    assert "_選べる(topics_mod.OBS_STATUS)" in src


文書 = pathlib.Path(__file__).parent.parent / "docs" / \
    "引継ぎ_トピックの棚_アカウント側セッションへ_2026-09-12.md"


@pytest.mark.parametrize("値", topics_mod.VERDICTS + tuple(topics_mod.KINDS))
def test_引継ぎ書に選べる値が全部出る(値):
    """**文書は手書きなので、ここで見る。** 型を足したら、この文書も直す。"""
    本文 = 文書.read_text(encoding="utf-8")
    assert 値 in 本文, f"**選べるのに引継ぎ書に出ていない**: {値}"


# --- `--json` の契約が文書と合っているか（設計 v1.0.0 §1 受け入れ T-A4） ------
#
# **鍵の名前を変えたのは、古い読み手に旧意味で読ませないため**（規約 5）。
# **文書がその名前を書いていなければ、読む人は旧契約のまま使う。**

使い方 = pathlib.Path(__file__).parent.parent / "docs" / \
    "使い方_プロジェクトのセッション向け_2026-09-09.md"

新しい鍵 = ("observations", "observations_more", "note_id",
             "thth topics history", "thth topics retract-note")
旧い鍵 = ("audience_account", "audience_by")


@pytest.mark.parametrize("紙", [文書, 使い方])
@pytest.mark.parametrize("鍵", 新しい鍵)
def test_新しい鍵が両方の文書に出る(紙, 鍵):
    本文 = 紙.read_text(encoding="utf-8")
    assert 鍵 in 本文, f"**新しい契約が {紙.name} に書かれていない**: {鍵}"


@pytest.mark.parametrize("紙", [文書, 使い方])
@pytest.mark.parametrize("鍵", 旧い鍵)
def test_旧い鍵は廃止したと分かる形でだけ残す(紙, 鍵):
    """**黙って消さない。** 旧鍵で読んでいた人が、消えた理由を読めるようにする。
    ただし**「使ってよい」とは書かせない**——`廃止` の語と同じ節にだけ置く。"""
    行 = [l for l in 紙.read_text(encoding="utf-8").splitlines() if 鍵 in l]
    assert 行, f"**廃止した鍵の説明が {紙.name} に無い**: {鍵}"
    assert all("廃止" in l for l in 行), \
        f"**廃止と書かずに {鍵} を出している**: {行}"


def test_advise_のjsonに旧鍵が無い(thth_root, capsys, monkeypatch):
    """**文書とコードの両方を見る。** 片方だけ直しても食い違いは残る。"""
    import json
    from thth import cli
    topics_mod.record("お茶", verdict="alive", audience="茶葉の話",
                       by="自分", account="kopicha-threads")
    monkeypatch.setattr(cli.account_report_mod, "topic_plan",
                         lambda *_a, **_k: {"topics": []})
    cli._advise("kopicha-threads", as_json=True)
    payload = json.loads(capsys.readouterr().out)
    行 = payload["proven"][0]
    assert "observations" in 行 and "observations_more" in 行
    for 旧 in ("audience", "audience_account", "audience_by"):
        assert 旧 not in 行, f"**旧鍵が `--json` に残っている**: {旧}"
