"""`skills/thth-ads/SKILL.md` の柵（5.6.3・masaru 11/03「skill を作っておいて下さい」）。

skill には**実際に作った命令と欄の名前だけ**を書く。ここで縛るのは:
1. 本文の `thth ads <命令> [<下の命令>]` が CLI に実在し、同じ行の `--引数` がその命令の引数にある。
2. 本文の MCP の道具の名前（`thth_ads_*`）が MCP に出している物だけ。
3. 本文に出てくる json の欄の名前（engagement・site・efficiency・video の鍵）がコードの名前と同じ。
4. description が「いつ呼ぶか」を言い、wheel に入る場所（skills/）にある。
"""
from __future__ import annotations

import argparse
import os
import re

from thth import cli
from thth.ads import cli as ads_cli
from thth.ads import graphread, guide

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(REPO, "skills", "thth-ads", "SKILL.md")


def _text():
    with open(SKILL, encoding="utf-8") as f:
        return f.read()


def _ads_parsers():
    out = {}

    def visit(parser, path):
        out[path] = parser
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    visit(child, path + (name,))
    root = cli.build_parser()
    for action in root._actions:
        if isinstance(action, argparse._SubParsersAction):
            visit(action.choices["ads"], ())
    return out


def test_skillに出てくるthth_adsの命令と引数は全部実在する():
    parsers = _ads_parsers()
    text = _text().replace("\\\n", " ")
    seen = 0
    for line in text.splitlines():
        for m in re.finditer(r"thth ads ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?((?:[^`\n|]*))", line):
            verb, sub, rest = m.group(1), m.group(2), m.group(3)
            assert (verb,) in parsers, f"skill の命令が CLI に無い: thth ads {verb}"
            path = (verb, sub) if sub and (verb, sub) in parsers else (verb,)
            options = {o for a in parsers[path]._actions for o in a.option_strings}
            for option in re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", rest):
                assert option in options, f"skill の引数が CLI に無い: thth ads {' '.join(path)} {option}"
            seen += 1
    assert seen >= 20


def test_skillのMCPの道具名は出している物だけ():
    names = set(re.findall(r"thth_ads_[a-z_]+", _text()))
    assert names and names <= set(ads_cli.MCP_TOOLS)


def test_skillの欄の名前はコードの名前と同じ():
    text = _text()
    for key, _kind, _label in graphread.ENGAGEMENT_ACTIONS:
        assert f"`{key}`" in text
    for key, _label in graphread.SITE_LABELS:
        assert f"`{key}`" in text
    for key in graphread.EFFICIENCY_NUMERIC + graphread.EFFICIENCY_LISTS:
        assert f"`{key}`" in text
    for key in ("p25", "p100", "avg_time_watched", "cost_per_thruplay", "plays"):
        assert key in text
    for why in ("reported", "zero_reported_list", "no_link_clicks", "not_measured_or_left"):
        assert f"`{why}`" in text
    for kind in ("pixel_create", "pixel_install", "pixel_check_page", "landing_url_unknown"):
        assert f"`kind: {kind}`" in text
    for goal in graphread.OPTIMIZATION_GOALS:
        assert goal in text
    for by in graphread.BREAKDOWNS:
        assert by in text
    # 設計 効果測定と連動 §1-4・§2-2（11/03）: ピクセルの判断の表・入れる前に masaru へ 1 行・印は medium=paid
    assert "ピクセルが要るかの判断の表" in text and "入れる前に masaru へ 1 行" in text
    assert "utm_medium=paid" in text and "utm_medium=social" in text and "thth-flow" in text


def test_skillのdescriptionはいつ呼ぶかを言い_置き場はwheelに入る所():
    text = _text()
    m = re.match(r"^---\nname: thth-ads\ndescription: (.+)\n---\n", text)
    assert m and "広告" in m.group(1) and "とき" in m.group(1)
    assert guide.skill_path() == os.path.normpath(SKILL)


def test_MCPの広告の道具の説明とinstructionsはまずskillを示す():
    import sys
    sys.path.insert(0, os.path.join(REPO, "mcp"))
    import server
    tools = [t for t in server.TOOLS if t["name"].startswith("thth_ads_")]
    assert tools
    for tool in tools:
        assert tool["description"].startswith("まず広告の手順の skill thth-ads を読む"), tool["name"]
    boost = next(t for t in tools if t["name"] == "thth_ads_boost")
    assert "ピクセルの基本のコード" in boost["description"] and "thth ads doctor" in boost["description"]
    reply = server._handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert reply["result"]["instructions"].index("skill thth-ads") < reply["result"]["instructions"].index("お金")
