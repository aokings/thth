#!/usr/bin/env python3
"""台帳の器 段 9「受け入れ」を、8 つのメディアの影で回す台本（各メディアの repo は**読むだけ**。何も書かない）。

    python3 tools/register_stage9_realcheck.py --work /Volumes/NexDev/Developer/thth-work/stage9
        [--developer /Volumes/NexDev/Developer] [--media <名前>] [--only a,b,c,d,e,f] [--json]
        [--replay <こぴちゃの再演の置き場>] [--previous-adopt <段 2 の前の result.json>]

別の者が同じ結果を出す 1 行（8 メディア全部・(a)〜(f)・段 1〜3 の確かめ込み。時間の目安は約 15 分、
2026-11-14 の通しで 14 分 35 秒。関東と MMR が各 4 分強・こぴちゃ 2.5 分・残りは各 1 分強まで）:

    python3 tools/register_stage9_realcheck.py --work /Volumes/NexDev/Developer/thth-work/stage9 --replay /Volumes/NexDev/Developer/thth-work/stage9/replay_kopicha --previous-adopt ~/Documents/THTH/shadow/20261114-055641/result.json

再演の置き場は `git archive <commit>^` と `git show <commit>:<道>` で作る（`register_adopt_realcheck.REPLAY_EXPORT`）。
無ければ (c) の段 2 の再演が「比べられない」になる。

指示 `docs/指示_台帳の器_段9_受け入れ_2026-11-14.md`。メディアの名前は台本に書かない。
`tools/register_decls/*/media.json` を順に読み、札の `stage9`（範囲・読み手・事を起こす種類）と `outside`（器の外）を使う。
影・試しの鍵・THTH の根・一時の置き場は、毎回新しい `<work>/run-<日時>/` の下だけに作る（前の回の物は消さない）。

メディアごとに見る物（「済み」の線 §1）:

- (a) 目録に漏れなし: 札の `stage9.include`（目録の「器に入れる物」。前の台本の範囲）の全部のファイルが、宣言の
  どれかの種類の置き場に入るか、札の `outside` の型に当たる。どちらにも無いファイルが 0。範囲から黙って除く口
  （`stage9.exclude`）は無い（札に書いてあれば「通らない（宣言）」）。器の外に置く物は理由つきの `outside` だけ。
  `outside` のうち `gap: true`（器が読めないので外に置いた物、または `held` の種類に bytes で置いて中を読めない物）
  が 1 つでもあれば「未完了（器の不足: N 件）」（N はファイルの数。「通る」とは書かない。rc は 1 にしない）。
- (b) 元どおり戻せる: 範囲を影に取り込み → 戻す → 数・名前・sha256 が、元のバイトから計った物と全部同じ。
- (c) 段 4 の実物の確かめ（`register_stage4_realcheck.run_one`）が「合う」。段 1〜3 の実物の確かめ 3 本を、この回の
  置き場で 1 回ずつ打ち、前と同じ結果か（段 1 は指示 段 1 §5 の数・段 2 は前の result.json の真偽の全部・段 3 は rc 0）。
  打つ範囲に入らないメディアは「段 1〜3 の範囲の外」と書く。
- (d) 難しい読み手の代表の例（札の `stage9.readers`）: 読み手の読むファイルが種類に入り、その記録に読みの問題 0・
  読み手の読む欄が記録に在る。中を見ていない記録は札に理由が書いてあるときだけ通す。
- (e) 起きうる事 e1〜e7（§2）を影の上で 1 回ずつ。どの事のあとも、元の repo の `git status` の行数と、取り込んだ
  ファイルの sha256 が変わっていないこと。e1・e2・e4 の赤は、理由の語と、外れる項目の記録がその記録だけであることまで
  比べる。e4 は止めたあと新しい材料を別の置き場に実際に書き、新しい材料・古い材料（古い封のまま）の両方で赤を見る。
  e5 の止め方は器の中の関数（`keep._replace`）の差し替えで、処理が落ちたときのまねではない。
  e7 は影のフォルダ全体（`.git/` を除く）を探す。影の台帳（`register/`）に残れば赤。台本が束として渡した写し・
  取り込みの元の写しは器の外の「ほかの写し」（`copies`）、出し口の材料の置き場は `materials` に数え、残っていれば
  消す求めの結果がその場所を「確かめていない」と出し、状態が「消し終わっていない」であることを見る。
- (e) の続き（札の `stage9.drop` が真のメディアだけ）: 出し口 `on_narrow: drop` の試しの出し口 `s9_drop` を足し、
  d1（更新）・d2（期限をまたぐ）・d4（止める）で、外して書いた材料は緑（警告 dropped にその記録だけ）・封のときの
  材料のままは赤・外して書いた材料の残りの行を 1 字変えると赤、を見る。
- (f) こぴちゃ（札の `stage9.page` が真）: 段 7 の確かめ（`register_stage7_realcheck.py`）を、この回の置き場で打つ。
  頁の一致（build）は `--page` のとき（指示 §3 の限り）。

事を起こす記録の選び方（出力の `picked` に書く）: 読者の出し口の種類の並び（札の `stage9.outlet_kinds` か、
出し口の宣言の並び）の中で、表（csv・行）の最初の種類の、材料に載った最初の記録から順に e1・e2・e4 に 1 つずつ。
表の無いメディア（札の `stage9.e1` が `file`）は、その種類の最初の記録の枚を、束の取り込み（`merge`）で入れ替える。

出力は「メディア × (a)〜(f)」の「通る・通らない・未確認（理由）」の表と rc（通らないが 1 つでもあれば 1）。
"""
from __future__ import annotations

import argparse
import collections
import datetime
import fnmatch
import hashlib
import importlib.util
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve()
REPO = HERE.parents[1]
DECLS = HERE.parent / "register_decls"
sys.path.insert(0, str(REPO))
sys.dont_write_bytecode = True

BY = "register_stage9_realcheck"
AT = "2026-11-14T12:00:00+09:00"            # 受け入れの日時
NOW_BEFORE = "2026-11-20T12:00:00+09:00"    # 期限の前
UNTIL = "2026-12-31"                         # e2 の判断の「いつまで」
NOW_AFTER = "2027-01-10T12:00:00+09:00"     # 期限の後
STAGE1_KANTO = {"result_rows": 7460, "ambiguous_groups": 37,
                "ambiguous_units": 74, "ambiguous_same_content": 0, "capacity_scope_undeclared": 2876,
                "field_years_rounds": 1510, "field_years_mixed": 207}   # 指示 段 1 §5
GIT_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0"}
LETTERS = "abcdef"
EVENTS = ["e1", "e2", "e3", "e4", "e5", "e6", "e7"]
DROP_EVENTS = ["d1", "d2", "d4"]                 # on_narrow: drop の出し口で（札の stage9.drop が真のメディアだけ）
CHANGED_WHYS = {"against_changed", "no_inspection_for_new_version"}   # e1・d1: 記録の版が変わったので外れる


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def module(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE.parent / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def verdict(state: str, why: str = "", **extra) -> dict:
    """`state` は pass・fail・unconfirmed。`cause` は通らないときの分け（vessel・decl・script・ledger）。"""
    return dict({"state": state, "why": why}, **extra)


# ------------------------------------------------------------------ 元の repo を見張る（読むだけ）

def git_lines(root: pathlib.Path) -> int | None:
    top = subprocess.run(["/usr/bin/git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True,
                         text=True, env=GIT_ENV)
    if top.returncode != 0:
        return None
    out = subprocess.run(["/usr/bin/git", "--no-optional-locks", "-C", str(root), "status", "--porcelain"],
                         capture_output=True, env=GIT_ENV)
    return len(out.stdout.splitlines()) if out.returncode == 0 else None


class Guard:
    """元の repo の `git status` の行数と、取り込んだファイルの sha256 を、事ごとに比べる。"""

    def __init__(self, root: pathlib.Path, files: list):
        self.root, self.files = root, files
        self.lines = git_lines(root)
        self.shas = self._shas()
        self.log = []

    def _shas(self) -> dict:
        return {rel: sha((self.root / rel).read_bytes()) for rel in self.files}

    def check(self, label: str) -> bool:
        lines, shas = git_lines(self.root), self._shas()
        moved = sorted(p for p in self.files if shas.get(p) != self.shas.get(p))
        ok = lines == self.lines and not moved
        self.log.append({"after": label, "git_lines": lines, "git_lines_before": self.lines,
                         "files": len(self.files), "moved": moved[:5], "ok": ok})
        return ok


# ------------------------------------------------------------------ 範囲と (a)

def plan_of(A, cfg: dict, media: dict | None = None) -> dict:
    """範囲。`media` を渡すと、札の `outside`（器の外）の型も除く（取り込む範囲）。"""
    exclude = []    # 範囲から黙って除く口（stage9.exclude）は無い。器の外は理由つきの outside だけ
    for entry in (media or {}).get("outside", []):
        if isinstance(entry, dict) and not entry.get("held"):   # held: 種類に bytes で置く（取り込む）
            exclude += entry.get("place", [])
    return A.parse_plan({"schema": A.PLAN_SCHEMA, "include": cfg["include"], "exclude": exclude})


def outside_hit(media: dict, rel: str):
    for i, entry in enumerate(media.get("outside", [])):
        if isinstance(entry, dict) and any(fnmatch.fnmatchcase(rel, p) for p in entry.get("place", [])):
            return i
    return None


def check_a(A, V, root: pathlib.Path, media: dict, decl: str) -> dict:
    cfg = media["stage9"]
    kinds = V.load_declarations(str(root), decl)["kinds"]
    catalog = A.source_catalog(str(root), plan_of(A, cfg))
    by_kind, outside, missing = collections.Counter(), collections.Counter(), []
    for e in catalog:
        hit = next((k.name for k in kinds.values() if V._matches(k, e["path"])), None)
        if hit:
            by_kind[hit] += 1
            continue
        o = outside_hit(media, e["path"])
        if o is not None:
            outside[o] += 1
        else:
            missing.append(e["path"])
    entries = media.get("outside", [])
    gaps = [e for e in entries if isinstance(e, dict) and e.get("gap")]
    gap_files = sorted(e["path"] for e in catalog
                       if any(fnmatch.fnmatchcase(e["path"], p) for g in gaps for p in g.get("place", [])))
    bad_shape = [e for e in entries if not isinstance(e, dict) or not e.get("why") or "gap" not in e
                 or (e.get("held") and e["held"] not in kinds)]
    out = {"files": len(catalog), "in_kinds": sum(by_kind.values()), "kinds_used": len(by_kind),
           "outside": {str(i): {"files": n, "why": entries[i]["why"][:80]} for i, n in sorted(outside.items())},
           "missing": missing[:20], "missing_count": len(missing), "gap_entries": [g["why"][:120] for g in gaps],
           "gap_files": gap_files}
    if cfg.get("exclude"):
        out["verdict"] = verdict("fail", "札に stage9.exclude がある（範囲から黙って除く口は無い。器の外は理由つきの outside）",
                                 cause="decl")
    elif missing:
        out["verdict"] = verdict("fail", f"どちらにも無いファイル {len(missing)}", cause="decl")
    elif bad_shape:
        out["verdict"] = verdict("fail", "outside に理由・gap の印の無い物か、held の種類が無い物がある", cause="decl")
    elif gaps:
        out["verdict"] = verdict("incomplete", f"器の不足: {len(gap_files)} 件（" + "・".join(gap_files[:6])
                                 + ("…" if len(gap_files) > 6 else "") + "）", cause="vessel", count=len(gap_files))
    else:
        out["verdict"] = verdict("pass", f"{len(catalog)} 枚が種類 {len(by_kind)} に入る・外 {sum(outside.values())}")
    return out


# ------------------------------------------------------------------ (b)

def check_b(A, K, root: pathlib.Path, media: dict, decl: str, place: pathlib.Path) -> tuple:
    plan = plan_of(A, media["stage9"], media)
    before = A.source_catalog(str(root), plan)
    expect = {e["path"]: sha((root / e["path"]).read_bytes()) for e in before}
    reg = place / "register"
    value = A.adopt(str(root), str(reg), plan=plan, by=BY, at=AT, decl=decl, checked_by="段9の確かめ（読むだけ）")
    restored = A.restore(str(reg), str(place / "restored"))
    got = {e["path"]: e["sha256"] for e in K.dir_catalog(str(place / "restored"))}
    after = A.source_catalog(str(root), plan)
    same = (K.catalog_fp(before) == value["fp"] == restored["fp"]) and got == expect
    out = {"files": len(before), "register_fp": value["fp"][:16], "restored_fp": restored["fp"][:16],
           "missing": sorted(set(expect) - set(got))[:5], "extra": sorted(set(got) - set(expect))[:5],
           "differ": sorted(p for p in set(expect) & set(got) if expect[p] != got[p])[:5],
           "source_unchanged": K.catalog_fp(after) == K.catalog_fp(before)}
    ok = same and out["source_unchanged"]
    out["verdict"] = verdict("pass" if ok else "fail",
                             f"{len(before)} 枚・数と名前と sha256 が同じ" if ok else "往復で違う", cause=None if ok else "vessel")
    return out, reg, sorted(expect)


# ------------------------------------------------------------------ (c)

def stage13(work: pathlib.Path, developer: pathlib.Path, replay: str | None, previous: str | None) -> dict:
    """段 1〜3 の実物の確かめ 3 本を、この回の置き場で打つ（メディアの repo は読むだけの台本）。"""
    env = dict(os.environ, TMPDIR=str(work / "tmp"))
    out = {}
    started = time.monotonic()
    r = subprocess.run([sys.executable, str(HERE.parent / "register_vessel_realcheck.py"), "--developer",
                        str(developer), "--json"], capture_output=True, text=True, env=env, cwd=str(REPO))
    try:
        got = json.loads(r.stdout)
        kanto = got.get("kanto", {})
        bad = [k for k, v in STAGE1_KANTO.items() if kanto.get(k) != v]
        if (kanto.get("records") or {}).get("admission_result") != 265:
            bad.append("records.admission_result")
        out["vessel"] = {"rc": r.returncode, "media": ["kanto", "kopicha", "mmr", "nigami"],
                         "ok": r.returncode == 0 and not bad, "differs": bad, "kanto": {k: kanto.get(k) for k in STAGE1_KANTO}}
    except ValueError:
        out["vessel"] = {"rc": r.returncode, "ok": False, "error": (r.stderr or r.stdout)[-400:],
                         "media": ["kanto", "kopicha", "mmr", "nigami"]}
    (work / "stage1_vessel.json").write_text(r.stdout, encoding="utf-8")
    r = subprocess.run([sys.executable, str(HERE.parent / "register_route_realcheck.py"), "--developer",
                        str(developer), "--shadow", str(work / "stage3")], capture_output=True, text=True, env=env,
                       cwd=str(REPO))
    (work / "stage3_route.out").write_text(r.stdout + r.stderr, encoding="utf-8")
    out["route"] = {"rc": r.returncode, "media": ["kopicha", "mmr"],
                    "ok": r.returncode == 0 and "全部が期待のとおり" in r.stdout}
    argv = [sys.executable, str(HERE.parent / "register_adopt_realcheck.py"), "--developer", str(developer),
            "--shadow", str(work / "stage2")]
    if replay:
        argv += ["--replay", replay]
    r = subprocess.run(argv, capture_output=True, text=True, env=env, cwd=str(REPO))
    (work / "stage2_adopt.out").write_text(r.stdout + r.stderr, encoding="utf-8")
    result = sorted((work / "stage2").glob("*/result.json"))
    entry = {"rc": r.returncode, "media": ["kopicha", "nigami", "sometokagaku", "jiangshi", "mmr", "kanto", "media_hub"]}
    if result and previous and os.path.exists(previous):
        new, old = json.loads(result[-1].read_text()), json.loads(pathlib.Path(previous).read_text())
        bn, bo = _bools(new), _bools(old)
        differs = sorted(k for k in set(bn) | set(bo) if bn.get(k) != bo.get(k))
        falses = sorted(k for k, v in bn.items() if v is False and not k.endswith(("/mixed_differs",)))
        entry.update(ok=r.returncode == 0 and not differs, differs=differs[:20], previous=previous,
                     result=str(result[-1]), bools=len(bn), false_now=falses[:20])
        entry["by_media"] = {m: not any(k.startswith(f"/{m}/") for k in differs) for m in entry["media"]}
    else:
        entry.update(ok=None, why="前の result.json が無い（比べられない）" if result else "result.json が無い")
    out["adopt"] = entry
    out["seconds"] = round(time.monotonic() - started, 1)
    return out


def _bools(d, p="") -> dict:
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(_bools(v, f"{p}/{k}"))
    elif isinstance(d, bool):
        out[p] = d
    return out


def check_c(S4, V, C, folder: pathlib.Path, media: dict, developer: pathlib.Path, work: pathlib.Path,
            s13: dict | None) -> dict:
    one = S4.run_one(V, C, folder, media, developer, work)
    out = {"stage4": {"ok": one.get("ok"), "mismatch": one.get("mismatch", []),
                      "exception": one.get("exception") or one.get("error")}}
    fails, unconfirmed = [], []
    if not one.get("ok"):
        fails.append(f"段 4 が合わない {one.get('mismatch') or one.get('exception') or one.get('error')}")
    covered = []
    for name, entry in (s13 or {}).items():
        if not isinstance(entry, dict) or folder.name not in entry.get("media", []):
            continue
        ok = entry["ok"]
        if name == "adopt" and entry.get("by_media") is not None:
            ok = entry["rc"] == 0 and entry["by_media"].get(folder.name, False)
        covered.append(f"段{ {'vessel': 1, 'adopt': 2, 'route': 3}[name] } {'同じ' if ok else '違う' if ok is False else '比べられない'}")
        if ok is False:
            fails.append(f"{name} が前と違う {entry.get('differs') or entry.get('error') or entry.get('rc')}")
        elif ok is None:
            unconfirmed.append(f"{name}: {entry.get('why')}")
    out["stage1to3"] = covered or (["段 1〜3 の範囲の外（この台本の打つ範囲に入らない）"] if s13 is not None else ["打っていない"])
    if fails:
        out["verdict"] = verdict("fail", "・".join(fails)[:300], cause="vessel")
    elif unconfirmed or s13 is None:
        out["verdict"] = verdict("unconfirmed", "段 4 は合う。" + ("段 1〜3 は打っていない" if s13 is None
                                                              else "・".join(unconfirmed)))
    else:
        out["verdict"] = verdict("pass", "段 4 が合う・" + "・".join(covered))
    return out


# ------------------------------------------------------------------ (d)

def check_d(V, vessel, media: dict) -> dict:
    readers = media["stage9"].get("readers", [])
    out = {"readers": []}
    fails = []
    for r in readers:
        files = [p for p in {rec.path for rec in vessel.all_records}
                 if any(fnmatch.fnmatchcase(p, g) for g in r.get("files", []))]
        recs = [rec for rec in vessel.all_records if rec.path in files]
        probs = [rec for rec in recs if rec.problems]
        blind = [rec for rec in recs if not rec.inspected and not rec.problems]
        missing_fields = []
        for kind, fields in (r.get("fields") or {}).items():
            have = set()
            for rec in recs:
                if rec.kind == kind and rec.values:
                    have |= set(rec.values)
            for f in fields:
                if f not in have:
                    missing_fields.append(f"{kind}.{f}")
        row = {"example": r["example"], "files": len(files), "records": len(recs), "problems": len(probs),
               "problem_examples": [f"{p.ref}: {','.join(p.problems)}" for p in probs[:3]],
               "not_inspected": len(blind), "missing_fields": missing_fields}
        if not r.get("files"):
            row["state"] = "skip"
            row["why"] = r.get("why", "")
        else:
            bad = []
            if not files:
                bad.append("読むファイルが台帳に無い")
            if probs:
                bad.append(f"読みの問題 {len(probs)}")
            if missing_fields:
                bad.append(f"欄が無い {missing_fields[:5]}")
            if blind and not r.get("uninspected_why"):
                bad.append(f"中を見ていない {len(blind)}（理由が札に無い）")
            row["state"] = "fail" if bad else "pass"
            row["why"] = "・".join(bad) or (f"中を見ていない {len(blind)}: {r['uninspected_why']}" if blind else "")
            if bad:
                fails.append(f"{r['example'][:30]}: {row['why']}")
        out["readers"].append(row)
    if not readers:
        out["verdict"] = verdict("unconfirmed", "札に読み手の例が無い")
    elif fails:
        out["verdict"] = verdict("fail", "・".join(fails)[:300], cause="decl")
    else:
        out["verdict"] = verdict("pass", f"{len(readers)} 例が読める（"
                                 + "・".join(f"{x['records']}" for x in out["readers"] if x["state"] == "pass") + " 記録）")
    return out


# ------------------------------------------------------------------ (e)

class Cannot(Exception):
    """メディアの形のために起こせない事（「通る」にしない）。"""


class Events:
    def __init__(self, mods, root: pathlib.Path, reg: pathlib.Path, place: pathlib.Path, media: dict,
                 guard: Guard, folder: pathlib.Path):
        self.A, self.K, self.V, self.J, self.R, self.H, self.E = mods
        self.root, self.reg, self.place, self.media, self.guard, self.folder = root, reg, place, media, guard, folder
        self.cfg = media["stage9"]
        self.out = {"picked": {}, "events": {}}

    # -- 支度
    def vessel(self):
        return self.V.open_vessel(str(self.reg))

    def health(self, as_of=NOW_BEFORE, common=None):
        return self.H.health(str(self.reg), as_of=as_of, common_root=str(common) if common else None)

    @staticmethod
    def count(h, group, item):
        return h["groups"][group][item]["count"]

    def to(self, outlet):
        return self.place / "out" / outlet

    def accept(self, outlet, as_of=AT, common=None):
        kw = {"common_root": str(common)} if common else {}
        plan = self.R.accept_plan(str(self.reg), outlet, str(self.to(outlet)), as_of, **kw)
        return self.R.accept(str(self.reg), outlet, str(self.to(outlet)), as_of, confirm=plan["digest"], at=AT, **kw)

    def verify(self, outlet, now=NOW_BEFORE, common=None, material=None):
        kw = {"common_root": str(common)} if common else {}
        return self.R.verify(str(self.reg), outlet, str(material or self.to(outlet)), now, no_previous=True, **kw)

    @staticmethod
    def items_of(v, kind, word):
        """確かめの赤（problems）か警告（warnings）の、語 `word` の項目を (記録, 理由) で。"""
        return [(i["item"].rsplit("=", 1)[0].split("#")[0], i.get("why"))
                for p in v[kind] if p["word"] == word for i in p.get("items", [])]

    def red_is(self, v, ref, whys, also=()) -> bool:
        """赤で、語が narrowed_after_seal（と `also`）だけ・外れた項目の記録がその記録だけ・理由が期待の語。"""
        words = [p["word"] for p in v["problems"]]
        items = self.items_of(v, "problems", "narrowed_after_seal")
        return (not v["ok"]) and "narrowed_after_seal" in words and set(words) <= {"narrowed_after_seal", *also} \
            and {r for r, _ in items} == {ref} \
            and {w for _, w in items} <= set(whys)

    @staticmethod
    def red_detail(v) -> dict:
        return {"words": [p["word"] for p in v["problems"]],
                "items": [i for p in v["problems"] for i in p.get("items", [])][:5]}

    def setup(self):
        """出し口の宣言を影の `.thth/` に置き（札の宣言のフォルダに無ければ試しの読者の出し口を作る）、基準で受ける。"""
        v = self.vessel()
        given = self.folder / "outlets.json"
        if given.exists() and not self.cfg.get("e_test_outlet"):
            data = json.loads(given.read_text(encoding="utf-8"))
            self.out["outlets_from"] = str(given.relative_to(REPO))
        else:
            kinds = self.cfg.get("outlet_kinds") or self.pick_kinds(v)
            data = {"schema": "thth.outlets/1", "note": "段 9 の試しの読者の出し口（影だけ）",
                    "outlets": {"s9_reader": {"audience": "reader", "kinds": kinds,
                                              "fact": [{"question": "may_publish@s9_reader", "conclusion": "good"}],
                                              "note": "表の種類を 1〜2 つ・基準で受け入れ"}}}
            self.out["outlets_from"] = "台本が作った試しの読者の出し口 s9_reader（種類: " + "・".join(kinds) + "）" + (
                f"。札の e_test_outlet: {self.cfg['e_test_outlet']}" if self.cfg.get("e_test_outlet") else "")
        data["outlets"]["s9_common"] = {"audience": "reader", "common": "source", "note": "e3 の見本の共通の台帳"}
        (self.reg / ".thth" / "outlets.json").write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                                          encoding="utf-8")
        v = self.vessel()
        self.outlets = self.R.load_outlets(str(self.reg), v)
        readers = [o for o in self.outlets.values() if o.audience == "reader" and o.kinds]
        self.main = readers[0]
        for o in readers:
            self.R.baseline(str(self.reg), o.name, by=BY, at=AT)
        for o in self.outlets.values():
            if o.kinds:
                self.accept(o.name)
        bad = {o.name: [p["word"] for p in self.verify(o.name)["problems"]] for o in self.outlets.values() if o.kinds}
        self.out["setup"] = {"main_outlet": self.main.name, "outlets": sorted(self.outlets),
                             "verify_red_at_start": {k: w for k, w in bad.items() if w}}
        return not any(bad.values())

    def pick_kinds(self, v) -> list:
        counts = collections.Counter(r.kind for r in v.all_records if r.id and not r.problems)
        tables = [k for k in v.kinds.values() if k.format == "csv" and k.records == "rows" and "field" in k.id_rule
                  and counts[k.name] >= 3]
        tables.sort(key=lambda k: (counts[k.name], k.name))
        self.out["picked"]["outlet_kinds_rule"] = "id の欄を持つ表（csv・行）のうち、記録 3 つ以上で数の少ない順に 2 つ"
        return [k.name for k in tables[:2]]

    def questions(self, outlet):
        return [(q, c) for q, c in outlet.requires + outlet.fact]

    def material_refs(self, outlet) -> list:
        seal = self.R.read_seal(str(self.reg), outlet.name)
        return [i.rsplit("=", 1)[0] for i in seal["body"]["items"] if "#" not in i]

    def pick_records(self):
        v = self.vessel()
        on = set(self.material_refs(self.main))
        mode = self.cfg.get("e1", "row")
        order = self.cfg.get("outlet_kinds") or self.main.kinds
        kind = None
        for name in order:
            k = v.kinds[name]
            if mode == "row" and k.format == "csv" and k.records == "rows":
                kind = k
                break
            if mode == "file" and k.records == "file":
                kind = k
                break
        if kind is None:
            raise RuntimeError(f"事を起こす種類が無い（{mode}）")
        recs = [r for r in v.all_records if r.kind == kind.name and r.id and not r.problems and r.ref in on
                and not v.is_duplicated(r.ref)]
        if len(recs) < 3:
            raise RuntimeError(f"{kind.name} の材料に載った記録が 3 つ無い")
        self.kind = kind
        self.recs = recs
        self.out["picked"].update(kind=kind.name, mode=mode, e1=recs[0].ref, e2=recs[1].ref, e4=recs[2].ref,
                                  rule=f"出し口 {self.main.name} の種類の並びで最初の"
                                       + ("表（csv・行）" if mode == "row" else "1 枚 1 記録の種類")
                                       + "の、材料に載った記録を先頭から 3 つ")

    def change_field(self, rec, kind=None):
        kind = kind or self.kind
        ids = {kind.id_rule.get("field")} | set(kind.id_rule.get("key", []))
        depends = []
        for q, _c in self.questions(self.main):
            question = self.V.question_of(kind, q)
            if question is not None and question.depends:
                depends += list(question.depends)
        for f in depends + list(rec.values or {}):
            if f not in ids and f in (rec.values or {}) and isinstance(rec.values[f], str):
                return f
        raise RuntimeError("直せる欄が無い")

    def judged_states(self):
        v = self.vessel()
        return {j["id"]: (j["target"].get("record"), self.J.evaluate(v, j)) for j in self.J.read(str(self.reg))
                if j.get("question") != self.R.TAKE and not j.get("question", "").startswith("stop")}

    def guard_ok(self, label) -> bool:
        return self.guard.check(label)

    # -- 事
    def e1(self):
        rec = self.recs[0]
        pre = self.K.save_snapshot(str(self.reg), self.K.view(str(self.reg)), what="keep", by=BY, at=AT)["snapshot"]
        self.pre_e1 = pre
        self.pre_catalog = {e["path"]: e["sha256"] for e in self.K.view(str(self.reg))}
        h0 = self.count(self.health(), "judgment", "stale")
        s0 = self.judged_states()
        how = self.modify(rec, "e1")
        diff = self.A.diff(str(self.reg), f"{pre[:16]}..now")
        changed = [c["record"] for c in diff["records"]["changed"]]
        s1 = self.judged_states()
        newly = sorted({s1[i][0] for i in s1 if i in s0 and s0[i][1] == "effective" and s1[i][1] != "effective"})
        h1 = self.count(self.health(), "judgment", "stale")
        red = self.verify(self.main.name)
        ex = self.R.export(str(self.reg), self.main.name, str(self.to(self.main.name)), NOW_BEFORE, check=True)
        leaving = ex["changes"]["leaving"]
        narrow = self.main.on_narrow
        checks = {"差が 1 レコード": changed == [rec.ref] and not diff["records"]["added"] and not diff["records"]["vanished"],
                  "古くなった判断はその記録だけ": newly == [rec.ref],
                  "健康の古くなった判断が 1 増える": h1 == h0 + 1,
                  "受け入れ直すまで赤（語 narrowed_after_seal・外れた項目はその記録だけ・理由は版が変わった）":
                      narrow == "stop" and self.red_is(red, rec.ref, CHANGED_WHYS),
                  "外れる物にその記録": any(x.startswith(rec.ref + "=") or x.startswith(rec.ref + "#") for x in leaving),
                  "元の repo は同じ": self.guard_ok("e1")}
        for q, c in self.questions(self.main):
            self.R.judge(str(self.reg), rec.ref, q, conclusion=c, by=BY, at=AT, reason_text="段9 e1 の受け入れ直し")
        self.accept(self.main.name)
        checks["受け入れ直すと緑"] = self.verify(self.main.name)["ok"]
        return checks, {"how": how, "diff_changed": changed[:5], "newly_stale": newly[:5], "stale": [h0, h1],
                        "red": self.red_detail(red), "leaving": leaving[:5], "on_narrow": narrow}

    def modify(self, rec, tag) -> str:
        if self.cfg.get("e1", "row") == "row":
            field = self.change_field(rec, self.vessel().kinds[rec.kind])
            self.A.row_set(str(self.reg), rec.ref, {field: rec.values[field] + f"（段9の確かめ {tag}）"}, by=BY, at=AT)
            return f"row set {rec.ref} の {field}"
        bundle = self.place / f"bundle-{tag}"
        data = (self.reg / rec.path).read_bytes() + f"\n<!-- 段9の確かめ {tag} -->\n".encode()
        (bundle / rec.path).parent.mkdir(parents=True, exist_ok=True)
        (bundle / rec.path).write_bytes(data)
        self.A.merge(str(self.reg), str(bundle), by=BY, at=AT, unchecked=True)
        return f"merge で {rec.path} を入れ替え"

    def e2(self):
        rec = self.recs[1]
        for q, c in self.questions(self.main):
            self.R.judge(str(self.reg), rec.ref, q, conclusion=c, by=BY, at=AT, until=UNTIL,
                         reason_text="段9 e2 の期限つき")
        self.accept(self.main.name)
        before, after = self.verify(self.main.name, NOW_BEFORE), self.verify(self.main.name, NOW_AFTER)
        hb, ha = self.health(NOW_BEFORE), self.health(NOW_AFTER)
        pb = self.count(hb, "deadline", "passed_in_last_generation")
        pa = self.count(ha, "deadline", "passed_in_last_generation")
        ex = [e.get("record") for e in ha["groups"]["deadline"]["passed_in_last_generation"]["examples"]]
        checks = {"期限の前は緑": before["ok"],
                  "期限の後は赤（語 narrowed_after_seal・外れた項目はその記録だけ・理由 expired）":
                      self.red_is(after, rec.ref, {"expired"}),
                  "健康の「過ぎたのに最後の世代に載っている」が 1": pa == pb + 1 and rec.ref in ex,
                  "元の repo は同じ": self.guard_ok("e2")}
        return checks, {"record": rec.ref, "until": UNTIL, "after": self.red_detail(after),
                        "passed_in_last_generation": [pb, pa]}

    def e3(self):
        common = self.place / "common"
        (common / ".thth" / "kinds").mkdir(parents=True)
        (common / "sources").mkdir()
        (common / ".thth/kinds/source.json").write_text(json.dumps(
            {"schema": "thth.kind/1", "kind": "source", "place": "sources/*.json", "format": "json",
             "records": "file", "id": {"name": True}, "note": "段 9 の見本の共通の台帳（出典 1 つ）"},
            ensure_ascii=False), encoding="utf-8")
        src = common / "sources" / "s9-sample.json"
        src.write_text(json.dumps({"title": "段9の見本の出典", "year": 2026}, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        ref = "source:s9-sample"
        self.R.take(str(self.reg), str(common), [ref], by=BY, at=AT)
        self.accept("s9_common", common=common)
        first = sorted(p for p in (self.to("s9_common")).rglob("*") if p.is_file() and not p.name.startswith("thth-seal"))
        first_bytes = {str(p): p.read_bytes() for p in first}
        t0 = self.count(self.health(common=common), "common", "take_pending")
        src.write_text(json.dumps({"title": "段9の見本の出典（共通を直した）", "year": 2026}, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        t1 = self.count(self.health(common=common), "common", "take_pending")
        ex = self.R.export(str(self.reg), "s9_common", str(self.to("s9_common")), NOW_BEFORE,
                           common_root=str(common), check=True)
        still = all(pathlib.Path(p).read_bytes() == b for p, b in first_bytes.items())
        self.R.take(str(self.reg), str(common), [], by=BY, at=AT)
        t2 = self.count(self.health(common=common), "common", "take_pending")
        self.accept("s9_common", common=common)
        moved = any(pathlib.Path(p).read_bytes() != b for p, b in first_bytes.items() if pathlib.Path(p).exists())
        checks = {"採り直すまで材料は前の版": ex["same_as_seal"] and still and bool(first_bytes),
                  "健康の採り入れ待ちが 1 → 0": t1 == t0 + 1 and t2 == t0,
                  "採り直して受け入れると材料が変わる": moved,
                  "元の repo は同じ": self.guard_ok("e3")}
        return checks, {"take_pending": [t0, t1, t2], "material_files": len(first_bytes)}

    def e4(self):
        rec = self.recs[2]
        name = self.main.name
        old_dir = self.place / "old-e4" / name          # 止める前の材料（古い封の材料）を別の置き場に写す
        shutil.copytree(self.to(name), old_dir)
        self.R.stop(str(self.reg), rec.ref, by=BY, at=AT)
        new_dir = self.place / "new-e4" / name          # 止めたあとの新しい材料を実際に書く（封はし直さない）
        ex = self.R.export(str(self.reg), name, str(new_dir), NOW_BEFORE)
        gone = any(x.startswith(rec.ref + "=") for x in ex["changes"]["leaving"])
        why = sorted({e["why"] for e in ex["excluded"] if e["record"] == rec.ref})
        new_files = {p.relative_to(new_dir): p.read_bytes() for p in new_dir.rglob("*") if p.is_file()}
        old_files = {p.relative_to(old_dir): p.read_bytes() for p in old_dir.rglob("*") if p.is_file()}
        red_new = self.verify(name, material=new_dir)
        red_old = self.verify(name, material=old_dir)  # 古い封（封はし直していない）と古い材料に戻す
        checks = {"受け入れを待たずに次の材料から外れる（理由 stopped）": gone and why == ["stopped"],
                  "新しい材料を実際に書いた（古い材料と違う）": bool(new_files) and new_files != old_files,
                  "新しい材料で確かめると赤（出し口は stop。語 narrowed_after_seal と material_mismatch・外れた項目は"
                  "その記録だけ・理由 stopped）": self.red_is(red_new, rec.ref, {"stopped"}, also={"material_mismatch"}),
                  "古い封と古い材料に戻して確かめると赤（外れた項目はその記録だけ・理由 stopped）":
                      self.red_is(red_old, rec.ref, {"stopped"}),
                  "元の repo は同じ": self.guard_ok("e4")}
        return checks, {"record": rec.ref, "why": why, "new_material": str(new_dir.relative_to(self.place)),
                        "red_new": self.red_detail(red_new), "red_old": self.red_detail(red_old)}

    def bundle_files(self) -> list:
        if self.cfg.get("bundle"):
            return list(self.cfg["bundle"])
        v = self.vessel()
        files = []
        for name in (self.cfg.get("outlet_kinds") or self.main.kinds):
            k = v.kinds[name]
            if k.format not in ("csv", "md", "md_yaml"):
                continue
            for p in sorted({r.path for r in v.all_records if r.kind == name}):
                if p not in files:
                    files.append(p)
                if len(files) >= 2:
                    return files
        return files

    def e5(self):
        files = self.bundle_files()
        if len(files) < 2:
            raise Cannot(f"中を変えて足せる枚（csv・md）が 2 つ無い {files}")
        desired = {}
        for rel in files:
            data = (self.reg / rel).read_bytes()
            if rel.endswith(".csv"):
                header = data.split(b"\n", 1)[0]
                eol = b"\r\n" if header.endswith(b"\r") else b"\n"
                desired[rel] = data + b"x-s9-bundle" + b"," * header.count(b",") + eol
            else:
                desired[rel] = data + "\n<!-- 段9の確かめ e5 -->\n".encode()
        before = self.K.catalog_fp(self.K.view(str(self.reg)))
        real, count = self.K._replace, [0]

        class Stop(Exception):
            pass

        def stop(a, b):
            if count[0] == 1:
                raise Stop()
            count[0] += 1
            real(a, b)
        self.K._replace = stop
        try:
            self.K.run(str(self.reg), desired, what="merge", by=BY, at=AT)
            stopped = False
        except Stop:
            stopped = True
        finally:
            self.K._replace = real
        on_disk = self.K.catalog_fp(self.K.dir_catalog(str(self.reg)))
        seen = self.K.catalog_fp(self.K.view(str(self.reg)))
        try:  # 段取りが残る間、器の読み手は断る（11/14 の検収の直し）か、前の姿を見せる
            self.vessel()
            reader_mid = "前の姿を読んだ"
        except Exception as exc:  # noqa: BLE001
            reader_mid = f"断った {getattr(exc, 'code', type(exc).__name__)}"
        result = self.K.run(str(self.reg), desired, what="merge", by=BY, at=AT)
        after = self.K.catalog_fp(self.K.view(str(self.reg)))
        checks = {"1 枚目のあとで止まった": stopped and count[0] == 1,
                  "止まっている間、読み手には前の姿（器の読み手は断るか前の姿）": seen == before and on_disk not in (before, after)
                  and (reader_mid.startswith("断った register_batch_pending") or reader_mid == "前の姿を読んだ"),
                  "続けると後の姿（半分ずつ混ざらない）": bool(result.get("continued"))
                  and all((self.reg / rel).read_bytes() == desired[rel] for rel in files) and after != before,
                  "元の repo は同じ": self.guard_ok("e5")}
        return checks, {"files": files, "before": before[:16], "on_disk_while_stopped": on_disk[:16],
                        "after": after[:16], "vessel_reader_while_stopped": reader_mid}

    def e6(self):
        into = self.place / "restored-e6"
        self.A.restore(str(self.reg), str(into), at_snapshot=self.pre_e1)
        got = {e["path"]: e["sha256"] for e in self.K.dir_catalog(str(into))}
        source = {rel: self.guard.shas[rel] for rel in self.guard.files}
        checks = {"e1 の前の姿と数・名前・sha256 が同じ": got == self.pre_catalog,
                  "元の repo の取り込んだ姿とも同じ": got == source,
                  "元の repo は同じ": self.guard_ok("e6")}
        return checks, {"snapshot": self.pre_e1[:16], "files": len(got),
                        "differ": sorted(p for p in set(got) | set(self.pre_catalog)
                                         if got.get(p) != self.pre_catalog.get(p))[:5]}

    def e7(self):
        value = "段9の試しの値-" + secrets.token_hex(6)
        v = self.vessel()
        mode = self.cfg.get("e1", "row")
        if mode == "row":
            kind = next((v.kinds[n] for n in (self.cfg.get("outlet_kinds") or self.main.kinds)
                         if v.kinds[n].format == "csv" and v.kinds[n].records == "rows" and "field" in v.kinds[n].id_rule
                         and (self.reg / next(r.path for r in v.all_records if r.kind == n)).read_bytes().endswith(b"\n")),
                        None)
            if kind is None:
                raise Cannot("試しの行を足せる表（id の欄を持ち、改行で終わる csv）が出し口に無い")
            sample = next(r for r in v.all_records if r.kind == kind.name and r.values)
            idf = kind.id_rule["field"]
            text = next(f for f in sample.values if f != idf)
            rid = "s9-erase-" + secrets.token_hex(3)
            self.A.row_add(str(self.reg), kind.name, {idf: rid, text: value}, by=BY, at=AT, path=sample.path)
            ref = f"{kind.name}:{rid}"
        else:
            rec = self.recs[0]
            stem = "s9-erase-" + secrets.token_hex(3)
            rel = str(pathlib.PurePosixPath(rec.path).with_name(stem + pathlib.PurePosixPath(rec.path).suffix))
            bundle = self.place / "bundle-e7"
            (bundle / rel).parent.mkdir(parents=True, exist_ok=True)
            (bundle / rel).write_bytes((self.reg / rec.path).read_bytes() + f"\n{value}\n".encode())
            self.A.merge(str(self.reg), str(bundle), by=BY, at=AT, unchecked=True)
            ref = f"{rec.kind}:{stem}"
        needle = value.encode()
        before = self.scan(needle)
        self.e7_value = needle
        req = self.E.request(str(self.reg), ref, by=BY, at=AT, why="mistake")["request"]
        first = self.E.scope(str(self.reg), req, ["all"], by=BY, at=AT)
        self.E.scope(str(self.reg), req, ["all"], by=BY, at=AT, confirm=first.get("digest"))
        result = self.E.run(str(self.reg), req, by=BY, at=AT)
        after = self.scan(needle)
        status = self.E.status(str(self.reg), req)
        not_checked = sorted(p for p, x in status["places"].items() if x["state"] == "not_checked")
        inside = [h for h in after if self.where(h) == "register"]
        outer = [h for h in after if self.where(h) != "register"]
        outer_places = sorted({self.where(h) for h in outer})
        checks = {"消す前は影の台帳に試しの値がある": any(self.where(h) == "register" for h in before),
                  "影の台帳（register/・.git を除く）に試しの値が無い": not inside,
                  "器の外の写しが残る場所の種類を、消す求めの結果が「確かめていない」と出す":
                      all(p in not_checked for p in outer_places),
                  "「確かめていない」場所が残り「消し終わっていない」": bool(not_checked) and status["state"] == "unfinished",
                  "元の repo は 1 バイトも変わらない": self.guard_ok("e7")}
        return checks, {"record": ref, "request": req, "found_before": len(before), "found_after": after[:5],
                        "left_in_register": inside[:5], "left_outside": len(outer), "left_outside_places": outer_places,
                        "left_outside_files": outer[:5],
                        "not_checked": not_checked, "state": status["state"],
                        "erased": sorted(p for p, x in status["places"].items() if x["state"] == "erased")}

    def scan(self, needle: bytes) -> list:
        """影のフォルダ全体（`<work>/<メディア>/`。`.git/` を除く）で、試しの値を持つファイルの道。"""
        hits = []
        for directory, dirs, files in os.walk(self.place):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in files:
                p = pathlib.Path(directory) / name
                if needle in p.read_bytes():
                    hits.append(str(p.relative_to(self.place)))
        return sorted(hits)

    @staticmethod
    def where(rel: str) -> str:
        """影の中の場所の種類: 台帳（register/）・出し口の材料（materials）・共通の台帳（common）・ほかの写し（copies:
        台本が束として渡した写し・取り込みの元の写し・戻した写しなど。器の外）。"""
        head = rel.split("/", 1)[0]
        if head == "register":
            return "register"
        if head == "out" or head.startswith(("old-", "new-", "tamper-")):
            return "materials"
        if head == "common":
            return "common"
        return "copies"

    # -- on_narrow: drop の出し口（札の stage9.drop が真のメディアだけ）
    def drop_setup(self):
        v = self.vessel()
        used = {r.ref for r in self.recs[:3]}
        mode = self.cfg.get("e1", "row")

        def fit(k):
            return (k.format == "csv" and k.records == "rows" and "field" in k.id_rule) if mode == "row" \
                else k.records == "file"

        def usable(name):
            return [r for r in v.all_records if r.kind == name and r.ref not in used and r.id and not r.problems
                    and not v.is_duplicated(r.ref) and (mode != "row" or r.values)]
        order = [n for n in (self.cfg.get("outlet_kinds") or self.main.kinds) if fit(v.kinds[n])]
        kind = next((n for n in order if len(usable(n)) >= 3), None)
        if kind is None:
            raise Cannot("出し口の種類に、使っていない記録が 3 つある種類が無い")
        self.drop_kind = kind
        path = self.reg / ".thth" / "outlets.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["outlets"]["s9_drop"] = {"audience": "reader", "kinds": [kind], "on_narrow": "drop",
                                      "fact": [{"question": "may_publish@s9_drop", "conclusion": "good"}],
                                      "note": "段 9 の on_narrow: drop の試し（影だけ）"}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        v = self.vessel()
        self.drop = self.R.load_outlets(str(self.reg), v)["s9_drop"]
        self.R.baseline(str(self.reg), "s9_drop", by=BY, at=AT)
        self.accept("s9_drop")
        if not self.verify("s9_drop")["ok"]:
            raise RuntimeError("s9_drop を受け入れた直後の確かめが赤")
        on = set(self.material_refs(self.drop))
        cands = [r for r in usable(kind) if r.ref in on]
        if len(cands) < 3:
            raise Cannot(f"s9_drop の材料に載った記録が 3 つ無い（{len(cands)}）")
        self.drecs = cands[:3]
        self.out["drop_picked"] = {"kind": kind, "d1": cands[0].ref, "d2": cands[1].ref, "d4": cands[2].ref}

    def drop_case(self, label, rec, whys, event, now=NOW_BEFORE, accept_first=None):
        """受け入れ直す → 封のときの材料を写す → 事 → 外して書く → 外した材料は緑・封のときの材料は赤・残りを変えると赤。"""
        name = "s9_drop"
        if accept_first:
            accept_first()
        self.accept(name)
        old_dir = self.place / f"old-{label}" / name
        shutil.copytree(self.to(name), old_dir)
        how = event()
        new_dir = self.place / f"new-{label}" / name
        ex = self.R.export(str(self.reg), name, str(new_dir), now)
        leaving = [x.rsplit("=", 1)[0].split("#")[0] for x in ex["changes"]["leaving"]]
        green = self.verify(name, now=now, material=new_dir)
        dropped = self.items_of(green, "warnings", "dropped")
        red_old = self.verify(name, now=now, material=old_dir)
        tamper_dir = self.place / f"tamper-{label}" / name
        shutil.copytree(new_dir, tamper_dir)
        target = None
        for p in sorted(tamper_dir.rglob("*")):
            if p.is_file() and not p.name.startswith("thth-seal"):
                data = p.read_bytes()
                if p.suffix == ".csv" and data.count(b"\n") >= 2:
                    target = p
                    break
                target = target or p
        if target is None:
            raise Cannot("外して書いた材料に、変えて試せる残りのファイルが無い")
        data = target.read_bytes()
        cut = data.rstrip(b"\r\n")
        target.write_bytes(cut + b"x" + data[len(cut):])       # 残りの最後の行の終わりに 1 字足す
        red_tamper = self.verify(name, now=now, material=tamper_dir)
        checks = {"外れる物はその記録だけ": leaving == [rec.ref],
                  "外して書いた材料は緑（警告 dropped の項目はその記録だけ・理由が期待の語）":
                      green["ok"] and {r for r, _ in dropped} == {rec.ref} and {w for _, w in dropped} <= set(whys),
                  "封のときの材料のままは赤（外れた項目はその記録だけ）": self.red_is(red_old, rec.ref, whys),
                  "外して書いた材料の残りを 1 字変えると赤": not red_tamper["ok"],
                  "元の repo は同じ": self.guard_ok(label)}
        return checks, {"record": rec.ref, "how": how, "leaving": leaving[:5], "dropped": dropped[:5],
                        "green_problems": self.red_detail(green), "red_old": self.red_detail(red_old),
                        "red_tamper": self.red_detail(red_tamper),
                        "tampered": str(target.relative_to(self.place))}

    def d1(self):
        rec = self.drecs[0]
        return self.drop_case("d1", rec, CHANGED_WHYS, lambda: self.modify(rec, "d1"))

    def d2(self):
        rec = self.drecs[1]

        def until():
            self.R.judge(str(self.reg), rec.ref, "may_publish@s9_drop", conclusion="good", by=BY, at=AT, until=UNTIL,
                         reason_text="段9 d2 の期限つき")
        return self.drop_case("d2", rec, {"expired"}, lambda: f"判断の「いつまで」{UNTIL}・判定 {NOW_AFTER}",
                              now=NOW_AFTER, accept_first=until)

    def d4(self):
        rec = self.drecs[2]

        def stop():
            self.R.stop(str(self.reg), rec.ref, by=BY, at=AT)
            return f"stop {rec.ref}"
        return self.drop_case("d4", rec, {"stopped"}, stop)

    def run(self) -> dict:
        try:
            ok = self.setup()
            if not ok:
                self.out["verdict"] = verdict("fail", f"受け入れた直後の確かめが赤 {self.out['setup']}", cause="vessel")
                return self.out
            self.pick_records()
        except Exception as exc:  # noqa: BLE001 — 止まった所を報告に残す
            self.out["verdict"] = verdict("fail", f"支度で止まった {type(exc).__name__}: {str(exc)[:300]}",
                                          cause="script")
            return self.out
        fails = []
        for name in EVENTS:
            started = time.monotonic()
            try:
                checks, detail = getattr(self, name)()
                ok = all(checks.values())
                self.out["events"][name] = {"ok": ok, "checks": checks, "detail": detail,
                                            "seconds": round(time.monotonic() - started, 1)}
            except Cannot as exc:
                self.out["events"][name] = {"ok": None, "cannot": str(exc)}
                continue
            except Exception as exc:  # noqa: BLE001
                code = getattr(exc, "code", None)
                self.out["events"][name] = {"ok": False, "error": f"{type(exc).__name__}: {code or ''} "
                                                                 f"{getattr(exc, 'detail', '') or str(exc)[:300]}"}
                ok = False
            if not ok:
                bad = [k for k, v in self.out["events"][name].get("checks", {}).items() if not v]
                fails.append(f"{name} {bad or self.out['events'][name].get('error')}")
        if self.cfg.get("drop"):
            self.out["drop"] = {}
            try:
                self.drop_setup()
                todo = DROP_EVENTS
            except Cannot as exc:
                self.out["drop"]["setup"] = {"ok": None, "cannot": str(exc)}
                todo = []
            except Exception as exc:  # noqa: BLE001
                self.out["drop"]["setup"] = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
                fails.append(f"drop の支度 {type(exc).__name__}")
                todo = []
            for name in todo:
                started = time.monotonic()
                try:
                    checks, detail = getattr(self, name)()
                    self.out["drop"][name] = {"ok": all(checks.values()), "checks": checks, "detail": detail,
                                              "seconds": round(time.monotonic() - started, 1)}
                except Cannot as exc:
                    self.out["drop"][name] = {"ok": None, "cannot": str(exc)}
                    continue
                except Exception as exc:  # noqa: BLE001
                    self.out["drop"][name] = {"ok": False, "error": f"{type(exc).__name__}: {getattr(exc, 'code', '') or ''} "
                                                                    f"{getattr(exc, 'detail', '') or str(exc)[:300]}"}
                if not self.out["drop"][name]["ok"]:
                    bad = [k for k, v in self.out["drop"][name].get("checks", {}).items() if not v]
                    fails.append(f"{name} {bad or self.out['drop'][name].get('error')}")
        self.out["guard"] = self.guard.log
        skipped = [x for x in ("e1", "e2", "e3", "e4", "e5", "e6", "e7") if x not in EVENTS]
        cannot = [x for x, ev in self.out["events"].items() if ev.get("ok") is None]
        self.out["event_states"] = {x: ("未確認（打っていない）" if x not in self.out["events"] else
                                        "起こせない" if self.out["events"][x]["ok"] is None else
                                        "通る" if self.out["events"][x]["ok"] else "通らない")
                                    for x in ("e1", "e2", "e3", "e4", "e5", "e6", "e7")}
        if fails:
            self.out["verdict"] = verdict("fail", "・".join(fails)[:400], cause="?")
        elif skipped or cannot:
            self.out["verdict"] = verdict("unconfirmed", "期待のとおり: " + "・".join(x for x in EVENTS if x not in cannot)
                                       + (f"。起こせない: {'・'.join(cannot)}" if cannot else "")
                                       + (f"。打っていない: {'・'.join(skipped)}" if skipped else ""))
        else:
            self.out["verdict"] = verdict("pass", "e1〜e7" + ("・d1・d2・d4（on_narrow: drop）" if self.out.get("drop") else "")
                                          + " がどれも期待のとおり・元の repo は事ごとに同じ")
        return self.out


# ------------------------------------------------------------------ (f)

def check_f(media: dict, developer: pathlib.Path, work: pathlib.Path) -> dict:
    if not media["stage9"].get("page"):
        return {"verdict": verdict("na", "頁の一致はこぴちゃだけ（指示 §1）")}
    env = dict(os.environ, TMPDIR=str(work / "tmp"))
    r = subprocess.run([sys.executable, str(HERE.parent / "register_stage7_realcheck.py"), "--developer",
                        str(developer), "--work", str(work / "stage7"), "--json"], capture_output=True, text=True,
                       env=env, cwd=str(REPO))
    try:
        report = json.loads(r.stdout)
    except ValueError:
        return {"verdict": verdict("fail", f"段 7 の台本が止まった {(r.stderr or r.stdout)[-300:]}", cause="script")}
    outlets = {n: {"audience": e["audience"], "files": e["files"], "same": e["same"], "differ": e["differ"],
                   "verify_ok": e["verify_ok"], "ok": e["ok"]} for n, e in report["outlets"].items()}
    out = {"rc": r.returncode, "run": report["run"], "source_head": report["source_head"], "outlets": outlets}
    page = media["stage9"].get("page_result")
    if r.returncode == 0:
        why = "入力の一致: " + "・".join(f"{n} {e['same']}/{e['files']} 同じ" for n, e in outlets.items())
        out["verdict"] = verdict("pass", why + ("" if page else "（頁の一致は別に・--page か結果の 1 枚）"))
    else:
        out["verdict"] = verdict("fail", f"段 7 の確かめが rc {r.returncode}", cause="vessel")
    return out


# ------------------------------------------------------------------ 回す

def run_media(mods, S4, folder: pathlib.Path, media: dict, developer: pathlib.Path, work: pathlib.Path,
              only: set, s13) -> dict:
    A, K, V, J, R, H, E, C = mods
    root = developer / media["source"]
    place = work / folder.name
    place.mkdir(parents=True)
    out = {"name": media["name"], "folder": folder.name, "source": str(root), "results": {}}
    started = time.monotonic()
    if "stage9" not in media:
        out["results"] = {x: {"verdict": verdict("unconfirmed", "札に stage9 が無い")} for x in LETTERS}
        return out
    decl = place / "decl"
    shutil.copytree(folder / "kinds", decl / "kinds")
    if (folder / "constitution.json").exists():
        shutil.copy(folder / "constitution.json", decl / "constitution.json")
    res = out["results"]
    if "a" in only:
        res["a"] = check_a(A, V, root, media, str(decl))
    reg = None
    if only & set("bde"):
        try:
            b, reg, files = check_b(A, K, root, media, str(decl), place)
        except Exception as exc:  # noqa: BLE001
            b = {"verdict": verdict("fail", f"取り込みで止まった {type(exc).__name__}: "
                                            f"{getattr(exc, 'code', '')} {getattr(exc, 'detail', '') or str(exc)[:300]} "
                                            f"{str(getattr(exc, 'extra', '') or '')[:300]}",
                                    cause="?")}
        if "b" in only:
            res["b"] = b
    if "c" in only:
        res["c"] = check_c(S4, V, C, folder, media, developer, work / "tmp", s13)
    if "d" in only:
        res["d"] = check_d(V, V.open_vessel(str(reg)), media) if reg else {
            "verdict": verdict("unconfirmed", "取り込めなかった")}
    if "e" in only:
        if reg:
            guard = Guard(root, files)
            res["e"] = Events((A, K, V, J, R, H, E), root, reg, place, media, guard, folder).run()
        else:
            res["e"] = {"verdict": verdict("unconfirmed", "取り込めなかった")}
    if "f" in only:
        res["f"] = check_f(media, developer, place)
    out["seconds"] = round(time.monotonic() - started, 1)
    return out


MARK = {"pass": "通る", "fail": "通らない", "unconfirmed": "未確認", "na": "—", "incomplete": "未完了（器の不足）"}


def mark(v: dict) -> str:
    if v["state"] == "incomplete":
        return f"未完了（器の不足: {v.get('count', '?')} 件）"
    return MARK.get(v["state"], v["state"])


def say(results: list, s13) -> None:
    print("メディア | " + " | ".join(f"({x})" for x in LETTERS) + " | 秒")
    for one in results:
        cells = []
        for x in LETTERS:
            v = one["results"].get(x, {}).get("verdict")
            cells.append(mark(v) if v else "打っていない")
        print(f"{one['name']} | " + " | ".join(cells) + f" | {one.get('seconds')}")
    for one in results:
        print(f"\n== {one['name']}（{one['source']}）")
        for x in LETTERS:
            r = one["results"].get(x)
            if not r:
                continue
            v = r["verdict"]
            print(f"  ({x}) {mark(v)}: {v['why']}"
                  + (f"［分け: {v['cause']}］" if v.get("cause") else ""))
            if x == "a" and r.get("missing"):
                print(f"      どちらにも無い: {r['missing'][:5]}")
            if x == "d":
                for row in r.get("readers", []):
                    print(f"      [{row['state']}] {row['example'][:60]}: ファイル {row['files']}・記録 {row['records']}・"
                          f"問題 {row['problems']}・中を見ていない {row['not_inspected']} {row['why'][:120]}")
            if x == "e":
                if r.get("picked"):
                    print(f"      選び方: {r['picked'].get('rule')}・{r.get('outlets_from')}")
                for name, ev in list(r.get("events", {}).items()) + list(r.get("drop", {}).items()):
                    bad = [k for k, val in ev.get("checks", {}).items() if not val]
                    print(f"      {name} {'起こせない ' + ev['cannot'] if ev['ok'] is None else '期待のとおり' if ev['ok'] else '違う'}"
                          + (f" {bad}" if bad else "") + (f" {ev['error']}" if ev.get("error") else ""))
                    if name == "e7" and ev.get("detail", {}).get("left_outside"):
                        d = ev["detail"]
                        print(f"         器の外の写しに {d['left_outside']} か所残る（器は確かめない。場所の種類 "
                              f"{'・'.join(d['left_outside_places'])}・消す求めの結果は「確かめていない」{'・'.join(d['not_checked'])}）")
    if s13:
        print("\n段 1〜3 の実物の確かめ（この回の置き場で打った）:")
        for k, v in s13.items():
            if isinstance(v, dict):
                print(f"  {k}: rc {v.get('rc')}・前と同じ {v.get('ok')}" + (f" 違う {v.get('differs')}" if v.get("differs") else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--developer", default="/Volumes/NexDev/Developer")
    ap.add_argument("--work", required=True, help="置き場の親（thth-work/stage9）。毎回 run-<日時> を作る")
    ap.add_argument("--media", action="append", help="1 つだけ（media.json の name かフォルダの名前）。重ねてよい")
    ap.add_argument("--only", default="a,b,c,d,e,f")
    ap.add_argument("--replay", default=None, help="段 2 の確かめに渡す、こぴちゃの再演の置き場")
    ap.add_argument("--previous-adopt", default=None, help="段 2 の確かめの前の result.json（真偽を比べる）")
    ap.add_argument("--no-stage13", action="store_true", help="(c) で段 1〜3 の確かめを打たない（未確認になる）")
    ap.add_argument("--events", default=",".join(EVENTS), help="(e) で起こす事（既定は e1〜e7 の全部）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    EVENTS[:] = [x for x in EVENTS if x in args.events.split(",")]
    only = set(args.only.split(","))
    work = pathlib.Path(args.work) / ("run-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    work.mkdir(parents=True)
    for sub in ("config", "home", "root", "tmp"):
        (work / sub).mkdir()
    # 試しの鍵と THTH の根と一時の置き場は、この回の置き場だけ（手元の本物の鍵には触らない・値は出さない）
    os.environ.update(XDG_CONFIG_HOME=str(work / "config"), HOME=str(work / "home"), THTH_ROOT=str(work / "root"),
                      TMPDIR=str(work / "tmp"))
    os.environ.pop("THTH_REGISTER_SEAL_PUBLIC_KEY", None)
    os.environ.pop("THTH_REGISTERS_DIR", None)
    tempfile.tempdir = str(work / "tmp")
    from thth.register import adopt as A, checks as C, erase as E, health as H, judgment as J, keep as K
    from thth.register import route as R, seal as S, vessel as V
    S.init_key()
    S4 = module("register_stage4_realcheck")
    targets = []
    for path in sorted(DECLS.glob("*/media.json")):
        media = json.loads(path.read_text(encoding="utf-8"))
        if args.media and not ({media.get("name"), path.parent.name} & set(args.media)):
            continue
        targets.append((path.parent, media))
    if not targets:
        print("media.json が見つからない", file=sys.stderr)
        return 2
    s13 = None
    if "c" in only and not args.no_stage13:
        s13 = stage13(work, pathlib.Path(args.developer), args.replay, args.previous_adopt)
    results = []
    for folder, media in targets:
        one = run_media((A, K, V, J, R, H, E, C), S4, folder, media, pathlib.Path(args.developer), work, only, s13)
        results.append(one)
        print(f"[{one['name']}] " + " ".join(f"{x}={one['results'].get(x, {}).get('verdict', {}).get('state', '-')}"
                                            for x in LETTERS) + f" {one.get('seconds')} 秒", file=sys.stderr, flush=True)
    rc = 1 if any(r.get("verdict", {}).get("state") == "fail" for one in results for r in one["results"].values()) else 0
    report = {"run": str(work), "rc": rc, "as_of": AT, "stage1to3": s13, "media": results}
    (work / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str) + "\n",
                                      encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    else:
        say(results, s13)
        print(f"\n置き場: {work}\nrc={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
