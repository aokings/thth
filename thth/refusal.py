"""断りの口（設計「監査と審査の機械化と前向きな断り」§3「3 つの口で同じもの」・段 B）。

目録（`thth/refusal_catalog.py`）を引いて、どの口でも同じ 3 つ（なぜ・通すには・もっと良く）を返す。

- **CLI の文**: 1 行目は呼ぶ側の今の行のまま（`符丁: なぜ`・`refusals.reason_code` が読む）。2 行目から
  `なぜ:`（1 行目が目録のなぜを言っていないときだけ）・`通すには:`・`もっと良く:`（目録に better が
  あるときだけ）を足す。
- **CLI の `--json`**: 今ある鍵は消さず名も変えず、`why`・`fix`・`better`・`next_at` を足す（同じ名の鍵が
  既にあれば上書きしない）。
- **MCP**: text の 1 行目（符丁）はそのまま、CLI の文と同じ 2 行目からを足す。

3 つの文は `parts()` の 1 か所で作るので、口ごとに食い違わない。穴埋めは目録の `render` の決まり
どおり（静的な型の穴に、形を検査した値だけ・本文・自由文・パス・秘密は埋めない）。

**通るときは呼ばない**（黙る）。目録に無い符丁・退いた符丁には何も足さない（1 行目だけ）。
"""
from __future__ import annotations

import json
import re
import sys

from . import refusal_catalog

WHY = "なぜ: "
FIX = "通すには: "
BETTER = "もっと良く: "
PREFIXES = (WHY, FIX, BETTER)
# `--json` に足す鍵（設計 §3）。
JSON_KEYS = ("why", "fix", "better", "next_at")

# 1 行目の先頭の符丁（`refusals._LEADING` と同じ形）。
_LEADING = re.compile(r"\s*([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?=$|[\s:：（(、。,])")
# 「言っているか」を比べるとき、補いの（…）と空白・句読点の差は見ない
# （安全装置の 1 行目は「（次に出せるのは …）」を文の途中に挟む）。
_PAREN = re.compile(r"（[^（）]*）|\([^()]*\)")
_NOISE = re.compile(r"[\s。、．，.,]")


def code_of(line) -> str | None:
    """行の先頭の符丁（無ければ None）。"""
    match = _LEADING.match(line or "")
    return match.group(1) if match else None


# 目録の fix が 1 つの狭い見方の文で、ほかの口では当たらない符丁（足すと誤った道を示す）。目録に口を問わない
# 文が入るまで、2 行目からを足さない。`unsupported_operation` の fix は「はてなの口座でだけ使えます」
# （`bookmark_cli._BATCH_MESSAGES` の 1 行目の文・目録の fix は優先の見方の文と同じ、が段 A の柵）。
VIEW_ONLY = frozenset(("unsupported_operation",))

# 続く符丁（詳細）の方が具体的な 3 つを持つ符丁。`invalid_draft: hashtags_disabled_by_ledger` は、
# 詳細の符丁が目録にあればその 3 つを出す（設計 §1 の代表例 1・1 行目と `error` は変えない）。
DETAIL_FIRST = frozenset(("invalid_draft",))


def focus(code, detail=None) -> str:
    """3 つを引く符丁: 詳細が目録の符丁で、`code` が `DETAIL_FIRST` なら詳細・それ以外は `code`。"""
    if (code in DETAIL_FIRST and isinstance(detail, str) and code_of(detail) == detail
            and parts(detail) is not None):
        return detail
    return code


DOOR_NAMES = ("cli", "remote", "mcp")
DOOR_ENV = "THTH_REFUSAL_DOOR"


def door_of(door=None):
    """口の名前。MCP が CLI を子として呼ぶとき（`THTH_REFUSAL_DOOR=mcp`）は、読み手が MCP なので mcp を先に。"""
    import os
    if os.environ.get(DOOR_ENV) == "mcp":
        return "mcp"
    return door if door in DOOR_NAMES else None


def _values(values: dict) -> dict:
    """穴に渡す値。`next_at` は `<時刻>` の穴にも入れる（通すにはの予約の命令がそのまま打てる）。"""
    out = dict(values)
    if isinstance(out.get("next_at"), str) and "at" not in out:
        out["at"] = out["next_at"]
    return out


def parts(code, **values) -> dict | None:
    """符丁の 3 つ（`why`・`fix`・`better`）。fix には打てる命令を（）で添える（`refusal_catalog.lines` と同じ形）。

    目録に無い・退いた符丁は None。値は `refusal_catalog.render` の穴の検査を通ったものだけ入る。
    `reason` は静的な詳細の語: 安全装置の詳細（`refusal_catalog.GUARD_DETAIL_WORDS`・なぜも引く）か、項の `by_detail` に
    鍵のある語（通すにはともっと良くだけを引く・店の引数の誤りなど）。ほかの語は見ない。
    `door` は口（`cli`＝手元の CLI・`remote`＝遠くの道の CLI・`mcp`）。目録の `fix_by_door` にその口の文があれば
    それを出す（その口で打てる道）。環境の `THTH_REFUSAL_DOOR=mcp`（MCP が CLI を呼ぶとき）は口の指定より先に効く。
    """
    if not isinstance(code, str) or code in VIEW_ONLY:
        return None
    entry = refusal_catalog.get(code)
    if entry is None or entry.get("retired"):
        return None
    if set(entry.get("doors") or ()) <= set(refusal_catalog.FOREIGN_DOORS):
        return None  # Worker だけの符丁（その文は Worker の口で通る道・Python の口には足さない）
    values = _values(values)
    rendered = refusal_catalog.render(code, **values)
    reason = values.get("reason")
    if reason in refusal_catalog.GUARD_DETAIL_WORDS and "guard_message" in entry.get("sources", ()):
        # 安全装置の断りの静的な詳細（止めた理由・返信の日の上限）では、なぜも安全装置の文のその分岐から引く
        # （「口座が止まっています（理由不明）」でなく「急な連投（burst）で口座を止めました…」）。
        special = refusal_catalog.guard_why(code, reason, **({"account": values["account"]}
                                                             if isinstance(values.get("account"), str) else {}))
        if special:
            rendered["why"] = special
    # 安全装置の詳細ごとの fix・better（返信の日の上限は投稿の枠と逆）と、口ごとの通す道（その口で打てる形）。
    # 詳細ごとの文は、安全装置の語か、その項の `by_detail` に鍵のある語（静的な語・店の引数の誤り `store_args_invalid` など）だけ。
    by_detail = entry.get("by_detail") or {}
    detail = by_detail.get(reason) if isinstance(reason, str) and (
        reason in refusal_catalog.GUARD_DETAIL_WORDS or reason in by_detail) else None
    chosen = {"text": entry["fix"]["text"], "command": entry["fix"]["command"],
              "better": entry["better"], "fix_by_door": entry.get("fix_by_door") or {}}
    if detail:
        chosen = {"text": detail.get("fix", chosen["text"]), "command": detail.get("command", chosen["command"]),
                  "better": detail.get("better", chosen["better"]), "fix_by_door": detail.get("fix_by_door") or {}}
    door = door_of(values.get("door"))
    by_door = chosen["fix_by_door"].get(door) if door else None
    if by_door:
        chosen = dict(chosen, text=by_door.get("text", chosen["text"]), command=by_door.get("command"),
                      better=by_door.get("better", chosen["better"]))
        if by_door.get("why"):
            rendered["why"] = refusal_catalog.fill_slots(by_door["why"], **values)
    rendered["fix"] = refusal_catalog.fill_slots(chosen["text"], **values)
    rendered["command"] = refusal_catalog.fill_slots(chosen["command"], **values)
    rendered["better"] = refusal_catalog.fill_slots(chosen["better"], **values)
    fix = rendered["fix"] or ""
    command = rendered["command"]
    if command and command not in fix:
        fix = f"{fix}（{command}）"
    return {"code": code, "why": rendered["why"] or "", "fix": fix, "better": rendered["better"] or None}


def _plain(text: str) -> str:
    previous = None
    while previous != text:
        previous, text = text, _PAREN.sub("", text)
    return _NOISE.sub("", text)


def says(line, why) -> bool:
    """1 行目が目録のなぜを既に言っているか（補いの（…）と句読点の差は見ない）。"""
    if not why or not isinstance(line, str):
        return False
    plain = _plain(why)
    return bool(plain) and plain in _plain(line)


def tail(code, first_line="", **values) -> list[str]:
    """2 行目からの行（`なぜ:`・`通すには:`・`もっと良く:`）。目録に無い符丁は []。"""
    found = parts(code, **values)
    if found is None:
        return []
    out = []
    if found["why"] and not says(first_line, found["why"]):
        out.append(WHY + found["why"])
    if found["fix"]:
        out.append(FIX + found["fix"])
    if found["better"]:
        out.append(BETTER + found["better"])
    return out


def fields(code, *, next_at=None, **values) -> dict:
    """`--json` に足す 4 つの鍵（目録に無い符丁は {}）。"""
    found = parts(code, next_at=next_at, **values)
    if found is None:
        return {}
    return {"why": found["why"], "fix": found["fix"], "better": found["better"],
            "next_at": next_at if isinstance(next_at, str) else None}


def add_fields(payload: dict, code, **values) -> dict:
    """今の JSON に 4 つの鍵を足す（ある鍵は上書きしない・消さない）。同じ dict を返す。"""
    for key, value in fields(code, **values).items():
        payload.setdefault(key, value)
    return payload


def text_lines(first_line, code=None, **values) -> list[str]:
    """CLI の文と MCP の text の行（1 行目は渡したまま・2 行目から足す）。"""
    code = code or code_of(first_line)
    return [first_line, *(tail(code, first_line, **values) if code else [])]


def mcp_text(text, *, code=None, **values) -> str:
    """MCP の text: 1 行目（符丁）はそのまま、CLI の文と同じ行を足す（既に載っている行は重ねない）。"""
    if not isinstance(text, str) or not text:
        return text
    first = text.split("\n", 1)[0]
    code = code or code_of(first)
    have = set(line.strip() for line in text.splitlines())
    extra = [line for line in (tail(code, first, **values) if code else []) if line not in have]
    return text + "".join("\n" + line for line in extra)


def _dump(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def emit(args, code, *, line=None, payload=None, next_at=None, rc=2, both=False, dump=None, err=None,
         about=None, as_json=None, **values) -> int:
    """断りを 1 か所で出して rc を返す。

    - 文: stderr に 1 行目（`line`・無ければ `符丁: なぜ`）と 2 行目から（`tail`）。
    - `args.json` が真: stdout に `payload`（無ければ `{"error": 符丁}`）に 4 つの鍵を足した JSON
      （`dump` で今の口の書き方を保つ）。`both` なら文も stderr に出す（遠くの道の今の形）。
    - `about` は 3 つを引く符丁（既定は `code`・`focus()` で詳細の符丁を選ぶとき）。
    - 口座の穴は `values` に無ければ `args.account` から（形の検査は `render` が持つ）。
    - `as_json` を渡せば `args.json` より先に見る（`--json` を引数でなく旗で受ける口）。
    """
    if as_json is None:
        as_json = bool(getattr(args, "json", False)) if args is not None else False
    values.setdefault("door", "cli")  # emit は CLI の口（遠くの道は remote を渡す・MCP の子は環境が先）
    if "account" not in values and isinstance(getattr(args, "account", None), str):
        values["account"] = args.account
    if next_at is not None:
        values["next_at"] = next_at
    about = about or code
    if line is None:
        found = parts(code, **values)
        line = f"{code}: {found['why']}" if found and found["why"] else str(code)
    stream = err if err is not None else sys.stderr
    if not as_json or both:
        for text in text_lines(line, about, **values):
            print(text, file=stream)
    if as_json:
        body = dict(payload) if payload is not None else {"error": code}
        values.pop("next_at", None)
        add_fields(body, about, next_at=next_at, **values)
        (dump or _dump)(body)
    return rc


def read(text, code, **values) -> dict:
    """文か MCP の text から 3 つを読み戻す（柵が読む）。なぜは `なぜ:` の行か、1 行目が目録のなぜを言っていればそれ。"""
    rows = [row.strip() for row in (text or "").splitlines()]
    found = parts(code, **values) or {}

    def after(prefix):
        return next((row[len(prefix):] for row in rows if row.startswith(prefix)), None)

    why = after(WHY)
    if why is None and rows and says(rows[0], found.get("why")):
        why = found.get("why")
    return {"why": why, "fix": after(FIX), "better": after(BETTER)}
