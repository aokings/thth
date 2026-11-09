"""審査の台本と提出の前の点検（計画 Meta 全部盛り §4 の M5 の (d)(e)・`thth/appreview.py`）。

縛ること:
- 権限の正本は `permission_watch.PERMISSIONS` の 1 か所（台本の表はそこから引き、`PLANNED`・機能と重ねない）。
- 台本の命令は道具に実在し、穴は 1 語。`tools/appreview_segments.tsv` は生成物と一致し、`tools/appreview_rec.sh` が
  その並びを読む。本物で確かめていない口（IG のいいね）の区間には「本物で 1 回打ってから」の印がある。
- check: 30 日以内の成功の呼び出し・口・privacy の語のどれかが欠けると赤（3 行の断り）、全部そろえば黙って rc 0。
  privacy の文は偽の文で縛る（本物の privacy は M6 で直す途中）。
"""
from __future__ import annotations

import datetime
import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from tests.command_helpers import command_problem
from tests.test_m5_permission_watch import NOW, World, ago  # noqa: F401
from thth import accounts, appreview, cli, jst, refusal, runs, scopes
from thth import permission_watch as pw
from thth import refusal_catalog as rc
from thth.adapters import facebook, instagram
from thth.ads import auth as ads_auth

ROOT = Path(__file__).resolve().parent.parent
TSV = ROOT / "tools" / "appreview_segments.tsv"
REC = ROOT / "tools" / "appreview_rec.sh"


@pytest.fixture
def world(tmp_path, monkeypatch, thth_root, frozen_now_jst):
    frozen_now_jst(NOW)
    return World(tmp_path, monkeypatch)


def row(name):
    return next(item for item in appreview.items() if item["name"] == name)


def test_落とした権限は申請の表にも_privacyの語にも出ない():
    """計画 §1.10（masaru 11/08）: 成功の呼び出しを作れない 5 つは DROPPED に理由つきで残し、申請に入れない。"""
    assert set(appreview.DROPPED) == {"commerce_account_read_orders", "commerce_account_read_reports",
                                      "commerce_account_manage_orders", "attribution_read",
                                      "live_shopping_manage_video"}
    assert not set(appreview.DROPPED) & set(appreview.names())
    assert not set(appreview.DROPPED) & set(appreview.PRIVACY_WORDS)
    assert all(reason.strip() for reason in appreview.DROPPED.values())
    assert len(appreview.PLANNED) == 9


# ---------------------------------------------------------------- (d) 台本の表

def test_権限の正本はPERMISSIONSの1か所で_台本の表は重ねない():
    assert not set(appreview.PLANNED) & set(pw.PERMISSIONS)
    assert not set(appreview.FEATURES) & (set(pw.PERMISSIONS) | set(appreview.PLANNED))
    names = appreview.names()
    assert len(names) == len(set(names))
    assert {item["name"] for item in appreview.items() if item["requested_by_thth"]} == set(pw.PERMISSIONS)
    for item in appreview.items():
        if item["requested_by_thth"]:   # 媒体と読む／書くは PERMISSIONS から引く
            assert (item["media"], item["kind"]) == (pw.PERMISSIONS[item["name"]]["media"],
                                                     pw.PERMISSIONS[item["name"]]["kind"])


def test_認可が求める権限は全部台本の表に出る():
    wanted = {*scopes.DEFAULT_SCOPES, *facebook.SCOPES, *instagram.SCOPES, *ads_auth.SCOPES}
    for extra in scopes.META_INVITE_EXTRA.values():
        wanted |= set(extra)
    assert sorted(wanted - {item["name"] for item in appreview.items()}) == []


def test_区間の名前は表にあり_privacyの語は名前ごとに1つずつ():
    names = set(appreview.names())
    used = [name for segment in appreview.SEGMENTS for name in segment["items"]]
    assert sorted(set(used) - names) == []
    assert set(appreview.PRIVACY_WORDS) == names
    ids = [segment["id"] for segment in appreview.SEGMENTS]
    assert len(ids) == len(set(ids))


def test_区間の手の種類は決まったものだけ():
    for segment in appreview.SEGMENTS:
        assert segment["steps"] or segment["door"], segment["id"]
        for step in segment["steps"]:
            assert step[0] in appreview.STEP_KINDS, (segment["id"], step)
            assert all("\t" not in part and "\n" not in part for part in step)


def _argv(template):
    return [token if not appreview.SLOT.fullmatch(token) else ("@x" if "USER" in token or "PAGE" in token else "1")
            for token in shlex.split(template)][1:]


def test_台本の命令は道具に実在する():
    parser = cli.build_parser()
    wrong = []
    for segment in appreview.SEGMENTS:
        for command in appreview.segment_commands(segment):
            problem = command_problem(parser, command)
            if problem:
                wrong.append(f"{segment['id']}: {command} — {problem}")
                continue
            try:
                parser.parse_args(_argv(command))
            except SystemExit:
                wrong.append(f"{segment['id']}: {command} — argparse が断った")
    assert wrong == []


def test_穴は1語だけで_聞く文がある():
    for segment in appreview.SEGMENTS:
        for command in appreview.segment_commands(segment):
            for token in command.split():
                if "{" in token or "}" in token:
                    found = appreview.SLOT.fullmatch(token)
                    assert found, (segment["id"], token)
                    assert found.group(1) in appreview.SLOTS, (segment["id"], token)


def test_本物で確かめていない口の区間には印がある():
    # 計画 §4: DM の綴りと窓の外の札は本物で確かめるまで印を付ける。IG のいいねは 11/08 本物で
    # POST /{ig-user-id}/likes が通ったので印を外した（「本物で打って分かったこと」）。
    for segment in appreview.SEGMENTS:
        if any("--human-agent" in command or command.startswith("thth dm send")
               for command in appreview.segment_commands(segment)):
            assert segment["real_first"], segment["id"]
    assert not row("instagram_manage_engagement")["real_first"]
    assert not row("threads_basic")["real_first"]


def test_口の無い権限は表で口が無いと言う():
    assert row("pages_utility_messaging")["doors"] == []
    assert row("email")["doors"] == [] and not row("email")["requested_by_thth"]
    assert row("threads_basic")["doors"] == ["thth posts"]
    assert row("ads_mcp_management")["doors"]            # 手だけの区間は区間の口の文
    assert row("marketing_api_access_tier")["doors"]     # 録画の要らない機能
    assert row("marketing_api_access_tier")["segments"] == []


def test_scriptの人向けと_jsonは1つの値(capsys):
    assert cli.main(["admin", "appreview", "script"]) == 0
    out = capsys.readouterr().out
    assert "| `threads_basic` | 読む | `thth posts` |" in out
    assert "**口が無い**" in out and "（本物で 1 回打ってから）" in out
    assert cli.main(["admin", "appreview", "script", "--json"]) == 0
    view = json.loads(capsys.readouterr().out)
    assert view["counts"]["segments"] == len(appreview.SEGMENTS)
    assert {item["name"] for item in view["items"]} == set(appreview.names())


def test_tsvは生成物と一致する(capsys):
    """直したら `python3 -m thth admin appreview script --tsv > tools/appreview_segments.tsv` で作り直す。"""
    assert cli.main(["admin", "appreview", "script", "--tsv"]) == 0
    assert TSV.read_text(encoding="utf-8") == capsys.readouterr().out == appreview.tsv()


def _bash(*args):
    return subprocess.run(["bash", str(REC), *args], capture_output=True, text=True, timeout=60, cwd=str(ROOT),
                          env={**os.environ, "REC_BY": "tester"})


def test_録画の台本は生成した区間の並びを読む():
    done = _bash("--list")
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert [line.split("\t")[0] for line in lines if not line.startswith("\t")] == \
        [segment["id"] for segment in appreview.SEGMENTS]
    assert sum(1 for line in lines if "本物で 1 回打ってから" in line) == \
        sum(1 for segment in appreview.SEGMENTS if segment["real_first"])


def test_録画の台本のdryは全区間の命令を穴の名前のまま見せて何も打たない():
    done = _bash("--dry")
    assert done.returncode == 0, done.stderr
    out = done.stdout
    assert f"区間の数: {len(appreview.SEGMENTS)}" in out
    steps = sum(1 for segment in appreview.SEGMENTS for step in segment["steps"]
                if step[0] in ("cmd", "two", "twooff", "login"))
    assert out.count("$ thth ") == steps
    assert "$ thth posts <TH> --limit 3" in out
    assert "$ thth like <IG> <IG_MEDIA> --by tester" in out
    assert "（二段目: --confirm <digest>・録画の外）" in out          # ライブの宛先（鍵）は録画の外
    assert "[本物で 1 回打ってから]" in out


def test_録画の台本のdryは区間を選べる():
    done = _bash("--dry", "th-basic", "ig-like")
    assert done.returncode == 0, done.stderr
    assert "区間の数: 2" in done.stdout and "== 区間 fb-live" not in done.stdout


def test_知らない区間のidは名前を出してrc2で断る():
    for mode in ("--dry", "--segments"):
        done = _bash(mode, "th-basic", "th-nope", "zzz")
        assert done.returncode == 2, (mode, done.stdout, done.stderr)
        assert "th-nope" in done.stdout and "zzz" in done.stdout
        assert "区間の数" not in done.stdout and "== 区間" not in done.stdout


def test_取り下げの区間は二段で_byが付く():
    """`thth retract` は手元の道も遠くの道も二段確認で、手元の道では --by が要る。cmd のままだと一段目で止まり消えない。"""
    found = [(segment["id"], step) for segment in appreview.SEGMENTS for step in segment["steps"]
             if len(step) > 1 and step[1].startswith("thth retract")]
    assert {sid for sid, _ in found} == {"th-delete", "fb-publish", "ig-delete"}
    for sid, step in found:
        assert step[0] == "two" and "--by {BY}" in step[1], (sid, step)
    text = REC.read_text(encoding="utf-8")
    assert "retract" in text and '--by "$BY"' in text and '--confirm "$DIGEST"' in text   # 録画 0 の区間 3


def test_Threadsのストーリーズ共有は投稿の本文と別の穴():
    commands = {segment["id"]: appreview.segment_commands(segment) for segment in appreview.SEGMENTS}
    assert "{BODY2}" in commands["th-share-ig"][0] and "{BODY}" not in commands["th-share-ig"][0]
    assert "BODY2" in appreview.SLOTS
    assert "{BODY}" in commands["th-publish"][0]
    assert "REC_V_BODY2" in REC.read_text(encoding="utf-8")


def test_二段目の前には人の手を待つ所がある():
    """seg_two が二段目の前に seg_pause を置く（dry は「[人の手]」で見せる）。二段の数だけ出る。"""
    done = _bash("--dry")
    assert done.returncode == 0, done.stderr
    two = sum(1 for segment in appreview.SEGMENTS for step in segment["steps"] if step[0] in ("two", "twooff"))
    assert two >= 8
    assert done.stdout.count("[人の手] 一段目の中身を確かめ、二段目を打ってよければ Return") == two
    body = REC.read_text(encoding="utf-8").split("seg_two() {")[1].split("\nseg_step()")[0]
    assert body.index("seg_pause '一段目の中身を確かめ") < body.index('show "${ARGS[@]}" --confirm')


# ---------------------------------------------------------------- (e) 提出の前の点検

ITEMS = ("threads_basic", "pages_manage_cta", "threads_oembed_read")
TEXT = "Threads: posts. Facebook Pages: the call-to-action button. Posts can be shown with embed."


def fake_view(**days):
    return {"permissions": [{"permission": name, "last_success_at": jst.iso(ago(value))}
                            for name, value in days.items()]}


def subset(*names):
    return [item for item in appreview.items() if item["name"] in names]


def test_全部そろえば問題は無い():
    result = appreview.evaluate(fake_view(threads_basic=1, pages_manage_cta=29), TEXT, now=NOW, rows=subset(*ITEMS))
    assert result["ok"] and result["problems"] == []


def test_壊す_1権限の呼び出しの記録を消すと赤():
    result = appreview.evaluate(fake_view(pages_manage_cta=1), TEXT, now=NOW, rows=subset(*ITEMS))
    assert not result["ok"]
    assert result["problems"] == [{"code": "appreview_no_recent_call", "level": "red", "names": ["threads_basic"]}]


@pytest.mark.parametrize("days, ok", [(29, True), (30, False), (45, False)])
def test_30日の線(days, ok):
    result = appreview.evaluate(fake_view(threads_basic=days), TEXT, now=NOW, rows=subset("threads_basic"))
    assert result["ok"] is ok


def test_機能は呼び出しを数えず_口とprivacyだけ見る():
    result = appreview.evaluate(fake_view(), TEXT, now=NOW, rows=subset("threads_oembed_read"))
    assert result["ok"]
    assert result["items"][0]["recent_call"] is None


def test_口の無い権限は赤():
    result = appreview.evaluate(fake_view(), "Messenger utility", now=NOW, rows=subset("pages_utility_messaging"))
    codes = {problem["code"]: problem["names"] for problem in result["problems"]}
    assert codes["appreview_no_door"] == ["pages_utility_messaging"]
    assert codes["appreview_no_recent_call"] == ["pages_utility_messaging"]
    assert "appreview_privacy_missing" not in codes


def test_privacyに語が無ければ赤で_大文字小文字は見ない():
    result = appreview.evaluate(fake_view(threads_basic=1, pages_manage_cta=1), "THREADS: posts. embed.",
                                now=NOW, rows=subset(*ITEMS))
    assert result["problems"] == [{"code": "appreview_privacy_missing", "level": "red", "names": ["pages_manage_cta"]}]
    assert result["items"][1]["privacy_missing"] == ["Facebook Pages:", "call-to-action"]


def test_媒体の語のある権限は_その媒体の項の中で語を探す():
    """Threads の項から metrics が消えても、Bluesky の項に metrics があれば通る、を許さない。"""
    rows = subset("threads_manage_insights")
    page = "<li>Threads: the account's posts.</li>\n<li>Bluesky: its posts and their metrics.</li>"
    result = appreview.evaluate(fake_view(threads_manage_insights=1), appreview._strip(page), now=NOW, rows=rows)
    assert result["problems"] == [{"code": "appreview_privacy_missing", "level": "red",
                                   "names": ["threads_manage_insights"]}]
    assert result["items"][0]["privacy_missing"] == ["metrics"]
    page = "<li>Threads: the account's posts and their metrics.</li>\n<li>Bluesky: its posts.</li>"
    assert appreview.evaluate(fake_view(threads_manage_insights=1), appreview._strip(page), now=NOW, rows=rows)["ok"]


def test_媒体の語は見出しの下の段落も項に数える():
    rows = subset("pages_messaging")
    page = ("<h2>Messenger and Instagram Direct</h2><p>Replies go out within 24 hours.</p>"
            "<h2>Other</h2><p>unrelated</p>")
    assert appreview.evaluate(fake_view(pages_messaging=1), appreview._strip(page), now=NOW, rows=rows)["ok"]
    page = "<h2>Messenger</h2><p>nothing here.</p><h2>Other</h2><p>within 24 hours</p>"
    result = appreview.evaluate(fake_view(pages_messaging=1), appreview._strip(page), now=NOW, rows=rows)
    assert result["items"][0]["privacy_missing"] == ["24 hours"]


def test_媒体の語の無い権限は頁全体で探す():
    page = appreview._strip("<p>Threads: posts.</p>\n<p>Posts can be shown with embed.</p>")
    assert appreview.evaluate(fake_view(), page, now=NOW, rows=subset("threads_oembed_read"))["items"][0][
        "privacy_missing"] == []


def test_privacyが読めなければ言えないと言う():
    result = appreview.evaluate(fake_view(threads_basic=1), None, now=NOW, rows=subset("threads_basic"))
    assert [problem["code"] for problem in result["problems"]] == ["appreview_privacy_unreadable"]


def test_断りの符丁は目録にあり3つがそろう():
    for code in (*appreview.CHECK_REFUSALS, *appreview.PRIVACY_REFUSALS):
        entry = rc.CATALOG[code]
        assert entry["why"] and entry["fix"]["text"] and entry["better"] and entry["since"] == "5.13.0", code
        assert refusal.parts(code)["fix"]


def test_privacyの既定はbuild_siteが作る頁の文(tmp_path):
    text = appreview.privacy_text()
    assert text and "Privacy Policy" in text and "<" not in text[:200]
    page = tmp_path / "privacy.html"
    page.write_text("<p>Threads: <b>posts</b> &amp; more</p>", encoding="utf-8")
    assert appreview.privacy_text(str(page)).strip() == "Threads: posts & more"
    assert appreview.privacy_text(str(tmp_path / "missing.html")) is None


@pytest.fixture
def few(monkeypatch, tmp_path):
    chosen = subset("threads_basic", "threads_oembed_read")
    monkeypatch.setattr(appreview, "items", lambda: chosen)
    page = tmp_path / "privacy.html"
    page.write_text("<li>Threads: posts</li><p>embed</p>", encoding="utf-8")
    return str(page)


def test_checkは通れば黙ってrc0_記録を消すと赤の3行(world, few, capsys):
    name = world.add("a-threads", obtained=40)
    world.run(name, "collect", 3, collected=1)
    assert cli.main(["admin", "appreview", "check", "--privacy", few]) == 0
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""
    os.remove(runs.path_for(accounts.state_dir_for(name), jst.month_str(ago(3))))   # 呼び出しの記録を消す
    assert cli.main(["admin", "appreview", "check", "--privacy", few]) == 2
    err = capsys.readouterr().err.splitlines()
    assert err[0] == "appreview_no_recent_call: 赤 1 件（threads_basic）"
    assert err[1].startswith("  " + refusal.WHY) and err[2].startswith("  " + refusal.FIX)
    assert err[3].startswith("  " + refusal.BETTER)


def test_checkのjsonは1つの値で3つの欄(world, few, capsys):
    world.add("a-threads", obtained=40)
    assert cli.main(["admin", "appreview", "check", "--privacy", few, "--json"]) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["ok"] is False and value["call_days"] == appreview.CALL_DAYS
    problem = value["problems"][0]
    assert problem["code"] == "appreview_no_recent_call" and problem["why"] and problem["fix"] and problem["better"]


def test_checkは網に出ず何も書かない(world, few, monkeypatch, capsys):
    import socket
    monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("unexpected network"))
    world.add("a-threads", obtained=1)
    before = sorted(str(path) for path in Path(os.environ["THTH_ROOT"]).rglob("*"))
    cli.main(["admin", "appreview", "check", "--privacy", few])
    assert sorted(str(path) for path in Path(os.environ["THTH_ROOT"]).rglob("*")) == before
    capsys.readouterr()


def test_今の表では口の無い権限と申請の前の呼び出しが赤になる():
    """偽の state（PERMISSIONS の全部を昨日使った）と偽の privacy（全部の語）で、残る赤は口の無いものだけ。"""
    # 媒体の語のある権限は媒体の項の中で探すので、偽の privacy は「権限ごとに 1 つの箇条」で全部の語を書く
    every = "\n".join(" ".join(words) for words in appreview.PRIVACY_WORDS.values() if words)
    view = fake_view(**{name: 1 for name in pw.PERMISSIONS})
    result = appreview.evaluate(view, every, now=NOW)
    codes = {problem["code"]: problem["names"] for problem in result["problems"]}
    no_door = sorted(item["name"] for item in appreview.items() if not item["doors"])
    assert sorted(codes["appreview_no_door"]) == no_door
    assert set(codes["appreview_no_recent_call"]) == set(appreview.PLANNED)   # 認可が求めないので呼べない
    assert "appreview_privacy_missing" not in codes
    assert appreview.CALL_DAYS == 30


def test_記録に残らない権限は赤の中でそう分けて言う():
    """evidence の空の権限と PLANNED は、1 回打っても記録に残らない（Dashboard の呼び出しの欄で確かめる）。"""
    assert pw.PERMISSIONS["threads_basic"]["evidence"] and not pw.PERMISSIONS["pages_utility_messaging"]["evidence"]
    names = ("threads_basic", "pages_utility_messaging", "email")
    result = appreview.evaluate(fake_view(), "", now=NOW, rows=subset(*names))
    problem = next(p for p in result["problems"] if p["code"] == "appreview_no_recent_call")
    assert set(problem["names"]) == set(names)
    assert set(problem["unrecorded"]) == {"pages_utility_messaging", "email"}
    fix = rc.CATALOG["appreview_no_recent_call"]["fix"]["text"]
    assert "Dashboard" in fix and "記録に残らない" in fix


def test_日付は記録の時刻から数える():
    at = NOW - datetime.timedelta(days=29, hours=23)
    view = {"permissions": [{"permission": "threads_basic", "last_success_at": jst.iso(at)}]}
    assert appreview.evaluate(view, TEXT, now=NOW, rows=subset("threads_basic"))["ok"]


def test_広告の区間はloginの道で撮り_遠くの道に出ない命令を使わない():
    """設計 広告の資格と遠くの道 段 5: 広告の招待も鍵が結ばれるので、IG と同じく広告の招待で login し直す。"""
    from thth import remote
    by_id = {segment["id"]: segment for segment in appreview.SEGMENTS}
    assert ("login",) in by_id["flow-ads"]["steps"]
    assert not any("REC_LOCAL_THTH" in str(step) for step in by_id["flow-ads"]["steps"])
    for segment in appreview.SEGMENTS:
        if segment["medium"] != "ads":
            continue
        for command in appreview.segment_commands(segment):
            words = command.split()
            key = " ".join(words[1:4]) if words[2] == "catalog" else " ".join(words[1:3])
            assert key in remote.ADS_COMMANDS, f"{segment['id']}: {command} は遠くの道に無い"
            assert "--post " not in command, "審査員の台帳に結んだページは無い（--object-story-id）"
