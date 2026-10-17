"""媒体の観測を集める（設計「上限と連投の柵」§9.2・段 4・T-M-3）。

**観測は機械が集め、台帳（`thth/media_traits/`）を直すのは人（開発のセッション）。** 数は動かさない（§9.3）。

- 置き場: `$THTH_ROOT/state/_media/<媒体>/observations.ndjson`（0600・追記だけ）。1 行
  `{"at", "media", "account", "kind", "code", "detail", "run_id", "month", "key"}`。
- `kind`: `platform_refusal`（runs の `error` にある媒体の静的な符丁: `x_*`・`provider_rate_limited`・
  `hatena_http_*`）・`human_verification`（`publish_hold` の印）・`display_suppressed` など人が足すもの。
- 材料は今の runs と `publish_hold` だけ。**新しい HTTP は打たない**。
- 冪等: 鍵 `key`（口座・run_id・符丁・月、印なら口座・置いた時刻）が既にある行は足さない。`run_id` の無い行は
  ファイルの中の行の番号を鍵に使う（同じ月・同じ符丁をまとめない）。読み・重ね・追記は `observations.lock` の
  排他ロックの中（2 つの集めが同時に走っても重複を入れない・監査 低-1）。
- runs の行には時刻が無いので、`at` は runs のファイルの月の終わり（今月なら今）——「この時刻までに起きた」。
  印は置いた時刻。`seen_at` に集めた時刻。
"""
from __future__ import annotations

import calendar
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re

from . import accounts as accounts_mod
from . import jst

FILE = "observations.ndjson"
KINDS = ("platform_refusal", "human_verification", "no_reaction", "display_suppressed", "note")
MANUAL_KINDS = ("display_suppressed", "human_verification", "no_reaction", "note")
CODE = re.compile(r"\b(x_[a-z0-9_]+|provider_rate_limited|hatena_http_[0-9]{3}|hatena_auth_rejected)\b")
# 符丁 → 台帳の項目（媒体ごと）。ここに無い符丁は「台帳に無い」印を付ける（直す候補）。
KNOWN = {
    ("x", "x_not_permitted"): "x.human_verification",
    ("x", "x_reply_not_engaged"): "x.reply_requires_mention",
    ("x", "x_duplicate_content"): "x.duplicate_content",
    ("x", "provider_rate_limited"): "x.api_post_rate",
    ("threads", "provider_rate_limited"): "threads.api_posts_24h",
    ("bluesky", "provider_rate_limited"): "bluesky.pds_points",
    ("mastodon", "provider_rate_limited"): "mastodon.api_rate",
    ("hatenabookmark", "provider_rate_limited"): "hatenabookmark.api_no_number",
}
# 台帳に無い形の断りが、この回数（30 日）を超えれば「直す候補」と言う（§4.3 の (2)）。
CANDIDATE_COUNT = 3
MEDIA_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")


class ObservationError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _dir(media) -> str:
    if not isinstance(media, str) or not MEDIA_NAME.match(media):
        raise ObservationError("unknown_media")
    return os.path.join(accounts_mod.thth_root(), "state", "_media", media)


def path_for(media) -> str:
    return os.path.join(_dir(media), FILE)


def read(media) -> list:
    path = path_for(media)
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    out.append(row)
    except FileNotFoundError:
        return []
    return out


def _append(media, rows) -> None:
    if not rows:
        return
    directory = _dir(media)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = os.path.join(directory, FILE)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8"))
    finally:
        os.close(fd)


@contextlib.contextmanager
def _locked(media):
    """媒体の観測の置き場の排他ロック（集めの読み → 重ね → 追記を 1 つに・監査 低-1）。"""
    directory = _dir(media)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(directory, "observations.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _month_end(month, now):
    """runs のファイルの月（`YYYY-MM`）の終わり。今月（か読めない月）なら今。"""
    try:
        year, mon = (int(x) for x in str(month).split("-"))
        last = calendar.monthrange(year, mon)[1]
        end = datetime.datetime(year, mon, last, 23, 59, 59, tzinfo=jst.JST)
    except (ValueError, TypeError):
        return now
    return min(end, now)


def _key(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:24]


def _media_of(cfg) -> str | None:
    media = cfg.get("media")
    return media if isinstance(media, str) else None


def collect(media=None, *, now=None) -> dict:
    """runs と `publish_hold` から観測を足す（冪等）。`{媒体: 足した行数}`。"""
    from . import publish_hold, runs
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    found = {}
    for name in accounts_mod.list_account_names():
        try:
            cfg = accounts_mod.load_account(name)
        except Exception:  # noqa: BLE001 — 読めない口座は飛ばす
            continue
        account_media = _media_of(cfg)
        if account_media is None:
            continue
        state_dir = accounts_mod.state_dir_for(name)
        try:
            files = sorted(f for f in os.listdir(state_dir) if f.startswith("runs-") and f.endswith(".ndjson"))
        except OSError:
            files = []
        for fname in files:
            month = fname[len("runs-"):-len(".ndjson")]
            try:
                with open(os.path.join(state_dir, fname), encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                continue
            for line_no, raw in enumerate(lines, start=1):
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                error = row.get("error") if isinstance(row, dict) else None
                if not isinstance(error, str):
                    continue
                run_ref = row.get("run_id") or f"line:{line_no}"
                for code in dict.fromkeys(CODE.findall(error)):
                    target = "hatenabookmark" if "bookmark" in str(row.get("action") or "") else account_media
                    found.setdefault(target, []).append(
                        {"at": jst.iso(_month_end(month, now)), "seen_at": jst.iso(now), "media": target,
                         "account": name, "kind": "platform_refusal", "code": code, "detail": None,
                         "run_id": row.get("run_id"), "month": month, "key": _key(name, run_ref, code, month)})
        try:
            held = publish_hold.held(name)
        except Exception:  # noqa: BLE001
            held = None
        if isinstance(held, dict) and held.get("held_at"):
            found.setdefault(account_media, []).append(
                {"at": held.get("held_at"), "seen_at": jst.iso(now), "media": account_media, "account": name, "kind": "human_verification",
                 "code": held.get("reason"), "detail": None, "run_id": None, "month": None,
                 "key": _key(name, "publish_hold", held.get("held_at"))})
    out = {}
    for target, rows in found.items():
        if media is not None and target != media:
            continue
        try:
            with _locked(target):
                seen = {r.get("key") for r in read(target)}
                fresh, keys = [], set()
                for r in rows:
                    if r["key"] in seen or r["key"] in keys:
                        continue
                    keys.add(r["key"])
                    fresh.append(r)
                _append(target, fresh)
        except ObservationError:
            continue
        out[target] = len(fresh)
    return out


def observe(media, kind, *, note, record=None, by, now=None) -> dict:
    """人が足す観測（`thth media observe`）。`admin_log` に `media_observed`（語と媒体だけ）。"""
    from . import admin_log, media_traits
    if media not in media_traits.list_media():
        raise ObservationError("unknown_media")
    if kind not in MANUAL_KINDS:
        raise ObservationError("invalid_kind")
    if not isinstance(note, str) or not note.strip() or len(note) > 500 or re.search(r"[\x00-\x1f\x7f]", note):
        raise ObservationError("invalid_note")
    if record is not None and (not isinstance(record, str) or len(record) > 300
                               or re.search(r"[\x00-\x1f\x7f]", record)):
        raise ObservationError("invalid_record")
    admin_log.actor(by)
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    row = {"at": jst.iso(now), "media": media, "account": None, "kind": kind, "code": None, "detail": note.strip(),
           "record": record, "by": by, "run_id": None, "month": None, "key": _key(media, kind, jst.iso(now), note)}
    with _locked(media):
        _append(media, [row])
    try:
        admin_log.append("media_observed", "app-" + media, {"media": media}, by=by, via="cli",
                         diff={"kind": [None, kind]})
    except Exception:  # noqa: BLE001 — 正本は観測のファイル
        pass
    return row


def summary(media, *, since_days=30, now=None) -> dict:
    """符丁ごとの件数・口座・最初と最後・台帳にあるか（直す候補の印）。"""
    now = jst.to_jst(now) if now is not None else jst.now_jst()
    since = now - datetime.timedelta(days=since_days)
    groups = {}
    for r in read(media):
        at = jst.parse(r.get("at"))
        if at is None or at < since:
            continue
        name = r.get("code") or r.get("kind")
        g = groups.setdefault((r.get("kind"), name), {"kind": r.get("kind"), "code": name, "count": 0,
                                                      "accounts": [], "first": r.get("at"), "last": r.get("at")})
        g["count"] += 1
        if r.get("account") and r["account"] not in g["accounts"]:
            g["accounts"].append(r["account"])
        g["first"] = min(g["first"], r.get("at"))
        g["last"] = max(g["last"], r.get("at"))
    rows = []
    for g in sorted(groups.values(), key=lambda g: (-g["count"], str(g["code"]))):
        trait = KNOWN.get((media, g["code"])) if g["kind"] == "platform_refusal" else None
        g["trait"] = trait
        g["in_ledger"] = trait is not None if g["kind"] == "platform_refusal" else None
        g["candidate"] = g["kind"] == "platform_refusal" and trait is None and g["count"] >= CANDIDATE_COUNT
        rows.append(g)
    return {"media": media, "since_days": since_days, "rows": rows,
            "total": sum(g["count"] for g in rows if g["kind"] == "platform_refusal")}


def summary_lines(s) -> list:
    out = [f"{s['media']}: 媒体の観測（{s['since_days']} 日）{len(s['rows'])} 種"]
    for g in s["rows"]:
        where = "・".join(g["accounts"]) or "—"
        mark = ""
        if g["kind"] == "platform_refusal":
            mark = f"  台帳 {g['trait']}" if g["trait"] else "  台帳に無い"
            if g["candidate"]:
                mark += f"（{CANDIDATE_COUNT} 回以上 → 台帳を直す候補）"
        out.append(f"  {g['kind']}  {g['code']}  {g['count']} 件  {where}{mark}")
    return out


def morning_line(*, since_days=30, now=None) -> str | None:
    """`thth morning` の 1 行（媒体の断り・30 日）。集めずに今のファイルを読むだけ。無ければ None。"""
    from . import media_traits
    parts = []
    try:
        names = media_traits.list_media()
    except Exception:  # noqa: BLE001
        return None
    for media in names:
        try:
            s = summary(media, since_days=since_days, now=now)
        except Exception:  # noqa: BLE001
            continue
        for g in s["rows"]:
            if g["kind"] == "platform_refusal":
                parts.append(f"{media} {g['count']} 件 {g['code']}")
    if not parts:
        return None
    return f"媒体の断り（{since_days} 日）: " + "・".join(parts[:6]) + ("…" if len(parts) > 6 else "")
