"""5.18.1: 記事の口座（WordPress・はてなブログ）の `thth leave` が最後まで進む（リリースノート 5.16.0「残したこと」）。

記事の口座の `token` は主体（`thth principal`）の鍵のパスを写したもの（`account_cli` の add）で、鍵の持ち主は主体。
5.18.0 までは、その鍵を口座の物として数えていた:

- 主体と口座の名が同じ（雛形の `demo-wordpress`）: 主体の鍵を消す対象に入れ、`revoke()` が記事の媒体を知らず
  `remote_revoke_unsupported` で止まる。
- 名が違う（雛形の `demo-hatena` と `demo-hatenablog`）: `credential_ownership_unproved` で止まる。
- 同じ主体の口座がほかにある: 進むが「他 account と共有の接続」と言い、遠隔の取り消しの案内が違う。

柵: 口座の台帳・state・口座の env は消える。**主体の鍵は口座の leave で消さない**（ほかの口座が無くても）。
遠隔の取り消しは本人の手（`unconfirmed_manual`）で、完了の案内は媒体ごとの 1 行と、主体の鍵が残ることと消し方。
鍵は試験の値（本物ではない）。網には出ない（`env` の柵が外への接続を落とす）。
"""
from __future__ import annotations

import json
import os
import pathlib
from types import SimpleNamespace

import pytest

from tests.test_v212_leave import worker  # noqa: F401  (fixture)
from tests.test_v212_server_writes import env  # noqa: F401  (fixture)
from thth import accounts, admin_log, leave

EXAMPLES = pathlib.Path(__file__).resolve().parent.parent / "accounts.example"
SECRET_VALUE = "synthetic-not-a-real-key"


def _config_dir() -> pathlib.Path:
    d = pathlib.Path(os.environ["HOME"]) / ".config" / "thth"
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    d.parent.chmod(0o700)
    return d


def _principal(env, kind, name, lanes):
    """主体の台帳と鍵（試験の値）を置く。鍵のパスを返す。"""
    root = env["root"]
    (root / "principals").mkdir(mode=0o700, exist_ok=True)
    example = "principal-wordpress.json" if kind == "wordpress" else "principal-hatena.json"
    p = json.loads((EXAMPLES / example).read_text(encoding="utf-8"))
    token = _config_dir() / f"{name}.token"
    blog = "wp.test" if kind == "wordpress" else "alpha.hatenablog.com"
    p.update(principal=name, owner=name, token=str(token), blogs=[{**p["blogs"][0], "blog_id": blog, "lanes": lanes}])
    if kind == "wordpress":
        p["wp_user"] = "editor1"
        token.write_text(json.dumps({"app_password": SECRET_VALUE}))
    else:
        p["hatena_id"] = "demo"
        p["consumer"] = str(_config_dir() / f"{name}.consumer")
        token.write_text(json.dumps({"access_token": SECRET_VALUE, "access_token_secret": SECRET_VALUE}))
    token.chmod(0o600)
    path = root / "principals" / f"{name}.json"
    path.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
    path.chmod(0o600)
    return token, blog


def _article_account(env, account, kind, principal, blog):
    """`env` の SNS の口座を、主体を通す記事の口座に書き換える（`account_cli` の add と同じ形）。"""
    root = env["root"]
    path = root / "accounts" / f"{account}.json"
    old = json.loads(path.read_text())
    example = "wordpress.json" if kind == "wordpress" else "hatenablog.json"
    a = json.loads((EXAMPLES / example).read_text(encoding="utf-8"))
    p = json.loads((root / "principals" / f"{principal}.json").read_text(encoding="utf-8"))
    own_env = _config_dir() / f"{account}.env"
    own_env.write_text("SYNTHETIC=1\n")
    own_env.chmod(0o600)
    a.update(account=account, project=old["project"], principal=principal, blog_id=blog,
             handle=p["wp_user"] if kind == "wordpress" else p["hatena_id"],
             token=p["token"], env=str(own_env), repo_dir=old["repo_dir"], production=True, scheduled=False)
    path.write_text(json.dumps(a, ensure_ascii=False))
    return own_env


def _leave_cli(name, capsys):
    rc = leave.command(SimpleNamespace(name=name, by="operator", json=False))
    out = capsys.readouterr()
    return rc, out.out, out.err


@pytest.mark.parametrize("kind,principal", [
    ("wordpress", "alpha"),          # 主体と口座の名が同じ（雛形 demo-wordpress の形）
    ("wordpress", "alpha-wp"),
    ("hatenablog", "alpha-hatena"),  # 名が違う（雛形 demo-hatena と demo-hatenablog の形）
    ("hatenablog", "alpha"),
])
def test_柵_記事の口座のleaveは最後まで進み主体の鍵は残る(env, worker, capsys, kind, principal):
    token, blog = _principal(env, kind, principal, ["alpha"])
    own_env = _article_account(env, "alpha", kind, principal, blog)
    before = token.read_bytes()
    assert accounts.load_account("alpha")["media"] == kind
    rc, out, err = _leave_cli("alpha", capsys)
    assert rc == 0, err
    row = leave.read("alpha")
    assert row["phase"] == "completed" and row["remote"] == "unconfirmed_manual"
    assert "principal_token" in row["preserved"] and "token" not in row["deleted"]
    # 口座の物は消える
    assert not (env["root"] / "accounts" / "alpha.json").exists()
    assert not (env["root"] / "state" / "alpha").exists()
    assert not own_env.exists() and row["deleted"]["env"] == 1
    # 主体の物は残る（鍵・台帳）
    assert token.read_bytes() == before
    assert (env["root"] / "principals" / f"{principal}.json").exists()
    # 案内は媒体ごとの 1 行と、主体の鍵の残りと消し方。SNS の案内は出さない・鍵の値は出さない。
    if kind == "wordpress":
        assert "アプリケーションパスワード" in out and "プロフィール" in out
        assert "連携アプリ" not in out
    else:
        assert "連携アプリ" in out and "アプリケーションパスワード" not in out
        assert f"--forget-consumer" in out
    assert "Threads" not in out and "Bluesky" not in out and "共有の接続" not in out
    assert f"principal_token_kept: 主体 {principal} の鍵は残しました" in out
    assert f"thth principal auth {principal} --by <名前> --forget" in out
    assert SECRET_VALUE not in out + err
    # 何度呼んでも同じ（案内も同じ）。
    rc2, out2, _ = _leave_cli("alpha", capsys)
    assert rc2 == 0 and out2 == out


def test_同じ主体のほかの口座が残っても共有の接続とは言わず鍵も残す(env, worker, capsys):
    token, blog = _principal(env, "wordpress", "shared-wp", ["alpha", "beta"])
    _article_account(env, "alpha", "wordpress", "shared-wp", blog)
    _article_account(env, "beta", "wordpress", "shared-wp", blog)
    rc, out, err = _leave_cli("alpha", capsys)
    assert rc == 0, err
    row = leave.read("alpha")
    assert row["remote"] == "unconfirmed_manual" and "token_shared" not in row["preserved"]
    assert token.exists() and accounts.load_account("beta")
    assert "共有の接続" not in out
    # ほかの口座が使っていることを添える（消すと beta も止まる）。
    assert "beta" in out


def test_5180の形で止まった退出も続きから進み主体の鍵を消さない(env, worker, capsys, monkeypatch):
    """5.18.0 で `remote_revoke_unsupported` で止まった row（主体の鍵が消す対象に入っている）を、続きから進める。"""
    token, blog = _principal(env, "wordpress", "alpha", ["alpha"])
    _article_account(env, "alpha", "wordpress", "alpha", blog)
    # 5.18.0 の数え方（主体の鍵を口座の物に数える）と、記事の媒体を知らない revoke。
    real = (leave._principal_owns_token, leave.revoke)
    monkeypatch.setattr(leave, "_principal_owns_token", lambda cfg: False)

    def revoke_5180(cfg, token, progress, save):
        raise ValueError("remote_revoke_unsupported")

    monkeypatch.setattr(leave, "revoke", revoke_5180)
    with pytest.raises(ValueError, match="remote_revoke_unsupported"):
        leave.run("alpha", by="operator")
    stuck = leave.read("alpha")
    assert stuck["phase"] == "remote_pending" and any(t["category"] == "token" for t in stuck["targets"])
    monkeypatch.setattr(leave, "_principal_owns_token", real[0])
    monkeypatch.setattr(leave, "revoke", real[1])
    rc, out, err = _leave_cli("alpha", capsys)
    assert rc == 0, err
    assert token.exists() and leave.read("alpha")["remote"] == "unconfirmed_manual"
    assert "principal_token_kept" in out


def test_退出の記録に主体の名が残り鍵の値は残らない(env, worker):
    token, blog = _principal(env, "hatenablog", "alpha-hatena", ["alpha"])
    _article_account(env, "alpha", "hatenablog", "alpha-hatena", blog)
    leave.run("alpha", by="operator")
    events, _ = admin_log.read(account="alpha", event="account_removed")
    assert len(events) == 1 and events[0]["medium"] == "hatenablog"
    assert events[0]["diff"]["principal"] == [None, "alpha-hatena"]
    assert SECRET_VALUE not in json.dumps(events, ensure_ascii=False)


def test_主体の鍵のファイルが無ければそう言う(env, worker, capsys):
    token, blog = _principal(env, "wordpress", "alpha-wp", ["alpha"])
    _article_account(env, "alpha", "wordpress", "alpha-wp", blog)
    token.unlink()
    rc, out, err = _leave_cli("alpha", capsys)
    assert rc == 0, err
    assert "principal_token_kept: 主体 alpha-wp の鍵のファイルはもうありません（消す物はありません）" in out


@pytest.mark.parametrize("media,word,absent", [
    ("threads", "Threads の設定", "Bluesky"),
    ("bluesky", "Bluesky の設定", "Threads"),
])
def test_SNSの案内も媒体で分ける(env, worker, capsys, media, word, absent):
    path = env["root"] / "accounts" / "alpha.json"
    cfg = json.loads(path.read_text())
    cfg["media"] = media
    if media == "bluesky":
        cfg["service"] = "https://bsky.social"
    path.write_text(json.dumps(cfg))
    rc, out, err = _leave_cli("alpha", capsys)
    assert rc == 0, err
    assert word in out and absent not in out and "principal_token_kept" not in out


@pytest.mark.parametrize("others", [["beta"], []], ids=["ほかの口座が残る", "最後の1つ"])
def test_はてなの主体の口座のleaveは最後まで進みOAuthの鍵とconsumerが残る(env, worker, capsys, others):
    """はてなの主体は OAuth の鍵（token と consumer）を持ち、1 つの主体を複数のブログの口座が使う。"""
    token, blog = _principal(env, "hatenablog", "kopi-hatena", ["alpha"] + others)
    consumer = pathlib.Path(json.loads((env["root"] / "principals" / "kopi-hatena.json").read_text())["consumer"])
    consumer.write_text(json.dumps({"consumer_key": SECRET_VALUE, "consumer_secret": SECRET_VALUE}))
    consumer.chmod(0o600)
    for name in ["alpha"] + others:
        _article_account(env, name, "hatenablog", "kopi-hatena", blog)
    before = (token.read_bytes(), consumer.read_bytes())
    rc, out, err = _leave_cli("alpha", capsys)
    assert rc == 0, err
    row = leave.read("alpha")
    assert row["phase"] == "completed" and row["remote"] == "unconfirmed_manual"
    assert row["preserved"] == ["principal_token"] and "token" not in row["deleted"]
    assert (token.read_bytes(), consumer.read_bytes()) == before
    assert not (env["root"] / "accounts" / "alpha.json").exists()
    for name in others:
        assert accounts.load_account(name)["principal"] == "kopi-hatena"
    remote = [line for line in out.splitlines() if line.startswith("remote_unconfirmed: ")]
    assert remote == ["remote_unconfirmed: " + leave.MANUAL_REVOKE["hatenablog"]]
    assert "はてなのアカウントの設定の連携アプリ（OAuth で許したアプリ）から THTH を外してください" in out
    kept = [line for line in out.splitlines() if line.startswith("principal_token_kept: ")]
    assert len(kept) == 1 and "主体 kopi-hatena の鍵は残しました" in kept[0]
    assert "--forget-consumer" in kept[0]
    if others:
        assert "この主体はまだ beta が使っています" in kept[0]
    else:
        assert "この主体を使う口座はもうありません" in kept[0]
    assert SECRET_VALUE not in out + err
