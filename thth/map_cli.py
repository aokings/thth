"""観測の地図の CLI（設計 3.5.0 §1〜§3）。口の中身は `thth/map_store.py` ほかに閉じる。

管理者: `thth admin map node add|remove`・`thth admin map edge add|remove`。
**点と線は人だけが足す**（`--by` 必須）。
"""
from __future__ import annotations

import json
import sys

from . import map_store


def _print_refusal(args, error):
    reason = str(error)
    print(f"{reason}: {map_store.NEXT.get(reason, '')}".rstrip(": "), file=sys.stderr)
    if getattr(args, "json", False):
        print(json.dumps({"cannot_say": [reason]}, ensure_ascii=False))
    return 2


def _emit(args, payload, render):
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        render(payload)
    return 0


# ------------------------------------------------------------------ 管理者

def cmd_admin_node(args) -> int:
    try:
        if args.map_node == "add":
            result = map_store.add_node(args.project, args.word, by=args.by)
        else:
            result = map_store.remove_node(args.project, args.word, by=args.by)
    except map_store.MapError as error:
        return _print_refusal(args, error)
    if args.map_node == "add":
        return _emit(args, result, lambda r: print(
            f"{r['project']}: 点「{r['node']}」を足しました（{r['n_nodes']}/{r['max_nodes']}）"))
    return _emit(args, result, lambda r: print(
        f"{r['project']}: 点「{r['node']}」を消しました（線 {r['edges_removed']} 本・"
        f"集計の行 {r['rows_removed']} 行も消えました）"))


def cmd_admin_edge(args) -> int:
    try:
        call = map_store.add_edge if args.map_edge == "add" else map_store.remove_edge
        result = call(args.project, args.narrower, args.broader, by=args.by)
    except map_store.MapError as error:
        return _print_refusal(args, error)
    verb = "張りました" if args.map_edge == "add" else "外しました"
    return _emit(args, result, lambda r: print(
        f"{r['project']}: 線「{r['edge']['narrower']} ⊂ {r['edge']['broader']}」を{verb}"
        f"（{r['n_edges']} 本）"))


def register_admin(commands) -> None:
    """`thth admin map node|edge`（設計 3.5.0 §1）。"""
    parser = commands.add_parser(
        "map", help="観測の地図（点と線は人が足す）",
        description="観測の地図の点（観測軸）と線（包含）を人が足す・消す（設計 3.5.0）。"
                    f"点は project あたり {map_store.MAX_NODES} まで。@名前・URL・個人名らしき"
                    "語は点にできません。変更は presence-only で変更ログに残ります（語は残しません）。")
    operations = parser.add_subparsers(dest="map_admin", required=True)

    node = operations.add_parser("node", help="点（観測軸）を足す・消す")
    node_ops = node.add_subparsers(dest="map_node", required=True)
    for verb, text in (("add", "点を 1 つ足す"), ("remove", "点を 1 つ消す（線と集計の行も消える）")):
        leaf = node_ops.add_parser(verb, help=text)
        leaf.add_argument("project", help="project 名（account 名ならその project）")
        leaf.add_argument("word", metavar="語")
        leaf.add_argument("--by", default=None, help="誰が変えたか（必須）")
        leaf.add_argument("--json", action="store_true")
        leaf.set_defaults(func=cmd_admin_node)

    edge = operations.add_parser("edge", help="包含の線（狭い語 ⊂ 広い語）を張る・外す")
    edge_ops = edge.add_subparsers(dest="map_edge", required=True)
    for verb, text in (("add", "包含の線を 1 本張る"), ("remove", "包含の線を 1 本外す")):
        leaf = edge_ops.add_parser(verb, help=text)
        leaf.add_argument("project", help="project 名（account 名ならその project）")
        leaf.add_argument("narrower", metavar="狭い語")
        leaf.add_argument("broader", metavar="広い語")
        leaf.add_argument("--by", default=None, help="誰が変えたか（必須）")
        leaf.add_argument("--json", action="store_true")
        leaf.set_defaults(func=cmd_admin_edge)
