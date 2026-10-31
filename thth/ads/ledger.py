"""広告の口座の台帳 `adaccounts/<名前>.json` と状態 `adaccount_state/<名前>/`（計画 B-4・D-1・D-3・段 4）。

置き場（はてなの主体 `principals.py` と同じ作り）:

- 台帳: `$THTH_ROOT/adaccounts/<名前>.json`（口座の台帳の置き場の隣・`THTH_ADACCOUNTS_DIR` で差し替え可・
  利用者の repo には置かない）。0600。**masaru か運用が VM で直接書く**（`ceilings`・`ads_enabled`・
  `production` を上げるのはここだけ。THTH の CLI と MCP に上げる口は無い・D-1 の 3）。
- 状態: `$THTH_ROOT/adaccount_state/<名前>/`（`state/` の外。口座の名前とぶつからない）。中身は
  `guard/stopped.json`（止めの印）・`lock`・`auth_state.json`（認可の途中）・`meta_usage.json`（量の写し）・
  `snapshot.json`（collect の読み・D-5）・`exchange_trials.ndjson`（鍵の再交換の試し・G-12）・
  `runs-YYYY-MM.ndjson`（maintain の行）。段 5 で `ops/`・`budget-<YYYY-MM>.json` が足される。

台帳の形:

```
{"adaccount": "kopicha-ads", "act_id": "1234567890", "business_id": null, "project": "kopicha",
 "currency": null, "timezone_name": null,          # `thth ads auth` が Meta の答えから写す
 "production": false, "ads_enabled": false,        # 既定は止めた状態（D-1 の 5）
 "pages": ["kopicha-fb"], "ig_accounts": ["kopicha-ig"],
 "ceilings": {"monthly": 0, "lifetime_budget_max": 0, "days_max": 0},   # 最小単位の整数・既定 0
 "writes_refused": null,                           # 書く操作を断る印（"ads_currency_unverified" など）
 "require_gate": true,                             # 省略は true（activate・extend に hub の番号の記録が要る・段 5）
 "token": "~/.config/thth/kopicha-ads.token", "provenance": {...}}
```

**金額は最小単位の整数**（JPY なら円・USD ならセント）。`ceilings` の値も同じ。

**止めた口座**（D-1 の 4）: 読む・止める・減らすは止まっていても通す（THTH の外で始まった配信に気づく
ため・止める道を塞がないため）。作る・始める・増やす・延ばす（段 5）は `require_active()` が
`accounts.AccountStopped` で断る（呼ぶのは `ops.boost`・`ops.activate`・`ops.extend` だけ）。
止めの印を書くのは `stop()`（`thth ads stop` の 4 番目）。**戻す口は THTH に無い**（上げる操作と同じく
VM で `guard/stopped.json` を控えてから消す・D-1 の 3）。

**鍵の期限**（段 5・本番 10/31）: 広告の鍵（ユーザーの鍵）は `/debug_token` が `expires_at=0`（期限なし）を
返した。`token_limits()` は「鍵の期限」と「データに触れる期限（`data_access_expires_at`）」の**早いほう**を
効く期限にする（0 は期限なし）。残り 14 日の柵（`activate`・maintain）はこの効く期限で見る。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .. import accounts, jst, secrets_fs, server_files

MEDIA = "ads"
ADACCOUNTS_DIR_ENV = "THTH_ADACCOUNTS_DIR"
REQUIRED_FIELDS = ("adaccount", "act_id", "project", "token", "ceilings")
CEILING_KEYS = ("monthly", "lifetime_budget_max", "days_max")
ACT_ID_RE = re.compile(r"[0-9]{1,32}")
CURRENCY_RE = re.compile(r"[A-Z]{3}")
# 書く操作を通してよい通貨（B-13）。`spend_cap` の読む欄は最小単位・書く引数は普通の単位の小数、と
# 頁の中で食い違う【確・原文】。円は小数を持たないので差が出ない。ほかの通貨は単位を本物で確かめるまで断る。
WRITE_CURRENCIES = frozenset({"JPY"})
CURRENCY_UNVERIFIED = "ads_currency_unverified"
CURRENCY_UNKNOWN = "ads_currency_unknown"
WRITE_REFUSALS = frozenset({CURRENCY_UNVERIFIED})
STOP_FILE = "stopped.json"
STOP_REASONS = frozenset(("owner", "emergency"))


class AdAccountError(accounts.AccountError):
    """広告の口座の台帳の断り。`code` は静的な符丁。値（鍵）は文面に出さない。"""

    def __init__(self, code, message):
        super().__init__(f"{message}（{code}）")
        self.code = code


# --------------------------------------------------------------------------
# 置き場
# --------------------------------------------------------------------------

def adaccounts_dir() -> str:
    """台帳の置き場。`$THTH_ADACCOUNTS_DIR` → 口座の台帳の置き場の隣の `adaccounts/`。"""
    env = os.environ.get(ADACCOUNTS_DIR_ENV)
    if env:
        return os.path.abspath(os.path.expanduser(env))
    info = accounts.accounts_dir_info()
    if info["source"] == accounts.SOURCE_ENV:
        return os.path.join(os.path.dirname(info["path"].rstrip(os.sep)), "adaccounts")
    return os.path.join(accounts.thth_root(), "adaccounts")


def _validate_name(name) -> None:
    if not accounts.name_is_safe(name):
        raise AdAccountError("invalid_adaccount_name",
                             f"広告の口座の名前に使えない字が入っています: {name!r}")


def path_for(name: str) -> str:
    _validate_name(name)
    return os.path.join(adaccounts_dir(), f"{name}.json")


def state_dir_for(name: str) -> str:
    """状態の置き場 `$THTH_ROOT/adaccount_state/<名前>/`（**`state/` の外**）。"""
    _validate_name(name)
    return os.path.join(accounts.thth_root(), "adaccount_state", name)


def ensure_state_dir(name: str) -> str:
    path = state_dir_for(name)
    if not os.path.isdir(path):
        os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def usage_path_for(name: str) -> str:
    """量の写し（`meta/usage.py` の置き場を直に渡す）。"""
    return os.path.join(state_dir_for(name), "meta_usage.json")


def list_names() -> list:
    """台帳のある広告の口座の名前（読めない置き場は空）。"""
    try:
        entries = sorted(os.listdir(adaccounts_dir()))
    except OSError:
        return []
    out = []
    for entry in entries:
        if entry.endswith(".json") and accounts.name_is_safe(entry[:-5]):
            out.append(entry[:-5])
    return out


# --------------------------------------------------------------------------
# 台帳
# --------------------------------------------------------------------------

def default_ceilings() -> dict:
    """上限の既定（**0 = 始められない**・X の投稿の本数と同じ型・D-1 の 5）。"""
    return {key: 0 for key in CEILING_KEYS}


def _int0(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate(name: str, data) -> None:
    """台帳の形を見る。外れれば `AdAccountError`（符丁つき）。"""
    if not isinstance(data, dict):
        raise AdAccountError("adaccount_invalid", f"{name}: 台帳が object ではありません")
    missing = [k for k in REQUIRED_FIELDS if k not in data]
    if missing:
        raise AdAccountError("adaccount_field_missing", f"{name}: 台帳に項目が足りません: {missing}")
    if data["adaccount"] != name:
        raise AdAccountError("adaccount_name_mismatch", f"{name}: 台帳の adaccount がファイル名と違います")
    if not isinstance(data["act_id"], str) or not ACT_ID_RE.fullmatch(data["act_id"]):
        raise AdAccountError("act_id_invalid", f"{name}: act_id は `act_` を抜いた数字です")
    business = data.get("business_id")
    if business is not None and (not isinstance(business, str) or not ACT_ID_RE.fullmatch(business)):
        raise AdAccountError("business_id_invalid", f"{name}: business_id は数字です")
    if not isinstance(data["project"], str) or not accounts.name_is_safe(data["project"]):
        raise AdAccountError("project_invalid", f"{name}: project は英数字と `_`・`.`・`-` の名前です")
    if not isinstance(data["token"], str) or not data["token"].strip():
        raise AdAccountError("adaccount_invalid", f"{name}: token はファイルのパスです")
    currency = data.get("currency")
    if currency is not None and (not isinstance(currency, str) or not CURRENCY_RE.fullmatch(currency)):
        raise AdAccountError("currency_invalid", f"{name}: currency は 3 字の大文字（例 JPY）です")
    tz = data.get("timezone_name")
    if tz is not None and (not isinstance(tz, str) or not tz or len(tz) > 64
                           or any(ord(c) <= 32 or ord(c) == 127 for c in tz)):
        raise AdAccountError("timezone_invalid", f"{name}: timezone_name は時間帯の名前です（例 Asia/Tokyo）")
    for key in ("production", "ads_enabled", "require_gate"):
        if key in data and not isinstance(data[key], bool):
            raise AdAccountError("adaccount_invalid", f"{name}: {key} は true／false です")
    for key in ("pages", "ig_accounts"):
        value = data.get(key, [])
        if not isinstance(value, list) or any(not accounts.name_is_safe(v) for v in value):
            raise AdAccountError("adaccount_invalid", f"{name}: {key} は THTH の口座の名前の並びです")
    ceilings = data["ceilings"]
    if (not isinstance(ceilings, dict) or set(ceilings) != set(CEILING_KEYS)
            or not all(_int0(ceilings[k]) for k in CEILING_KEYS)):
        raise AdAccountError("ceilings_invalid",
                             f"{name}: ceilings は {'・'.join(CEILING_KEYS)} の 3 つ（0 以上の整数・最小単位）です")
    refused = data.get("writes_refused")
    if refused is not None and refused not in WRITE_REFUSALS:
        raise AdAccountError("adaccount_invalid", f"{name}: writes_refused は {'・'.join(sorted(WRITE_REFUSALS))} か null です")


def _expand(value):
    return accounts._expand(value) if isinstance(value, str) else value


def load(name: str) -> dict:
    """`adaccounts/<名前>.json` を読んで検査する。`token` は展開したコピーを返す。"""
    path = path_for(name)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError as e:
        raise AdAccountError("adaccount_missing", f"広告の口座の台帳がありません: {name}（{path}）") from e
    except (OSError, ValueError) as e:
        raise AdAccountError("adaccount_unreadable", f"広告の口座の台帳が読めません: {name}") from e
    validate(name, data)
    out = dict(data)
    out["token"] = _expand(out["token"])
    return out


def load_raw(name: str) -> dict:
    """展開していない台帳（書き直すとき）。"""
    with open(path_for(name), encoding="utf-8") as f:
        data = json.load(f)
    validate(name, data)
    return data


def build(name: str, *, act_id: str, project: str, pages=(), ig_accounts=(), business_id=None) -> dict:
    """`thth ads add` が書く 1 本。**上限は 0・旗は false**（引数で上げる口は無い・D-1）。"""
    return {
        "adaccount": name,
        "act_id": act_id,
        "business_id": business_id,
        "project": project,
        "currency": None,
        "timezone_name": None,
        "production": False,
        "ads_enabled": False,
        "pages": list(pages),
        "ig_accounts": list(ig_accounts),
        "ceilings": default_ceilings(),
        "writes_refused": None,
        "token": f"~/.config/thth/{name}.token",
    }


def write_new(name: str, data: dict) -> str:
    """台帳を新しく書く（既にあれば断る・0600）。"""
    validate(name, data)
    path = path_for(name)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def mirror_account_facts(name: str, *, currency: str | None, timezone_name: str | None) -> dict:
    """`thth ads auth` が Meta の答えから `currency`・`timezone_name` を写す（無いときだけ）。

    **既に書いてあって違えば断る**（`adaccount_changed`・取り違え防止）。通貨が JPY 以外なら
    `writes_refused` に印を残す（B-13・単位を本物で確かめるまで書く操作を断る）。**上限・旗は触らない。**
    書き直した台帳（展開していない形）を返す。
    """
    data = load_raw(name)
    for key, value in (("currency", currency), ("timezone_name", timezone_name)):
        if value is None:
            continue
        if data.get(key) is not None and data[key] != value:
            raise AdAccountError("adaccount_changed",
                                 f"{name}: 台帳の {key}（{data[key]}）と Meta の答え（{value}）が違います。保存しません")
        data[key] = value
    if data.get("currency") is not None and data["currency"] not in WRITE_CURRENCIES:
        data["writes_refused"] = CURRENCY_UNVERIFIED
    validate(name, data)
    secrets_fs.atomic_write_json(path_for(name), data, mode=0o600)
    return data


def write_refusal(cfg: dict) -> str | None:
    """書く操作（段 5）を断る符丁。無ければ None。**この段では書く操作そのものが無い。**

    通貨が分からない（まだ `thth ads auth` をしていない）・JPY 以外・台帳の印、のどれかで断る（B-13）。
    """
    if cfg.get("writes_refused"):
        return cfg["writes_refused"]
    currency = cfg.get("currency")
    if currency is None:
        return CURRENCY_UNKNOWN
    if currency not in WRITE_CURRENCIES:
        return CURRENCY_UNVERIFIED
    return None


def ceilings(cfg: dict) -> dict:
    value = cfg.get("ceilings")
    return dict(value) if isinstance(value, dict) else default_ceilings()


def linked(cfg: dict) -> list:
    """結びつけた THTH の口座の名前（ページ・IG）。"""
    return list(cfg.get("pages") or []) + list(cfg.get("ig_accounts") or [])


# --------------------------------------------------------------------------
# 鍵（`thth ads auth` だけが書く。値は表示・ログに出さない）
# --------------------------------------------------------------------------

def load_token(cfg: dict) -> dict | None:
    """`.token` を読む。無ければ None、読めなければ `{"unreadable": True}`。"""
    path = cfg.get("token")
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError):
        return {"unreadable": True}
    if not isinstance(value, dict):
        return {"unreadable": True}
    if isinstance(value.get("access_token"), str):
        from .. import redact
        redact.register_secret(value["access_token"])
    return value


def write_token(cfg: dict, token: dict) -> str:
    secrets_fs.atomic_write_json(cfg["token"], token, mode=0o600)
    return cfg["token"]


def token_no_expiry(token) -> bool:
    """鍵そのものに期限が無いか（`/debug_token` の `expires_at=0`・本番 10/31）。"""
    if not isinstance(token, dict):
        return False
    if token.get("no_expiry") is True:
        return True
    value = token.get("expires_at")
    return isinstance(value, int) and not isinstance(value, bool) and value == 0


def token_limits(token) -> dict:
    """鍵の期限の見立て。`{"no_expiry", "expires_at", "data_access_expires_at", "effective_at", "basis"}`。

    `effective_at` は鍵の期限とデータに触れる期限の**早いほう**（分からない物は入れない）。
    `basis` は `token`・`data_access`・None（どちらも分からない）。
    """
    no_expiry = token_no_expiry(token)
    expires_at = None if no_expiry else token_expires_at(token)
    data_access = token.get("data_access_expires_at") if isinstance(token, dict) else None
    if not (isinstance(data_access, int) and not isinstance(data_access, bool) and data_access > 0):
        data_access = None
    candidates = [(at, basis) for at, basis in ((expires_at, "token"), (data_access, "data_access"))
                  if at is not None]
    effective, basis = min(candidates) if candidates else (None, None)
    return {"no_expiry": no_expiry, "expires_at": expires_at, "data_access_expires_at": data_access,
            "effective_at": effective, "basis": basis}


def refresh_token_expiry(cfg: dict, debug) -> dict | None:
    """doctor が `/debug_token` の**今の**値で `.token` の期限の欄を書き直す（C-2 の 2）。

    書き直すのは `expires_at`・`no_expiry`・`data_access_expires_at`・`expiry_source` だけ（鍵の値は触らない）。
    読めない・変わらなければ書かない。書き直した `.token` を返す（書かなければ None）。
    """
    if not isinstance(debug, dict):
        return None
    token = load_token(cfg)
    if not isinstance(token, dict) or token.get("unreadable"):
        return None
    updated = dict(token)
    at = debug.get("expires_at")
    if isinstance(at, int) and not isinstance(at, bool):
        if at == 0:
            updated["no_expiry"] = True
            updated.pop("expires_at", None)
            updated.pop("expires_in", None)
        elif at > 0:
            updated["expires_at"] = at
            updated.pop("no_expiry", None)
        updated["expiry_source"] = "debug_token"
    data_access = debug.get("data_access_expires_at")
    if isinstance(data_access, int) and not isinstance(data_access, bool) and data_access > 0:
        updated["data_access_expires_at"] = data_access
    if updated == token:
        return None
    write_token(cfg, updated)
    return updated


def token_expires_at(token) -> int | None:
    """鍵の期限（UNIX 秒）。`expires_at` が正本、無ければ `obtained_at + expires_in`。分からなければ None。"""
    if not isinstance(token, dict):
        return None
    value = token.get("expires_at")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    seconds = token.get("expires_in")
    obtained = jst.parse(token.get("obtained_at")) if isinstance(token.get("obtained_at"), str) else None
    if isinstance(seconds, int) and not isinstance(seconds, bool) and seconds > 0 and obtained is not None:
        return int(obtained.timestamp()) + seconds
    return None


def token_remaining_days(token, now) -> float | None:
    """効く期限（鍵とデータに触れる期限の早いほう）までの日数。分からなければ None。"""
    at = token_limits(token)["effective_at"]
    if at is None:
        return None
    return (at - now.timestamp()) / 86400.0


# --------------------------------------------------------------------------
# 止めの印（`principals.stopped` と同じ規律。読めなければ止まっている側）
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


def require_active(name) -> None:
    """作る・始める・増やす・延ばす（段 5）の前に呼ぶ。止まっていれば `AccountStopped`。

    読む・止める・減らすは呼ばない（D-1 の 4）。THTH の口座の退出（`leave_gate`）も同じく見る。
    """
    if stopped(name):
        raise accounts.AccountStopped("adaccount_stopped")
    from .. import leave_gate
    leave_gate.require_active(name)


def stop(name, reason="owner", *, by, via="cli", now=None) -> dict:
    """止めの印を書く（`thth ads stop` の 4 番目・既に止まっていれば上書きしない）。

    止めると作る・始める・延ばすが断られる。止める・減らす・読むは通る（D-1 の 4）。
    """
    if reason not in STOP_REASONS:
        raise ValueError("invalid_stop_reason")
    value = {"reason": reason, "stopped_at": jst.iso(now), "actor": by, "via": via}
    ensure_state_dir(name)
    with server_files.directory(_guard_directory(name), create=True, private=True) as fd:
        try:
            server_files.replace_at(fd, STOP_FILE, server_files.encode(value), new=True, private=True)
        except server_files.UnsafeFile:
            pass  # 既に止まっている（先に止めた印を消さない）
    try:
        from .. import admin_log
        admin_log.append("adaccount_stopped", name, {"media": MEDIA}, by=by,
                         via=via if via in ("cli", "mcp") else "cli",
                         diff={"stopped": [False, True], "reason": [None, reason]})
    except Exception:  # noqa: BLE001 — 変更ログに残せなくても止めることは止める（止めた印が正本）
        pass
    return value


# --------------------------------------------------------------------------
# 上限を下げる（**上げる口は無い**・D-1 の 3）
# --------------------------------------------------------------------------

def lower_ceiling(name: str, key: str, value: int, *, by: str, via: str = "cli") -> dict:
    """`ceilings.<key>` を**下げる**だけ。今の値以上なら `ads_not_lower` で断る（上げる口はここにも無い）。

    台帳は 0600 のまま書き直す。`{"before", "after"}` を返す。
    """
    from .. import admin_log
    admin_log.actor(by)
    if key not in CEILING_KEYS:
        raise AdAccountError("ceiling_unknown", f"{name}: 上限の名前は {'・'.join(CEILING_KEYS)} です")
    if not _int0(value):
        raise AdAccountError("ceilings_invalid", f"{name}: 上限は 0 以上の整数（最小単位）です")
    data = load_raw(name)
    before = data["ceilings"][key]
    if value >= before:
        raise AdAccountError("ads_not_lower",
                             f"{name}: {key} は今 {before} です。下げる値（{before} より小さい数）だけを受けます。"
                             "上げるのは VM の台帳を直接書く（cp -a で控えてから）")
    data["ceilings"] = dict(data["ceilings"], **{key: value})
    validate(name, data)
    secrets_fs.atomic_write_json(path_for(name), data, mode=0o600)
    try:
        admin_log.append("adaccount_lowered", name, {"media": MEDIA}, by=by,
                         via=via if via in ("cli", "mcp") else "cli", diff={key: [before, value]})
    except Exception:  # noqa: BLE001 — 下げたことは下げた（台帳が正本）
        pass
    return {"before": before, "after": value}
