"""独立監査 1（Opus・まっさらな文脈・v2-3・2026-09-13）の残り。

- **P3-5** `inbox`: `timestamp` の無いメッセージが月をまたぐと 2 度書かれる
- **P3-6** 極端に長い `post_id` で `collect` が `OSError` のまま落ちる
- **P3-7** Mastodon の `author_key` が `instance` の綴りと `acct` の大小で変わる
- **P3-8** `identifier` だけの Bluesky `.token` で doctor と board の言い分が違う
- **M6** `postid.from_filename()` の戻しを、**消費側**から見張るものが無い
"""
from __future__ import annotations

import datetime
import json
import os

import pytest

from tests.helpers import fake_push_adapter
from thth import collect as collect_mod
from thth import jst
from thth import maintain as maintain_mod
from thth import measured as measured_mod
from thth import postid as postid_mod
from thth.adapters import mastodon as mastodon_mod

MEDIUM = fake_push_adapter.MEDIUM
いま = datetime.datetime(2026, 9, 13, 10, 0, tzinfo=jst.JST)


# =========================================================== P3-5 inbox 冪等
@pytest.fixture
def 偽の媒体を登録する(monkeypatch):
    from thth import adapters as adapters_mod
    reg = dict(adapters_mod.REGISTRY)
    reg[MEDIUM] = fake_push_adapter.FakePushAdapter
    monkeypatch.setattr(adapters_mod, "REGISTRY", reg)
    return MEDIUM


def _inbox_rows(repo_dir, month):
    path = os.path.join(repo_dir, "data", "sns", "inbox", f"{month}.ndjson")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _push_account(factory, medium):
    return factory("pushfake-account", media=medium, handle="pushfake")


def test_timestampの無いメッセージは月をまたいでも1度しか書かれない(
        isolated_account_factory, 偽の媒体を登録する):
    """`_inbox_month()` は `timestamp` が無いと**「いま」の月**に落とす。

    重複除去がその月のファイルの中だけだったので、月末に採って翌月にもう一度
    採ると、同じ `message_id` が 2 つのファイルに 1 行ずつ入っていた。
    **追記専用の台帳で同じ id が 2 行あると、数え直したときに 2 件になる。**
    """
    account = _push_account(isolated_account_factory, 偽の媒体を登録する)
    msg = fake_push_adapter.message("NOSTAMP", timestamp=None)

    九月 = datetime.datetime(2026, 9, 30, 23, 0, tzinfo=jst.JST)
    十月 = datetime.datetime(2026, 10, 1, 1, 0, tzinfo=jst.JST)
    for now in (九月, 十月):
        collect_mod.collect_once(
            account["name"], now=now, log=lambda _l: None,
            adapter=fake_push_adapter.FakePushAdapter(messages=[msg]))

    九 = _inbox_rows(account["repo_dir"], "2026-09")
    十 = _inbox_rows(account["repo_dir"], "2026-10")
    assert len(九) + len(十) == 1, \
        f"**同じ message_id が 2 度書かれた**: 09={九} / 10={十}"


def test_timestampのあるメッセージは今までどおり月ごとに分かれる(
        isolated_account_factory, 偽の媒体を登録する):
    """絞りすぎて**別の人の別のメッセージ**まで落としていないこと（逆側）。"""
    account = _push_account(isolated_account_factory, 偽の媒体を登録する)
    collect_mod.collect_once(
        account["name"], now=いま, log=lambda _l: None,
        adapter=fake_push_adapter.FakePushAdapter(messages=[
            fake_push_adapter.message("A", timestamp="2026-08-31T23:00:00+09:00"),
            fake_push_adapter.message("B", timestamp="2026-09-01T01:00:00+09:00"),
        ]))

    assert [r["message_id"] for r in _inbox_rows(account["repo_dir"], "2026-08")] == ["A"]
    assert [r["message_id"] for r in _inbox_rows(account["repo_dir"], "2026-09")] == ["B"]


def test_同じ実行を2度走らせても増えない(isolated_account_factory, 偽の媒体を登録する):
    account = _push_account(isolated_account_factory, 偽の媒体を登録する)
    for _ in range(2):
        collect_mod.collect_once(
            account["name"], now=いま, log=lambda _l: None,
            adapter=fake_push_adapter.FakePushAdapter(
                messages=[fake_push_adapter.message("M1")]))
    assert len(_inbox_rows(account["repo_dir"], "2026-09")) == 1


# ================================================ P3-6 長すぎる post_id
def test_長すぎるpost_idは台帳の鍵にしない():
    """encode **後**のバイト数で見る（`quote()` は 1 文字を最大 12 倍に膨らます）。"""
    assert postid_mod.is_usable("1" * 200)
    assert not postid_mod.is_usable("1" * 201)
    # 絵文字は 1 文字 12 バイト（`%F0%9F%98%80`）。**encode 前の長さでは判らない。**
    assert not postid_mod.is_usable("😀" * 17)
    # 既存の形は今までどおり通る。
    assert postid_mod.is_usable("17900000000000000")
    assert postid_mod.is_usable("at://did:plc:abcdefghijklmnop/app.bsky.feed.post/3lxyz")


def test_長すぎるpost_idでcollectが落ちない(tmp_path, isolated_account_factory,
                                        偽の媒体を登録する):
    """**1 本の異常で採取が丸ごと落ちない**（採取は部分的な成功を許す）。

    以前はここで `open()` が `OSError: File name too long` を上げ、例外が
    `collect_once()` の外まで抜けていた。
    """
    from tests.conftest import write_queue_file

    account = _push_account(isolated_account_factory, 偽の媒体を登録する)
    長い = "at://did:plc:x/app.bsky.feed.post/" + "z" * 300
    write_queue_file(
        account["queue_dir"], "long.md", media=MEDIUM,
        fm_overrides={"account": account["name"], "status": "posted",
                      "post_id": 長い,
                      "posted_at": "2026-09-13T09:00:00+09:00"},
        body=f"## {MEDIUM}\n\n出した本文です。\n")

    out = collect_mod.collect_once(
        account["name"], now=いま, log=lambda _l: None,
        adapter=fake_push_adapter.FakePushAdapter(messages=[]))

    assert any("ファイル名に使えません" in e for e in out["errors"]), out["errors"]
    # **書きかけのファイルを残さない。**
    posts_dir = os.path.join(account["repo_dir"], "data", "sns", "insights", "posts")
    assert not os.path.isdir(posts_dir) or not os.listdir(posts_dir)


# ============================================ P3-7 Mastodon の author_key
def _mastodon(instance):
    return mastodon_mod.MastodonAdapter(instance=instance, access_token="x")


同じ人になるべき = [
    ("https://mastodon.social", "aoking"),
    ("https://mastodon.social/", "aoking"),          # 末尾 `/`
    ("https://Mastodon.Social", "aoking"),           # 大文字
    ("https://mastodon.social:443", "aoking"),       # 既定 port
    ("https://mastodon.social", "Aoking@mastodon.social"),   # acct の大小
    ("https://mastodon.social", "aoking@MASTODON.SOCIAL"),   # ドメインの大小
    ("https://mastodon.social", "@aoking"),          # 先頭 `@`
]


@pytest.mark.parametrize("instance,acct", 同じ人になるべき)
def test_綴りが違っても同じ人は同じ鍵(instance, acct):
    """鍵は `sha256` で**戻せない**。集めたあとで直せないので、集める前にそろえる。"""
    基準 = _mastodon("https://mastodon.social").author_key("aoking")
    assert _mastodon(instance).author_key(acct) == 基準, (instance, acct)


def test_別の人は別の鍵のまま():
    """そろえすぎて別人を同じ鍵にしていないこと（逆側の裏取り）。"""
    基準 = _mastodon("https://mastodon.social").author_key("aoking")
    assert _mastodon("https://fedibird.com").author_key("aoking") != 基準
    assert _mastodon("https://mastodon.social").author_key("other") != 基準
    # 既定でない port は**別のサーバ**として残す（落とすのは 443／80 だけ）。
    assert _mastodon("https://mastodon.social:8443").author_key("aoking") != 基準


def test_名前が無ければ鍵も無い():
    a = _mastodon("https://mastodon.social")
    assert a.author_key("") is None
    assert a.author_key(None) is None
    assert a.qualified_acct("  ") is None


# ==================================== P3-8 鍵の足りない .token の言い分
def _bluesky(factory, tmp_path, token_data):
    token_path = tmp_path / "bsky.token"
    token_path.write_text(json.dumps(token_data), encoding="utf-8")
    os.chmod(token_path, 0o600)
    return factory("masaru-bluesky-incomplete", media="bluesky",
                    handle="aoking.bsky.social", service="https://bsky.invalid",
                    token=str(token_path))


def test_identifierだけのtokenはdoctorと同じ物差しで見る(
        tmp_path, isolated_account_factory):
    """`thth auth` を途中で止めた／手で書いた `.token`。

    doctor は媒体の `TOKEN_KEYS` で「トークンが無い」と言っていたのに、
    `maintain.inspect()` は dict が返れば「在る」として先へ進み、
    `TOKEN_NO_EXPIRY` の枝で **`ok`／`期限なし`** と言っていた。
    """
    account = _bluesky(isolated_account_factory, tmp_path,
                       {"identifier": "aoking.bsky.social",
                        "obtained_at": "2026-09-13T00:00:00+09:00",
                        "no_expiry": True})

    row = maintain_mod.inspect(account["name"], now=いま)

    assert row["state"] == maintain_mod.TOKEN_INCOMPLETE, row
    assert maintain_mod.needs_attention(row), row
    # **欠けている鍵の名前を言う**（「足りない」だけでは何を足すか判らない）。
    assert "app_password" in row["message"], row["message"]
    assert "identifier" not in row["message"], row["message"]
    # 媒体の次の一手（P2-4 と同じ物差し）。
    assert "thth auth" in row["message"], row["message"]


def test_doctorとboardの言い分がそろう(tmp_path, isolated_account_factory):
    from thth import doctor as doctor_mod
    from thth import report as report_mod

    account = _bluesky(isolated_account_factory, tmp_path,
                       {"identifier": "aoking.bsky.social",
                        "obtained_at": "2026-09-13T00:00:00+09:00"})

    診断 = doctor_mod.diagnose(account["name"])
    行 = {r["account"]: r for r in report_mod.board_summary(now=いま)["accounts"]
          }[account["name"]]

    assert "トークンが無い" in (診断.get("error") or ""), 診断
    # **board も「問題なし」とは言わない。**
    assert 行["token_state"] == maintain_mod.TOKEN_INCOMPLETE, 行


def test_鍵が揃っていれば今までどおり(tmp_path, isolated_account_factory):
    account = _bluesky(isolated_account_factory, tmp_path,
                       {"identifier": "aoking.bsky.social",
                        "app_password": "abcd-efgh-ijkl-mnop",
                        "obtained_at": "2026-09-13T00:00:00+09:00",
                        "no_expiry": True})
    row = maintain_mod.inspect(account["name"], now=いま)
    assert row["state"] == maintain_mod.OK and row["no_expiry"] is True, row


# ======================================= M6 ファイル名の往復を消費側で見る
def test_at_uriのpost_idは書いて読んで一致する(tmp_path, isolated_account_factory):
    """`postid.to_filename()` / `from_filename()` の**往復を消費側から**見る。

    単体（`to_filename` → `from_filename`）は既に見ているが、**書いた台帳を
    `measured.load()` が読み直したときに同じ `post_id` に戻る**ことを見張る
    ものが無かった（独立監査 1・M6）。`from_filename()` の `unquote()` を
    落としても、台帳を読む側が誰も気づかない状態だった。
    """
    account = isolated_account_factory("masaru-bluesky-m6", media="bluesky",
                                        handle="aoking.bsky.social",
                                        service="https://bsky.invalid")
    post_id = "at://did:plc:abcdefg/app.bsky.feed.post/3lxyzabc"
    posts_dir = os.path.join(account["repo_dir"], "data", "sns", "insights", "posts")
    os.makedirs(posts_dir, exist_ok=True)
    名前 = postid_mod.to_filename(post_id)
    # ファイル名に区切りが残っていない（＝ディレクトリを掘っていない）。
    assert "/" not in 名前 and ":" not in 名前, 名前
    with open(os.path.join(posts_dir, f"{名前}.ndjson"), "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "account": account["name"], "medium": "bluesky", "post_id": post_id,
            "collected_at": "2026-09-13T10:00:00+09:00", "age_hours": 1.0,
            "marks": ["1h"], "metrics": {"likes": 3},
            "posted_at": "2026-09-13T09:00:00+09:00", "topic": None,
        }, ensure_ascii=False) + "\n")

    出た = measured_mod.load(account["name"])

    assert [p["post_id"] for p in 出た["posts"]] == [post_id], 出た


def test_ファイル名から戻せない値は台帳の鍵にしない():
    """往復が成り立つ範囲だけを鍵に使う（`is_usable` が入口）。"""
    for post_id in ("at://did:plc:x/app.bsky.feed.post/1", "17900000000000000",
                     "語を含む id", "a%2Fb"):
        assert postid_mod.is_usable(post_id), post_id
        assert postid_mod.from_filename(postid_mod.to_filename(post_id)) == post_id
