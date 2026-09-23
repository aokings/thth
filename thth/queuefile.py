"""front-matter 解析・形式検査・媒体の節の抜き出し・文字数（設計 §4.1・受け入れ 1〜3）。"""
from __future__ import annotations

import dataclasses
import datetime
import os
import re
import unicodedata

KNOWN_STATUSES = {"draft", "approved", "posted", "withdrawn"}
# 媒体ごとの文字数の上限（既定）。**台帳の `char_limit` で account ごとに
# 上書きできる**（`limit_for()`・設計 v2 §4.2「台帳と登録」）——Mastodon は
# インスタンスで上限が違う（既定 500 だが運用者が変えられる）。
# Bluesky は 300（grapheme・**L2**: bsky-docs）、Mastodon は既定 500（**L2**）。
# X は 2.14 で **暫定 280 コードポイント**（設計 2.14.0 §2）。公式ページに
# 280 の明記は無く、重み付き計数も実測していない——2.11 まで置いていた 140 は
# 2017 年より前の数字で、いまの本文を不当に断る。数え方は `XAdapter.count_text`。
MEDIA_LIMITS = {"threads": 500, "x": 280, "bluesky": 300, "mastodon": 500}
DEFAULT_MEDIA_LIMIT = 500
# threads の 450 字警告の閾値（食い違い 2 の裁定・2026-09-09）。絵文字カウントが
# 実物と一致する保証が無い（L3）ので、上限ぎりぎりに座らないための警告として置く。
# 500 で落とすのは従来どおり・450 は warning のみ（exit code は 0 のまま）。
WARN_LIMITS = {"threads": 450}
_FM_DELIM = "---"
# `#語` のハッシュタグ検出。行頭または空白の後に `#` + 語構成文字が続く形。
_HASHTAG_RE = re.compile(r"(?:^|\s)#\w")

# 制御文字（`\n`・`\t`・`\r` は許す・セキュリティ監査 2026-09-16「連投指紋の
# 境界の曖昧さ」）。`thth/approval.py::compute_bundle_components()` は段の本文を
# `\x1e`（ASCII record separator）で連結してハッシュする。段の本文にその文字が
# 混じっていると、承認後に段の境界をずらしても同じハッシュになる
# （`["a\x1eb","c"]` と `["a","b\x1ec"]` は同じ入力バイト列になる）。
# **ハッシュの定義自体は変えない**——代わりに、本文・段・front-matter の値に
# 制御文字が混じっていること自体を lint・approve・select（公開直前）の 3 か所で
# 拒む。
CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def find_control_char(text) -> tuple | None:
    """`text` に混じった制御文字を探す。見つかれば `(先頭からの位置, コードポイント)`、
    無ければ `None`。`text` が文字列でなければ `None`（呼び出し側で `None` を渡しても
    落ちないように）。"""
    if not isinstance(text, str):
        return None
    m = CONTROL_CHAR_RE.search(text)
    if m is None:
        return None
    return m.start(), ord(m.group())


def control_char_message(index: int, codepoint: int) -> str:
    """`find_control_char()` の結果を人が読める 1 行にする。"""
    return f"本文に制御文字が含まれています（位置 {index}・U+{codepoint:04X}）"


# トピック（Threads の `topic_tag`。設計 §2.2・§4.1・masaru 裁定 2026-09-09）。
# 1〜50 字・`.`（ピリオド）と `&`（アンパサンド）は不可・1 投稿に 1 つだけ。
TOPIC_MIN_LEN = 1
TOPIC_MAX_LEN = 50
TOPIC_FORBIDDEN_CHARS = ".&"


@dataclasses.dataclass
class QueueFile:
    """1 つの queue ファイル。`malformed` は「thth: が無い・front-matter が壊れている」
    （設計 §3.3 select 条件 2）で、これが立っていれば型外として扱う。"""

    path: str
    malformed: bool
    front_matter: dict
    body: str
    # 同期を確認した commit（HEAD）の中身と一致することを確かめられたか
    # （外部レビュー第 4 巡 P1・`thth.writeback.matches_synced_commit()`）。
    # **既定は False（確認できていない）**。`core.list_queue_files()` だけが
    # True を立てる。select はこれが False の approved を候補にしない。
    verified: bool = False
    # front-matter に同じ鍵が 2 回以上あったら、その鍵名の一覧（無ければ空）
    # （セキュリティ監査 2026-09-14「撤回が効かない嘘」）。`_parse_kv()` は
    # 従来どおり後勝ちで `front_matter` を組み立てるが、重複があったこと自体は
    # ここに残す——`malformed` を立てる根拠と、`lint`/エラーメッセージが
    # 「どの鍵が重複したか」を名指しできるようにするため。
    duplicate_keys: list = dataclasses.field(default_factory=list)
    parse_error: str | None = None

    def get(self, key: str, default=None):
        return self.front_matter.get(key, default)


def _split_front_matter(text: str) -> tuple[str, str] | None:
    """先頭が `---\\n...\\n---\\n` の形かを見て `(front_matter_text, body)` を返す。
    形が合わなければ None（front-matter が無い・壊れている＝型外の一部）。"""
    if not text.startswith(_FM_DELIM):
        return None
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return None


def _parse_kv(fm_text: str) -> tuple:
    """front-matter の 1 行 1 項目を読む。`(dict, 重複した鍵の一覧)`。

    **重複した鍵は今までどおり後勝ちで dict に残す**（読み方自体は変えない
    ——`thth/writeback.py::set_front_matter_fields()` が最初に見つけた行だけを
    書き換えるのと表裏で、「最後に書かれた行が効く」という前提は他の読み手にも
    広く使われている）。重複があったこと自体は 2 つめの戻り値で呼び出し側に返す。
    `parse_text()` はこれを見て `malformed` を立てる——`status: approved` を
    2 回書いた原稿に `thth revoke` をかけると、1 つ目の行だけが `draft` に
    書き換わり、読む側（ここ）は 2 つ目を後勝ちで採るので、**取り消したはずの
    原稿が承認済みのまま**になっていた（セキュリティ監査 2026-09-14）。
    """
    out: dict = {}
    seen: set = set()
    duplicates: list = []
    from . import media as media_mod
    lines = fm_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.partition(":")[0].strip() in media_mod.STRUCTURED_KEYS:
            key = line.partition(":")[0].strip()
            if key in seen:
                duplicates.append(key)
            seen.add(key)
            out[key], i = media_mod.parse_structured(lines, i, 0, key)
            continue
        i += 1
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key in seen and key not in duplicates:
            duplicates.append(key)
        seen.add(key)
        out[key] = value if value else None
    return out, duplicates


def duplicate_keys_message(keys: list) -> str:
    """`duplicate_keys` を人が読める 1 行にする（`lint`・`writeback` で使い回す）。"""
    return "front-matter の鍵が重複しています: " + "、".join(keys)


def parse(path: str) -> QueueFile:
    """queue ファイル 1 本を読む。front-matter が無い／`thth: 1` が無ければ malformed=True。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return parse_text(text, path)


def parse_text(text: str, path: str) -> QueueFile:
    """既に読んである中身から組み立てる（`parse()` の中身）。

    `core.list_queue_files()` は**ファイルを 1 回だけ読み**、その同じバイト列で
    「commit と一致するか」の検査も行う（外部レビュー第 4 巡 P1）。読み直すと、
    検査したバイト列と select が見るバイト列が別物になりうる（検査と使用の間に
    書き換えられる）ため、口を分けてある。

    **front-matter の鍵が重複していれば malformed にする**（セキュリティ監査
    2026-09-14）。型外として扱えば、`select`・`thth revoke` はどちらも既存の
    「malformed は候補にしない・書き換えない」という fail-closed にそのまま乗る。
    """
    split = _split_front_matter(text)
    if split is None:
        return QueueFile(path=path, malformed=True, front_matter={}, body=text)
    fm_text, body = split
    from . import media as media_mod
    try:
        fm, duplicate_keys = _parse_kv(fm_text)
    except media_mod.MediaError as exc:
        return QueueFile(path=path, malformed=True, front_matter={}, body=body, parse_error=str(exc))
    malformed = fm.get("thth") != "1" or bool(duplicate_keys)
    return QueueFile(path=path, malformed=malformed, front_matter=fm, body=body,
                      duplicate_keys=duplicate_keys)


def extract_section(body: str, media: str, *, allow_empty=False) -> str | None:
    """`## <media>` の節の本文を抜き出す。**前後の空白を落とした文字列**を「送る本文」
    として返す（設計 §4.1・T1 検収 2026-09-09 で確定。末尾改行は数に含めない・
    表示上の改行は printer の都合であって本文ではない）。節が無ければ None。
    次の `## ` 見出し（同レベル）までがその節の範囲。"""
    lines = body.split("\n")
    heading = f"## {media}".lower()
    start = None
    for i, line in enumerate(lines):
        if line.strip().lower() == heading:
            start = i + 1
            break
    if start is None:
        return None
    end = len(lines)
    for i in range(start, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    section = "\n".join(lines[start:end]).strip()
    if not section and not allow_empty:
        return None
    return section


def _is_emoji(ch: str) -> bool:
    """絵文字らしさの判定（実務上の近似）。Unicode カテゴリ So（記号・その他）は
    大半の絵文字（ダイングバット・絵文字ピクトグラム）を含む。国旗（地域指示記号
    ペア U+1F1E6-1F1FF）は Symbol, Other 以外のカテゴリなので個別に足す。"""
    cp = ord(ch)
    if 0x1F1E6 <= cp <= 0x1F1FF:
        return True
    return unicodedata.category(ch) == "So"


def limit_for(media: str, account_cfg: dict | None = None) -> int:
    """その媒体・そのアカウントの文字数上限（設計 v2 §4.2）。

    台帳の `char_limit` が**正の整数**なら媒体の既定を上書きする。壊れた値
    （0・負・数でない）は**黙って採らない**——上書きが効いたと思わせないため、
    既定に戻す（loud に断るのは `thth lint` の仕事で、ここは読むだけの口）。
    """
    limit = MEDIA_LIMITS.get(media, DEFAULT_MEDIA_LIMIT)
    override = (account_cfg or {}).get("char_limit")
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return override
    return limit


def count_for(media: str, text: str) -> int:
    """その媒体の数え方で本文を数える（**`limit_for()` と同じ口**）。

    `limit_for()` が「その媒体の上限」を答えるのと対で、こちらは「その媒体の
    数え方での長さ」。**上限と数え方は必ず対で使う**——`send`・`select`・`lint`
    はどれもこの 2 つだけを見る。

    数え方の中身はアダプタが持つ（`adapters.count_text_for()`）。**ここで媒体名
    で分岐しない**（設計 v2 §4.2「媒体名で分岐するコードを 1 か所に集める」）。
    import は関数の中——`thth.adapters` は `queuefile` を読むので、module 頭で
    書くと輪になる。
    """
    from . import adapters as adapters_mod
    return adapters_mod.count_text_for(media, text)


def length_line(media: str, text: str, account_cfg=None) -> str:
    from . import adapters
    unit = adapters.adapter_class(media).COUNT_UNIT
    return f"文字数: {count_for(media, text)}/{limit_for(media, account_cfg)}（{media}・{unit}）"


def char_count(text: str) -> int:
    """Threads の文字数の数え方（設計 §2.2・受け入れ 3）: 500 字上限、絵文字は
    UTF-8 バイト数で数える。絵文字以外は Unicode コードポイント 1 個を 1 字とする。"""
    count = 0
    for ch in text:
        if _is_emoji(ch):
            count += len(ch.encode("utf-8"))
        else:
            count += 1
    return count


def has_hashtag(text: str) -> bool:
    return bool(_HASHTAG_RE.search(text))


# --------------------------------------------------------------------------
# reply_to_file（設計 3.2.0 §1）: 返信先を「同じ queue の原稿の名前」で書く
# --------------------------------------------------------------------------

# 「今日は問い・明日は答え」の答えの原稿は、問いの post_id が出るまで書けない。
# そこで返信先を post_id ではなく**同じ queue の原稿の名前**で書き、その原稿が
# 出るまで道具が待つ。**解決できないのに root に落とす経路は作らない**（§0）。
REPLY_TO_FILE_KEY = "reply_to_file"
# 承認の指紋の reply_to 欄に入れる印（§3・`approval.reply_to_for_fingerprint()`）。
# post_id を直書きする `reply_to` と**同じ欄**を使うので、`reply_to` の値がこの
# 印で始まることは許さない——許すと `reply_to_file: a.md` と `reply_to: file:a.md`
# が同じ指紋になり、承認のあとに一方からもう一方へ書き換えても気づけない。
REPLY_TO_FILE_MARK = "file:"

# 断る理由の符丁（lint と select が同じ語を使う・静的）。
REPLY_TO_CONFLICT = "reply_to_conflict"
REPLY_TO_FILE_INVALID = "reply_to_file_invalid"
REPLY_TO_FILE_SELF = "reply_to_file_self"
REPLY_TO_FILE_MISSING = "reply_to_file_missing"
REPLY_TO_FILE_ACCOUNT_MISMATCH = "reply_to_file_account_mismatch"
REPLY_TO_FILE_BUNDLE = "reply_to_file_bundle_unsupported"
# 指した原稿は在るが `thth: 1` の原稿として読めない（設計 §1 の 6 語に 1 語足した
# ——「在るのに読めない」を「無い」と言うと、直す場所を取り違えるため）。
REPLY_TO_FILE_UNREADABLE = "reply_to_file_unreadable"

REPLY_TO_FILE_MESSAGES = {
    REPLY_TO_CONFLICT: "reply_to と reply_to_file は併用できません（どちらか 1 つ）",
    REPLY_TO_FILE_INVALID: ("同じ queue の原稿のファイル名だけを書いてください"
                            "（`.md`・`/` `\\` `..` を含まない）"),
    REPLY_TO_FILE_SELF: "自分自身は指せません",
    REPLY_TO_FILE_MISSING: "指した原稿が同じ queue にありません",
    REPLY_TO_FILE_ACCOUNT_MISMATCH: "指した原稿の account が違います（同じ account の原稿だけ）",
    REPLY_TO_FILE_BUNDLE: "スレッド連投（thth: 2）の原稿は指せません（thth: 1 の原稿だけ）",
    REPLY_TO_FILE_UNREADABLE: "指した原稿が thth: 1 の原稿として読めません",
}


def reply_to_file_of(fm) -> str | None:
    """front-matter の `reply_to_file`（前後の空白を落とす）。空・無しは None。"""
    raw = (fm or {}).get(REPLY_TO_FILE_KEY)
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def reply_to_file_name_ok(name) -> bool:
    """**ファイル名だけ**か（設計 §1）。パスは書けない——同じ queue の外を指させない。"""
    return (isinstance(name, str) and name.endswith(".md") and len(name) > len(".md")
            and "/" not in name and "\\" not in name and ".." not in name
            and not name.startswith(".") and find_control_char(name) is None)


def reply_to_file_static_problem(path: str, fm) -> str | None:
    """指した原稿を読まずに言える問題（併用・名前の形・自己参照）。無ければ None。

    lint（`lint.lint_file()`）と select（`select.resolve_reply_to_file()`）が
    **同じ関数**を通る——承認のあとに原稿を書き換えれば指紋で落ちるが、門を
    1 か所にしておけば、片方だけ緩む形にならない。
    """
    name = reply_to_file_of(fm)
    direct = (fm or {}).get("reply_to")
    direct = str(direct).strip() if direct is not None else ""
    if name is None:
        # `reply_to: file:…` は reply_to_file と同じ指紋になるので断る（上の注記）。
        if direct.startswith(REPLY_TO_FILE_MARK):
            return REPLY_TO_FILE_INVALID
        return None
    if direct:
        return REPLY_TO_CONFLICT
    if not reply_to_file_name_ok(name):
        return REPLY_TO_FILE_INVALID
    if name == os.path.basename(path):
        return REPLY_TO_FILE_SELF
    return None


def reply_to_file_target_problem(target_fm, *, account) -> str | None:
    """指した原稿（`thth: 1` として読めたもの）の側の問題。無ければ None。"""
    if (target_fm or {}).get("account") != account:
        return REPLY_TO_FILE_ACCOUNT_MISMATCH
    return None


def classify_reply_target(text: str | None, path: str):
    """指した原稿の中身を `(種類, QueueFile)` に分ける。

    種類は `missing`（無い）・`bundle`（`thth: 2`）・`unreadable`（型外）・`ok`。
    `text` が None なら無い。束の判定は `bundle.is_bundle_text()` と同じ
    （読めなくても `thth: 2` と書いてあれば束）。
    """
    if text is None:
        return "missing", None
    from . import bundle as bundle_mod
    if bundle_mod.is_bundle_text(text):
        return "bundle", None
    qf = parse_text(text, path)
    if qf.malformed:
        return "unreadable", qf
    return "ok", qf


def read_reply_target(candidate_path: str, name: str):
    """lint・preview 用: 候補と同じディレクトリの `name` を読んで分ける（照合はしない）。"""
    target_path = os.path.join(os.path.dirname(candidate_path), name)
    try:
        with open(target_path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        return "missing", None
    except (OSError, UnicodeDecodeError):
        return "unreadable", None
    if not os.path.isfile(target_path):
        return "missing", None
    return classify_reply_target(text, target_path)


def parse_publish_at(value: str) -> datetime.datetime:
    """`+09:00` 付きの ISO 8601 をパースする。壊れていれば ValueError。"""
    return datetime.datetime.fromisoformat(value)


def normalize_topic(raw: str | None) -> str | None:
    """front-matter の `topic` を検査・送信用に正規化する。前後の空白を落とし、
    先頭の `#` を落とす（人が `#苦味` と書きがちなので機械的に外す。ハッシュタグ
    とは別物なので `#` は付けない・設計 §4.1）。空・None は None（トピック無し）。
    """
    if raw is None:
        return None
    value = raw.strip()
    value = value.lstrip("#")
    value = value.strip()
    return value or None


def topic_error(topic: str) -> str | None:
    """正規化済み topic 1 件を検査する。OK なら None、駄目なら理由コードを返す
    （設計 §2.2: 1〜50 字・`.` と `&` は不可）。理由コードは `too_long(NNN)` 等の
    既存の名付け方に合わせ、`topic_` を付けた機械可読な形にする
    （`topic_too_long(NN)` / `topic_too_short(N)` / `topic_invalid_char(.)`）。
    呼び出し側で None（トピック無し）を渡さないこと（`normalize_topic()` で
    None になったものは検査対象にしない）。
    """
    n = len(topic)
    if n > TOPIC_MAX_LEN:
        return f"topic_too_long({n})"
    if n < TOPIC_MIN_LEN:
        return f"topic_too_short({n})"
    for ch in TOPIC_FORBIDDEN_CHARS:
        if ch in topic:
            return f"topic_invalid_char({ch})"
    return None
