"""スレッドの形（`thth/threadshape.py`・`thth threads`・設計 v2 §6 v2-0）。

受け入れ T-V0-1 〜 T-V0-5。**手計算と一致すること**・**取れていない刻みが
`null`（`0` ではない）こと**・**n<3 が「言えない」に落ちること**・**本物の台帳で
落ちないこと**を確かめる。**読むだけの口なので、書き込みが起きないことも見る。**
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess

import pytest

from thth import cli as cli_mod
from thth import threadshape as threadshape_mod

# ---------------------------------------------------------------- 台帳を組む

# 投稿の根。**投稿 → 返信の親子関係**を手で組める最小の形を作る。
ROOT = "POST1"
POSTED_AT = "2026-09-10T08:00:00+09:00"   # = 2026-09-09T23:00:00Z


def _write_ndjson(path: str, rows: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _replies_path(account, post_id: str = ROOT) -> str:
    return os.path.join(account["repo_dir"], "data", "sns", "replies",
                        f"{post_id}.ndjson")


def _insights_path(account, post_id: str = ROOT) -> str:
    return os.path.join(account["repo_dir"], "data", "sns", "insights", "posts",
                        f"{post_id}.ndjson")


def reply(rid: str, *, parent: str, username: str, minutes: int,
          root: str = ROOT) -> dict:
    """返信 1 行。`minutes` は投稿（`POSTED_AT`）からの経過分。

    `timestamp` は Threads API の生の形（UTC・`+0000`）で書く——**本番の台帳が
    その形をしている**ので、fixture だけ ISO の別形にすると読めない形が
    テストを通ってしまう（規約 11）。
    """
    base_h, base_m = 23, 0                       # 2026-09-09T23:00:00Z
    total = base_m + minutes
    day = 9 + (base_h * 60 + total) // 1440
    rem = (base_h * 60 + total) % 1440
    return {
        "kind": "reply", "post_id": ROOT, "id": rid, "is_reply": True,
        "username": username, "text": f"{rid} の本文",
        "timestamp": f"2026-09-{day:02d}T{rem // 60:02d}:{rem % 60:02d}:00+0000",
        "replied_to": {"id": parent}, "root_post": {"id": root},
        "collected_at": "2026-09-11T09:00:00+09:00",
    }


def fetch(*, marks: list, age_hours: float, trigger: str = "marks") -> dict:
    return {"kind": "fetch", "post_id": ROOT,
            "collected_at": f"2026-09-10T{int(8 + age_hours) % 24:02d}:00:00+09:00",
            "age_hours": age_hours, "marks": marks, "trigger": trigger,
            "replies": 0, "id_missing": 0}


def insights_row(*, marks: list, age_hours: float, views: int,
                  account: str, topic: str = "コーヒー") -> dict:
    return {"post_id": ROOT, "file": "2026-09-10-x.md", "account": account,
            "topic": topic, "collected_at": "2026-09-10T09:00:00+09:00",
            "posted_at": POSTED_AT, "age_hours": age_hours, "marks": marks,
            "metrics": {"views": views, "likes": 0, "replies": 0, "reposts": 0,
                         "quotes": 0, "shares": 0}}


# 3 階層の台帳（T-V0-1 の手計算のもと）。作者の handle は `nigamilab`
# （`tests/conftest.py:make_account_json()` の既定）。
#
#   POST1（根）
#     ├ R1  よそ子     +10 分   ← 最初の他人の返信
#     │   └ R2  nigamilab（作者） +90 分
#     │       └ R3  よそ太     +200 分   ← 深さ 3
#     ├ R4  よそ子     +400 分
#     └ R5  nigamilab（作者・連投） +1000 分
#
#   枝（根の直下）= R1・R4・R5 の 3 本。**うち作者の連投 R5 は
#   `author_reply_effect` の枝には数えない**（`self_branches`）。
#   最深 = 3。参加者（作者を除く）= よそ子・よそ太 の 2 人、最多は
#   よそ子 2/3。最初の他人の返信 = +10 分。
#   **分は刻みをまたぐように散らしてある**（1h に 1 件・6h までに 3 件・
#   24h までに 5 件）——全部 1 時間以内に置くと、`growth` の刻みが 1 本しか
#   試せない。
THREE_LEVELS = [
    reply("R1", parent=ROOT, username="よそ子", minutes=10),
    reply("R2", parent="R1", username="nigamilab", minutes=90),
    reply("R3", parent="R2", username="よそ太", minutes=200),
    reply("R4", parent=ROOT, username="よそ子", minutes=400),
    reply("R5", parent=ROOT, username="nigamilab", minutes=1000),
]


@pytest.fixture
def three_level_account(isolated_account):
    """3 階層の偽台帳（返信・取得記録・実測）を 1 本置いた account。"""
    _write_ndjson(_replies_path(isolated_account),
                  THREE_LEVELS + [fetch(marks=[1], age_hours=1.0),
                                   fetch(marks=[6], age_hours=6.1),
                                   fetch(marks=[24], age_hours=24.0)])
    _write_ndjson(_insights_path(isolated_account), [
        insights_row(marks=[1], age_hours=1.0, views=100,
                     account=isolated_account["name"]),
        insights_row(marks=[24], age_hours=24.0, views=300,
                     account=isolated_account["name"]),
    ])
    return isolated_account


# ------------------------------------------------------------ T-V0-1 手計算

def test_T_V0_1_三階層の台帳で枝と深さと参加者と最初の返信が手計算と一致する(
        three_level_account):
    """**手で数えた値と機械の値が合うこと。** ここがずれたら他の指標も信じられない。"""
    result = threadshape_mod.load(three_level_account["name"])
    assert [p["post_id"] for p in result["posts"]] == [ROOT]
    post = result["posts"][0]

    assert post["branches"] == 3, "根の直下は R1・R4・R5 の 3 本"
    assert post["depth"] == 3, "POST1 → R1 → R2 → R3 で 3 階層"
    assert post["replies_total"] == 5
    assert post["author_replies"] == 2, "R2・R5 が作者（handle: nigamilab）"
    assert post["other_replies"] == 3
    assert post["own_unknown"] == 0

    part = post["participants"]
    assert part["count"] == 2, "よそ子・よそ太（作者は除く）"
    assert part["denominator"] == 3, "分母は他人の返信 3 件"
    assert part["top_replies"] == 2 and part["top_share"] == pytest.approx(2 / 3)

    assert post["first_reply_min"] == pytest.approx(10.0), "R1 が投稿の 10 分後"
    assert post["orphan_replies"] == []
    assert post["hour_band"] == "朝", "08:00 JST は 5〜11 時の帯"


def test_深さは根まで辿れない返信を根の直下に寄せない(isolated_account):
    """親が台帳に無い返信を、**枝にも最深にも数えない**（規約 12）。

    頁の境界・削除・採り漏れで親が欠けることがある。そこで根の直下に寄せると
    「枝が 1 本増えた」ことになり、**枝の数がいちばん壊れやすい指標になる。**
    """
    _write_ndjson(_replies_path(isolated_account), [
        reply("R1", parent=ROOT, username="よそ子", minutes=10),
        reply("R9", parent="MISSING", username="よそ太", minutes=20),
        fetch(marks=[1], age_hours=1.0),
    ])
    post = threadshape_mod.load(isolated_account["name"])["posts"][0]
    assert post["branches"] == 1, "R9 を根の直下に寄せていないこと"
    assert post["depth"] == 1
    assert post["orphan_replies"] == ["R9"]
    assert post["replies_total"] == 2, "孤児も返信の総数からは落とさない"


def test_身内か判らない返信を他人に数えない(isolated_account_factory,
                                              three_level_account, monkeypatch):
    """`own` が `None`（handle 集合が不完全）の返信を「他人」にしない（規約 12）。

    `thth/replies.py` は、読めない account 台帳があると非一致を `other` ではなく
    `unknown` にする。**その `unknown` が participants や first_reply_min に
    紛れ込まないこと。**
    """
    # 壊れた台帳を 1 本置く ⇒ handle 集合が不完全 ⇒ 全部 `own: None`。
    broken = os.path.join(three_level_account["accounts_dir"], "こわれ.json")
    with open(broken, "w", encoding="utf-8") as f:
        f.write("{")
    post = threadshape_mod.load(three_level_account["name"])["posts"][0]

    assert post["own_unknown"] == 3, "作者と一致しない 3 件は「判らない」へ"
    assert post["author_replies"] == 2, "一致したものは身内のまま確定してよい"
    assert post["other_replies"] == 0
    assert post["participants"]["count"] == 0
    assert post["first_reply_min"] is None
    assert "他人と判った返信がありません" in post["first_reply"]["reason"]
    assert post["first_reply"]["unknown_reply_was_earlier"] is False


# ------------------------------------------------ T-V0-2 取れていない刻みは null

def test_T_V0_2_取れていない刻みはnullで0ではない(three_level_account):
    """**`0` と「まだ採っていない」を混ぜない**（設計 v1 §3.2.2・規約 12）。"""
    post = threadshape_mod.load(three_level_account["name"])["posts"][0]
    growth = post["growth"]

    # 取れている刻み（1・6・24h の取得記録がある）
    assert growth["1"]["replies"] == 1, "+10 分の R1 だけが 1 時間以内"
    assert growth["6"]["replies"] == 3, "+10/+90/+200 分の 3 件"
    assert growth["24"]["replies"] == 5
    assert growth["6"]["others"] == 2, "うち他人は R1・R3（R2 は作者）"
    assert growth["1"]["covered"] is True and growth["1"]["covered_by"] == "marks"

    # 取れていない刻み
    for mark in ("72", "168"):
        assert growth[mark]["replies"] is None, f"{mark}h は null であって 0 ではない"
        assert growth[mark]["covered"] is False
        assert "取得がまだありません" in growth[mark]["reason"]


def test_返信0件の投稿でも取れた刻みは0で取れていない刻みはnull(isolated_account):
    """**「0 件だった」と「まだ採っていない」が、同じ投稿の中で並ぶ。**

    取得記録（`kind: "fetch"`）だけがあって返信が 1 行も無い投稿——本番の台帳の
    大半がこの形。1h・6h は「採って 0 件」、24h 以降は「まだ採っていない」。
    """
    _write_ndjson(_replies_path(isolated_account),
                  [fetch(marks=[1], age_hours=1.0), fetch(marks=[6], age_hours=6.0)])
    _write_ndjson(_insights_path(isolated_account),
                  [insights_row(marks=[1], age_hours=1.0, views=7,
                                account=isolated_account["name"])])
    post = threadshape_mod.load(isolated_account["name"])["posts"][0]
    assert post["growth"]["1"]["replies"] == 0
    assert post["growth"]["6"]["replies"] == 0
    assert post["growth"]["24"]["replies"] is None
    assert post["replies_total"] == 0


def test_刻みを持たない取得でも経過時間が刻みを超えていれば数える(isolated_account):
    """`--refresh`（`marks: []`）の取得でも、**その時点の会話は見えている**。

    ただし**どちらの根拠で通したかを出す**（`covered_by`）——黙って通さない。
    """
    _write_ndjson(_replies_path(isolated_account), [
        reply("R1", parent=ROOT, username="よそ子", minutes=10),
        fetch(marks=[], age_hours=7.5, trigger="refresh"),
    ])
    _write_ndjson(_insights_path(isolated_account),
                  [insights_row(marks=[1], age_hours=1.0, views=7,
                                account=isolated_account["name"])])
    growth = threadshape_mod.load(isolated_account["name"])["posts"][0]["growth"]
    assert growth["6"]["replies"] == 1 and growth["6"]["covered_by"] == "age"
    assert growth["24"]["replies"] is None, "7.5h の取得は 24h の刻みを満たさない"


def test_postedatが読めなければ刻みを当てずnullにする(isolated_account):
    """実測の台帳が無い（＝`posted_at` が無い）投稿で、**刻みを推測しない。**"""
    _write_ndjson(_replies_path(isolated_account), [
        reply("R1", parent=ROOT, username="よそ子", minutes=10),
        fetch(marks=[1, 6, 24], age_hours=25.0),
    ])
    post = threadshape_mod.load(isolated_account["name"])["posts"][0]
    assert post["posted_at"] is None and post["hour_band"] is None
    assert all(post["growth"][str(m)]["replies"] is None
               for m in threadshape_mod.MARKS)
    assert post["first_reply_min"] is None
    assert "`posted_at`" in post["first_reply"]["reason"]
    # **形そのもの（枝・深さ）は時刻が無くても数えられる。**
    assert post["branches"] == 1 and post["depth"] == 1


def test_一回の取得に刻みが同居していたら印を残す(isolated_account):
    """25 時間後の 1 回で 1h・6h・24h が付いても、**測ったのは 1 点**。"""
    _write_ndjson(_replies_path(isolated_account), [fetch(marks=[1, 6, 24], age_hours=25.0)])
    _write_ndjson(_insights_path(isolated_account),
                  [insights_row(marks=[1], age_hours=25.0, views=1,
                                account=isolated_account["name"])])
    growth = threadshape_mod.load(isolated_account["name"])["posts"][0]["growth"]
    assert growth["1"]["marks_collapsed"] is True


def test_viewsは同じ刻みの実測から経過時間つきで取る(three_level_account):
    """`views_at` は**実測の台帳の同じ刻み**（設計 v1 §3.2.2）。無い刻みは `null`。"""
    views = threadshape_mod.load(three_level_account["name"])["posts"][0]["views_at"]
    assert views["1"]["views"] == 100 and views["1"]["age_hours"] == 1.0
    assert views["24"]["views"] == 300
    assert views["6"]["views"] is None and views["6"]["reason"]


# ------------------------------------------------ T-V0-3 author_reply_effect

def _effect_account(account, branches: list):
    """`branches` は `(枝の主, 作者が返すか, 枝の下の他人の返信数)` の並び。"""
    rows, n = [], 0
    for i, (owner, author_replies, others) in enumerate(branches):
        n += 1
        head = f"B{i}"
        rows.append(reply(head, parent=ROOT, username=owner, minutes=n))
        parent = head
        if author_replies:
            n += 1
            rows.append(reply(f"B{i}A", parent=head, username="nigamilab", minutes=n))
            parent = f"B{i}A"
        for j in range(others):
            n += 1
            rows.append(reply(f"B{i}O{j}", parent=parent, username=f"よそ{j}",
                              minutes=n))
    _write_ndjson(_replies_path(account), rows + [fetch(marks=[1], age_hours=1.0)])
    _write_ndjson(_insights_path(account),
                  [insights_row(marks=[1], age_hours=1.0, views=1,
                                account=account["name"])])
    return threadshape_mod.load(account["name"])["posts"][0]["author_reply_effect"]


def test_T_V0_3_作者返信の効きはnが3未満なら平均を出さない(isolated_account):
    """**n<3 は `null`。n は必ず出す**（設計 v2 §1 規約 1・2）。"""
    effect = _effect_account(isolated_account, [
        ("よそ子", True, 2),
        ("よそ太", False, 1),
    ])
    assert effect["replied"] == {"mean": None, "n": 1}
    assert effect["not_replied"] == {"mean": None, "n": 1}


def test_T_V0_3_作者返信の効きはnが3以上なら平均とnを出す(isolated_account):
    """返した枝 3 本（その後の他人の返信 2・1・0）⇒ 平均 1.0・n=3。

    返さなかった枝 3 本（3・3・0）⇒ 平均 2.0・n=3。
    """
    effect = _effect_account(isolated_account, [
        ("よそ子", True, 2), ("よそ太", True, 1), ("よそ三", True, 0),
        ("よそ四", False, 3), ("よそ五", False, 3), ("よそ六", False, 0),
    ])
    assert effect["replied"]["n"] == 3
    assert effect["replied"]["mean"] == pytest.approx(1.0)
    assert effect["not_replied"]["n"] == 3
    assert effect["not_replied"]["mean"] == pytest.approx(2.0)
    assert "因果ではありません" in effect["definition"]


def test_作者の連投は枝に数えない(three_level_account):
    """根の直下の作者自身の返信（連投）は `self_branches` へ。**2 群に混ぜない。**"""
    effect = threadshape_mod.load(
        three_level_account["name"])["posts"][0]["author_reply_effect"]
    assert effect["self_branches"] == 1, "R5 は作者の連投"
    assert effect["replied"]["n"] + effect["not_replied"]["n"] == 2, "枝は R1・R4"


def test_その後の返信は作者の返信より後のものだけを数える(isolated_account):
    """作者が返す**前**に付いていた返信を「その後」に数えない。

    枝を 3 本置いて平均まで見る（n<3 だと平均が `None` になり、**数え方が
    間違っていても気づけない**）。各枝は「作者より前に 1 件・作者より後に 1 件」
    なので、**その後の返信数はどの枝も 1・平均 1.0**。前の分まで数えていれば 2.0。
    """
    rows = []
    for i in range(3):
        b = f"B{i}"
        rows += [
            reply(b, parent=ROOT, username=f"よそ{i}", minutes=1 + i * 10),
            reply(b + "X", parent=b, username="よそ太", minutes=2 + i * 10),   # 前
            reply(b + "A", parent=b, username="nigamilab", minutes=3 + i * 10),  # 作者
            reply(b + "Y", parent=b + "A", username="よそ太", minutes=4 + i * 10),  # 後
        ]
    _write_ndjson(_replies_path(isolated_account), rows + [fetch(marks=[1], age_hours=1.0)])
    effect = threadshape_mod.load(
        isolated_account["name"])["posts"][0]["author_reply_effect"]
    assert effect["replied"]["n"] == 3
    assert effect["replied"]["mean"] == pytest.approx(1.0), \
        "作者より前の返信まで数えていれば 2.0 になる"


# ------------------------------------------------------------ T-V0-4 要約

def _post_with(account, post_id: str, *, topic: str, posted_at: str,
                replies_n: int = 0) -> None:
    """要約の分母を作るための投稿 1 本（返信は全部、根の直下の他人）。

    **返信の時刻は `posted_at` から測って置く**——固定の時刻にすると、投稿日が
    違うだけで `first_reply_min` が負になり、テストが「合っているのか偶然か」
    分からなくなる。
    """
    posted = datetime.datetime.fromisoformat(posted_at)
    rows = [{"kind": "reply", "post_id": post_id, "id": f"{post_id}-r{i}",
             "username": "よそ子", "text": "x",
             "timestamp": (posted + datetime.timedelta(minutes=10 * (i + 1)))
                          .astimezone(datetime.timezone.utc)
                          .strftime("%Y-%m-%dT%H:%M:%S+0000"),
             "replied_to": {"id": post_id}, "root_post": {"id": post_id},
             "collected_at": "2026-09-10T09:00:00+09:00"}
            for i in range(replies_n)]
    _write_ndjson(_replies_path(account, post_id),
                  rows + [{"kind": "fetch", "post_id": post_id, "marks": [1],
                            "age_hours": 1.0, "trigger": "marks",
                            "collected_at": "2026-09-10T09:00:00+09:00"}])
    _write_ndjson(_insights_path(account, post_id), [
        {"post_id": post_id, "file": f"{post_id}.md", "account": account["name"],
         "topic": topic, "collected_at": "2026-09-10T09:00:00+09:00",
         "posted_at": posted_at, "age_hours": 1.0, "marks": [1],
         "metrics": {"views": 1}}])


def test_T_V0_4_型と時刻帯の要約はnが3未満をcannot_sayに出す(isolated_account):
    """**n<3 の群は中央値を出さず、理由を `cannot_say` に並べる**（規約 2）。

    朝（08 時）3 本・深夜（23 時）2 本を置く。朝は中央値が出て、深夜は出ない。
    """
    # **朝の 3 本のうち 1 本は返信 0 件**——群の n は 3 でも、
    # `first_reply_min` の n は 2 にしかならない（指標ごとに分母が違う）。
    for i, replies_n in enumerate((0, 2, 3)):
        _post_with(isolated_account, f"MORN{i}", topic="コーヒー",
                   posted_at=f"2026-09-1{i}T08:00:00+09:00", replies_n=replies_n)
    for i in range(2):
        _post_with(isolated_account, f"NIGHT{i}", topic="コーヒー",
                   posted_at=f"2026-09-1{i}T23:00:00+09:00", replies_n=1)

    summary = threadshape_mod.load(isolated_account["name"])["summary"]
    朝 = summary["by_hour_band"]["朝"]
    深夜 = summary["by_hour_band"]["深夜"]

    assert 朝["n"] == 3
    assert 朝["metrics"]["replies_total"]["median"] == 2, "返信 0・2・3 の中央値"
    assert 朝["metrics"]["branches"]["median"] == 2
    assert 深夜["n"] == 2 and 深夜["metrics"] == {}
    assert 深夜["cannot_say"] == "時刻帯 `深夜`: n=2（3 未満）"
    assert "時刻帯 `深夜`: n=2（3 未満）" in summary["cannot_say"]
    # **群の n が足りていても、指標ごとの n が足りなければそちらも言えない。**
    assert 朝["metrics"]["first_reply_min"] == {
        "median": None, "n": 2,
        "cannot_say": "時刻帯 `朝` の first_reply_min: n=2（3 未満）"}
    assert "時刻帯 `朝` の first_reply_min: n=2（3 未満）" in summary["cannot_say"]
    assert summary["min_n"] == threadshape_mod.MIN_N


def test_要約は媒体で閉じている(isolated_account_factory):
    """**媒体をまたいで集計しない**（設計 v2 §2.1）。出力に `medium` を刻む。"""
    account = isolated_account_factory("bsky-1", media="bluesky")
    _post_with(account, "B1", topic="コーヒー", posted_at="2026-09-10T08:00:00+09:00")
    result = threadshape_mod.load(account["name"])
    assert result["medium"] == "bluesky"
    assert result["summary"]["medium"] == "bluesky"
    assert result["posts"][0]["medium"] == "bluesky"


def test_返信の台帳が無い投稿を0件として分母に入れない(isolated_account):
    """実測はあるが返信の台帳が無い投稿を、**「返信 0 件」として数えない。**"""
    _post_with(isolated_account, "HAS", topic="コーヒー",
               posted_at="2026-09-10T08:00:00+09:00")
    _write_ndjson(_insights_path(isolated_account, "NOLEDGER"), [
        {"post_id": "NOLEDGER", "file": "x.md", "account": isolated_account["name"],
         "topic": "コーヒー", "posted_at": "2026-09-10T08:00:00+09:00",
         "collected_at": "2026-09-10T09:00:00+09:00", "age_hours": 1.0,
         "marks": [1], "metrics": {"views": 5}}])
    result = threadshape_mod.load(isolated_account["name"])
    assert [p["post_id"] for p in result["posts"]] == ["HAS"]
    assert result["posts_without_reply_ledger"] == ["NOLEDGER"]
    assert result["summary"]["posts"] == 1


def test_時刻帯の境界は左閉右開(isolated_account):
    """`11:00` は昼、`10:59` は朝。**境界を曖昧にしない。**"""
    from thth import jst
    import datetime
    def band(h, m=0):
        return threadshape_mod.hour_band(
            datetime.datetime(2026, 9, 10, h, m, tzinfo=jst.JST))
    assert band(4, 59) == "深夜" and band(5) == "朝"
    assert band(10, 59) == "朝" and band(11) == "昼"
    assert band(16, 59) == "昼" and band(17) == "夕"
    assert band(21, 59) == "夕" and band(22) == "深夜"
    assert threadshape_mod.hour_band(None) is None


# ------------------------------------------------------------------ CLI

def _run_cli(account: str, *, as_json: bool = False, post: str | None = None,
             capsys=None):
    args = argparse.Namespace(account=account, json=as_json, post=post)
    rc = cli_mod.cmd_threads(args)
    return rc, capsys.readouterr()


def test_CLIの人向け画面は投稿ごとに数行と要約の表を出す(three_level_account, capsys):
    rc, out = _run_cli(three_level_account["name"], capsys=capsys)
    assert rc == 0
    assert "枝 3・最深 3・返信 5" in out.out
    assert "参加者 2" in out.out
    assert "1h=1" in out.out and "72h=—" in out.out
    assert "要約" in out.out and "言えないこと" in out.out


def test_CLIは指図の文言を出さない(three_level_account, capsys):
    """**事実だけ**（発注・設計 v2 §1 規約 3 は泉の答えの話で、この口は素の観測）。"""
    rc, out = _run_cli(three_level_account["name"], capsys=capsys)
    assert rc == 0
    for 禁句 in ("すべき", "べきです", "しましょう", "おすすめ", "推奨"):
        assert 禁句 not in out.out, f"指図の文言が出ています: {禁句}"


def test_CLIのjsonは分子と分母と除外の理由を持つ(three_level_account, capsys):
    rc, out = _run_cli(three_level_account["name"], as_json=True, capsys=capsys)
    assert rc == 0
    data = json.loads(out.out)
    post = data["posts"][0]
    assert post["participants"]["denominator"] == 3, "分母"
    assert post["growth"]["72"]["reason"], "除外の理由"
    assert data["summary"]["cannot_say"], "言えないことの理由"
    assert data["marks"] == list(threadshape_mod.MARKS)


def test_CLIは無い台帳で1を返す(capsys):
    # 名前は ASCII（`load_account()` が名前を先に検査する・監査 2026-09-14 P2-2）。
    rc, out = _run_cli("sonna-account-wa-nai", capsys=capsys)
    assert rc == 1
    assert "台帳が無い" in out.err


def test_postで1件だけに絞れる(isolated_account, capsys):
    _post_with(isolated_account, "A1", topic="コーヒー",
               posted_at="2026-09-10T08:00:00+09:00")
    _post_with(isolated_account, "A2", topic="コーヒー",
               posted_at="2026-09-10T08:00:00+09:00")
    rc, out = _run_cli(isolated_account["name"], as_json=True, post="A2",
                       capsys=capsys)
    assert rc == 0
    assert [p["post_id"] for p in json.loads(out.out)["posts"]] == ["A2"]


def test_読むだけで何も書かない(three_level_account):
    """**この口はどのファイルも触らない**（`thth/threadshape.py` の約束）。"""
    repo = three_level_account["repo_dir"]
    before = {}
    for dirpath, _dirs, files in os.walk(repo):
        for name in files:
            p = os.path.join(dirpath, name)
            before[p] = os.path.getmtime(p)
    threadshape_mod.load(three_level_account["name"])
    after = {}
    for dirpath, _dirs, files in os.walk(repo):
        for name in files:
            p = os.path.join(dirpath, name)
            after[p] = os.path.getmtime(p)
    assert before == after


# ------------------------------------------------------- T-V0-5 本物の台帳

# **この Mac にある本物の利用者 repo**（設計 v2 §6 v2-0 の出口条件）。
# 無ければ skip——CI や他人の機械には無い。**読むだけ。書かない。**
REAL_REPOS = {
    "kopicha-threads": os.path.expanduser("~/Developer/kopicha"),
    "nigamilab-threads": os.path.expanduser("~/Developer/nigamilab"),
    "asmon-kanto-threads": os.path.expanduser("~/Developer/asmon"),
}


@pytest.mark.parametrize("account_name,repo", sorted(REAL_REPOS.items()))
def test_T_V0_5_本物の台帳で落ちない(account_name, repo, isolated_account_factory,
                                       tmp_path, capsys):
    """**本物の返信の台帳**で rc=0・人向けと `--json` の両方が出ること。

    `THTH_ROOT` は tmp に向いている（`thth_root` fixture）ので、`state` も
    `logs` も本物には触れない。account 台帳もテスト用の隔離した dir に作る。
    **利用者 repo は読むだけ。**
    """
    if not os.path.isdir(os.path.join(repo, "data", "sns", "replies")):
        pytest.skip(f"本物の台帳がありません: {repo}")
    account = isolated_account_factory(account_name, repo_dir=repo,
                                        handle=account_name.split("-")[0])

    rc, out = _run_cli(account["name"], capsys=capsys)
    assert rc == 0, out.err
    assert "要約" in out.out

    rc, out = _run_cli(account["name"], as_json=True, capsys=capsys)
    assert rc == 0, out.err
    data = json.loads(out.out)
    assert data["account"] == account_name
    for post in data["posts"]:
        # **どの投稿も、刻みごとに「取れた／取れていない」を言い切れている。**
        for mark in threadshape_mod.MARKS:
            cell = post["growth"][str(mark)]
            assert (cell["replies"] is None) == (not cell.get("covered")
                                                  or post["posted_at"] is None)


def test_T_V0_5_本物の台帳を1バイトも書き換えない(isolated_account_factory, capsys):
    """**利用者 repo は読むだけ**（発注の作法）。git の作業ツリーが汚れないこと。"""
    repo = REAL_REPOS["kopicha-threads"]
    if not os.path.isdir(os.path.join(repo, "data", "sns", "replies")):
        pytest.skip(f"本物の台帳がありません: {repo}")
    account = isolated_account_factory("kopicha-threads", repo_dir=repo,
                                        handle="kopi_chaba")
    before = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                             capture_output=True, text=True).stdout
    _run_cli(account["name"], capsys=capsys)
    after = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    assert before == after


def test_要約の表は中央値だと画面に書いてある(capsys, monkeypatch):
    """運用の指摘（2026-09-13）: 8 本中 1 本だけ返信 14 でも表は 0 と出る。中央値と書かないと
    「一般名詞は返信 0」と読める。見出しと脚注の両方に出す。"""
    import types
    from thth import cli as cli_mod
    minimal = {"account": "x", "medium": "threads", "marks": [1, 6, 24, 72, 168], "posts": [],
               "posts_without_reply_ledger": [], "broken": [], "unreadable_accounts": [],
               "summary": {"medium": "threads", "by_kind": {}, "by_hour_band": {}, "cannot_say": []}}
    monkeypatch.setattr(cli_mod.threadshape_mod, "load", lambda *_a, **_k: minimal)
    rc = cli_mod.cmd_threads(types.SimpleNamespace(account="x", post=None, json=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "中央値" in out, out
    assert "投稿ごとの行を見てください" in out, out
