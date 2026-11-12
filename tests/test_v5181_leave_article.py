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


# --- 監査 5.18.1 直したほうがよい 1: 主体の鍵が口座の leave で消す所にあれば、消す前に止まる ---

def _move_principal_file(env, principal, key, new_path, accounts_=("alpha",)):
    """主体の台帳の `token`（か `consumer`）を `new_path` に置き直す（手で台帳を書き換えた形）。"""
    root = env["root"]
    pp = root / "principals" / f"{principal}.json"
    p = json.loads(pp.read_text())
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old = pathlib.Path(p[key]) if p.get(key) else None
    if old is not None and old.exists():
        new_path.write_bytes(old.read_bytes())
        old.unlink()
    else:
        new_path.write_text(json.dumps({"consumer_key": SECRET_VALUE, "consumer_secret": SECRET_VALUE}))
    new_path.chmod(0o600)
    p[key] = str(new_path)
    pp.write_text(json.dumps(p, ensure_ascii=False))
    if key == "token":
        for a in accounts_:
            ap = root / "accounts" / f"{a}.json"
            c = json.loads(ap.read_text())
            c["token"] = str(new_path)
            ap.write_text(json.dumps(c, ensure_ascii=False))


def _assert_stopped_before_delete(env, capsys, keep, principal, where):
    before = keep.read_bytes()
    rc, out, err = _leave_cli("alpha", capsys)
    assert rc == 2, out
    assert "owned_tree_contains_principal_credentials" in err
    # 前向きな断り: なぜ・通る直し方・もっと良く の 3 つ
    assert f"主体 {principal} の" in err and where in err
    assert "何も消さずに止めました" in err
    assert f"~/.config/thth/{principal}" in err and "同じ leave をもう一度" in err
    assert "既定の置き場" in err
    assert SECRET_VALUE not in out + err
    # 何も消えていない（主体の鍵も、口座の物も）
    assert keep.read_bytes() == before
    assert (env["root"] / "accounts" / "alpha.json").exists()
    row = leave.read("alpha")
    assert row["phase"] == "stopped" and not row["deleted"]
    # 打ち直しても同じ所で止まる（消さない）
    rc2, _, err2 = _leave_cli("alpha", capsys)
    assert rc2 == 2 and "owned_tree_contains_principal_credentials" in err2 and keep.read_bytes() == before


@pytest.mark.parametrize("kind,principal", [
    ("wordpress", "alpha"), ("wordpress", "alpha-wp"), ("hatenablog", "alpha-hatena"),
])
def test_柵_主体の鍵が口座のstateの中にあればleaveは消さずに止まる(env, worker, capsys, kind, principal):
    token, blog = _principal(env, kind, principal, ["alpha"])
    _article_account(env, "alpha", kind, principal, blog)
    inside = env["root"] / "state" / "alpha" / "principal.token"
    _move_principal_file(env, principal, "token", inside)
    assert accounts.load_account("alpha")["token"] == str(inside)
    _assert_stopped_before_delete(env, capsys, inside, principal, "state")


def test_柵_はてなのconsumerが口座のstateの中にあればleaveは消さずに止まる(env, worker, capsys):
    token, blog = _principal(env, "hatenablog", "alpha-hatena", ["alpha"])
    _article_account(env, "alpha", "hatenablog", "alpha-hatena", blog)
    inside = env["root"] / "state" / "alpha" / "nested" / "consumer.json"
    _move_principal_file(env, "alpha-hatena", "consumer", inside)
    _assert_stopped_before_delete(env, capsys, inside, "alpha-hatena", "state")
    assert token.exists()


def test_柵_口座のenvが主体の鍵と同じファイルならleaveは消さずに止まる(env, worker, capsys):
    token, blog = _principal(env, "wordpress", "alpha", ["alpha"])
    own_env = _article_account(env, "alpha", "wordpress", "alpha", blog)
    own_env.unlink()
    ap = env["root"] / "accounts" / "alpha.json"
    c = json.loads(ap.read_text())
    c["env"] = c["token"]
    ap.write_text(json.dumps(c))
    _assert_stopped_before_delete(env, capsys, token, "alpha", "env")


def test_柵_はてなのconsumerが口座のenvと同じファイルならleaveは消さずに止まる(env, worker, capsys):
    token, blog = _principal(env, "hatenablog", "alpha", ["alpha"])
    _article_account(env, "alpha", "hatenablog", "alpha", blog)
    # consumer を口座の env の既定の名（~/.config/thth/alpha.env）に置き、口座の env もそこを指す形
    own_env = _config_dir() / "alpha.env"
    _move_principal_file(env, "alpha", "consumer", own_env)
    _assert_stopped_before_delete(env, capsys, own_env, "alpha", "env")


# --- 監査 5.18.1 直したほうがよい 5: 5.18.0 までに unconfirmed_shared で終わった記事の口座の打ち直し ---

@pytest.mark.parametrize("kind", ["wordpress", "hatenablog"])
def test_5180までに共有の接続で終わった記事の口座は打ち直すと主体の鍵の行を出す(env, worker, capsys, monkeypatch, kind):
    principal = "shared-" + kind
    token, blog = _principal(env, kind, principal, ["alpha", "beta"])
    _article_account(env, "alpha", kind, principal, blog)
    _article_account(env, "beta", kind, principal, blog)
    # 5.18.0 の数え方: 主体の鍵を口座の物に数え、同じ鍵のファイルを beta も使うので「共有の接続」で終わる。
    real = leave._principal_owns_token
    monkeypatch.setattr(leave, "_principal_owns_token", lambda cfg: False)
    leave.run("alpha", by="operator")
    monkeypatch.setattr(leave, "_principal_owns_token", real)
    done = leave.read("alpha")
    assert done["phase"] == "completed" and done["remote"] == "unconfirmed_shared"
    rc, out, err = _leave_cli("alpha", capsys)
    assert rc == 0, err
    assert "共有の接続" not in out
    remote = [line for line in out.splitlines() if line.startswith("remote_unconfirmed: ")]
    assert remote == ["remote_unconfirmed: " + leave.MANUAL_REVOKE[kind]]
    kept = [line for line in out.splitlines() if line.startswith("principal_token_kept: ")]
    assert len(kept) == 1 and "主体の鍵は残しました" in kept[0] and "--forget" in kept[0]
    assert token.exists() and SECRET_VALUE not in out + err
    # 記録の形は崩さない（打ち直しは読むだけ）
    assert leave.read("alpha") == done


def test_SNSの共有の接続で終わった退出の打ち直しは今までの1行のまま(env, worker, capsys):
    root = env["root"]
    a = json.loads((root / "accounts" / "alpha.json").read_text())
    b = json.loads((root / "accounts" / "beta.json").read_text())
    (root / "accounts" / "beta.json").write_text(json.dumps(dict(b, token=a["token"])))   # 同じ鍵のファイル
    leave.run("alpha", by="operator")
    assert leave.read("alpha")["remote"] == "unconfirmed_shared"
    rc, out, _ = _leave_cli("alpha", capsys)
    assert rc == 0 and "remote_unconfirmed: 他 account と共有の接続です" in out and "principal_token_kept" not in out


# --- 監査 5.18.1 の確かめ 7・8・9: 断りのとおりに直せば通る・実体で比べる・主体の鍵を読まない ---

@pytest.mark.parametrize("kind,principal,key", [
    ("wordpress", "alpha", "token"), ("wordpress", "alpha-wp", "token"),
    ("hatenablog", "alpha-hatena", "token"), ("hatenablog", "alpha-hatena", "consumer"),
])
def test_柵_断りのとおりに直して同じleaveを打つと最後まで進み鍵は残る(env, worker, capsys, kind, principal, key):
    """7: 断りの「通すには」のとおりに鍵を既定の置き場へ移し、台帳を直して同じ leave を打つと rc 0 で終わる。

    止まったときの退出の記録（`cfg` の写し）は state の中のパスのまま。柵はそこにファイルがあるときだけ数える。"""
    token, blog = _principal(env, kind, principal, ["alpha"])
    _article_account(env, "alpha", kind, principal, blog)
    inside = env["root"] / "state" / "alpha" / f"principal.{key}"
    _move_principal_file(env, principal, key, inside)
    rc, out, err = _leave_cli("alpha", capsys)
    assert rc == 2 and "owned_tree_contains_principal_credentials" in err
    # 断りの言う直し方: 鍵を ~/.config/thth/<主体>.<token|consumer> へ移し、主体の台帳を直す。token は口座の台帳も。
    assert f"~/.config/thth/{principal}.token" in err and f"principals/{principal}.json" in err
    assert "accounts/alpha.json" in err
    home = _config_dir() / f"{principal}.{key}"
    home.write_bytes(inside.read_bytes())
    home.chmod(0o600)
    inside.unlink()
    before = home.read_bytes()
    pp = env["root"] / "principals" / f"{principal}.json"
    p = json.loads(pp.read_text())
    p[key] = str(home)
    pp.write_text(json.dumps(p, ensure_ascii=False))
    if key == "token":
        ap = env["root"] / "accounts" / "alpha.json"
        c = json.loads(ap.read_text())
        c["token"] = str(home)
        ap.write_text(json.dumps(c, ensure_ascii=False))
    assert leave.read("alpha")["cfg"].get("token") in (str(inside), str(token))   # 記録の写しは変えない
    rc, out, err = _leave_cli("alpha", capsys)
    assert rc == 0, err
    row = leave.read("alpha")
    assert row["phase"] == "completed" and "principal_token" in row["preserved"]
    assert home.read_bytes() == before
    assert not (env["root"] / "accounts" / "alpha.json").exists()
    assert SECRET_VALUE not in out + err


def test_柵_主体の鍵がstateの中を指すsymlinkならleaveは消さずに止まる(env, worker, capsys):
    """8: 葉が symlink（~/.config/thth/<主体>.token → state/<口座>/real.token）。字で比べると抜けて実体が消えた。"""
    token, blog = _principal(env, "wordpress", "alpha-wp", ["alpha"])
    _article_account(env, "alpha", "wordpress", "alpha-wp", blog)
    real = env["root"] / "state" / "alpha" / "real.token"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_bytes(token.read_bytes())
    real.chmod(0o600)
    token.unlink()
    os.symlink(real, token)
    _assert_stopped_before_delete(env, capsys, real, "alpha-wp", "state")
    assert os.path.islink(token)


def test_柵_はてなのconsumerがstateの中を指すsymlinkでも止まる(env, worker, capsys):
    token, blog = _principal(env, "hatenablog", "alpha-hatena", ["alpha"])
    _article_account(env, "alpha", "hatenablog", "alpha-hatena", blog)
    consumer = pathlib.Path(json.loads((env["root"] / "principals" / "alpha-hatena.json").read_text())["consumer"])
    real = env["root"] / "state" / "alpha" / "deep" / "consumer.json"
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text(json.dumps({"consumer_key": SECRET_VALUE, "consumer_secret": SECRET_VALUE}))
    real.chmod(0o600)
    if consumer.exists():
        consumer.unlink()
    os.symlink(real, consumer)
    _assert_stopped_before_delete(env, capsys, real, "alpha-hatena", "state")


def test_柵_字の大小だけ違うパスでもstateの中なら止まる(env, worker, capsys):
    """8: 字の大小を分けない置き場（macOS の既定）で `…/STATE/<口座>/…` と書いた形。分ける置き場では別の所なので飛ばす。"""
    root = env["root"]
    upper = root / "STATE"
    (root / "state" / "alpha").mkdir(parents=True, exist_ok=True)
    if not upper.exists():
        pytest.skip("この置き場は字の大小を分ける（本番の Linux と同じ）")
    token, blog = _principal(env, "wordpress", "alpha-wp", ["alpha"])
    _article_account(env, "alpha", "wordpress", "alpha-wp", blog)
    _move_principal_file(env, "alpha-wp", "token", upper / "alpha" / "principal.token")
    _assert_stopped_before_delete(env, capsys, root / "state" / "alpha" / "principal.token", "alpha-wp", "state")


@pytest.mark.parametrize("kind,others", [("wordpress", []), ("hatenablog", []), ("wordpress", ["beta"])],
                         ids=["WordPress", "はてな", "同じ主体のほかの口座"])
def test_柵_記事の口座のleaveは主体の鍵のファイルを読まない(env, worker, capsys, monkeypatch, kind, others):
    """9: 柵は場所と同一性（stat）だけで判定し、主体の鍵（はてなは consumer も）を開かない。"""
    principal = "alpha-p"
    token, blog = _principal(env, kind, principal, ["alpha"] + others)
    if kind == "hatenablog":
        consumer = pathlib.Path(json.loads((env["root"] / "principals" / f"{principal}.json").read_text())["consumer"])
        consumer.write_text(json.dumps({"consumer_key": SECRET_VALUE, "consumer_secret": SECRET_VALUE}))
        consumer.chmod(0o600)
    for name in ["alpha"] + others:
        _article_account(env, name, kind, principal, blog)
    secret_names = {f"{principal}.token", f"{principal}.consumer"}
    opened = []
    real_open, real_file = os.open, leave._file

    def spy_open(path, flags, *args, **kwargs):
        if pathlib.Path(os.fsdecode(path)).name in secret_names:
            opened.append(("os.open", os.fsdecode(path)))
        return real_open(path, flags, *args, **kwargs)

    def spy_file(path, *, private=False):
        if pathlib.Path(path).name in secret_names:
            opened.append(("leave._file", str(path)))
        return real_file(path, private=private)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(leave, "_file", spy_file)
    rc, out, err = _leave_cli("alpha", capsys)
    monkeypatch.undo()
    assert rc == 0, err
    assert opened == []
    assert token.exists()
