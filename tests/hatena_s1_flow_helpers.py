"""4.0 S1 順 7〜10 の試験（承認・timer の照合・遠くの道・出来事）が共有する組み立て。

`hatena_s1_helpers.world`（主体 2 つと口座 5 本）の `lane-a-hatenablog` を、本物の git repo
（bare origin 付き）を持つ口座に置き直し、記事・画像・配信原稿を置く。送信の口（adapter）は
`FakeAdapter`（はてなに何も送らない）。**本物の token は置かない。**
"""
from __future__ import annotations

import datetime
import io
import json
import os
import contextlib

import pytest

from tests.conftest import init_git_pair, run_git
from tests.hatena_s1_helpers import world  # noqa: F401
from thth import cli, jst
from thth.hatena_publish import EntryResult

NOW = datetime.datetime(2026, 9, 9, 10, 0, 0, tzinfo=jst.JST)
ACCOUNT = "lane-a-hatenablog"
ARTICLE_TEXT = "# アラビカ\n\n値動きのまとめ。  \n\n![豆](../images/bean.png)\n\n終わり。\n\n\n"
IMAGE_BYTES = b"\x89PNG fake image bytes"


def manuscript(*, op="create", title="アラビカの値動き", categories="[コーヒー, 価格]", delivery=None,
               publish_at="2026-09-09T09:00:00+09:00", extra=None, article_source="articles/a.md"):
    lines = ["---", "thth: 1", f"account: {ACCOUNT}", "status: draft", f"publish_at: {publish_at}",
             "article: lane-a/arabica", f"article_source: {article_source}", "form: full",
             f"title: {title}", f"categories: {categories}", f"op: {op}"]
    if delivery:
        lines.append(f"delivery: {delivery}")
    for key, value in (extra or {}).items():
        lines.append(f"{key}: {value}")
    lines += ["---", "", "## hatenablog", "", "（この節は使わない）", ""]
    return "\n".join(lines)


class Lane:
    def __init__(self, world, repo, queue_dir):
        self.world = world
        self.repo = repo
        self.queue_dir = queue_dir

    def write(self, rel, text, *, commit=True):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if isinstance(text, bytes) else "w"
        with open(path, mode, **({} if isinstance(text, bytes) else {"encoding": "utf-8"})) as f:
            f.write(text)
        if commit:
            self.commit(rel)
        return path

    def commit(self, rel, message="test"):
        run_git(self.repo, ["add", "--", rel])
        run_git(self.repo, ["commit", "-m", message])
        run_git(self.repo, ["push"])

    def queue(self, name="a.md", **kwargs):
        return self.write(f"docs/sns/queue/{name}", manuscript(**kwargs))

    @property
    def state(self):
        return self.world.state(ACCOUNT)


def call(argv):
    """`thth …` を同じ process で呼ぶ（時刻の固定が効く）。`(rc, stdout, stderr)`。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            rc = cli.main(argv)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


def first_stage_digest(path):
    rc, out, err = call(["approve", path])
    assert rc == 1, (out, err)
    digests = [line.split(": ", 1)[1].strip() for line in out.splitlines() if line.startswith("digest: ")]
    assert digests, (out, err)
    return digests[-1], out


def approve(path, by="tester"):
    digest, _ = first_stage_digest(path)
    rc, out, err = call(["approve", path, "--confirm", digest, "--by", by])
    assert rc == 0, (out, err)
    return digest


@pytest.fixture
def lane(world, tmp_path):  # noqa: F811
    # repo は $THTH_ROOT の下（遠くの道の `server_files` は根の外を読まない）。
    pair = init_git_pair(world.root / "repos" / "lane-a", seed_content="", seed_name=".gitkeep")
    data = world.account(ACCOUNT)
    data.update({"repo_dir": pair["work"], "production": True, "scheduled": True})
    world.write_account(data)
    w = Lane(world, pair["work"], pair["queue_dir"])
    w.write("articles/a.md", ARTICLE_TEXT, commit=False)
    w.write("images/bean.png", IMAGE_BYTES, commit=False)
    run_git(w.repo, ["add", "-A"])
    run_git(w.repo, ["commit", "-m", "article"])
    run_git(w.repo, ["push"])
    return w


class FakeAdapter:
    """はてなに何も送らない adapter。呼ばれた順を `calls` に残す。"""

    def __init__(self, *, entry_id="tag:blog.hatena.ne.jp,2013:blog-lane-owner-1-99",
                 url="https://a.hatenablog.com/entry/2026/09/09/100000", current_body=None,
                 result=None, readback=None):
        self.calls = []
        self.entry_id = entry_id
        self.url = url
        self.current_body = current_body
        self.result = result
        self.readback = readback

    def get_entry(self, *, entry_id, edit_uri):
        self.calls.append(("get", entry_id))
        return self.current_body

    def _ok(self, body):
        if self.result is not None:
            return self.result
        return EntryResult(ok=True, entry_id=self.entry_id, edit_uri="https://blog.hatena.ne.jp/x/atom/entry/99",
                           published_url=self.url, readback_body=body if self.readback is None else self.readback)

    def create_entry(self, *, title, body, categories, images):
        self.calls.append(("create", title))
        return self._ok(body)

    def update_entry(self, *, entry_id, edit_uri, title, body, categories, images):
        self.calls.append(("update", entry_id))
        return self._ok(body)

    def delete_entry(self, *, entry_id, edit_uri):
        self.calls.append(("delete", entry_id))
        return self._ok(None)


def factory_for(adapter):
    def factory(account_cfg, principal_cfg):
        return adapter
    return factory


def fm_of(path):
    from thth import queuefile
    return queuefile.parse(path).front_matter


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)
