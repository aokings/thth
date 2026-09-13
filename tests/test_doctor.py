"""`thth doctor`（統括が 2026-09-09 に追加）。

管理画面の表示とトークンの実力が一致しないことが分かったので、実際に叩いて測る
道具を持った。ここで確かめるのは 2 つ:
  - **トークンの値が出力に出ない**こと（診断のたびに漏れては本末転倒）
  - **読み取りしか呼ばない**こと（doctor が副作用を持つと「様子を見るつもりが出た」が起きる）
"""
from __future__ import annotations

import json

from tests.helpers.fake_oauth_server import fake_oauth_server
from thth import doctor as doctor_mod


def _write_token(path, token="DOCTOR-SECRET-TOKEN"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"access_token": token, "obtained_at": "2026-09-09T00:00:00+09:00",
                   "expires_in": 5184000, "user_id": "999999",
                   "username": "nigamilab", "scopes": None}, f)


def test_20260909_doctor_must_not_print_the_token(tmp_path, monkeypatch, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    account = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path)

    with fake_oauth_server() as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        lines = []
        doctor_mod.run_doctor(account["name"], log=lines.append)

    out = "\n".join(lines)
    assert "DOCTOR-SECRET-TOKEN" not in out
    assert "nigamilab" in out


def test_doctor_は書き込みの口を持たない():
    """副作用を持つ語（publish・delete・POST）が doctor に無いことを機械で見る。"""
    import inspect
    import re
    src = inspect.getsource(doctor_mod)
    # `threads_publishing_limit`（読み取り）に引っかからないよう、語の切れ目まで見る。
    forbidden = [
        r"threads_publish(?!ing)",   # 公開
        r"method=\"POST\"",          # 書き込み
        r"manage_reply",             # 返信の作成・非表示
        r"threads_delete",           # 削除
    ]
    for pat in forbidden:
        assert re.search(pat, src) is None, f"doctor に書き込みらしき語がある: {pat}"
    # 唯一の HTTP 呼び出しが GET（データを持たない urlopen）であること。
    assert src.count("urllib.request.urlopen(") == 1
    assert "data=" not in src


def test_トークンが無ければその旨を返す(tmp_path, isolated_account_factory):
    account = isolated_account_factory(token=str(tmp_path / "missing.token"))
    lines = []
    rc = doctor_mod.run_doctor(account["name"], log=lines.append)
    assert rc == 2
    assert "トークンが無い" in "\n".join(lines)


def test_投稿があると返信のprobeが実際にリクエストを送る(tmp_path, monkeypatch,
                                                isolated_account_factory):
    """バグ 1: ラベルの文字列比較（`== "自分の投稿一覧"`）が注記付きラベルと
    一致せず、`first_post_id` が常に None のまま「返信の取得」probe が一度も
    HTTP を叩かなかった事故（2026-09-10 のラベル変更で直し忘れ）。

    `key="my_posts"` での突き合わせに直したので、投稿が実在すれば返信 probe が
    実際にリクエストを送ることを、送られたパスを記録して確かめる。
    """
    token_path = str(tmp_path / "a.token")
    account = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path)

    requested_paths = []

    def fake_get(base_url, path, params, token):
        requested_paths.append(path)
        if path.endswith("/threads"):
            return {"data": [{"id": "POST123", "permalink": "https://example/p",
                               "timestamp": "2026-09-10T00:00:00+0000"}]}
        if path.endswith("/conversation"):
            return {"data": [{"id": "R1", "username": "someone",
                               "timestamp": "2026-09-10T01:00:00+0000"}]}
        return {}

    monkeypatch.setattr(doctor_mod, "_get", fake_get)
    report = doctor_mod.diagnose(account["name"])

    reply_paths = [p for p in requested_paths if p.endswith("/conversation")]
    assert reply_paths == ["/v1.0/POST123/conversation"], (
        "返信の取得 probe が実際に HTTP リクエストを送っていない: "
        f"{requested_paths}")

    reply_probe = next(p for p in report["probes"]
                        if p["permission"] == "threads_read_replies")
    assert reply_probe["ok"] is True


# --- T3 の配線（2026-09-13）: トークンの有無を媒体の鍵で判定する ----------------

def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def test_T3_Blueskyのトークンはidentifierとapp_passwordで判定する(
        tmp_path, isolated_account_factory):
    """**`access_token` の有無で判定しない**（`TOKEN_KEYS`）。

    以前は `token.get("access_token")` 固定だったので、`thth auth` で正しく
    認可した Bluesky が「トークンが無い」と言われた——`.token` に入るのは
    `identifier` と `app_password` で、`access_token` はどこにも無い。
    """
    token_path = str(tmp_path / "bsky.token")
    account = isolated_account_factory(
        media="bluesky", handle="aoking.bsky.social",
        service="https://bsky.invalid", token=token_path)
    _write_json(token_path, {"identifier": "aoking.bsky.social",
                             "app_password": "aaaa-bbbb-cccc-dddd",
                             "no_expiry": True,
                             "obtained_at": "2026-09-13T00:00:00+09:00"})

    report = doctor_mod.diagnose(account["name"])
    # 「トークンが無い」で早期 return **しない**（先へ進んで probe を試す）。
    assert "トークンが無い" not in (report.get("error") or "")
    assert report["probes"], report
    # 偽のホストなので createSession は × になる。**それでも値は出さない。**
    assert report["probes"][0]["ok"] is False
    assert "aaaa-bbbb-cccc-dddd" not in json.dumps(report, ensure_ascii=False)


def test_T3_Blueskyはトークンが無いときthth_authを勧める(
        tmp_path, isolated_account_factory):
    """次の一手が媒体で違う（`TOKEN_SETUP_HINT`）。`thth token set` では入らない。"""
    account = isolated_account_factory(
        media="bluesky", handle="aoking.bsky.social",
        token=str(tmp_path / "missing.token"))
    report = doctor_mod.diagnose(account["name"])
    assert report["error"] == "トークンが無い（thth auth を先に）"
    assert report["probes"] == []


def test_T3_Blueskyはidentifierだけでは足りない(tmp_path, isolated_account_factory):
    """**両方揃って初めて「在る」**（片方だけの `.token` は認可されていない）。"""
    token_path = str(tmp_path / "half.token")
    account = isolated_account_factory(media="bluesky", token=token_path)
    _write_json(token_path, {"identifier": "aoking.bsky.social"})
    assert doctor_mod.diagnose(account["name"])["error"] == \
        "トークンが無い（thth auth を先に）"


def test_T3_Mastodonとthreadsの文言は現行のまま(tmp_path, isolated_account_factory):
    """**Threads の出力は現行と同じ**（T-B5 の但し書き）。"""
    threads = isolated_account_factory(token=str(tmp_path / "m1.token"))
    assert doctor_mod.diagnose(threads["name"])["error"] == \
        "トークンが無い（thth token set を先に）"
    mastodon = isolated_account_factory(
        "mstdn", media="mastodon", instance="https://mastodon.invalid",
        token=str(tmp_path / "m2.token"))
    assert doctor_mod.diagnose(mastodon["name"])["error"] == \
        "トークンが無い（thth token set を先に）"


def test_T3_知らない媒体はトークンの有無より先に断る(tmp_path, isolated_account_factory):
    """`media` の誤字が「トークンが無い」という**別の理由**に化けない（T-B0）。"""
    account = isolated_account_factory(media="carrier-pigeon",
                                        token=str(tmp_path / "missing.token"))
    error = doctor_mod.diagnose(account["name"])["error"]
    assert "carrier-pigeon" in error and "知りません" in error
    assert "トークンが無い" not in error
