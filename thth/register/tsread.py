"""TypeScript の `export const` の値を読む（台帳の器の段 9。形式 `ts_const`）。

読むのは、行の頭から書かれた `export const 名[: 型] = <値>` の値のうち、次の形だけでできた物:

- 字（'…' と "…"。`${` を含まない `…` も字）・数・`true`・`false`・`null`・`undefined`（null と読む）
- 並び `[…]`・辞書 `{ 鍵: 値, … }`（鍵は名か字か数。重なる鍵は読まない）・末尾のカンマ
- `new Set(<並び>)`・`new Set<型>(<並び>)`・`new Set()`（並びとして読む）・`new Date('…')`（字として読む）
- 値のあとの `as const`

それ以外（関数・式・ほかの名を指す値・`...` の広げ）を含む定数は、値として読まない（名だけ返す）。
コードを動かさない。注（`//`・`/* */`）は読み飛ばす。行の頭でない `export const`・注や字の中の `export const`
（前もって注・字・テンプレート・正規表現の範囲を数えて除く）と、`export { … }` で後から出す定数は見ない。
数の鍵は 0 か頭が 0 でない整数だけ（JS の鍵の名と同じ字になる物）。読みは枚の長さにほぼ比例する（1 つの定数の
型の注釈と値は、次の行の頭の `export` で打ち切る）。
"""
from __future__ import annotations

import re

EXPORT = re.compile(r"^export\s+const\s+([A-Za-z_$][A-Za-z0-9_$]*)", re.M)
NAME = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
NUMBER = re.compile(r"-?(?:0|[1-9](?:_?[0-9])*)(?:\.[0-9](?:_?[0-9])*)?(?:[eE][+-]?[0-9]+)?")
INT_KEY = re.compile(r"0|[1-9][0-9]*")
U_ESC = re.compile(r"\\u\{([0-9a-fA-F]{1,6})\}|\\u([0-9a-fA-F]{4})")
X_ESC = re.compile(r"\\x([0-9a-fA-F]{2})")
AS_CONST = re.compile(r"as\s+const\b")
NEXT_EXPORT = re.compile(r"\nexport\b")
TYPE_MAX = 4096
REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^")
DEPTH_MAX = 64
SIZE_MAX = 4 * 1024 * 1024


class NotLiteral(Exception):
    pass


class _Reader:
    def __init__(self, text: str, at: int):
        self.s, self.i = text, at

    # ---- 字の送り
    def skip(self):
        s, n = self.s, len(self.s)
        while self.i < n:
            ch = s[self.i]
            if ch in " \t\r\n\ufeff":
                self.i += 1
            elif s.startswith("//", self.i):
                end = s.find("\n", self.i)
                self.i = n if end < 0 else end + 1
            elif s.startswith("/*", self.i):
                end = s.find("*/", self.i + 2)
                if end < 0:
                    raise NotLiteral("comment")
                self.i = end + 2
            else:
                return

    def peek(self, word: str) -> bool:
        self.skip()
        return self.s.startswith(word, self.i)

    def eat(self, word: str) -> None:
        if not self.peek(word):
            raise NotLiteral(word)
        self.i += len(word)

    def word(self) -> str | None:
        self.skip()
        m = NAME.match(self.s, self.i)
        return m.group(0) if m else None

    # ---- 型の注釈（`名: 型 =` の型）を読み飛ばす
    def skip_type_to_equals(self) -> None:
        s, depth = self.s, 0
        m = NEXT_EXPORT.search(s, self.i, self.i + TYPE_MAX)
        n = m.start() if m else min(len(s), self.i + TYPE_MAX)
        while self.i < n:
            self.skip()
            if self.i >= n:
                break
            ch = s[self.i]
            if ch in "'\"`":
                self.string()
                continue
            if s.startswith("=>", self.i):
                self.i += 2
                continue
            if ch in "<({[":
                depth += 1
            elif ch in ">)}]":
                depth -= 1
                if depth < 0:
                    raise NotLiteral("type")
            elif ch == "=" and depth == 0:
                if s.startswith("==", self.i):
                    raise NotLiteral("type")
                self.i += 1
                return
            elif ch == ";" and depth == 0:
                raise NotLiteral("no_value")   # `declare` などの値の無い定数
            self.i += 1
        raise NotLiteral("no_value")

    # ---- 値
    def value(self, depth: int = 0):
        if depth > DEPTH_MAX:
            raise NotLiteral("depth")
        self.skip()
        s = self.s
        if self.i >= len(s):
            raise NotLiteral("end")
        ch = s[self.i]
        if ch in "'\"`":
            return self.string()
        if ch == "[":
            return self.array(depth)
        if ch == "{":
            return self.obj(depth)
        m = NUMBER.match(s, self.i)
        if m and (ch.isdigit() or ch == "-"):
            self.i = m.end()
            if NAME.match(s, self.i) or s.startswith(".", self.i) or s[self.i:self.i + 1].isdigit():
                raise NotLiteral("number")
            text = m.group(0).replace("_", "")
            return float(text) if any(c in text for c in ".eE") else int(text)
        word = self.word()
        if word == "export" and s[self.i - 1:self.i] == "\n":
            raise NotLiteral("expression")   # 閉じない値は、次の定数の頭で打ち切る
        if word in ("true", "false", "null", "undefined"):
            self.i += len(word)
            return {"true": True, "false": False}.get(word)
        if word == "new":
            self.i += 3
            return self.new(depth)
        raise NotLiteral("expression")

    def new(self, depth: int):
        what = self.word()
        if what not in ("Set", "Date"):
            raise NotLiteral("new")
        self.i += len(what)
        if self.peek("<"):
            self.skip_generic()
        self.eat("(")
        if what == "Set":
            if self.peek(")"):
                self.i += 1
                return []
            items = self.array(depth)
            self.eat(")")
            return items
        text = self.value(depth + 1)
        if not isinstance(text, str):
            raise NotLiteral("date")
        self.eat(")")
        return text

    def skip_generic(self) -> None:
        depth = 0
        while self.i < len(self.s):
            ch = self.s[self.i]
            if ch == "<":
                depth += 1
            elif ch == ">":
                depth -= 1
                if depth == 0:
                    self.i += 1
                    return
            self.i += 1
        raise NotLiteral("generic")

    def array(self, depth: int) -> list:
        self.eat("[")
        out = []
        while True:
            if self.peek("]"):
                self.i += 1
                return out
            if self.peek("..."):
                raise NotLiteral("spread")
            out.append(self.value(depth + 1))
            if self.peek(","):
                self.i += 1
            elif not self.peek("]"):
                raise NotLiteral("array")

    def obj(self, depth: int) -> dict:
        self.eat("{")
        out = {}
        while True:
            if self.peek("}"):
                self.i += 1
                return out
            self.skip()
            ch = self.s[self.i] if self.i < len(self.s) else ""
            if ch in "'\"":
                key = self.string()
            elif ch.isdigit():
                m = INT_KEY.match(self.s, self.i)
                end = m.end() if m else self.i
                if not m or self.s[end:end + 1].isalnum() or self.s[end:end + 1] in "._":
                    raise NotLiteral("key")   # JS の鍵の名と字が変わる数（1.0・1e3・0x1）は読まない
                key = m.group(0)
                self.i = end
            else:
                key = self.word()
                if key is None:
                    raise NotLiteral("key")
                self.i += len(key)
            self.eat(":")
            if key in out:
                raise NotLiteral("key_duplicate")
            out[key] = self.value(depth + 1)
            if self.peek(","):
                self.i += 1
            elif not self.peek("}"):
                raise NotLiteral("object")

    _ESC = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}

    def string(self) -> str:
        s, quote = self.s, self.s[self.i]
        self.i += 1
        out = []
        while self.i < len(s):
            ch = s[self.i]
            if ch == quote:
                self.i += 1
                return "".join(out)
            if quote == "`" and s.startswith("${", self.i):
                raise NotLiteral("template")
            if ch in "\r\n" and quote != "`":
                raise NotLiteral("string")
            if ch == "\\":
                nxt = s[self.i + 1:self.i + 2]
                if nxt == "u":
                    m = U_ESC.match(s, self.i)
                    if not m:
                        raise NotLiteral("escape")
                    code = int(m.group(1) or m.group(2), 16)
                    if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                        raise NotLiteral("escape")
                    out.append(chr(code))
                    self.i = m.end()
                    continue
                if nxt == "x":
                    m = X_ESC.match(s, self.i)
                    if not m:
                        raise NotLiteral("escape")
                    out.append(chr(int(m.group(1), 16)))
                    self.i += 4
                    continue
                if nxt in "\r\n":
                    self.i += 2 + (1 if s.startswith("\r\n", self.i + 1) else 0)
                    continue
                if nxt == "" or (nxt.isdigit() and nxt != "0") or (nxt == "0" and s[self.i + 2:self.i + 3].isdigit()):
                    raise NotLiteral("escape")
                out.append(self._ESC.get(nxt, nxt))
                self.i += 2
                continue
            out.append(ch)
            self.i += 1
        raise NotLiteral("string")

    def end_of_statement(self) -> None:
        """値のあと: `as const`・`;`・行の終わり・次の文の頭（`export` など）だけを受ける。"""
        self.skip_inline()
        m = AS_CONST.match(self.s, self.i)
        if m:
            self.i = m.end()
            self.skip_inline()
        if self.i >= len(self.s) or self.s[self.i] in ";\r\n":
            if self.i < len(self.s) and self.s[self.i] in "\r\n":
                # 次の行が式の続き（`.map(` など）でないこと
                save = self.i
                self.skip()
                if self.i < len(self.s) and self.s[self.i] in ".[(?+-*/%&|^<>=,":
                    raise NotLiteral("expression")
                self.i = save
            return
        raise NotLiteral("expression")

    def skip_inline(self) -> None:
        s, n = self.s, len(self.s)
        while self.i < n:
            if s[self.i] in " \t":
                self.i += 1
            elif s.startswith("//", self.i):
                end = s.find("\n", self.i)
                self.i = n if end < 0 else end
            elif s.startswith("/*", self.i):
                end = s.find("*/", self.i + 2)
                if end < 0:
                    raise NotLiteral("comment")
                self.i = end + 2
            else:
                return


def _masked(s: str) -> list:
    """注・字・テンプレート・正規表現の範囲 `(頭, 終わり)` の並び（`export const` を探す前に除く）。"""
    out, i, n = [], 0, len(s)
    prev = ""          # 直前の意味のある字（`/` が割り算か正規表現かの見分け）
    stack = []         # テンプレートの `${` の中の波かっこの深さ
    depth = 0
    tpl_start = None   # いちばん外のテンプレートの頭

    def template(j):
        """テンプレートの字を `j` から読む。閉じれば (終わり, None)、`${` なら (中の頭, "expr")。"""
        while j < n:
            if s[j] == "\\":
                j += 2
            elif s[j] == "`":
                return j + 1, None
            elif s.startswith("${", j):
                return j + 2, "expr"
            else:
                j += 1
        return n, None

    while i < n:
        c = s[i]
        if s.startswith("//", i):
            j = s.find("\n", i)
            j = n if j < 0 else j
            out.append((i, j))
            i = j
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append((i, j))
            i = j
        elif c in "'\"":
            j = i + 1
            while j < n and s[j] != c and s[j] != "\n":
                j += 2 if s[j] == "\\" else 1
            j = min(j + 1, n)
            if tpl_start is None:
                out.append((i, j))
            i, prev = j, "a"
        elif c == "`":
            if tpl_start is None:
                tpl_start = i
            j, what = template(i + 1)
            if what == "expr":
                stack.append(depth)
                depth += 1
                prev = "("
            elif not stack:
                out.append((tpl_start, j))
                tpl_start, prev = None, "a"
            else:
                prev = "a"
            i = j
        elif c == "/" and prev in REGEX_BEFORE | {""}:
            j, klass = i + 1, False
            while j < n and s[j] != "\n":
                if s[j] == "\\":
                    j += 2
                    continue
                if s[j] == "[":
                    klass = True
                elif s[j] == "]":
                    klass = False
                elif s[j] == "/" and not klass:
                    break
                j += 1
            j = min(j + 1, n)
            if tpl_start is None:
                out.append((i, j))
            i, prev = j, "a"
        elif c == "}" and stack and depth - 1 == stack[-1]:
            stack.pop()
            depth -= 1
            j, what = template(i + 1)
            if what == "expr":
                stack.append(depth)
                depth += 1
                prev = "("
            elif not stack:
                out.append((tpl_start, j))
                tpl_start, prev = None, "a"
            i = j
        else:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            if not c.isspace():
                prev = c
            i += 1
    if tpl_start is not None:
        out.append((tpl_start, n))
    return out


def exports(text: str):
    """`(定数の名 → 値, 値として読めない定数の名の並び, 読みの問題)`。問題は枚の全体が読めないときだけ。"""
    if len(text) > SIZE_MAX:
        return {}, [], "too_large"
    consts, not_literal = {}, []
    masked = _masked(text)
    starts = [a for a, _b in masked]
    import bisect
    for m in EXPORT.finditer(text):
        k = bisect.bisect_right(starts, m.start()) - 1
        if k >= 0 and masked[k][0] <= m.start() < masked[k][1]:
            continue   # 注・字の中
        name = m.group(1)
        if name in consts or name in not_literal:
            return {}, [], "const_duplicate"
        r = _Reader(text, m.end())
        try:
            if r.peek(":"):
                r.i += 1
                r.skip_type_to_equals()
            else:
                r.eat("=")
            value = r.value()
            r.end_of_statement()
        except (NotLiteral, RecursionError, ValueError, OverflowError):
            not_literal.append(name)
            continue
        consts[name] = value
    return consts, not_literal, None
