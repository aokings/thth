"""5.34.0 監査の危ない 1 と軽い 1・3・4・5 の柵。

見るのは:
  - 遠くの道（thth.me の鍵の道）で `--unpublished` が VM まで運ばれ、Meta に `UNPUBLISHED` が送られる
    （監査役の台本 `audit-5340/test_audit_5340.py::test_R1_…` が元。黙って捨てて LIVE_NOW にしない）。
    一段目の文と打ち直しの命令も非公開の形。旗なしは欄を送らず今までどおり LIVE_NOW。
    VM が旗を読まない（古い VM）と分かったら、二段目を打たずに断る。
  - 遠くの道でもフォロワー 100 人の断りは `live_followers_too_few`（REASONS に載っている）。
  - adapter の「unpublished は真か偽」の検め。
  - 見張りの withheld は、文に permission の無い #100（PermissionMissing でない）と subcode 33 では起きない。
    見張りの画面は withheld を「失敗」と呼ばない。
偽の Graph と偽の Worker は loopback だけ。本物の Meta・thth.me は叩かない。
"""
from __future__ import annotations

import json

import pytest

from tests.test_remote_meta import (LIVE, PAGE, STREAM_KEY, V, cli, far, nothing_kept, two_steps, vm,  # noqa: F401
                                    writes)
from tests.test_v212_server_writes import env, save_config  # noqa: F401
from tests.test_v3140_remote_cli import home, worker  # noqa: F401
from tests.test_m5_permission_watch import world  # noqa: F401
from tests.test_v5340_threads_withheld import PERMISSION, _ig_world, _keepalive, _results, fake_graph
from thth import cli as cli_mod
from thth import permission_watch as pw
from thth import remote, server_meta
from thth.adapters import facebook as fb
from thth.meta import graph as meta_graph

CREATE = ("live", "create", "alpha", "--title", "審査の録画", "--by", "m")


# ---------------------------------------------------------------- 危ない 1: 遠くの道で旗を運ぶ

def test_遠くの道でunpublishedがVMまで運ばれUNPUBLISHEDが送られる(far, capsys):
    (rc, out, err), first = two_steps(capsys, *CREATE, "--unpublished")
    assert rc == 0, err
    assert first["unpublished"] is True and first["status"] == "UNPUBLISHED"
    value = json.loads(out)
    assert value["unpublished"] is True and value["status"] == "UNPUBLISHED"
    assert [p["form"].get("status") for p in writes(far)] == [["UNPUBLISHED"]]
    bodies = [c["body"] or {} for c in far["worker"].calls]
    assert bodies and all(b.get("unpublished") is True for b in bodies)
    nothing_kept(far, STREAM_KEY)


def test_遠くの道の旗なしは欄を送らず今までどおりLIVE_NOW(far, capsys):
    (rc, out, err), first = two_steps(capsys, *CREATE)
    assert rc == 0, err
    assert first["unpublished"] is False
    assert [p["form"].get("status") for p in writes(far)] == [["LIVE_NOW"]]
    assert all("unpublished" not in (c["body"] or {}) for c in far["worker"].calls)


def test_遠くの道で公開の一段目のdigestでは非公開を打てない(far, capsys):
    rc, out, _err = cli(capsys, *CREATE, "--remote", "--json")
    public = json.loads(out)["digest"]
    rc, out, _err = cli(capsys, *CREATE, "--unpublished", "--remote", "--confirm", public, "--json")
    assert rc == 1 and json.loads(out)["error"] == "meta_write_digest_mismatch"
    assert writes(far) == []


def test_遠くの道の非公開の一段目は意味を言い換え打ち直しに旗が残る(far, capsys):
    rc, out, _err = cli(capsys, *CREATE, "--unpublished", "--remote")
    assert rc == 1
    assert "（非公開）" in out and "ほかの人には見えません" in out and "未確認" in out
    assert "すぐ「配信中」" not in out
    assert "--unpublished" in out.split("打つなら:")[1]
    rc, out, _err = cli(capsys, *CREATE, "--remote")
    assert "すぐ「配信中」" in out and "--unpublished" not in out.split("打つなら:")[1]
    assert writes(far) == []


def test_VMが旗を読まなければ遠くの道は打たずに断る(monkeypatch, capsys):
    # 古い VM（旗を読まず公開の一段目を返す）と組んだ形。二段目を頼まない。
    asked = []

    def old_vm(operation, account, **fields):
        asked.append(fields)
        return {"ok": False, "stage": 1, "digest": "0" * 12, "title": fields.get("title"), "description": ""}
    monkeypatch.setattr(remote, "call", old_vm)
    monkeypatch.setattr(remote, "load", lambda: {"url": "https://example.test", "key": "k"})
    rc, out, err = cli(capsys, *CREATE, "--unpublished", "--remote")
    assert rc == 2 and "remote_unsupported" in out + err
    assert len(asked) == 1 and asked[0]["unpublished"] is True


def test_VMの口はunpublishedを真偽だけ受ける():
    assert "unpublished" in server_meta._FIELDS["live_create"][0] and "unpublished" in server_meta._BOOLS
    server_meta._validate({"operation": "live_create", "account": "alpha", "title": "t", "unpublished": True})
    for bad in ("true", 1, "UNPUBLISHED"):
        with pytest.raises(Exception):
            server_meta._validate({"operation": "live_create", "account": "alpha", "title": "t", "unpublished": bad})
    with pytest.raises(Exception):
        server_meta._validate({"operation": "live_create", "account": "alpha", "title": "t", "status": "UNPUBLISHED"})


# ---------------------------------------------------------------- 軽い 3: 柵の穴

def test_遠くの道でもフォロワーの断りはlive_followers_too_few(far, capsys):
    far["routes"][("POST", V + f"/{PAGE}/live_videos")] = (400, {"error": {
        "code": 100, "error_subcode": 1363144, "message": "(#100) Page must have at least 100 followers"}})
    (rc, out, _err), _first = two_steps(capsys, *CREATE, "--unpublished")
    assert rc != 0
    value = json.loads(out)
    assert value["error"] == "live_followers_too_few", value
    assert "live_followers_too_few" in server_meta.REASONS


@pytest.mark.parametrize("bad", [1, 0, "false", "true", None, "UNPUBLISHED"])
def test_adapterのunpublishedは真か偽だけ(bad, monkeypatch):
    adapter = fb.FacebookAdapter(access_token="t", page_id=PAGE)
    monkeypatch.setattr(adapter, "_post_object", lambda *a, **k: pytest.fail("網に出た"))
    with pytest.raises(fb.AdapterError):
        adapter.live_create("t", unpublished=bad)


# ---------------------------------------------------------------- 軽い 1・3: withheld の条件

def test_keepalive_文にpermissionの無い100は失敗(world, monkeypatch, capsys):
    _ig_world(world, monkeypatch)
    rc, out = _keepalive(monkeypatch, capsys, (400, {"error": {"code": 100, "message": "(#100) Invalid parameter"}}))
    assert rc == 1 and _results(out)[PERMISSION] == "error"


def test_keepalive_subcode33は口座ごと見えないのでwithheldにしない(world, monkeypatch, capsys):
    _ig_world(world, monkeypatch)
    rc, out = _keepalive(monkeypatch, capsys, (400, {"error": {
        "code": 100, "error_subcode": 33, "type": "GraphMethodException",
        "message": "Unsupported get request. Object with ID '77' does not exist, cannot be loaded due to "
                   "missing permissions, or does not support this operation."}}))
    assert rc == 1 and _results(out)[PERMISSION] == "error"


# ---------------------------------------------------------------- 軽い 4: 見張りの画面

def test_見張りの画面はwithheldを失敗と呼ばない(world, monkeypatch, capsys):
    from tests.test_v5340_threads_withheld import DENIED_100, _routes
    _ig_world(world, monkeypatch)
    with fake_graph(_routes(DENIED_100)) as (base, _seen):
        monkeypatch.setenv(meta_graph.BASE_URL_ENV, base)
        assert pw.keepalive()["results"]
    capsys.readouterr()
    assert cli_mod.main(["admin", "meta", "watch"]) == 0
    line = next(line for line in capsys.readouterr().out.splitlines() if line.startswith(PERMISSION))
    assert "失敗:" not in line and "Meta が出さない" in line
