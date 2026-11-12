"""店を外す（`thth shop remove <店>` と `thth leave` の店の分・設計 §7・監査 5.15.0 直したほうがよい 1）。

消すもの（この順）: 鍵のファイル → 状態 `store_state/<店>/`（写し）→ 台帳 `stores/<店>.json`。

- 鍵は**既定の置き場**（`~/.config/thth/store-<店>.token`）にあるときだけ消す。台帳を手で書き替えて別のパスを
  指していたら、そのファイルは消さずに残し、その旨を返す（店の鍵でないファイルを消さない）。
- symlink・普通のファイルでない物は消さずに断る（`store_remove_unsafe`）。
- 無い物は数えない。何度打っても同じ結果（台帳が無くても、残った写しと鍵は消せる）。
- 網には出ない（BASE の PAT の取り消しは BASE Developers で店の持ち主がする）。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat

from .. import accounts
from . import ledger
from .wire import StoreRefused

CATEGORIES = ("token", "state", "ledger")


def _kind(path: str) -> str | None:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(info.st_mode):
        return "unsafe"
    if stat.S_ISDIR(info.st_mode):
        return "dir"
    if stat.S_ISREG(info.st_mode):
        return "file"
    return "unsafe"


def plan(name: str) -> dict:
    """消す物の一覧（何も消さない）。`{"store", "targets": [{category, path}], "kept": [{category, path, why}]}`。"""
    ledger.check_name(name)
    default = accounts._expand(ledger.default_token(name))
    try:
        cfg = ledger.load(name)
        token = cfg["token"]
    except StoreRefused as exc:
        # 台帳が無い・壊れていても外せるように、鍵は既定の置き場だけを見る。
        if exc.code not in ("store_missing", "store_ledger_invalid"):
            raise
        token = default
    targets, kept = [], []
    if os.path.abspath(token) != os.path.abspath(default):
        if _kind(token) is not None:
            kept.append({"category": "token", "path": token, "why": "not_default_place"})
        token = default
    for category, path, want in (("token", token, "file"), ("state", ledger.state_dir_for(name), "dir"),
                                 ("ledger", ledger.path_for(name), "file")):
        found = _kind(path)
        if found is None:
            continue
        if found != want:
            raise StoreRefused("store_remove_unsafe", category)
        targets.append({"category": category, "path": path})
    return {"store": name, "targets": targets, "kept": kept}


def digest(found: dict) -> str:
    joined = "\x1f".join(["shopread_remove/1", found["store"]]
                         + [f"{t['category']}={t['path']}" for t in found["targets"]]
                         + [f"kept:{k['category']}={k['path']}" for k in found["kept"]])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def execute(found: dict) -> list:
    """`plan` の順に消す。消した分類の並びを返す。"""
    done = []
    for target in found["targets"]:
        path, category = target["path"], target["category"]
        kind = _kind(path)
        if kind is None:
            continue
        if category == "state":
            if kind != "dir":
                raise StoreRefused("store_remove_unsafe", category)
            shutil.rmtree(path)
        else:
            if kind != "file":
                raise StoreRefused("store_remove_unsafe", category)
            os.unlink(path)
        done.append(category)
    return done


def stores_of_project(project: str) -> list:
    out = []
    for name in ledger.list_names():
        try:
            if ledger.load(name)["project"] == project:
                out.append(name)
        except StoreRefused:
            continue  # 読めない台帳は project が分からないので、退出では消さない（`thth shop remove` で外す）
    return out


def purge_for_leave(account: str, project, *, by: str) -> list:
    """退出（`thth leave`）で、その project の**最後の口座**が抜けるとき、その project の店を外す。

    ほかの口座が残るなら外さない（店は project のもので、残る口座が下書きに使う）。読めない口座の台帳は
    「残っている」と数える（消しすぎない側・`map_world.purge_for_leave` と同じ筋）。何度呼んでも同じ結果。
    外した店の名前の並びを返す。
    """
    if not accounts.name_is_safe(project or ""):
        return []
    names = stores_of_project(project)
    if not names:
        return []
    for other in accounts.list_account_names():
        if other == account:
            continue
        try:
            if accounts.load_account(other).get("project") == project:
                return []
        except accounts.AccountError:
            return []
    from .. import admin_log
    from . import ACTION_PREFIX
    removed = []
    for name in names:
        found = plan(name)
        done = execute(found)
        admin_log.append(ACTION_PREFIX + "removed", name, {"media": "base"}, by=by,
                         diff={"deleted": [[], done], "via": [None, "leave"],
                               "kept": [[], [k["category"] for k in found["kept"]]]})
        removed.append(name)
    return removed
