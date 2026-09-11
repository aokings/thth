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
import traceback

from . import accounts as accounts_mod
from . import topic_advice as advice
from . import topic_models as models
from . import topic_store as store
from . import topics as topics_mod

SUBCOMMANDS = ("suggest", "observe", "record-decision", "decision",
                "profile", "observation", "retract", "unretract",
                # 検収と修正理由（Codex §6.2・§7・§12 第 1 段階）。
                "record-vocabulary", "vocabulary", "record-review", "review",
                # 型の仕様と、検収履歴からの改善候補（§12 第 2 段階）。
                "record-form-spec", "form-spec", "form-check", "improvements",
                # 語彙を替えたら何が読めなくなるか（§8 手順 2・3）。
                "impact")

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
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise InputError("invalid_json", f"{what}（{origin}）を JSON として読めません: {e}")
    _ORIGIN[id(parsed)] = origin
    return parsed


# **どのファイルを読んだか**（kopicha セッション報告 2026-09-11）。
# `thth` は VM 側で走るので、手元のつもりのパスが**別のセッションが VM に
# 置いた同名ファイル**を指すことがある。「項目が足りません」だけでは、
# 別の記事を読んだと気づけない。**中身には混ぜない**（内容 hash が変わる）。
_ORIGIN: dict = {}


def _origin_of(obj) -> str | None:
    return _ORIGIN.get(id(obj))


def _with_origin(obj, message: str) -> str:
    origin = _origin_of(obj)
    if not origin:
        return message
    head = ""
    if isinstance(obj, dict):
        for key in ("final_url", "requested_url", "url", "title"):
            if obj.get(key):
                head = f"・{key}={str(obj[key])[:60]}"
                break
    return f"{message}（読んだのは {origin}{head}）"


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
                         ("TopicObservation", advice.OBSERVATION_SHAPE),
                         ("TopicProposal", advice.PROPOSAL_SHAPE),
                         ("ReviewRecord", models.REVIEW_SHAPE),
                         ("ReasonVocabulary", models.VOCABULARY_SHAPE),
                         ("FormSpec", models.FORM_SPEC_SHAPE)):
        if name in message:
            schema.update(shape)
    if not schema and "記事" in message:
        schema.update(advice.ARTICLE_SHAPE)
    if not schema and ("観測" in message or "samples" in message):
        schema.update(advice.OBSERVATION_SHAPE)
    if not schema and ("候補比較" in message or "candidates" in message):
        schema.update(advice.PROPOSAL_SHAPE)
    if not schema and ("検収" in message or "findings" in message
                        or "disposition" in message or "reason_id" in message):
        schema.update(models.REVIEW_SHAPE)
    if not schema and ("型の仕様" in message or "roles" in message
                        or "machine_checks" in message
                        or "semantic_questions" in message):
        schema.update(models.FORM_SPEC_SHAPE)
    if not schema and ("語彙" in message or "entries" in message):
        schema.update(models.VOCABULARY_SHAPE)
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

    try:
        article = models.build_article(article_raw) if article_raw else None
    except models.SchemaError as e:
        raise models.SchemaError(_with_origin(article_raw, str(e))) from e

    # **観測は記事の有無に関係なく読む。** 以前は記事が無いと空にしていたので、
    # 「まず何が分かっているか」を聞く最初の呼び出しで**手持ちの根拠が
    # 返らなかった**（独立レビュー 2026-09-11・指摘 2）。
    observations, broken, taken = _all_observations()

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
                known_observation_ids=set(observations),
                known_topics=_topic_by_id(observations))
        except models.SchemaError as e:
            out = advice.rejected(context["context_id"], context["account"], str(e))
            _emit(out)
            return 1

    envelope = advice.evaluate(context, article=article, proposal=proposal,
                                observations=observations)
    if taken:
        # **取り下げは「壊れている」ではない。** 誰が何を理由に下げたか、
        # という別の事実（asmon 関東セッション提案 2026-09-11）。混ぜない。
        envelope["warnings"].append(
            f"取り下げられた観測が {len(taken)} 件あります（記録は残っています）: "
            + "／".join(f"{t.get('topic') or '（語なし）'}: {t['reason']}"
                         f"（{t['retracted_by']}）" for t in taken[:3]))
    if broken:
        # **「壊れている」と決めつけない。** 中身が足りない（検査を足す前に
        # 保存された）場合と、保存後に書き換わった場合の両方がここに来る。
        # どちらも「使えない」が、原因が違う——**推測で言わない。**
        # **件数と並べる数を食い違わせない**（kopicha セッション報告
        # 2026-09-11: 「4 件あります」なのに配列は 3 件だった）。
        shown = broken[:5]
        more = (f"（ほか {len(broken) - len(shown)} 件）"
                 if len(broken) > len(shown) else "")
        envelope["warnings"].append(
            f"使えない観測の記録が {len(broken)} 件あります"
            f"（無いのではなく、中身が足りないか保存後に変わっています）: "
            f"{shown}{more}")

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


def _topic_by_id(observations: dict) -> dict:
    """`{observation_id: 語}`。**弾くときに正解を並べるため。**"""
    return {oid: (obs.get("topic") or obs.get("normalized_topic"))
            for oid, obs in observations.items()}


def _all_observations() -> tuple:
    """保存済みの観測と、**既存 22 語から作った参考観測**をまとめて返す。

    以前は保存済みのものだけだった。**そのため `--note` で入れた記録は
    `observation_refs` に書けず、「観測が足りない」と言われても満たす手段が
    存在しなかった**（asmon 関東セッション報告 2026-09-11）。

    参考観測は投稿例を持たないので、**これだけでは `recommended` にならない**
    ——「投稿例を控えてください」という**満たせる**不足になる。
    """
    rows, broken, taken = store.load_all("observations")
    out = {r["observation_id"]: r for r in rows}
    withdrawn = set(store.retractions())
    for row in store.legacy_observations():
        if row["observation_id"] in withdrawn:
            continue
        out.setdefault(row["observation_id"], row)
    return out, broken, taken


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


def _relevance_order(context: dict, observations: dict) -> list:
    """**その原稿に関係する観測を先に出す**（kopicha セッション報告 2026-09-11）。

    > 記事 B の `evidence.observations` に並んだのはコーヒーの観測 3 件でした。
    > その原稿のトピック（日本茶）の記録は 1 件も出ません。

    ID の辞書順で切っていたので、**関係のない語が先に予算を使い切っていた。**
    原稿の topic と本文に出てくる語を先に、あとは新しい順。
    """
    text = (context.get("section") or "") + " " + (context.get("topic") or "")

    def key(oid):
        obs = observations[oid]
        topic = obs.get("topic") or obs.get("normalized_topic") or ""
        near = 0 if (topic and topic in text) else 1
        return (near, str(obs.get("retrieved_at") or ""), oid)

    return sorted(observations, key=key)


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
                # **ID は観測の中に置く**（kopicha セッション報告 2026-09-11）。
                # 行の top-level にだけ置いていたので、`observation` を見ていた
                # 読み手には**無いのと同じ**だった。
                "observation_id": by_topic.get(topic),
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
    for oid in _relevance_order(context, observations):
        obs = dict(observations[oid])
        # **表示のときだけ補う**（保存された記録は書き換えない）。検査を足す前の
        # 記録は `normalized_topic` しか持たず、`topic: null` と並んで読めない。
        if not obs.get("topic") and obs.get("normalized_topic"):
            obs["topic"] = obs["normalized_topic"]
            obs["topic_filled_from"] = "normalized_topic"
        if obs.get("provenance") == "legacy":
            # **既存 22 語は `legacy_notes` に出る。** ここに重ねると、
            # 実際に見てきた観測が埋もれる（実運用報告 2026-09-11）。
            # 参照できる ID は `legacy_notes[].observation.observation_id`。
            continue
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

    segments = context.get("segments") or []
    return {
        # **段に分けて返す。** 連結した 1 本の文字列だと、読み手は
        # 「どこで切れて何本の投稿になるか」を判断できない（設計 §10）。
        "post_text": segments[0] if len(segments) == 1 else None,
        "segments": segments,
        "segment_count": context.get("segment_count", len(segments)),
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
    if not isinstance(row, dict):
        raise InputError("invalid_json", "観測は object にしてください")
    # **中身を検査してから保存する**（両セッション報告 2026-09-11）。
    # 以前は素通しで、`{}` が `stored: true` になっていた。
    row = models.build_observation(
        {k: v for k, v in row.items()
         if k not in ("observation_id", "schema_version")}
        | {"submitted_by": _actor(args)})
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
    observations, broken, _taken = _all_observations()
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
    proposal = models.validate_proposal(
        row["proposal"], article=article,
        known_observation_ids=set(observations),
        known_topics=_topic_by_id(observations))
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

    `decision_id` の代わりに account 名を渡すと、**その account の新しい順に
    並べて返す**（kopicha セッション報告 2026-09-11: decision_id を控えて
    いないと読み返せなかった）。
    """
    if not args.decision_id.startswith("sha256:"):
        return _recent_decisions(args.decision_id)
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


def _recent_decisions(account: str) -> int:
    """account の判断を新しい順に並べる（読むだけ）。"""
    try:
        accounts_mod.load_account(account)
    except accounts_mod.AccountError as e:
        return _fail("unknown_account", str(e))
    rows, broken, _taken = store.load_all("decisions")
    mine = [r for r in rows if (r.get("context") or {}).get("account") == account]
    mine.sort(key=lambda r: (r.get("context") or {}).get("publish_at") or "",
               reverse=True)
    _emit({"ok": True, "account": account, "count": len(mine),
           "broken_ids": broken,
           "decisions": [{"decision_id": r["decision_id"], "status": r.get("status"),
                           "selected_topic": r.get("selected_topic"),
                           "publish_at": (r.get("context") or {}).get("publish_at"),
                           "draft_path": (r.get("context") or {}).get("draft_path"),
                           "recorded_by": r.get("recorded_by")} for r in mine],
           "notice": advice.NOTICE})
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


def cmd_retract(args) -> int:
    """観測を取り下げる。**消さない**（asmon 関東セッション提案 2026-09-11）。

    > 観測は内容アドレスで、**消すと参照していた候補比較が壊れます。**
    > 本当に要るのは削除ではなく「取り下げ」かもしれません。

    間違って入れた観測は運用が続けば定期的に出る。**そのたびに本番の state を
    手で消すのは重いし危ない。** 取り下げなら記録は残り、過去の判断が参照して
    いた事実も消えない。
    """
    row = store.retract(args.observation_id, reason=args.reason or "",
                         by=_actor(args))
    _emit({"ok": True, "retracted": row,
           "notice": "記録は消していません。新しい候補比較から参照できなく"
                      "なるだけです。戻すときは `thth topics unretract <id>`。"})
    return 0


def cmd_unretract(args) -> int:
    """取り下げを取り消す。**記録を消していないので戻せる。**"""
    ok = store.unretract(args.observation_id)
    if not ok:
        return _fail("not_found",
                      f"その観測は取り下げられていません: {args.observation_id}")
    _emit({"ok": True, "observation_id": args.observation_id,
           "notice": "取り下げを取り消しました。また参照できます。"})
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


# --- 検収と修正理由（Codex 構想書 §6.2・§7・§12 第 1 段階） ------------------

# **一つの緑表示にまとめない**（Codex §7）。機械が確かめられることと、意味の
# 評価と、人の確認は別の欄に出す。**THTH が見ていないもの**も名前で言う。
OUT_OF_SCOPE = ("記事内容そのものの科学的妥当性", "公開してよいかどうか",
                 "反応が読めるかどうか")
REVIEW_NOTICE = (
    "編集診断です。**公開の可否には使いません**（承認・予約・同期は変わりません）。"
    "未評価と問題なしは別です。")


def _draft_sha256(path: str | None, what: str) -> str | None:
    """原稿の**バイト列**から fingerprint を作る（Codex §6.1）。

    **パスは保存しない。** 置き場所が変わっても同じ原稿、本文が変われば別の原稿。
    """
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            import hashlib
            return hashlib.sha256(f.read()).hexdigest()
    except OSError as e:
        raise InputError("unreadable_input", f"{what}を読めません: {e}") from e


def _known_ids(account: str | None = None) -> set:
    """根拠として参照できる ID を**account の境界を見て**集める。

    以前は articles・observations・decisions・proposals・reviews の全 account
    の ID を 1 つの集合にプールしていた。**そのため `record-review` で、別
    account の判断（decision）を自分の指摘の根拠として保存できた。** THTH の
    設計原則は「account を越えて判断を継承しない」なので、これは通さない
    （`cmd_record_review` の `supersedes`・`recheck_of` 等の鎖の照合と同じ筋）。

    - **共有の事実**（そのまま許す）: `articles`・`observations`・
      `store.legacy_observations()`。この 2 種類は記録に account の欄を
      持たず、「誰がいたか」「記事に何が書いてあるか」という account 非依存の
      事実だから。
    - **account に属する記録**（同じ account のものだけ許す）: `reviews`
      （`row["account"]`）・`decisions`（`row["context"]["account"]`）。
      `account` が渡されなければ（＝どの account の検収かまだ分からない場面）、
      共有の事実だけを返す。
    - `proposals` は**account を解決できない**（`proposal` は `context_id`
      しか持たない）。**解決できないものは許さない**——account を渡しても
      渡さなくても、proposal は常に対象外。
    """
    out = set()
    for kind in ("articles", "observations"):
        rows, _broken, _taken = store.load_all(kind)
        out |= {r[store.ID_KEY[kind]] for r in rows if r.get(store.ID_KEY[kind])}
    out |= {r["observation_id"] for r in store.legacy_observations()}
    if account:
        rows, _broken, _taken = store.load_all("reviews")
        out |= {r["review_id"] for r in rows
                if r.get("review_id") and r.get("account") == account}
        rows, _broken, _taken = store.load_all("decisions")
        out |= {r["decision_id"] for r in rows if r.get("decision_id")
                and (r.get("context") or {}).get("account") == account}
    return out


def _foreign_evidence(refs: list, account: str | None) -> dict:
    """`refs` のうち、**実在はするが account の境界で根拠にできない**ものを
    `{ref: 断り文}` で返す。

    実在しない ID はここに含めない（`build_review` 側の「根拠 ID が実在しま
    せん」に任せる）。**「無い」と「他所のもの」は別の事実**なので、文言を
    分ける——ID が実在すらしないのか、他 account のものだから使えないのかを
    読み手が区別できるようにする。
    """
    out = {}
    if not refs:
        return out
    refs = set(refs)

    rows, _broken, _taken = store.load_all("decisions")
    for row in rows:
        rid = row.get("decision_id")
        if rid not in refs or rid in out:
            continue
        owner = (row.get("context") or {}).get("account")
        if owner != account:
            out[rid] = (f"その根拠は別の account の記録です"
                         f"（{account} の検収に {owner or '不明'} の判断は"
                         f"使えません）。**アカウントを越えて判断を継承しません**")

    rows, _broken, _taken = store.load_all("reviews")
    for row in rows:
        rid = row.get("review_id")
        if rid not in refs or rid in out:
            continue
        owner = row.get("account")
        if owner != account:
            out[rid] = (f"その根拠は別の account の記録です"
                         f"（{account} の検収に {owner or '不明'} の検収は"
                         f"使えません）。**アカウントを越えて判断を継承しません**")

    rows, _broken, _taken = store.load_all("proposals")
    for row in rows:
        rid = row.get("proposal_id")
        if rid not in refs or rid in out:
            continue
        # **proposal は context_id しか持たず、account を解決できない。**
        out[rid] = ("この候補比較がどの account のものか解決できないので"
                     "根拠にできません（`proposal` は `context_id` しか"
                     "持ちません）。**アカウントを越えて判断を継承しません**")

    return out


def cmd_record_vocabulary(args) -> int:
    """修正理由の語彙を 1 版、記録として残す（引継ぎ 設計条件 1）。

    **コードではなくデータ。** `forms.py` の語彙はモジュール定数だったので、
    入れ替えに commit と配布が要った。ここは**保存 1 回**で差し替わる。
    """
    row = read_json(args.input, stdin=args.json_stdin, what="理由語彙")
    if row is None:
        raise InputError("missing_input",
                          "--input か --json-stdin で理由語彙を渡してください")
    if not isinstance(row, dict):
        raise InputError("invalid_json", "理由語彙は object にしてください")
    vocab = models.build_vocabulary(
        {k: v for k, v in row.items()
         if k not in ("vocabulary_id", "schema_version")}
        | {"created_by": _actor(args)})
    # 語彙にも同じ穴があった（再検収 F4 と同じ形）。**自己申告の accepted を
    # 保存させない**——採用は独立確認のあと（構想書 §8）。
    accepted = sorted(e["reason_id"] for e in vocab["entries"]
                      if e["state"] == "accepted")
    if accepted:
        return _fail(
            "promotion_not_implemented",
            f"accepted の理由を登録できません: {accepted}。"
            f"いま登録できるのは proposed か shadow までです。"
            f"**正式な採用は未実装**で、独立確認の照合も採用判断の記録も"
            f"まだありません（構想書 §8）")
    saved, wrote = store.put("vocabularies", vocab, id_key="vocabulary_id")

    states = {}
    for entry in saved["entries"]:
        states[entry["state"]] = states.get(entry["state"], 0) + 1
    _emit({"ok": True, "vocabulary_id": saved["vocabulary_id"], "stored": wrote,
           "name": saved["name"], "supersedes": saved["supersedes"],
           "entries": len(saved["entries"]), "states": states,
           "warnings": ["**共通仕様の採用は masaru または保守責任者が独立"
                         "レビューを踏まえて確定します**（構想書 §8）。"
                         "実績から自動で採用しません。いまは accepted を"
                         "登録できません"],
           "notice": REVIEW_NOTICE})
    return 0


def cmd_vocabulary(args) -> int:
    """保存済みの語彙を読む（読むだけ）。"""
    if args.vocabulary_id:
        row = store.get("vocabularies", args.vocabulary_id)
        if row is None:
            return _fail("not_found",
                          f"その理由語彙は保存されていません: {args.vocabulary_id}")
        _emit({"ok": True, "vocabulary": row, "notice": REVIEW_NOTICE})
        return 0
    rows, broken, _taken = store.load_all("vocabularies")
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    _emit({"ok": True, "count": len(rows), "broken_ids": broken,
           "vocabularies": [{
               "vocabulary_id": r["vocabulary_id"], "name": r.get("name"),
               "created_at": r.get("created_at"), "created_by": r.get("created_by"),
               "supersedes": r.get("supersedes"),
               "reason_ids": [e["reason_id"] for e in r.get("entries") or []],
               "states": {e["reason_id"]: e["state"]
                           for e in r.get("entries") or []}} for r in rows],
           "notice": REVIEW_NOTICE})
    return 0


def _chain_problem(key: str, review: dict, target: dict) -> str | None:
    """鎖の 3 本を**意味で使い分けさせる**（再検収 F8・F7）。

    - `supersedes`  … 同じ版の同じ指摘に、**後から処置を決めた**
    - `recheck_of`  … その処置の**改訂先をもう一度見た**
    - `carried_from`… 同じ指摘を**新しい版へ持ち越した**

    版の関係を見ないと、3 つとも「棚に在る何か」を指せてしまう。
    """
    mine = review.get("draft_sha256")
    theirs = target.get("draft_sha256")
    if key == "supersedes":
        if mine != theirs:
            return (f"supersedes は**同じ版の同じ指摘に処置を付ける**ための線です"
                     f"（指紋が違います: {str(mine)[:12]}… / {str(theirs)[:12]}…）。"
                     f"新しい版へ指摘を持ち越すなら `carried_from` を使ってください")
        return None
    if key == "carried_from":
        if mine == theirs:
            return ("carried_from は**新しい版へ持ち越す**ための線です"
                     "（同じ版なら `supersedes`）")
        return None
    expected = target.get("revised_draft_sha256") or theirs
    if mine != expected:
        return (f"recheck_of の版が合いません。**再検査は、その処置が指した"
                 f"改訂先を見るもの**です（期待 {str(expected)[:12]}… / "
                 f"いま {str(mine)[:12]}…）")
    return None

def cmd_record_review(args) -> int:
    """検収と修正理由を 1 件残す（Codex §6.2）。

    **原稿はパスでなく本文の SHA-256 で指す**（§6.1）。`--draft` を渡せばここで
    計算する。JSON にも `draft_sha256` があって食い違うときは**黙って直さない。**

    **語彙が手元に無ければ保存しない。** どの版の語彙で判定したかは、後から
    決められない（未知の語彙版を既知の分類に読み替えないため・A05）。
    """
    row = read_json(args.input, stdin=args.json_stdin, what="検収記録")
    if row is None:
        raise InputError("missing_input",
                          "--input か --json-stdin で検収記録を渡してください")
    if not isinstance(row, dict):
        raise InputError("invalid_json", "検収記録は object にしてください")
    row = {k: v for k, v in row.items()
           if k not in ("review_id", "schema_version")}

    for flag, key, what in (("draft", "draft_sha256", "原稿"),
                             ("revised_draft", "revised_draft_sha256", "修正後の原稿")):
        computed = _draft_sha256(getattr(args, flag.replace("-", "_"), None), what)
        if computed is None:
            continue
        if row.get(key) and row[key] != computed:
            raise InputError(
                "draft_mismatch",
                f"--{flag.replace('_', '-')} の中身と {key} が違います"
                f"（{row[key][:12]}… / {computed[:12]}…）。"
                f"**どちらが正しいかはこちらでは決められません。**"
                f"検収したのがどの本文かを確かめてください")
        row[key] = computed

    if getattr(args, "provenance", None):
        row["provenance"] = args.provenance
    judged_by = dict(row.get("judged_by") or {})
    judged_by["id"] = _actor(args)
    row["judged_by"] = judged_by

    vocabulary_id = row.get("vocabulary_id")
    if not isinstance(vocabulary_id, str) or not vocabulary_id:
        return _fail("missing_vocabulary",
                      "vocabulary_id がありません。**どの版の語彙で判定したかは"
                      "後から決められません**（`thth topics vocabulary` で"
                      "登録済みの版を見てください）")
    vocabulary = store.get("vocabularies", vocabulary_id)
    if vocabulary is None:
        return _fail("unknown_vocabulary",
                      f"その理由語彙は保存されていません: {vocabulary_id}。"
                      f"**既知の分類へ読み替えません。**先に "
                      f"`thth topics record-vocabulary` で登録してください")

    # **根拠を account の境界で見る**（引継ぎ 2026-09-11: 直し 1）。形の検査
    # （`build_review`）より先に見る——「無い」と「他所のもの」は別の事実
    # なので、文言を分けたまま断る。
    account = row.get("account") if isinstance(row.get("account"), str) else None
    findings = row.get("findings")
    if account and isinstance(findings, list):
        refs = [r for f in findings if isinstance(f, dict)
                for r in (f.get("evidence_refs") or []) if isinstance(r, str)]
        foreign = _foreign_evidence(refs, account)
        if foreign:
            shown = list(foreign.items())[:3]
            detail = "／".join(f"{ref}: {why}" for ref, why in shown)
            return _fail("unrelated_reference",
                          f"根拠として使えない参照があります。{detail}")

    review = models.build_review(row, vocabulary=vocabulary,
                                  known_ids=_known_ids(account))
    # **形を先に見る。** 棚に聞くのはそのあと——`supersedes: "前のやつ"` に
    # 「ID の形が違います」とだけ返すと、**どの欄の話か分からない**。
    for key, what in (("recheck_of", "再検査の対象"),
                       ("supersedes", "処置を付ける先の記録"),
                       ("carried_from", "持ち越す元の記録")):
        if not review.get(key):
            continue
        target = store.get("reviews", review[key])
        if target is None:
            return _fail("not_found", f"{what}が保存されていません: {review[key]}")
        # **鎖の意味を照合する**（再検収 F8）。以前は「棚に在るか」しか見て
        # いなかったので、**別 account・別原稿の記録を supersedes できた。**
        # 在るだけのものを「処置の鎖」として保存しない。
        if target.get("account") != review.get("account"):
            return _fail(
                "unrelated_reference",
                f"{what}が別の account の記録です"
                f"（{target.get('account')!r} / {review.get('account')!r}）。"
                f"**account を越えて処置を継承しません**（構想書 §3）")
        problem = _chain_problem(key, review, target)
        if problem:
            return _fail("unrelated_reference", problem)
    saved, wrote = store.put("reviews", review, id_key="review_id")
    _emit({"ok": True, "review_id": saved["review_id"], "stored": wrote,
           "account": saved["account"], "draft_sha256": saved["draft_sha256"],
           # **鎖を読み返さずに確かめられるように出す**（運用セッション報告
           # 2026-09-11: 改訂先が応答に無いので `review` を引き直していた）。
           "revised_draft_sha256": saved.get("revised_draft_sha256"),
           "recheck_of": saved.get("recheck_of"),
           "supersedes": saved.get("supersedes"),
           "disposition": saved["disposition"],
           "reasons": models.tally_reasons([saved]),
           "warnings": _vocabulary_warnings(models.tally_reasons([saved])),
           "notice": REVIEW_NOTICE})
    return 0


def _vocabulary_warnings(tally: dict) -> list:
    """**語彙が足りないことを、道具が自分で言う**（引継ぎ 設計条件 2）。

    `forms` は 3 セッションが 3 本書いて 3 本とも同じ型に落ちたが、**そのとき
    道具は何も言わなかった。** 同じ失敗を繰り返さない。
    """
    count = tally["other"]["count"]
    if not count:
        return []
    return [f"`other` が {count} 件あります。**この語彙は分けていない可能性が"
            f"あります**——内訳（reasons.other.notes）を読んで、新しい理由の"
            f"候補にするか決めてください"]


def _review_support(review: dict) -> dict:
    """その記録を**いまの棚の語彙で読めるか**（Codex §11・A05・設計 §8）。

    **「語彙が無い」と「語彙が壊れている」を混ぜない。** 棚の側は前からこの 2 つを
    区別していた（`load_all` の `broken`）のに、**こちらが `store.get` の例外を
    そのまま外へ出していた**ので、束で読むと 1 件壊れているだけで一覧全体が
    `store_error` で落ちた——**残りの検収まで読めなくなる。**
    """
    vocabulary_id = review.get("vocabulary_id")
    if not isinstance(vocabulary_id, str) or not vocabulary_id.startswith("sha256:"):
        return {"status": "unsupported",
                "reason": f"vocabulary_id がありません（{vocabulary_id!r}）",
                "unknown_reason_ids": [], "retired_reason_ids": []}
    try:
        vocabulary = store.get("vocabularies", vocabulary_id)
    except store.StoreError as e:
        # **壊れた語彙を「無い」と言わない。** 読めない理由をそのまま出す。
        return {"status": "unsupported",
                "reason": (f"理由語彙を読めません（{e}）。"
                            f"**既知の分類に読み替えません**"),
                "unknown_reason_ids": [], "retired_reason_ids": []}
    return models.review_support(review, vocabulary)


def _by_check_method(findings: list) -> dict:
    """§7 の区分に分ける。**機械の合格を意味の合格にしない。**"""
    groups = {"machine_checks": [], "semantic_evaluations": [],
               "human_confirmations": []}
    for finding in findings:
        key = {"machine_check": "machine_checks",
                "llm_eval": "semantic_evaluations"}.get(
                    finding.get("check_method"), "human_confirmations")
        groups[key].append(finding)
    return groups


def _next_actions(review: dict) -> list:
    """§7「次の操作」。**指摘を消す以外の道も見せる**（見送りも記録できる）。"""
    open_findings = [f for f in review.get("findings") or []
                     if f.get("result") in ("problem", "suspected")]
    if not open_findings or review.get("disposition") != "unresolved":
        return []
    return [_action("review", None,
                     f"未処置の指摘が {len(open_findings)} 件あります。"
                     f"直して `--revised-draft` 付きで `disposition: fixed` を"
                     f"記録するか、`disposition: dismissed` と "
                     f"`disposition_reason` で見送りを残してください"
                     f"（**見送りは「解決済み」ではありません**）",
                     "ReviewRecord")]


def cmd_review(args) -> int:
    """保存済みの検収を読む（読むだけ・Codex §7）。

    `review_id` の代わりに account 名を渡すと、その account の検収を新しい順に
    並べて、**理由の件数と `other` の内訳**を返す（設計条件 2）。
    """
    if not args.target.startswith("sha256:"):
        return _recent_reviews(args.target, args)

    row = store.get("reviews", args.target)
    if row is None:
        return _fail("not_found", f"その検収は保存されていません: {args.target}")
    support = _review_support(row)
    withdrawn = models.withdrawn_evidence(row, store.retractions())
    now_sha = _draft_sha256(args.draft, "原稿")
    out = {"ok": True, "review": row,
           "target": {"account": row.get("account"),
                       "draft_sha256": row.get("draft_sha256"),
                       "article_id": row.get("article_id"),
                       "profile_version": row.get("profile_version"),
                       "section": row.get("section")},
           "support": support,
           # **取り下げられた観測を根拠にしている指摘に印を付ける**（A12）。
           # 記録は書き換えない——読むときに言う。
           "withdrawn_evidence": withdrawn,
           "out_of_scope": list(OUT_OF_SCOPE),
           "next_actions": _next_actions(row),
           "reasons": models.tally_reasons([row]),
           "notice": REVIEW_NOTICE}
    out.update(_by_check_method(row.get("findings") or []))
    if now_sha is not None:
        # **原稿が変われば、この検収は今の原稿のものではない**（A03）。
        out["freshness"] = {"draft_readable": True,
                             "applies_to_draft":
                                 models.review_applies_to(row, now_sha),
                             "draft_sha256": now_sha}
    _emit(out)
    return 0


def _recent_reviews(account: str, args) -> int:
    try:
        accounts_mod.load_account(account)
    except accounts_mod.AccountError as e:
        return _fail("unknown_account", str(e))
    rows, broken, _taken = store.load_all("reviews")
    mine = [r for r in rows if r.get("account") == account]
    now_sha = _draft_sha256(args.draft, "原稿")
    # **未解決の履歴を黙って 0 件にしない**（再検収 F7・2026-09-11 Codex）。
    # 原稿を替えると、旧版に付いた持ち越し（`deferred`）や未処置が
    # **一覧から消えていた**——`ok: true, count: 0, warnings: []`。
    # 新しい原稿への「確認済み」を継承しないのは正しいが、
    # **「旧版に持ち越しがある」ことまで失ってよいわけではない。**
    # 旧指摘を現版の不備と断定はしない（別の欄に、別の名前で出す）。
    unreviewed_history = []
    if now_sha is not None:
        applies = [r for r in mine if models.review_applies_to(r, now_sha)]
        # **持ち越し先の review_id ごとに、持ち越し元を集める**（再判定 R3・
        # 2026-09-11 Codex）。以前は `carried_from` が指す旧記録を丸ごと
        # `unreviewed_history` から外していたが、**「持ち越した」のは新版の
        # 検収が扱った指摘 1 件だけ**で、旧記録に付いた他の指摘（未確認のまま）
        # まで一緒に消えていた。**対応を証明できない限り旧レビューは履歴に
        # 残す**——除外の代わりに `carried_by` で印を付けるだけにする。
        carried_by = {}
        for r in applies:
            source = r.get("carried_from")
            if source:
                carried_by.setdefault(source, []).append(r["review_id"])
        for review in mine:
            if models.review_applies_to(review, now_sha):
                continue
            if review.get("disposition") not in ("deferred", "unresolved"):
                continue
            by = sorted(carried_by.get(review["review_id"], []))
            entry = {
                "review_id": review["review_id"],
                "draft_sha256": review.get("draft_sha256"),
                "disposition": review.get("disposition"),
                "reason_ids": [f.get("reason_id")
                                for f in review.get("findings") or []],
                "note": "**旧版の記録です。** 現版に当たるかは確かめていません"
                         "——持ち越すなら `carried_from` で新しい版へ記録して"
                         "ください"}
            if by:
                # **忘れられているわけではないが、これだけで安心はできない。**
                # `carried_from` は 1 件の指摘の対応を示すだけで、この記録の
                # 指摘全部が確認されたとは限らない（reason_id だけの一致では
                # 同じ理由の複数段・別の対象範囲を区別できない）。
                entry["carried_by"] = by
                # **「対応した」ではなく「対応した"と申告された"」**（表示の
                # 条件・2026-09-12 Codex）。`carried_from` は 1 件の指摘に
                # 対応した"という記録者の申告"であって、指摘ごとの対応を
                # 機械が確認したわけではない。
                entry["note"] = ("**旧版の記録です。** このうち一部の指摘は"
                                  f"新しい版へ持ち越されています（`carried_by`"
                                  f": {', '.join(by)}）。**持ち越しが申告されて"
                                  "います。指摘ごとの対応は未確認です。**"
                                  "それ以外の指摘まで確認済みとは限らないので、"
                                  "この記録ごと履歴に残しています。")
            unreviewed_history.append(entry)
        mine = applies
    mine.sort(key=lambda r: r.get("judged_at") or "", reverse=True)

    unsupported, needs_recheck = [], []
    taken = store.retractions()
    for review in mine:
        withdrawn = models.withdrawn_evidence(review, taken)
        if withdrawn:
            needs_recheck.append({"review_id": review["review_id"],
                                   "withdrawn_evidence": withdrawn})
        support = _review_support(review)
        if support["status"] != "supported":
            # **対応外を 0 件・問題なしに変換しない**（A05）。数えずに名前で出す。
            # **1 件読めないことを、一覧全体が読めないことにもしない。**
            unsupported.append({"review_id": review["review_id"],
                                 "reason": support["reason"],
                                 "unknown_reason_ids":
                                     support["unknown_reason_ids"]})
    superseded = {}
    for review in mine:
        if review.get("supersedes"):
            superseded.setdefault(review["supersedes"], []).append(
                review["review_id"])
    tally = models.tally_reasons(mine)
    _emit({"ok": True, "account": account, "count": len(mine),
           "broken_ids": broken, "unsupported": unsupported,
           # **撤回されたら、依存する判断を再確認対象にする**（Codex §11・A12）。
           "needs_recheck": needs_recheck,
           # **出自の内訳**（運用セッションの申し出 2026-09-11）。試験のために
           # 書かれた原稿への指摘と、実運用の指摘を混ぜて数えないため。以前は
           # `improvements` にしか内訳が出ず、一覧を見ただけでは区別できなかった。
           "provenance": models.provenance_tally(mine),
           "reasons": tally, "out_of_scope": list(OUT_OF_SCOPE),
           "unreviewed_history": unreviewed_history,
           "warnings": ([f"この account には**旧版の未解決記録が "
                          f"{len(unreviewed_history)} 件**あります"
                          f"（unreviewed_history）。"
                          + ("**現版はまだ検収していません。**"
                             if not mine else "")]
                         if unreviewed_history else [])
           + _vocabulary_warnings(tally) + (
               [f"根拠の観測が取り下げられた検収が {len(needs_recheck)} 件"
                f"あります。**再確認の対象です**——判定をやり直してください"
                f"（記録は消していません。取り下げた事実も残っています）"]
               if needs_recheck else []),
           "reviews": [{"review_id": r["review_id"],
                         "draft_sha256": r.get("draft_sha256"),
                         "judged_by": r.get("judged_by"),
                         "judged_at": r.get("judged_at"),
                         "disposition": r.get("disposition"),
                         "reason_ids": [f.get("reason_id")
                                         for f in r.get("findings") or []],
                         "recheck_of": r.get("recheck_of"),
                         "supersedes": r.get("supersedes"),
                         # **上書きしないので、後から来た処置は「印」で示す。**
                         # 古い記録を消さずに、いまの処置がどれかを読めるように。
                         "superseded_by": superseded.get(r["review_id"], []),
                         # **試験のために書かれた原稿への指摘と、実運用の指摘を
                         # 混ぜて数えない**ため、各記録にも出自を添える。印が
                         # 付く前の記録は `unknown`（＝本番とは数えない）。
                         "provenance": r.get("provenance") or "unknown"}
                        for r in mine],
           "notice": REVIEW_NOTICE})
    return 0

# --- 型の仕様と改善候補（Codex §5・§12 第 2 段階） ---------------------------

def cmd_record_form_spec(args) -> int:
    """型の仕様を 1 版残す。**語彙と同じく、コードではなくデータ。**"""
    row = read_json(args.input, stdin=args.json_stdin, what="型の仕様")
    if row is None:
        raise InputError("missing_input",
                          "--input か --json-stdin で型の仕様を渡してください")
    if not isinstance(row, dict):
        raise InputError("invalid_json", "型の仕様は object にしてください")
    spec = models.build_form_spec(
        {k: v for k, v in row.items()
         if k not in ("form_spec_id", "schema_version")}
        | {"created_by": _actor(args)})
    # **未検証の accepted を保存させない**（再検収 F4・2026-09-11 Codex）。
    # 昇格の検査は正例の件数・反例の有無・scope の文字列しか見ていなかったので、
    # **実在しない review ID を付けた正例 2 件と反例 1 件で、書き手が自分で
    # `accepted` を保存できた。** scope を「… and every other account」と
    # 書いても通った。注意文を出すだけでは、**保存された `accepted` という
    # 主張は変わらない。**
    #
    # 正式な採用（独立確認と採用判断の記録）は第 3 段階の ChangeProposal で
    # 実装する。**できていない状態を開けておかない。**
    if spec["state"] not in ("proposed", "shadow"):
        return _fail(
            "promotion_not_implemented",
            f"いま登録できるのは proposed か shadow までです"
            f"（{spec['state']} は受け付けません）。**正式な採用は未実装**で、"
            f"独立確認の照合も採用判断の記録もまだありません（構想書 §8）。"
            f"自己申告の accepted を保存すると、**採用されたという主張だけが"
            f"残ります。**")
    problem = _cases_problem(spec)
    if problem:
        return _fail("unverified_cases", problem)
    saved, wrote = store.put("form_specs", spec, id_key="form_spec_id")
    _emit({"ok": True, "form_spec_id": saved["form_spec_id"], "stored": wrote,
           "form": saved["form"], "state": saved["state"],
           "scope": saved["scope"], "supersedes": saved["supersedes"],
           "roles": [r["role_id"] for r in saved["roles"]],
           "cases": {"positive": len([c for c in saved["cases"]
                                       if c["kind"] == "positive"]),
                      "counter": len([c for c in saved["cases"]
                                       if c["kind"] == "counter"])},
           "warnings": [f"state={saved['state']} です。**採用は masaru または"
                         f"保守責任者が独立確認を踏まえて確定します**"
                         f"（構想書 §8。いまは登録できません）"],
           "notice": REVIEW_NOTICE})
    return 0


def _cases_problem(spec: dict) -> str | None:
    """正例・反例が**実在する検収**を指しているか（再検収 F4）。

    件数だけ数えていたので、**実在しない review ID でも通った。** 挙げた
    review が在るか・同じ account か・同じ原稿かを見る。**在るだけのものを
    根拠として数えない。**
    """
    for i, case in enumerate(spec.get("cases") or []):
        where = f"cases[{i}]（{case.get('kind')}）"
        for review_id in case.get("review_ids") or []:
            row = store.get("reviews", review_id) \
                if isinstance(review_id, str) \
                and review_id.startswith("sha256:") else None
            if row is None:
                return (f"{where} の review_ids が実在しません: {review_id}。"
                         f"**挙げた検収が無ければ、その事例は根拠になりません**")
            if row.get("account") != case.get("account"):
                return (f"{where} の検収は別の account のものです"
                         f"（{row.get('account')!r} / {case.get('account')!r}）")
            if row.get("draft_sha256") != case.get("draft_sha256"):
                return (f"{where} の検収は別の原稿のものです"
                         f"（{str(row.get('draft_sha256'))[:12]}… / "
                         f"{str(case.get('draft_sha256'))[:12]}…）")
    return None

def cmd_form_spec(args) -> int:
    """保存済みの型の仕様を読む（読むだけ）。"""
    if args.form_spec_id:
        row = store.get("form_specs", args.form_spec_id)
        if row is None:
            return _fail("not_found",
                          f"その型の仕様は保存されていません: {args.form_spec_id}")
        _emit({"ok": True, "form_spec": row, "notice": REVIEW_NOTICE})
        return 0
    rows, broken, _taken = store.load_all("form_specs")
    if args.form:
        rows = [r for r in rows if r.get("form") == args.form]
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    _emit({"ok": True, "count": len(rows), "broken_ids": broken,
           "form_specs": [{"form_spec_id": r["form_spec_id"], "form": r.get("form"),
                            "state": r.get("state"), "scope": r.get("scope"),
                            "meaning_version": r.get("meaning_version"),
                            "supersedes": r.get("supersedes"),
                            "roles": [x["role_id"] for x in r.get("roles") or []]}
                           for r in rows],
           "notice": REVIEW_NOTICE})
    return 0


def _load_form_spec(form_spec_id: str):
    if not isinstance(form_spec_id, str) or not form_spec_id.startswith("sha256:"):
        return None, _fail("missing_form_spec",
                            "--form-spec に型の仕様の form_spec_id を渡して"
                            "ください（`thth topics form-spec` で見られます）")
    spec = store.get("form_specs", form_spec_id)
    if spec is None:
        return None, _fail("not_found",
                            f"その型の仕様は保存されていません: {form_spec_id}")
    return spec, None


def cmd_form_check(args) -> int:
    """段の役割の申告を、**機械で見られる範囲だけ**検査する（Codex §5・§7）。

    **THTH は「その段が本当にその役割を果たしているか」を見ていない。**
    意味評価は問いのまま `not_evaluated` で返す。

    根拠の ID も**account の境界で見る**（引継ぎ 2026-09-11: 直し 1）。
    `--account` は省略できる——ここには判定した記録の account が渡ってこない
    ので、渡されたらその account で絞り、省略されたら共有の事実（記事・観測）
    だけを許す。
    """
    spec, failure = _load_form_spec(args.form_spec)
    if spec is None:
        return failure
    claim = read_json(args.input, stdin=args.json_stdin, what="段の役割の申告")
    if claim is None:
        raise InputError("missing_input",
                          "--input か --json-stdin で段の役割の申告を渡してください")
    out = models.check_form_claim(spec, claim,
                                   known_ids=_known_ids(args.account))
    now_sha = _draft_sha256(args.draft, "原稿")
    if now_sha is not None:
        out["draft_sha256"] = now_sha
    out["ok"] = True
    out["out_of_scope"] = list(OUT_OF_SCOPE)
    _emit(out)
    return 0


def cmd_improvements(args) -> int:
    """検収履歴から**改善案を出すところまで**（Codex §12 第 2 段階）。

    **仕様も語彙も profile も書き換えない。** 出すのは候補と根拠の ID だけ。
    """
    spec, failure = _load_form_spec(args.form_spec)
    if spec is None:
        return failure
    rows, broken, _taken = store.load_all("reviews")
    if args.account:
        try:
            accounts_mod.load_account(args.account)
        except accounts_mod.AccountError as e:
            return _fail("unknown_account", str(e))
        rows = [r for r in rows if r.get("account") == args.account]
    # **参照を候補の入口でも解決する**（再検収 F5）。`review` の側にだけ
    # `unsupported` があって、改善候補の側は通っていた。
    vocabularies, form_specs = {}, {}
    for review in rows:
        for key, cache, kind in (("vocabulary_id", vocabularies, "vocabularies"),
                                  ("form_spec_id", form_specs, "form_specs")):
            ref = review.get(key)
            if not ref or ref in cache:
                continue
            try:
                cache[ref] = store.get(kind, ref) if isinstance(ref, str) \
                    and ref.startswith("sha256:") else None
            except store.StoreError:
                cache[ref] = None      # **壊れている＝解決できない。** 0 件にしない
    # **版の系統をたどる**（逆監査 2026-09-11）。`meaning_version` は語彙ごとに
    # 独立して採番されるので、**無関係な語彙が同じ番号を名乗っただけで束ねて
    # しまう。** `supersedes` をたどって系統の根を鍵に足す。
    lineage = {}
    for vocabulary_id, vocabulary in vocabularies.items():
        seen, cursor, row = set(), vocabulary_id, vocabulary
        while row is not None and row.get("supersedes") \
                and row["supersedes"] not in seen:
            seen.add(cursor)
            cursor = row["supersedes"]
            try:
                row = store.get("vocabularies", cursor)
            except store.StoreError:
                break          # **たどれないなら、そこを根として扱う**
        lineage[vocabulary_id] = cursor
    out = models.improvement_candidates(rows, spec=spec,
                                         vocabularies=vocabularies,
                                         form_specs=form_specs, lineage=lineage)
    out["ok"] = True
    out["account"] = args.account
    out["broken_ids"] = broken
    out["reviewed"] = len([r for r in rows if r.get("form") == spec["form"]])
    _emit(out)
    return 0

def cmd_impact(args) -> int:
    """新しい語彙を**現行版を維持したまま**当ててみる（Codex §8 手順 2・3）。

    **何も保存しない。** 「替えたらどの記録が読めなくなるか」を先に言う。
    """
    candidate = read_json(args.input, stdin=args.json_stdin, what="理由語彙")
    if candidate is None:
        raise InputError("missing_input",
                          "--input か --json-stdin で新しい理由語彙を渡してください")
    if not isinstance(candidate, dict):
        raise InputError("invalid_json", "理由語彙は object にしてください")
    # **形の検査は同じ関数へ合流させる**（保存はしない）。
    candidate = models.build_vocabulary(
        {k: v for k, v in candidate.items()
         if k not in ("vocabulary_id", "schema_version")}
        | {"created_by": candidate.get("created_by") or "（未保存の候補）"})

    current = None
    if args.against:
        current = store.get("vocabularies", args.against)
        if current is None:
            return _fail("not_found",
                          f"比べる語彙が保存されていません: {args.against}")

    rows, broken, _taken = store.load_all("reviews")
    if args.account:
        try:
            accounts_mod.load_account(args.account)
        except accounts_mod.AccountError as e:
            return _fail("unknown_account", str(e))
        rows = [r for r in rows if r.get("account") == args.account]
    if current is not None:
        rows = [r for r in rows if r.get("vocabulary_id") == args.against]

    out = models.vocabulary_impact(candidate, rows, current=current)
    out["ok"] = True
    out["account"] = args.account
    out["candidate_vocabulary_id"] = candidate["vocabulary_id"]
    out["compared_with"] = args.against
    out["broken_ids"] = broken
    out["stored"] = False
    _emit(out)
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

    p = sub.add_parser("retract", help="観測を取り下げる（消さない）")
    p.add_argument("observation_id")
    p.add_argument("--reason", default=None, help="なぜ取り下げるか（必須）")
    p.add_argument("--by", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_retract)

    p = sub.add_parser("unretract", help="取り下げを取り消す")
    p.add_argument("observation_id")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_unretract)

    p = sub.add_parser("profile", help="account の profile を見る・確定する")
    p.add_argument("account")
    p.add_argument("--input", default=None)
    p.add_argument("--json-stdin", action="store_true")
    p.add_argument("--by", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("record-vocabulary", help="修正理由の語彙を 1 版残す")
    p.add_argument("--input", default=None)
    p.add_argument("--json-stdin", action="store_true")
    p.add_argument("--by", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_record_vocabulary)

    p = sub.add_parser("vocabulary", help="保存済みの理由語彙を読む")
    p.add_argument("vocabulary_id", nargs="?", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_vocabulary)

    p = sub.add_parser("record-review", help="検収と修正理由を残す")
    p.add_argument("--input", default=None)
    p.add_argument("--json-stdin", action="store_true")
    p.add_argument("--draft", default=None,
                    help="**検収した原稿**のパス（中身から fingerprint を計算する）。"
                         "**直したあとに記録するときは使わない**——現物はもう直って"
                         "いるので、対象がずれる。その場合は直す前の指紋を JSON の "
                         "draft_sha256 に書く")
    p.add_argument("--provenance", default=None,
                    choices=list(models.PROVENANCE),
                    help="この検収の出自。**試験のために書かれた原稿への指摘を"
                         "実運用の傾向に数えない**ため。省略すると unknown "
                         "（＝本番とは数えません）")
    p.add_argument("--revised-draft", default=None,
                    help="**直したあとの原稿**のパス（disposition: fixed に要る）。"
                         "見つけた時点では直っていない・記録するのは直したあと、"
                         "という順序が往復では常態なので、2 つを分けてある")
    p.add_argument("--by", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_record_review)

    p = sub.add_parser("review", help="保存済みの検収を読む（account 名でも引ける）")
    p.add_argument("target", help="review_id か account 名")
    p.add_argument("--draft", default=None,
                    help="いまの原稿のパス（**変わっていれば確認済みを引き継がない**）")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("record-form-spec", help="型の仕様を 1 版残す")
    p.add_argument("--input", default=None)
    p.add_argument("--json-stdin", action="store_true")
    p.add_argument("--by", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_record_form_spec)

    p = sub.add_parser("form-spec", help="保存済みの型の仕様を読む")
    p.add_argument("form_spec_id", nargs="?", default=None)
    p.add_argument("--form", default=None, help="型の名前で絞る")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_form_spec)

    p = sub.add_parser("form-check",
                        help="段の役割の申告を検査する（**意味は見ていない**）")
    p.add_argument("--form-spec", default=None, help="型の仕様の form_spec_id")
    p.add_argument("--input", default=None)
    p.add_argument("--json-stdin", action="store_true")
    p.add_argument("--draft", default=None, help="原稿のパス（指紋を出力に付ける）")
    p.add_argument("--account", default=None,
                    help="根拠を絞る account（省略可）。省略すると共有の事実"
                         "（記事・観測）だけを根拠として許す")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_form_check)

    p = sub.add_parser("improvements",
                        help="検収履歴から改善候補を出す（**書き換えない**）")
    p.add_argument("--form-spec", default=None, help="型の仕様の form_spec_id")
    p.add_argument("--account", default=None, help="account で絞る")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_improvements)

    p = sub.add_parser("impact",
                        help="新しい理由語彙を当ててみる（**保存しない**）")
    p.add_argument("--input", default=None)
    p.add_argument("--json-stdin", action="store_true")
    p.add_argument("--against", default=None,
                    help="比べる現行版の vocabulary_id")
    p.add_argument("--account", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_impact)
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
    except store.BadId as e:
        return _fail("invalid_id", str(e))
    except store.StoreError as e:
        return _fail("store_error", str(e))
    except Exception as e:
        # **stdout を空で終わらせない**（kopicha セッション報告 2026-09-11）。
        # 想定外の例外で traceback だけが出ると、**呼ぶ側には「出力が無い」と
        # しか分からない。** 設計 §6 は「stdout は JSON だけ・ログは stderr」
        # と決めているので、落ちるときも JSON を返して、traceback は stderr へ。
        #
        # **握りつぶさない。** 例外の型と場所を応答に入れる——出さなければ、
        # 使う側は報告のしようがない。
        traceback.print_exc(file=sys.stderr)
        where = ""
        tb = e.__traceback__
        while tb is not None:
            frame = tb.tb_frame
            where = f"{os.path.basename(frame.f_code.co_filename)}:{tb.tb_lineno}"
            tb = tb.tb_next
        return _fail("internal_error",
                      f"想定していない失敗です（{type(e).__name__}: {e}）"
                      f"{f'・{where}' if where else ''}。"
                      f"**この文をそのまま統括に報告してください。**")
