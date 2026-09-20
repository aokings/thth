"""`state/<account>/runs-YYYY-MM.ndjson` への追記と読込（設計 §4.6）。

1 行に**必ず出る**項目は 11 個・同じ順序: account, run_id, mode(rehearsal|
production|read), action(post|skip|none|where_to_appear|thread_read|…),
file, post_id, collected（**投稿の処理では測っていないので常に null**・
2026-09-12）, refreshed(bool), quota(json|null), status, error。

watchtower/watchtower/runs.py の流儀（1 行 1 実行・ndjson 追記のみ）を写したが、
フィールドは THTH 用（§4.6）に差し替えたので import はしない。

`topic`（T2c・設計 §4.1・masaru 裁定 2026-09-09）は付けたことを記録するための
追加項目。付けたトピックを Threads 側から読み返す field が無い（設計 §2.2）ので、
THTH 側の runs にだけ記録が残る。**既存の呼び出し元（record に `topic` を含めない
もの）を壊さないよう、必須項目には含めない**（無ければ None として書く）。

T5-3（発注書「runs の読み取り行を `append_run()` の形に揃える」）: 投稿の実行
（`core.py`・`collect.py`・`maintain.py`）は今までどおり 11 項目を渡してくる
ことが多いが、**呼ぶ側が必ず渡すべきなのは `REQUIRED_FIELDS` の 6 個だけ**
（account・run_id・mode・action・status・error）。残り 5 個
（file・post_id・collected・refreshed・quota）は渡さなければ `None` で書く
——出力の行にはこれまでどおり 11 個とも必ず出る（`RUNS_FIELDS` はそのための
「出力の形」であり続ける）。読み取りの行（`where`・`thread`・`who` 等）は
`mode: "read"`・`run_id: "<action>-<ISO>"` で書く。`record_minimal()` は
無ければこの 2 つを補ってから `append_run()` を呼ぶ薄い口になった。
"""
from __future__ import annotations

import json
import os

from . import api_diagnostic
from . import accounts as accounts_mod
from . import engagements as engagements_mod
from . import jst as jst_mod

# **出力の行に必ず出る 11 個**（順序も含めて既存の形のまま・読む側の互換）。
RUNS_FIELDS = [
    "account", "run_id", "mode", "action", "file", "post_id",
    "collected", "refreshed", "quota", "status", "error",
]
# **呼ぶ側が必ず渡すべき最小限**（T5-3）。残り 5 個（file・post_id・
# collected・refreshed・quota）は `RUNS_FIELDS` のうち書くのは必須ではない
# ——無ければ `None` として書く（投稿の実行はこれまでどおり全部渡してくる）。
REQUIRED_FIELDS = ["account", "run_id", "mode", "action", "status", "error"]
# 必須ではない追加項目（欠けていても None として書く。上の docstring 参照）。
# `trigger`（引継ぎ 2026-09-15 §3-D）: **誰がその実行を始めたか**。
# `"manual"`＝人が `thth collect` を手で打った・`"run"`＝`thth run`（timer が
# 10 分ごとに呼ぶ形）の中から。**None は「名乗っていない」**——古い行・
# 道具の中から直に呼ばれた場合で、`"manual"` と読み替えてはいけない。
# `mismatch_fields`（外部レビュー第 3 巡・持ち越し項目 C）: error が
# `text_mismatch_before_writeback`・`text_mismatch_after_rebase` のとき、5 項目
# （body・account・reply_to・topic・publish_at）のうちどれが食い違ったか。
# それ以外の error では None（人がなぜ止まったかを探さずに済むように）。
# `messages`・`truncated`（`thread_read`）・`words`・`n`（`where_to_appear`）・
# `medium`・`author_key`・`met`・`profile_fetched`（`who_is_this`）は T5-3 で
# 足した——読み取りの行（`record_minimal()` 経由）が運ぶ call 固有の数・鍵。
# `engagement_write_failed`（発注 T0-1）・`engagement_author_lookup_failed`
# （T7-2）: **`append_run()` が持っているキーだけを出力に残す allowlist が
# ここ**（`RUNS_FIELDS`＋`OPTIONAL_FIELDS`）なので、`core._append_run()` が
# `record` に積んでも、ここに無ければディスクに 1 バイトも出ない。T7-2 実装中に
# 発覚（`engagement_write_failed` は発注 T0-1 で `core.py` が積んでいたが、
# ここに列挙されておらず、実際には毎回黙って捨てられていた——「絡みの台帳が
# 書けなかったら runs に loud に残す」という規約が実際には効いていなかった）。
OPTIONAL_FIELDS = ["topic", "mismatch_fields", "trigger",
                   "messages", "truncated", "words", "n",
                   "medium", "author_key", "met", "profile_fetched",
                   "engagement_write_failed", "engagement_author_lookup_failed"]


def path_for(state_dir: str, jst_month: str) -> str:
    return os.path.join(state_dir, f"runs-{jst_month}.ndjson")


def append_run(state_dir: str, record: dict, jst_month: str) -> str:
    from pathlib import Path
    from . import leave_gate
    name=record.get('account')
    if accounts_mod.name_is_safe(name) and Path(state_dir).absolute()==Path(accounts_mod.state_dir_for(name)).absolute():
        try:
            with leave_gate.lease(name):return _append_run(state_dir,record,jst_month)
        except accounts_mod.AccountStopped:
            # A late read/maintenance completion must not recreate deleted state.
            return path_for(state_dir,jst_month)
    return _append_run(state_dir,record,jst_month)


def _append_run(state_dir: str, record: dict, jst_month: str) -> str:
    """`record` から 1 行を組んで追記する。**必須は `REQUIRED_FIELDS` の
    6 個だけ**（T5-3）——`RUNS_FIELDS` の残り 5 個・`OPTIONAL_FIELDS` は
    渡さなければ `None` として書く（出力の行の形は変わらない）。
    """
    os.makedirs(state_dir, exist_ok=True)
    path = path_for(state_dir, jst_month)
    missing = [k for k in REQUIRED_FIELDS if k not in record]
    if missing:
        raise ValueError(f"runs レコードに項目が足りません: {missing}")
    line = {k: record.get(k) for k in RUNS_FIELDS}
    for k in OPTIONAL_FIELDS:
        line[k] = record.get(k)
    if record.get("api_diagnostic"):
        line["api_diagnostic"] = api_diagnostic.clean(record["api_diagnostic"])
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return path


def record_minimal(account_name: str, line: dict, *, now=None) -> str:
    """`runs-YYYY-MM.ndjson` に、**読み取りの口の共通口**が使う 1 行を足す
    （設計「自分の泉」§2.1・§2.3）。

    `thread_read`（T1-2）が自前で持っていた `_record_run()` を、`where_cli`
    （T2-2）・`who_cli`（T3-3）と共有するためにここへ括り出した——**同じ
    「禁止語を検査してから書く」網を複数か所に置かない**（発注 T2-2
    「`_record_run` を共通化して使う」）。`line` は呼ぶ側が組んだ辞書
    （例: `{"action": "thread_read", "account", "medium", "post_id",
    "messages", "truncated", "status", "error"}`）——`mode`・`run_id` を
    渡さなくてよい。**無ければここで補う**（T5-3）: `mode` は `"read"`、
    `run_id` は `"<action>-<ISO 時刻>"`。そのあとは `append_run()` を呼ぶ
    薄い口——書式（`RUNS_FIELDS`・`OPTIONAL_FIELDS`）は共通。

    **禁止語（`engagements.FORBIDDEN_KEYS`）が 1 つでも混ざっていたら
    1 バイトも書かずに `RuntimeError`**——本文・username が runs に紛れ
    込まないことを機械的に守る（`engagements._assert_clean()` と同じ考え方・
    この網は T5-3 でも変えていない）。
    """
    now = now if now is not None else jst_mod.now_jst()
    hit = sorted(engagements_mod.FORBIDDEN_KEYS & set(line.keys()))
    if hit:
        raise RuntimeError(f"runs に書けない鍵が含まれています（書きません）: {hit}")
    record = dict(line)
    record.setdefault("mode", "read")
    if "run_id" not in record:
        record["run_id"] = f"{record.get('action')}-{jst_mod.iso(now)}"
    state_dir = accounts_mod.state_dir_for(account_name)
    return append_run(state_dir, record, jst_mod.month_str(now))


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
