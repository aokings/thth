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


def compute_approved_components(*, section: str, account: str, reply_to: str | None,
                                 topic: str | None,
                                 publish_at: str | datetime.datetime) -> dict:
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
    return {
        "body": (section or "").strip(),
        "account": (account or "").strip(),
        "reply_to": (reply_to or "").strip(),
        "topic": queuefile.normalize_topic(topic) or "",
        "publish_at": _normalize_publish_at(publish_at),
    }


def compute_approved_sha(*, section: str, account: str, reply_to: str | None,
                          topic: str | None, publish_at: str | datetime.datetime) -> str:
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

    返り値は sha256 の 16 進ダイジェスト（64 文字）。**この定義は
    `tests/test_approval.py` で固定する**（受け入れ 11）。将来この関数の中身を
    変えると、いままで書かれた `approved_sha` が一斉に「食い違う」扱いになる
    （＝いま approved のファイルが軒並み `approval_stale` になる）。変えるときは
    それが分かった上で意図的にやること。
    """
    components = compute_approved_components(
        section=section, account=account, reply_to=reply_to, topic=topic,
        publish_at=publish_at)
    joined = _SEP.join(components[key] for key in _COMPONENT_ORDER)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def compute_send_digest(*, text: str, account: str, reply_to: str | None,
                         topic: str | None, length: int = 12) -> str:
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
    joined = _SEP.join(parts)
    full = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return full[:length]


def compute_body_hash(text: str) -> str:
    """本文だけの sha256（外部レビュー §3・受け入れ 9・10）。

    公開の直前に `state/<account>/inflight.json` へ、公開の直後に
    `state/<account>/sent/<post_id>.json` へ書く hash はこちら。`approved_sha` と
    違い account・reply_to・topic・publish_at は含めない — 「送った本文そのものと
    いま repo にある本文が同じか」だけを見るための、本文単体のハッシュ
    （メタデータが変わっても本文が同じなら一致してよい）。
    """
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()
