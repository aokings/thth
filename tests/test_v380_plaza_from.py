"""3.8.0 C 置く手間を減らす（設計 3.8.0 §C・`thth plaza post … --from …`）。

見るのは:
  1. `--from analytics-report|after <account>`: 置く時点で道具が計算を呼び直し、数字（分母・
     期間・言えないこと）を観測の欄（`tool_numbers`）と本文の下書きに入れる。**observed は
     道具が付けた数字だけ**——人や LLM の本文の数字は observed にしない。
  2. `--from study-report <宣言>`: 3.4.0 と同じ観測の列（同じ計算）と下書き。
  3. `--from-doc <md>`: repo の中の文書を finding として（元のパスと commit・先頭 4,000 字・
     秘密の検査・commit していない変更がある文書は置かない）。
  4. `--from-report <id>`: 自分の project の閉じた報告の返事を tool_tip として写す。
"""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from thth import after_cli, analytics_report, cli, plaza, plaza_from, report_inbox
from tests.conftest import commit_and_push_path
from tests.test_v340_plaza_store import SCOPE, owners, post  # noqa: F401  (fixture)
from tests.test_v380_plaza_owner import viewer

# 3.9.1: text_numbers は単位や記号が付いた数だけを拾う（裸の数字は表のセル以外は拾わない）
# ようになったので、人の本文の数字は "999" 単体でなく "n=999" にしてある（意味は変えていない）。
HUMAN = "人の解釈: 朝のほうが伸びた気がする。n=999 と書いておく"


def _fake_node(views_median=340, n=12):
    return {"summary": "要約（写さない）",
            "posts": {"n": n, "views_24h": {"median": views_median, "p25": 1, "p75": 2, "n": n},
                      "by_post": [{"post_id": "OTHER-POST-ID", "text": "OTHER-REPLY-TEXT"}]},
            "engagements": {"n": 5, "reacted": 3, "likes_24h": {"median": 2, "n": 5},
                            "replies_back_24h": {"median": None, "n": 2},
                            "views_24h": {"median": 40, "n": 5},
                            "by_branch": [{"username": "SOMEONE-ELSE", "text": "OTHER-REPLY-TEXT"}]},
            "cannot_say": ["posted_at が読めず数えなかった実測投稿: 1 本"],
            "one_thing_to_change": "写さない"}


@pytest.fixture
def fake_after(monkeypatch):
    calls = []

    def answer(account_name=None, **kwargs):
        calls.append((account_name, kwargs))
        return _fake_node()
    monkeypatch.setattr(after_cli, "answer", answer)
    return calls


def test_fromのafterは道具の数字を観測の欄と下書きに_人の数字はobservedにしない(owners, fake_after):
    result = post(title="朝の結果", body=HUMAN, from_source=("after", "kopicha-threads", None))
    assert result["from"] == "after" and result["evidence_level"] == "observed"
    record = plaza.STORE.get(result["plaza_id"])
    numbers = record["tool_numbers"]
    assert numbers["source"] == "after" and numbers["target"] == "kopicha-threads"
    assert numbers["numbers"]["posts"] == {"n": 12, "views_24h": {"median": 340, "n": 12}}
    assert numbers["period"]["window_days"] == 30 and numbers["observed"] is True
    assert numbers["cannot_say"] == ["posted_at が読めず数えなかった実測投稿: 1 本"]
    # 人の本文の数字は観測に入らない（「本文」の欄に分けて出る）。
    assert "999" not in json.dumps(numbers, ensure_ascii=False)
    assert record["how"] == "thth after kopicha-threads --window-days 30"
    body = record["body"]
    assert body.startswith(plaza_from.DRAFT_HEAD) and body.endswith(HUMAN)
    assert "24h views 中央値 340（n=12）" in body
    # 投稿ごと・枝ごとの行（他人の返信・username）は写さない。
    assert "OTHER-REPLY-TEXT" not in json.dumps(record, ensure_ascii=False)
    assert "SOMEONE-ELSE" not in json.dumps(record, ensure_ascii=False)
    shown = plaza.show(result["plaza_id"], viewer("kopicha-bsky"))
    assert shown["tool_numbers"]["numbers"]["posts"]["n"] == 12
    assert any("999" in value for value in shown["text_numbers"]["values"])
    assert fake_after[0][0] == "kopicha-threads" and fake_after[0][1]["window_days"] == 30


def test_道具の数字が取れなければ本文に数字があってもobservedにしない(owners):
    # 本物の台帳（空）: 投稿 0 本・中央値はどれも言えない。
    result = post(title="空の台帳", body=HUMAN, from_source=("after", "kopicha-threads", 7))
    record = plaza.STORE.get(result["plaza_id"])
    assert record["tool_numbers"]["observed"] is False
    assert result["evidence_level"] == "stated"
    assert plaza.show(result["plaza_id"], viewer("kopicha-threads"))["evidence_level"] == "stated"
    assert record["tool_numbers"]["numbers"]["posts"]["n"] == 0


def test_fromのanalytics_reportも同じ形(owners, monkeypatch):
    payload = {"period": {"start": "2026-09-02T00:00:00+09:00", "end": "2026-09-09T00:00:00+09:00",
                          "window_days": 7, "basis": "posted_at", "timezone": "Asia/Tokyo"},
               "by_account": {"kopicha-bsky": _fake_node(views_median=51, n=6)}, "cannot_say": []}
    monkeypatch.setattr(analytics_report, "answer", lambda *a, **k: payload)
    result = post(title="Bluesky の週", body=None, kind="finding",
                  from_source=("analytics-report", "kopicha-bsky", None))
    record = plaza.STORE.get(result["plaza_id"])
    assert record["tool_numbers"]["medium"] == "bluesky"
    assert record["tool_numbers"]["numbers"]["posts"]["views_24h"] == {"median": 51, "n": 6}
    assert record["how"] == "thth analytics-report kopicha-bsky --window-days 7"
    assert record["body"].startswith(plaza_from.DRAFT_HEAD)


def test_fromは同じ持ち主のaccountだけ(owners, fake_after):
    with pytest.raises(plaza.PlazaError, match="^from_out_of_scope$"):
        post(title="他人の数字", from_source=("after", "other-threads", None))
    with pytest.raises(plaza.PlazaError, match="^invalid_from$"):
        post(title="知らない種類", from_source=("measured", "kopicha-threads", None))
    assert fake_after == []


def test_openの写しの道具の数字はaccount名と命令を落とす(owners, fake_after):
    plaza.set_membership("kopicha", joined=True, by="operator")
    plaza.set_membership("other", joined=True, by="operator")
    result = post(title="open の数字", body=None, visibility="open",
                  from_source=("after", "kopicha-bsky", None))
    shown = plaza.show(result["plaza_id"], viewer("other-threads"))
    assert shown["view"] == "open" and shown["tool_numbers"]["numbers"]["posts"]["n"] == 12
    assert "kopicha-bsky" not in json.dumps(shown["tool_numbers"], ensure_ascii=False)
    assert shown["source"] == {"kind": "after"}


def _declaration(tmp_path, account="kopicha-threads"):
    path = tmp_path / "study.json"
    path.write_text(json.dumps({"schema_version": 1, "id": "s1", "account": account,
                                "hypothesis": "問いの冒頭で返信が増える", "change": "冒頭を問いに",
                                "decision": {"status": "proposed"},
                                "baseline_post_ids": [], "changed_post_ids": []}), encoding="utf-8")
    return path


def test_fromのstudy_reportは同じ観測の列と下書き(owners, tmp_path, capsys):
    path = _declaration(tmp_path)
    rc = cli.main(["plaza", "post", "kopicha-threads", "--from", "study-report", str(path),
                   "--title", "問いの冒頭", "--scope", SCOPE, "--by", "k", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert rc == 0 and result["kind"] == "measure" and result["from"] == "study-report"
    record = plaza.STORE.get(result["plaza_id"])
    column = record["observations"][0]["columns"][0]
    assert column["reason"] == "proposed_not_adopted"
    assert record["body"].startswith(plaza_from.DRAFT_HEAD)
    assert "threads: 取れない（proposed_not_adopted）" in record["body"]
    assert record["how"].startswith("thth study-report ") and record["hypothesis"] == "問いの冒頭で返信が増える"
    # 宣言の数字は観測の列（道具の計算）から。未採用なので observed にはならない。
    assert result["evidence_level"] == "stated"
    with pytest.raises(plaza.PlazaError, match="^not_a_measure$"):
        post(kind="finding", title="finding に宣言", from_source=(
            "study-report", json.loads(path.read_text()), "thth study-report x"))


# ------------------------------------------------------------ 文書

def _doc(owners, name="docs/tips.md", text="# 朝の問いの型\n\n冒頭を問いにする。\n", commit=True):
    repo = owners["kopicha-threads"]["repo_dir"]
    path = os.path.join(repo, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        stream.write(text)
    if commit:
        assert commit_and_push_path(path, message="doc")
    return path


def _head(repo):
    return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip()


def test_from_docは元のパスとcommitを持つfinding(owners):
    path = _doc(owners)
    result = post(title=None, body=None, kind=None, from_source=("doc", path))
    record = plaza.STORE.get(result["plaza_id"])
    assert record["kind"] == "finding" and record["title"] == "朝の問いの型"
    commit = _head(owners["kopicha-threads"]["repo_dir"])
    assert record["source"] == {"kind": "doc", "path": "docs/tips.md", "commit": commit,
                                "truncated": False, "chars": len("# 朝の問いの型\n\n冒頭を問いにする。")}
    assert record["body"].startswith(f"［元の文書: docs/tips.md@{commit[:7]}］")
    # repo の中の相対パスでも同じ。
    again = post(title="相対", body="解釈", kind="finding", from_source=("doc", "docs/tips.md"))
    assert plaza.STORE.get(again["plaza_id"])["body"].endswith("解釈")


def test_from_docは先頭4000字まで(owners):
    long_text = "# 長い文書\n\n" + ("あ" * 5000)
    path = _doc(owners, name="docs/long.md", text=long_text)
    record = plaza.STORE.get(post(title=None, body=None, kind=None,
                                  from_source=("doc", path))["plaza_id"])
    assert record["source"]["truncated"] is True
    assert "・先頭 4,000 字］" in record["body"].splitlines()[0]
    assert len(record["body"].split("\n", 1)[1]) == 4000


def test_from_docの断り(owners, tmp_path):
    with pytest.raises(plaza.PlazaError, match="^from_doc_not_committed$"):
        post(title=None, body=None, kind=None, from_source=("doc", _doc(owners, name="docs/new.md",
                                                                       commit=False)))
    committed = _doc(owners, name="docs/edited.md")
    with open(committed, "a", encoding="utf-8") as stream:
        stream.write("未 commit の追記\n")
    with pytest.raises(plaza.PlazaError, match="^from_doc_not_committed$"):
        post(title=None, body=None, kind=None, from_source=("doc", committed))
    outside = tmp_path / "outside.md"
    outside.write_text("# 外\n", encoding="utf-8")
    with pytest.raises(plaza.PlazaError, match="^from_doc_outside_repo$"):
        post(title=None, body=None, kind=None, from_source=("doc", str(outside)))
    with pytest.raises(plaza.PlazaError, match="^invalid_from_doc$"):
        post(title=None, body=None, kind=None, from_source=("doc", _doc(owners, name="docs/a.txt")))
    with pytest.raises(plaza.PlazaError, match="^from_is_finding$"):
        post(title=None, body=None, kind="measure", from_source=("doc", _doc(owners)))
    secret = _doc(owners, name="docs/secret.md", text="# 秘密\n\ntoken: sk-" + "a" * 40 + "\n")
    with pytest.raises(plaza.PlazaError, match="^secret_detected$"):
        post(title=None, body=None, kind=None, from_source=("doc", secret))


# ------------------------------------------------------------ 閉じた報告

_SERIAL = iter(range(1000))


def _report(account="kopicha-threads", close=True, reply=True):
    title = "approve の --by を忘れる" + ("" if close and reply and account == "kopicha-threads"
                                        else f" {next(_SERIAL)}")
    filed = report_inbox.file_report(account, kind="friction", title=title,
                                     body="報告の本文（写さない）", by="kopicha-session")
    report_inbox.add(filed["report_id"], by="kopicha-session", text="報告した側の追記（写さない）")
    if reply:
        report_inbox.reply(filed["report_id"], by="operator",
                           text="THTH_ACTOR を環境に入れておけば --by を省けます")
    if close:
        report_inbox.close(filed["report_id"], by="operator", reason="fixed", version="3.7.0")
    return filed["report_id"]


def test_from_reportは閉じた報告の返事をtool_tipとして写す(owners):
    report_id = _report()
    result = post(account="kopicha-bsky", title=None, body=None, kind=None,
                  from_source=("report", report_id))
    record = plaza.STORE.get(result["plaza_id"])
    assert record["kind"] == "finding" and record["kind_detail"] == "tool_tip"
    assert record["title"] == "approve の --by を忘れる"
    assert "THTH_ACTOR を環境に入れておけば --by を省けます" in record["body"]
    assert "写さない" not in record["body"]
    assert record["source"] == {"kind": "report", "report_id": report_id,
                                "closed_version": "3.7.0", "closed_reason": "fixed"}


def test_from_reportの断り(owners):
    with pytest.raises(plaza.PlazaError, match="^from_report_open$"):
        post(title=None, body=None, kind=None, from_source=("report", _report(close=False)))
    with pytest.raises(plaza.PlazaError, match="^from_report_no_reply$"):
        post(title=None, body=None, kind=None, from_source=("report", _report(reply=False)))
    theirs = _report(account="other-threads")
    with pytest.raises(plaza.PlazaError, match="^from_report_not_found$"):
        post(title=None, body=None, kind=None, from_source=("report", theirs))
    with pytest.raises(plaza.PlazaError, match="^from_report_not_found$"):
        post(title=None, body=None, kind=None, from_source=("report", "r20260909-00000000"))
    with pytest.raises(plaza.PlazaError, match="^invalid_kind_detail$"):
        post(title=None, body=None, kind=None, kind_detail="pitfall",
             from_source=("report", _report()))


# ------------------------------------------------------------ CLI

def test_CLIの口(owners, fake_after, capsys, tmp_path):
    rc = cli.main(["plaza", "post", "kopicha-threads", "--kind", "finding", "--from", "after",
                   "kopicha-threads", "--from-window-days", "14", "--title", "2 週",
                   "--scope", SCOPE, "--by", "k", "--json"])
    result = json.loads(capsys.readouterr().out)
    assert rc == 0 and result["from"] == "after" and result["evidence_level"] == "observed"
    assert fake_after[-1][1]["window_days"] == 14
    rc = cli.main(["plaza", "show", result["plaza_id"], "--as", "kopicha-bsky"])
    out = capsys.readouterr().out
    assert rc == 0 and "[観測（道具が付けた数字）] after" in out and "24h views 中央値 340（n=12）" in out
    path = _doc(owners)
    rc = cli.main(["plaza", "post", "kopicha-threads", "--from", "after", "kopicha-threads",
                   "--from-doc", path, "--scope", SCOPE, "--by", "k"])
    assert rc == 2 and "invalid_from" in capsys.readouterr().err
    rc = cli.main(["plaza", "post", "kopicha-threads", "--from-doc", path, "--scope", SCOPE,
                   "--by", "k", "--json"])
    assert rc == 0 and json.loads(capsys.readouterr().out)["from"] == "doc"
    rc = cli.main(["plaza", "post", "kopicha-threads", "--kind", "finding", "--title", "x",
                   "--scope", SCOPE, "--by", "k"])
    assert rc == 2 and "invalid_post" in capsys.readouterr().err
