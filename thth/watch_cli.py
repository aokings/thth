"""監視語の口（設計 3.1.0 §3）——`thth admin watch set|show`。

**語は管理者が入れる**（masaru 裁定 3.1.0 §7-2「監視語は管理者が入れる（LLM に
選ばせない）」）。道具は語を**選ばない・足さない・直さない**。語が無い account
は `thth morning` の第 3 段（世間）を `no_watch_words` で飛ばす——**推測で語を
選んだ 1 枚を出さない。**

置き場は台帳（`accounts/<account>.json` の `watch_words`）。形の検査は
`accounts.valid_watch_words()` の 1 か所（loader も同じものを見る）。変更は
管理記録（`watch_set`）に **presence-only** で残る——**語そのものは記録に
書かない**（`admin_log` は「在った／無かった」だけ受け取る）。
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from . import accounts, admin_log, secrets_fs


class WatchError(ValueError):
    """語の形が受け取れない・account 名が使えない（静的な符丁だけを持つ）。"""


def show(account: str) -> dict:
    """いま入っている語（読むだけ）。"""
    if not accounts.name_is_safe(account):
        raise WatchError("invalid_account")
    cfg = accounts.load_account(account)
    return {"account": account, "watch_words": accounts.watch_words(cfg),
            "n": len(accounts.watch_words(cfg)),
            "max_words": accounts.WATCH_WORDS_MAX,
            "max_chars": accounts.WATCH_WORD_MAX_CHARS}


def set_words(account: str, words, *, by: str, via: str = "cli") -> dict:
    """語を**入れ替える**（追記ではない）。`--by` は必須（誰が入れたかを残す）。"""
    admin_log.actor(by)
    if not accounts.name_is_safe(account):
        raise WatchError("invalid_account")
    if not isinstance(words, list):
        raise WatchError("invalid_watch_words")
    cleaned = [word.strip() for word in words if isinstance(word, str)]
    if len(cleaned) != len(words) or not accounts.valid_watch_words(cleaned):
        raise WatchError("invalid_watch_words")
    # 台帳が読めて、停止していないことを先に確かめる（loader と同じ入口）。
    cfg = accounts.load_account(account)
    path = Path(accounts.accounts_dir()) / (account + ".json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WatchError("ledger_unavailable") from exc
    if not isinstance(raw, dict):
        raise WatchError("ledger_unavailable")
    before = raw.get("watch_words") or []
    raw["watch_words"] = cleaned
    secrets_fs.atomic_write_json(str(path), raw)
    # **presence-only**（設計 3.1.0 §3）。語そのものは管理記録に残さない。
    admin_log.append("watch_set", account, cfg, by=by, via=via,
                     diff={"watch_words": ["present" if before else "absent",
                                           "present" if cleaned else "absent"]})
    return {"account": account, "watch_words": cleaned, "n": len(cleaned)}


def _fail(args, reason: str) -> int:
    print(reason, file=sys.stderr)
    if getattr(args, "json", False):
        print(json.dumps({"cannot_say": [reason]}, ensure_ascii=False))
    return 2


def cmd_set(args) -> int:
    try:
        result = set_words(args.account, list(args.words), by=args.by)
    except (WatchError, ValueError) as exc:
        return _fail(args, str(exc) if str(exc) in ("invalid_account", "invalid_watch_words",
                                                    "ledger_unavailable")
                     else "invalid_watch_words")
    except accounts.AccountError:
        return _fail(args, "ledger_unavailable")
    except (OSError, TypeError, admin_log.AdminLogError):
        return _fail(args, "watch_set_unavailable")
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{result['account']}: 監視語 {result['n']} 語")
        for word in result["watch_words"]:
            print(f"  {word}")
    return 0


def cmd_show(args) -> int:
    try:
        result = show(args.account)
    except WatchError as exc:
        return _fail(args, str(exc))
    except accounts.AccountError:
        return _fail(args, "ledger_unavailable")
    except (OSError, ValueError, TypeError):
        return _fail(args, "watch_show_unavailable")
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"{result['account']}: 監視語 {result['n']} 語"
              f"（最大 {result['max_words']} 語・1 語 {result['max_chars']} 字）")
        for word in result["watch_words"]:
            print(f"  {word}")
        if not result["watch_words"]:
            print("  （無し。morning の世間の段は no_watch_words で飛ばします）")
    return 0


def register(commands) -> None:
    parser = commands.add_parser(
        "watch", help="監視語（管理者が入れる・morning の世間の段が読む）")
    operations = parser.add_subparsers(required=True)
    setter = operations.add_parser(
        "set", description="その account の監視語を入れ替えます（追記ではありません）。"
                           f"最大 {accounts.WATCH_WORDS_MAX} 語・1 語 "
                           f"{accounts.WATCH_WORD_MAX_CHARS} 字まで。"
                           "変更は presence-only で管理記録に残ります（語は残しません）。")
    setter.add_argument("account")
    setter.add_argument("words", nargs="+", metavar="語")
    setter.add_argument("--by", required=True)
    setter.add_argument("--json", action="store_true")
    setter.set_defaults(func=cmd_set)
    viewer = operations.add_parser(
        "show", description="その account の監視語を読みます（読むだけ）。")
    viewer.add_argument("account")
    viewer.add_argument("--json", action="store_true")
    viewer.set_defaults(func=cmd_show)
