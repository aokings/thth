"""柵の行の表（設計 `docs/設計_上限と連投の柵_2026-10-17.md` §6・段 1〜2）。

**段 1 は今の数と振る舞いをそのまま写す**（4.0.1 の時点: ブックマークの専用の窓・記事の burst・SNS の
返信の順番待ち・はてなの同じ配信は 1 件、など）。行の数は口座・主体の台帳（段 3 から `limits` の行の id と、
今の鍵＝別名・`thth/limits_config.py`）・ブログの欄から引き、明示が無ければ媒体の台帳の既定か表の `default`。この表が持つのは「どの動作に・どの順で・どの信号で・
どの単位と窓で・超えたらどう振る舞うか・断りの符丁」で、`thth/guard.py` はこの表を順に当てるだけになる。

行の形（§6.2）:

- `id`: `<動作>.<欄>`（`post.burst`・`bookmark.daily` など）
- `family`: 当てる道（`sns`・`hatenablog`・`hatenabookmark`・`reaction`・`fotolife`）。SNS の道は「主体を
  使わない口座のすべて」。段 2 から SNS・反応の道は口座の `media` で行を絞る（行の `media` に無い媒体には
  共通の行 `write.floor`（`media: *`）だけ。媒体を言わない口座は今までどおりすべての行）
- `phase`: `halt`（止めの印に並ぶ・媒体の断りの印）・`safety`（安全装置・超えたら止める）・`assess`（見立て）
- `purpose`: `platform`（a 媒体の事実）・`safety`（b）・`preference`（c 運用の好み）
- `signal`: `assess.SIGNALS` の 1 つ
- `actions`: 数える動作・`applies_to`: この行を当てる動作（`*` はすべて）
- `scope`: `account`・`blog`・`principal`・`owner`
- `window`: `day`（JST の日）・`{"minutes": n}`・`{"hours": h}`・`quiet_hours`・`forever`
- `key`: 窓の中で 1 件と数える単位（`op`・`target`・`url`・`host`・`own`）
- `param`: 数の出所（`("account", 欄)`・`("principal", 欄)`・`("blog", 欄)`・`("config", 欄)`・`("fixed", 数)`・
  `("ledger", None)`（媒体の台帳だけ）・`("fotolife", 欄)`（主体の `fotolife` の欄））
- `ledger`: 媒体 → 台帳の結び（`trait`・`confidence`・`count_evidence`・`count_confidence`・既定の数と窓）
- `on_exceed`: `stop`・`refuse`・`wait`・`advise`・`hold`・`halted`（印がすでに置かれている＝止めとして断る）
- `refusal`／`detail`: 今の符丁と詳細（増やさない・§7.1）
- `trait`: 根拠の項目。台帳に結んだ行は台帳の id（媒体ごとは `resolve` が選ぶ）、THTH だけの行は `thth:<id>`
- `source`: 出所の短い文

**当てる順は表の順**（今の `guard.check` の `if` の順そのもの）。最初に当たった行が結論になる（§5.3 の
「強い方」でなく今の first-hit・設計の末尾「段 1 で決めたこと」「段 2 で決めたこと」）。段 2 で足した安全装置は
`bookmark.stop` がブックマークの行の最初・`write.floor` が各道の最後。

**台帳（段 0 の `thth/media_traits/`）との結び（段 2・監査 中-1）**:

- 台帳の `defaults` の行（媒体 × 行の id）は、`LEDGER_MAP`（写像の表・この 1 か所）を通った行だけを受ける。
  写像に無い台帳の行は `LEDGER_NOT_ROWS`（理由つきで「表の行にしない」）に無ければ `build_policy` が断る。
- 写像の先が `THTH_DEFAULTS` の行なら、形（信号・動作・単位・鍵・振る舞い・目的・符丁・窓の種類）が一致する
  ことを確かめ、**根拠（媒体ごとの `trait`・確かさ・数の根拠）と既定の数**を行に結ぶ。台帳（口座・主体）に
  明示の値があればそれが勝つ（`origin`）。無ければ媒体の台帳の数（今の既定と同じ・試験が縛る）。
- 写像の先が `LEDGER_BORN` の行（`bookmark.stop`・`upload.daily`）は、台帳の既定から行が生まれる（道・順・
  数の出所だけここ）。台帳から既定を消せば行も消える（T-M-2）。
- 表は import のときに作らない。最初の `rules_for`（または `POLICY` を読んだとき）に台帳を読んで作る——
  壊れた台帳でも `thth media check` が理由を言えるように。壊れていれば柵は `invalid_media_traits` で断る
  （既定に倒さない）。
- 語彙（信号・単位・鍵・振る舞い・目的・行の id・動作）は `media_traits` が正本で、ここは import する。
"""
from __future__ import annotations

import copy

from . import media_traits as _mt

SNS_MEDIA = ("threads", "x", "bluesky", "mastodon", "facebook")
FAMILIES = ("sns", "hatenablog", "hatenabookmark", "reaction", "fotolife")
PHASES = ("halt", "safety", "assess")
PURPOSES = _mt.PURPOSES
ON_EXCEED = _mt.ON_EXCEED
SCOPES = _mt.SCOPES
KEYS = _mt.KEYS
ACTIONS = _mt.ACTIONS
RULE_IDS = _mt.RULE_IDS
# 数の出所: 口座・主体・ブログ・口座の欄・固定・媒体の台帳だけ（`ledger`）・主体のフォトライフの欄。
PARAM_KINDS = ("account", "principal", "blog", "config", "fixed", "ledger", "fotolife")
RULE_FIELDS = ("id", "family", "media", "phase", "purpose", "signal", "actions", "applies_to", "scope",
               "window", "key", "param", "on_exceed", "refusal", "detail", "trait", "source", "tighter",
               "ledger", "default")
# 全媒体の共通の行（`write.floor`）の `media`。
ANY_MEDIA = "*"

_ENTRY_WRITE = ("entry_create", "entry_update")
_ENTRY_ALL = ("entry_create", "entry_update", "entry_delete")
_BOOKMARK = ("bookmark_add", "bookmark_delete")
# 共通の最低線（`write.floor`）が数える「すべての書き」。反応（いいね・再投稿）は入れない。
_WRITES = ("post", "reply", "retract") + _ENTRY_ALL + _BOOKMARK
# 最低線で**止める**動作。返信は数えるが止めない（3.25.0「返信は burst で止めない」と合わせる・監査 低-1）。
_FLOOR_APPLIES = tuple(a for a in _WRITES if a != "reply")


def _row(id, family, *, signal, actions, applies_to, scope, window, param, on_exceed, refusal,
         phase="assess", purpose="preference", key="op", detail=None, media=None, source="", tighter="lower",
         default=None):
    return {"id": id, "family": tuple(family), "media": tuple(media or ()), "phase": phase, "purpose": purpose,
            "signal": signal, "actions": tuple(actions), "applies_to": applies_to if applies_to == "*" else tuple(applies_to),
            "scope": scope, "window": window, "key": key, "param": param, "on_exceed": on_exceed,
            "refusal": refusal, "detail": detail, "trait": "thth:" + id, "source": source, "tighter": tighter,
            "ledger": {}, "default": copy.deepcopy(default)}


_SNS = ("sns",)
_HATENA = ("hatenablog", "hatenabookmark")
_BLOG = ("hatenablog",)
_BM = ("hatenabookmark",)

# 表の順 = 当てる順（今の `guard.check`／`_check_hatena`／`check_reaction` の順）。
THTH_DEFAULTS = (
    # ---------------------------------------------------------------- SNS（主体を使わない口座）
    _row("post.quiet", _SNS, media=SNS_MEDIA, signal="quiet_and_interval", actions=("post", "reply"),
         applies_to=("post", "reply"), scope="account", window="quiet_hours", param=("config", "quiet_hours"),
         on_exceed="refuse", refusal="quiet_hours", source="THTH の線（目的 c・2.x）", tighter=None),
    _row("post.interval", _SNS, media=SNS_MEDIA, signal="quiet_and_interval", actions=("post",),
         applies_to=("post",), scope="account", window={"hours": None}, param=("config", "min_interval_hours"),
         on_exceed="refuse", refusal="too_soon", source="THTH の線（目的 c・2.x・返信には掛けない）",
         tighter="higher"),
    # 4.3（監査 中-1）: 投稿の日の上限は投稿だけ。返信は `reply.daily`（返信だけ・既定は媒体の台帳の 50）。
    _row("post.daily", _SNS, media=SNS_MEDIA, signal="frequency", actions=("post",),
         applies_to=("post", "schedule"), scope="account", window="day",
         param=("account", "daily_max_posts"), on_exceed="refuse", refusal="daily_limit",
         source="THTH の線（目的 c・3.12.0・4.3 から投稿だけ）"),
    _row("reply.daily", _SNS, media=SNS_MEDIA, signal="frequency", actions=("reply",),
         applies_to=("reply",), scope="account", window="day",
         param=("account", "daily_max_replies"), on_exceed="refuse", refusal="daily_limit", detail="reply_daily",
         source="THTH の推測（目的 c・4.3・返信だけの日の上限）",
         default=50),
    _row("retract.daily", _SNS, media=SNS_MEDIA, signal="frequency", actions=("retract",),
         applies_to=("retract",), scope="account", window="day", param=("account", "daily_max_retracts"),
         on_exceed="refuse", refusal="retract_limit", purpose="preference", source="THTH の線（目的 b＋c・3.12.0）"),
    # 4.3（設計「返信の扱い（4.3）」）: 返信の窓は**返信だけ**を数え（既定は媒体の台帳の 10 分に 10）、投稿の窓は
    # 返信を数えない。timer の道（`core._throw_locked`）も返信をこの行の数と窓で出す。
    _row("reply.burst", _SNS, media=SNS_MEDIA, signal="frequency", actions=("reply",),
         applies_to=("reply",), scope="account", window={"minutes": None}, param=("account", "burst"),
         on_exceed="wait", refusal="burst_limit", detail="burst",
         source="THTH の線（会話は並べる・3.25.0・返信だけを数える 4.3）",
         default={"count": 10, "minutes": 10}),
    _row("post.burst", _SNS, media=SNS_MEDIA, signal="frequency", actions=("post", "retract"),
         applies_to=("post", "retract"), scope="account", window={"minutes": None}, param=("account", "burst"),
         on_exceed="stop", refusal="account_stopped", detail="burst", phase="safety", purpose="safety",
         source="THTH の線（目的 b・3.12.0）",
         default={"count": 3, "minutes": 10}),
    # ---------------------------------------------------------------- はてな（ブログとブックマーク）
    _row("entry.platform_refusal", _HATENA, media=("hatenablog", "hatenabookmark"),
         signal="recent_platform_refusal", actions=(), applies_to="*", scope="account", window="forever",
         param=("fixed", 0), on_exceed="halted", refusal="account_stopped", phase="halt", purpose="platform",
         source="媒体の断りの印（publish_hold・3.19.2）", tighter=None),
    _row("entry.quiet", _BLOG, media=("hatenablog",), signal="quiet_and_interval", actions=_ENTRY_WRITE,
         applies_to=_ENTRY_WRITE, scope="account", window="quiet_hours", param=("config", "quiet_hours"),
         on_exceed="refuse", refusal="quiet_hours", source="THTH の線（目的 c）", tighter=None),
    _row("entry.interval", _BLOG, media=("hatenablog",), signal="quiet_and_interval", actions=_ENTRY_WRITE,
         applies_to=_ENTRY_WRITE, scope="account", window={"hours": None}, param=("config", "min_interval_hours"),
         on_exceed="refuse", refusal="too_soon", source="THTH の線（目的 c）", tighter="higher"),
    _row("entry.daily", _BLOG, media=("hatenablog",), signal="frequency", actions=_ENTRY_WRITE,
         applies_to=_ENTRY_WRITE + ("schedule",), scope="account", window="day",
         param=("account", "daily_max_posts"), on_exceed="refuse", refusal="daily_limit",
         source="THTH の線（目的 c・S1・雛形 3）"),
    _row("entry.blog_daily", _BLOG, media=("hatenablog",), signal="frequency", actions=_ENTRY_WRITE,
         applies_to=_ENTRY_WRITE, scope="blog", window="day", param=("blog", "daily_max_posts"),
         on_exceed="refuse", refusal="blog_daily_limit", source="THTH の線（目的 c・S1・任意）"),
    _row("entry.principal_daily", _BLOG, media=("hatenablog",), signal="frequency", actions=_ENTRY_WRITE,
         applies_to=_ENTRY_WRITE + ("schedule",), scope="principal", window="day",
         param=("principal", "daily_max_posts"), on_exceed="refuse", refusal="principal_daily_limit",
         source="THTH の線（目的 c・S1）",
         default=6),
    _row("entry_delete.daily", _BLOG, media=("hatenablog",), signal="frequency", actions=("entry_delete",),
         applies_to=("entry_delete",), scope="account", window="day", param=("account", "daily_max_retracts"),
         on_exceed="refuse", refusal="retract_limit", source="THTH の線（目的 c・S1・雛形 2）"),
    _row("entry_delete.blog_daily", _BLOG, media=("hatenablog",), signal="frequency", actions=("entry_delete",),
         applies_to=("entry_delete",), scope="blog", window="day", param=("blog", "daily_max_retracts"),
         on_exceed="refuse", refusal="blog_retract_limit", source="THTH の線（目的 c・S1・任意）"),
    _row("entry_delete.principal_daily", _BLOG, media=("hatenablog",), signal="frequency",
         actions=("entry_delete",), applies_to=("entry_delete",), scope="principal", window="day",
         param=("principal", "daily_max_retracts"), on_exceed="refuse", refusal="principal_retract_limit",
         source="THTH の線（目的 c・S1）",
         default=3),
    _row("bookmark.daily", _BM, media=("hatenabookmark",), signal="frequency", actions=_BOOKMARK,
         applies_to=_BOOKMARK, scope="principal", window="day", param=("principal", "daily_max_bookmarks"),
         on_exceed="refuse", refusal="bookmark_limit", source="THTH の線（目的 c・S1・4.0.1 で 30）"),
    _row("bookmark.self_daily", _BM, media=("hatenabookmark",), signal="self_reference",
         actions=("bookmark_add",), applies_to=("bookmark_add",), scope="principal", window="day", key="own",
         param=("principal", "daily_max_self_bookmarks"), on_exceed="refuse", refusal="self_bookmark_limit",
         purpose="platform", source="はてなのスパムの指針の内側（S4 §5.3）"),
    _row("bookmark.same_host_daily", _BM, media=("hatenabookmark",), signal="same_host",
         actions=("bookmark_add",), applies_to=("bookmark_add",), scope="principal", window="day", key="host",
         param=("principal", "bookmark_same_host_daily"), on_exceed="refuse", refusal="bookmark_host_limit",
         purpose="platform", source="同一サイトの大量のブックマーク（S4 §5.3・4.0.1 で 3）"),
    _row("bookmark.burst", _BM, media=("hatenabookmark",), signal="frequency", actions=_BOOKMARK,
         applies_to=_BOOKMARK, scope="principal", window={"minutes": None}, param=("principal", "bookmark_burst"),
         on_exceed="wait", refusal="burst_limit", detail="bookmark_burst",
         source="THTH の線（目的 c・4.0.1・主体は止めない）"),
    _row("entry.burst", _BLOG, media=("hatenablog",), signal="frequency", actions=_ENTRY_ALL,
         applies_to=_ENTRY_ALL, scope="account", window={"minutes": None}, key="target",
         param=("account", "burst"), on_exceed="stop", refusal="account_stopped", detail="burst",
         phase="safety", purpose="safety", source="THTH の線（目的 b・S5 §6 同じ配信は 1 件）",
         default={"count": 3, "minutes": 10}),
    _row("entry.principal_burst", _BLOG, media=("hatenablog",), signal="frequency", actions=_ENTRY_ALL,
         applies_to=_ENTRY_ALL, scope="principal", window={"minutes": None}, key="target",
         param=("principal", "burst"), on_exceed="stop", refusal="principal_stopped", detail="burst",
         phase="safety", purpose="safety", source="THTH の線（目的 b・S1・4.0.1 で記事だけ）",
         default={"count": 3, "minutes": 30}),
    _row("bookmark.once", _BM, media=("hatenabookmark",), signal="same_target", actions=("bookmark_add",),
         applies_to=("bookmark_add",), scope="owner", window="forever", key="url", param=("fixed", 1),
         on_exceed="refuse", refusal="bookmark_once", purpose="platform",
         source="1 URL に 1 回（S4 §4.2 柵 1）", tighter=None),
    # ---------------------------------------------------------------- 反応（いいね・再投稿・取り消し）
    _row("reaction.quiet", ("reaction",), signal="quiet_and_interval", actions=("repost",),
         applies_to=("repost",), scope="account", window="quiet_hours", param=("config", "quiet_hours"),
         on_exceed="refuse", refusal="quiet_hours", source="THTH の線（目的 c・再投稿だけ・3.15.0）", tighter=None),
    _row("reaction.daily", ("reaction",), signal="frequency", actions=("reaction",), applies_to="*",
         scope="account", window="day", param=("account", "daily_max_reactions"), on_exceed="refuse",
         refusal="reaction_limit", source="THTH の線（目的 b＋c・3.15.0）",
         default=50),
    # ---------------------------------------------------------------- 共通の最低線（段 2・§6.3）
    # 表に行の無い媒体・動作も受ける（`media: *`）。各道の**最後**に当てる（first-hit なので、記事・ブックマーク・
    # SNS の行が先に当たればここには届かない）。反応は数えない（3.15.0 の「反応は公開の枠に数えない」のまま）。
    _row("write.floor", ("sns", "hatenablog", "hatenabookmark"), media=(ANY_MEDIA,), signal="frequency",
         actions=_WRITES, applies_to=_FLOOR_APPLIES, scope="account", window={"minutes": 10}, param=("account", None),
         on_exceed="stop", refusal="account_stopped", detail="burst", phase="safety", purpose="safety",
         source="THTH の線（目的 b・段 2・行を書き忘れた媒体・動作の暴走を止める）",
         default={"count": 20, "minutes": 10}),
)

# ---------------------------------------------------------------------------
# 台帳（`thth/media_traits/`）との写像（段 2・監査 中-1）——**この 1 か所だけ**
# ---------------------------------------------------------------------------

# 媒体の性質の台帳（`thth/media_traits/`）を持つ SNS。facebook は 10/29 段 0 で台帳
# （`facebook.json`・post.daily・retract.daily・reply.burst・reply.daily）を持った。
LEDGER_SNS_MEDIA = ("threads", "x", "bluesky", "mastodon", "facebook")

# (媒体, 台帳の行の id) → 表の行の id。SNS の 4 媒体の行は表の 1 行（`family: sns`）に集まり、根拠は媒体ごと。
LEDGER_MAP = {
    **{(media, "post.daily"): "post.daily" for media in LEDGER_SNS_MEDIA},
    **{(media, "retract.daily"): "retract.daily" for media in LEDGER_SNS_MEDIA},
    # 4.3: 返信の窓の既定（10 分に 10・数の根拠は THTH の線）。根拠の項目は媒体の API の上限。
    **{(media, "reply.burst"): "reply.burst" for media in LEDGER_SNS_MEDIA},
    # 4.3（監査 中-1）: 返信の日の上限の既定（50・数は推測）。
    **{(media, "reply.daily"): "reply.daily" for media in LEDGER_SNS_MEDIA},
    # Threads の反応の行は台帳に根拠の項目が無い（段 0 の決め 8）→ その媒体は `thth:reaction.daily`。
    **{(media, "reaction.daily"): "reaction.daily" for media in ("x", "bluesky", "mastodon")},
    ("hatenablog", "entry.daily"): "entry.daily",
    ("hatenablog", "entry_delete.daily"): "entry_delete.daily",
    ("fotolife", "upload.daily"): "upload.daily",
    ("hatenabookmark", "bookmark.once"): "bookmark.once",
    ("hatenabookmark", "bookmark.self_daily"): "bookmark.self_daily",
    ("hatenabookmark", "bookmark.same_host_daily"): "bookmark.same_host_daily",
    ("hatenabookmark", "bookmark.burst"): "bookmark.burst",
    ("hatenabookmark", "bookmark.daily"): "bookmark.daily",
    ("hatenabookmark", "bookmark.stop"): "bookmark.stop",
}
# 台帳にあるが表の行にしないもの（理由つき）。ここにも写像にも無い台帳の行は `build_policy` が断る。
LEDGER_NOT_ROWS = {
    ("hatenabookmark", "bookmark.retry_once"):
        "not_found のあとの打ち直しは `hatena_bookmark` の `attempts[]` の道（guard の外・今のまま）",
    ("x", "post.platform_refusal"):
        "X の 403 で置く `publish_hold` の道（`server_writes`・今のまま）。はてなは `entry.platform_refusal`",
    ("x", "reply.counterpart"): "段 4（guard は相手との関わりを渡さない・1 段目の予告）",
}
# 台帳の既定から生まれる行（信号・動作・単位・窓・数・振る舞い・符丁は台帳。道・順・数の出所はここ）。
# `before`: 表のこの行の前に置く（None なら最後）。
LEDGER_BORN = {
    # ブックマークの止め線（主体 10 分に 30）。**ブックマークの行の最初**に当てる——`bookmark.burst`（10 で
    # wait）・`bookmark.daily`（30 で refuse）より後ろだと届かない（段 2 で決めたこと）。
    "bookmark.stop": {"family": ("hatenabookmark",), "phase": "safety", "param": ("ledger", None),
                      "before": "bookmark.daily", "source": "はてなの「機械的な高頻度」の内側（数は推測）"},
    # フォトライフの 1 日の上げ。guard は当てない（`hatena_fotolife.ensure_uploaded` が数を引くだけ）。
    "upload.daily": {"family": ("fotolife",), "phase": "assess", "param": ("fotolife", "daily_max_uploads"),
                     "before": None, "refusal": "fotolife_daily_limit",
                     "source": "THTH の線（目的 c・S3 §9・数えるのは画像の台帳）"},
}
_SHAPE_FIELDS = ("signal", "scope", "key", "on_exceed", "purpose")


def _window_kind(window):
    if window in ("day", {"day": "jst"}):
        return "day"
    if window in ("forever", {"forever": True}):
        return "forever"
    if isinstance(window, dict) and "minutes" in window:
        return "minutes"
    return repr(window)


def _ledger_window(window):
    """台帳の窓（`{"day": "jst"}` など）を表の窓（`"day"` など）に。"""
    kind = _window_kind(window)
    if kind == "minutes":
        return {"minutes": window["minutes"]}
    return kind if kind in ("day", "forever") else copy.deepcopy(window)


def _disagreement(row, item) -> str | None:
    """表の行と台帳の行の形の食い違い（無ければ None）。"""
    for name in _SHAPE_FIELDS:
        if row[name] != item.get(name):
            return f"{name}: 表 {row[name]!r}・台帳 {item.get(name)!r}"
    if set(row["actions"]) != set(item.get("actions") or ()):
        return f"actions: 表 {row['actions']!r}・台帳 {item.get('actions')!r}"
    if "applies_to" in item and set(row["applies_to"]) != set(item["applies_to"]):
        return f"applies_to: 表 {row['applies_to']!r}・台帳 {item['applies_to']!r}"
    for name in ("refusal", "detail"):
        if name in item and item[name] != row[name]:
            return f"{name}: 表 {row[name]!r}・台帳 {item[name]!r}"
    if _window_kind(row["window"]) != _window_kind(item.get("window")):
        return f"window: 表 {row['window']!r}・台帳 {item.get('window')!r}"
    return None


def _ledger_info(item) -> dict:
    return {"trait": item["trait"], "confidence": item.get("confidence"),
            "evidence_type": item.get("evidence_type"), "count_evidence": item.get("count_evidence"),
            "count_confidence": item.get("count_confidence"), "count": item.get("count"),
            "window": _ledger_window(item.get("window"))}


def _born(rule_id, item) -> dict:
    spec = LEDGER_BORN[rule_id]
    actions = tuple(item["actions"])
    applies = tuple(item.get("applies_to") or actions)
    return {"id": rule_id, "family": tuple(spec["family"]), "media": (item["media"],), "phase": spec["phase"],
            "purpose": item["purpose"], "signal": item["signal"], "actions": actions, "applies_to": applies,
            "scope": item["scope"], "window": _ledger_window(item["window"]), "key": item["key"],
            "param": spec["param"], "on_exceed": item["on_exceed"],
            "refusal": item.get("refusal") or spec.get("refusal"), "detail": item.get("detail"),
            "trait": item["trait"], "source": spec["source"], "tighter": item.get("tighter"), "ledger": {},
            "default": None}


def _ledger_rows() -> list:
    """段 0 の台帳の `defaults`（`media_traits.collect_defaults()`）。検査に落ちれば `invalid_media_traits`。"""
    return _mt.collect_defaults()


def build_policy(ledger=()) -> tuple:
    """`THTH_DEFAULTS` に台帳の行を結んだ表（順は `THTH_DEFAULTS`・`LEDGER_BORN` の `before`）。

    台帳の行（`collect_defaults()` の形）は `LEDGER_MAP` を通ったものだけ受ける:
    - 写像の先が THTH の行: 形が一致しなければ ValueError（`ledger_shape_mismatch`）。一致すれば `ledger`
      （媒体 → 根拠・確かさ・数の根拠・既定の数と窓）に足す。行の `trait` は最初に結んだ媒体の項目。
    - 写像の先が `LEDGER_BORN`: 台帳から行を作る。
    - `LEDGER_NOT_ROWS` にあるものは読み飛ばす。どちらにも無ければ ValueError（`unmapped_ledger_row`）。
    """
    rows = [copy.deepcopy(row) for row in THTH_DEFAULTS]
    by_id = {row["id"]: row for row in rows}
    born = {}
    for item in ledger or ():
        if not isinstance(item, dict) or not (isinstance(item.get("trait"), str) and item["trait"]):
            raise ValueError("invalid_ledger_row")
        where = (item.get("media"), item.get("id"))
        if where in LEDGER_NOT_ROWS:
            continue
        if where not in LEDGER_MAP:
            raise ValueError(f"unmapped_ledger_row:{where[0]}:{where[1]}")
        rule_id = LEDGER_MAP[where]
        if rule_id in LEDGER_BORN:
            if rule_id in born:
                raise ValueError(f"duplicate_ledger_row:{rule_id}")
            born[rule_id] = _born(rule_id, item)
            born[rule_id]["ledger"][item["media"]] = _ledger_info(item)
            continue
        row = by_id[rule_id]
        problem = _disagreement(row, item)
        if problem:
            raise ValueError(f"ledger_shape_mismatch:{where[0]}:{rule_id}: {problem}")
        if not row["ledger"]:
            row["trait"] = item["trait"]
        row["ledger"][item["media"]] = _ledger_info(item)
    for rule_id, row in born.items():
        anchor = LEDGER_BORN[rule_id]["before"]
        index = next((i for i, r in enumerate(rows) if r["id"] == anchor), len(rows)) if anchor else len(rows)
        rows.insert(index, row)
    return tuple(rows)


_POLICY = None


def policy() -> tuple:
    """表（最初に要ったときに台帳を読んで作る・以後は使い回す）。"""
    global _POLICY
    if _POLICY is None:
        _POLICY = build_policy(_ledger_rows())
    return _POLICY


def reset() -> None:
    """作った表を捨てる（試験で台帳の置き場を差し替えたあと）。"""
    global _POLICY
    _POLICY = None


def __getattr__(name):
    # `limits_policy.POLICY` は読んだときに作る（import のときに台帳を読まない・段 2）。
    if name == "POLICY":
        return policy()
    raise AttributeError(name)


def rule(rule_id, policy=None) -> dict:
    for row in policy or globals()["policy"]():
        if row["id"] == rule_id:
            return row
    raise KeyError(rule_id)


def ids(policy=None) -> tuple:
    return tuple(row["id"] for row in policy or globals()["policy"]())


def _media_of(row, media):
    """この依頼の媒体での台帳の結び（無ければ None）。SNS は口座の媒体・はてなは行の媒体。"""
    ledger = row.get("ledger") or {}
    if media in ledger:
        return ledger[media]
    if len(ledger) == 1 and len(row["media"]) == 1:
        return next(iter(ledger.values()))
    return None


def _uniform_ledger(row):
    """媒体の台帳の既定の数が媒体をまたいで同じなら、その結び（媒体を言わない口座の既定・段 3）。"""
    infos = [info for info in (row.get("ledger") or {}).values() if info.get("count") is not None]
    if infos and all((i["count"], i["window"]) == (infos[0]["count"], infos[0]["window"]) for i in infos):
        return infos[0]
    return None


def _value_parts(value, window):
    """台帳の値（数か `{"count","minutes"}`）を (数, 窓) に。"""
    if isinstance(value, dict):
        return value["count"], {"minutes": value["minutes"]}
    return value, window


def default_value(rule_id, *, media=None, policy=None):
    """行の既定の値（数か `{"count","minutes"}`）: 媒体の台帳の既定 → 表の `default`（段 3・`GUARD_DEFAULTS`
    などはここから引く）。どちらにも無ければ None。"""
    row = rule(rule_id, policy)
    info = _media_of(row, media) if media is not None else None
    if info is None or info.get("count") is None:
        info = _uniform_ledger(row)
    if info is not None and info.get("count") is not None:
        window = info.get("window")
        if isinstance(window, dict) and "minutes" in window:
            return {"count": info["count"], "minutes": window["minutes"]}
        return info["count"]
    return copy.deepcopy(row.get("default"))


def resolve(row, *, cfg, principal_cfg=None, blog=None, media=None) -> dict:
    """行に口座・主体・ブログの台帳の数を当てた写し（`count`・`window`・`origin`・`via`・根拠）。

    `count` が None の行は「数えない」（ブログの欄が無い・夜間や最短間隔が台帳に無い）。
    数の出所（段 3・§8）: 口座・主体の台帳の `limits`（行の id）→ 今の鍵（別名・`limits_config`）→ 媒体の台帳の
    既定 → 表の `default`。`origin` は `account`／`principal`（明示）か `default`、`via` は `limits`／
    `alias:<鍵>`／None。最低線（`write.floor`）を持ち主が上げていれば `floor_raised` と `floor_reason`。
    根拠（`trait`・`confidence`・`count_evidence`・`count_confidence`）は媒体ごとの台帳の結びから。
    """
    from . import limits_config
    out = copy.deepcopy(row)
    kind, name = row["param"]
    origin = "default"
    via = None
    count = None
    window = copy.deepcopy(row["window"])
    if media is None:
        media = (cfg or {}).get("media")
    info = _media_of(row, media)
    holder = limits_config.holder_of(row["id"])
    if holder is not None:
        given = limits_config.explicit(holder, cfg if holder == "account" else principal_cfg, row["id"])
        if given is not None:
            count, window = _value_parts(given[0], window)
            origin, via = holder, given[1]
        else:
            base = info if info is not None and info.get("count") is not None else _uniform_ledger(row)
            if base is not None:
                count = base["count"]
                if isinstance(base["window"], dict) and "minutes" in base["window"]:
                    window = dict(base["window"])
            elif row.get("default") is not None:
                count, window = _value_parts(row["default"], window)
    elif kind == "blog":
        blog = blog or {}
        if name in blog:
            count, origin = blog[name], "principal"
    elif kind == "config":
        value = (cfg or {}).get(name)
        if name == "min_interval_hours":
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                count, window, origin = 1, {"hours": value}, "account"
        elif value:
            count, window, origin = 0, {"quiet_hours": list(value)}, "account"
    elif kind == "fixed":
        count = name
    out.update({"count": count, "window": window, "origin": origin, "via": via,
                "trait": info["trait"] if info else "thth:" + row["id"],
                "confidence": info["confidence"] if info else None,
                "count_evidence": info["count_evidence"] if info else "thth_line",
                "count_confidence": info["count_confidence"] if info else None})
    if row["id"] == limits_config.FLOOR_ID:
        reason = limits_config.floor_reason(cfg) if via == "limits" else None
        out.update({"floor_raised": reason is not None, "floor_reason": reason})
    return out


def _applies_to_media(row, family, media) -> bool:
    """SNS・反応の道は口座の媒体で行を絞る（行の媒体に無い媒体には、共通の行 `*` だけ）。

    媒体を言わない口座（古い台帳・試験の口座）は今までどおりすべての行。はてなの道は行の媒体で分けない。
    """
    if family not in ("sns", "reaction") or not (isinstance(media, str) and media):
        return True
    return ANY_MEDIA in row["media"] or not row["media"] or media in row["media"]


def rules_for(family, action, *, cfg, principal_cfg=None, blog=None, policy=None, media=None) -> list:
    """`family` の道で `action` に当てる行（表の順・台帳の数を当てた写し）。"""
    if family not in FAMILIES:
        raise ValueError("invalid_family")
    if media is None:
        media = (cfg or {}).get("media")
    out = []
    for row in policy or globals()["policy"]():
        if family not in row["family"]:
            continue
        if row["applies_to"] != "*" and action not in row["applies_to"]:
            continue
        if not _applies_to_media(row, family, media):
            continue
        out.append(resolve(row, cfg=cfg, principal_cfg=principal_cfg, blog=blog, media=media))
    return out


def resolved(rule_id, *, cfg=None, principal_cfg=None, blog=None, media=None, policy=None) -> dict:
    """行 1 つを台帳の数で解いた写し（guard の外で数を引く口・`hatena_fotolife` の `upload.daily`）。"""
    return resolve(rule(rule_id, policy), cfg=cfg or {}, principal_cfg=principal_cfg, blog=blog, media=media)


def check_table(policy=None) -> list:
    """表の形の検査（試験と点検用）。問題の list（空なら通る）。"""
    from . import assess
    problems = []
    seen = set()
    for row in policy or globals()["policy"]():
        rid = row.get("id")
        if set(row) != set(RULE_FIELDS):
            problems.append(f"{rid}: fields")
        if rid in seen:
            problems.append(f"{rid}: duplicate")
        seen.add(rid)
        if rid not in RULE_IDS:
            problems.append(f"{rid}: id（media_traits.RULE_IDS に無い）")
        if not set(row["family"]) <= set(FAMILIES) or not row["family"]:
            problems.append(f"{rid}: family")
        if row["phase"] not in PHASES:
            problems.append(f"{rid}: phase")
        if row["purpose"] not in PURPOSES:
            problems.append(f"{rid}: purpose")
        if row["signal"] not in assess.SIGNALS:
            problems.append(f"{rid}: signal")
        if row["on_exceed"] not in ON_EXCEED:
            problems.append(f"{rid}: on_exceed")
        if row["scope"] not in SCOPES:
            problems.append(f"{rid}: scope")
        if row["key"] not in KEYS:
            problems.append(f"{rid}: key")
        if not set(row["actions"]) <= set(ACTIONS):
            problems.append(f"{rid}: actions")
        if row["param"][0] not in PARAM_KINDS:
            problems.append(f"{rid}: param")
        if (row["on_exceed"] == "stop") != (row["phase"] == "safety"):
            problems.append(f"{rid}: stop は安全装置だけ")
        if not (isinstance(row["refusal"], str) and row["refusal"]):
            problems.append(f"{rid}: refusal")
        if not (isinstance(row["trait"], str) and row["trait"]):
            problems.append(f"{rid}: trait")
    return problems
