"""観測の地図の見方（設計 3.5.0 §1 の層・§3）。**読むだけ。**

同じ点に層を重ねる:

  1. 自分の層: その話題（`topic`）で出した投稿の数と、`measured` の views・likes・
     replies の中央値と n。**計算は既存の口のまま**（`measured.load()` と
     `analytics_comparison._population()`——`analytics-report --by topic` と同じ
     24 時間の刻み・同じ `min_n`）。account をまたいで足さない（媒体ごとに並べる）。

数には分母、取れないものは null と静的な理由。
"""
from __future__ import annotations

from . import accounts, after_cli, analytics_comparison, map_store, measured

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
