"""判断の記録の最小の形と、判断が効いているかの読み（設計 §0.2・§5.3・§10 の段 1）。

- 判断 = 「どの記録（単位・値）の・どの版について・どの問いに・誰が・どう照らして・いつ・台帳の語と条件・
  理由（種類と説明）・いつまで」。置き場は台帳の `.thth/judgments.jsonl`（1 行 1 件・足していくだけ）。
  **段 1 の命令は判断を書かない**（privacy への影響なし）。書くのは試験と、段 3 の命令（`judge`・`accept`）。
- 判断は「見たときの版」と「依る所の指紋」を持つ。依る所が今と違えば**古い判断**（効かない・消さない）。
  指し先が今の台帳に無ければ**消えた指し先**への判断（引き継がない）。
- **書いてあるだけの状態は観測で、判断ではない**（`vessel.observations`）。判断になるのは、検収の記録（対象の版を
  示す: 指紋か、確かめた中身そのもの）か、明示の受け入れだけ。
- 結論（よい・だめ・まだ）は問いに答えるときに返す。保存するのは台帳の語と条件。語と結論の対応は憲法。
- **及ぶ範囲**（設計 §5.1 の内側の単位の 3）: 値への判断はその値に、行への判断は依る欄のうち範囲が行の鍵と同じ
  値にだけ及ぶ。範囲と結論は同じ 1 つの判断から取る。人が範囲を書けるのは、範囲が未宣言の値への判断だけ
  （空・空白だけは未宣言。ほかに書かれたら断り、保存された物にあれば答えに使わない）。
- **鍵で決まらない単位の訂正は結ばない。** 結びの候補（同じ鍵で、消えた指し先と現れた指し先）を見せるだけ。
  結ぶのは人が名指ししたときだけ（`carry`。直した者と同じ者は結べない）。

- **消す求めの例外**（段 5）: 消す求めは、範囲の版を対象にした行を「印の形」に書き換える（`erased`。行の数と順は
  変えない）。印の行は番号と中身を比べず、求めの結果に番号が載っていることを読む入口で見る（`erased_ok`）。
  止める・だめは印の形でも狭める向きに効き、よい・止めないは効かない（`evaluate`）。

網に出ない。
"""
from __future__ import annotations

import hashlib
import json
import os
import re

from . import vessel as V
from .errors import RegisterRefused

FILE = "judgments.jsonl"
SCHEMA = "thth.judgment/1"
ERASE_DIR = "erase"                   # 消す求めの記録（`.thth/erase/<求めの番号>/`。erase.py が書く）
ERASE_RESULT = "result.json"
ERASE_SCOPE = "scope.json"
ERASE_ID = re.compile(r"e-[0-9a-f]{16}\Z")
X_ID = re.compile(r"x-[0-9a-f]{16}\Z")      # 印の行の番号（消す求めで付け直した乱数の番号。中身の指紋でない）
PATHS = ("inspection", "acceptance", "carry")
MAX_BYTES = 50 * 1024 * 1024


def path_of(root: str) -> str:
    return os.path.join(root, V.DECL_DIR, FILE)


def read(root: str) -> list:
    path = path_of(root)
    if not os.path.exists(path):
        return []
    if os.path.islink(path):
        raise RegisterRefused("register_judgment_invalid", "symlink")
    out = []
    with open(path, "rb") as f:
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise RegisterRefused("register_judgment_invalid", "too_large")
    for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except (ValueError, RecursionError):
            raise RegisterRefused("register_judgment_invalid", f"line {number}") from None
        if not isinstance(item, dict) or item.get("schema") != SCHEMA or not isinstance(item.get("target"), dict):
            raise RegisterRefused("register_judgment_invalid", f"line {number}")
        if V.value_problem(item):
            raise RegisterRefused("register_judgment_invalid", f"line {number}")
        problem = shape_problem(item)
        if problem:
            raise RegisterRefused("register_judgment_invalid", f"line {number} {problem}")
        if "erased" in item and not erased_ok(root, item):
            raise RegisterRefused("register_judgment_invalid", f"line {number} erased_not_in_result")
        out.append(item)
    return out


def erased_ok(root, item: dict) -> bool:
    """消す求めで印の形に書き換えた行（`erased`）が、その求めの記録に載っているか（設計 §5.5・指示 段 5 §3.4）。
    `.thth/erase/<求め>/` に範囲の判断と結果があり、結果の「書き換えた判断の行」にこの行の番号があるときだけ真。
    印の行は、番号が中身から作った物と合うかを見ない（中身を除いたので）。代わりにこれを見る。"""
    mark = item.get("erased")
    if not isinstance(item.get("id"), str) or not X_ID.fullmatch(item["id"]):
        return False
    if root is None or not isinstance(mark, dict) or not isinstance(mark.get("request"), str) \
            or not ERASE_ID.fullmatch(mark["request"]):
        return False
    base = os.path.join(root, V.DECL_DIR, ERASE_DIR, mark["request"])
    if os.path.islink(base) or not os.path.isfile(os.path.join(base, ERASE_SCOPE)):
        return False
    try:
        with open(os.path.join(base, ERASE_RESULT), "rb") as f:
            result = json.loads(f.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    lines = result.get("rewritten_lines") if isinstance(result, dict) else None
    return isinstance(lines, list) and item.get("id") in lines and result.get("request") == mark["request"]


def is_erased(item) -> bool:
    return isinstance(item, dict) and isinstance(item.get("erased"), dict)


# ------------------------------------------------------------------ 行の骨と、続きの比べ（段 5 §2.2・§3.7）

def line_skeleton(j: dict, with_unit: bool = True) -> str:
    """判断の行の骨（問い・対象の記録の番号・単位の指し先・語・結論・日時・いつまで）の指紋。消す求めで印の形にしても
    変わらない欄だけから作る。**番号と「誰が」は入れない**（配る物は公開の場所に出るので、当てやすい名前を指紋に
    混ぜない。番号は消す求めで付け直す）。続きの比べ（CI の `route.erase_continued` と `check --against` の
    `trail.judgments_continue`）は、前の行と今の行を**位置で**この骨で比べる。骨の定めはここ 1 か所。
    `with_unit=False` は指し先を除いた骨（消す求めで指し先を除いた行と比べる）。"""
    t = j.get("target") if isinstance(j.get("target"), dict) else {}
    a = j.get("answer") if isinstance(j.get("answer"), dict) else {}
    body = {"question": j.get("question"), "record": t.get("record"), "word": a.get("word"),
            "conclusion": a.get("conclusion"), "at": j.get("at"), "until": j.get("until")}
    if with_unit:
        body["unit"] = t.get("unit")
        body["value"] = t.get("value")
    return hashlib.sha256(V.canonical(body).encode("utf-8")).hexdigest()[:32]


def _salted(salt, text: str) -> str:
    """骨に塩を混ぜる（公開の場所の写しに出す形。`salt` が None なら素の骨）。"""
    return text if salt is None else hashlib.sha256(salt + text.encode("utf-8")).hexdigest()


def skeleton_pair(j: dict, salt: bytes | None = None) -> list:
    """`[指し先つきの骨, 指し先を除いた骨]`（前の行の側。CI に配る物の `deployed.lines` の頭の 2 つは、塩を混ぜた形）。"""
    j = j if isinstance(j, dict) else {}
    return [_salted(salt, line_skeleton(j, True)), _salted(salt, line_skeleton(j, False))]


# 印の行が持ってよい欄（`erase.marked` が書く形・指示 段 5 §3.4）。ほかの欄を持つ印の行は読む入口で断る。
MARKED_KEYS = frozenset({"schema", "id", "question", "target", "answer", "reason", "by", "at", "until", "path",
                         "carries", "erased", "source"})
MARKED_TARGET = frozenset({"record", "unit", "value", "unit_dropped"})


def marked_shape_problem(item: dict) -> str | None:
    """印の行（`erased`）が決まった欄だけを持つか。合わない最初の語。`target` は記録・指し先・値・`unit_dropped`
    だけ（`ledger` などを足して効き方を変えさせない）。`unit_dropped` は真だけで、指し先と一緒には持たない。
    値の指し先は単位と一緒のときだけ。答えは語と結論だけ、理由は種類だけ。"""
    if set(item) - MARKED_KEYS:
        return "erased_shape"
    t = item.get("target")
    if not isinstance(t, dict) or set(t) - MARKED_TARGET:
        return "erased_target"
    if "unit_dropped" in t and (t["unit_dropped"] is not True or t.get("unit") or t.get("value")):
        return "erased_target"
    if t.get("value") and not t.get("unit"):
        return "erased_target"
    a, r, e = item.get("answer"), item.get("reason"), item.get("erased")
    if not isinstance(a, dict) or set(a) - {"word", "conclusion"}:
        return "erased_answer"
    if not isinstance(r, dict) or set(r) - {"kind"}:
        return "erased_reason"
    if not isinstance(e, dict) or set(e) != {"request"}:
        return "erased"
    return None


def line_continues(pair, j: dict, *, lines, requests, salt: bytes | None = None) -> str | None:
    """前の行（の骨の組 `pair`）の位置にある今の行 `j` が、続きとして通るか。通れば None、通らなければ語。
    印の行（`erased`）は、消す求めの結果に載った番号（`lines`）と求め（`requests`）のときだけ、印の形が決まった欄
    だけで、骨が前の行と同じなら通す。指し先を外した印の行は `unit_dropped` を持つときだけ、指し先を除いた骨で
    比べる（持たなければ指し先つきの骨で比べるので、前の行が単位への行なら合わない）。印でない行は、指し先つきの骨が
    同じなら通す（CI は印でない行を全バイトで比べる。`route.erase_continued`）。`salt` は配る物の塩を混ぜた骨と
    比べるとき。"""
    if is_erased(j):
        if j.get("id") not in lines or j["erased"].get("request") not in requests:
            return "line_not_in_erase"
        if marked_shape_problem(j):
            return "line_shape"
        if (j.get("target") or {}).get("unit_dropped"):
            return None if _salted(salt, line_skeleton(j, False)) == pair[1] else "line_changed"
        return None if _salted(salt, line_skeleton(j, True)) == pair[0] else "line_changed"
    return None if _salted(salt, line_skeleton(j, True)) == pair[0] else "line_changed"


def _text(value) -> bool:
    return isinstance(value, str) and bool(value)


def shape_problem(item: dict) -> str | None:
    """判断の 1 行の欄の形を見る（読む所と評価する所が前提にする形）。合わない最初の欄の名前を返す。合えば None。
    必須: `id`・`question`（1 字以上の字）、`target.record`（1 字以上の字）。在れば形を見る: `target.unit`・
    `target.value`（字）、`answer`・`reason`・`seen`・`content`（辞書）、`at`（字）、`siblings`（字の並び）。"""
    target = item.get("target")
    if not isinstance(target, dict):
        return "target"
    if not _text(item.get("id")):
        return "id"
    if not _text(item.get("question")):
        return "question"
    if not _text(target.get("record")):
        return "target.record"
    for name in ("unit", "value"):
        if target.get(name) is not None and not isinstance(target[name], str):
            return f"target.{name}"
    for name in ("answer", "reason", "seen", "content"):
        if item.get(name) is not None and not isinstance(item[name], dict):
            return name
    if item.get("at") is not None and not isinstance(item["at"], str):
        return "at"
    if "erased" in item:
        if not (isinstance(item["erased"], dict) and isinstance(item["erased"].get("request"), str)):
            return "erased"
        problem = marked_shape_problem(item)
        if problem:
            return problem
    siblings = item.get("siblings")
    if siblings is not None and not (isinstance(siblings, list) and all(isinstance(x, str) for x in siblings)):
        return "siblings"
    return None


def append(root: str, items: list) -> None:
    """試験と段 3 の命令が使う（段 1 の命令は呼ばない）。足すだけ。"""
    path = path_of(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def new_id(body: dict) -> str:
    """判断の番号: 判断の中身（番号の欄 `id` を除く全部の欄。足した欄も入る）の決まった書き方の sha256 の頭 16 字。
    段 3 のように欄を足す判断も、この作り方で番号を付ける（`make` の `extra` で足せば、`make` が付ける）。"""
    return "j-" + hashlib.sha256(V.canonical({k: v for k, v in body.items() if k != "id"}).encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ 対象と依る所

def _locate(vessel: V.Vessel, target: dict, *, strict: bool = False):
    """`(記録, 種類, 単位 or None, 値 or None)`。指し先が無ければ None を含む。

    同じ指し先に中身の違う単位が 2 つ以上ある（番号の重なり）ときは、`strict` なら断り（`register_unit_ambiguous`）、
    そうでなければ指し先が無いのと同じに扱う（そこへの判断は効かない）。"""
    try:
        rec = vessel.get(target["record"])
    except RegisterRefused:
        if strict:
            raise
        return None, None, None, None
    kind = vessel.kinds[rec.kind]
    unit = value = None
    if target.get("unit"):
        try:
            unit = V.unit_at(rec, target["unit"])
        except RegisterRefused:
            if strict:
                raise
            unit = None
        if unit is None:
            return rec, kind, None, None
        if target.get("value"):
            value = next((v for v in V.values_of(rec, kind, unit)
                          if v.name == target["value"] and ("time" not in target
                                                            or _same_time(v.time, target["time"]))), None)
    return rec, kind, unit, value


def _same_time(a, b) -> bool:
    """指し先の時点の比べ。依る所の指紋と同じ正規化（改行を LF に・辞書は鍵の順）を通した書き方で比べる
    （指紋が同じなのに時点だけ別と読んで「消えた」にしない）。"""
    return V.canonical(a) == V.canonical(b)


def _asked_time(a, b) -> bool:
    """尋ねる側の時点（`as_time`。命令からは字で来る）との比べ。字にして比べる（2026 と "2026" を同じに）。"""
    return (a is None and b is None) or (a is not None and b is not None and str(a) == str(b))


def scope_said(j) -> str | None:
    """判断に人が書いた範囲。字で、前後の空白を落として 1 字以上なら落とした字。それ以外（None・空・空白だけ・
    字でない物）は None（未宣言）。人が書いた範囲を読む所は、全部これを通す。"""
    raw = (j.get("answer") or {}).get("scope") if isinstance(j, dict) else None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _scope_arg(raw):
    """命令に渡された範囲（判断に書く・尋ねる）。None はそのまま。字で 1 字以上でなければ断る。"""
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise RegisterRefused("register_args_invalid", "scope_blank")
    return raw.strip()


def current_depend(vessel: V.Vessel, target: dict, question: str, said=None):
    """今の依る所の指紋。指し先が無ければ None。`said` は判断に人が書いた範囲（`scope_said` を通した字）。"""
    rec, kind, unit, value = _locate(vessel, target)
    if rec is None:
        return None
    if target.get("unit"):
        if unit is None:
            return None
        if target.get("value"):
            if value is None:
                return None
            return value.depend(said=said if value.scope_form is None else None)
        return V.unit_depend(kind, unit, V.question_of(kind, question))
    q = V.question_of(kind, question)
    return V.record_depend(kind, rec, q)


def make(vessel: V.Vessel, target: dict, question: str, *, by: str, at: str, path: str = "inspection",
         word=None, conclusion=None, scope=None, reason_kind=None, reason_text=None, how=None, until=None,
         content=None, carries=None, source=None, extra: dict | None = None) -> dict:
    """今の台帳の版を見て、判断を 1 件つくる（書かない）。`content` を渡すと「確かめた中身そのもの」を持つ
    検収の記録になり、指紋は持たない（効くかは、その中身の指紋が今の依る所と合うかで決まる）。
    `extra` は判断に足す欄（段 3 の `against` など）。番号の作り方に入る。決まった欄を上書きする名前は断る。"""
    if path not in PATHS:
        raise RegisterRefused("register_args_invalid", "path")
    scope = _scope_arg(scope)
    rec, kind, unit, value = _locate(vessel, target, strict=True)
    if rec is None or (target.get("unit") and unit is None) or (target.get("value") and value is None):
        raise RegisterRefused("register_record_missing", "target")
    if scope is not None and target.get("unit") and (value is None or value.scope_form is not None):
        # 行への判断は範囲を書けない。範囲が台帳の欄（鍵・範囲の欄）で決まっている値にも書けない。
        raise RegisterRefused("register_args_invalid", "scope_not_allowed")
    body = {"schema": SCHEMA, "target": dict(target), "question": question,
            "answer": {"word": word, "conclusion": conclusion, "scope": scope},
            "reason": {"kind": reason_kind, "text": reason_text}, "by": by, "how": how, "at": at, "until": until,
            "path": path, "carries": carries}
    if source is not None:
        body["source"] = source
    if target.get("value") and "time" not in body["target"]:
        body["target"]["time"] = value.time
    if content is not None:
        body["content"] = dict(content)
    else:
        body["seen"] = {"version": rec.version, "depend": current_depend(vessel, body["target"], question, scope)}
    if unit is not None:
        prefix = unit.pointer.split(".")[0]
        body["siblings"] = sorted(u.pointer for u in rec.units if u.pointer.split(".")[0] == prefix)
    if extra:
        if not isinstance(extra, dict) or any(k in body or k == "id" for k in extra):
            raise RegisterRefused("register_args_invalid", "extra")
        body.update(extra)
    body["id"] = new_id(body)
    return body


def _content_depend(vessel: V.Vessel, j: dict):
    rec, kind, _unit, _value = _locate(vessel, j["target"])
    if rec is None or j["target"].get("unit"):
        return None
    q = V.question_of(kind, j["question"])
    fields = q.depend_fields() if q else None
    if not fields or any(k not in j["content"] for k in fields):
        return "content-incomplete"
    return V.fp({k: j["content"].get(k) for k in fields})


def evaluate(vessel: V.Vessel, j: dict) -> str:
    """`effective`（効いている）・`stale`（依る所が変わった）・`vanished`（指し先が無い）・`unverifiable`
    （よその台帳を指す判断。この台帳の器では確かめられない。設計 §5.4。消えた指し先とは数えない）。"""
    if (j.get("target") or {}).get("ledger"):
        return "unverifiable"
    if is_erased(j):
        # 消す求めで印の形にした行: 対象の版を確かめられないので、広げる向き（よい・止めない）には効かない。
        # 番号に付く「だめ」は、狭める向きにだけ効いたまま（指示 段 5 §3.4）。
        conclusion, _reason = conclusion_of(vessel, j)
        return "effective" if conclusion == "bad" else "stale"
    now = current_depend(vessel, j["target"], j["question"], scope_said(j))
    if now is None:
        return "vanished"
    if "content" in j:
        return "effective" if _content_depend(vessel, j) == now else "stale"
    return "effective" if (j.get("seen") or {}).get("depend") == now else "stale"


def summary(vessel: V.Vessel, judgments: list) -> dict:
    counts = {"effective": 0, "stale": 0, "vanished": 0, "unverifiable": 0}
    for j in judgments:
        counts[evaluate(vessel, j)] += 1
    return counts


# ------------------------------------------------------------------ 問いに答える

def conclusion_of(vessel: V.Vessel, j: dict):
    """判断の結論と理由の種類 `(結論 or None, 理由の種類)`。結論を書いた判断はそれ。語だけの判断は憲法で問いごとに
    結論へ当てる（当たらなければ `(None, "word_unmapped")`）。段 3 もこれを呼ぶ。"""
    answer = j.get("answer") or {}
    if answer.get("conclusion") in V.CONCLUSIONS:
        return answer["conclusion"], (j.get("reason") or {}).get("kind")
    table = vessel.constitution.get(j["question"]) or vessel.constitution.get(j["question"].split("@")[0]) or {}
    meaning = table.get(answer.get("word"))
    if meaning is None:
        return None, "word_unmapped"
    return meaning["conclusion"], meaning["reason"] or (j.get("reason") or {}).get("kind")


def _matching(judgments, target_pred, question):
    return [j for j in judgments if j.get("question") == question and target_pred(j["target"])]


def answer_record(vessel: V.Vessel, judgments: list, ref: str, question: str) -> dict:
    """記録への問いの答え。観測（書いてある語）は返すが、結論には使わない。"""
    rec = vessel.get(ref)
    kind = vessel.kinds[rec.kind]
    observed = [o for o in V.observations(kind, rec) if o["question"] == question and o["unit"] is None]
    mine = _matching(judgments, lambda t: t.get("record") == ref and not t.get("unit"), question)
    effective = [j for j in mine if evaluate(vessel, j) == "effective"]
    out = {"record": ref, "question": question, "observed": observed[0]["word"] if observed else None,
           "judgments": len(mine), "effective": len(effective)}
    if not effective:
        reason = "no_inspection_for_new_version" if mine else "no_judgment"
        return {**out, "conclusion": "pending", "reason_kind": reason, "word": None}
    last = sorted(effective, key=lambda j: (j.get("at") or "", j["id"]))[-1]
    conclusion, reason = conclusion_of(vessel, last)
    if conclusion is None:
        return {**out, "conclusion": "pending", "reason_kind": reason, "word": (last.get("answer") or {}).get("word")}
    return {**out, "conclusion": conclusion, "reason_kind": reason, "word": (last.get("answer") or {}).get("word"),
            "by": last.get("by"), "how": last.get("how")}


def answer_value(vessel: V.Vessel, judgments: list, ref: str, unit_pointer: str, name: str, *,
                 question: str = "reads_right", as_scope=None, as_time=None) -> dict:
    """値を「この範囲・この時点の値として」使えるか（段 1 の最小の形。用途の全部は段 3）。

    行（単位）への判断は、範囲が行の鍵と同じ値にだけ及ぶ。範囲を別に持つ値は、範囲の欄か、判断に人が書いた範囲が
    無ければ「範囲が未宣言」。時点は値ごと（取った日は時点の代わりにしない）。"""
    as_scope = _scope_arg(as_scope)
    rec = vessel.get(ref)
    kind = vessel.kinds[rec.kind]
    unit = V.unit_at(rec, unit_pointer)
    if unit is None:
        raise RegisterRefused("register_record_missing", "unit")
    values = [v for v in V.values_of(rec, kind, unit) if v.name == name]
    if not values:
        raise RegisterRefused("register_record_missing", "value")
    value = values[0]
    missing = value.value is None or value.value == ""
    base = {"record": ref, "unit": unit_pointer, "value": name, "time": value.time, "scope_kind": value.scope_kind,
            "missing": missing}

    def on_value(t):
        return (t.get("record") == ref and t.get("unit") == unit_pointer and t.get("value") == name
                and _same_time(t.get("time"), value.time))

    def on_unit(t):
        return t.get("record") == ref and t.get("unit") == unit_pointer and not t.get("value")

    value_js = [j for j in _matching(judgments, on_value, question) if evaluate(vessel, j) == "effective"]
    row_js = [j for j in _matching(judgments, on_unit, question) if evaluate(vessel, j) == "effective"]
    stale = [j for j in _matching(judgments, lambda t: on_value(t) or on_unit(t), question)
             if evaluate(vessel, j) != "effective"]
    # 判断ごとに、その判断が言う範囲を 1 つ決める（範囲と結論は同じ 1 つの判断から取る）。
    #   値への判断: 範囲の欄があればその範囲。無ければ判断に人が書いた範囲。どちらも無ければ範囲が未宣言（使わない）。
    #   行への判断: 範囲が行の鍵と同じ値にだけ及ぶ（範囲を別に持つ値には及ばない・試験 1 (d)）。
    scoped, conflict = [], False
    for j in value_js:
        said = scope_said(j)
        if value.scope_form is not None:
            if said is not None:
                conflict = True  # 範囲が台帳で決まっている値に、人が範囲を書いた判断（食い違い）は使わない
                continue
            scoped.append((value.scope, j))
        elif said is not None:
            scoped.append((said, j))
    if value.scope_kind == "key" and value.scope_form is not None and \
            name in V.unit_fields(kind, unit, V.question_of(kind, question)):
        # 行への判断は、その問いが依る欄の値にだけ当たる（依らない値は、変わっても判断が古くならないので）。
        for j in row_js:
            if scope_said(j) is not None:
                conflict = True  # 行への判断は範囲を書けない
                continue
            scoped.append((value.scope, j))
    if not scoped:
        if conflict:
            return {**base, "usable": False, "conclusion": "pending", "reason_kind": "scope_conflict",
                    "scope": value.scope}
        if value.scope_form is None and (value_js or row_js):
            # 判断はあるが、範囲の決まらない値に範囲を言っていない（範囲の欄・範囲の軸の値も、人が書いた範囲も無い）。
            return {**base, "usable": False, "conclusion": "pending", "reason_kind": "scope_undeclared",
                    "scope": None}
        return {**base, "usable": False, "conclusion": "pending", "scope": value.scope,
                "reason_kind": "no_inspection_for_new_version" if stale else "no_judgment"}

    def latest(pairs):
        return sorted(pairs, key=lambda p: (p[1].get("at") or "", p[1]["id"]))[-1]
    pick = latest(scoped)
    if as_scope is not None:
        asked = [p for p in scoped if p[0] == as_scope]
        if not asked:
            scope, last = pick
            conclusion, _reason = conclusion_of(vessel, last)
            return {**base, "usable": False, "conclusion": conclusion, "reason_kind": "scope_mismatch",
                    "scope": scope}
        pick = latest(asked)
    scope, last = pick
    conclusion, reason = conclusion_of(vessel, last)
    if conclusion is None:
        return {**base, "usable": False, "conclusion": "pending", "reason_kind": reason, "scope": scope}
    if as_time is not None and not _asked_time(value.time, as_time):
        return {**base, "usable": False, "conclusion": conclusion, "reason_kind": "time_mismatch", "scope": scope}
    if missing:
        # 欠けを読んだ判断（「ここは空で合っている」）は判断として残すが、値としては使えない。
        return {**base, "usable": False, "conclusion": conclusion, "reason_kind": "value_missing", "scope": scope}
    return {**base, "usable": conclusion == "good", "conclusion": conclusion, "reason_kind": reason, "scope": scope}


# ------------------------------------------------------------------ 結びの候補と、名指しの結び

def candidates(vessel: V.Vessel, judgments: list) -> list:
    """消えた単位（の値）への判断ごとに、同じ鍵で今現れている指し先を候補として並べる。結ばない。
    鍵の指し先 `k:` と、鍵で決まらない行の指し先 `kc:` は同じ鍵どうしで並べる（同じ鍵の行が足されて `k:` が `kc:` に
    替わっても候補に出る）。`same_depend` は、候補のうち今の依る所が判断の見た依る所と同じ物。"""
    out = []
    for j in judgments:
        target = j["target"]
        if not target.get("unit") or evaluate(vessel, j) != "vanished":
            continue
        try:
            rec = vessel.get(target["record"])
        except RegisterRefused:
            continue
        family = _family(target["unit"])
        before = set(j.get("siblings") or [])
        now = sorted({u.pointer for u in rec.units if _family(u.pointer) == family})
        appeared = [p for p in now if p not in before]
        seen = (j.get("seen") or {}).get("depend")
        same = [p for p in appeared
                if seen is not None and current_depend(vessel, dict(target, unit=p), j["question"], scope_said(j)) == seen]
        out.append({"judgment": j["id"], "record": target["record"], "vanished": target["unit"],
                    "value": target.get("value"), "appeared": appeared, "same_depend": same})
    return out


def _family(pointer: str) -> str:
    """同じ鍵の指し先の組（`k:<鍵>` と `kc:<鍵>.<姿>` は同じ組）。`i:` の番号は点を含みうる（番号の字に点を許す）
    ので割らず、番号そのものを組にする（別の番号は同じ鍵ではない）。"""
    if pointer.startswith("i:"):
        return pointer
    head = pointer.split(".")[0]
    return "k:" + head[3:] if head.startswith("kc:") else head


def carry(vessel: V.Vessel, j: dict, to_unit: str, *, by: str, at: str, changed_by: str | None) -> dict:
    """人が名指しして、前の判断の元の検収を、新しい指し先へ結ぶ**新しい判断**（前の判断は書き換えない）。
    中身を変えた者と同じ者は結べない。変えた者が分からないとき（`changed_by` が空）は、同じ者でないと確かめられない
    ので結ばない（変えた者を名指しして呼ぶ）。"""
    if not isinstance(changed_by, str) or not changed_by.strip():
        raise RegisterRefused("register_carry_unnamed", "changed_by")
    if by == changed_by:
        raise RegisterRefused("register_judgment_same_person", "by")
    target = dict(j["target"], unit=to_unit)
    target.pop("time", None)
    answer = j.get("answer") or {}
    reason = j.get("reason") or {}
    return make(vessel, target, j["question"], by=by, at=at, path="carry", word=answer.get("word"),
                conclusion=answer.get("conclusion"), scope=answer.get("scope"), reason_kind=reason.get("kind"),
                reason_text=reason.get("text"), how=j.get("how"), carries=j["id"])


def accept_baseline(vessel: V.Vessel, kind_name: str, question: str, *, by: str, at: str) -> list:
    """基準の受け入れ（設計 §8.2 の 5）: その版に書いてあった状態を、明示の受け入れとして 1 回で受ける。
    受け入れた者は、版を見せられて受け入れる（呼ぶ側の責め）。"""
    kind = vessel.kinds[kind_name]
    out = []
    for rec in vessel.all_records:
        if rec.kind != kind_name or rec.id is None:
            continue
        for obs in V.observations(kind, rec):
            if obs["question"] != question:
                continue
            target = {"record": rec.ref, "unit": obs["unit"]}
            out.append(make(vessel, target, question, by=by, at=at, path="acceptance", word=obs["word"],
                            reason_kind=obs["reason_kind"]))
    return out


# ------------------------------------------------------------------ 検収の記録を持つ表から判断を読む

def inspections(vessel: V.Vessel) -> list:
    """検収の記録を持つ表から作った判断（`inspection_report` の `judgments`）。"""
    return inspection_report(vessel)["judgments"]


def inspection_report(vessel: V.Vessel) -> dict:
    """種類の宣言 `inspects` を持つ表（こぴちゃの中央の判断）の行ごとに、`follow` の指す欄を順にたどって
    対象の記録を見つけ、行の中身を「確かめた中身そのもの」として持つ検収の記録にする（書かない）。
    `{"judgments": [...], "skipped": [{"source", "reason"}]}`。

    中身は読んだ値のまま（空のセル "" も、JSON の明示の null も値。対象と合えば効く）。判断にしない行と理由:
    - `source_problem`: 行そのものに読みの問題がある（表の幅が違う行の欠けたセルは値でなく欠け。行の番号の
      重なりは、行の中身にも、たどる先にも効かないので除く）
    - `content_missing`: 中身の欄が行に無い（欄の欠落。明示の null とは別）
    - `target_blank`: たどる欄の並び・区切りの中に空の要素がある（指す先の一部の欠け）
    - `target_duplicated`: たどる先に同じ番号の記録が 2 つ以上ある
    - `target_missing`: たどる先が 1 つでも無い（残った先だけで判断にしない）・1 つも無い
    - `target_problem`: たどった先（途中の段も最後も）の記録に読みの問題がある（欠けたセルを読んだ null と
      取り違えない）
    - `target_split`: たどった先が 2 つ以上の記録に分かれる
    - `required_empty`: `required` の欄（既定は問いの状態の欄）が空か null
    - `target_unrouted`: たどる欄の先の種類が決まらない（`to_by` のどれにも当たらない。段 4 G15）
    状態の語は観測と同じ直し方（`word_of`）。効くかは `evaluate`（中身の指紋が今の依る所と合うか）で決まる。

    `content_by`（段 4 G16）の表は、行の「直した欄の名前」と「新しい中身」を、先の記録の「その欄がその中身である
    版」を確かめた検収の記録にする。結ばずに「版を示していない」（`unversioned`）に数える行と理由:
    `field_missing`（欄の名前が字でない・無い）・`field_compound`（2 つ以上の欄を 1 つの字で持つ。欄の名前の形でない）・
    `field_not_in_target`（先の記録にその欄が無い）・`value_missing`（新しい中身の欄が無い）。"""
    from .checks import get as _get_path
    out, skipped, unversioned = [], [], []
    for rec in vessel.all_records:
        kind = vessel.kinds[rec.kind]
        spec = kind.inspects
        if spec is None or rec.id is None or rec.values is None:
            continue

        def skip(reason):
            skipped.append({"source": rec.ref, "reason": reason})
        if any(word != "id_duplicate" for word in rec.problems):
            # 読みの問題のある行（幅の違う行の欠けたセルは値でなく欠け）。番号の重なりは行の中身に効かないので除く。
            skip("source_problem")
            continue
        by_row = spec.get("content_by")
        if by_row is None:
            if any(from_field not in rec.values for from_field in spec["content"].values()):
                skip("content_missing")
                continue
            content = {to_field: rec.values[from_field] for to_field, from_field in spec["content"].items()}
        else:
            content = None
        here, reason = [rec], None
        for step in spec["follow"]:
            nxt = []
            for one in here:
                found_here = 0
                for pointer in vessel.kinds[one.kind].pointers:
                    if pointer.get("field") != step:
                        continue
                    targets, blanks, lost = V.pointer_refs(one, pointer)
                    found_here += len(targets) + blanks + len(lost)
                    if blanks:
                        reason = "target_blank"
                    if lost:
                        reason = reason or "target_unrouted"
                    for to, target_id in targets:
                        ref = f"{to}:{target_id}"
                        found = vessel.unique(ref)
                        if vessel.is_duplicated(ref):
                            reason = reason or "target_duplicated"
                        elif found is None:
                            reason = reason or "target_missing"
                        elif found.problems:
                            # たどった先（途中も最後も）に読みの問題: 欠けたセルを中身と比べない。
                            reason = reason or "target_problem"
                        else:
                            nxt.append(found)
                if not found_here:
                    reason = reason or "target_missing"  # この枝は 0 件（空のセル・欄が無い）。ほかの枝で埋めない
            here = nxt
            if reason:
                break
            if not here:
                reason = "target_missing"
                break
        if reason:
            skip(reason)
            continue
        refs = sorted({r.ref for r in here})
        if len(refs) != 1:
            skip("target_split")
            continue
        target_kind = vessel.kinds[here[0].kind]
        if by_row is not None:
            name = _get_path(rec.values, by_row["field"])
            why = None
            if not isinstance(name, str) or not name.strip():
                why = "field_missing"
            elif not V.FIELD.fullmatch(name.strip()):
                why = "field_compound"
            elif here[0].values is None or name.strip() not in here[0].values:
                why = "field_not_in_target"
            elif not _has_path(rec.values, by_row["value"]):
                why = "value_missing"
            if why:
                unversioned.append({"source": rec.ref, "reason": why, "record": refs[0]})
                continue
            content = {name.strip(): _get_path(rec.values, by_row["value"])}
        q = V.question_of(target_kind, spec["question"])
        required = spec["required"] if spec["required"] is not None else (
            [q.state] if q is not None and q.state in content else [])
        if any(content.get(name) in (None, "") for name in required):
            skip("required_empty")
            continue
        word = V.word_of(content.get(q.state)) if q is not None and q.state in content else None
        out.append(make(vessel, {"record": refs[0]}, spec["question"], by=spec["by"], at=None, word=word,
                        content=content, source=rec.ref))
    return {"judgments": out, "skipped": skipped, "unversioned": unversioned}


def _has_path(values, path: str) -> bool:
    """点でつないだ道の先に欄が在るか（明示の null は在る）。"""
    here = values
    for step in path.split("."):
        if not isinstance(here, dict) or step not in here:
            return False
        here = here[step]
    return True
