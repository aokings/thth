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


# --- 4.2 観測 ---------------------------------------------------------------

SEARCH_MODE = ("topic_tag", "keyword", "manual_unknown")
# 取得結果の語彙は `topics.py` と 1 つにする（2 か所で別々に育てない）。
OBS_STATUS = ("ok", "empty", "permission_denied", "unavailable",
              "rate_limited", "partial")
OBSERVATION_KEYS = ("topic", "search_mode", "provider", "retrieved_at", "status",
                    "samples")


def build_observation(row: dict) -> dict:
    """観測を検査して `observation_id` を付ける（設計 §4.2）。

    **空の JSON が観測として保存できた**（nigamilab・kopicha 両セッション報告
    2026-09-11）。`echo "{}" | thth topics observe` が `stored: true` を返し、
    `topic` も `samples` も `retrieved_at` も無い記録が**共有台帳に ID つきで
    残った。** `validate_proposal()` は参照が**実在するか**しか見ないので、
    中身の無い観測でも「参照はある」ことになる。

    `build_article` / `build_profile` には検査があったのに、**観測だけ
    素通しだった**——また例外を 1 つ作っていた。

    **`samples` の投稿者は `author_key` で数える。** `author` だけ書いて保存すると
    投稿者 0 人と数えられる（nigamilab セッション報告）。ここで言う。
    """
    _require(row, OBSERVATION_KEYS, "観測")
    _require_choice(row["search_mode"], SEARCH_MODE, "search_mode")
    _require_choice(row["status"], OBS_STATUS, "status")
    _require_iso(row["retrieved_at"], "retrieved_at")
    if not isinstance(row["topic"], str) or not row["topic"].strip():
        raise SchemaError("topic が空です")
    if not isinstance(row["samples"], list):
        raise SchemaError("samples は配列（0 件なら [] と status を書いてください）")
    if "coverage" in row and not isinstance(row["coverage"], (dict, type(None))):
        # **`ArticleEvidence` の `coverage` は文字列**なので取り違えが起きる
        # （kopicha セッション報告 2026-09-11）。**同じ名前で別の形。**
        raise SchemaError(
            f"観測の coverage は object です（記事証拠の coverage とは別物）: "
            f"{row['coverage']!r}。例 "
            f'{{"pages": 1, "fetched": 20, "has_more": true}}')
    for i, sample in enumerate(row["samples"]):
        if not isinstance(sample, dict):
            raise SchemaError(f"samples[{i}] は object")
        if not sample.get("author_key"):
            raise SchemaError(
                f"samples[{i}] に author_key がありません。"
                f"**投稿者はここで数えます**（`author` では数えません）——"
                f"偏りを見るための非可逆な識別子で足ります")
        # **その投稿が本当にそのトピックを付けているか**（nigamilab
        # セッション発見 2026-09-11）。トピック頁には**タグ付き・別タグ・
        # 本文一致**が混ざる。頁に出ていることは、**その語を使っている証拠に
        # ならない。** 名前の右にラベルが出ているかで判る。
        if row["search_mode"] == "topic_tag" and "tagged" not in sample:
            raise SchemaError(
                f"samples[{i}] に tagged がありません。"
                f"**トピック頁には、そのタグを付けていない投稿も並びます**"
                f"（別のタグ・本文やハッシュタグの一致）。名前の右に"
                f"`› <語>` のラベルが出ているかを見て、"
                f"`tagged: true/false` を書いてください")
        if "tagged" in sample and not isinstance(sample["tagged"], bool):
            raise SchemaError(f"samples[{i}] の tagged は true/false")
    if row["status"] == "ok" and not row["samples"]:
        raise SchemaError(
            "status が ok なのに samples が空です。"
            "取得できて 0 件だったなら status は empty です"
            "（**0 件は「人がいない」ではありません**）")

    out = dict(row)
    out["schema_version"] = SCHEMA_VERSION
    out.setdefault("normalized_topic", row["topic"])
    out["observation_id"] = content_id(out, exclude=("observation_id",))
    return out


# --- 4.3 判断コンテキスト ---------------------------------------------------

CONTEXT_KEYS = ("account", "profile_version", "draft_bytes_sha256", "section",
                "reply_to", "topic", "publish_at", "article_id",
                "article_content_sha256", "observation_ids", "policy_version",
                # **主対象の記事も入力**（独立レビュー 2026-09-11・指摘 1）。
                # ID の外に置いていたので、`--article-url` を別の記事に変えても
                # context_id が動かなかった——**主対象を変えても同じ判断のまま**
                # 通る形になっていた。
                "main_article_url")


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
    # **`source_state` は ID に入れない。** 同じ原稿・同じ記事・同じ観測なら、
    # それが commit 済みかどうかで別の判断にはならない。入れてしまうと、
    # 承認して同期した瞬間に直前の判断が `stale_context` になる——**提案できる
    # ことと公開できることを混同しない**（設計 §4.3）に反する。
    out["context_id"] = content_id(
        out, exclude=("context_id", "draft_path", "source_state"))
    return out


# --- 4.4 候補比較 -----------------------------------------------------------

CANDIDATE_KEYS = ("topic", "article_fit", "conversation_fit", "article_quotes",
                  "observation_refs", "rationale", "counterevidence",
                  "uncertainties", "fit")
# **根拠の欠けと、結果の読めなさは別物**（kopicha セッション報告 2026-09-11）。
# 任意。書けば「何が足りないか」として扱う。
CANDIDATE_OPTIONAL = ("evidence_gaps",)
PROPOSAL_KEYS = ("context_id", "prompt_version", "intended_reader", "article_value",
                 "post_angle", "candidates", "selected_topic", "selection_reason")


def validate_proposal(row: dict, *, article: dict, known_observation_ids: set,
                       known_topics: dict | None = None) -> dict:
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
            quotes = cand["article_quotes"]
            if not isinstance(quotes, list) or any(not isinstance(q, str)
                                                    for q in quotes):
                raise SchemaError(f"{where} の article_quotes は文字列の配列")
            if cand["fit"] == "suitable" and not [q for q in quotes if q.strip()]:
                # **引用が 0 本なら、引用の検査は 1 度も走らない。**
                # for 文が 0 回まわって「不一致なし」で通ってしまう——
                # **根拠そのものが無いのに、根拠の充足条件を満たしたことになる**
                # （独立レビュー 2026-09-11・指摘 3）。適合すると言う候補には
                # 記事本文からの引用を要る。
                raise SchemaError(
                    f"{where} は fit=suitable なのに article_quotes が空です"
                    f"（記事本文からの引用を 1 つ以上）")
            for quote in quotes:
                if not quote_is_in_article(article, quote):
                    raise SchemaError(
                        f"{where} の引用が記事本文にありません: {quote[:40]!r}")
            if not isinstance(cand["observation_refs"], list):
                raise SchemaError(f"{where} の observation_refs は配列")
            unknown = [ref for ref in cand["observation_refs"]
                       if ref not in known_observation_ids]
            if unknown:
                # **正解を並べる**（asmon 関東セッション報告 2026-09-11）。
                # ID は記憶から書けないので、間違えたら**取り直しの往復**に
                # なる。`suggest` の出力には入っているが、**弾くときに
                # 手元に出すほうが早い。**
                raise SchemaError(
                    f"{where} の観測 ID が実在しません: {unknown[:3]}"
                    + _known_refs_hint(cand.get("topic"), known_topics))
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
# 任意の項目（既存の profile を壊さない）。`avoid_forms` は
# **その account が使わないと決めた連投の型**（masaru 裁定 2026-09-11）。
PROFILE_OPTIONAL = ("avoid_forms",)


def _known_refs_hint(topic, known_topics: dict | None) -> str:
    """この束で参照できる観測 ID を並べる。**その語のものを先に。**"""
    if not known_topics:
        return ""
    same = sorted(oid for oid, t in known_topics.items() if t == topic)
    lines = []
    if same:
        lines.append(f"「{topic}」で使えるのは: {'・'.join(same)}")
    others = sorted(set(known_topics) - set(same))
    if others:
        shown = others[:8]
        more = f"（ほか {len(others) - len(shown)} 件）" if len(others) > len(shown) else ""
        pairs = "・".join(f"{known_topics[o]}={o[:19]}…" for o in shown)
        lines.append(f"ほかの語: {pairs}{more}")
    return "。" + "。".join(lines) if lines else ""


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
    # **使わない型の宣言**（任意・masaru 裁定 2026-09-11）。
    # 既存の profile を壊さないため必須にしない。
    if "avoid_forms" in row:
        from . import forms as forms_mod
        if not isinstance(row["avoid_forms"], list):
            raise SchemaError("avoid_forms は配列です")
        unknown = [f for f in row["avoid_forms"] if f not in forms_mod.FORMS]
        if unknown:
            raise SchemaError(
                f"avoid_forms に知らない型があります: {unknown}。"
                f"使えるのは: {'・'.join(forms_mod.FORMS)}")
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
