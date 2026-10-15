"""配信の台帳 `state/<account>/deliveries/<delivery>.json`（設計 4.0 S1 §3.4）。

配信（記事 × 届け先 × 公開物の 1 件）ごとに 1 本。schema は `thth.delivery/1`。

- `state` は `STATES` の 7 つ。`unknown` は結果不明——**失敗とみなして出し直さない**。
- `seq` は状態が変わるたびに 1 増える整数で、`history[]` の最後の `seq` と同じ。
  **出来事の id の材料**（設計 §8）なので、振り直さない・飛ばさない。
- `history[]` は**追記だけ**。1 行が「状態の変わり目」。読むときに 1 から欠けずに並んで
  いるか・最後の行が上の `state`／`seq` と同じかを確かめ、外れていれば読めない台帳として断る。

書き方は `server_files.replace_at(expected=古い中身)`——読んでから書くまでに他の process が
変えていれば `server_file_changed` で断る。書く側は主体（か口座）のロックの中で呼ぶ
（設計 §4.3。この module はロックを取らない）。レーンの repo には書かない。

主体の台帳は読まない（`principal` は名前の文字列で持つだけ）。
"""
from __future__ import annotations

import json
import os
import re
import secrets

from . import accounts as accounts_mod
from . import jst
from . import server_files

SCHEMA = "thth.delivery/1"
DIRNAME = "deliveries"
STATES = ("wanted", "inflight", "unknown", "published", "conflict", "no_follow", "deleted")
DELIVERY_RE = re.compile(r"dl_[0-9a-f]{12}")

# 作ったあとに変えない欄（配信が何で・どこへ・誰の名で、を後から付け替えない）。
IDENTITY_FIELDS = ("schema", "delivery", "account", "principal", "blog_id", "article")
# 状態の変わり目で上書きしてよい欄（設計 §3.4 の例の欄）。
MUTABLE_FIELDS = ("article_source", "form", "queue_file", "entry_id", "edit_uri",
                  "published_url", "wanted_rev", "checked_rev", "checked_at",
                  "article_rev_kind", "readback_sha", "images", "bookmark")
# history の行の固定の鍵（ほかの鍵は値があるときだけ足す）。
_ROW_FIXED = ("seq", "state", "at")
MAX_BYTES = 1048576


class DeliveryError(ValueError):
    """台帳が読めない・書けない（符丁は `str(exc)`）。"""


def is_delivery_id(value) -> bool:
    return isinstance(value, str) and DELIVERY_RE.fullmatch(value) is not None


def new_id() -> str:
    """`dl_` ＋ 12 桁の 16 進の乱数（設計 §3.4）。"""
    return "dl_" + secrets.token_hex(6)


def dir_for(account: str) -> str:
    return os.path.join(accounts_mod.state_dir_for(account), DIRNAME)


def path_for(account: str, delivery: str) -> str:
    if not is_delivery_id(delivery):
        raise DeliveryError("invalid_delivery")
    return os.path.join(dir_for(account), f"{delivery}.json")


def _check_account(account) -> None:
    if not accounts_mod.name_is_safe(account):
        raise DeliveryError("invalid_account")


def _check_at(at) -> str:
    if jst.parse(at) is None:
        raise DeliveryError("invalid_at")
    return at


# ------------------------------------------------------------------ 検査

def validate(record, *, account: str | None = None, delivery: str | None = None) -> dict:
    """台帳 1 本の形を確かめる。外れていれば `DeliveryError("delivery_unreadable:<理由>")`。"""
    def bad(why):
        raise DeliveryError(f"delivery_unreadable:{why}")

    if type(record) is not dict:
        bad("not_object")
    if record.get("schema") != SCHEMA:
        bad("schema")
    if not is_delivery_id(record.get("delivery")):
        bad("delivery")
    if delivery is not None and record["delivery"] != delivery:
        bad("delivery_mismatch")
    if not accounts_mod.name_is_safe(record.get("account")):
        bad("account")
    if account is not None and record["account"] != account:
        bad("account_mismatch")
    for key in ("principal", "blog_id", "article"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            bad(key)
    if record.get("state") not in STATES:
        bad("state")
    seq = record.get("seq")
    if type(seq) is not int or seq < 1:
        bad("seq")
    history = record.get("history")
    if type(history) is not list or len(history) != seq:
        bad("history")
    for index, row in enumerate(history, start=1):
        if type(row) is not dict or row.get("seq") != index or row.get("state") not in STATES:
            bad("history")
        if jst.parse(row.get("at")) is None:
            bad("history_at")
    if history[-1]["state"] != record["state"]:
        bad("history_state")
    return record


# ------------------------------------------------------------------ 読む

class Loaded:
    """読んだ台帳と、読んだときのバイト列（書くときの `expected`）。"""

    __slots__ = ("record", "raw")

    def __init__(self, record: dict, raw: bytes):
        self.record = record
        self.raw = raw


def _read_raw(account: str, delivery: str) -> bytes:
    path = path_for(account, delivery)
    try:
        with server_files.directory(os.path.dirname(path)) as fd:
            return server_files.read_at(fd, os.path.basename(path), private=True, maximum=MAX_BYTES)
    except FileNotFoundError:
        raise DeliveryError("delivery_missing") from None
    except server_files.UnsafeFile as exc:
        raise DeliveryError(f"delivery_unreadable:{exc}") from None
    except OSError as exc:
        raise DeliveryError(f"delivery_unreadable:{exc.__class__.__name__}") from None


def load(account: str, delivery: str) -> Loaded:
    _check_account(account)
    raw = _read_raw(account, delivery)
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise DeliveryError("delivery_unreadable:json") from None
    return Loaded(validate(record, account=account, delivery=delivery), raw)


def read(account: str, delivery: str) -> dict:
    return load(account, delivery).record


def list_ids(account: str) -> list:
    """口座の台帳の置き場にある配信 id（名前の順）。置き場が無ければ空。"""
    _check_account(account)
    try:
        names = os.listdir(dir_for(account))
    except FileNotFoundError:
        return []
    return sorted(name[:-5] for name in names if name.endswith(".json") and is_delivery_id(name[:-5]))


def list_for(account: str) -> tuple[list, list]:
    """(読めた台帳, 読めなかったもの `[{"account","delivery","reason"}]`)。1 本で全部を落とさない。"""
    rows, broken = [], []
    for delivery in list_ids(account):
        try:
            rows.append(read(account, delivery))
        except DeliveryError as exc:
            broken.append({"account": account, "delivery": delivery, "reason": str(exc)})
    return rows, broken


# ------------------------------------------------------------------ 書く

def _write(account: str, delivery: str, record: dict, *, expected: bytes | None, new: bool) -> bytes:
    raw = server_files.encode(record)
    path = path_for(account, delivery)
    try:
        with server_files.directory(os.path.dirname(path), create=True) as fd:
            server_files.replace_at(fd, os.path.basename(path), raw, expected=expected, new=new,
                                    private=True)
    except server_files.UnsafeFile as exc:
        raise DeliveryError(str(exc)) from None
    return raw


def _history_row(seq: int, state: str, at: str, row: dict | None) -> dict:
    out = {"seq": seq, "state": state, "at": at}
    for key, value in (row or {}).items():
        if key in _ROW_FIXED:
            raise DeliveryError(f"history_key_reserved:{key}")
        if value is not None:
            out[key] = value
    return out


def create(account: str, *, principal: str, blog_id: str, article: str, at: str,
           delivery: str | None = None, state: str = "wanted", fields: dict | None = None,
           row: dict | None = None) -> dict:
    """新しい配信の台帳を `seq: 1` で作る。同じ id が既にあれば `server_file_changed`。"""
    _check_account(account)
    delivery = delivery or new_id()
    if not is_delivery_id(delivery):
        raise DeliveryError("invalid_delivery")
    if state not in STATES:
        raise DeliveryError("invalid_state")
    for name, value in (("principal", principal), ("blog_id", blog_id), ("article", article)):
        if not isinstance(value, str) or not value.strip():
            raise DeliveryError(f"{name}_required")
    record = {"schema": SCHEMA, "delivery": delivery, "account": account,
              "principal": principal, "blog_id": blog_id.strip().lower(), "article": article}
    for key, value in (fields or {}).items():
        if key not in MUTABLE_FIELDS:
            raise DeliveryError(f"field_not_allowed:{key}")
        record[key] = value
    record["state"] = state
    record["seq"] = 1
    record["history"] = [_history_row(1, state, _check_at(at), row)]
    validate(record, account=account, delivery=delivery)
    _write(account, delivery, record, expected=None, new=True)
    return record


def advance(account: str, delivery: str, *, state: str, at: str, base: Loaded | None = None,
            seq: int | None = None, fields: dict | None = None, row: dict | None = None) -> dict:
    """状態の変わり目を 1 行足す（`seq` を 1 進める・`history` に追記）。

    - `base`: 呼び手が前に読んだ台帳。渡せば、そのときのバイト列を `expected` にする
      ——読んでから今までに他の process が書いていれば `server_file_changed`。
      渡さなければここで読み直す（読んでから書くまでの間はやはり `expected` で守る）。
    - `seq`: 新しい行の番号を呼び手が決めているとき（inflight に書いた番号で解く・設計 §8.2）。
      `今の seq + 1` と違えば `seq_mismatch`——振り直さない。
    - `fields`: 上の欄の上書き（`MUTABLE_FIELDS` だけ）。`row`: history の行に足す鍵。
    """
    _check_account(account)
    if state not in STATES:
        raise DeliveryError("invalid_state")
    _check_at(at)
    current = base if base is not None else load(account, delivery)
    old = current.record
    if old.get("delivery") != delivery or old.get("account") != account:
        raise DeliveryError("delivery_mismatch")
    next_seq = old["seq"] + 1
    if seq is not None and seq != next_seq:
        raise DeliveryError("seq_mismatch")
    record = json.loads(json.dumps(old))
    for key, value in (fields or {}).items():
        if key not in MUTABLE_FIELDS:
            raise DeliveryError(f"field_not_allowed:{key}")
        record[key] = value
    record["state"] = state
    record["seq"] = next_seq
    record["history"] = list(old["history"]) + [_history_row(next_seq, state, at, row)]
    validate(record, account=account, delivery=delivery)
    _write(account, delivery, record, expected=current.raw, new=False)
    return record
