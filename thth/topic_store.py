"""記事別トピック提案の保存（設計 §8・§9・§13 P1）。

**中身から決まる ID で、一度書いたら上書きしない。** 同じ入力を二度送っても
同じ ID になるので、重複しない（冪等）。判断・記事・観測・profile 履歴は
作成後に書き換えない。active profile だけは、履歴を残したうえで更新する。

**壊れた記録を「観測なし」と偽らない**（設計 §8・受け入れ T14）。読めなかった
ファイルは**壊れたものとして名前を返す**。空だったのか読めなかったのかを、
呼び出し側が区別できるようにする。

**このロックと、投稿の clone/account ロックを同時に持たない**（設計 §8）。
提案の保存が失敗しても、投稿のロックや inflight には触らない。
"""
from __future__ import annotations

import json
import os
import re

from . import accounts as accounts_mod
from . import lock as lock_mod
from . import topic_models as models

KINDS = ("articles", "observations", "decisions", "proposals")

# 種類ごとの ID の項目名。**読むたびに中身から計算しなおして照合する。**
ID_KEY = {"articles": "article_id", "observations": "observation_id",
          "decisions": "decision_id", "proposals": "proposal_id"}
_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.:@+-]{1,200}$")


class StoreError(Exception):
    """保存・読取ができない。**推測で続けない。**"""


class BadId(StoreError):
    """ID の形が違う。**棚の状態とは関係ない**（入力の誤り）。

    `StoreError` の一種にしてあるのは、既存の呼び出し側を壊さないため。
    CLI だけが区別して `invalid_id` を返す（kopicha セッション報告 2026-09-11:
    「`store_busy`（棚が使用中）ではなく入力の形の誤りです」）。
    """


def root() -> str:
    return os.path.join(accounts_mod.thth_root(), "state", "topic_advice")


def _kind_dir(kind: str) -> str:
    if kind not in KINDS:
        raise StoreError(f"知らない種類です: {kind!r}")
    return os.path.join(root(), kind)


def _id_filename(record_id: str) -> str:
    """ID をファイル名にする。**任意のパスを連結させない**（設計 §8）。"""
    if not _ID_RE.match(record_id or ""):
        # **形の誤りは「棚が使用中」ではない**（kopicha セッション報告
        # 2026-09-11）。コードで分岐する側が誤る。
        raise BadId(f"ID の形が違います: {record_id!r}"
                     f"（sha256: に続く 64 桁の 16 進数）")
    return record_id.replace("sha256:", "") + ".json"


def _account_filename(account: str) -> str:
    if not _SEGMENT_RE.match(account or ""):
        raise StoreError(f"account の形が違います: {account!r}")
    return account + ".json"


def _atomic_write(path: str, payload: dict) -> None:
    """固有の一時ファイル → flush/fsync → rename（設計 §8）。

    **同名固定の `.tmp` を使わない**——複数プロセスが同時に書くと壊れる。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _store_lock():
    os.makedirs(root(), exist_ok=True)
    return lock_mod.AccountLock(os.path.join(root(), ".store.lock"))


def put(kind: str, record: dict, *, id_key: str) -> tuple:
    """記録を 1 つ保存する。`(記録, 新しく書いたか)`。

    **同じ ID が既にあれば書かない**（冪等・受け入れ T13）。中身から ID が決まる
    ので、同じ入力の再送は同じファイルになる。
    """
    record_id = record.get(id_key)
    path = os.path.join(_kind_dir(kind), _id_filename(record_id))
    lock = _store_lock()
    try:
        lock.acquire()
    except lock_mod.LockBusy as e:
        raise StoreError("ほかの実行が保存中です。少し待ってからやり直してください") from e
    try:
        problem = verify(kind, record, filename_id=record_id)
        if problem:
            raise StoreError(f"保存しようとした記録が壊れています: {problem}")
        if os.path.exists(path):
            stored = read_json(path)
            # **同じ ID なら中身も同じはず。** 違えば、どちらかが書き換わって
            # いる（独立レビュー 2026-09-11・指摘 5）。黙って古いほうを返さない。
            if models.canonical_json(stored) != models.canonical_json(record):
                raise StoreError(
                    f"同じ ID で中身の違う記録が保存されています（{record_id}）。"
                    f"保存済みのファイルが書き換わっている可能性があります")
            return stored, False
        _atomic_write(path, record)
        return record, True
    finally:
        lock.release()


def read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def verify_profile(profile: dict, *, account: str | None = None) -> str | None:
    """profile が自分の `profile_version` と合っているか（独立レビュー第 2 巡 P1-1）。

    **profile を例外にしない。** 観測・記事・候補比較・判断には読むたびの照合を
    入れたのに、**profile だけ `read_json` のままだった。** そのため
    `profile_version` を据え置いて `status` を `confirmed` に、`basis` を空に
    書き換えると、**未確定の方針に基づく判断が確定扱いで保存できた。**
    """
    claimed = profile.get("profile_version")
    if not isinstance(claimed, str) or not _ID_RE.match(claimed):
        return "profile_version がありません（または形が違います）"
    if account is not None and profile.get("account") != account:
        return (f"profile の account が違います"
                f"（{profile.get('account')!r} / {account!r}）")
    try:
        rebuilt = models.build_profile(
            {k: v for k, v in profile.items()
             if k not in ("profile_version", "schema_version")})
    except models.SchemaError as e:
        return f"profile が schema に合いません: {e}"
    if rebuilt["profile_version"] != claimed:
        return (f"中身が profile_version と合いません（保存後に書き換わって"
                f"います。{claimed[:19]}… のはずが "
                f"{rebuilt['profile_version'][:19]}…）")
    return None


def verify(kind: str, record: dict, *, filename_id: str | None = None) -> str | None:
    """記録が自分の ID と合っているか。合っていれば None、違えば理由。

    **「ID が変わっていない＝証拠が変わっていない」は、照合して初めて言える**
    （独立レビュー 2026-09-11・指摘 5）。以前は JSON として読めるかしか見て
    いなかったので、**ファイルを手で書き換えても同じ ID のまま通った**——
    `status: partial・投稿例 0 件` の観測を `ok・3 件` に差し替えて、
    元の判断を `recommended` として保存できた。

    内容アドレスは、**読むたびに計算しなおさなければ内容アドレスではない。**
    """
    id_key = ID_KEY.get(kind)
    if id_key is None:
        return None
    claimed = record.get(id_key)
    if not isinstance(claimed, str) or not _ID_RE.match(claimed):
        return f"{id_key} がありません（または形が違います）"
    if filename_id is not None and filename_id != claimed:
        return f"ファイル名と {id_key} が違います（{filename_id} / {claimed}）"
    actual = models.content_id(record, exclude=(id_key,))
    if actual != claimed:
        return (f"中身が {id_key} と合いません（保存後に書き換わっています。"
                 f"{claimed[:19]}… のはずが {actual[:19]}…）")
    return None


def get(kind: str, record_id: str) -> dict | None:
    path = os.path.join(_kind_dir(kind), _id_filename(record_id))
    if not os.path.exists(path):
        return None
    row = read_json(path)
    problem = verify(kind, row, filename_id=record_id)
    if problem:
        raise StoreError(f"{kind}/{record_id}: {problem}")
    return row


def _usable_observation(row: dict) -> bool:
    """観測として使える最低限があるか。**検査を足す前の記録の掃除用。**

    **`normalized_topic` しか無い記録も救う**（2026-09-11）。検査を足す前に
    asmon 関東セッションが残した `中学受験` の観測が、`topic` を持たず
    `normalized_topic` だけ持っていた。**ログイン状態のブラウザで実際に見てきた、
    唯一のトピック観測**だったのに、こちらの掃除で丸ごと捨てていた。

    照合には正規化語を使うので、**これで緩むものは無い。**
    「使えない」と「形が古い」を混同しない。
    """
    return bool(row.get("topic") or row.get("normalized_topic")) \
        and bool(row.get("retrieved_at")) \
        and row.get("search_mode") in models.SEARCH_MODE


def retraction_dir() -> str:
    return os.path.join(root(), "retractions")


def retract(observation_id: str, *, reason: str, by: str, now=None) -> dict:
    """観測を**取り下げる。消さない**（asmon 関東セッション提案 2026-09-11）。

    > 観測は内容アドレスで、**消すと参照していた候補比較が壊れます。**
    > 本当に要るのは削除ではなく「取り下げ」かもしれません。

    そのとおり。**間違って入れた観測は運用が続けば定期的に出る**が、そのたびに
    本番の state を手で消すのは重いし危ない。取り下げなら:

    - 記録そのものは残る（**過去の判断が参照していた事実は消えない**）
    - 新しい候補比較からは参照できない
    - 誰がいつなぜ取り下げたかが残る

    **取り消しの取り消しはできる**（`unretract`）。記録を消さないので。
    """
    from . import jst
    if not _ID_RE.match(observation_id or ""):
        raise BadId(f"ID の形が違います: {observation_id!r}")
    if not (reason or "").strip():
        raise StoreError("--reason を付けてください（なぜ取り下げるか）")
    if not (by or "").strip():
        raise StoreError("--by を付けてください（誰が取り下げたか）")
    if get("observations", observation_id) is None:
        raise StoreError(f"その観測は保存されていません: {observation_id}")

    now = now if now is not None else jst.now_jst()
    row = {"observation_id": observation_id, "reason": reason,
           "retracted_by": by, "retracted_at": now.isoformat(),
           "schema_version": models.SCHEMA_VERSION}
    os.makedirs(retraction_dir(), exist_ok=True)
    _atomic_write(os.path.join(retraction_dir(), _id_filename(observation_id)), row)
    return row


def unretract(observation_id: str) -> bool:
    """取り下げを取り消す。**記録を消していないので戻せる。**"""
    path = os.path.join(retraction_dir(), _id_filename(observation_id))
    if not os.path.exists(path):
        return False
    os.remove(path)
    return True


def retractions() -> dict:
    """`{observation_id: 取り下げの記録}`。"""
    out = {}
    directory = retraction_dir()
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name.endswith(".tmp"):
            continue
        try:
            row = read_json(os.path.join(directory, name))
        except (OSError, ValueError):
            continue
        if row.get("observation_id") == "sha256:" + name[:-len(".json")]:
            out[row["observation_id"]] = row
    return out


def load_all(kind: str) -> tuple:
    """`(読めた記録, 使えなかった ID, 取り下げられた記録)`（設計 §8・受け入れ T14）。

    **壊れたファイルを「無い」ことにしない。** 読めなかったものは ID を返す。
    呼び出し側は「観測が無い」と「観測が読めない」を区別できる。
    """
    directory = _kind_dir(kind)
    rows, broken = [], []
    withdrawn = retractions() if kind == "observations" else {}
    taken = []
    if not os.path.isdir(directory):
        return rows, broken, taken
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json") or name.endswith(".tmp"):
            continue
        path = os.path.join(directory, name)
        record_id = "sha256:" + name[:-len(".json")]
        try:
            row = read_json(path)
        except (OSError, ValueError):
            broken.append(record_id)
            continue
        # **JSON として読めることは、壊れていないことではない**（指摘 5）。
        # 手で書き換えられた記録は「読めた」側に入れない。
        if verify(kind, row, filename_id=record_id):
            broken.append(record_id)
            continue
        # **使えない記録も「読めた」側に入れない**（両セッション報告
        # 2026-09-11）。検査を足す前に保存された中身の無い観測が、
        # `topic: null` として一覧に並んでいた。**消さずに、使わない。**
        if kind == "observations" and not _usable_observation(row):
            broken.append(record_id)
            continue
        # **取り下げは「壊れている」ではない。** 誰かが理由をつけて下げた、
        # という別の事実（asmon 関東セッション提案 2026-09-11）。混ぜない。
        if record_id in withdrawn:
            taken.append(dict(withdrawn[record_id], topic=row.get("topic")))
            continue
        rows.append(row)
    return rows, broken, taken


# --- profile（active は履歴を残して更新する・設計 §8・§10） ------------------

def profile_path(account: str) -> str:
    return os.path.join(root(), "profiles", _account_filename(account))


def get_profile(account: str) -> dict | None:
    """active profile。**読むたびに中身と `profile_version` を照合する。**"""
    path = profile_path(account)
    if not os.path.exists(path):
        return None
    profile = read_json(path)
    problem = verify_profile(profile, account=account)
    if problem:
        raise StoreError(f"profiles/{account}: {problem}")
    return profile


def set_profile(profile: dict) -> dict:
    """active profile を更新する。**旧版は履歴に残す**（設計 §8）。"""
    account = profile["account"]
    lock = _store_lock()
    try:
        lock.acquire()
    except lock_mod.LockBusy as e:
        raise StoreError("ほかの実行が保存中です。少し待ってからやり直してください") from e
    try:
        old = get_profile(account)
        if old:
            _atomic_write(os.path.join(root(), "profile_history",
                                        _id_filename(old["profile_version"])), old)
        _atomic_write(profile_path(account), profile)
        return profile
    finally:
        lock.release()


# --- 旧記録の読み取り（設計 §9） --------------------------------------------

def legacy_observations() -> list:
    """既存 `state/topics.json` を**参考証拠として読む**（設計 §9）。

    **元データは変更しない。読むだけ。** 当時の判断は
    `provenance: legacy` として残し、**成功の実証としては扱わない**
    （旧記録だけで `recommended` に昇格しない）。

    **`by` から account を推測して埋めない**（masaru 指示 2026-09-11）。
    """
    from . import topics as topics_mod

    # **語ごとに最後の 1 件だけ**（実運用報告 2026-09-11）。履歴を全部返すと
    # 同じ語が何度も並んで、原稿に関係のある観測が埋もれる。履歴そのものは
    # `topics.json` に残っている。
    latest = {}
    for row in topics_mod.load()["checks"]:
        latest[row["topic"]] = row

    out = []
    for row in latest.values():
        record = {
            "topic": row["topic"],
            "normalized_topic": row["topic"],
            "provider": "legacy_note",
            "provenance": "legacy",
            "retrieved_at": row.get("checked_at"),
            "submitted_by": row.get("by"),
            "status": row.get("status"),          # 無ければ None のまま（推測しない）
            "audience": row.get("audience") or None,
            "kind": row.get("kind"),
            "account": row.get("account"),        # 46 件はすべて None
            "legacy_verdict": row.get("verdict"),
            "samples": [],                        # 旧記録は投稿例を持たない
            "coverage": None,
            "search_mode": "manual_unknown",       # 当時の引き方は記録が無い
        }
        # **参照できる ID を与える**（asmon 関東セッション報告 2026-09-11）。
        # ID が無いと `observation_refs` に書けず、**「観測が足りない」と言われても
        # 満たす手段が存在しなかった。** 中身から決まるので、同じ記録なら
        # 何度読んでも同じ ID になる。元データは変更しない。
        record["observation_id"] = models.content_id(
            record, exclude=("observation_id",))
        out.append(record)
    return out
