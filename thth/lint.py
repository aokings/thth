"""`thth lint` / `thth preview`（発注 §3・受け入れ 1・2）。

CLI と MCP（`thth_lint`・`thth_preview`）の両方から呼ばれる純粋関数。判断はここに
書き、`bin/thth`・`mcp/server.py` はどちらもこれを呼ぶだけにする。
"""
from __future__ import annotations

import re

from . import accounts as accounts_mod
from . import engagements as engagements_mod
from . import postid as postid_mod
from . import queuefile
from . import tags as tags_mod


def _account_cfg_or_none(account_name: str | None) -> dict | None:
    if not account_name:
        return None
    try:
        return accounts_mod.load_account(account_name)
    except accounts_mod.AccountError:
        return None


def is_warning(message: str) -> bool:
    """`lint_file()` が返す 1 行が警告（落とさない）か実エラー（落とす）かを見分ける。"""
    return message.startswith("warning:")


# --------------------------------------------------------------------------
# 断る理由の静的な符丁（2.14.1・初回の 3.0 通し運転 2026-09-23）
# --------------------------------------------------------------------------

# **断り文句は理由と次の一手を持って帰る。** `thth_draft_put` が
# `invalid_draft: lint_failed` とだけ返し、実際の理由（台帳の `hashtags: false`
# が本文の `#語` を弾いた）はサーバの中に留まった。呼び手は何を直せばよいか
# 判らないまま同じ本文を送り直す。
#
# `lint_file()` の 1 行は人向けの日本語で、ファイル名や値を含むことがある。
# **それはサーバの口から出さない**——ここで同じ 1 行を**固定の符丁 1 語**に
# 畳む。符丁は `REASONS` に閉じているので、将来 lint の文面が増えても
# 自由文が外へ漏れることはない（知らない文面は `lint_failed` に落ちる）。
HASHTAG_DISABLED = "hashtag: ハッシュタグは付けない規約（`#` を含む）"

FALLBACK_REASON = "lint_failed"

# 上から順に見る（`HASHTAG_DISABLED` は `hashtag: ` より先）。
_REASON_TABLE = (
    (HASHTAG_DISABLED, "hashtags_disabled_by_ledger"),
    ("length: ", "too_long"),
    ("hashtag: ", "hashtag_not_allowed"),
    ("max_hashtags: ", "invalid_max_hashtags"),
    ("front-matter", "invalid_front_matter"),
    ("thth: ", "missing_thth_marker"),
    ("account: ", "missing_account"),
    ("publish_at: ", "invalid_publish_at"),
    # 4.5.6: 出さない期限（形が違う・publish_at より前）。
    ("not_after: ", "invalid_not_after"),
    ("status: ", "unknown_status"),
    ("media: ", "invalid_media"),
    ("本文に制御文字", "control_char_in_body"),
    ("topic", "invalid_topic"),
    ("reply_to_author_key: ", "invalid_reply_to_author_key"),
    ("reply_to_root: ", "invalid_reply_to_root"),
    ("found_by: ", "invalid_found_by"),
    ("location: ", "invalid_publish_option"),
    ("location_id: ", "invalid_publish_option"),
    ("share_to_instagram: ", "invalid_publish_option"),
    ("community_id: ", "invalid_publish_option"),
    # reply_to_file（設計 3.2.0 §1）。lint の 1 行は符丁そのもので始まる。
    *((code + ": ", code) for code in queuefile.REPLY_TO_FILE_MESSAGES),
    # 投稿の目的（設計 3.6.0 §A1）。4 語の固定・承認の指紋には入らない。
    ("goal_invalid: ", "goal_invalid"),
    # はてなブックマークの札のファイル（設計 S4 §22）。
    ("bookmark_tag_too_long: ", "bookmark_tag_too_long"),
    # はてなブログの配信原稿（設計 4.0 S1 §3.5）。lint の 1 行は符丁そのもので始まる。
    *((code + ": ", code) for code in (
        "article_required", "article_source_missing", "invalid_article_source", "title_required",
        "invalid_op", "delivery_required", "blog_id_in_manuscript", "categories_invalid",
        "invalid_form", "delivery_unknown", "image_missing",
        # 4.0 S2（§4.2・§4.3・§7）。
        "relative_link_unresolvable", "article_not_committed",
        # 4.0 S3 §9（画像の形式・容量・枚数）。S2 の images_unsupported_until_s3 は外した。
        "image_too_large", "image_type_unsupported", "too_many_images", "images_too_large",
        # 4.0 S4 §9.2（SNS の告知を記事に結ぶ）。
        "invalid_announces", "announces_not_found", "announces_other_lane", "announces_not_allowed",
        "article_mismatch", "article_url_placeholder_twice", "article_url_without_announces",
        # 実機の確かめ 10/17 の 2: 配信の台帳の状態をそのまま言う（delivery_unknown に丸めない）。
        "delivery_state_wanted", "delivery_state_inflight", "delivery_state_unknown",
        "delivery_state_published", "delivery_state_conflict", "delivery_state_no_follow",
        "delivery_state_deleted",
        # 4.0 S5 §4.4: 本文・題・カテゴリ・画像のどれも変わっていない更新。
        "update_no_change")),
)

REASONS = frozenset([code for _, code in _REASON_TABLE] + [FALLBACK_REASON])


def reason_code(problems) -> str:
    """`lint_file()` の結果を静的な符丁 1 語にする（自由文・path は返さない）。

    最初の実エラー 1 件だけを見る。警告は落とさないので符丁にもしない。
    """
    for item in problems:
        if is_warning(item):
            continue
        for prefix, code in _REASON_TABLE:
            if item.startswith(prefix):
                return code
        return FALLBACK_REASON
    return FALLBACK_REASON


# --------------------------------------------------------------------------
# front-matter の無いファイルに当たったときの次の一手（T1・第 1 回の記録 §3）
# --------------------------------------------------------------------------

# **断るだけで終わらない。** 第 1 回（2026-09-13・L1）は 3 体とも素の原稿に
# `thth lint`／`thth preview` を当て、「front-matter が無い」とだけ言われて
# `--help` の往復に戻った（1〜2 往復の損・呼び出し 14・22・10 回のうち）。
# **素の原稿を持っている人がここに来るのは自然**なので、その人の行き先を
# 1 行で言う——`lint`/`preview` は queue のファイル用の道具であって、
# 素の原稿には `send` がある。
NEXT_STEP_NO_FRONT_MATTER = (
    "次の一手: 素の原稿を 1 回だけ出すなら `thth send <account> --text-file {file}`"
    "（乾式試験が既定）。queue で運用するなら front-matter 付きのファイル"
    "（`thth queue --help`）"
)


def has_front_matter(path: str) -> bool:
    """先頭が `---` で囲まれた front-matter か（中身の正しさは見ない）。"""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return True          # 読めない理由は別に述べられる。次の一手は出さない。
    return queuefile._split_front_matter(text) is not None


def next_step(path: str) -> str | None:
    """front-matter が無いなら次の一手の 1 行、あるなら `None`。"""
    if has_front_matter(path):
        return None
    return NEXT_STEP_NO_FRONT_MATTER.format(file=path)


def _bundle_segments(b, media: str):
    from . import bundle as bundle_mod
    return bundle_mod.load_segments(b, media)


def _bundle_or_none(path: str):
    """`thth: 2` なら束として読む。**v1 の経路には触らない。**"""
    from . import bundle as bundle_mod
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None, None
    if not bundle_mod.is_bundle_text(text):
        return None, None
    return bundle_mod.parse_text(text, path), text


def not_after_notes(fm, now=None) -> list:
    """出さない期限（`not_after`・4.5.6）の lint。無ければ空（今までどおり）。

    形が違う・publish_at より前は誤り。今より前は警告——承認はできるが、THTH は出さない。
    """
    error = queuefile.not_after_lint_error(fm)
    if error:
        return [error]
    until = queuefile.parse_not_after(fm)
    if until is None:
        return []
    from . import jst
    now = now if now is not None else jst.now_jst()
    if now > until:
        return [f"warning: not_after: 出さない期限 {queuefile.jst_minute(until)} を過ぎています"
                "（承認はできますが、THTH は出しません）"]
    return []


def lint_file(path: str) -> list:
    """形式検査。駄目な理由を 1 行ずつ返す（空リストなら OK）。

    `account` の台帳が引けるときは、その媒体・hashtags 設定で媒体節と文字数・
    ハッシュタグまで検査する。台帳が引けないとき（アカウント未指定・未知）は
    threads 既定（500 字・ハッシュタグ不可）で検査する。

    450 字を超えたら警告を返り値に含めるが、**落とさない**（食い違い 2 の裁定・
    2026-09-09）。警告は `warning:` で始まる文字列として errors と同じリストに
    混ぜて返す（`thth lint` の exit code は `error:`/その他の実エラーだけで決まる。
    `is_warning()` で区別できる）。
    """
    # はてなブックマークの札のファイル（設計 S4 §22・4.2）: 札ごとの形とタグの長さ（31 バイト）。
    if str(path).endswith(".jsonl"):
        from . import bookmark_batch
        return bookmark_batch.lint_problems(path)
    # **スレッド連投は束として検査する**（`thth: 1` が無いとだけ言わない）。
    b, _text = _bundle_or_none(path)
    if b is not None:
        from . import bundle as bundle_mod
        # **編集方針の診断は lint にだけ足す**（再検収 F1）。`bundle.check()` は
        # 公開経路からも呼ばれるので、あちらに profile を読ませない。
        cfg=_account_cfg_or_none(b.front_matter.get("account"))
        notes=bundle_mod.check(b,account_cfg=cfg)+bundle_mod.editorial_notes(b)
        # 4.5.6: 出さない期限を過ぎていれば警告（形の誤りは `bundle.check()` が言う）。
        notes.extend(note for note in not_after_notes(b.front_matter) if is_warning(note))
        # **目的は lint と承認の入口でだけ見る**（設計 3.6.0 §A1）。`bundle.check()` は
        # 公開の経路からも呼ばれる——目的は札なので、承認のあとの書き換えで公開を
        # 止めない（書き換えは実行記録に「目的の変更」として残る）。
        if not b.malformed:
            from . import goals as goals_mod
            notes.extend(goals_mod.lint_errors(b.front_matter))
        if cfg and not b.malformed:
            from . import media as media_mod, media_delivery
            for index,row in enumerate(b.posts):
                if media_mod.declared(row):
                    # Bundle topic belongs to the first effective segment only.
                    intent={**row,'topic':b.front_matter.get('topic') if index==0 else None}
                    notes.extend(media_delivery.lint_notes(cfg,intent,text=b.segments[index] if index<len(b.segments) else None))
        return notes

    qf = queuefile.parse(path)
    fm = qf.front_matter
    errors: list = []
    if qf.parse_error:
        errors.append(qf.parse_error)

    # **front-matter の鍵の重複を名指しで error にする**（セキュリティ監査
    # 2026-09-14「撤回が効かない嘘」）。`qf.malformed` は `thth: 1` 欠落とも
    # 共有するので、ここで理由を分けて言う。
    if qf.duplicate_keys:
        errors.append(queuefile.duplicate_keys_message(qf.duplicate_keys))

    # front-matter の値に制御文字が混じっていないか（セキュリティ監査
    # 2026-09-16・B-2）。本文（媒体の節）は下で別に見る。
    for key, value in fm.items():
        if not isinstance(value, str):
            continue
        control = queuefile.find_control_char(value)
        if control is not None:
            pos, cp = control
            errors.append(f"front-matter: {key} に制御文字が含まれています"
                           f"（位置 {pos}・U+{cp:04X}）")

    if fm.get("thth") != "1":
        errors.append("thth: front-matter に `thth: 1` が無い")

    account_name = fm.get("account")
    if not account_name:
        errors.append("account: account が無い")

    publish_at = fm.get("publish_at")
    if not publish_at:
        errors.append("publish_at: publish_at が無い")
    elif "+09:00" not in publish_at:
        errors.append("publish_at: +09:00 が無い")
    else:
        try:
            queuefile.parse_publish_at(publish_at)
        except ValueError:
            errors.append("publish_at: 形式が壊れている")

    status = fm.get("status")
    if status is not None and status not in queuefile.KNOWN_STATUSES:
        errors.append(f"status: 未知の値 ({status})")

    account_cfg = _account_cfg_or_none(account_name)
    from . import hatena_entry
    if (account_cfg is not None and hatena_entry.is_hatena(account_cfg)
            and queuefile.not_after_raw(fm) is not None):
        # はてなの配信原稿の指紋（`thth-hatena-entry-1`）は期限を含まない。期限を延ばしても承認し直しに
        # ならない形を作らないため、はてなでは受け付けない（4.5.6 は SNS の原稿だけ）。
        errors.append("not_after: はてなブログの配信原稿には使えません（SNS の原稿だけ）")
    else:
        errors.extend(not_after_notes(fm))
    if account_cfg is not None and hatena_entry.is_hatena(account_cfg):
        # **はてなブログの配信原稿**（設計 4.0 S1 §3.5）。本文の出所は `article_source` だけで、
        # `## hatenablog` の節・文字数・タグ・返信は見ない（SNS の原稿の検査は通さない）。
        from . import goals as goals_mod
        errors.extend(hatena_entry.lint_errors(fm, account_cfg))
        errors.extend(goals_mod.lint_errors(fm))
        from . import announce
        errors.extend(announce.lint_errors(fm, None, account_cfg, hatena=True))
        return errors
    media = account_cfg["media"] if account_cfg else "threads"
    from . import media as media_mod
    try:
        manifest=media_mod.manifest_for(fm, account_cfg)
        if manifest:
            from . import media_delivery
            errors.extend(media_delivery.lint_notes(account_cfg,fm,text=queuefile.extract_section(qf.body,media,allow_empty=True)))
    except media_mod.MediaError as exc:
        errors.append(str(exc))
    section = queuefile.extract_section(qf.body, media, allow_empty=bool(fm.get('media') or fm.get('attachments')))
    if section is None:
        errors.append(f"media: `## {media}` の節が無い")
    else:
        limit = queuefile.limit_for(media, account_cfg)
        # **数え方も媒体ごと**（`limit_for()` と対・引継ぎ 2026-09-15 §3-D）。
        topic = queuefile.normalize_topic(fm.get("topic"))
        # 4.0 S4 §9.2: `{article_url}` は仮の URL（`https://<blog_id>/entry/0000/00/00/000000`）に置き換えて数える。
        from . import announce
        effective = tags_mod.prepared(media, announce.counted_text(section, fm, account_cfg), topic,
                                      hashtags=bool(account_cfg.get("hashtags", True)) if account_cfg else False)
        n = queuefile.count_for(media, effective)
        if n > limit:
            figure = queuefile.length_figure(media, n, limit)
            errors.append(f"length: {media} は上限を超えています: {figure}" if figure
                          else f"length: {media} は {limit} 字以内（{n} 字）")
        else:
            warn_limit = queuefile.WARN_LIMITS.get(media)
            if warn_limit is not None and n > warn_limit:
                errors.append(
                    f"warning: length: {media} は {warn_limit} 字を超えています"
                    f"（{n} 字・上限 {limit} 字。絵文字の数え方は実物と一致する保証が無い）"
                )
        hashtags_allowed = bool(account_cfg.get("hashtags", True)) if account_cfg else False
        if not hashtags_allowed and queuefile.has_hashtag(section):
            errors.append(HASHTAG_DISABLED)
        errors.extend(tags_mod.errors(media, section, topic, account_cfg))
        # 本文に制御文字が混じっていないか（セキュリティ監査 2026-09-16・B-2）。
        control = queuefile.find_control_char(section)
        if control is not None:
            pos, cp = control
            errors.append(queuefile.control_char_message(pos, cp))

    # トピック（`topic_tag`）。省略・空は許す（設計 §4.1・masaru 裁定 2026-09-09）。
    topic = queuefile.normalize_topic(fm.get("topic"))
    if topic is not None:
        topic_err = queuefile.topic_error(topic) if media == "threads" else None
        if topic_err is not None:
            errors.append(topic_err)

    # 絡みの台帳（設計「自分の泉」§4・発注 T0-1）の任意の 3 鍵。**形だけ検査する**
    # ——値が無ければ通す。承認の 5 項目の指紋には入れない（別物）。
    reply_to_author_key = fm.get("reply_to_author_key")
    if reply_to_author_key not in (None, "") and \
            not engagements_mod.AUTHOR_KEY_RE.match(str(reply_to_author_key)):
        errors.append(
            f"reply_to_author_key: 16 進 16 桁で書いてください（{reply_to_author_key!r}）")

    reply_to_root = fm.get("reply_to_root")
    if reply_to_root not in (None, "") and not postid_mod.is_usable(reply_to_root):
        errors.append(f"reply_to_root: post_id の形ではありません（{reply_to_root!r}）")

    found_by = fm.get("found_by")
    if found_by not in (None, "") and found_by not in engagements_mod.FOUND_BY_VALUES:
        errors.append(
            "found_by: where_to_appear／manual／mention のどれかで書いてください"
            f"（{found_by!r}）")

    errors.extend(reply_to_file_errors(path, fm, account_name))
    # 4.0 S4 §9.2: SNS の告知（`announces`・`article`・本文の `{article_url}`）。
    from . import announce
    errors.extend(announce.lint_errors(fm, section, account_cfg, hatena=False))
    errors.extend(publish_option_errors(fm, account_cfg, account_name=account_name))
    # 投稿の目的（設計 3.6.0 §A1）。front-matter の `goal:` だけを見る——本文の
    # メモ（「目的: 誘導」）は読まない。承認の指紋には入れない（lint と承認の入口
    # だけの門で、select は見ない）。
    from . import goals as goals_mod
    errors.extend(goals_mod.lint_errors(fm))
    # 同じリンク先を前後 72 時間に使う click の原稿（設計 3.7.0 §A1）。**断らない**
    # ——投稿ごとのクリックが測れなくなると知らせるだけ（警告は `warning:` で始まる）。
    if not qf.malformed and not goals_mod.lint_errors(fm):
        from . import click_attribution
        errors.extend(click_attribution.lint_notes(path, fm, qf.body, account_cfg))
    # 見立ての予告（設計「上限と連投の柵」§7.3・段 4）: X の返信の相手（counterpart）・直近と同じ本文・
    # リンクの多さ・反応を頼む型。**断らない**（`warning:`）。根拠は媒体の特性の台帳の項目と確かさ。
    if section is not None and not qf.malformed:
        from . import advise
        errors.extend(advise.warning_line(item)
                      for item in advise.advisories(media, section, fm=fm, account=account_name))
    return errors


# --------------------------------------------------------------------------
# reply_to_file（設計 3.2.0 §1）: 返信先を同じ queue の原稿の名前で書く
# --------------------------------------------------------------------------

_TARGET_KIND_PROBLEM = {
    "missing": queuefile.REPLY_TO_FILE_MISSING,
    "bundle": queuefile.REPLY_TO_FILE_BUNDLE,
    "unreadable": queuefile.REPLY_TO_FILE_UNREADABLE,
}


def reply_to_file_problem(path: str, fm: dict, account_name) -> str | None:
    """`reply_to_file` の検査を符丁 1 語で（無ければ None）。**loud reject**。

    併用・名前の形・自己参照は指した原稿を読まずに言う（`queuefile` の共有の門）。
    その先は指した原稿を読み、無い・束・型外・account 違いを断る。**指した原稿が
    まだ出ていないことは断らない**——それは「待つ」であって誤りではない（§2）。
    """
    problem = queuefile.reply_to_file_static_problem(path, fm)
    name = queuefile.reply_to_file_of(fm)
    if problem is not None or name is None:
        return problem
    kind, target = queuefile.read_reply_target(path, name)
    problem = _TARGET_KIND_PROBLEM.get(kind)
    if problem is not None:
        return problem
    return queuefile.reply_to_file_target_problem(target.front_matter, account=account_name)


def reply_to_file_errors(path: str, fm: dict, account_name) -> list:
    problem = reply_to_file_problem(path, fm, account_name)
    if problem is None:
        return []
    value = queuefile.reply_to_file_of(fm) or fm.get("reply_to")
    return [f"{problem}: {queuefile.REPLY_TO_FILE_MESSAGES[problem]}（{value!r}）"]


# --------------------------------------------------------------------------
# 承認を通る書き込みの口（設計 v2 §4.3・v2.1-B）: 場所・Instagram 共有
# --------------------------------------------------------------------------

LOCATION_ID_MISSING = (
    "location_id: `location:` があるのに `location_id:` が無い——"
    "`thth location search {account} <語>` で id を引いて `location_id:` に書いてください"
    "（場所は推測しません・fail-closed）")
LOCATION_NAME_MISSING = (
    "location: `location_id:` があるのに `location:` が無い——"
    "場所の名前は人が書きます（承認の一段目に見せるため）")
INSTAGRAM_NOT_LINKED = (
    "share_to_instagram: 台帳に `instagram_linked: true` が無いので Instagram には"
    "出せません（Instagram を連携してから台帳に書いてください）")


def publish_option_errors(fm: dict, account_cfg: dict | None, *,
                          account_name: str | None = None) -> list:
    """`location:` / `location_id:` / `share_to_instagram:` の検査（fail-closed）。"""
    from . import approval as approval_mod
    out: list = []
    location = (fm.get("location") or "").strip()
    location_id = (fm.get("location_id") or "").strip()
    if location and not location_id:
        out.append(LOCATION_ID_MISSING.format(account=account_name or "<account>"))
    elif location_id and not location:
        out.append(LOCATION_NAME_MISSING)
    if location_id and any(ch.isspace() for ch in location_id):
        out.append(f"location_id: 空白を含んでいます（{location_id!r}）")

    raw_share = fm.get("share_to_instagram")
    if raw_share is not None and str(raw_share).strip() != "":
        if str(raw_share).strip().lower() not in ("true", "false", "yes", "no", "1", "0"):
            out.append(f"share_to_instagram: true か false で書いてください（{raw_share}）")
        elif approval_mod.is_true(raw_share):
            # 台帳が引けない（未知の account）ときも断る——連携の有無が判らない
            # ものを「出せる」と言わない。
            if not (account_cfg and approval_mod.is_true(account_cfg.get("instagram_linked"))):
                out.append(INSTAGRAM_NOT_LINKED)
    out.extend(community_id_errors(fm, account_cfg))
    return out


# 3.19.0: X のコミュニティ（`community_id:`）。**L1** docs.x.com create-post:
# "Community to post the tweet to." `^[0-9]{1,19}$`。
COMMUNITY_ID_RE = re.compile(r"[0-9]{1,19}")
COMMUNITY_NOT_X = ("community_id: コミュニティは X だけです（この原稿の account は {media}）"
                   "——X 以外の媒体の原稿には書かないでください")
COMMUNITY_FORM = "community_id: 19 桁までの数字で書いてください（{value!r}）"
COMMUNITY_WITH_REPLY = ("community_id: 返信（reply_to・reply_to_file）とは同時に使えません"
                        "——コミュニティへの投稿か返信か、どちらか 1 つにしてください")


def community_id_errors(fm: dict, account_cfg: dict | None) -> list:
    """`community_id:` の検査（fail-closed）。select も同じ関数で落とす。"""
    from . import approval as approval_mod
    value = approval_mod.community_id_of(fm)
    if value is None:
        return []
    out = []
    media = (account_cfg or {}).get("media")
    if media != "x":
        # 台帳が引けない（未知の account）ときも断る——X だと確かめられないものを通さない。
        out.append(COMMUNITY_NOT_X.format(media=media or "不明"))
    if not COMMUNITY_ID_RE.fullmatch(value):
        out.append(COMMUNITY_FORM.format(value=value))
    if (fm.get("reply_to") not in (None, "") or queuefile.reply_to_file_of(fm) is not None):
        out.append(COMMUNITY_WITH_REPLY)
    return out


def preview_file(path: str) -> str:
    """実際に投げる本文そのもの（媒体の節だけ。前後に何も足さない）。

    **スレッド連投は段ごとに分けて出す。** 区切り行まで本文として見せると、
    **それが投稿されるように読める**（実際には送らない）。
    """
    b, _text = _bundle_or_none(path)
    if b is not None:
        cfg = _account_cfg_or_none(b.front_matter.get("account"))
        media = cfg["media"] if cfg else "threads"
        segments, problems = _bundle_segments(b, media)
        if problems:
            return "（段に割れません）" + "／".join(problems)
        out = []
        total = len(segments)
        for i, seg in enumerate(segments, start=1):
            rel = "返信先なし（先頭）" if i == 1 else f"{i - 1} 段目への返信"
            out.append(f"── {i}/{total}　{rel}")
            out.append(seg)
            from . import media as media_mod
            manifest = media_mod.manifest_for(b.posts[i-1], cfg)
            if manifest:
                out.append(media_mod.display(manifest))
                from . import media_delivery
                note=media_delivery.cached_note(cfg)
                if note:out.append(note)
            out.append("")
        return "\n".join(out).rstrip() + "\n"

    qf = queuefile.parse(path)
    account_name = qf.front_matter.get("account")
    account_cfg = _account_cfg_or_none(account_name)
    from . import hatena_entry
    if account_cfg is not None and hatena_entry.is_hatena(account_cfg):
        # はてなの配信原稿は「変換したあとの本文」を見せる（設計 4.0 S1 §6.4・送るのはこれ）。
        try:
            m = hatena_entry.material(qf.front_matter, account_cfg, account_name)
        except hatena_entry.EntryError as exc:
            raise ValueError(hatena_entry.message(exc.code)) from None
        return m["body"] + "\n"
    media = account_cfg["media"] if account_cfg else "threads"
    from . import media as media_mod
    fm = qf.front_matter
    manifest = media_mod.manifest_for(fm, account_cfg)
    section = queuefile.extract_section(qf.body, media, allow_empty=bool(fm.get('media') or fm.get('attachments')))
    if section is None:
        raise ValueError(f"media section が無い（`## {media}`）: {path}")
    text = tags_mod.prepared(media, section, queuefile.normalize_topic(qf.front_matter.get("topic")),
                             hashtags=bool(account_cfg.get("hashtags", True)) if account_cfg else False)
    from . import media_delivery
    note=media_delivery.cached_note(account_cfg) if manifest else None
    reply = reply_to_file_line(qf)
    return (text + ("\n" + media_mod.display(manifest) if manifest else "") + ("\n"+note if note else "")
            + ("\n" + reply if reply else ""))


def reply_to_file_state(qf) -> dict | None:
    """preview 用: `reply_to_file` の解決の見込み（読むだけ・照合しない）。持たなければ None。

    `{"reply_to_file", "reply_to", "unresolved"}`。`reply_to` は指した原稿が出て
    いればその post_id。**これは見込み**——実際に出すときは select が同期を
    確かめた原稿だけを信じて解決し直す（設計 3.2.0 §2）。
    """
    from . import select as select_mod
    reply = select_mod.resolve_reply_to_file(qf, account_name=qf.front_matter.get("account"),
                                             require_verified=False)
    if reply is None:
        return None
    return {"reply_to_file": reply.name, "reply_to": reply.post_id, "unresolved": reply.reason}


def reply_to_file_line(qf) -> str | None:
    """preview の末尾の 1 行（設計 3.2.0 §4）。本文ではないので区切って足す。"""
    state = reply_to_file_state(qf)
    if state is None:
        return None
    from . import select as select_mod
    name = state["reply_to_file"]
    if state["reply_to"]:
        return f"返信先: {name} → {state['reply_to']}"
    if select_mod.waiting_target(state["unresolved"]) is not None:
        return f"返信先: {name}（未解決・待ち）"
    return f"返信先: {name}（解決できません: {state['unresolved']}）"
