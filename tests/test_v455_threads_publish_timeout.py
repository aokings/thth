"""4.5.5: Threads の公開の POST の時間切れは `media_publishing_timeout` と名指す。

作成の時間切れ（`media_creating_timeout`・2.13.2）と同じ筋。generic な
`media_publishing_failed` は「出ていない」と読まれる——kopicha-threads 10/23 は
公開の POST が例外で戻ったが出ていた。phase は unknown のまま、付与は引き上げず、
次の一歩は「媒体の画面で確かめて --published で解く」。
"""
import urllib.error

import pytest

from thth import media_delivery

from thth.adapters import threads_media as tm
from tests.test_v213_threads_media import env, wire, invoke  # noqa: F401


def _fail_publish(monkeypatch, error):
    original = tm._transport.open

    def open_request(request, timeout=None, **kwargs):
        if request.get_method() == "POST" and request.full_url.endswith("/threads_publish"):
            raise error
        return original(request, timeout=timeout, **kwargs)
    monkeypatch.setattr(tm._transport, "open", open_request)


@pytest.mark.parametrize("error", [TimeoutError("slow"), urllib.error.URLError(TimeoutError("slow"))])
def test_公開のPOSTの時間切れは_media_publishing_timeout(env, wire, monkeypatch, error):
    _fail_publish(monkeypatch, error)
    result, journal, _ = invoke(env)
    assert result.post_id is None and result.failure == "media_ambiguous"
    assert result.error == "media_publishing_timeout"
    assert journal["media"]["phase"] == "unknown" and journal["media"]["reason"] == "media_publishing_timeout"
    assert journal["media"]["remote_ids"] == ["11"]
    # 出ているかもしれない: 付与は引き上げない・再 POST もしない。
    assert env[3]["results"] == []
    step = media_delivery.next_step(result.error)
    assert step and "resolve --published" in step


def test_時間切れでない公開の例外は_failed_のまま_次の一歩は出ているかもしれない(env, wire, monkeypatch):
    _fail_publish(monkeypatch, urllib.error.URLError(ConnectionResetError("reset")))
    result, journal, _ = invoke(env)
    assert result.error == "media_publishing_failed" and journal["media"]["phase"] == "unknown"
    assert "resolve --published" in media_delivery.next_step(result.error)
