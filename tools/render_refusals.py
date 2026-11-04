#!/usr/bin/env python3
"""断りの目録から、Python の外の 2 つの口の表を作る（設計「監査と審査の機械化と前向きな断り」§3）。

- **ダッシュボード**: `skills/thth/dashboard/*.html` の 4 本の `const THTH_REASON={…};` の 1 か所だけを、
  目録の `dashboard` の口の `why`（`refusal_catalog.dashboard_view()`）から書き直す。HTML のほかは触らない。
- **Worker**: `callback/src/refusal-catalog.js` に、目録の `worker` の口の 3 つ（`refusal_catalog.door_view("worker")`）を
  JSON で書き出す（Worker の `reply` が読む）。

**表を二か所に書かない**: 文の正本は目録（`thth/refusal_catalog*.py`）。生成物は手で直さない。

    python3 tools/render_refusals.py           # 書き込む
    python3 tools/render_refusals.py --check   # 差があれば rc=1（どの file か stderr に出す）

`tests/test_refusal_worker.py` と `tests/test_refusal_catalog.py` が同じ比較をするので、目録を直して
作り直さないと試験が落ちる。Worker の符丁の棚卸し（`worker_codes`）もここに置く（試験が読む）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from thth import refusal_catalog  # noqa: E402

DASHBOARDS = tuple(ROOT / "skills" / "thth" / "dashboard" / f"{name}.html"
                   for name in ("all", "campaign", "series", "project"))
WORKER_JS = ROOT / "callback" / "src" / "refusal-catalog.js"
WORKER_SRC = ROOT / "callback" / "src"
TABLE = re.compile(r"const THTH_REASON=\{.*?\};")
KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

# Worker が断りの符丁を書く形（`{error:'…'}`・`fail(409,'…')`・`fail=(status=400,error='…')` の既定）。
WORKER_PATTERNS = (
    re.compile(r"\berror\s*:\s*['\"]([a-z][a-z0-9_]*)['\"]"),
    re.compile(r"\bfail\(\s*\d+\s*,\s*['\"]([a-z][a-z0-9_]*)['\"]"),
    re.compile(r"\berror\s*=\s*['\"]([a-z][a-z0-9_]*)['\"]"),
)
# 目録の符丁の形（2 語以上）。1 語の符丁（`forbidden` など）は目録に載らない。
CODE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+\Z")
WORKER_HEAD = (
    "// 生成物: tools/render_refusals.py が断りの目録（thth/refusal_catalog*.py）の worker の口から書き出す。手で直さない。\n"
    "// 符丁 → {why: なぜ, fix: 通すには, better: もっと良く（無ければ null）}。relay.js の reply が断りの答えに足す。\n"
    "// 穴（<口座> などの型）は埋めない。文を直すときは目録の部を直して python3 tools/render_refusals.py を打つ。\n"
)


def worker_codes(src: Path = WORKER_SRC) -> dict[str, list[str]]:
    """Worker の source（生成物を除く）が `error` に書く符丁 → 出る file の名前。"""
    found: dict[str, set[str]] = {}
    for path in sorted(src.glob("*.js")):
        if path.name == WORKER_JS.name:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in WORKER_PATTERNS:
            for match in pattern.finditer(text):
                found.setdefault(match.group(1), set()).add(path.name)
    return {code: sorted(files) for code, files in sorted(found.items())}


def dashboard_table() -> str:
    """`const THTH_REASON={…};` の 1 行（鍵は JS の名前・値は JSON の文字列）。"""
    rows = []
    for code, why in refusal_catalog.dashboard_view().items():
        if not KEY.fullmatch(code):
            raise SystemExit(f"dashboard の符丁が JS の名前の形でない: {code}")
        rows.append(f"{code}:{json.dumps(why, ensure_ascii=False)}")
    return "const THTH_REASON={" + ",".join(rows) + "};"


def render_dashboard(text: str) -> str:
    found = TABLE.findall(text)
    if len(found) != 1:
        raise SystemExit(f"const THTH_REASON の表が 1 つでない（{len(found)} 個）")
    table = dashboard_table()
    return TABLE.sub(lambda _match: table, text)


def worker_js() -> str:
    body = json.dumps(refusal_catalog.door_view("worker"), ensure_ascii=False, indent=1)
    return WORKER_HEAD + "export const REFUSALS=Object.freeze(" + body + ");\n"


def outputs() -> dict[Path, str]:
    """生成物の path → あるべき中身。"""
    out = {path: render_dashboard(path.read_text(encoding="utf-8")) for path in DASHBOARDS}
    out[WORKER_JS] = worker_js()
    return out


def stale() -> list[Path]:
    """今の file が生成物と違う path（無い file も含む）。"""
    wrong = []
    for path, text in outputs().items():
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            current = None
        if current != text:
            wrong.append(path)
    return wrong


def main(argv) -> int:
    if "--check" in argv:
        wrong = stale()
        for path in wrong:
            print(f"{path.relative_to(ROOT)} が断りの目録と食い違っています（python3 tools/render_refusals.py で作り直す）",
                  file=sys.stderr)
        return 1 if wrong else 0
    for path, text in outputs().items():
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            path.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
