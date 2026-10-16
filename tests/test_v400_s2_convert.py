"""4.0 S2 §4: 変換の表（`hatena_convert.RULES`）・相対リンクの `link_base`・画像の断り（T-S2-4a〜4e）。

見るのは:
  - 4a: 表の順・版の語・決定的（同じ入力 2 回で同じ）。
  - 4b: 相対リンクは `link_base` で絶対に。`#`・scheme あり・`//` はそのまま。コードの中は触らない。
    `link_base` が無い（雛形のダミーも無いとみなす）のに相対リンク → `relative_link_unresolvable`
    （lint・承認の 1 段目・timer の 3 か所）。推測で `https://<blog_id>/` を貼らない。
  - 4c: 手元の画像が 1 つでもあれば lint・1 段目・timer が `images_unsupported_until_s3`。https の画像は通る。
  - 4d: front matter を落とす・題と同じ先頭の H1 だけ落とす・無ければ無傷。
  - 4e: 変換の規則（と版）を変えると承認済みの原稿は `approval_stale`（本文の sha が変わる）。
  - 口座の `link_base`: https だけ・query と # は不可・雛形はダミーで `dummy_fields` に出る。
"""
from __future__ import annotations

import json

import pytest

from tests.hatena_s2_helpers import (ACCOUNT, FakeAdapter, approve, fm_of, lane, lint_codes, s2lane,  # noqa: F401
                                     set_account, stage1, states, throw, world)
from thth import accounts, deliveries, hatena_convert, hatena_entry, inflight, select, sent
from thth.hatena_convert import ConvertError, render

BASE = "https://kopicha.example/"


def body_of(text, **kwargs):
    kwargs.setdefault("article_source", "articles/sub/a.md")
    return render(text, **kwargs)[0]


# --------------------------------------------------------------------------
# 4a 表の形
# --------------------------------------------------------------------------

def test_T_S2_4a_表の順と版の語と決定的():
    assert hatena_convert.VERSION == "s2-rules-1"
    assert [name for name, _f, _c in hatena_convert.RULES] == [
        "strip_front_matter", "strip_leading_h1", "absolute_links", "images", "footnotes", "tables",
        "headings", "code_fences", "embeds", "normalize"]
    marks = {name: checked for name, _f, checked in hatena_convert.RULES}
    assert marks["images"] == "s3"
    for name in ("footnotes", "tables", "headings", "code_fences", "embeds"):
        assert marks[name] == "unverified"
    text = ("---\ntitle: x\n---\n\n# 題\n\n本文 [a](../x.md)。[^1]\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
            "```\n[c](rel)\n```\n\nhttps://example.com/embed\n\n[^1]: 注\n")
    once = render(text, article_source="articles/a.md", base_url=BASE, title="題")
    twice = render(text, article_source="articles/a.md", base_url=BASE, title="題")
    assert once == twice


def test_T_S2_4a_最後に正規化する():
    assert body_of("本文  \r\n\r\n終わり\n\n\n") == "本文\n\n終わり"


# --------------------------------------------------------------------------
# 4b 相対リンク
# --------------------------------------------------------------------------

def test_T_S2_4b_相対リンクはlink_baseで絶対にし_アンカーと絶対はそのまま():
    text = ("[a](../x.md) [b](/y) [c](#z) [d](https://e.example/q) [m](mailto:a@example.com) "
            "[p](//cdn.example/x) [t](rel.md \"題\") [u](<スペース 入り.md>)\n")
    assert body_of(text, base_url=BASE) == (
        "[a](https://kopicha.example/articles/x.md) [b](https://kopicha.example/y) [c](#z) "
        "[d](https://e.example/q) [m](mailto:a@example.com) [p](//cdn.example/x) "
        "[t](https://kopicha.example/articles/sub/rel.md \"題\") "
        "[u](<https://kopicha.example/articles/sub/スペース 入り.md>)")


def test_T_S2_4b_末尾のスラッシュが無いlink_baseでも同じ():
    assert body_of("[a](x.md)\n", base_url="https://kopicha.example") == \
        "[a](https://kopicha.example/articles/sub/x.md)"


def test_T_S2_4b_参照の定義も絶対にし_脚注の定義とコードの中は触らない():
    text = ("[参照][r]\n\n[r]: ../r.md \"題\"\n[^1]: 注の文 rel.md\n\n`[c](rel.md)` の外 [o](o.md)\n\n"
            "```md\n[f](fence.md)\n```\n\n~~~\n[g](tilde.md)\n~~~\n")
    assert body_of(text, base_url=BASE) == (
        "[参照][r]\n\n[r]: https://kopicha.example/articles/r.md \"題\"\n[^1]: 注の文 rel.md\n\n"
        "`[c](rel.md)` の外 [o](https://kopicha.example/articles/sub/o.md)\n\n"
        "```md\n[f](fence.md)\n```\n\n~~~\n[g](tilde.md)\n~~~")


def test_T_S2_4b_画像の中のリンク先は触らず_画像を包むリンクは絶対に(monkeypatch):
    monkeypatch.setattr(hatena_convert, "REFUSE_LOCAL_IMAGES", False)
    text = "![豆](../images/bean.png)\n\n[![外](https://i.example/a.png)](page.md)\n"
    body, images = render(text, article_source="articles/a.md", base_url=BASE,
                          image_sha=lambda path: ("0" * 64, 1))
    assert body == ("![豆](../images/bean.png)\n\n"
                    "[![外](https://i.example/a.png)](https://kopicha.example/articles/page.md)")
    assert [row["path"] for row in images] == ["images/bean.png"]


def test_T_S2_4b_link_baseが無ければ相対リンクは断り_推測で貼らない():
    """柵: link_base の無いときに `https://<blog_id>/` などを推測で使うと赤。"""
    with pytest.raises(ConvertError) as exc:
        body_of("[a](../x.md)\n", base_url=None)
    assert str(exc.value) == "relative_link_unresolvable"
    with pytest.raises(ConvertError):
        body_of("[r]: rel.md\n", base_url=None)
    # 相対リンクが無ければ link_base は要らない
    assert body_of("[c](#z) [d](https://e.example/) `[x](rel.md)`\n", base_url=None) == \
        "[c](#z) [d](https://e.example/) `[x](rel.md)`"


def test_T_S2_4b_雛形のダミーのlink_baseは無いとみなす():
    assert accounts.link_base({"link_base": "https://example.invalid/"}) is None
    assert accounts.link_base({}) is None
    assert accounts.link_base({"link_base": "https://kopicha.example"}) == "https://kopicha.example/"


def _relative_article(lane):
    lane.write("articles/a.md", "# アラビカ\n\n詳しくは [前の記事](../2026-09/old.md)。\n")


def test_T_S2_4b_lintと1段目はlink_baseが無ければrelative_link_unresolvable(s2lane):
    _relative_article(s2lane)
    path = s2lane.queue()
    assert "relative_link_unresolvable" in lint_codes(path)
    rc, out = stage1(path)
    assert rc != 0 and "relative_link_unresolvable" in out and "digest: " not in out


def test_T_S2_4b_link_baseがあれば1段目に絶対のリンク(s2lane):
    set_account(s2lane.world, link_base="https://kopicha.example/")
    _relative_article(s2lane)
    path = s2lane.queue()
    assert lint_codes(path) == []
    rc, out = stage1(path)
    assert "詳しくは [前の記事](https://kopicha.example/2026-09/old.md)。" in out


def test_T_S2_4b_承認のあとにlink_baseを外すとtimerはrelative_link_unresolvableで何も書かない(s2lane):
    set_account(s2lane.world, link_base="https://kopicha.example/")
    _relative_article(s2lane)
    path = s2lane.queue()
    approve(path)
    delivery = fm_of(path)["delivery"]
    set_account(s2lane.world, link_base=None)
    adapter = FakeAdapter()
    result = throw(adapter)
    assert result.action == "none" and adapter.calls == []
    assert {"file": "a.md", "reason": "relative_link_unresolvable"} in result.rejections
    assert states(delivery) == ["wanted"] and inflight.read(s2lane.state) is None
    assert sent.records(s2lane.state) == []


# --------------------------------------------------------------------------
# 4c 画像
# --------------------------------------------------------------------------

def test_T_S2_4c_手元の画像はrenderで断り_httpsの画像は通る():
    """柵: images の段の断りを消すと赤。"""
    with pytest.raises(ConvertError) as exc:
        render("![豆](images/bean.png)\n", article_source="a.md", image_sha=lambda p: ("0" * 64, 1))
    assert str(exc.value) == "images_unsupported_until_s3"
    body, images = render("![外](https://i.example/a.png)\n", article_source="a.md")
    assert body == "![外](https://i.example/a.png)" and images == []


def _image_article(lane):
    lane.write("articles/a.md", "# アラビカ\n\n![豆](../images/bean.png)\n")


def test_T_S2_4c_lintと1段目は画像つきの記事をimages_unsupported_until_s3で断る(s2lane):
    _image_article(s2lane)
    path = s2lane.queue()
    assert "images_unsupported_until_s3" in lint_codes(path)
    rc, out = stage1(path)
    assert rc != 0 and "images_unsupported_until_s3" in out and "フォトライフ" in out
    assert "digest: " not in out


def test_T_S2_4c_S1で承認した画像つきの記事もtimerは断り何も書かない(s2lane, monkeypatch):
    _image_article(s2lane)
    path = s2lane.queue()
    with monkeypatch.context() as patched:
        patched.setattr(hatena_convert, "REFUSE_LOCAL_IMAGES", False)   # S1 のあいだに承認された
        approve(path)
    delivery = fm_of(path)["delivery"]
    adapter = FakeAdapter()
    result = throw(adapter)
    assert result.action == "none" and adapter.calls == []
    assert {"file": "a.md", "reason": "images_unsupported_until_s3"} in result.rejections
    assert states(delivery) == ["wanted"] and inflight.read(s2lane.state) is None


def test_T_S2_4c_httpsの画像だけの記事は出る(s2lane):
    s2lane.write("articles/a.md", "# アラビカ\n\n![外](https://i.example/a.png)\n")
    path = s2lane.queue()
    assert lint_codes(path) == []
    approve(path)
    adapter = FakeAdapter()
    assert throw(adapter).action == "post" and adapter.calls == [("create", "アラビカの値動き")]


# --------------------------------------------------------------------------
# 4d front matter・先頭の H1
# --------------------------------------------------------------------------

def test_T_S2_4d_front_matterを落とす():
    assert body_of("---\ntitle: x\ntags: [a]\n---\n\n本文\n") == "本文"
    assert body_of("---\r\ntitle: x\r\n...\r\n本文\r\n") == "本文"


def test_T_S2_4d_閉じの無い区切りやfront_matterの無い記事は無傷():
    assert body_of("---\n本文\n") == "---\n本文"
    assert body_of("本文\n\n---\n\n下\n") == "本文\n\n---\n\n下"


def test_T_S2_4d_題と同じ先頭のH1だけ落とす():
    assert body_of("# アラビカの値動き\n\n本文\n", title="アラビカの値動き") == "本文"
    assert body_of("\n# アラビカの値動き #\n\n本文\n", title="アラビカの値動き") == "本文"
    assert body_of("# アラビカ\n\n本文\n", title="アラビカの値動き") == "# アラビカ\n\n本文"
    assert body_of("## アラビカの値動き\n\n本文\n", title="アラビカの値動き") == "## アラビカの値動き\n\n本文"
    assert body_of("本文\n\n# アラビカの値動き\n", title="アラビカの値動き") == "本文\n\n# アラビカの値動き"
    assert body_of("# アラビカの値動き\n", title=None) == "# アラビカの値動き"


def test_T_S2_4d_1段目の本文は題と同じ先頭のH1とfront_matterを落としたもの(s2lane):
    s2lane.write("articles/a.md", "---\nslug: a\n---\n# アラビカの値動き\n\n値動きのまとめ。\n")
    path = s2lane.queue()
    rc, out = stage1(path)
    head, body = out.split("---- 変換したあとの本文（全文） ----\n", 1)
    assert body.split("\n---- ここまで ----", 1)[0] == "値動きのまとめ。"


# --------------------------------------------------------------------------
# 4e 版を上げると承認が古くなる
# --------------------------------------------------------------------------

def test_T_S2_4e_規則と版を変えると承認済みの原稿はapproval_stale(s2lane, monkeypatch):
    path = s2lane.queue()
    approve(path)
    from tests.conftest import parse_verified
    cfg = accounts.load_account(ACCOUNT)
    assert hatena_entry.select_problem(parse_verified(path), cfg, ACCOUNT)[0] is None

    def embed(text, ctx):
        return text.replace("終わり。", "終わり。（埋め込み）")
    rules = tuple((name, embed if name == "embeds" else f, c) for name, f, c in hatena_convert.RULES)
    monkeypatch.setattr(hatena_convert, "RULES", rules)
    monkeypatch.setattr(hatena_convert, "VERSION", "s2-rules-2")
    assert hatena_entry.select_problem(parse_verified(path), cfg, ACCOUNT)[0] == "approval_stale"
    adapter = FakeAdapter()
    result = throw(adapter)
    assert adapter.calls == [] and {"file": "a.md", "reason": "approval_stale"} in result.rejections


# --------------------------------------------------------------------------
# 口座の link_base
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["http://kopicha.example/", "https://kopicha.example/?q=1",
                                   "https://kopicha.example/#top", "ftp://kopicha.example/", "",
                                   "https://user@kopicha.example/", 3, "https://kopicha.example/ a"])
def test_link_baseはhttpsだけ(world, value):
    set_account(world, link_base=value)
    with pytest.raises(accounts.AccountError) as exc:
        accounts.load_account(ACCOUNT)
    assert "invalid_link_base" in str(exc.value)


def test_link_baseは任意_path付きも受ける(world):
    set_account(world, link_base=None)
    assert "link_base" not in accounts.load_account(ACCOUNT)
    set_account(world, link_base="https://kopicha.example/notes/")
    assert accounts.link_base(accounts.load_account(ACCOUNT)) == "https://kopicha.example/notes/"


def test_雛形のlink_baseはダミーでdummy_fieldsに出る():
    from tests.hatena_s1_helpers import EXAMPLES
    acct = json.loads((EXAMPLES / "hatenablog.json").read_text(encoding="utf-8"))
    assert acct["link_base"] == "https://example.invalid/"
    assert "link_base" in {d["field"] for d in accounts.dummy_fields(acct)}
    acct["link_base"] = "https://kopicha.example/"
    assert "link_base" not in {d["field"] for d in accounts.dummy_fields(acct)}
    # SNS の口座の判定は変わらない
    threads = json.loads((EXAMPLES / "threads.json").read_text(encoding="utf-8"))
    assert {d["field"] for d in accounts.dummy_fields(threads)} == {"redirect_uri", "handle"}


def test_link_baseはSNSの口座では読まない(isolated_account_factory):
    info = isolated_account_factory(link_base="not a url")
    assert accounts.load_account(info["name"])["link_base"] == "not a url"


def test_lintの符丁は畳める(s2lane):
    from thth import lint
    _image_article(s2lane)
    path = s2lane.queue()
    assert lint.reason_code(lint.lint_file(path)) == "images_unsupported_until_s3"
    assert deliveries  # noqa: B018 — import の確かめ
    assert select  # noqa: B018
