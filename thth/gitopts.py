"""口座の repo（レーンの clone）に打つ git の共通の設定。

**自動の gc を止める**（運用 #7497・2026-11-11）。VM のレーンの repo は部分 clone
（`partialclonefilter=blob:none`）で、git 2.43 の自動の gc が reflog だけから辿れる
commit を消したあと commit-graph がその commit を指したまま残り、`git log` が落ちて
はてなの記事が `article_not_committed` で止まった（11/7 asmon・11/11 kopicha と
nigamilab）。壊れるのは部分 clone の repo だけで、引き金は道具の commit・fetch・
rebase のあとに走る自動の gc。道具の打つ git では自動の gc を走らせない
（repo の設定は書き換えない。人が手で打つ git には効かない）。
掃除は要らない: 記事と台帳の repo で、溜まるのは小さな loose object だけ。
"""

NO_AUTO_GC = ("-c", "gc.auto=0", "-c", "maintenance.auto=false")


def git(repo_dir, *args):
    """`git -C <repo> <自動の gc を止める設定> <args>` の argv。"""
    return ["git", *NO_AUTO_GC, "-C", str(repo_dir), *args]
