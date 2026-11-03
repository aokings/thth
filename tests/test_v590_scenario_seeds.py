"""5.9.0（設計 効果測定と連動 §3 段 5「戻りの輪」）: `thth scenario seeds <project>`。

見るのは:
- 次の糸の種を、合図ごとの 4 列で並べる（返信の多かった投稿・訪問の多かった告知・よく見られた投稿・閉じた糸の続き）。
  読むだけ・選ばない・数の多い順だけ（`sort_basis`）。投稿は媒体ごと、訪問はサイトごと。
- 柵（設計の「試験で縛る」）:
  - 他人の返信の本文と username が出ない（数だけ）。
  - 窓（`--days`）の外は数えない（投稿・閉じた糸・訪問の控え）。
  - n<5 の比べを言わない（中央値は null・`comparable: false`・表示は「言えない」）。
  - 取り込みが無いときの `cannot_say: site_visits_not_imported`（0 と言わない）。
  - `--json` の形の golden（`tests/fixtures/scenario_seeds_v590_golden.json`）。
- 出力の先頭に skill `thth-flow`、各列の末尾に「種にするなら」の 1 行、`thth scenario <project>` の末尾に seeds の 1 行。
- 何も書かない。
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib

import pytest

from tests.test_v500_scenario import NOW, PROJECT, SECRET_BODY, _sns, _write, kp  # noqa: F401
from tests.test_v570_flow_site_visits import _row, _visits
from thth import accounts as accounts_mod, after_cli, cli, jst, scenario_seeds, site_visits

GOLDEN = pathlib.Path(__file__).resolve().parent / "fixtures" / "scenario_seeds_v590_golden.json"

SEED_THREAD = """---
thth_scenario: 1
kind: thread
id: seed-01
title: 種の見本
project: kp
started: 2026-10-10
---
- promote: threads,x need=1
- boost: ads need=1

- 2026-10-25 closed 見届けた
"""

OLD_THREAD = """---
thth_scenario: 1
kind: thread
id: old-01
title: 古い糸
project: kp
started: 2026-07-01
---
- promote: threads,x need=1

- 2026-08-01 closed 見届けた
"""

# post_id → 24h の反応（after と同じ計算の結果の形）。計算の外の鍵（他人の本文・username）を混ぜても写さない。
METRICS = {
    "P1": {"views_24h": 100, "likes_24h": 4, "replies_back_24h": 3, "covered": True},
    "P2": {"views_24h": 50, "likes_24h": 1, "replies_back_24h": 7, "covered": True},
    "P3": {"views_24h": 400, "likes_24h": 9, "replies_back_24h": 0, "covered": True},
    "P4": {"views_24h": 10, "likes_24h": 0, "replies_back_24h": 2, "covered": True},
    "P5": {"views_24h": 30, "likes_24h": 0, "replies_back_24h": 2, "covered": True},
    "OLD": {"views_24h": 9999, "likes_24h": 99, "replies_back_24h": 99, "covered": True},
    "X1": {"views_24h": 20, "likes_24h": 1, "replies_back_24h": 4, "covered": True,
           "username": "SOMEONE-ELSE", "text": "OTHER-REPLY-TEXT"},
}
POSTS = (  # (ファイル, 口座, 媒体, post_id, 投稿の時刻, 糸)
    ("th-p1.md", "kp-threads", "threads", "P1", "2026-10-20T07:00:00+09:00", "seed-01"),
    ("th-p2.md", "kp-threads", "threads", "P2", "2026-10-21T07:00:00+09:00", None),
    ("th-p3.md", "kp-threads", "threads", "P3", "2026-10-22T07:00:00+09:00", None),
    ("th-p4.md", "kp-threads", "threads", "P4", "2026-10-23T07:00:00+09:00", None),
    ("th-p5.md", "kp-threads", "threads", "P5", "2026-10-24T07:00:00+09:00", None),
    # 窓（28 日・09-30 11:20 から）の外。数が大きくても数えない。
    ("th-old.md", "kp-threads", "threads", "OLD", "2026-09-01T07:00:00+09:00", None),
    ("x-x1.md", "kp-x", "x", "X1", "2026-10-25T07:00:00+09:00", None),
)


@pytest.fixture
def seeds(kp, monkeypatch):  # noqa: F811
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    _write(os.path.join(kp["scen"], "seed-01.md"), SEED_THREAD)
    _write(os.path.join(kp["scen"], "old-01.md"), OLD_THREAD)
    for file, account, media, post_id, at, thread in POSTS:
        _write(os.path.join(kp["queue"], file),
               _sns(account, status="posted", publish_at=at, thread=thread, media=media,
                    extra={"post_id": post_id, "posted_at": at}))
    # 下書き（出していない）は数えない。
    _write(os.path.join(kp["queue"], "th-draft.md"),
           _sns("kp-threads", status="draft", publish_at="2026-11-02T07:00:00+09:00"))
    calls = []

    def lookup(account, **kw):
        calls.append(account)
        return lambda post_id: dict(METRICS.get(post_id) or {"covered": False})
    monkeypatch.setattr(after_cli, "reaction_lookup", lookup)
    kp["lookups"] = calls
    return kp


def _build(**kwargs):
    kwargs.setdefault("now", NOW)
    return scenario_seeds.build(PROJECT, **kwargs)


def _column(payload, key):
    return next(c for c in payload["columns"] if c["key"] == key)


# ------------------------------------------------------------------ 並べ方

def test_4つの列を数の多い順に並べる_媒体ごと(seeds):
    payload = _build()
    # 5.11.0 段 7: クリックの列（Threads・72h）が訪問の列の次に入った（訪問とは別の列）。
    assert [c["key"] for c in payload["columns"]] == ["replies", "visits", "clicks", "views", "closed_threads"]
    assert payload["report_type"] == "scenario_seeds" and payload["schema"] == "thth.scenario/1"
    assert payload["skill"]["name"] == "thth-flow"
    replies = _column(payload, "replies")
    threads = replies["by_medium"]["threads"]
    # 窓の中の Threads 5 本。0 の P3 は並べない（数には入れる）。同じ 2 は新しい順（P5 → P4）。
    assert [r["file"] for r in threads["rows"]] == ["th-p2.md", "th-p1.md", "th-p5.md", "th-p4.md"]
    assert [r["count"] for r in threads["rows"]] == [7, 3, 2, 2]
    assert (threads["posts"], threads["n"], threads["median"], threads["comparable"]) == (5, 5, 2, True)
    assert threads["rows"][1]["thread"] == "seed-01" and threads["rows"][0]["thread"] is None
    assert "多い順" in replies["sort_basis"] and "媒体をまたいで並べない" in replies["sort_basis"]
    # 媒体をまたいで順位を作らない（X は別の群）。
    assert [r["count"] for r in replies["by_medium"]["x"]["rows"]] == [4]
    views = _column(payload, "views")["by_medium"]["threads"]
    assert [r["count"] for r in views["rows"]] == [400, 100, 50, 30, 10]
    assert views["median"] == 50


def test_limitで切り_残りの数を言う(seeds):
    threads = _column(_build(limit=2), "views")["by_medium"]["threads"]
    assert [r["count"] for r in threads["rows"]] == [400, 100] and threads["more"] == 3


def test_閉じた糸の続き_宣言にあって使わなかった媒体(seeds):
    closed = _column(_build(), "closed_threads")
    assert [r["thread"] for r in closed["rows"]] == ["seed-01"]
    row = closed["rows"][0]
    assert row["media_declared_untried"] == ["x"] and row["media_tried"] == ["threads"]
    # 広告の口座が無い（done: null）媒体は「使わなかった」と言わない。
    assert row["media_unknown"] == ["ads"]
    assert row["closed_at"] == "2026-10-25T00:00:00+09:00" and row["title"] == "種の見本"


# ------------------------------------------------------------------ 柵

def test_窓の外は数えない(seeds):
    payload = _build()
    text = json.dumps(payload, ensure_ascii=False)
    # 09-01 の投稿（返信 99・views 9999）と 08-01 に閉じた糸は、どの列にも出ない。
    assert "th-old.md" not in text and "9999" not in text and "old-01" not in text
    assert _column(payload, "replies")["by_medium"]["threads"]["posts"] == 5
    assert payload["window"]["start"] == "2026-09-30T11:20:00+09:00" and payload["window"]["days"] == 28
    # 窓を広げれば数える（窓の判定が効いている裏づけ）。
    wide = _build(days=120)
    assert _column(wide, "replies")["by_medium"]["threads"]["rows"][0]["file"] == "th-old.md"
    assert "old-01" in [r["thread"] for r in _column(wide, "closed_threads")["rows"]]


def test_n5未満の比べを言わない(seeds, capsys):
    payload = _build()
    x = _column(payload, "replies")["by_medium"]["x"]
    assert (x["n"], x["median"], x["comparable"]) == (1, None, False)
    assert "below_min_n:x" in _column(payload, "replies")["cannot_say"]
    assert cli.main(["scenario", "seeds", PROJECT]) == 0
    out = capsys.readouterr().out
    x_line = next(line for line in out.splitlines() if line.strip().startswith("x "))
    assert "中央値と比べは言えない" in x_line and "中央値 " not in x_line
    threads_line = next(line for line in out.splitlines() if line.strip().startswith("threads "))
    assert "中央値 2（n=5）" in threads_line


def test_取り込みが無いとき訪問はcannot_say_0と言わない(seeds, capsys):
    visits = _column(_build(), "visits")
    assert visits["cannot_say"] == ["site_visits_not_imported"] and visits["sites"] == []
    assert visits["as_of"] is None
    assert cli.main(["scenario", "seeds", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "—（site_visits_not_imported）" in out


def test_訪問を取り込めば原稿のパスと糸とsessionsの多い順(seeds, tmp_path):
    path = _visits(tmp_path, [_row("th-p1", 40, engaged=30), _row("th-p1", 5, medium="paid", source="facebook"),
                              _row("th-p2", 60), _row("(organic)", 999), _row("nai-genkou", 500)])
    site_visits.import_file(PROJECT, path, now=NOW)
    visits = _column(_build(), "visits")
    assert visits["cannot_say"] == [] and visits["as_of"] == "2026-10-28T06:00:00+09:00"
    site = visits["sites"][0]
    assert [(r["path"], r["thread"], r["sessions"]) for r in site["rows"]] == [
        ("docs/sns/queue/th-p2.md", None, 60), ("docs/sns/queue/th-p1.md", "seed-01", 45)]
    # 原稿に結べない campaign（(organic)・原稿の無いもの）は並べず、名前も出さない。
    text = json.dumps(visits, ensure_ascii=False)
    assert "999" not in text and "nai-genkou" not in text
    assert (site["manuscripts"], site["comparable"], site["cannot_say"]) == (2, False, "below_min_n")


def test_窓の外の訪問の控えは数えない(seeds, tmp_path):
    path = _visits(tmp_path, [_row("th-p2", 60)], frm="2026-08-01", to="2026-08-31")
    site_visits.import_file(PROJECT, path, now=NOW)
    visits = _column(_build(), "visits")
    assert visits["cannot_say"] == ["outside_window"] and visits["sites"][0]["rows"] == []


def test_他人の返信の本文とusernameが出ない_本物の返信の台帳(kp, monkeypatch, capsys):  # noqa: F811
    """reaction_lookup を差し替えず、返信の台帳（他人の本文と username 入り）から数だけ。"""
    monkeypatch.setattr(jst, "now_jst", lambda: NOW)
    _write(os.path.join(kp["queue"], "th-root.md"),
           _sns("kp-threads", status="posted", publish_at="2026-10-27T08:00:00+09:00",
                extra={"post_id": "ROOT", "posted_at": "2026-10-27T08:00:00+09:00"}))
    cfg = accounts_mod.load_account("kp-threads")
    directory = pathlib.Path(accounts_mod.data_dirs(cfg, "kp-threads")["replies"])
    directory.mkdir(parents=True, exist_ok=True)
    rows = [{"id": "R1", "username": "outside_person", "text": "PRIVATE REPLY", "timestamp": "2026-10-27T20:00:00+09:00",
             "post_id": "ROOT"},
            {"id": "R2", "username": "outside_person", "text": "PRIVATE REPLY", "timestamp": "2026-10-27T21:00:00+09:00",
             "post_id": "ROOT"},
            {"kind": "fetch", "post_id": "ROOT", "collected_at": "2026-10-28T09:00:00+09:00"}]
    (directory / "ROOT.ndjson").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    payload = _build()
    threads = _column(payload, "replies")["by_medium"]["threads"]
    assert [r["count"] for r in threads["rows"]] == [2]
    assert cli.main(["scenario", "seeds", PROJECT, "--json"]) == 0
    assert cli.main(["scenario", "seeds", PROJECT]) == 0
    whole = json.dumps(payload, ensure_ascii=False) + capsys.readouterr().out
    assert "PRIVATE" not in whole and "outside_person" not in whole


def test_計算の外の鍵を写さない(seeds, capsys):
    assert cli.main(["scenario", "seeds", PROJECT, "--json"]) == 0
    assert cli.main(["scenario", "seeds", PROJECT]) == 0
    whole = capsys.readouterr().out
    assert "SOMEONE-ELSE" not in whole and "OTHER-REPLY-TEXT" not in whole


def test_jsonの形のgolden(seeds, capsys):
    assert cli.main(["scenario", "seeds", PROJECT, "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    if os.environ.get("THTH_UPDATE_GOLDEN"):
        GOLDEN.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert payload == json.loads(GOLDEN.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ 先頭と次の一手・CLI

def test_先頭はskillで各列の末尾に種にするならの1行(seeds, capsys):
    assert cli.main(["scenario", "seeds", PROJECT]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("skill: 流れの型は skill thth-flow")
    assert sum(1 for line in lines if line.strip() == scenario_seeds.NEXT_LINE) == 5   # 5.11.0: クリックの列
    payload = _build()
    assert all(c["next"] == scenario_seeds.NEXT_LINE for c in payload["columns"])
    assert all(c["sort_basis"] for c in payload["columns"])


def test_scenarioの一覧の末尾にseedsの1行_showには出さない(seeds, capsys):
    assert cli.main(["scenario", PROJECT]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1].startswith(f"次の糸の種: thth scenario seeds {PROJECT}")
    assert cli.main(["scenario", "show", PROJECT, "seed-01"]) == 0
    assert "次の糸の種" not in capsys.readouterr().out


def test_daysとlimitの範囲の外は断る(seeds, capsys):
    assert cli.main(["scenario", "seeds", PROJECT, "--days", "0", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["seeds_days_invalid"]
    assert cli.main(["scenario", "seeds", PROJECT, "--limit", "0", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["seeds_limit_invalid"]
    assert cli.main(["scenario", "seeds", "no-such-project", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["cannot_say"] == ["target_unknown"]
    # --days は seeds だけ。
    assert cli.main(["scenario", PROJECT, "--days", "7", "--json"]) == 2
    capsys.readouterr()


def test_読むだけ_何も書かない(seeds, thth_root):
    def snapshot():
        out = {}
        for base in (seeds["repo"], thth_root):
            for dirpath, _dirs, files in os.walk(base):
                if ".git" in dirpath.split(os.sep):
                    continue
                for name in files:
                    p = os.path.join(dirpath, name)
                    out[p] = os.path.getmtime(p)
        return out
    before = snapshot()
    _build()
    _build(days=90, limit=1)
    assert snapshot() == before


def test_読めない実測は0と混ぜない(seeds, monkeypatch):
    def broken(account, **kw):
        raise OSError("measured")
    monkeypatch.setattr(after_cli, "reaction_lookup", broken)
    payload = _build()
    assert _column(payload, "replies")["by_medium"] == {}
    assert payload["skipped_counts"] == {"measured_unreadable": 6}
    assert "measured_unreadable" in _column(payload, "views")["cannot_say"]


# ------------------------------------------------------------------ 監査（Fable）のあとに足した柵

SITE_KEYS = {"site", "window", "generated_at", "stale", "rows", "more", "cannot_say", "manuscripts", "n",
             "min_n", "median", "comparable", "untied"}
SITE_ROW_KEYS = {"path", "file", "thread", "sessions", "engaged_sessions"}


def test_seedsだけはusageで断る(seeds, capsys):
    assert cli.main(["scenario", "seeds", "--json"]) == 2
    out = capsys.readouterr()
    assert "usage: thth scenario seeds <project>" in out.err
    assert json.loads(out.out)["cannot_say"][0].startswith("usage: thth scenario seeds")


def test_訪問の列のサイトの節の鍵_結べないcampaignは数だけ_ambiguousの道(seeds, tmp_path, capsys):
    # `queue-th-p1.md`（札なし）と `th-p1.md`（糸 seed-01）は、どちらも campaign `queue-th-p1` を名乗れる → ambiguous。
    _write(os.path.join(seeds["queue"], "queue-th-p1.md"),
           _sns("kp-threads", status="draft", publish_at="2026-11-02T07:00:00+09:00"))
    path = _visits(tmp_path, [_row("th-p2", 60, engaged=20), _row("(organic)", 30),
                              _row("nai-genkou", 5), _row("queue-th-p1", 7)])
    site_visits.import_file(PROJECT, path, now=NOW)
    site = _column(_build(), "visits")["sites"][0]
    assert set(site) == SITE_KEYS
    assert all(set(row) == SITE_ROW_KEYS for row in site["rows"])
    assert [(r["path"], r["sessions"]) for r in site["rows"]] == [("docs/sns/queue/th-p2.md", 60)]
    assert site["untied"] == {"campaigns": 3, "sessions": 42,
                              "by_reason": {"not_a_campaign": 1, "no_manuscript": 1, "ambiguous": 1}}
    text = json.dumps(site, ensure_ascii=False)
    assert "nai-genkou" not in text and "queue-th-p1" not in text and "organic" not in text
    assert cli.main(["scenario", "seeds", PROJECT]) == 0
    out = capsys.readouterr().out
    assert "原稿に結べない campaign 3（訪問 42・名前は出さない）" in out
    assert "--days より広くても控えを丸ごと使う" in out


def test_取れなかった窓の控えはsite_visits_unavailable(seeds, tmp_path, capsys):
    path = _visits(tmp_path, None, reason="quota_exceeded")
    site_visits.import_file(PROJECT, path, now=NOW)
    visits = _column(_build(), "visits")
    assert visits["cannot_say"] == ["site_visits_unavailable"]
    assert visits["sites"][0]["cannot_say"] == "site_visits_unavailable" and visits["sites"][0]["rows"] == []
    assert cli.main(["scenario", "seeds", PROJECT]) == 0
    assert "kopicha.org 2026-10-21〜2026-10-27: —（site_visits_unavailable）" in capsys.readouterr().out


def test_新しい窓が取れなかったら前の窓の数にstaleと表示(seeds, tmp_path, capsys):
    site_visits.import_file(PROJECT, _visits(tmp_path, [_row("th-p2", 60)], name="a.json"), now=NOW)
    site_visits.import_file(PROJECT, _visits(tmp_path, None, reason="quota_exceeded", frm="2026-10-22",
                                             to="2026-10-28", name="b.json"), now=NOW)
    site = _column(_build(), "visits")["sites"][0]
    assert site["stale"] is True and site["window"] == {"from": "2026-10-21", "to": "2026-10-27"}
    assert [r["sessions"] for r in site["rows"]] == [60]
    assert cli.main(["scenario", "seeds", PROJECT]) == 0
    assert "kopicha.org 2026-10-21〜2026-10-27（新しい窓は取れなかった・前の窓の数）" in capsys.readouterr().out


def test_ちょうど窓の始まりの投稿は入る_afterと揃う(seeds):
    at = "2026-09-30T11:20:00+09:00"   # NOW − 28 日ちょうど
    _write(os.path.join(seeds["queue"], "th-edge.md"),
           _sns("kp-threads", status="posted", publish_at=at, extra={"post_id": "P1", "posted_at": at}))
    threads = _column(_build(limit=10), "views")["by_medium"]["threads"]
    assert threads["posts"] == 6 and "th-edge.md" in [r["file"] for r in threads["rows"]]
    one_second_before = "2026-09-30T11:19:59+09:00"
    _write(os.path.join(seeds["queue"], "th-edge.md"),
           _sns("kp-threads", status="posted", publish_at=one_second_before,
                extra={"post_id": "P1", "posted_at": one_second_before}))
    assert _column(_build(), "views")["by_medium"]["threads"]["posts"] == 5

