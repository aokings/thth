"""広場の「読んだ」の記録（設計 3.8.0 §B）——**id と時刻だけ**。

読む瞬間を道具が作る（observe の 0 段の 1 件・`plaza show`）。一度読んだ書き込みを
翌日も同じ 1 件として出すと、読む瞬間が「いつもの 1 行」に変わって読まれなくなる。
そこで何を読んだかを道具が控え、**読んだものは再び出さない**。

規律:

  (a) 置き場は VM の私有（`$THTH_ROOT/state/_plaza_reads/`・0600・0700）。骨は
      報告の口・広場と共通（`thth/private_store.py`）。**持ち主（project）ごとに
      1 ファイル**、その中を account ごとに分ける（project を持たない account は
      account 名の 1 ファイル）。他の持ち主のファイルは読まない。
  (b) **本文・題を残さない**。残すのは plaza_id と読んだ時刻、observe が出した日
      （JST）と id だけ。
  (c) 読めなければ「読んでいない」扱いにしない——呼ぶ側が静的な理由で言えないと言う
      （`plaza_store_unavailable`）。
"""
from __future__ import annotations

import json
import os
import re

from . import accounts, jst, private_store

SCHEMA_VERSION = 1
DIRECTORY = "_plaza_reads"
# 1 account が控える「読んだ」の上限（古いものから捨てる）。observe の 1 件は 1 日 1 件、
# show は人とセッションが開いた分だけなので、1 年分でも収まる大きさ。
READ_MAX = 2000
# observe が出した日の控え（1 日 1 件の判定に使う）。
PICKED_MAX = 60
MAX_FILE_BYTES = 512 * 1024
_KEY = re.compile(r"[pa]-[A-Za-z0-9._-]+\Z")


def _store():
    from . import plaza
    return private_store.Store(
        DIRECTORY, id_key="key", id_pattern=_KEY, valid=lambda _record: False,
        error=plaza.PlazaError, unavailable="plaza_store_unavailable",
        not_found="plaza_not_found", log_unavailable="plaza_log_unavailable",
        max_bytes=MAX_FILE_BYTES, temporary_prefix=".reads-")


def key_for(account, project):
    """持ち主のファイルの名前（project があれば project・無ければ account）。"""
    if isinstance(project, str) and project and accounts.name_is_safe(project):
        return "p-" + project
    if accounts.name_is_safe(account):
        return "a-" + account
    return None


def _empty(key):
    return {"schema_version": SCHEMA_VERSION, "key": key, "accounts": {}}


def _parse(data, key):
    from . import plaza
    if data is None:
        return _empty(key)
    try:
        value = json.loads(data)
    except (ValueError, RecursionError):
        raise plaza.PlazaError("plaza_store_unavailable") from None
    rows = value.get("accounts") if isinstance(value, dict) else None
    if (not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION
            or value.get("key") != key or not isinstance(rows, dict)):
        raise plaza.PlazaError("plaza_store_unavailable")
    for name, row in rows.items():
        if (not accounts.name_is_safe(name) or not isinstance(row, dict)
                or not isinstance(row.get("read"), dict) or not isinstance(row.get("picked"), dict)):
            raise plaza.PlazaError("plaza_store_unavailable")
    return value


def load(by_account):
    """account → project の表から、その account の控え `{account: {read, picked}}`（読むだけ）。"""
    store = _store()
    directory = store.open()
    out = {}
    try:
        keys = {}
        for name, project in by_account.items():
            key = key_for(name, project)
            if key is not None:
                keys.setdefault(key, []).append(name)
        for key, names in keys.items():
            value = _parse(store.read_raw(directory, key + ".json") if directory is not None else None,
                           key)
            for name in names:
                row = value["accounts"].get(name) or {}
                out[name] = {"read": dict(row.get("read") or {}),
                             "picked": dict(row.get("picked") or {})}
    finally:
        if directory is not None:
            os.close(directory)
    return out


def read_ids(state):
    """読んだ plaza_id の集合（どの account が読んだものでも）。"""
    found = set()
    for row in state.values():
        found |= set(row["read"])
    return found


def picked_on(state, day):
    """その日（JST の日付の文字列）に observe が出した plaza_id（無ければ None）。"""
    for row in state.values():
        if day in row["picked"]:
            return row["picked"][day]
    return None


def record(by_account, *, plaza_ids=(), picked=None, day=None, now=None):
    """読んだことを控える（id と時刻だけ）。`picked` は observe がその日に出した 1 件。"""
    store = _store()
    at = jst.iso(now or jst.now_jst())
    from . import plaza
    ids = [pid for pid in plaza_ids if isinstance(pid, str) and plaza.PLAZA_ID.match(pid)]
    if picked is not None:
        ids.append(picked)
    keys = {}
    for name, project in by_account.items():
        key = key_for(name, project)
        if key is not None:
            keys.setdefault(key, []).append(name)
    with store.locked() as directory:
        for key, names in keys.items():
            value = _parse(store.read_raw(directory, key + ".json"), key)
            for name in names:
                row = value["accounts"].setdefault(name, {"read": {}, "picked": {}})
                for pid in ids:
                    row["read"][pid] = at
                if picked is not None and day is not None:
                    row["picked"][day] = picked
                if len(row["read"]) > READ_MAX:
                    kept = sorted(row["read"].items(), key=lambda item: item[1])[-READ_MAX:]
                    row["read"] = dict(kept)
                if len(row["picked"]) > PICKED_MAX:
                    row["picked"] = dict(sorted(row["picked"].items())[-PICKED_MAX:])
            data = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=1).encode("utf-8")
            store.write_raw(directory, key + ".json", data)
    return {"recorded": len(ids), "at": at}


def forget(account):
    """退出（`account leave`）で、その account の控えを消す（何度呼んでも同じ・置き場が無ければ何もしない）。"""
    store = _store()
    probe = store.open()
    if probe is None:
        return 0
    os.close(probe)
    removed = 0
    with store.locked() as directory:
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".json") or not _KEY.match(name[:-5]):
                continue
            value = _parse(store.read_raw(directory, name), name[:-5])
            if account in value["accounts"]:
                value["accounts"].pop(account)
                data = json.dumps(value, ensure_ascii=False, allow_nan=False, indent=1).encode("utf-8")
                store.write_raw(directory, name, data)
                removed += 1
    return removed
