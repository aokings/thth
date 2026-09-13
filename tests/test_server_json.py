"""`server.json` と README の marker（設計 v2-4 §3・4-2）。

MCP registry に登録するときに読まれる 2 つを固定する:
  - repo の根の `server.json`（`name`・`version`・`packages[]`）。
  - `pyproject.toml` の `readme` が指す `README.md` の **`mcp-name:` の 1 行**
    （PyPI 側の所有確認・quickstart の記述・**L2**）。

**`version` は `thth/VERSION` から動的に読まない。** JSON に数字を書き写して、
ここで一致を強制する——`thth/VERSION` を上げたらこの試験が落ちるべきだから
（tag のときに一緒に上げる、を人の記憶ではなく機械に守らせる）。動的に読むと、
版が割れたまま `mcp-publisher publish` が通り、**registry に古い版が載る**。
"""
from __future__ import annotations

import json
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_JSON = os.path.join(REPO_ROOT, "server.json")
MARKER = "mcp-name: io.github.aokings/thth"


def _server() -> dict:
    with open(SERVER_JSON, encoding="utf-8") as f:
        return json.load(f)


def _version() -> str:
    with open(os.path.join(REPO_ROOT, "thth", "VERSION"), encoding="utf-8") as f:
        return f.read().strip()


def _pyproject_name() -> str:
    """`[project] name = "…"` を読む（tomllib は 3.11 から・3.10 も動かす）。"""
    with open(os.path.join(REPO_ROOT, "pyproject.toml"), encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"(?m)^\s*name\s*=\s*\"([^\"]+)\"", text)
    assert m, "pyproject.toml に project.name がありません"
    return m.group(1)


def _readme_path() -> str:
    with open(os.path.join(REPO_ROOT, "pyproject.toml"), encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"(?m)^\s*readme\s*=\s*\"([^\"]+)\"", text)
    assert m, "pyproject.toml に project.readme がありません"
    return os.path.join(REPO_ROOT, m.group(1))


def test_server_jsonがJSONとして読める():
    data = _server()
    assert isinstance(data, dict), data
    assert data["$schema"] == (
        "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json")


def test_nameはgithubのnamespace():
    """`io.github.<org>/<name>`。所有は `mcp-publisher login github` で確かめる。"""
    assert _server()["name"] == "io.github.aokings/thth"


def test_versionはthth_VERSIONと同じ():
    """**ここが落ちたら `server.json` の 2 か所を上げる**（`version` と
    `packages[0].version`）。動的に読まないのはそのため（module docstring）。"""
    assert _server()["version"] == _version(), (
        f"server.json の version が thth/VERSION と違います: "
        f"{_server()['version']} != {_version()}")


def test_packagesはpypiのthth():
    pkg = _server()["packages"][0]
    assert pkg["registryType"] == "pypi"
    assert pkg["registryBaseUrl"] == "https://pypi.org"
    # **配る名前そのもの**（`pip install <ここ>` が通らなければ registry の案内が嘘になる）。
    assert pkg["identifier"] == _pyproject_name(), (
        f"server.json の identifier が pyproject の name と違います: "
        f"{pkg['identifier']} != {_pyproject_name()}")
    assert pkg["version"] == _version(), (
        f"packages[0].version が thth/VERSION と違います: "
        f"{pkg['version']} != {_version()}")
    assert pkg["runtimeHint"] == "uvx"
    assert pkg["transport"] == {"type": "stdio"}


def test_repositoryはgithub():
    repo = _server()["repository"]
    assert repo == {"url": "https://github.com/aokings/thth", "source": "github"}


def test_descriptionは1文の英語():
    d = _server()["description"]
    assert d and d.endswith("."), d
    assert d.count(".") == 1, f"1 文にしてください（`.` が {d.count('.')} 個）: {d}"
    assert not re.search(r"[ぁ-んァ-ン一-龥]", d), f"英語で書いてください: {d}"


def test_READMEにmcp_nameのmarkerがある():
    """**PyPI 側の所有確認**（設計 v2-4 §3）。`pyproject.toml` の `readme` が指す
    ファイルに、この 1 行が**そのまま**在ること。"""
    path = _readme_path()
    with open(path, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f]
    assert MARKER in lines, (
        f"{os.path.relpath(path, REPO_ROOT)} に `{MARKER}` の行がありません"
        f"（消すと mcp-publisher publish が通りません）")
