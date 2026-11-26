#!/usr/bin/env python3
"""対応表 thth.workspaces/1 の形を確かめる。置く前と置いたあとに回す。

  python3 tools/workspaces_check.py <file>     # 形の誤りを全部出す。rc 0 = 正しい
  python3 tools/workspaces_check.py -          # 標準入力から（ssh wt 'cat …' | で VM の物を見る）

依存は標準の部品と thth/workspaces_map.py だけ（VM の古い版の上でも、この 1 本を写せば回る）。
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from thth import workspaces_map as wm  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    raw = sys.stdin.read() if argv[1] == "-" else pathlib.Path(argv[1]).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"NG: JSON でない（{exc}）")
        return 1
    errs = wm.validate(data)
    if errs:
        for e in errs:
            print(f"NG: {e}")
        return 1
    m = wm.parse(data)
    by_ws: dict[str, list[str]] = {}
    for medium, ws in sorted(m.media.items()):
        by_ws.setdefault(ws, []).append(medium)
    print(f"OK: {wm.SCHEMA}・括り {len(m.workspaces)}・媒体 {len(m.media)}・表に無い名前 → {m.unlisted}")
    for ws in m.workspaces:
        print(f"  {ws}（持ち主 {m.owner_of(ws)}・窓口 {m.lead_of(ws)}）: {len(by_ws.get(ws, []))} 媒体")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
