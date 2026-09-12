"""トピックの台帳は同時に書いても消えない（独立検収 A・2026-09-12）。

`record()` は **load → append → 全文書き直し**なので、**間に別の実行が書くと
その行が消えていた。** 台帳は「**追記のみ**——後の確認が前の確認を上書きせず、
履歴として残る」と謳っているのに。

運用セッションと開発セッションが同じ VM で同時に `thth topics --note` を打つのは
現実の経路。**実プロセスで確かめたら、8 本中 1 本しか残らなかった。**
"""
from __future__ import annotations

import json
import os
import subprocess
import sys


コード = """
import sys
from thth import topics
try:
    topics.record(sys.argv[1], verdict="alive", kind="行動", by=sys.argv[1],
                   account="a")
    print("ok")
except Exception as e:
    print("だめ", type(e).__name__)
"""


def test_同時に8本書いても全部残る(thth_root):
    env = {**os.environ, "THTH_ROOT": thth_root,
            "PYTHONPATH": os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}
    procs = [subprocess.Popen([sys.executable, "-c", コード, f"語{i}"], env=env,
                               stdout=subprocess.PIPE, text=True) for i in range(8)]
    出た = [p.communicate()[0].strip() for p in procs]

    path = os.path.join(thth_root, "state", "topics.json")
    with open(path, encoding="utf-8") as f:
        残った = sorted(r["topic"] for r in json.load(f)["checks"])

    assert 残った == [f"語{i}" for i in range(8)], \
        f"**追記が消えている**: {残った}／各プロセスの結果 {出た}"
