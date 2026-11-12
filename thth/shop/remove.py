"""店を外す（`thth shop remove <店>` と `thth leave` の店の分・設計 §7・監査 5.15.0 直したほうがよい 1）。

消すもの（この順）: 鍵のファイル → 短い鍵のファイル（Shopify・`~/.config/thth/store-<店>.access`）→
状態 `store_state/<店>/`（写しと鎖）→ 台帳 `stores/<店>.json`。

- 鍵は**既定の置き場**（`~/.config/thth/store-<店>.token`）にあるときだけ消す。台帳を手で書き替えて別のパスを
  指していたら、そのファイルは消さずに残し、その旨を返す（店の鍵でないファイルを消さない）。
- symlink・普通のファイルでない物は消さずに断る（`store_remove_unsafe`）。
- 無い物は数えない。何度打っても同じ結果（台帳が無くても、残った写しと鍵は消せる）。
- 短い鍵も既定の置き場のものだけ消す（symlink は断る）。無い物は数えないので、BASE の店の digest は変わらない。
- 二段目は鎖の中で一覧を取り直す。鎖を持っていた読みが書いた短い鍵・状態の置き場は、一覧に無くても消す（`execute`）。
- 網には出ない（BASE の PAT の取り消しは BASE Developers・Shopify の鍵は Dev Dashboard で店の持ち主がする）。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat

from .. import accounts
from . import ledger
from .wire import StoreRefused

CATEGORIES = ("token", "access", "state", "ledger")


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
    access = accounts._expand(ledger.default_access(name))
    for category, path, want in (("token", token, "file"), ("access", access, "file"),
                                 ("state", ledger.state_dir_for(name), "dir"),
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


# 機械が既定の置き場に書く物（人が中身を選ばない）。鎖の中で一覧を取り直したとき、確かめた一覧に無くても消す。
MACHINE_MADE = ("access", "state")


def execute(found: dict) -> list:
    """`plan` の順に消す。消した分類の並びを返す。短い鍵の鎖を持って消す（取り直しの途中で消さない・`access.py`）。

    鎖の中で `plan` を取り直す（監査 5.17.0 残っていること 1）。一覧を作ったのは鎖の前なので、そのあと鎖を持っていた
    読みが短い鍵を書いていると、一覧に無い短い鍵が残る。取り直した一覧で:
      - 機械が既定の置き場に書く物（`MACHINE_MADE`）は、確かめた一覧に無くても消す（店を外すと決めた人が残したい物でない）。
      - 人が確かめた物（鍵・台帳・残す物）が増えていたら、何も消さずに `store_remove_unconfirmed`（今の digest）。
      - symlink・普通でない物は、取り直しの `plan` が `store_remove_unsafe` で断る（同じ決まり）。
    鎖はいつも取る（状態の置き場が無ければ作る）。無かった置き場を鎖のために作ったときは、消しても数えない。
    """
    from . import access
    name = found["store"]
    directory = ledger.state_dir_for(name)
    had_state = _kind(directory) is not None
    confirmed = {(t["category"], t["path"]) for t in found["targets"]}
    kept = {(k["category"], k["path"]) for k in found["kept"]}
    try:
        with access.locked(name):
            again = plan(name)
            grown = [t for t in again["targets"]
                     if (t["category"], t["path"]) not in confirmed and t["category"] not in MACHINE_MADE]
            done = None
            if not grown and all((k["category"], k["path"]) in kept for k in again["kept"]):
                done = _execute(again)
    except BaseException:
        if not had_state:
            _drop_lock_only(directory)
        raise
    if done is None:
        if not had_state:
            _drop_lock_only(directory)
        # 返す digest は、鎖のために作った置き場を片づけたあとの一覧（一段目を打ち直したときと同じ）。
        raise StoreRefused("store_remove_unconfirmed", digest(plan(name)))
    if not had_state and "state" in done and ("state", directory) not in confirmed:
        done.remove("state")                       # 鎖のために作った置き場（ほかに何も無ければ数えない）
    return done


def _drop_lock_only(directory: str) -> None:
    """鎖のために作った状態の置き場を、鎖のファイルしか無ければ消す（断ったときに置き場を残さない）。"""
    from . import access
    try:
        if _kind(directory) == "dir" and os.listdir(directory) in ([], [access.LOCK_FILE]):
            shutil.rmtree(directory)
    except OSError:
        pass


def _execute(found: dict) -> list:
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
        kind = ledger.load(name)["kind"]
        found = plan(name)
        done = execute(found)
        admin_log.append(ACTION_PREFIX + "removed", name, {"media": kind}, by=by,
                         diff={"deleted": [[], done], "via": [None, "leave"],
                               "kept": [[], [k["category"] for k in found["kept"]]]})
        removed.append(name)
    return removed
