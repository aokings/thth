"""台帳の器 段 1「読んで、同じ対象を指す」（設計 `docs/設計_台帳の器_2026-11-13.md` §0・§5.1・§5.2・§5.4・§10 の段 1）。

元のファイルを、元の名前・元のバイトのまま読む。台帳には何も書かない（段 1 は privacy への影響なし）。

- **種類の宣言**（`.thth/kinds/<名前>.json`）と**憲法**（`.thth/constitution.json`）は JSON。決まった欄だけを受け、
  知らない欄は断る。宣言の値は道の型・欄の名前・語として読むだけで、命令として実行しない。
- **記録** = 種類と番号。番号は `<種類>:<番号>`（種類の違う 2 つは、同じ番号でも 2 つの記録）。
- **版**は全バイトの sha256。表の行は、その行の元のバイト（行の終わりの改行も含む）の sha256。
- **依る所の指紋**（`depend_fp`）は、**読んだ値**から取る（バイトでなく）。作り方は `canonical` と `fp` の注、
  設計 §5.1 の内側の単位の 3 に 1 段落で書いた。
- **内側の単位**（関東の回の行・照合台帳の行）は、元の番号 → 宣言の鍵そのまま → 鍵と行の姿 の順で指す。
  位置では指さない。行の指し先に器は何も足さない。鍵で決まらない単位を黙って 1 つにしない。
- **値**は「単位の指し先 ＋ 欄の名前 ＋ 時点」で指し、範囲・時点・親の出典を持つ。
- **指し先・依る所・及ぶ範囲は別の 3 つ**（設計 §5.1 の内側の単位の 3）。どれも**欄の 4 つ組**
  `{"value", "scope", "time", "sources"}`（`cell`）1 つから作る: 行の姿（`unit_shape`）・中身の指紋
  （`unit_content`）・行と値の依る所（`unit_depend`・`Value.depend`）。
- **観測**: 状態の欄に書いてある語は、版つきで控えるだけ。判断ではない（判断は `judgment.py`）。

網に出ない（標準の部品と、台帳の機械の部品だけ）。
"""
from __future__ import annotations

import csv
import fnmatch
import hashlib
import io
import json
import os
import re
from dataclasses import dataclass, field

from . import form
from . import yamlread
from .errors import RegisterRefused

DECL_DIR = ".thth"
KINDS_DIR = "kinds"
CONSTITUTION_FILE = "constitution.json"
IDS_FILE = "ids.json"
KIND_SCHEMA = "thth.kind/1"
CONSTITUTION_SCHEMA = "thth.constitution/1"
IDS_SCHEMA = "thth.ids/1"

FORMATS = ("csv", "json", "yaml", "thth_row", "bytes")
FILE_MAX_DEFAULT = 64 * 1024          # 1 枚の既定（今の台帳と同じ）
TABLE_MAX_DEFAULT = 50 * 1024 * 1024  # 表の既定（叩き台: 50 MB）
TABLE_ROWS_MAX = 200_000              # 表の行の既定（叩き台: 20 万行）
DECL_MAX = 256 * 1024

NAME = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
RECORD_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,159}\Z")
PLACE = re.compile(r"[A-Za-z0-9_.*?\[\]/\-]{1,200}\Z")
TEMPLATE = re.compile(r"[A-Za-z0-9_.\-]{0,40}\{id\}[A-Za-z0-9_.\-]{0,40}\Z")
MEANINGS = ("about", "supports", "contradicts", "limits", "contains", "parent", "replaces", "same", "related",
            "derived_from")
CYCLE_FREE = ("contains", "parent", "replaces")  # 輪を禁じるのはこの 3 つだけ（主張どうしの往復は止めない）
CONCLUSIONS = ("good", "bad", "pending")
HASHES = {"sha1": hashlib.sha1, "sha256": hashlib.sha256}


# ------------------------------------------------------------------ 指紋の作り方（設計 §5.1 の内側の単位の 3）

def _lf(value):
    """値の中の改行を LF にそろえる（CRLF・CR → LF）。字下げ・空白・ほかの字はそのまま。"""
    if isinstance(value, str):
        return value.replace("\r\n", "\n").replace("\r", "\n")
    if isinstance(value, list):
        return [_lf(v) for v in value]
    if isinstance(value, dict):
        return {k: _lf(v) for k, v in value.items()}
    return value


DEPTH_MAX = 64                        # 読んだ値の入れ子の深さの上限（深い入れ子は指紋の所で例外になりうる）


def value_problem(value) -> str | None:
    """読んだ値を指紋にできないわけ（`surrogate`・`too_deep`）。できれば None。再帰しないで見る。"""
    stack = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > DEPTH_MAX:
            return "too_deep"
        if isinstance(item, str):
            if any("\ud800" <= c <= "\udfff" for c in item):
                return "surrogate"
        elif isinstance(item, dict):
            stack.extend((k, depth + 1) for k in item.keys())
            stack.extend((v, depth + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, depth + 1) for v in item)
    return None


def canonical(value) -> str:
    """読んだ値の決まった書き方: 値の中の改行を LF にそろえ、JSON で、欄の名前の順に並べ、区切りの空白なし・
    字は逃がさない（`sort_keys`・`separators=(",", ":")`・`ensure_ascii=False`）。数は JSON の書き方のまま
    （296 と 296.0 は別の値）。欠けた欄は null、空の欄は ""（別の値）。"""
    return json.dumps(_lf(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fp(value) -> str:
    """依る所の指紋: `canonical(value)` の UTF-8 の sha256（16 進 64 字）。"""
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def short(digest: str) -> str:
    return digest[:16]


def bytes_version(data: bytes) -> str:
    """版: 全バイトの sha256。"""
    return hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------ 宣言の読み

class DeclarationError(Exception):
    def __init__(self, word: str):
        self.word = word
        super().__init__(word)


def _strs(value, pattern, word, *, allow_empty=False) -> list:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise DeclarationError(word)
    for item in value:
        if not isinstance(item, str) or not pattern.fullmatch(item):
            raise DeclarationError(word)
    return list(value)


def _only(obj, allowed, word):
    if not isinstance(obj, dict):
        raise DeclarationError(word)
    for key in obj:
        if key not in allowed:
            raise DeclarationError(f"unknown_key:{key}" if isinstance(key, str) and FIELD.fullmatch(key) else
                                   "unknown_key")
    return obj


def _field(value, word):
    if not isinstance(value, str) or not FIELD.fullmatch(value):
        raise DeclarationError(word)
    return value


def _path_steps(value, word):
    """道の段: 欄の名前か `*`（並びの 1 つずつ）。"""
    if not isinstance(value, list):
        raise DeclarationError(word)
    for step in value:
        if not isinstance(step, str) or not (step == "*" or FIELD.fullmatch(step)):
            raise DeclarationError(word)
    return list(value)


@dataclass
class Question:
    name: str
    outlet: str | None
    level: str                 # record か unit
    state: str | None          # 状態の欄（観測）
    depends: list | None       # 依る欄（None は全部）
    reason_kind: str | None
    reason_text: str | None

    @property
    def ident(self) -> str:
        return self.name + (f"@{self.outlet}" if self.outlet else "")

    def depend_fields(self) -> list | None:
        """状態の欄と理由の種類の欄は、宣言で外せない（設計 §5.2 の「状態の欄」）。"""
        if self.depends is None:
            return None
        fields = list(self.depends)
        for extra in (self.state, self.reason_kind):
            if extra and extra not in fields:
                fields.append(extra)
        return fields


@dataclass
class Kind:
    name: str
    places: list
    format: str
    records: str               # file か rows
    max_bytes: int
    rows_at: list
    id_rule: dict
    questions: list
    pointers: list
    units: dict | None
    path: str                  # 宣言のファイル（台帳の根からの道）
    inspects: dict | None = None  # 検収の記録を持つ表の宣言（`_parse_inspects`）


_KIND_KEYS = ("schema", "kind", "place", "format", "records", "max_bytes", "rows_at", "id", "questions", "pointers",
              "units", "inspects", "note")
_Q_KEYS = ("name", "outlet", "level", "state", "depends", "reason_kind", "reason_text", "note")
_P_KEYS = ("field", "template", "meaning", "meaning_from", "to", "split", "recorded_version", "note")
_RV_KEYS = ("field", "of", "hash")
_U_KEYS = ("groups", "items", "id", "key", "time", "scope", "sources", "not_values", "note")
_I_KEYS = ("follow", "question", "by", "content", "required", "note")


def parse_kind(data, path: str) -> Kind:
    """宣言を読む。どの欄にどの型を書かれても、例外を漏らさず `DeclarationError`（宣言の場所と語）にする。"""
    try:
        return _parse_kind(data, path)
    except DeclarationError:
        raise
    except (TypeError, ValueError, AttributeError, KeyError, IndexError, re.error):
        raise DeclarationError("kind_shape") from None


def _parse_inspects(raw) -> dict:
    """検収の記録を持つ表の宣言（こぴちゃの中央の判断）: 行から `follow` の指す欄を順にたどった先の記録へ、
    `content`（先の欄 ← この行の欄）を「確かめた中身そのもの」として持つ検収の記録にする。"""
    _only(raw, _I_KEYS, "inspects")
    follow = _strs(raw.get("follow"), FIELD, "inspects")
    content = raw.get("content")
    if not isinstance(content, dict) or not content:
        raise DeclarationError("inspects")
    out = {"follow": follow, "question": _name(raw.get("question"), "inspects"),
           "by": _name(raw.get("by"), "inspects"),
           "content": {_field(k, "inspects"): _field(v, "inspects") for k, v in content.items()},
           "required": _strs(raw["required"], FIELD, "inspects") if raw.get("required") is not None else None}
    if out["required"] is not None and not set(out["required"]) <= set(out["content"]):
        raise DeclarationError("inspects")
    return out


def _parse_kind(data, path: str) -> Kind:
    _only(data, _KIND_KEYS, "kind_shape")
    if data.get("schema") != KIND_SCHEMA:
        raise DeclarationError("schema")
    name = data.get("kind")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise DeclarationError("kind_name")
    places = data.get("place")
    if isinstance(places, str):
        places = [places]
    places = _strs(places, PLACE, "place")
    for one in places:
        if one.startswith("/") or ".." in one.split("/") or one.startswith(DECL_DIR):
            raise DeclarationError("place")
    fmt = data.get("format")
    if fmt not in FORMATS:
        raise DeclarationError("format")
    records = data.get("records", "rows" if fmt == "csv" else "file")
    if records not in ("file", "rows") or (fmt == "csv" and records != "rows") or (
            fmt in ("thth_row", "bytes", "yaml") and records != "file"):
        raise DeclarationError("records")
    max_bytes = data.get("max_bytes", TABLE_MAX_DEFAULT if records == "rows" else FILE_MAX_DEFAULT)
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not 0 < max_bytes <= TABLE_MAX_DEFAULT:
        raise DeclarationError("max_bytes")
    rows_at = _path_steps(data.get("rows_at", []), "rows_at")
    id_rule = _only(data.get("id", {"name": True} if records == "file" else {}), ("name", "field", "key"), "id")
    if records == "rows" and not (("field" in id_rule) ^ ("key" in id_rule)):
        raise DeclarationError("id")
    if records == "file" and not (id_rule.get("name") is True) ^ ("field" in id_rule):
        raise DeclarationError("id")
    if "field" in id_rule:
        _field(id_rule["field"], "id")
    if "key" in id_rule:
        _strs(id_rule["key"], FIELD, "id")
    questions = []
    seen = set()
    for raw in data.get("questions", []) if isinstance(data.get("questions", []), list) else [None]:
        _only(raw, _Q_KEYS, "questions")
        q = Question(name=_name(raw.get("name"), "questions"),
                     outlet=_name(raw["outlet"], "questions") if raw.get("outlet") is not None else None,
                     level=raw.get("level", "record"),
                     state=_field(raw["state"], "questions") if raw.get("state") is not None else None,
                     depends=_strs(raw["depends"], FIELD, "questions", allow_empty=True) if raw.get("depends") is not None else None,
                     reason_kind=_field(raw["reason_kind"], "questions") if raw.get("reason_kind") else None,
                     reason_text=_field(raw["reason_text"], "questions") if raw.get("reason_text") else None)
        if q.level not in ("record", "unit") or q.ident in seen:
            raise DeclarationError("questions")
        seen.add(q.ident)
        questions.append(q)
    pointers = []
    for raw in data.get("pointers", []) if isinstance(data.get("pointers", []), list) else [None]:
        _only(raw, _P_KEYS, "pointers")
        if ("field" in raw) == ("template" in raw):
            raise DeclarationError("pointers")
        if "field" in raw:
            _field(raw["field"], "pointers")
        if "template" in raw and (not isinstance(raw["template"], str) or not TEMPLATE.fullmatch(raw["template"])):
            raise DeclarationError("pointers")
        if raw.get("meaning") not in MEANINGS:
            raise DeclarationError("pointers")
        if "meaning_from" in raw:
            _field(raw["meaning_from"], "pointers")
        _name(raw.get("to"), "pointers")
        if "split" in raw and (not isinstance(raw["split"], str) or not re.fullmatch(r"[;|,]", raw["split"])):
            raise DeclarationError("pointers")
        if "recorded_version" in raw:
            rv = _only(raw["recorded_version"], _RV_KEYS, "pointers")
            _field(rv.get("field"), "pointers")
            _strs(rv.get("of"), FIELD, "pointers")
            m = re.fullmatch(r"(sha1|sha256):([0-9]{1,2})", rv["hash"]) if isinstance(rv.get("hash"), str) else None
            if not m or not 1 <= int(m.group(2)) <= 64:
                raise DeclarationError("pointers")
        pointers.append(dict(raw))
    units = None
    if data.get("units") is not None:
        units = _parse_units(data["units"])
    inspects = None
    if data.get("inspects") is not None:
        inspects = _parse_inspects(data["inspects"])
        if not all(any(p.get("field") == f for p in pointers) for f in inspects["follow"][:1]):
            raise DeclarationError("inspects")
    return Kind(name=name, places=places, format=fmt, records=records, max_bytes=max_bytes, rows_at=rows_at,
                id_rule=id_rule, questions=questions, pointers=pointers, units=units, path=path, inspects=inspects)


def _name(value, word):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise DeclarationError(word)
    return value


def _parse_units(raw) -> dict:
    _only(raw, _U_KEYS, "units")
    out = {"groups": _path_steps(raw.get("groups", []), "units"),
           "items": _path_steps(raw.get("items"), "units") if raw.get("items") else None,
           "id": _field(raw["id"], "units") if raw.get("id") else None,
           "key": [], "time": {}, "scope": {}, "sources": None,
           "not_values": _strs(raw.get("not_values", []), FIELD, "units", allow_empty=True)}
    if not out["items"]:
        raise DeclarationError("units")
    for item in raw.get("key", []) if isinstance(raw.get("key", []), list) else [None]:
        if not isinstance(item, str) or not re.fullmatch(r"(group\.)?[A-Za-z_][A-Za-z0-9_]{0,63}", item):
            raise DeclarationError("units")
        out["key"].append(item)
    if not out["id"] and not out["key"]:
        raise DeclarationError("units")
    time = _only(raw.get("time", {}), ("group", "per_field"), "units")
    out["time"] = {k: _field(v, "units") for k, v in time.items()}
    scope = _only(raw.get("scope", {}), ("key_fields", "separate", "fields"), "units")
    out["scope"] = {"key_fields": _strs(scope.get("key_fields", []), FIELD, "units", allow_empty=True),
                    "separate": _strs(scope.get("separate", []), FIELD, "units", allow_empty=True),
                    "fields": {}}
    fields = scope.get("fields", {})
    if not isinstance(fields, dict):
        raise DeclarationError("units")
    for key, value in fields.items():
        out["scope"]["fields"][_field(key, "units")] = _field(value, "units")
    if raw.get("sources") is not None:
        src = _only(raw["sources"], ("list", "field", "split", "rest", "depends"), "units")
        out["sources"] = {"list": _field(src.get("list"), "units"), "field": _field(src.get("field"), "units"),
                          "split": src.get("split", "/,"), "rest": src.get("rest"),
                          "depends": _strs(src["depends"], FIELD, "units") if src.get("depends") else None}
        if not isinstance(out["sources"]["split"], str) or not re.fullmatch(r"[/,;|]{1,4}", out["sources"]["split"]):
            raise DeclarationError("units")
        if out["sources"]["rest"] is not None:
            _field(out["sources"]["rest"], "units")
    return out


def parse_constitution(data) -> dict:
    _only(data, ("schema", "words", "note"), "constitution")
    if data.get("schema") != CONSTITUTION_SCHEMA:
        raise DeclarationError("schema")
    words = data.get("words", {})
    if not isinstance(words, dict):
        raise DeclarationError("constitution")
    out = {}
    for question, table in words.items():
        if not isinstance(question, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}(@[a-z][a-z0-9_]{0,31})?",
                                                             question):
            raise DeclarationError("constitution")
        if not isinstance(table, dict):
            raise DeclarationError("constitution")
        out[question] = {}
        for word, meaning in table.items():
            if not isinstance(word, str) or not 0 < len(word) <= 64 or "\n" in word:
                raise DeclarationError("constitution")
            _only(meaning, ("conclusion", "reason"), "constitution")
            if meaning.get("conclusion") not in CONCLUSIONS:
                raise DeclarationError("constitution")
            if meaning.get("reason") is not None:
                _name(meaning["reason"], "constitution")
            out[question][word] = {"conclusion": meaning["conclusion"], "reason": meaning.get("reason")}
    return out


def _read_json_file(path: str, limit: int):
    if os.path.islink(path):
        raise DeclarationError("symlink")
    with open(path, "rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise DeclarationError("too_large")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise DeclarationError("json") from None
    problem = value_problem(value)
    if problem:
        raise DeclarationError(problem)
    return value


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise DeclarationError("key_duplicate")
        out[key] = value
    return out


def has_declarations(root: str) -> bool:
    return os.path.isdir(os.path.join(root, DECL_DIR, KINDS_DIR)) and not os.path.islink(os.path.join(root, DECL_DIR))


def load_declarations(root: str, base: str | None = None) -> dict:
    """`{"kinds": {名前: Kind}, "constitution": {...}, "ids": {...}}`。崩れていれば `register_kind_invalid`。
    `base` は宣言の置き場（既定は台帳の `.thth/`。影の写しで、元の repo に書かずに宣言を当てるときに渡す）。"""
    base = base or os.path.join(root, DECL_DIR)
    kinds_dir = os.path.join(base, KINDS_DIR)
    if os.path.islink(base) or os.path.islink(kinds_dir):
        raise RegisterRefused("register_kind_invalid", "symlink")
    kinds = {}
    for name in sorted(os.listdir(kinds_dir)) if os.path.isdir(kinds_dir) else []:
        if not name.endswith(".json") or name.startswith("."):
            continue
        rel = f"{DECL_DIR}/{KINDS_DIR}/{name}"
        try:
            kind = parse_kind(_read_json_file(os.path.join(kinds_dir, name), DECL_MAX), rel)
        except DeclarationError as exc:
            raise RegisterRefused("register_kind_invalid", f"{name[:-5][:30]}: {exc.word}"[:80],
                                  declaration=rel, word=exc.word) from None
        except OSError:
            raise RegisterRefused("register_kind_invalid", "unreadable", declaration=rel) from None
        if kind.name in kinds:
            raise RegisterRefused("register_kind_invalid", "kind_duplicate", declaration=rel)
        kinds[kind.name] = kind
    constitution = {}
    cpath = os.path.join(base, CONSTITUTION_FILE)
    if os.path.exists(cpath):
        try:
            constitution = parse_constitution(_read_json_file(cpath, DECL_MAX))
        except DeclarationError as exc:
            raise RegisterRefused("register_kind_invalid", f"constitution: {exc.word}"[:80],
                                  declaration=f"{DECL_DIR}/{CONSTITUTION_FILE}", word=exc.word) from None
    ids = {}
    ipath = os.path.join(base, IDS_FILE)
    if os.path.exists(ipath):
        try:
            raw = _read_json_file(ipath, TABLE_MAX_DEFAULT)
            _only(raw, ("schema", "ids"), "ids")
            if raw.get("schema") != IDS_SCHEMA or not isinstance(raw.get("ids"), dict):
                raise DeclarationError("ids")
            for key, value in raw["ids"].items():
                if not isinstance(key, str) or not isinstance(value, str) or ":" not in key:
                    raise DeclarationError("ids")
            if len(set(raw["ids"].values())) != len(raw["ids"]):
                raise DeclarationError("ids_path_duplicate")  # 1 つの置き場を 2 つの番号に当てない
            ids = dict(raw["ids"])
        except DeclarationError as exc:
            raise RegisterRefused("register_kind_invalid", f"ids: {exc.word}"[:80],
                                  declaration=f"{DECL_DIR}/{IDS_FILE}", word=exc.word) from None
    return {"kinds": kinds, "constitution": constitution, "ids": ids}


# ------------------------------------------------------------------ 記録と内側の単位

@dataclass
class Unit:
    pointer: str               # 単位の指し先（`k:` 鍵 / `kc:` 鍵と行の姿 / `i:` 元の番号）
    key: dict
    item: dict
    group: dict
    ambiguous: bool            # 鍵で決まらない（鍵と行の姿で指した）か、同じ番号で中身が違う
    same_content: int = 1      # 中身まで同じ単位の数（区別しない。判断はどれにも当たる）
    content: str = ""          # 中身の指紋（`unit_content`。全部の欄の 4 つ組。親の注など依らない物は入らない）


@dataclass
class Record:
    kind: str
    id: str | None
    path: str
    version: str               # 版（全バイト・表の行はその行のバイト）
    values: dict | None        # 読んだ値（中を見ていなければ None）
    inspected: bool
    problems: list = field(default_factory=list)
    units: list = field(default_factory=list)
    unread: tuple = ()         # 表の幅が足りない行の、読めなかったセル（欄の名前）。値として読まない（null と別）

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.id}"


def _walk(root: str):
    """台帳の中のファイル（隠しの名前と symlink は辿らない）。台帳の根からの道を `/` で。"""
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not os.path.islink(os.path.join(directory, d)))
        for name in sorted(files):
            if name.startswith("."):
                continue
            full = os.path.join(directory, name)
            yield os.path.relpath(full, root).replace(os.sep, "/"), full


def _matches(kind: Kind, rel: str) -> bool:
    return any(fnmatch.fnmatchcase(rel, pattern) for pattern in kind.places)


def split_csv_rows(text: str) -> list:
    """表を、元の字のまま行に分ける（引用の中の改行は行を切らない。CRLF・LF・CR が混ざってもよい）。
    行の終わりの改行も、その行の字に含める。"""
    rows, start, quoted, i, n = [], 0, False, 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            quoted = not quoted
        elif not quoted and ch in "\r\n":
            end = i + 2 if ch == "\r" and i + 1 < n and text[i + 1] == "\n" else i + 1
            rows.append(text[start:end])
            start = i = end
            continue
        i += 1
    if start < n:
        rows.append(text[start:])
    return rows


def _parse_csv_line(raw: str) -> list | None:
    """1 行を読む。読めなければ None（大きすぎるセルなど。csv の部品の例外を漏らさない）。"""
    try:
        return next(csv.reader(io.StringIO(raw, newline=""), strict=False), [])
    except csv.Error:
        return None


def _get(obj, steps):
    """道の段をたどる。`*` は並びの 1 つずつ。返すのは `(見つけた物, 道)` の並び。"""
    found = [(obj, [])]
    for step in steps:
        nxt = []
        for value, trail in found:
            if step == "*":
                if isinstance(value, list):
                    nxt += [(v, trail + [i]) for i, v in enumerate(value)]
            elif isinstance(value, dict) and step in value:
                nxt.append((value[step], trail + [step]))
        found = nxt
    return found


def _id_ok(value) -> str | None:
    if isinstance(value, (int,)) and not isinstance(value, bool):
        value = str(value)
    return value if isinstance(value, str) and RECORD_ID.fullmatch(value) else None


def _decode(data: bytes):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text[1:] if text.startswith("﻿") else text


def read_kind(root: str, kind: Kind, ids_table: dict) -> list:
    """1 つの種類の記録を読む。"""
    out = []
    reverse = {path: ref for ref, path in ids_table.items()}
    for rel, full in _walk(root):
        if rel.startswith(DECL_DIR + "/") or not _matches(kind, rel):
            continue
        out.extend(_read_file(rel, full, kind, reverse))
    _mark_duplicates(out)
    return out


def _read_file(rel: str, full: str, kind: Kind, reverse: dict) -> list:
    if os.path.islink(full):
        return [Record(kind.name, None, rel, "", None, False, ["symlink"])]
    try:
        with open(full, "rb") as f:
            data = f.read(kind.max_bytes + 1)
    except OSError:
        return [Record(kind.name, None, rel, "", None, False, ["unreadable"])]
    if len(data) > kind.max_bytes:
        return [Record(kind.name, None, rel, "", None, False, [f"too_large:{kind.max_bytes}"])]
    version = bytes_version(data)
    stem = os.path.basename(rel).rsplit(".", 1)[0]
    name_id = reverse.get(rel, "").split(":", 1)[-1] if rel in reverse else stem
    if kind.format == "bytes":
        return [Record(kind.name, _id_ok(name_id), rel, version, None, False)]
    text = _decode(data)
    if text is None:
        return [Record(kind.name, _id_ok(name_id) if kind.id_rule.get("name") else None, rel, version, None,
                       False, ["not_utf8"])]
    if kind.format == "csv":
        return _read_csv(rel, text, kind)
    if kind.format == "thth_row":
        row = form.parse(rel, text)
        if not form.is_row(row):
            return []
        values = dict(row.head, body=row.body)
        rec = Record(kind.name, row.id, rel, version, values, True, list(row.problems))
        rec.thth_row = row  # 今の台帳の 1 枚（依る所 = 今の指紋の取り方）
        return [rec]
    if kind.format == "yaml":
        value, problem, inspected = yamlread.load(text)
        rid = _id_ok(name_id) if kind.id_rule.get("name") else None
        if problem:
            return [Record(kind.name, rid, rel, version, None, False, [f"yaml:{problem}"])]
        if not inspected:
            return [Record(kind.name, rid, rel, version, None, False)]
        if "field" in kind.id_rule:
            rid = _id_ok(value.get(kind.id_rule["field"])) if isinstance(value, dict) else None
        rec = Record(kind.name, rid, rel, version, value if isinstance(value, dict) else {"_": value}, True)
        if rid is None:
            rec.problems.append("id_form")
        _read_units(rec, kind)
        return [rec]
    # json
    try:
        value = json.loads(text, object_pairs_hook=_json_pairs)
    except _DuplicateKey:
        return [Record(kind.name, _id_ok(name_id) if kind.id_rule.get("name") else None, rel, version, None, False,
                       ["json_key_duplicate"])]
    except (ValueError, RecursionError):
        return [Record(kind.name, _id_ok(name_id) if kind.id_rule.get("name") else None, rel, version, None, False,
                       ["json"])]
    problem = value_problem(value)
    if problem:
        return [Record(kind.name, _id_ok(name_id) if kind.id_rule.get("name") else None, rel, version, None, False,
                       [problem])]
    if kind.records == "file":
        rid = _id_ok(name_id) if kind.id_rule.get("name") else (
            _id_ok(value.get(kind.id_rule["field"])) if isinstance(value, dict) else None)
        rec = Record(kind.name, rid, rel, version, value if isinstance(value, dict) else {"_": value}, True)
        if rid is None:
            rec.problems.append("id_form")
        _read_units(rec, kind)
        return [rec]
    out = []
    for item, _trail in _get(value, kind.rows_at + ["*"]):
        if not isinstance(item, dict):
            out.append(Record(kind.name, None, rel, version, None, False, ["row_shape"]))
            continue
        if "field" in kind.id_rule:
            rid = _id_ok(item.get(kind.id_rule["field"]))
        else:
            rid = "k-" + short(fp([item.get(k) for k in kind.id_rule["key"]]))
        # 並びの中の 1 つは、ほかの記録とバイトを分け合うので、版は読んだ値の指紋（注: §5.1 の全バイトは枚の版）。
        rec = Record(kind.name, rid, rel, "v:" + fp(item), item, True)
        if rid is None:
            rec.problems.append("id_form")
        _read_units(rec, kind)
        out.append(rec)
    return out


class _DuplicateKey(ValueError):
    pass


def _json_pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateKey(key)
        out[key] = value
    return out


def _read_csv(rel: str, text: str, kind: Kind) -> list:
    lines = split_csv_rows(text)
    if not lines:
        return []
    header = _parse_csv_line(lines[0])
    out = []
    if len(lines) - 1 > TABLE_ROWS_MAX:
        return [Record(kind.name, None, rel, "", None, False, ["too_many_rows"])]
    if header is None or len(set(header)) != len(header):
        # 見出しが読めない・同じ名前の列がある: 黙って後ろの列に畳まず、表の全部の行を読まない。
        word = "csv" if header is None else "csv_header_duplicate"
        return [Record(kind.name, None, rel, bytes_version(raw.encode("utf-8")), None, False, [word])
                for raw in lines[1:] if raw.strip("\r\n")] or [Record(kind.name, None, rel, "", None, False, [word])]
    for raw in lines[1:]:
        if not raw.strip("\r\n"):
            continue
        cells = _parse_csv_line(raw)
        if cells is None:
            out.append(Record(kind.name, None, rel, bytes_version(raw.encode("utf-8")), None, False, ["csv"]))
            continue
        # 幅の足りない行の欠けたセルは値として読まない（読んだ null と取り違えない。依る所でも区別する）。
        values = {name: cells[i] for i, name in enumerate(header) if i < len(cells)}
        unread = tuple(name for i, name in enumerate(header) if i >= len(cells))
        problems = [] if len(cells) == len(header) else ["row_width"]
        if "field" in kind.id_rule:
            rid = _id_ok(values.get(kind.id_rule["field"]))
        else:
            rid = "k-" + short(fp([values.get(k) for k in kind.id_rule["key"]]))
        rec = Record(kind.name, rid, rel, bytes_version(raw.encode("utf-8")), values, True, problems, unread=unread)
        if rid is None:
            rec.problems.append("id_form")
        out.append(rec)
    return out


def _mark_duplicates(records: list) -> None:
    """同じ番号の記録は、黙って 1 つにしない（両方に印）。"""
    seen = {}
    for rec in records:
        if rec.id is None:
            continue
        seen.setdefault(rec.id, []).append(rec)
    for same in seen.values():
        if len(same) > 1:
            for rec in same:
                rec.problems.append("id_duplicate")


def _key_of(units_decl: dict, group: dict, item: dict) -> dict:
    """単位の鍵: **宣言の `key` の名前だけ**で作る（器は何も足さない。親の年度が入るのは、宣言が `group.year`
    と書いたときだけ。時点は値の指し先に入る）。"""
    key = {}
    for name in units_decl["key"]:
        if name.startswith("group."):
            key[name] = group.get(name[6:]) if isinstance(group, dict) else None
        else:
            key[name] = item.get(name)
    return key


def unit_shape(decl: dict, unit: Unit) -> str:
    """行の姿（鍵で決まらない行を見分ける指紋）: 行の全部の欄の `{"value", "time"}`（4 つ組から範囲と親の出典を
    除いた物）。親の出典だけが変わっても姿は変わらない。"""
    parts = {}
    for name in unit.item:
        c = cell(decl, unit, name)
        parts[name] = {"value": c["value"], "time": c["time"]}
    return fp(parts)


def unit_content(decl: dict, unit: Unit) -> str:
    """中身の指紋: 行の全部の欄の 4 つ組をまとめた物（同じ指し先の単位を比べる物はこれ 1 つ）。"""
    return fp({name: cell(decl, unit, name) for name in unit.item})


def _read_units(rec: Record, kind: Kind) -> None:
    decl = kind.units
    if decl is None or rec.values is None:
        return
    raw = []
    for group, _g in _get(rec.values, decl["groups"]):
        if not isinstance(group, dict) and "unit_not_object" not in rec.problems:
            rec.problems.append("unit_not_object")  # まとまりの並びの中の物でない要素を黙って落とさない
        if decl.get("sources") and isinstance(group, dict) and group.get(decl["sources"]["list"]) is not None \
                and not isinstance(group.get(decl["sources"]["list"]), list):
            # 出典の欄が並びでない: 「出典なし」と取り違えない（値はその読んだ値そのものに依る。`sources_for`）。
            if "sources_unreadable" not in rec.problems:
                rec.problems.append("sources_unreadable")
        for item, _i in _get(group, decl["items"]):
            if isinstance(item, dict):
                raw.append((group if isinstance(group, dict) else {}, item))
            elif "unit_not_object" not in rec.problems:
                rec.problems.append("unit_not_object")  # 行の並びの中の物でない要素を黙って落とさない
    by_key = {}
    for group, item in raw:
        if decl["id"] and _id_ok(item.get(decl["id"])):
            # 番号は字にそろえて比べる（数の 1 と字の "1" は同じ指し先 `i:1` なので、同じ組で比べて重なりを出す）。
            pointer_key = ("i", _id_ok(item.get(decl["id"])))
        else:
            pointer_key = ("k", canonical(_key_of(decl, group, item)))
        by_key.setdefault(pointer_key, []).append((group, item))
    for (how, keytext), members in by_key.items():
        bare = [Unit("", {}, item, group, False) for group, item in members]
        contents = [unit_content(decl, u) for u in bare]
        for (group, item), plain, content in zip(members, bare, contents):
            key = {"id": keytext} if how == "i" else _key_of(decl, group, item)
            if how == "i":
                # 同じ番号でも中身の指紋まで同じなら区別しない。違えば番号の重なり。
                pointer = "i:" + str(keytext)
                ambiguous = len(set(contents)) > 1
            elif len(members) == 1 or len(set(contents)) == 1:
                # 同じ鍵の行が 1 つ、または中身まで同じ行だけ（区別しない。設計 §5.1）: 鍵で指す。
                pointer, ambiguous = "k:" + short(fp(key)), False
            else:
                # 鍵で決まらない: 鍵と行の姿で指す（姿は読んだ値と値の時点。親の出典は入らない）。
                pointer, ambiguous = "kc:" + short(fp(key)) + "." + short(unit_shape(decl, plain)), True
            same = contents.count(content)
            rec.units.append(Unit(pointer, key, item, group, ambiguous, same, content))
    if any(u.pointer.startswith("i:") and u.ambiguous for u in rec.units):
        rec.problems.append("unit_id_duplicate")
    by_pointer = {}
    for unit in rec.units:
        if unit.pointer.startswith("kc:"):
            by_pointer.setdefault(unit.pointer, set()).add(unit.content)
    if any(len(contents) > 1 for contents in by_pointer.values()):
        # 姿は同じで中身が違う（親の出典だけが違う）: 同じ指し先の別々の行。`unit_at` は断る。健康に出す。
        rec.problems.append("unit_parent_differs")


def units_by_pointer(rec: Record) -> dict:
    """指し先 → 単位。同じ指し先に中身の違う単位が 2 つ以上ある指し先（番号の重なり）は入れない（`unit_at` で断る）。"""
    out = {}
    for pointer in {u.pointer for u in rec.units}:
        try:
            out[pointer] = unit_at(rec, pointer)
        except RegisterRefused:
            continue
    return out


def unit_at(rec: Record, pointer: str) -> Unit | None:
    """指し先の単位。無ければ None。同じ指し先に中身の違う単位が 2 つ以上あれば（同じ番号の別々の行）、
    先頭を黙って選ばずに断る（`register_unit_ambiguous`）。中身まで同じ単位は区別しない（どれも同じ）。"""
    same = [u for u in rec.units if u.pointer == pointer]
    if not same:
        return None
    if len({u.content for u in same}) > 1:
        raise RegisterRefused("register_unit_ambiguous", f"{len(same)} units", candidates=[pointer], record=rec.ref)
    return same[0]


def find_units(rec: Record, key: dict) -> Unit:
    """鍵で単位を引く。2 つ以上に当たれば黙って 1 つを選ばずに断る（`register_unit_ambiguous`）。"""
    want = {k: v for k, v in key.items()}
    hits = [u for u in rec.units
            if all(str(u.key.get(k)) == str(v) or str(u.item.get(k)) == str(v) for k, v in want.items())]
    distinct = sorted({u.pointer for u in hits})
    if not distinct:
        raise RegisterRefused("register_record_missing", "unit")
    if len(distinct) > 1:
        raise RegisterRefused("register_unit_ambiguous", f"{len(distinct)} units",
                              candidates=distinct, record=rec.ref)
    return unit_at(rec, distinct[0])  # 同じ指し先に中身の違う単位があれば断る（黙って 1 つに畳まない）


# ------------------------------------------------------------------ 値（範囲・時点・親の出典）

def _source_fields(spec: dict, source: dict) -> set:
    raw = source.get(spec["field"])
    if not isinstance(raw, str):
        return set()
    return {part.strip() for part in re.split("[" + re.escape(spec["split"]) + "]", raw) if part.strip()}


def sources_for(decl: dict, group: dict, name: str) -> list:
    """値の親の出典（読み方は設計 §5.1・期待する答え 試験 1 (c) の叩き台）:
    欄の名前を名指す出典があればそれ。無ければ `rest` の語（関東は `rounds`）を書いた出典。どれにも当たらなければ、
    そのまとまりの出典の全部（安全な側）。"""
    spec = decl.get("sources")
    if not spec:
        return []
    listing = group.get(spec["list"]) if isinstance(group, dict) else None
    if listing is None:
        return []
    if not isinstance(listing, list):
        # 出典の欄を並びとして読めない（記録の問題 `sources_unreadable`）: どの値も、読んだ値そのものに依る。
        return [["unreadable", listing]]
    sources = [s for s in listing if isinstance(s, dict)]
    # 辞書でない要素（手で書いた字など）は、どの欄の出典か読めない: 黙って落とさず、どの値も依る（安全な側）。
    loose = [s for s in listing if not isinstance(s, dict)]
    named = [s for s in sources if name in _source_fields(spec, s)]
    if named:
        return named + loose
    if spec["rest"]:
        rest = [s for s in sources if spec["rest"] in _source_fields(spec, s)]
        if rest:
            return rest + loose
    return sources + loose


def source_fp(decl: dict, source) -> str:
    spec = decl["sources"]
    if not isinstance(source, dict):
        return fp(source)  # 辞書でない出典は、読んだ値そのもの
    if spec["depends"]:
        return fp({k: source.get(k) for k in spec["depends"]})
    return fp(source)


def is_value_field(decl: dict, name: str) -> bool:
    """宣言から決まる: `not_values` に無く、値ごとの時点の表の名前でもない欄が値の欄。"""
    return name not in decl["not_values"] and name != decl["time"].get("per_field")


def value_fields(decl: dict, unit: Unit) -> list:
    return [k for k in unit.item if is_value_field(decl, k)]


def value_time(decl: dict, unit: Unit, name: str):
    table = decl["time"].get("per_field")
    if table and isinstance(unit.item.get(table), dict) and name in unit.item[table]:
        return unit.item[table][name]
    if decl["time"].get("group"):
        return unit.group.get(decl["time"]["group"]) if isinstance(unit.group, dict) else None
    return None


def _label_word(value) -> str:
    """札の語: 観測と同じ直し方で、前後の空白を落とす（尋ねる側も前後の空白を落として比べる。比べる両側を同じ形に）。"""
    word = word_of(value)
    return "" if word is None else word.strip()


def scope_of(decl: dict, unit: Unit, name: str):
    """`(範囲の種類, 範囲の物, 札)`。設計 §5.1「範囲の形」。

    - 範囲が行の鍵と同じ（`scope.separate` に無い）: 種類 `key`、物 `{"key": {軸: 読んだ値}}`（軸は
      `scope.key_fields`。無ければ `{"key": {}}`）、札は軸を名前の順に `軸=語` とコンマでつないだ字（軸が無ければ `key`）。
    - 範囲を別に持ち、範囲の欄の読んだ値が null・空・空白だけでない: 種類 `separate`、物 `{"field": {欄: 値}}`、
      札 `欄=語`。
    - それ以外（範囲の欄が無い・空・空白だけ）: 未宣言（物も札も None）。札が "" になる道は無い。
    指紋には札でなく物を入れる（札は区切りの字を隣の欄へ動かすと同じ字になりうる）。"""
    scope = decl["scope"]
    if name in scope["separate"]:
        holder = scope["fields"].get(name)
        raw = unit.item.get(holder) if holder else None
        if _blank(raw):
            return "separate", None, None
        return "separate", {"field": {holder: raw}}, f"{holder}={_label_word(raw)}"
    axes = sorted(scope["key_fields"])
    if any(_blank(unit.item.get(k)) for k in axes):
        # 宣言した範囲の軸の値が欠け・null・空・空白だけ: 範囲は決まっていない（未宣言）。
        return "key", None, None
    form = {"key": {k: unit.item.get(k) for k in axes}}
    label = ",".join(f"{k}={_label_word(unit.item.get(k))}" for k in axes) or "key"
    return "key", form, label


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def cell(decl: dict, unit: Unit, name: str) -> dict:
    """欄の 4 つ組 `{"value", "scope", "time", "sources"}`。値でない欄は `value` だけ（`scope`・`time` は None、
    `sources` は []。親の年度や出典を付けない）。"""
    value = unit.item.get(name)
    if name not in unit.item or not is_value_field(decl, name):
        # 値でない欄と、行に無い欄（欠け）は value だけ（欠けは null）。親の年度や出典を付けない。
        return {"value": value, "scope": None, "time": None, "sources": []}
    _kind, form, _label = scope_of(decl, unit, name)
    srcs = [source_fp(decl, s) for s in sources_for(decl, unit.group, name)] if decl.get("sources") else []
    return {"value": value, "scope": form, "time": value_time(decl, unit, name), "sources": sorted(srcs)}


@dataclass
class Value:
    record: str
    unit: str
    name: str
    value: object
    time: object
    scope_kind: str
    scope: str | None          # 範囲の札（人に見せ、尋ねる側と比べる）。None は未宣言
    sources: list
    scope_form: dict | None = None  # 範囲の物（指紋に入る）。None は未宣言

    @property
    def pointer(self) -> str:
        return f"{self.unit}/{self.name}@{self.time if self.time is not None else '-'}"

    def depend(self, said=None) -> str:
        """値への判断の依る所: その値の 4 つ組 1 つ。範囲は、範囲の物があればそれ、無くて人が書いた範囲
        （`said`）があれば `{"said": said}`、どちらも無ければ None。"""
        if self.scope_form is not None:
            scope = self.scope_form
        elif said is not None:
            scope = {"said": said}
        else:
            scope = None
        return fp({"value": self.value, "scope": scope, "time": self.time, "sources": sorted(self.sources)})


def values_of(rec: Record, kind: Kind, unit: Unit) -> list:
    decl = kind.units
    out = []
    for name in value_fields(decl, unit):
        scope_kind, form, label = scope_of(decl, unit, name)
        c = cell(decl, unit, name)
        out.append(Value(rec.ref, unit.pointer, name, c["value"], c["time"], scope_kind, label, c["sources"], form))
    return out


def unit_fields(kind: Kind, unit: Unit, question: Question | None = None) -> list:
    """単位（行）への判断が依る欄。行への判断は、この欄の値にだけ当たる（`judgment.answer_value`）。"""
    decl = kind.units
    fields = question.depend_fields() if question is not None else None
    if fields is None:
        fields = [k for k in value_fields(decl, unit) if k not in decl["scope"]["separate"]]
        for extra in ((question.state, question.reason_kind) if question is not None else ()):
            if extra and extra not in fields:
                fields.append(extra)
    return fields


def unit_depend(kind: Kind, unit: Unit, question: Question | None = None) -> str:
    """単位（行）への判断の依る所: 依る欄（`unit_fields`）それぞれの 4 つ組の束（範囲も入る。行の性別が変われば
    古い判断になる）。設計 §5.1 の内側の単位の 3。"""
    decl = kind.units
    return fp({name: cell(decl, unit, name) for name in unit_fields(kind, unit, question)})


# ------------------------------------------------------------------ 依る所・観測

def record_depend(kind: Kind, rec: Record, question: Question | None) -> str:
    """記録への判断の依る所の指紋。依る欄を宣言していない問いは全バイト（版）に依る（安全な側）。
    今の台帳の 1 枚（thth_row）は、今の指紋の取り方（THTH が書く欄を除いた全文）を依る所にする（設計 §6）。"""
    if hasattr(rec, "thth_row"):
        return "row:" + form.fingerprint(rec.thth_row.text)
    fields = question.depend_fields() if question else None
    if fields is None or rec.values is None:
        return "bytes:" + rec.version
    unread = sorted(k for k in fields if k in rec.unread)
    if unread:
        # 依る欄に読めなかったセルがある: 読んだ null の指紋と決して合わない形にする（欠けたセルと null を区別する）。
        return "unread:" + fp({"read": {k: rec.values.get(k) for k in fields if k not in rec.unread},
                               "unread": unread})
    return fp({k: rec.values.get(k) for k in fields})


def question_of(kind: Kind, ident: str) -> Question | None:
    return next((q for q in kind.questions if q.ident == ident), None)


def observations(kind: Kind, rec: Record) -> list:
    """状態の欄に書いてある語（観測）。版つき。判断ではない。"""
    out = []
    for q in kind.questions:
        if not q.state:
            continue
        if q.level == "record" and rec.values is not None and q.state in rec.values:
            out.append({"record": rec.ref, "unit": None, "question": q.ident, "word": _word(rec.values.get(q.state)),
                        "reason_kind": rec.values.get(q.reason_kind) if q.reason_kind else None,
                        "version": rec.version})
        elif q.level == "unit":
            for unit in rec.units:
                if q.state in unit.item:
                    out.append({"record": rec.ref, "unit": unit.pointer, "question": q.ident,
                                "word": _word(unit.item.get(q.state)),
                                "reason_kind": unit.item.get(q.reason_kind) if q.reason_kind else None,
                                "version": rec.version})
    return out


def word_of(value):
    """書いてある値を語にする（観測と、検収の記録の状態で同じ直し方）。"""
    return _word(value)


def _word(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return value if value is None or isinstance(value, str) else json.dumps(value, ensure_ascii=False)


# ------------------------------------------------------------------ 台帳を読む（目録）

@dataclass
class Vessel:
    root: str
    kinds: dict
    constitution: dict
    ids: dict
    records: dict              # ref → Record（同じ番号は最初の 1 つ。重なりは problems に。引くのは `unique`）
    all_records: list
    _counts: dict = field(default_factory=dict)
    _by_ref: dict = field(default_factory=dict)   # 番号 → 同じ番号の記録の並び（索引。`get` は頭から見ない）

    def __post_init__(self):
        for rec in self.all_records:
            self._by_ref.setdefault(rec.ref, []).append(rec)
            if rec.id is not None:
                self._counts[rec.ref] = self._counts.get(rec.ref, 0) + 1

    def kind_of(self, rec: Record) -> Kind:
        return self.kinds[rec.kind]

    def get(self, ref: str) -> Record:
        if not isinstance(ref, str) or ":" not in ref:
            raise RegisterRefused("register_args_invalid", "record")
        same = self._by_ref.get(ref, [])
        if not same:
            raise RegisterRefused("register_record_missing", "record")
        if len(same) > 1:
            raise RegisterRefused("register_unit_ambiguous", f"{len(same)} records",
                                  candidates=sorted({r.path for r in same}), record=ref)
        return same[0]

    def unique(self, ref: str) -> Record | None:
        """番号で 1 つに決まる記録。無いか、同じ番号の記録が 2 つ以上あれば None（先頭を黙って選ばない）。"""
        return self.records.get(ref) if self._counts.get(ref, 0) == 1 else None

    def is_duplicated(self, ref: str) -> bool:
        return self._counts.get(ref, 0) > 1

    def catalog(self) -> list:
        """目録（番号・種類・版・場所）。宣言とファイルから作り直せる物で、正本ではない。"""
        return [{"record": r.ref, "kind": r.kind, "version": r.version, "path": r.path, "inspected": r.inspected,
                 "units": len(r.units)} for r in self.all_records if r.id is not None]


def builtin_row_kind() -> Kind:
    """備え付けの種類: 今の店の台帳（頭つきの .md）を 1 字も直さずに読む形。"""
    return Kind(name="thth_row", places=["*.md"], format="thth_row", records="file", max_bytes=form.ROW_MAX_BYTES,
                rows_at=[], id_rule={"field": "id"},
                questions=[Question("says_so", None, "record", "confirmed", None, None, None)], pointers=[],
                units=None, path="(備え付け)")


def open_vessel(root: str, decl_base: str | None = None) -> Vessel:
    """台帳を器として読む。宣言が無い台帳は、備え付けの種類（今の頭つきの .md）だけで読む。"""
    if decl_base is not None:
        decl = load_declarations(root, decl_base)
    elif has_declarations(root):
        decl = load_declarations(root)
    else:
        decl = {"kinds": {"thth_row": builtin_row_kind()}, "constitution": {}, "ids": {}}
    all_records = []
    for kind in decl["kinds"].values():
        all_records.extend(read_kind(root, kind, {k: v for k, v in decl["ids"].items()
                                                  if k.startswith(kind.name + ":")}))
    records = {}
    for rec in all_records:
        if rec.id is not None:
            records.setdefault(rec.ref, rec)
    return Vessel(root, decl["kinds"], decl["constitution"], decl["ids"], records, all_records)


# ------------------------------------------------------------------ 指す欄

def pointer_targets(rec: Record, spec: dict) -> list:
    return pointer_pieces(rec, spec)[0]


def pointer_pieces(rec: Record, spec: dict) -> tuple:
    """`(指す先の番号の並び, 空の要素の数)`。欄そのものが空なら指していない（0 件・空 0）。並びや区切りの中の
    空の要素（`["e", ""]`・`c||d`）は、指す先の一部の欠けなので数えて返す（黙って落とさない）。"""
    if "template" in spec:
        return [spec["template"].replace("{id}", rec.id or "")], 0
    raw = rec.values.get(spec["field"]) if rec.values else None
    if raw in (None, ""):
        return [], 0
    if isinstance(raw, list):
        return [str(v) for v in raw if v not in (None, "")], sum(1 for v in raw if v in (None, ""))
    raw = str(raw)
    if spec.get("split"):
        parts = [p.strip() for p in raw.split(spec["split"])]
        return [p for p in parts if p], sum(1 for p in parts if not p)
    return [raw], 0


def recorded_hash(spec: dict, target: Record) -> str | None:
    """来歴の控えの指紋を、相手の今の値から作る（宣言の `hash`: `sha1:12` など）。欄が 1 つなら、その字の UTF-8。
    2 つ以上なら `canonical` の UTF-8。"""
    rv = spec["recorded_version"]
    algo, length = rv["hash"].split(":")
    if target.values is None:
        return None
    if len(rv["of"]) == 1:
        raw = target.values.get(rv["of"][0])
        data = ("" if raw is None else str(raw)).encode("utf-8")
    else:
        data = canonical({k: target.values.get(k) for k in rv["of"]}).encode("utf-8")
    return HASHES[algo](data).hexdigest()[:int(length)]


def links(vessel: Vessel) -> dict:
    """指す欄を全部たどる: `{"edges": [...], "dangling": [...], "cycles": [...], "review": [...]}`。"""
    edges, dangling, review, ambiguous, blank = [], [], [], [], []
    for rec in vessel.all_records:
        if rec.id is None or rec.values is None:
            continue
        kind = vessel.kinds[rec.kind]
        for spec in kind.pointers:
            targets, blanks = pointer_pieces(rec, spec)
            if blanks:
                # 並び・区切りの中の空の要素: 指す先の一部が欠けている（黙って落とさず見せる）。
                blank.append({"from": rec.ref, "to_kind": spec["to"], "field": spec.get("field"), "blanks": blanks})
            for target_id in targets:
                ref = f"{spec['to']}:{target_id}"
                target = vessel.unique(ref)
                meaning = spec["meaning"]
                if spec.get("meaning_from"):
                    # 表の列が意味を言う（根拠の relation など）。器が意味を知らない語（こぴちゃの context など）は
                    # 「関わる」として数える（支えにも反証にも数えない。述語は中身）。
                    said = rec.values.get(spec["meaning_from"])
                    meaning = said if said in MEANINGS else "related"
                edge = {"from": rec.ref, "to": ref, "meaning": meaning, "field": spec.get("field")}
                edges.append(edge)
                if vessel.is_duplicated(ref):
                    # 同じ番号の記録が 2 つ以上: 先頭を選んで来歴の控えを比べない（番号の重なりは problems に出る）。
                    ambiguous.append(edge)
                    continue
                if target is None:
                    dangling.append(edge)
                    continue
                if spec.get("recorded_version"):
                    recorded = rec.values.get(spec["recorded_version"]["field"])
                    now = recorded_hash(spec, target)
                    if recorded != now:
                        review.append({**edge, "recorded": recorded, "now": now})
    cycles = _cycles(edges)
    return {"edges": edges, "dangling": dangling, "cycles": cycles, "review": review, "ambiguous": ambiguous,
            "blank": blank}


def _cycles(edges: list) -> list:
    """輪を禁じる 3 つ（含む・親・置き換える）の輪。意味ごとに別に見る。"""
    found = []
    for meaning in CYCLE_FREE:
        graph = {}
        for e in edges:
            if e["meaning"] == meaning:
                graph.setdefault(e["from"], set()).add(e["to"])
        state = {}
        for start in sorted(graph):
            if state.get(start) is not None:
                continue
            # 再帰でなく繰り返しでたどる（長い参照の鎖でも深さの上限に当たらない）。
            state[start] = 1
            stack, todo = [start], [iter(sorted(graph.get(start, ())))]
            while todo:
                nxt = next(todo[-1], None)
                if nxt is None:
                    state[stack.pop()] = 2
                    todo.pop()
                elif state.get(nxt) == 1:
                    found.append({"meaning": meaning, "records": stack[stack.index(nxt):] + [nxt]})
                elif state.get(nxt) is None:
                    state[nxt] = 1
                    stack.append(nxt)
                    todo.append(iter(sorted(graph.get(nxt, ()))))
    return found


def problems(vessel: Vessel) -> list:
    """読みの問題（行ごと）。YAML の断りは `yaml:` で始まる語。"""
    out = []
    for rec in vessel.all_records:
        if rec.problems:
            out.append({"record": rec.ref if rec.id else None, "kind": rec.kind, "path": rec.path,
                        "words": list(rec.problems)})
    return out


def ambiguous_units(vessel: Vessel) -> list:
    """鍵で決まらない単位（鍵と中身の指紋で指した組）。"""
    out = []
    for rec in vessel.all_records:
        groups = {}
        for unit in rec.units:
            if unit.ambiguous and unit.pointer.startswith("kc:"):
                groups.setdefault(unit.pointer.split(".")[0], []).append(unit)
            elif unit.ambiguous and unit.pointer.startswith("i:"):
                groups.setdefault(unit.pointer, []).append(unit)  # 同じ番号の別々の行
        for _key, members in groups.items():
            out.append({"record": rec.ref, "key": members[0].key, "units": [u.pointer for u in members],
                        "same_content": max(u.same_content for u in members)})
    return out


def ids_table_moves(vessel: Vessel) -> list:
    """番号の表の場所に無い記録と、同じ版の候補（引き継ぐかは持ち主に聞く・器は結ばない）。"""
    out = []
    present = {r.path for r in vessel.all_records}
    listed = set(vessel.ids.values())
    for ref, path in sorted(vessel.ids.items()):
        if path in present:
            continue
        kind = ref.split(":", 1)[0]
        out.append({"record": ref, "path": path,
                    "candidates": sorted(r.path for r in vessel.all_records if r.kind == kind and r.path not in listed)})
    return out
