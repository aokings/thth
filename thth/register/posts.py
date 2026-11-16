"""台帳の記録と、もう出した投稿をつなぐ（段 12 の 2・3・読むだけ）。

正本の案: `docs/案_段12_台帳とSNSの反応をつなぐ_2026-11-16.md`。

- 投稿を出すとき、原稿の頭の `seeds:` を配信の台帳（`state/<口座>/sent/<post_id>.json`）の `seeds` に控える
  （段 12 の 1・`thth/sent.py` の `seeds_field`・承認の指紋には入れない）。
- `posts_for(root, refs)`: 直した記録（と影響の候補）を `seeds` で指す「出した投稿」（媒体・口座・post_id・出した日。
  本文は入れない）。`verify`・`fix`・`health` の影響の候補（`route.impact`）に `posts` として足す。
- `rechecks(root)`: 返信に事実の指摘の印（`thth reply-mark --fact-challenge`・`thth/reply_marks.py`）が付いた投稿が
  `seeds` で指す記録を「確かめ直しの候補」に（記録・投稿・印の日・印を付けた者）。返信の本文は読まない。

連投（threadthrow）は `sent/` を書かないので、段の実行記録（`state/threads/<run_id>.json`）の公開の行（段 12 の残りで
`seeds` を控える）も同じ形にして読む（読むだけ・`run_id` と `step` を足す）。配信の台帳は要るとき（直した記録がある・
事実の指摘の印がある口座）だけ読む（監査 5.24.0 の軽 3）。`seeds_by_post(口座)` はシナリオの種の記録の列が使う。

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
import re
import stat

from .. import accounts
from . import ledger as L

SHOWN = 200          # 並べる数（数は全部）
SENT_DIR = "sent"                                # `thth/sent.py` の `dir_for`
MARKS_PATH = ("reply_marks", "marks.json")       # `thth/reply_marks.py` の置き場
FACT_CHALLENGE = "fact_challenge"                # `reply_marks.FACT_CHALLENGE`
FILE_MAX = 4 * 1024 * 1024
# 返信の id・投稿の id（`reply_marks._ID` と同じ形・媒体の数字と英数と `_-.:`）。手で直した印の置き場の id を
# そのまま端末に出さない（制御文字・長い字の列）。投稿の id は Bluesky の AT URI の `/` も通す。
_REPLY_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_TEXT_MAX = 80


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


def _read_json(path):
    """ふつうのファイルだけを JSON として読む（検収 段 12 の残りの軽 2）。symlink は辿らない（`O_NOFOLLOW`）・FIFO などで
    止まらない（`O_NONBLOCK` で開いて `S_ISREG` を見る）・4 MiB を超える物は読まない。読めなければ None。"""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb") as f:
            fd = None
            raw = f.read(FILE_MAX + 1)
        return json.loads(raw.decode("utf-8")) if len(raw) <= FILE_MAX else None
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    finally:
        if fd is not None:
            os.close(fd)


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
        row = _read_json(os.path.join(folder, name))
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
    out = {}
    for rid, row in facts.items():
        # 使えない id・文字列でない post_id の行は落として「読めなかった」に数える（手で直した・壊れた置き場）。
        if (not isinstance(rid, str) or not _REPLY_ID.fullmatch(rid) or not isinstance(row, dict)
                or row.get("mark") != FACT_CHALLENGE):
            continue
        post_id = row.get("post_id")
        if post_id is not None and (not isinstance(post_id, str) or not post_id or len(post_id) > 512
                                    or any(ord(c) < 0x20 or ord(c) == 0x7f for c in post_id)):
            continue
        out[rid] = {"post_id": post_id, "at": _text(row.get("at")), "by": _text(row.get("by"))}
    return out, len(out) == len(facts)


def _text(value):
    """印の欄（日時・誰が）を出してよい形に（文字列だけ・制御文字を除く・80 字まで）。"""
    if not isinstance(value, str):
        return None
    return "".join(c for c in value if ord(c) >= 0x20 and ord(c) != 0x7f)[:_TEXT_MAX] or None


RUNS_PATH = ("state", "threads")                  # `thth/threadrun.py` の `runs_dir`（連投の段の実行記録）
RUN_PUBLISHED = "published"                      # `threadrun.PUBLISHED`


_RUN_ID = re.compile(r"run-[0-9]{8}T[0-9]{6}-[0-9a-f]{8}")   # `threadrun._RUN_ID_RE`
_STAMP_MAX = 64


def _stamp(value):
    """実行記録の時刻の欄を出してよい形に（文字列・制御文字なし・64 字まで。崩れていれば None）。"""
    if not isinstance(value, str) or len(value) > _STAMP_MAX or any(ord(c) < 0x20 or ord(c) == 0x7f for c in value):
        return None
    return value


def _runs_rows(names) -> tuple:
    """連投の段の実行記録（`state/threads/<run_id>.json`）から、口座ごとの公開済みの段 `({口座: [記録]}, 読めない物)`。

    連投は `sent/` を書かないので、段の公開の行（`post_id`・`posted_at`・`seeds`）を配信の台帳の行の形にして返す
    （`sent_at` は `posted_at`・`run_id` と `step` を足す。形の崩れた欄は None）。読むだけ。置き場が無ければ空（0 本）。

    読めない物（検収 段 12 の残りの軽 1・5）: 口座が `names` の物で `posts` が崩れていれば、その口座の
    `runs_unreadable`。ほかの project の口座の物は数えない。読めない・ふつうのファイルでない・口座が文字列でない記録は
    どの口座の物か言えないので、`account: None` の `runs_unreadable` に数える（その行だけ落とし、ほかは読む）。
    同じ口座・post_id・run_id・段の行は 1 本だけ（写しの記録で二重に数えない・軽 3）。"""
    folder = os.path.join(accounts.thth_root(), *RUNS_PATH)
    out = {name: [] for name in names}
    if not os.path.lexists(folder):
        return out, []
    if os.path.islink(folder) or not os.path.isdir(folder):
        return out, [{"account": None, "why": "runs_unreadable"}]
    try:
        files = sorted(os.listdir(folder))
    except OSError:
        return out, [{"account": None, "why": "runs_unreadable"}]
    unknown, broken, seen = 0, {}, set()
    for file in files:
        if not file.endswith(".json"):
            continue
        run = _read_json(os.path.join(folder, file))
        account = run.get("account") if isinstance(run, dict) else None
        if not isinstance(account, str):
            unknown += 1
            continue
        if account not in out:
            continue
        if not isinstance(run.get("posts"), list):
            broken[account] = broken.get(account, 0) + 1
            continue
        run_id = run.get("run_id") if isinstance(run.get("run_id"), str) and _RUN_ID.fullmatch(run["run_id"]) else None
        for step in run["posts"]:
            if not isinstance(step, dict) or step.get("state") != RUN_PUBLISHED:
                continue
            post_id = step.get("post_id")
            index = step.get("index")
            key = (account, post_id, run_id, repr(index))
            if not isinstance(post_id, str) or not post_id.strip() or key in seen:
                continue
            seen.add(key)
            row = {"post_id": post_id, "sent_at": _stamp(step.get("posted_at")), "run_id": run_id,
                   "step": index if isinstance(index, int) and not isinstance(index, bool) else None,
                   "from_run": True}
            if isinstance(step.get("seeds"), list):
                row["seeds"] = step["seeds"]
            out[account].append(row)
    problems = [{"account": name, "why": "runs_unreadable", "rows": broken[name]} for name in sorted(broken)]
    if unknown:
        problems.append({"account": None, "why": "runs_unreadable", "rows": unknown})
    return out, problems


class _Index:
    """1 回の命令の間だけ、project の口座の配信の台帳（と連投の段の実行記録）を読んでおく。

    **要るときだけ読む**（監査 5.24.0 の軽 3）: 口座の並びだけを先に引き、配信の台帳は `rows(口座)` で初めて読む。
    直した記録も事実の指摘の印も無いふつうの verify では `sent/` を開かない（壊れた行のある口座を毎回「言えない」に
    出さない）。読んだ口座の読めない物だけを、その欄の `cannot_say` に出す。"""

    def __init__(self, root: str | None = None, *, names=None):
        self.cannot = []          # 口座を引く前の読めない物（台帳の控え・口座の置き場）
        self.names = []           # (口座, 媒体)
        self._rows = {}           # 口座 → [記録]（読めなければ None）
        self._problems = {}       # 口座 → [読めない物]
        self._runs = None
        self._runs_problems = []
        if names is not None:
            self.project, self.register = None, None
            self.names, self.cannot = _named_accounts(names)
            return
        self.project, self.register, why = project_of(root)
        if why is not None:
            self.cannot.append({"account": None, "why": why})
        if self.project is None:
            return
        self.names, cannot = _accounts_of(self.project)
        self.cannot += cannot

    @property
    def accounts(self) -> list:
        """配信の台帳を読めた口座（5.24.0 と同じ意味・壊れた行があっても置き場が読めれば入る）。読んでいない口座は
        `not_read`（検収 段 12 の残りの軽 6）。"""
        return [name for name, _media in self.names if self._rows.get(name) is not None]

    @property
    def not_read(self) -> list:
        """この命令で配信の台帳を読まなかった口座（要るときだけ読む・監査 5.24.0 の軽 3）。"""
        return [name for name, _media in self.names if name not in self._rows]

    @property
    def usable(self) -> list:
        """読めた口座と、まだ読んでいない口座（印を読む・これから読む先）。"""
        return [name for name, _media in self.names if self._rows.get(name, ()) is not None]

    def _load(self, name) -> None:
        if name in self._rows:
            return
        if self._runs is None:
            self._runs, self._runs_problems = _runs_rows([n for n, _m in self.names])
        rows, why, broken = _sent_rows(name)
        problems = []
        if rows is None:
            problems.append({"account": name, "why": why})
        elif why is not None:
            problems.append({"account": name, "why": why, "rows": broken})
        if rows is not None:
            known = {row["post_id"] for row in rows}
            rows = rows + [row for row in self._runs.get(name, ()) if row["post_id"] not in known]
        self._rows[name] = rows
        self._problems[name] = problems

    def rows(self, names=None) -> list:
        """`[(口座, 媒体, 記録)]`。`names` を渡せばその口座だけ読む。"""
        wanted = None if names is None else set(names)
        out = []
        for name, media in self.names:
            if wanted is not None and name not in wanted:
                continue
            self._load(name)
            for row in self._rows[name] or ():
                out.append((name, media, row))
        return out

    def problems(self, names) -> list:
        """読んだ口座（`names`）の読めない物。連投の段の実行記録を読んだなら、その読めない物も。"""
        out = []
        for name in names:
            out += self._problems.get(name, [])
        if any(name in self._rows for name in names):
            # 連投の段の実行記録: 読んだ口座の物と、どの口座の物か言えない物だけ（ほかの project の物は数えない）。
            out += [p for p in self._runs_problems if p["account"] is None or p["account"] in names]
        return out

    def head(self, read=()) -> dict:
        return {"project": self.project, "register": self.register, "accounts": self.accounts,
                "accounts_not_read": self.not_read, "cannot_say": list(self.cannot) + self.problems(read)}


def _named_accounts(names) -> tuple:
    """口座の名前の並びを `([(口座, 媒体)], 読めない口座)` に（シナリオの種から呼ぶとき）。"""
    out, cannot = [], []
    for name in sorted(set(names)):
        try:
            cfg = accounts.load_account(name)
        except Exception as exc:  # noqa: BLE001
            cannot.append({"account": name, "why": "account_unreadable:" + type(exc).__name__})
            continue
        out.append((name, cfg.get("media")))
    return out, cannot


def seeds_by_post(names) -> dict:
    """口座（`names`）の出した投稿ごとの `seeds`（シナリオの種の「反応の多かった台帳の記録」の列・案の 4・読むだけ）。

    `{"posts": {(口座, post_id): {"seeds": [...], "retracted": bool}}, "unreadable": [口座], "cannot_say": [...]}`。
    配信の台帳（`sent/`）と連投の段の実行記録の両方から引く。本文は返さない。`unreadable` は配信の台帳が読めない・
    壊れた行のある口座（その口座で行の引けない投稿は「控えが無い」と言えない）。"""
    index = _Index(names=names)
    posts = {}
    read = [name for name, _media in index.names]
    for name, _media, row in index.rows(read):
        posts.setdefault((name, row["post_id"]), {"seeds": _seeds_of(row), "retracted": bool(row.get("retracted_at"))})
    problems = index.problems(read)
    # 読めない口座（設定・配信の台帳・その口座の実行記録）。どの口座の物か言えない実行記録の壊れは `cannot_say` に
    # 出すだけで、全口座を「言えない」に広げない（検収 段 12 の残りの軽 4・5）。
    unreadable = sorted({p["account"] for p in list(index.cannot) + problems if p.get("account")})
    return {"posts": posts, "unreadable": unreadable, "cannot_say": list(index.cannot) + problems}


def _seeds_of(row) -> list:
    value = row.get("seeds")
    return [s for s in value if isinstance(s, str)] if isinstance(value, list) else []


def _post(name, media, row) -> dict:
    out = {"media": media, "account": name, "post_id": row.get("post_id"), "sent_at": row.get("sent_at")}
    if row.get("kind"):
        out["kind"] = row["kind"]
    if row.get("url"):
        out["url"] = row["url"]
    if row.get("from_run"):
        # 連投の段（`sent/` でなく段の実行記録から引いた投稿）。
        out["run_id"] = row["run_id"]
        out["step"] = row.get("step")
    return out


def posts_for(root: str, refs, *, index: _Index | None = None) -> dict:
    """`refs`（`種類:番号`・共通は `common:種類:番号`）を `seeds` で指す出した投稿（取り下げた物は除く）。"""
    refs = set(refs or ())
    try:
        return _posts_for(root, refs, index)
    except Exception as exc:  # noqa: BLE001 — 読むだけの欄で判定を変えない（recheck とは別に言えないに送る）
        return _unreadable(exc)


def _posts_for(root, refs, index) -> dict:
    index = index or _Index(root)
    if index.project is None:
        out = index.head()
        out.update(count=None, items=[])
        return out
    if not refs:
        # 直した記録が無い: 配信の台帳を読まない（監査 5.24.0 の軽 3）。
        out = index.head()
        out.update(count=0, items=[])
        return out
    read = [name for name, _media in index.names]
    items = []
    for name, media, row in index.rows(read):
        if row.get("retracted_at"):
            continue
        hit = [s for s in _seeds_of(row) if s in refs]
        if hit:
            items.append({**_post(name, media, row), "seeds": hit})
    items.sort(key=lambda x: (str(x.get("sent_at") or ""), x["account"], str(x["post_id"]), x.get("step") or 0))
    out = index.head(read)
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
    if index.project is None:
        out = index.head()
        out.update(count=None, items=[], marks=None, unseeded=None, post_unknown=None)
        return out
    # 印を先に読み、印のある口座の配信の台帳だけを読む（監査 5.24.0 の軽 3・要るときだけ読む）。
    marked, unreadable = {}, []
    for name in index.usable:
        try:
            facts, ok = _facts(name)
        except Exception:  # noqa: BLE001
            facts, ok = {}, False
        if not ok:
            unreadable.append({"account": name, "why": "reply_marks_unreadable"})
        if facts:
            marked[name] = facts
    by_post = {}
    for name, media, row in index.rows(list(marked)):
        # 取り下げた投稿は外す（`posts_for` と揃える。消した投稿への指摘は、もう出ていない言い分なので候補にしない）。
        if row.get("retracted_at"):
            continue
        by_post.setdefault((name, row.get("post_id")), (media, row))
    out = index.head(list(marked))
    out["cannot_say"] += unreadable
    items, marks, unseeded, unknown = [], 0, 0, 0
    for name in index.usable:
        facts = marked.get(name, {})
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


def _unreadable(exc) -> dict:
    return {"project": None, "register": None, "accounts": [], "count": None, "items": [],
            "cannot_say": [{"account": None, "why": "posts_unreadable:" + type(exc).__name__}]}


def attach(found: dict, root: str, vessel=None) -> dict:
    """影響の候補（`route.impact` の値）に `posts`（直した記録と候補を指す出した投稿）と `recheck`（確かめ直しの候補）
    を足す。**何が起きても例外を出さない**: 読めない・壊れた物は `cannot_say` に送り、`found` の数・候補は変えない
    （赤にしない・rc を変えない・fix が記録を書いたあとに落ちない。検収 段 12 の危 1）。"""
    try:
        return _attach(found, root, vessel)
    except Exception as exc:  # noqa: BLE001 — 読むだけの欄で判定を変えない
        found["posts"] = _unreadable(exc)
        found["recheck"] = _unreadable(exc)
        return found


def _attach(found: dict, root: str, vessel=None) -> dict:
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
    found["posts"] = posts_for(root, refs, index=index)
    found["recheck"] = rechecks(root, vessel, index=index)
    return found
