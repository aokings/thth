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

from tests.conftest import write_queue_file
from tests.helpers import fake_push_adapter
from thth import account_report as account_report_mod
from thth import adapters as adapters_mod
from thth import cli as cli_mod
from thth import collect as collect_mod
from thth import core as core_mod
from thth import doctor as doctor_mod
from thth import jst
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
    assert threads_mod.ThreadsAdapter.capabilities() == {
        "topic", "link_preview", "views", "quota", "refresh"}


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
    """Threads には `inbox` を呼ばない（`capabilities()` に無い）。"""
    account = isolated_account_factory()

    class _呼ばれたら失敗(threads_mod.ThreadsAdapter):
        def __init__(self):
            pass

        def inbox(self, *, since=None):
            raise AssertionError("**inbox を持たない媒体に聞いている**")

        def account_insights(self, user_id, *, since, until):
            return {}

    collect_mod.collect_once(account["name"], adapter=_呼ばれたら失敗())
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

    assert 叩いた == [
        "/v1.0/me",
        "/v1.0/999999/threads",
        "/v1.0/999999/threads_publishing_limit",
        "/v1.0/999999/threads_insights",
        "/v1.0/999999/threads_insights",
        "/v1.0/POST123/conversation",
    ], 叩いた
    assert [p["permission"] for p in report["probes"]] == [
        "threads_basic", "threads_basic", "threads_content_publish",
        "threads_manage_insights", "threads_manage_insights", "threads_read_replies",
    ]
    for p in report["probes"]:
        # 人向けの表示（`run_doctor`）が使う鍵。**足すのはよいが、消さない。**
        assert {"label", "permission", "key", "ok", "detail"} <= set(p)
        assert "body" not in p, "**本文を返り値に残さない**（値が漏れる）"
    assert all(p["ok"] is True for p in report["probes"])


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
