"""THTH の側の控え（台帳そのものは持ち主の物で、THTH は預からない）。

- 控え: `$THTH_ROOT/registers/<名前>.json`（`THTH_REGISTERS_DIR` で差し替え可・0600）。欄は
  `register`・`project`・`path`（台帳のフォルダ・絶対の道）・`kind`（`media` か `store`）の 4 つだけ。
- 認めた記録: `$THTH_ROOT/register_state/<名前>/confirmed.json`（0600）。行の番号 → `{"digest", "at", "by"}`。
  台帳の頭の `confirmed: 本人が認めた` は手でも LLM でも書けるので、柵はこの記録の指紋で見る（計画 §8 の 6）。

`thth register remove` と `thth leave`（その project の最後の口座）が控えと記録を消す。台帳のフォルダは消さない。
"""
from __future__ import annotations

import json
import os

from .. import accounts, secrets_fs
from .errors import RegisterRefused

REGISTERS_DIR_ENV = "THTH_REGISTERS_DIR"
KINDS = ("media", "store")
FIELDS = ("register", "project", "path", "kind")
CONFIRMED_FILE = "confirmed.json"
CONFIRMED_SCHEMA = "thth.register_confirmed/1"


def registers_dir() -> str:
    env = os.environ.get(REGISTERS_DIR_ENV)
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(accounts.thth_root(), "registers")


def check_name(name) -> str:
    if not accounts.name_is_safe(name):
        raise RegisterRefused("register_args_invalid", "register_name")
    return name


def path_for(name: str) -> str:
    return os.path.join(registers_dir(), f"{check_name(name)}.json")


def state_dir_for(name: str) -> str:
    return os.path.join(accounts.thth_root(), "register_state", check_name(name))


def list_names() -> list:
    try:
        entries = sorted(os.listdir(registers_dir()))
    except OSError:
        return []
    return [e[:-5] for e in entries if e.endswith(".json") and accounts.name_is_safe(e[:-5])]


def check_path(value) -> str:
    """台帳のフォルダ（あるフォルダで、symlink でない・絶対の道に直す）。"""
    if not isinstance(value, str) or not value.strip() or "\n" in value:
        raise RegisterRefused("register_path_unusable")
    path = os.path.abspath(os.path.expanduser(value))
    if os.path.islink(path) or not os.path.isdir(path):
        raise RegisterRefused("register_path_unusable")
    return path


def _inside(path: str, directory: str) -> bool:
    path, directory = os.path.realpath(path), os.path.realpath(directory)
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:
        return False


def thth_owned_dirs() -> list:
    """THTH が `register remove`・`leave` で丸ごと消しうる置き場（台帳のフォルダを置いてはいけない所）。"""
    return [os.path.join(accounts.thth_root(), "register_state"), registers_dir()]


def check_outside_thth(path: str) -> str:
    """台帳のフォルダが THTH の置き場（認めた記録の置き場・控えの置き場）の中なら断る（消えるので・気づき 8）。"""
    if any(_inside(path, d) for d in thth_owned_dirs()):
        raise RegisterRefused("register_path_unusable", "inside_thth_state")
    return path


def validate(name: str, data) -> None:
    if (not isinstance(data, dict) or set(data) != set(FIELDS) or data.get("register") != name
            or data.get("kind") not in KINDS
            or not isinstance(data.get("project"), str) or not accounts.name_is_safe(data["project"])
            or not isinstance(data.get("path"), str) or not os.path.isabs(data["path"])):
        raise RegisterRefused("register_ledger_invalid")


def build(name: str, *, project: str, path: str, kind: str) -> dict:
    return {"register": name, "project": project, "path": path, "kind": kind}


def write_new(name: str, data: dict) -> str:
    validate(name, data)
    target = path_for(name)
    os.makedirs(os.path.dirname(target), mode=0o700, exist_ok=True)
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise RegisterRefused("register_name_taken") from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return target


def load(name: str) -> dict:
    try:
        with open(path_for(name), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        raise RegisterRefused("register_missing") from None
    except (OSError, ValueError):
        raise RegisterRefused("register_ledger_invalid") from None
    validate(name, data)
    return dict(data)


def root_of(cfg: dict) -> str:
    """控えの台帳のフォルダ（今もあるフォルダか確かめる）。"""
    return check_path(cfg["path"])


# ------------------------------------------------------------------ 認めた記録

def confirmed_path(name: str) -> str:
    return os.path.join(state_dir_for(name), CONFIRMED_FILE)


def read_confirmed(name: str) -> dict:
    """行の番号 → `{"digest", "at", "by"}`。無ければ空。壊れていれば断る（認めたと読み違えない）。"""
    try:
        with open(confirmed_path(name), encoding="utf-8") as f:
            value = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise RegisterRefused("register_ledger_invalid", "confirmed") from None
    rows = value.get("rows") if isinstance(value, dict) and value.get("schema") == CONFIRMED_SCHEMA else None
    if not isinstance(rows, dict) or not all(isinstance(v, dict) and isinstance(v.get("digest"), str)
                                             for v in rows.values()):
        raise RegisterRefused("register_ledger_invalid", "confirmed")
    return rows


def write_confirmed(name: str, rows: dict) -> None:
    secrets_fs.atomic_write_json(confirmed_path(name), {"schema": CONFIRMED_SCHEMA, "rows": rows}, mode=0o600)


def of_project(project: str) -> list:
    out = []
    for name in list_names():
        try:
            if load(name)["project"] == project:
                out.append(name)
        except RegisterRefused:
            continue
    return out
