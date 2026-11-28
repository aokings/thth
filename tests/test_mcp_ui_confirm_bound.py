"""確定のカードの柵: 1 段目の結果から写した reason を 1 字でも変えると、2 段目で断られる。

第24 11/27 #9961: 確定の引数は reason を 1 段目の結果から写す（控えを持たない）。
digest の材料に reason があるので、写し違い・書き換えは 2 段目で `confirm_mismatch`。
画面に文字の欄が 1 つ残っても「確かめた物と違う物は確定できない」ことをここで縛る。
柵: digest の材料から reason を抜くと赤。
"""
from __future__ import annotations

import pytest

from tests.test_retract_owner_request_remote import call, medium, refused  # noqa: F401
from tests.test_v212_server_writes import env, remote  # noqa: F401
from tests.test_v3120_guard import credential_id, ledger  # noqa: F401
from thth import approval


@pytest.mark.parametrize("changed", ["誤字。", "誤記", " 誤字x", "誤"])
def test_写したreasonを1字変えると2段目で断られ消えない(env, remote, medium, changed):
    first = call(env, post_id="777", reason="誤字")
    assert first["status"] == "confirm_required"
    assert refused(env, post_id="777", reason=changed, confirm=first["digest"]) == "confirm_mismatch"
    assert medium.deletes == []
    done = call(env, post_id="777", reason="誤字", confirm=first["digest"])
    assert done["status"] == "retracted" and medium.deletes == ["777"]


def test_記録のある投稿の番号もreasonで変わる():
    a = approval.compute_retract_digest(post_id="1", account="alpha", reason="誤字")
    b = approval.compute_retract_digest(post_id="1", account="alpha", reason="誤記")
    assert a != b
