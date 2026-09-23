"""Local-only operations evidence; never refresh, retry, approve or read credentials."""
from __future__ import annotations

import datetime
import json
from pathlib import Path
import sys

from . import accounts, analytics_report, healthcheck, incident, jst, queuefile, report, select, tool_version


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


def _account(name, cfg, now, *, allowed_names=None):
    state_dir = Path(accounts.state_dir_for(name))
    evidence, problems = {}, []
    counts = {key: 0 for key in report.STATUS_KEYS}
    counts.update(approval_needed=0, approved_waiting=0, overdue=0, waiting_reply=0,
                  malformed=0, unattributed_malformed=0)
    last = []
    queue_state = "not_configured"
    repo = cfg.get("repo_dir")
    if repo and Path(repo).name != accounts.REPO_NONE_BASENAME:
        path = Path(repo) / cfg["queue_dir"]
        try:
            paths = sorted(path.iterdir())
            queue_state = "available"
            # 先に全部読む（reply_to_file の指した原稿を同じ一覧から探すため）。
            parsed = [queuefile.parse_text(entry.read_text(encoding="utf-8"), str(entry))
                      for entry in paths if entry.suffix == ".md"]
            for qf in parsed:
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
                    # reply_to_file が未解決なら時刻超過に数えず返信待ちに数える
                    # （設計 3.2.0 §2）。この口は照合しない（読むだけ・鮮度未検証）。
                    reply = select.resolve_reply_to_file(qf, account_name=name, pool=parsed,
                                                         require_verified=False)
                    if reply is not None and reply.reason is not None:
                        counts["waiting_reply"] += 1
                        continue
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
    sent_count = 0
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
                sent_count += 1
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
                     "recorded_state": None, "recorded_at": None, "reason_code": None, "last_event_id": None}
    if available == "available":
        try:
            incident._validate(outbox)
            latest_event = outbox["events"][-1] if outbox["events"] else {}
            notifications["last_event_id"] = latest_event.get("id")
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
    from . import unanswered
    try:
        unanswered_result = unanswered.answer(name, now=now,
                                               allowed_names=allowed_names)
    except accounts.AccountError:
        unanswered_result = {'summary': {'n': None, 'oldest_age_hours': None},
                             'cannot_say': ['unanswered_ledger_unavailable']}
    return {"state": state, "queue": queue, "inflight": diagnostic,
            "unanswered": unanswered_result['summary'],
            "sent_count": sent_count if sent_state == "available" else 0 if sent_state == "missing" else None,
            "notifications": notifications, "last_run_notification": run,
            "last_post": {"observed_at": jst.iso(latest[0]) if latest else None,
                          "source": latest[1] if latest else None,
                          "coverage": "partial" if problems else "local_records_only"},
            "cannot_say": problems + unanswered_result["cannot_say"] + ["timer_health_unknown", "thread_runs_not_inspected",
                "remote_queue_not_verified", "approval_validity_not_verified",
                "no_previous_session_cursor"],
            "evidence": evidence}


from .report_details import detailed

@detailed
def answer(account_name=None, *, project=None, now=None, since_last_read=False,
           trusted_names=None, allowed_names=None):
    for value in (account_name, project):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise HandoffError("account/project は空でない文字列です")
    if (account_name is None) == (project is None):
        raise HandoffError("account か project のどちらか一方が要ります")
    now = now if now is not None else jst.now_jst()
    if not isinstance(now, datetime.datetime) or now.tzinfo is None:
        raise HandoffError("now はタイムゾーン付きの日時です")
    if type(since_last_read) is not bool:
        raise HandoffError("since_last_read は boolean です")
    configs = {}
    skipped = False
    if account_name is not None:
        if allowed_names is not None and account_name not in allowed_names:
            raise HandoffError("scope_unavailable")
        try:
            configs[account_name] = accounts.load_account(account_name)
        except (accounts.AccountError, ValueError, TypeError):
            raise HandoffError("account_configuration_unavailable") from None
    else:
        try:
            for name in (sorted(set(trusted_names)) if trusted_names is not None
                         else accounts.list_account_names()):
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
    nodes = {name: _account(name, cfg, now, allowed_names=allowed_names)
             for name, cfg in configs.items()}
    from . import handoff_cursor
    for name, node in nodes.items():
        node["changes_since"] = None
        node["tool"] = tool_version.summary()
        if since_last_read:
            previous, reason = handoff_cursor.read(name, now)
            if previous is not None:
                node["tool"] = tool_version.summary(previous['snapshot'].get('tool_version'))
                node["changes_since"] = {"read_at": previous["read_at"], "by": previous["by"],
                    "changes": handoff_cursor.changes(previous["snapshot"], handoff_cursor.snapshot(node))}
                node["cannot_say"].remove("no_previous_session_cursor")
            elif reason in {"cursor_unreadable", "cursor_directory_unavailable"}:
                node["cannot_say"].remove("no_previous_session_cursor")
                node["cannot_say"].append(reason)
    if since_last_read:
        # 報告の口（設計 3.1.2 §3・§3.5）。開始手順で必ず読む場所なので受け口の案内を
        # 常に添え、その account の project の報告と前回の栞から増えた返事を出す
        # （栞の snapshot は `tool.version` だけを見るので、足しても差分は増えない）。
        from . import report_inbox
        for name, node in nodes.items():
            node["tool"] = {**node["tool"], "report_channel": report_inbox.CHANNEL,
                            "reports": report_inbox.handoff_summary(
                                {name: configs[name]},
                                [(node.get("changes_since") or {}).get("read_at")])}
    tool = nodes[account_name]['tool'] if account_name else tool_version.summary()
    if since_last_read and account_name is None:
        tool = {**tool, "report_channel": report_inbox.CHANNEL,
                "reports": report_inbox.handoff_summary(
                    configs, [(node.get("changes_since") or {}).get("read_at")
                              for node in nodes.values()])}
    return {"schema_version": 1, "report_type": "operations_handoff",
            "generated_at": jst.iso(now), "filters": {"account": account_name, "project": project},
            "by_account": nodes, "scope_complete": not skipped,
            "tool": tool,
            "limitations": [("保存済みsnapshotと現在の値の差分。間に起きた全イベントを復元するものではない"
                              if since_last_read else
                              "ローカル保存記録の現在の読み取り。前回セッション以降の差分ではない"),
                "waiting は承認済み原稿の存在を表す。公開可能・timer正常の保証ではない",
                "鮮度は未検証。ファイル更新時刻はSNS情報の取得時刻ではない",
                "通知の未処理数はoutbox基準。SMTP受付は受信者への配送完了ではない",
                "unattributed_malformed は共有queue内でaccountを特定できない型外件数",
                "スレッド連投・token・現在のtimer稼働は検査対象外。停止なしとは断定しない"]}


def render_markdown(payload):
    lines = ["# Operations handoff", "", f"生成時刻: {payload['generated_at']}", ""]
    for name, row in payload["by_account"].items():
        if row.get("changes_since") is not None:
            for change in row["changes_since"]["changes"]:
                lines.append(f"- {analytics_report._markdown_text(name)}: {analytics_report._markdown_text(change['field'])}: {analytics_report._markdown_text(change['previous'])} → {analytics_report._markdown_text(change['current'])}")
        lines += [f"- {analytics_report._markdown_text(name)}: {row['state']}（timer正常性は不明）"]
    reports = payload["tool"].get("reports")
    if reports is not None and reports.get("cannot_say") is None:
        lines.append(f"- 報告: 開いている {reports['open']}/{reports['denominator']} 件"
                     f"・新しい返事 {len(reports['new_replies'])} 件（thth report show <id>）")
        for row in reports["new_replies"]:
            lines.append(f"  - {analytics_report._markdown_text(row['report_id'])} "
                         f"{analytics_report._markdown_text(row['by'] or '')}: "
                         f"{analytics_report._markdown_text(row['text'].splitlines()[0] if row['text'] else '')}")
    if payload["tool"].get("report_channel"):
        from . import report_inbox
        lines.append("- " + report_inbox.CHANNEL_LINE)
    lines += ["", *["- " + item for item in payload["limitations"]], "", "## 根拠と構造化データ", ""]
    lines += ["    " + line for line in json.dumps(payload, ensure_ascii=False, indent=2).splitlines()]
    return "\n".join(lines) + "\n"


def cmd_handoff_report(args):
    try:
        mark_read = getattr(args, "mark_read", False)
        by = getattr(args, "by", None)
        if type(mark_read) is not bool or (mark_read and (not isinstance(by, str) or not by.strip())) or (by is not None and not mark_read):
            raise HandoffError("--mark-read と --by 名前は組で指定してください")
        payload = answer(args.account, project=args.project, since_last_read=getattr(args,"since_last_read",False))
        if mark_read:
            from . import handoff_cursor
            for name, node in payload["by_account"].items():
                handoff_cursor.write(name, node, by, jst.parse(payload["generated_at"]))
    except (HandoffError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else render_markdown(payload))
    return 0
