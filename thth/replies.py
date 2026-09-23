"""返信の台帳を読む口（T4・masaru 指摘 2026-09-11）。

`thth/collect.py` は `data/sns/replies/<post_id>.ndjson` に返信を採っている
（`kind: "reply"` の行）。**ところが、それを読むコードがどこにも無かった。**
採っているだけで、誰も読んでいない（`grep -rn "_reply_rows\\|replies_dir" thth/`
で確認済み。書く側の `collect.py` 以外に出てこない）。この module がその読む口。

**読むだけ。何も書かない。**

外部の構想書（Codex §10）の条件——「自己返信・資料請求・具体的な質問を識別
できる場合は分ける。識別できなければ不明のまま残す」——のうち、まずここでは
**自己返信（身内の返信）だけを識別する**。`accounts/*.json` に登録された
**すべての account** の handle を集め、返信の `username` と突き合わせる。

**判らないものを「違う」にしない**（規約 12）。`username` が無い行は
`own: None`——「身内ではない」ではなく「判らない」。
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar

from . import accounts as accounts_mod
from . import postid as postid_mod


_report_allowed_names = ContextVar("thth_reply_report_allowed_names", default=None)


def active_report_scope():
    """Authorized owner names for this report call, or None for normal CLI."""
    return _report_allowed_names.get()


@contextmanager
def report_scope(allowed_names):
    """Keep the HTTP legacy core-call signature without a process-wide scope."""
    token = _report_allowed_names.set(tuple(allowed_names))
    try:
        yield
    finally:
        _report_allowed_names.reset(token)


def _normalize_handle(value) -> str | None:
    """`@` の有無・大小文字の揺れを吸収する（handle は表記が揺れうる）。

    空・None は None を返す（「正規化した結果 空文字」を有効な handle として
    扱わない）。
    """
    if value is None:
        return None
    v = str(value).strip()
    if v.startswith("@"):
        v = v[1:].strip()
    return v.lower() or None


def load_for_ownership(name):
    """「誰の返信か」を見分けるためだけに台帳を読む（handle・媒体・置き場）。

    **退出の途中（停止中）の台帳も読む**（3.1.2 件 7・実測 09-23 12:55）。停止中の
    1 台帳を unreadable に落とすと handle の集合が不完全になり、project 全体の返信の
    `own` が None になって未回答から消えていた。同じ本人の handle は退出の途中でも
    自分。読むだけなので退出の読み手と同じ `recovery` の下で読む。停止の状態そのもの
    が読めない（`account_stop_state_unreadable`）・台帳が壊れているときは従前どおり
    `AccountError` を上げる。
    """
    try:
        return accounts_mod.load_account(name)
    except accounts_mod.AccountStopped as exc:
        if str(exc) != "account_stopped":
            raise
    from . import leave_gate
    try:
        with leave_gate.recovery(name):
            return accounts_mod.load_account(name)
    except ValueError:
        raise accounts_mod.AccountError("account_unreadable") from None


def _own_handles(allowed_names=None) -> tuple:
    """`accounts/*.json` の**すべての account** の handle を正規化して集める。

    1 つの account の台帳が壊れていても、他の account の handle は使いたいので、
    読めたものだけ集める（読めない account があるからといって、この読みの口
    全体を止めない）。**だが、読めなかった account があったこと自体は捨てない**
    ——`(handles, unreadable_account_names)` で返す（外部レビュー再々判定 N6・
    2026-09-12）。以前は読めた分の handle だけを見て、そこに無い username を
    無条件で `own: False`（他者）にしていた。**集合が不完全なだけかもしれない**
    account が読めなかった台帳の中の身内かもしれないのに、それを「他者」と
    確定していた。呼び出し側（`_classify_own`）が「不一致を他者と確定してよいか」
    を判断できるよう、不完全だったという事実を一緒に返す。
    """
    if allowed_names is None:
        allowed_names = active_report_scope()
    handles = set()
    unreadable = []
    for name in (accounts_mod.list_account_names() if allowed_names is None
                 else sorted(set(allowed_names))):
        try:
            cfg = load_for_ownership(name)
        except accounts_mod.AccountError:
            unreadable.append(name)
            continue
        h = _normalize_handle(cfg.get("handle"))
        if h:
            handles.add(h)
    return handles, sorted(unreadable)


def _classify_own(username, own_handles: set, *, incomplete: bool):
    """`username` を身内の handle と突き合わせる。

    - 一致すれば `True`（**account 台帳の一部が読めなくても、一致したものは
      確定できるので身内のまま**——止めすぎない）
    - `username` があって一致しなければ、handle 集合が完全なときだけ `False`。
      **`incomplete`（読めなかった account がある）なら `None`**——非一致を
      「他者」と確定しない。読めなかった台帳の中の身内かもしれない。
    - **`username` が無ければ `None`**（判らない。「違う」と決めつけない）
    """
    norm = _normalize_handle(username)
    if norm is None:
        return None
    if norm in own_handles:
        return True
    return None if incomplete else False


def _read_replies_file(path: str) -> tuple:
    """1 本の `<post_id>.ndjson` を読む。`(返信の行, 取得記録の行, 壊れているか)`。

    **壊れと不存在を混ぜない**（この repo の規約・`thth/topic_store.py` と同じ
    流儀）。ファイルが無ければ「まだ採っていないだけ」——`broken=False` で空を
    返す。JSON として読めない行が 1 行でもあれば、そのファイルは信用できない
    ので `broken=True` にして**中身は 1 行も使わない**。壊れた行の手前までを
    こっそり混ぜると、「壊れて一部しか読めなかった」を「返信が n 件だけだった」
    と取り違える（規約「取れなかった指標は書かない」の裏返し）。
    """
    if not os.path.exists(path):
        return [], [], False
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return [], [], True

    replies, fetches = [], []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            return [], [], True
        # `kind` を持たない古い行は返信として扱う（`collect.py` の `_reply_rows()`
        # と同じ扱い。2026-09-10 以前に書いたもの）。
        if row.get("kind") == "fetch":
            fetches.append(row)
        else:
            replies.append(row)
    return replies, fetches, False


def _fetch_sources(fetches: list) -> dict:
    """取得の記録を**出所ごとに数える**（設計 v2.0.1 §3）。`{出所: 件数}`。"""
    out: dict = {}
    for row in fetches:
        key = row.get("source") or "queue"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def load(account_name: str, *, post_id: str | None = None, allowed_names=None,
         owned_only: bool = False) -> dict:
    """返信の台帳を読む。

    `post_id` を渡せばその投稿だけ、省略すれば `replies_dir` 配下の全 `*.ndjson`。

    `owned_only=True`（`thth replies <account>`・3.1.1）は、`post_id` を省略した
    ときに**この account の投稿の返信だけ**を読む。置き場は同じ repo の他 account と
    共有なので、ファイルがあることは所有の証拠にならない。母集団は採集と同じ
    （`collect.owned_post_ids()`・年齢の窓は掛けない）。この account の投稿と
    確かめられたファイルの行は、`account`・`medium` が無ければ account 側から補い、`counts.other_account_files`（読まなかった
    他 account のファイルの本数）と `population_errors`（母集団を作るときに
    読めなかった記録の数）を足す。既定（False）は従来どおり——他の読み手
    （`unanswered` 等）は所有を自分で判定している。

    戻り値:
      - `replies`: 返信の配列。元の行の項目に `own`（True/False/None）を足したもの
      - `fetches`: 取得の記録の配列（`kind: "fetch"` の行。返信 0 件の成功と
        取得の失敗を区別するために `collect.py` が残しているもの）
      - `broken`: 読めなかったファイルの名前（壊れと不存在は混ぜない）
      - `unreadable_accounts`: handle 突き合わせのために読もうとして**読めなかった
        account の名前**（外部レビュー再々判定 N6・2026-09-12）。非空なら
        handle 集合が不完全——一致しない `username` は `other` ではなく
        `unknown` に入っている（一致したものは `own` のまま）。
      - `counts`: `{"replies": n, "own": n, "other": n, "unknown": n, "fetches": n}`
    """
    if allowed_names is None:
        allowed_names = active_report_scope()
    if allowed_names is not None and account_name not in allowed_names:
        raise accounts_mod.AccountError("scope_unavailable")
    account_cfg = accounts_mod.load_account(account_name)
    # **置き場の解決は 1 か所**（設計 v2.0.1 §1・`accounts.data_dirs()`）。
    # `collect.py` の `collect_once()` が書く先と**同じ helper** から引く
    # ——別々に組み立てると、片方を直したときにもう片方が黙って別の場所を見る。
    base = accounts_mod.data_dirs(account_cfg, account_name)["replies"]

    population_errors: list = []
    excluded = 0
    owned = set()
    if owned_only:
        from . import collect as collect_mod
        owned = {f"{postid_mod.to_filename(pid)}.ndjson"
                 for pid in collect_mod.owned_post_ids(account_name, account_cfg,
                                                        errors=population_errors)}
    if post_id:
        # **`post_id` をそのままパスにしない**（`thth/postid.py`・T3 2026-09-13）。
        # Bluesky の `post_id` は AT URI で `/` を含む。
        names = [f"{postid_mod.to_filename(post_id)}.ndjson"]
    else:
        names = sorted(n for n in os.listdir(base) if n.endswith(".ndjson")) \
            if os.path.isdir(base) else []
        if owned_only:
            # 他 account の分は読まずに数だけ残す（分母の規律）。
            present = names
            names = [n for n in present if n in owned]
            excluded = len(present) - len(names)

    own_handles, unreadable_accounts = _own_handles(allowed_names)
    incomplete = bool(unreadable_accounts)

    all_replies, all_fetches, broken = [], [], []
    for name in names:
        rows, fetch_rows, is_broken = _read_replies_file(os.path.join(base, name))
        if is_broken:
            broken.append(name)
            continue
        for row in rows:
            own = _classify_own(row.get("username"), own_handles, incomplete=incomplete)
            if name in owned:
                # 古い行は `account`・`medium` を持たない。この account の投稿だと母集団で
                # 確かめられたファイルに限って補う——行にあればそのまま（採取時点の記録を
                # 正とする）。`--post` で他 account の投稿を名指ししたときは補わない。
                row = {**row, "account": row.get("account") or account_name,
                       "medium": row.get("medium") or account_cfg.get("media")}
            all_replies.append({**row, "own": own})
        all_fetches.extend(fetch_rows)

    counts = {
        "replies": len(all_replies),
        "own": sum(1 for r in all_replies if r["own"] is True),
        "other": sum(1 for r in all_replies if r["own"] is False),
        "unknown": sum(1 for r in all_replies if r["own"] is None),
        "fetches": len(all_fetches),
        # **どの記録を見て取りに行ったか**（設計 v2.0.1 §3）。`queue` は
        # 書き戻された front-matter、`sent` は `state/<account>/sent/`
        # （同席の様態）。**無印の古い行は `queue`**（`source` を書き始めたのは
        # 2026-09-14）。0 件の出所は数えない——**出さないことで「無い」と言う。**
        "fetch_sources": _fetch_sources(all_fetches),
    }
    result = {"replies": all_replies, "fetches": all_fetches, "broken": broken,
              "unreadable_accounts": unreadable_accounts, "counts": counts}
    if owned_only:
        # 置き場にあったが他 account の投稿のファイル（読んでいない）の本数と、
        # 母集団を作るときに読めなかった記録の数（その投稿の返信は出ていない）。
        counts["other_account_files"] = excluded
        result["population_errors"] = len(population_errors)
    return result
