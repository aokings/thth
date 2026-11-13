"""台帳の器 段 2 の土台: 器の側の控え・まとめての入れ替え・読み手に見せる 1 つの姿（設計 §5.5・§5.7・§10 の段 2）。

- **目録**（`catalog`）: 台帳のファイルの並び（道・大きさ・sha256・実行の印）。`.thth/` と `.git/` は入れない。
  **目録の指紋**（`catalog_fp`）は、道と sha256 と大きさの並びを `vessel.fp` で指紋にした物。指紋が同じなら、
  ファイルの数・名前・sha256 が全部同じ。
- **控え**（`.thth/kept/`）: 中身の sha256 で名を付けた置き場（`objects/`）と、取り込みや入れ替えのたびの目録
  （`snapshots/<目録の指紋>.json`）。台帳が git の外にあっても（MMR）、器が控えた時点の前の版を取り出せる。
  控えより前の版と、控えと控えの間の版は「取り出せない」と言う（設計 §5.5）。
- **まとめての入れ替え**（`Batch`）: 複数のファイルを 1 つのまとまりとして入れ替える。先に新旧の中身を控えに置き、
  段取り（`.thth/batch/pending.json`）を書いてから 1 枚ずつ置き換え、最後に段取りを「済み」へ移す。
  **段取りが残っている間、器の読み手（`view`）には入れ替えの前の姿を見せる**（置き換え済みの枚も、控えの前の中身で
  読む）。途中で止まっても、同じ命令を打ち直すか `resume --continue` で続け、`resume --rollback` で戻せる。
  別の者が打ち直してよい（段取りに書いてある中身だけを使う）。
- 器の外の道具（サイトの build など）が台帳のフォルダを直に読むと、止まった間は前と後が混ざって見える。器が守るのは
  器の読み手（戻す出口・目録・差・材料の出口）まで（正直に書く残り）。

網に出ない（標準の部品と、台帳の機械の部品だけ）。
"""
from __future__ import annotations

import hashlib
import json
import os
import stat

from . import vessel as V
from .errors import RegisterRefused

THTH = V.DECL_DIR
KEPT = "kept"
OBJECTS = "objects"
SNAPSHOTS = "snapshots"
LOG = "log.jsonl"
BATCH = "batch"
PENDING = "pending.json"
SNAPSHOT_SCHEMA = "thth.kept_snapshot/1"
BATCH_SCHEMA = "thth.batch/1"
SKIP_DIRS = (".git",)
DECL_FILES = (V.CONSTITUTION_FILE, V.IDS_FILE)

# 置き換えの 1 手（試験は、ここを差し替えて途中で止める）。
_replace = os.replace


# ------------------------------------------------------------------ 道と読み

def thth_dir(root: str) -> str:
    return os.path.join(root, THTH)


def safe_rel(rel) -> str:
    """台帳の中の道（`/` 区切り・`..` なし・絶対でない・`.thth` と `.git` の中でない）。合わなければ断る。"""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or "\\" in rel or "\x00" in rel:
        raise RegisterRefused("register_args_invalid", "path")
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts) or parts[0] == THTH or any(p in SKIP_DIRS for p in parts):
        raise RegisterRefused("register_args_invalid", "path")
    return rel


def full(root: str, rel: str) -> str:
    return os.path.join(root, *rel.split("/"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def walk(root: str, tops=None, *, skip_thth: bool = True):
    """台帳（か元）の中のファイル: `(道, 絶対の道, symlink か)`。隠しの名前も含める。`.git/` は辿らない。
    台帳の根の `.thth/` は辿らない（器の物。元を歩くときは `skip_thth=False` で見せ、範囲に入れば断る）。
    `tops` を渡すと、その下だけを辿る（元の大きな repo を全部歩かない）。"""
    starts = sorted(set(tops)) if tops else [""]
    seen = set()
    for top in starts:
        if any(top == s or top.startswith(s + "/") for s in seen if s):
            continue
        seen.add(top)
        base = full(root, top) if top else root
        if os.path.islink(base):
            yield top, base, True
            continue
        if os.path.isfile(base):
            yield top, base, False
            continue
        if not os.path.isdir(base):
            continue
        for directory, dirs, files in os.walk(base, followlinks=False):
            rel_dir = os.path.relpath(directory, root).replace(os.sep, "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            keep_dirs = []
            for d in sorted(dirs):
                if d in SKIP_DIRS or (skip_thth and not rel_dir and d == THTH):
                    continue
                path = os.path.join(directory, d)
                if os.path.islink(path):
                    yield (f"{rel_dir}/{d}" if rel_dir else d), path, True
                    continue
                keep_dirs.append(d)
            dirs[:] = keep_dirs
            for name in sorted(files):
                path = os.path.join(directory, name)
                yield (f"{rel_dir}/{name}" if rel_dir else name), path, os.path.islink(path)


def read_entry(path: str, rel: str) -> tuple:
    """`(目録の 1 行, 中身)`。"""
    with open(path, "rb") as f:
        data = f.read()
    mode = os.stat(path).st_mode
    return {"path": rel, "size": len(data), "sha256": sha256(data), "x": bool(mode & stat.S_IXUSR)}, data


def catalog_fp(entries) -> str:
    """目録の指紋: 道・sha256・大きさの並び（道の順）の指紋。実行の印は入れない（数・名前・sha256 を比べる物）。"""
    return V.fp(sorted([e["path"], e["sha256"], e["size"]] for e in entries))


def dir_catalog(root: str, *, select=None, tops=None) -> list:
    """フォルダの目録（読むだけ）。symlink は辿らず断る（中身を元のまま置けないので）。"""
    out, links = [], []
    for rel, path, is_link in walk(root, tops):
        if select is not None and not select(rel):
            continue
        if is_link:
            links.append(rel)
            continue
        out.append(read_entry(path, rel)[0])
    if links:
        raise RegisterRefused("register_adopt_unusable", "symlink", links=links[:20], count=len(links))
    return sorted(out, key=lambda e: e["path"])


# ------------------------------------------------------------------ 控え（中身の置き場と目録）

def _kept(root: str, *parts) -> str:
    return os.path.join(thth_dir(root), KEPT, *parts)


def _atomic_write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_json(path: str, value) -> None:
    _atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def _read_json(path: str, word: str):
    if os.path.islink(path):
        raise RegisterRefused("register_kept_missing", word)
    try:
        with open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise RegisterRefused("register_kept_missing", word) from None


def object_path(root: str, digest: str) -> str:
    return _kept(root, OBJECTS, digest[:2], digest[2:])


def put_object(root: str, data: bytes) -> str:
    digest = sha256(data)
    path = object_path(root, digest)
    if not os.path.exists(path):
        _atomic_write(path, data)
    return digest


def get_object(root: str, digest: str) -> bytes:
    """控えの中身。無いか、中身が名と合わなければ（控えが壊れている）断る。"""
    if not isinstance(digest, str) or len(digest) != 64:
        raise RegisterRefused("register_kept_missing", "object")
    try:
        with open(object_path(root, digest), "rb") as f:
            data = f.read()
    except OSError:
        raise RegisterRefused("register_kept_missing", "object", object=digest[:16]) from None
    if sha256(data) != digest:
        raise RegisterRefused("register_kept_missing", "object_broken", object=digest[:16])
    return data


def decl_entries(root: str, *, store: bool) -> list:
    """種類の宣言・憲法・番号の表（`.thth/` の JSON）の目録。差を読むときに、その時点の宣言で読むため控える。"""
    out = []
    base = thth_dir(root)
    names = [f"{V.KINDS_DIR}/{n}" for n in sorted(os.listdir(os.path.join(base, V.KINDS_DIR)))
             if n.endswith(".json") and not n.startswith(".")] if os.path.isdir(os.path.join(base, V.KINDS_DIR)) else []
    names += [n for n in DECL_FILES if os.path.isfile(os.path.join(base, n))]
    for name in names:
        path = os.path.join(base, *name.split("/"))
        if os.path.islink(path):
            continue
        entry, data = read_entry(path, name)
        if store:
            put_object(root, data)
        out.append(entry)
    return out


def save_snapshot(root: str, entries: list, *, what: str, by: str, at: str, stored: bool = False) -> str:
    """目録を控える（中身も控えの置き場へ）。名は目録の指紋。同じ指紋がもうあれば、跡だけを足す。"""
    for e in entries if not stored else ():
        if not os.path.exists(object_path(root, e["sha256"])):
            if put_object(root, read_file(root, e["path"])) != e["sha256"]:
                raise RegisterRefused("register_batch_conflict", "moved", path=e["path"])
    fp = catalog_fp(entries)
    path = _kept(root, SNAPSHOTS, fp + ".json")
    if not os.path.exists(path):
        _write_json(path, {"schema": SNAPSHOT_SCHEMA, "fp": fp, "files": entries,
                           "decl": decl_entries(root, store=True), "at": at, "by": by, "what": what})
    log(root, {"at": at, "by": by, "what": what, "fp": fp, "files": len(entries)})
    return fp


def log(root: str, item: dict) -> None:
    path = _kept(root, LOG)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def history(root: str) -> list:
    path = _kept(root, LOG)
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def snapshots(root: str) -> list:
    directory = _kept(root, SNAPSHOTS)
    return sorted(n[:-5] for n in os.listdir(directory) if n.endswith(".json")) if os.path.isdir(directory) else []


def load_snapshot(root: str, name: str) -> dict:
    """控えた目録を 1 つ。`first`・`last`（跡の順）か、目録の指紋（頭の 8 字以上）。"""
    hist = [h["fp"] for h in history(root) if h.get("fp")]
    if name in ("first", "last"):
        if not hist:
            raise RegisterRefused("register_kept_missing", name)
        name = hist[0] if name == "first" else hist[-1]
    if not isinstance(name, str) or len(name) < 8 or any(c not in "0123456789abcdef" for c in name):
        raise RegisterRefused("register_args_invalid", "snapshot")
    hits = [s for s in snapshots(root) if s.startswith(name)]
    if not hits:
        raise RegisterRefused("register_kept_missing", "snapshot", asked=name[:16])
    if len(hits) > 1:
        raise RegisterRefused("register_args_invalid", "snapshot", candidates=hits[:10])
    value = _read_json(_kept(root, SNAPSHOTS, hits[0] + ".json"), "snapshot")
    if value.get("schema") != SNAPSHOT_SCHEMA or catalog_fp(value.get("files", [])) != hits[0]:
        raise RegisterRefused("register_kept_missing", "snapshot_broken")
    return value


def materialize(root: str, snap: dict, into: str) -> None:
    """控えた目録の姿を、別のフォルダに書く（読む物を作るだけ。台帳には書かない）。"""
    for e in snap["files"]:
        _put_file(full(into, e["path"]), get_object(root, e["sha256"]), e.get("x", False))
    for e in snap.get("decl", []):
        _put_file(os.path.join(into, THTH, *e["path"].split("/")), get_object(root, e["sha256"]), False)


def _put_file(path: str, data: bytes, executable: bool) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    if executable:
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ------------------------------------------------------------------ 読み手に見せる 1 つの姿

def read_file(root: str, rel: str) -> bytes:
    with open(full(root, rel), "rb") as f:
        return f.read()


def pending(root: str) -> dict | None:
    path = os.path.join(thth_dir(root), BATCH, PENDING)
    if not os.path.exists(path):
        return None
    value = _read_json(path, "batch")
    if value.get("schema") != BATCH_SCHEMA or not isinstance(value.get("entries"), list):
        raise RegisterRefused("register_kept_missing", "batch_broken")
    return value


def view(root: str) -> list:
    """器の読み手に見せる目録。入れ替えの段取りが残っていれば、その道は入れ替えの前の中身で見せる
    （置き換え済みの枚も、前の中身で）。返すのは目録の行に、読む先（`object` か台帳のファイル）を付けた物。"""
    plan = pending(root)
    before = {e["path"]: e for e in plan["entries"]} if plan else {}
    out = []
    for rel, path, is_link in walk(root):
        if rel in before or is_link:
            continue
        entry = read_entry(path, rel)[0]
        out.append(entry)
    for rel, e in before.items():
        if e["old"] is not None:
            out.append({"path": rel, "size": e["old_size"], "sha256": e["old"], "x": e.get("old_x", False),
                        "object": True})
    return sorted(out, key=lambda e: e["path"])


def view_bytes(root: str, entry: dict) -> bytes:
    return get_object(root, entry["sha256"]) if entry.get("object") else read_file(root, entry["path"])


def refuse_if_pending(root: str) -> None:
    plan = pending(root)
    if plan is not None:
        raise RegisterRefused("register_batch_pending", plan.get("what", "batch")[:20], batch=plan["id"],
                              what=plan.get("what"), by=plan.get("by"), at=plan.get("at"),
                              files=len(plan["entries"]))


# ------------------------------------------------------------------ まとめての入れ替え

def _now_sha(root: str, rel: str):
    path = full(root, rel)
    if os.path.islink(path):
        raise RegisterRefused("register_batch_conflict", "symlink", path=rel)
    if not os.path.isfile(path):
        return None
    return sha256(read_file(root, rel))


def prepare(root: str, desired: dict, *, what: str, by: str, at: str, executable: dict | None = None) -> dict | None:
    """入れ替えの段取りを作る（まだ書かない）。`desired` は 道 → 新しい中身（bytes）か None（その道を除く）。
    今と同じ中身の道は段取りに入れない。何も変わらなければ None。"""
    entries = []
    for rel in sorted(desired):
        safe_rel(rel)
        data = desired[rel]
        old = _now_sha(root, rel)
        old_size = os.path.getsize(full(root, rel)) if old is not None else None
        old_x = bool(os.stat(full(root, rel)).st_mode & stat.S_IXUSR) if old is not None else False
        new = put_object(root, data) if data is not None else None
        if old == new:
            continue
        if old is not None:
            put_object(root, read_file(root, rel))
        entries.append({"path": rel, "old": old, "old_size": old_size, "old_x": old_x, "new": new,
                        "new_size": len(data) if data is not None else None,
                        "new_x": bool((executable or {}).get(rel, old_x))})
    if not entries:
        return None
    body = {"what": what, "entries": [[e["path"], e["old"], e["new"]] for e in entries]}
    return {"schema": BATCH_SCHEMA, "id": "b-" + V.fp(body)[:16], "what": what, "by": by, "at": at,
            "entries": entries}


def matches_pending(root: str, plan: dict, desired: dict) -> bool:
    """残っている段取りが、この求め（道 → 新しい中身）の続きか。段取りの全部の道で新しい中身が求めと同じで、
    段取りの外の求めは今と同じ中身なら、続きと見る。"""
    want = {rel: (sha256(data) if data is not None else None) for rel, data in desired.items()}
    planned = {e["path"] for e in plan["entries"]}
    for e in plan["entries"]:
        if e["path"] not in want or want[e["path"]] != e["new"]:
            return False
    for rel, digest in want.items():
        if rel not in planned and _now_sha(root, rel) != digest:
            return False
    return True


def begin(root: str, plan: dict) -> None:
    """段取りを書く（ここから先は、止まっても続けるか戻すかが決まる）。"""
    if pending(root) is not None:
        refuse_if_pending(root)
    _write_json(os.path.join(thth_dir(root), BATCH, PENDING), plan)


def _put_now(root: str, rel: str, digest, executable: bool) -> None:
    target = full(root, rel)
    if digest is None:
        if os.path.isfile(target):
            os.unlink(target)
        return
    data = get_object(root, digest)
    tmpdir = os.path.join(thth_dir(root), BATCH, "tmp")
    os.makedirs(tmpdir, exist_ok=True)
    tmp = os.path.join(tmpdir, digest[:16] + f".{os.getpid()}")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if executable:
        os.chmod(tmp, os.stat(tmp).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    _replace(tmp, target)


def _finish(root: str, plan: dict, state: str) -> None:
    done = os.path.join(thth_dir(root), BATCH, state, plan["id"] + ".json")
    os.makedirs(os.path.dirname(done), exist_ok=True)
    os.replace(os.path.join(thth_dir(root), BATCH, PENDING), done)


def apply(root: str, *, by: str, at: str) -> dict:
    """残っている段取りを最後まで進める（同じ命令の打ち直しと `resume --continue` が呼ぶ。別の者でよい）。
    置き換え済みの道は飛ばす。前でも後でもない中身の道があれば（ほかの者が書いた）、止めて断る。"""
    plan = pending(root)
    if plan is None:
        return {"applied": 0}
    applied = 0
    for e in plan["entries"]:
        now = _now_sha(root, e["path"])
        if now == e["new"]:
            continue
        if now != e["old"]:
            raise RegisterRefused("register_batch_conflict", "moved", path=e["path"], batch=plan["id"])
        _put_now(root, e["path"], e["new"], e.get("new_x", False))
        applied += 1
    entries = view_after(root)
    fp = save_snapshot(root, entries, what=plan["what"], by=by, at=at)
    plan["done"] = {"by": by, "at": at, "fp": fp}
    _write_json(os.path.join(thth_dir(root), BATCH, PENDING), plan)
    _finish(root, plan, "done")
    return {"applied": applied, "fp": fp, "batch": plan["id"], "files": len(plan["entries"])}


def rollback(root: str, *, by: str, at: str) -> dict:
    """残っている段取りを戻す（置き換え済みの道を前の中身に）。前でも後でもない道があれば止めて断る。"""
    plan = pending(root)
    if plan is None:
        return {"restored": 0}
    restored = 0
    for e in reversed(plan["entries"]):
        now = _now_sha(root, e["path"])
        if now == e["old"]:
            continue
        if now != e["new"]:
            raise RegisterRefused("register_batch_conflict", "moved", path=e["path"], batch=plan["id"])
        _put_now(root, e["path"], e["old"], e.get("old_x", False))
        restored += 1
    plan["rolled_back"] = {"by": by, "at": at}
    _write_json(os.path.join(thth_dir(root), BATCH, PENDING), plan)
    _finish(root, plan, "rolled_back")
    log(root, {"at": at, "by": by, "what": "rollback", "batch": plan["id"]})
    return {"restored": restored, "batch": plan["id"], "files": len(plan["entries"]),
            "fp": catalog_fp(view_after(root))}


def view_after(root: str) -> list:
    """台帳のファイルそのものの目録（段取りを見ない）。入れ替えが済んだあとの姿。"""
    return dir_catalog(root)


def run(root: str, desired: dict, *, what: str, by: str, at: str, executable: dict | None = None) -> dict:
    """求めた入れ替えを、まとまりとして行う。残っている段取りがこの求めの続きなら続ける（打ち直し）。
    別の求めの段取りが残っていれば断る（続けるか戻すかを先に決める）。"""
    plan = pending(root)
    if plan is not None:
        if not matches_pending(root, plan, desired):
            refuse_if_pending(root)
        result = apply(root, by=by, at=at)
        return {**result, "continued": True}
    plan = prepare(root, desired, what=what, by=by, at=at, executable=executable)
    if plan is None:
        return {"applied": 0, "files": 0, "fp": None, "batch": None, "continued": False}
    begin(root, plan)
    result = apply(root, by=by, at=at)
    return {**result, "continued": False}
