"""秘密の宛先: **Mastodon の access token を `graph.threads.net` へ送らない**。

独立監査 1（Opus・まっさらな文脈・v2-3・2026-09-13）の **P1-1**。

`account_report.fetch_posts()` は媒体で塞いであった（`tests/test_media_accounts.py::
test_この口はThreadsのAPIを直に叩くので他媒体では引かない`）。`oauth.run_refresh()` /
`refresh_long_lived_token()` は塞いでいなかった——媒体も能力も見ず、歯止めは
`.token` の中の `no_expiry` という**データの印だけ**。導入文書が示す手置きの形に
その印は無いので、

- `thth refresh masaru-mastodon` を人が打つと、
- **`thth maintain`（04:17 の timer）が人の手なしに毎日**、

Mastodon の access token を `graph.threads.net/refresh_access_token?access_token=…`
へ載せて送り、返ってきた Threads のトークンで `.token` を上書きしていた。

ここで固定するのは 3 つ:

1. **HTTP の口が 1 回も呼ばれない**（「返り値が None」ではなく**来ないこと**を見る
   ——偽の Meta を実際に立てて、着弾を数える）。
2. **`.token` が 1 バイトも変わらない**。
3. **board が「残り 60 日」と嘘をつかない**（`期限なし` と言う）。
"""
from __future__ import annotations

import datetime
import http.server
import json
import os
import threading

import pytest

from thth import jst
from thth import maintain as maintain_mod
from thth import oauth as oauth_mod
from thth import report as report_mod

# **Meta へ渡ってはいけない値**（渡ったら着弾の記録に現れる）。
MASTODON_SECRET = "MASTODON-ACCESS-TOKEN-DO-NOT-SEND-TO-META"
# 取得から 104 日（50 日の更新期限をとうに過ぎている＝更新を試みたくなる齢）。
取得 = "2026-06-01T00:00:00+09:00"
いま = datetime.datetime(2026, 9, 13, 10, 0, tzinfo=jst.JST)


class _偽のMeta(http.server.BaseHTTPRequestHandler):
    """本物の `graph.threads.net` の代わり。**来たものを全部記録する。**"""

    着弾: list = []

    def _go(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8") if n else ""
        type(self).着弾.append({"path": self.path, "body": body,
                                "headers": dict(self.headers)})
        out = json.dumps({"access_token": "META-GA-KAESHITA-TOKEN",
                          "expires_in": 5184000}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_GET = do_POST = _go

    def log_message(self, *a):
        pass


@pytest.fixture
def 偽のMeta(monkeypatch):
    """`THTH_THREADS_BASE_URL` をこの偽サーバに向ける。着弾の一覧を返す。"""
    _偽のMeta.着弾 = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _偽のMeta)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("THTH_THREADS_BASE_URL", f"http://127.0.0.1:{srv.server_port}")
    yield _偽のMeta.着弾
    srv.shutdown()
    srv.server_close()


def _mastodon(factory, tmp_path, *, token_data):
    """`no_expiry` の**無い** `.token` を持つ Mastodon の台帳（手置きの形）。"""
    token_path = tmp_path / "mastodon.token"
    token_path.write_text(json.dumps(token_data, ensure_ascii=False), encoding="utf-8")
    os.chmod(token_path, 0o600)
    account = factory("masaru-mastodon-test", media="mastodon",
                      instance="https://mastodon.example", handle="aoking",
                      token=str(token_path))
    account["token_path"] = token_path
    return account


手置きの形 = {"access_token": MASTODON_SECRET, "obtained_at": 取得,
              "expires_in": 5184000, "user_id": "1", "username": "aoking",
              "scopes": None}
# 導入文書 §「保存先と鍵の名前」がかつて示していた最小の形（`expires_in` すら無い）。
最小の形 = {"access_token": MASTODON_SECRET, "obtained_at": "2026-07-20T00:00:00+09:00"}


@pytest.mark.parametrize("token_data", [手置きの形, 最小の形])
def test_thth_refreshはMastodonのtokenをMetaへ送らない(
        isolated_account_factory, tmp_path, 偽のMeta, token_data):
    account = _mastodon(isolated_account_factory, tmp_path, token_data=token_data)
    前 = account["token_path"].read_bytes()

    rc = oauth_mod.run_refresh(account["name"], log=lambda _line: None, now=いま)

    assert rc == 0, rc
    assert 偽のMeta == [], f"**Threads 以外の媒体で graph.threads.net を叩いた**: {偽のMeta}"
    assert account["token_path"].read_bytes() == 前, "**.token が書き換わった**"


def test_thth_refresh_forceでもcheckでも覆らない(
        isolated_account_factory, tmp_path, 偽のMeta):
    """`--force` は「24 時間の下限」を覆す語であって、**宛先を覆す語ではない**。"""
    account = _mastodon(isolated_account_factory, tmp_path, token_data=手置きの形)
    前 = account["token_path"].read_bytes()
    行: list = []

    for kwargs in ({"force": True}, {"check": True}, {"force": True, "check": True}):
        rc = oauth_mod.run_refresh(account["name"], log=行.append, now=いま, **kwargs)
        assert rc == 0, (kwargs, rc)

    assert 偽のMeta == [], f"**{偽のMeta}**"
    assert account["token_path"].read_bytes() == 前
    # 黙って 0 を返さない——**なぜ何もしなかったか**を言う（loud に断る）。
    まとめ = "\n".join(行)
    assert "更新の口はありません" in まとめ, まとめ
    assert MASTODON_SECRET not in まとめ


@pytest.mark.parametrize("token_data", [手置きの形, 最小の形])
def test_thth_maintainは人の手なしにMetaを叩かない(
        isolated_account_factory, tmp_path, 偽のMeta, token_data):
    """**timer から発火する経路**（04:17 に 1 日 1 回・人は何も打っていない）。"""
    account = _mastodon(isolated_account_factory, tmp_path, token_data=token_data)
    前 = account["token_path"].read_bytes()
    行: list = []

    maintain_mod.run_maintain(account["name"], log=行.append, now=いま)

    assert 偽のMeta == [], f"**timer が Mastodon の token を Meta へ送った**: {偽のMeta}"
    assert account["token_path"].read_bytes() == 前, "**.token が書き換わった**"
    assert MASTODON_SECRET not in "\n".join(行)


@pytest.mark.parametrize("token_data", [手置きの形, 最小の形])
def test_maintainは期限を持たない媒体をokと言う(
        isolated_account_factory, tmp_path, token_data):
    """**媒体が期限を持たない**なら、`.token` の中身に関わらず `ok`・`no_expiry`。

    印（`no_expiry: true`）は `thth token set` が書くもので、**期限が無いという
    事実そのものは媒体の性質**。印の有無で言い分けると、手で置いた `.token` が
    「残り 60 日の Threads のトークン」に化ける。
    """
    account = _mastodon(isolated_account_factory, tmp_path, token_data=token_data)

    row = maintain_mod.inspect(account["name"], now=いま)

    assert row["state"] == maintain_mod.OK, row
    assert row["no_expiry"] is True, row
    assert row["remaining_days"] is None, row
    assert not maintain_mod.needs_attention(row)


@pytest.mark.parametrize("token_data", [手置きの形, 最小の形])
def test_boardは残り日数の嘘をつかない(isolated_account_factory, tmp_path, token_data):
    """`thth board` の行が `token=ok/期限なし`（`残り60日` ではない）。"""
    account = _mastodon(isolated_account_factory, tmp_path, token_data=token_data)

    board = report_mod.board_summary(now=いま)
    行 = {r["account"]: r for r in board["accounts"]}
    row = 行[account["name"]]

    assert row["token_state"] == "ok", row
    assert row["token_no_expiry"] is True, row
    assert row["token_remaining_days"] is None, row


def test_更新の口を持たない媒体は50日を過ぎてもrefresh_dueにならない(
        isolated_account_factory, tmp_path, 偽のMeta):
    """`REFRESH_DUE` は `run_maintain()` が `oauth.run_refresh()` を呼ぶ合図。

    更新の口が無い媒体をそこへ落とすと、**その先が `graph.threads.net`**。
    """
    # **最小の形**（`expires_in` 無し＝既定の 60 日が当たる・取得から 55 日）が
    # ちょうど「50 日超・まだ切れていない」＝ `REFRESH_DUE` に落ちる齢。
    account = _mastodon(isolated_account_factory, tmp_path, token_data=最小の形)

    row = maintain_mod.inspect(account["name"], now=いま)

    assert row["state"] != maintain_mod.REFRESH_DUE, row
    assert 偽のMeta == []


def test_refreshの門は能力で塞ぐ():
    """媒体名で分岐していない（増やすたびにここを直す形にしない）ことの確認。"""
    from thth import adapters as adapters_mod

    assert "refresh" in adapters_mod.capabilities_for("threads")
    assert "refresh" not in adapters_mod.capabilities_for("mastodon")
    assert "refresh" not in adapters_mod.capabilities_for("bluesky")
    # 知らない媒体は空集合＝**塞がる**（誤字が Threads 扱いにならない）。
    assert "refresh" not in adapters_mod.capabilities_for("typo-media")
