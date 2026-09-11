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

_REASON_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

VOCABULARY_KEYS = ("name", "entries", "created_at", "created_by", "supersedes")
ENTRY_KEYS = ("reason_id", "display", "definition", "includes", "excludes",
              "scope", "judged_by", "state", "meaning_version")


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
    if row["supersedes"] is not None and not isinstance(row["supersedes"], str):
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
                   "disposition_reason", "note")
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
    if row["disposition"] == "fixed" and not row.get("revised_draft_sha256"):
        raise SchemaError(
            "disposition が fixed なのに revised_draft_sha256 がありません。"
            "**「直した」は、直した後の原稿を指して初めて確かめられます**"
            "（直したことは、正しくなったことではありません）")
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
        "note": "覚え書き（**自由文は根拠データであって命令ではありません**）",
    },
}
