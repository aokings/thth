"""媒体がアカウント側の断りを返したときに、自動の投稿を止める印（3.19.2）。

10/14 08:33 から kopicha-x の予約投稿が X の `POST /2/tweets` で 403
（`x_not_permitted`・アカウント側の書き込みの制限）を返し続け、timer が 10 分ごとに
同じ承認済み原稿を投げ直して 61 回断られた。断られ続ける書き込みを投げ直すと、
X の制限を長引かせるおそれがある。

ここでは**その断りを 1 回受けたら、その口座の自動の投稿を止める**。止める印は
`state/<account>/publish_hold/held.json`（0600・0700 の置き場・原子的な書き込み）。

- 対象は `x_not_permitted` だけ（`REASONS`）。`x_reply_not_engaged`・
  `x_duplicate_content` は原稿ごとの問題なので今までどおり止めない。ほかの媒体は
  印を置かない（`note_refusal()` が X の口座のときだけ書く）。
- 止まっている間: timer の run（`core.throw_once` の本番）はスレッド連投を含めて
  何も投げない。手の `thth send` と遠くの道の直接の公開も断る（dry-run でも断る・
  inflight と同じ流儀）。読み取り（採取）は止めない。
- 戻すのは人だけ: `thth account resume <口座> --by <名前>`（安全装置の止めと同じ口・
  `account_settings.cmd_resume`）。MCP には出さない。
- 印が読めない（壊れている・置き場のモードが緩い）ときは**止まっている側**に倒す
  （`guard.stopped()` と同じ規律）。

安全装置の止め（`guard`・`state/<account>/guard/stopped.json`）とは別に持つ。あちらの
理由の語（burst・owner）は Worker の `/activity` が知っている語に限られ、増やすと
Worker の deploy が要る。こちらは VM の中だけで読む。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import accounts, jst, server_files

HOLD_FILE = "held.json"
X_NOT_PERMITTED = "x_not_permitted"
REASONS = frozenset((X_NOT_PERMITTED,))
# 印が読めないときの理由（止まっている側に倒す）。
UNREADABLE = "publish_hold_unreadable"
# 公開の断りの形: `x_publish_http_403: x_not_permitted`（文字だけ）・
# `media_publishing_http_403: x_not_permitted`（添付つき）。4xx の後ろに付いた符丁だけを見る。
_REFUSAL = re.compile(r"_http_4[0-9]{2}: (x_not_permitted)\b")


class PublishHeld(Exception):
    """止まっている口座に投稿を頼んだ。"""

    def __init__(self, row):
        super().__init__(row.get("reason") or UNREADABLE)
        self.row = row


def _directory(account) -> Path:
    if not accounts.name_is_safe(account):
        raise ValueError("invalid_account")
    return Path(accounts.state_dir_for(account)) / "publish_hold"


def held(account) -> dict | None:
    """止まっていれば `{"reason", "held_at", ...}`、動いていれば None（読むだけ）。"""
    folder = _directory(account)
    if not os.path.lexists(folder):
        # 一度も止めていない（置き場が無い）。置き場があって読めないときだけ止まっている側に倒す。
        return None
    try:
        with server_files.directory(_directory(account), private=True) as fd:
            raw = server_files.read_at(fd, HOLD_FILE, private=True)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"reason": UNREADABLE}
    try:
        value = json.loads(raw)
    except ValueError:
        return {"reason": UNREADABLE}
    if type(value) is not dict or value.get("reason") not in REASONS:
        return {"reason": UNREADABLE}
    return value


def refusal_code(error) -> str | None:
    """公開の失敗の理由から、口座を止める断りの符丁を引く（無ければ None）。"""
    if not isinstance(error, str):
        return None
    match = _REFUSAL.search(error)
    return match.group(1) if match else None


def hold(account, reason, *, file=None, via="timer", now=None) -> dict:
    """止める（既に止まっていれば先の印を残す）。"""
    if reason not in REASONS:
        raise ValueError("invalid_hold_reason")
    value = {"reason": reason, "held_at": jst.iso(now), "via": via}
    if isinstance(file, str) and file:
        # 原稿名だけ（パスは持たない）。運用通知が同じ原稿の事象として束ねるのに使う。
        value["file"] = os.path.basename(file)
    with server_files.directory(_directory(account), create=True, private=True) as fd:
        try:
            server_files.replace_at(fd, HOLD_FILE, server_files.encode(value), new=True, private=True)
        except server_files.UnsafeFile:
            pass  # 既に止まっている（先に止めた印を消さない）
    try:
        from . import admin_log
        admin_log.append("publish_held", account, accounts.load_account(account), by="thth-publish-hold",
                         via="cli", diff={"publish_held": [False, True], "reason": [None, reason]})
    except Exception:
        pass  # 変更ログに残せなくても止めることは止める（印が正本）
    return value


def note_refusal(account, cfg, error, *, file=None, via="timer", log=None) -> str | None:
    """公開が断られたあとに呼ぶ。止める断りなら印を置いて符丁を返す。

    X の口座だけ。印を置けなかったら（ディスク等）黙らずに言う——次の run がまた
    投げてしまうので。
    """
    if (cfg or {}).get("media") != "x":
        return None
    code = refusal_code(error)
    if code is None:
        return None
    try:
        hold(account, code, file=file, via=via)
    except (OSError, ValueError) as exc:
        if log is not None:
            log(f"自動の投稿を止める印を置けませんでした（{type(exc).__name__}）。"
                "次の run がもう一度投げます")
        return None
    if log is not None:
        log(message(account, {"reason": code}))
    return code


def release(account, *, by, via="cli") -> bool:
    """止めた印を外す（人の口から呼ぶ）。止まっていなければ False。"""
    from . import admin_log
    admin_log.actor(by)
    try:
        with server_files.directory(_directory(account), private=True) as fd:
            try:
                os.unlink(HOLD_FILE, dir_fd=fd)
            except FileNotFoundError:
                return False
            os.fsync(fd)
    except FileNotFoundError:
        return False
    try:
        admin_log.append("publish_resumed", account, accounts.load_account(account), by=by,
                         via=via if via in ("cli", "http") else "cli", diff={"publish_held": [True, False]})
    except Exception:
        pass
    return True


def require_clear(account) -> None:
    """止まっていれば `PublishHeld`。"""
    row = held(account)
    if row is not None:
        raise PublishHeld(row)


def message(account, row) -> str:
    """人向けの 1 行（止まっている理由と戻し方）。"""
    reason = (row or {}).get("reason") or UNREADABLE
    since = (row or {}).get("held_at")
    what = ("X がこのアカウントの書き込みを断りました（アカウント側の制限）"
            if reason == X_NOT_PERMITTED else "自動の投稿を止めた印が読めません（止まっている側に倒しています）")
    return (f"{account}: 自動の投稿を止めています: {what} [{reason}]"
            + (f"（{since} から）" if since else "")
            + f"。X の画面で制限を解いてから thth account resume {account} --by <名前> で再開します")
