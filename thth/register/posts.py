"""台帳の記録と、もう出した投稿をつなぐ（段 12 の 2・3・読むだけ）。

正本の案: `docs/案_段12_台帳とSNSの反応をつなぐ_2026-11-16.md`。

- 投稿を出すとき、原稿の頭の `seeds:` を配信の台帳（`state/<口座>/sent/<post_id>.json`）の `seeds` に控える
  （段 12 の 1・`thth/sent.py` の `seeds_field`・承認の指紋には入れない）。
- `posts_for(root, refs)`: 直した記録（と影響の候補）を `seeds` で指す「出した投稿」（媒体・口座・post_id・出した日。
  本文は入れない）。`verify`・`fix`・`health` の影響の候補（`route.impact`）に `posts` として足す。
- `rechecks(root)`: 返信に事実の指摘の印（`thth reply-mark --fact-challenge`・`thth/reply_marks.py`）が付いた投稿が
  `seeds` で指す記録を「確かめ直しの候補」に（記録・投稿・印の日・印を付けた者）。返信の本文は読まない。

どの口座の配信の台帳を見るかは、台帳の控え（`registers/<名前>.json`）の `project` から引き、その project の口座を
全部見る。読めない口座・読めない配信の台帳は `cannot_say` に名前を出し、0 と混ぜない（数は読めた物だけ）。

台帳の機械は網の部品を import しない柵（`tests/test_register_base.py`）の内にいるので、配信の台帳と返信の印は
`thth.sent`・`thth.reply_marks` を通さず、ここで JSON を字として読む（置き場の形は `sent.dir_for`・
`reply_marks.MARKS_FILE` と同じ。形がずれたら `tests/test_register_stage12_posts.py` の通しの試験が赤になる）。

**赤にしない・rc を変えない**: ここで何が起きても、呼んだ側の判定（verify の赤・配信の止まり・rc）は変えない。
読めない物は `cannot_say` に言うだけ。
"""
from __future__ import annotations

import json
import os

from .. import accounts
from . import ledger as L

SHOWN = 200          # 並べる数（数は全部）
SENT_DIR = "sent"                                # `thth/sent.py` の `dir_for`
MARKS_PATH = ("reply_marks", "marks.json")       # `thth/reply_marks.py` の置き場
FACT_CHALLENGE = "fact_challenge"                # `reply_marks.FACT_CHALLENGE`
FILE_MAX = 4 * 1024 * 1024


def _same(a, b) -> bool:
    return isinstance(a, str) and isinstance(b, str) and os.path.realpath(a) == os.path.realpath(b)


def project_of(root: str) -> tuple:
    """`(project, 台帳の名前, わけ)`。控えの道か台帳の中の名乗りで台帳を引く。引けなければ project は None。"""
    try:
        declared = L.declared_name(root)
    except Exception:  # noqa: BLE001 — 名乗りが崩れていても、ここは言うだけ
        declared = None
    for name in L.list_names():
        try:
            cfg = L.load(name)
        except Exception:  # noqa: BLE001
            continue
        if name == declared or _same(cfg.get("path"), root) or _same(L.raw_path(name), root):
            if L.is_common(cfg):
                return None, name, "common_register"
            return cfg["project"], name, None
    return None, None, "register_not_registered"


def _accounts_of(project: str) -> tuple:
    """`([(口座, 媒体)], [読めない口座の {"account", "why"}])`。"""
    cannot = []
    try:
        names = accounts.list_account_names()
    except Exception:  # noqa: BLE001 — 置き場が読めない（0 本ではない）
        return [], [{"account": None, "why": "accounts_unreadable"}]
    mine = []
    for name in names:
        try:
            cfg = accounts.load_account(name)
        except Exception as exc:  # noqa: BLE001
            cannot.append({"account": name, "why": "account_unreadable:" + type(exc).__name__})
            continue
        if cfg.get("project") == project:
            mine.append((name, cfg.get("media")))
    return mine, cannot


def _sent_rows(account: str) -> tuple:
    """`(記録の並び, わけ, 読めなかった本数)`。配信の台帳が無ければ空（0 本）。置き場が読めなければ記録は None。"""
    folder = os.path.join(accounts.state_dir_for(account), SENT_DIR)
    if not os.path.lexists(folder):
        return [], None, 0
    if os.path.islink(folder):
        return None, "sent_unreadable", 0
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return None, "sent_unreadable", 0
    rows, broken = [], 0
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(folder, name), "rb") as f:
                raw = f.read(FILE_MAX + 1)
            row = json.loads(raw.decode("utf-8")) if len(raw) <= FILE_MAX else None
        except (OSError, UnicodeDecodeError, ValueError):
            row = None
        # 名前から post_id を作らない（`sent.records` と同じ）。中身に post_id の無い物は壊れた記録。
        if not isinstance(row, dict) or not isinstance(row.get("post_id"), str) or not row["post_id"].strip():
            broken += 1
            continue
        rows.append(row)
    return rows, ("sent_rows_unreadable" if broken else None), broken


def _facts(account: str) -> tuple:
    """事実の指摘の印 `({reply_id: 印}, 読めたか)`（`reply_marks.load_facts` と同じ読み）。"""
    path = os.path.join(accounts.state_dir_for(account), *MARKS_PATH)
    if not os.path.lexists(path):
        return {}, True
    try:
        if os.path.islink(path) or os.path.islink(os.path.dirname(path)):
            return {}, False
        with open(path, "rb") as f:
            raw = f.read(FILE_MAX + 1)
        value = json.loads(raw.decode("utf-8")) if len(raw) <= FILE_MAX else None
    except (OSError, UnicodeDecodeError, ValueError):
        return {}, False
    facts = value.get("facts", {}) if isinstance(value, dict) else None
    if not isinstance(facts, dict):
        return {}, False
    out = {rid: row for rid, row in facts.items()
           if isinstance(rid, str) and isinstance(row, dict) and row.get("mark") == FACT_CHALLENGE}
    return out, len(out) == len(facts)


class _Index:
    """1 回の命令の間だけ、project の口座の配信の台帳を読んでおく。"""

    def __init__(self, root: str):
        self.project, self.register, why = project_of(root)
        self.accounts = []        # 読めた口座の名前
        self.cannot = [] if why is None else [{"account": None, "why": why}]
        self.rows = []            # (口座, 媒体, 記録)
        if self.project is None:
            return
        names, cannot = _accounts_of(self.project)
        self.cannot += cannot
        for name, media in names:
            rows, why, broken = _sent_rows(name)
            if rows is None:
                self.cannot.append({"account": name, "why": why})
                continue
            if why is not None:
                self.cannot.append({"account": name, "why": why, "rows": broken})
            self.accounts.append(name)
            for row in rows:
                self.rows.append((name, media, row))

    def head(self) -> dict:
        return {"project": self.project, "register": self.register, "accounts": list(self.accounts),
                "cannot_say": list(self.cannot)}


def _seeds_of(row) -> list:
    value = row.get("seeds")
    return [s for s in value if isinstance(s, str)] if isinstance(value, list) else []


def _post(name, media, row) -> dict:
    out = {"media": media, "account": name, "post_id": row.get("post_id"), "sent_at": row.get("sent_at")}
    if row.get("kind"):
        out["kind"] = row["kind"]
    if row.get("url"):
        out["url"] = row["url"]
    return out


def posts_for(root: str, refs, *, index: _Index | None = None) -> dict:
    """`refs`（`種類:番号`・共通は `common:種類:番号`）を `seeds` で指す出した投稿（取り下げた物は除く）。"""
    refs = set(refs or ())
    try:
        index = index or _Index(root)
    except Exception as exc:  # noqa: BLE001 — 読むだけの欄で判定を変えない
        return {"project": None, "register": None, "accounts": [], "count": None, "items": [],
                "cannot_say": [{"account": None, "why": "posts_unreadable:" + type(exc).__name__}]}
    out = index.head()
    if index.project is None:
        out.update(count=None, items=[])
        return out
    items = []
    for name, media, row in index.rows:
        if row.get("retracted_at"):
            continue
        hit = [s for s in _seeds_of(row) if s in refs]
        if hit:
            items.append({**_post(name, media, row), "seeds": hit})
    items.sort(key=lambda x: (str(x.get("sent_at") or ""), x["account"], str(x["post_id"])))
    out.update(count=len(items), items=items[:SHOWN])
    return out


def rechecks(root: str, vessel=None, *, index: _Index | None = None) -> dict:
    """事実の指摘の印が付いた返信の投稿が `seeds` で指す記録（確かめ直しの候補）。赤にしない。"""
    try:
        index = index or _Index(root)
    except Exception as exc:  # noqa: BLE001
        return {"project": None, "register": None, "accounts": [], "count": None, "items": [],
                "marks": None, "unseeded": None, "post_unknown": None,
                "cannot_say": [{"account": None, "why": "posts_unreadable:" + type(exc).__name__}]}
    out = index.head()
    if index.project is None:
        out.update(count=None, items=[], marks=None, unseeded=None, post_unknown=None)
        return out
    by_post = {}
    for name, media, row in index.rows:
        by_post.setdefault((name, row.get("post_id")), (media, row))
    items, marks, unseeded, unknown = [], 0, 0, 0
    for name in index.accounts:
        try:
            facts, ok = _facts(name)
        except Exception:  # noqa: BLE001
            facts, ok = {}, False
        if not ok:
            out["cannot_say"].append({"account": name, "why": "reply_marks_unreadable"})
        for reply_id, mark in sorted(facts.items()):
            marks += 1
            post_id = mark.get("post_id")
            found = by_post.get((name, post_id)) if post_id else None
            if found is None:
                unknown += 1
                continue
            media, row = found
            seeds = _seeds_of(row)
            if not seeds:
                unseeded += 1
                continue
            for ref in seeds:
                item = {"record": ref, **_post(name, media, row), "reply_id": reply_id,
                        "marked_at": mark.get("at"), "marked_by": mark.get("by")}
                if vessel is not None and not ref.startswith("common:"):
                    try:
                        item["in_register"] = vessel.unique(ref) is not None
                    except Exception:  # noqa: BLE001
                        pass
                items.append(item)
    items.sort(key=lambda x: (x["record"], str(x.get("marked_at") or ""), x["reply_id"]))
    out.update(count=len(items), items=items[:SHOWN], marks=marks, unseeded=unseeded, post_unknown=unknown)
    return out


def attach(found: dict, root: str, vessel=None) -> dict:
    """影響の候補（`route.impact` の値）に `posts`（直した記録と候補を指す出した投稿）と `recheck`（確かめ直しの候補）
    を足す。何が起きても `found` の数・候補は変えない（赤にしない）。"""
    try:
        index = _Index(root)
    except Exception as exc:  # noqa: BLE001
        index = None
        why = "posts_unreadable:" + type(exc).__name__
    refs = set(found.get("changed") or ()) | {c["record"] for c in found.get("candidates") or ()}
    if index is None:
        empty = {"project": None, "register": None, "accounts": [], "count": None, "items": [],
                 "cannot_say": [{"account": None, "why": why}]}
        found["posts"] = dict(empty)
        found["recheck"] = dict(empty)
        return found
    if refs:
        found["posts"] = posts_for(root, refs, index=index)
    else:
        found["posts"] = {**index.head(), "count": 0 if index.project is not None else None, "items": []}
    found["recheck"] = rechecks(root, vessel, index=index)
    return found
