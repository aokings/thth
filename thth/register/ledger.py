"""THTH の側の控え（台帳そのものは持ち主の物で、THTH は預からない）。

- 控え: `$THTH_ROOT/registers/<名前>.json`（`THTH_REGISTERS_DIR` で差し替え可・0600）。欄は
  `register`・`project`・`path`（台帳のフォルダ・絶対の道）・`kind`（`media` か `store`）の 4 つと、共通の台帳だけが
  持つ `role: common`（台帳の器 段 8・持ち主に 1 つ）。
- 認めた記録: `$THTH_ROOT/register_state/<名前>/confirmed.json`（0600）。行の番号 → `{"digest", "at", "by"}`。
  台帳の頭の `confirmed: 本人が認めた` は手でも LLM でも書けるので、柵はこの記録の指紋で見る（計画 §8 の 6）。

`thth register remove`・`thth project leave <メディア>`（共通の台帳の控えは消さない）・`thth owner leave` が控えと記録を
消す。台帳のフォルダは消さない。口座の `thth account leave` は、その project の最後の口座でも控えに触れない（段 8）。

**1 名前に道は 1 つでも、別の checkout を指せる**（にがみラボ 5.22.0 のつまずき）: 本体・作業木・配信の作業木で台帳の
フォルダの場所が違っても、控え直さずに `<台帳> --path <フォルダ>` か `<台帳>` にフォルダの道を渡して読める。別の道が
同じ台帳かは、台帳の中の名乗り（`.thth/register.json`・`thth.register_name/1`・欄は schema と register だけ）で確かめる。
名乗りの名前が違う・名乗りが崩れている・名乗りの無い別の道は `register_path_other` で断る（控えの道そのものは名乗り
が無くても今までどおり）。控えの道の台帳の名乗りが控えの名前と違うときも断る。指した道はその 1 回の命令の間だけ使い、
控えは書き換えない。
"""
from __future__ import annotations

import json
import os

from .. import accounts, secrets_fs
from .errors import RegisterRefused

REGISTERS_DIR_ENV = "THTH_REGISTERS_DIR"
KINDS = ("media", "store")
FIELDS = ("register", "project", "path", "kind")
ROLE_FIELD = "role"
COMMON = "common"
NAME_FILE = "register.json"                # 台帳の中の名乗り（`.thth/register.json`）
NAME_SCHEMA = "thth.register_name/1"
NAME_MAX = 4096
CONFIRMED_FILE = "confirmed.json"
CONFIRMED_SCHEMA = "thth.register_confirmed/1"


def registers_dir() -> str:
    env = os.environ.get(REGISTERS_DIR_ENV)
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(accounts.thth_root(), "registers")


def name_ok(name) -> bool:
    """名前の検査（末尾の改行も断る・`fullmatch`）。`accounts.name_is_safe` は `$` で末尾の改行を通すので重ねる。"""
    return accounts.name_is_safe(name) and bool(accounts.NAME_RE.fullmatch(name))


def check_name(name) -> str:
    if not name_ok(name):
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
    return [e[:-5] for e in entries if e.endswith(".json") and name_ok(e[:-5])]


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
    """THTH の置き場（台帳のフォルダを置いてはいけない所）: `$THTH_ROOT` の下のどこでも（口座の state・管理する repo・
    認めた記録・控え…。口座や台帳の leave が丸ごと消しうる）・控えの置き場・`~/.config/thth`（鍵の置き場）。"""
    return [accounts.thth_root(), os.path.join(accounts.thth_root(), "register_state"), registers_dir(),
            os.path.join(os.path.expanduser("~"), ".config", "thth")]


def check_outside_thth(path: str) -> str:
    """台帳のフォルダが THTH の置き場の中なら断る（消えるので・気づき 8・段 8 の検収の穴 1）。THTH の置き場を含む親の
    フォルダ（`/`・ホーム・`$THTH_ROOT` の先祖）も断る（そこを台帳と呼ぶと、THTH の置き場が台帳の中に入る）。"""
    if any(_inside(path, d) for d in thth_owned_dirs()):
        raise RegisterRefused("register_path_unusable", "inside_thth_state")
    if any(_inside(d, path) for d in thth_owned_dirs()) or os.path.realpath(path) in (
            os.path.realpath(os.sep), os.path.realpath(os.path.expanduser("~"))):
        raise RegisterRefused("register_path_unusable", "contains_thth_state")
    return path


WALK_MAX = 200_000


def check_walkable(root: str, limit: int | None = None) -> int:
    """台帳のフォルダを歩くのに上限を置く（段 8 の検収の穴 3: 控えの道が `/` に書き換えてあっても止まる）。
    ファイルの数（symlink は追わない）を返す。`/` かホームそのものなら `register_folder_too_wide`。`limit` を超えたら数えるのを
    やめて `limit + 1` を返す（断らない）。"""
    if os.path.realpath(root) == os.path.realpath(os.sep):
        raise RegisterRefused("register_folder_too_wide", "root")
    if os.path.islink(root) or not os.path.isdir(root):
        return 0
    home = os.path.realpath(os.path.expanduser("~"))
    if os.path.realpath(root) == home:
        raise RegisterRefused("register_folder_too_wide", "home")
    limit = WALK_MAX if limit is None else limit
    count = 0
    for _here, _dirs, files in os.walk(root, followlinks=False):
        count += len(files)
        if count > limit:
            # 実物の台帳は大きい（MMR は 63 万ファイル）。数えるのをやめるだけで、断らない（11/14 本線。
            # 断ると、大きな台帳の健康と外す命令が動かなくなる）。返すのは「上限より多い」を表す limit + 1。
            return limit + 1
    return count


# ------------------------------------------------------------------ 名乗りと別の checkout

# その 1 回の命令の間だけ使う道（名前 → フォルダ）。`register/cli._run` が命令の前と後に空にする。
_HERE: dict = {}


def looks_like_path(value) -> bool:
    """`<台帳>` がフォルダの道か（`/` を含む・`~` か `.` で始まる）。名前は `/` を含めない。"""
    return isinstance(value, str) and (os.sep in value or "/" in value or value.startswith(("~", ".")))


def declared_name(root: str):
    """台帳の中の名乗り（`.thth/register.json`）の名前。無ければ None。崩れていれば・symlink なら断る
    （名乗りが読めないのに「同じ台帳」と読み違えない）。"""
    base = os.path.join(root, ".thth")
    path = os.path.join(base, NAME_FILE)
    if not os.path.lexists(path):
        return None
    if os.path.islink(base) or os.path.islink(path) or not os.path.isfile(path):
        raise RegisterRefused("register_path_other", "declaration")
    try:
        with open(path, "rb") as f:
            raw = f.read(NAME_MAX + 1)
        value = json.loads(raw.decode("utf-8")) if len(raw) <= NAME_MAX else None
    except (OSError, UnicodeDecodeError, ValueError):
        value = None
    if (not isinstance(value, dict) or set(value) != {"schema", "register"} or value.get("schema") != NAME_SCHEMA
            or not isinstance(value.get("register"), str) or not name_ok(value["register"])):
        raise RegisterRefused("register_path_other", "declaration")
    return value["register"]


def _same_folder(a: str, b) -> bool:
    return isinstance(b, str) and os.path.realpath(a) == os.path.realpath(b)


def use_path(name: str, value) -> str:
    """`<名前> --path <フォルダ>`: 控えの道でない別の checkout を、この 1 回の命令の間だけ使う。控えの道そのものか、
    名乗りが同じ名前のフォルダだけ（名乗りが無い・違えば `register_path_other`）。THTH の置き場の中は今までどおり断る。

    名前ごとの状態（`register_state/<名前>/` の認めた記録・下書きの控え）は名前で引き、道ごとには分けない — 認めた
    記録は行の中身の指紋で引くので、別の checkout で中身が違えば認めたと読まれず、同じなら同じ同意で済むから。"""
    cfg = _load_raw(name)
    path = check_outside_thth(check_path(value))
    declared = declared_name(path)
    if declared is not None and declared != name:
        raise RegisterRefused("register_path_other", "other_name")
    if declared is None and not _same_folder(path, cfg["path"]):
        raise RegisterRefused("register_path_other", "undeclared")
    _HERE[name] = path
    return path


def name_at(value) -> str:
    """`<台帳>` にフォルダの道: 名乗りから控えの名前を引き、その道を使う。名乗りが無ければ、控えの道が同じ控え
    （1 つだけ）の名前。どちらも無ければ `register_path_other`（undeclared）。名乗りの名前の控えが無ければ `register_missing`。"""
    path = check_path(value)
    name = declared_name(path)
    if name is None:
        found = [n for n in list_names() if _same_folder(path, raw_path(n))]
        if len(found) != 1:
            raise RegisterRefused("register_path_other", "undeclared")
        name = found[0]
    use_path(name, path)
    return name


def clear_here() -> None:
    _HERE.clear()


def raw_path(name: str):
    """読めない控えからも `path` だけを拾う（消さない物の囲いに入れるため）。拾えなければ None。"""
    try:
        with open(path_for(name), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, RegisterRefused):
        return None
    value = data.get("path") if isinstance(data, dict) else None
    return value if isinstance(value, str) and os.path.isabs(value) else None


def validate(name: str, data) -> None:
    if (not isinstance(data, dict) or set(data) - {ROLE_FIELD} != set(FIELDS) or data.get("register") != name
            or (ROLE_FIELD in data and data[ROLE_FIELD] != COMMON)
            or data.get("kind") not in KINDS
            or not isinstance(data.get("project"), str) or not name_ok(data["project"])
            or not isinstance(data.get("path"), str) or not os.path.isabs(data["path"])):
        raise RegisterRefused("register_ledger_invalid")


def build(name: str, *, project: str, path: str, kind: str, common: bool = False) -> dict:
    data = {"register": name, "project": project, "path": path, "kind": kind}
    if common:
        data[ROLE_FIELD] = COMMON
    return data


def is_common(cfg: dict) -> bool:
    return cfg.get(ROLE_FIELD) == COMMON


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
    """控え（この命令が `--path` で別の checkout を指していれば、`path` はその道）。"""
    data = _load_raw(name)
    if name in _HERE:
        data["path"] = _HERE[name]
    return data


def _load_raw(name: str) -> dict:
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
    """控えの台帳のフォルダ（今もあるフォルダか確かめる）。台帳の中の名乗りが控えの名前と違えば断る。"""
    path = check_path(cfg["path"])
    declared = declared_name(path)
    if declared is not None and declared != cfg.get("register"):
        raise RegisterRefused("register_path_other", "other_name")
    return path


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


def media_of(project: str) -> list:
    """そのメディア（project）の台帳の控えの名前。**共通の台帳は入れない**（`project leave` が消さないように）。"""
    out = []
    for name in list_names():
        try:
            cfg = load(name)
        except RegisterRefused:
            continue
        if cfg["project"] == project and not is_common(cfg):
            out.append(name)
    return out


def common_names() -> list:
    """`role: common` の控えの名前（持ち主に 1 つのはず）。読めない控えは数えない。"""
    out = []
    for name in list_names():
        try:
            if is_common(load(name)):
                out.append(name)
        except RegisterRefused:
            continue
    return out
