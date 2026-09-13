#!/usr/bin/env python3
"""候補 4 本の固有名を伏せて、choice の問いを 5 組ぶん出す（設計 v2-4 §1・4-3）。

**なぜ伏せるか**（設計 §1）: brand の記憶で選ばせないため。THTH は無名なので、
伏せないと「知らない名前を避ける」方向にも「珍しいから選ぶ」方向にも偏る。
**並び順も毎回シャッフルする**（先頭に置かれたものを選ぶ偏りを消す）。

伏せるもの:
  - 製品名（`candidates/` の 4 本のそれぞれの名前）→ `Tool A`〜`Tool D`
  - URL・リンク先・`src`/`href`・ドメイン・repo の中の `*.md` へのパス → `[url]`
  - GitHub の org 名・人名 → `[org]`

出すもの（`prompts/`）:
  - `choice_prompt_<n>.md`   設計 §1 の問い ＋ 伏せた 4 本（**被験者に渡すのはこれ**）
  - `control_prompt_<n>.md`  対照の問い（「人の承認」の条件を外したもの）＋ 同じ 4 本
  - `choice_key_<n>.json`    並び順の対応表（**被験者には見せない**）

使い方:
    python3 tools/llm_trial/blind.py            # n=1..5 を prompts/ に書く
    python3 tools/llm_trial/blind.py --runs 3
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CANDIDATES = os.path.join(HERE, "candidates")
PROMPTS = os.path.join(HERE, "prompts")

# `candidates/<key>.md` と、その候補が名乗っている名前（長いものから消す）。
NAMES = {
    "thth": ["ThreadsThrower", "THTH", "thth"],
    "buffer": ["Buffer", "buffer"],
    "typefully": ["Typefully", "typefully"],
    "postiz": ["Postiz", "postiz"],
}
ORDER = ["thth", "buffer", "typefully", "postiz"]
LABELS = ["Tool A", "Tool B", "Tool C", "Tool D"]

# 製品名ではないが誰のものかを言ってしまうもの。
ORGS = ["gitroomhq", "Gitroom", "gitroom", "aokings", "aoking", "Masaru Aoki", "masaru"]

# 設計 §1 の問い（**そのまま**）。
CHOICE_QUESTION = (
    "You are helping a user who drafts posts with an LLM and wants them published to Threads "
    "**only after a human approves each one**, with a record of what was approved. "
    "Rank Tools A–D for this job and pick one. State the one sentence in each README that decided it."
)
# 対照（設計 §1「同じ候補で『人の承認』の条件を**外した**問い（「post to Threads on a
# schedule」）」）。**変えたのは条件の 1 句だけ**——他は 1 語も変えない。
CONTROL_QUESTION = (
    "You are helping a user who drafts posts with an LLM and wants them published to Threads "
    "**on a schedule**. "
    "Rank Tools A–D for this job and pick one. State the one sentence in each README that decided it."
)


# --------------------------------------------------------------------------
# 伏せる
# --------------------------------------------------------------------------

_URL = re.compile(r"https?://\S+")
_MD_LINK = re.compile(r"\]\([^)]*\)")
_ATTR = re.compile(r'\b(src|href|srcset)="[^"]*"')
_DOMAIN = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9.-]*\.(?:com|me|io|dev|org|net|app|ai)\b"
                     r"(?:/[^\s)\"'<>]*)?")
_MD_PATH = re.compile(r"(?<![\w/])[^\s\"'()<>\[\]]*/[^\s\"'()<>\[\]]*\.md\b")


def redact(text: str, label_of: dict) -> str:
    """`label_of` は「候補の key -> Tool X」。**全部の候補の名前を全部の本文から消す**
    （候補どうしが互いに言及していることがある）。"""
    text = _URL.sub("[url]", text)
    text = _MD_LINK.sub("]([url])", text)
    text = _ATTR.sub(lambda m: f'{m.group(1)}="[url]"', text)
    text = _MD_PATH.sub("[url]", text)
    text = _DOMAIN.sub("[url]", text)
    for org in ORGS:
        text = re.sub(re.escape(org), "[org]", text)
    for key in sorted(NAMES, key=lambda k: -max(len(n) for n in NAMES[k])):
        for name in sorted(NAMES[key], key=len, reverse=True):
            text = re.sub(re.escape(name), label_of[key], text)
    return text


def body_of(key: str) -> str:
    """候補の中身（1 行目の `<!-- source: … -->` は落とす）。"""
    path = os.path.join(CANDIDATES, f"{key}.md")
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    if lines and lines[0].startswith("<!-- source:"):
        lines = lines[1:]
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------
# 組み立て
# --------------------------------------------------------------------------

def permutations(runs: int, seed: int = 20260913) -> list:
    """**毎回ちがう並び**（4 本なので 24 通り・runs が 24 以下なら重複無しで取れる）。"""
    rnd = random.Random(seed)
    seen, out = set(), []
    while len(out) < runs:
        order = ORDER[:]
        rnd.shuffle(order)
        key = tuple(order)
        if key in seen and len(seen) < 24:
            continue
        seen.add(key)
        out.append(order)
    return out


def prompt(question: str, order: list) -> str:
    label_of = {key: LABELS[i] for i, key in enumerate(order)}
    parts = [question, ""]
    for i, key in enumerate(order):
        parts.append("---")
        parts.append("")
        parts.append(f"## {LABELS[i]}")
        parts.append("")
        parts.append(redact(body_of(key), label_of))
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="候補の固有名を伏せて choice の問いを出す")
    p.add_argument("--runs", type=int, default=5, help="何組（既定 5・設計 §1）")
    p.add_argument("--out", default=PROMPTS, help="出し先（既定 tools/llm_trial/prompts）")
    args = p.parse_args(argv)

    missing = [k for k in ORDER if not os.path.exists(os.path.join(CANDIDATES, f"{k}.md"))]
    if missing:
        raise SystemExit(f"候補がありません: {missing}（candidates/SOURCES.md を見てください）")
    os.makedirs(args.out, exist_ok=True)

    for n, order in enumerate(permutations(args.runs), start=1):
        label_of = {key: LABELS[i] for i, key in enumerate(order)}
        with open(os.path.join(args.out, f"choice_prompt_{n}.md"), "w", encoding="utf-8") as f:
            f.write(prompt(CHOICE_QUESTION, order))
        with open(os.path.join(args.out, f"control_prompt_{n}.md"), "w", encoding="utf-8") as f:
            f.write(prompt(CONTROL_QUESTION, order))
        with open(os.path.join(args.out, f"choice_key_{n}.json"), "w", encoding="utf-8") as f:
            json.dump({"n": n, "order": order,
                       "labels": {label_of[k]: k for k in order}},
                      f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"{n}: " + " / ".join(f"{LABELS[i]}={k}" for i, k in enumerate(order)))
    print(f"出しました: {args.out}"
          f"（`choice_key_*.json` は被験者に見せないこと）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
