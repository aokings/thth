"""外す範囲の 2 つめと 3 つめ（台帳の器 段 8・指示 `docs/指示_台帳の器_段8_健康と複数のメディア_2026-11-14.md` §5）。

    thth project leave <メディア> --by <名前> [--confirm D] [--json]
        そのメディアの全部の口座（今の `thth account leave` を 1 本ずつ）と、THTH の側の台帳の控え（`registers/<名前>.json`
        と `register_state/<名前>/`）を消す。台帳のフォルダ（`.thth/` の判断・封・控えも）・共通の台帳・ほかのメディアは残る。
    thth owner leave --by <名前> [--confirm D] [--json]
        全部の口座・THTH の側の台帳の控え（共通の台帳のも）・主体（`principals/`・`principal_state/`・`owner_state/` と、
        THTH の置き場にある主体の鍵）・封の秘密の鍵を消す。全部の台帳のフォルダは残る。

- **二段**: 1 回めは消える物と残る物の数を出すだけで、何も消さない（`--confirm` の digest も出す）。digest は消す物の
  並びから作るので、見せたあとに口座や控えが増減すれば合わなくなり、もう一度見積りからになる。
- **途中で止まっても打ち直せる**: 記録 `$THTH_ROOT/register_leave/<project-名前|owner>.json`（0600）に、途中の段と
  消した種類と数を書く（`thth/leave.py` の流儀）。止まったら同じ命令（同じ `--confirm`）をもう一度打つと続きから進み、
  済んだ物を二度消さない。
- **言った範囲より広く消さない**: 消すのは THTH の置き場の物だけ。台帳のフォルダは、どの控えのフォルダも（書かれた道と
  追った先の両方）、消す所と重なれば何も消さずに断る（`register_leave_overlap`）。控えの道が symlink・`..`・THTH の
  置き場の中を指すように書き換えてあっても、フォルダの中は読むだけで消さない。主体の鍵は、THTH の置き場（`~/.config/thth/`・
  `$THTH_ROOT/secrets/`）にある普通のファイルだけを消し、ほかは残して言う。封の秘密の鍵は `owner leave` のときだけ消す。
- 持ち出し（`register export-ledger`）は作っていない: 共通の出さない欄を外す口（段 7 の `private_fields` を共通の種類に
  当てる道）が公開の関数に無く、採った版の並び（`route._takes`）も内の関数なので、付けると出さない欄が漏れうる（報告）。

**この部品は台帳の側だけを持つ**（控え・記録の置き場・重なりの点検・封の鍵・途中の記録・文）。口座を外す
（`thth/leave.py` の `run`・遠隔の取り消しがある）と主体を消すのは `thth/leave.py` の `scope_run` が受け持ち、ここからは
呼ばない（台帳の機械は網の部品を import しない・`tests/test_register_base.py` の柵）。命令の登録も `thth/leave.py`。
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import stat
import sys

from .. import accounts, admin_log, draft_origin, jst, secrets_fs
from . import ACTION_PREFIX, ledger, seal
from . import vessel as V
from .errors import RegisterRefused

JOURNAL_DIR = "register_leave"
SCHEMA = "thth.register_leave/1"
SCOPES = ("project", "owner")
PHASES = ("planned", "accounts", "registers", "principals", "seal_key", "completed", "changed")
DONE_DIR = "done"            # 済んだ・やめた記録（名前に日時を付けて残す。次の回の邪魔をしない）
COUNTS = ("accounts", "register_controls", "register_state", "principals", "principal_state", "owner_state",
          "principal_tokens", "seal_key")
SEAL_NOTICE = ("封の秘密の鍵を消します。この鍵で封をした台帳は、ほかの機械で（公開鍵で）確かめ続けられますが、"
               "新しい封は作れなくなります。続けて封をするなら、先に thth register key show で公開鍵を控え、"
               "鍵を THTH の外へ写しておいてください")


# ------------------------------------------------------------------ 置き場と記録

def journal_dir() -> str:
    return os.path.join(accounts.thth_root(), JOURNAL_DIR)


def _journal_name(scope: str, target) -> str:
    return "owner.json" if scope == "owner" else f"project-{target}.json"


def read_journal(scope: str, target=None) -> dict | None:
    path = os.path.join(journal_dir(), _journal_name(scope, target))
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise RegisterRefused("register_leave_incomplete", "journal") from None
    if (not isinstance(value, dict) or value.get("schema") != SCHEMA or value.get("scope") != scope
            or value.get("target") != target or value.get("phase") not in PHASES
            or not isinstance(value.get("digest"), str) or not isinstance(value.get("done"), dict)
            or not isinstance(value.get("deleted"), dict) or not isinstance(value.get("kept"), list)):
        raise RegisterRefused("register_leave_incomplete", "journal")
    for key in ("accounts", "registers", "principals"):
        if not isinstance(value.get(key), list) or not all(ledger.name_ok(n) for n in value[key]):
            raise RegisterRefused("register_leave_incomplete", "journal")
    if not isinstance(value.get("account_meta", {}), dict) or not isinstance(value.get("register_meta", {}), dict):
        raise RegisterRefused("register_leave_incomplete", "journal")
    return value


def archive(journal: dict) -> None:
    """済んだ（やめた）記録を `done/` へ、名前に日時と回の印を付けて移す（今の名前は次の回のために空ける）。"""
    stamp = jst.iso().replace(":", "").replace("+", "p")
    name = _journal_name(journal["scope"], journal["target"])[:-5] + f".{stamp}.{journal['operation_id'][:8]}.json"
    secrets_fs.atomic_write_json(os.path.join(journal_dir(), DONE_DIR, name), journal, mode=0o600)
    os.unlink(os.path.join(journal_dir(), _journal_name(journal["scope"], journal["target"])))


def archived(scope: str, target, confirm) -> dict | None:
    """`done/` の記録のうち、この範囲で digest が `confirm` の物（打ち直しで同じ結果を返すため）。"""
    if not confirm:
        return None
    base = os.path.join(journal_dir(), DONE_DIR)
    head = _journal_name(scope, target)[:-5] + "."
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return None
    for entry in entries:
        if not entry.startswith(head) or not entry.endswith(".json"):
            continue
        try:
            with open(os.path.join(base, entry), encoding="utf-8") as f:
                value = json.load(f)
        except (OSError, ValueError):
            continue
        if (isinstance(value, dict) and value.get("scope") == scope and value.get("target") == target
                and value.get("digest") == confirm):
            return value
    return None


def save(journal: dict) -> None:
    os.makedirs(journal_dir(), mode=0o700, exist_ok=True)
    secrets_fs.atomic_write_json(os.path.join(journal_dir(), _journal_name(journal["scope"], journal["target"])),
                                 journal, mode=0o600)


def _real(path: str) -> str:
    return os.path.realpath(os.path.abspath(path))


def _inside(path: str, directory: str) -> bool:
    path, directory = _real(path), _real(directory)
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:
        return False


def _overlap(a: str, b: str) -> bool:
    return _inside(a, b) or _inside(b, a)


# ------------------------------------------------------------------ 見積り

def _controls() -> tuple:
    """`({名前: 控え}, [読めない控えの名前])`。"""
    good, bad = {}, []
    for name in ledger.list_names():
        try:
            good[name] = ledger.load(name)
        except RegisterRefused:
            bad.append(name)
    return good, bad


def folders(controls: dict | None = None) -> list:
    """全部の控えの台帳のフォルダ（書かれた道と追った先の両方）。消さない物の囲い。**読めない控えも、生の JSON の
    `path` を拾って入れる**（検収の穴 1: 読めない控えのフォルダが囲いから外れていた）。"""
    if controls is None:
        controls, _ = _controls()
    out = set()
    paths = [cfg["path"] for cfg in controls.values()]
    paths += [p for p in (ledger.raw_path(n) for n in ledger.list_names() if n not in controls) if p]
    for path in paths:
        out.add(os.path.abspath(path))
        out.add(_real(path))
    return sorted(out)


def _files_in(folder: str) -> int:
    """見積りのファイルの数（上限つき・`/` や上限を超える広さは `register_folder_too_wide`・検収の穴 3）。"""
    return ledger.check_walkable(folder)


def has_register_inside(path: str) -> bool:
    """`path`（THTH が丸ごと消す木）の中に、台帳のフォルダらしい物（`.thth/` を持つフォルダ）が 1 つでもあるか。
    控えが読めるかどうかに頼らず、構造で見る（検収の穴 1 の最後の柵）。symlink は追わない。歩く数に上限を置き、
    超えたら「ある」とみなす（狭い側）。"""
    if os.path.islink(path) or not os.path.isdir(path):
        return False
    seen = 0
    for _here, dirs, files in os.walk(path, followlinks=False):
        if V.DECL_DIR in dirs:
            return True
        seen += len(files) + len(dirs)
        if seen > ledger.WALK_MAX:
            return True
    return False


def control_meta(cfg: dict) -> dict:
    """見積りのときの控えの所属（打ち直しで照らす）: 道・プロジェクト・役。"""
    return {"path": cfg["path"], "project": cfg["project"], "role": cfg.get(ledger.ROLE_FIELD)}


def check_control_same(name: str, meta) -> None:
    """これから消す控えが、見積りのときと同じ道・プロジェクト・役か。違えば `register_leave_changed`（消さない）。
    控えがもう無いのは「消す物が無い」で、照らさない。読めなければ違うとみなす（狭い側）。"""
    if not os.path.lexists(ledger.path_for(name)):
        return
    try:
        now = control_meta(ledger.load(name))
    except RegisterRefused:
        raise RegisterRefused("register_leave_changed", "register", register=name, was=meta, now=None) from None
    if meta is None or now != meta:
        raise RegisterRefused("register_leave_changed", "register", register=name, was=meta, now=now)


def register_plan(scope: str, target=None) -> dict:
    """台帳の側の見積り: 消す控え・共通の台帳・残るフォルダとファイルの数・ほかのメディアの控え。何も書かない。"""
    controls, unreadable = _controls()
    common = sorted(n for n, cfg in controls.items() if ledger.is_common(cfg))
    if scope == "project":
        gone = sorted(n for n, cfg in controls.items() if cfg["project"] == target and not ledger.is_common(cfg))
        others = sorted({cfg["project"] for cfg in controls.values()
                         if not ledger.is_common(cfg) and cfg["project"] != target})
    else:
        gone, others = sorted(controls), []
    seen, count, files = set(), 0, 0
    for cfg in controls.values():
        real = _real(cfg["path"])
        if real in seen:
            continue
        seen.add(real)
        count += 1
        files += _files_in(real)
    return {"registers": gone, "register_meta": {n: control_meta(controls[n]) for n in gone},
            "common": common, "other_projects": others, "register_folders": count,
            "register_files": files, "kept": [{"what": "register_control_unreadable", "name": n} for n in unreadable],
            "folders": folders(controls)}


def digest(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


# ------------------------------------------------------------------ 重なりの点検（消す前に・何も消さずに断る）

def _register_places(name: str) -> tuple:
    return ledger.state_dir_for(name), ledger.path_for(name)


def _legacy_inside_state(name: str, guard: list) -> bool:
    """台帳のフォルダが認めた記録の置き場の中にある（5.19.0 の add の断りより前に控えた物・気づき 8）。"""
    state = _real(ledger.state_dir_for(name))
    return any(_inside(f, state) and _real(f) != state for f in guard)


def preflight_registers(names: list, guard: list) -> None:
    """控えと記録の置き場が台帳のフォルダの中か、記録の置き場の中に台帳のフォルダらしい物があれば
    `register_leave_overlap`（何も消さない）。"""
    for name in names:
        state, control = _register_places(name)
        for folder in guard:
            if _inside(state, folder) or _inside(control, folder):
                raise RegisterRefused("register_leave_overlap", "register_state", register=name)
        if not _legacy_inside_state(name, guard) and has_register_inside(state):
            raise RegisterRefused("register_leave_overlap", "register_state", register=name)


# ------------------------------------------------------------------ 消す（THTH の置き場の物だけ）

def _tree_count(path: str) -> int:
    count = 0
    for _here, dirs, files in os.walk(path, followlinks=False):
        count += len(files) + sum(1 for d in dirs if os.path.islink(os.path.join(_here, d)))
    return count


def remove_tree(path: str, guard: list, kept: list, what: str, name: str) -> int:
    """THTH の置き場の木を消す（symlink の木は追わず残す・台帳のフォルダと重なれば残す）。消したファイルの数。"""
    if not os.path.lexists(path):
        return 0
    if os.path.islink(path) or not os.path.isdir(path) or any(_overlap(path, f) for f in guard):
        kept.append({"what": what + "_kept", "name": name})
        return 0
    if has_register_inside(path):
        # 最後の柵: 控えが読めなくても、中に台帳のフォルダらしい物があれば何も消さずに止まる。
        raise RegisterRefused("register_leave_overlap", what, name=name)
    count = _tree_count(path)
    shutil.rmtree(path)
    return count


def remove_file(path: str, guard: list) -> int:
    """普通のファイルだけを消す（symlink は追わない・台帳のフォルダの中なら消さない）。消した数（0 か 1）。"""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return 0
    if not stat.S_ISREG(info.st_mode) or any(_inside(path, f) for f in guard):
        return 0
    os.unlink(path)
    return 1


def remove_control(name: str, guard: list, kept: list) -> dict:
    """THTH の側の控えと認めた記録を消す。台帳のフォルダには触れない。"""
    state, control = _register_places(name)
    done = {"register_controls": 0, "register_state": 0}
    if _legacy_inside_state(name, guard):
        for leaf in (ledger.CONFIRMED_FILE, draft_origin.FILE):
            done["register_state"] += remove_file(os.path.join(state, leaf), guard)
        kept.append({"what": "register_state_holds_folder", "name": name})
    else:
        done["register_state"] = remove_tree(state, guard, kept, "register_state", name)
    if os.path.islink(control):
        kept.append({"what": "register_control_link", "name": name})
    else:
        done["register_controls"] = remove_file(control, guard)
    return done


def remove_seal_key(guard: list, kept: list) -> int:
    path = seal.key_path()
    if not os.path.lexists(path):
        return 0
    if os.path.islink(path) or not os.path.isfile(path):
        kept.append({"what": "seal_key_not_regular", "name": "seal"})
        return 0
    for folder in guard:
        try:
            seal.check_key_place(folder)
        except RegisterRefused:
            kept.append({"what": "seal_key_inside_register", "name": "seal"})
            return 0
    return remove_file(path, guard)


# ------------------------------------------------------------------ 進める

def _log_removed(name: str, done: dict, journal: dict, by: str) -> None:
    try:
        entries, _ = admin_log.read(account=name, event=ACTION_PREFIX + "removed")
        if any(e.get("run_id") == journal["operation_id"] for e in entries):
            return
        kinds = [k for k in ("register_controls", "register_state") if done.get(k)]
        admin_log.append(ACTION_PREFIX + "removed", name, {"media": "register"}, by=by,
                         diff={"deleted": [[], kinds], "via": [None, journal["scope"] + "_leave"]},
                         run_id=journal["operation_id"])
    except (OSError, ValueError, admin_log.AdminLogError):
        raise RegisterRefused("register_log_unavailable") from None


def add(journal: dict, counts: dict) -> None:
    for key, value in counts.items():
        journal["deleted"][key] = journal["deleted"].get(key, 0) + int(value)


def remove_registers(journal: dict, by: str, guard: list) -> None:
    """段 `registers`: 記録にある控えを 1 つずつ消し、済んだ物を記録する（打ち直しで二度消さない）。"""
    done = journal["done"]
    preflight_registers([n for n in journal["registers"] if n not in done.get("registers", [])], guard)
    for name in journal["registers"]:
        if name in done.setdefault("registers", []):
            continue
        check_control_same(name, journal.get("register_meta", {}).get(name))
        counts = remove_control(name, guard, journal["kept"])
        _log_removed(name, counts, journal, by)
        done["registers"].append(name)
        add(journal, counts)
        save(journal)


def remove_seal(journal: dict, guard: list) -> None:
    """段 `seal_key`（`owner leave` だけ）。"""
    done = journal["done"]
    if journal["seal_key"] and not done.get("seal_key"):
        add(journal, {"seal_key": remove_seal_key(guard, journal["kept"])})
        done["seal_key"] = True
        save(journal)


@contextlib.contextmanager
def journal_lock(scope: str, target):
    """同じ範囲の外し方を 1 つずつにする（記録の置き場の中の lock・symlink を追わない）。"""
    os.makedirs(journal_dir(), mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(journal_dir(), _journal_name(scope, target) + ".lock"),
                 os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def public(journal: dict) -> dict:
    return {key: journal[key] for key in ("scope", "target", "phase", "operation_id", "digest", "started_at",
                                          "completed_at", "deleted", "kept", "accounts", "registers", "principals",
                                          "seal_key") if key in journal}


# ------------------------------------------------------------------ 命令

def _names(values) -> str:
    return "・".join(values) if values else "なし"


def _plan_lines(value: dict) -> list:
    scope, target = value["scope"], value["target"]
    head = f"メディア {target} を外す見積り" if scope == "project" else "持ち主の全部をやめる見積り"
    remain = value["remain"]
    gone = [f"口座 {len(value['accounts'])} 本（{_names(value['accounts'])}）",
            f"THTH の側の台帳の控え {len(value['registers'])} つ（{_names(value['registers'])}）"]
    if scope == "owner":
        gone += [f"主体 {len(value['principals'])} つ（{_names(value['principals'])}）",
                 "封の秘密の鍵" + ("" if value["seal_key"] else "（もともと無い）")]
    keep = [f"台帳のフォルダ {remain['register_folders']} つ（ファイル {remain['register_files']}。.thth/ の判断・封・控えも）"]
    if scope == "project":
        keep += [f"共通の台帳 {_names(remain['common'])}", f"ほかのメディア {_names(remain['other_media'])}"]
    lines = [f"{head}（まだ何も消していません）", "  消える物: " + "・".join(gone), "  残る物: " + "・".join(keep)]
    for item in value["kept"]:
        lines.append(f"  消さずに残す物: {item['what']} {item['name']}")
    if scope == "owner" and value["seal_key"]:
        lines.append("  知らせ: " + SEAL_NOTICE)
    command = f"thth project leave {target}" if scope == "project" else "thth owner leave"
    lines.append(f"  よければ: {command} --by <名前> --confirm {value['digest']}")
    return lines


def _done_lines(value: dict) -> list:
    scope, target = value["scope"], value["target"]
    head = f"メディア {target} を外しました" if scope == "project" else "持ち主の全部をやめました"
    counts = "・".join(f"{k} {value['deleted'][k]}" for k in COUNTS if value["deleted"].get(k))
    lines = [f"{head}（消した物: {counts or 'なし'}）。台帳のフォルダには触っていません"]
    for item in value.get("kept") or []:
        lines.append(f"  消さずに残した物: {item['what']} {item['name']}")
    if scope == "project":
        lines.append("  共通の台帳とほかのメディアは残っています")
    return lines


def refuse(args, exc: RegisterRefused) -> int:
    from .cli import _refuse
    reason = exc.extra.get("reason")
    if reason and not getattr(args, "json", False):
        print(f"  口座 {exc.extra.get('account')}: {reason}", file=sys.stderr)
    return _refuse(args, exc)


def show(args, value: dict) -> int:
    if args.json:
        value = {k: v for k, v in value.items() if k != "folders"}
        if value["scope"] == "owner" and value.get("seal_key"):
            value["notice"] = SEAL_NOTICE
        print(json.dumps(value, ensure_ascii=False))
        return 0
    for line in (_plan_lines(value) if value["stage"] == "plan" else _done_lines(value)):
        print(line)
    return 0
