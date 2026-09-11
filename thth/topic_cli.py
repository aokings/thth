"""`thth topics suggest / observe / record-decision / decision / profile`
（設計 §6・工程 6＝P3）。

**既存の `thth topics <account> --advise` 等を壊さない。** 新方式を同じ argparse
へ無理に足すと、`--note` のようなフラグと部分文字列一致で衝突する。`topics` の
直後の語だけを見て**入口で分ける**（設計 §6「CLI 互換性」・受け入れ T15）。

ここも**判断はしない**。`topic_advice` を呼んで、JSON を stdout に出すだけ。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import accounts as accounts_mod
from . import topic_advice as advice
from . import topic_models as models
from . import topic_store as store
from . import topics as topics_mod

SUBCOMMANDS = ("suggest", "observe", "record-decision", "decision",
                "profile", "observation")

# 本文を含めて 1 MiB（設計 §7）。**超過は構造化エラー**にする。
MAX_INPUT_BYTES = 1024 * 1024


class InputError(Exception):
    """入力が読めない・大きすぎる・形が違う（exit 2）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def account_collision() -> str | None:
    """新しい語と同じ名前の account があれば、その名前を返す。

    **黙って既存の account を隠さない**（設計 §6）。`suggest` という account が
    あるなら、`thth topics suggest` はもう曖昧なので導入エラーにする。
    """
    try:
        names = set(accounts_mod.list_account_names())
    except Exception:
        return None
    hit = sorted(names & set(SUBCOMMANDS))
    return hit[0] if hit else None


def is_new_style(argv: list) -> bool:
    """`topics` の直後が新しい語か。**それ以外は一切触らない**（T15）。"""
    try:
        i = argv.index("topics")
    except ValueError:
        return False
    return i + 1 < len(argv) and argv[i + 1] in SUBCOMMANDS


# --- 入力 -------------------------------------------------------------------

def read_json(path: str | None, *, stdin: bool, what: str):
    """ファイルか stdin から JSON を読む。**同じ検査関数へ合流させる**（設計 §7）。

    外部モデルが指定した任意のサーバファイルへ書いて受け渡す形は取らない。
    MCP からは stdin で渡る。
    """
    if stdin:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        origin = "標準入力"
    elif path:
        try:
            with open(path, "rb") as f:
                raw = f.read(MAX_INPUT_BYTES + 1)
        except OSError as e:
            raise InputError("unreadable_input", f"{what} を読めません: {e}")
        origin = path
    else:
        return None

    if len(raw) > MAX_INPUT_BYTES:
        raise InputError("input_too_large",
                          f"{what}（{origin}）が {MAX_INPUT_BYTES} バイトを超えています")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise InputError("invalid_json", f"{what}（{origin}）を JSON として読めません: {e}")


def _actor(args) -> str:
    by = getattr(args, "by", None) or os.environ.get("THTH_ACTOR")
    if not by:
        raise InputError("missing_actor", "--by を付けてください（誰が記録したかを残します）")
    return by


def _action(kind: str, topic, reason: str, schema: str) -> dict:
    return {"type": kind, "topic": topic, "reason": reason,
            "expected_schema": schema}


def _fail(code: str, message: str) -> int:
    # **形が違うと言うときは、正しい形も返す**（asmon 関東セッション報告
    # 2026-09-11）。項目の名指しはしていたが、値域は `topic_models.py` を
    # 読むまで分からなかった。**断るときこそ、次にできることを渡す。**
    schema = {}
    for name, shape in (("ArticleEvidence", advice.ARTICLE_SHAPE),
                         ("TopicProposal", advice.PROPOSAL_SHAPE)):
        if name in message:
            schema.update(shape)
    if not schema and "記事" in message:
        schema.update(advice.ARTICLE_SHAPE)
    if not schema and ("候補比較" in message or "candidates" in message):
        schema.update(advice.PROPOSAL_SHAPE)
    _emit({"schema_version": models.SCHEMA_VERSION, "ok": False, "status": None,
           "context_id": None, "account": None, "selected_topic": None,
           "candidates": [], "required_actions": [], "warnings": [],
           "shortfalls": [], "expected_schema": schema, "notice": advice.NOTICE,
           "error": {"code": code, "message": message}})
    return 2


def _emit(obj) -> None:
    """**stdout は JSON だけ。ログは stderr。**（設計 §6）"""
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")


def _exit_code(envelope: dict) -> int:
    """exit 0: 有効な応答。exit 1: stale_context・不正な proposal（設計 §6）。"""
    return 0 if envelope.get("ok") else 1


# --- suggest ----------------------------------------------------------------

def cmd_suggest(args) -> int:
    """記事も proposal も無ければ**必要な資料と schema を返す**。

    article だけなら文脈を返して `needs_proposal`。proposal 付きなら検査した
    結果を返す。**THTH 自身が LLM を呼んだかのように振る舞わない。**
    """
    if getattr(args, "input_json_stdin", False):
        # 標準入力は 1 本しかないので、記事と候補比較は 1 つの封筒で受ける。
        # **読んだあとは、ファイルから読んだ場合と同じ検査関数へ合流する。**
        envelope_in = read_json(None, stdin=True, what="入力") or {}
        if not isinstance(envelope_in, dict):
            raise InputError("invalid_json", "入力は object にしてください")
        if envelope_in and "article" not in envelope_in \
                and "proposal" not in envelope_in:
            # **記事をそのまま流した場合**（asmon 関東セッション報告
            # 2026-09-11）。`--input-json-stdin` は封筒を期待するので、
            # 記事だけを渡すと黙って「記事なし」になっていた。
            hint = ("--input-json-stdin は "
                     '{"article": …, "proposal": …} の形です。'
                     "記事だけを渡すなら --article-json-stdin を使ってください")
            if "content_text" in envelope_in or "requested_url" in envelope_in:
                raise InputError("wrong_input_shape",
                                  f"記事証拠がそのまま渡されました。{hint}")
            raise InputError("wrong_input_shape", hint)
        article_raw = envelope_in.get("article")
        proposal_raw = envelope_in.get("proposal")
    else:
        article_raw = read_json(args.article, stdin=args.article_json_stdin,
                                 what="記事証拠")
        proposal_raw = read_json(args.proposal, stdin=args.proposal_json_stdin,
                                  what="候補比較")

    article = models.build_article(article_raw) if article_raw else None

    # **観測は記事の有無に関係なく読む。** 以前は記事が無いと空にしていたので、
    # 「まず何が分かっているか」を聞く最初の呼び出しで**手持ちの根拠が
    # 返らなかった**（独立レビュー 2026-09-11・指摘 2）。
    observations, broken = _all_observations()

    context = advice.build_context(
        args.file, article=article, article_url=args.article_url,
        observation_ids=sorted(observations), profile=read_json(
            args.profile, stdin=False, what="検討用 profile"))

    proposal = None
    if proposal_raw is not None:
        if article is None:
            raise InputError("missing_article",
                              "候補比較を検査するには記事証拠が要ります")
        try:
            proposal = models.validate_proposal(
                proposal_raw, article=article,
                known_observation_ids=set(observations))
        except models.SchemaError as e:
            out = advice.rejected(context["context_id"], context["account"], str(e))
            _emit(out)
            return 1

    envelope = advice.evaluate(context, article=article, proposal=proposal,
                                observations=observations)
    if broken:
        envelope["warnings"].append(
            f"読めない観測の記録が {len(broken)} 件あります"
            f"（無いのではなく壊れています）: {broken[:3]}")

    profile_snapshot = context.pop("profile_snapshot", None)
    envelope["context"] = dict(context)
    envelope["urls_in_post"] = context.get("urls_in_post", [])
    envelope["main_article_url"] = context.get("main_article_url")
    envelope["ignored_urls"] = context.get("ignored_urls", [])
    envelope["evidence"] = _evidence(context, observations, profile_snapshot)
    if article is not None:
        envelope["article_id"] = article["article_id"]
    if proposal is not None:
        envelope["proposal_id"] = proposal["proposal_id"]
    # **検査した結果を、そのまま保存へ渡せる形で返す**（指摘 6）。
    # 利用側が過去の入力を拾って封筒を組み直す必要があると、
    # **組み直し方が出力のどこにも書いていない**ので詰まる。
    envelope["record_payload"] = {
        "draft_path": args.file,
        "context_id": context["context_id"],
        "article_url": context.get("main_article_url"),
        "article": _article_input(article),
        "proposal": _proposal_input(proposal),
    }
    _emit(envelope)
    return _exit_code(envelope)


# 出力に載せる根拠の総量の目安。超えたら投稿例を削って、削ったと言う。
EVIDENCE_BUDGET_BYTES = 256 * 1024
SAMPLES_PER_OBSERVATION = 8


def _all_observations() -> tuple:
    """保存済みの観測と、**既存 22 語から作った参考観測**をまとめて返す。

    以前は保存済みのものだけだった。**そのため `--note` で入れた記録は
    `observation_refs` に書けず、「観測が足りない」と言われても満たす手段が
    存在しなかった**（asmon 関東セッション報告 2026-09-11）。

    参考観測は投稿例を持たないので、**これだけでは `recommended` にならない**
    ——「投稿例を控えてください」という**満たせる**不足になる。
    """
    rows, broken = store.load_all("observations")
    out = {r["observation_id"]: r for r in rows}
    for row in store.legacy_observations():
        out.setdefault(row["observation_id"], row)
    return out, broken


def _article_input(article):
    """`build_article()` が付けた項目を外して、入力の形に戻す。"""
    if article is None:
        return None
    return {k: v for k, v in article.items()
            if k not in ("article_id", "content_sha256", "schema_version")}


def _proposal_input(proposal):
    if proposal is None:
        return None
    return {k: v for k, v in proposal.items()
            if k not in ("proposal_id", "schema_version")}


def _legacy_notes(account: str) -> list:
    """既存 22 語を返す。**観測・自分の判断・他所の判断を別々の物として返す**
    （設計 §9・独立レビュー第 2 巡 P1-2）。

    以前は「最新の 1 行」から `fit` も `note` も取っていた。**その 1 行が
    account を持たない記録だと、自分の account が `unsuitable` と判断した事実が
    出力から消えて、他所の `suitable` が自分の判断のように渡っていた。**
    `judgment()` は取っていたのに `bool()` しか使っていなかった——
    **正しい部品を用意しておいて、使っていなかった。**

    - `observation` … 誰がいたか。**共有できる事実**（最新の記録から）。
    - `own_judgment` … **この account 自身の判断だけ。** 無ければ `None`。
    - `other_judgments` … 他 account の判断。**参考。採らない。**
    - `legacy_judgment` … account を持たない当時の判断。**常に参考。**
    """
    by_topic = {}
    for row in store.legacy_observations():
        by_topic[row["topic"]] = row["observation_id"]   # 後の行が勝つ（時系列）

    out = []
    for topic, row in sorted(topics_mod.observation().items()):
        own = topics_mod.judgment(topic, account) if account else {}
        legacy = topics_mod.legacy_note(topic)
        others = topics_mod.other_accounts(topic, account=account)
        out.append({
            "topic": topic,
            # **`observation_refs` に書ける ID。** これが無いと、不足を
            # 満たす手段が存在しない（asmon 関東セッション報告 2026-09-11）。
            "observation_id": by_topic.get(topic),
            "observation": {
                "kind": row.get("kind"),
                "audience": row.get("audience"),
                "status": row.get("status"),
                "checked_at": row.get("checked_at"),
                "recorded_by": row.get("by"),
                "account": row.get("account"),
            },
            "own_judgment": _judgment_view(own) if own else None,
            "legacy_judgment": _judgment_view(legacy) if legacy else None,
            "other_judgments": [_judgment_view(r) for r in others],
            "notice": ("own_judgment だけがこの account の判断です。"
                        "legacy_judgment と other_judgments は参考で、"
                        "**自分の判断として採用しないでください。**"),
        })
    return out


def _judgment_view(row: dict) -> dict:
    """当時の判断を、新しい語彙を添えて返す。**元の語も残す。**

    `dead`（人がいない）と `mismatch`（意味が違う）はどちらも `unsuitable` に
    なるが**理由は別物**なので、訳した語だけにしない。
    """
    return {
        "fit": advice.legacy_fit(row.get("verdict")),
        "legacy_verdict": row.get("verdict"),
        "reason": row.get("note"),
        "audience": row.get("audience"),
        "account": row.get("account"),
        "recorded_by": row.get("by"),
        "checked_at": row.get("checked_at"),
    }


def _evidence(context: dict, observations: dict, profile) -> dict:
    """**LLM が読める形で根拠そのものを返す**（独立レビュー 2026-09-11・指摘 2）。

    以前は `observation_ids` と `profile_version` しか返していなかった。
    **ID だけでは、別のセッションは何も読めない。** state を直接読ませたり、
    前のセッションの記憶に頼らせたりすると、「THTH が根拠を引き継ぐ」という
    この道具の中心が成り立たない。

    大きくなりすぎる場合は投稿例を削るが、**削ったことを必ず書く**
    （黙って減らすと「その投稿例は無い」と読まれる）。
    """
    rows, truncated = [], []
    used = 0
    for oid in sorted(observations):
        obs = dict(observations[oid])
        samples = obs.get("samples") or []
        if len(samples) > SAMPLES_PER_OBSERVATION:
            obs["samples"] = samples[:SAMPLES_PER_OBSERVATION]
            obs["samples_truncated"] = {
                "shown": SAMPLES_PER_OBSERVATION, "total": len(samples),
                "read_all": f"thth topics observation {oid}"}
            truncated.append(oid)
        size = len(models.canonical_json(obs))
        if used + size > EVIDENCE_BUDGET_BYTES:
            truncated.append(oid)
            rows.append({"observation_id": oid, "topic": obs.get("topic"),
                          "omitted": "大きすぎるので本文を省きました",
                          "read_all": f"thth topics observation {oid}"})
            continue
        used += size
        rows.append(obs)

    return {
        "post_text": context.get("section"),
        # **実際に使った profile の本文**。`--profile` で差し替えた場合も同じ
        # ——差し替えた事実は `context.profile_overridden` に出る。
        "profile": profile,
        "profile_overridden": bool(context.get("profile_overridden")),
        "observations": rows,
        "legacy_notes": _legacy_notes(context["account"]),
        "truncated_observation_ids": sorted(set(truncated)),
        "how_to_read_more": "thth topics observation <observation_id>",
        "notice": advice.NOTICE,
    }


# --- observe / record-decision / decision / profile --------------------------

def cmd_observe(args) -> int:
    """観測を残す。**判定は混ぜない**（設計 §4.2）。"""
    row = read_json(args.input, stdin=args.json_stdin, what="観測")
    if row is None:
        raise InputError("missing_input", "--input か --json-stdin で観測を渡してください")
    row = dict(row)
    row["submitted_by"] = _actor(args)
    row["schema_version"] = models.SCHEMA_VERSION
    # **ID は中身から決める。** 送り手が名乗った ID は使わない（再送しても増えない）。
    row["observation_id"] = models.content_id(row, exclude=("observation_id",))
    saved, wrote = store.put("observations", row, id_key="observation_id")
    _emit({"ok": True, "observation_id": saved["observation_id"],
           "stored": wrote, "notice": advice.NOTICE})
    return 0


def cmd_record_decision(args) -> int:
    """判断を残す。**入力 JSON が「検証済み」と自称しても信用しない**（設計 §6）。

    参照はサーバ側で解決しなおし、記事 hash と `context_id` を再計算して、
    **原稿の現在のバイト列と照合する**。合わなければ `stale_context` で登録しない。

    `suggest` の出力をそのまま渡してよい（独立レビュー 2026-09-11・指摘 6）。
    以前は `record_payload` が無く、**利用側が封筒を組み直さないと保存できず、
    組み直し方は出力のどこにも書いていなかった。**
    """
    row = read_json(args.input, stdin=args.json_stdin, what="判断")
    if row is None:
        raise InputError("missing_input", "--input か --json-stdin で判断を渡してください")
    if isinstance(row, dict) and isinstance(row.get("record_payload"), dict):
        row = row["record_payload"]
    for key in ("draft_path", "article", "proposal"):
        if row.get(key) is None:
            raise InputError(
                "missing_input",
                f"保存に必要な {key} がありません。"
                f"`thth topics suggest … --proposal …` の出力をそのまま渡すか、"
                f"その record_payload を渡してください")
    by = _actor(args)

    article = models.build_article(row["article"])
    observations, broken = _all_observations()
    context = advice.build_context(row["draft_path"], article=article,
                                    article_url=row.get("article_url"),
                                    observation_ids=sorted(observations))
    if context["context_id"] != row.get("context_id"):
        _emit({"schema_version": models.SCHEMA_VERSION, "ok": False,
               "status": "stale_context", "context_id": context["context_id"],
               "account": context["account"], "selected_topic": None,
               "candidates": [], "required_actions": [], "warnings": [],
               "shortfalls": [], "notice": advice.NOTICE,
               "error": {"code": "stale_context",
                          "message": "原稿・記事・観測・profile・主対象の記事の"
                                      "どれかが読み取り時から変わっています。"
                                      "`thth topics suggest` を叩き直して "
                                      "context_id を取り直し、候補比較の "
                                      "context_id を差し替えてから保存して"
                                      "ください"}})
        return 1

    # **根拠が壊れていたら保存しない**（指摘 5）。参照している観測だけを見る。
    referenced = {r for c in row["proposal"].get("candidates") or []
                  for r in (c.get("observation_refs") or [])}
    damaged = sorted(referenced & set(broken))
    if damaged:
        _emit({"schema_version": models.SCHEMA_VERSION, "ok": False,
               "status": "needs_observation", "context_id": context["context_id"],
               "account": context["account"], "selected_topic": None,
               "candidates": [], "required_actions": [_action(
                   "observation", None,
                   f"参照している観測が壊れています（保存後に書き換わった可能性が"
                   f"あります）: {damaged}", "TopicObservation")],
               "warnings": [], "shortfalls": [], "notice": advice.NOTICE,
               "error": {"code": "broken_evidence",
                          "message": f"参照している観測が読めません: {damaged}"}})
        return 1

    context.pop("profile_snapshot", None)
    proposal = models.validate_proposal(row["proposal"], article=article,
                                         known_observation_ids=set(observations))
    envelope = advice.evaluate(context, article=article, proposal=proposal,
                                observations=observations)
    # **記事を先に atomic 保存し、参照が揃ってから判断を保存する**（設計 §6）。
    # 途中で止まって参照されない記事が残るのは許すが、不完全な判断は見せない。
    store.put("articles", article, id_key="article_id")
    store.put("proposals", proposal, id_key="proposal_id")
    decision = dict(envelope)
    decision.pop("context", None)
    decision.pop("evidence", None)
    decision.pop("record_payload", None)
    decision.update({"context": context, "article_id": article["article_id"],
                      "proposal_id": proposal["proposal_id"], "recorded_by": by})
    decision["decision_id"] = models.content_id(decision, exclude=("decision_id",))
    saved, wrote = store.put("decisions", decision, id_key="decision_id")
    envelope["decision_id"] = saved["decision_id"]
    envelope["stored"] = wrote
    _emit(envelope)
    return _exit_code(envelope)


def cmd_decision(args) -> int:
    """保存済みの判断を読む。**内容は上書きしない**（設計 §6）。

    今の原稿と食い違っていれば、判断そのものは変えずに `freshness` で別に言う。
    """
    row = store.get("decisions", args.decision_id)
    if row is None:
        return _fail("not_found", f"その判断は保存されていません: {args.decision_id}")
    out = dict(row)
    draft = (row.get("context") or {}).get("draft_path")
    try:
        with open(draft, "rb") as f:
            import hashlib
            now_sha = hashlib.sha256(f.read()).hexdigest()
        same = now_sha == row["context"]["draft_bytes_sha256"]
        out["freshness"] = {"draft_readable": True, "draft_unchanged": same}
    except (OSError, TypeError, KeyError):
        out["freshness"] = {"draft_readable": False, "draft_unchanged": None}
    _emit(out)
    return 0


def cmd_observation(args) -> int:
    """保存済みの観測を丸ごと読む（読むだけ）。

    `suggest` の `evidence` が大きくなったときに投稿例を削るので、**削った分を
    取りに来られる口**が要る（独立レビュー 2026-09-11・指摘 2）。
    既存 22 語から作った参考観測も、同じ ID で引ける。
    """
    row = store.get("observations", args.observation_id)
    if row is None:
        row = next((r for r in store.legacy_observations()
                     if r["observation_id"] == args.observation_id), None)
    if row is None:
        return _fail("not_found", f"その観測は保存されていません: {args.observation_id}")
    _emit({"ok": True, "observation": row, "notice": advice.NOTICE})
    return 0


def cmd_profile(args) -> int:
    """account の profile を確定・更新する（設計 §10・masaru 裁定 2026-09-11）。"""
    row = read_json(args.input, stdin=args.json_stdin, what="profile")
    if row is None:
        current = store.get_profile(args.account)
        if current is None:
            _emit({"ok": True, "account": args.account, "profile": None,
                   "notice": "この account の profile はまだありません。"
                              "既存のプロジェクト方針をもとに担当セッションが作ってください。"})
            return 0
        _emit({"ok": True, "account": args.account, "profile": current})
        return 0

    row = dict(row)
    row["account"] = args.account
    row["confirmed_by"] = _actor(args)
    profile = models.build_profile(row)
    saved = store.set_profile(profile)
    _emit({"ok": True, "account": args.account, "profile": saved,
           "notice": "新しい方針の決定・既存方針の変更・解消できない矛盾は "
                      "masaru に確認してください（トピック選定用 profile の確認権限です。"
                      "投稿そのものの承認権限は変わりません）。"})
    return 0


# --- parser -----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thth topics",
        description="記事別のトピック提案（読取・検査。THTH は候補を作らない）")
    sub = parser.add_subparsers(dest="sub", required=True)

    p = sub.add_parser("suggest", help="原稿と記事から、足りない資料か検査結果を返す")
    p.add_argument("file", help="queue ファイルのパス")
    p.add_argument("--article", default=None, help="記事証拠 JSON のパス")
    p.add_argument("--article-json-stdin", action="store_true",
                    help="記事証拠 JSON を標準入力から読む（MCP 用）")
    p.add_argument("--proposal", default=None, help="候補比較 JSON のパス")
    p.add_argument("--proposal-json-stdin", action="store_true",
                    help="候補比較 JSON を標準入力から読む")
    p.add_argument("--input-json-stdin", action="store_true",
                    help="{\"article\": …, \"proposal\": …} を標準入力から 1 つ読む（MCP 用）")
    p.add_argument("--article-url", default=None,
                    help="本文に URL が複数ある場合の主対象")
    p.add_argument("--profile", default=None,
                    help="この検討だけに使う profile（保存済みを書き換えない）")
    p.add_argument("--json", action="store_true", help="既定で JSON（受け取るだけ）")
    p.set_defaults(func=cmd_suggest)

    p = sub.add_parser("observe", help="観測（誰がいたか）を残す")
    p.add_argument("--input", default=None)
    p.add_argument("--json-stdin", action="store_true")
    p.add_argument("--by", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_observe)

    p = sub.add_parser("record-decision", help="検査した判断を残す")
    p.add_argument("--input", default=None)
    p.add_argument("--json-stdin", action="store_true")
    p.add_argument("--by", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_record_decision)

    p = sub.add_parser("decision", help="保存済みの判断を読む")
    p.add_argument("decision_id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_decision)

    p = sub.add_parser("observation", help="保存済みの観測を丸ごと読む")
    p.add_argument("observation_id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_observation)

    p = sub.add_parser("profile", help="account の profile を見る・確定する")
    p.add_argument("account")
    p.add_argument("--input", default=None)
    p.add_argument("--json-stdin", action="store_true")
    p.add_argument("--by", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_profile)
    return parser


def dispatch(argv: list) -> int:
    """`topics` を取り除いた残りを新しい parser に渡す。"""
    collision = account_collision()
    if collision:
        return _fail("subcommand_collides_with_account",
                      f"`{collision}` という account があるため、"
                      f"`thth topics {collision}` が曖昧です。"
                      f"account 名を変えるか、この機能の語を変えてください")
    i = argv.index("topics")
    args = build_parser().parse_args(argv[i + 1:])
    try:
        return args.func(args)
    except InputError as e:
        return _fail(e.code, e.message)
    except models.SchemaError as e:
        return _fail("schema_error", str(e))
    except FileNotFoundError as e:
        return _fail("unreadable_input", str(e))
    except KeyError as e:
        return _fail("schema_error", f"必須の項目がありません: {e}")
    except accounts_mod.AccountError as e:
        return _fail("unknown_account", str(e))
    except store.StoreError as e:
        return _fail("store_busy", str(e))
