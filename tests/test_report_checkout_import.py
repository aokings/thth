"""A checkout may be shipped in a minimal runtime with no Git executable."""
import os
from pathlib import Path
import subprocess
import sys


def test_report_import_from_checkout_without_git(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    assert (repo / ".git").exists()
    env = dict(os.environ, PATH=str(tmp_path / "no-binaries"), PYTHONPATH=str(repo),
               PYTHONDONTWRITEBYTECODE="1")
    env.pop("THTH_ROOT", None)
    env.pop("THTH_APP_DIR", None)
    result = subprocess.run([sys.executable, "-c", """
import shutil
from pathlib import Path
assert shutil.which('git') is None
from thth import report_service, selfupdate
assert (Path(selfupdate.APP_DIR) / '.git').exists()
assert selfupdate.LOADED_REV is None
print('checkout_without_git_import_ok')
"""], env=env, cwd=repo, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "checkout_without_git_import_ok"


def test_git_oserror_is_bounded_failure(monkeypatch):
    from thth import selfupdate
    def unavailable(*args, **kwargs):
        raise PermissionError("SECRET_PATH_CANARY")
    monkeypatch.setattr(selfupdate.subprocess, "run", unavailable)
    result = selfupdate._git(["rev-parse", "HEAD"], cwd="/unused")
    assert result.returncode == 127
    assert result.stdout == ""
    assert result.stderr == "git_unavailable"
