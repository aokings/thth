"""記事（`article_source`）からはてなブログへ送る本文への変換（設計 4.0 S1 §6.2）。

**S1 の変換は仮のもの。** S1 で決めるのは「変換の関数が 1 つで、同じ入力から同じ出力」
だけで、脚注・表・埋め込み・相対リンクの書き換えは S2 が変換の表で決める。S1 の
`render()` は §6.2 の正規化（改行・各行の末尾の空白・末尾の空白行）と、依存する画像の
一覧（`![alt](<相対パス>)` の元の形のまま・中身は `source_sha256` で固定）だけを行う。

**S2 はこの関数を差し替える。** 呼び手（`hatena_entry`）はここの `render()` しか呼ばない
——承認の 1 段目・2 段目・timer の照合・公開の直前が、同じ 1 つの関数を通る。
"""
from __future__ import annotations

import hashlib
import os
import posixpath
import re

from . import approval

# 版の語（変換の中身が変われば上げる。承認の指紋には入らない——本文の sha256 が入るので、
# 変換が変われば承認は自然に古くなる＝`approval_stale`）。
VERSION = "s1-normalize-only"

# `![alt](path)` と `![alt](path "title")`。http(s) の画像（他所に置いた画像）は依存に数えない。
_IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"\n]*\")?\s*\)")


class ConvertError(ValueError):
    """変換できない（符丁は `str(exc)`）。"""


def image_paths(source_text: str, *, article_source: str) -> list:
    """本文が参照する手元の画像の、repo の中の相対パス（出てくる順・重複なし）。

    `article_source` からの相対パスを repo の相対パスに直す。repo の外を指すもの・
    絶対パス・`.` 始まりの部分を含むものは `image_path_invalid` で断る（推測で拾わない）。
    """
    base = posixpath.dirname(article_source.replace(os.sep, "/"))
    out = []
    for match in _IMAGE_RE.finditer(source_text or ""):
        raw = match.group(1)
        if "://" in raw or raw.startswith(("data:", "//")):
            continue
        if raw.startswith("/") or "\\" in raw:
            raise ConvertError("image_path_invalid")
        path = posixpath.normpath(posixpath.join(base, raw)) if base else posixpath.normpath(raw)
        parts = path.split("/")
        if path.startswith("../") or path == ".." or any(p.startswith(".") for p in parts):
            raise ConvertError("image_path_invalid")
        if path not in out:
            out.append(path)
    return out


def render(source_text: str, *, article_source: str, image_sha=None, base_url=None) -> tuple:
    """`(本文, 画像の一覧)` を返す。決定的（同じ入力から同じ出力）。

    - 本文: `approval.normalize_hatena_body()`（§6.2）。画像は `![alt](<path>)` の元の形のまま
      （fotolife の記法への置き換えは公開の直前・S3）。`base_url` は S2 が相対リンクの書き換えに
      使う（S1 では使わない）。
    - 画像: `[{"path", "source_sha256", "bytes"}]`（path の順）。`image_sha(path) -> (sha256, bytes)`
      が中身を読む（読めなければ呼び手が `image_missing` などで断る）。
    """
    del base_url  # S2 の変換の表で使う
    if not isinstance(source_text, str):
        raise ConvertError("article_source_unreadable")
    body = approval.normalize_hatena_body(source_text)
    images = []
    for path in image_paths(body, article_source=article_source):
        if image_sha is None:
            raise ConvertError("image_missing")
        sha, size = image_sha(path)
        images.append({"path": path, "source_sha256": sha, "bytes": size})
    images.sort(key=lambda row: row["path"])
    return body, images


def file_sha256(path: str) -> tuple:
    """`(sha256, bytes)`。読めなければ `OSError`。"""
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size
