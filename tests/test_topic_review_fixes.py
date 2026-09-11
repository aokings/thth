"""独立レビュー（2026-09-11・Codex）で出た 6 件の修正。

`tests/test_independent_topic_review.py` はレビュアーが書いた再現テストを
**そのまま**置いてある。こちらは同じ穴を**別の角度から**押さえる。
「1 つの入力で直った」ことと「塞がった」ことは違う。
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys

import pytest

from tests.conftest import run_thth, write_queue_file
from tests.test_topic_advice import (candidate, ids, make_article,
                                      make_context, make_observation)
from thth import topic_advice as advice
from thth import topic_models as models
from thth import topic_store as store

NOW = datetime.datetime.fromisoformat("2026-09-11T12:00:00+09:00")
BODY = "## threads\n\n精製の話。https://example.test/coffee\n"
BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "bin", "thth")


def _run(args, payload=None):
    return subprocess.run(
        [sys.executable, BIN, *args], capture_output=True, text=True,
        env=dict(os.environ),
        input=None if payload is None else json.dumps(payload, ensure_ascii=False))


# --- 指摘 1: 投稿の URL と記事証拠の対応 ------------------------------------

def _evaluate_with(article, *, main_url="https://example.test/coffee"):
    obs = {ids(1)[0]: make_observation("コーヒー")}
    context = make_context(article, obs)
    context["main_article_url"] = main_url
    context["urls_in_post"] = [main_url] if main_url else []
    return advice.evaluate(context, article=article, proposal=None,
                            observations=obs, now=NOW)


def test_別の記事の証拠は主対象と対応しないと言われる():
    article = make_article()
    article = dict(article)
    article["requested_url"] = "https://different.test/unrelated"
    article["final_url"] = "https://different.test/unrelated"
    result = _evaluate_with(article)
    assert result["status"] == "needs_article", result
    assert any("別の記事" in a["reason"] for a in result["required_actions"]), \
        result["required_actions"]


def test_転送は通すが黙って通さない():
    """`requested_url` が投稿の URL なら、`final_url` が動いていても記事は同じ。"""
    article = dict(make_article())
    article["final_url"] = "https://example.test/coffee-2026"
    result = _evaluate_with(article)
    assert result["status"] != "needs_article", result
    assert any("転送" in w for w in result["warnings"]), result["warnings"]


def test_最終URLが投稿のURLでも通す():
    """短縮 URL を取りに行って、投稿の URL に着いた場合。"""
    article = dict(make_article())
    article["requested_url"] = "https://exmpl.test/x"
    result = _evaluate_with(article)
    assert result["status"] != "needs_article", result


def test_フラグメントの違いは同じ記事():
    assert advice.same_article_url("https://a.test/x", "https://a.test/x#s2")
    assert advice.same_article_url("https://a.test/x/", "https://a.test/x")


def test_queryの違いは同じ記事にしない():
    """**query で別の記事を出すサイトがある。** 機械には区別できないので寛容に
    しない——寛容にすると「別の記事を主対象にできる」穴に戻る。
    """
    assert not advice.same_article_url("https://a.test/x",
                                        "https://a.test/x?utm_source=threads")


def test_主対象が決まらなければ先へ進ませない():
    article = make_article()
    result = _evaluate_with(article, main_url=None)
    assert result["status"] == "needs_article"
    assert any(a["type"] == "article_url" for a in result["required_actions"])


def test_投稿にないURLは主対象にできない(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "u.md", body=BODY,
                             fm_overrides={"status": "draft"})
    proc = _run(["topics", "suggest", path,
                  "--article-url", "https://elsewhere.test/other"])
    assert proc.returncode == 2, proc.stdout
    out = json.loads(proc.stdout)
    assert out["error"]["code"] == "schema_error"
    assert "投稿本文にありません" in out["error"]["message"]


def test_主対象を変えれば別の判断になる(isolated_account):
    body = ("## threads\n\nA https://example.test/a と B https://example.test/b。\n")
    path = write_queue_file(isolated_account["queue_dir"], "two.md", body=body,
                             fm_overrides={"status": "draft"})
    a = json.loads(_run(["topics", "suggest", path,
                          "--article-url", "https://example.test/a"]).stdout)
    b = json.loads(_run(["topics", "suggest", path,
                          "--article-url", "https://example.test/b"]).stdout)
    assert a["context_id"] != b["context_id"], "主対象を変えても同じ判断のまま"


# --- 指摘 2: 根拠が LLM に届く ----------------------------------------------

def test_contextに根拠の本文が載る(isolated_account, tmp_path):
    """**ID だけでは、別のセッションは何も読めない。**"""
    path = write_queue_file(isolated_account["queue_dir"], "e.md", body=BODY,
                             fm_overrides={"status": "draft"})
    obs = make_observation("コーヒー", excerpt="MARKER_投稿例: 今日の一杯")
    obs["retrieved_at"] = datetime.datetime.now().astimezone().isoformat()
    saved = json.loads(_run(["topics", "observe", "--json-stdin", "--by", "t"],
                             obs).stdout)

    profile = {"language": "ja", "primary_goal": "article_visits",
               "editorial_scope": "MARKER_方針: 家庭で楽しむコーヒー",
               "intended_interests": ["コーヒー"],
               "avoid_misrepresentation": ["医療効能を主張しない"],
               "status": "confirmed", "basis": ["docs/編集方針.md"]}
    assert _run(["topics", "profile", isolated_account["name"],
                  "--json-stdin", "--by", "t"], profile).returncode == 0

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    blob = json.dumps(out, ensure_ascii=False)
    assert "MARKER_投稿例" in blob, "保存した投稿例が返らない"
    assert "MARKER_方針" in blob, "保存した編集方針が返らない"
    assert out["evidence"]["post_text"], "投稿本文が返らない"
    assert saved["observation_id"] in {o["observation_id"]
                                        for o in out["evidence"]["observations"]}


def test_投稿例が多ければ削るが削ったと言う(isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "many.md", body=BODY,
                             fm_overrides={"status": "draft"})
    obs = make_observation("コーヒー", authors=20, samples=40)
    obs["retrieved_at"] = datetime.datetime.now().astimezone().isoformat()
    oid = json.loads(_run(["topics", "observe", "--json-stdin", "--by", "t"],
                           obs).stdout)["observation_id"]

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    row = next(o for o in out["evidence"]["observations"]
               if o["observation_id"] == oid)
    assert len(row["samples"]) == 8
    # **黙って減らさない。** 減らしたら「その投稿例は無い」と読まれる。
    assert row["samples_truncated"]["total"] == 40
    assert oid in out["evidence"]["truncated_observation_ids"]

    full = json.loads(_run(["topics", "observation", oid]).stdout)
    assert len(full["observation"]["samples"]) == 40, "取りに来る口が無い"


def test_既存22語も根拠として返る(isolated_account, thth_root):
    from thth import topics as topics_mod
    topics_mod.record("コーヒー", verdict="alive", audience="焙煎士・喫茶店",
                       by="nigamilab セッション")
    path = write_queue_file(isolated_account["queue_dir"], "l.md", body=BODY,
                             fm_overrides={"status": "draft"})
    out = json.loads(_run(["topics", "suggest", path]).stdout)
    assert any(r["topic"] == "コーヒー"
               for r in out["evidence"]["legacy_notes"]), out["evidence"]


# --- 指摘 3: 空の引用 -------------------------------------------------------

def test_引用が空のsuitable候補は断る():
    article = make_article()
    with pytest.raises(models.SchemaError) as e:
        models.validate_proposal({
            "context_id": "x", "prompt_version": "t", "intended_reader": "x",
            "article_value": "x", "post_angle": "x",
            "candidates": [candidate("コーヒー", [], quote="精製")
                            | {"article_quotes": []}],
            "selected_topic": "コーヒー", "selection_reason": "x",
        }, article=article, known_observation_ids=set())
    assert "article_quotes が空" in str(e.value)


def test_不適合の候補は引用が無くてよい():
    """**見なかった候補にまで引用を求めない。**"""
    article = make_article()
    out = models.validate_proposal({
        "context_id": "x", "prompt_version": "t", "intended_reader": "x",
        "article_value": "x", "post_angle": "x",
        "candidates": [
            candidate("コーヒー", [], quote="精製"),
            candidate("お茶", [], fit="unsuitable") | {"article_quotes": []},
        ],
        "selected_topic": "コーヒー", "selection_reason": "x",
    }, article=article, known_observation_ids=set())
    assert out["proposal_id"].startswith("sha256:")


def test_引用が文字列でなければ断る():
    article = make_article()
    with pytest.raises(models.SchemaError):
        models.validate_proposal({
            "context_id": "x", "prompt_version": "t", "intended_reader": "x",
            "article_value": "x", "post_angle": "x",
            "candidates": [candidate("コーヒー", []) | {"article_quotes": [42]}],
            "selected_topic": "コーヒー", "selection_reason": "x",
        }, article=article, known_observation_ids=set())


# --- 指摘 4: 仮 profile -----------------------------------------------------

def _with_profile(status):
    article = make_article()
    obs = {ids(1)[0]: make_observation("コーヒー")}
    context = make_context(article, obs, profile_status=status)
    context["main_article_url"] = "https://example.test/coffee"
    proposal = models.validate_proposal({
        "context_id": context["context_id"], "prompt_version": "t",
        "intended_reader": "x", "article_value": "x", "post_angle": "x",
        "candidates": [candidate("コーヒー", ids(1))],
        "selected_topic": "コーヒー", "selection_reason": "x",
    }, article=article, known_observation_ids=set(obs))
    return advice.evaluate(context, article=article, proposal=proposal,
                            observations=obs, now=NOW)


def test_仮のprofileでは確定と言わない():
    result = _with_profile("provisional")
    assert result["status"] == "provisional", result
    assert any("profile が確定していません" in s for s in result["shortfalls"])


def test_確定したprofileなら推奨になる():
    assert _with_profile("confirmed")["status"] == "recommended"


def test_足りないものの種類が原因に合っている():
    """**profile が無いのに「観測を追加してください」と言わない。**"""
    result = _with_profile("provisional")
    kinds = {a["type"] for a in result["required_actions"]}
    assert kinds == {"profile"}, result["required_actions"]


def test_原因が複数なら別々の作業として返す():
    article = make_article()
    obs = {ids(1)[0]: make_observation("コーヒー", status="partial")}
    context = make_context(article, obs, profile_status="provisional")
    context["main_article_url"] = "https://example.test/coffee"
    proposal = models.validate_proposal({
        "context_id": context["context_id"], "prompt_version": "t",
        "intended_reader": "x", "article_value": "x", "post_angle": "x",
        "candidates": [candidate("コーヒー", ids(1), article_fit=1)],
        "selected_topic": "コーヒー", "selection_reason": "x",
    }, article=article, known_observation_ids=set(obs))
    result = advice.evaluate(context, article=article, proposal=proposal,
                              observations=obs, now=NOW)
    kinds = {a["type"] for a in result["required_actions"]}
    assert kinds == {"profile", "proposal", "observation"}, result["required_actions"]
    for action in result["required_actions"]:
        assert action["expected_schema"] != "TopicObservation" \
            or action["type"] == "observation"


# --- 指摘 5: 内容アドレスの再検証 -------------------------------------------

def test_同じIDで中身が変わっていたら壊れている扱い(thth_root):
    row = make_observation("コーヒー")
    row["schema_version"] = models.SCHEMA_VERSION
    row["observation_id"] = models.content_id(row, exclude=("observation_id",))
    store.put("observations", row, id_key="observation_id")

    path = os.path.join(store.root(), "observations",
                        row["observation_id"][7:] + ".json")
    tampered = dict(row)
    tampered["status"] = "ok"
    tampered["samples"] = make_observation("コーヒー", samples=9)["samples"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tampered, f, ensure_ascii=False)

    rows, broken = store.load_all("observations")
    assert rows == [], "書き換えられた記録を読めたことにした"
    assert broken == [row["observation_id"]]
    with pytest.raises(store.StoreError):
        store.get("observations", row["observation_id"])


def test_IDを名乗るだけの記録は保存できない(thth_root):
    row = make_observation("コーヒー")
    row["observation_id"] = "sha256:" + "a" * 64
    with pytest.raises(store.StoreError):
        store.put("observations", row, id_key="observation_id")


def test_壊れた根拠を参照する判断は保存しない(isolated_account, thth_root):
    path = write_queue_file(isolated_account["queue_dir"], "b.md", body=BODY,
                             fm_overrides={"status": "draft"})
    obs = make_observation("コーヒー")
    obs["retrieved_at"] = datetime.datetime.now().astimezone().isoformat()
    oid = json.loads(_run(["topics", "observe", "--json-stdin", "--by", "t"],
                           obs).stdout)["observation_id"]
    article = make_article()
    first = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                             {"article": article}).stdout)
    proposal = {
        "context_id": first["context_id"], "prompt_version": "t",
        "intended_reader": "x", "article_value": "x", "post_angle": "x",
        "candidates": [candidate("コーヒー", [oid])],
        "selected_topic": "コーヒー", "selection_reason": "x",
    }
    checked = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                               {"article": article, "proposal": proposal}).stdout)

    store_path = os.path.join(store.root(), "observations", oid[7:] + ".json")
    row = json.loads(open(store_path, encoding="utf-8").read())
    row["note"] = "あとから書き足した"
    open(store_path, "w", encoding="utf-8").write(json.dumps(row, ensure_ascii=False))

    proc = _run(["topics", "record-decision", "--json-stdin", "--by", "t"], checked)
    assert proc.returncode != 0, proc.stdout
    out = json.loads(proc.stdout)
    assert not out.get("stored")


# --- 指摘 6: suggest の出力をそのまま保存できる -----------------------------

def test_suggestの出力をそのまま保存できる(isolated_account, thth_root):
    path = write_queue_file(isolated_account["queue_dir"], "r.md", body=BODY,
                             fm_overrides={"status": "draft"})
    obs = make_observation("コーヒー")
    obs["retrieved_at"] = datetime.datetime.now().astimezone().isoformat()
    oid = json.loads(_run(["topics", "observe", "--json-stdin", "--by", "t"],
                           obs).stdout)["observation_id"]
    profile = {"language": "ja", "primary_goal": "article_visits",
               "editorial_scope": "コーヒー", "intended_interests": ["コーヒー"],
               "avoid_misrepresentation": ["医療効能を主張しない"],
               "status": "confirmed", "basis": ["docs/編集方針.md"]}
    _run(["topics", "profile", isolated_account["name"], "--json-stdin", "--by", "t"],
          profile)

    article = make_article()
    first = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                             {"article": article}).stdout)
    proposal = {
        "context_id": first["context_id"], "prompt_version": "t",
        "intended_reader": "コーヒーを淹れる人", "article_value": "精製の違い",
        "post_angle": "味の違い",
        "candidates": [candidate("コーヒー", [oid],
                                  counterevidence="重大な反証を確認できず")],
        "selected_topic": "コーヒー", "selection_reason": "本文と会話の適合",
    }
    checked = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                               {"article": article, "proposal": proposal}).stdout)
    assert checked["status"] == "recommended", checked["shortfalls"]

    # **手で組み直さずに保存できる。**
    proc = _run(["topics", "record-decision", "--json-stdin", "--by", "t"], checked)
    assert proc.returncode == 0, proc.stdout
    saved = json.loads(proc.stdout)
    assert saved["stored"] is True

    loaded = json.loads(_run(["topics", "decision", saved["decision_id"]]).stdout)
    assert loaded["selected_topic"] == "コーヒー"
    assert loaded["freshness"]["draft_unchanged"] is True


def test_保存に足りない入力はそう言う(isolated_account):
    proc = _run(["topics", "record-decision", "--json-stdin", "--by", "t"],
                 {"draft_path": "x.md"})
    assert proc.returncode == 2
    message = json.loads(proc.stdout)["error"]["message"]
    assert "record_payload" in message, message


# ===========================================================================
# 独立レビュー第 2 巡（2026-09-11）の 3 件
# ===========================================================================

def _make_profile(status="provisional", scope="家庭で楽しむコーヒー"):
    return {"language": "ja", "primary_goal": "article_visits",
            "editorial_scope": scope, "intended_interests": ["コーヒー"],
            "avoid_misrepresentation": ["医療効能を主張しない"],
            "status": status, "basis": ["docs/編集方針.md"]}


# --- P1-1: profile を例外にしない -------------------------------------------

def test_profileも読むたびに中身と版番号を照合する(thth_root, isolated_account):
    """**profile だけ `read_json` のままだった。**

    観測・記事・候補比較・判断には入れた照合が、profile には無かった。
    `profile_version` を据え置いて `status` を `confirmed` に、`basis` を
    空に書き換えると、**未確定の方針に基づく判断が確定扱いで保存できた。**
    """
    account = isolated_account["name"]
    assert _run(["topics", "profile", account, "--json-stdin", "--by", "t"],
                 _make_profile("provisional")).returncode == 0

    path = store.profile_path(account)
    row = json.loads(open(path, encoding="utf-8").read())
    row["status"] = "confirmed"
    row["basis"] = []
    row["editorial_scope"] = "書き換えた別の方針"
    open(path, "w", encoding="utf-8").write(json.dumps(row, ensure_ascii=False))

    with pytest.raises(store.StoreError) as e:
        store.get_profile(account)
    assert "profile_version と合いません" in str(e.value) \
        or "schema に合いません" in str(e.value), str(e.value)


def test_書き換えられたprofileでは判断も保存もできない(thth_root, isolated_account):
    path = write_queue_file(isolated_account["queue_dir"], "p.md", body=BODY,
                             fm_overrides={"status": "draft"})
    account = isolated_account["name"]
    _run(["topics", "profile", account, "--json-stdin", "--by", "t"],
          _make_profile("provisional"))

    ppath = store.profile_path(account)
    row = json.loads(open(ppath, encoding="utf-8").read())
    row["status"] = "confirmed"
    open(ppath, "w", encoding="utf-8").write(json.dumps(row, ensure_ascii=False))

    proc = _run(["topics", "suggest", path])
    assert proc.returncode == 2, proc.stdout
    assert json.loads(proc.stdout)["error"]["code"] == "store_busy" \
        or "profile" in json.loads(proc.stdout)["error"]["message"]


def test_検討用profileも検証関数を通る(isolated_account, tmp_path):
    """`--profile` を素通ししていたので、何を渡しても通っていた。"""
    path = write_queue_file(isolated_account["queue_dir"], "ov.md", body=BODY,
                             fm_overrides={"status": "draft"})
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(
        _make_profile("confirmed") | {"basis": [], "confirmed_by": "t"},
        ensure_ascii=False))
    proc = _run(["topics", "suggest", path, "--profile", str(bad)])
    assert proc.returncode == 2, proc.stdout
    assert "basis" in json.loads(proc.stdout)["error"]["message"]


def test_検討用profileは版番号を名乗らせず中身から決める(isolated_account, tmp_path):
    path = write_queue_file(isolated_account["queue_dir"], "ov2.md", body=BODY,
                             fm_overrides={"status": "draft"})
    liar = tmp_path / "liar.json"
    liar.write_text(json.dumps(
        _make_profile("confirmed") | {"confirmed_by": "t",
                                       "profile_version": "sha256:" + "0" * 64},
        ensure_ascii=False))
    out = json.loads(_run(["topics", "suggest", path,
                            "--profile", str(liar)]).stdout)
    assert out["context"]["profile_version"] != "sha256:" + "0" * 64


# --- P2-3: 使った方針が読み手に届く -----------------------------------------

def test_差し替えたprofileの本文が出力に載る(isolated_account, tmp_path):
    """**「差し替えています」の 1 行だけでは、次の LLM は何も分からない。**"""
    path = write_queue_file(isolated_account["queue_dir"], "ov3.md", body=BODY,
                             fm_overrides={"status": "draft"})
    override = tmp_path / "o.json"
    override.write_text(json.dumps(
        _make_profile("confirmed", scope="MARKER_差し替えた方針") |
        {"confirmed_by": "t"}, ensure_ascii=False))
    proc = _run(["topics", "suggest", path, "--profile", str(override)])
    out = json.loads(proc.stdout)
    assert "evidence" in out, out
    assert out["evidence"]["profile"]["editorial_scope"] == "MARKER_差し替えた方針"
    assert out["evidence"]["profile_overridden"] is True
    assert out["context"]["profile_overridden"] is True


def test_使ったprofileと出力のprofileが同じもの(isolated_account):
    """**検証した snapshot をそのまま読み手へ渡す。**

    以前は `evidence` が active profile を**読み直して**いた。判断に使ったものと
    読み手が見るものが別の経路だと、片方だけ変わっても気づけない。
    """
    account = isolated_account["name"]
    path = write_queue_file(isolated_account["queue_dir"], "sn.md", body=BODY,
                             fm_overrides={"status": "draft"})
    _run(["topics", "profile", account, "--json-stdin", "--by", "t"],
          _make_profile("confirmed"))
    out = json.loads(_run(["topics", "suggest", path]).stdout)
    assert out["evidence"]["profile"]["profile_version"] == \
        out["context"]["profile_version"]
    assert out["evidence"]["profile_overridden"] is False


# --- P1-2: 共有観測と自 account の判断を混ぜない ----------------------------

def test_自分の不適合が他所の適合に置き換わらない(isolated_account, thth_root):
    """**`judgment()` は取っていたのに `bool()` しか使っていなかった。**

    最新の 1 行から `fit` も理由も取っていたので、その行が account を持たない
    記録だと、**自分が unsuitable と判断した事実が出力から消えた。**
    """
    from thth import topics as topics_mod
    account = isolated_account["name"]
    path = write_queue_file(isolated_account["queue_dir"], "j.md", body=BODY,
                             fm_overrides={"status": "draft"})

    topics_mod.record("お茶", verdict="mismatch", by="自分", account=account,
                       note="研究所は効能を扱わない")
    topics_mod.record("お茶", verdict="alive", by="別のセッション",
                       note="日本茶の場として活きている")   # account 無し

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    row = next(r for r in out["evidence"]["legacy_notes"] if r["topic"] == "お茶")
    assert row["own_judgment"]["fit"] == "unsuitable"
    assert row["own_judgment"]["reason"] == "研究所は効能を扱わない"
    assert row["own_judgment"]["account"] == account
    # account 無しの記録は**常に参考**。
    assert row["legacy_judgment"]["fit"] == "suitable"
    assert row["legacy_judgment"]["account"] is None
    assert "採用しないでください" in row["notice"]


def test_他accountの判断も自分の判断を消さない(isolated_account, thth_root):
    from thth import topics as topics_mod
    account = isolated_account["name"]
    path = write_queue_file(isolated_account["queue_dir"], "j2.md", body=BODY,
                             fm_overrides={"status": "draft"})
    topics_mod.record("お茶", verdict="mismatch", by="自分", account=account)
    topics_mod.record("お茶", verdict="alive", by="kopicha", account="kopicha-threads")

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    row = next(r for r in out["evidence"]["legacy_notes"] if r["topic"] == "お茶")
    assert row["own_judgment"]["fit"] == "unsuitable"
    assert [r["account"] for r in row["other_judgments"]] == ["kopicha-threads"]


def test_自分の判断が無ければNoneで返す(isolated_account, thth_root):
    """**「まだ判断していない」を「他所が判断した」で埋めない。**"""
    from thth import topics as topics_mod
    path = write_queue_file(isolated_account["queue_dir"], "j3.md", body=BODY,
                             fm_overrides={"status": "draft"})
    topics_mod.record("お茶", verdict="alive", by="別のセッション")

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    row = next(r for r in out["evidence"]["legacy_notes"] if r["topic"] == "お茶")
    assert row["own_judgment"] is None
    assert row["legacy_judgment"]["fit"] == "suitable"


def test_観測と判断が別の物として返る(isolated_account, thth_root):
    """誰がいたか（共有できる事実）と、合うか（account ごと）を分ける。"""
    from thth import topics as topics_mod
    path = write_queue_file(isolated_account["queue_dir"], "j4.md", body=BODY,
                             fm_overrides={"status": "draft"})
    topics_mod.record("精製", verdict="mismatch", audience="レアアース・重加工",
                       by="THTH セッション", kind="専門語")

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    row = next(r for r in out["evidence"]["legacy_notes"] if r["topic"] == "精製")
    assert row["observation"]["audience"] == "レアアース・重加工"
    assert row["observation"]["kind"] == "専門語"
    assert "fit" not in row["observation"], "観測に判定が混ざっている"


# ===========================================================================
# asmon 関東セッションが実記事で見つけたもの（2026-09-11）
# ===========================================================================

def test_既存22語が参照できるIDを持つ(isolated_account, thth_root):
    """**いちばん重かった指摘。**

    > `required_actions` が `TopicObservation` を求めますが、私が入れた 16 語の
    > 記録は `legacy_notes` に入っていて `observation_id` を持っていません。
    > **満たす手段が無い。**

    検出は正しかったが、**直せない指示を出していた**——第 2 巡の
    「profile が無いのに観測を足せと言う」と同じ形。
    """
    from thth import topics as topics_mod
    account = isolated_account["name"]
    topics_mod.record("中学受験", verdict="alive", audience="受験親のやりとり",
                       by="関東セッション", account=account, kind="行動")
    path = write_queue_file(isolated_account["queue_dir"], "k.md", body=BODY,
                             fm_overrides={"status": "draft"})

    out = json.loads(_run(["topics", "suggest", path]).stdout)
    row = next(r for r in out["evidence"]["legacy_notes"]
               if r["topic"] == "中学受験")
    oid = row["observation_id"]
    assert oid and oid.startswith("sha256:"), row
    # **context にも入っている**＝候補比較に書いてよい ID。
    assert oid in out["context"]["observation_ids"]
    # **引ける。**
    full = json.loads(_run(["topics", "observation", oid]).stdout)
    assert full["observation"]["topic"] == "中学受験"
    assert full["observation"]["provenance"] == "legacy"


def test_noteで記録したら観測IDが返る(isolated_account, thth_root):
    account = isolated_account["name"]
    proc = _run(["topics", account, "--note", "中受", "--verdict", "alive",
                  "--status", "ok", "--kind", "行動", "--audience", "受験親",
                  "--by", "関東", "--json"])
    assert proc.returncode == 0, proc.stderr
    row = json.loads(proc.stdout)
    assert row["observation_id"].startswith("sha256:"), row

    text = _run(["topics", account, "--note", "中受2", "--verdict", "alive",
                  "--status", "ok", "--kind", "行動", "--by", "関東"]).stdout
    assert "観測 ID:" in text, text
    assert "これだけでは推奨になりません" in text, text


def test_参考記録だけでは推奨にならないが理由が正しい(isolated_account, thth_root):
    """**旧記録を「keyword だから」と言わない。**

    引き方の記録が無いだけで、keyword で引いたわけではない。理由が違えば
    **次にすることも違う**（tag で引き直す、ではなく、投稿例を控える）。
    """
    from thth import topics as topics_mod
    account = isolated_account["name"]
    topics_mod.record("コーヒー", verdict="alive", audience="焙煎士",
                       by="関東", account=account)
    path = write_queue_file(isolated_account["queue_dir"], "lg.md", body=BODY,
                             fm_overrides={"status": "draft"})
    _run(["topics", "profile", account, "--json-stdin", "--by", "t"],
          _make_profile("confirmed"))

    first = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                             {"article": make_article()}).stdout)
    oid = next(r["observation_id"] for r in first["evidence"]["legacy_notes"]
               if r["topic"] == "コーヒー")
    proposal = {
        "context_id": first["context_id"], "prompt_version": "t",
        "intended_reader": "x", "article_value": "x", "post_angle": "x",
        "candidates": [candidate("コーヒー", [oid])],
        "selected_topic": "コーヒー", "selection_reason": "x",
    }
    out = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                           {"article": make_article(),
                            "proposal": proposal}).stdout)
    assert out["status"] == "provisional", out
    reason = [s for s in out["shortfalls"] if "観測" in s or "記録" in s]
    assert reason, out["shortfalls"]
    assert "keyword" not in reason[0], reason
    assert "投稿例を控えて" in reason[0], reason


def test_needs_proposalでも次にすることが出る(isolated_account):
    """**`needs_article` には出て `needs_proposal` には出ていなかった。**

    > ソースを読まないと次に進めませんでした。
    """
    path = write_queue_file(isolated_account["queue_dir"], "np.md", body=BODY,
                             fm_overrides={"status": "draft"})
    out = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                           {"article": make_article()}).stdout)
    assert out["status"] == "needs_proposal"
    assert out["required_actions"], "次にすることが空だった"
    assert out["required_actions"][0]["type"] == "proposal"
    shape = out["expected_schema"]["TopicProposal"]
    assert "candidates" in shape and "context_id" in shape
    assert "suitable" in shape["candidates"][0]["fit"]


def test_記事の必須項目と値域が出力に出る(isolated_account):
    """`topic_models.py` を読ませない。"""
    path = write_queue_file(isolated_account["queue_dir"], "sc.md", body=BODY,
                             fm_overrides={"status": "draft"})
    out = json.loads(_run(["topics", "suggest", path]).stdout)
    assert out["status"] == "needs_article"
    shape = out["expected_schema"]["ArticleEvidence"]
    assert set(shape) >= set(models.ARTICLE_KEYS), shape
    assert "full" in shape["coverage"] and "ok" in shape["retrieval_status"]


def test_記事だけを封筒なしで渡したらそう言う(isolated_account):
    """通知に**動かない例**を書いてしまった（`--account` と同じ形の事故）。"""
    path = write_queue_file(isolated_account["queue_dir"], "wr.md", body=BODY,
                             fm_overrides={"status": "draft"})
    proc = _run(["topics", "suggest", path, "--input-json-stdin"],
                 make_article())
    assert proc.returncode == 2, proc.stdout
    message = json.loads(proc.stdout)["error"]["message"]
    assert "--article-json-stdin" in message, message


def test_stale_contextは直し方を言う(isolated_account, thth_root):
    """**不足を埋めると 1 回必ず踏む。** 踏んだときに迷わせない。"""
    account = isolated_account["name"]
    path = write_queue_file(isolated_account["queue_dir"], "st.md", body=BODY,
                             fm_overrides={"status": "draft"})
    article = make_article()
    first = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                             {"article": article}).stdout)
    # profile を作る＝入力が変わる
    _run(["topics", "profile", account, "--json-stdin", "--by", "t"],
          _make_profile("confirmed"))

    proposal = {
        "context_id": first["context_id"], "prompt_version": "t",
        "intended_reader": "x", "article_value": "x", "post_angle": "x",
        "candidates": [candidate("コーヒー", [])],
        "selected_topic": "コーヒー", "selection_reason": "x",
    }
    out = json.loads(_run(["topics", "suggest", path, "--input-json-stdin"],
                           {"article": article, "proposal": proposal}).stdout)
    assert out["status"] == "stale_context"
    assert "context_id を" in out["warnings"][-1], out["warnings"]
