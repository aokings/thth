"""4.0 S2 の試験（`tests/test_v400_s2_*.py`）のうち、変換・commit・知らせ・監査の縛りが共有する組み立て。

S1 の `lane`（本物の git repo を持つ `lane-a-hatenablog`）の上に、**画像の無い記事**を commit して
置き直し、S2 の画像の断り（`hatena_convert.REFUSE_LOCAL_IMAGES`）を戻す。送信は S1 の `FakeAdapter`
（はてなに何も送らない）。**本物の token は置かない。実機にはつながない。**
"""
from __future__ import annotations

import io
import contextlib

import pytest

from tests.hatena_s1_flow_helpers import (ACCOUNT, NOW, FakeAdapter, approve, call, factory_for,  # noqa: F401
                                          first_stage_digest, fm_of, lane, world)
from thth import core, deliveries, hatena_convert

PLAIN_ARTICLE = "# アラビカ\n\n値動きのまとめ。\n\n終わり。\n"


@pytest.fixture
def s2lane(lane, monkeypatch):  # noqa: F811
    monkeypatch.setattr(hatena_convert, "REFUSE_LOCAL_IMAGES", True)
    lane.write("articles/a.md", PLAIN_ARTICLE)
    return lane


def throw(adapter=None, *, logs=None):
    factory = factory_for(adapter) if adapter is not None else None
    return core.throw_once(ACCOUNT, production_flag=True, adapter_factory=factory, now=NOW,
                           log=(logs.append if logs is not None else None))


def states(delivery):
    return [row["state"] for row in deliveries.read(ACCOUNT, delivery)["history"]]


def lint_codes(path):
    from thth import lint
    return [line.split(":", 1)[0] for line in lint.lint_file(path) if not lint.is_warning(line)]


def set_account(world, **values):  # noqa: F811
    data = world.account(ACCOUNT)
    for key, value in values.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    world.write_account(data)


def stage1(path):
    rc, out, err = call(["approve", path])
    return rc, out + err


__all__ = ["ACCOUNT", "NOW", "FakeAdapter", "PLAIN_ARTICLE", "approve", "call", "factory_for",
           "first_stage_digest", "fm_of", "lane", "world", "s2lane", "throw", "states", "lint_codes",
           "set_account", "stage1", "io", "contextlib"]
