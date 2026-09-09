"""厚い CLI `thth <subcommand>`（発注 §3・設計 §3.7）。

判断・業務論理はここ・`thth.core`・`thth.select` 等に置く。MCP（`mcp/server.py`）は
これを subprocess で呼んで `--json` の出力を返すだけで、判断を持たない。
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys

from . import accounts as accounts_mod
from . import core
from . import lint as lint_mod
from . import oauth as oauth_mod
from . import queuefile
from . import report as report_mod


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_lint(args) -> int:
    messages = lint_mod.lint_file(args.file)
    errors = [m for m in messages if not lint_mod.is_warning(m)]
    warnings = [m for m in messages if lint_mod.is_warning(m)]
    if args.json:
        _print_json({"file": args.file, "errors": errors, "warnings": warnings, "ok": not errors})
    elif not messages:
        print("OK")
    else:
        for m in messages:
            print(m)
    # 警告（450 字超）は落とさない。exit code は実エラーだけで決まる（食い違い 2 の裁定）。
    return 0 if not errors else 1


def cmd_preview(args) -> int:
    """本文だけを出す規約（設計 §4.1）。`--json` のときだけ topic 等も返す
    （T2c・masaru 裁定 2026-09-09。本文の規約そのものは変えない）。"""
    try:
        section = lint_mod.preview_file(args.file)
    except (ValueError, accounts_mod.AccountError) as e:
        print(str(e), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        qf = queuefile.parse(args.file)
        topic = queuefile.normalize_topic(qf.front_matter.get("topic"))
        _print_json({"file": args.file, "text": section, "topic": topic})
        return 0
    sys.stdout.write(section)
    return 0


def cmd_queue(args) -> int:
    summary = report_mod.queue_summary(args.account)
    if args.json:
        _print_json(summary)
    else:
        for name, info in summary.items():
            if "error" in info:
                print(f"{name}: {info['error']}")
                continue
            c = info["counts"]
            topic_suffix = f" topic={info['next_topic']}" if info.get("next_topic") else ""
            print(f"{name}: draft={c['draft']} approved={c['approved']} posted={c['posted']} "
                  f"型外={info['type_mismatch']} 次={info['next_file']}（{info['next_publish_at']}）"
                  f"{topic_suffix}")
            for rej in info.get("next_rejections") or []:
                print(f"  いま出ない: {rej['file']} — {rej['reason']}")
    return 0


def cmd_throw(args) -> int:
    result = core.throw_once(args.account, production_flag=args.production,
                              bypass_pace=args.now, log=print)
    if args.json:
        _print_json(dataclasses.asdict(result))
    return result.exit_code


def cmd_run(args) -> int:
    """timer が呼ぶ形（throw ＋ T3 の collect ＋ T4 の refresh。T1 は throw だけ）。
    env・token が無ければ何も投げずに exit 2（設計 §3.2・受け入れ 7）。"""
    try:
        account_cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as e:
        print(str(e), file=sys.stderr)
        return 2
    if not accounts_mod.env_and_token_exist(account_cfg):
        print(f"env・token が無いので実行しません: {args.account}", file=sys.stderr)
        return 2
    result = core.throw_once(args.account, production_flag=True, log=print)
    return result.exit_code


def cmd_auth(args) -> int:
    """masaru が VM で対話的に実行する（設計 §9-3・MCP には出さない・§3.7）。"""
    return oauth_mod.run_auth(args.account, redirect_uri=args.redirect_uri, code=args.code)


def cmd_doctor(args) -> int:
    """`thth doctor`（読み取りだけで能力を測る。副作用を持たない・MCP には出さない）。"""
    from . import accounts as accounts_mod
    from . import doctor as doctor_mod
    try:
        return doctor_mod.run_doctor(args.account, as_json=args.as_json)
    except accounts_mod.AccountError as e:
        print(str(e))
        return 2


def cmd_refresh(args) -> int:
    """長期トークンの更新（設計 §2.2・MCP には出さない・§3.7）。"""
    return oauth_mod.run_refresh(args.account, force=args.force, check=args.check)


def cmd_token_set(args) -> int:
    """masaru が Meta 管理画面で発行した長期トークンを貼り付けて保存する
    （T2b・OAuth 往復を経ない tester 向け経路・MCP には出さない・§3.7 と同じ理由）。"""
    return oauth_mod.run_token_set(args.account, force=args.force, stdin=args.stdin)


def cmd_board(args) -> int:
    summary = report_mod.board_summary()
    if args.json:
        _print_json(summary)
    else:
        for row in summary["accounts"]:
            print(row)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="thth")
    sub = p.add_subparsers(dest="command", required=True)

    p_lint = sub.add_parser("lint", help="front-matter の形式・文字数等を検査する")
    p_lint.add_argument("file")
    p_lint.add_argument("--json", action="store_true")
    p_lint.set_defaults(func=cmd_lint)

    p_preview = sub.add_parser("preview", help="実際に投げる本文そのものを返す")
    p_preview.add_argument("file")
    p_preview.add_argument("--json", action="store_true", help="本文に加えて topic 等を JSON で返す")
    p_preview.set_defaults(func=cmd_preview)

    p_queue = sub.add_parser("queue", help="draft/approved/posted/型外 と次に出るもの")
    p_queue.add_argument("account", nargs="?")
    p_queue.add_argument("--json", action="store_true")
    p_queue.set_defaults(func=cmd_queue)

    p_throw = sub.add_parser("throw", help="approved を 1 件投げる（既定 dry-run）")
    p_throw.add_argument("account")
    p_throw.add_argument("--now", action="store_true", help="静かな時間帯・最短間隔を無視して今すぐ試す")
    p_throw.add_argument("--production", action="store_true")
    p_throw.add_argument("--json", action="store_true")
    p_throw.set_defaults(func=cmd_throw)

    p_run = sub.add_parser("run", help="throw ＋ collect ＋ refresh（timer が呼ぶ形）")
    p_run.add_argument("account")
    p_run.set_defaults(func=cmd_run)

    p_board = sub.add_parser("board", help="アカウントごとの鮮度・inflight・型外の骨")
    p_board.add_argument("--json", action="store_true")
    p_board.set_defaults(func=cmd_board)

    p_auth = sub.add_parser("auth", help="認可コードから長期トークンを取得する（masaru が対話で実行。MCPには出さない）")
    p_auth.add_argument("account")
    p_auth.add_argument("--redirect-uri", dest="redirect_uri", default=None,
                         help="省略時は accounts/<account>.json の redirect_uri を使う")
    p_auth.add_argument("--code", dest="code", default=None,
                         help="非対話用（テスト等）。省略時は標準入力から読む")
    p_auth.set_defaults(func=cmd_auth)

    p_refresh = sub.add_parser("refresh", help="長期トークンを更新する（50日超・--forceで無条件。MCPには出さない）")
    p_refresh.add_argument("account")
    p_refresh.add_argument("--force", action="store_true")
    p_refresh.add_argument("--check", action="store_true", help="更新はせず残日数等をJSONで返す（boardが使う）")
    p_refresh.set_defaults(func=cmd_refresh)

    p_doctor = sub.add_parser(
        "doctor", help="そのトークンで実際に何ができるかを読み取りだけで測る（MCPには出さない）")
    p_doctor.add_argument("account")
    p_doctor.add_argument("--json", action="store_true", dest="as_json")
    p_doctor.set_defaults(func=cmd_doctor)

    p_token = sub.add_parser("token", help="長期トークンを直接扱う（現状 set のみ。MCPには出さない）")
    token_sub = p_token.add_subparsers(dest="token_command", required=True)
    p_token_set = token_sub.add_parser(
        "set", help="管理画面で発行したトークンを貼り付けて検証し .token に保存する")
    p_token_set.add_argument("account")
    p_token_set.add_argument("--force", action="store_true", help="既存の .token を上書きする")
    p_token_set.add_argument("--stdin", action="store_true",
                              help="標準入力から黙って1行読む（非対話・パイプ用）")
    p_token_set.set_defaults(func=cmd_token_set)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
