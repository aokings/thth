"""媒体の特性の台帳を読む・確かめる（設計「上限と連投の柵」2026-10-17 §4・段 0）。

台帳は `thth/media_traits/<媒体>.json`（repo の中のデータ・wheel に同梱）。媒体ごとに 1 ファイルで、
項目（`traits[]`）ごとに「その媒体がスパムをどう見ていると想定できるか・何を良い振る舞いとするか・
罰の形・API の上限」と、**根拠の種類**（`evidence.type`）と**確かさ**（`confidence`）を持つ。

段 2 から柵の表（`limits_policy`）が `collect_defaults()` を読む: 台帳の既定の行は `limits_policy.LEDGER_MAP`
（写像の表）を通って表の行に結ばれ、根拠（媒体ごとの項目・確かさ・数の根拠）と、明示の値が無いときの数になる。
`bookmark.stop`・`upload.daily` は台帳から行が生まれる。語彙（信号・行の id・動作・単位・鍵・振る舞い・目的）の
正本はこの模組（`limits_policy`・`assess` が import する）。

検査（`validate`／`check`・T-M-1）が断るもの:
- 形の違い・知らない欄・知らない `kind`／`evidence.type`／`confidence`／信号（行の id）
- `id` の重複・`id` の頭が媒体名でない
- **`inferred` なのに `low` でない**（推測は確かさ low 固定）
- `official_doc` に `url` か `checked_at` が無い・二次で読んだ（`via`）のに `high`
- `defaults` の鍵が `signals` に無い・同じ媒体で同じ行の既定を 2 つの項目が持つ
- 既定の `applies_to` が配列でない・数える動作に無い（段 2）
- 数のある既定に数の根拠（`count_evidence`）が無い・公式／観測と言うのに項目の根拠にその種類が無い（段 2・中-2）
- **未対応の媒体（`supported: false`）に `defaults`**・既定を持てない種類（`good_signal`・`penalty`）に `defaults`
- 対応する 7 媒体の台帳が欠けている・`supported: true` を名乗る知らない媒体

この模組は import のときに台帳を読まない（壊れた台帳でも `thth media check` が理由を言えるように）。
読む口（`load`・`load_all`・`collect_defaults`）は、検査に落ちた台帳を断る（既定に倒さない）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SCHEMA = "thth.media-traits/1"
TRAITS_DIR = Path(__file__).resolve().parent / "media_traits"

# THTH が対応する媒体（§4.1）。ここに無い媒体は `supported: false` でだけ置ける。
SUPPORTED_MEDIA = ("threads", "x", "bluesky", "mastodon", "hatenablog", "fotolife", "hatenabookmark")

KINDS = ("spam_signal", "good_signal", "penalty", "api_limit", "policy", "observation", "culture")
# 既定（柵の行）を持てない種類（§4.1「既定を持たない項目（good_signal・penalty）は予告の材料」）。
KINDS_WITHOUT_DEFAULTS = ("good_signal", "penalty")
EVIDENCE_TYPES = ("official_doc", "official_statement", "observed", "inferred")
CONFIDENCES = ("high", "medium", "low")
# 根拠の種類ごとに要る欄と、置いてよい欄。
EVIDENCE_FIELDS = {
    "official_doc": ({"url", "checked_at"}, {"quote", "via", "also"}),
    "official_statement": ({"source"}, {"url", "checked_at", "quote", "also"}),
    "observed": ({"record", "observed_at"}, {"also"}),
    "inferred": ({"by", "at"}, {"also"}),
}
PENALTIES = ("display_suppression", "recommendation_excluded", "search_exclusion", "request_refused",
             "rate_limited", "human_verification", "account_lock", "account_suspension")

# 信号（§5.2）。最初の版の 7 つ（`assess.SIGNALS` はこれを読む）と、段 4 で足す 3 つ（advise だけ）。
# **語彙の正本はこの模組**（段 2・監査 中-1）: `limits_policy`・`assess` はここから import し、2 か所に書かない。
SIGNALS_V1 = ("frequency", "same_target", "same_host", "self_reference", "counterpart",
              "recent_platform_refusal", "quiet_and_interval")
SIGNALS = SIGNALS_V1 + ("same_text", "link_heavy", "engagement_bait")
# 柵の行の id（§6.3・§6.4・`<動作>.<欄>`）。台帳の `signals` はこの名で「どの行に効く項目か」を言う。
RULE_IDS = frozenset((
    # SNS
    "post.daily", "post.burst", "post.interval", "post.quiet", "retract.daily", "reply.burst", "reply.daily",
    "reaction.daily", "reply.counterpart", "post.platform_refusal", "post.monthly", "read.budget",
    "post.same_text", "post.link_heavy", "post.engagement_bait",
    # はてなブログ・フォトライフ
    "entry.daily", "entry_delete.daily", "entry.blog_daily", "entry_delete.blog_daily", "entry.principal_daily",
    "entry_delete.principal_daily", "entry.interval", "entry.quiet", "entry.burst", "entry.principal_burst",
    "entry.platform_refusal", "upload.daily",
    # 反応（段 1 の表の行）
    "reaction.quiet",
    # はてなブックマーク
    "bookmark.once", "bookmark.retry_once", "bookmark.self_daily", "bookmark.same_host_daily",
    "bookmark.burst", "bookmark.daily", "bookmark.stop", "read.interval",
    # 共通の最低線
    "write.floor",
))
# 行の欄（§2・§6.2）。
ACTIONS = ("post", "reply", "retract", "schedule", "entry_create", "entry_update", "entry_delete",
           "upload", "bookmark_add", "bookmark_delete", "reaction", "repost", "read")
SCOPES = ("account", "blog", "principal", "owner", "app")
# `own`: 自分の記事（`target: own`）のブックマークを 1 件と数える（`bookmark.self_daily`）。
KEYS = ("op", "target", "url", "host", "own")
# `halted`: 印がすでに置かれている（媒体の断りの印・`entry.platform_refusal`）＝止めとして断る。
ON_EXCEED = ("stop", "refuse", "wait", "advise", "hold", "halted")
# 既定の数の根拠（段 2・監査 中-2）。`official_doc`／`official_statement`／`observed` は項目の根拠に
# 同じ種類があるときだけ（数そのものが公式・観測に書いてある）。`inferred` は推測（確かさ low で出す）。
# `thth_line` は媒体の事実でなく THTH の運用の線（目的 c）——確かさは問わない（None で出す）。
COUNT_EVIDENCE = ("official_doc", "official_statement", "observed", "inferred", "thth_line")
# 目的（§2）: (a) 媒体の事実・(b) 安全装置・(c) 運用の好み。
PURPOSES = ("platform", "safety", "preference")
TIGHTER = ("lower", "higher")
DEFAULT_FIELDS_REQUIRED = {"signal", "actions", "scope", "window", "count", "key", "on_exceed", "purpose"}
DEFAULT_FIELDS_OPTIONAL = {"applies_to", "refusal", "detail", "tighter", "count_evidence"}
WINDOW_MINUTES_MAX = 1440
COUNT_MAX = 1000

TRAIT_FIELDS_REQUIRED = {"id", "kind", "statement", "evidence", "confidence"}
TRAIT_FIELDS_OPTIONAL = {"signals", "defaults", "penalty", "notes"}
LEDGER_FIELDS = {"schema", "media", "supported", "relates_to", "traits"}

MEDIA_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
TRAIT_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
URL_RE = re.compile(r"https?://\S+")
# 言葉の符丁（静的・CLI の stderr と JSON の `error`）。
UNKNOWN_MEDIA = "unknown_media"
INVALID = "invalid_media_traits"


class MediaTraitsError(ValueError):
    """台帳が読めない・知らない媒体。`code` は静的な符丁、`problems` は検査の結果。"""

    def __init__(self, code, message, problems=None):
        super().__init__(f"{message}（{code}）")
        self.code = code
        self.problems = list(problems or [])


def _problem(code, media, trait=None, detail=None) -> dict:
    return {"code": code, "media": media, "trait": trait, "detail": detail}


# --------------------------------------------------------------------------
# 検査
# --------------------------------------------------------------------------

def _check_evidence(media, tid, evidence, top=True) -> list:
    out = []
    if not isinstance(evidence, dict):
        return [_problem("evidence_not_object", media, tid)]
    kind = evidence.get("type")
    if kind not in EVIDENCE_TYPES:
        return [_problem("unknown_evidence_type", media, tid, kind)]
    required, optional = EVIDENCE_FIELDS[kind]
    if not top:
        optional = optional - {"also"}
    fields = set(evidence) - {"type"}
    for name in sorted(required - fields):
        out.append(_problem("evidence_field_missing", media, tid, f"{kind}.{name}"))
    for name in sorted(fields - required - optional):
        out.append(_problem("evidence_field_unknown", media, tid, f"{kind}.{name}"))
    for name in ("url",):
        if name in evidence and not (isinstance(evidence[name], str) and URL_RE.fullmatch(evidence[name])):
            out.append(_problem("evidence_url_invalid", media, tid, evidence.get(name)))
    for name in ("checked_at", "observed_at", "at"):
        if name in evidence and not (isinstance(evidence[name], str) and DATE_RE.fullmatch(evidence[name])):
            out.append(_problem("evidence_date_invalid", media, tid, f"{name}={evidence.get(name)!r}"))
    for name in ("source", "record", "by", "quote", "via"):
        if name in evidence and not (isinstance(evidence[name], str) and evidence[name].strip()):
            out.append(_problem("evidence_text_empty", media, tid, name))
    if "also" in evidence and top:
        also = evidence["also"]
        if not isinstance(also, list) or not also:
            out.append(_problem("evidence_also_invalid", media, tid))
        else:
            for item in also:
                out += _check_evidence(media, tid, item, top=False)
    return out


def _check_default(media, tid, rule_id, row) -> list:
    out = []
    where = f"{rule_id}"
    if not isinstance(row, dict):
        return [_problem("default_not_object", media, tid, where)]
    fields = set(row)
    for name in sorted(DEFAULT_FIELDS_REQUIRED - fields):
        out.append(_problem("default_field_missing", media, tid, f"{where}.{name}"))
    for name in sorted(fields - DEFAULT_FIELDS_REQUIRED - DEFAULT_FIELDS_OPTIONAL):
        out.append(_problem("default_field_unknown", media, tid, f"{where}.{name}"))
    if out:
        return out
    if row["signal"] not in SIGNALS:
        out.append(_problem("unknown_signal", media, tid, f"{where}.signal={row['signal']!r}"))
    actions = row["actions"]
    if not (isinstance(actions, list) and actions and all(a in ACTIONS for a in actions)
            and len(set(actions)) == len(actions)):
        out.append(_problem("default_actions_invalid", media, tid, f"{where}.actions={actions!r}"))
    # `applies_to` は数える動作のうち「この行を当てる動作」の配列（段 2・監査 中-1）。
    applies = row.get("applies_to")
    if "applies_to" in row and not (isinstance(applies, list) and applies and isinstance(actions, list)
                                    and all(a in actions for a in applies) and len(set(applies)) == len(applies)):
        out.append(_problem("default_applies_to_invalid", media, tid, f"{where}.applies_to={applies!r}"))
    if row["scope"] not in SCOPES:
        out.append(_problem("default_scope_invalid", media, tid, f"{where}.scope={row['scope']!r}"))
    window = row["window"]
    if not (window is None or window == {"day": "jst"} or window == {"forever": True}
            or (isinstance(window, dict) and set(window) == {"minutes"} and type(window["minutes"]) is int
                and 1 <= window["minutes"] <= WINDOW_MINUTES_MAX)):
        out.append(_problem("default_window_invalid", media, tid, f"{where}.window={window!r}"))
    count = row["count"]
    if not (count is None or (type(count) is int and 0 <= count <= COUNT_MAX)):
        out.append(_problem("default_count_invalid", media, tid, f"{where}.count={count!r}"))
    if row["key"] is not None and row["key"] not in KEYS:
        out.append(_problem("default_key_invalid", media, tid, f"{where}.key={row['key']!r}"))
    if row["on_exceed"] not in ON_EXCEED:
        out.append(_problem("default_on_exceed_invalid", media, tid, f"{where}.on_exceed={row['on_exceed']!r}"))
    if row["purpose"] not in PURPOSES:
        out.append(_problem("default_purpose_invalid", media, tid, f"{where}.purpose={row['purpose']!r}"))
    # 窓で数える信号は、窓と数と鍵が揃っていないと行にならない。
    if row["signal"] in ("frequency", "same_target", "same_host", "self_reference") and (
            window is None or count is None or row["key"] is None):
        out.append(_problem("default_window_count_missing", media, tid, where))
    # 止める（人が戻す）のは安全装置だけ（§3.2: (b) → stop）。
    if row["on_exceed"] == "stop" and row["purpose"] != "safety":
        out.append(_problem("default_stop_not_safety", media, tid, where))
    if "tighter" in row and row["tighter"] not in TIGHTER:
        out.append(_problem("default_tighter_invalid", media, tid, f"{where}.tighter={row['tighter']!r}"))
    for name in ("refusal", "detail"):
        if name in row and not (isinstance(row[name], str) and re.fullmatch(r"[a-z][a-z0-9_]*", row[name])):
            out.append(_problem("default_code_invalid", media, tid, f"{where}.{name}={row[name]!r}"))
    # 数のある行は数の根拠が要る（中-2: 推測の数に項目の `official_doc`・`high` が付いて見えないように）。
    basis = row.get("count_evidence")
    if count is not None and basis is None:
        out.append(_problem("count_evidence_missing", media, tid, where))
    elif basis is not None and basis not in COUNT_EVIDENCE:
        out.append(_problem("count_evidence_invalid", media, tid, f"{where}.count_evidence={basis!r}"))
    elif basis is not None and count is None:
        out.append(_problem("count_evidence_without_count", media, tid, where))
    return out


def _evidence_types(evidence) -> set:
    if not isinstance(evidence, dict):
        return set()
    out = {evidence.get("type")}
    for item in evidence.get("also") or [] if isinstance(evidence.get("also"), list) else []:
        if isinstance(item, dict):
            out.add(item.get("type"))
    return out


def count_confidence(trait, row):
    """既定の数の確かさ: 推測は low・THTH の線は None（媒体の事実でない）・公式／観測は項目の確かさ。"""
    basis = row.get("count_evidence")
    if basis == "inferred":
        return "low"
    if basis in (None, "thth_line"):
        return None
    return trait.get("confidence")


def _check_trait(media, supported, trait) -> list:
    if not isinstance(trait, dict):
        return [_problem("trait_not_object", media)]
    tid = trait.get("id") if isinstance(trait.get("id"), str) else None
    out = []
    fields = set(trait)
    for name in sorted(TRAIT_FIELDS_REQUIRED - fields):
        out.append(_problem("trait_field_missing", media, tid, name))
    for name in sorted(fields - TRAIT_FIELDS_REQUIRED - TRAIT_FIELDS_OPTIONAL):
        out.append(_problem("trait_field_unknown", media, tid, name))
    if tid is None or not tid.startswith(media + ".") or not TRAIT_NAME_RE.fullmatch(tid[len(media) + 1:]):
        out.append(_problem("trait_id_invalid", media, tid, f"「{media}.<名前>」の形で"))
    if "kind" in trait and trait["kind"] not in KINDS:
        out.append(_problem("unknown_kind", media, tid, trait["kind"]))
    if "statement" in trait and not (isinstance(trait["statement"], str) and trait["statement"].strip()):
        out.append(_problem("statement_empty", media, tid))
    confidence = trait.get("confidence")
    if "confidence" in trait and confidence not in CONFIDENCES:
        out.append(_problem("unknown_confidence", media, tid, confidence))
    evidence = trait.get("evidence")
    if "evidence" in trait:
        out += _check_evidence(media, tid, evidence)
    if isinstance(evidence, dict):
        # 推測は確かさ low 固定（§4.1）。
        if evidence.get("type") == "inferred" and confidence != "low":
            out.append(_problem("inferred_must_be_low", media, tid, f"confidence={confidence!r}"))
        # 公式の頁を開けず二次で読んだものは high にしない（§4.1「medium に落とし notes に書く」）。
        if evidence.get("type") == "official_doc" and evidence.get("via") and confidence == "high":
            out.append(_problem("secondhand_cannot_be_high", media, tid))
    signals = trait.get("signals", [])
    if not (isinstance(signals, list) and all(isinstance(s, str) for s in signals)):
        out.append(_problem("signals_invalid", media, tid))
        signals = []
    for name in signals:
        if name not in RULE_IDS:
            out.append(_problem("unknown_signal", media, tid, name))
    if len(set(signals)) != len(signals):
        out.append(_problem("signals_duplicate", media, tid))
    if "penalty" in trait:
        pen = trait["penalty"]
        if not (isinstance(pen, list) and pen and all(p in PENALTIES for p in pen)):
            out.append(_problem("penalty_invalid", media, tid, pen))
    if "notes" in trait and not isinstance(trait["notes"], str):
        out.append(_problem("notes_invalid", media, tid))
    if "defaults" in trait:
        defaults = trait["defaults"]
        if not supported:
            out.append(_problem("unsupported_media_has_defaults", media, tid))
        if trait.get("kind") in KINDS_WITHOUT_DEFAULTS:
            out.append(_problem("kind_cannot_have_defaults", media, tid, trait.get("kind")))
        if not isinstance(defaults, dict) or not defaults:
            out.append(_problem("defaults_invalid", media, tid))
        else:
            for rule_id, row in defaults.items():
                if rule_id not in signals:
                    out.append(_problem("default_not_in_signals", media, tid, rule_id))
                out += _check_default(media, tid, rule_id, row)
                # 数が公式・観測に書いてあると言うなら、項目の根拠にその種類が要る。
                basis = row.get("count_evidence") if isinstance(row, dict) else None
                if basis in ("official_doc", "official_statement", "observed") and \
                        basis not in _evidence_types(evidence):
                    out.append(_problem("count_evidence_not_in_trait", media, tid, f"{rule_id}={basis}"))
    return out


def validate(doc, media=None) -> list:
    """台帳 1 つの検査。問題の list（空なら通る）。`media` はファイル名から来る媒体名。"""
    name = media or (doc.get("media") if isinstance(doc, dict) else None) or "?"
    if not isinstance(doc, dict):
        return [_problem("ledger_not_object", name)]
    out = []
    fields = set(doc)
    for field in sorted(LEDGER_FIELDS - fields):
        out.append(_problem("ledger_field_missing", name, None, field))
    for field in sorted(fields - LEDGER_FIELDS):
        out.append(_problem("ledger_field_unknown", name, None, field))
    if doc.get("schema") != SCHEMA:
        out.append(_problem("schema_mismatch", name, None, doc.get("schema")))
    if media is not None and doc.get("media") != media:
        out.append(_problem("media_name_mismatch", name, None, f"media={doc.get('media')!r}"))
    if not (isinstance(doc.get("media"), str) and MEDIA_RE.fullmatch(doc.get("media"))):
        out.append(_problem("media_name_invalid", name, None, doc.get("media")))
    supported = doc.get("supported")
    if type(supported) is not bool:
        out.append(_problem("supported_invalid", name, None, supported))
        supported = False
    if supported and name not in SUPPORTED_MEDIA:
        out.append(_problem("supported_unknown_media", name))
    if not supported and name in SUPPORTED_MEDIA:
        out.append(_problem("supported_media_marked_unsupported", name))
    relates = doc.get("relates_to", [])
    if not (isinstance(relates, list) and all(isinstance(r, str) and MEDIA_RE.fullmatch(r) and r != name
                                               for r in relates)):
        out.append(_problem("relates_to_invalid", name, None, relates))
    traits = doc.get("traits")
    if not isinstance(traits, list) or not traits:
        out.append(_problem("traits_empty", name))
        return out
    seen_ids = set()
    default_owner = {}
    for trait in traits:
        out += _check_trait(name, supported, trait)
        if not isinstance(trait, dict):
            continue
        tid = trait.get("id")
        if tid in seen_ids:
            out.append(_problem("duplicate_id", name, tid))
        seen_ids.add(tid)
        for rule_id in (trait.get("defaults") or {}) if isinstance(trait.get("defaults"), dict) else ():
            if rule_id in default_owner:
                out.append(_problem("duplicate_default", name, tid, f"{rule_id}（{default_owner[rule_id]} と重なる）"))
            default_owner.setdefault(rule_id, tid)
    return out


# --------------------------------------------------------------------------
# 読む
# --------------------------------------------------------------------------

def _directory(directory=None) -> Path:
    return Path(directory) if directory is not None else TRAITS_DIR


def list_media(directory=None) -> list:
    """置き場にある台帳の媒体名（ファイル名の順）。"""
    base = _directory(directory)
    if not base.is_dir():
        return []
    return sorted(p.stem for p in base.glob("*.json"))


def _read(media, directory=None):
    """(doc, problems)。JSON として読めなければ doc は None。"""
    path = _directory(directory) / f"{media}.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [_problem("ledger_missing", media)]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, [_problem("ledger_unreadable", media, None, type(exc).__name__)]
    return doc, validate(doc, media)


def load(media, directory=None) -> dict:
    """媒体 1 つの台帳。知らない媒体は `unknown_media`、検査に落ちれば `invalid_media_traits`。"""
    if not (isinstance(media, str) and MEDIA_RE.fullmatch(media)) or media not in list_media(directory):
        raise MediaTraitsError(UNKNOWN_MEDIA, f"媒体の台帳がありません: {media!r}")
    doc, problems = _read(media, directory)
    if problems:
        raise MediaTraitsError(INVALID, f"媒体の台帳 {media} が検査を通りません", problems)
    return doc


def check(directory=None) -> dict:
    """全部の台帳の検査（`thth media check`・T-M-1）。台帳の間の検査（欠け・`relates_to` の先）も見る。"""
    names = list_media(directory)
    problems = []
    docs = {}
    for media in names:
        doc, found = _read(media, directory)
        problems += found
        if doc is not None:
            docs[media] = doc
    for media in SUPPORTED_MEDIA:
        if media not in names:
            problems.append(_problem("ledger_missing", media))
    known = set(names) | set(SUPPORTED_MEDIA)
    for media, doc in docs.items():
        for other in doc.get("relates_to") or [] if isinstance(doc.get("relates_to"), list) else []:
            if isinstance(other, str) and other not in known:
                problems.append(_problem("relates_to_unknown", media, None, other))
    traits = sum(len(doc.get("traits") or []) for doc in docs.values() if isinstance(doc.get("traits"), list))
    return {"schema": SCHEMA, "ok": not problems, "media": names, "traits": traits, "problems": problems}


def load_all(directory=None) -> dict:
    """全部の台帳（媒体名 → 台帳）。1 つでも検査に落ちれば `invalid_media_traits` で断る。"""
    report = check(directory)
    if report["problems"]:
        raise MediaTraitsError(INVALID, "媒体の台帳が検査を通りません（thth media check）", report["problems"])
    return {media: _read(media, directory)[0] for media in report["media"]}


def collect_defaults(directory=None) -> list:
    """台帳の `defaults` を柵の行の形（§6.2）に集める。段 1 の `limits_policy` が読む口（T-M-2）。

    行は `{"id", "media", "trait", "confidence", "evidence_type", "count_confidence", <§6.2 の欄>}`。
    対応する媒体だけ・媒体と id の組で 1 行。`supported: false` の媒体は検査で `defaults` を断るので行を
    作らない。`confidence` は項目の確かさ、`count_confidence` は**数**の確かさ（`count_evidence` が
    `inferred` なら low・`thth_line` なら None・段 2・中-2）。
    """
    rows = []
    for media, doc in load_all(directory).items():
        if not doc["supported"]:
            continue
        for trait in doc["traits"]:
            for rule_id, row in (trait.get("defaults") or {}).items():
                rows.append({"id": rule_id, "media": media, "trait": trait["id"],
                             "confidence": trait["confidence"], "evidence_type": trait["evidence"]["type"],
                             "count_confidence": count_confidence(trait, row), **row})
    return rows


def trait_ids(directory=None) -> set:
    """台帳にある項目の id の全部（行の `trait` が実在するかを見る口）。"""
    return {trait["id"] for doc in load_all(directory).values() for trait in doc["traits"]}
