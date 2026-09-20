"""v2.1-B: 承認を通る書き込みの口 3 つ（設計 v2 §4.3・2026-09-14）。

場所（`location:` / `location_id:`）・取り下げ（`thth retract`）・Instagram 共有
（`share_to_instagram:`）。**実 API には触れない**——偽サーバ
（`tests/helpers/fake_threads_writes.py`）が受けた **DELETE の回数**を数える。

芯: 人が承認していない公開行為は起きない。記録は消さない。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re

import pytest

from tests.conftest import approve_via_cli, commit_and_push_path, run_thth
from tests.helpers.fake_threads_writes import fake_threads_writes_server
from thth import accounts as accounts_mod
from thth import approval
from thth import core
from thth import inflight as inflight_mod
from thth import lint as lint_mod
from thth import queuefile
from thth import report as report_mod
from thth import retract_cli
from thth import select as select_mod
from thth import sent as sent_mod
from thth.adapters import base as adapter_base
from thth.adapters import threads as threads_mod

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
ACCOUNT = "nigamilab-threads"
BODY = "## threads\n\n渋谷で茶を淹れた。\n"
SECTION = "渋谷で茶を淹れた。"


# --------------------------------------------------------------------------
# 助け手
# --------------------------------------------------------------------------

def _render(fm: dict) -> str:
    lines = ["---"] + [f"{k}: {'' if v is None else v}" for k, v in fm.items()] + ["---"]
    return "\n".join(lines) + "\n"


def _write_queue(queue_dir: str, name: str, *, status="approved", body=BODY,
                 commit=True, **extra) -> str:
    """queue ファイルを 1 本（任意項目つき）。approved なら指紋を正しく埋める。"""
    fm = {"thth": "1", "account": ACCOUNT, "publish_at": "2026-09-09T08:00:00+09:00",
          "status": status, "approved_sha": None,
          "approved_at": "2026-09-08T12:00:00+09:00", "topic": None, "reply_to": None,
          "post_id": None, "posted_at": None}
    fm.update(extra)
    if status == "approved" and fm.get("approved_sha") is None:
        section = queuefile.extract_section(body, "threads")
        fm["approved_sha"] = approval.compute_approved_sha(
            section=section, account=ACCOUNT, reply_to=fm.get("reply_to"),
            topic=fm.get("topic"), publish_at=fm["publish_at"],
            **approval.publish_options(fm))
    os.makedirs(queue_dir, exist_ok=True)
    path = os.path.join(queue_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(_render(fm) + "\n" + body)
    if commit:
        commit_and_push_path(path, message=f"test: {name}")
    return path


def _account_with_token(factory, tmp_path, *, scopes="all", production=True, **overrides):
    """token（偽）と env を持つ台帳。`scopes` は "all" / "unknown" / list。"""
    token_path = str(tmp_path / "fake.token")
    env_path = str(tmp_path / "fake.env")
    token = {"access_token": "fake-token", "user_id": "12345"}
    if scopes == "all":
        from thth import scopes as scopes_mod
        token["scopes"] = list(scopes_mod.DEFAULT_SCOPES)
        token["scopes_source"] = "response"
    elif scopes == "unknown":
        token["scopes"] = None
        token["scopes_source"] = "unknown"
    else:
        token["scopes"] = list(scopes)
        token["scopes_source"] = "response"
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump(token, f)
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("X=1\n")
    return factory(production=production, token=token_path, env=env_path, **overrides)


def _factory_for(base_url):
    def factory(_cfg, _token):
        return threads_mod.ThreadsAdapter(base_url=base_url, access_token="fake-token",
                                          user_id="12345", wait_seconds=0, timeout=2.0)
    return factory


def _ns(**kw) -> argparse.Namespace:
    base = {"reason": None, "by": None, "confirm": None, "json": False}
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def point_at(monkeypatch):
    """`from_account()` が偽サーバを向くようにする（CLI 経路の試験用）。"""
    def _set(base_url):
        monkeypatch.setenv("THTH_THREADS_BASE_URL", str(base_url))
        monkeypatch.setenv("THTH_THREADS_WAIT_SECONDS", "0")
    return _set


def _parse_fm(path: str) -> dict:
    return queuefile.parse(path).front_matter


# ==========================================================================
# 1. 場所
# ==========================================================================

def test_lint_locationがあってlocation_idが無ければ断る(isolated_account, tmp_path):
    path = _write_queue(isolated_account["queue_dir"], "a.md", status="draft",
                        commit=False, location="渋谷駅")
    errors = [m for m in lint_mod.lint_file(path) if not lint_mod.is_warning(m)]
    assert errors, "location: だけの原稿が lint を通ってしまった"
    assert "thth location search" in errors[0]
    assert "location_id" in errors[0]
    assert ACCOUNT in errors[0]


def test_lint_location_idだけでも断る_場所の名前は人が書く(isolated_account):
    path = _write_queue(isolated_account["queue_dir"], "a.md", status="draft",
                        commit=False, location_id="101")
    errors = [m for m in lint_mod.lint_file(path) if not lint_mod.is_warning(m)]
    assert errors and errors[0].startswith("location:")


def test_lint_両方あれば通る(isolated_account):
    path = _write_queue(isolated_account["queue_dir"], "a.md", status="draft",
                        commit=False, location="渋谷駅", location_id="101")
    assert [m for m in lint_mod.lint_file(path) if not lint_mod.is_warning(m)] == []


def test_approveの一段目は場所を見せdigestにlocation_idが入る(isolated_account):
    path = _write_queue(isolated_account["queue_dir"], "a.md", status="draft",
                        location="渋谷駅", location_id="101")
    first = run_thth(["approve", path])
    assert first.returncode == 1
    assert "場所: 渋谷駅（id 101）" in first.stdout
    digest = [l.split(": ", 1)[1] for l in first.stdout.splitlines()
              if l.startswith("digest: ")][0]
    with_loc = approval.compute_approved_sha(
        section=SECTION, account=ACCOUNT, reply_to=None, topic=None,
        publish_at="2026-09-09T08:00:00+09:00", location_id="101")
    without = approval.compute_approved_sha(
        section=SECTION, account=ACCOUNT, reply_to=None, topic=None,
        publish_at="2026-09-09T08:00:00+09:00")
    assert digest == with_loc[:12]
    assert digest != without[:12], "digest に location_id が効いていない"


def test_approve後にlocation_idを変えるとapproval_stale(isolated_account):
    path = _write_queue(isolated_account["queue_dir"], "a.md", status="draft",
                        location="渋谷駅", location_id="101")
    result = approve_via_cli(path)
    assert result.returncode == 0, result.stderr
    fm = _parse_fm(path)
    assert fm["status"] == "approved" and fm["approved_sha"]
    # 承認後に場所の id だけを書き換える → 指紋が食い違う。
    text = open(path, encoding="utf-8").read().replace("location_id: 101", "location_id: 999")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    commit_and_push_path(path, message="tamper")
    account_cfg = accounts_mod.load_account(ACCOUNT)
    files = core.list_queue_files(account_cfg, tree_sha=None)
    res = select_mod.select_one(files, account_name=ACCOUNT, account_cfg=account_cfg,
                                now=NOW, last_post_at=None, recent_texts=set())
    assert res.chosen is None
    assert [r.reason for r in res.rejections if r.file == path] == ["approval_stale"]


def test_publishはlocation_idを投稿作成に付ける(isolated_account_factory, tmp_path):
    account = _account_with_token(isolated_account_factory, tmp_path)
    _write_queue(account["queue_dir"], "a.md", location="渋谷駅", location_id="101")
    with fake_threads_writes_server() as base_url:
        result = core.throw_once(ACCOUNT, production_flag=True,
                                 adapter_factory=_factory_for(base_url), now=NOW)
        assert result.exit_code == 0, result.message
        params = base_url.handler_cls.create_params
    assert params[0]["location_id"] == "101"
    assert "crossreshare_to_ig" not in params[0]


def test_場所も共有も無ければparamsに入らない(isolated_account_factory, tmp_path):
    account = _account_with_token(isolated_account_factory, tmp_path)
    _write_queue(account["queue_dir"], "a.md")
    with fake_threads_writes_server() as base_url:
        result = core.throw_once(ACCOUNT, production_flag=True,
                                 adapter_factory=_factory_for(base_url), now=NOW)
        assert result.exit_code == 0
        params = base_url.handler_cls.create_params
    assert "location_id" not in params[0]
    assert "crossreshare_to_ig" not in params[0]


def test_location_searchは5件までと標準アクセスの注記(isolated_account_factory, tmp_path,
                                                     point_at, capsys):
    _account_with_token(isolated_account_factory, tmp_path)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_location(_ns(action="search", account=ACCOUNT, query="渋谷駅"))
        assert base_url.handler_cls.locations[0]["q"] == "渋谷駅"
        assert base_url.handler_cls.deletes == []
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("  id ") == 5, out
    assert "6 件目" not in out
    assert "id 101" in out and "渋谷駅" in out
    assert "Menlo Park" in out.splitlines()[-1]


def test_location_searchのjson(isolated_account_factory, tmp_path, point_at, capsys):
    _account_with_token(isolated_account_factory, tmp_path)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_location(_ns(action="search", account=ACCOUNT, query="渋谷駅",
                                          json=True))
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0 and len(payload["locations"]) == 5
    assert payload["locations"][0] == {"id": "101", "name": "渋谷駅", "address": None,
                                       "city": "渋谷区", "country": "JP"}
    assert "Menlo Park" in payload["note"]


# ==========================================================================
# 2. 取り下げ
# ==========================================================================

def _posted_queue(account, post_id="555", **extra) -> str:
    return _write_queue(account["queue_dir"], "p.md", status="posted", post_id=post_id,
                        posted_at="2026-09-08T09:00:00+09:00", **extra)


def _sent_record(post_id="555", text=SECTION) -> str:
    state_dir = accounts_mod.state_dir_for(ACCOUNT)
    return sent_mod.write(state_dir, post_id=post_id, text=text,
                          body_hash=approval.compute_body_hash(text),
                          sent_at="2026-09-08T09:00:00+09:00")


def _digest(post_id="555", reason="誤字") -> str:
    return approval.compute_retract_digest(post_id=post_id, account=ACCOUNT, reason=reason)


def test_retractの一段目は本文とURLと理由を見せてDELETEを呼ばない(
        isolated_account_factory, tmp_path, point_at, capsys):
    account = _account_with_token(isolated_account_factory, tmp_path)
    _posted_queue(account)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="555", reason="誤字",
                                         by="masaru"))
        deletes = list(base_url.handler_cls.deletes)
    out = capsys.readouterr().out
    assert rc == 1
    assert deletes == [], "一段目で DELETE が飛んだ"
    assert SECTION in out
    assert "理由: 誤字" in out
    assert "URL :" in out
    assert f"digest: {_digest()}" in out
    assert not _parse_fm(os.path.join(account["queue_dir"], "p.md")).get("retracted_at")


def test_retractの二段目はDELETEを1回呼び3項目を書き戻しsentを残す(
        isolated_account_factory, tmp_path, point_at, capsys):
    account = _account_with_token(isolated_account_factory, tmp_path)
    path = _posted_queue(account)
    sent_path = _sent_record()
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="555", reason="誤字",
                                         by="masaru", confirm=_digest()))
        deletes = list(base_url.handler_cls.deletes)
    out = capsys.readouterr().out
    assert rc == 0, out
    assert deletes == ["/v1.0/555"], "DELETE は 1 回・その post_id だけ"
    fm = _parse_fm(path)
    assert fm["retracted_by"] == "masaru"
    assert fm["retract_reason"] == "誤字"
    assert fm["retracted_at"]
    # 記録は消さない: status・post_id・本文はそのまま。
    assert fm["status"] == "posted" and fm["post_id"] == "555"
    assert SECTION in open(path, encoding="utf-8").read()
    # sent/ は残り、同じ 3 項目が足されている。
    assert os.path.exists(sent_path)
    row = sent_mod.read(accounts_mod.state_dir_for(ACCOUNT), "555")
    assert row["text"] == SECTION and row["retracted_by"] == "masaru"
    # commit・push されている（origin に届いている）。
    import subprocess
    log = subprocess.run(["git", "-C", account["repo_dir"], "log", "--oneline", "origin/main"],
                         capture_output=True, text=True).stdout
    assert "取り下げ" in log


def test_retractの二段目_digest違いはDELETE0回(isolated_account_factory, tmp_path, point_at):
    account = _account_with_token(isolated_account_factory, tmp_path)
    path = _posted_queue(account)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="555", reason="誤字",
                                         by="masaru", confirm="000000000000"))
        deletes = list(base_url.handler_cls.deletes)
    assert rc == 1 and deletes == []
    assert not _parse_fm(path).get("retracted_at")


def test_retractは理由を変えるとdigestが変わる():
    assert _digest(reason="誤字") != _digest(reason="重複")
    # 他の口の digest（送信）と偶然にも一致しない（版の語が入る）。
    assert _digest() != approval.compute_send_digest(text="555", account=ACCOUNT,
                                                     reply_to=None, topic="誤字")


def test_retract_productionでなければ二段目でもDELETEを呼ばない(
        isolated_account_factory, tmp_path, point_at, capsys):
    account = _account_with_token(isolated_account_factory, tmp_path, production=False)
    path = _posted_queue(account)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="555", reason="誤字",
                                         by="masaru", confirm=_digest()))
        deletes = list(base_url.handler_cls.deletes)
    err = capsys.readouterr().err
    assert rc == 1 and deletes == []
    assert "production: true" in err
    assert not _parse_fm(path).get("retracted_at")


def test_retract_他媒体はrc2で未対応(isolated_account_factory, tmp_path, point_at):
    token_path = str(tmp_path / "bsky.token")
    with open(token_path, "w", encoding="utf-8") as f:
        json.dump({"identifier": "x.bsky.social", "app_password": "fake"}, f)
    isolated_account_factory("masaru-bluesky", media="bluesky", production=True,
                             token=token_path)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account="masaru-bluesky", post_id="at://x/y",
                                         reason="誤字", by="masaru", confirm="x"))
        deletes = list(base_url.handler_cls.deletes)
    assert rc == 2 and deletes == []


def test_retract_記録の無いpost_idは取り下げない(isolated_account_factory, tmp_path,
                                                    point_at, capsys):
    _account_with_token(isolated_account_factory, tmp_path)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="999", reason="誤字",
                                         by="masaru", confirm=_digest("999")))
        deletes = list(base_url.handler_cls.deletes)
    assert rc == 1 and deletes == []
    assert "記録が手元にありません" in capsys.readouterr().err


def test_retract_同席送信はsentに3項目を足す(isolated_account_factory, tmp_path, point_at):
    _account_with_token(isolated_account_factory, tmp_path)
    _sent_record(post_id="777")
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="777", reason="重複",
                                         by="masaru", confirm=_digest("777", "重複")))
        deletes = list(base_url.handler_cls.deletes)
    assert rc == 0 and deletes == ["/v1.0/777"]
    row = sent_mod.read(accounts_mod.state_dir_for(ACCOUNT), "777")
    assert row["retract_reason"] == "重複" and row["retracted_by"] == "masaru"
    assert row["text"] == SECTION and row["sent_at"]        # 消していない


def test_retract_既に取り下げ済みなら二度目はDELETEしない(isolated_account_factory,
                                                          tmp_path, point_at):
    account = _account_with_token(isolated_account_factory, tmp_path)
    _posted_queue(account, retracted_at="2026-09-09T00:00:00+09:00", retracted_by="x",
                  retract_reason="y")
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="555", reason="誤字",
                                         by="masaru", confirm=_digest()))
        deletes = list(base_url.handler_cls.deletes)
    assert rc == 1 and deletes == []


def test_retract_successの無い応答は成功と言わず記録も変えない(isolated_account_factory,
                                                              tmp_path, point_at, capsys):
    account = _account_with_token(isolated_account_factory, tmp_path)
    path = _posted_queue(account)
    with fake_threads_writes_server({"delete": "no_success"}) as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="555", reason="誤字",
                                         by="masaru", confirm=_digest()))
    assert rc == 1
    assert "success" in capsys.readouterr().err
    assert not _parse_fm(path).get("retracted_at")


def test_retract_改行入りの理由は断る(isolated_account_factory, tmp_path, point_at):
    account = _account_with_token(isolated_account_factory, tmp_path)
    _posted_queue(account)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="555",
                                         reason="x\nstatus: approved", by="masaru"))
        assert base_url.handler_cls.deletes == []
    assert rc == 2


def test_postsとboardは取り下げ済みを出す(isolated_account_factory, tmp_path, point_at,
                                        capsys):
    account = _account_with_token(isolated_account_factory, tmp_path)
    _posted_queue(account)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="555", reason="誤字",
                                         by="masaru", confirm=_digest()))
        assert rc == 0
        capsys.readouterr()
        from thth import account_report
        result = account_report.recent_posts(ACCOUNT, limit=5)
        assert result["error"] is None
        assert [r["id"] for r in result["retracted"]] == ["555"]
        assert result["retracted"][0]["retract_reason"] == "誤字"
        from thth import cli as cli_mod
        rc = cli_mod.cmd_posts(argparse.Namespace(account=ACCOUNT, limit=5, json=False))
        out = capsys.readouterr().out
        assert rc == 0 and "取り下げ済み: 1 件" in out and "id 555" in out
    summary = report_mod.board_summary(now=NOW)
    row = [r for r in summary["accounts"] if r["account"] == ACCOUNT][0]
    assert row["retracted_count"] == 1


# ==========================================================================
# 3. Instagram 共有
# ==========================================================================

def test_lint_台帳にinstagram_linkedが無ければ共有を断る(isolated_account):
    path = _write_queue(isolated_account["queue_dir"], "a.md", status="draft",
                        commit=False, share_to_instagram="true")
    errors = [m for m in lint_mod.lint_file(path) if not lint_mod.is_warning(m)]
    assert errors and "instagram_linked" in errors[0]


def test_lint_instagram_linkedがあれば通り_falseは何もしない(isolated_account_factory):
    account = isolated_account_factory(instagram_linked=True)
    path = _write_queue(account["queue_dir"], "a.md", status="draft", commit=False,
                        share_to_instagram="true")
    assert [m for m in lint_mod.lint_file(path) if not lint_mod.is_warning(m)] == []
    path2 = _write_queue(account["queue_dir"], "b.md", status="draft", commit=False,
                         share_to_instagram="false")
    assert approval.publish_options(_parse_fm(path2))["share_to_instagram"] is False


def test_approveの一段目はInstagramを見せdigestに入る(isolated_account_factory):
    account = isolated_account_factory(instagram_linked=True)
    path = _write_queue(account["queue_dir"], "a.md", status="draft",
                        share_to_instagram="true")
    first = run_thth(["approve", path])
    assert first.returncode == 1
    assert "Instagram のストーリーズにも出ます" in first.stdout
    digest = [l.split(": ", 1)[1] for l in first.stdout.splitlines()
              if l.startswith("digest: ")][0]
    with_ig = approval.compute_approved_sha(
        section=SECTION, account=ACCOUNT, reply_to=None, topic=None,
        publish_at="2026-09-09T08:00:00+09:00", share_to_instagram=True)
    without = approval.compute_approved_sha(
        section=SECTION, account=ACCOUNT, reply_to=None, topic=None,
        publish_at="2026-09-09T08:00:00+09:00")
    assert digest == with_ig[:12] and digest != without[:12]


def test_publishはcrossreshare_to_igを付ける(isolated_account_factory, tmp_path):
    account = _account_with_token(isolated_account_factory, tmp_path, instagram_linked=True)
    _write_queue(account["queue_dir"], "a.md", share_to_instagram="true")
    with fake_threads_writes_server() as base_url:
        result = core.throw_once(ACCOUNT, production_flag=True,
                                 adapter_factory=_factory_for(base_url), now=NOW)
        assert result.exit_code == 0, result.message
        params = base_url.handler_cls.create_params
    assert params[0]["crossreshare_to_ig"] == "true"


def test_承認後に台帳の連携を外すとselectが落とす(isolated_account_factory, tmp_path):
    account = _account_with_token(isolated_account_factory, tmp_path, instagram_linked=True)
    path = _write_queue(account["queue_dir"], "a.md", share_to_instagram="true")
    cfg_path = os.path.join(account["accounts_dir"], f"{ACCOUNT}.json")
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    cfg["instagram_linked"] = False
    json.dump(cfg, open(cfg_path, "w", encoding="utf-8"))
    account_cfg = accounts_mod.load_account(ACCOUNT)
    files = core.list_queue_files(account_cfg, tree_sha=None)
    res = select_mod.select_one(files, account_name=ACCOUNT, account_cfg=account_cfg,
                                now=NOW, last_post_at=None, recent_texts=set())
    assert res.chosen is None
    assert [r.reason for r in res.rejections if r.file == path] == ["instagram_not_linked"]


# ==========================================================================
# 4. 権限不足（doctor と同じ物差し・rc=2・inflight を残さない）
# ==========================================================================

def test_場所_tokenのscopesに無ければコンテナも作らずrc2でinflight無し(
        isolated_account_factory, tmp_path):
    account = _account_with_token(
        isolated_account_factory, tmp_path,
        scopes=["threads_basic", "threads_content_publish"])
    _write_queue(account["queue_dir"], "a.md", location="渋谷駅", location_id="101")
    with fake_threads_writes_server() as base_url:
        result = core.throw_once(ACCOUNT, production_flag=True,
                                 adapter_factory=_factory_for(base_url), now=NOW)
        params = base_url.handler_cls.create_params
    assert result.exit_code == 2
    assert "threads_location_tagging" in result.message
    assert f"thth auth {ACCOUNT}" in result.message
    assert params == [], "権限が無いのにコンテナ作成の要求が飛んだ"
    assert inflight_mod.read(accounts_mod.state_dir_for(ACCOUNT)) is None


def test_場所_scopesが判らず媒体が権限で断ればrc2でinflight無し(
        isolated_account_factory, tmp_path):
    account = _account_with_token(isolated_account_factory, tmp_path, scopes="unknown")
    _write_queue(account["queue_dir"], "a.md", location="渋谷駅", location_id="101")
    with fake_threads_writes_server({"create": "permission"}) as base_url:
        result = core.throw_once(ACCOUNT, production_flag=True,
                                 adapter_factory=_factory_for(base_url), now=NOW)
    assert result.exit_code == 2
    assert "threads_location_tagging" in result.message
    assert inflight_mod.read(accounts_mod.state_dir_for(ACCOUNT)) is None


def test_場所も共有も無い投稿の権限エラーは今までどおりrc1(isolated_account_factory,
                                                          tmp_path):
    account = _account_with_token(isolated_account_factory, tmp_path, scopes="unknown")
    _write_queue(account["queue_dir"], "a.md")
    with fake_threads_writes_server({"create": "permission"}) as base_url:
        result = core.throw_once(ACCOUNT, production_flag=True,
                                 adapter_factory=_factory_for(base_url), now=NOW)
    assert result.exit_code == 1
    assert inflight_mod.read(accounts_mod.state_dir_for(ACCOUNT)) is None


def test_Instagram_scopesに無ければrc2(isolated_account_factory, tmp_path):
    account = _account_with_token(
        isolated_account_factory, tmp_path, instagram_linked=True,
        scopes=["threads_basic", "threads_content_publish", "threads_location_tagging"])
    _write_queue(account["queue_dir"], "a.md", share_to_instagram="true")
    with fake_threads_writes_server() as base_url:
        result = core.throw_once(ACCOUNT, production_flag=True,
                                 adapter_factory=_factory_for(base_url), now=NOW)
        assert base_url.handler_cls.create_params == []
    assert result.exit_code == 2 and "threads_share_to_instagram" in result.message
    assert inflight_mod.read(accounts_mod.state_dir_for(ACCOUNT)) is None


def test_取り下げ_scopesに無ければDELETEせずrc2(isolated_account_factory, tmp_path,
                                              point_at, capsys):
    account = _account_with_token(
        isolated_account_factory, tmp_path, scopes=["threads_basic", "threads_content_publish"])
    path = _posted_queue(account)
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="555", reason="誤字",
                                         by="masaru", confirm=_digest()))
        deletes = list(base_url.handler_cls.deletes)
    err = capsys.readouterr().err
    assert rc == 2 and deletes == []
    assert "threads_delete" in err and f"thth auth {ACCOUNT}" in err
    assert not _parse_fm(path).get("retracted_at")
    assert inflight_mod.read(accounts_mod.state_dir_for(ACCOUNT)) is None


def test_取り下げ_媒体が権限で断ればrc2で記録を変えない(isolated_account_factory, tmp_path,
                                                       point_at, capsys):
    account = _account_with_token(isolated_account_factory, tmp_path, scopes="unknown")
    path = _posted_queue(account)
    with fake_threads_writes_server({"delete": "permission"}) as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_retract(_ns(account=ACCOUNT, post_id="555", reason="誤字",
                                         by="masaru", confirm=_digest()))
    assert rc == 2 and "threads_delete" in capsys.readouterr().err
    assert not _parse_fm(path).get("retracted_at")


def test_場所検索_scopesに無ければrc2(isolated_account_factory, tmp_path, point_at, capsys):
    _account_with_token(isolated_account_factory, tmp_path, scopes=["threads_basic"])
    with fake_threads_writes_server() as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_location(_ns(action="search", account=ACCOUNT, query="渋谷駅"))
        assert base_url.handler_cls.locations == []
    assert rc == 2 and "threads_location_tagging" in capsys.readouterr().err


def test_場所検索_媒体が権限で断ればrc2(isolated_account_factory, tmp_path, point_at, capsys):
    _account_with_token(isolated_account_factory, tmp_path, scopes="unknown")
    with fake_threads_writes_server({"location": "permission"}) as base_url:
        point_at(base_url)
        rc = retract_cli.cmd_location(_ns(action="search", account=ACCOUNT, query="渋谷駅"))
    assert rc == 2 and "threads_location_tagging" in capsys.readouterr().err


# ==========================================================================
# 5. 指紋の互換・DELETE が飛ぶ経路の唯一性
# ==========================================================================

def test_任意項目が無ければ指紋は5項目のまま():
    """既存の `approved_sha` を 1 つも無効にしない。"""
    kw = dict(section=SECTION, account=ACCOUNT, reply_to=None, topic=None,
              publish_at="2026-09-09T08:00:00+09:00")
    assert approval.compute_approved_sha(**kw) == approval.compute_approved_sha(
        **kw, location_id=None, share_to_instagram=False)
    assert approval.compute_approved_sha(**kw) == approval.compute_approved_sha(
        **kw, location_id="", share_to_instagram="false")


def test_mismatch_fieldsは任意項目の食い違いを名指しする(isolated_account):
    path = _write_queue(isolated_account["queue_dir"], "a.md", commit=False,
                        location="渋谷駅", location_id="101")
    expected = approval.compute_approved_components(
        section=SECTION, account=ACCOUNT, reply_to=None, topic=None,
        publish_at="2026-09-09T08:00:00+09:00", location_id="999")
    assert core._mismatch_fields(path, "threads", expected) == ["location_id"]


def test_DELETEが飛ぶ経路はretract_cliのdelete_postだけ():
    import inspect
    from thth import cli as cli_mod
    from thth import core as core_mod
    from thth import threadthrow, threadrun, maintain, collect, doctor, account_report
    from thth.adapters import bluesky, mastodon
    for mod in (cli_mod, core_mod, threadthrow, threadrun, maintain, collect, doctor,
                account_report, bluesky, mastodon):
        src = inspect.getsource(mod)
        assert ".delete_post(" not in src, f"{mod.__name__} が delete_post を呼んでいる"
        assert 'method="DELETE"' not in src
    src = inspect.getsource(retract_cli)
    assert src.count(".delete_post(") == 1          # 呼び出しは 1 か所（docstring の言及は除く）
    assert inspect.getsource(threads_mod).count('method="DELETE"') == 1


def test_Threadsのdelete_postは数字以外のidを断る():
    adapter = threads_mod.ThreadsAdapter(base_url="http://127.0.0.1:9", access_token="x",
                                         user_id="1", timeout=0.5)
    with pytest.raises(adapter_base.AdapterError):
        adapter.delete_post("../me")
    with pytest.raises(adapter_base.AdapterError):
        adapter.delete_post("555?x=1")


def test_MCPには直接取り下げの口が無い(monkeypatch):
    # 2.12 adds an authenticated human-approval request, never a direct DELETE.
    from tests.test_mcp import _load_server_module
    for key in ('THTH_REPORT_CREDENTIALS', 'THTH_REPORT_TOKEN'):
        monkeypatch.delenv(key, raising=False)
    server = _load_server_module()
    monkeypatch.setattr(server, 'run_cli', lambda *a, **k: pytest.fail('direct delete reached CLI'))
    names = {tool['name'] for tool in server.TOOLS + server.ADMIN_TOOLS}
    for name in ('thth_retract', 'thth_delete', 'thth_retract_request'):
        assert name not in names
        assert server.call_tool(name, {'account': 'alpha', 'post_id': '123'})['isError']
