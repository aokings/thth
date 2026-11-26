"""括りの段 0 の柵: 全部の命令に札（括りの中・管理者だけ・括りなし）がある。

札の無い命令を足すと赤になる。札の正本は thth/command_scope.py。
"""
from __future__ import annotations

import argparse

from thth import cli, command_scope as cs, remote


def _subparsers(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return {}


def _top():
    return _subparsers(cli.build_parser())


def test_every_top_level_command_has_a_scope():
    missing = sorted(set(_top()) - set(cs.TOP))
    assert not missing, f"札の無い命令（thth/command_scope.py の TOP に足す）: {missing}"


def test_no_stale_scope():
    stale = sorted(set(cs.TOP) - set(_top()))
    assert not stale, f"もう無い命令の札: {stale}"


def test_scopes_are_known():
    assert set(cs.TOP.values()) <= set(cs.SCOPES)
    assert set(cs.CHILD_OVERRIDES.values()) <= set(cs.SCOPES)


def test_child_overrides_name_real_children():
    top = _top()
    for key in cs.CHILD_OVERRIDES:
        parent, child = key.split(" ", 1)
        assert parent in top, key
        children = _subparsers(top[parent])
        if children:
            assert child in children, key


def test_remote_commands_are_not_local():
    # 遠くの道に出ている命令は VM の括りの中で動く。括りなし（手元だけ）の札は付かない。
    for name in remote.REMOTE_PARSERS + remote.REMOTE_CHILD_PARSERS:
        assert cs.scope_of(name) == cs.WS, name


def test_admin_children():
    assert cs.scope_of("account", "add") == cs.ADMIN
    assert cs.scope_of("account", "status") == cs.WS
    assert cs.scope_of("nope") is None
