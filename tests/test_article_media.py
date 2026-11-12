"""記事の口座かを問う 1 か所（`thth/article_media.py`）の柵。

以前は core・cli・events_cli・dashboard・doctor などが `media == "hatenablog"` と媒体名で分岐していた。
出し先を足すたびに全部を触ることになるので、問いを 1 つに寄せた。**新しく媒体名で分岐を書くと赤になる。**
"""
from __future__ import annotations

import re
from pathlib import Path

from thth import article_media, principals

ROOT = Path(__file__).resolve().parent.parent
PATTERN = re.compile(r"""(get\(['"]media['"]\)|\bmedia|\bmedium|\[['"]medium['"]\])\s*(==|!=)\s*['"]hatenablog['"]"""
                     r"""|in \(['"]hatenablog['"]""")
# 残してよい所（理由つき）。行の数で縛る（増えたら赤）。
ALLOWED = {
    # はてなの雛形のダミー（demo.hatenablog.com・example.invalid）を名指す検査。はてなの値そのものの話。
    "thth/accounts.py": 2,
    # シナリオの段の媒体（宣言の `media: [hatenablog]`）を数える所。段の名前で、口座の分岐ではない。
    "thth/scenario.py": 4,
    # 説明の文の中（コードではない）。
    "thth/article_media.py": 1,
    "thth/adapters/hatenablog.py": 1,
}


def _hits():
    out = {}
    for path in sorted((ROOT / "thth").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("thth/refusal_catalog"):
            continue
        n = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if PATTERN.search(line))
        if n:
            out[rel] = n
    return out


def test_媒体名の分岐は増えない():
    assert _hits() == ALLOWED


def test_壊れた例_媒体名の分岐を拾う():
    for line in ('if cfg.get("media") == "hatenablog":', "if medium != 'hatenablog':",
                 'x = r["medium"] == "hatenablog"', 'if media in ("hatenablog", "x"):'):
        assert PATTERN.search(line), line
    assert not PATTERN.search("if article_media.is_article_account(cfg):")


def test_問いの答え():
    assert article_media.is_article_account({"media": "hatenablog"})
    assert not article_media.is_article_account({"media": "threads"})
    assert not article_media.is_article_account(None)
    assert article_media.has({"media": "hatenablog"}, "reactions")
    assert not article_media.has("threads", "reactions")
    # 主体を通す口座の問いは同じ答え（`principals.uses_principal` はここを引く）。
    for media in ("hatenablog", "threads", "bluesky", None):
        assert principals.uses_principal({"media": media}) == article_media.is_article_account({"media": media})
