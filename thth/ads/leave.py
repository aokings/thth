"""広告の口座の退出 `thth ads leave <名前> --by <名前>`（設計 広告の資格と遠くの道 2026-11-06 段 5）。

`thth account leave`（`thth/leave.py`）の最小版。広告の招待で資格の行を結んだので、消す道も要る（比べる軸 (c)）:

1. 止める: 退出の印 `state/_leave/<名前>.json`（phase `stopped`）を書く。以後 `leave_gate` が名前で引く所
   （`current`・`load_credentials`・`ads/ledger.require_active`）が全部この口座を止める。
2. Worker へ `account revoke`（thth.me の鍵と口座の secret を無効に）。届かなければ止めたまま断る（打ち直せる）。
3. 消す: `.token`（root の下のときだけ）・台帳 `adaccounts/<名前>.json`・`adaccount_state/<名前>/`・
   `state/<名前>/`（遠くの道の依頼の印）。無い物は数えない（何度打っても同じ結果）。
4. 変更ログ `account_removed`（run_id は退出の番号・1 回だけ）→ 印を `completed` に。
5. 招待で足したその口座だけの資格の行を消す（`invites.forget_credential`）。

Meta の側の鍵の失効は FB・IG と同じく本人の手（`unconfirmed_manual`・ビジネス連携で外す）。広告は止めない
（配信中の物があれば先に `thth ads stop`）。
"""
from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path

from .. import accounts, admin_log, jst, leave as leave_mod, leave_gate as gate, relay, server_files
from . import ledger


def _inside_root(path) -> Path | None:
    if not isinstance(path, str) or not path:
        return None
    root = Path(accounts.thth_root()).resolve()
    value = Path(os.path.abspath(path))
    return value if value.resolve().is_relative_to(root) else None


def _remove_file(path) -> int:
    if path is None:
        return 0
    try:
        info = path.lstat()
    except FileNotFoundError:
        return 0
    server_files.regular(info)  # symlink・特殊ファイルは消さずに断る
    path.unlink()
    return 1


def _remove_tree(path) -> int:
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return 0
    if path.is_symlink() or not path.is_dir():
        raise ValueError("unsafe_owned_directory")
    count = sum(len(files) for _d, _s, files in os.walk(path, followlinks=False))
    shutil.rmtree(path)
    return count


def _logged(name, operation_id) -> bool:
    entries, _ = admin_log.read(account=name, event="account_removed")
    return any(e.get("run_id") == operation_id for e in entries)


def run(name, *, by) -> dict:
    """退出を 1 回分進める（途中で落ちても同じ命令で続きから）。`leave.public` の形を返す。"""
    by = admin_log.actor(by)
    if not accounts.name_is_safe(name):
        raise ValueError("invalid_account")
    with server_files.directory(gate.location(), create=True, private=True) as fd:
        with server_files.lock_at(fd, name + ".operation.lock"):
            row = leave_mod.read(name)
            if row is None:
                if accounts.ledger_kind(name) != accounts.ADACCOUNT_KIND:
                    raise ledger.AdAccountError("adaccount_missing", f"広告の口座の台帳がありません: {name}")
                full = ledger.load(name)
                cfg = {"account": name, "media": ledger.MEDIA, "project": full["project"], "token": full["token"]}
                with gate.lease(name, exclusive=True), gate.scope(name):
                    row = dict(schema_version=1, account=name, operation_id=secrets.token_urlsafe(32), phase="stopped",
                               stopped_at=jst.iso(), stopped_by=admin_log.clean(by), cfg=cfg,
                               cfg_sha256=leave_mod._hash(cfg), remote="pending", deleted={}, revoked=[])
                    leave_mod._save(row)
            if row["phase"] not in ("stopped", "completed"):
                raise ValueError("account_binding_invalid")  # 口座の退出の途中（`thth account leave` で続ける）
            if row["phase"] == "stopped":
                if row["cfg"].get("media") != ledger.MEDIA:
                    raise ValueError("account_binding_invalid")  # 口座の退出は `thth account leave`
                with gate.lease(name, exclusive=True), gate.recovery(name):
                    with admin_log.transaction():
                        pass  # 変更ログに書けない置き場なら、網に出る前に断る（`leave` と同じ筋）
                    result = relay.signed_request("account", name, "revoke", {})
                    if result != {"status": "revoked"}:
                        raise ValueError("worker_revoke_unconfirmed")
                    with gate.credentials(exclusive=True), admin_log.transaction():
                        deleted = dict(row["deleted"])
                        root = Path(accounts.thth_root()).resolve()
                        for category, count in (
                                ("token", _remove_file(_inside_root(row["cfg"].get("token")))),
                                ("ledger", _remove_file(Path(ledger.path_for(name)))),
                                ("state", _remove_tree(ledger.state_dir_for(name))
                                 + _remove_tree(root / "state" / name))):
                            deleted[category] = deleted.get(category, 0) + count
                        if not _logged(name, row["operation_id"]):
                            admin_log.repair_append_boundary()
                            admin_log.append("account_removed", name, {"media": ledger.MEDIA}, by=by,
                                             diff={"deleted_categories": [[], sorted(deleted)],
                                                   "deleted_counts": [[], [deleted[k] for k in sorted(deleted)]],
                                                   "remote": [None, "unconfirmed_manual"]},
                                             run_id=row["operation_id"])
                    row = {k: row[k] for k in ("schema_version", "account", "operation_id", "stopped_at", "stopped_by")}
                    row.update(phase="completed", completed_at=jst.iso(), remote="unconfirmed_manual",
                               deleted=deleted, preserved=[])
                    leave_mod._save(row)
            from .. import invites
            invites.forget_credential(name, by=by)
            return leave_mod.public(row)
