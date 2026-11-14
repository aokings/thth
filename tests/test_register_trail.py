"""台帳の器 段 5「跡」（指示 `docs/指示_台帳の器_段5_跡と消す求め_2026-11-14.md` §2・§1 の 1・2・4）。

- §2.1 git の commit どうしの差（`register diff <前>..<後>` が commit を受ける・作業木は変えない・番号が消えたら赤・
  消す求めの結果に載った番号を通す差し込み口・取り出せない・危ない名を git に渡さない）
- §2.2 判断の記録は足すだけ（`check --against <前>`・消す求めの印の行を通す差し込み口）
- §2.3 取り込みの跡（`adopt`・`merge` の `--checked-by` が `links` と `diff` に出る）
- §2.4 判断から、版と元の記録へ（`inspects` の表の行までたどれ、中身を変えると古い）

見本は段 1 の `tests/fixtures/register_vessel/kopicha`（こぴちゃの形・中央の判断の表を持つ）と `registers.example/
media-sample`（今の頭つきの台帳）。git の repo は試験の中で一時のフォルダに作る。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import subprocess

import pytest

from thth import cli
from thth.register import adopt as A, judgment as J, keep as K, trail as T, vessel as V, vessel_cli
from thth.register.errors import RegisterRefused

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "register_vessel"
EXAMPLES = ROOT / "registers.example"
AT = "2026-11-14T10:00:00+09:00"
CLAIMS = "data/knowledge/claims.csv"
MILK = "claim:c-term-tea-grade-ctc-res-milktea1-24"
GIT_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_AUTHOR_DATE": "2026-11-14T10:00:00+09:00", "GIT_COMMITTER_DATE": "2026-11-14T10:00:00+09:00"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    for key, value in [("THTH_ROOT", root), ("HOME", home), ("XDG_CONFIG_HOME", home)]:
        monkeypatch.setenv(key, str(value))
    return {"tmp": tmp_path, "root": root}


def run(capsys, *argv):
    rc_ = cli.main(list(argv))
    out, err = capsys.readouterr()
    return rc_, out, err


def jrun(capsys, *argv):
    rc_, out, err = run(capsys, *argv, "--json")
    return rc_, json.loads(out), err


def git(repo: pathlib.Path, *args) -> str:
    result = subprocess.run([T.git_binary(), "-C", str(repo), *args], capture_output=True, env=GIT_ENV, check=True)
    return result.stdout.decode("utf-8").strip()


def commit(repo: pathlib.Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def git_repo(env, name="reg") -> pathlib.Path:
    repo = env["tmp"] / name
    shutil.copytree(FIX / "kopicha", repo)
    git(repo, "init", "-q", "-b", "main")
    commit(repo, "c0")
    return repo


def edit(path: pathlib.Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1
    path.write_text(text.replace(old, new), encoding="utf-8")


def snapshot_tree(repo: pathlib.Path) -> dict:
    """作業木の全部（.git の中は index と HEAD だけ）の道 → (大きさ・変えた時刻・sha256)。"""
    out = {}
    for path in sorted(repo.rglob("*")):
        rel = str(path.relative_to(repo))
        if rel.startswith(".git" + os.sep) and rel not in (os.path.join(".git", "index"), os.path.join(".git", "HEAD")):
            continue
        if path.is_file():
            st = path.stat()
            out[rel] = (st.st_size, st.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
    return out


def add_register(capsys, path, name):
    rc_, _out, err = run(capsys, "register", "add", name, "--path", str(path), "--project", "sample", "--kind",
                         "media", "--by", "m")
    assert rc_ == 0, err


# ================================================================== §2.1 git の commit どうしの差

def test_diff_commitどうしで足した直した消えた記録だけを出し番号が消えたら赤(capsys, env):
    repo = git_repo(env)
    claims = repo / CLAIMS
    with claims.open("a", encoding="utf-8") as f:
        f.write("c-new-1,term-ctc,has_value,,新しい値,新しい主張,quantity,,supported,,,,\n")
    c1 = commit(repo, "足す")
    edit(claims, "CTC 茶葉の水浸出物は 17.3% とされる", "CTC 茶葉の水浸出物は 17.1% とされる")
    c2 = commit(repo, "直す")
    rc_, value, err = jrun(capsys, "register", "diff", str(repo), "HEAD~2..HEAD~1")
    assert rc_ == 0, err
    assert value["records"]["added"] == ["claim:c-new-1"]
    assert value["records"]["changed"] == [] and value["records"]["vanished"] == []
    assert value["files"]["changed"] == [CLAIMS]
    assert [s["side"] for s in value["sides"]] == ["commit", "commit"] and value["sides"][1]["commit"] == c1
    rc_, value, err = jrun(capsys, "register", "diff", str(repo), f"{c1}..main")
    assert rc_ == 0, err
    assert value["records"]["added"] == [] and value["records"]["vanished"] == []
    assert [c["record"] for c in value["records"]["changed"]] == [MILK]
    assert value["records"]["changed"][0]["candidates"] == []
    assert value["sides"][1]["commit"] == c2 and value["intakes"] is None
    # 消す
    text = claims.read_text(encoding="utf-8").splitlines(keepends=True)
    claims.write_text("".join(line for line in text if not line.startswith("c-origin-brazil-cerrado-003,")),
                      encoding="utf-8")
    commit(repo, "消す")
    before = snapshot_tree(repo)
    rc_, out, _ = run(capsys, "register", "diff", str(repo), "HEAD~1..HEAD")
    assert rc_ != 0 and "claim:c-origin-brazil-cerrado-003" in out
    rc_, value, _ = jrun(capsys, "register", "diff", str(repo), "HEAD~1..HEAD")
    assert value["code"] == "register_record_vanished"
    assert value["vanished"] == ["claim:c-origin-brazil-cerrado-003"]
    # 消す求めの結果に載った番号は赤にしない（差し込み口）。載っていない番号は赤のまま。
    passed = A.diff(str(repo), "HEAD~1..HEAD", excused={"claim:c-origin-brazil-cerrado-003"})
    assert not passed["red"] and passed["excused"] == ["claim:c-origin-brazil-cerrado-003"]
    assert A.diff(str(repo), "HEAD~1..HEAD", excused={"claim:other"})["red"]
    # 作業木（と index・HEAD）は 1 バイトも変えていない。今の端との差も取れる。
    assert A.diff(str(repo), "HEAD..now")["records"]["changed"] == []
    assert snapshot_tree(repo) == before


def test_diff_commitの端は宣言の置き場の枚だけを読みその時点に無い種類を出す(capsys, env):
    repo = env["tmp"] / "reg"
    shutil.copytree(FIX / "kopicha", repo)
    (repo / "README.md").write_text("範囲の外\n", encoding="utf-8")
    origins = repo / "src/content/origins"
    moved = env["tmp"] / "origins-later"
    shutil.move(str(origins), str(moved))
    git(repo, "init", "-q", "-b", "main")
    c0 = commit(repo, "産地の枚はまだ無い")
    shutil.move(str(moved), str(origins))
    (repo / "README.md").write_text("範囲の外が変わった\n", encoding="utf-8")
    commit(repo, "産地の枚を足す")
    value = A.diff(str(repo), f"{c0}..HEAD")
    assert value["sides"][0]["absent_kinds"] == ["page"] and value["sides"][1]["absent_kinds"] == []
    assert value["records"]["added"] == ["page:brazil", "page:kyoto"] and not value["red"]
    assert value["files"]["added"] == ["src/content/origins/brazil.yaml", "src/content/origins/kyoto.yaml"]
    assert value["files"]["changed"] == []  # 範囲の外（README.md）は取り出さない
    # 消えた種類（後の端に無い）は、記録が消えたので赤。
    assert A.diff(str(repo), f"HEAD..{c0}")["red"]
    rc_, out, err = run(capsys, "register", "diff", str(repo), f"{c0}..HEAD")
    assert rc_ == 0, err and "その時点に無い種類: page" in out


def test_diff_gitの作業木でない台帳と宣言の置き場が無いcommitは取り出せないと言う(capsys, env):
    plain = env["tmp"] / "plain"
    shutil.copytree(FIX / "kopicha", plain)
    rc_, value, _ = jrun(capsys, "register", "diff", str(plain), "HEAD..now")
    assert rc_ != 0 and value["code"] == "register_kept_missing" and value["detail"] == "not_git"
    rc_, out, err = run(capsys, "register", "diff", str(plain), "HEAD..now")
    assert rc_ != 0 and "keep" in out + err  # 控えの名で打ち直す道を添える
    # 控えの名の形（16 進 8 字以上）で控えに無い物は、今までどおり控えの断り。
    rc_, value, _ = jrun(capsys, "register", "diff", str(plain), "0123456789abcdef..now")
    assert value["code"] == "register_kept_missing" and value["detail"] == "snapshot"
    repo = env["tmp"] / "empty"
    repo.mkdir()
    (repo / ".thth").mkdir()
    shutil.copytree(FIX / "kopicha" / ".thth" / "kinds", repo / ".thth" / "kinds")
    (repo / "README.md").write_text("まだ何も無い\n", encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    commit(repo, "宣言だけ")
    shutil.copytree(FIX / "kopicha" / "data", repo / "data")
    commit(repo, "台帳の枚を足す")
    rc_, value, _ = jrun(capsys, "register", "diff", str(repo), "HEAD~1..HEAD")
    assert rc_ != 0 and value["code"] == "register_kept_missing" and value["detail"] == "no_places"
    rc_, value, _ = jrun(capsys, "register", "diff", str(repo), "nosuchbranch..HEAD")
    assert value["code"] == "register_kept_missing" and value["detail"] == "unknown_commit"


@pytest.mark.parametrize("name", ["--output=PWN", "-c", "HEAD;touch PWN", "HEAD 1", "HEAD:data", "@{-1}",
                                  "--end-of-options", "$(touch PWN)", "HEAD\nx", "a//b", "x.lock"])
def test_diff_commitの名の危ない字はgitの引数として読ませず断る(capsys, env, name):
    repo = git_repo(env)
    cwd = os.getcwd()
    os.chdir(env["tmp"])
    try:
        for spec in (f"{name}..HEAD", f"HEAD..{name}"):
            with pytest.raises(RegisterRefused) as caught:
                A.diff(str(repo), spec)
            assert caught.value.code == "register_args_invalid" and caught.value.detail == "commit"
        if not name.startswith("-"):  # `-` で始まる名は、命令の引数の読み手が先に断る
            rc_, value, _ = jrun(capsys, "register", "diff", str(repo), f"{name}..HEAD")
            assert rc_ != 0 and value["code"] == "register_args_invalid" and value["detail"] == "commit"
    finally:
        os.chdir(cwd)
    assert not (env["tmp"] / "PWN").exists() and not (repo / "PWN").exists()
    with pytest.raises(RegisterRefused):
        T.check_rev(name)


def test_diff_今の頭つきの台帳でcommitの間に退役した記録を出す(capsys, env):
    repo = env["tmp"] / "sample"
    shutil.copytree(EXAMPLES / "media-sample", repo)
    git(repo, "init", "-q", "-b", "main")
    commit(repo, "c0")
    path = repo / "事実" / "開講の時期.md"
    path.write_text(path.read_text(encoding="utf-8").replace("status: 試し", "status: 退役"), encoding="utf-8")
    commit(repo, "退役")
    rc_, value, err = jrun(capsys, "register", "diff", str(repo), "HEAD~1..HEAD")
    assert rc_ == 0, err
    assert value["records"]["retired"] == ["thth_row:fact-term-start"]
    assert [c["record"] for c in value["records"]["changed"]] == ["thth_row:fact-term-start"]


# ================================================================== §2.2 判断の記録は足すだけ

def _judge(root: pathlib.Path, ref: str, conclusion: str = "good", at: str = AT) -> dict:
    v = V.open_vessel(str(root))
    return J.make(v, {"record": ref}, "says_so", by="v", at=at, conclusion=conclusion)


def test_check_against_足しただけは緑で直す消す並べ替えるは赤(capsys, env):
    repo = git_repo(env)
    J.append(str(repo), [_judge(repo, MILK), _judge(repo, "claim:c-origin-brazil-024")])
    commit(repo, "判断 2 行")
    add_register(capsys, repo, "kopt")
    path = repo / ".thth" / "judgments.jsonl"
    J.append(str(repo), [_judge(repo, "claim:c-origin-brazil-cerrado-003")])
    rc_, value, err = jrun(capsys, "register", "check", "kopt", "--against", "HEAD")
    assert rc_ == 0, err
    assert value["against"]["continues"] and value["against"]["lines_before"] == 2
    rc_, out, err = run(capsys, "register", "check", "kopt", "--against", "HEAD")
    assert rc_ == 0 and "続き" in out
    lines = path.read_bytes().splitlines(keepends=True)
    cases = {
        "直す": [lines[0].replace(b'"good"', b'"bad"'), *lines[1:]],
        "消す": [lines[1], lines[2]],
        "並べ替える": [lines[1], lines[0], lines[2]],
        "最後を消す": [lines[0]],
    }
    for word, body in cases.items():
        path.write_bytes(b"".join(body))
        rc_, value, _ = jrun(capsys, "register", "check", "kopt", "--against", "HEAD")
        assert rc_ != 0 and value["code"] == "register_judgment_invalid", word
        assert value["detail"].startswith("judgments_rewritten"), word
    path.write_bytes(b"".join(lines))
    rc_, value, _ = jrun(capsys, "register", "check", "kopt", "--against", "HEAD;touch PWN")
    assert rc_ != 0 and value["code"] == "register_args_invalid" and value["detail"] == "commit"
    rc_, value, _ = jrun(capsys, "register", "check", "kopt", "--against", "now")
    assert rc_ != 0 and value["code"] == "register_args_invalid"
    # 前の時点に判断の記録が無ければ、何を足しても続き。
    rc_, value, err = jrun(capsys, "register", "check", "kopt", "--against", "HEAD~1")
    assert rc_ == 0, err


def test_check_against_控えの名でも前の時点の判断の記録と比べる(capsys, env):
    repo = env["tmp"] / "plain"
    shutil.copytree(FIX / "kopicha", repo)
    J.append(str(repo), [_judge(repo, MILK)])
    add_register(capsys, repo, "plain")
    rc_, kept, err = jrun(capsys, "register", "keep", str(repo), "--by", "m")
    assert rc_ == 0, err
    J.append(str(repo), [_judge(repo, "claim:c-origin-brazil-024")])
    rc_, value, err = jrun(capsys, "register", "check", "plain", "--against", kept["fp"][:12])
    assert rc_ == 0, err and value["against"]["from"] == "snapshot"
    path = repo / ".thth" / "judgments.jsonl"
    path.write_bytes(path.read_bytes().replace(b'"good"', b'"bad"', 1))
    rc_, value, _ = jrun(capsys, "register", "check", "plain", "--against", kept["fp"][:12])
    assert rc_ != 0 and value["detail"] == "judgments_rewritten line 1"
    # git の作業木でない台帳で、控えの名でない名は「取り出せない」。
    rc_, value, _ = jrun(capsys, "register", "check", "plain", "--against", "HEAD")
    assert rc_ != 0 and value["code"] == "register_kept_missing" and value["detail"] == "not_git"


def test_check_against_消す求めの結果に載った印の行だけを骨で通す(env):
    a = json.dumps({"id": "j-1", "question": "says_so", "target": {"record": MILK}, "answer": {"conclusion": "good",
                    "word": None}, "at": AT, "seen": {"version": "v", "depend": "d"}}).encode() + b"\n"
    b = json.dumps({"id": "j-2", "question": "says_so", "target": {"record": "claim:x"},
                    "answer": {"conclusion": "bad", "word": None}, "at": AT}).encode() + b"\n"
    marked = json.dumps({"id": "x-0123456789abcdef", "question": "says_so", "target": {"record": MILK},
                         "answer": {"conclusion": "good", "word": None}, "at": AT,
                         "erased": {"request": "e-0123456789abcdef"}}).encode() + b"\n"
    erase = {"requests": {"e-0123456789abcdef"}, "lines": {"x-0123456789abcdef"}}
    prev = a + b
    assert T.judgments_continue(prev, prev + b)["continues"]
    assert not T.judgments_continue(prev, marked + b)["continues"]           # 結果が無ければ赤
    ok = T.judgments_continue(prev, marked + b + a, erase)
    assert ok["continues"] and ok["excused"] == [1]                           # 番号は付け直してあってよい（骨に無い）
    assert not T.judgments_continue(prev, marked + marked, erase)["continues"]  # 2 行めは結果に無い
    assert not T.judgments_continue(prev, marked.replace(b'"good"', b'"bad"') + b, erase)["continues"]  # 結論
    assert not T.judgments_continue(prev, marked.replace(b"x-0123", b"x-9999") + b, erase)["continues"]  # 番号
    other = dict(erase, requests={"e-ffffffffffffffff"})
    assert not T.judgments_continue(prev, marked + b, other)["continues"]     # 求めが結果に無い
    assert not T.judgments_continue(prev, a.replace(b'"seen"', b'"seen2"') + b, erase)["continues"]  # 印でない
    assert not T.judgments_continue(prev, marked, erase)["continues"]         # 行が減った
    assert not T.judgments_continue(prev, b + marked, erase)["continues"]     # 並べ替え（位置で比べる）
    assert T.judgments_continue(a.rstrip(b"\n"), a + b)["continues"]         # 前の最後の行に改行が無い
    # 骨の定めは 1 か所（CI の続きの比べと同じ物）
    from thth.register import route as R
    assert R.line_skeleton is J.line_skeleton


# ================================================================== §2.3 取り込みの跡

def _source(env) -> pathlib.Path:
    src = env["tmp"] / "src"
    shutil.copytree(FIX / "kopicha", src, ignore=shutil.ignore_patterns(".thth"))
    return src


def test_adoptとmergeのchecked_byがlinksとdiffに出る(capsys, env):
    src = _source(env)
    reg = env["tmp"] / "reg"
    decl = FIX / "kopicha" / ".thth"
    rc_, value, err = jrun(capsys, "register", "adopt", str(src), "--to", str(reg), "--by", "集める者",
                           "--decl", str(decl))
    assert rc_ == 0, err and value["checked_by"] is None
    first = K.load_snapshot(str(reg), "first")
    assert first["trail"][-1]["by"] == "集める者" and first["trail"][-1]["checked_by"] is None
    assert first["trail"][-1]["bundle_fp"] == value["source_fp"]
    links = vessel_cli.links_of(str(reg), MILK)
    assert links["entered"]["kept"] and links["entered"]["what"] == "adopt"
    assert links["entered"]["by"] == "集める者" and links["entered"]["checked"] is False
    add_register(capsys, reg, "kop")
    rc_, out, err = run(capsys, "register", "links", "kop", MILK)
    assert rc_ == 0, err
    assert "集めた者 集める者" in out and "検めていない" in out
    # 束で 1 行を直して入れる（検めた者あり）。
    bundle = env["tmp"] / "bundle"
    (bundle / "data/knowledge").mkdir(parents=True)
    data = (reg / CLAIMS).read_text(encoding="utf-8").replace("CTC 茶葉の水浸出物は 17.3% とされる",
                                                              "CTC 茶葉の水浸出物は 17.1% とされる")
    (bundle / CLAIMS).write_text(data, encoding="utf-8")
    rc_, merged, err = jrun(capsys, "register", "merge", str(reg), str(bundle), "--by", "集める者",
                            "--checked-by", "検める者")
    assert rc_ == 0, err and merged["checked_by"] == "検める者"
    links = vessel_cli.links_of(str(reg), MILK)
    assert links["entered"]["what"] == "merge" and links["entered"]["checked_by"] == "検める者"
    assert links["entered"]["snapshot"] == merged["snapshot"] and links["entered"]["checked"]
    # 束で変わっていない記録は、最初の取り込みで入ったまま。
    assert vessel_cli.links_of(str(reg), "claim:c-origin-brazil-024")["entered"]["what"] == "adopt"
    rc_, value, err = jrun(capsys, "register", "diff", str(reg), "first..last")
    assert rc_ == 0, err
    assert [c["record"] for c in value["records"]["changed"]] == [MILK]
    assert [(i["what"], i["by"], i["checked_by"]) for i in value["intakes"]] == [("merge", "集める者", "検める者")]
    rc_, out, err = run(capsys, "register", "diff", str(reg), "first..now")
    assert rc_ == 0, err and "検めた者 検める者" in out
    # 取り込み直し（検めた者あり）も跡に残る。控えのあとに直に書いた版は「控えに無い」。
    rc_, value, err = jrun(capsys, "register", "adopt", str(src), "--to", str(reg), "--by", "集める者",
                           "--checked-by", "別の者")
    assert rc_ == 0, err
    assert vessel_cli.links_of(str(reg), MILK)["entered"]["what"] == "readopt"
    assert vessel_cli.links_of(str(reg), MILK)["entered"]["checked_by"] == "別の者"
    edit(reg / CLAIMS, "CTC 茶葉の水浸出物は 17.3% とされる", "CTC 茶葉の水浸出物は 17.0% とされる")
    assert vessel_cli.links_of(str(reg), MILK)["entered"] == {"kept": False}


def test_checked_byの名の形を断る(capsys, env):
    src = _source(env)
    rc_, value, _ = jrun(capsys, "register", "adopt", str(src), "--to", str(env["tmp"] / "reg"), "--by", "m",
                         "--checked-by", "a\nb", "--decl", str(FIX / "kopicha" / ".thth"))
    assert rc_ != 0 and value["code"] == "register_args_invalid" and value["detail"] == "checked_by"


# ================================================================== §2.4 判断から、版と元の記録へ

def test_links_判断から元の表の行と対象の版へたどれ中身を変えると古い(capsys, env):
    root = env["tmp"] / "kop"
    shutil.copytree(FIX / "kopicha", root)
    v = V.open_vessel(str(root))
    insp = {j["source"]: j for j in J.inspections(v)}
    assert insp["central:e-milk-01"]["target"]["record"] == MILK
    value = vessel_cli.links_of(str(root), MILK)
    [central] = [j for j in value["judgments"] if j["origin"].get("table") == "central"]
    assert central["origin"]["row"] == "e-milk-01"
    assert central["origin"]["file"] == "data/knowledge/staging/k1/central.csv"
    assert central["content"] and central["state"] == "stale"  # 中央は 17.1%・台帳はまだ 17.3%
    # 台帳を中央の判断の文に合わせると効く（K-1 の訂正の形）。
    edit(root / CLAIMS, "水浸出物 17.3%,CTC 茶葉の水浸出物は 17.3% とされる",
         "水浸出物 17.1%,CTC 茶葉の水浸出物は 17.1% とされ、テアニンは 18.6% とされる")
    value = vessel_cli.links_of(str(root), MILK)
    [central] = [j for j in value["judgments"] if j["origin"].get("table") == "central"]
    assert central["state"] == "effective"
    assert central["depend"] == J.current_depend(V.open_vessel(str(root)), {"record": MILK}, "says_so")
    # 命令で足した判断は、誰が・いつと、対象の版を持つ。
    made = _judge(root, MILK)
    J.append(str(root), [made])
    value = vessel_cli.links_of(str(root), MILK)
    [mine] = [j for j in value["judgments"] if j["id"] == made["id"]]
    assert mine["origin"] == {"by": "v", "at": AT, "path": "inspection"}
    assert mine["version"] == V.open_vessel(str(root)).get(MILK).version and mine["state"] == "effective"
    # 中身を変えると、どちらも古い。
    edit(root / CLAIMS, "テアニンは 18.6% とされる", "テアニンは 18.0% とされる")
    value = vessel_cli.links_of(str(root), MILK)
    assert {j["state"] for j in value["judgments"]} == {"stale"}
    add_register(capsys, root, "kop")
    rc_, out, err = run(capsys, "register", "links", "kop", MILK)
    assert rc_ == 0, err
    assert "元の表 central の行 e-milk-01" in out and "古い" in out and "命令で足した（v・" in out


def test_links_版を示していない検収の記録は判断に数えず元の表の行を引け効くかは言えない(capsys, env):
    root = env["tmp"] / "kop"
    shutil.copytree(FIX / "kopicha", root)
    # キョンシーの原文確認の記録の形: 日付と根拠の番号と誰がだけ（確かめた中身・版を持たない）
    (root / ".thth/kinds/check_log.json").write_text(json.dumps({
        "schema": "thth.kind/1", "kind": "check_log", "place": "data/knowledge/check-log.csv", "format": "csv",
        "id": {"key": ["checked_at", "evidence_id"]},
        "pointers": [{"field": "evidence_id", "meaning": "about", "to": "evidence"}],
        "inspects": {"follow": ["evidence_id", "claim_id"], "question": "says_so", "by": "integrator",
                     "content": {"claim_status": "verdict"}}}), encoding="utf-8")
    (root / "data/knowledge/check-log.csv").write_text(
        "checked_at,evidence_id,who,verdict\n2026-09-24,e-milk-01,human,supported\n", encoding="utf-8")
    value = vessel_cli.links_of(str(root), MILK)
    assert all(j["origin"].get("table") != "check_log" for j in value["judgments"])
    [u] = value["unversioned"]
    assert u["table"] == "check_log" and u["file"] == "data/knowledge/check-log.csv"
    assert u["state"] == "cannot_say" and u["why"] == "version_not_shown"
    assert "statement_ja" in u["fields_needed"] and "statement_ja" not in u["content"]
    add_register(capsys, root, "kop")
    rc_, out, err = run(capsys, "register", "links", "kop", MILK)
    assert rc_ == 0, err and "版を示していない検収の記録: 元の表 check_log" in out and "言えない" in out


def test_git_を呼ぶ部品はtrailだけで決まった場所の物を空の環境で使う():
    assert T.git_binary() in T.GIT_BINARIES
    assert T._ENV["PATH"] == "/usr/bin:/bin" and T._ENV["GIT_CONFIG_GLOBAL"] == "/dev/null"
    register = ROOT / "thth" / "register"
    users = sorted(p.name for p in register.glob("*.py") if "subprocess" in p.read_text(encoding="utf-8"))
    assert "trail.py" in users and "adopt.py" not in users and "vessel_cli.py" not in users
