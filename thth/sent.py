"""`state/<account>/sent/<post_id>.json`（外部レビュー §3・受け入れ 9・10）。

公開に**成功した直後**、書き戻し（front-matter の書き換え）より前に書く。**これが
正本**（VM 側）: 「実際に送った本文そのもの」と、そのハッシュ、送った時刻を動かせない
記録として残す。書き戻しの直前に、いま repo にある本文のハッシュをこれと突き合わせ、
食い違えば front-matter を書き換えない（`thth.core._throw_chosen()` 参照）。
"""
from __future__ import annotations

import json
import os

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
