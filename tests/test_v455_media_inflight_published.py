"""4.5.5: 画像つきの inflight を `--published <post_id>` で解く（kopicha-threads 10/23）。

10/23 朝、画像 1 枚の Threads の投稿で `threads_publish` の POST が例外で戻り
（`media_publishing_failed`・phase unknown）、実際は出ていた。`thth inflight … resolve
--published` は「添付はこの口では解かない」で断ったので、VM で `inflight_cli.apply()` を
手で呼んで解いた。ここではそれを道具の口でできるようにする。

- 出たかもしれない phase（unknown・publishing・published）は --published で解ける。
  書き戻しは文字だけの inflight と同じ `record_published`（本文の一致を確かめる）。
- 本文が違えば書き戻さない（inflight は残る）。
- held に --published は断る（`media_inflight_held`）。held の --not-published は今までどおり。
- 遠くの道（`server_writes.inflight`）も同じ `refusal()` を通る。
- 公開の POST の時間切れは tests/test_v455_threads_publish_timeout.py。
"""
from __future__ import annotations

import json
import os

import pytest

from tests.conftest import commit_and_push_path
from tests.test_v331_inflight_cli import _cli, _digest_from, _stuck
from thth import admin_log
from thth import inflight as inflight_mod
from thth import inflight_cli
from thth import inflight_resolve
from thth import queuefile
from thth import sent as sent_mod

SHA = "a" * 64
MANIFEST = {"files": [{"role": "media", "kind": "image", "format": "png", "public_sha256": SHA,
                       "alt": "緑茶の湯のみ"}],
            "attachments": [], "post_options": {}, "captions": []}


def _with_media(state_dir, **media):
    """inflight.json に添付の journal（`media_delivery._progress` と同じ形）を足す。"""
    path = os.path.join(state_dir, "inflight.json")
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    name = os.path.basename(os.path.normpath(state_dir))
    record["media"] = {"schema_version": 1, "account": name, "instance": "https://graph.invalid",
                       "intent_sha256": "b" * 64, "manifest": MANIFEST, **media}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    return record


UNKNOWN = {"phase": "unknown", "remote_ids": ["11"], "reason": "media_publishing_failed"}


def test_添付のunknownは_published_で解ける_sentに添付の記録が残る(isolated_account_factory, tmp_path, capsys):
    account, state_dir = _stuck(isolated_account_factory, tmp_path)
    _with_media(state_dir, **UNKNOWN)
    rc, out, _ = _cli(capsys, account["name"], "show")
    assert rc == 0 and "phase unknown" in out and "media_publishing_failed" in out
    assert f"thth inflight {account['name']} resolve --published <post_id>" in out
    rc, out, err = _cli(capsys, account["name"], "resolve", "--published", "8001", "--by", "masaru")
    assert rc == 1, err
    assert "画像と本文" in out and inflight_cli.MEDIA_GRANT_NOTE in out
    rc, out, err = _cli(capsys, account["name"], "resolve", "--published", "8001", "--by", "masaru",
                        "--confirm", _digest_from(out))
    assert rc == 0, err
    assert inflight_cli.MEDIA_GRANT_NOTE in out
    assert inflight_mod.read(state_dir) is None
    qf = queuefile.parse(os.path.join(account["queue_dir"], "a.md"))
    assert qf.get("status") == "posted" and str(qf.get("post_id")) == "8001"
    row = sent_mod.read(state_dir, "8001")
    assert row["media"] == [{"sha256": SHA, "kind": "image", "alt_present": True, "remote_id": "11"}]
    assert row["attachment_kinds"] == ["image"] and row["link_urls"] == []
    assert "緑茶の湯のみ" not in json.dumps(row, ensure_ascii=False)  # alt の文字は残さない
    [archived] = inflight_mod.archives(state_dir)
    with open(archived, encoding="utf-8") as fh:
        saved = json.load(fh)
    assert saved["resolution"] == "human_published" and saved["inflight"]["media"]["phase"] == "unknown"
    rows, _ = admin_log.read(account=account["name"], event="inflight_resolved")
    assert rows[-1]["diff"]["post_id"] == [None, "8001"]


def test_本文が違えば書き戻さない_inflightは残る(isolated_account_factory, tmp_path, capsys):
    account, state_dir = _stuck(isolated_account_factory, tmp_path)
    _with_media(state_dir, **UNKNOWN)
    path = os.path.join(account["queue_dir"], "a.md")
    text = open(path, encoding="utf-8").read().replace("本文です。", "書き換えた本文。")
    open(path, "w", encoding="utf-8").write(text)
    commit_and_push_path(path)
    rc, out, _ = _cli(capsys, account["name"], "resolve", "--published", "8001", "--by", "masaru")
    rc, _, err = _cli(capsys, account["name"], "resolve", "--published", "8001", "--by", "masaru",
                      "--confirm", _digest_from(out))
    assert rc == 1 and err.startswith("text_mismatch_before_writeback")
    left = inflight_mod.read(state_dir)
    assert left is not None and left["media"]["phase"] == "unknown"
    assert sent_mod.read(state_dir, "8001") is None and inflight_mod.archives(state_dir) == []


@pytest.mark.parametrize("phase", ["unknown", "publishing", "published"])
def test_出たかもしれないphaseだけ_published_で解ける(phase):
    record = {"file": "a.md", "media": {"phase": phase}}
    assert inflight_cli.refusal("kopicha-threads", record, "published", "8001") is None
    # --not-published は今までどおり held だけ。
    assert inflight_cli.refusal("kopicha-threads", record, "not_published", None)[1] == "media_inflight_unsupported"


@pytest.mark.parametrize("phase", ["prepared", "uploading", "creating", "processing", "ready", "failed", None])
def test_公開のPOSTより前のphaseは_published_でも断る(phase):
    record = {"file": "a.md", "media": {"phase": phase} if phase else {"account": "x"}}
    assert inflight_cli.refusal("kopicha-threads", record, "published", "8001")[1] == "media_inflight_unsupported"


def test_heldに_published_は断る_not_publishedは今までどおり(isolated_account_factory, tmp_path, capsys):
    account, state_dir = _stuck(isolated_account_factory, tmp_path, git=False)
    _with_media(state_dir, phase="held", remote_ids=["11"], reason="media_publishing_http_400_code_100")
    before = inflight_mod.read(state_dir)
    rc, _, err = _cli(capsys, account["name"], "resolve", "--published", "8001", "--by", "masaru")
    assert rc == 2 and err.startswith("media_inflight_held") and "--not-published" in err
    assert inflight_mod.read(state_dir) == before
    rc, out, _ = _cli(capsys, account["name"], "show")
    assert f"thth inflight {account['name']} resolve --not-published" in out
    rc, out, _ = _cli(capsys, account["name"], "resolve", "--not-published", "--by", "masaru")
    rc, _, err = _cli(capsys, account["name"], "resolve", "--not-published", "--by", "masaru",
                      "--confirm", _digest_from(out))
    assert rc == 0, err
    assert inflight_mod.read(state_dir) is None


def test_journalが覚えているpost_idと違えば断る():
    record = {"file": "a.md", "media": {"phase": "published", "post_id": "8001"}}
    assert inflight_cli.refusal("kopicha-threads", record, "published", "8001") is None
    assert inflight_cli.refusal("kopicha-threads", record, "published", "9999")[1] == "post_id_differs"
    # 監査（Fable 10/23）: journal が post_id を覚えているなら --not-published は docs でなく --published へ。
    got = inflight_cli.refusal("kopicha-threads", record, "not_published", None)
    assert got[1] == "published_known" and "--published" in got[2]


def test_添付の記録は媒体をまたいで同じ組み方():
    # Bluesky の動画は blob の後ろに字幕の blob が並ぶ——添付のファイル（role media）の数だけ組む。
    video = {"files": [{"role": "media", "kind": "video", "public_sha256": SHA, "alt": "a"},
                       {"role": "caption", "kind": "caption", "public_sha256": "c" * 64}]}
    manifest, receipt = inflight_resolve.media_receipt(
        {"media": {"manifest": video, "remote_ids": ["bafyvideo", "bafycaption"]}})
    assert manifest is video
    assert receipt == [{"sha256": SHA, "kind": "video", "alt_present": True, "remote_id": "bafyvideo"}]
    # remote_ids が足りなければ記録を組まない（半端な記録を書かない）。kinds は manifest から残る。
    assert inflight_resolve.media_receipt({"media": {"manifest": MANIFEST, "remote_ids": []}}) == (MANIFEST, None)
    assert inflight_resolve.media_receipt({"file": "a.md"}) == (None, None)


# ---------------------------------------------------------------- 遠くの道

from tests.test_v212_server_writes import env, remote  # noqa: E402,F401
from tests.test_v3143_inflight_remote import refused, serve  # noqa: E402
from thth import accounts, jst, server_writes as writes  # noqa: E402


def _remote_inflight(**media):
    state = accounts.state_dir_for("alpha")
    inflight_mod.write(state, file="(send)", started=jst.iso(), text_fingerprint="f" * 64)
    _with_media(state, **media)
    return state


def test_遠くの道も同じ_unknownは_published_で解ける(env):
    state = _remote_inflight(**UNKNOWN)
    got = serve(env, operation="inflight_status")
    assert got["inflight"]["media_phase"] == "unknown" and "resolve --published" in got["next"]
    first = serve(env, operation="inflight_resolve", decision="published", post_id="8001")
    assert first["stage"] == 1 and first["inflight"]["media"] is True
    done = serve(env, operation="inflight_resolve", decision="published", post_id="8001", confirm=first["digest"])
    assert done["resolved"] is True and done["resolution"] == inflight_cli.HUMAN_PUBLISHED
    assert inflight_mod.read(state) is None


def test_遠くの道も同じ_heldに_published_は断る(env):
    state = _remote_inflight(phase="held", remote_ids=["11"])
    assert "media_inflight_held" in writes.SAFE_ERRORS
    assert str(refused(env, operation="inflight_resolve", decision="published", post_id="8001")) == "media_inflight_held"
    first = serve(env, operation="inflight_resolve", decision="not_published")
    done = serve(env, operation="inflight_resolve", decision="not_published", confirm=first["digest"])
    assert done["resolved"] is True and inflight_mod.read(state) is None
