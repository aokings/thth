"""衝突（`conflict`）を見る・確かめる・解く（設計 4.0 S5 §5）。手元の `thth conflict`・遠くの道・MCP の 1 か所。

- `listing`: `conflict` と `no_follow` の配信の一覧（読むだけ・HTTP 無し）。
- `show`: はてなの今の記事を GET 1 回で読み、最後に送った本文（`sent/` の `text`）との差分・題とカテゴリの
  違い・`content_type`（台帳は書かない）。
- `check`: `published` の配信（か 1 つ）を GET して台帳と比べる。本文・題・カテゴリが違えば `conflict`
  （`readback_changed`・`detail`・`via: check`）、404 は `conflict`（`entry_missing`）。同じなら何も書かない。
  `readback_sha`・`edit_uri`・`published_url` の無い `published`（人が `--published` で解いたもの）は埋める
  （`published` → `published` の行・`op: check`・`resolution: readback_filled`）。1 件ごとに 2 秒空ける。
- `resolve`: `--adopt`（はてなの側を正にする・取り込みの控え）・`--gone`（外で消えていた）・
  `--no-follow`（追従をやめる）。**2 段**（`confirm` が無ければ解く内容と digest を見せて何もしない）。

「THTH の側で上書きする」は別の道を作らない: `--adopt` で取り込み、更新を承認し直す（1 段目の差分が
「はてなの今の本文 → 記事」になる＝2 重の承認・§5.1）。どれも媒体に**書き込まない**（GET だけ）。

ロックは `core._account_locks`（repo → principal → account・timer の run と同じ順）。inflight が残る口座では
`check`・`resolve` を断る（`inflight_open`）。主体・口座が止まっていれば GET を打たない（止めの段だけを見る。
数は見ない——読む側なので・§8.1）。
"""
from __future__ import annotations

import contextlib
import difflib
import hashlib
import time

from . import accounts as accounts_mod
from . import approval as approval_mod
from . import deliveries as deliveries_mod
from . import hatena_entry
from . import hatena_publish
from . import inflight as inflight_mod
from . import jst

DIGEST_VERSION = "thth-delivery-resolve-1"
_SEP = "\x1f"
RESOLUTIONS = ("adopt", "gone", "no_follow")
RESOLUTION_WORDS = {"adopt": "adopt_remote", "gone": "remote_deleted", "no_follow": "no_follow"}
CHECK_INTERVAL_SECONDS = 2
CHECK_LIMIT = 5      # 監査 低-3: 1 件ごとに 2 秒・その間口座のロックを持つので小さく
# 断りの符丁（遠くの道の `SAFE_ERRORS` に足す）。
REASONS = frozenset(("delivery_missing", "delivery_unreadable", "inflight_open", "resolution_not_applicable",
                     "entry_missing", "readback_failed", "confirm_mismatch", "resolution_required",
                     "conflict_busy", "principal_stopped", "account_stopped", "adapter_unavailable",
                     "delivery_changed"))
_SLEEP = time.sleep


class ConflictRefused(Exception):
    """断り（静的な符丁・次の一手・rc）。"""

    def __init__(self, code, message, rc=1):
        super().__init__(code)
        self.code, self.message, self.rc = code, message, rc


# ------------------------------------------------------------------ 読む

def _summary_row(record) -> dict:
    c = deliveries_mod.conflict_of(record) or {}
    return {"delivery": record["delivery"], "article": record.get("article"), "state": record["state"],
            "reason": c.get("reason"), "detail": c.get("detail"), "via": c.get("via"),
            "since": _since(record), "published_url": record.get("published_url"), "seq": record["seq"]}


def _since(record) -> str | None:
    """今の状態がいつから続いているか（同じ状態の行の並びの最初の時刻）。"""
    at = None
    for row in reversed(record["history"]):
        if row.get("state") != record["state"]:
            break
        at = row.get("at")
    return at


def listing(account) -> dict:
    records, broken = deliveries_mod.list_for(account)
    return {"account": account,
            "conflicts": [_summary_row(r) for r in records if r["state"] == "conflict"],
            "no_follow": [_summary_row(r) for r in records if r["state"] == "no_follow"],
            "unreadable": broken}


def _load(account, delivery):
    if not deliveries_mod.is_delivery_id(delivery):
        raise ConflictRefused("delivery_missing", "配信 id の形ではありません（dl_ と 12 桁）", 2)
    try:
        return deliveries_mod.load(account, delivery)
    except deliveries_mod.DeliveryError as exc:
        code = "delivery_missing" if str(exc) == "delivery_missing" else "delivery_unreadable"
        raise ConflictRefused(code, f"{account} の台帳に {delivery} がありません" if code == "delivery_missing"
                              else f"配信の台帳が読めません（{exc}）", 2) from None


def _stops(account, cfg) -> None:
    """止めの段だけ（主体 → 口座）。数は見ない（GET だけの読む側）。"""
    from . import guard, principals
    name = cfg.get("principal")
    halted = principals.stopped(name) if isinstance(name, str) else None
    if halted is not None:
        raise ConflictRefused("principal_stopped", f"主体 {name} が止まっています（{halted.get('reason')}）。"
                              "はてなには読みにも行きません")
    halted = guard.stopped(account)
    if halted is not None:
        raise ConflictRefused("account_stopped", f"{account} が止まっています（{halted.get('reason')}）")


def _adapter(account, cfg, adapter_factory):
    from . import principals
    _stops(account, cfg)
    try:
        principal_cfg = principals.check_account(cfg, account)
        return hatena_publish.make_adapter(cfg, principal_cfg, adapter_factory)
    except principals.PrincipalError as exc:
        raise ConflictRefused(exc.code, f"主体の台帳と合いません（{exc.code}）", 2) from None
    except (hatena_publish.AdapterUnavailable, hatena_publish.Unsupported) as exc:
        raise ConflictRefused("adapter_unavailable",
                              f"はてなブログの送信の口が使えません（{getattr(exc, 'code', 'unsupported_medium')}）",
                              2) from None


def _read(adapter, record):
    """`(remote | None（404）)`。読めなければ `readback_failed`（何も書かない）。"""
    try:
        return hatena_publish.read_remote(adapter, record.get("entry_id"), record.get("edit_uri"))
    except hatena_publish.EntryMissing:
        return None
    except hatena_publish.EntryRefused as exc:
        raise ConflictRefused("readback_failed", f"台帳の記事の URL が組んだ URL と合いません（{exc}）") from None
    except Exception:  # noqa: BLE001 — 5xx・時間切れ: 何も書かない（次にもう一度）
        raise ConflictRefused("readback_failed", "はてなから記事を読めませんでした。何も書いていません"
                              "（時間をおいてもう一度）") from None


def _last_sent_text(account, delivery) -> str | None:
    return hatena_entry._previous_text(account, delivery)


def _diff(before, after, a, b) -> list:
    return list(difflib.unified_diff(approval_mod.normalize_hatena_body(before or "").split("\n"),
                                     approval_mod.normalize_hatena_body(after or "").split("\n"), a, b,
                                     lineterm=""))


def show(account, cfg, delivery, *, adapter_factory=None) -> dict:
    """はてなの今の記事と最後に送った本文の差分（GET 1 回・台帳は書かない）。"""
    record = _load(account, delivery).record
    adapter = _adapter(account, cfg, adapter_factory)
    remote = _read(adapter, record)
    out = {"account": account, "delivery": delivery, "state": record["state"],
           "conflict": deliveries_mod.conflict_of(record), "published_url": record.get("published_url"),
           "remote": None, "gone": remote is None}
    if remote is None:
        return out
    sent_text = _last_sent_text(account, delivery)
    out["remote"] = {
        "readback_sha": approval_mod.hatena_body_sha256(remote["content"]),
        "content_type": remote.get("content_type"),
        "title": remote.get("title"), "categories": remote.get("categories"),
        "title_differs": (isinstance(record.get("title"), str) and isinstance(remote.get("title"), str)
                          and hatena_entry.norm_title(record["title"]) != hatena_entry.norm_title(remote["title"])),
        "categories_differs": (isinstance(record.get("categories"), list)
                               and isinstance(remote.get("categories"), list)
                               and hatena_entry.norm_categories(record["categories"])
                               != hatena_entry.norm_categories(remote["categories"])),
        "diff_lines": (_diff(sent_text, remote["content"], "最後に送った本文", "はてなの今の本文")
                       if sent_text is not None else None),
    }
    out["ledger_title"] = record.get("title")
    out["ledger_categories"] = record.get("categories")
    return out


# ------------------------------------------------------------------ 書く（ロックの中）

def _refuse_inflight(account) -> None:
    state_dir = accounts_mod.state_dir_for(account)
    try:
        record = inflight_mod.read(state_dir)
    except (OSError, ValueError):
        record = {"unreadable": True}
    if record is not None:
        raise ConflictRefused("inflight_open", f"{account} に inflight が残っています。先に thth inflight {account} "
                              "で解いてください")


@contextlib.contextmanager
def _locked(account, cfg, wait):
    """repo → principal → account のロック（timer の run と同じ順）。取れなければ `conflict_busy`。"""
    from . import core, lock as lock_mod
    try:
        manager = core._account_locks(account, cfg, accounts_mod.state_dir_for(account), wait=wait)
        manager.__enter__()
    except lock_mod.LockBusy:
        raise ConflictRefused("conflict_busy", f"{account} は実行中です（ロック取得失敗）。--wait <秒> で待てます"
                              ) from None
    try:
        yield
    except BaseException:
        import sys
        if not manager.__exit__(*sys.exc_info()):
            raise
    else:
        manager.__exit__(None, None, None)


def _check_one(account, adapter, loaded, now_iso) -> dict:
    record = loaded.record
    delivery = record["delivery"]
    remote = _read(adapter, record)
    if remote is None:
        deliveries_mod.advance(account, delivery, state="conflict", at=now_iso, base=loaded,
                               fields={"checked_at": now_iso},
                               row={"op": "check", "reason": "entry_missing", "via": "check"})
        return {"delivery": delivery, "result": "entry_missing"}
    sha = approval_mod.hatena_body_sha256(remote["content"])
    missing = [key for key in ("readback_sha", "edit_uri", "published_url") if not record.get(key)]
    if not record.get("readback_sha"):
        # 人が `--published` で解いて読み戻せなかった（S1 監査 4）。比べる先が無いので今の値で埋める。
        fields = {"readback_sha": sha, "checked_at": now_iso}
        for key in ("edit_uri", "published_url"):
            if not record.get(key) and remote.get(key):
                fields[key] = remote[key]
        if isinstance(remote.get("title"), str):
            fields["readback_title"] = remote["title"]
        if isinstance(remote.get("categories"), list):
            fields["readback_categories"] = list(remote["categories"])
        # 監査 低-1: 比べていないことを行にも答えにも残す（黙って正にしない）。
        deliveries_mod.advance(account, delivery, state="published", at=now_iso, base=loaded, fields=fields,
                               row={"op": "check", "resolution": "readback_filled", "readback_sha": sha,
                                    "compared": False})
        return {"delivery": delivery, "result": "readback_filled", "compared": False}
    detail = hatena_publish.remote_differences(record, remote, base_sha=record["readback_sha"])
    if detail:
        deliveries_mod.advance(account, delivery, state="conflict", at=now_iso, base=loaded,
                               fields={"checked_at": now_iso},
                               row={"op": "check", "reason": "readback_changed", "detail": detail, "via": "check",
                                    "readback_sha": sha})
        return {"delivery": delivery, "result": "readback_changed", "detail": detail}
    if missing:
        fields = {key: remote[key] for key in missing if remote.get(key)}
        if fields:
            fields["checked_at"] = now_iso
            deliveries_mod.advance(account, delivery, state="published", at=now_iso, base=loaded, fields=fields,
                                   row={"op": "check", "resolution": "readback_filled"})
            return {"delivery": delivery, "result": "readback_filled"}
    return {"delivery": delivery, "result": "same", "checked_at": now_iso}


def check(account, cfg, delivery=None, *, limit=CHECK_LIMIT, wait=0, adapter_factory=None, sleep=None) -> dict:
    """`published` の配信を GET して台帳と比べる（S5 §5.2）。同じなら台帳は 1 バイトも変えない。"""
    sleep = sleep or _SLEEP
    with _locked(account, cfg, wait):
        _refuse_inflight(account)
        if delivery is not None:
            loaded = _load(account, delivery)
            if loaded.record["state"] != "published":
                raise ConflictRefused("resolution_not_applicable",
                                      f"{delivery} は {loaded.record['state']} です（check は published の配信だけ）")
            targets = [delivery]
        else:
            records, _broken = deliveries_mod.list_for(account)
            targets = [r["delivery"] for r in records if r["state"] == "published"][:max(1, int(limit))]
        adapter = _adapter(account, cfg, adapter_factory)
        results = []
        for index, dl in enumerate(targets):
            if index:
                sleep(CHECK_INTERVAL_SECONDS)
            loaded = _load(account, dl)
            if loaded.record["state"] != "published":
                continue
            try:
                results.append(_check_one(account, adapter, loaded, jst.iso()))
            except ConflictRefused as exc:
                results.append({"delivery": dl, "result": exc.code})
    return {"account": account, "checked": len(results), "results": results}


def digest(account, delivery, seq, resolution, remote_sha=None) -> str:
    """`thth-delivery-resolve-1 | account | delivery | seq | resolution | （adopt なら今の本文の sha）`。"""
    parts = [DIGEST_VERSION, account, delivery, str(seq), resolution, remote_sha or ""]
    return hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()[:approval_mod.APPROVE_DIGEST_LENGTH]


def applicable(record, resolution) -> bool:
    state = record["state"]
    reason = (deliveries_mod.conflict_of(record) or {}).get("reason")
    if resolution == "adopt":
        return state in ("published", "no_follow") or (
            state == "conflict" and reason in ("readback_changed", "syntax_mismatch"))
    if resolution == "gone":
        return state == "conflict" and reason in ("entry_missing", "published_elsewhere")
    if resolution == "no_follow":
        return state in ("published", "conflict")
    return False


def resolve(account, cfg, delivery, resolution, *, by, confirm=None, wait=0, via="cli",
            adapter_factory=None) -> dict:
    """解く（S5 §5.3）。`confirm` が無ければ 1 段目（何も書かない・digest）。2 段目で台帳・控え・変更ログ。"""
    if resolution not in RESOLUTIONS:
        raise ConflictRefused("resolution_required", "--adopt・--gone・--no-follow のどれかを付けてください")
    with _locked(account, cfg, wait):
        _refuse_inflight(account)
        loaded = _load(account, delivery)
        record = loaded.record
        if not applicable(record, resolution):
            reason = (deliveries_mod.conflict_of(record) or {}).get("reason")
            hint = ("外で消えていたなら --gone" if reason == "entry_missing" else
                    "はてなの側を正にするなら --adopt" if reason in ("readback_changed", "syntax_mismatch") else
                    "thth conflict で状態を確かめてください")
            raise ConflictRefused("resolution_not_applicable",
                                  f"{delivery} は {record['state']}（{reason or '—'}）です。この解き方は使えません。{hint}")
        remote, remote_sha = None, None
        if resolution == "adopt":
            adapter = _adapter(account, cfg, adapter_factory)
            remote = _read(adapter, record)
            if remote is None:
                at = jst.iso()
                deliveries_mod.advance(account, delivery, state="conflict", at=at, base=loaded,
                                       fields={"checked_at": at},
                                       row={"op": "check", "reason": "entry_missing", "via": "resolve"})
                raise ConflictRefused("entry_missing", "はてなに記事がありません（404・外で消えた）。台帳を conflict"
                                      f"（entry_missing）にしました。thth conflict {account} {delivery} resolve --gone "
                                      "--by <名前> で解いてください")
            remote_sha = approval_mod.hatena_body_sha256(remote["content"])
        expected = digest(account, delivery, record["seq"], resolution, remote_sha)
        stage = {"account": account, "delivery": delivery, "article": record.get("article"),
                 "state": record["state"], "conflict": deliveries_mod.conflict_of(record),
                 "resolution": resolution, "by": by, "digest": expected, "seq": record["seq"]}
        if remote is not None:
            sent_text = _last_sent_text(account, delivery)
            diff = _diff(sent_text, remote["content"], "最後に送った本文", "はてなの今の本文") if sent_text else []
            stage["remote"] = {"readback_sha": remote_sha, "title": remote.get("title"),
                               "categories": remote.get("categories"), "content_type": remote.get("content_type"),
                               "diff_added": sum(1 for line in diff if line.startswith("+") and not line.startswith("+++")),
                               "diff_removed": sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))}
        if not confirm:
            return dict(stage, resolved=False, stage=1)
        if confirm != expected:
            raise ConflictRefused("confirm_mismatch", f"digest が一致しないので解きません（いまの digest は {expected}）。"
                                  "見せたあとに台帳かはてなの本文が変わりました。もう一度 1 段目から")
        at = jst.iso()
        word = RESOLUTION_WORDS[resolution]
        if resolution == "adopt":
            hatena_entry.keep_adopted_body(account, delivery, remote_sha, remote["content"])
            fields = {"readback_sha": remote_sha, "checked_at": at}
            if isinstance(remote.get("title"), str):
                fields["title"] = fields["readback_title"] = remote["title"]
            if isinstance(remote.get("categories"), list):
                fields["categories"] = list(remote["categories"])
                fields["readback_categories"] = list(remote["categories"])
            if remote.get("edit_uri"):
                fields["edit_uri"] = remote["edit_uri"]
            if remote.get("published_url") and not record.get("published_url"):
                fields["published_url"] = remote["published_url"]
            deliveries_mod.advance(account, delivery, state="published", at=at, base=loaded, fields=fields,
                                   row={"op": "resolve", "resolution": word, "resolved_by": by,
                                        "previous_readback_sha": record.get("readback_sha"),
                                        "readback_sha": remote_sha, "content_type": remote.get("content_type")})
        else:
            deliveries_mod.advance(account, delivery, state="deleted" if resolution == "gone" else "no_follow",
                                   at=at, base=loaded, row={"op": "resolve", "resolution": word, "resolved_by": by})
    from . import admin_log
    try:
        admin_log.append("delivery_resolved", account, cfg, by=by, via=via,
                         diff={"delivery": [None, delivery], "resolution": [None, word]})
        logged = True
    except Exception:  # noqa: BLE001 — 解いたことは取り消さない。書けなかったことだけ言う
        logged = False
    return dict(stage, resolved=True, stage=2, resolution_word=word, admin_log=logged)
