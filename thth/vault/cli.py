"""`thth sync …`（台帳を monban の金庫と合わせる）と `thth garden …`（庭の頁）の命令（段 1・**仮**・masaru の答えを
待たずに推しで作った・朝に覆せる）。

    thth sync [--repo <台帳>] [--branch main] [--json]     点検 → git pull --no-rebase monban → git push monban（3 回まで）
    thth sync init <monban://…> [--repo <台帳>] [--passphrase-stdin]
                                       1 台目。monban-vault init を呼ぶ（戻しの文字列は monban が端末に 1 度だけ出す・
                                       THTH は読まない）。人の端末でだけ動く
    thth sync join <monban://…> [--repo <台帳>] [--stdin]   2 台目から。戻しの文字列は monban-vault が直に読む
    thth sync status [--repo <台帳>] [--json]              金庫の頭・番号・大きさ（読むだけ）

    thth garden build <店> --out <フォルダ> [--title 名] [--shop-url https://…] [--json]
                                       店の写しから静的な頁（店の頁・商品の一覧）を作る。公開で買える商品だけ
    thth garden check <フォルダ> [--json]                 出す前の検査だけ（読むだけ）
    thth garden publish <フォルダ> --to <monban://…> [--confirm D] [--json]
                                       二段確認。一段目は新しく出る・変わる・消えるファイルと digest だけ
    thth garden stop|status <monban://…> [--json]          止める（確認なし・すぐ）・様子を見る

前提: monban の道具（`monban-vault`・`git-remote-monban`）が入っていて、`monban login --house <ハウス>` が済んでいること。
THTH は monban login を代わりにしない。
"""
from __future__ import annotations

import argparse
import json

from .. import redact, refusal
from . import INSTALL_HINT, garden, sync
from .wire import VaultRefused


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _refuse(args, exc: VaultRefused) -> int:
    found = refusal.parts(exc.code) or {}
    line = f"{exc.code}: {found.get('why') or exc.code}"
    if exc.detail:
        line += f"（{exc.detail}）"
    payload = {"ok": False, "code": exc.code, "detail": exc.detail, **exc.extra}
    return refusal.emit(args, exc.code, line=redact.redact(line), payload=payload)


def _run(handler):
    def call(args) -> int:
        try:
            return handler(args)
        except VaultRefused as exc:
            return _refuse(args, exc)
    return call


# ------------------------------------------------------------------ thth sync

def cmd_sync(args) -> int:
    done = sync.run(args.repo, branch=args.branch)
    if args.json:
        _print(done)
        return 0
    pulled = f"取り込み {done['pulled']} commit" if done["pulled"] else "取り込みなし"
    pushed = "送りました" if done["pushed"] else "送るものなし"
    print(f"合わせました: {done['branch']}（{pulled}・{pushed}・{done['attempts']} 回目で通りました）")
    return 0


REMOTE_WORDS = {"added": "git remote add monban を足しました", "kept": "remote monban は前からあります",
                "different": "remote monban が別の URL を指しています（変えていません。git remote -v で見てください）",
                "not_repo": "台帳の repo が見つからないので remote は足していません",
                "failed": "git remote add monban に失敗しました", "skipped": ""}


def cmd_init(args) -> int:
    done = sync.init(args.url, repo=args.repo, passphrase_stdin=args.passphrase_stdin)
    if done["recovery_shown"]:
        print("金庫を作りました。戻しの文字列は上に 1 度だけ出ました（THTH は読んでも保存してもいません）。パスワード管理に置いてください。")
    else:
        print("金庫を作りました。鍵は渡した合言葉で包みました（THTH は読んでも保存してもいません）。合言葉はパスワード管理に。")
    if REMOTE_WORDS.get(done["remote"]):
        print(REMOTE_WORDS[done["remote"]])
    print("次: thth sync（台帳を commit してから）")
    return 0


def cmd_join(args) -> int:
    done = sync.join(args.url, repo=args.repo, stdin=args.stdin)
    print("この端末に金庫の鍵を置きました。")
    if REMOTE_WORDS.get(done["remote"]):
        print(REMOTE_WORDS[done["remote"]])
    print("次: thth sync")
    return 0


def cmd_status(args) -> int:
    done = sync.status(args.repo)
    if args.json:
        _print(done)
        return 0
    have = "あります" if done.get("joined") else "ありません（thth sync join）"
    print(f"金庫: 番号 {done.get('seq')}・{done.get('bytes')} バイト・箱 {done.get('boxes')} 個・この端末の鍵 {have}")
    return 0


# ------------------------------------------------------------------ thth garden

def cmd_build(args) -> int:
    done = garden.build(args.store, args.out, title=args.title, shop_url=args.shop_url)
    if args.json:
        _print(done)
        return 0
    print(f"作りました: {done['out']}（{len(done['files'])} ファイル・公開で買える商品 {done['items']} 点・"
          f"載せなかった商品 {done['not_listed']} 点）")
    print(f"次: thth garden publish {done['out']} --to monban://<ハウス>/<アカウント>/<庭>")
    return 0


def cmd_check(args) -> int:
    found = garden.check_dir(args.directory)
    if args.json:
        _print({"ok": True, "files": sorted(found["files"]), "bytes": found["bytes"]})
        return 0
    print(f"通りました: {len(found['files'])} ファイル・{found['bytes']} バイト")
    return 0


def _change_lines(found: dict) -> list:
    words = (("new", "新しく出る"), ("changed", "変わる"), ("removed", "消える"))
    out = []
    for key, word in words:
        for path in found["changes"][key]:
            out.append(f"  {word}: {path}")
    if not out:
        out.append("  前に置いた物と同じです")
    return out


def cmd_publish(args) -> int:
    found = garden.plan(args.directory, args.to)
    payload = {"url": found["url"], "directory": found["directory"], "files": len(found["files"]),
               "bytes": found["bytes"], **found["changes"]}
    if not args.confirm:
        if args.json:
            _print({"ok": False, "stage": 1, "digest": found["digest"], **payload})
            return 1
        print(f"置きません（確認の一段目です）: 庭 {found['url']} に {len(found['files'])} ファイル・"
              f"{found['bytes']} バイト。誰でも読める公開の頁になります")
        if found["changes"]["first"]:
            print("  この端末から置くのは初めてです（前の版との違いは分かりません）")
        for line in _change_lines(found):
            print(line)
        print(f"  見本は手元のブラウザで {found['directory']}/index.html を開いて見てください")
        print(f"  置くなら: thth garden publish {found['directory']} --to {found['url']} --confirm {found['digest']}")
        return 1
    if args.confirm != found["digest"]:
        raise VaultRefused("garden_publish_unconfirmed", found["digest"])
    done = garden.publish(found)
    if args.json:
        _print({"ok": True, **payload, **done})
        return 0
    print(f"置きました: 庭 {found['url']}（{done['files']} ファイル・{done['bytes']} バイト）")
    print(f"止めるなら（確認なし・すぐ）: thth garden stop {found['url']}")
    return 0


def cmd_garden_simple(args) -> int:
    done = garden.simple(args.url, args.garden_command)
    if args.json:
        _print({"ok": True, **done})
        return 0
    if args.garden_command == "stop":
        print(f"止めました: 庭 {done['url']}（次の要求から配られません）")
    else:
        print(f"庭 {done['url']}: {garden.state_words(done)}・{done.get('files')} ファイル・{done.get('bytes')} バイト")
    return 0


# ------------------------------------------------------------------ 登録

SYNC_DESCRIPTION = f"""台帳の repo を monban の金庫と合わせる（段 1・仮。暗号は monban の道具が持ち、THTH は書かない）
    thth sync [--repo <台帳>] [--branch main] [--json]        点検 → git pull --no-rebase monban → git push monban
    thth sync init <monban://ハウス/アカウント/ledger> [--repo <台帳>]   1 台目（人の端末で。戻しの文字列は monban が 1 度だけ出す）
    thth sync join <monban://…> [--repo <台帳>] [--stdin]    2 台目から（戻しの文字列は monban-vault が直に読む）
    thth sync status [--repo <台帳>] [--json]                 金庫の頭・番号・大きさ
  前提: monban の道具（{INSTALL_HINT}）と monban login --house <ハウス>。
"""
GARDEN_DESCRIPTION = """庭（誰でも読める静的な頁）を作って monban に置く（段 1・仮。script・form・外の資源なし）
    thth garden build <店> --out <フォルダ> [--title 名] [--shop-url https://…]   店の写しから（公開で買える商品だけ）
    thth garden check <フォルダ>                                    出す前の検査だけ
    thth garden publish <フォルダ> --to <monban://…> [--confirm D]  二段確認
    thth garden stop|status <monban://…>                            止める（確認なし）・様子
"""


def register(sub) -> None:
    p_sync = sub.add_parser("sync", help="台帳を monban の金庫と合わせる（段 1・仮）", description=SYNC_DESCRIPTION,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    p_sync.add_argument("--repo", default=".", help="台帳の git の repo（既定は今のフォルダ）")
    p_sync.add_argument("--branch", default=sync.DEFAULT_BRANCH, help="合わせる枝（既定 main）")
    p_sync.add_argument("--json", action="store_true")
    p_sync.set_defaults(func=_run(cmd_sync))
    verbs = p_sync.add_subparsers(dest="sync_command", required=False)
    p_init = verbs.add_parser("init", help="1 台目: 金庫を作る（monban-vault init・人の端末で）")
    p_init.add_argument("url", metavar="monban://ハウス/アカウント/金庫")
    p_init.add_argument("--repo", default=".", help="remote monban を足す台帳の repo（既定は今のフォルダ）")
    p_init.add_argument("--passphrase-stdin", action="store_true",
                        help="戻しの文字列の代わりに自分の合言葉（16 文字以上）を標準入力の 1 行で monban-vault へ渡す")
    p_init.set_defaults(func=_run(cmd_init))
    p_join = verbs.add_parser("join", help="2 台目から: この端末に金庫の鍵を置く（monban-vault join）")
    p_join.add_argument("url", metavar="monban://ハウス/アカウント/金庫")
    p_join.add_argument("--repo", default=".", help="remote monban を足す台帳の repo（既定は今のフォルダ）")
    p_join.add_argument("--stdin", action="store_true", help="戻しの文字列を標準入力の 1 行で monban-vault へ渡す")
    p_join.set_defaults(func=_run(cmd_join))
    p_status = verbs.add_parser("status", help="金庫の頭・番号・大きさ（読むだけ）")
    p_status.add_argument("--repo", default=".", help="台帳の git の repo（既定は今のフォルダ）")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=_run(cmd_status))

    p_garden = sub.add_parser("garden", help="庭の静的な頁を作って monban に置く（段 1・仮）",
                              description=GARDEN_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    gverbs = p_garden.add_subparsers(dest="garden_command", required=True)
    p_build = gverbs.add_parser("build", help="店の写しから静的な頁を作る（公開で買える商品だけ）")
    p_build.add_argument("store", metavar="店", help="thth shop add で書いた店の名前（写しは thth shop diff が書く）")
    p_build.add_argument("--out", required=True, metavar="フォルダ", help="作る先（空か、前に作った 3 ファイルだけのフォルダ）")
    p_build.add_argument("--title", default=None, help=f"店の頁の題（{garden.TITLE_MAX} 字まで・既定は店の名前）")
    p_build.add_argument("--shop-url", default=None, help="買う頁（店の URL・https）。リンクとして載せる")
    p_build.add_argument("--json", action="store_true")
    p_build.set_defaults(func=_run(cmd_build))
    p_check = gverbs.add_parser("check", help="出す前の検査だけ（読むだけ）")
    p_check.add_argument("directory", metavar="フォルダ")
    p_check.add_argument("--json", action="store_true")
    p_check.set_defaults(func=_run(cmd_check))
    p_pub = gverbs.add_parser("publish", help="出来た頁を monban の庭に置く（二段確認）")
    p_pub.add_argument("directory", metavar="フォルダ")
    p_pub.add_argument("--to", required=True, metavar="monban://ハウス/アカウント/庭")
    p_pub.add_argument("--confirm", default=None, help="一段目が表示した digest。これが無いと何も置かない")
    p_pub.add_argument("--json", action="store_true")
    p_pub.set_defaults(func=_run(cmd_publish))
    for verb, help_text in (("stop", "庭を止める（確認なし・すぐ・戻すのは monban-vault garden resume）"),
                            ("status", "庭の様子（読むだけ）")):
        q = gverbs.add_parser(verb, help=help_text)
        q.add_argument("url", metavar="monban://ハウス/アカウント/庭")
        q.add_argument("--json", action="store_true")
        q.set_defaults(func=_run(cmd_garden_simple))
