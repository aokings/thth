"""境界の拡張（設計 v2 §4.2）の受け入れ: T-B0・T-B1・T-B4・T-B5。

T-B2（Bluesky）・T-B3（Mastodon）は別 Track（T1・T2）なのでここには無い。

**本物の API は 1 つも叩かない。** 使うのは `tests/helpers/fake_push_adapter.py`
の偽の push 型アダプタと、doctor の取得口の差し替えだけ。
"""
from __future__ import annotations

import datetime
import json
import os

import pytest

from tests.conftest import make_queue_text, parse_verified, write_queue_file
from tests.helpers import fake_push_adapter
from thth import account_report as account_report_mod
from thth import adapters as adapters_mod
from thth import cli as cli_mod
from thth import collect as collect_mod
from thth import core as core_mod
from thth import doctor as doctor_mod
from thth import jst
from thth import maintain as maintain_mod
from thth import oauth as oauth_mod
from thth import queuefile as queuefile_mod
from thth import select as select_mod
from thth.adapters import base as adapter_base
from thth.adapters import threads as threads_mod

MEDIUM = fake_push_adapter.MEDIUM

# 実測を採るには「出してから 24 時間以上」が要る（`collect.due_marks`）。
# conftest の `frozen_now_jst` は 2026-09-09 10:00 に固定するので、採取の刻みを
# 跨がせたいテストだけ明示的に `now` を渡す（**時刻依存を根から断つ**）。
NOW = datetime.datetime(2026, 9, 13, 10, 0, tzinfo=jst.JST)
POSTED_AT = "2026-09-12T08:00:00+09:00"


@pytest.fixture
def 偽の媒体を登録する(monkeypatch):
    """`REGISTRY` に 1 行足すだけ——**core も collect も触らない**（T-B1 の骨子）。"""
    monkeypatch.setitem(adapters_mod.REGISTRY, MEDIUM,
                        fake_push_adapter.FakePushAdapter)
    return MEDIUM


# =============================================================================
# T-B0  未知の `media` は、既知の一覧を添えて loud に断る
# =============================================================================

def test_TB0_未知のmediaは既知の一覧を添えて断る():
    with pytest.raises(adapter_base.UnknownMedium) as e:
        adapters_mod.make_adapter({"account": "x", "media": "carrier-pigeon"}, {})
    文 = str(e.value)
    assert "carrier-pigeon" in 文, f"何を知らなかったのかが出ていない: {文}"
    assert "threads" in 文, f"**知っている媒体の一覧が出ていない**: {文}"
    assert "REGISTRY" in 文, f"どこに足せばよいかが出ていない: {文}"


def test_TB0_coreも同じ口を通る():
    """**変異検出**: `core._default_adapter_factory` を `ThreadsAdapter` の直生成に
    戻すと、未知の媒体が黙って Threads として組み立てられ、ここが落ちる。"""
    with pytest.raises(adapter_base.UnknownMedium):
        core_mod._default_adapter_factory({"account": "x", "media": "mastodon-ish"},
                                           {"access_token": "T"})


def test_TB0_知っている媒体はREGISTRYのクラスで組み立てる(monkeypatch):
    monkeypatch.delenv("THTH_THREADS_BASE_URL", raising=False)
    adapter = core_mod._default_adapter_factory(
        {"account": "a", "media": "threads", "user_id": "123"},
        {"access_token": "T", "user_id": "999"})
    assert isinstance(adapter, threads_mod.ThreadsAdapter)
    # 旧 `_default_adapter_factory` の約束（T2a 検収）は保つ。
    assert adapter.user_id == "999"


def test_TB0_偽の媒体はREGISTRYに足すだけで通る(偽の媒体を登録する):
    adapter = core_mod._default_adapter_factory({"account": "a", "media": MEDIUM}, {})
    assert isinstance(adapter, fake_push_adapter.FakePushAdapter)
    assert adapters_mod.capabilities_for(MEDIUM) == {"inbox"}


def test_TB0_capabilitiesは実体を作らずに引ける():
    """`select` はトークンを読まずに判定する（設計 v2 §4.2・受け入れ 6）。"""
    assert "topic" in adapters_mod.capabilities_for("threads")
    assert "topic" not in adapters_mod.capabilities_for("carrier-pigeon")
    # `account_insights` は T3 の配線（2026-09-13）で足した語。**Threads だけ**が
    # 持つ——`collect._collect_account_daily()` はこれを見て、持たない媒体では
    # 呼ばない（以前は毎回 `errors` に積んでいた・T0 の残件）。
    # `recent_posts` は F2 の配線（2026-09-13）で足した語。**3 媒体とも持つ**が、
    # 宛先は媒体が決める（`account_report.fetch_posts()` はこの語で塞ぐだけ）。
    # `keyword_search`・`mentions`・`profile_lookup`・`inbox` は v2.1-A（設計 v2
    # §4.3・2026-09-14）で足した語。Threads の 11 権限を使う読み取りの口 3 つと、
    # 言及を `inbox` に流す配管（v2-3 の芽がそのまま受け皿）。
    assert threads_mod.ThreadsAdapter.capabilities() == {
        "topic", "link_preview", "views", "quota", "refresh", "recent_posts",
        "account_insights", "keyword_search", "mentions", "profile_lookup", "inbox"}


def test_F3_metrics_ofは新しい形だけを受ける():
    """`{"metrics", "available"}` を開く（設計 v2 §4.2）。"""
    metrics, available = adapter_base.metrics_of(
        {"metrics": {"likes": 2}, "available": ["likes", "replies"]})
    assert metrics == {"likes": 2}
    assert available == ["likes", "replies"]
    # `available` は空でもよい（**「何も持たない媒体」は「判らない」ではない**）。
    assert adapter_base.metrics_of({"metrics": {}, "available": []}) == ({}, [])


@pytest.mark.parametrize("戻り", [
    {"views": 10, "likes": 3},                     # 旧い平の dict
    {},                                             # 空の dict も旧い形
    {"metrics": {"likes": 1}},                      # available が無い
    {"available": ["likes"]},                       # metrics が無い
    {"metric": {"likes": 1}, "available": ["likes"]},   # 鍵の綴り間違い
    {"metrics": {"likes": 1}, "availables": ["likes"]},  # 同上
    {"metrics": [("likes", 1)], "available": ["likes"]},  # metrics が dict でない
    None,
    [{"likes": 1}],
])
def test_F3_metrics_ofは旧い形をloudに断る(戻り):
    """**黙って通さない**（規約 5）。

    以前は鍵が揃っていなければ辞書全体を指標とみなし、`available` を `None` に
    していた。**その `None` が「埋めない」経路**で、「そもそも媒体に無い指標」と
    「今回取れなかった指標」が台帳で同じ形になっていた。綴りを間違えた新しい
    アダプタも、戻り全体が指標として通っていた。

    `AdapterError` は `RuntimeError` の子なので、採取側の `except Exception` に
    そのまま乗る——**1 本のアダプタの不備で投稿は止まらず**、`errors` に残る。
    """
    with pytest.raises(adapter_base.AdapterError) as e:
        adapter_base.metrics_of(戻り)
    assert "available" in str(e.value), str(e.value)


def test_F3_旧い形を返すアダプタは採取のerrorsに出て投稿は止まらない(
        tmp_path, isolated_account_factory):
    """loud の届き先を固定する（**例外を握り潰さない・採取ごと落とさない**）。"""
    account = isolated_account_factory()
    write_queue_file(account["queue_dir"], "a.md", fm_overrides={
        "status": "posted", "post_id": "P1", "posted_at": POSTED_AT})

    class _旧い形(threads_mod.ThreadsAdapter):
        def __init__(self):
            pass

        def insights(self, post_id):
            return {"views": 12, "likes": 1}     # 旧い平の dict

        def conversation(self, post_id, *, since=None):
            return []

        def account_insights(self, user_id, *, since, until):
            return {}

    result = collect_mod.collect_once(account["name"], adapter=_旧い形(), now=NOW)
    assert any("insights" in e and "available" in e for e in result["errors"]), \
        result["errors"]
    # **実測の行は書かない**（旧い形を「取れた」ことにしない）。
    path = os.path.join(account["repo_dir"], "data", "sns", "insights", "posts",
                        "P1.ndjson")
    assert not os.path.exists(path), path


def test_TB0_Messageは旧名Replyでも作れる():
    """`Reply = Message` の別名（設計 v2 §4.2）。"""
    assert adapter_base.Reply is adapter_base.Message
    m = adapter_base.Message(message_id="R1", username="u", text="t",
                              timestamp="2026-09-13T00:00:00+09:00",
                              replied_to=None, root_post=None)
    assert (m.medium, m.author_key, m.reply_deadline) == (None, None, None)


def test_TB0_author_keyは非可逆で媒体ごとに違う():
    a = adapter_base.author_key("threads", "masaru")
    b = adapter_base.author_key(MEDIUM, "masaru")
    assert a and b and a != b, "媒体が違えば別の鍵（媒体をまたいで同一人物にしない）"
    assert "masaru" not in a and len(a) == 16
    assert adapter_base.author_key("threads", None) is None


def test_TB0_conversationはmediumとauthor_keyを足すが既存の鍵は変えない():
    """**返信の台帳の鍵は変えない・足すだけ**（設計 v2 §4.2）。"""
    class _口(threads_mod.ThreadsAdapter):
        def __init__(self):
            pass

        def _get(self, path, params):
            return {"data": [{"id": "R1", "username": "someone", "text": "やあ",
                               "timestamp": "2026-09-12T10:00:00+0000",
                               "replied_to": {"id": "P1"}, "root_post": {"id": "P1"}}]}

    row = _口().conversation("P1")[0]
    for 旧鍵 in ("id", "username", "text", "timestamp", "replied_to", "root_post"):
        assert 旧鍵 in row, f"既存の鍵 `{旧鍵}` が消えている"
    assert row["medium"] == "threads"
    assert row["author_key"] == adapter_base.author_key("threads", "someone")


def _select_1件(tmp_path, *, media: str, topic: str, char_limit=None):
    """`select` に 1 本だけ流して、落ちた理由を返す（時刻の関門より手前を見る）。"""
    path = tmp_path / "a.md"
    path.write_text(make_queue_text(
        fm_overrides={"account": "a", "topic": topic,
                       "publish_at": "2026-09-09T08:00:00+09:00"},
        body=f"## {media}\n\n本文です。\n", media=media), encoding="utf-8")
    account_cfg = {"media": media, "hashtags": False, "quiet_hours": None,
                   "min_interval_hours": 0, "stale_days": 3650}
    if char_limit is not None:
        account_cfg["char_limit"] = char_limit
    result = select_mod.select_one(
        [parse_verified(str(path))], account_name="a", account_cfg=account_cfg,
        now=datetime.datetime(2026, 9, 9, 10, 0, tzinfo=jst.JST),
        last_post_at=None, recent_texts=set())
    return result, [r.reason for r in result.rejections]


def test_topicを持たない媒体では検査しない(tmp_path, 偽の媒体を登録する):
    """**変異検出**: `capabilities` を見ずに常に検査する形に戻すと落ちる。

    1 つの queue を Threads と Bluesky の 2 account が拾う形（設計 v1 §8-16）で、
    **同じ原稿が媒体によって落ちる**のを避ける（設計 v2 §4.2）。
    """
    result, 理由 = _select_1件(tmp_path, media=MEDIUM, topic="苦味.コーヒー")
    assert not any(r.startswith("topic_") for r in 理由), 理由
    assert result.chosen is not None, 理由


def test_threadsは今までどおりtopicを検査する(tmp_path):
    result, 理由 = _select_1件(tmp_path, media="threads", topic="苦味.コーヒー")
    assert any(r.startswith("topic_") for r in 理由), 理由
    assert result.chosen is None


def test_char_limitで媒体の既定を上書きできる(tmp_path, 偽の媒体を登録する):
    """Mastodon のようにインスタンスで上限が違う媒体のため（設計 v2 §4.2）。"""
    assert queuefile_mod.MEDIA_LIMITS["bluesky"] == 300
    assert queuefile_mod.MEDIA_LIMITS["mastodon"] == 500
    assert queuefile_mod.limit_for("mastodon", {"char_limit": 1000}) == 1000
    # 壊れた上書き（0・負・文字列）は黙って採らない。
    assert queuefile_mod.limit_for("mastodon", {"char_limit": 0}) == 500
    assert queuefile_mod.limit_for("mastodon", {"char_limit": "たくさん"}) == 500

    _, 理由 = _select_1件(tmp_path, media=MEDIUM, topic="", char_limit=3)
    assert any(r.startswith("too_long(") for r in 理由), 理由


# =============================================================================
# T-B1  偽の push 型アダプタを REGISTRY に足すだけで inbox が ndjson に落ちる
# =============================================================================

def _push_account(factory, 偽の媒体):
    return factory("pushfake-account", media=偽の媒体, handle="pushfake")


def _inbox_rows(repo_dir: str, month: str = "2026-09") -> list:
    path = os.path.join(repo_dir, "data", "sns", "inbox", f"{month}.ndjson")
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def test_TB1_inboxがndjsonに落ち期限が残る(tmp_path, isolated_account_factory,
                                    偽の媒体を登録する):
    account = _push_account(isolated_account_factory, 偽の媒体を登録する)
    adapter = fake_push_adapter.FakePushAdapter(messages=[
        fake_push_adapter.message("M1"),
        fake_push_adapter.message("M2", reply_deadline="2026-09-14T12:00:00+09:00"),
    ])

    out = collect_mod.collect_once(account["name"], adapter=adapter)

    rows = _inbox_rows(account["repo_dir"])
    assert [r["message_id"] for r in rows] == ["M1", "M2"], rows
    # **24 時間の会話窓が行に残る**（設計 v2 §4.1・§4.2）。
    assert rows[1]["reply_deadline"] == "2026-09-14T12:00:00+09:00"
    assert rows[0]["medium"] == MEDIUM
    assert rows[0]["author_key"] and "customer" not in rows[0]["author_key"]
    # 利用者から始まった会話なので根が無い。
    assert rows[0]["root_post"] is None
    assert all(r["kind"] == "inbox" and r["collected_at"] for r in rows)
    # 書いたファイルは push の対象に入る（`run_collect` が commit する一覧）。
    assert any(p.endswith(os.path.join("inbox", "2026-09.ndjson"))
               for p in out["touched"]), out["touched"]
    assert not out["errors"], out["errors"]


def test_TB1_二度走らせても増えない(tmp_path, isolated_account_factory,
                              偽の媒体を登録する):
    """**冪等**（`message_id` で重複を除く）。"""
    account = _push_account(isolated_account_factory, 偽の媒体を登録する)

    def 一回():
        collect_mod.collect_once(account["name"], adapter=fake_push_adapter.FakePushAdapter(
            messages=[fake_push_adapter.message("M1"),
                       fake_push_adapter.message("M1"),   # 同じ応答の中の重複も除く
                       fake_push_adapter.message("M2")]))

    一回()
    一回()
    assert [r["message_id"] for r in _inbox_rows(account["repo_dir"])] == ["M1", "M2"]


def test_TB1_inboxを持たない媒体には聞かない(tmp_path, isolated_account_factory):
    """`inbox` を持たない媒体には呼ばない（`capabilities()` に無い）。

    **Bluesky で見る**（2026-09-14・v2.1-A）。以前は Threads で見ていたが、Threads は
    言及（`threads_manage_mentions`）を `inbox` に流すようになった（設計 v2 §4.3）。
    Threads のままだと `inbox()` が呼ばれて `AssertionError` が `collect` の
    `errors` に飲まれ、**呼ばれているのに通る**——見たい性質が見えなくなる。
    """
    from thth.adapters import bluesky as bluesky_mod
    account = isolated_account_factory(media="bluesky")

    class _呼ばれたら失敗(bluesky_mod.BlueskyAdapter):
        def __init__(self):
            pass

        def inbox(self, *, since=None):
            raise AssertionError("**inbox を持たない媒体に聞いている**")

        def account_insights(self, user_id, *, since, until):
            return {}

    assert "inbox" not in _呼ばれたら失敗.capabilities()
    result = collect_mod.collect_once(account["name"], adapter=_呼ばれたら失敗())
    assert not any(e.startswith("inbox") for e in result["errors"]), result["errors"]
    assert not os.path.isdir(os.path.join(account["repo_dir"], "data", "sns", "inbox"))


def test_TB1_届いた月ごとに分けて書く(tmp_path, isolated_account_factory,
                              偽の媒体を登録する):
    account = _push_account(isolated_account_factory, 偽の媒体を登録する)
    collect_mod.collect_once(account["name"], adapter=fake_push_adapter.FakePushAdapter(
        messages=[fake_push_adapter.message("M1", timestamp="2026-08-31T23:00:00+09:00"),
                   fake_push_adapter.message("M2", timestamp="2026-09-01T01:00:00+09:00")]))
    assert [r["message_id"] for r in _inbox_rows(account["repo_dir"], "2026-08")] == ["M1"]
    assert [r["message_id"] for r in _inbox_rows(account["repo_dir"], "2026-09")] == ["M2"]


def test_TB1_message_idの無い行は書かずに数える(tmp_path, isolated_account_factory,
                                     偽の媒体を登録する):
    account = _push_account(isolated_account_factory, 偽の媒体を登録する)
    壊れた = fake_push_adapter.message("")
    out = collect_mod.collect_once(account["name"], adapter=fake_push_adapter.FakePushAdapter(
        messages=[壊れた, fake_push_adapter.message("M2")]))
    assert [r["message_id"] for r in _inbox_rows(account["repo_dir"])] == ["M2"]
    assert any("message_id" in e for e in out["errors"]), out["errors"]


# =============================================================================
# T-B4  views を持たない媒体の実測は「媒体に views が無い」として数える
# =============================================================================

def _観測(**overrides) -> dict:
    o = {"views": None, "mark": 24, "age_hours": 24.5,
         "collected_at": "2026-09-13T09:00:00+09:00", "post_id": "P1",
         "source": account_report_mod.LEDGER_SOURCE,
         "topic_source": account_report_mod.DRAFT_TOPIC,
         "medium": MEDIUM}
    o.update(overrides)
    return o


def test_TB4_媒体にviewsが無い実測は理由つきで数える(偽の媒体を登録する):
    使う, 使わない = account_report_mod.comparable_views([_観測()])
    assert 使う == []
    assert len(使わない) == 1, "**捨てている**（数えていない）"
    理由 = 使わない[0]["理由"]
    assert account_report_mod.NO_VIEWS_REASON in 理由, 理由
    assert MEDIUM in 理由, f"どの媒体の話かが出ていない: {理由}"


def test_TB4_採れていないだけの行とは理由を分ける():
    """**変異検出**: 除外理由を 1 本にまとめると、ここが落ちる。

    views を持つ媒体（threads）で欠けているだけなら「次の刻みで入りうる」。
    媒体に views が無いのとは別の話なので、同じ理由文にしない。
    """
    _, 使わない = account_report_mod.comparable_views([_観測(medium="threads")])
    assert account_report_mod.NO_VIEWS_REASON not in 使わない[0]["理由"]


def _実測を書く(repo_dir: str, *, topic: str, medium: str, metrics: dict,
              account: str, post_id: str = "P1") -> None:
    path = os.path.join(repo_dir, "data", "sns", "insights", "posts", f"{post_id}.ndjson")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "post_id": post_id, "file": "a.md", "account": account, "medium": medium,
            "topic": topic, "collected_at": "2026-09-13T09:00:00+09:00",
            "posted_at": "2026-09-12T08:00:00+09:00", "age_hours": 25.0,
            "marks": [24], "metrics": metrics}, ensure_ascii=False) + "\n")


def test_TB4_topic_planが理由つきで数えadviseが落ちない(
        tmp_path, capsys, isolated_account_factory, 偽の媒体を登録する):
    account = _push_account(isolated_account_factory, 偽の媒体を登録する)
    write_queue_file(account["queue_dir"], "a.md", fm_overrides={
        "account": account["name"], "status": "posted", "post_id": "P1",
        "posted_at": POSTED_AT, "topic": "苦味"},
        body=f"## {MEDIUM}\n\n本文です。\n")
    _実測を書く(account["repo_dir"], topic="苦味", medium=MEDIUM,
              metrics={"likes": 2, "replies": 1, "views": None},
              account=account["name"])

    plan = account_report_mod.topic_plan(account["name"])
    row = next(r for r in plan["topics"] if r["topic"] == "苦味")
    assert row["measured_posts"] == 0
    assert row["views_median_24h"] is None
    理由 = [x["理由"] for x in row["not_compared"]]
    assert any(account_report_mod.NO_VIEWS_REASON in r for r in 理由), 理由

    # **`--advise` が落ちない**（受け入れ T-B4）。
    rc = cli_mod._advise(account["name"], as_json=True)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["account"] == account["name"]


def test_TB4_採取はavailableに無い指標をnullで書く(tmp_path, isolated_account_factory,
                                        偽の媒体を登録する):
    """**変異検出**: `available` を見ずに metrics をそのまま書くと、行に `views` が
    現れず「媒体に views が無い」と「まだ採っていない」が混ざる。"""
    account = _push_account(isolated_account_factory, 偽の媒体を登録する)
    write_queue_file(account["queue_dir"], "a.md", fm_overrides={
        "account": account["name"], "status": "posted", "post_id": "P1",
        "posted_at": POSTED_AT}, body=f"## {MEDIUM}\n\n本文です。\n")

    collect_mod.collect_once(account["name"], now=NOW,
                              adapter=fake_push_adapter.FakePushAdapter())

    path = os.path.join(account["repo_dir"], "data", "sns", "insights", "posts",
                        "P1.ndjson")
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    assert rows, "実測が 1 行も書かれていない"
    assert rows[0]["medium"] == MEDIUM
    metrics = rows[0]["metrics"]
    # **鍵はあるが値は `null`**——「媒体に views が無い」を、欄ごと消して
    # 「まだ採っていない」に化けさせない。
    assert "views" in metrics and metrics["views"] is None, metrics
    assert metrics["likes"] == 2


def test_TB4_threadsの実測は今までどおり(tmp_path, isolated_account_factory):
    """Threads は `available` に views があるので、行の形は変わらない。"""
    account = isolated_account_factory()
    write_queue_file(account["queue_dir"], "a.md", fm_overrides={
        "status": "posted", "post_id": "P1", "posted_at": POSTED_AT})

    class _数だけ(threads_mod.ThreadsAdapter):
        def __init__(self):
            pass

        def insights(self, post_id):
            return {"metrics": {"views": 12, "likes": 1},
                    "available": list(threads_mod.POST_METRICS)}

        def conversation(self, post_id, *, since=None):
            return []

        def account_insights(self, user_id, *, since, until):
            return {}

    collect_mod.collect_once(account["name"], adapter=_数だけ(), now=NOW)
    path = os.path.join(account["repo_dir"], "data", "sns", "insights", "posts",
                        "P1.ndjson")
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    assert rows[0]["metrics"] == {"views": 12, "likes": 1}, rows[0]["metrics"]
    assert rows[0]["medium"] == "threads"


# =============================================================================
# トークン: 「判らない」と「期限を持たない」を混ぜない（設計 v2 §4.2）
# =============================================================================

def _write_no_expiry_token(path, *, obtained_at="2026-01-01T00:00:00+09:00"):
    """Bluesky の App Password 相当（期限が無い・`expires_in` を持たない）。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"access_token": "NO-EXPIRY-TOKEN", "obtained_at": obtained_at,
                   "no_expiry": True, "user_id": "1", "username": "u"}, f)


def test_期限を持たないトークンはremaining_daysがNone():
    now = datetime.datetime(2026, 9, 13, 10, 0, tzinfo=jst.JST)
    age, remaining = oauth_mod.token_age_and_remaining(
        {"obtained_at": "2026-01-01T00:00:00+09:00", "no_expiry": True}, now)
    assert remaining is None and age > 0


def test_expires_inが書いてあれば期限が無いとは言わない():
    """**変異検出**: `no_expiry` を `expires_in` より優先すると落ちる。"""
    now = datetime.datetime(2026, 9, 13, 10, 0, tzinfo=jst.JST)
    _, remaining = oauth_mod.token_age_and_remaining(
        {"obtained_at": "2026-09-12T10:00:00+09:00", "no_expiry": True,
         "expires_in": 5184000}, now)
    assert remaining is not None and 58 < remaining < 60


def test_maintainは期限なしをokと言い更新しない(tmp_path, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    account = isolated_account_factory(token=token_path)
    _write_no_expiry_token(token_path)   # 取得から 250 日以上（50 日超）

    row = maintain_mod.inspect(account["name"],
                                now=datetime.datetime(2026, 9, 13, 10, 0, tzinfo=jst.JST))
    assert row["state"] == maintain_mod.OK, row
    assert row["remaining_days"] is None
    assert row["no_expiry"] is True
    assert "期限を持たない" in row["message"]

    呼ばれた = []
    lines = []
    rc = maintain_mod.run_maintain(account["name"], log=lines.append,
                                    refresh=lambda *a, **k: 呼ばれた.append(a) or 0,
                                    now=datetime.datetime(2026, 9, 13, 10, 0,
                                                           tzinfo=jst.JST))
    assert rc == 0 and 呼ばれた == [], "**期限を持たないトークンを更新しようとしている**"
    assert "期限なし" in "\n".join(lines), lines


def test_判らないと期限を持たないは別の顔で出る(tmp_path, isolated_account_factory):
    """`remaining_days: null` だけでは、読めなかったのか期限が無いのか判らない。"""
    token_path = str(tmp_path / "a.token")
    account = isolated_account_factory(token=token_path)
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump({"access_token": "T"}, f)   # `obtained_at` が無い＝読めない
    壊れ = maintain_mod.inspect(account["name"],
                                now=datetime.datetime(2026, 9, 13, 10, 0, tzinfo=jst.JST))
    assert 壊れ["state"] == maintain_mod.UNREADABLE
    assert 壊れ["remaining_days"] is None and 壊れ["no_expiry"] is False


def test_refreshのcheckにno_expiryが出る(tmp_path, capsys, isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    account = isolated_account_factory(token=token_path)
    _write_no_expiry_token(token_path)
    now = datetime.datetime(2026, 9, 13, 10, 0, tzinfo=jst.JST)

    lines = []
    assert oauth_mod.run_refresh(account["name"], check=True, log=lines.append,
                                  now=now) == 0
    payload = json.loads(lines[-1])
    assert payload["no_expiry"] is True
    assert payload["remaining_days"] is None
    assert payload["needs_refresh"] is False

    lines = []
    assert oauth_mod.run_refresh(account["name"], force=True, log=lines.append,
                                  now=now) == 0
    assert "期限を持ちません" in "\n".join(lines), lines


# =============================================================================
# T-B5  doctor は媒体ごとの probe を使う。Threads の出力は現行と同じ
# =============================================================================

def _write_token(path, token="TB5-SECRET-TOKEN"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"access_token": token, "obtained_at": "2026-09-09T00:00:00+09:00",
                   "expires_in": 5184000, "user_id": "999999",
                   "username": "nigamilab", "scopes": None}, f)


def test_TB5_doctorはアダプタのprobeを使う(tmp_path, monkeypatch,
                                  isolated_account_factory):
    """**変異検出**: `diagnose()` が自前で probe を組み立てる形に戻すと落ちる。"""
    token_path = str(tmp_path / "a.token")
    account = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path)

    印 = [{"name": "しるし", "ok": True, "detail": "アダプタから来た"}]
    monkeypatch.setattr(threads_mod.ThreadsAdapter, "probe",
                        lambda self, *, get=None: [dict(r) for r in 印])
    report = doctor_mod.diagnose(account["name"])
    assert report["probes"] == 印


def test_TB5_threadsの出力は現行と同じ(tmp_path, monkeypatch, isolated_account_factory):
    """叩く口・並び・鍵・判定が、probe を移す前と同じであることを固定する。"""
    token_path = str(tmp_path / "a.token")
    account = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path)

    叩いた = []

    def fake_get(base_url, path, params, token):
        叩いた.append(path)
        if path.endswith("/threads") and "insights" not in path:
            return {"data": [{"id": "POST123", "permalink": "https://example/p",
                               "timestamp": "2026-09-10T00:00:00+0000"}]}
        if path.endswith("/conversation"):
            return {"data": [{"id": "R1", "username": "someone",
                               "timestamp": "2026-09-10T01:00:00+0000"}]}
        return {"data": []}

    monkeypatch.setattr(doctor_mod, "_get", fake_get)
    report = doctor_mod.diagnose(account["name"])

    # **probe を移す前からある 6 行は、口も並びもそのまま**。2026-09-14 に残り
    # 7 権限の行を**この後ろに**足した（`tests/test_doctor_all_permissions.py`）
    # ので、ここは「先頭 6 つが現行と同じ」を固定する（増えた分は許す）。
    既存 = 6
    assert 叩いた[:既存] == [
        "/v1.0/me",
        "/v1.0/999999/threads",
        "/v1.0/999999/threads_publishing_limit",
        "/v1.0/999999/threads_insights",
        "/v1.0/999999/threads_insights",
        "/v1.0/POST123/conversation",
    ], 叩いた
    assert [p["permission"] for p in report["probes"]][:既存] == [
        "threads_basic", "threads_basic", "threads_content_publish",
        "threads_manage_insights", "threads_manage_insights", "threads_read_replies",
    ]
    for p in report["probes"]:
        # 人向けの表示（`run_doctor`）が使う鍵。**足すのはよいが、消さない。**
        assert {"label", "permission", "key", "ok", "detail"} <= set(p)
        assert "body" not in p, "**本文を返り値に残さない**（値が漏れる）"
    # 読み取りの口が無い権限（`ok=None`）が後ろに並ぶので、**叩いた行は全部○**。
    assert all(p["ok"] is True for p in report["probes"][:既存])
    assert all(p["ok"] is True for p in report["probes"] if p["ok"] is not None)


def test_TB5_投稿が無ければ返信のprobeは判定不能のまま(tmp_path, monkeypatch,
                                          isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    account = isolated_account_factory(token=token_path, handle="nigamilab")
    _write_token(token_path)
    monkeypatch.setattr(doctor_mod, "_get",
                        lambda base_url, path, params, token: {"data": []})
    report = doctor_mod.diagnose(account["name"])
    返信 = next(p for p in report["probes"]
                if p["permission"] == "threads_read_replies")
    assert 返信["ok"] is None
    assert 返信["detail"] == "投稿がまだ無いので試せない"


def test_TB5_知らない媒体のdoctorは黙って異常なしにしない(tmp_path,
                                            isolated_account_factory):
    token_path = str(tmp_path / "a.token")
    account = isolated_account_factory(token=token_path, media="carrier-pigeon")
    _write_token(token_path)
    report = doctor_mod.diagnose(account["name"])
    assert report["probes"] == []
    assert "carrier-pigeon" in report["error"] and "threads" in report["error"]
    rc = doctor_mod.run_doctor(account["name"], log=lambda _l: None)
    assert rc == 2


# --- T3 の配線（2026-09-13・Bluesky と Mastodon を REGISTRY に載せる） ---------

def test_T3_REGISTRYに3媒体が載っている():
    """**足すのは 1 行だけ**（設計 v2 §4.2「台帳と登録」）。

    ここが落ちると、台帳に `media: bluesky` と書いても
    「知りません」で断られる（＝端から端が通らない）。
    """
    from thth.adapters import bluesky as bluesky_mod
    from thth.adapters import mastodon as mastodon_mod
    assert adapters_mod.known_media() == ["bluesky", "mastodon", "threads"]
    assert adapters_mod.REGISTRY["bluesky"] is bluesky_mod.BlueskyAdapter
    assert adapters_mod.REGISTRY["mastodon"] is mastodon_mod.MastodonAdapter


def test_T3_全媒体のCAPABILITIESが境界の語彙に収まる():
    """**ここに無い語を返さない**（`base.KNOWN_CAPABILITIES` の但し書き）。

    読み手（`select`・`collect`・`core`）はこの一覧だけを見て分岐するので、
    媒体側が勝手な語を入れると**誰も見ない能力**になる（黙って効かない）。
    """
    for media, cls in adapters_mod.REGISTRY.items():
        unknown = set(cls.CAPABILITIES) - adapter_base.KNOWN_CAPABILITIES
        assert not unknown, f"{media}: 境界の語彙に無い能力 {sorted(unknown)}"
        # `capabilities()` は**実体を作らずに**引ける（受け入れ 6）。
        assert cls.capabilities() == set(cls.CAPABILITIES)


def test_T3_全媒体がfrom_accountを持つ():
    """`make_adapter()` は `from_account()` しか呼ばない（**core の唯一の入口**）。"""
    for media, cls in adapters_mod.REGISTRY.items():
        assert cls.from_account is not adapter_base.Adapter.from_account, \
            f"{media}: from_account が境界の未実装のまま"


def test_T3_台帳とトークンからBlueskyとMastodonを組み立てられる():
    bsky = adapters_mod.make_adapter(
        {"media": "bluesky", "handle": "aoking.bsky.social"},
        {"identifier": "aoking.bsky.social", "app_password": "aaaa-bbbb-cccc-dddd"})
    # `service` を省いたら既定の PDS（設計 v2 §4.2「台帳の追加項目」）。
    assert bsky.service == "https://bsky.social"
    assert bsky.identifier == "aoking.bsky.social"
    bsky2 = adapters_mod.make_adapter(
        {"media": "bluesky", "service": "https://pds.example.invalid/"}, {})
    assert bsky2.service == "https://pds.example.invalid"

    mstdn = adapters_mod.make_adapter(
        {"media": "mastodon", "instance": "https://mastodon.social"},
        {"access_token": "x"})
    assert mstdn.instance == "https://mastodon.social"


def test_T3_Blueskyはトークンが無くても組み立てだけは通る():
    """**読むだけの口を、トークンの有無で落とさない**（`thth board`・`thth account`）。

    実際に叩く段（`session()`）で loud に断る。
    """
    adapter = adapters_mod.make_adapter({"media": "bluesky"}, {})
    with pytest.raises(RuntimeError) as e:
        adapter.session()
    assert "thth auth" in str(e.value)


def test_T3_媒体ごとのTOKEN_KEYSが境界に載っている():
    """`doctor` は `access_token` の有無ではなく**媒体の鍵**で判定する。"""
    from thth.adapters import bluesky as bluesky_mod
    from thth.adapters import mastodon as mastodon_mod
    assert threads_mod.ThreadsAdapter.TOKEN_KEYS == ("access_token",)
    assert bluesky_mod.BlueskyAdapter.TOKEN_KEYS == ("identifier", "app_password")
    assert mastodon_mod.MastodonAdapter.TOKEN_KEYS == ("access_token",)
    # Bluesky は `identifier` だけでは足りない（両方揃って初めて「在る」）。
    assert not bluesky_mod.BlueskyAdapter.has_token({"identifier": "a"})
    assert bluesky_mod.BlueskyAdapter.has_token(
        {"identifier": "a", "app_password": "b"})
    # Threads の access_token を貼っても Bluesky では「在る」にならない。
    assert not bluesky_mod.BlueskyAdapter.has_token({"access_token": "x"})
    assert threads_mod.ThreadsAdapter.has_token({"access_token": "x"})
    assert not threads_mod.ThreadsAdapter.has_token(None)


def test_T3_期限を持たない媒体に印がある():
    """「判らない」と「期限を持たない」を分ける（設計 v2 §4.2・`maintain`）。"""
    from thth.adapters import bluesky as bluesky_mod
    from thth.adapters import mastodon as mastodon_mod
    assert bluesky_mod.BlueskyAdapter.TOKEN_NO_EXPIRY is True
    assert mastodon_mod.MastodonAdapter.TOKEN_NO_EXPIRY is True
    assert threads_mod.ThreadsAdapter.TOKEN_NO_EXPIRY is False
