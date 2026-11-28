#!/usr/bin/env python3
"""括りの段 5: main の置き場を共通の根から `ws/main/` へ移す・戻す台本。

  python3 tools/ws_move_main.py check  --root <根>            # 確かめだけ（何も書かない）
  python3 tools/ws_move_main.py move   --root <根> [--dry-run] # 移す（途中で落ちたら、同じ命令でやり直す）
  python3 tools/ws_move_main.py back   --root <根> [--dry-run] # 戻す（移す途中でも、移したあとでも）
  python3 tools/ws_move_main.py status --root <根>            # どこまで進んだか

設計: docs/設計_THTHアカウント（括り）_叩き台_2026-11-26.md §3 の段 5・§8 R2・R11。
手順書: docs/手順_括りの段5_mainを移す夜_2026-11-27.md。

決まり:
- `--root` は必ず名指しする（既定で本番を指さない）。
- 消さない。退ける物は `ws/.main-move/` の下へ名前を替えて置く（`rm` を使わない決まり）。
- 印 `ws/.main-move/MOVING` がある間、新しい版は main を `workspace_moving` で断る（thth/accounts.py）。
- 中身は `ws/.main-staging/` に集め、最後に 1 回の rename で `ws/main` にする（半分の `ws/main/` を見せない）。
- 移した物の元の場所には、開けないフォルダ（mode 000・空）を置く。古い版（`ws/main/` を知らない版）は
  台帳の置き場が読めずに止まる（R2: 黙って空の置き場を作って動き出さない）。
- 台帳の中で置き場を指す道は、`$THTH_ROOT` の意味が「括りの根」に替わるのに合わせて直す。直した所は
  帳面（`ws/.main-move/journal.json`）に残し、戻すときに 1 つずつ元に戻す。
- 記録（移した `state/<口座>/` などの JSON・NDJSON）の中の絶対の道 `"<根>/<移した物>…` も
  `"<根>/ws/main/<移した物>…` に書き替え、戻すときは逆にする（承認と止めの記録は repo を絶対の道で持ち、
  原稿の repo と照らす。直さないと移したあと承認も止めも無いものとされる・VM の写しで 1096→1・11/29）。
  帳面に頼らず道の頭で書き替えるので、打ち直しても二重にならず、移していた間に書かれた行も戻る。

依存は標準の部品だけ（VM の古い版の上でも、この 1 本を写せば回る）。thth は import しない。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import stat
import subprocess
import sys

SCHEMA = "thth.ws-move-main/1"
MAIN = "main"
MOVE_DIR = ".main-move"          # thth/accounts.py の MAIN_MOVE_DIR と同じ綴り
MOVING = "MOVING"                # thth/accounts.py の MAIN_MOVING_MARK と同じ綴り
STAGING = ".main-staging"
JOURNAL = "journal.json"

# 共通の根に残す物（括りの設計 §2.1 と、コードが common_root() で引く物・tests/test_ws_roots.py の表）。
TOP_COMMON = frozenset({"app", "venv", "private", "ws", "state", "workspaces.json", "notifications.json"})
# 括りの物（コードが ws_root() で引く物）。
TOP_WS = frozenset({
    "accounts", "adaccounts", "adaccount_state", "principals", "principal_state", "owner_state",
    "registers", "register_state", "register_runs", "register_leave", "stores", "store_state",
    "waba", "waba_state", "repos", "origins", "secrets", "logs",
})
# state/ の中で共通の根に残す物。ほかの名（口座ごとの state/<口座>/・_plaza・_leave・_reports など）は括りへ。
STATE_COMMON = frozenset({"_admin", "_webhook", "_meta", "_media", "_app.lock", "worker.json", "approval-worker.json"})
STATE_WS_UNDERSCORE = frozenset({"_plaza", "_plaza_reads", "_map", "_invites", "_leave", "_auth", "_repos", "_reports"})
# private/ の中で括りへ移す物（レーンの鍵の表は括りごと・thth/lane_keys.py）。
PRIVATE_WS = frozenset({"lane-keys.json"})
# 道を書いた台帳の置き場（JSON）。
LEDGER_DIRS = ("accounts", "adaccounts", "principals", "registers", "stores", "waba")
FREE_MIN = 64 * 1024 * 1024
UNIT_BUSY = frozenset({"active", "activating", "reloading", "deactivating", "refreshing"})


class Stop(Exception):
    """止まって知らせる（何を・どう直すか）。"""


class Halt(Exception):
    """`--stop-after` で決めた所で止まる（通し稽古の「途中で落ちた」）。"""


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def say(line: str) -> None:
    print(line, flush=True)


# --------------------------------------------------------------------------- 置き場の名

class Layout:
    def __init__(self, root: str):
        if not root or not os.path.isabs(root):
            raise Stop("--root は絶対の道で名指ししてください（既定はありません）")
        self.root = os.path.normpath(root)
        if self.root == os.sep or not os.path.isdir(self.root) or os.path.islink(self.root):
            raise Stop(f"根がフォルダではありません: {self.root}")
        self.ws = os.path.join(self.root, "ws")
        self.main = os.path.join(self.ws, MAIN)
        self.staging = os.path.join(self.ws, STAGING)
        self.move_dir = os.path.join(self.ws, MOVE_DIR)
        self.marker = os.path.join(self.move_dir, MOVING)
        self.journal = os.path.join(self.move_dir, JOURNAL)

    def at_root(self, rel: str) -> str:
        return os.path.join(self.root, rel)

    def at_staging(self, rel: str) -> str:
        return os.path.join(self.staging, rel)


def is_tombstone(path: str) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0


def exists(path: str) -> bool:
    return os.path.lexists(path)


# --------------------------------------------------------------------------- 帳面

def read_journal(lay: Layout) -> dict | None:
    try:
        with open(lay.journal, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise Stop(f"帳面の形が違います: {lay.journal}")
    if os.path.normpath(data.get("root", "")) != lay.root:
        raise Stop(f"帳面は別の根の物です（{data.get('root')}）")
    return data


def write_journal(lay: Layout, data: dict) -> None:
    tmp = lay.journal + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, lay.journal)


def note(lay: Layout, data: dict, step: str, **extra) -> None:
    data.setdefault("history", []).append({"at": now(), "step": step, **extra})
    write_journal(lay, data)


# --------------------------------------------------------------------------- 振り分け

def classify(lay: Layout, keep: set, move: set) -> tuple[list, list]:
    """根の今の形から、移す物の並び（plan）と、分からない物（unknown）を返す。"""
    plan, unknown = [], []
    for name in sorted(os.listdir(lay.root)):
        full = lay.at_root(name)
        if name in TOP_COMMON or name in keep:
            continue
        if is_tombstone(full):
            unknown.append(f"{name}（開けないフォルダ。前に移した跡か）")
            continue
        if name in TOP_WS or name in move:
            if os.path.islink(full):
                unknown.append(f"{name}（symlink）")
                continue
            plan.append({"src": name, "dst": name, "tomb": True})
        else:
            unknown.append(name)
    state = lay.at_root("state")
    if os.path.isdir(state) and not os.path.islink(state):
        for name in sorted(os.listdir(state)):
            rel = f"state/{name}"
            full = os.path.join(state, name)
            if name in STATE_COMMON or rel in keep:
                continue
            if is_tombstone(full):
                unknown.append(f"{rel}（開けないフォルダ。前に移した跡か）")
                continue
            if os.path.islink(full):
                unknown.append(f"{rel}（symlink）")
                continue
            known = (name in STATE_WS_UNDERSCORE or rel in move
                     or (not name.startswith(("_", ".")) and re.fullmatch(r"[A-Za-z0-9_.-]+", name)))
            if known:
                plan.append({"src": rel, "dst": rel, "tomb": True})
            else:
                unknown.append(rel)
    for name in sorted(PRIVATE_WS):
        rel = f"private/{name}"
        full = lay.at_root(rel)
        if exists(full):
            if os.path.islink(full) or not os.path.isfile(full):
                unknown.append(f"{rel}（ふつうのファイルでない）")
            else:
                # 古い版はこの表を読まない。新しい版は移したあと ws/main/private/ だけを読む。印は置かない。
                plan.append({"src": rel, "dst": rel, "tomb": False})
    return plan, unknown


# --------------------------------------------------------------------------- 道の直し

def _json_strings(value, out):
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            _json_strings(v, out)
    elif isinstance(value, list):
        for v in value:
            _json_strings(v, out)


def _first(rest: str) -> str:
    return rest.split("/", 1)[0]


def _moved_rel(rel: str, moved: set) -> bool:
    """根からの相対の道が、移す物の中を指すか。"""
    parts = rel.split("/")
    if parts[0] in moved:
        return True
    if len(parts) >= 2 and f"{parts[0]}/{parts[1]}" in moved:
        return True
    return False


def ledger_files(base: str) -> list:
    out = []
    for d in LEDGER_DIRS:
        top = os.path.join(base, d)
        if not os.path.isdir(top) or os.path.islink(top):
            continue
        for dirpath, dirnames, filenames in os.walk(top):
            depth = os.path.relpath(dirpath, top).count(os.sep) + (0 if dirpath == top else 1)
            if depth >= 2:
                dirnames[:] = []
            dirnames[:] = [n for n in dirnames if not os.path.islink(os.path.join(dirpath, n))]
            for n in sorted(filenames):
                p = os.path.join(dirpath, n)
                if n.endswith(".json") and os.path.isfile(p) and not os.path.islink(p) and os.path.getsize(p) <= 4 * 1024 * 1024:
                    out.append(p)
    return sorted(out)


GIT_TOKEN = re.compile(r"(?:(?<=\s)|(?<==)|(?<=^)|(?<=\"))(/[^\s\"]+)", re.M)


def git_files(base: str) -> list:
    """repos/ の下の git の置き場で、絶対の道が書かれうるファイル。"""
    out = []
    top = os.path.join(base, "repos")
    if not os.path.isdir(top) or os.path.islink(top):
        return out
    for dirpath, dirnames, filenames in os.walk(top):
        rel = os.path.relpath(dirpath, top)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if ".git" in filenames and os.path.isfile(os.path.join(dirpath, ".git")):
            out.append(os.path.join(dirpath, ".git"))
        if os.path.basename(dirpath) == ".git":
            for n in ("config",):
                if os.path.isfile(os.path.join(dirpath, n)):
                    out.append(os.path.join(dirpath, n))
            wt = os.path.join(dirpath, "worktrees")
            if os.path.isdir(wt):
                for w in sorted(os.listdir(wt)):
                    g = os.path.join(wt, w, "gitdir")
                    if os.path.isfile(g):
                        out.append(g)
            dirnames[:] = []
            continue
        dirnames[:] = [n for n in dirnames if not os.path.islink(os.path.join(dirpath, n))
                       and (n == ".git" or depth < 3)]
    return sorted(set(out))


def _count_json_literal(text: str, value: str) -> tuple[str | None, int]:
    for lit in (json.dumps(value, ensure_ascii=False), json.dumps(value)):
        n = text.count(lit)
        if n:
            return lit, n
    return None, 0


def plan_rewrites(lay: Layout, plan: list) -> tuple[list, list]:
    """移す前の根で、直す道を全部数える。返り値は (rewrites, problems)。"""
    moved = {item["src"] for item in plan} | TOP_WS
    roots = {lay.root, os.path.realpath(lay.root)}
    rewrites, problems = [], []
    for path in ledger_files(lay.root):
        rel_file = os.path.relpath(path, lay.root)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        try:
            data = json.loads(text)
        except ValueError:
            problems.append(f"{rel_file}: JSON として読めません（直さずに移すと、あとで道を数えられません）")
            continue
        strings = []
        _json_strings(data, strings)
        seen = set()
        for v in strings:
            if v in seen:
                continue
            seen.add(v)
            new = None
            if v == "$THTH_ROOT" or v == "$THTH_ROOT/":
                problems.append(f"{rel_file}: 根そのもの（{v}）を指しています。括りの根か共通の根か決めて手で直してください")
                continue
            if v.startswith("$THTH_ROOT/"):
                rest = v[len("$THTH_ROOT/"):]
                if not _moved_rel(rest, moved) and _first(rest) in TOP_COMMON | {"state"}:
                    # 共通の物を $THTH_ROOT で指している。移したあとも同じ所を指すよう、絶対の道にする。
                    new = os.path.join(lay.root, rest)
            else:
                for r in roots:
                    if v.startswith(r + "/"):
                        rest = v[len(r) + 1:]
                        if _moved_rel(rest, moved):
                            new = "$THTH_ROOT/" + rest
                        break
                    if v == r:
                        problems.append(f"{rel_file}: 根そのもの（{v}）を指しています。手で直してください")
            if new is None:
                continue
            lit, n = _count_json_literal(text, v)
            if not lit:
                problems.append(f"{rel_file}: 道 {v} の書き方が読めません（エスケープなど）。手で直してください")
                continue
            new_lit = json.dumps(new, ensure_ascii=False) if lit == json.dumps(v, ensure_ascii=False) else json.dumps(new)
            rewrites.append({"file": rel_file, "kind": "json", "old": lit, "new": new_lit, "count": n})
    for path in git_files(lay.root):
        rel_file = os.path.relpath(path, lay.root)
        with open(path, encoding="utf-8", errors="surrogateescape") as f:
            text = f.read()
        seen = set()
        for m in GIT_TOKEN.finditer(text):
            v = m.group(1)
            if v in seen:
                continue
            seen.add(v)
            for r in roots:
                if v.startswith(r + "/") and _moved_rel(v[len(r) + 1:], moved):
                    new = os.path.join(lay.root, "ws", MAIN, v[len(r) + 1:])
                    rewrites.append({"file": rel_file, "kind": "git", "old": v, "new": new,
                                     "count": len(_git_spans(text, v))})
                    break
    return rewrites, problems


def _git_spans(text: str, token: str) -> list:
    pat = re.compile(r"(?:(?<=\s)|(?<==)|(?<=^)|(?<=\"))" + re.escape(token) + r"(?=\s|\"|$)", re.M)
    return [m.span() for m in pat.finditer(text)]


def _replace(text: str, kind: str, old: str, new: str) -> tuple[str, int]:
    if kind == "json":
        return text.replace(old, new), text.count(old)
    spans = _git_spans(text, old)
    out, last = [], 0
    for a, b in spans:
        out.append(text[last:a])
        out.append(new)
        last = b
    out.append(text[last:])
    return "".join(out), len(spans)


def _count(text: str, kind: str, lit: str) -> int:
    return text.count(lit) if kind == "json" else len(_git_spans(text, lit))


def apply_rewrite(path: str, rw: dict, *, forward: bool) -> str:
    """1 つの直しを当てる（済んでいれば何もしない）。返り値は 'done'・'already'。"""
    old, new = (rw["old"], rw["new"]) if forward else (rw["new"], rw["old"])
    with open(path, encoding="utf-8", errors="surrogateescape") as f:
        text = f.read()
    have_old, have_new = _count(text, rw["kind"], old), _count(text, rw["kind"], new)
    if have_old == rw["count"]:
        out, n = _replace(text, rw["kind"], old, new)
        if rw["kind"] == "json":
            json.loads(out)  # 直したあとも JSON であること
        info = os.stat(path)
        tmp = path + ".ws-move.tmp"
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(out)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, stat.S_IMODE(info.st_mode))
        os.replace(tmp, path)
        return "done"
    if have_old == 0 and have_new >= rw["count"]:
        return "already"
    raise Stop(f"{os.path.basename(path)}: 道の数が帳面と合いません（{old} が {have_old}・帳面は {rw['count']}）。"
               "移したあとで手で直したか。中を見て、帳面か台帳を合わせてから打ち直してください")


# --------------------------------------------------------------------------- 記録の道

# 記録を書き替えない上の段: repos（原稿と git。git の設定は道の直しが扱う）・logs（読み返さない）・台帳（道の直しが扱う）。
RECORD_SKIP = frozenset({"repos", "logs"}) | frozenset(LEDGER_DIRS)
RECORD_EXT = (".json", ".ndjson")


def _record_pattern(lay: Layout, srcs, *, forward: bool):
    """`<根>/<移した物>`（戻すときは `<根>/ws/main/<移した物>`）。文字列の頭でも、文の途中でもよい。
    前は道の切れ目（英数・`_.-/` でない）、後ろは `/`・`"`・空白・`\\`（JSON の逃がし）か終わり。"""
    roots = sorted({lay.root, os.path.realpath(lay.root)}, key=len, reverse=True)
    names = sorted(set(srcs) | TOP_WS, key=len, reverse=True)
    mid = "" if forward else f"/ws/{MAIN}"
    return re.compile(r"(?<![A-Za-z0-9_.\-/])(" + "|".join(re.escape(r) for r in roots) + ")" + re.escape(mid)
                      + "/(" + "|".join(re.escape(n) for n in names) + r')(?=[/"\s\\]|$)', re.M)


def _escaped_roots(lay: Layout) -> list:
    """JSON で `/` を `\\/` と逃がした根（書き替えられないので、残っていれば断る）。"""
    return [r.replace("/", "\\/") for r in {lay.root, os.path.realpath(lay.root)}]


def record_files(base: str, rels=None) -> list:
    """`base` の下の記録のファイル。`rels` が無ければ `base` の中の全部（上の段と state/・private/ の中）。"""
    if rels is None:
        rels = []
        if os.path.isdir(base):
            for name in sorted(os.listdir(base)):
                if name in ("state", "private"):
                    d = os.path.join(base, name)
                    rels += [f"{name}/{n}" for n in sorted(os.listdir(d))] if os.path.isdir(d) else []
                else:
                    rels.append(name)
    out = []
    for rel in rels:
        if rel.split("/")[0] in RECORD_SKIP:
            continue
        top = os.path.join(base, rel)
        if os.path.islink(top):
            continue
        if os.path.isfile(top):
            if top.endswith(RECORD_EXT):
                out.append(top)
            continue
        if not os.path.isdir(top) or is_tombstone(top):
            continue
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = sorted(n for n in dirnames if not os.path.islink(os.path.join(dirpath, n)))
            for n in sorted(filenames):
                p = os.path.join(dirpath, n)
                if n.endswith(RECORD_EXT) and os.path.isfile(p) and not os.path.islink(p):
                    out.append(p)
    return out


def _still_json(path: str, before: str, after: str) -> None:
    """書き替えたあとも、読めた所は読めること（読めなかった行はそのまま）。"""
    if path.endswith(".ndjson"):
        pairs = zip(before.split("\n"), after.split("\n"))
    else:
        pairs = [(before, after)]
    for b, a in pairs:
        if not b.strip():
            continue
        try:
            json.loads(b)
        except ValueError:
            continue
        json.loads(a)


def rewrite_records(lay: Layout, base: str, srcs, *, forward: bool, rels=None, dry: bool = False) -> tuple[int, int]:
    """記録の中の絶対の道を書き替える。返り値は（ファイルの数・か所の数）。"""
    pat = _record_pattern(lay, srcs, forward=forward)
    repl = (lambda m: f"{m.group(1)}/ws/{MAIN}/{m.group(2)}") if forward else (lambda m: f"{m.group(1)}/{m.group(2)}")
    files = hits = 0
    for path in record_files(base, rels):
        with open(path, encoding="utf-8", errors="surrogateescape") as f:
            text = f.read()
        out, n = pat.subn(repl, text)
        if not n:
            continue
        files, hits = files + 1, hits + n
        if dry:
            continue
        try:
            _still_json(path, text, out)
        except ValueError:
            raise Stop(f"{os.path.relpath(path, base)}: 記録の道を書き替えると JSON として読めなくなります。中を見てください") from None
        info = os.stat(path)
        tmp = path + ".ws-move.tmp"
        with open(tmp, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(out)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, stat.S_IMODE(info.st_mode))
        os.replace(tmp, path)
    return files, hits


def leftover_records(lay: Layout, base: str, srcs, rels) -> list:
    """移した記録に残っている「移す前の根の道」（ws/main を通らない道）。返り値は（ファイル・か所の数）の並び。"""
    pat = _record_pattern(lay, srcs, forward=True)
    escaped = _escaped_roots(lay)
    out = []
    for path in record_files(base, rels):
        with open(path, encoding="utf-8", errors="surrogateescape") as f:
            text = f.read()
        n = len(pat.findall(text)) + sum(text.count(e) for e in escaped)
        if n:
            out.append((os.path.relpath(path, base), n))
    return out


# --------------------------------------------------------------------------- 確かめ

def check_units(systemctl: str | None) -> list:
    if systemctl is None:
        return []
    exe = shutil.which(systemctl) if os.sep not in systemctl else systemctl
    if not exe:
        return [f"systemctl が見つかりません（{systemctl}）。VM の上で打つか、手元の稽古なら --no-units-check"]
    try:
        out = subprocess.run([exe, "list-units", "--all", "--plain", "--no-legend", "--no-pager", "thth*"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"systemctl を打てません（{exc}）"]
    if out.returncode != 0:
        return [f"systemctl list-units が rc {out.returncode}（{out.stderr.strip()[:200]}）"]
    busy = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].startswith("thth") and parts[2] in UNIT_BUSY:
            busy.append(f"{parts[0]} が {parts[2]}（止めてから。手順書の「止める」）")
    return busy


def _try_lock(path: str) -> bool:
    import fcntl
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def check_locks(state_dir: str) -> list:
    busy = []
    if not os.path.isdir(state_dir):
        return busy
    for name in sorted(os.listdir(state_dir)):
        d = os.path.join(state_dir, name)
        if is_tombstone(d) or not os.path.isdir(d) or os.path.islink(d):
            continue
        cand = [os.path.join(d, "lock")] if name != "_repos" else [
            os.path.join(d, n) for n in sorted(os.listdir(d)) if n.endswith(".lock")]
        for p in cand:
            if os.path.isfile(p) and not _try_lock(p):
                busy.append(f"state/{name}/{os.path.basename(p)} を誰かが握っています（打っている命令が終わるのを待つ）")
    return busy


def check_space(lay: Layout) -> list:
    out = []
    st = os.statvfs(lay.root)
    free = st.f_bavail * st.f_frsize
    if free < FREE_MIN:
        out.append(f"空きが {free // (1024 * 1024)} MB しかありません（{FREE_MIN // (1024 * 1024)} MB は要る）")
    if os.path.isdir(lay.ws) and os.stat(lay.ws).st_dev != os.stat(lay.root).st_dev:
        out.append("ws/ が根と別のディスクにあります（付け替えが一瞬で済まない）")
    return out


class AppLock:
    """`state/_app.lock`（共通・移さない）を握る。自己更新と重ならないため。"""

    def __init__(self, lay: Layout):
        self.path = lay.at_root("state/_app.lock")
        self.fd = None

    def __enter__(self):
        import fcntl
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            raise Stop("state/_app.lock を誰かが握っています（自己更新の途中か）。終わってから打ち直してください")
        return self

    def __exit__(self, *exc):
        os.close(self.fd)
        return False


# --------------------------------------------------------------------------- 止まり所（稽古の口）

class Stopper:
    def __init__(self, spec: str | None):
        self.step, self.n = None, None
        if spec:
            step, _, n = spec.partition(":")
            self.step, self.n = step, (int(n) if n else None)

    def after(self, step: str, i: int | None = None):
        if self.step != step:
            return
        if self.n is None and i is None:
            raise Halt(step)
        if self.n is not None and i == self.n:
            raise Halt(f"{step}:{i}")


# --------------------------------------------------------------------------- 移す

def preflight(lay: Layout, args, *, for_back: bool) -> list:
    issues = check_units(None if args.no_units_check else args.systemctl)
    if for_back:
        base = lay.main if os.path.isdir(lay.main) else lay.staging
        issues += check_locks(os.path.join(base, "state"))
    issues += check_locks(lay.at_root("state"))
    issues += check_space(lay)
    return issues


def cmd_check(lay: Layout, args) -> int:
    j = read_journal(lay)
    issues = preflight(lay, args, for_back=False)
    if j and j.get("state") in ("moving", "moved", "reversing"):
        say(f"帳面: state={j['state']}（{lay.journal}）")
        issues.append(f"移す作業の帳面が {j['state']} です（status で見て、move か back で続ける）")
    if exists(lay.main):
        issues.append("ws/main がもうあります（移したあとか）")
    if not os.path.isdir(lay.at_root("accounts")) or is_tombstone(lay.at_root("accounts")):
        issues.append("根に読める accounts/ がありません（移す前の形でない）")
    plan, unknown = classify(lay, set(args.keep), set(args.move))
    for u in unknown:
        issues.append(f"振り分けの分からない物: {u}（--keep {u.split('（')[0]} で共通に残す・--move で括りへ）")
    rewrites, problems = plan_rewrites(lay, plan) if not unknown else ([], [])
    issues += problems
    say(f"根: {lay.root}")
    say(f"移す物: {len(plan)}（上の段 {sum(1 for p in plan if '/' not in p['src'])}・state の中 "
        f"{sum(1 for p in plan if p['src'].startswith('state/'))}・private の中 {sum(1 for p in plan if p['src'].startswith('private/'))}）")
    for p in plan:
        say(f"  移す {p['src']} → ws/main/{p['dst']}{'' if p['tomb'] else '（印なし）'}")
    say(f"道の直し: {len(rewrites)}")
    for rw in rewrites:
        say(f"  直す {rw['file']}: {rw['old']} → {rw['new']}（{rw['count']} か所）")
    if not unknown:
        files, hits = rewrite_records(lay, lay.root, [p["src"] for p in plan], forward=True,
                                      rels=[p["src"] for p in plan], dry=True)
        say(f"記録の道: {hits} か所（ファイル {files}）を ws/main の道に書き替える")
    if issues:
        for i in issues:
            say(f"NG: {i}")
        return 1
    say("OK: 移せる形です")
    return 0


def cmd_move(lay: Layout, args) -> int:
    stopper = Stopper(args.stop_after)
    j = read_journal(lay)
    if j and j.get("state") == "moved":
        say("もう移してあります（帳面 state=moved）。何もしません")
        return 0
    if j and j.get("state") == "reversing":
        raise Stop("戻す途中です。先に back を最後まで打ってください")
    resuming = bool(j and j.get("state") == "moving")
    issues = preflight(lay, args, for_back=False)
    if not resuming:
        if exists(lay.main):
            issues.append("ws/main がもうあります")
        if exists(lay.staging):
            issues.append("ws/.main-staging が残っています（帳面が無いのに。中を見てください）")
        plan, unknown = classify(lay, set(args.keep), set(args.move))
        issues += [f"振り分けの分からない物: {u}" for u in unknown]
        if not any(p["src"] == "accounts" for p in plan):
            issues.append("根に accounts/ がありません（移す前の形でない）")
        rewrites, problems = plan_rewrites(lay, plan) if not unknown else ([], [])
        issues += problems
    else:
        plan, rewrites = j["plan"], j["rewrites"]
    if issues:
        for i in issues:
            say(f"NG: {i}")
        return 1
    if args.dry_run:
        say(f"乾いた打ち方: {'続きから' if resuming else '初めから'}。移す物 {len(plan)}・道の直し {len(rewrites)}・印 "
            f"{sum(1 for p in plan if p['tomb'])}")
        for p in plan:
            say(f"  移す {p['src']} → ws/main/{p['dst']}")
        for rw in rewrites:
            say(f"  直す {rw['file']}: {rw['old']} → {rw['new']}")
        say("  最後に ws/.main-staging → ws/main・印 MOVING を退ける")
        return 0
    os.makedirs(lay.ws, mode=0o700, exist_ok=True)
    with AppLock(lay):
        if not resuming:
            if exists(lay.move_dir):
                old = read_journal(lay)
                if old and old.get("state") != "reversed":
                    raise Stop(f"{lay.move_dir} に終わっていない帳面があります（state={old.get('state')}）")
                os.rename(lay.move_dir, f"{lay.move_dir}-{now()}")
            os.makedirs(lay.move_dir, mode=0o700)
            with open(lay.marker, "w", encoding="utf-8") as f:
                f.write("main を ws/main へ移す途中（tools/ws_move_main.py）\n")
            j = {"schema": SCHEMA, "root": lay.root, "state": "moving", "plan": plan, "rewrites": rewrites,
                 "started_at": now()}
            note(lay, j, "begin")
        elif not exists(lay.marker):
            with open(lay.marker, "w", encoding="utf-8") as f:
                f.write("main を ws/main へ移す途中（tools/ws_move_main.py）\n")
        stopper.after("begin")
        committed = os.path.isdir(lay.main) and not exists(lay.staging)
        # 1. 中身を ws/.main-staging/ へ
        if not committed:
            for sub in ("", "state", "private"):
                os.makedirs(os.path.join(lay.staging, sub), mode=0o700, exist_ok=True)
        for i, item in enumerate(plan, 1):
            src, dst = lay.at_root(item["src"]), (lay.main if committed else lay.staging)
            dst = os.path.join(dst, item["dst"])
            src_real = exists(src) and not is_tombstone(src)
            if src_real and not exists(dst):
                os.rename(src, dst)
            elif not src_real and exists(dst):
                pass
            elif src_real and exists(dst):
                raise Stop(f"{item['src']} が根と ws の両方にあります。中を見てください")
            else:
                raise Stop(f"{item['src']} がどこにもありません")
            stopper.after("move", i)
        note(lay, j, "moved_entries", n=len(plan))
        stopper.after("move")
        # 2. 道の直し
        base = lay.main if committed else lay.staging
        for i, rw in enumerate(rewrites, 1):
            apply_rewrite(os.path.join(base, rw["file"]), rw, forward=True)
            stopper.after("rewrite", i)
        note(lay, j, "rewrote", n=len(rewrites))
        stopper.after("rewrite")
        # 2'. 記録の道（承認・止めの記録が持つ絶対の道。打ち直しても二重にならない）
        files, hits = rewrite_records(lay, base, [p["src"] for p in plan], forward=True, rels=[p["dst"] for p in plan])
        note(lay, j, "records", files=files, n=hits)
        stopper.after("records")
        # 2''. 書き替え損ねを断る（止めが消えると止めていた投稿が出る。黙って進まない・第24 11/29）
        left = leftover_records(lay, base, [p["src"] for p in plan], [p["dst"] for p in plan])
        if left:
            note(lay, j, "records_left", files=len(left), n=sum(n for _, n in left))
            for rel, n in left[:20]:
                say(f"NG: 記録に移す前の根の道が残っています: {rel}（{n} か所）")
            raise Stop(f"移した記録に、移す前の根の道が {sum(n for _, n in left)} か所（ファイル {len(left)}）残っています。"
                       "このまま進むと、承認と止めが無いものとされます（止めていた投稿が出る）。"
                       "timer と常駐を止めたまま `back` で戻し、残った道の形を見てから打ち直してください")
        # 3. 元の場所に開けないフォルダ
        for i, item in enumerate(plan, 1):
            if not item["tomb"]:
                continue
            src = lay.at_root(item["src"])
            if not exists(src):
                os.mkdir(src, 0o700)
                os.chmod(src, 0)
            elif os.path.isdir(src) and not os.path.islink(src) and not is_tombstone(src) and not os.listdir(src):
                os.chmod(src, 0)  # mkdir のあと chmod の前に落ちた跡
            elif not is_tombstone(src):
                raise Stop(f"{item['src']} に移したあとで何かができています。中を見てください")
            stopper.after("tomb", i)
        note(lay, j, "tombstoned")
        stopper.after("tomb")
        # 4. 付け替え（1 回の rename）
        if not committed:
            if exists(lay.main):
                raise Stop("ws/main と ws/.main-staging の両方があります")
            os.chmod(lay.staging, 0o700)
            os.rename(lay.staging, lay.main)
        note(lay, j, "committed")
        stopper.after("commit")
        # 5. 印を退ける（消さない）
        j["state"] = "moved"
        j["moved_at"] = now()
        note(lay, j, "finished")
        if exists(lay.marker):
            os.rename(lay.marker, os.path.join(lay.move_dir, f"moved-{now()}"))
    say(f"OK: main を {lay.main} へ移しました（移した物 {len(plan)}・道の直し {len(rewrites)}）")
    return 0


# --------------------------------------------------------------------------- 戻す

def cmd_back(lay: Layout, args) -> int:
    stopper = Stopper(args.stop_after)
    j = read_journal(lay)
    if not j:
        raise Stop(f"帳面がありません（{lay.journal}）。移していないか、帳面を退けたか")
    if j.get("state") == "reversed":
        say("もう戻してあります（帳面 state=reversed）。何もしません")
        return 0
    issues = preflight(lay, args, for_back=True)
    if issues:
        for i in issues:
            say(f"NG: {i}")
        return 1
    plan, rewrites = j["plan"], j["rewrites"]
    if args.dry_run:
        say(f"乾いた打ち方: 帳面 state={j['state']}。ws/main → ws/.main-staging・開けないフォルダを退ける・"
            f"道の直し {len(rewrites)} を戻す・中身を根へ戻す")
        src_base = lay.main if os.path.isdir(lay.main) else lay.staging
        for sub in ("", "state", "private"):
            d = os.path.join(src_base, sub)
            if os.path.isdir(d):
                for name in sorted(os.listdir(d)):
                    if sub == "" and name in ("state", "private"):
                        continue
                    say(f"  戻す ws/main/{os.path.join(sub, name) if sub else name} → {os.path.join(sub, name) if sub else name}")
        return 0
    with AppLock(lay):
        if not exists(lay.marker):
            with open(lay.marker, "w", encoding="utf-8") as f:
                f.write("main を ws/main から戻す途中（tools/ws_move_main.py）\n")
        j["state"] = "reversing"
        note(lay, j, "back_begin")
        stopper.after("back_begin")
        # 1. 付け替えを外す
        if os.path.isdir(lay.main) and not exists(lay.staging):
            os.rename(lay.main, lay.staging)
        elif os.path.isdir(lay.main) and exists(lay.staging):
            raise Stop("ws/main と ws/.main-staging の両方があります。中を見てください")
        note(lay, j, "uncommitted")
        stopper.after("uncommit")
        # 2. 開けないフォルダを退ける（消さない）
        grave = os.path.join(lay.move_dir, f"tombstones-{now()}")
        for i, item in enumerate(plan, 1):
            src = lay.at_root(item["src"])
            if is_tombstone(src):
                os.chmod(src, 0o700)
                if os.listdir(src):
                    os.chmod(src, 0)
                    raise Stop(f"{item['src']} の開けないフォルダの中に何かがあります。中を見てください")
                os.makedirs(grave, mode=0o700, exist_ok=True)
                os.rename(src, os.path.join(grave, item["src"].replace("/", "__")))
            stopper.after("untomb", i)
        note(lay, j, "untombstoned")
        stopper.after("untomb")
        # 3. 道の直しを戻す
        for i, rw in enumerate(rewrites, 1):
            staged = lay.at_staging(rw["file"])
            target = staged if exists(staged) else lay.at_root(rw["file"])
            if exists(target):
                apply_rewrite(target, rw, forward=False)
            stopper.after("unrewrite", i)
        note(lay, j, "unrewrote")
        stopper.after("unrewrite")
        # 3'. 記録の道を戻す（移していた間に書かれた行も・ws/.main-staging の中の全部）
        if os.path.isdir(lay.staging):
            files, hits = rewrite_records(lay, lay.staging, [p["src"] for p in plan], forward=False)
            note(lay, j, "unrecords", files=files, n=hits)
        stopper.after("unrecords")
        # 4. 中身を根へ（移したあとでできた物も、main の物なので一緒に戻す）
        moved_back = 0
        if os.path.isdir(lay.staging):
            for sub in ("state", "private", ""):
                d = os.path.join(lay.staging, sub)
                if not os.path.isdir(d):
                    continue
                for name in sorted(os.listdir(d)):
                    if sub == "" and name in ("state", "private"):
                        continue
                    rel = f"{sub}/{name}" if sub else name
                    src, dst = os.path.join(d, name), lay.at_root(rel)
                    if exists(dst):
                        raise Stop(f"{rel} が根にもあります（戻す先が空いていない）。中を見てください")
                    os.rename(src, dst)
                    moved_back += 1
                    stopper.after("moveback", moved_back)
            for sub in ("state", "private", ""):
                d = os.path.join(lay.staging, sub)
                if os.path.isdir(d) and os.listdir(d) and sub:
                    raise Stop(f"ws/.main-staging/{sub} が空になりません")
            os.rename(lay.staging, os.path.join(lay.move_dir, f"staging-emptied-{now()}"))
        note(lay, j, "moved_back", n=moved_back)
        stopper.after("moveback")
        j["state"] = "reversed"
        j["reversed_at"] = now()
        note(lay, j, "back_finished")
        if exists(lay.marker):
            os.rename(lay.marker, os.path.join(lay.move_dir, f"reversed-{now()}"))
    say(f"OK: main を根へ戻しました（戻した物 {moved_back}）。版を戻すのはこのあと")
    return 0


def cmd_status(lay: Layout, args) -> int:
    j = read_journal(lay)
    say(f"根: {lay.root}")
    say(f"ws/main: {'ある' if os.path.isdir(lay.main) else '無い'}・ws/.main-staging: "
        f"{'ある' if exists(lay.staging) else '無い'}・印 MOVING: {'ある' if exists(lay.marker) else '無い'}")
    if not j:
        say("帳面: 無い（移していない）")
        return 0
    say(f"帳面: state={j.get('state')}・移す物 {len(j.get('plan', []))}・道の直し {len(j.get('rewrites', []))}")
    for h in j.get("history", [])[-12:]:
        say(f"  {h['at']} {h['step']}")
    for item in j.get("plan", []):
        src = lay.at_root(item["src"])
        where = ("根（開けないフォルダ）" if is_tombstone(src) else "根" if exists(src) else
                 "ws/main" if exists(os.path.join(lay.main, item["dst"])) else
                 "ws/.main-staging" if exists(lay.at_staging(item["dst"])) else "どこにも無い")
        say(f"  {item['src']}: {where}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="main の置き場を ws/main へ移す・戻す（括りの段 5）")
    ap.add_argument("command", choices=("check", "move", "back", "status"))
    ap.add_argument("--root", required=True, help="置き場の根（本番なら /srv/thth）。既定はありません")
    ap.add_argument("--dry-run", action="store_true", help="何をするかだけ出す")
    ap.add_argument("--keep", action="append", default=[], help="振り分けの分からない物を共通の根に残す（state/ の中は state/<名>）")
    ap.add_argument("--move", action="append", default=[], help="振り分けの分からない物を括りへ移す")
    ap.add_argument("--systemctl", default="systemctl", help="timer と常駐の確かめに使う systemctl")
    ap.add_argument("--no-units-check", action="store_true", help="systemd の無い手元の稽古だけ（VM では使わない）")
    ap.add_argument("--stop-after", help=argparse.SUPPRESS)  # 通し稽古の「途中で落ちた」
    args = ap.parse_args(argv)
    try:
        lay = Layout(os.path.abspath(args.root))
        if args.no_units_check and args.command in ("move", "back") and not args.dry_run:
            say("注意: timer と常駐の確かめを飛ばしています（--no-units-check）")
        return {"check": cmd_check, "move": cmd_move, "back": cmd_back, "status": cmd_status}[args.command](lay, args)
    except Halt as h:
        say(f"止めた: {h}（稽古の止まり所）")
        return 3
    except Stop as s:
        say(f"NG: {s}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
