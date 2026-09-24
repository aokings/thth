"""`thth where … --also … --suggest`——検索の語の候補（設計 3.9.0 §B）。**その場で数えて出すだけ。**

2 つの出どころを並べる（自分の台帳からを先頭に）:

  1. `from_self`（自分のデータ）: 地図の点・原稿の topic（原稿の goal の本数を添える）・
     過去の自分の投稿で反応（24h の likes＋replies）の中央値が高かった topic。
  2. `from_search`（その場の人が書いた語）: also に合った投稿の本文から、漢字・カタカナ・
     英字（数字まじり可）の 2〜12 字の連なりを数える。**依存を足さない静的な方法**
     （形態素解析はしない）。検索語・also の語・数字だけの語は除き、2 投稿以上に出た語
     だけを上位 10 まで。

規律:

  (a) **本文・username・post_id・author_key を返さない**。返すのは語と、その語が出た
      投稿の数と分母だけ。本文の @名前 と URL は数える前に外す。取れた投稿の username・
      author_key と同じ語も候補にしない（名前を語として漏らさない）。
  (b) **保存しない**。候補も当たり率も、`data/` にも runs にも書かない（runs の 1 行は
      従前どおり語と件数だけ）。日をまたいで貯めるのは世間の層と同じ条件（Meta への
      説明の申請）まで入れない——設計 3.9.0 §E。
  (c) MCP の `where_to_appear` には足さない（CLI の下調べ用・3.7.0 §C と同じ線）。
"""
from __future__ import annotations

import collections
import os
import re
import unicodedata

MAX_CANDIDATES = 10
MIN_POSTS = 2
MIN_CHARS = 2
MAX_CHARS = 12
MAX_DRAFT_TOPICS = 10
MAX_REACTED_TOPICS = 5
SEARCH_LABEL = "その場の人が書いた語"
SELF_LABEL = "自分のデータ"
SEARCH_BASIS = ("also に合った投稿の本文の、漢字・カタカナ・英字（数字まじり可）の 2〜12 字の"
                "連なり。検索語・also の語・数字だけの語を除き、2 投稿以上に出た語")
REACTED_BASIS = "過去の自分の投稿の 24h の likes＋replies の中央値（topic ごと・値のある投稿だけ）"
STORAGE_NOTE = "候補は表示だけで、道具は保存しません（日をまたいで貯めるのは世間の層と同じ条件のあとで）"

# 漢字（CJK 統合漢字と拡張 A・々〆ヶ）・カタカナ（長音を含む）・英数字の連なり。
# **ひらがなは切れ目にする**（助詞・活用語尾を語に含めない）。
_TOKEN_RE = re.compile(r"[一-鿿㐀-䶿々〆ヶ]+|[ァ-ヺー]+|[a-z0-9]+")
# 数える前に外すもの（URL・@名前）。
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_MENTION_RE = re.compile(r"@[\w.\-]+")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def tokens(text) -> set:
    """1 本の本文の語の候補（重複なし）。2〜12 字の連なりだけ——長い連なりは切らずに捨てる。"""
    if not isinstance(text, str):
        return set()
    value = _norm(text)
    value = _MENTION_RE.sub(" ", _URL_RE.sub(" ", value))
    out = set()
    for match in _TOKEN_RE.findall(value):
        if not MIN_CHARS <= len(match) <= MAX_CHARS:
            continue
        if match.isdigit() or set(match) == {"ー"}:
            continue
        out.add(match)
    return out


def from_search(rows, *, words, also) -> dict:
    """also に合った投稿（`rows`）から、その場の人が書いた語の候補。**語と数だけ返す。**"""
    excluded = {_norm(w) for w in list(words) + list(also)}
    # 取れた投稿の名前も候補にしない（本文に自分の名前を書く人がいる）。
    for row in rows:
        for key in ("username", "author_key"):
            if isinstance(row.get(key), str) and row[key]:
                excluded.add(_norm(row[key]))
    counter = collections.Counter()
    for row in rows:
        counter.update(tokens(row.get("text")) - excluded)
    total = len(rows)
    ranked = sorted(((word, n) for word, n in counter.items() if n >= MIN_POSTS),
                    key=lambda item: (-item[1], item[0]))[:MAX_CANDIDATES]
    return {"label": SEARCH_LABEL, "basis": SEARCH_BASIS, "n_posts": total,
            "min_posts": MIN_POSTS, "max_candidates": MAX_CANDIDATES,
            "candidates": [{"word": word, "n_posts": n, "denominator": total}
                           for word, n in ranked],
            "reason": None if ranked else ("no_matched_posts" if not total
                                           else "no_word_in_two_posts")}


# ------------------------------------------------------------ 自分のデータ

def _map_nodes(cfg) -> tuple[list, str | None]:
    from . import map_store
    project = (cfg or {}).get("project")
    if not isinstance(project, str) or not project:
        return [], None
    try:
        config = map_store.load_config(project)
    except Exception:   # noqa: BLE001 — 読めない地図は「読めない」と言って続ける
        return [], "map_unreadable"
    return [row["word"] for row in config.get("nodes") or [] if isinstance(row.get("word"), str)], None


def _draft_topics(name, cfg) -> tuple[list, str | None]:
    """queue の原稿（取り下げを除く）の topic ごとの本数と goal の本数。読むだけ。"""
    from . import accounts, goals, queuefile
    repo = accounts.resolved_repo_dir(cfg)
    queue_dir = (cfg or {}).get("queue_dir")
    if not repo or not isinstance(queue_dir, str) or not queue_dir:
        return [], None
    folder = os.path.join(repo, queue_dir)
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".md"))
    except FileNotFoundError:
        return [], None
    except OSError:
        return [], "queue_unreadable"
    by_topic = {}
    for fname in names:
        try:
            qf = queuefile.parse(os.path.join(folder, fname))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        fm = qf.front_matter
        if qf.malformed or fm.get("account") != name or fm.get("status") == "withdrawn":
            continue
        raw = fm.get("topic")
        topic = queuefile.normalize_topic(raw) if isinstance(raw, str) else None
        if not topic:
            continue
        entry = by_topic.setdefault(topic, {"n": 0, "goals": collections.Counter()})
        entry["n"] += 1
        entry["goals"][goals.normalize(fm.get(goals.KEY)) or goals.UNKNOWN] += 1
    ranked = sorted(by_topic.items(), key=lambda item: (-item[1]["n"], item[0]))[:MAX_DRAFT_TOPICS]
    return [(topic, entry["n"], dict(sorted(entry["goals"].items()))) for topic, entry in ranked], None


def _reacted_topics(name, now) -> tuple[list, str | None]:
    from . import after_cli, analytics_comparison as comparison, measured
    try:
        loaded = measured.load(name, observation_metadata=True)
    except Exception:   # noqa: BLE001
        return [], "measured_unreadable"
    by_topic = {}
    for post in loaded["posts"]:
        topic, valid = after_cli._normalized_topic(post.get("topic"))
        posted = comparison._timestamp(post.get("posted_at"))
        if not valid or not topic or posted is None:
            continue
        entry = by_topic.setdefault(topic, {"n_posts": 0, "values": []})
        entry["n_posts"] += 1
        observation, _rejected = comparison._observation(post, posted, now, 24)
        metrics = (observation or {}).get("metrics") or {}
        likes, replies = metrics.get("likes"), metrics.get("replies")
        if isinstance(likes, (int, float)) and isinstance(replies, (int, float)):
            entry["values"].append(likes + replies)
    rows = []
    for topic, entry in by_topic.items():
        if not entry["values"]:
            continue
        median = comparison._median(entry["values"])
        if median and median > 0:
            rows.append((topic, median, len(entry["values"]), entry["n_posts"]))
    rows.sort(key=lambda row: (-row[1], -row[2], row[0]))
    return rows[:MAX_REACTED_TOPICS], None


def from_self(name, cfg, *, words, also, now) -> dict:
    """自分の台帳からの候補（地図の点・原稿の topic・反応の多かった topic）。"""
    excluded = {_norm(w) for w in list(words) + list(also)}
    merged, cannot_say = {}, []

    def add(word, source, **detail):
        key = _norm(word)
        if not key or key in excluded:
            return
        entry = merged.setdefault(key, {"word": word, "sources": []})
        entry["sources"].append(source)
        entry.update(detail)

    nodes, why = _map_nodes(cfg)
    if why:
        cannot_say.append(why)
    for word in nodes:
        add(word, "map_node")
    drafts, why = _draft_topics(name, cfg)
    if why:
        cannot_say.append(why)
    for topic, n, goal_counts in drafts:
        add(topic, "draft_topic", draft={"n": n, "goals": goal_counts})
    reacted, why = _reacted_topics(name, now)
    if why:
        cannot_say.append(why)
    for topic, median, n_observed, n_posts in reacted:
        add(topic, "reacted_topic", reacted={"median_likes_plus_replies_24h": median,
                                             "n_observed": n_observed, "n_posts": n_posts})
    return {"label": SELF_LABEL, "candidates": list(merged.values()),
            "sources_order": ["map_node", "draft_topic", "reacted_topic"],
            "reacted_basis": REACTED_BASIS, "cannot_say": cannot_say}


def build(name, cfg, rows, *, words, also, now) -> dict:
    """1 account の候補（自分のデータを先頭に）。`rows` は also に合った行（外へは出さない）。"""
    search = (from_search(rows, words=words, also=also) if also else
              {"label": SEARCH_LABEL, "candidates": [], "reason": "no_also",
               "message": "--also が無いので、その場の語の候補は出しません"})
    return {"from_self": from_self(name, cfg, words=words, also=also, now=now),
            "from_search": search, "storage": "display_only", "storage_note": STORAGE_NOTE}


# ------------------------------------------------------------ 当たり率

def also_hits(entries, *, period_days) -> list:
    """語ごとの「取れた件数・also に合った件数・当たり率」を当たり率の高い順に。

    `entries`: `[(word, n_before, n_matched)]`。0 件の語には「also に合った投稿が 0 件
    （直近 n 日）」を添える（n は `period_days[word]`・不明なら「期間は不明」）。
    """
    rows = []
    for word, n_before, n_matched in entries:
        rate = round(n_matched / n_before, 4) if n_before else None
        row = {"word": word, "n_fetched": n_before, "n_matched": n_matched, "hit_rate": rate,
               "denominator": n_before, "reason": None if n_before else "no_posts_fetched"}
        if n_matched == 0:
            days = period_days.get(word)
            span = f"直近 {days} 日" if days is not None else "期間は不明"
            row["zero_note"] = f"also に合った投稿が 0 件（{span}）"
        rows.append(row)
    rows.sort(key=lambda row: (row["hit_rate"] is None, -(row["hit_rate"] or 0), row["word"]))
    return rows
