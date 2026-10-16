"""4.0 S2 §7（記事ファイルが commit と一致するか）と §5 の 1（台帳の `syntax: markdown` だけ通す）。

見るのは:
  - T-S2-7a: 記事を直して commit しない・まだ追跡されていない → `article_not_committed`
    （lint・承認の 1 段目・timer）。commit すれば通り、台帳に `wanted_commit`。1 段目に commit を見せる。
  - T-S2-7b: repo でない置き場（`article_rev_kind == content`）は確かめない。
  - T-S2-5a（台帳の段）: 主体の台帳の `syntax` が `markdown` でなければ口座を読む段で
    `blog_syntax_unsupported`（timer は何も送らない）。欄が無ければ markdown とみなす。
"""
from __future__ import annotations

import os

import pytest

from tests.conftest import run_git
from tests.hatena_s2_helpers import (ACCOUNT, FakeAdapter, approve, fm_of, lane, lint_codes, s2lane,  # noqa: F401
                                     stage1, states, throw, world)
from thth import accounts, deliveries, hatena_entry, inflight, principals, sent


def head(repo):
    return run_git(repo, ["rev-parse", "HEAD"]).stdout.strip()


# --------------------------------------------------------------------------
# 7a
# --------------------------------------------------------------------------

def test_T_S2_7a_直してcommitしない記事はlintと1段目がarticle_not_committed(s2lane):
    """柵: HEAD の blob との比べを消すと赤。"""
    s2lane.write("articles/a.md", "# アラビカ\n\n直したがまだ commit していない。\n", commit=False)
    path = s2lane.queue()
    assert "article_not_committed" in lint_codes(path)
    rc, out = stage1(path)
    assert rc != 0 and "article_not_committed" in out and "digest: " not in out


def test_T_S2_7a_追跡されていない記事もarticle_not_committed(s2lane):
    s2lane.write("articles/new.md", "# 新しい\n\n本文\n", commit=False)
    path = s2lane.queue(article_source="articles/new.md")
    assert "article_not_committed" in lint_codes(path)
    rc, out = stage1(path)
    assert rc != 0 and "article_not_committed" in out


def test_T_S2_7a_commitすれば通り台帳にwanted_commitと1段目にcommit(s2lane):
    path = s2lane.queue()
    article_commit = run_git(s2lane.repo, ["log", "-1", "--format=%H", "--", "articles/a.md"]).stdout.strip()
    assert lint_codes(path) == []
    rc, out = stage1(path)
    assert f"・commit {head(s2lane.repo)[:12]}…" in out
    before = head(s2lane.repo)
    approve(path)
    record = deliveries.read(ACCOUNT, fm_of(path)["delivery"])
    assert record["wanted_commit"] == before
    # その commit の記事の blob が承認した版（wanted_rev）
    assert run_git(s2lane.repo, ["rev-parse", f"{record['wanted_commit']}:articles/a.md"]).stdout.strip() \
        == record["wanted_rev"]
    assert article_commit  # 記事の commit は HEAD の祖先（同じ blob）


def test_T_S2_7a_承認のあとに記事を直してcommitしないとtimerは断り何も書かない(s2lane):
    path = s2lane.queue()
    approve(path)
    delivery = fm_of(path)["delivery"]
    s2lane.write("articles/a.md", "# アラビカ\n\nVM で直接いじった。\n", commit=False)
    adapter = FakeAdapter()
    result = throw(adapter)
    assert result.action == "none" and adapter.calls == []
    assert {"file": "a.md", "reason": "article_not_committed"} in result.rejections
    assert states(delivery) == ["wanted"] and inflight.read(s2lane.state) is None
    assert sent.records(s2lane.state) == []


def test_T_S2_7a_材料にarticle_commitと出たあとの欄(s2lane):
    path = s2lane.queue()
    from thth import queuefile
    m = hatena_entry.material(queuefile.parse(path).front_matter, accounts.load_account(ACCOUNT), ACCOUNT)
    assert m["article_commit"] == head(s2lane.repo)
    assert hatena_entry.outcome_fields(m) == {"checked_commit": m["article_commit"]}
    assert hatena_entry.outcome_fields({"article_commit": None}) == {}
    assert {"wanted_commit", "checked_commit"} <= set(deliveries.MUTABLE_FIELDS)


@pytest.mark.xfail(strict=True, reason="継ぎ目: hatena_publish._record_outcome が hatena_entry.outcome_fields(m) "
                                       "を fields に足すまで（adapter 側の書き手の範囲）")
def test_T_S2_7a_出したあと台帳にchecked_commit(s2lane):
    path = s2lane.queue()
    approve(path)
    assert throw(FakeAdapter()).action == "post"
    record = deliveries.read(ACCOUNT, fm_of(path)["delivery"])
    assert record.get("checked_commit") == record["wanted_commit"]


# --------------------------------------------------------------------------
# 7b
# --------------------------------------------------------------------------

def test_T_S2_7b_repoでない置き場は確かめない(world, tmp_path):
    repo = tmp_path / "repo-b"
    (repo / "articles").mkdir(parents=True)
    (repo / "articles" / "a.md").write_text("# 題\n\n本文\n", encoding="utf-8")
    cfg = accounts.load_account("lane-b-hatenablog")
    fm = {"account": "lane-b-hatenablog", "article": "lane-b/a", "article_source": "articles/a.md",
          "title": "題", "op": "create", "publish_at": "2026-09-09T09:00:00+09:00"}
    assert hatena_entry.lint_errors(fm, cfg) == []
    m = hatena_entry.material(fm, cfg, "lane-b-hatenablog")
    assert m["article_rev_kind"] == "content" and m["article_commit"] is None
    assert hatena_entry.committed_commit(os.path.join(str(repo), "articles", "a.md"), b"x") is None


# --------------------------------------------------------------------------
# 5a（台帳の段）
# --------------------------------------------------------------------------

@pytest.mark.parametrize("syntax", ["hatena", "html"])
def test_T_S2_5a_台帳のsyntaxがmarkdownでなければblog_syntax_unsupported(world, syntax):
    """柵: load_account の syntax の確かめを消すと赤。"""
    data = world.principal("hatena-main")
    for blog in data["blogs"]:
        if blog["blog_id"] == "a.hatenablog.com":
            blog["syntax"] = syntax
    world.write_principal(data)
    with pytest.raises(principals.PrincipalError) as exc:
        accounts.load_account(ACCOUNT)
    assert exc.value.code == "blog_syntax_unsupported"
    # 同じ主体のほかのブログ（markdown）の口座は読める
    assert accounts.load_account("lane-b-hatenablog")["blog_id"] == "b.hatenablog.com"


def test_T_S2_5a_syntaxの欄が無ければmarkdownとみなす(world):
    assert "syntax" not in world.principal("hatena-other")["blogs"][0]
    assert accounts.load_account("lane-c-hatenablog")["blog_id"] == "c.hatenablog.com"


def test_T_S2_5a_承認のあとに記法を変えるとtimerは何も送らない(s2lane):
    path = s2lane.queue()
    approve(path)
    delivery = fm_of(path)["delivery"]
    data = s2lane.world.principal("hatena-main")
    data["blogs"][0]["syntax"] = "hatena"
    s2lane.world.write_principal(data)
    adapter = FakeAdapter()
    with pytest.raises(accounts.AccountError) as exc:
        throw(adapter)
    assert "blog_syntax_unsupported" in str(exc.value)
    assert adapter.calls == [] and states(delivery) == ["wanted"] and inflight.read(s2lane.state) is None
