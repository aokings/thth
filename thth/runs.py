"""`state/<account>/runs-YYYY-MM.ndjson` への追記と読込（設計 §4.6）。

1 行の必須項目は 11 個ちょうど・同じ順序: account, run_id, mode(rehearsal|production),
action(post|skip|none), file, post_id, collected（**投稿の処理では測っていないので常に null**・2026-09-12）, refreshed(bool), quota(json|null),
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

from . import accounts as accounts_mod
from . import engagements as engagements_mod
from . import jst as jst_mod

RUNS_FIELDS = [
    "account", "run_id", "mode", "action", "file", "post_id",
    "collected", "refreshed", "quota", "status", "error",
]
# 必須ではない追加項目（欠けていても None として書く。上の docstring 参照）。
# `trigger`（引継ぎ 2026-09-15 §3-D）: **誰がその実行を始めたか**。
# `"manual"`＝人が `thth collect` を手で打った・`"run"`＝`thth run`（timer が
# 10 分ごとに呼ぶ形）の中から。**None は「名乗っていない」**——古い行・
# 道具の中から直に呼ばれた場合で、`"manual"` と読み替えてはいけない。
# `mismatch_fields`（外部レビュー第 3 巡・持ち越し項目 C）: error が
# `text_mismatch_before_writeback`・`text_mismatch_after_rebase` のとき、5 項目
# （body・account・reply_to・topic・publish_at）のうちどれが食い違ったか。
# それ以外の error では None（人がなぜ止まったかを探さずに済むように）。
OPTIONAL_FIELDS = ["topic", "mismatch_fields", "trigger"]


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


def record_minimal(account_name: str, line: dict, *, now=None) -> str:
    """`runs-YYYY-MM.ndjson` に、標準 11 項目（`RUNS_FIELDS`）ではない**最小の
    1 行**を足す（読むだけの口の共通口・設計「自分の泉」§2.1・§2.3）。

    `thread_read`（T1-2）が自前で持っていた `_record_run()` を、`where_cli`
    （T2-2）と共有するためにここへ括り出した——**同じ「禁止語を検査してから
    書く」網を 2 か所に置かない**（発注 T2-2「`_record_run` を共通化して
    使う」）。`line` は呼ぶ側が組んだ辞書をそのまま書く（例:
    `{"action": "thread_read", "account", "medium", "post_id", "messages",
    "truncated", "status", "error"}` や `{"action": "where_to_appear",
    "account", "words", "n", "status", "error"}`）——`RUNS_FIELDS` の
    11 項目とは別物なので `append_run()` は使わない。

    **禁止語（`engagements.FORBIDDEN_KEYS`）が 1 つでも混ざっていたら
    1 バイトも書かずに `RuntimeError`**——本文・username が runs に紛れ
    込まないことを機械的に守る（`engagements._assert_clean()` と同じ考え方）。
    """
    now = now if now is not None else jst_mod.now_jst()
    state_dir = accounts_mod.state_dir_for(account_name)
    os.makedirs(state_dir, exist_ok=True)
    path = path_for(state_dir, jst_mod.month_str(now))
    hit = sorted(engagements_mod.FORBIDDEN_KEYS & set(line.keys()))
    if hit:
        raise RuntimeError(f"runs に書けない鍵が含まれています（書きません）: {hit}")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
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
