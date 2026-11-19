"""`thth gbp …`（Google ビジネス プロフィール・持ち主か管理者の店だけ）。

    thth gbp auth [--no-browser] [--timeout 秒]     利用者の Google アカウントで 1 度だけ認可する（端末から）
    thth gbp doctor [--json]                        client と認可の有無・scope・アカウントが読めるか
    thth gbp locations [--json]                     見えるアカウントの店（id と名前）
    thth gbp show <店> [--json]                     営業の時間・臨時の時間・営業の状態・サイトの URL（画面に出すだけ）
    thth gbp special <店> --date YYYY-MM-DD (--closed | --open HH:MM --close HH:MM) --by 名前 [--confirm D]
                                                    臨時の時間を 1 日足すか、同じ日を置き換える（二段確認）
    thth gbp posts <店> [--limit N] [--json]        投稿の一覧
    thth gbp post <店> --text 本文 [--url URL [--action LEARN_MORE|BOOK|ORDER|SHOP|SIGN_UP]] --by 名前 [--confirm D]
                                                    ふつうの投稿（STANDARD・日本語）を 1 つ出す（二段確認）
    thth gbp post <店> --event 題 --start "YYYY-MM-DD HH:MM" --end "YYYY-MM-DD HH:MM" --text 本文 … --by 名前 [--confirm D]
                                                    イベントの投稿（EVENT・題と始まりと終わり）を 1 つ出す（二段確認）

<店> は `locations/123…`（`thth gbp locations` に出る）か `accounts/…/locations/…`。

**残さない**: Google から読んだ中身（店の欄・投稿）は画面（か --json の出力）に出すだけで、THTH はファイルに書かない
（Google の決まりで、口の中身は性能のための一時の保存でも 30 日まで。この口は貯めない）。
**書くのは持ち主の二段確認のあとだけ**: 一段目は何も変えずに、送る中身と digest を出す（rc 1）。同じ命令に
`--confirm <digest>` を付けた二段目で、いまの店を読み直して digest が同じときだけ Google に送る。
client の JSON は `thth gads` と同じ。認可は `$THTH_GOOGLE_GBP_TOKEN`（無ければ `~/.config/thth/google-gbp-token.json`・600）。
MCP と遠くの道にはまだ出さない。
"""
from __future__ import annotations

import argparse
import builtins
import datetime
import json
import os
import re
import shlex
import stat

from .. import jst, redact, refusal
from . import api, auth
from .wire import GbpRefused

VERBS = ("auth", "doctor", "locations", "show", "special", "posts", "post")
# Google から来た店の名前・本文に端末を操る字（ESC など）が入っていても、そのまま端末に出さない
# （監査 5.27.0 の 11）。改行とタブは残す。JSON は json.dumps が先に逃がすので形は変わらない。
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x9b]")


def print(*items, **kwargs):  # noqa: A001 — この口の出力は全部ここを通す
    builtins.print(*(_CONTROL.sub("?", str(item)) for item in items), **kwargs)

MAX_BY = 256
DAY_JA = {"MONDAY": "月", "TUESDAY": "火", "WEDNESDAY": "水", "THURSDAY": "木", "FRIDAY": "金", "SATURDAY": "土",
          "SUNDAY": "日"}
OPEN_JA = {"OPEN": "営業中", "CLOSED_TEMPORARILY": "臨時休業", "CLOSED_PERMANENTLY": "閉業"}
STATE_JA = {"LIVE": "公開", "PROCESSING": "処理中", "REJECTED": "却下"}
NOT_SAVED = "（Google から読んだ中身は保存しません）"
# 詳細が Google の理由の名前の符丁（ほかは THTH の側の静的な語）。
FROM_GOOGLE = frozenset(("gbp_location_unknown", "gbp_permission_denied", "gbp_not_allowlisted", "gbp_api_not_enabled",
                         "gbp_authentication_error", "gbp_quota_exceeded", "gbp_request_rejected", "gbp_unavailable",
                         "gbp_write_unknown", "gbp_token_revoked", "gbp_token_refresh_failed",
                         "gbp_auth_exchange_failed", "gbp_auth_denied"))


def _refuse(args, exc: GbpRefused, rc: int = 2, **extra) -> int:
    found = refusal.parts(exc.code) or {}
    line = f"{exc.code}: {found.get('why') or exc.code}"
    if exc.detail:
        line += f"（Google: {exc.detail}）" if exc.code in FROM_GOOGLE else f"（{exc.detail}）"
    payload = {"ok": False, "code": exc.code, "detail": exc.detail, **extra}
    return refusal.emit(args, exc.code, line=redact.redact(line), payload=payload, rc=rc)


def _emit(value, args, render) -> int:
    if getattr(args, "json", False):
        print(json.dumps(value, ensure_ascii=False))
    else:
        render(value)
    return 0


def _client(args):
    return api.Client(transport=getattr(args, "transport", None))


def today_jst() -> datetime.date:
    """臨時の時間の「過去の日」は日本の日付で切る（店は日本にある）。"""
    return jst.now_jst().date()


def _by(args) -> str | None:
    by = (getattr(args, "by", None) or os.environ.get("THTH_ACTOR") or "").strip()
    if not by or len(by) > MAX_BY or any(ord(c) < 32 or ord(c) == 127 for c in by):
        return None
    return by


def _no_by(args) -> int:
    return refusal.emit(args, "by_required", line="by_required: --by を付けてください（誰が頼んだかを一段目と二段目に"
                        "出します）。環境変数 THTH_ACTOR でも指定できます", payload={"ok": False, "code": "by_required"},
                        rc=1)


def _stage_one(args, *, digest, payload, lines, again) -> int:
    """一段目: 見せて digest を出し、何もしない（rc 1）。rc 1 は断りではない（印を付ける・監査 5.27.0 の 7）。"""
    from .. import refusals
    refusals.mark_first_stage()
    if args.json:
        print(json.dumps({"ok": False, "stage": 1, "digest": digest, **payload}, ensure_ascii=False))
        return 1
    for line in lines:
        print(line)
    print(f"digest: {digest}")
    print(f"送るなら: {again} --confirm {digest}")
    return 1


def _quote(text: str) -> str:
    """そのまま貼って打てる形（改行を含む本文も同じ値で届く）。"""
    return shlex.quote(text)


# ------------------------------------------------------------------ auth・doctor

def cmd_auth(args) -> int:
    try:
        value = auth.run(open_browser=not args.no_browser, timeout=args.timeout,
                         transport=getattr(args, "transport", None))
    except GbpRefused as exc:
        return _refuse(args, exc)
    print(f"Google ビジネス プロフィールの認可を残しました: {value['token_path']}（{value['mode']}・中身は表示しません）")
    print("次の一手: thth gbp doctor で、アカウントが読めるかを確かめてください")
    return 0


def cmd_doctor(args) -> int:
    """順に確かめ、最初に止まった所を前向きな断りで出す（秘密は出さない・中身は表示しない）。"""
    checks = []

    def ok(name, detail):
        checks.append({"check": name, "ok": True, "detail": detail})

    try:
        auth.load_client()
        ok("client", auth.client_path())
        token = auth.load_token()
        mode = stat.S_IMODE(os.stat(auth.token_path()).st_mode)
        ok("token", f"{auth.token_path()}（{mode:o}）")
        if auth.SCOPE not in auth.token_scopes(token):
            raise GbpRefused("gbp_scope_missing")
        ok("scope", auth.SCOPE)
        accounts = _client(args).accounts()
        ok("accounts", f"{len(accounts)} 件")
    except GbpRefused as exc:
        checks.append({"check": ("client", "token", "scope", "accounts")[len(checks)], "ok": False,
                       "detail": exc.code})
        if not args.json:
            for row in checks[:-1]:
                print(f"  済 {row['check']}: {row['detail']}")
        return _refuse(args, exc, checks=checks)
    value = {"ok": True, "checks": checks, "accounts": accounts}

    def render(v):
        print("Google ビジネス プロフィールの口の点検:")
        for row in v["checks"]:
            print(f"  済 {row['check']}: {row['detail']}")
        for a in v["accounts"]:
            print(f"  {a['account']}  {a['name'] or '—'}  {a['type'] or ''}")
        print("次の一手: thth gbp locations で店の id を確かめてください")
    return _emit(value, args, render)


# ------------------------------------------------------------------ locations・show

def cmd_locations(args) -> int:
    try:
        rows = _client(args).all_locations()
    except GbpRefused as exc:
        return _refuse(args, exc)

    def render(v):
        if not v["locations"]:
            print("この Google アカウントが持つか任された店は 0 件でした（ビジネス プロフィールの画面で、この"
                  "アカウントがオーナーか管理者になっているかを確かめてから打ち直してください）")
            return
        print(f"この Google アカウントが持つか任された店（{len(v['locations'])} 件）{NOT_SAVED}:")
        for r in v["locations"]:
            print(f"  {r['location']}  {r['title'] or '—'}  {OPEN_JA.get(r['open_status'], '')}  ({r['account']})")
        print("次の一手: thth gbp show <店> で営業の時間と臨時の時間を見られます")
    return _emit({"locations": rows, "storage": "display_only"}, args, render)


def _regular_lines(location) -> list[str]:
    regular = location.get("regularHours") if isinstance(location.get("regularHours"), dict) else {}
    periods = [p for p in regular.get("periods", []) if isinstance(p, dict)] \
        if isinstance(regular.get("periods"), list) else []
    order = {d: i for i, d in enumerate(api.DAYS)}
    periods.sort(key=lambda p: (order.get(p.get("openDay"), 9), api.time_text(p.get("openTime"))))
    out = []
    for p in periods:
        tail = "" if p.get("closeDay") == p.get("openDay") else f"（{DAY_JA.get(p.get('closeDay'), '?')}）"
        out.append(f"{DAY_JA.get(p.get('openDay'), '?')} {api.time_text(p.get('openTime'))}〜"
                   f"{api.time_text(p.get('closeTime'))}{tail}")
    return out


def show_value(location: dict, today: datetime.date) -> dict:
    info = location.get("openInfo") if isinstance(location.get("openInfo"), dict) else {}
    meta = location.get("metadata") if isinstance(location.get("metadata"), dict) else {}
    special = []
    for p in api.periods_of(location):
        day = api.date_of(p.get("startDate"))
        special.append({"text": api.period_text(p), "past": bool(day and day < today), "period": p})
    status = info.get("status")
    return {"location": location.get("name"), "title": location.get("title") if isinstance(location.get("title"), str)
            else None, "website": location.get("websiteUri") if isinstance(location.get("websiteUri"), str) else None,
            "open_status": status if status in api.OPEN_STATUSES else None,
            "regular_hours": _regular_lines(location),
            "special_hours": special,
            "maps": meta.get("mapsUri") if isinstance(meta.get("mapsUri"), str) else None,
            "storage": "display_only"}


def cmd_show(args) -> int:
    try:
        _, location_id = api.parse_location(args.location)
        location = _client(args).location(location_id)
    except GbpRefused as exc:
        return _refuse(args, exc)
    value = show_value(location, today_jst())

    def render(v):
        print(f"{v['title'] or '—'}（{v['location']}）{NOT_SAVED}")
        print(f"  営業の状態: {OPEN_JA.get(v['open_status'], '—')}")
        print(f"  サイト: {v['website'] or '—'}")
        print("  営業の時間: " + ("・".join(v["regular_hours"]) if v["regular_hours"] else "なし（臨時の時間を書く前に、"
                                                                                       "ビジネス プロフィールの画面で営業の時間を入れてください）"))
        if v["special_hours"]:
            print(f"  臨時の時間（{len(v['special_hours'])} 件）:")
            for s in v["special_hours"]:
                print(f"    {s['text']}{'（過ぎた日）' if s['past'] else ''}")
        else:
            print("  臨時の時間: なし")
        if v["maps"]:
            print(f"  Google マップ: {v['maps']}")
    return _emit(value, args, render)


# ------------------------------------------------------------------ special（二段）

def _special_args(args) -> tuple[datetime.date, dict | None, dict | None]:
    day = api.parse_date(args.date)
    if day < today_jst():
        raise GbpRefused("gbp_date_past")
    if args.closed == (args.open is not None or args.close is not None):
        raise GbpRefused("gbp_hours_args_invalid")
    if args.closed:
        return day, None, None
    if args.open is None or args.close is None:
        raise GbpRefused("gbp_hours_args_invalid")
    return day, api.parse_time(args.open), api.parse_time(args.close)


def cmd_special(args) -> int:
    try:
        _, location_id = api.parse_location(args.location)
        day, open_time, close_time = _special_args(args)
        period = api.special_period(day, closed=args.closed, open_time=open_time, close_time=close_time)
    except GbpRefused as exc:
        return _refuse(args, exc)
    by = _by(args)
    if by is None:
        return _no_by(args)
    try:
        client = _client(args)
        location = client.location(location_id)
        if not api.has_regular_hours(location):
            raise GbpRefused("gbp_regular_hours_missing")
        current = api.periods_of(location)
        merged, dropped = api.replace_day(current, period)
        digest = api.digest_of("special", f"locations/{location_id}", period, current)
        if not args.confirm:
            title = location.get("title") if isinstance(location.get("title"), str) else "—"
            what = "置き換え" if dropped else "足し"
            lines = [f"変えません（確認の一段目です）: {title}（locations/{location_id}）",
                     f"  臨時の時間を{what}ます: {api.period_text(period)}"]
            lines += [f"  外す行: {api.period_text(p)}" for p in dropped]
            lines += [f"  ほかの日の臨時の時間 {len(merged) - 1} 件はそのまま送り直します",
                      f"  by  : {by}",
                      "  Google の画面に出るまで時間がかかることがあります。出たあと thth gbp show で確かめられます"]
            flags = "--closed" if args.closed else f"--open {args.open} --close {args.close}"
            return _stage_one(
                args, digest=digest,
                payload={"location": f"locations/{location_id}", "period": period, "replaces": dropped,
                         "special_hours_after": merged, "by": by},
                lines=lines,
                again=f"thth gbp special locations/{location_id} --date {day.isoformat()} {flags} --by {_quote(by)}")
        if args.confirm != digest:
            raise GbpRefused("gbp_digest_mismatch")
        client.patch_special(location_id, merged)
    except GbpRefused as exc:
        # 食い違いの断りに今の digest を入れない。返すと、変わった中身を見ないまま打ち直せる（監査 5.27.0 の 6）。
        return _refuse(args, exc, rc=1 if exc.code == "gbp_digest_mismatch" else 2)
    value = {"ok": True, "changed": True, "location": f"locations/{location_id}", "period": period, "by": by}

    def render(v):
        print(f"送りました: locations/{location_id} の臨時の時間 {api.period_text(period)}（{by}）")
        print("次の一手: thth gbp show で Google の今を確かめてください（反映に時間がかかることがあります）")
    return _emit(value, args, render)


# ------------------------------------------------------------------ posts・post（二段）

def _account_id(client, account_id, location_id) -> str:
    return account_id or client.account_of(location_id)


def cmd_posts(args) -> int:
    try:
        account_id, location_id = api.parse_location(args.location)
        client = _client(args)
        account_id = _account_id(client, account_id, location_id)
        rows = client.posts(account_id, location_id, limit=args.limit)
    except GbpRefused as exc:
        return _refuse(args, exc)
    value = {"location": f"accounts/{account_id}/locations/{location_id}", "posts": rows, "storage": "display_only"}

    def render(v):
        print(f"投稿（{v['location']}・{len(v['posts'])} 件・新しい順）{NOT_SAVED}:")
        if not v["posts"]:
            print("  0 件です（thth gbp post で最初の投稿を出せます）")
        for r in v["posts"]:
            summary = (r["summary"] or "").replace("\n", " ")
            summary = summary if len(summary) <= 60 else summary[:60] + "…"
            print(f"  {r['created'] or '—'}  {STATE_JA.get(r['state'], r['state'] or '—')}  {r['topic'] or '—'}  {summary}")
            if r["link"]:
                print(f"    {r['link']}")
    return _emit(value, args, render)


def _event_args(args) -> dict | None:
    """`--event 題 --start --end`（3 つそろって 1 つ・どれかだけなら断る）。"""
    given = [getattr(args, key, None) is not None for key in ("event", "start", "end")]
    if not any(given):
        return None
    if not all(given):
        raise GbpRefused("gbp_event_invalid", "event_start_end")
    return api.event_of(args.event, api.parse_minute(args.start), api.parse_minute(args.end))


def cmd_post(args) -> int:
    try:
        account_id, location_id = api.parse_location(args.location)
        event = _event_args(args)
        body = api.post_body(args.text, url=args.url, action=args.action, event=event)
    except GbpRefused as exc:
        return _refuse(args, exc)
    by = _by(args)
    if by is None:
        return _no_by(args)
    try:
        client = _client(args)
        account_id = _account_id(client, account_id, location_id)
        parent = f"accounts/{account_id}/locations/{location_id}"
        # Google の今のいちばん新しい投稿を digest に入れる。結果の分からない送信のあと同じ --confirm で
        # 打ち直したとき、届いていれば食い違いで止まり、二つ目を出さない（監査 5.27.0 の 1）。
        latest = [r.get("name") for r in client.posts(account_id, location_id, limit=1)]
        digest = api.digest_of("post", parent, body, latest)
        if not args.confirm:
            kind = (f"イベントの投稿（EVENT）: {api.event_text(event)}" if event else "ふつうの投稿（STANDARD）")
            lines = [f"出しません（確認の一段目です）: {parent}",
                     f"  種類: {kind}・日本語",
                     f"  本文（{len(body['summary'])} 字・上限 {api.POST_TEXT_MAX}）:"]
            lines += [f"    {line}" for line in body["summary"].splitlines()]
            if "callToAction" in body:
                lines.append(f"  ボタン: {body['callToAction']['actionType']} → {body['callToAction']['url']}")
            lines += [f"  by  : {by}", "  出した投稿は Google の確かめのあと公開されます（thth gbp posts で状態を見られます）"]
            again = f"thth gbp post {parent} --text {_quote(body['summary'])}"
            if event:
                again += f" --event {_quote(args.event.strip())} --start {_quote(args.start)} --end {_quote(args.end)}"
            if "callToAction" in body:
                again += f" --url {_quote(body['callToAction']['url'])}"
                if args.action:
                    again += f" --action {args.action}"
            return _stage_one(args, digest=digest, payload={"location": parent, "body": body, "by": by},
                              lines=lines, again=again + f" --by {_quote(by)}")
        if args.confirm != digest:
            raise GbpRefused("gbp_digest_mismatch")
        created = client.create_post(account_id, location_id, body)
    except GbpRefused as exc:
        # 食い違いの断りに今の digest を入れない。返すと、変わった中身を見ないまま打ち直せる（監査 5.27.0 の 6）。
        return _refuse(args, exc, rc=1 if exc.code == "gbp_digest_mismatch" else 2)
    state = created.get("state") if isinstance(created.get("state"), str) else None
    link = created.get("searchUrl") if isinstance(created.get("searchUrl"), str) else None
    value = {"ok": True, "changed": True, "location": parent, "post": created.get("name"), "state": state,
             "link": link, "by": by}

    def render(v):
        print(f"出しました: {v['location']}（{STATE_JA.get(v['state'], v['state'] or '—')}・{by}）")
        if v["link"]:
            print(f"  {v['link']}")
    return _emit(value, args, render)


# ------------------------------------------------------------------ 登録

def _bounded(low, high):
    def parse(text):
        try:
            value = int(text)
        except (TypeError, ValueError):
            raise argparse.ArgumentTypeError(f"{low}〜{high} の整数です") from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"{low}〜{high} の整数です")
        return value
    return parse


_LOC_HELP = "店（thth gbp locations に出る locations/… か accounts/…/locations/…）"


def _write_flags(parser) -> None:
    parser.add_argument("--by", default=None, help="誰が頼んだか（必須・環境変数 THTH_ACTOR でもよい）")
    parser.add_argument("--confirm", default=None, help="一段目が表示した digest。これが無いと何も変えない")
    parser.add_argument("--json", action="store_true")


def register(sub) -> None:
    """`build_parser()` に `gbp` を足す（cli.py に足すのはこの呼び出しだけ）。"""
    p = sub.add_parser("gbp", help="Google ビジネス プロフィール（認可・店・営業の時間・臨時の時間・投稿。書くのは二段確認）",
                       formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    verbs = p.add_subparsers(dest="gbp_verb", required=True)
    p_auth = verbs.add_parser("auth", help="利用者の Google アカウントで 1 度だけ認可する（端末から・ブラウザで許可）")
    p_auth.add_argument("--no-browser", dest="no_browser", action="store_true",
                        help="ブラウザを開かない（出た URL を手で貼る）")
    p_auth.add_argument("--timeout", type=int, default=auth.AUTH_TIMEOUT, help="戻りを待つ秒（既定 300）")
    p_auth.set_defaults(func=cmd_auth)
    p_doc = verbs.add_parser("doctor", help="client と認可の有無・scope・アカウントが読めるか")
    p_doc.add_argument("--json", action="store_true")
    p_doc.set_defaults(func=cmd_doctor)
    p_loc = verbs.add_parser("locations", help="見えるアカウントの店（id と名前・保存しない）")
    p_loc.add_argument("--json", action="store_true")
    p_loc.set_defaults(func=cmd_locations)
    p_show = verbs.add_parser("show", help="営業の時間・臨時の時間・営業の状態・サイトの URL（画面に出すだけ）")
    p_show.add_argument("location", metavar="店", help=_LOC_HELP)
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_show)
    p_sp = verbs.add_parser("special", help="臨時の時間を 1 日足すか、同じ日を置き換える（二段確認）")
    p_sp.add_argument("location", metavar="店", help=_LOC_HELP)
    p_sp.add_argument("--date", required=True, metavar="YYYY-MM-DD", help="その日（今日か先の日・日本の日付）")
    p_sp.add_argument("--closed", action="store_true", help="終日休み")
    p_sp.add_argument("--open", default=None, metavar="HH:MM", help="開く時刻")
    p_sp.add_argument("--close", default=None, metavar="HH:MM", help="閉める時刻（24:00 まで・開く時刻より前なら翌日）")
    _write_flags(p_sp)
    p_sp.set_defaults(func=cmd_special)
    p_ps = verbs.add_parser("posts", help="投稿の一覧（保存しない）")
    p_ps.add_argument("location", metavar="店", help=_LOC_HELP)
    p_ps.add_argument("--limit", type=_bounded(1, api.POSTS_PAGE_MAX), default=20, metavar="1-100",
                      help="出す件数（既定 20）")
    p_ps.add_argument("--json", action="store_true")
    p_ps.set_defaults(func=cmd_posts)
    p_post = verbs.add_parser("post", help="ふつうの投稿かイベントの投稿を 1 つ出す（日本語・二段確認）")
    p_post.add_argument("location", metavar="店", help=_LOC_HELP)
    p_post.add_argument("--text", required=True, metavar="本文", help=f"本文（{api.POST_TEXT_MAX} 字まで）")
    p_post.add_argument("--url", default=None, metavar="URL", help="ボタンの行き先（https の頁）")
    p_post.add_argument("--action", default=None, choices=api.ACTIONS, help="ボタンの種類（既定 LEARN_MORE・--url と一緒に）")
    p_post.add_argument("--event", default=None, metavar="題", help=f"イベントの投稿にする（題・{api.EVENT_TITLE_MAX} 字まで）")
    p_post.add_argument("--start", default=None, metavar="YYYY-MM-DD HH:MM", help="イベントの始まり（日本の時刻）")
    p_post.add_argument("--end", default=None, metavar="YYYY-MM-DD HH:MM", help="イベントの終わり（始まりより後）")
    _write_flags(p_post)
    p_post.set_defaults(func=cmd_post)
