"""つまずきの年表（設計 3.6.0 §B）——`thth report timeline <project|account> [--since]`。

報告の口の記録（置いた時刻・種類・題・閉じた版）と、リリースノートの「報告 id」
「〜の実測から」を日付で並べる。**読むだけ。**

規律:

  (a) **材料は道具の中の記録（`$THTH_ROOT/state/_reports`）と repo の docs
      （`docs/リリースノート_*.md`）だけ**。推測で埋めない——閉じていない報告の
      `days_to_close` は null、リリースノートが読めなければ理由を言う。
  (b) **project の範囲だけ**（`report_inbox.Scope`）。他の持ち主の報告は行にも
      id にも出さない。リリースノートの行は、範囲の報告 id を含むか、範囲の
      project・account の名前を含む「実測から」の行があるものだけ。リリースノートに
      書かれた他の持ち主の報告 id は拾わない。
  (c) 先輩の年表（新しい持ち主の最初の 2 週間だけ）は、広場の open に参加した持ち主の
      年表の**件数と日数の集計だけ**（題・id・project 名は出さない）。見る側も参加して
      いなければ出さない（広場の open と同じ線）。
"""
from __future__ import annotations

import collections
import datetime
import json
import re

from . import jst, report_inbox, tool_version

SCHEMA_VERSION = 1
REPORT_ID_IN_TEXT = re.compile(r"r\d{8}-[0-9a-f]{8}")
NOTE_NAME = re.compile(r"リリースノート_(\d+\.\d+\.\d+)_(\d{4}-\d{2}-\d{2})\.md")
MEASURED_FROM = "実測から"
# 「始めて 2 週間でどれだけつまずくのが普通か」の窓。
FIRST_DAYS = 14
LIMITATIONS = [
    "材料は道具の中の報告の記録と repo の docs のリリースノートだけ（推測で埋めない）",
    "他の持ち主の報告は出さない。リリースノートの行は範囲の報告 id か、範囲の名前を含む実測の行があるものだけ",
    "日付は JST。days_to_close は報告を置いた日から閉じた日までの日数（閉じていなければ null）",
    "集計は範囲の報告全部（--since は行の並びだけを絞る）",
]


def _day(at) -> datetime.date | None:
    parsed = jst.parse(at) if isinstance(at, str) else None
    return jst.to_jst(parsed).date() if parsed else None


def release_notes(directory=None):
    """repo の docs のリリースノート `[(版, 日付, 題, 本文)]` と、読めなければ理由。"""
    directory = directory or (tool_version.NOTES_ROOT / "docs")
    try:
        paths = sorted(directory.iterdir())
    except OSError:
        return [], "notes_directory_unavailable"
    out = []
    for path in paths:
        match = NOTE_NAME.fullmatch(path.name)
        if not match or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")),
                     path.name)
        out.append((match[1], match[2], title, text))
    out.sort(key=lambda row: (row[1], tool_version.numbers(row[0])))
    return out, None


def _words(scope) -> set:
    """「〜の実測から」を範囲に結ぶ語（範囲の project と account の名前）。"""
    return {word for word in set(scope.projects) | set(scope.accounts) if word}


def _report_row(record, notes):
    filed = _day(record["at"])
    closed = record.get("closed") or {}
    closed_day = _day(closed.get("at"))
    return {"date": filed.isoformat(), "kind": "report", "id": record["report_id"],
            "title": record["title"], "report_kind": record["kind"],
            "status": record["status"],
            "closed_in_version": closed.get("version"),
            "days_to_close": (closed_day - filed).days if closed_day else None,
            "mentioned_in": [version for version, _date, _title, text in notes
                             if record["report_id"] in text]}


def rows(scope, records, notes):
    """範囲の報告の行と、範囲に結ぶリリースノートの行（日付の順）。"""
    visible = [record for record in records if scope.allows(record)]
    out = [_report_row(record, notes) for record in visible]
    ids = {record["report_id"] for record in visible}
    words = _words(scope)
    for version, date, title, text in notes:
        mentioned = sorted(set(REPORT_ID_IN_TEXT.findall(text)) & ids)
        measured = [line for line in text.splitlines()
                    if MEASURED_FROM in line and any(word in line for word in words)]
        if not mentioned and not measured:
            continue
        out.append({"date": date, "kind": "release", "id": version, "title": title,
                    "closed_in_version": version, "days_to_close": None,
                    "reports": mentioned, "measured_from": len(measured)})
    out.sort(key=lambda row: (row["date"], 0 if row["kind"] == "report" else 1, row["id"]))
    return out, visible


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else ordered[middle - 1] / 2 + ordered[middle] / 2


def summary(visible):
    """最初の報告から数えた日数ごとの件数と、閉じるまでの日数の中央値と n。"""
    days = [_day(record["at"]) for record in visible]
    first = min(days) if days else None
    by_day = collections.Counter((day - first).days for day in days) if first else {}
    closed = [row["days_to_close"] for row in (_report_row(r, []) for r in visible)
              if row["days_to_close"] is not None]
    return {"n_reports": len(visible),
            "n_open": sum(record["status"] == "open" for record in visible),
            "n_closed": sum(record["status"] == "closed" for record in visible),
            "first_report_date": first.isoformat() if first else None,
            "by_day_since_first": [{"day": day, "n": n} for day, n in sorted(by_day.items())],
            "first_days": {"window_days": FIRST_DAYS,
                           "n": sum(n for day, n in by_day.items() if day < FIRST_DAYS)},
            "days_to_close": {"median": _median(closed), "n": len(closed),
                              "denominator": len(visible)}}


def peers(scope, records, own_summary, now):
    """先輩の年表の中央値（新しい持ち主の最初の 2 週間だけ・件数と日数の集計だけ）。"""
    first = own_summary["first_report_date"]
    today = jst.to_jst(now).date()
    if first is not None and (today - datetime.date.fromisoformat(first)).days >= FIRST_DAYS:
        return None, "not_in_first_14_days"
    from . import plaza
    try:
        joined = plaza.members()
    except plaza.PlazaError:
        return None, "plaza_store_unavailable"
    own = set(scope.projects)
    if not own & joined:
        return None, "plaza_not_joined"
    counts, closed = [], []
    for project in sorted(joined - own):
        theirs = [record for record in records if record.get("project") == project]
        if not theirs:
            continue
        their = summary(theirs)
        counts.append(their["first_days"]["n"])
        closed.extend(row["days_to_close"] for row in (_report_row(r, []) for r in theirs)
                      if row["days_to_close"] is not None)
    if not counts:
        return None, "no_peer_timelines"
    return {"basis": "plaza_open_members", "n_owners": len(counts),
            "median_first_days_reports": _median(counts), "window_days": FIRST_DAYS,
            "median_days_to_close": _median(closed), "n_closed": len(closed),
            "titles": "not_shown"}, None


def timeline(scope, *, since=None, now=None, notes_dir=None):
    """年表 1 枚（CLI と MCP が同じ形を返す）。`scope` は `report_inbox.Scope`。"""
    since_day = None
    if since is not None:
        try:
            since_day = datetime.date.fromisoformat(str(since)[:10])
        except ValueError:
            raise report_inbox.ReportError("invalid_since") from None
    now = now or jst.now_jst()
    records, _broken = report_inbox.load_all()
    notes, notes_reason = release_notes(notes_dir)
    listed, visible = rows(scope, records, notes)
    if since_day is not None:
        listed = [row for row in listed if row["date"] >= since_day.isoformat()]
    own = summary(visible)
    peer, peer_reason = peers(scope, records, own, now)
    return {"schema_version": SCHEMA_VERSION, "report_type": "report_timeline",
            "since": since_day.isoformat() if since_day else None,
            "rows": listed, "n_rows": len(listed), "summary": own,
            "peers": peer, "peers_reason": peer_reason,
            "sources": {"reports": "tool_store", "release_notes": "repo_docs",
                        "release_notes_reason": notes_reason},
            "limitations": list(LIMITATIONS)}


def render(payload, out=print):
    s = payload["summary"]
    out(f"つまずきの年表  報告 {s['n_reports']} 件（開いている {s['n_open']}・閉じた {s['n_closed']}）"
        f"  最初の報告 {s['first_report_date'] or '—'}"
        + (f"  {payload['since']} から" if payload["since"] else ""))
    for row in payload["rows"]:
        if row["kind"] == "report":
            closed = (f"  閉じた版 {row['closed_in_version']}（{row['days_to_close']} 日）"
                      if row["closed_in_version"] else "  開いている")
            out(f"  {row['date']}  報告 {row['id']}  {report_inbox.KIND_LABELS[row['report_kind']]}"
                f"  {row['title']}{closed}")
        else:
            refs = "・".join(row["reports"]) or "—"
            out(f"  {row['date']}  版 {row['id']}  {row['title']}  報告 {refs}"
                + (f"  実測の行 {row['measured_from']}" if row["measured_from"] else ""))
    close = s["days_to_close"]
    out(f"始めて {s['first_days']['window_days']} 日の報告: {s['first_days']['n']} 件"
        f"  閉じるまでの日数の中央値: {close['median'] if close['median'] is not None else '—'}"
        f"（n={close['n']}/{close['denominator']}）")
    peer = payload["peers"]
    if peer:
        out(f"先輩の年表（open に参加した {peer['n_owners']} 持ち主）: 始めて {peer['window_days']} 日の"
            f"報告の中央値 {peer['median_first_days_reports']} 件・閉じるまでの日数の中央値 "
            f"{peer['median_days_to_close'] if peer['median_days_to_close'] is not None else '—'} 日"
            f"（n={peer['n_closed']}）")
    if payload["sources"]["release_notes_reason"]:
        out(f"リリースノートを読めません: {payload['sources']['release_notes_reason']}")


def cmd_timeline(args) -> int:
    try:
        payload = timeline(report_inbox.scope_for_account(args.target), since=args.since)
    except report_inbox.ReportError as error:
        return report_inbox._print_refusal(args, error)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        render(payload)
    return 0


def register(operations) -> None:
    parser = operations.add_parser(
        "timeline", help="つまずきの年表（報告とリリースノートを日付で並べる・読むだけ）",
        description="自分の project の報告（置いた日・種類・題・閉じた版）と、リリースノートの"
                    "報告 id・「〜の実測から」を日付で並べる。材料は道具の中の記録と repo の docs "
                    "だけ。他の持ち主の報告は出さない。始めて 2 週間の件数と、閉じるまでの日数の"
                    "中央値と n。新しい持ち主の最初の 2 週間は、広場の open に参加した持ち主の"
                    "年表の中央値を 1 行（件数と日数だけ・題は出さない）。")
    parser.add_argument("target", metavar="account|project")
    parser.add_argument("--since", default=None, help="この日（YYYY-MM-DD）から並べる（集計は全部）")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=cmd_timeline)

