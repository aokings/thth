"""`thth share` が「そんなコマンドは無い」で断られること（T5-1「`thth share` を消す」）。

コード（`thth/share.py`・`thth/share_cli.py`・`cli.py` の登録）ごと消した。
argparse の subparsers は未知のコマンドを渡すと自分で `invalid choice` を
出して非ゼロで終わる——**新しく何かを書かなくても「無い」と言えている**
ことを、消した直後に固定する。
"""
from __future__ import annotations

import argparse

import pytest

from thth import cli as cli_mod


def _subcommand_choices() -> set:
    parser = cli_mod.build_parser()
    for action in parser._subparsers._group_actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("subparsers action が見つかりません")


def test_thth_shareはそんなコマンドは無い():
    parser = cli_mod.build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["share"])
    assert exc.value.code != 0


def test_thth_shareはそんなコマンドは無い_stderr(capsys):
    parser = cli_mod.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["share"])
    captured = capsys.readouterr()
    assert "invalid choice" in captured.err
    assert "'share'" in captured.err


def test_shareはsubparsersの選択肢に無い():
    """`thth --help` が挙げる実際のサブコマンド一覧（`build_parser()` の
    subparsers）に `share` という名前が残っていないこと。"""
    assert "share" not in _subcommand_choices()
