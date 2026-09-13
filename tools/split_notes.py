#!/usr/bin/env python3
"""docs/記録/ と、docs/ 直下に残っている「日誌」寄りの文書を、隣の private
repo `../thth-notes/` へ切り出す計画を出す（設計 v2 §3 public・§7-2 masaru
裁定 2026-09-13: 「public・OSS にする（MIT）。docs/記録/ は別 repo
thth-notes（private）へ移し、本 repo からは参照だけ。」）。

対象:
  1. docs/記録/ 配下の全 .md（丸ごと）。
  2. docs/ 直下で「日誌」と判定した接頭辞のファイル: 出口条件_・引継ぎ_・
     記録_・調査_。

     `依頼_・検収_・発注_・回答_・レビュー_・レビュー依頼_・使用報告_・
     相談_・監査_・申請_・訂正_・仮説_` は tools/docs_reorg.py が既に
     docs/記録/ へ移し終えている（2026-09-13 棚卸し時点で docs/ 直下に
     この接頭辞のファイルは無い）ので (1) に含まれる。本スクリプトの
     TOPLEVEL_DIARY_PREFIXES はそれ以外で残っていた 4 接頭辞だけを持つ。
     新しい日誌ファイルが別の接頭辞で増えたら、ここに足す
     （docs/公開前チェックリスト_2026-09-13.md 参照）。

  移動先はどちらも thth-notes 側の `記録/<ファイル名>`（フラット）。
  docs/ 直下にあった分も含めて「運用日誌」という 1 つの棚にまとめる
  （docs_reorg.py が docs/記録/ に集めているのと同じ考え方）。

除外（動かさない）:
  - PIN_EXCEPTIONS: コードやテストから機能的パスとして直接読まれている
    ファイル（現時点では該当なし。将来 docs_reorg.py の PIN_EXCEPTIONS
    のようなものが増えたらここに足す）。

--dry-run（既定・引数なしでも同じ）: 何も書き換えず、移動計画と
  リンク切れの影響（tests/test_docs_links.py が検査している形の参照）
  の一覧だけを表示する。**v2-4 棚卸し track ではこのモードしか使わない。**

--execute: `../thth-notes` が git repo として実在するときだけ動く
  （無ければ何もせずエラーで終わる。作らない）。2 つの独立した repo
  にまたがる移動なので `git mv` 一発ではできない —
    1. 元ファイルの中身を thth-notes 側の新しい場所へコピーして
       `git -C ../thth-notes add` する。
    2. 元ファイルを `git -C <this repo> rm` する（作業ツリーからも消す）。
  **どちらの repo でも commit はしない。** 実行後に出す TODO に従って、
  (a) 移動によって死ぬリンク・パス表記を手で直し、
  (b) tests/test_docs_links.py の docs/記録/ 存在チェックと除外リストを
      更新し、
  (c) 2 つの repo でそれぞれ commit する。

NOTES_REPO は「このスクリプトファイルの 2 つ上のディレクトリ（repo
ルート）の、さらに隣」として求める。worktree の中でこのスクリプトを
走らせると誤った場所を指す（`.claude/worktrees/<name>/` の隣を見てしまう）
ので、--execute 前に必ず表示される NOTES_REPO の解決結果を確認すること。
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
RECORDS_DIRNAME = "記録"
RECORDS = DOCS / RECORDS_DIRNAME
NOTES_RECORDS_DIRNAME = "記録"


def _main_repo_root() -> pathlib.Path:
    """worktree の中で実行されていても、本体 repo のルートを返す。

    `REPO_ROOT`（= このファイルから辿ったルート）は、worktree の中で
    走らせると worktree 自身（`.claude/worktrees/<name>/`）になって
    しまい、NOTES_REPO のアンカーには使えない（モジュール docstring
    参照）。`git rev-parse --git-common-dir` は worktree からでも
    本体 repo の `.git` を指すので、その親を本体 repo ルートとする。
    git が使えない・repo の外などで失敗したら REPO_ROOT にフォールバック
    する。
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--git-common-dir"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return REPO_ROOT
    if not out:
        return REPO_ROOT
    common_dir = pathlib.Path(out)
    if not common_dir.is_absolute():
        common_dir = (REPO_ROOT / common_dir).resolve()
    return common_dir.parent


NOTES_REPO = _main_repo_root().parent / "thth-notes"

# docs/ 直下に残っている日誌寄りの接頭辞（モジュール docstring 参照）。
TOPLEVEL_DIARY_PREFIXES = ("出口条件_", "引継ぎ_", "記録_", "調査_")

# 機能的パスとして直接読まれるため動かさないファイル（現時点では空）。
PIN_EXCEPTIONS: set[str] = set()

_LINK_RE = re.compile(r"\]\(([^)]+)\)")
_BARE_DOCS_RE = re.compile(r"(?<![\w/])docs/[^\s\"'()「」\]]+\.md")


class MoveItem:
    __slots__ = ("src", "dst_rel")

    def __init__(self, src: pathlib.Path, dst_rel: str) -> None:
        self.src = src  # repo 内の絶対パス
        self.dst_rel = dst_rel  # thth-notes repo ルートからの相対パス


def discover_moves() -> list[MoveItem]:
    items: list[MoveItem] = []

    if RECORDS.is_dir():
        for p in sorted(RECORDS.iterdir()):
            if p.is_file() and p.name not in PIN_EXCEPTIONS:
                items.append(MoveItem(p, f"{NOTES_RECORDS_DIRNAME}/{p.name}"))

    if DOCS.is_dir():
        for p in sorted(DOCS.iterdir()):
            if not p.is_file() or p.name in PIN_EXCEPTIONS:
                continue
            if any(p.name.startswith(prefix) for prefix in TOPLEVEL_DIARY_PREFIXES):
                items.append(MoveItem(p, f"{NOTES_RECORDS_DIRNAME}/{p.name}"))

    return items


def _iter_staying_md_files(moving_names: set[str]) -> list[pathlib.Path]:
    """移動しない側の .md（README.md・docs/ 直下の残り）を返す。

    docs/記録/ 配下は丸ごと移るので走査しない。
    """
    files: list[pathlib.Path] = []
    readme = REPO_ROOT / "README.md"
    if readme.is_file():
        files.append(readme)
    if DOCS.is_dir():
        for p in sorted(DOCS.iterdir()):
            if p.is_file() and p.suffix == ".md" and p.name not in moving_names:
                files.append(p)
    return files


def find_impacted_references(moves: list[MoveItem]) -> list[str]:
    """移動対象を参照している「残る側」の行を、file:line 形式で返す。

    tests/test_docs_links.py と同じ 2 つの書き方（Markdown リンク・
    地の文の docs/....md 表記）を見る。見つかった参照は、実行後に
    リンク切れになる（≒ そのテストが落ちる）候補。
    """
    moving_names = {item.src.name for item in moves}
    findings: list[str] = []

    for path in _iter_staying_md_files(moving_names):
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(REPO_ROOT)
        for lineno, line in enumerate(text.splitlines(), start=1):
            hit_name = None
            for m in _LINK_RE.finditer(line):
                target = m.group(1).split(" ", 1)[0].split("#", 1)[0]
                name = target.rsplit("/", 1)[-1]
                if name in moving_names:
                    hit_name = name
                    break
            if hit_name is None:
                for m in _BARE_DOCS_RE.finditer(line):
                    name = m.group(0).rsplit("/", 1)[-1]
                    if name in moving_names:
                        hit_name = name
                        break
            if hit_name is not None:
                findings.append(f"{rel}:{lineno} -> {hit_name}")

    return findings


def print_plan(moves: list[MoveItem], impacted: list[str]) -> None:
    print(f"NOTES_REPO (解決結果): {NOTES_REPO}")
    notes_is_git = (NOTES_REPO / ".git").exists()
    print(f"NOTES_REPO は git repo か: {notes_is_git}")
    print()

    records_moves = [m for m in moves if RECORDS in m.src.parents]
    toplevel_moves = [m for m in moves if m not in records_moves]

    print(f"移動対象 合計: {len(moves)} 本")
    print(f"  docs/記録/ 配下: {len(records_moves)} 本")
    print(f"  docs/ 直下（日誌接頭辞）: {len(toplevel_moves)} 本")
    if toplevel_moves:
        print("  docs/ 直下の内訳:")
        for m in toplevel_moves:
            print(f"    {m.src.relative_to(REPO_ROOT)} -> thth-notes/{m.dst_rel}")
    print()

    print(f"移動後にリンク切れの候補になる参照: {len(impacted)} 件")
    for line in impacted:
        print(f"  {line}")
    print()

    print("既知のテスト影響（tests/test_docs_links.py）:")
    print("  - test_docs_records_directory_exists_and_has_files が")
    print("    docs/記録/ の実在・非空を assert している。実行後は")
    print("    このテスト自体の削除・更新が要る。")
    print("  - 上の「リンク切れの候補」に挙がった参照は、実行前に")
    print("    書き換える（thth-notes への言及に変える、または削る）か、")
    print("    test_docs_links.py の EXCLUDED_PREFIXES / EXCLUDED_PATHS へ")
    print("    明示的に足す必要がある。")


def do_execute(moves: list[MoveItem]) -> int:
    if not NOTES_REPO.exists():
        print(f"エラー: {NOTES_REPO} が無い。作らない方針なので何もしません。", file=sys.stderr)
        return 1
    if not (NOTES_REPO / ".git").exists():
        print(f"エラー: {NOTES_REPO} は git repo に見えない（.git が無い）。何もしません。", file=sys.stderr)
        return 1

    moved: list[MoveItem] = []
    for item in moves:
        dst = NOTES_REPO / item.dst_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(item.src.read_bytes())
        subprocess.run(["git", "-C", str(NOTES_REPO), "add", item.dst_rel], check=True)
        subprocess.run(
            ["git", "rm", "-q", str(item.src.relative_to(REPO_ROOT))],
            check=True,
            cwd=REPO_ROOT,
        )
        moved.append(item)

    print(f"移動を実行: {len(moved)} 本（どちらの repo でも commit はしていません）")
    print()
    print("残っている作業（このスクリプトはやらない）:")
    print("  1. 死んだリンク・パス表記を直す（--dry-run の出力を参照）。")
    print("  2. tests/test_docs_links.py の docs/記録/ 存在チェックと除外リストを更新する。")
    print(f"  3. {REPO_ROOT} 側で commit（削除）。")
    print(f"  4. {NOTES_REPO} 側で commit（追加）。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="実際に移す（既定は --dry-run 相当）。../thth-notes が git repo として無いと何もしない。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="明示的に指定しても既定の動作と同じ（--execute を渡さない限り常に dry-run）。",
    )
    args = parser.parse_args()

    moves = discover_moves()
    impacted = find_impacted_references(moves)

    if args.execute:
        return do_execute(moves)

    print_plan(moves, impacted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
