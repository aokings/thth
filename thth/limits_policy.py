"""柵の行の表（設計 `docs/設計_上限と連投の柵_2026-10-17.md` §6・段 1）。

**段 1 は今の数と振る舞いをそのまま写す**（4.0.1 の時点: ブックマークの専用の窓・記事の burst・SNS の
返信の順番待ち・はてなの同じ配信は 1 件、など）。行の数は今までどおり口座の台帳（`accounts.guard_limits`）・
主体の台帳（`principals.limits`）・ブログの欄から引く。この表が持つのは「どの動作に・どの順で・どの信号で・
どの単位と窓で・超えたらどう振る舞うか・断りの符丁」で、`thth/guard.py` はこの表を順に当てるだけになる。

行の形（§6.2）:

- `id`: `<動作>.<欄>`（`post.burst`・`bookmark.daily` など）
- `family`: 当てる道（`sns`・`hatenablog`・`hatenabookmark`・`reaction`）。SNS の道は「主体を使わない口座の
  すべて」で、`media` は読み手のための説明（今の `guard.check` は媒体を問わず同じ数を当てる）
- `phase`: `halt`（止めの印に並ぶ・媒体の断りの印）・`safety`（安全装置・超えたら止める）・`assess`（見立て）
- `purpose`: `platform`（a 媒体の事実）・`safety`（b）・`preference`（c 運用の好み）
- `signal`: `assess.SIGNALS` の 1 つ
- `actions`: 数える動作・`applies_to`: この行を当てる動作（`*` はすべて）
- `scope`: `account`・`blog`・`principal`・`owner`
- `window`: `day`（JST の日）・`{"minutes": n}`・`{"hours": h}`・`quiet_hours`・`forever`
- `key`: 窓の中で 1 件と数える単位（`op`・`target`・`url`・`host`・`own`）
- `param`: 数の出所（`("account", 欄)`・`("principal", 欄)`・`("blog", 欄)`・`("config", 欄)`・`("fixed", 数)`）
- `on_exceed`: `stop`・`refuse`・`wait`・`advise`・`hold`・`halted`（印がすでに置かれている＝止めとして断る）
- `refusal`／`detail`: 今の符丁と詳細（増やさない・§7.1）
- `trait`: 根拠の項目。台帳（段 0）の行は台帳の id、THTH の行は `thth:<id>`
- `source`: 出所の短い文

**当てる順は表の順**（今の `guard.check` の `if` の順そのもの）。最初に当たった行が結論になる（§5.3 の
「強い方」でなく今の first-hit・設計の末尾「段 1 で決めたこと」）。

台帳（段 0 の `thth/media_traits/`）の口は `_ledger_rows()` と `build_policy(ledger)`。段 1 では台帳の
`defaults` は**根拠（`trait`）を結ぶだけ**で、数は変えない（数を台帳から引くのは段 2・段 3）。
"""
from __future__ import annotations

import copy

from . import accounts

SNS_MEDIA = ("threads", "x", "bluesky", "mastodon")
FAMILIES = ("sns", "hatenablog", "hatenabookmark", "reaction")
PHASES = ("halt", "safety", "assess")
PURPOSES = ("platform", "safety", "preference")
ON_EXCEED = ("stop", "refuse", "wait", "advise", "hold", "halted")
SCOPES = ("account", "blog", "principal", "owner")
KEYS = ("op", "target", "url", "host", "own")
PARAM_KINDS = ("account", "principal", "blog", "config", "fixed")
RULE_FIELDS = ("id", "family", "media", "phase", "purpose", "signal", "actions", "applies_to", "scope",
               "window", "key", "param", "on_exceed", "refusal", "detail", "trait", "source", "tighter")

_ENTRY_WRITE = ("entry_create", "entry_update")
_ENTRY_ALL = ("entry_create", "entry_update", "entry_delete")
_BOOKMARK = ("bookmark_add", "bookmark_delete")


def _row(id, family, *, signal, actions, applies_to, scope, window, param, on_exceed, refusal,
         phase="assess", purpose="preference", key="op", detail=None, media=None, source="", tighter="lower"):
    return {"id": id, "family": tuple(family), "media": tuple(media or ()), "phase": phase, "purpose": purpose,
            "signal": signal, "actions": tuple(actions), "applies_to": applies_to if applies_to == "*" else tuple(applies_to),
            "scope": scope, "window": window, "key": key, "param": param, "on_exceed": on_exceed,
            "refusal": refusal, "detail": detail, "trait": "thth:" + id, "source": source, "tighter": tighter}


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
    _row("post.daily", _SNS, media=SNS_MEDIA, signal="frequency", actions=("post", "reply"),
         applies_to=("post", "reply", "schedule"), scope="account", window="day",
         param=("account", "daily_max_posts"), on_exceed="refuse", refusal="daily_limit",
         source="THTH の線（目的 c・3.12.0）"),
    _row("retract.daily", _SNS, media=SNS_MEDIA, signal="frequency", actions=("retract",),
         applies_to=("retract",), scope="account", window="day", param=("account", "daily_max_retracts"),
         on_exceed="refuse", refusal="retract_limit", purpose="preference", source="THTH の線（目的 b＋c・3.12.0）"),
    _row("reply.burst", _SNS, media=SNS_MEDIA, signal="frequency", actions=("post", "reply", "retract"),
         applies_to=("reply",), scope="account", window={"minutes": None}, param=("account", "burst"),
         on_exceed="wait", refusal="burst_limit", detail="burst",
         source="THTH の線（会話は並べる・3.25.0）"),
    _row("post.burst", _SNS, media=SNS_MEDIA, signal="frequency", actions=("post", "reply", "retract"),
         applies_to=("post", "retract"), scope="account", window={"minutes": None}, param=("account", "burst"),
         on_exceed="stop", refusal="account_stopped", detail="burst", phase="safety", purpose="safety",
         source="THTH の線（目的 b・3.12.0）"),
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
         source="THTH の線（目的 c・S1）"),
    _row("entry_delete.daily", _BLOG, media=("hatenablog",), signal="frequency", actions=("entry_delete",),
         applies_to=("entry_delete",), scope="account", window="day", param=("account", "daily_max_retracts"),
         on_exceed="refuse", refusal="retract_limit", source="THTH の線（目的 c・S1・雛形 2）"),
    _row("entry_delete.blog_daily", _BLOG, media=("hatenablog",), signal="frequency", actions=("entry_delete",),
         applies_to=("entry_delete",), scope="blog", window="day", param=("blog", "daily_max_retracts"),
         on_exceed="refuse", refusal="blog_retract_limit", source="THTH の線（目的 c・S1・任意）"),
    _row("entry_delete.principal_daily", _BLOG, media=("hatenablog",), signal="frequency",
         actions=("entry_delete",), applies_to=("entry_delete",), scope="principal", window="day",
         param=("principal", "daily_max_retracts"), on_exceed="refuse", refusal="principal_retract_limit",
         source="THTH の線（目的 c・S1）"),
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
         phase="safety", purpose="safety", source="THTH の線（目的 b・S5 §6 同じ配信は 1 件）"),
    _row("entry.principal_burst", _BLOG, media=("hatenablog",), signal="frequency", actions=_ENTRY_ALL,
         applies_to=_ENTRY_ALL, scope="principal", window={"minutes": None}, key="target",
         param=("principal", "burst"), on_exceed="stop", refusal="principal_stopped", detail="burst",
         phase="safety", purpose="safety", source="THTH の線（目的 b・S1・4.0.1 で記事だけ）"),
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
         refusal="reaction_limit", source="THTH の線（目的 b＋c・3.15.0）"),
)


def _ledger_rows() -> list:
    """段 0 の台帳（`thth/media_traits/`）から行を足す口。

    返す形: `[{"rule": "<行の id>", "trait": "<台帳の項目の id>"}]`。段 0 が入ったら、ここを台帳の
    `defaults` の読みに差し替える（設計 §6.1）。段 1 では台帳を読まない（並行して作っているので形が
    決まるまで空）。
    """
    return []


def build_policy(ledger=()) -> tuple:
    """`THTH_DEFAULTS` に台帳の行を重ねた表（順は `THTH_DEFAULTS` のまま）。

    台帳の行は同じ `id` の行の `trait` を台帳の項目に差し替える（根拠を結ぶ）。段 1 では数・振る舞い・
    当てる順は変えない（`count` などを持ってきても読まない）。知らない `id` の行は ValueError
    （書き間違いで黙って効かない行を作らない）。
    """
    rows = [copy.deepcopy(row) for row in THTH_DEFAULTS]
    by_id = {row["id"]: row for row in rows}
    for item in ledger or ():
        rid = item.get("rule") if isinstance(item, dict) else None
        trait = item.get("trait") if isinstance(item, dict) else None
        if rid not in by_id or not (isinstance(trait, str) and trait):
            raise ValueError("invalid_ledger_row")
        by_id[rid]["trait"] = trait
    return tuple(rows)


POLICY = build_policy(_ledger_rows())


def rule(rule_id, policy=None) -> dict:
    for row in policy or POLICY:
        if row["id"] == rule_id:
            return row
    raise KeyError(rule_id)


def ids(policy=None) -> tuple:
    return tuple(row["id"] for row in policy or POLICY)


def _explicit(cfg, key, valid) -> bool:
    value = (cfg or {}).get(key)
    return value is not None and valid(key, value)


def resolve(row, *, cfg, principal_cfg=None, blog=None) -> dict:
    """行に口座・主体・ブログの台帳の数を当てた写し（`count`・`window`・`origin`）。

    `count` が None の行は「数えない」（ブログの欄が無い・夜間や最短間隔が台帳に無い）。
    """
    out = copy.deepcopy(row)
    kind, name = row["param"]
    origin = "default"
    count = None
    window = copy.deepcopy(row["window"])
    if kind == "account":
        value = accounts.guard_limits(cfg)[name]
        if _explicit(cfg, name, accounts.valid_guard_value):
            origin = "account"
        if isinstance(value, dict):
            count, window = value["count"], {"minutes": value["minutes"]}
        else:
            count = value
    elif kind == "principal":
        from . import principals
        value = principals.limits(principal_cfg)[name]
        if _explicit(principal_cfg, name, principals.valid_limit):
            origin = "principal"
        if isinstance(value, dict):
            count, window = value["count"], {"minutes": value["minutes"]}
        else:
            count = value
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
    out.update({"count": count, "window": window, "origin": origin})
    return out


def rules_for(family, action, *, cfg, principal_cfg=None, blog=None, policy=None) -> list:
    """`family` の道で `action` に当てる行（表の順・台帳の数を当てた写し）。"""
    if family not in FAMILIES:
        raise ValueError("invalid_family")
    out = []
    for row in policy or POLICY:
        if family not in row["family"]:
            continue
        if row["applies_to"] != "*" and action not in row["applies_to"]:
            continue
        out.append(resolve(row, cfg=cfg, principal_cfg=principal_cfg, blog=blog))
    return out


def check_table(policy=None) -> list:
    """表の形の検査（試験と `import` のあとの点検用）。問題の list（空なら通る）。"""
    from . import assess
    problems = []
    seen = set()
    for row in policy or POLICY:
        rid = row.get("id")
        if set(row) != set(RULE_FIELDS):
            problems.append(f"{rid}: fields")
        if rid in seen:
            problems.append(f"{rid}: duplicate")
        seen.add(rid)
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
        if row["param"][0] not in PARAM_KINDS:
            problems.append(f"{rid}: param")
        if (row["on_exceed"] == "stop") != (row["phase"] == "safety"):
            problems.append(f"{rid}: stop は安全装置だけ")
        if not (isinstance(row["refusal"], str) and row["refusal"]):
            problems.append(f"{rid}: refusal")
        if not (isinstance(row["trait"], str) and row["trait"]):
            problems.append(f"{rid}: trait")
    return problems
