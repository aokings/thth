"""`state/<account>/runs-YYYY-MM.ndjson` への追記と読込（設計 §4.6）。

1 行の必須項目は 11 個ちょうど・同じ順序: account, run_id, mode(rehearsal|production),
action(post|skip|none), file, post_id, collected(n), refreshed(bool), quota(json|null),
status, error。

watchtower/watchtower/runs.py の流儀（1 行 1 実行・ndjson 追記のみ）を写したが、
フィールドは THTH 用（§4.6）に差し替えたので import はしない。

`topic`（T2c・設計 §4.1・masaru 裁定 2026-09-09）は付けたことを記録するための
追加項目。付けたトピックを Threads 側から読み返す field が無い（設計 §2.2）ので、
THTH 側の runs にだけ記録が残る。**既存の呼び出し元（record に `topic` を含めない
もの）を壊さないよう、必須項目には含めない**（無ければ None として書く）。
"""
from __future__ import annotations

import json
import os

RUNS_FIELDS = [
    "account", "run_id", "mode", "action", "file", "post_id",
    "collected", "refreshed", "quota", "status", "error",
]
# 必須ではない追加項目（欠けていても None として書く。上の docstring 参照）。
# `mismatch_fields`（外部レビュー第 3 巡・持ち越し項目 C）: error が
# `text_mismatch_before_writeback`・`text_mismatch_after_rebase` のとき、5 項目
# （body・account・reply_to・topic・publish_at）のうちどれが食い違ったか。
# それ以外の error では None（人がなぜ止まったかを探さずに済むように）。
OPTIONAL_FIELDS = ["topic", "mismatch_fields"]


def path_for(state_dir: str, jst_month: str) -> str:
    return os.path.join(state_dir, f"runs-{jst_month}.ndjson")


def append_run(state_dir: str, record: dict, jst_month: str) -> str:
    os.makedirs(state_dir, exist_ok=True)
    path = path_for(state_dir, jst_month)
    missing = [k for k in RUNS_FIELDS if k not in record]
    if missing:
        raise ValueError(f"runs レコードに項目が足りません: {missing}")
    line = {k: record.get(k) for k in RUNS_FIELDS}
    for k in OPTIONAL_FIELDS:
        line[k] = record.get(k)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return path


def read_runs(state_dir: str) -> list:
    out = []
    if not os.path.isdir(state_dir):
        return out
    for fname in sorted(os.listdir(state_dir)):
        if fname.startswith("runs-") and fname.endswith(".ndjson"):
            with open(os.path.join(state_dir, fname), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        out.append(json.loads(line))
    return out
