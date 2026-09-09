"""env も token も無い状態で `thth run` が exit 2 で、何も投げない（発注 §5 受け入れ 7）。"""
from __future__ import annotations

from tests.conftest import run_thth, write_queue_file


def test_env_tokenが無ければrunはexit2で何も投げない(isolated_account):
    write_queue_file(isolated_account["queue_dir"], "a.md")
    # isolated_account の台帳は env・token を存在しないパスにしてある（conftest 既定）。
    result = run_thth(["run", isolated_account["name"]])
    assert result.returncode == 2

    # queue ファイルの front-matter が書き換わっていない（何も投げていない）ことを確認する。
    path = isolated_account["queue_dir"] + "/a.md"
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert "status: approved" in text
    assert "status: posted" not in text
    for line in text.split("\n"):
        if line.startswith("post_id:"):
            assert line.strip() == "post_id:"
