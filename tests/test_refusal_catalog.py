"""断りの目録の柵（設計「監査と審査の機械化と前向きな断り」§3・段 A）。

原則（docs/原則_前向きな断り.md）: 断るときは なぜ・どうすれば通るか・どうすればもっと良くなるか を出す。
新しい断りの符丁を足して目録に載せなければ、ここが赤になる。
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from thth import cli, core, guard, lint, queuefile
from thth import refusal_catalog as rc
from tests.command_helpers import command_problem

ROOT = Path(__file__).resolve().parent.parent
DASHBOARDS = [ROOT / "skills" / "thth" / "dashboard" / f"{name}.html"
              for name in ("all", "campaign", "series", "project")]

# no_better の数の上限（段 A の今の数で固定・版ごとに下げる。上げない）。
NO_BETTER_LIMIT = {"internal": 196, "terminal": 14}
# 目録の項目の数の下限（消すときは項目を残して retired を立てる）。
CATALOG_FLOOR = 750
NEGATIVE_ENDINGS = ("できません", "不正です")
# fix の終わりで断る打ち消し（5.11.0 監査 5-d）。可能の打ち消し・「ありません」・「ていません」。
NEGATIVE_FIX_ENDING = re.compile(r"((でき|[えけせてねべめれ])ません|できない|不正です|不可です|ありません|ていません)$")


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


def test_説明の辞書がある符丁は部にfixの文を書かない():
    """5.11.0 段 8 監査: `fix_dict` が優先するので、部に書いた `fix` の文は使われず、食い違っても気づけない。
    説明の辞書（`FIX_SOURCES`）に文がある符丁は、部の `fix` を None にする（正本は辞書の 1 か所）。"""
    texts = rc.fix_texts()
    dead = sorted(code for part in rc.PARTS for code, entry in part.ENTRIES.items()
                  if code in texts and entry.get("fix") is not None)
    assert dead == []


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


def _last_sentence(text: str) -> str:
    """文の終わり（句点・空白を除いたもの）。末尾の補いの「（…）」が通る道（`thth …`・
    「ください」・「〜ます」）を書いていればその補いを終わりとし、そうでなければ補いを除く。"""
    text = text.rstrip("。 ")
    m = re.search(r"（([^（）]*)）$", text)
    if m:
        inner = m.group(1).rstrip("。 ")
        if "thth " in inner or "ください" in inner or inner.endswith("ます"):
            return inner
        text = text[:m.start()]
    return text.rstrip("。 ")


def test_言い方_できませんで終わらない():
    """fix は「どうすれば通るか」で終える（原則 docs/原則_前向きな断り.md）。

    5.11.0 監査 5-d: 説明の辞書から引いた文も見る（前は除いていた）。fix は「できません」
    「不正です」のほか、可能の打ち消し（〜えません・〜けません・〜せません …）・「ありません」・
    「ていません」で終わる形も断る。better は利点で終わる文なので「〜ません」は見ない。"""
    wrong = []
    for code, entry in rc.CATALOG.items():
        fix = _last_sentence(entry["fix"]["text"])
        if NEGATIVE_FIX_ENDING.search(fix):
            wrong.append(f"{code}（fix）: {entry['fix']['text']}")
        if _last_sentence(entry["better"]).endswith(NEGATIVE_ENDINGS):
            wrong.append(f"{code}（better）: {entry['better']}")
    assert wrong == []


def test_壊れた例_言い方の柵が打ち消しの終わりを拾う():
    bad = ["予約はできません。", "付けません", "THTH からは変更できない", "この形は不可です", "指した原稿が queue にありません", "何もしていません。",
           "その札は待ちではありません（付けた札は取り消せません）"]
    good = ["thth lint で直してください", "口座ごとの欄を見てください（媒体ごとに足していません）",
            "持ち主が戻すと、また出せます", "その点はありません（thth map show <project>）"]
    assert [t for t in bad if not NEGATIVE_FIX_ENDING.search(_last_sentence(t))] == []
    assert [t for t in good if NEGATIVE_FIX_ENDING.search(_last_sentence(t))] == []


def test_sinceは版の形で今の版より新しくない():
    """5.11.0 監査 5-d: `since` は x.y.z の形で、今の版（thth/VERSION）を越えない。"""
    current = tuple(int(n) for n in (ROOT / "thth" / "VERSION").read_text().strip().split("."))
    wrong = [code for code, entry in rc.CATALOG.items()
             if not re.fullmatch(r"\d+\.\d+\.\d+", str(entry["since"]))
             or tuple(int(n) for n in entry["since"].split(".")) > current]
    assert wrong == []


# 符丁を投げる file から、その符丁が出る口が分かるもの（5.11.0 監査 5-d）。
_DOOR_OF_FILE = (("mcp/", "mcp"), ("thth/server", "remote"), ("skills/thth/dashboard/", "dashboard"))


def test_doorsは符丁を投げるfileの口を含む(inventory):
    """`tools/count_refusals.py` が符丁を見つけた file から口が分かるとき、その口が `doors` にある。"""
    wrong = []
    for row in inventory["rows"]:
        doors = set(rc.CATALOG[row["code"]]["doors"])
        for path in row["files"]:
            for prefix, door in _DOOR_OF_FILE:
                if path.startswith(prefix) and door not in doors:
                    wrong.append(f"{row['code']}: {path} は {door} の口（doors {sorted(doors)}）")
    assert wrong == []


def test_fixの命令は実在する():
    """段 C の skill lint と同じ確かめ（語で分ける段・choices・旗まで）を使う（5.11.0 監査 5-c）。"""
    parser = cli.build_parser()
    wrong = []
    for code, entry in rc.CATALOG.items():
        command = entry["fix"]["command"]
        if not command:
            continue
        problem = command_problem(parser, command)
        if problem is not None:
            wrong.append(f"{code}: `{command}` — {problem}")
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
