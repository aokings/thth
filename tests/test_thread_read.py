"""`thth thread <account> <post_id>`（T1-2・設計「自分の泉」§2.1）。

芯: **枝はその場で読む。残すのは自分の行為と反応。判断は LLM。** この口は
「読んで見せる」だけ——台帳（`data/`）に 1 バイトも書かない・runs にも本文を
出さない・読めないを空に化かさない。

確かめるもの（T1-2 発注書のとおり）:
  - Bluesky の偽サーバで、根＋返信 5 件（うち自分 1 件・`replied_to` の入れ子
    あり）→ 順序・`depth`・`is_own`・`participants`・`already_replied`
    （絡みの台帳に 1 行・queue に下書きを置く の 2 通り）
  - `max_messages=3` で `truncated` と `continue_from`
  - `data/` に書かないこと（`accounts.data_dirs()` の全 dir が実行前後で不変）
  - runs に本文（`text`・`username`）が無いこと
  - Threads の偽サーバで他人の根が 400 → rc=1・`error` に PermissionMissing
    の文言
"""
from __future__ import annotations

import json
import os

import pytest

from tests.conftest import run_thth, write_queue_file
from tests.test_bluesky_adapter import APP_PASSWORD, DID, HANDLE
from tests.test_bluesky_adapter import _post_view, fake_bluesky
from tests.test_threads_read_permissions import OTHER_POST_ID, _server
from thth.adapters import bluesky as bsky_mod
from thth import accounts as accounts_mod
from thth import engagements as engagements_mod
from thth import runs as runs_mod

ROOT_DID = "did:plc:carol"
ROOT_HANDLE = "carol.bsky.social"
ROOT_URI = f"at://{ROOT_DID}/app.bsky.feed.post/root001"
ROOT_CID = "bafyroot001"

BOB_DID, BOB_HANDLE = "did:plc:bob", "bob.bsky.social"
DAVE_DID, DAVE_HANDLE = "did:plc:dave", "dave.bsky.social"
EVE_DID, EVE_HANDLE = "did:plc:eve", "eve.bsky.social"

R1_URI = f"at://{BOB_DID}/app.bsky.feed.post/r1"       # bob → root（depth 1）
R2_URI = f"at://{DID}/app.bsky.feed.post/r2"           # 自分 → r1（depth 2）
R3_URI = f"at://{DAVE_DID}/app.bsky.feed.post/r3"      # dave → r2（depth 3）
R4_URI = f"at://{EVE_DID}/app.bsky.feed.post/r4"       # eve → root（depth 1・別の枝）
R5_URI = f"at://{BOB_DID}/app.bsky.feed.post/r5"       # bob → r4（depth 2）


def _node(uri, *, did, handle, text, created, replies=None):
    return {
        "$type": "app.bsky.feed.defs#threadViewPost",
        "post": _post_view(uri, "bafy" + uri[-3:], handle=handle, did=did,
                            text=text, created_at=created),
        "replies": replies or [],
    }


def _thread_fixture():
    r3 = _node(R3_URI, did=DAVE_DID, handle=DAVE_HANDLE, text="孫の返信",
               created="2026-09-16T03:00:00.000Z")
    r2 = _node(R2_URI, did=DID, handle=HANDLE, text="自分の返信",
               created="2026-09-16T02:00:00.000Z", replies=[r3])
    r1 = _node(R1_URI, did=BOB_DID, handle=BOB_HANDLE, text="bobの返信",
               created="2026-09-16T01:00:00.000Z", replies=[r2])
    r5 = _node(R5_URI, did=BOB_DID, handle=BOB_HANDLE, text="bobの別の返信",
               created="2026-09-16T02:30:00.000Z")
    r4 = _node(R4_URI, did=EVE_DID, handle=EVE_HANDLE, text="eveの返信",
               created="2026-09-16T01:30:00.000Z", replies=[r5])
    root_view = _post_view(ROOT_URI, ROOT_CID, handle=ROOT_HANDLE, did=ROOT_DID,
                            text="根の投稿", created_at="2026-09-16T00:00:00.000Z")
    return {"$type": "app.bsky.feed.defs#threadViewPost", "post": root_view,
            "replies": [r1, r4]}


def _write_token(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


@pytest.fixture
def bsky_account(tmp_path, thth_root, isolated_account_factory):
    """自分の account（Bluesky・偽 PDS）。queue_dir も持つ（already_replied の queue 側）。"""
    with fake_bluesky(thread=_thread_fixture(),
                      posts={ROOT_URI: _post_view(
                          ROOT_URI, ROOT_CID, handle=ROOT_HANDLE, did=ROOT_DID,
                          text="根の投稿", created_at="2026-09-16T00:00:00.000Z")}) as service:
        token_path = str(tmp_path / "bsky.token")
        _write_token(token_path, {
            "identifier": HANDLE, "app_password": APP_PASSWORD,
            "did": DID, "handle": HANDLE, "no_expiry": True,
            "user_id": DID, "username": HANDLE,
            "obtained_at": "2026-09-16T09:00:00+09:00"})
        account = isolated_account_factory(
            "kopicha-thread-test", media="bluesky", handle=HANDLE,
            service=str(service), token=token_path, production=False)
        yield account, service


def _data_snapshot(account_cfg: dict, account_name: str) -> dict:
    """`accounts.data_dirs()` の全 dir のファイル一覧とサイズ（規約 (a) の検査用）。"""
    snap: dict = {}
    for d in accounts_mod.data_dirs(account_cfg, account_name).values():
        if not os.path.isdir(d):
            continue
        for root, _dirs, names in os.walk(d):
            for name in names:
                path = os.path.join(root, name)
                snap[path] = os.path.getsize(path)
    return snap


def _cli(account_name, post_id, *args, service=None):
    # `service` は台帳（`isolated_account_factory(... service=...)`）にすでに
    # 書いてあるので、ここでは env は要らない（引数は呼び出し側の対称性のため）。
    return run_thth(["thread", account_name, post_id, *args, "--json"])


def test_枝を根から時刻順でdepthとis_ownつきで返す(bsky_account):
    account, service = bsky_account
    cfg = accounts_mod.load_account(account["name"])
    before = _data_snapshot(cfg, account["name"])

    r = _cli(account["name"], ROOT_URI, service=service)
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)

    # 根。
    assert result["root"]["post_id"] == ROOT_URI
    assert result["root"]["username"] == ROOT_HANDLE
    assert result["root"]["is_own"] is False
    assert result["root"]["text"] == "根の投稿"

    # 順序は根から時刻順（r1 01:00 → r4 01:30 → r2 02:00 → r5 02:30 → r3 03:00）。
    ids = [m["message_id"] for m in result["messages"]]
    assert ids == [R1_URI, R4_URI, R2_URI, R5_URI, R3_URI]

    by_id = {m["message_id"]: m for m in result["messages"]}
    assert by_id[R1_URI]["depth"] == 1 and by_id[R1_URI]["replied_to"] == ROOT_URI
    assert by_id[R2_URI]["depth"] == 2 and by_id[R2_URI]["replied_to"] == R1_URI
    assert by_id[R3_URI]["depth"] == 3 and by_id[R3_URI]["replied_to"] == R2_URI
    assert by_id[R4_URI]["depth"] == 1 and by_id[R4_URI]["replied_to"] == ROOT_URI
    assert by_id[R5_URI]["depth"] == 2 and by_id[R5_URI]["replied_to"] == R4_URI

    # is_own（自分の handle は HANDLE。r2 だけが自分）。
    assert by_id[R2_URI]["is_own"] is True
    assert by_id[R1_URI]["is_own"] is False
    assert by_id[R3_URI]["is_own"] is False
    assert by_id[R4_URI]["is_own"] is False
    assert by_id[R5_URI]["is_own"] is False
    assert by_id[R2_URI]["author_key"] == bsky_mod.author_key(DID)

    counts = result["counts"]
    assert counts["messages"] == 5
    # 参加者: carol（根）・bob・own・dave・eve の 5 人。
    assert counts["participants"] == 5
    assert counts["own"] == 1
    assert counts["truncated"] is False

    # **`data/` の下に何も書かない**（規約 (a)）。
    after = _data_snapshot(cfg, account["name"])
    assert after == before


def test_already_repliedは絡みの台帳とqueueの両方から引く(bsky_account, tmp_path):
    account, service = bsky_account
    cfg = accounts_mod.load_account(account["name"])

    # (a) 絡みの台帳: r1（bob の投稿）にはもう返信済み。
    engagements_mod.append(cfg, account["name"], {
        "post_id": "at://" + DID + "/app.bsky.feed.post/myreply1",
        "reply_to": R1_URI, "root_post": ROOT_URI,
        "author_key": bsky_mod.author_key(BOB_DID), "account": account["name"],
        "medium": "bluesky", "topic": None, "kind": None, "hour_band": "朝",
        "posted_at": "2026-09-16T05:00:00+09:00", "found_by": "manual",
    })

    # (b) queue の下書き: r4（eve の投稿）には reply_to 付きの draft がある。
    write_queue_file(account["queue_dir"], "reply-to-r4.md", fm_overrides={
        "account": account["name"], "status": "draft", "reply_to": R4_URI,
        "publish_at": None, "approved_sha": None, "approved_at": None,
    }, commit=False)

    r = _cli(account["name"], ROOT_URI, service=service)
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)
    by_id = {m["message_id"]: m for m in result["messages"]}

    # T7-3: object には `source`（ledger／queue／thread のどこで見つかったか）が付く。
    assert by_id[R1_URI]["already_replied"] == {
        "post_id": "at://" + DID + "/app.bsky.feed.post/myreply1",
        "at": "2026-09-16T05:00:00+09:00", "source": "ledger"}
    assert by_id[R4_URI]["already_replied"] == {"status": "draft", "source": "queue"}
    # まだ絡んでいない相手には`False`（台帳・queueは両方読めているので
    # 「見当たらない」と言い切れる・「返していない」の確定ではない・T7-3）。
    assert by_id[R3_URI]["already_replied"] is False
    assert by_id[R5_URI]["already_replied"] is False


# --- T7-3: already_replied を枝の中の自分の返信からも埋める（3 値の意味） -----
# 設計「自分の泉」§2.1・T7-3 発注書「その枝に is_own: true の返信があり、
# その replied_to がこの message なら、台帳に無くても already_replied を
# 埋める」。`false`＝見当たらない（返していない、の確定ではない）／
# `null`＝台帳か queue が読めず判らない、の 2 つも別々に確かめる。


def test_台帳queueに無くても枝の中の自分の返信からalready_repliedが埋まる(bsky_account):
    account, service = bsky_account
    # 台帳にも queue にも何も無い状態（このテストは追加しない）。
    r = _cli(account["name"], ROOT_URI, service=service)
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)
    by_id = {m["message_id"]: m for m in result["messages"]}

    # R2（自分）は R1（bob）への返信——台帳に無くても枝そのものから拾う。
    assert by_id[R1_URI]["already_replied"] == {
        "post_id": R2_URI, "at": "2026-09-16T02:00:00.000Z", "source": "thread"}
    # R2・R3・R4・R5 には自分からの返信がぶら下がっていない
    # ——台帳・queue も空なので`False`（見当たらない。「返していない」の確定ではない）。
    assert by_id[R2_URI]["already_replied"] is False
    assert by_id[R3_URI]["already_replied"] is False
    assert by_id[R4_URI]["already_replied"] is False
    assert by_id[R5_URI]["already_replied"] is False


def test_台帳が読めなければ見当たらなくてもFalseでなくNoneになる(bsky_account, tmp_path):
    account, service = bsky_account
    cfg = accounts_mod.load_account(account["name"])
    eng_dir = accounts_mod.data_dirs(cfg, account["name"])["engagements"]
    os.makedirs(eng_dir, exist_ok=True)
    # 壊れた ndjson（JSON として読めない行）を 1 本置く——
    # `engagements.load()` はこのファイルを `broken` として中身を捨てる
    # （`thth/engagements.py::_read_ndjson()` と同じ流儀）。
    with open(os.path.join(eng_dir, "2026-09.ndjson"), "w", encoding="utf-8") as f:
        f.write("これは JSON ではありません\n")

    r = _cli(account["name"], ROOT_URI, service=service)
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)
    by_id = {m["message_id"]: m for m in result["messages"]}

    # R1 は枝の中の自分の返信（R2）があるので、台帳が読めなくても object のまま
    # （見つかったものを取り消さない）。
    assert by_id[R1_URI]["already_replied"]["source"] == "thread"
    # どこにも見当たらない行は、`False`（見当たらない）ではなく`None`
    # （「台帳が読めないので判らない」・T7-3 規約）。
    assert by_id[R3_URI]["already_replied"] is None
    assert by_id[R5_URI]["already_replied"] is None
    assert any("絡みの台帳" in reason for reason in result["provenance"]["ledgers_unreadable"])


def test_max_messagesを超えたら新しい側を切りcontinue_fromを返す(bsky_account):
    account, service = bsky_account
    r = _cli(account["name"], ROOT_URI, "--max-messages", "3", service=service)
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)

    ids = [m["message_id"] for m in result["messages"]]
    assert ids == [R1_URI, R4_URI, R2_URI]
    assert result["counts"]["truncated"] is True
    assert result["counts"]["messages"] == 3
    assert result["provenance"]["continue_from"] == "2026-09-16T02:00:00.000Z"


def test_runsに本文が無いこと(bsky_account):
    account, service = bsky_account
    r = _cli(account["name"], ROOT_URI, service=service)
    assert r.returncode == 0, r.stdout + r.stderr

    state_dir = accounts_mod.state_dir_for(account["name"])
    rows = [row for row in runs_mod.read_runs(state_dir) if row.get("action") == "thread_read"]
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["account"] == account["name"]
    assert row["medium"] == "bluesky"
    assert row["post_id"] == ROOT_URI
    assert row["messages"] == 5
    assert row["truncated"] is False
    assert row["status"] == "ok"
    assert row["error"] is None

    # **本文・username が無いこと**（`engagements.FORBIDDEN_KEYS` と同じ、鍵の
    # 完全一致での検査。`row` の鍵はここで列挙した 8 つだけの構造なので、
    # `messages` という鍵が `FORBIDDEN_KEYS` の `message` と部分一致しても
    # 取り違えない——完全一致でしか見ない）。
    hit = sorted(engagements_mod.FORBIDDEN_KEYS & set(row.keys()))
    assert not hit, hit
    # 実測でも本文の綴りがどこにも無いことを見ておく（保険）。
    dumped = json.dumps(row, ensure_ascii=False)
    assert "根の投稿" not in dumped
    assert "bobの返信" not in dumped
    assert HANDLE not in dumped
    assert ROOT_HANDLE not in dumped


def test_you_and_themにlast_reactionがある(bsky_account):
    """T3-2: `you_and_them` の各行に `last_reaction`（`likes`・`replies`）が
    付く。計算は `after_cli.reaction_lookup()` の 1 か所（`who_is_this` と
    同じ）——ここでは実測（24h の刻み）を採っていないので、絡みの台帳に
    行がある相手（bob）でも `null`（**取れていない刻みは 0 と混ぜない**）。
    絡みの台帳に行が無い相手（dave）は `last_reaction` そのものが `None`。
    """
    account, service = bsky_account
    cfg = accounts_mod.load_account(account["name"])
    engagements_mod.append(cfg, account["name"], {
        "post_id": "at://" + DID + "/app.bsky.feed.post/myreply1",
        "reply_to": R1_URI, "root_post": ROOT_URI,
        "author_key": bsky_mod.author_key(BOB_DID), "account": account["name"],
        "medium": "bluesky", "topic": None, "kind": None, "hour_band": "朝",
        "posted_at": "2026-09-16T05:00:00+09:00", "found_by": "manual",
    })

    r = _cli(account["name"], ROOT_URI, service=service)
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)
    you_and_them = {row["author_key"]: row for row in result["you_and_them"]}

    bob_key = bsky_mod.author_key(BOB_DID)
    assert you_and_them[bob_key]["last_reaction"] == {"likes": None, "replies": None}

    dave_key = bsky_mod.author_key(DAVE_DID)
    assert you_and_them[dave_key]["met"] == 0
    assert you_and_them[dave_key]["last_reaction"] is None


# --- T7-1: Threadsの偽サーバで枝を読む（conversation()の生の行をMessageの形に揃える） ---
# `ThreadsAdapter.conversation()`は生の行（`id`・`replied_to: {"id": …}`・
# `root_post: {"id": …}`）を返す（返信の台帳との互換のため adapter 自身は
# 変えない・設計「自分の泉」T7-1発注書）。`thread_read`は`normalize_message()`
# を通してから読むので、Threadsでも`message_id`・`depth`・`already_replied`が
# ちゃんと埋まることをここで確かめる（T1-2はBluesky・Mastodonの偽サーバだけで
# 枝を確かめていた・Threadsは400の経路だけだった、という所見の穴）。

C1_ID, C2_ID, C3_ID = "C1", "C2", "C3"


def _threads_conversation_rows():
    """根（`OTHER_POST_ID`・alice）→ C1（bob）→ C2（nigamilab・自分）→ C3（carol）。"""
    return [
        {"id": C1_ID, "username": "bob", "text": "bobの返信",
         "timestamp": "2026-09-16T01:00:00+0000",
         "replied_to": {"id": OTHER_POST_ID}, "root_post": {"id": OTHER_POST_ID},
         "permalink": "https://t/c1", "has_replies": True, "is_reply": True},
        {"id": C2_ID, "username": "nigamilab", "text": "自分の返信",
         "timestamp": "2026-09-16T02:00:00+0000",
         "replied_to": {"id": C1_ID}, "root_post": {"id": OTHER_POST_ID},
         "permalink": "https://t/c2", "has_replies": True, "is_reply": True},
        {"id": C3_ID, "username": "carol", "text": "孫の返信",
         "timestamp": "2026-09-16T03:00:00+0000",
         "replied_to": {"id": C2_ID}, "root_post": {"id": OTHER_POST_ID},
         "permalink": "https://t/c3", "has_replies": False, "is_reply": True},
    ]


def test_Threadsの枝も根から時刻順でdepthとis_ownつきで返る(isolated_account_factory, tmp_path):
    token_path = str(tmp_path / "threads.token")
    _write_token(token_path, {"access_token": "FAKE-SECRET", "user_id": "999999",
                              "username": "nigamilab", "scopes": None,
                              "obtained_at": "2026-09-16T09:00:00+09:00"})
    account = isolated_account_factory(
        "nigamilab-threads-branch-test", media="threads", handle="nigamilab",
        token=token_path, production=False)

    with _server(conversation_rows=_threads_conversation_rows()) as (base_url, requests):
        r = run_thth(["thread", account["name"], OTHER_POST_ID, "--json"],
                     env={"THTH_THREADS_BASE_URL": base_url})
    assert r.returncode == 0, r.stdout + r.stderr
    result = json.loads(r.stdout)

    assert result["root"]["post_id"] == OTHER_POST_ID
    assert result["root"]["username"] == "alice"

    ids = [m["message_id"] for m in result["messages"]]
    assert ids == [C1_ID, C2_ID, C3_ID], ids   # Threadsの生の`id`から写った

    by_id = {m["message_id"]: m for m in result["messages"]}
    assert by_id[C1_ID]["depth"] == 1 and by_id[C1_ID]["replied_to"] == OTHER_POST_ID
    assert by_id[C2_ID]["depth"] == 2 and by_id[C2_ID]["replied_to"] == C1_ID
    assert by_id[C3_ID]["depth"] == 3 and by_id[C3_ID]["replied_to"] == C2_ID

    # `replied_to`は`{"id": …}`のdictではなく、id文字列に開かれていること。
    for m in result["messages"]:
        assert isinstance(m["replied_to"], str)

    assert by_id[C2_ID]["is_own"] is True
    assert by_id[C1_ID]["is_own"] is False
    assert by_id[C3_ID]["is_own"] is False
    assert by_id[C2_ID]["author_key"] is not None

    counts = result["counts"]
    assert counts["messages"] == 3 and counts["own"] == 1 and counts["participants"] == 4


def test_Threadsで他人の根が400ならPermissionMissingのままrc1(isolated_account_factory, tmp_path):
    with _server({f"/{OTHER_POST_ID}": "permission"}) as (base_url, requests):
        token_path = str(tmp_path / "threads.token")
        _write_token(token_path, {"access_token": "FAKE-SECRET", "user_id": "999999",
                                  "username": "nigamilab", "scopes": None,
                                  "obtained_at": "2026-09-16T09:00:00+09:00"})
        account = isolated_account_factory(
            "nigamilab-thread-test", media="threads", handle="nigamilab",
            token=token_path, production=False)
        r = run_thth(["thread", account["name"], OTHER_POST_ID, "--json"],
                     env={"THTH_THREADS_BASE_URL": base_url})
    assert r.returncode == 1, r.stdout + r.stderr
    payload = json.loads(r.stdout)
    assert payload["permission"] == "threads_basic"
    assert "がトークンに乗っていません" in payload["error"]
    assert "FAKE-SECRET" not in r.stdout + r.stderr

    # **「読めない」を空の枝に化かさない**——rc≠0 で、messages が空配列として
    # 返っているわけではない（そもそも result 本体を返さない）。
    assert "messages" not in payload or payload.get("messages") is None


# --- T6-2: post_id の形違いは adapter を叩く前に rc=2 で断る ------------------
# 被験者が Bluesky の post_id に rkey だけの短い id（`hot1`）を渡して 1 回
# 失敗し、`--json` の `at://` で打ち直した（試験の摩擦）。検査は媒体ごとの
# adapter の classmethod（`Adapter.is_post_id()`）に置き、`thread_read` は
# それを呼んで rc=2・`--json` を促す文言で断る。


def test_Blueskyで短いpost_idはrc2で形を示す(bsky_account):
    account, service = bsky_account
    r = _cli(account["name"], "hot1", service=service)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "at://did" in r.stderr, r.stderr
    assert "--json" in r.stderr, r.stderr


def test_Threadsで形違いのpost_idはrc2で示す(isolated_account_factory, tmp_path):
    token_path = str(tmp_path / "threads.token")
    _write_token(token_path, {"access_token": "FAKE-SECRET", "user_id": "999999",
                              "username": "nigamilab", "scopes": None,
                              "obtained_at": "2026-09-16T09:00:00+09:00"})
    account = isolated_account_factory(
        "nigamilab-thread-test", media="threads", handle="nigamilab",
        token=token_path, production=False)
    r = run_thth(["thread", account["name"], ".", "--json"])
    assert r.returncode == 2, r.stdout + r.stderr
    assert "数字の id" in r.stderr, r.stderr
    assert "--json" in r.stderr, r.stderr


def test_Mastodonで形違いのpost_idはrc2で示す(isolated_account_factory, tmp_path):
    token_path = str(tmp_path / "mstdn.token")
    _write_token(token_path, {"access_token": "FAKE-SECRET", "no_expiry": True,
                              "user_id": "9000", "username": "nigamilab",
                              "obtained_at": "2026-09-16T09:00:00+09:00"})
    account = isolated_account_factory(
        "nigamilab-mastodon-test", media="mastodon", handle="nigamilab",
        instance="https://mastodon.invalid", token=token_path, production=False)
    r = run_thth(["thread", account["name"], ".", "--json"])
    assert r.returncode == 2, r.stdout + r.stderr
    assert "数字の id" in r.stderr, r.stderr
    assert "--json" in r.stderr, r.stderr
