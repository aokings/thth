"""`thth events <account|project> [--since <時刻|24h> | --after <カーソル|event_id>] [--json]`
——出来事の流れ（4.0 の柱 1・計画 §3.3・外部レビュー 10/16）。

**読むだけ。** 既にある記録（`sent/`・返信の台帳・言及の inbox・安全装置の印・
`publish_hold`・inflight・X の予算）から、出来事を 1 本の流れに並べる。媒体の API は
呼ばない。何も書かない（栞も runs も）。

1 件の形（各行に `schema: "thth.events/1"`）:

    {"schema", "kind", "account",
     "occurred_at"（起きた時刻・JST の ISO）, "recorded_at"（THTH が記録した時刻）,
     "at"（= occurred_at。無ければ recorded_at）, "ref"（post_id 等）, "summary"（1 行）,
     "event_id"}

`event_id` は記録の中身（種類・口座・ref・起きた時刻）の雑湊で、**同じ記録から何度読んでも
同じ**。**重複を除くのは受け手の仕事**——同じ出来事が `--since` の重なりや、遅れて記録された
出来事を拾うための読み直しで 2 度届くことがある。受け手は `event_id` で 1 度だけ扱う。

ほかに足すことがある鍵: `post_id`（返信の付いた自分の投稿）・`ongoing`（今も続いている状態）。

種類（`KINDS`。増やしはしても、名前を変えない・消さない）:

- `published`: 公開した（`sent/` の `sent_at`）。summary は自分の投稿の題（先頭 60 字）。
- `retracted`: 取り下げた（`sent/` の `retracted_at`）。
- `reply_received`: 自分の投稿に他人の返信が付いた（返信の台帳）。**他人の本文・username は
  入れない**——summary は件数と自分の投稿の題まで（保存しない決まりの線）。本文は `thth thread`。
  recorded_at は THTH が採った時刻（`collected_at`）。
- `mention_received`: 言及が届いた（`data/sns/inbox/`）。本文・username は入れない。
- `account_stopped`: 安全装置で口座が止まっている（`guard`）。
- `publish_held`: 媒体の断りで自動の公開を止めている（`publish_hold`）。
- `inflight_open`: 公開の結果待ちが残っている（inflight）。
- `budget_exhausted`: X の読み取り・投稿の予算で断っている（X の口座だけ・始まりの時刻は無い）。
- `delivery_published`・`delivery_updated`・`delivery_deleted`・`delivery_unknown`・`delivery_conflict`・
  `bookmarked`・`bookmark_deleted`（4.0 S1 §8）: はてなの配信の台帳の状態の変わり目。`ref` は配信 id、
  `event_id` は `"ev_" + sha256("thth.delivery/1|<delivery>|<seq>")[:24]`（種類も時刻も入れない——
  記録の前に落ちて人が同じ seq で解いても同じ id）。はてなの `sent/` の行は `published`／`retracted`
  に出さない（同じ出来事を 2 つの名前で出さない）。
- `principal_stopped`（4.0 S1 §4.1）: 主体（はてな ID）が止まっている。その主体を使う口座ごとに 1 件。

読み方は 2 つ:

- `--since`（既定 24h）: 起きた時刻か記録した時刻のどちらかが窓の中なら出す。止まっている
  状態（`ongoing: true`）は窓に関わらず、今も続いていれば必ず出す。
- `--after <next_cursor>`: 前に受け取った答えの `next_cursor` から続きを読む。並びは
  **記録した時刻**の順（同じ時刻は event_id の順）。採取の 1 回の中で付いた時刻が、書き込みより
  少し前になることがあるので、カーソルは最後の時刻から `CURSOR_MARGIN` だけ遡った間に受け取った
  event_id を覚えていて、その間に遅れて記録されたものも取りこぼさずに返す（届いたものは
  返さない）。始まりの時刻が無い状態（予算）は毎回返す（受け手が event_id で除く）。
  `--after <event_id>` は、今読める流れの中でその出来事より後ろから。

`runs-*.ndjson` は使わない: 投稿の行は時刻を持たない（`run_id` は乱数）ので、窓で切れない。
公開の事実は `sent/`（正本）から取る。

押す口（h2d fires record・hub status post）は作っていない。読みに来る口だけ。作るときは、
通知の失敗で本処理（run・collect）を失敗扱いにしない・失敗そのものは記録する・重複は受け手が
event_id で除く、の 3 つを守る。

出所ごとに `try` で囲む。1 つの記録が読めなくても他は出し、読めなかったものは `cannot_say` に
`<出所>_unreadable` の符丁で言う（0 件と混ぜない）。
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import re
import sys

from . import accounts as accounts_mod
from . import jst

EVENTS_SCHEMA = "thth.events/1"
KINDS = ("published", "retracted", "reply_received", "mention_received",
         "account_stopped", "publish_held", "inflight_open", "budget_exhausted",
         # 4.0 S1 §8: はてなの配信の台帳から（id は配信 id ＋ 通し番号だけで決まる）と主体の止め。
         "delivery_published", "delivery_updated", "delivery_deleted", "delivery_unknown",
         "delivery_conflict", "bookmarked", "bookmark_deleted", "principal_stopped")
# 配信の台帳の出来事の id の版の語（設計 4.0 S1 §8.1）。
DELIVERY_EVENT_PREFIX = "thth.delivery/1"
# はてなの `sent/` の行（`kind` あり）。配信の台帳の出来事と同じことを 2 つの名前で出さない。
_HATENA_SENT_KINDS = ("entry_create", "entry_update", "entry_delete", "bookmark_add", "bookmark_delete")
DEFAULT_SINCE = "24h"
TITLE_CHARS = 60
# カーソルが覚えておく幅（採取の 1 回の長さより長く）と、覚える event_id の上限。
CURSOR_MARGIN = datetime.timedelta(minutes=30)
CURSOR_SEEN_MAX = 500
CURSOR_PREFIX = "c1."
EVENT_ID = re.compile(r"ev_[0-9a-f]{24}")


class EventsError(ValueError):
    """問いが受け取れない（対象が無い・since が読めない・カーソルが読めない）。"""


def _title(text) -> str:
    from . import threads_read_cli
    return threads_read_cli._one_line(text, TITLE_CHARS)


def event_id(kind, account, ref, occurred) -> str:
    """記録の中身から決まる id（同じ記録なら何度読んでも同じ）。"""
    raw = "|".join((kind, account, "" if ref is None else str(ref),
                    jst.iso(occurred) if occurred is not None else ""))
    return "ev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def delivery_event_id(delivery, seq) -> str:
    """配信の台帳から出る出来事の id（設計 4.0 S1 §8.1）。**配信 id と通し番号だけ**で決まる
    ——種類も時刻も入れない（時刻は記録のたびに揺れうる。delivery と seq は書いた瞬間に決まる）。
    記録の前に落ちて人が解いても、同じ seq で書くので同じ id になる（§8.2）。"""
    raw = f"{DELIVERY_EVENT_PREFIX}|{delivery}|{seq}"
    return "ev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _event(kind, account, occurred, recorded, ref, summary, *, _id=None, **extra) -> dict:
    occurred_at = jst.iso(occurred) if occurred is not None else None
    recorded_at = jst.iso(recorded) if recorded is not None else None
    row = {"schema": EVENTS_SCHEMA, "kind": kind, "account": account,
           "occurred_at": occurred_at, "recorded_at": recorded_at,
           "at": occurred_at or recorded_at, "ref": ref, "summary": summary,
           "event_id": _id or event_id(kind, account, ref, occurred)}
    row.update({key: value for key, value in extra.items() if value is not None})
    return row


# ------------------------------------------------------------------ 出所ごと

def _sent_events(name, titles):
    from . import sent as sent_mod
    out = []
    for row in sent_mod.records(accounts_mod.state_dir_for(name)):
        post_id = row.get("post_id")
        if row.get("kind") in _HATENA_SENT_KINDS:
            continue  # はてなの行は配信の台帳の出来事として出す（同じ出来事を 2 つの名前で出さない）
        titles[post_id] = _title(row.get("text"))
        at = jst.parse(row.get("sent_at"))
        if at is not None:
            out.append(_event("published", name, at, at, post_id, f"公開: {titles[post_id]}"))
        at = jst.parse(row.get("retracted_at"))
        if at is not None:
            out.append(_event("retracted", name, at, at, post_id, f"取り下げ: {titles[post_id]}"))
    return out


def _reply_events(name, titles, allowed_names):
    from . import replies as replies_mod
    data = replies_mod.load(name, owned_only=True,
                            **({} if allowed_names is None else {"allowed_names": tuple(allowed_names)}))
    out = []
    for row in data["replies"]:
        if row.get("own") is not False:
            continue   # 自分の返信・持ち主が判らない行は「届いた返信」に数えない
        at, seen = jst.parse(row.get("timestamp")), jst.parse(row.get("collected_at"))
        if at is None and seen is None:
            continue
        root = row.get("post_id")
        title = titles.get(root)
        summary = f"返信 1 件（自分の投稿「{title}」へ）" if title else "返信 1 件（自分の投稿へ）"
        out.append(_event("reply_received", name, at, seen or at, row.get("reply_id"), summary,
                          post_id=root))
    return out, bool(data.get("broken"))


def _mention_events(name, cfg):
    folder = accounts_mod.data_dirs(cfg, name)["inbox"]
    try:
        files = sorted(f for f in os.listdir(folder) if f.endswith(".ndjson"))
    except FileNotFoundError:
        return [], False
    out, broken = [], False
    medium = cfg.get("media")
    for file_name in files:
        try:
            with open(os.path.join(folder, file_name), encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]
        except (OSError, ValueError):
            broken = True
            continue
        for row in rows:
            if not isinstance(row, dict) or not row.get("message_id"):
                continue
            # 置き場は同じ repo の口座で共有しうる。媒体が違う行はこの口座のものではない。
            if row.get("medium") not in (None, medium):
                continue
            at, seen = jst.parse(row.get("timestamp")), jst.parse(row.get("collected_at"))
            if at is None and seen is None:
                continue
            kind = row.get("mention_kind")
            summary = f"言及 1 件（{kind}）" if isinstance(kind, str) and kind else "言及 1 件"
            out.append(_event("mention_received", name, at, seen or at, row["message_id"], summary))
    return out, broken


def _delivery_kind(row):
    """台帳の history の 1 行 → 出来事の種類（出さない行は None）。`inflight`・`wanted` は出さない。"""
    bookmark = row.get("bookmark")
    if bookmark == "added":
        return "bookmarked"
    if bookmark == "deleted":
        return "bookmark_deleted"
    state = row.get("state")
    if state == "published":
        return "delivery_updated" if row.get("op") == "update" else "delivery_published"
    return {"deleted": "delivery_deleted", "unknown": "delivery_unknown",
            "conflict": "delivery_conflict"}.get(state)


def _delivery_events(name):
    """配信の台帳の状態の変わり目（設計 4.0 S1 §8.1）。`(出来事, 読めない台帳があったか)`。"""
    from . import deliveries as deliveries_mod
    records, broken = deliveries_mod.list_for(name)
    out = []
    for record in records:
        title = record.get("article") or record["delivery"]
        for row in record["history"]:
            kind = _delivery_kind(row)
            if kind is None:
                continue
            at = jst.parse(row.get("at"))
            summary = f"{kind}: {title}（{record.get('blog_id')}）"
            out.append(_event(kind, record["account"], at, at, record["delivery"], summary,
                              _id=delivery_event_id(record["delivery"], row["seq"]),
                              seq=row["seq"], url=row.get("published_url")))
    return out, bool(broken)


def _state_events(name, cfg):
    """今も続いている止まりの状態。"""
    from . import guard, inflight as inflight_mod, publish_hold
    out, cannot = [], []
    try:
        row = guard.stopped(name)
    except Exception:  # noqa: BLE001 — 読めない印は「止まっている」側で言う
        row = {"reason": "guard_state_unreadable"}
    if row:
        at = jst.parse(row.get("stopped_at"))
        out.append(_event("account_stopped", name, at, at, row.get("reason"),
                          f"口座を止めています（{row.get('reason')}）", ongoing=True))
    try:
        row = publish_hold.held(name)
    except Exception:  # noqa: BLE001
        row = {"reason": publish_hold.UNREADABLE}
    if row:
        at = jst.parse(row.get("held_at"))
        out.append(_event("publish_held", name, at, at, row.get("file") or row.get("reason"),
                          f"自動の公開を止めています（{row.get('reason')}）", ongoing=True))
    try:
        row = inflight_mod.read(accounts_mod.state_dir_for(name))
    except (OSError, ValueError):
        row = None
        cannot.append("inflight_unreadable")
    if isinstance(row, dict):
        file_name = os.path.basename(row.get("file") or "") or None
        at = jst.parse(row.get("started"))
        # はてなの配信は ref に配信 id（設計 4.0 S1 §8.1）。SNS は今までどおり。
        out.append(_event("inflight_open", name, at, at,
                          row.get("delivery") or row.get("post_id") or file_name,
                          f"公開の結果待ち（{file_name or '原稿名なし'}）", ongoing=True))
    if cfg.get("media") == "hatenablog" and cfg.get("principal"):
        from . import principals
        try:
            row = principals.stopped(cfg["principal"])
        except Exception:  # noqa: BLE001 — 読めない印は止まっている側
            row = {"reason": "guard_state_unreadable"}
        if row:
            at = jst.parse(row.get("stopped_at"))
            out.append(_event("principal_stopped", name, at, at, cfg["principal"],
                              f"主体 {cfg['principal']} を止めています（{row.get('reason')}）", ongoing=True))
    if cfg.get("media") == "x":
        try:
            from . import budget_x, budget_x_posts
            codes = [code for code in (budget_x.report().get("read_refusal"),
                                       budget_x_posts.report().get("post_refusal")) if code]
        except Exception:  # noqa: BLE001
            codes = []
            cannot.append("budget_unreadable")
        for code in codes:
            out.append(_event("budget_exhausted", name, None, None, code,
                              f"X の予算で断っています（{code}）", ongoing=True))
    return out, cannot


# ------------------------------------------------------------------ カーソル

def _key(row):
    """記録した時刻の順（同じ時刻は event_id の順）。時刻の無いものは後ろ。"""
    at = jst.parse(row["recorded_at"]) if row["recorded_at"] else None
    return (at is None, at or datetime.datetime.max.replace(tzinfo=jst.JST), row["event_id"])


def encode_cursor(t, seen) -> str:
    raw = json.dumps({"t": jst.iso(t), "seen": sorted(seen)[:CURSOR_SEEN_MAX]}, separators=(",", ":"))
    return CURSOR_PREFIX + base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(value):
    try:
        if not value.startswith(CURSOR_PREFIX):
            raise ValueError
        body = value[len(CURSOR_PREFIX):]
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        t = jst.parse(data["t"])
        seen = data["seen"]
        if t is None or not isinstance(seen, list) or not all(
                isinstance(x, str) and EVENT_ID.fullmatch(x) for x in seen):
            raise ValueError
        return t, set(seen)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise EventsError("invalid_cursor") from None


def _next_cursor(returned, previous):
    """返した分（と前のカーソル）から次のカーソル。返したものが無ければ前のまま。"""
    timed = [row for row in returned if row["recorded_at"]]
    t_prev, seen_prev = previous if previous else (None, set())
    times = [jst.parse(row["recorded_at"]) for row in timed] + ([t_prev] if t_prev else [])
    if not times:
        return None
    t = max(times)
    floor = t - CURSOR_MARGIN
    seen = {row["event_id"] for row in timed if jst.parse(row["recorded_at"]) >= floor}
    if t_prev is not None and t_prev >= floor:
        seen |= seen_prev
    return encode_cursor(t, seen)


# ------------------------------------------------------------------ 組み立て

def build(target, *, since=None, after=None, now=None, allowed_names=None, limit=None) -> dict:
    """出来事の流れ。**読むだけ。** `allowed_names` が渡されたら、その外の口座は読まない。"""
    from . import morning, read_window
    now = now if now is not None else jst.now_jst()
    if since is not None and after is not None:
        raise EventsError("since_and_after")
    floor = None
    if after is None:
        try:
            floor = read_window.cutoff(since or DEFAULT_SINCE, now=now)
        except ValueError:
            raise EventsError("invalid_since") from None
    names = morning.targets(target)
    kind = "account" if names == [target] else "project"
    if allowed_names is not None:
        names = [name for name in names if name in allowed_names]
    if not names:
        raise EventsError("target_unknown")

    events, cannot_say, read = [], set(), []
    for name in names:
        try:
            cfg = accounts_mod.load_account(name)
        except (accounts_mod.AccountError, ValueError, TypeError):
            cannot_say.add("account_unreadable")
            continue
        read.append(name)
        titles: dict = {}
        try:
            events += _sent_events(name, titles)
        except Exception:  # noqa: BLE001 — 1 つの出所で全部を落とさない
            cannot_say.add("sent_unreadable")
        try:
            rows, broken = _reply_events(name, titles, allowed_names)
            events += rows
            if broken:
                cannot_say.add("replies_unreadable")
        except Exception:  # noqa: BLE001
            cannot_say.add("replies_unreadable")
        try:
            rows, broken = _mention_events(name, cfg)
            events += rows
            if broken:
                cannot_say.add("inbox_unreadable")
        except Exception:  # noqa: BLE001
            cannot_say.add("inbox_unreadable")
        if cfg.get("media") == "hatenablog":
            try:
                rows, broken = _delivery_events(name)
                events += rows
                if broken:
                    cannot_say.add("deliveries_unreadable")
            except Exception:  # noqa: BLE001
                cannot_say.add("deliveries_unreadable")
        rows, cannot = _state_events(name, cfg)
        events += rows
        cannot_say.update(cannot)
    events.sort(key=_key)

    previous = None
    if after is None:
        def keep(row):
            if row.get("ongoing"):
                return True
            return any(row[k] and jst.parse(row[k]) >= floor for k in ("occurred_at", "recorded_at"))
        chosen = [row for row in events if keep(row)]
    elif EVENT_ID.fullmatch(after):
        index = next((i for i, row in enumerate(events) if row["event_id"] == after), None)
        if index is None:
            raise EventsError("cursor_unknown")
        chosen = events[index + 1:]
    else:
        previous = decode_cursor(after)
        t, seen = previous
        margin_floor = t - CURSOR_MARGIN

        def keep(row):
            if not row["recorded_at"]:
                return True    # 始まりの時刻が無い状態（予算）は毎回。受け手が event_id で除く
            at = jst.parse(row["recorded_at"])
            return at > t or (at >= margin_floor and row["event_id"] not in seen)
        chosen = [row for row in events if keep(row)]

    total = len(chosen)
    if limit is not None and total > limit:
        # 続きを読む形（--after）は古い方から、窓で見る形（--since）は新しい方を残す。
        chosen = chosen[:limit] if after is not None else chosen[-limit:]
    by_kind = {name: 0 for name in KINDS}
    for row in chosen:
        by_kind[row["kind"]] += 1
    return {"schema": EVENTS_SCHEMA, "report_type": "events", "generated_at": jst.iso(now),
            "target": target, "target_kind": kind, "accounts": read,
            "since": jst.iso(floor) if floor is not None else None, "after": after,
            "n": len(chosen), "n_total": total, "by_kind": by_kind,
            "events": chosen, "next_cursor": _next_cursor(chosen, previous),
            "order": "recorded_at, event_id",
            "cannot_say": sorted(cannot_say),
            "limitations": [
                "読むだけ。媒体の API は呼ばず、THTH の記録だけから並べる",
                "他人の投稿の本文・username は入れない（本文は thth thread で読む）",
                "同じ出来事が 2 度届くことがある。受け手が event_id で重複を除く",
                "止まっている状態（ongoing）は --since の窓に関わらず、今も続いていれば出す",
                "公開は THTH を通したもの（sent/）だけ。手で出した投稿は入らない"]}


# ------------------------------------------------------------------ CLI

def render(payload, out=print) -> None:
    head = f"{payload['since']} から" if payload.get("since") else "カーソルの続きから"
    out(f"出来事  {payload['target']}（{payload['target_kind']}）  {head}  {payload['n']} 件")
    for row in payload["events"]:
        mark = "（続いている）" if row.get("ongoing") else ""
        out(f"  {row['at'] or '時刻不明'}  {row['account']}  {row['kind']}  {row['summary']}{mark}")
    if payload.get("next_cursor"):
        out(f"続き: --after {payload['next_cursor']}")
    if payload["cannot_say"]:
        out("読めなかったもの: " + "・".join(payload["cannot_say"]))


def register(sub) -> None:
    p = sub.add_parser("events", help="出来事の流れ（公開・返信・言及・止まり・予算）を時刻順に（読むだけ）")
    p.add_argument("account", metavar="target", help="account 名か project 名")
    p.add_argument("--since", default=None, help="窓の始まり（24h・7d・ISO 時刻・既定 24h）")
    p.add_argument("--after", default=None,
                   help="前の答えの next_cursor（か event_id）から続きを読む（--since とは一緒に使わない）")
    p.add_argument("--limit", type=int, default=None, help="何件まで（--since は新しい方・--after は古い方から）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd)


def cmd(args) -> int:
    limit = getattr(args, "limit", None)
    try:
        if limit is not None and limit < 1:
            raise EventsError("invalid_limit")
        payload = build(args.account, since=args.since, after=getattr(args, "after", None), limit=limit)
    except EventsError as exc:
        print(str(exc), file=sys.stderr)
        if args.json:
            print(json.dumps({"schema": EVENTS_SCHEMA, "cannot_say": [str(exc)]}, ensure_ascii=False))
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    render(payload)
    return 0
