"""observe の返信の段に、取り直しの窓の様子を足す（11/06・依頼
`docs/依頼_media-hub_返信の取得の窓_2026-11-06.md`）。

- `live_posts`: 取り直しの窓（`reply_follow.in_window`）の中にある自分の投稿の数。
  取り直しが無効の口座は None（言えない・0 にしない）。
- `follow_last_at`・`follow_last_hours`: VM の `state/<口座>/reply_follow.json` の最後の取り直し。
  無い・壊れているなら None と理由コード（`follow_cannot_say`）。
- `last_fetch_*` は台帳の `kind: fetch` だけから数える意味のまま。
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from thth import accounts, jst, morning, reply_follow, sent
from tests.test_v310_morning import one  # noqa: F401

NOW = jst.parse("2026-11-06T10:00:00+09:00")


def ago(hours):
    return jst.iso(NOW - datetime.timedelta(hours=hours))


def post(name, pid, hours, reply_to=None):
    sent.write(accounts.state_dir_for(name), post_id=pid, text="本文", body_hash="hash",
               sent_at=ago(hours), reply_to=reply_to)


def ledger(name, pid, rows):
    directory = Path(accounts.data_dirs(accounts.load_account(name), name)["replies"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{pid}.ndjson").write_text("".join(json.dumps(r) + "\n" for r in rows))


def follow_state(name, body):
    directory = Path(accounts.state_dir_for(name))
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / reply_follow.STATE_FILE
    path.write_text(body if isinstance(body, str) else json.dumps(body))
    return path


def node(name):
    return morning._unanswered_rows(name, NOW, cfg=accounts.load_account(name))


# ------------------------------------------------------------------ 窓の中の投稿の数

def test_投稿が無ければ窓の中は0(isolated_account_factory):
    isolated_account_factory("one", handle="owner")
    rows = node("one")
    assert rows["live_posts"] == 0 and rows["live_hours"] == 48


def test_窓の中の投稿が1本(isolated_account_factory):
    isolated_account_factory("one", handle="owner")
    post("one", "NEW", 10)
    post("one", "OLD", 100)
    assert node("one")["live_posts"] == 1


def test_窓が閉じたあとは0_nigamilab_fbの件(isolated_account_factory):
    # 投稿は 1 本・50 時間前・返信なし。48 時間で窓が閉じたので取り直しは来ない。
    isolated_account_factory("one", handle="owner", media="facebook")
    post("one", "ONLY", 50)
    ledger("one", "ONLY", [{"kind": "fetch", "post_id": "ONLY", "collected_at": ago(44)}])
    rows = node("one")
    assert rows["live_posts"] == 0
    # 台帳の取得の意味は変わらない（後方互換）。
    assert rows["last_fetch_hours"] == 44.0


def test_返信の動きで窓が延びる(isolated_account_factory):
    isolated_account_factory("one", handle="owner")
    post("one", "ROOT", 100)
    ledger("one", "ROOT", [
        {"id": "R1", "username": "outside", "text": "x", "timestamp": ago(20), "post_id": "ROOT"},
        {"kind": "fetch", "post_id": "ROOT", "collected_at": ago(19)}])
    assert node("one")["live_posts"] == 1


def test_自分の返信の動きでも窓が延びる(isolated_account_factory):
    isolated_account_factory("one", handle="owner")
    post("one", "ROOT", 100)
    post("one", "ANSWER", 5, reply_to="ROOT")
    # ANSWER 自身も窓の中（5 時間前）。ROOT は自分の返信で延びる。
    assert node("one")["live_posts"] == 2


def test_返信が古ければ窓は延びない(isolated_account_factory):
    isolated_account_factory("one", handle="owner")
    post("one", "ROOT", 100)
    ledger("one", "ROOT", [
        {"id": "R1", "username": "outside", "text": "x", "timestamp": ago(60), "post_id": "ROOT"}])
    assert node("one")["live_posts"] == 0


def test_取り直しが無効の口座はnull(isolated_account_factory):
    isolated_account_factory("xone", handle="owner", media="x")
    post("xone", "NEW", 1)
    rows = node("xone")
    assert rows["live_posts"] is None
    assert "follow_disabled" in rows["follow_cannot_say"]


def test_明示で切った口座もnull(isolated_account_factory):
    isolated_account_factory("one", handle="owner", reply_follow={"enabled": False})
    post("one", "NEW", 1)
    assert node("one")["live_posts"] is None


def test_窓の長さは口座の設定に従う(isolated_account_factory):
    isolated_account_factory("one", handle="owner", reply_follow={"live_hours": 12})
    post("one", "NEW", 10)
    post("one", "MID", 20)
    rows = node("one")
    assert rows["live_posts"] == 1 and rows["live_hours"] == 12


# ------------------------------------------------------------------ 最後の取り直し

def test_reply_followの最後の時刻(isolated_account_factory):
    isolated_account_factory("one", handle="owner")
    follow_state("one", {"last": {"A": ago(30), "B": ago(3)}})
    rows = node("one")
    assert rows["follow_last_at"] == ago(3) and rows["follow_last_hours"] == 3.0
    assert rows["follow_cannot_say"] == []


def test_reply_followが無ければnullと理由(isolated_account_factory):
    isolated_account_factory("one", handle="owner")
    rows = node("one")
    assert rows["follow_last_at"] is None and rows["follow_last_hours"] is None
    assert rows["follow_cannot_say"] == ["follow_state_missing"]


def test_reply_followが壊れていればnullと理由(isolated_account_factory):
    isolated_account_factory("one", handle="owner")
    follow_state("one", "{not json")
    rows = node("one")
    assert rows["follow_last_at"] is None and rows["follow_last_hours"] is None
    assert rows["follow_cannot_say"] == ["follow_state_unreadable"]
    follow_state("one", {"last": ["x"]})
    assert node("one")["follow_cannot_say"] == ["follow_state_malformed"]
    follow_state("one", {"last": {}})
    assert node("one")["follow_cannot_say"] == ["follow_state_empty"]


def test_取り直しの記録が壊れても窓の数は言える(isolated_account_factory):
    isolated_account_factory("one", handle="owner")
    post("one", "NEW", 1)
    follow_state("one", "{not json")
    assert node("one")["live_posts"] == 1


# ------------------------------------------------------------------ 判定は採取と同じ

def test_採取の候補と同じ窓の判定():
    cfg = reply_follow.settings({"media": "threads"})
    posted = NOW - datetime.timedelta(hours=49)
    reply = [{"timestamp": ago(47)}]
    assert reply_follow.in_window(now=NOW, posted_at=posted, reply_rows=reply, own_reply_at=None, cfg=cfg)
    assert reply_follow.candidate(now=NOW, posted_at=posted, reply_rows=reply, fetch_rows=[],
                                  own_reply_at=None, cfg=cfg) is not None
    assert not reply_follow.in_window(now=NOW, posted_at=posted, reply_rows=[], own_reply_at=None, cfg=cfg)
    assert reply_follow.candidate(now=NOW, posted_at=posted, reply_rows=[], fetch_rows=[],
                                  own_reply_at=None, cfg=cfg) is None
    # 間隔（every_minutes）は窓の数に効かない: 今取り直したばかりでも窓の中。
    fresh = [{"kind": "fetch", "collected_at": jst.iso(NOW)}]
    assert reply_follow.candidate(now=NOW, posted_at=NOW - datetime.timedelta(hours=1), reply_rows=[],
                                  fetch_rows=fresh, own_reply_at=None, cfg=cfg) is None
    assert reply_follow.live_count(now=NOW, posts={"P": NOW - datetime.timedelta(hours=1)},
                                   reply_rows_by_post={}, own_reply_at={}, cfg=cfg) == 1


# ------------------------------------------------------------------ observe の出力

def test_counts_onlyの出力にも出る(isolated_account_factory, monkeypatch):
    isolated_account_factory("one", handle="owner")
    post("one", "NEW", 10)
    follow_state("one", {"last": {"NEW": ago(2)}})
    sections = [{"section": "unanswered", "value": {"by_account": {"one": {
        "cannot_say": None, "value": {"replies": {"cannot_say": None, "value": node("one")},
                                      "mentions": {"cannot_say": "not_supported", "value": None}}}}}}]
    morning._apply_counts_only(sections)
    rows = sections[0]["value"]["by_account"]["one"]["value"]["replies"]["value"]
    assert rows["live_posts"] == 1 and rows["follow_last_hours"] == 2.0
    assert rows["follow_last_at"] == ago(2) and "last_fetch_hours" in rows
    assert "items" not in rows


def test_observeのcounts_onlyの全体の出力にも出る(one):  # noqa: F811
    from tests.test_v310_morning import ACCOUNT, NOW as OBSERVE_NOW, PROJECT, sections
    sent.write(accounts.state_dir_for(ACCOUNT), post_id="ROOT", text="本文", body_hash="hash",
               sent_at=jst.iso(OBSERVE_NOW - datetime.timedelta(hours=10)))
    follow_state(ACCOUNT, {"last": {"ROOT": jst.iso(OBSERVE_NOW - datetime.timedelta(hours=2))}})
    payload = morning.build(PROJECT, now=OBSERVE_NOW, mark=False, counts_only=True, no_world=True)
    rows = sections(payload)["unanswered"]["value"]["by_account"][ACCOUNT]["value"]["replies"]["value"]
    assert rows["live_posts"] == 1 and rows["live_hours"] == 48
    assert rows["follow_last_hours"] == 2.0 and rows["follow_cannot_say"] == []
