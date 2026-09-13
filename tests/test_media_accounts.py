"""同席用の台帳 2 本（Bluesky・Mastodon・T3 の配線 2026-09-13）。

masaru の各 1 アカウント（無料）を `accounts/` に置いた。**まだ稼働させない**
（`production: false`・`scheduled: false`）ので、投稿は `thth send --production`
を人が打ったときだけ出る。ここで確かめるのは 2 つ:

- **台帳の形が正しい**（媒体ごとの追加項目・秘密の置き場・未稼働の印）。
- **`thth account` と `thth board` が 2 本を読んで落ちない**（トークンが無い
  状態で `no_token` と出る）。台帳を足しただけで既存の画面が落ちると、
  masaru が現物を試す前に手が止まる。

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
ACCOUNTS = REPO_ROOT / "accounts"

新しい台帳 = ["masaru-bluesky", "masaru-mastodon"]


def _load(name):
    with open(ACCOUNTS / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("name", 新しい台帳)
def test_台帳が読めて既知の媒体を指している(name):
    cfg = _load(name)
    assert cfg["account"] == name
    assert cfg["project"] == "masaru"
    # **知らない媒体なら loud に断られる**（T-B0）。ここが通ることが「登録できた」
    # ことの証明でもある。
    adapters_mod.adapter_class(cfg["media"])


@pytest.mark.parametrize("name", 新しい台帳)
def test_まだ稼働させない印が両方立っている(name):
    """**未稼働**（masaru の指示 2026-09-13）。timer も持たず、本番でもない。

    `production: false` なら `--production` を付けても出ない。
    `scheduled: false` なら `thth systemd` が timer を作らない。
    **どちらか片方だけだと、うっかり出る／うっかり回る。**
    """
    cfg = _load(name)
    assert cfg["production"] is False, cfg
    assert cfg["scheduled"] is False, cfg


@pytest.mark.parametrize("name", 新しい台帳)
def test_秘密はプロジェクトの外に置く(name):
    """`env`・`token` は `~/.config/thth/` （repo の中に秘密を置かない）。"""
    cfg = _load(name)
    assert cfg["env"] == f"~/.config/thth/{name}.env"
    assert cfg["token"] == f"~/.config/thth/{name}.token"
    # 値そのものは台帳に入らない（**鍵の名前だけ**）。
    raw = json.dumps(cfg, ensure_ascii=False)
    assert "app_password" not in raw and "access_token" not in raw


def test_blueskyの台帳はserviceとhandleを持つ():
    """媒体ごとの追加項目（設計 v2 §4.2「台帳と登録」）。"""
    cfg = _load("masaru-bluesky")
    assert cfg["media"] == "bluesky"
    assert cfg["handle"] == "aoking.bsky.social"
    assert cfg["service"] == "https://bsky.social"
    # 台帳から実際に組み立てられる（トークンが無くても組み立ては通る）。
    adapter = adapters_mod.make_adapter(cfg, {})
    assert adapter.service == "https://bsky.social"


def test_mastodonの台帳はinstanceとhandleを持つ():
    """**`instance` は必須**——既定に落とすと、書き忘れた人が知らないサーバに投げる。"""
    cfg = _load("masaru-mastodon")
    assert cfg["media"] == "mastodon"
    assert cfg["handle"] == "aoking"
    assert cfg["instance"] == "https://mastodon.social"
    adapter = adapters_mod.make_adapter(cfg, {"access_token": ""})
    assert adapter.instance == "https://mastodon.social"


def _トークンがまだ無い(name) -> bool:
    """masaru が `thth auth` を打ったあとの手元でも、この試験が嘘にならないように。

    トークンを入れたら `no_token` でなくなるのが**正しい**——そこまで固定すると、
    現物を試した瞬間にテストが落ちる。
    """
    return not os.path.exists(os.path.expanduser(_load(name)["token"]))


@pytest.mark.parametrize("name", 新しい台帳)
def test_thth_accountが読んで落ちない(name):
    """トークンが無い状態で `no_token` と出る（**落ちない**）。"""
    r = run_thth(["account", name, "--no-remote"])
    assert "Traceback" not in r.stderr, r.stderr
    assert f"（masaru / {_load(name)['media']} /" in r.stdout, r.stdout
    if _トークンがまだ無い(name):
        # 投稿できない状態なので rc=1（**2 は台帳が読めない・使い方が違う**）。
        assert r.returncode == 1, f"{r.returncode}: {r.stdout}{r.stderr}"
        assert "no_token" in r.stdout, r.stdout


def test_thth_boardが2本を並べて落ちない():
    r = run_thth(["board"])
    assert r.returncode == 0, f"{r.returncode}: {r.stdout}{r.stderr}"
    assert "Traceback" not in r.stderr, r.stderr
    行 = {line.split(":", 1)[0]: line for line in r.stdout.splitlines()}
    for name in 新しい台帳:
        assert name in 行, r.stdout
        assert "project=masaru" in 行[name]
        if _トークンがまだ無い(name):
            assert "token=no_token" in 行[name], 行[name]


def test_この口はThreadsのAPIを直に叩くので他媒体では引かない(monkeypatch):
    """**Mastodon の access token を Meta のサーバへ送らない**（T3 で見つけた）。

    `account_report.fetch_posts()` は `graph.threads.net` の URL を直に組み立て、
    `access_token` を**クエリに載せる**。Mastodon の `.token` も鍵が
    `access_token` なので、媒体を見ずに通すと**宛先違いに秘密が出る**。
    """
    import urllib.request
    from thth import account_report as account_report_mod

    # **叩いたら即落ちる**（開発セッションの変異で、止めを外しても通ったため）。
    # 止めを外すと本物の graph.threads.net へ秘密つきの URL が飛ぶ形になるので、
    # 「返り値が None」ではなく「HTTP の口が一度も呼ばれない」を固定する。
    def 叩いてはいけない(*_a, **_k):
        raise AssertionError("Threads 以外の媒体で graph.threads.net を叩いた（秘密が宛先違いに出る）")
    monkeypatch.setattr(urllib.request, "urlopen", 叩いてはいけない)

    rows, message = account_report_mod.fetch_posts(
        _load("masaru-mastodon"), {"access_token": "MASTODON-SECRET", "user_id": "1"})
    assert rows is None
    assert "mastodon" in message and "引けません" in message
    assert "MASTODON-SECRET" not in message
