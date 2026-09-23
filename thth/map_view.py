"""観測の地図の見方（設計 3.5.0 §1 の層・§3）。**読むだけ。**

同じ点に層を重ねる:

  1. 自分の層: その話題（`topic`）で出した投稿の数と、`measured` の views・likes・
     replies の中央値と n。**計算は既存の口のまま**（`measured.load()` と
     `analytics_comparison._population()`——`analytics-report --by topic` と同じ
     24 時間の刻み・同じ `min_n`）。account をまたいで足さない（媒体ごとに並べる）。

  2. 広場の層（3.4.0）: その話題に結んだ施策・気づき・問いの数と判定・追試の数。
     **結び方は語の一致**（書き込みの title か scope_note〔媒体・企画の範囲〕に点の語が
     含まれる）。広場の記録に欄を足さない（3.4.0 で置いた書き込みもそのまま結べる）。
     読めるのは `plaza.access()` が許す範囲だけ（自分の持ち主の書き込みと、参加して
     いれば他の持ち主の open の写し）。**地図から広場へは何も渡さない**（一方向）。

数には分母、取れないものは null と静的な理由。
"""
from __future__ import annotations

import os

from . import accounts, after_cli, analytics_comparison, jst, map_store, measured, read_window
from . import plaza as plaza_mod

# 自分の層で並べる指標（設計 §1「views・likes・replies の中央値と n」）。
SELF_METRICS = ("views", "likes", "replies")
SELF_MIN_N = after_cli.DEFAULT_MIN_N


def _topic_key(value):
    topic, valid = after_cli._normalized_topic(value)
    if not valid or not topic:
        return None
    return map_store.node_key(topic)


def self_layer(configs, words, *, since, now, min_n=SELF_MIN_N):
    """点ごとの自分の層 `{word: {"by_account": {name: 升目}}}`（読むだけ）。

    - `posts`: 窓（`since`〜`now`）の中でその topic で出した投稿の数。分母
      `denominator` は同じ窓の投稿の数（topic を問わない）。
    - `metrics`: 根の投稿の 24 時間の値の中央値と n（`analytics-report` と同じ母集団）。
      n が `min_n` に届かなければ中央値は null・理由 `below_min_n`。
    """
    keys = {word: map_store.node_key(word) for word in words}
    layer = {word: {"by_account": {}} for word in words}
    for name in sorted(configs):
        medium = configs[name].get("media")
        try:
            loaded = measured.load(name, observation_metadata=True)
        except (accounts.AccountError, TypeError, ValueError, AttributeError, OSError):
            for word in words:
                layer[word]["by_account"][name] = {"medium": medium, "posts": None,
                                                   "denominator": None, "metrics": None,
                                                   "cannot_say": "measured_unreadable"}
            continue
        in_window = []
        for post in loaded["posts"]:
            posted = analytics_comparison._timestamp(post.get("posted_at"))
            if posted is None or not since <= posted <= now:
                continue
            in_window.append((str(post["post_id"]), posted, post))
        incomplete = bool(loaded.get("broken"))
        from . import click_attribution, goals as goals_mod
        recorded = goals_mod.recorded_goals(name)
        # click を投稿単位で（設計 3.7.0 §A1）。共有かどうかは account の投稿の全部と比べる。
        click_index = click_attribution.Index.for_account(
            name, configs[name],
            posts=[(str(p["post_id"]), analytics_comparison._timestamp(p.get("posted_at")))
                   for p in loaded["posts"]],
            account_daily=loaded.get("account_daily"), now=now)
        for word in words:
            members = [item for item in in_window if _topic_key(item[2].get("topic")) == keys[word]]
            roots = [item for item in members if analytics_comparison._root_exclusion(item[2]) is None]
            population = analytics_comparison._population(roots, since, now, now, min_n)
            metrics = {}
            for metric in SELF_METRICS:
                stat = population["metrics"][metric]
                metrics[metric] = {"median": stat["median"], "n": stat["n_eligible"],
                                   "denominator": stat["n_total"],
                                   "reason": None if stat["median"] is not None else "below_min_n"}
            layer[word]["by_account"][name] = {
                "medium": medium, "posts": len(members), "denominator": len(in_window),
                "root_posts": population["n_total"], "metrics": metrics,
                # 点×目的の表（設計 3.6.0 §A2）。目的ごとの本数と主な物差しの中央値。
                "by_goal": goal_table(name, medium, members, recorded, since=since, now=now,
                                      min_n=min_n, click_index=click_index),
                "basis": {"source": "measured", "mark_hours": 24, "min_n": min_n,
                          "population": "root_posts"},
                "incomplete_sources": incomplete,
                "cannot_say": "measured_partly_unreadable" if incomplete else None}
    return layer


def goal_table(name, medium, members, recorded, *, since, now, min_n, click_index=None):
    """その点の投稿を目的ごとに数え、主な物差しの中央値を 1 つ（設計 3.6.0 §A2）。

    目的は公開の時点の記録（`goals.recorded_goals()`）。reach と reply は根の投稿の
    24 時間の値（`analytics-report --by goal` と同じ計算）、follow は投稿単位の数字が
    一次資料に無いので中央値を出さず `cannot_say` を言う（割らない）。click は
    一意のリンク先の投稿だけ、投稿から 72 時間のクリックとクリック率（設計 3.7.0
    §A1・`click_attribution`）。言えない投稿は理由ごとの本数（`reasons`）。
    """
    from . import analytics_goals, goals as goals_mod
    table = {}
    for goal in goals_mod.LAYERS:
        chosen = [item for item in members if goals_mod.goal_for(recorded, item[0]) == goal]
        roots = [item for item in chosen if analytics_comparison._root_exclusion(item[2]) is None]
        row = {"posts": len(chosen), "root_posts": len(roots), "primary": None, "cannot_say": None}
        if goal == "click" and click_index is not None:
            rows = []
            for post_id, posted, post in chosen:
                observation, _rejected = analytics_comparison._observation(post, posted, now, 24)
                views = ((observation or {}).get("metrics") or {}).get("views")
                rows.append(click_index.attribute(post_id, posted, views))
            from . import click_attribution
            summary = click_attribution.summarize(rows, min_n)
            stat, rate = summary["clicks_72h"], summary["click_rate"]
            row["primary"] = {"metric": "clicks_72h", "median": stat["median"],
                              "n": stat["n_eligible"], "denominator": stat["n_total"],
                              "reason": stat["reason"], "basis": summary["basis"],
                              "window": summary["window"]}
            row["click_rate"] = {"median": rate["median"], "n": rate["n_eligible"],
                                 "denominator": rate["n_total"], "reason": rate["reason"],
                                 "rate_basis": rate["rate_basis"]}
            row["reasons"] = summary["reasons"]
        elif goal in goals_mod.PER_POST_CANNOT_SAY:
            row["cannot_say"] = goals_mod.PER_POST_CANNOT_SAY[goal]
        elif goal == "reach":
            yard = analytics_goals.yardstick("reach", name=name, medium=medium, members=roots,
                                             goal_days={}, daily={}, start=since, end=now,
                                             now=now, min_n=min_n)
            stat = yard["by_mark"]["24"]
            row["primary"] = {"metric": f"{yard['metric']}_24h", "median": stat["median"],
                              "n": stat["n_eligible"], "denominator": stat["n_total"],
                              "reason": stat["reason"]}
        elif goal == "reply":
            values = []
            for post_id, posted, post in roots:
                if since <= posted < now:
                    observation, _rejected = analytics_comparison._observation(post, posted, now, 24)
                    value = ((observation or {}).get("metrics") or {}).get("replies")
                    if value is not None:
                        values.append(value)
            median = analytics_comparison._median(values) if len(values) >= min_n else None
            row["primary"] = {"metric": "replies_24h", "median": median, "n": len(values),
                              "denominator": len(roots),
                              "reason": None if median is not None else "below_min_n"}
        table[goal] = row
    return table


def goal_line(table) -> str | None:
    """地図の 1 行（本数のある目的だけ）。無ければ None。"""
    parts = []
    for goal, row in (table or {}).items():
        if not row["posts"]:
            continue
        if row["cannot_say"]:
            parts.append(f"{goal} {row['posts']} 本（言えない: {row['cannot_say']}）")
        elif row["primary"]:
            stat = row["primary"]
            rate = row.get("click_rate")
            extra = (f"・クリック率 中央値 {_num(rate['median'])}（{rate['rate_basis']}）"
                     if rate else "")
            parts.append(f"{goal} {row['posts']} 本（{stat['metric']} 中央値 "
                         f"{_num(stat['median'])}・n={stat['n']}/{stat['denominator']}{extra}）")
        else:
            parts.append(f"{goal} {row['posts']} 本")
    return "目的: " + "・".join(parts) if parts else None


# ---------------------------------------------------------------- 広場の層

PLAZA_LINK_BASIS = "title_or_scope_note_contains_word"
# 1 つの点に並べる書き込みの id の上限（新しい順）。数は全部数える。
PLAZA_RECENT = 5


def _plaza_counts():
    return {"measure": 0, "finding": 0, "question": 0, "denominator": 0,
            "verdicts": {"adopted": 0, "dropped": 0, "inconclusive": 0, "none": 0},
            "trials": {"reproduced": 0, "not_reproduced": 0, "not_tried": 0, "denominator": 0},
            "recent": []}


def plaza_layer(viewer, words, *, since=None, now=None):
    """点ごとの広場の層 `{word: {"own": 升目, "open": 升目}}`（読むだけ）。

    `own` は自分の持ち主（viewer の project）の書き込み、`open` は参加した他の持ち主の
    open の写し（不参加なら null・理由 `plaza_not_joined`）。分母 `denominator` は
    その範囲で読める書き込みの数（語を問わない）。非表示の書き込みは数えない。
    `since` があれば置かれた時刻がそれより後のものだけ。
    """
    try:
        records, _broken = plaza_mod.load_all()
        joined = plaza_mod.members()
    except plaza_mod.PlazaError:
        return {word: {"own": None, "open": None, "link_basis": PLAZA_LINK_BASIS,
                       "cannot_say": "plaza_store_unavailable"} for word in words}
    participating = viewer.admin or bool(viewer.projects & joined)
    keys = {word: map_store.node_key(word) for word in words}
    layer = {word: {"own": _plaza_counts(), "open": _plaza_counts() if participating else None,
                    "open_reason": None if participating else "plaza_not_joined",
                    "link_basis": PLAZA_LINK_BASIS, "cannot_say": None} for word in words}
    totals = {"own": 0, "open": 0}
    readable = []
    for record in records:
        if record.get("hidden"):
            continue
        at = analytics_comparison._timestamp(record.get("at"))
        if since is not None and (at is None or at < since or (now is not None and at > now)):
            continue
        level = plaza_mod.access(record, viewer, joined)
        if level is None:
            continue
        if level == "own":
            scope, title, note = "own", record.get("title"), record.get("scope_note")
        else:
            copy = record.get("open_copy") or {}
            scope, title, note = "open", copy.get("title"), copy.get("scope_note")
        totals[scope] += 1
        readable.append((record, scope, map_store.node_key(f"{title or ''}\n{note or ''}")))
    readable.sort(key=lambda item: (item[0]["at"], item[0]["plaza_id"]), reverse=True)
    for word in words:
        for scope in ("own", "open"):
            if layer[word][scope] is not None:
                layer[word][scope]["denominator"] = totals[scope]
        for record, scope, text in readable:
            cell = layer[word][scope]
            if cell is None or keys[word] not in text:
                continue
            cell[record["kind"]] += 1
            verdict = (record.get("verdict") or {}).get("verdict")
            if record["kind"] == "measure":
                cell["verdicts"][verdict if verdict in plaza_mod.VERDICTS else "none"] += 1
            trials = plaza_mod.trial_counts(record)
            for result in plaza_mod.TRIAL_RESULTS:
                cell["trials"][result] += trials[result]
            cell["trials"]["denominator"] += trials["denominator"]
            if len(cell["recent"]) < PLAZA_RECENT:
                cell["recent"].append(record["plaza_id"])
    return layer


# ------------------------------------------------------------------ 世間の層

WORLD_ENV = "THTH_MAP_WORLD"


def world_enabled() -> bool:
    """世間の層が有効か（**管理者が `THTH_MAP_WORLD=1` を入れたときだけ**・設計 §4・照合 §6-9）。

    有効にする前提（照合 §6-9・§6-8）: Meta の Dashboard の説明と privacy ページを実態に
    合わせたあと。外部の LLM に渡る口（MCP・observe）に出すなら Service Provider の条件も。
    既定は無効で、無効のあいだは集めない・見せない（`world_layer_disabled`）。
    """
    return os.environ.get(WORLD_ENV) == "1"


def _world_cell(project, words, *, since, now, edge_words=None):
    """点ごとの世間の層と共起の線。無効なら全部 null と `world_layer_disabled`。"""
    if not world_enabled():
        return ({word: {"cannot_say": "world_layer_disabled", "by_medium": None} for word in words},
                [], "world_layer_disabled")
    from . import map_world
    cells, co, _broken = map_world.view(project, words, since=since, now=now,
                                        edge_words=edge_words)
    return cells, co, None


# ------------------------------------------------------------------ map show

DEFAULT_SINCE = "30d"


def _viewer_for(configs, project):
    return plaza_mod.Viewer({name: project for name in configs})


def show(target, *, since=DEFAULT_SINCE, node=None, now=None, allowed=None):
    """`thth map show <project>`・MCP `thth_map_show` の答え（読むだけ）。

    `allowed`（サーバ型の credential が許した account → project）があれば、その中の
    project だけ・その中の account だけを読む（**世間の層は project の中だけ**・照合 §6-7:
    他の持ち主・広場の open の参加者・横断集計には出さない）。
    """
    now = now if now is not None else jst.now_jst()
    try:
        floor = read_window.cutoff(since, now=now)
    except ValueError:
        raise map_store.MapError("invalid_since") from None
    if floor is None or floor > now:
        raise map_store.MapError("invalid_since")
    if allowed is not None:
        readable = {value for value in allowed.values() if value}
        try:
            project, found = map_store.project_accounts(target)
        except map_store.MapError:
            # 無い project と読めない project は同じ断り（在ることを漏らさない）。
            raise map_store.MapError("scope_unavailable") from None
        # **世間の層は project の中だけ**（照合 §6-7）。広場の open に参加している他の
        # 持ち主にも、横断の集計にも出さない——読めるのは credential の project だけ。
        if project not in readable:
            raise map_store.MapError("scope_unavailable")
        configs = {name: cfg for name, cfg in found.items() if name in allowed}
    else:
        project, configs = map_store.project_accounts(target)
    config = map_store.load_config(project)
    words = [row["word"] for row in config["nodes"]]
    edges = [{"narrower": row["narrower"], "broader": row["broader"], "kind": "broader"}
             for row in config["edges"]]
    neighbors = None
    if node is not None:
        chosen = next((w for w in words if map_store.node_key(w) == map_store.node_key(
            str(node).strip().lstrip("#＃").strip())), None)
        if chosen is None:
            raise map_store.MapError("node_not_found")
        broader = [e["broader"] for e in edges if e["narrower"] == chosen]
        narrower = [e["narrower"] for e in edges if e["broader"] == chosen]
        neighbors = {"node": chosen, "broader": broader, "narrower": narrower, "co": []}
        words = [chosen] + [w for w in words if w in broader or w in narrower]
        edges = [e for e in edges if chosen in (e["narrower"], e["broader"])]
    selfs = self_layer(configs, words, since=floor, now=now)
    plazas = plaza_layer(_viewer_for(configs, project), words, since=floor, now=now)
    worlds, co_edges, world_reason = _world_cell(
        project, words, since=floor, now=now,
        edge_words=[row["word"] for row in config["nodes"]])
    if neighbors is not None:
        co_edges = [e for e in co_edges if neighbors["node"] in e["edge"]]
        neighbors["co"] = co_edges
    nodes = [{"word": word, "self": selfs[word], "plaza": plazas[word], "world": worlds[word]}
             for word in words]
    return {
        "schema_version": map_store.SCHEMA_VERSION, "report_type": "map_show",
        "project": project, "generated_at": jst.iso(now),
        "window": {"since": jst.iso(floor), "until": jst.iso(now)},
        "accounts": sorted(configs), "nodes": nodes,
        "edges": {"broader": edges, "co": co_edges}, "neighbors": neighbors,
        "world_layer": {"enabled": world_enabled(), "cannot_say": world_reason},
        "limits": {"n_nodes": len(config["nodes"]), "max_nodes": map_store.MAX_NODES,
                   "n_edges": len(config["edges"]), "retention_days": config["retention_days"]},
        "cannot_say": [] if config["nodes"] else ["no_map_nodes"],
        "notes": [
            "点と包含の線は人が足したものだけ（thth admin map node|edge）。道具は語を選ばない",
            "自分の層は analytics-report --by topic と同じ計算（24 時間の刻み・根の投稿・min_n 5）。"
            "account をまたいで足さない",
            "広場の層は title か scope_note に点の語を含む書き込み（語の一致）",
            "世間の層は管理者が有効にしたときだけ（既定は無効）。project の外には出さない",
        ],
    }


# ------------------------------------------------------------------ 人向け

def _num(value):
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(round(value, 3)) if isinstance(value, float) else str(value)


def render(payload, out=print) -> None:
    limits = payload["limits"]
    out(f"地図  {payload['project']}  （{payload['window']['since'][:10]}〜"
        f"{payload['window']['until'][:10]}・点 {limits['n_nodes']}/{limits['max_nodes']}・"
        f"線 {limits['n_edges']}・保持 {limits['retention_days']} 日）")
    world = payload["world_layer"]
    out("世間の層: " + ("有効" if world["enabled"] else "無効") +
        (f"（{world['cannot_say']}）" if world["cannot_say"] else ""))
    if not payload["nodes"]:
        out("点がありません（thth admin map node add <project> <語> --by <名前>）")
        return
    broader = payload["edges"]["broader"]
    for node in payload["nodes"]:
        word = node["word"]
        out("")
        out(f"[{word}]")
        ups = [e["broader"] for e in broader if e["narrower"] == word]
        downs = [e["narrower"] for e in broader if e["broader"] == word]
        if ups or downs:
            out("  線: " + "・".join([f"⊂ {w}" for w in ups] + [f"⊃ {w}" for w in downs]))
        for name, cell in node["self"]["by_account"].items():
            if cell.get("cannot_say") == "measured_unreadable":
                out(f"  自分 {name}（{cell['medium']}）: 言えない: measured_unreadable")
                continue
            parts = []
            for metric in SELF_METRICS:
                stat = cell["metrics"][metric]
                parts.append(f"{metric} 中央値 {_num(stat['median'])}（n={stat['n']}）")
            out(f"  自分 {name}（{cell['medium']}）: 投稿 {cell['posts']}/{cell['denominator']}  "
                + "・".join(parts))
            line = goal_line(cell.get("by_goal"))
            if line:
                out(f"    {line}")
        plaza_cell = node["plaza"]
        if plaza_cell.get("cannot_say"):
            out(f"  広場: 言えない: {plaza_cell['cannot_say']}")
        else:
            for scope, label in (("own", "広場（自分の持ち主）"), ("open", "広場（open）")):
                cell = plaza_cell[scope]
                if cell is None:
                    if scope == "open":
                        continue
                    out(f"  {label}: —")
                    continue
                trials = cell["trials"]
                verdicts = cell["verdicts"]
                out(f"  {label}: 施策 {cell['measure']}・気づき {cell['finding']}・問い "
                    f"{cell['question']}（分母 {cell['denominator']}）  判定 採用 "
                    f"{verdicts['adopted']}・取りやめ {verdicts['dropped']}・保留 "
                    f"{verdicts['inconclusive']}・未判定 {verdicts['none']}  追試 再現 "
                    f"{trials['reproduced']}・再現せず {trials['not_reproduced']}・未試行 "
                    f"{trials['not_tried']}（分母 {trials['denominator']}）")
                if cell["recent"]:
                    out("    " + "・".join(cell["recent"]))
        world_cell = node["world"]
        if world_cell.get("by_medium") is None:
            out(f"  世間: 言えない: {world_cell['cannot_say']}")
        else:
            for medium, cell in world_cell["by_medium"].items():
                latest = cell.get("latest") or {}
                out(f"  世間 {medium}（{cell['label']}）: {latest.get('date') or '—'}  件数 "
                    f"{_num(latest.get('n'))}/{_num(latest.get('requested'))}  異なり "
                    f"{_num(latest.get('distinct'))}  上位 3 の占有率 "
                    f"{_num(latest.get('top3_share'))}  直近 {latest.get('latest_age_bucket') or '—'}"
                    + (f"  理由 {latest['reason']}" if latest.get("reason") else ""))
    if payload["edges"]["co"]:
        out("")
        out("共起（強い順）:")
        for edge in payload["edges"]["co"][:10]:
            out(f"  {edge['edge'][0]}—{edge['edge'][1]}（{edge['medium']}）: "
                f"{_num(edge['co'])}/{_num(edge['denominator'])}  {edge['date']}")


# ------------------------------------------------------------------ observe の 1 行

# 伸びた点・強まった線を比べる窓（直近の日と、その前の最大 7 日の平均）。
OBSERVE_BASE_DAYS = 7


def observe_summary(project, *, now=None):
    """`thth observe` の 0 段の 1 行「地図: 伸びた点・強まった線」（**候補の列挙だけ**）。

    - 伸びた点: 媒体ごとの件数 n が、直近の日で前の最大 7 日の平均より最も伸びた点。
    - 強まった線: 共起の割合（co／分母）が、直近の日で前の平均より最も上がった線。
    どちらも世間の層の行だけから決める（無効なら null と `world_layer_disabled`）。
    本文は作らない。点と線の数は層を問わず出す。
    """
    now = now or jst.now_jst()
    cell = {"project": project, "n_nodes": None, "n_edges": None, "grown_node": None,
            "strengthened_edge": None, "cannot_say": None}
    try:
        config = map_store.load_config(project)
    except map_store.MapError:
        cell["cannot_say"] = "map_store_unavailable"
        return cell
    cell.update(n_nodes=len(config["nodes"]), n_edges=len(config["edges"]))
    if not config["nodes"]:
        cell["cannot_say"] = "no_map_nodes"
        return cell
    if not world_enabled():
        cell["cannot_say"] = "world_layer_disabled"
        return cell
    import datetime
    from . import map_world
    words = [row["word"] for row in config["nodes"]]
    since = now - datetime.timedelta(days=OBSERVE_BASE_DAYS + 1)
    try:
        cells, co, _broken = map_world.view(project, words, since=since, now=now)
    except map_store.MapError:
        cell["cannot_say"] = "map_store_unavailable"
        return cell
    best = None
    for word, node in cells.items():
        for medium, series in (node["by_medium"] or {}).items():
            values = [day for day in series["days"] if day["n"] is not None]
            if len(values) < 2:
                continue
            latest, before = values[-1], values[:-1][-OBSERVE_BASE_DAYS:]
            base = sum(day["n"] for day in before) / len(before)
            if base <= 0 or latest["n"] <= base:
                continue
            change = (latest["n"] - base) / base
            if best is None or change > best[0]:
                best = (change, {"node": word, "medium": medium, "date": latest["date"],
                                 "n": latest["n"], "base": round(base, 1),
                                 "change_pct": round(change * 100)})
    cell["grown_node"] = best[1] if best else None
    strongest = max((edge for edge in co if edge["change"] is not None and edge["change"] > 0),
                    key=lambda edge: edge["change"], default=None)
    if strongest is not None:
        cell["strengthened_edge"] = {"edge": strongest["edge"], "medium": strongest["medium"],
                                     "date": strongest["date"], "ratio": round(strongest["ratio"], 3),
                                     "change": round(strongest["change"], 3)}
    if best is None and strongest is None:
        cell["cannot_say"] = "no_change"
    return cell


def observe_text(cell):
    """0 段に出す 1 行（本文は作らない・数と語だけ）。"""
    head = f"地図 {cell['project']}: "
    if cell["cannot_say"] in ("map_store_unavailable",):
        return head + f"言えない: {cell['cannot_say']}"
    size = f"点 {cell['n_nodes']}・線 {cell['n_edges']}"
    parts = []
    grown = cell.get("grown_node")
    if grown:
        parts.append(f"伸びた点 {grown['node']}（+{grown['change_pct']}%・{grown['medium']}・"
                     f"{grown['n']}／前 {grown['base']}）")
    edge = cell.get("strengthened_edge")
    if edge:
        parts.append(f"強まった線 {edge['edge'][0]}—{edge['edge'][1]}（{edge['medium']}・"
                     f"割合 {edge['ratio']}）")
    if parts:
        return head + "・".join(parts) + f"（{size}）"
    return head + size + (f"（{cell['cannot_say']}）" if cell["cannot_say"] else "")
