"""台帳の器 段 11 の B「訂正の 1 本の命令」（考察 `docs/考察_台帳の器の手間と利点_2026-11-15.md` §1 の R1）。

    thth register fix <台帳> <種類:番号> --set 欄=値 ... --reason-text <説明> --by <名前>
                      --outlet <出し口|all> --to <先> --as-of <日時> [--how <照らし方>] [--against <種類:番号> ...]
                      [--common <共通の台帳>] [--json]

1. **書く**: 表の 1 行を直す（`row set` と同じ道。触っていない行のバイトは変えない）。
2. **名指し**: 出し口ごとに受け入れの 1 段めを計算し、外れる記録を一覧にする。判断を足す相手は、**指定した記録のうち、
   この命令で版が変わった物だけ**。ほかの記録が外れていれば判断せずに一覧に出し、1 段めで止める（段 11 の検収 危 1）。
   外れる記録ごとに、出し口の条件（`requires`・`fact`）のうち、今の判断で満たさない問いを挙げる。
3. **判断を足す**: 名指しした記録と問いに、1 つの理由（`--reason-text`・`--how`・`--against`）で判断を足す（足すだけ）。
   結論は出し口の条件が求める結論。留保（`reservation`）で載っていた記録には足さない（1 段めで止まる）。
   その問いの最後の判断が「だめ」（誰の物でも・前の版への物でも）なら、自動の「よい」を重ねず 1 段めで止める。

何も変えない fix（今と同じ値）は断る。引数と前提（記録・欄・`--against` の先・封・鍵）は表を書く前に確かめる。
4. **受け入れの 1 段め**を計算し直す。**外れる 0・入る 0・新しい判断はこの命令が足した物だけ**・旧い材料 0・
   採り入れ待ち 0、かつ回す出し口の全部が宣言で `fix_seal: true` のときだけ、同じ命令の中で封まで進める。
   それ以外は 1 段めで止め、出し口ごとの一覧と印（`accept --confirm` に渡す物）を出す（rc 1）。

柵は弱めない: 判断は記録に足し（誰が・いつ・何と照らして）、封は今までどおり `accept` の道で作り、CI の `verify` も
同じ。省くのは、verify 赤 → judge → accept 1 段め → accept 2 段め の往復だけ。
影響の候補（段 11 の F）: 直した記録と指す欄で 1 段の記録を名指しする（判断は足さない・無効にしない）。

網に出ない。
"""
from __future__ import annotations

from . import adopt as A
from . import route as R
from . import seal as S
from . import vessel as V
from .errors import RegisterRefused


def _targets(root: str, outlet: str, to) -> list:
    if R.is_all(outlet):
        return [(name, R.dest_under(root, name, to)) for name in R.all_outlets(root)]
    return [(outlet, to)]


def _needs(root: str, outlet: R.Outlet, ref: str, unit, as_of, common_root) -> list:
    """外れた記録（か単位）について、今の全部の判断で満たさない出し口の条件 `[(問い, 結論)]`。"""
    ctx = R.context(root, as_of, common_root=common_root, sealed_all=True)
    match = R._unit_match(ref, unit) if unit else R._record_match(ref)
    out = []
    for question, want in outlet.requires:
        got = R.answer(ctx, ctx.every, R._record_match(ref), question, ref)
        if not R._meets(got, want):
            out.append((question, want, None))
    for question, want in outlet.fact:
        got = R.answer(ctx, ctx.every, match, question, ref)
        if not R._meets(got, want):
            out.append((question, want, unit))
    return out


def _last_bad(root: str, ref: str, unit, question: str, as_of) -> bool:
    """その記録（か単位）とその問いの最後の判断が「だめ」か。版が変わって効かなくなった判断も見る（ほかの者が
    前の版に「だめ」と言った所へ、直した者が自動の「よい」を重ねないため。段 11 の検収 軽 1）。"""
    ctx = R.context(root, as_of, sealed_all=True)
    match = R._unit_match(ref, unit) if unit else R._record_match(ref)
    mine = [(n, j) for n, j in ctx.by_record(ctx.every).get(ref, [])
            if j.get("question") == question and match(j.get("target") or {})]
    if not mine:
        return False
    last = max(mine, key=lambda p: R._key(p, ctx.n_sealed))[1]
    return R.conclusion_of(ctx.vessel, last)[0] == "bad"


def fix(root: str, ref: str, values: dict, *, outlet: str, to, as_of, by: str, at: str, reason_text,
        how=None, against=None, common_root=None) -> dict:
    if not isinstance(reason_text, str) or not reason_text.strip():
        raise RegisterRefused("register_args_invalid", "reason_text")
    if not values:
        raise RegisterRefused("register_args_invalid", "fix_no_change")
    targets = _targets(root, outlet, to)
    vessel = V.open_vessel(root)
    outlets = R.load_outlets(root, vessel)
    # 書く前に、記録・照らした物があるか、何かを変えるか（表だけ直って判断の無い姿を残さない。段 11 の検収 軽 1）
    before = vessel.get(ref)
    for one in against or []:
        if vessel.unique(one) is None:
            raise RegisterRefused("register_record_missing", "against", record=one)
    if all(before.values.get(name) == value for name, value in values.items()):
        raise RegisterRefused("register_args_invalid", "fix_no_change", record=ref)
    for name, _to in targets:
        R.outlet_of(root, vessel, name)
    # 書く前に、どの出し口の封も読めるか（署名の合わない封は、書く前に断る。旧い材料などは 1 段めで止まる）
    for name, _dest in targets:
        _seal, state = R.trusted_seal(root, name)
        if state not in ("none", "verified", "salt"):
            R._refuse_unverified(name, state)
    if all(outlets[name].fix_seal for name, _dest in targets):
        # 封まで進みうるときは、書く前に持ち主の鍵が使えるかを見る（書いて判断を足したあとで封だけ断られ、半ばで
        # 止まらないように。kopicha の写しで、鍵の置き場が台帳を含む repo の中だったときに踏んだ）
        S.check_key_place(root)
        S._private_ok(S.key_path())
    written = A.row_set(root, ref, values, by=by, at=at)
    moved = V.open_vessel(root).get(ref).version != before.version
    # 名指し: 直したことで外れる記録と、満たさない条件（判断するのは、指定した記録でこの命令で版が変わった物だけ）
    named, added = [], []
    for name, dest in targets:
        o = outlets[name]
        if o.audience != "reader":
            continue
        plan = R.accept_plan(root, name, dest, as_of, common_root=common_root)
        for key in plan["changes"]["leaving"]:
            record, unit, as_ = R._parse_item_key(key)
            if record.startswith(R.COMMON + ":"):
                named.append({"outlet": name, "item": key, "judged": False, "why": "common"})
                continue
            if record != before.ref or not moved:
                named.append({"outlet": name, "item": key, "judged": False, "why": "not_this_fix"})
                continue
            if as_ != "fact":
                named.append({"outlet": name, "item": key, "judged": False, "why": "not_fact"})
                continue
            needs = _needs(root, o, record, unit, as_of, common_root)
            if not needs:
                named.append({"outlet": name, "item": key, "judged": False, "why": "no_condition_missing"})
                continue
            bad = [q for q, _w, u in needs if _w != "bad" and _last_bad(root, record, u, q, as_of)]
            if bad:
                named.append({"outlet": name, "item": key, "judged": False, "why": "bad_judgment",
                              "questions": bad})
                continue
            for question, want, at_unit in needs:
                body = R.judge(root, record, question, by=by, at=at, unit=at_unit, conclusion=want,
                               reason_text=reason_text, how=how, against=against)
                added.append(body["id"])
            named.append({"outlet": name, "item": key, "judged": True,
                          "questions": [q for q, _w, _u in needs]})
    # 受け入れの 1 段めを計算し直す
    stage = {}
    mine = set(added)
    can_seal = True
    for name, dest in targets:
        plan = R.accept_plan(root, name, dest, as_of, common_root=common_root)
        new_ids = [j["id"] for j in plan["new_judgments"]]
        why = []
        if plan["changes"]["leaving"]:
            why.append("leaving")
        if plan["changes"]["entering"]:
            why.append("entering")
        if any(i not in mine for i in new_ids):
            why.append("other_judgments")
        if plan["stale"]:
            why.append("stale")
        if plan["mixed"]:
            why.append("mixed")
        if plan["result"]["pending_takes"]:
            why.append("pending_takes")
        if not outlets[name].fix_seal:
            why.append("fix_seal_not_declared")
        can_seal = can_seal and not why
        stage[name] = {"plan": plan, "why": why}
    after = V.open_vessel(root)
    found = R.impact(after, {ref}, ())
    value = {"record": ref, "path": written.get("path"), "named": named, "judgments": added, "impact": found,
             "outlets": {}}
    if can_seal:
        for name, dest in targets:
            got = R.accept(root, name, dest, as_of, confirm=stage[name]["plan"]["digest"], at=at,
                           common_root=common_root)
            value["outlets"][name] = {"sealed": True, "generation": got["generation"], "seal": got["seal"],
                                      "files": got["files"], "judgments": got["judgments"]}
        value["sealed"] = True
        return value
    for name, item in stage.items():
        plan = item["plan"]
        value["outlets"][name] = {"sealed": False, "stopped_because": item["why"], "digest": plan["digest"],
                                  "generation": plan["body"]["generation"], "changes": plan["changes"],
                                  "new_judgments": plan["new_judgments"], "stale": plan["stale"],
                                  "pending_takes": plan["result"]["pending_takes"]}
    value["sealed"] = False
    if R.is_all(outlet):
        value["digest"] = R.all_digest({name: item["plan"]["digest"] for name, item in stage.items()})
    else:
        value["digest"] = stage[outlet]["plan"]["digest"]
    return value
