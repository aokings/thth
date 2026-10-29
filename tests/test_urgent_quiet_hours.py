"""緊急の札（`urgent: true`）は静かな時間帯だけを越える（masaru 10/30・依頼 docs/依頼_運用から開発へ_
緊急の原稿は静かな時間帯を越える_2026-10-30.md）。

- 札の無い原稿は今までどおり静かな時間帯で落ちる
- 札の付いた原稿は静かな時間帯でも選ばれる。最短間隔・本数（max_per_run）・`--now` の振る舞いは今のまま
- 札は承認の指紋に入る（承認のあとに付けたら出ない）。札の無い原稿の指紋は変わらない（版も上げない）
- 越えて出たら sent の記録・runs・`thth events` に残る。`thth queue`・`thth schedule` に札が見える
- lint は真偽値以外・はてなの配信原稿・連投の札を断る
"""
from __future__ import annotations

import datetime
import os

from tests.conftest import make_queue_text, parse_verified
from tests.test_fake_api import _adapter, fake_threads_server
from tests.test_max_per_run import _init_git_pair_multi
from thth import accounts as accounts_mod
from thth import approval, cli, core, events_cli, fingerprint_golden, lint, queuefile
from thth import report as report_mod
from thth import runs as runs_mod
from thth import select as select_mod
from thth import sent as sent_mod

QUIET = ["22:00", "06:00"]
NIGHT = datetime.datetime.fromisoformat("2026-10-30T03:00:00+09:00")
DAY = datetime.datetime.fromisoformat("2026-10-30T10:00:00+09:00")
CFG = {"media": "threads", "quiet_hours": QUIET, "min_interval_hours": 2, "stale_days": 7, "hashtags": False}
EARLIER = "2026-10-30T02:00:00+09:00"


def _write(dir_path, name, text):
    path = os.path.join(str(dir_path), name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _select(files, now=NIGHT, last_post_at=None, bypass_pace=False):
    return select_mod.select_one(files, account_name="nigamilab-threads", account_cfg=CFG, now=now,
                                 last_post_at=last_post_at, recent_texts=set(), bypass_pace=bypass_pace)


def _reasons(result):
    return sorted((os.path.basename(r.file), r.reason) for r in result.rejections)


def _qf(tmp_path, name, fm=None, body=None):
    text = make_queue_text({"publish_at": EARLIER, **(fm or {})},
                           body=body or f"## threads\n\n{name} の本文\n")
    return parse_verified(_write(tmp_path, name, text))


# ------------------------------------------------------------------ 指紋

def test_札の無い原稿の指紋は変わらず版も上げない():
    assert approval.FINGERPRINT_VERSION == 2
    assert fingerprint_golden.drift() == []
    base = dict(section="本文です。", account="nigamilab-threads", reply_to=None, topic=None,
                publish_at="2026-10-30T08:00:00+09:00")
    plain = approval.compute_approved_sha(**base)
    assert approval.compute_approved_sha(**base, urgent=False) == plain
    # front-matter から: 札なし・`urgent: false`・空は札なしと同じ指紋。
    for fm in ({}, {"urgent": "false"}, {"urgent": "FALSE"}, {"urgent": None}):
        assert approval.compute_approved_sha(**base, **approval.publish_options(fm)) == plain
    marked = approval.compute_approved_sha(**base, **approval.publish_options({"urgent": "true"}))
    assert marked != plain
    assert approval.compute_approved_components(**base, urgent=True)["urgent"] == "true"
    assert "urgent" in approval.COMPARED_KEYS


def test_承認のあとに札を付けると承認が古くなり出ない(tmp_path):
    text = make_queue_text({"publish_at": EARLIER})
    assert "urgent" not in text
    added = text.replace("account: nigamilab-threads", "account: nigamilab-threads\nurgent: true", 1)
    assert "urgent: true" in added
    qf = parse_verified(_write(tmp_path, "a.md", added))
    assert queuefile.is_urgent(qf.front_matter)
    result = _select([qf])
    assert result.chosen is None
    assert _reasons(result) == [("a.md", "approval_stale")]
    assert qf.path in result.needs_review
    # 昼でも同じ（静かな時間帯の話ではなく承認の話）。
    assert _reasons(_select([qf], now=DAY)) == [("a.md", "approval_stale")]


def test_承認のあとに札を外しても承認が古くなる(tmp_path):
    text = make_queue_text({"publish_at": EARLIER, "urgent": "true"})
    removed = text.replace("urgent: true\n", "")
    qf = parse_verified(_write(tmp_path, "a.md", removed))
    assert _reasons(_select([qf], now=DAY)) == [("a.md", "approval_stale")]


# ------------------------------------------------------------------ select

def test_札が無いと静かな時間帯で落ちる(tmp_path):
    qf = _qf(tmp_path, "a.md")
    result = _select([qf])
    assert result.chosen is None and result.crossed_quiet_hours is False
    assert _reasons(result) == [("a.md", "quiet_hours")]
    assert result.needs_review == []
    # 昼は出る（札なしの今までの道）。
    day = _select([qf], now=DAY)
    assert day.chosen is qf and day.crossed_quiet_hours is False


def test_札があれば静かな時間帯でも選ぶ_札の無い原稿は落ちたまま(tmp_path):
    plain = _qf(tmp_path, "a.md")
    urgent = _qf(tmp_path, "b.md", {"urgent": "true", "publish_at": "2026-10-30T02:30:00+09:00"})
    result = _select([plain, urgent])
    assert result.chosen is urgent
    assert result.crossed_quiet_hours is True
    assert _reasons(result) == [("a.md", "quiet_hours")]


def test_札があっても最短間隔は守る(tmp_path):
    urgent = _qf(tmp_path, "b.md", {"urgent": "true"})
    last = NIGHT - datetime.timedelta(minutes=30)   # min_interval_hours 2 の内
    result = _select([urgent], last_post_at=last)
    assert result.chosen is None and result.crossed_quiet_hours is False
    assert _reasons(result) == [("b.md", "min_interval")]
    # 間隔が空いていれば出る。
    ok = _select([urgent], last_post_at=NIGHT - datetime.timedelta(hours=3))
    assert ok.chosen is urgent and ok.crossed_quiet_hours is True


def test_札があっても未来の予約と承認なしは出ない(tmp_path):
    future = _qf(tmp_path, "f.md", {"urgent": "true", "publish_at": "2026-10-30T05:00:00+09:00"})
    draft = _qf(tmp_path, "d.md", {"urgent": "true", "status": "draft"})
    result = _select([future, draft])
    assert result.chosen is None
    assert dict(_reasons(result))["f.md"] == "future"
    assert dict(_reasons(result))["d.md"] != "quiet_hours"


def test_昼の札は越えたことにしない(tmp_path):
    urgent = _qf(tmp_path, "b.md", {"urgent": "true"})
    result = _select([urgent], now=DAY)
    assert result.chosen is urgent and result.crossed_quiet_hours is False


def test_throw_nowの振る舞いは変えない(tmp_path):
    plain = _qf(tmp_path, "a.md")
    urgent = _qf(tmp_path, "b.md", {"urgent": "true", "publish_at": "2026-10-30T02:30:00+09:00"})
    last = NIGHT - datetime.timedelta(minutes=10)
    result = _select([plain, urgent], last_post_at=last, bypass_pace=True)
    # `--now` は静かな時間帯・最短間隔を無視して publish_at の早い順（札は関係しない・越えた印も付けない）。
    assert result.chosen is plain and result.crossed_quiet_hours is False


def test_札が真偽値でなければ要確認で出さない(tmp_path):
    qf = _qf(tmp_path, "a.md", {"urgent": "yes"})
    assert not queuefile.is_urgent(qf.front_matter)
    result = _select([qf], now=DAY)
    assert result.chosen is None
    assert _reasons(result) == [("a.md", queuefile.URGENT_INVALID)]
    assert qf.path in result.needs_review


def test_はてなの口座では札を見ない():
    # 静かな時間帯の関門だけを直に当てる（はてなの指紋は札を含まないので、札で越えさせない）。
    qf = queuefile.parse_text(make_queue_text({"publish_at": EARLIER, "urgent": "true"}), "/x/a.md")
    publish_at = queuefile.parse_publish_at(EARLIER)
    cfg = dict(CFG, media="hatenablog", principal="p", blog_id="b.hatenablog.com")
    from thth import hatena_entry
    assert hatena_entry.is_hatena(cfg)
    result = select_mod._apply_timing_gate(
        [(qf, publish_at, None, None)], rejections=[], type_mismatch=[], needs_review=[],
        account_cfg=cfg, now=NIGHT, last_post_at=None, bypass_pace=False)
    assert result.chosen is None
    assert [r.reason for r in result.rejections] == ["quiet_hours"]


# ------------------------------------------------------------------ lint

def _lint(tmp_path, fm):
    path = _write(tmp_path, "l.md", make_queue_text({"status": "draft", **fm}))
    return lint.lint_file(path)


def test_lintは真偽値以外を断る(tmp_path):
    assert not any(m.startswith("urgent") for m in _lint(tmp_path, {}))
    assert not any(m.startswith("urgent") for m in _lint(tmp_path, {"urgent": "true"}))
    assert not any(m.startswith("urgent") for m in _lint(tmp_path, {"urgent": "false"}))
    for bad in ("yes", "1", "緊急"):
        problems = _lint(tmp_path, {"urgent": bad})
        assert any(m.startswith("urgent: true か false") for m in problems), bad
        assert not lint.is_warning(next(m for m in problems if m.startswith("urgent")))
        assert lint.reason_code(problems) == "invalid_urgent"
    assert "invalid_urgent" in lint.REASONS


def test_lintは連投の札を断る():
    assert lint.urgent_errors({"urgent": "true"}, where="連投（thth: 2）")[0].startswith("urgent: 連投")
    assert lint.urgent_errors({}, where="連投（thth: 2）") == []


# ------------------------------------------------------------------ 通しで（throw・記録・表示）

def _account(isolated_account_factory, tmp_path, files, **kw):
    pair = _init_git_pair_multi(tmp_path, files)
    account = isolated_account_factory(repo_dir=pair["work"], production=True,
                                       quiet_hours=QUIET, **kw)
    return pair, account


def test_throwは静かな時間帯に札の原稿だけ出し記録に残す(isolated_account_factory, tmp_path, capsys,
                                                    frozen_now_jst):
    frozen_now_jst(NIGHT)
    files = {
        "a.md": make_queue_text({"publish_at": EARLIER}, body="## threads\n\nふつうの原稿\n"),
        "b.md": make_queue_text({"publish_at": "2026-10-30T02:10:00+09:00", "urgent": "true"},
                                body="## threads\n\n運休のお知らせ\n"),
        "c.md": make_queue_text({"publish_at": "2026-10-30T02:20:00+09:00", "urgent": "true"},
                                body="## threads\n\n運休の続報\n"),
    }
    # 最短間隔 1 時間: 札の 2 本目は同じ run では出ない（越えるのは静かな時間帯だけ）。
    pair, account = _account(isolated_account_factory, tmp_path, files, min_interval_hours=1, max_per_run=3)
    name = account["name"]

    rows = report_mod.schedule(name, now=NIGHT)
    assert {r["file"]: r["urgent"] for r in rows} == {"a.md": False, "b.md": True, "c.md": True}
    cli.show_schedule(rows)
    out = capsys.readouterr().out
    assert out.count("[緊急]") == 2
    summary = report_mod.queue_summary(name, now=NIGHT)[name]
    assert summary["urgent"] == ["b.md", "c.md"]

    logs = []
    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        result = core.throw_once(name, production_flag=True,
                                 adapter_factory=lambda *_: _adapter(base_url), now=NIGHT, log=logs.append)
        created = len(base_url.handler_cls.create_params)
    assert result.exit_code == 0 and result.action == "post"
    assert created == 1
    queue = os.path.join(pair["work"], "docs", "sns", "queue")
    fm = {n: queuefile.parse(os.path.join(queue, n)).front_matter for n in files}
    assert fm["a.md"]["status"] == "approved" and not fm["a.md"].get("post_id")
    assert fm["b.md"]["status"] == "posted"
    assert fm["c.md"]["status"] == "approved" and not fm["c.md"].get("post_id")
    assert any("緊急の札（urgent: true）で静かな時間帯を越えて出します: b.md" in line for line in logs)

    state = accounts_mod.state_dir_for(name)
    records = list(sent_mod.records(state))
    assert len(records) == 1 and records[0]["crossed_quiet_hours"] is True
    runs = runs_mod.read_runs(state)
    posted = [r for r in runs if r.get("action") == "post" and r.get("status") == "ok"]
    assert len(posted) == 1 and posted[0]["file"] == "b.md" and posted[0]["crossed_quiet_hours"] is True

    payload = events_cli.build(name, since="24h", now=NIGHT)
    published = [e for e in payload["events"] if e["kind"] == "published"]
    assert len(published) == 1 and published[0]["crossed_quiet_hours"] is True
    assert published[0]["summary"].endswith("（緊急の札で静かな時間帯を越えた）")


def test_昼に出した札の原稿は越えた印を付けない(isolated_account_factory, tmp_path, frozen_now_jst):
    frozen_now_jst(DAY)
    files = {"b.md": make_queue_text({"publish_at": EARLIER, "urgent": "true"}, body="## threads\n\n昼の札\n")}
    _pair, account = _account(isolated_account_factory, tmp_path, files, min_interval_hours=0)
    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        result = core.throw_once(account["name"], production_flag=True,
                                 adapter_factory=lambda *_: _adapter(base_url), now=DAY)
    assert result.exit_code == 0 and result.action == "post"
    state = accounts_mod.state_dir_for(account["name"])
    records = list(sent_mod.records(state))
    assert len(records) == 1 and "crossed_quiet_hours" not in records[0]
    payload = events_cli.build(account["name"], since="24h", now=DAY)
    published = [e for e in payload["events"] if e["kind"] == "published"]
    assert "crossed_quiet_hours" not in published[0]


def test_札があっても1回の本数は守る(isolated_account_factory, tmp_path, frozen_now_jst):
    frozen_now_jst(NIGHT)
    files = {
        "b.md": make_queue_text({"publish_at": "2026-10-30T02:10:00+09:00", "urgent": "true"},
                                body="## threads\n\n一本目\n"),
        "c.md": make_queue_text({"publish_at": "2026-10-30T02:20:00+09:00", "urgent": "true"},
                                body="## threads\n\n二本目\n"),
    }
    pair, account = _account(isolated_account_factory, tmp_path, files, min_interval_hours=0, max_per_run=1)
    with fake_threads_server({"create": "ok", "publish": "ok"}) as base_url:
        result = core.throw_once(account["name"], production_flag=True,
                                 adapter_factory=lambda *_: _adapter(base_url), now=NIGHT)
    assert result.exit_code == 0
    queue = os.path.join(pair["work"], "docs", "sns", "queue")
    statuses = [queuefile.parse(os.path.join(queue, n)).front_matter["status"] for n in ("b.md", "c.md")]
    assert statuses == ["posted", "approved"]
