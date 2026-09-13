"""`thth share` —— 泉に落とす水を**手元に積む**（設計 v2 §2・§3・裁定 §7-3）。

**既定は off。** `$THTH_ROOT/state/share/config.json` が無ければ off。
**off なら 1 バイトも積まない**——`state/share/` の下に何も作らない。

**送る先はまだ無い。** v2.0.0 の範囲に泉のサーバ（v2-5）は入っていない
（設計 v2 §7）。ここがするのは `state/share/outbox/<YYYY-MM>.ndjson` に積むこと
だけで、**ネットワークには触らない**。`thth share log` で積んだ全部が読める。
**門の道具が黙って何かを送った瞬間に信用が消える**（§3 の表）ので、
「送る前に、送るはずのものが全部読める」状態を先に作る。

**この module は既存の経路の外側にいる。** `thth/collect.py` と `thth/topics.py` は
1 行も変わらない——`sync()` が**出来上がった台帳を読んで**積む。だから:

  - **`thth throw` は share に依存しない。** share を丸ごと壊しても投稿は通る
    （`tests/test_share_safety.py`）。
  - 積むのをやめても、採るほうは何も変わらない。

**落ちるもの**（§2 の表）:

  - 観測 …… 語・audience（誰がいたか）・型・取得状態・観測者の仮名・日時
  - スレッドの形 …… post_id の**ハッシュ**・媒体・型・語・時刻帯・刻みごとの数

**落ちないもの**（§2・構造で保証）: 原稿本文・返信の本文・返信者の `username`・
自分の判断（`verdict`）・台帳（accounts）・トークン・利用者 repo のパス・生の
post_id。**書く直前に機械で検査する**（`_assert_clean()`）——「入れないように
書いた」ではなく「入っていたら落ちる」にしないと、いつか静かに混ざる。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time

from . import accounts as accounts_mod
from . import jst

SCHEMA_OBSERVATION = "thth.share.observation.v1"
SCHEMA_THREAD_SHAPE = "thth.share.thread_shape.v1"
SCHEMA_RETRACTION = "thth.share.retraction.v1"

# 観測者の仮名の桁数（設計 v2 §2「インストール時に生成する乱数 ID」）。
# **16 進で 16 桁**にする——全部が数字の値だけは採らない（`_assert_clean()` の
# 「10 桁以上の数字だけの文字列は生の post_id を疑う」に自分で引っかかるため）。
OBSERVER_ID_HEX = 16

# **自由文はここで切る。** audience は §2 で「落ちるもの」だが、原稿本文が
# 間違って回ってきたときに 1 万字が積まれる経路を残さない。
MAX_FREE_TEXT = 200

# **落ちないもの**（§2）。行のどこか（入れ子の奥でも）にこの鍵があれば落とす。
FORBIDDEN_KEYS = frozenset({
    "text", "body", "content", "message", "draft", "preview",
    "username", "handle", "author", "by",
    "verdict", "decision", "note", "reason",
    "access_token", "token", "refresh_token", "secret", "app_secret",
    "repo_dir", "queue_dir", "replies_dir", "env", "path",
    "account", "accounts", "user_id",
    "post_id", "root_post", "replied_to", "permalink", "url",
})

# 生の post_id の形。Threads は数字だけ、Bluesky は AT URI（`thth/postid.py`）。
_RAW_POST_ID = re.compile(r"^\d{10,}$")


class ShareError(Exception):
    """share の置き場が壊れている・書けない。**黙って諦めない。**"""


# --------------------------------------------------------------------------
# 置き場
# --------------------------------------------------------------------------

def root() -> str:
    return os.path.join(accounts_mod.thth_root(), "state", "share")


def config_path() -> str:
    return os.path.join(root(), "config.json")


def observer_id_path() -> str:
    return os.path.join(root(), "observer_id")


def _salt_path() -> str:
    """post_id を結ぶための塩。**この値は 1 バイトも積まない。**

    塩を積むと、積んだ側で `sha256(塩 + post_id)` を総当たりできてしまう
    （Threads の post_id は数字で、空間が狭い）。塩は手元にだけ置き、
    **同じ投稿の刻みを結ぶ**という目的にだけ使う（§2「permalink に戻せない」）。
    """
    return os.path.join(root(), "post_salt")


def outbox_dir() -> str:
    return os.path.join(root(), "outbox")


def outbox_path(now=None) -> str:
    now = now if now is not None else jst.now_jst()
    return os.path.join(outbox_dir(), f"{jst.month_str(now)}.ndjson")


# --------------------------------------------------------------------------
# on / off（既定 off）
# --------------------------------------------------------------------------

def is_on() -> bool:
    """**config が無ければ off。** 壊れていても off（読めないものを on にしない）。"""
    p = config_path()
    if not os.path.exists(p):
        return False
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ShareError(f"share の設定が読めません: {p}（{e}）。"
                          f"直すまで積みません（消せば off に戻ります）") from e
    if not isinstance(data, dict):
        raise ShareError(f"share の設定の形が違います: {p}"
                          f"（いちばん外側が object ではありません）")
    return data.get("enabled") is True


def config() -> dict:
    p = config_path()
    if not os.path.exists(p):
        return {"enabled": False, "changed_at": None, "by": None}
    is_on()  # 壊れていればここで落ちる
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def set_enabled(enabled: bool, *, by: str = "", now=None) -> dict:
    """on / off を切り替える。**on にした時刻と誰がを残す。**"""
    now = now if now is not None else jst.now_jst()
    data = {"enabled": bool(enabled), "changed_at": jst.iso(now),
            "by": (by or "").strip() or None}
    os.makedirs(root(), exist_ok=True)
    _write_json(config_path(), data)
    if enabled:
        # **on にしたときに仮名と塩を作る**（off のままなら作らない）。
        observer_id()
        _salt()
    return data


def _write_json(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# 観測者の仮名
# --------------------------------------------------------------------------

def observer_id() -> str:
    """`state/share/observer_id`。**初回に乱数で作る**（設計 v2 §2）。

    仮名であって匿名ではない——同じインストールの観測は結べる。**本名・
    アカウント名・ホスト名からは作らない**（作ると復元できる）。
    """
    p = observer_id_path()
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            value = f.read().strip()
        if value:
            return value
    while True:
        value = secrets.token_hex(OBSERVER_ID_HEX // 2)
        # 全部が数字なら引き直す（`_assert_clean()` の生 post_id の検査に
        # 自分で引っかかる値を配らない。10 兆分の 5 程度だが、**起きたときに
        # 原因が分からない**のがいちばん高くつく）。
        if not value.isdigit():
            break
    os.makedirs(root(), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(value + "\n")
    os.chmod(p, 0o600)
    return value


def _salt() -> str:
    p = _salt_path()
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            value = f.read().strip()
        if value:
            return value
    value = secrets.token_hex(32)
    os.makedirs(root(), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(value + "\n")
    os.chmod(p, 0o600)
    return value


def post_hash(post_id: str) -> str:
    """post_id の**ハッシュ**（§2）。**permalink に戻せない。**

    同じ投稿の刻みを結ぶためだけ。塩は手元にしか無いので、積んだ側で
    総当たりしても元の post_id は出ない。
    """
    if not isinstance(post_id, str) or not post_id.strip():
        raise ValueError(f"post_id が空です: {post_id!r}")
    return hashlib.sha256((_salt() + "\x00" + post_id).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 落ちないものの機械検査（書く直前）
# --------------------------------------------------------------------------

def _assert_clean(row, *, where="") -> None:
    """**落ちないもの**（§2）が 1 バイトも入っていないことを、書く前に確かめる。

    「入れないように書いた」では足りない——上流の台帳に鍵が 1 つ増えただけで
    静かに混ざる。**入っていたら例外で落ちる**（loud reject）。
    """
    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in FORBIDDEN_KEYS:
                    raise ShareError(
                        f"泉に落としてはいけない鍵が行に入っています: "
                        f"{path + '.' + k if path else k}"
                        f"（設計 v2 §2「落ちないもの」{where}）")
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            if "at://" in node:
                raise ShareError(
                    f"生の post_id（AT URI）が行に入っています: {path}"
                    f"（ハッシュにしてください・§2）")
            if _RAW_POST_ID.match(node):
                raise ShareError(
                    f"生の post_id（数字だけの ID）が行に入っています: {path}"
                    f"（ハッシュにしてください・§2）")
            if len(node) > MAX_FREE_TEXT:
                raise ShareError(
                    f"自由文が長すぎます（{len(node)} 字 > {MAX_FREE_TEXT}）: {path}"
                    f"（原稿本文が回ってきていませんか・§2）")

    walk(row, "")


# --------------------------------------------------------------------------
# 積む
# --------------------------------------------------------------------------

def _trim(value, limit=MAX_FREE_TEXT):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:limit]


def _append(row: dict, *, now=None) -> dict | None:
    """1 行積む。**off なら何もしない（ファイルも作らない）。**"""
    if not is_on():
        return None
    _assert_clean(row)
    now = now if now is not None else jst.now_jst()
    path = outbox_path(now)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return row


def enqueue_observation(*, topic, audience=None, kind=None, status=None,
                         observed_at=None, row_id=None, now=None) -> dict | None:
    """観測を 1 件（§2「語・誰がいたか・型・取得状態・観測者の仮名・日時」）。

    **`verdict` は受け取らない。** 「合っているか」はそのプロジェクトの判断で、
    泉の水ではない（§2「落ちないもの」・`thth/topics.py` の `record()` の注記）。
    **`by`（誰が確かめたか）も受け取らない**——観測者の仮名に置き換わる。
    """
    # **off なら、行を組み立てる前に帰る。** `observer_id()` はファイルを作る
    # ので、`_append()` の中の検査まで持っていくと **off でも `state/share/` が
    # 出来る**（`tests/test_share.py` がここを踏んだ・2026-09-13）。
    if not is_on():
        return None
    now = now if now is not None else jst.now_jst()
    row = {
        "schema": SCHEMA_OBSERVATION,
        "row_id": row_id or f"obs:{secrets.token_hex(8)}",
        "observer": observer_id() if is_on() else None,
        "topic": _trim(topic),
        "audience": _trim(audience),
        "kind": _trim(kind, 40),
        "obs_status": _trim(status, 40),
        "observed_at": _trim(observed_at, 40),
        "enqueued_at": jst.iso(now),
    }
    if not row["topic"]:
        raise ValueError("語が空の観測は積みません")
    return _append(row, now=now)


def enqueue_retraction(*, row_id, now=None) -> dict | None:
    """積んだ観測が**打ち消された**ことを積む（理由は積まない＝自由文）。

    追記だけの outbox で前の行を消せない以上、**打ち消しも 1 行**にする
    （`thth/topics.py` の `retract_note()` と同じ流儀——記録は残り、
    読み出しから外れる）。
    """
    if not is_on():
        return None
    now = now if now is not None else jst.now_jst()
    row = {
        "schema": SCHEMA_RETRACTION,
        "row_id": f"ret:{row_id}",
        "observer": observer_id() if is_on() else None,
        "retracts": row_id,
        "enqueued_at": jst.iso(now),
    }
    return _append(row, now=now)


def enqueue_thread_shape(shape: dict, *, medium=None, now=None) -> dict | None:
    """スレッドの形を 1 投稿ぶん（§2）。

    **`thth/threadshape.py` の出力から、落ちるものだけを選び直す。** 丸ごと
    渡さない——上流に `participants` の内訳や `reason` の自由文が増えたときに、
    ここを通って積まれてしまう。**選ぶ側を白名簿にする。**
    """
    # **off なら、ハッシュを計算する前に帰る。** `post_hash()` は塩のファイルを
    # 作る——off で `state/share/post_salt` が出来ていた（同上）。
    if not is_on():
        return None
    now = now if now is not None else jst.now_jst()
    raw_id = shape.get("post_id")
    if not raw_id:
        return None
    参加者 = shape.get("participants") or {}
    row = {
        "schema": SCHEMA_THREAD_SHAPE,
        "row_id": f"shape:{post_hash(raw_id)}",
        "observer": observer_id() if is_on() else None,
        "post_hash": post_hash(raw_id),
        "medium": _trim(medium if medium is not None else shape.get("medium"), 40),
        "topic": _trim(shape.get("topic")),
        "kind": _trim(shape.get("kind"), 40),
        "hour_band": _trim(shape.get("hour_band"), 40),
        "branches": _num(shape.get("branches")),
        "depth": _num(shape.get("depth")),
        "replies_total": _num(shape.get("replies_total")),
        "author_replies": _num(shape.get("author_replies")),
        "other_replies": _num(shape.get("other_replies")),
        "participants_count": _num(参加者.get("count")),
        "participants_denominator": _num(参加者.get("denominator")),
        "first_reply_min": _num(shape.get("first_reply_min")),
        # **刻みごとの数だけ。** `reason` の自由文も `collected_at` も積まない
        # （前者は落ちないもの、後者は観測者の生活時間が透ける）。
        "growth": _growth_numbers(shape.get("growth")),
        "views_at": _views_numbers(shape.get("views_at")),
        "enqueued_at": jst.iso(now),
    }
    return _append(row, now=now)


def _num(value):
    """数だけ通す。**取れていない刻みは `null`。`0` ではない**（規約 12）。"""
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _growth_numbers(growth) -> dict:
    out = {}
    for mark, slot in (growth or {}).items():
        slot = slot if isinstance(slot, dict) else {}
        out[str(mark)] = {
            "replies": _num(slot.get("replies")),
            "others": _num(slot.get("others")),
            "covered": slot.get("covered") is True,
        }
    return out


def _views_numbers(views_at) -> dict:
    out = {}
    for mark, slot in (views_at or {}).items():
        slot = slot if isinstance(slot, dict) else {}
        out[str(mark)] = {
            "views": _num(slot.get("views")),
            "age_hours": _num(slot.get("age_hours")),
        }
    return out


# --------------------------------------------------------------------------
# 読む（`thth share log` は積んだ全部を出す）
# --------------------------------------------------------------------------

def log_rows() -> tuple:
    """`(行, 読めなかったファイル)`。**積んだ全部**（1 行残らず・古い順）。

    **壊れた行を「無い」ことにしない**（`thth/topics.py` の `ShelfBroken` と
    同じ作法）。読めなかった行は数えて返す。
    """
    d = outbox_dir()
    if not os.path.isdir(d):
        return [], []
    rows, broken = [], []
    for name in sorted(os.listdir(d)):
        if not name.endswith(".ndjson"):
            continue
        path = os.path.join(d, name)
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError as e:
            broken.append({"file": name, "detail": str(e)})
            continue
        for i, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except ValueError as e:
                broken.append({"file": name, "line": i, "detail": str(e)})
    return rows, broken


def bytes_on_disk() -> int:
    """outbox が実際に何バイトあるか。**off なら 0**（テストがここを見る）。"""
    d = outbox_dir()
    if not os.path.isdir(d):
        return 0
    total = 0
    for name in os.listdir(d):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            total += os.path.getsize(p)
    return total


def status() -> dict:
    """いま off か on か・仮名・積んだ件数。**読むだけ。**"""
    on = is_on()
    cfg = config()
    rows, broken = log_rows()
    counts: dict = {}
    for r in rows:
        key = r.get("schema") or "不明"
        counts[key] = counts.get(key, 0) + 1
    return {
        "enabled": on,
        "changed_at": cfg.get("changed_at"),
        # **off のうちは仮名を作らない**（作れば「まだ何もしていない」と言えなく
        # なる）。`observer_id()` を呼ばずにファイルの有無だけ見る。
        "observer": (observer_id() if on else None),
        "root": root(),
        "outbox": outbox_dir(),
        "queued": len(rows),
        "by_schema": counts,
        "bytes": bytes_on_disk(),
        "broken": broken,
        # **送る先はまだ無い**（設計 v2 §6 v2-5）。黙っていると「送った」と
        # 読まれるので、status に毎回書く。
        "destination": None,
        "destination_note": "泉のサーバはまだありません（v2-5）。積むだけで、送っていません",
    }


# --------------------------------------------------------------------------
# 台帳から積む（既存の経路の外側）
# --------------------------------------------------------------------------

_NOTE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


def _observations_from_shelf() -> tuple:
    """`state/topics.json` から観測の行を読む。**topics.py は変えない。**

    `(生きている行, 打ち消された note_id)`。読み方は `topics._split()` と同じ
    （打ち消しの行は `topic` を持たない）。
    """
    from . import topics as topics_mod
    checks = topics_mod.load().get("checks") or []
    rows = [r for r in checks if isinstance(r, dict)]
    taken = {r.get("retracts") for r in rows
             if isinstance(r.get("retracts"), str) and _NOTE_ID.match(r["retracts"])}
    live = [dict(r, note_id=topics_mod.note_id_of(r)) for r in rows
            if r.get("retracts") is None and r.get("topic")]
    return live, taken


def sync_lock_path() -> str:
    return os.path.join(root(), "sync.lock")


def _take_sync_lock():
    """`sync()` の鍵。**待つ**（`topics.record()` と同じ形）。

    見つかり方（監査 1・P2-4）: 鍵が無かったので、4 本同時に `thth share sync` を
    打つと **outbox が 4 倍**になった（484 行・`row_id` は 121 種）。`sync()` は
    「積み済みを読む → 無いものを積む」なので、**読みと書きの間に他人が入ると
    全員が「まだ積んでいない」と判断する**。重複を増やす口が、送る前の outbox に
    溜まる。

    断るのではなく**待つ**: `sync` は `thth share on` からも呼ばれるし、
    書き込みは一瞬。ただし**無限には待たない**（呼んだ側が止まって見える）。
    """
    from . import lock as lock_mod
    鍵 = lock_mod.AccountLock(sync_lock_path())
    限度 = 5.0
    待った = 0.0
    while True:
        try:
            鍵.acquire()
            return 鍵
        except lock_mod.LockBusy:
            if 待った >= 限度:
                # **黙って落とさない。** 積めなかったことを言う。
                raise ShareError(
                    f"ほかの実行が share を積んでいます（{限度} 秒待ちました）。"
                    f"少し待って `thth share sync` を打ち直してください")
            time.sleep(0.05)
            待った += 0.05


def sync(*, now=None, accounts=None) -> dict:
    """出来上がった台帳を読んで、まだ積んでいないものを積む。

    **ここが「積む口」で、`thth/collect.py` と `thth/topics.py` の外側にいる。**
    採るほうの経路には 1 行も足していないので、**share を丸ごと壊しても
    `thth throw` は通る**（`tests/test_share_safety.py`）。

    同じものを 2 度積まない（`row_id` で照合する）。観測は `note_id`、
    スレッドの形は post_id のハッシュが鍵。**その照合は鍵の中でやる**
    （`_take_sync_lock()` の但し書き・監査 1・P2-4）。
    """
    if not is_on():
        # **off なら鍵も取らない**（`state/share/` を作らない＝off で 0 バイト）。
        return _sync_off()
    鍵 = _take_sync_lock()
    try:
        return _sync_locked(now=now, accounts=accounts)
    finally:
        鍵.release()


def _sync_off() -> dict:
    return {"enabled": False, "added": 0, "observations": 0, "shapes": 0,
            "retractions": 0, "skipped": [],
            "note": "share は off です（`thth share on` で始まります）"}


def _sync_locked(*, now=None, accounts=None) -> dict:
    now = now if now is not None else jst.now_jst()
    既に, _broken = log_rows()
    積み済み = {r.get("row_id") for r in 既に}

    added = {"observations": 0, "shapes": 0, "retractions": 0}
    skipped = []

    # --- 観測 ---
    try:
        live, taken = _observations_from_shelf()
    except Exception as e:  # ShelfBroken 等。**share のために採集を止めない。**
        live, taken = [], set()
        skipped.append({"what": "観測", "why": str(e)})
    for r in live:
        row_id = r["note_id"]
        # **打ち消された観測は最初から積まない。** `taken` を数えておきながら
        # ここで使い忘れていた——打ち消した語がそのまま泉に出ていた
        # （`tests/test_share.py` が捕まえた・2026-09-13）。
        if row_id in taken:
            continue
        if row_id in 積み済み:
            continue
        enqueue_observation(topic=r.get("topic"), audience=r.get("audience"),
                             kind=r.get("kind"), status=r.get("status"),
                             observed_at=r.get("checked_at"), row_id=row_id, now=now)
        added["observations"] += 1
    # 積んだあとで打ち消された観測は、打ち消しを 1 行足す。
    for note_id in sorted(taken):
        if note_id in 積み済み and f"ret:{note_id}" not in 積み済み:
            enqueue_retraction(row_id=note_id, now=now)
            added["retractions"] += 1

    # --- スレッドの形 ---
    from . import threadshape as shape_mod
    names = accounts if accounts is not None else accounts_mod.list_account_names()
    for name in names:
        try:
            data = shape_mod.load(name)
        except Exception as e:
            skipped.append({"what": f"スレッドの形（{name}）", "why": str(e)})
            continue
        medium = data.get("medium")
        for post in data.get("posts") or []:
            raw_id = post.get("post_id")
            if not raw_id:
                continue
            if f"shape:{post_hash(raw_id)}" in 積み済み:
                # **刻みが増えていれば積み直す**のが本来だが、v2.0.0 では
                # 1 投稿 1 行に留める（送る先がまだ無い・重複を増やさない）。
                continue
            enqueue_thread_shape(post, medium=medium, now=now)
            added["shapes"] += 1

    return {"enabled": True, "added": sum(added.values()), **added,
            "skipped": skipped}
