#!/usr/bin/env python3
"""docs/ の運用日誌（依頼・検収・発注 等）を docs/記録/ へ移し、参照している
リンク・パス文字列を新しい場所へ書き換える。

再実行できる: 既に移動済みのファイルは飛ばし、書き換え済みの文字列は
二重に書き換えない（パターンが厳密なので冪等）。他 Track の merge 後に
もう一度当てる運用を想定している（設計 §3）。

動かすもの（接頭辞）: 依頼・検収・発注・回答・レビュー・レビュー依頼・
使用報告・相談・監査・申請・訂正・仮説

動かさないもの（接頭辞。製品文書）: 設計・使い方・手順・語彙・型・定型・
引継ぎ・調査・記録_URL…・出口条件・資料・実装・導入

書き換える対象: docs/ 配下（記録/ 含む）の全 .md・README.md・
tests/conftest.py のコメント中のパス文字列。文書の中身（本文の主張）は
一切変えない。docs/sns/queue/... のような契約上の仮想パスは触らない
（そもそも移動対象の接頭辞に一致しない）。

例外: `仮説_露出と反応率_v1.json` は接頭辞上は移動対象に見えるが、
tests/test_r2_r4_r5.py・tests/test_hypotheses.py が
`docs/仮説_露出と反応率_v1.json` を機能的なファイルパスとして直接
読んでいる。本スクリプトの書き換え対象は文書と conftest.py のコメント
に限られ、この 2 つのテストファイルは書き換え対象外（作法: 文書の中身は
書き換えるが、テストの実装コードは触らない）。動かすとテストが壊れるため、
移動しない。
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
RECORDS_DIRNAME = "記録"
RECORDS = DOCS / RECORDS_DIRNAME

MOVE_PREFIXES = [
    "依頼",
    "検収",
    "発注",
    "回答",
    "レビュー依頼",  # レビュー より先に見る（どちらも移動対象なので判定順は結果に影響しないが明示）
    "レビュー",
    "使用報告",
    "相談",
    "監査",
    "申請",
    "訂正",
    "仮説",
]

# 接頭辞上は移動対象だが、コードから機能的パスとして参照されているため
# 動かさないファイル（理由はモジュール docstring 参照）。
PIN_EXCEPTIONS = {"仮説_露出と反応率_v1.json"}

# 書き換え走査の対象にする追加ファイル（.md 以外）。コメント中の
# パス文字列だけを書き換える。
EXTRA_REWRITE_TARGETS = [REPO_ROOT / "tests" / "conftest.py"]


def _matches_move_prefix(name: str) -> str | None:
    for prefix in MOVE_PREFIXES:
        if name.startswith(prefix + "_"):
            return prefix
    return None


def discover_names() -> tuple[set[str], set[str]]:
    """いまの docs/ の状態から (moved, kept) の完全な集合を返す。

    移動済み（docs/記録/ 直下）のファイルも moved に含めるので、
    このスクリプトを 2 回目に走らせても同じ (moved, kept) が出る。
    """
    moved: set[str] = set()
    kept: set[str] = set()

    if RECORDS.is_dir():
        for p in RECORDS.iterdir():
            if p.is_file():
                moved.add(p.name)

    for p in DOCS.iterdir():
        if not p.is_file():
            continue
        name = p.name
        if name in PIN_EXCEPTIONS:
            kept.add(name)
            continue
        prefix = _matches_move_prefix(name)
        if prefix:
            moved.add(name)
        else:
            kept.add(name)

    return moved, kept


def do_moves(moved: set[str]) -> list[str]:
    """docs/<name> がまだ docs/ 直下にあるものだけ git mv する。"""
    performed: list[str] = []
    for name in sorted(moved):
        src = DOCS / name
        dst = RECORDS / name
        if dst.exists():
            continue  # 既に移動済み
        if not src.exists():
            continue  # 移動対象がそもそも docs/ 直下に無い
        RECORDS.mkdir(exist_ok=True)
        subprocess.run(
            ["git", "mv", str(src.relative_to(REPO_ROOT)), str(dst.relative_to(REPO_ROOT))],
            check=True,
            cwd=REPO_ROOT,
        )
        performed.append(name)
    return performed


def _docs_prefixed_pattern(name: str) -> re.Pattern[str]:
    # "docs/<name>" だが "nigamilab/docs/<name>" や "watchtower/docs/<name>"
    # のように別 repo 配下の docs/ を指しているものは弾く（直前が "/" や
    # 単語文字でないことを要求）。
    return re.compile(r"(?<![\w/])docs/" + re.escape(name) + r"(?!\w)")


def _bare_pattern(name: str) -> re.Pattern[str]:
    # ディレクトリ接頭辞が一切無い裸のファイル名参照だけを拾う
    # （"docs/" や "記録/" や "../" が前置されているものは対象外）。
    return re.compile(r"(?<![\w/])" + re.escape(name) + r"(?!\w)")


def rewrite_text(text: str, *, own_moved: bool, moved: set[str], kept: set[str]) -> tuple[str, int]:
    """1 ファイル分のテキストを書き換える。戻り値は (新テキスト, 置換件数)。"""
    count = 0

    # 素通し 1: "docs/<name>.md" 形式（README.md や、同じ docs/ 内での
    # 明示的なフルパス表記）。移動先が docs/記録/ になった名前だけ直す。
    # この形は「repo ルートからの相対」という表記の約束であり、
    # 書いている側のファイルがどこにあるかに関係なく成り立つ。
    for name in moved:
        pattern = _docs_prefixed_pattern(name)
        new_text, n = pattern.subn(f"docs/{RECORDS_DIRNAME}/{name}", text)
        if n:
            text = new_text
            count += n

    # 素通し 2: 接頭辞の無い裸のファイル名参照（同じディレクトリにある
    # という前提で書かれたもの）。書いている側自身が動いたかどうかで
    # 足す接頭辞が変わる。
    if own_moved:
        # docs/記録/ に移った側から、docs/ 直下に残った相手への参照には
        # "../" を足す。記録/ 同士の参照は元々同じディレクトリのまま
        # なので触らない。
        for name in kept:
            pattern = _bare_pattern(name)
            new_text, n = pattern.subn(f"../{name}", text)
            if n:
                text = new_text
                count += n
    else:
        # docs/ 直下に残った側から、docs/記録/ に移った相手への参照には
        # "記録/" を足す。
        for name in moved:
            pattern = _bare_pattern(name)
            new_text, n = pattern.subn(f"{RECORDS_DIRNAME}/{name}", text)
            if n:
                text = new_text
                count += n

    return text, count


def rewrite_all(moved: set[str], kept: set[str]) -> tuple[int, int]:
    """対象ファイルを全部書き換える。戻り値は (書き換えたファイル数, 置換件数合計)。"""
    targets: list[tuple[pathlib.Path, bool]] = []

    for p in sorted(DOCS.rglob("*.md")):
        own_moved = RECORDS in p.parents
        targets.append((p, own_moved))

    readme = REPO_ROOT / "README.md"
    if readme.is_file():
        targets.append((readme, False))

    for p in EXTRA_REWRITE_TARGETS:
        if p.is_file():
            targets.append((p, False))

    files_changed = 0
    total_replacements = 0
    for path, own_moved in targets:
        text = path.read_text(encoding="utf-8")
        new_text, n = rewrite_text(text, own_moved=own_moved, moved=moved, kept=kept)
        if n:
            path.write_text(new_text, encoding="utf-8")
            files_changed += 1
            total_replacements += n

    return files_changed, total_replacements


def main() -> int:
    moved, kept = discover_names()

    moves_performed = do_moves(moved)

    # 移動後の状態で再度 (moved, kept) を確認してから書き換える
    # （do_moves 前後で集合自体は変わらない設計だが、念のため）。
    moved, kept = discover_names()

    files_changed, total_replacements = rewrite_all(moved, kept)

    print(f"moved this run: {len(moves_performed)}")
    for name in moves_performed:
        print(f"  git mv docs/{name} -> docs/{RECORDS_DIRNAME}/{name}")
    print(f"moved total (docs/記録/ 内): {len(moved)}")
    print(f"kept total (docs/ 直下・製品文書): {len(kept)}")
    print(f"rewrote files: {files_changed}")
    print(f"path replacements: {total_replacements}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
