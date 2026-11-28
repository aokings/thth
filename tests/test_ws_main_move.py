"""括りの段 5 の柵と通し稽古: main を `ws/main/` へ移す台本（tools/ws_move_main.py）。

設計: docs/設計_THTHアカウント（括り）_叩き台_2026-11-26.md §3 の段 5・§8 R2・R11。
手順書: docs/手順_括りの段5_mainを移す夜_2026-11-27.md。

- 通し稽古: 本物に似せた置き場（main の口座 3・括り garden・レーンの鍵の表・承認の台帳・止め・管理された repo の
  絶対の origin・絶対の repo_dir 1 本）を tmp に作り、移す → 確かめ → 戻す → 確かめ。
- 途中で落ちた（各段のあと・段の途中）: やり直しで「落ちずに移した姿」と同じになり、戻すで元の姿に戻る。
- 確かめ（timer が動いている・鍵を握っている・分からない物・もう移した）で、何も書かずに止まる。
- R2: 移したあとの置き場に古い版（origin/main）を当てると、開けない印で止まり、書かず・押し上げない。
  印を置かない移し方だと、古い版は黙って口座 0 で動く（印が要る理由の証拠）。
- 新しい版は移したあとの置き場を main として読み、移す途中は `workspace_moving` で断る（判定は 1 か所）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import io
from pathlib import Path

import pytest

from tests.conftest import make_account_json

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import ws_move_main as mv  # noqa: E402

ACCOUNTS = ("alpha-main", "beta-main", "kappa-main")
OWNER = "masaru"
KEY = "ab" * 32


def _write(path: Path, text: str, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def build(base: Path) -> Path:
    """本物に似せた置き場（/srv/thth の形）。"""
    root = base / "srv"
    root.mkdir(mode=0o700)
    (root / "app").mkdir()
    (root / "venv").mkdir()
    expires = "2030-01-01T00:00:00+00:00"  # 決まった値（稽古の姿を比べるため）
    _write(root / "private" / "report-credentials.json", json.dumps({
        "schema_version": 1, "root": str(root),
        "credentials": [{"sha256": KEY, "expires_at": expires, "revoked": False,
                         "accounts": {"alpha-main": "alphaproj"}, "scope": "user", "writes": True, "actor": OWNER}]}))
    _write(root / "private" / "lane-keys.json", json.dumps({"schema": "thth.lane-keys/1", "keys": []}))
    _write(root / "workspaces.json", json.dumps({"schema": "thth.workspaces/1", "unlisted": "main",
                                                 "workspaces": {"main": {"owner": OWNER, "lead": "media-hub"},
                                                                "garden": {"owner": OWNER, "lead": "gamma"}},
                                                 "media": {}}))
    _write(root / "notifications.json", "{}")
    # 口座: alpha は $THTH_ROOT の repo、beta は根からの絶対の repo（直す 1 本）、kappa は repo を持たない。
    accounts = root / "accounts"
    (root / "repos" / "alpha").mkdir(parents=True)
    (root / "repos" / "beta").mkdir(parents=True)
    (root / "repos" / "_none").mkdir(parents=True)
    make_account_json(str(accounts), "alpha-main", repo_dir="$THTH_ROOT/repos/alpha", project="alphaproj",
                      env="~/.config/thth/alpha-main.env", token="~/.config/thth/alpha-main.token")
    make_account_json(str(accounts), "beta-main", repo_dir=str(root / "repos" / "beta"), project="betaproj",
                      env="~/.config/thth/beta-main.env", token="~/.config/thth/beta-main.token")
    make_account_json(str(accounts), "kappa-main", repo_dir="$THTH_ROOT/repos/_none", project="kappaproj",
                      env="$THTH_ROOT/private/kappa.env", token="~/.config/thth/kappa-main.token")
    # 管理された repo（origin が state の下の絶対の道）
    managed = root / "repos" / "_server" / "alpha-main" / ".git"
    _write(managed / "config", "[core]\n\trepositoryformatversion = 0\n\tbare = false\n"
           f"[remote \"origin\"]\n\turl = {root}/state/alpha-main/git-origin.git\n"
           "\tfetch = +refs/heads/*:refs/remotes/origin/*\n[branch \"main\"]\n\tremote = origin\n\tmerge = refs/heads/main\n",
           0o644)
    (root / "principals").mkdir()
    _write(root / "principals" / "masaru-note.json", json.dumps({"principal": "masaru-note", "token": "~/.config/thth/n.token"}))
    for name in ACCOUNTS:
        st = root / "state" / name
        st.mkdir(parents=True)
        _write(st / "approvals.json", json.dumps({"approved": {"queue/a.md": {"by": OWNER}}}))
        _write(st / "holds.json", json.dumps({"holds": []}))
        _write(st / "lock", "", 0o644)
        (root / "logs" / name).mkdir(parents=True)
    (root / "state" / "alpha-main" / "git-origin.git").mkdir()
    # 記録が絶対の道を持つ（本番の approvals.json・holds.json・inflight.json と同じ形・記録の道で書き替える）
    _write(root / "state" / "alpha-main" / "inflight.json", json.dumps({"file": f"{root}/repos/alpha/queue/a.md"}))
    for common in ("_admin", "_webhook", "_meta", "_media"):
        _write(root / "state" / common / "x.json", json.dumps({"common": common}))
    _write(root / "state" / "_app.lock", "", 0o644)
    _write(root / "state" / "worker.json", json.dumps({"version": "6.0.0"}))
    for ws_thing in ("_plaza", "_leave", "_auth", "_map", "_reports"):
        _write(root / "state" / ws_thing / "r.json", json.dumps({"ws": ws_thing}))
    _write(root / "state" / "_repos" / "beta-0123456789ab.lock", "", 0o644)
    _write(root / "state" / "topics.json", "{}")
    # 2 つ目の括り（段 4 で開いた物）
    garden = root / "ws" / "garden"
    make_account_json(str(garden / "accounts"), "gamma-garden", repo_dir="$THTH_ROOT/repos/gamma", project="gammaproj",
                      env="$THTH_ROOT/secrets/gamma-garden.env", token="$THTH_ROOT/secrets/gamma-garden.token")
    (garden / "repos" / "gamma").mkdir(parents=True)
    (garden / "state" / "gamma-garden").mkdir(parents=True)
    _write(garden / "private" / "lane-keys.json", json.dumps({"schema": "thth.lane-keys/1", "keys": []}))
    for dirpath, dirnames, _ in os.walk(root):  # 本番と同じく、置き場のフォルダは持ち主だけ（server_files の検め）
        for n in dirnames:
            os.chmod(os.path.join(dirpath, n), 0o700)
    return root


def _open_tombs(root: Path):
    for dirpath, dirnames, _ in os.walk(root):
        for n in dirnames:
            p = Path(dirpath) / n
            if not p.is_symlink() and stat.S_IMODE(p.lstat().st_mode) == 0:
                p.chmod(0o700)


@pytest.fixture(autouse=True)
def _reopen(tmp_path):
    yield
    _open_tombs(tmp_path)  # 後片付けは pytest に任せる（消さない）。開けない印だけ開けておく


@pytest.fixture
def place(tmp_path):
    return build(tmp_path)


def snap(root: Path, *, skip_move_dir=True) -> dict:
    """置き場の姿（相対の道 → 種類・mode・中身の hash）。根の道は <ROOT> に畳む。"""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        keep = []
        for n in sorted(dirnames):
            rel = os.path.normpath(os.path.join(rel_dir, n))
            if skip_move_dir and rel.startswith(("ws/.main-move",)):
                continue
            mode = stat.S_IMODE(os.lstat(os.path.join(dirpath, n)).st_mode)
            out[rel] = ("dir", mode)
            if mode:
                keep.append(n)
        dirnames[:] = keep
        for n in sorted(filenames):
            rel = os.path.normpath(os.path.join(rel_dir, n))
            p = os.path.join(dirpath, n)
            data = Path(p).read_bytes().replace(str(root).encode(), b"<ROOT>")
            out[rel] = ("file", stat.S_IMODE(os.lstat(p).st_mode), hashlib.sha256(data).hexdigest())
    return out


def run(cmd, root, *extra):
    return mv.main([cmd, "--root", str(root), "--no-units-check", *extra])


# --------------------------------------------------------------------------- 形と印

def test_marks_match_the_code():
    from thth import accounts
    assert (mv.MOVE_DIR, mv.MOVING, mv.MAIN) == (accounts.MAIN_MOVE_DIR, accounts.MAIN_MOVING_MARK, accounts.MAIN_WS)


def test_r11_default_main_has_a_version_deadline():
    """R11: 名指しの無い打ち方を main と読むのは MAIN_DEFAULT_UNTIL の版まで。届いたら段 6 の替えを入れる。"""
    from thth import accounts
    version = tuple(int(x) for x in (REPO / "thth" / "VERSION").read_text().split()[0].split("."))
    until = tuple(int(x) for x in accounts.MAIN_DEFAULT_UNTIL.split("."))
    assert version < until, (
        f"版 {version} が R11 の期限 {accounts.MAIN_DEFAULT_UNTIL} に届いた。段 6（VM の上では括りの名指しが要る）を入れて、"
        "current_workspace() の既定 main を外す")


def test_check_lists_the_plan_and_writes_nothing(place, capsys):
    before = snap(place)
    assert run("check", place) == 0
    out = capsys.readouterr().out
    assert "移す accounts → ws/main/accounts" in out
    assert "移す state/alpha-main → ws/main/state/alpha-main" in out
    assert "移す private/lane-keys.json" in out
    assert "state/_admin" not in out and "移す private/report-credentials" not in out
    assert f"{place}/repos/beta" in out and "$THTH_ROOT/repos/beta" in out
    assert "$THTH_ROOT/private/kappa.env" in out  # 共通の物を $THTH_ROOT で指す道は、絶対の道に替える
    assert snap(place) == before


def test_dry_run_writes_nothing(place, capsys):
    before = snap(place, skip_move_dir=False)
    assert run("move", place, "--dry-run") == 0
    assert "乾いた打ち方" in capsys.readouterr().out
    assert snap(place, skip_move_dir=False) == before
    assert run("move", place) == 0
    after = snap(place, skip_move_dir=False)
    assert run("back", place, "--dry-run") == 0
    assert snap(place, skip_move_dir=False) == after


# --------------------------------------------------------------------------- 通し稽古

def _check_moved(root: Path, monkeypatch, tmp_path):
    """新しい版が、移したあとの置き場を main として読む。"""
    monkeypatch.setenv("THTH_ROOT", str(root))
    for name in ("THTH_WS", "THTH_ACCOUNTS_DIR", "THTH_WS_MAP"):
        monkeypatch.delenv(name, raising=False)
    from thth import accounts, lane_keys
    monkeypatch.setattr(accounts, "APP_DIR", str(tmp_path / "noapp"))
    main = root / "ws" / "main"
    assert accounts.ws_root() == str(main)
    assert accounts.common_root() == str(root)
    assert accounts.list_account_names() == sorted(ACCOUNTS)
    beta = accounts.load_account("beta-main")
    assert beta["repo_dir"] == str(main / "repos" / "beta") and os.path.isdir(beta["repo_dir"])
    assert accounts.load_account("alpha-main")["repo_dir"] == str(main / "repos" / "alpha")
    assert accounts.load_account("kappa-main")["env"] == str(root / "private" / "kappa.env")  # 共通の物は同じ所
    assert accounts.state_dir_for("alpha-main") == str(main / "state" / "alpha-main")
    assert json.loads((main / "state" / "alpha-main" / "approvals.json").read_text())["approved"]
    assert lane_keys.path_for("main") == main / "private" / "lane-keys.json" and lane_keys.path_for("main").is_file()
    assert accounts.workspace_of_account("gamma-garden") == "garden"
    assert accounts.workspace_of_account("alpha-main") == "main"
    assert accounts.app_lock_path() == str(root / "state" / "_app.lock")
    cfg = (main / "repos" / "_server" / "alpha-main" / ".git" / "config").read_text()
    assert f"url = {main}/state/alpha-main/git-origin.git\n" in cfg
    for gone in ("accounts", "repos", "logs", "principals", "state/alpha-main", "state/_plaza", "state/topics.json"):
        assert stat.S_IMODE((root / gone).lstat().st_mode) == 0, gone  # 開けない印
    for kept in ("private/report-credentials.json", "state/_admin/x.json", "state/worker.json", "workspaces.json"):
        assert (root / kept).is_file(), kept
    assert not (root / "private" / "lane-keys.json").exists()
    assert not (root / "ws" / ".main-staging").exists() and not (root / "ws" / ".main-move" / "MOVING").exists()


def test_round_trip_move_then_back(place, monkeypatch, tmp_path, capsys):
    before = snap(place)
    beta_bytes = (place / "accounts" / "beta-main.json").read_bytes()
    assert run("move", place) == 0
    assert run("back", place) == 0
    assert snap(place) == before
    assert (place / "accounts" / "beta-main.json").read_bytes() == beta_bytes
    # もう一度移す（帳面は退けて新しく）→ 新しい版で読む → 戻す
    assert run("move", place) == 0
    assert [p.name for p in (place / "ws").iterdir() if p.name.startswith(".main-move-")]
    _check_moved(place, monkeypatch, tmp_path)
    from thth import cli
    capsys.readouterr()
    try:
        cli.main(["board", "--json"])
    except SystemExit:
        pass
    board = capsys.readouterr().out
    assert all(name in board for name in ACCOUNTS) and "gamma-garden" not in board
    assert run("status", place) == 0
    assert "state=moved" in capsys.readouterr().out
    assert run("back", place) == 0
    after = snap(place)
    # 違うのは、移していた間に新しい版が置いた止めの鍵のファイルだけ（main の物として根へ戻る）
    extra = {k for k in set(after) ^ set(before)}
    assert extra and all(k.startswith("state/_leave/") and k.endswith(".lock") for k in extra), extra
    assert {k: v for k, v in after.items() if k not in extra} == before


def test_data_made_after_the_move_goes_back_with_main(place):
    before = snap(place)
    assert run("move", place) == 0
    main = place / "ws" / "main"
    _write(main / "state" / "alpha-main" / "sent.json", "[1]")
    (main / "state" / "zeta-main").mkdir()
    _write(main / "register_runs" / "r.json", "{}")
    assert run("back", place) == 0
    after = snap(place)
    assert (place / "state" / "alpha-main" / "sent.json").read_text() == "[1]"
    assert (place / "state" / "zeta-main").is_dir() and (place / "register_runs" / "r.json").is_file()
    extra = set(after) - set(before)
    assert extra == {"state/alpha-main/sent.json", "state/zeta-main", "register_runs", "register_runs/r.json"}


def _reference(tmp_path):
    other = tmp_path / "ref"
    other.mkdir()
    root = build(other)
    assert run("move", root) == 0
    got = snap(root)
    _open_tombs(other)
    return got


MOVE_STOPS = ["begin", "move:1", "move:9", "move", "rewrite:1", "rewrite", "records", "tomb:1", "tomb:5", "tomb", "commit"]
BACK_STOPS = ["back_begin", "uncommit", "untomb:1", "untomb:7", "untomb", "unrewrite:1", "unrewrite", "unrecords",
              "moveback:1", "moveback:6", "moveback"]


@pytest.mark.parametrize("stop", MOVE_STOPS)
def test_move_interrupted_then_redo(place, tmp_path, stop, monkeypatch):
    want = _reference(tmp_path)
    assert run("move", place, "--stop-after", stop) == 3
    from thth import accounts
    monkeypatch.setenv("THTH_ROOT", str(place))
    monkeypatch.delenv("THTH_WS", raising=False)
    if stop != "commit":
        # 途中の置き場を、新しい版は main として読まない（判定は印の 1 か所）
        with pytest.raises(accounts.WorkspaceError, match="workspace_moving"):
            accounts.ws_root()
    assert run("move", place) == 0
    assert snap(place) == want


@pytest.mark.parametrize("stop", MOVE_STOPS)
def test_move_interrupted_then_back(place, stop):
    before = snap(place)
    assert run("move", place, "--stop-after", stop) == 3
    assert run("back", place) == 0
    assert snap(place) == before


@pytest.mark.parametrize("stop", BACK_STOPS)
def test_back_interrupted_then_redo(place, stop, monkeypatch):
    before = snap(place)
    assert run("move", place) == 0
    assert run("back", place, "--stop-after", stop) == 3
    from thth import accounts
    monkeypatch.setenv("THTH_ROOT", str(place))
    monkeypatch.delenv("THTH_WS", raising=False)
    with pytest.raises(accounts.WorkspaceError, match="workspace_moving"):
        accounts.ws_root()
    assert run("back", place) == 0
    assert snap(place) == before


@pytest.mark.parametrize("stop", ["begin", "move:3", "tomb:2", "commit"])
def test_interrupted_move_then_back_interrupted_then_back(place, stop):
    before = snap(place)
    assert run("move", place, "--stop-after", stop) == 3
    assert run("back", place, "--stop-after", "untomb:1") == 3
    assert run("back", place) == 0
    assert snap(place) == before


# --------------------------------------------------------------------------- 確かめで止まる

def test_refuses_while_a_unit_is_active(place, tmp_path, capsys):
    fake = tmp_path / "systemctl"
    fake.write_text("#!/bin/sh\necho 'thth-worker.service loaded active running THTH worker'\n"
                    "echo 'thth@alpha-main.timer loaded inactive dead THTH'\n")
    fake.chmod(0o755)
    before = snap(place, skip_move_dir=False)
    assert mv.main(["move", "--root", str(place), "--systemctl", str(fake)]) == 1
    out = capsys.readouterr().out
    assert "thth-worker.service が active" in out and "thth@alpha-main.timer" not in out
    assert snap(place, skip_move_dir=False) == before
    quiet = tmp_path / "systemctl-quiet"
    quiet.write_text("#!/bin/sh\necho 'thth-worker.service loaded inactive dead THTH worker'\n")
    quiet.chmod(0o755)
    assert mv.main(["move", "--root", str(place), "--systemctl", str(quiet)]) == 0


def test_refuses_without_systemctl_unless_told(place, tmp_path):
    before = snap(place, skip_move_dir=False)
    assert mv.main(["move", "--root", str(place), "--systemctl", str(tmp_path / "nothing")]) == 1
    assert snap(place, skip_move_dir=False) == before


def test_refuses_while_an_account_lock_is_held(place, capsys):
    import fcntl
    fd = os.open(place / "state" / "beta-main" / "lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        before = snap(place, skip_move_dir=False)
        assert run("move", place) == 1
        assert "state/beta-main/lock を誰かが握っています" in capsys.readouterr().out
        assert snap(place, skip_move_dir=False) == before
    finally:
        os.close(fd)
    fd = os.open(place / "state" / "_app.lock", os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        assert run("move", place) == 1
    finally:
        os.close(fd)


def test_refuses_unknown_entries_until_sorted(place, capsys):
    (place / "state.bak-1101").mkdir()
    (place / "state" / "_mystery").mkdir()
    before = snap(place, skip_move_dir=False)
    assert run("move", place) == 1
    out = capsys.readouterr().out
    assert "state.bak-1101" in out and "state/_mystery" in out
    assert snap(place, skip_move_dir=False) == before
    assert run("move", place, "--keep", "state.bak-1101", "--move", "state/_mystery") == 0
    assert (place / "state.bak-1101").is_dir() and (place / "ws" / "main" / "state" / "_mystery").is_dir()


def test_refuses_when_already_moved_or_main_exists(place, capsys):
    (place / "ws" / "main").mkdir()
    assert run("move", place) == 1
    assert "ws/main がもうあります" in capsys.readouterr().out


def test_refuses_a_root_that_is_not_named():
    assert mv.main(["move", "--root", "relative/path", "--no-units-check"]) == 1
    with pytest.raises(SystemExit):
        mv.main(["move", "--no-units-check"])


def test_refuses_a_ledger_that_points_at_the_root_itself(place, capsys):
    data = json.loads((place / "accounts" / "kappa-main.json").read_text())
    data["repo_dir"] = "$THTH_ROOT"
    (place / "accounts" / "kappa-main.json").write_text(json.dumps(data))
    assert run("move", place) == 1
    assert "根そのもの" in capsys.readouterr().out


def test_back_refuses_when_the_root_slot_is_taken(place, capsys):
    assert run("move", place) == 0
    os.chmod(place / "logs", 0o700)
    (place / "logs").rename(place / "ws" / ".main-move" / "taken-logs")
    (place / "logs").mkdir()
    _write(place / "logs" / "new.txt", "x")
    assert run("back", place) == 1
    assert "logs が根にもあります" in capsys.readouterr().out


# --------------------------------------------------------------------------- R2: 古い版を当てる

def _old_tree(tmp_path) -> Path | None:
    ref = os.environ.get("THTH_OLD_REF", "origin/main")
    got = subprocess.run(["git", "archive", "--format=tar", ref, "thth", "bin"], cwd=REPO, capture_output=True,
                         timeout=60)
    if got.returncode != 0:
        return None
    dest = tmp_path / "old_thth"
    dest.mkdir()
    with tarfile.open(fileobj=io.BytesIO(got.stdout)) as tar:
        tar.extractall(dest, filter="data")
    if (dest / "thth" / "accounts.py").read_text().count("MAIN_MOVE_DIR"):
        return None  # 古い版がもう段 5 を知っている（この試験の前提が崩れた）
    return dest


OLD_ACTIVITY = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import thth.relay as relay
calls = []
def fake(kind, subject, operation, body):
    calls.append({"kind": kind, "subject": subject, "operation": operation,
                  "accounts": [a.get("account") for a in body.get("accounts", [])], "keys": len(body.get("keys") or [])})
    return {"actions": [], "requests": []}
relay.signed_request = fake
from thth import activity
activity.run_once(sys.argv[2])
print(json.dumps(calls))
"""


def _old(old: Path, root: Path, *argv):
    env = {**os.environ, "THTH_ROOT": str(root), "PYTHONPATH": str(old), "HOME": str(root.parent / "home"),
           "THTH_APP_DIR": str(old)}
    for name in ("THTH_WS", "THTH_ACCOUNTS_DIR", "THTH_DRY_RUN"):
        env.pop(name, None)
    return subprocess.run([sys.executable, *argv], cwd=root.parent, env=env, capture_output=True, text=True, timeout=120)


def _old_world(tmp_path, *, tombstones: bool):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root は mode 000 のフォルダも開けるので、この試験の意味が無い")
    old = _old_tree(tmp_path)
    if old is None:
        pytest.skip("古い版（origin/main）を取り出せない")
    root = build(tmp_path)
    assert run("move", root) == 0
    if not tombstones:
        _open_tombs(root)
        for name in [p for p in root.iterdir()] + [p for p in (root / "state").iterdir()]:
            if name.is_dir() and not any(name.iterdir()) and name.name not in ("app", "venv", "ws"):
                name.rename(root / "ws" / ".main-move" / f"open-{name.name}")
    return old, root


OLD_COMMANDS = [
    ["-m", "thth", "board", "--json"],
    ["-m", "thth", "account"],
    ["-m", "thth", "maintain"],
    ["-m", "thth", "run", "alpha-main"],
    ["-m", "thth", "collect", "alpha-main"],
]


@pytest.mark.parametrize("argv", OLD_COMMANDS, ids=lambda a: " ".join(a[2:]))
def test_old_version_stops_on_the_moved_place(tmp_path, argv):
    """R2 (b)(c): 版だけ古くしても、古い版は開けない印で止まり、置き場を 1 つも書き換えない。"""
    old, root = _old_world(tmp_path, tombstones=True)
    before = snap(root, skip_move_dir=False)
    got = _old(old, root, *argv)
    assert got.returncode != 0, (got.stdout, got.stderr)
    said = got.stdout + got.stderr
    assert any(w in said for w in ("読めません", "Permission", "unreadable")), (got.stdout, got.stderr)
    assert snap(root, skip_move_dir=False) == before


def test_old_worker_does_not_push_the_key_table(tmp_path):
    """R2 (c): 古い常駐は、移したあとの置き場で Worker に何も押し上げない（全部の鍵を閉じない）。"""
    old, root = _old_world(tmp_path, tombstones=True)
    before = snap(root, skip_move_dir=False)
    got = _old(old, root, "-c", OLD_ACTIVITY, str(old), str(root / "private" / "report-credentials.json"))
    assert got.returncode == 0, got.stderr
    assert json.loads(got.stdout.strip().splitlines()[-1]) == []
    assert "activity_owners_unavailable" in got.stderr
    assert snap(root, skip_move_dir=False) == before


def test_without_the_marks_the_old_version_runs_silently(tmp_path):
    """印が要る理由の証拠: 開けない印を置かないと、古い版は口座 0 の置き場として黙って動き、
    常駐は空の要約を Worker に押し上げる（R2 に書いた危なさ）。"""
    old, root = _old_world(tmp_path, tombstones=False)
    board = _old(old, root, "-m", "thth", "board", "--json")
    assert board.returncode == 0, board.stderr
    got = _old(old, root, "-c", OLD_ACTIVITY, str(old), str(root / "private" / "report-credentials.json"))
    calls = json.loads(got.stdout.strip().splitlines()[-1])
    assert calls and calls[0]["accounts"] == [], got.stderr


def test_new_version_refuses_main_while_moving(place, monkeypatch, tmp_path):
    monkeypatch.setenv("THTH_ROOT", str(place))
    monkeypatch.delenv("THTH_WS", raising=False)
    from thth import accounts, cli
    monkeypatch.setattr(accounts, "APP_DIR", str(tmp_path / "noapp"))
    assert accounts.ws_root() == str(place)  # 移す前は根が main（今の本番と同じ）
    assert run("move", place, "--stop-after", "move:4") == 3
    with pytest.raises(accounts.WorkspaceError, match="workspace_moving"):
        accounts.list_account_names()
    with accounts.use_workspace("garden"):  # ほかの括りは止めない
        assert accounts.list_account_names() == ["gamma-garden"]
    from thth import refusal_catalog
    assert "workspace_moving" in refusal_catalog.fix_view("ws_admin.NEXT")


def test_report_isolation_reads_main_from_the_moved_place(place, monkeypatch, tmp_path):
    """報告の門の検め（`report_isolation`）は、移したあとも main の台帳を括りの根で見る。"""
    monkeypatch.setenv("THTH_ROOT", str(place))
    for name in ("THTH_WS", "THTH_ACCOUNTS_DIR"):
        monkeypatch.delenv(name, raising=False)
    from thth import accounts, report_isolation
    monkeypatch.setattr(accounts, "APP_DIR", str(tmp_path / "noapp"))
    report_isolation.validate_environment(str(place), {}, allow_empty=True)
    assert run("move", place) == 0
    report_isolation.validate_environment(str(place), {}, allow_empty=True)


# --------------------------------------------------------------------------- 記録の道（VM の写しで 1096→1・11/29）

def _ledger_place(root: Path, monkeypatch, tmp_path):
    """本物の承認と止めの口で、alpha の repo の原稿 2 本に承認と止めを書く（記録は repo を絶対の道で持つ）。"""
    monkeypatch.setenv("THTH_ROOT", str(root))
    for name in ("THTH_WS", "THTH_ACCOUNTS_DIR", "THTH_WS_MAP"):
        monkeypatch.delenv(name, raising=False)
    from thth import accounts, approval_ledger as led
    monkeypatch.setattr(accounts, "APP_DIR", str(tmp_path / "noapp"))
    repo = root / "repos" / "alpha"
    (repo / ".git").mkdir()
    for n in ("a", "b"):
        _write(repo / "queue" / f"{n}.md", "---\naccount: alpha-main\nstatus: draft\n---\n本文\n", 0o644)
    for n in ("approvals.json", "holds.json"):  # 稽古の偽の記録は退けて（消さない）、本物の口に書かせる
        (root / "state" / "alpha-main" / n).rename(root / "state" / "alpha-main" / f"{n}.fake")
    _clear_ledger_caches()
    led.record([{"account": "alpha-main", "file": "queue/a.md", "state": led.APPROVED, "repo": str(repo),
                 "fields": {"approved_sha": "x" * 64, "approved_at": "2026-11-29T10:00:00+09:00",
                            "approved_by": OWNER}}])
    led.record_hold("alpha-main", "queue/b.md", repo=str(repo), until="2026-12-31T00:00:00+09:00",
                    reason="稽古", by=OWNER, at="2026-11-29T10:00:00+09:00")
    return led


def _clear_ledger_caches():
    from thth import approval_ledger as led
    for cache in (led._TOP_CACHE, led._LOAD_CACHE, led._HOLDS_CACHE, led._GATE_CACHE):
        cache.clear()


def _seen(led, repo: Path) -> tuple:
    """照らした結果: a の台帳の行が重なるか・b の止めが見えるか。"""
    _clear_ledger_caches()
    a, b = str(repo / "queue" / "a.md"), str(repo / "queue" / "b.md")
    approved = led.overlay(a, {"account": "alpha-main", "status": "draft"}).get("status")
    held = (led.hold_for(b, "alpha-main") or {}).get("reason")
    return approved, held


def test_records_keep_approvals_and_holds_across_move_and_back(place, monkeypatch, tmp_path, capsys):
    led = _ledger_place(place, monkeypatch, tmp_path)
    assert _seen(led, place / "repos" / "alpha") == ("approved", "稽古")
    assert run("check", place) == 0
    assert "記録の道: 3 か所（ファイル 3）" in capsys.readouterr().out
    assert run("move", place) == 0
    main_repo = place / "ws" / "main" / "repos" / "alpha"
    assert _seen(led, main_repo) == ("approved", "稽古")  # 直さないと (draft, None): 承認も止めも消える
    assert str(place / "repos" / "alpha") not in (place / "ws" / "main" / "state" / "alpha-main" / "holds.json").read_text()
    # 移していた間に書かれた止め（新しい道）も、戻すと根の道になる
    led.record_hold("alpha-main", "queue/a.md", repo=str(main_repo), until="2026-12-31T00:00:00+09:00",
                    reason="移した間", by=OWNER, at="2026-11-29T11:00:00+09:00")
    assert run("back", place) == 0
    assert _seen(led, place / "repos" / "alpha") == ("approved", "稽古")
    _clear_ledger_caches()
    assert (led.hold_for(str(place / "repos" / "alpha" / "queue" / "a.md"), "alpha-main") or {}).get("reason") == "移した間"
    assert "/ws/main/" not in (place / "state" / "alpha-main" / "holds.json").read_text()


def test_record_rewrite_does_not_touch_other_names_or_common_paths(place):
    """`state/alpha-main` の書き替えが `state/alpha-main-x` や共通の `state/_admin`・`private` の道に及ばない。"""
    st = place / "state" / "alpha-main"
    rows = {"a": f"{place}/state/alpha-main/x", "b": f"{place}/state/alpha-main-x/y", "c": f"{place}/state/_admin/z",
            "d": f"{place}/private/report-credentials.json", "e": f"{place}/repos/alpha", "f": f"文の中の {place}/repos/alpha"}
    _write(st / "rows.json", json.dumps(rows, ensure_ascii=False))
    _write(st / "runs.ndjson", json.dumps({"repo": f"{place}/repos/beta"}) + "\n" + "{壊れた行\n")
    assert run("move", place) == 0
    got = json.loads((place / "ws" / "main" / "state" / "alpha-main" / "rows.json").read_text())
    main = f"{place}/ws/main"
    assert got == {"a": f"{main}/state/alpha-main/x", "b": rows["b"], "c": rows["c"], "d": rows["d"],
                   "e": f"{main}/repos/alpha", "f": f"文の中の {main}/repos/alpha"}
    nd = (place / "ws" / "main" / "state" / "alpha-main" / "runs.ndjson").read_text()
    assert nd == json.dumps({"repo": f"{main}/repos/beta"}) + "\n" + "{壊れた行\n"
    assert run("move", place) == 0  # もう一度打っても二重にならない（state=moved で何もしない）
    assert run("back", place) == 0
    assert json.loads((st / "rows.json").read_text()) == rows


def _repos_hit(led, root: Path, base: Path) -> int:
    """承認と止めの記録の各行の repo が、realpath で原稿の repo の頂上に当たる数（当たらなければ assert）。"""
    _clear_ledger_caches()
    hit = 0
    for name in ("approvals.json", "holds.json"):
        data = json.loads((base / "state" / "alpha-main" / name).read_text())
        for key, row in data["items"].items():
            top = led.repo_root_of(str(base / "repos" / "alpha" / key))
            assert top and os.path.realpath(row["repo"]) == top, (name, key, row["repo"])
            hit += 1
    return hit


def test_records_repo_paths_and_inflight_hit_the_drafts(place, monkeypatch, tmp_path):
    led = _ledger_place(place, monkeypatch, tmp_path)
    assert _repos_hit(led, place, place) == 2
    assert run("move", place) == 0
    main = place / "ws" / "main"
    assert _repos_hit(led, place, main) == 2
    inflight = json.loads((main / "state" / "alpha-main" / "inflight.json").read_text())
    assert inflight["file"] == str(main / "repos" / "alpha" / "queue" / "a.md") and os.path.isfile(inflight["file"])
    assert run("back", place) == 0
    assert _repos_hit(led, place, place) == 2


def test_move_refuses_when_a_record_rewrite_is_skipped(place, monkeypatch, tmp_path, capsys):
    """柵: 書き替えを外すと move が自分で断り（rc 1）、back を案内する。back で元に戻る。"""
    led = _ledger_place(place, monkeypatch, tmp_path)
    before = snap(place)
    monkeypatch.setattr(mv, "rewrite_records", lambda *a, **k: (0, 0))
    assert run("move", place) == 1
    out = capsys.readouterr().out
    assert "移す前の根の道が残っています: state/alpha-main/" in out and "`back` で戻し" in out
    assert (place / "ws" / ".main-move" / "MOVING").exists()  # 印は残る（新しい版は main を workspace_moving で断る）
    monkeypatch.undo()
    assert run("back", place) == 0
    monkeypatch.setenv("THTH_ROOT", str(place))
    from thth import approval_ledger
    assert _seen(approval_ledger, place / "repos" / "alpha") == ("approved", "稽古")
    after = snap(place)
    assert {k: v for k, v in after.items() if k in before} == before


def test_move_refuses_an_escaped_root_left_in_a_record(place, capsys):
    """JSON で `/` を逃がした道（書き替えられない形）が記録に残っていれば断る。"""
    esc = str(place).replace("/", "\\/")
    _write(place / "state" / "alpha-main" / "odd.json", '{"repo": "' + esc + '\\/repos\\/alpha"}')
    assert run("move", place) == 1
    assert "state/alpha-main/odd.json（1 か所）" in capsys.readouterr().out
    assert run("back", place) == 0
