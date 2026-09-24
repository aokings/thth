"""招待リンク（設計 3.10.0）。運営者が作る、期限つきで 1 回だけ使える招待。

Meta の審査員や外の人が、運営者の VM に触らずに自分の Threads 口座で入る道。
運営者が `thth admin invite create` で招待を作り、URL（`https://thth.me/invite/<code>`）
を 1 回だけ tty に出す。本人がそれを開いて押すと、VM の常駐（`thth approval-worker`）
が拾って認可の session を作り、認可が済むと口座を用意する。

規律（設計 §2）:

  (a) **code は VM に残さない**。残すのは SHA-256 だけ（Worker も同じ hash で招待を引く）。
      code は生成したその場で tty に 1 回だけ出す——stdout にもログにも出さない
      （`approver set` の secret と同じ作法。LLM が打つ命令の出力に載せない）。
  (b) 招待 1 本で作れる口座は 1 つ。1 回使えば終わり・期限（既定 30 日・最長 90 日）・
      運営者がいつでも取り消せる。使われた・切れた・取り消された招待は Worker が 410。
  (c) 招待の project は**持ち主の組に入っていないもの**だけ（外の人を持ち主の
      読み合いに入れない・設計 §2）。作るときと口座を用意するときの 2 回見る。
  (d) 変更ログは `invite_created`・`invite_used`・`invite_revoked`（subject は
      `invite-<id>`・presence-only）と、口座の `account_added`（via invite）。

置き場は `$THTH_ROOT/state/_invites/<invite_id>.json`（0600・親 0700・`private_store`）。
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sys
import time

from . import accounts, admin_log, approval_relay as relay, jst, private_store, redact

DIRECTORY = "_invites"
INVITE_ID = re.compile(r"[0-9a-f]{12}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
MEDIA = ("threads",)
DEFAULT_DAYS = 30
MAX_DAYS = 90
LABEL_MAX = 80
# `inv-<project>-<6 桁>` が口座名の上限（64）に収まる project の長さ。
PROJECT_MAX = accounts.NAME_MAX - len("inv--") - 6
# 招待の状態。`registering`/`unknown`/`failed` は Worker への登録の途中・不明・拒否。
STATUSES = ("registering", "open", "authorizing", "used", "revoked", "expired", "failed", "unknown")
OPEN = ("registering", "unknown", "open", "authorizing")
# 招待の認可で求める権限。申請の範囲（`docs/計画_Meta申請_2026-09-25.md`）に合わせて
# `threads_share_to_instagram` は外す——申請しない権限を外の人に求めると、審査前は
# 認可そのものが通らないことがある。招待のページはこの一覧をそのまま見せる。
def invite_scopes():
    from . import scopes
    return [name for name in scopes.DEFAULT_SCOPES if name != "threads_share_to_instagram"]


REASONS = frozenset((
    "invite_media_unsupported", "invalid_project", "invalid_label", "invalid_expires",
    "invite_project_in_owner_group", "invite_account_exists", "invite_not_found",
    "invite_store_unavailable", "invite_log_unavailable", "invite_already_used",
    "invite_not_open", "invite_tty_required", "invite_registration_rejected",
    "invite_registration_unknown", "invite_revoke_unknown",
    "invite_remote_created_audit_unconfirmed", "invite_remote_created_delivery_failed",
))
# 断るときは理由と次の一手（静的な文だけ・値は混ぜない）。
NEXT = {
    "invite_media_unsupported": "3.10.0 の招待は --media threads だけです。Bluesky・Mastodon は従前どおり"
                                "運営者が thth account add と thth auth で用意してください",
    "invalid_project": f"--project は英数字と - _ . だけ・{PROJECT_MAX} 字までです",
    "invalid_label": f"--label は 1 行・{LABEL_MAX} 字までです（控えのメモ・Worker には送りません）",
    "invalid_expires": f"--expires は 1d〜{MAX_DAYS}d の日数です（例: 30d）",
    "invite_project_in_owner_group": "その project は持ち主の組に入っています。招待は外の人のためなので、"
                                     "組の外の project で作ってください（thth admin plaza owner list）",
    "invite_account_exists": "招待の口座名が既にあります。もう一度 create してください",
    "invite_not_found": "その id の招待はありません（thth admin invite list）",
    "invite_already_used": "その招待は使用済みです。口座を止めるなら thth account leave <口座> --by <名前>",
    "invite_not_open": "その招待はもう使えない状態です（取り消し済み・期限切れ・登録失敗）",
    "invite_tty_required": "招待 URL は tty に 1 回だけ出します。ssh なら ssh -t で入ってください",
    "invite_registration_rejected": "Worker が招待の登録を断りました。Worker の deploy と署名鍵"
                                    "（APPROVAL_PUBLIC_KEY）を確かめてください。招待は作られていません",
    "invite_registration_unknown": "Worker に届いたか分かりません。thth admin invite revoke <id> で"
                                   "取り消してから作り直してください",
    "invite_revoke_unknown": "Worker で取り消せたか分かりません。同じ命令をもう一度打ってください",
    "invite_remote_created_audit_unconfirmed": "招待は作られ URL も出しましたが、変更ログに書けたか"
                                               "分かりません。thth admin log を確かめてください",
    "invite_remote_created_delivery_failed": "招待は作られましたが URL を tty に出せませんでした。"
                                             "thth admin invite revoke <id> で取り消してください",
}


class InviteError(ValueError):
    """招待の断り（静的な理由コードだけを持つ）。"""


def error(reason):
    return InviteError(reason)


def _valid(record):
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        return False
    if not isinstance(record.get("invite_id"), str) or not INVITE_ID.fullmatch(record["invite_id"]):
        return False
    if not isinstance(record.get("code_hash"), str) or not HASH.fullmatch(record["code_hash"]):
        return False
    if record.get("media") not in MEDIA or record.get("status") not in STATUSES:
        return False
    if not accounts.name_is_safe(record.get("project")) or not accounts.name_is_safe(record.get("account")):
        return False
    if jst.parse(record.get("at")) is None or not isinstance(record.get("by"), str):
        return False
    if type(record.get("expires_at")) is not int or type(record.get("production")) is not bool:
        return False
    label = record.get("label")
    return label is None or isinstance(label, str) and len(label) <= LABEL_MAX


STORE = private_store.Store(
    DIRECTORY, id_key="invite_id", id_pattern=INVITE_ID, valid=_valid, error=error,
    unavailable="invite_store_unavailable", not_found="invite_not_found",
    log_unavailable="invite_log_unavailable", max_bytes=64 * 1024, temporary_prefix=".invite-")


def subject(invite_id):
    """変更ログの subject（口座名ではないが、置き場の名前と同じ字だけ）。"""
    return "invite-" + invite_id


def base_url():
    return os.environ.get("THTH_APPROVAL_BASE_URL", "https://thth.me").rstrip("/")


def now_ms():
    return int(time.time() * 1000)


def parse_expires(value):
    """`30d` → 日数。1〜90 日だけ（最長 90 日・設計 §2）。"""
    if value is None:
        return DEFAULT_DAYS
    match = re.fullmatch(r"([0-9]{1,3})d", str(value).strip())
    if not match or not 1 <= int(match.group(1)) <= MAX_DAYS:
        raise error("invalid_expires")
    return int(match.group(1))


def check_project(project):
    if not accounts.name_is_safe(project) or len(project) > PROJECT_MAX or project.startswith("."):
        raise error("invalid_project")
    # 外の人を持ち主の組に入れない（設計 §2）。組は project で引く。
    from . import plaza
    if plaza.owner_of(project) is not None:
        raise error("invite_project_in_owner_group")
    return project


def _account_free(name):
    """口座名がまだどこにも無い（台帳も停止の記録も無い）か。"""
    from . import account_cli, leave_gate
    if leave_gate.stopped(name):
        return False
    return not os.path.lexists(os.path.join(account_cli.target_accounts_dir(), name + ".json"))


def terminal():
    # テストはここを差し替える（本物の /dev/tty を開かない）。
    return relay.terminal()


def public(record):
    """一覧に出す形（code_hash も出さない）。"""
    keys = ("invite_id", "status", "media", "project", "account", "label", "production",
            "expires_at", "at", "by", "reason", "approver_set")
    return {key: record.get(key) for key in keys}


def create(*, media, project, label=None, expires=None, production=False, by):
    """招待を 1 本作り、URL を tty に 1 回だけ出す。戻り値に code は入れない。"""
    admin_log.actor(by)
    if media not in MEDIA:
        raise error("invite_media_unsupported")
    check_project(project)
    label = private_store.check_text(label, LABEL_MAX, error=error, invalid="invalid_label",
                                     too_long="invalid_label", required=False, one_line=True)
    days = parse_expires(expires)
    if type(production) is not bool:
        raise error("invalid_request")
    # tty を先に確かめる（無ければ code を作る前・Worker に何も送る前に断る）。
    try:
        tty_context = terminal()
        tty = tty_context.__enter__()
    except (OSError, relay.RelayError):
        raise error("invite_tty_required") from None
    try:
        # 書けない変更ログは、Worker に何か作る前に断る（approver set と同じ）。
        with admin_log.transaction():
            pass
        code = secrets.token_urlsafe(32)
        redact.register_secret(code)
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        invite_id = secrets.token_hex(6)
        account = f"inv-{project}-{secrets.token_hex(3)}"
        if not accounts.name_is_safe(account) or not _account_free(account):
            raise error("invite_account_exists")
        expires_at = now_ms() + days * 86_400_000
        record = dict(schema_version=1, invite_id=invite_id, at=jst.iso(), by=admin_log.clean(by),
                      code_hash=code_hash, media=media, project=project, label=label,
                      production=production, expires_at=expires_at, status="registering",
                      account=account, reason=None, approver_set=False, updated_at=jst.iso())
        # 先に手元へ「作ろうとしている」を残す（Worker に届いたか分からなくても
        # revoke で後始末できるように）。
        with STORE.locked() as directory:
            STORE.write(directory, record)
        try:
            value = relay.signed_request("invite", code_hash, "create",
                                         {"media": media, "production": production, "expires_at": expires_at,
                                          "scopes": invite_scopes()})
            if value != {"status": "open"}:
                raise relay.RelayError("approval_relay_invalid")
        except relay.RelayError as exc:
            rejected = isinstance(exc.status, int) and 400 <= exc.status < 500
            reason = "invite_registration_rejected" if rejected else "invite_registration_unknown"
            record.update(status="failed" if rejected else "unknown", reason=reason, updated_at=jst.iso())
            with STORE.locked() as directory:
                STORE.write(directory, record)
            raise error(reason) from None
        record.update(status="open", updated_at=jst.iso())
        with STORE.locked() as directory:
            STORE.write(directory, record)
        # Worker 側は既にできている。URL は必ず 1 回出す（ログが書けなくても）。
        try:
            tty.write("招待 URL（この表示は一度だけです）: " + base_url() + "/invite/" + code + "\n")
            tty.flush()
        except OSError:
            raise error("invite_remote_created_delivery_failed") from None
        try:
            with admin_log.transaction():
                admin_log.append("invite_created", subject(invite_id), {"media": media}, by=by,
                                 diff={"invite": ["absent", "present"], "production": [None, production],
                                       "expires_days": [None, days]})
        except private_store.LOG_ERRORS:
            raise error("invite_remote_created_audit_unconfirmed") from None
        return public(record)
    finally:
        tty_context.__exit__(None, None, None)


def revoke(invite_id, *, by):
    """招待を取り消す（Worker が先・手元はそのあと）。使用済みは断る。"""
    admin_log.actor(by)
    with STORE.locked() as directory:
        record = STORE.find(directory, invite_id)
        if record["status"] == "used":
            raise error("invite_already_used")
        if record["status"] not in OPEN:
            raise error("invite_not_open")
        with admin_log.transaction():
            pass
        try:
            value = relay.signed_request("invite", record["code_hash"], "revoke", {})
            if value != {"status": "revoked"}:
                raise relay.RelayError("approval_relay_invalid")
        except relay.RelayError as exc:
            if exc.status == 409:
                # Worker では口座の用意が済んでいる（手元の記録が追いついていない）。
                raise error("invite_already_used") from None
            # 登録が届いていなかった招待は Worker に無い（404）——それで取り消し済み。
            if not (exc.status == 404 and record["status"] in ("registering", "unknown")):
                raise error("invite_revoke_unknown") from None
        before = dict(record)
        record.update(status="revoked", reason=None, updated_at=jst.iso())
        _forget_session(directory, invite_id)
        STORE.write(directory, record)
        try:
            with admin_log.transaction():
                admin_log.append("invite_revoked", subject(invite_id), {"media": record["media"]}, by=by,
                                 diff={"status": [before["status"], "revoked"]})
        except private_store.LOG_ERRORS:
            # Worker ではもう使えない。手元の記録は取り消しのまま残す（戻すと、使えない
            # 招待を「使える」と言うことになる）。
            raise error("invite_log_unavailable") from None
    return public(record)


def list_invites():
    records, broken = STORE.load()
    return [public(record) for record in records], broken


def _session_name(invite_id):
    return invite_id + ".session.json"


def _forget_session(directory, invite_id):
    STORE.remove_name(directory, _session_name(invite_id))


# ------------------------------------------------------------------- CLI

def _fail(reason):
    reason = reason if reason in REASONS or reason == "invalid_request" else "invite_store_unavailable"
    print(reason + (": " + NEXT[reason] if reason in NEXT else ""), file=sys.stderr)
    return 2


def cmd_create(args):
    try:
        row = create(media=args.media, project=args.project, label=args.label, expires=args.expires,
                     production=bool(args.production), by=args.by)
    except InviteError as exc:
        return _fail(str(exc))
    except ValueError as exc:
        if str(exc).startswith("admin_by_required"):
            print(str(exc), file=sys.stderr)
            return 2
        return _fail("invite_store_unavailable")
    except (OSError, admin_log.AdminLogError):
        return _fail("invite_log_unavailable")
    print(f"invite_created: id={row['invite_id']} account={row['account']} media={row['media']} "
          f"production={str(row['production']).lower()} expires_at={_when(row['expires_at'])}")
    return 0


def cmd_revoke(args):
    try:
        row = revoke(args.invite_id, by=args.by)
    except InviteError as exc:
        return _fail(str(exc))
    except ValueError as exc:
        if str(exc).startswith("admin_by_required"):
            print(str(exc), file=sys.stderr)
            return 2
        return _fail("invite_store_unavailable")
    except (OSError, admin_log.AdminLogError):
        return _fail("invite_log_unavailable")
    print(f"invite_revoked: id={row['invite_id']}")
    return 0


def _when(ms):
    import datetime
    return jst.iso(datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc))


def cmd_list(args):
    import json
    try:
        rows, broken = list_invites()
    except InviteError as exc:
        return _fail(str(exc))
    if args.json:
        print(json.dumps({"schema_version": 1, "invites": rows, "broken": broken}, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("招待はありません（thth admin invite create --media threads --project <p> --by <名前>）")
    for row in rows:
        print(f"{row['invite_id']}  {row['status']:<11} {row['media']}  {row['account']}  "
              f"期限 {_when(row['expires_at'])}  production={str(row['production']).lower()}"
              + (f"  {row['label']}" if row.get("label") else ""))
    if broken:
        print(f"読めない招待の記録: {broken} 件", file=sys.stderr)
    return 0


def register_admin(commands):
    parser = commands.add_parser("invite", help="招待リンク（期限つき・1 回きり・URL は tty に 1 回だけ）")
    operations = parser.add_subparsers(dest="invite_operation", required=True)
    p = operations.add_parser("create", help="招待を作る（URL は tty に 1 回だけ）")
    p.add_argument("--media", required=True)
    p.add_argument("--project", required=True)
    p.add_argument("--label", default=None, help="控えのメモ（1 行・Worker には送らない）")
    p.add_argument("--expires", default=f"{DEFAULT_DAYS}d", help=f"1d〜{MAX_DAYS}d（既定 {DEFAULT_DAYS}d）")
    p.add_argument("--production", action="store_true", help="作る口座を production: true に（審査員向け）")
    p.add_argument("--by", required=True)
    p.set_defaults(func=cmd_create)
    p = operations.add_parser("revoke", help="招待を取り消す")
    p.add_argument("invite_id")
    p.add_argument("--by", required=True)
    p.set_defaults(func=cmd_revoke)
    p = operations.add_parser("list", help="招待の一覧（code は出さない）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)
