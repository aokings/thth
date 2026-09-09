"""送った本文を動かせない記録として残し、書き戻し前に照合する（外部レビュー §3・
受け入れ 9・10）。

本文 A を公開している最中に repo の本文が B に書き換わると、front-matter の
書き換えと自動マージが成立して「本文 B ＋ A の post_id」が repo に残ってしまう
——これを防ぐため、公開の直前に送る本文の hash を inflight に書き、公開に成功したら
`state/<account>/sent/<post_id>.json` に実際に送った本文そのものを記録し、
front-matter を書き換える**直前**にいまの repo の本文と突き合わせる。不一致なら
書き換えない・再公開しない・inflight を残したまま exit 1 で止める。**inflight を
残すことで、次の実行が同じファイルをもう一度出そうとするのを防ぐ**（受け入れ 10）。
"""
from __future__ import annotations

import datetime
import re

from tests.conftest import init_git_pair, make_queue_text, write_queue_file
from thth import accounts as accounts_mod
from thth import core
from thth import inflight as inflight_mod
from thth import sent as sent_mod
from thth.adapters import base as adapter_base

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")


def test_9と10_公開後に本文が変わると書き戻さず再公開もせずinflightが残り次回も止まる(
        isolated_account_factory):
    account = isolated_account_factory(production=True)
    path = write_queue_file(account["queue_dir"], "a.md")
    state_dir = accounts_mod.state_dir_for(account["name"])

    class _MutatingSpy:
        """公開が成立する最中に「remote で本文が書き換わった」ことを模す。"""

        def publish(self, post, *, dry_run, on_container_created=None):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            assert "本文です。" in text
            text = text.replace("本文です。", "誰かが書き換えた本文です。")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return adapter_base.PublishResult(
                post_id="P1", url=None, ts="2026-09-09T10:00:30+09:00")

    # ---- 受け入れ 9 ----
    result = core.throw_once(
        account["name"], production_flag=True,
        adapter_factory=lambda *_: _MutatingSpy(), now=NOW)

    assert result.exit_code == 1
    assert result.action == "inflight"
    assert result.post_id == "P1"
    assert result.error == "text_mismatch_before_writeback"

    # front-matter は書き換えていない: status は approved のまま、post_id は空。
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "status: approved" in text
    m = re.search(r"^post_id:[ \t]*(.*)$", text, re.M)
    assert m is not None and m.group(1).strip() == ""

    # inflight は残っている（post_id は書けている＝公開はできていた証拠）。
    left = inflight_mod.read(state_dir)
    assert left is not None
    assert left.get("post_id") == "P1"
    assert left.get("body_hash")

    # 送った本文そのものが正本として残っている（元の本文。書き換え後の本文ではない）。
    record = sent_mod.read(state_dir, "P1")
    assert record is not None
    assert "本文です。" in record["text"]
    assert "誰かが書き換えた" not in record["text"]
    assert record["body_hash"] == left["body_hash"]

    # ---- 受け入れ 10: 直後にもう一度走らせると inflight で止まる（二重投稿しない）----
    class _MustNotPublish:
        def publish(self, *a, **k):
            raise AssertionError("inflight が残っているのに publish を呼んだ（二重投稿）")

    result2 = core.throw_once(
        account["name"], production_flag=True,
        adapter_factory=lambda *_: _MustNotPublish(), now=NOW)
    assert result2.exit_code == 1
    assert result2.action == "inflight"

    # front-matter は依然として書き換わっていない。
    with open(path, encoding="utf-8") as f:
        text2 = f.read()
    assert text2 == text


def test_公開後に本文が変わらなければ普通に書き戻す(isolated_account_factory, tmp_path):
    """対照実験: 本文が変わらなければ通常どおり posted になる（回帰確認）。
    書き戻しは実際に git push まで行うので、`init_git_pair()`（隔離 bare origin）を使う。
    """
    seed_content = make_queue_text()
    pair = init_git_pair(tmp_path, seed_content=seed_content, seed_name="a.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True)
    path = pair["queue_dir"] + "/a.md"
    state_dir = accounts_mod.state_dir_for(account["name"])

    class _Spy:
        def publish(self, post, *, dry_run, on_container_created=None):
            return adapter_base.PublishResult(
                post_id="P2", url=None, ts="2026-09-09T10:00:30+09:00")

    result = core.throw_once(
        account["name"], production_flag=True, adapter_factory=lambda *_: _Spy(), now=NOW)

    assert result.exit_code == 0
    assert result.action == "post"
    assert result.post_id == "P2"

    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "status: posted" in text
    assert "post_id: P2" in text

    assert inflight_mod.read(state_dir) is None
    record = sent_mod.read(state_dir, "P2")
    assert record is not None
    assert "本文です。" in record["text"]
