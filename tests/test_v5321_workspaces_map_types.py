"""監査 5.32.0 の軽い 10（5.32.1）: 対応表の検めは、型の違う値でも落ちずに形の誤りとして返す。

`unlisted` や `media` の値が配列・object だと、`in` で辞書を引いて TypeError で落ちていた（`load` も
`WorkspacesMapError` にならなかった）。
"""
from __future__ import annotations

import json

import pytest

from thth import workspaces_map as wm

BASE = {"schema": wm.SCHEMA, "unlisted": "main", "workspaces": {"main": {"owner": "masaru", "lead": "media-hub"}},
        "media": {}}


@pytest.mark.parametrize("patch,word", [
    ({"unlisted": []}, "unlisted"), ({"unlisted": {}}, "unlisted"), ({"unlisted": 1}, "unlisted"),
    ({"media": {"a": []}}, "media.a"), ({"media": {"a": {}}}, "media.a"), ({"media": {"a": 3}}, "media.a"),
])
def test_型の違う値は誤りとして返し落ちない(patch, word, tmp_path):
    data = {**BASE, **patch}
    errs = wm.validate(data)
    assert errs and any(word in e for e in errs), errs
    path = tmp_path / "workspaces.json"
    path.write_text(json.dumps(data))
    with pytest.raises(wm.WorkspacesMapError):
        wm.load(path)


def test_正しい表は今までどおり通る():
    assert wm.validate({**BASE, "media": {"kopicha": "main"}}) == []
