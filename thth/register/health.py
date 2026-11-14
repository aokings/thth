"""台帳の器 段 8「健康」と「姉妹リンクの候補」（指示 `docs/指示_台帳の器_段8_健康と複数のメディア_2026-11-14.md` §2・§3、
設計 `docs/設計_台帳の器_2026-11-13.md` §5.8・§7.2・§7.3）。

**読むだけ。** 台帳のフォルダにも THTH の側の控えにも書かない。判断を足さない。網に出ない。

- `health(root)`: 設計 §5.8 の項目を、群（網・判断・封・期限・決まり・共通）ごとに「数と、多くて 20 件の例」で返す。
  台帳を器として読むのは 1 回（`open_vessel` を 1 回・判断の記録のバイトを 1 回）。土台の部品がまだ返さない物は
  数えずに `count: None` と `uncountable`（わけ）を出す。推測で数を作らない。
- `across(entries)`: 持ち主の台帳をまたいで、DOI か URL が同じ出典らしい物を並べる（候補だけ）。見る欄は記録の欄の
  名前 `doi`・`url`（既定）。どの記録にもその欄の無い種類は「見ていない」に出す。
- `summary_line(value)`: `--all --json` の 1 行（群ごとの数だけ。hub が束ねる形）。
- `sisters(root)`: 共通の台帳の対応の記録（種類 `correspondence`: `media_a`・`id_a`・`media_b`・`id_b`・`relation`）から、
  関係が `same` で、相手のメディアの記録がその読者の出し口で使える対を並べる。候補を出すだけで、判断は足さない。
  相手の台帳が手元に無ければ「確かめられない」。

数え方の細かい所（書き手の推し。指示 §2 の表の残り）:
- 線の薄い点は、指す欄を持つ種類と、指される種類の記録だけを見る（網に入らない種類は `not_in_net` に名前だけ）。
- 判断は `judgments.jsonl` の判断と、検収の記録を持つ表から作った判断（`judgment.inspections`）を合わせて見る。
- 「まだ」の理由・期限は、同じ対象と問いの効いている判断のうち最後の 1 件で数える（前の判断を蘇らせない。`route.answer`
  と同じ考え）。
- 範囲か時点が未宣言の値は、内側の単位の値のうち空でない物だけ（空のセルは値の主張でないので数えない）。
- 受け入れ待ちは、封のある出し口ごとに、封の位置より後ろの、その出し口に関わる判断のうち狭めない物（結論が「だめ」で
  ない物・印の形の行は除く）。封の無い出し口は `unsealed_outlets` に名前だけ出す（全部が待ちになり、数が意味を持たない）。
- 書き手だけの読み・同じ者の確かめは、取り込みの跡（`trail.entered`）で今の版を入れた取り込みの「集めた者」と、
  判断の「誰が」を比べる。控えの無い版・取り込みでない控え（keep）・跡の注の無い古い控えは数えず、わけごとに数を出す。
"""
from __future__ import annotations

import datetime
import os

from .. import jst
from . import checks as C
from . import erase as E
from . import judgment as J
from . import ledger as L
from . import questions as Q
from . import route as R
from . import trail as T
from . import vessel as V
from .errors import RegisterRefused

GROUPS = ("net", "judgment", "seal", "deadline", "rules", "common")
EXAMPLES_MAX = 20
NEAR_DAYS = 30
REVIEW_FIELD = "review_by"
SAME_FIELDS = (("doi", "doi"), ("url", "url"))
CORRESPONDENCE = "correspondence"
RELATIONS = ("same", "related", "broader", "narrower")
_DOI_HEADS = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:")


def _item(count: int, examples=None, **extra) -> dict:
    return {"count": count, "examples": list(examples or [])[:EXAMPLES_MAX], **extra}


def _cannot(why: str, **extra) -> dict:
    """数えられない項目（推測で数を作らない）。"""
    return {"count": None, "examples": [], "uncountable": why, **extra}


def _now(as_of):
    return R.parse_time(as_of) if as_of is not None else jst.now_jst()


# ------------------------------------------------------------------ 判断の読み（1 回）

def _judgments(raw: bytes, root: str) -> tuple:
    """`(判断の並び, 断りの語 or None)`。段 3 の読みで断られる記録でも、形の合う行だけを読んで数える
    （健康は直す所を見せる物なので、1 行の崩れで全部を止めない。崩れは `judgments_problem` に出す）。"""
    try:
        return R.parse_judgments(raw, root), None
    except RegisterRefused as exc:
        problem = f"{exc.code}: {exc.detail}" if exc.detail else exc.code
    import json
    out = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if (isinstance(item, dict) and item.get("schema") == J.SCHEMA and isinstance(item.get("target"), dict)
                and not V.value_problem(item) and not J.shape_problem(item)):
            out.append(item)
    return out, problem


def _state(vessel: V.Vessel, j: dict) -> str:
    try:
        return J.evaluate(vessel, j)
    except RegisterRefused:
        return "vanished"


def _conclusion(vessel: V.Vessel, j: dict):
    try:
        return J.conclusion_of(vessel, j)
    except (RegisterRefused, KeyError, AttributeError):
        return None, "word_unmapped"


def _target_key(j: dict) -> tuple:
    t = j.get("target") or {}
    return (t.get("ledger"), t.get("record"), t.get("unit"), t.get("value"), j.get("question"))


def _latest(pairs: list) -> dict:
    """同じ対象と問いの判断のうち、最後の 1 件（日時を時刻として・同じなら記録の中で後に足した物）。"""
    best = {}
    for n, j in pairs:
        key = _target_key(j)
        rank = (R._when(j), n)
        if key not in best or rank > best[key][0]:
            best[key] = (rank, j)
    return {k: v[1] for k, v in best.items()}


def _jex(j: dict, **extra) -> dict:
    t = j.get("target") or {}
    out = {"judgment": j.get("id"), "record": t.get("record"), "question": j.get("question")}
    if t.get("unit"):
        out["unit"] = t["unit"]
    if t.get("value"):
        out["value"] = t["value"]
    out.update(extra)
    return out


# ------------------------------------------------------------------ 網

def _net(vessel: V.Vessel, graph: dict, root: str, thin_all: bool) -> dict:
    net_kinds = set()
    for kind in vessel.kinds.values():
        if kind.pointers:
            net_kinds.add(kind.name)
            for p in kind.pointers:  # 先の種類は `to` か、G15 の `to_by`（頭・map の値の全部）
                route = p.get("to_by") or {}
                net_kinds.update([p["to"]] if p.get("to") else
                                 list((route.get("prefix") or route.get("map") or {}).values()))
    degree = {}
    for e in graph["edges"]:
        degree[e["from"]] = degree.get(e["from"], 0) + 1
        degree[e["to"]] = degree.get(e["to"], 0) + 1
    thin, seen = [], set()
    for rec in vessel.all_records:
        if rec.id is None or rec.kind not in net_kinds or rec.ref in seen:
            continue
        seen.add(rec.ref)
        if degree.get(rec.ref, 0) <= 1:
            thin.append({"record": rec.ref, "kind": rec.kind, "lines": degree.get(rec.ref, 0)})
    by_kind = {}
    for t in thin:
        by_kind[t["kind"]] = by_kind.get(t["kind"], 0) + 1
    thin_item = _item(len(thin), thin, by_kind=dict(sorted(by_kind.items())),
                      not_in_net=sorted(k for k in vessel.kinds if k not in net_kinds))
    if thin_all:
        thin_item["all"] = thin
    dangling_by = {}
    for e in graph["dangling"]:
        name = f"{e['from'].split(':', 1)[0]}.{e.get('field') or '?'}"
        dangling_by[name] = dangling_by.get(name, 0) + 1
    dangling = _item(len(graph["dangling"]), [{"from": e["from"], "to": e["to"], "field": e.get("field")}
                                              for e in graph["dangling"]], by_pointer=dict(sorted(dangling_by.items())))
    try:
        log = Q.read(root)
    except RegisterRefused as exc:
        questions = _cannot(f"問いの記録が読めない（{exc.code}）")
    else:
        rows = list(Q.state(log).values())
        closed = {}
        for s in rows:
            if s["answered"]:
                word = s["answered"].get("result")
                closed[word] = closed.get(word, 0) + 1
        open_rows = [s["asked"] for s in rows if s["answered"] is None]
        questions = _item(len(open_rows), [{"question": a.get("id"), "hole": a.get("hole"), "row": a.get("row"),
                                            "at": a.get("at")} for a in open_rows],
                          closed=dict(sorted(closed.items())))
    return {"thin": thin_item, "dangling": dangling, "open_questions": questions}


# ------------------------------------------------------------------ 判断

def _writer(root: str, vessel: V.Vessel, effective: list) -> tuple:
    """`(書き手と同じ者の確かめ, 書き手だけの読み)`。取り込みの跡が言えない版は数えずに、わけごとに数える。"""
    cache, unc, trail_cache = {}, {}, {}   # trail_cache: 跡・控え・枚ごとの版の覚え（この 1 回の中だけ）
    selfs, by_record = [], {}
    for j in effective:
        t = j.get("target") or {}
        if t.get("ledger") or not isinstance(j.get("by"), str):
            continue
        rec = vessel.unique(t.get("record"))
        if rec is None:
            continue
        if rec.ref not in cache:
            try:
                cache[rec.ref] = T.entered(root, rec, cache=trail_cache)
            except (RegisterRefused, OSError):
                cache[rec.ref] = None
        info = cache[rec.ref]
        if not info or not info.get("kept"):
            why = "version_not_kept"        # 今の版が控えに無い（控えのあとに直に書かれた・控えが無い）
        elif info.get("what") not in T.INTAKES:
            why = "kept_not_intake"         # 今の版を入れたのが取り込みでなく控え（keep）: 書いた者が分からない
        elif not info.get("noted") or not info.get("by"):
            why = "no_trail_note"           # 跡の注の無い古い控え
        else:
            why = None
        if why:
            unc[why] = unc.get(why, 0) + 1
            continue
        same = j["by"] == info["by"]
        if same:
            selfs.append(_jex(j, by=j["by"], intake=info.get("what"), snapshot=info.get("snapshot")))
        by_record.setdefault(rec.ref, []).append(same)
    only = [{"record": ref} for ref, flags in sorted(by_record.items()) if flags and all(flags)]
    counted = sum(len(v) for v in by_record.values())
    if not counted and unc:
        reason = "取り込みの跡から書いた者が言えない（" + "・".join(f"{k} {n}" for k, n in sorted(unc.items())) + "）"
        return _cannot(reason, uncounted=unc), _cannot(reason, uncounted=unc)
    return (_item(len(selfs), selfs, uncounted=unc, counted=counted),
            _item(len(only), only, uncounted=unc, counted=counted))


def _undeclared(vessel: V.Vessel) -> dict:
    count, scope_n, time_n, examples = 0, 0, 0, []
    unit_kinds = [k for k in vessel.kinds.values() if k.units is not None]
    for rec in vessel.all_records:
        kind = vessel.kinds[rec.kind]
        if kind.units is None or rec.id is None:
            continue
        decl = kind.units
        for unit in rec.units:
            for name in V.value_fields(decl, unit):
                value = unit.item.get(name)
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                _kind, form, _label = V.scope_of(decl, unit, name)
                no_time = V.value_time(decl, unit, name) is None
                if form is None or no_time:
                    count += 1
                    scope_n += form is None
                    time_n += no_time
                    if len(examples) < EXAMPLES_MAX:
                        examples.append({"record": rec.ref, "unit": unit.pointer, "value": name,
                                         "missing": [w for w, bad in (("scope", form is None), ("time", no_time)) if bad]})
    note = None if unit_kinds else "内側の単位の宣言のある種類が無い（値は内側の単位の欄だけ）"
    return _item(count, examples, by={"scope": scope_n, "time": time_n}, **({"note": note} if note else {}))


def _judgment_group(root, vessel, js, pool, states, graph, js_problem) -> dict:
    effective = [j for j in pool if states[id(j)] == "effective"]
    stale = [j for j in pool if states[id(j)] == "stale"]
    vanished = [j for j in pool if states[id(j)] == "vanished"]
    eff_keys = set()
    for j in effective:
        t = j.get("target") or {}
        if not t.get("ledger") and not t.get("value"):
            eff_keys.add((t.get("record"), t.get("unit") or None, j.get("question")))
    observed, observed_records = [], set()
    for rec in vessel.all_records:
        if rec.id is None or rec.ref in observed_records:
            continue
        for obs in V.observations(vessel.kinds[rec.kind], rec):
            if (rec.ref, obs["unit"], obs["question"]) not in eff_keys:
                observed_records.add(rec.ref)
                observed.append({"record": rec.ref, "question": obs["question"], "unit": obs["unit"],
                                 "word": obs["word"]})
                break
    try:
        cands = J.candidates(vessel, js)
        candidates = _item(len(cands), cands)
    except RegisterRefused as exc:
        candidates = _cannot(f"結びの候補を出せない（{exc.code}）")
    self_check, writer_only = _writer(root, vessel, effective)
    latest = _latest([(n, j) for n, j in enumerate(pool) if states[id(j)] == "effective"])
    pending, by_reason = [], {}
    for j in latest.values():
        conclusion, reason = _conclusion(vessel, j)
        if conclusion == "pending":
            word = reason or "no_reason_kind"
            by_reason[word] = by_reason.get(word, 0) + 1
            pending.append(_jex(j, reason_kind=word))
    by_rec = {}
    for j in effective:
        t = j.get("target") or {}
        if t.get("ledger") or j.get("question") == R.TAKE:
            continue
        q = j.get("question") or ""
        if q == R.STOP or q.startswith(R.STOP + "@"):
            continue
        by_rec.setdefault(t.get("record"), []).append(j.get("path") == "acceptance")
    baseline = [{"record": ref} for ref, flags in sorted(by_rec.items()) if flags and all(flags)]
    review = graph["review"]
    out = {
        "stale": _item(len(stale), [_jex(j) for j in stale]),
        "vanished": _item(len(vanished), [_jex(j) for j in vanished]),
        "observed_only": _item(len(observed), observed),
        "candidates": candidates,
        "self_check": self_check,
        "writer_only": writer_only,
        "pending_reasons": _item(len(pending), pending, by_reason=dict(sorted(by_reason.items()))),
        "recheck_due": _cannot("憲法に「調べ直す条件」を書く欄が無い（thth.constitution/1 は語・結論・理由の種類だけ）"),
        "baseline_only": _item(len(baseline), baseline),
        "undeclared_values": _undeclared(vessel),
        "provenance_review": _item(len(review), [{"from": e["from"], "to": e["to"], "field": e.get("field"),
                                                  "recorded": e.get("recorded"), "now": e.get("now")} for e in review]),
    }
    if js_problem:
        for item in out.values():
            item["judgments_problem"] = js_problem
    return out


# ------------------------------------------------------------------ 封

def _relevant(outlet: R.Outlet, j: dict) -> bool:
    t = j.get("target") or {}
    q = j.get("question") or ""
    ref = t.get("record") or ""
    if t.get("ledger"):
        return (t.get("ledger") == R.COMMON and q == R.TAKE and outlet.common is not None
                and ref.startswith(outlet.common + ":"))
    if ref.split(":", 1)[0] not in outlet.kinds:
        return False
    if q in (R.STOP, f"{R.STOP}@{outlet.name}"):
        return True
    asked = [qq for qq, _c in outlet.requires + outlet.fact]
    if outlet.reservation is not None:
        asked.append(outlet.reservation["question"])
    return q in asked


def _seals(root: str, outlets: dict, raw: bytes, js: list) -> dict:
    """出し口ごとの `(封 | None, 状態, 封の中の判断の数)`。署名の合わない封・判断の記録が続きでない封は封なし。"""
    out = {}
    for name in sorted(outlets):
        seal, state = R.trusted_seal(root, name)
        n = 0
        if seal is not None:
            jm = seal["body"]["judgments"]
            if len(raw) < jm["bytes"] or R.sha(raw[:jm["bytes"]]) != jm["sha256"]:
                seal, state = None, "judgments_rewritten"
            else:
                try:
                    n = len(R.parse_judgments(raw[:jm["bytes"]], root))
                except RegisterRefused:
                    seal, state = None, "judgments_unreadable"
        out[name] = (seal, state, n)
    return out


def _seal_group(root, vessel, js, outlets, seals, common, as_of, js_problem, outlets_problem) -> dict:
    if outlets_problem:
        pending = _cannot(f"出し口の宣言が読めない（{outlets_problem}）")
    elif not outlets:
        pending = _cannot("出し口の宣言（.thth/outlets.json）が無い")
    elif js_problem:
        pending = _cannot(f"判断の記録が段 3 の読みで断られる（{js_problem}）。封の中の位置を言えない")
    else:
        found, examples, unsealed = set(), [], []
        for name, outlet in sorted(outlets.items()):
            seal, state, n = seals[name]
            if seal is None:
                unsealed.append({"outlet": name, "seal": state})
                continue
            for j in js[n:]:
                if J.is_erased(j) or not _relevant(outlet, j):
                    continue
                if _conclusion(vessel, j)[0] == "bad":
                    continue      # 狭める物は封を待たない
                if j.get("id") not in found:
                    found.add(j.get("id"))
                examples.append(_jex(j, outlet=name))
        pending = _item(len(found), examples, unsealed_outlets=unsealed)
    takes = R._takes(js)
    if not takes:
        foreign = _item(0, [])
    elif common is None:
        foreign = _cannot("共通の台帳が分からない（--common で渡す）", takes=len(takes))
    else:
        names = {}
        for name, outlet in (outlets or {}).items():
            if outlet.common:
                names.setdefault(outlet.common, []).append(name)
        hits = []
        for ref in sorted(takes):
            for name in names.get(ref.split(":", 1)[0], []):
                stop = R._stop_state(common.judgments, len(common.judgments), None, ref, None, name, as_of)
                if stop is not None:
                    hits.append({"record": f"{R.COMMON}:{ref}", "outlet": name, "judgment": stop.get("id")})
                    break
        foreign = _item(len(hits), hits)
    try:
        status = E.status(root)
        rows = [r for r in status["requests"] if r["state"] != "finished"]
        erase = _item(status["unfinished"], [{"request": r["request"], "record": r["record"], "state": r["state"]}
                                             for r in rows])
    except (RegisterRefused, OSError, ValueError) as exc:
        erase = _cannot(f"消す求めの記録が読めない（{getattr(exc, 'code', type(exc).__name__)}）")
    return {"accept_pending": pending, "foreign_stops": foreign, "erase_unfinished": erase}


# ------------------------------------------------------------------ 期限

def _deadline_group(root, vessel, pool, states, outlets, as_of) -> dict:
    review, holders = [], 0
    today = as_of.astimezone(R.JST).date()
    for rec in vessel.all_records:
        if rec.id is None or not isinstance(rec.values, dict) or REVIEW_FIELD not in rec.values:
            continue
        holders += 1
        raw = rec.values.get(REVIEW_FIELD)
        day = None
        if isinstance(raw, str) and len(raw.strip()) == 10:
            try:
                day = datetime.date.fromisoformat(raw.strip())
            except ValueError:
                day = None
        if day is not None and day < today:
            review.append({"record": rec.ref, "review_by": raw})
    review_item = _item(len(review), review) if holders else _cannot(
        f"見直す日の欄（{REVIEW_FIELD}）を持つ記録が無い（種類の宣言に見直す日の欄を言う書き方が無い）")
    latest = _latest([(n, j) for n, j in enumerate(pool) if states[id(j)] == "effective"])
    near, passed = [], []
    for j in latest.values():
        if J.is_erased(j) or j.get("until") in (None, ""):
            continue
        end = R.until_end(j["until"])
        if end is None or as_of >= end:
            passed.append(_jex(j, until=j["until"]))
        elif end - as_of <= datetime.timedelta(days=NEAR_DAYS):
            near.append(_jex(j, until=j["until"]))
    in_last = []
    passed_refs = {p["record"] for p in passed}
    for name in sorted(outlets or {}):
        try:
            seal = R.read_seal(root, name)
        except RegisterRefused:
            continue
        if seal is None:
            continue
        for key in seal["body"]["items"]:
            if not isinstance(key, str):
                continue
            record, unit, as_ = R._parse_item_key(key)
            if record in passed_refs:
                in_last.append({"record": record, "unit": unit, "as": as_, "outlet": name,
                                "generation": seal["body"]["generation"]})
    return {"review_passed": review_item,
            "until_near": _item(len(near), near, days=NEAR_DAYS),
            "until_passed": _item(len(passed), passed),
            "passed_in_last_generation": _item(len({x["record"] for x in in_last}), in_last)}


# ------------------------------------------------------------------ 決まり

def _when_view(cond) -> dict | None:
    if not cond:
        return None
    out = {}
    for name, (op, arg, or_blank) in cond.items():
        out[name] = {op: (sorted(str(w) for w in arg) if isinstance(arg, (list, tuple, set, frozenset)) else arg)}
        if or_blank:
            out[name]["or_blank"] = True
    return out


def _rules_group(vessel, result) -> dict:
    decl = {(k.name, c["id"]): c for k in vessel.kinds.values() for c in (k.checks or [])}
    counts = {}
    for rec in vessel.all_records:
        counts[rec.kind] = counts.get(rec.kind, 0) + 1
    unmatched, failed, violating = [], [], []
    unverifiable = unresolved = 0
    for one in result["list"]:
        if one.get("failed"):
            failed.append({"kind": one["kind"], "id": one["id"], "check": one["check"], "level": one["level"]})
            continue
        unverifiable += one.get("unverifiable", 0)
        unresolved += one.get("unresolved", 0)
        if one.get("checked", 0) == 0:
            c = decl.get((one["kind"], one["id"])) or {}
            unmatched.append({"kind": one["kind"], "id": one["id"], "check": one["check"], "level": one["level"],
                              "when": _when_view(c.get("when")), "records_of_kind": counts.get(one["kind"], 0),
                              "note": one.get("note")})
        if one.get("violations"):
            violating.append({"kind": one["kind"], "id": one["id"], "level": one["level"],
                              "violations": one["violations"]})
    return {"unmatched_checks": _item(len(unmatched), unmatched),
            "failed_checks": _item(len(failed), failed),
            "unverifiable_pointers": _item(unverifiable, [], unresolved=unresolved),
            "violations": _item(result["error"] + result["warn"] + result["review"], violating,
                                by_level={"error": result["error"], "warn": result["warn"],
                                          "review": result["review"]})}


# ------------------------------------------------------------------ 共通

def _common_group(js, common, seals) -> dict:
    takes = R._takes(js)
    if not takes:
        pending = _item(0, [])
    elif common is None:
        pending = _cannot("共通の台帳が分からない（--common で渡す）", takes=len(takes))
    else:
        rows, missing = [], []
        for ref, j in sorted(takes.items()):
            taken = (j.get("seen") or {}).get("version")
            now = common.vessel.unique(ref)
            if now is None:
                missing.append({"record": f"{R.COMMON}:{ref}"})
            elif now.version != taken:
                rows.append({"record": f"{R.COMMON}:{ref}", "taken": taken, "now": now.version})
        pending = _item(len(rows), rows, missing_in_common=missing)
    unchecked = [{"outlet": name, "generation": seal["body"]["generation"]}
                 for name, (seal, _state, _n) in sorted(seals.items())
                 if seal is not None and seal["body"].get("common_unchecked")]
    return {"take_pending": pending,
            "common_unchecked": _item(len(unchecked), unchecked,
                                      not_seen="CI で共通の台帳を読めずに配った回は、配る物が台帳の外に置かれるので、"
                                               "台帳からは見えない（封を共通なしで作った出し口だけを数えた）")}


# ------------------------------------------------------------------ 共通の台帳の見つけ方

def find_common(common_root, root: str):
    """`--common` か、THTH の側の控えの `role: common` の台帳（1 つのときだけ）。見つからなければ None。"""
    if common_root is not None:
        L.check_walkable(common_root)
        return R.open_common(common_root)
    names = getattr(L, "common_names", None)
    if names is None:
        return None
    try:
        found = names()
    except RegisterRefused:
        return None
    if len(found) != 1:
        return None
    try:
        path = L.root_of(L.load(found[0]))
    except RegisterRefused:
        return None
    if os.path.abspath(path) == os.path.abspath(root):
        return None
    try:
        L.check_walkable(path)
    except RegisterRefused:
        return None
    return R.open_common(path)


# ------------------------------------------------------------------ 健康

def _open_vessel(root: str, *args):
    """歩く数に上限を置いてから器として開く（段 8 の検収の穴 3: 控えの道が `/` でも止まる。上限を超えたら
    `register_folder_too_wide`。`--all` と `--across` では、その台帳だけ読めないとして次へ進む）。"""
    L.check_walkable(root)
    return V.open_vessel(root, *args)


def health(root: str, *, as_of=None, common_root=None, decl_base=None, thin: bool = False, name=None) -> dict:
    """読むだけ。`{"register", "as_of", "records", "groups": {群: {項目: {"count", "examples", ...}}}}`。"""
    as_of = _now(as_of)
    vessel = _open_vessel(root, decl_base)
    raw = R.judgments_raw(root)
    js, js_problem = _judgments(raw, root)
    try:
        inspected = J.inspections(vessel)
    except RegisterRefused:
        inspected = []
    pool = js + inspected
    states = {id(j): _state(vessel, j) for j in pool}
    graph = V.links(vessel)
    try:
        outlets, outlets_problem = R.load_outlets(root, vessel), None
    except RegisterRefused as exc:
        outlets, outlets_problem = {}, exc.detail or exc.code
    try:
        common = find_common(common_root, root)
        common_problem = None
    except RegisterRefused as exc:
        common, common_problem = None, exc.code
    seals = _seals(root, outlets, raw, js) if not js_problem else {}
    groups = {
        "net": _net(vessel, graph, root, thin),
        "judgment": _judgment_group(root, vessel, js, pool, states, graph, js_problem),
        "seal": _seal_group(root, vessel, js, outlets, seals, common, as_of, js_problem, outlets_problem),
        "deadline": _deadline_group(root, vessel, pool, states, outlets, as_of),
        "rules": _rules_group(vessel, C.run(vessel, js)),
        "common": _common_group(js, common, seals),
    }
    out = {"register": name or root, "as_of": as_of.isoformat(timespec="seconds"),
           "records": sum(1 for r in vessel.all_records if r.id is not None),
           "judgments": len(js), "inspections": len(inspected), "groups": groups}
    if js_problem:
        out["judgments_problem"] = js_problem
    if common_problem:
        out["common_problem"] = common_problem
    return out


def summary_line(value: dict) -> dict:
    """`--all --json` の 1 行: 群ごとの数（数えられた項目の和）と、数えられなかった項目の名前だけ。"""
    groups, uncountable = {}, []
    for group in GROUPS:
        total = 0
        for item_name, item in value["groups"][group].items():
            if item["count"] is None:
                uncountable.append(f"{group}.{item_name}")
            else:
                total += item["count"]
        groups[group] = total
    return {"register": value["register"], "as_of": value["as_of"], "groups": groups, "uncountable": uncountable}


# ------------------------------------------------------------------ 台帳をまたぐ（--across）

def _doi(raw) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    word = raw.strip().lower()
    for head in _DOI_HEADS:
        if word.startswith(head):
            word = word[len(head):].strip()
    return word or None


def _url(raw) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    word = raw.strip().lower().rstrip("/")
    return word or None


def across(entries: list) -> dict:
    """`entries` = `[(台帳の名前, Vessel)]`。DOI か URL が同じで、2 つ以上の台帳にまたがる物を並べる（候補だけ）。"""
    norm = {"doi": _doi, "url": _url}
    index, looked = {}, {}
    for name, vessel in entries:
        seen_kinds = looked.setdefault(name, {})
        for rec in vessel.all_records:
            if rec.id is None or not isinstance(rec.values, dict):
                continue
            for field, label in SAME_FIELDS:
                if field not in rec.values:
                    continue
                seen_kinds.setdefault(rec.kind, set()).add(field)
                key = norm[label](rec.values.get(field))
                if key:
                    members = index.setdefault(f"{label}:{key}", [])
                    member = {"register": name, "record": rec.ref}
                    if member not in members:
                        members.append(member)
    same = [{"key": key, "members": members} for key, members in sorted(index.items())
            if len({m["register"] for m in members}) >= 2]
    not_looked = {name: sorted(k for k in vessel.kinds if k not in looked.get(name, {})) for name, vessel in entries}
    return {"fields": [f for f, _l in SAME_FIELDS], "same": same,
            "looked": {name: {k: sorted(v) for k, v in sorted(kinds.items())} for name, kinds in looked.items()},
            "not_looked": not_looked}


def owner_ledgers() -> list:
    """`[(名前, 台帳の根)]`（THTH の側の控えの一覧。読むだけ）。読めない控えは飛ばす。"""
    out = []
    for name in L.list_names():
        try:
            out.append((name, L.root_of(L.load(name))))
        except RegisterRefused:
            continue
    return out


def across_owner(root: str, name: str, vessel: V.Vessel | None = None) -> dict:
    entries, unreadable = [], []
    here = os.path.abspath(root)
    have_here = False
    for other, path in owner_ledgers():
        if os.path.abspath(path) == here:
            have_here = True
            entries.append((other, vessel or _open_vessel(root)))
            continue
        try:
            entries.append((other, _open_vessel(path)))
        except (RegisterRefused, V.DeclarationError, OSError) as exc:
            unreadable.append({"register": other, "why": getattr(exc, "code", None) or type(exc).__name__})
    if not have_here:
        entries.insert(0, (name, vessel or _open_vessel(root)))
    value = across(entries)
    value["unreadable"] = unreadable
    return value


# ------------------------------------------------------------------ 姉妹リンクの候補

def _project_of(name: str | None, root: str) -> str | None:
    if name is not None and not ("/" in name or name.startswith(("~", "."))):
        try:
            return L.load(name)["project"]
        except (RegisterRefused, KeyError):
            return None
    here = os.path.abspath(root)
    for other, path in owner_ledgers():
        if os.path.abspath(path) == here:
            try:
                return L.load(other)["project"]
            except (RegisterRefused, KeyError):
                return None
    return None


def _usable_outlets(path: str, ref: str, as_of, cache: dict) -> tuple:
    """`(記録が在るか, 読者の出し口で使える出し口の名前の並び)`。封は確かめてから使う（`route.use` と同じ計算）。"""
    if path not in cache:
        vessel = _open_vessel(path)
        try:
            outlets = R.load_outlets(path, vessel)
        except RegisterRefused:
            outlets = {}
        cache[path] = {"vessel": vessel, "outlets": outlets, "ctx": {}}
    entry = cache[path]
    rec = entry["vessel"].unique(ref)
    if rec is None:
        return False, []
    usable = []
    for name, outlet in sorted(entry["outlets"].items()):
        if outlet.audience != "reader" or rec.kind not in outlet.kinds:
            continue
        if name not in entry["ctx"]:
            seal, _state = R.trusted_seal(path, name)
            entry["ctx"][name] = R.context(path, as_of, seal=seal)
        ctx = entry["ctx"][name]
        if outlet.level == "unit":
            ok = any(R.decide(ctx, outlet, rec.ref, u.pointer)["as"] is not None for u in rec.units)
        else:
            ok = R.decide(ctx, outlet, rec.ref)["as"] is not None
        if ok:
            usable.append(name)
    return True, usable


def sisters(root: str, *, name=None, media=None, common_root=None, as_of=None) -> dict:
    """読むだけ。対応の記録から、関係が `same` で相手の記録が読者の出し口で使える対（候補）を並べる。"""
    as_of = _now(as_of)
    project = media or _project_of(name, root)
    if not project:
        raise RegisterRefused("register_args_invalid", "media")
    common = find_common(common_root, root)
    if common is None:
        raise RegisterRefused("register_common_unusable", "common")
    if CORRESPONDENCE not in common.vessel.kinds:
        raise RegisterRefused("register_common_unusable", "correspondence")
    own = _open_vessel(root)
    here = os.path.abspath(root)
    candidates, not_usable, unverifiable, skipped = [], [], [], {}
    cache = {}
    ledgers_of = {}
    for rec in common.vessel.all_records:
        if rec.kind != CORRESPONDENCE or rec.id is None or not isinstance(rec.values, dict):
            continue
        v = rec.values
        relation = v.get("relation")
        sides = [(v.get("media_a"), v.get("id_a")), (v.get("media_b"), v.get("id_b"))]
        mine = [s for s in sides if s[0] == project]
        if len(mine) != 1:
            continue                       # このメディアの対応でない（か、同じメディアどうし）
        if relation != "same":
            word = relation if relation in RELATIONS else "unknown"
            skipped[word] = skipped.get(word, 0) + 1
            continue
        mine_ref = mine[0][1]
        partner_media, partner_ref = next(s for s in sides if s[0] != project)
        row = {"correspondence": rec.ref, "mine": mine_ref,
               "mine_exists": isinstance(mine_ref, str) and own.unique(mine_ref) is not None,
               "partner": {"media": partner_media, "record": partner_ref}}
        if not isinstance(partner_media, str) or not isinstance(partner_ref, str):
            not_usable.append({**row, "why": "correspondence_incomplete"})
            continue
        if partner_media not in ledgers_of:
            names = []
            for other, path in owner_ledgers():
                if os.path.abspath(path) == here or os.path.abspath(path) == common.root:
                    continue
                try:
                    if L.load(other)["project"] == partner_media:
                        names.append((other, path))
                except (RegisterRefused, KeyError):
                    continue
            ledgers_of[partner_media] = names
        found_any, usable_at = False, None
        for other, path in ledgers_of[partner_media]:
            exists, usable = _usable_outlets(path, partner_ref, as_of, cache)
            found_any = found_any or exists
            if usable:
                usable_at = (other, usable)
                break
        if not ledgers_of[partner_media]:
            unverifiable.append({**row, "why": "partner_ledger_missing"})
        elif usable_at is None:
            not_usable.append({**row, "why": "not_usable_for_readers" if found_any else "record_missing"})
        else:
            row["partner"].update(register=usable_at[0], outlets=usable_at[1])
            candidates.append(row)
    return {"media": project, "as_of": as_of.isoformat(timespec="seconds"), "candidates": candidates,
            "not_usable": not_usable, "unverifiable": unverifiable, "other_relations": dict(sorted(skipped.items()))}
