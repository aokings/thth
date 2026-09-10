"""記事別トピック提案の型と ID（設計 §4.1〜4.5・§13 P0）。

**保存も判断もしない。** 形を決めて、検査して、ID を計算するだけ。
保存は `topic_store`（P1）、判断は `topic_advice`（P2）。

**ID は中身から決める**（canonical JSON の SHA-256）。同じ入力なら同じ ID になり、
違う入力なら必ず違う ID になる。これで「どの記事・どの原稿・どの観測に対する
判断か」が後から動かせなくなる。

**設計から変えた点**（masaru 指示 2026-09-11）:

- **引用の文字 offset を持たない。** 固定した記事本文に対する**引用文字列の一致**
  だけで検査する。offset は空白の正規化ひとつでずれるので、脆いわりに得るものが
  少ない。改竄の検知という目的は文字列一致で足りる。
"""
from __future__ import annotations

import datetime
import hashlib
import json

SCHEMA_VERSION = 1

RETRIEVAL_STATUS = ("ok", "blocked", "failed", "partial")
COVERAGE = ("full", "partial", "unknown")
# 判断の語彙（設計 §4.4）。既存の alive/mismatch/dead は `thth.topics` 側の旧語彙で、
# 工程 5 でここへ寄せる。
FIT = ("suitable", "unsuitable", "uncertain")
DECISION_STATE = ("recommended", "provisional", "abstained")
PRIMARY_GOAL = ("article_visits", "relevant_conversation", "awareness")
PROFILE_STATUS = ("confirmed", "provisional")


class SchemaError(ValueError):
    """形が違う。**推測で補わない**——何が足りないかを述べて止まる。"""


def canonical_json(obj) -> bytes:
    """ID 計算に使う決まった形（設計 §8）。

    `sort_keys`・固定 separators・UTF-8・NaN 禁止。**同じ中身なら必ず同じバイト列**に
    なることだけが要件。人が読む用ではない。
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def content_id(obj: dict, *, exclude: tuple = ()) -> str:
    """中身から決まる ID（`sha256:` 付き 64 桁）。

    `exclude` に挙げた鍵は計算から外す（ID 自身を含めないため）。
    """
    payload = {k: v for k, v in obj.items() if k not in exclude}
    return "sha256:" + hashlib.sha256(canonical_json(payload)).hexdigest()


def _require(row: dict, keys: tuple, what: str) -> None:
    missing = [k for k in keys if k not in row]
    if missing:
        raise SchemaError(f"{what} に項目が足りません: {missing}")


def _require_choice(value, allowed: tuple, what: str) -> None:
    if value not in allowed:
        raise SchemaError(f"{what} は {allowed} のどれか: {value!r}")


def _require_iso(value, what: str) -> None:
    if not isinstance(value, str):
        raise SchemaError(f"{what} は ISO 8601 の文字列: {value!r}")
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError as e:
        raise SchemaError(f"{what} を時刻として読めません: {value!r}") from e
    if parsed.tzinfo is None:
        raise SchemaError(f"{what} に timezone がありません: {value!r}")


# --- 4.1 記事証拠 -----------------------------------------------------------

ARTICLE_KEYS = ("requested_url", "final_url", "retrieved_at", "provider",
                "submitted_by", "retrieval_status", "title", "language",
                "content_text", "coverage", "source_locator")


def build_article(row: dict) -> dict:
    """記事証拠を検査して `article_id` と `content_sha256` を付ける（設計 §4.1）。

    **`summary` で全文を代用しない。** `content_text` は取得した本文そのもの。
    `coverage` は取得者の申告であって、真偽を検証した印ではない。
    """
    _require(row, ARTICLE_KEYS, "記事証拠")
    _require_choice(row["retrieval_status"], RETRIEVAL_STATUS, "retrieval_status")
    _require_choice(row["coverage"], COVERAGE, "coverage")
    _require_iso(row["retrieved_at"], "retrieved_at")
    if not isinstance(row["content_text"], str):
        raise SchemaError("content_text は文字列")

    out = dict(row)
    out["schema_version"] = SCHEMA_VERSION
    out["content_sha256"] = hashlib.sha256(
        row["content_text"].encode("utf-8")).hexdigest()
    out["article_id"] = content_id(out, exclude=("article_id",))
    return out


def quote_is_in_article(article: dict, quote: str) -> bool:
    """引用が**その記事本文にそのまま在るか**（masaru 指示 2026-09-11）。

    offset は持たない。**固定した本文に対する文字列の一致だけ**を見る。
    空白の正規化はしない——「そのまま在るか」を問うているので、整形して合わせると
    検査にならない。
    """
    if not isinstance(quote, str) or not quote.strip():
        return False
    return quote in (article.get("content_text") or "")


# --- 4.3 判断コンテキスト ---------------------------------------------------

CONTEXT_KEYS = ("account", "profile_version", "draft_bytes_sha256", "section",
                "reply_to", "topic", "publish_at", "article_id",
                "article_content_sha256", "observation_ids", "policy_version")


def build_context(row: dict) -> dict:
    """判断の入力を固定して `context_id` を付ける（設計 §4.3）。

    **本文・topic・URL・宛先・返信先・予約時刻・記事内容・profile・証拠のいずれかが
    変われば別の context** になる（受け入れ T02・T03・T26）。表示用のパスは ID の
    計算に入れない（同じ中身なら置き場所が違っても同じ判断のため）。

    `source_state` は `local_draft / synced / unverified`。**提案ができることと
    公開できることを混同しない**ので、公開側の `matches_synced_commit` を
    分析の必須条件にしない（設計 §4.3）。
    """
    _require(row, CONTEXT_KEYS, "判断コンテキスト")
    out = dict(row)
    out["schema_version"] = SCHEMA_VERSION
    out.setdefault("source_state", "unverified")
    out["observation_ids"] = sorted(out["observation_ids"])
    out["context_id"] = content_id(out, exclude=("context_id", "draft_path"))
    return out


# --- 4.4 候補比較 -----------------------------------------------------------

CANDIDATE_KEYS = ("topic", "article_fit", "conversation_fit", "article_quotes",
                  "observation_refs", "rationale", "counterevidence",
                  "uncertainties", "fit")
PROPOSAL_KEYS = ("context_id", "prompt_version", "intended_reader", "article_value",
                 "post_angle", "candidates", "selected_topic", "selection_reason")


def validate_proposal(row: dict, *, article: dict, known_observation_ids: set) -> dict:
    """LLM が返した候補比較を検査する（設計 §4.4・受け入れ T10）。

    **THTH が検査できるのは、引用が本文に在るか・参照が実在するか・値域だけ。**
    意味の妥当性を機械的に証明したとは言わない（点数は説明付きの判断であって、
    THTH が測った客観値ではない）。
    """
    _require(row, PROPOSAL_KEYS, "候補比較")
    if not isinstance(row["candidates"], list) or not row["candidates"]:
        raise SchemaError("candidates が空です")

    problems = []
    for i, cand in enumerate(row["candidates"]):
        where = f"candidates[{i}]（{cand.get('topic')!r}）"
        try:
            _require(cand, CANDIDATE_KEYS, where)
            _require_choice(cand["fit"], FIT, f"{where} の fit")
            for key in ("article_fit", "conversation_fit"):
                if cand[key] not in (0, 1, 2, 3):
                    raise SchemaError(f"{where} の {key} は 0〜3: {cand[key]!r}")
            for quote in cand["article_quotes"]:
                if not quote_is_in_article(article, quote):
                    raise SchemaError(
                        f"{where} の引用が記事本文にありません: {quote[:40]!r}")
            unknown = [ref for ref in cand["observation_refs"]
                       if ref not in known_observation_ids]
            if unknown:
                raise SchemaError(f"{where} の観測 ID が実在しません: {unknown[:3]}")
        except SchemaError as e:
            problems.append(str(e))

    if problems:
        raise SchemaError("／".join(problems))

    topics = [c["topic"] for c in row["candidates"]]
    if row["selected_topic"] is not None and row["selected_topic"] not in topics:
        raise SchemaError("selected_topic が候補にありません")

    out = dict(row)
    out["schema_version"] = SCHEMA_VERSION
    out["proposal_id"] = content_id(out, exclude=("proposal_id",))
    return out


# --- 4.5 profile ------------------------------------------------------------

PROFILE_KEYS = ("account", "language", "primary_goal", "editorial_scope",
                "intended_interests", "avoid_misrepresentation", "status",
                "confirmed_by", "basis")


def build_profile(row: dict) -> dict:
    """account の profile（設計 §10・**masaru 裁定 2026-09-11 で変更**）。

    変更後の §10:

    > 初回 profile は**担当セッションが既存のプロジェクト方針に基づいて作成・
    > 確認する。根拠資料と確認者を記録する。** 新しい方針の決定、既存方針の変更、
    > 解消できない矛盾については masaru に確認する。

    実装での担保:

    - `basis`（根拠資料）が空のまま `confirmed` にできない。**根拠なしの確定を
      作らない。**
    - `confirmed_by`（確認したセッション）が要る。
    - 方針が不明・矛盾するときは `provisional` のまま置く（設計 §4.5 の
      `provisional` 判断の理由になる）。

    **これはトピック選定用 profile の確認権限であって、投稿の承認権限は変えない。**
    """
    _require(row, PROFILE_KEYS, "profile")
    _require_choice(row["primary_goal"], PRIMARY_GOAL, "primary_goal")
    _require_choice(row["status"], PROFILE_STATUS, "status")
    if not row.get("confirmed_by"):
        raise SchemaError("confirmed_by が要ります（誰が確認したか）")
    if row["status"] == "confirmed" and not row.get("basis"):
        raise SchemaError(
            "根拠（basis）が無いまま confirmed にはできません。"
            "既存方針のどこに基づくかを書くか、status を provisional にしてください。")

    out = dict(row)
    out["schema_version"] = SCHEMA_VERSION
    out["profile_version"] = content_id(out, exclude=("profile_version",))
    return out
