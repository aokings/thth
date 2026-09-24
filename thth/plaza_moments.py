"""広場を読む瞬間を道具が作る（設計 3.8.0 §B）。

材料の報告（r20260924-90aafc42・a51eaafa）: 広場に書き込みはあるのに読み手がいない。
読みに行く手間を利用者に求めるのをやめ、**利用者が必ず通る場所**（observe・approve・
lint・`plaza list`・`ask before-you-post`）に、関係のある書き込みを道具が 1 行で出す。

規律:

  (a) **読める範囲は `plaza.access()` だけが決める**（自分の project・同じ持ち主の組・
      参加していれば open の写し）。組に登録していない project の書き込みは出さない。
  (b) **approve と lint には題と id だけ**（本文は出さない）。承認の 1 段目は「出す
      本文」を読む場所で、他の書き込みの本文を並べると、どれが承認の対象か紛れる。
  (c) observe の 1 件は **1 日 1 件・読んだものは出さない**（`thth/plaza_reads.py`）。
  (d) 選び方は静的（点・goal・topic の重なりの数、同じなら新しい順）。LLM に選ばせない。
"""
from __future__ import annotations

import datetime

from . import accounts, jst

# observe の 1 件の候補にする「新しい書き込み」の窓。
PICK_WINDOW_DAYS = 30
# approve と lint に並べる件数の上限（設計 §B2「1〜3 件」）。
RELATED_MAX = 3
PICK_BASIS = "own_or_owner_level_other_account_unread_within_30d_ranked_by_map_goal_topic_overlap"


def _key(text):
    from . import map_store
    return map_store.node_key(text or "")


def _texts(record, level):
    """書き込みの題と範囲（読む側に見える方）。本文は照合にも使わない。"""
    if level == "open":
        copy = record.get("open_copy") or {}
        return copy.get("title") or "", copy.get("scope_note") or ""
    return record.get("title") or "", record.get("scope_note") or ""


def _title(record, level):
    from . import plaza
    return plaza._preview(_texts(record, level)[0])


# ------------------------------------------------------------ observe の 1 件

def _map_words(projects):
    from . import map_store
    words = []
    for project in sorted(projects):
        try:
            config = map_store.load_config(project)
        except Exception:  # noqa: BLE001 — 地図が読めなくても 1 件は選べる（重なりが減るだけ）
            continue
        words.extend(row["word"] for row in config.get("nodes") or [])
    return words


def _draft_hints(configs, now):
    """これから出す原稿の goal と topic（front-matter だけ・本文は読まない）。"""
    from . import core, goals, queuefile
    found_goals, found_topics = set(), set()
    for name, cfg in configs.items():
        try:
            files = core.list_queue_files(cfg, tree_sha=None)
        except Exception:  # noqa: BLE001 — 原稿が読めなくても 1 件は選べる
            continue
        for qf in files:
            fm = qf.front_matter or {}
            if qf.malformed or fm.get("account") != name or fm.get("post_id"):
                continue
            if fm.get("status") not in ("draft", "approved"):
                continue
            goal = goals.goal_of(qf)
            if goal in goals.GOALS:
                found_goals.add(goal)
            topic = queuefile.normalize_topic(fm.get("topic"))
            if topic:
                found_topics.add(topic)
    return found_goals, found_topics


def _overlap(record, level, words, draft_goals, draft_topics):
    title, note = _texts(record, level)
    text = _key(title + "\n" + note)
    matched = [f"map:{word}" for word in words if _key(word) and _key(word) in text]
    if record.get("goal") in draft_goals:
        matched.append(f"goal:{record['goal']}")
    matched += [f"topic:{topic}" for topic in sorted(draft_topics) if _key(topic) in text]
    return matched


def observe_pick(configs, names, *, now, record=True):
    """observe の 0 段「広場: 同じ持ち主の他の媒体の新しい書き込み 1 件」。

    - 候補: 読める書き込みのうち、同じ project か同じ持ち主の組のもの（open の写しは
      入れない）で、observe の対象の account が書いたものでなく、非表示でなく、
      `PICK_WINDOW_DAYS` 日以内に置かれ、**まだ読んでいない**もの。
    - 並べ方: 自分の地図の点・原稿の goal・原稿の topic との重なりの数、同じなら新しい順。
    - **1 日 1 件**: その日にもう 1 件出していれば出さない（`picked_today`）。
    - `record=True` なら出した 1 件を「読んだ」に控える（id と時刻だけ）。
    """
    from . import plaza, plaza_reads
    by_account = {name: configs[name].get("project") for name in names if name in configs}
    viewer = plaza.Viewer(by_account)
    try:
        records, _broken = plaza.load_all()
        joined = plaza.members()
        state = plaza_reads.load(by_account)
    except plaza.PlazaError as error:
        return {"pick": None, "denominator": None, "reason": None, "cannot_say": str(error)}
    day = jst.to_jst(now).date().isoformat()
    if plaza_reads.picked_on(state, day) is not None:
        return {"pick": None, "denominator": None, "reason": "picked_today", "cannot_say": None}
    read = plaza_reads.read_ids(state)
    floor = now - datetime.timedelta(days=PICK_WINDOW_DAYS)
    candidates = []
    for row in records:
        level = plaza.access(row, viewer, joined)
        if level not in ("own", "owner") or row.get("hidden"):
            continue
        if row.get("account") in by_account or row["plaza_id"] in read:
            continue
        at = jst.parse(row["at"])
        if at is None or not (floor < at <= now):
            continue
        candidates.append((row, level))
    if not candidates:
        return {"pick": None, "denominator": 0, "reason": "no_unread_posts", "cannot_say": None}
    words = _map_words(viewer.projects)
    draft_goals, draft_topics = _draft_hints({n: configs[n] for n in by_account}, now)
    scored = []
    for row, level in candidates:
        matched = _overlap(row, level, words, draft_goals, draft_topics)
        scored.append((len(matched), row["at"], row["plaza_id"], row, level, matched))
    scored.sort(key=lambda item: item[:3], reverse=True)
    _n, _at, _pid, row, level, matched = scored[0]
    first = sorted(by_account)[0]
    pick = {"plaza_id": row["plaza_id"], "title": _title(row, level), "kind": row["kind"],
            "owner": plaza.owner_label(row.get("project"), row.get("account")),
            "medium": row.get("medium"), "goal": row.get("goal"), "at": row["at"],
            "view": level, "matched": matched,
            "command": f"thth plaza show {row['plaza_id']} --as {first}"}
    recorded = None
    if record:
        try:
            plaza_reads.record(by_account, picked=row["plaza_id"], day=day, now=now)
            recorded = True
        except plaza.PlazaError:
            recorded = False
    return {"pick": pick, "denominator": len(candidates), "basis": PICK_BASIS,
            "recorded": recorded, "reason": None, "cannot_say": None}


def pick_line(cell):
    """人向けの 1 行（無ければ None）。本文は出さない（題と id と重なりだけ）。"""
    pick = (cell or {}).get("pick")
    if not pick:
        return None
    why = "・".join(pick["matched"]) if pick["matched"] else "重なりなし（新しい順）"
    return (f"広場の 1 件（同じ持ち主の他の媒体・未読 {cell['denominator']} 件から今日の 1 件）: "
            f"{pick['plaza_id']}  {pick['title']}（{pick['owner']}・{pick.get('medium') or '—'}・"
            f"{why}）→ {pick['command']}")


# ------------------------------------------------------------ approve と lint

def related(account, *, goal=None, topic=None, limit=RELATED_MAX):
    """同じ goal か同じ topic の広場の書き込み（**題と id だけ**・本文は出さない）。

    goal も topic も無ければ None（何も言わない）。読めなければ `cannot_say`。
    その account が書いたものは入れない（自分の書いたものは知っている）。
    """
    from . import goals, plaza, queuefile
    goal = goal if goal in goals.GOALS else None
    topic = queuefile.normalize_topic(topic) if isinstance(topic, str) else None
    if goal is None and not topic:
        return None
    try:
        viewer = plaza.viewer_for_target(account)
        records, _broken = plaza.load_all()
        joined = plaza.members()
    except plaza.PlazaError as error:
        return {"items": None, "cannot_say": str(error)}
    topic_key = _key(topic) if topic else None
    found = []
    for row in records:
        level = plaza.access(row, viewer, joined)
        if level is None or row.get("hidden") or row.get("account") == account:
            continue
        match = []
        if goal is not None and row.get("goal") == goal:
            match.append("goal")
        if topic_key:
            title, note = _texts(row, level)
            if topic_key in _key(title + "\n" + note):
                match.append("topic")
        if match:
            found.append((len(match), row["at"], row["plaza_id"], row, level, match))
    found.sort(key=lambda item: item[:3], reverse=True)
    items = [{"plaza_id": row["plaza_id"], "title": _title(row, level),
              "medium": row.get("medium"), "match": match}
             for _n, _at, _pid, row, level, match in found[:limit]]
    return {"items": items, "n": len(found), "goal": goal, "topic": topic, "cannot_say": None}


def related_lines(cell, indent="  "):
    """approve・lint の人向けの行（題と id だけ）。何も無ければ空。"""
    if not cell:
        return []
    if cell.get("cannot_say"):
        return [f"{indent}広場: 言えない: {cell['cannot_say']}"]
    if not cell["items"]:
        return []
    labels = {"goal": f"goal {cell.get('goal')}", "topic": f"topic {cell.get('topic')}"}
    lines = [f"{indent}広場: 同じ goal・topic の書き込み {len(cell['items'])} 件"
             f"（{cell['n']} 件のうち・題と id だけ）"]
    for item in cell["items"]:
        lines.append(f"{indent}  {item['plaza_id']}  {item['title']}"
                     f"（{item.get('medium') or '—'}・{'・'.join(labels[m] for m in item['match'])}）")
    return lines


def related_for_file(path):
    """lint の 1 本の原稿（front-matter の account・goal・topic だけを読む）。"""
    from . import bundle as bundle_mod, goals, queuefile
    try:
        raw = open(path, encoding="utf-8").read()
    except OSError:
        return None
    try:
        draft = (bundle_mod.parse_text(raw, path) if bundle_mod.is_bundle_text(raw)
                 else queuefile.parse_text(raw, path))
    except Exception:  # noqa: BLE001 — 読めない原稿は lint が断る（ここは何も言わない）
        return None
    fm = draft.front_matter or {}
    account = fm.get("account")
    if not accounts.name_is_safe(account):
        return None
    return related(account, goal=goals.goal_of(draft), topic=fm.get("topic"))


# ------------------------------------------------------------ ask before-you-post

def same_goal(account, goal, *, medium=None):
    """「同じ goal で他の媒体ではこうだった」の 1 行（広場の observed の書き込みがあるときだけ）。

    観測は道具が付けた数字だけ（`plaza.evidence_level_of()` が observed のもの）。
    """
    from . import goals, plaza, plaza_observe
    if goal not in goals.GOALS:
        return None
    try:
        viewer = plaza.viewer_for_target(account)
        records, _broken = plaza.load_all()
        joined = plaza.members()
    except plaza.PlazaError as error:
        return {"line": None, "items": None, "cannot_say": str(error)}
    found = []
    for row in records:
        level = plaza.access(row, viewer, joined)
        if level is None or row.get("hidden") or row.get("goal") != goal:
            continue
        if plaza.evidence_level_of(row) != "observed" or row.get("medium") == medium:
            continue
        found.append((row["at"], row["plaza_id"], row, level))
    if not found:
        return None
    found.sort(key=lambda item: item[:2], reverse=True)
    items = []
    for _at, _pid, row, level in found:
        items.append({"plaza_id": row["plaza_id"], "title": _title(row, level),
                      "medium": row.get("medium"), "numbers": _numbers(row, level)})
    first = items[0]
    numbers = "・".join(first["numbers"]) or "—"
    more = f"（ほか {len(items) - 1} 件）" if len(items) > 1 else ""
    line = (f"広場: 同じ goal（{goal}）で他の媒体では {first.get('medium') or '—'} "
            f"{first['plaza_id']}「{first['title']}」{plaza_observe.LABEL}: {numbers}{more}")
    return {"line": line, "items": items, "n": len(items), "cannot_say": None}


def _numbers(record, level):
    """道具が付けた数字の短い表記（観測の列の差と両群の分母）。"""
    from . import plaza_observe
    out = []
    history = record.get("observations") or []
    if history:
        for column in plaza_observe._columns(history[-1], level):
            if not column.get("observed"):
                continue
            for metric, value in (column.get("metrics") or {}).items():
                diff = value.get("observational_difference")
                if diff is None:
                    continue
                sign = "+" if diff > 0 else ""
                out.append(f"{metric} {sign}{diff}（n={value['baseline_n_eligible']}/"
                           f"{value['changed_n_eligible']}）")
    return out[:3]
