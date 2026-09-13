"""端から端（Bluesky・Mastodon）——`throw` → 書き戻し → `collect` → 返信の台帳。

**本物の API は 1 つも叩かない。** 台帳の `service`／`instance` を偽サーバに向け、
`tests/test_bluesky_adapter.py`・`tests/test_mastodon_adapter.py` の偽サーバを
そのまま使う（`tests/test_fake_api.py` の型）。

確かめたいのは**配線**であって媒体の実装ではない（そちらは T1・T2 のテスト）:

  queue の `## bluesky` / `## mastodon` の節を承認済みで置く
    → `thth throw <account> --now`（`production: true` は**この試験の台帳だけ**）
    → front-matter に `post_id` が書き戻る
    → `thth collect <account>`
    → 返信の ndjson に `message_id`・`medium`・`author_key` の行が入る

**`REGISTRY` に 1 行足すだけで core を変えずに通る**ことの証明でもある——ここが
落ちるなら、境界のどこかが媒体名を知ってしまっている。
"""
from __future__ import annotations

import datetime
import json
import os

from tests.conftest import write_queue_file
from tests.test_bluesky_adapter import (APP_PASSWORD, DID, HANDLE, _post_view,
                                        _strong, fake_bluesky)
from tests.test_mastodon_adapter import TOKEN as MASTODON_TOKEN
from tests.test_mastodon_adapter import CONTEXT_FIXTURE, fake_mastodon
from thth import collect as collect_mod
from thth import core
from thth import postid as postid_mod
from thth import queuefile as queuefile_mod

# 静かな時間帯（22:00〜07:00）の外・既定の `publish_at`（08:00）より後。
NOW = datetime.datetime.fromisoformat("2026-09-13T10:00:00+09:00")
# 採取はその 2 時間後（1h の刻みを跨いでいる・6h はまだ）。
COLLECT_NOW = NOW + datetime.timedelta(hours=2)

# 偽の Bluesky が `createRecord` で作る 1 本目の uri（`new1`）。
NEW_URI = f"at://{DID}/app.bsky.feed.post/new1"
NEW_CID = "bafynew"
返信の作者 = "reader.bsky.social"
返信のdid = "did:plc:reader"


def _write_token(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.chmod(path, 0o600)


def _queue(account, *, media, body=None):
    """`## <media>` の節だけを持つ承認済みの原稿を 1 本置く（commit・push まで）。

    **宛先（`account`）を明示する。** conftest の既定は Threads のアカウントなので、
    そのままだと select が「自分宛てではない」として黙って見送る。
    """
    return write_queue_file(
        account["queue_dir"], "a.md", media=media,
        fm_overrides={"account": account["name"]},
        body=body if body is not None else f"## {media}\n\n{media} に出す本文です。\n")


def _front_matter(path):
    with open(path, encoding="utf-8") as f:
        return queuefile_mod.parse_text(f.read(), path).front_matter


def _reply_rows(repo_dir, post_id):
    path = os.path.join(repo_dir, "data", "sns", "replies",
                        f"{postid_mod.to_filename(post_id)}.ndjson")
    assert os.path.exists(path), f"返信の台帳が無い: {path}"
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _insight_row(repo_dir, post_id):
    path = os.path.join(repo_dir, "data", "sns", "insights", "posts",
                        f"{postid_mod.to_filename(post_id)}.ndjson")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()][0]


def _thread_with_one_reply():
    """`getPostThread` の応答（うちの投稿に返信が 1 件付いた形・**L2** の union）。"""
    reply_uri = f"at://{返信のdid}/app.bsky.feed.post/r1"
    return {
        "$type": "app.bsky.feed.defs#threadViewPost",
        "post": _post_view(NEW_URI, NEW_CID, handle=HANDLE, did=DID,
                            text="bluesky に出す本文です。",
                            created_at="2026-09-13T01:00:00.000Z",
                            counts={"likeCount": 4, "replyCount": 1,
                                    "repostCount": 1, "quoteCount": 0}),
        "replies": [{
            "$type": "app.bsky.feed.defs#threadViewPost",
            "post": _post_view(
                reply_uri, "bafyr1", handle=返信の作者, did=返信のdid,
                text="読みました", created_at="2026-09-13T02:00:00.000Z",
                reply={"root": _strong(NEW_URI, NEW_CID),
                       "parent": _strong(NEW_URI, NEW_CID)}),
            "replies": [],
        }],
    }


# ------------------------------------------------------------------ Bluesky
def test_端から端_bluesky(tmp_path, isolated_account_factory):
    thread = _thread_with_one_reply()

    with fake_bluesky(posts={NEW_URI: thread["post"]}, thread=thread) as service:
        token_path = str(tmp_path / "bsky.token")
        _write_token(token_path, {"identifier": HANDLE, "app_password": APP_PASSWORD,
                                   "did": DID, "handle": HANDLE, "no_expiry": True,
                                   "user_id": DID, "username": HANDLE,
                                   "obtained_at": "2026-09-13T09:00:00+09:00"})
        account = isolated_account_factory(
            "masaru-bluesky-test", media="bluesky", handle=HANDLE,
            service=str(service), token=token_path,
            # **`production: true` はこの試験の台帳だけ**（repo の
            # `accounts/masaru-bluesky.json` は `false` のまま）。
            production=True)
        path = _queue(account, media="bluesky")

        # --- 投げる
        result = core.throw_once(account["name"], production_flag=True, now=NOW)
        assert result.exit_code == 0, result

        # --- post_id が書き戻る
        fm = _front_matter(path)
        assert fm["status"] == "posted"
        # **`post_id` は AT URI**（`/` と `:` を含む）。台帳のファイル名は
        # percent-encode されるが、front-matter に入るのは URI そのもの
        # ——`getPostThread` に渡すのがこれだから（`thth/postid.py`）。
        assert fm["post_id"] == NEW_URI, fm

        # --- 採る
        rc = collect_mod.run_collect(account["name"], now=COLLECT_NOW,
                                      log=lambda _l: None)
        assert rc == 0

    rows = _reply_rows(account["repo_dir"], NEW_URI)
    返信 = [r for r in rows if r.get("kind") == "reply"]
    assert len(返信) == 1, rows
    行 = 返信[0]
    # **境界の 3 つ**（設計 v2 §4.2）が台帳に入っている。
    assert 行["message_id"] == f"at://{返信のdid}/app.bsky.feed.post/r1"
    assert 行["medium"] == "bluesky"
    assert 行["author_key"], 行
    # **非可逆**（did も handle も戻らない）。
    assert 返信のdid not in 行["author_key"]
    assert 返信の作者 not in 行["author_key"]
    assert 行["root_post"] == NEW_URI and 行["replied_to"] == NEW_URI
    # 「取れて 0 件」と「取っていない」を分ける行も残る。
    assert [r for r in rows if r.get("kind") == "fetch"]

    # --- 実測の行（**views は無い媒体**・設計 v2 §4.2「採集と実測の媒体差」）
    実測 = _insight_row(account["repo_dir"], NEW_URI)
    assert 実測["medium"] == "bluesky"
    assert 実測["metrics"]["likes"] == 4
    # **`views: null`——捨てずに数える**（`comparable_views` が理由付きで除外する）。
    assert 実測["metrics"]["views"] is None, 実測["metrics"]


# ----------------------------------------------------------------- Mastodon
MASTODON_POST_ID = "110000000000000099"


def test_端から端_mastodon(tmp_path, isolated_account_factory):
    with fake_mastodon() as fake:
        token_path = str(tmp_path / "mstdn.token")
        _write_token(token_path, {"access_token": MASTODON_TOKEN, "no_expiry": True,
                                   "user_id": "9000", "username": "nigamilab",
                                   "obtained_at": "2026-09-13T09:00:00+09:00"})
        account = isolated_account_factory(
            "masaru-mastodon-test", media="mastodon", handle="nigamilab",
            instance=fake.instance, token=token_path, production=True)
        path = _queue(account, media="mastodon")

        result = core.throw_once(account["name"], production_flag=True, now=NOW)
        assert result.exit_code == 0, result
        fm = _front_matter(path)
        assert fm["status"] == "posted"
        assert fm["post_id"] == MASTODON_POST_ID, fm

        rc = collect_mod.run_collect(account["name"], now=COLLECT_NOW,
                                      log=lambda _l: None)
        assert rc == 0

        host = fake.instance.split("//", 1)[-1]

    rows = _reply_rows(account["repo_dir"], MASTODON_POST_ID)
    返信 = [r for r in rows if r.get("kind") == "reply"]
    # `/context` の `descendants` 3 件がそのまま台帳へ（全階層）。
    assert [r["message_id"] for r in 返信] == \
        [d["id"] for d in CONTEXT_FIXTURE["descendants"]], rows
    for 行 in 返信:
        assert 行["medium"] == "mastodon"
        assert 行["author_key"], 行

    # **ドメイン付きの acct から決まる**（別インスタンスの同名を同じ人にしない）。
    from thth.adapters import base as adapter_base
    assert 返信[0]["author_key"] == adapter_base.author_key("mastodon", f"alice@{host}")
    assert 返信[1]["author_key"] == adapter_base.author_key("mastodon",
                                                            "bob@other.invalid")
    assert 返信[0]["author_key"] == 返信[2]["author_key"]   # 同じ人
    assert "alice" not in 返信[0]["author_key"]             # 非可逆
    assert [r for r in rows if r.get("kind") == "fetch"]

    実測 = _insight_row(account["repo_dir"], MASTODON_POST_ID)
    assert 実測["medium"] == "mastodon"
    assert 実測["metrics"]["likes"] == 7
    assert 実測["metrics"]["views"] is None, 実測["metrics"]


# ------------------------------------------------------- 節と媒体の食い違い
def test_節が媒体名でなければ投げない(tmp_path, isolated_account_factory):
    """queue の節は媒体名（設計 v2 §4.2）。`## threads` の原稿は Bluesky に出ない。

    1 つの queue ファイルを Threads と Bluesky の 2 account が拾う形
    （設計 v1 §8-16）を守るには、**自分の節が無ければ投げない**のが正しい。
    """
    with fake_bluesky() as service:
        token_path = str(tmp_path / "bsky.token")
        _write_token(token_path, {"identifier": HANDLE, "app_password": APP_PASSWORD})
        account = isolated_account_factory(
            "bsky-section", media="bluesky", handle=HANDLE,
            service=str(service), token=token_path, production=True)
        _queue(account, media="bluesky",
               body="## threads\n\nThreads 用の本文です。\n")
        result = core.throw_once(account["name"], production_flag=True, now=NOW)

    assert result.action != "post", result
    # **1 本も投げていない。**
    assert service.handler_cls.created == []


# ------------------------------------------------------- post_id とファイル名
def test_post_idをそのままパスにしない():
    """Bluesky の `post_id` は AT URI（`/` と `:` を含む・`thth/postid.py`）。

    **Threads の既存のファイル名は 1 文字も変わらない**——追記専用の台帳を
    改名せずに済むのが、percent-encoding を選んだ理由。
    """
    assert postid_mod.to_filename("17916074118445631") == "17916074118445631"
    assert postid_mod.from_filename("17916074118445631") == "17916074118445631"

    名 = postid_mod.to_filename(NEW_URI)
    assert "/" not in 名 and ":" not in 名
    assert postid_mod.from_filename(名) == NEW_URI

    # **外へ出る値は弾く**（`.`／`..`／空／NUL）。区切りはもう弾かない。
    assert postid_mod.is_usable(NEW_URI)
    assert not postid_mod.is_usable("..")
    assert not postid_mod.is_usable("")
    assert not postid_mod.is_usable("a\x00b")
    assert not postid_mod.is_usable(None)


def test_sentの記録もencodeしたファイル名で書く(tmp_path):
    """公開に成功した直後の**正本**（`state/<account>/sent/`）。

    ここが落ちると、Bluesky は**公開に成功したのに書き戻せない**
    （`FileNotFoundError`）——出たのに出ていないことになる、いちばん悪い落ち方。
    """
    from thth import sent as sent_mod

    state_dir = str(tmp_path / "state")
    path = sent_mod.write(state_dir, post_id=NEW_URI, text="本文", body_hash="h",
                           sent_at="2026-09-13T10:00:00+09:00")
    assert os.path.isfile(path)
    assert os.path.dirname(path) == sent_mod.dir_for(state_dir)
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["post_id"] == NEW_URI
