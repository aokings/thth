"""`thth account <name>`: 1 アカウントの状態を一枚で述べる（masaru 指摘 2026-09-10）。

本番切り替えのとき、統括は「このアカウントは投稿できる状態か」に答えるために
その場限りのスクリプトを書いた。**その問いに答える口が無かった。** ここでは
「できる／できない」と、できない場合の理由が正しく出ることを固定する。
"""
from __future__ import annotations

from pathlib import Path

from tests.conftest import (init_git_pair, make_queue_text, run_git, run_thth,
                            write_queue_file)
from tests.helpers.fake_threads_reader import fake_threads_reader
from thth import account_report, accounts as accounts_mod, inflight as inflight_mod, jst

REL = "docs/sns/queue/a.md"


def _ready_account(tmp_path, factory, **overrides):
    """投稿できる状態を一式そろえる（clone・queue・token・production）。"""
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({"status": "draft"}))
    token = tmp_path / "t.token"
    token.write_text('{"access_token": "T", "obtained_at": "%s", "expires_in": 5184000,'
                     ' "user_id": "1", "username": "nigamilab", "scopes": null}'
                     % jst.iso(jst.now_jst()))
    token.chmod(0o600)
    opts = {"repo_dir": pair["work"], "production": True, "token": str(token)}
    opts.update(overrides)
    return pair, factory(**opts)


def test_全部そろっていれば投稿できると言う(tmp_path, isolated_account_factory):
    pair, account = _ready_account(tmp_path, isolated_account_factory)
    d = account_report.account_detail(account["name"])
    assert d["ready"] is True, d["blockers"]
    assert d["blockers"] == []
    assert d["repo"]["synced"] is True
    assert "投稿できます" in account_report.render(d)


def test_リハーサルなら理由として出る(tmp_path, isolated_account_factory):
    pair, account = _ready_account(tmp_path, isolated_account_factory, production=False)
    d = account_report.account_detail(account["name"])
    assert d["ready"] is False
    assert any("production: false" in b for b in d["blockers"]), d["blockers"]


def test_queue_dirが無ければ場所を示して理由にする(tmp_path, isolated_account_factory):
    pair, account = _ready_account(tmp_path, isolated_account_factory)
    import shutil
    shutil.rmtree(Path(pair["work"]) / "docs" / "sns" / "queue")
    d = account_report.account_detail(account["name"])
    assert d["ready"] is False
    queue_blocker = next(b for b in d["blockers"] if b.startswith("queue:"))
    # 「None がありません」と言わない。直すべき場所を必ず示す。
    assert "docs/sns/queue" in queue_blocker, queue_blocker


def test_tokenが無ければ理由にする(tmp_path, isolated_account_factory):
    pair, account = _ready_account(tmp_path, isolated_account_factory,
                                    token=str(tmp_path / "無い.token"))
    d = account_report.account_detail(account["name"])
    assert d["ready"] is False
    assert any(b.startswith("token: no_token") for b in d["blockers"]), d["blockers"]


def test_pushしていないcommitがあれば理由にする(tmp_path, isolated_account_factory):
    """`thth approve` が push を断られた状態など（HEAD != upstream）。"""
    pair, account = _ready_account(tmp_path, isolated_account_factory)
    (Path(pair["work"]) / "note.txt").write_text("未 push\n")
    run_git(pair["work"], ["add", "note.txt"])
    run_git(pair["work"], ["commit", "-m", "push していない"])
    d = account_report.account_detail(account["name"])
    assert d["ready"] is False
    assert any("upstream" in b for b in d["blockers"]), d["blockers"]


def test_inflightが残っていれば理由にする(tmp_path, isolated_account_factory):
    pair, account = _ready_account(tmp_path, isolated_account_factory)
    inflight_mod.write(accounts_mod.state_dir_for(account["name"]),
                        file="a.md", started=jst.iso())
    d = account_report.account_detail(account["name"])
    assert d["ready"] is False
    assert any(b.startswith("inflight:") for b in d["blockers"]), d["blockers"]


def test_判らないものを無効と言わない(tmp_path, isolated_account_factory, monkeypatch):
    """systemd の無い機械で timer を「無効」と断定しない（規約 12 と同じ筋）。"""
    pair, account = _ready_account(tmp_path, isolated_account_factory)
    monkeypatch.setattr(account_report.shutil, "which", lambda _name: None)
    d = account_report.account_detail(account["name"])
    assert d["timer"]["known"] is False
    assert d["timer"]["enabled"] is None
    assert "判りません" in account_report.render(d)
    # 判らないことは blocker にしない（判らない＝駄目、ではない）
    assert not any("timer" in b for b in d["blockers"])


def test_CLIは投稿できない状態で非ゼロを返す(tmp_path, isolated_account_factory):
    pair, account = _ready_account(tmp_path, isolated_account_factory, production=False)
    result = run_thth(["account", account["name"]])
    assert result.returncode == 1
    assert "投稿できません" in result.stdout


# --- Threads 側の実物を引く（masaru 指摘 2026-09-10「thth通してないものも
#     ひいてきたら加わると良いですね」）

REMOTE_ROWS = [
    {"id": "REMOTE_NEW", "timestamp": "2026-09-10T09:00:00+0000",
     "permalink": "https://www.threads.net/@nigamilab/post/REMOTE_NEW"},
    {"id": "VIA_THTH", "timestamp": "2026-09-09T09:00:00+0000",
     "permalink": "https://www.threads.net/@nigamilab/post/VIA_THTH"},
]


def test_THTHを通していない投稿を見つけて数える(tmp_path, monkeypatch, isolated_account_factory):
    """queue に post_id があるものは「THTH 経由」、無いものは「外で出したもの」。"""
    pair, account = _ready_account(tmp_path, isolated_account_factory)
    posted = Path(pair["work"]) / "docs/sns/queue/posted.md"
    posted.write_text(make_queue_text({
        "status": "posted", "post_id": "VIA_THTH",
        "posted_at": "2026-09-09T18:00:00+09:00"}))
    from tests.conftest import commit_and_push_path
    commit_and_push_path(str(posted), message="THTH が出した分")

    with fake_threads_reader(REMOTE_ROWS) as base_url:
        monkeypatch.setenv("THTH_THREADS_BASE_URL", base_url)
        d = account_report.account_detail(account["name"])

    assert d["remote"]["known"] is True
    assert d["remote"]["count"] == 2
    outside = d["remote"]["outside"]
    assert [row["id"] for row in outside] == ["REMOTE_NEW"], outside
    text = account_report.render(d)
    assert "THTH を通していないもの 1 件" in text, text


def test_引けなければ判らないと言う_0件と言わない(tmp_path, monkeypatch, isolated_account_factory):
    """網に届かない・応答が壊れているときに「0 件」と断定しない（規約 12）。"""
    pair, account = _ready_account(tmp_path, isolated_account_factory)
    monkeypatch.setenv("THTH_THREADS_BASE_URL", "http://127.0.0.1:1")  # 誰もいない
    d = account_report.account_detail(account["name"])
    assert d["remote"]["known"] is False
    assert d["remote"]["count"] is None
    assert "判りません" in account_report.render(d)
    # 引けないことは投稿の可否とは無関係（blocker にしない）
    assert d["ready"] is True, d["blockers"]


def test_no_remoteなら網に出ない(tmp_path, isolated_account_factory):
    pair, account = _ready_account(tmp_path, isolated_account_factory)
    d = account_report.account_detail(account["name"], remote=False)
    assert d["remote"]["known"] is False
    assert "--no-remote" in d["remote"]["message"]


def test_同席専用は予約投稿の仕組みが無くても欠陥にしない(tmp_path, isolated_account_factory):
    """`masaru-threads` のような同席専用（台帳 `scheduled: false`）。

    作った直後、この口は正常な同席専用アカウントを「投稿できません」と言った
    （repo が無い・queue が無い、を欠陥として数えたため）。**使わない仕組みが
    無いことを欠陥にしない。**
    """
    token = tmp_path / "t.token"
    token.write_text('{"access_token": "T", "obtained_at": "%s", "expires_in": 5184000,'
                     ' "user_id": "1", "username": "nigamilab", "scopes": null}'
                     % jst.iso(jst.now_jst()))
    token.chmod(0o600)
    account = isolated_account_factory(
        repo_dir=str(tmp_path / "使わない"), production=True,
        token=str(token), scheduled=False)

    d = account_report.account_detail(account["name"], remote=False)
    assert d["ready"] is True, d["blockers"]
    text = account_report.render(d)
    assert "同席の送信ができます" in text, text
    # 使わないものの**状態**を並べない（説明の 1 行に語が出るのは構わない）
    assert "  queue       :" not in text and "  timer       :" not in text, text
    assert "  repo        :" not in text, text


def test_同席専用でもtokenが無ければ投稿できないと言う(tmp_path, isolated_account_factory):
    account = isolated_account_factory(
        repo_dir=str(tmp_path / "使わない"), production=True,
        token=str(tmp_path / "無い.token"), scheduled=False)
    d = account_report.account_detail(account["name"], remote=False)
    assert d["ready"] is False
    assert any(b.startswith("token:") for b in d["blockers"]), d["blockers"]
