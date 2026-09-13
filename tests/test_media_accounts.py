"""同席用の 2 媒体（Bluesky・Mastodon）の台帳と画面（T3 の配線 2026-09-13）。

**2026-09-14 に土台が変わった。** 以前ここは repo に同梱されていた
`accounts/masaru-bluesky.json`・`accounts/masaru-mastodon.json` を**直に読んで**
いた。台帳は repo の外へ出た（設計 v2 §3「台帳を repo の外へ」・6 本を `git rm`）
ので、repo からは読めない。運用の `$THTH_ROOT/accounts/` を読みに行かせるのも
だめ（**打つ機械によって通ったり落ちたりする試験**になる）。

だからここは**同じ形の台帳を隔離した置き場に組んで**確かめる。失ったのは
「masaru の 2 本が実際にその値で置かれていること」で、それは repo の仕事では
なくなった（運用の `$THTH_ROOT/accounts/` の中身）。残したのは道具の側の約束:

- **媒体ごとの追加項目がアダプタに届く**（Bluesky は `service`、Mastodon は
  `instance`）。雛形 `accounts.example/` が知っている媒体を指している。
- **`thth account` と `thth board` が 2 媒体を読んで落ちない**（トークンが無い
  状態で `no_token` と出る）。台帳を足しただけで既存の画面が落ちると、masaru が
  現物を試す前に手が止まる。
- **他媒体の token が `graph.threads.net` へ行かない**（宛先を媒体が決める・F2）。
- **`recent_posts` を持たない媒体には聞きに行かない**（能力で塞ぐ・アダプタを
  組み立てもしない）。

**本物の API は 1 つも叩かない**（トークンが無いので、そもそも網に出ない）。
"""
from __future__ import annotations

import json
import os
import pathlib

import pytest

from tests.conftest import run_thth
from thth import adapters as adapters_mod

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "accounts.example"

# 同席用の 2 媒体。**名前は試験のもの**（実アカウント名ではない——repo に
# 実アカウントの綴りを残さない・公開前チェックリスト §4）。
同席用 = {
    "demo-bluesky": {"media": "bluesky", "handle": "demo.bsky.social",
                     "service": "https://bsky.social"},
    "demo-mastodon": {"media": "mastodon", "handle": "demo",
                      "instance": "https://mastodon.example"},
}


@pytest.fixture
def 同席の2本(isolated_account_factory, tmp_path):
    """2 媒体の台帳を隔離した置き場に 1 本ずつ。**トークンは置かない。**

    `production: true`・`scheduled: false`（同席専用の様態・masaru 裁定
    2026-09-13 夕）を写す——`thth send --production` を人が打ったときだけ出て、
    timer は生えない。
    """
    out = {}
    for name, 欄 in 同席用.items():
        out[name] = isolated_account_factory(
            name, project="demo", production=True, scheduled=False,
            token=str(tmp_path / f"{name}.token"),   # **置かない**（no_token）
            **欄)
    return out


@pytest.mark.parametrize("media", ["bluesky", "mastodon"])
def test_雛形が既知の媒体を指している(media):
    """**知らない媒体なら loud に断られる**（T-B0）。配る雛形がその関門を通る。"""
    with open(EXAMPLES / f"{media}.json", encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["media"] == media
    adapters_mod.adapter_class(cfg["media"])


def test_blueskyの台帳のserviceがアダプタに届く(同席の2本):
    """媒体ごとの追加項目（設計 v2 §4.2「台帳と登録」）。"""
    cfg = _読む(同席の2本["demo-bluesky"])
    assert cfg["media"] == "bluesky"
    assert cfg["handle"] == "demo.bsky.social"
    assert cfg["service"] == "https://bsky.social"
    # 台帳から実際に組み立てられる（トークンが無くても組み立ては通る）。
    adapter = adapters_mod.make_adapter(cfg, {})
    assert adapter.service == "https://bsky.social"


def test_mastodonの台帳のinstanceがアダプタに届く(同席の2本):
    """**`instance` は必須**——既定に落とすと、書き忘れた人が知らないサーバに投げる。"""
    cfg = _読む(同席の2本["demo-mastodon"])
    assert cfg["media"] == "mastodon"
    assert cfg["instance"] == "https://mastodon.example"
    adapter = adapters_mod.make_adapter(cfg, {"access_token": ""})
    assert adapter.instance == "https://mastodon.example"


def _読む(account: dict) -> dict:
    path = os.path.join(account["accounts_dir"], f"{account['name']}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("name", sorted(同席用))
def test_thth_accountが読んで落ちない(同席の2本, name):
    """トークンが無い状態で `no_token` と出る（**落ちない**）。"""
    r = run_thth(["account", name, "--no-remote"])
    assert "Traceback" not in r.stderr, r.stderr
    assert f"（demo / {同席用[name]['media']} /" in r.stdout, r.stdout
    # **表示できたら rc=0**（T3・第 1 回の記録 §3）。トークンを入れる前の台帳は
    # 「まだ投稿できない」を**正常に表示できている**——それを失敗として返すと、
    # 呼んだ側からは道具が落ちたように見える。非ゼロは読めなかったときだけ
    # （rc=2 は台帳が読めない・使い方が違う）。
    assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
    assert "no_token" in r.stdout, r.stdout
    # 投稿できない旨は**本文に残る**。
    assert "投稿できません" in r.stdout, r.stdout


def test_thth_boardが2本を並べて落ちない(同席の2本):
    r = run_thth(["board"])
    assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
    assert "Traceback" not in r.stderr, r.stderr
    行 = {line.split(":", 1)[0]: line for line in r.stdout.splitlines()}
    for name in 同席用:
        assert name in 行, r.stdout
        assert "project=demo" in 行[name]
        assert "token=no_token" in 行[name], 行[name]


def test_他媒体のtokenはgraph_threads_netに行かない(monkeypatch, 同席の2本):
    """**Mastodon の access token を Meta のサーバへ送らない**（T3 で見つけた）。

    **止め方が変わった**（F2・2026-09-13）。以前 `account_report.fetch_posts()` は
    `graph.threads.net` の URL を直に組み立て、`access_token` を**クエリに載せて**
    いた。Mastodon の `.token` も鍵が `access_token` なので、媒体を見ずに通すと
    宛先違いに秘密が出る——当座は `media != "threads"` を名指しで弾いていたが、
    それは**媒体を足すたびにここを見直す**形だった。

    いまは `Adapter.recent_posts()` が境界にあり、**宛先を媒体が決める**。だから
    固定するのも変わる: 「引かない」ではなく「**引きに行く先が Meta ではない**」。
    網には出さない（`urlopen` を差し替えて**宛先だけ**を数える）。
    """
    import urllib.request
    from thth import account_report as account_report_mod

    宛先 = []

    def 記録して落とす(req, *_a, **_k):
        宛先.append(req.full_url if hasattr(req, "full_url") else str(req))
        raise urllib.error.URLError("この試験は網に出ません")
    monkeypatch.setattr(urllib.request, "urlopen", 記録して落とす)

    cfg = _読む(同席の2本["demo-mastodon"])
    rows, message = account_report_mod.fetch_posts(
        cfg, {"access_token": "MASTODON-SECRET", "user_id": "9000"})

    assert rows is None                                  # 引けなかった（網に出ていない）
    assert "MASTODON-SECRET" not in message, message     # 理由文にも出さない
    assert 宛先, "そもそも引きに行っていない（この試験が宛先を見られていない）"
    for url in 宛先:
        assert "graph.threads.net" not in url, url
        assert url.startswith(cfg["instance"]), url
        # トークンは**ヘッダ**に載る（Threads と違ってクエリに出ない）。
        assert "MASTODON-SECRET" not in url, url


def test_recent_postsを持たない媒体には聞きに行かない(monkeypatch):
    """**能力で塞ぐ**（媒体名で分岐しない・`oauth.run_refresh()` と同じ止め方）。

    持たない媒体では**アダプタを組み立てもしない**——`.token` を渡す先を増やさない。
    """
    from thth import account_report as account_report_mod
    from thth.adapters import base as adapter_base

    組み立てた = []

    class 口の無い媒体(adapter_base.Adapter):
        CAPABILITIES = frozenset()
        TOKEN_KEYS = ("access_token",)

        @classmethod
        def from_account(cls, account_cfg, token):
            組み立てた.append(token)
            return cls()

        def recent_posts(self, *, limit=25):
            raise AssertionError("持たない媒体に聞きに行った")

    monkeypatch.setitem(adapters_mod.REGISTRY, "架空", 口の無い媒体)
    rows, message = account_report_mod.fetch_posts(
        {"media": "架空"}, {"access_token": "SECRET"})

    assert rows is None
    assert "架空" in message and "recent_posts" in message, message
    assert "SECRET" not in message, message
    assert 組み立てた == [], "能力の無い媒体でアダプタを組み立てた（token を渡した）"
