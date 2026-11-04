"""手元に残った THTH 自身の書き戻しの commit を、`sync_repo()` が載せ直して push する（2026-11-04）。

起きたこと（関東の口座）: 書き戻しの commit の push が落ちて手元に残っている（ahead）間に origin が
進む（behind）と、`merge --ff-only @{u}` が通らず、`thth inflight resolve`・`thth pull`・投稿が
全部 `repo_sync_failed` で止まって、道具が自分で解けなかった。手で stash → rebase → push して解いた。

ここでは本物の git（bare の origin・別 clone の seed・THTH が使う work）で、

- 道具の commit だけなら載せ直して push し、`HEAD == @{u}` で通る（枝分かれ・ahead だけ）
- 人の commit が混ざる・upstream と同じファイルに重なる・rebase がぶつかる・push が落ちる・
  rebase の途中・照合（validate）で止めた commit は、`(False, 理由)` で手元を元のまま残す
- stage の区別は前後で保つ
- 下見の経路（`sync_repo_no_push`・dry-run の投稿）は push しない

を確かめる。
"""
from __future__ import annotations

import datetime
import os
from pathlib import Path

import pytest

from tests.conftest import init_git_pair, make_queue_text, run_git
from thth import accounts as accounts_mod
from thth import core
from thth import inflight as inflight_mod
from thth import writeback
from thth.adapters.base import PublishResult

NOW = datetime.datetime.fromisoformat("2026-09-09T10:00:00+09:00")
REL = "docs/sns/queue/a.md"
OWN = "thth: kanto-x a.md を投稿（post_id 111）"


def _head(repo, ref="HEAD"):
    return run_git(repo, ["rev-parse", ref]).stdout.strip()


def _write(repo, rel, text):
    path = Path(repo) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _advance_seed(pair, rel="b.txt", text="upstream\n"):
    """origin を別 clone から進める（手元の書き戻しとは別のファイル）。"""
    run_git(pair["seed"], ["pull", "-q", "--ff-only"])
    _write(pair["seed"], rel, text)
    run_git(pair["seed"], ["add", "--", rel])
    run_git(pair["seed"], ["commit", "-q", "-m", f"elsewhere {rel}"])
    run_git(pair["seed"], ["push", "-q"])
    return _head(pair["bare"], "main")


def _own_commit(pair, message=OWN, post_id="111"):
    """道具の書き戻しと同じ形の commit を手元にだけ積む（front-matter だけ・`commit_local`）。"""
    writeback.rewrite_front_matter(os.path.join(pair["work"], REL), status="posted",
                                   post_id=post_id, posted_at="2026-09-09T10:00:30+09:00")
    ok, err = writeback.commit_local(pair["work"], rel_path=REL, message=message)
    assert ok, err
    return _head(pair["work"])


def _reject_pushes(bare, *, on=True):
    hook = Path(bare) / "hooks" / "pre-receive"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho '拒否します' >&2\nexit 1\n" if on else "#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)


@pytest.fixture
def pair(tmp_path):
    return init_git_pair(tmp_path, seed_content=make_queue_text({"status": "approved"}))


def _posted_on_origin(pair):
    shown = run_git(pair["bare"], ["show", f"main:{REL}"])
    return "status: posted" in shown.stdout and "post_id: 111" in shown.stdout


# ------------------------------------------------------------ 解ける

def test_自分のcommitだけの枝分かれは載せ直してpushして通る(pair):
    own_before = _own_commit(pair)
    upstream = _advance_seed(pair)

    ok, err, sha = writeback.sync_repo(pair["work"])

    assert ok, err
    assert sha == _head(pair["work"]) == _head(pair["work"], "@{u}") == _head(pair["bare"], "main")
    assert sha != own_before  # 載せ直した（upstream の上に）
    assert run_git(pair["work"], ["rev-parse", f"{sha}^"]).stdout.strip() == upstream
    assert _posted_on_origin(pair)
    assert run_git(pair["bare"], ["log", "-1", "--format=%s", "main"]).stdout.strip() == OWN


def test_aheadだけでもpushして通る(pair):
    own = _own_commit(pair)

    ok, err, sha = writeback.sync_repo(pair["work"])

    assert ok, err
    assert sha == own == _head(pair["bare"], "main")  # 並びは書き換えていない
    assert _posted_on_origin(pair)


def test_本物の投稿の書き戻しが残ったまま_originが進んでも解ける(tmp_path, isolated_account_factory):
    """関東の件そのもの。`core` の書き戻し（本物の message）が push を断られて手元に残り、その間に
    origin が進む。以前は以後の同期がすべて `repo_sync_failed` だった。"""
    pair = init_git_pair(tmp_path, seed_content=make_queue_text({"status": "approved"}))
    account = isolated_account_factory(repo_dir=pair["work"], production=True, quiet_hours=None,
                                       min_interval_hours=0)

    class Spy:
        def publish(self, post, **kw):
            return PublishResult("111", None, NOW.isoformat())

    _reject_pushes(pair["bare"])
    first = core.throw_once(account["name"], production_flag=True, adapter_factory=lambda *_: Spy(), now=NOW)
    assert first.action == "post" and first.exit_code != 0, first  # 出たが記録を push できなかった
    assert inflight_mod.read(accounts_mod.state_dir_for(account["name"])) is not None
    _reject_pushes(pair["bare"], on=False)
    _advance_seed(pair)

    ok, err, sha = writeback.sync_repo(pair["work"])

    assert ok, err
    assert sha == _head(pair["bare"], "main") and _posted_on_origin(pair)


def _tracked_loose_file(pair):
    """追跡されているファイルを 1 本、両方の clone に置く（あとで手元だけ未 stage で変える）。"""
    _advance_seed(pair, rel="notes/loose.txt", text="元\n")
    run_git(pair["work"], ["pull", "-q", "--ff-only"])


def test_stageの区別は載せ直しても残る(pair):
    _tracked_loose_file(pair)
    _write(pair["work"], "notes/staged.txt", "途中まで組み立てた\n")
    run_git(pair["work"], ["add", "--", "notes/staged.txt"])
    _write(pair["work"], "notes/loose.txt", "stage していない\n")
    _own_commit(pair)
    _advance_seed(pair)

    ok, err, _sha = writeback.sync_repo(pair["work"])

    assert ok, err
    assert run_git(pair["work"], ["diff", "--cached", "--name-only"]).stdout.split() == ["notes/staged.txt"]
    assert run_git(pair["work"], ["diff", "--name-only"]).stdout.split() == ["notes/loose.txt"]
    assert (Path(pair["work"]) / "notes/loose.txt").read_text(encoding="utf-8") == "stage していない\n"


# ------------------------------------------------------------ 止まる（手元は元のまま）

def _assert_untouched(pair, head_before, origin_before):
    assert _head(pair["work"]) == head_before
    assert _head(pair["bare"], "main") == origin_before
    status = run_git(pair["work"], ["status"]).stdout
    assert "rebase" not in status


def test_人のcommitが混ざると止まる(pair):
    _own_commit(pair)
    _write(pair["work"], "README.md", "手で書いた\n")
    run_git(pair["work"], ["add", "--", "README.md"])
    run_git(pair["work"], ["commit", "-q", "-m", "手で直した"])
    head_before = _head(pair["work"])
    origin_before = _advance_seed(pair)

    ok, err, sha = writeback.sync_repo(pair["work"])

    assert not ok and sha is None
    assert "THTH の書き戻しではないもの" in err and "手で直した" in err
    assert err.endswith("もう一度打ってください")
    _assert_untouched(pair, head_before, origin_before)


def test_人のcommitはaheadだけでも押さない(pair):
    _write(pair["work"], "README.md", "手で書いた\n")
    run_git(pair["work"], ["add", "--", "README.md"])
    run_git(pair["work"], ["commit", "-q", "-m", "手で直した"])
    head_before, origin_before = _head(pair["work"]), _head(pair["bare"], "main")

    ok, err, _sha = writeback.sync_repo(pair["work"])

    assert not ok and "THTH の書き戻しではないもの" in err
    _assert_untouched(pair, head_before, origin_before)


def test_題が同じでも本文に触れたcommitは人のcommitとして扱う(pair):
    path = Path(pair["work"]) / REL
    path.write_text(path.read_text(encoding="utf-8").replace("本文です。", "本文を手で直した。"),
                    encoding="utf-8")
    writeback.rewrite_front_matter(str(path), status="posted", post_id="111",
                                   posted_at="2026-09-09T10:00:30+09:00")
    assert writeback.commit_local(pair["work"], rel_path=REL, message=OWN)[0]
    head_before = _head(pair["work"])
    origin_before = _advance_seed(pair)

    ok, err, _sha = writeback.sync_repo(pair["work"])

    assert not ok and "THTH の書き戻しではないもの" in err
    _assert_untouched(pair, head_before, origin_before)


def test_upstreamが同じファイルを変えていれば止まる(pair):
    _own_commit(pair)
    head_before = _head(pair["work"])
    # 行はぶつからない（本文の行だけ）が、同じ原稿。どちらを残すかは人が決める。
    run_git(pair["seed"], ["pull", "-q", "--ff-only"])
    seed_path = Path(pair["seed"]) / REL
    seed_path.write_text(seed_path.read_text(encoding="utf-8").replace("本文です。", "別 clone で直した本文。"),
                         encoding="utf-8")
    run_git(pair["seed"], ["commit", "-q", "-am", "本文を直す"])
    run_git(pair["seed"], ["push", "-q"])
    origin_before = _head(pair["bare"], "main")

    ok, err, _sha = writeback.sync_repo(pair["work"])

    assert not ok and "同じファイル" in err and REL in err
    _assert_untouched(pair, head_before, origin_before)


def test_rebaseがぶつかれば止まりabortして元に戻る(pair):
    _own_commit(pair)
    head_before = _head(pair["work"])
    origin_before = _advance_seed(pair, rel="new.txt", text="upstream が足した\n")
    # upstream が足すファイルを、手元が追跡せずに持っている（rebase が書けずに止まる）。
    _write(pair["work"], "new.txt", "手元の未追跡\n")

    ok, err, _sha = writeback.sync_repo(pair["work"])

    assert not ok and "push できませんでした（手元は元のままです）" in err
    _assert_untouched(pair, head_before, origin_before)
    assert (Path(pair["work"]) / "new.txt").read_text(encoding="utf-8") == "手元の未追跡\n"


def test_pushが落ちれば止まりHEADもstageも元のまま(pair):
    _tracked_loose_file(pair)
    _write(pair["work"], "notes/loose.txt", "stage していない\n")
    _write(pair["work"], "notes/staged.txt", "途中\n")
    run_git(pair["work"], ["add", "--", "notes/staged.txt"])
    _own_commit(pair)
    head_before = _head(pair["work"])
    origin_before = _advance_seed(pair)
    _reject_pushes(pair["bare"])

    ok, err, _sha = writeback.sync_repo(pair["work"])

    assert not ok and "拒否します" in err
    assert err.endswith("（thth pull <口座> で載せ直せます）")
    _assert_untouched(pair, head_before, origin_before)
    assert run_git(pair["work"], ["diff", "--cached", "--name-only"]).stdout.split() == ["notes/staged.txt"]
    assert run_git(pair["work"], ["diff", "--name-only"]).stdout.split() == ["notes/loose.txt"]


def test_rebaseの途中なら触らない(pair):
    _own_commit(pair)
    _advance_seed(pair)
    git_dir = Path(pair["work"]) / ".git" / "rebase-merge"
    git_dir.mkdir()  # 人か前の実行が残した rebase の途中を模す
    head_before = _head(pair["work"])

    ok, err, _sha = writeback.sync_repo(pair["work"])

    assert not ok and "rebase の途中" in err and "rebase --abort か --continue" in err
    assert _head(pair["work"]) == head_before
    assert git_dir.is_dir()  # 人の rebase を abort しない


def test_照合で止めたcommitは載せ直さない(pair):
    _advance_seed(pair)
    writeback.rewrite_front_matter(os.path.join(pair["work"], REL), status="posted",
                                   post_id="111", posted_at="2026-09-09T10:00:30+09:00")
    with pytest.raises(writeback.PushValidationFailed):
        writeback.commit_and_push(pair["work"], rel_path=REL, message=OWN, validate=lambda: False)
    head_before, origin_before = _head(pair["work"]), _head(pair["bare"], "main")

    ok, err, _sha = writeback.sync_repo(pair["work"])

    assert not ok and "照合" in err and err.endswith("もう一度打ってください")
    _assert_untouched(pair, head_before, origin_before)


# ------------------------------------------------------------ 下見は push しない

def test_下見の同期はpushせずthth_pullを案内する(pair):
    _own_commit(pair)
    head_before = _head(pair["work"])
    origin_before = _advance_seed(pair)

    ok, err, _sha = writeback.sync_repo_no_push(pair["work"])

    assert not ok and "下見なので push しません" in err and "thth pull <口座>" in err
    _assert_untouched(pair, head_before, origin_before)


def test_dry_runの投稿は書き戻しのcommitをpushしない(pair, isolated_account_factory):
    account = isolated_account_factory(repo_dir=pair["work"], production=True, quiet_hours=None,
                                       min_interval_hours=0)
    _own_commit(pair)
    head_before = _head(pair["work"])
    origin_before = _advance_seed(pair)

    result = core.throw_once(account["name"], production_flag=False,
                             adapter_factory=lambda *_: None, now=NOW)

    assert result.error == "repo_sync_failed", result
    _assert_untouched(pair, head_before, origin_before)


# ------------------------------------------------------------ 見分け方の見本

def _commit_fields(pair, message, fields):
    writeback.set_front_matter_fields(os.path.join(pair["work"], REL), fields)
    assert writeback.commit_local(pair["work"], rel_path=REL, message=message)[0]
    sha = _head(pair["work"])
    return sha, run_git(pair["work"], ["rev-parse", f"{sha}^"]).stdout.strip()


def _commit_segment(pair, message):
    """束の原稿（`posts:` の段）に、段の公開と同じ書き方（`bundle.set_post_fields`）で書く。"""
    from thth import bundle
    path = Path(pair["work"]) / REL
    text = path.read_text(encoding="utf-8").replace(
        "\n---\n", "\nposts:\n  - index: 1\n  - index: 2\n---\n", 1)
    path.write_text(text, encoding="utf-8")
    run_git(pair["work"], ["commit", "-q", "-am", "束にする（人）"])
    path.write_text(bundle.set_post_fields(text, 2, {"post_id": "111", "posted_at": "2026-09-09T10:00:30+09:00",
                                                     "run_id": "r1"}), encoding="utf-8")
    assert writeback.commit_local(pair["work"], rel_path=REL, message=message)[0]
    sha = _head(pair["work"])
    return sha, run_git(pair["work"], ["rev-parse", f"{sha}^"]).stdout.strip()


POSTED = {"status": "posted", "post_id": "111", "posted_at": "2026-09-09T10:00:30+09:00"}


@pytest.mark.parametrize("subject, write", [
    ("thth: kanto-x a.md を投稿（post_id 111）", POSTED),
    ("thth: kanto-x a.md を投稿（post_id 111・inflight を解いて記録）", dict(POSTED, reply_to_resolved="9")),
    ("thth: kanto-blog a.md をはてなブログへ（d-1）", dict(POSTED, url="https://example.invalid/e/1")),
    ("THTH: docs/sns/queue/a.md の 2 段目を公開（111）", "segment"),
    ("取り下げ: a.md post_id 111（masaru）",
     {"retracted_at": "2026-09-09T11:00:00+09:00", "retracted_by": "masaru", "retract_reason": "誤字"}),
])
def test_front_matterだけを書き換える書き戻しの題は道具のcommit(pair, subject, write):
    sha, parent = (_commit_segment(pair, subject) if write == "segment"
                   else _commit_fields(pair, subject, write))
    assert writeback._own_writeback_files(pair["work"], sha, [parent]) == {REL}


@pytest.mark.parametrize("write", [
    {"status": "draft"},                                     # status だけ手で変えた（題だけ真似た）
    {"status": "posted"},                                    # posted にしたが post_id が無い
    dict(POSTED, post_id="222"),                             # 題の post_id と違う
    dict(POSTED, approved_by="masaru"),                      # 道具が書かない欄にも触れた
])
def test_題を真似ても道具の書かない欄や値なら人のcommit(pair, write):
    """11/05 Fable の再現: 人が front-matter の status だけ手で変え、題を投稿の形にした commit。"""
    sha, parent = _commit_fields(pair, OWN, write)
    assert writeback._own_writeback_files(pair["work"], sha, [parent]) is None


def test_題を真似てstatusだけ手で変えたcommitは載せ直さない(pair):
    _commit_fields(pair, OWN, {"status": "draft"})
    head_before = _head(pair["work"])
    origin_before = _advance_seed(pair)

    ok, err, _sha = writeback.sync_repo(pair["work"])

    assert not ok and "THTH の書き戻しではないもの" in err
    _assert_untouched(pair, head_before, origin_before)


def test_障害記録のcommitは言い分けて断る(pair):
    _commit_fields(pair, writeback.INCIDENT_SUBJECT, {"thth_run_state": "fail"})
    head_before = _head(pair["work"])
    origin_before = _advance_seed(pair)

    ok, err, _sha = writeback.sync_repo(pair["work"])

    assert not ok and "THTH の障害記録の commit" in err and "書き戻しではないもの" not in err
    assert err.endswith("もう一度打ってください")
    _assert_untouched(pair, head_before, origin_before)


@pytest.mark.parametrize("subject", [
    "thth: record account incident state",  # 自分で OID を控えて押し直す口（載せ直さない）
    "thth: kanto-x b.md を投稿（post_id 111）",  # 題のファイルと触ったファイルが違う
    "thth: kanto-x a.md を投稿（post_id 111）\n\n本文つき",
    "fix typo",
])
def test_形の合わない題は人のcommit(pair, subject):
    sha = _own_commit(pair, message=subject)
    parent = run_git(pair["work"], ["rev-parse", f"{sha}^"]).stdout.strip()
    assert writeback._own_writeback_files(pair["work"], sha, [parent]) is None
