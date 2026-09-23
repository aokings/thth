"""報告の口（設計 3.1.2）——不具合と要望を**道具の中に**置く。

動機（設計 §0 の前文）: 利用者のセッションの報告が、セッション間メッセージでは
承認待ちのまま期限切れになり（09-21・09-23）、文書を人が運ぶ形になっていた。
**報告は道具の台帳に置き、実装側（管理者）が道具で読む。**

規律:

  (a) **置き場は VM の私有**（`$THTH_ROOT/state/_reports/<report_id>.json`・
      0600・ディレクトリは 0700）。**SNS の台帳には書かない**（設計 §4）。
  (b) **秘密が混ざっていたら置かない**（`redact.looks_like_secret()`）。伏字に
      して置くと置いた側から「どこが消えたか」が見えないので、断って置く側が直す。
  (c) 断るときは**静的な理由コード**（`REASONS`）と次の一手だけ。本文も
      例外の文面も断りの口から出さない。
  (d) 変更ログ（`admin_log`）には `report_filed`・`report_replied`・
      `report_closed`・`report_added`（報告した側の追記・3.2.0）を
      **presence-only** で残す——本文・返事は入れない。
  (e) 利用者 scope は**自分の project の報告だけ**読める（`Scope`）。
      存在しない id と読めない id は同じ `report_not_found` で断る（在る
      ことを漏らさない）。管理者は全部。
  (f) 応答にも書き出しにも**絶対パスを出さない**。本文に `$THTH_ROOT` や
      ホームの絶対パスが含まれていたら、置く時点で `$THTH_ROOT`・`~` に畳む。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import secrets
import sys

from . import accounts, admin_log, jst, private_store, redact
from . import __version__

SCHEMA_VERSION = 1
# `friction`（3.3.0 B4）: 迷った・分かりにくかった・同じ操作を繰り返した。不具合とも
# 要望とも言い切れない小さなつまずき。提案が無くても置ける（本文に何が起きたかだけ）。
KINDS = ("bug", "request", "friction")
KIND_LABELS = {"bug": "不具合", "request": "要望", "friction": "つまずき"}
STATUSES = ("open", "closed")
CLOSE_REASONS = ("fixed", "wontfix", "duplicate", "invalid")
VIAS = ("cli", "mcp")

TITLE_MAX = 120
BODY_MAX = 8000
REPRO_MAX = 4000
REPLY_MAX = 4000
# 1 account が 1 日に置ける件数（直近 24 時間で数える・設計 §2）。
DAILY_LIMIT = 20
WINDOW = datetime.timedelta(hours=24)
# 1 件の上限（本文 8,000 字＋再現 4,000 字＋返事を十分に収める）。これより
# 大きいファイルは壊れているとみなす。
MAX_FILE_BYTES = 1024 * 1024

# `r` + 日付（JST）+ `-` + 乱数 8 字。日付と乱数を `-` で分けるのは、つなげると
# 16 進の長い並びに見えて秘密の検査（32 桁以上の 16 進）と紛らわしくなるため。
REPORT_ID = re.compile(r"r\d{8}-[0-9a-f]{8}\Z")
DIRECTORY = "_reports"

# 断りの理由（**この表の語だけ**を外へ出す）。
REASONS = frozenset((
    "by_required", "invalid_account", "account_unavailable", "invalid_kind",
    "invalid_report", "report_too_long", "secret_detected", "report_rate_limited",
    "duplicate_report", "report_not_found", "report_already_closed",
    "invalid_close_reason", "invalid_version", "invalid_status",
    "report_store_unavailable", "report_log_unavailable", "invalid_export_target",
    # 報告した側の追記（設計 3.2.0 §4.5-1）: 閉じた報告には足せない。
    "report_closed",
    # 直前の断りを添える口（設計 3.3.0 B2）: 控えが無い。
    "no_last_refusal",
    # つまずきの年表（設計 3.6.0 §B）: --since が日付として読めない。
    "invalid_since",
))

# 断りのあとに添える「次の一手」（人と LLM が読む 1 行・静的）。
NEXT = {
    "by_required": "--by <名前> を付けてください（誰が置いたかを残します）",
    "invalid_account": "account 名を確かめてください（thth account <account>）",
    "account_unavailable": "account の台帳が読めないか停止中です（thth account <account>）",
    "invalid_kind": "--kind は bug・request・friction のどれかです（friction は迷った・"
                    "分かりにくかった・同じ操作を繰り返した）",
    "invalid_report": "title と本文は空にできません。title は 1 行です",
    "report_too_long": f"title は {TITLE_MAX} 字・本文は {BODY_MAX} 字・再現手順は {REPRO_MAX} 字"
                       f"・返事は {REPLY_MAX} 字までです。分けるか縮めてください",
    "secret_detected": "秘密らしき値（token・Bearer・sk-・32 桁以上の 16 進 等）が含まれます。"
                       "伏せてから置き直してください（sha は先頭 12 桁までで足ります）",
    "report_rate_limited": f"1 account につき直近 24 時間で {DAILY_LIMIT} 件までです。"
                           "既存の報告に書き足せないときは時間をおいてください",
    "duplicate_report": "同じ title と本文の報告が 24 時間以内にあります。既存の id を見てください",
    "report_not_found": "report_id を確かめてください（thth report list <account|project>）",
    "report_already_closed": "閉じた報告は閉じ直せません。返事は reply で足せます",
    "invalid_close_reason": "--reason は fixed・wontfix・duplicate・invalid のどれかです",
    "invalid_version": "--version は 3.1.2 のような版の番号です",
    "invalid_status": "--status は open・closed・all のどれかです",
    "report_store_unavailable": "報告の置き場が読めません。管理者に知らせてください",
    "report_log_unavailable": "変更ログに書けなかったので置いていません。管理者に知らせてください",
    "invalid_export_target": "--to は書き込めるディレクトリです",
    "report_closed": "閉じた報告には書き足せません。新しく thth report file で置いてください",
    "no_last_refusal": "この account の直前の断りの控えがありません。--body-file で何が起きたかを"
                       "書いて置いてください",
    "invalid_since": "--since は 2026-09-24 のような日付です",
}

# 置く前の似た報告（設計 3.2.0 §4.5-2）: 同じ project の開いている報告から、
# 題の語が 1 つ以上重なるものを最大この件数まで。
SIMILAR_LIMIT = 5
# 題の語 = 空白・記号（`_` を含む）で切った 2 字以上の並び。静的な照合で LLM は使わない。
_TITLE_WORD = re.compile(r"[^\W_]+")
SIMILAR_MIN_CHARS = 2

# **報告は依頼の範囲内と道具が言う**（設計 3.3.0 B1）。固定の 1 文。報告した側の
# LLM は「依頼の範囲外に見える」「小さいことを送ってよいか分からない」ので自分からは
# 置かなかった（報告 r20260923-2f5a1b2d）。skill・使い方・thth_report_file の説明・
# 断りの report_channel 行に同じ文を置く。
WELCOME = ("つまずき・迷い・期待との違いの報告は、利用者の作業の一部として歓迎します。"
           "小さいものも。重複は道具が束ねます")
# 何を報告してほしいかの例（設計 3.3.0 B6）。
EXAMPLES = ("止まったのに気づかなかった", "断られた理由が分からなかった", "同じ操作を 3 回繰り返した")

# **受け口の案内**（設計 3.1.2 §3.5）。静的——account 名も本文も入れない。
# cannot_say・開始手順に載せ、成功には載せない。断りでは `refusal_line()` が
# account と理由の符丁を埋めた「そのまま打てる 1 行」にする（3.3.0 B3）。
CHANNEL_LINE = ("report_channel: thth report file <account> --kind bug|request|friction"
                f"（MCP: thth_report_file）——{WELCOME}")
CHANNEL = {"cli": "thth report file <account> --kind bug|request|friction --title … --body-file … --by <名前>",
           "from_last_refusal": "thth report file <account> --from-last-refusal --title … --by <名前>",
           "mcp": "thth_report_file", "when": "unexpected_or_unsupported",
           "welcome": WELCOME, "examples": list(EXAMPLES)}
_CODE_FOR_LINE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+|exit_[0-9]{1,3}")


def _known_account(account):
    try:
        return (isinstance(account, str) and accounts.name_is_safe(account)
                and account in accounts.list_account_names())
    except accounts.AccountError:
        return False


def _line_code(reason_code):
    return (reason_code if isinstance(reason_code, str) and _CODE_FOR_LINE.fullmatch(reason_code)
            else "<reason_code>")


def refusal_line(account=None, reason_code=None) -> str:
    """断りの後ろに添える「そのまま打てる報告の 1 行」（設計 3.3.0 B3）。

    account は台帳にある名前だけ、理由は静的な符丁だけを埋める（違えば
    `<account>`・`<reason_code>` のまま）。`--by` は誰が置いたかの記録（3.1.2）
    なので名前だけ打ち足す。
    """
    name = account if _known_account(account) else "<account>"
    return (f"report_channel: thth report file {name} --kind friction --from-last-refusal"
            f" --title \"{_line_code(reason_code)} で断られた\" --by <名前>（{WELCOME}）")


def mcp_refusal_line(account=None, reason_code=None) -> str:
    """MCP の断りの 2 つ目の text（B3 の MCP 版）。account が分からなければ静的な 1 行。"""
    if not _known_account(account):
        return CHANNEL_LINE
    return (f"report_channel: thth_report_file {{\"account\": \"{account}\", "
            f"\"from_last_refusal\": true, \"title\": \"{_line_code(reason_code)} で断られた\"}}"
            f"（{WELCOME}）")


class ReportError(ValueError):
    """静的な理由コードだけを持つ断り（`REASONS` のどれか）。"""

    def __init__(self, reason, report_id=None):
        super().__init__(reason if reason in REASONS else "report_store_unavailable")
        self.report_id = report_id


class Scope:
    """利用者が読める範囲（**自分の account と、その project**）。

    `None` を渡す側（管理者の口）は全部を読む。project を持たない account は
    account 名でだけ当てる——`None` 同士を「同じ project」とみなさない。
    """

    def __init__(self, account_names=(), projects=()):
        self.accounts = frozenset(account_names)
        self.projects = frozenset(p for p in projects if isinstance(p, str) and p)

    def allows(self, record) -> bool:
        return (record.get("account") in self.accounts
                or (record.get("project") is not None and record.get("project") in self.projects))


# ------------------------------------------------------------------ 置き場

def _valid(record) -> bool:
    """形の検査（壊れた 1 件で一覧全体を止めない・読めないものは数えて飛ばす）。"""
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
        return False
    if not isinstance(record.get("report_id"), str) or not REPORT_ID.match(record["report_id"]):
        return False
    if record.get("kind") not in KINDS or record.get("status") not in STATUSES:
        return False
    if jst.parse(record.get("at")) is None or not accounts.name_is_safe(record.get("account")):
        return False
    if not isinstance(record.get("title"), str) or not isinstance(record.get("body"), str):
        return False
    replies = record.get("replies")
    if not isinstance(replies, list) or any(
            not isinstance(row, dict) or not isinstance(row.get("text"), str)
            or jst.parse(row.get("at")) is None for row in replies):
        return False
    closed = record.get("closed")
    return closed is None or (isinstance(closed, dict) and closed.get("reason") in CLOSE_REASONS)


# 置き場の骨は `thth/private_store.py`（施策の広場と共通）。ここは報告の口の
# 形の検査・理由コード・id の形だけを渡す。
STORE = private_store.Store(
    DIRECTORY, id_key="report_id", id_pattern=REPORT_ID, valid=_valid, error=ReportError,
    unavailable="report_store_unavailable", not_found="report_not_found",
    log_unavailable="report_log_unavailable", max_bytes=MAX_FILE_BYTES,
    temporary_prefix=".report-")


def _directory(create=False):
    """`$THTH_ROOT/state/_reports` を祖先ごと O_NOFOLLOW で開く（無ければ `None`）。"""
    return STORE.open(create=create)


def _Locked():
    """置き場の排他（件数の上限と重複の検査を、書き込みと同じロックの中で行う）。"""
    return STORE.locked()


def _load_all(directory):
    return STORE.load_all(directory)


def load_all():
    """読むだけの口（ロックは取らない）。置き場がまだ無ければ 0 件。"""
    return STORE.load()


def _write(directory, record):
    STORE.write(directory, record)


def _find(directory, report_id):
    return STORE.find(directory, report_id)


# ------------------------------------------------------------------ 検査

def _text(value, limit, *, required=True, one_line=False):
    """自由文の検査。空・型違い・制御文字は `invalid_report`、長さは `report_too_long`。"""
    return private_store.check_text(value, limit, error=ReportError, invalid="invalid_report",
                                    too_long="report_too_long", required=required,
                                    one_line=one_line)


def _fold_paths(value):
    """本文の中の `$THTH_ROOT` とホームの絶対パスを畳む（応答・書き出しに出さない）。"""
    return private_store.fold_paths(value)


def reporter(by):
    """誰が置いた・返した・閉じたか（`admin_log.actor` と同じ検査）。"""
    try:
        return admin_log.actor(by)
    except ValueError:
        raise ReportError("by_required") from None


def _digest(title, body):
    return hashlib.sha256((title + "\n" + body).encode("utf-8")).hexdigest()


def title_words(title) -> frozenset:
    """題の語（空白・記号で切った 2 字以上の語・英字は大小を畳む）。"""
    return frozenset(word for word in _TITLE_WORD.findall((title or "").casefold())
                     if len(word) >= SIMILAR_MIN_CHARS)


def similar_reports(records, *, title, scope, exclude=None) -> list:
    """同じ範囲（`scope`）の開いている報告のうち、題の語が 1 つ以上重なるもの。

    **置くのは止めない**——似ているかどうかの判断は置く側に任せ、材料だけ返す。
    新しい順に最大 `SIMILAR_LIMIT` 件。本文は見ない（題だけ・静的な照合）。
    """
    words = title_words(title)
    if not words:
        return []
    found = [row for row in records
             if row["status"] == "open" and row["report_id"] != exclude
             and scope.allows(row) and words & title_words(row["title"])]
    found.sort(key=lambda row: (row["at"], row["report_id"]), reverse=True)
    return [{"report_id": row["report_id"], "title": row["title"], "status": row["status"]}
            for row in found[:SIMILAR_LIMIT]]


def _new_id(now, directory):
    for _ in range(8):
        report_id = "r" + now.strftime("%Y%m%d") + "-" + secrets.token_hex(4)
        try:
            os.stat(report_id + ".json", dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return report_id
    raise ReportError("report_store_unavailable")


# ------------------------------------------------------------------ 置く

def file_report(account, *, kind, title, body, repro=None, by, via="cli",
                project=None, medium=None, now=None, trusted=False):
    """報告を 1 件置く。`report_id` を返す。

    `trusted=True` はサーバの口（credential が account と project を決めた）。
    CLI は台帳から project と媒体を読む。**秘密が混ざっていたら置かない。**
    """
    by = reporter(by)
    if via not in VIAS:
        raise ReportError("invalid_report")
    if not accounts.name_is_safe(account):
        raise ReportError("invalid_account")
    if kind not in KINDS:
        raise ReportError("invalid_kind")
    title = _text(title, TITLE_MAX, one_line=True)
    body = _text(body, BODY_MAX)
    repro = _text(repro, REPRO_MAX, required=False)
    cfg = None
    if not trusted:
        try:
            cfg = accounts.load_account(account)
        except accounts.AccountError:
            raise ReportError("account_unavailable") from None
        project, medium = cfg.get("project"), cfg.get("media")
        # 台帳が指す秘密の値そのものを登録簿に控える（綴りの無い貼り付けも当てる）。
        admin_log.register_account_secrets(cfg, via="cli")
    if any(redact.looks_like_secret(value) for value in (title, body, repro, by)):
        raise ReportError("secret_detected")
    title, body, repro = _fold_paths(title), _fold_paths(body), _fold_paths(repro)
    now = now or jst.now_jst()
    digest = _digest(title, body)
    with _Locked() as directory:
        records, _broken = _load_all(directory)
        recent = [row for row in records if row["account"] == account
                  and now - jst.parse(row["at"]) < WINDOW]
        # 重複は**同じ account の中だけ**で見る。全体で見ると、他の project の
        # 報告が在ることを id ごと返してしまう（利用者 scope の漏れ）。
        for row in recent:
            if _digest(row["title"], row["body"]) == digest:
                raise ReportError("duplicate_report", report_id=row["report_id"])
        if len(recent) >= DAILY_LIMIT:
            raise ReportError("report_rate_limited")
        # 似た報告（設計 3.2.0 §4.5-2）。**同じ project の中だけ**（project を
        # 持たない account は account の中だけ）——他の project の報告の題を返さない。
        similar = similar_reports(
            records, title=title, scope=Scope([account], [project] if project else []))
        report_id = _new_id(now, directory)
        record = {"schema_version": SCHEMA_VERSION, "report_id": report_id,
                  "at": jst.iso(now), "kind": kind, "title": title, "body": body,
                  "repro": repro, "project": project, "account": account,
                  "medium": medium, "reporter": by, "via": via,
                  "tool_version": __version__, "status": "open",
                  "replies": [], "closed": None}
        # presence-only（本文は入れない）。書けなければ置いた 1 件を戻す
        # ——記録の無い報告を残さない（`watch_set` と同じ「すべて記録」）。
        STORE.create(directory, record, lambda _record: admin_log.append(
            "report_filed", account, {"media": medium}, by=by, via=via,
            diff={"report": ["absent", "present"]}))
    return {"schema_version": SCHEMA_VERSION, "report_type": "report_filed",
            "report_id": report_id, "status": "open", "kind": kind, "at": record["at"],
            "account": account, "project": project, "tool_version": __version__,
            "similar": similar}


# ------------------------------------------------------------------ 読む

def summary_row(record) -> dict:
    """一覧の 1 行（本文・再現手順・返事の本文は出さない）。"""
    replies = record["replies"]
    return {"report_id": record["report_id"], "at": record["at"], "kind": record["kind"],
            "title": record["title"], "status": record["status"],
            "project": record.get("project"), "account": record["account"],
            "reporter": record.get("reporter"), "n_replies": len(replies),
            "last_reply_at": replies[-1]["at"] if replies else None,
            "closed": record.get("closed")}


def list_reports(*, scope=None, status="open"):
    """報告の一覧。`scope=None` は管理者（全部）。`status` は open・closed・all。"""
    if status not in (*STATUSES, "all"):
        raise ReportError("invalid_status")
    records, broken = load_all()
    visible = [row for row in records if scope is None or scope.allows(row)]
    rows = [summary_row(row) for row in visible if status == "all" or row["status"] == status]
    return {"schema_version": SCHEMA_VERSION, "report_type": "report_list",
            "status_filter": status, "n": len(rows), "denominator": len(visible),
            "open": sum(row["status"] == "open" for row in visible),
            "closed": sum(row["status"] == "closed" for row in visible),
            "reports": rows,
            # 壊れた件数は管理者にだけ（利用者に他 project の件数の手掛かりを渡さない）。
            "unreadable": broken if scope is None else None}


def show(report_id, *, scope=None):
    """1 件の全文（本文・再現手順・返事）。読めない id と無い id は同じ断り。"""
    directory = _directory()
    try:
        record = _find(directory, report_id)
    finally:
        if directory is not None:
            os.close(directory)
    if scope is not None and not scope.allows(record):
        raise ReportError("report_not_found")
    return {"report_type": "report", **record}


def scope_for_account(account):
    """CLI の `report list <account|project>` の範囲（account 名が先）。"""
    if not accounts.name_is_safe(account):
        raise ReportError("invalid_account")
    names = accounts.list_account_names()
    if account in names:
        try:
            project = accounts.load_account(account).get("project")
        except accounts.AccountError:
            raise ReportError("account_unavailable") from None
        return Scope([account], [project] if project else [])
    return Scope([], [account])


# ------------------------------------------------------------ 実装側（管理者）

def _update(report_id, change, *, event, by, via, diff):
    """1 件を書き換えて変更ログに残す。ログに書けなければ元に戻す（記録の無い変更を残さない）。"""
    return STORE.update(report_id, change, lambda record: admin_log.append(
        event, record["account"], {"media": record.get("medium")}, by=by, via=via, diff=diff))


def reply(report_id, *, by, text, via="cli", now=None):
    """実装側の返事を 1 つ足す（閉じた報告にも足せる）。本文と同じく秘密は置かない。"""
    by = reporter(by)
    if via not in VIAS:
        raise ReportError("invalid_report")
    text = _text(text, REPLY_MAX)
    if redact.looks_like_secret(text):
        raise ReportError("secret_detected")
    text = _fold_paths(text)
    at = jst.iso(now or jst.now_jst())

    def change(record):
        record["replies"].append({"at": at, "by": by, "text": text})

    record = _update(report_id, change, event="report_replied", by=by, via=via,
                     diff={"reply": ["absent", "present"]})
    return {"schema_version": SCHEMA_VERSION, "report_type": "report_replied",
            "report_id": record["report_id"], "status": record["status"],
            "n_replies": len(record["replies"]), "at": at}


def add(report_id, *, by, text, scope=None, via="cli", now=None):
    """報告した側の追記（設計 3.2.0 §4.5-1）。**自分の project の開いている報告にだけ。**

    追記は返事と同じ列（`replies`）に `role: "reporter"` を付けて入る——`show` と
    `handoff-report --since-last-read` の `tool.reports` にそのまま出る。上限は
    返事と同じ 4,000 字・秘密の検査・変更ログは `report_added`（presence-only）。
    `scope` の外の報告は、無い報告と同じ `report_not_found`（在ることを漏らさない）。
    閉じた報告は `report_closed`（範囲の中の報告にだけ言う）。
    """
    by = reporter(by)
    if via not in VIAS:
        raise ReportError("invalid_report")
    text = _text(text, REPLY_MAX)
    if redact.looks_like_secret(text):
        raise ReportError("secret_detected")
    text = _fold_paths(text)
    at = jst.iso(now or jst.now_jst())

    def change(record):
        # 範囲を先に見る（範囲の外の報告が閉じているかどうかも漏らさない）。
        if scope is not None and not scope.allows(record):
            raise ReportError("report_not_found")
        if record["status"] == "closed":
            raise ReportError("report_closed")
        record["replies"].append({"at": at, "by": by, "text": text, "role": "reporter"})

    record = _update(report_id, change, event="report_added", by=by, via=via,
                     diff={"reply": ["absent", "present"]})
    return {"schema_version": SCHEMA_VERSION, "report_type": "report_added",
            "report_id": record["report_id"], "status": record["status"],
            "n_replies": len(record["replies"]), "at": at}


def close(report_id, *, by, reason, version, via="cli", now=None):
    """閉じる。理由（fixed・wontfix・duplicate・invalid）と、直した・判断した版を書く。"""
    by = reporter(by)
    if via not in VIAS:
        raise ReportError("invalid_report")
    if reason not in CLOSE_REASONS:
        raise ReportError("invalid_close_reason")
    from . import tool_version
    if tool_version.numbers(version) is None:
        raise ReportError("invalid_version")
    at = jst.iso(now or jst.now_jst())

    def change(record):
        if record["status"] == "closed":
            raise ReportError("report_already_closed")
        record["status"] = "closed"
        record["closed"] = {"at": at, "by": by, "reason": reason, "version": version}

    record = _update(report_id, change, event="report_closed", by=by, via=via,
                     diff={"status": ["open", "closed"]})
    return {"schema_version": SCHEMA_VERSION, "report_type": "report_closed",
            "report_id": record["report_id"], "status": "closed", "closed": record["closed"]}


def _slug(title):
    """ファイル名に使う題（英数字・かな漢字・`-`・`_` だけ・40 字まで）。"""
    slug = re.sub(r"[^\w-]+", "_", title).strip("_-")[:40].strip("_-")
    return slug or "report"


def _fence(text):
    """本文をそのまま見せる囲み（本文の中の ``` より長い囲みを使う）。"""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    return "`" * max(3, longest + 1)


def render_markdown(record, *, by):
    """書き出しの 1 件（私有の置き場で git が追う形）。**絶対パスも秘密も入れない**
    ——本文は置く時点で秘密を断り、`$THTH_ROOT`・ホームを畳んである。"""
    lines = [f"# {record['title']}", "",
             f"- report_id: `{record['report_id']}`",
             f"- 種類: {record['kind']}（{KIND_LABELS[record['kind']]}）",
             f"- 状態: {record['status']}",
             f"- 置いた時刻: {record['at']}",
             f"- project: {record.get('project') or '—'} / account: {record['account']}"
             f"（{record.get('medium') or '—'}）",
             f"- 置いた人: {record.get('reporter')}（via {record.get('via')}）",
             f"- 置いた時点の版: {record.get('tool_version')}"]
    closed = record.get("closed")
    if closed:
        lines.append(f"- 閉じた: {closed['at']} {closed.get('by')}（{closed['reason']}・"
                     f"版 {closed.get('version')}）")
    for heading, text in (("本文", record["body"]), ("再現手順", record.get("repro"))):
        lines += ["", f"## {heading}", ""]
        if text:
            fence = _fence(text)
            lines += [fence + "text", text, fence]
        else:
            lines.append("（なし）")
    lines += ["", f"## 返事（{len(record['replies'])} 件）"]
    for row in record["replies"]:
        fence = _fence(row["text"])
        role = "（報告した側の追記）" if row.get("role") == "reporter" else ""
        lines += ["", f"### {row['at']} {row.get('by')}{role}", "", fence + "text", row["text"], fence]
    lines += ["", "---", "",
              f"書き出し: `thth admin reports export`（by {by}）。返事は "
              f"`thth admin reports reply {record['report_id']} --by <名前> --text-file <file>`、"
              f"閉じるときは `thth admin reports close {record['report_id']} --by <名前> "
              "--reason fixed|wontfix|duplicate|invalid --version <版>`。"]
    return "\n".join(lines) + "\n"


def export(to, *, by):
    """開いている報告を 1 件 1 ファイルで書き出す。閉じた報告は、前に書き出した
    ファイルがあるときだけ閉じた形に書き直す（写しを実物に揃える）。

    **行き先は呼ぶ側が必ず選ぶ**（既定の行き先を持たない・masaru 裁定 09-23）。
    THTH の repo は公開なので、利用者が書いた本文を `docs/報告/` に置くと公開される
    ——私有の置き場へ書き出す。

    応答には**書き出し先の絶対パスを返さない**（ファイル名だけ）。
    """
    by = reporter(by)
    if not isinstance(to, str) or not to.strip():
        raise ReportError("invalid_export_target")
    target = os.path.abspath(to)
    try:
        os.makedirs(target, exist_ok=True)
        if os.path.islink(target) or not os.path.isdir(target) or not os.access(target, os.W_OK):
            raise ReportError("invalid_export_target")
        existing = os.listdir(target)
    except OSError:
        raise ReportError("invalid_export_target") from None
    records, broken = load_all()
    written, updated = [], []
    for record in records:
        name = f"{record['report_id']}_{_slug(record['title'])}.md"
        previous = [entry for entry in existing if entry.startswith(record["report_id"] + "_")
                    and entry.endswith(".md")]
        if record["status"] != "open" and not previous:
            continue
        try:
            for stale in previous:
                if stale != name:
                    os.unlink(os.path.join(target, stale))
            path = os.path.join(target, name)
            if os.path.islink(path):
                raise ReportError("invalid_export_target")
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(render_markdown(record, by=by))
        except OSError:
            raise ReportError("invalid_export_target") from None
        (written if record["status"] == "open" else updated).append(name)
    return {"schema_version": SCHEMA_VERSION, "report_type": "report_export",
            "n_open": len(written), "n_closed_updated": len(updated),
            "files": sorted(written + updated), "unreadable": broken}


# ------------------------------------------------------------ 開始手順に載せる

def handoff_summary(configs, read_ats):
    """`handoff-report --since-last-read` の `tool.reports`（設計 §3・§3.5）。

    読むのは**その呼び出しの account と、その project の報告だけ**（`configs` は
    既に呼び出しの範囲に絞られている）。「新しい返事」は前回の栞（account ごとの
    `read_at` の最も古いもの）より後の返事。栞が 1 つも無ければ全部を新しいとみなす
    ——見落とすより重ねて見せる。
    """
    names = set(configs)
    projects = {cfg.get("project") for cfg in configs.values() if cfg.get("project")}
    scope = Scope(names, projects)
    parsed = [jst.parse(value) for value in read_ats if value]
    since = min(parsed) if parsed else None
    try:
        records, _broken = load_all()
    except ReportError as error:
        return {"open": None, "closed": None, "denominator": None, "replies": None,
                "new_replies": None, "since": None, "cannot_say": str(error)}
    visible = [row for row in records if scope.allows(row)]
    new = [{"report_id": row["report_id"], "title": row["title"], "status": row["status"],
            "at": reply_row["at"], "by": reply_row.get("by"), "text": reply_row["text"],
            # 報告した側の追記は `reporter`、実装側の返事は `implementer`（3.2.0）。
            "role": reply_row.get("role") or "implementer"}
           for row in visible for reply_row in row["replies"]
           if since is None or jst.parse(reply_row["at"]) > since]
    return {"open": sum(row["status"] == "open" for row in visible),
            "closed": sum(row["status"] == "closed" for row in visible),
            "denominator": len(visible),
            "replies": sum(len(row["replies"]) for row in visible),
            "new_replies": new, "since": jst.iso(since) if since else None,
            "cannot_say": None}


def project_summary(scope, version=None):
    """「あなたの project の報告」（設計 3.3.0 B5・効いたことを見せる）。

    `scope` の報告のうち開いている件数と、**この版で閉じた**報告（`admin reports close
    --version` の版がいまの道具の版と一致するもの）の id・題・種類・閉じた理由。
    報告した側の LLM が「効いた実感が無い」と言ったので、置いた報告が直ったことを
    毎朝の一枚の 0 段で見せる。本文は出さない。
    """
    version = version or __version__
    try:
        records, _broken = load_all()
    except ReportError as error:
        return {"open": None, "closed_this_version": None, "m": None, "denominator": None,
                "version": version, "cannot_say": str(error)}
    visible = [row for row in records if scope.allows(row)]
    closed = [row for row in visible if row["status"] == "closed"
              and (row.get("closed") or {}).get("version") == version]
    closed.sort(key=lambda row: ((row.get("closed") or {}).get("at") or "", row["report_id"]))
    return {"open": sum(row["status"] == "open" for row in visible),
            "closed_this_version": [
                {"report_id": row["report_id"], "title": row["title"], "kind": row["kind"],
                 "reason": (row.get("closed") or {}).get("reason")} for row in closed],
            "m": len(closed), "denominator": len(visible), "version": version,
            "cannot_say": None}


def morning_summary(now):
    """管理者の毎朝の一枚の 0 段「道具」に載せる件数（設計 §3）。

    「新規」は直近 24 時間に置かれて、まだ開いている報告。
    """
    try:
        records, broken = load_all()
    except ReportError as error:
        return {"open": None, "new": None, "denominator": None, "new_window_hours": 24,
                "unreadable": None, "next": None, "cannot_say": str(error)}
    opened = [row for row in records if row["status"] == "open"]
    new = [row for row in opened if now - jst.parse(row["at"]) < WINDOW]
    return {"open": len(opened), "new": len(new), "denominator": len(records),
            "new_window_hours": 24, "unreadable": broken,
            "next": "thth admin reports list --status open" if opened else None,
            "cannot_say": None}


# ------------------------------------------------------------------ CLI

def _print_refusal(args, error):
    reason = str(error)
    print(f"{reason}: {NEXT.get(reason, '')}".rstrip(": "), file=sys.stderr)
    if getattr(args, "json", False):
        payload = {"cannot_say": [reason], "report_channel": CHANNEL}
        if error.report_id:
            payload["report_id"] = error.report_id
        print(json.dumps(payload, ensure_ascii=False))
    elif error.report_id:
        print(f"既存の報告: {error.report_id}", file=sys.stderr)
    return 2


def _read_input(path):
    """`--body-file` 等を読む（`-` は標準入力）。読めなければ `invalid_report`。"""
    return private_store.read_input(path, BODY_MAX, error=ReportError, invalid="invalid_report")


def from_last_refusal(account, *, kind=None, body=None, repro=None):
    """直前の断りを再現手順に添える（設計 3.3.0 B2）。`(kind, body, repro)` を返す。

    **その account の控えだけ**を読む（`refusals.latest()`）。種類を書かなければ
    `friction`、本文を書かなければ静的な 1 文。自分で書いた再現手順は控えの後ろに
    足す。控えが無ければ `no_last_refusal`（推測で埋めない）。
    """
    from . import refusals
    if not accounts.name_is_safe(account):
        raise ReportError("invalid_account")
    row = refusals.latest(account)
    if row is None:
        raise ReportError("no_last_refusal")
    auto = refusals.repro_text(row)
    if isinstance(repro, str) and repro.strip():
        auto = auto + "\n\n" + repro
    return (kind or "friction",
            body if isinstance(body, str) and body.strip() else refusals.DEFAULT_BODY, auto)


def cmd_file(args) -> int:
    try:
        reporter(args.by)
        kind, body, repro = args.kind, _read_input(args.body_file), _read_input(args.repro_file)
        if getattr(args, "from_last_refusal", False):
            kind, body, repro = from_last_refusal(args.account, kind=kind, body=body, repro=repro)
        elif kind is None:
            raise ReportError("invalid_kind")
        result = file_report(args.account, kind=kind, title=args.title,
                             body=body, repro=repro, by=args.by, via="cli")
    except ReportError as error:
        return _print_refusal(args, error)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"報告を置きました: {result['report_id']}（{KIND_LABELS[result['kind']]}・"
              f"{result['account']}・版 {result['tool_version']}）")
        print(f"返事は thth report show {result['report_id']} と "
              "handoff-report --since-last-read の tool.reports に出ます")
        if result.get("similar"):
            # 置いたあとで重なりに気づけるように（置くのは止めない・3.2.0 §4.5-2）。
            print("似た報告: " + "・".join(
                f"{row['report_id']}（{row['title']}）" for row in result["similar"])
                + "——同じ件なら thth report add <id> で書き足せます")
    return 0


def cmd_add(args) -> int:
    """`thth report add <report_id> --body-file <path|->`（設計 3.2.0 §4.5-1）。"""
    try:
        reporter(args.by)
        result = add(args.report_id, by=args.by, text=_read_input(args.body_file), via="cli")
    except ReportError as error:
        return _print_refusal(args, error)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"書き足しました: {result['report_id']}（返事と追記 {result['n_replies']} 件）")
    return 0


def render_list(payload, out=print):
    out(f"報告 {payload['n']}/{payload['denominator']} 件（{payload['status_filter']}）"
        f"  開いている {payload['open']}・閉じた {payload['closed']}")
    for row in payload["reports"]:
        closed = row["closed"]
        tail = f"  閉じた: {closed['reason']}（{closed.get('version') or '—'}）" if closed else ""
        out(f"  {row['report_id']}  {KIND_LABELS[row['kind']]}  {row['status']}"
            f"  {row['account']}  置いた人 {row.get('reporter') or '—'}"
            f"  返事 {row['n_replies']}  {row['title']}{tail}")
    if payload.get("unreadable"):
        out(f"  読めない報告: {payload['unreadable']} 件")


def render_report(record, out=print):
    out(f"{record['report_id']}  {KIND_LABELS[record['kind']]}  {record['status']}")
    out(f"題: {record['title']}")
    out(f"置いた: {record['at']}  {record.get('reporter')}（{record.get('via')}）"
        f"  {record['account']}（{record.get('project') or '—'}・{record.get('medium') or '—'}）"
        f"  版 {record.get('tool_version')}")
    out("")
    out(record["body"])
    if record.get("repro"):
        out("")
        out("[再現手順]")
        out(record["repro"])
    out("")
    out(f"[返事 {len(record['replies'])} 件]")
    for row in record["replies"]:
        role = "（報告した側の追記）" if row.get("role") == "reporter" else ""
        out(f"- {row['at']}  {row.get('by')}{role}")
        for line in row["text"].splitlines():
            out(f"  {line}")
    closed = record.get("closed")
    if closed:
        out(f"[閉じた] {closed['at']}  {closed.get('by')}  {closed['reason']}"
            f"  版 {closed.get('version') or '—'}")


def cmd_list(args) -> int:
    try:
        payload = list_reports(scope=scope_for_account(args.target), status=args.status)
    except ReportError as error:
        return _print_refusal(args, error)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        render_list(payload)
    return 0


def cmd_show(args) -> int:
    try:
        record = show(args.report_id)
    except ReportError as error:
        return _print_refusal(args, error)
    if args.json:
        print(json.dumps(record, ensure_ascii=False))
    else:
        render_report(record)
    return 0


def register(sub) -> None:
    parser = sub.add_parser(
        "report",
        help="不具合・要望・つまずきを道具に置く（道具が断った・結果が期待と違った・欲しい形がある・迷った）",
        description=f"{WELCOME}。例: " + "・".join(f"「{e}」" for e in EXAMPLES) + "。"
                    "不具合・要望・つまずき（friction）を道具の中に置く（設計 3.1.2・3.3.0）。"
                    "道具が断った・結果が期待と違った・欲しい形がある・迷った、のどれかならここへ。"
                    "断られた直後なら --from-last-refusal で道具が控えた断りを添えられる。実装側は "
                    "`thth admin reports` と毎朝の一枚で読み、返事は "
                    "`thth report show <id>` と handoff-report --since-last-read に出ます。"
                    "秘密らしき値が含まれていたら置きません。",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    operations = parser.add_subparsers(dest="report_command", required=True)
    filer = operations.add_parser(
        "file", help="報告を 1 件置く（report_id が返る）",
        description=f"報告を 1 件置く。title は {TITLE_MAX} 字・本文は {BODY_MAX} 字・"
                    f"再現手順は {REPRO_MAX} 字まで。1 account につき直近 24 時間で "
                    f"{DAILY_LIMIT} 件まで。同じ title と本文は 24 時間以内なら既存の id を返します。")
    filer.add_argument("account")
    filer.add_argument("--kind", default=None, choices=KINDS,
                       help="bug・request・friction（--from-last-refusal のときの既定は friction）")
    filer.add_argument("--title", required=True)
    filer.add_argument("--body-file", default=None, dest="body_file",
                       help="本文のファイル（VM 側のパス。`-` は標準入力。"
                            "--from-last-refusal のときは省略できる）")
    filer.add_argument("--from-last-refusal", action="store_true", dest="from_last_refusal",
                       help="この account で直前に道具が断った記録（時刻・版・命令・理由の符丁）を"
                            "再現手順として添える（本文・引数の値は控えていない）")
    filer.add_argument("--repro-file", default=None, dest="repro_file",
                       help="再現手順のファイル（任意）")
    filer.add_argument("--by", default=None, help="誰が置いたか（必須）")
    filer.add_argument("--json", action="store_true")
    filer.set_defaults(func=cmd_file)
    lister = operations.add_parser(
        "list", help="自分の project の報告と返事の数を読む（読むだけ）")
    lister.add_argument("target", metavar="account|project")
    lister.add_argument("--status", default="all", choices=(*STATUSES, "all"))
    lister.add_argument("--json", action="store_true")
    lister.set_defaults(func=cmd_list)
    viewer = operations.add_parser("show", help="報告 1 件と実装側の返事を読む（読むだけ）")
    viewer.add_argument("report_id")
    viewer.add_argument("--json", action="store_true")
    viewer.set_defaults(func=cmd_show)
    adder = operations.add_parser(
        "add", help="自分の開いている報告に書き足す（報告した側の追記・閉じた報告には足せない）",
        description=f"開いている報告に報告した側から書き足す（{REPLY_MAX} 字まで・秘密らしき値が"
                    "含まれていたら足さない）。返事と同じ列に並び、thth report show と "
                    "handoff-report --since-last-read に出ます。")
    adder.add_argument("report_id")
    adder.add_argument("--body-file", required=True, dest="body_file",
                       help="書き足す文のファイル（VM 側のパス。`-` は標準入力）")
    adder.add_argument("--by", default=None, help="誰が書き足したか（必須）")
    adder.add_argument("--json", action="store_true")
    adder.set_defaults(func=cmd_add)
    # つまずきの年表（設計 3.6.0 §B）。読むだけ。
    from . import report_timeline
    report_timeline.register(operations)


# ------------------------------------------------------------ 管理者の CLI

def cmd_admin_list(args) -> int:
    try:
        payload = list_reports(scope=None, status=args.status)
    except ReportError as error:
        return _print_refusal(args, error)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        render_list(payload)
    return 0


def cmd_admin_reply(args) -> int:
    try:
        reporter(args.by)
        result = reply(args.report_id, by=args.by, text=_read_input(args.text_file), via="cli")
    except ReportError as error:
        return _print_refusal(args, error)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"返事を足しました: {result['report_id']}（返事 {result['n_replies']} 件）")
    return 0


def cmd_admin_close(args) -> int:
    try:
        reporter(args.by)
        result = close(args.report_id, by=args.by, reason=args.reason, version=args.version, via="cli")
    except ReportError as error:
        return _print_refusal(args, error)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        closed = result["closed"]
        print(f"閉じました: {result['report_id']}（{closed['reason']}・版 {closed['version']}）")
    return 0


def cmd_admin_export(args) -> int:
    try:
        result = export(args.to, by=args.by)
    except ReportError as error:
        return _print_refusal(args, error)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"書き出しました: 開いている {result['n_open']} 件・閉じた形に書き直し "
              f"{result['n_closed_updated']} 件")
        for name in result["files"]:
            print(f"  {name}")
    return 0


def register_admin(commands) -> None:
    """`thth admin reports list|show|reply|close|export`（設計 3.1.2 §1）。"""
    parser = commands.add_parser(
        "reports", help="報告の口（全 project の不具合と要望を読み、返事と状態を書く）",
        description="全 project の報告を読み、返事と状態を書く（設計 3.1.2）。"
                    "返事は報告した側の handoff-report --since-last-read に出ます。")
    operations = parser.add_subparsers(dest="reports_command", required=True)
    lister = operations.add_parser("list", help="報告の一覧（既定は開いているものだけ）")
    lister.add_argument("--status", default="open", choices=(*STATUSES, "all"))
    lister.add_argument("--json", action="store_true")
    lister.set_defaults(func=cmd_admin_list)
    viewer = operations.add_parser("show", help="報告 1 件の全文と返事")
    viewer.add_argument("report_id")
    viewer.add_argument("--json", action="store_true")
    viewer.set_defaults(func=cmd_show)
    replier = operations.add_parser(
        "reply", help="返事を 1 つ足す（秘密らしき値が含まれていたら足さない）")
    replier.add_argument("report_id")
    replier.add_argument("--by", default=None, help="誰が返したか（必須）")
    replier.add_argument("--text-file", required=True, dest="text_file",
                         help=f"返事のファイル（`-` は標準入力・{REPLY_MAX} 字まで）")
    replier.add_argument("--json", action="store_true")
    replier.set_defaults(func=cmd_admin_reply)
    closer = operations.add_parser("close", help="閉じる（理由と版を書く）")
    closer.add_argument("report_id")
    closer.add_argument("--by", default=None, help="誰が閉じたか（必須）")
    closer.add_argument("--reason", required=True, choices=CLOSE_REASONS)
    closer.add_argument("--version", required=True, help="直した・判断した版（例 3.1.2）")
    closer.add_argument("--json", action="store_true")
    closer.set_defaults(func=cmd_admin_close)
    exporter = operations.add_parser(
        "export", help="開いている報告を Markdown で私有の置き場へ書き出す（1 件 1 ファイル）",
        description="開いている報告を <report_id>_<題>.md で書き出します。前に書き出した報告が"
                    "閉じていれば閉じた形に書き直します。**私有の置き場へ。公開 repo には置かない**"
                    "（本文は利用者が書いた文です。既定の行き先はありません）。")
    exporter.add_argument("--to", required=True,
                          help="書き出し先の私有のディレクトリ（公開 repo には置かない）")
    exporter.add_argument("--by", default=None, help="誰が書き出したか（必須）")
    exporter.add_argument("--json", action="store_true")
    exporter.set_defaults(func=cmd_admin_export)
