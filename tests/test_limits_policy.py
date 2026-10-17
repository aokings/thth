"""段 1: 柵の行の表（`thth/limits_policy.py`・設計 上限と連投の柵 §6・§12 の段 1）。

見るのは:
  - 表の形の検査が通る（`id` の重複・知らない信号・`stop` は安全装置だけ、など）。
  - 動作ごとに当てる行と順が 4.0.1 の `guard.check` の `if` の順と同じ（golden）。
  - 行の数は口座・主体・ブログの台帳から引く（既定は `GUARD_DEFAULTS`・`LIMIT_DEFAULTS`・台帳の値が勝つ）。
  - THTH の行の `trait` は `thth:<id>`（台帳に結んだ行は台帳の項目）。台帳（段 0）との写像の縛りは
    `tests/test_limits_stage2.py`（段 2）。
"""
from __future__ import annotations

import pytest

from thth import accounts, assess, limits_policy, principals


def ids(family, action, **kwargs):
    kwargs.setdefault("cfg", {})
    return [row["id"] for row in limits_policy.rules_for(family, action, **kwargs)]


def test_表の形の検査が通る():
    assert limits_policy.check_table() == []
    assert len(set(limits_policy.ids())) == len(limits_policy.POLICY)


def test_表の形の検査は崩れを見つける():
    """柵: 検査が何も見ないと赤（わざと崩した表で問題が出る）。"""
    broken = [dict(row) for row in limits_policy.POLICY]
    broken[0] = dict(broken[0], signal="nope")
    broken[1] = dict(broken[1], on_exceed="stop")          # 見立ての行で止める
    broken.append(dict(broken[2]))                          # id の重複
    problems = limits_policy.check_table(broken)
    assert any("signal" in p for p in problems)
    assert any("stop は安全装置だけ" in p for p in problems)
    assert any("duplicate" in p for p in problems)


def test_THTHの行のtraitはthth():
    for row in limits_policy.THTH_DEFAULTS:
        assert row["trait"] == "thth:" + row["id"]
        assert row["signal"] in assess.SIGNALS
    # 台帳に結ばない行は解いても thth:・結んだ行は台帳の項目（T-M-2 の後半）
    tied = {row["id"] for row in limits_policy.POLICY if row["ledger"]}
    for row in limits_policy.POLICY:
        if row["id"] not in tied:
            assert limits_policy.resolve(row, cfg={}, principal_cfg={})["trait"] == "thth:" + row["id"]
        else:
            assert not row["trait"].startswith("thth:")


def test_信号は7つ():
    assert assess.SIGNALS == ("frequency", "same_target", "same_host", "self_reference", "counterpart",
                              "recent_platform_refusal", "quiet_and_interval")


# ---------------------------------------------------------------- 当てる順（4.0.1 の if の順）

@pytest.mark.parametrize("family,action,expected", [
    # 段 2: 各道の最後に共通の最低線 `write.floor`（予約と反応には当てない）。
    ("sns", "post", ["post.quiet", "post.interval", "post.daily", "post.burst", "write.floor"]),
    ("sns", "reply", ["post.quiet", "post.daily", "reply.burst", "write.floor"]),
    ("sns", "retract", ["retract.daily", "post.burst", "write.floor"]),
    ("sns", "schedule", ["post.daily"]),
    ("hatenablog", "entry_create", ["entry.platform_refusal", "entry.quiet", "entry.interval", "entry.daily",
                                    "entry.blog_daily", "entry.principal_daily", "entry.burst",
                                    "entry.principal_burst", "write.floor"]),
    ("hatenablog", "entry_update", ["entry.platform_refusal", "entry.quiet", "entry.interval", "entry.daily",
                                    "entry.blog_daily", "entry.principal_daily", "entry.burst",
                                    "entry.principal_burst", "write.floor"]),
    ("hatenablog", "entry_delete", ["entry.platform_refusal", "entry_delete.daily", "entry_delete.blog_daily",
                                    "entry_delete.principal_daily", "entry.burst", "entry.principal_burst",
                                    "write.floor"]),
    ("hatenablog", "schedule", ["entry.platform_refusal", "entry.daily", "entry.principal_daily"]),
    # 段 2: ブックマークの止め線 `bookmark.stop` はブックマークの行の最初（日の上限・窓より先）。
    ("hatenabookmark", "bookmark_add", ["entry.platform_refusal", "bookmark.stop", "bookmark.daily",
                                        "bookmark.self_daily", "bookmark.same_host_daily", "bookmark.burst",
                                        "bookmark.once", "write.floor"]),
    ("hatenabookmark", "bookmark_delete", ["entry.platform_refusal", "bookmark.stop", "bookmark.daily",
                                           "bookmark.burst", "write.floor"]),
    ("reaction", "repost", ["reaction.quiet", "reaction.daily"]),
    ("reaction", "like", ["reaction.daily"]),
    ("reaction", "undo", ["reaction.daily"]),
    ("fotolife", "upload", ["upload.daily"]),
])
def test_動作ごとの行と順(family, action, expected):
    """柵: 表の順を入れ替えると赤（first-hit なので順が振る舞い）。"""
    assert ids(family, action) == expected


@pytest.mark.parametrize("media", ["threads", "x", "bluesky", "mastodon", None])
def test_SNSの道は媒体を問わず同じ行(media):
    """段 2 で媒体で行を絞るようにしても、対応する SNS の 4 媒体（と媒体を言わない口座）の行は変わらない。"""
    cfg = {"media": media} if media else {}
    assert ids("sns", "post", cfg=cfg) == ["post.quiet", "post.interval", "post.daily", "post.burst",
                                           "write.floor"]
    assert ids("reaction", "repost", cfg=cfg) == ["reaction.quiet", "reaction.daily"]


def test_行の無い媒体には共通の最低線だけ():
    assert ids("sns", "post", cfg={"media": "fake"}) == ["write.floor"]
    assert ids("sns", "retract", cfg={"media": "fake"}) == ["write.floor"]
    # 反応の行は媒体を名乗らない（どの媒体にも当てる・緩める側に倒さない）
    assert ids("reaction", "like", cfg={"media": "fake"}) == ["reaction.daily"]


def test_知らない道は断る():
    with pytest.raises(ValueError):
        limits_policy.rules_for("misskey", "post", cfg={})


def test_止める行は安全装置の5つ():
    stops = {row["id"]: (row["refusal"], row["detail"], row["scope"]) for row in limits_policy.POLICY
             if row["on_exceed"] == "stop"}
    assert stops == {"post.burst": ("account_stopped", "burst", "account"),
                     "entry.burst": ("account_stopped", "burst", "account"),
                     "entry.principal_burst": ("principal_stopped", "burst", "principal"),
                     # 段 2（§6.3）
                     "bookmark.stop": ("principal_stopped", "burst", "principal"),
                     "write.floor": ("account_stopped", "burst", "account")}
    # ブックマークの窓は止めない（4.0.1）・返信は並べる（3.25.0）
    assert limits_policy.rule("bookmark.burst")["on_exceed"] == "wait"
    assert limits_policy.rule("reply.burst")["on_exceed"] == "wait"


def test_符丁は今のものだけ():
    """柵: 行が新しい符丁を作ると赤（符丁は増やさない・§7.1）。"""
    from thth import guard, hatena_fotolife
    known = set(guard.REFUSALS) | {"account_stopped", "principal_stopped"} | set(hatena_fotolife.WAITING)
    assert {row["refusal"] for row in limits_policy.POLICY} <= known


# ---------------------------------------------------------------- 数の出所

def by_id(rows):
    return {row["id"]: row for row in rows}


def test_口座の既定と台帳の値():
    rows = by_id(limits_policy.rules_for("sns", "post", cfg={}))
    assert rows["post.daily"]["count"] == accounts.GUARD_DEFAULTS["daily_max_posts"] == 8
    assert rows["post.daily"]["origin"] == "default"
    assert rows["post.burst"]["count"] == 3 and rows["post.burst"]["window"] == {"minutes": 10}
    assert rows["post.quiet"]["count"] is None and rows["post.interval"]["count"] is None  # 台帳に無い
    rows = by_id(limits_policy.rules_for("sns", "post", cfg={
        "daily_max_posts": 20, "burst": {"count": 5, "minutes": 15}, "quiet_hours": ["23:00", "06:00"],
        "min_interval_hours": 2}))
    assert rows["post.daily"]["count"] == 20 and rows["post.daily"]["origin"] == "account"
    assert rows["post.burst"]["count"] == 5 and rows["post.burst"]["window"] == {"minutes": 15}
    assert rows["post.quiet"]["window"] == {"quiet_hours": ["23:00", "06:00"]}
    assert rows["post.interval"]["window"] == {"hours": 2}
    # 受け取れない値は既定（`guard_limits` と同じ）
    assert by_id(limits_policy.rules_for("sns", "post", cfg={"daily_max_posts": -1}))["post.daily"]["count"] == 8


def test_主体の既定と台帳の値():
    rows = by_id(limits_policy.rules_for("hatenabookmark", "bookmark_add", cfg={}, principal_cfg={}))
    assert rows["bookmark.daily"]["count"] == principals.LIMIT_DEFAULTS["daily_max_bookmarks"] == 30
    assert rows["bookmark.burst"]["count"] == 10 and rows["bookmark.burst"]["window"] == {"minutes": 10}
    assert rows["bookmark.same_host_daily"]["count"] == 3
    assert rows["bookmark.self_daily"]["count"] == 1
    assert rows["bookmark.once"]["count"] == 1
    rows = by_id(limits_policy.rules_for("hatenabookmark", "bookmark_add", cfg={},
                                         principal_cfg={"daily_max_bookmarks": 3}))
    assert rows["bookmark.daily"]["count"] == 3 and rows["bookmark.daily"]["origin"] == "principal"
    rows = by_id(limits_policy.rules_for("hatenablog", "entry_create", cfg={}, principal_cfg={}))
    assert rows["entry.principal_burst"]["count"] == 3 and rows["entry.principal_burst"]["window"] == {"minutes": 30}


def test_ブログの欄が無ければ数えない():
    rows = by_id(limits_policy.rules_for("hatenablog", "entry_create", cfg={}, principal_cfg={}, blog={}))
    assert rows["entry.blog_daily"]["count"] is None
    rows = by_id(limits_policy.rules_for("hatenablog", "entry_create", cfg={}, principal_cfg={},
                                         blog={"daily_max_posts": 1}))
    assert rows["entry.blog_daily"]["count"] == 1


# ---------------------------------------------------------------- 台帳（段 0）の口は段 2 の試験
# （写像の表・形の一致・台帳から生まれる行・遅れて作る表）: `tests/test_limits_stage2.py`
