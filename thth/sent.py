"""`state/<account>/sent/<post_id>.json`（外部レビュー §3・受け入れ 9・10）。

公開に**成功した直後**、書き戻し（front-matter の書き換え）より前に書く。**これが
正本**（VM 側）: 「実際に送った本文そのもの」と、そのハッシュ、送った時刻を動かせない
記録として残す。書き戻しの直前に、いま repo にある本文のハッシュをこれと突き合わせ、
食い違えば front-matter を書き換えない（`thth.core._throw_chosen()` 参照）。
"""
from __future__ import annotations

import datetime
import json
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
          link_urls: list | None = None) -> str:
    """送った本文そのものを動かせない記録として保存する。返り値は書いたパス。

    `approved_fingerprint`（外部レビュー再々レビュー P1・1）は公開直前に固定した
    5 項目（本文・account・reply_to・topic・publish_at）の指紋。`body_hash` は
    後方互換のため残す（本文だけの hash・`tests/test_sent_integrity.py` が参照）。

    **書けない `post_id` はここでも断る**（監査 2 回目・P3-9）。呼び出し側
    （`core`・`threadthrow`）は公開の直後に確かめているが、**書く側にも 1 枚置く**
    ——`post_id` はそのままファイル名になるので、長すぎれば `OSError`、制御文字が
    入れば読めない名前が残る。**書く場所に近いほうで断ると、新しい呼び出し口が
    増えても穴が空かない。**
    """
    if not postid_mod.is_usable(post_id):
        raise ValueError(
            f"post_id を記録の鍵にできません（空・`.`／`..`・制御文字・長すぎる）:"
            f" {post_id!r}")
    d = dir_for(state_dir)
    os.makedirs(d, exist_ok=True)
    p = path_for(state_dir, post_id)
    tmp = p + ".tmp"
    data = {"post_id": post_id, "text": text, "body_hash": body_hash, "sent_at": sent_at,
            "approved_fingerprint": approved_fingerprint, "reply_to": reply_to}
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
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)
    return p


RETRACT_KEYS = ("retracted_at", "retracted_by", "retract_reason")


def mark_retracted(state_dir: str, post_id: str, *, retracted_at: str,
                   retracted_by: str, retract_reason: str) -> str:
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
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)
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
