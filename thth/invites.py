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

import contextlib
import contextvars
import fcntl
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
import uuid

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
    "invalid_approval",
))
# 断るときは理由と次の一手（静的な文だけ・値は混ぜない）。
NEXT = {
    "invite_media_unsupported": "3.10.0 の招待は --media threads だけです。Bluesky・Mastodon は従前どおり"
                                "運営者が thth account add と thth auth で用意してください",
    "invalid_project": f"--project は英数字と - _ . だけ・{PROJECT_MAX} 字までです",
    "invalid_label": f"--label は 1 行・{LABEL_MAX} 字までです（控えのメモ・Worker には送りません）",
    "invalid_expires": f"--expires は 1d〜{MAX_DAYS}d の日数です（例: 30d）",
    "invalid_approval": "--approval は none・publish・all のどれかです（審査員向けは all）",
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
    # 3.12.0 段 3: 招待で作る口座の approval（項目の無い古い招待は none を書く）。
    if "approval" in record and not accounts.valid_approval(record["approval"]):
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
    row = {key: record.get(key) for key in keys}
    row["approval"] = record.get("approval") or accounts.APPROVAL_DEFAULT
    return row


def create(*, media, project, label=None, expires=None, production=False, by, approval=None):
    """招待を 1 本作り、URL を tty に 1 回だけ出す。戻り値に code は入れない。

    `approval`（3.12.0 段 3）はその招待で作る口座の台帳に書く値（既定 none）。審査員のように
    自分の LLM を持たない人の招待は all にし、運営者が下書きを置いて本人が承認ページで押す。
    """
    admin_log.actor(by)
    approval = accounts.APPROVAL_DEFAULT if approval is None else approval
    if not accounts.valid_approval(approval):
        raise error("invalid_approval")
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
                      production=production, approval=approval, expires_at=expires_at, status="registering",
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
                                       "approval": [None, approval],
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


# ------------------------------------------------------------ 常駐の 1 巡

# 押された（clicked）招待を拾う間隔。押してから数十秒で進めば足り、30 日ぶんの
# 招待を 2 秒ごとに署名つきで問い合わせない。認可中は 1 巡ごとに預かり所を見る。
OPEN_POLL_SECONDS = 10
# 招待の認可の戻り先（Meta に登録してある受け口・`callback/src/index.js`）。
INVITE_REDIRECT_URI = "https://thth.me/callback/"
THREADS_HANDLE = re.compile(r"[A-Za-z0-9_.]{1,64}\Z")
_next_poll = {}


def run_once(credentials_path=None):
    """`thth approval-worker` が 1 巡ごとに呼ぶ。1 本の失敗で他の招待と承認 job を止めない。

    `credentials_path` は常駐が読んでいる資格情報のファイル（`--credentials`）。渡されたときは、
    招待で用意した口座に、その口座だけの書き込みの資格情報を 1 件足す（裁定 2026-09-25）。
    """
    token = _credentials.set(credentials_path)
    try:
        _run_once()
    finally:
        _credentials.reset(token)


def _run_once():
    try:
        records, _ = STORE.load()
    except InviteError:
        print("invite_store_unavailable", file=sys.stderr)
        return
    for record in records:
        if not _active(record):
            continue
        try:
            process(record["invite_id"])
        except Exception:
            # 理由は静的に 1 語だけ（URL・code・token を journald に流さない）。
            print("invite_process_failed: " + record["invite_id"], file=sys.stderr)


def _active(record):
    if record["status"] in ("open", "authorizing"):
        return True
    # 使われた招待は、Worker への「用意ができた」と承認 secret の表示を見届けるまで。
    return (record["status"] == "used" and now_ms() < record["expires_at"]
            and not (record.get("remote_ready") and record.get("approver_set")
                     and (record.get("credential_sha256") or not _credentials.get())))


def process(invite_id):
    with STORE.locked() as directory:
        record = STORE.find(directory, invite_id)
        if record["status"] in ("open", "authorizing") and now_ms() >= record["expires_at"]:
            # 期限は手元でも見る（Worker が 410 を返す前に口座を作らない）。
            _forget_session(directory, invite_id)
            _save(directory, record, status="expired")
        elif record["status"] == "open":
            _open(directory, record)
        elif record["status"] == "authorizing":
            _authorizing(directory, record)
        elif record["status"] == "used":
            _used(directory, record)
        return public(record)


def _save(directory, record, **changes):
    record.update(changes, updated_at=jst.iso())
    STORE.write(directory, record)


def _throttled(record):
    key = record["invite_id"]
    if time.monotonic() < _next_poll.get(key, 0):
        return True
    _next_poll[key] = time.monotonic() + OPEN_POLL_SECONDS
    return False


def _reset(record, reason):
    """Worker の招待を open に戻す（本人がもう一度押せる・理由はページに出る）。届かなくてもよい
    ——次の巡で Worker が認可中のままなら、もう一度送る。"""
    try:
        relay.signed_request("invite", record["code_hash"], "reset", {"reason": reason})
    except relay.RelayError:
        pass


def _back_to_open(directory, record, reason):
    _forget_session(directory, record["invite_id"])
    _reset(record, reason)
    _save(directory, record, status="open", reason=reason)


def _open(directory, record):
    if _throttled(record):
        return
    try:
        remote = relay.signed_request("invite", record["code_hash"], "status", {})
    except relay.RelayError as exc:
        if exc.status in (404, 410):
            # Worker では期限の alarm で消えた。
            _save(directory, record, status="expired")
        return
    state = remote.get("status")
    if state in ("revoked", "expired"):
        _save(directory, record, status=state)
    elif state == "authorizing":
        # 手元は open なのに Worker は認可中: 前の巡の reset が届かなかった。
        _reset(record, "auth_failed")
    elif state == "clicked":
        _notice_stall(directory, record, remote.get("clicked_at"))
        _start(directory, record)


# 押されてから常駐が拾うまでにこれだけ掛かったら、常駐が止まっていた（招待のページは
# 2 分で「運営者に連絡を」と出している・Worker の STALL_MS と同じ）。
STALL_SECONDS = 120


def _notice_stall(directory, record, clicked_at):
    """常駐が止まっていて招待が進まなかったことを、運用通知で管理者に 1 回だけ知らせる。

    Worker からはメールを送れない。止まっている間は誰も送れないので、常駐が戻って
    押された招待を拾ったときに送る（止まった VM そのものは外部の死活監視が拾う）。
    """
    if record.get("stall_notified") or type(clicked_at) is not int:
        return
    waited = (now_ms() - clicked_at) // 1000
    if waited < STALL_SECONDS:
        return
    import json
    from . import incident
    try:
        ready = incident.readiness({})
        if ready["admin_configured"] and ready["smtp_configured"]:
            settings = incident.settings({})
            event = {"event": "invite_stalled", "invite": record["invite_id"], "waited_seconds": waited,
                     "at": jst.iso(),
                     "next": "招待が押されてから常駐（thth approval-worker）が拾うまで時間が掛かりました。"
                             "常駐の状態を確かめてください（systemctl status thth-approval-worker）"}
            notice = dict(id=hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest(),
                          state="admin_change", at=event["at"], reason="healthy", repo="no_source",
                          admin_event=event)
            incident._send(settings, settings["admin"], notice, subject(record["invite_id"]))
    except Exception:
        # 通知の失敗で招待を止めない（1 回だけ・再送しない）。
        pass
    _save(directory, record, stall_notified=True)


def _profile():
    from . import appenv
    from .adapters.auth_threads import ThreadsInviteAuthProfile
    app_id, app_secret = appenv.load_app_env(log=lambda _: None)
    redact.register_secret(app_secret)
    profile = ThreadsInviteAuthProfile(app_id, app_secret, INVITE_REDIRECT_URI, invite_scopes())
    profile.validate()
    return profile


def _start(directory, record):
    """押された招待に、認可の session（state・read key は VM で作る）と認可 URL を用意する。"""
    from . import appenv, authflow, oauth
    try:
        check_project(record["project"])
        if not _account_free(record["account"]):
            raise error("invite_account_exists")
        profile = _profile()
    except (InviteError, appenv.AppEnvError, OSError, ValueError):
        return _reset(record, "unavailable")
    session = {"state": authflow.secret(oauth._new_state()),
               "read_key": authflow.secret(secrets.token_urlsafe(32)), "created_at": now_ms()}
    try:
        status, _ = authflow.relay_request(session, register=True)
    except authflow.FlowError:
        status = None
    if status != 201:
        return _reset(record, "unavailable")
    _write_session(directory, record["invite_id"], session)
    try:
        value = relay.signed_request("invite", record["code_hash"], "authorize",
                                     {"authorize_url": profile.authorize(session),
                                      "expires_at": session["created_at"] + authflow.TTL * 1000})
        if value != {"status": "authorizing"}:
            raise relay.RelayError("approval_relay_invalid")
    except relay.RelayError:
        _forget_session(directory, record["invite_id"])
        return _reset(record, "unavailable")
    _save(directory, record, status="authorizing", reason=None)


def _write_session(directory, invite_id, session):
    import json
    STORE.write_raw(directory, _session_name(invite_id), json.dumps(session).encode())


def _read_session(directory, invite_id):
    import json
    from . import authflow
    raw = STORE.read_raw(directory, _session_name(invite_id))
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if (not isinstance(value, dict) or set(value) != {"state", "read_key", "created_at"}
            or not all(isinstance(value[k], str) and relay.OPAQUE.fullmatch(value[k]) for k in ("state", "read_key"))
            or type(value["created_at"]) is not int):
        return None
    for key in ("state", "read_key"):
        authflow.secret(value[key])
    return value


def _code(value):
    """預かり所が返した code の検査（`authflow.poll` と同じ形・受け取って 5 分以内）。"""
    from . import authflow
    code, received = value.get("code"), jst.parse(value.get("received_at"))
    if isinstance(code, str):
        authflow.secret(code)
    age = (jst.now_jst() - received).total_seconds() if received else -1
    if (not isinstance(code, str) or not code or len(code.encode()) > 4096
            or any(ord(c) < 32 or ord(c) == 127 for c in code) or age < 0 or age >= 300):
        return None
    return code


def _recovered(directory, record):
    """口座は書けたのに、記録を used にする前に常駐が止まった。台帳から続ける。"""
    try:
        cfg = accounts.load_account(record["account"])
    except accounts.AccountError:
        return False
    if cfg.get("invite_id") != record["invite_id"] or not isinstance(cfg.get("handle"), str) \
            or not THREADS_HANDLE.fullmatch(cfg["handle"]):
        return False
    _forget_session(directory, record["invite_id"])
    _save(directory, record, status="used", reason=None, handle=cfg["handle"], remote_ready=False, approver_set=False)
    _announce(directory, record)
    return True


def _authorizing(directory, record):
    from . import authflow
    if _recovered(directory, record):
        return
    session = _read_session(directory, record["invite_id"])
    if session is None:
        return _back_to_open(directory, record, "auth_failed")
    if now_ms() >= session["created_at"] + authflow.TTL * 1000:
        return _back_to_open(directory, record, "auth_expired")
    try:
        status, value = authflow.relay_request(session, timeout=5)
    except authflow.FlowError:
        return  # 預かり所が一時的に答えない。次の巡で見る。
    if status in (404, 429):
        return
    code = _code(value) if status == 200 else None
    if code is None:
        return _back_to_open(directory, record, "auth_failed")
    _complete(directory, record, session, code)


def _ledger(record, *, handle="", user_id=""):
    """招待の口座の台帳（`account add` と同じ雛形・置き場は `docs/運用_招待する側.md` のとおり）。"""
    from . import account_cli
    name = record["account"]
    data = account_cli.build_ledger(name, media="threads", project=record["project"],
                                    repo_dir=f"$THTH_ROOT/repos/_server/{name}",
                                    redirect_uri=INVITE_REDIRECT_URI)
    data.update(handle=handle, user_id=user_id, env=f"$THTH_ROOT/secrets/{name}.env",
                token=f"$THTH_ROOT/secrets/{name}.token", production=record["production"],
                scheduled=False, invite_id=record["invite_id"],
                # 設計 3.12.0 §3.1: 新しく用意する口座は既定の none を明記する（項目の無い
                # 既存の招待の口座は all として扱うので、ここで書かないと all になる）。
                approval=record.get("approval") or accounts.APPROVAL_DEFAULT,
                provenance=admin_log.provenance(record["by"], via="invite"))
    return data


def _expanded(data):
    value = accounts.AccountConfig(data)
    value._thth_account_name = data["account"]
    for key in ("repo_dir", "env", "token"):
        value[key] = accounts._expand(value[key])
    return value


def _threads_account_exists(user_id, username):
    """同じ Threads 口座が既に台帳にあるか（handle・user_id・token の user_id で見る）。"""
    from . import oauth
    for name in accounts.list_account_names():
        try:
            cfg = accounts.load_account(name)
        except accounts.AccountError:
            continue
        if cfg.get("media") != "threads":
            continue
        if oauth.handle_matches(cfg.get("handle") or "", username) or str(cfg.get("user_id") or "") == user_id:
            return True
        try:
            token = accounts.load_token(cfg)
        except (OSError, ValueError, accounts.AccountError):
            continue
        if isinstance(token, dict) and token.get("user_id") == user_id:
            return True
    return False


def _complete(directory, record, session, code):
    """認可の code を VM で token に換え、口座を用意して Worker に知らせる。"""
    from . import appenv, oauth
    try:
        profile = _profile()
        exchange_cfg = _expanded(_ledger(record))
        token = profile.exchange(code, session, exchange_cfg, log=lambda _: None)
    except (oauth.OAuthError, appenv.AppEnvError, accounts.AccountError, OSError, ValueError):
        return _back_to_open(directory, record, "auth_failed")
    user_id, username = token.get("user_id"), token.get("username")
    if not isinstance(username, str) or not THREADS_HANDLE.fullmatch(username) or not isinstance(user_id, str):
        return _back_to_open(directory, record, "auth_failed")
    # 招待 1 本で口座は 1 つ・同じ Threads 口座に 2 つ目は作らない（設計 §2）。
    if _threads_account_exists(user_id, username):
        return _back_to_open(directory, record, "account_exists")
    try:
        check_project(record["project"])
    except InviteError:
        return _back_to_open(directory, record, "unavailable")
    token["auth_via"] = "relay"
    data = _ledger(record, handle=username, user_id=user_id)
    try:
        _create_account(record, data, token)
    except (InviteError, OSError, ValueError, accounts.AccountError, admin_log.AdminLogError):
        return _back_to_open(directory, record, "unavailable")
    _forget_session(directory, record["invite_id"])
    _save(directory, record, status="used", reason=None, handle=username, remote_ready=False, approver_set=False)
    _try_credential(directory, record)
    try:
        from . import doctor
        doctor.record_auth(record["account"], _expanded(data), token, log=lambda _: None)
    except Exception:
        pass
    _announce(directory, record)


def _create_account(record, data, token):
    """token（0600）と台帳（0600・O_EXCL）を書き、変更ログに残す。ログに書けなければ両方戻す。"""
    import json
    from . import account_cli, leave_gate, secrets_fs
    name = record["account"]
    expanded = _expanded(data)
    ledger_path = os.path.join(account_cli.target_accounts_dir(), name + ".json")
    token_path = expanded["token"]
    created = []

    def rollback():
        for path in reversed(created):
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass

    with leave_gate.scope(name), leave_gate.lease(name), leave_gate.credentials(), \
            admin_log.transaction(rollback=rollback):
        if not _account_free(name) or os.path.lexists(token_path):
            raise error("invite_account_exists")
        token_dir = os.path.dirname(token_path)
        if not os.path.isdir(token_dir):
            os.makedirs(token_dir, mode=0o700, exist_ok=True)
            os.chmod(token_dir, 0o700)
        secrets_fs.atomic_write_json(token_path, token, mode=0o600)
        created.append(token_path)
        os.makedirs(account_cli.target_accounts_dir(), exist_ok=True)
        fd = os.open(ledger_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        created.append(ledger_path)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        by = record["by"]
        admin_log.append("account_added", name, data, by=by,
                         diff={**admin_log.difference({}, data), "via_invite": [None, record["invite_id"]]})
        if data["production"]:
            admin_log.append("production_enabled", name, data, by=by, diff={"production": [False, True]})
        admin_log.append("token_set", name, data, by=by,
                         diff={"token": ["absent", "present"], "auth_via": [None, "relay"],
                               "via_invite": [None, record["invite_id"]]})
        admin_log.append("invite_used", subject(record["invite_id"]), {"media": "threads"}, by=by,
                         diff={"account": [None, name]})


def _announce(directory, record):
    """Worker に「用意ができた」と知らせる（届くまで次の巡でも送る・同じ内容なら何度でも 200）。"""
    try:
        value = relay.signed_request("invite", record["code_hash"], "complete",
                                     {"person": record["account"], "account": record["account"],
                                      "handle": record["handle"]})
    except relay.RelayError:
        return
    if value == {"status": "ready"}:
        _save(directory, record, remote_ready=True)


def _used(directory, record):
    _try_credential(directory, record)
    if not record.get("remote_ready"):
        return _announce(directory, record)
    if _throttled(record):
        return
    try:
        remote = relay.signed_request("invite", record["code_hash"], "status", {})
    except relay.RelayError:
        return
    if remote.get("status") != "done":
        return
    # 承認 secret は Worker の完了ページが本人に 1 回だけ見せた。VM に secret は無い。
    # 記録に残すのは「承認者が設定された」ことだけ（approver set と同じ event）。
    admin_log.append("approver_set", record["account"], {"media": "threads"}, by=record["by"], via="http",
                     diff={"credential_present": [None, True], "via_invite": [None, record["invite_id"]]})
    _save(directory, record, approver_set=True)


# ------------------------------------------------------------ 口座だけの資格情報
#
# 招待の口座の承認 job は、actor がその口座（承認ページの人）の資格情報からしか作れない。
# 審査員が認可した直後に運営者の手を挟まないように、口座を用意したら常駐の資格情報の
# ファイルに 1 件足す（裁定 2026-09-25）。accounts はその口座 1 つ・scope user・writes
# true・actor は口座名。bearer は乱数で作って hash だけ残し、値は誰にも出さない
# （MCP で使うときは運営者が既存の手順で入れ直す）。退出で消す（`forget_credential`）。

CREDENTIAL_DAYS = 365
_credentials = contextvars.ContextVar("thth_invite_credentials", default=None)


def _new_bearer():
    # テストはここを差し替えて、値がどこにも出ないことを確かめる。
    return secrets.token_urlsafe(32)


def _try_credential(directory, record):
    path = _credentials.get()
    if not path or record.get("credential_sha256"):
        return
    try:
        digest = add_credential(path, record)
    except Exception:
        # 次の巡でもう一度（口座は用意済み・資格情報だけが無い）。
        return
    _save(directory, record, credential_sha256=digest, credentials_path=str(Path(path).absolute()))


@contextlib.contextmanager
def _locked_credentials(path):
    """資格情報のファイルの排他（同じ置き場の `.<名前>.lock`・0600）。"""
    lock = os.open(str(path.with_name("." + path.name + ".lock")),
                   os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield
    finally:
        os.close(lock)


def _read_credentials(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
                or info.st_nlink != 1):
            raise ValueError("credentials_file_unsafe")
        return stream.read()


def _replace_credentials(path, data):
    """一時ファイル（0600・O_EXCL）→ fsync → rename → 親の fsync。"""
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def add_credential(credentials_path, record):
    """資格情報を 1 件足し、その hash を返す（bearer の値は返さない・どこにも書かない）。"""
    import datetime
    import json
    from . import report_http
    path = Path(credentials_path).absolute()
    name = record["account"]
    with _locked_credentials(path):
        before = _read_credentials(path)
        report_http.load_credentials(path)  # 壊れたファイルには足さない（作りもしない）
        config = json.loads(before)
        rows = config["credentials"]
        # 同じ口座の招待の 1 件が既にあれば、それを使う（前の巡で書けたが記録が追いつかなかった）。
        for row in rows:
            if row.get("accounts") == {name: record["project"]} and row.get("actor") == name:
                return row["sha256"]
        if len(rows) >= 100:
            raise ValueError("credentials_full")
        digest = hashlib.sha256(_new_bearer().encode()).hexdigest()
        expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=CREDENTIAL_DAYS)
        rows.append({"sha256": digest, "expires_at": jst.iso(expires), "revoked": False,
                     "accounts": {name: record["project"]}, "scope": "user", "writes": True, "actor": name})
        data = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode()

        def rollback():
            _replace_credentials(path, before)

        with admin_log.transaction(rollback=rollback):
            _replace_credentials(path, data)
            report_http.load_credentials(path)
            admin_log.append("credential_added", name, {"media": "threads"}, by=record["by"], via="http",
                             diff={"credential": ["absent", "present"], "writes": [None, True],
                                   "via_invite": [None, record["invite_id"]]})
    return digest


def forget_credential(account, *, by):
    """退出（`thth account leave`）の最後に、招待で足したその口座の資格情報を消す。何度呼んでも同じ。"""
    import json
    from . import report_http
    try:
        records, _ = STORE.load()
    except InviteError:
        raise ValueError("credential_cleanup_incomplete") from None
    for record in records:
        if record.get("account") != account or not record.get("credential_sha256") or record.get("credential_removed"):
            continue
        path = Path(record["credentials_path"])
        try:
            with _locked_credentials(path):
                before = _read_credentials(path)
                config = json.loads(before)
                kept = [row for row in config["credentials"]
                        if not (row.get("sha256") == record["credential_sha256"] and row.get("accounts") == {account: record["project"]})]
                if len(kept) != len(config["credentials"]):
                    if not kept:
                        # 資格情報が 1 件も無いファイルは読めない形になる。その 1 件は取り消し済みで残す。
                        kept = [dict(row, revoked=True) for row in config["credentials"]]
                    config["credentials"] = kept

                    def rollback():
                        _replace_credentials(path, before)

                    with admin_log.transaction(rollback=rollback):
                        _replace_credentials(path, (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode())
                        report_http.load_credentials(path)
                        admin_log.append("credential_removed", account, {"media": "threads"}, by=by,
                                         diff={"credential": ["present", "absent"], "via_invite": [None, record["invite_id"]]})
        except FileNotFoundError:
            pass
        except (OSError, ValueError, admin_log.AdminLogError):
            raise ValueError("credential_cleanup_incomplete") from None
        with STORE.locked() as directory:
            latest = STORE.find(directory, record["invite_id"])
            _save(directory, latest, credential_removed=True)


# ------------------------------------------------ 運営者の口（資格情報の bearer を使わない）
#
# 招待の口座の下書きを置き、その口座の承認者（口座名）あての承認 URL を出す（裁定 2026-09-25）。
# 審査の間、運営者が審査員の口座に下書きを置いて承認 URL を渡すための口。bearer は使わない:
# job は招待で足したその口座だけの資格情報の 1 件（hash だけ）に結び、常駐の既存の再確認
# （その 1 件がまだあり、取り消されていないか・口座の範囲か）をそのまま通す。
# 招待で用意していない口座（運営者自身の repo 型の口座など）には出さない。

ADMIN_REASONS = frozenset(("admin_approval_invite_account_only", "admin_approval_credential_missing",
                           "admin_approval_invalid_draft"))
NEXT.update({
    "admin_approval_invite_account_only": "この口は招待で用意した口座（inv-…）だけです。運営者自身の口座は"
                                          "従来どおり thth approve か MCP の承認の道で",
    "admin_approval_credential_missing": "その口座の資格情報の 1 件がありません。thth approval-worker を "
                                         "--credentials 付きで動かすと、次の巡で足します（thth admin invite list）",
    "admin_approval_invalid_draft": "--draft は draft_id（64 桁）か、その口座の queue の原稿のパスです",
})


def invite_context(account):
    """招待で用意した口座の、その口座だけの資格情報の文脈（bearer は使わない）。"""
    from . import report_http
    try:
        cfg = accounts.load_account(account)
    except accounts.AccountError:
        raise error("admin_approval_invite_account_only") from None
    records, _ = STORE.load()
    record = next((row for row in records if row.get("account") == account and row["status"] == "used"
                   and row["invite_id"] == cfg.get("invite_id")), None)
    if record is None:
        raise error("admin_approval_invite_account_only")
    if not record.get("credential_sha256") or not record.get("credentials_path"):
        raise error("admin_approval_credential_missing")
    import datetime
    try:
        _, loaded = report_http.load_credentials(Path(record["credentials_path"]))
    except Exception:
        raise error("admin_approval_credential_missing") from None
    now = datetime.datetime.now(datetime.timezone.utc)
    for digest, expires, revoked, context in loaded:
        if (digest == record["credential_sha256"] and not revoked and now < expires
                and context.actor == account and dict(context.allowed_accounts) == {account: record["project"]}):
            return context
    raise error("admin_approval_credential_missing")


def _draft_id(context, account, value):
    from . import server_writes
    if isinstance(value, str) and server_writes.DRAFT_ID.fullmatch(value):
        return value
    cfg = accounts.load_account(account)
    try:
        _, queue = server_writes._queue(cfg)
        path = Path(value).absolute()
    except Exception:
        raise error("admin_approval_invalid_draft") from None
    if path.parent != queue.absolute() or not path.name.endswith(".md"):
        raise error("admin_approval_invalid_draft")
    return server_writes._id(path.name)


def admin_draft_put(account, *, body, publish_at=None, topic=None, reply_to=None, by):
    admin_log.actor(by)
    from . import server_writes
    context = invite_context(account)
    request = {"operation": "draft_put", "account": account, "body": body, "publish_at": publish_at or jst.iso()}
    for key, value in (("topic", topic), ("reply_to", reply_to)):
        if value:
            request[key] = value
    return _admin_execute(context, request, by=by)


def _admin_execute(context, request, *, by, listed=True):
    """運営者の CLI だけ: `write_unavailable` を理由 1 語と次の一手に直す（設計 3.12.0 §6-5）。

    MCP やサーバの口は `server_writes.execute` の静的な名前のまま（ここを通らない）。
    """
    from . import admin_diagnose, server_writes
    from .report_service import ReportServiceError
    try:
        return server_writes.execute(context, request, via="cli", by=by, listed=listed)
    except ReportServiceError as exc:
        better = admin_diagnose.explain(exc, context, request.get("account"))
        if better is exc:
            raise
        raise better from None


def admin_approval_request(account, *, draft=None, send=False, retract=None, reason=None, by, listed=True):
    """承認 URL を出す。既定は原稿の承認、--send は承認の直後に公開、--retract は削除。

    既定で本人の承認待ちの一覧（https://thth.me/pending）にも出す（設計 3.11.0）。
    """
    admin_log.actor(by)
    from . import queuefile, server_writes
    context = invite_context(account)
    if retract is not None:
        request = {"operation": "retract_request", "account": account, "post_id": retract, "reason": reason or ""}
    else:
        draft_id = _draft_id(context, account, draft)
        if not send:
            request = {"operation": "approval_request", "account": account, "draft_id": draft_id}
        else:
            # 原稿の本文・話題・返信先で send_request（承認の直後に公開・返信）。
            cfg = accounts.load_account(account)
            name, raw, q = server_writes._draft(cfg, account, draft_id)
            if q.front_matter.get("status") != "draft":
                raise error("admin_approval_invalid_draft")
            body = queuefile.extract_section(q.body, cfg["media"]).strip()
            request = {"operation": "send_request", "account": account, "body": body}
            for key in ("topic", "reply_to"):
                if q.front_matter.get(key):
                    request[key] = str(q.front_matter[key])
    return _admin_execute(context, request, by=by, listed=listed)


def _admin_fail(exc):
    from .report_service import ReportServiceError
    if isinstance(exc, InviteError):
        return _fail(str(exc))
    if isinstance(exc, ReportServiceError):
        detail = getattr(exc, "reason", None)
        print(str(exc) + (": " + detail if isinstance(detail, str) else ""), file=sys.stderr)
        return 2
    if isinstance(exc, ValueError) and str(exc).startswith("admin_by_required"):
        print(str(exc), file=sys.stderr)
        return 2
    return _fail("invite_store_unavailable")


def cmd_admin_draft_put(args):
    from .report_service import ReportServiceError
    try:
        body = private_store.read_input(args.body_file, 48000, error=error, invalid="admin_approval_invalid_draft")
        row = admin_draft_put(args.account, body=body or "", publish_at=args.publish_at, topic=args.topic,
                              reply_to=args.reply_to, by=args.by)
    except (InviteError, ReportServiceError, ValueError, OSError) as exc:
        return _admin_fail(exc)
    print(f"draft_put: account={row['account']} draft_id={row['draft_id']} status={row['status']}")
    return 0


def cmd_admin_approval_request(args):
    from .report_service import ReportServiceError
    if (args.draft is None) == (args.retract is None) or (args.retract is not None and (args.send or not args.reason)):
        print("--draft か --retract（と --reason）のどちらか 1 つを渡してください", file=sys.stderr)
        return 2
    try:
        row = admin_approval_request(args.account, draft=args.draft, send=args.send, retract=args.retract,
                                     reason=args.reason, by=args.by, listed=not args.no_list)
    except (InviteError, ReportServiceError, ValueError, OSError) as exc:
        return _admin_fail(exc)
    if "job_id" not in row:
        # 口座の approval が承認を求めない（設計 3.12.0 §3.1）: その場で出した・消した・刻んだ。
        shown = {key: row[key] for key in ("status", "post_id", "permalink", "draft_id", "publish_at", "retracted_at")
                 if row.get(key)}
        print(f"done_without_approval: account={row['account']} "
              + " ".join(f"{key}={value}" for key, value in shown.items()))
        return 0
    # 承認 URL はこの口の出力（運営者が本人に渡す）。押すには本人の承認 secret が要る。
    # 一覧に出したもの（既定）は、本人が https://thth.me/pending から自分で開ける（URL を届けなくてよい）。
    print(f"approval_requested: account={row['account']} kind={row['kind']} job_id={row['job_id']}")
    if row.get("pending_url"):
        import time as time_mod
        minutes = max(0, (row["expires_at"] - int(time_mod.time() * 1000)) // 60000)
        print(f"承認待ちの一覧: {row['pending_url']}（本人がユーザ名 {row['account']} と承認 secret で入る・期限まで {minutes} 分）")
        print("承認 URL（開いてから 10 分）: " + row["approval_url"])
    else:
        print("承認 URL（10 分）: " + row["approval_url"])
    return 0


def register_admin_writes(commands):
    parser = commands.add_parser("approval", help="招待の口座の承認 URL を出す（運営者の口・bearer を使わない）")
    operations = parser.add_subparsers(dest="approval_operation", required=True)
    p = operations.add_parser("request", help="その口座の承認者あての承認 URL（既定で本人の承認待ちの一覧にも出す・最大 24 時間）")
    p.add_argument("account")
    p.add_argument("--draft", default=None, help="draft_id か、その口座の queue の原稿のパス")
    p.add_argument("--send", action="store_true", help="承認の直後に公開する（原稿の本文・返信先で）")
    p.add_argument("--retract", default=None, metavar="POST_ID", help="この投稿の削除の承認")
    p.add_argument("--reason", default=None, help="--retract の理由")
    p.add_argument("--no-list", action="store_true",
                   help="承認待ちの一覧（https://thth.me/pending）に出さない（承認 URL だけ・10 分）")
    p.add_argument("--by", required=True)
    p.set_defaults(func=cmd_admin_approval_request)
    parser = commands.add_parser("draft", help="招待の口座に下書きを置く（運営者の口）")
    operations = parser.add_subparsers(dest="draft_operation", required=True)
    p = operations.add_parser("put", help="下書きを 1 本置く")
    p.add_argument("account")
    p.add_argument("--body-file", required=True, help="本文のファイル（- は標準入力）")
    p.add_argument("--publish-at", default=None, help="ISO 8601（省略時は今）")
    p.add_argument("--topic", default=None)
    p.add_argument("--reply-to", default=None)
    p.add_argument("--by", required=True)
    p.set_defaults(func=cmd_admin_draft_put)


# ------------------------------------------------------------------- CLI

def _fail(reason):
    known = REASONS | ADMIN_REASONS | {"invalid_request"}
    reason = reason if reason in known else "invite_store_unavailable"
    print(reason + (": " + NEXT[reason] if reason in NEXT else ""), file=sys.stderr)
    return 2


def cmd_create(args):
    try:
        row = create(media=args.media, project=args.project, label=args.label, expires=args.expires,
                     production=bool(args.production), by=args.by, approval=getattr(args, "approval", None))
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
          f"production={str(row['production']).lower()} approval={row['approval']} "
          f"expires_at={_when(row['expires_at'])}")
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
              f"  approval={row['approval']}"
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
    p.add_argument("--approval", default="none", choices=accounts.APPROVAL_VALUES,
                   help="作る口座の approval（既定 none。自分の LLM を持たない審査員向けは all）")
    p.add_argument("--by", required=True)
    p.set_defaults(func=cmd_create)
    p = operations.add_parser("revoke", help="招待を取り消す")
    p.add_argument("invite_id")
    p.add_argument("--by", required=True)
    p.set_defaults(func=cmd_revoke)
    p = operations.add_parser("list", help="招待の一覧（code は出さない）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)
    register_admin_writes(commands)
