"""timer がはてなブログの配信原稿を出す枝（設計 4.0 S1 §6.5）と、その inflight を人が解く口（§8.2）。

**実際の送信（AtomPub の POST／PUT／DELETE と GET）は S2 の adapter**（`thth/adapters/hatenablog.py`）。
差し替え口は `ADAPTER_FACTORY`・`throw_once(adapter_factory=…)`。adapter を作れなければ
（token・consumer が無い・形が違う・`auth: wsse`）`AdapterUnavailable` で何も書かずに止まる
（出ない側に倒す）。adapter の口（fake で試験できる形）:

    adapter.get_entry(*, entry_id, edit_uri) -> str               # 今の本文（読み戻し）
    adapter.create_entry(*, title, body, categories, images) -> EntryResult
    adapter.update_entry(*, entry_id, edit_uri, title, body, categories, images) -> EntryResult
    adapter.delete_entry(*, entry_id, edit_uri) -> EntryResult
    adapter.blog_syntax() -> str | None                           # 任意（S2 §5 の 2）。無ければ確かめない

adapter は AtomPub の URL を主体の `hatena_id` と口座の `blog_id` **だけ**から組み立てる（§5.2 の 7）。
原稿にも依頼にも URL を書く欄は無い。

順（`core._throw_locked` のロックの中＝repo → principal → account・§4.3）:

1. 主体の台帳を読み直し、`blog_id ∈ blogs`・`project ∈ lanes` を確かめる（§5.2 の 6）
2. `guard.check(kind=entry_create|entry_update|entry_delete)` を口座・ブログ・主体の 3 段で
3. update は GET で読み戻し、`base_readback_sha` と違えば台帳を `conflict` にして止める（PUT しない）
4. inflight を書き（`delivery`・`seq`＝結果の行の番号）→ 台帳に `inflight` の行
5. POST／PUT／DELETE を 1 回
6. `sent.write`（`kind`・`blog_id`・`delivery`）→ 台帳に `published`／`deleted` の行 → queue の書き戻し → inflight を消す
7. 応答が無い・読めない → inflight を残して `unknown` の行（出し直さない）

**seq の約束**（§8.2）: inflight に書く `seq` は、結果の行（published・deleted・unknown・…）が取る番号。
台帳の `inflight` の行が `n` なら inflight の `seq` は `n+1`。`unknown` の行を書いたら inflight の
`seq` を 1 進める（常に「台帳の seq ＋ 1」）。人が解くときは**その番号で**書く——だから落ちずに
書けていたときと同じ `event_id` になる。
"""
from __future__ import annotations

import dataclasses
import os
import urllib.parse

from . import approval as approval_mod
from . import deliveries as deliveries_mod
from . import hatena_entry
from . import inflight as inflight_mod
from . import jst

# 差し替え口（`callable(account_cfg, principal_cfg) -> adapter`）。None なら S2 の本物の adapter
# （`thth.adapters.hatenablog.factory`）を使う。試験は `throw_once(adapter_factory=…)` かここで差し替える。
ADAPTER_FACTORY = None

KINDS = {"create": "entry_create", "update": "entry_update", "delete": "entry_delete"}
# 送る前の状態（失敗・人の「出ていない」で戻す先）。
BEFORE = {"create": "wanted", "update": "published", "delete": "published"}
# 時刻が来れば解ける断り（timer の失敗にしない）。
WAITING_REFUSALS = frozenset(("too_soon", "quiet_hours", "daily_limit", "retract_limit",
                              "principal_daily_limit", "principal_retract_limit",
                              "blog_daily_limit", "blog_retract_limit"))


@dataclasses.dataclass
class EntryResult:
    """adapter の 1 回の答え。`ok` が偽で `ambiguous` が真なら「出たか分からない」。"""

    ok: bool
    ambiguous: bool = False
    entry_id: str | None = None
    edit_uri: str | None = None
    published_url: str | None = None
    readback_body: str | None = None
    error: str | None = None
    # 応答の `content` の `type`（S2 §5 の 3）。分からなければ None（確かめない）。
    content_type: str | None = None


class Unsupported(Exception):
    """adapter が差し込まれていない（S1）。factory がこれを上げれば `unsupported_medium`。"""


class AdapterUnavailable(Exception):
    """adapter を作れない（S2 §3.1）: `token_missing`・`token_invalid`・`consumer_missing`・
    `consumer_invalid`・`auth_unsupported`。何も書かずに出ない側へ倒す。"""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class EntryMissing(Exception):
    """`get_entry` が 404（外で消された記事）。`conflict`（`entry_missing`）に写す（S2 §3.5）。"""


class EntryRefused(Exception):
    """`get_entry` を送る前に断った（`edit_uri_mismatch`・`entry_id_invalid`）。出ない側。"""


# S2 §5: 送る直前と 201／200 の応答で確かめるブログの記法（Markdown だけ通す）。
MARKDOWN_TYPE = "text/x-markdown"
# 外で消された記事への PUT／DELETE の 404（adapter の符丁）。
ENTRY_MISSING = "hatena_entry_missing"


def _default_factory():
    """差し込まれていなければ S2 の本物の adapter（`thth/adapters/hatenablog.py`）。"""
    if ADAPTER_FACTORY is not None:
        return ADAPTER_FACTORY
    from .adapters import hatenablog
    return hatenablog.factory


def make_adapter(account_cfg, principal_cfg, adapter_factory=None):
    factory = adapter_factory or _default_factory()
    if factory is None:
        raise Unsupported("unsupported_medium")
    return factory(account_cfg, principal_cfg)


def _result(core, **kwargs):
    return core.ThrowResult(**kwargs)


def _host_matches(url, blog_id) -> bool:
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except (TypeError, ValueError):
        return False
    return bool(host) and host == blog_id


def _sent_post_id(op, entry_id, seq) -> str:
    """`sent/` の鍵。create は entry_id。update・delete は別の鍵（はてなの記録は上書きしない）。"""
    if op == "create":
        return entry_id
    return f"{entry_id}#{'u' if op == 'update' else 'd'}{seq}"


def _record_sent(state_dir, m, op, entry_id, seq, *, url, at, approved_sha, delivery):
    """`sent/` に 1 行（数の材料）。delete は作成の行に `retracted_at` を足す（無ければ削除の行）。"""
    from . import postid as postid_mod
    from . import sent as sent_mod
    if op == "delete":
        row = sent_mod.read(state_dir, entry_id)
        if row is not None and not row.get("retracted_at"):
            sent_mod.mark_retracted(state_dir, entry_id, retracted_at=at, retracted_by="thth",
                                    retract_reason="hatena_entry_delete")
            return entry_id
    post_id = _sent_post_id(op, entry_id, seq)
    if not postid_mod.is_usable(post_id):
        raise ValueError("unusable_post_id")
    if sent_mod.read(state_dir, post_id) is not None:
        return post_id  # 既に記録してある（解き直し）
    sent_mod.write(state_dir, post_id=post_id, text=m["body"],
                   body_hash=approval_mod.compute_body_hash(m["body"]), sent_at=at,
                   approved_fingerprint=approved_sha, kind=hatena_entry_kind(op),
                   blog_id=m["blog_id"], delivery=delivery, url=url)
    return post_id


def hatena_entry_kind(op) -> str:
    return KINDS[op]


def _writeback(account_cfg, path, fields, expected_sha, delivery, account_name):
    """queue の書き戻し（status: posted・post_id・posted_at・url）と commit・push。`(ok, error)`。"""
    from . import queuefile, writeback

    def unchanged() -> bool:
        try:
            fm = queuefile.parse(path).front_matter
        except OSError:
            return False
        return fm.get("approved_sha") == expected_sha and fm.get("delivery") == delivery

    if not unchanged():
        return False, "text_mismatch_before_writeback"
    writeback.set_front_matter_fields(path, fields)
    repo_dir = account_cfg["repo_dir"]
    rel = os.path.relpath(path, repo_dir)
    message = f"thth: {account_name} {os.path.basename(path)} をはてなブログへ（{delivery}）"
    try:
        ok, err = writeback.commit_and_push(repo_dir, rel_path=rel, message=message, validate=unchanged)
    except writeback.PushValidationFailed:
        return False, "text_mismatch_after_rebase"
    return (True, None) if ok else (False, err or "writeback_push_failed")


def throw_chosen(account_name, account_cfg, state_dir, run_id, mode, chosen, now, log,
                 adapter_factory=None):
    """`select_one` が選んだはてなの配信原稿を 1 本出す（dry-run なら見せるだけ）。"""
    from . import core, guard, principals
    path = chosen.path

    def run(action, status, error=None, post_id=None, extra=None):
        core._append_run(state_dir, account_name, run_id, mode, action, path, post_id, now,
                         status=status, error=error, extra=extra)

    # 0. 照合し直す（select と同じ関数・ロックの中）
    reason, m = hatena_entry.select_problem(chosen, account_cfg, account_name)
    if reason is not None:
        log(f"承認が古いので出しません: {os.path.basename(path)}（{reason}）")
        run("post", "error", reason)
        return _result(core, exit_code=2, mode=mode, action="approval_stale", message=reason,
                       file=path, error=reason)
    op, delivery, sha = m["op"], m["delivery"], m["approved_sha"]

    # 1. 主体の台帳を読み直す（承認のあとにブログ・レーンを外せばここで止まる・§5.2 の 6）
    try:
        principal_cfg = principals.check_account(account_cfg, account_name)
    except principals.PrincipalError as exc:
        log(f"主体の台帳と合わないので出しません: {exc}")
        run("post", "error", exc.code)
        return _result(core, exit_code=2, mode=mode, action="refused", message=str(exc),
                       file=path, error=exc.code)

    if mode == "rehearsal":
        log(f"送るはずの配信: {account_name} → {m['blog_id']}（{hatena_entry.OP_LABELS[op]}・{delivery}）")
        log("送るはずの本文:")
        log(m["body"])
        run("skip", "ok")
        return _result(core, exit_code=0, mode=mode, action="skip",
                       message="dry-run: 送るはずの本文をログに出した", file=path)

    # adapter（S2）。無ければ出ない側に倒す（inflight も台帳も書かない）。
    try:
        adapter = make_adapter(account_cfg, principal_cfg,
                               None if adapter_factory is core._default_adapter_factory else adapter_factory)
    except Unsupported:
        log("はてなブログの送信の口（adapter）がまだありません（S2）。出しません")
        run("post", "error", "unsupported_medium")
        return _result(core, exit_code=2, mode=mode, action="unsupported_medium",
                       message="unsupported_medium", file=path, error="unsupported_medium")
    except AdapterUnavailable as exc:
        code = exc.code
        hint = (f"。thth principal auth {principal_cfg['principal']} --by <名前> で入れてください"
                if code in ("token_missing", "token_invalid", "consumer_missing", "consumer_invalid") else "")
        log(f"はてなブログの認証の材料が使えないので出しません（{code}）{hint}")
        run("post", "error", code)
        return _result(core, exit_code=2, mode=mode, action="refused", message=code, file=path, error=code)

    # 2. 安全装置（主体 → 口座 → 媒体の断りの止め・口座／ブログ／主体の数）
    kind = KINDS[op]
    try:
        guard.check(account_name, account_cfg, kind, now=now)
    except guard.GuardRefused as exc:
        code = str(exc)
        log(core.guard_refusal_message(account_name, exc, kind="retract" if op == "delete" else "publish"))
        run("post", "error", code)
        return _result(core, exit_code=0 if code in WAITING_REFUSALS else 2, mode=mode,
                       action="guard_refused", message=code, file=path, error=code)

    loaded = deliveries_mod.load(account_name, delivery)
    ledger = loaded.record

    # 3. update は今の本文を読み戻して、承認した「変更前の版」と比べる（上書きしない）
    if op == "update":
        try:
            current = adapter.get_entry(entry_id=m["entry_id"], edit_uri=ledger.get("edit_uri"))
            current_sha = approval_mod.hatena_body_sha256(current)
        except EntryMissing:
            # 外で消された記事（404）。PUT を打たず、人が見るまで止める（S2 §3.5・§15 の 5）。
            deliveries_mod.advance(account_name, delivery, state="conflict", at=jst.iso(), base=loaded,
                                   fields={"checked_at": jst.iso()},
                                   row={"op": op, "reason": "entry_missing", "approved_sha": sha})
            log("はてなブログの側に記事がありません（404・外で消された）。更新しません")
            run("post", "error", "delivery_conflict")
            return _result(core, exit_code=1, mode=mode, action="conflict",
                           message="entry_missing", file=path, error="delivery_conflict")
        except EntryRefused as exc:
            log(f"台帳の記事の URL が組んだ URL と合わないので出しません（{exc}）")
            run("post", "error", str(exc))
            return _result(core, exit_code=2, mode=mode, action="refused", message=str(exc),
                           file=path, error=str(exc))
        except Exception:  # noqa: BLE001 — 読めなければ比べられない＝出さない
            log("更新の前の読み戻しができないので出しません（次の run でもう一度）")
            run("post", "error", "readback_failed")
            return _result(core, exit_code=1, mode=mode, action="readback_failed",
                           message="readback_failed", file=path, error="readback_failed")
        if current_sha != m["base_readback_sha"]:
            deliveries_mod.advance(account_name, delivery, state="conflict", at=jst.iso(), base=loaded,
                                   fields={"checked_at": jst.iso()},
                                   row={"op": op, "reason": "readback_changed", "approved_sha": sha,
                                        "readback_sha": current_sha})
            log("はてなブログの側で本文が変わっています（承認した変更前の版と違う）。上書きしません")
            run("post", "error", "delivery_conflict")
            return _result(core, exit_code=1, mode=mode, action="conflict",
                           message="readback_changed", file=path, error="delivery_conflict")

    # 3b. ブログの記法（S2 §5 の 2）: 一覧の最新の記事の `content@type` が Markdown でなければ断る。
    #     記事が 0 件なら確かめられない（台帳の宣言を信じる）。削除は本文を送らないので見ない。
    syntax_of = getattr(adapter, "blog_syntax", None)
    if op != "delete" and syntax_of is not None:
        try:
            syntax = syntax_of()
        except Exception:  # noqa: BLE001 — 確かめられなければ出さない（次の run）
            log("ブログの記法を確かめられないので出しません（次の run でもう一度）")
            run("post", "error", "syntax_check_failed")
            return _result(core, exit_code=1, mode=mode, action="syntax_check_failed",
                           message="syntax_check_failed", file=path, error="syntax_check_failed")
        if syntax is not None and syntax != MARKDOWN_TYPE:
            log(f"ブログの最新の記事が Markdown ではありません（{syntax}）。"
                "ブログの編集モードを Markdown にしてから出し直してください。出しません")
            run("post", "error", "blog_syntax_mismatch")
            return _result(core, exit_code=2, mode=mode, action="refused",
                           message="blog_syntax_mismatch", file=path, error="blog_syntax_mismatch")

    # 4. inflight（結果の行の番号を書く）→ 台帳に inflight の行
    outcome_seq = ledger["seq"] + 2
    inflight_mod.write(state_dir, file=path, started=jst.iso(), container_id=None,
                       body_hash=approval_mod.compute_body_hash(m["body"]), approved_fingerprint=sha,
                       delivery=delivery, seq=outcome_seq)
    loaded = deliveries_mod.load(account_name, delivery)
    deliveries_mod.advance(account_name, delivery, state="inflight", at=jst.iso(), base=loaded,
                           seq=outcome_seq - 1,
                           row={"op": op, "approved_sha": sha, "origin": {"via": "timer"}})

    # 5. 1 回だけ送る
    try:
        if op == "create":
            result = adapter.create_entry(title=m["title"], body=m["body"], categories=m["categories"],
                                          images=m["images"])
        elif op == "update":
            result = adapter.update_entry(entry_id=m["entry_id"], edit_uri=ledger.get("edit_uri"),
                                          title=m["title"], body=m["body"], categories=m["categories"],
                                          images=m["images"])
        else:
            result = adapter.delete_entry(entry_id=m["entry_id"], edit_uri=ledger.get("edit_uri"))
    except Exception as exc:  # noqa: BLE001 — 送ったあとの例外は「出たか分からない」
        result = EntryResult(ok=False, ambiguous=True, error=exc.__class__.__name__)

    if not result.ok:
        at = jst.iso()
        if result.ambiguous:
            # 7. 結果不明: inflight を残し、unknown の行。出し直さない。
            deliveries_mod.advance(account_name, delivery, state="unknown", at=at, seq=outcome_seq,
                                   row={"op": op, "error": result.error or "ambiguous"})
            inflight_mod.update(state_dir, seq=outcome_seq + 1)
            log(f"はてなブログへの送信の結果が分からないので inflight を残します: {path}")
            run("post", "error", result.error or "publish_ambiguous")
            return _result(core, exit_code=1, mode=mode, action="inflight",
                           message="publish_ambiguous", file=path, error="publish_ambiguous")
        if result.error == ENTRY_MISSING and op in ("update", "delete"):
            # 外で消された記事（404）: 元の状態（published）に戻すと次の run がまた打つので conflict。
            deliveries_mod.advance(account_name, delivery, state="conflict", at=at, seq=outcome_seq,
                                   row={"op": op, "reason": "entry_missing", "error": result.error})
            inflight_mod.clear(state_dir)
            log("はてなブログの側に記事がありません（404・外で消された）。台帳を conflict にして止めます")
            run("post", "error", "delivery_conflict")
            return _result(core, exit_code=1, mode=mode, action="conflict", message="entry_missing",
                           file=path, error="delivery_conflict")
        # 確かに出ていない（媒体が断った）: 元の状態に戻して inflight を消す。
        deliveries_mod.advance(account_name, delivery, state=BEFORE[op], at=at, seq=outcome_seq,
                               row={"op": op, "error": result.error or "refused"})
        inflight_mod.clear(state_dir)
        log(f"はてなブログが断りました: {result.error}")
        if result.error == "hatena_auth_rejected":
            log(f"認可が通りません。thth principal auth {principal_cfg['principal']} --force --by <名前> "
                "でやり直してください（時計のずれでも 401 になります）")
        run("post", "error", result.error or "publication_failed")
        return _result(core, exit_code=1, mode=mode, action="post", message="公開に失敗しました",
                       file=path, error=result.error or "publication_failed")

    return _record_outcome(account_name, account_cfg, state_dir, m, result, outcome_seq,
                           log=log, run=run, mode=mode, path=path)


def _record_outcome(account_name, account_cfg, state_dir, m, result, outcome_seq, *, log, run, mode, path,
                    resolution=None, resolved_by=None):
    """6. 出た（消えた）ことを記録する: sent → 台帳 → queue の書き戻し → inflight を消す。"""
    from . import core, postid as postid_mod
    op, delivery, sha = m["op"], m["delivery"], m["approved_sha"]
    entry_id = result.entry_id or m.get("entry_id")
    at = jst.iso()
    if not postid_mod.is_usable(entry_id):
        deliveries_mod.advance(account_name, delivery, state="unknown", at=at, seq=outcome_seq,
                               row={"op": op, "error": "unusable_entry_id"})
        inflight_mod.update(state_dir, seq=outcome_seq + 1)
        run("post", "error", "unusable_post_id")
        return _result(core, exit_code=1, mode=mode, action="inflight", message="unusable_post_id",
                       file=path, error="unusable_post_id")
    url = result.published_url
    elsewhere = op != "delete" and url is not None and not _host_matches(url, m["blog_id"])
    # S2 §5 の 3: 出たが、応答の記法が Markdown でない → conflict（syntax_mismatch）。出し直さない。
    wrong_syntax = (not elsewhere and op != "delete" and result.content_type is not None
                    and result.content_type != MARKDOWN_TYPE)
    # S2 §6: 応答の content（読み戻し）が送った本文と違えば印を付ける（conflict にはしない）。
    differs = (op != "delete" and result.readback_body is not None
               and approval_mod.hatena_body_sha256(result.readback_body)
               != approval_mod.hatena_body_sha256(m["body"]))
    reason = "published_elsewhere" if elsewhere else ("syntax_mismatch" if wrong_syntax else None)
    ledger = deliveries_mod.read(account_name, delivery)
    written = ledger["seq"] == outcome_seq   # 前に書けていた（解き直し）
    _record_sent(state_dir, m, op, entry_id, outcome_seq, url=url if url and not elsewhere else None,
                 at=at, approved_sha=sha, delivery=delivery)
    if not written:
        state = "deleted" if op == "delete" else ("conflict" if reason else "published")
        fields = {"entry_id": entry_id, "checked_at": at}
        if m.get("article_rev"):
            fields["checked_rev"] = m["article_rev"]
        if result.edit_uri:
            fields["edit_uri"] = result.edit_uri
        if url:
            fields["published_url"] = url
        if result.readback_body is not None:
            fields["readback_sha"] = approval_mod.hatena_body_sha256(result.readback_body)
        row = {"op": op, "entry_id": entry_id, "published_url": url, "checked_rev": m.get("article_rev"),
               "approved_sha": sha, "reason": reason, "content_type": result.content_type if wrong_syntax else None,
               "readback_differs": True if differs else None,
               "resolution": resolution, "resolved_by": resolved_by}
        deliveries_mod.advance(account_name, delivery, state=state, at=at, seq=outcome_seq,
                               fields=fields, row=row)
    if elsewhere:
        log(f"公開された URL のホストが {m['blog_id']} と違います（{url}）。台帳を conflict にして止めます")
    if wrong_syntax:
        log(f"記事は出ましたが、別の記法（{result.content_type}）で解釈されました。台帳を conflict にして"
            "止めます。はてなの画面で確かめてください（出し直さない・消さない）")
    if differs:
        log("はてなが返した本文が送った本文と違います（読み戻し差あり・readback_differs）")
    fm_fields = {"status": "posted", "post_id": entry_id, "posted_at": at}
    if url:
        fm_fields["url"] = url
    ok, err = _writeback(account_cfg, path, fm_fields, sha, delivery, account_name)
    if not ok:
        inflight_mod.update(state_dir, post_id=entry_id)
        log(f"出ましたが queue に書き戻せません（inflight を残します）: {err}")
        run("post", "error", err, post_id=entry_id)
        return _result(core, exit_code=1, mode=mode, action="inflight", message=err, file=path,
                       post_id=entry_id, error=err)
    inflight_mod.clear(state_dir)
    run("post", "ok", None, post_id=entry_id,
        extra={"delivery": delivery, "inflight_resolution": resolution})
    return _result(core, exit_code=0 if not reason else 1, mode=mode, action="post",
                   message=reason or "投稿しました", file=path,
                   post_id=entry_id, url=url, error=reason)


# --------------------------------------------------------------------------
# 人が inflight を解く（`thth inflight <口座> resolve`・§8.2）。同じ seq で書く。
# --------------------------------------------------------------------------

class ResolveRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _op_of(ledger) -> str:
    for row in reversed(ledger["history"]):
        if row.get("state") == "inflight" and row.get("op") in KINDS:
            return row["op"]
    raise ResolveRefused("delivery_unreadable")


def resolve(account_name, account_cfg, state_dir, record, decision, post_id, *, by, run_id, now):
    """hatena の inflight を解く（ロックの中で呼ぶ）。戻り値は解き方の語。断りは `ResolveRefused`。

    - `seq` は inflight に書いてある番号のまま使う（振り直さない）。台帳の `seq ＋ 1` と違えば
      `seq_mismatch`。前に結果の行まで書けていた（台帳の seq が同じ）なら台帳は書かない。
    - inflight の行を書く前に落ちた（台帳の seq ＋ 2）なら、何も送っていない——「出ていない」
      だけを受ける。
    """
    from . import core, inflight_cli, queuefile
    delivery = record.get("delivery")
    seq = record.get("seq")
    if not deliveries_mod.is_delivery_id(delivery) or type(seq) is not int:
        raise ResolveRefused("inflight_unreadable")
    try:
        loaded = deliveries_mod.load(account_name, delivery)
    except deliveries_mod.DeliveryError:
        raise ResolveRefused("delivery_unreadable") from None
    ledger = loaded.record
    path = record.get("file")
    at = jst.iso()

    def run(action, status, error=None, post_id=None, extra=None):
        core._append_run(state_dir, account_name, run_id, "production", action, path, post_id, now,
                         status=status, error=error, extra=extra)

    never_sent = ledger["seq"] + 2 == seq and ledger["state"] != "inflight"
    if decision == "not_published":
        if never_sent:
            pass  # inflight の行を書く前に止まった＝送っていない。台帳は書かない
        elif ledger["seq"] + 1 == seq and ledger["state"] in ("inflight", "unknown"):
            op = _op_of(ledger)
            deliveries_mod.advance(account_name, delivery, state=BEFORE[op], at=at, base=loaded, seq=seq,
                                   row={"op": op, "resolution": inflight_cli.HUMAN_NOT_PUBLISHED,
                                        "resolved_by": by})
        else:
            raise ResolveRefused("seq_mismatch")
        inflight_mod.archive(state_dir, record, resolved_at=at, resolved_by=by,
                             resolution=inflight_cli.HUMAN_NOT_PUBLISHED)
        inflight_mod.clear(state_dir)
        run("post", "error", "inflight_resolved_not_published",
            extra={"inflight_resolution": inflight_cli.HUMAN_NOT_PUBLISHED, "delivery": delivery})
        return inflight_cli.HUMAN_NOT_PUBLISHED

    # 出ていた（entry_id を人が確かめた）
    if never_sent:
        raise ResolveRefused("seq_mismatch")
    done = ledger["seq"] == seq and ledger["state"] in ("published", "deleted", "conflict")
    if not done and not (ledger["seq"] + 1 == seq and ledger["state"] in ("inflight", "unknown")):
        raise ResolveRefused("seq_mismatch")
    try:
        qf = queuefile.parse(path)
    except (OSError, TypeError):
        raise ResolveRefused("text_mismatch_before_writeback") from None
    fm = qf.front_matter
    if qf.malformed or fm.get("approved_sha") != record.get("approved_fingerprint") or fm.get("delivery") != delivery:
        raise ResolveRefused("text_mismatch_before_writeback")
    import copy
    before = copy.deepcopy(ledger)
    before["state"] = BEFORE.get(hatena_entry.op_of(fm), "wanted")
    try:
        m = hatena_entry.material(fm, account_cfg, account_name, ledger=before)
    except hatena_entry.EntryError:
        raise ResolveRefused("text_mismatch_before_writeback") from None
    if not done and m["approved_sha"] != record.get("approved_fingerprint"):
        # 送ったときの中身と、いまの原稿・記事が違う。書き戻さない（inflight は残る）。
        raise ResolveRefused("text_mismatch_before_writeback")
    if m["op"] != "create" and post_id and post_id != m["entry_id"]:
        raise ResolveRefused("post_id_differs")   # 更新・削除の対象は台帳の entry_id だけ
    result = EntryResult(ok=True, entry_id=post_id if m["op"] == "create" else m["entry_id"],
                         published_url=ledger.get("published_url") if done else None)
    outcome = _record_outcome(account_name, account_cfg, state_dir, m, result, seq,
                              log=lambda _line: None, run=run, mode="production", path=path,
                              resolution=inflight_cli.HUMAN_PUBLISHED, resolved_by=by)
    if outcome.action == "inflight":
        raise ResolveRefused(outcome.error or "writeback_failed")
    inflight_mod.archive(state_dir, record, resolved_at=at, resolved_by=by,
                         resolution=inflight_cli.HUMAN_PUBLISHED, post_id=post_id)
    return inflight_cli.HUMAN_PUBLISHED
