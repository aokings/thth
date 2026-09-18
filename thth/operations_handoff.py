"""Local-only operations evidence; never refresh, retry, approve or read credentials."""
from __future__ import annotations

import datetime
import json
from pathlib import Path
import sys

from . import accounts, analytics_report, healthcheck, incident, jst, queuefile, report


class HandoffError(ValueError):
    pass


def _json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value, "available"
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError, UnicodeError):
        return None, "unreadable"


def _source(path, state, now):
    try:
        modified = datetime.datetime.fromtimestamp(path.stat().st_mtime, datetime.timezone.utc)
    except OSError:
        modified = None
    return {"availability": state, "local_modified_at": jst.iso(modified) if modified else None,
            "observed_at": jst.iso(now), "remote_current_verified": False,
            "freshness": "unknown"}


def _account(name, cfg, now):
    state_dir = Path(accounts.state_dir_for(name))
    evidence, problems = {}, []
    counts = {key: 0 for key in report.STATUS_KEYS}
    counts.update(approval_needed=0, approved_waiting=0, overdue=0, malformed=0, unattributed_malformed=0)
    last = []
    queue_state = "not_configured"
    repo = cfg.get("repo_dir")
    if repo and Path(repo).name != accounts.REPO_NONE_BASENAME:
        path = Path(repo) / cfg["queue_dir"]
        try:
            paths = sorted(path.iterdir())
            queue_state = "available"
            for entry in paths:
                if entry.suffix != ".md":
                    continue
                qf = queuefile.parse_text(entry.read_text(encoding="utf-8"), str(entry))
                fm = qf.front_matter
                if qf.malformed:
                    # Cannot assign malformed shared-repo files to an account.
                    counts["unattributed_malformed"] += 1
                    continue
                if fm.get("account") != name:
                    continue
                status = fm.get("status")
                if status not in report.STATUS_KEYS:
                    counts["malformed"] += 1
                    continue
                counts[status] += 1
                counts["approval_needed"] += status == "draft"
                if status == "approved" and not fm.get("post_id"):
                    counts["approved_waiting"] += 1
                    at = jst.parse(fm.get("publish_at"))
                    if at and now - at > datetime.timedelta(hours=report.OVERDUE_HOURS):
                        counts["overdue"] += 1
                if status == "posted":
                    at = jst.parse(fm.get("posted_at"))
                    if at and at <= now:
                        last.append((at, "queue"))
        except FileNotFoundError:
            queue_state = "missing"
        except (OSError, ValueError, UnicodeError, TypeError):
            queue_state = "unreadable"
        evidence["queue"] = _source(path, queue_state, now)
    else:
        evidence["queue"] = {"availability": queue_state, "freshness": "not_applicable",
                             "local_modified_at": None, "observed_at": jst.iso(now),
                             "remote_current_verified": False}
    if queue_state in {"missing", "unreadable"}:
        problems.append("queue_" + queue_state)
    queue = {"availability": queue_state, "counts": counts if queue_state == "available" else None,
             "approval_validity": "unverified", "publication_readiness": "unverified"}

    sent_dir = state_dir / "sent"
    sent_state = "available"
    try:
        for path in sorted(sent_dir.iterdir()):
            if path.suffix != ".json":
                continue
            row, available = _json(path)
            at = jst.parse(row.get("sent_at")) if isinstance(row, dict) else None
            if available != "available" or not isinstance(row, dict) or not row.get("post_id") or not at:
                sent_state = "unreadable"
            elif at <= now:
                last.append((at, "sent"))
    except FileNotFoundError:
        sent_state = "missing"
    except OSError:
        sent_state = "unreadable"
    evidence["sent"] = _source(sent_dir, sent_state, now)
    if sent_state == "unreadable":
        problems.append("sent_unreadable")

    path = state_dir / "inflight.json"
    inflight, available = _json(path)
    if available == "available" and (not isinstance(inflight, dict) or not inflight):
        available = "unreadable"
    evidence["inflight"] = _source(path, available, now)
    blocked = available == "available"
    diagnostic = None
    if blocked:
        diag = healthcheck.diagnostic(name, "fail", state_dir=str(state_dir))
        diagnostic = {"since": diag.since, "reason_code": diag.reason_code,
                      "next_action_code": diag.next_action_code}
    elif available == "unreadable":
        problems.append("inflight_unreadable")

    path = state_dir / incident.STATE_FILE
    outbox, available = _json(path)
    notifications = {"availability": available, "mail_pending": None, "repo_pending": None,
                     "recorded_state": None, "recorded_at": None, "reason_code": None}
    if available == "available":
        try:
            incident._validate(outbox)
            latest_event = outbox["events"][-1] if outbox["events"] else {}
            notifications.update(recorded_state=outbox["last"], recorded_at=latest_event.get("at"),
                                 reason_code=latest_event.get("reason"))
            notifications.update(mail_pending=sum(2-len(e["accepted"]) for e in outbox["events"]),
                                 repo_pending=sum(e["repo"] == "pending" for e in outbox["events"]))
        except (ValueError, TypeError, AttributeError):
            available = notifications["availability"] = "unreadable"
    evidence["notification_outbox"] = _source(path, available, now)
    if available == "unreadable":
        problems.append("notification_outbox_unreadable")

    path = state_dir / healthcheck.STATUS_FILE
    status = healthcheck.read_status(str(state_dir))
    _, available = _json(path)
    if available == "available" and status is None:
        available = "unreadable"
    evidence["run_notification"] = _source(path, available, now)
    if available == "unreadable":
        problems.append("run_notification_unreadable")
    run = {"recorded_state": status.get("last_state") if status else None,
           "recorded_at": status.get("last_attempt_at") if status else None,
           "reason_code": status.get("reason_code") if status else None,
           "next_action_code": status.get("next_action_code") if status else None,
           "timer_health": "unknown", "notification_delivery": status.get("delivery") if status else None}
    review = counts["approval_needed"] or counts["overdue"] or counts["malformed"] or counts["unattributed_malformed"]
    recorded_failure = status and (status.get("last_state") == "fail" or status.get("delivery") == "failed")
    recorded_failure = recorded_failure or notifications["recorded_state"] == "fail"
    pending = notifications["mail_pending"] or notifications["repo_pending"]
    state = ("blocked" if blocked else "unknown" if problems else
             "review_required" if review or pending or recorded_failure else
             "waiting" if queue_state == "available" and counts["approved_waiting"] else "unknown")
    latest = max(last, key=lambda item: item[0]) if last else None
    return {"state": state, "queue": queue, "inflight": diagnostic,
            "notifications": notifications, "last_run_notification": run,
            "last_post": {"observed_at": jst.iso(latest[0]) if latest else None,
                          "source": latest[1] if latest else None,
                          "coverage": "partial" if problems else "local_records_only"},
            "cannot_say": problems + ["timer_health_unknown", "thread_runs_not_inspected",
                "remote_queue_not_verified", "approval_validity_not_verified",
                "unresolved_replies_not_measured", "no_previous_session_cursor"],
            "evidence": evidence}


from .report_details import detailed

@detailed
def answer(account_name=None, *, project=None, now=None):
    for value in (account_name, project):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise HandoffError("account/project は空でない文字列です")
    if (account_name is None) == (project is None):
        raise HandoffError("account か project のどちらか一方が要ります")
    now = now if now is not None else jst.now_jst()
    if not isinstance(now, datetime.datetime) or now.tzinfo is None:
        raise HandoffError("now はタイムゾーン付きの日時です")
    configs = {}
    skipped = False
    if account_name is not None:
        try:
            configs[account_name] = accounts.load_account(account_name)
        except (accounts.AccountError, ValueError, TypeError):
            raise HandoffError("account_configuration_unavailable") from None
    else:
        try:
            for name in accounts.list_account_names():
                try:
                    cfg = accounts.load_account(name)
                except (accounts.AccountError, ValueError, TypeError):
                    skipped = True
                    continue
                if cfg.get("project") == project:
                    configs[name] = cfg
        except accounts.AccountError:
            raise HandoffError("account_registry_unavailable") from None
        if not configs:
            raise HandoffError("project に読める account がありません")
    nodes = {name: _account(name, cfg, now) for name, cfg in configs.items()}
    return {"schema_version": 1, "report_type": "operations_handoff",
            "generated_at": jst.iso(now), "filters": {"account": account_name, "project": project},
            "by_account": nodes, "scope_complete": not skipped,
            "limitations": ["ローカル保存記録の現在の読み取り。前回セッション以降の差分ではない",
                "waiting は承認済み原稿の存在を表す。公開可能・timer正常の保証ではない",
                "鮮度は未検証。ファイル更新時刻はSNS情報の取得時刻ではない",
                "通知の未処理数はoutbox基準。SMTP受付は受信者への配送完了ではない",
                "unattributed_malformed は共有queue内でaccountを特定できない型外件数",
                "スレッド連投・token・現在のtimer稼働は検査対象外。停止なしとは断定しない"]}


def render_markdown(payload):
    lines = ["# Operations handoff", "", f"生成時刻: {payload['generated_at']}", ""]
    for name, row in payload["by_account"].items():
        lines += [f"- {analytics_report._markdown_text(name)}: {row['state']}（timer正常性は不明）"]
    lines += ["", *["- " + item for item in payload["limitations"]], "", "## 根拠と構造化データ", ""]
    lines += ["    " + line for line in json.dumps(payload, ensure_ascii=False, indent=2).splitlines()]
    return "\n".join(lines) + "\n"


def cmd_handoff_report(args):
    try:
        payload = answer(args.account, project=args.project)
    except HandoffError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else render_markdown(payload))
    return 0
