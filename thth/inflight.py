"""`state/<account>/inflight.json`（設計 §3.5）。

投稿は取り消せない。公開の**前**に書き、post_id を md に書き戻して push が成功して
**初めて**消す（push 失敗では消さない）。次の実行の冒頭でこれが残っていれば、
そのアカウントは何もしないで exit 1 にする（select.select_one を呼ばない）。
"""
from __future__ import annotations

import json
import os


def path_for(state_dir: str) -> str:
    return os.path.join(state_dir, "inflight.json")


def read(state_dir: str) -> dict | None:
    p = path_for(state_dir)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _write(state_dir: str, data: dict) -> None:
    if data.get('media'):
        from . import media_delivery, accounts
        name=data['media']['account']
        if os.path.realpath(accounts.state_dir_for(name))!=os.path.realpath(state_dir):raise ValueError('media_journal_account_mismatch')
        return media_delivery._save(name,data)
    os.makedirs(state_dir, exist_ok=True)
    p = path_for(state_dir)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)


def write(state_dir: str, *, file: str, started: str,
          container_id: str | None = None, post_id: str | None = None,
          body_hash: str | None = None, approved_fingerprint: str | None = None) -> None:
    """`body_hash`（外部レビュー §3・受け入れ 9・10）は「送るはずの本文」の
    `approval.compute_body_hash()`。公開の**前**にここへ書いておくことで、書き戻し
    直前の「送った本文と repo の本文が同じか」の照合ができる（`thth.core` 参照）。

    `approved_fingerprint`（外部レビュー再々レビュー P1・1）は
    `approval.compute_approved_sha()` と同じ 5 項目（本文・account・reply_to・
    topic・publish_at）の指紋。`body_hash` は本文だけしか見ないため、公開中に
    別 clone から account や topic だけを書き換えられても検知できない
    （本文の hash は変わらないため）。書き戻し前・rebase 後の照合はこちらを使う
    （`thth.core._fingerprint_matches()` 参照）。`body_hash` は既存の記録
    （`tests/test_sent_integrity.py`）との後方互換のため残す。
    """
    _write(state_dir, {
        "file": file,
        "started": started,
        "container_id": container_id,
        "post_id": post_id,
        "body_hash": body_hash,
        "approved_fingerprint": approved_fingerprint,
    })


def update(state_dir: str, **fields) -> None:
    data = read(state_dir) or {}
    data.update(fields)
    _write(state_dir, data)


def clear(state_dir: str) -> None:
    p = path_for(state_dir)
    if os.path.exists(p):
        os.remove(p)
