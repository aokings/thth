"""`state/<account>/sent/<post_id>.json`（外部レビュー §3・受け入れ 9・10）。

公開に**成功した直後**、書き戻し（front-matter の書き換え）より前に書く。**これが
正本**（VM 側）: 「実際に送った本文そのもの」と、そのハッシュ、送った時刻を動かせない
記録として残す。書き戻しの直前に、いま repo にある本文のハッシュをこれと突き合わせ、
食い違えば front-matter を書き換えない（`thth.core._throw_chosen()` 参照）。
"""
from __future__ import annotations

import datetime
import json
import os

from . import jst
from . import postid as postid_mod


def dir_for(state_dir: str) -> str:
    return os.path.join(state_dir, "sent")


def path_for(state_dir: str, post_id: str) -> str:
    """**`post_id` をそのままパスにしない**（`thth/postid.py`・T3 2026-09-13）。

    Bluesky の `post_id` は AT URI で `/` を含む。Threads の数字だけの
    `post_id` は encode しても 1 文字も変わらないので、既存のファイルは動かない。
    """
    return os.path.join(dir_for(state_dir),
                        f"{postid_mod.to_filename(post_id)}.json")


def write(state_dir: str, *, post_id: str, text: str, body_hash: str, sent_at: str,
          approved_fingerprint: str | None = None) -> str:
    """送った本文そのものを動かせない記録として保存する。返り値は書いたパス。

    `approved_fingerprint`（外部レビュー再々レビュー P1・1）は公開直前に固定した
    5 項目（本文・account・reply_to・topic・publish_at）の指紋。`body_hash` は
    後方互換のため残す（本文だけの hash・`tests/test_sent_integrity.py` が参照）。
    """
    d = dir_for(state_dir)
    os.makedirs(d, exist_ok=True)
    p = path_for(state_dir, post_id)
    tmp = p + ".tmp"
    data = {"post_id": post_id, "text": text, "body_hash": body_hash, "sent_at": sent_at,
            "approved_fingerprint": approved_fingerprint}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    return p


def read(state_dir: str, post_id: str) -> dict | None:
    p = path_for(state_dir, post_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def records(state_dir: str) -> list:
    """`sent/` に残っている記録を全部読む（**壊れた 1 本で全部を落とさない**）。

    **なぜ要るか**（2026-09-13 の本番）。`thth send`（同席の様態）で出した 1 本が
    `board` の `last_post` にも `posts` の突合にも現れなかった。どちらも
    **queue の front-matter しか見ていなかった**からで、同席の様態には書き戻す
    front-matter が無い（`core._send_locked()` の註）。**出した事実がここにしか
    無い**以上、読み手もここを見なければ「出していない」と言ってしまう。

    読めなかったファイル・辞書でないもの・`post_id` を復元できないものは飛ばす
    （読むだけの口が 1 本の壊れた記録で止まらないように）。`post_id` が中身に
    無い記録はファイル名から復元する（`postid.from_filename()`）。
    """
    d = dir_for(state_dir)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = []
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(d, name), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        post_id = data.get("post_id") or postid_mod.from_filename(name[: -len(".json")])
        if not post_id:
            continue
        data["post_id"] = post_id
        out.append(data)
    return out


def post_ids(state_dir: str) -> set:
    """`sent/` に記録のある `post_id` の集合（＝**THTH を通して出したもの**）。"""
    return {row["post_id"] for row in records(state_dir)}


def _parse_sent_at(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    if value.endswith("Z"):          # 媒体が UTC の Z 表記で返してきた場合
        value = value[:-1] + "+00:00"
    try:
        return jst.to_jst(datetime.datetime.fromisoformat(value))
    except ValueError:
        return None


def latest_sent(state_dir: str):
    """`sent/` の中で**いちばん新しい記録**を `(JST の時刻, 記録)` で返す。

    時刻を読めない記録は比較に混ぜない（**判らないものを「最新」と言わない**）。
    1 件も無ければ `(None, None)`。
    """
    best_at = None
    best_row = None
    for row in records(state_dir):
        at = _parse_sent_at(row.get("sent_at"))
        if at is None:
            continue
        if best_at is None or at > best_at:
            best_at, best_row = at, row
    return best_at, best_row
