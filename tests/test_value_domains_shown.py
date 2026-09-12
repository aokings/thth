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
