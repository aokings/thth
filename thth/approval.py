"""承認を「見た本文」に結び付ける hash（外部レビュー §1・§1b・受け入れ 1〜4・6・11）。

masaru が見たものだけが出る、という設計の中心を機械で守るための共通関数。
`thth approve`（CLI）が承認時にこの hash を front-matter の `approved_sha` に書き、
`select.select_one()` が投げる直前にもう一度計算して一致するかを検査する。
`thth send`（同席の送信）の確認用 digest もここに置く（本文の正規化を使い回す。
レビュー §1b: 「digest の対象は 1 と同じ正規化」）。

**この 2 つの定義自体を test_approval.py で固定する**（受け入れ 11）。順序・区切り・
正規化の有無を変えるときは、その変更が意図的であることをテストの変更で示すこと
（気づかず変えて既存の approved_sha が一斉に無効になる事故を防ぐ）。
"""
from __future__ import annotations

import datetime
import hashlib

from . import queuefile
from . import tags as tags_mod


def effective_section(section: str, account_cfg: dict | None, topic: str | None) -> str:
    """Exact text shown and sent for a v1 queue item under this account policy."""
    cfg = account_cfg or {}
    return tags_mod.prepared(cfg.get("media"), section,
                             queuefile.normalize_topic(topic),
                             hashtags=bool(cfg.get("hashtags", True)))

# フィールド区切り: ASCII unit separator（本文にまず現れない制御文字。
# 区切り文字の衝突でハッシュが化けるのを避けるため、印字可能文字を避けた）。
_SEP = "\x1f"


def _normalize_publish_at(publish_at: str | datetime.datetime) -> str:
    """`publish_at` を `datetime.isoformat()` の文字列に揃える。

    front-matter の生文字列（例 `2026-09-09T08:00:00+09:00`）と、select.py が
    既に `queuefile.parse_publish_at()` でパース済みの `datetime` の両方を受け付け、
    どちらを渡しても同じ文字列になるようにする（表記のわずかな違い——将来 tz の
    書き方が変わる等——でハッシュがずれないようにするための正規化）。
    """
    if isinstance(publish_at, datetime.datetime):
        dt = publish_at
    else:
        dt = queuefile.parse_publish_at(publish_at)
    return dt.isoformat()


_COMPONENT_ORDER = ("body", "account", "reply_to", "topic", "publish_at")

# **承認を通る書き込みの口**（設計 v2 §4.3・v2.1-B・2026-09-14）。場所と Instagram
# 共有は「公開の側を変える」ので承認の対象に入れる——承認後に `location_id:` や
# `share_to_instagram:` を変えれば `approval_stale`。
#
# **既存の `approved_sha` を 1 つも無効にしない**ために、これらは**値があるときだけ**
# 5 項目の後ろに `\x1f` 区切りで足す（`location_id=<id>`・`share_to_instagram=true`）。
# 無いときの入力バイト列は今までと 1 バイトも変わらない（`tests/test_approval.py`
# の固定はそのまま）。鍵名付きで足すのは、`location_id` 無し＋共有ありと
# `location_id="true"` を同じ列にしないため。
_OPTION_ORDER = ("location_id", "share_to_instagram", "media_sources", "media_manifest")


def is_true(raw) -> bool:
    """front-matter の真偽（`true` / `yes` / `1`・大文字小文字を問わない）。"""
    if isinstance(raw, bool):
        return raw
    return str(raw or "").strip().lower() in ("true", "yes", "1")


def publish_options(fm: dict) -> dict:
    """front-matter から承認の対象になる任意項目を抜く（`compute_approved_*` に渡す形）。"""
    fm = fm or {}
    return {
        "location_id": (fm.get("location_id") or "").strip() or None,
        "share_to_instagram": is_true(fm.get("share_to_instagram")),
    }


def reply_to_for_fingerprint(fm: dict) -> str | None:
    """承認の指紋の `reply_to` 欄に入れる値（設計 3.2.0 §3）。

    `reply_to_file` を持つ原稿は **`file:<名前>`**。承認の対象は「どの原稿への
    返信か」であって、解決後の post_id ではない（要望どおり・解決した post_id は
    入れない）。`reply_to`（post_id 直書き）は従前どおりその値——**既存の
    `approved_sha` は 1 つも変わらない**。

    `reply_to` と `reply_to_file` は同じ欄を使うので、承認のあとにどちらかへ
    書き換えれば指紋が変わって `approval_stale` で落ちる。両方あるとき（lint も
    select も断る）は、どちらか一方だけのときの値と一致しない値にする——
    片方を足しただけで承認が生き残る形を作らない。
    """
    fm = fm or {}
    name = queuefile.reply_to_file_of(fm)
    direct = fm.get("reply_to")
    if name is None:
        return direct
    if direct is not None and str(direct).strip():
        return f"{queuefile.REPLY_TO_FILE_MARK}{name}\x1e{str(direct).strip()}"
    return queuefile.REPLY_TO_FILE_MARK + name


def _option_components(location_id, share_to_instagram) -> dict:
    return {
        "location_id": (location_id or "").strip() if isinstance(location_id, str) else "",
        "share_to_instagram": "true" if is_true(share_to_instagram) else "",
    }


def _option_parts(components: dict) -> list:
    parts = []
    for key in _OPTION_ORDER:
        value = components.get(key) or ""
        if value:
            parts.append(f"{key}={value}")
    return parts


def compute_approved_components(*, section: str, account: str, reply_to: str | None,
                                 topic: str | None,
                                 publish_at: str | datetime.datetime,
                                 location_id: str | None = None,
                                 share_to_instagram=False, media_sources=None, media_manifest=None) -> dict:
    """`compute_approved_sha()` が hash する前の、5 項目それぞれの正規化済みの値。

    外部レビュー第 3 巡・持ち越し項目 C: 指紋（`compute_approved_sha()` の
    戻り値）が食い違ったとき、hash からは「どの項目が違うか」を復元できない。
    公開直前に固定した指紋と、書き戻し前・rebase 後に読み直した現在の内容の
    **両方をこの関数に通して**キーごとに突き合わせれば、違った項目名だけを
    拾える（`thth.core._mismatch_fields()` 参照）。**正規化の定義はここが正本**
    ——`compute_approved_sha()` はこの戻り値を `_COMPONENT_ORDER` の順に連結して
    hash するだけで、sha の入力バイト列自体はいままでと変えていない
    （`tests/test_approval.py` の固定を壊さない）。
    """
    out = {
        "body": (section or "").strip(),
        "account": (account or "").strip(),
        "reply_to": (reply_to or "").strip(),
        "topic": queuefile.normalize_topic(topic) or "",
        "publish_at": _normalize_publish_at(publish_at),
    }
    out.update(_option_components(location_id, share_to_instagram))
    from . import media as media_mod
    component = media_mod.fingerprint_component(media_sources)
    if component:
        out["media_sources"] = component
    prepared = media_mod.prepared_component(media_manifest)
    if prepared:
        out["media_manifest"] = prepared
    return out


def compute_approved_sha(*, section: str, account: str, reply_to: str | None,
                          topic: str | None, publish_at: str | datetime.datetime,
                          location_id: str | None = None,
                          share_to_instagram=False, media_sources=None, media_manifest=None) -> str:
    """承認の対象を固定する sha256（外部レビュー §1・受け入れ 1〜4・11）。

    ハッシュの入力は、次の 5 つをこの順序で `\\x1f`（ASCII unit separator）区切りに
    連結した UTF-8 バイト列:

      1. `section`    — 媒体の節の本文。前後の空白を落として比較する
                         （`queuefile.extract_section()` の返り値と同じ規約。
                         ここでも念のためもう一度 `.strip()` する）。
      2. `account`    — front-matter の `account` の値（`.strip()` のみ）。
      3. `reply_to`   — front-matter の `reply_to` の値（`.strip()` のみ）。
                         無ければ空文字列。
      4. `topic`      — `queuefile.normalize_topic()` で正規化（前後の空白と
                         先頭の `#` を落とす）。無ければ空文字列。
      5. `publish_at` — `datetime` として解釈した上で `.isoformat()` した文字列
                         （`_normalize_publish_at()` 参照）。

    **任意項目**（v2.1-B）: `location_id` があれば 6 つめとして `location_id=<id>`、
    `share_to_instagram` が真なら次に `share_to_instagram=true` を同じ区切りで足す。
    **どちらも無ければ 5 項目のまま**（既存の承認を無効にしない）。

    返り値は sha256 の 16 進ダイジェスト（64 文字）。**この定義は
    `tests/test_approval.py` で固定する**（受け入れ 11）。将来この関数の中身を
    変えると、いままで書かれた `approved_sha` が一斉に「食い違う」扱いになる
    （＝いま approved のファイルが軒並み `approval_stale` になる）。変えるときは
    それが分かった上で意図的にやること。
    """
    components = compute_approved_components(
        section=section, account=account, reply_to=reply_to, topic=topic,
        publish_at=publish_at, location_id=location_id,
        share_to_instagram=share_to_instagram, media_sources=media_sources, media_manifest=media_manifest)
    parts = [components[key] for key in _COMPONENT_ORDER] + _option_parts(components)
    joined = _SEP.join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# `_mismatch_fields()` が突き合わせる鍵（5 項目＋任意項目）。
COMPARED_KEYS = _COMPONENT_ORDER + _OPTION_ORDER


# `thth approve` の二段確認で使う digest の桁数（`approved_sha` の先頭から取る）。
# `thth send` の digest と同じ 12 桁にそろえる（手順を 1 つにする）。
APPROVE_DIGEST_LENGTH = 12


def compute_bundle_digest(approved_shas: list, length: int = APPROVE_DIGEST_LENGTH) -> str:
    """複数本まとめて承認するときの**束の digest**（asmon 関東セッション指摘 2026-09-10）。

    各ファイルの `approved_sha` を**並べ替えてから**連結した文字列の sha256 の
    先頭 `length` 桁。並べ替えるのは、ファイルを渡す順で digest が変わらないように
    するため（同じ束なら同じ digest）。

    **どれか 1 本でも中身が変われば、そのファイルの `approved_sha` が変わり、
    束の digest も変わる。** 「見せたもの＝承認したもの」の保証は、本数が増えても
    崩れない。
    """
    shas = sorted(approved_shas)
    if len(shas) == 1:
        # 1 本なら「束」という概念は要らない。そのファイルの digest をそのまま使う
        # （1 本のときの手順を、複数本を足したせいで変えない）。
        return shas[0][:length]
    joined = _SEP.join(shas)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def compute_send_digest(*, text: str, account: str, reply_to: str | None,
                         topic: str | None, length: int = 12, media_sources=None, media_manifest=None) -> str:
    """`thth send` の dry-run が出す短い digest（外部レビュー §1b・受け入れ 6）。

    `compute_approved_sha()` と同じ正規化（本文＋account＋reply_to＋topic）だが、
    **`publish_at` は含めない**（`send` は同席の様態で予約時刻という概念を持たない
    ため。裁定文書の指示どおり）。sha256 の先頭 `length` 桁（既定 12・16 進）を返す。

    `--production --confirm <digest>` はここで返した文字列と完全一致するかだけを見る
    （大文字小文字も区別する。dry-run の出力をそのままコピペする運用のため）。
    """
    parts = [
        (text or "").strip(),
        (account or "").strip(),
        (reply_to or "").strip(),
        queuefile.normalize_topic(topic) or "",
    ]
    from . import media as media_mod
    component = media_mod.fingerprint_component(media_sources)
    if component:
        parts.append("media_sources=" + component)
    prepared = media_mod.prepared_component(media_manifest)
    if prepared:
        parts.append("media_manifest=" + prepared)
    joined = _SEP.join(parts)
    full = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return full[:length]


# `thth retract` の二段確認の digest（設計 v2 §4.3・v2.1-B）。
_RETRACT_VERSION = "thth-retract-1"


def compute_retract_digest(*, post_id: str, account: str, reason: str,
                            length: int = APPROVE_DIGEST_LENGTH) -> str:
    """`thth retract` の一段目が出す digest（`compute_send_digest()` と同じ型）。

    `thth-retract-1`・`post_id`・`account`・`reason` をこの順で `\x1f` 区切りに
    連結した sha256 の先頭 `length` 桁。先頭に版の語を置くのは、同じ `post_id` と
    `account` から作る他の digest（承認・送信）と**偶然にも一致させない**ため
    ——取り下げの確認に、別の口が出した digest を流用できてはいけない。
    """
    parts = [
        _RETRACT_VERSION,
        (post_id or "").strip(),
        (account or "").strip(),
        (reason or "").strip(),
    ]
    joined = _SEP.join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def compute_body_hash(text: str) -> str:
    """本文だけの sha256（外部レビュー §3・受け入れ 9・10）。

    公開の直前に `state/<account>/inflight.json` へ、公開の直後に
    `state/<account>/sent/<post_id>.json` へ書く hash はこちら。`approved_sha` と
    違い account・reply_to・topic・publish_at は含めない — 「送った本文そのものと
    いま repo にある本文が同じか」だけを見るための、本文単体のハッシュ
    （メタデータが変わっても本文が同じなら一致してよい）。
    """
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


# --- スレッド連投（`thth: 2`）の承認指紋（設計 §2・§5・工程 2） -------------

_BUNDLE_VERSION = "thth-bundle-1"
_SEG = "\x1e"          # ASCII record separator（段の境界）


def compute_bundle_components(*, segments: list, account: str, topic: str | None,
                               publish_at, continue_until,
                               frozen: list | None = None, media_sources=None, media_manifest=None) -> dict:
    """束の承認対象を項目ごとに正規化する（設計 §2.2・§5）。

    **単なる本文連結ではなく、段の境界と順序を保った配列**（Codex 最終条件 5）。
    連結してしまうと、段の割り方を変えても指紋が同じになる
    ——「2 段で出す」と「1 段で出す」は**別の承認**でなければならない。

    **`frozen`（公開済み部分の記録）は指紋に入れない**——承認の意味は
    「この段の並びを、この account で、この時刻に、この期限まで出す」まで。

    Codex の最終条件 2 は「再承認は**固定した公開済み部分の記録と、変更後の
    残り**をまとめて対象にする」だった。**その意図は満たすが、指紋の入力には
    しない。** 入れると **1 段出すたびに `frozen` が増えて指紋が変わり、
    編集していない束の続きが `approval_stale` になって出せなくなる**
    （THTH が `approved_sha` を書き換えるわけにはいかない——それは人の承認）。

    代わりに**照合で担保する**:

    - 公開済みの段の本文は `threadthrow._frozen_drift()` が
      `text_sha256` と突き合わせる（**凍結の強制**）
    - 親は `threadrun.resolve_parent()` が**永続化した公開記録**から決める
      （**承認された親子関係を別の投稿へ付け替えられない**——不変条件そのもの）
    - 承認画面には**公開済みの段とその `post_id` を明示する**（人が
      「何が既に出ているか」を見た上で残りを承認する）

    `frozen` 引数は**承認画面の表示**のために受け取るだけで、hash には使わない。
    """
    normalized = [(s or "").strip() for s in segments]
    frozen_parts = []
    for row in (frozen or []):
        frozen_parts.append(
            f"{row.get('index')}:{row.get('post_id') or ''}"
            f":{row.get('text_sha256') or ''}")
    out = {
        "version": _BUNDLE_VERSION,
        "segments": _SEG.join(normalized),
        "segment_count": str(len(normalized)),
        "account": (account or "").strip(),
        "topic": queuefile.normalize_topic(topic) or "",
        "publish_at": _normalize_publish_at(publish_at),
        "continue_until": _normalize_publish_at(continue_until),
        "frozen": _SEG.join(frozen_parts),
    }
    from . import media as media_mod
    if media_sources is not None:
        if not isinstance(media_sources, list) or len(media_sources) != len(segments):
            raise media_mod.MediaError("media: one source array per segment required")
        components = [media_mod.fingerprint_component(rows) for rows in media_sources]
        if any(components):
            import json
            out["media_sources"] = json.dumps(components, ensure_ascii=False, separators=(",", ":"))
    if media_manifest is not None:
        if not isinstance(media_manifest, list) or len(media_manifest) != len(segments):
            raise media_mod.MediaError("media: one prepared manifest per segment required")
        prepared = [media_mod.prepared_component(row) for row in media_manifest]
        if any(prepared):
            import json
            out["media_manifest"] = json.dumps(prepared, ensure_ascii=False, separators=(",", ":"))
    return out


# **`frozen` は入らない**（上の理由）。
_BUNDLE_ORDER = ("version", "segments", "segment_count", "account", "topic",
                 "publish_at", "continue_until")


def compute_bundle_sha(*, segments: list, account: str, topic: str | None,
                        publish_at, continue_until, frozen: list | None = None, media_sources=None, media_manifest=None) -> str:
    """束の `approved_sha`。

    v1 の `compute_approved_sha()` とは**別の入力バイト列**（先頭に
    `thth-bundle-1` が入る）。**v1 の指紋には触れない**ので、いま approved の
    84 本は影響を受けない（Codex 最終条件 5）。
    """
    components = compute_bundle_components(
        segments=segments, account=account, topic=topic, publish_at=publish_at,
        continue_until=continue_until, frozen=frozen, media_sources=media_sources, media_manifest=media_manifest)
    parts = [components[key] for key in _BUNDLE_ORDER]
    if components.get("media_sources"):
        parts.append("media_sources=" + components["media_sources"])
    if components.get("media_manifest"):
        parts.append("media_manifest=" + components["media_manifest"])
    joined = _SEP.join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def segment_sha(text: str) -> str:
    """1 段の本文の指紋。**公開記録に残して、あとから照合する。**"""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def approved_fields(prepared, by, at):
    """Identical approval writeback for local two-stage and server human receipt."""
    return {'status':'approved', 'approved_sha':prepared['approved_sha'],
            'approved_at':at, 'approved_by':by, 'revoked_at':None,
            'revoked_by':None, 'revoked_reason':None}
