"""シナリオ（キャンペーン＝糸・シリーズ＝型）の現在地（設計 5.0 §2〜§4・段 1）。

**読むだけ・書かない・決めない。** 窓口が書いた宣言のファイル（`docs/sns/scenarios/<id>.md`・
`thth_scenario: 1`）を読み、THTH の台帳（queue の原稿の札 `thread:`・`series:`・配信の台帳・
`sent/` のブックマーク・返信の台帳）と突き合わせて、**毎回**現在地を計算する（状態を持たない・§3.5）。
媒体の API は呼ばない。

- 糸（`kind: thread`）: 段（stage）を上から順に満たす。現在の段・段ごとの本数・次の一手（名詞）・止まり。
- 型（`kind: series`）: 枠（slot）を埋め続ける。承認済みのストック・次の枠・空き枠・素材の残り。
- `next` は名詞で書く。**本文は入れない**（`body` という鍵は出さない）。他人の本文は読まない
  （返信は数と時刻だけ）。**絶対パスは出さない**（`declared_in` は repo の相対・原稿は名前だけ）。
- 読めない台帳は `cannot_say` に符丁を出し、0 と混ぜない（取れない数は `null`）。
- 宣言の置き場は、口座の `queue_dir` の 1 つ上の `scenarios/`（`docs/sns/queue` → `docs/sns/scenarios`）。
  宣言を 1 つも持たない project は、糸も型も空（`events` にも何も足さない）。

`--json` の契約は `thth.scenario/1`（設計 §4.2）。**鍵は増やしても名前を変えない・消さない。**
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import os
import posixpath
import re

from . import accounts as accounts_mod
from . import jst

SCHEMA = "thth.scenario/1"
MARKER = "thth_scenario"
SCENARIOS_DIRNAME = "scenarios"
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
KINDS = ("thread", "series")
SNS_MEDIA = ("threads", "x", "bluesky", "mastodon", "facebook")
MEDIA = ("hatena_bookmark", "hatenablog", "site") + SNS_MEDIA
STATES = ("active", "stalled", "closed")
DEFAULT_STALE_AFTER_DAYS = 7
DEFAULT_STOCK_FLOOR = 2
MATERIAL_FLOOR = 2
DEFAULT_REPLIES_WITHIN_HOURS = 24
HEAD_CHARS = 60
SLOT_WINDOW_DAYS = 14
# 枠の一致の幅（分）。関東は手で時刻を数分ずらすので、枠の前後この幅の承認済みはその枠の回に数える。
SLOT_TOLERANCE_MINUTES = 15
DEADLINE_HOURS = 48
DEFAULT_SIGNAL = "article_published"
# 知識の出来事が数の条件の代わりになる段（設計 §3.2）。
EVENT_FOR_STAGE = {"collect": "researched", "diary": "hypothesis"}
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
STAGE_RE = re.compile(r"-\s+([a-z][a-z0-9_]{0,31}):\s*(.*)")
EVENT_RE = re.compile(r"-\s+(\d{4}-\d{2}-\d{2})\s+([a-z][a-z0-9_]{0,31})(?:\s+(.*))?")
DATE_LIKE_RE = re.compile(r"-\s+\d{4}-")
TIME_RE = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")
STAGE_OPTIONS = ("need", "tag", "signal", "replies_within_hours")
# events の id の版の語（設計 §4.3）。
EVENT_PREFIX = "thth.scenario/1"


def _head(text) -> str:
    """表示に出す 1 行（先頭 60 字）。"""
    if not isinstance(text, str):
        return ""
    flat = " ".join(text.split())
    return flat[:HEAD_CHARS]


def _plain(value):
    """front-matter の値（`"…"` で囲まれていれば中身）。空・文字列でなければ None。"""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value or None


def _int(value, *, minimum=0):
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number >= minimum else None


def _true(value) -> bool:
    return isinstance(value, str) and value.strip().lower() in ("true", "yes", "1")


def _date_at(raw):
    """`YYYY-MM-DD` → その日の 00:00 JST。読めなければ None。"""
    try:
        day = datetime.date.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return datetime.datetime(day.year, day.month, day.day, tzinfo=jst.JST)


# ------------------------------------------------------------------ 宣言を読む

@dataclasses.dataclass
class Declaration:
    rel: str                  # repo の根からの相対（posix）
    file: str                 # ファイルの名前
    kind: str | None
    id: str | None
    fm: dict
    stages: list
    events: list
    problems: list            # [{"code", "detail"}]
    mtime: datetime.datetime | None = None   # 宣言のファイルの更新時刻（`started` も動きも無い糸の停滞の起点）


def _split(text):
    if not text.startswith("---"):
        return None
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i], lines[i + 1:]
    return None


def _parse_stage(name, rest, problems):
    tokens = rest.split()
    if not tokens:
        problems.append({"code": "stage_invalid", "detail": name})
        return None
    media = [m for m in tokens[0].split(",") if m]
    unknown = [m for m in media if m not in MEDIA]
    if not media or unknown:
        problems.append({"code": "stage_medium_unknown", "detail": f"{name}: {tokens[0]}"})
        return None
    if len(media) > 1 and any(m not in SNS_MEDIA for m in media):
        problems.append({"code": "stage_medium_unknown", "detail": f"{name}: {tokens[0]}（並べられるのは SNS だけ）"})
        return None
    stage = {"name": name, "media": media, "need": 1, "tag": None, "signal": None,
             "replies_within_hours": None}
    for token in tokens[1:]:
        key, sep, value = token.partition("=")
        if not sep or key not in STAGE_OPTIONS or not value:
            problems.append({"code": "stage_option_invalid", "detail": f"{name}: {token[:40]}"})
            continue
        if key in ("need", "replies_within_hours"):
            number = _int(value, minimum=1)
            if number is None:
                problems.append({"code": "stage_option_invalid", "detail": f"{name}: {token[:40]}"})
                continue
            stage[key] = number
        else:
            stage[key] = value
    if stage["tag"] is not None and media != ["hatena_bookmark"]:
        problems.append({"code": "stage_option_invalid", "detail": f"{name}: tag= ははてなブックマークの段だけ"})
    if stage["signal"] is not None and media != ["site"]:
        problems.append({"code": "stage_option_invalid", "detail": f"{name}: signal= は site の段だけ"})
    if media == ["site"] and stage["signal"] is None:
        stage["signal"] = DEFAULT_SIGNAL
    if name == "warm":
        if any(m not in SNS_MEDIA for m in media):
            problems.append({"code": "stage_medium_unknown", "detail": "warm: SNS の媒体だけ"})
            return None
        if stage["replies_within_hours"] is None:
            stage["replies_within_hours"] = DEFAULT_REPLIES_WITHIN_HOURS
    return stage


def parse_declaration(text: str, rel: str) -> Declaration | None:
    """宣言のファイル 1 本。`thth_scenario: 1` が無ければ None（宣言ではない）。"""
    split = _split(text)
    if split is None:
        return None
    fm_lines, body = split
    fm, problems, seen = {}, [], set()
    for line in fm_lines:
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if key in seen:
            problems.append({"code": "duplicate_key", "detail": key})
        seen.add(key)
        fm[key] = value.strip() or None
    if _plain(fm.get(MARKER)) != "1":
        return None
    file_name = posixpath.basename(rel)
    kind = _plain(fm.get("kind"))
    if kind not in KINDS:
        problems.append({"code": "kind_unknown", "detail": str(kind)[:40]})
        kind = None
    ident = _plain(fm.get("id"))
    if ident is None or not ID_RE.fullmatch(ident):
        problems.append({"code": "invalid_id", "detail": str(ident)[:64]})
        ident = None
    elif file_name != f"{ident}.md":
        problems.append({"code": "id_mismatch_filename", "detail": f"{ident} / {file_name}"})
    title = _plain(fm.get("title"))
    if title is not None and len(title) > HEAD_CHARS:
        problems.append({"code": "title_too_long", "detail": f"{len(title)} 字"})
    goal = _plain(fm.get("goal_text"))
    if goal is not None and len(goal) > HEAD_CHARS:
        problems.append({"code": "goal_text_too_long", "detail": f"{len(goal)} 字"})
    for key, minimum in (("stale_after_days", 1), ("stock_floor", 0), ("material_remaining", 0)):
        if fm.get(key) is not None and _int(fm.get(key), minimum=minimum) is None:
            problems.append({"code": "invalid_number", "detail": key})
    for key in ("started", "material_written_at"):
        if fm.get(key) is not None and _date_at(_plain(fm.get(key))) is None:
            problems.append({"code": "invalid_date", "detail": key})
    stages, events = [], []
    names = set()
    for line in body:
        stripped = line.strip()
        m = EVENT_RE.fullmatch(stripped)
        if m:
            at = _date_at(m.group(1))
            if at is None:
                problems.append({"code": "event_invalid", "detail": stripped[:40]})
                continue
            events.append({"at": m.group(1), "name": m.group(2), "head": _head(m.group(3) or "")})
            continue
        if DATE_LIKE_RE.match(stripped):
            problems.append({"code": "event_invalid", "detail": stripped[:40]})
            continue
        m = STAGE_RE.fullmatch(stripped)
        if m and kind == "thread":
            stage = _parse_stage(m.group(1), m.group(2), problems)
            if stage is not None:
                if stage["name"] in names:
                    problems.append({"code": "stage_duplicate", "detail": stage["name"]})
                    continue
                names.add(stage["name"])
                stages.append(stage)
    if kind == "thread" and not stages:
        problems.append({"code": "no_stages", "detail": ""})
    if kind == "series":
        slots, bad = parse_slots(fm.get("slots"))
        if bad:
            problems.append({"code": "slots_invalid", "detail": bad[:40]})
        if not slots and not _true(fm.get("deadline")):
            problems.append({"code": "no_slots", "detail": ""})
    return Declaration(rel=rel, file=file_name, kind=kind, id=ident, fm=fm, stages=stages,
                       events=events, problems=problems)


def parse_slots(raw):
    """`tue 07:00, fri 07:00`・`08:00, 21:00`（曜日を省けば毎日）→ `([{"day","time"}], 読めなかった部分)`。"""
    raw = _plain(raw)
    if raw is None:
        return [], None
    out = []
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        words = part.split()
        if len(words) == 1 and TIME_RE.fullmatch(words[0]):
            out.append({"day": None, "time": words[0]})
        elif len(words) == 2 and words[0].lower() in DAYS and TIME_RE.fullmatch(words[1]):
            out.append({"day": words[0].lower(), "time": words[1]})
        else:
            return out, part
    return out, None


def _accounts_list(raw):
    raw = _plain(raw)
    if raw is None:
        return None
    return [a.strip() for a in raw.split(",") if a.strip()]


# ------------------------------------------------------------------ 台帳を読む（project ごとに 1 回）

@dataclasses.dataclass
class Item:
    """queue の原稿 1 本（本文は持たない）。"""
    file: str
    account: str
    medium: str
    status: str | None
    publish_at: datetime.datetime | None
    posted_at: datetime.datetime | None
    post_ids: list
    thread: str | None
    series: str | None
    delivery: str | None
    not_after: datetime.datetime | None
    head: str | None = None   # 自分の原稿の題（先頭 60 字・`report.schedule` と同じ取り方）


@dataclasses.dataclass
class Ledger:
    project: str
    accounts: dict            # 名前 → 台帳
    items: list
    declarations: list
    deliveries: dict          # 配信 id → {"state", "at"}
    bookmarks: list           # [{"tags": [...], "at": datetime}]
    hatena_accounts: list
    cannot_say: set
    now: datetime.datetime
    hidden: tuple = ()        # 同じ project でも範囲（`allowed_names`）の外で読まなかった口座


def resolve(target):
    """target（project か口座）→ `(project, 口座の名前の一覧)`。口座なら、その口座の project 全体。"""
    from . import morning
    names = morning.targets(target)
    if not names:
        return None, []
    if names == [target]:
        try:
            cfg = accounts_mod.load_account(target)
        except (accounts_mod.AccountError, ValueError, TypeError):
            return target, [target]
        project = cfg.get("project") or target
        others = morning.targets(project) if cfg.get("project") else []
        return project, (others or [target])
    return target, names


def _scoped(target, allowed_names):
    """`resolve` の結果を範囲（`allowed_names`）で絞る（`events_cli._scenario_events` と同じ絞り方）。

    戻り値 `(project, 読む口座, 読まなかった口座)`。範囲の外の口座の原稿・台帳は開かない（遠くの道の鍵で、
    同じ project の別の口座の原稿名・状態がシナリオ経由で見えないように）。対象が無ければ ValueError。
    """
    project, names = resolve(target)
    if not names:
        raise ValueError("target_unknown")
    hidden = ()
    if allowed_names is not None:
        allowed = set(allowed_names)
        hidden = tuple(n for n in names if n not in allowed)
        names = [n for n in names if n in allowed]
        if not names:
            raise ValueError("target_unknown")
    return project, names, hidden


def _scenario_dirs(cfg):
    """口座の台帳 → `(repo の根, queue の置き場, 宣言の置き場)`。repo を持たなければ None。"""
    if accounts_mod.repo_state(cfg) != accounts_mod.REPO_OK:
        return None
    repo = accounts_mod.resolved_repo_dir(cfg)
    queue_rel = cfg.get("queue_dir") or ""
    if not isinstance(queue_rel, str) or not queue_rel.strip():
        return None
    queue = os.path.join(repo, queue_rel)
    scen = os.path.join(os.path.dirname(os.path.normpath(queue)), SCENARIOS_DIRNAME)
    return repo, queue, scen


def _rel(path, repo):
    return posixpath.join(*os.path.relpath(path, repo).split(os.sep))


def _read_item(path, account_names, accounts):
    from . import bundle as bundle_mod, queuefile
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if bundle_mod.is_bundle_text(text):
        b = bundle_mod.parse_text(text, path)
        if b.malformed:
            return None
        fm = b.front_matter
        post_ids = [bundle_mod.unquote(p.get("post_id")) for p in b.posts if p.get("post_id")]
        posted = [jst.parse(bundle_mod.unquote(p.get("posted_at"))) for p in b.posts if p.get("posted_at")]
        posted_at = max((p for p in posted if p is not None), default=None)
        first = next((seg for seg in b.segments if isinstance(seg, str) and seg.strip()), "")
        head = first.strip().split("\n", 1)[0] if first else None
    else:
        qf = queuefile.parse_text(text, path)
        if qf.malformed:
            return None
        fm = qf.front_matter
        post_ids = [fm["post_id"]] if _plain(fm.get("post_id")) else []
        posted_at = jst.parse(_plain(fm.get("posted_at")))
        head = None
    account = _plain(fm.get("account"))
    if account not in account_names or account not in accounts:
        return None
    medium = accounts[account].get("media")
    if medium == "hatenablog":
        head = _plain(fm.get("title"))
    elif head is None and not bundle_mod.is_bundle_text(text):
        section = queuefile.extract_section(qf.body, medium or "") or ""
        head = section.strip().split("\n", 1)[0] or None
    try:
        publish_at = queuefile.parse_publish_at(fm.get("publish_at")) if fm.get("publish_at") else None
    except (ValueError, TypeError):
        publish_at = None
    try:
        not_after = queuefile.parse_not_after(fm)
    except (ValueError, TypeError):
        not_after = None
    return Item(file=os.path.basename(path), account=account, medium=medium,
                status=_plain(fm.get("status")), publish_at=publish_at, posted_at=posted_at,
                post_ids=post_ids, thread=_plain(fm.get("thread")), series=_plain(fm.get("series")),
                delivery=_plain(fm.get("delivery")), not_after=not_after, head=_head(head) or None)


def manuscript_head(path, medium) -> str | None:
    """原稿 1 本の題（先頭 60 字）。SNS は `## <媒体>` の節の 1 行目（`report.schedule` と同じ）、連投は最初の段、
    はてなブログは front matter の `title`。読めなければ None。本文の残りは返さない。"""
    from . import bundle as bundle_mod, queuefile
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        if bundle_mod.is_bundle_text(text):
            b = bundle_mod.parse_text(text, path)
            first = next((seg for seg in b.segments if isinstance(seg, str) and seg.strip()), "")
            line = first.strip().split("\n", 1)[0] if first else ""
        else:
            qf = queuefile.parse_text(text, path)
            if medium == "hatenablog":
                line = _plain(qf.front_matter.get("title")) or ""
            else:
                line = (queuefile.extract_section(qf.body, medium or "") or "").strip().split("\n", 1)[0]
    except (OSError, UnicodeDecodeError, ValueError, TypeError, AttributeError):
        return None
    return _head(line) or None


def load_ledger(project, names, *, now=None, hidden=()) -> Ledger:
    """project の台帳を 1 回だけ読む。**読むだけ。** 1 つ読めなくても他は読む（`cannot_say`）。"""
    from . import deliveries as deliveries_mod, sent as sent_mod
    now = now if now is not None else jst.now_jst()
    cannot = set()
    accounts = {}
    for name in names:
        try:
            accounts[name] = accounts_mod.load_account(name)
        except (accounts_mod.AccountError, ValueError, TypeError):
            cannot.add("account_unreadable")
    dirs = {}
    for name, cfg in accounts.items():
        if accounts_mod.repo_state(cfg) == accounts_mod.REPO_BROKEN:
            cannot.add("repo_unreadable")
            continue
        found = _scenario_dirs(cfg)
        if found is not None:
            dirs.setdefault(os.path.realpath(found[1]), found)
    items, declarations, seen_scen = [], [], set()
    for repo, queue, scen in dirs.values():
        real_scen = os.path.realpath(scen)
        if real_scen not in seen_scen:
            seen_scen.add(real_scen)
            try:
                files = sorted(n for n in os.listdir(scen) if n.endswith(".md"))
            except FileNotFoundError:
                files = []
            except OSError:
                files = []
                cannot.add("scenarios_unreadable")
            for file_name in files:
                path = os.path.join(scen, file_name)
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                    mtime = datetime.datetime.fromtimestamp(os.stat(path).st_mtime, tz=jst.JST)
                except (OSError, UnicodeDecodeError):
                    cannot.add("scenarios_unreadable")
                    continue
                decl = parse_declaration(text, _rel(path, repo))
                if decl is not None:
                    decl.mtime = mtime
                    declarations.append(decl)
        try:
            files = sorted(n for n in os.listdir(queue) if n.endswith(".md"))
        except FileNotFoundError:
            files = []
        except OSError:
            files = []
            cannot.add("queue_unreadable")
        for file_name in files:
            try:
                item = _read_item(os.path.join(queue, file_name), names, accounts)
            except (OSError, UnicodeDecodeError, ValueError, TypeError, AttributeError):
                cannot.add("queue_unreadable")
                continue
            if item is not None:
                items.append(item)
    deliveries, bookmarks = {}, []
    hatena = sorted(name for name, cfg in accounts.items() if cfg.get("media") == "hatenablog")
    if declarations:
        for name in hatena:
            try:
                records, broken = deliveries_mod.list_for(name)
            except (OSError, ValueError):
                records, broken = [], [True]
            if broken:
                cannot.add("deliveries_unreadable")
            for record in records:
                history = record.get("history") or []
                at = jst.parse(history[-1].get("at")) if history else None
                deliveries[record["delivery"]] = {"state": record.get("state"), "at": at}
            errors = []
            for row in sent_mod.records(accounts_mod.state_dir_for(name), errors=errors):
                if (row.get("kind") != "bookmark_add" or row.get("result") != "confirmed"
                        or row.get("retracted_at")):
                    continue
                tags = row.get("tags") if isinstance(row.get("tags"), list) else []
                bookmarks.append({"tags": [t for t in tags if isinstance(t, str)],
                                  "at": jst.parse(row.get("sent_at"))})
            if errors:
                cannot.add("sent_unreadable")
    return Ledger(project=project, accounts=accounts, items=items, declarations=declarations,
                  deliveries=deliveries, bookmarks=bookmarks, hatena_accounts=hatena,
                  cannot_say=cannot, now=now, hidden=tuple(hidden))


def usable_declarations(ledger):
    """計算に使う宣言（id と kind が読め・project が合い・id が重ならないもの）。"""
    out, seen = [], set()
    for decl in ledger.declarations:
        if decl.id is None or decl.kind is None:
            continue
        project = _plain(decl.fm.get("project"))
        if project is not None and project != ledger.project:
            continue
        if decl.id in seen:
            continue
        seen.add(decl.id)
        out.append(decl)
    return out


# ------------------------------------------------------------------ 糸の現在地

def _is_posted(item):
    return item.status == "posted" or bool(item.post_ids)


def _is_future_approved(item, now):
    return (item.status == "approved" and not _is_posted(item) and item.publish_at is not None
            and item.publish_at > now and not (item.not_after is not None and now > item.not_after))


def _count_sns(stage, items, now):
    done = pending = drafts = 0
    by_medium = {m: {"done": 0, "pending": 0} for m in stage["media"]}
    times = []
    for item in items:
        if item.medium not in stage["media"]:
            continue
        if _is_posted(item):
            done += 1
            by_medium[item.medium]["done"] += 1
            if item.posted_at is not None:
                times.append(item.posted_at)
        elif _is_future_approved(item, now):
            pending += 1
            by_medium[item.medium]["pending"] += 1
        elif item.status == "draft":
            drafts += 1
    return {"done": done, "pending": pending, "drafts": drafts, "by_medium": by_medium}, times


def _count_hatenablog(items, ledger):
    done = pending = drafts = conflict = 0
    times = []
    for item in items:
        if item.medium != "hatenablog":
            continue
        record = ledger.deliveries.get(item.delivery) if item.delivery else None
        if record is not None:
            state = record["state"]
            if state == "published":
                done += 1
                if record["at"] is not None:
                    times.append(record["at"])
            elif state in ("wanted", "inflight", "unknown"):
                pending += 1
            elif state == "conflict":
                conflict += 1
        elif item.status == "approved" and not _is_posted(item):
            pending += 1
        elif item.status == "draft":
            drafts += 1
    return {"done": done, "pending": pending, "drafts": drafts, "conflict": conflict}, times


def _warm(stage, items, ledger):
    """返信の数と、返していないまま `replies_within_hours` を越えたもの。他人の本文は読まない（数と時刻だけ）。"""
    from . import replies as replies_mod, unanswered
    total = overdue = 0
    times = []
    limit = ledger.now - datetime.timedelta(hours=stage["replies_within_hours"])
    by_account = {}
    for item in items:
        if item.medium in stage["media"] and _is_posted(item):
            by_account.setdefault(item.account, []).extend(item.post_ids)
    for account, post_ids in sorted(by_account.items()):
        cfg = ledger.accounts[account]
        answered = unanswered._relations(account, cfg, ledger.now)[1]
        for post_id in post_ids:
            data = replies_mod.load(account, post_id=post_id)
            if data.get("broken"):
                raise ValueError("replies_unreadable")
            for row in data["replies"]:
                if row.get("own") is not False:
                    continue
                total += 1
                at = jst.parse(row.get("timestamp")) or jst.parse(row.get("collected_at"))
                if at is not None:
                    times.append(at)
                if row.get("reply_id") not in answered and at is not None and at < limit:
                    overdue += 1
    return total, overdue, times


def thread_status(decl, ledger) -> dict:
    """糸 1 本の現在地（`thth.scenario/1` の `threads[]` の 1 行）。"""
    now = ledger.now
    items = [item for item in ledger.items if item.thread == decl.id]
    event_names = {e["name"] for e in decl.events}
    cannot, stages, times = [], [], []
    for e in decl.events:
        at = _date_at(e["at"])
        if at is not None and at <= now:
            times.append(at)
    started = _date_at(_plain(decl.fm.get("started")))
    conflict = 0
    for stage in decl.stages:
        medium = stage["media"][0] if len(stage["media"]) == 1 else list(stage["media"])
        row = {"name": stage["name"], "medium": medium}
        if stage["name"] == "warm":
            row["replies_within_hours"] = stage["replies_within_hours"]
            try:
                replies, overdue, warm_times = _warm(stage, items, ledger)
                times += warm_times
            except Exception:  # noqa: BLE001 — 読めない台帳は数えない（0 と混ぜない）
                replies = overdue = None
                cannot.append("replies_unreadable")
            row.update({"replies": replies, "overdue": overdue,
                        "satisfied": bool(replies) and overdue == 0})
            if row["satisfied"]:
                row["satisfied_by"] = "count"
            stages.append(row)
            continue
        row["need"] = stage["need"]
        satisfied_by = None
        if stage["media"] == ["site"]:
            done = 1 if stage["signal"] in event_names else 0
            row.update({"done": done, "pending": 0, "signal": stage["signal"]})
            if done:
                satisfied_by = "event"
        elif stage["media"] == ["hatena_bookmark"]:
            tag = stage["tag"] or decl.id
            row["tag"] = tag
            if not ledger.hatena_accounts:
                row.update({"done": None, "pending": None})
                cannot.append("hatena_account_missing")
            else:
                marks = [b for b in ledger.bookmarks if tag in b["tags"]]
                times += [b["at"] for b in marks if b["at"] is not None]
                row.update({"done": len(marks), "pending": 0})
        elif stage["media"] == ["hatenablog"]:
            if not ledger.hatena_accounts:
                row.update({"done": None, "pending": None})
                cannot.append("hatena_account_missing")
            else:
                counts, found = _count_hatenablog(items, ledger)
                times += found
                conflict += counts["conflict"]
                row.update(counts)
        else:
            counts, found = _count_sns(stage, items, now)
            times += found
            row.update(counts)
        if satisfied_by is None and row["done"] is not None and row["done"] >= stage["need"]:
            satisfied_by = "count"
        substitute = EVENT_FOR_STAGE.get(stage["name"])
        if satisfied_by is None and substitute and substitute in event_names:
            satisfied_by = "event"
        row["satisfied"] = satisfied_by is not None
        if satisfied_by is not None:
            row["satisfied_by"] = satisfied_by
        stages.append(row)

    closed = "closed" in event_names
    index = next((i for i, row in enumerate(stages) if not row["satisfied"]), None)
    of = len(stages)
    if index is None:
        stage_pos = {"name": stages[-1]["name"] if stages else None, "index": of, "of": of}
    else:
        stage_pos = {"name": stages[index]["name"], "index": index + 1, "of": of}
    next_items = [] if closed else _next(decl, stages, index)
    last = max(times, default=None)
    # 停滞の起点: 最後の動き → `started` → 宣言のファイルの更新時刻（どれも無い糸が永遠に停滞しないように）。
    if last is not None:
        basis, basis_from = last, "movement"
    elif started is not None:
        basis, basis_from = started, "started"
    else:
        basis, basis_from = decl.mtime, ("file_mtime" if decl.mtime is not None else None)
    idle_days = int((now - basis).total_seconds() // 86400) if basis is not None and basis <= now else None
    stale = _int(decl.fm.get("stale_after_days"), minimum=1) or DEFAULT_STALE_AFTER_DAYS
    pending_now = index is not None and bool(stages[index].get("pending"))
    if closed:
        state = "closed"
    elif conflict or (not pending_now and basis is not None
                      and now - basis > datetime.timedelta(days=stale)):
        state = "stalled"
    else:
        state = "active"
    stalled = None
    if state == "stalled":
        stalled = {"days": idle_days, "stale_after_days": stale,
                   "since": jst.iso(basis) if basis is not None else None, "since_from": basis_from}
        stalled["label"] = stalled_label(stage_pos["name"], stage_pos["index"] if index is not None else None)
        if conflict:
            stalled["reason"] = "delivery_conflict"
    title = _plain(decl.fm.get("title"))
    goal = _plain(decl.fm.get("goal_text"))
    return {
        "id": decl.id, "title": _head(title) if title else None,
        "goal_text": _head(goal) if goal else None,
        "declared_in": decl.rel, "state": state, "stage": stage_pos, "stages": stages,
        "next": next_items,
        "last_movement_at": jst.iso(last) if last is not None else None,
        "idle_days": idle_days,
        "stalled": stalled,
        "events": [dict(e) for e in decl.events],
        "cannot_say": sorted(set(cannot)),
    }


# 画面に出す日本語（`next[].label`・`stalled_label`）。`what` は今までどおり（LLM 向けの符丁まじり）。
STAGE_JA = {"collect": "リサーチ", "diary": "仮説", "article": "本記事", "announce": "SNS 告知", "warm": "エンゲージ"}
MEDIUM_JA = {"threads": "Threads", "x": "X", "bluesky": "Bluesky", "mastodon": "Mastodon",
             "facebook": "Facebook", "hatenablog": "はてなブログ", "hatena_bookmark": "はてブ", "site": "サイト"}
STALLED_JA = {"collect": "リサーチが進んでいない", "diary": "仮説のブログが未公開", "article": "本記事が未公開",
              "announce": "SNS 告知が未投稿", "warm": "返信の対応が止まっている"}


def stage_ja(name, index=None) -> str:
    """段の名前の日本語。宣言で独自に付けた名前は出さず「n 段目」。"""
    if name in STAGE_JA:
        return STAGE_JA[name]
    return f"{index} 段目" if index else "途中の段"


def stalled_label(name, index=None) -> str:
    return STALLED_JA.get(name) or f"{stage_ja(name, index)}で止まっている"


def _media_ja(media) -> str:
    return "・".join(MEDIUM_JA.get(m, "SNS") for m in media)


def _next(decl, stages, index):
    """現在の段で足りないもの（**名詞で**・本文は書かない）。`what` は今までどおり、`label` は画面の言葉。"""
    if index is None:
        return [{"stage": stages[-1]["name"] if stages else None, "kind": "close",
                 "what": "closed を糸のファイルに 1 行（全段を満たした）",
                 "label": "全部の段が済んだ（終えるなら記録する）"}]
    row = stages[index]
    name = row["name"]
    ja = stage_ja(name, index + 1)
    out = []
    if name == "warm":
        if row["replies"] is None:
            return [{"stage": name, "kind": "cannot_say", "what": "返信の台帳が読めない",
                     "label": "返信の台帳が読めない"}]
        if row["overdue"]:
            out.append({"stage": name, "kind": "replies",
                        "what": f"未返信のうち {row['replies_within_hours']} 時間を越えた {row['overdue']} 件",
                        "label": f"返していない返信 {row['overdue']} 件（{row['replies_within_hours']} 時間を過ぎた）"})
        else:
            out.append({"stage": name, "kind": "wait", "what": "返信待ち（まだ 0 件）",
                        "label": "返信を待つ（まだ 0 件）"})
        return out
    if "signal" in row:
        return [{"stage": name, "kind": "signal", "what": f"{row['signal']} を糸のファイルに 1 行",
                 "label": f"{ja}を公開したら記録する" if name == "article" else f"{ja}が済んだら記録する"}]
    if row["done"] is None:
        return [{"stage": name, "kind": "cannot_say", "what": "この段の台帳が読めない",
                 "label": f"{ja}の台帳が読めない"}]
    missing = row["need"] - row["done"] - (row.get("pending") or 0)
    if row.get("conflict"):
        out.append({"stage": name, "kind": "conflict", "what": f"配信の台帳が conflict の原稿 {row['conflict']} 本",
                    "label": f"配信の記録が食い違う原稿 {row['conflict']} 本を確かめる"})
    if row.get("pending"):
        out.append({"stage": name, "kind": "wait", "what": f"承認済み {row['pending']} 本の公開待ち",
                    "label": f"承認済み {row['pending']} 本の公開を待つ"})
    if missing > 0:
        if row.get("drafts"):
            out.append({"stage": name, "kind": "approve", "what": f"下書き {row['drafts']} 本の承認",
                        "label": f"{ja}の下書き {row['drafts']} 本を承認する"})
        medium = row["medium"]
        if isinstance(medium, list):
            done = [m for m in medium if row["by_medium"][m]["done"] or row["by_medium"][m]["pending"]]
            rest = [m for m in medium if m not in done] or medium
            what = f"{'・'.join(rest)} に {missing} 本（{name}）"
            label = f"{_media_ja(rest)} に{ja}を {missing} 本"
            if done:
                what += f"（{'・'.join(done)} は済み）"
                label += f"（{_media_ja(done)} は済み）"
        elif medium == "hatena_bookmark":
            what = f"hatena_bookmark に {missing} 本（タグ {row['tag']}）"
            label = f"はてブをあと {missing} 本"
        else:
            what = f"{medium} に {missing} 本（{name}）"
            label = f"{_media_ja([medium])} に{ja}を {missing} 本"
        out.append({"stage": name, "kind": "count", "what": what, "label": label})
    return out


# ------------------------------------------------------------------ 型の現在地

def _slot_times(slots, now, days):
    today = now.date()
    out = []
    for offset in range(days + 1):
        day = today + datetime.timedelta(days=offset)
        for slot in slots:
            if slot["day"] is not None and DAYS[day.weekday()] != slot["day"]:
                continue
            hour, minute = (int(x) for x in slot["time"].split(":"))
            at = datetime.datetime(day.year, day.month, day.day, hour, minute, tzinfo=jst.JST)
            if now < at <= now + datetime.timedelta(days=days):
                out.append(at)
    return sorted(out)


def _minute(at):
    return jst.to_jst(at).replace(second=0, microsecond=0)


def _round_key(at, slot_times):
    """承認済み 1 本がどの「回」か。枠の前後 `SLOT_TOLERANCE_MINUTES` 分ならその枠、外れれば公開の分。"""
    moment = _minute(at)
    tolerance = datetime.timedelta(minutes=SLOT_TOLERANCE_MINUTES)
    for slot in slot_times:
        if abs(moment - slot) <= tolerance:
            return slot
    return moment


def _rounds(items, slots, now):
    """承認済みの未来を「回」に束ねる: `{回の時刻: {口座}}`。同じ分（と枠の幅の中）は口座をまたいで 1 回。"""
    if not items:
        return {}
    last = max(item.publish_at for item in items)
    days = max(SLOT_WINDOW_DAYS, int((last - now).total_seconds() // 86400) + 2)
    slot_times = [_minute(at) for at in _slot_times(slots, now, days)] if slots else []
    out = {}
    for item in items:
        out.setdefault(_round_key(item.publish_at, slot_times), set()).add(item.account)
    return out


def _series_scope(decl, ledger):
    """型の口座（宣言の `accounts`・無ければ project の口座）のうち読める口座と、型に要る口座。

    戻り値 `(読める型の口座, 回が「埋まった」と言うのに要る口座の集合か None, 符丁)`。宣言の口座のうち
    範囲の外（`ledger.hidden`）は数えない・出さない。宣言の口座が無ければ、どれか 1 口座で埋まる。
    """
    declared = _accounts_list(decl.fm.get("accounts"))
    cannot = []
    if not declared:
        return sorted(ledger.accounts), None, cannot
    visible = [a for a in declared if a in ledger.accounts]
    if [a for a in declared if a not in ledger.accounts and a not in ledger.hidden]:
        cannot.append("series_account_unknown")
    if [a for a in declared if a in ledger.hidden]:
        cannot.append("series_account_out_of_scope")
    return visible, set(visible), cannot


def _filled(accounts, required) -> bool:
    if not accounts:
        return False
    return required <= accounts if required else True


def series_status(decl, ledger) -> dict:
    """型 1 つの現在地（`thth.scenario/1` の `series[]` の 1 行）。

    数えるのは「回」: 同じ分（枠の前後 `SLOT_TOLERANCE_MINUTES` 分は同じ枠）に出る承認済みは、口座をまたいで
    1 回。`approved_future` は回の数、`approved_future_items` は本数。枠が `filled` なのは、型の口座
    （宣言の `accounts`）がどれもその回に承認済みを持つとき。`filled_by` はその回に承認済みを持つ口座。
    """
    now = ledger.now
    accounts, required, cannot = _series_scope(decl, ledger)
    scope = set(accounts)
    items = [item for item in ledger.items if item.series == decl.id and item.account in scope]
    slots, _bad = parse_slots(decl.fm.get("slots"))
    future = [item for item in items if _is_future_approved(item, now)]
    rounds = _rounds(future, slots, now)
    explicit_floor = _int(decl.fm.get("stock_floor"), minimum=0)
    is_deadline = _true(decl.fm.get("deadline"))
    # 期日の型は、目安（`stock_floor`）を明示したときだけストック不足を言う（期日の型は枠を持たない）。
    floor = explicit_floor if explicit_floor is not None else (None if is_deadline else DEFAULT_STOCK_FLOOR)
    last = max(rounds, default=None)
    n_rounds = len(rounds)
    stock = {"approved_future": n_rounds, "floor": floor, "low": floor is not None and n_rounds < floor,
             "days_covered": max(0, int((last - now).total_seconds() // 86400)) if last is not None else 0,
             "approved_future_items": len(future)}
    next_slot, empty, empty_slots = None, None, None
    if slots:
        upcoming = _slot_times(slots, now, SLOT_WINDOW_DAYS)
        empty_slots = []
        for at in upcoming:
            by = rounds.get(_minute(at), set())
            if not _filled(by, required):
                empty_slots.append({"at": jst.iso(at), "filled_by": sorted(by)})
        empty = len(empty_slots)
        if upcoming:
            by = rounds.get(_minute(upcoming[0]), set())
            next_slot = {"at": jst.iso(upcoming[0]), "filled": _filled(by, required), "filled_by": sorted(by)}
    material = None
    remaining = _int(decl.fm.get("material_remaining"), minimum=0)
    if remaining is not None:
        written = _date_at(_plain(decl.fm.get("material_written_at")))
        material = {"remaining": remaining, "written_at": written.date().isoformat() if written else None,
                    "floor": MATERIAL_FLOOR, "low": remaining < MATERIAL_FLOOR}
    deadline = None
    if is_deadline:
        soon = now + datetime.timedelta(hours=DEADLINE_HOURS)
        deadline = {
            "expiring_48h": sum(1 for item in items if not _is_posted(item) and item.status != "approved"
                                and item.not_after is not None and now <= item.not_after <= soon),
            "passed": sum(1 for item in items if not _is_posted(item) and item.status in ("draft", "approved")
                          and item.not_after is not None and item.not_after < now),
        }
    next_items = []
    if stock["low"]:
        next_items.append({"kind": "stock", "what": f"承認済みストック {stock['approved_future']} 本（目安 {floor}）",
                           "label": f"承認済みのストックが {n_rounds} 回分（目安 {floor} 回）"})
    if next_slot is not None and not next_slot["filled"]:
        at = jst.parse(next_slot["at"])
        next_items.append({"kind": "slot", "what": f"次の枠 {next_slot['at'][:16].replace('T', ' ')} に原稿 1 本",
                           "label": f"次の枠 {at.month}/{at.day} {at.strftime('%H:%M')} の原稿を用意する"})
    if material is not None and material["low"]:
        next_items.append({"kind": "collect", "what": f"調べる（collect）を起こす（素材の残り {remaining} 回）",
                           "label": f"素材を集める（残り {remaining} 回分）"})
    if deadline is not None and deadline["expiring_48h"]:
        next_items.append({"kind": "deadline", "what": f"期限 48 時間以内の未承認 {deadline['expiring_48h']} 本",
                           "label": f"期限まで 48 時間を切った未承認 {deadline['expiring_48h']} 本を確かめる"})
    title = _plain(decl.fm.get("title"))
    goal = _plain(decl.fm.get("goal_text"))
    return {
        "id": decl.id, "title": _head(title) if title else None,
        "goal_text": _head(goal) if goal else None,
        "declared_in": decl.rel,
        "accounts": accounts,
        "slots": slots, "stock": stock, "next_slot": next_slot, "empty_slots_14d": empty,
        "empty_slots": empty_slots,
        "material": material, "deadline": deadline, "next": next_items,
        "cannot_say": sorted(set(cannot)),
    }


# ------------------------------------------------------------------ 束ねる

def _untied(ledger):
    drafts = deliveries = 0
    for item in ledger.items:
        if item.thread or item.series or _is_posted(item):
            continue
        if item.status not in ("draft", "approved"):
            continue
        if item.medium == "hatenablog":
            if item.delivery and (ledger.deliveries.get(item.delivery) or {}).get("state") == "published":
                continue
            deliveries += 1
        else:
            drafts += 1
    return {"drafts": drafts, "deliveries": deliveries}


def build(target, *, now=None, ledger=None, allowed_names=None) -> dict:
    """`thth scenario <project|account> --json` の中身。**読むだけ。** 対象が無ければ ValueError。

    `allowed_names` が渡されたら、その外の口座（同じ project でも）の原稿・台帳は読まない（遠くの道の鍵）。
    """
    now = now if now is not None else jst.now_jst()
    if ledger is None:
        project, names, hidden = _scoped(target, allowed_names)
        ledger = load_ledger(project, names, now=now, hidden=hidden)
    decls = usable_declarations(ledger)
    threads = [thread_status(d, ledger) for d in decls if d.kind == "thread"]
    series = [series_status(d, ledger) for d in decls if d.kind == "series"]
    return {
        "schema": SCHEMA, "report_type": "scenario", "generated_at": jst.iso(ledger.now),
        "project": ledger.project, "accounts": sorted(ledger.accounts),
        "threads": threads, "series": series, "untied": _untied(ledger),
        "cannot_say": sorted(ledger.cannot_say),
    }


def _item_row(item, ledger):
    """`show` の原稿 1 本（本文は持たない・名前と状態と id だけ）。"""
    now = ledger.now
    record = ledger.deliveries.get(item.delivery) if item.delivery else None
    return {"file": item.file, "account": item.account, "medium": item.medium,
            "status": item.status, "posted": _is_posted(item),
            "delivery_state": record["state"] if record else None,
            "publish_at": jst.iso(item.publish_at) if item.publish_at else None,
            "posted_at": jst.iso(item.posted_at) if item.posted_at else None,
            "head": item.head,
            # 5.0 段 1＋2（足すだけ）: 回ごとの配信 id（はてなブログは配信の台帳の id・SNS は投稿の id）。
            "delivery": item.delivery, "post_ids": list(item.post_ids),
            "future_approved": _is_future_approved(item, now)}


def _stage_items(stage, decl, items, ledger):
    """段 1 つの成果物（段 2 のダッシュボードの「成果物」）。本文・他人の本文・URL は入れない。"""
    media = stage["media"]
    if stage["name"] == "warm":
        return []
    if media == ["site"]:
        return [{"kind": "event", "name": e["name"], "at": e["at"], "head": e.get("head")}
                for e in decl.events if e["name"] == stage["signal"]]
    if media == ["hatena_bookmark"]:
        tag = stage["tag"] or decl.id
        marks = sorted((b for b in ledger.bookmarks if tag in b["tags"]),
                       key=lambda b: b["at"] or ledger.now)
        return [{"kind": "bookmark", "status": "confirmed", "at": jst.iso(b["at"]) if b["at"] else None}
                for b in marks]
    return [dict(_item_row(item, ledger), kind="draft") for item in items if item.medium in media]


def _slots_upcoming(decl, items, ledger, days, required):
    """型の今後の枠（`days` 日）と、埋めている承認済みの原稿の名前と題（枠の前後の幅も同じ回）。"""
    slots, _bad = parse_slots(decl.fm.get("slots"))
    if not slots:
        return []
    slot_times = [_minute(at) for at in _slot_times(slots, ledger.now, days)]
    by_slot = {}
    for item in sorted(items, key=lambda i: (i.publish_at or ledger.now, i.file)):
        if _is_future_approved(item, ledger.now):
            by_slot.setdefault(_round_key(item.publish_at, slot_times), []).append(item)
    out = []
    for at in slot_times:
        rows = by_slot.get(at, [])
        accounts = {item.account for item in rows}
        out.append({"at": jst.iso(at), "filled": _filled(accounts, required),
                    "file": rows[0].file if rows else None, "head": rows[0].head if rows else None,
                    "filled_by": sorted(accounts)})
    return out


# `show` の型の枠の先（段 2 のシリーズの画面は 4 週）。
SHOW_SLOT_DAYS = 28


def show(target, ident, *, now=None, allowed_names=None) -> dict:
    """`thth scenario show <project> <id>`: 1 本の詳細（段ごとの原稿の名前と状態・出来事）。

    5.0 段 2 のために足した鍵（`thth.scenario/1` は足すだけ）: 糸は `thread.stages[].items`（段ごとの成果物）、
    型は `series.slots_upcoming`（今後 4 週の枠）、両方に `items[].delivery`・`items[].post_ids`・
    `items[].future_approved` と `deliveries`（配信の台帳の id の列）。
    """
    now = now if now is not None else jst.now_jst()
    project, names, hidden = _scoped(target, allowed_names)
    ledger = load_ledger(project, names, now=now, hidden=hidden)
    decl = next((d for d in usable_declarations(ledger) if d.id == ident), None)
    if decl is None:
        raise ValueError("scenario_unknown")
    key = "thread" if decl.kind == "thread" else "series"
    body = thread_status(decl, ledger) if key == "thread" else series_status(decl, ledger)
    items = [item for item in ledger.items if getattr(item, key) == decl.id]
    items.sort(key=lambda i: (i.publish_at or now, i.file))
    if key == "thread":
        for row, stage in zip(body["stages"], decl.stages):
            row["items"] = _stage_items(stage, decl, items, ledger)
    else:
        _accounts, required, _cannot = _series_scope(decl, ledger)
        scope = set(body["accounts"])   # 枠を埋めるのは型の口座の原稿だけ（`series_status` と同じ）
        body["slots_upcoming"] = _slots_upcoming(decl, [i for i in items if i.account in scope], ledger,
                                                 SHOW_SLOT_DAYS, required)
    rows = [_item_row(item, ledger) for item in items]
    return {"schema": SCHEMA, "report_type": "scenario_show", "generated_at": jst.iso(ledger.now),
            "project": ledger.project, "kind": decl.kind, key: body, "items": rows,
            "deliveries": sorted({item.delivery for item in items if item.delivery}),
            "cannot_say": sorted(ledger.cannot_say)}


def lint(target, *, now=None, allowed_names=None) -> dict:
    """`thth scenario lint <project>`: 宣言の誤り・無い id を指す札・結ばれていない原稿。**予告だけ。**"""
    now = now if now is not None else jst.now_jst()
    project, names, hidden = _scoped(target, allowed_names)
    ledger = load_ledger(project, names, now=now, hidden=hidden)
    problems = []
    seen = {}
    for decl in ledger.declarations:
        for p in decl.problems:
            problems.append({"code": p["code"], "file": decl.rel, "id": decl.id, "detail": p["detail"]})
        declared_project = _plain(decl.fm.get("project"))
        if declared_project is not None and declared_project != project:
            problems.append({"code": "project_mismatch", "file": decl.rel, "id": decl.id,
                             "detail": declared_project[:64]})
        if decl.kind == "thread" and decl.fm.get("started") is None:
            # `started` が無いと、動きの無い糸の停滞は宣言のファイルの更新時刻から数える（予告だけ）。
            problems.append({"code": "started_missing", "file": decl.rel, "id": decl.id,
                             "detail": "started: YYYY-MM-DD を書くと、動きの無い糸の停滞をその日から数えます"})
        if decl.id is not None and decl.kind is not None:
            if decl.id in seen:
                problems.append({"code": "duplicate_id", "file": decl.rel, "id": decl.id, "detail": seen[decl.id]})
            else:
                seen[decl.id] = decl.rel
    kinds = {d.id: d.kind for d in usable_declarations(ledger)}
    untied = []
    for item in ledger.items:
        for key in ("thread", "series"):
            value = getattr(item, key)
            if value is not None and kinds.get(value) != key:
                problems.append({"code": f"{key}_not_declared", "file": item.file, "id": value[:64],
                                 "detail": item.account})
        if (not item.thread and not item.series and not _is_posted(item)
                and item.status in ("draft", "approved")):
            untied.append({"file": item.file, "account": item.account, "status": item.status})
    return {"schema": SCHEMA, "report_type": "scenario_lint", "generated_at": jst.iso(ledger.now),
            "project": project, "declarations": len(ledger.declarations),
            "problems": problems, "untied": untied, "cannot_say": sorted(ledger.cannot_say)}


# ------------------------------------------------------------------ 原稿の lint（予告だけ）

def manuscript_warnings(path, fm) -> list:
    """原稿の `thread:`・`series:` が宣言のファイルを指しているか（**予告だけ・断らない**）。

    宣言は原稿の置き場の 1 つ上の `scenarios/<id>.md`。札が無ければ何も言わない（今までと同じ）。
    """
    out = []
    for key in ("thread", "series"):
        value = _plain((fm or {}).get(key))
        if value is None:
            continue
        ok = False
        if ID_RE.fullmatch(value):
            decl_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(path))),
                                     SCENARIOS_DIRNAME, f"{value}.md")
            try:
                with open(decl_path, encoding="utf-8") as f:
                    decl = parse_declaration(f.read(), f"{SCENARIOS_DIRNAME}/{value}.md")
                ok = decl is not None and decl.kind == key and decl.id == value
            except (OSError, UnicodeDecodeError):
                ok = False
        if not ok:
            shown = value[:64]
            out.append(f"warning: {key}_not_declared: {key}: {shown} の宣言（{SCENARIOS_DIRNAME}/{shown}.md・"
                       f"kind: {key}）が見つかりません（出すのは止めません）")
    return out


# ------------------------------------------------------------------ events（§4.3）

def _any_declaration_file(names) -> bool:
    """宣言の置き場に `.md` が 1 つでもあるか（events の毎回の読みで queue まで開かないための前の確かめ）。
    台帳が読めない口座は True に倒す（読めないことは `load_ledger` が `cannot_say` で言う）。"""
    for name in names:
        try:
            found = _scenario_dirs(accounts_mod.load_account(name))
        except (accounts_mod.AccountError, ValueError, TypeError):
            return True
        if found is None:
            continue
        try:
            if any(n.endswith(".md") for n in os.listdir(found[2])):
                return True
        except FileNotFoundError:
            continue
        except OSError:
            return True
    return False


def stalled_event_id(project, ident, stage) -> str:
    raw = f"{EVENT_PREFIX}|{project}|{ident}|{stage}"
    return "ev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def series_low_event_id(project, ident) -> str:
    raw = f"{EVENT_PREFIX}|{project}|{ident}|series_low"
    return "ev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def events_for(project, names, *, now, make_event) -> tuple:
    """`scenario_stalled`・`series_low`（どちらも続いている状態）。`(出来事, 読めなかった符丁)`。

    宣言が 1 つも無ければ何も読まずに空を返す（宣言を持たない口座の events は今までと同じ）。
    """
    if not _any_declaration_file(names):
        return [], set()
    ledger = load_ledger(project, names, now=now)
    if not usable_declarations(ledger):
        return [], set()
    payload = build(project, now=now, ledger=ledger)
    out = []
    for row in payload["threads"]:
        if row["state"] != "stalled":
            continue
        stage = row["stage"]["name"]
        days = row["stalled"]["days"] if row["stalled"] else None
        nxt = "・".join(n["what"] for n in row["next"])
        summary = (f"キャンペーン {row['id']} が {stage} で止まっています"
                   + (f"（{days} 日動きなし）" if days is not None else "")
                   + (f"。次: {nxt}" if nxt else ""))
        out.append(make_event("scenario_stalled", project, row["id"], summary,
                              stalled_event_id(project, row["id"], stage),
                              stage=stage, days=days, next=row["next"], project=project,
                              title=row["title"], label=(row["stalled"] or {}).get("label")))
    for row in payload["series"]:
        if not row["stock"]["low"]:
            continue
        slot = row["next_slot"]["at"] if row["next_slot"] else None
        summary = (f"シリーズ {row['id']} の承認済みストックが {row['stock']['approved_future']} 本"
                   f"（目安 {row['stock']['floor']}）"
                   + (f"。次の枠 {slot[:16].replace('T', ' ')}" if slot else ""))
        out.append(make_event("series_low", project, row["id"], summary,
                              series_low_event_id(project, row["id"]),
                              approved_future=row["stock"]["approved_future"], floor=row["stock"]["floor"],
                              next_slot=slot, project=project, title=row["title"], next=row["next"]))
    return out, set(payload["cannot_say"])
