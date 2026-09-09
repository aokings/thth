"""共通 fixture。account 台帳・queue ファイルの組み立て、THTH_ROOT の隔離を提供する。"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys

import pytest

from thth import approval as approval_mod
from thth import jst
from thth import queuefile as queuefile_mod

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_THTH = os.path.join(REPO_ROOT, "bin", "thth")
FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

DEFAULT_ACCOUNT_NAME = "nigamilab-threads"
# make_queue_text() が既定で仮定する媒体（DEFAULT_ACCOUNT_NAME は threads）。
# approved_sha の自動計算（下記）に使う。
DEFAULT_MEDIA = "threads"

DEFAULT_FM = {
    "thth": "1",
    "account": DEFAULT_ACCOUNT_NAME,
    "publish_at": "2026-09-09T08:00:00+09:00",
    "status": "approved",
    "approved_sha": None,
    "approved_at": "2026-09-08T12:00:00+09:00",
    "topic": None,
    "reply_to": None,
    "post_id": None,
    "posted_at": None,
}

FM_ORDER = ["thth", "account", "publish_at", "status", "approved_sha", "approved_at",
            "topic", "reply_to", "post_id", "posted_at"]

# quiet_hours の既定（22:00〜07:00）の外にある値。frozen_now_jst() の既定値に使う。
FIXED_NOW_JST = datetime.datetime(2026, 9, 9, 10, 0, 0, tzinfo=jst.JST)


@pytest.fixture(autouse=True)
def frozen_now_jst(monkeypatch):
    """`thth.jst.now_jst()` を既定で静かな時間帯の外（2026-09-09 10:00 JST）に固定する。

    なぜ要るか（2026-09-09 に発見・記録は docs/検収_T3a_2026-09-09.md）:
    投稿の経路（`thth.core.throw_once()` 等）は `now` を渡さなければ本物の壁時計
    （`jst.now_jst()`）を読む。このため、テストが `now` を注入しないまま夜間
    （静かな時間帯 22:00〜07:00）に `python3 -m pytest` を走らせると、`quiet_hours`
    が候補を全部落として assertion が必ず failed になっていた
    （`tests/test_roundtrip.py` 1 件・`tests/test_fake_api.py` 4 件）。
    これは「たまに落ちる」より悪い形: 昼は必ず緑・夜は必ず赤になる。日中に開発して
    いれば一度も見えず、夜に pytest を走らせた人だけが「自分の変更で壊した」ように
    見えてしまう（実際には無関係）。

    この fixture は autouse なので、**個々のテストが何もしなくても**既定で
    `now_jst()` が静かな時間帯の外を返す。新しく書かれるテストも自動でこの恩恵を
    受ける（再発防止の本体）。個別のテストが別の時刻（例: 静かな時間帯の中・日を
    またぐ境界）を試したいときは、この fixture が返す setter を呼んで上書きする:

        def test_何か(frozen_now_jst):
            frozen_now_jst(datetime.datetime(2026, 9, 9, 23, 0, 0, tzinfo=jst.JST))
            ...

    ただしサブプロセス越し（`run_thth()` で `bin/thth` を別プロセスとして呼ぶ
    テスト）にはこの monkeypatch は届かない（プロセス境界を越えられない）。そちら
    は `thth throw --now` の `bypass_pace` で個別に対応するか、時刻に依存しない
    形にする。
    """
    state = {"value": FIXED_NOW_JST}

    def fake_now_jst() -> datetime.datetime:
        return state["value"]

    monkeypatch.setattr(jst, "now_jst", fake_now_jst)

    def _set(dt: datetime.datetime) -> None:
        state["value"] = dt

    return _set


def render_front_matter(fm: dict, omit=()) -> str:
    lines = ["---"]
    for key in FM_ORDER:
        if key in omit or key not in fm:
            continue
        value = fm[key]
        lines.append(f"{key}: {value if value is not None else ''}")
    lines.append("---")
    return "\n".join(lines)


def make_queue_text(fm_overrides=None, body="## threads\n\n本文です。\n",
                     omit=(), no_front_matter=False, media=DEFAULT_MEDIA) -> str:
    """queue ファイルのテキストを組み立てる。

    `status: approved`（既定）で、呼び出し側が `approved_sha` を明示していなければ、
    いまの内容（`body`・`account`・`reply_to`・`topic`・`publish_at`）から
    `approval.compute_approved_sha()` で機械的に計算して埋める（外部レビュー §1・
    受け入れ 1〜4）。**ほとんどのテストは「承認された本文がそのまま出る」ことを
    前提にしている**ので、テストごとに手で計算させない。approved_sha を意図的に
    古くしたい・欠落させたいテスト（`approval_stale` を確かめるテスト）は
    `fm_overrides={"approved_sha": "...", ...}`（None を含む）を明示して渡すこと
    ——`fm_overrides` に `approved_sha` キーがあれば自動計算しない。
    """
    if no_front_matter:
        return body
    fm = dict(DEFAULT_FM)
    overrides = dict(fm_overrides or {})
    fm.update(overrides)
    if fm.get("status") == "approved" and "approved_sha" not in overrides:
        publish_at = fm.get("publish_at")
        if publish_at:
            section = queuefile_mod.extract_section(body, media)
            if section is not None:
                try:
                    fm["approved_sha"] = approval_mod.compute_approved_sha(
                        section=section, account=fm.get("account"),
                        reply_to=fm.get("reply_to"), topic=fm.get("topic"),
                        publish_at=publish_at)
                except ValueError:
                    pass  # publish_at が壊れている型外テスト等: 計算できないので触らない
    return render_front_matter(fm, omit=omit) + "\n" + body


def write_queue_file(dir_path: str, name: str, **kwargs) -> str:
    os.makedirs(dir_path, exist_ok=True)
    path = os.path.join(dir_path, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(make_queue_text(**kwargs))
    return path


@pytest.fixture
def thth_root(tmp_path, monkeypatch):
    """THTH_ROOT を隔離したディレクトリに向ける（state/lock がテスト間で衝突しない）。"""
    root = tmp_path / "thth_root"
    root.mkdir()
    monkeypatch.setenv("THTH_ROOT", str(root))
    return str(root)


def init_real_repo(tmp_path, subdir: str) -> str:
    """queue_dir を持つ、利用者 repo 相当の**実 git repo**（bare origin 付き）を作る。

    `writeback.sync_repo()` の fail-closed 化（外部レビュー第 3 巡 P1）で、
    `repo_dir` は「存在しない」以外は git repo・origin・同期成功が要る形に
    なった。本番の利用者 repo は元々そうなっている（`accounts/*.json` の
    `repo_dir` はすべて実クローン）ので、テストの既定 repo_dir もそれに合わせる。
    `init_git_pair()` と同じ流儀（bare → seed → clone）だが、置くのは
    queue_dir 直下の `.gitkeep` だけ（各テストは `write_queue_file()` で queue
    ファイルを直接・未追跡のまま書き込む。push しないので `.md` と衝突しない）。
    フェッチしても新しい commit は無いので、同期は毎回 trivially 成功する。
    """
    pair = init_git_pair(tmp_path / subdir, seed_content="", seed_name=".gitkeep")
    return pair["work"]


@pytest.fixture
def nigamilab_repo(tmp_path):
    """queue_dir を持つ、利用者 repo 相当のディレクトリ（実 git repo。`init_real_repo()` 参照）。"""
    return init_real_repo(tmp_path, "nigamilab")


def make_account_json(accounts_dir: str, name: str, *, repo_dir: str, **overrides) -> str:
    data = {
        "account": name,
        "project": "nigamilab",
        "media": "threads",
        "handle": "nigamilab",
        "user_id": "123",
        "repo_dir": repo_dir,
        "queue_dir": "docs/sns/queue",
        "replies_dir": "data/sns/replies",
        "quiet_hours": ["22:00", "07:00"],
        "min_interval_hours": 6,
        "collect_days": 14,
        "hashtags": False,
        "stale_days": 7,
        "env": os.path.join(repo_dir, "..", "nonexistent.env"),
        "token": os.path.join(repo_dir, "..", "nonexistent.token"),
        "ping": "wrapper",
        "timeout": 300,
        "dry_run_env": "THTH_DRY_RUN",
        "production": False,
    }
    data.update(overrides)
    os.makedirs(accounts_dir, exist_ok=True)
    path = os.path.join(accounts_dir, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


@pytest.fixture
def isolated_account_factory(thth_root, nigamilab_repo, monkeypatch):
    """`isolated_account` の中身をテストごとの台帳上書きに対応させたファクトリ。

    `thth.accounts.APP_DIR` を差し替えて、実物の `accounts/nigamilab-threads.json`
    （repo_dir が `$THTH_ROOT/repos/nigamilab` の実運用値）を汚さずにテストする。
    """
    import thth.accounts as accounts_mod

    fake_app_dir = os.path.dirname(thth_root)
    accounts_dir = os.path.join(fake_app_dir, "accounts")
    monkeypatch.setattr(accounts_mod, "APP_DIR", fake_app_dir)
    # subprocess 越し（bin/thth を別プロセスで呼ぶロック・inflight・round-trip 系の
    # テスト）でも同じ隔離した accounts/ を見るように、環境変数でも渡しておく。
    monkeypatch.setenv("THTH_APP_DIR", fake_app_dir)

    def _make(name: str = DEFAULT_ACCOUNT_NAME, *, repo_dir: str | None = None, **overrides) -> dict:
        repo_dir = repo_dir or nigamilab_repo
        make_account_json(accounts_dir, name, repo_dir=repo_dir, **overrides)
        return {
            "name": name,
            "accounts_dir": accounts_dir,
            "repo_dir": repo_dir,
            "queue_dir": os.path.join(repo_dir, "docs", "sns", "queue"),
        }

    return _make


@pytest.fixture
def isolated_account(isolated_account_factory):
    """既定の台帳（production: false・env/token 無し）1 本を隔離した状態で返す。"""
    return isolated_account_factory()


def run_thth(args, env=None, cwd=None) -> subprocess.CompletedProcess:
    """`bin/thth` をサブプロセスで呼ぶ（プロセス境界が要るテスト用: ロック・inflight）。"""
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, BIN_THTH, *args],
        capture_output=True, text=True, env=full_env, cwd=cwd,
    )


def run_git(repo_dir: str, args: list) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result


def init_git_pair(tmp_path, *, seed_content: str, seed_name: str = "a.md"):
    """`git init --bare` を origin にした隔離 clone を作る（発注 §5 受け入れ 11）。

    origin（bare）→ seed（初回 commit・push）→ work（origin から clone。THTH が
    ここで書き戻す）の 3 者を作り、`work` の queue_dir パスを返す。
    """
    bare = str(tmp_path / "origin.git")
    seed = str(tmp_path / "seed")
    work = str(tmp_path / "work")

    subprocess.run(["git", "init", "--bare", "-b", "main", bare], check=True,
                    capture_output=True, text=True)
    subprocess.run(["git", "init", "-b", "main", seed], check=True, capture_output=True, text=True)
    run_git(seed, ["config", "user.email", "thth-test@example.invalid"])
    run_git(seed, ["config", "user.name", "thth-test"])

    queue_dir = os.path.join(seed, "docs", "sns", "queue")
    os.makedirs(queue_dir, exist_ok=True)
    with open(os.path.join(queue_dir, seed_name), "w", encoding="utf-8") as f:
        f.write(seed_content)
    run_git(seed, ["add", "-A"])
    run_git(seed, ["commit", "-m", "seed"])
    run_git(seed, ["remote", "add", "origin", bare])
    run_git(seed, ["push", "-u", "origin", "main"])

    subprocess.run(["git", "clone", bare, work], check=True, capture_output=True, text=True)
    run_git(work, ["config", "user.email", "thth-test@example.invalid"])
    run_git(work, ["config", "user.name", "thth-test"])

    return {"bare": bare, "seed": seed, "work": work,
            "queue_dir": os.path.join(work, "docs", "sns", "queue")}
