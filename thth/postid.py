"""`post_id` をファイル名にする（媒体で `post_id` の形が違う・T3・2026-09-13）。

**見つかり方**: Bluesky を配線して端から端を通したら、`state/<account>/sent/` の
書き込みが `FileNotFoundError` で落ちた。Bluesky の `post_id` は AT URI
（`at://did:plc:…/app.bsky.feed.post/<rkey>`）で、**`/` と `:` を含む**。
`sent.path_for()` も `collect` の台帳のパスも `f"{post_id}.ndjson"` と素朴に
繋いでいたので、途中の `/` がディレクトリの区切りとして効いてしまう。
`collect._safe_post_id()` は区切りを含む `post_id` を弾いていたので、
**Bluesky の投稿は 1 本も採取されない**（弾いたことは errors に出るが、
「そういう媒体だ」という理由は誰も知らない）。

**直し方**: パスに使うときだけ percent-encoding を通す（`quote(safe="")`）。

- **Threads の既存のファイル名は 1 文字も変わらない。** Threads の `post_id` は
  数字だけで、`quote()` が触らない文字（`A-Za-z0-9_.-~`）に収まっている。
  追記専用の台帳（`data/sns/insights/posts/*.ndjson`・`data/sns/replies/*.ndjson`）を
  改名せずに済むのが、この綴りを選んだ理由。
- **戻せる。** ファイル名から `post_id` を復元する側（`measured`・
  `account_report`）は `unquote()` を通す。数字だけの古い名前は素通りする。

**ここで弾くのは「ファイル名にできない値」だけ**で、媒体ごとの `post_id` の形は
知らない（`at://` も数字も同じ扱い）。
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse

# 制御文字（`\x00`〜`\x1f`・`\x7f`）。**`post_id` の判定はここが正本**
# （監査 2 回目・P3-9）。前は `writeback.has_control_chars()` と
# `postid.is_usable()`（NUL だけ）の 2 つがあり、**呼び出し側が両方を並べて
# 書いたところだけが守られていた**——`collect._safe_post_id()` と
# `sent.write()` は `is_usable()` しか通らないので、**タブや改行を含む
# `post_id` がファイル名・台帳の鍵になれた。** 判定は 1 か所に置き、
# 通る口を全部そこへ寄せる。
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


# ファイル名 1 つぶんの上限（encode **後**のバイト数）。ext4・APFS・HFS+ は
# いずれも 255 バイト。`.ndjson`（7 バイト）や `.tmp` を足しても収まるように
# 200 で切る。**`quote()` は 1 文字を最大 12 バイトに膨らませる**（4 バイトの
# 絵文字 → `%F0%9F%98%80`）ので、encode 前の長さでは判定できない。
MAX_FILENAME_BYTES = 200


def is_usable(post_id) -> bool:
    """`post_id` を台帳の鍵として使えるか。**外へ出る値・書けない値を弾く。**

    区切り文字（`/`）はもう弾かない——**encode すれば安全に書ける**し、
    弾くと Bluesky の投稿が丸ごと採取から落ちる。残っているのは
    「空」「`.`／`..`」「**制御文字**」と、**encode 後のファイル名が長すぎるもの**。

    **制御文字は NUL だけではない**（監査 2 回目・P3-9）。改行は front-matter の
    別の行になり、タブ・復帰は台帳や画面の 1 行を割る。ここを通る値は
    ファイル名にも front-matter の値にもなるので、**まとめて弾く**。

    長さを見るのは独立監査 1（P3-6・2026-09-13）。極端に長い `post_id`
    （壊れた台帳・悪意のある返信先・別の媒体の URI を丸ごと入れた原稿）で
    `open()` が `OSError: [Errno 63] File name too long` を上げ、**`collect`
    が 1 本の異常で全部落ちていた**——採取は部分的な成功を許す設計なのに、
    ここだけ例外が `collect_once()` の外まで抜けていた。**書けないものは
    `errors` に積んで次へ**（`.` や `..` と同じ扱い）。
    """
    if not isinstance(post_id, str) or not post_id.strip():
        return False
    if post_id.strip() in (".", ".."):
        return False
    if CONTROL_RE.search(post_id):
        return False
    return len(to_filename(post_id).encode("utf-8")) <= MAX_FILENAME_BYTES


def to_filename(post_id: str) -> str:
    """`post_id` 1 つぶんのファイル名（拡張子を除く）。

    `safe=""` なので `/` も `:` も `%2F`・`%3A` になる。`~` は `quote()` が
    触らないが、ファイル名として問題は無い。
    """
    return urllib.parse.quote(post_id, safe="")


def from_filename(name: str) -> str:
    """ファイル名（拡張子を除いたもの）から `post_id` を戻す。

    古い（encode 前の）名前は素通りする——`unquote()` は `%` を含まない文字列を
    そのまま返す。
    """
    return urllib.parse.unquote(name)


class PostIdError(ValueError):
    """A user URL cannot be converted to a stable platform id."""


_THREADS_HOSTS = ('www.threads.com', 'threads.com', 'www.threads.net', 'threads.net')


def threads_shortcode(value):
    """`https://www.threads.com/@<user>/post/<符号>`（threads.net も）の符号。違う形は None。"""
    try:
        parsed = urllib.parse.urlsplit(value.strip())
    except (ValueError, AttributeError):
        return None
    parts = parsed.path.rstrip('/').split('/')
    if (parsed.scheme not in ('https', 'http') or parsed.netloc.lower() not in _THREADS_HOSTS
            or len(parts) != 4 or not parts[1].startswith('@') or parts[2] != 'post'
            or not re.fullmatch(r'[A-Za-z0-9_-]{5,64}', parts[3])):
        return None
    return parts[3]


def _threads_url_to_id(account_cfg, account_name, text):
    """Threads の投稿リンクを、台帳に残ったリンク（permalink）と突き合わせて id にする。

    Threads にはリンクの符号から id を引く API が無い。返信の台帳の行は permalink を
    持つので、採った返信ならリンクから id が分かる（要望 r20261007-4b57e1ee・3）。
    媒体には問い合わせない。"""
    if urllib.parse.urlsplit(text).path.startswith('/share/'):
        raise PostIdError('share_url_unsupported: 共有リンク（/share/…）は、開いたあとのアドレス'
                          '（https://www.threads.com/@<user>/post/<符号>）を渡してください')
    code = threads_shortcode(text)
    if code is None:
        raise PostIdError('invalid_post_url: https://www.threads.com/@<user>/post/<符号> を指定してください')
    from . import accounts
    directory = accounts.data_dirs(account_cfg, account_name)['replies']
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        names = []
    for name in names:
        if not name.endswith('.ndjson'):
            continue
        try:
            lines = open(os.path.join(directory, name), encoding='utf-8').read().splitlines()
        except OSError:
            continue
        for line in lines:
            if code not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if (isinstance(row, dict) and isinstance(row.get('permalink'), str)
                    and threads_shortcode(row['permalink']) == code and is_usable(row.get('id'))):
                return row['id']
    raise PostIdError('post_url_not_in_ledger: このリンクの投稿は返信の台帳にありません。'
                      '`thth replies <account> --refresh` で採ってから渡すか、id を渡してください')


def for_account(account_cfg, value, account_name=None):
    """Bluesky と Threads の web URL を id にする。資格情報は読まない。"""
    if account_cfg.get('media') not in ('bluesky', 'threads') or not isinstance(value, str):
        return value
    text=value.strip()
    if not text.startswith(('https://', 'http://')):
        return value
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
        raise PostIdError('invalid_post_url: URL に制御文字は使えません')
    if account_cfg.get('media') == 'threads':
        return _threads_url_to_id(account_cfg, account_name, text)
    from .adapters import bluesky
    from . import leave_gate
    with leave_gate.scope(account_cfg), leave_gate.lease():
        return bluesky.normalize_post_url(text)
