"""設計 持ち主の依頼で消す §5: THTH の外で付けた反応の取り消し。

X・Mastodon の再投稿といいね、Instagram のいいねは「自分の、その投稿への反応を外す」口で、元の投稿の
id だけで打てる（他人の反応には届かない）。記録が無くても 1 回頼み、`reactions/` に
`outside_thth: true` で残す。Threads の再投稿は再投稿そのものの id が要るので断りの文に直し方を書く。
本物の SNS は叩かない（`tests/test_v315_reactions.py` の偽サーバ）。
"""
from __future__ import annotations

from tests.test_v315_reactions import fake, make, run, sns_calls, state_rows  # noqa: F401


def test_Xの記録の無い再投稿は元のidで1回だけ外して記録する(make, fake, capsys):
    make("x")
    fake.on("DELETE", "/2/users/777/retweets/1800000000000000001", body={"data": {"retweeted": False}})
    rc, payload, _ = run(capsys, "unrepost", "1800000000000000001")
    assert rc == 0, payload
    assert [c["path"] for c in sns_calls(fake)] == ["/2/users/777/retweets/1800000000000000001"]
    [row] = state_rows()
    assert row["outside_thth"] is True and row["undone_at"] and row["at"] is None
    assert row["undone_by"] == "masaru" and row["kind"] == "repost"
    assert payload["x_cost_estimate"]["usd"] == "0.010"


def test_Mastodonの記録の無いいいねは元のidで外す(make, fake, capsys):
    make("mastodon")
    fake.on("POST", "/api/v1/statuses/113000000000000001/unfavourite",
            body={"id": "113000000000000001", "favourited": False})
    rc, payload, _ = run(capsys, "unlike", "113000000000000001")
    assert rc == 0, payload
    assert [c["path"] for c in sns_calls(fake)] == ["/api/v1/statuses/113000000000000001/unfavourite"]
    assert [row["outside_thth"] for row in state_rows()] == [True]


def test_Threadsの再投稿は記録が無ければ送らず直し方を言う(make, fake, capsys):
    make("threads")
    rc, payload, _ = run(capsys, "unrepost", "555")
    assert rc == 1 and payload["error"] == "not_found" and fake.calls == []
    message = payload.get("message") or ""
    assert "thth retract" in message and "なぜ:" in message and "より良く:" in message


def test_取り消しを元のidで打てる反応はX_Mastodon_Instagramのいいねだけ():
    from thth.adapters import bluesky, instagram, mastodon, threads, x
    assert x.XAdapter.REACTION_UNDO_BY_POST == {"repost", "like"}
    assert mastodon.MastodonAdapter.REACTION_UNDO_BY_POST == {"repost", "like"}
    assert instagram.InstagramAdapter.REACTION_UNDO_BY_POST == {"like"}
    assert threads.ThreadsAdapter.REACTION_UNDO_BY_POST == frozenset()
    assert bluesky.BlueskyAdapter.REACTION_UNDO_BY_POST == frozenset()
