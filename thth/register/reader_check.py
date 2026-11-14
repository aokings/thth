"""台帳の器 段 7 の後半 §7.7「読者に見える欄の点検」（指示 `docs/指示_台帳の器_段6と段7_書く口と出口の残り_2026-11-14.md`
§7.7・「済み」の線の 4）。

宣言は 2 か所（種類の宣言と憲法は触らない。段 7 の前半が `private_fields` を置いた所と同じ出し口の宣言に置く）:

- `outlets.<出し口>.kinds.<種類>.reader_fields`: 読者に見える欄（読者に出る出し口だけが持てる）。
- `outlets.json` の頭の `internal_words`: 内部の語。**字のまま**比べる（正規表現は受けない）。512 語・1 語 64 字まで。

`check(root)` が、読者に出る出し口の種類の記録（内側の単位の出し口なら単位）の読者に見える欄から、
(a) 内部の語（字のまま・大文字と小文字も区別する）と
(b) この台帳の、読者の出し口に載らない種類（どの読者の出し口の `kinds` にも無い種類）の記録の番号
を拾う。番号は、前後が英数字か `_` でない所に字として現れた物（`hyp-12` は `hyp-123` の中では拾わない）。4 字より
短い番号は拾わない（どこにでも当たる。拾わなかった数を `short_ids_skipped` に出す）。

点検する記録（「載る記録」）は、出し口ごとに次の順で決める（出力の `scope`）:
`given`（呼ぶ側が `loaded` で渡した集まり。`compute` の項目など）→ `seal`（その出し口の最後の封の項目。封の署名は
確かめない。封の確かめは `verify` の仕事で、ここで封を偽っても狭まるのは点検だけ）→ `all`（封が無ければ、その種類の
記録の全部。狭い側へ倒す）。段 6・7 の検収の直し（11/14）:
- 比べる前に、欄の字と内部の語の両方を NFKC・小文字にそろえ、ゼロ幅の字（書式の字）を除く（全角・大文字違いを拾う）。
  番号も同じそろえ方で探す。
- 差し込みを受ける欄は、台帳の値（差し込む前の字）に加えて、差し込んだあとの値でも見る（7-3）。`check` は台帳の
  記録から差し込みを置き換えた字を自分で作る（使えるかは問わない。狭い側）。材料を作る流れからは、`compute` の
  `inserted`（材料に書く直前の値）を `check_material` に渡す。
検査の部品（段 4・`checks.py`）の出力の 1 件と同じ形で返すので、本線が `check` の `checks.list`
へ足せば、`checks.red` がそのまま赤を選ぶ。台帳にも THTH の側にも書かない。網に出ない。
"""
from __future__ import annotations

import re
import unicodedata

from . import route as R
from . import vessel as V

SHORT_ID = 4          # これより短い番号は拾わない
SHOWN_MAX = 50        # checks.py と同じ（1 つの検査につき出す違反の記録の数）
CHECK = "reader_fields"
WORDS_ID = "reader_internal_words"
IDS_ID = "reader_hidden_ids"
_RUN = re.compile(r"[A-Za-z0-9_.\-]+")
_SEP = re.compile(r"[.\-]")


def _texts(value, depth=0):
    """欄の値の中の字（並びと鍵ごとの値の中も。鍵の名前は見ない）。"""
    if depth > V.DEPTH_MAX:
        return
    if isinstance(value, str):
        yield value
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, (int, float)):
        yield str(value)
    elif isinstance(value, list):
        for v in value:
            yield from _texts(v, depth + 1)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _texts(v, depth + 1)


def fold(text: str) -> str:
    """比べる形: NFKC・小文字・ゼロ幅の字（書式の字 Cf）を除く。"""
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in text if unicodedata.category(ch) != "Cf")


def _wordlike(word: str) -> bool:
    """字（L）か数（N）を 1 つでも含む語か。"""
    return any(unicodedata.category(ch)[0] in "LN" for ch in word)


def _expand(vessel: V.Vessel, value):
    """差し込み `{{種類:番号.欄}}` を、先の記録の値（字か数）に置き換えた字（置き換えられない所は字のまま）。"""
    if not isinstance(value, str) or "{{" not in value:
        return None

    def one(m):
        kind_name, rid, field_name = m.groups()
        target = vessel.unique(f"{kind_name}:{rid}") if V.RECORD_ID.fullmatch(rid) else None
        got = (target.values or {}).get(field_name) if target is not None else None
        if isinstance(got, bool) or not isinstance(got, (str, int, float)):
            return m.group(0)
        return str(got)
    out = R.INSERT.sub(one, value)
    return out if out != value else None


def _ids_in(text: str, ids: dict) -> list:
    """字の中に現れる番号（前後が英数字か `_` でない所）。番号の字の並びの中の、`.` か `-` で区切った続きを全部見る。"""
    found = []
    for m in _RUN.finditer(text):
        run = m.group(0)
        cuts = [0] + [s.end() for s in _SEP.finditer(run)]
        ends = [s.start() for s in _SEP.finditer(run)] + [len(run)]
        for i in cuts:
            for j in ends:
                if j <= i:
                    continue
                if j - i > 160:          # 番号は 160 字まで（`V.RECORD_ID`）
                    break
                piece = run[i:j]
                if piece in ids and piece not in found:
                    found.append(piece)
    return found


class _Out:
    def __init__(self, outlet: str, kind: str, cid: str, note: str):
        self.out = {"kind": kind, "id": cid, "check": CHECK, "level": "error", "note": note, "outlet": outlet,
                    "checked": 0, "not_when": 0, "not_inspected": 0, "unverifiable": 0, "unresolved": 0,
                    "violations": 0, "records": [], "more": 0}

    def bad(self, rec_kind: str, ref: str, reason: str, **extra):
        self.out["violations"] += 1
        if len(self.out["records"]) < SHOWN_MAX:
            self.out["records"].append({"kind": rec_kind, "record": ref, "check": self.out["id"], "reason": reason,
                                        **extra})
        else:
            self.out["more"] += 1


def _sealed_refs(root: str, outlet: str):
    """最後の封の項目の記録（内側の単位は `記録#指し先`）。封が無い・読めなければ None。"""
    try:
        seal = R.read_seal(root, outlet)
    except Exception:          # noqa: BLE001 — 読めない封は「封なし」（全部を見る）に倒す
        return None
    if seal is None or seal["body"].get("outlet") != outlet:
        return None
    out = set()
    for key in seal["body"]["items"]:
        if isinstance(key, str):
            record, unit, _as = R._parse_item_key(key)
            out.add(record)
            if unit:
                out.add(f"{record}#{unit}")
    return out


def check(root: str, vessel: V.Vessel | None = None, *, loaded: dict | None = None,
          inserted: dict | None = None) -> list:
    """読者に見える欄の点検。返すのは `checks.py` の出力の 1 件と同じ形の並び（出し口と種類と検査ごとに 1 件。
    足した鍵は `outlet`・`scope` と、番号の検査の `short_ids_skipped`）。`reader_fields` を書いた種類が無ければ空の並び。
    `loaded` は `{出し口: 記録の番号（内側の単位は "種類:番号#指し先" も可）の集まり}`。
    `inserted` は `{出し口: {記録: {欄: 差し込んだあとの値}}}`（`compute` の `inserted`。材料に書く直前の値）。"""
    vessel = vessel or V.open_vessel(root)
    outlets = R.load_outlets(root, vessel)
    words = R.internal_words(root, vessel)
    reader_kinds = {k for o in outlets.values() if o.audience == "reader" for k in o.kinds}
    hidden = sorted(set(vessel.kinds) - reader_kinds)
    ids, short = {}, 0
    for rec in vessel.all_records:
        if rec.kind in hidden and rec.id is not None:
            if len(rec.id) < SHORT_ID:
                short += 1
            else:
                ids.setdefault(fold(rec.id), rec.ref)
    # 字か数を含む語はそろえて比べる。記号だけの語（`**` など書きかけの印）はそろえずに字のまま比べる
    # （NFKC で全角の `＊＊` 脚注の印まで当てない。染めと化学の実物で、脚注の `＊＊` を拾った）
    folded_words = [(word, fold(word) if _wordlike(word) else None) for word in words]
    out = []
    for name in sorted(outlets):
        outlet = outlets[name]
        if outlet.audience != "reader":
            continue
        if loaded is not None and name in loaded:
            scope, only = "given", set(loaded[name])
        else:
            only = _sealed_refs(root, name)
            scope = "all" if only is None else "seal"
        for kind in outlet.kinds:
            conf = outlet.kind_conf(kind)
            fields = conf.reader
            if not fields:
                continue
            w = _Out(name, kind, WORDS_ID, f"読者に見える欄（{', '.join(fields)}）に内部の語が無い")
            n = _Out(name, kind, IDS_ID, f"読者に見える欄（{', '.join(fields)}）に、読者の出し口に載らない種類"
                                         f"（{', '.join(hidden) or 'なし'}）の番号が無い")
            n.out["short_ids_skipped"] = short
            w.out["scope"] = n.out["scope"] = scope
            for rec in vessel.all_records:
                if rec.kind != kind or rec.id is None:
                    continue
                if only is not None and rec.ref not in only:
                    w.out["not_when"] += 1
                    n.out["not_when"] += 1
                    continue
                if rec.values is None:
                    w.out["not_inspected"] += 1
                    n.out["not_inspected"] += 1
                    continue
                if outlet.level == "unit":
                    whole = only is None or not any(x.startswith(rec.ref + "#") for x in only)
                    targets = [(u.pointer, u.item) for u in rec.units
                               if whole or f"{rec.ref}#{u.pointer}" in only]
                else:
                    targets = [(None, rec.values)]
                for pointer, values in targets:
                    w.out["checked"] += 1
                    n.out["checked"] += 1
                    extra = {"unit": pointer} if pointer is not None else {}
                    given = ((inserted or {}).get(name) or {}).get(rec.ref) or {}
                    for field in fields:
                        if not isinstance(values, dict) or field not in values:
                            continue
                        texts = list(_texts(values[field]))
                        if pointer is None and field in conf.inserts:
                            # 差し込みを受ける欄は、差し込んだあとの字でも見る（台帳の値と両方）
                            texts += [x for x in (_expand(vessel, values[field]), given.get(field))
                                      if isinstance(x, str)]
                        folded_texts = [fold(t) for t in texts]
                        for word, folded in folded_words:
                            hit = any(folded in t for t in folded_texts) if folded else (
                                folded is None and any(word in t for t in texts))
                            if hit:
                                w.bad(rec.kind, rec.ref, "internal_word", field=field, word=word, **extra)
                        if ids:
                            seen = []
                            for t in folded_texts:
                                for found in _ids_in(t, ids):
                                    if found not in seen:
                                        seen.append(found)
                                        n.bad(rec.kind, rec.ref, "hidden_id", field=field, found=ids[found], **extra)
            out += [w.out, n.out]
    return out


def check_material(root: str, outlet_name: str, result: dict, vessel: V.Vessel | None = None) -> list:
    """材料を作る流れ（`export`・`accept_plan` の `compute` の結果）から呼ぶ口: 材料に載る項目だけを、差し込んだ
    あとの値（`result["inserted"]`）と台帳の値の両方で点検する。返す形は `check` と同じ（その出し口の分だけ）。"""
    refs = set()
    for i in result.get("items", []):
        refs.add(i["record"])
        if i.get("unit"):
            refs.add(f"{i['record']}#{i['unit']}")
    loaded = {outlet_name: refs}
    out = check(root, vessel, loaded=loaded, inserted={outlet_name: result.get("inserted") or {}})
    return [c for c in out if c["outlet"] == outlet_name]


def red(result: list) -> list:
    """赤にする物（`checks.red` と同じ選び方）。"""
    return [c for c in result if c["level"] == "error" and (c["violations"] or c.get("failed"))]
