"""判断の記録の最小の形と、判断が効いているかの読み（設計 §0.2・§5.3・§10 の段 1）。

- 判断 = 「どの記録（単位・値）の・どの版について・どの問いに・誰が・どう照らして・いつ・台帳の語と条件・
  理由（種類と説明）・いつまで」。置き場は台帳の `.thth/judgments.jsonl`（1 行 1 件・足していくだけ）。
  **段 1 の命令は判断を書かない**（privacy への影響なし）。書くのは試験と、段 3 の命令（`judge`・`accept`）。
- 判断は「見たときの版」と「依る所の指紋」を持つ。依る所が今と違えば**古い判断**（効かない・消さない）。
  指し先が今の台帳に無ければ**消えた指し先**への判断（引き継がない）。
- **書いてあるだけの状態は観測で、判断ではない**（`vessel.observations`）。判断になるのは、検収の記録（対象の版を
  示す: 指紋か、確かめた中身そのもの）か、明示の受け入れだけ。
- 結論（よい・だめ・まだ）は問いに答えるときに返す。保存するのは台帳の語と条件。語と結論の対応は憲法。
- **鍵で決まらない単位の訂正は結ばない。** 結びの候補（同じ鍵で、消えた指し先と現れた指し先）を見せるだけ。
  結ぶのは人が名指ししたときだけ（`carry`。直した者と同じ者は結べない）。

網に出ない。
"""
from __future__ import annotations

import hashlib
import json
import os

from . import vessel as V
from .errors import RegisterRefused

FILE = "judgments.jsonl"
SCHEMA = "thth.judgment/1"
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
        except ValueError:
            raise RegisterRefused("register_judgment_invalid", f"line {number}") from None
        if not isinstance(item, dict) or item.get("schema") != SCHEMA or not isinstance(item.get("target"), dict):
            raise RegisterRefused("register_judgment_invalid", f"line {number}")
        out.append(item)
    return out


def append(root: str, items: list) -> None:
    """試験と段 3 の命令が使う（段 1 の命令は呼ばない）。足すだけ。"""
    path = path_of(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def _new_id(body: dict) -> str:
    return "j-" + hashlib.sha256(V.canonical(body).encode("utf-8")).hexdigest()[:16]


# ------------------------------------------------------------------ 対象と依る所

def _locate(vessel: V.Vessel, target: dict):
    """`(記録, 種類, 単位 or None, 値 or None)`。指し先が無ければ None を含む。"""
    try:
        rec = vessel.get(target["record"])
    except RegisterRefused:
        return None, None, None, None
    kind = vessel.kinds[rec.kind]
    unit = value = None
    if target.get("unit"):
        unit = V.units_by_pointer(rec).get(target["unit"])
        if unit is None:
            return rec, kind, None, None
        if target.get("value"):
            value = next((v for v in V.values_of(rec, kind, unit)
                          if v.name == target["value"] and ("time" not in target
                                                            or _same_time(v.time, target["time"]))), None)
    return rec, kind, unit, value


def _same_time(a, b) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and str(a) == str(b))


def current_depend(vessel: V.Vessel, target: dict, question: str, scope_written=None):
    """今の依る所の指紋。指し先が無ければ None。"""
    rec, kind, unit, value = _locate(vessel, target)
    if rec is None:
        return None
    if target.get("unit"):
        if unit is None:
            return None
        if target.get("value"):
            if value is None:
                return None
            return value.depend(scope_override=scope_written if value.scope is None else None)
        return V.unit_depend(kind, unit)
    q = V.question_of(kind, question)
    return V.record_depend(kind, rec, q)


def make(vessel: V.Vessel, target: dict, question: str, *, by: str, at: str, path: str = "inspection",
         word=None, conclusion=None, scope=None, reason_kind=None, reason_text=None, how=None, until=None,
         content=None, carries=None) -> dict:
    """今の台帳の版を見て、判断を 1 件つくる（書かない）。`content` を渡すと「確かめた中身そのもの」を持つ
    検収の記録になり、指紋は持たない（効くかは、その中身の指紋が今の依る所と合うかで決まる）。"""
    if path not in PATHS:
        raise RegisterRefused("register_args_invalid", "path")
    rec, kind, unit, value = _locate(vessel, target)
    if rec is None or (target.get("unit") and unit is None) or (target.get("value") and value is None):
        raise RegisterRefused("register_record_missing", "target")
    body = {"schema": SCHEMA, "target": dict(target), "question": question,
            "answer": {"word": word, "conclusion": conclusion, "scope": scope},
            "reason": {"kind": reason_kind, "text": reason_text}, "by": by, "how": how, "at": at, "until": until,
            "path": path, "carries": carries}
    if target.get("value") and "time" not in body["target"]:
        body["target"]["time"] = value.time
    if content is not None:
        body["content"] = dict(content)
    else:
        body["seen"] = {"version": rec.version, "depend": current_depend(vessel, body["target"], question, scope)}
    if unit is not None:
        prefix = unit.pointer.split(".")[0]
        body["siblings"] = sorted(u.pointer for u in rec.units if u.pointer.split(".")[0] == prefix)
    body["id"] = _new_id(body)
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
    """`effective`（効いている）・`stale`（依る所が変わった）・`vanished`（指し先が無い）。"""
    now = current_depend(vessel, j["target"], j["question"], (j.get("answer") or {}).get("scope"))
    if now is None:
        return "vanished"
    if "content" in j:
        return "effective" if _content_depend(vessel, j) == now else "stale"
    return "effective" if (j.get("seen") or {}).get("depend") == now else "stale"


def summary(vessel: V.Vessel, judgments: list) -> dict:
    counts = {"effective": 0, "stale": 0, "vanished": 0}
    for j in judgments:
        counts[evaluate(vessel, j)] += 1
    return counts


# ------------------------------------------------------------------ 問いに答える

def _conclusion(vessel: V.Vessel, j: dict):
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
    conclusion, reason = _conclusion(vessel, last)
    if conclusion is None:
        return {**out, "conclusion": "pending", "reason_kind": reason, "word": (last.get("answer") or {}).get("word")}
    return {**out, "conclusion": conclusion, "reason_kind": reason, "word": (last.get("answer") or {}).get("word"),
            "by": last.get("by"), "how": last.get("how")}


def answer_value(vessel: V.Vessel, judgments: list, ref: str, unit_pointer: str, name: str, *,
                 question: str = "reads_right", as_scope=None, as_time=None) -> dict:
    """値を「この範囲・この時点の値として」使えるか（段 1 の最小の形。用途の全部は段 3）。

    行（単位）への判断は、範囲が行の鍵と同じ値にだけ及ぶ。範囲を別に持つ値は、範囲の欄か、判断に人が書いた範囲が
    無ければ「範囲が未宣言」。時点は値ごと（取った日は時点の代わりにしない）。"""
    rec = vessel.get(ref)
    kind = vessel.kinds[rec.kind]
    unit = V.units_by_pointer(rec).get(unit_pointer)
    if unit is None:
        raise RegisterRefused("register_record_missing", "unit")
    values = [v for v in V.values_of(rec, kind, unit) if v.name == name]
    if not values:
        raise RegisterRefused("register_record_missing", "value")
    value = values[0]
    base = {"record": ref, "unit": unit_pointer, "value": name, "time": value.time, "scope_kind": value.scope_kind}

    def on_value(t):
        return (t.get("record") == ref and t.get("unit") == unit_pointer and t.get("value") == name
                and _same_time(t.get("time"), value.time))

    def on_unit(t):
        return t.get("record") == ref and t.get("unit") == unit_pointer and not t.get("value")

    value_js = [j for j in _matching(judgments, on_value, question) if evaluate(vessel, j) == "effective"]
    row_js = [j for j in _matching(judgments, on_unit, question) if evaluate(vessel, j) == "effective"]
    unit_js = row_js if value.scope_kind == "key" else []
    stale = [j for j in _matching(judgments, lambda t: on_value(t) or on_unit(t), question)
             if evaluate(vessel, j) != "effective"]
    scope = value.scope
    if scope is None:
        written = [(j.get("answer") or {}).get("scope") for j in value_js if (j.get("answer") or {}).get("scope")]
        scope = written[-1] if written else None
    effective = value_js + unit_js
    if value.scope_kind == "separate" and scope is None and (value_js or row_js):
        # 行への判断はあるが、範囲を別に持つ値には及ばない（範囲の欄も、人が書いた範囲も無い）。
        return {**base, "usable": False, "conclusion": "pending", "reason_kind": "scope_undeclared", "scope": None}
    if not effective:
        return {**base, "usable": False, "conclusion": "pending", "scope": scope,
                "reason_kind": "no_inspection_for_new_version" if stale else "no_judgment"}
    last = sorted(effective, key=lambda j: (j.get("at") or "", j["id"]))[-1]
    conclusion, reason = _conclusion(vessel, last)
    if as_time is not None and not _same_time(value.time, as_time):
        return {**base, "usable": False, "conclusion": conclusion, "reason_kind": "time_mismatch", "scope": scope}
    if as_scope is not None and as_scope != scope:
        return {**base, "usable": False, "conclusion": conclusion, "reason_kind": "scope_mismatch", "scope": scope}
    return {**base, "usable": conclusion == "good", "conclusion": conclusion, "reason_kind": reason, "scope": scope}


# ------------------------------------------------------------------ 結びの候補と、名指しの結び

def candidates(vessel: V.Vessel, judgments: list) -> list:
    """消えた単位（の値）への判断ごとに、同じ鍵で今現れている指し先を候補として並べる。結ばない。"""
    out = []
    for j in judgments:
        target = j["target"]
        if not target.get("unit") or evaluate(vessel, j) != "vanished":
            continue
        try:
            rec = vessel.get(target["record"])
        except RegisterRefused:
            continue
        prefix = target["unit"].split(".")[0]
        before = set(j.get("siblings") or [])
        now = sorted({u.pointer for u in rec.units if u.pointer.split(".")[0] == prefix})
        appeared = [p for p in now if p not in before]
        out.append({"judgment": j["id"], "record": target["record"], "vanished": target["unit"],
                    "value": target.get("value"), "appeared": appeared})
    return out


def carry(vessel: V.Vessel, j: dict, to_unit: str, *, by: str, at: str, changed_by: str | None) -> dict:
    """人が名指しして、前の判断の元の検収を、新しい指し先へ結ぶ**新しい判断**（前の判断は書き換えない）。
    中身を変えた者と同じ者は結べない。"""
    if changed_by is not None and by == changed_by:
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
