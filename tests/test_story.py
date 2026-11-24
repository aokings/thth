"""Instagram のストーリーを `thth send --story` で出す口（11/24・`thth/story.py`）。

11/24 本物: Threads の `--share-to-instagram` は SUCCESS でもストーリーが作られないことがあり、IG の口座から直接
（`post_options: {story: true}`）なら出た。ここは旗・断り・指紋を縛る。本物の Graph API は叩かない。
"""
from __future__ import annotations

import json

import pytest

from thth import approval, cli, core, read_coordination, refusal
from thth import story as story_mod

IG_CFG = {"media": "instagram", "handle": "kopicha_test"}


class _R:
    exit_code, error, action, mode, post_id, url, digest = 0, None, "dry_run", "rehearsal", None, None, "d"
    message = next_at = reason = publish_at = file = None


@pytest.fixture
def sent(monkeypatch):
    """`core.send_once` に渡った引数（呼ばれなければ空）。台帳は試験の中の IG の口座。"""
    seen = {}

    def fake(account, **kw):
        seen.update(kw)
        return _R()
    monkeypatch.setattr(core, "send_once", fake)
    monkeypatch.setattr(read_coordination, "invoke", lambda args, command, call=None: call() if call else args.func(args))
    monkeypatch.setattr(cli.accounts_mod, "load_account", lambda name: dict(IG_CFG))
    return seen


def test_storyの旗はpost_optionsのstoryとして渡る(sent):
    assert cli.main(["send", "kopicha-ig", "--story", "--media", "a.jpg", "--alt", "赤い点"]) == 0
    assert sent["post_options"] == {"story": True}
    assert sent["media_rows"] == [{"file": "a.jpg", "alt": "赤い点"}]
    sent.clear()
    assert cli.main(["send", "kopicha-ig", "--media", "a.jpg", "--alt", "赤い点"]) == 0
    assert "post_options" not in sent


@pytest.mark.parametrize("extra,detail", [
    (["--media", "a.jpg", "--alt", "a", "--reply-to", "17900000000000001"], "reply"),
    (["--media", "a.jpg", "--alt", "a", "--paid-partnership"], "paid_partnership"),
    (["--media", "a.jpg", "--alt", "a", "--paid-partnership", "--sponsor", "17841400000000001"], "paid_partnership"),
    (["--media", "a.jpg", "--alt", "a", "--topic", "話題"], "other_flags"),
    (["--media", "a.jpg", "--alt", "a", "--media", "b.jpg", "--alt", "b"], "media_count"),
    (["--media", "a.jpg", "--alt", "a", "--text", "本文"], "text_and_photo"),
    (["--text", " "], "empty"),
])
def test_storyの組み合わせの誤りは3つで断り_送らない(sent, capsys, extra, detail):
    assert cli.main(["send", "kopicha-ig", "--story", *extra]) == 2
    assert sent == {}
    err = capsys.readouterr().err
    first = err.splitlines()[0]
    assert first.startswith("story_unavailable: " + story_mod.LINES[detail].split("{")[0])
    for head in (refusal.WHY, refusal.FIX, refusal.BETTER):
        assert any(line.startswith(head) for line in err.splitlines()), (detail, head, err)


def test_Instagramでない口座のstoryは断る(sent, monkeypatch, capsys):
    monkeypatch.setattr(cli.accounts_mod, "load_account", lambda name: {"media": "threads"})
    assert cli.main(["send", "kopicha", "--story", "--media", "a.jpg", "--alt", "a"]) == 2
    assert sent == {}
    err = capsys.readouterr().err
    assert "Instagram の口座だけです（この口座は threads）" in err
    assert "Threads の投稿を見せたいときも" in err   # 詳細の語ごとの もっと良く


def test_storyの断りはjsonにも3つと詳細の語が載る(sent, capsys):
    assert cli.main(["send", "kopicha-ig", "--story", "--json", "--media", "a.jpg", "--alt", "a",
                     "--reply-to", "17900000000000001"]) == 2
    body = json.loads(capsys.readouterr().out)
    assert body["error"] == "story_unavailable" and body["reason"] == "reply"
    assert body["why"] and body["fix"].startswith("--reply-to を外して") and body["better"]


def test_storyは承認の指紋に入る():
    """ストーリーとフィードで digest が違う（ストーリーのつもりの承認でフィードには出ない）。"""
    row = {"file": "a.jpg", "alt": "赤い点", "source_sha256": "0" * 64, "size": 100, "role": "media",
           "index": 1, "public_sha256": "1" * 64, "public_size": 100, "format": "jpeg", "kind": "image",
           "width": 1080, "height": 1920, "duration": None, "orientation": 1}
    feed = {"files": [dict(row)], "attachments": [], "post_options": {}, "captions": []}
    story = {"files": [dict(row)], "attachments": [], "post_options": {"story": True}, "captions": []}
    args = dict(text="", account="kopicha-ig", reply_to=None, topic=None)
    assert approval.compute_send_digest(**args, media_manifest=feed) != \
        approval.compute_send_digest(**args, media_manifest=story)
