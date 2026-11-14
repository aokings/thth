"""台帳の器 段 2「7 つを戻す・取り込んだあとに 1 回直す」— 受け入れの試験 2「保存したあとも、今ある道具で育て続けられる」。

物差しは `docs/期待する答え_台帳の器_段1〜3_2026-11-13.md` の §3（(a)〜(d)）。見本は repo の中の小さな物:

- (a) 7 つのメディアの形を小さく写した元のフォルダを、試験の中で作る（表・YAML・JSON・md・バイナリ・混ざった改行・
  BOM・最後の改行が無い表・空のファイル・実行の印・git の外のファイル・範囲の外の物）。範囲は目録 7 枚の §1 と
  レーンの答え（染めと化学の `docs/hatena/`・キョンシーの `stories/`・にがみラボの `data/loves/` と `data/doi.json`
  は入れない、など）。
- (b)〜(d) は段 1 の見本（`tests/fixtures/register_vessel/` の kopicha・kanto・mmr）を元のフォルダとして取り込む。
  宣言は元の `.thth/` を範囲の外にして、`--decl` で台帳の側に置く。

［レーン待ち］の項は、期待する答えに書かれた答えで組み、試験の名前に「レーン待ち」と残した。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

from thth import cli
from thth.register import adopt as A, judgment as J, keep as K, vessel as V

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "register_vessel"
EXAMPLES = ROOT / "registers.example"
AT = "2026-11-14T10:00:00+09:00"


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


def write(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def write_plan(tmp: pathlib.Path, name: str, include, exclude=()) -> pathlib.Path:
    path = tmp / f"plan-{name}.json"
    path.write_text(json.dumps({"schema": "thth.adopt_plan/1", "include": list(include), "exclude": list(exclude)},
                               ensure_ascii=False), encoding="utf-8")
    return path


def stat_all(root: pathlib.Path) -> dict:
    """元のフォルダの全部（範囲の外も・.git も）の道 → (大きさ, 変えた時刻, sha256)。"""
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            st = path.stat()
            out[str(path.relative_to(root))] = (st.st_size, st.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
    return out


def sha_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ================================================================== 7 つのメディアの形（小さな見本）

def _kopicha(src: pathlib.Path):
    shutil.copytree(FIX / "kopicha", src, ignore=shutil.ignore_patterns(".thth"))
    write(src / "data/knowledge/sources.csv", "id,url,title\ns-1,https://example.invalid/1,一\ns-2,https://example.invalid/2,二\n")
    write(src / "data/knowledge/leads.csv", "id,question_ja,status\nl-1,茶の等級は？,open\n")
    write(src / "src/content/articles/milk-tea.md", "---\ntitle: ミルクティー\n---\n本文〔c-term-tea-grade-ctc-res-milktea1-24〕\n")
    write(src / "data/sister-links.csv", "kopicha_id,sister,url\norigin-kyoto,nigamilab,https://example.invalid/n\n")
    write(src / "data/article-links.csv", "from,to\nmilk-tea,brazil\n")
    write(src / "data/hatena/bookmark-list.csv",
          "url,title,status\nhttps://example.invalid/a,あ,queued 2026-11-01\nhttps://example.invalid/b,い,todo\n")
    write(src / "data/sns/post-1.json", '{"text": "ほかの人の投稿"}\n')
    write(src / "src/data/catalog/x.json", '{"price": 1}\n')
    return ["src/content/**", "data/knowledge/*.csv", "data/knowledge/staging/*/central.csv", "data/sister-links.csv",
            "data/article-links.csv", "data/hatena/bookmark-list.csv"], [], ["data/sns/post-1.json",
                                                                              "src/data/catalog/x.json"]


def _nigami(src: pathlib.Path):
    write(src / "data/claims.csv", "id,statement\r\nn-1,\"苦い,とされる\"\r\nn-2,\"改行を\r\n含む\"\r\n".encode())
    write(src / "data/topic-hubs.csv", b"id,title\r\nt-1,A\r\nt-2,B\nt-3,C\n\nt-4,D\n")
    write(src / "data/entity-media.csv", b"id,file\nm-1,a.jpg\n")
    write(src / "data/sensory_measurements.csv", b"id,value\n")
    write(src / "data/questions/q-1/question.csv", b"id,text\nq-1,\xe8\x8b\xa6\xe5\x91\xb3\xe3\x81\xaf\xef\xbc\x9f\n")
    write(src / "data/i18n/en/texts.csv", "key,en,source_sha\nk1,Bitter,abc\n")
    write(src / "schema/schema.sql", "CREATE TABLE claims (id TEXT);")  # 最後の改行が無い
    write(src / "data/loves/records.csv", "id\n")
    write(src / "data/doi.json", '{"doi": "10.0/x"}\n')
    write(src / "data/sns/a.ndjson", "{}\n")
    write(src / "data/hatena/memos.csv", "id\n")
    return ["data/*.csv", "data/questions/**", "data/i18n/en/*.csv", "schema/*"], [], [
        "data/loves/records.csv", "data/doi.json", "data/sns/a.ndjson", "data/hatena/memos.csv"]


def _sometokagaku(src: pathlib.Path):
    write(src / "data/claims.csv", "﻿id,statement,claim_status\nclaim-0002,藍は還元して染める,supported\n")
    write(src / "data/observations.csv", "id,note\n")
    write(src / "site/src/content/articles/a.md", "# 藍\n本文〔claim-0002〕\n")
    write(src / "site/src/content/pillars/p.md", "柱\n")
    write(src / "site/src/content/pillars-en/p.md", "pillar\n")
    write(src / "site/src/lib/entity-page-rules.ts", "export const HIDDEN_CLAIM_IDS = new Set([\n  'claim-0121',\n]);\n")
    write(src / "site/photos.yaml", "- file: a.jpg\n  credit: 自分\n")
    write(src / "site/owner-read.txt", "# パス 読んだ日 メモ\nsite/src/content/articles/a.md 2026-10-01 よい\n")
    write(src / "docs/hatena/札_1.md", "札\n")
    write(src / "docs/hatena/bookmark-list.csv", "url\n")
    return ["data/*.csv", "site/src/content/**", "site/src/lib/entity-page-rules.ts", "site/photos.yaml",
            "site/owner-read.txt"], [], ["docs/hatena/札_1.md", "docs/hatena/bookmark-list.csv"]


def _jiangshi(src: pathlib.Path):
    write(src / "data/claims.csv", "id,statement\nj-1,キョンシーは跳ねる\n")
    write(src / "data/observations.csv", "id,scene\no-1,場面\n")
    write(src / "lab.yaml", "name: キョンシーラボ\ndecisions:\n  - id: 13\n    text: 検索への公開は記事 6 と同時\n")
    write(src / "drafts/article-1.md", "---\ntitle: 一\n---\n本文\n")
    write(src / "images/files/fig-1.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(range(256)) + b"\r\n\r")
    write(src / "audit/reader/2026-11-01.json", '{"base": "abc", "checked": [{"file": "drafts/article-1.md", "line": 3}]}\n')
    write(src / "stories/s-1.md", "短編\n")
    write(src / "settings/world.md", "設定\n")
    return ["data/*.csv", "lab.yaml", "drafts/*.md", "images/files/*", "audit/reader/*.json"], [], [
        "stories/s-1.md", "settings/world.md"]


QUEUE = [
    {"seed_id": "seed-liked-0161", "research_order": 1, "title": "曲 A", "artists": ["人 1"], "album": "盤 1",
     "duration": 201, "liked_added_date": "2026-01-02", "liked_added_display": "1 月 2 日", "date_basis": "liked",
     "position": 1, "relative_unit": 0.1, "research_status": "audited", "assigned_at": "2026-09-01",
     "moodpass_wave": 3},
    {"seed_id": "seed-liked-0443", "research_order": 721, "title": "曲 B", "artists": ["人 2"], "album": "盤 2",
     "duration": 180, "liked_added_date": "2026-03-04", "liked_added_display": "3 月 4 日", "date_basis": "liked",
     "position": 443, "relative_unit": 0.4, "research_status": "merged", "assigned_at": "2026-10-01"},
    {"seed_id": "seed-liked-0665", "research_order": 9, "title": "曲 C", "artists": ["人 3"], "album": "盤 3",
     "duration": 240, "liked_added_date": "2026-05-06", "liked_added_display": "5 月 6 日", "date_basis": "liked",
     "position": 665, "relative_unit": 0.6, "research_status": "merged", "assigned_at": "2026-10-02"},
]


def queue_bytes(items) -> bytes:
    # MMR の道具と同じ書き方（`json.dumps(…, ensure_ascii=False, indent=2)`・数-M2）。
    return json.dumps(items, ensure_ascii=False, indent=2).encode("utf-8")


def _mmr(src: pathlib.Path):
    write(src / "data/claims.csv", "id,statement\nm-1,この盤は 1975 年に出た\n")
    write(src / "data/evidence.csv", 'id,claim_id,finding\ne-1,m-1,"一行め\n二行め"\n')
    write(src / "queue.json", queue_bytes(QUEUE))
    write(src / "ledger.json", '{"waves": [], "root_fixes_data": [{"old": "1974", "new": "1975", "why": "盤の裏"}]}')
    write(src / "content/index.yaml", "essays:\n  - slug: e-1\n")
    write(src / "content/essays/e-1.md", "随筆\n")
    shutil.copytree(FIX / "mmr" / "site", src / "site")
    write(src / "site/src/credit-versions/versions.json", '{"rows": []}\n')
    write(src / "lab.yaml", "name: MMR\n")
    write(src / "reports/night.md", "夜の仕事の報告\n")
    write(src / "staging/build-1/x.html", "<p>写し</p>\n")
    return ["data/*.csv", "queue.json", "ledger.json", "content/index.yaml", "content/essays/*.md", "site/src/**",
            "lab.yaml"], [], ["reports/night.md", "staging/build-1/x.html"]


def _kanto(src: pathlib.Path):
    shutil.copytree(FIX / "kanto", src / "kanto_type", ignore=shutil.ignore_patterns(".thth"))
    big = {"school_id": "900", "rounds": [{"round": f"第{i}回", "date": "2027-02-01", "note": "あ" * 40}
                                          for i in range(700)]}
    write(src / "kanto_type/school_records/900.json", json.dumps(big, ensure_ascii=False, indent=1) + "\n")
    write(src / "kanto_type/school_master/_targets.csv", "id\n1\n")
    write(src / "kanto_type/admission_results/_INSTRUCTIONS.md", "手順\n")
    write(src / "results/full/features_book.csv", "school,feature\n1,共学\n")
    write(src / "kanto_dl/past.pdf", b"%PDF-1.4\n%\xe2\xe3\n")
    return ["kanto_type/*/[0-9]*.json", "kanto_type/school_master/_targets.csv", "results/full/*.csv"], [], [
        "kanto_type/admission_results/_INSTRUCTIONS.md", "kanto_type/admission_results/_audit_sample.json",
        "kanto_dl/past.pdf"]


def _hub(src: pathlib.Path):
    write(src / "registry/concepts.csv", "shared_id,name_ja,sometokagaku,nigamilab,kopicha,notes\ntannin,タンニン,x,y,,\n")
    write(src / "registry/README.md", "# 対応表\n")
    write(src / "media.yaml", "schema: hub.media/1\nmedia:\n  - id: kopicha\n    status: live\n")
    write(src / "hub/score/indicators.yaml", "indicators: []\n")
    write(src / "tools/run.sh", "#!/bin/sh\necho hi\n")
    os.chmod(src / "tools/run.sh", 0o755)
    write(src / "empty.txt", b"")
    return ["registry/*", "media.yaml", "tools/run.sh", "empty.txt"], [], ["hub/score/indicators.yaml"]


MEDIA = {"kopicha": _kopicha, "nigami": _nigami, "sometokagaku": _sometokagaku, "jiangshi": _jiangshi, "mmr": _mmr,
         "kanto": _kanto, "media_hub": _hub}


# 見本ごとの範囲の中の枚の数（見本の元の枚の数から、範囲の外と名指しした枚を引いた数。選び方と別に数えた物）。
IN_SCOPE = {"kopicha": 15, "nigami": 7, "sometokagaku": 8, "jiangshi": 6, "mmr": 10, "kanto": 9, "media_hub": 5}
LARGE_KINDS = {"kanto": [{"schema": "thth.kind/1", "kind": "school_record", "place": "kanto_type/school_records/[0-9]*.json",
                          "format": "json", "id": {"name": True}, "max_bytes": 400000}]}


def build(env, name):
    src = env["tmp"] / f"src-{name}"
    include, exclude, outside = MEDIA[name](src)
    plan = write_plan(env["tmp"], name, include, exclude)
    return src, plan, outside


# ================================================================== 試験 2 (a)

@pytest.mark.parametrize("name", sorted(MEDIA))
def test_試験2a_7つ全部で取り込み戻すと数と名前とsha256が全部同じで元には書かない(capsys, env, name):
    src, plan, outside = build(env, name)
    # 期待は器の選び方を使わずに作る: 元の全部の枚（pathlib で歩く）から、見本が範囲の外と名指しした枚を除いた物。
    # 数は見本ごとに決めた数と比べる（範囲の中の欠けも、範囲の外の混入も、ここと台帳の差で見つかる）。
    names = sorted(p.relative_to(src).as_posix() for p in src.rglob("*") if p.is_file() and not p.is_symlink())
    assert set(outside) <= set(names)
    want = sorted(set(names) - set(outside))
    assert len(want) == IN_SCOPE[name]
    expected = [K.read_entry(str(src / rel), rel)[0] for rel in want]
    before = stat_all(src)
    reg = env["tmp"] / "reg"
    decl = []
    if name in LARGE_KINDS:  # 64 KB を超える枚は、種類の宣言で上限を言う（無ければ断る・下の試験）
        for kind in LARGE_KINDS[name]:
            write(env["tmp"] / "decl" / "kinds" / f"{kind['kind']}.json", json.dumps(kind))
        decl = ["--decl", str(env["tmp"] / "decl")]
    rc_, value, err = jrun(capsys, "register", "adopt", str(src), "--to", str(reg), "--plan", str(plan), *decl,
                           "--by", "m")
    assert rc_ == 0, err
    assert stat_all(src) == before                        # 元には 1 バイトも書かない（範囲の外も・時刻も）
    assert value["same"] and value["fp"] == K.catalog_fp(expected) and not value["source_moved"]
    out = env["tmp"] / "out"
    rc_, restored, err = jrun(capsys, "register", "restore", str(reg), "--to", str(out))
    assert rc_ == 0, err
    assert restored["fp"] == value["source_fp"]
    got = K.dir_catalog(str(out))
    assert [e["path"] for e in K.dir_catalog(str(reg))] == want          # 台帳: 欠けも余分も無い
    assert len(got) == len(expected) and [e["path"] for e in got] == want  # 戻した物: 欠けも余分も無い
    assert [e["sha256"] for e in got] == [e["sha256"] for e in expected]
    assert [e["x"] for e in got] == [e["x"] for e in expected]
    for rel in outside:                                    # 範囲の外は入れない（レーンの答え）
        assert not (reg / rel).exists() and not (out / rel).exists()


def test_試験2a_混ざった改行とBOMと最後の改行が無いファイルをバイトのまま戻す(capsys, env):
    src, plan, _ = build(env, "nigami")
    reg, out = env["tmp"] / "reg", env["tmp"] / "out"
    assert run(capsys, "register", "adopt", str(src), "--to", str(reg), "--plan", str(plan), "--by", "m")[0] == 0
    assert run(capsys, "register", "restore", str(reg), "--to", str(out))[0] == 0
    for rel in ("data/topic-hubs.csv", "data/claims.csv", "schema/schema.sql", "data/sensory_measurements.csv"):
        assert (out / rel).read_bytes() == (src / rel).read_bytes(), rel
    assert b"\r\n" in (out / "data/topic-hubs.csv").read_bytes() and b"t-2,B\n" in (out / "data/topic-hubs.csv").read_bytes()


@pytest.mark.skipif(shutil.which("git") is None, reason="git の無い機械")
def test_試験2a_gitの外のファイルも取り込み_gitの物は入れない(capsys, env):
    """MMR は表・queue.json・ledger.json・記事の本文が git の外（`.gitignore` が `/*` で始まる）。器は git を見ずに
    範囲のファイルをそのまま置く。比べる相手は「取り込んだ時点の作業木」（期待する答え (a) の git の外の物）。"""
    src, plan, _ = build(env, "mmr")
    write(src / ".gitignore", "/*\n!/site/\n!/content/\n/content/*\n!/content/index.yaml\n!/lab.yaml\n!/.gitignore\n")
    git = ["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "見本"], check=True)
    tracked = subprocess.run(git + ["ls-files"], check=True, capture_output=True, text=True).stdout.split()
    assert "queue.json" not in tracked and "data/claims.csv" not in tracked
    status = subprocess.run(git + ["status", "--porcelain", "--ignored"], check=True, capture_output=True, text=True).stdout
    reg = env["tmp"] / "reg"
    rc_, value, err = jrun(capsys, "register", "adopt", str(src), "--to", str(reg), "--plan", str(plan), "--by", "m")
    assert rc_ == 0, err
    assert value["same"]
    assert (reg / "queue.json").read_bytes() == (src / "queue.json").read_bytes()
    assert (reg / "data/evidence.csv").read_bytes() == (src / "data/evidence.csv").read_bytes()
    assert not (reg / ".git").exists()
    assert subprocess.run(git + ["status", "--porcelain", "--ignored"], check=True, capture_output=True,
                          text=True).stdout == status


def test_試験2a_64KBを超える枚は宣言が無ければ断り宣言があれば通る(capsys, env):
    src, plan, _ = build(env, "kanto")
    assert (src / "kanto_type/school_records/900.json").stat().st_size > 64 * 1024
    reg = env["tmp"] / "reg"
    # 宣言が無い: 取り込みの時点で断る（黙って切らない・何も書かない）。
    rc_, value, _ = jrun(capsys, "register", "adopt", str(src), "--to", str(reg), "--plan", str(plan), "--by", "m")
    assert rc_ != 0 and value["code"] == "register_adopt_unusable" and value["detail"] == "too_large"
    assert value["files"] == [{"path": "kanto_type/school_records/900.json",
                               "size": (src / "kanto_type/school_records/900.json").stat().st_size,
                               "kind": None, "max_bytes": 64 * 1024}]
    assert not reg.exists() or not any(reg.iterdir())
    # 種類の宣言はあるが上限を書いていない: 種類の既定（64 KB）で断る。
    kind = {"schema": "thth.kind/1", "kind": "school_record", "place": "kanto_type/school_records/[0-9]*.json",
            "format": "json", "id": {"name": True}}
    decl = env["tmp"] / "decl"
    write(decl / "kinds/school_record.json", json.dumps(kind))
    rc_, value, _ = jrun(capsys, "register", "adopt", str(src), "--to", str(reg), "--plan", str(plan),
                         "--decl", str(decl), "--by", "m")
    assert rc_ != 0 and value["detail"] == "too_large" and value["files"][0]["kind"] == "school_record"
    assert not reg.exists() or not any(reg.iterdir())
    # 宣言に上限を書けば通り、バイトのまま入る。
    write(decl / "kinds/school_record.json", json.dumps(dict(kind, max_bytes=400000)))
    rc_, value, err = jrun(capsys, "register", "adopt", str(src), "--to", str(reg), "--plan", str(plan),
                           "--decl", str(decl), "--by", "m")
    assert rc_ == 0, err
    assert value["same"]
    assert sha_of(reg / "kanto_type/school_records/900.json") == sha_of(src / "kanto_type/school_records/900.json")
    rc_, _out, err = run(capsys, "register", "add", "kanto", "--path", str(reg), "--project", "sample", "--kind",
                         "media", "--by", "m")
    assert rc_ == 0, err
    rc_, value, err = jrun(capsys, "register", "check", "kanto")
    assert rc_ == 0, err
    assert value["records"]["school_record"] == 2
    # あとで宣言から上限を外すと、読み手（check）も上限を超えた枚を断る。
    write(reg / ".thth/kinds/school_record.json", json.dumps(kind))
    rc_, value, _ = jrun(capsys, "register", "check", "kanto")
    assert rc_ != 0 and value["code"] == "register_record_invalid"
    assert any(w.startswith("too_large") for p in value["problems"] for w in p["words"])


def test_試験2a_取り込み直しは片道で変わった物と消えた物だけを入れ替え前の版は控えに残る(capsys, env):
    src, plan, _ = build(env, "media_hub")
    reg = env["tmp"] / "reg"
    rc_, first, _ = jrun(capsys, "register", "adopt", str(src), "--to", str(reg), "--plan", str(plan), "--by", "m")
    assert rc_ == 0
    write(src / "registry/concepts.csv", "shared_id,name_ja,sometokagaku,nigamilab,kopicha,notes\ntannin,タンニン,x,y,z,\n")
    os.unlink(src / "registry/README.md")
    write(src / "registry/aliases.csv", "a,b\n")
    rc_, again, err = jrun(capsys, "register", "adopt", str(src), "--to", str(reg), "--plan", str(plan), "--by", "m")
    assert rc_ == 0, err
    assert again["same"] and again["changed"] == 3 and again["removed"] == 1
    assert not (reg / "registry/README.md").exists()
    out = env["tmp"] / "first"
    rc_, value, _ = jrun(capsys, "register", "restore", str(reg), "--to", str(out), "--at", "first")
    assert rc_ == 0 and value["fp"] == first["fp"]
    assert (out / "registry/README.md").read_text(encoding="utf-8") == "# 対応表\n"


# ------------------------------------------------------------------ (a) の柵

def test_柵_取り込みの書き先が元の中か空でなければ断り元には何も書かない(capsys, env):
    src, plan, _ = build(env, "media_hub")
    before = stat_all(src)
    rc_, value, _ = jrun(capsys, "register", "adopt", str(src), "--to", str(src / "reg"), "--plan", str(plan),
                         "--by", "m")
    assert rc_ != 0 and value["code"] == "register_adopt_unusable" and value["detail"] == "inside"
    busy = env["tmp"] / "busy"
    write(busy / "x.txt", "x")
    rc_, value, _ = jrun(capsys, "register", "adopt", str(src), "--to", str(busy), "--plan", str(plan), "--by", "m")
    assert rc_ != 0 and value["detail"] == "not_empty"
    assert stat_all(src) == before and not (src / "reg").exists()


def test_柵_範囲の中のsymlinkと器の置き場は書く前に断る(capsys, env):
    src, plan, _ = build(env, "media_hub")
    os.symlink(src / "media.yaml", src / "registry" / "link.yaml")
    reg = env["tmp"] / "reg"
    rc_, value, _ = jrun(capsys, "register", "adopt", str(src), "--to", str(reg), "--plan", str(plan), "--by", "m")
    assert rc_ != 0 and value["detail"] == "symlink" and value["links"] == ["registry/link.yaml"]
    assert not reg.exists()
    os.unlink(src / "registry" / "link.yaml")
    write(src / ".thth/kinds/x.json", "{}")
    everything = write_plan(env["tmp"], "all", ["**"])
    rc_, value, _ = jrun(capsys, "register", "adopt", str(src), "--to", str(reg), "--plan", str(everything), "--by", "m")
    assert rc_ != 0 and value["detail"] == "thth_in_source" and not reg.exists()


def test_柵_断りの文は前向きな3つがそろう(capsys, env):
    from thth import refusal_catalog as rc
    for code in ("register_adopt_unusable", "register_batch_pending", "register_batch_conflict",
                 "register_kept_missing", "register_record_vanished", "register_write_unsafe"):
        entry = rc.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["fix"]["command"] and entry["better"], code


# ================================================================== 試験 2 (b)

def adopt_fixture(capsys, env, name, *, extra=None):
    """段 1 の見本を元のフォルダとして取り込む（宣言は台帳の側に置く）。"""
    src = env["tmp"] / f"src-{name}"
    shutil.copytree(FIX / name, src)
    if extra:
        extra(src)
    plan = write_plan(env["tmp"], name, ["**"], [".thth/**"])
    reg = env["tmp"] / f"reg-{name}"
    rc_, value, err = jrun(capsys, "register", "adopt", str(src), "--to", str(reg), "--plan", str(plan), "--by", "m",
                           "--decl", str(src / ".thth"))
    assert rc_ == 0, err
    assert value["same"]
    return src, reg


def toCsv(rows: list) -> bytes:
    """こぴちゃの読み書きの部品（`parseCsv`・`toCsv`）と同じく、要る所だけ引用して LF で書く。"""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def parseCsv(path: pathlib.Path) -> list:
    return list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"), newline="")))


def kopicha_k1_merge(reg: pathlib.Path, changes: dict) -> None:
    """束の取り込みの台本（`knowledge-k1-merge.mjs`）の道: 表を丸ごと読み、直す行を直し、丸ごと書き直す。"""
    for rel, fix in changes.items():
        rows = parseCsv(reg / rel)
        header = rows[0]
        for row in rows[1:]:
            fix(dict(zip(header, row)), row, header)
        (reg / rel).write_bytes(toCsv(rows))


def files_sha(root: pathlib.Path) -> dict:
    return {e["path"]: e["sha256"] for e in K.dir_catalog(str(root))}


CTC = "c-term-tea-grade-ctc-res-milktea1-24"


def test_試験2b_CSV_こぴちゃの束の取り込みの道で1行ずつ直すと触っていない原物は1バイトも変わらない(capsys, env):
    """`8ead0fc` の再演: 主張の文の 17.3% を 17.1% に・根拠の抜き書きを直し・来歴の控えの指紋を書き直す。"""
    _src, reg = adopt_fixture(capsys, env, "kopicha")
    for rel in ("data/knowledge/claims.csv", "data/knowledge/evidence.csv", "data/knowledge/article-claims.csv"):
        assert toCsv(parseCsv(reg / rel)) == (reg / rel).read_bytes()  # 読んで書き戻すとバイトで同じ（数-C6）
    v = V.open_vessel(str(reg))
    judged = [J.make(v, {"record": f"claim:{c}"}, "says_so", by="v", at=AT, conclusion="good")
              for c in (CTC, "c-origin-brazil-024", "c-origin-brazil-cerrado-003")]
    judged += [J.make(v, {"record": f"evidence:{e}"}, "reads_right", by="v", at=AT, conclusion="good")
               for e in ("e-milk-01", "e-br-024-a")]
    versions = {r.ref: r.version for r in v.all_records}
    before = files_sha(reg)
    new_statement = "CTC 茶葉の水浸出物は 17.1% とされる"

    def fix_claim(values, row, header):
        if values["id"] == CTC:
            row[header.index("statement_ja")] = new_statement
            row[header.index("object_text_ja")] = "水浸出物 17.1%"

    def fix_evidence(values, row, header):
        if values["id"] == "e-milk-01":
            row[header.index("finding_ja")] = "水浸出物 17.1%"

    def fix_article(values, row, header):
        if values["claim_id"] == CTC:
            row[header.index("statement_hash")] = hashlib.sha1(new_statement.encode()).hexdigest()[:12]

    kopicha_k1_merge(reg, {"data/knowledge/claims.csv": fix_claim, "data/knowledge/evidence.csv": fix_evidence,
                           "data/knowledge/article-claims.csv": fix_article})
    after = files_sha(reg)
    changed = sorted(p for p in before if before[p] != after[p])
    assert changed == ["data/knowledge/article-claims.csv", "data/knowledge/claims.csv", "data/knowledge/evidence.csv"]
    v2 = V.open_vessel(str(reg))
    moved = sorted(r.ref for r in v2.all_records if versions.get(r.ref) != r.version)
    assert len(moved) == 3 and f"claim:{CTC}" in moved and "evidence:e-milk-01" in moved
    assert [J.evaluate(v2, j) for j in judged] == ["stale", "effective", "effective", "stale", "effective"]
    assert V.links(v2)["review"] == []                  # 来歴の控えは新しい文と合う


@pytest.mark.skipif(not V.yamlread.available(), reason="PyYAML が無い機械（pip install 'thth[yaml]'）")
def test_試験2b_YAML_1枚を置き換えるとその枚の判断だけが効かなくなる(capsys, env):
    """`3398d6d28` の再演: 産地の枚 1 枚を書き換える（枚 = 1 つの記録）。"""
    _src, reg = adopt_fixture(capsys, env, "kopicha")
    v = V.open_vessel(str(reg))
    judged = [J.make(v, {"record": ref}, "reads_right", by="v", at=AT, conclusion="good")
              for ref in ("page:brazil", "page:kyoto", "producer_page:kyoto", "tea_page:tieguanyin")]
    before = files_sha(reg)
    path = reg / "src/content/origins/brazil.yaml"
    path.write_bytes(path.read_bytes() + "summary_ja: |\n  シッキムの頁と並べて出す。\n".encode())
    after = files_sha(reg)
    assert sorted(p for p in before if before[p] != after[p]) == ["src/content/origins/brazil.yaml"]
    v2 = V.open_vessel(str(reg))
    assert [J.evaluate(v2, j) for j in judged] == ["stale", "effective", "effective", "effective"]


def kanto_year_replace(path: pathlib.Path, year: int, change) -> None:
    """関東の年度の置き換えの道（`admission_results_yotsuya.py`）: 1 校の 1 つの年度のまとまりを置き換え、1 枚を
    `json.dumps(ensure_ascii=False, indent=1)` と改行で丸ごと書く（ほかの年度の字下げも書き直される）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    data["years"] = [change(json.loads(json.dumps(y))) if y["year"] == year else y for y in data["years"]]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _compact_2025(src: pathlib.Path) -> None:
    """今の実物の 121 枚のように、道具の書き方とバイトで合わない形にする（2025 年度の出典を 1 行に）。"""
    path = src / "admission_results" / "1.json"
    text = path.read_text(encoding="utf-8")
    loose = '"sources": [\n    {\n     "field": "rounds",\n     "url": "https://example.invalid/aoyama/data2025.html",\n' \
            '     "quote": "志願者568",\n     "retrieved": "2026-08-17"\n    }\n   ]'
    assert loose in text
    tight = '"sources": [{"field": "rounds", "url": "https://example.invalid/aoyama/data2025.html", ' \
            '"quote": "志願者568", "retrieved": "2026-08-17"}]'
    path.write_text(text.replace(loose, tight), encoding="utf-8")


def test_試験2b_レーン待ち_関東問い2_JSONの内側_年度の置き換えの道で1か所直すと依る値の判断だけが外れる(capsys, env):
    _src, reg = adopt_fixture(capsys, env, "kanto", extra=_compact_2025)
    v = V.open_vessel(str(reg))
    ref = "admission_result:1"
    rec = v.get(ref)
    male26 = V.find_units(rec, {"group.year": 2026, "gender": "男子"})
    female26 = V.find_units(rec, {"group.year": 2026, "gender": "女子"})
    male25 = V.find_units(rec, {"group.year": 2025, "gender": "男子"})

    def judge(unit, name):
        return J.make(v, {"record": ref, "unit": unit.pointer, "value": name}, "reads_right", by="v", at=AT,
                      conclusion="good")
    judged = [judge(male26, "applicants"), judge(male26, "examinees"), judge(female26, "applicants"),
              judge(male25, "applicants"), judge(male25, "min_score")]
    others = {p: s for p, s in files_sha(reg).items() if p != "admission_results/1.json"}

    def fix(year):
        year["rounds"][0]["applicants"] = 350  # 訂正: 志願者 349 → 350
        return year
    kanto_year_replace(reg / "admission_results" / "1.json", 2026, fix)
    after = files_sha(reg)
    assert {p: s for p, s in after.items() if p != "admission_results/1.json"} == others  # ほかの枚は 1 バイトも
    raw = (reg / "admission_results" / "1.json").read_text(encoding="utf-8")
    assert '"sources": [{"field"' not in raw          # 2025 年度の書き方（バイト）も変わった
    v2 = V.open_vessel(str(reg))
    assert [J.evaluate(v2, j) for j in judged] == ["stale", "effective", "effective", "effective", "effective"]


# ================================================================== 試験 2 (c)

SONG_PAGE = ["seed_id", "research_order", "title", "artists", "album", "duration", "liked_added_date",
             "liked_added_display", "date_basis"]


def _mmr_queue(src: pathlib.Path) -> None:
    write(src / "queue.json", queue_bytes(QUEUE))
    write(src / ".thth/kinds/song.json", json.dumps({
        "schema": "thth.kind/1", "kind": "song", "place": "queue.json", "format": "json", "records": "rows",
        "max_bytes": 2000000, "id": {"field": "seed_id"},
        "questions": [{"name": "song_page", "depends": SONG_PAGE}]}, ensure_ascii=False))


def accept_onepass(path: pathlib.Path, seed: str, **changes) -> None:
    """MMR の進みの書き込み（`accept_onepass.py` の 85〜95 行と同じ書き方）: 全部を読み、1 曲の欄を書き、丸ごと書く。"""
    items = json.loads(path.read_text(encoding="utf-8"))
    for item in items:
        if item["seed_id"] == seed:
            item.update(changes)
    path.write_bytes(queue_bytes(items))


def test_試験2c_レーン待ち_MMR新しく聞く_research_statusを書き換えても曲の頁の判断は効いたままで原物の版は変わる(capsys, env):
    _src, reg = adopt_fixture(capsys, env, "mmr", extra=_mmr_queue)
    v = V.open_vessel(str(reg))
    song = "song:seed-liked-0443"
    judged = [J.make(v, {"record": s}, "song_page", by="v", at=AT, conclusion="good")
              for s in (song, "song:seed-liked-0161", "song:seed-liked-0665")]
    credit = V.find_units(v.get("credit:ap-sample.mastered_by"), {"seed": "seed-liked-0443"})
    judged.append(J.make(v, {"record": "credit:ap-sample.mastered_by", "unit": credit.pointer}, "credit_holds",
                         by="v", at=AT, conclusion="good"))
    versions = {r.ref: r.version for r in v.all_records}
    queue_sha = sha_of(reg / "queue.json")
    credits = {p: s for p, s in files_sha(reg).items() if p.startswith("site/")}
    accept_onepass(reg / "queue.json", "seed-liked-0443", research_status="audited")
    assert sha_of(reg / "queue.json") != queue_sha     # 原物の版は変わる
    v2 = V.open_vessel(str(reg))
    assert v2.get(song).version != versions[song]      # この曲の記録の版も変わる
    assert all(v2.get(r).version == versions[r] for r in ("song:seed-liked-0161", "song:seed-liked-0665"))
    assert {p: s for p, s in files_sha(reg).items() if p.startswith("site/")} == credits
    assert [J.evaluate(v2, j) for j in judged] == ["effective"] * 4   # 効かなくなる判断 0
    accept_onepass(reg / "queue.json", "seed-liked-0443", title="曲 B（再発）")
    v3 = V.open_vessel(str(reg))
    assert [J.evaluate(v3, j) for j in judged] == ["stale", "effective", "effective", "effective"]


# ================================================================== 試験 2 (d)

FOUR = ["data/knowledge/claims.csv", "data/knowledge/evidence.csv", "data/knowledge/leads.csv",
        "data/knowledge/sources.csv"]


def _kopicha_four(src: pathlib.Path) -> None:
    write(src / "data/knowledge/sources.csv", "id,url\ns-1,https://example.invalid/1\n")
    write(src / "data/knowledge/leads.csv", "id,question_ja\nl-1,茶の等級は？\n")


def make_bundle(env, reg: pathlib.Path) -> pathlib.Path:
    """束: 4 枚の新しい中身（主張と根拠は 17.3% → 17.1%・問いと出典は 1 行ずつ足す）。"""
    bundle = env["tmp"] / "bundle"
    for rel in FOUR:
        data = (reg / rel).read_bytes()
        if rel.endswith(("claims.csv", "evidence.csv")):
            data = data.replace("17.3%".encode(), "17.1%".encode())
        elif rel.endswith("leads.csv"):
            data += "l-2,新しい問い\n".encode()
        else:
            data += b"s-9,https://example.invalid/9\n"
        write(bundle / rel, data)
    return bundle


def stopped_merge(env, reg: pathlib.Path, bundle: pathlib.Path, *, after: int, by: str = "a") -> int:
    """別の処理で merge を打ち、`after` 枚を置き換えたところで処理ごと落とす（後始末も走らない）。"""
    code = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from thth import cli\n"
        "from thth.register import keep\n"
        "real = keep._replace\n"
        "count = [0]\n"
        "def stop(a, b):\n"
        f"    if count[0] == {after}:\n"
        "        os._exit(9)\n"
        "    count[0] += 1\n"
        "    real(a, b)\n"
        "keep._replace = stop\n"
        f"cli.main(['register', 'merge', {str(reg)!r}, {str(bundle)!r}, '--by', {by!r}, '--checked-by', 'z'])\n")
    return subprocess.run([sys.executable, "-c", code], env=dict(os.environ), capture_output=True).returncode


def setup_merge(capsys, env):
    _src, reg = adopt_fixture(capsys, env, "kopicha", extra=_kopicha_four)
    bundle = make_bundle(env, reg)
    twin = env["tmp"] / "twin"
    shutil.copytree(reg, twin)
    rc_, done, err = jrun(capsys, "register", "merge", str(twin), str(bundle), "--by", "a", "--checked-by", "z")
    assert rc_ == 0, err
    assert done["files"] == 4
    return reg, bundle, K.catalog_fp(K.view(str(reg))), done["fp"]


def reader_fps(capsys, env, reg: pathlib.Path, tag: str) -> set:
    """読み手（戻す出口・差の今・目録）が見せる目録の指紋。"""
    rc_, value, err = jrun(capsys, "register", "restore", str(reg), "--to", str(env["tmp"] / f"read-{tag}"))
    assert rc_ == 0, err
    rc_, diff, err = jrun(capsys, "register", "diff", str(reg), "first..now")
    assert rc_ == 0, err
    return {value["fp"], diff["to"], K.catalog_fp(K.view(str(reg)))}


def test_試験2d_まとめての入れ替えを途中で止めても読み手には前の版だけが見え打ち直すと後の版になる(capsys, env):
    reg, bundle, before_fp, after_fp = setup_merge(capsys, env)
    assert stopped_merge(env, reg, bundle, after=2) == 9
    on_disk = K.dir_catalog(str(reg))
    mixed_fp = K.catalog_fp(on_disk)
    new = sum(1 for e in on_disk if e["path"] in FOUR and e["sha256"] == sha_of(bundle / e["path"]))
    assert new == 2 and mixed_fp not in (before_fp, after_fp)   # ファイルそのものは 2 枚だけ新しい
    seen = reader_fps(capsys, env, reg, "stopped")
    assert seen == {before_fp}                          # 読み手には 4 枚とも前の版
    rc_, out, _ = run(capsys, "register", "add", "kopicha", "--path", str(reg), "--project", "sample", "--kind",
                      "media", "--by", "m")
    assert rc_ == 0
    rc_, value, _ = jrun(capsys, "register", "check", "kopicha")
    assert rc_ != 0 and value["code"] == "register_batch_pending" and value["what"] == "merge"
    # 別の者が同じ命令を打ち直す → 続きから済ませ、4 枚とも後の版。
    rc_, value, err = jrun(capsys, "register", "merge", str(reg), str(bundle), "--by", "b", "--checked-by", "z")
    assert rc_ == 0, err
    assert value["continued"] and value["applied"] == 2
    assert value["fp"] == after_fp
    seen |= reader_fps(capsys, env, reg, "done")
    assert after_fp in seen and mixed_fp not in seen     # 混ざった指紋は 1 度も見えない
    assert run(capsys, "register", "check", "kopicha")[0] == 0


def test_試験2d_止まった入れ替えは別の者がresumeで戻せて前の版に戻る(capsys, env):
    reg, bundle, before_fp, _after = setup_merge(capsys, env)
    assert stopped_merge(env, reg, bundle, after=3) == 9
    rc_, value, err = jrun(capsys, "register", "resume", str(reg), "--rollback", "--by", "c")
    assert rc_ == 0, err
    assert value["action"] == "rollback" and value["restored"] == 3
    assert value["fp"] == before_fp and K.catalog_fp(K.dir_catalog(str(reg))) == before_fp
    assert K.pending(str(reg)) is None


def test_試験2d_止まった入れ替えの間にほかの者が書いた枚があれば黙って上書きせず断る(capsys, env):
    reg, bundle, before_fp, _after = setup_merge(capsys, env)
    assert stopped_merge(env, reg, bundle, after=1) == 9
    plan = K.pending(str(reg))
    untouched = next(e["path"] for e in plan["entries"]
                     if (reg / e["path"]).exists() and sha_of(reg / e["path"]) == e["old"])
    write(reg / untouched, "id\nほかの者\n")
    rc_, value, _ = jrun(capsys, "register", "resume", str(reg), "--continue", "--by", "b")
    assert rc_ != 0 and value["code"] == "register_batch_conflict" and value["path"] == untouched
    rc_, value, _ = jrun(capsys, "register", "merge", str(reg), str(bundle), "--by", "b", "--checked-by", "z")
    assert rc_ != 0 and value["code"] in ("register_batch_conflict", "register_batch_pending")
    assert (reg / untouched).read_text(encoding="utf-8") == "id\nほかの者\n"


def test_試験2d_止まったまま別の束を入れようとすると断り続けるか戻すかを言う(capsys, env):
    reg, bundle, _before, _after = setup_merge(capsys, env)
    assert stopped_merge(env, reg, bundle, after=1) == 9
    other = env["tmp"] / "other"
    write(other / "data/knowledge/leads.csv", "id,question_ja\nl-3,別の束\n")
    rc_, value, _ = jrun(capsys, "register", "merge", str(reg), str(other), "--by", "b", "--checked-by", "z")
    assert rc_ != 0 and value["code"] == "register_batch_pending" and value["batch"].startswith("b-")


# ================================================================== 変えた跡（diff）

def test_diff_gitの外の台帳でも器の控えから足した変わった消えた記録を出し番号が消えたら赤(capsys, env):
    _src, reg = adopt_fixture(capsys, env, "mmr", extra=_mmr_queue)
    accept_onepass(reg / "queue.json", "seed-liked-0443", title="曲 B（再発）")
    items = json.loads((reg / "queue.json").read_text(encoding="utf-8"))
    items.append(dict(items[0], seed_id="seed-liked-0999", title="曲 D"))
    (reg / "queue.json").write_bytes(queue_bytes(items))
    rc_, kept, err = jrun(capsys, "register", "keep", str(reg), "--by", "m")
    assert rc_ == 0, err
    rc_, value, err = jrun(capsys, "register", "diff", str(reg), f"first..{kept['fp'][:12]}")
    assert rc_ == 0, err
    assert not value["red"]
    assert value["records"]["added"] == ["song:seed-liked-0999"]
    assert [c["record"] for c in value["records"]["changed"]] == ["song:seed-liked-0443"]
    os.unlink(reg / "site/src/credits/ap-other.produced_by.json")
    rc_, out, _ = run(capsys, "register", "diff", str(reg), "last..now")
    assert rc_ != 0 and "credit:ap-other.produced_by" in out
    rc_, value, _ = jrun(capsys, "register", "diff", str(reg), "last..now")
    assert rc_ != 0 and value["code"] == "register_record_vanished"
    assert value["vanished"] == ["credit:ap-other.produced_by"]
    # 前の版は控えから取り出せる（台帳は git でない）。
    out_dir = env["tmp"] / "old"
    assert run(capsys, "register", "restore", str(reg), "--to", str(out_dir), "--at", "first")[0] == 0
    assert (out_dir / "site/src/credits/ap-other.produced_by.json").exists()


def test_diff_控えに無い時点は取り出せないと言う(capsys, env):
    _src, reg = adopt_fixture(capsys, env, "mmr")
    rc_, value, _ = jrun(capsys, "register", "diff", str(reg), "0123456789abcdef..now")
    assert rc_ != 0 and value["code"] == "register_kept_missing"


def test_diff_今の頭つきの台帳は退役した記録を出す(capsys, env):
    src = env["tmp"] / "src-sample"
    shutil.copytree(EXAMPLES / "media-sample", src)
    reg = env["tmp"] / "reg"
    assert run(capsys, "register", "adopt", str(src), "--to", str(reg), "--by", "m")[0] == 0
    path = reg / "事実" / "開講の時期.md"
    path.write_text(path.read_text(encoding="utf-8").replace("status: 試し", "status: 退役"), encoding="utf-8")
    rc_, value, err = jrun(capsys, "register", "diff", str(reg), "first..now")
    assert rc_ == 0, err
    assert value["records"]["retired"] == ["thth_row:fact-term-start"]


# ================================================================== 表の 1 行を書く口

def table_reg(capsys, env, data: bytes) -> pathlib.Path:
    src = env["tmp"] / "src-table"
    write(src / "data/topic-hubs.csv", data)
    write(src / ".thth/kinds/hub.json", json.dumps({"schema": "thth.kind/1", "kind": "hub",
                                                     "place": "data/topic-hubs.csv", "format": "csv",
                                                     "id": {"field": "id"}}))
    plan = write_plan(env["tmp"], "table", ["data/*.csv"])
    reg = env["tmp"] / "reg-table"
    rc_, _v, err = jrun(capsys, "register", "adopt", str(src), "--to", str(reg), "--plan", str(plan), "--by", "m",
                        "--decl", str(src / ".thth"))
    assert rc_ == 0, err
    return reg


MIXED = b"id,title\r\nt-1,A\r\nt-2,\"B,\"\"b\"\"\"\nt-3,C\n\nt-4,D\r\n"


def rows_of(data: bytes) -> list:
    return V.split_csv_rows(data.decode("utf-8"))


def test_row_set_混ざった改行の表で1行だけを書きその行の改行も保つ(capsys, env):
    reg = table_reg(capsys, env, MIXED)
    rc_, value, err = jrun(capsys, "register", "row", "set", str(reg), "hub:t-1", "--set", "title=A,2", "--by", "m")
    assert rc_ == 0, err
    before, after = rows_of(MIXED), rows_of((reg / "data/topic-hubs.csv").read_bytes())
    assert [i for i, (a, b) in enumerate(zip(before, after)) if a != b] == [1]
    assert after[1] == 't-1,"A,2"\r\n'


def test_row_add_最後の行の改行で1行を足し重なる番号と最後の改行が無い表は断る(capsys, env):
    reg = table_reg(capsys, env, MIXED)
    rc_, value, err = jrun(capsys, "register", "row", "add", str(reg), "hub", "--set", "id=t-5", "--set", "title=E",
                           "--by", "m")
    assert rc_ == 0, err
    data = (reg / "data/topic-hubs.csv").read_bytes()
    assert data == MIXED + b"t-5,E\r\n"
    rc_, value, _ = jrun(capsys, "register", "row", "add", str(reg), "hub", "--set", "id=t-5", "--by", "m")
    assert rc_ != 0 and value["detail"] == "id_exists"
    reg2 = env["tmp"] / "second"
    env2 = dict(env, tmp=reg2)
    reg2.mkdir()
    reg_no = table_reg(capsys, env2, b"id,title\nt-1,A")
    rc_, value, _ = jrun(capsys, "register", "row", "add", str(reg_no), "hub", "--set", "id=t-2", "--by", "m")
    assert rc_ != 0 and value["code"] == "register_write_unsafe" and value["detail"] == "no_final_newline"
    assert (reg_no / "data/topic-hubs.csv").read_bytes() == b"id,title\nt-1,A"


def test_柵_表の1行を書く口は触っていない行が変わるなら書かずに断る(capsys, env, monkeypatch):
    reg = table_reg(capsys, env, MIXED)
    monkeypatch.setattr(A, "_join_row", lambda cells, eol, delimiter=",": delimiter.join(cells))  # わざと行の終わりを落とす
    rc_, value, _ = jrun(capsys, "register", "row", "set", str(reg), "hub:t-1", "--set", "title=Z", "--by", "m")
    assert rc_ != 0 and value["code"] == "register_write_unsafe" and value["detail"] == "other_rows"
    assert (reg / "data/topic-hubs.csv").read_bytes() == MIXED
    assert K.pending(str(reg)) is None
