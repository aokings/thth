"""3.1.1 件 3: 毎朝の一枚の「絡みに行く先」から自分を除く（同じ本人の別台帳も）。

実測（依頼書）: 同じ project の Mastodon 台帳が 2 本（同じ handle）あり、片方の
`morning` の世間に、もう片方の投稿が絡む先として出た。ここでは:
- 同じ handle の Mastodon 台帳 2 本と、その handle の投稿を含む検索結果で、
  `targets` と `next_steps` の `engage` にその投稿が出ない。
- `own_excluded` が 1、件数 `n` と `distinct_authors` は除く前と同じ。
- `morning <account>`（単体）でも、同じ project の他台帳の鍵で除く。
"""
from __future__ import annotations

import datetime
import pytest

from thth import accounts, adapters, jst, morning
from thth.adapters import base as adapter_base, mastodon as mastodon_mod

NOW = datetime.datetime(2026, 9, 23, 7, 0, 0, tzinfo=jst.JST)
PROJECT = "kopicha"
INSTANCE = "https://mastodon.social"
HANDLE = "kopi_chaba"


class FakeMastodon(mastodon_mod.MastodonAdapter):
    """鍵の式は本物（`author_key()`・ドメイン付き acct）、読む口だけ偽物。"""
    rows: list = []

    def keyword_search(self, word, **kwargs): return list(self.rows)
    def tag_observation(self, tag, **kwargs): raise adapter_base.AdapterError("synthetic")
    def mentions(self, *, since=None): return []


def row(post_id, acct):
    key = mastodon_mod.MastodonAdapter(instance=INSTANCE).author_key(acct)
    return {"message_id": post_id, "username": acct, "author_key": key,
            "text": "コーヒーの話 " + post_id, "timestamp": "2026-09-23T06:00:00+09:00",
            "medium": "mastodon", "permalink": "https://mastodon.social/@x/" + post_id,
            "has_replies": False}


@pytest.fixture
def two(isolated_account_factory, monkeypatch):
    for name in ("kopicha-mastodon", "kopicha-server-mastodon"):
        isolated_account_factory(name, project=PROJECT, media="mastodon", instance=INSTANCE,
                                 handle="@" + HANDLE,
                                 watch_words=["コーヒー"] if name == "kopicha-mastodon" else [])
    monkeypatch.delenv(mastodon_mod.INSTANCE_ENV, raising=False)
    monkeypatch.setattr(accounts, "load_token", lambda cfg: {"access_token": "FAKE"})
    FakeMastodon.rows = [row("1", "alice"), row("2", HANDLE), row("3", "bob@other.example"),
                         row("4", "carol")]
    monkeypatch.setattr(adapters, "make_adapter", lambda cfg, token: FakeMastodon(instance=cfg["instance"]))


def world_cell(payload, name="kopicha-mastodon"):
    world = {n["section"]: n for n in payload["sections"]}["world"]
    return world["value"]["by_account"][name]


def engage_ids(payload):
    steps = {n["section"]: n for n in payload["sections"]}["next_steps"]["value"]["steps"]
    return [s["post_id"] for s in steps if s["kind"] == "engage"]


@pytest.mark.parametrize("target", [PROJECT, "kopicha-mastodon"])
def test_own_posts_are_not_engage_targets_and_counts_stay(two, target):
    payload = morning.build(target, now=NOW, mark=False)
    account = world_cell(payload)
    value = account["value"]["by_word"]["コーヒー"]["value"]
    assert [t["post_id"] for t in value["targets"]] == ["1", "3", "4"]
    assert value["own_excluded"] == 1
    assert value["n"] == 4 and value["distinct_authors"] == 4
    assert "2" not in engage_ids(payload) and set(engage_ids(payload)) == {"1", "3", "4"}
    # 見た同じ媒体の台帳は 2 本（単体の問いでも project の他台帳を見る）。
    assert account["value"]["own_accounts"] == {"n": 2, "unreadable": 0}


def test_other_project_with_same_handle_is_not_excluded(two):
    # 別 project の同名 handle は別人として扱う（同じ project の台帳だけを見る）。
    import json, os
    for name, changes in (("kopicha-mastodon", {"handle": "@someone-else"}),
                          ("kopicha-server-mastodon", {"project": "elsewhere"})):
        path = os.path.join(accounts.accounts_dir(), name + ".json")
        with open(path, encoding="utf-8") as stream: cfg = json.load(stream)
        cfg.update(changes)
        with open(path, "w", encoding="utf-8") as stream: json.dump(cfg, stream)
    value = world_cell(morning.build("kopicha-mastodon", now=NOW, mark=False))["value"]["by_word"]["コーヒー"]["value"]
    assert value["own_excluded"] == 0 and [t["post_id"] for t in value["targets"]] == ["1", "2", "3"]


def test_human_render_shows_own_excluded(two):
    lines = []
    morning.render(morning.build(PROJECT, now=NOW, mark=False), out=lines.append)
    assert any("自分を除外 1" in line for line in lines)
