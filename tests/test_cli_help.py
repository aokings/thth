"""`thth share` が「そんなコマンドは無い」で断られること（T5-1「`thth share` を消す」）。

コード（`thth/share.py`・`thth/share_cli.py`・`cli.py` の登録）ごと消した。
argparse の subparsers は未知のコマンドを渡すと自分で `invalid choice` を
出して非ゼロで終わる——**新しく何かを書かなくても「無い」と言えている**
ことを、消した直後に固定する。
"""
from __future__ import annotations

import argparse
import pathlib
import re

import pytest

from thth import cli as cli_mod

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


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


# --- T6-5: README の「動くもの」の列挙を `thth --help` に合わせる ------------
# `tests/test_docs_links.py` と同じ型（**実物と食い違えば落ちる**・loud
# reject）——README・README.en の列挙は手で書いているので、`thth --help` が
# 増えても文書が追随しない。**片方だけ増えても落ちる**ように、両方の書式
# それぞれから機械的に名前を拾って `_subcommand_choices()` と突き合わせる。


def _readme_ja_commands() -> list:
    """README.md の「動くもの」の 1 行から、バッククォート付きの名前を拾う。"""
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"動くもの.*?: (.+?)。\n", text)
    assert m, "README.md に「動くもの」の行が見つかりません"
    return re.findall(r"`([a-z_-]+)`", m.group(1))


def _readme_en_commands() -> list:
    """README.en.md の Commands 節、`- \\`name\\` — ...` の形の行から名前を拾う。"""
    text = (REPO_ROOT / "README.en.md").read_text(encoding="utf-8")
    return re.findall(r"^- `([a-z_-]+)` — ", text, flags=re.MULTILINE)


def test_READMEの動くものの列挙はthth_helpの選択肢と一致する():
    choices = _subcommand_choices()
    ja = _readme_ja_commands()
    assert len(ja) == len(set(ja)), f"README.md の列挙に重複があります: {ja}"
    assert set(ja) == choices, (
        f"README.md の「動くもの」の列挙が thth --help の choices と一致しません。"
        f"README にだけある: {set(ja) - choices} / --help にだけある: {choices - set(ja)}")


def test_READMEenのCommands列挙はthth_helpの選択肢と一致する():
    choices = _subcommand_choices()
    en = _readme_en_commands()
    assert len(en) == len(set(en)), f"README.en.md の列挙に重複があります: {en}"
    assert set(en) == choices, (
        f"README.en.md の Commands 節が thth --help の choices と一致しません。"
        f"README.en にだけある: {set(en) - choices} / --help にだけある: {choices - set(en)}")
