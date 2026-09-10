"""`thth account <name>`: 1 アカウントの状態を一枚で述べる（masaru 指摘 2026-09-10）。

**なぜ要るか。** 2026-09-10 の本番切り替えのとき、統括は「このアカウントはいま
投稿できる状態か」を答えるために、その場限りのスクリプトを書いて ssh で
つなぎ合わせた——台帳・clone の有無・upstream・sparse-checkout・queue_dir の
有無・トークン・timer・`production`・inflight。**その問いに答える口が無かった。**
masaru に「アカウント別の台帳が欲しそう」と指摘されて作った。

**方針は他と同じ「確認できたものだけを通す」。** 「たぶん大丈夫」を言わない。
判らないものは **判らないと言う**（例: この機械に systemd が無ければ timer の
状態は「判りません」であって「無効」ではない）。最後に **投稿できる状態かどうか
の 1 行**を出す——これがこの口の存在理由。

秘密は扱わない。トークンは残り日数と状態だけ（`thth.maintain` と同じ判定）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from . import accounts as accounts_mod
from . import core
from . import inflight as inflight_mod
from . import jst
from . import maintain as maintain_mod
from . import redact as redact_mod
from . import select as select_mod
from . import writeback as writeback_mod

# 「投稿できる」と言うために確認できていなければならないこと。**ここに並ぶのは
# すべて「確認できた」形**（「〜でない」を数え上げない・規約 12）。
READY_CHECKS = ["ledger", "token", "repo", "queue_dir", "production", "no_inflight"]


def _git(repo_dir: str, args: list):
    return subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True)


def _repo_state(account_cfg: dict) -> dict:
    """clone の状態。**存在・git・upstream・一致・queue_dir を別々に述べる。**"""
    repo_dir = account_cfg.get("repo_dir") or ""
    out = {
        "repo_dir": repo_dir,
        "exists": bool(repo_dir) and os.path.isdir(repo_dir),
        "is_git": False,
        "branch": None,
        "upstream": None,
        "synced": False,       # HEAD == @{u}（＝いま照合先にできる commit がある）
        "head": None,
        "sparse": None,
        "queue_dir": None,
        "queue_dir_exists": False,
    }
    # queue_dir は repo が無くても**どこを見ているか**を述べる（「None がありません」と
    # 言わない。人が直すべき場所を毎回示す）。
    out["queue_dir"] = os.path.join(repo_dir, account_cfg.get("queue_dir") or "")
    out["queue_dir_exists"] = os.path.isdir(out["queue_dir"])
    if not out["exists"]:
        return out

    if _git(repo_dir, ["rev-parse", "--git-dir"]).returncode != 0:
        return out
    out["is_git"] = True

    branch = _git(repo_dir, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch.returncode == 0:
        out["branch"] = branch.stdout.strip()
    upstream = _git(repo_dir, ["rev-parse", "--abbrev-ref", "@{u}"])
    if upstream.returncode == 0:
        out["upstream"] = upstream.stdout.strip()

    sha = writeback_mod.upstream_sha(repo_dir)
    out["synced"] = sha is not None
    out["head"] = sha or (_git(repo_dir, ["rev-parse", "HEAD"]).stdout.strip() or None)

    sparse = _git(repo_dir, ["sparse-checkout", "list"])
    if sparse.returncode == 0 and sparse.stdout.strip():
        out["sparse"] = sparse.stdout.split()
    return out


def _timer_state(account_name: str) -> dict:
    """systemd の timer の状態。**この機械に systemd が無ければ「判らない」。**"""
    unit = f"thth@{account_name}.timer"
    out = {"unit": unit, "known": False, "enabled": None, "active": None}
    if shutil.which("systemctl") is None:
        return out
    enabled = subprocess.run(["systemctl", "is-enabled", unit], capture_output=True, text=True)
    active = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True)
    # is-enabled は無効・不在でも非ゼロを返すが、標準出力に理由が出る。
    if not enabled.stdout.strip() and not active.stdout.strip():
        return out
    out["known"] = True
    out["enabled"] = enabled.stdout.strip() or None
    out["active"] = active.stdout.strip() or None
    return out


REMOTE_TIMEOUT_SECONDS = 8.0
REMOTE_LIMIT = 25


def _remote_posts(account_cfg: dict, token: dict | None, known_post_ids: set) -> dict:
    """Threads 側の**実際の**直近の投稿を引く（masaru 指摘 2026-09-10）。

    > thth通してないものもひいてきたら加わると良いですね

    THTH は自分が出した投稿しか知らない。masaru が携帯の Threads アプリから
    出した投稿・THTH を作る前の投稿は、queue のどこにも無い。そのため
    「最後に投稿したのはいつか」も「同じ本文を最近出していないか」も、
    **THTH の中だけを見ていると嘘になる**。ここで実物を引いて突き合わせる。

    読み取りだけ（`threads_basic`）。投稿・返信・削除は呼ばない。トークンが無い・
    網に届かない場合は **「判りません」** を返す（「0 件」と言わない・規約 12）。
    """
    out = {"known": False, "count": None, "latest": None, "outside": [], "message": ""}
    if token is None or not token.get("access_token"):
        out["message"] = "token が無いので引けません"
        return out
    user_id = token.get("user_id") or account_cfg.get("user_id")
    if not user_id:
        out["message"] = "user_id が判らないので引けません"
        return out

    base_url = os.environ.get("THTH_THREADS_BASE_URL", "https://graph.threads.net")
    params = {"fields": "id,permalink,timestamp", "limit": REMOTE_LIMIT,
              "access_token": token["access_token"]}
    url = base_url.rstrip("/") + f"/v1.0/{user_id}/threads?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=REMOTE_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        out["message"] = "引けませんでした: " + redact_mod.redact(str(e))
        return out

    rows = body.get("data")
    if not isinstance(rows, list):
        out["message"] = "応答の形が想定と違います"
        return out

    out["known"] = True
    out["count"] = len(rows)
    if rows:
        out["latest"] = rows[0].get("timestamp")
    # **THTH の queue に post_id が無いもの＝THTH を通していない投稿。**
    out["outside"] = [
        {"id": row.get("id"), "timestamp": row.get("timestamp"),
         "permalink": row.get("permalink")}
        for row in rows if row.get("id") and row["id"] not in known_post_ids
    ]
    return out


def account_detail(account_name: str, *, now=None, remote: bool = True) -> dict:
    """1 アカウントの全体像。`thth account <name> [--json]` が使う。"""
    now = now if now is not None else jst.now_jst()
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        return {"account": account_name, "error": str(e),
                "ready": False, "blockers": ["ledger: " + str(e)]}

    repo = _repo_state(account_cfg)
    token = maintain_mod.inspect(account_name, now=now)
    state_dir = accounts_mod.state_dir_for(account_name)
    pending = inflight_mod.read(state_dir)

    tree_sha = writeback_mod.upstream_sha(account_cfg.get("repo_dir"))
    files = core.list_queue_files(account_cfg, tree_sha=tree_sha)
    counts = {"draft": 0, "approved": 0, "posted": 0, "型外": 0}
    for qf in files:
        if qf.malformed:
            counts["型外"] += 1
            continue
        if qf.front_matter.get("account") != account_name:
            continue
        status = qf.front_matter.get("status")
        if status in counts:
            counts[status] += 1

    known_post_ids = {qf.front_matter.get("post_id") for qf in files
                       if not qf.malformed and qf.front_matter.get("post_id")}
    remote_state = ({"known": False, "count": None, "latest": None, "outside": [],
                     "message": "--no-remote が指定されました"} if not remote
                    else _remote_posts(account_cfg, accounts_mod.load_token(account_cfg),
                                       known_post_ids))

    last_at = core.last_post_at(files, account_name)
    recent = select_mod.recent_posted_texts(
        files, account_name=account_name, media=account_cfg["media"], now=now)
    result = select_mod.select_one(
        files, account_name=account_name, account_cfg=account_cfg, now=now,
        last_post_at=last_at, recent_texts=recent)

    blockers = []
    if token["state"] in maintain_mod.ATTENTION_STATES:
        blockers.append(f"token: {token['state']}（{token['message']}）")
    if not repo["exists"]:
        blockers.append(f"repo: {repo['repo_dir']} がありません")
    elif not repo["is_git"]:
        blockers.append(f"repo: {repo['repo_dir']} は git repo ではありません")
    elif not repo["synced"]:
        blockers.append("repo: HEAD が upstream と一致していません"
                        "（push していない commit があるか、upstream が無い）")
    if not repo["queue_dir_exists"]:
        blockers.append(f"queue: {repo['queue_dir']} がありません（作って push してください）")
    if not account_cfg.get("production"):
        blockers.append("台帳: production: false（リハーサル。出しません）")
    if pending is not None:
        blockers.append(f"inflight: {pending.get('file')} が残っています（人が確認するまで止まります）")

    return {
        "account": account_name,
        "project": account_cfg.get("project"),
        "media": account_cfg.get("media"),
        "handle": account_cfg.get("handle"),
        "production": bool(account_cfg.get("production")),
        "設定": {
            "publish_at を上書きしうるもの": {
                "min_interval_hours": account_cfg.get("min_interval_hours"),
                "quiet_hours": account_cfg.get("quiet_hours"),
                "max_per_run": account_cfg.get("max_per_run"),
            },
            "tick_minutes": account_cfg.get("tick_minutes"),
            "stale_days": account_cfg.get("stale_days"),
            "hashtags": account_cfg.get("hashtags"),
            "collect_days": account_cfg.get("collect_days"),
        },
        "repo": repo,
        "token": {k: token[k] for k in ("state", "remaining_days", "obtained_at", "message")},
        "timer": _timer_state(account_name),
        "queue": {
            "counts": counts,
            "next_file": os.path.basename(result.chosen.path) if result.chosen else None,
            "needs_review": [os.path.basename(p) for p in result.needs_review],
            "last_post_at": last_at.isoformat() if last_at else None,
        },
        # Threads 側の実物（THTH を通していない投稿を含む）。
        "remote": remote_state,
        "inflight": pending.get("file") if pending else None,
        "ready": not blockers,
        "blockers": blockers,
    }


def render(detail: dict) -> str:
    """人が読む形。**最後の 1 行が結論**（投稿できるか・できないなら何が足りないか）。"""
    if detail.get("error") and "repo" not in detail:
        return f"{detail['account']}: {detail['error']}\n"

    repo, token, timer, queue = detail["repo"], detail["token"], detail["timer"], detail["queue"]
    over = detail["設定"]["publish_at を上書きしうるもの"]
    lines = [
        f"{detail['account']}（{detail['project']} / {detail['media']} / @{detail['handle']}）",
        f"  本番        : {'はい' if detail['production'] else 'いいえ（リハーサル）'}",
        f"  repo        : {repo['repo_dir']}",
        f"                git={repo['is_git']} branch={repo['branch']} upstream={repo['upstream']} "
        f"同期={'済' if repo['synced'] else '未'}",
        f"  queue       : {repo['queue_dir']}（{'あり' if repo['queue_dir_exists'] else 'なし'}）",
        f"                draft={queue['counts']['draft']} approved={queue['counts']['approved']} "
        f"posted={queue['counts']['posted']} 型外={queue['counts']['型外']}",
        f"                次に出るもの={queue['next_file']} 要確認={queue['needs_review'] or 'なし'}",
        f"  token       : {token['state']}"
        + (f"（残り {token['remaining_days']:.1f} 日）" if token["remaining_days"] is not None else ""),
        f"  timer       : {timer['unit']} "
        + (f"enabled={timer['enabled']} active={timer['active']}" if timer["known"]
           else "（この機械では判りません）"),
        f"  指定を上書きしうる設定: min_interval={over['min_interval_hours']}h "
        f"quiet={over['quiet_hours']} max_per_run={over['max_per_run']}",
        f"  inflight    : {detail['inflight'] or 'なし'}",
    ]
    remote = detail.get("remote") or {}
    if remote.get("known"):
        outside = remote["outside"]
        lines.append(f"  Threads 側  : 直近 {remote['count']} 件（最新 {remote['latest']}）")
        if outside:
            lines.append(f"                うち **THTH を通していないもの {len(outside)} 件**"
                         f"（最新 {outside[0]['timestamp']}）")
        else:
            lines.append("                すべて THTH 経由です")
    else:
        lines.append(f"  Threads 側  : 判りません（{remote.get('message', '')}）")
    if detail["ready"]:
        lines.append("  → **投稿できます**")
    else:
        lines.append("  → **投稿できません**:")
        lines.extend(f"       - {b}" for b in detail["blockers"])
    return "\n".join(lines) + "\n"
