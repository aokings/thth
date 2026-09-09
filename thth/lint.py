"""`thth lint` / `thth preview`（発注 §3・受け入れ 1・2）。

CLI と MCP（`thth_lint`・`thth_preview`）の両方から呼ばれる純粋関数。判断はここに
書き、`bin/thth`・`mcp/server.py` はどちらもこれを呼ぶだけにする。
"""
from __future__ import annotations

from . import accounts as accounts_mod
from . import queuefile


def _account_cfg_or_none(account_name: str | None) -> dict | None:
    if not account_name:
        return None
    try:
        return accounts_mod.load_account(account_name)
    except accounts_mod.AccountError:
        return None


def lint_file(path: str) -> list:
    """形式検査。駄目な理由を 1 行ずつ返す（空リストなら OK）。

    `account` の台帳が引けるときは、その媒体・hashtags 設定で媒体節と文字数・
    ハッシュタグまで検査する。台帳が引けないとき（アカウント未指定・未知）は
    threads 既定（500 字・ハッシュタグ不可）で検査する。
    """
    qf = queuefile.parse(path)
    fm = qf.front_matter
    errors: list = []

    if fm.get("thth") != "1":
        errors.append("thth: front-matter に `thth: 1` が無い")

    account_name = fm.get("account")
    if not account_name:
        errors.append("account: account が無い")

    publish_at = fm.get("publish_at")
    if not publish_at:
        errors.append("publish_at: publish_at が無い")
    elif "+09:00" not in publish_at:
        errors.append("publish_at: +09:00 が無い")
    else:
        try:
            queuefile.parse_publish_at(publish_at)
        except ValueError:
            errors.append("publish_at: 形式が壊れている")

    status = fm.get("status")
    if status is not None and status not in queuefile.KNOWN_STATUSES:
        errors.append(f"status: 未知の値 ({status})")

    account_cfg = _account_cfg_or_none(account_name)
    media = account_cfg["media"] if account_cfg else "threads"
    section = queuefile.extract_section(qf.body, media)
    if section is None:
        errors.append(f"media: `## {media}` の節が無い")
    else:
        limit = queuefile.MEDIA_LIMITS.get(media, 500)
        n = queuefile.char_count(section)
        if n > limit:
            errors.append(f"length: {media} は {limit} 字以内（{n} 字）")
        hashtags_allowed = bool(account_cfg.get("hashtags", True)) if account_cfg else False
        if not hashtags_allowed and queuefile.has_hashtag(section):
            errors.append("hashtag: ハッシュタグは付けない規約（`#` を含む）")

    return errors


def preview_file(path: str) -> str:
    """実際に投げる本文そのもの（媒体の節だけ。前後に何も足さない）。"""
    qf = queuefile.parse(path)
    account_name = qf.front_matter.get("account")
    account_cfg = _account_cfg_or_none(account_name)
    media = account_cfg["media"] if account_cfg else "threads"
    section = queuefile.extract_section(qf.body, media)
    if section is None:
        raise ValueError(f"media section が無い（`## {media}`）: {path}")
    return section
