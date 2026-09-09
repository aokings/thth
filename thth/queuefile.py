"""front-matter 解析・形式検査・媒体の節の抜き出し・文字数（設計 §4.1・受け入れ 1〜3）。"""
from __future__ import annotations

import dataclasses
import datetime
import re
import unicodedata

KNOWN_STATUSES = {"draft", "approved", "posted", "withdrawn"}
MEDIA_LIMITS = {"threads": 500, "x": 140}
_FM_DELIM = "---"
# `#語` のハッシュタグ検出。行頭または空白の後に `#` + 語構成文字が続く形。
_HASHTAG_RE = re.compile(r"(?:^|\s)#\w")


@dataclasses.dataclass
class QueueFile:
    """1 つの queue ファイル。`malformed` は「thth: が無い・front-matter が壊れている」
    （設計 §3.3 select 条件 2）で、これが立っていれば型外として扱う。"""

    path: str
    malformed: bool
    front_matter: dict
    body: str

    def get(self, key: str, default=None):
        return self.front_matter.get(key, default)


def _split_front_matter(text: str) -> tuple[str, str] | None:
    """先頭が `---\\n...\\n---\\n` の形かを見て `(front_matter_text, body)` を返す。
    形が合わなければ None（front-matter が無い・壊れている＝型外の一部）。"""
    if not text.startswith(_FM_DELIM):
        return None
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return None


def _parse_kv(fm_text: str) -> dict:
    out: dict = {}
    for line in fm_text.split("\n"):
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        out[key] = value if value else None
    return out


def parse(path: str) -> QueueFile:
    """queue ファイル 1 本を読む。front-matter が無い／`thth: 1` が無ければ malformed=True。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    split = _split_front_matter(text)
    if split is None:
        return QueueFile(path=path, malformed=True, front_matter={}, body=text)
    fm_text, body = split
    fm = _parse_kv(fm_text)
    malformed = fm.get("thth") != "1"
    return QueueFile(path=path, malformed=malformed, front_matter=fm, body=body)


def extract_section(body: str, media: str) -> str | None:
    """`## <media>` の節の本文を抜き出す。前後の空行を落とし、末尾改行 1 個にして返す。
    節が無ければ None。次の `## ` 見出し（同レベル）までがその節の範囲。"""
    lines = body.split("\n")
    heading = f"## {media}".lower()
    start = None
    for i, line in enumerate(lines):
        if line.strip().lower() == heading:
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    section_lines = lines[start:end]
    while section_lines and not section_lines[0].strip():
        section_lines.pop(0)
    while section_lines and not section_lines[-1].strip():
        section_lines.pop()
    if not section_lines:
        return None
    return "\n".join(section_lines) + "\n"


def _is_emoji(ch: str) -> bool:
    """絵文字らしさの判定（実務上の近似）。Unicode カテゴリ So（記号・その他）は
    大半の絵文字（ダイングバット・絵文字ピクトグラム）を含む。国旗（地域指示記号
    ペア U+1F1E6-1F1FF）は Symbol, Other 以外のカテゴリなので個別に足す。"""
    cp = ord(ch)
    if 0x1F1E6 <= cp <= 0x1F1FF:
        return True
    return unicodedata.category(ch) == "So"


def char_count(text: str) -> int:
    """Threads の文字数の数え方（設計 §2.2・受け入れ 3）: 500 字上限、絵文字は
    UTF-8 バイト数で数える。絵文字以外は Unicode コードポイント 1 個を 1 字とする。"""
    count = 0
    for ch in text:
        if _is_emoji(ch):
            count += len(ch.encode("utf-8"))
        else:
            count += 1
    return count


def has_hashtag(text: str) -> bool:
    return bool(_HASHTAG_RE.search(text))


def parse_publish_at(value: str) -> datetime.datetime:
    """`+09:00` 付きの ISO 8601 をパースする。壊れていれば ValueError。"""
    return datetime.datetime.fromisoformat(value)
