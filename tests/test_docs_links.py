"""docs/ 配下（docs/記録/ 含む）と README.md のリンク・パス文字列が実在することを検査する。

チェックする 2 つの書き方:
1. Markdown のリンク構文 `[label](path.md)` — path はリンクを書いたファイル自身の
   ディレクトリから見た相対パスとして解決する（実際のレンダリングと同じ規則）。
2. 地の文・バッククォート中の `docs/....md` という表記 — こちらは repo ルートから
   の相対パスという書き方の約束として扱う（実際のリンクではなく人が読む説明）。

存在しないリンクがあれば **落ちる**（警告にしない・作法 5・loud reject）。

除外するもの:
- 外部 URL（http:// / https:// / mailto:）
- `docs/sns/queue/...` のような、投稿キューの契約上の仮想パス（実在しないのが正しい）
- プレースホルダを含むパターン（`<n>`・`*`・`{...}` や three-dot の `...` を含むもの）。
  実在のファイル名ではなく、命名の型を示しているだけの記法。
- `EXCLUDED_PATHS` に明示した、いまはまだ存在しない前方参照
  （他 Track がこの後で作る予定の文書・過去の文書構成案など。理由はコメント参照）。
- repo の外（`../../watchtower/...` のような他 repo への相対参照）を指すもの。
"""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"

# 実在しないのが正しい契約上の仮想パス（テスト fixture 内で使われる架空のキューパス）。
EXCLUDED_PREFIXES = (
    "docs/sns/queue/",
)

# まだ存在しない前方参照・過去の文書構成案。文書の中身は書き換えない方針のため
# ここで明示的に除外する（本文の主張・提案は書かれた当時のまま残す）。
EXCLUDED_PATHS = {
    # docs/記録/レビュー_外部_2026-09-09.md の提案（当時の分割案。採用されていない）。
    "docs/仕様_THTH.md",
    "docs/決定の記録_THTH.md",
    # docs/実装_トピック提案_2026-09-11.md が「取り込む予定」と書いている、
    # まだ取り込まれていない外部ファイル。
    "docs/設計_記事別トピック提案_2026-09-11.md",
}

_PLACEHOLDER_MARKERS = ("<", ">", "*", "{", "}", "...")

_LINK_RE = re.compile(r"\]\(([^)]+)\)")
_BARE_DOCS_RE = re.compile(r"(?<![\w/])docs/[^\s\"'()「」\]]+\.md")


def _looks_like_placeholder(target: str) -> bool:
    return any(marker in target for marker in _PLACEHOLDER_MARKERS)


def _strip_link_target(raw: str) -> str:
    # `path "Title"` や `path#anchor` の余分を落とす。
    target = raw.strip()
    target = target.split(" ", 1)[0]
    target = target.split("#", 1)[0]
    return target


def _is_external(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:"))


def _iter_target_files():
    files = sorted(DOCS.rglob("*.md"))
    readme = REPO_ROOT / "README.md"
    if readme.is_file():
        files.append(readme)
    return files


def _collect_references(path: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    """(書かれていた生のパス文字列, 解決した絶対パス) のリストを返す。

    存在しないと判定してよいもの（外部 URL・仮想パス・プレースホルダ・
    明示的な除外リスト・repo の外）はここで既に取り除いてある。
    """
    text = path.read_text(encoding="utf-8")
    refs: list[tuple[str, pathlib.Path]] = []

    # 1. Markdown リンク構文: 書いたファイル自身のディレクトリからの相対。
    for m in _LINK_RE.finditer(text):
        target = _strip_link_target(m.group(1))
        if not target or _is_external(target) or not target.endswith(".md"):
            continue
        if _looks_like_placeholder(target):
            continue
        resolved = (path.parent / target).resolve()
        refs.append((target, resolved))

    # 2. 地の文の "docs/....md" 表記: repo ルートからの相対という約束。
    for m in _BARE_DOCS_RE.finditer(text):
        target = m.group(0)
        if _looks_like_placeholder(target):
            continue
        resolved = (REPO_ROOT / target).resolve()
        refs.append((target, resolved))

    return refs


def _should_skip(target: str, resolved: pathlib.Path) -> bool:
    if any(target.startswith(prefix) for prefix in EXCLUDED_PREFIXES):
        return True
    if target in EXCLUDED_PATHS:
        return True
    try:
        resolved.relative_to(REPO_ROOT)
    except ValueError:
        # repo の外（他 repo への相対参照）を指している。本タスクの検査対象外。
        return True
    return False


def test_docs_links_resolve_to_real_files():
    missing: list[str] = []

    for path in _iter_target_files():
        rel_path = path.relative_to(REPO_ROOT)
        for target, resolved in _collect_references(path):
            if _should_skip(target, resolved):
                continue
            if not resolved.exists():
                missing.append(f"{rel_path}: '{target}' -> {resolved.relative_to(REPO_ROOT)} が存在しない")

    assert not missing, "存在しないリンク・パス文字列:\n" + "\n".join(sorted(missing))


def test_docs_records_directory_exists_and_has_files():
    records = DOCS / "記録"
    assert records.is_dir(), "docs/記録/ が無い（tools/docs_reorg.py を走らせたか確認）"
    assert any(records.iterdir()), "docs/記録/ が空（移動が実行されていない）"
