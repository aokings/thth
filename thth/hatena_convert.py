"""記事（`article_source`）からはてなブログへ送る本文への変換（設計 4.0 S2 §4）。

**変換は規則の表 `RULES` を上から順に通す 1 つの決定的な関数**（同じ入力から同じ出力）。
呼び手（`hatena_entry`）はここの `render()` しか呼ばない——lint・承認の 1 段目・2 段目・
timer の照合・公開の直前が、同じ 1 つの関数を通る。

表の 3 列目（実機で確かめたか）は文書のための印で、コードの動きを変えない。`unverified` は
「通しているが、はてなの Markdown で通るかは見ていない」（設計 §12 の 6 で確かめる）。

**規則を変えたら `VERSION` を上げる。** 本文の出力が変われば本文の sha256（承認の指紋の
材料）が変わるので、承認済みの原稿は `approval_stale` になる（指紋の形そのものは変えない）。
規則を変える版のリリースノートには「はてなの承認済みの原稿は再承認」と 1 行書く。
"""
from __future__ import annotations

import hashlib
import os
import posixpath
import re
import urllib.parse

from . import approval

# 版の語（変換の中身が変われば上げる。承認の指紋には入らない——本文の sha256 が入るので、
# 変換が変われば承認は自然に古くなる＝`approval_stale`）。
VERSION = "s2-rules-1"

# S2 にはフォトライフが無かったので手元の画像を参照する記事を断っていた（設計 S2 §4.3）。
# S3（フォトライフ）で外した。手元の画像は承認のあと・公開の直前にフォトライフへ上げる
# （`hatena_fotolife`）。
REFUSE_LOCAL_IMAGES = False

# 送る直前の置き換えの形（設計 S3 §5.2）。`fotolife_plain` はフォトライフ記法
# `[f:id:<user>:<ID'>:plain]`、`markdown_url` は `![alt](<hatena:imageurl>)`。実機の §15 の 16 で
# フォトライフ記法が描かれなければ `markdown_url` に切り替える。
IMAGE_FORMS = ("fotolife_plain", "markdown_url")
IMAGE_FORM = "fotolife_plain"

# 画像の形式（先頭のバイト列で決める・拡張子は見ない・設計 S3 §9）。WebP・HEIC・SVG は入れない。
IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

# `![alt](path)` と `![alt](path "title")`。http(s) の画像（他所に置いた画像）は依存に数えない。
_IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"\n]*\")?\s*\)")
# `[text](target)`・`[text](<target>)`・`[text](target "title")`。画像（`![`）は除く。
# text は角括弧を含まない（画像を 1 つ入れた `[![x](i.png)](rel)` だけは受ける）。
_LINK_RE = re.compile(
    r"(?<!!)\[((?:[^\[\]\n]|!\[[^\[\]\n]*\]\([^)\n]*\))*)\]"
    r"\((\s*)(<[^>\n]*>|[^)\s]+)((?:\s+\"[^\"\n]*\")?\s*)\)")
# 参照の定義 `[id]: target`（脚注の定義 `[^1]: …` は除く）。
_REFDEF_RE = re.compile(r"^( {0,3}\[(?!\^)[^\]\n]+\]:[ \t]*)(<[^>\n]*>|\S+)(.*)$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CODE_SPAN_RE = re.compile(r"(`+)(?:.|\n)*?(?<!`)\1(?!`)")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_ATX_H1_RE = re.compile(r"^#[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
# S2 で変えずに断る形（監査 低-6）: インライン HTML の `<a href="…">` と、山括弧のリンク `<…>`。
_A_HREF_RE = re.compile(r"<a\b[^>]*?\bhref\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))", re.IGNORECASE)
_ANGLE_RE = re.compile(r"<([^<>\s]+)>")
# 属性の無い HTML のタグ（`<br>`・`</p>`・`<hr/>`）は山括弧のリンクに数えない。
_BARE_TAG_RE = re.compile(r"^/?[A-Za-z][A-Za-z0-9-]*/?$")


class ConvertError(ValueError):
    """変換できない（符丁は `str(exc)`）。"""


def image_paths(source_text: str, *, article_source: str) -> list:
    """本文が参照する手元の画像の、repo の中の相対パス（出てくる順・重複なし）。

    `article_source` からの相対パスを repo の相対パスに直す。repo の外を指すもの・
    絶対パス・`.` 始まりの部分を含むものは `image_path_invalid` で断る（推測で拾わない）。
    """
    base = posixpath.dirname(article_source.replace(os.sep, "/"))
    out = []

    def collect(segment):
        for match in _IMAGE_RE.finditer(segment):
            path = _image_repo_path(match.group(1), base)
            if path is not None and path not in out:
                out.append(path)
        return segment

    _map_outside_code(source_text or "", collect)
    return out


def image_content_type(head: bytes) -> str | None:
    """先頭のバイト列から画像の `Content-Type`（JPEG・PNG・GIF だけ。ほかは None）。"""
    for signature, content_type in IMAGE_SIGNATURES:
        if isinstance(head, (bytes, bytearray)) and bytes(head).startswith(signature):
            return content_type
    return None


def _image_repo_path(raw: str, base: str) -> str | None:
    """`![alt](raw)` の raw を repo の相対パスに。他所の画像なら None。外れれば `image_path_invalid`。"""
    if "://" in raw or raw.startswith(("data:", "//")):
        return None
    if raw.startswith("/") or "\\" in raw:
        raise ConvertError("image_path_invalid")
    path = posixpath.normpath(posixpath.join(base, raw)) if base else posixpath.normpath(raw)
    parts = path.split("/")
    if path.startswith("../") or path == ".." or any(p.startswith(".") for p in parts):
        raise ConvertError("image_path_invalid")
    return path


def _map_outside_code(text: str, fn) -> str:
    """コードの囲み（``` と ~~~）とコードの span（`…`）の外の部分だけに `fn(segment) -> segment` を効かせる。

    区切り方は `_absolute_links` と同じ（S3 §5.2: 画像の数え方と置き換えの範囲を揃える）。
    """
    out, fence = [], None
    for line in text.split("\n"):
        opened = _FENCE_RE.match(line)
        if fence is not None:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence) \
                    and not line.strip().strip(fence[0]):
                fence = None
            out.append(line)
            continue
        if opened:
            fence = opened.group(1)
            out.append(line)
            continue
        pieces, last = [], 0
        for span in _CODE_SPAN_RE.finditer(line):
            pieces.append(fn(line[last:span.start()]))
            pieces.append(span.group(0))
            last = span.end()
        pieces.append(fn(line[last:]))
        out.append("".join(pieces))
    return "\n".join(out)


def embed_images(body: str, placements: dict, *, article_source: str, form: str | None = None,
                 hatena_id: str | None = None) -> str:
    """送る直前の置き換え（設計 S3 §5.2）。`RULES` の外——承認の指紋の材料（`render()` の出力）は変えない。

    `placements` は `{repo の相対パス: {"syntax", "image_url", …}}`。数える範囲は `image_paths()` と
    同じ（コードの囲みと span の中は触らない・他所の画像は触らない）。決定的。

    - `fotolife_plain`（既定）: `[f:id:<user>:<ID'>:plain]`。`<user>:<ID'>` は台帳の `syntax` の
      3・4 番目の欄をそのまま使う（推測の形式の字を足さない）。alt は落ちる（`:alt=` は未確認）。
    - `markdown_url`: `![alt](<image_url>)`。
    配置の無い手元の画像があれば `ConvertError("image_placement_missing")`。
    """
    form = form or IMAGE_FORM
    if form not in IMAGE_FORMS:
        raise ConvertError("image_form_invalid")
    base = posixpath.dirname(article_source.replace(os.sep, "/"))

    def replace(match):
        path = _image_repo_path(match.group(1), base)
        if path is None:
            return match.group(0)
        place = (placements or {}).get(path)
        if not isinstance(place, dict):
            raise ConvertError("image_placement_missing")
        if form == "markdown_url":
            url = place.get("image_url")
            if not isinstance(url, str) or not url or any(c in url for c in "<>\n "):
                raise ConvertError("image_placement_missing")
            alt = re.match(r"!\[([^\]\n]*)\]", match.group(0)).group(1)
            return f"![{alt}](<{url}>)"
        parts = str(place.get("syntax") or "").split(":")
        if len(parts) < 4 or parts[:2] != ["f", "id"] or not parts[2] or not parts[3]:
            raise ConvertError("image_placement_missing")
        if hatena_id is not None and parts[2] != hatena_id:
            raise ConvertError("image_placement_missing")
        return f"[f:id:{parts[2]}:{parts[3]}:plain]"

    return _map_outside_code(body or "", lambda segment: _IMAGE_RE.sub(replace, segment))


# ------------------------------------------------------------------ 規則（text, ctx) -> text

def _strip_front_matter(text, ctx):
    """記事の先頭の `---` … `---`（か `...`）を落とす。閉じが無ければ front matter ではない。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].rstrip() != "---":
        return text
    for i in range(1, len(lines)):
        if lines[i].rstrip() in ("---", "..."):
            rest = lines[i + 1:]
            while rest and not rest[0].strip():
                rest = rest[1:]
            return "\n".join(rest)
    return text


def _strip_leading_h1(text, ctx):
    """先頭の `# 題` が原稿の title と同じなら落とす（題は `<title>` で別に送る・二重に出さない）。"""
    title = (ctx.get("title") or "").strip()
    if not title:
        return text
    lines = text.split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i >= len(lines):
        return text
    match = _ATX_H1_RE.match(lines[i])
    if match is None or match.group(1).strip() != title:
        return text
    rest = lines[i + 1:]   # 前の空行（lines[:i]）も一緒に落とす
    while rest and not rest[0].strip():
        rest = rest[1:]
    return "\n".join(rest)


def _is_relative(target) -> bool:
    return bool(target) and not (target.startswith("#") or target.startswith("//") or _SCHEME_RE.match(target))


def _refuse_unconverted_relatives(segment):
    """`<a href="相対">` と `<相対>` は S2 では変えないので、`link_base` があっても断る（監査 低-6）。

    `[text](<相対>)` は先に `_LINK_RE` が絶対にしているので、ここには絶対の形で来る。
    メールの自動リンク（`<a@b.example>`）・属性の無いタグ（`<br>`・`</p>`）・`/` も `.` も無いもの
    （`<注意>`）は相対に数えない。
    """
    for match in _A_HREF_RE.finditer(segment):
        href = next((g for g in match.groups() if g is not None), "")
        if _is_relative(href.strip()):
            raise ConvertError("relative_link_unresolvable")
    for match in _ANGLE_RE.finditer(segment):
        inner = match.group(1)
        if (inner.startswith(("!", "?")) or _BARE_TAG_RE.match(inner) or ("@" in inner and "/" not in inner)
                or not ("/" in inner or "." in inner)):
            continue   # 注記の山括弧（`<注意>`）などパスに見えないものは数えない
        if _is_relative(inner):
            raise ConvertError("relative_link_unresolvable")


def _resolve_link(target, ctx):
    """1 つのリンク先を絶対にする（相対でなければそのまま）。`link_base` が無ければ断る。"""
    if not _is_relative(target):
        return target
    base = ctx.get("link_base")
    if not base:
        raise ConvertError("relative_link_unresolvable")
    base = base if base.endswith("/") else base + "/"
    if target.startswith("/"):
        return urllib.parse.urljoin(base, target)
    folder = posixpath.dirname((ctx.get("article_source") or "").replace(os.sep, "/"))
    return urllib.parse.urljoin(base + (folder + "/" if folder else ""), target)


def _rewrite_target(raw, ctx):
    if raw.startswith("<") and raw.endswith(">"):
        return "<" + _resolve_link(raw[1:-1], ctx) + ">"
    return _resolve_link(raw, ctx)


def _links_in_segment(segment, ctx):
    def inline(match):
        target = _rewrite_target(match.group(3), ctx)
        return f"[{match.group(1)}]({match.group(2)}{target}{match.group(4)})"
    out = _LINK_RE.sub(inline, segment)
    _refuse_unconverted_relatives(out)
    return out


def _absolute_links(text, ctx):
    """`[text](相対)` と参照の定義 `[id]: 相対` を `link_base` で絶対にする（設計 §4.2）。

    `#` だけのリンク・scheme のあるもの（`https:`・`mailto:` など）・`//` 始まりはそのまま。
    コードの囲み（``` と ~~~）とコードの span（`…`）の中は触らない。画像は触らない（§4.3）。
    インライン HTML の `<a href="相対">` と `<相対>` は変えずに `relative_link_unresolvable` で断る
    （S2・監査 低-6。断るだけなので通る本文の出力は変わらない＝`VERSION` は据え置き）。
    """
    out, fence = [], None
    for line in text.split("\n"):
        opened = _FENCE_RE.match(line)
        if fence is not None:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence) \
                    and not line.strip().strip(fence[0]):
                fence = None
            out.append(line)
            continue
        if opened:
            fence = opened.group(1)
            out.append(line)
            continue
        ref = _REFDEF_RE.match(line)
        if ref:
            out.append(ref.group(1) + _rewrite_target(ref.group(2), ctx) + ref.group(3))
            continue
        pieces, last = [], 0
        for span in _CODE_SPAN_RE.finditer(line):
            pieces.append(_links_in_segment(line[last:span.start()], ctx))
            pieces.append(span.group(0))
            last = span.end()
        pieces.append(_links_in_segment(line[last:], ctx))
        out.append("".join(pieces))
    return "\n".join(out)


def _images(text, ctx):
    """手元の画像の一覧を作る（`![alt](<path>)` は元の形のまま・コードの中は数えない）。

    本文は変えない（フォトライフの参照への置き換えは送る直前の `embed_images`・S3 §5.2）。
    `REFUSE_LOCAL_IMAGES` は S2 の断りの名残（S3 から False・試験で S2 の形を作るときだけ真にする）。
    """
    paths = image_paths(text, article_source=ctx["article_source"])
    if paths and REFUSE_LOCAL_IMAGES:
        raise ConvertError("images_unsupported_until_s3")
    ctx["image_paths"] = paths
    return text


def _passthrough(text, ctx):
    return text


def _normalize(text, ctx):
    return approval.normalize_hatena_body(text)


RULES = (
    # (名前, 本文に効く関数, 実機で確かめたか)
    ("strip_front_matter", _strip_front_matter, "n/a"),       # 記事の先頭の --- … --- を落とす
    ("strip_leading_h1", _strip_leading_h1, "n/a"),           # 先頭の `# 題` が title と同じなら落とす
    ("absolute_links", _absolute_links, "n/a"),               # [text](相対) → link_base で絶対に（§4.2）
    ("images", _images, "n/a"),                               # 手元の画像を数える（本文は変えない・S3 §5.2）
    ("footnotes", _passthrough, "unverified"),                # [^1] をそのまま通す
    ("tables", _passthrough, "unverified"),                   # GFM の表をそのまま通す
    ("headings", _passthrough, "unverified"),                 # ## 以下をそのまま
    ("code_fences", _passthrough, "unverified"),              # ``` をそのまま
    ("embeds", _passthrough, "unverified"),                   # URL だけの行をそのまま（[URL:embed] に変えない）
    ("normalize", _normalize, "n/a"),                         # S1 §6.2（最後）
)


def render(source_text: str, *, article_source: str, image_sha=None, base_url=None, title=None) -> tuple:
    """`(本文, 画像の一覧)` を返す。決定的（同じ入力から同じ出力）。

    - 本文: `RULES` を上から順に通したもの。`base_url` は口座の `link_base`（無い・雛形の
      ダミーなら None を渡す。相対リンクがあれば `relative_link_unresolvable`）。`title` は
      `strip_leading_h1` が比べる題。
    - 画像: `[{"path", "source_sha256", "bytes"}]`（path の順）。`image_sha(path) -> (sha256, bytes)`
      が中身を読む（読めなければ呼び手が `image_missing` などで断る）。形式と容量の検査は
      呼び手（`hatena_entry._render`・S3 §9）。
    """
    if not isinstance(source_text, str):
        raise ConvertError("article_source_unreadable")
    ctx = {"article_source": article_source, "link_base": base_url, "title": title, "image_paths": []}
    body = source_text
    for _name, rule, _checked in RULES:
        body = rule(body, ctx)
    images = []
    for path in ctx["image_paths"]:
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
