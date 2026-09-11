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


def is_warning(message: str) -> bool:
    """`lint_file()` が返す 1 行が警告（落とさない）か実エラー（落とす）かを見分ける。"""
    return message.startswith("warning:")


def _bundle_segments(b, media: str):
    from . import bundle as bundle_mod
    return bundle_mod.load_segments(b, media)


def _bundle_or_none(path: str):
    """`thth: 2` なら束として読む。**v1 の経路には触らない。**"""
    from . import bundle as bundle_mod
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None, None
    if not bundle_mod.is_bundle_text(text):
        return None, None
    return bundle_mod.parse_text(text, path), text


def lint_file(path: str) -> list:
    """形式検査。駄目な理由を 1 行ずつ返す（空リストなら OK）。

    `account` の台帳が引けるときは、その媒体・hashtags 設定で媒体節と文字数・
    ハッシュタグまで検査する。台帳が引けないとき（アカウント未指定・未知）は
    threads 既定（500 字・ハッシュタグ不可）で検査する。

    450 字を超えたら警告を返り値に含めるが、**落とさない**（食い違い 2 の裁定・
    2026-09-09）。警告は `warning:` で始まる文字列として errors と同じリストに
    混ぜて返す（`thth lint` の exit code は `error:`/その他の実エラーだけで決まる。
    `is_warning()` で区別できる）。
    """
    # **スレッド連投は束として検査する**（`thth: 1` が無いとだけ言わない）。
    b, _text = _bundle_or_none(path)
    if b is not None:
        from . import bundle as bundle_mod
        return bundle_mod.check(
            b, account_cfg=_account_cfg_or_none(b.front_matter.get("account")))

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
        else:
            warn_limit = queuefile.WARN_LIMITS.get(media)
            if warn_limit is not None and n > warn_limit:
                errors.append(
                    f"warning: length: {media} は {warn_limit} 字を超えています"
                    f"（{n} 字・上限 {limit} 字。絵文字の数え方は実物と一致する保証が無い）"
                )
        hashtags_allowed = bool(account_cfg.get("hashtags", True)) if account_cfg else False
        if not hashtags_allowed and queuefile.has_hashtag(section):
            errors.append("hashtag: ハッシュタグは付けない規約（`#` を含む）")

    # トピック（`topic_tag`）。省略・空は許す（設計 §4.1・masaru 裁定 2026-09-09）。
    topic = queuefile.normalize_topic(fm.get("topic"))
    if topic is not None:
        topic_err = queuefile.topic_error(topic)
        if topic_err is not None:
            errors.append(topic_err)

    return errors


def preview_file(path: str) -> str:
    """実際に投げる本文そのもの（媒体の節だけ。前後に何も足さない）。

    **スレッド連投は段ごとに分けて出す。** 区切り行まで本文として見せると、
    **それが投稿されるように読める**（実際には送らない）。
    """
    b, _text = _bundle_or_none(path)
    if b is not None:
        cfg = _account_cfg_or_none(b.front_matter.get("account"))
        media = cfg["media"] if cfg else "threads"
        segments, problems = _bundle_segments(b, media)
        if problems:
            return "（段に割れません）" + "／".join(problems)
        out = []
        total = len(segments)
        for i, seg in enumerate(segments, start=1):
            rel = "返信先なし（先頭）" if i == 1 else f"{i - 1} 段目への返信"
            out.append(f"── {i}/{total}　{rel}")
            out.append(seg)
            out.append("")
        return "\n".join(out).rstrip() + "\n"

    qf = queuefile.parse(path)
    account_name = qf.front_matter.get("account")
    account_cfg = _account_cfg_or_none(account_name)
    media = account_cfg["media"] if account_cfg else "threads"
    section = queuefile.extract_section(qf.body, media)
    if section is None:
        raise ValueError(f"media section が無い（`## {media}`）: {path}")
    return section
