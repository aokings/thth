"""括りの段 2 の柵（設計 叩き台 §8 R1）: 資格のファイルは main の版がそのまま読める形のまま。

- main の版の `load_credentials` は、行の欄を `MAIN_FIELDS` に絞り、知らない欄が 1 つあるとファイルごと読めなくする
  （本番の鍵が全部止まる）。新しい版が書いた資格のファイルの行は、どれもこの集合に収まる。
- レーンの鍵は括りの表（`ws/<括り>/private/lane-keys.json`）にだけ書き、発行・取り消しで資格のファイルの bytes は
  変わらない。
- レーンの鍵の口座は照合のたびに括りから引く（あとで足した口座も同じ鍵で動く）。
- 括りの表が 1 つ壊れても、資格のファイルの鍵とほかの括りの鍵は止まらない。
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from tests.conftest import make_account_json
from tests.test_ws_lane_login import LANE, OWNER, _key, world  # noqa: F401  （同じ 2 つの括りと持ち主）

# main の版（origin/main・5.31.0）の `report_http.load_credentials` が受ける欄（写し）。
MAIN_FIELDS = frozenset(("sha256", "expires_at", "revoked", "accounts", "scope", "writes", "actor"))
MAIN_REQUIRED = frozenset(("sha256", "expires_at", "revoked", "accounts"))


def _main_reads(path) -> bool:
    """main の版の欄の絞りを、そのまま当てる。"""
    config = json.loads(Path(path).read_text())
    return set(config) == {"schema_version", "root", "credentials"} and all(
        type(item) is dict and not set(item) - MAIN_FIELDS and MAIN_REQUIRED <= set(item)
        for item in config["credentials"])


def test_the_copy_matches_main_and_this_version():
    from thth import report_http
    assert report_http.CREDENTIAL_FIELDS == MAIN_FIELDS
    try:
        source = subprocess.run(["git", "show", "origin/main:thth/report_http.py"], capture_output=True, text=True,
                                timeout=20, cwd=Path(__file__).resolve().parent.parent)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git が無い")
    if source.returncode != 0:
        pytest.skip("origin/main が読めない")
    literal = re.search(r'set\(item\) - (\{[^}]*\})', source.stdout)
    if literal is None:
        assert "CREDENTIAL_FIELDS" in source.stdout
        return
    assert set(json.loads(literal.group(1).replace("{", "[").replace("}", "]"))) == MAIN_FIELDS


def test_issue_and_revoke_do_not_touch_the_credentials_file(world):  # noqa: F811
    from thth import activity, lane_keys
    root, path = world
    before = Path(path).read_bytes()
    activity.issue_lane_key(path, OWNER, None, LANE, _key(20))
    activity.issue_lane_key(path, OWNER, None, LANE, _key(21))   # 前の鍵の取り消しを含む
    assert lane_keys.revoke("garden", OWNER, LANE)
    assert Path(path).read_bytes() == before
    assert _main_reads(path)
    table = root / "ws" / "garden" / "private" / "lane-keys.json"
    assert oct(table.stat().st_mode & 0o777) == "0o600"
    assert [row["revoked"] for row in lane_keys.read("garden")] == [True, True]


def test_rotate_in_another_workspace_writes_main_readable_rows(world):  # noqa: F811
    from thth import accounts, activity
    root, path = world
    with accounts.use_workspace("garden"):
        activity.rotate(path, "gamma-garden", OWNER, _key(22))
    with accounts.use_workspace("orchard"):
        activity.rotate(path, "delta-orchard", "delta", _key(23))
    assert _main_reads(path)
    rows = json.loads(Path(path).read_text())["credentials"]
    assert all("workspace" not in row and "lane" not in row for row in rows)


def test_this_version_refuses_what_main_refuses(world):  # noqa: F811
    from thth import report_http
    root, path = world
    config = json.loads(Path(path).read_text())
    config["credentials"][0]["workspace"] = "garden"
    Path(path).write_text(json.dumps(config))
    with pytest.raises(ValueError):
        report_http.load_credentials(Path(path))


def test_lane_key_sees_accounts_added_later(world):  # noqa: F811
    from thth import activity, api_requests
    root, path = world
    activity.issue_lane_key(path, OWNER, None, LANE, _key(24))
    make_account_json(str(root / "ws" / "garden" / "accounts"), "gamma-later", repo_dir=str(root.parent / "repo-b"),
                      project="laterproj", env="$THTH_ROOT/secrets/l.env", token="$THTH_ROOT/secrets/l.token")
    assert api_requests.lane_accounts(OWNER, path) == ["gamma-garden", "gamma-later"]
    item = {"request_id": "z" * 43 + ".gamma-later", "account": "gamma-later", "operation": "lane_status",
            "key_sha256": _key(24), "body": {"account": "gamma-later"}, "workspace": "garden"}
    assert api_requests.handle(OWNER, item, path)["result"]["accounts"] == 2


def test_a_broken_table_stops_only_its_own_workspace(world, capsys):  # noqa: F811
    from thth import activity, report_http
    root, path = world
    activity.issue_lane_key(path, OWNER, None, LANE, _key(25))
    broken = root / "ws" / "orchard" / "private" / "lane-keys.json"
    broken.write_text("{broken")
    _, creds = report_http.load_credentials(Path(path))
    digests = {c[0] for c in creds}
    assert _key("acct") in digests and _key(25) in digests and _key("delta") not in digests
    assert "lane_keys_unreadable: orchard" in capsys.readouterr().err
