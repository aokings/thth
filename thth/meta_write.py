"""計画 Meta 全部盛り M4: 書く最小の口（審査の録画に映す・人が承認して 1 回だけ打つ）。

    thth page cta show <FB の口座> [--json]                              行動ボタンを読む
    thth page cta delete <FB の口座> <cta_id> --by 名前 [--confirm D]     行動ボタンを消す（二段）
    thth live create <FB の口座> --title T [--description D] --by 名前 [--confirm D]
                                                                           ライブの宛先を作る（二段）
    thth live end <FB の口座> <live_id> --by 名前 [--confirm D]           ライブを終える（二段）
    thth dm list <FB|IG の口座> [--json]                                  受信箱の会話（本文なし）
    thth dm read <FB|IG の口座> <会話の id> [--profile] [--json]          直近の通を画面に出す
    thth dm send <FB|IG の口座> (<会話の id> | --comment <id>) (--text T | --text-file F)
                 --by 名前 [--human-agent] [--confirm D]                   返す（二段・24 時間の窓）
    thth dm event <FB|IG の口座> --type purchase|lead|… (--conversation <id> | --psid <id>)
                 [--value N --currency JPY] --by 名前 [--confirm D]        会話から起きた出来事を Meta に送る（二段・
                                                                           Conversions API for Business Messaging・
                                                                           `thth/meta_events.py`）

**`build_parser()` には 1 行しか足さない**（`meta_write.register(sub)`）。媒体の書く口（`delete_page_cta`・
`live_create`・`live_end`・`dm_send`・`private_reply`）を呼ぶのはこのモジュールだけ（`tests/test_m4_meta_writes.py`
が source を見て縛る）。

fail-closed（`edit_cli`・`reaction_cli` と同じ型）:
  - 二段確認: 一段目は中身と digest を見せて何もしない（rc=1）。二段目 `--confirm <digest>` は、その場で読み直した
    中身から digest を作り直し、一致したときだけ 1 回打つ（窓の判定が変われば digest が変わり通らない）。
  - 台帳に `production: true` が無ければ媒体の書く口を呼ばない（読むだけの一段目は通す）。
  - 安全装置の止めの印（`guard/stopped.json`）があれば呼ばない。口座の停止（`leave_gate`）は台帳を読む所と
    adapter の口そのものが断る。
  - 結果が分からない（時間切れ・5xx）ときは済んだと言わない（runs に `status: unknown`・自動で打ち直さない）。
  - 手元の MCP と thth.me の鍵の道には出さない（`retract` と同じ線）。

**DM の決まり**（判断 ① masaru 11/05）: 自動の返信をしない（人か利用者の LLM が本文を書き、人が二段目を打つ）。
24 時間の窓（相手の最後の通から）の中だけ `RESPONSE` で返す。窓の外は `--human-agent`（人の担当者が 7 日の内に
返す・`HUMAN_AGENT` の札）だけ。どちらも過ぎたら `messaging_window_closed` で断る。コメントへの非公開の返事
（`--comment`）はコメントから 7 日の内・1 コメントに 1 通（Meta が数える）。

**保存**: 本文・相手の名前・相手の id はどこにも残さない。窓の判定は送る直前に会話を読み直して決める（台帳を
持たない）。残すのは runs の 1 行（命令の名前・媒体・使った権限・結果）だけで、`permission_watch` の「最後に成功
した日」がそれを数える。ライブの宛先（`secure_stream_url`）は配信の鍵を含むので画面に 1 回出すだけ。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys
import unicodedata

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import approval as approval_mod
from . import core
from . import jst
from . import lock as lock_mod
from . import redact as redact_mod
from . import refusal
from . import runs as runs_mod
from .adapters import base as adapter_base

WINDOW = datetime.timedelta(hours=24)
HUMAN_AGENT_WINDOW = datetime.timedelta(days=7)
PRIVATE_REPLY_WINDOW = datetime.timedelta(days=7)
HUMAN_AGENT_TAG = "HUMAN_AGENT"
DIGEST_VERSION = "thth-m4-1"
MAX_BY = 256
LIVE_TITLE_MAX = 255
LIVE_DESCRIPTION_MAX = 2000

# runs の `action`（`permission_watch.RUN_ACTIONS` に同じ名前で載る）。
RUN_ACTIONS = ("page_cta_delete", "live_create", "live_end", "dm_list", "dm_read", "dm_send", "dm_profile", "dm_event")


class Refused(Exception):
    """静的な符丁の断り。`rc` は終了コード、`extra` は `--json` に足す欄。"""

    def __init__(self, code, message, *, rc=2, **extra):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.rc, self.extra = code, message, rc, extra


def _refuse(code, message, *, rc=2, **extra):
    raise Refused(code, message, rc=rc, **extra)


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _run(handler):
    """命令の共通の外側: 断りを目録の 3 つで出す。"""
    def call(args) -> int:
        try:
            return handler(args)
        except Refused as exc:
            return refusal.emit(args, exc.code, line=f"{exc.code}: {exc.message}",
                                payload={"ok": False, "error": exc.code, "message": exc.message, **exc.extra},
                                rc=exc.rc, dump=_print_json)
        except adapter_base.PermissionMissing as exc:
            text = str(exc).replace("<account>", str(getattr(args, "account", "")))
            return refusal.emit(args, "meta_write_permission_missing", line=f"meta_write_permission_missing: {text}",
                                payload={"ok": False, "error": "meta_write_permission_missing",
                                         "permission": exc.permission}, rc=2, dump=_print_json)
        except accounts_mod.AccountStopped:
            return refusal.emit(args, "account_stopped", line=f"account_stopped: {args.account} は止まっています。"
                                "媒体は呼んでいません", rc=1, dump=_print_json)
    return call


# ------------------------------------------------------------------ 共通の前の検査

def _by(args) -> str:
    by = (getattr(args, "by", None) or os.environ.get("THTH_ACTOR") or "").strip()
    if not by or len(by) > MAX_BY or any(ord(c) < 32 or ord(c) == 127 for c in by):
        _refuse("by_required", "--by を付けてください（誰が頼んだかを記録します）。環境変数 THTH_ACTOR でも指定できます",
                rc=1)
    return by


def _load(args, capability: str, word: str):
    """台帳・adapter のクラス・鍵。止めた口座は台帳を読む所で断る（`leave_gate`）。"""
    try:
        account_cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountStopped:
        raise
    except accounts_mod.AccountError as exc:
        _refuse("account_unavailable", str(exc))
    media = account_cfg.get("media")
    if capability not in adapters_mod.capabilities_for(media):
        _refuse("meta_write_unsupported", f"{media} の口座では{word}を扱いません（capability {capability}）")
    adapter_cls = adapters_mod.adapter_class(media)
    token = accounts_mod.load_token(account_cfg)
    if not adapter_cls.has_token(token):
        _refuse("meta_write_permission_missing", f"{args.account}: 鍵がありません（{adapter_cls.TOKEN_SETUP_HINT}）")
    return account_cfg, adapter_cls, token


def _need(args, adapter_cls, token, permissions) -> None:
    """要る権限が鍵に無いと分かっていれば、呼ぶ前に断る（一覧が無い鍵は Meta の答えで分かる）。"""
    missing = adapter_cls.missing_permissions(token, [p for p in permissions if p])
    if missing:
        _refuse("meta_write_permission_missing",
                adapter_base.not_granted_message(missing[0]).replace("<account>", args.account),
                permission=missing[0])


def _adapter(account_cfg, token):
    return adapters_mod.make_adapter(account_cfg, token)


def _read(what, call):
    """一段目の読み（人に見せる）。媒体の失敗は 1 行にして何もしない。"""
    try:
        return call()
    except (adapter_base.PermissionMissing, accounts_mod.AccountStopped):
        raise
    except adapter_base.AdapterError as exc:
        _refuse("meta_write_refused", f"{what}を読めませんでした（何もしていません）: " + redact_mod.redact(str(exc)),
                rc=1)


def digest_of(kind: str, account: str, *parts) -> str:
    """二段確認の digest（sha256 の先頭 12 桁）。中身の 1 つでも変われば変わる。"""
    joined = "\x1f".join([DIGEST_VERSION, kind, account, *[str(p) for p in parts]])
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:approval_mod.APPROVE_DIGEST_LENGTH]


def _stage_one(args, *, digest, payload, lines, again) -> int:
    """一段目: 見せて digest を出し、何もしない（rc=1）。"""
    if args.json:
        _print_json({"ok": False, "stage": 1, "digest": digest, "account": args.account, **payload})
        return 1
    for line in lines:
        print(line)
    print(f"digest: {digest}")
    print(f"打つなら: {again} --confirm {digest}")
    return 1


def _check_confirm(args, digest) -> None:
    if args.confirm != digest:
        _refuse("meta_write_digest_mismatch",
                f"digest が一致しないので打ちません（表示したあとに中身か窓の判定が変わりました）。いまの digest は "
                f"{digest} です。一段目からやり直してください", rc=1, digest=digest)


def _ready(args, account_cfg) -> None:
    if not approval_mod.is_true(account_cfg.get("production")):
        _refuse("production_disabled", f"{args.account}: 台帳に production: true が無いので媒体の書く口を呼びません",
                rc=1)


def _guard_stopped(account) -> None:
    from . import guard as guard_mod
    if not guard_mod.stop_place_usable(account):
        return
    halted = guard_mod.stopped(account)
    if halted is not None:
        _refuse("account_stopped", f"{account} は安全装置で止まっています（{halted.get('reason') or '理由不明'}）。"
                "止めたまま媒体は呼びません", rc=1, guard="stopped")


def _record(account, action, media, status, *, permission=None, error=None, mode=None, extra=None) -> None:
    """runs に 1 行（命令の名前・媒体・権限・結果だけ）。本文・相手・id は入れない。

    `extra` は静的な欄だけ（`thth dm event` の `event_type`・件数 `n`）。
    """
    try:
        runs_mod.record_minimal(account, {"action": action, "account": account, "medium": media,
                                          "mode": mode or ("read" if action in ("dm_list", "dm_read", "dm_profile")
                                                         else "production"),
                                          "status": status, "error": error, "permission": permission,
                                          **(extra or {})})
    except (OSError, accounts_mod.AccountError) as exc:
        record_failed(action, status, exc)


def record_failed(action, status, exc) -> None:
    """実行記録（runs）に書けなかったことを画面に知らせる（監査 5.14.0b の 5）。

    媒体を呼んだあとなので取り消せないが、黙っていると「記録に無い＝していない」と読まれる。
    """
    reason = exc.__class__.__name__
    sys.stderr.write(f"注意: 実行記録（runs）に書けませんでした（{action}・{status}・{reason}）。"
                     "この実行は記録に残っていません。state の置き場の空きと権限を確かめてください。\n")


def _write(args, account_cfg, action, permission, call, *, extra=None):
    """二段目の 1 回だけの書き込み。ロック・止めの印・結果不明の扱いをそろえる。"""
    state_dir = accounts_mod.state_dir_for(args.account)
    media = account_cfg.get("media")
    try:
        with core._account_locks(args.account, account_cfg, state_dir, wait=getattr(args, "wait", 0)):
            _guard_stopped(args.account)
            try:
                result = call()
            except (adapter_base.PermissionMissing, accounts_mod.AccountStopped):
                raise
            except adapter_base.AdapterError as exc:
                message = redact_mod.redact(str(exc))
                if message.startswith("messaging_window_closed"):
                    _record(args.account, action, media, "error", permission=permission,
                            error="messaging_window_closed")
                    _refuse("messaging_window_closed", message, rc=1)
                if getattr(exc, "failure", None) == "ambiguous":
                    _record(args.account, action, media, "unknown", permission=permission, error="meta_write_unknown")
                    _refuse("meta_write_unknown", f"済んだかどうか分かりません（打ち直していません）。媒体の画面で"
                                                  f"確かめてください: {message}", rc=1, result="unknown")
                _record(args.account, action, media, "error", permission=permission, error="meta_write_refused")
                _refuse("meta_write_refused", "Meta が断りました（何も変わっていません）: " + message, rc=1)
    except lock_mod.LockBusy:
        _refuse("meta_write_busy", f"{args.account} は既に実行中です（ロック取得失敗）。--wait <秒> で空くのを待てます",
                rc=1)
    _record(args.account, action, media, "ok", permission=permission, extra=extra)
    return result


# ------------------------------------------------------------------ thth page cta

def cmd_page_cta(args) -> int:
    account_cfg, adapter_cls, token = _load(args, "page_cta", "ページの行動ボタン")
    adapter = _adapter(account_cfg, token)
    if args.action == "show":
        rows = _read("行動ボタン", adapter.page_cta)
        if args.json:
            _print_json({"ok": True, "account": args.account, "call_to_actions": rows})
            return 0
        print(f"{args.account}  行動ボタン {len(rows)} 件")
        for row in rows:
            print(f"  id={row.get('id')}  {row.get('type') or '—'}  {row.get('web_url') or ''}  "
                  f"{row.get('status') or ''}")
        print("  Meta の公開の口は行動ボタンを作る・直すを持たず、消すだけです（thth page cta delete）")
        return 0
    by = _by(args)
    cta_id = str(args.cta_id or "").strip()
    if not cta_id:
        _refuse("page_cta_not_found", "消す行動ボタンの id を渡してください（thth page cta show に出る id）")
    from .adapters import facebook as fb
    _need(args, adapter_cls, token, [fb.CTA_PERMISSION])
    rows = _read("行動ボタン", adapter.page_cta)
    row = next((r for r in rows if r.get("id") == cta_id), None)
    if row is None:
        _refuse("page_cta_not_found", f"{args.account} のページの行動ボタンに id {cta_id} がありません（何もしていません）",
                rc=1)
    digest = digest_of("page_cta_delete", args.account, cta_id, row.get("type"), row.get("web_url"))
    if not args.confirm:
        return _stage_one(args, digest=digest, payload={"cta": row, "by": by}, lines=[
            f"消しません（確認の一段目です）: {args.account} 行動ボタン {cta_id}",
            f"  種類: {row.get('type') or '—'}  行き先: {row.get('web_url') or '—'}",
            f"  by  : {by}",
            "  Meta の公開の口では作り直せません。消したあとはページの画面で付け直します",
            *([] if approval_mod.is_true(account_cfg.get("production"))
              else ["  ⚠ 台帳に production: true が無いので、二段目でも媒体は呼びません"])],
            again=f"thth page cta delete {args.account} {cta_id} --by {json.dumps(by, ensure_ascii=False)}")
    _check_confirm(args, digest)
    _ready(args, account_cfg)
    _write(args, account_cfg, "page_cta_delete", fb.CTA_PERMISSION, lambda: adapter.delete_page_cta(cta_id))
    if args.json:
        _print_json({"ok": True, "changed": True, "account": args.account, "cta_id": cta_id, "by": by})
    else:
        print(f"消しました: {args.account} 行動ボタン {cta_id}（{by}）")
    return 0


# ------------------------------------------------------------------ thth live

def _clean_line(value, what, limit, *, required) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    if required and not text:
        _refuse("live_title_invalid", f"{what}が空です")
    if len(text) > limit or any(unicodedata.category(c) == "Cc" and c not in "\n" for c in text):
        _refuse("live_title_invalid", f"{what}は制御文字なしの {limit} 字までです（{len(text)} 字）")
    return text


def cmd_live(args) -> int:
    from .adapters import facebook as fb
    account_cfg, adapter_cls, token = _load(args, "live_video", "ライブ配信")
    by = _by(args)
    _need(args, adapter_cls, token, [fb.LIVE_PERMISSION])
    adapter = _adapter(account_cfg, token)
    production_note = ([] if approval_mod.is_true(account_cfg.get("production"))
                       else ["  ⚠ 台帳に production: true が無いので、二段目でも媒体は呼びません"])
    if args.action == "create":
        title = _clean_line(args.title, "題", LIVE_TITLE_MAX, required=True)
        description = _clean_line(args.description, "説明", LIVE_DESCRIPTION_MAX, required=False)
        digest = digest_of("live_create", args.account, title, description)
        if not args.confirm:
            return _stage_one(args, digest=digest, payload={"title": title, "description": description, "by": by},
                              lines=[f"作りません（確認の一段目です）: {args.account} ライブ配信",
                                     f"  題  : {title}", f"  説明: {description or '—'}", f"  by  : {by}",
                                     "  作るとすぐ「配信中」になり、宛先に映像を送るとページに出ます（止め絵・ループは"
                                     "使わないでください）。終えるのは thth live end",
                                     *production_note],
                              again=(f"thth live create {args.account} --title {json.dumps(title, ensure_ascii=False)}"
                                     + (f" --description {json.dumps(description, ensure_ascii=False)}"
                                        if description else "")
                                     + f" --by {json.dumps(by, ensure_ascii=False)}"))
        _check_confirm(args, digest)
        _ready(args, account_cfg)
        made = _write(args, account_cfg, "live_create", fb.LIVE_PERMISSION,
                      lambda: adapter.live_create(title, description))
        target = made.get("secure_stream_url") or made.get("stream_url")
        if args.json:
            _print_json({"ok": True, "account": args.account, "live_id": made["id"], "stream_url": target,
                         "storage": "display_only"})
        else:
            print(f"作りました: {args.account} ライブ {made['id']}（{by}）")
            print("  配信の宛先（配信の鍵を含みます・どこにも残していません・人に見せないでください）:")
            print(f"  {target or '（宛先が返りませんでした・ページの画面で確かめてください）'}")
            print(f"  終えるとき: thth live end {args.account} {made['id']} --by {json.dumps(by, ensure_ascii=False)}")
        return 0
    live_id = str(args.live_id or "").strip()
    if not live_id:
        _refuse("invalid_request", "終えるライブの id を渡してください（thth live create が返した id）", rc=2)
    status = _read("ライブ", lambda: adapter.live_status(live_id))
    # 持ち主が返らないときも止める（分からないまま終えない）。
    if status.get("owner") != str(account_cfg.get("page_id") or "") or not status.get("owner"):
        _refuse("live_not_this_page", f"ライブ {live_id} はこの口座のページのものではありません（何もしていません）",
                rc=1)
    digest = digest_of("live_end", args.account, live_id, status.get("status"))
    if not args.confirm:
        return _stage_one(args, digest=digest, payload={"live": status, "by": by},
                          lines=[f"終えません（確認の一段目です）: {args.account} ライブ {live_id}",
                                 f"  題: {status.get('title') or '—'}  状態: {status.get('status') or '—'}",
                                 f"  by: {by}", "  終えると録画が動画としてページに残ります", *production_note],
                          again=f"thth live end {args.account} {live_id} --by {json.dumps(by, ensure_ascii=False)}")
    _check_confirm(args, digest)
    _ready(args, account_cfg)
    _write(args, account_cfg, "live_end", fb.LIVE_PERMISSION, lambda: adapter.live_end(live_id))
    if args.json:
        _print_json({"ok": True, "account": args.account, "live_id": live_id, "ended": True, "by": by})
    else:
        print(f"終えました: {args.account} ライブ {live_id}（{by}）")
    return 0


# ------------------------------------------------------------------ thth dm

def _at(value):
    return jst.parse(value) if isinstance(value, str) else None


def window(messages: list, now: datetime.datetime, *, human_agent: bool) -> dict:
    """24 時間の窓の判定。`{"tag", "last_inbound", "closes_at"}`。**どちらの窓も過ぎていれば断る。**

    `tag` は None（`RESPONSE`・窓の中）か `HUMAN_AGENT`（窓の外・人の担当者が 7 日の内）。相手の通が 1 つも
    読めなければ窓は開いていない（相手から始まった会話にだけ返す）。
    """
    times = [t for t in (_at(m.get("created_time")) for m in messages if m.get("inbound")) if t is not None]
    if not times:
        _refuse("messaging_window_closed", "相手からの通が見当たりません。相手から届いた会話にだけ返します", rc=1)
    last = max(times)
    if now - last <= WINDOW:
        return {"tag": None, "last_inbound": jst.iso(last), "closes_at": jst.iso(last + WINDOW)}
    if human_agent and now - last <= HUMAN_AGENT_WINDOW:
        return {"tag": HUMAN_AGENT_TAG, "last_inbound": jst.iso(last), "closes_at": jst.iso(last + HUMAN_AGENT_WINDOW)}
    hint = ("" if human_agent or now - last > HUMAN_AGENT_WINDOW
            else "。人の担当者が返すなら --human-agent で 7 日の内まで返せます（宣伝は不可）")
    _refuse("messaging_window_closed", f"相手の最後の通（{jst.iso(last)}）から 24 時間を過ぎています{hint}", rc=1,
            last_inbound=jst.iso(last))


def check_text(text, limit) -> str:
    """送る本文の検査（空・制御文字・媒体の上限）。切り詰めない。"""
    value = unicodedata.normalize("NFC", str(text or ""))
    if not value.strip():
        _refuse("dm_text_invalid", "本文が空です", rc=1)
    if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in value):
        _refuse("dm_text_invalid", "本文に制御文字があります", rc=1)
    unit, size = limit
    n = len(value.encode("utf-8")) if unit == "bytes" else len(value)
    # IG は「1,000 バイト未満」、Messenger は 2,000 字まで。
    too_long = (n >= size) if unit == "bytes" else (n > size)
    if too_long:
        word = "バイト" if unit == "bytes" else "字"
        _refuse("dm_text_invalid", f"長すぎます（{n} {word}・上限 {size} {word}）。切り詰めません", rc=1)
    return value


def _read_text(args) -> str:
    if args.text is not None and args.text_file is not None:
        _refuse("dm_text_invalid", "--text と --text-file はどちらか 1 つです", rc=1)
    if args.text is not None:
        return args.text
    if args.text_file is None:
        _refuse("dm_text_invalid", "本文を --text か --text-file で渡してください（- なら標準入力）", rc=1)
    try:
        if args.text_file == "-":
            return sys.stdin.read()
        with open(args.text_file, encoding="utf-8") as stream:
            return stream.read()
    except OSError:
        _refuse("dm_text_invalid", f"本文のファイルを読めません: {args.text_file}", rc=1)


def _name(person) -> str:
    return person.get("name") or "（名前は返りません）"


def cmd_dm(args) -> int:
    if args.action == "event":      # 会話から起きた出来事を Meta に送る（`thth/meta_events.py`）
        from . import meta_events
        return meta_events.cmd_event(args)
    account_cfg, adapter_cls, token = _load(args, "direct_messages", "ダイレクトメッセージ")
    permission = adapter_cls.MESSAGING_PERMISSION
    media = account_cfg.get("media")
    if args.action == "list":
        _need(args, adapter_cls, token, [permission])
        adapter = _adapter(account_cfg, token)
        rows = _read("受信箱", adapter.dm_conversations)
        _record(args.account, "dm_list", media, "ok", permission=permission)
        if args.json:
            _print_json({"ok": True, "account": args.account, "conversations": rows, "storage": "display_only"})
            return 0
        print(f"{args.account}  受信箱 {len(rows)} 件（本文は出しません・保存しません）")
        for row in rows:
            people = "、".join(_name(p) for p in row["with"]) or "—"
            print(f"  {row.get('updated_time') or '—'}  {people}  会話 {row['id']}")
        print(f"  読むとき: thth dm read {args.account} <会話の id>")
        return 0
    if args.action == "read":
        if getattr(args, "profile", False) and media != "facebook":
            _refuse("meta_write_unsupported", "相手の属性（--profile）は Facebook ページの 1 対 1 の会話だけです")
        _need(args, adapter_cls, token, [permission])
        adapter = _adapter(account_cfg, token)
        convo = _read("会話", lambda: adapter.dm_messages(args.target))
        _record(args.account, "dm_read", media, "ok", permission=permission)
        profile = None
        if getattr(args, "profile", False):
            if media != "facebook" or len(convo["with"]) != 1:
                _refuse("meta_write_unsupported", "相手の属性（--profile）は Facebook ページの 1 対 1 の会話だけです")
            profile = _read("相手の属性", lambda: adapter.dm_profile(convo["with"][0]["id"]))
            _record(args.account, "dm_profile", media, "ok", permission="pages_user_locale")
        now = jst.now_jst()
        try:
            state = window(convo["messages"], now, human_agent=False)
        except Refused:
            state = None
        if args.json:
            _print_json({"ok": True, "account": args.account, "conversation": convo, "window": state,
                         "profile": profile, "storage": "display_only"})
            return 0
        print(f"{args.account}  会話 {convo['id']}  相手: {'、'.join(_name(p) for p in convo['with']) or '—'}"
              "（本文は画面に出すだけ・保存しません）")
        if profile is not None:
            print(f"  相手の言語 {profile.get('locale') or '—'}・時間帯 {profile.get('timezone') if profile.get('timezone') is not None else '—'}"
                  f"・性別 {profile.get('gender') or '—'}（権限が無い欄は —）")
        for row in sorted(convo["messages"], key=lambda r: str(r.get("created_time") or "")):
            who = _name(row["from"]) if row["inbound"] else "こちら"
            print(f"  {row.get('created_time') or '—'}  {who}: {row.get('text') or ''}")
        if state:
            print(f"  返せる窓: {state['closes_at']} まで（相手の最後の通 {state['last_inbound']}）")
        else:
            print("  返せる窓: 閉じています（24 時間を過ぎました。人の担当者なら --human-agent で 7 日の内）")
        print(f"  返すとき: thth dm send {args.account} {convo['id']} --text-file <file> --by <名前>")
        return 0
    return _dm_send(args, account_cfg, adapter_cls, token, permission, media)


def _dm_send(args, account_cfg, adapter_cls, token, permission, media) -> int:
    by = _by(args)
    comment = (getattr(args, "comment", None) or "").strip()
    target = (args.target or "").strip()
    if bool(comment) == bool(target):
        _refuse("dm_recipient_unknown", "返す先を 1 つ渡してください（会話の id か --comment <コメントの id>）", rc=1)
    if comment and args.human_agent:
        _refuse("dm_recipient_unknown", "--human-agent は会話への返事だけです（コメントへの非公開の返事は 7 日の内）",
                rc=1)
    text = check_text(_read_text(args), adapter_cls.DM_TEXT_LIMIT)
    needed = [permission] + ([adapter_cls.COMMENT_READ_PERMISSION] if comment else [])
    _need(args, adapter_cls, token, needed)
    adapter = _adapter(account_cfg, token)
    now = jst.now_jst()
    if comment:
        created = _read("コメント", lambda: adapter.comment_created(comment))
        if now - created > PRIVATE_REPLY_WINDOW:
            _refuse("private_reply_window_closed",
                    f"コメント（{jst.iso(created)}）から 7 日を過ぎているので非公開の返事は送れません", rc=1)
        kind, recipient, tag = "private_reply", comment, None
        closes = jst.iso(created + PRIVATE_REPLY_WINDOW)
        where = f"コメント {comment} への非公開の返事（1 コメントに 1 通）"
    else:
        convo = _read("会話", lambda: adapter.dm_messages(target))
        if len(convo["with"]) != 1:
            _refuse("dm_recipient_unknown", f"会話 {target} の相手が 1 人に決まりません（{len(convo['with'])} 人）", rc=1)
        state = window(convo["messages"], now, human_agent=bool(args.human_agent))
        kind, recipient, tag = "message", convo["with"][0]["id"], state["tag"]
        closes = state["closes_at"]
        where = f"会話 {target} の {_name(convo['with'][0])}" + ("（人の担当者・HUMAN_AGENT）" if tag else "")
    digest = digest_of("dm_send", args.account, kind, target or comment, recipient, tag or "RESPONSE", text)
    if not args.confirm:
        return _stage_one(args, digest=digest, payload={"to": where, "closes_at": closes, "text": text, "by": by,
                                                        "storage": "display_only"}, lines=[
            f"送りません（確認の一段目です）: {args.account} → {where}",
            f"  窓: {closes} まで", f"  by: {by}",
            "--- 送る本文（保存しません） ---", text, "--- ここまで ---",
            *([] if approval_mod.is_true(account_cfg.get("production"))
              else ["  ⚠ 台帳に production: true が無いので、二段目でも媒体は呼びません"])],
            again=(f"thth dm send {args.account} " + (f"--comment {comment}" if comment else target)
                   + " --text-file <同じ本文> " + ("--human-agent " if tag else "")
                   + f"--by {json.dumps(by, ensure_ascii=False)}"))
    _check_confirm(args, digest)
    _ready(args, account_cfg)
    if comment:
        sent = _write(args, account_cfg, "dm_send", permission, lambda: adapter.private_reply(comment, text))
    else:
        sent = _write(args, account_cfg, "dm_send", permission, lambda: adapter.dm_send(recipient, text, tag=tag))
    if args.json:
        _print_json({"ok": True, "account": args.account, "message_id": sent.get("message_id"), "by": by,
                     "storage": "display_only"})
    else:
        print(f"送りました: {args.account} → {where}（{by}・本文は保存していません）")
    return 0


# ------------------------------------------------------------------ 登録

def register(sub) -> None:
    """`thth/cli.py` の `build_parser()` から 1 行で呼ばれる。"""
    page = sub.add_parser("page", help="Facebook ページの設定（行動ボタンを読む・消す・二段確認）")
    page_sub = page.add_subparsers(dest="page_command", required=True)
    cta = page_sub.add_parser("cta", help="ページの行動ボタン（show は読むだけ・delete は二段確認）")
    cta.add_argument("action", choices=("show", "delete"))
    cta.add_argument("account")
    cta.add_argument("cta_id", nargs="?", default=None, help="delete で消す行動ボタンの id（show に出る）")
    _write_flags(cta)
    cta.set_defaults(func=_run(cmd_page_cta))

    live = sub.add_parser("live", help="Facebook ページのライブ配信の宛先を作る・終える（二段確認）")
    live.add_argument("action", choices=("create", "end"))
    live.add_argument("account")
    live.add_argument("live_id", nargs="?", default=None, help="end で終えるライブの id（create が返す）")
    live.add_argument("--title", default=None, help="create のライブの題（必須）")
    live.add_argument("--description", default=None, help="create のライブの説明（任意）")
    _write_flags(live)
    live.set_defaults(func=_run(cmd_live))

    dm = sub.add_parser("dm", help="ページと Instagram の受信箱を読む・24 時間の窓の中で返す（send は二段確認・"
                                   "自動返信なし・本文は保存しない）")
    dm.add_argument("action", choices=("list", "read", "send", "event"))
    dm.add_argument("account")
    dm.add_argument("target", nargs="?", default=None, help="read・send の会話の id（list に出る）")
    dm.add_argument("--comment", default=None, help="send でコメントへ非公開の返事を送る（コメントの id・7 日の内）")
    dm.add_argument("--text", default=None, help="send の本文")
    dm.add_argument("--text-file", dest="text_file", default=None, help="send の本文のファイル（- なら標準入力）")
    dm.add_argument("--human-agent", dest="human_agent", action="store_true",
                    help="24 時間を過ぎた会話に人の担当者が 7 日の内に返す（HUMAN_AGENT の札・宣伝は不可）")
    dm.add_argument("--profile", action="store_true", help="read で相手の言語・時間帯・性別も出す（Facebook・保存しない）")
    # event: 会話から起きた購入・問い合わせを Meta に送る（Conversions API for Business Messaging・二段確認）。
    dm.add_argument("--type", default=None, help="event の出来事（purchase・lead・qualified_lead・add_to_cart など）")
    dm.add_argument("--conversation", default=None, help="event の相手を会話の id で（会話の相手が 1 人のとき）")
    dm.add_argument("--psid", default=None, help="event の相手の id（thth dm list・read に出る）")
    dm.add_argument("--value", default=None, help="event の金額（--currency と一緒に。purchase は必須）")
    dm.add_argument("--currency", default=None, help="event の通貨コード（JPY など）")
    dm.add_argument("--channel", default=None, help="event のチャネル（messenger・instagram・whatsapp。既定は口座の媒体から）")
    dm.add_argument("--nth", type=_event_nth, default=1,
                    help="event で、同じ相手・同じ金額の出来事が今日 2 回目以降のとき（2・3…）。同じ日の二重送信を防ぐ柵を通す")
    dm.add_argument("--refresh-dataset", dest="refresh_dataset", action="store_true",
                    help="event で記録した dataset の id を使わず、Meta に訊き直す（1 ページ 1 つなので同じ id が返る）")
    _write_flags(dm)
    dm.set_defaults(func=_run(cmd_dm))


def _event_nth(value):
    from .meta_events import _nth
    return _nth(value)


def _write_flags(parser) -> None:
    parser.add_argument("--by", default=None, help="誰が頼んだか（書く命令は必須・記録には残さない）")
    parser.add_argument("--confirm", default=None, help="一段目が表示した digest。これが無いと何もしない")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--wait", type=lock_mod.wait_seconds, default=0, help="ロックを待つ秒数（既定 0）")
