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
    # 利用者 repo 側の契約上のパス（設計 §4.1・§4.4）。この repo には無いのが正しい。
    # `docs/sns/queue/` だけ除外していたが、`docs/sns/返信の方針.md` のような
    # 利用者 repo の文書への言及も同じ扱い（2026-09-13・運用の引継ぎ書で発火）。
    "docs/sns/",
    # 運用日誌は private repo `thth-notes` の `記録/` へ移した（2026-09-14・
    # 設計 v2 §3 public・§7-2 masaru 裁定。tools/split_notes.py --execute）。
    # 本 repo に残る `docs/記録/...` という表記は、移動前の状態を書き留めた
    # 記録・転記（例: docs/公開前チェックリスト_2026-09-13.md の dry-run 出力）
    # なので、**実在しないのが正しい**。文書の中身は書かれた当時のまま残す。
    "docs/記録/",
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
    # docs/ 直下にあった日誌（出口条件_・引継ぎ_・記録_・調査_）も 2026-09-14 に
    # thth-notes/記録/ へ移した。docs/公開前チェックリスト_2026-09-13.md が
    # 引用している dry-run 出力の中にこれらのパスが**移動前の表記のまま**
    # 残っている（転記なので書き換えない）。上と同じ理由で実在しないのが正しい。
    # （`docs/引継ぎ_トピックの棚_…_2026-09-12.md` は
    # tests/test_value_domains_shown.py が直接読むので本 repo に残した。
    # tools/split_notes.py の PIN_EXCEPTIONS 参照。ここには足さない。）
    "docs/出口条件_編集知識の蓄積_第2段階_2026-09-12.md",
    "docs/引継ぎ_実測と未決の論点_2026-09-11.md",
    "docs/引継ぎ_編集知識の蓄積_第1段階_2026-09-11.md",
    "docs/引継ぎ_運用セッション_2026-09-13.md",
    "docs/引継ぎ_開発セッション_2026-09-12.md",
    "docs/引継ぎ_開発セッション_2026-09-13.md",
    "docs/記録_URLの使用を限定した期間_2026-09-12.md",
    "docs/調査_配布の権限分離_2026-09-12.md",
}

# **この repo の文章作法は `…`（U+2026）**。ASCII の "..." だけ持っていたので、
# 実際に使われる綴りを取り逃がしていた（監査 2・2026-09-12）。
_PLACEHOLDER_MARKERS = ("<", ">", "*", "{", "}", "...", "…")

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


def test_docs_records_directory_is_empty_or_absent():
    """`docs/記録/` は**無いのが正しい**（あるなら空でなければならない）。

    2026-09-14 に運用日誌を private repo `thth-notes` の `記録/` へ移した
    （設計 v2 §3 public・§7-2 masaru 裁定。tools/split_notes.py --execute）。
    以前はここで `docs/記録/` の実在・非空を assert していたが、分離後は
    逆に「日誌が本 repo へ戻ってきていないこと」を守る検査にする。
    ディレクトリごと無くなっているのが通常の状態なので、無ければ通る。
    """
    records = DOCS / "記録"
    if not records.exists():
        return
    strays = sorted(p.name for p in records.iterdir())
    assert not strays, (
        "docs/記録/ に日誌が戻っている（thth-notes/記録/ へ置くこと）:\n"
        + "\n".join(strays)
    )


def test_thth_share_の語が出てこない():
    """`thth share` は T5-1 で消した（裁定 2026-09-16「横断の泉はやめる」・
    T5 発注書「`thth share` を消す」）。利用者向けの現在形の文書（README・
    README.en・llms.txt・docs/usage.en.md・skills/）に、消したコマンドの
    綴りが 1 つでも残っていたら落ちる（`top_share`・`shares`・
    `share_to_instagram` は別の意味なので検査の対象にしない——ここは
    文字どおり `"thth share"` という並びだけを見る）。
    """
    targets = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "README.en.md",
        REPO_ROOT / "llms.txt",
        DOCS / "usage.en.md",
    ]
    targets += sorted((REPO_ROOT / "skills").rglob("*.md"))

    hits: list[str] = []
    for path in targets:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "thth share" in text:
            hits.append(str(path.relative_to(REPO_ROOT)))
    assert not hits, "`thth share` がまだ残っている:\n" + "\n".join(hits)
