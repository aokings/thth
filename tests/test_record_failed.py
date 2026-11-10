"""実行記録（runs）に書けないとき、黙らずに画面へ知らせる（監査 5.14.0b の 5 の残り）。"""
from thth import meta_write as mw
from thth import runs as runs_mod
from thth.wa import cli as wa_cli
from thth.wa import ledger as wa_ledger


def _broken(*_a, **_k):
    raise PermissionError(13, "denied")


def test_Metaの書く口は記録に書けなければ知らせる(monkeypatch, capsys):
    monkeypatch.setattr(runs_mod, "record_minimal", _broken)
    mw._record("acct", "dm_event", "facebook", "ok", permission="page_events")
    err = capsys.readouterr().err
    assert "実行記録（runs）に書けませんでした" in err and "dm_event" in err and "PermissionError" in err


def test_WhatsAppの送信は記録に書けなければ今日の数に入らないと知らせる(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(wa_ledger, "ensure_state_dir", lambda _name: str(tmp_path))
    monkeypatch.setattr(runs_mod, "append_run", _broken)
    wa_cli._record("acct", "wa_send", "ok", permission="whatsapp_business_messaging", mode="production")
    err = capsys.readouterr().err
    assert "実行記録（runs）に書けませんでした" in err and "今日の通数に入っていません" in err
    wa_cli._record("acct", "wa_numbers", "ok", permission="whatsapp_business_management")
    err = capsys.readouterr().err
    assert "実行記録（runs）に書けませんでした" in err and "今日の通数" not in err


def test_書ければ何も出さない(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(runs_mod, "record_minimal", lambda *_a, **_k: None)
    mw._record("acct", "dm_list", "facebook", "ok")
    assert capsys.readouterr().err == ""


def test_残さない試験はreflogの時刻とshaを見ず文だけを見る(tmp_path):
    """11/10 の CI: reflog の時刻 1762737001 が目印 7001 に偶然当たって落ちた。"""
    from tests.kept_bytes import kept_bytes
    logs = tmp_path / ".git" / "logs"
    logs.mkdir(parents=True)
    head = logs / "HEAD"
    head.write_bytes(b"0" * 40 + b" " + b"a" * 40 + b" t <t@x> 1762737001 +0000\tcommit: first\n")
    assert b"7001" not in kept_bytes(head)
    head.write_bytes(b"0" * 40 + b" " + b"a" * 40 + b" t <t@x> 1762730000 +0000\tcommit: id 7001\n")
    assert b"7001" in kept_bytes(head)                  # 文に入ったものは見落とさない
    other = tmp_path / "runs.jsonl"
    other.write_bytes(b'{"at": 1762737001}')
    assert b"7001" in kept_bytes(other)                 # git の外はそのまま
