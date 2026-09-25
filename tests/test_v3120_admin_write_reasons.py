"""3.12.0 §6-5: 運営者の CLI の書き込み命令は、`write_unavailable` の中身を 1 語と次の一手で言う。

見るのは:
  - `THTH_ROOT` を付けずに打つと（VM では置き場の推定は合っていても）`root_mismatch` と
    「THTH_ROOT=<置き場> を付けて」が出る。`write_unavailable` とだけは言わない。
  - state の親のモードが緩いと `unsafe_server_directory` と、THTH_ROOT からの相対の場所・700 が出る。
  - MCP・サーバの口（`server_writes.execute`）は今までどおり静的な `write_unavailable` だけ（パスを出さない）。
"""
from __future__ import annotations

import pytest

from thth import accounts, cli, invites, server_writes
from thth.report_service import ReportServiceError
from tests.test_v3100_invite_create import env as _env  # noqa: F401
from tests.test_v3100_invite_worker import world, authorize_params, make  # noqa: F401
from tests.test_v3100_invite_credential import credentials, run_flow  # noqa: F401
from tests.test_v3100_invite_admin_approval import sessions  # noqa: F401


def _without_root(world, monkeypatch):
    # VM で `THTH_ROOT` を付け忘れた形: app は $THTH_ROOT/app にあるので置き場の推定は合うが、
    # 置き場の検査は明示の THTH_ROOT を求める。
    root = world[0]
    monkeypatch.delenv("THTH_ROOT")
    monkeypatch.setattr(accounts, "APP_DIR", str(root / "app"))
    assert accounts.thth_root() == str(root)
    return root


def test_THTH_ROOTなしはroot_mismatchと次の一手(world, sessions, capsys, tmp_path, monkeypatch):
    record, created = sessions
    name = record["account"]
    root = _without_root(world, monkeypatch)
    body = tmp_path / "body.txt"
    body.write_text("審査用の下書きです。")
    assert cli.main(["admin", "draft", "put", name, "--body-file", str(body), "--by", "masaru"]) == 2
    err = capsys.readouterr().err
    assert err.startswith(f"root_mismatch: THTH_ROOT={root} を付けて打ってください"), err
    assert "write_unavailable" not in err
    assert cli.main(["admin", "approval", "request", name, "--draft", "0" * 64, "--by", "masaru"]) == 2
    assert capsys.readouterr().err.startswith("root_mismatch: THTH_ROOT=")
    assert created == []


def test_サーバの口は静的な名前のまま(world, sessions, monkeypatch):
    record, _ = sessions
    name = record["account"]
    root = _without_root(world, monkeypatch)
    context = invites.invite_context(name)
    request = {"operation": "draft_put", "account": name, "body": "本文", "publish_at": "2026-09-26T10:00:00+09:00"}
    with pytest.raises(ReportServiceError) as caught:
        server_writes.execute(context, request, via="mcp")
    assert str(caught.value) == "write_unavailable"
    assert str(root) not in repr(getattr(caught.value, "reason", None))


def test_stateの親のモードが緩いとunsafe_server_directoryと相対の場所(world, sessions, capsys, tmp_path):
    record, created = sessions
    name = record["account"]
    root = world[0]
    state = root / "state" / name
    state.chmod(0o775)
    try:
        body = tmp_path / "body.txt"
        body.write_text("審査用の下書きです。")
        assert cli.main(["admin", "draft", "put", name, "--body-file", str(body), "--by", "masaru"]) == 2
        err = capsys.readouterr().err
        assert err.startswith(f"unsafe_server_directory: state/{name} のモードを 700 にしてください（THTH_ROOT の下・chmod 700）\n"), err
        assert str(root) not in err
        with pytest.raises(ReportServiceError) as caught:
            invites.admin_approval_request(name, draft="0" * 64, by="masaru")
        assert str(caught.value) == "unsafe_server_directory"
        assert created == []
        # サーバの口は同じ置き場でも静的な名前だけ。
        context = invites.invite_context(name)
        with pytest.raises(ReportServiceError) as caught:
            server_writes.execute(context, {"operation": "draft_put", "account": name, "body": "本文",
                                            "publish_at": "2026-09-26T10:00:00+09:00"}, via="mcp")
        assert str(caught.value) == "write_unavailable"
    finally:
        state.chmod(0o700)
