"""金庫と庭の口（`thth sync`・`thth garden`・段 1・仮）の試験。

**本物の monban には繋がない。** monban の道具（`monban-vault`・`git-remote-monban`）は、`PATH` の先頭に置いた偽の実行
ファイルに差し替える。git は本物を使い、偽の git-remote-monban が遠隔の口の `connect` で手元の bare repo に繋ぐ
（insteadOf には頼らない: insteadOf で先を替えると thth sync は断る）。monban の断りの形（`monban: <符丁> — …`）も
偽の git-remote-monban が出す。

柵（わざと壊して赤を見たもの・報告に記録）:

- 鍵・戻しの文字列が THTH の出力に出ない（monban と git の出力をそのまま写さない）
- 庭の頁に script・外の資源を入れない（作る口と出す前の検査）
- 非公開・売り切れの商品を頁に載せない
- monban の制限（種類・HTML 1 MB・UTF-8・http-equiv）を出す前に THTH の側で断る
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from thth import cli
from thth import refusal_catalog as rc
from thth.vault import garden, sync, tools, wire

ROOT = Path(__file__).resolve().parent.parent
REAL_GIT = shutil.which("git")
URL = "monban://thth/acct/ledger"
OTHER = "monban://thth/acct/other"
GARDEN = "monban://thth/acct/kopicha"
RECOVERY = "abcd-efgh-ijkl-mnop-qrst-uvwx-yz23-4567"
BEARER = "Bearer mbk_LIVE_0123456789abcdefABCDEF"

pytestmark = pytest.mark.skipif(REAL_GIT is None, reason="git が要る")

# ------------------------------------------------------------------ 偽の道具

FAKE_HELPER = r'''
import os, sys, json
log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"tool": "git-remote-monban", "argv": sys.argv[1:]}) + "\n")
mode = os.environ.get("FAKE_MONBAN_MODE", "ok")
if mode == "ok":
    # 動く偽の金庫: git の遠隔の口の `connect` で、手元の bare repo の upload-pack・receive-pack に繋ぐ（暗号はしない）。
    def line():
        out = b""
        while True:
            ch = os.read(0, 1)
            if not ch or ch == b"\n":
                return out.decode()
            out += ch
    while True:
        got = line()
        if got == "capabilities":
            os.write(1, b"connect\n\n")
        elif got.startswith("connect "):
            os.write(1, b"\n")
            service = got.split(" ", 1)[1]
            git = os.environ["FAKE_REAL_GIT"]
            os.execv(git, [git, service.replace("git-", "", 1), os.environ["FAKE_BARE"]])
        else:
            sys.exit(0)
if mode == "leak":
    print("monban: invalid_key — " + os.environ["FAKE_BEARER"] + " recovery " + os.environ["FAKE_RECOVERY"], file=sys.stderr)
else:
    print("monban: " + mode + " — the words after the code are not shown by THTH", file=sys.stderr)
sys.exit(1)
'''

FAKE_VAULT = r'''
import os, sys, json
log = os.environ.get("FAKE_LOG")
args = sys.argv[1:]
if log:
    with open(log, "a") as f:
        f.write(json.dumps({"tool": "monban-vault", "argv": args}) + "\n")
mode = os.environ.get("FAKE_VAULT_MODE", "ok")
recovery, bearer = os.environ["FAKE_RECOVERY"], os.environ["FAKE_BEARER"]
cmd = args[0]
if cmd == "init":
    print("made / 作りました: " + args[1])
    if "--passphrase-stdin" not in args:
        print("戻しの文字列（1 度だけ表示）:\n" + recovery)
    sys.exit(0)
if cmd == "join":
    line = sys.stdin.readline().rstrip("\n") if "--stdin" in args else ""
    if line == recovery:
        print(json.dumps({"status": "joined", "url": args[1]}))
        sys.exit(0)
    print(json.dumps({"error": "passphrase_mismatch", "reason": "got " + line + " " + bearer}))
    sys.exit(1)
if cmd == "status":
    print(json.dumps({"head": "a" * 64, "seq": 3, "bytes": 1200, "boxes": 3, "joined": True, "key": bearer}))
    sys.exit(0)
if cmd == "garden":
    verb = args[1]
    if mode == "token_missing":
        print(json.dumps({"error": "garden_token_missing", "reason": "no token"}))
        sys.exit(2)
    if mode == "leak":
        print(json.dumps({"error": "invalid_key", "reason": bearer + " " + recovery}))
        print("monban-vault: invalid_key — " + bearer, file=sys.stderr)
        sys.exit(1)
    if verb == "publish":
        print(json.dumps({"head": "b" * 64, "files": 3, "bytes": 999, "token": bearer}))
        sys.exit(0)
    stops = os.environ.get("FAKE_GARDEN_STOPS", "")
    print(json.dumps({"head": "b" * 64, "files": 3, "bytes": 999, "owner_stopped": "owner" in stops or verb == "stop",
                      "operator_stopped": "operator" in stops}))
    sys.exit(0)
sys.exit(3)
'''

FAKE_GIT = r'''
import os, sys
real = os.environ["FAKE_REAL_GIT"]
state = os.environ["FAKE_PUSH_STATE"]
if "push" in sys.argv[1:]:
    n = int(open(state).read()) if os.path.exists(state) else 0
    if n < int(os.environ.get("FAKE_PUSH_REJECTS", "0")):
        open(state, "w").write(str(n + 1))
        print("To monban://thth/acct/ledger", file=sys.stderr)
        print(" ! [rejected]        main -> main (fetch first)", file=sys.stderr)
        print("error: failed to push some refs to 'monban://thth/acct/ledger'", file=sys.stderr)
        sys.exit(1)
os.execv(real, [real] + sys.argv[1:])
'''


def _script(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """偽の道具の入った PATH・git の全体の設定・THTH_ROOT。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _script(bin_dir / "git-remote-monban", FAKE_HELPER)
    _script(bin_dir / "monban-vault", FAKE_VAULT)
    bare = tmp_path / "bare.git"
    subprocess.run([REAL_GIT, "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = Test\n\temail = test@example.invalid\n[init]\n\tdefaultBranch = main\n"
        "[commit]\n\tgpgsign = false\n", encoding="utf-8")
    monkeypatch.setenv("FAKE_BARE", str(bare))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.path.dirname(REAL_GIT)}{os.pathsep}/usr/bin:/bin")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("FAKE_RECOVERY", RECOVERY)
    monkeypatch.setenv("FAKE_BEARER", BEARER)
    monkeypatch.setenv("FAKE_REAL_GIT", REAL_GIT)
    monkeypatch.setenv("FAKE_PUSH_STATE", str(tmp_path / "push-count"))
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("THTH_ROOT", str(root))
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("unexpected network"))
    return {"tmp": tmp_path, "bin": bin_dir, "bare": bare, "root": root}


def _calls(env) -> list:
    path = env["tmp"] / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _git(repo, *args, check=True):
    return subprocess.run([REAL_GIT, "-C", str(repo), *args], check=check, capture_output=True, text=True)


def _repo(env, name: str, *, url: str = URL, clone: bool = False) -> Path:
    path = env["tmp"] / name
    if clone:
        subprocess.run([REAL_GIT, "clone", "-q", str(env["bare"]), str(path)], check=True, capture_output=True)
        _git(path, "remote", "rename", "origin", "monban")
        _git(path, "remote", "set-url", "monban", url)
    else:
        path.mkdir()
        _git(path, "init", "-q", "-b", "main")
        (path / "README.md").write_text("# 台帳\n", encoding="utf-8")
        _git(path, "add", "README.md")
        _git(path, "commit", "-q", "-m", "first")
        _git(path, "remote", "add", "monban", url)
    return path


def _commit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"edit {name}")


def _main(argv, capfd):
    code = cli.main(argv)
    out = capfd.readouterr()
    return code, out.out, out.err


def _json(argv, capfd):
    code, out, err = _main([*argv, "--json"], capfd)
    return code, json.loads(out), err


# ------------------------------------------------------------------ thth sync

def test_sync_1台目は空の金庫に送る(env, capfd):
    a = _repo(env, "a")
    code, got, _ = _json(["sync", "--repo", str(a)], capfd)
    assert code == 0 and got["ok"] and got["pushed"] and got["attempts"] == 1
    assert _git(env["bare"], "rev-parse", "main").stdout == _git(a, "rev-parse", "HEAD").stdout


def test_sync_2台で合わせる_mergeで取り込んでから送る(env, capfd):
    a = _repo(env, "a")
    assert _json(["sync", "--repo", str(a)], capfd)[0] == 0
    b = _repo(env, "b", clone=True)
    _commit(a, "店.md", "店の行\n")
    assert _json(["sync", "--repo", str(a)], capfd)[0] == 0
    _commit(b, "商品.md", "商品の行\n")
    code, got, _ = _json(["sync", "--repo", str(b)], capfd)
    assert code == 0 and got["pulled"] == 1 and got["pushed"]
    assert (b / "店.md").exists()
    code, got, _ = _json(["sync", "--repo", str(a)], capfd)
    assert code == 0 and got["pulled"] >= 1 and (a / "商品.md").exists()
    code, text, _ = _main(["sync", "--repo", str(a)], capfd)
    assert code == 0 and "送るものなし" in text


def test_sync_同じ行のぶつかりは人に返す(env, capfd):
    a = _repo(env, "a")
    _json(["sync", "--repo", str(a)], capfd)
    b = _repo(env, "b", clone=True)
    _commit(a, "README.md", "A の版\n")
    _json(["sync", "--repo", str(a)], capfd)
    _commit(b, "README.md", "B の版\n")
    code, got, _ = _json(["sync", "--repo", str(b)], capfd)
    assert code == 2 and got["code"] == "sync_conflict"
    assert got["fix"] and got["better"]


def test_sync_fetch_firstならpullからやり直す(env, capfd, monkeypatch):
    _script(env["bin"] / "git", FAKE_GIT)
    monkeypatch.setenv("FAKE_PUSH_REJECTS", "2")
    a = _repo(env, "a")
    code, got, _ = _json(["sync", "--repo", str(a)], capfd)
    assert code == 0 and got["attempts"] == 3


def test_sync_3回やり直して置けなければ前向きに断る(env, capfd, monkeypatch):
    _script(env["bin"] / "git", FAKE_GIT)
    monkeypatch.setenv("FAKE_PUSH_REJECTS", "9")
    a = _repo(env, "a")
    code, got, _ = _json(["sync", "--repo", str(a)], capfd)
    assert code == 2 and got["code"] == "sync_retry_exhausted"
    assert (env["tmp"] / "push-count").read_text() == "3"


def test_sync_点検_cleanでなければpullもpushもしない(env, capfd):
    a = _repo(env, "a")
    (a / "書きかけ.md").write_text("まだ\n", encoding="utf-8")
    code, got, _ = _json(["sync", "--repo", str(a)], capfd)
    assert code == 2 and got["code"] == "sync_register_unclean" and got["files"] == 1
    assert _git(env["bare"], "rev-parse", "--verify", "--quiet", "main", check=False).returncode != 0


def test_sync_点検_枝とrepoとremote(env, capfd, tmp_path):
    a = _repo(env, "a")
    _git(a, "switch", "-q", "-c", "other")
    assert _json(["sync", "--repo", str(a)], capfd)[1]["code"] == "sync_branch_mismatch"
    assert _json(["sync", "--repo", str(tmp_path / "none")], capfd)[1]["code"] == "sync_not_repo"
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _json(["sync", "--repo", str(plain)], capfd)[1]["code"] == "sync_not_repo"
    _git(a, "switch", "-q", "main")
    _git(a, "remote", "remove", "monban")
    assert _json(["sync", "--repo", str(a)], capfd)[1]["code"] == "sync_remote_missing"
    _git(a, "remote", "add", "monban", "https://example.invalid/x.git")
    assert _json(["sync", "--repo", str(a)], capfd)[1]["code"] == "sync_url_invalid"
    assert _json(["sync", "--repo", str(a), "--branch", "../x"], capfd)[1]["code"] == "sync_args_invalid"


def test_sync_点検_mergeの途中は止める(env, capfd):
    a = _repo(env, "a")
    git_dir = Path(_git(a, "rev-parse", "--absolute-git-dir").stdout.strip())
    (git_dir / "MERGE_HEAD").write_text(_git(a, "rev-parse", "HEAD").stdout, encoding="utf-8")
    code, got, _ = _json(["sync", "--repo", str(a)], capfd)
    assert got["code"] == "sync_in_progress" and got["detail"] == "MERGE_HEAD"


def test_sync_monbanの道具が無ければ入れ方を言う(env, capfd, monkeypatch):
    (env["bin"] / "git-remote-monban").unlink()
    a = _repo(env, "a")
    code, got, _ = _json(["sync", "--repo", str(a)], capfd)
    assert code == 2 and got["code"] == "sync_tool_missing" and got["detail"] == "git-remote-monban"
    assert "pip install '.[vault]'" in got["fix"]
    code, _, err = _main(["sync", "--repo", str(a)], capfd)
    assert code == 2 and err.startswith("sync_tool_missing: ") and "pip install '.[vault]'" in err


@pytest.mark.parametrize("mode,code", [("tampered", "sync_tampered"), ("too_many_pending", "sync_busy"),
                                       ("box_too_large", "sync_box_too_large"), ("vault_full", "sync_vault_full"),
                                       ("vault_not_joined", "sync_not_joined"), ("invalid_key", "sync_login_required"),
                                       ("something_new", "sync_failed")])
def test_sync_monbanの断りを符丁で写す(env, capfd, monkeypatch, mode, code):
    monkeypatch.setenv("FAKE_MONBAN_MODE", mode)
    a = _repo(env, "a", url=OTHER)
    rc_, got, _ = _json(["sync", "--repo", str(a)], capfd)
    assert rc_ == 2 and got["code"] == code and mode in (got["detail"] or "")
    assert got["why"] and got["fix"] and got["better"]
    helper_calls = [c for c in _calls(env) if c["tool"] == "git-remote-monban"]
    assert len(helper_calls) == 1, "tampered などはやり直さない"


def test_柵_monbanの出力の鍵と戻しの文字列を写さない(env, capfd, monkeypatch):
    monkeypatch.setenv("FAKE_MONBAN_MODE", "leak")
    a = _repo(env, "a", url=OTHER)
    for argv in (["sync", "--repo", str(a)], ["sync", "--repo", str(a), "--json"]):
        code, out, err = _main(argv, capfd)
        assert code == 2 and "sync_login_required" in out + err
        assert RECOVERY not in out + err and "mbk_LIVE" not in out + err
        assert "words after the code" not in out + err


def test_sync_init_は人の端末でだけ_monbanを呼ばない(env, capfd):
    a = _repo(env, "a")
    _git(a, "remote", "remove", "monban")
    code, got, _ = _json_like(["sync", "init", URL, "--repo", str(a)], capfd)
    assert code == 2 and "sync_init_needs_terminal" in got
    assert _calls(env) == []


def _json_like(argv, capfd):
    code, out, err = _main(argv, capfd)
    return code, out + err, err


def test_sync_init_端末では出力を取らずに流し_remoteを足す(env, capfd, monkeypatch):
    a = _repo(env, "a")
    _git(a, "remote", "remove", "monban")
    monkeypatch.setattr(sync, "is_tty", lambda: True)
    seen = []
    real_run = tools.run

    def spy(argv, **kw):
        if os.path.basename(argv[0]) == "monban-vault":
            seen.append(kw)
        return real_run(argv, **kw)

    monkeypatch.setattr(tools, "run", spy)
    code, out, err = _main(["sync", "init", URL, "--repo", str(a)], capfd)
    assert code == 0
    assert seen == [{"passthrough": True}], "戻しの文字列が出る init は THTH が出力を取らない"
    assert _git(a, "config", "--get", "remote.monban.url").stdout.strip() == URL
    assert [c["argv"] for c in _calls(env)] == [["init", URL]]


def test_sync_init_合言葉の道では戻しの文字列が出たと言わない(env, capfd, monkeypatch):
    a = _repo(env, "a")
    _git(a, "remote", "remove", "monban")
    code, out, err = _main(["sync", "init", URL, "--repo", str(a), "--passphrase-stdin"], capfd)
    assert code == 0 and "戻しの文字列" not in out + err and "合言葉" in out


@pytest.mark.parametrize("setting,word", [
    (("url.{bare}.insteadOf", URL), "fetch"),
    (("url.{bare}.pushInsteadOf", URL), "push"),
    (("remote.monban.pushurl", "{bare}"), "push"),
])
def test_柵_実際の先がmonbanでなければpullもpushもしない(env, capfd, setting, word):
    a = _repo(env, "a")
    key, value = (part.format(bare=env["bare"]) for part in setting)
    _git(a, "config", key, value)
    code, got, _ = _json(["sync", "--repo", str(a)], capfd)
    assert code == 2 and got["code"] == "sync_remote_redirected" and got["detail"] == word
    assert got["why"] and got["fix"] and got["better"]
    assert _calls(env) == []
    assert _git(env["bare"], "rev-parse", "--verify", "--quiet", "main", check=False).returncode != 0


def _thth(env, argv, stdin=""):
    run_env = dict(os.environ)
    run_env["PYTHONPATH"] = str(ROOT)
    return subprocess.run([sys.executable, "-m", "thth", *argv], input=stdin, capture_output=True, text=True,
                          env=run_env, cwd=str(env["tmp"]))


def test_sync_join_戻しの文字列はTHTHを通らずmonbanへ(env):
    b = _repo(env, "b")
    _git(b, "remote", "remove", "monban")
    done = _thth(env, ["sync", "join", URL, "--stdin", "--repo", str(b)], stdin=RECOVERY + "\n")
    assert done.returncode == 0, done.stderr
    assert RECOVERY not in done.stdout + done.stderr
    assert _git(b, "config", "--get", "remote.monban.url").stdout.strip() == URL
    wrong = _thth(env, ["sync", "join", URL, "--stdin", "--repo", str(b)], stdin="zzzz-wrong-" + RECOVERY + "\n")
    assert wrong.returncode == 2 and "sync_join_failed" in wrong.stderr
    assert RECOVERY not in wrong.stdout + wrong.stderr and "mbk_LIVE" not in wrong.stdout + wrong.stderr


def test_sync_status_は許した欄だけ(env, capfd):
    a = _repo(env, "a")
    code, got, _ = _json(["sync", "status", "--repo", str(a)], capfd)
    assert code == 0 and got["seq"] == 3 and got["joined"] is True
    assert "mbk_LIVE" not in json.dumps(got) and "key" not in got


# ------------------------------------------------------------------ 庭: 店の写しから頁を作る

def _snapshot(env, store="kopicha-base", items=None):
    path = env["root"] / "store_state" / store / "snapshot.json"
    path.parent.mkdir(parents=True)
    rows = items if items is not None else {
        "1": {"id": 1, "name": "抹茶の缶 <b>限定</b>", "price": 1200, "stock": 3, "visible": True,
              "sales_status": "item_now_on_sale", "image_url": "https://img.example.invalid/1.jpg", "item_url": None,
              "modified_at": "2026-11-12"},
        "2": {"id": 2, "visible": False},
        "3": {"id": 3, "name": "売り切れのほうじ茶", "price": 900, "stock": 0, "visible": True,
              "sales_status": "item_now_on_sale", "image_url": None, "item_url": None, "modified_at": "2026-11-12"},
        "4": {"id": 4, "name": "予約の玄米茶", "price": 800, "stock": 5, "visible": True,
              "sales_status": "item_waiting_for_sale", "image_url": None, "item_url": None, "modified_at": "2026-11-12"},
        "5": {"id": 5, "name": "値段なし http-equiv 茶", "price": None, "stock": 1, "visible": True,
              "sales_status": "item_now_on_sale", "image_url": None, "item_url": None, "modified_at": "2026-11-12"},
    }
    path.write_text(json.dumps({"schema": "thth.store_snapshot/1", "read_at": "2026-11-12T09:00+09:00",
                                "shop_id": "kopicha", "items": rows}, ensure_ascii=False), encoding="utf-8")


def test_garden_build_公開で買える商品だけ_scriptも外の資源もない(env, capfd):
    _snapshot(env)
    out_dir = env["tmp"] / "site"
    code, got, _ = _json(["garden", "build", "kopicha-base", "--out", str(out_dir), "--title", "こぴちゃ",
                          "--shop-url", "https://kopicha.example.invalid/"], capfd)
    assert code == 0 and got["items"] == 2 and got["not_listed"] == 3
    assert got["files"] == ["/index.html", "/items.html", "/style.css"]
    pages = {name: (out_dir / name).read_text(encoding="utf-8") for name in garden.OUTPUT_FILES}
    items = pages["items.html"]
    assert "抹茶の缶 &lt;b&gt;限定&lt;/b&gt;" in items and "1,200 円" in items and "価格は店の頁で" in items
    for hidden in ("売り切れのほうじ茶", "予約の玄米茶"):
        assert all(hidden not in text for text in pages.values())
    joined = "".join(pages.values())
    assert "img.example.invalid" not in joined and "<img" not in joined and "<script" not in joined.lower()
    assert "<form" not in joined.lower() and "http-equiv" not in joined.lower()
    for name in ("index.html", "items.html"):
        assert pages[name].count(garden.GUIDE_MARK) == 1 and "THTH の案内" in pages[name]
    garden.check_dir(str(out_dir))  # 作った物は出す前の検査を通る


def test_garden_build_の断り(env, capfd):
    assert _json(["garden", "build", "kopicha-base", "--out", str(env["tmp"] / "s")], capfd)[1]["code"] \
        == "garden_snapshot_missing"
    _snapshot(env)
    assert _json(["garden", "build", "kopicha-base", "--out", str(env["tmp"] / "s"),
                  "--shop-url", "http://kopicha.example.invalid/"], capfd)[1]["code"] == "garden_args_invalid"
    busy = env["tmp"] / "busy"
    busy.mkdir()
    (busy / "大事なメモ.txt").write_text("消さないで", encoding="utf-8")
    assert _json(["garden", "build", "kopicha-base", "--out", str(busy)], capfd)[1]["code"] == "garden_out_not_empty"
    assert (busy / "大事なメモ.txt").read_text(encoding="utf-8") == "消さないで"
    again = env["tmp"] / "again"
    assert _json(["garden", "build", "kopicha-base", "--out", str(again)], capfd)[0] == 0
    assert _json(["garden", "build", "kopicha-base", "--out", str(again)], capfd)[0] == 0  # 作り直しは通る


# ------------------------------------------------------------------ 庭: 出す前の検査

GOOD = ('<!DOCTYPE html><html lang="ja"><head><meta charset="utf-8"><title>t</title>'
        '<link rel="stylesheet" href="style.css"></head><body>{body}' + garden.GUIDE + "</body></html>")


def _site(tmp_path, body="<p>本文</p>", name="index.html", raw=None, extra=None) -> Path:
    site = tmp_path / "site"
    site.mkdir(exist_ok=True)
    data = raw if raw is not None else GOOD.format(body=body).encode("utf-8")
    (site / name).write_bytes(data)
    for key, value in (extra or {}).items():
        (site / key).write_bytes(value)
    return site


@pytest.mark.parametrize("body,code", [
    ("<script>alert(1)</script>", "garden_active_refused"),
    ("<SCRIPT src=a.js></SCRIPT>", "garden_active_refused"),
    ('<form action="https://evil.example.invalid/"><input name="pw"></form>', "garden_active_refused"),
    ('<p onclick="x()">押す</p>', "garden_active_refused"),
    ('<a href="javascript:alert(1)">押す</a>', "garden_active_refused"),
    ('<iframe src="a.html"></iframe>', "garden_active_refused"),
    ('<img src="https://cdn.example.invalid/a.png">', "garden_external_refused"),
    ('<img src="//cdn.example.invalid/a.png">', "garden_external_refused"),
    ('<img srcset="a.png 1x, https://cdn.example.invalid/b.png 2x">', "garden_external_refused"),
    ('<link rel="stylesheet" href="https://fonts.example.invalid/a.css">', "garden_external_refused"),
    ('<p style="background:url(https://t.example.invalid/p.gif)">x</p>', "garden_markup_refused"),
    ("<style>@import url(a.css);</style>", "garden_markup_refused"),
    ('<p style="color:red">x</p>', "garden_markup_refused"),
    # 監査 段 1 の 4: 正規表現の柵の抜け道
    ('<script a=b"c>alert(1)', "garden_active_refused"),
    ('<a href="java&#9;script:alert(1)">押す</a>', "garden_active_refused"),
    ('<a href="&#1;javascript:alert(1)">押す</a>', "garden_active_refused"),
    ('<a href="JaVaScRiPt&colon;alert(1)">押す</a>', "garden_active_refused"),
    ('<img src="/\\evil.example.invalid/x.png">', "garden_external_refused"),
    ('<img src=" &#1;//evil.example.invalid/x.png">', "garden_external_refused"),  # 生の制御文字は先に断る（10）
    ('<img src="a.png" alt="閉じない>', "garden_markup_refused"),
    ('<p>x</p><img src="a.png"', "garden_markup_refused"),
    ('<a href="a.html" ping="https://t.example.invalid/">x</a>', "garden_external_refused"),
    ('<aside hidden data-thth-guide="free">隠した枠</aside>', "garden_guide_missing"),
    ('<meta http-equiv="refresh" content="0;url=https://evil.example.invalid/">', "garden_text_refused"),
    ("<p>二つめ</p>" + garden.GUIDE, "garden_guide_missing"),
])
def test_garden_check_THTHの柵(tmp_path, body, code):
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(_site(tmp_path, body)))
    assert exc.value.code == code


def test_garden_check_終わりで閉じないタグ(tmp_path):
    raw = GOOD.format(body="<p>x</p>").encode("utf-8") + b'<img src="a.png'
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(_site(tmp_path, raw=raw)))
    assert exc.value.code == "garden_markup_refused" and "broken tag" in exc.value.detail


def test_garden_check_普通のSVGのxmlnsは外の資源でない(tmp_path):
    svg = (b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1 1">'
           b'<use xlink:href="#a"/><rect width="1" height="1"/></svg>')
    site = _site(tmp_path, extra={"logo.svg": svg})
    assert "/logo.svg" in garden.check_dir(str(site))["files"]


@pytest.mark.parametrize("css", [b"body{background:url(\\68ttps://t.example.invalid/p.gif)}",
                                 b"body{background:url(/* x */https://t.example.invalid/p.gif)}",
                                 b"@font-face{src:url('//f.example.invalid/a.woff2')}",
                                 b"@import 'a.css';"], ids=["escape", "comment", "font", "import"])
def test_garden_check_CSSの外の資源(tmp_path, css):
    site = _site(tmp_path, extra={"style.css": css})
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(site))
    assert exc.value.code == "garden_external_refused"


# 監査 段 1 の 6: html.parser（3.12 まで）とブラウザで注の終わりの読み方が違う 4 つ。後ろのタグが検査に見えなかった。
_OUT = '<img src="https://e.example.invalid/x.png" onerror="x()">'


@pytest.mark.parametrize("body,detail", [
    ("<![CDATA[>" + _OUT + "]]>", "cdata"),
    ("<!-- a --!>" + _OUT + "<!-- b -->", "comment"),
    ("<!-->" + _OUT + "<!-- b -->", "comment"),
    ("<!--->" + _OUT + "<!-- b -->", "comment"),
    ("<!-- a --!>x<!-- b -->", "comment"),  # 後ろに何も無くても、版で読みが割れる書き方そのものを断る
    ("<!--[if IE]><p>x</p><![endif]-->", "cdata"),  # 古い IE の条件つき注も <![ を含む
], ids=["cdata", "bang", "abrupt", "abrupt3", "bang-alone", "if-ie"])
def test_garden_check_注の終わりの読み違いを断る(tmp_path, body, detail):
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(_site(tmp_path, body)))
    assert exc.value.code == "garden_markup_refused" and detail in exc.value.detail


@pytest.mark.parametrize("body,detail", [
    ("<xmp><img src=\"https://e.example.invalid/x.png\"></xmp>", "old raw tag"),
    ("<noembed><p>x</p></noembed>", "old raw tag"),
    ("<!-- a -- >" + _OUT + "<!-- b -->", "comment"),  # 3.12 までの html.parser だけが閉じる
], ids=["xmp", "noembed", "dash-space"])
def test_garden_check_版で読みの割れる書き方を断る(tmp_path, body, detail):
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(_site(tmp_path, body)))
    assert exc.value.code == "garden_markup_refused" and detail in exc.value.detail


def test_garden_check_titleの中のタグに見える字(tmp_path):
    raw = GOOD.format(body="<p>x</p>").replace("<title>t</title>", "<title>a <b>b</b></title>").encode("utf-8")
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(_site(tmp_path, raw=raw)))
    assert exc.value.code == "garden_markup_refused" and "title" in exc.value.detail
    ok = GOOD.format(body="<p>x</p>").replace("<title>t</title>", "<title>1 < 2 &amp; 抹茶</title>").encode("utf-8")
    assert "/index.html" in garden.check_dir(str(_site(tmp_path, raw=ok)))["files"]


@pytest.mark.parametrize("name,raw,detail", [
    ("a.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><!--><rect fill="url(#a)"/><!-- b --></svg>', "comment"),
    ("a.xml", b'<?xml version="1.0"?><r><!-- a --!><x/><!-- b --></r>', "comment"),
    ("a.xml", b'<?xml version="1.0"?><r><![CDATA[ x </r>', "cdata"),  # 閉じない CDATA
    ("a.xml", b'<?xml version="1.0"?><?xml-stylesheet type="text/xsl" href="s.xsl"?><r/>', "xml-stylesheet"),
    ("a.svg", b'<?xml-stylesheet href="s.css"?><svg xmlns="http://www.w3.org/2000/svg"/>', "xml-stylesheet"),
    ("a.svg", b'<?XML-STYLESHEET href="s.css"?><svg xmlns="http://www.w3.org/2000/svg"/>', "xml-stylesheet"),
], ids=["svg-abrupt", "xml-bang", "xml-cdata-open", "xml-ss", "svg-ss", "svg-ss-upper"])
def test_garden_check_SVGとXMLの注とxml_stylesheet(tmp_path, name, raw, detail):
    site = _site(tmp_path, extra={name: raw})
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(site))
    assert exc.value.code == "garden_markup_refused" and detail in exc.value.detail


def test_garden_check_XMLのCDATAは字として読む(tmp_path):
    feed = ('<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>t</title>'
            '<item><title><![CDATA[抹茶 & ほうじ茶 <新しい>]]></title>'
            '<description><![CDATA[<p>今週の <a href="https://kopicha.example.invalid/">新しい缶</a></p>]]></description>'
            '</item></channel></rss>').encode("utf-8")
    assert "/feed.xml" in garden.check_dir(str(_site(tmp_path, extra={"feed.xml": feed})))["files"]
    for inner in (b"<script>x()</script>", b"<form>"):  # 字面の検査は CDATA の中でも止める（安全な側）
        bad = b'<?xml version="1.0"?><r><![CDATA[' + inner + b"]]></r>"
        with pytest.raises(wire.VaultRefused) as exc:
            garden.check_dir(str(_site(tmp_path, extra={"feed.xml": bad})))
        assert exc.value.code == "garden_active_refused"


# 監査 段 1 の 7: 普通の頁を誤って断っていた所のうち、読みに行かないもの。
@pytest.mark.parametrize("body", [
    '<link rel="canonical" href="https://kopicha.example.invalid/">',
    '<link rel="alternate" hreflang="en" href="https://kopicha.example.invalid/en/">',
    '<link rel="alternate" type="application/rss+xml" href="https://kopicha.example.invalid/feed.xml">',
    '<link rel="ME author" href="https://sns.example.invalid/kopicha">',
    '<div itemscope itemtype="https://schema.org/Product"><span itemprop="name">抹茶</span></div>',
    '<blockquote cite="https://news.example.invalid/a">引用</blockquote>',
    '<q cite="https://news.example.invalid/a">短い引用</q>',
    '<img src="a.webp" alt="a < b">',
    '<img src="a.webp" alt="1 <= 2 &lt;b&gt;">',
], ids=["canonical", "alternate-lang", "alternate-rss", "me-author", "itemtype", "blockquote-cite", "q-cite",
        "alt-lt", "alt-lt-escaped"])
def test_garden_check_読みに行かない印は通る(tmp_path, body):
    assert "/index.html" in garden.check_dir(str(_site(tmp_path, body)))["files"]


@pytest.mark.parametrize("body,code", [
    ('<link rel="alternate stylesheet" href="https://f.example.invalid/a.css">', "garden_external_refused"),
    ('<link rel="canonical stylesheet" href="https://f.example.invalid/a.css">', "garden_external_refused"),
    ('<link rel="icon" href="https://f.example.invalid/a.ico">', "garden_external_refused"),
    ('<link rel="alternate icon" href="https://f.example.invalid/a.ico">', "garden_external_refused"),
    ('<link rel="preload" as="image" href="https://f.example.invalid/a.png">', "garden_external_refused"),
    ('<link rel="prefetch" href="https://f.example.invalid/a">', "garden_external_refused"),
    ('<link rel="dns-prefetch" href="//f.example.invalid">', "garden_external_refused"),
    ('<link href="https://f.example.invalid/a.css">', "garden_external_refused"),
    ('<link rel="canonical" href="a.html" imagesrcset="https://f.example.invalid/a.png 1x">', "garden_external_refused"),
    ('<link rel="canonical" href="javascript:x()">', "garden_active_refused"),
    ('<blockquote cite="javascript:x()">引用</blockquote>', "garden_active_refused"),
    ('<img src="a.webp" alt="閉じない><b>x</b>', "garden_markup_refused"),  # 引用符の合わない属性が次のタグを呑む
], ids=["alt-stylesheet", "canonical-stylesheet", "icon", "alt-icon", "preload", "prefetch", "dns-prefetch",
        "no-rel", "imagesrcset", "canonical-js", "cite-js", "swallow"])
def test_garden_check_読みに行くlinkと動く先は断るまま(tmp_path, body, code):
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(_site(tmp_path, body)))
    assert exc.value.code == code


def test_garden_check_終わりがアンドの語でも通る(tmp_path):
    raw = GOOD.format(body="<p>研究</p>").encode("utf-8") + b"\nR&D"
    assert "/index.html" in garden.check_dir(str(_site(tmp_path, raw=raw)))["files"]


@pytest.mark.parametrize("css", [b'p.note::before{content:"Note: "}',
                                 b"q{quotes:'\xe3\x80\x8c: ' '\xe3\x80\x8d'}",
                                 b'li::after{content:counter(n) ". see: "}',
                                 b'a[href^="https:"]::after{content:" (out)"}',
                                 b'body{font-family:"Hiragino Sans",sans-serif}'],
                         ids=["content", "quotes", "counter", "attr-selector", "font"])
def test_garden_check_CSSのcontentの文字列は通る(tmp_path, css):
    assert "/style.css" in garden.check_dir(str(_site(tmp_path, extra={"style.css": css})))["files"]


@pytest.mark.parametrize("css", [
    b'p{background:image-set("https://t.example.invalid/a.png" 1x)}',
    b'p::before{content:image-set("https://t.example.invalid/a.png" 1x)}',
    b'p::before{content:"x" url(https://t.example.invalid/a.png)}',
    b':root{--u:"https://t.example.invalid/a.png"}p{background:image-set(var(--u) 1x)}',
    b'p{content:"/*"; background:image-set("https://t.example.invalid/a.png" 1x) /*"*/}',
    b'p{content:"/*"; background:url(https://t.example.invalid/a.png) /*"*/}',
    b'p::before{content:image-set(x\\29 "https://t.example.invalid/a.png" 1x)}',
    b'p{background:image-set(; content:"https://t.example.invalid/a.png" 1x)}',
    b'p{cont\\65nt:"x"; background:image-set("//t.example.invalid/a.png" 1x)}',
    b'@im\\70ort "a.css";',
], ids=["image-set", "content-image-set", "content-url", "var", "comment-in-string", "comment-in-string-url",
        "escaped-paren", "semicolon-in-paren", "escaped-prop", "escaped-import"])
def test_garden_check_CSSの文字列の外の資源は断るまま(tmp_path, css):
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(_site(tmp_path, extra={"style.css": css})))
    assert exc.value.code == "garden_external_refused"


def test_garden_check_断るままの所に直し方がある():
    markup, active, external = (rc.get(c) for c in ("garden_markup_refused", "garden_active_refused",
                                                     "garden_external_refused"))
    assert "SVG" in markup["fix"]["text"] and "fill=" in markup["fix"]["text"] and "&lt;" in markup["fix"]["text"]
    for word in ("comment", "cdata", "xml-stylesheet", "title", "old raw tag"):  # 検査の出す詳細の語を全部説明する
        assert word in markup["why"]
    assert "注" in active["why"] and "&lt;" in active["fix"]["text"]
    assert "canonical" in external["better"]


def test_garden_check_日本語の名前でもどれかを言う(tmp_path):
    site = _site(tmp_path, extra={"抹茶の缶.js": b"x"})
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(site))
    assert exc.value.code == "garden_kind_refused" and "抹茶の缶.js" in exc.value.detail


def test_garden_check_リンクは外へ向けてよい(tmp_path):
    site = _site(tmp_path, '<p><a href="https://kopicha.example.invalid/" rel="noopener">店へ</a></p>',
                 extra={"style.css": b"body{color:#222}", "a.webp": b"RIFF"})
    found = garden.check_dir(str(site))
    assert sorted(found["files"]) == ["/a.webp", "/index.html", "/style.css"]


@pytest.mark.parametrize("raw,code,detail", [
    (b"\xff\xfe<\x00h\x00", "garden_text_refused", "utf-8"),
    (b"<html>\x00</html>", "garden_text_refused", "utf-8"),
    ("<p>é</p>".encode("latin-1"), "garden_text_refused", "utf-8"),
    (b"<p>" + b"a" * (garden.HTML_MAX + 1) + b"</p>", "garden_size_refused", "1 MB"),
], ids=["utf16-bom", "nul", "latin1", "over-1mb"])
def test_garden_check_monbanの制限を先に断る(tmp_path, raw, code, detail):
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(_site(tmp_path, raw=raw)))
    assert exc.value.code == code and detail in exc.value.detail


@pytest.mark.parametrize("name", ["a.js", ".hidden.html", "空白 の名前.html", "a.php"])
def test_garden_check_置けない種類と名前(tmp_path, name):
    site = _site(tmp_path, extra={name: b"x"})
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(site))
    assert exc.value.code == "garden_kind_refused"


def test_garden_check_symlinkと空と無いフォルダ(tmp_path):
    site = _site(tmp_path)
    (site / "link.html").symlink_to(site / "index.html")
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(site))
    assert exc.value.code == "garden_kind_refused"
    empty = tmp_path / "empty"
    empty.mkdir()
    for path, code in ((empty, "garden_empty"), (tmp_path / "none", "garden_dir_missing")):
        with pytest.raises(wire.VaultRefused) as exc:
            garden.check_dir(str(path))
        assert exc.value.code == code


def test_garden_check_の制限はmonbanのEdgeと同じ数():
    edge = Path("/Volumes/NexDev/Developer/monban/edge/src/vault.js")
    if not edge.exists():
        pytest.skip("monban の checkout が無い（CI）")
    text = edge.read_text(encoding="utf-8")
    assert "HTML_MAX = 1024 * 1024" in text and "GARDEN_MAX = 100 * 1024 ** 2" in text and "FILES_MAX = 2000" in text
    for kind in garden.TYPES:
        assert f"{kind}:" in text, kind


# 監査 段 1 の 8: CSS の改行は \n・\r\n・\r・\f の 4 つ。\n しか知らない読み手は、閉じない文字列の終わりと \ + CRLF の
# 行継ぎで文字列の内と外をブラウザと逆に読み、後ろの image-set や @import を「読みに行かない文字列」と見ていた。
_EVIL_SET = 'background:image-set("https://evil.example.invalid/x.png" 1x)}'


@pytest.mark.parametrize("css", [
    'a{content:"x\r;' + _EVIL_SET,
    'a{content:"x\f;' + _EVIL_SET,
    'a{quotes:"x\r;' + _EVIL_SET,
    'a[title="x\r]{} b{' + _EVIL_SET,
    'a{content:"a\\\r\n";' + _EVIL_SET,  # \ + CRLF の行継ぎ（8b61eee で開いた形）
    '@charset "/*\r;@import "https://evil.example.invalid/x.css";a{content:"*/"}',
], ids=["cr", "ff", "quotes-cr", "selector-cr", "crlf-continue", "charset-cr-import"])
def test_garden_check_CSSの改行の4つ(tmp_path, css):
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(_site(tmp_path, extra={"style.css": css.encode("utf-8")})))
    assert exc.value.code == "garden_external_refused"


def test_garden_check_CRLFの普通のCSSは通る(tmp_path):
    css = ('body{color:#222}\r\np.note::before{content:"Note: \\\r\n続き"}\r\n'
           'a[href^="https://"]::after{content:" (out)"}\r\n/* 注 */\r\n\fq{quotes:"「" "」"}\r\n')
    assert "/style.css" in garden.check_dir(str(_site(tmp_path, extra={"style.css": css.encode("utf-8")})))["files"]


# 監査 段 1 の 9: @import の前置きの [ ] の塊を数えず、{ をセレクタの始まりと読んでいた。字面の @import の検査は
# "/*"…"*/" の文字列で隠せた。
@pytest.mark.parametrize("css", [
    '@charset "/*";@import "https://evil.example.invalid/x.css" [{}];a{content:"*/"}',
    '@import "https://evil.example.invalid/x.css" [{}];',
    '@charset "/*";@im\\70ort "https://evil.example.invalid/x.css" [{}];a{content:"*/"}',
    '@charset "/*";@import "a.css" [{}];a{content:"*/"}',  # 同じ庭の CSS でも @import は断る（前からの決まり）
    '@font-feature-values "https://evil.example.invalid/x" {}',  # @ の前置きの文字列は読みに行かない物と見ない
    # [ ] の中の { } で宣言が終わったと読まない（var で image-set に渡せるカスタムプロパティの文字列のまま）
    ':root{--u:"https://evil.example.invalid/x.png" [{}]}p{background:image-set(var(--u) 1x)}',
], ids=["hidden-bracket", "bracket", "hidden-escaped", "hidden-local", "at-prelude-string", "custom-prop-bracket"])
def test_garden_check_CSSのimportを字句で見る(tmp_path, css):
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(_site(tmp_path, extra={"style.css": css.encode("utf-8")})))
    assert exc.value.code == "garden_external_refused"


@pytest.mark.parametrize("css", ['a[title="{"]{color:red}', 'a[href^="https://"]{color:red}',
                                 '@media (min-width:40em){a[href$=".pdf"]::after{content:" (PDF)"}}',
                                 '@supports (content:"x"){p{color:red}}', '@charset "utf-8";body{color:#222}'],
                         ids=["brace-in-attr", "attr", "media-attr", "supports", "charset"])
def test_garden_check_CSSの角括弧とat規則の普通の書き方は通る(tmp_path, css):
    assert "/style.css" in garden.check_dir(str(_site(tmp_path, extra={"style.css": css.encode("utf-8")})))["files"]


# 監査 段 1 の 10（THTH の側）: monban の Edge（1e85405）と同じ 2 つを、html・svg・xml で出す前に断る。
_SVG_NS = 'xmlns="http://www.w3.org/2000/svg"'


@pytest.mark.parametrize("name,raw,detail", [
    ("j.svg", f'<?xml version="1.0" encoding="ISO-2022-JP"?><svg {_SVG_NS} on\x1b(Bload="x"/>', "control"),
    ("k.svg", f'<?xml version="1.0" encoding="ISO-2022-JP"?><svg {_SVG_NS}/>', "encoding"),
    ("m.html", '<meta charset="shift_jis"><p>x</p>', "encoding"),
    ("x.xml", "<?xml version='1.0' encoding='EUC-JP'?><r/>", "encoding"),
    ("u.xml", '<?xml version="1.0" encoding="utf-16"?><r/>', "encoding"),
    ("v.svg", f'<?xml version="1.0" encoding="UTF-8x"?><svg {_SVG_NS}/>', "encoding"),
    ("b.svg", f"<svg {_SVG_NS}><text>a\x01b</text></svg>", "control"),
    ("c.xml", "<r>a\x0bb</r>", "control"),
    ("d.html", "<p>a\x0cb</p>", "control"),
    ("e.html", "<p>a\x1fb</p>", "control"),
    ("f.html", "<p>a\x7fb</p>", "control"),
], ids=["iso2022-esc", "iso2022", "html-sjis", "xml-eucjp", "xml-utf16", "utf8x", "svg-01", "xml-0b", "html-0c",
        "html-1f", "html-7f"])
def test_garden_check_制御文字とUTF8以外の宣言はmonbanと同じに断る(tmp_path, name, raw, detail):
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(_site(tmp_path, extra={name: raw.encode("utf-8")})))
    assert exc.value.code == "garden_text_refused" and detail in exc.value.detail


def test_garden_check_UTF8の宣言とタブ改行CRは通る(tmp_path):
    page = GOOD.format(body="<p>a\tb\r\nc</p>").replace('<meta charset="utf-8">',
                                                         '<meta charset="utf-8"><meta charset=UTF-8>')
    extra = {"a.svg": f'<?xml version="1.0" encoding="UTF-8"?><svg {_SVG_NS}/>'.encode("utf-8"),
             "b.xml": b"<?xml version='1.0' encoding='utf-8'?><x/>",
             "c.xml": b'<?xml version="1.0" encoding="utf8" standalone="no"?>\r\n<x>\t</x>'}
    found = garden.check_dir(str(_site(tmp_path, raw=page.encode("utf-8"), extra=extra)))
    assert sorted(found["files"]) == ["/a.svg", "/b.xml", "/c.xml", "/index.html"]


def test_garden_check_制御文字と宣言の型はmonbanのEdgeと同じ():
    edge = Path("/Volumes/NexDev/Developer/monban/edge/src/vault.js")
    if not edge.exists():
        pytest.skip("monban の checkout が無い（CI）")
    text = edge.read_text(encoding="utf-8")
    assert "/" + garden.CONTROL_JS + "/.test(text)" in text
    assert "/" + garden.DECLARED_JS + "/i.test(text)" in text


def test_garden_check_制御文字と宣言の断りに直し方がある():
    entry = rc.get("garden_text_refused")
    for word in ("制御文字", "encoding", "charset", "UTF-8"):
        assert word in entry["why"], word
    assert "&#61;" in entry["fix"]["text"] and "utf-8" in entry["fix"]["text"]


# 監査 段 1 の 11: SVG・XML は xlink の前置きの名前を替えられる（xmlns:z="…xlink" と z:href）。前置きに依らず見る。
_XLINK_Z = 'xmlns:z="http://www.w3.org/1999/xlink"'


@pytest.mark.parametrize("name,raw,code", [
    ("a.svg", f'<svg {_SVG_NS} {_XLINK_Z}><image z:href="https://evil.example.invalid/x.png"/></svg>',
     "garden_external_refused"),
    ("b.svg", f'<svg {_SVG_NS} {_XLINK_Z}><a z:href="javascript:alert(1)"><text>x</text></a></svg>',
     "garden_active_refused"),
    ("c.svg", f'<svg {_SVG_NS} {_XLINK_Z}><use z:href="//evil.example.invalid/s.svg#a"/></svg>',
     "garden_external_refused"),
    ("d.xml", f'<r {_XLINK_Z}><x z:href="https://evil.example.invalid/x.png"/></r>', "garden_external_refused"),
], ids=["svg-image", "svg-a-js", "svg-use", "xml"])
def test_garden_check_xlinkの前置きを替えても見る(tmp_path, name, raw, code):
    with pytest.raises(wire.VaultRefused) as exc:
        garden.check_dir(str(_site(tmp_path, extra={name: raw.encode("utf-8")})))
    assert exc.value.code == code


def test_garden_check_xlinkの前置きの普通の書き方は通る(tmp_path):
    svg = (f'<svg {_SVG_NS} {_XLINK_Z} xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" viewBox="0 0 1 1">'
           '<defs><path id="a" d="M0 0h1"/></defs><use z:href="#a"/><a z:href="https://kopicha.example.invalid/">'
           '<text inkscape:label="店へ">店へ</text></a></svg>')
    assert "/a.svg" in garden.check_dir(str(_site(tmp_path, extra={"a.svg": svg.encode("utf-8")})))["files"]


# 速さ（監査 8b61eee の気づき）: 閉じない url( や注・CDATA・タグが多い物でも、検査が黙って固まらない。直す前は
# 下の大きさで url( が 33 秒・注が 13 秒・CDATA が 9 秒・閉じない <a が 39 秒ほど掛かった（3.12・この端末）。
@pytest.mark.parametrize("kind,text", [
    ("css", "a{b:url(" * 25000), ("css", "a{b:c}/*" * 25000), ("xml", "<r>" + "<![CDATA[" * 20000 + "]]></r>"),
    ("html", "<a " * 20000), ("html", '<p a="' * 20000), ("html", "<title" * 50000), ("html", "<!DOCTYPE" * 60000),
], ids=["css-url", "css-comment", "xml-cdata", "html-open-tag", "html-open-attr", "html-title", "html-doctype"])
def test_garden_check_意地の悪い大きな字でも速い(kind, text):
    import time
    start = time.perf_counter()
    if kind == "css":
        garden.css_problem(text)
    else:
        garden._text_problem("p." + kind, text.encode("utf-8"), kind)
    assert time.perf_counter() - start < 5


# ------------------------------------------------------------------ 庭: 置く（二段確認）

def test_garden_publish_一段目は置かない_二段目で置く(env, capfd):
    site = _site(env["tmp"])
    code, got, _ = _json(["garden", "publish", str(site), "--to", GARDEN], capfd)
    assert code == 1 and got["stage"] == 1 and got["new"] == ["/index.html"] and got["first"]
    assert _calls(env) == []
    code, bad, _ = _json(["garden", "publish", str(site), "--to", GARDEN, "--confirm", "000000000000"], capfd)
    assert bad["code"] == "garden_publish_unconfirmed" and _calls(env) == []
    code, done, _ = _json(["garden", "publish", str(site), "--to", GARDEN, "--confirm", got["digest"]], capfd)
    assert code == 0 and done["ok"] and done["head"] == "b" * 64
    assert "mbk_LIVE" not in json.dumps(done)
    assert [c["argv"] for c in _calls(env)] == [["garden", "publish", GARDEN, str(site.resolve()), "--json"]]
    (site / "items.html").write_text(GOOD.format(body="<p>一覧</p>"), encoding="utf-8")
    code, got2, _ = _json(["garden", "publish", str(site), "--to", GARDEN], capfd)
    assert got2["new"] == ["/items.html"] and got2["changed"] == [] and not got2["first"]


def test_garden_publish_検査に通らなければmonbanを呼ばない(env, capfd):
    site = _site(env["tmp"], "<script>x</script>")
    code, got, _ = _json(["garden", "publish", str(site), "--to", GARDEN, "--confirm", "x" * 12], capfd)
    assert code == 2 and got["code"] == "garden_active_refused" and _calls(env) == []
    code, got, _ = _json(["garden", "publish", str(site), "--to", "monban://thth/acct/www"], capfd)
    assert got["code"] == "garden_args_invalid"


@pytest.mark.parametrize("mode,code", [("token_missing", "garden_not_ready"), ("leak", "garden_login_required")])
def test_garden_publish_monbanの断り_文を写さない(env, capfd, monkeypatch, mode, code):
    monkeypatch.setenv("FAKE_VAULT_MODE", mode)
    site = _site(env["tmp"])
    digest = garden.plan(str(site), GARDEN)["digest"]
    for argv in (["garden", "publish", str(site), "--to", GARDEN, "--confirm", digest],
                 ["garden", "publish", str(site), "--to", GARDEN, "--confirm", digest, "--json"]):
        rc_, out, err = _main(argv, capfd)
        assert rc_ == 2 and code in out + err
        assert "mbk_LIVE" not in out + err and RECOVERY not in out + err


def test_garden_stop_は確認なしですぐ(env, capfd):
    code, got, _ = _json(["garden", "stop", GARDEN], capfd)
    assert code == 0 and got["owner_stopped"] is True
    assert [c["argv"] for c in _calls(env)] == [["garden", "stop", GARDEN, "--json"]]


@pytest.mark.parametrize("stops,words,serving", [("", "配っています", True), ("owner", "自分で止めました", False),
                                                ("operator", "運営者が止めました", False)])
def test_garden_status_止めを2つとも読む(env, capfd, monkeypatch, stops, words, serving):
    monkeypatch.setenv("FAKE_GARDEN_STOPS", stops)
    code, out, _ = _main(["garden", "status", GARDEN], capfd)
    assert code == 0 and words in out
    code, got, _ = _json(["garden", "status", GARDEN], capfd)
    assert got["serving"] is serving and got["owner_stopped"] is ("owner" in stops)
    assert got["operator_stopped"] is ("operator" in stops)


def test_garden_build_出す先のsymlinkは上書きしない(env, capfd):
    _snapshot(env)
    out_dir = env["tmp"] / "site"
    out_dir.mkdir()
    target = env["tmp"] / "大事.html"
    target.write_text("消さないで", encoding="utf-8")
    (out_dir / "index.html").symlink_to(target)
    assert _json(["garden", "build", "kopicha-base", "--out", str(out_dir)], capfd)[1]["code"] == "garden_out_not_empty"
    assert target.read_text(encoding="utf-8") == "消さないで"


def test_garden_道具が無ければ入れ方を言う(env, capfd):
    (env["bin"] / "monban-vault").unlink()
    site = _site(env["tmp"])
    digest = garden.plan(str(site), GARDEN)["digest"]
    code, got, _ = _json(["garden", "publish", str(site), "--to", GARDEN, "--confirm", digest], capfd)
    assert got["code"] == "garden_tool_missing" and "pip install" in got["fix"]


# ------------------------------------------------------------------ 目録と関門

def test_符丁はすべて目録にあり3つがそろう():
    for code in wire.REASONS:
        entry = rc.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"] and entry["no_better"] is None, code


def test_monbanの符丁の写しは目録の符丁だけ():
    assert set(sync.MONBAN_TO_SYNC.values()) <= wire.REASONS
    assert set(garden.MONBAN_TO_GARDEN.values()) <= wire.REASONS


def test_関門の危ない領域に金庫と庭の口がある():
    text = (ROOT / "tools" / "release_gate.py").read_text(encoding="utf-8")
    assert '"vault_garden": ("thth/vault/*",)' in text
