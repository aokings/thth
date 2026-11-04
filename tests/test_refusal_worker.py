"""Worker の口の断りの柵（設計「監査と審査の機械化と前向きな断り」§3「3 つの口で同じもの」）。

Worker（`callback/src`）が `{error: 符丁}` で断る符丁は、目録に `worker` の口で載り、その 3 つ（なぜ・通すには・
もっと良く）を `tools/render_refusals.py` が `callback/src/refusal-catalog.js` に書き出す。ここは、
①生成物が目録と一字も違わない ②Worker の source の符丁が目録にある（と、目録の `worker` の口が source に
まだある）③ほかの口にも出る符丁は Worker の口の文を持つ ④Worker の口の文が前向きな断りの決まりに合う
（穴を書かない・できませんで終えない・書いた命令が実在する）⑤Worker だけの符丁は Python の口に何も足さない、を縛る。
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from thth import cli, refusal
from thth import refusal_catalog as rc
from tests.command_helpers import command_problem
from tests.test_refusal_catalog import NEGATIVE_FIX_ENDING, _last_sentence

ROOT = Path(__file__).resolve().parent.parent
# 1 語の符丁（目録の符丁の形でないので載らない・Worker の答えは {error} のまま）。増えたら赤（載せるか決める）。
ONE_WORD = frozenset({"busy", "expired", "forbidden", "revealing", "unauthorized", "unverified", "used"})
SLOT = re.compile(r"<(口座|主体|名前|時刻)>")
COMMAND = re.compile(r"`(thth [^`]+)`")


@pytest.fixture(scope="module")
def render():
    spec = importlib.util.spec_from_file_location("render_refusals", ROOT / "tools" / "render_refusals.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def worker_codes(render):
    return render.worker_codes()


def test_Workerの目録の写しは生成物と一字も違わない(render):
    assert render.WORKER_JS.read_text(encoding="utf-8") == render.worker_js(), (
        "callback/src/refusal-catalog.js が目録と食い違う（python3 tools/render_refusals.py で作り直す）")
    assert render.stale() == []


def test_Workerのsourceの符丁はすべて目録のworkerの口にある(render, worker_codes):
    view = rc.door_view("worker")
    missing = sorted(code for code in worker_codes if render.CODE.fullmatch(code) and code not in view)
    assert missing == [], f"目録の worker の口に無い Worker の符丁（thth/refusal_catalog_part6.py に足す）: {missing}"
    assert {code for code in worker_codes if not render.CODE.fullmatch(code)} == ONE_WORD


def test_目録のworkerの口はWorkerのsourceにまだある(worker_codes):
    stale = sorted(code for code in rc.door_view("worker") if code not in worker_codes)
    assert stale == [], f"Worker がもう出さない符丁に worker の口がある（doors から外すか retired）: {stale}"


def test_Workerの符丁の数():
    view = rc.door_view("worker")
    assert len(view) == 50
    assert sum(1 for parts in view.values() if parts["better"]) == 47


def test_ほかの口にも出る符丁はWorkerの口の文を持つ():
    wrong = []
    for code, entry in rc.CATALOG.items():
        if "worker" not in entry["doors"] or set(entry["doors"]) == {"worker"}:
            continue
        by = entry["fix_by_door"].get("worker") or {}
        if not by.get("text") or "command" not in by:
            wrong.append(code)
    assert wrong == [], f"ほかの口の文は Worker の口で通る道と限らない（fix_by_door['worker'] に text と command を書く）: {wrong}"


def test_Workerの口の文は前向きな断りの決まりに合う():
    parser = cli.build_parser()
    wrong = []
    for code, parts in rc.door_view("worker").items():
        for key in ("why", "fix", "better"):
            text = parts[key] or ""
            if SLOT.search(text):
                wrong.append(f"{code} {key}: Worker は穴を埋めない（型の穴を書かない）: {text}")
            for command in COMMAND.findall(text):
                problem = command_problem(parser, command)
                if problem is not None:
                    wrong.append(f"{code} {key}: `{command}` — {problem}")
        if not parts["why"] or not parts["fix"]:
            wrong.append(f"{code}: why と fix は空にしない")
        if NEGATIVE_FIX_ENDING.search(_last_sentence(parts["fix"])):
            wrong.append(f"{code}（fix）: {parts['fix']}")
        entry = rc.CATALOG[code]
        if not parts["better"] and not entry["no_better"]:
            wrong.append(f"{code}: better が空なら no_better の理由が要る")
        by = entry["fix_by_door"].get("worker") or {}
        if by.get("command"):
            wrong.append(f"{code}: Worker の口の読み手は HTTP（command は None・打てる命令は文の中に `thth …` で）")
    assert wrong == []


def test_Workerだけの符丁はPythonの口に何も足さない():
    for code, entry in rc.CATALOG.items():
        if set(entry["doors"]) != {"worker"}:
            continue
        assert refusal.parts(code) is None, code
        assert refusal.tail(code, f"{code}: x") == [], code
        assert refusal.fields(code) == {}, code
        assert refusal.mcp_text(code) == code, code


def test_Workerの口の文はPythonの口の文に混ざらない():
    """ほかの口にも出る符丁は、Python の口（cli・remote・mcp）では今までの文のまま（Worker の口の文を出さない）。"""
    for code, entry in rc.CATALOG.items():
        by = entry["fix_by_door"].get("worker")
        if not by:
            continue
        worker = rc.door_parts(code, "worker")
        for door in ("cli", "remote", "mcp", None):
            found = refusal.parts(code, door=door)
            assert found is not None, code
            if by.get("why") and by["why"] != entry["why"]:
                assert found["why"] != worker["why"], (code, door)
            if by["text"] != entry["fix"]["text"]:
                assert worker["fix"] not in found["fix"], (code, door)


def test_壊れた例_目録を直して作り直さないと赤(render, monkeypatch):
    """④ の柵が本当に赤になる: 目録の Worker の口の文を 1 つ変えると、生成物と食い違う。"""
    entry = rc.CATALOG["media_expired"]
    monkeypatch.setitem(entry, "why", entry["why"] + "（わざと壊した）")
    assert render.WORKER_JS in render.stale()
    assert render.WORKER_JS.read_text(encoding="utf-8") != render.worker_js()
