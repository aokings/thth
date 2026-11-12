"""`thth sync`: 台帳の repo を monban の金庫と合わせる（段 1・**仮**・masaru の答えを待たずに推しで作った・朝に覆せる）。

形は monban の設計案 §15.6 のまま:

1. 台帳の点検（`check`）
2. `git pull --no-rebase --no-edit monban <枝>`（合わせる。ぶつかりは THTH の側で人が解く・段 1 は自動で解かない）
3. `git push monban <枝>`。`fetch first`（ほかの端末が先に置いた＝`stale_head`）か `non-fast-forward` なら 2 からやり直す（3 回まで）

**台帳の点検は仮の最小**: 構想の書 §5 の「点検の命令（仮に `thth register check`）」はまだ無い（`thth lint` は投稿の原稿の
点検で、台帳の形を見ない）。なので段 1 は「git の作業木であること・合わせる枝にいること・merge や rebase の途中でないこと・
作業木が clean（commit していない変更と追跡していないファイルが無い）」だけを見る。`thth register check` ができたら、
`check` の中でそれを呼ぶ（朝に覆せる）。

monban の断り（git-remote-monban が stderr に出す `monban: <符丁> — …`）は符丁だけ読んで THTH の符丁に写す。
`tampered` は止める（やり直さない・自動で直さない）。`too_many_pending` は待ちの断り。`box_too_large` は前向きに断る。
"""
from __future__ import annotations

import os
import re
import sys

from . import tools, wire
from .wire import VaultRefused

REMOTE = "monban"
DEFAULT_BRANCH = "main"
ATTEMPTS = 3
# monban の金庫の URL（monban/vault.py の URL と同じ形）。
URL = re.compile(r"monban://([a-z0-9][a-z0-9-]{0,31})/([a-zA-Z0-9][a-zA-Z0-9_.-]{0,63})/([a-z0-9][a-z0-9-]{0,31})/?\Z")
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}\Z")

# monban の符丁 → THTH の符丁（monban の設計案 §15.6 の表と、monban の client の断り）。
MONBAN_TO_SYNC = {
    "tampered": "sync_tampered",
    "too_many_pending": "sync_busy",
    "rate_limited": "sync_busy",
    "box_too_large": "sync_box_too_large",
    "body_too_large": "sync_box_too_large",
    "vault_full": "sync_vault_full",
    "vault_not_joined": "sync_not_joined",
    "vault_key_unreadable": "sync_not_joined",
    "invalid_key": "sync_login_required",
    "not_logged_in": "sync_login_required",
    "login_expired": "sync_login_required",
    "key_expired": "sync_login_required",
    "remote_unavailable": "sync_unavailable",
    "vault_missing": "sync_vault_missing",
    "passphrase_mismatch": "sync_join_failed",
    "secret_required": "sync_join_failed",
    "vault_dependency_missing": "sync_tool_missing",
    "invalid_vault_url": "sync_url_invalid",
}
# やり直す符丁（ほかの端末が先に置いた）。
RETRY_CODES = frozenset(("stale_head", "box_not_next"))


def is_tty() -> bool:
    """`thth sync init` は人の端末でだけ（戻しの文字列が会話や記録に流れない）。試験はここを差し替える。"""
    return sys.stdout.isatty()


def check_url(url) -> str:
    if not isinstance(url, str) or not URL.fullmatch(url):
        raise VaultRefused("sync_url_invalid")
    return url.rstrip("/")


def check_branch(branch) -> str:
    if not isinstance(branch, str) or not BRANCH.fullmatch(branch) or ".." in branch or branch.endswith((".lock", "/")):
        raise VaultRefused("sync_args_invalid", "branch")
    return branch


def _git(repo: str, *args):
    return tools.run([tools.need(tools.GIT), *args], cwd=repo)


def toplevel(repo: str) -> str:
    if not os.path.isdir(repo):
        raise VaultRefused("sync_not_repo")
    rc, out, _ = _git(repo, "rev-parse", "--show-toplevel")
    if rc != 0 or not out.strip():
        raise VaultRefused("sync_not_repo")
    return out.strip()


def check(repo: str, branch: str) -> dict:
    """台帳の点検（段 1 の仮の最小・上の注）。通れば `{"repo", "branch", "head"}`。"""
    top = toplevel(repo)
    for name in ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
        rc, out, _ = _git(top, "rev-parse", "--git-path", name)
        path = out.strip()
        if rc == 0 and path and os.path.exists(path if os.path.isabs(path) else os.path.join(top, path)):
            raise VaultRefused("sync_in_progress", name)
    rc, out, _ = _git(top, "symbolic-ref", "--quiet", "--short", "HEAD")
    if rc != 0 or out.strip() != branch:
        raise VaultRefused("sync_branch_mismatch", branch)
    rc, out, _ = _git(top, "status", "--porcelain=v1", "--untracked-files=normal")
    if rc != 0:
        raise VaultRefused("sync_not_repo")
    changed = [line for line in out.splitlines() if line.strip()]
    if changed:
        raise VaultRefused("sync_register_unclean", f"{len(changed)} files", files=len(changed))
    rc, out, _ = _git(top, "rev-parse", "--verify", "--quiet", "HEAD")
    if rc != 0:
        raise VaultRefused("sync_register_unclean", "no commit")
    return {"repo": top, "branch": branch, "head": out.strip()}


def remote_url(repo: str, remote: str = REMOTE) -> str:
    # 台帳に書いた URL の字面と、git が実際に使う先（insteadOf・pushInsteadOf・pushurl を展開した後）の両方が
    # monban:// の形であること（平文で別の所へ push しない・監査 段 1 の 1）。
    rc, out, _ = _git(repo, "config", "--get", f"remote.{remote}.url")
    if rc != 0 or not out.strip():
        raise VaultRefused("sync_remote_missing", remote)
    url = check_url(out.strip())
    for args, word in ((("remote", "get-url", "--all", remote), "fetch"),
                       (("remote", "get-url", "--push", "--all", remote), "push")):
        rc, out, _ = _git(repo, *args)
        targets = [line.strip() for line in out.splitlines() if line.strip()]
        if rc != 0 or not targets or any(not URL.fullmatch(t) for t in targets):
            raise VaultRefused("sync_remote_redirected", word)
    return url


def refusal_for(text: str, *, step: str) -> VaultRefused:
    """monban と git の出力から断りを 1 つ（文は写さない・符丁と静的な語だけ）。"""
    code = wire.monban_code(text)
    if code is not None:
        mapped = MONBAN_TO_SYNC.get(code)
        if mapped == "sync_tool_missing":
            return VaultRefused(mapped, "cryptography")
        if mapped:
            return VaultRefused(mapped, code)
        return VaultRefused("sync_failed", f"{step} {code}")
    if wire.GIT_NO_HELPER.search(text or ""):
        return VaultRefused("sync_tool_missing", tools.GIT_REMOTE)
    if wire.GIT_CONFLICT.search(text or ""):
        return VaultRefused("sync_conflict")
    return VaultRefused("sync_failed", step)


def _count(repo: str, before: str, after: str) -> int:
    if before == after:
        return 0
    rc, out, _ = _git(repo, "rev-list", "--count", "--no-merges", f"{before}..{after}")
    return int(out.strip()) if rc == 0 and out.strip().isdigit() else 0


def run(repo: str, *, branch: str = DEFAULT_BRANCH, remote: str = REMOTE, attempts: int = ATTEMPTS) -> dict:
    """check → pull → push（fetch first なら pull からやり直し・`attempts` 回まで）。"""
    branch = check_branch(branch)
    tools.need(tools.GIT)
    tools.need(tools.GIT_REMOTE)
    found = check(repo, branch)
    top = found["repo"]
    remote_url(top, remote)  # 実際の先が monban でなければ、pull も push もしない
    pulled = 0
    for attempt in range(1, attempts + 1):
        before = _git(top, "rev-parse", "HEAD")[1].strip()
        rc, out, err = _git(top, "pull", "--no-rebase", "--no-edit", remote, branch)
        text = out + "\n" + err
        if rc != 0:
            code = wire.monban_code(text)
            if code in RETRY_CODES:
                continue
            if not (code is None and wire.GIT_NO_REMOTE_REF.search(text)):
                raise refusal_for(text, step="pull")
            # 金庫がまだ空（1 回目の push の前）: 取り込むものが無い。
        after = _git(top, "rev-parse", "HEAD")[1].strip()
        pulled += _count(top, before, after)
        rc, out, err = _git(top, "push", remote, f"refs/heads/{branch}:refs/heads/{branch}")
        text = out + "\n" + err
        if rc == 0:
            pushed = "Everything up-to-date" not in text
            return {"ok": True, "repo": top, "branch": branch, "remote": remote, "attempts": attempt,
                    "pulled": pulled, "pushed": pushed}
        code = wire.monban_code(text)
        if code in RETRY_CODES or (code is None and (wire.GIT_FETCH_FIRST.search(text) or wire.GIT_NON_FF.search(text))):
            continue
        raise refusal_for(text, step="push")
    raise VaultRefused("sync_retry_exhausted", f"{attempts} attempts", attempts=attempts)


# ------------------------------------------------------------------ init・join・status（monban-vault を呼ぶ薄い口）

def _ensure_remote(repo: str | None, url: str, remote: str = REMOTE) -> str:
    """repo が git の作業木なら `git remote add monban <url>`。既にあれば触らない。結果の語を返す。"""
    if not repo:
        return "skipped"
    try:
        top = toplevel(repo)
    except VaultRefused:
        return "not_repo"
    rc, out, _ = _git(top, "config", "--get", f"remote.{remote}.url")
    if rc == 0:
        return "kept" if out.strip().rstrip("/") == url else "different"
    rc, _, _ = _git(top, "remote", "add", remote, url)
    return "added" if rc == 0 else "failed"


def init(url: str, *, repo: str | None, passphrase_stdin: bool = False) -> dict:
    """1 台目: `monban-vault init`。戻しの文字列は monban-vault が端末へ 1 度だけ出す（THTH は読まない・持たない）。"""
    url = check_url(url)
    exe = tools.need(tools.MONBAN_VAULT)
    if not passphrase_stdin and not is_tty():
        raise VaultRefused("sync_init_needs_terminal")
    argv = [exe, "init", url] + (["--passphrase-stdin"] if passphrase_stdin else [])
    rc, _, _ = tools.run(argv, passthrough=True)
    if rc != 0:
        raise VaultRefused("sync_failed", "init")
    return {"ok": True, "url": url, "remote": _ensure_remote(repo, url), "recovery_shown": not passphrase_stdin}


def join(url: str, *, repo: str | None, stdin: bool = False) -> dict:
    """2 台目から: `monban-vault join`。標準入力は monban-vault へ直に繋ぐ（THTH は戻しの文字列を読まない）。"""
    url = check_url(url)
    exe = tools.need(tools.MONBAN_VAULT)
    argv = [exe, "join", url, "--json"] + (["--stdin"] if stdin else [])
    rc, out, err = tools.run(argv, stdin_passthrough=True)
    got = wire.monban_json(out)
    if rc != 0:
        code = got.get("error") or wire.monban_code(err)
        if code:
            mapped = MONBAN_TO_SYNC.get(code)
            raise VaultRefused(mapped or "sync_join_failed", code)
        raise VaultRefused("sync_join_failed")
    return {"ok": True, "url": url, "remote": _ensure_remote(repo, url)}


def status(repo: str, *, remote: str = REMOTE) -> dict:
    """金庫の頭・番号・大きさ・この端末に鍵があるか（読むだけ）。"""
    top = toplevel(repo)
    url = remote_url(top, remote)
    exe = tools.need(tools.MONBAN_VAULT)
    rc, out, err = tools.run([exe, "status", url, "--json"])
    got = wire.monban_json(out)
    if rc != 0:
        code = got.get("error") or wire.monban_code(err)
        raise refusal_for(f"monban: {code}" if code else "", step="status")
    return {"ok": True, "repo": top, "remote": remote, **{k: v for k, v in got.items() if k != "error"}}
