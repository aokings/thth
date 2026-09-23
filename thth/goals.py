"""投稿の目的 `goal:`（設計 3.6.0 §A）。

約束（§0）: **投稿ごとに目的を 1 つ書けば、道具が目的ごとに、その目的に合う物差しで
比べる。** 目的は人と LLM が決め、道具は推測しない。

規律:

  (a) 値は 4 つの固定語（`reach`・`click`・`follow`・`reply`）。無ければ `none`
      （数えるときは「目的なし」の層）。それ以外は lint が `goal_invalid` で断る。
  (b) **承認の指紋に入れない**。目的は測り方の札で、公開される中身ではない。
      承認のあとに書き換えても `approval_stale` にしない——代わりに承認の時点の
      目的（`approved_goal`・`thth approve` が書く）と公開の時点の目的が違えば、
      runs と sent に「目的の変更」（`goal_change`）として残す。
  (c) **本文のメモ（「目的: 誘導」）は読まない**。front-matter の `goal:` だけが正。
      kopicha が本文のメモに書き始めた目的（09-24 時点で queue の 19 本）を道具が
      拾うと、書いた人の意図と違う語に写した推測が記録に入る。
  (d) 連投（`thth: 2`）は束の front-matter に 1 つ（段ごとには書けない——
      `bundle.POST_KEYS` に無い鍵は段の検査が断る）。
"""
from __future__ import annotations

import os

GOALS = ("reach", "click", "follow", "reply")
NONE = "none"
# 公開の時点で `goal:` の行が 4 語のどれでもなかった（lint を通らない書き換えが承認の
# あとに入った）ときに記録に残す語。数えるときは「不明」の層。
UNKNOWN = "unknown"
# 公開の記録（sent・連投の実行記録）に goal の欄そのものが無い投稿——3.6.0 より前、
# 目的を書く口が無かった頃の投稿（masaru 裁定 09-24）。`none`（欄はあって目的なし）と
# 混ぜない。**記録には書かない語**（数えるときだけの層）。
UNRECORDED = "unrecorded"
LABELS = {"reach": "表示", "click": "サイト誘導", "follow": "フォロー", "reply": "会話",
          NONE: "目的なし", UNKNOWN: "不明", UNRECORDED: "記録なし（目的を書く口が無かった頃）"}
# 数えるときの層（分母には `none` と `unrecorded` の両方を出す）。
LAYERS = GOALS + (NONE, UNRECORDED)
# 記録に書いてよい語（sent・runs・連投の実行記録）。
RECORDED = GOALS + (NONE, UNKNOWN)

KEY = "goal"
APPROVED_KEY = "approved_goal"
GOAL_INVALID = "goal_invalid"
GOAL_INVALID_MESSAGE = "goal は reach・click・follow・reply のどれか 1 つです（無ければ書かない）"


def normalize(raw) -> str | None:
    """front-matter の値を 4 語か `none` に。**それ以外は None（読めない）。**

    大文字小文字や表記の揺れは畳まない——`Reach`・`誘導` を道具が読み替えると、
    書いた人が知らない語が記録に入る。lint が断り、書いた人が直す。
    """
    if raw is None:
        return NONE
    value = str(raw).strip()
    if not value:
        return NONE
    return value if value in GOALS else None


def goal_of(draft) -> str:
    """原稿（`QueueFile`・`Bundle`）の目的。**front-matter の `goal:` だけを見る。**

    本文（`draft.body`）は読まない（上の (c)）。読めない値は `unknown`。
    """
    value = normalize((draft.front_matter or {}).get(KEY))
    return UNKNOWN if value is None else value


def lint_errors(fm) -> list:
    """lint の 1 行（無ければ空）。符丁 `goal_invalid` で始める（`lint.reason_code`）。"""
    raw = (fm or {}).get(KEY)
    if normalize(raw) is not None:
        return []
    return [f"{GOAL_INVALID}: {GOAL_INVALID_MESSAGE}（{raw!r}）"]


def approved_value(prepared_goal) -> str:
    """`thth approve` が `approved_goal` に書く値（承認の時点の目的）。"""
    return prepared_goal if prepared_goal in GOALS else NONE


def change(draft) -> dict | None:
    """承認の時点と公開の時点で目的が違えば `{"from", "to"}`、同じ・分からなければ None。

    `approved_goal` を持たない原稿（3.6.0 より前の承認）は「承認の時点の目的が
    分からない」ので、変更とは言わない（無いものを `none` と読んで変更を作らない）。
    """
    fm = draft.front_matter or {}
    before = fm.get(APPROVED_KEY)
    before = str(before).strip() if before is not None else ""
    if before not in GOALS + (NONE,):
        return None
    after = goal_of(draft)
    if after == before:
        return None
    return {"from": before, "to": after}


def change_line(value) -> str | None:
    """ログに出す 1 行（変更が無ければ None）。"""
    if not value:
        return None
    return (f"目的の変更: {value['from']} → {value['to']}"
            "（承認の指紋には入らないので出します・runs と sent に残します）")


def valid_change(value) -> bool:
    return (type(value) is dict and set(value) == {"from", "to"}
            and value["from"] in RECORDED and value["to"] in RECORDED
            and value["from"] != value["to"])


# ------------------------------------------------------------ 記録から引く

def recorded_goals(account_name: str) -> dict:
    """公開の時点に記録した目的 `{post_id: goal}`（読むだけ）。

    材料は 2 つだけ: `state/<account>/sent/`（単発の公開・同席の送信・inflight を
    解いた記録）と連投の実行記録（`state/threads/`・段ごとの `goal`）。**いまの原稿は
    読まない**——公開のあとに `goal:` を書き換えても、過去の投稿の層は動かない
    （`measured` が `account` を採取時点の行から読むのと同じ筋）。記録に goal の欄
    そのものが無い投稿（3.6.0 より前）はここに入らず、`goal_for()` が `unrecorded`
    として数える。欄があって値が空なら `none`。
    """
    from . import accounts, sent, threadrun
    out = {}
    try:
        state_dir = accounts.state_dir_for(account_name)
    except accounts.AccountError:
        return out
    for row in sent.records(state_dir):
        if KEY not in row:
            continue
        value = row.get(KEY)
        out[str(row["post_id"])] = value if value in RECORDED else (NONE if not value else UNKNOWN)
    try:
        names = sorted(n for n in os.listdir(threadrun.runs_dir())
                       if n.endswith(".json"))
    except OSError:
        names = []
    for name in names:
        try:
            run = threadrun.load(name[:-len(".json")])
        except Exception:   # noqa: BLE001 — 読めない実行記録は公開の経路が止める
            continue
        if not isinstance(run, dict) or run.get("account") != account_name:
            continue
        for post in run.get("posts") or []:
            if isinstance(post, dict) and post.get("post_id") and KEY in post:
                value = post[KEY]
                out[str(post["post_id"])] = (value if value in RECORDED
                                             else NONE if not value else UNKNOWN)
    return out


def goal_for(recorded: dict, post_id) -> str:
    """記録から引いた目的。記録に goal の欄が無ければ `unrecorded`（3.6.0 より前は
    目的を書く口が無かった——「目的なし」の `none` と混ぜない・masaru 裁定 09-24）。"""
    return recorded.get(str(post_id), UNRECORDED)


# ------------------------------------------------------------ 主な物差し

# 投稿単位で目的ごとに先頭に出す指標（`thth observe`・地図の表）。reach は媒体で違う
# ——Threads と X は views、Bluesky と Mastodon は views を持たないので likes と reposts。
REACH_METRICS = {"threads": ("views",), "x": ("views",),
                 "bluesky": ("likes", "reposts"), "mastodon": ("likes", "reposts")}
REPLY_METRICS = ("replies",)
# 投稿単位の数字が一次資料に無い目的（設計 §A2・D）。**割らずに「言えない」と言う。**
PER_POST_CANNOT_SAY = {"click": "per_post_clicks_unavailable",
                       "follow": "per_post_follows_unavailable"}


def primary_metrics(goal, medium) -> tuple:
    """その目的・その媒体で先頭に出す投稿単位の指標（無ければ空）。"""
    if goal == "reach":
        return REACH_METRICS.get(medium, ("views",))
    if goal == "reply":
        return REPLY_METRICS
    return ()
