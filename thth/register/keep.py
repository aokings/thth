"""台帳の器 段 2 の土台: 器の側の控え・まとめての入れ替え・読み手に見せる 1 つの姿（設計 §5.5・§5.7・§10 の段 2）。

- **目録**（`catalog`）: 台帳のファイルの並び（道・大きさ・sha256・実行の印）。`.thth/` と `.git/` は入れない。
  **目録の指紋**（`catalog_fp`）は、道と sha256 と大きさの並びを `vessel.fp` で指紋にした物。指紋が同じなら、
  ファイルの数・名前・sha256 が全部同じ（「元と同じか」を比べる物なので、実行の印は入れない）。
- **控え**（`.thth/kept/`）: 中身の sha256 で名を付けた置き場（`objects/`）と、取り込みや入れ替えのたびの目録
  （`snapshots/<控えの名>.json`）。**控えの名**（`snapshot_id`）は、目録（実行の印も）と、その時点の宣言（種類・
  憲法・番号の表）の両方の指紋。宣言だけを足した取り込み直しも、実行の印だけの変化も、別の控えになる。
  台帳が git の外にあっても（MMR）、器が控えた時点の前の版を取り出せる。控えより前と、控えと控えの間の版は
  「取り出せない」と言う（設計 §5.5）。
- **まとめての入れ替え**（`Batch`）: 複数のファイルを 1 つのまとまりとして入れ替える。先に新旧の中身を控えに置き、
  段取り（`.thth/batch/plans/<世代>.json`）を「無ければ置く」で置いてから 1 枚ずつ置き換え、最後に済んだ記録
  （`.thth/batch/ended/<世代>.json`）を「無ければ置く」で置く。段取りのファイルは置いたら書き換えも退かしもしない
  ので、どの瞬間で止まっても、読み手は前か後のどちらかを見て、再開は段取りを読める。
  **段取りが残っている間、器の読み手（`view`）には入れ替えの前の姿を見せる**（置き換え済みの枚も、控えの前の中身で
  読む）。途中で止まっても、同じ命令を打ち直すか `resume --continue` で続け、`resume --rollback` で戻せる。
  別の者が打ち直してよい（段取りに書いてある中身だけを使う）。
- **1 枚の置き換え**は、今の枚を器の置き場へ退かし（`rename`・ここで今の枚を 1 つに決める）、退かした枚が段取りの
  前の中身かを確かめてから、新しい枚を「無ければ置く」（`link`）で置く。確かめたあとにほかの者が書いた枚は、
  退かした枚か新しく置かれた枚として必ず見つかり、上書きせずに断る。退かしたあとで落ちても、打ち直しが退かした枚
  から続ける。
- **読み手の世代**（`.thth/batch/seq`）: 入れ替えを始めるときと済ませるときに 1 つ進める。読み手は読み始めと
  読み終わりに世代と段取りを見て、変わっていたら読み直す（読み始めた直後に始まった入れ替えを混ぜない）。
- **symlink**: 器が書く先と読む先（台帳のファイル・`.thth/` の中）は、台帳の根からその先までのどの段も symlink で
  ない所だけ。symlink の段があれば、書く前に断る（台帳の外、たとえば元の repo へ書かない）。
- 器の外の道具（サイトの build など）が台帳のフォルダを直に読むと、止まった間は前と後が混ざって見え、1 枚の置き換えの
  間は枚が無く見える。器が守るのは器の読み手（戻す出口・目録・差・材料の出口）まで（正直に書く残り）。

- **消す求め**（段 5・`erase.py`）だけは、控えの置き場から中身を除き（`remove_object`）、控えの目録の行から sha256 と
  大きさを外して「消した」と記す（`write_snapshot`）。外した行のある目録は名を作り直せないので、目録が求めの番号を
  持つときだけ読み（`load_snapshot`）、外した行の中身は「取り出せない」と言う。

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
PLANS = "plans"          # 段取り: plans/<世代>.json（無ければ置く。置いたら書き換えない）
ENDED = "ended"          # 済んだ（か戻した）記録: ended/<世代>.json（無ければ置く。1 つの段取りに 1 度だけ）
SEQ = "seq"
SNAPSHOT_SCHEMA = "thth.kept_snapshot/1"
BATCH_SCHEMA = "thth.batch/1"
SKIP_DIRS = (".git",)
DECL_FILES = (V.CONSTITUTION_FILE, V.IDS_FILE)
READ_TRIES = 8


def _publish(tmp: str, target: str) -> None:
    """新しい枚を「無ければ置く」（同じ道にほかの者が先に置いていれば FileExistsError）。"""
    os.link(tmp, target)
    os.unlink(tmp)


# 新しい枚を置く 1 手（試験は、ここを差し替えて途中で止める）。
_replace = _publish


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


def guard(root: str, path: str) -> str:
    """`path` が台帳の根 `root` の中で、根そのものとその先までのどの段も symlink でないことを確かめる
    （書く前と、器の置き場を読む前）。合わなければ `register_batch_conflict`（symlink）で断る。"""
    base = os.path.abspath(root)
    target = os.path.abspath(path)
    rel = os.path.relpath(target, base)
    if os.path.isabs(rel) or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        raise RegisterRefused("register_batch_conflict", "symlink", path=rel.replace(os.sep, "/"))
    if os.path.islink(base):
        raise RegisterRefused("register_batch_conflict", "symlink", path=".")
    current = base
    for part in ([] if rel == os.curdir else rel.split(os.sep)):
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise RegisterRefused("register_batch_conflict", "symlink",
                                  path=os.path.relpath(current, base).replace(os.sep, "/"))
        if not os.path.lexists(current):
            break
    return target


def check_thth(root: str) -> None:
    """器の置き場（`.thth/`）とその中のどの段も symlink でないことを、書き始める前に一度に確かめる。"""
    base = guard(root, thth_dir(root))
    if not os.path.isdir(base):
        return
    for directory, dirs, files in os.walk(base, followlinks=False, onerror=_walk_error(root)):
        for name in dirs + files:
            path = os.path.join(directory, name)
            if os.path.islink(path):
                raise RegisterRefused("register_batch_conflict", "symlink",
                                      path=os.path.relpath(path, root).replace(os.sep, "/"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _link_on_the_way(root: str, top: str):
    """`top` までの段（根の下）のうち、最初の symlink の道。無ければ None。"""
    parts = top.split("/")
    for i in range(1, len(parts) + 1):
        rel = "/".join(parts[:i])
        if os.path.islink(full(root, rel)):
            return rel
    return None


def _unreadable(root: str, path: str):
    rel = os.path.relpath(path, root).replace(os.sep, "/") if path else "."
    return RegisterRefused("register_adopt_unusable", "unreadable", path=rel)


def _walk_error(root: str):
    """`os.walk` は読めないフォルダを黙って飛ばす。飛ばすと、その下の枚を「消えた」と読んでしまうので断る。"""
    def raise_(err: OSError):
        raise _unreadable(root, getattr(err, "filename", None) or "") from None
    return raise_


def walk(root: str, tops=None, *, skip_thth: bool = True):
    """台帳（か元）の中のファイル: `(道, 絶対の道, symlink か)`。隠しの名前も含める。`.git/` は辿らない。
    台帳の根の `.thth/` は辿らない（器の物。元を歩くときは `skip_thth=False` で見せ、範囲に入れば断る）。
    `tops` を渡すと、その下だけを辿る（元の大きな repo を全部歩かない）。`tops` の途中の段が symlink なら、
    その段を symlink として返す（辿らない）。"""
    starts = sorted(set(tops)) if tops else [""]
    seen = set()
    for top in starts:
        if any(top == s or top.startswith(s + "/") for s in seen if s):
            continue
        seen.add(top)
        if top:
            link = _link_on_the_way(root, top)
            if link is not None:
                yield link, full(root, link), True
                continue
        base = full(root, top) if top else root
        try:
            mode = os.stat(base).st_mode  # 途中の symlink の段は上で返した（根そのものは辿る）
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            raise _unreadable(root, base) from None  # 途中の段が読めない（無いのか分からない）
        if stat.S_ISREG(mode):
            yield top, base, False
            continue
        if not stat.S_ISDIR(mode):
            continue
        for directory, dirs, files in os.walk(base, followlinks=False, onerror=_walk_error(root)):
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


def _is_x(mode: int) -> bool:
    return bool(mode & stat.S_IXUSR)


def read_entry(path: str, rel: str) -> tuple:
    """`(目録の 1 行, 中身)`。開いた 1 つの枚から、中身と実行の印を読む。権限で読めなければ断る（黙って飛ばさない）。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
            mode = os.fstat(f.fileno()).st_mode
    except PermissionError:
        raise RegisterRefused("register_adopt_unusable", "unreadable", path=rel or os.path.basename(path)) from None
    return {"path": rel, "size": len(data), "sha256": sha256(data), "x": _is_x(mode)}, data


def catalog_fp(entries) -> str:
    """目録の指紋: 道・sha256・大きさの並び（道の順）の指紋。実行の印は入れない（数・名前・sha256 を比べる物）。"""
    return V.fp(sorted([e["path"], e["sha256"], e["size"]] for e in entries))


def snapshot_id(entries, decl) -> str:
    """控えの名: 目録（実行の印も）と宣言の目録の指紋。"""
    return V.fp({"files": sorted([e["path"], e["sha256"], e["size"], bool(e.get("x", False))] for e in entries),
                 "decl": sorted([e["path"], e["sha256"], e["size"]] for e in decl)})


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


def makedirs(root: str, directory: str) -> None:
    """台帳の中にフォルダを作る（途中の段が symlink なら断る）。"""
    guard(root, directory)
    os.makedirs(directory, exist_ok=True)
    guard(root, directory)


def _atomic_write(root: str, path: str, data: bytes) -> None:
    makedirs(root, os.path.dirname(path))
    guard(root, path)
    tmp = f"{path}.{os.getpid()}.tmp"
    guard(root, tmp)
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_json(root: str, path: str, value) -> None:
    _atomic_write(root, path, (json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def _read_json(root: str, path: str, word: str):
    guard(root, path)
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
    guard(root, path)
    if not os.path.exists(path):
        _atomic_write(root, path, data)
    return digest


def get_object(root: str, digest: str) -> bytes:
    """控えの中身。無いか、中身が名と合わなければ（控えが壊れている）断る。"""
    if not isinstance(digest, str) or len(digest) != 64:
        raise RegisterRefused("register_kept_missing", "object")
    path = guard(root, object_path(root, digest))
    try:
        with open(path, "rb") as f:
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
    kinds = os.path.join(base, V.KINDS_DIR)
    guard(root, kinds)
    names = [f"{V.KINDS_DIR}/{n}" for n in sorted(os.listdir(kinds))
             if n.endswith(".json") and not n.startswith(".")] if os.path.isdir(kinds) else []
    names += [n for n in DECL_FILES if os.path.isfile(os.path.join(base, n))]
    for name in names:
        path = guard(root, os.path.join(base, *name.split("/")))
        entry, data = read_entry(path, name)
        if store:
            put_object(root, data)
        out.append(entry)
    return out


def save_snapshot(root: str, entries: list, *, what: str, by: str, at: str, stored: bool = False,
                  write_log: bool = True) -> dict:
    """目録を控える（中身も控えの置き場へ）。名は控えの名（目録と宣言の指紋）。同じ名がもうあれば、跡だけを足す。
    返すのは `{"fp": 目録の指紋, "snapshot": 控えの名}`。"""
    for e in entries if not stored else ():
        if not os.path.exists(object_path(root, e["sha256"])):
            if put_object(root, read_file(root, e["path"])) != e["sha256"]:
                raise RegisterRefused("register_batch_conflict", "moved", path=e["path"])
    decl = decl_entries(root, store=True)
    fp = catalog_fp(entries)
    sid = snapshot_id(entries, decl)
    path = _kept(root, SNAPSHOTS, sid + ".json")
    guard(root, path)
    if not os.path.exists(path):
        _write_json(root, path, {"schema": SNAPSHOT_SCHEMA, "id": sid, "fp": fp, "files": entries, "decl": decl,
                                 "at": at, "by": by, "what": what})
    if write_log:
        log(root, {"at": at, "by": by, "what": what, "fp": fp, "snapshot": sid, "files": len(entries)})
    return {"fp": fp, "snapshot": sid}


def log(root: str, item: dict) -> None:
    path = _kept(root, LOG)
    makedirs(root, os.path.dirname(path))
    guard(root, path)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def history(root: str) -> list:
    path = guard(root, _kept(root, LOG))
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
    directory = guard(root, _kept(root, SNAPSHOTS))
    return sorted(n[:-5] for n in os.listdir(directory) if n.endswith(".json")) if os.path.isdir(directory) else []


def load_snapshot(root: str, name: str) -> dict:
    """控えた目録を 1 つ。`first`・`last`（跡の順）か、控えの名か目録の指紋（頭の 8 字以上）。"""
    hist = [h.get("snapshot") or h["fp"] for h in history(root) if h.get("snapshot") or h.get("fp")]
    if name in ("first", "last"):
        if not hist:
            raise RegisterRefused("register_kept_missing", name)
        name = hist[0] if name == "first" else hist[-1]
    if not isinstance(name, str) or len(name) < 8 or any(c not in "0123456789abcdef" for c in name):
        raise RegisterRefused("register_args_invalid", "snapshot")
    hits = [s for s in snapshots(root) if s.startswith(name)]
    if not hits:
        # 目録の指紋の頭で求められたら、その目録を最後に控えた控え。
        by_fp = [h.get("snapshot") for h in history(root) if str(h.get("fp", "")).startswith(name) and h.get("snapshot")]
        hits = sorted(set(by_fp[-1:]))
    if not hits:
        raise RegisterRefused("register_kept_missing", "snapshot", asked=name[:16])
    if len(hits) > 1:
        raise RegisterRefused("register_args_invalid", "snapshot", candidates=hits[:10])
    value = _read_json(root, _kept(root, SNAPSHOTS, hits[0] + ".json"), "snapshot")
    if value.get("schema") != SNAPSHOT_SCHEMA:
        raise RegisterRefused("register_kept_missing", "snapshot_broken")
    if erased_entries(value):
        # 消す求めで中身の sha256 を外した控え（段 5 §3.3）: 名は作り直せない。外した行は「消した」と記してあり、
        # 求めの番号を目録が持つときだけ受ける（外した行の中身は取り出せない）。
        if not isinstance(value.get("erased"), list) or any(
                not e.get("erased") or e["erased"] not in value["erased"] for e in erased_entries(value)):
            raise RegisterRefused("register_kept_missing", "snapshot_broken")
        return value
    if snapshot_id(value.get("files", []), value.get("decl", [])) != hits[0]:
        raise RegisterRefused("register_kept_missing", "snapshot_broken")
    return value


def erased_entries(snap: dict) -> list:
    """控えの目録のうち、消す求めで sha256 を外した行。"""
    return [e for e in snap.get("files", []) if isinstance(e, dict) and e.get("sha256") is None]


def snapshot_path(root: str, name: str) -> str:
    return guard(root, _kept(root, SNAPSHOTS, name + ".json"))


def write_snapshot(root: str, name: str, value: dict) -> None:
    """消す求めが、控えの目録から sha256 を外して書き直す（段 5 §3.3。ほかの口は控えの目録を書き直さない）。"""
    _write_json(root, snapshot_path(root, name), value)


def remove_object(root: str, digest: str) -> bool:
    """消す求めが、控えの置き場から 1 つの中身を除く（段 5 §3.3）。除いたら True・もう無ければ False。"""
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise RegisterRefused("register_args_invalid", "object")
    path = guard(root, object_path(root, digest))
    try:
        os.unlink(path)
    except FileNotFoundError:
        return False
    return True


def object_digests(root: str) -> list:
    """控えの置き場の中身の名（sha256）の並び。"""
    base = guard(root, _kept(root, OBJECTS))
    out = []
    if not os.path.isdir(base):
        return out
    for head in sorted(os.listdir(base)):
        sub = os.path.join(base, head)
        if len(head) != 2 or not os.path.isdir(sub) or os.path.islink(sub):
            continue
        for rest in sorted(os.listdir(sub)):
            digest = head + rest
            if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
                out.append(digest)
    return out


def materialize(root: str, snap: dict, into: str) -> None:
    """控えた目録の姿を、別のフォルダに書く（読む物を作るだけ。台帳には書かない）。"""
    for e in snap["files"]:
        if e.get("sha256") is None:
            raise RegisterRefused("register_kept_missing", "erased", path=e.get("path"))
        put_file(into, e["path"], get_object(root, e["sha256"]), e.get("x", False))
    for e in snap.get("decl", []):
        put_file(into, f"{THTH}/{e['path']}", get_object(root, e["sha256"]), False)


def put_file(into: str, rel: str, data: bytes, executable: bool) -> None:
    """器の外のフォルダ（戻す先・差の作業場）に 1 枚を書く。途中の段が symlink なら断る。無い枚だけを作る。"""
    path = full(into, rel)
    makedirs(into, os.path.dirname(path))
    guard(into, path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o777 if executable else 0o666)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if executable:
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ------------------------------------------------------------------ 読み手に見せる 1 つの姿

def read_file(root: str, rel: str) -> bytes:
    with open(guard(root, full(root, rel)), "rb") as f:
        return f.read()


def _plans_dir(root: str) -> str:
    return os.path.join(thth_dir(root), BATCH, PLANS)


def _plan_path(root: str, gen: int) -> str:
    return guard(root, os.path.join(_plans_dir(root), f"{gen}.json"))


def _end_path(root: str, plan: dict) -> str:
    return guard(root, os.path.join(thth_dir(root), BATCH, ENDED, f"{int(plan.get('gen', 0))}.json"))


def _gens(root: str) -> list:
    directory = guard(root, _plans_dir(root))
    try:
        names = os.listdir(directory)
    except FileNotFoundError:
        return []
    except OSError:
        raise RegisterRefused("register_adopt_unusable", "unreadable", path=f"{THTH}/{BATCH}/{PLANS}") from None
    return sorted(int(n[:-5]) for n in names if n.endswith(".json") and n[:-5].isdigit())


def _pending_file(root: str) -> dict | None:
    """いちばん新しい段取り（済んでいても）。段取りは世代の名で置き、置いたら書き換えず、済んだかどうかは別の
    記録（`ended/<世代>.json`）で言う。だから段取りを退かす瞬間が無く、どこで止まっても読み手は前か後のどちらかを見る。"""
    for _ in range(READ_TRIES):
        gens = _gens(root)
        if not gens:
            return None
        path = _plan_path(root, gens[-1])
        if not os.path.exists(path):
            continue  # 読む間に片づけられた古い段取り（新しい物がもうある）: 並びを読み直す
        value = _read_json(root, path, "batch")
        if value.get("schema") != BATCH_SCHEMA or not isinstance(value.get("entries"), list) \
                or value.get("gen") != gens[-1]:
            raise RegisterRefused("register_kept_missing", "batch_broken")
        return value
    raise RegisterRefused("register_batch_conflict", "busy", tries=READ_TRIES)


def _ended(root: str, plan: dict) -> bool:
    return os.path.exists(_end_path(root, plan))


def pending(root: str) -> dict | None:
    """止まっている（まだ済んでも戻してもいない）段取り。無ければ None。"""
    plan = _pending_file(root)
    if plan is None or _ended(root, plan):
        return None
    return plan


def _seq_path(root: str) -> str:
    return os.path.join(thth_dir(root), BATCH, SEQ)


def seq(root: str) -> int:
    path = guard(root, _seq_path(root))
    try:
        with open(path, "rb") as f:
            return int(f.read().decode("ascii").strip() or "0")
    except (OSError, ValueError, UnicodeDecodeError):
        return 0


def _bump(root: str) -> None:
    _atomic_write(root, _seq_path(root), f"{seq(root) + 1}\n".encode("ascii"))


def generation(root: str, *, end: bool = False) -> tuple:
    """読み手の世代: (世代の数, 段取りの名)。読み始めは数→段取り、読み終わりは段取り→数の順に読む。"""
    def ident():
        plan = _pending_file(root)
        return (plan["id"], plan["gen"], _ended(root, plan)) if plan else None
    if end:
        now = ident()
        return seq(root), now
    number = seq(root)
    return number, ident()


def consistent(root: str, read):
    """`read()` を、読み始めと読み終わりの世代が同じになるまで読み直す（入れ替えと混ぜない）。"""
    for _ in range(READ_TRIES):
        start = generation(root)
        value = read()
        if generation(root, end=True) == start:
            return value
    raise RegisterRefused("register_batch_conflict", "busy", tries=READ_TRIES)


def _view_once(root: str) -> list:
    plan = pending(root)
    before = {e["path"]: e for e in plan["entries"]} if plan else {}
    out, links = [], []
    for rel, path, is_link in walk(root):
        if is_link:
            links.append(rel)
            continue
        if rel in before:
            continue
        out.append(read_entry(path, rel)[0])
    if links:
        raise RegisterRefused("register_adopt_unusable", "symlink", links=links[:20], count=len(links))
    for rel, e in before.items():
        if e["old"] is not None:
            out.append({"path": rel, "size": e["old_size"], "sha256": e["old"], "x": e.get("old_x", False),
                        "object": True})
    return sorted(out, key=lambda e: e["path"])


def view(root: str) -> list:
    """器の読み手に見せる目録。入れ替えの段取りが残っていれば、その道は入れ替えの前の中身で見せる
    （置き換え済みの枚も、前の中身で）。読んでいる間に入れ替えが始まるか済めば、読み直す。
    返すのは目録の行に、読む先（`object` か台帳のファイル）を付けた物。"""
    guard(root, thth_dir(root))
    return consistent(root, lambda: _view_once(root))


def view_bytes(root: str, entry: dict) -> bytes:
    """目録の 1 行の中身。台帳のファイルが目録を読んだあとで変わっていれば、控えの同じ中身を使う
    （入れ替えは前の中身を先に控えに置く）。控えにも無ければ断る。"""
    if entry.get("object"):
        return get_object(root, entry["sha256"])
    data = read_file(root, entry["path"])
    if sha256(data) == entry["sha256"]:
        return data
    return get_object(root, entry["sha256"])


def refuse_if_pending(root: str) -> None:
    plan = pending(root)
    if plan is not None:
        raise RegisterRefused("register_batch_pending", plan.get("what", "batch")[:20], batch=plan["id"],
                              what=plan.get("what"), by=plan.get("by"), at=plan.get("at"),
                              files=len(plan["entries"]))


# ------------------------------------------------------------------ まとめての入れ替え

def _state_at(path: str):
    """1 枚の今の中身と実行の印: `(sha256, x)`。無ければ None。symlink なら断る。"""
    if os.path.islink(path):
        raise RegisterRefused("register_batch_conflict", "symlink", path=os.path.basename(path))
    if not os.path.isfile(path):
        return None
    return tuple(read_entry(path, "")[0][k] for k in ("sha256", "x"))


def _now_state(root: str, rel: str):
    return _state_at(guard(root, full(root, rel)))


def prepare(root: str, desired: dict, *, what: str, by: str, at: str, executable: dict | None = None) -> dict | None:
    """入れ替えの段取りを作る（まだ書かない）。`desired` は 道 → 新しい中身（bytes）か None（その道を除く）。
    `executable` は 道 → 実行の印（無い道は今の印のまま）。今と同じ中身・同じ印の道は段取りに入れない。
    何も変わらなければ None。"""
    entries = []
    for rel in sorted(desired):
        safe_rel(rel)
        data = desired[rel]
        now = _now_state(root, rel)
        old, old_x = now if now else (None, False)
        new = sha256(data) if data is not None else None
        new_x = bool((executable or {}).get(rel, old_x)) if data is not None else False
        if old == new and (new is None or old_x == new_x):
            continue
        if data is not None:
            put_object(root, data)
        old_size = None
        if old is not None:
            current = read_file(root, rel)
            if put_object(root, current) != old:
                raise RegisterRefused("register_batch_conflict", "moved", path=rel)
            old_size = len(current)
        entries.append({"path": rel, "old": old, "old_size": old_size, "old_x": old_x, "new": new,
                        "new_size": len(data) if data is not None else None, "new_x": new_x})
    if not entries:
        return None
    body = {"what": what, "entries": [[e["path"], e["old"], e["old_x"], e["new"], e["new_x"]] for e in entries]}
    return {"schema": BATCH_SCHEMA, "id": "b-" + V.fp(body)[:16], "what": what, "by": by, "at": at,
            "entries": entries}


def matches_pending(root: str, plan: dict, desired: dict, executable: dict | None = None) -> bool:
    """残っている段取りが、この求め（道 → 新しい中身）の続きか。段取りの全部の道で新しい中身（と実行の印）が
    求めと同じで、段取りの外の求めは今と同じ中身と同じ実行の印なら、続きと見る。"""
    want = {rel: (sha256(data) if data is not None else None) for rel, data in desired.items()}
    planned = {e["path"] for e in plan["entries"]}
    for e in plan["entries"]:
        if e["path"] not in want or want[e["path"]] != e["new"]:
            return False
        if executable and e["new"] is not None and e["path"] in executable \
                and bool(executable[e["path"]]) != bool(e.get("new_x", False)):
            return False
    for rel, digest in want.items():
        if rel in planned:
            continue
        now = _now_state(root, rel)
        if digest is None:
            if now is not None:
                return False
            continue
        # 段取りの外の求めは、今と同じ中身で、同じ実行の印（求めに印が無ければ今の印のまま）のときだけ続き。
        if now is None or now[0] != digest or (executable and rel in executable and bool(executable[rel]) != now[1]):
            return False
    return True


def begin(root: str, plan: dict) -> None:
    """段取りを置く（ここから先は、止まっても続けるか戻すかが決まる）。段取りは次の世代の名で「無ければ置く」
    （`link`）。同じ時に始めたほかの者と同じ世代を取り合えば、1 人だけが置ける。読み手の世代を先に進める。"""
    last = _pending_file(root)
    if last is not None and not _ended(root, last):
        refuse_if_pending(root)
        raise RegisterRefused("register_batch_pending", "batch", batch=last["id"])
    if last is not None:
        _settle(root, last)  # 済んだ記録のあとで落ちていれば、控えの跡を先に足す
    _bump(root)
    gen = (int(last["gen"]) + 1) if last is not None else 1
    plan["gen"] = gen  # 段取りの世代: 同じ名の段取りが後で始まっても、別の物として見分ける
    path = _plan_path(root, gen)
    makedirs(root, os.path.dirname(path))
    tmp = os.path.join(_tmpdir(root), f"plan-{gen}.{os.getpid()}.new")
    _write_json(root, tmp, plan)
    try:
        os.link(tmp, path)  # 無ければ置く: 同じ時に始めたほかの者の段取りを上書きしない
    except FileExistsError:
        os.unlink(tmp)
        refuse_if_pending(root)
        raise RegisterRefused("register_batch_pending", "batch") from None
    os.unlink(tmp)
    # 済んだ前の段取りは片づけない（片づけると、遅れて始めた者が同じ世代の名で置けてしまう）。


def _tmpdir(root: str) -> str:
    path = os.path.join(thth_dir(root), BATCH, "tmp")
    makedirs(root, path)
    return path


def _aside(root: str, plan: dict, index: int) -> str:
    return guard(root, os.path.join(_tmpdir(root), f"{plan['id']}-{plan.get('gen', 0)}-{index}.aside"))


def _conflict(plan: dict, rel: str, word: str = "moved", **extra):
    return RegisterRefused("register_batch_conflict", word, path=rel, batch=plan["id"], **extra)


def _put_back(aside: str, target: str) -> bool:
    """退かした枚を戻す（同じ道にほかの者が置いていれば、戻さずに退かした枚を残す）。"""
    try:
        os.link(aside, target)
    except FileExistsError:
        return False
    os.unlink(aside)
    return True


def _step(root: str, plan: dict, index: int, frm, to) -> bool:
    """段取りの 1 枚を `frm`（今のはずの (sha256, x) か None）から `to` へ。済んでいれば False。
    今が `frm` でも `to` でもなければ（ほかの者が書いた）断る。"""
    rel = plan["entries"][index]["path"]
    target = guard(root, full(root, rel))
    aside = _aside(root, plan, index)
    now = _state_at(target)
    if now == to:
        if os.path.lexists(aside) and _state_at(aside) == frm:
            os.unlink(aside)  # 置いたあとで落ちた: 退かした前の枚（控えにある）を片づける
        return False
    grabbed = False
    if now is None and os.path.lexists(aside):
        held = _state_at(aside)
        if held == to:
            # 逆向きの途中で落ちた（戻す先の枚を退かしたまま）: その枚を戻せば済む。
            if not _put_back(aside, target):
                raise _conflict(plan, rel)
            return False  # 新しい枚はまだ置いていなかった（数えない）
        if frm is None or held != frm:
            raise _conflict(plan, rel, aside=os.path.relpath(aside, root))
        grabbed = True  # 退かしたあとで落ちた: 退かした枚から続ける
    elif now != frm:
        raise _conflict(plan, rel)
    tmp = None
    if to is not None:
        data = get_object(root, to[0])
        tmp = os.path.join(_tmpdir(root), f"{plan['id']}-{index}.{os.getpid()}.new")
        guard(root, tmp)
        if os.path.lexists(tmp):
            os.unlink(tmp)
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        mode = os.stat(tmp).st_mode
        os.chmod(tmp, (mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) if to[1]
                 else (mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)))
    side = os.path.relpath(aside, root).replace(os.sep, "/")
    if frm is not None and not grabbed:
        os.rename(target, aside)  # 今の枚を 1 つに決める（ここから先にほかの者が書けば、新しい枚として見つかる）
        if _state_at(aside) != frm:
            if tmp:
                os.unlink(tmp)
            if _put_back(aside, target):
                raise _conflict(plan, rel)
            raise _conflict(plan, rel, aside=side)  # 戻す道にもほかの者が置いた: 両方を残す
    if to is not None:
        makedirs(root, os.path.dirname(target))
        guard(root, target)
        try:
            _replace(tmp, target)
        except FileExistsError:
            os.unlink(tmp)
            # ほかの者が新しい道に置いた: その枚はそのまま。退かした前の枚も残して断る（何も上書きしない）。
            raise _conflict(plan, rel, **({"aside": side} if frm is not None else {})) from None
    # 置いたあと: 退かした枚（開いていた者が直に書きうる）と新しい道（ほかの者が置き換えうる）の両方を見る。
    # どちらかが動いていれば、どれも書き換えずに断る（ほかの者の書き込みは、そのまま残る）。
    moved_aside = frm is not None and _state_at(aside) != frm
    moved_target = to is not None and _state_at(target) != to
    if moved_aside or moved_target:
        raise _conflict(plan, rel, **({"aside": side} if frm is not None else {}))
    if frm is not None:
        os.unlink(aside)
    return True


def _mine(root: str, plan: dict) -> bool:
    """いちばん新しい段取りが、まだこの段取り（同じ名と世代）で、済んでも戻してもいないか。"""
    now = _pending_file(root)
    return now is not None and now.get("id") == plan["id"] and now.get("gen") == plan.get("gen") \
        and not _ended(root, plan)


def _finish(root: str, plan: dict, state: str) -> bool:
    """済んだ（か戻した）記録を「無ければ置く」で置く。段取りのファイルには触らない（退かす瞬間を作らない）。
    ほかの者が先に済ませて（か戻して）いれば、何も書かずに False。記録を置いた者だけが控えの跡を足す。"""
    path = _end_path(root, plan)
    makedirs(root, os.path.dirname(path))
    tmp = os.path.join(_tmpdir(root), f"ended-{plan.get('gen', 0)}.{os.getpid()}.new")
    _write_json(root, tmp, {**plan, "state": state})
    try:
        os.link(tmp, path)
    except FileExistsError:
        os.unlink(tmp)
        return False
    os.unlink(tmp)
    _bump(root)
    _settle(root, plan)
    return True


def _settle(root: str, plan: dict) -> None:
    """済んだ記録に合わせて控えの跡（`log.jsonl`）を足し、足した印を置く。済んだ記録のあとで落ちても、次に
    段取りを読んだ者（再開・次の入れ替え）が足す（同じ時に 2 人が足せば、跡は 2 行になりうる）。"""
    end = _end_path(root, plan)
    mark = end[:-len(".json")] + ".logged"
    if not os.path.exists(end) or os.path.exists(mark):
        return
    record = _read_json(root, end, "batch")
    if record.get("state") == "done":
        done = record.get("done", {})
        log(root, {"at": done.get("at"), "by": done.get("by"), "what": record.get("what"), "fp": done.get("fp"),
                   "snapshot": done.get("snapshot"), "files": done.get("files"), "batch": record.get("id")})
    else:
        back = record.get("rolled_back", {})
        log(root, {"at": back.get("at"), "by": back.get("by"), "what": "rollback", "batch": record.get("id")})
    _atomic_write(root, mark, b"")


def _from_to(e: dict, back: bool):
    old = (e["old"], bool(e.get("old_x", False))) if e["old"] is not None else None
    new = (e["new"], bool(e.get("new_x", False))) if e["new"] is not None else None
    return (new, old) if back else (old, new)


def apply(root: str, *, by: str, at: str) -> dict:
    """残っている段取りを最後まで進める（同じ命令の打ち直しと `resume --continue` が呼ぶ。別の者でよい）。
    置き換え済みの道は飛ばす。前でも後でもない中身の道があれば（ほかの者が書いた）、止めて断る。"""
    last = _pending_file(root)
    if last is not None:
        _settle(root, last)
    plan = pending(root)
    if plan is None:
        return {"applied": 0}
    applied = 0
    for index, e in enumerate(plan["entries"]):
        if not _mine(root, plan):
            return {"applied": applied, "batch": plan["id"], "files": len(plan["entries"]), "by_other": True}
        frm, to = _from_to(e, back=False)
        applied += _step(root, plan, index, frm, to)
    if not _mine(root, plan):
        return {"applied": applied, "batch": plan["id"], "files": len(plan["entries"]), "by_other": True}
    entries = view_after(root)
    # 控えの中身と目録だけを先に置き（名で決まるので、ほかの者と同じ物になる）、跡は済んだ記録を置けた者だけが
    # 足す（ほかの者が先に済ませ、次の束が始まったあとの混ざった姿を跡に残さない）。
    kept = save_snapshot(root, entries, what=plan["what"], by=by, at=at, write_log=False)
    plan["done"] = {"by": by, "at": at, "files": len(entries), **kept}
    if not _finish(root, plan, "done"):
        return {"applied": applied, **kept, "batch": plan["id"], "files": len(plan["entries"]), "by_other": True}
    return {"applied": applied, **kept, "batch": plan["id"], "files": len(plan["entries"])}


def rollback(root: str, *, by: str, at: str) -> dict:
    """残っている段取りを戻す（置き換え済みの道を前の中身に）。前でも後でもない道があれば止めて断る。"""
    last = _pending_file(root)
    if last is not None:
        _settle(root, last)
    plan = pending(root)
    if plan is None:
        return {"restored": 0}
    restored = 0
    for index in reversed(range(len(plan["entries"]))):
        if not _mine(root, plan):
            return {"restored": restored, "batch": plan["id"], "files": len(plan["entries"]), "by_other": True,
                    "fp": catalog_fp(view_after(root))}
        frm, to = _from_to(plan["entries"][index], back=True)
        restored += _step(root, plan, index, frm, to)
    plan["rolled_back"] = {"by": by, "at": at}
    if not _finish(root, plan, "rolled_back"):
        return {"restored": restored, "batch": plan["id"], "files": len(plan["entries"]), "by_other": True,
                "fp": catalog_fp(view_after(root))}
    return {"restored": restored, "batch": plan["id"], "files": len(plan["entries"]),
            "fp": catalog_fp(view_after(root))}


def view_after(root: str) -> list:
    """台帳のファイルそのものの目録（段取りを見ない）。入れ替えが済んだあとの姿。"""
    guard(root, thth_dir(root))
    return dir_catalog(root)


def run(root: str, desired: dict, *, what: str, by: str, at: str, executable: dict | None = None) -> dict:
    """求めた入れ替えを、まとまりとして行う。残っている段取りがこの求めの続きなら続ける（打ち直し）。
    別の求めの段取りが残っていれば断る（続けるか戻すかを先に決める）。"""
    check_thth(root)
    plan = pending(root)
    if plan is not None:
        if not matches_pending(root, plan, desired, executable):
            refuse_if_pending(root)
        result = apply(root, by=by, at=at)
        return {**result, "continued": True}
    plan = prepare(root, desired, what=what, by=by, at=at, executable=executable)
    if plan is None:
        return {"applied": 0, "files": 0, "fp": None, "snapshot": None, "batch": None, "continued": False}
    begin(root, plan)
    result = apply(root, by=by, at=at)
    return {**result, "continued": False}
