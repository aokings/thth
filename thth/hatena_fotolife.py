"""はてなフォトライフへ記事の画像を上げる段と、主体ごとの画像の台帳（設計 4.0 S3 §3〜§9）。

**画像は、承認のあと・公開の直前に、timer が同じロックの中で 1 枚ずつ上げる**（記事の POST の前・
`hatena_publish.throw_chosen` の 3c）。承認していないものは外に出ない。

台帳は**主体ごとに 1 画像 1 ファイル** `principal_state/<p>/fotolife/<sha256>.json`（§4）。鍵が中身の
sha256 なので、どの記事・どの配信からでも同じ画像は 1 度しか上げない。使い回す前に毎回
`GET /atom/edit/<id>` で在ることを確かめる（人が画面で消していれば 404 → 台帳を消して上げ直す）。
配信の台帳の `images[]` は「その配信で何を使ったか」の写し（履歴）で、使い回す判断には使わない。

- 台帳は**主体のロックの中でしか書かない**（`core._account_locks` が repo → principal → account の
  順で取ったあとに `throw_chosen` が呼ぶ）。
- 読めない台帳は「無い」とみなさず `fotolife_ledger_unreadable` で止める（二重に上げない）。
- 上げる前に `unknown` の行を先に書き（落ちても次の run が照合できるように）、201 が読めたら
  `uploaded` に直す。確かに断られた（4xx）ら行を消す。結果が分からなければ `unknown` のまま止める。
- `unknown` の画像は、次の run が上げ直す前に `/atom/feed` を読み、題（`<basename> thth:<12 桁>`）が
  一致する写真を探す（§7.2）。見つからなければ上げる（二重の可能性を受け入れる）。
- **参照されなくなった画像は消さない**（4.0 では消す口を作らない・§8・§17 の 1）。
"""
from __future__ import annotations

import datetime
import json
import os
import posixpath
import re

from . import jst, server_files

SCHEMA = "thth.fotolife-image/1"
DIRNAME = "fotolife"
STATES = ("uploaded", "unknown")
SHA_RE = re.compile(r"[0-9a-f]{64}")
FOTOLIFE_ID_RE = re.compile(r"[0-9]{1,20}")
DEFAULT_FOLDER = "thth"
TITLE_BASENAME_MAX = 80
FOLDER_MAX = 64
MiB = 1024 * 1024

# 主体の台帳 `fotolife` の欄（任意）。公式に数が無いので THTH の側の上限（§9）。
LIMIT_DEFAULTS = {
    "max_image_bytes": 8 * MiB,
    "max_images_per_entry": 20,
    "max_total_bytes": 64 * MiB,
    "daily_max_uploads": 30,
    "feed_pages": 1,
}
LIMIT_RANGES = {
    "max_image_bytes": (1 * MiB, 64 * MiB),
    "max_images_per_entry": (1, 100),
    "max_total_bytes": (1 * MiB, 1024 * MiB),   # 設計に範囲が無い。1 MiB〜1 GiB に決めた
    "daily_max_uploads": (1, 500),
    "feed_pages": (1, 10),                      # 設計に範囲が無い。1〜10 に決めた
}

# 差し替え口（`callable(account_cfg, principal_cfg) -> adapter`）。None なら本物
# （`thth.adapters.fotolife.factory`）。試験は `throw_once(fotolife_factory=…)` かここで差し替える。
ADAPTER_FACTORY = None

# 次の run で解けるもの（exit 1）。ほかの断りは人が直すもの（exit 2）。
RETRYABLE = frozenset(("fotolife_ambiguous", "fotolife_check_failed", "provider_rate_limited"))
# 時刻が来れば解ける断り（`hatena_publish.WAITING_REFUSALS` に足す）。
WAITING = frozenset(("fotolife_daily_limit",))


class LedgerUnreadable(Exception):
    """画像の台帳の 1 本が読めない（壊れた JSON・形が違う・置き場が危ない）。"""


class FotolifeStop(Exception):
    """画像の段で止まった（inflight も配信の台帳も書かない）。`code` は符丁。"""

    def __init__(self, code, *, path=None, next_at=None, detail=None):
        super().__init__(code)
        self.code = code
        self.path = path
        self.next_at = next_at
        self.detail = detail

    @property
    def exit_code(self) -> int:
        if self.code in WAITING:
            return 0
        return 1 if self.code in RETRYABLE else 2

    def log_line(self) -> str:
        where = f": {self.path}" if self.path else ""
        if self.code == "fotolife_ambiguous":
            return f"画像の上げの結果が分からないので出しません（次の run で照合します）{where}（{self.detail}）"
        if self.code == "fotolife_check_failed":
            return f"フォトライフの画像を確かめられないので出しません（次の run でもう一度）{where}（{self.detail}）"
        if self.code == "fotolife_daily_limit":
            return f"主体の 1 日の画像の上げの上限に達しました（次は {self.next_at} から）。出しません{where}"
        if self.code == "fotolife_ledger_unreadable":
            return (f"画像の台帳が読めないので出しません（上げ直すと二重になります）{where}。"
                    "thth fotolife <主体> list で確かめてください")
        if self.code == "fotolife_user_mismatch":
            return (f"フォトライフに上がった先が主体のはてな ID と違います（token が別人）。出しません{where}。"
                    "thth principal auth <主体> --force --by <名前> でやり直してください")
        if self.code == "hatena_auth_rejected":
            return (f"フォトライフが認可を断りました{where}。thth principal auth <主体> --force --by <名前> で"
                    "やり直してください（ブログは通るのにフォトライフだけ 401 なら OAuth が通っていない"
                    "可能性があります・設計 S3 §11）")
        if self.code == "fotolife_too_large":
            return f"フォトライフが画像を大きすぎると断りました{where}。画像を小さくするか fotolife.max_image_bytes を下げてください"
        if self.code == "invalid_media_traits":
            return f"媒体の特性の台帳が検査を通らないので出しません{where}。thth media check で確かめてください"
        return f"画像を上げられないので出しません（{self.code}）{where}"


# --------------------------------------------------------------------------
# 上限・フォルダ・題
# --------------------------------------------------------------------------

def valid_limit(key, value) -> bool:
    low, high = LIMIT_RANGES[key]
    return type(value) is int and low <= value <= high


def limits(principal_cfg) -> dict:
    """主体の台帳 `fotolife` の上限（無い・範囲外の欄は既定）。"""
    given = (principal_cfg or {}).get("fotolife")
    given = given if isinstance(given, dict) else {}
    out = {}
    for key, default in LIMIT_DEFAULTS.items():
        value = given.get(key)
        out[key] = value if key in LIMIT_RANGES and valid_limit(key, value) else default
    return out


def daily_max_uploads(principal_cfg) -> int:
    """1 日の上げの上限（柵の表の行 `upload.daily`・段 2）。数えるのはここ（画像の台帳）で、数だけ表から引く。

    主体の台帳 `fotolife.daily_max_uploads` に明示があればそれ、無ければ媒体の台帳（`fotolife.json`）の既定。
    超えたら `fotolife_daily_limit`（`WAITING`・timer の run を失敗にせず次へ＝行の `on_exceed: hold`）。
    """
    from . import limits_policy
    return limits_policy.resolved("upload.daily", principal_cfg=principal_cfg)["count"]


def valid_folder(value) -> bool:
    return (isinstance(value, str) and 1 <= len(value) <= FOLDER_MAX and value == value.strip()
            and "/" not in value and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def folder_for(principal_cfg, blog_id) -> str:
    """上げる先のフォルダ（`blogs[].fotolife_folder`・無ければ `thth`・§10）。"""
    from . import principals
    blog = principals.blog_entry(principal_cfg, blog_id) or {}
    folder = blog.get("fotolife_folder")
    return folder if valid_folder(folder) else DEFAULT_FOLDER


def title_for(path, sha256) -> str:
    """送る題 `<basename> thth:<sha256 の先頭 12 桁>`（§7.2 の照合の鍵・§10）。"""
    name = posixpath.basename(str(path)) or "image"
    return f"{name[:TITLE_BASENAME_MAX]} thth:{sha256[:12]}"


# --------------------------------------------------------------------------
# 台帳
# --------------------------------------------------------------------------

def dir_for(principal) -> str:
    from . import principals
    return os.path.join(principals.state_dir_for(principal), DIRNAME)


def _check_sha(sha256) -> str:
    if not isinstance(sha256, str) or not SHA_RE.fullmatch(sha256):
        raise LedgerUnreadable("fotolife_sha_invalid")
    return sha256


def _validate(record, sha256) -> dict:
    if (type(record) is not dict or record.get("schema") != SCHEMA or record.get("sha256") != sha256
            or record.get("state") not in STATES):
        raise LedgerUnreadable("fotolife_ledger_unreadable")
    if record["state"] == "unknown" and (not isinstance(record.get("title"), str) or not record["title"]):
        raise LedgerUnreadable("fotolife_ledger_unreadable")
    if record["state"] == "uploaded" and (
            not isinstance(record.get("fotolife_id"), str) or not FOTOLIFE_ID_RE.fullmatch(record["fotolife_id"])
            or not isinstance(record.get("syntax"), str) or not record["syntax"]):
        raise LedgerUnreadable("fotolife_ledger_unreadable")
    return record


def read_raw(principal, sha256) -> tuple:
    """`(record | None, raw | None)`。無ければ `(None, None)`。読めなければ `LedgerUnreadable`。"""
    _check_sha(sha256)
    directory = dir_for(principal)
    if not os.path.isdir(directory):
        return None, None
    try:
        with server_files.directory(directory) as fd:
            raw = server_files.read_at(fd, f"{sha256}.json", private=True)
    except FileNotFoundError:
        return None, None
    except (server_files.UnsafeFile, OSError) as exc:
        raise LedgerUnreadable(f"fotolife_ledger_unreadable:{exc.__class__.__name__}") from None
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise LedgerUnreadable("fotolife_ledger_unreadable:json") from None
    return _validate(record, sha256), raw


def read(principal, sha256) -> dict | None:
    return read_raw(principal, sha256)[0]


def _write(principal, record, *, expected=None) -> bytes:
    """1 本を書く（**主体のロックの中で呼ぶ**）。0600・置き場は 0700。"""
    sha256 = _check_sha(record.get("sha256"))
    _validate(record, sha256)
    raw = server_files.encode(record)
    with server_files.directory(dir_for(principal), create=True) as fd:
        server_files.replace_at(fd, f"{sha256}.json", raw, expected=expected, private=True)
    return raw


def _remove(principal, sha256) -> None:
    """1 本を消す（404 で人が画面で消したと分かったとき・確かに断られたときの先書き）。"""
    _check_sha(sha256)
    try:
        with server_files.directory(dir_for(principal)) as fd:
            os.unlink(f"{sha256}.json", dir_fd=fd)
            os.fsync(fd)
    except FileNotFoundError:
        pass


def list_records(principal) -> tuple:
    """`(読めた台帳の並び, 読めなかった sha256 の並び)`（sha256 の順）。置き場が無ければ空。"""
    try:
        names = sorted(os.listdir(dir_for(principal)))
    except FileNotFoundError:
        return [], []
    rows, broken = [], []
    for name in names:
        if not name.endswith(".json") or not SHA_RE.fullmatch(name[:-5]):
            continue
        try:
            record = read(principal, name[:-5])
        except LedgerUnreadable:
            broken.append(name[:-5])
            continue
        if record is not None:
            rows.append(record)
    return rows, broken


def status_of(principal, sha256) -> tuple:
    """`(状態, 台帳)`。状態は `uploaded`・`unknown`・`none`・`unreadable`（HTTP は打たない）。"""
    try:
        record = read(principal, sha256)
    except LedgerUnreadable:
        return "unreadable", None
    if record is None:
        return "none", None
    return record["state"], record


def _day_of(value):
    when = jst.parse(value)
    return jst.to_jst(when).date() if when is not None else None


def uploads_today(principal, now, *, exclude=None) -> int:
    """今日（JST）上げた数: `uploaded_at` が今日の `uploaded`＋`sent_at` が今日の `unknown`（§9）。

    `exclude` はこれから上げ直す画像の sha256（その画像自身の古い `unknown` の行を 2 度数えない）。
    """
    today = jst.to_jst(now).date()
    count = 0
    for record in list_records(principal)[0]:
        if exclude is not None and record.get("sha256") == exclude:
            continue
        stamp = record.get("uploaded_at") if record["state"] == "uploaded" else record.get("sent_at")
        if _day_of(stamp) == today:
            count += 1
    return count


def _next_day(now) -> str:
    local = jst.to_jst(now)
    start = datetime.datetime.combine(local.date() + datetime.timedelta(days=1), datetime.time(0, 0),
                                      tzinfo=local.tzinfo)
    return jst.iso(start)


def _placement(record) -> dict:
    return {key: record.get(key) for key in ("fotolife_id", "syntax", "image_url", "uploaded_at")}


def placements_from_ledger(principal, images) -> dict | None:
    """主体の台帳の `uploaded` の行だけから配置を作る（**HTTP を打たない**・§7.3）。

    1 枚でも台帳に無い（か `unknown`・読めない）なら None。画像が無ければ `{}`。
    """
    out = {}
    for row in images or []:
        try:
            record = read(principal, row["source_sha256"])
        except (LedgerUnreadable, KeyError):
            return None
        if record is None or record["state"] != "uploaded":
            return None
        out[row["path"]] = _placement(record)
    return out


def plan(principal, images) -> tuple:
    """dry-run の見積もり `(画像の数, うち上がっている数)`（台帳を読むだけ）。"""
    uploaded = 0
    for row in images or []:
        state, _record = status_of(principal, row["source_sha256"])
        if state == "uploaded":
            uploaded += 1
    return len(images or []), uploaded


# --------------------------------------------------------------------------
# 上げる（`throw_chosen` の 3c）
# --------------------------------------------------------------------------

def make_adapter(account_cfg, principal_cfg, factory=None):
    chosen = factory or ADAPTER_FACTORY
    if chosen is None:
        from .adapters import fotolife
        chosen = fotolife.factory
    return chosen(account_cfg, principal_cfg)


def _read_image(account_cfg, row, max_bytes) -> tuple:
    """`(bytes, content_type)`。承認した sha256 と違えば `approval_stale`・形式・容量は §9 の符丁。"""
    import hashlib
    from . import hatena_convert, hatena_entry
    try:
        path = hatena_entry.repo_file(account_cfg.get("repo_dir"), row["path"])
        with open(path, "rb") as f:
            data = f.read(max_bytes + 1)
    except (OSError, hatena_entry.EntryError):
        raise FotolifeStop("image_missing", path=row["path"]) from None
    if not data:
        raise FotolifeStop("image_missing", path=row["path"])
    if len(data) > max_bytes:
        raise FotolifeStop("image_too_large", path=row["path"])
    if hashlib.sha256(data).hexdigest() != row["source_sha256"]:
        raise FotolifeStop("approval_stale", path=row["path"])
    content_type = hatena_convert.image_content_type(data[:16])
    if content_type is None:
        raise FotolifeStop("image_type_unsupported", path=row["path"])
    return data, content_type


def _check_stop(exc, path) -> "FotolifeStop":
    """確かめの GET の失敗を止めの符丁に。401・403 は人が直すもの（exit 2・`principal auth --force` の案内）。"""
    if str(exc) == "hatena_auth_rejected":
        return FotolifeStop("hatena_auth_rejected", path=path)
    return FotolifeStop("fotolife_check_failed", path=path, detail=str(exc))


def _id_tail(value) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return value.rsplit("-", 1)[-1]


def ensure_uploaded(principal_cfg, account_cfg, m, *, factory=None, log=None, now=None) -> tuple:
    """`m["images"]` の全部をフォトライフにそろえる。`(配置 {path: {...}}, {"uploaded", "reused"})`。

    止まるときは `FotolifeStop`（呼び手は inflight も配信の台帳も書かない）。この run で
    `uploaded` を書けた分は台帳に残り、次の試みで使い回す（「画像だけ成功」の 1 つ目・§6.4）。
    """
    from .adapters import fotolife
    from .hatena_publish import AdapterUnavailable
    log = log or (lambda _line: None)
    now = now if now is not None else jst.now_jst()
    images = m.get("images") or []
    stats = {"uploaded": 0, "reused": 0}
    if not images:
        return {}, stats
    principal = principal_cfg["principal"]
    hatena_id = principal_cfg.get("hatena_id")
    lim = limits(principal_cfg)
    folder = folder_for(principal_cfg, m.get("blog_id"))
    try:
        adapter = make_adapter(account_cfg, principal_cfg, factory)
    except AdapterUnavailable as exc:
        raise FotolifeStop(exc.code) from None
    first_seen = {"account": m.get("account"), "delivery": m.get("delivery")}
    placements = {}
    for row in images:
        path, sha256 = row["path"], row["source_sha256"]
        try:
            record, raw = read_raw(principal, sha256)
        except LedgerUnreadable:
            raise FotolifeStop("fotolife_ledger_unreadable", path=path) from None

        if record is not None and record["state"] == "uploaded":
            try:
                fields = adapter.get_image(fotolife_id=record["fotolife_id"], edit_uri=record.get("edit_uri"))
            except fotolife.ImageMissing:
                _remove(principal, sha256)   # 人が画面で消した。上げ直す
                log(f"フォトライフの画像が外で消されていたので上げ直します: {path}")
                record, raw = None, None
            except fotolife.CheckFailed as exc:
                raise _check_stop(exc, path) from None
            except Exception as exc:  # noqa: BLE001 — 確かめられなければ出さない（上げ直さない）
                raise FotolifeStop("fotolife_check_failed", path=path, detail=exc.__class__.__name__) from None
            else:
                tail = _id_tail(fields.get("id")) or fields.get("fotolife_id")
                if tail != record["fotolife_id"]:
                    raise FotolifeStop("fotolife_check_failed", path=path, detail="fotolife_id_differs")
                updated = dict(record, checked_at=jst.iso(now))
                _write(principal, updated, expected=raw)
                placements[path] = _placement(updated)
                stats["reused"] += 1
                continue

        if record is not None and record["state"] == "unknown":
            try:
                photos = adapter.recent(pages=lim["feed_pages"])
            except fotolife.CheckFailed as exc:
                raise _check_stop(exc, path) from None
            except Exception as exc:  # noqa: BLE001
                raise FotolifeStop("fotolife_check_failed", path=path, detail=exc.__class__.__name__) from None
            match = next((p for p in photos if p.get("title") == record.get("title")
                          and p.get("fotolife_id") and p.get("syntax")), None)
            if match is not None:
                if fotolife.syntax_user(match["syntax"]) != hatena_id:
                    raise FotolifeStop("fotolife_user_mismatch", path=path)
                found = dict(record, state="uploaded", fotolife_id=match["fotolife_id"], syntax=match["syntax"],
                             image_url=match.get("image_url"),
                             edit_uri=adapter.edit_url(match["fotolife_id"]),
                             uploaded_at=record.get("sent_at") or jst.iso(now), checked_at=jst.iso(now))
                found.pop("error", None)
                _write(principal, found, expected=raw)
                log(f"結果の分からなかった画像がフォトライフの一覧にあったので使い回します: {path}")
                placements[path] = _placement(found)
                stats["reused"] += 1
                continue

        # 上げる（§6）
        data, content_type = _read_image(account_cfg, row, lim["max_image_bytes"])
        try:
            max_uploads = daily_max_uploads(principal_cfg)
        except ValueError:
            # 媒体の台帳が検査を通らない（表を作れない）: 例外で落とさず、ほかの断りと同じ形で止める。
            raise FotolifeStop("invalid_media_traits", path=path) from None
        if uploads_today(principal, now, exclude=sha256) >= max_uploads:
            raise FotolifeStop("fotolife_daily_limit", path=path, next_at=_next_day(now))
        title = title_for(path, sha256)
        pending = {"schema": SCHEMA, "sha256": sha256, "bytes": len(data), "content_type": content_type,
                   "state": "unknown", "title": title, "folder": folder, "sent_at": jst.iso(now),
                   "first_seen": dict(first_seen, path=path)}
        if record is not None and isinstance(record.get("first_seen"), dict):
            pending["first_seen"] = record["first_seen"]
        raw = _write(principal, pending, expected=raw)   # 先に unknown を書く（落ちても照合できる）
        try:
            result = adapter.upload(data=data, content_type=content_type, title=title, folder=folder)
        except Exception as exc:  # noqa: BLE001 — 送ったあとの例外は「上がったか分からない」
            result = fotolife.UploadResult(ok=False, ambiguous=True, error=exc.__class__.__name__)
        if result.ok:
            done = dict(pending, state="uploaded", fotolife_id=result.fotolife_id, syntax=result.syntax,
                        image_url=result.image_url, edit_uri=result.edit_uri, uploaded_at=jst.iso(now),
                        checked_at=jst.iso(now))
            _write(principal, done, expected=raw)
            placements[path] = _placement(done)
            stats["uploaded"] += 1
            continue
        if result.ambiguous:
            _write(principal, dict(pending, error=result.error or "ambiguous"), expected=raw)
            raise FotolifeStop("fotolife_ambiguous", path=path, detail=result.error)
        if record is not None and record["state"] == "unknown":
            # この run の送りは確かに断られたが、前の run の送りは上がっていたかもしれない。
            # 元の unknown の行に戻して記憶を残す（次の run が feed でまた照合する）。
            _write(principal, record, expected=raw)
        else:
            _remove(principal, sha256)   # 確かに出ていない（4xx・送る前の断り）
        raise FotolifeStop(result.error or "fotolife_rejected", path=path)
    return placements, stats
