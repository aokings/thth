"""台帳の器 段 6「書く口と束」の試験（指示 `docs/指示_台帳の器_段6と段7_書く口と出口の残り_2026-11-14.md` の段 6）。

重い穴の定め:
- 触っていないレコードのバイトが変わる書き方を、書かずに断る（引用符の付け直しが要る値・欄の数と合わない行・
  混ざった改行の表・タブ区切りの表）。
- 検められていない束が黙って入らない（判定が断りの行・重なる番号・集めた者と検めた者が同じ・検めた者が無い）。
- 版を示さない検収の記録が判断として結ばれない。

段 2 で入っていて今も効いている物は「今ある柵が効いている」の確かめとして残す（試験の名前に「柵」）。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import subprocess
import sys

import pytest

from thth import cli
from thth.register import adopt as A
from thth.register import judgment as J
from thth.register import keep as K
from thth.register import trail as T
from thth.register import vessel as V
from thth.register.errors import RegisterRefused

ROOT = pathlib.Path(__file__).resolve().parents[1]
AT = "2026-11-14T10:00:00+09:00"


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key, sub in (("THTH_ROOT", "root"), ("HOME", "home"), ("XDG_CONFIG_HOME", "config")):
        (tmp_path / sub).mkdir()
        monkeypatch.setenv(key, str(tmp_path / sub))
    return tmp_path


def write(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def jrun(capsys, *argv):
    rc = cli.main([str(a) for a in argv] + ["--json"])
    out, err = capsys.readouterr()
    return rc, (json.loads(out) if out.strip() else None), err


def refused(fn, *args, **kwargs) -> RegisterRefused:
    with pytest.raises(RegisterRefused) as info:
        fn(*args, **kwargs)
    return info.value


def kind_json(name, place, *, delimiter=None, **extra) -> str:
    body = {"schema": "thth.kind/1", "kind": name, "place": place, "format": "csv", "id": {"field": "id"}, **extra}
    if delimiter is not None:
        body["delimiter"] = delimiter
    return json.dumps(body, ensure_ascii=False)


def ledger(env, files: dict, kinds: dict, constitution=None, name="led") -> pathlib.Path:
    led = env / name
    for rel, data in files.items():
        write(led / rel, data)
    for kname, body in kinds.items():
        write(led / ".thth/kinds" / f"{kname}.json", body)
    if constitution is not None:
        write(led / ".thth/constitution.json", json.dumps({"schema": "thth.constitution/1", "words": constitution}))
    return led


def record_shas(data: bytes) -> list:
    """レコードごとの sha256（見出しも 1 つのレコードとして、元の字のまま。改行も含む）。"""
    return [hashlib.sha256(raw.encode("utf-8")).hexdigest() for raw in V.split_csv_rows(data.decode("utf-8"))]


def changed_records(before: bytes, after: bytes) -> tuple:
    a, b = record_shas(before), record_shas(after)
    return [x for x in a if x not in b], [x for x in b if x not in a]


# ================================================================== 表の 1 行（row add・set）

TABLE = "data/t.csv"


def table(env, data: bytes, *, delimiter=None) -> pathlib.Path:
    return ledger(env, {TABLE: data}, {"t": kind_json("t", TABLE, delimiter=delimiter)})


def test_row_add_混ざった改行の表で最後が空の行なら最後のレコードの改行で足す(env):
    # 最後のレコードは LF・そのあとに CRLF の空の行。足す行は最後のレコード（LF）に合わせる。
    data = b"id,title\r\nt-1,A\r\nt-2,B\n\r\n"
    led = table(env, data)
    A.row_add(str(led), "t", {"id": "t-3", "title": "C"}, by="m", at=AT)
    after = (led / TABLE).read_bytes()
    assert after == data + b"t-3,C\n"
    gone, new = changed_records(data, after)
    assert gone == [] and len(new) == 1


def test_柵_row_set_引用符の付け直しが要る値はその欄だけ引用符で包みほかのレコードは1バイトも変えない(env):
    data = b'id,title,note\r\nt-1,A,"x,""y"""\nt-2,B,z\r\n'
    led = table(env, data)
    A.row_set(str(led), "t:t-2", {"title": 'B,"2"\nb'}, by="m", at=AT)
    after = (led / TABLE).read_bytes()
    gone, new = changed_records(data, after)
    assert len(gone) == 1 and len(new) == 1
    assert V.open_vessel(str(led)).get("t:t-2").values["title"] == 'B,"2"\nb'
    assert after.startswith(b'id,title,note\r\nt-1,A,"x,""y"""\n') and after.endswith(b"\r\n")


def test_柵_引用符を付け直さない書き方は書く前に断る(env, monkeypatch):
    data = b"id,title\nt-1,A\nt-2,B\n"
    led = table(env, data)
    monkeypatch.setattr(A, "render_cell", lambda value, delimiter=",": value)   # わざと引用符を付けない
    err = refused(A.row_set, str(led), "t:t-1", {"title": "A,1"}, by="m", at=AT)
    assert err.code == "register_write_unsafe" and err.detail == "other_rows"
    assert (led / TABLE).read_bytes() == data and K.pending(str(led)) is None


def test_柵_欄の数と合わない行は書かずに断る(env):
    data = b"id,title,note\nt-1,A\nt-2,B,b\n"
    led = table(env, data)
    err = refused(A.row_set, str(led), "t:t-1", {"title": "Z"}, by="m", at=AT)
    assert err.code == "register_write_unsafe" and err.detail == "row_width"
    assert (led / TABLE).read_bytes() == data


def test_タブ区切りの表にも1欄直す1行足すが書けほかのレコードは変えない(env):
    data = b"id\ttitle\tnote\r\nt-1\tA,a\tx\nt-2\tB\ty\r\n"
    led = table(env, data, delimiter="\t")
    A.row_set(str(led), "t:t-1", {"title": "A\t2"}, by="m", at=AT)
    A.row_add(str(led), "t", {"id": "t-3", "title": "C,c"}, by="m", at=AT)
    after = (led / TABLE).read_bytes()
    assert after == b'id\ttitle\tnote\r\nt-1\t"A\t2"\tx\nt-2\tB\ty\r\nt-3\tC,c\t\r\n'
    vessel = V.open_vessel(str(led))
    assert vessel.get("t:t-1").values["title"] == "A\t2" and vessel.get("t:t-3").values["title"] == "C,c"


def test_タブ区切りの表でも消す求めが1欄を空にでき触っていないレコードは変えない(env, capsys):
    from thth.register import erase as E
    from thth.register import seal as S
    try:
        S.openssl()
    except RegisterRefused:
        pytest.skip("OpenSSL 3 か 4 が決まった場所に無い")
    S.init_key()
    data = b"id\ttitle\tnote\r\nt-1\tA\t\xe4\xbd\x8f\xe6\x89\x80\nt-2\tB\ty\r\n"
    led = table(env, data, delimiter="\t")
    rc, v, err = jrun(capsys, "register", "erase", "request", led, "t:t-1", "--field", "note", "--by", "m",
                      "--why", "personal")
    assert rc == 0, err
    req = v["request"]
    est = E.scope(str(led), req, ["all"], by="m", at=AT)
    E.scope(str(led), req, ["all"], by="m", at=AT, confirm=est["digest"])
    rc, v, err = jrun(capsys, "register", "erase", "run", led, req, "--by", "m")
    assert rc == 0, err
    after = (led / TABLE).read_bytes()
    assert after == b"id\ttitle\tnote\r\nt-1\tA\t\nt-2\tB\ty\r\n"


# ================================================================== 束（merge）

CLAIMS = "data/claims.csv"
VERIFY = "data/verify.csv"
CHECKLOG = "data/check-log.csv"
WORDS = {"says_so": {"ok": {"conclusion": "good"}, "reject": {"conclusion": "bad"}}}


def claim_kinds() -> dict:
    claim = kind_json("claim", CLAIMS, questions=[{"name": "says_so", "state": "status", "depends": ["text"]}])
    verify = kind_json("verify", VERIFY, pointers=[{"field": "claim_id", "meaning": "about", "to": "claim"}],
                       inspects={"follow": ["claim_id"], "question": "says_so", "by": "verifier",
                                 "content": {"text": "text", "status": "verdict"}})
    log = kind_json("check_log", CHECKLOG, pointers=[{"field": "claim_id", "meaning": "about", "to": "claim"}],
                    inspects={"follow": ["claim_id"], "question": "says_so", "by": "log",
                              "content": {"status": "verdict"}})
    return {"claim": claim, "verify": verify, "check_log": log}


def claim_ledger(env) -> pathlib.Path:
    return ledger(env, {CLAIMS: "id,text,status\nc-1,茶は苦い,ok\nc-2,豆は甘い,ok\n",
                        VERIFY: "id,claim_id,text,verdict\n",
                        CHECKLOG: "id,claim_id,verdict,date\n"}, claim_kinds(), WORDS)


def bundle_of(env, files: dict, name="bundle") -> pathlib.Path:
    b = env / name
    for rel, data in files.items():
        write(b / rel, data)
    return b


def claims_fix() -> dict:
    """c-1 の文を直す束（検めた結果の行も同じ束に入る）。"""
    return {CLAIMS: "id,text,status\nc-1,茶はとても苦い,ok\nc-2,豆は甘い,ok\n"}


def test_merge_検めた者が無い束は入れず二段のunchecked言ったときだけ入れて跡に検めていないと残る(env, capsys):
    led = claim_ledger(env)
    before = (led / CLAIMS).read_bytes()
    b = bundle_of(env, claims_fix())
    rc, v, _ = jrun(capsys, "register", "merge", led, b, "--by", "集める者")
    assert rc != 0 and v["code"] == "register_merge_refused" and v["detail"] == "unchecked"
    rc, v, _ = jrun(capsys, "register", "merge", led, b, "--by", "集める者", "--unchecked")
    assert rc != 0 and v["code"] == "register_merge_refused" and v["detail"] == "unchecked_once"
    assert (led / CLAIMS).read_bytes() == before and K.pending(str(led)) is None
    rc, v, err = jrun(capsys, "register", "merge", led, b, "--by", "集める者", "--unchecked", "--unchecked")
    assert rc == 0, err
    assert v["checked_by"] is None and v["unchecked"] is True
    from thth.register import vessel_cli
    links = vessel_cli.links_of(str(led), "claim:c-1")
    assert links["entered"]["what"] == "merge" and links["entered"]["checked"] is False
    assert links["entered"]["checked_by"] is None


def test_merge_集めた者と検めた者が同じ束は入れない(env, capsys):
    led = claim_ledger(env)
    before = (led / CLAIMS).read_bytes()
    rc, v, _ = jrun(capsys, "register", "merge", led, bundle_of(env, claims_fix()), "--by", "同じ者",
                    "--checked-by", "同じ者")
    assert rc != 0 and v["code"] == "register_merge_refused" and v["detail"] == "same_person"
    assert (led / CLAIMS).read_bytes() == before


def test_merge_判定が断りの行を含む束は入れない(env, capsys):
    led = claim_ledger(env)
    before = (led / CLAIMS).read_bytes()
    files = dict(claims_fix(), **{VERIFY: "id,claim_id,text,verdict\nv-1,c-1,茶はとても苦い,reject\n"})
    rc, v, _ = jrun(capsys, "register", "merge", led, bundle_of(env, files), "--by", "a", "--checked-by", "b")
    assert rc != 0 and v["code"] == "register_merge_refused" and v["detail"] == "verdict_refused"
    assert v["rows"] == ["verify:v-1"]
    assert (led / CLAIMS).read_bytes() == before and K.pending(str(led)) is None


def test_merge_重なる番号を作る束は入れない(env, capsys):
    led = claim_ledger(env)
    before = (led / CLAIMS).read_bytes()
    files = {CLAIMS: "id,text,status\nc-1,茶は苦い,ok\nc-2,豆は甘い,ok\nc-1,茶は渋い,ok\n"}
    rc, v, _ = jrun(capsys, "register", "merge", led, bundle_of(env, files), "--by", "a", "--checked-by", "b")
    assert rc != 0 and v["code"] == "register_merge_refused" and v["detail"] == "id_overlap"
    assert v["records"] == ["claim:c-1"]
    assert (led / CLAIMS).read_bytes() == before


def test_merge_版を示す検収の記録は新しい版への判断として結び版を示さない記録は結ばずに数える(env, capsys):
    led = claim_ledger(env)
    files = dict(claims_fix(), **{
        VERIFY: "id,claim_id,text,verdict\nv-1,c-1,茶はとても苦い,ok\nv-2,c-2,豆は辛い,ok\n",
        CHECKLOG: "id,claim_id,verdict,date\nl-1,c-2,ok,2026-11-14\n"})
    rc, v, err = jrun(capsys, "register", "merge", led, bundle_of(env, files), "--by", "a", "--checked-by", "b")
    assert rc == 0, err
    bound = v["bound"]
    assert [b["record"] for b in bound["judgments"]] == ["claim:c-1"]
    assert [(n["record"], n["state"]) for n in bound["not_bound"]] == [("claim:c-2", "stale")]
    assert [(u["record"], u["table"], u["row"], u["state"]) for u in bound["unversioned"]] == \
        [("claim:c-2", "check_log", "l-1", "cannot_say")]
    vessel = V.open_vessel(str(led))
    kept = J.read(str(led))
    assert len(kept) == 1
    j = kept[0]
    assert j["target"] == {"record": "claim:c-1"} and j["path"] == "inspection" and j["source"] == "verify:v-1"
    assert j["seen"]["version"] == vessel.get("claim:c-1").version and J.evaluate(vessel, j) == "effective"
    assert J.conclusion_of(vessel, j)[0] == "good"
    # 版を示さない記録（c-2 への検めの記録）は、判断として結ばれていない。
    assert not any(x["target"]["record"] == "claim:c-2" for x in kept)
    # 同じ束を打ち直しても、同じ判断を 2 度結ばない。
    rc, v, err = jrun(capsys, "register", "merge", led, bundle_of(env, files, "bundle2"), "--by", "a",
                      "--checked-by", "b")
    assert rc == 0, err
    assert len(J.read(str(led))) == 1


def test_merge_複数の表の束を途中で止めても読み手には前か後のどちらかが見え結びは打ち直しで済む(env, capsys):
    led = claim_ledger(env)
    files = dict(claims_fix(), **{VERIFY: "id,claim_id,text,verdict\nv-1,c-1,茶はとても苦い,ok\n"})
    b = bundle_of(env, files)
    rc, v, err = jrun(capsys, "register", "keep", led, "--by", "m")
    assert rc == 0, err
    before_fp = K.catalog_fp(K.view(str(led)))
    code = (
        "import os, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from thth import cli\n"
        "from thth.register import keep\n"
        "real = keep._replace\n"
        "count = [0]\n"
        "def stop(a, b):\n"
        "    if count[0] == 1:\n"
        "        os._exit(9)\n"
        "    count[0] += 1\n"
        "    real(a, b)\n"
        "keep._replace = stop\n"
        f"cli.main(['register', 'merge', {str(led)!r}, {str(b)!r}, '--by', 'a', '--checked-by', 'b'])\n")
    assert subprocess.run([sys.executable, "-c", code], env=dict(os.environ), capture_output=True).returncode == 9
    assert K.pending(str(led)) is not None
    assert K.catalog_fp(K.view(str(led))) == before_fp            # 読み手には前の版
    assert J.read(str(led)) == []                                 # 結びもまだ無い
    rc, v, err = jrun(capsys, "register", "merge", led, b, "--by", "a", "--checked-by", "b")
    assert rc == 0, err
    assert v["continued"] and K.catalog_fp(K.view(str(led))) == v["fp"] != before_fp
    assert [j["source"] for j in J.read(str(led))] == ["verify:v-1"]
