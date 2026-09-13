"""`thth share sync` は同時に打っても outbox を増やさない（監査 1・P2-4）。

`sync()` は **積み済みを読む → まだ無いものを積む** なので、**読みと書きの間に
他人が入ると全員が「まだ積んでいない」と判断する。** 鍵が無かったので、4 本
同時に打つと **outbox が 4 倍**になった（484 行・`row_id` は 121 種）。
**送る前の outbox に重複を溜める口**を残さない。

`tests/test_topics_record_concurrent.py` と同じ形（実プロセスを並べる——
同じプロセスの中の thread では、この手の穴は再現しない）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 観測を 30 語ぶん棚に置いてから、`share sync` を 4 本同時に打つ。
仕込み = """
import sys
from thth import topics
for i in range(30):
    topics.record(f"語{i}", verdict="alive", kind="行動", by="t", account="a")
print("ok")
"""

コード = """
import json, sys
from thth import share
try:
    print(json.dumps(share.sync(), ensure_ascii=False))
except Exception as e:
    print("だめ " + type(e).__name__ + ": " + str(e))
"""


def _env(thth_root: str) -> dict:
    return {**os.environ, "THTH_ROOT": thth_root, "PYTHONPATH": REPO_ROOT}


def _rows(thth_root: str) -> list:
    d = os.path.join(thth_root, "state", "share", "outbox")
    rows = []
    for name in sorted(os.listdir(d)):
        with open(os.path.join(d, name), encoding="utf-8") as f:
            rows += [json.loads(line) for line in f if line.strip()]
    return rows


def test_同時に4本syncしてもoutboxが増えない(thth_root):
    env = _env(thth_root)
    仕込んだ = subprocess.run([sys.executable, "-c", 仕込み], env=env,
                              capture_output=True, text=True)
    assert 仕込んだ.returncode == 0, 仕込んだ.stdout + 仕込んだ.stderr

    on = subprocess.run([sys.executable, "-c",
                         "from thth import share; share.set_enabled(True, by='t')"],
                        env=env, capture_output=True, text=True)
    assert on.returncode == 0, on.stdout + on.stderr

    procs = [subprocess.Popen([sys.executable, "-c", コード], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) for _ in range(4)]
    出た = [p.communicate() for p in procs]
    for out, err in 出た:
        assert "だめ" not in out, out + err

    rows = _rows(thth_root)
    row_ids = [r.get("row_id") for r in rows]
    assert row_ids, "1 行も積まれていない（この試験の前提が崩れている）"
    # **行の数 == row_id の種類の数。** 鍵が無いと 4 倍になる。
    assert len(row_ids) == len(set(row_ids)), (
        f"outbox に重複がある: {len(row_ids)} 行 / {len(set(row_ids))} 種"
        f"（同時に打つと積み直している）")


def test_2回目のsyncは何も足さない_冪等(thth_root):
    """鍵の有無と別に、**1 本ずつ打っても 2 回目は 0 件**（回帰）。"""
    env = _env(thth_root)
    subprocess.run([sys.executable, "-c", 仕込み], env=env, check=True,
                   capture_output=True, text=True)
    subprocess.run([sys.executable, "-c",
                    "from thth import share; share.set_enabled(True, by='t')"],
                   env=env, check=True, capture_output=True, text=True)

    一回目 = json.loads(subprocess.run([sys.executable, "-c", コード], env=env,
                                       capture_output=True, text=True).stdout)
    assert 一回目["added"] > 0, 一回目
    二回目 = json.loads(subprocess.run([sys.executable, "-c", コード], env=env,
                                       capture_output=True, text=True).stdout)
    assert 二回目["added"] == 0, 二回目
