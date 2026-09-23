"""`thth where` / MCP `where_to_appear`——次にどこへ絡みに行くか（設計「自分の
泉」§2.3・§2.5・§2.6・T2-2）。

芯（設計 §0・§2.3・§5）: **検索の一覧に自分の履歴を重ねて並べるだけ**。順位
付け・おすすめは作らない——材料を並べ、選ぶのは呼ぶ側（LLM）。account ごと
（＝媒体ごと）の節を並べる。**account をまたぐ集計は一切作らない**（トップ
レベルに `n` を置かない・媒体ごとの数を足さない・割らない・順位も付けない・
設計「自分の泉」§2.6）。

規約（発注 T2-2）:

  (a) **`data/` の下に何も書かない**（`thread_read` の T1-2 と同じ検査。
      `keyword_search`・`after_cli.answer()`・`engagements.load()` はどれも
      読むだけで、この口自身も書かない）。
  (b) runs には **account ごとに 1 行**: `{"action": "where_to_appear",
      "account", "words", "n", "status", "error"}`。**本文・username を
      含めない**——`thread_read._record_run()` と同じ網（`runs.
      record_minimal()` に T2-2 で共通化）。
  (c) 本文は 1 行プレビューだけ（鍵名は `preview`——`text` という鍵は使わない。
      `preview` は保存しない・画面と `--json` の両方に出るが、`data/` にも
      runs にも残らない）。
  (d) 権限が無い（`PermissionMissing`）は、その account の `cannot_say` に
      `threads_read_cli._narrowed_by_standard_access()` / `_not_granted()`
      と同じ文言で出し、他の語・他の account は続ける。token が無い・媒体に
      `keyword_search` が無い・台帳が読めない account は、その account
      自体を `by_account` に**入れず**、トップレベルの `cannot_say` に
      名前で出す（例:「kopicha-mastodon: token が無い」）。
"""
from __future__ import annotations

import datetime
import json
import sys

from . import accounts as accounts_mod
from . import adapters as adapters_mod
from . import after_cli as after_cli_mod
from . import engagements as engagements_mod
from . import jst, read_window
from . import redact as redact_mod
from . import runs as runs_mod
from . import threads_read_cli as threads_read_cli_mod
from .adapters import base as adapter_base

CAPABILITY = "keyword_search"

MIN_WORDS = 1
MAX_WORDS = 5
# `--also`（設計 3.7.0 §C1）: 同じ検索結果のうち本文にどれかの語を含むものだけを残す
# 語。**追加の検索はしない**——絞るだけ。observe の監視語には使わない（CLI の下調べ用）。
MAX_ALSO = 5
DEFAULT_LIMIT = 25
MAX_LIMIT = 100

# Mastodon の検索の限界（T2-1 の adapter docstring と同じ事実・**L2**:
# docs.joinmastodon.org/methods/search/「statuses depend on an ElasticSearch
# backend being present and the API request being authenticated」）。
# **応答の件数だけからは全文検索が効いているか区別できない**——黙って隠さず、
# 呼ぶ側の provenance に必ず出す（T2-1 発注書のとおり）。
MASTODON_SEARCH_NOTE = (
    "mastodon の検索はインスタンスの設定次第です（全文検索を有効にしている"
    "とは限りません。無効なら自分の投稿・自分が触れた投稿しか返らない"
    "可能性がありますが、件数だけからは判別できません）")
# Mastodon の検索 API に並び順の指定は無い（一次資料の param 一覧に sort 系が
# 無い・**L2**）。**嘘の並びを作らない**——`--recent` を受け取っても無視した
# ことを言う。
MASTODON_RECENT_IGNORED_NOTE = (
    "mastodon の検索に並び順の指定は無いので、--recent（RECENT）は無視しました")


# 0 件のときの言い方（設計 3.7.0 §C2）。**0 件を「無い」と言わない**——媒体が語を
# 受け付けずに空を返した場合と区別できない。Threads の keyword search の文書には
# 「センシティブと判断した語を含む検索には空の配列を返す」とある（照合
# `docs/照合_観測の地図_集計の定点観測_2026-09-23.md` の K）。短い語・形容詞の扱いは
# 一次資料に記載が無い（未確認）。
ZERO_OR_FILTERED = "zero_or_filtered"
ZERO_MESSAGE = "0 件でした。本当に無いか、媒体が語を受け付けなかったかは区別できません"
ZERO_MEDIUM_NOTE = {
    "threads": "Threads は、センシティブと判断した語の検索に空の配列を返すと文書にあります",
}
ZERO_UNVERIFIED = "短い語・形容詞の扱いは一次資料に記載がありません（未確認）"


def zero_or_filtered(medium) -> dict:
    """0 件の検索に添える升目（静的な符丁と固定の文言だけ）。"""
    note = ZERO_MEDIUM_NOTE.get(medium)
    return {"code": ZERO_OR_FILTERED,
            "message": ZERO_MESSAGE + (f"（{note}）" if note else ""),
            "unverified": ZERO_UNVERIFIED}


class WhereError(Exception):
    """問いが受け取れない（account/project どちらも無い・語が 1〜5 個でない 等）。

    **黙って空の答えを返さない**（`thread_read.ThreadReadError` と同じ筋）。
    """


def _reject(message: str) -> None:
    raise WhereError(message)


def _posts_with_preview(rows: list, replied) -> list:
    """`threads_read_cli.post_rows()` の一覧に `preview`（1 行の抜粋）を足す。

    **本文そのもの（`text`）は鍵にしない**——`preview` は画面にも `--json` にも
    出るが、`data/` にも runs にも残らない（規約 (c)）。`rows`（`keyword_search()`
    の生の行）と `post_rows()` の戻りは同じ順序で対応するので `zip()` で足す。
    """
    posts = threads_read_cli_mod.post_rows(rows, replied=replied)
    for row, post in zip(rows, posts):
        post["preview"] = threads_read_cli_mod._one_line(row.get("text"))
        post["reply_count"] = threads_read_cli_mod.reply_count(row)
    return posts


def _permission_message(account_name: str, adapter, e: adapter_base.PermissionMissing) -> str:
    """`threads_read_cli._run()` と同じ言い分け（「乗っていない」／「標準アクセス
    では絞られる」）を、CLI の出力を経由せずにここで直に組む。
    """
    source = threads_read_cli_mod._granted_source(adapter, e.permission)
    if source is None:
        return threads_read_cli_mod._not_granted(account_name, e)
    return threads_read_cli_mod._narrowed_by_standard_access(
        account_name, e, source, threads_read_cli_mod.SEARCH_NARROWED_NOTE)


def _my_history(account_name: str, word: str) -> tuple[dict | None, str | None]:
    """`after_cli.answer(account_name, topic=word)` の `engagements` から
    `n`・`reacted`・`likes_24h`・`replies_back_24h` を**写す**（新しい集計を
    作らない・発注 T2-2）。戻りは `(my_history, cannot_say理由)`。
    """
    try:
        result = after_cli_mod.answer(account_name, topic=word)
    except (accounts_mod.AccountError, after_cli_mod.AfterError) as e:
        return None, f"{word}: 自分の履歴が読めません（{e}）"
    eng = result["engagements"]
    return {"n": eng["n"], "reacted": eng["reacted"],
            "likes_24h": eng["likes_24h"], "replies_back_24h": eng["replies_back_24h"]}, None


def _also_filter(rows: list, also: list) -> tuple[list, dict]:
    """本文にどれかの語を含む行だけ残す（大文字小文字を畳んだ部分一致）。**検索はしない。**"""
    needles = [word.casefold() for word in also]
    kept = [row for row in rows
            if isinstance(row.get("text"), str)
            and any(needle in row["text"].casefold() for needle in needles)]
    return kept, {"words": list(also), "n_before": len(rows), "n_matched": len(kept),
                  "basis": "text_contains_any_casefold", "additional_search": False}


def _account_node(account_name: str, words: list, *, search_type: str,
                  limit: int, now, since=None, exclude_engaged=False, max_per_author=None,
                  also=None) -> tuple[dict | None, Exception | str | None]:
    """1 account 分の節。戻りは `(node, 理由)`——どちらか一方だけが非 `None`。
    台帳が読めない・token が無い・媒体に `keyword_search` が無いときは
    `node` が `None`（`by_account` に**入れない**・規約 (d)）。

    **理由の型で役割が違う**（T5-2・`who_cli._account_node()` と揃える）:
    account 自体が読めない（`AccountError`）ときだけ**例外そのまま**を返す
    ——単一 account 呼び出しはこれをそのまま投げ直す（`answer()` 参照）。
    それ以外（媒体未対応・token 無し）は account 自体は読めているので、
    文字列のまま（`--project` と同じく `cannot_say` に流れて rc=0 のまま
    続ける・単一 account でもここは変えない——「読めない」ではなく
    「この account ではこの機能が使えない」だから）。
    """
    try:
        floor = read_window.cutoff(since, now=now)
    except ValueError as exc:
        _reject(str(exc))
    if max_per_author is not None and (isinstance(max_per_author, bool) or not isinstance(max_per_author, int) or max_per_author < 1):
        _reject('max_per_author は 1 以上の整数です')
    try:
        account_cfg = accounts_mod.load_account(account_name)
    except accounts_mod.AccountError as e:
        return None, e
    media = account_cfg.get("media")
    if CAPABILITY not in adapters_mod.capabilities_for(media):
        return None, (f"{account_name}: この媒体（{media}）では語による検索"
                      f"（{CAPABILITY}）は未対応です")
    token = accounts_mod.load_token(account_cfg)
    adapter_cls = adapters_mod.adapter_class(media)
    if not adapter_cls.has_token(token):
        return None, f"{account_name}: token が無いので検索できません"
    adapter = adapters_mod.make_adapter(account_cfg, token)

    try:
        replied_idx, replied_why = threads_read_cli_mod.replied_index(
            account_cfg, account_name)
    except accounts_mod.AccountError as e:
        replied_idx, replied_why = None, str(e)

    node_cannot_say: list = []
    if replied_why:
        # **印が無いことを「返していない」にしない**（`post_rows()` と同じ規律）。
        node_cannot_say.append(f"「返信済み」印: {replied_why}")

    eng_ledger = engagements_mod.load(account_cfg, account_name)
    eng_rows = [r for r in eng_ledger['rows'] if r.get('account') == account_name
                and r.get('medium') == media]
    engaged = {r.get('author_key') for r in eng_rows if r.get('author_key')}
    if exclude_engaged and eng_ledger['broken']:
        node_cannot_say.append('engagements_unreadable: 除外対象の一部は不明です')
    by_word: dict = {}
    by_tag: list = []
    author_keys: set = set()
    for word in words:
        if media in ("bluesky", "mastodon"):
            tag = word.lstrip("#")
            try:
                tag_since = jst.iso(now - datetime.timedelta(hours=24))
                if media == "bluesky":
                    pages = account_cfg.get("search_pages", 4)
                    tagged = adapter.tag_search(tag, tags=[tag],
                                                sort="latest" if search_type == "RECENT" else "top",
                                                since=tag_since, until=jst.iso(now),
                                                pages=pages, limit=limit)
                    tagged["tag"] = tag
                else:
                    tagged = adapter.tag_observation(tag, limit=min(limit, 40), since=tag_since)
                tagged["window_basis"] = "independent_24h"
                by_tag.append(tagged)
            except (adapter_base.AdapterError, RuntimeError, ValueError) as e:
                node_cannot_say.append(f"{word}: タグの観測: {redact_mod.redact(str(e))}")
        try:
            kwargs = {'search_type': search_type, 'limit': limit}
            if floor is not None and media == 'bluesky':kwargs['since'] = jst.iso(floor)
            rows = adapter.keyword_search(word, **kwargs)
        except adapter_base.PermissionMissing as e:
            node_cannot_say.append(f"{word}: {_permission_message(account_name, adapter, e)}")
            continue
        except adapter_base.AdapterError as e:
            node_cannot_say.append(f"{word}: {redact_mod.redact(str(e))}")
            continue
        except RuntimeError as e:
            # **`AdapterError` ではない素の `RuntimeError`**（Bluesky の
            # `_request` 周りなど）を、`AdapterError` と同じ出し方で受ける
            # （T9-2）。`AdapterError` は `RuntimeError` の子なので、上の
            # `except AdapterError` をすり抜けたものだけがここに来る。この
            # account 全体を落とさず、その語だけ `cannot_say` に流して他の
            # 語・他の account を続ける（`--project` で 1 account だけ落ちて
            # も他が出る、という既存の筋と同じ）。
            node_cannot_say.append(f"{word}: {redact_mod.redact(str(e))}")
            continue

        # 媒体が返した件数（手元の絞り込みの前）。0 なら「無い」と言わない（§C2）。
        raw_n = len(rows)
        dropped = {'since': None if floor and media == 'bluesky' else 0,
                   'exclude_engaged': 0, 'max_per_author': 0}
        filtered, per_author = [], {}
        for row in rows:
            if floor is not None and media != 'bluesky':
                stamp = jst.parse(row.get('timestamp'))
                if stamp is None or stamp < floor:
                    dropped['since'] += 1
                    continue
            key = row.get('author_key') or adapter_base.author_key(row.get('medium') or media, row.get('username'))
            if exclude_engaged and key and key in engaged:
                dropped['exclude_engaged'] += 1
                continue
            if max_per_author is not None and key:
                if per_author.get(key, 0) >= max_per_author:
                    dropped['max_per_author'] += 1
                    continue
                per_author[key] = per_author.get(key, 0) + 1
            filtered.append(row)
        rows = filtered
        also_counts = None
        if also:
            # 同じ検索結果を絞るだけ（設計 3.7.0 §C1）。語ごとに 1 回の検索のまま。
            rows, also_counts = _also_filter(rows, also)
        material = threads_read_cli_mod.search_material(
            rows, q=word, search_type=search_type, limit=limit)
        material["medium"] = media
        posts = _posts_with_preview(rows, replied_idx)
        for post in posts:
            if post.get("author_key"):
                author_keys.add(post["author_key"])

        my_history, history_reason = _my_history(account_name, word)
        if history_reason:
            node_cannot_say.append(history_reason)

        by_word[word] = {"posts": posts, "material": material, "my_history": my_history,
                         "dropped": dropped,
                         "window": {"since": jst.iso(floor) if floor else None,
                                    "basis": "server_sortAt" if media == 'bluesky' else 'timestamp'}}
        if also_counts is not None:
            by_word[word]["also"] = also_counts
        if raw_n == 0:
            by_word[word][ZERO_OR_FILTERED] = zero_or_filtered(media)

    # `last_reaction`（T3-2・設計 §2.3「要約」= met・last・last_reaction の
    # 3 つ）。計算は `after_cli.reaction_lookup()` の 1 か所だけ。
    summary = engagements_mod.author_summary(
        author_keys, eng_rows, reaction_for=after_cli_mod.reaction_lookup(account_name))
    you_and_them = {row["author_key"]: {"met": row["met"], "last": row["last"],
                                        "last_reaction": row["last_reaction"]}
                   for row in summary}

    node = {"medium": media, "by_word": by_word, "by_tag": by_tag,
            "you_and_them": you_and_them,
            "cannot_say": node_cannot_say}
    return node, None


def _resolve_names(*, account_name: str | None, project: str | None) -> tuple[list, list]:
    """対象の account 名の一覧と、そこに至るまでの top-level `cannot_say`。

    `--project` は台帳の `project` が一致する account 全部（`list_account_names()`
    → `load_account()`）。**読めない台帳が 1 本混ざっていても他は続ける**——
    その account がこの project に属するかどうかは判らないままだが、読めな
    かったこと自体を隠さない（発注 T2-2「--project で読めない台帳が 1 本
    混ざっていても他が出る」）。
    """
    if account_name is not None:
        return [account_name], []

    top_cannot_say: list = []
    names: list = []
    for name in accounts_mod.list_account_names():
        try:
            cfg = accounts_mod.load_account(name)
        except accounts_mod.AccountError as e:
            top_cannot_say.append(f"{name}: {e}")
            continue
        if cfg.get("project") == project:
            names.append(name)
    if not names:
        top_cannot_say.append(f"project={project!r} に一致する account がありません")
    return names, top_cannot_say


def _clean_also(also) -> list:
    if also is None:
        return []
    if not isinstance(also, list):
        _reject(f"also は配列です: {also!r}")
    out = []
    for word in also:
        if not isinstance(word, str) or not word.strip():
            _reject(f"also の語は空でない文字列です: {word!r}")
        out.append(word.strip())
    if len(out) > MAX_ALSO:
        _reject(f"also は {MAX_ALSO} 語までです（受け取ったのは {len(out)} 語）")
    return out


def answer(*, account_name: str | None = None, project: str | None = None,
          words: list, recent: bool = False, limit: int = DEFAULT_LIMIT,
          now=None, since=None, exclude_engaged=False, max_per_author=None,
          also=None) -> dict:
    """`where_to_appear` の答え（設計「自分の泉」§2.3・§2.6）。**読むだけ。**"""
    if account_name and project:
        _reject("account と --project は同時に指定できません")
    if not account_name and not project:
        _reject("account か --project のどちらかが要ります")
    if not isinstance(words, list):
        _reject(f"words は配列です: {words!r}")
    cleaned: list = []
    for w in words:
        if not isinstance(w, str) or not w.strip():
            _reject(f"語は空でない文字列です: {w!r}")
        cleaned.append(w.strip())
    if not (MIN_WORDS <= len(cleaned) <= MAX_WORDS):
        _reject(f"語は {MIN_WORDS}〜{MAX_WORDS} 個です（受け取ったのは {len(cleaned)} 個）")
    words = cleaned
    if not isinstance(limit, int) or isinstance(limit, bool) or not (1 <= limit <= MAX_LIMIT):
        _reject(f"limit は 1〜{MAX_LIMIT} です: {limit!r}")

    now = now if now is not None else jst.now_jst()
    try:
        read_window.cutoff(since, now=now)
    except ValueError as exc:
        _reject(str(exc))
    if max_per_author is not None and (type(max_per_author) is not int or max_per_author < 1):
        _reject('max_per_author は 1 以上の整数です')
    also = _clean_also(also)
    search_type = "RECENT" if recent else "TOP"

    names, top_cannot_say = _resolve_names(account_name=account_name, project=project)

    by_account: dict = {}
    notes: list = []
    for name in names:
        node, reason = _account_node(name, words, search_type=search_type,
                                     limit=limit, now=now, since=since,
                                     exclude_engaged=exclude_engaged, max_per_author=max_per_author,
                                     also=also)
        if node is None:
            if isinstance(reason, accounts_mod.AccountError) and project is None:
                # **単一 account: そのまま投げ直す**（T5-2・`who_cli.answer()`
                # と揃える・loud reject）。account 名の不正・台帳が無いを
                # rc=0・空の答えで隠さない。`--project` はこれまでどおり
                # 他の account を続ける（下の else へ）。
                raise reason
            reason_text = f"{name}: {reason}" if isinstance(reason, Exception) else reason
            top_cannot_say.append(reason_text)
            runs_mod.record_minimal(name, {
                "action": "where_to_appear", "account": name, "words": words,
                "n": None, "status": "error", "error": reason_text}, now=now)
            continue

        by_account[name] = node
        n = sum(len(entry["posts"]) for entry in node["by_word"].values())
        runs_mod.record_minimal(name, {
            "action": "where_to_appear", "account": name, "words": words,
            "n": n, "status": "ok", "error": None}, now=now)

        if node["medium"] == "mastodon":
            if MASTODON_SEARCH_NOTE not in notes:
                notes.append(MASTODON_SEARCH_NOTE)
            if recent and MASTODON_RECENT_IGNORED_NOTE not in notes:
                notes.append(MASTODON_RECENT_IGNORED_NOTE)

    extra = {"also": also} if also else {}
    return {
        "account": account_name, "project": project, "words": words, **extra,
        "by_tag": [{"account": name, **entry} for name, node in by_account.items()
                   for entry in node["by_tag"]],
        "by_account": by_account, "cannot_say": top_cannot_say,
        "provenance": {"fetched_at": jst.iso(now), "notes": notes},
    }


# ---------------------------------------------------------------------- CLI

def _render_human(result: dict) -> None:
    """人向けの画面: account ごとに見出し → 語ごとに 1 行の要約 → 行ごとに
    印・時刻・@username・仮名 8 桁・本文 1 行・permalink。**順位の数字を
    付けない**（規約・設計「自分の泉」§5 規約 6）。
    """
    who = f"project {result['project']}" if result["project"] else result["account"]
    print(f"where  {who}  語: {'・'.join(result['words'])}")
    for name, node in result["by_account"].items():
        print(f"\n[{name}]（{node['medium']}）")
        for word, entry in node["by_word"].items():
            material = entry["material"]
            authors = material["authors"]
            print(f"  語「{word}」  件数={material['n']}  異なり={authors['distinct']}"
                 f"  直近={material['latest_timestamp'] or '—'}")
            if entry.get('dropped'):
                print(f"    除外: {entry['dropped']}")
            if entry.get(ZERO_OR_FILTERED):
                zero = entry[ZERO_OR_FILTERED]
                print(f"    {zero['message']}。{zero['unverified']}")
            if entry.get("also"):
                also = entry["also"]
                print(f"    also（{'・'.join(also['words'])}）: {also['n_before']} 件中 also に合ったもの"
                      f" {also['n_matched']} 件（追加の検索はしていません）")
            history = entry["my_history"]
            if history:
                print(f"    自分の履歴: n={history['n']}  反応あり={history['reacted']}")
            for post in entry["posts"]:
                mark = threads_read_cli_mod._replied_cell(post["replied"])
                stamp = post["timestamp"] or "—"
                print(f"    {mark}{stamp}  @{post['author'] or '—'}"
                     f"（{post['author_key'] or '—'}）  {post['preview']}")
                if post["permalink"]:
                    print(f"      {post['permalink']}")
                print(f"      post_id: {post['post_id']}")
        for tagged in node["by_tag"]:
            print(f"  タグ #{tagged['tag']}: n={tagged['n']}  "
                  f"異なり={tagged['distinct_authors']}  "
                  f"直近={tagged['latest_at'] or '—'}  "
                  f"観測元={tagged['observed_from']}")
            if tagged.get("co_tags"):
                print("    同時タグ: " + "・".join(
                    f"#{item['tag']} ({item['n']})" for item in tagged["co_tags"]))
            if tagged.get("history"):
                print("    日次 history（インスタンスの視界）: " + "・".join(
                    f"{item['day']}: {item['uses']} 投稿/{item['accounts']} account"
                    for item in tagged["history"]))
        if node["cannot_say"]:
            for line in node["cannot_say"]:
                print(f"  言えない: {line}")
    if result["cannot_say"]:
        print("")
        for line in result["cannot_say"]:
            print(f"言えない: {line}")
    if result["provenance"]["notes"]:
        print("")
        for line in result["provenance"]["notes"]:
            print(f"注記: {line}")


def register(sub) -> None:
    """`thth where (<account> | --project P) <語…>` を親の subparsers に
    ぶら下げる（`thread_read.register()` と同じ型）。

    **1 本の `nargs='+'` 位置引数**にする（`account` と `words` を別々の位置
    引数にすると、`--project` のとき argparse が最初の語を account 側に
    食う——`nargs='?'` + `nargs='+'` は値の個数だけで割り振るので、`--project`
    の有無を見て後から `cmd_where()` で割る）。
    """
    p = sub.add_parser(
        "where",
        help="次にどこへ絡みに行くか——検索の一覧に自分の履歴を重ねて返す"
             "（設計「自分の泉」§2.3・§2.6・読むだけ）")
    p.add_argument("targets", nargs="*", metavar="ACCOUNT_OR_WORD",
                   help="<account> <語…>（1〜5 語）。--project のときは語だけ")
    p.add_argument("--project", default=None, metavar="P",
                   help="account の代わりに、この project の account 全部を対象にする")
    p.add_argument("--recent", action="store_true",
                   help="TOP でなく RECENT（新しい順）で検索する")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                   help=f"1 account・1 語あたりの上限（既定 {DEFAULT_LIMIT}）")
    p.add_argument("--json", action="store_true")
    p.add_argument("--word", action="append", default=[], help="検索語（繰り返し指定できます）")
    p.add_argument('--since', default=None, help='検索期間（7d/1h/ISO）')
    p.add_argument('--exclude-engaged', action='store_true')
    p.add_argument('--max-per-author', type=int, default=None)
    p.add_argument("--also", action="append", default=None, metavar="語",
                   help="同じ検索結果のうち、本文にどれかの語を含むものだけを残す"
                        f"（繰り返し指定・{MAX_ALSO} 語まで・追加の検索はしない）")
    p.set_defaults(func=cmd_where)


def cmd_where(args) -> int:
    """`thth where (<account>|--project P) <語…> [--recent] [--limit N] [--json]`。"""
    as_json = bool(getattr(args, "json", False))
    if args.project:
        account_name = None
        words = list(args.targets) + list(getattr(args, "word", []))
    else:
        if len(args.targets) < 1:
            print("account と語（1〜5 個）が要ります: thth where <account> <語…>"
                 "（project ごとなら --project P <語…>）", file=sys.stderr)
            return 2
        account_name = args.targets[0]
        words = args.targets[1:] + list(getattr(args, "word", []))

    try:
        result = answer(account_name=account_name, project=args.project, words=words,
                        recent=args.recent, limit=args.limit, since=getattr(args, "since", None),
                        exclude_engaged=getattr(args, "exclude_engaged", False),
                        max_per_author=getattr(args, "max_per_author", None),
                        also=getattr(args, "also", None))
    except accounts_mod.AccountError as e:
        # **単一 account が読めなければ loud reject**（T5-2・`who` と揃える）。
        # `--json` は人向けの文言でなく `{"error", "account"}` を出す——
        # 呼ぶ側（LLM・MCP）がプロパティで拾えるように。
        if as_json:
            print(json.dumps({"error": str(e), "account": account_name},
                             ensure_ascii=False))
        else:
            print(str(e), file=sys.stderr)
        return 1
    except WhereError as e:
        print(str(e), file=sys.stderr)
        return 2

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    _render_human(result)
    return 0
