"""断りの目録の柵（設計「監査と審査の機械化と前向きな断り」§3・段 A）。

原則（docs/原則_前向きな断り.md）: 断るときは なぜ・どうすれば通るか・どうすればもっと良くなるか を出す。
新しい断りの符丁を足して目録に載せなければ、ここが赤になる。
"""
from __future__ import annotations

import ast
import importlib
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

# no_better の数の上限（段 F・版ごとに下げる。上げない）。版を上げたら、その版の行を足す
# （前の版より上げない・今の数と同じにする）。better を足して数が減ったら、その版の上限も下げる。
NO_BETTER_LIMITS = {
    "5.11.1": {"internal": 196, "terminal": 13},
    "5.12.0": {"internal": 196, "terminal": 13},
    "5.12.1": {"internal": 196, "terminal": 13},
    "5.12.2": {"internal": 196, "terminal": 13},
    "5.12.3": {"internal": 196, "terminal": 13},
    "5.12.4": {"internal": 196, "terminal": 13},
    "5.12.5": {"internal": 196, "terminal": 13},
    "5.12.6": {"internal": 196, "terminal": 13},
    "5.13.0": {"internal": 196, "terminal": 13},
    "5.14.0": {"internal": 196, "terminal": 13},
    "5.14.1": {"internal": 196, "terminal": 13},
    "5.14.2": {"internal": 196, "terminal": 13},
    "5.14.3": {"internal": 196, "terminal": 13},
    "5.14.4": {"internal": 196, "terminal": 13},
    "5.14.5": {"internal": 196, "terminal": 13},
    "5.15.0": {"internal": 196, "terminal": 13},
    "5.16.0": {"internal": 196, "terminal": 13},
    "5.17.0": {"internal": 196, "terminal": 13},
    "5.18.0": {"internal": 196, "terminal": 13},
    "5.18.1": {"internal": 196, "terminal": 13},
    "5.18.2": {"internal": 196, "terminal": 13},
    "5.19.0": {"internal": 196, "terminal": 13},
    "5.20.0": {"internal": 196, "terminal": 13},
}
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


def _version_key(version):
    return tuple(int(part) for part in version.split("."))


def _current_version():
    return (ROOT / "thth" / "VERSION").read_text(encoding="utf-8").strip()


def test_no_betterの上限は今の版の行がある():
    version = _current_version()
    assert version in NO_BETTER_LIMITS, (
        f"版 {version} の no_better の上限が無い。NO_BETTER_LIMITS に {version} の行を足す"
        f"（前の版より上げない・今の数 {rc.counts()['no_better']} にそろえる）")


def test_no_betterの上限は版ごとに上がらない():
    rows = sorted(NO_BETTER_LIMITS.items(), key=lambda item: _version_key(item[0]))
    for (before, low), (after, high) in zip(rows, rows[1:]):
        for reason in rc.NO_BETTER:
            assert high.get(reason, 0) <= low.get(reason, 0), (
                f"no_better: {reason} の上限が {before} の {low.get(reason, 0)} から {after} で "
                f"{high.get(reason, 0)} に上がった（better を足して数を下げる）")


def test_no_betterの数は上限を超えない():
    counts = rc.counts()["no_better"]
    limit = NO_BETTER_LIMITS.get(_current_version(), {})
    for reason in rc.NO_BETTER:
        n, cap = counts.get(reason, 0), limit.get(reason, 0)
        assert n <= cap, (f"no_better: {reason} が {n}（上限 {cap}）。足した断りに better を書く"
                          f"（thth/refusal_catalog_part*.py）か、better を書けない理由を見直す")


def test_no_betterの上限は今の数まで下げてある():
    """数が減ったら上限も下げる（上限に余りを残すと、あとで黙って増やせる）。"""
    counts = rc.counts()["no_better"]
    limit = NO_BETTER_LIMITS.get(_current_version(), {})
    loose = {reason: (counts.get(reason, 0), cap) for reason, cap in limit.items()
             if counts.get(reason, 0) < cap}
    assert loose == {}, f"no_better の上限を今の数まで下げる（今の数, 上限）: {loose}"


def test_項目の形():
    for code, entry in rc.CATALOG.items():
        assert re.fullmatch(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+", code), code
        assert entry["kind"] in rc.KINDS, code
        assert entry["doors"] and set(entry["doors"]) <= set(rc.DOORS), code
        assert entry["since"] and entry["lang"] == "ja", code


def test_retiredの無い符丁が消えたら赤(source_codes):
    assert len(rc.CATALOG) >= CATALOG_FLOOR, "目録の項目を消した（消すなら retired を立てて残す）"
    # Worker の source（`callback/src`）の符丁も source に数える（`tools/render_refusals.py` の棚卸し）。
    worker_codes = set(_render_refusals().worker_codes())
    gone = sorted(code for code, entry in rc.CATALOG.items()
                  if not entry["retired"] and code not in source_codes | worker_codes)
    assert gone == [], f"ソースから消えた符丁（互換のため retired を立てる）: {gone}"


def test_目録のfixは見方の優先の文と同じ():
    """目録の `fix.text` は、`FIX_VIEWS` の順で先に来る見方の文（部の `fix`）。"""
    texts = rc.fix_texts()
    wrong = [code for code, found in texts.items() if rc.CATALOG[code]["fix"]["text"] != found[0][1]]
    assert wrong == []


def _part_entry(code: str) -> dict:
    found = [part.ENTRIES[code] for part in rc.PARTS if code in part.ENTRIES]
    assert len(found) == 1, code
    return found[0]


# 見方を持つモジュールの辞書に、目録に無い鍵として残してよいもの（欄の名前・符丁の形でない）。
NON_CODE_VIEW_KEYS = {"accounts._DUMMY_NEXT": {"handle", "instance"}}


def _view_table(view: str):
    module_name, _, name = view.partition(".")
    return getattr(importlib.import_module(f"thth.{module_name}"), name)


def test_見方の値は部に書いた文と一致する():
    """段 A の 2 つ目: 各モジュールの説明の辞書（`remote.NEXT` など）は目録の見方。値は部の `fix`
    （その見方の `fix_by_view` があればそれ）と 1 字も違わず、鍵の順は `FIX_VIEWS` の順。"""
    wrong = []
    for view, codes in rc.FIX_VIEWS.items():
        table = _view_table(view)
        for code in codes:
            entry = _part_entry(code)
            expected = (entry.get("fix_by_view") or {}).get(view, entry.get("fix"))
            if table.get(code) != expected or not expected:
                wrong.append(f"{view} {code}")
        extra = set(table) - set(codes)
        if extra - NON_CODE_VIEW_KEYS.get(view, set()):
            wrong.append(f"{view} の辞書に目録に無い鍵: {sorted(extra)}")
        listed = [key for key in table if key in set(codes)]
        if listed != list(codes):
            wrong.append(f"{view} の鍵の順が FIX_VIEWS と違う")
    assert wrong == []


def test_部のfix_by_viewは見方に出る符丁の違う文だけ():
    """5.11.0 段 8 監査の柵（使われない写しを置かない）を、正本が部に移った形で保つ。`fix_by_view` は
    その符丁を並べた見方の鍵だけで、`fix` と違う文だけを書く（同じ文・出ない見方の文は使われない写し）。
    見方に出る符丁は部の `fix` が空でない。"""
    texts = rc.fix_texts()
    wrong = []
    for part in rc.PARTS:
        for code, entry in part.ENTRIES.items():
            views = {view for view, _ in texts.get(code, [])}
            for view, text in (entry.get("fix_by_view") or {}).items():
                if view not in views or text == entry.get("fix") or not text:
                    wrong.append(f"{code}: fix_by_view {view}")
            if views and not entry.get("fix"):
                wrong.append(f"{code}: 見方に出るのに部の fix が空")
    assert wrong == []


_FIX_DICT_NAME = re.compile(r"(NEXT|MESSAGES|HINTS)")
# 説明の辞書の名でも、断りの符丁の文でないもの（目録の外に置いてよい・理由を添える）。
FIX_DICT_OUTSIDE = {
    "thth/maintain.py:_MESSAGES": "token の状態（ok・expired …）の言い分で、断りの符丁の通す直し方ではない",
}
_CODE_KEY = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+\Z")


def _fix_dict_writes(path: Path) -> list[str]:
    """説明の辞書の名（NEXT・MESSAGES・HINTS を含む）に、符丁の鍵（字面か定数の名）の辞書を直に書く所。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rel = path.relative_to(ROOT)
    found = []

    def code_keys(node):
        return [k for k in node.keys
                if (isinstance(k, ast.Constant) and isinstance(k.value, str) and _CODE_KEY.fullmatch(k.value))
                or isinstance(k, (ast.Name, ast.Attribute))]

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
                if isinstance(target, ast.Subscript):
                    base = target.value
                    name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
                    if name and _FIX_DICT_NAME.search(name):
                        found.append(f"{rel}:{node.lineno} {name}[…] = …")
                elif name and _FIX_DICT_NAME.search(name) and isinstance(node.value, ast.Dict) \
                        and code_keys(node.value) and f"{rel}:{name}" not in FIX_DICT_OUTSIDE:
                    found.append(f"{rel}:{node.lineno} {name} = {{…}}")
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr in ("update", "setdefault")):
            base = node.func.value
            name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
            if name and _FIX_DICT_NAME.search(name):
                found.append(f"{rel}:{node.lineno} {name}.{node.func.attr}(…)")
    return found


def test_説明の辞書はモジュールに直に書かない():
    """通す直し方の文の正本は目録の部の 1 か所。モジュールに `NEXT = {"符丁": "文"}`・`NEXT.update(…)`・
    `NEXT["符丁"] = …` を書くと赤（文は部の `fix` に書き、符丁を `FIX_VIEWS` に並べて
    `refusal_catalog.fix_view(…)` で引く）。"""
    found = []
    for path in sorted((ROOT / "thth").rglob("*.py")):
        if "__pycache__" in path.parts or path.name.startswith("refusal_catalog"):
            continue
        found.extend(_fix_dict_writes(path))
    assert found == [], f"説明の辞書を直に書いた所（目録の部へ移す）: {found}"


def test_見方は各モジュールでfix_viewから作る():
    """`FIX_VIEWS` の見方ごとに、そのモジュールの名が `refusal_catalog.fix_view("<見方>")` で作られている。"""
    wrong = []
    for view in rc.FIX_VIEWS:
        module_name, _, name = view.partition(".")
        tree = ast.parse((ROOT / "thth" / f"{module_name}.py").read_text(encoding="utf-8"))
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "fix_view"
                 and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == view]
        assigned = [node for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
                    and any(sub in calls for sub in ast.walk(node.value))]
        if len(assigned) != 1:
            wrong.append(view)
    assert wrong == []


def test_見方は読むだけ():
    table = _view_table("remote.NEXT")
    with pytest.raises(TypeError):
        table["new_code"] = "文"


# 置き換えの前に f 文字列で定数を埋めていた文（見方では部の字面）。定数を変えたら部の文も直す。
VIEW_CONSTANTS = (
    ("invites.NEXT", "invalid_project", ("PROJECT_MAX",)),
    ("invites.NEXT", "invalid_label", ("LABEL_MAX",)),
    ("invites.NEXT", "invalid_expires", ("MAX_DAYS",)),
    ("report_inbox.NEXT", "report_too_long", ("TITLE_MAX", "BODY_MAX", "REPRO_MAX", "REPLY_MAX")),
    ("report_inbox.NEXT", "report_rate_limited", ("DAILY_LIMIT",)),
    ("map_store.NEXT", "invalid_node", ("NODE_MAX_CHARS",)),
    ("map_store.NEXT", "node_limit", ("MAX_NODES",)),
    ("map_store.NEXT", "edge_limit", ("MAX_EDGES",)),
    ("map_store.NEXT", "invalid_retention", ("MAX_RETENTION_DAYS",)),
    ("plaza.NEXT", "post_too_long", ("TITLE_MAX", "BODY_MAX", "REPLY_MAX", "TEXT_MAX", "REASON_MAX")),
    ("plaza.NEXT", "plaza_rate_limited", ("DAILY_LIMIT",)),
    ("plaza.NEXT", "invalid_owner", ("OWNER_PROJECTS_MAX",)),
    ("plaza.NEXT", "scope_required", ("SCOPE_NOTE_MAX",)),
    ("plaza.NEXT", "invalid_how", ("HOW_MAX",)),
    ("hatena_entry._MESSAGES", "categories_invalid", ("CATEGORIES_MAX", "CATEGORY_CHARS_MAX")),
)


def test_見方の文はモジュールの定数の今の値を言う():
    wrong = []
    for view, code, names in VIEW_CONSTANTS:
        module = importlib.import_module(f"thth.{view.partition('.')[0]}")
        text = _view_table(view)[code]
        for name in names:
            value = getattr(module, name)
            if not re.search(rf"(?<!\d){value}(?!\d)", text):
                wrong.append(f"{view} {code}: {name}={value}")
    assert wrong == []


def test_queuefileの見方の鍵はREPLY_TOの定数():
    """`REPLY_TO_FILE_MESSAGES` は定数（`REPLY_TO_CONFLICT` など）を鍵にしていた。見方の鍵が同じ値の集合。"""
    constants = {value for name, value in vars(queuefile).items()
                 if name.startswith("REPLY_TO_") and name != "REPLY_TO_FILE_KEY"  # 欄の名前
                 and isinstance(value, str) and _CODE_KEY.fullmatch(value)}
    assert set(queuefile.REPLY_TO_FILE_MESSAGES) == constants


def test_目録のwhyは安全装置の文と食い違わない():
    for code, entry in rc.CATALOG.items():
        why = rc.guard_why(code)
        if why is not None:
            assert entry["why"] == why, code
            assert why in core.guard_refusal_message("<口座>", rc._Refused(code)), code


def _render_refusals():
    spec = importlib.util.spec_from_file_location("render_refusals", ROOT / "tools" / "render_refusals.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ダッシュボードのTHTH_REASONは目録と一致する():
    """④ 4 本の `THTH_REASON` は `tools/render_refusals.py` が目録から作った表と一字も違わない。"""
    render = _render_refusals()
    expected = render.outputs()
    for path in DASHBOARDS:
        assert path.read_text(encoding="utf-8") == expected[path], (
            f"{path.name} の THTH_REASON が目録と食い違う（python3 tools/render_refusals.py で作り直す）")
        body = re.search(r"const THTH_REASON=\{(.*?)\};", path.read_text(encoding="utf-8")).group(1)
        assert dict(re.findall(r'(\w+):"([^"]*)"', body)) == rc.dashboard_view(), path.name
    for code, why in rc.DASHBOARD_REASON_JA.items():
        assert rc.CATALOG[code]["why"] == why, code
    assert set(rc.dashboard_view()) == set(rc.DASHBOARD_REASON_JA)


def test_ダッシュボードの表の生成は表の1行だけを変える():
    render = _render_refusals()
    for path in DASHBOARDS:
        html = path.read_text(encoding="utf-8")
        broken = html.replace('no_link:"リンクが無い"', 'no_link:"リンクがない"')
        assert broken != html
        fixed = render.render_dashboard(broken)
        assert fixed == html, path.name


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
    good = rc.render("too_soon", at="2026-11-03T10:00+09:00")  # 段 B の直し: 時刻はタイムゾーンまでそろった形だけ
    assert "2026-11-03T10:00+09:00" in good["command"]
    assert "<時刻>" in rc.render("too_soon", at="2026-11-03T10:00")["command"]
    bad = rc.render("too_soon", at="明日の朝 /etc/passwd")
    assert "<時刻>" in bad["command"] and "passwd" not in bad["command"]
    assert rc.render("too_soon", body="本文")["command"] == rc.CATALOG["too_soon"]["fix"]["command"]
    first = rc.lines("daily_limit")[0]
    assert first.startswith("daily_limit: ")


def test_目録を作るときに台帳や置き場を読まない(monkeypatch):
    """目録は初めて引かれた時に作る（`__getattr__`）ので、断りの最中に作られうる。作る途中で口座の台帳を読むと、
    不正な名前の断りより先に置き場に触る（CI の分け方で test_v213_review_followups が赤になった・11/04）。
    順番に頼らず、毎回作り直して確かめる。"""
    from thth import accounts

    calls = []

    def forbidden(*a, **kw):
        # 呼ぶ側が `except Exception` で包む所もあるので、上げずに記録して後で照らす。
        calls.append(a)
        raise AssertionError(f"catalog build touched storage: {a!r}")

    monkeypatch.setattr(accounts, "load_account", forbidden)
    built = rc._build()
    assert calls == [], f"目録を作る途中で口座の台帳を読んだ: {calls}"
    assert built["bookmark_not_allowed_for_lane"]["why"]
