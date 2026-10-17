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
from . import hatena_convert
from . import hatena_entry
from . import inflight as inflight_mod
from . import jst

# 差し替え口（`callable(account_cfg, principal_cfg) -> adapter`）。None なら S2 の本物の adapter
# （`thth.adapters.hatenablog.factory`）を使う。試験は `throw_once(adapter_factory=…)` かここで差し替える。
ADAPTER_FACTORY = None

KINDS = {"create": "entry_create", "update": "entry_update", "delete": "entry_delete"}
# 送る前の状態の既定（4.0 S5 §5.4 から、戻し先は `deliveries.state_before_inflight`＝台帳の inflight の前の
# 状態。この表は読み手の互換のために残す・戻し先には使わない）。
BEFORE = {"create": "wanted", "update": "published", "delete": "published"}
# 時刻が来れば解ける断り（timer の失敗にしない）。
WAITING_REFUSALS = frozenset(("too_soon", "quiet_hours", "daily_limit", "retract_limit",
                              "principal_daily_limit", "principal_retract_limit",
                              "blog_daily_limit", "blog_retract_limit",
                              # 4.0 S3 §9: 主体の 1 日の画像の上げ（翌日の 0 時に解ける）。
                              "fotolife_daily_limit"))


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
    # 4.0 S5 §5.6: 応答の題とカテゴリ（はてなが返した形）。分からなければ None（比べない）。
    readback_title: str | None = None
    readback_categories: list | None = None


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


def read_remote(adapter, entry_id, edit_uri) -> dict:
    """はてなの今の記事（`read_entry` の欄）。adapter に `read_entry` が無ければ本文だけ（`get_entry`）。

    題・カテゴリが取れなかったときは鍵を置かない（比べない）。例外は `get_entry` と同じ。
    """
    read = getattr(adapter, "read_entry", None)
    if read is None:
        return {"content": adapter.get_entry(entry_id=entry_id, edit_uri=edit_uri)}
    fields = read(entry_id=entry_id, edit_uri=edit_uri)
    if not isinstance(fields, dict):
        raise RuntimeError("readback_invalid")
    out = {"content": fields.get("content") or ""}
    for key in ("title", "categories", "edit_uri", "published_url", "content_type", "entry_id", "edited",
                "published"):
        if fields.get(key) is not None:
            out[key] = fields[key]
    return out


def remote_differences(ledger, remote, *, base_sha) -> list:
    """台帳（と変更前の版）とはてなの今の記事の違い（4.0 S5 §5.6）。`["body", "title", "categories"]` の部分。

    本文は `base_sha`（承認した変更前の版・台帳の `readback_sha`）と比べる。題とカテゴリは台帳に
    記録があり、はてなの答えにもあるときだけ比べる（正規化は指紋と同じ・カテゴリは集合）。比べ先は
    はてなが前に返した形（`readback_title`・`readback_categories`）、無ければ出した値（`title`・`categories`）。
    """
    out = []
    if approval_mod.hatena_body_sha256(remote.get("content") or "") != (base_sha or ""):
        out.append("body")
    title = ledger.get("readback_title") if isinstance(ledger.get("readback_title"), str) else ledger.get("title")
    if isinstance(title, str) and isinstance(remote.get("title"), str):
        if hatena_entry.norm_title(title) != hatena_entry.norm_title(remote["title"]):
            out.append("title")
    cats = (ledger.get("readback_categories") if isinstance(ledger.get("readback_categories"), list)
            else ledger.get("categories"))
    if isinstance(cats, list) and isinstance(remote.get("categories"), list):
        if hatena_entry.norm_categories(cats) != hatena_entry.norm_categories(remote["categories"]):
            out.append("categories")
    return out


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


def sent_body(m) -> str:
    """送った本文（S3 §5.3: 画像の参照を置き換えたあと）。置き換えが無い・組めなければ承認した本文。"""
    body = m.get("body_sent")
    return body if isinstance(body, str) else m["body"]


def _images_fields(m) -> list:
    """配信の台帳の `images[]`（S3 §4.2）。上がった画像は `fotolife_id`・`syntax`・`image_url`・`uploaded_at`
    を写す（正本は主体の画像の台帳・これは記録）。配置の無い画像は path と sha256 だけ。"""
    placements = m.get("placements") or {}
    out = []
    for row in m.get("images") or []:
        item = {"path": row["path"], "source_sha256": row["source_sha256"]}
        place = placements.get(row["path"])
        if isinstance(place, dict):
            item.update({k: v for k, v in place.items() if v is not None})
        out.append(item)
    return out


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
    text = sent_body(m)
    sent_mod.write(state_dir, post_id=post_id, text=text,
                   body_hash=approval_mod.compute_body_hash(text), sent_at=at,
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
                 adapter_factory=None, fotolife_factory=None):
    """`select_one` が選んだはてなの配信原稿を 1 本出す（dry-run なら見せるだけ）。

    `fotolife_factory` は画像の段（3c）の adapter の差し替え口（None なら `hatena_fotolife` の既定）。
    """
    from . import core, guard, hatena_fotolife, principals
    path = chosen.path

    def run(action, status, error=None, post_id=None, extra=None):
        core._append_run(state_dir, account_name, run_id, mode, action, path, post_id, now,
                         status=status, error=error, extra=extra)

    # 0. 照合し直す（select と同じ関数・ロックの中）
    reason, m = hatena_entry.select_problem(chosen, account_cfg, account_name)
    if reason is not None:
        log(hatena_entry.refusal_log_line(path, reason))
        run("post", "error", reason)
        # 承認が古いときだけ approval_stale。S2 の断り（画像・相対リンク・未 commit）や台帳の状態は refused。
        return _result(core, exit_code=2, mode=mode,
                       action="approval_stale" if reason == "approval_stale" else "refused",
                       message=reason, file=path, error=reason)
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
        if op != "delete" and m["images"]:
            # 画像は上げない（POST も GET も打たない・S3 §3.2）。台帳を読んで数だけ言う。
            total, ready = hatena_fotolife.plan(principal_cfg["principal"], m["images"])
            log(f"画像 {total} 件をフォトライフに上げるはず（うち {ready} 件は上がっている）")
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
        # 4.0 S5 §6: burst は同じ配信への操作を窓の中で 1 件と数える（配信 id を渡す）。
        guard.check(account_name, account_cfg, kind, now=now, delivery=delivery)
    except guard.GuardRefused as exc:
        code = str(exc)
        log(core.guard_refusal_message(account_name, exc, kind=kind))
        run("post", "error", code)
        return _result(core, exit_code=0 if code in WAITING_REFUSALS else 2, mode=mode,
                       action="guard_refused", message=code, file=path, error=code)

    loaded = deliveries_mod.load(account_name, delivery)
    ledger = loaded.record

    # 3. update は今の本文を読み戻して、承認した「変更前の版」と比べる（上書きしない）。
    #    4.0 S5 §5.6: 題とカテゴリも台帳の記録と比べる（台帳に無ければ比べない）。指紋は本文だけのまま。
    if op == "update":
        try:
            remote = read_remote(adapter, m["entry_id"], ledger.get("edit_uri"))
            current_sha = approval_mod.hatena_body_sha256(remote["content"])
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
        detail = remote_differences(ledger, remote, base_sha=m["base_readback_sha"])
        if detail:
            deliveries_mod.advance(account_name, delivery, state="conflict", at=jst.iso(), base=loaded,
                                   fields={"checked_at": jst.iso()},
                                   row={"op": op, "reason": "readback_changed", "approved_sha": sha,
                                        "readback_sha": current_sha, "detail": detail})
            what = "・".join(hatena_entry.DETAIL_LABELS.get(d, d) for d in detail)
            log(f"はてなブログの側で{what}が変わっています（承認した変更前の版と違う）。上書きしません。"
                f"thth conflict {account_name} {delivery} show で見て、resolve で解いてください")
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

    # 3c. 画像（S3 §3.2）: 主体のロックの中で、記事の POST の前に 1 枚ずつフォトライフへ上げる。
    #     止まったときは inflight も配信の台帳も書かない（記事はまだ何も送っていない）。
    m["body_sent"] = m["body"]
    m["placements"] = {}
    if op != "delete" and m["images"]:
        try:
            placements, stats = hatena_fotolife.ensure_uploaded(principal_cfg, account_cfg, m,
                                                                factory=fotolife_factory, log=log, now=now)
            m["body_sent"] = hatena_convert.embed_images(m["body"], placements,
                                                         article_source=m["article_source"],
                                                         hatena_id=principal_cfg.get("hatena_id"))
            m["placements"] = placements
        except hatena_fotolife.FotolifeStop as stop:
            log(stop.log_line())
            run("post", "error", stop.code)
            exit_code = stop.exit_code
            action = "refused" if exit_code == 2 else ("guard_refused" if exit_code == 0 else stop.code)
            return _result(core, exit_code=exit_code, mode=mode, action=action,
                           message=stop.code if not stop.next_at else f"{stop.code}（次は {stop.next_at}）",
                           file=path, error=stop.code)
        except hatena_convert.ConvertError as exc:
            # 置き換えが組めない（台帳の syntax の形が違う等）。記事は送らない。
            code = str(exc) if str(exc).startswith("image_") else "fotolife_placement_failed"
            log(f"画像の参照を置き換えられないので出しません（{code}）")
            run("post", "error", code)
            return _result(core, exit_code=2, mode=mode, action="refused", message=code, file=path, error=code)
        except (OSError, ValueError) as exc:
            # 画像の台帳が書けない（置き場が危ない・ディスク）。上げた画像の記録を失うので出さない。
            log(f"画像の台帳が書けないので出しません（{exc.__class__.__name__}）。thth fotolife で確かめてください")
            run("post", "error", "fotolife_ledger_unwritable")
            return _result(core, exit_code=2, mode=mode, action="refused", message="fotolife_ledger_unwritable",
                           file=path, error="fotolife_ledger_unwritable")
        made, reused = stats["uploaded"], stats["reused"]
        log(f"画像 {made + reused} 件をそろえました（上げた {made} 件・使い回し {reused} 件）")

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
        # 本文は画像の参照を置き換えたあと（S3 §5.2）。画像は 3c で上げ終えているので渡さない。
        if op == "create":
            result = adapter.create_entry(title=m["title"], body=m["body_sent"], categories=m["categories"],
                                          images=[])
        elif op == "update":
            result = adapter.update_entry(entry_id=m["entry_id"], edit_uri=ledger.get("edit_uri"),
                                          title=m["title"], body=m["body_sent"], categories=m["categories"],
                                          images=[])
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
        # 4.0 S5 §5.4: 戻し先は台帳の inflight の前の状態（conflict からの削除が断られても published に化けない）。
        deliveries_mod.advance(account_name, delivery, state=deliveries_mod.state_before_inflight(ledger), at=at,
                               seq=outcome_seq,
                               row={"op": op, "error": result.error or "refused", "restored": True})
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


def announcement_note(account_cfg, delivery) -> str | None:
    """記事を更新しても SNS の告知は書き換えない（4.0 S5 §7）。告知があれば案内の 1 行。読むだけ。"""
    from . import deliveries_cli
    try:
        rows, _broken = deliveries_cli.announcements(account_cfg.get("project"), {delivery})
    except Exception:  # noqa: BLE001 — 案内の 1 行。読めなくても更新の記録は止めない
        return None
    rows = [r for r in rows if not r.get("retracted_at")]
    if not rows:
        return None
    told = "・".join(f"{r['account']} {hatena_entry.short_at(r.get('sent_at'))}（{r['post_id']}）" for r in rows)
    return (f"この記事の告知 {len(rows)} 件（{told}）は書き換えません。訂正や追記を出すなら、"
            "その投稿への返信の原稿（reply_to）を書いてください")


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
    # S3 §5.3: 比べるのは送った本文（画像の参照を置き換えたあと）。置き換えを組み立て直せなかった
    # （`resolve` で台帳に無い画像があった＝`body_sent` が None）ときは比べない（印を付けない）。
    compare = m.get("body_sent", m["body"])
    differs = (op != "delete" and result.readback_body is not None and compare is not None
               and approval_mod.hatena_body_sha256(result.readback_body)
               != approval_mod.hatena_body_sha256(compare))
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
        fields.update(hatena_entry.outcome_fields(m))  # checked_commit（設計 4.0 S2 §7）
        if result.edit_uri:
            fields["edit_uri"] = result.edit_uri
        if url:
            fields["published_url"] = url
        if result.readback_body is not None:
            fields["readback_sha"] = approval_mod.hatena_body_sha256(result.readback_body)
        if op != "delete":
            # 4.0 S5 §5.6: はてなが返した題とカテゴリ（外での手直しの比べ先・本文の readback_sha と同じ考え）。
            fields["readback_title"] = result.readback_title if isinstance(result.readback_title, str) else None
            fields["readback_categories"] = (list(result.readback_categories)
                                             if isinstance(result.readback_categories, list) else None)
        if op != "delete" and m.get("images"):
            fields["images"] = _images_fields(m)   # S3 §4.2: 上がった画像の記録（写し）
        elif op == "update":
            fields["images"] = []                  # S5: 画像を外した更新（前の記録を残さない）
        if op != "delete":
            # 4.0 S5 §4.2: 何を出したか（題・カテゴリ）。次の更新の 1 段目と読み戻しの比べ先。
            fields["title"] = m["title"]
            fields["categories"] = list(m.get("categories") or [])
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
    if op == "update" and not written:
        line = announcement_note(account_cfg, delivery)
        if line:
            log(line)
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

def postid_usable(value) -> bool:
    from . import postid as postid_mod
    return postid_mod.is_usable(value)


class ResolveRefused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _op_of(ledger) -> str:
    for row in reversed(ledger["history"]):
        if row.get("state") == "inflight" and row.get("op") in KINDS:
            return row["op"]
    raise ResolveRefused("delivery_unreadable")


# 人が「出ていた」で解いたのに読み戻せなかったときの 1 行（監査 中-2）。
NO_READBACK_NOTE = ("はてなから記事を読み戻せなかったので、台帳に readback_sha・edit_uri が入っていません。"
                    "次の更新は読み戻しが無いので止まります（conflict）。thth deliveries {account} で確かめてください")


def _readback_for_resolve(account_name, account_cfg, m, entry_id, base, adapter_factory):
    """`resolve --published` の create・update で記事を GET 1 回して欄を埋める。読めなければ `base` のまま。

    戻り値は `(EntryResult, 読めたか)`。adapter を作れない・GET が失敗・応答の entry_id が違う、は
    どれも「読めない」（今までどおり書く。呼び手が 1 行を出す）。
    """
    from . import principals
    try:
        principal_cfg = principals.check_account(account_cfg, account_name)
        adapter = make_adapter(account_cfg, principal_cfg, adapter_factory)
        read = getattr(adapter, "read_entry", None)
        if read is None:
            return base, False
        ledger_edit = deliveries_mod.read(account_name, m["delivery"]).get("edit_uri") if m["op"] != "create" else None
        fields = read(entry_id=entry_id, edit_uri=ledger_edit)
    except Exception:  # noqa: BLE001 — 読めなければ今のまま（解くことは止めない）
        return base, False
    if not isinstance(fields, dict) or fields.get("entry_id") != entry_id or not fields.get("edit_uri"):
        return base, False
    return EntryResult(ok=True, entry_id=entry_id, edit_uri=fields["edit_uri"],
                       published_url=fields.get("published_url") or base.published_url,
                       readback_body=fields.get("content"), content_type=fields.get("content_type"),
                       readback_title=fields.get("title"), readback_categories=fields.get("categories")), True


# 人が「出ていた」で解いたのに、主体の画像の台帳から画像の配置を組み立て直せなかったときの 1 行（S3 §7.3）。
NO_PLACEMENT_NOTE = ("画像の台帳に無い画像があるので、送った本文（画像の参照を置き換えたあと）を組み立て直せません。"
                     "読み戻しの比べ（readback_differs）は飛ばし、台帳の images[] には fotolife_id 無しで写します。"
                     "thth fotolife {principal} list で確かめてください")


def _rebuild_body_sent(m, account_cfg, log) -> None:
    """`resolve --published` で送った本文を組み立て直す（**HTTP を打たない**・S3 §7.3）。"""
    from . import hatena_fotolife
    m["placements"] = {}
    if m["op"] == "delete" or not m.get("images"):
        m["body_sent"] = m["body"]
        return
    principal = m.get("principal")
    placements = hatena_fotolife.placements_from_ledger(principal, m["images"])
    body_sent = None
    if placements is not None:
        try:
            from . import principals
            hatena_id = principals.load_principal(principal).get("hatena_id")
        except Exception:  # noqa: BLE001 — 主体が読めなければ user の照合を飛ばす
            hatena_id = None
        try:
            body_sent = hatena_convert.embed_images(m["body"], placements, article_source=m["article_source"],
                                                    hatena_id=hatena_id)
            m["placements"] = placements
        except hatena_convert.ConvertError:
            body_sent = None
    m["body_sent"] = body_sent
    if body_sent is None and log is not None:
        log(NO_PLACEMENT_NOTE.format(principal=principal))


def resolve(account_name, account_cfg, state_dir, record, decision, post_id, *, by, run_id, now,
            adapter_factory=None, log=None):
    """hatena の inflight を解く（ロックの中で呼ぶ）。戻り値は解き方の語。断りは `ResolveRefused`。

    - 「出ていた」で解く create・update は、記事を GET 1 回して `readback_sha`・`edit_uri`・
      `published_url` を台帳に入れる（入れないと次の update が必ず `readback_changed` で止まる）。
      読めなければ今までどおり書き、`log` に 1 行（`NO_READBACK_NOTE`）。

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
            deliveries_mod.advance(account_name, delivery, state=deliveries_mod.state_before_inflight(ledger), at=at,
                                   base=loaded, seq=seq,
                                   row={"op": op, "resolution": inflight_cli.HUMAN_NOT_PUBLISHED,
                                        "resolved_by": by, "restored": True})
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
    before["state"] = deliveries_mod.state_before_inflight(ledger)
    try:
        m = hatena_entry.material(fm, account_cfg, account_name, ledger=before)
    except hatena_entry.EntryError:
        raise ResolveRefused("text_mismatch_before_writeback") from None
    if not done and m["approved_sha"] != record.get("approved_fingerprint"):
        # 送ったときの中身と、いまの原稿・記事が違う。書き戻さない（inflight は残る）。
        raise ResolveRefused("text_mismatch_before_writeback")
    if m["op"] != "create" and post_id and post_id != m["entry_id"]:
        raise ResolveRefused("post_id_differs")   # 更新・削除の対象は台帳の entry_id だけ
    _rebuild_body_sent(m, account_cfg, log)
    result = EntryResult(ok=True, entry_id=post_id if m["op"] == "create" else m["entry_id"],
                         published_url=ledger.get("published_url") if done else None)
    if not done and m["op"] in ("create", "update") and postid_usable(result.entry_id):
        result, read_back = _readback_for_resolve(account_name, account_cfg, m, result.entry_id, result,
                                                  adapter_factory)
        if not read_back and log is not None:
            log(NO_READBACK_NOTE.format(account=account_name))
    outcome = _record_outcome(account_name, account_cfg, state_dir, m, result, seq,
                              log=lambda _line: None, run=run, mode="production", path=path,
                              resolution=inflight_cli.HUMAN_PUBLISHED, resolved_by=by)
    if outcome.action == "inflight":
        raise ResolveRefused(outcome.error or "writeback_failed")
    inflight_mod.archive(state_dir, record, resolved_at=at, resolved_by=by,
                         resolution=inflight_cli.HUMAN_PUBLISHED, post_id=post_id)
    return inflight_cli.HUMAN_PUBLISHED


# --------------------------------------------------------------------------
# 結果不明の照合の候補（4.0 S5 §8.3）。**見せるだけ・自動では解かない**（台帳も inflight も書かない）。
# --------------------------------------------------------------------------

CANDIDATE_BEFORE_MINUTES = 2     # inflight の始まりの何分前から
CANDIDATE_AFTER_MINUTES = 30     # 何分後まで


def candidates(account_name, account_cfg, record, *, adapter_factory=None) -> dict:
    """はてなの inflight（`delivery` あり）に合う記事の候補。GET 1 回（create は一覧・update／delete は記事）。

    戻り値: `{"checked", "error", "op", "candidates": [{entry_id, title, published, edited, body_same}],
    "gone"}`。`body_same` は送った本文（組み立て直し・HTTP を打たない）と同じか（組めなければ None）。
    """
    import copy
    import datetime
    from . import principals, queuefile
    out = {"checked": False, "error": None, "op": None, "candidates": [], "gone": None}
    from . import hatena_conflict
    try:
        # 監査 中-2: 主体・口座が止まっていれば、はてなには読みにも行かない（conflict の口と同じ段）。
        hatena_conflict._stops(account_name, account_cfg)
    except hatena_conflict.ConflictRefused as exc:
        out["error"] = exc.code
        return out
    try:
        ledger = deliveries_mod.read(account_name, record.get("delivery"))
        op = _op_of(ledger)
    except (deliveries_mod.DeliveryError, ResolveRefused):
        out["error"] = "delivery_unreadable"
        return out
    out["op"] = op
    try:
        qf = queuefile.parse(record.get("file"))
        before = copy.deepcopy(ledger)
        before["state"] = deliveries_mod.state_before_inflight(ledger)
        m = hatena_entry.material(qf.front_matter, account_cfg, account_name, ledger=before)
    except (OSError, TypeError, ValueError, hatena_entry.EntryError):
        out["error"] = "manuscript_unreadable"
        return out
    _rebuild_body_sent(m, account_cfg, None)
    sent_sha = approval_mod.hatena_body_sha256(m["body_sent"]) if isinstance(m.get("body_sent"), str) else None

    def same(content):
        if sent_sha is None or not isinstance(content, str):
            return None
        return approval_mod.hatena_body_sha256(content) == sent_sha

    try:
        principal_cfg = principals.check_account(account_cfg, account_name)
        adapter = make_adapter(account_cfg, principal_cfg, adapter_factory)
    except Exception as exc:  # noqa: BLE001 — 照合できなければ今の表示に倒す
        out["error"] = getattr(exc, "code", None) or "adapter_unavailable"
        return out
    try:
        if op == "create":
            started = jst.parse(record.get("started"))
            feed = adapter._list()
            lo = started - datetime.timedelta(minutes=CANDIDATE_BEFORE_MINUTES) if started else None
            hi = started + datetime.timedelta(minutes=CANDIDATE_AFTER_MINUTES) if started else None
            for entry in (feed or {}).get("entries") or []:
                if hatena_entry.norm_title(entry.get("title")) != hatena_entry.norm_title(m["title"]):
                    continue
                at = jst.parse(entry.get("published"))
                if lo is None or at is None or not lo <= at <= hi:
                    continue
                out["candidates"].append({"entry_id": entry.get("entry_id"), "title": entry.get("title"),
                                          "published": entry.get("published"), "edited": entry.get("edited"),
                                          "body_same": same(entry.get("content"))})
        else:
            try:
                fields = read_remote(adapter, m["entry_id"], ledger.get("edit_uri"))
            except EntryMissing:
                out["gone"] = True
            else:
                out["gone"] = False
                if op == "update":
                    out["candidates"].append({"entry_id": m["entry_id"], "title": fields.get("title"),
                                              "published": fields.get("published"),
                                              "edited": fields.get("edited"), "body_same": same(fields.get("content"))})
    except Exception as exc:  # noqa: BLE001 — GET が失敗したら照合できない（今の表示に倒す）
        text = str(exc)
        out["error"] = text if text.startswith(("hatena_", "endpoint_")) else "readback_failed"
        out["candidates"] = []
        return out
    out["checked"] = True
    return out
