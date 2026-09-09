"""台帳 `production: false` なら `--production` を付けても dry-run（発注 §5 受け入れ 6）。
既知事故 test_20260907_env_unwired_ran_production: watchtower 9/7 と同じ型の事故を防ぐ。"""
from __future__ import annotations

import datetime

from tests.conftest import write_queue_file
from thth import core

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")


def test_production未commitならproductionフラグを付けてもdry_run(isolated_account):
    write_queue_file(isolated_account["queue_dir"], "a.md")  # 既定 approved・08:00 publish_at
    lines = []
    result = core.throw_once(isolated_account["name"], production_flag=True,
                              log=lines.append, now=NOW)
    assert lines[0] == "mode: rehearsal"
    assert result.mode == "rehearsal"
    assert result.exit_code == 0
    assert result.action == "skip"  # dry-run: 投げるはずの本文をログに出すだけ


def test_20260907_env_unwired_ran_production(isolated_account):
    """watchtower 9/7 の事故と同じ型: env ファイルの有無に関わらず、台帳の
    production: true が commit されていなければ本番にしない。"""
    write_queue_file(isolated_account["queue_dir"], "a.md")
    result = core.throw_once(isolated_account["name"], production_flag=True, now=NOW)
    assert result.mode == "rehearsal"

    # env・token が（たまたま）存在しても、台帳 production が false のままなら変わらない。
    import os
    env_path = os.path.join(isolated_account["repo_dir"], "..", "dummy.env")
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("HEALTHCHECK_URL=https://example.invalid/x\n")
    result2 = core.throw_once(isolated_account["name"], production_flag=True, now=NOW)
    assert result2.mode == "rehearsal"
