"""観測の地図の見方（設計 3.5.0 §1 の層・§3）。**読むだけ。**

同じ点に層を重ねる:

  1. 自分の層: その話題（`topic`）で出した投稿の数と、`measured` の views・likes・
     replies の中央値と n。**計算は既存の口のまま**（`measured.load()` と
     `analytics_comparison._population()`——`analytics-report --by topic` と同じ
     24 時間の刻み・同じ `min_n`）。account をまたいで足さない（媒体ごとに並べる）。

  2. 広場の層（3.4.0）: その話題に結んだ施策・気づき・問いの数と判定・追試の数。
     **結び方は語の一致**（書き込みの title か scope_note〔媒体・企画の範囲〕に点の語が
     含まれる）。広場の記録に欄を足さない（3.4.0 で置いた書き込みもそのまま結べる）。
     読めるのは `plaza.access()` が許す範囲だけ（自分の持ち主の書き込みと、参加して
     いれば他の持ち主の open の写し）。**地図から広場へは何も渡さない**（一方向）。

数には分母、取れないものは null と静的な理由。
"""
from __future__ import annotations

from . import accounts, after_cli, analytics_comparison, map_store, measured
from . import plaza as plaza_mod

# 自分の層で並べる指標（設計 §1「views・likes・replies の中央値と n」）。
SELF_METRICS = ("views", "likes", "replies")
SELF_MIN_N = after_cli.DEFAULT_MIN_N


def _topic_key(value):
    topic, valid = after_cli._normalized_topic(value)
    if not valid or not topic:
        return None
    return map_store.node_key(topic)


def self_layer(configs, words, *, since, now, min_n=SELF_MIN_N):
    """点ごとの自分の層 `{word: {"by_account": {name: 升目}}}`（読むだけ）。

    - `posts`: 窓（`since`〜`now`）の中でその topic で出した投稿の数。分母
      `denominator` は同じ窓の投稿の数（topic を問わない）。
    - `metrics`: 根の投稿の 24 時間の値の中央値と n（`analytics-report` と同じ母集団）。
      n が `min_n` に届かなければ中央値は null・理由 `below_min_n`。
    """
    keys = {word: map_store.node_key(word) for word in words}
    layer = {word: {"by_account": {}} for word in words}
    for name in sorted(configs):
        medium = configs[name].get("media")
        try:
            loaded = measured.load(name, observation_metadata=True)
        except (accounts.AccountError, TypeError, ValueError, AttributeError, OSError):
            for word in words:
                layer[word]["by_account"][name] = {"medium": medium, "posts": None,
                                                   "denominator": None, "metrics": None,
                                                   "cannot_say": "measured_unreadable"}
            continue
        in_window = []
        for post in loaded["posts"]:
            posted = analytics_comparison._timestamp(post.get("posted_at"))
            if posted is None or not since <= posted <= now:
                continue
            in_window.append((str(post["post_id"]), posted, post))
        incomplete = bool(loaded.get("broken"))
        for word in words:
            members = [item for item in in_window if _topic_key(item[2].get("topic")) == keys[word]]
            roots = [item for item in members if analytics_comparison._root_exclusion(item[2]) is None]
            population = analytics_comparison._population(roots, since, now, now, min_n)
            metrics = {}
            for metric in SELF_METRICS:
                stat = population["metrics"][metric]
                metrics[metric] = {"median": stat["median"], "n": stat["n_eligible"],
                                   "denominator": stat["n_total"],
                                   "reason": None if stat["median"] is not None else "below_min_n"}
            layer[word]["by_account"][name] = {
                "medium": medium, "posts": len(members), "denominator": len(in_window),
                "root_posts": population["n_total"], "metrics": metrics,
                "basis": {"source": "measured", "mark_hours": 24, "min_n": min_n,
                          "population": "root_posts"},
                "incomplete_sources": incomplete,
                "cannot_say": "measured_partly_unreadable" if incomplete else None}
    return layer


# ---------------------------------------------------------------- 広場の層

PLAZA_LINK_BASIS = "title_or_scope_note_contains_word"
# 1 つの点に並べる書き込みの id の上限（新しい順）。数は全部数える。
PLAZA_RECENT = 5


def _plaza_counts():
    return {"measure": 0, "finding": 0, "question": 0, "denominator": 0,
            "verdicts": {"adopted": 0, "dropped": 0, "inconclusive": 0, "none": 0},
            "trials": {"reproduced": 0, "not_reproduced": 0, "not_tried": 0, "denominator": 0},
            "recent": []}


def plaza_layer(viewer, words, *, since=None, now=None):
    """点ごとの広場の層 `{word: {"own": 升目, "open": 升目}}`（読むだけ）。

    `own` は自分の持ち主（viewer の project）の書き込み、`open` は参加した他の持ち主の
    open の写し（不参加なら null・理由 `plaza_not_joined`）。分母 `denominator` は
    その範囲で読める書き込みの数（語を問わない）。非表示の書き込みは数えない。
    `since` があれば置かれた時刻がそれより後のものだけ。
    """
    try:
        records, _broken = plaza_mod.load_all()
        joined = plaza_mod.members()
    except plaza_mod.PlazaError:
        return {word: {"own": None, "open": None, "link_basis": PLAZA_LINK_BASIS,
                       "cannot_say": "plaza_store_unavailable"} for word in words}
    participating = viewer.admin or bool(viewer.projects & joined)
    keys = {word: map_store.node_key(word) for word in words}
    layer = {word: {"own": _plaza_counts(), "open": _plaza_counts() if participating else None,
                    "open_reason": None if participating else "plaza_not_joined",
                    "link_basis": PLAZA_LINK_BASIS, "cannot_say": None} for word in words}
    totals = {"own": 0, "open": 0}
    readable = []
    for record in records:
        if record.get("hidden"):
            continue
        at = analytics_comparison._timestamp(record.get("at"))
        if since is not None and (at is None or at < since or (now is not None and at > now)):
            continue
        level = plaza_mod.access(record, viewer, joined)
        if level is None:
            continue
        if level == "own":
            scope, title, note = "own", record.get("title"), record.get("scope_note")
        else:
            copy = record.get("open_copy") or {}
            scope, title, note = "open", copy.get("title"), copy.get("scope_note")
        totals[scope] += 1
        readable.append((record, scope, map_store.node_key(f"{title or ''}\n{note or ''}")))
    readable.sort(key=lambda item: (item[0]["at"], item[0]["plaza_id"]), reverse=True)
    for word in words:
        for scope in ("own", "open"):
            if layer[word][scope] is not None:
                layer[word][scope]["denominator"] = totals[scope]
        for record, scope, text in readable:
            cell = layer[word][scope]
            if cell is None or keys[word] not in text:
                continue
            cell[record["kind"]] += 1
            verdict = (record.get("verdict") or {}).get("verdict")
            if record["kind"] == "measure":
                cell["verdicts"][verdict if verdict in plaza_mod.VERDICTS else "none"] += 1
            trials = plaza_mod.trial_counts(record)
            for result in plaza_mod.TRIAL_RESULTS:
                cell["trials"][result] += trials[result]
            cell["trials"]["denominator"] += trials["denominator"]
            if len(cell["recent"]) < PLAZA_RECENT:
                cell["recent"].append(record["plaza_id"])
    return layer
