"""send の確認指紋の境界を入力でずらせないことを、送信口で検証する。"""
import pytest

from thth import approval, core, inflight, accounts
from thth.adapters.base import PublishResult


@pytest.mark.parametrize("production", [False, True])
@pytest.mark.parametrize("field", ["text", "reply_to", "topic"])
@pytest.mark.parametrize("control", ["\x1f", "\x1e", "\x00", "\x7f"])
def test_send_rejects_controls_before_digest_or_adapter(
        isolated_account_factory, production, field, control):
    account = isolated_account_factory(production=True)
    values = {"text": "Visible body", "reply_to": "12345", "topic": "coffee"}
    values[field] = "before" + control + "after"
    logs = []
    def no_adapter(*_):
        pytest.fail("invalid input reached adapter construction")
    digest = approval.compute_send_digest(account=account["name"], **values)
    result = core.send_once(account["name"], **values, production_flag=production,
                            confirm=digest, adapter_factory=no_adapter, log=logs.append)
    assert result.exit_code == 1 and result.action == "skip"
    assert result.digest is None
    assert result.error.startswith(f"control_char({field},")
    assert values[field] not in "\n".join(logs)
    assert inflight.read(accounts.state_dir_for(account["name"])) is None


def test_send_refuses_different_payload_with_same_legacy_digest(isolated_account_factory):
    account = isolated_account_factory(production=True)
    name = account["name"]
    original = {"text": f"A\x1f{name}\x1fB", "reply_to": "12345"}
    changed = {"text": "A", "reply_to": f"B\x1f{name}\x1f12345"}
    digest = approval.compute_send_digest(account=name, topic=None, **original)
    assert digest == approval.compute_send_digest(account=name, topic=None, **changed)
    calls = []
    class Spy:
        def publish(self, post, **_):
            calls.append(post)
            return PublishResult("9001", None, "2026-09-17T12:00:00+09:00")
    result = core.send_once(name, **changed, production_flag=True, confirm=digest,
                            adapter_factory=lambda *_: Spy())
    assert result.exit_code == 1
    assert calls == []


def test_send_keeps_normal_multiline_digest_and_publication(isolated_account_factory):
    account = isolated_account_factory(production=True)
    body = "First line\nSecond\tline\r\n"
    digest = approval.compute_send_digest(text=body.strip(), account=account["name"],
                                          reply_to=None, topic=None)
    calls = []
    class Spy:
        def publish(self, post, **_):
            calls.append(post.text)
            return PublishResult("9001", None, "2026-09-17T12:00:00+09:00")
    result = core.send_once(account["name"], text=body, production_flag=True,
                            confirm=digest, adapter_factory=lambda *_: Spy())
    assert result.exit_code == 0 and calls == [body.strip()]
