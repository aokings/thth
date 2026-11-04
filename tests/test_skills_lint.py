"""skill の lint（設計「監査と審査の機械化と前向きな断り」2026-11-03 §0 の 1・§5・§7 段 C）。

skill 4 本（`skills/*/SKILL.md`）の本文が、道具の実物とずれていないかを縛る。
**regex で命令の形を当てるのでなく、`thth.cli.build_parser()` の argparse と
`mcp/server.py` の道具一覧を import して突き合わせる**（§5 の試作で、regex だと
`"thth_"+name` の動的生成を「無い」と誤検知した）。

見るもの:
  1. backtick の `thth …` の命令（行内と、``` の囲みの中で `thth ` で始まる行）が
     argparse の経路に実在する。`a|b` は全部の枝が実在すること。`<…>`・`[…]`・
     `-…`・`…` が来たらそこで止める（その先は引数）。語で分ける段（scenario・
     account・topics）の語・位置引数の choices・旗も見る（`tests/command_helpers.py`）。
     囲みの中の行頭の `$ `・`NAME=value ` は剥がし、`\\` で続く行は繋ぐ。
  2. backtick の MCP の道具名（`thth_…`）が `mcp/server.py` の道具一覧に実在する。
  3. 古い版の「新しく」を名乗る文——今の版より大版が 2 つ以上古い「（x.y.z）」の
     印——を**警告**する（赤にしない・集計して 1 行）。
  4. skill ごとの大きさ（bytes）の上限（ラチェット）。上げるのは理由を添えた
     ときだけ。下がったら上限も下げる。
  5. 断りの符丁（backtick の `xxx_yyy` が「で断」と続くもの）が断りの目録
     （`thth/refusal_catalog.py`）にある。目録ができるまでは飛ばす。

skill の本文はこの試験では変えない（skill を薄くするのは 5.11.0 のあと・別の段）。
実在しないものが見つかったら、skill を直さずに下の既知の例外の表に理由つきで
載せ、表の数に上限を置く。
"""
from __future__ import annotations

import glob
import importlib.util
import os
import re
import warnings

import pytest

from thth.cli import build_parser

# 命令の確かめは目録の柵と共用（5.11.0 監査 5-a〜5-c）。
from tests.command_helpers import command_problem, extract_commands  # noqa: F401

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_GLOB = os.path.join(REPO_ROOT, "skills", "*", "SKILL.md")
MCP_SERVER = os.path.join(REPO_ROOT, "mcp", "server.py")
VERSION_FILE = os.path.join(REPO_ROOT, "thth", "VERSION")

# ④ skill ごとの大きさの上限（bytes）。2026-11-03（5.10.0）の大きさの少し上で固定。
#   thth 69,079 / thth-flow 17,436 / thth-ads 12,704 / thth-admin 3,987。
#   thth は設計 §5 のとおり 70,000 から。skill を薄くしたら上限も下げる。
SIZE_LIMITS = {
    # 段 C（2026-11-04）: 断りの 3 行と重なる符丁ごとの直し方を落として 70,048 → 68,690。下げる向きにだけ動かす。
    "thth": 69_000,
    "thth-flow": 18_000,
    "thth-ads": 13_000,
    "thth-admin": 4_200,
}

# ① 実在しない命令のうち、今は直さずに通すもの（skill, 命令の文字列）→ 理由。
KNOWN_MISSING_COMMANDS: dict = {}
MAX_KNOWN_MISSING_COMMANDS = 0

# ② 実在しない MCP の道具名のうち、今は直さずに通すもの（skill, 名前）→ 理由。
KNOWN_MISSING_TOOLS = {
    # 道具名でなく、queue の置き場（docs/sns/queue/）のファイル名を backtick で書いている。
    ("thth", "thth_run_state"): "queue のファイル名（MCP の道具ではない）",
    ("thth", "thth_run_detail"): "queue のファイル名（MCP の道具ではない）",
    ("thth", "thth_run_next"): "queue のファイル名（MCP の道具ではない）",
}
MAX_KNOWN_MISSING_TOOLS = 3

# ③ 「（x.y.z）」の印が今の大版よりこれだけ以上古いと警告する。
OLD_MAJOR_GAP = 2


# ---------------------------------------------------------------- 読み出し

def _skills() -> dict:
    """{skill の名前: 本文}。"""
    out = {}
    for path in sorted(glob.glob(SKILLS_GLOB)):
        name = os.path.basename(os.path.dirname(path))
        with open(path, encoding="utf-8") as f:
            out[name] = f.read()
    return out


_TOOL = re.compile(r"`(thth_[a-z0-9_]+)(?=[`\s{])")


def extract_tool_names(text: str) -> list:
    """backtick の MCP の道具名（`thth_…`）。`thth_scenario: 1` のような
    front-matter の鍵（直後が `:`）は道具名に数えない。"""
    return _TOOL.findall(text)


_CODE = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`\s*(?:で断|になり、断|（断り）)")


def extract_refusal_codes(text: str) -> list:
    """断りの符丁と読める backtick（直後が「で断」「になり、断」「（断り）」のもの。5.11.0 監査 5-e）。"""
    return _CODE.findall(text)


_VERSION_MARK = re.compile(r"（(\d+)\.(\d+)\.(\d+)")


def old_version_marks(text: str, current_major: int) -> list:
    """今の大版より OLD_MAJOR_GAP 以上古い「（x.y.z」の印 [(行番号, x.y.z)]。"""
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in _VERSION_MARK.finditer(line):
            if current_major - int(m.group(1)) >= OLD_MAJOR_GAP:
                out.append((lineno, ".".join(m.groups())))
    return out


# ---------------------------------------------------------------- 実物

def _load_mcp_server():
    spec = importlib.util.spec_from_file_location("thth_mcp_server_skills_lint", MCP_SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _collect_names(obj, names: set, depth: int = 0) -> None:
    if depth > 3:
        return
    if isinstance(obj, dict):
        if isinstance(obj.get("name"), str) and ("inputSchema" in obj or "description" in obj):
            names.add(obj["name"])
        for key, value in obj.items():
            if isinstance(key, str) and key.startswith("thth_"):
                names.add(key)
            _collect_names(value, names, depth + 1)
    elif isinstance(obj, (list, tuple, set, frozenset)):
        for item in obj:
            if isinstance(item, str) and item.startswith("thth_"):
                names.add(item)
            _collect_names(item, names, depth + 1)


def mcp_tool_names() -> set:
    """`mcp/server.py` の道具一覧（TOOLS・ADMIN_TOOLS・SERVER_TOOLS の `"thth_"+name`・
    REPORT_TOOLS・PLAZA_TOOLS・REMOTE_TOOLS・名前の表など、大文字の名前の全部）。"""
    mod = _load_mcp_server()
    names: set = set()
    for key, value in vars(mod).items():
        if key.isupper():
            _collect_names(value, names)
    return names


def _current_major() -> int:
    with open(VERSION_FILE, encoding="utf-8") as f:
        return int(f.read().strip().split(".")[0])


@pytest.fixture(scope="module")
def parser():
    return build_parser()


@pytest.fixture(scope="module")
def tools():
    return mcp_tool_names()


# ---------------------------------------------------------------- 試験

def test_skillは4本ある():
    assert set(_skills()) == set(SIZE_LIMITS), \
        f"skill が増減した（SIZE_LIMITS も直す）: {sorted(_skills())}"


def test_skillの命令がargparseに実在する(parser):
    problems = []
    used_exceptions = set()
    for name, text in _skills().items():
        for command in extract_commands(text):
            problem = command_problem(parser, command)
            if problem is None:
                continue
            if (name, command) in KNOWN_MISSING_COMMANDS:
                used_exceptions.add((name, command))
                continue
            problems.append(f"{name}: `{command}` — {problem}")
    assert not problems, "skill に実在しない命令:\n" + "\n".join(problems)
    stale = set(KNOWN_MISSING_COMMANDS) - used_exceptions
    assert not stale, f"既知の例外が直った（表から消す）: {sorted(stale)}"
    assert len(KNOWN_MISSING_COMMANDS) <= MAX_KNOWN_MISSING_COMMANDS


def test_skillのMCP道具名が実在する(tools):
    problems = []
    used_exceptions = set()
    for name, text in _skills().items():
        for tool in sorted(set(extract_tool_names(text))):
            if tool in tools:
                continue
            if (name, tool) in KNOWN_MISSING_TOOLS:
                used_exceptions.add((name, tool))
                continue
            problems.append(f"{name}: `{tool}`")
    assert not problems, "skill に実在しない MCP の道具名:\n" + "\n".join(problems)
    stale = set(KNOWN_MISSING_TOOLS) - used_exceptions
    assert not stale, f"既知の例外が直った（表から消す）: {sorted(stale)}"
    assert len(KNOWN_MISSING_TOOLS) <= MAX_KNOWN_MISSING_TOOLS


def test_skillの大きさが上限の下():
    over = []
    for name, text in _skills().items():
        size = len(text.encode("utf-8"))
        if size > SIZE_LIMITS[name]:
            over.append(f"{name}: {size} bytes > 上限 {SIZE_LIMITS[name]}")
    assert not over, "skill が上限を超えた（薄くするか、理由を添えて上限を上げる）:\n" + "\n".join(over)


def test_古い版の新しくを名乗る印を警告する():
    """赤にしない。古い印の数を集計して 1 行の警告にする。"""
    major = _current_major()
    counts = {}
    for name, text in _skills().items():
        marks = old_version_marks(text, major)
        if marks:
            counts[name] = len(marks)
    if counts:
        summary = "・".join(f"{name} {n}" for name, n in sorted(counts.items()))
        warnings.warn(
            f"skill に今の版（{major}.x）より大版が {OLD_MAJOR_GAP} つ以上古い「（x.y.z）」の印: "
            f"{summary}（新しい機能の印としては古い。skill を薄くする段で落とす）",
            UserWarning, stacklevel=1)


def _catalog_codes(catalog) -> set:
    for attr in ("CATALOG", "REFUSALS", "ENTRIES", "CODES"):
        value = getattr(catalog, attr, None)
        if isinstance(value, dict):
            return set(value)
        if isinstance(value, (list, tuple, set, frozenset)):
            return {v if isinstance(v, str) else v.get("code") for v in value}
    for attr in ("codes", "all_codes"):
        fn = getattr(catalog, attr, None)
        if callable(fn):
            return set(fn())
    pytest.fail("thth.refusal_catalog に符丁の一覧が見つからない（この試験の _catalog_codes を目録の形に合わせる）")


def test_skillの断りの符丁が目録にある():
    catalog = pytest.importorskip("thth.refusal_catalog")
    codes = _catalog_codes(catalog)
    missing = []
    for name, text in _skills().items():
        for code in sorted(set(extract_refusal_codes(text))):
            if code not in codes:
                missing.append(f"{name}: `{code}`")
    assert not missing, "skill の符丁が断りの目録に無い:\n" + "\n".join(missing)


# ---------------------------------------------------------------- 柵の効き（壊れた例で赤）

BROKEN_SKILL = """---
name: broken
---
投稿は `thth send <account>` で。管理は `thth admin reports list --json`。
囲みの中も見る:
```
thth bookmark add <口座> --url <URL>
thth bookmrk list <口座>
```
存在しない `thth sendd <account>`・枝の片方が無い `thth repost|retweet <account>`・
存在しない段 `thth ads launch <広告の口座>`。道具は `thth_observe` と `thth_obsrve`。
符丁は `too_soon` で断る。front-matter の `thth_scenario: 1` は道具名でない。
"""


def test_壊れた例_実在しない命令で赤になる(parser):
    commands = extract_commands(BROKEN_SKILL)
    bad = {c for c in commands if command_problem(parser, c) is not None}
    assert bad == {
        "thth bookmrk list <口座>",
        "thth sendd <account>",
        "thth repost|retweet <account>",
        "thth ads launch <広告の口座>",
    }, bad
    good = set(commands) - bad
    assert good == {"thth send <account>", "thth admin reports list --json",
                    "thth bookmark add <口座> --url <URL>"}, good


def test_壊れた例_実在しない道具名で赤になる(tools):
    names = extract_tool_names(BROKEN_SKILL)
    assert names == ["thth_observe", "thth_obsrve"]
    assert [n for n in names if n not in tools] == ["thth_obsrve"]


def test_壊れた例_語で分ける命令の綴り違いと旗で赤になる(parser):
    """5.11.0 監査 5-a・5-c: argparse の外で語を分ける命令・位置引数の choices・旗。"""
    bad = ["thth scenario lnt kp", "thth account kp sett", "thth inflight kp resolv",
           "thth topics observ", "thth topics observe --inptu x", "thth send <a> --fiel x",
           "thth inflight <口座> shw", "thth topics suggest|observ", "thth plaza post --hwo x"]
    good = ["thth scenario lint kp", "thth account set kp write.floor 3 --reason x",
            "thth inflight kp resolve --published x", "thth topics observe --input x",
            "thth topics kopicha --search 語 --tag", "thth scenario kp", "thth <コマンド> … --json",
            "thth topics history お茶", "thth topics retract-note <note_id>",
            "thth inflight <口座> resolve --published x", "thth account set|resume <口座>",
            "thth scenario show|lint <project>", 'thth plaza post --how "thth measured kopicha-x --days 7"']
    assert [c for c in bad if command_problem(parser, c) is None] == []
    assert {c: command_problem(parser, c) for c in good if command_problem(parser, c)} == {}


def test_壊れた例_囲みの行頭の印と続きの行を読む():
    """5.11.0 監査 5-b: 行頭の `$ `・`NAME=value `・`env NAME=value ` を剥がし、`\\` で続く行を繋ぐ。"""
    text = "```\n$ thth sendd kp \\\n  --file x\nTHTH_ROOT=/srv/thth thth bord\nenv THTH_ROOT=/srv thth doctor kp\n```"
    assert extract_commands(text) == ["thth sendd kp --file x", "thth bord", "thth doctor kp"]


def test_壊れた例_符丁と古い印を拾う():
    assert extract_refusal_codes(BROKEN_SKILL) == ["too_soon"]
    assert extract_refusal_codes("`write_floor_reached`（断り）") == ["write_floor_reached"]
    marks = old_version_marks("**新しい**（3.8.0・何か）と（4.1.0）と（5.9.0）", 5)
    assert marks == [(1, "3.8.0")]
