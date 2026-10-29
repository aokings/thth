"""`thth quota <account>`（Meta 計画 段 2・10/29）: 媒体の量の制限の使用量を読むだけ。

**`build_parser()` には 1 行しか足さない**（`quota_cli.register(sub)`・`edit_cli` と同じ作り）。
媒体名では分けず、adapter の `quota()` が値を返すかで分ける（返さない媒体は rc=2 で断る）。

- facebook: Meta の共通層（`thth/meta/usage.py`）が残した `X-App-Usage`・
  `X-Business-Use-Case-Usage`・`x-fb-ads-insights-throttle` と、量の制限の待ち。待ちの間でなければ
  軽い読みを 1 回投げて今の値にする（書く口は呼ばない）。
- 口座が止まっていても読める道は作らない（adapter の口は `leave_gate.bind` で包まれている）。

- instagram（段 3）: 上の 3 つに、1 日の公開の上限（`content_publishing_limit` の `quota_usage`・
  `config.quota_total`）を足す。

capability の語 `quota` は段 3 で facebook・instagram の `CAPABILITIES` に足した（段 2 では
`tests/test_facebook_adapter.py` を 1 行も変えない条件だったので足していなかった）。ここは今も
`quota()` が値を返すかで見る。
"""
from __future__ import annotations

import json
import sys

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import redact as redact_mod
from .adapters import base as adapter_base


def register(sub) -> None:
    """`thth/cli.py` の `build_parser()` から 1 行で呼ばれる。"""
    p = sub.add_parser("quota", help="媒体の量の制限の使用量を読む（読むだけ・Facebook・Instagram）")
    p.add_argument("account")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_quota)


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _fail(args, code: int, message: str) -> int:
    message = redact_mod.redact(message) or ""
    if getattr(args, "json", False):
        _print_json({"ok": False, "account": args.account, "error": message})
    else:
        print(message, file=sys.stderr)
    return code


def _lines(account: str, value: dict) -> list:
    out = [f"{account}: {value.get('medium')} の使用量（{'今の値' if value.get('fresh') else '残っている値'}・"
           f"{value.get('observed_at') or '記録なし'}）"]
    waiting = value.get("waiting")
    if waiting:
        out.append(f"  待ち: Meta の量の制限で {waiting.get('until')} まで（あと {waiting.get('remaining_minutes')} 分・"
                   f"code {waiting.get('code')}）。この間 timer は出しません")
    for key, label in (("app_usage", "アプリ（X-App-Usage・％）"),
                       ("business_use_case_usage", "事業の物（X-Business-Use-Case-Usage）"),
                       ("ads_insights_throttle", "広告の成績（x-fb-ads-insights-throttle）")):
        row = value.get(key)
        out.append(f"  {label}: " + (json.dumps(row, ensure_ascii=False, sort_keys=True) if row is not None
                                       else "ヘッダが返っていません"))
    publishing = value.get("publishing")
    if isinstance(publishing, dict):
        out.append(f"  1 日の公開の上限（content_publishing_limit）: 使用 {publishing.get('usage')} / "
                   f"上限 {publishing.get('total')}（{publishing.get('duration')} 秒の窓）")
    if value.get("regain_minutes"):
        out.append(f"  戻るまでの見込み: {value['regain_minutes']} 分")
    out.append("  appsecret_proof: " + ("付けています" if value.get("appsecret_proof") else "付けていません"))
    return out


def cmd_quota(args) -> int:
    try:
        account_cfg = accounts_mod.load_account(args.account)
    except accounts_mod.AccountError as e:
        return _fail(args, 2, str(e))
    media = account_cfg.get("media")
    try:
        adapter_cls = adapters_mod.adapter_class(media)
    except adapter_base.AdapterError as e:
        return _fail(args, 2, str(e))
    token = accounts_mod.load_token(account_cfg)
    if not adapter_cls.has_token(token):
        return _fail(args, 2, f"{args.account}: token がありません（{adapter_cls.TOKEN_SETUP_HINT}）")
    try:
        adapter = adapters_mod.make_adapter(account_cfg, token)
        value = adapter.quota()
    except accounts_mod.AccountStopped as e:
        return _fail(args, 2, f"{args.account}: 口座が止まっているので媒体を呼びません（{e}）")
    except adapter_base.AdapterError as e:
        return _fail(args, 1, f"{args.account}: 使用量を読めませんでした（{e}）")
    except (ValueError, OSError) as e:
        return _fail(args, 2, f"{args.account}: {e}")
    if value is None:
        return _fail(args, 2, f"{media}: この媒体は THTH から使用量を返しません")
    if args.json:
        _print_json({"ok": True, "account": args.account, **value})
    else:
        for line in _lines(args.account, value):
            print(line)
    return 0
