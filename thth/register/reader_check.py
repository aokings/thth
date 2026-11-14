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

点検する記録は、読者の出し口の種類の記録の**全部**（封や判断で今載るかは見ない。狭い側へ倒す: 載らない記録に内部の語が
あっても赤になる）。検査の部品（段 4・`checks.py`）の出力の 1 件と同じ形で返すので、本線が `check` の `checks.list`
へ足せば、`checks.red` がそのまま赤を選ぶ。台帳にも THTH の側にも書かない。網に出ない。
"""
from __future__ import annotations

import re

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


def check(root: str, vessel: V.Vessel | None = None) -> list:
    """読者に見える欄の点検。返すのは `checks.py` の出力の 1 件と同じ形の並び（出し口と種類と検査ごとに 1 件。
    足した鍵は `outlet` と、番号の検査の `short_ids_skipped`）。`reader_fields` を書いた種類が無ければ空の並び。"""
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
                ids.setdefault(rec.id, rec.ref)
    out = []
    for name in sorted(outlets):
        outlet = outlets[name]
        if outlet.audience != "reader":
            continue
        for kind in outlet.kinds:
            fields = outlet.kind_conf(kind).reader
            if not fields:
                continue
            w = _Out(name, kind, WORDS_ID, f"読者に見える欄（{', '.join(fields)}）に内部の語が無い")
            n = _Out(name, kind, IDS_ID, f"読者に見える欄（{', '.join(fields)}）に、読者の出し口に載らない種類"
                                         f"（{', '.join(hidden) or 'なし'}）の番号が無い")
            n.out["short_ids_skipped"] = short
            for rec in vessel.all_records:
                if rec.kind != kind or rec.id is None:
                    continue
                if rec.values is None:
                    w.out["not_inspected"] += 1
                    n.out["not_inspected"] += 1
                    continue
                if outlet.level == "unit":
                    targets = [(u.pointer, u.item) for u in rec.units]
                else:
                    targets = [(None, rec.values)]
                for pointer, values in targets:
                    w.out["checked"] += 1
                    n.out["checked"] += 1
                    extra = {"unit": pointer} if pointer is not None else {}
                    for field in fields:
                        if not isinstance(values, dict) or field not in values:
                            continue
                        texts = list(_texts(values[field]))
                        for word in words:
                            if any(word in t for t in texts):
                                w.bad(rec.kind, rec.ref, "internal_word", field=field, word=word, **extra)
                        if ids:
                            seen = []
                            for t in texts:
                                for found in _ids_in(t, ids):
                                    if found not in seen:
                                        seen.append(found)
                                        n.bad(rec.kind, rec.ref, "hidden_id", field=field, found=ids[found], **extra)
            out += [w.out, n.out]
    return out


def red(result: list) -> list:
    """赤にする物（`checks.red` と同じ選び方）。"""
    return [c for c in result if c["level"] == "error" and (c["violations"] or c.get("failed"))]
