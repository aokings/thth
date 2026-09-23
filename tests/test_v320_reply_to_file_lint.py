"""3.2.0 `reply_to_file` の lint（設計 3.2.0 §1・§6「lint 6 種」）。

返信先を post_id ではなく**同じ queue の原稿の名前**で書く。書き方が誤っていれば
loud に断る（併用・自己参照・不在・account 違い・名前の形・束）。**指した原稿が
まだ出ていないこと自体は断らない**——それは待つのであって誤りではない（§2）。
"""
from __future__ import annotations

import os

import pytest

from tests.conftest import write_queue_file
from thth import lint as lint_mod
from thth import queuefile

Q = "2026-09-23-q-decaf.md"
A = "2026-09-24-a-decaf.md"


def _errors(path):
    return [e for e in lint_mod.lint_file(path) if not lint_mod.is_warning(e)]


def _pair(tmp_path, *, answer_fm=None, question_fm=None, question_text=None):
    queue = str(tmp_path / "queue")
    if question_text is not None:
        os.makedirs(queue, exist_ok=True)
        with open(os.path.join(queue, Q), "w", encoding="utf-8") as f:
            f.write(question_text)
    else:
        write_queue_file(queue, Q, commit=False,
                         fm_overrides={"status": "draft", **(question_fm or {})},
                         body="## threads\n\n今日の問い。\n")
    return write_queue_file(queue, A, commit=False,
                            fm_overrides={"status": "draft", "reply_to_file": Q,
                                          **(answer_fm or {})},
                            body="## threads\n\n明日の答え。\n")


def test_まだ出ていない同じaccountの原稿を指すのは通る(isolated_account, tmp_path):
    """指した原稿が draft（post_id 無し）でも lint は通す——待つのは誤りではない。"""
    assert _errors(_pair(tmp_path)) == []


def test_reply_toとの併用は断る(isolated_account, tmp_path):
    path = _pair(tmp_path, answer_fm={"reply_to": "18000000000000001"})
    errors = _errors(path)
    assert any(e.startswith("reply_to_conflict: ") for e in errors), errors
    assert lint_mod.reason_code(errors) == "reply_to_conflict"


def test_自分自身を指すのは断る(isolated_account, tmp_path):
    path = _pair(tmp_path, answer_fm={"reply_to_file": A})
    errors = _errors(path)
    assert any(e.startswith("reply_to_file_self: ") for e in errors), errors
    assert lint_mod.reason_code(errors) == "reply_to_file_self"


def test_指した原稿が無いのは断る(isolated_account, tmp_path):
    path = _pair(tmp_path, answer_fm={"reply_to_file": "2026-09-22-nothing.md"})
    errors = _errors(path)
    assert any(e.startswith("reply_to_file_missing: ") for e in errors), errors
    assert lint_mod.reason_code(errors) == "reply_to_file_missing"


def test_指した原稿のaccountが違うのは断る(isolated_account, tmp_path):
    path = _pair(tmp_path, question_fm={"account": "kopicha-threads"})
    errors = _errors(path)
    assert any(e.startswith("reply_to_file_account_mismatch: ") for e in errors), errors
    assert lint_mod.reason_code(errors) == "reply_to_file_account_mismatch"


@pytest.mark.parametrize("name", [
    "../" + Q, "sub/" + Q, "sub\\" + Q, "2026-09-23-q-decaf.txt", ".md",
    ".hidden.md", "a..b.md",
])
def test_名前の形が違うのは断る(isolated_account, tmp_path, name):
    path = _pair(tmp_path, answer_fm={"reply_to_file": name})
    errors = _errors(path)
    assert any(e.startswith("reply_to_file_invalid: ") for e in errors), (name, errors)
    assert lint_mod.reason_code(errors) == "reply_to_file_invalid"


def test_reply_toにfile印を書くのは断る(isolated_account, tmp_path):
    """`reply_to: file:<名前>` は reply_to_file と同じ指紋になるので書かせない（§3）。"""
    queue = str(tmp_path / "queue")
    path = write_queue_file(queue, A, commit=False,
                            fm_overrides={"status": "draft", "reply_to": "file:" + Q})
    errors = _errors(path)
    assert lint_mod.reason_code(errors) == "reply_to_file_invalid", errors


def test_束の原稿は指せない(isolated_account, tmp_path):
    bundle_text = ("---\nthth: 2\naccount: nigamilab-threads\n"
                   "publish_at: 2026-09-23T23:00:00+09:00\nstatus: draft\n---\n"
                   "## threads\n\n一段目\n")
    path = _pair(tmp_path, question_text=bundle_text)
    errors = _errors(path)
    assert any(e.startswith("reply_to_file_bundle_unsupported: ") for e in errors), errors
    assert lint_mod.reason_code(errors) == "reply_to_file_bundle_unsupported"


def test_指した原稿が型外なら読めないと言う(isolated_account, tmp_path):
    path = _pair(tmp_path, question_text="front-matter の無い素の原稿\n")
    errors = _errors(path)
    assert any(e.startswith("reply_to_file_unreadable: ") for e in errors), errors


def test_指した原稿がさらにreply_to_fileを持っていてもよい(isolated_account, tmp_path):
    """答えへの補足（指した原稿がさらに reply_to_file を持つ）は通る（§1）。"""
    queue = str(tmp_path / "queue")
    _pair(tmp_path)
    extra = write_queue_file(queue, "2026-09-25-extra.md", commit=False,
                             fm_overrides={"status": "draft", "reply_to_file": A},
                             body="## threads\n\n補足。\n")
    assert _errors(extra) == []


def test_理由の符丁はREASONSに閉じている():
    for code in queuefile.REPLY_TO_FILE_MESSAGES:
        assert code in lint_mod.REASONS
