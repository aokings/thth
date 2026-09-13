"""道具側の摩擦（T1〜T4・記録 `docs/記録/試験_LLMに選ばせる_2026-09-13.md` §3）。

第 1 回（2026-09-13・**L1**）で、まっさらな Claude 3 体が `thth` を 14・22・10 回
打った。超過分の大半は `--help` の往復と、**素の原稿への `lint`/`preview` の空振り**。
どれも「断るところまでは正しいが、次の一手を言わない」ことで起きている。

ここで見るのは 4 つだけ:
  - **T1**: front-matter の無いファイルに `lint`／`preview` が当たったら、断り文の
    末尾に**次の一手を 1 行**。`lint --help`／`preview --help` の冒頭に
    「queue のファイル用」。
  - **T2**: `send` の「長すぎます」に **`thth forms` を指す 1 行**。
  - **T3**: `thth account <name>` は**表示できたら rc=0**（投稿できない旨は本文と
    `--json` の `ready`／`blockers` が述べる）。
  - **T4**: `thth --help` の冒頭に**3 行の道案内**。
"""
from __future__ import annotations

import os

from tests.conftest import run_thth
from thth import lint as lint_mod


素の原稿 = """# Why I started keeping a "boring notes" file

For about a year I have kept a single plain text file called boring-notes.md.
It only holds the small facts I keep re-discovering.
"""


def _素の原稿を置く(tmp_path) -> str:
    path = tmp_path / "draft.md"
    path.write_text(素の原稿, encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------
# T1: front-matter の無いファイルに当たったときの次の一手
# --------------------------------------------------------------------------

def test_lintは素の原稿に次の一手を1行足す(tmp_path):
    """**断るだけで終わらない。** 素の原稿を持っている人の行き先を言う。"""
    path = _素の原稿を置く(tmp_path)
    r = run_thth(["lint", path])
    assert r.returncode == 1, r.stdout + r.stderr
    出力 = r.stdout + r.stderr
    # 断り（front-matter が無い）はそのまま残る。
    assert "thth: front-matter に `thth: 1` が無い" in 出力, 出力
    # そのうえで**次の一手が 1 行**。
    assert "次の一手:" in 出力, 出力
    assert "thth send" in 出力 and "--text-file" in 出力, 出力
    assert path in 出力, 出力
    assert "乾式試験が既定" in 出力, 出力
    assert "thth queue --help" in 出力, 出力
    # **1 行**（次の一手が複数行に散らない）。
    次の一手の行 = [line for line in 出力.splitlines() if "次の一手:" in line]
    assert len(次の一手の行) == 1, 出力


def test_previewは素の原稿に次の一手を1行足す(tmp_path):
    path = _素の原稿を置く(tmp_path)
    r = run_thth(["preview", path])
    assert r.returncode == 1, r.stdout + r.stderr
    出力 = r.stdout + r.stderr
    assert "media section が無い" in 出力, 出力
    assert "次の一手:" in 出力, 出力
    assert "thth send" in 出力 and "thth queue --help" in 出力, 出力


def test_front_matterのあるファイルには次の一手を出さない(tmp_path, isolated_account):
    """**queue のファイルの検査結果に混ぜない。** そこは行き先の話ではない。"""
    path = tmp_path / "q.md"
    path.write_text("---\nthth: 1\naccount: nigamilab-threads\n---\n\n## threads\n\nhi\n",
                    encoding="utf-8")
    r = run_thth(["lint", str(path)])
    # publish_at が無いので落ちる（＝断り文は出ている）。
    assert r.returncode == 1, r.stdout + r.stderr
    assert "publish_at" in r.stdout + r.stderr
    assert "次の一手:" not in r.stdout + r.stderr, r.stdout + r.stderr
    assert lint_mod.next_step(str(path)) is None


def test_lintが通るファイルには次の一手を出さない(tmp_path):
    """エラーが無ければ足さない（`OK` の後ろに行き先の話を付けない）。"""
    path = tmp_path / "q.md"
    path.write_text("---\nthth: 1\naccount: unknown-threads\n"
                    "publish_at: 2026-09-20T08:00:00+09:00\n---\n\n## threads\n\nhi\n",
                    encoding="utf-8")
    r = run_thth(["lint", str(path)])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "次の一手:" not in r.stdout, r.stdout


def test_lintのjsonにも次の一手が入る(tmp_path):
    import json
    path = _素の原稿を置く(tmp_path)
    r = run_thth(["lint", path, "--json"])
    assert r.returncode == 1
    row = json.loads(r.stdout)
    assert "thth send" in row["next_step"], row


def test_lintとpreviewのhelpはqueueのファイル用と言う():
    for cmd in ("lint", "preview"):
        r = run_thth([cmd, "--help"])
        assert r.returncode == 0, r.stdout + r.stderr
        assert "queue のファイル用" in r.stdout, (cmd, r.stdout)
