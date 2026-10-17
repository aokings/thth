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
            print(f"    既定 {rule_id}: {row['scope']}・{_window_text(row['window'])}・{count}・"
                  f"{row['on_exceed']}（{row['purpose']}）")
        if t.get("notes"):
            print(f"    注: {t['notes']}")


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


def cmd_check(args) -> int:
    report = mt.check()
    if args.json:
        _print_json(report)
    elif report["ok"]:
        print(f"媒体の特性の台帳: {len(report['media'])} 媒体・{report['traits']} 項目、検査を通りました")
    else:
        print(f"媒体の特性の台帳: 検査に落ちました（{len(report['problems'])} 件）", file=sys.stderr)
        for p in report["problems"]:
            print(_problem_line(p), file=sys.stderr)
    return 0 if report["ok"] else 1
