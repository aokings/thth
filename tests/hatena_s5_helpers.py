"""4.0 S5 の試験（`tests/test_v400_s5_*.py`）が共有する組み立て。

S1 の `lane`（本物の git repo・偽物のフォトライフ）の上で、はてなの今の記事を覚えている偽物の adapter
（`FakeBlog`）を使う。`read_entry`（題・カテゴリ・公開時刻）と一覧（`_list`）を持つ。**はてなには出ない。**
"""
from __future__ import annotations

from tests.hatena_s1_flow_helpers import (ACCOUNT, NOW, FakeAdapter, approve, factory_for, fm_of,  # noqa: F401
                                          lane, world)
from thth import approval, core, deliveries
from thth.hatena_publish import EntryMissing, EntryResult


class FakeBlog(FakeAdapter):
    """はてなの今の記事（本文・題・カテゴリ）を覚える偽物。外での手直しは `edit()`、消すのは `gone`。"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.body = None
        self.title = None
        self.categories = None
        self.gone = False
        self.published = "2026-09-09T10:00:00+09:00"
        self.feed = None          # 一覧（`_list`）の答え。None なら今の記事 1 本
        self.fail_read = False

    def _remember(self, title, body, categories):
        self.title, self.body, self.categories = title, body, list(categories or [])

    def create_entry(self, *, title, body, categories, images):
        self._remember(title, body, categories)
        return super().create_entry(title=title, body=body, categories=categories, images=images)

    def update_entry(self, *, entry_id, edit_uri, title, body, categories, images):
        self._remember(title, body, categories)
        return super().update_entry(entry_id=entry_id, edit_uri=edit_uri, title=title, body=body,
                                    categories=categories, images=images)

    def delete_entry(self, *, entry_id, edit_uri):
        self.gone = True
        return super().delete_entry(entry_id=entry_id, edit_uri=edit_uri)

    def edit(self, *, body=None, title=None, categories=None):
        """はてなの画面で人が手直しした。"""
        if body is not None:
            self.body = body
        if title is not None:
            self.title = title
        if categories is not None:
            self.categories = list(categories)

    def read_entry(self, *, entry_id, edit_uri):
        self.calls.append(("read", entry_id))
        if self.fail_read:
            raise RuntimeError("hatena_http_503")
        if self.gone:
            raise EntryMissing("hatena_entry_missing")
        return {"entry_id": entry_id, "edit_uri": "https://blog.hatena.ne.jp/x/atom/entry/99",
                "published_url": self.url, "content": self.body or "", "content_type": "text/x-markdown",
                "title": self.title, "categories": list(self.categories or []), "edited": self.published,
                "published": self.published, "draft": "no"}

    def get_entry(self, *, entry_id, edit_uri):
        return self.read_entry(entry_id=entry_id, edit_uri=edit_uri)["content"]

    def _list(self):
        self.calls.append(("list", None))
        if self.fail_read:
            raise RuntimeError("hatena_http_503")
        if self.feed is not None:
            return {"entries": self.feed, "next": None}
        return {"entries": [{"entry_id": self.entry_id, "title": self.title, "content": self.body or "",
                             "published": self.published, "edited": self.published}], "next": None}

    def _ok(self, body):
        if self.result is not None:
            return self.result
        return EntryResult(ok=True, entry_id=self.entry_id, edit_uri="https://blog.hatena.ne.jp/x/atom/entry/99",
                           published_url=self.url, readback_body=body,
                           readback_title=self.title if body is not None else None,
                           readback_categories=list(self.categories or []) if body is not None else None)


def throw(adapter, *, log=None):
    return core.throw_once(ACCOUNT, production_flag=True, adapter_factory=factory_for(adapter), now=NOW,
                           **({"log": log} if log is not None else {}))


def approved(lane, name="a.md", **kwargs):
    path = lane.queue(name, **kwargs)
    approve(path)
    return path, fm_of(path)["delivery"]


def published(lane, blog=None):
    """create を承認して出す。`(path, delivery, blog)`。"""
    blog = blog or FakeBlog()
    path, delivery = approved(lane)
    result = throw(blog)
    assert result.action == "post", result
    return path, delivery, blog


def states(delivery):
    return [row["state"] for row in deliveries.read(ACCOUNT, delivery)["history"]]


def body_sha(text):
    return approval.hatena_body_sha256(text)


def use_blog(monkeypatch, blog):
    """`thth conflict`・`thth inflight` が作る adapter を偽物に（既定の柵の代わり）。"""
    from thth import hatena_publish
    monkeypatch.setattr(hatena_publish, "ADAPTER_FACTORY", factory_for(blog))
