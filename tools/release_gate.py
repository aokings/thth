#!/usr/bin/env python3
"""release の関門（設計「監査と審査の機械化と前向きな断り」§6・段 D）。

本番に出す前（release の枝を進める・tag `vX.Y.Z` を push する前）に、そろうべきものがそろっているかを
機械で確かめる。手元でも CI（`.github/workflows/publish.yml` の `gate` の job）でも同じ命令で動く。

見るもの（どれも **その版の commit の中身** を git から読む。作業木の未 commit の変更は見ない）:

1. その commit で main の試験（`test.yml`）が緑（`gh run list --commit`）。
2. その版のリリースノート `docs/リリースノート_<版>_*.md` があり、`thth/VERSION` と `server.json` の版が
   そろう。その版の tag が別の commit に打ってあれば、版を上げていない。
3. 前の tag からの差分が危ない領域（下の `DANGER_AREAS`・表はここの 1 か所）に触るなら、その commit を覆う
   監査の書（`docs/監査_*.md`・頭の形は `docs/定型_監査の書.md`）があり、「危ない」が 0（か却下の理由）。
   LLM の監査が必須なのは危ない領域に触る版だけ（masaru 2026-11-03・設計 §8 の 2）。
4. 互換の柵（`COMPAT_TESTS`）が緑。

欠けたものがあれば、1 つごとに `release_blocked: <符丁>` の 1 行と、目録（`thth/refusal_catalog_part5.py`）の
なぜ・通すには・もっと良く の 3 行（と、見たものの 1 行）を出し、rc 1。通るときは黙って rc 0
（原則 `docs/原則_前向きな断り.md`）。

使い方: `python3 tools/release_gate.py [--ref <commit>]`（既定は HEAD）。
`--skip-ci` は CI の結果を見ない（CI の中でまだ main の試験の結果が無い枝を確かめる時だけ。出すときは付けない）。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 危ない領域（設計 §6 の初めの表）。名前 → 当たるパスの型（repo の根からの fnmatch）。
# 足すときはここだけを書く（関門・試験・監査の書の雛形はこの表を読む）。
DANGER_AREAS: dict[str, tuple[str, ...]] = {
    "server_writes": ("thth/server_writes.py",),
    "server_reads": ("thth/server_reads.py",),
    "guard": ("thth/guard.py",),
    "plaza_redact": ("thth/plaza_redact.py",),
    "leave": ("thth/leave.py", "thth/leave_gate.py", "thth/ads/leave.py"),
    "callback": ("callback/*",),
    "privacy_terms": ("callback/public/privacy/*", "callback/public/terms/*", "tools/build_site.py"),
    "approval": ("thth/approval.py", "thth/approval_ledger.py", "thth/approve_pending.py"),
    # 外の LLM と thth.me の鍵が入る口（11/04 に足した: 段 B が両方の断りの出力を変えたのに、表に無くて
    # 監査が要らないと判断されたため）。
    "mcp": ("mcp/server.py",),
    "remote": ("thth/remote.py",),
    # Meta に書く口と、利用者の鍵を一時的に持つ認可（11/09 の監査 5.14.0 で足した）。
    # meta_write: Meta への書き込みの共通部・出来事の送信・ブランドの承認・商品タグ
    "meta_write": ("thth/meta_write.py", "thth/meta_events.py", "thth/meta_brand.py", "thth/meta_shop.py",
                   "thth/wa/*"),
    # auth_keys: 認可の流れと、認可のときに鍵やメールを扱う所
    "auth_keys": ("thth/authflow.py", "thth/adapters/auth_*.py", "thth/wa/auth.py"),
    # お金が動く広告の書く口と、広告と各媒体の認可（11/10・監査 5.14.0b の「危ない領域の穴」）。
    # facebook.py・instagram.py の `_write` は入れない: 読む口と同じ大きなファイルで、入れると媒体の読みの
    # 直しのたびに監査が要る。`_write` を通る書き込みは、呼ぶ側（meta_write ほか）が表に入っている。
    # `thth/ads/cli.py`（二段確認の入口）・`ads/watch.py`・`server_ads.py` も入れない: 書き込みと予算の判断は
    # 上の 3 つに集まっていて、入口の直しは CI の全件と互換の柵で縛る（監査 5.14.2 の気づき）。
    "ads_write": ("thth/ads/graphwrite.py", "thth/ads/ops.py", "thth/ads/budget.py"),
    "oauth": ("thth/oauth.py", "thth/ads/auth.py"),
    # 店を読む口（11/12・設計 ショップとWordPressの口 §6）: 新しい読む口・鍵の受け取り・queue に下書きを書く口。
    "store_read": ("thth/shop/*",),
    # WordPress に記事を出す口（5.16.0）: 鍵（アプリケーションパスワード）を送る adapter と、承認の指紋に入る
    # 本文の変換（規則が変われば承認が古くなる・生の HTML を通さない柵）。
    "wordpress_write": ("thth/adapters/wordpress.py", "thth/wp_html.py"),
    # 金庫と庭の口（11/12 夜・段 1・仮）: 台帳を外の金庫へ送る口・戻しの文字列の通り道・誰でも読める庭へ置く口と、
    # 出す前の検査（script・外の資源・非公開の商品の柵）。
    "vault_garden": ("thth/vault/*",),
}

# 互換の柵（設計 §6 の ④）。目録の retired・MCP の古い読みの形・server.json・privacy とコードの一致。
COMPAT_TESTS = (
    "tests/test_refusal_catalog.py",
    "tests/test_v212_mcp_compat.py",
    "tests/test_server_json.py",
    "tests/test_privacy_matches_code.py",
)

# privacy の生成物に、施行日が未設定のしるしが残っていないか（build_site.py が None のとき出す表示）。
PRIVACY_PAGE = "callback/public/privacy/index.html"
PRIVACY_UNSET_MARKS = ("Unpublished update", "未公開の更新案")

TEST_WORKFLOW = "test.yml"
AUDIT_GLOB = "docs/監査_*.md"
AUDIT_TEMPLATE = "docs/定型_監査の書.md"
FENCE_HEADING = re.compile(r"^##\s*柵にしたもの\s*$", re.M)
REQUIRED_KEYS = ("range", "dangers", "fixed", "auditor")
VERSION_FORM = re.compile(r"\d+\.\d+\.\d+\Z")


@dataclass
class Block:
    code: str
    seen: str = ""


@dataclass
class Audit:
    path: str
    start: str | None = None
    end: str | None = None
    dangers: int = 0
    waived: str = ""
    fixed: list[str] = field(default_factory=list)
    auditor: str = ""
    problem: str | None = None


def _refuse(code: str, seen: str = "") -> Block:
    return Block(code, seen)


# ---- git ----

def git(root: Path, *args: str, check: bool = True) -> str:
    # 日本語のファイル名（リリースノート・監査の書）を "\343…" に包ませない。
    done = subprocess.run(["git", "-C", str(root), "-c", "core.quotepath=false", *args],
                          capture_output=True, text=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout if done.returncode == 0 else ""


def show(root: Path, ref: str, path: str) -> str | None:
    done = subprocess.run(["git", "-C", str(root), "show", f"{ref}:{path}"], capture_output=True)
    return done.stdout.decode("utf-8") if done.returncode == 0 else None


def is_ancestor(root: Path, older: str, newer: str) -> bool:
    return subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", older, newer],
                          capture_output=True).returncode == 0


def resolve(root: Path, rev: str) -> str | None:
    out = git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", check=False).strip()
    return out or None


def _version_key(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in text.split("."))


def previous_tag(root: Path, sha: str, version: str) -> str | None:
    """前の tag: その commit から辿れる `v*` の tag のうち、その commit 自身に打ったもの（tag の push の CI）を除き、
    版が `version` 以下で一番大きいもの（版を上げ忘れたときは同じ版の tag からの差分を見る）。"""
    tags = [t for t in git(root, "tag", "--merged", sha, "--list", "v*").split()
            if VERSION_FORM.fullmatch(t[1:]) and resolve(root, f"refs/tags/{t}") != sha]
    if VERSION_FORM.fullmatch(version):
        tags = [t for t in tags if _version_key(t[1:]) <= _version_key(version)]
    return max(tags, key=lambda t: _version_key(t[1:]), default=None)


# ---- 危ない領域 ----

def areas_of(path: str) -> list[str]:
    return [name for name, patterns in DANGER_AREAS.items()
            if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)]


def danger_commits(root: Path, base: str | None, sha: str) -> dict[str, set[str]]:
    """前の tag..その commit で危ない領域のファイルを変えた commit → 当たった領域の名前。"""
    span = f"{base}..{sha}" if base else sha
    out = git(root, "log", "--format=@%H", "--name-only", "--no-renames", span)
    found: dict[str, set[str]] = {}
    current = None
    for line in out.splitlines():
        if line.startswith("@"):
            current = line[1:]
        elif line.strip() and current:
            names = areas_of(line.strip())
            if names:
                found.setdefault(current, set()).update(names)
    return found


# ---- 監査の書 ----

def parse_audit(path: str, text: str) -> Audit:
    """頭（`---` で挟んだ `鍵: 値` の行）と末尾の「## 柵にしたもの」の節を読む。"""
    audit = Audit(path)
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not match:
        audit.problem = "頭（--- で挟んだ欄）が無い"
        return audit
    head: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            audit.problem = f"頭の行が「鍵: 値」でない: {line.strip()}"
            return audit
        head[key.strip()] = value.split(" #", 1)[0].strip()
    missing = [key for key in REQUIRED_KEYS if key not in head]
    if missing:
        audit.problem = f"頭の欄が無い: {', '.join(missing)}"
        return audit
    start, sep, end = head["range"].partition("..")
    if not sep or not start.strip() or not end.strip():
        audit.problem = f"range が「前の tag..監査した commit」の形でない: {head['range']}"
        return audit
    audit.start, audit.end = start.strip(), end.strip()
    if not re.fullmatch(r"\d+", head["dangers"]):
        audit.problem = f"dangers が数でない: {head['dangers']}"
        return audit
    audit.dangers = int(head["dangers"])
    audit.waived = head.get("waived", "")
    audit.fixed = [c for c in re.split(r"[\s,、]+", head["fixed"]) if c and c not in ("なし", "-")]
    audit.auditor = head["auditor"]
    if not audit.auditor:
        audit.problem = "auditor が空"
        return audit
    fence = FENCE_HEADING.search(text)
    if not fence:
        audit.problem = "末尾の「## 柵にしたもの」の節が無い"
        return audit
    body = text[fence.end():]
    body = re.split(r"^##\s", body, maxsplit=1, flags=re.M)[0]
    if not re.sub(r"<!--.*?-->", "", body, flags=re.S).strip():
        audit.problem = "「## 柵にしたもの」の節が空（無ければ「なし」と理由を書く）"
    return audit


def audits_at(root: Path, sha: str) -> list[Audit]:
    names = git(root, "ls-tree", "-r", "--name-only", sha, "--", "docs/").splitlines()
    found = []
    for name in names:
        if not fnmatch.fnmatchcase(name, AUDIT_GLOB) or name == AUDIT_TEMPLATE:
            continue
        text = show(root, sha, name) or ""
        if not text.startswith("---\n"):
            continue  # 頭の形ができる前の監査の書（読み物として残す・関門は見ない）
        found.append(parse_audit(name, text))
    return found


def covers(root: Path, audit: Audit, commit: str) -> bool:
    if audit.problem:
        return False
    for fixed in audit.fixed:
        full = resolve(root, fixed)
        if full and full == commit:
            return True
    start, end = resolve(root, audit.start or ""), resolve(root, audit.end or "")
    if not start or not end:
        return False
    return is_ancestor(root, commit, end) and not is_ancestor(root, commit, start)


# ---- CI ----

def ci_state(root: Path, sha: str) -> tuple[str, str]:
    """`(green|red|waiting|unreadable, 見たもの)`。main の試験（test.yml）の、その commit の一番新しい run。"""
    try:
        done = subprocess.run(["gh", "run", "list", "--commit", sha, "--workflow", TEST_WORKFLOW, "--limit", "20",
                               "--json", "status,conclusion,databaseId,createdAt"],
                              capture_output=True, text=True, cwd=str(root), timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "unreadable", f"gh を動かせない: {exc.__class__.__name__}"
    if done.returncode != 0:
        return "unreadable", (done.stderr.strip().splitlines() or ["gh が 0 以外で終わった"])[-1]
    try:
        runs = json.loads(done.stdout or "[]")
    except json.JSONDecodeError:
        return "unreadable", "gh の出力が JSON でない"
    if not runs:
        return "waiting", f"{sha[:7]} の {TEST_WORKFLOW} の run が無い"
    run = max(runs, key=lambda r: r.get("createdAt") or "")
    seen = f"{TEST_WORKFLOW} run {run.get('databaseId')}（{run.get('status')}・{run.get('conclusion') or '-'}）"
    if run.get("status") != "completed":
        return "waiting", seen
    return ("green" if run.get("conclusion") == "success" else "red"), seen


# ---- 互換の柵 ----

def run_compat(root: Path) -> tuple[bool, str]:
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *COMPAT_TESTS],
                          capture_output=True, text=True, cwd=str(root))
    last = (done.stdout.strip().splitlines() or [""])[-1]
    failed = [line.split(" ", 1)[1].split(" - ", 1)[0] for line in done.stdout.splitlines()
              if line.startswith("FAILED ")]
    seen = "・".join(failed[:5]) if failed else last
    return done.returncode == 0, seen


# ---- 関門 ----

def check(root: Path = ROOT, ref: str = "HEAD", *, ci=ci_state, compat=run_compat, skip_ci: bool = False) -> list[Block]:
    blocks: list[Block] = []
    sha = resolve(root, ref)
    if sha is None:
        raise RuntimeError(f"commit が見つからない: {ref}")

    # ① CI
    if not skip_ci:
        state, seen = ci(root, sha)
        if state == "red":
            blocks.append(_refuse("release_ci_red", seen))
        elif state == "waiting":
            blocks.append(_refuse("release_ci_waiting", seen))
        elif state != "green":
            blocks.append(_refuse("release_ci_unreadable", seen))

    # ② 版とリリースノート
    version = (show(root, sha, "thth/VERSION") or "").strip()
    try:
        server = json.loads(show(root, sha, "server.json") or "{}")
    except json.JSONDecodeError:
        server = {}
    versions = {server.get("version")} | {p.get("version") for p in server.get("packages") or []}
    if not VERSION_FORM.fullmatch(version) or versions != {version}:
        blocks.append(_refuse("release_version_mismatch",
                              f"thth/VERSION {version or '-'}・server.json {sorted(map(str, versions))}"))
    tagged = resolve(root, f"refs/tags/v{version}") if version else None
    if tagged and tagged != sha:
        blocks.append(_refuse("release_version_not_bumped", f"v{version} は {tagged[:7]} に打ってある（今は {sha[:7]}）"))
    docs = git(root, "ls-tree", "--name-only", sha, "docs/").splitlines()
    if not any(fnmatch.fnmatchcase(name, f"docs/リリースノート_{version}_*.md") for name in docs):
        blocks.append(_refuse("release_notes_missing", f"docs/リリースノート_{version}_*.md が {sha[:7]} に無い"))

    # ③ 危ない領域と監査の書
    base = previous_tag(root, sha, version)
    touched = danger_commits(root, base, sha)
    if touched:
        audits = audits_at(root, sha)
        broken = [a for a in audits if a.problem]
        uncovered = {c: names for c, names in touched.items() if not any(covers(root, a, c) for a in audits)}
        used = [a for a in audits if any(covers(root, a, c) for c in touched)]
        span = f"{base or '最初'}..{sha[:7]}"
        if uncovered:
            if broken:
                blocks.append(_refuse("release_audit_malformed",
                                      "・".join(f"{a.path}: {a.problem}" for a in broken)))
            names = sorted({n for ns in uncovered.values() for n in ns})
            blocks.append(_refuse("release_audit_missing",
                                  f"{span} で {'・'.join(names)} に触る commit {len(uncovered)} 件"
                                  f"（{'・'.join(sorted(c[:7] for c in uncovered))}）を覆う監査の書が無い"))
        opened = [a for a in used if a.dangers > 0 and not a.waived]
        if opened:
            blocks.append(_refuse("release_audit_dangers_open",
                                  "・".join(f"{a.path}: dangers {a.dangers}" for a in opened)))

    # ③' privacy の施行日（tools/build_site.py の PRIVACY_EFFECTIVE が None のまま deploy されると、公開の
    # privacy の頁が「未公開の更新案」と名乗る。監査 5.14.0 の危ない 1）。見るのは release を出すときだけ。
    privacy = show(root, sha, PRIVACY_PAGE) or ""
    left = [mark for mark in PRIVACY_UNSET_MARKS if mark in privacy]
    if left:
        blocks.append(_refuse("release_privacy_date_unset", f"{PRIVACY_PAGE} に「{'」「'.join(left)}」が残っている"))

    # ④ 互換の柵
    ok, seen = compat(root)
    if not ok:
        blocks.append(_refuse("release_compat_red", seen))
    return blocks


def render(blocks: list[Block]) -> str:
    sys.path.insert(0, str(ROOT))
    from thth import refusal  # noqa: PLC0415 — 目録の 3 つを CLI と同じ口で引く

    out = []
    for block in blocks:
        first = f"release_blocked: {block.code}"
        lines = [first, *refusal.tail(block.code, first)]
        if block.seen:
            lines.append(f"見たもの: {block.seen}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="release の関門（設計 §6・段 D）")
    parser.add_argument("--ref", default="HEAD", help="確かめる commit（既定 HEAD）")
    parser.add_argument("--root", default=str(ROOT), help="git の repo（既定はこの repo）")
    parser.add_argument("--skip-ci", action="store_true", help="CI の結果を見ない（出すときは付けない）")
    args = parser.parse_args(argv)
    blocks = check(Path(args.root), args.ref, skip_ci=args.skip_ci)
    if not blocks:
        return 0
    print(render(blocks), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
