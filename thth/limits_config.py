"""台帳の `limits`——柵の行を口座・主体の台帳で上書きする面（設計 上限と連投の柵 §8・§14・段 3）。

口座（`accounts/<name>.json`）と主体（`principals/<p>.json`）に `limits` を足せる。鍵は柵の表の行の id
（`post.burst`・`bookmark.daily` など）、値は数（日の行）か `{"count", "minutes"}`（分の窓の行）:

    "limits": {"post.daily": 12, "post.burst": {"count": 5, "minutes": 10},
               "bookmark.burst": {"count": 20, "minutes": 10}}

**今の鍵は別名として読み続ける**（足して残す・撤去は段 5）。`daily_max_posts` = `post.daily`（はてなの口座は
`entry.daily`）など、表 `ACCOUNT_ROWS`／`PRINCIPAL_ROWS` の右の欄。両方あれば `limits` が勝つ。VM の今の
台帳（`hatena-kopicha` の `daily_max_bookmarks: 50`・`bookmark_same_host_daily: 5` など）は 1 つも書き換えずに
同じ数で効く。

決めたこと（設計の末尾「段 3 で決めたこと」）:

- 知らない id・単位の違う行（口座に主体の行・主体に口座の行）・口座の媒体に当たらない行・形の違う値は
  **既定に倒さず断る**（`invalid_guard_limits`・台帳の読み込みで）。
- `null` は受けない（設計 §8.1 の「行を外す」は採らない。数えない行を作る口は今の `count: 0` だけ）。
- 共通の最低線 `write.floor`（口座 10 分に 20 で止める）は**持ち主が明示すれば上げられる**（masaru 10/18）。
  上げるときは値に `reason`（理由・1〜200 字）が要り、上げられるのは **10 分あたり 100 件**まで
  （`count` ≤ 100 かつ `count` ≤ 10 × `minutes`）。下げる（締める）向きは理由が要らない。

この模組は**柵の表（`limits_policy`）も媒体の台帳も読まない**——口座・主体の台帳の読み込み（`load_account`・
`principals.validate`）から呼ばれ、媒体の台帳が壊れていても口座の台帳は読める。行の形（単位・窓の種類）は
ここに静的に持ち、表と食い違わないことを試験が縛る。
"""
from __future__ import annotations

SNS_MEDIA = ("threads", "x", "bluesky", "mastodon")
INT_MAX = 1000
COUNT_MAX = 1000
MINUTES_MAX = 1440
# 共通の最低線の既定と、持ち主が上げられる上限（10 分あたり 100 件）。
FLOOR_ID = "write.floor"
FLOOR_DEFAULT = {"count": 20, "minutes": 10}
FLOOR_MAX_COUNT = 100
FLOOR_MAX_PER_MINUTE = 10
REASON_MAX = 200

# 行の id → (窓の種類, 別名の鍵, 当たる媒体)。窓の種類: `day`（整数）・`minutes`（`{"count","minutes"}`）・
# `uploads`（フォトライフの 1 日・`hatena_fotolife.LIMIT_RANGES` の範囲）・`floor`（最低線）。
# 別名の鍵が None の行は `limits` でしか書けない。媒体 None はすべての媒体。
ACCOUNT_ROWS = {
    "post.daily": ("day", "daily_max_posts", SNS_MEDIA),
    "retract.daily": ("day", "daily_max_retracts", SNS_MEDIA),
    "reply.burst": ("minutes", "burst", SNS_MEDIA),
    # 4.3（監査 中-1）: 返信だけの日の上限。今の鍵（別名）は無い（`limits` でだけ書ける）。
    "reply.daily": ("day", None, SNS_MEDIA),
    "post.burst": ("minutes", "burst", SNS_MEDIA),
    "reaction.daily": ("day", "daily_max_reactions", SNS_MEDIA),
    "entry.daily": ("day", "daily_max_posts", ("hatenablog",)),
    "entry_delete.daily": ("day", "daily_max_retracts", ("hatenablog",)),
    "entry.burst": ("minutes", "burst", ("hatenablog",)),
    FLOOR_ID: ("floor", None, None),
}
PRINCIPAL_ROWS = {
    "entry.principal_daily": ("day", "daily_max_posts", None),
    "entry_delete.principal_daily": ("day", "daily_max_retracts", None),
    "entry.principal_burst": ("minutes", "burst", None),
    "bookmark.daily": ("day", "daily_max_bookmarks", None),
    "bookmark.self_daily": ("day", "daily_max_self_bookmarks", None),
    "bookmark.same_host_daily": ("day", "bookmark_same_host_daily", None),
    "bookmark.burst": ("minutes", "bookmark_burst", None),
    "bookmark.stop": ("minutes", None, None),
    "upload.daily": ("uploads", "fotolife.daily_max_uploads", None),
}
HOLDERS = {"account": ACCOUNT_ROWS, "principal": PRINCIPAL_ROWS}


class LimitsError(ValueError):
    """`limits` の断り。`code` は静的な符丁（`invalid_guard_limits`）、`key` は行の id（値は入れない）。"""

    def __init__(self, code, key=None):
        super().__init__(code)
        self.code = code
        self.key = key


def holder_of(rule_id) -> str | None:
    """行を書ける台帳（`account`／`principal`）。書けない行は None。"""
    for holder, rows in HOLDERS.items():
        if rule_id in rows:
            return holder
    return None


def kind_of(rule_id) -> str | None:
    holder = holder_of(rule_id)
    return HOLDERS[holder][rule_id][0] if holder else None


def alias_of(rule_id) -> str | None:
    holder = holder_of(rule_id)
    return HOLDERS[holder][rule_id][1] if holder else None


def rows_for_alias(holder, key, media=None) -> tuple:
    """別名の鍵が指す行（口座は媒体で選ぶ・媒体を言わない口座はすべて）。"""
    out = []
    for rule_id, (_kind, alias, fits) in HOLDERS[holder].items():
        if alias != key:
            continue
        if holder == "account" and not fits_media(rule_id, media):
            continue
        out.append(rule_id)
    return tuple(out)


def fits_media(rule_id, media) -> bool:
    """口座の行がその口座の媒体に当たるか（媒体を言わない口座はすべて当たる）。"""
    entry = ACCOUNT_ROWS.get(rule_id)
    if entry is None:
        return False
    fits = entry[2]
    return fits is None or not (isinstance(media, str) and media) or media in fits


def _plain_int(value) -> bool:
    return type(value) is int


def valid_reason(value) -> bool:
    return (isinstance(value, str) and 0 < len(value.strip()) <= REASON_MAX
            and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value))


def floor_raised(value) -> bool:
    """最低線の値が既定（10 分に 20）より緩いか（1 窓の件数が多い・または 1 分あたりが多い）。"""
    count, minutes = value["count"], value["minutes"]
    return (count > FLOOR_DEFAULT["count"]
            or count * FLOOR_DEFAULT["minutes"] > FLOOR_DEFAULT["count"] * minutes)


def floor_within_max(value) -> bool:
    count, minutes = value["count"], value["minutes"]
    return count <= FLOOR_MAX_COUNT and count <= FLOOR_MAX_PER_MINUTE * minutes


def problem(rule_id, value) -> str | None:
    """値 1 つの形の問題（無ければ None）。符丁だけ（値は入れない）。"""
    kind = kind_of(rule_id)
    if kind is None:
        return "unknown_rule"
    if kind == "day":
        return None if _plain_int(value) and 0 <= value <= INT_MAX else "invalid_value"
    if kind == "uploads":
        from . import hatena_fotolife
        return None if hatena_fotolife.valid_limit("daily_max_uploads", value) else "invalid_value"
    if type(value) is not dict:
        return "invalid_value"
    keys = set(value)
    allowed = {"count", "minutes", "reason"} if kind == "floor" else {"count", "minutes"}
    if not {"count", "minutes"} <= keys or keys - allowed:
        return "invalid_value"
    if not (_plain_int(value["count"]) and 1 <= value["count"] <= COUNT_MAX
            and _plain_int(value["minutes"]) and 1 <= value["minutes"] <= MINUTES_MAX):
        return "invalid_value"
    if kind == "floor":
        if "reason" in value and not valid_reason(value["reason"]):
            return "invalid_reason"
        if floor_raised(value):
            if not floor_within_max(value):
                return "floor_above_max"
            if "reason" not in value:
                return "floor_reason_required"
    return None


def check(holder, data, *, media=None) -> None:
    """台帳の `limits` を見る（無ければ何もしない）。外れれば `LimitsError("invalid_guard_limits", 行の id)`。"""
    if "limits" not in (data or {}):
        return
    block = data["limits"]
    if type(block) is not dict:
        raise LimitsError("invalid_guard_limits", "limits")
    rows = HOLDERS[holder]
    for rule_id, value in block.items():
        if rule_id not in rows:
            # 知らない id・別の台帳の行（口座に主体の行、主体に口座の行）
            raise LimitsError("invalid_guard_limits", rule_id if isinstance(rule_id, str) else "limits")
        if holder == "account" and not fits_media(rule_id, media):
            raise LimitsError("invalid_guard_limits", rule_id)
        if problem(rule_id, value) is not None:
            raise LimitsError("invalid_guard_limits", rule_id)


def _normal(value):
    if isinstance(value, dict):
        return {"count": value["count"], "minutes": value["minutes"]}
    return value


def _alias_value(holder, cfg, key):
    """別名の鍵の値（受け取れる形のときだけ・無ければ None）。"""
    cfg = cfg or {}
    if key is None:
        return None
    if key.startswith("fotolife."):
        block = cfg.get("fotolife")
        value = block.get(key.split(".", 1)[1]) if isinstance(block, dict) else None
        from . import hatena_fotolife
        return value if hatena_fotolife.valid_limit("daily_max_uploads", value) else None
    value = cfg.get(key)
    if value is None:
        return None
    if holder == "account":
        from . import accounts
        return value if accounts.valid_guard_value(key, value) else None
    from . import principals
    return value if principals.valid_limit(key, value) else None


def explicit(holder, cfg, rule_id) -> tuple | None:
    """台帳に明示された値 `(値, どこから)`。どこから: `limits`（新しい形）か `alias:<鍵>`（今の鍵）。無ければ None。

    `limits` が勝つ。値は `{"count","minutes"}` か数（最低線の `reason` は外す）。
    """
    if HOLDERS[holder].get(rule_id) is None:
        return None
    block = (cfg or {}).get("limits")
    if isinstance(block, dict) and rule_id in block and problem(rule_id, block[rule_id]) is None:
        return _normal(block[rule_id]), "limits"
    key = HOLDERS[holder][rule_id][1]
    value = _alias_value(holder, cfg, key)
    if value is not None:
        return _normal(value), f"alias:{key}"
    return None


def floor_reason(cfg) -> str | None:
    """持ち主が最低線を上げているときの理由（上げていなければ None）。"""
    block = (cfg or {}).get("limits")
    value = block.get(FLOOR_ID) if isinstance(block, dict) else None
    if problem(FLOOR_ID, value) is None and isinstance(value, dict) and floor_raised(value):
        return value.get("reason")
    return None


def legacy_keys(holder, cfg) -> list:
    """台帳に明示された今の鍵（別名）の名前（`show` の非推奨の 1 行に使う・断らない）。"""
    cfg = cfg or {}
    out = []
    seen = set()
    for _rule_id, (_kind, key, _fits) in HOLDERS[holder].items():
        if key is None or key in seen:
            continue
        seen.add(key)
        if _alias_value(holder, cfg, key) is not None:
            out.append(key)
    return out


def tightens(rule_id, before, after) -> bool:
    """`before` → `after` が締める向き（か同じ）か（MCP の口は締める向きだけ・今の `TIGHTER_WHEN_LARGER`
    の一般形）。日の行は小さいほど、窓の行は件数が少なく窓が長いほど締まる。"""
    if isinstance(before, dict) and isinstance(after, dict):
        return after["count"] <= before["count"] and after["minutes"] >= before["minutes"]
    if isinstance(before, dict) or isinstance(after, dict):
        return False
    return after <= before


def parse_value(rule_id, words, *, reason=None):
    """CLI の語（`["12"]`・`["20", "10"]`）を行の値に。受け取れなければ `LimitsError`（符丁は `problem`）。"""
    kind = kind_of(rule_id)
    if kind is None:
        raise LimitsError("unknown_rule", rule_id)
    numbers = []
    for word in words:
        text = str(word).strip()
        if not text.isdigit() or len(text) > 6:
            raise LimitsError("invalid_value", rule_id)
        numbers.append(int(text))
    if kind in ("day", "uploads"):
        if len(numbers) != 1:
            raise LimitsError("invalid_value", rule_id)
        value = numbers[0]
    else:
        if len(numbers) != 2:
            raise LimitsError("invalid_value", rule_id)
        value = {"count": numbers[0], "minutes": numbers[1]}
        if kind == "floor" and reason is not None:
            value["reason"] = reason
    issue = problem(rule_id, value)
    if issue is not None:
        raise LimitsError(issue, rule_id)
    return value


class TableDefaults:
    """既定の数の表（`accounts.GUARD_DEFAULTS`・`principals.LIMIT_DEFAULTS`・`hatena_fotolife.LIMIT_DEFAULTS`）を
    柵の表から引く薄い写し（段 3・§8.4）。鍵は今の鍵、値は読んだときに `limits_policy.default_value` から。

    `rows` に無い鍵（`hold_minutes`・フォトライフの大きさなど・表の行でないもの）は `fixed` の値。媒体の台帳が
    検査を通らないときは `fallback`（4.1.0 の数）を返す——柵は `invalid_media_traits` で断るので、ここは表示と
    設定の検査のためだけに落ちない。撤去は段 5。
    """

    def __init__(self, rows: dict, fixed: dict, fallback: dict):
        self._rows = dict(rows)
        self._fixed = dict(fixed)
        self._fallback = dict(fallback)
        self._keys = tuple(fallback)

    def __getitem__(self, key):
        import copy
        if key not in self._keys:
            raise KeyError(key)
        if key in self._fixed:
            return copy.deepcopy(self._fixed[key])
        try:
            from . import limits_policy
            value = limits_policy.default_value(self._rows[key])
        except (ValueError, KeyError):
            value = None
        return copy.deepcopy(self._fallback[key] if value is None else value)

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)

    def __contains__(self, key):
        return key in self._keys

    def keys(self):
        return list(self._keys)

    def items(self):
        return [(key, self[key]) for key in self._keys]

    def values(self):
        return [self[key] for key in self._keys]

    def get(self, key, default=None):
        return self[key] if key in self._keys else default

    def __eq__(self, other):
        return dict(self.items()) == (dict(other.items()) if hasattr(other, "items") else other)

    def __repr__(self):
        return f"TableDefaults({dict(self.items())!r})"


# --------------------------------------------------------------------------
# 書く（`thth account set`／`thth principal set` の新しい名前＝行の id・段 3・§8.2）
# --------------------------------------------------------------------------

REMOVE_WORDS = ("-",)
# 設定の口の断り（静的な符丁）。
CHANGE_REASONS = frozenset(("invalid_setting", "unknown_rule", "invalid_value", "invalid_reason",
                            "rule_not_for_media", "floor_reason_required", "floor_above_max",
                            "settings_loosen_requires_owner", "ledger_unavailable"))


def _effective(holder, cfg, rule_id):
    """いま効いている値（数か `{"count","minutes"}`）と出所。"""
    from . import limits_policy
    if holder == "account":
        row = limits_policy.resolved(rule_id, cfg=cfg)
    else:
        row = limits_policy.resolved(rule_id, principal_cfg=cfg)
    count = row["count"]
    if kind_of(rule_id) in ("minutes", "floor"):
        value = None if count is None else {"count": count, "minutes": row["window"]["minutes"]}
    else:
        value = count
    return value, row["origin"], row.get("via")


def _path(holder, name):
    import os
    if holder == "account":
        from . import accounts
        return os.path.join(accounts.accounts_dir(), name + ".json")
    from . import principals
    return principals.path_for(name)


def _load(holder, name):
    if holder == "account":
        from . import accounts
        return accounts.load_account(name)
    from . import principals
    return principals.load_principal(name)


def drop_aliased(raw, holder, key, media=None) -> list:
    """今の鍵（別名）で書いたとき、その鍵が指す行の `limits` を外す（今の鍵の変更が効くように・段 3）。"""
    block = raw.get("limits")
    if not isinstance(block, dict):
        return []
    dropped = [rule_id for rule_id in rows_for_alias(holder, key, media) if rule_id in block]
    for rule_id in dropped:
        block.pop(rule_id)
    if not block:
        raw.pop("limits")
    return dropped


def change(holder, name, rule_id, words, *, by, via="cli", reason=None, tighten_only=False) -> dict:
    """行 1 つを台帳の `limits` に書く（`-` で外す）。答えは `{<holder>, key, before, after, changed, via, ...}`。

    - 口座の行は口座の媒体に当たるものだけ（`rule_not_for_media`）。
    - `write.floor` を既定より上げるには `reason`（`floor_reason_required`）・10 分あたり 100 件まで
      （`floor_above_max`）。上げた理由は台帳の値と変更ログ（`settings_set` の `write.floor.reason`）に残る。
    - `tighten_only`（MCP の口の形）は締める向きだけ（`settings_loosen_requires_owner`）。今は CLI だけが呼ぶ。
    """
    import json
    from . import accounts, admin_log, secrets_fs
    admin_log.actor(by)
    if holder not in HOLDERS or not accounts.name_is_safe(name):
        raise LimitsError("invalid_setting")
    if HOLDERS[holder].get(rule_id) is None:
        raise LimitsError("unknown_rule", rule_id)
    cfg = _load(holder, name)
    if holder == "account" and not fits_media(rule_id, cfg.get("media")):
        raise LimitsError("rule_not_for_media", rule_id)
    before, _origin_before, via_before = _effective(holder, cfg, rule_id)
    remove = list(words) == list(REMOVE_WORDS)
    if reason is not None and not valid_reason(reason):
        raise LimitsError("invalid_reason", rule_id)
    value = None if remove else parse_value(rule_id, words, reason=reason)
    path = _path(holder, name)
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raise LimitsError("ledger_unavailable") from None
    if not isinstance(raw, dict):
        raise LimitsError("ledger_unavailable")
    original = json.loads(json.dumps(raw))
    block = dict(raw["limits"]) if isinstance(raw.get("limits"), dict) else {}
    unchanged = {holder: name, "key": rule_id, "before": before, "after": before, "changed": False,
                 "via": via_before}
    if remove:
        if rule_id not in block:
            return unchanged
        block.pop(rule_id)
    else:
        if block.get(rule_id) == value:
            return unchanged
        block[rule_id] = value
    if block:
        raw["limits"] = block
    else:
        raw.pop("limits", None)
    # 締める向きだけ: 書いたあとの効く値で比べる（外すと既定に戻る＝緩むこともある）
    after_cfg = dict(raw)
    if holder == "account":
        after_cfg["media"] = cfg.get("media")
    after, origin_after, via_after = _effective(holder, after_cfg, rule_id)
    if tighten_only and before is not None and not (after is not None and tightens(rule_id, before, after)):
        raise LimitsError("settings_loosen_requires_owner", rule_id)
    secrets_fs.atomic_write_json(path, raw)
    try:
        _load(holder, name)
    except accounts.AccountError:
        secrets_fs.atomic_write_json(path, original)
        raise LimitsError("invalid_value", rule_id) from None
    diff = {rule_id: [before, after]}
    reason_after = floor_reason(raw) if rule_id == FLOOR_ID else None
    if rule_id == FLOOR_ID and (reason_after or floor_reason(original)):
        diff[FLOOR_ID + ".reason"] = [floor_reason(original), reason_after]
    if holder == "account":
        subject_cfg = cfg
    else:
        from . import principals
        subject_cfg = {"media": principals.MEDIA}
    try:
        admin_log.append("settings_set", name, subject_cfg, by=by, via=via, diff=diff)
    except Exception:
        # 変更ログに残せない書き込みは残さない（`principal add` と同じ規律）
        secrets_fs.atomic_write_json(path, original)
        raise
    out = {holder: name, "key": rule_id, "before": before, "after": after, "changed": True,
           "via": via_after, "origin": origin_after}
    if rule_id == FLOOR_ID:
        out["floor_raised"] = reason_after is not None
        out["floor_reason"] = reason_after
    return out


def message(code, rule_id=None, holder="account") -> str:
    """設定の口の断りの文（符丁つき・値は入れない）。"""
    rows = "・".join(HOLDERS.get(holder, ACCOUNT_ROWS))
    words = {
        "unknown_rule": f"知らない行です（{holder} で書ける行: {rows}）",
        "invalid_value": "値の形が違います（日の行は 0 以上の整数 1 つ、窓の行は `<件数> <分>`、外すのは `-`）",
        "invalid_reason": f"理由は 1〜{REASON_MAX} 字（制御文字は不可）です",
        "rule_not_for_media": "この口座の媒体に当たらない行です",
        "floor_reason_required": ("最低線（write.floor）を既定（10 分に 20）より上げるには --reason <理由> が要ります"
                                  "（変更ログと台帳に残り、show に出ます）"),
        "floor_above_max": (f"最低線は 10 分あたり {FLOOR_MAX_COUNT} 件までしか上げられません"
                            f"（件数 ≤ {FLOOR_MAX_COUNT} かつ 件数 ≤ {FLOOR_MAX_PER_MINUTE} × 分）"),
        "settings_loosen_requires_owner": "緩める変更は持ち主の口（CLI・/activity）だけです",
        "ledger_unavailable": "台帳が読めません",
        "invalid_setting": "受け取れません",
    }
    return f"{code}: " + (f"{rule_id}: " if rule_id else "") + words.get(code, "受け取れません")
