"""セキュリティ監査 2026-09-14 の小さい直し（P2-6・P3-1・P3-3）。

- **P2-6**: `.token` の親ディレクトリを umask 任せ（755）で作っていた。ファイルは
  600 なので中身は読めないが、**どのアカウントのトークンが在るかは見える**。
- **P3-1**: `redact()` は `access_token` の綴りしか知らず、**Bluesky の秘密**
  （`app_password`・`accessJwt`・`refreshJwt`）は素通りしていた。
- **P3-3**: Mastodon の `instance` が `http://` でも通り、`Authorization` が
  平文で網に出た。
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from thth import redact as redact_mod
from thth import secrets_fs
from thth.adapters import mastodon as mastodon_mod


# ------------------------------------------------------------------ P2-6

def test_tokenの親ディレクトリは700で作る(tmp_path):
    path = tmp_path / "新しい置き場" / "nest" / "a.token"
    secrets_fs.atomic_write_json(str(path), {"access_token": "x"})

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    親 = os.path.dirname(str(path))
    assert stat.S_IMODE(os.stat(親).st_mode) == 0o700, "親が 700 でない"
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["access_token"] == "x"


def test_既にある置き場のパーミッションは触らない(tmp_path):
    """運用者が決めたものを勝手に変えない（`atomic_write_text()` と同じ規約）。"""
    d = tmp_path / "既にある"
    d.mkdir()
    os.chmod(d, 0o755)
    secrets_fs.atomic_write_json(str(d / "a.token"), {"access_token": "x"})
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o755


# ------------------------------------------------------------------ P3-1

@pytest.mark.parametrize("鍵", ["app_password", "accessJwt", "refreshJwt"])
def test_blueskyの秘密も伏字になる(鍵):
    値 = "SECRET-VALUE-abc123"
    for 形 in (f'{鍵}={値}', f'"{鍵}": "{値}"', f'{鍵}: {値}'):
        out = redact_mod.redact(f"createSession に失敗: {形} です")
        assert 値 not in out, 形
        assert "***" in out, 形


def test_これまでの伏字は変わらない():
    assert "SECRET" not in redact_mod.redact("access_token=SECRET")
    assert "SECRET" not in redact_mod.redact("Authorization: Bearer SECRET")
    # 巻き込み事故を作らない（普通の語は消さない）。
    assert redact_mod.redact("password strength") == "password strength"


# ------------------------------------------------------------------ P3-3

@pytest.mark.parametrize("instance", [
    "http://mastodon.social",
    "http://mastodon.example",
    "http://192.0.2.10:3000",
])
def test_httpのinstanceは手元以外では断る(instance):
    with pytest.raises(ValueError) as e:
        mastodon_mod._instance_url(instance)
    assert "平文" in str(e.value)


@pytest.mark.parametrize("instance", [
    "http://127.0.0.1:8080",
    "http://localhost:3000",
    "https://mastodon.social",
])
def test_手元の偽サーバとhttpsはこれまでどおり通る(instance):
    assert mastodon_mod._instance_url(instance) == instance


def test_scheme無しはこれまでどおり名指しで断る():
    with pytest.raises(ValueError) as e:
        mastodon_mod._instance_url("mastodon.social")
    assert "scheme" in str(e.value)
