#!/usr/bin/env python3
"""薄い MCP（stdio）。発注 §4・設計 §3.7。

中身は CLI（`bin/thth`）を subprocess で呼んで `--json` の出力を返すだけ。
判断・整形・条件分岐はここに書かない（MCP に業務論理を持たせない・§3.7）。

T1 で出すのは読み取り 4 本だけ: thth_lint・thth_queue・thth_preview・thth_board。
thth_throw・thth_approve・thth_auth・thth_refresh は T1 では作らない（§4）。

MCP の stdio トランスポートは改行区切りの JSON-RPC 2.0（1 メッセージ 1 行、
埋め込み改行なし）。追加の依存を持ち込まないため（標準ライブラリのみ・発注 §0-4）
最小限のループを自前で書く。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# **CLI の在り処は 2 通りある**（設計 v2 §3・`pip install thth`）。
#
#   - repo をそのまま使う運用: このファイルの 1 つ上に `bin/thth` がある（従来）。
#   - `pip install thth`: wheel の中では**このファイル自身が `thth/mcp_server.py`**
#     として入る（`pyproject.toml` の force-include）。隣に `bin/` は無い。
#
# **無いものを指したまま subprocess を起こさない。** `bin/thth` が実在するときだけ
# それを使い、無ければ `python -m thth` に落とす（`thth/__main__.py`・同じ `main()`）。
# `THTH_BIN` は従来どおり環境変数で上書きできる（テストはこの属性を差し替える）。
_REPO_BIN = os.path.join(APP_DIR, "bin", "thth")
THTH_BIN = os.environ.get("THTH_BIN") or (_REPO_BIN if os.path.exists(_REPO_BIN) else None)

# 観測の地図（設計 3.5.0 §3）の読む口。手元の stdio とサーバ型の利用者の両方に同じ
# 説明と形で出す（サーバ型では credential が許した project だけ）。
MAP_SHOW_DESCRIPTION = (
    "話題を選ぶ前に呼ぶ。観測の地図（点＝人が決めた話題・線＝包含と共起）に、自分の投稿の"
    "数字（topic ごとの投稿数と views・likes・replies の中央値と n）と、広場の施策・気づき・"
    "追試の数を重ねて返す。読むだけ。世間の層は管理者が有効にしたときだけ")
MAP_SHOW_SCHEMA = {
    "type": "object",
    "properties": {
        "project": {"type": "string", "description": "project 名（account 名ならその project）"},
        "node": {"type": "string", "description": "この点と隣り合う点だけ（任意）"},
        "since": {"type": "string", "description": "窓の始まり（既定 30d・12w・ISO 時刻）"},
    },
    "required": ["project"],
    "additionalProperties": False,
}

TOOLS = [
    {
        "name": "thth_lint",
        "description": "docs/sns/queue の 1 ファイルを形式検査する（読むだけ・副作用なし）。",
        "inputSchema": {
            "type": "object",
            "properties": {"file": {"type": "string", "description": "queue ファイルのパス"}},
            "required": ["file"],
        },
    },
    {
        "name": "thth_queue",
        "description": "draft/approved/posted/型外 の一覧と、次に出るもの・いつかを返す（読むだけ）。",
        "inputSchema": {
            "type": "object",
            "properties": {"account": {"type": "string", "description": "省略時は全アカウント"}},
        },
    },
    {
        "name": "thth_preview",
        "description": "実際に投げる本文そのもの（媒体の節。前後に何も足さない）を返す（読むだけ）。",
        "inputSchema": {
            "type": "object",
            "properties": {"file": {"type": "string", "description": "queue ファイルのパス"}},
            "required": ["file"],
        },
    },
    {
        "name": "thth_topic_context",
        "description": (
            "原稿のトピックを決める前に**最初に**呼ぶ。原稿と（あれば）記事本文から、"
            "判断の入力を固定して返す。記事がまだ無ければ、取りに行くべき URL と"
            "必要な JSON の形を返す（読むだけ・副作用なし）。"
            "使う順序: context → 足りない資料を取る → evaluate。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "queue ファイルのパス"},
                "article": {"type": "object", "description": "記事証拠（ArticleEvidence）"},
                "article_url": {"type": "string",
                                 "description": "本文に URL が複数ある場合の主対象"},
            },
            "required": ["file"],
        },
    },
    {
        "name": "thth_topic_evaluate",
        "description": (
            "記事本文と候補比較を渡して、**引用が本文に在るか・参照が実在するか・"
            "観測が新しいか・投稿者が偏っていないか**を検査させる。"
            "THTH は候補を作らないし順位も付けない——足りないものを言うだけ。"
            "候補が suitable でなければ別の候補に繰り上げず、比較し直しを求める。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string"},
                "article": {"type": "object"},
                "proposal": {"type": "object", "description": "候補比較（TopicProposal）"},
                "article_url": {"type": "string"},
            },
            "required": ["file", "article", "proposal"],
        },
    },
    {
        "name": "thth_topic_decision",
        "description": "保存済みの判断を読む（読むだけ）。今の原稿と食い違っていれば freshness で言う。",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "description": "decision_id"}},
            "required": ["id"],
        },
    },
    {
        "name": "thth_board",
        "description": "アカウントごとの最終投稿・approved 待ち・inflight・型外の骨を返す（読むだけ）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        # **名前と説明文がそのまま売り文句**（設計 v2 §1 規約 6）。LLM は説明文で
        # 呼ぶかを決めるので、**この文言は設計 v2 §1 の正本から動かさない。**
        "name": "before_you_post",
        # **設計 v2 §1 規約 6 の文言そのまま。** 実装が 1 語でも足すと、
        # 「正本はどっちだ」が始まる（監査 2・B7 で「。言えないことは
        # cannot_say に出る」を足していたのを外した）。**設計書ではなく
        # 実装を戻す。**
        "description": (
            "投稿する前に呼ぶ。この語とこの型で、スレッドがどう伸びたかの実績を、"
            "件数と期間つきで返す"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "account 名"},
                "topic": {"type": "string", "description": "語（トピック）"},
                "kind": {"type": "string", "description": "型（行動・年度付き 等）"},
                "hour_band": {"type": "string",
                               "description": "出す予定の時刻帯（朝・昼・夕・深夜）"},
                "is_reply": {"type": "boolean", "description": "返信として出すか"},
                "window_days": {"type": "integer", "description": "直近何日（既定 30）"},
                "min_n": {"type": "integer",
                           "description": "中央値を返す下限（既定 20）"},
                "goal": {"type": "string", "enum": ["reach", "click", "follow", "reply"],
                         "description": "出す予定の投稿の目的（任意）。広場に同じ goal で他の媒体の"
                                        "観測があれば 1 行（plaza_same_goal）"},
            },
            "required": ["account", "topic"],
        },
    },
    {
        "name": "operations_handoff",
        "description": "ローカル運用記録の状態・承認待ち・inflight・通知未処理を根拠と鮮度の制約付きで返す。tool.capabilities に媒体ごとの添付の対応表が付く（添付を付ける前に読む）。同期・承認・再送はしない",
        "inputSchema": {"type": "object", "properties": {
            "account": {"type": "string"}, "project": {"type": "string"},
            "since_last_read": {"type": "boolean"}}},
    },
    {
        "name": "study_report",
        "description": "ローカル施策JSONの未検証の採用宣言と本人の観測を結ぶ。proposedは集計せず、adoptedも因果効果や投稿承認を証明しない。読むだけ",
        "inputSchema": {
            "type": "object", "required": ["file"],
            "properties": {"file": {"type": "string"}, "min_n": {"type": "integer"}},
        },
    },
    {
        "name": "analytics_report",
        "description": "自分の活動のスナップショットを期間・母数・欠測・根拠つきで返す。ローカル台帳を読むだけ。compare_previous=trueで前期間比較。推奨は含まない",
        "inputSchema": {
            "type": "object",
            "properties": {
                "by": {"type": "string", "enum": ["kind", "hour_band", "topic", "tag", "attachment_kind", "goal"]},
                "compare_previous": {"type": "boolean", "description": "直前の同じ日数との比較（既定false）"},
                "account": {"type": "string"},
                "project": {"type": "string"},
                "window_days": {"type": "integer", "description": "直近何日（既定7）"},
                "min_n": {"type": "integer", "description": "中央値の最小母数（既定5）"},
            },
        },
    },
    {
        # **名前と説明文がそのまま売り文句**（設計「自分の泉」§2.2）。
        # 実装が 1 語でも足したら設計書でなく実装を戻す（§2 の頭書きそのまま）。
        "name": "after_you_posted",
        # **設計「自分の泉」§2.2 の文言そのまま。**
        "description": (
            "出したあとに呼ぶ。この語・この型・この枝で、自分の投稿と返信が"
            "どう受け取られたかを、件数と期間つきで返す"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string",
                            "description": "account 名（project の代わり）"},
                "project": {"type": "string",
                            "description": "この project の account 全部（account の代わり）"},
                "topic": {"type": "string", "description": "語（トピック）"},
                "kind": {"type": "string", "description": "型（行動・年度付き 等）"},
                "hour_band": {"type": "string",
                               "description": "時刻帯（朝・昼・夕・深夜）で絞る"},
                "reply_to": {"type": "string",
                              "description": "この post_id への返信だけに絞る"},
                "author_key": {"type": "string",
                                "description": "この仮名（16 進 16 桁）への返信だけに絞る"},
                "window_days": {"type": "integer", "description": "直近何日（既定 30）"},
                "min_n": {"type": "integer",
                           "description": "中央値を返す下限（既定 5）"},
            },
            # account / project の排他的 OR は validate_arguments() で見る。
        },
    },
    {
        # **名前と説明文がそのまま売り文句**（設計「自分の泉」§2.1）。
        # 実装が 1 語でも足したら設計書でなく実装を戻す（§2 の頭書きそのまま）。
        "name": "thread_read",
        # **設計「自分の泉」§2.1 の文言そのまま。**
        "description": (
            "返信を書く前に呼ぶ。この投稿の枝を、誰が・いつ・何を・誰に向けて"
            "言ったかの順で返す。何も保存しない"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "account 名"},
                "post_id": {
                    "type": "string",
                    "description": (
                        "枝の根の post_id。各 message の already_replied は"
                        " 3 値（T7-3）: object（source 付き。ledger＝絡みの台帳／"
                        "queue＝queue の下書き／thread＝枝の中の自分の返信）＝返した"
                        "／false＝台帳・queue・枝のどこにも見当たらない"
                        "（「返していない」の確定ではない）"
                        "／null＝台帳か queue が読めず判らない"
                        "（理由は provenance.ledgers_unreadable）"),
                },
                "since": {"type": "string", "description": "この時刻以降だけ"},
                "max_messages": {"type": "integer",
                                   "description": "読む上限（既定 200・上限 1000）"},
            },
            "required": ["account", "post_id"],
        },
    },
    {
        # **名前と説明文がそのまま売り文句**（設計「自分の泉」§2.3）。
        # 実装が 1 語でも足したら設計書でなく実装を戻す（§2 の頭書きそのまま）。
        "name": "where_to_appear",
        # **設計「自分の泉」§2.3 の文言そのまま。**
        "description": (
            "絡みに行く先を選ぶ前に呼ぶ。検索の一覧に、自分の履歴（この語・"
            "この相手で何が起きたか）を重ねて返す。選ぶのは呼ぶ側"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string",
                             "description": "account 名（project の代わり）"},
                "project": {"type": "string",
                             "description": "この project の account 全部"
                                            "（account の代わり）"},
                "words": {"type": "array", "items": {"type": "string"},
                           "description": "検索の語（1〜5 個）"},
                "recent": {"type": "boolean",
                            "description": "TOP でなく RECENT（新しい順）で検索する"},
                "limit": {"type": "integer",
                           "description": "1 account・1 語あたりの上限（既定 25）"},
            },
            # **`account` か `project` のどちらか必須**（両方無ければ
            # `validate_arguments()` が `-32602`）。`required` は AND
            # （全部要る）の意味しか持てないので、ここには入れない——OR の
            # 検査は `validate_arguments()` の `where_to_appear` 専用の分岐で行う。
            "required": ["words"],
        },
    },
    {
        # **名前と説明文がそのまま売り文句**（設計「自分の泉」§2.4）。
        # 実装が 1 語でも足したら設計書でなく実装を戻す（§2 の頭書きそのまま）。
        "name": "who_is_this",
        # **設計「自分の泉」§2.4 の文言そのまま。**
        "description": (
            "返信する相手を確かめる。この仮名と自分のアカウントの接触の"
            "回数・時期・反応を返す。発言の内容は持たない"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string",
                             "description": "account 名（project の代わり）"},
                "project": {"type": "string",
                             "description": "この project の account 全部"
                                            "（account の代わり）"},
                "author_key": {"type": "string",
                                "description": "仮名（16 進 16 桁・username の代わり）"},
                "username": {"type": "string",
                              "description": "@ 無しでも可。その場で author_key に"
                                             "写す（保存しない・author_key の代わり）"},
                "profile": {"type": "boolean",
                             "description": "その場で公開プロフィールを引く"
                                            "（Threads だけ・保存しない・既定 false）"},
            },
            # **`account`/`project` と `author_key`/`username`、どちらも
            # 「片方だけ必須」の OR**（`where_to_appear` と同じ理由で
            # `required` には入れない——`validate_arguments()` の
            # `who_is_this` 専用の分岐で見る）。
        },
    },
    {
        # **名前と説明文がそのまま売り文句**（設計 3.1.0 §1・3.3.0 §F で観測に）。
        "name": "thth_observe",
        "description": (
            "セッションの始めと区切りごとに呼ぶ。前回の観測から何があって、いまなにを"
            "すればよいかを、道具が事実だけで 1 枚にする。本文は作らない"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "project 名か account 名"},
                "mark": {"type": "boolean",
                          "description": "栞（前回読んだ時点）を進める（既定 true）"},
            },
            "required": ["target"],
        },
    },
    {
        # 別名（3.3.0 §F）。既存の skill・セッションを壊さないために残す。
        "name": "thth_morning",
        "description": (
            "thth_observe の別名（同じ 1 枚を返す）。セッションの始めと区切りごとに呼ぶ。"
            "前回の観測から何があって、いまなにをすればよいかを、道具が事実だけで 1 枚にする"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "project 名か account 名"},
                "mark": {"type": "boolean",
                          "description": "栞（前回読んだ時点）を進める（既定 true）"},
            },
            "required": ["target"],
        },
    },
    {
        # 観測の地図（設計 3.5.0 §3）。**読むだけ**——点と線は管理者の CLI だけが足す。
        "name": "thth_map_show",
        "description": MAP_SHOW_DESCRIPTION,
        "inputSchema": MAP_SHOW_SCHEMA,
    },
]


class ToolInputError(Exception):
    """要求が受け取れない（JSON-RPC `-32602`・セキュリティ監査 2026-09-14・P2-1）。

    **落ちない・黙らない。** 以前は `arguments["file"]` の `KeyError` や
    `params` が list のときの `AttributeError` が `main()` のループの外まで抜けて、
    **サーバが死んでいた**——クライアントから見ると応答が来ないまま接続が切れる
    （どの要求が悪かったのかも判らない）。1 要求の失敗は 1 要求の error で返し、
    ループは続ける。
    """


def _schema_for(name):
    for tool in TOOLS:
        if tool["name"] == name:
            return tool.get("inputSchema") or {}
    return None


# `inputSchema` の `type` を Python の型に写す。**`bool` は `int` の子**なので、
# 整数の検査から明示的に外す（`True` を `window_days` に通さない）。
_TYPES = {
    "string": ((str,), "文字列"),
    "object": ((dict,), "object"),
    "boolean": ((bool,), "真偽値"),
    "integer": ((int,), "整数"),
    "number": ((int, float), "数"),
    # `where_to_appear` の `words`（T2-3）で初めて使う型。**配列だけを通す**
    # ——足しておかないと `_TYPES.get(...)` の既定（`(object,)`）に落ちて、
    # 文字列 1 個を渡しても素通りしてしまう。
    "array": ((list,), "配列"),
}


def ledger_roots() -> list:
    """台帳の `repo_dir` の realpath 一覧（`file` 引数の**唯一の行き先**）。

    セキュリティ監査 2026-09-14・P2-3。`thth_preview`・`thth_lint`・
    `thth_topic_context` の `file` は**どこでも指せた**——MCP を持つ LLM が
    `/etc/passwd` や `~/.config/thth/*.token` を渡せば、`thth preview` は
    front-matter が読めないと言うだけだが、**`thth lint` は中身の断片を返す**。
    読むだけの道具でも、読む先は縛る。

    `queue_dir` は台帳で `repo_dir` からの相対なので、`repo_dir` の下で足りる。
    """
    try:
        if APP_DIR not in sys.path:
            sys.path.insert(0, APP_DIR)
        from thth import accounts as accounts_mod
    except ImportError:
        return []
    try:
        names = accounts_mod.list_account_names()
    except Exception:                              # noqa: BLE001（台帳が読めない）
        return []
    roots = []
    for account in names:
        try:
            cfg = accounts_mod.load_account(account)
        except Exception:                          # noqa: BLE001（1 本壊れていても続ける）
            continue
        # **正規の函数を通す**（監査 2026-09-16）。ここが台帳の `repo_dir` を
        # 生のまま `os.path.realpath()` していたので、相対パスは**この
        # プロセスの cwd** 基準で畳まれていた。`thth/accounts.py::resolved_repo_dir()`
        # は `$THTH_ROOT` 基準——**同じ台帳で許可する場所が MCP だけ食い違って
        # いた**（cwd は systemd 経由なら `/`、手で打てば人それぞれ）。
        repo_dir = accounts_mod.resolved_repo_dir(cfg)
        if repo_dir:
            roots.append(os.path.realpath(repo_dir))
    return roots


def check_file_argument(name: str, path: str) -> None:
    """`file` は**台帳のどれかの `repo_dir` の下**だけ（外れれば `ToolInputError`）。

    `realpath` してから比べる（symlink・`..`・相対パスを畳んでから見る）。
    """
    real = os.path.realpath(path)
    roots = ledger_roots()
    for root in roots:
        if real == root or real.startswith(root + os.sep):
            return
    raise ToolInputError(
        f"{name}: file は台帳の repo_dir の中のパスだけを渡してください"
        f"（受け取ったものはどの repo_dir にも入っていません）")


def validate_arguments(name: str, arguments) -> dict:
    """道具の `inputSchema` どおりの引数か。違えば `ToolInputError`。

    見るのは 3 つだけ: **object であること**・**必須が揃っていること**・
    **型が合っていること**（知らない鍵も断る——綴りを間違えたまま既定値で
    動いたことにしない）。判断はしない（中身の意味は CLI の仕事）。
    """
    schema = next((tool["inputSchema"] for tool in ADMIN_TOOLS if tool["name"] == name), None) or _schema_for(name)
    if schema is None:
        return {}                     # 知らない道具は `call_tool()` が断る
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ToolInputError(
            f"{name}: arguments は object で渡してください"
            f"（受け取った: {type(arguments).__name__}）")
    if name == "analytics_report" and "by" in arguments:
        if arguments["by"] not in ("kind", "hour_band", "topic", "tag", "attachment_kind", "goal") or arguments.get("compare_previous") is not True:
            raise ToolInputError("analytics_report: by は compare_previous=true と kind/hour_band/topic/tag/attachment_kind/goal が必要です")
    if name == "operations_handoff" and "since_last_read" in arguments and type(arguments["since_last_read"]) is not bool:
        raise ToolInputError("operations_handoff: since_last_read は boolean です")
    props = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if arguments.get(key) is None:
            raise ToolInputError(f"{name}: 必須の引数がありません: {key}")
    for key, value in arguments.items():
        spec = props.get(key)
        if spec is None:
            raise ToolInputError(f"{name}: 知らない引数です: {key}")
        if value is None:
            continue
        types, 名 = _TYPES.get(spec.get("type"), ((object,), "値"))
        if spec.get("type") == "integer" and isinstance(value, bool):
            raise ToolInputError(f"{name}: {key} は{名}で渡してください（受け取った: bool）")
        if not isinstance(value, types):
            raise ToolInputError(
                f"{name}: {key} は{名}で渡してください"
                f"（受け取った: {type(value).__name__}）")
        # **位置引数に渡る値が `-` で始まらない**（監査 2026-09-14・P3-2）。
        # `run_cli()` は値を argv の位置に置く。`--json` のような綴りを渡されると
        # **CLI の別の旗として読まれる**（`thth lint --help` のように無害な形も
        # あるが、旗の意味は CLI 側の都合で増える）。`--` を足して黙って通すより、
        # **受け取れないものとして断る**（作法 5）。
        if isinstance(value, str) and value.startswith("-"):
            raise ToolInputError(
                f"{name}: {key} が `-` で始まっています（{value!r}）。"
                f"CLI の旗と区別できないので受け取りません")
        if (spec.get("type") == "array"
                and (spec.get("items") or {}).get("type") == "string"):
            # **`words` の各要素も同じ理由で縛る**（監査 2026-09-14・P3-2 と同じ筋・
            # T2-3）。`call_tool()` は要素を argv にそのまま並べるので、ここで
            # 文字列でないもの・`-` で始まるものを通すと `thth where` の別の旗と
            # 区別できなくなる。
            for item in value:
                if not isinstance(item, str):
                    raise ToolInputError(
                        f"{name}: {key} の要素は文字列です"
                        f"（受け取った: {type(item).__name__}）")
                if item.startswith("-"):
                    raise ToolInputError(
                        f"{name}: {key} の要素が `-` で始まっています（{item!r}）。"
                        f"CLI の旗と区別できないので受け取りません")
        if key == "file":
            check_file_argument(name, value)
    if name in ("operations_handoff", "analytics_report", "after_you_posted", "where_to_appear", "who_is_this") \
            and not arguments.get("account") \
            and not arguments.get("project"):
        # **`account` か `project` のどちらか必須**（`required` は AND の意味しか
        # 持てないので、ここで OR を見る・T2-3 発注書・T3-3 も同じ形）。
        raise ToolInputError(
            f"{name}: account か project のどちらかが要ります")
    if name in ("operations_handoff", "analytics_report", "after_you_posted") and arguments.get("account") \
            and arguments.get("project"):
        raise ToolInputError(
            f"{name}: account と project は同時に指定できません")
    if name == "who_is_this" and not arguments.get("author_key") \
            and not arguments.get("username"):
        # **`author_key` か `username` のどちらか必須**（同じ理由で OR を
        # ここで見る・T3-3 発注書）。
        raise ToolInputError(
            f"{name}: author_key か username のどちらかが要ります")
    return arguments


def cli_argv(args: list) -> list:
    """CLI を起こす argv。`THTH_BIN` があればそれ、無ければ `python -m thth`。"""
    head = [sys.executable, THTH_BIN] if THTH_BIN else [sys.executable, "-m", "thth"]
    return [*head, *args]


def run_cli(args: list, *, stdin_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cli_argv(args), capture_output=True,
                           text=True, input=stdin_text)


def _topic_stdin(arguments: dict, *, with_proposal: bool) -> tuple:
    """記事・候補比較は**標準入力に 1 つの JSON** で渡す（設計 §7）。

    外部モデルが指定した任意のサーバファイルへ書いて受け渡さない。stdin は 1 本
    しかないので、記事と候補比較を別々の口にせず 1 つの封筒にまとめる
    （**設計からの変更・理由はここ**）。CLI 側はファイル入力と同じ検査関数へ
    合流する。
    """
    args = ["topics", "suggest", arguments["file"], "--input-json-stdin"]
    if arguments.get("article_url"):
        args += ["--article-url", arguments["article_url"]]
    payload = {"article": arguments.get("article")}
    if with_proposal:
        payload["proposal"] = arguments.get("proposal")
    return args, json.dumps(payload, ensure_ascii=False)


ADMIN_TOOLS = [
    {"name": "thth_admin_" + name, "description": "Read-only administrator " + name,
     "inputSchema": {"type": "object", "properties":
         ({"account": {"type": "string"}, "limit": {"type": "integer"}} if name == "account" else
          {"account": {"type": "string"}, "since": {"type": "string"}, "event": {"type": "string"}} if name == "log" else
          {"since_last_read": {"type": "boolean"}} if name == "diff" else {}),
         "required": ["account"] if name == "account" else [], "additionalProperties": False}}
    for name in ("inventory", "account", "log", "tokens", "release", "diff")]


ADMIN_TOOLS.append({'name':'thth_admin_budget_set','description':'Set the X monthly read estimate cap (kind x_read, default) or the monthly post count cap (kind x_posts); administrator only, by required; no provider call',
    'inputSchema':{'type':'object','properties':{key:{'type':'string'} for key in ('monthly','currency','rate','rate_source','by','kind')},
                   'required':['monthly','by'],'additionalProperties':False}})

# 監視語（設計 3.1.0 §3）。**語は管理者が入れる**——読む口（`thth_morning`）
# からは変えられない。presence-only で管理記録に残る。
ADMIN_TOOLS.append({'name':'thth_admin_watch_set','description':'Replace the watch words of one account (max 5 words, 40 chars each); administrator only, by required; recorded presence-only; no provider call',
    'inputSchema':{'type':'object','properties':{'account':{'type':'string'},'by':{'type':'string'},
                   'words':{'type':'array','items':{'type':'string'}}},
                   'required':['account','words','by'],'additionalProperties':False}})

# 報告の口の実装側（設計 3.1.2 §1）。返事と閉じるは `by` 必須・変更ログに
# presence-only で残る。書き出し（export）は repo に書くので CLI だけ。
ADMIN_TOOLS += [
    {'name':'thth_admin_reports_list','description':'List bug reports and requests from every project (status open by default, or closed/all); administrator only; read-only',
     'inputSchema':{'type':'object','properties':{'status':{'type':'string','enum':['open','closed','all']}},
                    'required':[],'additionalProperties':False}},
    {'name':'thth_admin_reports_show','description':'Read one report with its body, reproduction steps and replies; administrator only; read-only',
     'inputSchema':{'type':'object','properties':{'report_id':{'type':'string'}},
                    'required':['report_id'],'additionalProperties':False}},
    {'name':'thth_admin_reports_reply','description':'Add a reply to one report (shown to the reporter in operations_handoff tool.reports); administrator only, by required; refused if the text looks like a secret',
     'inputSchema':{'type':'object','properties':{'report_id':{'type':'string'},'text':{'type':'string'},'by':{'type':'string'}},
                    'required':['report_id','text','by'],'additionalProperties':False}},
    {'name':'thth_admin_reports_close','description':'Close one report with a reason (fixed, wontfix, duplicate, invalid) and the version that settled it; administrator only, by required',
     'inputSchema':{'type':'object','properties':{'report_id':{'type':'string'},'by':{'type':'string'},
                    'reason':{'type':'string','enum':['fixed','wontfix','duplicate','invalid']},'version':{'type':'string'}},
                    'required':['report_id','reason','version','by'],'additionalProperties':False}},
]

# 施策の広場の管理者側（設計 3.4.0 §4）。参加・退出・非表示は `by` 必須・変更ログに
# presence-only で残る。
ADMIN_TOOLS += [
    {'name':'thth_admin_plaza_list','description':'List every plaza post (measures, findings, questions) including hidden ones, and the joined projects; administrator only; read-only',
     'inputSchema':{'type':'object','properties':{},'required':[],'additionalProperties':False}},
    {'name':'thth_admin_plaza_show','description':'Read one plaza post as stored (original text, observations, replies); administrator only; read-only',
     'inputSchema':{'type':'object','properties':{'plaza_id':{'type':'string'}},
                    'required':['plaza_id'],'additionalProperties':False}},
    {'name':'thth_admin_plaza_join','description':'Let one project (owner) read and post open plaza entries; administrator only, by required; default is not joined',
     'inputSchema':{'type':'object','properties':{'project':{'type':'string'},'by':{'type':'string'}},
                    'required':['project','by'],'additionalProperties':False}},
    {'name':'thth_admin_plaza_leave','description':'Remove one project (owner) from the open plaza; its open entries stop being visible to others; administrator only, by required',
     'inputSchema':{'type':'object','properties':{'project':{'type':'string'},'by':{'type':'string'}},
                    'required':['project','by'],'additionalProperties':False}},
    {'name':'thth_admin_plaza_hide','description':'Hide one plaza post with a reason (the owner still sees it with the reason); administrator only, by required',
     'inputSchema':{'type':'object','properties':{'plaza_id':{'type':'string'},'reason':{'type':'string'},'by':{'type':'string'}},
                    'required':['plaza_id','reason','by'],'additionalProperties':False}},
    # 持ち主の組（設計 3.8.0 §A）。管理者が登録する（道具は推測しない）・既定は組なし。
    {'name':'thth_admin_plaza_owner_set','description':'Register one owner group (projects of the same owner read each other\'s owner-scope plaza posts); replaces the group\'s projects; one project belongs to one group; administrator only, by required',
     'inputSchema':{'type':'object','properties':{'owner':{'type':'string'},'projects':{'type':'array','items':{'type':'string'}},'by':{'type':'string'}},
                    'required':['owner','projects','by'],'additionalProperties':False}},
    {'name':'thth_admin_plaza_owner_unset','description':'Dissolve one owner group; its owner-scope posts stop being visible to the other projects; administrator only, by required',
     'inputSchema':{'type':'object','properties':{'owner':{'type':'string'},'by':{'type':'string'}},
                    'required':['owner','by'],'additionalProperties':False}},
]

SERVER_TOOLS = [
    {"name":"thth_"+name,"description":"Scoped server "+name,
     "inputSchema":{"type":"object","properties":{key:{"type":"string"} for key in ("account",*keys)},
                    "required":["account",*required],"additionalProperties":False}}
    for name,keys,required in (
        ('draft_put',('body','publish_at','topic','reply_to','draft_id','expected_revision'),('body','publish_at')),
        ('approval_request',('draft_id',),('draft_id',)),
        ('send_request',('body','topic','reply_to'),('body',)),
        ('retract_request',('post_id','reason'),('post_id','reason')),
        ('draft_list',(),()),('queue',(),()),('request_status',('job_id',),('job_id',))) ]

# 添付の 2 本だけは型が文字列でないので、表に足さず個別に書く（日本語 1 行）。
SERVER_TOOLS += [
    {"name":"thth_media_upload_url",
     "description":"添付を置く一回限りのアップロード URL を発行する（添付を付ける前に tool.capabilities を読む。種類・大きさ・sha256 を先に申告。URL はこの応答にだけ出る・10 分で失効・もう一度呼ぶと別の URL）",
     "inputSchema":{"type":"object","properties":{"account":{"type":"string"},"kind":{"type":"string"},
                    "size":{"type":"integer"},"sha256":{"type":"string"},"mime":{"type":"string"}},
                    "required":["account","kind","size","sha256","mime"],"additionalProperties":False}},
    {"name":"thth_media_complete",
     "description":"置き終えた添付を確定する（サーバが読み戻して sha256 を照合し、位置情報などを落としてから repo に置く。公開 sha を返す）",
     "inputSchema":{"type":"object","properties":{"account":{"type":"string"},"media_id":{"type":"string"}},
                    "required":["account","media_id"],"additionalProperties":False}},
]
# `media` だけは配列（`[{media_id, alt}]`・alt は必須）。
for _tool in SERVER_TOOLS:
    if _tool["name"] == "thth_draft_put":
        _tool["inputSchema"]["properties"]["media"] = {"type":"array"}
        _tool["description"] = "下書きを置く（media に確定済みの添付 [{media_id, alt}] を並べられる。alt は必須。添付を付ける前に tool.capabilities を読む）"
del _tool



# 報告の口の利用者側（設計 3.1.2 §1）。**サーバ型だけに出す**——手元の stdio
# （pip 版）で置いても実装側には届かないので、届かない口は並べない。誰が置いたかは
# credential の `actor` が決める（要求の欄からは作らない）。
REPORT_TOOLS = [
    {"name": "thth_report_file",
     "description": "道具が断った・結果が期待と違った・欲しい形がある・迷った、のどれかならこれで置く。"
                    "つまずき・迷い・期待との違いの報告は、利用者の作業の一部として歓迎します。"
                    "小さいものも。重複は道具が束ねます。"
                    "例: 「止まったのに気づかなかった」「断られた理由が分からなかった」"
                    "「同じ操作を 3 回繰り返した」。断られた直後なら from_last_refusal: true で"
                    "道具が控えた断りを再現手順に添えられる"
                    "（kind は bug・request・friction〔迷った・分かりにくかった・同じ操作を繰り返した〕。"
                    "実装側が読み、返事は thth_report_show と "
                    "operations_handoff の tool.reports に出る。秘密らしき値が含まれていたら置かない）",
     "inputSchema": {"type": "object", "properties": {
         "account": {"type": "string"},
         "kind": {"type": "string", "enum": ["bug", "request", "friction"]},
         "title": {"type": "string", "description": "1 行・120 字まで"},
         "body": {"type": "string", "description": "8,000 字まで（from_last_refusal のときは省略可）"},
         "repro": {"type": "string", "description": "再現手順（任意・4,000 字まで）"},
         "from_last_refusal": {"type": "boolean",
                               "description": "この account で直前に道具が断った記録（時刻・版・命令・"
                                              "理由の符丁）を再現手順として添える。kind の既定は friction"}},
         "required": ["account", "title"], "additionalProperties": False}},
    {"name": "thth_report_list",
     "description": "自分の project の報告と、実装側の返事の数を読む（読むだけ）",
     "inputSchema": {"type": "object", "properties": {
         "status": {"type": "string", "enum": ["open", "closed", "all"]}},
         "additionalProperties": False}},
    {"name": "thth_report_show",
     "description": "報告 1 件の本文と実装側の返事を読む（読むだけ）",
     "inputSchema": {"type": "object", "properties": {"report_id": {"type": "string"}},
                     "required": ["report_id"], "additionalProperties": False}},
    # つまずきの年表（設計 3.6.0 §B）。読むだけ・自分の project の範囲だけ。
    {"name": "thth_report_timeline",
     "description": "つまずきの年表: 自分の project の報告（置いた日・種類・題・閉じた版）と、"
                    "リリースノートの報告 id・「〜の実測から」を日付で並べる（読むだけ）。材料は道具の中の"
                    "記録と repo の docs だけ。始めて 2 週間の件数と、閉じるまでの日数の中央値と n。"
                    "新しい持ち主の最初の 2 週間は、広場の open に参加した持ち主の年表の中央値（件数と"
                    "日数だけ）。他の持ち主の報告は出さない",
     "inputSchema": {"type": "object", "properties": {
         "since": {"type": "string", "description": "この日（YYYY-MM-DD）から並べる（集計は全部）"}},
         "additionalProperties": False}},
    # 報告した側の追記（設計 3.2.0 §4.5-1）。
    {"name": "thth_report_add",
     "description": "自分の project の開いている報告に書き足す（返事と同じ列に並ぶ・4,000 字まで・"
                    "閉じた報告には足せない・秘密らしき値が含まれていたら足さない）",
     "inputSchema": {"type": "object", "properties": {
         "report_id": {"type": "string"},
         "text": {"type": "string", "description": "4,000 字まで"}},
         "required": ["report_id", "text"], "additionalProperties": False}},
]


# 施策の広場の利用者側（設計 3.4.0 §4）。**サーバ型だけに出す**（報告の口と同じ——
# 置き場は VM の私有）。誰が書いたかは credential の `actor`。読めるのは credential が
# 許した account の持ち主（project）の書き込みと、参加していれば open の写しだけ。
PLAZA_WELCOME = ("施策を試したら広場に置き、次を決める前に他の媒体の施策を読む——"
                 "どちらも利用者の作業の一部です")
PLAZA_TOOLS = [
    {"name": "thth_plaza_post",
     "description": PLAZA_WELCOME + "。施策（measure: 観測は道具が study-report と同じ計算で付ける・"
                    "how〔数字を出し直せる thth の命令〕が必須）・気づき（finding）・問い（question）を"
                    "置く。scope（媒体・企画の範囲）は必須。置いたものは同じ持ち主の全 account だけに"
                    "見える（visibility: owner は管理者が登録した持ち主の組の全 project に一段で見せる。"
                    "project が組に入っていれば visibility 省略の既定は owner・入っていなければ"
                    "従前どおり project（設計 3.8.1）。他の持ち主に見せる open は人が CLI の二段確認"
                    "で行う・この口には無い）。"
                    "evidence_level の observed は道具だけが付ける。from_tool（analytics-report・after・"
                    "study-report）・from_doc・from_report で、道具がその出力を置く時点で作り直して"
                    "数字を観測の欄と本文の下書きに入れる（body は任意の解釈）",
     "inputSchema": {"type": "object", "properties": {
         "account": {"type": "string"},
         "kind": {"type": "string", "enum": ["measure", "finding", "question"]},
         "title": {"type": "string", "description": "1 行・120 字まで"},
         "body": {"type": "string", "description": "8,000 字まで"},
         "scope": {"type": "string", "description": "媒体・企画の範囲（1 行・120 字・必須）"},
         "kind_detail": {"type": "string", "enum": ["pattern", "rule", "pitfall", "tool_tip"],
                         "description": "finding の細目（任意）"},
         "how": {"type": "string", "description": "数字を出し直せる thth の命令 1 行（measure は必須・実行しない）"},
         "evidence_level": {"type": "string", "enum": ["stated", "hypothesis"]},
         "declarations": {"type": "array", "description": "measure の宣言（study-report の形・媒体ごとに 1 つ）"},
         "hypothesis": {"type": "string"}, "change": {"type": "string"},
         "until": {"type": "string", "description": "期間の終わり（timezone 付きの時刻）"},
         "min_n": {"type": "integer"},
         "goal": {"type": "string", "enum": ["reach", "click", "follow", "reply"],
                  "description": "施策の目的（任意）。媒体をまたいで同じ目的の施策を比べる札"},
         "visibility": {"type": "string", "enum": ["project", "owner"],
                        "description": "project か owner（持ち主の組・管理者が組を登録したときだけ）。"
                                       "省略時は project が組に入っていれば owner・入っていなければ project"},
         "trial_due": {"type": "string", "description": "finding の追試の予定日（2026-10-08 か timezone 付きの時刻）"},
         "from_tool": {"type": "string", "enum": ["analytics-report", "after", "study-report"],
                       "description": "道具の出力から置く（analytics-report・after は from_account・"
                                      "study-report は declarations の 1 つ目）"},
         "from_account": {"type": "string", "description": "from_tool の account（同じ持ち主）"},
         "from_window_days": {"type": "integer"},
         "from_doc": {"type": "string", "description": "repo の中の md の相対パス（finding として・commit 済みのもの）"},
         "from_report": {"type": "string", "description": "自分の project の閉じた報告の report_id（tool_tip として）"}},
         "required": ["account", "scope"], "additionalProperties": False}},
    {"name": "thth_plaza_list",
     "description": "広場の一覧（既定は自分の持ち主の書き込み・open: true は open の広場）。次を決める前に"
                    "他の媒体の施策を読む（読むだけ）",
     "inputSchema": {"type": "object", "properties": {
         "project": {"type": "string"}, "open": {"type": "boolean"}},
         "additionalProperties": False}},
    {"name": "thth_plaza_show",
     "description": "広場の 1 件（本文・道具が付けた観測・媒体をまたぐ比較の表・追試の数・返信）を読む（読むだけ）",
     "inputSchema": {"type": "object", "properties": {"plaza_id": {"type": "string"}},
                     "required": ["plaza_id"], "additionalProperties": False}},
    {"name": "thth_plaza_reply",
     "description": "返信を 1 つ足す（comment・agree・disagree〔理由必須〕・tried〔自分の measure の id 必須〕・"
                    "trial〔追試: result に reproduced・not_reproduced・not_tried。再現しなかった報告も"
                    "同じ重さで数える〕）。project 範囲の書き込みにだけ返せる（open の書き込みへの返信は"
                    "他の持ち主にも見えるので、人の CLI の二段確認だけ・open_requires_cli）",
     "inputSchema": {"type": "object", "properties": {
         "plaza_id": {"type": "string"}, "account": {"type": "string"},
         "kind": {"type": "string", "enum": ["comment", "tried", "agree", "disagree", "trial"]},
         "text": {"type": "string", "description": "4,000 字まで"},
         "measure_id": {"type": "string"},
         "result": {"type": "string", "enum": ["reproduced", "not_reproduced", "not_tried"]}},
         "required": ["plaza_id", "account", "kind"], "additionalProperties": False}},
    {"name": "thth_plaza_update",
     "description": "自分の持ち主の施策の結果を取り直す（refresh）・判定（verdict: adopted・dropped・"
                    "inconclusive と reason）・project の範囲に戻す（visibility: project。open への切り替えは"
                    "人の CLI の二段確認だけ。open の書き込みで他の持ち主に見える写しが変わる更新も同じ・"
                    "open_requires_cli）",
     "inputSchema": {"type": "object", "properties": {
         "plaza_id": {"type": "string"}, "account": {"type": "string"},
         "refresh": {"type": "boolean"},
         "verdict": {"type": "string", "enum": ["adopted", "dropped", "inconclusive"]},
         "reason": {"type": "string"},
         "visibility": {"type": "string", "enum": ["project", "owner"]}},
         "required": ["plaza_id", "account"], "additionalProperties": False}},
    {"name": "thth_plaza_digest",
     "description": "生きたコツ集: 2 媒体以上で再現した施策・気づき（再現しなかった媒体も並べる・分母つき）。"
                    "新しいセッションが最初に読むもの（読むだけ）。target は自分の project か持ち主の組の名前",
     "inputSchema": {"type": "object", "properties": {"target": {"type": "string"}},
                     "additionalProperties": False}},
]


# **うまくいかなければ報告の口へ**（設計 3.1.2 §3.5）。利用者の道具の説明文の
# 末尾に 1 句。定数（TOOLS 等）は書き換えず、並べるときに足す——「売り文句」の
# 正本（設計 v2 §1・「自分の泉」§2）はそのまま残る。
REPORT_HINT = "（うまくいかなければ thth_report_file）"


def _with_report_hint(tools):
    return [tool if tool["name"].startswith("thth_report_")
            else dict(tool, description=tool["description"] + REPORT_HINT) for tool in tools]


def _channel_note(account=None, reason=None):
    """断りの応答に添える 2 つ目の text。本文は入れない。

    account（credential が許した・台帳にある名前）と理由の符丁が分かれば、そのまま
    呼べる `thth_report_file` の 1 行（設計 3.3.0 B3）。分からなければ静的な 1 行。
    """
    if APP_DIR not in sys.path:
        sys.path.insert(0, APP_DIR)
    from thth.report_inbox import mcp_refusal_line
    return {"type": "text", "text": mcp_refusal_line(account, reason)}


def server_mode():
    return 'THTH_REPORT_CREDENTIALS' in os.environ or 'THTH_REPORT_TOKEN' in os.environ


def server_tools(context):
    if context is None: return []
    reports = [tool for tool in TOOLS if tool['name'] in
               ('analytics_report', 'operations_handoff', 'study_report')]
    if context.scope=='admin': return reports + ADMIN_TOOLS
    # 毎朝の一枚は**利用者の scope だけ**（設計 3.1.0 §1）。credential が許した
    # account／project しか対象にできない（`report_service.execute_morning()`）。
    reports = reports + [tool for tool in TOOLS if tool['name'] in ('thth_observe', 'thth_morning',
                                                                     'thth_map_show')]
    from thth.server_writes import WRITE_OPERATIONS
    return _with_report_hint(reports + [tool for tool in SERVER_TOOLS
                      if context.writes or tool['name'][5:] not in WRITE_OPERATIONS]
                      + PLAZA_TOOLS) + REPORT_TOOLS


def server_call(name, arguments):
    from thth.report_service import execute_report, execute_mcp_report, ReportServiceError
    from thth.server_writes import execute, WRITE_OPERATIONS, SAFE_ERRORS, DRAFT_REASONS
    context=authenticated_context()

    def failure(value):
        # 断りは 1 つ目の text が静的な理由、2 つ目が受け口の案内（設計 3.1.2 §3.5）。
        account, code = _remember_refusal(context, name, arguments, value)
        return {'content':[{'type':'text','text':value},_channel_note(account, code)],'isError':True}
    if context is None: return failure('unauthorized')
    tool=next((tool for tool in server_tools(context) if tool['name']==name),None)
    if tool is None: return failure('unsupported_operation')
    if arguments is None: arguments={}
    schema=tool['inputSchema']
    if (type(arguments) is not dict or set(arguments)-set(schema['properties'])
            or not set(schema.get('required',[]))<=set(arguments)):
        return failure('invalid_request')
    for key,value in arguments.items():
        kind=schema['properties'][key].get('type')
        # admin_log owns the event enum and its invalid_options distinction.
        # Let every invalid event type reach that validation before log I/O.
        if name == 'thth_admin_log' and key == 'event':
            continue
        if value is not None and (kind=='string' and not isinstance(value,str)
                or kind=='integer' and type(value) is not int or kind=='boolean' and type(value) is not bool):
            return failure('invalid_request')
    operation=name[5:] if name.startswith('thth_') else name
    request={**arguments,'operation':operation}
    try:
        if operation in ('admin_budget_set','admin_watch_set'):
            from thth.report_service import execute_admin_write
            result=execute_admin_write(context,request)
        elif operation in WRITE_OPERATIONS:
            result=execute(context,request,via='mcp')
        elif operation.startswith('admin_reports_'):
            from thth.report_service import execute_admin_reports
            result=execute_admin_reports(context,request)
        elif operation.startswith('admin_plaza_'):
            from thth.report_service import execute_admin_plaza
            result=execute_admin_plaza(context,request)
        elif operation.startswith('plaza_'):
            from thth.report_service import execute_user_plaza
            result=execute_user_plaza(context,request)
        elif operation in ('report_file','report_list','report_show','report_add','report_timeline'):
            from thth.report_service import execute_user_reports
            result=execute_user_reports(context,request)
        elif operation == 'map_show':
            from thth.report_service import execute_map_show
            result=execute_map_show(context,request)
        elif operation in ('morning', 'observe'):
            from thth.report_service import execute_morning
            request={**request,'operation':'morning'}
            result=execute_morning(context,request,invoked_as=operation)
        elif operation in ('analytics_report', 'operations_handoff', 'study_report'):
            result=execute_mcp_report(context,request)
        else:
            result=execute_report(context,request)
        return {'content':[{'type':'text','text':json.dumps(result,ensure_ascii=False,allow_nan=False)}]}
    except ReportServiceError as exc:
        if str(exc)=='invalid_draft':
            # 理由は静的な符丁の表にあるものだけ。lint の自由文は通さない。
            detail=getattr(exc,'reason',None)
            return failure('invalid_draft: '+(detail if detail in DRAFT_REASONS else 'validation_failed'))
        from thth.report_inbox import REASONS as REPORT_REASONS
        if str(exc)=='duplicate_report' and getattr(exc,'report_id',None):
            # 既存の id は同じ account の報告だけ（重複の検査がそう絞っている）。
            return failure('duplicate_report: '+exc.report_id)
        from thth.plaza import REASONS as PLAZA_REASONS
        if str(exc)=='duplicate_post' and getattr(exc,'plaza_id',None):
            # 既存の id は同じ account の書き込みだけ（重複の検査がそう絞っている）。
            return failure('duplicate_post: '+exc.plaza_id)
        if str(exc) in PLAZA_REASONS:
            return failure(str(exc))
        from thth.map_store import REASONS as MAP_REASONS
        if str(exc) in MAP_REASONS:
            return failure(str(exc))
        return failure(str(exc) if str(exc) in SAFE_ERRORS or str(exc) in REPORT_REASONS or str(exc) in ('budget_change_durability_unconfirmed','budget_change_partially_recorded','budget_change_refused','watch_change_refused') else 'request_unavailable')
    except Exception:
        return failure('request_unavailable')


def _remember_refusal(context, name, arguments, value):
    """サーバ型の断りを account ごとに控える（設計 3.3.0 B2）。控えの失敗で断りを変えない。

    戻り値は断りの 2 つ目の text に埋める `(account, 符丁)`（B3・分からなければ None）。

    account は credential が許した名前のときだけ。残すのは道具の名前と、断りの
    先頭の静的な符丁だけ（`thth/refusals.py`・引数の値は残さない）。報告の口の
    断りは控えない（報告しようとしていた元の断りを押し出さないため）。
    """
    try:
        if context is None or not isinstance(arguments, dict) or not isinstance(name, str):
            return None, None
        account = arguments.get('account')
        if not isinstance(account, str) or account not in (context.allowed_accounts or {}):
            return None, None
        if not name.replace('_', '').isalnum():
            return None, None
        from thth import refusals
        command = 'mcp ' + name
        values = [v for v in arguments.values() if isinstance(v, str)]
        code = refusals.reason_code(value, argv=values, command=command, rc=2)
        if not name.startswith('thth_report_'):
            refusals.record(account, command=command, reason_code=code)
        return account, code
    except Exception:
        return None, None


def admin_context():
    context=authenticated_context()
    return context if context is not None and context.scope=='admin' else None


def authenticated_context():
    """Authenticate the startup environment, rechecked on list and every call."""
    import hashlib
    import hmac
    from datetime import datetime, timezone
    from pathlib import Path
    path = os.environ.get("THTH_REPORT_CREDENTIALS")
    token = os.environ.get("THTH_REPORT_TOKEN")
    if not path or not token:
        return None
    if APP_DIR not in sys.path:
        sys.path.insert(0, APP_DIR)
    from thth.report_http import load_credentials
    from thth.report_isolation import validate_environment
    try:
        root, credentials = load_credentials(Path(path))
        digest = hashlib.sha256(token.encode()).hexdigest()
        for expected, expiry, revoked, context in credentials:
            if hmac.compare_digest(digest, expected) and not revoked and datetime.now(timezone.utc) < expiry:
                validate_environment(root, context.allowed_accounts, allow_unreadable=context.scope=="admin", allow_empty=True)
                return context
    except (OSError, ValueError, TypeError):
        pass
    return None


def call_tool(name: str, arguments: dict | None) -> dict:
    """CLI を呼んで結果を返すだけ。判断（条件分岐・整形）をここに書かない。"""
    if server_mode():
        return server_call(name, arguments)
    arguments = arguments or {}
    if isinstance(name, str) and name.startswith("thth_admin_"):
        context = admin_context()
        if context is None:
            return {"content": [{"type": "text", "text": "unauthorized"}, _channel_note()], "isError": True}
        from thth.report_service import execute_report, ReportServiceError
        try:
            if "operation" in arguments: raise ReportServiceError("invalid_request")
            request = {**arguments, "operation": name[len("thth_"):]}
            result = execute_report(context, request)
            return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
        except (ValueError, TypeError, ReportServiceError) as error:
            reason = "invalid_options" if str(error) == "invalid_options" else "invalid_request"
            return {"content": [{"type": "text", "text": reason}, _channel_note()], "isError": True}
    if name == "thth_lint":
        proc = run_cli(["lint", arguments["file"], "--json"])
        text = proc.stdout
    elif name == "thth_queue":
        args = ["queue"]
        if arguments.get("account"):
            args.append(arguments["account"])
        args.append("--json")
        proc = run_cli(args)
        text = proc.stdout
    elif name == "thth_preview":
        proc = run_cli(["preview", arguments["file"]])
        text = proc.stdout if proc.returncode == 0 else proc.stderr
    elif name in ("thth_topic_context", "thth_topic_evaluate"):
        args, payload = _topic_stdin(
            arguments, with_proposal=(name == "thth_topic_evaluate"))
        proc = run_cli(args, stdin_text=payload)
        text = proc.stdout
    elif name == "thth_topic_decision":
        proc = run_cli(["topics", "decision", arguments["id"], "--json"])
        text = proc.stdout
    elif name == "thth_board":
        proc = run_cli(["board", "--json"])
        text = proc.stdout
    elif name == "before_you_post":
        args = ["ask", "before-you-post", arguments["account"],
                "--topic", arguments["topic"]]
        if arguments.get("kind"):
            args += ["--kind", arguments["kind"]]
        if arguments.get("hour_band"):
            args += ["--hour-band", arguments["hour_band"]]
        if arguments.get("is_reply"):
            args.append("--reply")
        if arguments.get("window_days") is not None:
            args += ["--window-days", str(arguments["window_days"])]
        if arguments.get("min_n") is not None:
            args += ["--min-n", str(arguments["min_n"])]
        if arguments.get("goal"):
            args += ["--goal", arguments["goal"]]
        args.append("--json")
        proc = run_cli(args)
        text = proc.stdout
    elif name == "operations_handoff":
        args = ["handoff-report"]
        if "mark_read" in arguments or "by" in arguments:
            raise ValueError("operations_handoff is read-only")
        if arguments.get("since_last_read"):
            args.append("--since-last-read")
        if arguments.get("project"):
            args += ["--project", arguments["project"]]
        else:
            args.append(arguments["account"])
        args.append("--json")
        proc = run_cli(args)
        text = proc.stdout
    elif name == "study_report":
        args = ["study-report", arguments["file"]]
        if arguments.get("min_n") is not None:
            args += ["--min-n", str(arguments["min_n"])]
        args.append("--json")
        proc = run_cli(args)
        text = proc.stdout
    elif name == "analytics_report":
        args = ["analytics-report"]
        if arguments.get("project"):
            args += ["--project", arguments["project"]]
        else:
            args.append(arguments["account"])
        for key in ("window_days", "min_n"):
            if arguments.get(key) is not None:
                args += ["--" + key.replace("_", "-"), str(arguments[key])]
        if arguments.get("compare_previous"):
            args.append("--compare-previous")
        if arguments.get("by"):
            args.extend(["--by", arguments["by"]])
        args.append("--json")
        proc = run_cli(args)
        text = proc.stdout
    elif name == "after_you_posted":
        # **`before_you_post` と同じ型**（発注 T0-2）: CLI を `--json` で呼ぶだけ。
        args = ["after"]
        if arguments.get("project"):
            args += ["--project", arguments["project"]]
        else:
            args.append(arguments["account"])
        if arguments.get("topic"):
            args += ["--topic", arguments["topic"]]
        if arguments.get("kind"):
            args += ["--kind", arguments["kind"]]
        if arguments.get("hour_band"):
            args += ["--hour-band", arguments["hour_band"]]
        if arguments.get("reply_to"):
            args += ["--reply-to", arguments["reply_to"]]
        if arguments.get("author_key"):
            args += ["--author-key", arguments["author_key"]]
        if arguments.get("window_days") is not None:
            args += ["--window-days", str(arguments["window_days"])]
        if arguments.get("min_n") is not None:
            args += ["--min-n", str(arguments["min_n"])]
        args.append("--json")
        proc = run_cli(args)
        text = proc.stdout
    elif name == "thread_read":
        # **`after_you_posted` と同じ型**: CLI（`thth thread`）を `--json` で
        # 呼ぶだけ（設計「自分の泉」§2.1・T1-3）。
        args = ["thread", arguments["account"], arguments["post_id"]]
        if arguments.get("since"):
            args += ["--since", arguments["since"]]
        if arguments.get("max_messages") is not None:
            args += ["--max-messages", str(arguments["max_messages"])]
        args.append("--json")
        proc = run_cli(args)
        text = proc.stdout
    elif name == "where_to_appear":
        # **`thread_read` と同じ型**: CLI（`thth where`）を `--json` で呼ぶ
        # だけ（設計「自分の泉」§2.3・T2-3）。`account`／`project` のどちらか
        # 必須は `validate_arguments()` が先に見ているので、ここでは
        # `project` を優先するだけでよい。
        args = ["where"]
        if arguments.get("project"):
            args += ["--project", arguments["project"]]
        else:
            args.append(arguments["account"])
        args += list(arguments.get("words") or [])
        if arguments.get("recent"):
            args.append("--recent")
        if arguments.get("limit") is not None:
            args += ["--limit", str(arguments["limit"])]
        args.append("--json")
        proc = run_cli(args)
        text = proc.stdout
    elif name in ("thth_morning", "thth_observe"):
        # **`where_to_appear` と同じ型**: CLI（`thth morning`）を `--json` で
        # 呼ぶだけ（設計 3.1.0 §1・§5）。栞を進めないときだけ旗を足す。
        args = [name[len("thth_"):], arguments["target"]]
        if arguments.get("mark") is False:
            args.append("--no-mark")
        args.append("--json")
        proc = run_cli(args)
        text = proc.stdout
    elif name == "thth_map_show":
        # **`where_to_appear` と同じ型**: CLI（`thth map show`）を `--json` で呼ぶだけ。
        args = ["map", "show", arguments["project"]]
        for key in ("node", "since"):
            if arguments.get(key) is not None:
                args += ["--" + key, arguments[key]]
        args.append("--json")
        proc = run_cli(args)
        text = proc.stdout
    elif name == "who_is_this":
        # **`where_to_appear` と同じ型**: CLI（`thth who`）を `--json` で呼ぶ
        # だけ（設計「自分の泉」§2.4・T3-3）。`account`／`project` と
        # `author_key`／`username` のどちらかずつ必須は `validate_arguments()`
        # が先に見ているので、ここでは組み立てるだけでよい。
        args = ["who"]
        if arguments.get("project"):
            args += ["--project", arguments["project"]]
        else:
            args.append(arguments["account"])
        if arguments.get("author_key"):
            args.append(arguments["author_key"])
        else:
            args.append("@" + arguments["username"])
        if arguments.get("profile"):
            args.append("--profile")
        args.append("--json")
        proc = run_cli(args)
        text = proc.stdout
    else:
        return {"content": [{"type": "text", "text": f"unknown tool: {name}"}, _channel_note()], "isError": True}

    if name.startswith("thth_topic_") or name in (
            "before_you_post", "after_you_posted", "analytics_report", "operations_handoff", "study_report", "thread_read", "where_to_appear",
            "who_is_this", "thth_morning", "thth_observe", "thth_map_show"):
        # **新しい道具は exit 1 も isError**（設計 §7）。lint の exit 1（検査結果）
        # とは意味が違う——こちらは stale_context・不正な候補比較で、
        # **そのまま使ってはいけない**応答。既存の扱いは変えない。
        # `before_you_post`／`after_you_posted` も同じ（設計 v2 §1・「自分の泉」§5）:
        # exit 1 は台帳が無い、2 は問いが受け取れない。**`cannot_say` だらけの
        # 答えは exit 0 で、error ではない。**
        is_error = proc.returncode != 0
    else:
        is_error = proc.returncode not in (0, 1)  # lint は 1 も正常な「検査結果」
    if not text:
        text = proc.stderr
    content = [{"type": "text", "text": text}]
    # 断りなら受け口の案内を 2 つ目に（CLI の理由行に既に載っていれば重ねない）。
    if is_error and _channel_note()["text"] not in text:
        content.append(_channel_note())
    return {"content": content, "isError": is_error}


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _error(req_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id,
            "error": {"code": code, "message": message}}


def _is_notification(req: dict) -> bool:
    """JSON-RPC の**通知**（`id` を持たない要求）か。

    監査 2 回目・P3-6。**通知には応答を返してはいけない**（JSON-RPC 2.0 §4.1）。
    `notifications/initialized` だけを名前で特定して黙っていたので、
    **`id` の無い `tools/call` には結果を、`id` の無い未知の method には
    エラーを返していた**——`"id": null` を付けた応答は仕様違反で、厳密な
    クライアントは接続を切る。**名前ではなく形で決める。**
    """
    return "id" not in req


def _handle_request(req: dict):
    method = req.get("method")
    req_id = req.get("id")

    if _is_notification(req):
        # **通知は黙って処理する**（応答を作らない）。いまのところ中身のある
        # 通知は `notifications/initialized` だけで、それも何もしない。
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "thth", "version": "0.1.0"},
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": server_tools(authenticated_context()) if server_mode() else TOOLS + (ADMIN_TOOLS if admin_context() is not None else [])}}
    if method == "tools/call":
        params = req.get("params") or {}
        if not isinstance(params, dict):
            raise ToolInputError(
                f"params は object で渡してください（受け取った: {type(params).__name__}）")
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise ToolInputError("params.name（道具の名前）が要ります")
        arguments = params.get("arguments") if server_mode() else validate_arguments(name, params.get("arguments"))
        result = call_tool(name, arguments)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    if req_id is not None:
        return _error(req_id, -32601, f"method not found: {method}")
    return None


def main() -> None:
    """1 行 1 要求のループ。**1 件の失敗でループを降りない**（監査 2026-09-14・P2-1）。

    以前は `_handle_request()` の中で上がった例外がここまで抜けてサーバが死んで
    いた（`arguments` に `file` が無い `KeyError`・`params` が list のときの
    `AttributeError`）。クライアントから見ると応答が来ないまま口が閉じる——
    **どの要求が悪かったのかも判らない。** 1 要求の失敗は 1 要求の error にして、
    次の要求は今までどおり受ける。
    """
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except (ValueError, RecursionError):
            # **`json.JSONDecodeError` だけでは足りない**（監査 2026-09-16）。
            # `json.JSONDecodeError` は `ValueError` の子だが、Python 3.12 は
            # 4300 桁を超える整数で別の `ValueError`
            # （"Exceeds the limit (4300 digits) for integer string conversion"）
            # を上げる——`except json.JSONDecodeError` だけではここが漏れて
            # プロセスが死ぬ。深い入れ子（`[` の連続）の `RecursionError` も同じ
            # 筋で、ここでしか捕まえられない。**id が判らないので返しようがない**
            # （JSON-RPC も id:null を許すが、従来どおり黙って次の行へ・方針は
            # 変えない）。
            continue
        if isinstance(req, list):
            # **batch（配列）は受けない。** 受けたふりをして 1 件目だけ処理する
            # と、残りが黙って消える（`-32600 Invalid Request`）。
            _send(_error(None, -32600,
                         "batch（配列）の要求は受け付けません。1 行 1 要求で送ってください"))
            continue
        if not isinstance(req, dict):
            _send(_error(None, -32600,
                         f"要求は object で送ってください（受け取った: {type(req).__name__}）"))
            continue
        req_id = req.get("id")
        try:
            resp = _handle_request(req)
        except ToolInputError as e:
            resp = _error(req_id, -32602, str(e))
        except Exception as e:                      # noqa: BLE001
            # **死なない。** 想定していない失敗も 1 要求の error にして次へ。
            resp = _error(req_id, -32602, f"{type(e).__name__}: {e}")
        # **通知には何も返さない**（監査 2 回目・P3-6）。失敗しても同じ——
        # 返す先の `id` が無いので、`"id": null` の error を送ることになる
        # （JSON-RPC 2.0 §4.1 違反・厳密なクライアントは接続を切る）。
        # **死なないことと、黙ることは両立する。**
        if resp is not None and not _is_notification(req):
            _send(resp)


if __name__ == "__main__":
    main()
