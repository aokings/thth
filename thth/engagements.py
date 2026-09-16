"""絡みの台帳（`data/sns/engagements/<YYYY-MM>.ndjson`・設計「自分の泉」§4）。

利用者横断の泉はやめた（裁定 2026-09-16）。残るのは**自分の泉**——自分の
アカウントが何をして何が返ってきたかの記録。ここに残すのは「絡みに行った
返信」（他人の投稿への `reply_to` 付きの投稿）を、どの枝の・誰への返信かと
結ぶ 1 行だけ。**残すのは自分の行為と反応だけ。相手の本文・username・自分の
判断は 1 バイトも書かない**（設計 §4）。

書くのは `thth/core.py` の公開が確定した直後——`sent_mod.write()` の直後
（`writeback.rewrite_front_matter()` より前）。原稿の front-matter に
`reply_to` が無ければ何も書かない。
"""
from __future__ import annotations

import json
import os
import re

from . import accounts as accounts_mod
from . import jst

# **行の形の版**（設計「自分の泉」§4）。形を変えたら上げる。
SCHEMA = "thth.engagement.v1"

# **禁止鍵**（`thth/share.py` の `FORBIDDEN_KEYS` と同じ顔ぶれ）。本文・
# 相手の名前・自分の判断・秘密・置き場の絶対パスは、この台帳のどこにも書かない。
#
# **`share.py` から import しない**（share は後で消える予定・発注 T0）。ここに
# 自前で持つ。`post_id`・`root_post`・`account` は share.py の一覧にはあるが、
# **絡みの台帳ではそれ自体が §4 の正規の鍵**なので、ここでは禁止語から外して
# ある——さもないと `_assert_clean()` は台帳の正しい 1 行すら常に拒む。
FORBIDDEN_KEYS = frozenset({
    "text", "body", "content", "message", "draft", "preview",
    "username", "handle", "author", "by",
    "verdict", "decision", "note", "reason",
    "access_token", "token", "refresh_token", "secret", "app_secret",
    "repo_dir", "queue_dir", "replies_dir", "env", "path",
    "accounts", "user_id",
    "replied_to", "permalink", "url",
})

# `author_key`（`thth/adapters/base.py:author_key()`）の形。16 進 16 桁。
AUTHOR_KEY_RE = re.compile(r"^[0-9a-f]{16}$")

# front-matter `found_by` に許す 3 値（設計「自分の泉」§4）。
FOUND_BY_VALUES = frozenset({"where_to_appear", "manual", "mention"})


class EngagementError(Exception):
    """禁止鍵・形の違う `author_key` を含む行を書こうとした（**書かない**）。"""


def _assert_clean(row: dict) -> None:
    """書く前の検査。禁止鍵が無いこと・`author_key` が 16hex か `None` であること。

    **落ちたら書かない**——呼び出し側（`append()`）はこれを通ってから初めて
    ディスクに触る。
    """
    if not isinstance(row, dict):
        raise EngagementError(f"行が object ではありません: {row!r}")
    hit = sorted(FORBIDDEN_KEYS & set(row.keys()))
    if hit:
        raise EngagementError(
            f"絡みの台帳に書けない鍵が含まれています（書きません）: {hit}")
    author_key = row.get("author_key")
    if author_key is not None and not AUTHOR_KEY_RE.match(str(author_key)):
        raise EngagementError(
            f"author_key は 16 進 16 桁か null でなければなりません: {author_key!r}")


def _dir(account_cfg: dict, account_name: str) -> str:
    return accounts_mod.data_dirs(account_cfg, account_name)["engagements"]


def append(account_cfg: dict, account_name: str, row: dict, *, now=None) -> str:
    """絡みの台帳に 1 行足す。**書く前に `_assert_clean()` を通す。**

    `row` に `recorded_at` が無ければ `now`（既定 `jst.now_jst()`）で補う。
    ファイルは `<YYYY-MM>.ndjson`（`posted_at` の JST 年月。読めなければ
    `now` の年月）。返り値は書いたファイルのパス。

    禁止鍵・形の違う `author_key` は `EngagementError`——**その場合ファイルは
    1 本も作らない・書かない**。
    """
    now = now if now is not None else jst.now_jst()
    full_row = dict(row)
    full_row.setdefault("recorded_at", jst.iso(now))
    _assert_clean(full_row)
    posted_dt = jst.parse(full_row.get("posted_at")) or now
    month = jst.month_str(posted_dt)
    d = _dir(account_cfg, account_name)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{month}.ndjson")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(full_row, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def _read_ndjson(path: str) -> tuple[list, bool]:
    """1 本の ndjson を読む。`(行の配列, 壊れているか)`。

    `thth/measured.py:_read_ndjson()` と同じ流儀: **壊れと不存在を混ぜない**。
    ファイルが無ければ「まだ書いていないだけ」——`broken=False` で空を返す。
    JSON として読めない行が 1 行でもあれば、そのファイルは信用できないので
    `broken=True` にして中身は 1 行も使わない。
    """
    if not os.path.exists(path):
        return [], False
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return [], True
    rows: list = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            return [], True
    return rows, False


def load(account_cfg: dict, account_name: str) -> dict:
    """絡みの台帳を全部読む。**読むだけ**。

    戻り値: `{"rows": [...], "file_count": N, "broken": N}`。壊れたファイルは
    数えて `broken` に分け、中身は使わない（`measured._read_ndjson()` と同じ
    流儀。壊れと不存在を混ぜない）。
    """
    d = _dir(account_cfg, account_name)
    if not os.path.isdir(d):
        return {"rows": [], "file_count": 0, "broken": 0}
    rows: list = []
    file_count = 0
    broken = 0
    for name in sorted(os.listdir(d)):
        if not name.endswith(".ndjson"):
            continue
        file_count += 1
        file_rows, is_broken = _read_ndjson(os.path.join(d, name))
        if is_broken:
            broken += 1
            continue
        rows.extend(file_rows)
    return {"rows": rows, "file_count": file_count, "broken": broken}


def records(account_cfg: dict, account_name: str) -> list:
    """絡みの台帳の行だけを並べる（`load()` の便利口）。壊れたファイルは除く。"""
    return load(account_cfg, account_name)["rows"]


def author_summary(author_keys, rows: list) -> list:
    """`author_key` ごとの `met`（絡みの台帳の行数）・`last`（最新の
    `posted_at`）（設計「自分の泉」§2.1・§2.3・T1-2 の `thread_read._you_and_them`
    から T2-2 で共通化）。`thread_read`（枝の中の `you_and_them`）と
    `where_cli`（account ごとの `you_and_them`）の両方がここを呼ぶ——
    **同じ計算を 2 か所に置かない**（発注 T2-2「共通の関数に括り出してよい」）。

    **発言内容は持たない**（設計「自分の泉」§2.4 と同じ規律）——`rows` は
    絡みの台帳の行（`load()["rows"]`）で、本文・username を含まない。
    """
    out = []
    for key in sorted(author_keys):
        matched = [r for r in rows if r.get("author_key") == key]
        met = len(matched)
        stamps = sorted((r.get("posted_at") for r in matched
                         if isinstance(r.get("posted_at"), str)), reverse=True)
        out.append({"author_key": key, "met": met, "last": stamps[0] if stamps else None})
    return out
