"""記事の本文（Markdown）から WordPress へ送る HTML への変換（5.16.0・設計 ショップとWordPressの口 §4）。

WordPress の REST（`/wp-json/wp/v2/posts`）の `content` は HTML を受ける。依存 0 の決まり（pyproject）なので
標準ライブラリだけで、**狭い部分集合**だけを HTML にする:

- 段落（空行で区切る。段落の中の改行は `<br />`）
- 見出し（`#`〜`######`）。WordPress は記事の題を `h1` で出すので、本文の見出しは 1 段下げる（`#` は `h2`・
  `#####` と `######` は `h6`）。題と本文の `h1` が 2 つ並ぶと、読み上げと検索で記事の頭が分からなくなる
- 強調（`**太字**`・`*斜体*`）。斜体は日本語の字の隣でも効く（`**太字**と*斜体*`）。英数の語の中の `*`
  （`2*3*4`・`a*b*c`）は字のまま
- リンク（`[文字](https://…)`。`http`・`https`・`mailto`・相対・`#` だけ）。リンクの文字の中のコードも受ける
- 箇条書き（`-`・`*`・`+`）と番号付き（`1.`・`1)`）。入れ子は作らない
- コード（```` ``` ```` の塊と `` ` `` の文字）
- バックスラッシュの打ち消し（`\\*` は字の `*`。打ち消せるのは ASCII の記号だけ・ほかの `\\` は字のまま）

**外れる記法は送らずに前向きに断る**（`WpMarkupError`・符丁は `wp_markup_unsupported` など。どの記法か
を `detail` に添える）——黙って文字のまま送ると、読者には崩れた記事が出る。生の HTML・画像・引用・表・
区切り線・脚注・参照のリンク・入れ子の箇条書き・字下げのコード（4 つの空白かタブで始まる行）は、この版では断る。

WordPress の側で働く書き方の決め（5.16.0 監査 直したほうがよい 4）:

- **ショートコード**（`[gallery ids="1,2"]` など）: 送る HTML の `[` はすべて `&#91;` にする。読者には書いた
  とおりの `[` が見え、WordPress はショートコードとして働かせない（`<code>` の中でも働くので、コードも同じ）。
  この版はショートコードを使う道を持たない。
- **URL だけの行**: WordPress は段落に URL だけがあると埋め込み（oEmbed）に替える。黙って替わらないよう、
  URL だけの行はリンク（`<a href="URL">URL</a>`）にして送る（埋め込みにはならない）。
- **私用の字**（U+E000〜U+F8FF）: 本文にあれば断る（読者の画面では字にならない・アイコンの字が写したときに
  混じる）。この変換が内側の目印に使う字でもある。

**題**（`render_title`）: WordPress の REST は題も HTML として保存し、多くのテーマは題をそのまま HTML として
出す。題に生の HTML の型（`<b>`・`<img …>`・`vector<int>` のように `<` の後ろに英字が続き `>` で閉じるもの）
があれば `wp_markup_unsupported`（題の中の HTML）で断る。それ以外の `<`・`>`・`&` はエスケープして送る
（`a < b`・`A&B` は読者に書いたとおり見える。WordPress の編集の画面の題の欄には `&lt;`・`&amp;` の形で出る）。
送る題（エスケープしたあと）が承認の指紋に入る。

**リンクの行き先**（`_link_problem`）: 制御の字と空白（U+0000〜U+0020・U+007F〜U+009F）があれば断る
（ブラウザは頭の制御の字を落として読むので、`\\x01javascript:` が `javascript:` として働く）。相対と見なすのは
`/`・`#`・`.`・`?`・`%`・英数で始まり、最初の `/`・`?`・`#` より前に `:` が無いものだけ。

同じ入力から同じ出力（決定的）。**承認の指紋には変換したあとの HTML（と送る題）の sha256 が入る**
（`approval.compute_wp_entry_sha`）——規則を変えたら、まだ出していない版でなければ `VERSION` を上げ、
リリースノートに「WordPress の承認済みの原稿は再承認」と 1 行書く（変換したあとが変われば、古い承認は
指紋が合わずに通らない）。
"""
from __future__ import annotations

import html
import re

VERSION = "wp-html-1"

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([A-Za-z0-9_+-]{0,32})[ \t]*$")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_UL_RE = re.compile(r"^[-*+][ \t]+(.*)$")
_OL_RE = re.compile(r"^(\d{1,9})[.)][ \t]+(.*)$")
_INDENTED_ITEM_RE = re.compile(r"^[ \t]+(?:[-*+]|\d{1,9}[.)])[ \t]+")
_INDENTED_CODE_RE = re.compile(r"^(?: {4}|\t)[ \t]*\S")
_CODE_SPAN_RE = re.compile(r"(`+)(.+?)(?<!`)\1(?!`)")
_LINK_RE = re.compile(r"\[([^\[\]\n]+)\]\(\s*([^()\s<>]+)\s*\)")
_STRONG_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
# 斜体の前後で見るのは ASCII の英数だけ（日本語の字の隣でも効く・英数の語の中の `*` は字のまま）。
_EM_RE = re.compile(r"(?<![A-Za-z0-9_*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![A-Za-z0-9_*])")
# バックスラッシュで打ち消せる字（ASCII の記号）。
_ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")
_BARE_URL_RE = re.compile(r"https?://[^\s<>\"]+")
_SAFE_SCHEMES = ("http:", "https:", "mailto:")
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_CONTROL_RE = re.compile(r"[\x00-\x20\x7f-\x9f]")
# 私用の字（BMP の私用領域）。本文にあれば断る（内側の目印もこの中の 2 字）。
_PRIVATE_RE = re.compile("[\ue000-\uf8ff]")
# 部分集合の外の記法（行の頭で見るもの）。
_BLOCK_REFUSALS = (
    (re.compile(r"^ {0,3}>"), "引用（>）"),
    (re.compile(r"^ {0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$"), "区切り線（---）"),
    (re.compile(r"^ {0,3}\|.*\|[ \t]*$"), "表（|）"),
    (re.compile(r"^ {0,3}\[\^[^\]]+\]:"), "脚注"),
    (re.compile(r"^ {0,3}\[[^\]]+\]:[ \t]*\S"), "参照のリンクの定義"),
    (re.compile(r"^ {0,3}(?:=+|-+)[ \t]*$"), "下線の見出し（=== / ---）"),
)
_RAW_HTML_RE = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^>]*)?/?>")
_INLINE_REFUSALS = (
    (re.compile(r"!\["), "画像（![…](…)）"),
    (_RAW_HTML_RE, "生の HTML"),
    (re.compile(r"\[\^[^\]]+\]"), "脚注"),
    (re.compile(r"\]\[[^\]]*\]"), "参照のリンク"),
)
# 置き換え済みの断片の目印（私用の字で囲む。本文の私用の字は入口で断るので、本文とは混ざらない）。
_MARK_OPEN, _MARK_CLOSE = "\ue000", "\ue001"
_MARK_RE = re.compile(f"{_MARK_OPEN}(\\d+){_MARK_CLOSE}")


class WpMarkupError(ValueError):
    """部分集合の外の記法（`code` は静的な符丁・`detail` はどの記法か）。"""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def _link_problem(url: str) -> str | None:
    """リンクの行き先が送れない理由（送れれば None）。"""
    if _CONTROL_RE.search(url):
        return "行き先の制御の字か空白"
    if _SCHEME_RE.match(url):
        return None if url.lower().startswith(_SAFE_SCHEMES) else url.split(":", 1)[0][:20]
    head = url[0] if url else ""
    if not (head in "/#.?%" or head.isalnum()):
        return "相対の形"
    if ":" in re.split(r"[/?#]", url, maxsplit=1)[0]:
        return "相対の形"
    return None


def _private_char_error(text: str) -> WpMarkupError | None:
    m = _PRIVATE_RE.search(text or "")
    if m is None:
        return None
    line = (text or "").count("\n", 0, m.start()) + 1
    return WpMarkupError("wp_markup_unsupported", f"私用の字 U+{ord(m.group(0)):04X}（{line} 行目）")


def _restore(text: str, pieces: list) -> str:
    """目印を断片に戻す（1 回の置き換え。断片は戻したあとの HTML なので目印を含まない）。"""
    def put(m):
        index = int(m.group(1))
        if index >= len(pieces):
            raise WpMarkupError("wp_markup_unsupported", "私用の字")
        return pieces[index]
    return _MARK_RE.sub(put, text)


def _inline(text: str, pieces: list | None = None) -> str:
    """1 行の中の記法。`pieces` は外側の断片の表（リンクの文字を処理するとき引き継ぐ）。"""
    if pieces is None:
        error = _private_char_error(text)
        if error is not None:
            raise error
        pieces = []

    def keep(fragment: str) -> str:
        pieces.append(fragment)
        return f"{_MARK_OPEN}{len(pieces) - 1}{_MARK_CLOSE}"

    text = _CODE_SPAN_RE.sub(lambda m: keep(f"<code>{html.escape(m.group(2).strip(), quote=False)}</code>"), text)
    text = _ESCAPE_RE.sub(lambda m: keep(html.escape(m.group(1), quote=False)), text)
    for pattern, label in _INLINE_REFUSALS:
        if pattern.search(text):
            raise WpMarkupError("wp_markup_unsupported", label)

    def link(m):
        label, url = m.group(1), m.group(2)
        if _MARK_OPEN in url:   # 行き先の中の `…` や `\` は字のまま送れない（目印が残る）
            raise WpMarkupError("wp_markup_unsupported", "リンクの行き先の中の ` か \\")
        problem = _link_problem(url)
        if problem is not None:
            raise WpMarkupError("wp_link_scheme_unsupported", problem)
        return keep(f'<a href="{html.escape(url)}">{_inline(label, pieces)}</a>')

    text = _LINK_RE.sub(link, text)
    if "](" in text:   # 形の崩れたリンク（行き先に括弧や空白がある）は字のまま送らずに断る
        raise WpMarkupError("wp_markup_unsupported", "リンクの形（行き先に括弧か空白）")
    text = html.escape(text, quote=False)
    text = _STRONG_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = _EM_RE.sub(lambda m: f"<em>{m.group(1)}</em>", text)
    return _restore(text, pieces)


def _para_line(line: str) -> str:
    """段落の 1 行。URL だけの行はリンクにする（WordPress の埋め込みに替わらない）。"""
    if _BARE_URL_RE.fullmatch(line):
        problem = _link_problem(line)
        if problem is not None:
            raise WpMarkupError("wp_link_scheme_unsupported", problem)
        url = html.escape(line)
        return f'<a href="{url}">{url}</a>'
    return _inline(line)


def render_title(title: str) -> str:
    """送る題。生の HTML の型があれば断り、`<`・`>`・`&` はエスケープする（頭の注）。"""
    text = title or ""
    if _RAW_HTML_RE.search(text):
        raise WpMarkupError("wp_markup_unsupported", "題の中の HTML")
    error = _private_char_error(text)
    if error is not None:
        raise WpMarkupError("wp_markup_unsupported", "題の中の私用の字")
    return html.escape(text, quote=False)


def render(body: str) -> str:
    """Markdown（`hatena_convert.render` のあとの本文）から HTML。外れる記法は `WpMarkupError`。"""
    body = (body or "").replace("\r\n", "\n").replace("\r", "\n")
    error = _private_char_error(body)
    if error is not None:
        raise error
    lines = body.split("\n")
    out: list = []
    para: list = []
    i = 0

    def flush():
        if para:
            out.append("<p>" + "<br />\n".join(_para_line(line.strip()) for line in para) + "</p>")
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
        if _INDENTED_ITEM_RE.match(line):
            raise WpMarkupError("wp_markup_unsupported", "入れ子の箇条書き")
        if _INDENTED_CODE_RE.match(line):
            raise WpMarkupError("wp_markup_unsupported", f"字下げのコード（{i + 1} 行目・``` で囲む）")
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            level = min(len(heading.group(1)) + 1, 6)   # 題が h1 なので 1 段下げる
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            i += 1
            continue
        for pattern, label in _BLOCK_REFUSALS:
            if pattern.match(line):
                raise WpMarkupError("wp_markup_unsupported", label)
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
    # ショートコードを働かせない（頭の注）。`[` は本文の字からしか来ない（リンクの `[` は使い切っている）。
    return "\n".join(out).replace("[", "&#91;")
