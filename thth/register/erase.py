"""台帳の器 段 5「消す求め」（設計 `docs/設計_台帳の器_2026-11-13.md` §5.5・§11 の 22、指示
`docs/指示_台帳の器_段5_跡と消す求め_2026-11-14.md` §3）。

器の約束は「原物と履歴を残す・判断は足すだけ」。持ち主が本当に消したい物（運営者の住所を書いてしまった、など）は、
この約束の**例外**として、種類の違う 4 つの記録で受ける。今の THTH の `deletion.py`（求めを受けて照らす）と
`leave.py`（消す処理。途中の段と、消した種類と数を記録する）と同じ分け方。置き場は `.thth/erase/<求めの番号>/`。

1. **求め**（`request.json`）: 誰が・いつ・何を（記録の番号・欄・単位）・理由の種類（`personal`・`rights`・
   `mistake`・`other`）。**消したい中身そのものは持たない**（理由は種類の語だけで、文を取らない）。求めを置いただけ
   では何も消えず、出口にも効かない（出すのを止めたいなら、別に「止める」を足す）。
2. **範囲の判断**（`scope.json`）: どの番号の・どの欄の・どの版まで（`all`・`current`・控えの名）。本人の判断で
   二段（1 回めは見積りと印だけ・印を付けて打ち直すと置く）。範囲は求めより広げられない。
3. **実行の対象**（`targets.json`）: 手を入れる場所の一覧（下の表）。
4. **結果と残り**（`result.json`）: 場所ごとの「消した（erased）・無かった（nothing）・消していない（not_erased）・
   確かめていない（not_checked）・消さないと決めた（kept）」・消した範囲（版の**数**）・消した番号・書き換えた判断の
   行の番号・**再現できない世代**（出し口と世代の番号の範囲）。**消した中身もその指紋も持たない。**

器が自分で手を入れるのは、台帳のフォルダの中の、今の本文と器の記録だけ（場所の順に、1 つ済むたびに結果を置く。
止まっても同じ命令で続きから。済んだ場所はやり直さない）:

| 場所 | すること |
|---|---|
| `judgments` 判断の記録 | 範囲の版を対象にした行を、印の形（`judgment.erased_ok` の注・指示 §3.4）に書き換える。行の数と順は変えない |
| `seals` 封と材料の控え | 範囲の版を載せた封から、その記録の入力の版・項目の指紋・行の指紋・書いたファイルの sha256 を外す（署名は合わなくなり、受け入れ直すまで封なし） |
| `current_text` 今の本文 | 欄を空にする（表は変える行だけ・枚はまとめての入れ替え）か、記録を除く |
| `kept_objects` 前の版 | 範囲の版の中身を、控えの置き場から除く（表は枚ごとの控えなので、ほかの行の前の版も道連れ） |
| `batch` 段取りの控え | 除いた中身の sha256 を、段取りと済んだ記録から外す |
| `snapshots` 控えの目録 | 除いた中身の sha256（と大きさ）を外し、「消した」と記す |
| `trail` 訂正と結びの跡 | 旧い文・新しい文は前の版と判断の行で除いた。結び（`carries`）は残す。控えの目録の跡の注
  （`trail.note`）が参照する判断の記録の控えのうち範囲を含む物を除き、参照と束の目録の指紋を外す |
| `materials` 書き出した材料 | 器は書き出し先を知らない（台帳の中には置けない）: 確かめていない |
| `common`・`git_history`・`clones`・`pages`・`copies` | 確かめていない（器は書き換えない。持ち主の手） |
| `self_check` 消し終わりの確かめ | 台帳のフォルダ（`.git/` を除く・`.thth/` を含む）の全部のファイルを読み、範囲の中身と指紋がどこにも無いか |

- **二次の指紋も残さない**（本線の決め 11/14）: 印の行の番号は乱数の番号（`x-`）に付け直し、その番号を指す結びも直す。
  範囲の版を目録に持つ控えの名・目録の指紋・段取りの名・取り込みの記録の指紋は乱数に付け直し、跡の参照も直す
  （古い名は書かない・対応表を作らない。付け直した数を結果に出す）。封の判断の記録の頭の指紋は外す。残る物は
  結果の `second_fingerprints` に書く（公開の場所の配る物）。
- 消す前に、範囲の版の中身・sha256・器の指紋の一覧を**メモリの中だけ**で作り（`_gather`）、確かめに使う。ファイルに
  書かない。前の版を除いたあとで止まり、打ち直したときは一覧を作り直せないので、確かめは「確かめていない」と言う。
- 「確かめていない」が 1 つでも残る間、状態は「消し終わっていない」。持ち主が外の場所を確かめたら `done` で
  1 つずつ結果を付ける。全部に結果が付いて初めて「消し終わり」。`unfinished` が数を返す（`check`・`health` に出す物）。
- 消したあと、「過去の判定を全部再現できる」とは言わない。再現できない世代は、その版を入力にしていた封の出し口ごとに
  「1 からその封の世代まで」を**多めに**言う（それより前にいつから入力だったかは、器の記録に無い）。

網に出ない。
"""
from __future__ import annotations

import json
import os
import secrets

from . import adopt as A
from . import judgment as J
from . import keep as K
from . import route as R
from . import vessel as V
from . import yamlread
from .errors import RegisterRefused

REQUEST_SCHEMA = "thth.erase_request/1"
SCOPE_SCHEMA = "thth.erase_scope/1"
TARGETS_SCHEMA = "thth.erase_targets/1"
RESULT_SCHEMA = "thth.erase_result/1"
REQUEST_FILE = "request.json"
SCOPE_FILE = J.ERASE_SCOPE
TARGETS_FILE = "targets.json"
RESULT_FILE = J.ERASE_RESULT
WHY = ("personal", "rights", "mistake", "other")
RUN_ORDER = ("judgments", "seals", "current_text", "kept_objects", "batch", "snapshots", "trail")
OUTSIDE = ("materials", "common", "git_history", "clones", "pages", "copies")
PLACES = RUN_ORDER + OUTSIDE + ("self_check",)
FINISHED = ("erased", "nothing", "kept")
DONE_RESULTS = ("erased", "kept")
MIN_NEEDLE = 4      # これより短い値は、全部のファイルから字で探さない（どこにでも当たるので。結果に数を出す）
PLACE_JA = {
    "judgments": "判断の記録", "seals": "封と材料の控え", "current_text": "今の本文", "kept_objects": "前の版",
    "batch": "段取りの控え", "snapshots": "控えの目録", "trail": "訂正と結びの跡", "materials": "書き出した材料",
    "common": "共通の台帳・ほかのメディアが採った版", "git_history": "git の履歴", "clones": "ほかの clone",
    "pages": "配った頁", "copies": "持ち出した写し", "self_check": "消し終わりの確かめ",
}
OUTSIDE_NOTE = {
    "materials": "器は書き出し先を知らない（台帳の中には書き出せない）。封が言う書いたファイルを、書き出し先で持ち主が確かめる",
    "common": "共通の台帳とほかのメディアが採った版は、その台帳で同じ求めを起こす",
    "git_history": "器は git の履歴を書き換えない。書き換えるのは持ち主",
    "clones": "ほかの clone は器から見えない。持ち主が確かめる",
    "pages": "もう配った頁は器から見えない。持ち主が確かめる",
    "copies": "持ち出した写し（相手の手元）は器から見えない。持ち主が確かめる",
}


SECOND_FINGERPRINTS = [
    "台帳の中: 行の番号・控えの名・目録の指紋・段取りの名は乱数に付け直し、封の判断の記録の頭の指紋"
    "（judgments.sha256）は外した。古い名と指紋は台帳のどこにも書いていない（確かめの一覧に入れて見た）",
    "公開の場所: 前に配った物（封と配った時の判断の記録の頭）には、消した行を含むバイトの指紋と行ごとの骨の指紋が"
    "残る。CI の続きの比べに要るので器は消さない。当て直すには、消した行のほかの全バイト（ほかの欄と全部の行）が要る",
]


# ------------------------------------------------------------------ 置き場

def _dir(root: str, req: str) -> str:
    if not isinstance(req, str) or not J.ERASE_ID.fullmatch(req):
        raise RegisterRefused("register_erase_invalid", "request_id")
    return os.path.join(K.thth_dir(root), J.ERASE_DIR, req)


def _load(root: str, req: str, name: str):
    path = K.guard(root, os.path.join(_dir(root, req), name))
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            value = json.loads(f.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise RegisterRefused("register_erase_invalid", "record_broken") from None
    if not isinstance(value, dict):
        raise RegisterRefused("register_erase_invalid", "record_broken")
    return value


def _save(root: str, req: str, name: str, value: dict) -> None:
    K._write_json(root, os.path.join(_dir(root, req), name), value)


def _request(root: str, req: str) -> dict:
    value = _load(root, req, REQUEST_FILE)
    if value is None or value.get("schema") != REQUEST_SCHEMA or value.get("id") != req:
        raise RegisterRefused("register_erase_invalid", "request_missing")
    return value


def requests(root: str) -> list:
    base = K.guard(root, os.path.join(K.thth_dir(root), J.ERASE_DIR))
    if not os.path.isdir(base):
        return []
    return sorted(n for n in os.listdir(base) if J.ERASE_ID.fullmatch(n))


# ------------------------------------------------------------------ 求め

def _kind_of(vessel: V.Vessel, ref: str) -> V.Kind:
    if not isinstance(ref, str) or ":" not in ref:
        raise RegisterRefused("register_args_invalid", "record")
    kind = vessel.kinds.get(ref.split(":", 1)[0])
    if kind is None:
        raise RegisterRefused("register_record_missing", "kind")
    return kind


def _check_target(kind: V.Kind, rec: V.Record, field, unit) -> None:
    """消せる形か。表（CSV）の行は欄か行ごと。1 枚 = 1 記録の JSON は欄（内側の単位の欄も）か枚ごと。
    ほかの形（YAML・頭つきの .md・バイトの枚）は枚ごとだけ。JSON の行の並びは、この段では受けない。"""
    if field is not None and (not isinstance(field, str) or not V.FIELD.fullmatch(field)):
        raise RegisterRefused("register_erase_invalid", "field")
    if kind.format == "csv":
        if unit is not None:
            raise RegisterRefused("register_erase_invalid", "unit")
        if field is not None and (rec.values is None or field not in rec.values):
            raise RegisterRefused("register_erase_invalid", "field")
        if field is not None and field == kind.id_rule.get("field"):
            raise RegisterRefused("register_erase_invalid", "id_field")
        return
    if kind.format == "json" and kind.records == "file":
        if unit is not None:
            found = V.unit_at(rec, unit)
            if found is None or field is None or field not in found.item:
                raise RegisterRefused("register_erase_invalid", "unit")
            return
        if field is not None and (rec.values is None or field not in rec.values or field == kind.id_rule.get("field")):
            raise RegisterRefused("register_erase_invalid", "field")
        return
    if kind.records == "file" and field is None and unit is None:
        return
    raise RegisterRefused("register_erase_invalid", "format")


def request(root: str, ref: str, *, field=None, unit=None, by: str, at: str, why: str) -> dict:
    """求めを置く（何も消さない・出口にも効かない）。中身そのものは持たない。"""
    if why not in WHY:
        raise RegisterRefused("register_erase_invalid", "why")
    K.check_thth(root)
    vessel = V.open_vessel(root)
    _kind_of(vessel, ref)
    rec = vessel.get(ref)
    kind = vessel.kinds[rec.kind]
    _check_target(kind, rec, field, unit)
    body = {"schema": REQUEST_SCHEMA, "record": rec.ref, "path": rec.path, "field": field, "unit": unit, "by": by,
            "at": at, "why": why}
    req = "e-" + V.fp(body)[:16]
    body["id"] = req
    if _load(root, req, REQUEST_FILE) is None:
        _save(root, req, REQUEST_FILE, body)
    return {"request": req, "record": rec.ref, "field": field, "unit": unit, "why": why, "exit_unaffected": True,
            "next": f"thth register erase scope <台帳> {req} --versions all --by <名前>"}


# ------------------------------------------------------------------ 範囲（メモリの中だけ）

class Range:
    """範囲の版の中身と指紋（メモリの中だけ。ファイルに書かない）。"""

    def __init__(self):
        self.shas = set()        # 範囲の版を持つ枚の中身の sha256（控えの置き場の名・今の枚）
        self.versions = set()    # 範囲の記録の版（表の行は行のバイトの sha256・枚はその sha256）
        self.values = set()      # 消す欄の値（字）
        self.rows = set()        # 記録ごと消すときの、表の行のバイト（字）
        self.depends = set()     # 範囲の版から作った依る所の指紋（消す欄に依る問いだけ）
        self.extra = set()       # 封の項目の指紋・書いたファイルの sha256（範囲の版から作った物）
        self.current = False     # 今の版が範囲に入るか
        self.current_sha = None
        self.complete = True     # 一覧を全部作れたか（前の版を除いたあとで打ち直すと False）
        self.names = set()       # 二次の指紋: 範囲の行の番号・控えの名・目録の指紋・段取りの名・判断の記録の頭

    def needles(self) -> tuple:
        """`(探す物の並び, 短くて探さなかった値の数)`。字は UTF-8 と JSON の逃がした形の両方。"""
        out, short = set(), 0
        for value in self.values | self.rows:
            if len(value.encode("utf-8")) < MIN_NEEDLE:
                short += 1
                continue
            out.add(value.encode("utf-8"))
            escaped = json.dumps(value, ensure_ascii=True)[1:-1].encode("ascii")
            if escaped != value.encode("utf-8"):
                out.add(escaped)
            escaped = json.dumps(value, ensure_ascii=False)[1:-1].encode("utf-8")
            out.add(escaped)
        for digest in self.shas | self.versions | self.depends | self.extra:
            if isinstance(digest, str) and len(digest) >= 32:
                out.add(digest.encode("ascii"))
        for name in self.names:
            if isinstance(name, str) and len(name) >= 16:
                out.add(name.encode("ascii", errors="ignore"))
        return sorted(out), short


def _target_of(kind: V.Kind, reqb: dict, path: str, data: bytes) -> V.Record | None:
    """1 つの枚の中身から、求めの記録を読む（無ければ None）。"""
    text = V._decode(data)
    rid = reqb["record"].split(":", 1)[1]
    if kind.format == "csv":
        if text is None:
            return None
        hits = [r for r in V._read_csv(path, text, kind) if r.id == rid]
        return hits[0] if len(hits) == 1 else None
    version = V.bytes_version(data)
    if kind.format == "json" and text is not None:
        try:
            value = json.loads(text)
        except (ValueError, RecursionError):
            return None
        rec = V.Record(kind.name, rid, path, version, value if isinstance(value, dict) else {"_": value}, True)
        V._read_units(rec, kind)
        return rec
    if kind.format == "yaml" and text is not None:
        value, problem, inspected = yamlread.load(text)
        values = value if isinstance(value, dict) and not problem and inspected else None
        return V.Record(kind.name, rid, path, version, values, values is not None)
    return V.Record(kind.name, rid, path, version, None, False)


def _field_value(rec: V.Record, reqb: dict):
    """消す欄の今の値（字）。無いか空なら None。"""
    if reqb.get("unit"):
        unit = V.unit_at(rec, reqb["unit"]) if rec.units else None
        value = unit.item.get(reqb["field"]) if unit is not None else None
    else:
        value = (rec.values or {}).get(reqb["field"])
    if value is None or value == "" or value == [] or value == {}:
        return None
    return value if isinstance(value, str) else V.canonical(value)


def _in_range(rec: V.Record | None, reqb: dict) -> bool:
    if rec is None:
        return False
    return reqb.get("field") is None or _field_value(rec, reqb) is not None


def _erase_plans(root: str, req: str) -> list:
    """この求めのまとめての入れ替えの段取り（済んだ物も）。"""
    out = []
    for kind_dir in (K.PLANS, K.ENDED):
        base = K.guard(root, os.path.join(K.thth_dir(root), K.BATCH, kind_dir))
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if name.endswith(".json"):
                value = K._read_json(root, os.path.join(base, name), "batch")
                if value.get("what") == _what(req):
                    out.append(value)
    return out


def _what(req: str) -> str:
    return f"erase {req}"


def _batch_files(root: str) -> list:
    out = []
    for kind_dir in (K.PLANS, K.ENDED):
        base = K.guard(root, os.path.join(K.thth_dir(root), K.BATCH, kind_dir))
        if os.path.isdir(base):
            out += [os.path.join(base, n) for n in sorted(os.listdir(base)) if n.endswith(".json")]
    return out


def _snapshot_files(root: str) -> list:
    base = K.guard(root, os.path.join(K.thth_dir(root), K.KEPT, K.SNAPSHOTS))
    return [os.path.join(base, n) for n in sorted(os.listdir(base)) if n.endswith(".json")] \
        if os.path.isdir(base) else []


def _candidates(root: str, path: str) -> dict:
    """その道の枚の版の候補: sha256 → どこから（控えの目録・段取り）。控えの置き場に中身があるものだけ。"""
    out = {}
    for full in _snapshot_files(root):
        snap = K._read_json(root, full, "snapshot")
        for e in snap.get("files", []):
            if isinstance(e, dict) and e.get("path") == path and isinstance(e.get("sha256"), str):
                out.setdefault(e["sha256"], set()).add("snapshot:" + os.path.basename(full)[:-5])
    for full in _batch_files(root):
        plan = K._read_json(root, full, "batch")
        for e in plan.get("entries", []):
            if isinstance(e, dict) and e.get("path") == path:
                for key in ("old", "new"):
                    if isinstance(e.get(key), str):
                        out.setdefault(e[key], set()).add("batch:" + str(plan.get("what")))
    return {d: s for d, s in out.items() if os.path.exists(K.object_path(root, d))}


def _gather(root: str, reqb: dict, scopeb: dict, result: dict | None) -> Range:
    """範囲の版の一覧（メモリの中だけ）。"""
    vessel = V.open_vessel(root)
    kind = _kind_of(vessel, reqb["record"])
    path = reqb["path"]
    rng = Range()
    versions = scopeb["versions"]
    done = (result or {}).get("places", {})
    cands = _candidates(root, path)
    current_full = os.path.join(root, *path.split("/"))
    current = None
    if os.path.isfile(K.guard(root, current_full)):
        with open(current_full, "rb") as f:
            current = f.read()
    allowed = set()
    if versions == ["all"]:
        allowed = set(cands)
    elif versions == ["current"]:
        # 今の版。今の本文を書き換えたあと（打ち直し）は、この求めの入れ替えの前の中身。
        for plan in _erase_plans(root, reqb["id"]):
            for e in plan.get("entries", []):
                if e.get("path") == path and isinstance(e.get("old"), str):
                    allowed.add(e["old"])
    else:
        for name in versions:
            full = K.snapshot_path(root, name)
            if os.path.exists(full):
                snap = K._read_json(root, full, "snapshot")
                allowed.update(e["sha256"] for e in snap.get("files", [])
                               if isinstance(e, dict) and e.get("path") == path and isinstance(e.get("sha256"), str))
    blobs = []
    for digest in sorted(allowed & set(cands)):
        blobs.append((digest, K.get_object(root, digest), False))
    if current is not None:
        cur = _target_of(kind, reqb, path, current)
        cur_sha = K.sha256(current)
        in_scope = versions == ["all"] or versions == ["current"] or cur_sha in allowed
        if in_scope and _in_range(cur, reqb):
            rng.current = True
            rng.current_sha = cur_sha
            blobs.append((cur_sha, current, True))
    for digest, data, is_current in blobs:
        rec = _target_of(kind, reqb, path, data)
        if not _in_range(rec, reqb):
            continue
        rng.shas.add(digest)
        rng.versions.add(rec.version)
        if reqb.get("field"):
            rng.values.add(_field_value(rec, reqb))
        elif kind.format == "csv":
            text = V._decode(data) or ""
            for raw in V.split_csv_rows(text)[1:]:
                if V.bytes_version(raw.encode("utf-8")) == rec.version:
                    rng.rows.add(raw.rstrip("\r\n"))
        for q in kind.questions + [None]:
            fields = q.depend_fields() if q else None
            if reqb.get("field") is None or fields is None or reqb["field"] in fields or reqb.get("unit"):
                rng.depends.add(V.record_depend(kind, rec, q).split(":", 1)[-1])
    # 封の項目の指紋・書いたファイルの sha256（範囲の版を入力にしていた封だけ。外したあとの封には無い）
    for full in _seal_files(root):
        body = (_read_any(full) or {}).get("body") or {}
        if not any(isinstance(i, dict) and i.get("record") == reqb["record"] and i.get("version") in rng.versions
                   for i in body.get("inputs") or []):
            continue
        for key, value in ((body.get("fingerprints") or {}).get("items") or {}).items():
            if _item_of(key, reqb["record"]) and isinstance(value, str):
                rng.extra.add(value)
        for out_path, digest in (body.get("outputs") or {}).items():
            if _output_of(out_path, path, body.get("outlet")) and isinstance(digest, str):
                rng.extra.add(digest)
    # 二次の指紋（付け直す・外す前の物。確かめの一覧に入れる）
    for full in _seal_files(root):
        body = (_read_any(full) or {}).get("body") or {}
        if any(isinstance(i, dict) and i.get("record") == reqb["record"] and i.get("version") in rng.versions
               for i in body.get("inputs") or []):
            for head in (body.get("judgments"), (body.get("previous") or {}).get("judgments")):
                if isinstance(head, dict) and isinstance(head.get("sha256"), str):
                    rng.names.add(head["sha256"])
    raw = R.judgments_raw(root)
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line) if line.strip() else None
        except ValueError:
            item = None
        if isinstance(item, dict) and not J.is_erased(item) and _line_in_range(item, reqb, rng):
            rng.names.add(item.get("id"))
    for full in _snapshot_files(root):
        snap = K._read_json(root, full, "snapshot")
        if any(isinstance(e, dict) and e.get("sha256") in rng.shas for e in snap.get("files", [])):
            rng.names.update(x for x in (snap.get("id"), snap.get("fp")) if isinstance(x, str))
    for full in _batch_files(root):
        plan = K._read_json(root, full, "batch")
        if any(isinstance(e, dict) and (e.get("old") in rng.shas or e.get("new") in rng.shas)
               for e in plan.get("entries", [])):
            rng.names.add(plan.get("id"))
    rng.names.discard(None)
    rng.complete = (done.get("kept_objects") or {}).get("state") not in FINISHED + ("running",)
    return rng


def _item_of(key: str, ref: str) -> bool:
    return isinstance(key, str) and (key.startswith(ref + "=") or key.startswith(ref + "#"))


def _output_of(out_path: str, path: str, outlet) -> bool:
    return out_path == path or out_path == f"{R.MATERIALS_DIR}/{outlet}.json"


def _seal_files(root: str) -> list:
    base = K.guard(root, os.path.join(K.thth_dir(root), R.SEALS_DIR))
    return [os.path.join(base, n) for n in sorted(os.listdir(base)) if n.endswith(".json")] \
        if os.path.isdir(base) else []


def _read_any(path: str):
    try:
        with open(path, "rb") as f:
            value = json.loads(f.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _line_in_range(item: dict, reqb: dict, rng: Range) -> bool:
    """判断の行が範囲に入るか: 範囲の版を対象にした・確かめた中身に消す値がある・照らした物が範囲の版・
    その記録への判断の理由の文に消す値がある。"""
    t = item.get("target") if isinstance(item.get("target"), dict) else {}
    if t.get("ledger"):
        return False
    ref = reqb["record"]
    if t.get("record") == ref:
        seen = item.get("seen") if isinstance(item.get("seen"), dict) else {}
        if seen.get("version") in rng.versions:
            return True
        if isinstance(seen.get("depend"), str) and seen["depend"].split(":", 1)[-1] in rng.depends:
            return True
        content = item.get("content") if isinstance(item.get("content"), dict) else None
        if content is not None:
            if reqb.get("field") is None:
                return True
            if any(isinstance(v, str) and v in rng.values for v in content.values()):
                return True
        text = (item.get("reason") or {}).get("text") if isinstance(item.get("reason"), dict) else None
        if isinstance(text, str) and any(v in text for v in rng.values if len(v.encode("utf-8")) >= MIN_NEEDLE):
            return True
    for one in item.get("against") or []:
        if isinstance(one, dict) and one.get("record") == ref and one.get("version") in rng.versions:
            return True
    return False


def _unit_from_erased(kind: V.Kind, unit: str, reqb: dict) -> bool:
    """指し先そのものが消す中身から作った指紋を含むか（含めば印の形で指し先を除く）。`i:` は元の番号なので含まない。
    `kc:` の姿は単位の値の指紋、`k:` は鍵の指紋（消す欄が鍵の欄なら含む）。"""
    if not reqb.get("unit") or not unit or unit.startswith("i:"):
        return False
    if unit.startswith("kc:"):
        return True
    keys = (kind.units or {}).get("key") or []
    return reqb.get("field") in keys


def new_line_id() -> str:
    """印の行の新しい番号（乱数。中身の指紋でない。古い番号との対応表は作らない）。"""
    return "x-" + secrets.token_hex(8)


def marked(item: dict, req: str, reqb: dict, kind: V.Kind, line_id: str) -> dict:
    """判断の行の印の形（指示 §3.4）。残す: 番号・問い・対象の番号（と指し先）・結論にあたる語・日時・誰が・いつまで・
    結び。除く: 対象の版・依る所の指紋・確かめた中身・理由の説明の文（種類は残す）・照らし方・照らした物・`seen`。
    足す: `erased`。決まった欄だけを写す（知らない欄は持ち越さない）。番号は乱数の番号 `line_id` に付け直す
    （中身から作った番号は、1 行が短いので、ほかの欄が分かれば消した値を当て直せる）。"""
    t = item["target"]
    target = {"record": t["record"]}
    if t.get("unit"):
        if _unit_from_erased(kind, t["unit"], reqb):
            target["unit_dropped"] = True     # 狭める向きにだけ効く: その記録の全部の単位に当たる
        else:
            target["unit"] = t["unit"]
            if t.get("value"):
                target["value"] = t["value"]
    a = item.get("answer") if isinstance(item.get("answer"), dict) else {}
    reason = item.get("reason") if isinstance(item.get("reason"), dict) else {}
    out = {"schema": item["schema"], "id": line_id, "question": item["question"], "target": target,
           "answer": {"word": a.get("word"), "conclusion": a.get("conclusion")},
           "reason": {"kind": reason.get("kind")}, "by": item.get("by"), "at": item.get("at"),
           "until": item.get("until"), "path": item.get("path"), "carries": item.get("carries"),
           "erased": {"request": req}}
    if isinstance(item.get("source"), str):
        out["source"] = item["source"]
    return out


# ------------------------------------------------------------------ 範囲の判断（二段）

def _norm_versions(root: str, versions) -> list:
    if not isinstance(versions, list) or not versions or any(not isinstance(v, str) for v in versions):
        raise RegisterRefused("register_erase_invalid", "versions")
    if versions in (["all"], ["current"]):
        return list(versions)
    if "all" in versions or "current" in versions:
        raise RegisterRefused("register_erase_invalid", "versions")
    names = []
    for name in versions:
        snap = K.load_snapshot(root, name)
        names.append(snap["id"])
    return sorted(set(names))


def _outlets_with(root: str, reqb: dict, rng: Range) -> list:
    out = []
    for full in _seal_files(root):
        body = (_read_any(full) or {}).get("body") or {}
        if any(isinstance(i, dict) and i.get("record") == reqb["record"] and i.get("version") in rng.versions
               for i in body.get("inputs") or []) and isinstance(body.get("generation"), int):
            out.append({"outlet": body.get("outlet"), "generations": [1, body["generation"]], "at_most": True})
    return sorted(out, key=lambda x: str(x["outlet"]))


def estimate(root: str, reqb: dict, scopeb: dict) -> dict:
    """消える範囲の見積り（数だけ。中身も指紋も出さない）。"""
    rng = _gather(root, reqb, scopeb, None)
    raw = R.judgments_raw(root)
    lines = 0
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line) if line.strip() else None
        except ValueError:
            item = None
        if isinstance(item, dict) and not J.is_erased(item) and _line_in_range(item, reqb, rng):
            lines += 1
    kept = [d for d in rng.shas if os.path.exists(K.object_path(root, d))]
    entries = 0
    for full in _snapshot_files(root):
        entries += sum(1 for e in K._read_json(root, full, "snapshot").get("files", [])
                       if isinstance(e, dict) and e.get("path") == reqb["path"] and e.get("sha256") in rng.shas)
    plans = 0
    for full in _batch_files(root):
        plans += sum(1 for e in K._read_json(root, full, "batch").get("entries", [])
                     if isinstance(e, dict) and e.get("path") == reqb["path"]
                     and (e.get("old") in rng.shas or e.get("new") in rng.shas))
    unrep = _outlets_with(root, reqb, rng)
    places = {
        "current_text": {"in_range": rng.current, "what": "field_blank" if reqb.get("field") else "record_remove"},
        "kept_objects": {"objects": len(kept), "versions": len(rng.versions),
                         "note": "表は枚ごとの控えなので、その枚の前の版は、ほかの行の分も取り出せなくなる"},
        "snapshots": {"entries": entries},
        "batch": {"entries": plans},
        "judgments": {"lines": lines},
        "seals": {"outlets": [u["outlet"] for u in unrep]},
        "trail": {"note": "旧い文・新しい文は前の版と判断の行で除く。結びは残す"},
    }
    for place in OUTSIDE:
        places[place] = {"state": "not_checked", "note": OUTSIDE_NOTE[place]}
    return {"places": places, "unreproducible": unrep}


def scope(root: str, req: str, versions, *, by: str, at: str, confirm=None, field=None, record=None) -> dict:
    reqb = _request(root, req)
    if record is not None and record != reqb["record"]:
        raise RegisterRefused("register_erase_invalid", "scope_wider")
    if field is not None and reqb.get("field") is not None and field != reqb["field"]:
        raise RegisterRefused("register_erase_invalid", "scope_wider")
    eff_field = field if field is not None else reqb.get("field")
    if field is not None and reqb.get("field") is None:
        vessel = V.open_vessel(root)
        rec = vessel.get(reqb["record"])
        _check_target(vessel.kinds[rec.kind], rec, field, reqb.get("unit"))
    if _load(root, req, SCOPE_FILE) is not None:
        raise RegisterRefused("register_erase_invalid", "scope_exists")
    names = _norm_versions(root, versions)
    body = {"schema": SCOPE_SCHEMA, "request": req, "record": reqb["record"], "field": eff_field,
            "unit": reqb.get("unit"), "versions": names}
    narrowed = dict(reqb, field=eff_field)
    est = estimate(root, narrowed, body)
    mark = R.digest({"scope": body, "estimate": est})
    if confirm is None:
        return {"stage": 1, "request": req, "scope": body, "estimate": est, "digest": mark}
    if confirm != mark:
        raise RegisterRefused("register_confirm_mismatch", "confirm", digest=mark)
    body.update(by=by, at=at, estimate=est)
    _save(root, req, SCOPE_FILE, body)
    return {"stage": 2, "request": req, "scope": body, "estimate": est}


# ------------------------------------------------------------------ 実行

def _new_result(req: str, reqb: dict, scopeb: dict) -> dict:
    return {"schema": RESULT_SCHEMA, "request": req, "record": reqb["record"], "field": scopeb.get("field"),
            "unit": scopeb.get("unit"), "versions": scopeb["versions"], "places": {}, "rewritten_lines": [],
            "erased_records": [], "erased_counts": {}, "seals": [], "unreproducible": [], "materials": [],
            "runs": [], "done": []}


def _place_judgments(root, req, reqb, rng, result, kind, by, at) -> dict:
    """範囲の行を印の形にし、番号を乱数の番号に付け直す。その番号を指す結び（`carries`）も新しい番号に直す
    （印でない行の結びを直すと、その行の番号も中身から作り直す。その行を指す結びも、続けて直す）。"""
    raw = R.judgments_raw(root)
    text = raw.decode("utf-8")
    rows, mine, rewritten, renumber = [], [], [], {}
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if not body.strip():
            rows.append((line, None, ""))
            continue
        try:
            item = json.loads(body)
        except ValueError:
            raise RegisterRefused("register_judgment_invalid", "line") from None
        tail = line[len(body):]
        if J.is_erased(item):
            if item["erased"].get("request") == req:
                mine.append(item["id"])
            rows.append((line, item, tail))
            continue
        if isinstance(item, dict) and _line_in_range(item, reqb, rng):
            fresh = new_line_id()
            renumber[item["id"]] = fresh
            rng.names.add(item["id"])
            rewritten.append(fresh)
            rows.append((None, marked(item, req, reqb, kind, fresh), tail))
        else:
            rows.append((line, item, tail))
    if not rewritten:
        count = len(mine)
        return {"state": "erased" if count else "nothing", "lines": count}
    changed = True
    while changed:
        changed = False
        for n, (line, item, tail) in enumerate(rows):
            if item is None or item.get("carries") not in renumber:
                continue
            item = dict(item, carries=renumber[item["carries"]])
            if not J.is_erased(item):
                old = item["id"]
                item["id"] = J.new_id(item)
                renumber[old] = item["id"]
            rows[n] = (None, item, tail)
            changed = True
    out = [line if line is not None else json.dumps(item, ensure_ascii=False, sort_keys=True) + tail
           for line, item, tail in rows]
    # 結果に行の番号を先に置く（読む入口は、結果に載った印の行だけを受ける）。それから判断の記録を書き換える。
    # 前の回に結果へ置いたが判断の記録に書けなかった番号は、ここで落とす（今の判断の記録にある物だけ）。
    result["rewritten_lines"] = sorted(set(mine) | set(rewritten))
    _save(root, req, RESULT_FILE, result)
    K._atomic_write(root, J.path_of(root), "".join(out).encode("utf-8"))
    count = len(set(rewritten) | set(mine))
    return {"state": "erased", "lines": count}


def _write_seal(root: str, path: str, value: dict) -> None:
    K._write_json(root, path, value)


def _place_seals(root, req, reqb, rng, result, kind, by, at) -> dict:
    ref = reqb["record"]
    touched, materials = [], set()
    for full in _seal_files(root):
        value = _read_any(full)
        body = (value or {}).get("body")
        if not isinstance(body, dict):
            continue
        changed = False
        for item in body.get("inputs") or []:
            if isinstance(item, dict) and item.get("record") == ref and item.get("version") in rng.versions:
                item["version"] = None
                item["erased"] = req
                changed = True
        already = req in (body.get("erased") or [])
        if not changed and not already:
            continue
        if changed:
            fps = body.get("fingerprints") if isinstance(body.get("fingerprints"), dict) else {}
            items = fps.get("items") if isinstance(fps.get("items"), dict) else {}
            for key in [k for k in items if _item_of(k, ref)]:
                del items[key]
            tables = fps.get("tables") if isinstance(fps.get("tables"), dict) else {}
            for table, shas in tables.items():
                if isinstance(shas, list):
                    tables[table] = [s for s in shas if s not in rng.versions and s not in rng.shas]
            for out_path in list((body.get("outputs") or {})):
                if _output_of(out_path, reqb["path"], body.get("outlet")) and body["outputs"][out_path] is not None:
                    body["outputs"][out_path] = None
                    materials.add(out_path)
            # 判断の記録の頭の指紋（消した行を含むバイトの指紋）も外す。署名はどうせ合わない。
            for head in (body.get("judgments"), (body.get("previous") or {}).get("judgments")):
                if isinstance(head, dict) and head.get("sha256") is not None:
                    head["sha256"] = None
            body["erased"] = sorted(set(body.get("erased") or []) | {req})
            _write_seal(root, full, value)
        else:
            materials.update(p for p, d in (body.get("outputs") or {}).items() if d is None)
        touched.append({"outlet": body.get("outlet"), "generations": [1, body.get("generation")], "at_most": True})
    result["seals"] = sorted(set(result["seals"]) | {t["outlet"] for t in touched})
    known = {u["outlet"]: u for u in result["unreproducible"]}
    for t in touched:
        known[t["outlet"]] = t
    result["unreproducible"] = [known[k] for k in sorted(known, key=str)]
    result["materials"] = sorted(set(result["materials"]) | materials)
    return {"state": "erased" if touched else "nothing", "outlets": len(touched)}


def _place_current(root, req, reqb, rng, result, kind, by, at) -> dict:
    what = _what(req)
    plan = K.pending(root)
    if plan is not None:
        if plan.get("what") != what:
            K.refuse_if_pending(root)
        K.apply(root, by=by, at=at)          # 打ち直し: この求めの入れ替えの続き
        return {"state": "erased", "continued": True}
    if not rng.current:
        return {"state": "nothing"}
    vessel = V.open_vessel(root)
    rec = vessel.get(reqb["record"])
    rel = rec.path
    if kind.format == "csv":
        data, bom, lines = A._table(root, kind, rel)
        header = V._parse_csv_line(lines[0], kind.delimiter)
        hits = [i for i, raw in enumerate(lines[1:], start=1)
                if raw.strip("\r\n") and V.bytes_version(raw.encode("utf-8")) == rec.version]
        if len(hits) != 1:
            raise RegisterRefused("register_unit_ambiguous", "row", record=reqb["record"])
        if reqb.get("field"):
            body, eol = A._split_eol(lines[hits[0]])
            cells = A.raw_cells(body, kind.delimiter)
            if len(cells) != len(header):
                raise RegisterRefused("register_write_unsafe", "row_width")
            cells[header.index(reqb["field"])] = ""
            new_lines = list(lines)
            new_lines[hits[0]] = A._join_row(cells, eol, kind.delimiter)
            A._write_table(root, rel, kind, "".join(lines), new_lines, bom, target_id=rec.id,
                           values=dict(rec.values, **{reqb["field"]: ""}), adding=False, by=by, at=at, what=what)
        else:
            new_lines = lines[:hits[0]] + lines[hits[0] + 1:]
            before = [(r.id, r.version) for r in V._read_csv(rel, "".join(lines), kind) if r.id != rec.id]
            after = [(r.id, r.version) for r in V._read_csv(rel, "".join(new_lines), kind)]
            if before != after:
                raise RegisterRefused("register_write_unsafe", "other_rows", path=rel)
            K.run(root, {rel: bom + "".join(new_lines).encode("utf-8")}, what=what, by=by, at=at)
            result["erased_records"] = sorted(set(result["erased_records"]) | {reqb["record"]})
        return {"state": "erased", "what": "field_blank" if reqb.get("field") else "record_remove"}
    if reqb.get("field") is None:
        K.run(root, {rel: None}, what=what, by=by, at=at)
        result["erased_records"] = sorted(set(result["erased_records"]) | {reqb["record"]})
        return {"state": "erased", "what": "record_remove"}
    # 1 枚 = 1 記録の JSON の欄（内側の単位の欄も）: 枚をまとめての入れ替えで書き直す
    data = K.read_file(root, rel)
    value = json.loads(V._decode(data))
    probe = V.Record(kind.name, rec.id, rel, rec.version, value, True)
    V._read_units(probe, kind)
    if reqb.get("unit"):
        unit = V.unit_at(probe, reqb["unit"])
        if unit is None:
            raise RegisterRefused("register_record_missing", "unit")
        unit.item[reqb["field"]] = ""
    else:
        value[reqb["field"]] = ""
    K.run(root, {rel: (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")}, what=what, by=by,
          at=at)
    return {"state": "erased", "what": "field_blank"}


def _place_kept(root, req, reqb, rng, result, kind, by, at) -> dict:
    removed = 0
    for digest in sorted(rng.shas):
        removed += K.remove_object(root, digest)
    # 入れ替えの作業場に残った枚（途中で落ちた入れ替えの退かした枚）も、範囲の中身なら除く
    tmp = K.guard(root, os.path.join(K.thth_dir(root), K.BATCH, "tmp"))
    if os.path.isdir(tmp):
        for name in sorted(os.listdir(tmp)):
            full = K.guard(root, os.path.join(tmp, name))
            if os.path.isfile(full):
                with open(full, "rb") as f:
                    if K.sha256(f.read()) in rng.shas:
                        os.unlink(full)
                        removed += 1
    result["erased_counts"]["objects"] = result["erased_counts"].get("objects", 0) + removed
    result["erased_counts"]["versions"] = max(result["erased_counts"].get("versions", 0), len(rng.versions))
    return {"state": "erased" if removed else "nothing", "objects": removed}


def _gone(root: str, digest, rng: Range) -> bool:
    return isinstance(digest, str) and (digest in rng.shas or not os.path.exists(K.object_path(root, digest)))


def _rewrite_log(root: str, change) -> None:
    """控えの跡（`log.jsonl`）の行ごとに `change(行)` を当てて書き直す（付け直した名へ）。"""
    path = K.guard(root, K._kept(root, K.LOG))
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            out.append(line)
            continue
        out.append(json.dumps(change(item) if isinstance(item, dict) else item, ensure_ascii=False, sort_keys=True))
    K._atomic_write(root, path, ("\n".join(out) + ("\n" if out else "")).encode("utf-8"))


def _renamed(value: dict, req: str) -> bool:
    return req in (value.get("renamed") or [])


def _place_batch(root, req, reqb, rng, result, kind, by, at) -> dict:
    """除いた中身の sha256 を段取りと済んだ記録から外し、外した段取りの名（中身の sha256 から作った名）を乱数の名に
    付け直す。跡（`log.jsonl`）の参照も直す。古い名は書かない。"""
    count, plans = 0, []
    for full in _batch_files(root):
        plan = K._read_json(root, full, "batch")
        if not K._ended(root, plan) and plan.get("what") != _what(req):
            K.refuse_if_pending(root)
        changed = False
        for e in plan.get("entries", []):
            if not isinstance(e, dict) or e.get("path") != reqb["path"]:
                continue
            for key in ("old", "new"):
                if _gone(root, e.get(key), rng):
                    e[key] = None
                    e[f"{key}_size"] = None
                    e[f"{key}_erased"] = req
                    changed = True
                    count += 1
        marked_here = any(isinstance(e, dict) and req in (e.get("old_erased"), e.get("new_erased"))
                          for e in plan.get("entries", []))
        if changed or (marked_here and not _renamed(plan, req)):
            plans.append((full, plan))
    rename = {}
    for _full, plan in plans:
        if not _renamed(plan, req) and isinstance(plan.get("id"), str):
            rng.names.add(plan["id"])
            rename.setdefault(plan["id"], "b-" + secrets.token_hex(8))
    if rename:
        _rewrite_log(root, lambda h: dict(h, batch=rename.get(h.get("batch"), h.get("batch")))
                     if h.get("batch") in rename else h)
    for full, plan in plans:
        if plan.get("id") in rename:
            plan["id"] = rename[plan["id"]]
            plan["renamed"] = sorted(set(plan.get("renamed") or []) | {req})
        K._write_json(root, full, plan)
    result.setdefault("renamed", {})
    result["renamed"]["plans"] = result["renamed"].get("plans", 0) + len(rename)
    return {"state": "erased" if count or rename else "nothing", "entries": count, "renamed": len(rename)}


def _place_snapshots(root, req, reqb, rng, result, kind, by, at) -> dict:
    """控えの目録から除いた中身の sha256 と大きさを外して「消した」と記し、その目録の名と目録の指紋（範囲の版の
    sha256 から作った物）を乱数に付け直す。跡・取り込みの記録・段取りの済んだ記録の参照も直す。古い名は書かない。"""
    count, todo = 0, []
    for full in _snapshot_files(root):
        snap = K._read_json(root, full, "snapshot")
        changed = False
        files = []
        for e in snap.get("files", []):
            if isinstance(e, dict) and e.get("path") == reqb["path"] and _gone(root, e.get("sha256"), rng):
                files.append({"path": e["path"], "sha256": None, "size": None, "x": bool(e.get("x", False)),
                              "erased": req})
                changed = True
                count += 1
            else:
                files.append(e)
        if changed:
            snap["files"] = files
            snap["erased"] = sorted(set(snap.get("erased") or []) | {req})
        if req in (snap.get("erased") or []) and not _renamed(snap, req):
            todo.append((full, snap))
    names, fps = {}, {}
    for _full, snap in todo:
        rng.names.update(x for x in (snap.get("id"), snap.get("fp")) if isinstance(x, str))
        names[os.path.basename(_full)[:-5]] = secrets.token_hex(32)
        if isinstance(snap.get("fp"), str):
            fps.setdefault(snap["fp"], secrets.token_hex(32))
    # 1) 新しい名で目録を置く 2) 参照を直す 3) 古い名の目録を除く（どこで止まっても、打ち直しが続ける）
    for full, snap in todo:
        new = names[os.path.basename(full)[:-5]]
        snap = dict(snap, id=new, fp=fps.get(snap.get("fp"), snap.get("fp")),
                    renamed=sorted(set(snap.get("renamed") or []) | {req}))
        K.write_snapshot(root, new, snap)
    if names:
        def ref(h):
            out = dict(h)
            if out.get("snapshot") in names:
                out["snapshot"] = names[out["snapshot"]]
            if out.get("fp") in fps:
                out["fp"] = fps[out["fp"]]
            return out
        _rewrite_log(root, ref)
        adopt = os.path.join(K.thth_dir(root), A.ADOPT_FILE)
        record = A.read_adopt(root)
        if record is not None:
            for item in record.get("adopted") or []:
                if not isinstance(item, dict):
                    continue
                hit = item.get("snapshot") in names or item.get("fp") in fps
                item.update(ref(item))
                if hit and isinstance(item.get("source_fp"), str):
                    # 元の目録の指紋も、範囲の版を含む目録から作った物なので付け直す（台帳と同じなら同じ名へ）
                    item["source_fp"] = fps.get(item["source_fp"]) or secrets.token_hex(32)
            K._write_json(root, adopt, record)
        for full in _batch_files(root):
            plan = K._read_json(root, full, "batch")
            done = plan.get("done") if isinstance(plan.get("done"), dict) else None
            if done is not None and (done.get("snapshot") in names or done.get("fp") in fps):
                plan["done"] = ref(done)
                K._write_json(root, full, plan)
    for full, _snap in todo:
        os.unlink(K.guard(root, full))
    result.setdefault("renamed", {})
    result["renamed"]["snapshots"] = result["renamed"].get("snapshots", 0) + len(names)
    return {"state": "erased" if count or names else "nothing", "entries": count, "renamed": len(names)}


def _judgments_in_range(data: bytes, reqb: dict, rng: Range, needles: list) -> bool:
    """控えた判断の記録の中身が、範囲の行（範囲の版を対象にした・理由の文に消す値がある）か範囲の中身を含むか。"""
    if any(n in data for n in needles):
        return True
    for line in data.decode("utf-8", errors="replace").splitlines():
        item = _read_line(line)
        if item is not None and not J.is_erased(item) and _line_in_range(item, reqb, rng):
            return True
    return False


def _read_line(line: str):
    try:
        item = json.loads(line) if line.strip() else None
    except (ValueError, RecursionError):
        return None
    return item if isinstance(item, dict) else None


def _strip_trail_notes(root: str, req: str, reqb: dict, rng: Range) -> dict:
    """控えの目録の跡の注（`trail`・段 5 §2.3）が参照する、判断の記録の控えのうち範囲を含む物を除き、参照を外す
    （指紋を残さない: 名の代わりに `judgments_erased` を記す）。範囲の版を持っていた目録の注の束の目録の指紋も外す。
    除いた時点への `check --against` は「取り出せない」と言う。"""
    needles, _short = rng.needles()
    removed = notes_changed = 0
    for full in _snapshot_files(root):
        snap = K._read_json(root, full, "snapshot")
        trail = snap.get("trail")
        if not isinstance(trail, list) or not trail:
            continue
        touched_snapshot = req in (snap.get("erased") or [])
        out, changed = [], False
        for note in trail:
            if not isinstance(note, dict):
                out.append(note)
                continue
            note = dict(note)
            digest = note.get("judgments")
            if isinstance(digest, str):
                path = K.guard(root, K.object_path(root, digest))
                data = None
                if os.path.isfile(path):
                    with open(path, "rb") as f:
                        data = f.read()
                if data is None or _judgments_in_range(data, reqb, rng, needles):
                    rng.names.add(digest)
                    if data is not None:
                        removed += K.remove_object(root, digest)
                    note.pop("judgments")
                    note["judgments_erased"] = req
                    changed = True
            bundle = note.get("bundle_fp")
            if isinstance(bundle, str) and (touched_snapshot or bundle in rng.names):
                rng.names.add(bundle)
                note.pop("bundle_fp")
                note["bundle_fp_erased"] = req
                changed = True
            out.append(note)
        if changed:
            notes_changed += 1
            K.write_snapshot(root, os.path.basename(full)[:-5], dict(snap, trail=out))
    return {"objects": removed, "snapshots": notes_changed}


def _place_trail(root, req, reqb, rng, result, kind, by, at) -> dict:
    lines = [j for j in J.read(root) if J.is_erased(j) and j["erased"].get("request") == req
             and (j.get("carries") or j.get("path") == "carry")]
    notes = _strip_trail_notes(root, req, reqb, rng)
    result["erased_counts"]["trail_judgments"] = result["erased_counts"].get("trail_judgments", 0) + notes["objects"]
    hit = lines or notes["objects"] or notes["snapshots"] \
        or result["places"].get("kept_objects", {}).get("state") == "erased"
    return {"state": "erased" if hit else "nothing", "carry_lines": len(lines),
            "judgments_kept": notes["objects"], "notes": notes["snapshots"],
            "note": "旧い文・新しい文は前の版と判断の行から除いた。結び（carries）は残した。控えに置いた判断の記録の"
                    "うち範囲を含む物は除き、跡の注の参照も外した"}


PLACE_FUNCS = {"judgments": _place_judgments, "seals": _place_seals, "current_text": _place_current,
               "kept_objects": _place_kept, "batch": _place_batch, "snapshots": _place_snapshots,
               "trail": _place_trail}


def _where(rel: str) -> str:
    """見つかった道を、結果の場所の名に当てる。"""
    parts = rel.split("/")
    if parts[0] != K.THTH:
        return "current_text"
    second = parts[1] if len(parts) > 1 else ""
    if second == K.KEPT:
        return "snapshots" if len(parts) > 2 and parts[2] == K.SNAPSHOTS else "kept_objects"
    return {J.FILE: "judgments", R.SEALS_DIR: "seals", K.BATCH: "batch"}.get(second, "self_check")


def self_check(root: str, rng: Range) -> dict:
    """台帳のフォルダ（`.git/` を除く・`.thth/` を含む）の全部のファイルを読み、範囲の中身と指紋が無いか。"""
    needles, short = rng.needles()
    hits = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in K.SKIP_DIRS)
        for name in sorted(files):
            full = os.path.join(directory, name)
            if os.path.islink(full):
                continue
            with open(full, "rb") as f:
                data = f.read()
            if any(n in data for n in needles):
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                hits.setdefault(_where(rel), []).append(rel)
    out = {"state": "erased" if not hits else "not_erased", "files": sum(len(v) for v in hits.values()),
           "short_values_not_scanned": short}
    if hits:
        out["found_in"] = {k: sorted(v)[:20] for k, v in sorted(hits.items())}
    if not rng.complete:
        out["state"] = "not_checked" if not hits else "not_erased"
        out["why"] = "list_rebuilt"   # 前の版を除いたあとで打ち直した: 一覧を全部は作り直せない
    return out


def _state(result: dict) -> str:
    places = result.get("places", {})
    if all((places.get(p) or {}).get("state") in FINISHED for p in PLACES):
        return "finished"
    return "unfinished"


def run(root: str, req: str, *, by: str, at: str) -> dict:
    """範囲の判断がある求めを実行する（場所の順に、1 つ済むたびに結果を置く。打ち直すと続きから）。"""
    reqb = _request(root, req)
    scopeb = _load(root, req, SCOPE_FILE)
    if scopeb is None or scopeb.get("schema") != SCOPE_SCHEMA:
        raise RegisterRefused("register_erase_invalid", "scope_missing")
    K.check_thth(root)
    plan = K.pending(root)
    if plan is not None and plan.get("what") != _what(req):
        K.refuse_if_pending(root)
    reqb = dict(reqb, field=scopeb.get("field"))
    vessel = V.open_vessel(root)
    kind = _kind_of(vessel, reqb["record"])
    result = _load(root, req, RESULT_FILE) or _new_result(req, reqb, scopeb)
    if _load(root, req, TARGETS_FILE) is None:
        _save(root, req, TARGETS_FILE, {"schema": TARGETS_SCHEMA, "request": req, "at": at, "by": by,
                                        "places": [{"place": p, "name": PLACE_JA[p],
                                                    "by": "owner" if p in OUTSIDE else "vessel"} for p in PLACES]})
    rng = _gather(root, reqb, scopeb, result)
    for place in RUN_ORDER:
        state = (result["places"].get(place) or {}).get("state")
        if state in FINISHED:
            continue
        result["places"][place] = {"state": "running", "by": by, "at": at}
        _save(root, req, RESULT_FILE, result)
        got = PLACE_FUNCS[place](root, req, reqb, rng, result, kind, by, at)
        if got.get("state") == "nothing" and state == "running":
            got["state"] = "erased"            # 前の回に途中まで済ませた場所
        result["places"][place] = dict(got, by=by, at=at)
        _save(root, req, RESULT_FILE, result)
    for place in OUTSIDE:
        if (result["places"].get(place) or {}).get("state") not in FINISHED:
            entry = {"state": "not_checked", "note": OUTSIDE_NOTE[place]}
            if place == "materials" and result["materials"]:
                entry["outputs"] = result["materials"]
            result["places"][place] = entry
    check = self_check(root, rng)
    for place in check.get("found_in", {}):
        if place in result["places"] and (result["places"][place] or {}).get("state") != "kept":
            result["places"][place] = dict(result["places"][place], state="not_erased")
    if (result["places"].get("self_check") or {}).get("state") != "kept" or check["state"] == "not_erased":
        result["places"]["self_check"] = dict(check, by=by, at=at)
    result["second_fingerprints"] = SECOND_FINGERPRINTS
    result.setdefault("renamed", {})
    result["runs"].append({"by": by, "at": at})
    result["state"] = _state(result)
    _save(root, req, RESULT_FILE, result)
    return result


# ------------------------------------------------------------------ 結果と残り

def status(root: str, req: str | None = None) -> dict:
    if req is None:
        out = []
        for one in requests(root):
            reqb = _load(root, one, REQUEST_FILE) or {}
            result = _load(root, one, RESULT_FILE)
            out.append({"request": one, "record": reqb.get("record"), "field": reqb.get("field"),
                        "scope": _load(root, one, SCOPE_FILE) is not None,
                        "state": (result or {}).get("state") or "unfinished"})
        return {"requests": out, "unfinished": sum(1 for x in out if x["state"] != "finished")}
    reqb = _request(root, req)
    result = _load(root, req, RESULT_FILE)
    if result is None:
        pending = {p: {"state": "not_started"} for p in PLACES}
        return {"request": req, "record": reqb["record"], "field": reqb.get("field"),
                "scope": _load(root, req, SCOPE_FILE) is not None, "state": "unfinished", "places": pending,
                "rewritten_lines": [], "unreproducible": []}
    return dict(result, state=_state(result))


def done(root: str, req: str, place: str, *, by: str, at: str, outcome: str) -> dict:
    """持ち主が確かめた場所に、結果を 1 つ付ける（「消した」か「消さないと決めた」）。"""
    _request(root, req)
    if place not in PLACES:
        raise RegisterRefused("register_erase_invalid", "place")
    if outcome not in DONE_RESULTS:
        raise RegisterRefused("register_erase_invalid", "result")
    result = _load(root, req, RESULT_FILE)
    if result is None:
        raise RegisterRefused("register_erase_invalid", "not_run")
    now = (result["places"].get(place) or {}).get("state")
    if now in ("erased", "nothing") and place not in OUTSIDE:
        return dict(result, state=_state(result))
    result["places"][place] = dict(result["places"].get(place) or {}, state=outcome, by=by, at=at, owner=True)
    result["done"].append({"place": place, "result": outcome, "by": by, "at": at})
    result["state"] = _state(result)
    _save(root, req, RESULT_FILE, result)
    return result


def unfinished(root: str) -> int:
    """消し終わっていない求めの数（`check` と `health` に出す物。つなぐのは呼ぶ側）。"""
    return status(root)["unfinished"]
