"""厚い CLI `thth <subcommand>`（発注 §3・設計 §3.7）。

判断・業務論理はここ・`thth.core`・`thth.select` 等に置く。MCP（`mcp/server.py`）は
これを subprocess で呼んで `--json` の出力を返すだけで、判断を持たない。
"""
from __future__ import annotations

import argparse
import dataclasses
import getpass
import json
import os
import sys

from . import account_report as account_report_mod
from . import accounts as accounts_mod
from . import approval as approval_mod
from . import core
from . import jst
from . import lint as lint_mod
from . import lock as lock_mod
from . import maintain as maintain_mod
from . import oauth as oauth_mod
from . import queuefile
from . import report as report_mod
from . import selfupdate as selfupdate_mod
from . import writeback as writeback_mod


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


def cmd_approve(args) -> int:
    """`thth approve <file>`（**CLI のみ・MCP には出さない**・設計 §3.7・外部レビュー §1）。

    「不在の実行への承認」は masaru の手だけが持つ（統括は書けない・§3.7 の一線）。
    `status: approved`・`approved_sha`・`approved_at`（JST ISO）を front-matter に
    書く。**書く前に lint を通し、通らなければ承認しない**（受け入れ 5）。
    `approved_sha` は「masaru が見た本文」を固定するハッシュ（`thth.approval`）で、
    `select` はこれが現在の内容と一致するときだけ通す（不一致・欠落は
    `approval_stale` で落として board に出す）。

    書いたあと、その 1 ファイルを **commit して push する**（外部レビュー第 4 巡
    P1）。select は「同期を確認した commit の中身と一致するファイル」しか候補に
    しないので、承認をファイルに書いただけでは投稿されない。push できなければ
    その旨を述べて非ゼロで終わる（黙って「承認しました」で終わらせない）。

    **`--confirm <digest>` が必ず要る**（masaru 指示 2026-09-10「AI との対話の中から
    承認できるようにしたい」）。`--confirm` を付けずに呼ぶと、**出す本文そのものと
    digest を表示して、何も書き換えずに終わる**。承認するにはその digest を渡して
    もう一度呼ぶ。`thth send --confirm` とまったく同じ形。

    これは「AI が勝手に承認する」ことを防ぐ仕掛けではない——**防げない**。
    セッションは Bash と ssh を持っているので、`approve` を MCP に出さないという
    従来の線は最初から何も守っていなかった（統括の思い違い・2026-09-10 に自覚）。
    この二段が実際に防ぐのは **「A を見せて B を承認する」**ほう:

      - 承認の前に、**必ず本文の全文が画面に出る**（一段目が表示する）。
      - 二段目は、**一段目が表示した内容から計算した digest** としか一致しない。
        表示と承認の間に本文・account・reply_to・topic・publish_at のどれかが
        変われば digest が変わり、承認は通らない。

    `--by` は**誰が承認したか**を front-matter（`approved_by`）と commit に残す。
    後から「これは誰の判断だったか」を辿れるようにするためで、値を偽れないと
    いう類の保証はしない（そこは記録で担保する）。
    """
    messages = lint_mod.lint_file(args.file)
    errors = [m for m in messages if not lint_mod.is_warning(m)]
    if errors:
        for m in errors:
            print(m, file=sys.stderr)
        print(f"lint に通らないので承認しません: {args.file}", file=sys.stderr)
        return 1

    qf = queuefile.parse(args.file)
    fm = qf.front_matter
    if fm.get("post_id"):
        print(f"post_id が付いています（既に投稿済み）ので承認しません: {args.file}", file=sys.stderr)
        return 1

    account_name = fm.get("account")
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        print(str(e), file=sys.stderr)
        return 1

    media = account_cfg["media"]
    section = queuefile.extract_section(qf.body, media)
    if section is None:
        print(f"media: `## {media}` の節が無いので承認しません: {args.file}", file=sys.stderr)
        return 1

    # 承認は commit として残す（外部レビュー第 4 巡 P1）。select は「同期を確認した
    # commit の中身と一致するファイル」しか候補にしないので、承認をファイルに
    # 書いただけでは出せない（board には `unverified_content` として出る）。
    # ここで commit・push まで済ませることで、**承認した瞬間に利用者 repo の
    # 履歴に残る**——「masaru がいつ何を承認したか」が後から動かせない形になる。
    # commit 先は **そのファイルが入っている repo**（台帳の repo_dir ではない）。
    # masaru が自分の clone で承認することもある。git repo の中でないなら、
    # 承認を記録できないので front-matter を書く前に断る。
    repo_dir = writeback_mod.repo_toplevel(args.file)
    if repo_dir is None:
        print(f"git repo の中のファイルではないので承認しません（承認を commit として"
              f"残せません）: {args.file}", file=sys.stderr)
        return 1
    rel_path = os.path.relpath(os.path.realpath(args.file), os.path.realpath(repo_dir))

    approved_sha = approval_mod.compute_approved_sha(
        section=section, account=account_name, reply_to=fm.get("reply_to"),
        topic=fm.get("topic"), publish_at=fm.get("publish_at"))
    digest = approved_sha[:approval_mod.APPROVE_DIGEST_LENGTH]
    approved_at = jst.iso()

    # 一段目: 本文そのものを見せて、何も書き換えずに終わる。
    if not args.confirm:
        if args.json:
            _print_json({"file": args.file, "status": fm.get("status"), "digest": digest,
                         "text": section, "account": account_name,
                         "publish_at": fm.get("publish_at"),
                         "topic": queuefile.normalize_topic(fm.get("topic")),
                         "reply_to": fm.get("reply_to"), "approved": False})
        else:
            print(f"承認しません（確認の一段目です）: {args.file}")
            print(f"  account   : {account_name}")
            print(f"  publish_at: {fm.get('publish_at')}")
            topic = queuefile.normalize_topic(fm.get("topic"))
            print(f"  topic     : {topic or '（なし）'}")
            print(f"  reply_to  : {fm.get('reply_to') or '（なし）'}")
            print("--- 出す本文 ---")
            sys.stdout.write(section if section.endswith("\n") else section + "\n")
            print("--- ここまで ---")
            print(f"digest: {digest}")
            print(f"この本文でよければ: thth approve {args.file} --confirm {digest}")
        return 1

    if args.confirm != digest:
        print(f"digest が一致しないので承認しません（表示した本文と中身が違います）。"
              f"いまの digest は {digest} です。もう一度 thth approve {args.file} から"
              "やり直してください。", file=sys.stderr)
        return 1

    approved_by = args.by or os.environ.get("THTH_ACTOR") or getpass.getuser()

    # **投稿と同じ clone ロックに参加する**（外部レビュー第 5 巡 P1）。承認は
    # この clone の作業ツリー・index・HEAD を動かすので、公開の最中に割り込めて
    # しまうと、同期を確認した状態と実際に読まれる状態がずれる。**書き換える前に**
    # 取る（front-matter を書いてからでは、取れなかったときに中途半端に残る）。
    # 取れなければ何もせずに断る（待たない・壊さない）。
    repo_lock = lock_mod.AccountLock(accounts_mod.repo_lock_path_for(repo_dir))
    try:
        repo_lock.acquire()
    except lock_mod.LockBusy:
        print(f"いまこの repo を別の実行が使っています（{repo_dir}）。"
              "少し待ってからもう一度 thth approve してください。", file=sys.stderr)
        return 1

    try:
        writeback_mod.set_front_matter_fields(args.file, {
            "status": "approved",
            "approved_sha": approved_sha,
            "approved_at": approved_at,
            "approved_by": approved_by,
        })

        pushed, push_err = writeback_mod.commit_and_push(
            repo_dir, rel_path=rel_path,
            message=f"承認: {os.path.basename(args.file)}（{account_name}・{approved_by}）")
    finally:
        repo_lock.release()

    if args.json:
        _print_json({"file": args.file, "status": "approved",
                     "approved_sha": approved_sha, "approved_at": approved_at,
                     "approved_by": approved_by, "approved": True,
                     "pushed": pushed, "push_error": push_err or None})
    else:
        print(f"承認しました: {args.file}（{approved_by}）")
        print(f"approved_sha: {approved_sha}")
        print(f"approved_at: {approved_at}")

    if not pushed:
        # front-matter は書けたが、承認が commit として残っていない。この状態では
        # **投稿されない**（select が `unverified_content` で落とす）。黙って
        # 「承認しました」で終わらせない。
        print("承認を commit・push できませんでした。このままでは投稿されません"
              f"（board に unverified_content として出ます）: {push_err}", file=sys.stderr)
        print("  ※ commit だけ済んで push を断られた場合は、その commit がローカルに"
              "残っています。手で push するか、取り消してから承認し直してください。",
              file=sys.stderr)
        return 1
    return 0


def cmd_account(args) -> int:
    """`thth account [<name>]`: 1 アカウント（省略時は全部）の状態を一枚で述べる。

    「このアカウントはいま投稿できる状態か」に答える口（masaru 指摘 2026-09-10）。
    台帳・clone・queue・トークン・timer・inflight を 1 か所で見て、**最後に
    投稿できるかどうかの 1 行**を出す。読むだけで、何も変えない。
    """
    names = [args.account] if args.account else accounts_mod.list_account_names()
    details = [account_report_mod.account_detail(name, remote=not args.no_remote)
               for name in names]
    if args.json:
        _print_json(details if args.account is None else details[0])
    else:
        for d in details:
            sys.stdout.write(account_report_mod.render(d))
    # 1 本でも投稿できない状態があれば非ゼロ（board と同じ流儀で、機械から使える）
    return 0 if all(d.get("ready") for d in details) else 1


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
    elif result.action == "none" and result.rejections:
        # 手で打ったときに、落ちた理由を添える（外部レビュー再レビュー C・
        # いままでは「出すものが無い」とだけ出て、何を直せば出るのか分からなかった）。
        for rej in result.rejections:
            print(f"  いま出ない: {rej['file']} — {rej['reason']}")
    return result.exit_code


def cmd_run(args) -> int:
    """timer が呼ぶ形（throw ＋ T3 の collect。T1 は throw だけ）。

    **トークンの更新はここでは行わない**（`thth maintain` が別の timer で行う）。
    投稿が詰まっている・timer を持たない・長期停止中のアカウントでトークンだけが
    死ぬのを避けるため、投稿の可否をトークン保守の前提条件にしない
    （外部レビュー §5・`thth/maintain.py` の docstring）。
    token が無ければ何も投げずに exit 2（設計 §3.2・T3a 訂正 2026-09-09。env は任意
    ・`accounts.token_exists()` docstring 参照）。"""
    # app 自身を最新にしてから走る（設計 §3.2・**lock を取る前**）。進んでいたら
    # 同じ引数で 1 回だけ exec しなおすので、以降の行は新しいコードで動く。
    stale = selfupdate_mod.pull_and_reexec(sys.argv, log=print)
    if stale:
        print(stale, file=sys.stderr)

    try:
        account_cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as e:
        print(str(e), file=sys.stderr)
        return 2
    if not accounts_mod.token_exists(account_cfg):
        print(f"token が無いので実行しません: {args.account}", file=sys.stderr)
        return 2
    result = core.throw_once(args.account, production_flag=True, log=print)
    return result.exit_code


def cmd_auth(args) -> int:
    """masaru が VM で対話的に実行する（設計 §9-3・MCP には出さない・§3.7）。"""
    return oauth_mod.run_auth(args.account, redirect_uri=args.redirect_uri, code=args.code)


def cmd_maintain(args) -> int:
    """`thth maintain`（**CLI のみ・MCP には出さない**）。

    投稿の可否と独立にトークンを保つ（`thth/maintain.py` の docstring 参照）。
    1 日 1 回の timer が引数無しで呼ぶ。人の手が要るものがあれば非ゼロで終わる。
    """
    return maintain_mod.run_maintain(
        args.account, check=args.check, as_json=args.json, log=print)


def cmd_send(args) -> int:
    """`thth send`（同席の様態・§3.7）。本文はファイルか標準入力から受ける。

    **本文をコマンドライン引数で受けない**: シェルの履歴に残り、引用の扱いで
    本文が変わりうる。「masaru が見た本文がそのまま出る」を守るため、
    ファイル（`--text-file`）か標準入力だけにする。

    **`--confirm`**（外部レビュー §1b・受け入れ 6）: dry-run（`--production` を
    付けない実行）が表示する短い digest を、`--production` のときに
    `--confirm <digest>` として渡す。省略・不一致はどちらも送らない。
    """
    import sys as _sys
    from . import core as core_mod
    if args.text_file:
        with open(args.text_file, encoding="utf-8") as f:
            text = f.read()
    else:
        text = _sys.stdin.read()
    result = core_mod.send_once(
        args.account, text=text, topic=args.topic, reply_to=args.reply_to,
        production_flag=args.production, confirm=args.confirm, log=print)
    return result.exit_code


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


def cmd_systemd(args) -> int:
    """`thth systemd <account>`: 台帳から `.timer` unit を機械的に生成して標準出力に
    出す（設計 §3.2・masaru 指摘 2026-09-09）。手で書くと刻みがずれる（実際に
    `systemd/thth@nigamilab-threads.timer` は毎時になっていて 10 分刻みの設計と
    食い違っていた）ので、生成に一本化する。MCP には出さない（運用コマンド・§3.7
    の auth／refresh と同じ扱い）。"""
    from . import systemd_gen
    if getattr(args, "maintain", False):
        # `thth maintain` の timer/service は 1 日 1 回・アカウント別ではない。
        sys.stdout.write(systemd_gen.render_maintain_service() if args.service
                         else systemd_gen.render_maintain_timer())
        return 0
    if not args.account:
        print("account を指定してください（または --maintain）", file=sys.stderr)
        return 2
    try:
        account_cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as e:
        print(str(e), file=sys.stderr)
        return 2
    sys.stdout.write(systemd_gen.render_timer(account_cfg))
    return 0


def cmd_board(args) -> int:
    summary = report_mod.board_summary()
    if args.json:
        _print_json(summary)
    else:
        # 生の dict をそのまま出さず、人が読む形に整える（--json は機械可読のまま
        # 残す・外部レビュー再レビュー C）。
        for row in summary["accounts"]:
            if "error" in row:
                print(f"{row['account']}: {row['error']}")
                continue
            last_post = row["last_post_at"] or "(なし)"
            inflight = row["inflight"] or "(なし)"
            print(f"{row['account']}: project={row['project']} last_post={last_post} "
                  f"approved_waiting={row['approved_waiting']} type_mismatch={row['type_mismatch']} "
                  f"inflight={inflight}")
            # 指紋の 5 項目のどれが食い違って inflight が残ったか（外部レビュー
            # 第 3 巡・持ち越し項目 C）。人が止まった原因をファイルを開いて
            # 自分で探さずに済むように、board の 1 画面にそのまま出す。
            mismatch_fields = row.get("inflight_mismatch_fields")
            if inflight != "(なし)" and mismatch_fields:
                print(f"  食い違った項目: {', '.join(mismatch_fields)}")
            needs_review = row.get("needs_review") or []
            if needs_review:
                # 「承認して待っている（正常）」と「承認が古くて永久に出ない（異常）」
                # を board 1 画面で区別できるようにする印。
                stale = row.get("approval_stale_count", 0)
                print(f"  要確認: {len(needs_review)} 件（approval_stale {stale} 件）")
                for item in needs_review:
                    print(f"    {item['file']} — {item['reason']}")
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

    p_approve = sub.add_parser(
        "approve",
        help="本文を見せて（一段目）、digest を渡すと承認する（二段目）")
    p_approve.add_argument("file")
    p_approve.add_argument("--json", action="store_true")
    p_approve.add_argument("--confirm", default=None,
                           help="一段目が表示した digest。これが無いと承認しない")
    p_approve.add_argument("--by", default=None,
                           help="誰が承認したか（front-matter と commit に残す）")
    p_approve.set_defaults(func=cmd_approve)

    p_account = sub.add_parser(
        "account", help="1 アカウントの状態を一枚で述べる（投稿できる状態かどうか）")
    p_account.add_argument("account", nargs="?")
    p_account.add_argument("--json", action="store_true")
    p_account.add_argument("--no-remote", action="store_true", dest="no_remote",
                           help="Threads 側を引きに行かない（網に出ない・速い）")
    p_account.set_defaults(func=cmd_account)

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

    p_systemd = sub.add_parser(
        "systemd", help="台帳から <account>.timer unit を生成して標準出力に出す")
    p_systemd.add_argument("account", nargs="?")
    p_systemd.add_argument("--maintain", action="store_true",
                           help="thth maintain（トークン保守・1 日 1 回）の unit を出す")
    p_systemd.add_argument("--service", action="store_true",
                           help="--maintain と併用: .timer でなく .service を出す")
    p_systemd.set_defaults(func=cmd_systemd)

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

    p_maintain = sub.add_parser(
        "maintain",
        help="全アカウントのトークンを保つ（投稿とは独立。1 日 1 回の timer が呼ぶ。MCPには出さない）")
    p_maintain.add_argument("--account", default=None, help="1 本だけ見る（省略時は全部）")
    p_maintain.add_argument("--check", action="store_true",
                            help="更新はせず状態だけ述べる")
    p_maintain.add_argument("--json", action="store_true", dest="json")
    p_maintain.set_defaults(func=cmd_maintain)

    p_send = sub.add_parser(
        "send", help="同席の様態: queue を通さずその場で 1 本出す（本文はファイルか標準入力）")
    p_send.add_argument("account")
    p_send.add_argument("--text-file", dest="text_file", default=None,
                        help="本文のファイル。省略時は標準入力から読む")
    p_send.add_argument("--topic", default=None)
    p_send.add_argument("--reply-to", dest="reply_to", default=None)
    p_send.add_argument("--production", action="store_true",
                        help="本番で出す（台帳 production: true が無ければ dry-run のまま）")
    p_send.add_argument("--confirm", default=None,
                        help="dry-run が表示した digest。--production のときはこれが一致しないと送らない")
    p_send.set_defaults(func=cmd_send)

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
