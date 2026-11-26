"""`thth admin ws …`: 括り（THTH アカウント）の器を管理者が扱う口（括りの段 1）。

設計: docs/設計_THTHアカウント（括り）_叩き台_2026-11-26.md §3 の段 1・§5.3、
共通の層 §1.3・§5。形の約束: docs/約束_括りの対応表_thth.workspaces.1.md。

- `list`             括りごとの持ち主・窓口・媒体の数・口座の数（名前は出さない・数だけ）
- `show <括り>`       1 つの括りの持ち主・窓口・媒体・フォルダ・口座の数
- `add <括り> --lead <媒体[/レーン]> [--owner <人>]`   台帳に足し、`ws/<括り>/` を作る。
                     2 つ目の括りを足すとき、同じ書き換えで `unlisted` を null にする
- `media add <括り> <媒体>`   媒体を括りに結ぶ。ほかの括りにある媒体は移さない（断る）

台帳（対応表）は共通の根の `workspaces.json`（VM では `/srv/thth/workspaces.json`）。
環境変数 `THTH_WS_MAP` があればそちら。書くのはこの口だけ（別の名前で書いて付け替える）。
"""
from __future__ import annotations

import datetime
import json
import os
import sys

from . import accounts, refusal_catalog, workspaces_map as wm


def map_path() -> str:
    return os.environ.get(wm.ENV_PATH) or os.path.join(accounts.common_root(), "workspaces.json")


def _load_raw() -> dict:
    """台帳の中身（無ければ main だけの初めの形）。壊れていれば断る。"""
    path = map_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {"schema": wm.SCHEMA, "unlisted": accounts.MAIN_WS,
                "workspaces": {accounts.MAIN_WS: {"owner": "masaru", "lead": "media-hub"}}, "media": {}}
    except (OSError, json.JSONDecodeError) as exc:
        raise wm.WorkspacesMapError(f"対応表が読めない: {exc}") from exc
    wm.parse(data)
    return data


def _now() -> str:
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).isoformat(timespec="seconds")


def _account_count(ws: str) -> int | None:
    try:
        folder = os.path.join(accounts.workspace_dir(ws), "accounts")
    except accounts.WorkspaceError:
        return None
    if not os.path.isdir(folder):
        return 0
    return sum(1 for f in os.listdir(folder) if f.endswith(".json") and accounts.name_is_safe(f[:-5]))


def _dir_of(ws: str) -> str | None:
    try:
        return accounts.workspace_dir(ws)
    except accounts.WorkspaceError:
        return None


def list_rows() -> list[dict]:
    data = _load_raw()
    rows = []
    for ws, entry in data["workspaces"].items():
        rows.append({"workspace": ws, "owner": entry.get("owner"), "lead": entry.get("lead"),
                     "media": sum(1 for v in data["media"].values() if v == ws),
                     "accounts": _account_count(ws), "folder": _dir_of(ws) is not None})
    return rows


def show(ws: str) -> dict:
    data = _load_raw()
    entry = data["workspaces"].get(ws)
    if entry is None:
        raise accounts.WorkspaceError("workspace_unknown")
    return {"workspace": ws, "owner": entry.get("owner"), "lead": entry.get("lead"),
            "created": entry.get("created"),
            "media": sorted(m for m, w in data["media"].items() if w == ws),
            "unlisted_here": data.get("unlisted") == ws,
            "folder": _dir_of(ws), "accounts": _account_count(ws)}


def add(ws: str, *, lead: str, owner: str = "masaru") -> dict:
    if not accounts.WS_NAME.match(ws or ""):
        raise accounts.WorkspaceError("workspace_name_invalid")
    data = _load_raw()
    if ws in data["workspaces"]:
        raise accounts.WorkspaceError("workspace_exists")
    new = json.loads(json.dumps(data))
    new["workspaces"][ws] = {"owner": owner, "lead": lead, "created": _now()[:10]}
    if len(new["workspaces"]) >= 2:
        new["unlisted"] = None  # 表に無い媒体を main に混ぜない（共通の層 §8.1 S2）
    new["updated_at"] = _now()
    errs = wm.validate(new)
    if errs:
        raise wm.WorkspacesMapError(" / ".join(errs))
    folder = os.path.join(accounts.workspaces_base(), ws)
    if os.path.lexists(folder):
        raise accounts.WorkspaceError("workspace_folder_exists")
    os.makedirs(accounts.workspaces_base(), mode=0o700, exist_ok=True)
    os.mkdir(folder, 0o700)
    with open(os.path.join(folder, "ws.json"), "x", encoding="utf-8") as fh:
        json.dump({"name": ws, "created": new["workspaces"][ws]["created"]}, fh, ensure_ascii=False)
        fh.write("\n")
    wm.write(new, map_path())
    return show(ws)


def media_add(ws: str, medium: str) -> dict:
    data = _load_raw()
    if ws not in data["workspaces"]:
        raise accounts.WorkspaceError("workspace_unknown")
    if not wm.MEDIUM_RE.match(medium or ""):
        raise accounts.WorkspaceError("medium_name_invalid")
    current = data["media"].get(medium)
    if current == ws:
        return show(ws)
    if current is not None:
        # 移すことは表の書き換えではしない（置き場のデータが付いてこない）。
        raise accounts.WorkspaceError("medium_in_other_workspace")
    new = json.loads(json.dumps(data))
    new["media"][medium] = ws
    new["updated_at"] = _now()
    wm.write(new, map_path())
    return show(ws)


NEXT = refusal_catalog.fix_view("ws_admin.NEXT")


def _refuse(code: str, as_json: bool) -> int:
    nxt = NEXT.get(code, "`thth admin ws list` で今の姿を確かめてください。")
    if as_json:
        print(json.dumps({"error": code, "next": nxt}, ensure_ascii=False))
    else:
        print(f"{code}: {nxt}", file=sys.stderr)
    return 2


def command(args) -> int:
    as_json = getattr(args, "json", False)
    try:
        verb = args.ws_command
        if verb == "list":
            payload = {"workspaces": list_rows(), "map": map_path()}
        elif verb == "show":
            payload = show(args.workspace)
        elif verb == "add":
            payload = add(args.workspace, lead=args.lead, owner=args.owner)
        elif verb == "media":
            payload = media_add(args.workspace, args.medium)
        else:  # pragma: no cover - argparse が先に断る
            return 2
    except wm.WorkspacesMapError as exc:
        if as_json:
            print(json.dumps({"error": "workspaces_map_invalid", "detail": str(exc),
                              "next": "対応表を `tools/workspaces_check.py` で確かめてください。"}, ensure_ascii=False))
        else:
            print(f"workspaces_map_invalid: {exc}", file=sys.stderr)
        return 2
    except accounts.WorkspaceError as exc:
        return _refuse(str(exc), as_json)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif verb == "list":
        for r in payload["workspaces"]:
            acc = "フォルダ無し" if r["accounts"] is None else f"口座 {r['accounts']}"
            print(f"{r['workspace']}（持ち主 {r['owner']}・窓口 {r['lead']}）: 媒体 {r['media']}・{acc}")
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")
    return 0


def register_admin(commands) -> None:
    parser = commands.add_parser("ws", help="括り（THTH アカウント）の器: list／show／add／media add")
    sub = parser.add_subparsers(dest="ws_command", required=True)
    p = sub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show")
    p.add_argument("workspace")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("add")
    p.add_argument("workspace")
    p.add_argument("--lead", required=True, help="括りの窓口の媒体（媒体 か 媒体/レーン）")
    p.add_argument("--owner", default="masaru")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("media")
    media_sub = p.add_subparsers(dest="media_command", required=True)
    q = media_sub.add_parser("add")
    q.add_argument("workspace")
    q.add_argument("medium")
    q.add_argument("--json", action="store_true")
    for leaf in (sub.choices["list"], sub.choices["show"], sub.choices["add"], q):
        leaf.set_defaults(func=command)
