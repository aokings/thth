#!/usr/bin/env python3
"""断りの棚卸し（設計「監査と審査の機械化と前向きな断り」§1・段 A）。

`thth/**/*.py` と `mcp/server.py`（と release の関門 `tools/release_gate.py`）を AST で読み、断りの符丁を集めて、3 つの欠け
（なぜ・どうすれば通るか・もっと良く）を数える。読むだけ（ファイルを書かない）。

数え方（§1）:

- **断りの口**: `_fail(`・`_refuse(`・`refuse(`・`error(`・`ReportError(`・`GuardRefused(`・
  `RemoteError(`・`ReportServiceError(`（段 A で `OpsRefused(`・`FlowError(` を足した）の第 1 引数が
  符丁の形の文字列のもの、または「符丁: 文」の形で頭に符丁がある文字列のもの。
  第 2 引数以降に文字列（文）があれば「文つき」。
- **集合**: 名に `REASONS`・`REFUSALS`・`SAFE_ERRORS`（と `_REASON_TABLE`）を含む代入の値の中の、
  符丁の形の文字列。
- **JSON の欄**: dict の字面の `"error"`・`"reason"`・`"code"` の値が符丁の形の文字列のもの。
- **fix の辞書**: 名に `NEXT`・`MESSAGES`・`HINTS` を含む dict の鍵（符丁の形のもの）。
  鍵は符丁にも数える（説明の辞書に載るのは断りの符丁）。説明の辞書は目録の見方（`fix_view`）に
  置き換えたので、目録の `FIX_VIEWS` と部の文を AST で読み、見方を持つモジュールの file の辞書として数える。

欠けの見方（今のソース）:

- なぜ: 口が文つき・fix の辞書にある・`core.guard_refusal_message` の分岐にある・
  `lint._REASON_TABLE` にある・ダッシュボードの `THTH_REASON` にある、のどれか。
- fix: fix の辞書にある（`guard_refusal_message` は「数は … で変えられる」の文が付くので fix らしきに数える）。
- better: 文に「もっと」「より良」「伸び」「おすすめ」の語がある（近似）。

目録（`thth/refusal_catalog.py`）が import できれば、目録で見た 3 つの欠けも出す。

使い方: `python3 tools/count_refusals.py [--json] [--list]`
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# §1 の 8 つに、広告の口（`OpsRefused`）と認可の口（`FlowError`・「符丁: 文」の形）を足した（段 A・
# skill に出る `ads_tree_changed`・`facebook_page_not_granted` が数えから漏れていた）。
MOUTHS = frozenset({"_fail", "_refuse", "refuse", "error", "ReportError", "GuardRefused",
                    "RemoteError", "ReportServiceError", "OpsRefused", "FlowError"})
PREFIXED = re.compile(r"([a-z][a-z0-9_]*): ")
SET_NAME = re.compile(r"(REASONS|REFUSALS|SAFE_ERRORS|_REASON_TABLE)")
FIX_NAME = re.compile(r"(NEXT|MESSAGES|HINTS)")
JSON_KEYS = frozenset({"error", "reason", "code"})
# 符丁の形: 英小文字で始まり、`_` で区切った 2 語以上（refusals._CODE と同じ形）。
CODE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+\Z")
BETTER_WORDS = ("もっと", "より良", "伸び", "おすすめ")
DASHBOARD = ROOT / "skills" / "thth" / "dashboard" / "all.html"


def is_code(value) -> bool:
    return isinstance(value, str) and len(value) <= 64 and bool(CODE.fullmatch(value))


def source_files(root: Path = ROOT) -> list[Path]:
    files = sorted(p for p in (root / "thth").rglob("*.py") if "__pycache__" not in p.parts)
    # MCP の口と、release の関門（段 D・`_refuse("release_…")` が断りの口）。
    for extra in (root / "mcp" / "server.py", root / "tools" / "release_gate.py"):
        if extra.exists():
            files.append(extra)
    return files


def _callee(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _target_names(node) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    out = []
    for target in targets:
        if isinstance(target, ast.Name):
            out.append(target.id)
        elif isinstance(target, ast.Attribute):
            out.append(target.attr)
    return out


def _strings(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            yield sub.value


def _has_text(args) -> bool:
    for arg in args:
        if isinstance(arg, ast.JoinedStr):
            return True
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and not is_code(arg.value):
            return True
    return False


def scan(root: Path = ROOT) -> dict:
    """ソースを読んで、符丁ごとの出どころと、fix の辞書の中身を返す。"""
    codes: dict[str, dict] = defaultdict(lambda: {"sources": set(), "files": set(), "texts": []})
    mouth_calls = {"total": 0, "with_text": 0, "code_only": 0}
    fix_dicts: dict[str, dict[str, str]] = {}
    for path in source_files(root):
        rel = str(path.relative_to(root))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update"
                    and isinstance(node.func.value, ast.Name) and FIX_NAME.search(node.func.value.id)
                    and node.args and isinstance(node.args[0], ast.Dict)):
                # `NEXT.update({...})`（あとから足した説明）も fix の辞書に数える。
                name = node.func.value.id
                table = fix_dicts.setdefault(f"{rel}:{name}", {})
                for key, value in zip(node.args[0].keys, node.args[0].values):
                    if isinstance(key, ast.Constant) and is_code(key.value):
                        text = " ".join(_strings(value))
                        table[key.value] = text
                        codes[key.value]["sources"].add("fix_dict")
                        codes[key.value]["files"].add(rel)
                        codes[key.value]["texts"].append(text)
            elif isinstance(node, ast.Call) and _callee(node) in MOUTHS and node.args:
                first = node.args[0]
                while isinstance(first, ast.BinOp) and isinstance(first.op, ast.Add):
                    first = first.left  # `"符丁: 文" + 値` の左の頭
                code, texted = None, False
                if isinstance(first, ast.Constant) and is_code(first.value):
                    code, texted = first.value, _has_text(node.args[1:])
                elif isinstance(first, (ast.Constant, ast.JoinedStr)):
                    # `FlowError("符丁: 文")`・`FlowError(f"符丁: …{値}")` の形（文字列の頭に符丁）。
                    lead = first if isinstance(first, ast.Constant) else (first.values[0] if first.values else None)
                    text = lead.value if isinstance(lead, ast.Constant) and isinstance(lead.value, str) else ""
                    head = PREFIXED.match(text)
                    if head and is_code(head.group(1)):
                        code, texted = head.group(1), True
                if code is not None:
                    mouth_calls["total"] += 1
                    mouth_calls["with_text" if texted else "code_only"] += 1
                    entry = codes[code]
                    entry["sources"].add("mouth_text" if texted else "mouth")
                    entry["files"].add(rel)
                    for arg in node.args[1:]:
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                            entry["texts"].append(arg.value)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                names = _target_names(node)
                if any(SET_NAME.search(name) for name in names):
                    for value in _strings(node.value):
                        if is_code(value):
                            codes[value]["sources"].add("set")
                            codes[value]["files"].add(rel)
                if any(FIX_NAME.search(name) for name in names) and isinstance(node.value, ast.Dict):
                    name = next(n for n in names if FIX_NAME.search(n))
                    table = {}
                    for key, value in zip(node.value.keys, node.value.values):
                        if isinstance(key, ast.Constant) and is_code(key.value):
                            text = " ".join(s for s in _strings(value)) if value is not None else ""
                            table[key.value] = text
                            codes[key.value]["sources"].add("fix_dict")
                            codes[key.value]["files"].add(rel)
                            codes[key.value]["texts"].append(text)
                    if table:
                        fix_dicts[f"{rel}:{name}"] = table
            elif isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (isinstance(key, ast.Constant) and key.value in JSON_KEYS
                            and isinstance(value, ast.Constant) and is_code(value.value)):
                        codes[value.value]["sources"].add("json")
                        codes[value.value]["files"].add(rel)
    for rel, name, table in fix_views(root):
        # 説明の辞書の見方（`NEXT = refusal_catalog.fix_view("remote.NEXT")`）は、目録の `FIX_VIEWS` と部の文から
        # 読む。符丁はその見方を持つモジュールの file に数える（辞書を字面で書いていた時と同じ数え方）。
        fix_dicts[f"{rel}:{name}"] = table
        for code, text in table.items():
            codes[code]["sources"].add("fix_dict")
            codes[code]["files"].add(rel)
            codes[code]["texts"].append(text)
    return {"codes": codes, "mouth_calls": mouth_calls, "fix_dicts": fix_dicts}


def fix_views(root: Path = ROOT) -> list[tuple[str, str, dict[str, str]]]:
    """目録の `FIX_VIEWS`（見方 → 符丁の並び）と部の `fix`・`fix_by_view` を AST で読み、
    `[(モジュールの file, 名前, {符丁: 文}), …]` を返す（import しない）。"""
    try:
        tree = ast.parse((root / "thth" / "refusal_catalog.py").read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    views: dict[str, list[str]] = {}
    for node in tree.body:
        if (isinstance(node, (ast.Assign, ast.AnnAssign)) and "FIX_VIEWS" in _target_names(node)
                and isinstance(node.value, ast.Dict)):
            for key, value in zip(node.value.keys, node.value.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    views[key.value] = [v for v in _strings(value) if is_code(v)]
    entries: dict[str, ast.Dict] = {}
    for part in sorted((root / "thth").glob("refusal_catalog_part*.py")):
        for node in ast.parse(part.read_text(encoding="utf-8")).body:
            if (isinstance(node, ast.Assign) and "ENTRIES" in _target_names(node)
                    and isinstance(node.value, ast.Dict)):
                for key, value in zip(node.value.keys, node.value.values):
                    if isinstance(key, ast.Constant) and isinstance(value, ast.Dict):
                        entries[key.value] = value
    out = []
    for view, view_codes in views.items():
        module, _, name = view.partition(".")
        table = {}
        for code in view_codes:
            fields = {k.value: v for k, v in zip(entries[code].keys, entries[code].values)
                      if isinstance(k, ast.Constant)} if code in entries else {}
            text = ""
            by_view = fields.get("fix_by_view")
            if isinstance(by_view, ast.Dict):
                for k, v in zip(by_view.keys, by_view.values):
                    if isinstance(k, ast.Constant) and k.value == view:
                        text = " ".join(_strings(v))
            if not text and "fix" in fields:
                text = " ".join(_strings(fields["fix"]))
            table[code] = text
        out.append((f"thth/{module}.py", name, table))
    return out


def guard_message_codes(root: Path = ROOT) -> set[str]:
    """`core.guard_refusal_message` の分岐（`code == "…"`・`code in (…)`）にある符丁。"""
    tree = ast.parse((root / "thth" / "core.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "guard_refusal_message":
            found = set()
            for sub in ast.walk(node):
                if isinstance(sub, ast.Compare) and isinstance(sub.left, ast.Name) and sub.left.id == "code":
                    found.update(v for v in _strings(sub) if is_code(v))
            return found
    return set()


def lint_table_codes(root: Path = ROOT) -> set[str]:
    try:
        sys.path.insert(0, str(root))
        from thth import lint  # noqa: PLC0415
        return set(lint.REASONS)
    except Exception:  # noqa: BLE001 — 読めなければ AST の集合に任せる
        return set()


def dashboard_reasons(path: Path = DASHBOARD) -> dict[str, str]:
    """ダッシュボードの `THTH_REASON`（4 本は同じ写し・all.html を読む）。"""
    try:
        html = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    found = re.search(r"const THTH_REASON=\{(.*?)\};", html)
    if not found:
        return {}
    return dict(re.findall(r'(\w+):"([^"]*)"', found.group(1)))


def inventory(root: Path = ROOT) -> dict:
    """符丁の一覧と 3 つの欠け（今のソースで見たもの）。"""
    scanned = scan(root)
    codes = scanned["codes"]
    guard = guard_message_codes(root)
    lint_codes = lint_table_codes(root)
    reasons = dashboard_reasons(root / "skills" / "thth" / "dashboard" / "all.html")
    for code in guard:
        codes[code]["sources"].add("guard_message")
    for code in lint_codes:
        codes[code]["sources"].add("lint_table")
    for code, text in reasons.items():
        codes[code]["sources"].add("dashboard")
        codes[code]["texts"].append(text)
    rows = []
    for code in sorted(codes):
        entry = codes[code]
        sources = entry["sources"]
        has_why = bool(sources & {"mouth_text", "fix_dict", "guard_message", "lint_table", "dashboard"})
        has_fix = "fix_dict" in sources
        fixish = has_fix or "guard_message" in sources
        better = any(word in text for text in entry["texts"] for word in BETTER_WORDS)
        rows.append({"code": code, "sources": sorted(sources), "files": sorted(entry["files"]),
                     "why": has_why, "fix": has_fix, "fixish": fixish, "better": better})
    total = len(rows)
    summary = {
        "codes": total,
        "mouth_calls": scanned["mouth_calls"],
        "why": sum(r["why"] for r in rows),
        "fix": sum(r["fix"] for r in rows),
        "fixish": sum(r["fixish"] for r in rows),
        "better": sum(r["better"] for r in rows),
        "missing": {"why": sum(not r["why"] for r in rows), "fix": sum(not r["fix"] for r in rows),
                    "better": sum(not r["better"] for r in rows)},
        "fix_dicts": len(scanned["fix_dicts"]),
        "multi_dict_codes": sorted(c for c in {k for t in scanned["fix_dicts"].values() for k in t}
                                   if sum(c in t for t in scanned["fix_dicts"].values()) > 1),
        "guard_message_codes": len(guard),
        "lint_table_codes": len(lint_codes),
        "dashboard_reasons": len(reasons),
    }
    return {"summary": summary, "rows": rows, "fix_dicts": sorted(scanned["fix_dicts"])}


def catalog_view(codes: list[str]) -> dict | None:
    """目録で見た 3 つの欠け（目録が import できるときだけ）。"""
    try:
        sys.path.insert(0, str(ROOT))
        from thth import refusal_catalog  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None
    catalog = refusal_catalog.CATALOG
    entries = [catalog[c] for c in codes if c in catalog]
    kinds: dict[str, int] = defaultdict(int)
    no_better: dict[str, int] = defaultdict(int)
    for entry in entries:
        kinds[entry["kind"]] += 1
        if entry.get("no_better"):
            no_better[entry["no_better"]] += 1
    return {
        "entries": len(catalog),
        "missing_from_catalog": sorted(c for c in codes if c not in catalog),
        "why": sum(bool(e.get("why")) for e in entries),
        "fix": sum(bool((e.get("fix") or {}).get("text")) for e in entries),
        "fix_command": sum(bool((e.get("fix") or {}).get("command")) for e in entries),
        "better": sum(bool(e.get("better")) for e in entries),
        "no_better": dict(no_better),
        "kind": dict(kinds),
        "retired": sorted(c for c, e in catalog.items() if e.get("retired")),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="断りの符丁と 3 つの欠けを数える（読むだけ）")
    parser.add_argument("--json", action="store_true", help="JSON で出す")
    parser.add_argument("--list", action="store_true", help="符丁ごとの行も出す")
    args = parser.parse_args(argv)
    found = inventory()
    found["catalog"] = catalog_view([r["code"] for r in found["rows"]])
    if args.json:
        if not args.list:
            found = {k: v for k, v in found.items() if k != "rows"}
        print(json.dumps(found, ensure_ascii=False, indent=2))
        return 0
    s = found["summary"]
    print(f"断りの符丁: {s['codes']}")
    print(f"断りの口の呼び出し: {s['mouth_calls']['total']}（文つき {s['mouth_calls']['with_text']}・"
          f"符丁だけ {s['mouth_calls']['code_only']}）")
    print(f"今のソースで なぜ あり {s['why']}・fix あり {s['fix']}（fix らしき {s['fixish']}）・better あり {s['better']}")
    print(f"欠け: なぜ {s['missing']['why']}・fix {s['missing']['fix']}・better {s['missing']['better']}")
    print(f"fix の辞書 {s['fix_dicts']}・guard_refusal_message の分岐 {s['guard_message_codes']}・"
          f"lint の表 {s['lint_table_codes']}・THTH_REASON {s['dashboard_reasons']}")
    print(f"複数の辞書に写された符丁: {len(s['multi_dict_codes'])} {' '.join(s['multi_dict_codes'])}")
    cat = found["catalog"]
    if cat is not None:
        print(f"目録: 項目 {cat['entries']}・目録に無い符丁 {len(cat['missing_from_catalog'])}・"
              f"why {cat['why']}・fix {cat['fix']}（命令つき {cat['fix_command']}）・better {cat['better']}・"
              f"no_better {cat['no_better']}・kind {cat['kind']}・retired {len(cat['retired'])}")
    if args.list:
        for row in found["rows"]:
            marks = "".join(m if row[k] else "-" for k, m in (("why", "W"), ("fix", "F"), ("better", "B")))
            print(f"{marks} {row['code']}  [{','.join(row['sources'])}]  {' '.join(row['files'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
