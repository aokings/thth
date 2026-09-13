"""`thth ask before-you-post <account> --topic <語>`（設計 v2 §1・§6 v2-1）。

**`thth/cli.py` の `build_parser()` に足すのは `ask_cli.register(sub)` の 1 行
だけ。** 口の中身はこの module に閉じる——`build_parser()` は同じ時期に別の
Track も触るので、衝突する面を 1 行に減らす（`thth/topic_cli.py` が
`SUBCOMMANDS` で入口を分けているのと同じ筋）。

**読むだけ。** 判断はしない（`thth/ask.py` を呼んで、人向けに並べるだけ）。

exit code:
  - `0` … 答えた（**`cannot_say` だらけでも 0**。「言えない」は正常な答え）
  - `1` … 台帳が無い・壊れている（`accounts.AccountError`）
  - `2` … 問いが受け取れない（未知の媒体・型・時刻帯・語が空。`ask.AskError`）
"""
from __future__ import annotations

import json
import sys
import unicodedata

from . import accounts as accounts_mod
from . import ask as ask_mod
from . import topics as topics_mod


def register(sub) -> None:
    """`thth ask …` を親の subparsers にぶら下げる。"""
    p_ask = sub.add_parser(
        "ask",
        help="投稿する前に聞く（手元の account の水だけで答える・読むだけ）")
    ask_sub = p_ask.add_subparsers(dest="ask_command", required=True)

    p = ask_sub.add_parser(
        "before-you-post",
        help="この語・この型で、スレッドがどう伸びたかの実績を件数と期間つきで返す")
    p.add_argument("account")
    p.add_argument("--topic", required=True, help="語（トピック）")
    p.add_argument("--kind", default=None,
                   help=f"型（{'・'.join(topics_mod.KINDS)}）")
    p.add_argument("--hour-band", dest="hour_band", default=None,
                   help=f"出す予定の時刻帯（{'・'.join(ask_mod.HOUR_BAND_NAMES)}）")
    p.add_argument("--reply", dest="is_reply", action="store_true",
                   help="返信として出す予定（既定は返信ではない投稿）")
    p.add_argument("--window-days", dest="window_days", type=int,
                   default=ask_mod.DEFAULT_WINDOW_DAYS,
                   help=f"直近何日を数えるか（既定 {ask_mod.DEFAULT_WINDOW_DAYS}）")
    p.add_argument("--min-n", dest="min_n", type=int,
                   default=ask_mod.DEFAULT_MIN_N,
                   help=f"中央値を返す下限（既定 {ask_mod.DEFAULT_MIN_N}）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_before_you_post)


def _pad(text, width: int) -> str:
    """表示幅で詰める（全角は 2）。`str.ljust` は全角を 1 と数えるので使えない。

    **`thth/cli.py:_pad()` と同じ規則。** `cli` はこの module を import して
    いるので、あちらから借りると循環する——表示幅は純粋な関数なので、ここは
    写しを持つ（規則が割れる余地は無い）。
    """
    text = str(text)
    w = sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
            for ch in text)
    return text + " " * max(0, width - w)


def _fmt(value) -> str:
    """数でなければ `—`。**`0` と「言えない」を見た目でも分ける。**"""
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.1f}".rstrip("0").rstrip(".") or "0"
    return str(value)


def cmd_before_you_post(args) -> int:
    try:
        answer = ask_mod.before_you_post(
            args.account, topic=args.topic, kind=args.kind,
            hour_band=args.hour_band, is_reply=args.is_reply,
            window_days=args.window_days, min_n=args.min_n)
    except ask_mod.AskError as e:
        print(str(e), file=sys.stderr)
        return 2
    except accounts_mod.AccountError as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(answer, ensure_ascii=False, indent=2))
        return 0

    prov = answer["provenance"]
    comp = answer["comparable"]
    print(f"{args.account}  before_you_post"
          f"（{prov['source']}＝手元の台帳。**泉ではありません**）")
    print("")
    print(answer["summary"])
    print("")
    print(f"  {_pad('指標', 20)}{_pad('中央値', 10)}{_pad('p25', 8)}"
          f"{_pad('p75', 8)}n")
    for metric, label, unit in ask_mod.METRICS:
        stat = answer["expected"][metric]
        名 = f"{label}（{unit}）" if unit else label
        print(f"  {_pad(名, 20)}{_pad(_fmt(stat['median']), 10)}"
              f"{_pad(_fmt(stat['p25']), 8)}{_pad(_fmt(stat['p75']), 8)}"
              f"{stat['n']}")
    print("  ※ `—` は「言えない」（0 ではありません）")
    print("")
    print(f"  比べた群: n={comp['n']}・直近 {comp['window_days']} 日"
          f"・揃えた条件 {comp['aligned_on']}・媒体 {comp['medium']}")
    print("")

    print("  言えないこと:")
    if not answer["cannot_say"]:
        print("    （ありません）")
    for line in answer["cannot_say"]:
        print(f"    - {line}")
    print("")

    one = answer["one_thing_to_change"]
    print(f"  1 つだけ挙げるなら: {one if one else '差が言える群がありません'}")
    print("")

    print("  誰がいるか（観測）:")
    if not answer["audience"]:
        print("    （この語の観測はまだありません）")
    for row in answer["audience"]:
        # **観測者ごとに並べる**（設計 v2 §1 規約 6′）。1 人の自由文を人数と
        # 並べて出すと「N 人がこう言った」と読めた（監査 2・B9）。
        print(f"    - {row['topic']}: 観測者 {row['observers']} 人"
              f"・最新 {row['latest'] or '不明'}")
        for view in row["views"]:
            who = view["who"] or "（自由文の記録なし）"
            print(f"      - {who}（{view['latest'] or '不明'}）")
        if row["views_more"]:
            print(f"      ほか {row['views_more']} 人")
    print("")

    print(f"  出所 {prov['source']}・観測者 {prov['observers']} 人"
          f"・更新 {prov['updated'] or '不明'}・schema {prov['schema']}")
    return 0
