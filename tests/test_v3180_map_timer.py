"""世間の層の timer（`thth systemd --map <project>`・3.18.0・masaru 10/12「全部やって良い」）。"""
from __future__ import annotations

import pytest

from thth import cli, systemd_gen


def test_map_timerは1日1回で分はprojectごとに決まる(capsys):
    assert cli.main(["systemd", "--map", "kopicha"]) == 0
    out = capsys.readouterr().out
    assert "Unit=thth-map@kopicha.service" in out
    line = next(l for l in out.splitlines() if l.startswith("OnCalendar="))
    assert line.startswith("OnCalendar=*-*-* 05:") and line.endswith(":00")
    assert out == systemd_gen.render_map_timer("kopicha")  # 決定的


def test_map_serviceだけが世間の層を有効にする(capsys):
    assert cli.main(["systemd", "--map", "kopicha", "--service"]) == 0
    out = capsys.readouterr().out
    assert "Environment=THTH_MAP_WORLD=1" in out
    assert "ExecStart=/srv/thth/app/bin/thth map collect %i" in out
    assert "THTH_MAP_WORLD" not in systemd_gen.render_map_timer("kopicha")
    assert "THTH_MAP_WORLD" not in systemd_gen.render_maintain_service()


@pytest.mark.parametrize("argv", [["systemd", "a", "--map", "p"], ["systemd", "--maintain", "--map", "p"]])
def test_mapは他の口と併用しない(argv, capsys):
    assert cli.main(argv) == 2


def test_map_project名に使えない字は断る(capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["systemd", "--map", "../x"])
