"""承認の台帳（4.4.1・`state/<account>/approvals.json`）。

**承認の記録は VM の台帳に持ち、利用者の repo には積まない**（masaru 10/21「推しでよし」）。
4.4.0 までは `thth approve --confirm`・`thth revoke`・thth.me の予約と取り消しが、原稿の
front-matter（`status: approved`・`approved_sha` など）を書き換えて利用者の repo に commit・push
していた。利用者は配信の承認を repo の main の先頭の commit に付けているので、THTH の commit で
先頭がずれ、配信の照合が通らなくなった（10/21 だけで 4 回）。

## 形

- 置き場: `$THTH_ROOT/state/<account>/approvals.json`（口座ごと）。書くときは同じ所の
  `approvals.lock` を flock で取り、一時ファイルから `os.replace` で入れ替える（読み手は
  ロックを取らない・いつ読んでも書き終えた形のどちらか）。
- 鍵: 原稿の **repo の中の相対パス**（git の作業ツリーの頂上からの相対・clone が違っても同じ原稿）。
- 中身: `state`（`approved`・`revoked`）・`fields`（原稿の front-matter に重ねて見せる項目）・
  `approved_sha`・`at`・`by`・`gate`（hub の承認の番号・⑦）・`via`・短い `history`。

## 読み方（重ねて見せる）

原稿を読む口（`queuefile.parse_text()`・`bundle.parse_text()`）が `overlay()` を通す。読み手
（select・timer・board・schedule の表示・revoke・cancel・hatena の配信・返信の送り）は今までどおり
front-matter の `status`・`approved_sha` を見るだけでよい。

- 台帳に `approved` があり、原稿がまだ出ていない（`status` が `draft`／`approved`・`post_id` 無し）:
  台帳の `fields` を重ねる（台帳が repo の古い承認より優先）。
- 台帳に `revoked` があり、原稿がまだ出ていない: `status: draft` にし、承認の項目を外し、
  `revoked_*` を見せる（repo に古い `status: approved` が書いてあっても止まる）。
- 出たあと（`post_id` あり・`status: posted` など）: 承認の控え（`approved_sha`・`delivery` など）が
  repo に無ければ足して見せるだけ。`status` は変えない。
- 台帳に何も無い: repo のまま（**移行期間**: 4.4.0 までに repo に書いた承認はそのまま効く）。

**指紋は今までどおり読み手が照合する。** 台帳の承認のあとで原稿の中身（本文・account・reply_to・
topic・publish_at など）が変われば、select が `approval_stale` で落とす（今と同じ fail-closed）。
重ねる項目に指紋の材料は入れない（`OVERLAY_KEYS`）。

**台帳が読めないとき**（壊れた JSON など）は、その口座の「まだ出ていない承認済み」を全部
`status: draft` に倒して見せる（取り消しを読み落として出さないため・fail-closed）。
`problem()` が理由を返し、`thth approvals` と `thth approve` が名指しする。
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import uuid

FILE = "approvals.json"
LOCK = "approvals.lock"
VERSION = 1
HISTORY_MAX = 20

APPROVED = "approved"
REVOKED = "revoked"

# 原稿の front-matter に重ねてよい項目。**指紋の材料（本文・account・reply_to・topic・
# publish_at・場所・共有・media・community・announces）は入れない**。
OVERLAY_KEYS = ("status", "approved_sha", "approved_at", "approved_by", "approved_goal",
                "approved_kind", "delivery", "approved_via", "approved_credential",
                "approved_gate", "held_minutes", "queued_for",
                "revoked_at", "revoked_by", "revoked_reason")
# 出たあとの原稿に「無ければ足す」だけの控え（status は変えない）。
_POSTED_INFO_KEYS = ("approved_sha", "approved_at", "approved_by", "approved_goal",
                     "approved_kind", "delivery", "approved_via", "approved_gate")
# 取り消しで外す項目（`thth revoke` の 4.4.0 までの書き戻しと同じ）。
_REVOKE_DROP = ("approved_sha", "approved_at", "approved_by", "approved_goal")
_OPEN_STATUSES = ("draft", "approved")
# 取り消しのあとも見せる控え。4.4.0 の `thth revoke` は repo の `approved_kind`・`delivery` を残した
# （はてなの配信の台帳は wanted のまま・承認し直すと同じ配信 id を使う）。同じ形に見せる。
_REVOKE_CARRY = ("approved_kind", "delivery")

# hub の承認の番号（例 `A261021-25`）。
GATE_PATTERN = re.compile(r"A\d{6}-\d{1,5}\Z")
UNREADABLE_MARK = "approval_ledger_unreadable"
# require_gate の口座で、台帳に記録の無い repo の承認を倒した、の印（監査 3）。
GATE_LEGACY_MARK = "approval_gate_missing"
# 承認のあと人が repo の status を書き換えたので重ねなかった、の印（親の裁定 ①）。
REPO_STATUS_MARK = "approval_repo_status_changed"


class LedgerError(Exception):
    """台帳を読めない・書けない。"""


def valid_gate(value) -> bool:
    return isinstance(value, str) and bool(GATE_PATTERN.match(value))


def gate_refusal(account_cfg, gate) -> str | None:
    """⑦: 公開・予約の道の番号の検査。`require_gate: true` の口座で番号が無いか形が違えば
    `approval_gate_required`。ほかの口座で形が違えば `approval_invalid`。通れば None。"""
    required = isinstance(account_cfg, dict) and account_cfg.get("require_gate") is True
    if gate is None or gate == "":
        return "approval_gate_required" if required else None
    if not valid_gate(gate):
        return "approval_gate_required" if required else "approval_invalid"
    return None


def path_for(account_name: str) -> str:
    from . import accounts
    return os.path.join(accounts.state_dir_for(account_name), FILE)


def _lock_path(account_name: str) -> str:
    from . import accounts
    return os.path.join(accounts.state_dir_for(account_name), LOCK)


# ---- repo の中の相対パス ---------------------------------------------------

_TOP_CACHE: dict = {}


def repo_root_of(path: str) -> str | None:
    """`path` を含む git の作業ツリーの頂上（`.git` のある所）。git を呼ばずに上へたどる。"""
    try:
        real = os.path.realpath(path)
    except (OSError, ValueError, TypeError):
        return None
    directory = os.path.dirname(real)
    seen = []
    found = None
    while True:
        if directory in _TOP_CACHE:
            found = _TOP_CACHE[directory]
            break
        seen.append(directory)
        if os.path.exists(os.path.join(directory, ".git")):
            found = directory
            break
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    for item in seen:
        _TOP_CACHE[item] = found
    return found


def rel_key(path: str, repo_dir: str | None = None) -> str | None:
    """台帳の鍵（repo の中の相対パス・`/` 区切り）。repo の外なら None。"""
    top = os.path.realpath(repo_dir) if repo_dir else repo_root_of(path)
    if not top:
        return None
    rel = os.path.relpath(os.path.realpath(path), top)
    if os.path.isabs(rel) or rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return None
    return rel.replace(os.sep, "/")


# ---- 読む ------------------------------------------------------------------

_LOAD_CACHE: dict = {}


def _valid_entry(entry) -> bool:
    return (isinstance(entry, dict) and entry.get("state") in (APPROVED, REVOKED)
            and isinstance(entry.get("file"), str) and isinstance(entry.get("fields"), dict))


def _read(path: str) -> dict:
    """`{鍵: 記録}`。無ければ空。**読めなければ `LedgerError`**（空に倒さない）。"""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise LedgerError(f"承認の台帳を読めません（{exc.__class__.__name__}）: {path}") from None
    stamp = (st.st_ino, st.st_mtime_ns, st.st_size)
    cached = _LOAD_CACHE.get(path)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise LedgerError(f"承認の台帳を読めません（{exc.__class__.__name__}）: {path}") from None
    if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("items"), dict):
        raise LedgerError(f"承認の台帳の形が違います（version {VERSION} の items が要ります）: {path}")
    items = data["items"]
    if not all(_valid_entry(v) and v["file"] == k for k, v in items.items()):
        raise LedgerError(f"承認の台帳に読めない行があります: {path}")
    _LOAD_CACHE[path] = (stamp, items)
    return items


def load(account_name: str) -> dict:
    """口座の台帳（`{repo の中の相対パス: 記録}`）。読めなければ `LedgerError`。"""
    return _read(path_for(account_name))


def problem(account_name: str) -> str | None:
    """台帳が読めなければその理由（読めれば None）。"""
    try:
        load(account_name)
    except LedgerError as exc:
        return str(exc)
    return None


def entry_for(account_name: str, key: str):
    return load(account_name).get(key)


# ---- 重ねて見せる ----------------------------------------------------------

def _safe_account(name) -> bool:
    if not isinstance(name, str) or not name:
        return False
    from . import accounts
    try:
        return bool(accounts.name_is_safe(name))
    except Exception:  # noqa: BLE001 — 名前の検査で読みを止めない（重ねないだけ）
        return False


def _requires_gate(account_name: str) -> bool:
    """口座の台帳が `require_gate: true` か（読めなければ True・fail-closed）。"""
    from . import accounts
    try:
        path = os.path.join(accounts.accounts_dir(), f"{account_name}.json")
        st = os.stat(path)
    except FileNotFoundError:
        return False  # 口座の台帳が無い（その口座では出ない・gate の設定も無い）
    except Exception:  # noqa: BLE001 — 置き場が読めないなら承認済みとして見せない
        return True
    stamp = (path, st.st_ino, st.st_mtime_ns, st.st_size)
    if stamp in _GATE_CACHE:
        return _GATE_CACHE[stamp]
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f).get("require_gate") is True
    except Exception:  # noqa: BLE001 — 読めない台帳は fail-closed
        return True
    _GATE_CACHE[stamp] = value
    return value


_GATE_CACHE: dict = {}


def overlay(path: str, front_matter: dict, *, posted: bool | None = None) -> dict:
    """台帳の承認を front-matter に重ねた写しを返す（重ねるものが無ければ同じ dict）。

    `posted` は「もう出ているか」（束は呼び出し側が決める）。省略時は `post_id` と `status`。
    """
    fm = front_matter
    if not isinstance(fm, dict) or not path or not os.path.isfile(path):
        return fm
    account = fm.get("account")
    if not _safe_account(account):
        return fm
    key = rel_key(path)
    if key is None:
        return fm
    is_open = fm.get("status") in _OPEN_STATUSES and not fm.get("post_id")
    if posted is not None:
        is_open = fm.get("status") in _OPEN_STATUSES and not posted
    try:
        entry = load(account).get(key)
    except LedgerError:
        if is_open and fm.get("status") == "approved":
            out = dict(fm)
            out["status"] = "draft"
            out[UNREADABLE_MARK] = "true"
            return out
        return fm
    if entry is not None and entry.get("repo"):
        # 4.4.1（監査）: 鍵は相対パスなので、別の clone の同じパスにも当たる。台帳に書いた repo と
        # 原稿の repo の頂上が違えば重ねない（別の原稿として扱う）。
        top = repo_root_of(path)
        try:
            same = top is not None and os.path.realpath(entry["repo"]) == top
        except (OSError, ValueError, TypeError):
            same = False
        if not same:
            entry = None
    if entry is None:
        if is_open and fm.get("status") == "approved" and _requires_gate(account):
            # 4.4.1（監査）: require_gate の口座では、台帳に記録の無い repo の承認（4.4.0 までの形）は
            # 使わない（hub の番号を通っていない承認で出さない）。draft に倒して見せる。
            out = dict(fm)
            out["status"] = "draft"
            for k in _REVOKE_DROP:
                out.pop(k, None)
            out[GATE_LEGACY_MARK] = "true"
            return out
        return fm
    out = dict(fm)
    fields = entry["fields"]
    if entry["state"] == APPROVED:
        if is_open and fm.get("status") != (entry.get("repo_status") or "draft"):
            # **人の手の止めを尊重する**（親の裁定 ① 10/21）。承認のときの repo の status
            # （ふつうは draft）から人が書き換えていれば（4.4.0 までの `status: approved` を draft に
            # 戻した、など）、台帳に承認があっても重ねない。draft に倒して見せる（fail-closed）。
            out["status"] = "draft"
            for k in _REVOKE_DROP:
                out.pop(k, None)
            out[REPO_STATUS_MARK] = "true"
            return out
        if is_open:
            for k in OVERLAY_KEYS:
                if k in fields:
                    if fields[k] is None:
                        out.pop(k, None)
                    else:
                        out[k] = fields[k]
            for k in ("revoked_at", "revoked_by", "revoked_reason"):
                if k not in fields:
                    out.pop(k, None)
            out["status"] = "approved"
        else:
            # 出たあとは status を変えず、承認の控えだけ**台帳の値で**見せる。4.4.1 から承認は
            # repo に書かないので、repo に残る `approved_sha` などは台帳より古い（4.4.0 までの承認・
            # 手で戻した残り）。「無ければ足す」にすると、束の最後の段を書き戻した直後の照合が
            # repo の古い指紋を読み、`approval_stale` で push を止めていた（4.4.1 の書き手が見つけた）。
            for k in _POSTED_INFO_KEYS:
                if fields.get(k) is not None:
                    out[k] = fields[k]
        return out
    # 取り消し
    if not is_open:
        return fm
    for k in _REVOKE_DROP:
        out.pop(k, None)
    for k in ("approved_kind", "approved_via", "approved_credential", "approved_gate",
              "held_minutes", "queued_for"):
        # 台帳の承認で重ねていた項目は外す（repo に元からある項目はそのまま）。
        if k in fields:
            out.pop(k, None)
    out["status"] = "draft"
    for k in ("revoked_at", "revoked_by", "revoked_reason") + _REVOKE_CARRY:
        if fields.get(k) is not None:
            out[k] = fields[k]
    return out


def raw_status(path: str) -> str | None:
    """repo の原稿の front-matter に**書いてある** `status`（台帳を重ねる前・読めなければ None）。"""
    from . import queuefile
    try:
        with open(path, encoding="utf-8") as f:
            split = queuefile._split_front_matter(f.read())
    except (OSError, ValueError):
        return None
    if split is None:
        return None
    for line in split[0].splitlines():
        if line.startswith("status:"):
            return line.partition(":")[2].strip() or None
    return None


def approved_under_other_account(path: str, account_name) -> str | None:
    """同じ repo の同じ原稿が、別の口座の台帳で承認されていればその口座の名前（裁定 ④）。

    原稿の account を書き換えると、変えた先の口座の台帳には承認が無く「承認されていない」に見える。
    そのときの手がかり（読むだけ・見つからなければ None）。
    """
    from . import accounts
    key = rel_key(path)
    top = repo_root_of(path)
    if key is None or top is None:
        return None
    root = os.path.join(accounts.thth_root(), "state")
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None
    for name in names:
        if name == account_name or not _safe_account(name):
            continue
        if not os.path.exists(os.path.join(root, name, FILE)):
            continue
        try:
            entry = load(name).get(key)
        except LedgerError:
            continue
        if not entry or entry["state"] != APPROVED:
            continue
        repo = entry.get("repo")
        if repo and os.path.realpath(repo) != top:
            continue
        return name
    return None


# ---- 書く ------------------------------------------------------------------

@contextlib.contextmanager
def _locked(account_name: str):
    import fcntl
    lock_path = _lock_path(account_name)
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _save(account_name: str, items: dict) -> None:
    path = path_for(account_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp, "w", encoding="utf-8") as f:
            json.dump({"version": VERSION, "items": {k: items[k] for k in sorted(items)}},
                      f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def _clean_fields(fields: dict) -> dict:
    out = {}
    for k, v in (fields or {}).items():
        if k not in OVERLAY_KEYS:
            raise ValueError(f"承認の台帳に書けない項目です: {k}")
        if v is not None and not isinstance(v, str):
            v = str(v)
        out[k] = v
    return out


def _history(old, event: dict) -> list:
    rows = list((old or {}).get("history") or [])
    rows.append(event)
    return rows[-HISTORY_MAX:]


def _apply(items: dict, change: dict) -> None:
    key = change["file"]
    old = items.get(key)
    if change["state"] == APPROVED:
        fields = _clean_fields(change["fields"])
        fields["status"] = "approved"
        if change.get("gate"):
            fields["approved_gate"] = change["gate"]
        items[key] = {
            "file": key, "state": APPROVED, "fields": fields,
            "approved_sha": fields.get("approved_sha"), "at": fields.get("approved_at"),
            "by": fields.get("approved_by"), "gate": change.get("gate"),
            "via": fields.get("approved_via") or change.get("via") or "cli",
            "repo": change.get("repo"),
            # 承認のときの repo の status（ふつうは draft）。人がこれを書き換えたら重ねない（裁定 ①）。
            "repo_status": change.get("repo_status") or "draft",
            "history": _history(old, {"event": "approved", "at": fields.get("approved_at"),
                                      "by": fields.get("approved_by"), "approved_sha": fields.get("approved_sha"),
                                      "gate": change.get("gate"), "via": fields.get("approved_via") or change.get("via")}),
        }
    else:
        fields = _clean_fields(change["fields"])
        prior = (old or {}).get("fields") or {}
        kept = {k: prior[k] for k in ("approved_kind", "approved_via", "approved_credential", "approved_gate",
                                      "held_minutes", "queued_for") if prior.get(k) is not None}
        # 重ねていた項目の名前だけ残す（overlay が外すため）。値は使わない。
        carry = {k: prior[k] for k in _REVOKE_CARRY if prior.get(k) is not None}
        fields = {**{k: kept[k] for k in kept}, **carry, **fields, "status": "draft"}
        items[key] = {
            "file": key, "state": REVOKED, "fields": fields,
            "approved_sha": None, "at": fields.get("revoked_at"), "by": fields.get("revoked_by"),
            "gate": None, "via": change.get("via") or "cli", "repo": change.get("repo"),
            "history": _history(old, {"event": "revoked", "at": fields.get("revoked_at"),
                                      "by": fields.get("revoked_by"), "reason": fields.get("revoked_reason"),
                                      "via": change.get("via")}),
        }


def record(changes: list) -> None:
    """承認・取り消しをまとめて書く。`changes` は
    `{"account", "file"（repo の中の相対パス）, "state", "fields", "gate"?, "via"?, "repo"?}` の並び。

    **口座ごとに 1 回の書き込み。途中の口座で書けなければ、それまでに書いた口座を元に戻して
    `LedgerError`**（半分だけ承認された状態を作らない）。
    """
    by_account: dict = {}
    for change in changes:
        if change.get("state") not in (APPROVED, REVOKED):
            raise ValueError("state は approved か revoked です")
        if not _safe_account(change.get("account")) or not isinstance(change.get("file"), str):
            raise ValueError("account と file が要ります")
        if change["state"] == APPROVED and not change.get("repo_status") and change.get("repo"):
            change = {**change, "repo_status": raw_status(os.path.join(change["repo"], change["file"]))}
        by_account.setdefault(change["account"], []).append(change)
    done = []  # (account, 書く前の items か None＝ファイルが無かった)
    try:
        for account, rows in by_account.items():
            with _locked(account):
                path = path_for(account)
                before = _read(path)
                existed = os.path.exists(path)
                items = dict(before)
                for change in rows:
                    _apply(items, change)
                _save(account, items)
                done.append((account, dict(before) if existed else None))
    except (OSError, ValueError, LedgerError) as exc:
        for account, before in reversed(done):
            try:
                with _locked(account):
                    if before is None:
                        with contextlib.suppress(FileNotFoundError):
                            os.remove(path_for(account))
                    else:
                        _save(account, before)
            except OSError:
                pass
        if isinstance(exc, LedgerError):
            raise
        raise LedgerError(f"承認の台帳に書けません（{exc}）") from None


def export(account_name: str) -> list:
    """`thth approvals` の行（相対パスの順）。読めなければ `LedgerError`。"""
    out = []
    for key, entry in sorted(load(account_name).items()):
        out.append({"file": key, "state": entry["state"], "approved_sha": entry.get("approved_sha"),
                    "at": entry.get("at"), "by": entry.get("by"), "gate": entry.get("gate"),
                    "via": entry.get("via"),
                    "revoked_reason": entry["fields"].get("revoked_reason") if entry["state"] == REVOKED else None,
                    "history": entry.get("history") or []})
    return out
