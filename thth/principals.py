"""主体（principal）の台帳 `principals/<name>.json` と、その状態（設計 4.0 S1 §3.2・§4・§14）。

**主体** は認証の主体（はてな ID 1 つと、その token）。レーンごとの口座
（`accounts/kopicha-hatenablog.json`）は「主体 ＋ ブログ 1 つ」を指すだけで、
止め・burst・ブックマークの上限・ロック・token は主体に 1 つ置く（設計 §3.1 の案 A）。

置き場は 2 つ（設計 §14 の照合で `state/_principals/` を改めた）:

- 台帳: `$THTH_ROOT/principals/<name>.json`（口座の台帳の置き場の隣・`THTH_PRINCIPALS_DIR`
  で差し替え可・app repo の中には置かない）。0600。
- 状態: `$THTH_ROOT/principal_state/<name>/`（**`state/` の外**——口座の名前の規則では
  `_principals` という口座が作れて `state/<account>/` とぶつかるため）。中身は
  `guard/stopped.json`（止めの印）・`lock`（ロック）・`token.lock`（token の書き込みの
  ロック）。**公開の記録（sent）は主体には置かない**——数は口座の `sent/` を横断して
  数える（`guard`）。写しを作らない。

**口座の側の道具は今のまま動く**（`load_account` と `state/<account>/` の形を変えない）。
主体が効くのは `media: hatenablog` の口座だけで、SNS の口座に `principal` が書いて
あっても読まない（T-1c）。
"""
from __future__ import annotations

import contextlib
import datetime
import json
import os
import re
from pathlib import Path

from . import accounts, jst, server_files

MEDIA = "hatenablog"
PRINCIPALS_DIR_ENV = "THTH_PRINCIPALS_DIR"
KINDS = ("hatena",)
AUTHS = ("oauth", "wsse")
REQUIRED_FIELDS = ("principal", "kind", "hatena_id", "token", "blogs")
HATENA_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
# ブログの id はブログのドメイン（小文字のホスト名）。独自ドメインならそれ。
BLOG_ID_RE = re.compile(r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                        r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
SYNTAXES = ("markdown", "hatena", "html")
# **主体の上限の既定**（設計 §4.2）。はてなに公式の回数の上限は無い——THTH の側の
# 安全装置で、持ち主が台帳で上げ下げする（`thth principal set`）。
LIMIT_DEFAULTS = {
    "daily_max_posts": 6,       # 全ブログの entry_create＋entry_update
    "daily_max_retracts": 3,    # 全ブログの entry_delete
    "daily_max_bookmarks": 3,   # bookmark_add＋bookmark_delete
    "burst": {"count": 3, "minutes": 30},  # entry と bookmark の合計
}
# ブログごとの上限（任意・無ければ数えない＝口座の上限に任せる）。
BLOG_LIMIT_KEYS = ("daily_max_posts", "daily_max_retracts")
STOP_FILE = "stopped.json"
STOP_REASONS = frozenset(("burst", "owner"))


class PrincipalError(accounts.AccountError):
    """主体の台帳・口座との突き合わせの断り。`code` は静的な符丁。"""

    def __init__(self, code, message):
        super().__init__(f"{message}（{code}）")
        self.code = code


# --------------------------------------------------------------------------
# 置き場
# --------------------------------------------------------------------------

def principals_dir() -> str:
    """台帳の置き場。`$THTH_PRINCIPALS_DIR` → 口座の台帳の置き場の隣の `principals/`。

    口座の台帳が app repo の中（互換）を読んでいるときも、主体の台帳は repo の中に
    置かない（`$THTH_ROOT/principals/`）。
    """
    env = os.environ.get(PRINCIPALS_DIR_ENV)
    if env:
        return os.path.abspath(os.path.expanduser(env))
    info = accounts.accounts_dir_info()
    if info["source"] == accounts.SOURCE_ENV:
        return os.path.join(os.path.dirname(info["path"].rstrip(os.sep)), "principals")
    return os.path.join(accounts.thth_root(), "principals")


def path_for(name: str) -> str:
    _validate_name(name)
    return os.path.join(principals_dir(), f"{name}.json")


def state_dir_for(name: str) -> str:
    """主体の状態の置き場 `$THTH_ROOT/principal_state/<name>/`（**`state/` の外**・§14）。"""
    _validate_name(name)
    return os.path.join(accounts.thth_root(), "principal_state", name)


def lock_path_for(name: str) -> str:
    """主体のロック（`core._account_locks`・`server_files.account_locks` が repo のあとに取る）。"""
    return os.path.join(state_dir_for(name), "lock")


def token_lock_path_for(name: str) -> str:
    return os.path.join(state_dir_for(name), "token.lock")


def ensure_state_dir(name: str) -> str:
    """状態の置き場を 0700 で用意する（既にあれば触らない）。"""
    path = state_dir_for(name)
    if not os.path.isdir(path):
        os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _validate_name(name) -> None:
    if not accounts.name_is_safe(name):
        raise PrincipalError("invalid_principal_name",
                             f"主体の名前に使えない字が入っています: {name!r}")


# --------------------------------------------------------------------------
# 台帳
# --------------------------------------------------------------------------

def valid_limit(key: str, value) -> bool:
    """主体・ブログの上限の 1 項目として受け取れる形か（口座と同じ検査・鍵名を共有）。"""
    if key == "daily_max_bookmarks":
        return accounts.valid_guard_value("daily_max_posts", value)
    if key in ("daily_max_posts", "daily_max_retracts", "burst"):
        return accounts.valid_guard_value(key, value)
    return False


def _expand(value):
    return accounts._expand(value) if isinstance(value, str) else value


def _check_blogs(name, blogs) -> None:
    if not isinstance(blogs, list) or not blogs:
        raise PrincipalError("blogs_invalid", f"{name}: 主体の台帳の blogs は空でない配列です")
    seen = set()
    for row in blogs:
        if not isinstance(row, dict):
            raise PrincipalError("blogs_invalid", f"{name}: blogs の 1 行が object ではありません")
        blog_id = row.get("blog_id")
        if not isinstance(blog_id, str) or not BLOG_ID_RE.fullmatch(blog_id):
            raise PrincipalError("blog_id_invalid",
                                 f"{name}: blogs の blog_id は小文字のホスト名です")
        if blog_id in seen:
            raise PrincipalError("blog_duplicate", f"{name}: blogs に同じ blog_id が 2 つあります: {blog_id}")
        seen.add(blog_id)
        lanes = row.get("lanes")
        if (not isinstance(lanes, list) or not lanes
                or any(not isinstance(lane, str) or not lane.strip() for lane in lanes)):
            raise PrincipalError("lanes_empty",
                                 f"{name}: {blog_id} の lanes は空でない project 名の配列です")
        if "syntax" in row and row["syntax"] not in SYNTAXES:
            raise PrincipalError("syntax_invalid", f"{name}: {blog_id} の syntax は {'・'.join(SYNTAXES)}")
        for key in BLOG_LIMIT_KEYS:
            if key in row and not valid_limit(key, row[key]):
                raise PrincipalError("invalid_guard_limits",
                                     f"{name}: {blog_id} の {key} が受け取れません（0 以上の整数）")
        if "fotolife_folder" in row:
            from . import hatena_fotolife
            if not hatena_fotolife.valid_folder(row["fotolife_folder"]):
                raise PrincipalError("fotolife_folder_invalid",
                                     f"{name}: {blog_id} の fotolife_folder は 1〜{hatena_fotolife.FOLDER_MAX} 字"
                                     "（`/`・制御文字・前後の空白は不可）です")


def _check_fotolife(name, value) -> None:
    """主体の台帳の `fotolife`（任意・設計 4.0 S3 §9）。欄ごとの範囲は `hatena_fotolife.LIMIT_RANGES`。"""
    from . import hatena_fotolife
    if not isinstance(value, dict):
        raise PrincipalError("fotolife_invalid", f"{name}: fotolife は object です")
    unknown = set(value) - set(hatena_fotolife.LIMIT_RANGES)
    if unknown:
        raise PrincipalError("fotolife_invalid", f"{name}: fotolife に知らない欄があります: {sorted(unknown)}")
    for key, item in value.items():
        if not hatena_fotolife.valid_limit(key, item):
            low, high = hatena_fotolife.LIMIT_RANGES[key]
            raise PrincipalError("fotolife_invalid", f"{name}: fotolife.{key} は {low}〜{high} の整数です")


def validate(name: str, data) -> None:
    """主体の台帳の形を見る。外れれば `PrincipalError`（符丁つき）。値は文面に出さない。"""
    if not isinstance(data, dict):
        raise PrincipalError("principal_invalid", f"{name}: 主体の台帳が object ではありません")
    missing = [k for k in REQUIRED_FIELDS if k not in data]
    if missing:
        raise PrincipalError("principal_field_missing", f"{name}: 主体の台帳に項目が足りません: {missing}")
    if data["principal"] != name:
        raise PrincipalError("principal_name_mismatch",
                             f"{name}: 主体の台帳の principal がファイル名と違います")
    if data["kind"] not in KINDS:
        raise PrincipalError("principal_kind_unknown", f"{name}: 主体の kind は {'・'.join(KINDS)} だけです")
    if not isinstance(data["hatena_id"], str) or not HATENA_ID_RE.fullmatch(data["hatena_id"]):
        raise PrincipalError("hatena_id_invalid", f"{name}: hatena_id は英数と `-`・`_` の 1〜32 字です")
    for key in ("token", "consumer"):
        if key in data and (not isinstance(data[key], str) or not data[key].strip()):
            raise PrincipalError("principal_invalid", f"{name}: {key} はファイルのパスです")
    if data.get("auth", "oauth") not in AUTHS:
        raise PrincipalError("principal_invalid", f"{name}: auth は {'・'.join(AUTHS)} のどれかです")
    _check_blogs(name, data["blogs"])
    bookmark = data.get("bookmark", {"lanes": []})
    if (not isinstance(bookmark, dict) or set(bookmark) - {"lanes"}
            or not isinstance(bookmark.get("lanes", []), list)
            or any(not isinstance(lane, str) or not lane.strip() for lane in bookmark.get("lanes", []))):
        raise PrincipalError("principal_invalid", f"{name}: bookmark は {{\"lanes\": [project …]}} の形です")
    for key in LIMIT_DEFAULTS:
        if key in data and not valid_limit(key, data[key]):
            raise PrincipalError("invalid_guard_limits",
                                 f"{name}: 主体の {key} が受け取れません（0 以上の整数。"
                                 f"burst は {{\"count\": 1〜, \"minutes\": 1〜1440}}）")
    if "fotolife" in data:
        _check_fotolife(name, data["fotolife"])
    contact = data.get("user_agent_contact")
    if contact is not None and (not isinstance(contact, str)
                                or not contact.startswith(("https://", "http://")) or len(contact) > 2048
                                or any(ord(ch) <= 32 or ord(ch) == 127 for ch in contact)):
        raise PrincipalError("principal_invalid", f"{name}: user_agent_contact は URL です")


def load_principal(name: str) -> dict:
    """`principals/<name>.json` を読んで検査する。`token`・`consumer` は展開したコピーを返す。

    無いときは `principal_missing`、読めない・壊れているときは `principal_unreadable`。
    """
    _validate_name(name)
    path = path_for(name)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as e:
        raise PrincipalError("principal_missing", f"主体の台帳がありません: {name}（{path}）") from e
    except (OSError, ValueError) as e:
        raise PrincipalError("principal_unreadable", f"主体の台帳が読めません: {name}") from e
    validate(name, data)
    out = dict(data)
    for key in ("token", "consumer"):
        if key in out:
            out[key] = _expand(out[key])
    return out


def limits(principal_cfg: dict) -> dict:
    """主体の上限（台帳に無い項目は既定）。"""
    out = {}
    for key, default in LIMIT_DEFAULTS.items():
        value = (principal_cfg or {}).get(key)
        if value is None or not valid_limit(key, value):
            value = default
        out[key] = dict(value) if isinstance(value, dict) else value
    return out


def blog_entry(principal_cfg: dict, blog_id) -> dict | None:
    key = str(blog_id or "").strip().lower()
    for row in (principal_cfg or {}).get("blogs") or []:
        if row.get("blog_id") == key:
            return row
    return None


def uses_principal(account_cfg) -> bool:
    """主体を通す口座か（`media: hatenablog` のときだけ。SNS の口座の `principal` は読まない）。"""
    return isinstance(account_cfg, dict) and account_cfg.get("media") == MEDIA


def _same_path(a, b) -> bool:
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return os.path.normpath(_expand(a)) == os.path.normpath(_expand(b))


def check_account(account_cfg: dict, name: str, principal_cfg: dict | None = None) -> dict:
    """はてなの口座を主体の台帳と突き合わせる（設計 §3.3 の 1〜5）。主体の台帳を返す。

    1 つでも外れれば `PrincipalError`（符丁は `principal_missing`・`blog_unknown`・
    `blog_not_allowed_for_lane`・`principal_token_mismatch`・`principal_handle_mismatch`）。
    `load_account` の末尾と、ロックの中の数え直し（`guard`）の両方が呼ぶ。
    """
    principal = account_cfg.get("principal")
    blog_id = account_cfg.get("blog_id")
    if not isinstance(principal, str) or not accounts.name_is_safe(principal):
        raise PrincipalError("principal_missing", f"{name}: 台帳に principal がありません")
    if not isinstance(blog_id, str) or not blog_id.strip():
        raise PrincipalError("blog_unknown", f"{name}: 台帳に blog_id がありません")
    # 1. 主体がある・kind: hatena
    if principal_cfg is None:
        try:
            principal_cfg = load_principal(principal)
        except PrincipalError as e:
            if e.code == "principal_missing":
                raise PrincipalError("principal_missing", f"{name}: 主体 {principal} の台帳がありません") from e
            raise
    if principal_cfg.get("kind") != "hatena":
        raise PrincipalError("principal_missing", f"{name}: 主体 {principal} ははてなの主体ではありません")
    # 2. blog_id が主体の blogs[] にある
    blog = blog_entry(principal_cfg, blog_id)
    if blog is None:
        raise PrincipalError("blog_unknown", f"{name}: blog_id {blog_id} は主体 {principal} のブログにありません")
    # 3. 口座の project がそのブログの lanes にある
    if account_cfg.get("project") not in blog.get("lanes", []):
        raise PrincipalError("blog_not_allowed_for_lane",
                             f"{name}: {blog['blog_id']} はレーン {account_cfg.get('project')} に許されていません")
    # 4. 口座の token が主体の token と同じパス（正本は主体・書くのは主体の口だけ）
    if not _same_path(account_cfg.get("token"), principal_cfg.get("token")):
        raise PrincipalError("principal_token_mismatch",
                             f"{name}: 台帳の token が主体 {principal} の token と同じパスではありません")
    # 5. handle が主体の hatena_id と同じ（見せるときの名乗りを 1 つに）
    if str(account_cfg.get("handle") or "").strip().lstrip("@") != principal_cfg.get("hatena_id"):
        raise PrincipalError("principal_handle_mismatch",
                             f"{name}: 台帳の handle が主体 {principal} の hatena_id と違います")
    return principal_cfg


def accounts_of(principal_name: str) -> list:
    """主体を使う口座の `(口座名, 台帳の生の dict)`（名前の順）。

    台帳は**生の JSON を読む**（`load_account` を通さない）——止めた口座・退出中の口座の
    `sent/` も数に入れるため。読めない台帳は飛ばす（その口座は `load_account` で断られる
    ので、今から出すことはできない）。
    """
    out = []
    d = accounts.accounts_dir()
    for name in accounts.list_account_names():
        if not accounts.name_is_safe(name):
            continue
        try:
            with open(os.path.join(d, f"{name}.json"), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if uses_principal(data) and data.get("principal") == principal_name:
            out.append((name, data))
    return out


# --------------------------------------------------------------------------
# 止める・戻す（`guard.stopped`・`stop`・`resume` と同じ規律。置き場だけ主体の側）
# --------------------------------------------------------------------------

def _guard_directory(name) -> Path:
    return Path(state_dir_for(name)) / "guard"


def stopped(name) -> dict | None:
    """止まっていれば `{"reason", "stopped_at", ...}`、動いていれば None。読めなければ止まっている側。"""
    try:
        with server_files.directory(_guard_directory(name), private=True) as fd:
            raw = server_files.read_at(fd, STOP_FILE, private=True)
    except FileNotFoundError:
        return None
    except server_files.UnsafeFile as exc:
        if str(exc) in ("unsafe_server_directory", "unsafe_server_owner"):
            raise
        return {"reason": "guard_state_unreadable"}
    except (OSError, ValueError):
        return {"reason": "guard_state_unreadable"}
    try:
        value = json.loads(raw)
    except ValueError:
        return {"reason": "guard_state_unreadable"}
    if type(value) is not dict or value.get("reason") not in STOP_REASONS:
        return {"reason": "guard_state_unreadable"}
    return value


def stop_place_usable(name) -> bool:
    """止めの印を置ける置き場か（`guard.stop_place_usable` の主体版）。"""
    try:
        state = Path(state_dir_for(name))
        if not state.exists():
            return True
        with server_files.directory(state):
            pass
        return True
    except (server_files.UnsafeFile, OSError, ValueError):
        return False


def _log_subject_cfg() -> dict:
    return {"media": MEDIA}


def stop(name, reason, *, now=None, actor=None, via=None, credential=None) -> dict:
    """主体を止める（既に止まっていれば上書きしない）。その主体のすべてのブログと
    ブックマークの書き込みが止まる（設計 §4.1）。"""
    if reason not in STOP_REASONS:
        raise ValueError("invalid_stop_reason")
    value = {"reason": reason, "stopped_at": jst.iso(now)}
    for key, item in (("actor", actor), ("via", via), ("credential", credential)):
        if isinstance(item, str) and item:
            value[key] = item
    with server_files.directory(_guard_directory(name), create=True, private=True) as fd:
        try:
            server_files.replace_at(fd, STOP_FILE, server_files.encode(value), new=True, private=True)
        except server_files.UnsafeFile:
            pass  # 既に止まっている（先に止めた印を消さない）
    try:
        from . import admin_log
        admin_log.append("principal_stopped", name, _log_subject_cfg(),
                         by=actor if isinstance(actor, str) and actor else "thth-guard",
                         via=via if via in ("cli", "mcp", "http", "api") else "cli",
                         diff={"stopped": [False, True], "reason": [None, reason]})
    except Exception:
        pass  # 変更ログに残せなくても止めることは止める（止めた印が正本）
    return value


def resume(name, *, by, via="cli") -> bool:
    """止めたのを戻す。止まっていなければ False。

    口は**運営者の CLI（`thth principal resume`）だけ**（設計 §4.1・T-2f）。MCP にも
    /activity にも出さない——暴走して止まった LLM が自分で戻せないように。
    """
    from . import admin_log
    admin_log.actor(by)
    try:
        with server_files.directory(_guard_directory(name), private=True) as fd:
            try:
                os.unlink(STOP_FILE, dir_fd=fd)
            except FileNotFoundError:
                return False
            os.fsync(fd)
    except FileNotFoundError:
        return False
    try:
        admin_log.append("principal_resumed", name, _log_subject_cfg(), by=by,
                         via=via if via == "cli" else "cli", diff={"stopped": [True, False]})
    except Exception:
        pass
    return True


# --------------------------------------------------------------------------
# token（設計 §4.4）。書くのは `thth principal auth`（S2）と期限つきなら maintain の更新だけ。
# どちらも token.lock を flock で取って os.replace で原子的に置く。レーンの口座の道は読むだけ。
# --------------------------------------------------------------------------

@contextlib.contextmanager
def token_lock(name):
    """`principal_state/<name>/token.lock` を取る（待たない。取れなければ `LockBusy`）。"""
    import fcntl
    from .lock import LockBusy
    ensure_state_dir(name)
    fd = os.open(token_lock_path_for(name), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise LockBusy("principal_token_busy") from None
        yield
    finally:
        os.close(fd)


def write_token(name, data: dict) -> str:
    """主体の token を書く（**token.lock の中だけ**・原子的な置き換え・0600）。書いたパスを返す。

    値は呼び手から受けたまま書く。表示・ログには出さない。
    """
    from . import secrets_fs
    if not isinstance(data, dict):
        raise ValueError("invalid_token")
    principal_cfg = load_principal(name)
    path = principal_cfg["token"]
    with token_lock(name):
        secrets_fs.atomic_write_json(path, data)
    return path


def write_token_locked(name, data: dict) -> str:
    """`write_token` の、**呼び手が既に `token_lock(name)` を持っているとき**の版
    （`thth principal auth` が段 0 から段 6 まで token.lock を持ち続けるため・S2 §2.4）。"""
    from . import secrets_fs
    if not isinstance(data, dict):
        raise ValueError("invalid_token")
    path = load_principal(name)["token"]
    secrets_fs.atomic_write_json(path, data)
    return path


def forget_token(name, *, consumer=False, locked=False) -> dict:
    """token（`consumer=True` なら consumer も）を消す。消したかどうかを `{"token": bool, "consumer": bool}` で。

    `locked=True` は呼び手が `token_lock(name)` を持っているとき。値は読まない。
    """
    principal_cfg = load_principal(name)

    def unlink(path) -> bool:
        if not isinstance(path, str) or not path:
            return False
        try:
            os.unlink(path)
            return True
        except FileNotFoundError:
            return False

    def run():
        return {"token": unlink(principal_cfg["token"]),
                "consumer": unlink(principal_cfg.get("consumer")) if consumer else False}

    if locked:
        return run()
    with token_lock(name):
        return run()


# --------------------------------------------------------------------------
# 予約の数（`guard.check(kind="schedule")` が主体の段で使う）
# --------------------------------------------------------------------------

def scheduled_same_day(principal_name: str, day: datetime.date) -> int:
    """主体を使う全口座の queue のうち、`day`（JST）に刻んである approved（未公開）の数。

    `server_writes.schedule` の `same_day` と同じ数え方を、主体の全口座に広げたもの
    （設計 §4.2）。読めない queue・原稿は数えない。queue は repo に 1 つで、同じ repo の
    別の口座（SNS の口座・同じレーンの別のブログ）の原稿も並ぶので、front matter の
    `account` がその口座のものだけを数える（同じ原稿を 2 度数えない）。
    """
    from . import queuefile
    total = 0
    for name, cfg in accounts_of(principal_name):
        repo = accounts.resolved_repo_dir({"repo_dir": _expand(cfg.get("repo_dir"))})
        queue_dir = cfg.get("queue_dir")
        if not repo or not isinstance(queue_dir, str):
            continue
        directory = os.path.join(repo, queue_dir)
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for entry in names:
            if not entry.endswith(".md"):
                continue
            try:
                fm = queuefile.parse(os.path.join(directory, entry)).front_matter
            except (OSError, ValueError):
                continue
            if fm.get("account") != name or fm.get("status") != "approved" or fm.get("post_id"):
                continue
            when = jst.parse(fm.get("publish_at"))
            if when is not None and jst.to_jst(when).date() == day:
                total += 1
    return total
