"""`thth …` の命令の文字列が、道具の実物（argparse）に実在するかを確かめる（5.11.0 監査 5-a〜5-c）。

skill lint（`tests/test_skills_lint.py`）と断りの目録の柵（`tests/test_refusal_catalog.py`）が
同じものを使う。片方だけ直ると、片方の盲点が残る。

見るもの:
  1. 段（argparse の subparsers）を辿る。`a|b` は全部の枝が実在すること。
  2. **語で分ける段**（argparse の外で、位置引数の 1 語目を見て分ける命令）は、
     その語の表で見る（`WORD_DISPATCH`）。2 語以上あるのに 1 語目が表に無い・
     1 語目が表の語の綴り違いに見える、を断る。
  3. 位置引数に choices がある段（`thth inflight <a> show|resolve`）は choices で見る。
  4. 旗（`--…`）は、辿り着いた段か、その上の段の旗に実在すること。
"""
from __future__ import annotations

import argparse
import difflib
import re

from thth import account_cli, cli, scenario_cli, topic_cli

# 語で分ける段 → (その段で知っている語, 2 語以上なら 1 語目が表の語でなければならないか)。
# `thth topics <account> <語>` は 2 語でも正しい（account と語）ので、綴りの近さだけで見る。
WORD_DISPATCH = {
    ("thth", "scenario"): (frozenset(scenario_cli.SUBCOMMANDS) | {"skill"}, True),
    ("thth", "account"): (frozenset(account_cli.DISPATCH_WORDS), True),
    ("thth", "topics"): (frozenset(topic_cli.SUBCOMMANDS) | set(cli._TOPIC_WORDS), False),
}

# `thth topics <新しい語>` の先は `topic_cli.build_parser()` が読む。
_INNER_PARSERS = {("thth", "topics"): topic_cli.build_parser}

_STOP_PREFIXES = ("-", "<", "[", "{", '"', "'", "|", "…", "$", "(", "（")
# ここから先は別の命令（パイプ・繋ぎ・書き出し）。
_SHELL_BREAKS = {"|", "||", "&&", ";", ">", ">>", "2>&1", "<"}
_FLAG = re.compile(r"--[a-z0-9][a-z0-9-]*")


def subparsers(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def positionals(parser) -> list:
    return [a for a in parser._actions
            if not a.option_strings and not isinstance(a, argparse._SubParsersAction)]


def _near(word: str, known) -> str | None:
    """`word` が表の語の綴り違いに見えれば、その語。4 文字未満は見ない（account の名と区別できない）。"""
    if len(word) < 4:
        return None
    hit = difflib.get_close_matches(word, sorted(known), n=1, cutoff=0.8)
    return hit[0] if hit else None


def _plain_words(tokens: list) -> list:
    out = []
    for token in tokens:
        if token.startswith(_STOP_PREFIXES) or "…" in token or token in _SHELL_BREAKS:
            break
        out.append(token)
    return out


def _check_flags(tokens: list, parsers: list, path: list):
    known = set()
    for p in parsers:
        for action in p._actions:
            known.update(action.option_strings)
    quoted = None  # 引用の中（`--how "thth … --days 7"` の中の旗は、その命令の旗でない）。
    for token in tokens:
        if quoted:
            if token.endswith(quoted):
                quoted = None
            continue
        if token[:1] in ("\"", "'"):
            if not (len(token) > 1 and token.endswith(token[0])):
                quoted = token[0]
            continue
        if token in _SHELL_BREAKS:
            break
        for part in token.strip("[]()").split("|"):
            if not part.startswith("--"):
                continue
            flag = _FLAG.match(part)
            if flag and flag.group(0) not in known:
                return f"{' '.join(path)} に旗 {flag.group(0)} が無い"
    return None


def command_problem(parser, command: str):
    """`thth …` が実物に実在しなければ理由の文字列、実在すれば None。"""
    tokens = command.split()
    if not tokens or tokens[0] != "thth":
        return f"thth で始まらない: {command!r}"
    current, path, chain = parser, ["thth"], [parser]
    rest = tokens[1:]
    dispatched = False  # 語で分ける段を通ったか（通った先は位置引数か、自前の parser）。
    while rest:
        token = rest[0]
        if token.startswith(_STOP_PREFIXES) or "…" in token or token in _SHELL_BREAKS:
            break
        key = tuple(path)
        if key in WORD_DISPATCH and not dispatched:
            dispatched = True
            known, strict = WORD_DISPATCH[key]
            branches = token.split("|")
            found = [b for b in branches if b in known]
            if len(found) == len(branches):
                inner = _INNER_PARSERS.get(key)
                if inner is not None and all(b in topic_cli.SUBCOMMANDS for b in branches):
                    current = inner()
                    chain.append(current)
                    continue  # 自前の parser の段として、同じ語をもう一度辿る。
                path.append(token)
                rest = rest[1:]
                break  # 語の先は位置引数（名など）。
            if found:
                return f"{' '.join(path)} の下に無い語: {[b for b in branches if b not in known]}"
            near = next((n for n in (_near(b, known) for b in branches) if n), None)
            if near:
                return f"{' '.join(path)} の下の {token!r} は {near!r} の綴り違いに見える"
            if strict and len(_plain_words(rest)) >= 2:
                return f"{' '.join(path)} の下に {token!r} という語が無い（{sorted(known)}）"
            break  # 位置引数（名）として受ける。
        sub = subparsers(current)
        if sub is None:
            break  # 葉に着いた。ここから先は引数。
        branches = token.split("|")
        found = [b for b in branches if b in sub.choices]
        if len(found) == len(branches):
            current = sub.choices[branches[0]]
            chain.append(current)
            path.append(token)
            rest = rest[1:]
            continue
        if found:
            missing = [b for b in branches if b not in sub.choices]
            return f"{' '.join(path)} の下に無い枝: {missing}"
        if len(path) == 1 or not positionals(current):
            # 最上段・位置引数を取らない段で、知らない語が来た。
            return f"{' '.join(path)} の下に {token!r} が無い"
        break  # 位置引数（プロジェクトの名など）として受ける段。
    if subparsers(current) is None and not dispatched:
        problem = _check_choices(current, _positional_words(rest), path)
        if problem:
            return problem
    if subparsers(current) is not None and not dispatched:
        return None  # `thth <コマンド> --json` のように段の途中で止まった。旗の持ち主が決まらない。
    return _check_flags(rest, chain, path)


def _positional_words(tokens: list) -> list:
    """位置引数として並ぶ語。`<口座>` のような置き場の語は 1 つの位置引数（None）と数えて先へ進む。"""
    out = []
    for token in tokens:
        if token.startswith("<") and token.endswith(">"):
            out.append(None)
            continue
        if token.startswith(_STOP_PREFIXES) or "…" in token or token in _SHELL_BREAKS:
            break
        out.append(token)
    return out


def _check_choices(parser, words: list, path: list):
    """位置引数を順に当て、choices のある位置引数に表に無い語が来たら断る。"""
    for action, word in zip(positionals(parser), words):
        if action.nargs in ("*", "+", argparse.REMAINDER):
            break
        if action.choices and word is not None:
            missing = [b for b in word.split("|") if b not in action.choices]
            if missing:
                return f"{' '.join(path)} の {action.dest} に {missing} は無い（{sorted(action.choices)}）"
    return None


_FENCE = re.compile(r"```.*?```", re.S)
_INLINE = re.compile(r"`([^`\n]+)`")
_ENV = re.compile(r"^(?:env\s+)?(?:[A-Z_][A-Z0-9_]*=\S*\s+)+")


def _strip_prompt(line: str) -> str:
    """行頭の `$ `・`NAME=value `（`env NAME=value ` も）を剥がす（5.11.0 監査 5-b）。"""
    line = line.strip()
    if line.startswith("$ "):
        line = line[2:].lstrip()
    return _ENV.sub("", line)


def extract_commands(text: str) -> list:
    """backtick の `thth …` の命令を集める（行内と、囲みの中の `thth ` で始まる行）。
    囲みの中の `\\` で続く行は 1 本に繋ぐ。"""
    found = []
    for block in _FENCE.findall(text):
        lines = block.splitlines()
        joined, buf = [], ""
        for line in lines:
            if line.rstrip().endswith("\\"):
                buf += line.rstrip()[:-1] + " "
                continue
            joined.append(buf + line)
            buf = ""
        if buf:
            joined.append(buf)
        for line in joined:
            line = _strip_prompt(line)
            if line.startswith("thth "):
                found.append(" ".join(line.split()))
    outside = _FENCE.sub("", text)
    for inner in _INLINE.findall(outside):
        inner = _strip_prompt(inner)
        if inner == "thth" or inner.startswith("thth "):
            found.append(inner)
    return found
