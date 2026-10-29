"""`state/<account>/sent/<post_id>.json`（外部レビュー §3・受け入れ 9・10）。

公開に**成功した直後**、書き戻し（front-matter の書き換え）より前に書く。**これが
正本**（VM 側）: 「実際に送った本文そのもの」と、そのハッシュ、送った時刻を動かせない
記録として残す。書き戻しの直前に、いま repo にある本文のハッシュをこれと突き合わせ、
食い違えば front-matter を書き換えない（`thth.core._throw_chosen()` 参照）。
"""
from __future__ import annotations

import datetime
import json
import re as _re
import os

from . import jst
from . import postid as postid_mod


def dir_for(state_dir: str) -> str:
    return os.path.join(state_dir, "sent")


def path_for(state_dir: str, post_id: str) -> str:
    """**`post_id` をそのままパスにしない**（`thth/postid.py`・T3 2026-09-13）。

    Bluesky の `post_id` は AT URI で `/` を含む。Threads の数字だけの
    `post_id` は encode しても 1 文字も変わらないので、既存のファイルは動かない。
    """
    return os.path.join(dir_for(state_dir),
                        f"{postid_mod.to_filename(post_id)}.json")


# 記録に書いてよい「添付の種類」（第 10 段・設計 2.13.0 §5）。ファイルの kind と
# 型付きの種類、複数ファイルの `carousel`、何も無い `none`。**`thth/media.py` の
# 語と一致していること**を `tests/test_v213_attachment_kinds.py` が見る——台帳の
# 語彙が実装より遅れると、`--by attachment_kind` の層が黙って欠ける。
ATTACHMENT_NONE_LABEL = "none"
ATTACHMENT_KINDS = ("none", "image", "video", "audio", "carousel",
                    "poll", "quote", "link", "gif", "text")


def write(state_dir: str, *, post_id: str, text: str, body_hash: str, sent_at: str,
          approved_fingerprint: str | None = None, reply_to: str | None = None, media: list | None = None,
          attachment_kinds: list | None = None, resolved_from: dict | None = None,
          goal: str | None = None, goal_change: dict | None = None,
          link_urls: list | None = None, origin: dict | None = None,
          community_id: str | None = None, kind: str | None = None,
          blog_id: str | None = None, delivery: str | None = None,
          url: str | None = None, url_canonical: str | None = None, target: str | None = None,
          article: str | None = None, tags: list | None = None, result: str | None = None,
          remote: dict | None = None, announces: str | None = None,
          article_url: str | None = None, retry_not_found: bool = False,
          crossed_quiet_hours: bool = False) -> str:
    """送った本文そのものを動かせない記録として保存する。返り値は書いたパス。

    `approved_fingerprint`（外部レビュー再々レビュー P1・1）は公開直前に固定した
    5 項目（本文・account・reply_to・topic・publish_at）の指紋。`body_hash` は
    後方互換のため残す（本文だけの hash・`tests/test_sent_integrity.py` が参照）。

    **書けない `post_id` はここでも断る**（監査 2 回目・P3-9）。呼び出し側
    （`core`・`threadthrow`）は公開の直後に確かめているが、**書く側にも 1 枚置く**
    ——`post_id` はそのままファイル名になるので、長すぎれば `OSError`、制御文字が
    入れば読めない名前が残る。**書く場所に近いほうで断ると、新しい呼び出し口が
    増えても穴が空かない。**

    `kind`・`blog_id`・`delivery`・`url` ははてなの記録だけ（設計 4.0 S1 §4.2・`HATENA_KINDS`）。
    渡さなければ記録の形は今までと 1 バイトも変わらない。

    ブックマークの行（`kind: bookmark_*`・設計 4.0 S4 §3.2）は `url_canonical`・`target`・`article`・
    `tags`・`result`・`remote` を値があるときだけ足す。SNS の告知（設計 4.0 S4 §9.4 の 3）は `kind` の
    無い行に `announces`・`article`・`article_url` を値があるときだけ足す（無ければ 1 バイトも変えない）。はてなの記録は上書きしない
    （`sent_record_exists`）——安全装置が数える材料なので、書き直すと数が減る。
    """
    if not postid_mod.is_usable(post_id):
        raise ValueError(
            f"post_id を記録の鍵にできません（空・`.`／`..`・制御文字・長すぎる）:"
            f" {post_id!r}")
    hatena = _hatena_fields(kind=kind, blog_id=blog_id, delivery=delivery, url=url)
    announce = {}
    if announces is not None or article_url is not None or (kind is None and article is not None):
        if kind is not None:
            raise ValueError("invalid_sent_kind")
        announce = _announce_fields(announces=announces, article=article, article_url=article_url)
    else:
        hatena.update(_bookmark_fields(kind=kind, url_canonical=url_canonical, target=target, article=article,
                                       tags=tags, result=result, remote=remote))
    d = dir_for(state_dir)
    os.makedirs(d, exist_ok=True)
    p = path_for(state_dir, post_id)
    attempts = None
    if hatena and os.path.exists(p):
        # **はてなの記録は上書きしない**（設計 4.0 S1 §4.2）。数（安全装置）の材料なので、
        # 同じ post_id で書き直すと前の行が消えて数が減る（更新を entry_id のまま書くと
        # 作成の行が消える）。更新・削除は別の post_id か `mark_retracted` で残す。
        # 4.0 S4 §7: 照合で `not_found` になったブックマークの打ち直し（`retry_not_found`）だけは、
        # 前の試みを `attempts[]` に移して同じ行に書く（前の試みも枠に数え続ける・`guard._classified`）。
        previous = read(state_dir, post_id) if retry_not_found and kind == "bookmark_add" else None
        if (not isinstance(previous, dict) or previous.get("kind") != "bookmark_add"
                or previous.get("result") != "not_found" or previous.get("retracted_at")):
            raise ValueError("sent_record_exists")
        attempts = list(previous.get("attempts") or []) + [
            {k: previous[k] for k in ("sent_at", "result", "reconciled_at", "approved_fingerprint", "body_hash")
             if k in previous}]
    tmp = p + ".tmp"
    data = {"post_id": post_id, "text": text, "body_hash": body_hash, "sent_at": sent_at,
            "approved_fingerprint": approved_fingerprint, "reply_to": reply_to}
    data.update(hatena)
    if attempts is not None:
        data["attempts"] = attempts
    # 告知（設計 4.0 S4 §9.4 の 3）: どの記事を告げたか。無ければ 1 バイトも変えない。
    data.update(announce)
    if crossed_quiet_hours:
        # 緊急の札（`urgent: true`・10/30）で静かな時間帯を越えて出した。越えたときだけ足す
        # （無ければ 1 バイトも変えない・`thth events` の `published` に出る）。
        data["crossed_quiet_hours"] = True
    if resolved_from is not None:
        # reply_to_file（設計 3.2.0 §3）。`reply_to` は解決した post_id（記録は事実）。
        # どの原稿から解決したかを足す——あとから「なぜこの投稿に返したか」を辿れる。
        if (type(resolved_from) is not dict or set(resolved_from) != {"file", "post_id"}
                or resolved_from["post_id"] != reply_to):
            raise ValueError("invalid_resolved_from")
        data["reply_to_file"] = resolved_from["file"]
        data["resolved_from"] = {"file": resolved_from["file"], "post_id": resolved_from["post_id"]}
    if media is not None:
        if type(media) is not list or any(type(x) is not dict or set(x)!={'sha256','kind','alt_present','remote_id'} for x in media):raise ValueError('invalid_media_receipt')
        data['media']=media
    if attachment_kinds is not None:
        # **観測の側が読む鍵**（`thth/collect.py` → `insights` の行 → `--by
        # attachment_kind`）。知らない語・重複・空 list はここで断る——記録に
        # 入ってしまえば、あとから「これは何の層か」を誰も決められない。
        if (type(attachment_kinds) is not list or not attachment_kinds
                or any(x not in ATTACHMENT_KINDS for x in attachment_kinds)
                or len(set(attachment_kinds)) != len(attachment_kinds)
                or (ATTACHMENT_NONE_LABEL in attachment_kinds and len(attachment_kinds) != 1)):
            raise ValueError('invalid_attachment_kinds')
        data['attachment_kinds']=sorted(attachment_kinds)
    if goal is not None:
        # **投稿の目的**（設計 3.6.0 §A）。公開の時点の front-matter の `goal:`
        # （`thth send` は `--goal`）。`--by goal` と observe はここ（と連投の実行
        # 記録）から読む——いまの原稿は読まない。知らない語はここで断る。
        from . import goals as goals_mod
        if goal not in goals_mod.RECORDED:
            raise ValueError('invalid_goal')
        data['goal'] = goal
        if goal_change is not None:
            # 承認の時点の目的（`approved_goal`）と違った。指紋には入らないので公開は
            # 止めず、ここに残す（「目的の変更」）。
            if not goals_mod.valid_change(goal_change) or goal_change['to'] != goal:
                raise ValueError('invalid_goal_change')
            data['goal_change'] = dict(goal_change)
    if link_urls is not None:
        # **添付のリンク先**（設計 3.7.0 §A1）。本文の URL は `text` にあるが、link・
        # text 添付のリンク先は本文に出ない——click を投稿単位で数える
        # （`click_attribution`）のに、どのリンク先を使った投稿かが要る。添付のある
        # 公開では空でも書く（「無かった」と「控えていない」を分ける）。
        if (type(link_urls) is not list or len(link_urls) > 10
                or any(type(x) is not str or not x or len(x) > 2048 for x in link_urls)):
            raise ValueError('invalid_link_urls')
        data['link_urls'] = list(link_urls)
    if community_id is not None:
        # **X のコミュニティ**（3.19.0）。自分の投稿のメタデータ——あとで数字を
        # コミュニティごとに見るため。数字 19 桁まで以外は書かない。
        if not isinstance(community_id, str) or not _re.fullmatch(r"[0-9]{1,19}", community_id):
            raise ValueError("invalid_community_id")
        data["community_id"] = community_id
    if origin is not None:
        # **誰の依頼で直接出したか**（設計 3.12.0 §3.2）。`via`（mcp・cli・http・api＝遠くの道）と
        # 資格の id（credential_digest の先頭 12 文字）。承認ページを通らない公開だけに付く。
        data.update(_origin_fields(origin, prefix=""))
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    return p


ORIGIN_VIAS = ("mcp", "cli", "http", "api")

# はてなの記録の種類（設計 4.0 S1 §4.2）。`kind` の無い行は SNS の公開として今までどおり数える。
# - `entry_create`・`entry_update`: 公開の枠（`daily_max_posts`）。`retracted_at` が足されたら削除の枠
# - `entry_delete`: 削除の枠（`daily_max_retracts`）
# - `bookmark_add`・`bookmark_delete`: ブックマークの枠（`daily_max_bookmarks`）。`retracted_at` も同じ枠
HATENA_KINDS = ("entry_create", "entry_update", "entry_delete", "bookmark_add", "bookmark_delete")
_BLOG_ID_RE = _re.compile(r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
                          r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
_DELIVERY_RE = _re.compile(r"dl_[0-9a-f]{12}")


def bookmark_post_id(url: str) -> str:
    """ブックマークの記録の鍵（`bm:` ＋ **正規化した URL** の sha256 の先頭 24 桁・設計 4.0 S4 §3.2）。

    1 URL に 1 つ——同じ URL の 2 回目（`bookmark_once`）は、この記録があるかで分かる。正規化は
    `hatena_bookmark.canonical_url`（`#a` と `#b` は同じ鍵）。読めない URL は `ValueError("invalid_url")`。
    """
    import hashlib
    from .hatena_bookmark import canonical_url
    return "bm:" + hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:24]


BOOKMARK_KINDS = ("bookmark_add", "bookmark_delete")
BOOKMARK_TARGETS = ("own", "external")
BOOKMARK_RESULTS = ("confirmed", "unknown", "not_found")
_REMOTE_KEYS = ("permalink", "created_epoch", "user")


def _plain_text(value, limit) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and not CONTROL_TEXT_RE.search(value)


def _good_url(value) -> bool:
    return (isinstance(value, str) and value.startswith(("https://", "http://")) and len(value) <= 2048
            and not CONTROL_URL_RE.search(value))


def _bookmark_fields(*, kind, url_canonical, target, article, tags, result, remote) -> dict:
    """ブックマークの行の欄（設計 4.0 S4 §3.2）。どれも無ければ空。ブックマークの行でなければ断る。"""
    given = {k: v for k, v in (("url_canonical", url_canonical), ("target", target), ("article", article),
                               ("tags", tags), ("result", result), ("remote", remote)) if v is not None}
    if not given:
        return {}
    if kind not in BOOKMARK_KINDS:
        raise ValueError("invalid_sent_kind")
    out = {}
    if url_canonical is not None:
        if not _good_url(url_canonical):
            raise ValueError("invalid_url")
        out["url_canonical"] = url_canonical
    if target is not None:
        if target not in BOOKMARK_TARGETS:
            raise ValueError("invalid_target")
        out["target"] = target
    if article is not None:
        if not _plain_text(article, 512):
            raise ValueError("invalid_article")
        out["article"] = article
    if tags is not None:
        if type(tags) is not list or len(tags) > 10 or any(not _plain_text(t, 100) for t in tags):
            raise ValueError("invalid_tags")
        out["tags"] = sorted(tags)
    if result is not None:
        if result not in BOOKMARK_RESULTS:
            raise ValueError("invalid_result")
        out["result"] = result
    if remote is not None:
        if type(remote) is not dict or set(remote) - set(_REMOTE_KEYS):
            raise ValueError("invalid_remote")
        out["remote"] = {k: remote[k] for k in _REMOTE_KEYS if k in remote}
    return out


def _announce_fields(*, announces, article, article_url) -> dict:
    """SNS の告知の欄（設計 4.0 S4 §9.4 の 3）。`announces` は配信 id・`article_url` は URL。"""
    if not isinstance(announces, str) or not _DELIVERY_RE.fullmatch(announces):
        raise ValueError("invalid_announces")
    out = {"announces": announces}
    if article is not None:
        if not _plain_text(article, 512):
            raise ValueError("invalid_article")
        out["article"] = article
    if article_url is not None:
        if not _good_url(article_url):
            raise ValueError("invalid_url")
        out["article_url"] = article_url
    return out


def _hatena_fields(*, kind, blog_id, delivery, url) -> dict:
    """はてなの欄を検査して記録に足す分を返す（どれも無ければ空＝SNS の行は 1 バイトも変えない）。"""
    if kind is None:
        if blog_id is not None or delivery is not None or url is not None:
            raise ValueError("invalid_sent_kind")
        return {}
    if kind not in HATENA_KINDS:
        raise ValueError("invalid_sent_kind")
    out = {"kind": kind}
    if blog_id is not None:
        if not isinstance(blog_id, str) or not _BLOG_ID_RE.fullmatch(blog_id):
            raise ValueError("invalid_blog_id")
        out["blog_id"] = blog_id
    if delivery is not None:
        if not isinstance(delivery, str) or not _DELIVERY_RE.fullmatch(delivery):
            raise ValueError("invalid_delivery")
        out["delivery"] = delivery
    if url is not None:
        if (not isinstance(url, str) or not url.startswith(("https://", "http://")) or len(url) > 2048
                or CONTROL_URL_RE.search(url)):
            raise ValueError("invalid_url")
        out["url"] = url
    return out


CONTROL_URL_RE = _re.compile(r"[\x00-\x20\x7f]")
CONTROL_TEXT_RE = _re.compile(r"[\x00-\x1f\x7f]")


def _origin_fields(origin, *, prefix: str) -> dict:
    import re
    if (type(origin) is not dict or set(origin) - {"via", "credential"}
            or origin.get("via") not in ORIGIN_VIAS
            or origin.get("credential") is not None
            and (not isinstance(origin["credential"], str)
                 or not re.fullmatch(r"[0-9a-f]{12}", origin["credential"]))):
        raise ValueError("invalid_origin")
    out = {prefix + "via": origin["via"]}
    if origin.get("credential"):
        out[prefix + "credential"] = origin["credential"]
    return out


RETRACT_KEYS = ("retracted_at", "retracted_by", "retract_reason")


def mark_retracted(state_dir: str, post_id: str, *, retracted_at: str,
                   retracted_by: str, retract_reason: str, origin: dict | None = None) -> str:
    """取り下げの 3 項目を `sent/<post_id>.json` に**足す**（設計 v2 §4.3・v2.1-B）。

    **消さない**——送った本文・hash・時刻はそのまま残り、`retracted_at` 等が
    加わるだけ。記録が無ければ `FileNotFoundError`（無いものに足さない）。
    """
    p = path_for(state_dir, post_id)
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"sent の記録が object ではありません: {p}")
    data["retracted_at"] = retracted_at
    data["retracted_by"] = retracted_by
    data["retract_reason"] = retract_reason
    if origin is not None:
        data.update(_origin_fields(origin, prefix="retracted_"))
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    return p


def mark_bookmark_result(state_dir: str, post_id: str, *, result: str, at: str, remote: dict | None = None) -> str:
    """ブックマークの行の `result` を `unknown` から決まった値へ（設計 4.0 S4 §3.2・§7）。**足す**だけ。

    `unknown` 以外の行は変えない（`ValueError("result_fixed")`）。`result` は `confirmed` か `not_found`。
    `reconciled_at` に時刻を残す。`remote` は `confirmed` のときだけ（応答のブックマークの 3 欄）。
    """
    if result not in ("confirmed", "not_found"):
        raise ValueError("invalid_result")
    p = path_for(state_dir, post_id)
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("kind") not in BOOKMARK_KINDS:
        raise ValueError("invalid_sent_kind")
    if data.get("result") != "unknown":
        raise ValueError("result_fixed")
    data["result"] = result
    data["reconciled_at"] = at
    if remote is not None:
        if result != "confirmed" or type(remote) is not dict or set(remote) - set(_REMOTE_KEYS):
            raise ValueError("invalid_remote")
        data["remote"] = {k: remote[k] for k in _REMOTE_KEYS if k in remote}
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    return p


BOOKMARK_EDIT_RESULTS = ("confirmed", "unknown", "not_applied")


def _check_edit_values(comment, tags) -> None:
    if not isinstance(comment, str) or len(comment) > 100 or CONTROL_TEXT_RE.search(comment):
        raise ValueError("invalid_comment")
    if type(tags) is not list or len(tags) > 10 or any(not _plain_text(t, 100) for t in tags):
        raise ValueError("invalid_tags")


def _save(p, data) -> None:
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)


def pending_bookmark_edit(row) -> int | None:
    """ブックマークの行の `attempts[]` のうち、結果不明の直し（`op: edit`・`result: unknown`）の番号。無ければ None。"""
    for index, attempt in enumerate((row or {}).get("attempts") or []):
        if isinstance(attempt, dict) and attempt.get("op") == "edit" and attempt.get("result") == "unknown":
            return index
    return None


def mark_bookmark_edit(state_dir: str, post_id: str, *, result: str, edited_at: str, by: str, via: str | None,
                       comment: str, tags: list, approved_fingerprint: str, remote: dict | None = None) -> str:
    """ブックマークの直し（設計 S4 §21.5）を `sent/bm:` の行に**足す**。

    `confirmed`: 行の `text`・`tags`・`body_hash` を直したあとの値にし、`edited_at` を足す。前の値は
    `attempts[]` に `op: edit` の 1 行（`previous_comment`・`previous_tags`）。`unknown`: 行の中身は変えず、
    `attempts[]` に `result: unknown` の行を足す（照合で決める）。`sent_at`・`result`・追加の指紋は変えない。
    行が無い・ブックマークの行でない・`confirmed` でない・消した印がある → `ValueError`。
    """
    from . import approval
    if result not in ("confirmed", "unknown"):
        raise ValueError("invalid_result")
    _check_edit_values(comment, tags)
    p = path_for(state_dir, post_id)
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if (not isinstance(data, dict) or data.get("kind") != "bookmark_add" or data.get("result") != "confirmed"
            or data.get("retracted_at") or pending_bookmark_edit(data) is not None):
        raise ValueError("bookmark_not_editable")
    attempt = {"op": "edit", "edited_at": edited_at, "by": by, "via": via,
               "previous_comment": data.get("text") or "", "previous_tags": list(data.get("tags") or []),
               "comment": comment, "tags": sorted(tags), "approved_fingerprint": approved_fingerprint,
               "result": result}
    if remote is not None:
        if type(remote) is not dict or set(remote) - set(_REMOTE_KEYS):
            raise ValueError("invalid_remote")
        attempt["remote"] = {k: remote[k] for k in _REMOTE_KEYS if k in remote}
    data["attempts"] = list(data.get("attempts") or []) + [attempt]
    if result == "confirmed":
        data["text"] = comment
        data["tags"] = sorted(tags)
        data["body_hash"] = approval.compute_body_hash(comment)
        data["edited_at"] = edited_at
    _save(p, data)
    return p


def resolve_bookmark_edit(state_dir: str, post_id: str, *, result: str, at: str) -> str:
    """結果不明の直しを照合で決める（`confirmed` なら行の中身も直す・`not_applied` は前のまま）。"""
    from . import approval
    if result not in ("confirmed", "not_applied"):
        raise ValueError("invalid_result")
    p = path_for(state_dir, post_id)
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    index = pending_bookmark_edit(data) if isinstance(data, dict) else None
    if index is None:
        raise ValueError("result_fixed")
    attempt = data["attempts"][index]
    attempt["result"] = result
    attempt["reconciled_at"] = at
    if result == "confirmed":
        data["text"] = attempt["comment"]
        data["tags"] = list(attempt["tags"])
        data["body_hash"] = approval.compute_body_hash(attempt["comment"])
        data["edited_at"] = attempt["edited_at"]
    _save(p, data)
    return p


POST_EDIT_RESULTS = ("confirmed", "unknown")


def mark_post_edit(state_dir: str, post_id: str, *, result: str, edited_at: str, by: str,
                   text: str, reason: str | None = None, digest: str | None = None) -> str:
    """SNS の投稿の直し（`thth edit`・Meta 計画 段 1）を `sent/<post_id>.json` に**足す**。

    `confirmed`: 行の `text`・`body_hash` を直したあとの値にし、`edited_at` を足す。前の本文は
    `edits[]` の 1 行（`previous_text`）に残す。`unknown`（時間切れ・5xx）: 行の中身は変えず、
    `edits[]` に `result: unknown` の行だけ足す（媒体の画面で確かめる）。`sent_at`・
    `approved_fingerprint`（最初の承認）は変えない。**ブックマークの `attempts[]` とは別の鍵**
    ——ブックマークの照合が SNS の直しを拾わないように。行が無い・取り下げ済みは `ValueError`。
    """
    from . import approval
    if result not in POST_EDIT_RESULTS:
        raise ValueError("invalid_result")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("invalid_text")
    p = path_for(state_dir, post_id)
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("kind") is not None or data.get("retracted_at"):
        raise ValueError("post_not_editable")
    entry = {"edited_at": edited_at, "by": by, "previous_text": data.get("text") or "",
             "text": text, "result": result}
    if reason:
        entry["reason"] = reason
    if digest:
        entry["digest"] = digest
    data["edits"] = list(data.get("edits") or []) + [entry]
    if result == "confirmed":
        data["text"] = text
        data["body_hash"] = approval.compute_body_hash(text)
        data["edited_at"] = edited_at
    _save(p, data)
    return p


def retracted_records(state_dir: str) -> list:
    """`sent/` のうち取り下げ済み（`retracted_at` あり）の記録。"""
    return [row for row in records(state_dir) if row.get("retracted_at")]


def read(state_dir: str, post_id: str) -> dict | None:
    p = path_for(state_dir, post_id)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def records(state_dir: str, *, errors: list | None = None) -> list:
    """`sent/` に残っている記録を全部読む（**壊れた 1 本で全部を落とさない**）。

    **なぜ要るか**（2026-09-13 の本番）。`thth send`（同席の様態）で出した 1 本が
    `board` の `last_post` にも `posts` の突合にも現れなかった。どちらも
    **queue の front-matter しか見ていなかった**からで、同席の様態には書き戻す
    front-matter が無い（`core._send_locked()` の註）。**出した事実がここにしか
    無い**以上、読み手もここを見なければ「出していない」と言ってしまう。

    読めなかったファイル・辞書でないもの・`post_id` の無いものは飛ばす
    （読むだけの口が 1 本の壊れた記録で止まらないように）。飛ばしたものは
    `errors` に 1 行ずつ積む（渡されていれば）。

    **ファイル名から `post_id` を作らない**（監査 2 回目・P3-7）。前は中身に
    `post_id` が無ければ `postid.from_filename()` で復元していたが、**名前は
    誰でも置ける**——`sent/` に `at%3A%2F%2F別人の投稿.json` を 1 つ置けば、
    `thth board` の `last_post`・`thth posts` の突合・採取の母集団に、
    **THTH が出していない `post_id` が「出したもの」として入る**。名前は
    パスの都合（`postid.to_filename()`）であって、記録の中身ではない。
    `write()` は必ず `post_id` を書くので、無いものは**壊れた記録**。
    """
    d = dir_for(state_dir)
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = []
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(d, name)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            if errors is not None:
                errors.append(f"sent の記録を読めません: {name}")
            continue
        if not isinstance(data, dict):
            if errors is not None:
                errors.append(f"sent の記録が object ではありません: {name}")
            continue
        post_id = data.get("post_id")
        if not isinstance(post_id, str) or not post_id.strip():
            if errors is not None:
                errors.append(
                    f"sent の記録に post_id がありません: {name}"
                    f"（**ファイル名からは作りません**——名前は誰でも置けます）")
            continue
        out.append(data)
    return out


def post_ids(state_dir: str) -> set:
    """`sent/` に記録のある `post_id` の集合（＝**THTH を通して出したもの**）。"""
    return {row["post_id"] for row in records(state_dir)}


def _parse_sent_at(raw):
    """**綴りの揺れを吸うのは `jst.parse()` の仕事**（監査 2 回目・P3-11）。

    ここにあった実装を `jst` へ移した——同じ仕事が採取の側にも 2 つあり、
    **そちらだけ `Z` を読めなかった**（同じ投稿が board には出るのに採取の
    母集団から落ちる）。名前は呼び出し側のために残す。
    """
    return jst.parse(raw)


def latest_sent(state_dir: str):
    """`sent/` の中で**いちばん新しい記録**を `(JST の時刻, 記録)` で返す。

    時刻を読めない記録は比較に混ぜない（**判らないものを「最新」と言わない**）。
    1 件も無ければ `(None, None)`。
    """
    best_at = None
    best_row = None
    for row in records(state_dir):
        at = _parse_sent_at(row.get("sent_at"))
        if at is None:
            continue
        if best_at is None or at > best_at:
            best_at, best_row = at, row
    return best_at, best_row
