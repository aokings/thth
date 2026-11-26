"""対応表 thth.workspaces/1 の柵（設計_THTHアカウント（括り）_共通の層 §7「台帳の形」「台帳の約束」）。

contracts/thth.workspaces.1/cases.json は hub と h2d の試験も読む見本。ここが答えの正本。
"""
from __future__ import annotations

import json
import pathlib

import pytest

from thth import workspaces_map as wm

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES = json.loads((ROOT / "contracts" / "thth.workspaces.1" / "cases.json").read_text(encoding="utf-8"))["cases"]


def _materialize(case, tmp_path):
    p = tmp_path / "workspaces.json"
    if case.get("file_text") is not None:
        p.write_text(case["file_text"], encoding="utf-8")
    elif case.get("file") is not None:
        p.write_text(json.dumps(case["file"], ensure_ascii=False), encoding="utf-8")
    return p


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_contract_cases(case, tmp_path):
    p = _materialize(case, tmp_path)
    if not case["valid"]:
        with pytest.raises(wm.WorkspacesMapError):
            wm.load(p)
        return
    m = wm.load(p)
    for medium, want in case.get("workspace_of", {}).items():
        assert m.workspace_of(medium) == want, medium
    for a, b, want in case.get("same_workspace", []):
        assert m.same_workspace(a, b) is want, (a, b)
    for ws, want in case.get("owner_of", {}).items():
        assert m.owner_of(ws) == want, ws
    for ws, want in case.get("lead_of", {}).items():
        assert m.lead_of(ws) == want, ws


def test_env_var_selects_path(tmp_path, monkeypatch):
    p = tmp_path / "x.json"
    wm.write({"schema": wm.SCHEMA, "unlisted": None,
              "workspaces": {"main": {"owner": "masaru", "lead": "hubby"}},
              "media": {"alpha": "main"}}, p)
    monkeypatch.setenv(wm.ENV_PATH, str(p))
    m = wm.load()
    assert m.present and m.workspace_of("alpha") == "main" and m.workspace_of("beta") is None


def test_missing_file_is_default_main(tmp_path):
    m = wm.load(tmp_path / "none.json")
    assert not m.present and m.workspace_of("anything") == "main"


@pytest.mark.parametrize("value", ["sk-abcdef", "ghp_x", "Bearer abc", "my token", "a@example.com"])
def test_secret_like_values_are_refused(value):
    data = {"schema": wm.SCHEMA, "unlisted": "main",
            "workspaces": {"main": {"owner": "masaru", "lead": "hubby"}}, "media": {}}
    data["updated_at"] = value
    assert any("秘密" in e for e in wm.validate(data))


def test_unknown_workspace_field_refused():
    data = {"schema": wm.SCHEMA, "unlisted": "main",
            "workspaces": {"main": {"owner": "masaru", "lead": "hubby", "phone": "x"}}, "media": {}}
    assert wm.validate(data)


def test_write_refuses_bad_and_leaves_old(tmp_path):
    p = tmp_path / "w.json"
    good = {"schema": wm.SCHEMA, "unlisted": "main",
            "workspaces": {"main": {"owner": "masaru", "lead": "hubby"}}, "media": {"alpha": "main"}}
    wm.write(good, p)
    before = p.read_bytes()
    with pytest.raises(wm.WorkspacesMapError):
        wm.write({**good, "media": {"alpha": "nowhere"}}, p)
    assert p.read_bytes() == before
    assert sorted(x.name for x in tmp_path.iterdir()) == ["w.json"]  # 書きかけが残らない
