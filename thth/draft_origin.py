"""THTH の側の、下書きの出所の記録（監査 5.19.0 直したほうがよい 1）。

台帳（`thth register draft`）と店（`thth shop draft`）が queue に下書きを書くとき、その下書きの道と、頭に置いた出所の行
（`from_register*`・`from_store*`・`not_after`）を、THTH の側の置き場に控える。承認の確定の段は、下書きの頭からでなく
**この控えから**「台帳（店）の下書きか」を知るので、持ち主の LLM が本文を整えるときに頭の知らない欄を落としても、
読み直しが外れない。

- 置き場: 台帳は `register_state/<台帳>/drafts.json`、店は `store_state/<店>/drafts.json`（どちらも 0600）。
  台帳・店を外す（`register remove`・`shop remove`・`leave`）と、置き場ごと消える。
- 鍵は下書きの道（`os.path.realpath`）。値は頭の行の名前と値（字）だけ。本文は控えない。
- 道のファイルがもう無い控えは、書くたびに捨てる。数の上限は `MAX_ENTRIES`（古い順に捨てる）。
- 網には出ない。台帳の機械からも店の機械からも読まれるので、どちらの部品も import しない。
"""
from __future__ import annotations

import json
import os

from . import secrets_fs

FILE = "drafts.json"
SCHEMA = "thth.draft_origin/1"
MAX_ENTRIES = 500
VALUE_MAX = 400


class OriginInvalid(Exception):
    """控えが壊れている（読み違えて承認を通さないよう、呼ぶ側が断る）。"""


def key_of(path) -> str:
    return os.path.realpath(os.path.abspath(str(path)))


def _path(state_dir: str) -> str:
    return os.path.join(state_dir, FILE)


def read(state_dir: str) -> dict:
    """道 → 頭の行。無ければ空。壊れていれば `OriginInvalid`。"""
    try:
        with open(_path(state_dir), encoding="utf-8") as f:
            value = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise OriginInvalid(state_dir) from None
    drafts = value.get("drafts") if isinstance(value, dict) and value.get("schema") == SCHEMA else None
    if not isinstance(drafts, dict) or not all(
            isinstance(k, str) and isinstance(v, dict) and all(isinstance(x, str) and isinstance(y, str)
                                                               for x, y in v.items())
            for k, v in drafts.items()):
        raise OriginInvalid(state_dir)
    return drafts


def record(state_dir: str, path, head: dict) -> None:
    """下書き `path` の頭の行を控える（同じ道の古い控えは置き換える）。"""
    clean = {str(k): str(v)[:VALUE_MAX] for k, v in head.items() if v is not None}
    try:
        drafts = read(state_dir)
    except OriginInvalid:
        drafts = {}  # 壊れた控えは捨てて書き直す（読む側は壊れていれば断るので、ここで直る）
    drafts = {k: v for k, v in drafts.items() if os.path.exists(k)}
    drafts[key_of(path)] = clean
    if len(drafts) > MAX_ENTRIES:
        drafts = dict(list(drafts.items())[-MAX_ENTRIES:])
    secrets_fs.atomic_write_json(_path(state_dir), {"schema": SCHEMA, "drafts": drafts}, mode=0o600)


def find(state_dirs, path) -> tuple:
    """`(頭の行 か None, 壊れた控えがあったか)`。

    壊れた控えがあっても、ほかの置き場は見る（ある台帳の控えが壊れただけで、ほかの原稿の承認を止めない）。
    呼ぶ側は、見つからず壊れた控えがあったとき、その下書きが台帳（店）の物らしければ断る。
    """
    key = key_of(path)
    broken = False
    for state_dir in state_dirs:
        try:
            found = read(state_dir).get(key)
        except OriginInvalid:
            broken = True
            continue
        if found is not None:
            return dict(found), broken
    return None, broken
