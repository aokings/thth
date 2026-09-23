"""``thth run`` の死活通知と、安全な停止理由の組み立て。

Healthchecks の ping URL は check の秘密そのものなので、プロセス内でだけ扱う。
通知本文・状態ファイル・board には URL、投稿本文、生の例外文を残さない。
"""
from __future__ import annotations

from . import api_diagnostic

import dataclasses
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from . import accounts as accounts_mod
from . import httpsafe
from . import inflight as inflight_mod
from . import jst
from . import redact as redact_mod
from . import runs as runs_mod


ENV_KEY = "HEALTHCHECK_URL"
STATUS_FILE = "healthcheck-status.json"
TIMEOUT_SECONDS = 8.0
_HTTP_STATUS = re.compile(r"(?:HTTP\s*)?([45][0-9]{2})", re.IGNORECASE)
_KNOWN_REASONS = {
    "account_config_error", "account_locked", "approval_stale", "healthy",
    "missing_token", "permission_missing", "publish_failed",
    "publish_result_unknown", "publish_timeout", "repo_sync_failed",
    "text_mismatch_after_rebase", "text_mismatch_before_writeback",
    "unusable_post_id", "writeback_push_failed", "unknown",
    # 媒体に訊いても公開の結果が決まらなかった（設計 3.3.1 §3）。次の一手は人が
    # `thth inflight <account> resolve` で決めること。
    "inflight_unresolved",
}
_KNOWN_EXCEPTIONS = {
    "TimeoutError": "exception_timeout",
    "ConnectionError": "exception_connection",
    "OSError": "exception_os_error",
    "ValueError": "exception_value_error",
    "TypeError": "exception_type_error",
    "KeyError": "exception_key_error",
    "RuntimeError": "exception_runtime_error",
}
_EXCEPTION_REASONS = set(_KNOWN_EXCEPTIONS.values()) | {"exception_process_error"}

# 承認済みなのに出られない（設計 3.3.0 A1）。区分の語は `select.HELD_CATEGORIES`
# と同じ 4 語だけ・後ろは本数（数字）だけ——本文も原稿名も入らない静的な理由。
_HELD_PART = r"(?:approval_stale|reply_to_unresolved|stale|invalid) [1-9][0-9]{0,5}"
_HELD_CODE = re.compile(rf"approved_but_held: {_HELD_PART}(?:, {_HELD_PART}){{0,3}}")
# 0 本に戻った（held の復旧）・指紋の版が変わって再承認が要る（A3・1 回だけ）。
HELD_CLEARED = "held_cleared"
_REAPPROVAL_CODE = re.compile(r"reapproval_required: [1-9][0-9]{0,5}")
# 承認の確定待ちで publish_at まで 3 時間を切った本数（設計 3.7.0 §B3）。本数だけ。
_CONFIRM_DUE_CODE = re.compile(r"confirm_due: [1-9][0-9]{0,5}")


def is_confirm_due_code(value) -> bool:
    return isinstance(value, str) and _CONFIRM_DUE_CODE.fullmatch(value) is not None
# 死活通知の状態（`held` は run が成功したのに承認済みの原稿が出られない）。
STATES = ("success", "fail", "held")


def is_held_code(value) -> bool:
    return isinstance(value, str) and _HELD_CODE.fullmatch(value) is not None


def held_total(value) -> int:
    """`approved_but_held: …` の本数の合計（形が違えば 0）。"""
    if not is_held_code(value):
        return 0
    return sum(int(part.rsplit(" ", 1)[1]) for part in value.split(": ", 1)[1].split(", "))


def _reason_code_is_safe(value, *, allow_legacy: bool = True) -> bool:
    if not isinstance(value, str):
        return False
    if value in _KNOWN_REASONS or value in _EXCEPTION_REASONS:
        return True
    if (value == HELD_CLEARED or is_held_code(value) or _REAPPROVAL_CODE.fullmatch(value)
            or is_confirm_due_code(value)):
        return True
    if re.fullmatch(r"(?:container|publish)_http_[45][0-9]{2}", value):
        return True
    if allow_legacy and value.startswith("legacy_candidate_"):
        return _reason_code_is_safe(
            value.removeprefix("legacy_candidate_"), allow_legacy=False)
    return False


@dataclasses.dataclass(frozen=True)
class Diagnostic:
    account: str
    state: str
    since: str | None
    file: str | None
    reason: str
    next_action: str
    reason_code: str
    next_action_code: str
    api_diagnostic: dict | None = None


@dataclasses.dataclass(frozen=True)
class Attempt:
    configured: bool
    delivery: str  # delivered | failed | not_configured
    category: str | None = None
    state_saved: bool = True


def status_path(state_dir: str) -> str:
    return os.path.join(state_dir, STATUS_FILE)


def has_blocking_state(account: str, state_dir: str) -> bool:
    """rc=0 の thread ``stopped`` に隠れた未解決状態があるか。読むだけ。"""
    try:
        if inflight_mod.read(state_dir):
            return True
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return True
    try:
        from . import threadrun
        if threadrun.unreadable_runs():
            return True
        return bool(threadrun.has_unresolved(account))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return True


def read_status(state_dir: str) -> dict | None:
    """通知の安全な状態だけを読む。壊れた旧記録は ``None`` とする。"""
    try:
        with open(status_path(state_dir), encoding="utf-8") as f:
            row = json.load(f)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(row, dict):
        return None
    configured = row.get("endpoint_configured")
    if configured not in (True, False):
        configured = None
    delivery = row.get("delivery")
    if not isinstance(delivery, str) or delivery not in {
            "delivered", "failed", "not_configured"}:
        delivery = None
    last_state = row.get("last_state")
    if not isinstance(last_state, str) or last_state not in STATES:
        last_state = None
    attempted = jst.parse(row.get("last_attempt_at"))
    incident_since = jst.parse(row.get("incident_since"))
    reason_code = row.get("reason_code")
    if not _reason_code_is_safe(reason_code):
        reason_code = None
    category = row.get("category")
    if not isinstance(category, str) or not re.fullmatch(
            r"(?:http_[45][0-9]{2}|invalid_endpoint|redirect_blocked|timeout|"
            r"transport_error|unexpected_response|http_error)", category):
        category = None
    action_code = next_action_for(reason_code or "unknown")
    return {
        "endpoint_configured": configured,
        "delivery": delivery,
        "last_state": last_state,
        "last_attempt_at": jst.iso(attempted) if attempted else None,
        "category": category,
        "reason": reason_text(reason_code or "unknown"),
        "reason_code": reason_code,
        "next_action": next_action_text(action_code),
        "next_action_code": action_code,
        "incident_file": _safe_file(row.get("incident_file")),
        "incident_since": jst.iso(incident_since) if incident_since else None,
    }


def _write_status(state_dir: str, row: dict) -> None:
    os.makedirs(state_dir, exist_ok=True)
    path = status_path(state_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(row, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def configured_url(account_cfg: dict | None) -> str | None:
    """account env を優先し、無ければプロセス env を読む。URL は保存しない。"""
    value = None
    if account_cfg is not None:
        try:
            value = accounts_mod.load_env(account_cfg).get(ENV_KEY)
        except (OSError, UnicodeError):
            value = None
    if not value:
        value = os.environ.get(ENV_KEY)
    if isinstance(value, str) and value.strip():
        value = value.strip()
        redact_mod.register_secret(value)
        return value
    return None


def _safe_text(value, *, fallback: str, limit: int) -> str:
    if not isinstance(value, str):
        return fallback
    value = value.strip()[:limit]
    return value if value else fallback


def _safe_account(value: str) -> str:
    value = _safe_text(value, fallback="unknown", limit=80)
    return re.sub(r"[^A-Za-z0-9_.@-]", "_", value)


def _safe_file(value) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    name = os.path.basename(value)[:120]
    # 日本語の原稿名は調査の手掛かりなので保つ。区切り・制御文字・改行だけを落とす。
    safe = "".join(ch if ch.isalnum() or ch in "_.@() -" else "_" for ch in name)
    return safe or None


def sanitize_reason(raw, *, action: str | None = None,
                    exception_class: str | None = None) -> str:
    """任意の外部文字列を、短い診断コードだけに落とす。"""
    if exception_class:
        return _KNOWN_EXCEPTIONS.get(exception_class, "exception_process_error")
    if action == "locked":
        return "account_locked"
    if not isinstance(raw, str) or not raw:
        return "unknown"
    if raw in _KNOWN_REASONS:
        return raw
    lower = raw.lower()
    for code in ("text_mismatch_before_writeback", "text_mismatch_after_rebase",
                 "unusable_post_id", "repo_sync_failed", "approval_stale"):
        if code in lower:
            return code
    if "permission_missing" in lower or "権限" in raw:
        return "permission_missing"
    match = _HTTP_STATUS.search(raw)
    if match:
        stage = "container" if "container" in lower else "publish"
        return f"{stage}_http_{match.group(1)}"
    if "timeout" in lower or "timed out" in lower or "時間切れ" in raw:
        return "publish_timeout"
    if "push" in lower:
        return "writeback_push_failed"
    if "sync" in lower or "同期" in raw:
        return "repo_sync_failed"
    if action == "inflight":
        return "publish_result_unknown"
    if action == "post":
        return "publish_failed"
    return "unknown"


def next_action_for(reason: str) -> str:
    if reason == "missing_token" or reason == "permission_missing":
        return "run_thth_auth"
    if reason.startswith("text_mismatch_"):
        return "inspect_queue_and_inflight"
    if reason == "inflight_unresolved":
        return "resolve_inflight_by_hand"
    if reason in {"unusable_post_id", "publish_result_unknown", "publish_timeout"} \
            or reason.startswith("publish_http_5"):
        return "verify_remote_post_then_resolve_inflight"
    if reason in {"writeback_push_failed", "repo_sync_failed"}:
        return "inspect_repo_sync"
    if reason == "account_locked":
        return "inspect_running_process"
    if reason.startswith("exception_"):
        return "inspect_timer_log"
    if reason == "healthy" or reason == HELD_CLEARED:
        return "none"
    if is_held_code(reason) or (isinstance(reason, str) and _REAPPROVAL_CODE.fullmatch(reason)):
        return "reapprove_or_fix_held"
    if is_confirm_due_code(reason):
        return "confirm_pending_approval"
    return "inspect_board_and_timer_log"


def reason_text(reason: str) -> str:
    fixed = {
        "healthy": "定期実行は正常に完了しました",
        "missing_token": "認証 token が無いため実行できませんでした",
        "account_config_error": "アカウント台帳を読めませんでした",
        "account_locked": "別の実行がアカウントを使用中です",
        "permission_missing": "投稿に必要な権限がありません",
        "publish_result_unknown": "公開できたか確認できず inflight で停止しています",
        "publish_timeout": "公開 API が時間切れになり結果を確認できません",
        "publish_failed": "公開 API が投稿を受け付けませんでした",
        "repo_sync_failed": "投稿 repo を同期できませんでした",
        "writeback_push_failed": "投稿後の記録を repo へ送れませんでした",
        "text_mismatch_before_writeback": "送信内容と repo の内容が食い違いました",
        "text_mismatch_after_rebase": "同期後に送信内容と repo の内容が食い違いました",
        "unusable_post_id": "媒体が保存できない形式の post_id を返しました",
        "inflight_unresolved": "媒体に問い合わせても公開の結果が決まらず inflight で停止しています",
        "exception_process_error": "定期実行が予期しない例外で停止しました",
        "unknown": "停止理由を安全に特定できませんでした",
    }
    if reason in fixed:
        return f"{fixed[reason]} [{reason}]"
    if reason == HELD_CLEARED:
        return f"承認済みで出られなかった原稿は 0 本になりました [{HELD_CLEARED}]"
    if is_held_code(reason):
        detail = "・".join(f"{part.rsplit(' ', 1)[0]} {part.rsplit(' ', 1)[1]} 本"
                           for part in reason.split(": ", 1)[1].split(", "))
        return (f"承認済みで予定時刻を過ぎた原稿が {held_total(reason)} 本、出られずに"
                f"止まっています（{detail}） [approved_but_held]")
    if is_confirm_due_code(reason):
        count = reason.split(": ", 1)[1]
        return (f"承認の確定待ち（thth approve の 1 段目だけ済んだ原稿）で、予定時刻まで"
                f" 3 時間を切ったものが {count} 本あります。確定するまで出ません [confirm_due]")
    if _REAPPROVAL_CODE.fullmatch(reason):
        count = reason.split(": ", 1)[1]
        return (f"この版で承認の指紋の計算が変わりました。再承認が要る原稿: {count} 本"
                " [reapproval_required]")
    match = re.fullmatch(r"(container|publish)_http_([45][0-9]{2})", reason)
    if match:
        stage = "コンテナ作成 API" if match.group(1) == "container" else "公開 API"
        return f"{stage} が HTTP {match.group(2)} を返しました [{reason}]"
    if reason.startswith("exception_"):
        return f"定期実行が例外で停止しました [{reason}]"
    if reason.startswith("legacy_candidate_"):
        candidate = reason.removeprefix("legacy_candidate_")
        return ("同じ原稿の過去の投稿エラー候補がありますが、この inflight との対応は"
                f"未確認です [{candidate}]")
    return fixed["unknown"] + " [unknown]"


def next_action_text(action: str) -> str:
    fixed = {
        "none": "対応は不要です",
        "run_thth_auth": "thth auth で認証と権限を確認してください",
        "inspect_queue_and_inflight": "queue と inflight の食い違いを確認してください",
        "verify_remote_post_then_resolve_inflight":
            "媒体上の投稿有無を確認してから inflight を解消してください"
            "（thth inflight <account> show・resolve）",
        "resolve_inflight_by_hand":
            "媒体の画面で出たかどうかを確かめ、thth inflight <account> resolve"
            " --not-published か --published <post_id> で解いてください",
        "inspect_repo_sync": "repo の同期状態と push 失敗を確認してください",
        "inspect_running_process": "実行中プロセスと timer の重複を確認してください",
        "inspect_timer_log": "timer のログで例外の発生箇所を確認してください",
        "inspect_board_and_timer_log": "thth board と timer のログを確認してください",
        "reapprove_or_fix_held": ("thth morning の held_items（または thth board の要確認）で"
                                  "名前を見て、再承認するか原稿を直してください"),
        "confirm_pending_approval": ("thth observe の次の一手（確定）にある確定の命令を見て、"
                                     "本文を確かめてから --confirm を実行してください"),
    }
    return f"{fixed.get(action, fixed['inspect_board_and_timer_log'])} [{action}]"


def _matching_run_reason(state_dir: str, file_name: str | None) -> str:
    """同じ原稿の publication error だけを見る。時刻の対応は主張しない。"""
    if not file_name:
        return "unknown"
    try:
        rows = runs_mod.read_runs(state_dir)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return "unknown"
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        if row.get("action") != "post" or row.get("status") != "error":
            continue
        raw_file = row.get("file")
        if not isinstance(raw_file, str) or os.path.basename(raw_file) != file_name:
            continue
        reason = sanitize_reason(row.get("error"), action="post")
        # 古い inflight には run_id が無く、同じ原稿の過去事故と厳密には結べない。
        # 候補であることをコード自体に含め、元の原因だとは断定しない。
        return f"legacy_candidate_{reason}"
    return "unknown"


def diagnostic(account: str, state: str, *, state_dir: str,
               result=None, reason: str | None = None,
               exception: Exception | None = None) -> Diagnostic:
    inflight = None
    try:
        inflight = inflight_mod.read(state_dir)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        inflight = None
    inflight = inflight if isinstance(inflight, dict) else None
    result_file = getattr(result, "file", None)
    raw_file = ((inflight or {}).get("file")
                if inflight else result_file)
    raw_file_name = os.path.basename(raw_file) if isinstance(raw_file, str) else None
    file_name = _safe_file(raw_file_name)
    parsed_since = jst.parse((inflight or {}).get("started"))
    since = jst.iso(parsed_since) if parsed_since is not None else None

    action = getattr(result, "action", None)
    raw = reason if reason is not None else getattr(result, "error", None)
    if exception is not None:
        safe_reason = sanitize_reason(None, exception_class=type(exception).__name__)
    elif state == "success":
        safe_reason = "healthy"
    elif state == "held":
        # 承認済みなのに出られない（設計 3.3.0 A1）。理由は呼び出し側が作った
        # 静的な符丁だけを通す（形が違えば unknown）。原稿は account 全体の話
        # なので 1 本に結ばない。
        safe_reason = reason if is_held_code(reason) else "unknown"
        file_name = None
    else:
        safe_reason = sanitize_reason(raw, action=action)
        if safe_reason in {"unknown", "publish_result_unknown"} and inflight:
            previous = read_status(state_dir)
            previous_reason = previous.get("reason_code") if previous else None
            if (previous and previous.get("last_state") == "fail"
                    and previous.get("incident_file") == file_name
                    and previous.get("incident_since") == since
                    and _reason_code_is_safe(previous_reason)):
                safe_reason = previous_reason
            else:
                mismatch = inflight.get("mismatch_fields")
                if isinstance(mismatch, list) and mismatch:
                    safe_reason = "text_mismatch_before_writeback"
                else:
                    recovered = _matching_run_reason(state_dir, raw_file_name)
                    if recovered != "unknown":
                        safe_reason = recovered
                    elif safe_reason == "unknown":
                        safe_reason = "publish_result_unknown"
    next_action_code = next_action_for(safe_reason)
    return Diagnostic(
        account=_safe_account(account), state=state, since=since, file=file_name,
        reason=reason_text(safe_reason), next_action=next_action_text(next_action_code),
        reason_code=safe_reason, next_action_code=next_action_code,
        api_diagnostic=api_diagnostic.clean(getattr(result, "api_diagnostic", None)) if state == "fail" else {})


def _failure_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    path = parts.path.rstrip("/") + "/fail"
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def _validate_url(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return (parts.scheme == "https" and bool(parts.hostname)
            and parts.username is None and parts.password is None
            and not parts.fragment)


def _post(url: str, diag: Diagnostic, *, timeout: float = TIMEOUT_SECONDS) -> tuple[bool, str | None]:
    # body の鍵はこの 6 個だけ。内部の code は reason/next_action の末尾にも含めるが、
    # 別フィールドを増やして将来本文や URL が紛れ込む余地を作らない。
    body = json.dumps({
        "account": diag.account,
        "state": diag.state,
        "since": diag.since,
        "file": diag.file,
        "reason": diag.reason,
        "next_action": diag.next_action,
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    try:
        if not _validate_url(url):
            return False, "invalid_endpoint"
        target = url if diag.state == "success" else _failure_url(url)
        req = urllib.request.Request(
            target, data=body, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8",
                     "User-Agent": "thth-healthcheck/1"})
    except ValueError:
        return False, "invalid_endpoint"
    try:
        with httpsafe.urlopen(req, timeout=timeout) as response:
            reply = response.read(128).decode("utf-8", errors="replace").strip()
        if reply != "OK":
            return False, "unexpected_response"
        return True, None
    except httpsafe.RedirectBlocked:
        return False, "redirect_blocked"
    except urllib.error.HTTPError as e:
        return False, f"http_{e.code}" if 400 <= e.code <= 599 else "http_error"
    except (TimeoutError, socket.timeout):
        return False, "timeout"
    except (urllib.error.URLError, OSError, ValueError):
        return False, "transport_error"


def notify(account: str, account_cfg: dict | None, diag: Diagnostic, *,
           state_dir: str | None = None, timeout: float = TIMEOUT_SECONDS) -> Attempt:
    """1 ping を試み、安全な結果だけを保存する。投稿の結果を例外で上書きしない。"""
    state_dir = state_dir or accounts_mod.state_dir_for(account)
    url = configured_url(account_cfg)
    if url is None:
        attempt = Attempt(False, "not_configured")
    else:
        ok, category = _post(url, diag, timeout=timeout)
        attempt = Attempt(True, "delivered" if ok else "failed", category)

    row = {
        "endpoint_configured": attempt.configured,
        "last_attempt_at": jst.iso(),
        "last_state": diag.state,
        "delivery": attempt.delivery,
        "category": attempt.category,
        "reason": diag.reason,
        "reason_code": diag.reason_code,
        "next_action": diag.next_action,
        "next_action_code": diag.next_action_code,
        "incident_file": diag.file,
        "incident_since": diag.since,
    }
    try:
        _write_status(state_dir, row)
    except (OSError, ValueError, TypeError):
        return dataclasses.replace(attempt, state_saved=False)
    return attempt
