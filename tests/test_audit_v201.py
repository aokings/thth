"""セキュリティ監査 2 回目（v2.0.1 候補・2026-09-14）で見つかった穴。

正本は `docs/検収_セキュリティ監査_v2.0.0_2026-09-14.md` の
「監査 2 回目」節と `docs/設計_v2.0.1_同席送信の採集_2026-09-14.md`。

ここに集めたのは**単発の経路にあった守りが、あとから足した経路に無かった**もの。

- **P1-1**: `thth: 2`（連投）の書き戻しが、媒体の返した `post_id` を素通しで
  `bundle.set_post_fields()` に渡していた（単発の `core._throw_chosen()` には
  検査がある）。改行入りの `id` で束の front-matter に任意の行が入り、以後その
  account の `run` が `sync_repo` で止まる。
- **P2-1**: 置き場の判定が「実在する git repo か」だけだったので、`repo_dir` が
  一時的に見えないと**黙って state へ転ぶ**（v2.0.0 では loud に断っていた）。
- **P2-3**: `thth send` が媒体の `post_id` を検査していなかった。
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest

from tests.conftest import init_git_pair, run_git, run_thth
from thth import accounts as accounts_mod
from thth import bundle as bundle_mod
from thth import inflight as inflight_mod
from thth import threadrun, threadthrow
from thth.adapters.base import PublishResult

from tests.test_thread_publish import REL, SEGMENTS, bundle_text  # noqa: F401

NOW = datetime.datetime.fromisoformat("2026-09-15T19:00:00+09:00")

# 束の front-matter に「別の行」を作る値。`posts:` の段は字下げされているので、
# 改行のあとを字下げ無しで書けば **top-level の行**になる。
注入 = "POST1\nstatus: approved\napproved_by: attacker"


class 悪いidを返す媒体:
    """偽の媒体（乗っ取られた口・間に入った proxy）。**本物は叩かない。**"""

    def __init__(self, post_id):
        self.post_id = post_id
        self.calls = []

    def publish(self, post, dry_run=False, on_container_created=None,
                 before_publish=None):
        if on_container_created:
            on_container_created(f"container-{len(self.calls) + 1}")
        if before_publish is not None:
            veto = before_publish()
            if veto:
                return PublishResult(None, None, NOW.isoformat(), error=str(veto),
                                      failure="publish_vetoed")
        self.calls.append(post)
        return PublishResult(self.post_id, None, NOW.isoformat())


@pytest.fixture
def thread_pair(tmp_path, isolated_account_factory):
    pair = init_git_pair(tmp_path, seed_content=bundle_text(),
                          seed_name="thread.md")
    account = isolated_account_factory(repo_dir=pair["work"], production=True,
                                        quiet_hours=None, min_interval_hours=0)
    return pair, account, Path(pair["work"]) / REL


def _publish(account, adapter, **kw):
    return threadthrow.publish_bundle(
        account["name"], REL, adapter_factory=lambda *_: adapter,
        now=kw.pop("now", NOW), **kw)


# --------------------------------------------------------------- P1-1

@pytest.mark.parametrize("悪いid", [
    注入,
    "POST1\rstatus: approved",
    "POST1\x00",
    "POST1\tstatus: approved",
    "..",
    "P" * 400,
])
def test_P1_1_連投は書けないpost_idを書き戻さずinflightを残す(
        thread_pair, 悪いid):
    """**単発（`core.py`）と同じ扱い**——書き戻さない・再公開しない・inflight を残す。"""
    pair, account, path = thread_pair
    before = path.read_text(encoding="utf-8")
    spy = 悪いidを返す媒体(悪いid)

    results = _publish(account, spy)

    assert [r.action for r in results] == ["unresolved"], [r.reason for r in results]
    assert "post_id" in results[0].reason
    # **1 文字も書いていない**（front-matter に注入行が入っていない）。
    assert path.read_text(encoding="utf-8") == before
    assert "approved_by: attacker" not in path.read_text(encoding="utf-8")

    # inflight が残っている＝次の実行が止まる。
    state_dir = accounts_mod.state_dir_for(account["name"])
    assert inflight_mod.read(state_dir) is not None

    # **再公開しない**（2 回目の実行で publish を呼ばない）。
    again = 悪いidを返す媒体("POST2")
    results2 = _publish(account, again)
    assert again.calls == []
    assert results2[0].action == "stopped"

    # run にも「結果が確定していない」として残る。
    run = threadrun.load(results[0].run_id)
    assert run["posts"][0]["state"] == threadrun.UNRESOLVED


def test_P1_1_まともなidはこれまでどおり書き戻る(thread_pair):
    pair, account, path = thread_pair
    results = _publish(account, 悪いidを返す媒体("POST1"))
    # 1 段目だけ確かめる（同じ id を 3 段に返す偽媒体なので 2 段目は親と衝突しない）。
    assert results[0].action == "published", results[0].reason
    assert "post_id: POST1" in path.read_text(encoding="utf-8")


def test_P1_1_AT_URIのpost_idは連投でも通る(thread_pair):
    """Bluesky の `at://…` は `/` と `:` を含むが**書ける**（弾かない）。"""
    pair, account, path = thread_pair
    uri = "at://did:plc:abc123/app.bsky.feed.post/3kabc"
    results = _publish(account, 悪いidを返す媒体(uri))
    assert results[0].action == "published", results[0].reason
    assert f"post_id: {uri}" in path.read_text(encoding="utf-8")


def test_P1_1_set_post_fieldsの入口が改行入りの値を断る():
    """**検査は 1 か所**（`writeback.check_front_matter_field()`）。

    `threadthrow` を直しただけでは、`set_post_fields()` を呼ぶ別の口が
    増えたときに同じ穴が空く。**書く側の入口でも断る。**
    """
    text = bundle_text()
    for 値 in (注入, "x\ry: z", "x\x00y"):
        with pytest.raises(ValueError):
            bundle_mod.set_post_fields(text, 1, {"post_id": 値})
    with pytest.raises(ValueError):
        bundle_mod.set_post_fields(text, 1, {"post_id\nstatus": "x"})
    # まともな値はこれまでどおり書ける。
    assert "post_id: POST9" in bundle_mod.set_post_fields(
        text, 1, {"post_id": "POST9"})


# --------------------------------------------------------------- P2-1
# 置き場の判定（`repo_dir` が一時的に見えないと黙って state に転んでいた）。

from thth import collect as collect_mod  # noqa: E402
from thth import writeback as writeback_mod  # noqa: E402

# 台帳の名前は**決め打ち**にする（`hash()` はプロセスごとに変わる）。
壊れ方の名前 = {
    "実在": "acct-ok", "git無し": "acct-nogit", "git外し": "acct-moved",
    "読めない": "acct-locked", "相対で実在": "acct-rel", "相対で不在": "acct-relgone",
    "持たない": "acct-none",
}


def _repo(tmp_path, name="clone"):
    """実在する git repo（`.git` つき）を 1 つ作る。"""
    pair = init_git_pair(tmp_path / name, seed_content="---\nthth: 1\n---\n本文\n")
    return pair["work"]


@pytest.fixture
def 壊れ方(tmp_path, thth_root, isolated_account_factory):
    """5 通りの `repo_dir` を作って `(名前, 台帳, 期待する状態)` を返す工場。"""
    戻す = []

    def _make(どれ):
        if どれ == "実在":
            repo = _repo(tmp_path)
        elif どれ == "git無し":
            repo = str(tmp_path / "plain")
            os.makedirs(repo, exist_ok=True)
        elif どれ == "git外し":
            repo = _repo(tmp_path, "moved")
            os.rename(os.path.join(repo, ".git"), str(tmp_path / "退避.git"))
        elif どれ == "読めない":
            repo = _repo(tmp_path, "locked")
            os.chmod(repo, 0o000)
            戻す.append(repo)
        elif どれ == "相対で実在":
            # `$THTH_ROOT` の下に置いた実在の clone を**相対で**指す。
            repo = _repo(Path(thth_root), "rel")
            repo = os.path.relpath(repo, thth_root)
        elif どれ == "相対で不在":
            repo = os.path.join("repos", "どこにも無い")
        elif どれ == "持たない":
            repo = str(tmp_path / "repos" / "_none")
        else:                                       # pragma: no cover
            raise AssertionError(どれ)
        account = isolated_account_factory(
            name=壊れ方の名前[どれ], repo_dir=repo,
            media="bluesky", handle="x.bsky.social", production=True,
            scheduled=False)
        return account
    yield _make
    # chmod 000 のまま tmp_path を片付けられないので戻す。
    for p in 戻す:
        try:
            os.chmod(p, 0o755)
        except OSError:
            pass


@pytest.mark.parametrize("どれ,期待", [
    ("実在", accounts_mod.REPO_OK),
    ("git無し", accounts_mod.REPO_BROKEN),
    ("git外し", accounts_mod.REPO_BROKEN),
    ("読めない", accounts_mod.REPO_BROKEN),
    ("相対で実在", accounts_mod.REPO_OK),
    ("相対で不在", accounts_mod.REPO_BROKEN),
    ("持たない", accounts_mod.REPO_NONE),
])
def test_P2_1_repo_stateが5通りを言い分ける(壊れ方, どれ, 期待):
    account = 壊れ方(どれ)
    cfg = accounts_mod.load_account(account["name"])
    assert accounts_mod.repo_state(cfg) == 期待


@pytest.mark.parametrize("どれ", ["git無し", "git外し", "読めない", "相対で不在"])
def test_P2_1_使えないrepoでは採取せずrcが立つ(壊れ方, どれ):
    """**黙って state に転ばない。** v2.0.0 と同じく loud に断る。"""
    account = 壊れ方(どれ)
    出力 = []
    rc = collect_mod.run_collect(account["name"], adapter=object(), now=NOW,
                                  log=出力.append)
    assert rc != 0
    assert any("repo を同期できないので採取しません" in l for l in 出力), 出力
    # state 側に置き場を作っていない（転んでいない）。
    state = accounts_mod.state_dir_for(account["name"])
    assert not os.path.isdir(os.path.join(state, "data", "sns")), "state に転んだ"


@pytest.mark.parametrize("どれ", ["git無し", "git外し", "読めない", "相対で不在"])
def test_P2_1_使えないrepoでは取り直しもしない(壊れ方, どれ):
    account = 壊れ方(どれ)
    out = collect_mod.refresh_replies(account["name"], adapter=object(), now=NOW,
                                       log=lambda _l: None)
    assert out["skipped"] == "not_synced", out
    assert any("repo を同期できない" in e for e in out["errors"]), out


@pytest.mark.parametrize("どれ,repo側", [
    ("持たない", False),
    ("git無し", True),
    ("git外し", True),
])
def test_P2_1_data_dirsはrepoを持たないときだけstateへ(壊れ方, どれ, repo側):
    account = 壊れ方(どれ)
    cfg = accounts_mod.load_account(account["name"])
    d = accounts_mod.data_dirs(cfg, account["name"])
    state = accounts_mod.state_dir_for(account["name"])
    if repo側:
        assert not d["insights_posts"].startswith(state), d
    else:
        assert d["insights_posts"].startswith(state), d


def test_P2_1_相対のrepo_dirはTHTH_ROOT基準で畳む(壊れ方, thth_root):
    """**cwd 基準にしない**——timer の cwd は `/`、手打ちの cwd は人それぞれ。"""
    account = 壊れ方("相対で実在")
    cfg = accounts_mod.load_account(account["name"])
    解決 = accounts_mod.resolved_repo_dir(cfg)
    assert os.path.isabs(解決) and 解決.startswith(os.path.realpath(thth_root)) \
        or 解決.startswith(thth_root), 解決
    assert os.path.isdir(os.path.join(解決, ".git")) or \
        os.path.exists(os.path.join(解決, ".git"))
    d = accounts_mod.data_dirs(cfg, account["name"])
    assert d["insights_posts"].startswith(解決), d


# --------------------------------------------------------------- P3-1

def test_P3_1_upstream_shaはgitの無いdirで上の階層を拾わない(tmp_path):
    """`git -C` は**上へ遡って repo を探す**——他人の repo の HEAD を返していた。"""
    repo = _repo(tmp_path)
    中の空dir = os.path.join(repo, "repos", "_none")
    os.makedirs(中の空dir, exist_ok=True)
    assert writeback_mod.upstream_sha(repo) is not None, "前提（repo 自体は読める）"
    assert writeback_mod.upstream_sha(中の空dir) is None


# --------------------------------------------------------------- P2-2
# repo 無しの分岐がロックを取らなかった（守っていたのは repo ではなく台帳）。

import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# **遅い偽アダプタ**。1 本目が採っている最中に 2 本目を起こす。
# 同じプロセスの中の thread では、この手の穴は再現しない（`flock` は
# プロセス単位）ので、実プロセスを 2 本並べる
# （`tests/test_share_sync_concurrent.py` と同じ形）。
同時に打つ = """
import json, sys, time
from thth import collect

class のろい媒体:
    CAPABILITIES = frozenset({"views"})

    @classmethod
    def capabilities(cls):
        return set(cls.CAPABILITIES)

    def insights(self, post_id):
        time.sleep(2.0)
        return {"metrics": {"views": 7}, "available": ["views"]}

    def conversation(self, post_id, since=None):
        time.sleep(2.0)
        return [{"message_id": "R1", "text": "返信"}]

出た = []
どちら = sys.argv[2]
if どちら == "collect":
    rc = collect.run_collect(sys.argv[1], adapter=のろい媒体(), log=出た.append)
    print(json.dumps({"rc": rc, "log": 出た}, ensure_ascii=False))
else:
    out = collect.refresh_replies(sys.argv[1], adapter=のろい媒体(),
                                   log=出た.append)
    print(json.dumps({"skipped": out["skipped"], "fetched": out["fetched"]},
                      ensure_ascii=False))
"""


@pytest.fixture
def 同席専用(tmp_path, thth_root, isolated_account_factory):
    """repo を持たない台帳＋`sent/` に 1 件（2 時間前＝1h の刻みを跨いでいる）。"""
    from thth import jst
    from thth import sent as sent_mod

    account = isolated_account_factory(
        name="acct-solo", repo_dir=str(tmp_path / "repos" / "_none"),
        media="bluesky", handle="x.bsky.social", production=True, scheduled=False)
    二時間前 = jst.iso(jst.now_jst() - datetime.timedelta(hours=2))
    sent_mod.write(accounts_mod.state_dir_for(account["name"]),
                    post_id="POST-SOLO", text="本文", body_hash="h",
                    sent_at=二時間前)
    return account


def _2本同時に(account_name: str, どちら: str) -> list:
    env = {**os.environ, "PYTHONPATH": REPO_ROOT}
    procs = [subprocess.Popen(
        [sys.executable, "-c", 同時に打つ, account_name, どちら],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(2)]
    出た = []
    for p in procs:
        out, err = p.communicate()
        assert out.strip(), err
        出た.append(json.loads(out))
    return 出た


def _ndjson(path: str) -> list:
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def test_P2_2_repo無しでも同時のcollectは1本しか採らない(同席専用):
    """**ロックが守っていたのは repo ではなく台帳**（追記専用の ndjson）。"""
    出た = _2本同時に(同席専用["name"], "collect")
    d = accounts_mod.data_dirs(accounts_mod.load_account(同席専用["name"]),
                               同席専用["name"])
    行 = _ndjson(os.path.join(d["insights_posts"], "POST-SOLO.ndjson"))
    assert len(行) == 1, 行
    # 片方は見送っている（busy は待たない・repo 付きと同じ作法）。
    見送り = [r for r in 出た if any("見送ります" in l for l in r["log"])]
    assert len(見送り) == 1, 出た
    assert 見送り[0]["rc"] == 0, 見送り[0]


def test_P2_2_repo無しでも同時のrefreshは1本しか採らない(同席専用):
    出た = _2本同時に(同席専用["name"], "refresh")
    d = accounts_mod.data_dirs(accounts_mod.load_account(同席専用["name"]),
                               同席専用["name"])
    取得 = [r for r in _ndjson(os.path.join(d["replies"], "POST-SOLO.ndjson"))
            if r.get("kind") == "fetch"]
    assert len(取得) == 1, 取得
    assert sorted(r["skipped"] or "" for r in 出た) == ["", "locked"], 出た


# --------------------------------------------------------------- P2-3
# `thth send` が媒体の `post_id` を検査していなかった（不在の様態にはあった）。

from thth import approval as approval_mod  # noqa: E402
from thth import core as core_mod  # noqa: E402
from thth import queuefile  # noqa: E402
from thth import sent as sent_mod  # noqa: E402
from thth.adapters import base as adapter_base  # noqa: E402


def _digest(account_name: str, text: str) -> str:
    return approval_mod.compute_send_digest(
        text=(text or "").strip(), account=account_name, reply_to=None,
        topic=queuefile.normalize_topic(None))


def _send(account_name, post_id, *, log=None):
    class _媒体:
        def publish(self, post, *, dry_run, on_container_created=None):
            return adapter_base.PublishResult(
                post_id=post_id, url=None, ts="2026-09-14T00:00:00+09:00")

    body = "同席で出す 1 本。"
    return core_mod.send_once(account_name, text=body, production_flag=True,
                               confirm=_digest(account_name, body),
                               adapter_factory=lambda *_: _媒体(),
                               log=log or (lambda _l: None))


@pytest.mark.parametrize("悪いid", [
    "P1\nstatus: approved",      # 改行
    "P1\rx",
    "P1\x00",
    "P" * 300,                   # ファイル名にすると長すぎる（OSError が抜けていた）
    "..",
    ".",
])
def test_P2_3_sendは書けないpost_idでinflightを残す(isolated_account_factory, 悪いid):
    """**traceback を出さない**（300 文字の `OSError` が端末まで抜けていた）。"""
    account = isolated_account_factory(production=True)
    r = _send(account["name"], 悪いid)

    assert r.exit_code == 1 and r.action == "inflight", r
    assert r.error == "unusable_post_id", r
    state_dir = accounts_mod.state_dir_for(account["name"])
    # **`sent/` には書かない**（書けない）。
    assert sent_mod.records(state_dir) == []
    # inflight は残る（記録できないまま消すと二重投稿になりうる）。
    assert inflight_mod.read(state_dir) is not None
    # **runs には残る**（何が起きたかは追える）。
    from thth import runs as runs_mod
    rows = runs_mod.read_runs(state_dir)
    assert any(row.get("error") == "unusable_post_id" for row in rows), rows


def test_P2_3_まともなidはこれまでどおりsentに残る(isolated_account_factory):
    account = isolated_account_factory(production=True)
    r = _send(account["name"], "P1")
    assert r.exit_code == 0 and r.post_id == "P1", r
    state_dir = accounts_mod.state_dir_for(account["name"])
    assert [row["post_id"] for row in sent_mod.records(state_dir)] == ["P1"]
    assert inflight_mod.read(state_dir) is None


def test_P2_3_AT_URIのpost_idはsendでも通る(isolated_account_factory):
    account = isolated_account_factory(production=True)
    uri = "at://did:plc:abc123/app.bsky.feed.post/3kabc"
    r = _send(account["name"], uri)
    assert r.exit_code == 0, r
    state_dir = accounts_mod.state_dir_for(account["name"])
    assert [row["post_id"] for row in sent_mod.records(state_dir)] == [uri]


# --------------------------------------------------------------- P2-4
# 署名を確かめられずに配布を見送った回でも board が「署名: 確認」と出していた。

import argparse  # noqa: E402

from thth import cli as cli_mod  # noqa: E402
from thth import report as report_mod  # noqa: E402
from thth import selfupdate  # noqa: E402

from tests.test_selfupdate import _advance_origin, _app_pair  # noqa: E402


def test_P2_4_署名を確かめられなければ取得の記録に残る(tmp_path, monkeypatch):
    """**確かめられなかったことを、別プロセスからも読める形で残す。**"""
    pair = _app_pair(tmp_path)
    _advance_origin(pair)
    monkeypatch.setattr(os, "execve", lambda *a: None)
    monkeypatch.setenv(selfupdate.REQUIRE_SIGNED_ENV, "1")

    selfupdate.pull_and_reexec(["thth"], app_dir=pair["work"], log=lambda _l: None)

    記録 = selfupdate.release_check(pair["work"])
    assert 記録 is not None, "取得試行の記録が無い"
    assert 記録["ok"] is False, 記録
    assert selfupdate.SIGNATURE_ERROR in (記録.get("error") or ""), 記録


@pytest.mark.parametrize("要求,記録,期待", [
    (False, {"ok": True}, "署名: 未確認"),
    (True, {"ok": True}, "署名: 確認"),
    (True, {"ok": False, "error": selfupdate.SIGNATURE_ERROR},
     "署名: 確認できず（取り込んでいません）"),
    # 署名以外の失敗（取りに行けなかった）を「確認できず」に混ぜない。
    (True, {"ok": False, "error": "origin に届きません"}, "署名: 確認"),
])
def test_P2_4_boardの1語が3つに分かれる(monkeypatch, capsys, 要求, 記録, 期待):
    if 要求:
        monkeypatch.setenv(selfupdate.REQUIRE_SIGNED_ENV, "1")
    else:
        monkeypatch.delenv(selfupdate.REQUIRE_SIGNED_ENV, raising=False)
    monkeypatch.setattr(report_mod.selfupdate_mod, "release_check",
                        lambda *a, **k: dict(記録))

    cli_mod.cmd_board(argparse.Namespace(json=False, account=None))
    出た = capsys.readouterr().out
    assert 期待 in 出た, 出た


# --------------------------------------------------------------- P2-5
# 同席専用の採集が自動で回らなかった（人が毎日手で打つ前提になっていた）。

from thth import systemd_gen  # noqa: E402


def test_P2_5_collect_onlyのtimerは採集のserviceを指す():
    cfg = {"account": "masaru-bluesky", "tick_minutes": 10}
    timer = systemd_gen.render_collect_timer(cfg)
    offset = systemd_gen.collect_offset_minutes("masaru-bluesky", 10)
    assert f"OnCalendar=*:{offset}/10" in timer
    assert "Unit=thth-collect@masaru-bluesky.service" in timer
    # **投稿の unit は指さない**（`scheduled: false` の台帳で投稿を回さない）。
    assert "thth@masaru-bluesky.service" not in timer
    assert "Persistent=true" in timer and "WantedBy=timers.target" in timer


def test_P2_5_採集のserviceはcollectだけを呼ぶ():
    service = systemd_gen.render_collect_service()
    assert "ExecStart=/srv/thth/app/bin/thth collect %i" in service
    # 投稿の経路（`bin/thth-run`）は呼ばない。
    assert "thth-run" not in service
    assert "Type=oneshot" in service


def test_P2_5_採集の起点は投稿とずらす():
    """同じ機械で当たりを重ねない（設計 §3.2 と同じ考え）。"""
    同じ = [n for n in ("a-threads", "b-threads", "masaru-bluesky", "c-mastodon")
            if systemd_gen.collect_offset_minutes(n, 10)
            == systemd_gen.offset_minutes(n, 10)]
    assert not 同じ, 同じ


def test_P2_5_CLIがcollect_onlyを出す(isolated_account_factory):
    account = isolated_account_factory(name="solo-bluesky", media="bluesky",
                                        handle="x.bsky.social", scheduled=False)
    out = run_thth(["systemd", account["name"], "--collect-only"])
    assert out.returncode == 0, out.stderr
    assert "Unit=thth-collect@solo-bluesky.service" in out.stdout

    svc = run_thth(["systemd", account["name"], "--collect-only", "--service"])
    assert svc.returncode == 0, svc.stderr
    assert "thth collect %i" in svc.stdout

    # 既定（`--collect-only` 無し）は今までどおり投稿の timer。
    old = run_thth(["systemd", account["name"]])
    assert old.returncode == 0 and "Unit=thth@solo-bluesky.service" in old.stdout


def test_P2_5_boardは最後に採った時刻を1行出す(同席専用, capsys):
    """**「1 度も採っていない」を `0 時間前` と言わない。**"""
    cli_mod.cmd_board(argparse.Namespace(json=False, account=None))
    assert "最後に採ったのは: 未採取" in capsys.readouterr().out

    from thth import jst
    collect_mod.run_collect(同席専用["name"], adapter=_採れる媒体(), now=None,
                             log=lambda _l: None)
    行 = report_mod.board_summary()["accounts"]
    row = next(r for r in 行 if r["account"] == 同席専用["name"])
    assert row["last_collected_at"], row
    assert jst.parse(row["last_collected_at"]) is not None

    cli_mod.cmd_board(argparse.Namespace(json=False, account=None))
    出た = capsys.readouterr().out
    assert "最後に採ったのは: 0 時間前" in 出た, 出た


def _採れる媒体():
    class _媒体:
        CAPABILITIES = frozenset({"views"})

        @classmethod
        def capabilities(cls):
            return set(cls.CAPABILITIES)

        def insights(self, post_id):
            return {"metrics": {"views": 1}, "available": ["views"]}

        def conversation(self, post_id, since=None):
            return []
    return _媒体()
