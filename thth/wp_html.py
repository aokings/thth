"""記事の本文（Markdown）から WordPress へ送る HTML への変換（5.16.0・設計 ショップとWordPressの口 §4）。

WordPress の REST（`/wp-json/wp/v2/posts`）の `content` は HTML を受ける。依存 0 の決まり（pyproject）なので
標準ライブラリだけで、**狭い部分集合**だけを HTML にする:

- 段落（空行で区切る。段落の中の改行は `<br />`）
- 見出し（`#`〜`######`）
- 強調（`**太字**`・`*斜体*`）
- リンク（`[文字](https://…)`。`http`・`https`・`mailto`・相対・`#` だけ）
- 箇条書き（`-`・`*`・`+`）と番号付き（`1.`）。入れ子は作らない
- コード（```` ``` ```` の塊と `` ` `` の文字）

**外れる記法は送らずに前向きに断る**（`WpMarkupError`・符丁は `wp_markup_unsupported` など。どの記法か
を `detail` に添える）——黙って文字のまま送ると、読者には崩れた記事が出る。生の HTML・画像・引用・表・
区切り線・脚注・参照のリンク・入れ子の箇条書きは、この版では断る。

同じ入力から同じ出力（決定的）。**承認の指紋には変換したあとの HTML の sha256 が入る**
（`approval.compute_wp_entry_sha`）——規則を変えたら `VERSION` を上げ、リリースノートに
「WordPress の承認済みの原稿は再承認」と 1 行書く。
"""
from __future__ import annotations

import html
import re

VERSION = "wp-html-1"

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([A-Za-z0-9_+-]{0,32})[ \t]*$")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_UL_RE = re.compile(r"^[-*+][ \t]+(.*)$")
_OL_RE = re.compile(r"^(\d{1,9})\.[ \t]+(.*)$")
_INDENTED_ITEM_RE = re.compile(r"^[ \t]+(?:[-*+]|\d{1,9}\.)[ \t]+")
_CODE_SPAN_RE = re.compile(r"(`+)(.+?)(?<!`)\1(?!`)")
_LINK_RE = re.compile(r"\[([^\[\]\n]+)\]\(\s*([^()\s<>]+)\s*\)")
_STRONG_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_EM_RE = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_SAFE_SCHEMES = ("http:", "https:", "mailto:")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
# 部分集合の外の記法（行の頭で見るもの）。
_BLOCK_REFUSALS = (
    (re.compile(r"^ {0,3}>"), "引用（>）"),
    (re.compile(r"^ {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$"), "区切り線（---）"),
    (re.compile(r"^ {0,3}\|.*\|[ \t]*$"), "表（|）"),
    (re.compile(r"^ {0,3}\[\^[^\]]+\]:"), "脚注"),
    (re.compile(r"^ {0,3}\[[^\]]+\]:[ \t]*\S"), "参照のリンクの定義"),
    (re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$"), "下線の見出し（=== / ---）"),
)
_INLINE_REFUSALS = (
    (re.compile(r"!\["), "画像（![…](…)）"),
    (re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^>]*)?/?>"), "生の HTML"),
    (re.compile(r"\[\^[^\]]+\]"), "脚注"),
    (re.compile(r"\]\[[^\]]*\]"), "参照のリンク"),
)
# 置き換え済みの断片の目印（本文に現れない私用の字で囲む）。
_MARK_OPEN, _MARK_CLOSE = "", ""


class WpMarkupError(ValueError):
    """部分集合の外の記法（`code` は静的な符丁・`detail` はどの記法か）。"""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _safe_url(url: str) -> bool:
    if _SCHEME_RE.match(url):
        return url.lower().startswith(_SAFE_SCHEMES)
    return True   # 相対・`#`・`/`


def _inline(text: str) -> str:
    pieces: list = []

    def keep(fragment: str) -> str:
        pieces.append(fragment)
        return f"{_MARK_OPEN}{len(pieces) - 1}{_MARK_CLOSE}"

    text = _CODE_SPAN_RE.sub(lambda m: keep(f"<code>{html.escape(m.group(2).strip(), quote=False)}</code>"), text)
    for pattern, label in _INLINE_REFUSALS:
        if pattern.search(text):
            raise WpMarkupError("wp_markup_unsupported", label)

    def link(m):
        label, url = m.group(1), m.group(2)
        if not _safe_url(url):
            raise WpMarkupError("wp_link_scheme_unsupported", url.split(":", 1)[0][:20])
        return keep(f'<a href="{html.escape(url)}">{_inline(label)}</a>')

    text = _LINK_RE.sub(link, text)
    if "](" in text:   # 形の崩れたリンク（行き先に括弧や空白がある）は字のまま送らずに断る
        raise WpMarkupError("wp_markup_unsupported", "リンクの形（行き先に括弧か空白）")
    text = html.escape(text, quote=False)
    text = _STRONG_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = _EM_RE.sub(lambda m: f"<em>{m.group(1)}</em>", text)
    while _MARK_OPEN in text:
        text = re.sub(f"{_MARK_OPEN}(\\d+){_MARK_CLOSE}", lambda m: pieces[int(m.group(1))], text)
    return text


def render(body: str) -> str:
    """Markdown（`hatena_convert.render` のあとの本文）から HTML。外れる記法は `WpMarkupError`。"""
    lines = (body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list = []
    para: list = []
    i = 0

    def flush():
        if para:
            out.append("<p>" + "<br />\n".join(_inline(line.strip()) for line in para) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        fence = _FENCE_RE.match(line)
        if fence:
            flush()
            mark, lang = fence.group(1), fence.group(2)
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(mark):
                code.append(lines[i])
                i += 1
            if i >= len(lines):
                raise WpMarkupError("wp_markup_unsupported", "閉じていないコードの塊")
            i += 1
            cls = f' class="language-{lang}"' if lang else ""
            out.append(f"<pre><code{cls}>" + html.escape("\n".join(code), quote=False) + "</code></pre>")
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1
            continue
        for pattern, label in _BLOCK_REFUSALS:
            if pattern.match(line):
                raise WpMarkupError("wp_markup_unsupported", label)
        if _INDENTED_ITEM_RE.match(line):
            raise WpMarkupError("wp_markup_unsupported", "入れ子の箇条書き")
        matched = False
        for pattern, tag in ((_UL_RE, "ul"), (_OL_RE, "ol")):
            if pattern.match(line):
                flush()
                items = []
                while i < len(lines) and pattern.match(lines[i]):
                    m = pattern.match(lines[i])
                    items.append(m.group(m.lastindex))
                    i += 1
                if i < len(lines) and _INDENTED_ITEM_RE.match(lines[i]):
                    raise WpMarkupError("wp_markup_unsupported", "入れ子の箇条書き")
                out.append(f"<{tag}>" + "".join(f"<li>{_inline(item.strip())}</li>" for item in items) + f"</{tag}>")
                matched = True
                break
        if matched:
            continue
        para.append(line)
        i += 1
    flush()
    return "\n".join(out)
