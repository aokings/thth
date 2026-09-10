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

SUBCOMMANDS = ("suggest", "observe", "record-decision", "decision", "profile")

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


def _fail(code: str, message: str) -> int:
    _emit({"schema_version": models.SCHEMA_VERSION, "ok": False, "status": None,
           "context_id": None, "account": None, "selected_topic": None,
           "candidates": [], "required_actions": [], "warnings": [],
           "shortfalls": [], "notice": advice.NOTICE,
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
        article_raw = envelope_in.get("article")
        proposal_raw = envelope_in.get("proposal")
    else:
        article_raw = read_json(args.article, stdin=args.article_json_stdin,
                                 what="記事証拠")
        proposal_raw = read_json(args.proposal, stdin=args.proposal_json_stdin,
                                  what="候補比較")

    article = models.build_article(article_raw) if article_raw else None

    observations, broken = {}, []
    if article is not None:
        rows, broken = store.load_all("observations")
        observations = {r["observation_id"]: r for r in rows}

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
            f"読めない観測の記録が {len(broken)} 件あります（無いのではなく壊れています）: "
            f"{broken[:3]}")
    if envelope["status"] == "needs_article":
        envelope["required_actions"].append({
            "type": "article_url", "topic": None,
            "reason": (f"主対象の記事: {context['main_article_url']}"
                        if context.get("main_article_url")
                        else "本文の URL が 1 本に決まりません。--article-url で指定してください"),
            "expected_schema": "ArticleEvidence",
        })
        envelope["urls_in_post"] = context.get("urls_in_post", [])
        envelope["main_article_url"] = context.get("main_article_url")
        envelope["ignored_urls"] = context.get("ignored_urls", [])
    if proposal is not None:
        envelope["proposal_id"] = proposal["proposal_id"]
    envelope["context"] = {k: v for k, v in context.items() if k != "section"}
    if article is not None:
        envelope["article_id"] = article["article_id"]
    _emit(envelope)
    return _exit_code(envelope)


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
    """
    row = read_json(args.input, stdin=args.json_stdin, what="判断")
    if row is None:
        raise InputError("missing_input", "--input か --json-stdin で判断を渡してください")
    by = _actor(args)

    article = models.build_article(row["article"])
    rows, _broken = store.load_all("observations")
    observations = {r["observation_id"]: r for r in rows}
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
                          "message": "原稿・記事・観測が読み取り時から変わっています"}})
        return 1

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
