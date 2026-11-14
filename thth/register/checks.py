"""台帳の器 段 4「検査の部品」（種類の宣言の `checks`。指示 `docs/指示_台帳の器_段4_検査の形と済みの線_2026-11-14.md` §2）。

宣言に書けるのは 9 つの部品（`unique`・`required`・`empty`・`one_of`・`words`・`shape`・`forbid_pair`・`count`・
`judged`）と、決まった形の条件だけ。**式・正規表現・命令は受け取らない。** 値の形（`shape`）は THTH が備えた名前から
選ぶ。宣言にどんな値を書かれても、例外を漏らさず `DeclarationError("checks")` にする。

回すときは、先に索引を作り、記録の数に比例する手間で終える（2 重の繰り返しにしない）。`forbid_pair` と `count` は
宣言済みの指す欄を 1 段だけたどる。よその台帳を指す先（`<台帳>:<番号>`）は違反にせず「確かめられない」に数える。

台帳にも THTH の側にも書かない。網に出ない。
"""
from __future__ import annotations

import json
import re

from .errors import RegisterRefused

PARTS = ("unique", "required", "empty", "one_of", "words", "shape", "forbid_pair", "count", "judged", "columns",
         "prefixed_words")
LEVELS = ("error", "warn", "review")
CHECKS_MAX = 64          # 1 つの種類の検査の数
FIELDS_MAX = 16          # `fields` の欄の数
WORDS_MAX = 1024         # 条件・`words` の語の数（段 4 G5 で 256 → 1,024）
COLUMNS_MAX = 1024       # `columns` の欄の数
PREFIXES_MAX = 64        # `prefixed_words` の頭の数
PATH_STEPS_MAX = 4       # 点でつないだ欄の道の段（段 4 G8）
WORD_LEN_MAX = 256       # 1 つの語の字数
COND_MAX = 16            # 1 つの条件の欄の数
NOTE_MAX = 2000          # 注の字数
PREFIX_MAX = 40
COUNT_MAX = 1_000_000_000
SHOWN_MAX = 50           # 1 つの検査につき出す違反の記録の数（残りは数だけ）

# 値の形（THTH が備えた名前だけ。宣言から正規表現は受け取らない）。
_SHAPES = {
    "slug": re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z"),
    "slug_loose": re.compile(r"[a-z0-9][a-z0-9-]*\Z"),
    "date": re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z"),
    "date_partial": re.compile(r"[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?\Z"),
    "lang": re.compile(r"[a-z]{2,3}\Z"),
    "http_url": re.compile(r"https?://", re.ASCII),
    "https_url": re.compile(r"https://", re.ASCII),
    "positive_int": re.compile(r"[1-9][0-9]*\Z"),
    "sha256": re.compile(r"[0-9a-f]{64}\Z"),
}
SHAPES = tuple(_SHAPES)
_PREFIX_ONLY = ("http_url", "https_url")  # 頭だけを見る形（match）。ほかは全体（fullmatch）

_COMMON = ("check", "id", "level", "when", "note")
_ARGS = {
    "unique": ("fields", "fold", "skip_blank"),
    "required": ("fields",),
    "empty": ("fields",),
    "one_of": ("fields", "mode"),
    "words": ("field", "in", "allow_blank"),
    "shape": ("field", "shape", "prefix", "allow_blank"),
    "forbid_pair": ("via", "target"),
    "count": ("from", "where", "distinct", "min", "max"),
    "judged": ("question", "min", "how"),
    "columns": ("required", "allowed", "forbidden"),
    "prefixed_words": ("field", "sep", "prefixes", "allow_blank"),
}
_QUESTION = re.compile(r"[a-z][a-z0-9_]{0,31}(@[a-z][a-z0-9_]{0,31})?\Z")


# ------------------------------------------------------------------ 宣言の読み

def _err():
    from .vessel import DeclarationError
    return DeclarationError("checks")


def _bool(value, default):
    if value is None:
        return default
    if not isinstance(value, bool):
        raise _err()
    return value


def _int(value, *, low=0):
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= COUNT_MAX:
        raise _err()
    return value


def _fieldname(value):
    from .vessel import FIELD
    if not isinstance(value, str) or not FIELD.fullmatch(value):
        raise _err()
    return value


def _path(value):
    """検査と条件の欄: 欄の名前か、点でつないだ道（`mb.verdict`。4 段まで。並びの中は読まない）。"""
    if not isinstance(value, str) or value.count(".") >= PATH_STEPS_MAX:
        raise _err()
    for step in value.split("."):
        _fieldname(step)
    return value


def _fields(value, *, at_least=1, limit=FIELDS_MAX, name=None):
    if not isinstance(value, list) or not at_least <= len(value) <= limit:
        raise _err()
    out = [(name or _path)(v) for v in value]
    if len(set(out)) != len(out):
        raise _err()
    return out


def _words(value):
    if not isinstance(value, list) or not 1 <= len(value) <= WORDS_MAX:
        raise _err()
    out = []
    for word in value:
        if not isinstance(word, str) or len(word) > WORD_LEN_MAX or "\n" in word or "\r" in word:
            raise _err()
        out.append(word.strip())
    return frozenset(out)


def parse_condition(raw) -> dict:
    """条件 `{"<欄>": {"in": [...]} | {"not_in": [...]} | {"blank": bool}, …}`（全部が合えば合う）。"""
    if not isinstance(raw, dict) or not 1 <= len(raw) <= COND_MAX:
        raise _err()
    out = {}
    for name, test in raw.items():
        _path(name)
        if not isinstance(test, dict):
            raise _err()
        has_or_blank = "or_blank" in test
        or_blank = test.get("or_blank")
        rest = {k: v for k, v in test.items() if k != "or_blank"}
        if len(rest) != 1:
            raise _err()
        (op, arg), = rest.items()
        if has_or_blank and (or_blank is not True or op not in ("in", "not_in")):
            raise _err()  # `or_blank`（この語か、空。段 4 G9）は in・not_in と一緒にだけ
        if op in ("in", "not_in"):
            out[name] = (op, _words(arg), bool(or_blank))
        elif op == "blank":
            if not isinstance(arg, bool):
                raise _err()
            out[name] = (op, arg, False)
        else:
            raise _err()
    return out


def _parse_one(raw, pointers: list) -> dict:
    from .vessel import NAME, _only
    if not isinstance(raw, dict):
        raise _err()
    part = raw.get("check")
    if part not in PARTS:
        raise _err()
    try:
        _only(raw, _COMMON + _ARGS[part], "checks")
    except Exception:
        raise _err() from None
    cid = raw.get("id")
    if not isinstance(cid, str) or not NAME.fullmatch(cid):
        raise _err()
    level = raw.get("level", "error")
    if level not in LEVELS:
        raise _err()
    note = raw.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > NOTE_MAX):
        raise _err()
    out = {"check": part, "id": cid, "level": level, "note": note,
           "when": parse_condition(raw["when"]) if raw.get("when") is not None else None}
    if part == "unique":
        out["fields"] = _fields(raw.get("fields"))
        fold = raw.get("fold")
        if fold is not None and fold != "lower":
            raise _err()
        out["fold"] = fold
        out["skip_blank"] = _bool(raw.get("skip_blank"), True)
    elif part in ("required", "empty"):
        out["fields"] = _fields(raw.get("fields"))
    elif part == "one_of":
        out["fields"] = _fields(raw.get("fields"), at_least=2)
        mode = raw.get("mode", "at_least_one")
        if mode not in ("at_least_one", "exactly_one"):
            raise _err()
        out["mode"] = mode
    elif part == "words":
        out["field"] = _path(raw.get("field"))
        out["in"] = _words(raw.get("in"))
        out["allow_blank"] = _bool(raw.get("allow_blank"), False)
    elif part == "shape":
        out["field"] = _path(raw.get("field"))
        shape = raw.get("shape")
        if shape is not None and shape not in _SHAPES:
            raise _err()
        prefix = raw.get("prefix")
        if prefix is not None and (not isinstance(prefix, str) or not 0 < len(prefix) <= PREFIX_MAX
                                   or any(c in prefix for c in "\r\n")):
            raise _err()
        if shape is None and prefix is None:
            raise _err()
        out["shape"], out["prefix"] = shape, prefix
        out["allow_blank"] = _bool(raw.get("allow_blank"), True)
    elif part == "forbid_pair":
        via = _fieldname(raw.get("via"))
        specs = [p for p in pointers if p.get("field") == via]
        if not specs or any("to" not in p for p in specs) or len({p["to"] for p in specs}) != 1:
            raise _err()  # 宣言済みの指す欄だけ（先の種類が 1 つに決まる物）
        out["via"], out["to"] = via, specs[0]["to"]
        out["target"] = parse_condition(raw.get("target"))
    elif part == "count":
        src = raw.get("from")
        if not isinstance(src, dict):
            raise _err()
        if set(src) == {"units"} and src["units"] is True:  # 1 == True で通さない
            out["from"] = {"units": True}
        else:
            if set(src) != {"kind", "via"} or not isinstance(src.get("kind"), str) or not NAME.fullmatch(src["kind"]):
                raise _err()
            out["from"] = {"kind": src["kind"], "via": _fieldname(src["via"])}
        out["where"] = parse_condition(raw["where"]) if raw.get("where") is not None else None
        out["distinct"] = _path(raw["distinct"]) if raw.get("distinct") is not None else None
        out["min"] = _int(raw["min"]) if raw.get("min") is not None else None
        out["max"] = _int(raw["max"]) if raw.get("max") is not None else None
        if out["min"] is None and out["max"] is None:
            raise _err()
        if out["min"] is not None and out["max"] is not None and out["min"] > out["max"]:
            raise _err()
    elif part == "columns":
        # 欄の一覧を閉じる（段 4 G10）: 頭の段の欄の名前だけ（道は書けない）。
        for key in ("required", "allowed", "forbidden"):
            out[key] = (frozenset(_fields(raw[key], limit=COLUMNS_MAX, name=_fieldname))
                        if raw.get(key) is not None else None)
        if all(out[k] is None for k in ("required", "allowed", "forbidden")):
            raise _err()
        if out["forbidden"] and ((out["required"] or set()) | (out["allowed"] or set())) & out["forbidden"]:
            raise _err()
        if out["required"] and out["allowed"] is not None and not out["required"] <= out["allowed"]:
            raise _err()
    elif part == "prefixed_words":
        # 1 欄を割って、頭ごとに語を替える（段 4 G11）。語の並びが null の頭は、残りの slug の形だけ見る。
        out["field"] = _path(raw.get("field"))
        sep = raw.get("sep")
        if not isinstance(sep, str) or len(sep) != 1 or not sep.isprintable() or sep.isspace():
            raise _err()
        prefixes = raw.get("prefixes")
        if not isinstance(prefixes, dict) or not 1 <= len(prefixes) <= PREFIXES_MAX:
            raise _err()
        out["sep"], out["prefixes"] = sep, {}
        for head, words in prefixes.items():
            if not isinstance(head, str) or not 0 < len(head) <= 64 or sep in head or head != head.strip() \
                    or not head.isprintable():
                raise _err()
            out["prefixes"][head] = None if words is None else _words(words)
        out["allow_blank"] = _bool(raw.get("allow_blank"), True)
    elif part == "judged":
        q = raw.get("question")
        if not isinstance(q, str) or not _QUESTION.fullmatch(q):
            raise _err()
        out["question"] = q
        out["min"] = _int(raw["min"], low=1) if raw.get("min") is not None else 1
        how = raw.get("how")
        if how is not None and (not isinstance(how, str) or not 0 < len(how) <= 64 or "\n" in how):
            raise _err()
        out["how"] = how
    return out


def parse_checks(raw, pointers: list) -> list:
    """種類の宣言の `checks` を読む。どの値を書かれても `DeclarationError("checks")` 以外の例外を漏らさない。"""
    from .vessel import DeclarationError
    try:
        if not isinstance(raw, list) or len(raw) > CHECKS_MAX:
            raise _err()
        out, seen = [], set()
        for item in raw:
            one = _parse_one(item, pointers)
            if one["id"] in seen:
                raise _err()
            seen.add(one["id"])
            out.append(one)
        return out
    except DeclarationError as exc:
        if exc.word != "checks":
            raise _err() from None
        raise
    except Exception:  # 宣言の値で例外を漏らさない（型の違い・深い入れ子・大きすぎる数など）
        raise _err() from None


def validate_refs(kinds: dict) -> str | None:
    """種類をまたぐ宣言の点検（全部の種類を読んだあと）。`count` の `from.kind` が在り、その `via` が
    この種類を指す宣言済みの指す欄であること。合わない種類の名前を返す（合えば None）。"""
    for kind in kinds.values():
        for c in getattr(kind, "checks", []) or []:
            if c["check"] != "count" or "kind" not in c["from"]:
                continue
            other = kinds.get(c["from"]["kind"])
            if other is None:
                return kind.name
            specs = [p for p in other.pointers if p.get("field") == c["from"]["via"]]
            if not specs or any(p.get("to") != kind.name for p in specs):
                return kind.name
    return None


# ------------------------------------------------------------------ 値の見方

def _blank(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return not value
    return False


def _text(value):
    """比べる字（前後の空白を落とす）。辞書・並びは None（字として比べない）。欠け・null は ""。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return None


def match(cond: dict | None, values: dict) -> bool:
    if not cond:
        return True
    for name, (op, arg, or_blank) in cond.items():
        value = get(values, name)
        if or_blank and _blank(value):
            continue
        if op == "blank":
            if _blank(value) != arg:
                return False
            continue
        word = _text(value)
        if word is None:
            return False  # 辞書・並びには blank だけが使える
        if op == "in" and word not in arg:
            return False
        if op == "not_in" and word in arg:
            return False
    return True


def get(values, path: str):
    """欄の値。点でつないだ道は辞書だけをたどる（並びの中は読まない。途中が辞書でなければ欠け）。"""
    if "." not in path:
        return values.get(path) if isinstance(values, dict) else None
    value = values
    for step in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(step)
    return value


def _key_word(value, fold):
    if isinstance(value, str) and fold == "lower":
        value = value.lower()
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _distinct_word(value):
    """`distinct` で比べる語: 字は前後の空白を落とした字、辞書・並びは決まった書き方の JSON。"""
    word = _text(value)
    return ("t", word) if word is not None else ("j", _key_word(value, None))


def shape_ok(c: dict, value) -> bool:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return False
    if c["prefix"] is not None and not value.startswith(c["prefix"]):
        return False
    if c["shape"] is not None:
        pattern = _SHAPES[c["shape"]]
        return bool(pattern.match(value) if c["shape"] in _PREFIX_ONLY else pattern.fullmatch(value))
    return True


# ------------------------------------------------------------------ 回す

class _Result:
    def __init__(self, kind: str, c: dict):
        self.out = {"kind": kind, "id": c["id"], "check": c["check"], "level": c["level"], "note": c["note"],
                    "checked": 0, "not_when": 0, "not_inspected": 0, "unverifiable": 0, "unresolved": 0,
                    "violations": 0, "records": [], "more": 0}

    def bad(self, rec, reason, **extra):
        self.out["violations"] += 1
        if len(self.out["records"]) < SHOWN_MAX:
            self.out["records"].append({"kind": rec.kind, "record": rec.ref, "check": self.out["id"],
                                        "reason": reason, **extra})
        else:
            self.out["more"] += 1


def _targets(vessel, rec, via: str):
    """`via` の指す欄の先 `(種類, 番号)` の並び（重なりは 1 つ）と、空の要素の数。"""
    from . import vessel as V
    out, blanks = [], 0
    for spec in vessel.kinds[rec.kind].pointers:
        if spec.get("field") != via:
            continue
        got, empty = V.pointer_pieces(rec, spec)
        blanks += empty
        for target_id in got:
            pair = (spec["to"], target_id)
            if pair not in out:
                out.append(pair)
    return out, blanks


def _pointing(vessel, row, via: str):
    """`via` の指す欄で、この行が指す先 `(種類, 番号, 数える行の値)`。内側の単位の指す欄（`in_units`）は、
    単位ごとに 1 行として返す（`count` が指してくる単位を数える）。"""
    from . import vessel as V
    for spec in vessel.kinds[row.kind].pointers:
        if spec.get("field") != via:
            continue
        holders = [(u.item, V._pieces(u.item, spec)[0]) for u in row.units] if spec.get("in_units") else \
            [(row.values, V.pointer_pieces(row, spec)[0])]
        for values, got in holders:
            if spec.get("in_units"):
                got = V._in_folder(row, spec, got)
            for target_id in dict.fromkeys(got):
                yield spec["to"], target_id, values


def _good_judgments(vessel, judgments: list, c: dict) -> dict:
    """記録 → 今効いている「よい」の判断の数（問いと照らし方が合う物）。判断ごとに 1 度だけ評価する。"""
    from . import judgment as J
    counts = {}
    for j in judgments:
        target = j.get("target") or {}
        if j.get("question") != c["question"] or target.get("unit") or target.get("value"):
            continue
        if c["how"] is not None and j.get("how") != c["how"]:
            continue
        ref = target.get("record")
        if not isinstance(ref, str):
            continue
        try:
            if J.evaluate(vessel, j) != "effective" or J.conclusion_of(vessel, j)[0] != "good":
                continue
        except RegisterRefused:
            continue
        counts[ref] = counts.get(ref, 0) + 1
    return counts


def _run_one(vessel, kind, c: dict, recs: list, by_kind: dict, judgments_fn) -> dict:
    res = _Result(kind.name, c)
    live = []
    for rec in recs:
        if rec.values is None or rec.id is None:
            res.out["not_inspected"] += 1
            continue
        if not match(c["when"], rec.values):
            res.out["not_when"] += 1
            continue
        live.append(rec)
    res.out["checked"] = len(live)
    part = c["check"]
    if part == "unique":
        groups = {}
        for rec in live:
            vals = [get(rec.values, f) for f in c["fields"]]
            if c["skip_blank"] and all(_blank(v) for v in vals):
                continue
            groups.setdefault(tuple(_key_word(v, c["fold"]) for v in vals), []).append(rec)
        for members in groups.values():
            if len(members) > 1:
                for rec in members:
                    res.bad(rec, "duplicate", same=len(members))
    elif part == "required":
        for rec in live:
            missing = [f for f in c["fields"] if _blank(get(rec.values, f))]
            if missing:
                res.bad(rec, "blank", fields=missing)
    elif part == "empty":
        for rec in live:
            filled = [f for f in c["fields"] if not _blank(get(rec.values, f))]
            if filled:
                res.bad(rec, "not_blank", fields=filled)
    elif part == "one_of":
        for rec in live:
            n = sum(1 for f in c["fields"] if not _blank(get(rec.values, f)))
            if n == 0:
                res.bad(rec, "none_filled")
            elif c["mode"] == "exactly_one" and n > 1:
                res.bad(rec, "more_than_one", filled=n)
    elif part == "words":
        for rec in live:
            value = get(rec.values, c["field"])
            if _blank(value):
                if not c["allow_blank"]:
                    res.bad(rec, "blank", field=c["field"])
                continue
            word = _text(value)
            if word is None:
                res.bad(rec, "not_text", field=c["field"])
            elif word not in c["in"]:
                res.bad(rec, "word_not_allowed", field=c["field"])
    elif part == "shape":
        for rec in live:
            value = get(rec.values, c["field"])
            if _blank(value):
                if not c["allow_blank"]:
                    res.bad(rec, "blank", field=c["field"])
                continue
            if not shape_ok(c, value):
                res.bad(rec, "shape", field=c["field"], shape=c["shape"], prefix=c["prefix"])
    elif part == "forbid_pair":
        for rec in live:
            pairs, _blanks = _targets(vessel, rec, c["via"])
            hits = []
            for to, target_id in pairs:
                if ":" in target_id:
                    res.out["unverifiable"] += 1  # よその台帳の先: 違反にせず、確かめられないと数える
                    continue
                target = vessel.unique(f"{to}:{target_id}")
                if target is None or target.values is None:
                    res.out["unresolved"] += 1  # 先が無い・重なる・中を見ていない（先の無さは links が数える）
                    continue
                if match(c["target"], target.values):
                    hits.append(target.ref)
            if hits:
                res.bad(rec, "forbidden_pair", targets=hits[:5])
    elif part == "count":
        if "units" in c["from"]:
            def rows_of(rec):
                return [u.item for u in rec.units]
        else:
            index = {}
            for row in by_kind.get(c["from"]["kind"], []):
                if row.values is None or row.id is None:
                    continue
                for to, target_id, values in _pointing(vessel, row, c["from"]["via"]):
                    if to == kind.name and ":" not in target_id:
                        index.setdefault(target_id, []).append(values)

            def rows_of(rec):
                return index.get(rec.id, [])
        for rec in live:
            rows = [r for r in rows_of(rec) if match(c["where"], r)]
            if c["distinct"]:
                n = len({_distinct_word(get(r, c["distinct"])) for r in rows if not _blank(get(r, c["distinct"]))})
            else:
                n = len(rows)
            if c["min"] is not None and n < c["min"]:
                res.bad(rec, "too_few", count=n, min=c["min"])
            elif c["max"] is not None and n > c["max"]:
                res.bad(rec, "too_many", count=n, max=c["max"])
    elif part == "columns":
        for rec in live:
            present = set(rec.values) | set(rec.unread)  # 幅の足りない行の欠けたセルも、欄としては在る
            if c["required"] and not c["required"] <= present:
                res.bad(rec, "column_missing", fields=sorted(c["required"] - present))
            if c["allowed"] is not None and not present <= c["allowed"]:
                res.bad(rec, "column_not_allowed", fields=sorted(present - c["allowed"]))
            if c["forbidden"] and present & c["forbidden"]:
                res.bad(rec, "column_forbidden", fields=sorted(present & c["forbidden"]))
    elif part == "prefixed_words":
        for rec in live:
            value = get(rec.values, c["field"])
            if _blank(value):
                if not c["allow_blank"]:
                    res.bad(rec, "blank", field=c["field"])
                continue
            word = _text(value)
            if word is None:
                res.bad(rec, "not_text", field=c["field"])
                continue
            head, sep, tail = word.partition(c["sep"])
            if not sep:
                res.bad(rec, "no_prefix", field=c["field"])
            elif head not in c["prefixes"]:
                res.bad(rec, "prefix_not_allowed", field=c["field"])
            elif c["prefixes"][head] is None:
                if not _SHAPES["slug"].fullmatch(tail):
                    res.bad(rec, "shape", field=c["field"], shape="slug", prefix=head)
            elif tail not in c["prefixes"][head]:
                res.bad(rec, "word_not_allowed", field=c["field"])
    elif part == "judged":
        counts = judgments_fn(c)
        for rec in live:
            n = counts.get(rec.ref, 0)
            if n < c["min"]:
                res.bad(rec, "not_judged_good", count=n, min=c["min"])
    return res.out


def run(vessel, judgments: list | None = None) -> dict:
    """宣言の検査を全部回す。`{"total", "error", "warn", "review", "failed", "list": [...]}`。
    `error` は error の検査の違反の数。`failed` は回す途中で止まった検査の数（error の検査なら赤に数える）。"""
    by_kind = {}
    for rec in vessel.all_records:
        by_kind.setdefault(rec.kind, []).append(rec)
    cache = {}
    pool = None

    def judgments_fn(c):
        nonlocal pool
        if pool is None:
            from . import judgment as J
            pool = list(judgments or [])
            try:
                pool += J.inspections(vessel)
            except RegisterRefused:
                pass
        key = (c["question"], c["how"])
        if key not in cache:
            cache[key] = _good_judgments(vessel, pool, c)
        return cache[key]

    out = {"total": 0, "error": 0, "warn": 0, "review": 0, "failed": 0, "list": []}
    for name in sorted(vessel.kinds):
        kind = vessel.kinds[name]
        for c in getattr(kind, "checks", None) or []:
            out["total"] += 1
            try:
                one = _run_one(vessel, kind, c, by_kind.get(name, []), by_kind, judgments_fn)
            except (TypeError, ValueError, KeyError, AttributeError, IndexError, RecursionError, RegisterRefused):
                # 回す途中で止まった検査は、黙って緑にしない（error の検査なら赤に数える）。
                one = {"kind": name, "id": c["id"], "check": c["check"], "level": c["level"], "note": c["note"],
                       "failed": True, "violations": 0, "records": [], "more": 0}
                out["failed"] += 1
            out[c["level"]] += one["violations"]
            out["list"].append(one)
    return out


def red(result: dict) -> list:
    """赤にする検査（error の検査で違反があるか、回す途中で止まった物）。"""
    return [c for c in result["list"] if c["level"] == "error" and (c["violations"] or c.get("failed"))]


def view(kind) -> list:
    """`kinds` の出力: 種類ごとの検査の一覧（呼び名・部品・level・注）。"""
    return [{"id": c["id"], "check": c["check"], "level": c["level"], "note": c["note"]}
            for c in getattr(kind, "checks", None) or []]
