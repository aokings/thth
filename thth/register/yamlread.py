"""YAML の記録の中を読む（設計 §5.2「YAML の中を読む部品」）。

- 自前の読み手は作らない。中を見るのは、追加の部品（PyYAML。`pip install 'thth[yaml]'`）を入れた機械だけ。
  入れていない機械では、YAML の記録は「置く・戻す・版を取る」までで、「中は見ていない」と言う（`inspected` が偽）。
- 型を決めつけない読み: ほぼ全部を文字として読む（`BaseLoader`）。`updated: 2026-08-27` は字の `2026-08-27`、
  `yes`・`no`・`on`・`off`・数も字のまま。型は種類の宣言で見る。
  ただし 2 つだけ型にする（段 4 §9）: 引用していない `null`・`Null`・`NULL`・`~`・値の無い鍵は null、引用していない
  `true`・`True`・`TRUE`・`false`・`False`・`FALSE` は真偽。引用した `"null"`・`'true'` は字。鍵はいつも字。
- **範囲の外の書き方は、黙って読み違えずに断る**（語と行を返す）: 同じ段に同じ鍵が 2 回（`key_duplicate`）・
  別名の参照と錨（`&a`・`*a` → `anchor`・`alias`）・`---` で始まる 2 つめの文書（`multi_document`）・
  型の札（`!!str`・`!x` → `tag`）・`<<:`（`merge`）・鍵が字でない（`complex_key`）・読めない（`syntax`）。

網に出ない。PyYAML は字を読むだけの部品で、網にも外の命令にも触れない（柵 `tests/test_register_base.py` は、
この 1 枚にだけ `yaml` の import を許す）。
"""
from __future__ import annotations


def available() -> bool:
    try:
        import yaml  # noqa: F401
    except ImportError:
        return False
    return True


def load(text: str):
    """`(値, 断りの語 or None, 中を見たか)`。断りの語は `<語>:<行>`。"""
    try:
        import yaml
    except ImportError:
        return None, None, False
    loader = None
    events = []
    try:
        # 読み手を作るときにも字を確かめる（NUL などは ReaderError）。作るのも try の中。
        loader = yaml.BaseLoader(text)
        while loader.check_event():
            events.append(loader.get_event())
    except yaml.YAMLError as exc:
        return None, f"syntax:{_line(exc, text)}", True
    except Exception:  # noqa: BLE001 — 字の逃がしが範囲の外（\U00110000 など）だと ValueError・OverflowError が出る
        return None, f"syntax:{(loader.line + 1) if loader is not None else 0}", True
    finally:
        if loader is not None:
            loader.dispose()
    documents = 0
    depth = 0
    for event in events:
        line = getattr(getattr(event, "start_mark", None), "line", -1) + 1
        if isinstance(event, yaml.DocumentStartEvent):
            documents += 1
            if documents > 1:
                return None, f"multi_document:{line}", True
        if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
            depth += 1
            if depth > DEPTH_MAX:
                return None, f"too_deep:{line}", True  # 深い入れ子は組み立ての所で再帰の例外になる
        elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
            depth -= 1
        if isinstance(event, yaml.ScalarEvent) and _surrogate(event.value):
            # \uD800 などの逃がしは読めるが UTF-8 にできない（指紋を作れない）。読み口で断る。
            return None, f"surrogate:{line}", True
        if isinstance(event, yaml.AliasEvent):
            return None, f"alias:{line}", True
        if getattr(event, "anchor", None) is not None:
            return None, f"anchor:{line}", True
        if isinstance(event, (yaml.ScalarEvent, yaml.MappingStartEvent, yaml.SequenceStartEvent)):
            if getattr(event, "tag", None) is not None:
                return None, f"tag:{line}", True
    try:
        node = yaml.compose(text, Loader=yaml.BaseLoader)
    except yaml.YAMLError as exc:
        return None, f"syntax:{_line(exc, text)}", True
    except Exception:  # noqa: BLE001 — 上で読めた物は、ここでも読める（念のため断りにする）
        return None, "syntax:0", True
    if node is not None:
        problem = _check_node(node, yaml)
        if problem:
            return None, problem, True
    try:
        value = _build(node, yaml) if node is not None else None
    except Exception:  # noqa: BLE001 — 上で形を確かめた節だけを組む（念のため断りにする）
        return None, "syntax:0", True
    return value, None, True


_NULLS = frozenset(("", "~", "null", "Null", "NULL"))
_BOOLS = {"true": True, "True": True, "TRUE": True, "false": False, "False": False, "FALSE": False}


def _scalar(node):
    """引用していない（plain の）null と真偽だけを型にする。ほかは字のまま。"""
    if node.style is None:
        if node.value in _NULLS:
            return None
        if node.value in _BOOLS:
            return _BOOLS[node.value]
    return node.value


def _build(node, yaml):
    """節から値を組む（深さは DEPTH_MAX までに確かめてある）。鍵はいつも字。"""
    if isinstance(node, yaml.ScalarNode):
        return _scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return [_build(item, yaml) for item in node.value]
    out = {}
    for key, value in node.value:
        out[key.value] = _build(value, yaml)
    return out


DEPTH_MAX = 64  # vessel.DEPTH_MAX と同じ（ここは vessel を読まない）


def _surrogate(text) -> bool:
    return isinstance(text, str) and any("\ud800" <= c <= "\udfff" for c in text)


def _line(exc, text: str) -> int:
    mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
    if mark is not None:
        return mark.line + 1
    position = getattr(exc, "position", None)  # 読み手の字の断り（ReaderError）は位置だけを持つ
    if isinstance(position, int) and isinstance(text, str):
        return text.count("\n", 0, position) + 1
    return 0


def _check_node(node, yaml):
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, yaml.MappingNode):
            seen = set()
            for key, value in current.value:
                line = key.start_mark.line + 1
                if not isinstance(key, yaml.ScalarNode):
                    return f"complex_key:{line}"
                if key.value == "<<" and key.style is None:
                    return f"merge:{line}"
                if key.value in seen:
                    return f"key_duplicate:{line}"
                seen.add(key.value)
                stack.append(value)
        elif isinstance(current, yaml.SequenceNode):
            stack.extend(current.value)
    return None
