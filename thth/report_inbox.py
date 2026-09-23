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
      `report_closed` を **presence-only** で残す——本文・返事は入れない。
  (e) 利用者 scope は**自分の project の報告だけ**読める（`Scope`）。
      存在しない id と読めない id は同じ `report_not_found` で断る（在る
      ことを漏らさない）。管理者は全部。
  (f) 応答にも書き出しにも**絶対パスを出さない**。本文に `$THTH_ROOT` や
      ホームの絶対パスが含まれていたら、置く時点で `$THTH_ROOT`・`~` に畳む。
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import uuid

from . import accounts, admin_log, handoff_cursor, jst, redact
from . import __version__

SCHEMA_VERSION = 1
KINDS = ("bug", "request")
KIND_LABELS = {"bug": "不具合", "request": "要望"}
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
))

# 断りのあとに添える「次の一手」（人と LLM が読む 1 行・静的）。
NEXT = {
    "by_required": "--by <名前> を付けてください（誰が置いたかを残します）",
    "invalid_account": "account 名を確かめてください（thth account <account>）",
    "account_unavailable": "account の台帳が読めないか停止中です（thth account <account>）",
    "invalid_kind": "--kind は bug か request です",
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
}

# **受け口の案内**（設計 §3.5）。文面は 1 種類・静的——account 名も本文も
# 入れない。断り・cannot_say・開始手順の 3 場面だけに載せ、成功には載せない。
CHANNEL_LINE = "report_channel: thth report file <account> --kind bug|request（MCP: thth_report_file）"
CHANNEL = {"cli": "thth report file <account> --kind bug|request --title … --body-file … --by <名前>",
           "mcp": "thth_report_file", "when": "unexpected_or_unsupported"}


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

def _directory(create=False):
    """`$THTH_ROOT/state/_reports` を祖先ごと O_NOFOLLOW で開く（fd を返す）。"""
    try:
        return handoff_cursor._directory(DIRECTORY, create=create)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ReportError("report_store_unavailable") from exc


class _Locked:
    """置き場の排他（件数の上限と重複の検査を、書き込みと同じロックの中で行う）。"""

    def __enter__(self):
        self.directory = _directory(create=True)
        try:
            self.fd = os.open(".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600,
                              dir_fd=self.directory)
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except OSError as exc:
            os.close(self.directory)
            raise ReportError("report_store_unavailable") from exc
        return self.directory

    def __exit__(self, *exc):
        try:
            os.close(self.fd)
        finally:
            os.close(self.directory)
        return False


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


def _read_one(directory, name):
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("not_regular")
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("too_large")
    record = json.loads(data)
    if not _valid(record) or record["report_id"] + ".json" != name:
        raise ValueError("invalid_record")
    return record


def _load_all(directory):
    """置き場の全件（壊れた件数も返す）。"""
    records, broken = [], 0
    if directory is None:
        return records, broken
    try:
        names = sorted(os.listdir(directory))
    except OSError as exc:
        raise ReportError("report_store_unavailable") from exc
    for name in names:
        if not name.endswith(".json") or not REPORT_ID.match(name[:-5]):
            continue
        try:
            records.append(_read_one(directory, name))
        except (OSError, ValueError, TypeError, RecursionError):
            broken += 1
    records.sort(key=lambda row: (row["at"], row["report_id"]))
    return records, broken


def load_all():
    """読むだけの口（ロックは取らない）。置き場がまだ無ければ 0 件。"""
    directory = _directory()
    try:
        return _load_all(directory)
    finally:
        if directory is not None:
            os.close(directory)


def _write(directory, record):
    temporary = ".report-" + uuid.uuid4().hex
    name = record["report_id"] + ".json"
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
                     dir_fd=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream, ensure_ascii=False, allow_nan=False, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    except OSError as exc:
        raise ReportError("report_store_unavailable") from exc
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except OSError:
            pass


def _find(directory, report_id):
    if not isinstance(report_id, str) or not REPORT_ID.match(report_id):
        raise ReportError("report_not_found")
    if directory is None:
        raise ReportError("report_not_found")
    try:
        return _read_one(directory, report_id + ".json")
    except FileNotFoundError:
        raise ReportError("report_not_found") from None
    except (OSError, ValueError, TypeError, RecursionError):
        raise ReportError("report_store_unavailable") from None


# ------------------------------------------------------------------ 検査

def _text(value, limit, *, required=True, one_line=False):
    """自由文の検査。空・型違い・制御文字は `invalid_report`、長さは `report_too_long`。"""
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ReportError("invalid_report")
    value = value.replace("\r\n", "\n").strip()
    if not value:
        if required:
            raise ReportError("invalid_report")
        return None
    if one_line and "\n" in value:
        raise ReportError("invalid_report")
    if any(ord(c) < 32 and c not in "\n\t" for c in value) or "\x7f" in value:
        raise ReportError("invalid_report")
    if len(value) > limit:
        raise ReportError("report_too_long")
    return value


def _fold_paths(value):
    """本文の中の `$THTH_ROOT` とホームの絶対パスを畳む（応答・書き出しに出さない）。"""
    if value is None:
        return None
    candidates = []
    for raw in (accounts.thth_root(), os.path.realpath(accounts.thth_root())):
        if raw and os.path.isabs(raw) and raw != os.sep:
            candidates.append((raw.rstrip(os.sep), "$THTH_ROOT"))
    home = os.path.expanduser("~")
    for raw in (home, os.path.realpath(home)):
        if raw and os.path.isabs(raw) and raw != os.sep:
            candidates.append((raw.rstrip(os.sep), "~"))
    # 長い方から（ホームの下に root があると、先にホームを畳むと root が残る）。
    for raw, mark in sorted(set(candidates), key=lambda item: len(item[0]), reverse=True):
        value = value.replace(raw, mark)
    return value


def reporter(by):
    """誰が置いた・返した・閉じたか（`admin_log.actor` と同じ検査）。"""
    try:
        return admin_log.actor(by)
    except ValueError:
        raise ReportError("by_required") from None


def _digest(title, body):
    return hashlib.sha256((title + "\n" + body).encode("utf-8")).hexdigest()


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
        report_id = _new_id(now, directory)
        record = {"schema_version": SCHEMA_VERSION, "report_id": report_id,
                  "at": jst.iso(now), "kind": kind, "title": title, "body": body,
                  "repro": repro, "project": project, "account": account,
                  "medium": medium, "reporter": by, "via": via,
                  "tool_version": __version__, "status": "open",
                  "replies": [], "closed": None}
        _write(directory, record)
        try:
            # presence-only（本文は入れない）。書けなければ置いた 1 件を戻す
            # ——記録の無い報告を残さない（`watch_set` と同じ「すべて記録」）。
            admin_log.append("report_filed", account, {"media": medium}, by=by, via=via,
                             diff={"report": ["absent", "present"]})
        except (OSError, ValueError, admin_log.AdminLogError):
            try:
                os.unlink(report_id + ".json", dir_fd=directory)
            except OSError:
                pass
            raise ReportError("report_log_unavailable") from None
    return {"schema_version": SCHEMA_VERSION, "report_type": "report_filed",
            "report_id": report_id, "status": "open", "kind": kind, "at": record["at"],
            "account": account, "project": project, "tool_version": __version__}


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
    if path is None:
        return None
    try:
        if path == "-":
            return sys.stdin.read(BODY_MAX * 4 + 1)
        with open(path, encoding="utf-8") as stream:
            return stream.read(BODY_MAX * 4 + 1)
    except (OSError, UnicodeError):
        raise ReportError("invalid_report") from None


def cmd_file(args) -> int:
    try:
        reporter(args.by)
        result = file_report(args.account, kind=args.kind, title=args.title,
                             body=_read_input(args.body_file),
                             repro=_read_input(args.repro_file), by=args.by, via="cli")
    except ReportError as error:
        return _print_refusal(args, error)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"報告を置きました: {result['report_id']}（{KIND_LABELS[result['kind']]}・"
              f"{result['account']}・版 {result['tool_version']}）")
        print(f"返事は thth report show {result['report_id']} と "
              "handoff-report --since-last-read の tool.reports に出ます")
    return 0


def render_list(payload, out=print):
    out(f"報告 {payload['n']}/{payload['denominator']} 件（{payload['status_filter']}）"
        f"  開いている {payload['open']}・閉じた {payload['closed']}")
    for row in payload["reports"]:
        closed = row["closed"]
        tail = f"  閉じた: {closed['reason']}（{closed.get('version') or '—'}）" if closed else ""
        out(f"  {row['report_id']}  {KIND_LABELS[row['kind']]}  {row['status']}"
            f"  {row['account']}  返事 {row['n_replies']}  {row['title']}{tail}")
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
        out(f"- {row['at']}  {row.get('by')}")
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
        help="不具合と要望を道具に置く（道具が断った・結果が期待と違った・欲しい形がある）",
        description="不具合と要望を道具の中に置く（設計 3.1.2）。道具が断った・結果が期待と"
                    "違った・欲しい形がある、のどれかならここへ。実装側は "
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
    filer.add_argument("--kind", required=True, choices=KINDS)
    filer.add_argument("--title", required=True)
    filer.add_argument("--body-file", required=True, dest="body_file",
                       help="本文のファイル（VM 側のパス。`-` は標準入力）")
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
