"""台帳の器 段 5「跡」（設計 §5.5・指示 `docs/指示_台帳の器_段5_跡と消す求め_2026-11-14.md` §2）。

- **git の commit どうしの差**（§2.1）: 台帳のフォルダが git の作業木のとき、差の両端に commit（`HEAD`・枝の名・
  `<commit>^` も）を受ける。宣言の置き場に入るファイルだけを、git の読むだけの命令（`rev-parse`・`ls-tree`・
  `cat-file`）で取り出し、repo の外の一時の置き場に並べて器として読む。**作業木も index も変えない。**
  git の作業木でない台帳・その commit に宣言の置き場が 1 つも無い・commit が無いときは「取り出せない」
  （`register_kept_missing`。控えの名で打ち直す道を添える）。commit に無い種類は「その時点に無い」として出す。
- **git を呼ぶのはこの部品だけ**（`tests/test_register_base.py` の `SUBPROCESS_ALLOWED`）。決まった場所の git だけを
  使い、PATH もシェルも使わない（`seal.py` の openssl と同じ決め方）。環境は空から作る（系と利用者の設定・
  置き換えの物・任意の錠を読まない）。commit の名は字の形を先に見て、`-` で始まる物・空白・`;`・`:`・`..` は
  git に渡さずに断る（`register_args_invalid`）。
- **判断の記録は足すだけ**（§2.2）: 前の時点の `.thth/judgments.jsonl` の続きが今か（`judgments_continue`）。
  行の位置で比べ、食い違った行は、消す求めの結果（`erase_excuses`）に載った印の行で、骨（`judgment.line_skeleton`。
  CI の続きの比べ `route.erase_continued` と同じ骨）が前の行と同じときだけ通す。控えの名で比べるときは、控えの
  目録の跡の注に置いた判断の記録の中身を読む（消す求めが除いたら「取り出せない」）。
- **取り込みの跡**（§2.3）: 取り込み・束の取り込み・控えのたびに、控えの目録に跡を足す（`note`。集めた者・
  検めた者・束の目録の指紋・その時の判断の記録の中身の名）。`links` と `diff` は、その版がどの取り込みで入ったかを
  出す（`entered`・`intakes_between`）。
- **判断から、版と元の記録へ**（§2.4）: `judgment_view`。元の記録は元のまま置き、器は索引だけを持つ。
  対象の版を示していない検収の記録（日付と番号だけ）は判断に数えず、`unversioned_view` で元の表と行を引けるように
  し、今の版に効くかは「言えない」と出す（無い物を「効く」と言わない）。
- 番号が消えたら赤。消す求めの結果に載った番号（`erase_excuses` の `records`）は赤にしない（`vanished`）。

網に出ない。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile

from . import judgment as J
from . import keep as K
from . import vessel as V
from .errors import RegisterRefused

GIT_BINARIES = ("/usr/bin/git", "/opt/homebrew/bin/git", "/usr/local/bin/git")
_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1", "GIT_LITERAL_PATHSPECS": "1"}
TIMEOUT = 120
# commit の名: 英数字か `_` で始まり、英数字・`_`・`.`・`/`・`-` と、後ろに `^`・`~`（数つき）だけ。
REV = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./\-]{0,199}(?:[\^~][0-9]{0,4})*\Z")
SNAPSHOT_WORDS = ("first", "last", "now")
INTAKES = ("adopt", "readopt", "merge")


# ------------------------------------------------------------------ git（読むだけ）

def git_binary() -> str:
    for binary in GIT_BINARIES:
        if os.path.isfile(binary) and os.access(binary, os.X_OK):
            return binary
    raise RegisterRefused("register_kept_missing", "git_missing")


def _git(cwd: str, arguments: list, *, data: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run([git_binary(), "-c", "core.fsmonitor=false", "-C", cwd, *arguments], input=data,
                              capture_output=True, timeout=TIMEOUT, env=_ENV)
    except (OSError, subprocess.SubprocessError):
        raise RegisterRefused("register_kept_missing", "git_failed") from None


def check_rev(name) -> str:
    """commit の名の字の形（git の引数として読まれうる物を、git に渡す前に断る）。"""
    if not isinstance(name, str) or not REV.fullmatch(name) or ".." in name or "//" in name \
            or name.endswith((".", "/", ".lock")):
        raise RegisterRefused("register_args_invalid", "commit")
    return name


def repo_of(root: str) -> tuple:
    """`(git の作業木の根, 台帳のフォルダの根からの道の頭)`。git の作業木でなければ「取り出せない」。"""
    if not os.path.isdir(root):
        raise RegisterRefused("register_kept_missing", "not_git")
    result = _git(root, ["rev-parse", "--show-toplevel", "--show-prefix"])
    if result.returncode != 0:
        raise RegisterRefused("register_kept_missing", "not_git")
    lines = result.stdout.decode("utf-8", errors="strict").split("\n")
    if len(lines) < 2 or not lines[0]:
        raise RegisterRefused("register_kept_missing", "not_git")
    return lines[0], lines[1]


def is_git(root: str) -> bool:
    try:
        repo_of(root)
    except RegisterRefused:
        return False
    return True


def resolve(top: str, name: str) -> str:
    check_rev(name)
    result = _git(top, ["rev-parse", "--verify", "--quiet", "--end-of-options", name + "^{commit}"])
    sha = result.stdout.decode("ascii", errors="replace").strip()
    if result.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        raise RegisterRefused("register_kept_missing", "unknown_commit")
    return sha


def _tree(top: str, sha: str) -> list:
    """commit の木の全部の枚 `(道, 中身の名, 実行の印)`（symlink と submodule は除く）。"""
    result = _git(top, ["ls-tree", "-r", "-z", "--full-tree", sha])
    if result.returncode != 0:
        raise RegisterRefused("register_kept_missing", "git_failed")
    out = []
    for item in result.stdout.split(b"\0"):
        if not item:
            continue
        meta, _tab, raw = item.partition(b"\t")
        parts = meta.split(b" ")
        if len(parts) != 3 or parts[1] != b"blob" or parts[0] not in (b"100644", b"100755"):
            continue
        try:
            path = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        out.append((path, parts[2].decode("ascii"), parts[0] == b"100755"))
    return out


def _blobs(top: str, names: list) -> dict:
    """中身の名 → バイト（`cat-file --batch` を 1 回）。"""
    names = sorted(set(names))
    if not names:
        return {}
    result = _git(top, ["cat-file", "--batch"], data=("\n".join(names) + "\n").encode("ascii"))
    if result.returncode != 0:
        raise RegisterRefused("register_kept_missing", "git_failed")
    out, data, pos = {}, result.stdout, 0
    for name in names:
        end = data.find(b"\n", pos)
        header = data[pos:end].split(b" ") if end >= 0 else []
        if len(header) != 3 or header[1] != b"blob" or header[0].decode("ascii", errors="replace") != name:
            raise RegisterRefused("register_kept_missing", "git_failed")
        size = int(header[2])
        out[name] = data[end + 1:end + 1 + size]
        pos = end + 1 + size + 1
    return out


def selector(kinds: dict):
    def select(rel: str) -> bool:
        return any(V._matches(kind, rel) for kind in kinds.values())
    return select


def extract(root: str, name: str, into: str, kinds: dict) -> dict:
    """commit `name` の、宣言の置き場に入る枚を `into` に書く（作業木は読まない・変えない）。
    `{"commit": 名, "files": 目録, "absent_kinds": [その時点に無い種類]}`。1 枚も無ければ「取り出せない」。"""
    top, prefix = repo_of(root)
    sha = resolve(top, name)
    select = selector(kinds)
    wanted = []
    for path, blob, executable in _tree(top, sha):
        if prefix and not path.startswith(prefix):
            continue
        rel = path[len(prefix):]
        if rel.split("/")[0] in (K.THTH, ".git") or not select(rel):
            continue
        wanted.append((K.safe_rel(rel), blob, executable))
    if not wanted:
        raise RegisterRefused("register_kept_missing", "no_places", commit=sha[:12])
    blobs = _blobs(top, [b for _r, b, _x in wanted])
    files = []
    for rel, blob, executable in wanted:
        data = blobs[blob]
        K.put_file(into, rel, data, executable)
        files.append({"path": rel, "size": len(data), "sha256": K.sha256(data), "x": executable})
    absent = sorted(k.name for k in kinds.values() if not any(V._matches(k, e["path"]) for e in files))
    return {"commit": sha, "files": sorted(files, key=lambda e: e["path"]), "absent_kinds": absent}


def file_at(root: str, name: str, rel: str) -> bytes | None:
    """commit `name` の、台帳の中の道 `rel`（器の置き場の `.thth/…` も）の 1 枚。その時点に無ければ None。"""
    top, prefix = repo_of(root)
    sha = resolve(top, name)
    if not rel or rel.startswith("/") or any(p in ("", ".", "..") for p in rel.split("/")):
        raise RegisterRefused("register_args_invalid", "path")
    path = prefix + rel
    hits = [b for p, b, _x in _tree_at(top, sha, path) if p == path]
    if not hits:
        return None
    return _blobs(top, hits)[hits[0]]


def _tree_at(top: str, sha: str, path: str) -> list:
    result = _git(top, ["ls-tree", "-z", "--full-tree", sha, "--", path])
    if result.returncode != 0:
        raise RegisterRefused("register_kept_missing", "git_failed")
    out = []
    for item in result.stdout.split(b"\0"):
        meta, _tab, raw = item.partition(b"\t")
        parts = meta.split(b" ")
        if len(parts) == 3 and parts[1] == b"blob":
            out.append((raw.decode("utf-8", errors="replace"), parts[2].decode("ascii"), parts[0] == b"100755"))
    return out


# ------------------------------------------------------------------ 差の端（控えか commit か）

def snapshot_of(root: str, name: str):
    """控えの名として読めれば控えた目録。読めなければ None（`first`・`last` は控えが無ければ断る）。"""
    if name in ("first", "last"):
        return K.load_snapshot(root, name)
    if len(name) < 8 or any(c not in "0123456789abcdef" for c in name):
        return None
    try:
        return K.load_snapshot(root, name)
    except RegisterRefused as exc:
        if exc.code == "register_kept_missing" and exc.detail == "snapshot":
            return None
        raise


def side_kind(root: str, name: str) -> str:
    """`now`・`snapshot`・`commit`。控えの名として読めない名は、台帳が git の作業木なら commit と読む。
    git の作業木でなければ、控えの名の断り（今までどおり）か「取り出せない」。"""
    if name == "now":
        return "now"
    if snapshot_of(root, name) is not None:
        return "snapshot"
    if not is_git(root):
        if len(name) >= 8 and all(c in "0123456789abcdef" for c in name):
            raise RegisterRefused("register_kept_missing", "snapshot", asked=name[:16])
        check_rev(name)
        raise RegisterRefused("register_kept_missing", "not_git")
    check_rev(name)
    return "commit"


# ------------------------------------------------------------------ 消す求めの結果（通す物）

def erase_excuses(root: str) -> dict:
    """台帳の消す求めの結果の記録（`.thth/erase/<求め>/result.json`）から、通す物を集める:
    `requests`（結果のある求め）・`lines`（書き換えた判断の行の番号）・`records`（今の本文から除いた記録の番号）。
    結果に載っていない消え・書き換えは、今までどおり赤。"""
    out = {"requests": set(), "lines": set(), "records": set()}
    base = os.path.join(K.thth_dir(root), J.ERASE_DIR)
    if os.path.islink(base) or not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        if not J.ERASE_ID.fullmatch(name):
            continue
        path = os.path.join(base, name, J.ERASE_RESULT)
        if os.path.islink(path) or not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as f:
                result = json.loads(f.read().decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(result, dict) or result.get("request") != name:
            continue
        out["requests"].add(name)
        out["lines"].update(x for x in result.get("rewritten_lines") or [] if isinstance(x, str))
        out["records"].update(x for x in result.get("erased_records") or [] if isinstance(x, str))
    return out


# ------------------------------------------------------------------ 番号が消えたら赤

def vanished(records: list, units: list, *, excused=frozenset()) -> dict:
    """消えた記録の番号と内側の番号のうち、赤にする物と通す物。`excused` は消す求めの結果に載った記録の番号
    （`種類:番号`。`erase_excuses(root)["records"]`）。載った記録の内側の番号も通す。"""
    excused = frozenset(excused or ())
    return {"records": [r for r in records if r not in excused],
            "units": [u for u in units if u["record"] not in excused],
            "excused": sorted({r for r in records if r in excused} | {u["record"] for u in units
                                                                       if u["record"] in excused})}


# ------------------------------------------------------------------ 判断の記録は足すだけ

def _lines(data: bytes) -> list:
    return [line for line in data.decode("utf-8", errors="replace").splitlines() if line.strip()]


def _item(line: str) -> dict:
    try:
        item = json.loads(line)
    except (ValueError, RecursionError):
        return {}
    return item if isinstance(item, dict) else {}


def _only_renumbered(before: dict, after: dict) -> bool:
    """印でない行が、消す求めの結びの付け直し（`carries` と、そこから作り直した番号）だけで変わったか。"""
    drop = ("id", "carries")
    return bool(before) and {k: v for k, v in before.items() if k not in drop} == \
        {k: v for k, v in after.items() if k not in drop} and before.get("carries") != after.get("carries")


def judgments_continue(prev: bytes, now: bytes, erase: dict | None = None) -> dict:
    """前の時点の判断の記録 `prev` の続きが今の `now` か（足しただけか）。行の位置で比べる（空の行は数えない）。
    `{"continues": bool, "line": 最初に食い違った前の行の番号 or None, "why": 語 or None, "excused": [通した行]}`。
    `erase`（`erase_excuses` の形）があれば、消す求めの結果に載った印の行は、骨（`judgment.line_skeleton`。
    CI の続きの比べと同じ）が前の行と同じなら通す。印でない行は、結びの付け直しだけの変化を通す。"""
    if now.startswith(prev):
        return {"continues": True, "line": None, "why": None, "excused": []}
    before, after = _lines(prev), _lines(now)
    lines = set((erase or {}).get("lines") or ())
    requests = set((erase or {}).get("requests") or ())
    passed = []
    for number, line in enumerate(before, start=1):
        if number > len(after):
            return {"continues": False, "line": number, "why": "shortened", "excused": passed}
        other = after[number - 1]
        if line == other:
            continue
        a, b = _item(line), _item(other)
        if erase and (J.is_erased(b) or _only_renumbered(a, b)) \
                and J.line_continues(J.skeleton_pair(a), b, lines=lines, requests=requests) is None:
            passed.append(number)
            continue
        return {"continues": False, "line": number, "why": "rewritten", "excused": passed}
    return {"continues": True, "line": None, "why": None, "excused": passed}


def _judgments_bytes(root: str) -> bytes:
    path = J.path_of(root)
    if not os.path.exists(path):
        return b""
    if os.path.islink(path):
        raise RegisterRefused("register_judgment_invalid", "symlink")
    with open(path, "rb") as f:
        return f.read(J.MAX_BYTES + 1)


def previous_judgments(root: str, name: str) -> dict:
    """前の時点の判断の記録。`{"from": "snapshot"|"commit", "name": …, "bytes": …}`。"""
    kind = side_kind(root, name)
    if kind == "now":
        raise RegisterRefused("register_args_invalid", "against")
    if kind == "snapshot":
        snap = snapshot_of(root, name)
        notes = [n for n in snap.get("trail", []) if isinstance(n, dict)
                 and ("judgments" in n or "judgments_erased" in n)]
        if not notes:
            # 控えの目録に判断の記録の跡が無い（段 5 より前の控え）。commit の名で打ち直す。
            raise RegisterRefused("register_kept_missing", "no_judgments_kept", snapshot=snap["id"][:16])
        if notes[-1].get("judgments_erased"):
            # 消す求めが、その時点の判断の記録の控えを除いた（範囲の行を含んでいた）。取り出せない。
            raise RegisterRefused("register_kept_missing", "judgments_erased")
        digest = notes[-1]["judgments"]
        return {"from": "snapshot", "name": snap["id"], "bytes": b"" if digest is None else K.get_object(root, digest)}
    top, _prefix = repo_of(root)
    data = file_at(root, name, f"{K.THTH}/{J.FILE}")
    return {"from": "commit", "name": resolve(top, name), "bytes": data or b""}


def against(root: str, name: str) -> dict:
    """`check --against <前>`: 判断の記録が前の時点の続きか（消す求めの結果に載った書き換えは通す）。
    続きでなければ呼ぶ側が赤にする。"""
    prev = previous_judgments(root, name)
    now = _judgments_bytes(root)
    result = judgments_continue(prev["bytes"], now, erase_excuses(root))
    return {"against": prev["name"], "from": prev["from"], **result,
            "lines_before": len(_lines(prev["bytes"])), "lines_now": len(_lines(now))}


# ------------------------------------------------------------------ 取り込みの跡

def note(root: str, snapshot: str | None, *, what: str, by: str, at: str, checked_by: str | None = None,
         bundle_fp: str | None = None) -> dict | None:
    """控えの目録に跡を足す（取り込み・束の取り込み・控え）。判断の記録の今の中身も控えに置き、その名を残す
    （`check --against <控えの名>` が前の時点の判断の記録を読めるように）。"""
    if not snapshot:
        return None
    data = _judgments_bytes(root)
    item = {"what": what, "at": at, "by": by, "checked_by": checked_by or None, "bundle_fp": bundle_fp,
            "judgments": K.put_object(root, data) if data else None}
    K.note_trail(root, snapshot, item)
    return item


def _intake_view(entry: dict, snap: dict | None) -> dict:
    notes = [n for n in (snap or {}).get("trail", []) if isinstance(n, dict) and n.get("at") == entry.get("at")
             and n.get("what") == entry.get("what")]
    found = notes[-1] if notes else None
    checked_by = (found or {}).get("checked_by")
    return {"snapshot": entry.get("snapshot"), "what": entry.get("what"), "at": entry.get("at"),
            "by": entry.get("by"), "checked_by": checked_by, "checked": bool(checked_by),
            "bundle_fp": (found or {}).get("bundle_fp"), "noted": found is not None}


def _history(root: str) -> list:
    return [h for h in K.history(root) if h.get("snapshot")]


def _load(root: str, sid: str):
    try:
        return K.load_snapshot(root, sid)
    except RegisterRefused:
        return None


def intakes_between(root: str, a_sid: str | None, b_sid: str | None) -> list:
    """跡の順で、`a_sid` の控えのあとから `b_sid` の控えまでの取り込み（`b_sid` が None なら今まで）。"""
    hist = _history(root)
    start = max((i for i, h in enumerate(hist) if h["snapshot"] == a_sid), default=-1) if a_sid else -1
    end = max((i for i, h in enumerate(hist) if h["snapshot"] == b_sid), default=None) if b_sid else len(hist) - 1
    if end is None:
        return []
    out = []
    for h in hist[start + 1:end + 1]:
        if h.get("what") in INTAKES:
            out.append(_intake_view(h, _load(root, h["snapshot"])))
    return out


def _version_in(root: str, snap: dict, rec: V.Record) -> str | None:
    """控えた時点の、その記録の版（記録のある 1 枚と宣言だけを一時の置き場に書いて読む）。"""
    entry = next((e for e in snap["files"] if e["path"] == rec.path), None)
    if entry is None or not entry.get("sha256"):
        return None
    with tempfile.TemporaryDirectory(prefix="thth-trail-") as tmp:
        try:
            K.put_file(tmp, rec.path, K.get_object(root, entry["sha256"]), False)
            for e in snap.get("decl", []):
                K.put_file(tmp, f"{K.THTH}/{e['path']}", K.get_object(root, e["sha256"]), False)
            vessel = V.open_vessel(tmp)
        except RegisterRefused:
            return None
        same = [r for r in vessel.all_records if r.ref == rec.ref]
        return same[0].version if len(same) == 1 else None


def entered(root: str, rec: V.Record) -> dict:
    """今の版の記録が、どの控え（取り込み）で入ったか。跡を後ろからたどり、今と同じ版が続くいちばん前の控え。
    最後の控えの版が今と違えば、控えのあとに直に書かれた版（`kept: False`）。"""
    hist = _history(root)
    now_sha = K.sha256(K.read_file(root, rec.path)) if os.path.isfile(K.full(root, rec.path)) else None
    found, same_at = None, {}
    for h in reversed(hist):
        sid = h["snapshot"]
        if sid not in same_at:
            snap = _load(root, sid)
            entry = None if snap is None else next((e for e in snap["files"] if e["path"] == rec.path), None)
            same = snap is not None and entry is not None and (
                entry.get("sha256") == now_sha or _version_in(root, snap, rec) == rec.version)
            same_at[sid] = (same, snap)
        same, snap = same_at[sid]
        if not same:
            break
        found = (h, snap)
    if found is None:
        return {"kept": False}
    h, snap = found
    return {"kept": True, **_intake_view(h, snap)}


# ------------------------------------------------------------------ 判断から、版と元の記録へ

def _depend_fields(vessel: V.Vessel, j: dict):
    try:
        kind = vessel.kinds[vessel.get(j["target"]["record"]).kind]
    except RegisterRefused:
        return None
    q = V.question_of(kind, j["question"])
    return q.depend_fields() if q else None


def shows_version(vessel: V.Vessel, j: dict) -> bool:
    """判断が対象の版を示しているか: 見た版の指紋か、問いの依る欄の全部を「確かめた中身」として持つか。
    日付と番号だけの検収の記録（キョンシーの原文確認の記録の形）は示していない（効くとも古いとも言えない）。"""
    if "content" not in j:
        return bool((j.get("seen") or {}).get("version") or (j.get("seen") or {}).get("depend"))
    fields = _depend_fields(vessel, j)
    content = j.get("content") or {}
    return bool(fields) and all(k in content for k in fields)


def unversioned_view(vessel: V.Vessel, j: dict) -> dict:
    """版を示していない検収の記録: 判断には数えず、元の表と行を引けるようにする。効くかは「言えない」。"""
    source = j.get("source") if isinstance(j.get("source"), str) else ""
    src = vessel.unique(source) if ":" in source else None
    return {"question": j["question"], "record": (j.get("target") or {}).get("record"),
            "table": source.split(":", 1)[0] if source else None,
            "row": source.split(":", 1)[1] if ":" in source else None,
            "file": src.path if src is not None else None, "content": sorted((j.get("content") or {})),
            "fields_needed": _depend_fields(vessel, j), "state": "cannot_say", "why": "version_not_shown"}


def judgment_view(vessel: V.Vessel, j: dict) -> dict:
    """判断 1 件の、対象の版・依る所の指紋・今の版に効くか・元の記録。"""
    seen = j.get("seen") or {}
    depend = seen.get("depend")
    if "content" in j:
        try:
            kind = vessel.kinds[vessel.get(j["target"]["record"]).kind]
            q = V.question_of(kind, j["question"])
            fields = q.depend_fields() if q else None
        except RegisterRefused:
            fields = None
        content = j.get("content") or {}
        if fields and all(k in content for k in fields):
            depend = V.fp({k: content.get(k) for k in fields})
    state = J.evaluate(vessel, j)
    origin = {"by": j.get("by"), "at": j.get("at"), "path": j.get("path")}
    source = j.get("source")
    if isinstance(source, str) and ":" in source:
        src = vessel.unique(source)
        origin = {"table": source.split(":", 1)[0], "row": source.split(":", 1)[1],
                  "file": src.path if src is not None else None, "by": j.get("by")}
    target = j.get("target") or {}
    return {"id": j["id"], "question": j["question"], "unit": target.get("unit"), "value": target.get("value"),
            "version": seen.get("version"), "depend": depend, "content": "content" in j, "state": state,
            "origin": origin, "by": j.get("by"), "path": j.get("path")}
