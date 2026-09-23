"""3.7.0 §B3 承認の確定待ち（`state/<account>/approve_pending.json`）。

- `thth approve` の 1 段目で `{file, digest, at}` を控える（本文は残さない）。確定・
  取り消し・本文の変更で消える。
- queue・board・observe で not_approved を `awaiting_confirm` と `not_requested` に分ける。
- publish_at まで 3 時間を切ったら observe の次の一手（`kind: confirm_due`）と運用通知
  （増えたときだけ）。
- 1 段目の出力に「確定するまで出ません」。
"""
from __future__ import annotations

import datetime
import json
import os

from tests.conftest import run_thth, write_queue_file
from tests.test_v330_held import held_run, Response  # noqa: F401 — fixture を借りる
from thth import approve_pending, jst, morning, queuefile, report

BODY = "## threads\n\n確定待ちの本文です（PRIVATE BODY）。\n"


def _draft(account, name="a.md", publish_at="2026-09-30T08:00:00+09:00", body=BODY):
    return write_queue_file(account["queue_dir"], name, body=body,
                            fm_overrides={"account": account["name"], "status": "draft",
                                          "approved_sha": None, "publish_at": publish_at})


def _pending_path(account):
    return approve_pending.path_for(account["name"])


def _first_stage(path, *extra):
    result = run_thth(["approve", str(path), *extra])
    assert result.returncode == 1, result.stderr
    return result


def test_1段目で控えを残す_本文は残さない_確定するまで出ませんと言う(isolated_account):
    path = _draft(isolated_account)
    first = _first_stage(path)
    assert "確定するまで出ません" in first.stdout.splitlines()[0]
    assert "**確定するまで出ません**" in first.stdout
    raw = open(_pending_path(isolated_account), encoding="utf-8").read()
    assert "PRIVATE BODY" not in raw and "確定待ちの本文" not in raw
    data = json.loads(raw)
    digest = [line.split(": ", 1)[1] for line in first.stdout.splitlines()
              if line.startswith("digest: ")][0]
    assert data["version"] == 1 and len(data["items"]) == 1
    item = data["items"][0]
    assert item["file"] == "docs/sns/queue/a.md" and item["digest"] == digest
    assert set(item) == {"file", "digest", "bundle_digest", "at", "file_sha256"}

    as_json = json.loads(_first_stage(path, "--json").stdout)
    assert as_json["not_until_confirmed"] is True and as_json["pending_recorded"] is True
    assert as_json["note"].startswith("確定するまで出ません")


def test_確定すると控えが消える(isolated_account):
    path = _draft(isolated_account)
    first = _first_stage(path)
    digest = [line.split(": ", 1)[1] for line in first.stdout.splitlines()
              if line.startswith("digest: ")][0]
    assert os.path.exists(_pending_path(isolated_account))
    second = run_thth(["approve", str(path), "--confirm", digest, "--by", "テスト"])
    assert second.returncode == 0, second.stderr
    assert not os.path.exists(_pending_path(isolated_account))


def test_本文を変えると確定待ちに数えない_queueとboardで分ける(isolated_account):
    now = jst.parse("2026-09-30T06:00:00+09:00")
    a = _draft(isolated_account, "a.md", "2026-09-30T08:00:00+09:00")
    b = _draft(isolated_account, "b.md", "2026-09-30T12:00:00+09:00")
    _draft(isolated_account, "c.md", "2026-09-30T13:00:00+09:00")
    _first_stage(a)
    _first_stage(b)
    queue = report.queue_summary(isolated_account["name"], now=now)[isolated_account["name"]]
    assert queue["approval"] == {"awaiting_confirm": 2, "not_requested": 1,
                                 "awaiting_confirm_earliest_publish_at": "2026-09-30T08:00:00+09:00",
                                 "confirm_due": 1}
    row = next(r for r in report.board_summary(now=now)["accounts"]
               if r["account"] == isolated_account["name"])
    assert row["awaiting_confirm_count"] == 2 and row["not_requested_count"] == 1
    assert row["awaiting_confirm_earliest_publish_at"] == "2026-09-30T08:00:00+09:00"

    # 本文を変えた（commit・push まで）——b は確定待ちから外れる。
    write_queue_file(isolated_account["queue_dir"], "b.md", body="## threads\n\n書き直した。\n",
                     fm_overrides={"account": isolated_account["name"], "status": "draft",
                                   "approved_sha": None, "publish_at": "2026-09-30T12:00:00+09:00"})
    queue = report.queue_summary(isolated_account["name"], now=now)[isolated_account["name"]]
    assert queue["approval"]["awaiting_confirm"] == 1 and queue["approval"]["not_requested"] == 2

    rc_out = run_thth(["queue", isolated_account["name"]])
    assert "確定待ち 1 本（最短の publish_at 2026-09-30T08:00:00+09:00）" in rc_out.stdout


def test_取り消しで控えが消える(isolated_account):
    path = _draft(isolated_account)
    _first_stage(path)
    items = approve_pending.load(isolated_account["name"])
    assert list(items) == ["docs/sns/queue/a.md"]
    approve_pending.clear(isolated_account["name"], isolated_account["repo_dir"], [path])
    assert approve_pending.load(isolated_account["name"]) == {}
    assert not os.path.exists(_pending_path(isolated_account))


def test_observeに確定待ちと3時間を切ったら次の一手(isolated_account):
    a = _draft(isolated_account, "a.md", "2026-09-30T08:00:00+09:00")
    _draft(isolated_account, "b.md", "2026-09-30T20:00:00+09:00")
    _first_stage(a)
    now = jst.parse("2026-09-30T06:00:00+09:00")
    payload = morning.build(isolated_account["name"], now=now, mark=False)
    sections = {s["section"]: s for s in payload["sections"]}
    today = sections["today"]["value"]["by_account"][isolated_account["name"]]["value"]
    confirm = today["confirm_items"]["value"]
    assert confirm["n_awaiting_confirm"] == 1 and confirm["n_not_requested"] == 1
    assert confirm["n_due"] == 1 and confirm["items"][0]["file"] == "a.md"
    steps = [s for s in sections["next_steps"]["value"]["steps"] if s["kind"] == "confirm_due"]
    assert len(steps) == 1
    assert steps[0]["command"].startswith("thth approve docs/sns/queue/a.md --confirm ")
    assert steps[0]["command"].endswith("--by <名前>")
    assert "body" not in steps[0]
    lines = []
    morning.render(payload, out=lines.append)
    text = "\n".join(lines)
    assert "確定待ち 1 本（最短の publish_at 2026-09-30T08:00:00+09:00" in text
    assert "確定  " in text and "確定するまで出ません" in text
    # 確定待ちの升目と次の一手は本文を持たない（今日の予定の先頭 60 字は従前どおり）。
    assert "PRIVATE BODY" not in json.dumps([today["confirm_items"], steps], ensure_ascii=False)

    # 3 時間より前なら次の一手に出さない（確定待ちの名前は出る）。
    early = morning.build(isolated_account["name"], now=now - datetime.timedelta(hours=2),
                          mark=False)
    steps = [s for s in {s["section"]: s for s in early["sections"]}["next_steps"]["value"]["steps"]
             if s["kind"] == "confirm_due"]
    assert steps == []


def test_運用通知は3時間を切った確定待ちが増えたときだけ(held_run):  # noqa: F811
    account = held_run.account
    soon = (jst.now_jst() + datetime.timedelta(hours=1)).replace(microsecond=0)
    path = write_queue_file(account["queue_dir"], "soon.md", body=BODY,
                            fm_overrides={"account": account["name"], "status": "draft",
                                          "approved_sha": None, "publish_at": jst.iso(soon)})
    approve_pending.record(account["name"], account["repo_dir"],
                           [{"path": path, "digest": "abc123abc123"}], bundle_digest="abc123abc123")
    assert held_run.run() == 0
    notices = [m for m in held_run.mails if m[1] == "notice"]
    assert notices == [("user@example.org", "notice", "confirm_due: 1"),
                       ("admin@example.org", "notice", "confirm_due: 1")]
    assert held_run.run() == 0
    assert len([m for m in held_run.mails if m[1] == "notice"]) == 2, "同じ本数なら黙る"
    later = write_queue_file(account["queue_dir"], "soon2.md", body="## threads\n\n二本目。\n",
                             fm_overrides={"account": account["name"], "status": "draft",
                                           "approved_sha": None, "publish_at": jst.iso(soon)})
    approve_pending.record(account["name"], account["repo_dir"],
                           [{"path": later, "digest": "def456def456"}], bundle_digest="def456def456")
    assert held_run.run() == 0
    assert [m[2] for m in held_run.mails if m[1] == "notice"][-1] == "confirm_due: 2"
    assert queuefile.parse(path).front_matter["status"] == "draft", "通知は原稿を変えない"
