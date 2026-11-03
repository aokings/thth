"""断りの目録の柵（設計「監査と審査の機械化と前向きな断り」§3・段 A）。

原則（docs/原則_前向きな断り.md）: 断るときは なぜ・どうすれば通るか・どうすればもっと良くなるか を出す。
新しい断りの符丁を足して目録に載せなければ、ここが赤になる。
"""
from __future__ import annotations

import argparse
import importlib.util
import re
from pathlib import Path

import pytest

from thth import cli, core, guard, lint, queuefile
from thth import refusal_catalog as rc

ROOT = Path(__file__).resolve().parent.parent
DASHBOARDS = [ROOT / "skills" / "thth" / "dashboard" / f"{name}.html"
              for name in ("all", "campaign", "series", "project")]

# no_better の数の上限（段 A の今の数で固定・版ごとに下げる。上げない）。
NO_BETTER_LIMIT = {"internal": 196, "terminal": 14}
# 目録の項目の数の下限（消すときは項目を残して retired を立てる）。
CATALOG_FLOOR = 750
NEGATIVE_ENDINGS = ("できません", "不正です")


def _count_refusals():
    spec = importlib.util.spec_from_file_location("count_refusals", ROOT / "tools" / "count_refusals.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def inventory():
    return _count_refusals().inventory(ROOT)


@pytest.fixture(scope="module")
def source_codes(inventory):
    return {row["code"] for row in inventory["rows"]}


def test_ASTで集めた符丁がすべて目録にある(source_codes):
    missing = sorted(source_codes - set(rc.CATALOG))
    assert missing == [], f"目録に無い断りの符丁（thth/refusal_catalog_part*.py に足す）: {missing}"


def test_lintとguardと説明の辞書の符丁も目録にある():
    codes = set(lint.REASONS) | set(guard.PRINCIPAL_REFUSALS) | set(queuefile.REPLY_TO_FILE_MESSAGES)
    # 説明の辞書の鍵のうち符丁の形のもの（`handle` のような 1 語の欄の名は符丁でない）。
    codes |= {key for key in rc.fix_texts() if re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", key)}
    assert sorted(codes - set(rc.CATALOG)) == []


def test_whyとfixは空でない():
    empty = [code for code, entry in rc.CATALOG.items()
             if not entry["why"].strip() or not entry["fix"]["text"].strip()]
    assert empty == []


def test_betterが空ならno_betterの理由が要る():
    wrong = []
    for code, entry in rc.CATALOG.items():
        has_better = bool(entry["better"].strip())
        reason = entry["no_better"]
        if has_better == (reason is not None) or (reason is not None and reason not in rc.NO_BETTER):
            wrong.append(code)
    assert wrong == []


def test_no_betterの数は上限を超えない():
    counts = rc.counts()["no_better"]
    for reason, limit in NO_BETTER_LIMIT.items():
        assert counts.get(reason, 0) <= limit, f"no_better: {reason} が {counts.get(reason)}（上限 {limit}）"


def test_項目の形():
    for code, entry in rc.CATALOG.items():
        assert re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", code), code
        assert entry["kind"] in rc.KINDS, code
        assert entry["doors"] and set(entry["doors"]) <= set(rc.DOORS), code
        assert entry["since"] and entry["lang"] == "ja", code


def test_retiredの無い符丁が消えたら赤(source_codes):
    assert len(rc.CATALOG) >= CATALOG_FLOOR, "目録の項目を消した（消すなら retired を立てて残す）"
    gone = sorted(code for code, entry in rc.CATALOG.items()
                  if not entry["retired"] and code not in source_codes)
    assert gone == [], f"ソースから消えた符丁（互換のため retired を立てる）: {gone}"


def test_目録のfixは既存の辞書の文と食い違わない():
    texts = rc.fix_texts()
    wrong = [code for code, found in texts.items()
             if code in rc.CATALOG and rc.CATALOG[code]["fix"]["text"] not in {text for _, text in found}]
    assert wrong == []


def test_目録のwhyは安全装置の文と食い違わない():
    for code, entry in rc.CATALOG.items():
        why = rc.guard_why(code)
        if why is not None:
            assert entry["why"] == why, code
            assert why in core.guard_refusal_message("<口座>", rc._Refused(code)), code


def test_ダッシュボードのTHTH_REASONは目録と一致する():
    for path in DASHBOARDS:
        html = path.read_text(encoding="utf-8")
        body = re.search(r"const THTH_REASON=\{(.*?)\};", html).group(1)
        assert dict(re.findall(r'(\w+):"([^"]*)"', body)) == rc.DASHBOARD_REASON_JA, path.name
    for code, why in rc.DASHBOARD_REASON_JA.items():
        assert rc.CATALOG[code]["why"] == why, code


def test_言い方_できませんで終わらない():
    dict_fixed = {code for code, entry in rc.CATALOG.items()
                  if any(s.startswith("fix_dict:") for s in entry["sources"])}
    wrong = []
    for code, entry in rc.CATALOG.items():
        texts = [entry["better"]] + ([] if code in dict_fixed else [entry["fix"]["text"]])
        wrong += [code for text in texts if text.rstrip("。 ").endswith(NEGATIVE_ENDINGS)]
    assert wrong == []


def _subcommands(parser):
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    return None


def test_fixの命令は実在する():
    parser = cli.build_parser()
    wrong = []
    for code, entry in rc.CATALOG.items():
        command = entry["fix"]["command"]
        if not command:
            continue
        words = command.split()
        if words[0] != "thth":
            wrong.append(code)
            continue
        current = parser
        for word in words[1:3]:
            choices = _subcommands(current)
            if choices is None or word.startswith(("-", "<")):
                break
            if word not in choices:
                wrong.append(code)
                break
            current = choices[word]
    assert wrong == []


def test_skillに出る符丁も目録にある():
    # 段 C（skill lint）が目録の CATALOG を読む。skill の本文に出る符丁の代表。
    for code in ("goal_invalid", "facebook_page_not_granted", "ads_tree_changed"):
        assert code in rc.CATALOG, code


def test_安全装置のtoo_soonは3つがそろう():
    entry = rc.CATALOG["too_soon"]
    assert entry["why"] and entry["fix"]["text"] and entry["better"]
    assert entry["fix"]["command"].startswith("thth schedule")


def test_穴埋めは検査を通った値だけ():
    good = rc.render("too_soon", at="2026-11-03T10:00")
    assert "2026-11-03T10:00" in good["command"]
    bad = rc.render("too_soon", at="明日の朝 /etc/passwd")
    assert "<時刻>" in bad["command"] and "passwd" not in bad["command"]
    assert rc.render("too_soon", body="本文")["command"] == rc.CATALOG["too_soon"]["fix"]["command"]
    first = rc.lines("daily_limit")[0]
    assert first.startswith("daily_limit: ")
