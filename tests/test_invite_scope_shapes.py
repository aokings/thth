"""VM が招待に載せる Meta の権限は、どれも Worker の形の検査（`callback/src/invite-object.js` の `META_SCOPE`）を通る。

5.13.0 の監査（Fable）: `--review-scopes` の広告の招待で `commerce_account_read_settings` を載せたのに、Worker の型に
`commerce` の頭が無く 400 で断られていた。片方だけに権限を足すと、ここが赤になる。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from thth import scopes

INVITE_OBJECT = Path(__file__).resolve().parent.parent / "callback" / "src" / "invite-object.js"


def worker_meta_scope() -> re.Pattern:
    text = INVITE_OBJECT.read_text(encoding="utf-8")
    found = re.search(r"const META_SCOPE=/(.+?)/;", text)
    assert found, "invite-object.js に META_SCOPE が無い"
    return re.compile(found.group(1))


@pytest.mark.parametrize("media", ["facebook", "instagram", "ads"])
@pytest.mark.parametrize("review", [False, True])
def test_VMが載せるMetaの権限はWorkerの形を通る(media, review):
    pattern = worker_meta_scope()
    rejected = [s for s in scopes.meta_invite_scopes(media, review=review) if not pattern.fullmatch(s)]
    assert rejected == [], f"Worker の META_SCOPE が断る権限（{media}・review={review}）: {rejected}"
