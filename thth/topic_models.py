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
import re

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


# --- 4.6 修正理由の語彙（Codex §4・設計条件 1: コードではなくデータ） --------

# **語彙そのものはここに書かない。** `forms.py` の語彙はモジュール定数なので、
# 入れ替えに commit と配布が要った（引継ぎ 2026-09-11・設計条件 1）。修正理由は
# **ストアの中の版付きレコード**にする——差し替えが記録 1 件で済み、
# `proposed → shadow → accepted → retired`（Codex §8）がそのまま乗る。
#
# ここにあるのは**語彙の入れ物の形**だけ。中身（`unsupported_claim` ほか）は
# 記録として保存する。
REASON_STATE = ("proposed", "shadow", "accepted", "retired", "rejected")
# **使ってよい状態**。`retired`・`rejected` の理由で新しく指摘を書かない。
# **読むほうは別**——過去の記録は元のラベルのまま読める（Codex §8・A08）。
REASON_USABLE = ("proposed", "shadow", "accepted")
# 判定主体（Codex §6.2）。**finding の検査方式にも同じ語彙を使う**——
# 「機械検査」「LLM 評価」「人」を 2 か所で別々に育てない（§4: 名前を増やさない）。
JUDGE_KIND = ("human", "session", "llm_eval", "machine_check")
FINDING_RESULT = ("problem", "suspected", "no_problem", "not_evaluated")
# 処置（Codex §6.2）。**`fixed` は「直した」であって「正しくなった」ではない。**
# 解決済みを表す値をわざと置いていない（「修正しただけで解消済みにしない」）。
DISPOSITION = ("fixed", "dismissed", "deferred", "unresolved")
# **説明を必須にする理由 ID**（設計条件 2）。`other` が増えたら語彙を疑う。
OTHER_REASON = "other"
# 検収の**出自**（再検収の付帯指摘・運用セッションの申し出 2026-09-11）。
#
# > 指摘は本物、原稿は試験データ。…ここから出た `missing_condition` 6 件を
# > 「うちの原稿は条件が抜けやすい」の根拠に使うと、**試作 1 本の癖を全体の
# > 傾向として読む**ことになります。
#
# **`unknown` は「本番」ではない。** 印が付く前に書かれた記録がここに入る。
PROVENANCE = ("production", "trial", "unknown")

_REASON_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# 正例と反例（Codex §8）。**型の側と、理由の採用の側で同じ語を使う。**
CASE_KINDS = ("positive", "counter")
_REF_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

VOCABULARY_KEYS = ("name", "entries", "created_at", "created_by", "supersedes")
ENTRY_KEYS = ("reason_id", "display", "definition", "includes", "excludes",
              "scope", "judged_by", "state", "meaning_version")


# --- 修正理由の採用（masaru 裁定 2026-09-12・2 番）-----------------------------
#
# > **確認できた項目から個別に `accepted` へ上げましょう。** 実際の原稿で意味・
# > 使い分け・適用例を確認する。**`accepted` は「編集に使う語彙として採用した」で
# > あり「反応率を改善すると実証された」ではありません。一括昇格はしません。**
#
# **語彙の記録は書き換えない**（内容アドレスの追記のみ）。採用は**別の記録**として
# 1 件ずつ積み、読むときに重ねる。**1 レコード 1 項目**——まとめて上げる口を作ら
# ない。
ADOPTION_KEYS = ("vocabulary_id", "reason_id", "cases", "meaning",
                  "distinguished_from", "applied_example", "decided_by",
                  "decided_at", "meaning_version")

# **`accepted` は何を保証する状態か**（masaru 指示 2026-09-12）。
#
# **条件を先に決めない。** まず「採用済みとは何が言える状態か」を決め、
# **条件はそこから導く。**
#
# **保証すること**——**別の人が、同じ語を、同じ意味で使える。**
#   - その語が何を指すかが書いてある（`meaning`）
#   - 隣の語とどう違うかが書いてある（`distinguished_from`）
#   - 実際の原稿で当てた例がある（`cases` の正例）
#   - **当てはまらなかった例もある**（`cases` の反例）——境界が引けていないと、
#     次の人は違う意味で使う
#
# **保証しないこと**
#   - **反応率を改善すること。** 効果は測っていない（masaru 裁定 2 番）
#   - **すべての原稿に当てはまること。** 適用範囲は語彙側の `scope`
#   - **将来も同じ意味であること。** 意味が変われば `meaning_version` を上げる
ACCEPTED_GUARANTEE = (
    "**別の人が、同じ語を、同じ意味で使える**という状態です。"
    "**反応率を改善すること・すべての原稿に当てはまること・将来も同じ意味である"
    "ことは保証しません。**")

# 記録に毎回書く——読む人が入れ替わるので、
# 「採用済み＝効果が実証済み」と読まれる余地を残さない。
ADOPTION_DISCLAIMER = ("**編集に使う語彙として採用した、という意味です。"
                        "反応率を改善すると実証された、という意味ではありません。**")


def _require_adoption_evidence(cases: list) -> None:
    """採用に足る材料か。

    **まず `ACCEPTED_GUARANTEE` がある。** 「別の人が、同じ語を、同じ意味で
    使える」——そこから、材料として**当てはまった例**と**当てはまらなかった例**が
    要る、が出てくる。**境界が引けていないと、次の人は違う意味で使う。**

    **件数のしきい値（正例 2・反例 1）は暫定です**（masaru 指示 2026-09-12:
    **型の昇格条件の流用を、決定済みの条件として扱わない**）。いまは型
    （`form_spec`）の `_require_promotion` と同じ数にしてあるが、**理由の語彙に
    その数が適切かは決まっていない。** 決まるまでの置き場所であって、根拠では
    ない。**変えるときは、しきい値だけでなく `ACCEPTED_GUARANTEE` から
    引き直すこと。**

    **「同じ原稿を 2 回数えない」は、しきい値とは別の話。** 同じ原稿の同じ段を
    2 回記録しても、**別の事例にはならない**——ここは数の問題ではない。

    **判定者の人数は条件にしていません**（masaru 指示: 「3 人目必須」を決定済みの
    条件として扱わない）。運用セッションが材料として挙げたが、**決まっていない。**
    """
    positives = [c for c in cases if c["kind"] == "positive"]
    counters = [c for c in cases if c["kind"] == "counter"]
    independent = {c["sha256"] for c in positives}
    if len(independent) < 2:
        raise SchemaError(
            f"採用には**独立した正例が 2 件以上**要ります（いま {len(independent)} 件）。"
            f"**同じ原稿を 2 回数えません。** 揃わないうちは proposed か shadow の"
            f"ままにしてください。**この件数は暫定です**（型の昇格条件からの借用で、"
            f"理由の語彙に適切かは未決）")
    if not counters:
        raise SchemaError(
            "採用には**反例が 1 件以上**要ります"
            "（この語に当てはまりそうだが当てはまらない、と判定した記録）。"
            "**反例が無いと、語の境界を確かめたことになりません**"
            "——採用が保証するのは「別の人が同じ意味で使える」ことなので")


def build_reason_adoption(row: dict) -> dict:
    """修正理由 1 件の採用を記録する（masaru 裁定 2026-09-12）。

    **1 レコード 1 項目。** `reason_id` は 1 つだけ——**配列を受け取らない。**
    まとめて上げたいときは、**1 件ずつ実際の原稿で確かめて、1 件ずつ積む。**

    `checked_on` は**実際に確かめた原稿**（`queue` の相対パスと、そのときの内容の
    sha256）。**「確かめた」と書くだけにしない**——**どの原稿で確かめたのかが
    後から辿れること**が、この記録の値打ち。
    """
    _require(row, ADOPTION_KEYS, "理由の採用")
    if not isinstance(row["vocabulary_id"], str) or not _REF_ID_RE.fullmatch(
            row["vocabulary_id"]):
        raise SchemaError("vocabulary_id は語彙の ID（sha256:…）")
    if not isinstance(row["reason_id"], str) or not row["reason_id"].strip():
        # **配列を弾く。** 「一括昇格はしない」を型で守る。
        raise SchemaError("reason_id は 1 件だけの文字列です"
                           "（**まとめて上げる口はありません**）")
    if not isinstance(row["cases"], list) or not row["cases"]:
        raise SchemaError("cases に、実際に確かめた原稿を 1 つ以上")
    for i, c in enumerate(row["cases"]):
        if not isinstance(c, dict):
            raise SchemaError(f"cases[{i}] は object")
        _require_choice(c.get("kind"), CASE_KINDS, f"cases[{i}] の kind")
        for key in ("path", "sha256"):
            if not isinstance(c.get(key), str) or not c[key].strip():
                raise SchemaError(f"cases[{i}].{key} が空です"
                                   f"（**どの原稿で確かめたのかを残す**）")
        if not _SHA256_HEX_RE.match(c["sha256"]):
            raise SchemaError(f"cases[{i}].sha256 は 64 桁の 16 進数")
        if not isinstance(c.get("account"), str) or not c["account"].strip():
            raise SchemaError(f"cases[{i}].account が空です")

    # **手で数えていたことを、道具が数える**（外部の合意ではなく検査にする）。
    # **型（`form_spec`）の側には昇格条件があったのに、理由の採用には入れて
    # いなかった**——「確認できた項目から」が、**書けば通る**になっていた。
    # 2026-09-12 に運用セッションが手で数え直して「まだ 1 件も採用できない」と
    # 結論した。**その数え方を、ここに置く。**
    _require_adoption_evidence(row["cases"])
    for key in ("meaning", "distinguished_from", "applied_example"):
        if not isinstance(row.get(key), str) or not row[key].strip():
            raise SchemaError(
                f"{key} が空です（意味・使い分け・適用例の 3 つを、"
                f"**実際の原稿で確かめた内容として**書いてください）")
    if not isinstance(row["meaning_version"], int) or row["meaning_version"] < 1:
        raise SchemaError("meaning_version は 1 以上の整数"
                           "（**どの意味の版を採用したか**）")
    if not row.get("decided_by"):
        raise SchemaError("decided_by が要ります（誰が決めたか）")
    _require_iso(row["decided_at"], "decided_at")

    out = {k: row[k] for k in ADOPTION_KEYS}
    out["state"] = "accepted"
    out["guarantees"] = ACCEPTED_GUARANTEE
    out["means"] = ADOPTION_DISCLAIMER
    out["schema_version"] = SCHEMA_VERSION
    out["adoption_id"] = content_id(out, exclude=("adoption_id",))
    return out


ADOPTION_SHAPE = {
    "ReasonAdoption": {
        "vocabulary_id": "採用する項目が載っている語彙の ID（sha256:…）",
        "reason_id": "**1 件だけ**。配列は受け取りません（一括昇格はしません）",
        "meaning_version": "採用する意味の版（整数・1 以上）",
        "cases": [{"kind": list(CASE_KINDS),
                    "path": "実際に確かめた原稿（queue の相対パス）",
                    "sha256": "そのときの内容の sha256（64 桁）",
                    "account": "その原稿の account"}],
        "**採用の条件**": "独立した正例 2 件以上（同じ原稿は 2 回数えない）＋ 反例 1 件以上",
        "meaning": "その語が何を指すか（**原稿で確かめた内容**）",
        "distinguished_from": "隣の語とどう使い分けたか",
        "applied_example": "実際にどう当てたか",
        "decided_by": "誰が決めたか",
        "decided_at": "いつ（ISO8601・+09:00）",
        "**accepted が保証すること**": ACCEPTED_GUARANTEE,
        "**注意**": ADOPTION_DISCLAIMER,
    }
}


def build_vocabulary(row: dict) -> dict:
    """修正理由の語彙を検査して `vocabulary_id` を付ける（Codex §4・設計条件 1）。

    **表記だけの別名と、意味の再分類を区別する**（Codex §8）ので、項目ごとに
    `meaning_version` を持つ。同じ言葉でも意味が変わるなら上げる。

    **`other` を欠いた語彙を作れない**（設計条件 2）。分類できない指摘の行き先が
    無い語彙は、「分類できなかった」という事実を**記録できない**形になる——
    道具が自分で「語彙が足りない」と言えなくなる。`forms` で 3 セッションが
    3 本とも `問い→答え` に落ちたとき、**道具は何も言わなかった。**

    `accepted` にするには**含む例と含まない例**が要る。`proposed` / `shadow` の
    うちは空でよい（書きながら育てる）。
    """
    _require(row, VOCABULARY_KEYS, "理由語彙")
    if not isinstance(row["name"], str) or not row["name"].strip():
        raise SchemaError("name が空です")
    _require_iso(row["created_at"], "created_at")
    if not row.get("created_by"):
        raise SchemaError("created_by が要ります（誰が作ったか）")
    if row["supersedes"] is not None and (
            not isinstance(row["supersedes"], str)
            or not _REF_ID_RE.fullmatch(row["supersedes"])):
        # **表記まで見る**（2026-09-12）。ただし**これは仮説側を直したときに
        # 置換がここにも効いたもので、こちらが意図して入れた変更ではなく、
        # 依頼書にも書いていなかった**——外部レビューが差分から自分で拾って
        # 検証し、「新たな不合格項目にはしないが、**今後の変更報告には仮説・
        # 語彙の両方を記載してほしい**」との裁定。同じ 13 入力を語彙にも通して
        # 結果が一致することまで確認された上で残っている。
        raise SchemaError("supersedes は前の版の vocabulary_id か null")
    entries = row["entries"]
    if not isinstance(entries, list) or not entries:
        raise SchemaError("entries が空です（理由の定義を 1 つ以上）")

    seen = set()
    for i, entry in enumerate(entries):
        where = f"entries[{i}]（{entry.get('reason_id')!r}）" \
            if isinstance(entry, dict) else f"entries[{i}]"
        if not isinstance(entry, dict):
            raise SchemaError(f"{where} は object")
        _require(entry, ENTRY_KEYS, where)
        reason_id = entry["reason_id"]
        if not isinstance(reason_id, str) or not _REASON_ID_RE.match(reason_id):
            raise SchemaError(
                f"{where} の reason_id は英小文字・数字・下線（2〜63 文字）: "
                f"{reason_id!r}")
        if reason_id in seen:
            # **同じ ID で意味の違う定義を 2 つ置けない。** どちらで判定したかが
            # 後から決まらなくなる。
            raise SchemaError(f"{where} の reason_id が重複しています: {reason_id}")
        seen.add(reason_id)
        _require_choice(entry["state"], REASON_STATE, f"{where} の state")
        if not isinstance(entry["meaning_version"], int) \
                or entry["meaning_version"] < 1:
            raise SchemaError(f"{where} の meaning_version は 1 以上の整数")
        for key in ("display", "definition", "scope"):
            if not isinstance(entry[key], str) or not entry[key].strip():
                raise SchemaError(f"{where} の {key} が空です")
        for key in ("includes", "excludes"):
            if not isinstance(entry[key], list) \
                    or any(not isinstance(x, str) for x in entry[key]):
                raise SchemaError(f"{where} の {key} は文字列の配列")
        if entry["state"] == "accepted":
            for key in ("includes", "excludes"):
                if not [x for x in entry[key] if x.strip()]:
                    raise SchemaError(
                        f"{where} は state=accepted なのに {key} が空です"
                        f"（含む例と含まない例が無い定義は、**同じ箱に違う判断を"
                        f"入れたかどうかを後から確かめられません**）")
        judges = entry["judged_by"]
        if not isinstance(judges, list) or not judges:
            raise SchemaError(f"{where} の judged_by は空でない配列")
        for judge in judges:
            _require_choice(judge, JUDGE_KIND, f"{where} の judged_by")

    usable = {e["reason_id"] for e in entries if e["state"] in REASON_USABLE}
    if OTHER_REASON not in usable:
        raise SchemaError(
            f"{OTHER_REASON!r} が使える状態でありません。"
            f"**既存分類で説明できない指摘の行き先が無い語彙は使えません**"
            f"（分類できなかったという事実を記録できない＝語彙の不足を"
            f"道具が自分で言えない）")

    out = dict(row)
    out["schema_version"] = SCHEMA_VERSION
    out["vocabulary_id"] = content_id(out, exclude=("vocabulary_id",))
    return out


def reason_entry(vocabulary: dict, reason_id: str) -> dict | None:
    for entry in vocabulary.get("entries") or []:
        if entry.get("reason_id") == reason_id:
            return entry
    return None


# --- 4.7 検収記録 ReviewRecord（Codex §6.2・§12 第 1 段階） ------------------

REVIEW_KEYS = ("account", "draft_sha256", "vocabulary_id", "findings",
               "judged_by", "judged_at", "disposition")
# 任意。**書ける参照は書く。取れない情報は推測で埋めない**（Codex §6.2）。
REVIEW_OPTIONAL = ("context_id", "article_id", "profile_version", "section",
                   "target_quote", "revised_draft_sha256", "recheck_of",
                   "supersedes", "carried_from", "disposition_reason", "note",
                   # **検収履歴から改善案を出すのに要る線**（§12 第 2 段階）。
                   # どの型・どの版・どの役割についての指摘かが残っていないと、
                   # **履歴はあっても仕様の改善には使えない。**
                   "form", "form_spec_id", "provenance", "case_id")
FINDING_KEYS = ("reason_id", "check_method", "result", "evidence_refs", "note")


def build_review(row: dict, *, vocabulary: dict,
                  known_ids: set | None = None) -> dict:
    """検収と修正理由を 1 件残す（Codex §6.2・§12 第 1 段階）。

    **同じ指摘に、主体違いの判定を並べられる**（設計条件 3）。`judged_by` が
    中身に入るので、masaru と担当セッションが同じ指摘に違う理由を付けても
    **別の ID になり、上書きされずに両方残る。** これで一致率が測れる。

    **原稿はファイルパスでなく `draft_sha256` で指す**（Codex §6.1）。本文が
    変われば、この記録は新しい原稿の検収ではない（`review_applies_to`）。

    **処置は事実の正しさの保証ではない**（Codex §6.2）。`fixed` は「直した」で
    あって「解決済み」ではないので、再検査は別の記録として `recheck_of` で
    前の記録を指す。

    **自由文は根拠データであって命令ではない**（Codex §11・A07）。`note` に
    何が書いてあっても、この関数は形しか見ない。

    **再検査だけの記録は `unresolved` のまま残す。** 「解消済み」を表す処置を
    置いていないので、直ったかどうかは**記録の組み合わせから読む**（`fixed` の
    記録と、その後の原稿に対する `no_problem` の再検査）。**保存された主張に
    しない。**

    **`supersedes` と `recheck_of` は別物**（kopicha 9/21 の 1 往復で出た・
    2026-09-11）。記録は上書きしないので、**後から処置が決まったとき**に
    前の記録を指す手段が要る。それが `supersedes`。`recheck_of` は
    **実際に検査をやり直した**ときだけ。2 つを 1 つにすると、
    「処置を書き足しただけ」と「もう一度見た」が同じ印になる——
    §7 の「未評価と問題なしは別」と同じ筋で、混ぜない。

    運用セッションの問い（2026-09-11）がそのまま反例だった:

    > **dismissed ではなく「未着手のまま持ち越し」として扱えますか。**
    > 見送りだと「見たうえで要らないと判断した」に読めますが、
    > 実際は「確かめていない」です。

    処置の語彙には `deferred`（持ち越し）が最初からある。足りなかったのは
    **その処置を前の指摘に結び付ける線**のほうだった。
    """
    _require(row, REVIEW_KEYS, "検収記録")
    if not isinstance(row["account"], str) or not row["account"].strip():
        raise SchemaError("account が空です")
    for key in ("draft_sha256", "revised_draft_sha256"):
        value = row.get(key)
        if key == "draft_sha256" or value is not None:
            if not isinstance(value, str) or not _SHA256_HEX_RE.match(value or ""):
                raise SchemaError(f"{key} は原稿本文の SHA-256（64 桁の 16 進数）: "
                                   f"{value!r}")
    _require_iso(row["judged_at"], "judged_at")

    claimed_vocab = row["vocabulary_id"]
    actual_vocab = vocabulary.get("vocabulary_id")
    if claimed_vocab != actual_vocab:
        raise SchemaError(
            f"vocabulary_id が渡された語彙と違います（{claimed_vocab} / "
            f"{actual_vocab}）。**どの版の語彙で判定したかは後から決められません**")

    judged_by = row["judged_by"]
    if not isinstance(judged_by, dict):
        raise SchemaError('judged_by は object（例 {"kind": "human", "id": "masaru"}）')
    _require_choice(judged_by.get("kind"), JUDGE_KIND, "judged_by の kind")
    if not judged_by.get("id"):
        raise SchemaError("judged_by に id が要ります（誰・どのセッションか）")

    _require_choice(row["disposition"], DISPOSITION, "disposition")
    if row.get("revised_draft_sha256") \
            and row["revised_draft_sha256"] == row.get("draft_sha256"):
        # **直す前と後が同じ指紋になることはない**（運用セッションが踏みかけた・
        # 2026-09-11）。指摘は「直す前の原稿」に対するものなのに、`--draft` は
        # 現物（＝もう直っている）から計算する。そのまま両方渡すと、
        # **判定した対象と記録される対象がずれたまま通ってしまう。**
        raise SchemaError(
            "draft_sha256 と revised_draft_sha256 が同じです。"
            "**指摘は直す前の原稿に対するもの**なので、`--draft` に現物を渡すと"
            "（もう直っているので）対象がずれます。直す前の指紋を JSON の "
            "draft_sha256 に書き、直した後を `--revised-draft` で渡してください")
    if row["disposition"] == "fixed" and not row.get("revised_draft_sha256"):
        raise SchemaError(
            "disposition が fixed なのに revised_draft_sha256 がありません。"
            "**「直した」は、直した後の原稿を指して初めて確かめられます**"
            "（直したことは、正しくなったことではありません）")
    if "provenance" in row:
        _require_choice(row["provenance"], PROVENANCE, "provenance")
    if "case_id" in row and (not isinstance(row["case_id"], str)
                              or not row["case_id"].strip()):
        # **独立性は推測できない**（再判定 R5・2026-09-11 Codex）。指紋が違う
        # ことは別の事例であることではない（同じ原稿の改訂かもしれない）。
        # **人が「これは別の事例だ」と言った**ときだけ独立として数える。
        raise SchemaError("case_id は空でない文字列（原稿の系列の名前）")
    for key in ("recheck_of", "supersedes", "carried_from"):
        value = row.get(key)
        if value is not None and (not isinstance(value, str)
                                   or not value.startswith("sha256:")):
            raise SchemaError(f"{key} は前の記録の review_id（sha256: 付き）: "
                               f"{value!r}")
    if row["disposition"] in ("dismissed", "deferred") \
            and not (row.get("disposition_reason") or "").strip():
        raise SchemaError(
            f"disposition が {row['disposition']} なら disposition_reason が"
            f"要ります（**見送りは「解決済み」ではありません**。"
            f"なぜ見送るかを残します）")

    findings = row["findings"]
    if not isinstance(findings, list) or not findings:
        raise SchemaError("findings が空です（指摘を 1 件以上）")
    for i, finding in enumerate(findings):
        where = f"findings[{i}]（{finding.get('reason_id')!r}）" \
            if isinstance(finding, dict) else f"findings[{i}]"
        if not isinstance(finding, dict):
            raise SchemaError(f"{where} は object")
        _require(finding, FINDING_KEYS, where)
        _require_choice(finding["check_method"], JUDGE_KIND,
                         f"{where} の check_method")
        _require_choice(finding["result"], FINDING_RESULT, f"{where} の result")
        entry = reason_entry(vocabulary, finding["reason_id"])
        if entry is None:
            # **既知の分類へ寄せない**（Codex §11・A05）。近い名前の理由に
            # 読み替えると、**別の判断が同じ箱に入る**——語彙が足りないことを
            # 道具が言えなくなる。
            usable = sorted(e["reason_id"] for e in vocabulary["entries"]
                            if e["state"] in REASON_USABLE)
            raise SchemaError(
                f"{where} の reason_id はこの語彙にありません。"
                f"**近い理由に読み替えません**——当てはまるものが無ければ "
                f"{OTHER_REASON!r} に説明を書いてください。"
                f"使えるのは: {'・'.join(usable)}")
        if entry["state"] not in REASON_USABLE:
            raise SchemaError(
                f"{where} の理由は state={entry['state']} です"
                f"（新しい指摘には使えません。過去の記録は元のラベルのまま"
                f"読めます）")
        if not isinstance(finding["note"], str):
            raise SchemaError(f"{where} の note は文字列")
        if "role_id" in finding and not isinstance(finding["role_id"], str):
            raise SchemaError(f"{where} の role_id は文字列（段の役割の名前）")
        if finding["reason_id"] == OTHER_REASON and not finding["note"].strip():
            # **語彙が足りないことを、道具が自分で言えるようにする**
            # （設計条件 2）。説明の無い `other` は、あとから語彙候補にできない。
            raise SchemaError(
                f"{where} は {OTHER_REASON!r} なので note が要ります"
                f"（**既存分類で説明できない指摘は、説明が残って初めて"
                f"次の語彙候補になります**）")
        refs = finding["evidence_refs"]
        if not isinstance(refs, list) or any(not isinstance(r, str) or not r.strip()
                                              for r in refs):
            raise SchemaError(f"{where} の evidence_refs は文字列の配列"
                               f"（無ければ空配列）")
        if known_ids is not None:
            unknown = [r for r in refs if r.startswith("sha256:")
                       and r not in known_ids]
            if unknown:
                raise SchemaError(
                    f"{where} の根拠 ID が実在しません: {unknown[:3]}")

    out = dict(row)
    out["schema_version"] = SCHEMA_VERSION
    out["review_id"] = content_id(out, exclude=("review_id",))
    return out


def review_applies_to(review: dict, draft_sha256: str) -> bool:
    """この検収記録が、**いまの原稿**に対するものか（Codex §7・A03）。

    検収で使った原稿から本文が変われば、旧結果は履歴。**新しい原稿への
    「確認済み」表示を引き継がない。**
    """
    return bool(draft_sha256) and review.get("draft_sha256") == draft_sha256


def review_support(review: dict, vocabulary: dict | None) -> dict:
    """その記録を**いまの語彙で読めるか**（Codex §11・A05）。

    **未知の語彙版を、既知の分類に推測変換しない。**「対応外」と言う。
    0 件・問題なしにも変換しない——**読めないことは、問題が無いことではない。**

    `retired` の理由が使われていても**読めないとは言わない**（過去の記録は
    元のラベルのまま残る・Codex §8）。印だけ付ける。
    """
    used = [f.get("reason_id") for f in review.get("findings") or []]
    if vocabulary is None:
        return {"status": "unsupported",
                "reason": (f"この記録の語彙版が手元にありません"
                           f"（{review.get('vocabulary_id')}）。"
                           f"**既知の分類に読み替えません**"),
                "unknown_reason_ids": [], "retired_reason_ids": []}
    unknown, retired = [], []
    for reason_id in used:
        entry = reason_entry(vocabulary, reason_id)
        if entry is None:
            unknown.append(reason_id)
        elif entry["state"] in ("retired", "rejected"):
            retired.append(reason_id)
    if unknown:
        return {"status": "unsupported",
                "reason": (f"語彙に無い理由が使われています: {sorted(set(unknown))}。"
                           f"**近い理由に読み替えません**"),
                "unknown_reason_ids": sorted(set(unknown)),
                "retired_reason_ids": sorted(set(retired))}
    return {"status": "supported", "reason": None, "unknown_reason_ids": [],
            "retired_reason_ids": sorted(set(retired))}


def tally_reasons(reviews: list) -> dict:
    """理由ごとの件数と、`other` の内訳（設計条件 2）。

    **`other` が増えたら語彙を疑う。** これが「この語彙は分けているか」の
    自動化——`forms` のときは 3 本とも同じ型に落ちたのに**道具が何も言わ
    なかった。**

    同じ記録を 2 回渡しても 1 件（`review_id` で数える・A04）。
    """
    seen, by_reason, others = set(), {}, []
    for review in reviews:
        review_id = review.get("review_id")
        if review_id in seen:
            continue
        seen.add(review_id)
        for finding in review.get("findings") or []:
            reason_id = finding.get("reason_id")
            by_reason[reason_id] = by_reason.get(reason_id, 0) + 1
            if reason_id == OTHER_REASON:
                others.append({"review_id": review_id,
                                "note": finding.get("note", "")})
    return {"reviews": len(seen),
            "findings": sum(by_reason.values()),
            "by_reason": dict(sorted(by_reason.items())),
            "other": {"count": by_reason.get(OTHER_REASON, 0), "notes": others}}


# 出力に載せる「渡すものの形」（規約 14・`ArticleEvidence` 等と同じ扱い）。
VOCABULARY_SHAPE = {
    "ReasonVocabulary": {
        "name": "この語彙の名前（例 修正理由）",
        "created_at": "ISO 8601・timezone 必須",
        "created_by": "作った人・セッション",
        "supersedes": "前の版の vocabulary_id。最初は null",
        "entries": [{
            "reason_id": "英小文字・数字・下線（例 unsupported_claim）",
            "display": "表示名",
            "definition": "定義（1〜2 文）",
            "includes": ["含む例（**accepted には 1 つ以上**）"],
            "excludes": ["**混同しないもの**（accepted には 1 つ以上）"],
            "scope": "適用範囲（例 すべての原稿／この account だけ）",
            "judged_by": list(JUDGE_KIND),
            "state": list(REASON_STATE),
            "meaning_version": "意味の版（整数）。**表記の変更では上げない。"
                                "意味が変わったら上げる**",
        }],
        "_注意": f"{OTHER_REASON!r} が使える状態で要ります（既存分類で説明"
                  f"できない指摘の行き先）",
    },
}

REVIEW_SHAPE = {
    "ReviewRecord": {
        "account": "どの account の原稿か",
        "draft_sha256": "**検収した原稿本文の SHA-256**（パスでは指しません）",
        "vocabulary_id": "どの版の語彙で判定したか",
        "context_id": "トピック判断の context_id（あれば・省略可）",
        "article_id": "記事証拠の article_id（あれば・省略可）",
        "profile_version": "参照した profile の版（あれば・省略可）",
        "section": "対象の段（省略可）",
        "target_quote": "対象の主張または原文抜粋（省略可）",
        "findings": [{
            "reason_id": "語彙にある理由 ID。**近い理由に読み替えません**",
            "check_method": list(JUDGE_KIND),
            "result": list(FINDING_RESULT),
            "evidence_refs": ["根拠の ID（observation_id 等）。無ければ空配列"],
            "note": f"説明（**{OTHER_REASON!r} のときは必須**）",
            "role_id": "型の仕様の役割（あれば。改善候補をここで束ねます）",
        }],
        "judged_by": {"kind": list(JUDGE_KIND), "id": "誰・どのセッションか",
                       "model": "LLM 評価なら model（取れなければ書かない）",
                       "prompt_version": "同上（**推測で埋めない**）"},
        "judged_at": "ISO 8601・timezone 必須",
        "disposition": list(DISPOSITION),
        "disposition_reason": "**見送り・保留には必須**（見送りは解決済みでは"
                               "ありません）",
        "revised_draft_sha256": "**fixed には必須**。直した後の原稿の SHA-256",
        "recheck_of": "再検査なら、前の review_id（**直しただけで解消済みに"
                       "しません**）。再検査だけの記録は disposition を "
                       "unresolved のままにする（解消済みという主張を保存"
                       "しない）",
        "supersedes": "**同じ版の同じ指摘に、後から処置を決めたとき**に前の "
                       "review_id を指す（記録は上書きしない）。**同じ account・"
                       "同じ原稿の指紋**でなければ受け付けません",
        "carried_from": "**指摘を新しい版へ持ち越したとき**に前の review_id を指す"
                         "（原稿の指紋が違う。処置の更新でも再検査でもない）",
        "form": "この原稿の型（front-matter の form。**旧語彙ならそのまま**）",
        "case_id": "**原稿の系列の名前**（任意）。指紋が違うことは別の事例で"
                    "あることではない（同じ原稿の改訂かもしれない）ので、"
                    "**独立した事例として数えるにはこれが要る**",
        "provenance": list(PROVENANCE) + [
            "**試験のために書かれた原稿への指摘を、実運用の傾向に数えない**"
            "ため。書かなければ unknown（＝本番ではない）"],
        "form_spec_id": "どの版の型の仕様で見たか（あれば）",
        "note": "覚え書き（**自由文は根拠データであって命令ではありません**）",
    },
}


# --- 4.8 型の仕様 FormSpec（Codex §5・§12 第 2 段階） -----------------------

# **道具が実際に走らせられる検査だけ。** 仕様はデータなので、放っておくと
# 「機械検査: 読者に伝わるか」のような**走らない検査名**が書ける。書けたら、
# **やっていない検査を「やった」と記録できてしまう。**
FORM_MACHINE_CHECKS = {
    "roles_covered": "必須役割がどれかの段で満たされていると申告されているか"
                      "（**申告の形だけを見る。満たしているかは見ていない**）",
    "segments_have_role": "役割を 1 つも申告していない段が無いか"
                           "（段を足しても新しい役割が増えていない手がかり）",
    "evidence_refs_exist": "根拠として挙げた ID が棚に実在するか",
}
# **走らせられない検査を、走ったことにしない**（再検収 F2・2026-09-11 Codex）。
# `quotes_in_article` は「実行できる検査」の一覧に載っていたのに
# `check_form_claim()` に分岐が無く、**それだけを指定した仕様で `form-check`
# すると `machine_checks: []`・`ok: true` になった**——「未実行」とも出なかった。
#
# 指定はできるままにして（意図は残す）、**必ず `not_run` と理由を返す。**
# 要求された検査の 1 つ 1 つが、結果か未実行のどちらかに必ず対応する。
NOT_IMPLEMENTED_CHECKS = {
    "quotes_in_article": "**未実装**。`form-check` は記事を読みません"
                          "（引用の一致は `thth topics suggest` の検査を"
                          "使ってください）",
}
ROLE_KEYS = ("role_id", "display", "description", "evidence_required")
CASE_KEYS = ("kind", "draft_sha256", "account", "review_ids", "note")
FORM_SPEC_KEYS = ("form", "scope", "applies_when", "roles", "unfit_examples",
                  "machine_checks", "semantic_questions", "success_measure",
                  "cases", "state", "meaning_version",
                  "created_at", "created_by", "supersedes")


def build_form_spec(row: dict) -> dict:
    """型を**テンプレートから検収可能な仕様へ**（Codex §5）。

    型は「この順に書く」という説明に加えて、**適用条件・段の役割・必要な根拠・
    不適合例・機械検査・意味評価・成功の評価**を持つ。

    **段数と役割数は同じではない**（§5）。ここでは段数を書かない——
    役割だけを書き、**1 段で複数の役割を満たしてよい**（`check_form_claim`）。
    水増しを仕様の側から誘わないため。

    **`accepted` に上げる条件を種類別に分ける**（§8・引継ぎ §6 で採用価値が高いと
    書いた項目）。編集上の型は **独立ケース 2 件以上と反例 1 件以上**が要る。
    ケースが 1 つの account に偏っているなら、**`scope` にその account を書く**
    ——「一分野だけならその範囲に限定する」。
    """
    _require(row, FORM_SPEC_KEYS, "型の仕様")
    from . import forms as forms_mod
    if row["form"] not in forms_mod.FORMS:
        raise SchemaError(
            f"知らない型です: {row['form']!r}。"
            f"使えるのは: {'・'.join(forms_mod.FORMS)}"
            + (f"（`{row['form']}` は旧語彙です。いまは "
               f"`{forms_mod.FORM_MIGRATION[row['form']]}`。"
               f"**ただし機械的な読み替えはしません**——"
               f"どの型に移すかは原稿の判断です）"
               if row["form"] in forms_mod.FORM_MIGRATION else ""))
    _require_choice(row["state"], REASON_STATE, "state")
    _require_iso(row["created_at"], "created_at")
    if not row.get("created_by"):
        raise SchemaError("created_by が要ります（誰が書いたか）")
    if not isinstance(row["meaning_version"], int) or row["meaning_version"] < 1:
        raise SchemaError("meaning_version は 1 以上の整数")
    for key in ("scope", "success_measure"):
        if not isinstance(row[key], str) or not row[key].strip():
            raise SchemaError(f"{key} が空です")
    for key in ("applies_when", "unfit_examples", "semantic_questions"):
        value = row[key]
        if not isinstance(value, list) or not [x for x in value
                                                if isinstance(x, str) and x.strip()]:
            raise SchemaError(
                f"{key} が空です（**適用条件・不適合例・意味評価は型の本体**——"
                f"「この順に書く」だけならテンプレートのままです）")

    roles = row["roles"]
    if not isinstance(roles, list) or not roles:
        raise SchemaError("roles が空です（必須役割を 1 つ以上）")
    seen = set()
    for i, role in enumerate(roles):
        where = f"roles[{i}]（{role.get('role_id') if isinstance(role, dict) else '?'}）"
        if not isinstance(role, dict):
            raise SchemaError(f"{where} は object")
        _require(role, ROLE_KEYS, where)
        if not isinstance(role["role_id"], str) or not role["role_id"].strip():
            raise SchemaError(f"{where} の role_id が空です")
        if role["role_id"] in seen:
            raise SchemaError(f"{where} の role_id が重複しています")
        seen.add(role["role_id"])
        for key in ("display", "description"):
            if not isinstance(role[key], str) or not role[key].strip():
                raise SchemaError(f"{where} の {key} が空です")
        if not isinstance(role["evidence_required"], list) \
                or any(not isinstance(x, str) for x in role["evidence_required"]):
            raise SchemaError(
                f"{where} の evidence_required は文字列の配列"
                f"（根拠が要らない役割なら空配列）")

    checks = row["machine_checks"]
    if not isinstance(checks, list):
        raise SchemaError("machine_checks は配列")
    unknown = [c for c in checks
               if c not in FORM_MACHINE_CHECKS
               and c not in NOT_IMPLEMENTED_CHECKS]
    if unknown:
        # **走らない検査名を書かせない。** 書けると「やっていない検査を
        # やったことにできる」——意味評価を機械検査の欄に置くのが典型。
        raise SchemaError(
            f"走らせられない機械検査です: {unknown}。"
            f"**意味の評価は semantic_questions に書いてください。**"
            f"機械検査に書けるのは: {'・'.join(sorted(FORM_MACHINE_CHECKS))}")

    cases = row["cases"]
    if not isinstance(cases, list):
        raise SchemaError("cases は配列（正例・反例）")
    for i, case in enumerate(cases):
        where = f"cases[{i}]"
        if not isinstance(case, dict):
            raise SchemaError(f"{where} は object")
        _require(case, CASE_KEYS, where)
        _require_choice(case["kind"], CASE_KINDS, f"{where} の kind")
        if not isinstance(case["draft_sha256"], str) \
                or not _SHA256_HEX_RE.match(case["draft_sha256"] or ""):
            raise SchemaError(f"{where} の draft_sha256 は 64 桁の 16 進数")
        if not isinstance(case["account"], str) or not case["account"].strip():
            raise SchemaError(f"{where} の account が空です")
        if not isinstance(case["review_ids"], list):
            raise SchemaError(f"{where} の review_ids は配列（無ければ空配列）")

    if row["state"] == "accepted":
        _require_promotion(row, cases)

    out = dict(row)
    out["schema_version"] = SCHEMA_VERSION
    out["form_spec_id"] = content_id(out, exclude=("form_spec_id",))
    return out


def _require_promotion(row: dict, cases: list) -> None:
    """**昇格条件を種類別に分ける**（Codex §8）。ここは「編集上の型」の側。

    論理的不変条件なら反例 1 件で直せるが、**型と意味評価は独立ケースと反例が
    要る。** 型の作成者の自己採点だけで採用しない。
    """
    positives = [c for c in cases if c["kind"] == "positive"]
    counters = [c for c in cases if c["kind"] == "counter"]
    independent = {c["draft_sha256"] for c in positives}
    if len(independent) < 2:
        raise SchemaError(
            f"state=accepted には**独立した正例が 2 件以上**要ります"
            f"（いま {len(independent)} 件）。同じ原稿を 2 回数えません。"
            f"揃わないうちは proposed か shadow のままにしてください")
    if not counters:
        raise SchemaError(
            "state=accepted には**反例が 1 件以上**要ります"
            "（この型に当てはまらない原稿）。**反例が無い仕様は、"
            "何を除いているかを言っていません**")
    accounts = {c["account"] for c in positives}
    if len(accounts) == 1:
        only = next(iter(accounts))
        if only not in row["scope"]:
            # **「一分野だけならその範囲に限定する」**（§8）。広い共通仕様と
            # 呼ぶには、異なる文脈で確かめる。
            raise SchemaError(
                f"正例が {only} だけです。scope にその範囲を書くか"
                f"（例「{only} の原稿」）、別の account の正例を足してください。"
                f"**一分野だけの確認を共通仕様と呼ばない**")


def check_form_claim(spec: dict, claim: dict, *, known_ids: set | None = None) -> dict:
    """段の役割の申告を**機械で見られる範囲だけ**検査する（Codex §5・§7）。

    **THTH は「その段が本当にその役割を果たしているか」を見ていない。**
    見ているのは、必須役割が申告されているか・知らない役割名が無いか・役割を
    1 つも持たない段が無いか・根拠の ID が実在するか。**意味評価は別の欄**に
    質問として並べ、`not_evaluated` のまま返す（§7: 未評価と問題なしは別）。

    **段数と役割数は同じでなくてよい**——1 段で複数の役割を満たしてよい。
    """
    segments = claim.get("segments")
    if not isinstance(segments, list) or not segments:
        raise SchemaError("segments が空です（段ごとの役割の申告）")
    required = [r["role_id"] for r in spec["roles"]]
    claimed = {}
    empty_segments = []
    unknown_roles = []
    for i, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise SchemaError(f"segments[{i}] は object")
        roles = segment.get("roles")
        if not isinstance(roles, list):
            raise SchemaError(f"segments[{i}] の roles は配列")
        label = segment.get("index") or f"segments[{i}]"
        if not roles:
            empty_segments.append(label)
        for role_id in roles:
            if role_id not in required:
                unknown_roles.append({"segment": label, "role_id": role_id})
            claimed.setdefault(role_id, []).append(label)

    evidence = claim.get("evidence") or {}
    if not isinstance(evidence, dict):
        raise SchemaError("evidence は object（役割 ID → 根拠 ID の配列）")
    # **根拠でない値を「実在確認・問題なし」にしない**（再検収 F3）。
    # 以前は配列かどうかも中身の形も見ていなかったので、`[123]` を入れると
    # **「空ではない」ので不足を回避し、文字列でないので未知 ID にもならず、
    # `no_problem` になった。**
    missing_evidence, unknown_refs, malformed, unverified = [], [], [], []
    for role in spec["roles"]:
        role_id = role["role_id"]
        needs = [x for x in role["evidence_required"] if x.strip()]
        refs = evidence.get(role_id)
        if refs is None:
            refs = []
        elif not isinstance(refs, list):
            malformed.append({"role_id": role_id,
                               "problem": "配列ではありません",
                               "value": repr(refs)[:60]})
            refs = []
        local, other = [], []
        for ref in refs:
            if not isinstance(ref, str) or not ref.strip():
                malformed.append({"role_id": role_id,
                                   "problem": "文字列ではありません",
                                   "value": repr(ref)[:60]})
            elif _REF_ID_RE.match(ref):
                local.append(ref)
            else:
                # **ID でない根拠は、実在を確かめていない**——「確かめた」側に
                # 数えない（別種の根拠を禁止はしないが、混ぜない）。
                other.append(ref)
        if needs and not local and not other:
            missing_evidence.append({"role_id": role_id, "needs": needs})
        if other:
            unverified.append({"role_id": role_id, "refs": other,
                                "note": "棚の ID ではないので実在を確かめて"
                                        "いません（sha256: 付きの ID だけ照合"
                                        "します）"})
        if known_ids is not None:
            unknown_refs += [{"role_id": role_id, "ref": r} for r in local
                             if r not in known_ids]

    machine = []
    if "roles_covered" in spec["machine_checks"]:
        missing = [r for r in required if r not in claimed]
        machine.append({"check": "roles_covered",
                         "result": "problem" if missing else "no_problem",
                         "missing_roles": missing,
                         "unknown_roles": unknown_roles})
    if "segments_have_role" in spec["machine_checks"]:
        machine.append({"check": "segments_have_role",
                         "result": "problem" if empty_segments else "no_problem",
                         "segments_without_role": empty_segments})
    if "evidence_refs_exist" in spec["machine_checks"]:
        if missing_evidence or unknown_refs or malformed:
            result = "problem"
        elif unverified:
            # **確かめていないものを「問題なし」にしない**（§7）。
            result = "not_evaluated"
        else:
            result = "no_problem"
        machine.append({"check": "evidence_refs_exist", "result": result,
                         "missing_evidence": missing_evidence,
                         "unknown_refs": unknown_refs,
                         "malformed_evidence": malformed,
                         "unverified_refs": unverified})
    # **要求された検査は、必ず「結果」か「未実行と理由」のどちらかに対応させる**
    # （再検収 F2）。黙って消えるのがいちばん悪い。
    ran = {c["check"] for c in machine}
    for check in spec["machine_checks"]:
        if check in ran:
            continue
        machine.append({
            "check": check, "result": "not_run",
            "reason": NOT_IMPLEMENTED_CHECKS.get(
                check, FORM_MACHINE_CHECKS.get(
                    check, "この版では走らせられません"))})
    return {
        "form": spec["form"], "form_spec_id": spec.get("form_spec_id"),
        "machine_checks": machine,
        # **機械が見ていないものを、見ていないと書く。**
        "semantic_evaluations": [
            {"question": q, "result": "not_evaluated"}
            for q in spec["semantic_questions"]],
        "roles_claimed_by": claimed,
        "notice": "**役割を満たしていると申告されたこと**しか見ていません。"
                   "本当に満たしているかは意味評価の側です。",
    }


FORM_SPEC_SHAPE = {
    "FormSpec": {
        "form": "型の名前（いまの語彙のどれか）",
        "scope": "適用範囲（例「すべての原稿」／「kopicha-threads の原稿」）",
        "applies_when": ["**適用条件**（例「比較対象が明示されている」）"],
        "roles": [{
            "role_id": "役割の名前（段ではない）",
            "display": "表示名",
            "description": "その役割が何を渡すか",
            "evidence_required": ["**必要な根拠**（例「比較項目ごとの原資料参照」）"
                                   "。要らない役割は空配列"],
        }],
        "unfit_examples": ["**不適合例**（例「A だけ実測・B は推測なのに同列に"
                            "並べる」）"],
        "machine_checks": list(FORM_MACHINE_CHECKS)
                           + [f"{c}（未実行と返ります）"
                              for c in NOT_IMPLEMENTED_CHECKS],
        "semantic_questions": ["**意味評価の問い**（例「本当に比較可能か」）。"
                                "**機械検査に混ぜない**"],
        "success_measure": "何が良くなれば効いたと言えるか（例「検収者が指摘した"
                            "条件漏れの減少」）",
        "cases": [{"kind": list(CASE_KINDS), "draft_sha256": "原稿の指紋",
                    "account": "どの account の原稿か",
                    "review_ids": ["根拠になる検収記録"], "note": "何の例か"}],
        "state": list(REASON_STATE),
        "meaning_version": "意味の版（整数）",
        "created_at": "ISO 8601・timezone 必須",
        "created_by": "書いた人・セッション",
        "supersedes": "前の版の form_spec_id。最初は null",
        "_注意": "**accepted には独立した正例 2 件と反例 1 件**が要ります"
                  "（§8・編集上の型）。正例が 1 つの account に偏るなら scope を"
                  "その範囲に限定してください",
    },
}


# 独立ケースの下限（Codex §8「編集上の型は複数の独立ケースと反例が要る」）。
# **1 件は候補にしない。** 1 件で仕様を動かすと、その 1 本の事情が共通仕様になる。
MIN_INDEPENDENT_CASES = 2


def draft_series(reviews: list) -> dict:
    """原稿の**系列**を作る（再検収 F6・2026-09-11 Codex）。

    > 一つの原稿の修正前後を、独立した事例 2 件と数える。

    指紋が違うことは、別の事例であることではない。**改訂（`fixed` の改訂先）・
    再検査・持ち越しの線でつながっているものは同じ系列。** 線が無ければ
    つながりは分からないので、**分からないまま別の版として置く**
    （「独立」と確定しない）。

    返すのは `{draft_sha256: 系列の代表}`。
    """
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        if not a or not b:
            return
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    by_id = {r.get("review_id"): r for r in reviews}
    for review in reviews:
        draft = review.get("draft_sha256")
        find(draft) if draft else None
        union(draft, review.get("revised_draft_sha256"))
        for key in ("recheck_of", "supersedes", "carried_from"):
            target = by_id.get(review.get(key))
            if target is not None:
                union(draft, target.get("draft_sha256"))
                union(draft, target.get("revised_draft_sha256"))
    return {draft: find(draft) for draft in parent}


def _meaning_fingerprint(entry: dict) -> str:
    """理由の**意味そのもの**から決まる指紋（再判定 R1・2026-09-11 Codex）。

    > 番号が同じでも意味の同一性は証明されていない。

    版の番号は語彙ごとに独立して採番されるうえ、**同じ系統で番号を据え置いた
    まま定義を書き換える**こともできる。だから番号でも系統でもなく、
    **定義・含む例・含まない例そのもの**から指紋を作って束ねる。
    文言が変われば別として扱う（**取りこぼすより、混ぜないほうを選ぶ**）。
    """
    return content_id({
        "reason_id": entry.get("reason_id"),
        "meaning_version": entry.get("meaning_version"),
        "definition": entry.get("definition"),
        "includes": entry.get("includes"),
        "excludes": entry.get("excludes"),
    })


def _role_fingerprint(spec: dict | None, role_id) -> str | None:
    """役割の意味から決まる指紋。**型側も版の番号だけで同一視しない**（R1）。"""
    if spec is None or role_id is None:
        return None
    for role in spec.get("roles") or []:
        if role.get("role_id") == role_id:
            return content_id({"role_id": role_id,
                                "description": role.get("description"),
                                "evidence_required": role.get("evidence_required")})
    return None


def _case_id_clusters(series_case_ids: dict,
                       production_series: set | None = None) -> list:
    """同じ `case_id` を名乗る非接続系列を 1 クラスタに畳む（再判定 M2 対応・

    再々判定・2026-09-12 Codex／再々々判定 R2・2026-09-12 Codex）。

    > 同じ account・同じ `case_id=same-case` の非接続 2 hash を production
    > として登録すると、`reported_case_ids=['same-case']` のまま
    > `independent_cases=2` となり候補が成立した。

    N4 は「同じ系列に別々の `case_id`」を系列 1 件に畳んだ。M2 はその
    逆向き——「別々の系列に同じ `case_id`」も畳んだ。`draft_series()` の線
    (`recheck_of`・`supersedes`・`carried_from`) が無くても、**同じ ID を
    名乗っている**こと自体を独立性への制約として扱う。account をまたいで
    同じ文字列が出てきても畳む側に倒す（`case_id` の名前空間がどこまで
    一意かは記録からは分からないので、疑わしいものを多く数えない）。

    **R2 で直した順序の誤り。** M2 の呼び出し側は実運用限定の
    `series_case_ids` だけを渡して結合していたため、**trial の ID が
    非接続の production 2 系列を橋渡ししているケースを見落としていた**——
    production 系列 A が `case-a`、同系列の trial 再検査が `shared-case`、
    非接続の production 系列 B も `shared-case` を名乗ると、trial の記録は
    結合の材料に入らないので A・B は非接続のまま `independent_cases=2` に
    なっていた。trial の ID は「同系列内の競合表示」には使われていたが、
    「系列横断の結合と競合」には使われていなかった。

    直したのは**結合と計数の順番**。**同一性・衝突の結合は provenance を
    問わずすべての申告（trial・unknown も含む）で先に作り**、そのあとで
    **production の根拠を持つ成分だけを母数に数える**（受入条件どおり）。
    trial 自体を正例として件数に加算する変更ではない——`production_series`
    に属する `series_rep` を 1 つも含まない成分は、trial の ID がどれだけ
    橋渡ししていても母数からは外れたままになる。

    引数:
      series_case_ids: `series_rep -> {case_id, ...}`。**結合には
        provenance を問わずすべての申告を渡すこと**（呼び出し側は
        `series_case_ids_all` を渡す）。
      production_series: 実運用の根拠を持つ `series_rep` の集合。渡すと、
        返すクラスタはこの集合と 1 つ以上重なるものだけに絞る（＝
        production の根拠を持つ成分だけを母数に数える）。省略時は
        絞り込まず全クラスタを返す（結合結果だけを見たい呼び出し側用）。

    返り値は `series_rep` の集合のリスト（＝独立事例として数える単位）。
    """
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    case_id_to_series = {}
    for series_rep, case_ids in series_case_ids.items():
        find(series_rep)
        for case_id in case_ids:
            case_id_to_series.setdefault(case_id, set()).add(series_rep)
    for reps in case_id_to_series.values():
        reps = sorted(reps)
        for other in reps[1:]:
            union(reps[0], other)

    clusters = {}
    for series_rep in series_case_ids:
        clusters.setdefault(find(series_rep), set()).add(series_rep)
    members_list = list(clusters.values())
    if production_series is None:
        return members_list
    # **production の根拠を持つ成分だけを母数へ**。trial だけで作られた
    # 成分（`production_series` と重ならない）は、橋渡しの材料にはなっても
    # 件数には数えない。
    return [members for members in members_list
            if members & production_series]


def improvement_candidates(reviews: list, *, spec: dict,
                            vocabularies: dict | None = None,
                            form_specs: dict | None = None,
                            lineage: dict | None = None) -> dict:
    """検収履歴から**改善案を出すところまで**（構想書 §12 第 2 段階）。

    **ここで仕様も語彙も profile も書き換えない。** 出すのは候補と、その根拠に
    なった検収記録の ID だけ。採否は §8 の手順（shadow・独立確認・masaru）。

    **数え方は 3 度直っている。**

    - **F5/F6（再検収）**: 語彙を解決せず文字列だけで束ねていた／1 本の原稿の
      修正前後を独立 2 件と数えていた。
    - **R1〜R5（再判定・2026-09-11 Codex）**:
      **番号が同じでも意味が同じとは限らない**ので定義そのものの指紋で束ねる／
      **指定された型の仕様が読めない**のを「元から未指定」と同じ None に
      落としていたので分ける／**試作が実運用の候補成立に加算されていた**ので
      閾値は実運用だけで数える／**線の無い版を独立 2 件と判定していた**ので、
      独立は `case_id` が明示されたときだけにする。
    - **N3/N4（再々判定・2026-09-12 Codex）**: 解決できることと解決先が
      その型であることは別なので照合する／**既知の改訂・再検査系列に別々の
      `case_id` が申告されても、系列 1 件としてしか数えない**（`case_id` の
      申告より繋がっている線のほうが強い証拠）。
    - **M2（再々判定・2026-09-12 Codex）**: N4 の逆向き。**別々の非接続系列が
      同じ `case_id` を名乗っても、独立事例には 1 件としてしか数えない**
      （`_case_id_clusters()`）。account をまたいでも畳む側に倒す——
      `case_id` の名前空間がどこまで一意かは記録から分からないので、疑わしい
      ものを多く数えない。畳んでも申告そのものは消さず、両向きの競合を
      `case_id_conflicts` に残す。
    """
    vocabularies = vocabularies or {}
    form_specs = form_specs or {}
    known_roles = {r["role_id"] for r in spec["roles"]}
    series = draft_series(reviews)
    mine = [r for r in reviews if r.get("form") == spec["form"]]

    groups, unresolved = {}, []
    for review in mine:
        vocabulary = vocabularies.get(review.get("vocabulary_id"))
        if vocabulary is None:
            unresolved.append({"review_id": review.get("review_id"),
                                "reason": "この記録の語彙を解決できません"
                                           f"（{review.get('vocabulary_id')}）"})
            continue
        spec_ref = review.get("form_spec_id")
        resolved_spec = None
        if spec_ref:
            resolved_spec = form_specs.get(spec_ref)
            if resolved_spec is None:
                # **「指定があるのに読めない」を「元から未指定」と同じにしない**
                # （R2）。読めないものを候補の母数に入れない。
                unresolved.append({
                    "review_id": review.get("review_id"),
                    "reason": f"指定された型の仕様を解決できません（{spec_ref}）"})
                continue
            # **解決できることと、指した先がこの型であることは別**（再々判定
            # N3・2026-09-12 Codex）。比較型のレビューに列挙型など別の型の
            # `form_spec_id` を付けても、それが読めてしまえば根拠として通って
            # いた——解決可能性だけを見て、解決先の `form` とレビューの
            # `form` を照合していなかった。ここで照合し、食い違うなら
            # 母数から外す（既存履歴もこの分岐を通るので安全に読める）。
            resolved_form = resolved_spec.get("form")
            if resolved_form != review.get("form"):
                unresolved.append({
                    "review_id": review.get("review_id"),
                    "reason": f"指定された型の仕様は別の型です（{spec_ref} は "
                               f"{resolved_form!r}・このレビューは "
                               f"{review.get('form')!r}）"})
                continue
        for finding in review.get("findings") or []:
            if finding.get("result") not in ("problem", "suspected"):
                continue
            reason_id = finding.get("reason_id")
            entry = reason_entry(vocabulary, reason_id)
            if entry is None:
                unresolved.append({
                    "review_id": review.get("review_id"),
                    "reason": f"理由 {reason_id!r} がこの記録の語彙にありません"})
                continue
            role_id = finding.get("role_id")
            key = (reason_id, _meaning_fingerprint(entry), role_id,
                   _role_fingerprint(resolved_spec, role_id))
            row = groups.setdefault(key, {
                "series": set(), "drafts": set(), "review_ids": [], "notes": [],
                "provenance": {}, "case_ids": set(), "unlinked": set(),
                "lineage": set(), "series_case_ids": {},
                # **表示用。** `series_case_ids` は実運用だけ（独立事例の
                # 母数）だが、こちらは provenance を問わず集める——trial の
                # `case_id` も競合表示からは消さない（M2）。
                "series_case_ids_all": {}, "case_id_accounts": {}})
            draft = review.get("draft_sha256")
            source = review.get("provenance") or "unknown"
            series_rep = series.get(draft, draft)
            row["series"].add(series_rep)
            row["drafts"].add(draft)
            row["review_ids"].append(review["review_id"])
            row["provenance"][source] = row["provenance"].get(source, 0) + 1
            row["lineage"].add((lineage or {}).get(review.get("vocabulary_id"),
                                                    review.get("vocabulary_id")))
            if review.get("case_id"):
                # **provenance を問わず先に集める。** trial 側の申告も、
                # 同じ系列に付いていれば競合として見える必要がある
                # （M2・「同じ ID の非接続系列」を畳む条件・2026-09-12
                # Codex）。閾値には数えない（下の production 分岐だけ）。
                row["series_case_ids_all"].setdefault(
                    series_rep, set()).add(review["case_id"])
                row["case_id_accounts"].setdefault(
                    review["case_id"], set()).add(review.get("account"))
            if source == "production":
                # **閾値は実運用だけで数える**（R4）。試作は消さずに別枠。
                if review.get("case_id"):
                    row["case_ids"].add(review["case_id"])
                    # **`case_id` の申告より、既知の改訂・再検査系列のほうが
                    # 強い証拠**（再々判定 N4・2026-09-12 Codex）。
                    # `draft_series()` が `recheck_of`・`supersedes`・
                    # `carried_from` から同一系列を計算済みなので、それを
                    # 使って束ねる。同じ系列に別々の `case_id` が申告されて
                    # いても、**独立事例には系列 1 件としてしか数えない**
                    # ——競合は消さず `case_id_conflicts` に出す。
                    row["series_case_ids"].setdefault(
                        series_rep, set()).add(review["case_id"])
                else:
                    row["unlinked"].add(series_rep)
            if finding.get("note"):
                row["notes"].append(finding["note"])

    candidates, not_yet = [], []
    for (reason_id, meaning, role_id, role_fp), row in sorted(
            groups.items(),
            key=lambda kv: (-len(_case_id_clusters(
                                 kv[1]["series_case_ids_all"],
                                 set(kv[1]["series_case_ids"]))),
                             str(kv[0][0]), str(kv[0][2]))):
        # **独立事例は「申告された `case_id` の数」ではなく「`case_id` で
        # 畳んだあとの系列クラスタの数」**（N4・M2）。同じ系列（既知の
        # 改訂・再検査でつながっている）に複数の `case_id` が付いていても
        # 系列としては 1 件（N4）。**逆に、繋がっていない系列でも同じ
        # `case_id` を名乗っていれば 1 クラスタに畳む**（M2・
        # `_case_id_clusters()`）。矛盾する申告はどちらも消さず、下で
        # 両向きに `case_id_conflicts` へ出す。
        #
        # **R2: 結合には `series_case_ids_all`（provenance を問わない）を
        # 使う。** trial の `case_id` が非接続の production 2 系列を橋渡し
        # していることがあり（同系列内の再検査が別系列と同じ ID を名乗る）、
        # 実運用だけで結合すると見落とす。結合はすべての申告で行い、
        # `production_series`（=`series_case_ids` のキー）と重なる成分だけを
        # 母数に数える——trial 単独の成分は依然として数えない。
        clusters = _case_id_clusters(row["series_case_ids_all"],
                                      set(row["series_case_ids"]))
        confirmed = len(clusters)
        # **線が無い版**。ただし、その系列がすでに別の版で `case_id` を
        # 名乗っている（provenance を問わない）なら、「線の無い版」の集計
        # から外す——系列としての身元はもう分かっている（N4/M2 反例・
        # 「一部の版だけ `case_id` がある既知系列」・2026-09-12 Codex）。
        unlinked = row["unlinked"] - set(row["series_case_ids_all"])
        # (a) 同じ系列に複数の `case_id`（trial の申告も含め、消さない）。
        same_series_conflicts = [
            {"series": series_rep, "case_ids": sorted(ids)}
            for series_rep, ids in sorted(row["series_case_ids_all"].items())
            if len(ids) > 1]
        # (b) 同じ `case_id` を複数の非接続系列が名乗っている（M2）。
        # 独立事例には 1 件にしか数えないが、なぜ畳んだかを消さず出す。
        # `case_id` は account をまたぐと曖昧なので、どの account が
        # 名乗ったかも添える。
        # **R2: ここも `series_case_ids_all`（provenance を問わない）から
        # 作る。** trial 経由で 2 つの production 系列がつながる場合も、
        # その橋渡しが競合表示に出る必要がある（「trial は同系列内の競合
        # 表示には使うが、系列横断の結合と競合には使っていない」という
        # 外部レビューの指摘そのもの）。母数（`confirmed`）には数えなくても、
        # 表示からは消さない。
        case_id_series = {}
        for series_rep, ids in row["series_case_ids_all"].items():
            for case_id in ids:
                case_id_series.setdefault(case_id, set()).add(series_rep)
        cross_series_conflicts = [
            {"case_id": case_id, "series": sorted(reps),
             "accounts": sorted(
                 a for a in row["case_id_accounts"].get(case_id, ()) if a)}
            for case_id, reps in sorted(case_id_series.items())
            if len(reps) > 1]
        conflicts = same_series_conflicts + cross_series_conflicts
        entry = {
            "reason_id": reason_id, "meaning": meaning,
            "vocabulary_lineage": sorted(row["lineage"]),
            "role_id": role_id, "role_meaning": role_fp,
            # **`case_id` で畳んだあとのクラスタ数だけが「独立事例」。**
            "independent_cases": confirmed,
            # **「確認済み」ではない。** ここにある `case_id` はすべて
            # 記録者の自己申告であって、実測で確認したものではない
            # （表示の条件・2026-09-12 Codex）。名前もそれが分かるように
            # `confirmed_case_ids` から変えた。
            "reported_case_ids": sorted(row["case_ids"]),
            # **両向きの競合。** 独立事例には加算していないが、申告そのもの
            # は消さずここに出す。
            "case_id_conflicts": conflicts,
            # **線が無い版**。数えはするが、閾値の証拠にはしない（R5）。
            "unlinked_versions": len(unlinked),
            "draft_versions": len(row["drafts"]),
            "review_ids": sorted(row["review_ids"]),
            "notes": row["notes"],
            "provenance": dict(sorted(row["provenance"].items())),
            "role_known": role_id in known_roles if role_id else None,
        }
        if not row["provenance"].get("production"):
            entry["representativeness"] = (
                "**根拠に実運用の検収が 1 件もありません**"
                f"（{dict(sorted(row['provenance'].items()))}）。"
                "試験のために書かれた原稿の癖を、全体の傾向として読まないこと")
        elif len(row["provenance"]) > 1:
            entry["representativeness"] = (
                f"実運用以外の記録が混ざっています（{entry['provenance']}）。"
                "**閾値には実運用だけを数えています。**")
        if role_id and role_id not in known_roles:
            entry["suggestion"] = (
                f"仕様に無い役割 {role_id!r} に指摘が付いています。"
                f"**役割の名前が合っていないか、仕様に足りない役割があります。**")
        elif role_id:
            entry["suggestion"] = (
                f"役割 {role_id!r} に {reason_id} が繰り返しています。"
                f"**その役割の `evidence_required` か `applies_when` が"
                f"足りていない可能性。** 説明（notes）を読んで、"
                f"何を要求すれば防げたかを書いてください")
        elif reason_id == OTHER_REASON:
            entry["suggestion"] = (
                "既存分類で説明できない指摘が繰り返しています。"
                "**語彙の候補。** ただし説明を読んで、**同じ形かどうかを"
                "確かめてから**束ねてください（違うものを 1 つにすると、"
                "また別のものが混ざります）")
        else:
            entry["suggestion"] = (
                f"{reason_id} が繰り返しています。役割が記録されていないので、"
                f"**どこを直せば防げるかがこの履歴からは決まりません**"
                f"（検収に `role_id` を付けると束ねられます）")
        if confirmed < MIN_INDEPENDENT_CASES:
            entry["why_not_yet"] = (
                f"**独立した事例が {confirmed} 件です**"
                f"（実運用で `case_id` を名乗った系列を、同じ `case_id` で"
                f"畳んだあとのクラスタ数）。"
                + (f"ほかに線の無い版が {len(unlinked)} 件ありますが、"
                   f"**別の原稿なのか同じ原稿の改訂なのかを記録から言えない**ので、"
                   f"独立とは数えていません（`case_id` を付けてください）"
                   if unlinked else "")
                + ("／試作・出自不明の記録は閾値に数えません"
                   if set(row["provenance"]) - {"production"} else "")
                + ("／同じ系列に別々の `case_id` が申告されていて、"
                   "**競合したまま系列 1 件としてしか数えていません**"
                   "（`case_id_conflicts` 参照）"
                   if same_series_conflicts else "")
                + ("／同じ `case_id` を非接続の系列が名乗っていて、"
                   "**別の原稿かもしれないまま 1 件に畳んでいます**"
                   "（`case_id_conflicts` 参照）"
                   if cross_series_conflicts else ""))
        (candidates if confirmed >= MIN_INDEPENDENT_CASES
         else not_yet).append(entry)

    return {
        "form": spec["form"], "form_spec_id": spec.get("form_spec_id"),
        "state": spec.get("state"),
        "candidates": candidates,
        "not_enough_cases": not_yet,
        "unresolved_records": unresolved,
        "minimum_independent_cases": MIN_INDEPENDENT_CASES,
        "provenance": provenance_tally(mine),
        "notice": "**候補です。** 仕様も語彙も profile も書き換えていません。"
                   "採否は独立確認のあと（構想書 §8）。**閾値は実運用の記録だけ**で"
                   "数え、**独立は `case_id` が明示されたときだけ**数えます。"
                   "**`case_id`・`provenance`（実運用かどうか）・独立性は、"
                   "いずれも記録者の自己申告であって、実測で確認したもの"
                   "ではありません（未検証）。** 候補表示はあくまで"
                   "**申告に基づく探索候補**として扱ってください。",
    }



def provenance_tally(reviews: list) -> dict:
    """**出自の内訳**（運用セッションの申し出 2026-09-11）。

    区別する手段が無いと、**次に実運用の検収が入った瞬間に、試作と混ざって
    見分けがつかなくなる。** 印が付く前の記録は `unknown`——
    **`unknown` を「本番」と読まない。**

    （2026-09-11: `improvement_candidates()` を書き直したときに、この関数ごと
    消してしまっていた。呼ぶ側が別モジュールから当て木をしていたのを、
    元に戻した。**当て木は消す。**）
    """
    out = {}
    for review in reviews:
        source = review.get("provenance") or "unknown"
        out[source] = out.get(source, 0) + 1
    return dict(sorted(out.items()))


def withdrawn_evidence(review: dict, retracted_ids) -> list:
    """**取り下げられた観測を根拠にしている指摘**（Codex §11・A12）。

    > 観測・根拠が撤回されたら、依存する候補を**再確認対象にする**。
    > 過去にそれを根拠として採用した事実は消さない。

    **記録は書き換えない。** 読むときに印を付けるだけ——だから
    「取り下げられた観測に基づいて判定した」という事実そのものは残る。
    """
    taken = set(retracted_ids or ())
    out = []
    for finding in review.get("findings") or []:
        hit = sorted(set(finding.get("evidence_refs") or []) & taken)
        if hit:
            out.append({"reason_id": finding.get("reason_id"),
                         "withdrawn_refs": hit})
    return out


def vocabulary_impact(candidate: dict, reviews: list, *,
                       current: dict | None = None) -> dict:
    """新しい語彙を**現行版を維持したまま**既存の記録へ当てる（Codex §8 手順 2・3）。

    > **影響範囲**：どの判断が変わり、何を変えないか、過去記録への影響を明記する。
    > **shadow 検証**：現行版を維持したまま、新版を別結果として既存ケースへ適用する。

    **ここでは何も保存しない。** 出すのは「この版に替えたら、どの記録が読めなく
    なるか・どの理由が一度も使われていないか」。

    **読めなくなる記録を「0 件」や「問題なし」に変換しない**（A05 と同じ線）。
    名前で出す。
    """
    usable = {e["reason_id"] for e in candidate.get("entries") or []
              if e.get("state") in REASON_USABLE}
    known = {e["reason_id"]: e for e in candidate.get("entries") or []}
    before = {e["reason_id"] for e in (current or {}).get("entries") or []}

    breaks, used = [], {}
    for review in reviews:
        lost = []
        for finding in review.get("findings") or []:
            reason_id = finding.get("reason_id")
            used[reason_id] = used.get(reason_id, 0) + 1
            if reason_id not in known:
                lost.append(reason_id)
        if lost:
            # **過去のラベルを読み出せなくしない**（Codex §11）。替えるなら、
            # どの記録が読めなくなるかを先に言う。
            breaks.append({"review_id": review.get("review_id"),
                            "account": review.get("account"),
                            "lost_reason_ids": sorted(set(lost))})

    return {
        "reviews_examined": len(reviews),
        "records_that_stop_reading": breaks,
        "reasons_added": sorted(usable - before) if current else sorted(usable),
        "reasons_removed": sorted(before - set(known)) if current else [],
        "reasons_never_used": sorted(r for r in usable if not used.get(r)),
        "reasons_in_use": dict(sorted(used.items())),
        "notice": "**当てただけです。** 現行版は替えていません（保存もしていま"
                   "せん）。替えるかどうかは独立確認のあと（Codex §8）。",
    }


# --- 4.9 仮説（masaru 指示 2026-09-11・外部調査の §9 を型にしたもの） ---------

# **うちの台帳に実際にある指標だけ**。予測をこの語彙で書けないものは、
# 「いまのうちでは検証できない」（下の `UNIDENTIFIABLE`）として残す。
# 「滞在時間」「親密度」のような**持っていない量で予測を書かせない。**
# **平らな名前一覧をやめ、観測単位を持たせた**（運用指摘 2026-09-12）。
# 元は 8 つの名前を並べただけだったので、**`views`（投稿単位）と `clicks`
# （アカウント日次）を 1 つの予測に並べても通った。** 同じ根の間違いが
# `thth/measured.py` の欠測判定でも出ていた（層を混ぜた期待一覧）——**元が
# 平らな一覧であることだった**ので、ここを層のある定義にして両方を塞ぐ。
UNIT_POST = "post"                    # 投稿単位（`insights/posts/*.ndjson`）
UNIT_ACCOUNT_DAILY = "account_daily"  # アカウント日次（`insights/account/*.ndjson`）
UNIT_LABELS = {UNIT_POST: "投稿単位", UNIT_ACCOUNT_DAILY: "アカウント日次"}

# 指標 → **その指標が実際に取れる観測単位**（`thth/adapters/threads.py:237`
# と `:255` が要求している metric の一覧そのもの）。
METRIC_UNITS = {
    "views": (UNIT_POST, UNIT_ACCOUNT_DAILY),
    "likes": (UNIT_POST, UNIT_ACCOUNT_DAILY),
    "replies": (UNIT_POST, UNIT_ACCOUNT_DAILY),
    "reposts": (UNIT_POST, UNIT_ACCOUNT_DAILY),
    "quotes": (UNIT_POST, UNIT_ACCOUNT_DAILY),
    "shares": (UNIT_POST,),
    "followers_count": (UNIT_ACCOUNT_DAILY,),
    "clicks": (UNIT_ACCOUNT_DAILY,),
}
LEDGER_METRICS = tuple(METRIC_UNITS)

# **その指標を予測に使うと、何が台帳に無いのか**。名前があることと、欲しい量が
# あることは違う——ここを書いておかないと「検証したつもり」になれてしまう。
METRIC_CAVEATS = {
    "clicks": ("URL ごとの内訳（`clicks_by_url`）しか無く、**どの投稿が生んだ"
                "クリックかは台帳にありません**——同じ URL に過去の投稿・"
                "プロフィール欄・外部からの流入が乗ります"),
    "followers_count": ("アカウント全体の値で、**どの投稿で増えたかは台帳に"
                         "ありません**"),
}

# 証拠の階層（masaru の `AI協業の作法` §7・外部調査の分類に合わせた）。
# **L1（公式）が L2（うちの実測）より上とは限らない**——「指標の定義」なら L1、
# 「うちで何が起きたか」ならうちの実測が一次資料。**何を問うているかで変わる。**
EVIDENCE_TIERS = ("L1", "L2", "L3", "L4", "L5")

# 標本設計の区分（外部調査 2026-09-11 の §10）。**「30 本で合格」を作らない。**
REFUTATION_ONLY = "B"      # 絶対命題への反例探し。**1 件でも崩せる**
ESTIMATION = "M"           # 平均差・予測性能の推定。**本数が要る**
UNIDENTIFIABLE = "U"       # **変数が無いので識別できない。本数を増やしても無理**
SAMPLE_DESIGNS = (REFUTATION_ONLY, ESTIMATION, UNIDENTIFIABLE)

# 仮説の種類。**構造仮説（相手の仕組みの推測）を登録できるようにする**
# （masaru 指摘 2026-09-11）。それ自体は観測できないが、**観測できる派生予測を
# ぶら下げる**ことを条件にする。
HYPOTHESIS_KINDS = ("structural", "operational")

# **精度を持てる日付**（外部レビュー R5・外部調査 2026-09-12）。
# 「出所には必ず日付を」という schema が、知らない日を補完させていた
# （観測対象期間しか分からない出所に、実在しない公開日を書かせていた）。
# `date_precision` を必ず申告させることで、**知らないことを明示させる**。
# - `day`: `date` は `YYYY-MM-DD` を要求する。
# - `month`: `date` は `YYYY-MM` を要求する。
# - `unknown`: `date` は `null`（日そのものを持っていない）。
DATE_PRECISIONS = ("day", "month", "unknown")

# 日付が**何の日か**（公開日か・観測対象の期間か・こちらが取得した日か）。
# 区別しないと、「観測対象期間」を「公開日」と読み違えて知見が固まる
# （外部調査 §9 H09・H05・H08 の裁定）。
# **更新日を公開日として保存しない**（再判定 S2・2026-09-12 Codex）。
# Transparency Center の頁は「更新 2025-03-07」であって、**その日に書かれた
# わけではない。** 表せない語彙しか無いと、**近い名前の欄に入れてしまう。**
DATE_KINDS = ("published_at", "updated_at", "observed_period", "retrieved_at")

SOURCE_KEYS = ("tier", "ref", "date", "date_precision", "date_kind", "note")
PREDICTION_KEYS = ("statement", "metrics", "window", "scope")
HYPOTHESIS_KEYS = ("code", "claim", "kind", "sources", "predictions",
                   "refutation", "sample_design", "counter_hypothesis",
                   "scope", "state", "proposed_by", "verifier",
                   "created_at", "supersedes")

# **版を改めた理由**（運用指摘 2026-09-12）。検収記録には `disposition_reason`
# があるのに、**仮説には版を改めた理由を書く欄が無かった。** 運用は当座
# `claim` の末尾に書いていたが、本人が**置き場所として間違っていると自覚して
# いた**——`claim` は主張そのものの欄なので、版の運用メモが混ざると**次に読む
# 人が主張の一部として読む。**
#
# **`supersedes` を書くなら、理由も書く。** 版を改めた記録で、いちばん価値が
# あるのは「**何を間違えていたか**」で、それが残らないなら記録を増やす意味が
# 薄い。逆に**版を改めていないのに理由だけ書くことはできない**（何の理由か
# 判らない）。
#
# **キー自体は任意**（`REVIEW_OPTIONAL` と同じ扱い。既存の入力を一斉に
# 書き換えさせない）だが、**`supersedes` があるときだけ必須**にする。
HYPOTHESIS_OPTIONAL = ("supersede_reason",)


# **仮説の正しい形**（規約 14・2026-09-12）。
#
# **規約 14 の 4 回目をやっていた。** 「新しい schema を足したら、(1) 必須項目の
# 検査 (2) `expected_schema` の返却 (3) 空・欠損での拒否テスト を同時に入れる」
# ——`hypotheses` を足すとき (1) と (3) は入れて、**(2) だけ落としていた。**
# `reviews`・`vocabularies`・`form_specs` は 3 つとも載っているので、**仮説だけが
# 例外**。規約 14 が名指しで禁じている形そのもの。
#
# **しかも 2026-09-12 に、この関門を 3 回きつくした**（`supersedes` の表記・
# `supersede_reason` の条件付き必須・予測の観測単位）。**断られる回数を増やして
# おいて、断り文句だけ空のままにしていた。**
#
# 実地で踏まれている——運用セッションは仮説 4 件を**すべて `topic_models.py` を
# 直接読んで書いた**。手順書（`docs/手順_LLM_トピック選定.md:85`）が「**ソースを
# 読む必要はありません**」と約束している経路が、仮説の棚だけ機能していなかった。
#
# > 私は 1672 行目を読んだ時点では JSON をまだ書いていませんでした。
#
# **値域が見えていれば防げた、ではない**（値域は見えていた）。**書いている最中に
# 目に入らなかった。** 断られたときは「何を書けばいいか」を探しているので、
# そこで渡すのがいちばん結びつく。
HYPOTHESIS_SHAPE = {
    "Hypothesis": {
        "code": "短い識別子（例 H01）。**同じ仮説の版をまたいで同じ値にします**",
        "claim": "主張そのもの。**版の運用メモを混ぜないこと**"
                  "（理由は supersede_reason へ）",
        "kind": list(HYPOTHESIS_KINDS),
        "sources": [{
            "tier": list(EVIDENCE_TIERS),
            "ref": "出所（URL・文書名・うちの台帳の ID）",
            "date": "date_precision に合わせた形。"
                     "day→YYYY-MM-DD / month→YYYY-MM / unknown→null。"
                     "**知らない日を作らないこと**",
            "date_precision": list(DATE_PRECISIONS),
            "date_kind": list(DATE_KINDS),
            # **キーは必須・値は null 可**（外部レビュー・2026-09-12）。
            # 「省略可」と案内していたが、`SOURCE_KEYS` に `note` が入っていて
            # `_require()` が省略を拒む。**案内どおりに書くと断られた。**
            "note": "補足。**キーは必須・書くことが無ければ null**",
        }],
        "predictions": [{
            "statement": "**うちの台帳でどう見えるはずか**（相手の仕組みそのもの"
                          "ではなく、観測できる派生予測）",
            "metrics": [f"うちが持っている指標だけ: {'・'.join(LEDGER_METRICS)}。"
                         f"**取れる観測単位が指標ごとに違います**——"
                         f"shares は投稿単位にしかなく、clicks・followers_count は"
                         f"アカウント日次にしかありません。"
                         f"**そろう観測単位が無い組み合わせは警告が出ます**"
                         f"（拒否はしません）"],
            "window": "比較窓（例 24h・公開後 7 日）",
            "scope": "どの範囲の話か。**統制していないものもここに書くこと**"
                      "（例 曜日・本文は揃っていない）",
        }],
        "refutation": "**何が観測されたら捨てるか。** 本数ではなく"
                       "『何が起きたら』を書く——本数を書くと B（反例探し）が"
                       " M（推定）に化けます",
        "sample_design": {
            "B": "反例探し。**1 件でも崩せる。** 何本積んでも確かにはならない",
            "M": "推定（平均差・予測性能）。**本数が要る**",
            "U": "**変数が無いので識別できない。本数を増やしても無理。**"
                  "捨てずに残すが、shadow には上げられない",
        },
        "counter_hypothesis": "対抗仮説。**無いと、どんな観測も『支持した』に"
                               "読めます**",
        # **`scope` が 2 つある**（ここと `predictions[].scope`）ので、どちらに
        # 何を書くかを名指しする（運用指摘 2026-09-12）。統制の話を**予測ごとに
        # 書く**のは、**予測ごとに統制が違い得る**から——1 つの仮説に 2 つの予測が
        # あって、片方だけ曜日が揃っていない、が起こる。
        "scope": "**この仮説がどの account の話か**（すべてなら「すべて」）。"
                  "**統制していないもの（曜日・本文など）はここではなく "
                  "`predictions[].scope` へ**——予測ごとに統制は違い得ます",
        "state": [f"**いま登録できるのは 'proposed' だけ**"
                   f"（shadow への昇格は未実装・accepted は masaru の裁定）。"
                   f"語彙の状態は {list(REASON_STATE)}"],
        "proposed_by": "立てた人・セッション（`--by` で上書きされます）",
        "verifier": "**確かめる人。立てた人と分けること**"
                     "（同じ手が両方をやると、何も確かめていない）",
        "created_at": "ISO 8601・timezone 必須",
        "supersedes": "前の版の hypothesis_id（`sha256:` + 16 進 64 桁）。"
                       "最初は null。**版は 1 つずつ差し替えます**",
        "supersede_reason": "**supersedes があるときは必須。**"
                             "**『以前の誤り』の告白の欄ではありません**——"
                             "追加資料・対象範囲の変更でも構いません。"
                             "supersedes が null のときは書けません",
    },
}


def build_hypothesis(row: dict) -> dict:
    """仮説を 1 件、記録として残す（masaru 指示 2026-09-11）。

    **「検証できないから捨てる」も「正しいと決めて焼き込む」もしない。**
    出所と日付を付けて棚に置き、**手応えが溜まる形**にする。

    守らせること:

    - **出所には必ず日付欄を書く**（`AI協業の作法` §6「『○○によると』は書くな」）。
      公式でも「いま時点の真実」ではない——ランキングの説明は 1 年半前だった。
      ただし**日付欄を書くことと、日を知っていることは別**——`date_precision`
      を必ず申告させ、`day`/`month`/`unknown` に応じて要求する形を変える
      （外部レビュー R5）。**知らない日を作らせない。**
    - **構造仮説には、観測できる派生予測を 1 つ以上**。相手の仕組みそのものは
      観測できないが、**うちの台帳でどう見えるはずか**は書ける。
    - **予測はうちが持っている指標の語彙でしか書けない**（`LEDGER_METRICS`）。
      持っていない量（滞在時間・親密度）で予測を書くと、**検証したつもりに
      なれてしまう。**
    - **捨てる条件が無い仮説は登録できない。** 捨てられない仮説は知見にならない。
    - **対抗仮説を書く。** 無いと、どんな観測も「支持した」に読める。
    - **`U`（識別できない）は消さずに残す**が、**`shadow` には上げられない**
      ——検証が動いているように見せない（外部調査 §9 H09 の裁定）。
    - **`accepted` は作れない。** 語彙・型の仕様と同じく、正式な採用は
      独立確認の仕組みができてから（構想書 §8）。
    """
    _require(row, HYPOTHESIS_KEYS, "仮説")
    if row["supersedes"] is not None and (
            not isinstance(row["supersedes"], str)
            or not _REF_ID_RE.fullmatch(row["supersedes"])):
        # **型を検査していなかった**（外部レビュー V1・2026-09-12）。配列を
        # 渡すと**登録は通るのに、その記録があると仮説の一覧が丸ごと読めなく
        # なっていた**（逆索引が辞書のキーに使うため `TypeError`）。
        # **既存の検査だけなら受理できる入力が、後から足した読取処理を壊す。**
        # **表記まで見る**（外部レビュー・2026-09-12）。最初は型だけ見ていたが、
        # **空文字列と空白だけの文字列が素通りしていた**（`str` なので）。
        # 「指し先なし」を意味する値が診断されずに索引へ入る——挙げられた
        # `[]` `{}` `0` `false` と同じ形だった。**登録と読取で同じ基準にする。**
        # **見るのは表記だけ**——形式が正しいが存在しない ID は通る
        # （参照先の実在性・循環検査は対象外との指示）。
        raise SchemaError(
            f"supersedes は前の版の hypothesis_id（`sha256:` + 16 進 64 桁）"
            f"1 つか null です: {row['supersedes']!r}。**版は 1 つずつ"
            f"差し替えます**——複数を 1 件でまとめて差し替えたい場合も、"
            f"1 件ずつ記録してください（記録は消しません）")
    reason = row.get("supersede_reason")
    if row["supersedes"] is None and reason is not None:
        raise SchemaError(
            f"supersede_reason は supersedes があるときだけ書けます: "
            f"{reason!r}（**版を改めていないのに理由だけがあると、何の理由か"
            f"判りません**）")
    if row["supersedes"] is not None and (
            not isinstance(reason, str) or not reason.strip()):
        # **版を改めるなら、何を間違えていたかを書く。** ここが残らないなら、
        # 古い版を消さずに置いておく意味が薄い（読んでも何が変わったか判らない）。
        raise SchemaError(
            "supersedes を書くなら supersede_reason も書いてください"
            "（**版を改めた記録でいちばん価値があるのは『何を間違えて"
            "いたか』です**）")
    if not isinstance(row["claim"], str) or not row["claim"].strip():
        raise SchemaError("claim が空です")
    _require_choice(row["kind"], HYPOTHESIS_KINDS, "kind")
    _require_choice(row["sample_design"], SAMPLE_DESIGNS, "sample_design")
    _require_choice(row["state"], REASON_STATE, "state")
    _require_iso(row["created_at"], "created_at")
    for key in ("proposed_by", "verifier"):
        if not row.get(key):
            raise SchemaError(
                f"{key} が要ります（**立てた人と、確かめる人を分ける**。"
                f"同じ手が両方をやると、手応えが溜まったように見えて"
                f"何も確かめていない）")
    if not isinstance(row["scope"], str) or not row["scope"].strip():
        raise SchemaError("scope が空です（どの account の話か・すべてなら「すべて」）")
    if not isinstance(row["refutation"], str) or not row["refutation"].strip():
        raise SchemaError(
            "refutation が空です。**何が観測されたら捨てるかを書いてください**"
            "——捨てられない仮説は知見になりません")
    if not isinstance(row["counter_hypothesis"], str) \
            or not row["counter_hypothesis"].strip():
        raise SchemaError(
            "counter_hypothesis が空です。**対抗仮説を書いてください**"
            "——無いと、どんな観測も「支持した」に読めます")

    sources = row["sources"]
    if not isinstance(sources, list) or not sources:
        raise SchemaError("sources が空です（誰の・いつの話か）")
    for i, source in enumerate(sources):
        where = f"sources[{i}]"
        if not isinstance(source, dict):
            raise SchemaError(f"{where} は object")
        _require(source, SOURCE_KEYS, where)
        _require_choice(source["tier"], EVIDENCE_TIERS, f"{where} の tier")
        if not source.get("ref"):
            raise SchemaError(f"{where} の ref が空です（どこの何か）")
        # **日付欄そのものは必ず埋めさせる**が、精度は申告させる
        # （外部レビュー R5）。あとで「うちで確かめた事実」に化けさせない。
        _require_choice(source["date_precision"], DATE_PRECISIONS,
                         f"{where} の date_precision")
        _require_choice(source["date_kind"], DATE_KINDS,
                         f"{where} の date_kind")
        _require_source_date(source["date"], source["date_precision"],
                              f"{where} の date")

    predictions = row["predictions"]
    if not isinstance(predictions, list):
        raise SchemaError("predictions は配列")
    if row["sample_design"] != UNIDENTIFIABLE and not predictions:
        raise SchemaError(
            "predictions が空です。**うちの台帳でどう見えるはずかを 1 つ以上**"
            "書いてください（相手の仕組みそのものは観測できなくても、"
            "**派生する予測は書けます**）。書けないなら sample_design を "
            f"{UNIDENTIFIABLE!r} にして、なぜ書けないかを refutation に"
            "書いてください")
    for i, prediction in enumerate(predictions):
        where = f"predictions[{i}]"
        if not isinstance(prediction, dict):
            raise SchemaError(f"{where} は object")
        _require(prediction, PREDICTION_KEYS, where)
        if not isinstance(prediction["statement"], str) \
                or not prediction["statement"].strip():
            raise SchemaError(f"{where} の statement が空です")
        metrics = prediction["metrics"]
        if not isinstance(metrics, list) or not metrics:
            raise SchemaError(f"{where} の metrics が空です")
        # **「そろう観測単位が 1 つも無い」で拒否しない**（外部レビュー U1・
        # 2026-09-12）。一度は拒否にしたが、**2 つの点で間違っていた。**
        #
        #   1. **案内が効かなかった。** エラーは「sample_design を 'U' にして
        #      残してください」と言うのに、**同じ予測を U にしても同じエラーで
        #      保存できなかった。** 逃げ道の無い案内を出していた
        #   2. **判るのは「そのまま同じ粒度の量として扱えない」ことだけ。**
        #      集計・対応づけ・相関を検討する**未検証の案そのものを保存できない
        #      とまでは言えない。それを未検証のまま保持するのが proposed の役割**
        #
        # 層の食い違いは `prediction_unit_problems()` が警告として返す（保存は
        # しない）。**shadow・accepted への登録禁止はそのまま。**
        unknown = [m for m in metrics if m not in LEDGER_METRICS]
        if unknown:
            # **持っていない量で予測を書かせない**（外部調査 §9 H10）。
            raise SchemaError(
                f"{where} に、うちの台帳に無い指標があります: {unknown}。"
                f"使えるのは: {'・'.join(LEDGER_METRICS)}。"
                f"**持っていない量（滞在時間・親密度など）で予測を書くと、"
                f"検証したつもりになれてしまいます**——その仮説は "
                f"sample_design を {UNIDENTIFIABLE!r} にして残してください")

    if row["sample_design"] == UNIDENTIFIABLE and row["state"] == "shadow":
        # **識別できないものを「検証が動いている」と見せない**（§9 H09）。
        raise SchemaError(
            f"sample_design が {UNIDENTIFIABLE!r} の仮説は shadow にできません"
            f"（**検証が動いているように見えてしまう**）。proposed のまま"
            f"残してください——捨てはしません")

    out = dict(row)
    # **任意の欄は、無ければ「キーごと落とす」**（外部レビュー・2026-09-12）。
    #
    # 最初は `null` で埋めていた。「書いた記録と書かなかった記録で内容 ID が
    # 変わらないように」という意図だったが、**それは新しい記録どうしの話**で、
    # **この欄ができる前に保存した記録と同じ入力を出すと、別 ID になっていた**
    # （旧: キー無し／新: `null`）。**同じ仮説が 2 件に見える**——避けたかった
    # ことを、別の向きで起こしていた。しかも**旧 ID を指す改訂を作っても、
    # 複製のほうは別 ID なので後継として結び付かない。**
    #
    # **省略と明示 `null` を、どちらも「キー無し」に正規化する。** これで
    # 新しい記録どうしの同一性も、旧記録との同一性も、両方保てる
    # （**二択ではなかった**）。保存済み JSON の書き換えも棚の移行も要らない。
    for key in HYPOTHESIS_OPTIONAL:
        if out.get(key) is None:
            out.pop(key, None)
    out["schema_version"] = SCHEMA_VERSION
    out["hypothesis_id"] = content_id(out, exclude=("hypothesis_id",))
    return out


def prediction_unit_problems(row) -> list:
    """**予測の指標が、同じ観測単位に並んでいるか**を見る（運用指摘 2026-09-12）。

    **読むだけ。保存しない。** 保存済みの仮説にも後から当てられるように、
    `build_hypothesis()` の中ではなく独立した関数にしてある。

    きっかけ: `views` が最多の投稿の URL が `clicks_by_url` でも最多になる、
    という予測が通った。**`views` は投稿単位、`clicks` はアカウント日次×URL**
    で、**「その投稿が生んだクリック」という量は台帳に無い。** それでも
    `LEDGER_METRICS` が平らな名前一覧だったので、両方「うちにある指標」として
    通っていた。

    **見つけられるのは申告された `metrics` の組み合わせだけ**で、statement の
    日本語は読んでいない。**「問題なし」は「観測可能だと確かめた」ではない。**
    """
    problems = []
    for i, prediction in enumerate(row.get("predictions") or []):
        metrics = [m for m in (prediction.get("metrics") or [])
                   if m in METRIC_UNITS]
        if not metrics:
            continue
        unit_sets = [set(METRIC_UNITS[m]) for m in metrics]
        if len(set(map(frozenset, unit_sets))) > 1:
            detail = "・".join(
                f"{m}（{'／'.join(UNIT_LABELS[u] for u in METRIC_UNITS[m])}）"
                for m in metrics)
            common = set.intersection(*unit_sets)
            where = (f"**どちらも {'／'.join(UNIT_LABELS[u] for u in sorted(common))}"
                     f"で読む**のでなければ、この予測は同じ観測単位に並びません"
                     if common else "**同じ観測単位にそろう読み方がありません**")
            problems.append(
                f"predictions[{i}]: 指標の観測単位が食い違っています——{detail}。"
                f"{where}")
        for m in metrics:
            caveat = METRIC_CAVEATS.get(m)
            if caveat:
                problems.append(f"predictions[{i}]: `{m}` は、{caveat}")
    return problems


def _require_source_date(value, precision: str, what: str) -> None:
    """出所の日付を、**申告した精度どおりの形**でだけ検査する（外部レビュー R5）。

    `date` を必ず `YYYY-MM-DD` にする schema が、知らない日の補完を誘発して
    いた（観測対象期間しか分からない出所に、実在しない公開日を書かせていた
    ——外部調査 §9 H05・H08・H09）。**`unknown` を許しても「書かなくてよい」
    にはしない**——`date_precision` を必ず申告させることで、知らないことを
    明示させる。
    """
    if precision == "unknown":
        if value is not None:
            raise SchemaError(
                f"{what} は date_precision='unknown' のときは null にして"
                f"ください（{value!r}）。**知らない日を書くと、知っている"
                f"ことにされてしまいます**")
        return
    if precision == "month":
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
            raise SchemaError(
                f"{what} は date_precision='month' のとき YYYY-MM を要求"
                f"します: {value!r}")
        try:
            datetime.date.fromisoformat(f"{value}-01")
        except ValueError as e:
            raise SchemaError(f"{what} を月として読めません: {value!r}") from e
        return
    # precision == "day"（デフォルトの厳しさ。知っているならここまで書ける）
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise SchemaError(
            f"{what} は date_precision='day' のとき YYYY-MM-DD を要求します"
            f"（**いつの話かを必ず書く**）: {value!r}")
    try:
        datetime.date.fromisoformat(value)
    except ValueError as e:
        raise SchemaError(f"{what} を日付として読めません: {value!r}") from e
