"""4.0 S1 の試験（`tests/test_v400_s1_*.py`）が共有する、主体と口座の組み立て。

隔離した `$THTH_ROOT`・`$THTH_ACCOUNTS_DIR` の下に、主体 2 つと、それを指す
はてなの口座を置く。**本物の token は置かない**（中身は試験の値）。
"""
from __future__ import annotations

import copy
import json
import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "accounts.example"


def principal_data(name, *, token, hatena_id="lane-owner", blogs=None, **extra):
    data = json.loads((EXAMPLES / "principal-hatena.json").read_text(encoding="utf-8"))
    data.update({"principal": name, "hatena_id": hatena_id, "token": str(token),
                 "consumer": str(token) + ".consumer"})
    if blogs is not None:
        data["blogs"] = blogs
    data.update(extra)
    return data


def account_data(name, *, project, principal, blog_id, token, handle="lane-owner",
                 repo_dir=None, **extra):
    data = json.loads((EXAMPLES / "hatenablog.json").read_text(encoding="utf-8"))
    data.update({"account": name, "project": project, "principal": principal, "blog_id": blog_id,
                 "handle": handle, "token": str(token), "env": str(token) + ".env",
                 "repo_dir": repo_dir or "$THTH_ROOT/repos/_none"})
    data.update(extra)
    return data


class World:
    def __init__(self, root, accounts_dir, principals_dir, tmp):
        self.root = root
        self.accounts_dir = accounts_dir
        self.principals_dir = principals_dir
        self.tmp = tmp

    def write_principal(self, data):
        path = self.principals_dir / f"{data['principal']}.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.chmod(path, 0o600)
        return path

    def write_account(self, data):
        path = self.accounts_dir / f"{data['account']}.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return path

    def principal(self, name):
        return json.loads((self.principals_dir / f"{name}.json").read_text(encoding="utf-8"))

    def account(self, name):
        return json.loads((self.accounts_dir / f"{name}.json").read_text(encoding="utf-8"))

    def edit_principal(self, name, **values):
        data = self.principal(name)
        data.update(copy.deepcopy(values))
        self.write_principal(data)

    def state(self, account):
        return str(self.root / "state" / account)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """主体 `hatena-main`（ブログ 3 つ・レーン lane-a／lane-b）と `hatena-other`、その口座。

    - `lane-a-hatenablog`: lane-a → a.hatenablog.com（repo-a）
    - `lane-b-hatenablog`: lane-b → b.hatenablog.com（repo-b）
    - `lane-a-shared`／`lane-b-shared`: 同じ shared.hatenablog.com を 2 レーンで
    - `lane-c-hatenablog`: 別の主体 `hatena-other` → c.hatenablog.com
    """
    root = tmp_path / "root"
    root.mkdir()
    accounts_dir = root / "accounts"
    accounts_dir.mkdir()
    principals_dir = root / "principals"
    principals_dir.mkdir(mode=0o700)
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(accounts_dir))
    monkeypatch.delenv("THTH_PRINCIPALS_DIR", raising=False)
    w = World(root, accounts_dir, principals_dir, tmp_path)
    main_token = tmp_path / "secrets" / "hatena-main.token"
    other_token = tmp_path / "secrets" / "hatena-other.token"
    w.main_token, w.other_token = main_token, other_token
    w.write_principal(principal_data(
        "hatena-main", token=main_token,
        blogs=[{"blog_id": "a.hatenablog.com", "lanes": ["lane-a"], "syntax": "markdown"},
               {"blog_id": "b.hatenablog.com", "lanes": ["lane-b"], "syntax": "markdown"},
               {"blog_id": "shared.hatenablog.com", "lanes": ["lane-a", "lane-b"], "syntax": "markdown"}],
        bookmark={"lanes": ["lane-a"]}))
    w.write_principal(principal_data(
        "hatena-other", token=other_token, hatena_id="someone-else",
        blogs=[{"blog_id": "c.hatenablog.com", "lanes": ["lane-c"]}]))
    w.write_account(account_data("lane-a-hatenablog", project="lane-a", principal="hatena-main",
                                 blog_id="a.hatenablog.com", token=main_token,
                                 repo_dir=str(tmp_path / "repo-a")))
    w.write_account(account_data("lane-b-hatenablog", project="lane-b", principal="hatena-main",
                                 blog_id="b.hatenablog.com", token=main_token,
                                 repo_dir=str(tmp_path / "repo-b")))
    w.write_account(account_data("lane-a-shared", project="lane-a", principal="hatena-main",
                                 blog_id="shared.hatenablog.com", token=main_token,
                                 repo_dir=str(tmp_path / "repo-a")))
    w.write_account(account_data("lane-b-shared", project="lane-b", principal="hatena-main",
                                 blog_id="shared.hatenablog.com", token=main_token,
                                 repo_dir=str(tmp_path / "repo-b")))
    w.write_account(account_data("lane-c-hatenablog", project="lane-c", principal="hatena-other",
                                 blog_id="c.hatenablog.com", token=other_token, handle="someone-else",
                                 repo_dir=str(tmp_path / "repo-c")))
    return w
