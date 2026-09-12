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
from . import jst as jst_mod
from . import maintain as maintain_mod
from . import queuefile
from . import topics as topics_mod
from . import redact as redact_mod
from . import select as select_mod
from . import writeback as writeback_mod

# 「投稿できる」と言うために確認できていなければならないこと。**ここに並ぶのは
# すべて「確認できた」形**（「〜でない」を数え上げない・規約 12）。
READY_CHECKS = ["ledger", "token", "repo", "queue_dir", "production", "no_inflight"]


LEDGER_SOURCE = "ledger"          # 台帳（保存済みの行）
API_SOURCE = "api_observed"        # API（実行時点で叩いた値）
DRAFT_TOPIC = "draft_front_matter"  # 原稿由来の記録値
API_TOPIC = "api_topic_tag"         # API 観測値（**付与の主体は未確認**）

# 24 時間の刻みとして扱ってよい実経過の幅。**刻みの名前だけで揃えない。**
# `thth run` は 10 分ごとなので、刻みを跨いだ直後に採れば 24.0〜24.2h に入る。
# ここを広く取ると「1 日の値」と「3 日の値」が同じ中央値に混ざる。
AGE_BAND_HOURS = {24: (24.0, 30.0)}


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


def fetch_posts(account_cfg: dict, token: dict | None, *, limit: int = REMOTE_LIMIT,
                 fields: str = "id,permalink,timestamp") -> tuple:
    """Threads 側の直近の投稿をそのまま返す `(rows, エラー文)`。**切り詰めない。**

    `thth doctor` の `detail` は能力の確認が目的で 220 字で切っている——2 件目の
    permalink が読めない、という報告を受けた（nigamilab セッション 2026-09-10）。
    診断の要約を投稿一覧の代わりに使わせていたのが間違いだったので、**投稿を読む
    ための口を別に用意する**（`thth posts`）。読み取りだけ（`threads_basic`）。
    """
    if token is None or not token.get("access_token"):
        return None, "token が無いので引けません"
    user_id = token.get("user_id") or account_cfg.get("user_id")
    if not user_id:
        return None, "user_id が判らないので引けません"

    base_url = os.environ.get("THTH_THREADS_BASE_URL", "https://graph.threads.net")
    params = {"fields": fields, "limit": limit, "access_token": token["access_token"]}
    url = base_url.rstrip("/") + f"/v1.0/{user_id}/threads?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=REMOTE_TIMEOUT_SECONDS) as resp:
            body = json.loads(resp.read() or b"{}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, "引けませんでした: " + redact_mod.redact(str(e))

    rows = body.get("data")
    if not isinstance(rows, list):
        return None, "応答の形が想定と違います"
    return rows, ""


def recent_posts(account_name: str, *, limit: int = REMOTE_LIMIT) -> dict:
    """`thth posts <account>`: 実際の投稿を一覧する（手で出した分も含む）。

    queue の `post_id` と突き合わせて、1 本ごとに **THTH 経由か外で出したか**を
    付ける。手で投稿していた時期からの移行で要る（nigamilab セッション指摘）。
    """
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        return {"account": account_name, "error": str(e), "posts": []}

    tree_sha = writeback_mod.upstream_sha(account_cfg.get("repo_dir"))
    files = core.list_queue_files(account_cfg, tree_sha=tree_sha)
    by_post_id = {}
    for qf in files:
        if qf.malformed:
            continue
        post_id = qf.front_matter.get("post_id")
        if post_id:
            by_post_id[post_id] = os.path.basename(qf.path)

    rows, err = fetch_posts(account_cfg, accounts_mod.load_token(account_cfg),
                             limit=limit, fields="id,permalink,timestamp,text,topic_tag")
    if rows is None:
        return {"account": account_name, "error": err, "posts": []}

    posts = []
    for row in rows:
        post_id = row.get("id")
        posts.append({
            "id": post_id,
            "timestamp": row.get("timestamp"),
            "permalink": row.get("permalink"),
            "text": row.get("text"),
            "topic": row.get("topic_tag"),
            "via_thth": post_id in by_post_id,
            "file": by_post_id.get(post_id),
        })
    return {"account": account_name, "error": None, "posts": posts}


def measured_views_by_account() -> dict:
    """実測（24 時間の刻み）を `{account: {トピック: [views, ...]}}` で返す。

    **アカウントを跨いで合算しない**（設計 §2 の表・§12.3・masaru 指示 2026-09-11）。

    以前は全アカウントの views を 1 つの辞書に混ぜていた。**読者も目的も違う
    アカウントの数字を足すと、比較の母集団が壊れる**——nigamilab の `コーヒー` の
    数字が kopicha の判断材料に混ざっていた。トピックの**観測**は共有できるが、
    **数字は account・投稿の単位で保つ。**
    """
    out: dict = {}
    for name in accounts_mod.list_account_names():
        try:
            account_cfg = accounts_mod.load_account(name)
        except accounts_mod.AccountError:
            continue
        out[name] = _measured_observations_by_topic(account_cfg.get("repo_dir") or "")
    return out


def comparable_views(観測: list, *, source: str = LEDGER_SOURCE,
                      topic_source: str = DRAFT_TOPIC, mark: int = 24) -> tuple:
    """**出所と時間条件が揃った数値だけ**を返す（masaru 裁定 2026-09-12）。

    戻り値は `(使った観測, 使わなかった観測)`。**使わなかったものは捨てない**
    ——`理由` を添えて返す。**「数が少ない」と「揃わなかった」を混ぜない。**

    **名前を分けるだけでは足りない**というのがこの口の理由。別名にしても、
    集計の手前で混ぜられる。**混ぜられない形にする。**
    """
    lo, hi = AGE_BAND_HOURS.get(mark, (None, None))
    使う, 使わない = [], []
    for o in 観測 or []:
        if not isinstance(o, dict):
            使わない.append({"観測": o, "理由": "観測の形ではありません"})
            continue
        age = o.get("age_hours")
        if o.get("source") != source:
            理由 = f"出所が違います（{o.get('source')}）"
        elif o.get("topic_source") != topic_source:
            理由 = f"トピックの由来が違います（{o.get('topic_source')}）"
        elif o.get("mark") != mark:
            理由 = f"刻みが違います（{o.get('mark')}）"
        elif not isinstance(age, (int, float)):
            理由 = "実経過時間が記録されていません"
        elif lo is not None and not (lo <= age <= hi):
            # **刻みの名前で揃えたつもりにならない。**
            理由 = f"実経過 {age}h が {mark}h の帯（{lo}〜{hi}h）の外です"
        elif not isinstance(o.get("views"), int):
            理由 = "views が数ではありません"
        else:
            使う.append(o)
            continue
        使わない.append({**o, "理由": 理由})
    return 使う, 使わない


def topic_plan(account_name: str, *, now=None) -> dict:
    """**これから出す本数が、どのトピックにぶら下がっているか**（masaru 指摘 2026-09-10）。

    > とにかくトピックが threads 新参者にとってはとても重要だと考えています

    フォロワーが 0 でも `中学受験` で 202〜574 views 取れている一方、
    トピック無し・弱いトピックは 1 view。**新参者にとってトピックは唯一の入口**
    なので、「何本がどのトピックに賭かっているか」と「そのトピックを確かめたか」を
    1 枚で出す。読むだけ。

    `checked` は `thth.topics`（下調べの記録）から。`views_median` は
    `data/sns/insights/posts/*.ndjson`（実測）から——**同じ経過時間で比べる**ため、
    24 時間の刻みを満たした行だけを使う。
    """
    now = now if now is not None else jst_mod.now_jst()
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        return {"account": account_name, "error": str(e), "topics": []}

    repo_dir = account_cfg.get("repo_dir") or ""
    files = core.list_queue_files(
        account_cfg, tree_sha=writeback_mod.upstream_sha(repo_dir))

    measured = _measured_observations_by_topic(repo_dir)
    rows: dict = {}
    for qf in files:
        if qf.malformed or qf.front_matter.get("account") != account_name:
            continue
        status = qf.front_matter.get("status")
        if status not in ("draft", "approved", "posted"):
            continue
        topic = queuefile.normalize_topic(qf.front_matter.get("topic")) or "(トピック無し)"
        row = rows.setdefault(topic, {"topic": topic, "draft": 0, "approved": 0, "posted": 0})
        row[status] += 1

    out = []
    for topic, row in rows.items():
        check = topics_mod.latest(topic, account=account_name)
        # **揃ったものだけで数える。揃わなかったものは理由ごと残す。**
        使う, 使わない = comparable_views(measured.get(topic, []))
        seen = sorted(o["views"] for o in 使う)
        row.update({
            "planned": row["draft"] + row["approved"],
            "verdict": check.get("verdict", "unknown"),
            "audience": check.get("audience") or None,
            "checked_at": check.get("checked_at"),
            "checked_by": check.get("by"),
            "views_median_24h": seen[len(seen) // 2] if seen else None,
            "measured_posts": len(seen),
            # **比較に使わなかったもの**（「無かった」と混ぜない）。
            "not_compared": [{"post_id": o.get("post_id"), "理由": o.get("理由")}
                              for o in 使わない],
            "comparison_basis": {"source": LEDGER_SOURCE,
                                  "topic_source": DRAFT_TOPIC,
                                  "mark": 24,
                                  "age_band_hours": AGE_BAND_HOURS[24]},
        })
        out.append(row)
    # **賭かっている本数が多く、かつ確かめていないもの**を先頭に置く。
    out.sort(key=lambda r: (r["verdict"] != "unknown", -r["planned"], r["topic"]))
    return {"account": account_name, "error": None, "topics": out}


# **数値は「出所」と「時間条件」を連れて歩く**（設計 §3.2.2・masaru 裁定
# 2026-09-12）。**`views` だけを配列に積むと、何と比べてよいか分からなくなる。**
#
# `24 in marks` は**「24 時間ちょうどに採った」ではない。** 行には実際の
# `age_hours` があり、`6.16h で 42` のように**刻みの名前と実経過時間はずれる。**
# 出所が同じでも、**実経過時間が違う値を並べて中央値を出すのは比較になっていない。**


def _measured_observations_by_topic(repo_dir: str) -> dict:
    """実測を topic ごとに集める。**数値だけでなく、出所と時間条件も返す。**

    **改名した**（`_measured_views_by_topic` → これ・2026-09-12）。返すものが
    `int` の配列から観測の記録に変わったので、**古い読み手を黙って通さない**
    （規約 5）。

    戻り値は `{topic: [観測, ...]}`。観測は
    `{views, age_hours, collected_at, mark, source, topic_source, post_id}`。
    """
    out: dict = {}
    base = os.path.join(repo_dir, "data", "sns", "insights", "posts")
    if not os.path.isdir(base):
        return out
    for name in sorted(os.listdir(base)):
        if not name.endswith(".ndjson"):
            continue
        best = None
        try:
            with open(os.path.join(base, name), encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    if 24 in (row.get("marks") or []):
                        best = row
        except (OSError, ValueError):
            continue
        if best is None:
            continue
        views = (best.get("metrics") or {}).get("views")
        if not isinstance(views, int):
            continue
        out.setdefault(best.get("topic") or "(トピック無し)", []).append({
            "views": views,
            # **刻みの名前と、実際にいつ採ったかは別。**
            "mark": 24,
            "age_hours": best.get("age_hours"),
            "collected_at": best.get("collected_at"),
            "post_id": best.get("post_id") or name[:-len(".ndjson")],
            "source": LEDGER_SOURCE,
            "topic_source": DRAFT_TOPIC,
        })
    return out


def topic_performance(account_name: str, *, limit: int = REMOTE_LIMIT) -> dict:
    """**トピック別に、実際にどれだけ見られたか**（masaru 指摘 2026-09-10）。

    > いかに合ったトピックに刺さるポストを落とすか、です。

    その 2 つを分けて見るための口。**合っているか**＝トピックごとの中央値、
    **刺さったか**＝同じトピックの中での散らばり（最小〜最大）。

    実測の裏付け（2026-09-10）: asmon はフォロワー 0 で `中学受験` を付けた投稿が
    202〜574 views、nigamilab のトピック無しは 1 view。**届ける経路はフォロワー
    ではなくトピック**。だからトピックは「付けるかどうか」ではなく
    「どれを付けるか」の問題になる。

    読み取りだけ（`threads_basic` ＋ `threads_manage_insights`）。投稿ごとに
    1 回ずつ数を読むので、`limit` 本ぶんの呼び出しが走る（手で叩く口なので可）。
    """
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        return {"account": account_name, "error": str(e), "topics": []}
    token = accounts_mod.load_token(account_cfg)
    rows, err = fetch_posts(account_cfg, token, limit=limit,
                             fields="id,permalink,timestamp,text,topic_tag")
    if rows is None:
        return {"account": account_name, "error": err, "topics": []}

    adapter = core._default_adapter_factory(account_cfg, token)
    by_topic: dict = {}
    for row in rows:
        post_id = row.get("id")
        if not post_id:
            continue
        try:
            metrics = adapter.insights(post_id)
        except Exception as e:
            metrics = {"error": redact_mod.redact(str(e))}
        views = metrics.get("views")
        topic = row.get("topic_tag") or "(トピック無し)"
        by_topic.setdefault(topic, []).append({
            "id": post_id, "timestamp": row.get("timestamp"),
            # **この数は「打った瞬間の値」**（設計 §3.2.2）。台帳の刻みの値では
            # ない。**時点を書かないと、受け取る側が台帳の数字と混ぜる**
            # （2026-09-12 に kopicha セッションが実際に混ぜた）。
            "observed_at": jst.iso(),
            "source": API_SOURCE,
            "topic_source": API_TOPIC,
            "views": views, "likes": metrics.get("likes"),
            "replies": metrics.get("replies"),
            "head": (row.get("text") or "").strip().split("\n", 1)[0][:40],
        })

    topics = []
    for topic, posts in by_topic.items():
        seen = sorted(p["views"] for p in posts if isinstance(p["views"], int))
        topics.append({
            "topic": topic,
            "posts": len(posts),
            "views_median": seen[len(seen) // 2] if seen else None,
            "views_min": seen[0] if seen else None,
            "views_max": seen[-1] if seen else None,
            "likes_total": sum(p["likes"] for p in posts if isinstance(p["likes"], int)),
            "items": sorted(posts, key=lambda p: (p["views"] is None, -(p["views"] or 0))),
        })
    topics.sort(key=lambda t: (t["views_median"] is None, -(t["views_median"] or 0)))
    return {"account": account_name, "error": None, "topics": topics,
            # **この口が返す数の素性**（設計 §3.2.2）。台帳の数と混ぜない。
            "source": API_SOURCE, "topic_source": API_TOPIC,
            "observed_at": jst.iso()}


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
    rows, err = fetch_posts(account_cfg, token)
    if rows is None:
        out["message"] = err
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

    # **予約投稿を使うアカウントかどうか**（台帳 `scheduled`・既定 true）。
    # `masaru-threads` は同席専用（queue も timer も持たない・設計 §3.7）。
    # これを区別しないと、正常な同席専用アカウントを「投稿できません」と
    # 言ってしまう（作った直後に実際に言った）。**使わない仕組みが無いことを
    # 欠陥として数えない。**
    scheduled = account_cfg.get("scheduled", True)

    blockers = []
    if token["state"] in maintain_mod.ATTENTION_STATES:
        blockers.append(f"token: {token['state']}（{token['message']}）")
    if pending is not None:
        blockers.append(f"inflight: {pending.get('file')} が残っています（人が確認するまで止まります）")
    if scheduled:
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

    return {
        "account": account_name,
        "project": account_cfg.get("project"),
        "media": account_cfg.get("media"),
        "handle": account_cfg.get("handle"),
        "production": bool(account_cfg.get("production")),
        "scheduled": bool(scheduled),
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
    head = [
        f"{detail['account']}（{detail['project']} / {detail['media']} / @{detail['handle']}）",
        f"  本番        : {'はい' if detail['production'] else 'いいえ（リハーサル）'}",
    ]
    if not detail.get("scheduled", True):
        # 同席専用。**使わない仕組みの状態を並べない**（無いことが欠陥に見える）。
        lines = head + [
            "  様態        : 同席専用（queue も timer も持たない・設計 §3.7）",
            f"  token       : {token['state']}"
            + (f"（残り {token['remaining_days']:.1f} 日）" if token["remaining_days"] is not None else ""),
            f"  inflight    : {detail['inflight'] or 'なし'}",
        ]
        return _append_remote_and_verdict(lines, detail)
    lines = head + [
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
    return _append_remote_and_verdict(lines, detail)


def _append_remote_and_verdict(lines: list, detail: dict) -> str:
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
    if detail["ready"] and not detail.get("scheduled", True):
        lines.append("  → **同席の送信ができます**（このアカウントは予約投稿を使いません）")
    elif detail["ready"]:
        lines.append("  → **投稿できます**")
    else:
        lines.append("  → **投稿できません**:")
        lines.extend(f"       - {b}" for b in detail["blockers"])
    return "\n".join(lines) + "\n"
