"""`thth wa …`（WhatsApp の最小の口・審査の録画に映す・`whatsapp_business_management`／`whatsapp_business_messaging`）。

    thth wa add <名前> --waba <WABA の id> --project <p> --by <名前>        台帳を 1 本書く（production は false）
    thth wa auth <名前> --by <名前> [--code <戻り URL>]                     ユーザーの長い鍵を .token に（端末から）
    thth wa numbers <名前> [--json]                                         番号の id・表示名・品質（読むだけ）
    thth wa templates <名前> [--json]                                       テンプレートの名前・状態・言語（読むだけ）
    thth wa template create <名前> --name N --language ja --category UTILITY --body T --by 名前 [--confirm D]
                                                                            テンプレートを作る（二段・審査に出る）
    thth wa send <名前> --from <phone-number-id> --to <番号> --template N --language ja --by 名前 [--confirm D]
                                                                            承認済みのテンプレートを 1 通送る（二段）
    thth wa stop <名前> --by <名前>                                         止めの印を置く（送る・作るが断られる）

**送る口は外の人に届く書く口**。`thth dm send` と同じ強さの柵を付ける:
  - 二段確認: 一段目は中身と digest を見せて何もしない（rc=1）。二段目 `--confirm <digest>` は、その場で読み直した
    番号・テンプレートの状態から digest を作り直し、一致したときだけ 1 回打つ。
  - 台帳に `production: true` が無ければ媒体の書く口を呼ばない（読むだけの一段目は通す）。
  - 止めの印（`waba_state/<名前>/guard/stopped.json`）があれば呼ばない。
  - 1 日の通数の上限（台帳の `send_daily_max`・既定 10）。
  - 結果が分からない（時間切れ・5xx）ときは済んだと言わない（runs に `status: unknown`・自動で打ち直さない）。
  - **自由文は送らない**: 送れるのは承認済み（APPROVED）のテンプレートだけ。変数も渡さない。
  - 手元の MCP と thth.me の鍵の道には出さない。

**保存**: 相手の番号・本文（テンプレートの本文を含む）は、どこにも残さない。残すのは runs の 1 行（命令の名前・媒体・使った
権限・結果）だけ。`thth wa send` の画面にも、送ったあとは番号を出さない。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .. import accounts, admin_log, approval as approval_mod, jst, redact, runs as runs_mod
from .. import lock as lock_mod
from .. import meta_write as mw
from ..adapters import base as adapter_base
from ..meta import errors as meta_errors
from . import auth as wa_auth
from . import graph, ledger

MGMT_PERMISSION = "whatsapp_business_management"
MSG_PERMISSION = "whatsapp_business_messaging"
RUN_ACTIONS = ("wa_numbers", "wa_templates", "wa_template_create", "wa_send")
BODY_MAX = graph.BODY_MAX


def _bad(message, **extra):
    mw._refuse("wa_args_invalid", message, rc=2, **extra)


# ------------------------------------------------------------------ 共通

def _load(args):
    """台帳・鍵・網の口。止めの印は読む口では見ない（読む・止めるは止まっていても通す）。"""
    try:
        cfg = ledger.load(args.account)
    except accounts.AccountError as exc:
        mw._refuse("account_unavailable", str(exc))
    token = ledger.load_token(cfg)
    if not isinstance(token, dict) or token.get("unreadable") or not isinstance(token.get("access_token"), str):
        mw._refuse("meta_write_permission_missing",
                   f"{args.account}: 鍵がありません（thth wa auth {args.account} --by <名前>）")
    return cfg, token


def _need(args, token, permissions) -> None:
    scopes = token.get("scopes")
    if not isinstance(scopes, list):
        return          # 一覧が無い鍵は Meta の答えで分かる
    for permission in permissions:
        if permission not in scopes:
            mw._refuse("meta_write_permission_missing",
                       f"`{permission}` がトークンに乗っていません。`thth wa auth {args.account} --by <名前>` を"
                       "やり直し、許可の画面で WhatsApp アカウントに印を付けてください", permission=permission)


def _graph(args, cfg, token) -> graph.Graph:
    try:
        return graph.Graph(args.account, cfg, token)
    except graph.WaError as exc:
        mw._refuse("meta_write_permission_missing", f"{args.account}: {exc.code}")


def _record(name, action, status, *, permission, error=None, mode=None) -> None:
    """runs に 1 行（命令の名前・媒体・権限・結果だけ）。番号・本文・id は入れない。"""
    now = jst.now_jst()
    try:
        runs_mod.append_run(ledger.ensure_state_dir(name), {
            "account": name, "run_id": f"{action}-{jst.iso(now)}", "mode": mode or "read", "action": action,
            "medium": ledger.MEDIA, "status": status, "error": error, "permission": permission}, jst.month_str(now))
    except (OSError, ValueError, accounts.AccountError):
        pass


def _guard_stopped(name) -> None:
    halted = ledger.stopped(name)
    if halted is not None:
        mw._refuse("account_stopped", f"{name} は止まっています（{halted.get('reason') or '理由不明'}）。"
                   "止めたまま媒体は呼びません", rc=1, guard="stopped")


def _sent_today(name, now) -> int:
    """今日（JST）の送信の通数。runs の wa_send の行（ok と unknown）を数える。"""
    try:
        with open(runs_mod.path_for(ledger.state_dir_for(name), jst.month_str(now)), encoding="utf-8") as stream:
            lines = stream.readlines()
    except OSError:
        return 0
    count = 0
    for line in lines:
        if '"action": "wa_send"' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        run_id = row.get("run_id") if isinstance(row, dict) else None
        at = jst.parse(run_id.split("-", 1)[1]) if isinstance(run_id, str) and "-" in run_id else None
        if at is not None and at.date() == now.date() and row.get("status") in ("ok", "unknown"):
            count += 1
    return count


def _limit_check(args, cfg, now) -> int:
    used, cap = _sent_today(args.account, now), ledger.send_daily_max(cfg)
    if used >= cap:
        mw._refuse("wa_daily_limit", f"{args.account}: 今日（JST）はもう {used} 通送りました（台帳の send_daily_max は {cap}）",
                   rc=1, used=used, limit=cap)
    return cap - used


def _write(args, cfg, action, permission, call, *, before=None):
    """二段目の 1 回だけの書き込み。ロック・止めの印・上限・結果不明の扱いをそろえる。"""
    lock = lock_mod.AccountLock(ledger.lock_path_for(args.account))
    ledger.ensure_state_dir(args.account)
    try:
        lock_mod.acquire(lock, getattr(args, "wait", 0))
    except lock_mod.LockBusy:
        mw._refuse("meta_write_busy", f"{args.account} は既に実行中です（ロック取得失敗）。--wait <秒> で空くのを待てます",
                   rc=1)
    try:
        _guard_stopped(args.account)
        if before is not None:
            before()
        try:
            result = call()
        except adapter_base.PermissionMissing:
            raise
        except adapter_base.AdapterError as exc:
            message = redact.redact(str(exc))
            if getattr(exc, "user_msg", None):
                message += f"（Meta: {exc.user_msg}）"
            if getattr(exc, "failure", None) == meta_errors.AMBIGUOUS:
                _record(args.account, action, "unknown", permission=permission, error="meta_write_unknown",
                        mode="production")
                mw._refuse("meta_write_unknown", "済んだかどうか分かりません（打ち直していません）。WhatsApp Manager で"
                           f"確かめてください: {message}", rc=1, result="unknown")
            _record(args.account, action, "error", permission=permission, error="meta_write_refused",
                    mode="production")
            mw._refuse("meta_write_refused", "Meta が断りました（何も変わっていません）: " + message, rc=1)
    finally:
        lock.release()
    _record(args.account, action, "ok", permission=permission, mode="production")
    return result


def _production_note(cfg) -> list:
    return ([] if approval_mod.is_true(cfg.get("production"))
            else ["  ⚠ 台帳に production: true が無いので、二段目でも媒体は呼びません"])


# ------------------------------------------------------------------ add / auth / stop

def cmd_add(args) -> int:
    try:
        by = admin_log.actor(getattr(args, "by", None))
    except ValueError as exc:
        print(redact.redact(str(exc)), file=sys.stderr)
        return 2
    name = args.account
    if not accounts.name_is_safe(name):
        print(f"invalid_waba_name: 名前に使えない字が入っています: {name!r}", file=sys.stderr)
        return 2
    taken = ledger.name_taken(name)
    if taken:
        print(f"waba_name_taken: {name} は{taken}の名前です。WhatsApp の口座には別の名前を（例 kopicha-wa）", file=sys.stderr)
        return 2
    try:
        data = ledger.build(name, waba_id=args.waba, project=args.project, business_id=args.business_id)
        ledger.validate(name, data)
    except ledger.WabaError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    path = ledger.path_for(name)
    if os.path.exists(path):
        print(f"既にあります。上書きしません: {path}", file=sys.stderr)
        return 2
    data["provenance"] = admin_log.provenance(by)
    try:
        ledger.write_new(name, data)
    except OSError as exc:
        print(f"台帳を書けませんでした: {path}（{exc.strerror or exc}）", file=sys.stderr)
        return 2
    try:
        admin_log.append("waba_added", name, {"media": ledger.MEDIA}, by=by,
                         diff=admin_log.difference({}, {k: v for k, v in data.items() if k != "provenance"}))
    except (OSError, ValueError, admin_log.AdminLogError):
        try:
            os.unlink(path)
        except OSError:
            pass
        print("admin_change_refused: --by and a private writable event log are required", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"path": path, "waba": data}, ensure_ascii=False))
        return 0
    print(f"書きました: {path}（production は false。送る・作るを通すのは VM の台帳を直接書くとき）")
    print(f"次の一手: thth wa auth {name} --by <名前> → thth wa numbers {name}")
    return 0


def cmd_auth(args) -> int:
    return wa_auth.run_auth(args.account, by=args.by, code=args.code,
                            log=lambda line: print(line, file=sys.stderr), human_output=print)


def cmd_stop(args) -> int:
    try:
        by = admin_log.actor(getattr(args, "by", None))
        ledger.load(args.account)
        value = ledger.stop(args.account, "owner", by=by)
    except (ValueError, accounts.AccountError, OSError) as exc:
        print(redact.redact(str(exc)), file=sys.stderr)
        return 2
    print(f"止めました: {args.account}（{value['stopped_at']}）。送る・テンプレートを作るは断られます。"
          "外すのは VM で guard/stopped.json を控えてから消します")
    return 0


# ------------------------------------------------------------------ 読む

def cmd_numbers(args) -> int:
    cfg, token = _load(args)
    _need(args, token, [MGMT_PERMISSION])
    rows = mw._read("番号の一覧", _graph(args, cfg, token).phone_numbers)
    _record(args.account, "wa_numbers", "ok", permission=MGMT_PERMISSION)
    if args.json:
        mw._print_json({"ok": True, "account": args.account, "numbers": rows, "count": len(rows),
                        "storage": "display_only"})
        return 0
    print(f"{args.account}  番号 {len(rows)} 件（保存しません）")
    for row in rows:
        print(f"  phone_number_id={row['phone_number_id']}  {row.get('verified_name') or '—'}  "
              f"品質={row.get('quality_rating') or '—'}")
    if not rows:
        print("  0 件です（読みは通っています。WABA に番号がまだ無いだけかもしれません）")
    else:
        print(f"  送るとき: thth wa send {args.account} --from <phone_number_id> --to <番号> --template <名前> "
              "--language <言語> --by <名前>")
    return 0


def cmd_templates(args) -> int:
    cfg, token = _load(args)
    _need(args, token, [MGMT_PERMISSION])
    rows = mw._read("テンプレートの一覧", _graph(args, cfg, token).message_templates)
    _record(args.account, "wa_templates", "ok", permission=MGMT_PERMISSION)
    if args.json:
        mw._print_json({"ok": True, "account": args.account, "templates": rows, "count": len(rows),
                        "storage": "display_only"})
        return 0
    print(f"{args.account}  テンプレート {len(rows)} 件（名前・状態・言語だけ・保存しません）")
    for row in rows:
        print(f"  {row['name']}  状態={row.get('status') or '—'}  言語={row.get('language') or '—'}  "
              f"分類={row.get('category') or '—'}")
    if not rows:
        print("  0 件です（読みは通っています。作るのは thth wa template create）")
    return 0


# ------------------------------------------------------------------ 書く: テンプレート

def _clean(value, what, pattern, hint):
    text = str(value or "").strip()
    if not pattern.fullmatch(text):
        _bad(f"{what}は{hint}です")
    return text


def cmd_template_create(args) -> int:
    cfg, token = _load(args)
    by = mw._by(args)
    name = _clean(args.name, "--name（テンプレートの名前）", graph.NAME_RE, "小文字の英数字と _ の 1〜512 字")
    language = _clean(args.language, "--language", graph.LANGUAGE_RE, "ja・en・en_US のような言語の符号")
    category = str(args.category or "").strip().upper()
    if category not in graph.CATEGORIES:
        _bad(f"--category は {'・'.join(graph.CATEGORIES)} のどれかです（Meta の分類。審査で変わることがあります）")
    body = str(args.body or "").strip()
    if not body or len(body) > BODY_MAX or any(ord(c) < 32 and c not in "\n" or ord(c) == 127 for c in body):
        _bad(f"--body は制御文字なしの 1〜{BODY_MAX} 字です")
    if "{{" in body:
        _bad("変数（{{1}} など）を含む本文は、この口ではまだ作りません（変数には example が要るため）。変数なしの本文にしてください")
    _need(args, token, [MGMT_PERMISSION])
    g = _graph(args, cfg, token)
    current = mw._read("テンプレートの一覧", g.message_templates)
    if any(row["name"] == name and row.get("language") == language for row in current):
        _bad(f"同じ名前・言語のテンプレート（{name}・{language}）が既にあります。別の名前にしてください")
    digest = mw.digest_of("wa_template_create", args.account, name, language, category, body, len(current))
    if not args.confirm:
        return mw._stage_one(
            args, digest=digest,
            payload={"name": name, "language": language, "category": category, "body_chars": len(body), "by": by},
            lines=[f"作りません（確認の一段目です）: {args.account} テンプレート {name}（{language}）",
                   f"  分類: {category}", f"  本文: {body}", f"  by  : {by}",
                   "  作ると Meta の審査に出ます（APPROVED になるまで送れません）。本文は THTH に保存しません",
                   *_production_note(cfg)],
            again=f"thth wa template create {args.account} --name {name} --language {language} --category {category} "
                  f"--body {json.dumps(body, ensure_ascii=False)} --by {json.dumps(by, ensure_ascii=False)}")
    mw._check_confirm(args, digest)
    mw._ready(args, cfg)
    made = _write(args, cfg, "wa_template_create", MGMT_PERMISSION,
                  lambda: g.create_template(name=name, language=language, category=category, body=body))
    if args.json:
        mw._print_json({"ok": True, "changed": True, "account": args.account, "name": name,
                        "status": made.get("status"), "category": made.get("category"), "by": by})
    else:
        print(f"作りました: {args.account} テンプレート {name}（{language}）状態={made.get('status') or '—'}"
              f"（{by}）。審査の結果は thth wa templates {args.account} で読みます")
    return 0


# ------------------------------------------------------------------ 書く: 送る

def cmd_send(args) -> int:
    cfg, token = _load(args)
    by = mw._by(args)
    number_id = _clean(args.from_number, "--from（phone-number-id）", graph.ID_RE, "thth wa numbers に出る数字の id")
    to = _clean(args.to, "--to", graph.PHONE_RE, "国番号から始まる 8〜15 桁の数字（先頭の + は任意）")
    template = _clean(args.template, "--template", graph.NAME_RE, "テンプレートの名前（小文字の英数字と _）")
    language = _clean(args.language, "--language", graph.LANGUAGE_RE, "ja・en_US のような言語の符号")
    _need(args, token, [MSG_PERMISSION, MGMT_PERMISSION])
    g = _graph(args, cfg, token)
    now = jst.now_jst()
    left = _limit_check(args, cfg, now)
    numbers = mw._read("番号の一覧", g.phone_numbers)
    if not any(row["phone_number_id"] == number_id for row in numbers):
        _bad(f"--from {number_id} はこの WABA の番号ではありません（thth wa numbers {args.account} に出る id を使います）")
    templates = mw._read("テンプレートの一覧", g.message_templates)
    found = next((row for row in templates if row["name"] == template and row.get("language") == language), None)
    if found is None:
        mw._refuse("wa_template_not_ready", f"テンプレート {template}（{language}）がこの WABA にありません", rc=1)
    if found.get("status") != "APPROVED":
        mw._refuse("wa_template_not_ready",
                   f"テンプレート {template}（{language}）の状態は {found.get('status') or '不明'} です（送れるのは APPROVED だけ）",
                   rc=1, status=found.get("status"))
    digest = mw.digest_of("wa_send", args.account, number_id, to, template, language, found.get("status"))
    if not args.confirm:
        return mw._stage_one(
            args, digest=digest,
            payload={"from": number_id, "to": to, "template": template, "language": language, "left_today": left,
                     "by": by, "storage": "display_only"},
            lines=[f"送りません（確認の一段目です）: {args.account} → {to}",
                   f"  番号: {number_id}  テンプレート: {template}（{language}・{found.get('category') or '—'}）",
                   f"  by  : {by}", f"  今日あと送れる通数: {left}",
                   "  送るのは承認済みのテンプレートだけです（自由文・変数は送りません）。相手の番号は THTH に保存しません",
                   *_production_note(cfg)],
            again=f"thth wa send {args.account} --from {number_id} --to {to} --template {template} "
                  f"--language {language} --by {json.dumps(by, ensure_ascii=False)}")
    mw._check_confirm(args, digest)
    mw._ready(args, cfg)
    result = _write(args, cfg, "wa_send", MSG_PERMISSION,
                    lambda: g.send_template(phone_number_id=number_id, to=to, template=template, language=language),
                    before=lambda: _limit_check(args, cfg, jst.now_jst()))
    if args.json:
        mw._print_json({"ok": True, "changed": True, "account": args.account, "template": template,
                        "message_status": result.get("message_status"), "by": by})
    else:
        print(f"送りました: {args.account} テンプレート {template}（{by}・状態={result.get('message_status') or '—'}・"
              "相手の番号は保存していません）")
    return 0


# ------------------------------------------------------------------ 登録

def register(sub) -> None:
    """`thth/cli.py` の `build_parser()` から 1 行で呼ばれる。"""
    p = sub.add_parser("wa", help="WhatsApp（番号・テンプレートは読む／作る・送るは二段確認・テンプレートだけ）",
                       formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    verbs = p.add_subparsers(dest="wa_verb", required=True)

    def add(name, help_text, func, *, write=False, json_flag=True):
        q = verbs.add_parser(name, help=help_text)
        q.add_argument("account", metavar="name")
        if write:
            q.add_argument("--by", default=None, help="誰が頼んだか（必須・記録には残さない）")
            q.add_argument("--confirm", default=None, help="一段目が表示した digest。これが無いと何もしない")
            q.add_argument("--wait", type=lock_mod.wait_seconds, default=0, help="ロックを待つ秒数（既定 0）")
        if json_flag:
            q.add_argument("--json", action="store_true")
        q.set_defaults(func=mw._run(func) if write or name in ("numbers", "templates") else func)
        return q

    q = add("add", "WhatsApp の口座の台帳を 1 本書く（production は false）", cmd_add)
    q.add_argument("--waba", required=True, help="WhatsApp Business Account の数字の id")
    q.add_argument("--project", required=True)
    q.add_argument("--business-id", dest="business_id", default=None)
    q.add_argument("--by", required=True)
    q = add("auth", "ユーザーの長い鍵を台帳の .token に（端末から・MCP からはできない）", cmd_auth, json_flag=False)
    q.add_argument("--by", required=True)
    q.add_argument("--code", default=None, help="戻り URL 全体（預かり所を使わないとき）")
    add("numbers", "番号の id・表示名・品質（読むだけ）", cmd_numbers)
    add("templates", "テンプレートの名前・状態・言語（読むだけ）", cmd_templates)
    q = add("stop", "止めの印を置く（送る・作るが断られる）", cmd_stop, json_flag=False)
    q.add_argument("--by", required=True)
    tpl = verbs.add_parser("template", help="テンプレートを作る（二段確認）")
    tpl_verbs = tpl.add_subparsers(dest="wa_template_verb", required=True)
    q = tpl_verbs.add_parser("create", help="テンプレートを作る（二段確認・審査に出る）")
    q.add_argument("account", metavar="name")
    q.add_argument("--name", required=True, help="小文字の英数字と _")
    q.add_argument("--language", required=True, help="ja・en_US など")
    q.add_argument("--category", required=True, help="UTILITY・MARKETING・AUTHENTICATION")
    q.add_argument("--body", required=True, help="本文（変数なし）")
    q.add_argument("--by", default=None)
    q.add_argument("--confirm", default=None)
    q.add_argument("--wait", type=lock_mod.wait_seconds, default=0)
    q.add_argument("--json", action="store_true")
    q.set_defaults(func=mw._run(cmd_template_create))
    q = add("send", "承認済みのテンプレートを 1 通送る（二段確認）", cmd_send, write=True)
    q.add_argument("--from", dest="from_number", required=True, help="phone-number-id（thth wa numbers に出る）")
    q.add_argument("--to", required=True, help="相手の番号（国番号から・保存しません）")
    q.add_argument("--template", required=True)
    q.add_argument("--language", required=True)
