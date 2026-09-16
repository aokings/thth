"""`skills/thth/SKILL.md` の書き直しを縛る（発注 T4-1・設計「自分の泉」§6）。

見るのは 3 つ:
  1. frontmatter の `description` に「自分の泉」の 4 場面
     （投稿する前・返信を書く前・出したあと・絡みに行く先を選ぶとき）が
     全部入っている——**LLM は description で読むかを決める**ので、ここに
     無い場面はそもそも読まれない。
  2. 本文に 4 つの MCP 道具名（`thread_read`・`after_you_posted`・
     `where_to_appear`・`who_is_this`）と `before_you_post` が全部出てくる。
  3. `thth share` の語が**出てこない**（裁定 2026-09-16「横断の泉はやめる」・
     T4-1 発注書「thth share の段落を落とす」）。

`thth who`／MCP `who_is_this` は設計「自分の泉」§2.4 の名前と説明文で
**存在する前提で書いてよいが、コードからは呼ばない**（T3 は並行実装中・
発注 T4 の前提）——このテストも `thth who` を実行しない。文字列があるか
だけを見る。
"""
from __future__ import annotations

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_PATH = os.path.join(REPO_ROOT, "skills", "thth", "SKILL.md")

# description に入っているべき「自分の泉」の 4 場面（T4-1 発注書のとおり）。
SCENES = ("投稿する前", "返信を書く前", "出したあと", "絡みに行く先を選ぶとき")

# 本文に出てくるべき道具名（MCP 4 つ + before_you_post）。
TOOL_NAMES = ("thread_read", "after_you_posted", "where_to_appear",
             "who_is_this", "before_you_post")

# MCP 4 つは「MCP `<name>`」の形で、その道具を説明する段に名指しで出てくる
# はず（`before_you_post` は `thth topics` 型の文脈が無いのでこの形は問わない）。
# **substring だけの検査だと、他の場面（`found_by:` の列挙など）に語が
# 生き残っていれば、その段の説明そのものを消しても通ってしまう**——変異
# （T4-1 発注書「where_to_appear の行を消す → failed」）で確かめたのはここ。
MCP_TOOL_NAMES = ("thread_read", "after_you_posted", "where_to_appear", "who_is_this")


def _read() -> str:
    with open(SKILL_PATH, encoding="utf-8") as f:
        return f.read()


def _frontmatter_description(text: str) -> str:
    """先頭の `---\n...\n---` の中の `description:` 行を取り出す。"""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "frontmatter（先頭の --- で囲まれた節）が見つかりません"
    fm = m.group(1)
    d = re.search(r"^description:\s*(.+)$", fm, re.M)
    assert d, "frontmatter に description がありません"
    return d.group(1).strip()


def test_descriptionに自分の泉の4場面が全部入っている():
    text = _read()
    description = _frontmatter_description(text)
    missing = [scene for scene in SCENES if scene not in description]
    assert not missing, f"description に無い場面: {missing}（{description!r}）"


def test_4つのMCP名とbefore_you_postが全部出てくる():
    text = _read()
    missing = [name for name in TOOL_NAMES if name not in text]
    assert not missing, f"SKILL.md に出てこない道具名: {missing}"


def test_4つのMCP名がそれぞれの段でMCPの道具として名指しされている():
    """`MCP \\`<name>\\`` の形で、その道具の段に出てくること。

    `found_by: where_to_appear|manual|mention` のような列挙に語が残っている
    だけでは足りない——各段の説明文そのものが道具名を名指ししているかを見る。
    """
    text = _read()
    missing = [name for name in MCP_TOOL_NAMES if f"MCP `{name}`" not in text]
    assert not missing, f"「MCP `<name>`」の形で出てこない道具名: {missing}"


def test_thth_share_の語が出てこない():
    """裁定 2026-09-16「横断の泉はやめる」・T4-1「thth share の段落を落とす」。

    `thth share` コマンドそのものは別の掃除で消える（発注書のとおり、この
    テストは docs の書き直しだけを縛る）ので、コードの `thth/share_cli.py` 等
    には触れない——SKILL.md の文字列だけを見る。
    """
    text = _read()
    assert "thth share" not in text, "SKILL.md に `thth share` がまだ残っています"


def test_何を保証するか_拒むか_絶対にしないかの3節が残っている():
    """T4-1「『何を保証するか／拒むか／絶対にしないか』は残す」。"""
    text = _read()
    for title in ("## 何を保証するか", "## 何を拒むか", "## 何を絶対にしないか"):
        assert title in text, f"節が見つかりません: {title}"


def test_泉に落とすかどうかの節が削られている():
    """T4-1「『泉に落とすかどうか（§4）』の節は削る」。"""
    text = _read()
    assert "泉に落とすかどうか" not in text


def _bullets(text: str) -> list:
    """トップレベルの `- ` 箇条書きを 1 項目 1 要素に割る。

    続きの行（インデントされた行）は直前の項目にくっつく——1 つの箇条書き
    項目が複数行にまたがっていても 1 ブロックとして見られるように。
    """
    return re.split(r"\n(?=- )", text)


def test_replies台帳が相手のusernameと本文を残すと書かれている():
    """T9-3: 自分の投稿への返信の台帳（`thth collect` が採り、`thth replies` で
    読む）は相手の `username` と本文を残す——「何を絶対にしないか」の読む口の
    項（`thread_read`・`where_to_appear`・`who_is_this` は何も保存しない・
    絡みの台帳は自分の行為と反応だけ）と混同されないよう、この事実を書く
    （照合「X Developer Agreement と自分の泉」検収 2）。

    **言い方を固定しすぎない検査**: 特定の文言を assert するのではなく、
    `replies` と `username` が**同じ箇条書き項目**に出てくることだけを見る。
    """
    text = _read()
    bullets = _bullets(text)
    hit = [b for b in bullets if "replies" in b and "username" in b]
    assert hit, ("「replies」と「username」が同じ箇条書き項目に出てくる"
                 "ブロックがありません")
