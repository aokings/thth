"""**媒体ごとに違う「次の一手」**（独立監査 1・v2-3・**P2-3**・**P2-4**）。

道具の中で言い分が割れていた。`doctor` は媒体を見て `TOKEN_SETUP_HINT` /
`AUTH_NEEDS_APP_ENV` を使うようになっていたのに、

- **P2-3**: `doctor` の `APP_ENV_ABSENT_NOTICE`（「`thth auth` を使うときだけ
  app.env が要ります」）は媒体を見ずに出ていた。Bluesky の `thth auth` は App
  Password を対話で受けるだけで app.env を読まない。
- **P2-4**: `maintain._MESSAGES` の `no_token`・`expired` は Threads 用の
  `thth token set` を焼き付けていて、その文言が **board・`thth account`・
  `thth maintain` の 3 か所**にそのまま出ていた。

**要らない準備を勧める道具は、そこで人の手を止める。**
"""
from __future__ import annotations

import datetime
import json
import os

import pytest

from tests.conftest import run_thth
from thth import account_report as account_report_mod
from thth import adapters as adapters_mod
from thth import jst
from thth import maintain as maintain_mod

いま = datetime.datetime(2026, 9, 13, 10, 0, tzinfo=jst.JST)


def _account(factory, tmp_path, media, **overrides):
    """トークンの**無い**台帳（`no_token` の道を通す）。"""
    kwargs = {"media": media, "token": str(tmp_path / f"{media}-missing.token")}
    if media == "bluesky":
        kwargs["service"] = "https://bsky.invalid"
    if media == "mastodon":
        kwargs["instance"] = "https://mastodon.invalid"
    kwargs.update(overrides)
    return factory(f"masaru-{media}-hint", **kwargs)


# --- P2-3: app.env の案内 ---------------------------------------------------

def test_app_envの案内はauthがそれを要る媒体にだけ出す(
        tmp_path, monkeypatch, isolated_account_factory):
    account = _account(isolated_account_factory, tmp_path, "bluesky")
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(tmp_path / "does-not-exist.env"))

    out = _doctor(account["name"])

    assert "app.env" not in out, "**Bluesky に app.env の話をした**: " + out
    # トークンが無いことは言う（黙るのではなく、**要る話だけをする**）。
    assert "§5" in out or "トークン" in out, out


def test_app_envの案内はThreadsには今までどおり出る(
        tmp_path, monkeypatch, isolated_account_factory):
    """絞り込みが効きすぎて Threads の案内まで消えていないこと（逆側の裏取り）。"""
    account = _account(isolated_account_factory, tmp_path, "threads")
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(tmp_path / "does-not-exist.env"))

    out = _doctor(account["name"])

    assert "app.env: 無し" in out, out
    assert "任意" in out, out


def test_app_envが壊れていれば媒体に関わらず言う(
        tmp_path, monkeypatch, isolated_account_factory):
    """**無い（任意）**と**置いたのに使えない（要修理）**は別の話。

    後者は実在するファイルの異常なので、媒体で黙らせない。
    """
    account = _account(isolated_account_factory, tmp_path, "bluesky")
    broken = tmp_path / "app.env"
    broken.write_text("THREADS_APP_ID=\nTHREADS_APP_SECRET=\n", encoding="utf-8")
    os.chmod(broken, 0o600)
    monkeypatch.setenv("THTH_APP_ENV_PATH", str(broken))

    out = _doctor(account["name"])

    assert "app.env:" in out, out


def _doctor(name):
    r = run_thth(["doctor", name])
    assert "Traceback" not in r.stderr, r.stderr
    return r.stdout + r.stderr


# --- P2-4: トークンの入れ方 -------------------------------------------------

def test_アダプタが次の一手を持っている():
    assert adapters_mod.REGISTRY["threads"].TOKEN_SETUP_HINT == "thth token set"
    assert adapters_mod.REGISTRY["bluesky"].TOKEN_SETUP_HINT == "thth auth"


@pytest.mark.parametrize("media,勧める,勧めない", [
    ("bluesky", "thth auth", "thth token set"),
    ("threads", "thth token set", None),
])
def test_maintainのno_tokenは媒体の次の一手を言う(
        tmp_path, isolated_account_factory, media, 勧める, 勧めない):
    account = _account(isolated_account_factory, tmp_path, media)

    row = maintain_mod.inspect(account["name"], now=いま)

    assert row["state"] == maintain_mod.NO_TOKEN, row
    assert 勧める in row["message"], row["message"]
    if 勧めない:
        assert 勧めない not in row["message"], row["message"]


def test_thth_accountの案内にtoken_setが出ない(tmp_path, isolated_account_factory):
    """**`thth account masaru-bluesky` の画面**（board・maintain と同じ文言）。"""
    account = _account(isolated_account_factory, tmp_path, "bluesky")

    detail = account_report_mod.account_detail(account["name"], now=いま,
                                                remote=False)
    # `--json` の中身と、人向けの画面の**両方**を見る。
    まとめ = (json.dumps(detail, ensure_ascii=False, default=str) + "\n"
              + account_report_mod.render(detail))

    assert "thth auth" in まとめ, まとめ
    assert "thth token set" not in まとめ, "**Bluesky に入らない道を勧めた**: " + まとめ


def test_boardの行も同じ文言を使う(tmp_path, isolated_account_factory):
    from thth import report as report_mod

    account = _account(isolated_account_factory, tmp_path, "bluesky")
    board = report_mod.board_summary(now=いま)
    行 = {r["account"]: r for r in board["accounts"]}[account["name"]]

    assert 行["token_state"] == "no_token", 行
    # board の行そのものには文言が乗らないが、**同じ `inspect()` を読む**ので
    # 判定が一致していることだけ押さえる（文言は maintain のテストが見る）。
    assert maintain_mod.inspect(account["name"], now=いま)["message"].count(
        "thth token set") == 0


def test_token_setの貼り付けの案内は媒体名を言う(monkeypatch):
    """masaru 報告（2026-09-13）: Mastodon の `token set` が「Threads の長期アクセストークン」と
    聞いていた。動きは同じでも、別媒体の秘密を貼らせる画面で媒体名を間違えない。"""
    import sys
    from thth import oauth as oauth_mod
    assert "Mastodon" in oauth_mod._paste_prompt("mastodon")
    assert "Threads" not in oauth_mod._paste_prompt("mastodon")
    assert "Threads" in oauth_mod._paste_prompt("threads")
    assert "bluesky" in oauth_mod._paste_prompt("bluesky")  # 既定の形（媒体名を含む）
    seen = {}
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(oauth_mod.getpass, "getpass", lambda prompt: seen.setdefault("p", prompt) or "x")
    oauth_mod._read_pasted_token(stdin=False, input_func=None, prompt=oauth_mod._paste_prompt("mastodon"))
    assert "Mastodon" in seen["p"] and "Threads" not in seen["p"], seen
