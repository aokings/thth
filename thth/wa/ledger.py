"""WhatsApp の口座の台帳 `waba/<名前>.json` と状態 `waba_state/<名前>/`（広告の `thth/ads/ledger.py` の型）。

置き場:

- 台帳: `$THTH_ROOT/waba/<名前>.json`（`THTH_WABA_DIR` で差し替え可・0600）。**`production` と `send_daily_max` を
  上げるのは VM の台帳を直接書くときだけ**（THTH の命令に上げる口は無い）。
- 状態: `$THTH_ROOT/waba_state/<名前>/`: `guard/stopped.json`（止めの印）・`lock`・`auth_state.json`・
  `meta_usage.json`・`runs-YYYY-MM.ndjson`。

台帳の形:

```
{"waba": "kopicha-wa", "waba_id": "1234567890", "business_id": null, "project": "kopicha",
 "production": false,          # 既定は止めた状態。false の間、送る・テンプレートを作るは媒体を呼ばない
 "send_daily_max": 10,         # 1 日（JST）に送れる通数の上限（runs の wa_send の行を数える）
 "token": "~/.config/thth/kopicha-wa.token", "provenance": {...}}
```

**保存しないもの**: 送った相手の番号・本文・テンプレートの本文。残すのは runs の 1 行（命令の名前・媒体・使った権限・結果）だけ。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .. import accounts, jst, server_files
from ..ads import ledger as ads_ledger

MEDIA = "whatsapp"
WABA_DIR_ENV = "THTH_WABA_DIR"
REQUIRED_FIELDS = ("waba", "waba_id", "project", "token")
ID_RE = re.compile(r"[0-9]{1,32}")
SEND_DAILY_MAX_DEFAULT = 10
STOP_FILE = "stopped.json"
STOP_REASONS = frozenset(("owner", "emergency"))


class WabaError(accounts.AccountError):
    """WhatsApp の台帳の断り。`code` は静的な符丁。値（鍵）は文面に出さない。"""

    def __init__(self, code, message):
        super().__init__(f"{message}（{code}）")
        self.code = code


def waba_dir() -> str:
    env = os.environ.get(WABA_DIR_ENV)
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(accounts.thth_root(), "waba")


def _validate_name(name) -> None:
    if not accounts.name_is_safe(name):
        raise WabaError("invalid_waba_name", f"WhatsApp の口座の名前に使えない字が入っています: {name!r}")


def path_for(name: str) -> str:
    _validate_name(name)
    return os.path.join(waba_dir(), f"{name}.json")


def state_dir_for(name: str) -> str:
    _validate_name(name)
    return os.path.join(accounts.thth_root(), "waba_state", name)


def ensure_state_dir(name: str) -> str:
    path = state_dir_for(name)
    if not os.path.isdir(path):
        os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def usage_path_for(name: str) -> str:
    return os.path.join(state_dir_for(name), "meta_usage.json")


def lock_path_for(name: str) -> str:
    return os.path.join(state_dir_for(name), "lock")


def list_names() -> list:
    try:
        entries = sorted(os.listdir(waba_dir()))
    except OSError:
        return []
    return [e[:-5] for e in entries if e.endswith(".json") and accounts.name_is_safe(e[:-5])]


def name_taken(name: str) -> str | None:
    """ほかの種類の台帳（口座・広告）に同じ名前があれば、その種類。"""
    if name in accounts.list_account_names():
        return "THTH の口座"
    if name in ads_ledger.list_names():
        return "広告の口座"
    return None


def _int0(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate(name: str, data) -> None:
    if not isinstance(data, dict):
        raise WabaError("waba_invalid", f"{name}: 台帳が object ではありません")
    missing = [k for k in REQUIRED_FIELDS if k not in data]
    if missing:
        raise WabaError("waba_field_missing", f"{name}: 台帳に項目が足りません: {missing}")
    if data["waba"] != name:
        raise WabaError("waba_name_mismatch", f"{name}: 台帳の waba がファイル名と違います")
    if not isinstance(data["waba_id"], str) or not ID_RE.fullmatch(data["waba_id"]):
        raise WabaError("waba_id_invalid", f"{name}: waba_id は WhatsApp Business Account の数字の id です")
    business = data.get("business_id")
    if business is not None and (not isinstance(business, str) or not ID_RE.fullmatch(business)):
        raise WabaError("business_id_invalid", f"{name}: business_id は数字です")
    if not isinstance(data["project"], str) or not accounts.name_is_safe(data["project"]):
        raise WabaError("project_invalid", f"{name}: project は英数字と `_`・`.`・`-` の名前です")
    if not isinstance(data["token"], str) or not data["token"].strip():
        raise WabaError("waba_invalid", f"{name}: token はファイルのパスです")
    if "production" in data and not isinstance(data["production"], bool):
        raise WabaError("waba_invalid", f"{name}: production は true／false です")
    if "send_daily_max" in data and not _int0(data["send_daily_max"]):
        raise WabaError("waba_invalid", f"{name}: send_daily_max は 0 以上の整数です")


def load(name: str) -> dict:
    path = path_for(name)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as e:
        raise WabaError("waba_missing", f"WhatsApp の口座の台帳がありません: {name}（{path}）") from e
    except (OSError, ValueError) as e:
        raise WabaError("waba_unreadable", f"WhatsApp の口座の台帳が読めません: {name}") from e
    validate(name, data)
    out = dict(data)
    out["token"] = accounts._expand(out["token"])
    return out


def build(name: str, *, waba_id: str, project: str, business_id=None) -> dict:
    """`thth wa add` が書く 1 本。**production は false**（引数で上げる口は無い）。"""
    return {"waba": name, "waba_id": waba_id, "business_id": business_id, "project": project,
            "production": False, "send_daily_max": SEND_DAILY_MAX_DEFAULT,
            "token": f"~/.config/thth/{name}.token"}


def write_new(name: str, data: dict) -> str:
    validate(name, data)
    path = path_for(name)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def send_daily_max(cfg: dict) -> int:
    value = cfg.get("send_daily_max")
    return value if _int0(value) else SEND_DAILY_MAX_DEFAULT


# 鍵（`thth wa auth` だけが書く）。読み書き・期限の見立ては広告と同じ関数を使う（`token` の欄を読むだけ）。
load_token = ads_ledger.load_token
write_token = ads_ledger.write_token
token_limits = ads_ledger.token_limits
token_remaining_days = ads_ledger.token_remaining_days
token_no_expiry = ads_ledger.token_no_expiry


# 止めの印（読めなければ止まっている側）。
def _guard_directory(name) -> Path:
    return Path(state_dir_for(name)) / "guard"


def stopped(name) -> dict | None:
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


def stop(name, reason="owner", *, by, now=None) -> dict:
    """止めの印を書く（既に止まっていれば上書きしない）。外すのは VM で印を控えてから消す。"""
    if reason not in STOP_REASONS:
        raise ValueError("invalid_stop_reason")
    value = {"reason": reason, "stopped_at": jst.iso(now), "actor": by}
    ensure_state_dir(name)
    with server_files.directory(_guard_directory(name), create=True, private=True) as fd:
        try:
            server_files.replace_at(fd, STOP_FILE, server_files.encode(value), new=True, private=True)
        except server_files.UnsafeFile:
            pass
    return value
