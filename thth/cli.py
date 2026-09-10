"""厚い CLI `thth <subcommand>`（発注 §3・設計 §3.7）。

判断・業務論理はここ・`thth.core`・`thth.select` 等に置く。MCP（`mcp/server.py`）は
これを subprocess で呼んで `--json` の出力を返すだけで、判断を持たない。
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import os
import sys

from . import account_report as account_report_mod
from . import accounts as accounts_mod
from . import approval as approval_mod
from . import collect as collect_mod
from . import core
from . import jst
from . import lint as lint_mod
from . import lock as lock_mod
from . import maintain as maintain_mod
from . import oauth as oauth_mod
from . import queuefile
from . import report as report_mod
from . import selfupdate as selfupdate_mod
from . import topics as topics_mod
from . import writeback as writeback_mod


def _print_json(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def cmd_lint(args) -> int:
    """`thth lint <file...>`（複数可・asmon 関東セッション指摘 2026-09-10）。

    47 本の連載で lint を 47 回呼ぶことになった、という報告を受けて複数受けにした。
    exit code は**全体**で決まる（1 本でも実エラーがあれば非ゼロ）。警告（450 字超）
    では落とさない。
    """
    paths, _note = _expand_targets(args.file, only_draft=False)
    rows, any_error = [], False
    for path in paths:
        messages = lint_mod.lint_file(path)
        errors = [m for m in messages if not lint_mod.is_warning(m)]
        warnings = [m for m in messages if lint_mod.is_warning(m)]
        any_error = any_error or bool(errors)
        rows.append({"file": path, "errors": errors, "warnings": warnings, "ok": not errors})

    if args.json:
        _print_json(rows[0] if len(rows) == 1 else rows)
    else:
        for row in rows:
            prefix = "" if len(rows) == 1 else f"{row['file']}: "
            if not row["errors"] and not row["warnings"]:
                print(prefix + "OK")
            for m in row["errors"] + row["warnings"]:
                print(prefix + m)
    return 0 if not any_error else 1


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


def _expand_targets(files, *, only_draft: bool) -> tuple:
    """ファイルとディレクトリの混在を受けて、対象のファイル一覧に展開する。

    **ディレクトリを受けられるようにした**（kopicha セッション指摘 2026-09-10）。
    手元（Mac）から VM の thth を呼ぶとき、`*.md` は**手元のシェルが展開しようと
    して失敗する**（VM 側のパスは手元に存在しない）。glob を使わずに済むように
    ディレクトリそのものを受ける。

    `only_draft=True`（`thth approve`）のときは、ディレクトリから拾うのは
    `status: draft` のものだけ——「下書きを全部承認する」が自然な意味だから。
    **ファイルを名指しで渡した場合は絞らない**（承認済みに `--by` を足し直す用途が
    ある）。何を外したかは呼び出し側が述べる。
    """
    out, skipped = [], 0
    for item in files if isinstance(files, list) else [files]:
        if not os.path.isdir(item):
            out.append(item)
            continue
        for name in sorted(os.listdir(item)):
            if not name.endswith(".md"):
                continue
            path = os.path.join(item, name)
            if only_draft:
                try:
                    if queuefile.parse(path).front_matter.get("status") != "draft":
                        skipped += 1
                        continue
                except OSError:
                    continue
            out.append(path)
    note = (f"（ディレクトリから {skipped} 本を対象外にしました: status が draft ではない）"
            if skipped else "")
    return out, note


def _prepare_one(path: str):
    """承認できるかを検査して `(準備, 断る理由)` を返す（何も書き換えない）。"""
    messages = lint_mod.lint_file(path)
    errors = [m for m in messages if not lint_mod.is_warning(m)]
    if errors:
        return None, f"{path}: lint に通りません（{errors[0]}）"

    qf = queuefile.parse(path)
    fm = qf.front_matter
    if fm.get("post_id"):
        return None, f"{path}: post_id が付いています（既に投稿済み）"

    account_name = fm.get("account")
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        return None, f"{path}: {e}"

    media = account_cfg["media"]
    section = queuefile.extract_section(qf.body, media)
    if section is None:
        return None, f"{path}: `## {media}` の節がありません"

    approved_sha = approval_mod.compute_approved_sha(
        section=section, account=account_name, reply_to=fm.get("reply_to"),
        topic=fm.get("topic"), publish_at=fm.get("publish_at"))

    # **予定時刻を過ぎた原稿の扱いを、承認の前に言う**（nigamilab セッション指摘
    # 2026-09-10）。起草する人と承認する人が別なので、承認までに時刻が過ぎるのは
    # 普通に起きる。「承認したらいつ出るのか」を承認者が知らないまま押す形に
    # しない。
    warning = None
    now = jst.now_jst()
    stale_days = account_cfg.get("stale_days", 7)
    try:
        publish_at = queuefile.parse_publish_at(fm.get("publish_at"))
    except (ValueError, TypeError):
        publish_at = None
    if publish_at is not None and publish_at <= now:
        late = now - publish_at
        if late > datetime.timedelta(days=stale_days):
            warning = (f"**承認しても出ません**: 予定時刻から {late.days} 日過ぎていて、"
                       f"このアカウントの stale_days={stale_days} を超えています"
                       "（board に要確認として出ます）。publish_at を直してください。")
        else:
            warning = (f"**承認するとすぐ出ます**: 予定時刻 {fm.get('publish_at')} は"
                       f"既に過ぎています（{int(late.total_seconds() // 3600)} 時間前）。")

    return {
        "path": path,
        "warning": warning,
        "account": account_name,
        "publish_at": fm.get("publish_at"),
        "topic": queuefile.normalize_topic(fm.get("topic")),
        "reply_to": fm.get("reply_to"),
        "text": section,
        "approved_sha": approved_sha,
        "digest": approved_sha[:approval_mod.APPROVE_DIGEST_LENGTH],
    }, None


def cmd_approve(args) -> int:
    """`thth approve <file...>`（**二段確認**・複数本まとめて可）。

    **二段にする理由**（masaru 指示 2026-09-10「AI との対話の中から承認できるように
    したい」）。元の線「approve は CLI だけ・MCP には出さない」は最初から何も守って
    いなかった——各セッションは Bash と ssh を持っているので、MCP に出さなくても
    approve は打てる。不便だけがあって保証は無かった（統括の思い違い）。

    1. `--confirm` 無し: **出す本文の全文と digest を表示して、何も書き換えずに終わる**
       （exit 1）。
    2. `--confirm <digest>`: 承認する。

    **防げるのは「A を見せて B を承認する」ほう。** 表示と承認の間に本文・account・
    reply_to・topic・publish_at のどれかが変われば digest が変わり、二段目は通らない。
    承認の前に必ず本文の全文が画面に出ることも、この形が強制する。**防げないのは
    AI が本文を見せずに承認すること**——digest は AI 自身でも計算できる。そこは
    仕掛けではなく記録（`approved_by`）で担保する。

    **複数本をまとめて承認できる**（asmon 関東セッション指摘 2026-09-10）。47 本の
    連載で lint 47 回・一段目 94 回・二段目 47 回になった、という報告を受けての形。
    複数渡すと**束の digest** を 1 つ出す。束の digest は各ファイルの
    `approved_sha` を**パス順に**並べた文字列の sha256 の先頭 12 桁なので、
    **どれか 1 本でも変われば束の digest が変わる**——見せたもの＝承認したもの、の
    保証は崩れない。

    **全部そろって初めて承認する。** 1 本でも lint に落ちる・post_id が付いている・
    節が無いものがあれば、**何も書き換えずに全部断る**（半分だけ承認された状態を
    作らない）。

    **repo の同期も承認の一部**（同指摘 3-b）。ロックを取ったあとに
    `writeback.sync_repo()` を通す。以前は「承認の前に VM で git pull が要る」ことが
    どこにも書いていなかった。手順を文書に足すのではなく、道具の側でやる。
    """
    paths, note = _expand_targets(args.file, only_draft=True)
    if not paths:
        print(f"承認できるものがありません{note}", file=sys.stderr)
        return 1

    repos = {}
    for path in paths:
        repo_dir = writeback_mod.repo_toplevel(path)
        if repo_dir is None:
            print(f"git repo の中のファイルではないので承認できません: {path}", file=sys.stderr)
            return 1
        repos.setdefault(os.path.realpath(repo_dir), []).append(path)
    if len(repos) > 1:
        print("別々の repo のファイルを一度に承認できません（clone ごとに分けてください）: "
              + " / ".join(sorted(repos)), file=sys.stderr)
        return 1
    repo_dir = next(iter(repos))

    repo_lock = lock_mod.AccountLock(accounts_mod.repo_lock_path_for(repo_dir))
    try:
        repo_lock.acquire()
    except lock_mod.LockBusy:
        print(f"いまこの repo を別の実行が使っています（{repo_dir}）。"
              "少し待ってからもう一度 thth approve してください。", file=sys.stderr)
        return 1

    try:
        synced, sync_err, _sha = writeback_mod.sync_repo(repo_dir)
        if not synced:
            print(f"repo を同期できないので承認しません: {sync_err}", file=sys.stderr)
            return 1

        prepared, problems = [], []
        for path in sorted(paths):
            one, problem = _prepare_one(path)
            (problems if problem else prepared).append(problem or one)
        if problems:
            for problem in problems:
                print(problem, file=sys.stderr)
            print(f"{len(problems)} 件に問題があるので、**1 本も承認しませんでした**"
                  "（半分だけ承認された状態を作らないため）。", file=sys.stderr)
            return 1

        bundle = approval_mod.compute_bundle_digest([one["approved_sha"] for one in prepared])

        if not args.confirm:
            _show_first_stage(prepared, bundle, as_json=args.json, note=note)
            return 1
        if args.confirm != bundle:
            print(f"digest が一致しないので承認しません（表示した本文と中身が違います）。"
                  f"いまの digest は {bundle} です。もう一度 thth approve からやり直して"
                  "ください。", file=sys.stderr)
            return 1

        approved_by = args.by or os.environ.get("THTH_ACTOR")
        if not approved_by:
            # **ホスト名で埋めない**（kopicha セッション指摘 2026-09-10）。
            # `--by` を省いたら 28 本に `approved_by: wt`（VM の unix ユーザー名）が
            # 入った。判断したのは masaru なのに、記録は `wt`。承認は THTH が
            # いちばん重く扱っている一線なのだから、**誰が承認したか判らないまま
            # 通してはいけない**。既定を作らず、名乗らせる。
            print("--by を付けてください（誰が承認したかを記録します）。"
                  "例: --by masaru / --by \"claude（kopicha セッション）\"。"
                  "環境変数 THTH_ACTOR でも指定できます。", file=sys.stderr)
            return 1
        approved_at = jst.iso()
        for one in prepared:
            writeback_mod.set_front_matter_fields(one["path"], {
                "status": "approved",
                "approved_sha": one["approved_sha"],
                "approved_at": approved_at,
                "approved_by": approved_by,
                "revoked_at": None,
                "revoked_by": None,
                "revoked_reason": None,
            })

        rel_paths = [os.path.relpath(os.path.realpath(one["path"]), repo_dir) for one in prepared]
        label = (os.path.basename(prepared[0]["path"]) if len(prepared) == 1
                 else f"{len(prepared)} 本")
        pushed, push_err = writeback_mod.commit_and_push(
            repo_dir, rel_path=rel_paths,
            message=f"承認: {label}（{prepared[0]['account']}・{approved_by}）")
    finally:
        repo_lock.release()

    if args.json:
        _print_json({"approved": True, "count": len(prepared), "approved_by": approved_by,
                     "approved_at": approved_at, "bundle_digest": bundle,
                     "files": [{"file": one["path"], "approved_sha": one["approved_sha"],
                                "digest": one["digest"]} for one in prepared],
                     "pushed": pushed, "push_error": push_err or None})
    else:
        print(f"承認しました: {len(prepared)} 本（{approved_by}）")
        for one in prepared:
            print(f"  {os.path.basename(one['path'])} — {one['publish_at']}")

    if not pushed:
        print("承認を commit・push できませんでした。このままでは投稿されません"
              f"（board に unverified_content として出ます）: {push_err}", file=sys.stderr)
        print("  ※ commit だけ済んで push を断られた場合は、その commit がローカルに"
              "残っています。手で push するか、取り消してから承認し直してください。",
              file=sys.stderr)
        return 1
    return 0


def _show_first_stage(prepared: list, bundle: str, *, as_json: bool, note: str = "") -> None:
    """一段目: **出す本文をすべて全文表示する**。何も書き換えない。"""
    if as_json:
        _print_json({"approved": False, "count": len(prepared), "bundle_digest": bundle,
                     "files": [{"file": one["path"], "account": one["account"],
                                "publish_at": one["publish_at"], "topic": one["topic"],
                                "reply_to": one["reply_to"], "text": one["text"],
                                "warning": one.get("warning"),
                                "digest": one["digest"]} for one in prepared]})
        return
    print(f"承認しません（確認の一段目です）: {len(prepared)} 本")
    if note:
        print(f"  {note}")
    for one in prepared:
        print("")
        print(f"=== {one['path']}")
        print(f"  account   : {one['account']}")
        print(f"  publish_at: {one['publish_at']}")
        print(f"  topic     : {one['topic'] or '（なし）'}")
        print(f"  reply_to  : {one['reply_to'] or '（なし）'}")
        if one.get("warning"):
            print(f"  ⚠ {one['warning']}")
        topic_line = topics_mod.verdict_line(one.get("topic"), account=one.get("account"))
        if topic_line:
            print(f"  ◆ {topic_line}")
        print("--- 出す本文 ---")
        sys.stdout.write(one["text"] if one["text"].endswith("\n") else one["text"] + "\n")
        print("--- ここまで ---")
        print(f"digest: {one['digest']}")
    warned = [one for one in prepared if one.get("warning")]
    if warned:
        print("")
        print(f"⚠ 予定時刻を過ぎているものが {len(warned)} 本あります:")
        for one in warned:
            print(f"    {os.path.basename(one['path'])} — {one['warning']}")
    print("")
    if len(prepared) == 1:
        print(f"この本文でよければ: thth approve {prepared[0]['path']} --confirm {bundle}")
        return
    print(f"束の digest: {bundle}")
    print(f"この {len(prepared)} 本でよければ、同じファイルを並べて "
          f"--confirm {bundle} を付けてもう一度実行してください。")

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


def cmd_revoke(args) -> int:
    """`thth revoke <file>`: 承認を取り消す（関東セッション指摘 2026-09-10・最優先）。

    > revoke が無い。承認後に 1 本だけ止めたいとき、正しい操作が用意されていません。
    > いまできるのは本文を書き換えて approval_stale にすることだけで、**止める手段が
    > 壊すことになっています。**

    そのとおりだった。しかも意図して止めたものと、うっかり書き換えたものが board で
    同じ見た目になる。**Meta の権限で投稿の削除ができない**からこそ、出る前に止める
    道が要る（設計 §2.2）。

    やること: `status` を `draft` に戻し、`approved_sha`・`approved_at`・`approved_by`
    を空にし、`revoked_at`・`revoked_by`・`revoked_reason` を残す。**本文には触らない**
    ——止めることと壊すことを分ける。そのまま直して `thth approve` し直せる。

    **投稿と同じ clone ロックを取る**（`approve` と同じ理由）。公開の最中には
    割り込めない。既に出てしまったもの（`post_id` あり）は取り消せないので断る
    ——その場合は Threads の画面から手で消すしかない、とその場で言う。
    """
    repo_dir = writeback_mod.repo_toplevel(args.file)
    if repo_dir is None:
        print(f"git repo の中のファイルではないので取り消しを記録できません: {args.file}",
              file=sys.stderr)
        return 1
    rel_path = os.path.relpath(os.path.realpath(args.file), os.path.realpath(repo_dir))

    revoked_by = args.by or os.environ.get("THTH_ACTOR")
    if not revoked_by:
        print("--by を付けてください（誰が止めたかを記録します）。"
              "環境変数 THTH_ACTOR でも指定できます。", file=sys.stderr)
        return 1
    revoked_at = jst.iso()

    repo_lock = lock_mod.AccountLock(accounts_mod.repo_lock_path_for(repo_dir))
    try:
        repo_lock.acquire()
    except lock_mod.LockBusy:
        print(f"いまこの repo を別の実行が使っています（{repo_dir}）。"
              "少し待ってからもう一度 thth revoke してください。", file=sys.stderr)
        return 1

    try:
        # **検査はロックの中で、同期して読み直してから行う**（外部レビュー第 6 巡 P1-2）。
        #
        # 以前はロックを取る**前**に post_id と status を読んでいた。ロックが守るのは
        # 書き込みだけで、**読んだ事実はその間に古くなる**。検査した直後・ロックを取る
        # 直前に公開が完了すると、`post_id` が付いているのに `draft` へ書き換え、
        # **exit 0 で「取り消しました」と返していた**。止められなかった投稿を、
        # 止められたと利用者に伝える——取り消しという機能で最も避けたい嘘。
        #
        # 別 clone から公開された場合も同じなので、**同期してから**読み直す。
        synced, sync_err, _sha = writeback_mod.sync_repo(repo_dir)
        if not synced:
            print(f"repo を同期できないので取り消しません（いまの状態が判りません）: {sync_err}",
                  file=sys.stderr)
            return 1

        qf = queuefile.parse(args.file)
        fm = qf.front_matter
        if qf.malformed:
            print(f"front-matter が読めないので取り消せません: {args.file}", file=sys.stderr)
            return 1
        if fm.get("post_id"):
            print(f"**もう出ています**（post_id: {fm.get('post_id')}）。"
                  "THTH からは取り消せません。消すなら Threads の画面から手で消してください。",
                  file=sys.stderr)
            return 1
        if fm.get("status") != "approved":
            print(f"承認されていません（status: {fm.get('status')}）。取り消すものがありません: "
                  f"{args.file}", file=sys.stderr)
            return 1

        writeback_mod.set_front_matter_fields(args.file, {
            "status": "draft",
            "approved_sha": None,
            "approved_at": None,
            "approved_by": None,
            "revoked_at": revoked_at,
            "revoked_by": revoked_by,
            "revoked_reason": args.reason or "",
        })
        pushed, push_err = writeback_mod.commit_and_push(
            repo_dir, rel_path=rel_path,
            message=f"承認の取り消し: {os.path.basename(args.file)}（{revoked_by}）")

        # push の直前に `pull --rebase` が走るので、**その間に別 clone から
        # 公開されたもの**が入ってくることがある。書き終えたあとにもう一度見る。
        after = queuefile.parse(args.file).front_matter
        if after.get("post_id"):
            print(f"**取り消せませんでした。処理の途中で公開されました**"
                  f"（post_id: {after.get('post_id')}）。消すなら Threads の画面から"
                  "手で消してください。", file=sys.stderr)
            return 1
    finally:
        repo_lock.release()

    if args.json:
        _print_json({"file": args.file, "status": "draft", "revoked_at": revoked_at,
                     "revoked_by": revoked_by, "revoked_reason": args.reason or None,
                     "pushed": pushed, "push_error": push_err or None})
    else:
        print(f"承認を取り消しました: {args.file}（{revoked_by}）")
        print("  本文はそのままです。直して thth approve し直せます。")

    if not pushed:
        # **ここが押さえどころ。** push できていなければ、VM の clone では
        # 取り消しが commit として残っていても、次の同期で HEAD != upstream に
        # なって投稿そのものが止まる（fail-closed）。ただし黙って安心させない。
        print("取り消しを push できませんでした。**まだ出る可能性があります。**"
              f"手で push して、thth account で確かめてください: {push_err}", file=sys.stderr)
        return 1
    return 0


def cmd_posts(args) -> int:
    """`thth posts <account>`: 実際に出ている投稿を一覧する（読むだけ）。

    **`thth doctor` の要約を投稿一覧の代わりに使わせていたのが間違いだった**
    （nigamilab セッション指摘 2026-09-10: 「`detail` が途中で切れた文字列で返るので、
    2 件目の permalink が読めませんでした」）。doctor は能力の確認が目的なので
    220 字で切る。**投稿を読むための口はこちら。切り詰めない。**

    手で出した分も含めて全部出し、1 本ごとに THTH 経由かどうかを付ける。
    """
    result = account_report_mod.recent_posts(args.account, limit=args.limit)
    if args.json:
        _print_json(result)
        return 0 if not result.get("error") else 1
    if result.get("error"):
        print(f"{args.account}: {result['error']}", file=sys.stderr)
        return 1
    posts = result["posts"]
    if not posts:
        print("投稿がありません")
        return 0
    for post in posts:
        via = f"THTH（{post['file']}）" if post["via_thth"] else "**外で出したもの**"
        topic = f"  [{post['topic']}]" if post.get("topic") else "  [トピック無し]"
        print(f"{post['timestamp']}{topic}  {via}")
        print(f"  id       : {post['id']}")
        print(f"  permalink: {post['permalink']}")
        if post.get("text"):
            for line in post["text"].split("\n"):
                print(f"  | {line}")
        print("")
    outside = sum(1 for p in posts if not p["via_thth"])
    print(f"—— {len(posts)} 件（うち THTH を通していないもの {outside} 件）")
    return 0


def cmd_topics(args) -> int:
    """`thth topics`: トピックを見る・調べた結果を残す。

    **新参者にとってトピックは唯一の入口**（masaru 2026-09-10）。実測でも、
    フォロワー 0 で `中学受験` は 202〜574 views、弱いトピックは 1 view——
    効き目が約 400 倍違う。だから THTH はトピックを 3 つの層で扱う。

      1. `--note`  下調べの結果を残す（誰がいる場所か。人が見て、THTH が覚える）
      2. `--plan`  これから出す本数が、どのトピックに賭かっているか
      3. （既定） 実際にどれだけ見られたか

    **見に行くのは人（またはブラウザを持つ AI）、覚えておくのは THTH。**
    トピック検索の権限（上級アクセス）が降りれば 1 も機械にできる。
    """
    if args.note:
        if not args.verdict:
            print("--verdict を付けてください（alive / mismatch / dead / unknown）",
                  file=sys.stderr)
            return 1
        by = args.by or os.environ.get("THTH_ACTOR")
        if not by:
            print("--by を付けてください（誰が確かめたかを残します）", file=sys.stderr)
            return 1
        try:
            row = topics_mod.record(args.note, verdict=args.verdict,
                                     audience=args.audience or "", by=by,
                                     kind=args.kind, account=args.account,
                                     note=args.reason or "")
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        if args.json:
            _print_json(row)
        else:
            scope = f"（{row['account']} の判定）" if row.get("account") else "（全体の記録）"
            print(f"記録しました: {row['topic']} → {row['verdict']}{scope}"
                  + (f" {row['audience']}" if row["audience"] else ""))
            if not row.get("account"):
                print("  ※ account を添えると**そのプロジェクトの判定**として残せます"
                      "（同じ語でも合う／合わないはプロジェクトで変わります）:"
                      f" thth topics <account> --note {row['topic']} ...")
        return 0

    if args.advise:
        # **書き始める前に LLM が読む口**（masaru 提案 2026-09-10）。
        # 「それが thth から接続している llm に対して供給されるので、ユーザは
        # 意識しないで最適なトピック選択をしてもらえる／結果の fb が入ってくるから
        # どんどん最適化される」。
        # 人向けの表ではなく、**選ぶために必要なことだけ**を上から順に置く。
        return _advise(args.account, as_json=args.json)

    if args.learned:
        rows = topics_mod.learned(account_report_mod.measured_views_all_accounts())
        if args.json:
            _print_json(rows)
            return 0
        if not rows:
            print("まだ何も記録がありません（thth topics --note で下調べを残してください）")
            return 0
        print("型ごとに何が起きたか（全アカウント合算・24 時間時点の実測）")
        for row in rows:
            measured = ("実測まだ" if row["views_median"] is None
                        else f"views 中央値={row['views_median']}"
                             f"（{row['views_min']}〜{row['views_max']}・{row['posts_measured']} 本）")
            print(f"［{row['kind']}］{row['topics']} 語  {measured}")
            print(f"    合っている {row['alive']}・不一致 {row['mismatch']}・"
                  f"人がいない {row['dead']}・未確認 {row['unknown']}")
            print(f"    当たり率 {row['hit_rate']}")
            print(f"    例: {'・'.join(row['examples'])}")
            if row["description"]:
                print(f"    {row['description']}")
        return 0

    if not args.account:
        print("account を指定してください（または --note <トピック> / --learned）",
              file=sys.stderr)
        return 2

    if args.plan:
        result = account_report_mod.topic_plan(args.account)
        if args.json:
            _print_json(result)
            return 0 if not result.get("error") else 1
        if result.get("error"):
            print(f"{args.account}: {result['error']}", file=sys.stderr)
            return 1
        mark = {"alive": "合っている", "mismatch": "**不一致**",
                "dead": "**人がいない**", "unknown": "**未確認**"}
        unchecked = 0
        for row in result["topics"]:
            if row["verdict"] == "unknown":
                unchecked += row["planned"]
            measured = ("—" if row["views_median_24h"] is None
                        else f"{row['views_median_24h']}（{row['measured_posts']}本）")
            print(f"[{row['topic']}]  これから {row['planned']} 本"
                  f"（下書き {row['draft']}・承認済み {row['approved']}）"
                  f"  済 {row['posted']} 本  24h views 中央値={measured}")
            print(f"    {mark[row['verdict']]}"
                  + (f"（{row['audience']}）" if row["audience"] else "")
                  + (f"  {row['checked_at'][:10]} {row['checked_by']}"
                     if row.get("checked_at") else ""))
        if unchecked:
            print(f"—— **未確認のトピックに {unchecked} 本が賭かっています。**"
                  "出す前に確かめることを勧めます。")
        return 0

    result = account_report_mod.topic_performance(args.account, limit=args.limit)
    if args.json:
        _print_json(result)
        return 0 if not result.get("error") else 1
    if result.get("error"):
        print(f"{args.account}: {result['error']}", file=sys.stderr)
        return 1
    if not result["topics"]:
        print("投稿がありません")
        return 0
    for row in result["topics"]:
        span = ("—" if row["views_min"] is None
                else f"{row['views_min']}〜{row['views_max']}")
        print(f"[{row['topic']}]  {row['posts']} 本  "
              f"views 中央値={row['views_median']}（{span}）  いいね計={row['likes_total']}")
        for item in row["items"]:
            print(f"    views={str(item['views']):>6}  likes={str(item['likes']):>3}  "
                  f"{item['timestamp'][:10]}  {item['head']}")
    return 0


def _advise(account_name: str | None, *, as_json: bool) -> int:
    """トピックを選ぶために必要なことを、上から順に 1 画面で出す。

    **これを読めば、使い方文書を読まなくてもトピックを選べる**ことを目標にする。
    実測が溜まるほど、上の「使える語」が具体的になる。
    """
    measured = account_report_mod.measured_views_all_accounts()
    checks = topics_mod.latest(account=account_name)
    kinds = topics_mod.learned(measured)

    def views_of(topic):
        seen = sorted(measured.get(topic, []))
        return seen[len(seen) // 2] if seen else None

    proven, avoid, unproven = [], [], []
    for topic, row in checks.items():
        item = {"topic": topic, "kind": row.get("kind"), "verdict": row["verdict"],
                "audience": row.get("audience") or None,
                "views_median": views_of(topic), "posts": len(measured.get(topic, []))}
        if row["verdict"] == "alive":
            proven.append(item)
        elif row["verdict"] in ("mismatch", "dead"):
            avoid.append(item)
        else:
            unproven.append(item)
    proven.sort(key=lambda r: (r["views_median"] is None, -(r["views_median"] or 0)))

    plan = (account_report_mod.topic_plan(account_name)["topics"]
            if account_name else [])
    unchecked = [r for r in plan if r["verdict"] == "unknown" and r["planned"]]

    # **自分が触っている語を先に、ほかのプロジェクトの記録は後ろに**
    # （kanto セッション要望 2026-09-10: プロジェクトが増えると関係ない語が増える）。
    # 共有すること自体は正しいので**捨てない**——並び順だけ変える。
    mine = {row["topic"] for row in plan}

    def split(items):
        return ([r for r in items if r["topic"] in mine],
                [r for r in items if r["topic"] not in mine])

    if as_json:
        _print_json({"account": account_name, "proven": proven, "avoid": avoid,
                     "unproven": unproven, "kinds": kinds, "unchecked_in_queue": unchecked,
                     "check_url": "https://www.threads.com/search?q=<トピック>&filter=topic"})
        return 0

    print("■ トピックを選ぶ前に（THTH が知っていること）")
    print("")
    def show(items, formatter, empty="  （まだありません）"):
        here, elsewhere = split(items) if account_name else ([], items)
        if not here and not elsewhere:
            print(empty)
            return
        for r in here:
            print("  " + formatter(r))
        if elsewhere:
            if here:
                print("  ── ほかのプロジェクトの記録（参考）")
            for r in elsewhere:
                print("  " + formatter(r))

    def as_proven(r):
        m = ("実測まだ" if r["views_median"] is None
             else f"24h views 中央値 {r['views_median']}（{r['posts']} 本）")
        return (f"{r['topic']}［{r['kind'] or '型なし'}］ {m}"
                + (f" — {r['audience']}" if r["audience"] else ""))

    def as_avoid(r):
        label = "不一致" if r["verdict"] == "mismatch" else "人がいない"
        return (f"{r['topic']}［{r['kind'] or '型なし'}］ {label}"
                + (f" — {r['audience']}" if r["audience"] else ""))

    print("【使ってよい語】確かめ済み・合っている")
    show(proven, as_proven)
    print("")
    print("【避ける語】人はいるが別の場所・または誰もいない")
    show(avoid, as_avoid)
    print("")
    print("【型ごとの傾向】自分たちの実測から")
    for row in kinds:
        m = ("実測まだ" if row["views_median"] is None
             else f"views 中央値 {row['views_median']}（{row['posts_measured']} 本）")
        print(f"  ［{row['kind']}］{row['topics']} 語  {m}  当たり率 {row['hit_rate']}"
              f"（合っている {row['alive']}・不一致 {row['mismatch']}"
              f"・人がいない {row['dead']}）")
    if not kinds:
        print("  （まだありません）")
    if unchecked:
        print("")
        print(f"【いまの queue で未確認】{account_name}")
        for r in unchecked[:12]:
            print(f"  {r['topic']} — これから {r['planned']} 本")
    print("")
    print("【選び方】")
    print("  1. 上の「使ってよい語」から選ぶのが最も確実。**散らすより寄せる。**")
    print("  2. 無ければ、人がいまやっている行動の名前か日常の一般名詞を選ぶ。")
    print("     狭い専門語は精度が上がるのではなく**人がいなくなる**。")
    print("  3. 自分で作った語・「〜と繋がりたい」型は Threads では場になっていない。")
    print("  4. 漢語の専門語は中国語圏の場になりやすい。")
    print("  5. 新しい語を使うなら、**先にログイン状態のブラウザで確かめる**:")
    print("       https://www.threads.com/search?q=<トピック>&filter=topic")
    print("     見るのは「何件あるか」ではなく**誰がいるか**。")
    print("  6. 確かめたら残す:")
    print("       thth topics --note <語> --verdict alive|mismatch|dead \\")
    print("         --kind 行動|一般名詞|抽象|専門語|固有名|つながり型|自作 \\")
    print("         --audience \"誰がいたか\" --by \"<あなた>\"")
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


def cmd_schedule(args) -> int:
    """`thth schedule [account] [--days N]`: 日付順に「いつ何が出るか」を並べる。

    読むだけ（asmon 関東セッション指摘 2026-09-10）。承認済みと下書きの両方を出す
    ——連載を組むときに見たいのは全体だから。
    """
    rows = report_mod.schedule(args.account, days=args.days)
    if args.json:
        _print_json(rows)
        return 0
    if not rows:
        print("これから出る予定はありません")
        return 0
    for row in rows:
        mark = "済" if row["status"] == "approved" else "未"
        overdue = "  ← 時刻を過ぎています" if row["past"] else ""
        topic = f" [{row['topic']}]" if row["topic"] else ""
        print(f"{row['publish_at'][:16]}  {mark}  {row['account']:22} "
              f"{row['file']:28}{topic} {row['head']}{overdue}")
    print(f"—— {len(rows)} 本（承認済み {sum(1 for r in rows if r['status'] == 'approved')}）")
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
    # **投稿より先に、前回送れなかった収集を送り直す**（外部レビュー第 6 巡 P1-1）。
    # 収集の push 失敗が `HEAD != @{u}` を残し、それが同期検査に阻まれて
    # **投稿まで恒久的に止めていた**。ここで復旧させれば、同じ実行の中で投稿が再開する。
    collect_mod.recover_pending(args.account, log=print)

    result = core.throw_once(args.account, production_flag=True, log=print)

    # **投稿のあとに必ず採る**（masaru 裁定 2026-09-10）。数は「読んだ時点の累計」
    # しか返らないので、逃した経過時間は永久に復元できない。採取の失敗で timer の
    # 終了コードを悪くしない（次の実行で埋まる）が、黙らせもしない。
    try:
        collect_rc = collect_mod.run_collect(args.account, log=print)
        if collect_rc:
            print(f"（採取は完全ではありません: exit={collect_rc}。次の実行で埋めます）")
    except Exception as e:  # 採取の失敗で投稿の経路を壊さない
        print(f"（採取に失敗しました: {e}。次の実行で埋めます）", file=sys.stderr)
    return result.exit_code


def cmd_collect(args) -> int:
    """`thth collect <account>`: 数と返信を採る（`thth run` が自動で呼びます）。

    **経過時間で取る**（`thth/collect.py` の docstring 参照）。手で呼ぶ必要は
    ふつうありません。
    """
    names = [args.account] if args.account else accounts_mod.list_account_names()
    worst = 0
    for name in names:
        rc = collect_mod.run_collect(name, log=print)
        worst = max(worst, rc)
    return worst


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
            # トークンの状態は **人向けの出力にも出す**（kopicha セッション指摘
            # 2026-09-10: 文書には出ると書いてあるのに --json にしか出ていなかった）。
            # 期限が切れると 1 本も出なくなるので、見えないのが痛い欄。
            remaining = row.get("token_remaining_days")
            token = row.get("token_state") or "?"
            if remaining is not None:
                token += f"/残り{remaining:.0f}日"
            print(f"{row['account']}: project={row['project']} last_post={last_post} "
                  f"approved_waiting={row['approved_waiting']} type_mismatch={row['type_mismatch']} "
                  f"inflight={inflight} token={token}")
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
    p_lint.add_argument("file", nargs="+", help="ファイルでもディレクトリでも可")
    p_lint.add_argument("--json", action="store_true")
    p_lint.set_defaults(func=cmd_lint)

    p_preview = sub.add_parser("preview", help="実際に投げる本文そのものを返す")
    p_preview.add_argument("file")
    p_preview.add_argument("--json", action="store_true", help="本文に加えて topic 等を JSON で返す")
    p_preview.set_defaults(func=cmd_preview)

    p_approve = sub.add_parser(
        "approve",
        help="本文を見せて（一段目）、digest を渡すと承認する（二段目）")
    p_approve.add_argument("file", nargs="+",
                           help="ファイルでもディレクトリでも可（ディレクトリなら draft の .md をまとめて）")
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

    p_revoke = sub.add_parser(
        "revoke", help="承認を取り消して draft に戻す（本文は触らない）")
    p_revoke.add_argument("file")
    p_revoke.add_argument("--reason", default=None, help="なぜ止めたか（記録に残す）")
    p_revoke.add_argument("--by", default=None, help="誰が止めたか（記録に残す）")
    p_revoke.add_argument("--json", action="store_true")
    p_revoke.set_defaults(func=cmd_revoke)

    p_posts = sub.add_parser(
        "posts", help="実際に出ている投稿を一覧する（手で出した分も含む・読むだけ）")
    p_posts.add_argument("account")
    p_posts.add_argument("--limit", type=int, default=25)
    p_posts.add_argument("--json", action="store_true")
    p_posts.set_defaults(func=cmd_posts)

    p_topics = sub.add_parser(
        "topics", help="トピック別にどれだけ見られたかを並べる（読むだけ）")
    p_topics.add_argument("account", nargs="?")
    p_topics.add_argument("--plan", action="store_true",
                          help="これから出す本数がどのトピックに賭かっているか")
    p_topics.add_argument("--note", default=None, metavar="トピック",
                          help="下調べの結果を残す（誰がいる場所か）")
    p_topics.add_argument("--verdict", default=None,
                          choices=["alive", "mismatch", "dead", "unknown"],
                          help="--note と併用: alive=合っている / mismatch=別の業界・言語 / dead=人がいない")
    p_topics.add_argument("--audience", default=None,
                          help="--note と併用: 誰がいたか（例「レアアース・重加工」）")
    p_topics.add_argument("--kind", default=None, choices=list(topics_mod.KINDS),
                          help="--note と併用: トピックの型（回すほど型ごとの傾向が溜まる）")
    p_topics.add_argument("--advise", action="store_true",
                          help="書き始める前に読む: 使ってよい語・避ける語・型の傾向・選び方")
    p_topics.add_argument("--learned", action="store_true",
                          help="型ごとに何が起きたか（全アカウント合算・実測つき）")
    p_topics.add_argument("--reason", default=None, help="--note と併用: 補足")
    p_topics.add_argument("--by", default=None, help="--note と併用: 誰が確かめたか")
    p_topics.add_argument("--limit", type=int, default=25)
    p_topics.add_argument("--json", action="store_true")
    p_topics.set_defaults(func=cmd_topics)

    p_queue = sub.add_parser("queue", help="draft/approved/posted/型外 と次に出るもの")
    p_queue.add_argument("account", nargs="?")
    p_queue.add_argument("--json", action="store_true")
    p_queue.set_defaults(func=cmd_queue)

    p_schedule = sub.add_parser(
        "schedule", help="日付順に「いつ何が出るか」を並べる（読むだけ）")
    p_schedule.add_argument("account", nargs="?")
    p_schedule.add_argument("--days", type=int, default=None, help="この日数ぶんに絞る")
    p_schedule.add_argument("--json", action="store_true")
    p_schedule.set_defaults(func=cmd_schedule)

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

    p_collect = sub.add_parser(
        "collect", help="数と返信を採る（経過時間の刻みで・thth run が自動で呼びます）")
    p_collect.add_argument("account", nargs="?")
    p_collect.set_defaults(func=cmd_collect)

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
