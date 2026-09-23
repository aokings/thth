"""3.1.1 件 4: 運用通知の案内をそのまま打てる形にする。

- `thth run` の「運用通知に未完了があります」に account 名が入る。
- `thth notifications status|test` を account 無しで打つと、汎用文に潰さず
  静的な `account_required` と正しい打ち方を stderr に出して rc=2。
- それ以外の ValueError は従前どおり汎用文（秘密値を出さない）。
"""
from types import SimpleNamespace
import pytest

from thth import cli, healthcheck, incident, selfupdate


@pytest.fixture
def root(tmp_path, monkeypatch):
    path = tmp_path / "root"; path.mkdir(mode=0o700)
    monkeypatch.setenv("THTH_ROOT", str(path))
    monkeypatch.setenv("THTH_ACCOUNTS_DIR", str(path / "accounts"))
    (path / "accounts").mkdir()
    return path


@pytest.mark.parametrize("action", ["status", "test"])
def test_missing_account_is_static_account_required_with_the_right_form(root, capsys, action):
    args = cli.build_parser().parse_args(["notifications", action])
    assert args.func(args) == 2
    err = capsys.readouterr().err
    assert err.startswith("account_required: ")
    assert f"thth notifications {action} <account>" in err
    assert "通知操作に失敗しました" not in err


def test_other_value_errors_keep_the_generic_message(root, capsys):
    args = cli.build_parser().parse_args(["notifications", "config"])  # --input 無し
    assert args.func(args) == 2
    err = capsys.readouterr().err
    assert "通知操作に失敗しました" in err and "account_required" not in err


def test_run_guidance_names_the_account(root, monkeypatch, capsys):
    # 通知に未完了がある状態だけを作る（投稿・SMTP・Git には触らない）。
    monkeypatch.setattr(selfupdate, "pull_and_reexec", lambda *a, **k: None)
    monkeypatch.setattr(healthcheck, "diagnostic", lambda *a, **k: object())
    monkeypatch.setattr(healthcheck, "notify", lambda *a, **k: SimpleNamespace(
        delivery="not_configured", category=None, state_saved=True))
    monkeypatch.setattr(incident, "notify", lambda *a, **k: "processed")
    monkeypatch.setattr(incident, "summary", lambda *a, **k: {"mail_pending": 1, "repo_pending": 0})
    args = cli.build_parser().parse_args(["run", "kopicha-mastodon"])
    assert args.func(args) == 2  # 台帳が無いので投稿はしない
    err = capsys.readouterr().err
    assert "運用通知に未完了があります: thth notifications status kopicha-mastodon で確認してください" in err
