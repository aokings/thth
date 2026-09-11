"""返信の台帳を読む口（T4・masaru 指摘 2026-09-11）。

`thth/collect.py` は `data/sns/replies/<post_id>.ndjson` に返信を採っている
（`kind: "reply"` の行）。**ところが、それを読むコードがどこにも無かった。**
採っているだけで、誰も読んでいない（`grep -rn "_reply_rows\\|replies_dir" thth/`
で確認済み。書く側の `collect.py` 以外に出てこない）。この module がその読む口。

**読むだけ。何も書かない。**

外部の構想書（Codex §10）の条件——「自己返信・資料請求・具体的な質問を識別
できる場合は分ける。識別できなければ不明のまま残す」——のうち、まずここでは
**自己返信（身内の返信）だけを識別する**。`accounts/*.json` に登録された
**すべての account** の handle を集め、返信の `username` と突き合わせる。

**判らないものを「違う」にしない**（規約 12）。`username` が無い行は
`own: None`——「身内ではない」ではなく「判らない」。
"""
from __future__ import annotations

import json
import os

from . import accounts as accounts_mod


def _normalize_handle(value) -> str | None:
    """`@` の有無・大小文字の揺れを吸収する（handle は表記が揺れうる）。

    空・None は None を返す（「正規化した結果 空文字」を有効な handle として
    扱わない）。
    """
    if value is None:
        return None
    v = str(value).strip()
    if v.startswith("@"):
        v = v[1:].strip()
    return v.lower() or None


def _own_handles() -> set:
    """`accounts/*.json` の**すべての account** の handle を正規化して集める。

    1 つの account の台帳が壊れていても、他の account の handle は使いたいので、
    読めたものだけ集める（読めない account があるからといって、この読みの口
    全体を止めない）。
    """
    handles = set()
    for name in accounts_mod.list_account_names():
        try:
            cfg = accounts_mod.load_account(name)
        except accounts_mod.AccountError:
            continue
        h = _normalize_handle(cfg.get("handle"))
        if h:
            handles.add(h)
    return handles


def _classify_own(username, own_handles: set):
    """`username` を身内の handle と突き合わせる。

    - 一致すれば `True`
    - `username` があって一致しなければ `False`
    - **`username` が無ければ `None`**（判らない。「違う」と決めつけない）
    """
    norm = _normalize_handle(username)
    if norm is None:
        return None
    return norm in own_handles


def _read_replies_file(path: str) -> tuple:
    """1 本の `<post_id>.ndjson` を読む。`(返信の行, 取得記録の行, 壊れているか)`。

    **壊れと不存在を混ぜない**（この repo の規約・`thth/topic_store.py` と同じ
    流儀）。ファイルが無ければ「まだ採っていないだけ」——`broken=False` で空を
    返す。JSON として読めない行が 1 行でもあれば、そのファイルは信用できない
    ので `broken=True` にして**中身は 1 行も使わない**。壊れた行の手前までを
    こっそり混ぜると、「壊れて一部しか読めなかった」を「返信が n 件だけだった」
    と取り違える（規約「取れなかった指標は書かない」の裏返し）。
    """
    if not os.path.exists(path):
        return [], [], False
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return [], [], True

    replies, fetches = [], []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return [], [], True
        # `kind` を持たない古い行は返信として扱う（`collect.py` の `_reply_rows()`
        # と同じ扱い。2026-09-10 以前に書いたもの）。
        if row.get("kind") == "fetch":
            fetches.append(row)
        else:
            replies.append(row)
    return replies, fetches, False


def load(account_name: str, *, post_id: str | None = None) -> dict:
    """返信の台帳を読む。

    `post_id` を渡せばその投稿だけ、省略すれば `replies_dir` 配下の全 `*.ndjson`。

    戻り値:
      - `replies`: 返信の配列。元の行の項目に `own`（True/False/None）を足したもの
      - `fetches`: 取得の記録の配列（`kind: "fetch"` の行。返信 0 件の成功と
        取得の失敗を区別するために `collect.py` が残しているもの）
      - `broken`: 読めなかったファイルの名前（壊れと不存在は混ぜない）
      - `counts`: `{"replies": n, "own": n, "other": n, "unknown": n, "fetches": n}`
    """
    account_cfg = accounts_mod.load_account(account_name)
    repo_dir = account_cfg.get("repo_dir") or ""
    # `collect.py` の `collect_once()` と同じ組み立て方に揃える（`replies_dir`
    # は repo_dir からの相対パスという前提）。
    base = os.path.join(repo_dir, account_cfg.get("replies_dir") or "data/sns/replies")

    if post_id:
        names = [f"{post_id}.ndjson"]
    else:
        names = sorted(n for n in os.listdir(base) if n.endswith(".ndjson")) \
            if os.path.isdir(base) else []

    own_handles = _own_handles()

    all_replies, all_fetches, broken = [], [], []
    for name in names:
        rows, fetch_rows, is_broken = _read_replies_file(os.path.join(base, name))
        if is_broken:
            broken.append(name)
            continue
        for row in rows:
            all_replies.append({**row, "own": _classify_own(row.get("username"), own_handles)})
        all_fetches.extend(fetch_rows)

    counts = {
        "replies": len(all_replies),
        "own": sum(1 for r in all_replies if r["own"] is True),
        "other": sum(1 for r in all_replies if r["own"] is False),
        "unknown": sum(1 for r in all_replies if r["own"] is None),
        "fetches": len(all_fetches),
    }

    return {"replies": all_replies, "fetches": all_fetches, "broken": broken, "counts": counts}
