"""`state/<account>/inflight.json`（設計 §3.5）。

投稿は取り消せない。公開の**前**に書き、post_id を md に書き戻して push が成功して
**初めて**消す（push 失敗では消さない）。次の実行の冒頭でこれが残っていれば、
そのアカウントは何もしないで exit 1 にする（select.select_one を呼ばない）。
"""
from __future__ import annotations

import json
import os


# 「確かめてから消す」を 1 行で（設計 §3.5・`docs/原稿_添付_2.13.md`・
# `docs/使い方_プロジェクトのセッション向け_2026-09-09.md`）。**静的な文**で、
# アカウント名もパスも値も埋め込まない（`thth send` が stderr に出す）。
# inflight が残っているのは「出たか出ていないか分からない」ということなので、
# 消してよいかを決められるのは画面を見た人だけ。
NEXT_STEP = ("次の一歩: 前回の結果が分かっていません。媒体の画面で出たかどうかを確かめ、"
             "出ていれば記録してから、出ていなければ state/<アカウント>/inflight.json "
             "を消してからもう一度（確かめずに消さない）")


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
          body_hash: str | None = None, approved_fingerprint: str | None = None,
          text_fingerprint: str | None = None, reply_to: str | None = None) -> None:
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

    `text_fingerprint`（設計 3.3.1 §3）は `inflight_resolve.text_fingerprint()`
    ——媒体が改行や空白を詰め直しても同じ本文を同じと見る指紋。次の run が
    自分の最近の投稿と照合するときに使う。**本文そのものは書かない。**
    `reply_to` は公開に渡した返信先の post_id（解けたときの書き戻しに使う）。
    どちらも無ければ鍵ごと書かない（古い形の inflight と同じ顔のまま）。
    """
    data = {
        "file": file,
        "started": started,
        "container_id": container_id,
        "post_id": post_id,
        "body_hash": body_hash,
        "approved_fingerprint": approved_fingerprint,
    }
    if text_fingerprint is not None:
        data["text_fingerprint"] = text_fingerprint
    if reply_to is not None:
        data["reply_to"] = reply_to
    _write(state_dir, data)


def update(state_dir: str, **fields) -> None:
    data = read(state_dir) or {}
    data.update(fields)
    _write(state_dir, data)


def clear(state_dir: str) -> None:
    p = path_for(state_dir)
    if os.path.exists(p):
        os.remove(p)


# 解いた inflight の控え（設計 3.3.1 §4）。`inflight.resolved-<日時>.json`。
ARCHIVE_PREFIX = "inflight.resolved-"


def archive(state_dir: str, record: dict, *, resolved_at: str, resolved_by: str,
            resolution: str, post_id: str | None = None) -> str:
    """解く**前に**、いまの inflight を控えとして state に残す（返り値は書いたパス）。

    人がファイルを消していた操作（09-23）の代わりに、何を解いたかを後から
    辿れるようにする。中身は inflight そのもの（本文は入っていない・指紋だけ）
    と、誰がいつどう解いたか。同じ秒に 2 度解いても上書きしない。
    """
    os.makedirs(state_dir, exist_ok=True)
    stamp = "".join(ch for ch in resolved_at[:19] if ch.isdigit() or ch == "T")
    base = os.path.join(state_dir, f"{ARCHIVE_PREFIX}{stamp}")
    path = base + ".json"
    n = 1
    while os.path.exists(path):
        n += 1
        path = f"{base}-{n}.json"
    payload = {"inflight": record, "resolved_at": resolved_at, "resolved_by": resolved_by,
               "resolution": resolution, "post_id": post_id}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def archives(state_dir: str) -> list:
    """控えのパス（古い順）。"""
    try:
        names = sorted(n for n in os.listdir(state_dir)
                       if n.startswith(ARCHIVE_PREFIX) and n.endswith(".json"))
    except FileNotFoundError:
        return []
    return [os.path.join(state_dir, n) for n in names]
