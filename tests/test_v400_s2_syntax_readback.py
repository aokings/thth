"""4.0 S2 §5（送る直前と 201 の応答の記法の確かめ）と §6（読み戻し）。T-S2-5a の一部・5b・6a・6b。

台帳の `blogs[].syntax` の段（`blog_syntax_unsupported`）は別の書き手の範囲（ここでは見ない）。
"""
from __future__ import annotations

import pytest

from tests.hatena_s1_flow_helpers import ACCOUNT, NOW, approve, call, fm_of, lane  # noqa: F401
from tests.hatena_s1_helpers import world  # noqa: F401
from tests.hatena_s2_http import (COLLECTION, blog_handler, entry_xml, fake_http, feed_xml,  # noqa: F401
                                  put_secrets, sent_content)
from thth import approval, core, deliveries, inflight, sent

ARTICLE = "# アラビカ\n\n値動きのまとめ。\n\n終わり。\n"


def throw():
    return core.throw_once(ACCOUNT, production_flag=True, now=NOW)


def states(delivery):
    return [row["state"] for row in deliveries.read(ACCOUNT, delivery)["history"]]


@pytest.fixture
def blog(lane, fake_http):  # noqa: F811
    lane.write("articles/a.md", ARTICLE)
    put_secrets(lane.world)
    lane.install = fake_http
    return lane


def approved(lane, name="a.md", **kwargs):
    path = lane.queue(name, **kwargs)
    approve(path)
    return path, fm_of(path)["delivery"]


# ---------------------------------------------------------------- T-S2-5a（送る直前）

@pytest.mark.parametrize("latest", ["text/x-hatena-syntax", "text/html"])
def test_T_S2_5a_一覧の最新がMarkdownでなければblog_syntax_mismatchでPOSTしない(blog, latest):
    """柵: `throw_chosen` の直前の確かめ（3b）を消すと赤（POST が呼ばれる）。"""
    feed = feed_xml([entry_xml(num="2", content_type=latest), entry_xml(num="1")])
    http = blog.install(blog_handler(feed=(200, feed)))
    path, delivery = approved(blog)
    lines = []
    result = core.throw_once(ACCOUNT, production_flag=True, now=NOW, log=lines.append)
    assert (result.action, result.error, result.exit_code) == ("refused", "blog_syntax_mismatch", 2)
    assert http.methods() == [("GET", COLLECTION)]
    assert states(delivery) == ["wanted"] and inflight.read(blog.state) is None
    assert any("編集モードを Markdown" in line for line in lines)


def test_T_S2_5a_記事が0件なら確かめられず進む(blog):
    http = blog.install(blog_handler(feed=(200, feed_xml([]))))
    path, delivery = approved(blog)
    result = throw()
    assert (result.action, result.exit_code) == ("post", 0), result
    assert http.methods() == [("GET", COLLECTION), ("POST", COLLECTION)]


@pytest.mark.parametrize("feed", [(500, b""), (401, b""), (200, b"<broken"), TimeoutError("slow")])
def test_T_S2_5a_一覧のGETが失敗すればsyntax_check_failedで次のrun(blog, feed):
    http = blog.install(blog_handler(feed=feed))
    path, delivery = approved(blog)
    result = throw()
    assert (result.action, result.error, result.exit_code) == ("syntax_check_failed", "syntax_check_failed", 1)
    assert [m for m, _u in http.methods()] == ["GET"]
    assert states(delivery) == ["wanted"] and inflight.read(blog.state) is None
    # 次の run は一覧が読めれば出る
    blog.install(blog_handler())
    assert throw().action == "post"


def test_T_S2_5a_削除は記法を見ない(blog):
    http = blog.install(blog_handler())
    path, delivery = approved(blog)
    assert throw().action == "post"
    approved(blog, "d.md", op="delete", delivery=delivery)
    http = blog.install(blog_handler(feed=(200, feed_xml([entry_xml(content_type="text/html")]))))
    assert throw().action == "post"
    assert [m for m, _u in http.methods()] == ["DELETE"]


# ---------------------------------------------------------------- T-S2-5b（201 の応答の型）

def test_T_S2_5b_201のcontent_typeがtext_htmlなら台帳conflictでsentには書き出し直さない(blog):
    """柵: `_record_outcome` の型の比べ（wrong_syntax）を消すと赤（published になる）。"""
    http = blog.install(blog_handler(post=lambda body: (201, entry_xml(content=sent_content(body),
                                                                       content_type="text/html"))))
    path, delivery = approved(blog)
    lines = []
    result = core.throw_once(ACCOUNT, production_flag=True, now=NOW, log=lines.append)
    assert (result.action, result.error, result.exit_code) == ("post", "syntax_mismatch", 1)
    record = deliveries.read(ACCOUNT, delivery)
    assert record["state"] == "conflict"
    assert record["history"][-1]["reason"] == "syntax_mismatch"
    assert record["history"][-1]["content_type"] == "text/html"
    (row,) = sent.records(blog.state)
    assert row["kind"] == "entry_create" and row["delivery"] == delivery
    assert fm_of(path)["status"] == "posted" and inflight.read(blog.state) is None
    assert any("別の記法" in line for line in lines)
    http.calls.clear()
    assert throw().action == "none" and http.calls == []          # 出し直さない


# ---------------------------------------------------------------- T-S2-6a（読み戻しの差の印）

def test_T_S2_6a_応答のcontentが空白だけ違えばpublishedで印が付き_readback_shaは応答の側(blog):
    returned = {}

    def post(body):
        text = sent_content(body)
        # はてなが行の途中の空白を詰めて返した（正規化では消えない違い）
        returned["text"] = text.replace("値動きのまとめ。", "値動きの まとめ。")
        return 201, entry_xml(content=returned["text"])
    blog.install(blog_handler(post=post))
    path, delivery = approved(blog)
    result = throw()
    assert (result.action, result.exit_code) == ("post", 0), result
    record = deliveries.read(ACCOUNT, delivery)
    assert record["state"] == "published"
    assert record["history"][-1]["readback_differs"] is True
    assert record["readback_sha"] == approval.hatena_body_sha256(returned["text"])
    rc, out, _err = call(["deliveries", ACCOUNT])
    assert rc == 0 and "読み戻し差あり" in out


def test_T_S2_6a_末尾の空白だけの違いは正規化で同じ_印なし(blog):
    blog.install(blog_handler(post=lambda body: (201, entry_xml(content=sent_content(body) + "  \n\n\n"))))
    path, delivery = approved(blog)
    assert throw().action == "post"
    record = deliveries.read(ACCOUNT, delivery)
    assert "readback_differs" not in record["history"][-1]
    rc, out, _err = call(["deliveries", ACCOUNT])
    assert "読み戻し差あり" not in out


def test_T_S2_6a_読み戻しは別にGETしない(blog):
    http = blog.install(blog_handler())
    approved(blog)
    throw()
    assert [m for m, _u in http.methods()] == ["GET", "POST"]   # GET は記法の一覧の 1 回だけ


# ---------------------------------------------------------------- T-S2-6b（更新の前の比べ）

def test_T_S2_6b_更新の前のGETがbase_readback_shaと違えばconflictでPUTしない(blog):
    blog.install(blog_handler())
    path, delivery = approved(blog)
    assert throw().action == "post"
    blog.write("articles/a.md", "# アラビカ\n\n直した本文。\n")
    approved(blog, "u.md", op="update", delivery=delivery)
    http = blog.install(blog_handler(get=(200, entry_xml(content="はてなの画面で誰かが直した本文"))))
    result = throw()
    assert result.action == "conflict" and result.message == "readback_changed"
    assert [m for m, _u in http.methods()] == ["GET"]
    assert deliveries.read(ACCOUNT, delivery)["history"][-1]["reason"] == "readback_changed"
