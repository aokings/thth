"""`thth media traits [<媒体>]`・`thth media check`——媒体の特性の台帳を読む（設計「上限と連投の柵」§4.3・段 0）。

**読むだけ。** 台帳は `thth/media_traits/*.json`（道具に同梱）。媒体の API は呼ばない・口座の台帳も読まない。
柵（guard）はまだ台帳を読まない（段 1 から）。

- `thth media traits` — 媒体の一覧（対応・未対応・項目の数・既定を持つ行）
- `thth media traits <媒体> [--signal <行>]` — 項目・根拠・確かさ・導く既定
- `thth media check` — 台帳の検査（T-M-1）。問題があれば exit 1

終了コード: 0 通る・1 検査に落ちた（check と、検査に落ちた台帳を traits で読んだとき）・2 知らない媒体。
"""
from __future__ import annotations

import json
import sys

from . import media_traits as mt

TRAITS_SCHEMA = "thth.media-traits-view/1"
CONFIDENCE_LABEL = {"high": "高", "medium": "中", "low": "低"}
EVIDENCE_LABEL = {"official_doc": "公式の文書", "official_statement": "公式の発言", "observed": "THTH の実機",
                  "inferred": "推測"}


def register(sub) -> None:
    p = sub.add_parser("media", help="媒体の特性の台帳（スパムの見方・上限・根拠と確かさ）を読む・確かめる（読むだけ）")
    msub = p.add_subparsers(dest="media_command", metavar="{traits,check}")
    msub.required = True
    t = msub.add_parser("traits", help="媒体の特性の項目・根拠・確かさ・導く既定")
    t.add_argument("media", nargs="?", default=None, help="媒体名（省けば一覧）")
    t.add_argument("--signal", default=None, metavar="<行>",
                   help="この行（例 bookmark.burst）に効く項目だけ")
    t.add_argument("--json", action="store_true")
    t.set_defaults(func=cmd_traits)
    c = msub.add_parser("check", help="台帳の検査（根拠の欠け・確かさの縛り・未対応の媒体の既定など）")
    c.add_argument("--json", action="store_true")
    c.set_defaults(func=cmd_check)
    # 段 4（設計 §9.2）: 観測を集めて数える・人が足す。台帳（数）は動かさない。
    o = msub.add_parser("observations", help="媒体の断りの観測を runs と止めの印から集めて数える（冪等・HTTP は打たない）")
    o.add_argument("media", help="媒体名")
    o.add_argument("--since", default="30d", help="この日数（例 30d）")
    o.add_argument("--no-collect", action="store_true", help="集めずに今のファイルだけを数える")
    o.add_argument("--json", action="store_true")
    o.set_defaults(func=cmd_observations)
    w = msub.add_parser("observe", help="人が観測を足す（表示の抑制など機械では分からないもの・開発のセッション）")
    w.add_argument("media", help="媒体名")
    w.add_argument("--kind", required=True, help="display_suppressed・human_verification・no_reaction・note")
    w.add_argument("--note", required=True, help="観たこと（500 字まで）")
    w.add_argument("--record", default=None, help="記録の名前（docs/… など）")
    w.add_argument("--by", default=None, help="誰が（必須）")
    w.add_argument("--json", action="store_true")
    w.set_defaults(func=cmd_observe)


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _problem_line(p) -> str:
    where = p["media"] + (f" {p['trait']}" if p.get("trait") else "")
    return f"  {p['code']}: {where}" + (f"（{p['detail']}）" if p.get("detail") not in (None, "") else "")


def _fail(args, rc, exc) -> int:
    if args.json:
        _print_json({"schema": TRAITS_SCHEMA, "ok": False, "error": exc.code, "message": str(exc),
                     "problems": exc.problems})
    else:
        print(str(exc), file=sys.stderr)
        for p in exc.problems:
            print(_problem_line(p), file=sys.stderr)
        if exc.code == mt.UNKNOWN_MEDIA:
            print("  台帳のある媒体: " + "・".join(mt.list_media()), file=sys.stderr)
    return rc


def _summary(doc) -> dict:
    return {"media": doc["media"], "supported": doc["supported"], "relates_to": doc.get("relates_to") or [],
            "traits": len(doc["traits"]),
            "defaults": sorted({rule for t in doc["traits"] for rule in (t.get("defaults") or {})})}


def build(media=None, signal=None) -> dict:
    """`--json` の形。媒体を省けば一覧（全部の台帳が検査を通るときだけ）。"""
    if media is None:
        docs = mt.load_all()
        return {"schema": TRAITS_SCHEMA, "ok": True, "media": [_summary(doc) for doc in docs.values()]}
    doc = mt.load(media)
    traits = doc["traits"]
    if signal is not None:
        traits = [t for t in traits if signal in (t.get("signals") or [])]
    return {"schema": TRAITS_SCHEMA, "ok": True, **_summary(doc), "signal": signal, "items": traits}


def _evidence_text(ev) -> str:
    label = EVIDENCE_LABEL.get(ev["type"], ev["type"])
    if ev["type"] == "official_doc":
        text = f"{label} {ev['url']}（{ev['checked_at']}）"
        if ev.get("via"):
            text += f"・二次: {ev['via']}"
    elif ev["type"] == "official_statement":
        text = f"{label} {ev['source']}" + (f" {ev['url']}" if ev.get("url") else "")
    elif ev["type"] == "observed":
        text = f"{label} {ev['record']}（{ev['observed_at']}）"
    else:
        text = f"{label}（{ev['by']}・{ev['at']}）"
    return text


def _window_text(window) -> str:
    if window is None:
        return "—"
    if window.get("day"):
        return "今日"
    if window.get("forever"):
        return "永久"
    return f"{window['minutes']} 分"


def render(payload) -> None:
    if "items" not in payload:
        print("媒体の特性の台帳")
        for row in payload["media"]:
            mark = "対応" if row["supported"] else "未対応"
            rel = f"・つながり {'・'.join(row['relates_to'])}" if row["relates_to"] else ""
            rules = "・".join(row["defaults"]) or "（既定なし）"
            print(f"  {row['media']:<15} {mark}  項目 {row['traits']}{rel}")
            print(f"    既定を持つ行: {rules}")
        return
    mark = "対応" if payload["supported"] else "未対応（柵の行は導かない）"
    head = f"{payload['media']}（{mark}）"
    if payload.get("signal"):
        head += f"  行 {payload['signal']} に効く項目"
    print(head)
    if not payload["items"]:
        print("  （該当する項目はありません）")
    for t in payload["items"]:
        conf = CONFIDENCE_LABEL.get(t["confidence"], t["confidence"])
        print(f"  {t['id']}  [{t['kind']}・確かさ {conf}]")
        print(f"    {t['statement']}")
        print(f"    根拠: {_evidence_text(t['evidence'])}")
        for extra in t["evidence"].get("also") or []:
            print(f"          {_evidence_text(extra)}")
        if t.get("penalty"):
            print(f"    罰の形: {'・'.join(t['penalty'])}")
        if t.get("signals"):
            print(f"    効く行: {'・'.join(t['signals'])}")
        for rule_id, row in (t.get("defaults") or {}).items():
            count = "—" if row["count"] is None else row["count"]
            basis = COUNT_BASIS_LABEL.get(row.get("count_evidence"), "")
            print(f"    既定 {rule_id}: {row['scope']}・{_window_text(row['window'])}・{count}{basis}・"
                  f"{row['on_exceed']}（{row['purpose']}）")
        if t.get("notes"):
            print(f"    注: {t['notes']}")


# 既定の数の根拠（段 2・中-2）。推測の数は「推測・確かさ low」と言う（項目の high を数に付けない）。
COUNT_BASIS_LABEL = {"inferred": "（数は推測・確かさ low）", "thth_line": "（数は THTH の線）",
                     "official_doc": "（数は公式の文書）", "official_statement": "（数は公式の発言）",
                     "observed": "（数は観測）"}


def cmd_traits(args) -> int:
    try:
        payload = build(args.media, signal=args.signal)
    except mt.MediaTraitsError as exc:
        return _fail(args, 2 if exc.code == mt.UNKNOWN_MEDIA else 1, exc)
    if args.json:
        _print_json(payload)
    else:
        render(payload)
    return 0


def full_check() -> dict:
    """台帳の検査に加えて、柵の表を作れること（`build_policy(collect_defaults())`・`check_table`）も見る。

    台帳の検査は通るのに柵の入口だけが断る（写像の食い違いなど）を無くす（段 2・監査 中-2）。
    """
    report = mt.check()
    if report["problems"]:
        return report
    from . import limits_policy
    try:
        table = limits_policy.build_policy(mt.collect_defaults())
        found = limits_policy.check_table(table)
    except ValueError as exc:
        found = [str(exc)]
    except Exception as exc:  # noqa: BLE001
        found = [exc.__class__.__name__]
    if found:
        report = dict(report, ok=False, problems=[
            {"code": "policy_build_failed", "media": "-", "trait": None, "detail": text} for text in found])
    return report


def cmd_check(args) -> int:
    report = full_check()
    if args.json:
        _print_json(report)
    elif report["ok"]:
        print(f"媒体の特性の台帳: {len(report['media'])} 媒体・{report['traits']} 項目、検査を通りました")
    else:
        print(f"媒体の特性の台帳: 検査に落ちました（{len(report['problems'])} 件）", file=sys.stderr)
        for p in report["problems"]:
            print(_problem_line(p), file=sys.stderr)
    return 0 if report["ok"] else 1


def _since_days(text) -> int | None:
    value = str(text or "").strip().lower()
    if value.endswith("d"):
        value = value[:-1]
    try:
        days = int(value)
    except ValueError:
        return None
    return days if 1 <= days <= 366 else None


def cmd_observations(args) -> int:
    """`thth media observations <媒体>`（段 4・§9.2）。集めて（冪等）から数える。"""
    from . import media_observations as mo
    days = _since_days(args.since)
    if days is None:
        return _observation_fail(args, "invalid_since", "--since は 1〜366 の日数（例 30d）です")
    if args.media not in mt.list_media():
        return _observation_fail(args, mt.UNKNOWN_MEDIA, f"知らない媒体です: {args.media}")
    try:
        added = {} if args.no_collect else mo.collect(args.media)
        s = mo.summary(args.media, since_days=days)
    except mo.ObservationError as exc:
        return _observation_fail(args, exc.code, "観測を読めません")
    s["collected"] = added.get(args.media, 0)
    if args.json:
        _print_json(s)
    else:
        for line in mo.summary_lines(s):
            print(line)
        print(f"  （今回足した観測 {s['collected']} 行・台帳を直すのは開発のセッション）")
    return 0


def cmd_observe(args) -> int:
    """`thth media observe <媒体> --kind … --note … --by <名前>`（段 4・§9.2）。"""
    import os
    from . import media_observations as mo
    by = (args.by or os.environ.get("THTH_ACTOR") or "").strip()
    if not by:
        return _observation_fail(args, "by_required", "--by <名前> を付けてください")
    try:
        row = mo.observe(args.media, args.kind, note=args.note, record=args.record, by=by)
    except mo.ObservationError as exc:
        return _observation_fail(args, exc.code, "観測を足せません（媒体・kind・note を確かめてください）")
    except ValueError:
        return _observation_fail(args, "by_required", "--by <名前> を付けてください")
    if args.json:
        _print_json({"ok": True, **row})
    else:
        print(f"{args.media}: 観測を足しました（{row['kind']}）")
    return 0


def _observation_fail(args, code, message) -> int:
    if args.json:
        _print_json({"ok": False, "error": code, "message": message})
    else:
        print(f"{code}: {message}", file=sys.stderr)
    return 2
