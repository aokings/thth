"""スレッド連投（`thth: 2`）の原稿を読む・検査する（設計 §1・§7・工程 1）。

**v1 の解析と指紋には一切触らない。** `queuefile.parse_text()` は `thth: 1`
以外を `malformed` にするので、**旧版の実行経路は `thth: 2` を単発として
送れない**（`queuefile.py:91`）——この fail-closed をそのまま使い、
v2 専用の口をここに分ける（Codex 最終条件 5）。

段の区切りは `<!-- thth: N/M -->` の**独立行だけ**。予約構文として扱い、
**番号・総数が不正なもの、空の段は拒否する。**
"""
from __future__ import annotations

import dataclasses
import re

from . import queuefile
from . import writeback as writeback_mod

VERSION = "2"

# 段の区切り。**独立行のみ**（行の途中に現れても区切りにしない）。
SEPARATOR_RE = re.compile(r"^<!--\s*thth:\s*(\d+)\s*/\s*(\d+)\s*-->$")
# 段の数（初版・設計 §6）
MIN_SEGMENTS = 2
MAX_SEGMENTS = 4

# 段ごとに持てる項目。**`topic` は先頭だけ**（設計 §7）。
POST_KEYS = ("index", "post_id", "posted_at", "reply_to", "run_id",
             "bundle_sha", "text_sha256", "topic")
# 分類するだけのラベル（**承認の対象ではない**・設計 §8.3）。
LABEL_KEYS = ("form", "outlet", "numbering")


class BundleError(Exception):
    """v2 として読めない。**推測で補わない。**"""


@dataclasses.dataclass
class Bundle:
    """`thth: 2` の原稿 1 本 ＝ スレッド 1 束。"""

    path: str
    malformed: bool
    front_matter: dict
    posts: list          # front matter の `posts:` 配列（段ごとの記録）
    body: str
    segments: list       # 送る本文。**順序を保った配列**（連結しない）
    verified: bool = False

    def get(self, key: str, default=None):
        return self.front_matter.get(key, default)

    @property
    def account(self):
        return self.front_matter.get("account")


def is_bundle_text(text: str) -> bool:
    """`thth: 2` の原稿か。**読めなくても True**（型外として扱うため）。"""
    split = queuefile._split_front_matter(text)
    if split is None:
        return False
    for line in split[0].split("\n"):
        if line.strip().startswith("thth:"):
            return line.split(":", 1)[1].strip() == VERSION
    return False


def parse_front_matter(fm_text: str) -> tuple:
    """v2 の front matter を読む。`(top_level, posts)`。

    **YAML を入れない**（依存を増やさない・発注 §0-4）。読むのは
    「`key: value` の行」と「`posts:` の下の `- index: N` で始まる配列」だけ。
    **それ以外の形は拒否する**——曖昧な解釈を残さない（Codex 最終条件 5）。
    """
    top: dict = {}
    posts: list = []
    in_posts = False
    current = None

    for raw in fm_text.split("\n"):
        if not raw.strip():
            continue
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))

        if indent == 0:
            if stripped == "posts:" or stripped.startswith("posts:"):
                rest = stripped[len("posts:"):].strip()
                if rest:
                    raise BundleError("posts: は配列です（同じ行に値を書かない）")
                in_posts, current = True, None
                continue
            in_posts, current = False, None
            if ":" not in stripped:
                raise BundleError(f"front matter に読めない行があります: {raw!r}")
            key, _, value = stripped.partition(":")
            top[key.strip()] = value.strip() or None
            continue

        if not in_posts:
            raise BundleError(f"front matter に読めない字下げがあります: {raw!r}")

        if stripped.startswith("- "):
            item = stripped[2:].strip()
            current = {}
            posts.append(current)
            if item:
                if ":" not in item:
                    raise BundleError(f"posts の項目が読めません: {raw!r}")
                key, _, value = item.partition(":")
                current[key.strip()] = value.strip() or None
            continue

        if current is None:
            raise BundleError(f"posts の配列の外に字下げした行があります: {raw!r}")
        if ":" not in stripped:
            raise BundleError(f"posts の項目が読めません: {raw!r}")
        key, _, value = stripped.partition(":")
        current[key.strip()] = value.strip() or None

    return top, posts


def split_segments(section: str) -> tuple:
    """媒体の節を段に割る。`(段の配列, 問題の配列)`。

    **連結したものは返さない。** 承認指紋もトピック提案も「段の境界と順序を
    保った配列」を使う（Codex 最終条件 5）。
    """
    problems: list = []
    segments: list = []
    buffer: list = []
    seen: list = []          # 区切りが名乗った (N, M)

    for line in section.split("\n"):
        m = SEPARATOR_RE.match(line.strip())
        if m is None:
            buffer.append(line)
            continue
        segments.append("\n".join(buffer).strip())
        buffer = []
        seen.append((int(m.group(1)), int(m.group(2))))
    segments.append("\n".join(buffer).strip())

    for i, seg in enumerate(segments, start=1):
        if not seg:
            problems.append(f"{i} 段目が空です")

    total = len(segments)
    for position, (number, declared_total) in enumerate(seen, start=2):
        if number != position:
            problems.append(
                f"区切りの番号が順番どおりではありません"
                f"（{position} 番目の区切りに {number}/{declared_total} と"
                f"書かれています）")
        if declared_total != total:
            problems.append(
                f"区切りが名乗る総数が段の数と合いません"
                f"（{number}/{declared_total} と書かれていますが {total} 段です）")

    return segments, problems


def parse_text(text: str, path: str) -> Bundle:
    """`thth: 2` の原稿を読む。**読めなければ `malformed`。**"""
    split = queuefile._split_front_matter(text)
    if split is None:
        return Bundle(path=path, malformed=True, front_matter={}, posts=[],
                       body=text, segments=[])
    fm_text, body = split
    try:
        top, posts = parse_front_matter(fm_text)
    except BundleError:
        return Bundle(path=path, malformed=True, front_matter={}, posts=[],
                       body=body, segments=[])
    if top.get("thth") != VERSION:
        return Bundle(path=path, malformed=True, front_matter=top, posts=posts,
                       body=body, segments=[])
    return Bundle(path=path, malformed=False, front_matter=top, posts=posts,
                   body=body, segments=[])


def unquote(value):
    """front matter に `"18016…"` と書かれていても中身を返す。"""
    if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def why_malformed(text: str) -> str:
    """なぜ読めないのかを返す。**「読めません」だけでは直せない。**"""
    split = queuefile._split_front_matter(text)
    if split is None:
        return "front matter の区切りがありません"
    try:
        top, _posts = parse_front_matter(split[0])
    except BundleError as e:
        return str(e)
    if top.get("thth") != VERSION:
        return f"thth が {VERSION} ではありません（{top.get('thth')!r}）"
    return "理由を特定できません"


def parse(path: str) -> Bundle:
    with open(path, encoding="utf-8") as f:
        return parse_text(f.read(), path)


def load_segments(bundle: Bundle, media: str) -> tuple:
    """媒体の節を段に割って `bundle.segments` に入れる。`(段, 問題)`。"""
    section = queuefile.extract_section(bundle.body, media)
    if section is None:
        return [], [f"media: `## {media}` の節が無い"]
    segments, problems = split_segments(section)
    bundle.segments = segments
    return segments, problems


def check(bundle: Bundle, *, account_cfg: dict | None) -> list:
    """v2 の形式検査（設計 §1・§7）。**問題の配列を返す。**"""
    errors: list = []
    fm = bundle.front_matter

    if bundle.malformed:
        return ["thth: 2 の原稿として読めません（front matter の形）"]

    for key in ("account", "publish_at"):
        if not fm.get(key):
            errors.append(f"{key}: 必須です")
    status = fm.get("status")
    if status is not None and status not in queuefile.KNOWN_STATUSES:
        errors.append(f"status: 未知の値 ({status})")

    if not fm.get("continue_until"):
        # **「翌日になって突然 3/4 が出る」を防ぐ**（設計 §3.2）。
        errors.append("continue_until: 必須です（いつまでなら続きを出してよいか）")
    else:
        for key in ("publish_at", "continue_until"):
            if fm.get(key):
                try:
                    queuefile.parse_publish_at(fm[key])
                except (ValueError, TypeError) as e:
                    errors.append(f"{key}: 時刻として読めません（{e}）")
        if not errors and fm.get("publish_at"):
            try:
                start = queuefile.parse_publish_at(fm["publish_at"])
                until = queuefile.parse_publish_at(fm["continue_until"])
                if until <= start:
                    errors.append(
                        "continue_until: publish_at より後にしてください")
            except (ValueError, TypeError):
                pass

    media = account_cfg["media"] if account_cfg else "threads"
    segments, seg_problems = load_segments(bundle, media)
    errors += seg_problems

    if segments:
        if not (MIN_SEGMENTS <= len(segments) <= MAX_SEGMENTS):
            errors.append(
                f"段の数は {MIN_SEGMENTS}〜{MAX_SEGMENTS} です（{len(segments)} 段）")
        limit = queuefile.limit_for(media, account_cfg)
        warn_limit = queuefile.WARN_LIMITS.get(media)
        hashtags_allowed = bool(account_cfg.get("hashtags", True)) if account_cfg else False
        for i, seg in enumerate(segments, start=1):
            n = queuefile.char_count(seg)
            if n > limit:
                errors.append(f"length: {i} 段目は {limit} 字以内（{n} 字）")
            elif warn_limit is not None and n > warn_limit:
                errors.append(
                    f"warning: length: {i} 段目が {warn_limit} 字を超えています"
                    f"（{n} 字・上限 {limit} 字）")
            if not hashtags_allowed and queuefile.has_hashtag(seg):
                errors.append(f"hashtag: {i} 段目にハッシュタグがあります")

    errors += check_posts(bundle, segments)

    topic = queuefile.normalize_topic(fm.get("topic"))
    if topic is not None:
        topic_err = queuefile.topic_error(topic)
        if topic_err is not None:
            errors.append(topic_err)

    # **form / outlet は承認対象ではない**（分類するだけのラベル・設計 §8.3）。
    # ただし**知らない語は推測で通さない**——実測を型ごとに並べるときに、
    # 綴り違いが別の型として増えると比較にならない。
    #
    # **ここで profile を読まない**（再検収 F1・2026-09-11 Codex）。この関数は
    # **公開経路（`threadthrow`）からも呼ばれる。** `avoid_forms` の照合を
    # ここに置いていたので、**編集方針が公開の可否を決めていた**——しかも
    # profile が読めないと制限が消える **fail-open** だった。
    # 同じ承認済み原稿が、正常な profile では公開 0 回、**profile を壊れた
    # JSON に置き換えるだけで 3 段とも公開された。**
    #
    # 編集方針の診断は `editorial_notes()` へ移した（`lint` からだけ呼ぶ・
    # 警告まで）。型の禁止を強制的な公開方針にするなら別設計・別レビュー
    # （構想書 §11）。
    from . import forms as forms_mod
    label_err = forms_mod.label_error(fm.get("form"), fm.get("outlet"),
                                       fm.get("numbering"))
    if label_err is not None:
        errors.append(label_err)
    number_warn = forms_mod.numbering_warning(fm.get("numbering"), segments)
    if number_warn is not None:
        errors.append(number_warn)
    form_warn = forms_mod.missing_form_warning(fm.get("form"), segments)
    if form_warn is not None:
        errors.append(form_warn)

    return errors


def editorial_notes(bundle: Bundle) -> list:
    """編集方針の診断（**公開経路からは呼ばない**・再検収 F1）。

    `avoid_forms`（その account が「使わない」と宣言した型）の照合はここ。
    **警告までで、承認も公開も止めない**——構想書 §11「診断結果、型、profile を
    公開時の必須条件にする変更は別設計・別レビュー」。

    **profile が読めないことを黙って「制限なし」にしない。** 以前は例外を
    握って空配列にしていたので、**壊せば制限が消えた。** いまは読めなければ
    読めないと言う（言うだけで、止めない）。
    """
    fm = bundle.front_matter
    account, form = fm.get("account"), fm.get("form")
    if not account or not form:
        return []
    from . import topic_store
    try:
        profile = topic_store.get_profile(account)
    except Exception as e:
        return [f"warning: profile: {account} の profile を読めません（{e}）。"
                f"**使わないと宣言した型（avoid_forms）を照合していません**"
                f"——照合できなかっただけで、制限が無いわけではありません"]
    avoid = list((profile or {}).get("avoid_forms") or [])
    if form in avoid:
        return [f"warning: form: この account は `{form}` を使わないと profile に"
                f"宣言しています（avoid_forms）。**型を変えるか、profile を "
                f"運用者に諮って変えてください。** "
                f"**これは警告です——承認・公開は止まりません。**"]
    return []


def check_posts(bundle: Bundle, segments: list) -> list:
    """`posts:` の配列を検査する。"""
    errors: list = []
    posts = bundle.posts

    if segments and len(posts) != len(segments):
        errors.append(
            f"posts: の件数が段の数と合いません"
            f"（{len(posts)} 件・{len(segments)} 段）")

    for i, post in enumerate(posts, start=1):
        unknown = sorted(set(post) - set(POST_KEYS))
        if unknown:
            errors.append(f"posts[{i}]: 知らない項目があります: {unknown}")
        index = post.get("index")
        if index is None:
            errors.append(f"posts[{i}]: index が要ります")
        elif str(index) != str(i):
            errors.append(
                f"posts[{i}]: index が順番どおりではありません（{index}）")
        # **初版の方針: topic は先頭の段だけ**（設計 §7）。
        # **黙って捨てない。** API が併用を許すかは未確認。
        if i > 1 and post.get("topic"):
            errors.append(
                f"posts[{i}]: {i} 段目に topic が指定されています。"
                f"**初版では未対応です。** 画面では返信にもトピックが付いて"
                f"いますが（2026-09-11 に実物を確認）、**API で同じことが"
                f"できるかは未確認**です。先頭の段だけに付けてください")
    return errors


# --- 書き戻し（工程 3） -----------------------------------------------------

def set_post_fields(text: str, index: int, fields: dict) -> str:
    """`posts:` の `index` 段目に値を書き戻す。**ほかの行に触らない。**

    front matter を組み直すのではなく**その段の中だけを書き換える**——
    組み直すと、書いた人のコメントや並びが消える（v1 の `writeback` と同じ考え）。

    **鍵・値に改行・復帰・NUL があれば 1 文字も書かずに `ValueError`**
    （セキュリティ監査 2 回目・P1-1）。検査は単発の書き戻し（`thth/writeback.py`
    の `set_front_matter_fields()`）と**同じ 1 本**を通す——`posts:` の段も
    front-matter の一部で、改行のあとを字下げ無しで書けば **top-level の行**に
    なる（`status: approved` を後勝ちで足せる）。ここを通らない口が 1 つでも
    残ると、単発で塞いだ穴が連投でそのまま空く（実際にそうなっていた）。
    """
    for key, value in fields.items():
        writeback_mod.check_front_matter_field(key, value)
    lines = text.split("\n")
    fm_end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm_end = i
            break
    if fm_end is None:
        raise BundleError("front matter が見つかりません")

    starts = [i for i in range(1, fm_end) if lines[i].strip().startswith("- ")]
    if index < 1 or index > len(starts):
        raise BundleError(f"{index} 段目が posts: にありません")
    start = starts[index - 1]
    if index < len(starts):
        end = starts[index]
    else:
        # **最後の段の終わりは front matter の終わりではない**（独立検収で
        # 踏んだ）。`posts:` の後ろに top-level のキー（`approved_at:` など）が
        # 続くことがあり、そこまで含めて書き足すと**字下げ行が top-level キーの
        # 後ろに落ちて front matter が読めなくなる。**
        end = fm_end
        for i in range(start + 1, fm_end):
            if lines[i].strip() and not lines[i].startswith(" "):
                end = i
                break

    block = lines[start:end]
    indent = " " * (len(lines[start]) - len(lines[start].lstrip(" ")) + 2)
    for key, value in fields.items():
        rendered = f"{indent}{key}: {'' if value is None else value}"
        for j, line in enumerate(block):
            body = line.strip()
            if body.startswith("- "):
                body = body[2:].strip()
            if body.split(":", 1)[0].strip() == key:
                # `- index: 1` の行は潰さない（項目名が index のときだけ）。
                if block[j].strip().startswith("- ") and key != "index":
                    continue
                block[j] = (lines[start][:len(lines[start]) - len(lines[start].lstrip(" "))]
                             + "- " + f"{key}: {'' if value is None else value}"
                             if block[j].strip().startswith("- ")
                             else rendered)
                break
        else:
            block.append(rendered)
    return "\n".join(lines[:start] + block + lines[end:])
