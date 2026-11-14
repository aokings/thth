"""台帳の器 段 2「7 つを戻す・取り込んだあとに 1 回直す」（設計 §5.1・§5.5・§5.6 の (1)・§5.7・§8・§10 の段 2・§13）。

- `adopt`: 元のフォルダ（各メディアの repo）から、取り込む物の範囲（`thth.adopt_plan/1` の JSON）に入るファイルを、
  元の名前・元のバイトのまま台帳のフォルダに置く。**元には 1 バイトも書かない**（開くのは読むためだけ）。
  2 回め（取り込み直し・片道）は、変わった物と消えた物（実行の印だけの変化も）を、まとめての入れ替えで入れ替える。
  止まった取り込み直しは、同じ命令で続きになる。次の物は、書く前に断る: 範囲に入りうる symlink（固定の道の途中の
  段も、型の頭のフォルダも）・実在と綴りの違う範囲の道（大文字小文字・Unicode の正規化）・種類の宣言の上限
  （既定 64 KB）を超える枚・書き先とその `.thth/` の中の symlink。
- `restore`: 台帳の全部（か、控えた前の版）を、元の場所・名前・バイトで別のフォルダに書く。目録の指紋が取り込んだ
  時の元の目録の指紋と同じなら、数・名前・sha256 が全部同じ（進みの欄も含めて、バイトで比べるので）。
- `diff`: 控えた 2 つの時点（か今）の、足した・変わった・退役した・消えた記録。番号が消えたら赤。台帳が git の外でも
  （MMR）、器が控えた時点どうしなら取り出せる。台帳が git の作業木なら、端に commit も受ける（段 5 §2.1・
  `trail.py`。宣言の置き場に入る枚だけを読むだけの git で取り出す）。変わった記録には結びの候補を付け、
  その間の取り込みの跡（集めた者・検めた者）を出す。
- `merge`: 束（新しい中身のファイルを、台帳の中の道に置いたフォルダ）を、まとめての入れ替えで入れる。次の束は
  入れない（段 6）: 判定が断りの行がある・重なる番号を作る・集めた者と検めた者が同じ・検めた者が無い（`unchecked`
  を渡したときだけ入れ、取り込みの跡は「検めていない」）。束の中の検収の記録が対象の版を示していれば、取り込みと
  同時に新しい版への判断として結ぶ。版を示さない検収の記録は結ばず、「版を示していない検収の記録」として数える。
- `row_set`・`row_add`: 表（CSV・タブ区切り。区切りは種類の宣言の `delimiter`）の 1 行を直す・足す。変える行の
  バイトだけを書き直し、触っていない行のバイトが 1 つでも変わるなら書かずに断る。足す行の改行は、最後の
  レコードの改行に合わせる（混ざった改行の表でも、ほかの行の改行は変えない）。

元の形式の道具（こぴちゃの取り込みの台本・関東の年度の置き換え・MMR の進みの書き込み）は、台帳のファイルに
そのまま書いてよい（設計 §5.1）。器の書く口は、壊さないための口。

網に出ない（標準の部品と、台帳の機械の部品だけ）。
"""
from __future__ import annotations

import csv
import fnmatch
import functools
import io
import json
import os
import tempfile
import unicodedata

from . import form
from . import judgment as J
from . import keep as K
from . import trail as T
from . import vessel as V
from .errors import RegisterRefused

PLAN_SCHEMA = "thth.adopt_plan/1"
ADOPT_SCHEMA = "thth.adopt/1"
ADOPT_FILE = "adopt.json"
GLOB_CHARS = "*?["


# ------------------------------------------------------------------ 取り込む物の範囲

def _check_pattern(pattern) -> str:
    if not isinstance(pattern, str) or not pattern or len(pattern) > 300 or pattern.startswith("/") \
            or "\\" in pattern or any(p in ("", ".", "..") for p in pattern.split("/")):
        raise RegisterRefused("register_adopt_unusable", "plan")
    return pattern


def parse_plan(raw) -> dict:
    """取り込む物の範囲。`include`（道の型の並び・既定は全部 `**`）と `exclude`。`**` は段をいくつでも、
    `*`・`?`・`[…]` は 1 つの段の中だけに当たる。"""
    if raw is None:
        raw = {"schema": PLAN_SCHEMA, "include": ["**"]}
    if not isinstance(raw, dict) or raw.get("schema") != PLAN_SCHEMA:
        raise RegisterRefused("register_adopt_unusable", "plan")
    for key in raw:
        if key not in ("schema", "include", "exclude", "note"):
            raise RegisterRefused("register_adopt_unusable", "plan")
    include = raw.get("include", ["**"])
    exclude = raw.get("exclude", [])
    if not isinstance(include, list) or not include or not isinstance(exclude, list):
        raise RegisterRefused("register_adopt_unusable", "plan")
    return {"schema": PLAN_SCHEMA, "include": [_check_pattern(p) for p in include],
            "exclude": [_check_pattern(p) for p in exclude], "note": raw.get("note")}


def read_plan(path: str | None) -> dict:
    if path is None:
        return parse_plan(None)
    try:
        with open(path, "rb") as f:
            raw = json.loads(f.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise RegisterRefused("register_adopt_unusable", "plan") from None
    return parse_plan(raw)


def glob_match(pattern: str, rel: str) -> bool:
    pat, parts = pattern.split("/"), rel.split("/")

    @functools.lru_cache(maxsize=None)
    def m(i: int, j: int) -> bool:
        if i == len(pat):
            return j == len(parts)
        if pat[i] == "**":
            return m(i + 1, j) or (j < len(parts) and m(i, j + 1))
        return j < len(parts) and fnmatch.fnmatchcase(parts[j], pat[i]) and m(i + 1, j + 1)

    return m(0, 0)


def selector(plan: dict):
    def select(rel: str) -> bool:
        return any(glob_match(p, rel) for p in plan["include"]) and not any(glob_match(p, rel) for p in plan["exclude"])
    return select


def tops(plan: dict) -> list:
    """型の頭の、型の字を含まない段（元の大きな repo を全部歩かずに済ます）。"""
    out = []
    for pattern in plan["include"]:
        fixed = []
        for part in pattern.split("/"):
            if part == "**" or any(c in part for c in GLOB_CHARS):
                break
            fixed.append(part)
        out.append("/".join(fixed))
    return [""] if "" in out else sorted(set(out))


# ------------------------------------------------------------------ 取り込み

def _inside(path: str, directory: str) -> bool:
    path, directory = os.path.realpath(path), os.path.realpath(directory)
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:
        return False


def _adopt_path(root: str) -> str:
    return os.path.join(K.thth_dir(root), ADOPT_FILE)


def read_adopt(root: str) -> dict | None:
    path = K.guard(root, _adopt_path(root))
    if not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as f:
            value = json.loads(f.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise RegisterRefused("register_kept_missing", "adopt") from None
    if not isinstance(value, dict) or value.get("schema") != ADOPT_SCHEMA:
        raise RegisterRefused("register_kept_missing", "adopt")
    return value


def _decl_files(decl: str) -> list:
    """宣言の置き場の `(名, 中身)`。種類の宣言・憲法・番号の表を、元の repo でなく台帳の `.thth/` に置くために読む。
    symlink の枚は断る（置き場の外を読まない）。"""
    out = []
    kinds_dir = os.path.join(decl, V.KINDS_DIR)
    names = [f"{V.KINDS_DIR}/{n}" for n in sorted(os.listdir(kinds_dir))
             if n.endswith(".json") and not n.startswith(".")] if os.path.isdir(kinds_dir) else []
    names += [n for n in K.DECL_FILES if os.path.lexists(os.path.join(decl, n))]
    for name in names:
        path = os.path.join(decl, *name.split("/"))
        if os.path.islink(path) or not os.path.isfile(path):
            raise RegisterRefused("register_kind_invalid", "symlink", declaration=name)
        with open(path, "rb") as f:
            out.append((name, f.read()))
    return out


def source_catalog(source: str, plan: dict) -> list:
    """元の目録（読むだけ）。取り込む物の範囲に入る物だけ。"""
    return K.dir_catalog(source, select=selector(plan), tops=tops(plan))


def reaches(plan: dict, rel: str) -> bool:
    """フォルダ `rel` の下に、範囲に入りうる道があるか（symlink のフォルダを、黙って飛ばさないため）。"""
    parts = rel.split("/")

    def under(pattern: str) -> bool:
        pat = pattern.split("/")

        @functools.lru_cache(maxsize=None)
        def m(i: int, j: int) -> bool:
            if j == len(parts):
                return i < len(pat)
            if i == len(pat):
                return False
            if pat[i] == "**":
                return m(i + 1, j) or m(i, j + 1)
            return fnmatch.fnmatchcase(parts[j], pat[i]) and m(i + 1, j + 1)

        return m(0, 0)

    return any(under(p) for p in plan["include"])


def link_selects(plan: dict, rel: str, path: str) -> bool:
    """フォルダを指す symlink `rel` の先の、実在の枚のどれかが範囲に入るか（除く型も当てて）。先が無いか読めなければ、
    入りうるとみる。先の中の symlink のフォルダは、下に範囲が届くなら入りうるとみる。"""
    if not reaches(plan, rel):
        return False
    if not os.path.isdir(path):
        return True
    select = selector(plan)
    for directory, dirs, files in os.walk(path, followlinks=False):
        sub = os.path.relpath(directory, path).replace(os.sep, "/")
        prefix = rel if sub == "." else f"{rel}/{sub}"
        if any(select(f"{prefix}/{name}") for name in files):
            return True
        keep = []
        for d in dirs:
            inner = f"{prefix}/{d}"
            if not reaches(plan, inner):
                continue
            if os.path.islink(os.path.join(directory, d)):
                return True
            keep.append(d)
        dirs[:] = keep
    return False


def _spelling(root: str, rel: str):
    """道 `rel` の実在の綴り。どの段も同じ綴りで在れば（か、無ければ）None。綴りの違う物（大文字小文字・
    Unicode の正規化）だけが在れば、その実在の綴り。"""
    current, actual, differs = root, [], False
    for part in rel.split("/"):
        try:
            names = os.listdir(current)
        except OSError:
            return None
        if part not in names:
            want = unicodedata.normalize("NFC", part).casefold()
            hits = [n for n in names if unicodedata.normalize("NFC", n).casefold() == want]
            if len(hits) != 1:
                return None
            part, differs = hits[0], True
        actual.append(part)
        current = os.path.join(current, part)
    return "/".join(actual) if differs else None


def _too_large(kinds: dict, entries: list) -> list:
    """上限を超える枚。種類の宣言に当たる枚はその上限で、どの宣言にも当たらない枚は既定の上限（64 KB）で見る
    （宣言が無ければ断る・期待する答え §3 (a)）。"""
    out = []
    for e in entries:
        matched = [kind for kind in kinds.values() if V._matches(kind, e["path"])]
        for kind in matched:
            if e["size"] > kind.max_bytes:
                out.append({"path": e["path"], "size": e["size"], "kind": kind.name, "max_bytes": kind.max_bytes})
        if not matched and e["size"] > V.FILE_MAX_DEFAULT:
            out.append({"path": e["path"], "size": e["size"], "kind": None, "max_bytes": V.FILE_MAX_DEFAULT})
    return out


def adopt(source: str, to: str, *, plan: dict, by: str, at: str, decl: str | None = None,
          checked_by: str | None = None) -> dict:
    source = os.path.abspath(os.path.expanduser(source))
    to = os.path.abspath(os.path.expanduser(to))
    if os.path.islink(source) or not os.path.isdir(source):
        raise RegisterRefused("register_adopt_unusable", "source")
    if _inside(to, source) or _inside(source, to):
        raise RegisterRefused("register_adopt_unusable", "inside")
    # 書き先とその器の置き場が symlink なら、書く前に断る（台帳の外、たとえば元の repo へ書かない）。
    if os.path.islink(to):
        raise RegisterRefused("register_adopt_unusable", "symlink", links=["."])
    if os.path.islink(K.thth_dir(to)):
        raise RegisterRefused("register_adopt_unusable", "symlink", links=[K.THTH])
    if os.path.isdir(to):
        K.check_thth(to)
    record = read_adopt(to) if os.path.isdir(to) else None
    if record is None and os.path.exists(to):
        if not os.path.isdir(to) or os.listdir(to):
            raise RegisterRefused("register_adopt_unusable", "not_empty")
    if record is not None and os.path.realpath(record.get("source", "")) != os.path.realpath(source):
        raise RegisterRefused("register_adopt_unusable", "other_source")
    select = selector(plan)
    starts = tops(plan)
    # 綴りの違う道（大文字小文字・Unicode の正規化）で範囲を書くと、その綴りで置いてしまう: 実在の綴りを言って断る。
    for top in starts:
        actual = _spelling(source, top) if top else None
        if actual is not None:
            raise RegisterRefused("register_adopt_unusable", "spelling", asked=top, actual=actual)
    # 先に道だけを見る（書く前に、置けない物と器の置き場を断る）。範囲に入りうる symlink は、固定の道の途中の段も、
    # 型の頭のフォルダも断る（辿れば範囲の外を取り込み、辿らなければ黙って 0 になる）。
    links, reserved = [], []
    for rel, path, is_link in K.walk(source, starts, skip_thth=False):
        if is_link:
            # 枚を指す symlink は範囲に当たるときだけ、フォルダを指す（か行き先の無い）symlink は下に範囲が届くときも断る。
            if select(rel) or (not os.path.isfile(path) and link_selects(plan, rel, path)):
                links.append(rel)
            continue
        if not select(rel):
            continue
        if K.reserved_path(rel):
            reserved.append(rel)   # どの段の .thth・.git も（NFC・小文字にそろえて比べる・段 6 の 6-4）
    if links:
        raise RegisterRefused("register_adopt_unusable", "symlink", links=links[:20], count=len(links))
    if reserved:
        raise RegisterRefused("register_adopt_unusable", "thth_in_source", paths=reserved[:5])
    if decl is not None:
        kinds = V.load_declarations(source, decl)["kinds"]
        decl_files = _decl_files(decl)
    else:
        kinds = V.load_declarations(to)["kinds"] if os.path.isdir(to) and V.has_declarations(to) else {}
        decl_files = []
    # 元を 1 回だけ読み、読んだバイトをそのまま置く（読みながら元が動いても、置いた物と目録は同じ読みから）。
    desired, before, executable = {}, [], {}
    for rel, path, is_link in K.walk(source, starts):
        if is_link or not select(rel):
            continue
        entry, data = K.read_entry(path, rel)
        desired[rel] = data
        executable[rel] = entry["x"]
        before.append(entry)
    large = _too_large(kinds, before)
    if large:
        # 黙って切らない: 取り込みの時点で断る（種類の宣言に max_bytes を書けば通る）。
        raise RegisterRefused("register_adopt_unusable", "too_large", files=large[:20], count=len(large))
    if os.path.isdir(to):
        for entry in K.dir_catalog(to):
            if entry["path"] not in desired:
                desired[entry["path"]] = None  # 元から消えた物（片道なので、台帳からも除く。前の中身は控えに残る）
        for e in (K.pending(to) or {}).get("entries", []):
            if e["new"] is None and e["path"] not in desired:
                desired[e["path"]] = None  # 止まった取り込み直しで、もう除いた物（同じ命令で続きにする）
    removed = sum(1 for v in desired.values() if v is None)
    # ここから書く（書く先はどれも台帳の中で、symlink の段を通らない）。
    os.makedirs(to, exist_ok=True)
    K.makedirs(to, K.thth_dir(to))
    if record is None:
        record = {"schema": ADOPT_SCHEMA, "source": source, "plan": plan, "adopted": []}
        K._write_json(to, _adopt_path(to), record)
    copied = []
    for name, data in decl_files:
        K._atomic_write(to, os.path.join(K.thth_dir(to), *name.split("/")), data)
        copied.append(name)
    first = not record["adopted"]
    result = K.run(to, desired, what="adopt" if first else "readopt", by=by, at=at,
                   executable=executable)
    after_view = K.view(to)
    fp = K.catalog_fp(after_view)
    if result["fp"] is None:
        # 変わる物が無くても、取り込んだ時点として控える（取り込みのたびに版を控える・§5.5）。宣言だけを
        # 足したときは、宣言の入った別の控えになる。
        result.update(K.save_snapshot(to, after_view, what="readopt", by=by, at=at))
    source_fp = K.catalog_fp(before)
    source_after = K.catalog_fp(source_catalog(source, plan))
    record["plan"] = plan
    record["adopted"].append({"at": at, "by": by, "checked_by": checked_by or None, "fp": fp,
                              "snapshot": result.get("snapshot"), "source_fp": source_fp, "files": len(before)})
    K._write_json(to, _adopt_path(to), record)
    # 取り込みの跡（段 5 §2.3）: 控えの目録に、集めた者・検めた者・束（元の範囲の目録）の指紋を残す。
    T.note(to, result.get("snapshot"), what="adopt" if first else "readopt", by=by, at=at, checked_by=checked_by,
           bundle_fp=source_fp)
    return {"register": to, "source": source, "files": len(before), "bytes": sum(e["size"] for e in before),
            "fp": fp, "snapshot": result.get("snapshot"), "source_fp": source_fp, "source_fp_after": source_after,
            "source_moved": source_after != source_fp, "same": fp == source_fp, "changed": result.get("applied", 0),
            "removed": removed, "continued": result.get("continued", False), "batch": result.get("batch"),
            "decl": copied, "first": first, "checked_by": checked_by or None}


# ------------------------------------------------------------------ 戻す出口

def _row(e: dict) -> tuple:
    return e["path"], e["sha256"], e["size"], bool(e.get("x", False))


def restore(root: str, to: str, *, at_snapshot: str | None = None) -> dict:
    """台帳の全部（か控えた前の版）を、元の場所・名前・バイト・実行の印で `to` に書く（手元に書くだけ）。
    入れ替えの段取りが残っていれば、入れ替えの前の姿を書く（器の読み手は前か後のどちらかしか見せない）。"""
    to = os.path.abspath(os.path.expanduser(to))
    if _inside(to, root) or _inside(root, to):
        raise RegisterRefused("register_adopt_unusable", "inside")
    if os.path.islink(to):
        raise RegisterRefused("register_adopt_unusable", "symlink", links=["."])
    if os.path.exists(to) and (not os.path.isdir(to) or os.listdir(to)):
        raise RegisterRefused("register_adopt_unusable", "not_empty")
    if at_snapshot is not None:
        snap = K.load_snapshot(root, at_snapshot)
        entries = [dict(e, object=True) for e in snap["files"]]
    else:
        entries = K.view(root)
    os.makedirs(to, exist_ok=True)
    for e in entries:
        K.put_file(to, e["path"], K.view_bytes(root, e), e.get("x", False))
    written = K.dir_catalog(to)
    fp = K.catalog_fp(entries)
    if sorted(map(_row, written)) != sorted(map(_row, entries)):
        raise RegisterRefused("register_batch_conflict", "restore_differs")
    return {"to": to, "files": len(entries), "bytes": sum(e["size"] for e in entries), "fp": fp,
            "pending": K.pending(root) is not None, "snapshot": at_snapshot}


# ------------------------------------------------------------------ 変えた跡

def _side(root: str, name: str, tmp: str, decl: str | None = None):
    """`(目録, 器として読んだ台帳, 端の説明)`。`now` は今（段取りが残っていれば前の姿）。控えの名は控えた目録。
    台帳が git の作業木なら、控えの名でない名を commit として取り出す（段 5 §2.1）。`decl` は宣言の置き場
    （既定は台帳の `.thth/`。commit と今の端に当てる。控えの端は控えた時点の宣言で読む）。"""
    kind = T.side_kind(root, name)
    if kind == "now":
        K.check_thth(root)

        def read():
            # 目録と器の読みを、同じ世代の中で取る（読み始めた直後の入れ替えと混ぜない）。
            entries = K._view_once(root)
            if K.pending(root) is None:
                return entries, V.open_vessel(root, decl_base=decl)
            into = tempfile.mkdtemp(prefix="now-", dir=tmp)
            for e in entries:
                K.put_file(into, e["path"], K.view_bytes(root, e), False)
            if decl is None:
                for e in K.decl_entries(root, store=False):
                    K.put_file(into, f"{K.THTH}/{e['path']}", _read_decl(root, e["path"]), False)
            return entries, V.open_vessel(into, decl_base=decl)

        entries, vessel = K.consistent(root, read)
        return entries, vessel, {"side": "now", "snapshot": None}
    if kind == "commit":
        into = tempfile.mkdtemp(prefix="commit-", dir=tmp)
        base = decl or os.path.join(root, K.THTH)
        for rel, data in (_decl_files(base) if os.path.isdir(base) else []):
            K.put_file(into, f"{K.THTH}/{rel}", data, False)
        kinds = V.open_vessel(into).kinds  # 宣言だけを先に読む（宣言の置き場に入る枚だけを取り出すため）
        got = T.extract(root, name, into, kinds)
        return got["files"], V.open_vessel(into), {"side": "commit", "commit": got["commit"],
                                                   "absent_kinds": got["absent_kinds"], "snapshot": None}
    snap = K.load_snapshot(root, name)
    into = tempfile.mkdtemp(prefix="kept-", dir=tmp)
    K.materialize(root, snap, into)
    return snap["files"], V.open_vessel(into), {"side": "snapshot", "snapshot": snap["id"]}


def _read_decl(root: str, rel: str) -> bytes:
    with open(K.guard(root, os.path.join(K.thth_dir(root), *rel.split("/"))), "rb") as f:
        return f.read()


def _records(vessel: V.Vessel) -> dict:
    out = {}
    for rec in vessel.all_records:
        if rec.id is None:
            continue
        out.setdefault(rec.ref, []).append(rec)
    return out


def _retired(rec: V.Record) -> bool:
    return rec.kind == "thth_row" and rec.values is not None and rec.values.get("status") == form.RETIRED


def diff(root: str, spec: str, *, decl: str | None = None, excused=None) -> dict:
    """`<前>..<後>`（どちらも控えた目録の指紋の頭・`first`・`last`・`now`。台帳が git の作業木なら commit も）。
    `excused` は赤にしない記録の番号。既定は、台帳の消す求めの結果に載った番号（`trail.erase_excuses`）。"""
    if excused is None:
        excused = T.erase_excuses(root)["records"]
    if not isinstance(spec, str) or spec.count("..") != 1:
        raise RegisterRefused("register_args_invalid", "range")
    left, right = spec.split("..")
    if decl is not None:
        decl = os.path.abspath(os.path.expanduser(decl))
    with tempfile.TemporaryDirectory(prefix="thth-diff-") as tmp:
        a_files, a, a_side = _side(root, left or "first", tmp, decl)
        b_files, b, b_side = _side(root, right or "now", tmp, decl)
        ra, rb = _records(a), _records(b)
        added = sorted(set(rb) - set(ra))
        vanished = sorted(set(ra) - set(rb))
        changed, retired, units_gone = [], [], []
        # 変わった記録の結びの候補（器は結ばない）。判断は台帳の今の記録（後の端で評価する）。
        try:
            stored = J.read(root)
        except RegisterRefused:
            stored = []
        candidates = J.candidates(b, stored) if stored else []
        for ref in sorted(set(ra) & set(rb)):
            before, after = ra[ref], rb[ref]
            # 内側の番号は、記録の版が同じでも比べる（宣言だけを変えると、中身が同じまま番号が変わる）。
            units_a = {u.pointer for r in before for u in r.units}
            units_b = {u.pointer for r in after for u in r.units}
            if sorted(r.version for r in before) != sorted(r.version for r in after) or units_a != units_b:
                item = {"record": ref, "path": after[0].path}
                if units_a or units_b:
                    item["units"] = {"added": len(units_b - units_a), "vanished": len(units_a - units_b),
                                     "added_pointers": sorted(units_b - units_a)[:50],
                                     "vanished_pointers": sorted(units_a - units_b)[:50]}
                # 内側の行の明示の番号（units.id。指し先は i:）が消えたら赤（設計 §5.5）。鍵の指し先（k:・kc:）は
                # 中身で動くので、消えても赤にしない（変わった記録として出す）。
                units_gone += [{"record": ref, "unit": p} for p in sorted(units_a - units_b) if p.startswith("i:")]
                item["candidates"] = [c for c in candidates if c["record"] == ref]
                changed.append(item)
            if not any(_retired(r) for r in before) and any(_retired(r) for r in after):
                retired.append(ref)
        fa = {e["path"]: e["sha256"] for e in a_files}
        fb = {e["path"]: e["sha256"] for e in b_files}
        if "commit" in (a_side["side"], b_side["side"]):
            # commit の端は宣言の置き場の枚だけ。今と控えの端も、宣言の置き場の枚だけで比べる。
            select = T.selector({**a.kinds, **b.kinds})
            fa = {p: h for p, h in fa.items() if select(p)}
            fb = {p: h for p, h in fb.items() if select(p)}
    files = {"added": sorted(set(fb) - set(fa)), "removed": sorted(set(fa) - set(fb)),
             "changed": sorted(p for p in set(fa) & set(fb) if fa[p] != fb[p])}
    red = T.vanished(vanished, units_gone, excused=excused)
    removed_red = [p for p in files["removed"]
                   if not any(r.path == p and r.ref in red["excused"] for rs in ra.values() for r in rs)]
    intakes = None
    if a_side["side"] != "commit" and b_side["side"] != "commit":
        intakes = T.intakes_between(root, a_side.get("snapshot"), b_side.get("snapshot"))
    return {"from": K.catalog_fp(a_files) if a_side["side"] != "commit" else a_side["commit"],
            "to": K.catalog_fp(b_files) if b_side["side"] != "commit" else b_side["commit"],
            "sides": [a_side, b_side],
            "records": {"added": added, "changed": changed, "retired": retired, "vanished": vanished},
            "units": {"vanished": units_gone},
            "files": files, "intakes": intakes, "excused": red["excused"],
            "red": bool(red["records"] or removed_red or red["units"])}


# ------------------------------------------------------------------ 束をまとめて入れる

def merge(root: str, bundle: str, *, by: str, at: str, base: str | None = None,
          checked_by: str | None = None, unchecked: bool = False) -> dict:
    """束のフォルダの全部のファイルを、台帳の同じ道へ、まとめての入れ替えで入れる（足すか置き換えるだけ）。
    `base` を渡すと、台帳の今の目録の指紋がそれで始まるときだけ入れる（束を作った時から台帳が動いていない）。
    検めた者が無い束は、`unchecked` が真のときだけ入れる（命令では `--unchecked` を二段で言う）。"""
    bundle = os.path.abspath(os.path.expanduser(bundle))
    if os.path.islink(bundle) or not os.path.isdir(bundle):
        raise RegisterRefused("register_adopt_unusable", "bundle")
    if _inside(bundle, root) or _inside(root, bundle):
        raise RegisterRefused("register_adopt_unusable", "inside")
    desired, reserved = {}, []
    # 束の根の .thth も見せて断る（黙って読み飛ばさない）。.git のフォルダは束の作り手の物として辿らない。
    for rel, path, is_link in K.walk(bundle, skip_thth=False):
        if K.reserved_path(rel):
            reserved.append(rel)   # 器が書かない道（.thth・.git。どの段でも・NFC と小文字にそろえて。段 6 の 6-4）
            continue
        if is_link:
            raise RegisterRefused("register_adopt_unusable", "symlink", links=[rel])
        K.safe_rel(rel)
        desired[rel] = K.read_entry(path, rel)[1]
    if reserved:
        raise RegisterRefused("register_merge_refused", "reserved_path", paths=sorted(reserved)[:20],
                              count=len(reserved))
    if not desired:
        raise RegisterRefused("register_adopt_unusable", "bundle_empty")
    if checked_by is None and not unchecked:
        raise RegisterRefused("register_merge_refused", "unchecked")
    if checked_by is not None:
        if not person_key(checked_by) or not person_key(by):
            raise RegisterRefused("register_args_invalid", "checked_by")
        if same_person(checked_by, by):
            raise RegisterRefused("register_merge_refused", "same_person")
    plan = K.pending(root)
    if base is not None and plan is None:
        now = K.catalog_fp(K.view(root))
        if not now.startswith(base):
            raise RegisterRefused("register_batch_conflict", "base", now=now[:16])
    if plan is None:
        _screen(root, desired)   # 止まった入れ替えの続きは、初めの打ちで見た（違う束なら K.run が断る）
    result = K.run(root, desired, what="merge", by=by, at=at)
    bundle_fp = K.catalog_fp([{"path": rel, "sha256": K.sha256(data), "size": len(data)}
                              for rel, data in desired.items()])
    bound = _bind(root, set(desired), at=at, bundle_fp=bundle_fp)
    # 取り込みの跡（段 5 §2.3）: 控えの目録に、集めた者・検めた者・束の目録の指紋を残す（結んだ判断も控えに入る）。
    T.note(root, result.get("snapshot"), what="merge", by=by, at=at, checked_by=checked_by, bundle_fp=bundle_fp)
    return {**result, "bundle_files": len(desired), "bundle_fp": bundle_fp, "checked_by": checked_by or None,
            "unchecked": checked_by is None, "bound": bound}


def person_key(name) -> str:
    """集めた者と検めた者を比べるときだけの名前の形（段 6 の 6-2）: NFKC・小文字・空白とゼロ幅の字（書式の字）を
    全部除く・末尾の `.` を除く。記録（取り込みの跡）には書かれたとおりの名前を残す。"""
    if not isinstance(name, str):
        return ""
    text = unicodedata.normalize("NFKC", name).lower()
    text = "".join(ch for ch in text if not ch.isspace() and unicodedata.category(ch) != "Cf")
    return text.rstrip(".")


def same_person(a, b) -> bool:
    """そろえた形が同じなら同じ者（`Masaru`・`ｍａｓａｒｕ`・`ma saru`・`masaru.`・ゼロ幅の字つきは `masaru` と同じ）。"""
    return person_key(a) == person_key(b)


def _after_view(root: str, desired: dict, into: str) -> V.Vessel:
    """束を入れたあとの姿を、台帳の外の作業場に組んで器として読む（台帳には書かない）。"""
    for e in K.view(root):
        if e["path"] not in desired:
            K.put_file(into, e["path"], K.view_bytes(root, e), False)
    for rel, data in desired.items():
        K.put_file(into, rel, data, False)
    for e in K.decl_entries(root, store=False):
        K.put_file(into, f"{K.THTH}/{e['path']}", _read_decl(root, e["path"]), False)
    return V.open_vessel(into)


def _screen(root: str, desired: dict) -> None:
    """入れる前に、束を入れたあとの姿で (a) 判定が断りの行 (b) 重なる番号 を見る。どちらかがあれば書かずに断る。"""
    if not os.path.isdir(os.path.join(K.thth_dir(root), V.KINDS_DIR)):
        return   # 宣言の無い台帳（備え付けの頭つきの .md だけ）は、検収の記録も番号の表も持たない
    paths = set(desired)
    with tempfile.TemporaryDirectory(prefix="thth-merge-") as tmp:
        after = _after_view(root, desired, tmp)
        _screen_inspections(after, paths)
        bad = sorted({j["source"] for j in J.inspections(after)
                      if _source_path(after, j) in paths and J.conclusion_of(after, j)[0] == "bad"})
        if bad:
            raise RegisterRefused("register_merge_refused", "verdict_refused", rows=bad[:20])
        dup_before = {r.ref for r in V.open_vessel(root).all_records if r.id is not None and "id_duplicate" in r.problems}
        dup_after = {r.ref for r in after.all_records
                     if r.id is not None and "id_duplicate" in r.problems and r.path in paths}
        overlap = sorted(dup_after - dup_before)
        if overlap:
            raise RegisterRefused("register_merge_refused", "id_overlap", records=overlap[:20])


def _row_numbers(vessel: V.Vessel, path: str) -> dict:
    """表の行の番号（見出しを 1 行め。空の行も数える）: 行の版 → 番号の並び。"""
    out = {}
    try:
        with open(os.path.join(vessel.root, *path.split("/")), "rb") as f:
            data = f.read()
        text = data[3:].decode("utf-8") if data.startswith(b"\xef\xbb\xbf") else data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for number, raw in enumerate(V.split_csv_rows(text), start=1):
        out.setdefault(V.bytes_version(raw.encode("utf-8")), []).append(number)
    return out


def _screen_inspections(after: V.Vessel, paths: set) -> None:
    """束の中の、検収の記録を持つ表（`inspects` の宣言）の行は、1 行でも検収の記録として読めなければ束ごと断る
    （`inspection_unreadable`・行の番号つき）。語が憲法に無い行も断る（`verdict_unknown`。大文字違い・前後の空白
    つきは「無い語」）。段 6 の 6-3。"""
    rows = [r for r in after.all_records
            if r.path in paths and after.kinds[r.kind].inspects is not None]
    if not rows:
        return
    report = J.inspection_report(after)
    made = {j["source"]: j for j in report["judgments"]}
    reasons = {s["source"]: s["reason"] for s in report["skipped"]}
    reasons.update({u["source"]: u["reason"] for u in report["unversioned"]})
    numbers, used = {}, {}
    unreadable, unknown = [], []

    def where(rec):
        if rec.path not in numbers:
            numbers[rec.path] = _row_numbers(after, rec.path)
        got = numbers[rec.path].get(rec.version) or []
        index = used.get((rec.path, rec.version), 0)
        used[(rec.path, rec.version)] = index + 1
        return got[index] if index < len(got) else None
    for rec in rows:
        line = where(rec)
        if rec.id is None or rec.values is None:
            unreadable.append({"path": rec.path, "line": line, "record": None,
                               "reason": (rec.problems or ["id_form"])[0]})
            continue
        j = made.get(rec.ref)
        if j is None:
            unreadable.append({"path": rec.path, "line": line, "record": rec.ref,
                               "reason": reasons.get(rec.ref, "not_read")})
            continue
        if J.conclusion_of(after, j)[0] is None:
            unknown.append({"path": rec.path, "line": line, "record": rec.ref,
                            "word": (j.get("answer") or {}).get("word")})
    if unreadable:
        raise RegisterRefused("register_merge_refused", "inspection_unreadable", rows=unreadable[:20],
                              count=len(unreadable))
    if unknown:
        raise RegisterRefused("register_merge_refused", "verdict_unknown", rows=unknown[:20], count=len(unknown))


def _source_path(vessel: V.Vessel, j: dict):
    source = j.get("source")
    if not isinstance(source, str):
        return None
    recs = [r for r in vessel.all_records if r.ref == source]
    return recs[0].path if recs else None


def _bind(root: str, paths: set, *, at: str, bundle_fp: str) -> dict:
    """束の中の検収の記録を、入れたあとの版への判断として結ぶ（段 6 の 3）。版を示す記録で、中身が今の版と合う
    物だけ。合わない物は結ばずに `not_bound`、版を示さない物は結ばずに `unversioned`（判断に数えない）。
    同じ対象・問い・見た依る所・元の行の判断がもうあれば、2 度は結ばない（止まった入れ替えの打ち直し）。"""
    vessel = V.open_vessel(root)
    have = {(x["target"].get("record"), x.get("question"), (x.get("seen") or {}).get("depend"), x.get("source"))
            for x in J.read(root) if isinstance(x.get("target"), dict)}
    made, not_bound, unversioned = [], [], []
    for j in J.inspections(vessel):
        if _source_path(vessel, j) not in paths:
            continue
        if not T.shows_version(vessel, j):
            unversioned.append(T.unversioned_view(vessel, j))
            continue
        state = J.evaluate(vessel, j)
        if state != "effective":
            not_bound.append({"record": j["target"]["record"], "source": j["source"], "state": state})
            continue
        new = J.make(vessel, j["target"], j["question"], by=j["by"], at=at, word=j["answer"]["word"],
                     source=j["source"], extra={"bundle_fp": bundle_fp})
        key = (new["target"]["record"], new["question"], new["seen"]["depend"], new["source"])
        if key in have:
            continue
        have.add(key)
        made.append(new)
    if made:
        J.append(root, made)
    return {"judgments": [{"id": m["id"], "record": m["target"]["record"], "source": m["source"],
                           "version": m["seen"]["version"]} for m in made],
            "not_bound": not_bound, "unversioned": unversioned}


# ------------------------------------------------------------------ 表の 1 行を書く

def _split_eol(raw: str) -> tuple:
    for eol in ("\r\n", "\n", "\r"):
        if raw.endswith(eol):
            return raw[:-len(eol)], eol
    return raw, ""


def raw_cells(body: str, delimiter: str = ",") -> list:
    """行のセルを、元の字（引用符も含めて）のまま分ける。区切りは種類の宣言の `delimiter`（`,` かタブ）。"""
    cells, start, quoted = [], 0, False
    for i, ch in enumerate(body):
        if ch == '"':
            quoted = not quoted
        elif ch == delimiter and not quoted:
            cells.append(body[start:i])
            start = i + 1
    cells.append(body[start:])
    return cells


def render_cell(value: str, delimiter: str = ",") -> str:
    if any(c in value for c in delimiter + '"\r\n'):
        return '"' + value.replace('"', '""') + '"'
    return value


def _join_row(cells: list, eol: str, delimiter: str = ",") -> str:
    return delimiter.join(cells) + eol


def split_mismatch(text: str, delimiter: str = ",") -> bool:
    """器の行の分け方（`V.split_csv_rows`）と、Python の `csv` の部品が表の全部を読んだ行とが食い違うか（段 6 の
    6-1。読みの問題 `row_split_mismatch`）。行の数と、行ごとのセルの並びを比べる（空の行も 1 行）。`csv` の部品で
    読めない表（大きすぎるセルなど）も、食い違うとして扱う（書く口は書かない側に倒す）。"""
    try:
        whole = list(csv.reader(io.StringIO(text, newline=""), strict=False, delimiter=delimiter))
    except csv.Error:
        return True
    split = [V._parse_csv_line(raw, delimiter) for raw in V.split_csv_rows(text)]
    return whole != split


def _records_by_csv(text: str, delimiter: str) -> int:
    """`csv` の部品で読んだレコードの数（見出しと空の行を除く）。"""
    rows = list(csv.reader(io.StringIO(text, newline=""), strict=False, delimiter=delimiter))
    return sum(1 for row in rows[1:] if row)


def _table(root: str, kind: V.Kind, rel: str):
    K.safe_rel(rel)
    data = K.read_file(root, rel)
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    try:
        text = data[len(bom):].decode("utf-8")
    except UnicodeDecodeError:
        raise RegisterRefused("register_write_unsafe", "not_utf8") from None
    lines = V.split_csv_rows(text)
    if "".join(lines) != text or not lines:
        raise RegisterRefused("register_write_unsafe", "table_shape")
    if split_mismatch(text, kind.delimiter):
        # 器の分け方と csv の部品の数え方が食い違う表（セルの途中の引用符など）: 器から見えない行を呑むので書かない。
        raise RegisterRefused("register_write_unsafe", "row_split_mismatch", path=rel)
    return data, bom, lines


def _rows(rel: str, text: str, kind: V.Kind) -> list:
    return [(r.id, r.version) for r in V._read_csv(rel, text, kind)]


def _csv_kind(vessel: V.Vessel, name: str) -> V.Kind:
    kind = vessel.kinds.get(name)
    if kind is None:
        raise RegisterRefused("register_record_missing", "kind")
    if kind.format != "csv":
        raise RegisterRefused("register_write_unsafe", "not_table")
    return kind


def _write_table(root: str, rel: str, kind: V.Kind, before_text: str, new_lines: list, bom: bytes, *,
                 target_id: str, values: dict, adding: bool, by: str, at: str, what: str) -> dict:
    new_text = "".join(new_lines)
    if split_mismatch(new_text, kind.delimiter):
        raise RegisterRefused("register_write_unsafe", "row_split_mismatch", path=rel)
    # レコードの数（csv の部品の数え方）: 直すなら同じ・足すなら 1 つ増える。違えば書かない（段 6 の 6-1）。
    if _records_by_csv(new_text, kind.delimiter) != _records_by_csv(before_text, kind.delimiter) + (1 if adding else 0):
        raise RegisterRefused("register_write_unsafe", "other_rows", path=rel)
    before = _rows(rel, before_text, kind)
    after = _rows(rel, new_text, kind)
    others_before = [r for r in before if r[0] != target_id]
    others_after = [r for r in after if r[0] != target_id]
    mine = [r for r in V._read_csv(rel, new_text, kind) if r.id == target_id]
    if others_before != others_after or len(mine) != 1 or len(after) != len(before) + (1 if adding else 0) \
            or any(mine[0].values.get(k) != v for k, v in values.items()) or mine[0].problems:
        # 触っていない行のバイトが 1 つでも変わる・書いた行が読み直せない: 書かずに断る。
        raise RegisterRefused("register_write_unsafe", "other_rows", path=rel)
    result = K.run(root, {rel: bom + new_text.encode("utf-8")}, what=what, by=by, at=at)
    return {"record": f"{kind.name}:{target_id}", "path": rel, "version": mine[0].version, **result}


def row_set(root: str, ref: str, changes: dict, *, by: str, at: str) -> dict:
    K.refuse_if_pending(root)
    vessel = V.open_vessel(root)
    rec = vessel.get(ref)
    kind = _csv_kind(vessel, rec.kind)
    if "id_duplicate" in rec.problems:
        raise RegisterRefused("register_unit_ambiguous", "record", record=ref)
    data, bom, lines = _table(root, kind, rec.path)
    header = V._parse_csv_line(lines[0], kind.delimiter)
    if header is None:
        raise RegisterRefused("register_write_unsafe", "table_shape")
    for name in changes:
        if name not in header:
            raise RegisterRefused("register_write_unsafe", "unknown_field", field=name)
    if "field" in kind.id_rule and kind.id_rule["field"] in changes:
        raise RegisterRefused("register_write_unsafe", "id_field")
    hits = []
    for index, raw in enumerate(lines[1:], start=1):
        if not raw.strip("\r\n"):
            continue
        if V.bytes_version(raw.encode("utf-8")) == rec.version:
            hits.append(index)
    if len(hits) != 1:
        raise RegisterRefused("register_unit_ambiguous", "row", record=ref)
    body, eol = _split_eol(lines[hits[0]])
    cells = raw_cells(body, kind.delimiter)
    if len(cells) != len(header):
        raise RegisterRefused("register_write_unsafe", "row_width")
    for name, value in changes.items():
        cells[header.index(name)] = render_cell(value, kind.delimiter)
    new_lines = list(lines)
    new_lines[hits[0]] = _join_row(cells, eol, kind.delimiter)
    expect = dict(rec.values, **changes)
    return _write_table(root, rec.path, kind, "".join(lines), new_lines, bom, target_id=rec.id, values=expect,
                        adding=False, by=by, at=at, what="row_set")


def row_add(root: str, kind_name: str, values: dict, *, by: str, at: str, path: str | None = None) -> dict:
    K.refuse_if_pending(root)
    vessel = V.open_vessel(root)
    kind = _csv_kind(vessel, kind_name)
    files = sorted({r.path for r in vessel.all_records if r.kind == kind_name})
    if path is not None:
        files = [p for p in files if p == path]
    if len(files) != 1:
        raise RegisterRefused("register_write_unsafe", "which_table", tables=files[:10])
    rel = files[0]
    data, bom, lines = _table(root, kind, rel)
    header = V._parse_csv_line(lines[0], kind.delimiter)
    if header is None:
        raise RegisterRefused("register_write_unsafe", "table_shape")
    for name in values:
        if name not in header:
            raise RegisterRefused("register_write_unsafe", "unknown_field", field=name)
    full_values = {name: values.get(name, "") for name in header}
    if "field" in kind.id_rule:
        rid = V._id_ok(full_values.get(kind.id_rule["field"]))
    else:
        rid = "k-" + V.short(V.fp([full_values.get(k) for k in kind.id_rule["key"]]))
    if rid is None:
        raise RegisterRefused("register_write_unsafe", "id_form")
    if any(r.kind == kind_name and r.id == rid for r in vessel.all_records):
        raise RegisterRefused("register_write_unsafe", "id_exists", record=f"{kind_name}:{rid}")
    if not _split_eol(lines[-1])[1]:
        # 最後の行に改行が無い: 足すには、その行のバイトを変えることになる（触っていない行は変えない）。
        raise RegisterRefused("register_write_unsafe", "no_final_newline", path=rel)
    # 足す行の改行は、最後のレコード（空の行でない最後の行。無ければ見出し）の改行に合わせる。
    last = next(raw for raw in reversed(lines) if raw.strip("\r\n"))
    eol = _split_eol(last)[1]
    new_lines = list(lines) + [_join_row([render_cell(full_values[n], kind.delimiter) for n in header], eol,
                                         kind.delimiter)]
    return _write_table(root, rel, kind, "".join(lines), new_lines, bom, target_id=rid, values=full_values,
                        adding=True, by=by, at=at, what="row_add")
