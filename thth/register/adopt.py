"""台帳の器 段 2「7 つを戻す・取り込んだあとに 1 回直す」（設計 §5.1・§5.5・§5.6 の (1)・§5.7・§8・§10 の段 2・§13）。

- `adopt`: 元のフォルダ（各メディアの repo）から、取り込む物の範囲（`thth.adopt_plan/1` の JSON）に入るファイルを、
  元の名前・元のバイトのまま台帳のフォルダに置く。**元には 1 バイトも書かない**（開くのは読むためだけ）。
  2 回め（取り込み直し・片道）は、変わった物と消えた物だけを、まとめての入れ替えで入れ替える。
- `restore`: 台帳の全部（か、控えた前の版）を、元の場所・名前・バイトで別のフォルダに書く。目録の指紋が取り込んだ
  時の元の目録の指紋と同じなら、数・名前・sha256 が全部同じ（進みの欄も含めて、バイトで比べるので）。
- `diff`: 控えた 2 つの時点（か今）の、足した・変わった・退役した・消えた記録。番号が消えたら赤。台帳が git の外でも
  （MMR）、器が控えた時点どうしなら取り出せる。
- `merge`: 束（新しい中身のファイルを、台帳の中の道に置いたフォルダ）を、まとめての入れ替えで入れる。
- `row_set`・`row_add`: 表（CSV）の 1 行を直す・足す。変える行のバイトだけを書き直し、触っていない行のバイトが
  1 つでも変わるなら書かずに断る。

元の形式の道具（こぴちゃの取り込みの台本・関東の年度の置き換え・MMR の進みの書き込み）は、台帳のファイルに
そのまま書いてよい（設計 §5.1）。器の書く口は、壊さないための口。

網に出ない（標準の部品と、台帳の機械の部品だけ）。
"""
from __future__ import annotations

import fnmatch
import functools
import json
import os
import tempfile

from . import form
from . import keep as K
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
    path = _adopt_path(root)
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


def _copy_decl(decl: str, to: str) -> list:
    """種類の宣言・憲法・番号の表を、元の repo でなく台帳の `.thth/` に置く（宣言は先に読んで確かめる）。"""
    V.load_declarations(to, decl)
    copied = []
    kinds_dir = os.path.join(decl, V.KINDS_DIR)
    names = [f"{V.KINDS_DIR}/{n}" for n in sorted(os.listdir(kinds_dir))
             if n.endswith(".json") and not n.startswith(".")] if os.path.isdir(kinds_dir) else []
    names += [n for n in K.DECL_FILES if os.path.isfile(os.path.join(decl, n))]
    for name in names:
        with open(os.path.join(decl, *name.split("/")), "rb") as f:
            data = f.read()
        K._atomic_write(os.path.join(K.thth_dir(to), *name.split("/")), data)
        copied.append(name)
    return copied


def source_catalog(source: str, plan: dict) -> list:
    """元の目録（読むだけ）。取り込む物の範囲に入る物だけ。"""
    return K.dir_catalog(source, select=selector(plan), tops=tops(plan))


def adopt(source: str, to: str, *, plan: dict, by: str, at: str, decl: str | None = None) -> dict:
    source = os.path.abspath(os.path.expanduser(source))
    to = os.path.abspath(os.path.expanduser(to))
    if os.path.islink(source) or not os.path.isdir(source):
        raise RegisterRefused("register_adopt_unusable", "source")
    if _inside(to, source) or _inside(source, to):
        raise RegisterRefused("register_adopt_unusable", "inside")
    record = read_adopt(to) if os.path.isdir(to) else None
    if record is None and os.path.exists(to):
        if os.path.islink(to) or not os.path.isdir(to) or os.listdir(to):
            raise RegisterRefused("register_adopt_unusable", "not_empty")
    if record is not None and os.path.realpath(record.get("source", "")) != os.path.realpath(source):
        raise RegisterRefused("register_adopt_unusable", "other_source")
    select = selector(plan)
    # 先に道だけを見る（書く前に、置けない物と器の置き場を断る）。
    links, reserved = [], []
    for rel, _path, is_link in K.walk(source, tops(plan), skip_thth=False):
        if not select(rel):
            continue
        if is_link:
            links.append(rel)
        elif rel.split("/")[0] == K.THTH:
            reserved.append(rel)
    if links:
        raise RegisterRefused("register_adopt_unusable", "symlink", links=links[:20], count=len(links))
    if reserved:
        raise RegisterRefused("register_adopt_unusable", "thth_in_source", paths=reserved[:5])
    if decl is not None:
        V.load_declarations(source, decl)
    os.makedirs(K.thth_dir(to), exist_ok=True)
    if record is None:
        record = {"schema": ADOPT_SCHEMA, "source": source, "plan": plan, "adopted": []}
        K._write_json(_adopt_path(to), record)
    copied = _copy_decl(decl, to) if decl is not None else []
    # 元を 1 回だけ読み、読んだバイトをそのまま置く（読みながら元が動いても、置いた物と目録は同じ読みから）。
    desired, before, executable = {}, [], {}
    for rel, path, _link in K.walk(source, tops(plan)):
        if not select(rel):
            continue
        entry, data = K.read_entry(path, rel)
        desired[rel] = data
        executable[rel] = entry["x"]
        before.append(entry)
    for entry in K.dir_catalog(to):
        if entry["path"] not in desired:
            desired[entry["path"]] = None  # 元から消えた物（片道なので、台帳からも除く。前の中身は控えに残る）
    removed = sum(1 for v in desired.values() if v is None)
    first = not record["adopted"]
    result = K.run(to, desired, what="adopt" if first else "readopt", by=by, at=at,
                   executable=executable)
    after_view = K.view(to)
    fp = K.catalog_fp(after_view)
    if result["fp"] is None:
        # 変わる物が無くても、取り込んだ時点として控える（取り込みのたびに版を控える・§5.5）。
        result["fp"] = K.save_snapshot(to, after_view, what="readopt", by=by, at=at)
    source_fp = K.catalog_fp(before)
    source_after = K.catalog_fp(source_catalog(source, plan))
    record["plan"] = plan
    record["adopted"].append({"at": at, "by": by, "fp": fp, "source_fp": source_fp, "files": len(before)})
    K._write_json(_adopt_path(to), record)
    return {"register": to, "source": source, "files": len(before), "bytes": sum(e["size"] for e in before),
            "fp": fp, "source_fp": source_fp, "source_fp_after": source_after, "source_moved": source_after != source_fp,
            "same": fp == source_fp, "changed": result.get("applied", 0), "removed": removed,
            "continued": result.get("continued", False), "batch": result.get("batch"), "decl": copied, "first": first}


# ------------------------------------------------------------------ 戻す出口

def restore(root: str, to: str, *, at_snapshot: str | None = None) -> dict:
    """台帳の全部（か控えた前の版）を、元の場所・名前・バイトで `to` に書く（手元に書くだけ）。
    入れ替えの段取りが残っていれば、入れ替えの前の姿を書く（器の読み手は前か後のどちらかしか見せない）。"""
    to = os.path.abspath(os.path.expanduser(to))
    if _inside(to, root) or _inside(root, to):
        raise RegisterRefused("register_adopt_unusable", "inside")
    if os.path.exists(to) and (os.path.islink(to) or not os.path.isdir(to) or os.listdir(to)):
        raise RegisterRefused("register_adopt_unusable", "not_empty")
    if at_snapshot is not None:
        snap = K.load_snapshot(root, at_snapshot)
        entries = [dict(e, object=True) for e in snap["files"]]
    else:
        entries = K.view(root)
    os.makedirs(to, exist_ok=True)
    for e in entries:
        K._put_file(K.full(to, e["path"]), K.view_bytes(root, e), e.get("x", False))
    written = K.dir_catalog(to)
    fp = K.catalog_fp(entries)
    if K.catalog_fp(written) != fp:
        raise RegisterRefused("register_batch_conflict", "restore_differs")
    return {"to": to, "files": len(entries), "bytes": sum(e["size"] for e in entries), "fp": fp,
            "pending": K.pending(root) is not None, "snapshot": at_snapshot}


# ------------------------------------------------------------------ 変えた跡

def _side(root: str, name: str, tmp: str):
    """`(目録, 器として読んだ台帳)`。`now` は今（段取りが残っていれば前の姿）。ほかは控えた目録。"""
    if name == "now":
        entries = K.view(root)
        if K.pending(root) is None:
            return entries, V.open_vessel(root)
        into = os.path.join(tmp, "now")
        os.makedirs(into)
        for e in entries:
            K._put_file(K.full(into, e["path"]), K.view_bytes(root, e), False)
        for e in K.decl_entries(root, store=False):
            K._put_file(os.path.join(into, K.THTH, *e["path"].split("/")), _read_decl(root, e["path"]), False)
        return entries, V.open_vessel(into)
    snap = K.load_snapshot(root, name)
    into = os.path.join(tmp, snap["fp"][:16])
    os.makedirs(into, exist_ok=True)
    K.materialize(root, snap, into)
    return snap["files"], V.open_vessel(into)


def _read_decl(root: str, rel: str) -> bytes:
    with open(os.path.join(K.thth_dir(root), *rel.split("/")), "rb") as f:
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


def diff(root: str, spec: str) -> dict:
    """`<前>..<後>`（どちらも控えた目録の指紋の頭・`first`・`last`・`now`）。"""
    if not isinstance(spec, str) or spec.count("..") != 1:
        raise RegisterRefused("register_args_invalid", "range")
    left, right = spec.split("..")
    with tempfile.TemporaryDirectory(prefix="thth-diff-") as tmp:
        a_files, a = _side(root, left or "first", tmp)
        b_files, b = _side(root, right or "now", tmp)
        ra, rb = _records(a), _records(b)
        added = sorted(set(rb) - set(ra))
        vanished = sorted(set(ra) - set(rb))
        changed, retired = [], []
        for ref in sorted(set(ra) & set(rb)):
            before, after = ra[ref], rb[ref]
            if sorted(r.version for r in before) != sorted(r.version for r in after):
                units_a = {u.pointer for r in before for u in r.units}
                units_b = {u.pointer for r in after for u in r.units}
                item = {"record": ref, "path": after[0].path}
                if units_a or units_b:
                    item["units"] = {"added": len(units_b - units_a), "vanished": len(units_a - units_b)}
                changed.append(item)
            if not any(_retired(r) for r in before) and any(_retired(r) for r in after):
                retired.append(ref)
        fa = {e["path"]: e["sha256"] for e in a_files}
        fb = {e["path"]: e["sha256"] for e in b_files}
    files = {"added": sorted(set(fb) - set(fa)), "removed": sorted(set(fa) - set(fb)),
             "changed": sorted(p for p in set(fa) & set(fb) if fa[p] != fb[p])}
    return {"from": K.catalog_fp(a_files), "to": K.catalog_fp(b_files),
            "records": {"added": added, "changed": changed, "retired": retired, "vanished": vanished},
            "files": files, "red": bool(vanished or files["removed"])}


# ------------------------------------------------------------------ 束をまとめて入れる

def merge(root: str, bundle: str, *, by: str, at: str, base: str | None = None) -> dict:
    """束のフォルダの全部のファイルを、台帳の同じ道へ、まとめての入れ替えで入れる（足すか置き換えるだけ）。
    `base` を渡すと、台帳の今の目録の指紋がそれで始まるときだけ入れる（束を作った時から台帳が動いていない）。"""
    bundle = os.path.abspath(os.path.expanduser(bundle))
    if os.path.islink(bundle) or not os.path.isdir(bundle):
        raise RegisterRefused("register_adopt_unusable", "bundle")
    if _inside(bundle, root) or _inside(root, bundle):
        raise RegisterRefused("register_adopt_unusable", "inside")
    desired = {}
    for rel, path, is_link in K.walk(bundle):
        if is_link:
            raise RegisterRefused("register_adopt_unusable", "symlink", links=[rel])
        K.safe_rel(rel)
        desired[rel] = K.read_entry(path, rel)[1]
    if not desired:
        raise RegisterRefused("register_adopt_unusable", "bundle_empty")
    plan = K.pending(root)
    if base is not None and plan is None:
        now = K.catalog_fp(K.view(root))
        if not now.startswith(base):
            raise RegisterRefused("register_batch_conflict", "base", now=now[:16])
    result = K.run(root, desired, what="merge", by=by, at=at)
    return {**result, "bundle_files": len(desired)}


# ------------------------------------------------------------------ 表の 1 行を書く

def _split_eol(raw: str) -> tuple:
    for eol in ("\r\n", "\n", "\r"):
        if raw.endswith(eol):
            return raw[:-len(eol)], eol
    return raw, ""


def raw_cells(body: str) -> list:
    """行のセルを、元の字（引用符も含めて）のまま分ける。"""
    cells, start, quoted = [], 0, False
    for i, ch in enumerate(body):
        if ch == '"':
            quoted = not quoted
        elif ch == "," and not quoted:
            cells.append(body[start:i])
            start = i + 1
    cells.append(body[start:])
    return cells


def render_cell(value: str) -> str:
    if any(c in value for c in ',"\r\n'):
        return '"' + value.replace('"', '""') + '"'
    return value


def _join_row(cells: list, eol: str) -> str:
    return ",".join(cells) + eol


def _table(root: str, kind: V.Kind, rel: str):
    data = K.read_file(root, rel)
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    try:
        text = data[len(bom):].decode("utf-8")
    except UnicodeDecodeError:
        raise RegisterRefused("register_write_unsafe", "not_utf8") from None
    lines = V.split_csv_rows(text)
    if "".join(lines) != text or not lines:
        raise RegisterRefused("register_write_unsafe", "table_shape")
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
    header = V._parse_csv_line(lines[0])
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
    cells = raw_cells(body)
    if len(cells) != len(header):
        raise RegisterRefused("register_write_unsafe", "row_width")
    for name, value in changes.items():
        cells[header.index(name)] = render_cell(value)
    new_lines = list(lines)
    new_lines[hits[0]] = _join_row(cells, eol)
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
    header = V._parse_csv_line(lines[0])
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
    last_body, eol = _split_eol(lines[-1])
    if not eol:
        # 最後の行に改行が無い: 足すには、その行のバイトを変えることになる（触っていない行は変えない）。
        raise RegisterRefused("register_write_unsafe", "no_final_newline", path=rel)
    new_lines = list(lines) + [_join_row([render_cell(full_values[n]) for n in header], eol)]
    return _write_table(root, rel, kind, "".join(lines), new_lines, bom, target_id=rid, values=full_values,
                        adding=True, by=by, at=at, what="row_add")
